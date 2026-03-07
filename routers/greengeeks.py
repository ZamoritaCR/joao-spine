"""GreenGeeks cPanel deploy + health status endpoints."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/greengeeks", tags=["greengeeks"])

_SCRIPTS_DIR = Path.home() / "scripts"
_HEALTH_JSON = Path.home() / "research" / "GREENGEEKS_HEALTH.json"


# ── Schemas ──────────────────────────────────────────────────────────────────

class DeployFile(BaseModel):
    remote_path: str = Field(..., description="Destination path on server (relative to domain root)")
    content: str = Field(..., description="File content to upload")


class DeployRequest(BaseModel):
    domain: str = Field(..., description="Root domain, e.g. dopamine.watch")
    subdomain: str = Field("", description="Subdomain to create (leave empty to deploy to root domain)")
    files: list[DeployFile] = Field(default_factory=list, description="Files to upload")
    install_ssl: bool = Field(True, description="Install/renew SSL certificate")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_greengeeks_api():
    """Import GreenGeeksAPI from ~/scripts, loading .env first."""
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))

    env_file = Path.home() / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

    from greengeeks_api import GreenGeeksAPI  # type: ignore
    return GreenGeeksAPI


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/deploy")
async def greengeeks_deploy(req: DeployRequest) -> dict[str, Any]:
    """Deploy files to GreenGeeks via cPanel API.

    Optionally creates a subdomain and installs SSL, then uploads the
    provided files into the domain document root.
    """
    logger.info("GreenGeeks deploy: domain=%s subdomain=%s files=%d",
                req.domain, req.subdomain, len(req.files))

    try:
        GreenGeeksAPI = _load_greengeeks_api()
        api = GreenGeeksAPI()
    except ValueError as e:
        raise HTTPException(500, f"GreenGeeks API not configured: {e}")
    except Exception as e:
        raise HTTPException(500, f"Failed to initialise GreenGeeks API: {e}")

    results: dict[str, Any] = {"domain": req.domain, "steps": []}
    target = f"{req.subdomain}.{req.domain}" if req.subdomain else req.domain

    # Step 1: create subdomain (optional)
    if req.subdomain:
        sub_result = api.create_subdomain(req.subdomain, req.domain)
        step = {"step": "create_subdomain", "target": target, "ok": sub_result["ok"]}
        if not sub_result["ok"]:
            err = str(sub_result.get("error", ""))
            if "already exists" in err.lower():
                step["note"] = "already exists, continuing"
                step["ok"] = True
            else:
                step["warning"] = err
        results["steps"].append(step)

    # Step 2: install SSL
    if req.install_ssl:
        ssl_result = api.install_ssl(target)
        results["steps"].append({
            "step": "install_ssl",
            "target": target,
            "ok": ssl_result["ok"],
            "warning": None if ssl_result["ok"] else str(ssl_result.get("error", "")),
        })

    # Step 3: upload files
    remote_base = f"/home/{api.username}/{target}"
    api.create_directory(remote_base)

    uploaded, failed = 0, 0
    upload_errors: list[str] = []
    for f in req.files:
        remote_path = f"{remote_base}/{f.remote_path.lstrip('/')}"
        remote_dir = str(Path(remote_path).parent)
        api.create_directory(remote_dir)

        # write content to a temp file then upload
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=Path(f.remote_path).suffix,
                                         delete=False) as tmp:
            tmp.write(f.content)
            tmp_path = tmp.name

        up_result = api.upload_file(tmp_path, remote_path)
        Path(tmp_path).unlink(missing_ok=True)

        if up_result["ok"]:
            uploaded += 1
        else:
            failed += 1
            upload_errors.append(f"{f.remote_path}: {up_result.get('error', '')}")

    results["steps"].append({
        "step": "upload_files",
        "uploaded": uploaded,
        "failed": failed,
        "errors": upload_errors[:10],
    })

    results["url"] = f"https://{target}"
    results["ok"] = failed == 0
    logger.info("GreenGeeks deploy done: %s uploaded=%d failed=%d", target, uploaded, failed)
    return results


@router.post("/status")
async def greengeeks_status() -> dict[str, Any]:
    """Return latest GreenGeeks health dashboard from GREENGEEKS_HEALTH.json."""
    if not _HEALTH_JSON.exists():
        raise HTTPException(
            503,
            "Health data not available — run greengeeks_monitor.py first "
            "(cron: */5 * * * * python3 ~/scripts/greengeeks_monitor.py)"
        )

    try:
        data = json.loads(_HEALTH_JSON.read_text())
    except Exception as e:
        raise HTTPException(500, f"Failed to read health data: {e}")

    return data
