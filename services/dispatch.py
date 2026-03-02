"""SSH + tmux dispatch to home server via asyncssh."""

from __future__ import annotations

import logging
import os

import asyncssh

logger = logging.getLogger(__name__)


def _ssh_config() -> dict:
    return {
        "host": os.environ.get("SSH_HOST", "192.168.0.55"),
        "port": int(os.environ.get("SSH_PORT", "22")),
        "username": os.environ.get("SSH_USER", "joao"),
        "known_hosts": None,  # Railway won't have known_hosts
        "client_keys": [os.environ["SSH_PRIVATE_KEY_PATH"]]
        if os.environ.get("SSH_PRIVATE_KEY_PATH")
        else [],
    }


async def dispatch_command(
    session_name: str, command: str, wait: bool = False
) -> dict[str, str]:
    """Send a command to a tmux session on the home server.

    Creates the session if it doesn't exist, then sends the command via send-keys.
    """
    cfg = _ssh_config()
    output = ""
    status = "sent"

    try:
        async with asyncssh.connect(**cfg) as conn:
            # Ensure tmux session exists
            check = await conn.run(
                f"tmux has-session -t {session_name} 2>/dev/null && echo EXISTS || echo MISSING",
                check=False,
            )
            if "MISSING" in (check.stdout or ""):
                await conn.run(
                    f"tmux new-session -d -s {session_name}", check=True
                )
                logger.info("Created tmux session: %s", session_name)

            # Send the command
            await conn.run(
                f"tmux send-keys -t {session_name} {_shell_escape(command)} Enter",
                check=True,
            )

            if wait:
                # Brief pause then capture pane
                import asyncio
                await asyncio.sleep(2)
                result = await conn.run(
                    f"tmux capture-pane -t {session_name} -p", check=False
                )
                output = (result.stdout or "").strip()
                status = "completed"

    except Exception as e:
        logger.exception("SSH dispatch failed")
        status = "error"
        output = str(e)

    return {"session_name": session_name, "command": command, "status": status, "output": output}


def _shell_escape(cmd: str) -> str:
    """Escape a command for tmux send-keys."""
    return "'" + cmd.replace("'", "'\\''") + "'"
