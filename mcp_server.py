"""FastMCP tools — JOAO MCP server.

12 joao_* tools (JOAO First Law: every capability here before anywhere else):
  joao_chat, joao_memory_read, joao_memory_write,
  joao_learn_youtube, joao_learn_pdf, joao_learn_excel,
  joao_learn_url, joao_learn_docx,
  joao_council_dispatch, joao_council_status,
  joao_agent_output, joao_qa_review

Legacy tools retained for backward compatibility:
  dispatch_agent, capture_idea, get_status, query_memory,
  ftp_access, council_dispatch, council_status, council_session_output
"""

from __future__ import annotations

import logging
import os
import time

import httpx
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


from models.schemas import (
    AgentOutputRecord,
    DispatchLogRecord,
    IdeaVaultRecord,
    SessionLogRecord,
)
from services import ai_processor, dispatch, ftp_client, supabase_client, telegram

_RAILWAY_HOST = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")

_allowed_hosts = [
    "127.0.0.1:*", "localhost:*", "[::1]:*", "192.168.0.*:*",
    "100.93.94.121:*",                        # Tailscale
    "joao.theartofthepossible.io",             # Cloudflare tunnel
    "taop-mcp.theartofthepossible.io",         # MCP-dedicated tunnel
]
if _RAILWAY_HOST:
    _allowed_hosts.append(_RAILWAY_HOST)

mcp = FastMCP(
    "joao-spine",
    transport_security={"allowed_hosts": _allowed_hosts},
)


@mcp.tool()
async def dispatch_agent(session_name: str, command: str, wait: bool = False) -> str:
    """Dispatch a shell command to a tmux session on the home server.

    Uses HTTP tunnel (Railway -> Cloudflare -> ROG Strix). Falls back to SSH
    only for tunnel connectivity failures (not for application errors).

    Args:
        session_name: tmux session name / agent name (e.g. IRIS, BYTE)
        command: Shell command or task to execute
        wait: If True, capture and return terminal output after dispatch
    """
    import asyncio

    t0 = time.time()
    status = "sent"
    output = ""

    try:
        result = await dispatch.dispatch_raw_to_agent(session_name, command)
        status = result.get("status", "sent")
        output = result.get("output", "")

        if wait:
            await asyncio.sleep(3)
            try:
                session_data = await dispatch.get_session(session_name)
                output = session_data.get("output", "")
                status = "completed"
            except Exception:
                pass
    except (httpx.ConnectError, httpx.ConnectTimeout) as tunnel_err:
        # Tunnel unreachable -- try SSH as local-network fallback (with timeout)
        logger.warning("dispatch_agent tunnel unreachable, trying SSH: %s", tunnel_err)
        try:
            ssh_result = await asyncio.wait_for(
                dispatch.dispatch_command(session_name, command, wait),
                timeout=15.0,
            )
            status = ssh_result["status"]
            output = ssh_result.get("output", "")
        except asyncio.TimeoutError:
            status = "error"
            output = "SSH fallback timed out (home server unreachable from Railway)"
        except Exception as ssh_err:
            status = "error"
            output = f"SSH fallback failed: {ssh_err}"
    except Exception as e:
        # Application error (400, 422, etc.) -- don't fall back to SSH
        status = "error"
        output = str(e)

    duration_ms = int((time.time() - t0) * 1000)

    await supabase_client.insert_session_log(
        SessionLogRecord(
            endpoint="mcp/dispatch_agent",
            action="dispatch",
            input_summary=f"{session_name}: {command[:100]}",
            output_summary=output[:200],
            status=status,
            duration_ms=duration_ms,
        )
    )
    await supabase_client.insert_agent_output(
        AgentOutputRecord(
            session_name=session_name,
            command=command,
            output=output,
            status=status,
        )
    )

    if status == "error":
        return f"Error: {output}"
    if wait and output:
        return f"[{status}] Output:\n{output}"
    return f"Command sent to session '{session_name}': {command}"


@mcp.tool()
async def capture_idea(text: str, source: str = "mcp", context: str = "") -> str:
    """Process text through AI and save to idea vault with Telegram notification.

    Args:
        text: The text content to process
        source: Origin of the content (default: mcp)
        context: Optional context for AI processing
    """
    t0 = time.time()
    ai_result = await ai_processor.process_text(text, context)

    vault_record = IdeaVaultRecord(
        source=source,
        title=ai_result.title,
        content=text,
        summary=ai_result.summary,
        tags=ai_result.tags,
        metadata={"key_points": ai_result.key_points, "context": context},
    )
    vault_row = await supabase_client.insert_idea_vault(vault_record)
    duration_ms = int((time.time() - t0) * 1000)

    await supabase_client.insert_session_log(
        SessionLogRecord(
            endpoint="mcp/capture_idea",
            action="capture_idea",
            input_summary=text[:200],
            output_summary=ai_result.summary[:200],
            status="ok",
            duration_ms=duration_ms,
        )
    )

    notify_msg = f"*{ai_result.title}*\n{ai_result.summary}\nTags: {', '.join(ai_result.tags)}"
    await telegram.send_notification(notify_msg)

    return (
        f"Saved: {ai_result.title}\n"
        f"Summary: {ai_result.summary}\n"
        f"Tags: {', '.join(ai_result.tags)}\n"
        f"ID: {vault_row.get('id', 'n/a')}"
    )


@mcp.tool()
async def get_status() -> str:
    """Get server uptime and recent activity from session log."""
    from routers.joao import _start_time

    uptime = time.time() - _start_time
    recent = await supabase_client.query_recent_activity(limit=5)

    lines = [f"Uptime: {uptime:.0f}s", f"Recent activity ({len(recent)} entries):"]
    for entry in recent:
        lines.append(
            f"  - [{entry.get('status')}] {entry.get('endpoint')} / {entry.get('action')} "
            f"({entry.get('duration_ms', 0)}ms)"
        )
    return "\n".join(lines)


@mcp.tool()
async def query_memory(query: str, limit: int = 10) -> str:
    """Search the idea vault for past ideas, notes, and captured content.

    Args:
        query: Search text (matched against title, summary, content)
        limit: Maximum number of results (default: 10)
    """
    results = await supabase_client.query_memory(query, limit)

    if not results:
        return f"No results found for: {query}"

    lines = [f"Found {len(results)} result(s) for '{query}':"]
    for r in results:
        tags = ", ".join(r.get("tags") or [])
        lines.append(
            f"\n- **{r.get('title', 'Untitled')}** ({r.get('source', '?')})\n"
            f"  {r.get('summary', 'No summary')}\n"
            f"  Tags: {tags}\n"
            f"  Created: {r.get('created_at', '?')}"
        )
    return "\n".join(lines)


# ── Council Dispatch MCP Tools ────────────────────────────────────────────


@mcp.tool()
async def ftp_access(
    action: str,
    host: str,
    remote_path: str,
    user: str,
    password: str,
    port: int = 21,
    local_path: str = "",
) -> str:
    """Access files on a remote FTP server — list, download, upload, or delete.

    Args:
        action: Operation to perform — list, get, put, or delete
        host: FTP server hostname or IP
        remote_path: Path on the remote server
        user: FTP username
        password: FTP password
        port: FTP port (default: 21)
        local_path: Local file path (required for get/put)
    """
    t0 = time.time()
    logger.info("ftp_access: action=%s host=%s path=%s", action, host, remote_path)

    try:
        if action == "list":
            entries = await ftp_client.ftp_list(host, port, user, password, remote_path)
            lines = [f"FTP listing for {remote_path} on {host}:"]
            for e in entries:
                prefix = "[DIR]" if e.get("type") == "dir" else "[FILE]"
                size = f" ({e['size']}B)" if e.get("size") else ""
                lines.append(f"  {prefix} {e['name']}{size}")
            result_text = "\n".join(lines) if entries else f"Empty directory: {remote_path}"
        elif action == "get":
            if not local_path:
                return "Error: local_path is required for get action"
            result_text = await ftp_client.ftp_get(host, port, user, password, remote_path, local_path)
        elif action == "put":
            if not local_path:
                return "Error: local_path is required for put action"
            result_text = await ftp_client.ftp_put(host, port, user, password, local_path, remote_path)
        elif action == "delete":
            result_text = await ftp_client.ftp_delete(host, port, user, password, remote_path)
        else:
            return f"Error: Unknown action '{action}'. Use list, get, put, or delete."
    except Exception as e:
        logger.exception("ftp_access failed")
        return f"FTP {action} failed: {e}"

    duration_ms = int((time.time() - t0) * 1000)
    await supabase_client.insert_session_log(
        SessionLogRecord(
            endpoint="mcp/ftp_access",
            action=f"ftp_{action}",
            input_summary=f"{host}:{port} {remote_path}",
            output_summary=result_text[:200],
            status="ok",
            duration_ms=duration_ms,
        )
    )
    return result_text


@mcp.tool()
async def council_dispatch(
    agent: str,
    task: str,
    priority: str = "normal",
    context: str = "",
    project: str = "",
) -> str:
    """Dispatch a task to a Council agent (BYTE, ARIA, CJ, SOFIA, DEX, GEMMA).

    The agent receives the task in their tmux session and executes autonomously.

    Args:
        agent: Agent name — BYTE (engineering), ARIA (architecture), CJ (product),
               SOFIA (design), DEX (support), GEMMA (research)
        task: Detailed task description for the agent
        priority: normal, urgent, or critical
        context: Optional additional context
        project: Optional project name
    """
    logger.info("council_dispatch called: agent=%s task=%s", agent, task[:80])
    try:
        result = await dispatch.dispatch_to_agent(
            agent=agent,
            task=task,
            priority=priority,
            context=context or None,
            project=project or None,
        )
        logger.info("council_dispatch success: %s", result)
    except Exception as e:
        logger.exception("council_dispatch failed")
        return f"Dispatch failed: {e}"

    try:
        await supabase_client.insert_dispatch_log(
            DispatchLogRecord(
                agent=agent,
                task=task,
                priority=priority,
                project=project or None,
                status=result.get("status", "unknown"),
                session=result.get("session"),
            )
        )
    except Exception:
        logger.warning("Failed to log dispatch to Supabase", exc_info=True)

    try:
        return (
            f"Dispatched to {agent}:\n"
            f"Task: {task[:200]}\n"
            f"Priority: {priority}\n"
            f"Session: {result.get('session', 'unknown')}\n"
            f"Status: {result.get('status', 'unknown')}"
        )
    except Exception as e:
        logger.exception("council_dispatch response formatting failed")
        return f"Dispatched to {agent} but error formatting response: {e}"


@mcp.tool()
async def council_status() -> str:
    """Check which Council agents are active and their tmux session status."""
    logger.info("council_status called")
    try:
        result = await dispatch.get_agents()
        logger.info("council_status got result: %s", result)

        agents = result.get("agents", {})
        lines = ["Council Agent Status:"]
        for name, info in agents.items():
            status = "ACTIVE" if info.get("active") else "INACTIVE"
            lines.append(f"  {name}: {status} (session: {info.get('session', '?')})")
        return "\n".join(lines)
    except Exception as e:
        logger.exception("council_status failed")
        return f"Failed to reach local server: {e}"


@mcp.tool()
async def council_session_output(agent: str) -> str:
    """Get the recent terminal output from a Council agent's tmux session.

    Args:
        agent: Agent name (BYTE, ARIA, CJ, SOFIA, DEX, GEMMA)
    """
    logger.info("council_session_output called: agent=%s", agent)
    try:
        result = await dispatch.get_session(agent)
        logger.info("council_session_output got %d bytes", len(result.get("output", "")))

        return (
            f"Agent: {result.get('agent', agent)}\n"
            f"Session: {result.get('session', '?')}\n"
            f"--- Output ---\n{result.get('output', 'No output')}"
        )
    except Exception as e:
        logger.exception("council_session_output failed")
        return f"Failed to get session for {agent}: {e}"


# ── JOAO MCP Tools (JOAO First Law) ──────────────────────────────────────────
# All 12 joao_* tools — every capability available to JOAO before anywhere else.

from tools.chat import joao_chat as _joao_chat
from tools.memory import joao_memory_read as _joao_memory_read, joao_memory_write as _joao_memory_write
from tools.learning import (
    joao_learn_youtube as _joao_learn_youtube,
    joao_learn_pdf as _joao_learn_pdf,
    joao_learn_excel as _joao_learn_excel,
    joao_learn_url as _joao_learn_url,
    joao_learn_docx as _joao_learn_docx,
)
from tools.council import (
    joao_council_dispatch as _joao_council_dispatch,
    joao_council_status as _joao_council_status,
    joao_agent_output as _joao_agent_output,
    joao_qa_review as _joao_qa_review,
)


@mcp.tool()
async def joao_chat(message: str, context: str = "") -> str:
    """Send a message to JOAO and get a response via Claude Sonnet.

    JOAO_MASTER_CONTEXT.md is loaded as the system prompt — full identity, stack,
    and project awareness. Response is logged to JOAO_SESSION_LOG.md.

    Args:
        message: The message or question for JOAO
        context: Optional additional context to include
    """
    return await _joao_chat(message, context)


@mcp.tool()
async def joao_memory_read(
    file: str = "master",
    tail_lines: int = 0,
) -> str:
    """Read JOAO memory files — master context or session log.

    Args:
        file: 'master' (JOAO_MASTER_CONTEXT.md) or 'session' (JOAO_SESSION_LOG.md)
        tail_lines: If > 0, return only the last N lines. 0 returns full file.
    """
    return await _joao_memory_read(file, tail_lines)  # type: ignore[arg-type]


@mcp.tool()
async def joao_memory_write(
    content: str,
    file: str = "session",
    header: str = "",
) -> str:
    """Append content to JOAO memory files. Append-only — never overwrites.

    Session log entries get a timestamped header automatically.

    Args:
        content: Text to append
        file: 'session' (default) or 'master'
        header: Optional section label (auto-timestamped)
    """
    return await _joao_memory_write(content, file, header)  # type: ignore[arg-type]


@mcp.tool()
async def joao_learn_youtube(url: str) -> str:
    """Extract a YouTube transcript, analyze with Claude, feed insights to JOAO's brain.

    Auto-transcript first, Whisper fallback. Claude maps every insight to active
    TAOP/JOAO projects. Appended to JOAO_SESSION_LOG.md.

    Args:
        url: Full YouTube URL (youtube.com or youtu.be)
    """
    return await _joao_learn_youtube(url)


@mcp.tool()
async def joao_learn_pdf(file_path: str) -> str:
    """Extract a PDF, generate an HTML intelligence report, feed insights to JOAO's brain.

    Reads the file from the server filesystem. Claude generates a full HTML report
    saved to /joao/outputs/. Learning summary appended to JOAO_SESSION_LOG.md.

    Args:
        file_path: Absolute path to the .pdf file on the server
    """
    return await _joao_learn_pdf(file_path)


@mcp.tool()
async def joao_learn_excel(file_path: str) -> str:
    """Parse Excel/CSV, run Dr. Data full BI analysis, generate HTML dashboard.

    Dr. Data analyzes patterns, anomalies, and business insights. Produces a
    Fortune 500-quality HTML dashboard saved to /joao/outputs/.

    Args:
        file_path: Absolute path to the .xlsx, .xls, or .csv file on the server
    """
    return await _joao_learn_excel(file_path)


@mcp.tool()
async def joao_learn_url(url: str) -> str:
    """Fetch any URL, extract clean content, analyze with Claude, feed to JOAO's brain.

    BeautifulSoup first, trafilatura fallback. Claude maps insights to TAOP/JOAO.
    Appended to JOAO_SESSION_LOG.md.

    Args:
        url: Full URL to fetch and analyze (http or https)
    """
    return await _joao_learn_url(url)


@mcp.tool()
async def joao_learn_docx(file_path: str) -> str:
    """Extract a Word doc, generate a stunning HTML version, feed insights to JOAO's brain.

    Preserves headings and tables. Claude produces a visually rich HTML version
    saved to /joao/outputs/. Insights appended to JOAO_SESSION_LOG.md.

    Args:
        file_path: Absolute path to the .docx file on the server
    """
    return await _joao_learn_docx(file_path)


@mcp.tool()
async def joao_council_dispatch(
    agent: str,
    task: str,
    priority: str = "normal",
    context: str = "",
    project: str = "",
) -> str:
    """Dispatch a task to any Council agent.

    Sends the task to the agent's dedicated tmux session on ROG Strix (192.168.0.55).
    The agent executes autonomously. Dispatch logged to Supabase.

    Args:
        agent: BYTE | ARIA | CJ | SOFIA | DEX | GEMMA | MAX | LEX | NOVA |
               SAGE | FLUX | CORE | APEX | IRIS | VOLT
        task: Detailed task description — be specific, agents operate autonomously
        priority: normal | urgent | critical
        context: Additional context for the agent
        project: Project scope if applicable
    """
    return await _joao_council_dispatch(agent, task, priority, context, project)


@mcp.tool()
async def joao_council_status() -> str:
    """Get status of all Council agents — active/inactive in their tmux sessions on ROG Strix."""
    return await _joao_council_status()


@mcp.tool()
async def joao_agent_output(agent: str) -> str:
    """Get recent terminal output from a specific Council agent's tmux session.

    Captures the agent's terminal buffer from ROG Strix. Use to check what
    an agent is currently doing or what it produced.

    Args:
        agent: Agent name (BYTE, ARIA, CJ, SOFIA, DEX, GEMMA, MAX, LEX, NOVA,
               SAGE, FLUX, CORE, APEX, IRIS, VOLT, SCOUT)
    """
    return await _joao_agent_output(agent)


@mcp.tool()
async def joao_qa_review(
    agent: str,
    task_summary: str,
    code_diff: str,
    files_changed: str = "",
    test_results: str = "",
) -> str:
    """Trigger 3-model QA consensus review — Claude Sonnet + GPT-4o + Claude Opus.

    Runs all three reviewers in parallel. Scores correctness, security, quality,
    and completeness. Consensus verdict: deploy (all >= 8), review (2/3 >= 8),
    or reject (any < 5).

    Args:
        agent: Agent that produced the work (e.g. BYTE)
        task_summary: What the agent was asked to do
        code_diff: The code diff or output to review
        files_changed: Comma-separated list of changed files (optional)
        test_results: Test output if available (optional)
    """
    return await _joao_qa_review(agent, task_summary, code_diff, files_changed, test_results)
