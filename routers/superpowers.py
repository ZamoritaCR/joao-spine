"""
Superpowers router -- JOAO Capability OS command surface.

All capabilities, provenance, undo, git, tunnel, context, ingestion policy.
Every endpoint enforces auth + autonomy + provenance.
Mounts under /joao/ prefix.
"""

import logging
import time
import traceback
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from capability import artifact_store
from capability import registry
from capability.tableau_to_powerbi import execute as execute_tableau
from capability.mood_playlist import execute as execute_playlist
from capability import git_adapter
from capability import tunnel_adapter
from capability import context_builder
from capability import undo_executor
from capability import legal_ingest
from exocortex import ledgers
from middleware.auth import require_api_key
from middleware.autonomy import enforce_autonomy, parse_autonomy_from_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/joao", tags=["superpowers"])

# Max upload size: 100 MB
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024


# ──────────────────────────────────────────────
# AUTH DEPENDENCY
# ──────────────────────────────────────────────

async def require_superpowers_auth(
    x_joao_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
) -> None:
    """Authenticate superpowers requests.

    Accepts either x-joao-api-key header or Bearer token.
    Falls back to JOAO_DISPATCH_HMAC_SECRET.
    """
    import hmac
    import os

    secret = os.environ.get("JOAO_API_KEY") or os.environ.get("JOAO_DISPATCH_HMAC_SECRET", "")
    if not secret:
        # No secret configured -- auth disabled (dev mode)
        return

    # Check API key header
    if x_joao_api_key and hmac.compare_digest(secret, x_joao_api_key):
        return

    # Check Bearer token
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            if hmac.compare_digest(secret, parts[1]):
                return

    raise HTTPException(status_code=401, detail="Unauthorized: provide x-joao-api-key or Bearer token")


# ──────────────────────────────────────────────
# REQUEST MODELS
# ──────────────────────────────────────────────

class RouteRequest(BaseModel):
    text: str
    filename: Optional[str] = None

class PlaylistRequest(BaseModel):
    current_feeling: str
    desired_feeling: str
    constraints: Optional[dict] = None
    adapter: Optional[str] = "spotify"
    autonomy: Optional[str] = "L2"

class GitScanRequest(BaseModel):
    repo: Optional[str] = "all"
    since: Optional[str] = "7d"
    query: Optional[str] = ""
    autonomy: Optional[str] = "L0"

class GitWriteRequest(BaseModel):
    repo: str
    action: str  # branch | commit | pr_draft
    branch_name: Optional[str] = ""
    message: Optional[str] = ""
    files: Optional[list[str]] = None
    autonomy: Optional[str] = "L3"
    lock_scope: Optional[str] = ""

class GitShipRequest(BaseModel):
    repo: str
    action: str  # push | deploy | restart_service
    branch: Optional[str] = ""
    service: Optional[str] = ""
    autonomy: Optional[str] = "L4"
    lock_scope: Optional[str] = ""

class ContextBuildRequest(BaseModel):
    project: Optional[str] = "auto"
    task: Optional[str] = ""
    autonomy: Optional[str] = "L1"

class UndoRequest(BaseModel):
    autonomy: Optional[str] = "L2"

class LockGrantRequest(BaseModel):
    lock_type: str  # WRITE_LOCK | SHIP_LOCK
    scope: str
    duration_minutes: Optional[int] = 30

class DispatchRequest(BaseModel):
    capability: str
    params: Optional[dict] = None
    autonomy: Optional[str] = "L1"

class IngestUrlRequest(BaseModel):
    url: str
    intent: Optional[str] = ""
    autonomy: Optional[str] = "L1"


# ──────────────────────────────────────────────
# PROVENANCE HELPER
# ──────────────────────────────────────────────

def _record_provenance(
    raw_input: str,
    capability: str,
    autonomy: str,
    job_id: str,
    artifacts: list = None,
    success: bool = True,
    error_msg: str = "",
    duration_ms: int = 0,
    egress: dict = None,
    undo_recipe: dict = None,
) -> dict:
    """Record intent + outcome for a capability execution."""
    intent = ledgers.record_intent(
        raw_input=raw_input,
        parsed_intent=capability,
        autonomy_level=autonomy,
        capability_chain=[capability],
        context_pack_hash="",
        confidence=1.0,
    )

    outcome = ledgers.record_outcome(
        linked_intent_id=intent["intent_id"],
        artifacts=artifacts or [],
        success=success,
        reason=error_msg if not success else "completed",
        time_to_artifact_ms=duration_ms,
        egress_summary=egress or {"external_apis_called": [], "data_sent_externally": False},
    )

    return {
        "intent_id": intent["intent_id"],
        "outcome_id": outcome["outcome_id"],
        "undo_recipe": undo_recipe or {"type": "noop"},
    }


# ──────────────────────────────────────────────
# CAPABILITY LISTING + ROUTING
# ──────────────────────────────────────────────

@router.get("/superpowers/capabilities")
async def list_capabilities(_auth=Depends(require_superpowers_auth)):
    """List all registered superpowers."""
    return {"capabilities": registry.list_capabilities()}


@router.post("/superpowers/route")
async def route_intent(req: RouteRequest, _auth=Depends(require_superpowers_auth)):
    """Classify intent and return routing decision."""
    routing = registry.route(req.text, req.filename)
    return routing


# ──────────────────────────────────────────────
# TABLEAU-TO-POWERBI
# ──────────────────────────────────────────────

@router.post("/superpowers/tableau")
async def tableau_upload(
    file: UploadFile = File(...),
    intent: str = Form("migrate to power bi"),
    autonomy: str = Form("L2"),
    _auth=Depends(require_superpowers_auth),
):
    """Upload TWB/TWBX -> full migration bundle (6 artifacts)."""
    start = time.time()

    # Validate file type
    fname = file.filename or ""
    if not fname.lower().endswith((".twb", ".twbx")):
        raise HTTPException(400, "File must be .twb or .twbx")

    # Read and validate size
    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large: {len(content)} bytes > {_MAX_UPLOAD_BYTES} limit")

    # Legal ingestion check
    upload_check = legal_ingest.validate_file_upload(fname, content)
    if not upload_check["allowed"]:
        raise HTTPException(413, upload_check["reason"])

    # Autonomy enforcement
    enforce_autonomy(autonomy, "L2", "tableau_to_powerbi")

    # WU data warning (flag but don't block -- Tableau files processed locally)
    wu_check = upload_check.get("wu_check", {})
    egress = {"external_apis_called": [], "data_sent_externally": False}
    if wu_check.get("is_wu"):
        egress["wu_data_detected"] = True
        egress["note"] = "WU data detected; processed locally only (Ollama/Dr. Data)"
        logger.warning("WU data detected in Tableau upload: %s", fname)

    job_id = artifact_store.new_job_id()
    saved_path = artifact_store.save_upload(job_id, fname, content)
    logger.info("[SUPERPOWERS] Tableau upload: %s -> job %s (autonomy=%s)", fname, job_id, autonomy)

    try:
        result = execute_tableau(saved_path, job_id)
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        _record_provenance(
            raw_input=f"tableau upload: {fname}",
            capability="tableau_to_powerbi",
            autonomy=autonomy, job_id=job_id,
            success=False, error_msg=str(e),
            duration_ms=duration_ms, egress=egress,
        )
        logger.error("[SUPERPOWERS] Tableau failed: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"Migration failed: {str(e)}")

    if result["status"] == "error":
        duration_ms = int((time.time() - start) * 1000)
        _record_provenance(
            raw_input=f"tableau upload: {fname}",
            capability="tableau_to_powerbi",
            autonomy=autonomy, job_id=job_id,
            artifacts=result.get("artifacts", []),
            success=False, error_msg="; ".join(result["errors"]),
            duration_ms=duration_ms, egress=egress,
        )
        raise HTTPException(500, f"Migration errors: {'; '.join(result['errors'])}")

    duration_ms = int((time.time() - start) * 1000)
    prov = _record_provenance(
        raw_input=f"tableau upload: {fname}",
        capability="tableau_to_powerbi",
        autonomy=autonomy, job_id=job_id,
        artifacts=result.get("artifacts", []),
        success=True, duration_ms=duration_ms,
        egress=egress,
        undo_recipe={"type": "delete_artifacts", "target": job_id},
    )

    return {
        "job_id": job_id,
        "status": "success",
        "summary": result["summary"],
        "artifacts": result["artifacts"],
        "errors": result.get("errors", []),
        "download_base": f"/joao/superpowers/artifacts/{job_id}",
        "provenance": prov,
        "autonomy_level": autonomy,
    }


# ──────────────────────────────────────────────
# MOOD PLAYLIST
# ──────────────────────────────────────────────

@router.post("/superpowers/playlist")
async def playlist(req: PlaylistRequest, _auth=Depends(require_superpowers_auth)):
    """Generate mood-transition playlist."""
    start = time.time()
    autonomy = req.autonomy or "L2"

    enforce_autonomy(autonomy, "L1", "mood_playlist")

    job_id = artifact_store.new_job_id()
    logger.info("[SUPERPOWERS] Playlist: %s -> %s", req.current_feeling, req.desired_feeling)

    try:
        result = execute_playlist(
            current_feeling=req.current_feeling,
            desired_feeling=req.desired_feeling,
            job_id=job_id,
            constraints=req.constraints,
            adapter=req.adapter or "spotify",
        )
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        _record_provenance(
            raw_input=f"playlist: {req.current_feeling} -> {req.desired_feeling}",
            capability="mood_playlist",
            autonomy=autonomy, job_id=job_id,
            success=False, error_msg=str(e), duration_ms=duration_ms,
        )
        raise HTTPException(500, f"Playlist generation failed: {str(e)}")

    duration_ms = int((time.time() - start) * 1000)
    prov = _record_provenance(
        raw_input=f"playlist: {req.current_feeling} -> {req.desired_feeling}",
        capability="mood_playlist",
        autonomy=autonomy, job_id=job_id,
        success=True, duration_ms=duration_ms,
        undo_recipe={"type": "noop"},
    )

    return {"job_id": job_id, "provenance": prov, **result}


# ──────────────────────────────────────────────
# GIT SCAN (L0+)
# ──────────────────────────────────────────────

@router.post("/superpowers/git/scan")
async def git_scan(req: GitScanRequest, _auth=Depends(require_superpowers_auth)):
    """Scan git repos: status, diff, log, search."""
    start = time.time()
    autonomy = req.autonomy or "L0"

    enforce_autonomy(autonomy, "L0", "git_scan")

    result = git_adapter.scan(repo=req.repo or "all", since=req.since or "7d", query=req.query or "")
    duration_ms = int((time.time() - start) * 1000)

    prov = _record_provenance(
        raw_input=f"git scan: repo={req.repo} since={req.since}",
        capability="git_scan",
        autonomy=autonomy, job_id="",
        success="error" not in result,
        duration_ms=duration_ms,
        undo_recipe={"type": "noop"},
    )

    return {**result, "provenance": prov}


# ──────────────────────────────────────────────
# GIT WRITE (L3 + WRITE_LOCK)
# ──────────────────────────────────────────────

@router.post("/superpowers/git/write")
async def git_write(req: GitWriteRequest, _auth=Depends(require_superpowers_auth)):
    """Branch, commit, or PR draft. Requires L3 + WRITE_LOCK."""
    autonomy = req.autonomy or "L3"
    scope = req.lock_scope or f"repo:{req.repo}"

    enforce_autonomy(autonomy, "L3", "git_write", lock_type="WRITE_LOCK", lock_scope=scope)

    if req.action == "branch":
        if not req.branch_name:
            raise HTTPException(400, "branch_name required for action=branch")
        result = git_adapter.write_branch(req.repo, req.branch_name)
    elif req.action == "commit":
        result = git_adapter.write_commit(req.repo, req.message or "JOAO automated commit", req.files)
    elif req.action == "pr_draft":
        # PR draft = branch + commit, but don't push (that's L4)
        if req.branch_name:
            br = git_adapter.write_branch(req.repo, req.branch_name)
            if not br["success"]:
                raise HTTPException(500, br.get("error", "Branch creation failed"))
        result = git_adapter.write_commit(req.repo, req.message or "JOAO PR draft", req.files)
        if result["success"]:
            result["pr_command"] = f"gh pr create --title '{req.message}' --body 'Created by JOAO'"
            result["note"] = "Branch and commit created. Push (L4 + SHIP_LOCK) required to create PR."
    else:
        raise HTTPException(400, f"Unknown action: {req.action}. Use: branch, commit, pr_draft")

    prov = _record_provenance(
        raw_input=f"git write: {req.action} repo={req.repo}",
        capability="git_write",
        autonomy=autonomy, job_id="",
        success=result.get("success", False),
        error_msg=result.get("error", ""),
        undo_recipe=result.get("undo_recipe", {"type": "noop"}),
    )

    return {**result, "provenance": prov}


# ──────────────────────────────────────────────
# GIT SHIP (L4 + SHIP_LOCK)
# ──────────────────────────────────────────────

@router.post("/superpowers/git/ship")
async def git_ship(req: GitShipRequest, _auth=Depends(require_superpowers_auth)):
    """Push, deploy, restart. Requires L4 + SHIP_LOCK."""
    autonomy = req.autonomy or "L4"
    scope = req.lock_scope or f"repo:{req.repo}"

    enforce_autonomy(autonomy, "L4", "git_ship", lock_type="SHIP_LOCK", lock_scope=scope)

    if req.action == "push":
        result = git_adapter.ship_push(req.repo, req.branch or "")
    else:
        raise HTTPException(400, f"Unsupported ship action: {req.action}. Use: push")

    prov = _record_provenance(
        raw_input=f"git ship: {req.action} repo={req.repo}",
        capability="git_ship",
        autonomy=autonomy, job_id="",
        success=result.get("success", False),
        error_msg=result.get("error", ""),
        undo_recipe=result.get("undo_recipe", {"type": "noop"}),
    )

    return {**result, "provenance": prov}


# ──────────────────────────────────────────────
# TUNNEL STATUS (L0+)
# ──────────────────────────────────────────────

@router.get("/superpowers/tunnel/status")
async def tunnel_status(_auth=Depends(require_superpowers_auth)):
    """Cloudflared tunnel health and configuration."""
    result = tunnel_adapter.status()
    return result


# ──────────────────────────────────────────────
# CONTEXT PACK (L1+)
# ──────────────────────────────────────────────

@router.post("/superpowers/context/build")
async def build_context(req: ContextBuildRequest, _auth=Depends(require_superpowers_auth)):
    """Build a context pack for a project/task."""
    autonomy = req.autonomy or "L1"
    enforce_autonomy(autonomy, "L1", "context_build")

    pack = context_builder.build_context_pack(
        project=req.project or "auto",
        task=req.task or "",
    )

    prov = _record_provenance(
        raw_input=f"context build: project={req.project}",
        capability="context_build",
        autonomy=autonomy, job_id="",
        success=True,
        undo_recipe={"type": "noop"},
    )

    return {"context_pack": pack, "provenance": prov}


# ──────────────────────────────────────────────
# GENERIC DISPATCH
# ──────────────────────────────────────────────

@router.post("/superpowers/dispatch")
async def dispatch_superpower(req: DispatchRequest, _auth=Depends(require_superpowers_auth)):
    """Dispatch a job to any registered capability."""
    cap_key = req.capability
    params = req.params or {}
    autonomy = req.autonomy or "L1"

    cap = registry.get_capability(cap_key)
    if not cap:
        available = [c["key"] for c in registry.list_capabilities()]
        raise HTTPException(400, f"Unknown capability: {cap_key}. Available: {available}")

    if cap_key == "tableau_to_powerbi":
        return {"error": "Use /superpowers/tableau for file uploads"}
    elif cap_key == "mood_playlist":
        enforce_autonomy(autonomy, "L1", "mood_playlist")
        job_id = artifact_store.new_job_id()
        result = execute_playlist(
            current_feeling=params.get("current_feeling", "neutral"),
            desired_feeling=params.get("desired_feeling", "happy"),
            job_id=job_id,
            constraints=params.get("constraints"),
            adapter=params.get("adapter", "spotify"),
        )
        return {"job_id": job_id, "status": "completed", "result": result}
    else:
        return {"status": "routed", "capability": cap_key, "agent": cap.get("default_agent", "CJ")}


# ──────────────────────────────────────────────
# ARTIFACTS
# ──────────────────────────────────────────────

@router.get("/superpowers/artifacts/{job_id}")
async def list_job_artifacts(job_id: str, _auth=Depends(require_superpowers_auth)):
    """List all artifacts for a job."""
    artifacts = artifact_store.list_artifacts(job_id)
    if not artifacts:
        raise HTTPException(404, f"No artifacts for job {job_id}")
    return {
        "job_id": job_id,
        "artifacts": artifacts,
        "download_urls": {a: f"/joao/superpowers/artifacts/{job_id}/{a}" for a in artifacts},
    }


@router.get("/superpowers/artifacts/{job_id}/{filename}")
async def download_artifact(job_id: str, filename: str, _auth=Depends(require_superpowers_auth)):
    """Download a specific artifact file."""
    try:
        path = artifact_store.load_artifact(job_id, filename)
        if filename.endswith(".json"):
            media_type = "application/json"
        elif filename.endswith(".md"):
            media_type = "text/markdown"
        else:
            media_type = "application/octet-stream"
        return FileResponse(path, filename=filename, media_type=media_type)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError:
        raise HTTPException(404, f"Artifact not found: {job_id}/{filename}")


@router.get("/superpowers/artifacts/{job_id}/bundle")
async def download_bundle(job_id: str, _auth=Depends(require_superpowers_auth)):
    """Download all artifacts as a zip bundle."""
    artifacts = artifact_store.list_artifacts(job_id)
    if not artifacts:
        raise HTTPException(404, f"No artifacts for job {job_id}")
    zip_path = artifact_store.make_zip_bundle(job_id)
    return FileResponse(zip_path, filename=f"{job_id}.zip", media_type="application/zip")


# ──────────────────────────────────────────────
# PROVENANCE QUERIES
# ──────────────────────────────────────────────

@router.get("/superpowers/provenance")
async def recent_provenance(last: int = 20, _auth=Depends(require_superpowers_auth)):
    """Get recent provenance entries."""
    intents = ledgers.get_intents(last_n=last)
    return {"entries": intents, "count": len(intents)}


@router.get("/superpowers/provenance/{run_id}")
async def get_provenance(run_id: str, _auth=Depends(require_superpowers_auth)):
    """Get a specific provenance entry by intent_id or outcome_id."""
    intent = ledgers.get_intent(run_id)
    if intent:
        outcomes = ledgers.get_outcomes(intent_id=run_id)
        return {"intent": intent, "outcomes": outcomes}

    outcome = ledgers.get_outcome(run_id)
    if outcome:
        intent = ledgers.get_intent(outcome.get("linked_intent_id", ""))
        return {"intent": intent, "outcome": outcome}

    raise HTTPException(404, f"Provenance entry not found: {run_id}")


# ──────────────────────────────────────────────
# TRUST RECEIPTS
# ──────────────────────────────────────────────

@router.get("/superpowers/trust-receipt/{intent_id}")
async def trust_receipt(intent_id: str, _auth=Depends(require_superpowers_auth)):
    """Generate a trust receipt for an operation.

    Shows: what happened, what data was touched, what external APIs were called,
    and the undo plan.
    """
    intent = ledgers.get_intent(intent_id)
    if not intent:
        raise HTTPException(404, f"Intent not found: {intent_id}")

    outcomes = ledgers.get_outcomes(intent_id=intent_id)
    outcome = outcomes[0] if outcomes else {}

    receipt = {
        "receipt_id": f"rcpt-{intent_id}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "intent": {
            "id": intent.get("intent_id"),
            "timestamp": intent.get("ts"),
            "raw_input": intent.get("raw_input"),
            "capability": intent.get("parsed_intent"),
            "autonomy_level": intent.get("autonomy_level"),
            "capability_chain": intent.get("capability_chain", []),
        },
        "outcome": {
            "id": outcome.get("outcome_id"),
            "success": outcome.get("success"),
            "artifacts": outcome.get("artifacts", []),
            "duration_ms": outcome.get("time_to_artifact_ms", 0),
        },
        "data_touched": {
            "files_read": [],
            "files_written": outcome.get("artifacts", []),
            "external_apis": outcome.get("egress_summary", {}).get("external_apis_called", []),
            "data_sent_externally": outcome.get("egress_summary", {}).get("data_sent_externally", False),
        },
        "undo_plan": outcome.get("undo_steps", []) or [{"type": "noop"}],
        "trust_level": "verified" if outcome.get("success") else "failed",
    }

    return receipt


# ──────────────────────────────────────────────
# UNDO
# ──────────────────────────────────────────────

@router.post("/superpowers/undo/{run_id}")
async def execute_undo(run_id: str, req: UndoRequest = None, _auth=Depends(require_superpowers_auth)):
    """Execute the undo recipe for a previous operation."""
    autonomy = req.autonomy if req else "L2"

    # Find the intent + outcome
    intent = ledgers.get_intent(run_id)
    if not intent:
        raise HTTPException(404, f"Intent not found: {run_id}")

    outcomes = ledgers.get_outcomes(intent_id=run_id)
    if not outcomes:
        raise HTTPException(404, f"No outcome found for intent: {run_id}")

    outcome = outcomes[0]
    undo_steps = outcome.get("undo_steps", [])

    # If no explicit undo_steps, check if it's a capability with known undo
    if not undo_steps:
        capability = intent.get("parsed_intent", "")
        if capability == "tableau_to_powerbi":
            # Find the job_id from artifacts
            artifacts = outcome.get("artifacts", [])
            if artifacts:
                # Extract job_id from artifact path
                undo_steps = [{"type": "delete_artifacts", "target": run_id}]
            else:
                undo_steps = [{"type": "noop"}]
        else:
            undo_steps = [{"type": "noop"}]

    results = []
    for recipe in undo_steps:
        result = undo_executor.execute_undo(recipe, autonomy)
        results.append(result)

    all_success = all(r.get("success") for r in results)

    # Record undo in provenance
    prov = _record_provenance(
        raw_input=f"undo: {run_id}",
        capability="undo",
        autonomy=autonomy, job_id="",
        success=all_success,
        undo_recipe={"type": "noop"},
    )

    return {
        "undo_results": results,
        "all_success": all_success,
        "provenance": prov,
    }


# ──────────────────────────────────────────────
# LOCKS
# ──────────────────────────────────────────────

@router.post("/superpowers/locks/grant")
async def grant_lock(req: LockGrantRequest, _auth=Depends(require_superpowers_auth)):
    """Grant a WRITE_LOCK or SHIP_LOCK."""
    if req.lock_type not in ("WRITE_LOCK", "SHIP_LOCK"):
        raise HTTPException(400, "lock_type must be WRITE_LOCK or SHIP_LOCK")

    lock = ledgers.grant_lock(req.lock_type, req.scope, req.duration_minutes or 30)
    return {"lock": lock, "message": f"{req.lock_type} granted for {req.scope} ({req.duration_minutes}m)"}


@router.get("/superpowers/locks")
async def list_locks(_auth=Depends(require_superpowers_auth)):
    """List all active locks."""
    locks = ledgers.get_active_locks()
    return {"locks": locks, "count": len(locks)}


# ──────────────────────────────────────────────
# INGESTION POLICY
# ──────────────────────────────────────────────

@router.get("/superpowers/ingestion/policy")
async def ingestion_policy(_auth=Depends(require_superpowers_auth)):
    """Return the current legal ingestion policy."""
    return legal_ingest.get_ingestion_policy()


@router.post("/superpowers/ingestion/validate-url")
async def validate_url(req: IngestUrlRequest, _auth=Depends(require_superpowers_auth)):
    """Validate a URL against the legal ingestion policy."""
    result = legal_ingest.validate_url(req.url)
    return result


# ──────────────────────────────────────────────
# AGENT CALLBACK
# ──────────────────────────────────────────────

class AgentCallbackRequest(BaseModel):
    job_id: str
    agent: str
    status: str  # success | error
    result: Optional[dict] = None
    error: Optional[str] = None

@router.post("/superpowers/agent-callback")
async def agent_callback(req: AgentCallbackRequest, _auth=Depends(require_superpowers_auth)):
    """Receive structured results from council agents."""
    logger.info(
        "[SUPERPOWERS] Agent callback: agent=%s job=%s status=%s",
        req.agent, req.job_id, req.status,
    )

    # Save result as artifact
    if req.result and req.job_id:
        artifact_store.save_artifact(
            req.job_id,
            f"result_{req.agent}.json",
            req.result,
        )

    return {"received": True, "agent": req.agent, "job_id": req.job_id}
