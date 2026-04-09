"""
Superpowers router -- Tableau-to-PowerBI, MrDP Mood Playlist, Capability Routing.
Mounts under /joao/ prefix (same as existing joao router).
"""

import logging
import traceback
from datetime import datetime, timezone

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional

from capability import artifact_store
from capability import registry
from capability.tableau_to_powerbi import execute as execute_tableau
from capability.mood_playlist import execute as execute_playlist

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/joao", tags=["superpowers"])


# --------------- Models --------------- #

class RouteRequest(BaseModel):
    text: str
    filename: Optional[str] = None

class SuperpowerDispatchRequest(BaseModel):
    job_id: str
    capability: str
    agent: Optional[str] = None
    params: Optional[dict] = None

class PlaylistRequest(BaseModel):
    current_feeling: str
    desired_feeling: str
    constraints: Optional[dict] = None
    adapter: Optional[str] = "spotify"


# --------------- Route (intent classification) --------------- #

@router.post("/superpowers/route")
async def route_intent(req: RouteRequest):
    """Classify intent and return routing decision."""
    routing = registry.route(req.text, req.filename)
    return routing


@router.get("/superpowers/capabilities")
async def list_capabilities():
    """List all registered superpowers."""
    return {"capabilities": registry.list_capabilities()}


# --------------- Tableau-to-PowerBI --------------- #

@router.post("/superpowers/tableau")
async def tableau_upload(
    file: UploadFile = File(...),
    intent: str = Form("migrate to power bi"),
):
    """Upload a TWB/TWBX and get full migration artifact bundle.

    Returns: tableau_spec, model_mapping, dax_translations,
    migration_plan, pbix_build_instructions, pbip_config.
    """
    job_id = artifact_store.new_job_id()
    content = await file.read()

    # Validate file type
    fname = file.filename or ""
    if not fname.lower().endswith((".twb", ".twbx")):
        raise HTTPException(400, "File must be .twb or .twbx")

    saved_path = artifact_store.save_upload(job_id, fname, content)
    logger.info(f"[SUPERPOWERS] Tableau upload: {fname} -> job {job_id}")

    try:
        result = execute_tableau(saved_path, job_id)
    except Exception as e:
        logger.error(f"[SUPERPOWERS] Tableau execution failed: {e}\n{traceback.format_exc()}")
        raise HTTPException(500, f"Migration failed: {str(e)}")

    if result["status"] == "error":
        raise HTTPException(500, f"Migration errors: {'; '.join(result['errors'])}")

    return {
        "job_id": job_id,
        "status": "success",
        "summary": result["summary"],
        "artifacts": result["artifacts"],
        "errors": result.get("errors", []),
        "download_base": f"/joao/superpowers/artifacts/{job_id}",
    }


# --------------- MrDP Mood Playlist --------------- #

@router.post("/superpowers/playlist")
async def playlist(req: PlaylistRequest):
    """Generate a mood-transition playlist with MrDP voice.

    Input: current feeling + desired feeling + optional constraints.
    Output: curated track list + rationale + streaming links.
    """
    job_id = artifact_store.new_job_id()
    logger.info(f"[SUPERPOWERS] Playlist: {req.current_feeling} -> {req.desired_feeling}")

    try:
        result = execute_playlist(
            current_feeling=req.current_feeling,
            desired_feeling=req.desired_feeling,
            job_id=job_id,
            constraints=req.constraints,
            adapter=req.adapter or "spotify",
        )
    except Exception as e:
        logger.error(f"[SUPERPOWERS] Playlist failed: {e}")
        raise HTTPException(500, f"Playlist generation failed: {str(e)}")

    return {
        "job_id": job_id,
        **result,
    }


# --------------- Dispatch (superpowers) --------------- #

@router.post("/superpowers/dispatch")
async def dispatch_superpower(req: SuperpowerDispatchRequest):
    """Dispatch a job to a superpower capability."""
    job_id = req.job_id
    cap_key = req.capability
    params = req.params or {}

    if cap_key == "tableau_to_powerbi":
        context = artifact_store.load_context_pack(job_id)
        file_path = context.get("file_path", "")
        if not file_path:
            raise HTTPException(400, "No file uploaded for this job. Use /joao/superpowers/tableau first.")
        try:
            result = execute_tableau(file_path, job_id)
            return {"job_id": job_id, "status": "completed", "result": result}
        except Exception as e:
            raise HTTPException(500, f"Execution failed: {str(e)}")

    elif cap_key == "mood_playlist":
        try:
            result = execute_playlist(
                current_feeling=params.get("current_feeling", "neutral"),
                desired_feeling=params.get("desired_feeling", "happy"),
                job_id=job_id,
                constraints=params.get("constraints"),
                adapter=params.get("adapter", "spotify"),
            )
            return {"job_id": job_id, "status": "completed", "result": result}
        except Exception as e:
            raise HTTPException(500, f"Execution failed: {str(e)}")

    else:
        raise HTTPException(400, f"Unknown superpower: {cap_key}. Available: tableau_to_powerbi, mood_playlist")


# --------------- Artifacts --------------- #

@router.get("/superpowers/artifacts/{job_id}")
async def list_job_artifacts(job_id: str):
    """List all artifacts for a superpower job."""
    artifacts = artifact_store.list_artifacts(job_id)
    if not artifacts:
        raise HTTPException(404, f"No artifacts for job {job_id}")
    return {
        "job_id": job_id,
        "artifacts": artifacts,
        "download_urls": {
            a: f"/joao/superpowers/artifacts/{job_id}/{a}" for a in artifacts
        },
    }


@router.get("/superpowers/artifacts/{job_id}/{filename}")
async def download_artifact(job_id: str, filename: str):
    """Download a specific artifact file."""
    try:
        path = artifact_store.load_artifact(job_id, filename)
        # Determine media type
        if filename.endswith(".json"):
            media_type = "application/json"
        elif filename.endswith(".md"):
            media_type = "text/markdown"
        else:
            media_type = "application/octet-stream"
        return FileResponse(path, filename=filename, media_type=media_type)
    except FileNotFoundError:
        raise HTTPException(404, f"Artifact not found: {job_id}/{filename}")


@router.get("/superpowers/artifacts/{job_id}/bundle")
async def download_bundle(job_id: str):
    """Download all artifacts as a zip bundle."""
    artifacts = artifact_store.list_artifacts(job_id)
    if not artifacts:
        raise HTTPException(404, f"No artifacts for job {job_id}")
    zip_path = artifact_store.make_zip_bundle(job_id)
    return FileResponse(zip_path, filename=f"{job_id}.zip", media_type="application/zip")
