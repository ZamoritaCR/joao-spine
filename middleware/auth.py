"""HMAC-SHA256 authentication dependency for POST /joao/dispatch."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import time
import uuid
from typing import Annotated

from fastapi import Header, HTTPException, Request

logger = logging.getLogger(__name__)

ALLOWED_AGENTS = frozenset({"BYTE", "ARIA", "CJ", "SOFIA", "DEX", "GEMMA", "MAX"})

_SHELL_DANGEROUS = re.compile(r"[;&|`$<>]")


async def require_dispatch_auth(
    request: Request,
    x_joao_signature: Annotated[str | None, Header()] = None,
    x_joao_timestamp: Annotated[str | None, Header()] = None,
) -> str:
    """FastAPI dependency. Validates HMAC signature, returns request_id."""
    request_id = str(uuid.uuid4())
    secret_str = os.environ.get("JOAO_DISPATCH_HMAC_SECRET", "")

    if not secret_str:
        logger.warning(
            "JOAO_DISPATCH_HMAC_SECRET not set — dispatch auth disabled",
            extra={"request_id": request_id},
        )
        return request_id

    if not x_joao_signature or not x_joao_timestamp:
        logger.warning(
            "dispatch_auth_failed: missing headers",
            extra={"request_id": request_id, "client": _client_ip(request)},
        )
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        ts = int(x_joao_timestamp)
    except ValueError:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if abs(int(time.time()) - ts) > 300:
        logger.warning(
            "dispatch_auth_failed: timestamp skew",
            extra={"request_id": request_id, "client": _client_ip(request)},
        )
        raise HTTPException(status_code=401, detail="Unauthorized")

    raw_body: bytes = await request.body()
    message = f"{x_joao_timestamp}.".encode() + raw_body
    expected = "sha256=" + hmac.new(
        secret_str.encode(), message, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, x_joao_signature):
        logger.warning(
            "dispatch_auth_failed: invalid signature",
            extra={"request_id": request_id, "client": _client_ip(request)},
        )
        raise HTTPException(status_code=401, detail="Unauthorized")

    return request_id


async def require_api_key(
    request: Request,
    x_joao_api_key: Annotated[str | None, Header()] = None,
) -> None:
    """Simple API key auth for voice endpoints."""
    secret = os.environ.get("JOAO_API_KEY") or os.environ.get("JOAO_DISPATCH_HMAC_SECRET", "")
    if not secret:
        logger.warning("JOAO_API_KEY not set — voice auth disabled")
        return
    if not x_joao_api_key or not hmac.compare_digest(secret, x_joao_api_key):
        raise HTTPException(status_code=401, detail="Unauthorized")


def validate_agent_name(session_name: str) -> None:
    """Raise ValueError if session_name is not in the agent allowlist."""
    if session_name.upper() not in ALLOWED_AGENTS:
        raise ValueError(f"Agent '{session_name}' not in allowlist")


def validate_command_safety(command: str) -> None:
    """Raise ValueError if command contains shell injection characters."""
    if _SHELL_DANGEROUS.search(command):
        raise ValueError("Command contains disallowed shell characters")


def _client_ip(request: Request) -> str:
    """Best-effort client IP from headers or connection."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"
