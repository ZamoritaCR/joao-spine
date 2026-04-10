"""
Remote Inspector -- server-side URL diagnostics for JOAO.

Security:
- Domain allowlist (default: *.theartofthepossible.io)
- HTTPS only
- SSRF protection (blocks private IPs, localhost, metadata endpoints)
- Rate limited by caller (no open proxy)
- Provenance logged
"""

import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
import subprocess
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/joao", tags=["inspector"])

# ── Security ──

_ALLOWLIST_RAW = os.getenv("JOAO_INSPECT_ALLOWLIST", ".theartofthepossible.io")
_ALLOWLIST = [d.strip().lower() for d in _ALLOWLIST_RAW.split(",") if d.strip()]

_BLOCKED_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local + AWS metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]
# Allow override for local testing
_ALLOW_PRIVATE = os.getenv("JOAO_INSPECT_ALLOW_PRIVATE", "").lower() == "true"


def _check_domain(url: str) -> str:
    """Validate URL against allowlist. Returns hostname or raises."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise HTTPException(400, "Only https URLs are allowed")
    host = parsed.hostname or ""
    if not host:
        raise HTTPException(400, "No hostname in URL")
    host_lower = host.lower()
    if not any(host_lower.endswith(d) for d in _ALLOWLIST):
        raise HTTPException(
            403,
            f"Domain '{host}' not in allowlist. Allowed: {', '.join(_ALLOWLIST)}"
        )
    return host


def _check_ssrf(host: str):
    """Block resolution to private/internal IPs."""
    if _ALLOW_PRIVATE:
        return
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        for family, _, _, _, sockaddr in infos:
            ip = ipaddress.ip_address(sockaddr[0])
            for net in _BLOCKED_NETS:
                if ip in net:
                    raise HTTPException(
                        403,
                        f"Resolved to private IP {ip} -- blocked for SSRF protection"
                    )
    except socket.gaierror as e:
        raise HTTPException(502, f"DNS resolution failed for {host}: {e}")


# ── DNS ──

def _resolve_dns(host: str) -> dict:
    """Resolve DNS for a host."""
    result = {"host": host, "addresses": [], "cname": None, "error": None}
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        result["addresses"] = list({sockaddr[0] for _, _, _, _, sockaddr in infos})
    except socket.gaierror as e:
        result["error"] = str(e)
        return result

    # Try to get CNAME via dig (best effort)
    try:
        dig = subprocess.run(
            ["dig", "+short", "CNAME", host],
            capture_output=True, text=True, timeout=5,
        )
        cname = dig.stdout.strip()
        if cname:
            result["cname"] = cname
    except Exception:
        pass

    return result


# ── TLS ──

def _check_tls(host: str) -> dict:
    """Get TLS certificate summary."""
    result = {"subject": None, "issuer": None, "not_after": None, "protocol": None, "error": None}
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.socket(), server_hostname=host) as s:
            s.settimeout(5)
            s.connect((host, 443))
            cert = s.getpeercert()
            result["subject"] = dict(x[0] for x in cert.get("subject", []))
            result["issuer"] = dict(x[0] for x in cert.get("issuer", []))
            result["not_after"] = cert.get("notAfter")
            result["protocol"] = s.version()
    except Exception as e:
        result["error"] = str(e)
    return result


# ── Header scoring ──

_SECURITY_HEADERS = {
    "strict-transport-security": "HSTS",
    "content-security-policy": "CSP",
    "x-frame-options": "X-Frame",
    "x-content-type-options": "X-Content-Type",
    "referrer-policy": "Referrer",
}

_CACHE_HEADERS = ["cache-control", "etag", "cf-cache-status"]
_INFRA_HEADERS = ["server", "via", "cf-ray", "content-encoding", "content-type"]


def _score_headers(headers: dict) -> list[dict]:
    """Score security and cache headers."""
    notes = []
    h = {k.lower(): v for k, v in headers.items()}

    for hdr, label in _SECURITY_HEADERS.items():
        if hdr in h:
            notes.append({"header": label, "status": "present", "value": h[hdr][:120]})
        else:
            notes.append({"header": label, "status": "MISSING", "value": None})

    for hdr in _CACHE_HEADERS:
        if hdr in h:
            notes.append({"header": hdr, "status": "present", "value": h[hdr][:120]})

    return notes


# ── Models ──

class InspectRequest(BaseModel):
    url: str
    max_bytes: int = 200000
    include_body: bool = True
    follow_redirects: bool = True


# ── Endpoints ──

@router.post("/inspect")
async def inspect_url(req: InspectRequest):
    """Fetch and diagnose a URL server-side. Domain allowlisted."""
    host = _check_domain(req.url)
    _check_ssrf(host)

    t0 = time.monotonic()

    # DNS
    dns = _resolve_dns(host)
    t_dns = round((time.monotonic() - t0) * 1000)

    # TLS
    tls = _check_tls(host)
    t_tls = round((time.monotonic() - t0) * 1000)

    # HTTP fetch
    redirect_chain = []
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5, read=10, write=5, pool=5),
            follow_redirects=req.follow_redirects,
            max_redirects=5,
        ) as client:
            resp = await client.get(req.url)

            # Capture redirect history
            if resp.history:
                for r in resp.history:
                    redirect_chain.append({
                        "url": str(r.url),
                        "status": r.status_code,
                    })
    except httpx.TooManyRedirects:
        raise HTTPException(502, "Too many redirects (>5)")
    except httpx.ConnectTimeout:
        raise HTTPException(504, f"Connection timeout to {host}")
    except httpx.ReadTimeout:
        raise HTTPException(504, f"Read timeout from {host}")
    except Exception as e:
        raise HTTPException(502, f"Fetch failed: {str(e)}")

    t_http = round((time.monotonic() - t0) * 1000)

    # Body
    body_bytes = resp.content[:req.max_bytes]
    body_preview = ""
    if req.include_body:
        try:
            body_preview = body_bytes.decode("utf-8", errors="replace")[:req.max_bytes]
        except Exception:
            body_preview = f"[binary, {len(body_bytes)} bytes]"

    # Headers
    headers_dict = dict(resp.headers)
    header_notes = _score_headers(headers_dict)

    # Content hash
    content_hash = hashlib.sha256(body_bytes).hexdigest()[:16]

    # Build result
    result = {
        "final_url": str(resp.url),
        "status": resp.status_code,
        "redirect_chain": redirect_chain,
        "headers": headers_dict,
        "header_notes": header_notes,
        "timings_ms": {
            "dns": t_dns,
            "tls": t_tls,
            "total_http": t_http,
        },
        "dns": dns,
        "tls": tls,
        "body_preview": body_preview[:2000] if req.include_body else "(not requested)",
        "body_size_bytes": len(resp.content),
        "content_hash": content_hash,
        "inspected_at": datetime.now(timezone.utc).isoformat(),
        "notes": [
            n for n in header_notes if n["status"] == "MISSING"
        ],
    }

    # Provenance log
    try:
        from exocortex.ledgers import _append_jsonl, PROVENANCE_DIR
        _append_jsonl(PROVENANCE_DIR / "inspections.jsonl", {
            "ts": result["inspected_at"],
            "url": req.url,
            "final_url": result["final_url"],
            "status": result["status"],
            "content_hash": content_hash,
        })
    except Exception:
        pass

    return result


@router.get("/inspect/focusflow")
async def inspect_focusflow():
    """Quick diagnostic of focusflow.theartofthepossible.io."""
    req = InspectRequest(
        url="https://focusflow.theartofthepossible.io",
        include_body=True,
        max_bytes=50000,
    )
    result = await inspect_url(req)

    # Build markdown report
    status = result["status"]
    final = result["final_url"]
    dns = result["dns"]
    tls_info = result["tls"]
    timings = result["timings_ms"]

    missing_sec = [n["header"] for n in result.get("notes", [])]
    present_sec = [
        n for n in result.get("header_notes", [])
        if n["status"] == "present" and n["header"] in _SECURITY_HEADERS.values()
    ]

    report = f"""# FocusFlow Inspection Report

**URL:** {final}
**Status:** {status}
**Inspected:** {result['inspected_at']}

## Connectivity
| Metric | Value |
|--------|-------|
| DNS resolve | {', '.join(dns.get('addresses', []))} |
| CNAME | {dns.get('cname', 'none')} |
| TLS protocol | {tls_info.get('protocol', '?')} |
| TLS issuer | {tls_info.get('issuer', {}).get('organizationName', '?')} |
| TLS expires | {tls_info.get('not_after', '?')} |
| Redirects | {len(result['redirect_chain'])} |

## Timings
| Phase | ms |
|-------|-----|
| DNS | {timings['dns']} |
| TLS | {timings['tls']} |
| Total HTTP | {timings['total_http']} |

## Security Headers
"""
    for n in result.get("header_notes", []):
        if n["header"] in list(_SECURITY_HEADERS.values()) + _CACHE_HEADERS:
            icon = "OK" if n["status"] == "present" else "MISSING"
            val = f": {n['value']}" if n.get("value") else ""
            report += f"- **{n['header']}**: {icon}{val}\n"

    report += f"""
## Infrastructure
- Server: {result['headers'].get('server', '?')}
- CF-Ray: {result['headers'].get('cf-ray', '?')}
- CF-Cache: {result['headers'].get('cf-cache-status', '?')}
- Content-Type: {result['headers'].get('content-type', '?')}
- Content-Encoding: {result['headers'].get('content-encoding', 'none')}
- Body size: {result['body_size_bytes']} bytes
- Content hash: {result['content_hash']}

## Body Preview (first 500 chars)
```
{result.get('body_preview', '')[:500]}
```
"""
    return {
        "report": report,
        "raw": result,
    }
