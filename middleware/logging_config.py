"""JSON structured logging formatter and request/response middleware."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from pythonjsonlogger.json import JsonFormatter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

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


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs request start/end with latency. Propagates X-Request-ID."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        t0 = time.monotonic()

        logger.info(
            "request_start",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
            },
        )

        response: Response = await call_next(request)

        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        logger.info(
            "request_end",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
            },
        )

        response.headers["X-Request-ID"] = request_id
        return response
