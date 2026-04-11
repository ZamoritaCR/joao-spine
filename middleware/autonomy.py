"""
Autonomy enforcement middleware for JOAO Capability OS.

Enforces L0-L4 autonomy levels and lock requirements per capability.
Every superpowers request flows through this before execution.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException, Request

from exocortex.ledgers import check_lock, parse_control_flags

logger = logging.getLogger(__name__)

# Autonomy level ordering
_LEVEL_ORDER = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}


def parse_autonomy_from_request(
    text: str = "",
    autonomy_header: Optional[str] = None,
) -> dict:
    """Extract autonomy level, learning mode, and locks from request.

    Checks header first (x-joao-autonomy), then parses from text body.
    Returns parse_control_flags result dict.
    """
    if autonomy_header and autonomy_header in _LEVEL_ORDER:
        flags = parse_control_flags(text)
        flags["autonomy"] = autonomy_header
        return flags
    return parse_control_flags(text)


def enforce_autonomy(
    requested_level: str,
    min_autonomy: str,
    capability_name: str,
    lock_type: Optional[str] = None,
    lock_scope: Optional[str] = None,
) -> None:
    """Enforce autonomy level and lock requirements.

    Raises HTTPException(403) if:
    - requested level < min_autonomy for the capability
    - L3 operation without valid WRITE_LOCK
    - L4 operation without valid SHIP_LOCK

    Args:
        requested_level: The autonomy level from the request (e.g. "L2")
        min_autonomy: The minimum level required by the capability
        capability_name: Name for error messages
        lock_type: Required lock type ("WRITE_LOCK" or "SHIP_LOCK") if any
        lock_scope: Required lock scope (e.g. "repo:dr-data")
    """
    req_ord = _LEVEL_ORDER.get(requested_level, 1)
    min_ord = _LEVEL_ORDER.get(min_autonomy, 0)

    if req_ord < min_ord:
        logger.warning(
            "autonomy_denied: %s requires %s, got %s",
            capability_name, min_autonomy, requested_level,
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": "autonomy_insufficient",
                "capability": capability_name,
                "required": min_autonomy,
                "requested": requested_level,
                "message": f"Capability '{capability_name}' requires autonomy {min_autonomy}. "
                           f"Current level: {requested_level}. "
                           f"Set autonomy with 'L{min_ord}' in your request.",
            },
        )

    # L3 requires WRITE_LOCK
    if req_ord >= 3 and lock_type == "WRITE_LOCK":
        scope = lock_scope or f"capability:{capability_name}"
        lock = check_lock("WRITE_LOCK", scope)
        if not lock:
            logger.warning(
                "lock_denied: %s requires WRITE_LOCK scope=%s",
                capability_name, scope,
            )
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "lock_required",
                    "lock_type": "WRITE_LOCK",
                    "scope": scope,
                    "message": f"WRITE_LOCK required for '{capability_name}'. "
                               f"Grant with: WRITE_LOCK=30m scope={scope}",
                },
            )

    # L4 requires SHIP_LOCK
    if req_ord >= 4 and lock_type == "SHIP_LOCK":
        scope = lock_scope or f"capability:{capability_name}"
        lock = check_lock("SHIP_LOCK", scope)
        if not lock:
            logger.warning(
                "lock_denied: %s requires SHIP_LOCK scope=%s",
                capability_name, scope,
            )
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "lock_required",
                    "lock_type": "SHIP_LOCK",
                    "scope": scope,
                    "message": f"SHIP_LOCK required for '{capability_name}'. "
                               f"Grant with: SHIP_LOCK=10m scope={scope}",
                },
            )

    logger.info(
        "autonomy_granted: %s level=%s (min=%s)",
        capability_name, requested_level, min_autonomy,
    )
