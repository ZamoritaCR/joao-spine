"""joao_memory_read, joao_memory_write — JOAO brain file access."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

_MASTER_CONTEXT = Path("/home/zamoritacr/joao-spine/JOAO_MASTER_CONTEXT.md")
_SESSION_LOG = Path("/home/zamoritacr/joao-spine/JOAO_SESSION_LOG.md")

_FILE_MAP = {
    "master": _MASTER_CONTEXT,
    "session": _SESSION_LOG,
}


async def joao_memory_read(
    file: Literal["master", "session"] = "master",
    tail_lines: int = 0,
) -> str:
    """Read JOAO memory files — master context or session log.

    Args:
        file: Which file to read — 'master' (JOAO_MASTER_CONTEXT.md) or
              'session' (JOAO_SESSION_LOG.md)
        tail_lines: If > 0, return only the last N lines. 0 returns full file.
    """
    path = _FILE_MAP.get(file)
    if not path:
        return f"Error: unknown file '{file}'. Use 'master' or 'session'."

    if not path.exists():
        return f"File not found: {path}"

    content = path.read_text(encoding="utf-8")

    if tail_lines > 0:
        lines = content.splitlines()
        content = "\n".join(lines[-tail_lines:])

    return content


async def joao_memory_write(
    content: str,
    file: Literal["master", "session"] = "session",
    header: str = "",
) -> str:
    """Append content to JOAO memory files.

    Writes are append-only. Never overwrites. Session log gets a timestamped
    header automatically. Master context appends at the end with a separator.

    Args:
        content: Text to append
        file: Target file — 'session' (default) or 'master'
        header: Optional section header label (auto-timestamped)
    """
    path = _FILE_MAP.get(file)
    if not path:
        return f"Error: unknown file '{file}'. Use 'master' or 'session'."

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = header if header else "MCP WRITE"

    if file == "session":
        entry = f"\n## {label} — {ts}\n\n{content}\n"
    else:
        entry = f"\n\n---\n\n## {label} — {ts}\n\n{content}\n"

    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(entry)
        return f"Appended to {path.name} at {ts}"
    except Exception as e:
        return f"Error writing to {path.name}: {e}"
