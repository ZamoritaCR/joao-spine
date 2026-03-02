"""FastMCP tools — delegates to the same services as REST endpoints."""

from __future__ import annotations

import os
import time

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from models.schemas import (
    AgentOutputRecord,
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
