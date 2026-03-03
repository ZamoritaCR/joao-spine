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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mcp_server import mcp
from routers.joao import router as joao_router
from routers.voice import router as voice_router

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("joao-spine starting up")
    yield
    logger.info("joao-spine shutting down")


app = FastAPI(
    title="joao-spine",
    description="Personal automation server — SSH dispatch, AI processing, idea vault",
    version="1.0.0",
    lifespan=lifespan,
)

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
app.include_router(voice_router)


# PWA entry point
@app.get("/joao/app", include_in_schema=False)
async def pwa_app():
    return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")


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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
