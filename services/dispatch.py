"""SSH + tmux dispatch to home server via asyncssh, with HTTP tunnel fallback."""

from __future__ import annotations

import logging
import os
import stat
import time

import asyncssh
import httpx

from models.schemas import SshCheck, TmuxCheck

logger = logging.getLogger(__name__)

def _tunnel_config() -> tuple[str, str]:
    """Read tunnel config fresh from env vars (not cached at import time)."""
    url = os.environ.get("JOAO_LOCAL_DISPATCH_URL", "")
    secret = os.environ.get("JOAO_DISPATCH_SECRET", "")
    return url, secret


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

            # Send the command text, then Enter as a separate call
            await conn.run(
                f"tmux send-keys -t {session_name} {_shell_escape(command)}",
                check=True,
            )
            await conn.run(
                f"tmux send-keys -t {session_name} Enter",
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


async def capture_pane(session_name: str) -> dict[str, str]:
    """Capture the current tmux pane output for a session via SSH."""
    cfg = _ssh_config()
    try:
        async with asyncssh.connect(**cfg) as conn:
            result = await conn.run(
                f"tmux capture-pane -t {session_name} -p 2>/dev/null",
                check=False,
            )
            output = (result.stdout or "").strip()
            return {"status": "ok", "output": output}
    except Exception as e:
        logger.exception("SSH capture-pane failed for %s", session_name)
        return {"status": "error", "output": str(e)}


async def health_check() -> tuple[SshCheck, TmuxCheck]:
    """Check connectivity to the home server and list tmux sessions.

    Uses HTTP tunnel (works from Railway) with SSH as local fallback.
    """
    url, _secret = _tunnel_config()

    # Try HTTP tunnel first (works from both Railway and local)
    if url:
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                health_resp = await client.get(f"{url}/health")
                ssh_latency = round((time.monotonic() - t0) * 1000, 1)

                if health_resp.status_code == 200:
                    data = health_resp.json()
                    target = f"tunnel:{url}"
                    ssh_check = SshCheck(ok=True, latency_ms=ssh_latency, target=target)

                    t1 = time.monotonic()
                    agents_resp = await client.get(f"{url}/agents")
                    tmux_latency = round((time.monotonic() - t1) * 1000, 1)

                    sessions: list[str] = []
                    if agents_resp.status_code == 200:
                        agents_data = agents_resp.json()
                        for name, info in agents_data.get("agents", {}).items():
                            if info.get("active"):
                                sessions.append(info.get("session", name.lower()))

                    tmux_check = TmuxCheck(ok=True, latency_ms=tmux_latency, sessions=sessions)
                    return ssh_check, tmux_check
        except Exception as e:
            logger.warning("HTTP tunnel health check failed, trying SSH: %s", e)

    # Fallback to SSH (works when running locally on same network)
    cfg = _ssh_config()
    target = f"{cfg['username']}@{cfg['host']}:{cfg['port']}"
    t0 = time.monotonic()
    try:
        async with asyncssh.connect(**cfg, login_timeout=3, connect_timeout=3) as conn:
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


# ── HTTP Tunnel Dispatch (Cloudflare tunnel → local listener) ──────────────


def _require_tunnel() -> tuple[str, str]:
    """Get tunnel URL and secret, raising RuntimeError if not configured."""
    url, secret = _tunnel_config()
    if not url:
        raise RuntimeError("JOAO_LOCAL_DISPATCH_URL not configured — set it in Railway env vars")
    if not secret:
        raise RuntimeError("JOAO_DISPATCH_SECRET not configured — set it in Railway env vars")
    return url, secret


async def dispatch_to_agent(
    agent: str,
    task: str,
    priority: str = "normal",
    context: str | None = None,
    project: str | None = None,
) -> dict:
    """Dispatch a task to a Council agent via the local HTTP listener."""
    url, secret = _require_tunnel()

    payload = {
        "agent": agent,
        "task": task,
        "priority": priority,
        "context": context,
        "project": project,
        "lane": "interactive",
    }

    last_error = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    f"{url}/dispatch",
                    json=payload,
                    headers={"Authorization": f"Bearer {secret}"},
                )
                if response.status_code == 401:
                    raise RuntimeError(
                        "Local dispatch rejected auth — JOAO_DISPATCH_SECRET mismatch "
                        "between Railway and Ubuntu server"
                    )
                if response.status_code == 422:
                    detail = response.json().get("detail", response.text)
                    raise RuntimeError(f"Local dispatch schema error: {detail}")
                response.raise_for_status()
                return response.json()
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
            last_error = e
            if attempt < 2:
                import asyncio
                logger.warning("Dispatch attempt %d failed (%s), retrying...", attempt + 1, e)
                await asyncio.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Dispatch failed after 3 attempts: {last_error}")


async def dispatch_raw_to_agent(agent: str, command: str) -> dict:
    """Send a raw command to an agent's tmux session via the local listener."""
    url, secret = _require_tunnel()

    payload = {"agent": agent, "task": command}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{url}/dispatch/raw",
            json=payload,
            headers={"Authorization": f"Bearer {secret}"},
        )
        if response.status_code in (401, 422):
            raise RuntimeError(f"Local dispatch error {response.status_code}: {response.text}")
        response.raise_for_status()
        return response.json()


async def _get_with_retry(path: str, timeout: float = 15.0) -> dict:
    """GET request to local dispatch with retry logic."""
    url, _secret = _require_tunnel()
    last_error = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(f"{url}{path}")
                response.raise_for_status()
                return response.json()
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
            last_error = e
            if attempt < 2:
                import asyncio
                logger.warning("GET %s attempt %d failed (%s), retrying...", path, attempt + 1, e)
                await asyncio.sleep(1 * (attempt + 1))
    raise RuntimeError(f"GET {path} failed after 3 attempts: {last_error}")


async def get_agents() -> dict:
    """Get agent status from local server via tunnel."""
    return await _get_with_retry("/agents")


async def get_sessions() -> dict:
    """Get all tmux session outputs from local server."""
    return await _get_with_retry("/sessions", timeout=20.0)


async def get_session(agent: str) -> dict:
    """Get a specific agent's tmux session output."""
    return await _get_with_retry(f"/session/{agent}")


async def tunnel_health_check() -> dict:
    """Check if the local dispatch listener is reachable via tunnel."""
    url, secret = _tunnel_config()
    if not url:
        return {"ok": False, "error": "JOAO_LOCAL_DISPATCH_URL not configured"}

    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{url}/health")
            latency = round((time.monotonic() - t0) * 1000, 1)
            if response.status_code == 200:
                return {"ok": True, "latency_ms": latency, "data": response.json()}
            return {"ok": False, "latency_ms": latency, "error": f"HTTP {response.status_code}"}
    except Exception as e:
        latency = round((time.monotonic() - t0) * 1000, 1)
        return {"ok": False, "latency_ms": latency, "error": str(e)[:200]}
