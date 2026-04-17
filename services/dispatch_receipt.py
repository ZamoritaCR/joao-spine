"""
JOÃO dispatch-with-receipt.

Every dispatch going through dispatch_with_receipt():
  1. POSTs to the local dispatch service
  2. Polls the tmux buffer for echo / verify_token
  3. Detects Claude Code /login-locked sessions (loud fail)
  4. Persists a typed receipt to Supabase agent_outputs
  5. Returns a Receipt object with verified: bool

Replaces the fire-and-forget pattern where dispatch_command() claimed
success on HTTP 200 without verifying the agent actually ran anything.

Usage:
    from services.dispatch_receipt import dispatch_with_receipt

    verify_token = f"RECEIPT_{uuid.uuid4().hex[:8]}"
    wrapped = f"echo {verify_token} && {actual_command}"
    receipt = await dispatch_with_receipt(
        agent="BYTE",
        command=wrapped,
        verify_token=verify_token,
        timeout_s=30,
    )
    if receipt.verified:
        # use receipt.output
        ...
    else:
        # receipt.failure_reason tells you which gate failed
        ...
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, asdict, field
from typing import Any

import httpx

from services import supabase_client
from models.schemas import AgentOutputRecord

logger = logging.getLogger(__name__)

DISPATCH_URL = os.environ.get(
    "JOAO_LOCAL_DISPATCH_URL",
    "https://dispatch.theartofthepossible.io",
)
DISPATCH_SECRET = os.environ.get("JOAO_DISPATCH_SECRET", "")

POLL_INTERVAL_S = 1.0
DEFAULT_TIMEOUT_S = 30

LOGIN_REQUIRED_MARKERS = (
    "Please run /login",
    "Not logged in",
    "Run /login",
)


@dataclass
class Receipt:
    """Typed proof object. Serializes to JSON cleanly for Supabase / UI."""
    request_id: str
    agent: str
    command: str
    output: str
    verified: bool
    failure_reason: str | None
    http_status: int
    attempt_count: int
    duration_ms: int
    dispatched_at: float
    completed_at: float | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def _post_dispatch(agent: str, command: str) -> tuple[int, str]:
    url = f"{DISPATCH_URL.rstrip('/')}/dispatch/raw"
    payload = {"session_name": agent, "command": command, "wait": False}
    headers = {}
    if DISPATCH_SECRET:
        headers["Authorization"] = f"Bearer {DISPATCH_SECRET}"
    async with httpx.AsyncClient(timeout=10) as c:
        resp = await c.post(url, json=payload, headers=headers)
    return resp.status_code, resp.text


async def _fetch_session_output(agent: str) -> str:
    url = f"{DISPATCH_URL.rstrip('/')}/session/{agent}"
    headers = {}
    if DISPATCH_SECRET:
        headers["Authorization"] = f"Bearer {DISPATCH_SECRET}"
    async with httpx.AsyncClient(timeout=10) as c:
        resp = await c.get(url, headers=headers)
    if resp.status_code != 200:
        return ""
    try:
        data = resp.json()
        return data.get("output", "") or data.get("buffer", "") or resp.text
    except Exception:
        return resp.text


def _detect_login_required(buffer: str) -> bool:
    return any(marker in buffer for marker in LOGIN_REQUIRED_MARKERS)


def _echo_verified(buffer: str, verify_token: str | None, command: str) -> bool:
    if verify_token:
        return verify_token in buffer
    # Weaker fallback: look for the command itself
    simple = re.sub(r"[&|><$()\"'`\\]", "", command).strip()[:40]
    return bool(simple) and simple in buffer


async def dispatch_with_receipt(
    agent: str,
    command: str,
    *,
    verify_token: str | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    metadata: dict[str, Any] | None = None,
    persist: bool = True,
) -> Receipt:
    """
    Dispatch `command` to `agent`, poll for echo, write receipt to Supabase.

    Returns a Receipt (never raises on dispatch failure — check receipt.verified).
    """
    request_id = str(uuid.uuid4())
    metadata = {"request_id": request_id, **(metadata or {})}
    t0 = time.time()
    attempt_count = 0

    # Step 1: POST the dispatch
    try:
        status_code, body = await _post_dispatch(agent, command)
    except Exception as e:
        return Receipt(
            request_id=request_id, agent=agent, command=command, output="",
            verified=False, failure_reason=f"dispatch_post_exception: {e!s}"[:200],
            http_status=0, attempt_count=0,
            duration_ms=int((time.time() - t0) * 1000),
            dispatched_at=t0, completed_at=time.time(), metadata=metadata,
        )

    if status_code >= 400:
        return Receipt(
            request_id=request_id, agent=agent, command=command, output=body[:2000],
            verified=False, failure_reason=f"dispatch_post_http_{status_code}",
            http_status=status_code, attempt_count=0,
            duration_ms=int((time.time() - t0) * 1000),
            dispatched_at=t0, completed_at=time.time(), metadata=metadata,
        )

    # Step 2: poll the tmux buffer for echo/verify
    deadline = t0 + timeout_s
    last_buffer = ""
    verified = False
    login_locked = False

    while time.time() < deadline:
        attempt_count += 1
        await asyncio.sleep(POLL_INTERVAL_S)
        last_buffer = await _fetch_session_output(agent)
        if _detect_login_required(last_buffer):
            login_locked = True
            break
        if _echo_verified(last_buffer, verify_token, command):
            verified = True
            break

    completed_at = time.time()
    duration_ms = int((completed_at - t0) * 1000)

    if login_locked:
        failure_reason = "agent_claude_code_login_required"
    elif not verified:
        failure_reason = f"echo_not_seen_within_{timeout_s}s"
    else:
        failure_reason = None

    receipt = Receipt(
        request_id=request_id, agent=agent, command=command,
        output=last_buffer[-4000:] if last_buffer else "",
        verified=verified, failure_reason=failure_reason,
        http_status=status_code, attempt_count=attempt_count,
        duration_ms=duration_ms, dispatched_at=t0, completed_at=completed_at,
        metadata=metadata,
    )

    # Step 3: persist receipt
    if persist:
        try:
            await supabase_client.insert_agent_output(
                AgentOutputRecord(
                    session_name=agent, command=command,
                    output=receipt.output,
                    status=("completed" if verified else "failed"),
                    metadata={
                        "request_id": request_id,
                        "verified": verified,
                        "failure_reason": failure_reason,
                        "attempt_count": attempt_count,
                        "duration_ms": duration_ms,
                        "http_status": status_code,
                        **metadata,
                    },
                )
            )
        except Exception:
            logger.warning("receipt persist failed", exc_info=True)

    if not verified:
        logger.warning(
            "DISPATCH UNVERIFIED agent=%s reason=%s attempts=%d duration_ms=%d",
            agent, failure_reason, attempt_count, duration_ms,
        )

    return receipt
