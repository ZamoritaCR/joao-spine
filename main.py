"""FastAPI app, lifespan, MCP mounts for joao-spine."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI

from mcp_server import mcp
from routers.joao import router as joao_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


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

# REST routes
app.include_router(joao_router)

# MCP mount — SSE transport at /mcp (exposes /mcp/sse endpoint)
mcp_app = mcp.sse_app()
app.mount("/mcp", mcp_app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
