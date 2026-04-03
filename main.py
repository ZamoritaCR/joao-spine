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
import os

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
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
from routers.voice_chat import router as voice_chat_router
from routers.terminal import router as terminal_router
from routers.hub import router as hub_router
from terminal_manager import terminal_manager
from routers.ftp import router as ftp_router
from routers.greengeeks import router as greengeeks_router
from routers.telegram_webhook import router as telegram_webhook_router
from routers.os_autonomy import os_app as os_autonomy_app
from routers.arena import router as arena_router

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
    await terminal_manager.start()
    yield
    await terminal_manager.stop()
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
app.include_router(voice_chat_router)
app.include_router(terminal_router)
app.include_router(hub_router)
app.include_router(ftp_router)
app.include_router(greengeeks_router)
app.include_router(telegram_webhook_router)
app.include_router(arena_router)
app.mount("/os", os_autonomy_app)


# TAOP Hub (Mission Control) -- served via Cloudflare tunnel
_TAOP_SITE_DIR = Path.home() / "taop-site"


@app.get("/hub", include_in_schema=False)
async def taop_hub():
    # JOAO Living OS hub (primary)
    living_hub = _STATIC_DIR / "hub.html"
    if living_hub.exists():
        return FileResponse(living_hub, media_type="text/html")
    # Fallback to taop-site hub
    hub_path = _TAOP_SITE_DIR / "hub.html"
    if hub_path.exists():
        return FileResponse(hub_path, media_type="text/html")
    return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")


@app.get("/login", include_in_schema=False)
async def taop_login():
    login_path = _TAOP_SITE_DIR / "login.html"
    if login_path.exists():
        return FileResponse(login_path, media_type="text/html")
    raise HTTPException(status_code=404, detail="Login page not found")


# Telemetry proxy for hub (when accessed remotely via tunnel)
@app.get("/api/stats", include_in_schema=False)
async def telemetry_stats_proxy():
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("http://localhost:8200/api/stats")
            return resp.json()
    except Exception:
        return {"error": "telemetry service unreachable", "uptime_seconds": 0}


# PWA entry point
@app.get("/joao/app", include_in_schema=False)
async def pwa_app():
    return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")


# JOAO chat UI
@app.get("/joao/chat-ui", include_in_schema=False)
async def chat_ui():
    return FileResponse(_STATIC_DIR / "chat.html", media_type="text/html")


# JOAO Voice UI — joao.theartofthepossible.io
@app.get("/joao/voice", include_in_schema=False)
async def voice_ui():
    return FileResponse(_STATIC_DIR / "voice.html", media_type="text/html")


# JOAO Terminal — browser shell to ROG Strix
@app.get("/joao/terminal", include_in_schema=False)
async def terminal_ui():
    token = os.environ.get("JOAO_TERMINAL_TOKEN") or os.environ.get("JOAO_DISPATCH_HMAC_SECRET", "")
    html = (_STATIC_DIR / "terminal.html").read_text()
    html = html.replace("__TERMINAL_TOKEN__", token)
    return HTMLResponse(html)


# Service worker — served from /sw.js (root) so its default scope covers /
@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse(
        _STATIC_DIR / "sw.js",
        media_type="application/javascript",
    )


# Static files
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# ── MCP SSE mounts ────────────────────────────────────────────────────────
# We create the SSE transports manually so the advertised messages endpoint
# includes the mount prefix (e.g. /mcp/messages/ instead of /messages/).
# Without this, clients POST to the wrong path and get 404.
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from mcp.server.sse import SseServerTransport

def _make_mcp_sse_app(mcp_server, mount_prefix: str) -> Starlette:
    """Build SSE Starlette app that advertises the full path including mount prefix."""
    sse = SseServerTransport(f"{mount_prefix}/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send,
        ) as streams:
            await mcp_server._mcp_server.run(
                streams[0], streams[1],
                mcp_server._mcp_server.create_initialization_options(),
            )

    return Starlette(
        debug=mcp_server.settings.debug,
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )

# MCP mount at /mcp (exposes /mcp/sse endpoint)
app.mount("/mcp", _make_mcp_sse_app(mcp, "/mcp"))

# TAOP MCP mount at /taop/mcp
app.mount("/taop/mcp", _make_mcp_sse_app(taop_mcp, "/taop/mcp"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
