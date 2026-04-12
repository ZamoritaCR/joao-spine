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

from fastapi import FastAPI, HTTPException
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
from routers.cockpit import router as cockpit_router
from routers.superpowers import router as superpowers_router
from routers.exocortex import router as exocortex_router
from routers.inspector import router as inspector_router
from routers.ingest import router as ingest_router

# Dr. Data (original) -- Tableau parser, DAX transpiler, direct mapper for superpowers
import sys as _sys
_DRDATA_PATH = "/home/zamoritacr/taop-repos/dr-data"
if os.path.isdir(_DRDATA_PATH) and _DRDATA_PATH not in _sys.path:
    _sys.path.insert(0, _DRDATA_PATH)

# Dr. Data V2 -- independent codebase (only available on ROG, not Railway)
_DRDATA_V2_PATH = "/home/zamoritacr/taop/drdata-v2"
_drdata_available = os.path.isdir(_DRDATA_V2_PATH)
if _drdata_available:
    if _DRDATA_V2_PATH not in _sys.path:
        _sys.path.insert(0, _DRDATA_V2_PATH)
    from api.drdata_router import router as drdata_router

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

    # Start streamable-HTTP session managers (their lifespans don't auto-run
    # when mounted as sub-apps under FastAPI).
    async with mcp.session_manager.run():
        async with taop_mcp.session_manager.run():
            logger.info("MCP streamable-HTTP session managers started")
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
# Serve Dr Data CSVs statically
_csv_dir = "/home/zamoritacr/taop/drdata-v2/static/drdata-csv"
if os.path.exists(_csv_dir):
    app.mount("/drdata-csv", StaticFiles(directory=_csv_dir), name="drdata-csv")


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
_ALLOWED_ORIGINS = [
    "https://joao.theartofthepossible.io",
    "https://dispatch.theartofthepossible.io",
    "https://drdata.theartofthepossible.io",
    "https://drdata-v2.theartofthepossible.io",
    "http://localhost:7772",
    "http://localhost:7778",
    "http://localhost:8100",
    "http://localhost:8502",
    "http://127.0.0.1:7772",
    "http://127.0.0.1:7778",
    "http://192.168.0.55:7772",
    "http://192.168.0.55:7778",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
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
if _drdata_available:
    app.include_router(drdata_router)
app.include_router(cockpit_router)
app.include_router(superpowers_router)
app.include_router(exocortex_router)
app.include_router(inspector_router)
app.include_router(ingest_router)
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


# AI Arena — multi-brain comparison UI
@app.get("/arena", include_in_schema=False)
async def arena_ui():
    return HTMLResponse((_STATIC_DIR / "arena.html").read_text())


# MrDP — neurodivergent companion
@app.get("/mrdp", include_in_schema=False)
async def mrdp_ui():
    return FileResponse(_STATIC_DIR / "mrdp.html", media_type="text/html")


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
#
# NOTE: mcp >= ~1.8 auto-prepends the ASGI root_path to the endpoint path,
# so we only pass "/messages/" to avoid double-prefixing (e.g. /mcp/mcp/messages/).
# Older mcp versions (< 1.8) need the full path including mount prefix.
from starlette.applications import Starlette
from starlette.responses import Response as StarletteResponse
from starlette.routing import Mount, Route
from mcp.server.sse import SseServerTransport

from importlib.metadata import version as _pkg_version
_mcp_version = tuple(int(x) for x in _pkg_version("mcp").split(".")[:2])

def _make_mcp_sse_app(mcp_server, mount_prefix: str) -> Starlette:
    """Build SSE Starlette app that advertises the full path including mount prefix."""
    # mcp >= 1.8 auto-prepends root_path; older versions need the full prefix
    if _mcp_version >= (1, 8):
        sse = SseServerTransport("/messages/")
    else:
        sse = SseServerTransport(f"{mount_prefix}/messages/")

    async def handle_sse(request):
        try:
            async with sse.connect_sse(
                request.scope, request.receive, request._send,
            ) as streams:
                await mcp_server._mcp_server.run(
                    streams[0], streams[1],
                    mcp_server._mcp_server.create_initialization_options(),
                )
        finally:
            # Clean up dead session from the transport's session dict so stale
            # session_ids can never receive messages meant for a new connection.
            sid_param = request.query_params.get("session_id", "")
            if not sid_param:
                # Session ID is in the path the server gave the client, but the
                # transport stores it by UUID key.  Walk the dict and remove any
                # writers whose underlying stream is closed.
                closed = [
                    k for k, w in list(sse._read_stream_writers.items())
                    if getattr(w, "_closed", False)
                ]
                for k in closed:
                    sse._read_stream_writers.pop(k, None)
                    logger.debug("Reaped closed SSE session %s", k)
        return StarletteResponse(status_code=200)

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

# ── Streamable HTTP MCP mounts ──────────────────────────────────────────
# Streamable HTTP uses regular POST/GET (no long-lived SSE connections),
# so it works through Cloudflare tunnels and proxies that buffer/drop SSE.
# Claude Desktop / Claude.ai should use these endpoints instead of /sse.
#
# NOTE: FastAPI doesn't propagate lifespan to mounted sub-apps, so we
# create the apps here and start their session managers in our lifespan.
_mcp_http_app = mcp.streamable_http_app()
_taop_mcp_http_app = taop_mcp.streamable_http_app()
app.mount("/mcp-http", _mcp_http_app)
app.mount("/taop/mcp-http", _taop_mcp_http_app)


# Dr. Data V2 frontend — served from spine via Cloudflare tunnel
_DRDATA_V2_HTML = Path.home() / "taop" / "drdata-v2" / "index.html"


@app.get("/drdata", include_in_schema=False)
@app.get("/drdata/", include_in_schema=False)
async def drdata_v2_frontend():
    if _DRDATA_V2_HTML.exists():
        from fastapi.responses import RedirectResponse, Response
        from starlette.requests import Request

        # If no cache-bust param, redirect to add one (forces browser to drop any cached version)
        # This is a one-time redirect; subsequent loads will have the param
        content = _DRDATA_V2_HTML.read_bytes()
        return Response(
            content=content,
            media_type="text/html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
                "ETag": str(len(content)),
            },
        )
    raise HTTPException(status_code=404, detail="Dr. Data V2 frontend not deployed")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
