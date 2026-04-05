"""Neurodivergent Cockpit -- ADHD-optimized smart home control.

Routes for Home Assistant scene control, device management,
and energy state logging. All calls handle Pi-offline gracefully.
"""

from __future__ import annotations

import hmac
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from services.home_assistant import cockpit, SCENES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cockpit", tags=["cockpit"])


def _check_auth(request: Request, token: str = "") -> None:
    """Reuse hub auth pattern."""
    secret = os.environ.get("HUB_SECRET", "") or os.environ.get("JOAO_DISPATCH_HMAC_SECRET", "")
    if not secret:
        return
    if token and hmac.compare_digest(secret, token):
        return
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        if hmac.compare_digest(secret, auth_header[7:]):
            return
    raise HTTPException(status_code=401, detail="Unauthorized")


def _sb_insert(table: str, row: dict) -> None:
    """Non-blocking Supabase insert."""
    try:
        from services.supabase_client import get_client
        sb = get_client()
        sb.table(table).insert(row).execute()
    except Exception as e:
        logger.warning("Supabase insert to %s failed: %s", table, str(e)[:200])


# -- GET /cockpit/status --

@router.get("/status")
async def cockpit_status(request: Request, token: str = Query(default="")):
    _check_auth(request, token)

    ping = await cockpit.ping()
    online = ping.get("status") == "online"

    device_count = 0
    if online:
        states = await cockpit.get_states()
        if isinstance(states, list):
            device_count = len(states)

    return {
        "pi": ping.get("status", "offline"),
        "ha_version": ping.get("version", "--"),
        "devices": device_count,
        "last_scene": cockpit.last_scene,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# -- GET /cockpit/devices --

@router.get("/devices")
async def list_devices(request: Request, token: str = Query(default="")):
    _check_auth(request, token)

    result = await cockpit.get_states()
    if isinstance(result, dict) and result.get("status") == "offline":
        raise HTTPException(status_code=503, detail=result.get("error", "Pi offline"))
    return {"devices": result}


# -- GET /cockpit/state/{entity_id} --

@router.get("/state/{entity_id:path}")
async def device_state(entity_id: str, request: Request, token: str = Query(default="")):
    _check_auth(request, token)

    result = await cockpit.get_state(entity_id)
    if isinstance(result, dict) and result.get("status") == "offline":
        raise HTTPException(status_code=503, detail=result.get("error", "Pi offline"))
    return result


# -- POST /cockpit/scene/{scene_name} --

@router.post("/scene/{scene_name}")
async def activate_scene(scene_name: str, request: Request, token: str = Query(default="")):
    _check_auth(request, token)

    if scene_name not in SCENES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scene: {scene_name}. Valid: {sorted(SCENES.keys())}",
        )

    method = getattr(cockpit, SCENES[scene_name])
    result = await method()

    # Log to energy log
    _sb_insert("cockpit_energy_log", {
        "energy_level": scene_name,
        "scene_activated": scene_name,
        "source": "api",
    })

    if result.get("status") == "offline":
        raise HTTPException(status_code=503, detail=result.get("error", "Pi offline"))

    return {"scene": scene_name, "status": "activated", "result": result}


# -- POST /cockpit/command --

class CommandBody(BaseModel):
    entity_id: str
    domain: str
    service: str
    data: dict[str, Any] = {}


@router.post("/command")
async def raw_command(body: CommandBody, request: Request, token: str = Query(default="")):
    _check_auth(request, token)

    result = await cockpit.call_service(body.domain, body.service, body.entity_id, **body.data)
    if result.get("status") == "offline":
        raise HTTPException(status_code=503, detail=result.get("error", "Pi offline"))
    return result


# -- POST /cockpit/energy --

class EnergyBody(BaseModel):
    level: str  # high, medium, low, crashed


@router.post("/energy")
async def log_energy(body: EnergyBody, request: Request, token: str = Query(default="")):
    _check_auth(request, token)

    valid = {"high", "medium", "low", "crashed"}
    if body.level not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid level. Valid: {sorted(valid)}")

    _sb_insert("cockpit_energy_log", {
        "energy_level": body.level,
        "scene_activated": cockpit.last_scene,
        "source": "api",
    })

    return {"logged": body.level, "timestamp": datetime.now(timezone.utc).isoformat()}
