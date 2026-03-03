"""7 REST endpoints + shared _content_pipeline."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from middleware.auth import require_dispatch_auth, validate_agent_name, validate_command_safety

from models.schemas import (
    AIResult,
    AudioRequest,
    ChatRequest,
    ContentResponse,
    ContextResponse,
    CouncilDispatchRequest,
    CouncilDispatchResponse,
    DispatchLogRecord,
    DispatchRequest,
    DispatchResponse,
    HealthResponse,
    IdeaVaultRecord,
    LogEntry,
    LogResponse,
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


# ── Council Dispatch (via Cloudflare tunnel) ──────────────────────────────

@router.post("/council/dispatch", response_model=CouncilDispatchResponse)
async def council_dispatch(req: CouncilDispatchRequest):
    """Dispatch a task to a Council agent via the local HTTP listener."""
    from datetime import datetime, timezone

    try:
        result = await dispatch.dispatch_to_agent(
            agent=req.agent,
            task=req.task,
            priority=req.priority,
            context=req.context,
            project=req.project,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Council dispatch failed")
        raise HTTPException(status_code=503, detail=f"Dispatch failed: {e}")

    # Log to Supabase
    try:
        await supabase_client.insert_dispatch_log(
            DispatchLogRecord(
                agent=req.agent,
                task=req.task,
                priority=req.priority,
                project=req.project,
                status=result.get("status", "unknown"),
                session=result.get("session"),
            )
        )
    except Exception:
        logger.warning("Failed to log dispatch to Supabase", exc_info=True)

    return CouncilDispatchResponse(
        status="dispatched",
        agent=req.agent,
        task_preview=req.task[:100],
        timestamp=datetime.now(timezone.utc).isoformat(),
        server_response=result,
    )


@router.get("/council/agents")
async def council_agents():
    """Get agent status from the local server."""
    try:
        return await dispatch.get_agents()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/council/sessions")
async def council_sessions():
    """Get tmux session outputs from the local server."""
    try:
        return await dispatch.get_sessions()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/council/session/{agent}")
async def council_session(agent: str):
    """Get a specific agent's tmux session output."""
    try:
        return await dispatch.get_session(agent)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/council/health")
async def council_health():
    """Check if the local dispatch listener is reachable via tunnel."""
    return await dispatch.tunnel_health_check()


# ── Context & Log Endpoints (joao-interface memory) ───────────────────────

_MEMORY_DIR = Path("/home/zamoritacr/joao-interface/memory")
_CONTEXT_FILE = _MEMORY_DIR / "JOAO_MASTER_CONTEXT.md"
_SESSION_LOG_FILE = _MEMORY_DIR / "JOAO_SESSION_LOG.md"


@router.get("/context", response_model=ContextResponse)
async def get_context():
    """Read both memory files and return their contents."""
    context_text = ""
    session_log_text = ""

    if _CONTEXT_FILE.exists():
        context_text = _CONTEXT_FILE.read_text(encoding="utf-8")
    if _SESSION_LOG_FILE.exists():
        session_log_text = _SESSION_LOG_FILE.read_text(encoding="utf-8")

    last_mod = max(
        _CONTEXT_FILE.stat().st_mtime if _CONTEXT_FILE.exists() else 0,
        _SESSION_LOG_FILE.stat().st_mtime if _SESSION_LOG_FILE.exists() else 0,
    )

    from datetime import datetime, timezone

    last_updated = datetime.fromtimestamp(last_mod, tz=timezone.utc).isoformat() if last_mod else "never"

    return ContextResponse(
        context=context_text,
        session_log=session_log_text,
        last_updated=last_updated,
    )


@router.post("/log", response_model=LogResponse)
async def append_log(entry: LogEntry):
    """Append a log entry to the session log file."""
    from datetime import datetime, timezone

    ts = entry.timestamp or datetime.now(timezone.utc).isoformat()

    line = f"\n**[{ts}] {entry.role}:** {entry.content}\n"

    with open(_SESSION_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)

    return LogResponse(status="logged")


# ── Chat Proxy (streams Claude API, keeps key server-side) ─────────────

def _append_log_sync(role: str, content: str) -> None:
    """Append to session log (sync helper for use inside generator)."""
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).isoformat()
    line = f"\n**[{ts}] {role}:** {content}\n"
    with open(_SESSION_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)


@router.post("/chat")
async def chat_proxy(req: ChatRequest):
    """Proxy chat to Claude API with persistent memory context. Streams SSE."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    # Build system prompt from memory files
    context_text = ""
    session_log_text = ""
    if _CONTEXT_FILE.exists():
        context_text = _CONTEXT_FILE.read_text(encoding="utf-8")
    if _SESSION_LOG_FILE.exists():
        session_log_text = _SESSION_LOG_FILE.read_text(encoding="utf-8")

    system_prompt = context_text
    if session_log_text:
        system_prompt += f"\n\n---\n\n## Session Log\n\n{session_log_text}"

    # Log the latest user message
    if req.messages:
        last_msg = req.messages[-1]
        if last_msg.role == "user":
            _append_log_sync("user", last_msg.content)

    # Build messages for the API
    api_messages = [{"role": m.role, "content": m.content} for m in req.messages]

    client = anthropic.Anthropic(api_key=api_key)

    async def event_stream():
        full_response = ""
        try:
            with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=8096,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=api_messages,
            ) as stream:
                for text in stream.text_stream:
                    full_response += text
                    yield f"data: {text}\n\n"
        except anthropic.APIError as e:
            logger.error("Claude API error: %s", e)
            yield f"data: [ERROR] {e.message}\n\n"
        except Exception as e:
            logger.exception("Chat stream error")
            yield f"data: [ERROR] {e}\n\n"

        # Log the full assistant response
        if full_response:
            _append_log_sync("assistant", full_response)

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
