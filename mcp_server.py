"""FastMCP tools — delegates to the same services as REST endpoints."""

from __future__ import annotations

import os
import time

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from models.schemas import (
    AgentOutputRecord,
    DispatchLogRecord,
    IdeaVaultRecord,
    SessionLogRecord,
)
from services import ai_processor, dispatch, supabase_client, telegram

_RAILWAY_HOST = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")

_allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
if _RAILWAY_HOST:
    _allowed_hosts.append(_RAILWAY_HOST)

mcp = FastMCP(
    "joao-spine",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts,
    ),
)


@mcp.tool()
async def dispatch_agent(session_name: str, command: str, wait: bool = False) -> str:
    """Dispatch a shell command to a tmux session on the home server via SSH.

    Args:
        session_name: tmux session name (created if missing)
        command: Shell command to execute
        wait: If True, wait briefly and return captured output
    """
    t0 = time.time()
    result = await dispatch.dispatch_command(session_name, command, wait)
    duration_ms = int((time.time() - t0) * 1000)

    await supabase_client.insert_session_log(
        SessionLogRecord(
            endpoint="mcp/dispatch_agent",
            action="dispatch",
            input_summary=f"{session_name}: {command[:100]}",
            output_summary=(result.get("output") or "")[:200],
            status=result["status"],
            duration_ms=duration_ms,
        )
    )
    await supabase_client.insert_agent_output(
        AgentOutputRecord(
            session_name=session_name,
            command=command,
            output=result.get("output") or "",
            status=result["status"],
        )
    )

    if result["status"] == "error":
        return f"Error: {result['output']}"
    if wait and result["output"]:
        return f"[{result['status']}] Output:\n{result['output']}"
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
    try:
        result = await dispatch.dispatch_to_agent(
            agent=agent,
            task=task,
            priority=priority,
            context=context or None,
            project=project or None,
        )
    except Exception as e:
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
        pass

    return (
        f"Dispatched to {agent}:\n"
        f"Task: {task[:200]}\n"
        f"Priority: {priority}\n"
        f"Session: {result.get('session', 'unknown')}\n"
        f"Status: {result.get('status', 'unknown')}"
    )


@mcp.tool()
async def council_status() -> str:
    """Check which Council agents are active and their tmux session status."""
    try:
        result = await dispatch.get_agents()
    except Exception as e:
        return f"Failed to reach local server: {e}"

    agents = result.get("agents", {})
    lines = ["Council Agent Status:"]
    for name, info in agents.items():
        status = "ACTIVE" if info.get("active") else "INACTIVE"
        lines.append(f"  {name}: {status} (session: {info.get('session', '?')})")
    return "\n".join(lines)


@mcp.tool()
async def council_session_output(agent: str) -> str:
    """Get the recent terminal output from a Council agent's tmux session.

    Args:
        agent: Agent name (BYTE, ARIA, CJ, SOFIA, DEX, GEMMA)
    """
    try:
        result = await dispatch.get_session(agent)
    except Exception as e:
        return f"Failed to get session for {agent}: {e}"

    return (
        f"Agent: {result.get('agent', agent)}\n"
        f"Session: {result.get('session', '?')}\n"
        f"--- Output ---\n{result.get('output', 'No output')}"
    )
