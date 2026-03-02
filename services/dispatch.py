"""SSH + tmux dispatch to home server via asyncssh."""

from __future__ import annotations

import logging
import os
import stat
import time

import asyncssh

from models.schemas import SshCheck, TmuxCheck

logger = logging.getLogger(__name__)


def _resolve_ssh_key() -> list[str]:
    """Resolve SSH private key.

    Priority:
    1. COUNCIL_SSH_PRIVATE_KEY env (PEM content) -> write to temp file
    2. COUNCIL_SSH_PRIVATE_KEY_PATH / SSH_PRIVATE_KEY_PATH (file path)
    """
    pem_content = os.environ.get("COUNCIL_SSH_PRIVATE_KEY", "")
    if pem_content:
        key_path = "/tmp/council_ssh_key"
        with open(key_path, "w") as f:
            f.write(pem_content)
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
        return [key_path]
    for var in ("COUNCIL_SSH_PRIVATE_KEY_PATH", "SSH_PRIVATE_KEY_PATH"):
        path = os.environ.get(var, "")
        if path:
            return [path]
    return []


def _ssh_config() -> dict:
    host = os.environ.get("COUNCIL_SSH_HOST") or os.environ.get("SSH_HOST", "192.168.0.55")
    port = int(os.environ.get("COUNCIL_SSH_PORT") or os.environ.get("SSH_PORT", "22"))
    username = os.environ.get("COUNCIL_SSH_USER") or os.environ.get("SSH_USER", "joao")
    return {
        "host": host,
        "port": port,
        "username": username,
        "known_hosts": None,
        "client_keys": _resolve_ssh_key(),
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


async def health_check() -> tuple[SshCheck, TmuxCheck]:
    """Check SSH connectivity and list tmux sessions."""
    cfg = _ssh_config()
    target = f"{cfg['username']}@{cfg['host']}:{cfg['port']}"
    t0 = time.monotonic()
    try:
        async with asyncssh.connect(**cfg) as conn:
            ssh_latency = round((time.monotonic() - t0) * 1000, 1)
            ssh_check = SshCheck(ok=True, latency_ms=ssh_latency, target=target)

            t1 = time.monotonic()
            result = await conn.run("tmux ls 2>/dev/null || echo __NO_SESSIONS__", check=False)
            tmux_latency = round((time.monotonic() - t1) * 1000, 1)
            stdout = (result.stdout or "").strip()

            sessions: list[str] = []
            if "__NO_SESSIONS__" not in stdout and stdout:
                for line in stdout.splitlines():
                    name = line.split(":")[0].strip()
                    if name:
                        sessions.append(name)

            tmux_check = TmuxCheck(ok=True, latency_ms=tmux_latency, sessions=sessions)

    except Exception as e:
        latency = round((time.monotonic() - t0) * 1000, 1)
        ssh_check = SshCheck(ok=False, latency_ms=latency, target=target, error=str(e)[:200])
        tmux_check = TmuxCheck(ok=False, error="SSH connection failed")

    return ssh_check, tmux_check


def _shell_escape(cmd: str) -> str:
    """Escape a command for tmux send-keys."""
    return "'" + cmd.replace("'", "'\\''") + "'"
