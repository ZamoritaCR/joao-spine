"""TAOP MCP router — Council dispatch, status, memory, and SCOUT intel tools.

Mounted at /taop/mcp (SSE transport). Gives Claude Desktop direct access to the
full Council: dispatch tasks, check agent status, read/write JOAO memory, and
pull SCOUT intelligence.
"""

from __future__ import annotations

import logging
import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from services import dispatch, scout as scout_service
from tools.memory import joao_memory_read, joao_memory_write

logger = logging.getLogger(__name__)

_RAILWAY_HOST = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
_allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
if _RAILWAY_HOST:
    _allowed_hosts.append(_RAILWAY_HOST)

taop_mcp = FastMCP(
    "taop-council",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts,
    ),
)


@taop_mcp.tool()
async def dispatch_agent(
    agent: str,
    task: str,
    priority: str = "normal",
    context: str = "",
    project: str = "",
) -> str:
    """Send a task to any Council agent (BYTE, ARIA, CJ, SOFIA, DEX, GEMMA, MAX, etc.).

    The agent receives the task in their tmux session and executes autonomously.

    Args:
        agent: Agent name — BYTE, ARIA, CJ, SOFIA, DEX, GEMMA, MAX, LEX,
               NOVA, SAGE, FLUX, CORE, APEX, IRIS, VOLT
        task: Detailed task description
        priority: normal | urgent | critical
        context: Additional context for the agent
        project: Optional project scope
    """
    try:
        result = await dispatch.dispatch_to_agent(
            agent=agent,
            task=task,
            priority=priority,
            context=context or None,
            project=project or None,
        )
        return (
            f"Dispatched to {agent}:\n"
            f"Task: {task[:200]}\n"
            f"Priority: {priority}\n"
            f"Session: {result.get('session', 'unknown')}\n"
            f"Status: {result.get('status', 'unknown')}"
        )
    except Exception as e:
        logger.exception("taop dispatch_agent failed")
        return f"Dispatch failed: {e}"


@taop_mcp.tool()
async def agent_status() -> str:
    """Get status of all Council agents — active/inactive in their tmux sessions."""
    try:
        result = await dispatch.get_agents()
        agents = result.get("agents", {})
        lines = ["Council Agent Status:"]
        for name, info in agents.items():
            status = "ACTIVE" if info.get("active") else "INACTIVE"
            lines.append(f"  {name}: {status} (session: {info.get('session', '?')})")
        return "\n".join(lines)
    except Exception as e:
        logger.exception("taop agent_status failed")
        return f"Failed to get agent status: {e}"


@taop_mcp.tool()
async def agent_output(agent: str) -> str:
    """Get recent terminal output from a specific Council agent's tmux session.

    Args:
        agent: Agent name (BYTE, ARIA, CJ, SOFIA, DEX, GEMMA, MAX, LEX, NOVA,
               SAGE, FLUX, CORE, APEX, IRIS, VOLT, SCOUT)
    """
    try:
        result = await dispatch.get_session(agent)
        return (
            f"Agent: {result.get('agent', agent)}\n"
            f"Session: {result.get('session', '?')}\n"
            f"--- Output ---\n{result.get('output', 'No output')}"
        )
    except Exception as e:
        logger.exception("taop agent_output failed: agent=%s", agent)
        return f"Failed to get output for {agent}: {e}"


@taop_mcp.tool()
async def read_memory(
    file: str = "session",
    tail_lines: int = 50,
) -> str:
    """Read JOAO memory files — session log or master context.

    Args:
        file: 'session' (JOAO_SESSION_LOG.md) or 'master' (JOAO_MASTER_CONTEXT.md)
        tail_lines: If > 0, return only the last N lines. 0 returns full file.
    """
    return await joao_memory_read(file=file, tail_lines=tail_lines)  # type: ignore[arg-type]


@taop_mcp.tool()
async def write_memory(
    content: str,
    file: str = "session",
    header: str = "",
) -> str:
    """Append content to JOAO memory files. Append-only — never overwrites.

    Args:
        content: Text to append
        file: 'session' (default) or 'master'
        header: Optional section label (auto-timestamped)
    """
    return await joao_memory_write(content=content, file=file, header=header)  # type: ignore[arg-type]


@taop_mcp.tool()
async def scout_intel(limit: int = 20, min_score: int = 7) -> str:
    """Get the latest SCOUT intelligence items from local SQLite.

    SCOUT monitors RSS feeds, news, and signals relevant to TAOP and JOAO.

    Args:
        limit: Max number of items to return (default: 20)
        min_score: Minimum relevance score 1-10 (default: 7)
    """
    try:
        items = scout_service.get_recent_intel(limit=limit, min_score=min_score)
        if not items:
            return f"No SCOUT intel found (min_score={min_score})."

        lines = [f"SCOUT Intel ({len(items)} items, score >= {min_score}):"]
        for item in items:
            lines.append(
                f"\n[{item.get('score', '?')}/10] {item.get('title', 'Untitled')}\n"
                f"  Source: {item.get('source', '?')} | {item.get('created_at', '?')}\n"
                f"  {item.get('summary', 'No summary')}"
            )
        return "\n".join(lines)
    except Exception as e:
        logger.exception("taop scout_intel failed")
        return f"Failed to fetch SCOUT intel: {e}"
