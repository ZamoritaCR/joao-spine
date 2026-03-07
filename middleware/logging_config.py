"""JSON structured logging formatter and request/response middleware."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from pythonjsonlogger.json import JsonFormatter
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)


class _JoaoJsonFormatter(JsonFormatter):
    """Adds fixed service field and normalises level/timestamp keys."""

    def add_fields(
        self,
        log_record: dict,
        record: logging.LogRecord,
        message_dict: dict,
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record["timestamp"] = datetime.now(timezone.utc).isoformat()
        log_record["level"] = record.levelname
        log_record["service"] = "joao-spine"
        log_record.pop("levelname", None)
        log_record.pop("asctime", None)


def configure_json_logging() -> None:
    """Call once at startup. Replaces root handler formatters with JSON."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(_JoaoJsonFormatter())
    root.addHandler(handler)


class RequestLoggingMiddleware:
    """Pure ASGI middleware. Logs request start/end with latency.

    Uses raw ASGI instead of BaseHTTPMiddleware so that SSE / streaming
    responses (like the MCP transport) are passed through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Pass MCP routes straight through — no wrapping at all
        if path.startswith("/mcp/") or path.startswith("/taop/mcp/"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        t0 = time.monotonic()

        logger.info(
            "request_start",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": path,
            },
        )

        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = dict(message.get("headers", []))
                headers[b"x-request-id"] = request_id.encode()
                message = {**message, "headers": list(headers.items())}
            await send(message)

        await self.app(scope, receive, send_wrapper)

        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        logger.info(
            "request_end",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": path,
                "status_code": status_code,
                "latency_ms": latency_ms,
            },
        )
