"""OS Autonomy proxy router — forwards /os/* to the os-agent on the Dell."""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/os", tags=["os-autonomy"])

OS_AGENT_URL = os.getenv("OS_AGENT_URL", "http://192.168.0.55:7801")
OS_AGENT_KEY = os.getenv("OS_AGENT_KEY", "joao-os-2026")


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_os(path: str, request: Request):
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.request(
                method=request.method,
                url=f"{OS_AGENT_URL}/{path}",
                headers={"X-API-Key": OS_AGENT_KEY, "Content-Type": "application/json"},
                content=body,
            )
        return JSONResponse(content=r.json(), status_code=r.status_code)
    except httpx.ConnectError:
        logger.error("os-agent not reachable at %s", OS_AGENT_URL)
        raise HTTPException(
            status_code=503,
            detail=f"os-agent not reachable at {OS_AGENT_URL}. Is the service running on the Dell?",
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="os-agent request timed out")
