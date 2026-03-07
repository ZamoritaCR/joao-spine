"""OS Autonomy proxy — forwards /os/* to the os-agent via dispatch tunnel.

Uses a raw ASGI app mounted at /os to avoid Starlette/FastAPI {path:path}
routing issues on Railway.
"""

from __future__ import annotations

import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

# Route through the Cloudflare tunnel to the local dispatch's /os-proxy endpoint.
# dispatch.theartofthepossible.io/os-proxy/* -> localhost:7801/* on the ROG.
_DISPATCH_TUNNEL = "https://dispatch.theartofthepossible.io"
_DISPATCH_URL = os.getenv("JOAO_LOCAL_DISPATCH_URL", "").rstrip("/")
if not _DISPATCH_URL or "localhost" in _DISPATCH_URL or "127.0.0.1" in _DISPATCH_URL:
    _DISPATCH_URL = _DISPATCH_TUNNEL
if _DISPATCH_URL.endswith("/os-proxy"):
    _DISPATCH_URL = _DISPATCH_URL.removesuffix("/os-proxy")
OS_AGENT_URL = os.getenv(
    "OS_AGENT_URL",
    f"{_DISPATCH_URL}/os-proxy" if _DISPATCH_URL else "http://192.168.0.55:7801",
).rstrip("/")
OS_AGENT_KEY = os.getenv("OS_AGENT_KEY", "joao-os-2026")
_DISPATCH_SECRET = os.getenv("JOAO_DISPATCH_SECRET", "")


class OsProxyApp:
    """Raw ASGI proxy -- mounted at /os, forwards everything to os-agent."""

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return

        # Mount passes full path; strip the /os prefix
        raw_path = scope.get("path", "/")
        root_path = scope.get("root_path", "")
        path = raw_path.split("/os/", 1)[-1] if "/os/" in raw_path else raw_path.lstrip("/")
        logger.info("os-proxy ASGI: raw_path=%s root_path=%s resolved=%s", raw_path, root_path, path)
        method = scope.get("method", "GET")

        # Debug endpoint
        if path == "_debug":
            debug_body = json.dumps({
                "raw_path": raw_path,
                "root_path": root_path,
                "resolved_path": path,
                "os_agent_url": OS_AGENT_URL,
                "dispatch_secret_set": bool(_DISPATCH_SECRET),
            }).encode()
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [[b"content-type", b"application/json"], [b"content-length", str(len(debug_body)).encode()]],
            })
            await send({"type": "http.response.body", "body": debug_body})
            return

        # Read request body
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body", False):
                break

        headers = {"Content-Type": "application/json"}
        if "os-proxy" in OS_AGENT_URL:
            headers["Authorization"] = f"Bearer {_DISPATCH_SECRET}"
        else:
            headers["X-API-Key"] = OS_AGENT_KEY

        target = f"{OS_AGENT_URL}/{path}" if path else OS_AGENT_URL

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.request(method=method, url=target, headers=headers, content=body)
            resp_body = r.content
            status = r.status_code
            content_type = r.headers.get("content-type", "application/json")
        except httpx.ConnectError:
            logger.error("os-agent not reachable at %s", OS_AGENT_URL)
            resp_body = json.dumps({"detail": f"os-agent not reachable at {OS_AGENT_URL}"}).encode()
            status = 503
            content_type = "application/json"
        except httpx.TimeoutException:
            resp_body = json.dumps({"detail": "os-agent request timed out"}).encode()
            status = 504
            content_type = "application/json"

        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                [b"content-type", content_type.encode()],
                [b"content-length", str(len(resp_body)).encode()],
            ],
        })
        await send({
            "type": "http.response.body",
            "body": resp_body,
        })


os_app = OsProxyApp()
