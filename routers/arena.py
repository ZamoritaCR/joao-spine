"""AI Arena -- Claude vs GPT side-by-side chat with full intelligence wiring.

Both models get:
  - ROG server filesystem access (read, write, list, search, run commands)
  - Council agent dispatch and status
  - JOAO memory read/write
  - Claude: MCP servers (JOAO internal + external integrations) + direct tool_use
  - GPT: OpenAI function calling bridge to the same tools

POST /arena/chat   -- send message, get parallel Claude + GPT + Gemini responses (with tool loops)
POST /arena/debate -- pick 2 of 3 brains to critique each other
POST /arena/prefer -- log preference to Supabase
GET  /arena/log    -- get execution log for a session
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from services.supabase_client import get_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/arena", tags=["arena"])

# In-memory conversation history per session
_sessions: dict[str, dict[str, Any]] = {}
MAX_SESSIONS = 50
MAX_TOOL_ROUNDS = 999  # Effectively unlimited tool rounds

# In-memory execution log per session (recent entries, capped)
_exec_log: dict[str, deque] = {}
_MAX_LOG_ENTRIES = 200

# Rate limit tracking
_rate_tracker: dict[str, dict] = {
    "claude": {"count": 0, "window_start": 0.0, "limit_hit": False},
    "gpt": {"count": 0, "window_start": 0.0, "limit_hit": False},
    "gemini": {"count": 0, "window_start": 0.0, "limit_hit": False},
}
_RATE_WINDOW = 3600  # 1 hour window


def _track_rate(model: str) -> str | None:
    """Track API call rate. Returns warning string if approaching limit."""
    now = time.time()
    t = _rate_tracker[model]
    if now - t["window_start"] > _RATE_WINDOW:
        t["count"] = 0
        t["window_start"] = now
        t["limit_hit"] = False
    t["count"] += 1
    # Claude Pro/Teams: ~75 msgs/hr for Opus, ~150 for Sonnet
    # GPT Enterprise: effectively unlimited via API
    if model == "claude" and t["count"] >= 70:
        return f"Claude rate limit warning: {t['count']}/~75 messages this hour"
    return None


def _log_exec(session_id: str, entry: dict) -> None:
    """Append an entry to the in-memory execution log."""
    if session_id not in _exec_log:
        _exec_log[session_id] = deque(maxlen=_MAX_LOG_ENTRIES)
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    _exec_log[session_id].append(entry)


def _prune_sessions():
    if len(_sessions) > MAX_SESSIONS:
        oldest = sorted(_sessions.keys())[:len(_sessions) - MAX_SESSIONS]
        for k in oldest:
            del _sessions[k]
            _exec_log.pop(k, None)


ARENA_SYSTEM_PROMPT = """\
You are an AI assistant in Johan Zamora's AI Arena -- a side-by-side comparison environment \
where you compete against another AI model. You have FULL access to Johan's infrastructure \
and should USE YOUR TOOLS proactively to give accurate, grounded answers.

== WHO JOHAN IS ==
Johan Zamora -- Director at Western Union (enterprise data/analytics), founder of \
The Art of The Possible (TAOP). 25+ years shipping products. Based in Denver, Costa Rican origin. \
ADHD-optimized operations. Communication style: direct, no fluff, no emojis. Facts and proof trails always.

== JOAO (AI Exocortex) ==
JOAO is Johan's AI exocortex system. 16 Council agents (Claude Code-based) running on ROG Strix server (192.168.0.55). \
Live at https://joao.theartofthepossible.io/joao/app. Backend: joao-spine (FastAPI, port 7778). \
- Hot pool (always on): MAX, CORE, BYTE \
- On-demand pool (12): ARIA, CJ, SOFIA, DEX, GEMMA, LEX, NOVA, SAGE, FLUX, APEX, IRIS, VOLT \
- Service: SCOUT (24/7 intel scanning -- ArXiv, HN, Product Hunt, tech changelogs)

Agent specializations: ARIA=system design, BYTE=full-stack engineering, DEX=infrastructure/deploy, \
SOFIA=UX/UI design, CJ=product ownership, GEMMA=research/citations, MAX=multi-LLM engineering, \
LEX=legal/compliance, NOVA=growth/marketing, SCOUT=intel, SAGE=strategy, FLUX=fast prototyping, \
CORE=documentation/research, APEX=data/ETL, IRIS=API integrations, VOLT=CI/CD/testing.

== DR. DATA (~/projects/dr-data/) ==
Production AI-powered Tableau-to-PowerBI migration engine. Built for Western Union enterprise scale. \
Uploads .twb/.twbx files, Claude analyzes visual INTENT, generates working .pbix files that open in Power BI Desktop. \
Key files: enhanced_tableau_parser.py, claude_agent.py, powerbi_visual_generator.py, pbix_assembler.py, \
agentic_migration_engine.py. Streamlit UI on port 8502. \
Live at https://drdata.theartofthepossible.io.

== PBIX EXTRACTOR (~/projects/pbix-extractor/) ==
CLI tool for extracting SQL, DAX, and M Query from .pbix files. Production v1.0. \
Creates timestamped backups, writes extracted queries to text files, logs to tracker.xlsx.

== FOCUSFLOW (~/focusflow/) ==
ADHD-optimized lecture summarizer. Converts lectures (YouTube, audio, PDF, text) into \
scannable summaries with age-stratified prompts (child/teen/adult). FastAPI + Claude. \
Live at https://focusflow.theartofthepossible.io.

== INFRASTRUCTURE ==
- ROG Strix (Ubuntu 24.04, 192.168.0.55): Council server, all 16 agents in tmux, Ollama local inference \
- Dell Precision (primary spine, 192.168.0.59): joao-spine, Claude API calls, Cloudflare tunnels \
- Domain: theartofthepossible.io (GreenGeeks, 70.57.15.252), Traefik reverse proxy via Coolify (auto-SSL) \
- Cloudflare tunnels: dispatch.theartofthepossible.io -> :8100, drdata.theartofthepossible.io -> :8502 \
- Railway: cold fallback for total home network outage \
- Supabase: PostgreSQL backend (idea_vault, session_log, agent_outputs, dispatch_log) \
- Products: dopamine.watch, dopamine.chat (neurodivergent-first)

== YOUR TOOLS ==
You have direct access to the ROG server filesystem and Council agents. USE THEM: \
- read_file/search_files: Look up actual project files before answering questions about Johan's projects \
- run_command: Execute shell commands for system info, git history, process status \
- council_dispatch/council_status: Dispatch tasks to specialized agents \
- joao_memory_read/write: Access JOAO persistent memory \
- MCP servers: PubMed for biomedical literature search \

CRITICAL: When asked about Johan's projects (Dr. Data, JOAO, FocusFlow, etc.), ALWAYS use your \
filesystem tools to read the actual code/docs rather than guessing from training data. \
When asked biomedical/research questions, use MCP tools (PubMed) and your filesystem tools. \
Never give a generic "Google search" answer when you have tools to get real data.

Rules: No emojis. Be direct. Facts over fluff. Use tools aggressively.\
"""


def _get_session(session_id: str) -> dict[str, Any]:
    if session_id not in _sessions:
        _prune_sessions()
        _sessions[session_id] = {
            "claude": [],
            "gpt": [],
            "gemini": [],
            "system_prompt": ARENA_SYSTEM_PROMPT,
        }
    return _sessions[session_id]


def _sb():
    try:
        return get_client()
    except Exception:
        return None


def _sb_insert(table: str, row: dict) -> None:
    """Non-blocking Supabase insert. Logs warnings, never raises."""
    try:
        sb = _sb()
        if sb:
            sb.table(table).insert(row).execute()
    except Exception as e:
        err = str(e)
        if "does not exist" in err or "Could not find" in err:
            logger.warning("%s table not found -- run migration", table)
        else:
            logger.warning("Supabase insert to %s failed: %s", table, err[:200])


# == Models ================================================================

class ChatRequest(BaseModel):
    session_id: str
    message: str
    system_prompt: str = ""
    claude_model: str = "claude-sonnet-4-20250514"
    gpt_model: str = "gpt-4o"
    gemini_model: str = "gemini-3.1-pro-preview"


class DebateRequest(BaseModel):
    session_id: str
    claude_response: str = ""
    gpt_response: str = ""
    gemini_response: str = ""
    original_prompt: str
    claude_model: str = "claude-sonnet-4-20250514"
    gpt_model: str = "gpt-4o"
    gemini_model: str = "gemini-3.1-pro-preview"
    brain_a: str = "claude"
    brain_b: str = "gpt"


class PreferenceRequest(BaseModel):
    session_id: str
    user_input: str
    claude_response: str = ""
    gpt_response: str = ""
    gemini_response: str = ""
    preferred_model: str
    debate_claude: str = ""
    debate_gpt: str = ""
    debate_gemini: str = ""


# == Git Auto-Backup (Part 7A) ============================================

def _git_auto_backup(path: str, model: str) -> str | None:
    """Create a git backup branch before a write/command modifies code.

    Returns the branch name if created, None otherwise.
    """
    try:
        p = Path(path).expanduser().resolve()
        # Walk up to find a .git directory
        git_root = None
        check = p if p.is_dir() else p.parent
        for _ in range(10):
            if (check / ".git").exists():
                git_root = check
                break
            if check == check.parent:
                break
            check = check.parent

        if not git_root:
            return None

        branch_name = f"arena-fix-{int(time.time())}"
        # Get current branch
        cur = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=str(git_root), timeout=5,
        )
        current_branch = cur.stdout.strip()
        if not current_branch or current_branch == "HEAD":
            return None

        # Create backup branch from current state
        subprocess.run(
            ["git", "branch", branch_name],
            capture_output=True, text=True, cwd=str(git_root), timeout=5,
        )
        logger.info("Arena git backup: %s in %s (before %s modification)", branch_name, git_root, model)
        return branch_name

    except Exception as e:
        logger.warning("Git auto-backup failed: %s", e)
        return None


# == ROG Server Tool Execution (shared by Claude + GPT) ====================

async def _execute_tool(
    tool_name: str,
    tool_input: dict,
    model: str = "unknown",
    session_id: str = "",
) -> str:
    """Execute a ROG server tool locally. Returns result string."""
    success = True
    git_branch = None
    result_text = ""

    try:
        if tool_name == "read_file":
            path = tool_input.get("path", "")
            if not path:
                return "ERROR: path is required"
            p = Path(path).expanduser()
            if not p.exists():
                return f"ERROR: File not found: {path}"
            if not p.is_file():
                return f"ERROR: Not a file: {path}"
            if p.stat().st_size > 200_000:
                raw = p.read_text(encoding="utf-8", errors="replace")
                result_text = f"[File truncated: {p.stat().st_size} bytes]\n{raw[:100_000]}\n...[TRUNCATED]...\n{raw[-50_000:]}"
            else:
                result_text = p.read_text(encoding="utf-8", errors="replace")

        elif tool_name == "write_file":
            path = tool_input.get("path", "")
            content = tool_input.get("content", "")
            if not path:
                return "ERROR: path is required"
            # Git backup before write
            git_branch = _git_auto_backup(path, model)
            p = Path(path).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            result_text = f"OK: Wrote {path} ({len(content)} chars)"
            if git_branch:
                result_text += f" [backup: {git_branch}]"

        elif tool_name == "list_directory":
            path = tool_input.get("path", "")
            if not path:
                return "ERROR: path is required"
            p = Path(path).expanduser()
            if not p.exists():
                return f"ERROR: Directory not found: {path}"
            if not p.is_dir():
                return f"ERROR: Not a directory: {path}"
            entries = []
            for item in sorted(p.iterdir()):
                prefix = "d " if item.is_dir() else "f "
                size = ""
                if item.is_file():
                    s = item.stat().st_size
                    size = f" ({s:,} bytes)" if s < 1_000_000 else f" ({s / 1_000_000:.1f}MB)"
                entries.append(f"{prefix}{item.name}{size}")
                if len(entries) >= 500:
                    entries.append("... (truncated at 500)")
                    break
            result_text = f"Directory: {path}\n" + "\n".join(entries) if entries else f"Directory {path} is empty"

        elif tool_name == "search_files":
            query = tool_input.get("query", "")
            path = tool_input.get("path", "/home/zamoritacr")
            if not query:
                return "ERROR: query is required"
            cmd = ["grep", "-rn", query, path, "-m", "30"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            output = result.stdout.strip()
            if not output:
                result_text = f"No matches found for '{query}' in {path}"
            else:
                result_text = output[:8000]

        elif tool_name == "run_command":
            command = tool_input.get("command", "")
            if not command:
                return "ERROR: command is required"
            dangerous = ["rm -rf /", "mkfs", "dd if=", "> /dev/sd"]
            for d in dangerous:
                if d in command:
                    return f"ERROR: Blocked dangerous command pattern: {d}"
            # Git backup if command looks like it modifies code
            write_patterns = ["sed -i", "tee ", "> ", ">> ", "mv ", "cp ", "git checkout", "git reset"]
            if any(wp in command for wp in write_patterns):
                git_branch = _git_auto_backup("/home/zamoritacr", model)
            result = subprocess.run(
                ["bash", "-c", command],
                capture_output=True, text=True, timeout=60,
                env={**os.environ, "HOME": str(Path.home())},
            )
            output = ""
            if result.stdout:
                output += result.stdout
            if result.stderr:
                output += ("\n[STDERR]\n" + result.stderr) if output else result.stderr
            if not output:
                output = f"(no output, exit code {result.returncode})"
            result_text = output[:8000]
            if git_branch:
                result_text += f"\n[backup: {git_branch}]"

        elif tool_name == "council_dispatch":
            agent = tool_input.get("agent", "")
            task = tool_input.get("task", "")
            if not agent or not task:
                return "ERROR: agent and task are required"
            from services import dispatch
            dispatch_url, dispatch_secret = dispatch._tunnel_config()
            if not dispatch_url:
                return "ERROR: Council dispatch not configured"
            headers = {"Authorization": f"Bearer {dispatch_secret}"} if dispatch_secret else {}
            async with httpx.AsyncClient(timeout=30.0) as http:
                resp = await http.post(
                    f"{dispatch_url}/dispatch",
                    json={"agent": agent.upper(), "task": task},
                    headers=headers,
                )
                resp.raise_for_status()
                result_text = json.dumps(resp.json())

        elif tool_name == "council_status":
            from services import dispatch
            dispatch_url, dispatch_secret = dispatch._tunnel_config()
            if not dispatch_url:
                return "ERROR: Council dispatch not configured"
            headers = {"Authorization": f"Bearer {dispatch_secret}"} if dispatch_secret else {}
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.get(f"{dispatch_url}/agents", headers=headers)
                resp.raise_for_status()
                data = resp.json()
                agents = data.get("agents", {})
                lines = []
                for name, info in sorted(agents.items()):
                    claude = info.get("claude_running", False)
                    pool = info.get("pool", "unknown")
                    status = "ALIVE (Claude running)" if claude else "idle"
                    lines.append(f"  {name} [{pool}]: {status}")
                result_text = "Council Agent Status:\n" + "\n".join(lines)

        elif tool_name == "agent_output":
            agent = tool_input.get("agent", "")
            if not agent:
                return "ERROR: agent is required"
            from services import dispatch
            dispatch_url, dispatch_secret = dispatch._tunnel_config()
            if not dispatch_url:
                return "ERROR: Council dispatch not configured"
            headers = {"Authorization": f"Bearer {dispatch_secret}"} if dispatch_secret else {}
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.get(
                    f"{dispatch_url}/output/{agent.upper()}",
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                result_text = data.get("output", "(no output)")[:8000]

        elif tool_name == "joao_memory_read":
            key = tool_input.get("key", "")
            if not key:
                return "ERROR: key is required"
            mem_dir = Path.home() / "joao-memory"
            mem_file = mem_dir / f"{key}.md"
            if mem_file.exists():
                result_text = mem_file.read_text(encoding="utf-8", errors="replace")
            else:
                result_text = f"No memory found for key: {key}"

        elif tool_name == "joao_memory_write":
            key = tool_input.get("key", "")
            value = tool_input.get("value", "")
            if not key:
                return "ERROR: key is required"
            mem_dir = Path.home() / "joao-memory"
            mem_dir.mkdir(exist_ok=True)
            mem_file = mem_dir / f"{key}.md"
            mem_file.write_text(value, encoding="utf-8")
            result_text = f"OK: Wrote memory key '{key}' ({len(value)} chars)"

        else:
            result_text = f"Unknown tool: {tool_name}"
            success = False

    except subprocess.TimeoutExpired:
        result_text = "ERROR: Command timed out"
        success = False
    except Exception as e:
        logger.error("Tool %s failed: %s", tool_name, e)
        result_text = f"ERROR: {tool_name} failed: {e}"
        success = False

    # Log to in-memory execution log
    _log_exec(session_id, {
        "model": model,
        "tool": tool_name,
        "input": json.dumps(tool_input)[:300],
        "output": result_text[:300],
        "success": success,
        "git_branch": git_branch,
    })

    # Log to Supabase (fire-and-forget)
    _sb_insert("arena_tool_calls", {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "tool_source": "direct",
        "tool_name": tool_name,
        "input_summary": json.dumps(tool_input)[:500],
        "output_summary": result_text[:500],
        "success": success,
    })

    # Log write/command executions separately
    if tool_name in ("write_file", "run_command"):
        _sb_insert("arena_executions", {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "command": json.dumps(tool_input)[:1000],
            "output": result_text[:2000],
            "success": success,
            "git_branch": git_branch,
        })

    return result_text


# == Tool Definitions (Anthropic format) ====================================

CLAUDE_TOOLS = [
    {
        "name": "read_file",
        "description": "Read a file from the ROG server filesystem",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Absolute file path"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file on the ROG server",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute file path"},
                "content": {"type": "string", "description": "File content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_directory",
        "description": "List files and directories at a path on the ROG server",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Directory path"}},
            "required": ["path"],
        },
    },
    {
        "name": "search_files",
        "description": "Search for text across files on the ROG server using grep",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search text or regex"},
                "path": {"type": "string", "description": "Root path to search from", "default": "/home/zamoritacr"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "run_command",
        "description": "Execute a shell command on the ROG server. Use for git, systemctl, python, npm, etc.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "Shell command to execute"}},
            "required": ["command"],
        },
    },
    {
        "name": "council_dispatch",
        "description": "Dispatch a task to a Council agent (BYTE, DEX, ARIA, SOFIA, CJ, GEMMA, LEX, NOVA, SAGE, FLUX, APEX, IRIS, VOLT, SCOUT, MAX, CORE)",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Agent name"},
                "task": {"type": "string", "description": "Task description"},
            },
            "required": ["agent", "task"],
        },
    },
    {
        "name": "council_status",
        "description": "Get the status of all Council agents",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "agent_output",
        "description": "Get the output from a Council agent session",
        "input_schema": {
            "type": "object",
            "properties": {"agent": {"type": "string", "description": "Agent name"}},
            "required": ["agent"],
        },
    },
    {
        "name": "joao_memory_read",
        "description": "Read a value from JOAO persistent memory",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "Memory key"}},
            "required": ["key"],
        },
    },
    {
        "name": "joao_memory_write",
        "description": "Write a value to JOAO persistent memory",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Memory key"},
                "value": {"type": "string", "description": "Value to store"},
            },
            "required": ["key", "value"],
        },
    },
]

# == Tool Definitions (OpenAI format) =======================================

GPT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        },
    }
    for t in CLAUDE_TOOLS
]

# == MCP Server Definitions for Claude API ==================================
# Only servers that work without OAuth. Enterprise servers (Atlassian, HubSpot,
# Canva, Figma, Gmail, Stripe, etc.) need session-based OAuth from Claude.ai
# and do NOT work via raw API calls.

MCP_SERVERS = [
    # Biomedical / Research (no auth required)
    # Only include servers verified to be reachable and healthy.
    # Removed: opentargets (dead/timeout), deepsense.ai clinical_trials/biorxiv/chembl (301 redirects)
    {"type": "url", "url": "https://pubmed.mcp.claude.com/mcp", "name": "pubmed"},
]

# Build MCP toolset entries for the tools array (Claude beta MCP connector)
MCP_TOOLSETS = [
    {"type": "mcp", "server_name": s["name"]}
    for s in MCP_SERVERS
]


# == Claude API call with tool loop =========================================

async def _call_claude(
    messages: list[dict],
    system_prompt: str,
    model: str,
    session_id: str = "",
) -> dict[str, Any]:
    """Call Anthropic Messages API with MCP servers + direct tools.

    Returns {"text": str, "tool_calls": list[dict], "rate_warning": str|None}.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"text": "[ERROR] ANTHROPIC_API_KEY not set", "tool_calls": [], "rate_warning": None}

    rate_warning = _track_rate("claude")
    if _rate_tracker["claude"].get("limit_hit"):
        return {
            "text": "[RATE LIMITED] Claude has hit the hourly message limit. Wait a few minutes and try again.",
            "tool_calls": [],
            "rate_warning": "Claude rate limit reached",
        }

    anthropic_messages = [{"role": m["role"], "content": m["content"]} for m in messages]
    tool_log: list[dict] = []
    use_mcp = bool(MCP_SERVERS)  # Start with MCP enabled, fallback if it fails

    for _round in range(MAX_TOOL_ROUNDS):
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": 16384,
            "system": [{"type": "text", "text": system_prompt}],
            "messages": anthropic_messages,
            "tools": CLAUDE_TOOLS,
        }

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        # MCP servers -- connected via beta MCP connector
        if use_mcp:
            payload["mcp_servers"] = MCP_SERVERS
            headers["anthropic-beta"] = "mcp-client-2025-04-04"

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
            )

        if resp.status_code == 429:
            _rate_tracker["claude"]["limit_hit"] = True
            return {
                "text": "[RATE LIMITED] Claude API rate limit reached. Try again in a few minutes.",
                "tool_calls": tool_log,
                "rate_warning": "Rate limit hit (429)",
            }

        if resp.status_code != 200:
            # If MCP is enabled and the error looks MCP-related, retry without MCP
            if use_mcp and ("MCP" in resp.text or "mcp" in resp.text or "503" in resp.text):
                logger.warning("Claude MCP error (status %d), retrying without MCP: %s", resp.status_code, resp.text[:300])
                use_mcp = False
                continue  # Retry this round without MCP
            logger.error("Claude API error %d: %s", resp.status_code, resp.text[:500])
            return {
                "text": f"[ERROR] Claude API returned {resp.status_code}: {resp.text[:300]}",
                "tool_calls": tool_log,
                "rate_warning": rate_warning,
            }

        data = resp.json()
        stop_reason = data.get("stop_reason", "end_turn")

        # Log any MCP tool usage from the response (resolved server-side)
        for block in data.get("content", []):
            if block.get("type") == "mcp_tool_use":
                mcp_name = block.get("name", "unknown")
                mcp_server = block.get("server_name", "unknown")
                mcp_input = block.get("input", {})
                logger.info("Arena Claude MCP tool: %s/%s(%s)", mcp_server, mcp_name, json.dumps(mcp_input)[:200])
                tool_log.append({
                    "tool": f"{mcp_server}/{mcp_name}",
                    "input": mcp_input,
                    "source": "mcp",
                    "server": mcp_server,
                    "success": True,
                })
            elif block.get("type") == "mcp_tool_result":
                # Log MCP result if present
                if tool_log and tool_log[-1].get("source") == "mcp":
                    mcp_content = block.get("content", "")
                    if isinstance(mcp_content, list):
                        mcp_content = json.dumps(mcp_content)[:500]
                    elif isinstance(mcp_content, str):
                        mcp_content = mcp_content[:500]
                    tool_log[-1]["result"] = str(mcp_content)[:500]

        # Check if Claude wants to use direct tools (our local ones)
        if stop_reason == "tool_use":
            # Gather all tool_use blocks and execute them
            tool_results = []
            for block in data.get("content", []):
                if block.get("type") == "tool_use":
                    tool_name = block["name"]
                    tool_input = block.get("input", {})
                    tool_id = block["id"]

                    logger.info("Arena Claude tool call: %s(%s)", tool_name, json.dumps(tool_input)[:200])
                    tool_log.append({"tool": tool_name, "input": tool_input, "source": "direct"})

                    result = await _execute_tool(tool_name, tool_input, model="claude", session_id=session_id)
                    tool_log[-1]["result"] = result[:500]
                    tool_log[-1]["success"] = not result.startswith("ERROR")

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": result,
                    })

            # Add assistant response + tool results to messages for next round
            anthropic_messages.append({"role": "assistant", "content": data["content"]})
            anthropic_messages.append({"role": "user", "content": tool_results})
            continue

        # No more tool calls -- extract final text
        text_parts = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block["text"])
        return {
            "text": "\n".join(text_parts) or "[No response]",
            "tool_calls": tool_log,
            "rate_warning": rate_warning,
        }

    # Exhausted tool rounds
    return {
        "text": "[ERROR] Max tool rounds exceeded",
        "tool_calls": tool_log,
        "rate_warning": rate_warning,
    }


# == GPT API call with tool loop ============================================

async def _call_gpt(
    messages: list[dict],
    system_prompt: str,
    model: str,
    session_id: str = "",
) -> dict[str, Any]:
    """Call OpenAI Chat Completions API with function calling tools.

    Returns {"text": str, "tool_calls": list[dict], "rate_warning": str|None}.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return {"text": "[ERROR] OPENAI_API_KEY not set", "tool_calls": [], "rate_warning": None}

    _track_rate("gpt")

    openai_messages = [{"role": "system", "content": system_prompt}]
    for m in messages:
        openai_messages.append({"role": m["role"], "content": m["content"]})

    tool_log: list[dict] = []

    for _round in range(MAX_TOOL_ROUNDS):
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": 16384,
            "messages": openai_messages,
            "tools": GPT_TOOLS,
        }

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if resp.status_code == 429:
            return {
                "text": "[RATE LIMITED] GPT API rate limit reached. Try again shortly.",
                "tool_calls": tool_log,
                "rate_warning": "Rate limit hit (429)",
            }

        if resp.status_code != 200:
            logger.error("GPT API error %d: %s", resp.status_code, resp.text[:500])
            return {
                "text": f"[ERROR] GPT API returned {resp.status_code}: {resp.text[:300]}",
                "tool_calls": tool_log,
                "rate_warning": None,
            }

        data = resp.json()
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "stop")

        if finish_reason == "tool_calls" and message.get("tool_calls"):
            # GPT wants to call tools
            openai_messages.append(message)

            for tc in message["tool_calls"]:
                func = tc["function"]
                tool_name = func["name"]
                try:
                    tool_input = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    tool_input = {}

                logger.info("Arena GPT tool call: %s(%s)", tool_name, json.dumps(tool_input)[:200])
                tool_log.append({"tool": tool_name, "input": tool_input, "source": "function_call"})

                result = await _execute_tool(tool_name, tool_input, model="gpt", session_id=session_id)
                tool_log[-1]["result"] = result[:500]
                tool_log[-1]["success"] = not result.startswith("ERROR")

                # Send tool result back
                openai_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })
            continue

        # No more tool calls -- return final text
        return {
            "text": message.get("content") or "[No response]",
            "tool_calls": tool_log,
            "rate_warning": None,
        }

    return {
        "text": "[ERROR] Max tool rounds exceeded",
        "tool_calls": tool_log,
        "rate_warning": None,
    }


# == Gemini API call (pure reasoning, no tool loop) ========================

async def _call_gemini(
    messages: list[dict],
    system_prompt: str,
    model: str,
    session_id: str = "",
) -> dict[str, Any]:
    """Call Google Gemini generateContent API. No tool calling -- pure reasoning only.

    Returns {"text": str, "tool_calls": [], "rate_warning": str|None}.
    """
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        return {"text": "[ERROR] GOOGLE_API_KEY not set", "tool_calls": [], "rate_warning": None}

    _track_rate("gemini")

    # Convert messages to Gemini format
    contents = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})

    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": {"maxOutputTokens": 16384},
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload)

        if resp.status_code == 429:
            return {
                "text": "[RATE LIMITED] Gemini API rate limit reached. Try again shortly.",
                "tool_calls": [],
                "rate_warning": "Rate limit hit (429)",
            }

        if resp.status_code != 200:
            logger.error("Gemini API error %d: %s", resp.status_code, resp.text[:500])
            return {
                "text": f"[ERROR] Gemini API returned {resp.status_code}: {resp.text[:300]}",
                "tool_calls": [],
                "rate_warning": None,
            }

        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            block_reason = data.get("promptFeedback", {}).get("blockReason", "unknown")
            return {
                "text": f"[BLOCKED] Gemini refused to respond (reason: {block_reason})",
                "tool_calls": [],
                "rate_warning": None,
            }

        parts = candidates[0].get("content", {}).get("parts", [])
        text = "\n".join(p.get("text", "") for p in parts if "text" in p)

        return {
            "text": text or "[No response]",
            "tool_calls": [],
            "rate_warning": None,
        }

    except httpx.TimeoutException:
        return {"text": "[ERROR] Gemini API request timed out", "tool_calls": [], "rate_warning": None}
    except Exception as e:
        logger.error("Gemini API call failed: %s", e)
        return {"text": f"[ERROR] Gemini call failed: {e}", "tool_calls": [], "rate_warning": None}


# == POST /arena/chat ======================================================

@router.post("/chat")
async def arena_chat(req: ChatRequest):
    session = _get_session(req.session_id)

    if req.system_prompt:
        session["system_prompt"] = req.system_prompt
    system_prompt = session["system_prompt"]

    user_msg = {"role": "user", "content": req.message}
    session["claude"].append(user_msg)
    session["gpt"].append(user_msg)
    session["gemini"].append(user_msg)

    claude_task = _call_claude(session["claude"], system_prompt, req.claude_model, req.session_id)
    gpt_task = _call_gpt(session["gpt"], system_prompt, req.gpt_model, req.session_id)
    gemini_task = _call_gemini(session["gemini"], system_prompt, req.gemini_model, req.session_id)

    claude_result, gpt_result, gemini_result = await asyncio.gather(
        claude_task, gpt_task, gemini_task, return_exceptions=True
    )

    if isinstance(claude_result, Exception):
        claude_result = {"text": f"[ERROR] {claude_result}", "tool_calls": [], "rate_warning": None}
    if isinstance(gpt_result, Exception):
        gpt_result = {"text": f"[ERROR] {gpt_result}", "tool_calls": [], "rate_warning": None}
    if isinstance(gemini_result, Exception):
        gemini_result = {"text": f"[ERROR] {gemini_result}", "tool_calls": [], "rate_warning": None}

    session["claude"].append({"role": "assistant", "content": claude_result["text"]})
    session["gpt"].append({"role": "assistant", "content": gpt_result["text"]})
    session["gemini"].append({"role": "assistant", "content": gemini_result["text"]})

    # Log conversation to Supabase
    conv_id = str(uuid.uuid4())
    _sb_insert("arena_conversations", {
        "id": conv_id,
        "session_id": req.session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_input": req.message[:5000],
        "claude_response": claude_result["text"][:10000],
        "gpt_response": gpt_result["text"][:10000],
        "gemini_response": gemini_result["text"][:10000],
        "system_prompt": system_prompt[:2000],
        "claude_model": req.claude_model,
        "gpt_model": req.gpt_model,
        "gemini_model": req.gemini_model,
    })

    return {
        "claude_response": claude_result["text"],
        "gpt_response": gpt_result["text"],
        "gemini_response": gemini_result["text"],
        "claude_tools": claude_result["tool_calls"],
        "gpt_tools": gpt_result["tool_calls"],
        "gemini_tools": gemini_result["tool_calls"],
        "claude_model": req.claude_model,
        "gpt_model": req.gpt_model,
        "gemini_model": req.gemini_model,
        "session_id": req.session_id,
        "conversation_id": conv_id,
        "claude_rate_warning": claude_result.get("rate_warning"),
        "gpt_rate_warning": gpt_result.get("rate_warning"),
        "gemini_rate_warning": gemini_result.get("rate_warning"),
    }


# == POST /arena/debate ====================================================

@router.post("/debate")
async def arena_debate(req: DebateRequest):
    session = _get_session(req.session_id)
    system_prompt = session["system_prompt"]

    # Map brain names to their responses and models
    brain_responses = {
        "claude": req.claude_response,
        "gpt": req.gpt_response,
        "gemini": req.gemini_response,
    }
    brain_labels = {"claude": "Claude", "gpt": "GPT", "gemini": "Gemini"}
    brain_models = {
        "claude": req.claude_model,
        "gpt": req.gpt_model,
        "gemini": req.gemini_model,
    }
    brain_callers = {
        "claude": _call_claude,
        "gpt": _call_gpt,
        "gemini": _call_gemini,
    }

    a, b = req.brain_a, req.brain_b

    debate_prompt_a = (
        f"The user asked: \"{req.original_prompt}\"\n\n"
        f"Your response was:\n{brain_responses[a]}\n\n"
        f"Another AI ({brain_labels[b]}) responded with:\n{brain_responses[b]}\n\n"
        "Critique the other AI's response. What did it get right? What did it get wrong? "
        "Where does your answer differ and why is your approach better or worse? Be honest and direct."
    )

    debate_prompt_b = (
        f"The user asked: \"{req.original_prompt}\"\n\n"
        f"Your response was:\n{brain_responses[b]}\n\n"
        f"Another AI ({brain_labels[a]}) responded with:\n{brain_responses[a]}\n\n"
        "Critique the other AI's response. What did it get right? What did it get wrong? "
        "Where does your answer differ and why is your approach better or worse? Be honest and direct."
    )

    task_a = brain_callers[a]([{"role": "user", "content": debate_prompt_a}], system_prompt, brain_models[a], req.session_id)
    task_b = brain_callers[b]([{"role": "user", "content": debate_prompt_b}], system_prompt, brain_models[b], req.session_id)

    result_a, result_b = await asyncio.gather(task_a, task_b, return_exceptions=True)

    if isinstance(result_a, Exception):
        result_a = {"text": f"[ERROR] {result_a}", "tool_calls": [], "rate_warning": None}
    if isinstance(result_b, Exception):
        result_b = {"text": f"[ERROR] {result_b}", "tool_calls": [], "rate_warning": None}

    # Log debate to Supabase
    _sb_insert("arena_debates", {
        "id": str(uuid.uuid4()),
        "session_id": req.session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "brain_a": a,
        "brain_b": b,
        "critique_a": result_a["text"][:10000],
        "critique_b": result_b["text"][:10000],
    })

    return {
        "brain_a": a,
        "brain_b": b,
        "debate_a": result_a["text"],
        "debate_b": result_b["text"],
        "tools_a": result_a["tool_calls"],
        "tools_b": result_b["tool_calls"],
    }


# == POST /arena/prefer ====================================================

@router.post("/prefer")
async def arena_prefer(req: PreferenceRequest):
    sb = _sb()
    if not sb:
        logger.warning("Supabase unavailable -- preference not logged")
        return {"status": "skipped", "reason": "supabase_unavailable"}

    row = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_input": req.user_input[:2000],
        "claude_response": req.claude_response[:5000],
        "gpt_response": req.gpt_response[:5000],
        "gemini_response": req.gemini_response[:5000],
        "preferred_model": req.preferred_model,
        "debate_claude": req.debate_claude[:5000] if req.debate_claude else None,
        "debate_gpt": req.debate_gpt[:5000] if req.debate_gpt else None,
        "debate_gemini": req.debate_gemini[:5000] if req.debate_gemini else None,
    }

    try:
        sb.table("arena_preferences").insert(row).execute()
        return {"status": "logged", "id": row["id"]}
    except Exception as e:
        err = str(e)
        if "does not exist" in err or "Could not find" in err:
            logger.warning("arena_preferences table not found -- run migration")
            return {"status": "skipped", "reason": "table_not_found"}
        logger.error("Failed to log preference: %s", e)
        return {"status": "error", "reason": str(e)[:200]}


# == GET /arena/log -- execution log for frontend ==========================

@router.get("/log")
async def arena_log(session_id: str = Query(...)):
    """Return the execution log for a session."""
    entries = list(_exec_log.get(session_id, []))
    return {"session_id": session_id, "entries": entries}


# == GET /arena (serve HTML) ================================================

@router.get("", include_in_schema=False)
async def arena_page():
    html_path = Path(__file__).parent.parent / "static" / "arena.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    raise HTTPException(status_code=404, detail="Arena page not found")
