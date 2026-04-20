"""JOAO web browsing endpoints — read, screenshot, navigate."""

from __future__ import annotations

import hmac
import logging
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, HttpUrl

from services import web_browser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/joao/browse", tags=["browse"])


# -- Auth (Bearer token against JOAO_DISPATCH_SECRET) -------------------------

def _require_bearer(request: Request) -> None:
    secret = os.environ.get("JOAO_DISPATCH_SECRET", "") or os.environ.get("JOAO_DISPATCH_HMAC_SECRET", "")
    if not secret:
        return  # auth disabled if no secret configured
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ") and hmac.compare_digest(secret, auth[7:]):
        return
    raise HTTPException(status_code=401, detail="Unauthorized")


# -- Schemas -------------------------------------------------------------------

class ReadRequest(BaseModel):
    url: str

class ScreenshotRequest(BaseModel):
    url: str
    full_page: bool = True

class NavigateRequest(BaseModel):
    url: str
    actions: list[dict] = []


# -- Endpoints -----------------------------------------------------------------

@router.post("/read")
async def browse_read(req: ReadRequest, request: Request):
    _require_bearer(request)
    try:
        return await web_browser.fetch_and_read(req.url)
    except Exception as e:
        logger.exception("browse/read failed for %s", req.url)
        raise HTTPException(status_code=502, detail=f"Fetch failed: {e}")


@router.post("/screenshot")
async def browse_screenshot(req: ScreenshotRequest, request: Request):
    _require_bearer(request)
    try:
        png = await web_browser.screenshot(req.url, full_page=req.full_page)
        return Response(content=png, media_type="image/png")
    except Exception as e:
        logger.exception("browse/screenshot failed for %s", req.url)
        raise HTTPException(status_code=502, detail=f"Screenshot failed: {e}")


@router.post("/navigate")
async def browse_navigate(req: NavigateRequest, request: Request):
    _require_bearer(request)
    try:
        return await web_browser.navigate_and_extract(req.url, req.actions)
    except Exception as e:
        logger.exception("browse/navigate failed for %s", req.url)
        raise HTTPException(status_code=502, detail=f"Navigate failed: {e}")
