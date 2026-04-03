"""AI Arena -- 7 Brains side-by-side chat with full intelligence wiring.

Claude + GPT get full tool loops. All others get pure reasoning.
Fallback routing: Groq -> OpenRouter, Cerebras -> Groq, GPT -> GitHub Models.

POST /arena/chat   -- send message, get parallel responses from all active brains
POST /arena/debate -- pick 2 brains to critique each other
POST /arena/prefer -- log preference to Supabase
GET  /arena/log    -- get execution log for a session
GET  /arena/brains -- get brain registry for frontend
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
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from services.supabase_client import get_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/arena", tags=["arena"])

# In-memory conversation history per session
_sessions: dict[str, dict[str, Any]] = {}
MAX_SESSIONS = 50
MAX_TOOL_ROUNDS = 3  # Cap tool rounds to keep arena responses fast

# In-memory execution log per session (recent entries, capped)
_exec_log: dict[str, deque] = {}
_MAX_LOG_ENTRIES = 200

# Rate limit tracking -- initialized for all brains
_rate_tracker: dict[str, dict] = {}
_RATE_WINDOW = 3600  # 1 hour window


def _track_rate(brain: str) -> str | None:
    """Track API call rate. Returns warning string if approaching limit."""
    now = time.time()
    if brain not in _rate_tracker:
        _rate_tracker[brain] = {"count": 0, "window_start": 0.0, "limit_hit": False}
    t = _rate_tracker[brain]
    if now - t["window_start"] > _RATE_WINDOW:
        t["count"] = 0
        t["window_start"] = now
        t["limit_hit"] = False
    t["count"] += 1
    if brain == "claude" and t["count"] >= 70:
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
where you compete against other AI models. You have FULL access to Johan's infrastructure \
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


ALL_BRAIN_KEYS = ["claude", "gpt", "gemini", "deepseek", "llama", "mistral", "qwen"]


def _get_session(session_id: str) -> dict[str, Any]:
    if session_id not in _sessions:
        _prune_sessions()
        session: dict[str, Any] = {"system_prompt": ARENA_SYSTEM_PROMPT}
        for key in ALL_BRAIN_KEYS:
            session[key] = []
        _sessions[session_id] = session
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
    active_brains: list[str] = ["claude", "gpt", "gemini"]
    models: dict[str, str] = {}  # optional per-brain model override


class DebateRequest(BaseModel):
    session_id: str
    brain_a: str = "claude"
    brain_b: str = "gpt"
    brains: list[str] = []  # N-brain debate (overrides brain_a/brain_b if set)
    original_prompt: str
    responses: dict[str, str] = {}  # brain_key -> response text
    models: dict[str, str] = {}


class PreferenceRequest(BaseModel):
    session_id: str
    user_input: str
    responses: dict[str, str] = {}  # brain_key -> response text
    preferred_model: str
    debates: dict[str, str] = {}


# == Git Auto-Backup (Part 7A) ============================================

def _git_auto_backup(path: str, model: str) -> str | None:
    """Create a git backup branch before a write/command modifies code."""
    try:
        p = Path(path).expanduser().resolve()
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
        cur = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=str(git_root), timeout=5,
        )
        current_branch = cur.stdout.strip()
        if not current_branch or current_branch == "HEAD":
            return None

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

    _log_exec(session_id, {
        "model": model,
        "tool": tool_name,
        "input": json.dumps(tool_input)[:300],
        "output": result_text[:300],
        "success": success,
        "git_branch": git_branch,
    })

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

MCP_SERVERS = [
    {"type": "url", "url": "https://pubmed.mcp.claude.com/mcp", "name": "pubmed"},
]

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
    """Call Anthropic Messages API with MCP servers + direct tools."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"text": "[ERROR] ANTHROPIC_API_KEY not set", "tool_calls": [], "rate_warning": None}

    rate_warning = _track_rate("claude")
    if _rate_tracker.get("claude", {}).get("limit_hit"):
        return {
            "text": "[RATE LIMITED] Claude has hit the hourly message limit. Wait a few minutes and try again.",
            "tool_calls": [],
            "rate_warning": "Claude rate limit reached",
        }

    anthropic_messages = [{"role": m["role"], "content": m["content"]} for m in messages]
    tool_log: list[dict] = []
    use_mcp = bool(MCP_SERVERS)

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

        if use_mcp:
            payload["mcp_servers"] = MCP_SERVERS
            headers["anthropic-beta"] = "mcp-client-2025-04-04"

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
            )

        if resp.status_code == 429:
            _rate_tracker.setdefault("claude", {})["limit_hit"] = True
            return {
                "text": "[RATE LIMITED] Claude API rate limit reached. Try again in a few minutes.",
                "tool_calls": tool_log,
                "rate_warning": "Rate limit hit (429)",
            }

        if resp.status_code != 200:
            if use_mcp and ("MCP" in resp.text or "mcp" in resp.text or "503" in resp.text):
                logger.warning("Claude MCP error (status %d), retrying without MCP: %s", resp.status_code, resp.text[:300])
                use_mcp = False
                continue
            logger.error("Claude API error %d: %s", resp.status_code, resp.text[:500])
            return {
                "text": f"[ERROR] Claude API returned {resp.status_code}: {resp.text[:300]}",
                "tool_calls": tool_log,
                "rate_warning": rate_warning,
            }

        data = resp.json()
        stop_reason = data.get("stop_reason", "end_turn")

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
                if tool_log and tool_log[-1].get("source") == "mcp":
                    mcp_content = block.get("content", "")
                    if isinstance(mcp_content, list):
                        mcp_content = json.dumps(mcp_content)[:500]
                    elif isinstance(mcp_content, str):
                        mcp_content = mcp_content[:500]
                    tool_log[-1]["result"] = str(mcp_content)[:500]

        if stop_reason == "tool_use":
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

            anthropic_messages.append({"role": "assistant", "content": data["content"]})
            anthropic_messages.append({"role": "user", "content": tool_results})
            continue

        text_parts = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block["text"])
        return {
            "text": "\n".join(text_parts) or "[No response]",
            "tool_calls": tool_log,
            "rate_warning": rate_warning,
        }

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
    """Call OpenAI Chat Completions API with function calling tools."""
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

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if resp.status_code == 429:
            # Fallback to GitHub Models
            logger.warning("GPT rate limited, trying GitHub Models fallback")
            return await _call_github(messages, system_prompt, model, session_id)

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

                openai_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })
            continue

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
    """Call Google Gemini generateContent API. No tool calling -- pure reasoning only."""
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        return {"text": "[ERROR] GOOGLE_API_KEY not set", "tool_calls": [], "rate_warning": None}

    _track_rate("gemini")

    contents = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})

    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": {"maxOutputTokens": 8192},
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


# == OpenAI-compatible caller (shared by Groq, Cerebras, Mistral, GitHub, OpenRouter) ==

async def _call_openai_compatible(
    messages: list[dict],
    system_prompt: str,
    model: str,
    session_id: str,
    endpoint: str,
    api_key: str,
    brain_name: str,
    extra_headers: dict[str, str] | None = None,
    timeout: float = 75.0,
) -> dict[str, Any]:
    """Generic OpenAI-compatible chat/completions caller. No tool loop."""
    if not api_key:
        return {"text": f"[ERROR] API key not set for {brain_name}", "tool_calls": [], "rate_warning": None}

    _track_rate(brain_name)

    openai_messages = [{"role": "system", "content": system_prompt}]
    for m in messages:
        openai_messages.append({"role": m["role"], "content": m["content"]})

    payload = {
        "model": model,
        "max_tokens": 8192,
        "messages": openai_messages,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(endpoint, headers=headers, json=payload)

        if resp.status_code in (429, 413, 402):
            return {
                "text": f"[RATE LIMITED] {brain_name} rate limit reached.",
                "tool_calls": [],
                "rate_warning": f"Rate limit hit ({resp.status_code})",
                "rate_limited": True,
            }

        if resp.status_code != 200:
            logger.error("%s API error %d: %s", brain_name, resp.status_code, resp.text[:500])
            return {
                "text": f"[ERROR] {brain_name} API returned {resp.status_code}: {resp.text[:300]}",
                "tool_calls": [],
                "rate_warning": None,
            }

        data = resp.json()
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        text = message.get("content") or "[No response]"

        # Strip <think>...</think> blocks from reasoning models (DeepSeek R1, Qwen QwQ)
        import re
        text = re.sub(r"<think>[\s\S]*?</think>\s*", "", text).strip()

        return {
            "text": text,
            "tool_calls": [],
            "rate_warning": None,
        }

    except httpx.TimeoutException:
        return {"text": f"[ERROR] {brain_name} API request timed out", "tool_calls": [], "rate_warning": None}
    except Exception as e:
        logger.error("%s API call failed: %s", brain_name, e)
        return {"text": f"[ERROR] {brain_name} call failed: {e}", "tool_calls": [], "rate_warning": None}


# == Groq (DeepSeek R1, Llama, Qwen) ======================================

async def _call_groq(
    messages: list[dict],
    system_prompt: str,
    model: str,
    session_id: str = "",
) -> dict[str, Any]:
    result = await _call_openai_compatible(
        messages, system_prompt, model, session_id,
        endpoint="https://api.groq.com/openai/v1/chat/completions",
        api_key=os.environ.get("GROQ_API_KEY", ""),
        brain_name="groq",
    )
    # Fallback to OpenRouter on rate limit or error
    if result.get("rate_limited") or "[ERROR]" in result.get("text", ""):
        logger.warning("Groq failed for %s, trying OpenRouter fallback", model)
        or_model_map = {
            "llama-3.3-70b-versatile": "meta-llama/llama-3.3-70b-instruct:free",
            "qwen/qwen3-32b": "qwen/qwen3-next-80b-a3b-instruct:free",
        }
        or_model = or_model_map.get(model)
        if or_model:
            fallback = await _call_openrouter(messages, system_prompt, or_model, session_id)
            if "[ERROR]" not in fallback.get("text", "") and not fallback.get("rate_limited"):
                fallback["fallback_used"] = True
                fallback["fallback_provider"] = "openrouter"
                return fallback
    return result


async def _call_open_model(
    messages: list[dict],
    system_prompt: str,
    model: str,
    session_id: str = "",
    groq_model: str = "",
) -> dict[str, Any]:
    """Try OpenRouter first, fall back to Groq if it fails."""
    result = await _call_openrouter(messages, system_prompt, model, session_id)
    if result.get("rate_limited") or "[ERROR]" in result.get("text", ""):
        if groq_model:
            logger.warning("OpenRouter failed for %s, trying Groq fallback %s", model, groq_model)
            fallback = await _call_openai_compatible(
                messages, system_prompt, groq_model, session_id,
                endpoint="https://api.groq.com/openai/v1/chat/completions",
                api_key=os.environ.get("GROQ_API_KEY", ""),
                brain_name="groq",
            )
            if "[ERROR]" not in fallback.get("text", "") and not fallback.get("rate_limited"):
                fallback["fallback_used"] = True
                fallback["fallback_provider"] = "groq"
                return fallback
    return result


# == Cerebras (Llama fallback) =============================================

async def _call_cerebras(
    messages: list[dict],
    system_prompt: str,
    model: str,
    session_id: str = "",
) -> dict[str, Any]:
    result = await _call_openai_compatible(
        messages, system_prompt, model, session_id,
        endpoint="https://api.cerebras.ai/v1/chat/completions",
        api_key=os.environ.get("CEREBRAS_API_KEY", ""),
        brain_name="cerebras",
    )
    # Fallback to Groq on rate limit
    if result.get("rate_limited"):
        logger.warning("Cerebras rate limited for %s, trying Groq fallback", model)
        groq_model_map = {
            "llama3.1-8b": "llama-3.1-8b-instant",
        }
        groq_model = groq_model_map.get(model, "llama-3.3-70b-versatile")
        fallback = await _call_groq(messages, system_prompt, groq_model, session_id)
        fallback["fallback_used"] = True
        fallback["fallback_provider"] = "groq"
        return fallback
    return result


# == Mistral ===============================================================

async def _call_mistral(
    messages: list[dict],
    system_prompt: str,
    model: str,
    session_id: str = "",
) -> dict[str, Any]:
    result = await _call_openai_compatible(
        messages, system_prompt, model, session_id,
        endpoint="https://api.mistral.ai/v1/chat/completions",
        api_key=os.environ.get("MISTRAL_API_KEY", ""),
        brain_name="mistral",
    )
    if result.get("rate_limited"):
        logger.warning("Mistral rate limited, trying OpenRouter fallback")
        fallback = await _call_openrouter(messages, system_prompt, "mistralai/mistral-large-latest", session_id)
        fallback["fallback_used"] = True
        fallback["fallback_provider"] = "openrouter"
        return fallback
    return result


# == GitHub Models (free GPT-4o fallback) ==================================

async def _call_github(
    messages: list[dict],
    system_prompt: str,
    model: str,
    session_id: str = "",
) -> dict[str, Any]:
    return await _call_openai_compatible(
        messages, system_prompt, model if "/" not in model else "gpt-4o", session_id,
        endpoint="https://models.inference.ai.azure.com/chat/completions",
        api_key=os.environ.get("GITHUB_MODELS_KEY", ""),
        brain_name="github-models",
    )


# == OpenRouter (universal fallback) =======================================

async def _call_openrouter(
    messages: list[dict],
    system_prompt: str,
    model: str,
    session_id: str = "",
) -> dict[str, Any]:
    return await _call_openai_compatible(
        messages, system_prompt, model, session_id,
        endpoint="https://openrouter.ai/api/v1/chat/completions",
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        brain_name="openrouter",
        extra_headers={"HTTP-Referer": "https://joao.theartofthepossible.io"},
    )


# == Brain Registry ========================================================

BRAINS = {
    "claude": {
        "name": "CLAUDE", "color": "#00e5ff", "tag": "THE BRAIN",
        "caller": _call_claude, "has_tools": True,
        "models": ["claude-sonnet-4-20250514", "claude-opus-4-20250514"],
        "default_model": "claude-sonnet-4-20250514",
        "trainable": False,
    },
    "gpt": {
        "name": "GPT", "color": "#00ff88", "tag": "THE GENERALIST",
        "caller": _call_gpt, "has_tools": True,
        "models": ["gpt-4o", "gpt-4.1"],
        "default_model": "gpt-4o",
        "trainable": False,
    },
    "gemini": {
        "name": "GEMINI", "color": "#4488ff", "tag": "GOOGLE ORACLE",
        "caller": _call_gemini, "has_tools": False,
        "models": ["gemini-2.5-flash", "gemini-3.1-pro-preview", "gemini-3-flash-preview"],
        "default_model": "gemini-2.5-flash",
        "trainable": False,
    },
    "deepseek": {
        "name": "DEEPSEEK", "color": "#b388ff", "tag": "REASONING BEAST",
        "caller": lambda m, s, model, sid: _call_open_model(m, s, model, sid, groq_model=""),
        "has_tools": False,
        "models": ["deepseek/deepseek-r1-distill-qwen-32b", "deepseek/deepseek-chat-v3.1"],
        "default_model": "deepseek/deepseek-r1-distill-qwen-32b",
        "trainable": True,
    },
    "llama": {
        "name": "LLAMA", "color": "#ff9800", "tag": "OPEN WARRIOR",
        "caller": lambda m, s, model, sid: _call_open_model(m, s, model, sid, groq_model="llama-3.3-70b-versatile"),
        "has_tools": False,
        "models": ["meta-llama/llama-3.3-70b-instruct:free"],
        "default_model": "meta-llama/llama-3.3-70b-instruct:free",
        "trainable": True,
    },
    "mistral": {
        "name": "MISTRAL", "color": "#ff4466", "tag": "EU FRONTIER",
        "caller": _call_mistral, "has_tools": False,
        "models": ["mistral-large-latest", "mistral-small-latest"],
        "default_model": "mistral-large-latest",
        "trainable": True,
    },
    "qwen": {
        "name": "QWEN", "color": "#00bcd4", "tag": "MATH KING",
        "caller": lambda m, s, model, sid: _call_open_model(m, s, model, sid, groq_model="qwen/qwen3-32b"),
        "has_tools": False,
        "models": ["qwen/qwen3-next-80b-a3b-instruct:free", "qwen/qwen3.6-plus:free"],
        "default_model": "qwen/qwen3-next-80b-a3b-instruct:free",
        "trainable": True,
    },
}


# == GET /arena/brains -- brain registry for frontend ======================

@router.get("/brains")
async def arena_brains():
    """Return the brain registry (without caller functions) for the frontend."""
    result = {}
    for key, brain in BRAINS.items():
        result[key] = {
            "name": brain["name"],
            "color": brain["color"],
            "tag": brain["tag"],
            "has_tools": brain["has_tools"],
            "models": brain["models"],
            "default_model": brain["default_model"],
            "trainable": brain["trainable"],
        }
    return result


# == POST /arena/chat ======================================================

@router.post("/chat")
async def arena_chat(req: ChatRequest):
    session = _get_session(req.session_id)

    if req.system_prompt:
        session["system_prompt"] = req.system_prompt
    system_prompt = session["system_prompt"]

    # Validate active brains
    active = [b for b in req.active_brains if b in BRAINS]
    if not active:
        active = ["claude"]
    # Claude is always on
    if "claude" not in active:
        active.insert(0, "claude")

    user_msg = {"role": "user", "content": req.message}

    # Append user message to all active brain histories
    for brain_key in active:
        if brain_key not in session:
            session[brain_key] = []
        session[brain_key].append(user_msg)

    # Build tasks for all active brains
    tasks = {}
    for brain_key in active:
        brain = BRAINS[brain_key]
        model = req.models.get(brain_key, brain["default_model"])
        caller = brain["caller"]
        tasks[brain_key] = caller(session[brain_key], system_prompt, model, req.session_id)

    # Execute all in parallel with per-brain timeout (80s to stay under Cloudflare's 100s)
    async def _with_timeout(coro, brain_key, timeout=80):
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            return {"text": f"[TIMEOUT] {brain_key} took too long to respond", "tool_calls": [], "rate_warning": None}

    keys = list(tasks.keys())
    results_list = await asyncio.gather(*[_with_timeout(tasks[k], k) for k in keys], return_exceptions=True)

    results = {}
    for i, key in enumerate(keys):
        r = results_list[i]
        if isinstance(r, Exception):
            r = {"text": f"[ERROR] {r}", "tool_calls": [], "rate_warning": None}
        results[key] = r
        # Store assistant response in session history
        session[key].append({"role": "assistant", "content": r["text"]})

    # Log conversation to Supabase
    conv_id = str(uuid.uuid4())
    log_row = {
        "id": conv_id,
        "session_id": req.session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_input": req.message[:5000],
        "system_prompt": system_prompt[:2000],
        # Store all brain responses as JSON
        "claude_response": results.get("claude", {}).get("text", "")[:10000],
        "gpt_response": results.get("gpt", {}).get("text", "")[:10000],
        "gemini_response": results.get("gemini", {}).get("text", "")[:10000],
    }
    _sb_insert("arena_conversations", log_row)

    # Build response
    response = {
        "session_id": req.session_id,
        "conversation_id": conv_id,
        "active_brains": active,
        "responses": {},
    }
    for key in active:
        r = results[key]
        brain = BRAINS[key]
        model_used = req.models.get(key, brain["default_model"])
        response["responses"][key] = {
            "text": r["text"],
            "tool_calls": r.get("tool_calls", []),
            "model": model_used,
            "rate_warning": r.get("rate_warning"),
            "fallback_used": r.get("fallback_used", False),
            "fallback_provider": r.get("fallback_provider"),
        }

    return response


# == POST /arena/chat/stream -- SSE streaming, each brain emits as it finishes
@router.post("/chat/stream")
async def arena_chat_stream(req: ChatRequest):
    session = _get_session(req.session_id)

    if req.system_prompt:
        session["system_prompt"] = req.system_prompt
    system_prompt = session["system_prompt"]

    active = [b for b in req.active_brains if b in BRAINS]
    if not active:
        active = ["claude"]
    if "claude" not in active:
        active.insert(0, "claude")

    user_msg = {"role": "user", "content": req.message}
    for brain_key in active:
        if brain_key not in session:
            session[brain_key] = []
        session[brain_key].append(user_msg)

    conv_id = str(uuid.uuid4())

    async def _stream():
        results_store: dict[str, Any] = {}
        done_event = asyncio.Event()
        pending = set(active)

        async def _run_brain(key):
            brain = BRAINS[key]
            model = req.models.get(key, brain["default_model"])
            try:
                result = await asyncio.wait_for(
                    brain["caller"](session[key], system_prompt, model, req.session_id),
                    timeout=80,
                )
            except asyncio.TimeoutError:
                result = {"text": f"[TIMEOUT] {key} took too long", "tool_calls": [], "rate_warning": None}
            except Exception as e:
                result = {"text": f"[ERROR] {e}", "tool_calls": [], "rate_warning": None}

            session[key].append({"role": "assistant", "content": result["text"]})
            results_store[key] = result
            return key, result

        tasks = [asyncio.create_task(_run_brain(k)) for k in active]

        for coro in asyncio.as_completed(tasks):
            key, result = await coro
            brain = BRAINS[key]
            model_used = req.models.get(key, brain["default_model"])
            payload = {
                "brain": key,
                "text": result["text"],
                "tool_calls": result.get("tool_calls", []),
                "model": model_used,
                "rate_warning": result.get("rate_warning"),
                "fallback_used": result.get("fallback_used", False),
                "fallback_provider": result.get("fallback_provider"),
            }
            yield f"data: {json.dumps(payload)}\n\n"

        # Log to supabase
        log_row = {
            "id": conv_id,
            "session_id": req.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_input": req.message[:5000],
            "system_prompt": system_prompt[:2000],
            "claude_response": results_store.get("claude", {}).get("text", "")[:10000],
            "gpt_response": results_store.get("gpt", {}).get("text", "")[:10000],
            "gemini_response": results_store.get("gemini", {}).get("text", "")[:10000],
        }
        _sb_insert("arena_conversations", log_row)

        yield "data: [DONE]\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


# == POST /arena/debate ====================================================

@router.post("/debate")
async def arena_debate(req: DebateRequest):
    session = _get_session(req.session_id)
    system_prompt = session["system_prompt"]

    # Support N-brain debate via `brains` list, fallback to legacy brain_a/brain_b
    debate_brains = req.brains if len(req.brains) >= 2 else [req.brain_a, req.brain_b]
    for key in debate_brains:
        if key not in BRAINS:
            raise HTTPException(400, f"Invalid brain: {key}")

    # Build critique prompt for each brain: critique all other participants
    tasks = []
    for key in debate_brains:
        brain = BRAINS[key]
        own_response = req.responses.get(key, "")
        others_text = ""
        for other_key in debate_brains:
            if other_key == key:
                continue
            other_name = BRAINS[other_key]["name"]
            other_response = req.responses.get(other_key, "")
            others_text += f"\n\n--- {other_name} responded ---\n{other_response}"

        debate_prompt = (
            f"The user asked: \"{req.original_prompt}\"\n\n"
            f"Your response was:\n{own_response}\n\n"
            f"Other AIs responded:{others_text}\n\n"
            "Critique each of the other AIs' responses. What did they get right? What did they get wrong? "
            "Where does your answer differ and why is your approach better or worse? Be honest and direct."
        )

        model = req.models.get(key, brain["default_model"])
        tasks.append((key, brain["caller"]([{"role": "user", "content": debate_prompt}], system_prompt, model, req.session_id)))

    results_raw = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)

    critiques = {}
    for i, (key, _) in enumerate(tasks):
        result = results_raw[i]
        if isinstance(result, Exception):
            result = {"text": f"[ERROR] {result}", "tool_calls": [], "rate_warning": None}
        critiques[key] = result

    _sb_insert("arena_debates", {
        "id": str(uuid.uuid4()),
        "session_id": req.session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "brain_a": debate_brains[0],
        "brain_b": debate_brains[1] if len(debate_brains) > 1 else "",
        "critique_a": critiques[debate_brains[0]]["text"][:10000],
        "critique_b": critiques[debate_brains[1]]["text"][:10000] if len(debate_brains) > 1 else "",
    })

    # Return both legacy format (for 2-brain compat) and new multi-brain format
    response: dict[str, Any] = {
        "brains": debate_brains,
        "critiques": {k: {"text": v["text"], "tools": v.get("tool_calls", [])} for k, v in critiques.items()},
    }
    # Legacy compat
    if len(debate_brains) >= 2:
        response["brain_a"] = debate_brains[0]
        response["brain_b"] = debate_brains[1]
        response["debate_a"] = critiques[debate_brains[0]]["text"]
        response["debate_b"] = critiques[debate_brains[1]]["text"]
        response["tools_a"] = critiques[debate_brains[0]].get("tool_calls", [])
        response["tools_b"] = critiques[debate_brains[1]].get("tool_calls", [])

    return response


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
        "preferred_model": req.preferred_model,
        # Store top 3 for backward compat with existing table
        "claude_response": req.responses.get("claude", "")[:5000],
        "gpt_response": req.responses.get("gpt", "")[:5000],
        "gemini_response": req.responses.get("gemini", "")[:5000],
        "debate_claude": req.debates.get("claude", "")[:5000] or None,
        "debate_gpt": req.debates.get("gpt", "")[:5000] or None,
        "debate_gemini": req.debates.get("gemini", "")[:5000] or None,
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
