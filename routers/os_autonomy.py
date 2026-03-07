"""OS Autonomy proxy router — forwards /os/* to the os-agent via dispatch tunnel."""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/os", tags=["os-autonomy"])

# Route through the Cloudflare tunnel to the local dispatch's /os-proxy endpoint.
# dispatch.theartofthepossible.io/os-proxy/* -> localhost:7801/* on the ROG.
_DISPATCH_TUNNEL = "https://dispatch.theartofthepossible.io"
_DISPATCH_URL = os.getenv("JOAO_LOCAL_DISPATCH_URL", "").rstrip("/")
if not _DISPATCH_URL or "localhost" in _DISPATCH_URL or "127.0.0.1" in _DISPATCH_URL:
    _DISPATCH_URL = _DISPATCH_TUNNEL
OS_AGENT_URL = os.getenv(
    "OS_AGENT_URL",
    f"{_DISPATCH_URL}/os-proxy" if _DISPATCH_URL else "http://192.168.0.55:7801",
).rstrip("/")
OS_AGENT_KEY = os.getenv("OS_AGENT_KEY", "joao-os-2026")


_DISPATCH_SECRET = os.getenv("JOAO_DISPATCH_SECRET", "")


async def _proxy(path: str, request: Request) -> JSONResponse:
    body = await request.body()
    headers = {"Content-Type": "application/json"}
    if "os-proxy" in OS_AGENT_URL:
        headers["Authorization"] = f"Bearer {_DISPATCH_SECRET}"
    else:
        headers["X-API-Key"] = OS_AGENT_KEY
    target = f"{OS_AGENT_URL}/{path}" if path else OS_AGENT_URL
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.request(
                method=request.method,
                url=target,
                headers=headers,
                content=body,
            )
        return JSONResponse(content=r.json(), status_code=r.status_code)
    except httpx.ConnectError:
        logger.error("os-agent not reachable at %s", OS_AGENT_URL)
        raise HTTPException(
            status_code=503,
            detail=f"os-agent not reachable at {OS_AGENT_URL}",
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="os-agent request timed out")


# Explicit route registrations — api_route with {path:path} breaks on some
# Starlette versions when used with APIRouter prefixes.
@router.get("/{path:path}")
async def proxy_os_get(path: str, request: Request):
    return await _proxy(path, request)


@router.post("/{path:path}")
async def proxy_os_post(path: str, request: Request):
    return await _proxy(path, request)


@router.put("/{path:path}")
async def proxy_os_put(path: str, request: Request):
    return await _proxy(path, request)


@router.delete("/{path:path}")
async def proxy_os_delete(path: str, request: Request):
    return await _proxy(path, request)
