"""7 REST endpoints + shared _content_pipeline."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException

from middleware.auth import require_dispatch_auth, validate_agent_name, validate_command_safety

from models.schemas import (
    AIResult,
    AudioRequest,
    ContentResponse,
    DispatchRequest,
    DispatchResponse,
    HealthResponse,
    IdeaVaultRecord,
    MeetingRequest,
    SessionLogRecord,
    StatusChecks,
    StatusResponse,
    TextRequest,
    VisionRequest,
)
from services import ai_processor, dispatch, supabase_client, telegram

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/joao", tags=["joao"])

_start_time: float = time.time()


# ── Shared Pipeline ─────────────────────────────────────────────────────────

async def _content_pipeline(
    source: str,
    endpoint: str,
    raw_content: str,
    ai_result: AIResult,
    metadata: dict | None = None,
) -> ContentResponse:
    """AI-process → insert idea_vault → insert session_log → Telegram notify."""
    t0 = time.time()
    metadata = metadata or {}

    # Insert into idea_vault
    vault_record = IdeaVaultRecord(
        source=source,
        title=ai_result.title,
        content=raw_content,
        summary=ai_result.summary,
        tags=ai_result.tags,
        metadata={**metadata, "key_points": ai_result.key_points},
    )
    vault_row = await supabase_client.insert_idea_vault(vault_record)

    duration_ms = int((time.time() - t0) * 1000)

    # Insert session_log
    log_record = SessionLogRecord(
        endpoint=endpoint,
        action=f"process_{source}",
        input_summary=raw_content[:200],
        output_summary=ai_result.summary[:200],
        status="ok",
        duration_ms=duration_ms,
        metadata=metadata,
    )
    await supabase_client.insert_session_log(log_record)

    # Telegram notification (fire-and-forget, never fails the request)
    notify_msg = f"*{ai_result.title}*\n{ai_result.summary}\nTags: {', '.join(ai_result.tags)}"
    await telegram.send_notification(notify_msg)

    return ContentResponse(
        source=source,
        title=ai_result.title,
        summary=ai_result.summary,
        tags=ai_result.tags,
        idea_vault_id=vault_row.get("id"),
    )


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse()


@router.get("/status", response_model=StatusResponse)
async def status():
    import asyncio
    import os

    uptime = time.time() - _start_time

    supabase_check, (ssh_check, tmux_check) = await asyncio.gather(
        supabase_client.health_check(),
        dispatch.health_check(),
    )

    all_ok = supabase_check.ok and ssh_check.ok and tmux_check.ok
    any_ok = supabase_check.ok or ssh_check.ok
    overall = "healthy" if all_ok else ("degraded" if any_ok else "down")

    try:
        recent = await supabase_client.query_recent_activity(limit=5)
    except Exception:
        recent = []

    return StatusResponse(
        status=overall,
        version=os.environ.get("RAILWAY_GIT_COMMIT_SHA", os.environ.get("GIT_SHA")),
        uptime_seconds=round(uptime, 2),
        checks=StatusChecks(
            supabase=supabase_check,
            ssh=ssh_check,
            tmux=tmux_check,
        ),
        recent_activity=recent,
    )


@router.post("/dispatch", response_model=DispatchResponse)
async def dispatch_endpoint(
    req: DispatchRequest,
    request_id: str = Depends(require_dispatch_auth),
):
    try:
        validate_agent_name(req.session_name)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        validate_command_safety(req.command)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    t0 = time.time()
    result = await dispatch.dispatch_command(
        session_name=req.session_name, command=req.command, wait=req.wait
    )
    duration_ms = int((time.time() - t0) * 1000)

    await supabase_client.insert_session_log(
        SessionLogRecord(
            endpoint="/joao/dispatch",
            action="dispatch",
            input_summary=f"{req.session_name}: {req.command[:100]}",
            output_summary=(result.get("output") or "")[:200],
            status=result["status"],
            duration_ms=duration_ms,
            metadata={"request_id": request_id},
        )
    )

    from models.schemas import AgentOutputRecord
    await supabase_client.insert_agent_output(
        AgentOutputRecord(
            session_name=req.session_name,
            command=req.command,
            output=result.get("output") or "",
            status=result["status"],
            metadata={"request_id": request_id},
        )
    )

    return DispatchResponse(request_id=request_id, **result)


@router.post("/audio", response_model=ContentResponse)
async def audio(req: AudioRequest):
    ai_result = await ai_processor.process_audio(req.audio_url, req.context)
    return await _content_pipeline(
        source="audio",
        endpoint="/joao/audio",
        raw_content=req.audio_url,
        ai_result=ai_result,
        metadata={"context": req.context},
    )


@router.post("/meeting", response_model=ContentResponse)
async def meeting(req: MeetingRequest):
    ai_result = await ai_processor.process_meeting(
        req.transcript, req.participants, req.context
    )
    return await _content_pipeline(
        source="meeting",
        endpoint="/joao/meeting",
        raw_content=req.transcript,
        ai_result=ai_result,
        metadata={"participants": req.participants, "context": req.context},
    )


@router.post("/vision", response_model=ContentResponse)
async def vision(req: VisionRequest):
    ai_result = await ai_processor.process_vision(req.image_url, req.prompt)
    return await _content_pipeline(
        source="vision",
        endpoint="/joao/vision",
        raw_content=req.image_url,
        ai_result=ai_result,
        metadata={"prompt": req.prompt},
    )


@router.post("/text", response_model=ContentResponse)
async def text(req: TextRequest):
    ai_result = await ai_processor.process_text(req.text, req.context)
    return await _content_pipeline(
        source="text",
        endpoint="/joao/text",
        raw_content=req.text,
        ai_result=ai_result,
        metadata={"context": req.context},
    )
