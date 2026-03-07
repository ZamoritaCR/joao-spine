"""FastAPI app, lifespan, MCP mounts for joao-spine."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Configure JSON logging before importing modules that log at import time
from middleware.logging_config import configure_json_logging, RequestLoggingMiddleware

configure_json_logging()

import logging

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# Fix Railway 30s idle timeout + zero-downtime redeploys
import anyio
from sse_starlette import EventSourceResponse as _OriginalEventSourceResponse

_OriginalEventSourceResponse.DEFAULT_PING_INTERVAL = 15

# Global shutdown event — fired during lifespan shutdown so SSE connections
# get a grace period to close cleanly instead of being killed on redeploy.
_shutdown_event = anyio.Event()


class _PatchedEventSourceResponse(_OriginalEventSourceResponse):
    """Injects shutdown grace period into every SSE response.

    On redeploy, uvicorn sends SIGTERM. The lifespan sets _shutdown_event,
    which gives SSE streams 25s to send a final message and close cleanly
    instead of being killed mid-stream.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("shutdown_event", _shutdown_event)
        kwargs.setdefault("shutdown_grace_period", 25)
        super().__init__(*args, **kwargs)


# Patch mcp.server.sse so the MCP transport uses our graceful version
import mcp.server.sse
mcp.server.sse.EventSourceResponse = _PatchedEventSourceResponse

from mcp_server import mcp
from routers.taop_mcp import taop_mcp
from routers.joao import router as joao_router
from routers.qa import router as qa_router
from routers.scout import router as scout_router
from routers.voice import router as voice_router
from routers.ftp import router as ftp_router
from routers.greengeeks import router as greengeeks_router
from routers.telegram_webhook import router as telegram_webhook_router
from routers.os_autonomy import os_app as os_autonomy_app

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("joao-spine starting up")
    from services.supabase_migrate import run_startup_migrations
    run_startup_migrations()
    from services import scout as scout_service
    scout_service.start_scheduler()
    logger.info("SCOUT scheduler started in lifespan")
    yield
    scout_service.stop_scheduler()
    logger.info("joao-spine shutting down — signaling SSE connections to close")
    _shutdown_event.set()


app = FastAPI(
    title="joao-spine",
    description="Personal automation server — SSH dispatch, AI processing, idea vault",
    version="1.0.0",
    lifespan=lifespan,
)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "joao-spine", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse(url="/joao/app")


@app.get("/joao", include_in_schema=False)
async def joao_redirect():
    return RedirectResponse(url="/joao/app")


# CORS — allow frontend origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request logging middleware
app.add_middleware(RequestLoggingMiddleware)

# REST routes
app.include_router(joao_router)
app.include_router(qa_router)
app.include_router(scout_router)
app.include_router(voice_router)
app.include_router(ftp_router)
app.include_router(greengeeks_router)
app.include_router(telegram_webhook_router)
app.mount("/os", os_autonomy_app)


# PWA entry point
@app.get("/joao/app", include_in_schema=False)
async def pwa_app():
    return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")


# JOAO chat UI
@app.get("/joao/chat-ui", include_in_schema=False)
async def chat_ui():
    return FileResponse(_STATIC_DIR / "chat.html", media_type="text/html")


# Service worker — served from /sw.js (root) so its default scope covers /
@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse(
        _STATIC_DIR / "sw.js",
        media_type="application/javascript",
    )


# Static files
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# MCP mount — SSE transport at /mcp (exposes /mcp/sse endpoint)
mcp_app = mcp.sse_app()
app.mount("/mcp", mcp_app)

# TAOP MCP mount — Council dispatch, memory, SCOUT intel at /taop/mcp
taop_mcp_app = taop_mcp.sse_app()
app.mount("/taop/mcp", taop_mcp_app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
