"""
Artifact store for superpowers -- manages output bundles.
Stores artifacts under /home/zamoritacr/joao-spine/superpower_artifacts/{job_id}/.
"""

import os
import uuid
import json
import shutil
from pathlib import Path
from datetime import datetime, timezone

ARTIFACTS_DIR = Path("/home/zamoritacr/joao-spine/superpower_artifacts")
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


def _job_dir(job_id: str) -> Path:
    d = ARTIFACTS_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_job_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]


def _safe_filename(filename: str) -> str:
    """Sanitize filename: strip path components, reject traversal attempts."""
    # Take only the final component (no directory traversal)
    name = Path(filename).name
    # Reject empty or hidden files
    if not name or name.startswith("."):
        name = "upload"
    # Remove any remaining path separators
    name = name.replace("/", "_").replace("\\", "_")
    return name


def save_upload(job_id: str, filename: str, content: bytes) -> str:
    safe_name = _safe_filename(filename)
    job_d = _job_dir(job_id)
    dest = (job_d / safe_name).resolve()
    # Final check: resolved path must be inside job dir
    if not str(dest).startswith(str(job_d.resolve())):
        raise ValueError(f"Path traversal blocked: {filename}")
    dest.write_bytes(content)
    return str(dest)


def save_artifact(job_id: str, filename: str, data) -> str:
    dest = _job_dir(job_id) / filename
    if isinstance(data, (dict, list)):
        dest.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    elif isinstance(data, bytes):
        dest.write_bytes(data)
    else:
        dest.write_text(str(data), encoding="utf-8")
    return str(dest)


def load_artifact(job_id: str, filename: str) -> Path:
    safe_name = _safe_filename(filename)
    job_d = _job_dir(job_id)
    p = (job_d / safe_name).resolve()
    # Path traversal guard
    if not str(p).startswith(str(job_d.resolve())):
        raise ValueError(f"Path traversal blocked: {filename}")
    if not p.exists():
        raise FileNotFoundError(f"Artifact not found: {job_id}/{safe_name}")
    return p


def list_artifacts(job_id: str) -> list[str]:
    d = ARTIFACTS_DIR / job_id
    if not d.exists():
        return []
    return [f.name for f in d.iterdir() if f.is_file()]


def save_context_pack(job_id: str, context: dict) -> str:
    return save_artifact(job_id, "context_pack.json", context)


def load_context_pack(job_id: str) -> dict:
    p = _job_dir(job_id) / "context_pack.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def make_zip_bundle(job_id: str) -> Path:
    job_d = _job_dir(job_id)
    zip_path = ARTIFACTS_DIR / f"{job_id}.zip"
    shutil.make_archive(str(zip_path.with_suffix("")), "zip", str(job_d))
    return zip_path
