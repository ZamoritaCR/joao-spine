"""8 REST endpoints + shared _content_pipeline + content intelligence."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from middleware.auth import require_api_key, require_dispatch_auth, validate_agent_name, validate_command_safety

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
from services import ai_processor, content_intelligence, dispatch, supabase_client, telegram
from services.llm_router import (
    health_check as llm_health_check,
    complete as llm_complete,
    summarize as llm_summarize,
    generate_code as llm_generate_code,
    council_task as llm_council_task,
    stream_complete as llm_stream_complete,
    OLLAMA_MODELS, OPENROUTER_MODELS, USE_OPENROUTER, resolve_model
)

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


@router.get("/llm/health")
async def llm_health():
    return await llm_health_check()


@router.get("/llm/models")
async def llm_models():
    return {"provider": "openrouter" if USE_OPENROUTER else "ollama", "active_models": OPENROUTER_MODELS if USE_OPENROUTER else OLLAMA_MODELS}


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


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_AUDIO_DIR = _PROJECT_ROOT / "audio"


@router.post("/audio", response_model=ContentResponse)
async def audio(req: AudioRequest):
    ai_result = await ai_processor.process_audio(req.audio_url, req.context)

    # Write transcript to audio dir for context watcher pickup
    try:
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        transcript_file = _AUDIO_DIR / f"transcript_{ts}.txt"
        _AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        transcript_file.write_text(
            f"TITLE: {ai_result.title}\n"
            f"SUMMARY: {ai_result.summary}\n"
            f"KEY POINTS: {', '.join(ai_result.key_points)}\n"
            f"TAGS: {', '.join(ai_result.tags)}\n"
            f"SOURCE: {req.audio_url}\n",
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("Failed to write audio transcript feed: %s", e)

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

    # Build log -- AI workforce activity tracking
    try:
        _ollama_agents = {"GEMMA", "LEX", "NOVA", "IRIS", "VOLT", "FLUX", "SAGE", "APEX"}
        sb = supabase_client.get_client()
        sb.table("build_log").insert({
            "agent": req.agent,
            "task_summary": req.task[:120],
            "model_used": "ollama" if req.agent.upper() in _ollama_agents else "claude",
            "tokens_used": 0,
            "qa_result": "PENDING",
            "dispatch_id": result.get("session", ""),
        }).execute()
    except Exception as e:
        logger.warning("build_log write failed: %s", e)

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


# ── Build Completion Webhook ───────────────────────────────────────────────

_VALID_BUILDS = {"joao_mcp", "taop_mcp"}
_build_status: dict[str, bool] = {b: False for b in _VALID_BUILDS}

_COMPLETION_MESSAGE = (
    "JOAO MCP: DONE. TAOP MCP: DONE. Both are code-complete. Awaiting your approval to deploy."
)


class BuildCompleteRequest(BaseModel):
    build: str  # "joao_mcp" or "taop_mcp"
    agent: str = "BYTE"


@router.post("/council/build-complete")
async def build_complete(req: BuildCompleteRequest):
    """
    BYTE calls this when it finishes a MCP build.
    When both joao_mcp and taop_mcp are marked done, fire a Telegram notification.
    """
    build_key = req.build.lower().strip()

    if build_key not in _VALID_BUILDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown build '{req.build}'. Valid values: {sorted(_VALID_BUILDS)}",
        )

    _build_status[build_key] = True
    logger.info("Build marked complete: %s (agent=%s)", build_key, req.agent)

    status_snapshot = dict(_build_status)
    all_done = all(status_snapshot.values())

    if all_done:
        logger.info("All MCP builds complete — sending Telegram notification")
        await telegram.send_notification(_COMPLETION_MESSAGE)

    return {
        "acknowledged": build_key,
        "status": status_snapshot,
        "notification_sent": all_done,
    }


@router.get("/council/build-status")
async def build_status():
    """Current completion state for MCP builds."""
    return {"status": dict(_build_status), "all_done": all(_build_status.values())}


@router.delete("/council/build-status")
async def reset_build_status():
    """Reset build completion flags (use when starting a new build cycle)."""
    for key in _build_status:
        _build_status[key] = False
    return {"reset": True, "status": dict(_build_status)}


# ── Context & Log Endpoints (joao-interface memory) ───────────────────────

_MEMORY_DIR = Path(os.environ.get("JOAO_MEMORY_DIR", str(_PROJECT_ROOT.parent / "joao-interface" / "memory")))
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

_SPINE_SESSION_LOG = _PROJECT_ROOT / "JOAO_SESSION_LOG.md"


def _append_log_sync(role: str, content: str) -> None:
    """Append to session log (sync helper for use inside generator)."""
    try:
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc).isoformat()
        line = f"\n**[{ts}] {role}:** {content}\n"
        _SESSION_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_SESSION_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.warning("Failed to append to session log: %s", e)


def _append_chat_feed(user_message: str, response_text: str) -> None:
    """Append chat exchange to spine session log for context watcher pickup."""
    try:
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = f"\n## CHAT -- [{ts}]\nUSER: {user_message}\nRESPONSE: {response_text}\n"
        with open(_SPINE_SESSION_LOG, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        logger.warning("Failed to append chat feed: %s", e)


def _auto_grow_context(user_msg: str, response: str) -> None:
    """Auto-append conversation exchange to JOAO_SESSION_LOG.md for persistent memory.

    Also rotates the session log if it exceeds 2MB to prevent unbounded growth.
    """
    try:
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Append to session log (the running history)
        _SESSION_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        entry = f"\n---\n### [{ts}]\n**User:** {user_msg[:500]}\n**JOAO:** {response[:1000]}\n"
        with open(_SESSION_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(entry)

        # Rotate if over 2MB
        if _SESSION_LOG_FILE.exists() and _SESSION_LOG_FILE.stat().st_size > 2_000_000:
            content = _SESSION_LOG_FILE.read_text(encoding="utf-8")
            # Keep last 1MB
            truncated = content[-1_000_000:]
            header = "# JOAO Session Log (auto-rotated)\n\n[Previous entries truncated]\n\n"
            _SESSION_LOG_FILE.write_text(header + truncated, encoding="utf-8")
            logger.info("Session log rotated (was >2MB)")

    except Exception as e:
        logger.warning("Failed to auto-grow context: %s", e)



class SessionSyncRequest(BaseModel):
    session_id: str
    name: str = ""
    messages: list[dict]
    source: str = "joao-app"
    model: str = ""
    mode: str = "joao"


def _session_messages_payload(row: dict | None) -> dict:
    row = row or {}
    payload = row.get("messages") or {}
    if isinstance(payload, list):
        payload = {"messages": payload}
    if not isinstance(payload, dict):
        payload = {"messages": []}
    payload.setdefault("messages", [])
    payload.setdefault("name", "")
    payload.setdefault("source", "unknown")
    payload.setdefault("metadata", {})
    return payload


async def _persist_chat_session(session_id: str, messages: list[dict], assistant_text: str, source: str, session_name: str = "", model: str = "", mode: str = "joao") -> None:
    if not session_id:
        return
    full_messages = list(messages)
    if assistant_text:
        full_messages.append({"role": "assistant", "content": assistant_text})
    user_text = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            user_text = content if isinstance(content, str) else str(content)
            break
    summary = assistant_text[:200] if assistant_text else user_text[:200]
    await supabase_client.upsert_joao_session(session_id=session_id, messages=full_messages, name=session_name, summary=summary, source=source, metadata={"model": model, "mode": mode})
    await supabase_client.insert_joao_memory(source=f"{source}:chat", content=f"User: {user_text[:1000]}\n\nAssistant: {assistant_text[:2000]}", summary=summary, tags=[source, "chat", mode or "joao"])


@router.get('/sessions')
async def list_sessions(limit: int = 50):
    rows = await supabase_client.list_joao_sessions(limit=limit)
    items = []
    for row in rows:
        payload = _session_messages_payload(row)
        items.append({'id': row.get('id'), 'name': payload.get('name') or row.get('summary') or 'New conversation', 'source': payload.get('source') or 'unknown', 'messages': payload.get('messages', []), 'created_at': row.get('created_at'), 'summary': row.get('summary', '')})
    return {'sessions': items}


@router.get('/session/{session_id}')
async def get_session(session_id: str):
    row = await supabase_client.get_joao_session(session_id)
    if not row:
        raise HTTPException(status_code=404, detail='Session not found')
    payload = _session_messages_payload(row)
    return {'id': row.get('id'), 'name': payload.get('name', ''), 'source': payload.get('source', 'unknown'), 'messages': payload.get('messages', []), 'metadata': payload.get('metadata', {}), 'summary': row.get('summary', ''), 'created_at': row.get('created_at')}


@router.post('/session')
async def sync_session(req: SessionSyncRequest):
    row = await supabase_client.upsert_joao_session(session_id=req.session_id, messages=req.messages, name=req.name, summary=(req.messages[-1].get('content', '') if req.messages else '')[:200], source=req.source, metadata={'model': req.model, 'mode': req.mode})
    return {'status': 'ok', 'id': row.get('id', req.session_id)}

# ── Council Tools for Chat ──────────────────────────────────────────────

COUNCIL_TOOLS = [
    {
        "name": "escalate_to_opus",
        "description": (
            "Escalate a question or task to Claude Opus for deep analysis. "
            "Use ONLY for: complex architecture decisions, QA/code review of critical systems, "
            "debugging hard problems, strategic planning, or when Johan explicitly asks for 'deep thinking' or 'opus'. "
            "Do NOT use for simple questions, status checks, or routine tasks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The full question or task for Opus to analyze deeply. Include all relevant context.",
                },
                "context": {
                    "type": "string",
                    "description": "Optional additional context (code snippets, error logs, etc.)",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "council_status",
        "description": "Check which Council agents are online. Call when Johan asks 'who's online', 'check the council', 'are agents running', etc.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "council_dispatch",
        "description": "Send a task to a specific Council agent. Call when Johan says 'tell BYTE to...', 'have SOFIA build...', 'dispatch ARIA to...', etc. Agents: BYTE (engineering), ARIA (architecture), CJ (product), SOFIA (UX/UI), DEX (support), GEMMA (research).",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": (
                        "Agent name. ARIA=architect, BYTE=full-stack, CJ=product, SOFIA=UX/UI, "
                        "DEX=infrastructure, GEMMA=research, MAX=multi-LLM, LEX=legal, NOVA=marketing, "
                        "SAGE=strategy, FLUX=rapid-prototyping, CORE=deep-research, APEX=data-processing, "
                        "IRIS=integrations, VOLT=CI/CD-testing"
                    ),
                    "enum": [
                        "ARIA", "BYTE", "CJ", "SOFIA", "DEX", "GEMMA",
                        "MAX", "LEX", "NOVA", "SCOUT",
                        "SAGE", "FLUX", "CORE", "APEX", "IRIS", "VOLT",
                    ],
                },
                "task": {
                    "type": "string",
                    "description": "Detailed task description for the agent",
                },
                "priority": {
                    "type": "string",
                    "description": "Priority level",
                    "enum": ["normal", "urgent", "critical"],
                    "default": "normal",
                },
                "project": {
                    "type": "string",
                    "description": "Optional project name",
                },
            },
            "required": ["agent", "task"],
        },
    },
    {
        "name": "council_session_output",
        "description": "Check what an agent is currently doing. Call when Johan asks 'how's BYTE doing', 'check on SOFIA', 'what's the progress', etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent name to check",
                    "enum": [
                        "ARIA", "BYTE", "CJ", "SOFIA", "DEX", "GEMMA",
                        "MAX", "LEX", "NOVA", "SCOUT",
                        "SAGE", "FLUX", "CORE", "APEX", "IRIS", "VOLT",
                    ],
                },
            },
            "required": ["agent"],
        },
    },
    {
        "name": "qa_review",
        "description": (
            "Check QA review status for an agent's code, or override a QA decision. "
            "Call when Johan asks 'how did BYTE's code score?', 'check QA status', "
            "'deploy it anyway', 'reject that code', etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dispatch_id": {
                    "type": "string",
                    "description": "Dispatch ID to check or override. If unknown, use 'latest'.",
                },
                "action": {
                    "type": "string",
                    "description": "Action: 'status' to check, 'deploy' to force deploy, 'reject' to reject",
                    "enum": ["status", "deploy", "reject"],
                    "default": "status",
                },
            },
            "required": ["dispatch_id"],
        },
    },
    # ── File System & Server Tools ─────────────────────────────────────────
    {
        "name": "read_file",
        "description": (
            "Read a file from the server. Use when Johan asks to check a file, config, log, "
            "script, or any content on the ROG server. Supports text files of any kind."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file (e.g. /home/zamoritacr/joao-spine/main.py)",
                },
                "tail": {
                    "type": "integer",
                    "description": "Only return the last N lines (useful for logs). 0 = full file.",
                    "default": 0,
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write or overwrite a file on the server. Use when Johan asks to create, update, "
            "or fix a config, script, or any text file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file to write",
                },
                "content": {
                    "type": "string",
                    "description": "Full file content to write",
                },
                "append": {
                    "type": "boolean",
                    "description": "If true, append instead of overwrite",
                    "default": False,
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_directory",
        "description": (
            "List files and directories. Use when Johan asks 'what files are in...', "
            "'show me the project structure', 'list the logs', etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the directory",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "If true, list recursively (max 500 entries)",
                    "default": False,
                },
                "pattern": {
                    "type": "string",
                    "description": "Optional glob pattern filter (e.g. '*.py', '*.log')",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_files",
        "description": (
            "Search file contents using grep/regex. Use when Johan asks to find code, "
            "search for a string, locate where something is defined, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Search pattern (regex supported)",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: /home/zamoritacr)",
                    "default": "/home/zamoritacr",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Glob to filter files (e.g. '*.py', '*.sh')",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max results to return",
                    "default": 30,
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Run a shell command on the server. Use for system checks, service restarts, "
            "process inspection, git operations, package management, or any server task. "
            "Use when Johan asks to restart something, check a service, run a script, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute (runs as zamoritacr user)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 30, max 120)",
                    "default": 30,
                },
            },
            "required": ["command"],
        },
    },
    # ── Dr. Data Tools ─────────────────────────────────────────────────────
    {
        "name": "drdata_analyze",
        "description": (
            "Analyze a data file using Dr. Data's AI engines. Upload a CSV/Excel file "
            "and get a full data profile with quality scores, semantic types, and insights. "
            "Use when Johan asks to 'analyze this data', 'profile this file', 'check data quality', etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the data file (CSV, XLSX, Parquet, JSON, etc.)",
                },
                "question": {
                    "type": "string",
                    "description": "Optional question about the data (e.g. 'what are the top trends?')",
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "drdata_quality_scan",
        "description": (
            "Run a full DAMA-DMBOK data quality scan on a file. Returns scores for 6 dimensions: "
            "completeness, accuracy, consistency, timeliness, uniqueness, validity. "
            "Use when Johan asks 'check quality', 'run DQ scan', 'is this data clean?', etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the data file to scan",
                },
                "min_score": {
                    "type": "integer",
                    "description": "Minimum quality score to pass (default 80)",
                    "default": 80,
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "drdata_build_dashboard",
        "description": (
            "Build an interactive HTML dashboard or Power BI project from a data file. "
            "Use when Johan asks 'build a dashboard', 'create a report', 'make a Power BI from this', etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the data file",
                },
                "request": {
                    "type": "string",
                    "description": "What kind of dashboard to build (e.g. 'sales overview with KPIs and trends')",
                },
                "format": {
                    "type": "string",
                    "description": "Output format: 'html' for interactive dashboard, 'powerbi' for .pbip project",
                    "enum": ["html", "powerbi"],
                    "default": "html",
                },
            },
            "required": ["file_path", "request"],
        },
    },
    {
        "name": "drdata_chat",
        "description": (
            "Ask Dr. Data a question about loaded data. Dr. Data can analyze, explain, "
            "build charts, find patterns, and generate reports conversationally. "
            "Use when Johan asks data questions like 'what are the outliers?', "
            "'explain the correlation between X and Y', 'summarize the key findings', etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Question or request for Dr. Data",
                },
                "file_path": {
                    "type": "string",
                    "description": "Optional data file to load first (if not already loaded)",
                },
            },
            "required": ["message"],
        },
    },
    # ── FocusFlow Tools ────────────────────────────────────────────────────
    {
        "name": "focusflow_process_url",
        "description": (
            "Process a YouTube or lecture URL through FocusFlow -- transcribe and summarize "
            "for ADHD-friendly reading. Use when Johan says 'summarize this video', "
            "'transcribe this lecture', 'focusflow this URL', etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "YouTube, Udemy, or video URL to process",
                },
                "age_group": {
                    "type": "string",
                    "description": "Target audience: child, teen, or adult",
                    "enum": ["child", "teen", "adult"],
                    "default": "adult",
                },
                "class_name": {
                    "type": "string",
                    "description": "Name/topic of the lecture (e.g. 'Machine Learning 101')",
                    "default": "Lecture",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "focusflow_process_file",
        "description": (
            "Process an audio/video file through FocusFlow -- transcribe and summarize. "
            "Use when Johan says 'summarize this recording', 'transcribe this audio file', etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to audio/video file (MP3, MP4, WAV, M4A, OGG, WEBM, FLAC, AAC)",
                },
                "age_group": {
                    "type": "string",
                    "description": "Target audience: child, teen, or adult",
                    "enum": ["child", "teen", "adult"],
                    "default": "adult",
                },
                "class_name": {
                    "type": "string",
                    "description": "Name/topic of the lecture",
                    "default": "Lecture",
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "focusflow_status",
        "description": (
            "Check the status of a FocusFlow processing job. "
            "Use after submitting a URL or file to check if it's done."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Job ID returned from focusflow_process_url or focusflow_process_file",
                },
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "focusflow_download",
        "description": (
            "Download a FocusFlow summary in a specific format. "
            "Use when Johan asks for 'the PDF', 'give me the slides', etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID from a completed FocusFlow job",
                },
                "format": {
                    "type": "string",
                    "description": "Download format",
                    "enum": ["txt", "html", "pdf", "docx", "pptx", "xlsx"],
                    "default": "pdf",
                },
            },
            "required": ["session_id"],
        },
    },
]


async def _execute_council_tool(tool_name: str, tool_input: dict) -> str:
    """Execute a council tool and return the result as a string."""
    import anthropic as _anthropic
    import httpx

    # Handle Opus escalation separately — no tunnel needed
    if tool_name == "escalate_to_opus":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return "ERROR: ANTHROPIC_API_KEY not configured"
        try:
            client = _anthropic.AsyncAnthropic(api_key=api_key)
            opus_prompt = tool_input.get("prompt", "")
            extra_context = tool_input.get("context", "")
            if extra_context:
                opus_prompt += f"\n\nAdditional context:\n{extra_context}"
            response = await client.messages.create(
                model="claude-opus-4-6",
                max_tokens=4096,
                messages=[{"role": "user", "content": opus_prompt}],
            )
            opus_text = ""
            for block in response.content:
                if block.type == "text":
                    opus_text += block.text
            return f"[OPUS ANALYSIS]\n\n{opus_text}"
        except Exception as e:
            logger.error("Opus escalation failed: %s", e)
            return f"ERROR: Opus escalation failed: {e}"

    dispatch_url, dispatch_secret = dispatch._tunnel_config()
    if not dispatch_url:
        return "ERROR: Council dispatch not configured (JOAO_LOCAL_DISPATCH_URL missing)"

    headers = {"Authorization": f"Bearer {dispatch_secret}"} if dispatch_secret else {}

    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            if tool_name == "council_status":
                resp = await http.get(f"{dispatch_url}/agents", headers=headers)
                resp.raise_for_status()
                data = resp.json()
                agents = data.get("agents", {})
                lines = []
                for name, info in sorted(agents.items()):
                    pool = info.get("pool", "unknown")
                    claude = info.get("claude_running", False)
                    active = info.get("active", False)
                    if claude:
                        status = "ALIVE (Claude running)"
                    elif active:
                        status = "IDLE (tmux up, no Claude)"
                    elif pool == "on-demand":
                        status = "STANDBY (on-demand, launches when dispatched)"
                    elif pool == "service":
                        status = "SERVICE (systemd)"
                    else:
                        status = "OFFLINE"
                    lines.append(f"  {name}: {status} [{pool}]")
                return "Council Agent Status:\n" + "\n".join(lines)

            elif tool_name == "council_dispatch":
                agent = tool_input.get("agent", "")
                task = tool_input.get("task", "")
                priority = tool_input.get("priority", "normal")
                project = tool_input.get("project")
                payload = {
                    "agent": agent,
                    "task": task,
                    "priority": priority,
                    "project": project,
                    "lane": "interactive",
                }
                resp = await http.post(
                    f"{dispatch_url}/dispatch",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                result = resp.json()
                return f"Dispatched to {agent}: {result.get('message', 'sent')}"

            elif tool_name == "council_session_output":
                agent = tool_input.get("agent", "")
                resp = await http.get(
                    f"{dispatch_url}/session/{agent}",
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                output = data.get("output", "No output available")
                # Truncate to last 1200 chars to keep tool results manageable
                # (prevents Claude API timeout when multiple agents are queried)
                if len(output) > 3000:
                    output = "...\n" + output[-3000:]
                return f"{agent} session output:\n{output}"

            elif tool_name == "qa_review":
                dispatch_id = tool_input.get("dispatch_id", "")
                action = tool_input.get("action", "status")

                if action == "status":
                    # Check QA status — try local cache in qa router first
                    from routers.qa import _qa_cache

                    if dispatch_id == "latest" and _qa_cache:
                        # Get the most recent entry
                        latest_key = list(_qa_cache.keys())[-1]
                        entry = _qa_cache[latest_key]
                        dispatch_id = latest_key
                    elif dispatch_id in _qa_cache:
                        entry = _qa_cache[dispatch_id]
                    else:
                        entry = None

                    if entry:
                        reviews = entry.get("reviews", {})
                        lines = [f"QA Review for dispatch {dispatch_id}:"]
                        lines.append(f"  Agent: {entry.get('agent', 'unknown')}")
                        lines.append(f"  Task: {entry.get('task_summary', 'N/A')[:100]}")
                        for name in ("sonnet", "gpt", "opus"):
                            r = reviews.get(name, {})
                            lines.append(
                                f"  {name.upper()}: score={r.get('score', '?')}/10 "
                                f"verdict={r.get('verdict', '?')} — {r.get('feedback', '')[:100]}"
                            )
                        lines.append(f"  CONSENSUS: {entry.get('consensus_verdict', '?')}")
                        lines.append(f"  AVG SCORE: {entry.get('avg_score', '?')}")
                        lines.append(f"  DEPLOY READY: {entry.get('deploy_ready', False)}")
                        return "\n".join(lines)
                    else:
                        return f"No QA record found for dispatch_id={dispatch_id}"

                elif action in ("deploy", "reject"):
                    # Override via QA router
                    resp = await http.post(
                        f"http://localhost:7778/joao/council/qa/{dispatch_id}/override",
                        params={"action": action, "override_by": "johan"},
                    )
                    if resp.status_code == 200:
                        return f"QA override: {action} applied for dispatch {dispatch_id}"
                    else:
                        return f"QA override failed: {resp.text}"

                return f"Unknown qa_review action: {action}"

            else:
                # File system and server tools — run locally, no dispatch needed
                return await _execute_server_tool(tool_name, tool_input)

    except Exception as e:
        logger.error("Council tool %s failed: %s", tool_name, e)
        return f"ERROR executing {tool_name}: {e}"


async def _execute_server_tool(tool_name: str, tool_input: dict) -> str:
    """Execute file system and server tools locally."""
    import asyncio
    import subprocess
    import glob as _glob

    try:
        if tool_name == "read_file":
            path = tool_input.get("path", "")
            tail = tool_input.get("tail", 0)
            if not path:
                return "ERROR: path is required"
            p = Path(path).expanduser()
            if not p.exists():
                return f"ERROR: File not found: {path}"
            if not p.is_file():
                return f"ERROR: Not a file: {path}"
            # Size guard: max 200KB
            if p.stat().st_size > 200_000:
                if tail > 0:
                    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                    content = "\n".join(lines[-tail:])
                    return f"[Last {tail} lines of {path}]\n{content}"
                else:
                    # Read first 100KB + last 50KB
                    raw = p.read_text(encoding="utf-8", errors="replace")
                    return f"[File truncated: {p.stat().st_size} bytes]\n{raw[:100_000]}\n...\n[TRUNCATED]\n...\n{raw[-50_000:]}"
            content = p.read_text(encoding="utf-8", errors="replace")
            if tail > 0:
                lines = content.splitlines()
                content = "\n".join(lines[-tail:])
                return f"[Last {tail} lines of {path}]\n{content}"
            return content

        elif tool_name == "write_file":
            path = tool_input.get("path", "")
            content = tool_input.get("content", "")
            append = tool_input.get("append", False)
            if not path:
                return "ERROR: path is required"
            p = Path(path).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            p.write_text(content, encoding="utf-8") if not append else p.open("a", encoding="utf-8").write(content)
            return f"OK: {'Appended to' if append else 'Wrote'} {path} ({len(content)} chars)"

        elif tool_name == "list_directory":
            path = tool_input.get("path", "")
            recursive = tool_input.get("recursive", False)
            pattern = tool_input.get("pattern", "")
            if not path:
                return "ERROR: path is required"
            p = Path(path).expanduser()
            if not p.exists():
                return f"ERROR: Directory not found: {path}"
            if not p.is_dir():
                return f"ERROR: Not a directory: {path}"
            entries = []
            if recursive:
                glob_pat = f"**/{pattern}" if pattern else "**/*"
                for item in sorted(p.glob(glob_pat)):
                    rel = item.relative_to(p)
                    prefix = "d " if item.is_dir() else "f "
                    entries.append(f"{prefix}{rel}")
                    if len(entries) >= 500:
                        entries.append("... (truncated at 500)")
                        break
            else:
                items = sorted(p.iterdir())
                if pattern:
                    items = sorted(p.glob(pattern))
                for item in items:
                    prefix = "d " if item.is_dir() else "f "
                    size = ""
                    if item.is_file():
                        s = item.stat().st_size
                        size = f" ({s:,} bytes)" if s < 1_000_000 else f" ({s / 1_000_000:.1f}MB)"
                    entries.append(f"{prefix}{item.name}{size}")
                    if len(entries) >= 500:
                        entries.append("... (truncated at 500)")
                        break
            return f"Directory: {path}\n" + "\n".join(entries) if entries else f"Directory {path} is empty"

        elif tool_name == "search_files":
            pattern = tool_input.get("pattern", "")
            path = tool_input.get("path", "/home/zamoritacr")
            file_pattern = tool_input.get("file_pattern", "")
            max_results = min(tool_input.get("max_results", 30), 100)
            if not pattern:
                return "ERROR: pattern is required"
            cmd = ["grep", "-rn", "--include", file_pattern, pattern, path] if file_pattern else ["grep", "-rn", pattern, path]
            cmd += ["-m", str(max_results)]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15,
            )
            output = result.stdout.strip()
            if not output:
                return f"No matches found for '{pattern}' in {path}"
            # Truncate long output
            if len(output) > 8000:
                output = output[:8000] + "\n... (truncated)"
            return output

        elif tool_name == "run_command":
            command = tool_input.get("command", "")
            timeout = min(tool_input.get("timeout", 30), 120)
            if not command:
                return "ERROR: command is required"
            # Block obviously dangerous commands
            dangerous = ["rm -rf /", "mkfs", "dd if=", "> /dev/sd"]
            for d in dangerous:
                if d in command:
                    return f"ERROR: Blocked dangerous command pattern: {d}"
            result = subprocess.run(
                ["bash", "-c", command],
                capture_output=True, text=True, timeout=timeout,
                env={**os.environ, "HOME": str(Path.home())},
            )
            output = ""
            if result.stdout:
                output += result.stdout
            if result.stderr:
                output += ("\n[STDERR]\n" + result.stderr) if output else result.stderr
            if not output:
                output = f"(no output, exit code {result.returncode})"
            # Truncate
            if len(output) > 8000:
                output = output[:8000] + "\n... (truncated)"
            return output

        elif tool_name.startswith("drdata_"):
            return await _execute_drdata_tool(tool_name, tool_input)

        elif tool_name.startswith("focusflow_"):
            return await _execute_focusflow_tool(tool_name, tool_input)

        else:
            return f"Unknown server tool: {tool_name}"

    except subprocess.TimeoutExpired:
        return f"ERROR: Command timed out"
    except Exception as e:
        logger.error("Server tool %s failed: %s", tool_name, e)
        return f"ERROR: {tool_name} failed: {e}"


# ── Dr. Data Tool Execution ────────────────────────────────────────────

# Shared DrDataAgent instance (lazy init)
_drdata_agent = None
_drdata_lock = None


def _get_drdata_agent():
    """Lazy-init Dr. Data agent."""
    global _drdata_agent
    if _drdata_agent is not None:
        return _drdata_agent
    import sys
    drdata_root = str(Path.home() / "taop-repos" / "dr-data")
    if drdata_root not in sys.path:
        sys.path.insert(0, drdata_root)
    try:
        from app.dr_data_agent import DrDataAgent
        _drdata_agent = DrDataAgent()
        return _drdata_agent
    except Exception as e:
        logger.error("Failed to init DrDataAgent: %s", e)
        return None


async def _execute_drdata_tool(tool_name: str, tool_input: dict) -> str:
    """Execute Dr. Data tools."""
    import asyncio
    import pandas as pd

    try:
        if tool_name == "drdata_analyze":
            file_path = tool_input.get("file_path", "")
            question = tool_input.get("question", "")
            if not file_path:
                return "ERROR: file_path is required"
            p = Path(file_path).expanduser()
            if not p.exists():
                return f"ERROR: File not found: {file_path}"

            def _run():
                import sys
                drdata_root = str(Path.home() / "taop-repos" / "dr-data")
                if drdata_root not in sys.path:
                    sys.path.insert(0, drdata_root)
                from core.data_analyzer import DataAnalyzer
                from core.deep_analyzer import DeepAnalyzer

                ext = p.suffix.lower()
                if ext in (".csv", ".tsv"):
                    df = pd.read_csv(str(p), sep=None, engine="python")
                elif ext in (".xlsx", ".xls"):
                    df = pd.read_excel(str(p))
                elif ext == ".parquet":
                    df = pd.read_parquet(str(p))
                elif ext == ".json":
                    df = pd.read_json(str(p))
                else:
                    return f"ERROR: Unsupported file type: {ext}"

                table_name = p.stem.replace(" ", "_").replace("-", "_")
                analyzer = DataAnalyzer()
                profile = analyzer.analyze(df, table_name=table_name)

                deep = DeepAnalyzer()
                deep_profile = deep.profile(df)

                lines = [
                    f"Data Profile: {table_name}",
                    f"Rows: {profile.get('row_count', '?'):,}  Columns: {profile.get('column_count', '?')}",
                    f"Quality Score: {deep_profile.get('data_quality_score', deep_profile.get('quality_score', '?'))}",
                    "",
                    "Columns:",
                ]
                for col in profile.get("columns", [])[:30]:
                    sem = col.get("semantic_type", "?")
                    dtype = col.get("dtype", "?")
                    nulls = col.get("null_percentage", 0)
                    lines.append(f"  {col['name']}: {dtype} ({sem}) nulls={nulls:.1f}%")

                insights = deep_profile.get("quick_insights", deep_profile.get("insights", []))
                if insights:
                    lines.append("")
                    lines.append("Insights:")
                    for ins in insights[:10]:
                        if isinstance(ins, dict):
                            lines.append(f"  - {ins.get('text', ins.get('insight', str(ins)))}")
                        else:
                            lines.append(f"  - {ins}")

                return "\n".join(lines)

            return await asyncio.get_event_loop().run_in_executor(None, _run)

        elif tool_name == "drdata_quality_scan":
            file_path = tool_input.get("file_path", "")
            min_score = tool_input.get("min_score", 80)
            if not file_path:
                return "ERROR: file_path is required"
            p = Path(file_path).expanduser()
            if not p.exists():
                return f"ERROR: File not found: {file_path}"

            def _run():
                import sys
                drdata_root = str(Path.home() / "taop-repos" / "dr-data")
                if drdata_root not in sys.path:
                    sys.path.insert(0, drdata_root)
                from core.dq_engine import DataQualityEngine

                ext = p.suffix.lower()
                if ext in (".csv", ".tsv"):
                    df = pd.read_csv(str(p), sep=None, engine="python")
                elif ext in (".xlsx", ".xls"):
                    df = pd.read_excel(str(p))
                elif ext == ".parquet":
                    df = pd.read_parquet(str(p))
                else:
                    return f"ERROR: Unsupported file type: {ext}"

                table_name = p.stem.replace(" ", "_").replace("-", "_")
                dq = DataQualityEngine()
                result = dq.scan_table(df, table_name)

                overall = result.get("overall_score", "?")
                dims = result.get("dimensions", {})
                lines = [
                    f"Data Quality Scan: {table_name}",
                    f"Overall Score: {overall}",
                    f"Quality Gate: {'PASS' if isinstance(overall, (int, float)) and overall >= min_score else 'FAIL'} (min={min_score})",
                    "",
                    "Dimensions:",
                ]
                for dim_name, dim_data in dims.items():
                    score = dim_data.get("score", "?")
                    issues = dim_data.get("issues", [])
                    lines.append(f"  {dim_name}: {score}")
                    for issue in issues[:3]:
                        if isinstance(issue, dict):
                            lines.append(f"    - {issue.get('message', str(issue))}")
                        else:
                            lines.append(f"    - {issue}")

                recs = result.get("recommendations", [])
                if recs:
                    lines.append("")
                    lines.append("Recommendations:")
                    for r in recs[:5]:
                        if isinstance(r, dict):
                            pri = r.get("priority", "")
                            msg = r.get("message", str(r))
                            lines.append(f"  [{pri}] {msg}")
                        else:
                            lines.append(f"  - {r}")

                return "\n".join(lines)

            return await asyncio.get_event_loop().run_in_executor(None, _run)

        elif tool_name == "drdata_build_dashboard":
            file_path = tool_input.get("file_path", "")
            request = tool_input.get("request", "")
            fmt = tool_input.get("format", "html")
            if not file_path or not request:
                return "ERROR: file_path and request are required"
            p = Path(file_path).expanduser()
            if not p.exists():
                return f"ERROR: File not found: {file_path}"

            def _run():
                import sys, json as _j
                drdata_root = str(Path.home() / "taop-repos" / "dr-data")
                if drdata_root not in sys.path:
                    sys.path.insert(0, drdata_root)

                ext = p.suffix.lower()
                if ext in (".csv", ".tsv"):
                    df = pd.read_csv(str(p), sep=None, engine="python")
                elif ext in (".xlsx", ".xls"):
                    df = pd.read_excel(str(p))
                elif ext == ".parquet":
                    df = pd.read_parquet(str(p))
                else:
                    return f"ERROR: Unsupported file type: {ext}"

                agent = _get_drdata_agent()
                if not agent:
                    return "ERROR: Could not initialize Dr. Data agent"

                agent.inject_file(str(p), df)

                if fmt == "html":
                    result_json = agent._tool_build_html({
                        "request": request,
                        "title": request[:60],
                    })
                else:
                    result_json = agent._tool_build_powerbi({
                        "request": request,
                        "project_name": request[:40],
                        "audience": "executive",
                    })

                try:
                    result = _j.loads(result_json)
                    if "error" in result:
                        return f"ERROR: {result['error']}"
                    path = result.get("file_path", result.get("path", ""))
                    return f"Dashboard built: {path}\n{_j.dumps(result, indent=2)[:2000]}"
                except Exception:
                    return result_json[:3000]

            return await asyncio.get_event_loop().run_in_executor(None, _run)

        elif tool_name == "drdata_chat":
            message = tool_input.get("message", "")
            file_path = tool_input.get("file_path", "")
            if not message:
                return "ERROR: message is required"

            def _run():
                import sys
                drdata_root = str(Path.home() / "taop-repos" / "dr-data")
                if drdata_root not in sys.path:
                    sys.path.insert(0, drdata_root)

                agent = _get_drdata_agent()
                if not agent:
                    return "ERROR: Could not initialize Dr. Data agent"

                if file_path:
                    fp = Path(file_path).expanduser()
                    if fp.exists():
                        ext = fp.suffix.lower()
                        if ext in (".csv", ".tsv"):
                            df = pd.read_csv(str(fp), sep=None, engine="python")
                        elif ext in (".xlsx", ".xls"):
                            df = pd.read_excel(str(fp))
                        elif ext == ".parquet":
                            df = pd.read_parquet(str(fp))
                        else:
                            df = None
                        if df is not None:
                            agent.inject_file(str(fp), df)

                result = agent.chat(message)
                text = result.get("text", str(result)) if isinstance(result, dict) else str(result)
                files = result.get("files", []) if isinstance(result, dict) else []

                output = text
                if files:
                    output += "\n\nGenerated files:\n" + "\n".join(f"  - {f}" for f in files)
                if len(output) > 5000:
                    output = output[:5000] + "\n... (truncated)"
                return output

            return await asyncio.get_event_loop().run_in_executor(None, _run)

        else:
            return f"Unknown drdata tool: {tool_name}"

    except Exception as e:
        logger.error("DrData tool %s failed: %s", tool_name, e)
        return f"ERROR: {tool_name} failed: {e}"


# ── FocusFlow Tool Execution ───────────────────────────────────────────

_FOCUSFLOW_URL = "http://localhost:8001"


async def _execute_focusflow_tool(tool_name: str, tool_input: dict) -> str:
    """Execute FocusFlow tools via its REST API."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=300.0) as http:
            if tool_name == "focusflow_process_url":
                url = tool_input.get("url", "")
                age_group = tool_input.get("age_group", "adult")
                class_name = tool_input.get("class_name", "Lecture")
                if not url:
                    return "ERROR: url is required"
                resp = await http.post(
                    f"{_FOCUSFLOW_URL}/youtube",
                    data={"url": url, "age_group": age_group, "class_name": class_name},
                )
                resp.raise_for_status()
                data = resp.json()
                job_id = data.get("job_id", "")
                return (
                    f"FocusFlow job submitted: {job_id}\n"
                    f"URL: {url}\n"
                    f"Age group: {age_group}\n"
                    f"Use focusflow_status to check progress."
                )

            elif tool_name == "focusflow_process_file":
                file_path = tool_input.get("file_path", "")
                age_group = tool_input.get("age_group", "adult")
                class_name = tool_input.get("class_name", "Lecture")
                if not file_path:
                    return "ERROR: file_path is required"
                p = Path(file_path).expanduser()
                if not p.exists():
                    return f"ERROR: File not found: {file_path}"
                with open(p, "rb") as f:
                    resp = await http.post(
                        f"{_FOCUSFLOW_URL}/process",
                        files={"file": (p.name, f, "audio/mpeg")},
                        data={"age_group": age_group, "class_name": class_name},
                    )
                resp.raise_for_status()
                data = resp.json()
                job_id = data.get("job_id", "")
                return (
                    f"FocusFlow job submitted: {job_id}\n"
                    f"File: {file_path}\n"
                    f"Use focusflow_status to check progress."
                )

            elif tool_name == "focusflow_status":
                job_id = tool_input.get("job_id", "")
                if not job_id:
                    return "ERROR: job_id is required"
                resp = await http.get(f"{_FOCUSFLOW_URL}/status/{job_id}")
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status", "unknown")
                if status == "done":
                    session_id = data.get("session_id", "")
                    summary = data.get("summary", "")
                    result = f"Status: DONE\nSession ID: {session_id}\n"
                    if summary:
                        result += f"\nSummary:\n{summary[:3000]}"
                    result += f"\n\nUse focusflow_download with session_id='{session_id}' to get PDF/PPTX/DOCX."
                    return result
                elif status == "processing":
                    progress = data.get("progress", "")
                    return f"Status: PROCESSING\nProgress: {progress}"
                elif status == "error":
                    return f"Status: ERROR\n{data.get('error', 'Unknown error')}"
                else:
                    return f"Status: {status}\n{str(data)[:1000]}"

            elif tool_name == "focusflow_download":
                session_id = tool_input.get("session_id", "")
                fmt = tool_input.get("format", "pdf")
                if not session_id:
                    return "ERROR: session_id is required"
                resp = await http.get(
                    f"{_FOCUSFLOW_URL}/download/{session_id}",
                    params={"fmt": fmt},
                )
                resp.raise_for_status()
                # Save to output directory
                out_dir = Path.home() / "focusflow" / "output"
                out_dir.mkdir(parents=True, exist_ok=True)
                out_file = out_dir / f"focusflow_{session_id[:8]}.{fmt}"
                out_file.write_bytes(resp.content)
                return f"Downloaded: {out_file} ({len(resp.content):,} bytes)"

            else:
                return f"Unknown focusflow tool: {tool_name}"

    except httpx.HTTPStatusError as e:
        return f"ERROR: FocusFlow returned {e.response.status_code}: {e.response.text[:500]}"
    except httpx.ConnectError:
        return "ERROR: FocusFlow service unreachable at localhost:8001. Is it running?"
    except Exception as e:
        logger.error("FocusFlow tool %s failed: %s", tool_name, e)
        return f"ERROR: {tool_name} failed: {e}"


_TAOP_CONTEXT_FILE = Path.home() / "council" / "brain" / "TAOP_MASTER_CONTEXT_v3.md"


async def _load_context() -> tuple[str, str]:
    """Load context and session log from local files or tunnel.

    Hard cap: context_text <= 15,000 chars (~3,750 tokens) to stay well
    within Sonnet's 200k limit after tools + messages are added.
    """
    _MAX_CONTEXT = 15_000
    _MAX_SESSION_LOG = 4_000

    context_text = ""
    session_log_text = ""
    if _CONTEXT_FILE.exists():
        raw = _CONTEXT_FILE.read_text(encoding="utf-8")
        # Cap master context at 12k to leave room for TAOP
        context_text = raw[:12_000] if len(raw) > 12_000 else raw
        if len(raw) > 12_000:
            logger.warning("JOAO_MASTER_CONTEXT.md truncated from %d to 12000 chars", len(raw))
        # Load TAOP master context (ground truth by ARIA)
        remaining = _MAX_CONTEXT - len(context_text)
        if _TAOP_CONTEXT_FILE.exists() and remaining > 500:
            taop_ctx = _TAOP_CONTEXT_FILE.read_text(encoding="utf-8")
            cap = min(len(taop_ctx), remaining - 100)  # leave room for header
            if cap > 0:
                taop_ctx = taop_ctx[:cap]
                if len(taop_ctx) < len(_TAOP_CONTEXT_FILE.read_text(encoding="utf-8")):
                    taop_ctx += "\n... [TRUNCATED]"
                context_text += "\n\n---\n\n## TAOP Master Context (Ground Truth)\n\n" + taop_ctx
        if _SESSION_LOG_FILE.exists():
            full_log = _SESSION_LOG_FILE.read_text(encoding="utf-8")
            session_log_text = full_log[-_MAX_SESSION_LOG:] if len(full_log) > _MAX_SESSION_LOG else full_log
    else:
        tunnel_url = os.environ.get(
            "JOAO_TUNNEL_URL",
            "https://convicted-subjects-slow-impressive.trycloudflare.com",
        )
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                resp = await http.get(f"{tunnel_url}/joao/context")
                resp.raise_for_status()
                data = resp.json()
                context_text = data.get("context", "")
                session_log_text = data.get("session_log", "")
        except Exception as e:
            logger.warning("Failed to fetch context from tunnel: %s", e)
    return context_text, session_log_text


@router.post("/chat")
async def chat_proxy(req: ChatRequest):
    """Proxy chat to Claude with persistent memory context and council tools. Streams SSE."""
    import anthropic
    import json as _json

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    # MrDP mode: neurodivergent companion, no tools, opus model
    is_mrdp = getattr(req, "mode", "joao") == "mrdp"

    if is_mrdp:
        _mrdp_prompt_path = Path(__file__).parent.parent / "mrdp_system_prompt.md"
        try:
            system_prompt = _mrdp_prompt_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            system_prompt = "You are MrDP, a neurodivergent life companion built from neuroscience."
    else:
        context_text, session_log_text = await _load_context()

        system_prompt = context_text or "You are JOÃO, a persistent AI companion."
        if session_log_text:
            system_prompt += f"\n\n---\n\n## Session Log (recent)\n\n{session_log_text}"

    if not is_mrdp:
        system_prompt += (
        "\n\n---\n\n## Your Tools (MANDATORY -- USE THEM)\n\n"
        "### Council Tools\n"
        "- council_status: Check which agents are online\n"
        "- council_dispatch: Send a task to an agent\n"
        "- council_session_output: Check agent progress/output\n"
        "- qa_review: Check QA scores or override deploy/reject\n"
        "- escalate_to_opus: Deep analysis via Claude Opus\n\n"
        "### Server Tools (FULL ACCESS)\n"
        "- read_file: Read any file on the server\n"
        "- write_file: Write/create/append to files\n"
        "- list_directory: List directory contents\n"
        "- search_files: Search file contents with regex\n"
        "- run_command: Execute any shell command on the server\n\n"
        "### Dr. Data Tools (Data Intelligence)\n"
        "- drdata_analyze: Profile a data file (semantic types, quality, insights)\n"
        "- drdata_quality_scan: Run DAMA-DMBOK DQ scan (6 dimensions, quality gate)\n"
        "- drdata_build_dashboard: Build HTML dashboard or Power BI project from data\n"
        "- drdata_chat: Ask Dr. Data questions about data (analysis, patterns, reports)\n\n"
        "### FocusFlow Tools (Lecture Summarizer)\n"
        "- focusflow_process_url: Transcribe + summarize a YouTube/lecture URL\n"
        "- focusflow_process_file: Transcribe + summarize an audio/video file\n"
        "- focusflow_status: Check processing job status\n"
        "- focusflow_download: Download summary as PDF/PPTX/DOCX/HTML/TXT/XLSX\n\n"
        "RULES:\n"
        "- When Johan mentions ANY agent, asks who is online, dispatch, status, or progress: "
        "ALWAYS call the appropriate tool.\n"
        "- When Johan asks about files, logs, configs, code, services, processes: "
        "USE read_file, list_directory, search_files, or run_command.\n"
        "- When Johan asks to fix, update, or create something: "
        "USE write_file or run_command.\n"
        "- When Johan asks about DATA (analyze, quality, dashboard, profile, trends): "
        "USE drdata_analyze, drdata_quality_scan, drdata_build_dashboard, or drdata_chat.\n"
        "- When Johan asks to summarize a VIDEO or LECTURE or AUDIO: "
        "USE focusflow_process_url or focusflow_process_file, then focusflow_status to poll.\n"
        "- NEVER say 'I cannot see', 'I don't have access', 'check directly', etc. "
        "You HAVE full access to the entire server. USE YOUR TOOLS.\n"
        "- Hot pool agents (MAX, CORE, BYTE) are always running. On-demand agents "
        "are launched automatically when dispatched.\n"
        "- Always confirm results after tool execution.\n"
        "- Use escalate_to_opus for complex architecture, deep debugging, or when Johan "
        "explicitly asks for 'deep thinking' or 'opus'.\n"
        "- You are running on the ROG Strix server (192.168.0.55). Home dir: /home/zamoritacr\n"
        "- Key paths: ~/joao-spine/ (spine), ~/joao-interface/ (interface), ~/council/ (agents), "
        "~/projects/ (projects), ~/taop-site/ (hub), ~/logs/ (all logs)\n"
        )

    if req.messages:
        last_msg = req.messages[-1]
        if last_msg.role == "user":
            log_content = last_msg.content if isinstance(last_msg.content, str) else "[multimodal message]"
            _append_log_sync("user", log_content)

    api_messages = [{"role": m.role, "content": m.content} for m in req.messages]
    client = anthropic.AsyncAnthropic(api_key=api_key, timeout=120.0)
    # MrDP uses Opus for depth; JOAO uses Sonnet for tool reliability
    model = "claude-opus-4-6" if is_mrdp else "claude-sonnet-4-6"

    async def event_stream():
        import asyncio

        full_response = ""
        messages = list(api_messages)
        max_tool_rounds = 5

        try:
            for _round in range(max_tool_rounds):
                logger.info("Chat round %d starting (messages=%d)", _round, len(messages))
                create_kwargs = dict(
                    model=model,
                    max_tokens=4096,
                    system=[
                        {
                            "type": "text",
                            "text": system_prompt,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=messages,
                )
                if not is_mrdp:
                    create_kwargs["tools"] = COUNCIL_TOOLS
                response = await client.messages.create(**create_kwargs)

                # Separate text blocks and tool_use blocks
                has_tool_use = False
                tool_calls = []

                for block in response.content:
                    if block.type == "text":
                        full_response += block.text
                        # SSE requires each line to start with "data: "
                        # Send as single JSON-encoded line to preserve newlines
                        import json as _json
                        yield f"data: {_json.dumps(block.text)}\n\n"
                    elif block.type == "tool_use":
                        has_tool_use = True
                        tool_calls.append(block)

                if not has_tool_use:
                    break

                # Show what tools are being called
                tool_names = [tc.name for tc in tool_calls]
                yield f"data: [Executing: {', '.join(tool_names)}...]\n\n"

                # Run ALL tool calls in parallel
                async def _run_tool(block):
                    try:
                        result = await asyncio.wait_for(
                            _execute_council_tool(block.name, block.input),
                            timeout=60.0,
                        )
                    except asyncio.TimeoutError:
                        result = f"ERROR: {block.name} timed out after 60s"
                        logger.error("Tool %s timed out", block.name)
                    except Exception as e:
                        result = f"ERROR: {block.name} failed: {e}"
                        logger.error("Tool %s failed: %s", block.name, e)
                    return {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    }

                tool_results = await asyncio.gather(*[_run_tool(tc) for tc in tool_calls])
                tool_results = list(tool_results)

                logger.info("Tools completed: %s", tool_names)
                yield f"data: [Tools done, generating response...]\n\n"

                # Add assistant response and tool results for next round
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})

        except anthropic.APIError as e:
            logger.error("Claude API error: %s", e)
            yield f"data: [ERROR] {e.message}\n\n"
        except Exception as e:
            logger.exception("Chat stream error")
            yield f"data: [ERROR] {e}\n\n"

        if full_response:
            _append_log_sync("assistant", full_response)
            # Feed to spine session log for context watcher
            user_msg = ""
            if req.messages:
                last = req.messages[-1]
                if last.role == "user":
                    user_msg = last.content if isinstance(last.content, str) else "[multimodal message]"
            _append_chat_feed(user_msg, full_response)
            # Auto-grow context: append exchange summary to session log
            _auto_grow_context(user_msg, full_response)
            await _persist_chat_session(
                session_id=req.session_id,
                messages=[{"role": m.role, "content": m.content} for m in req.messages],
                assistant_text=full_response,
                source="joao-chat",
                model=model,
                mode=("mrdp" if is_mrdp else "joao"),
            )

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Transcribe (hold-to-record mic from chat UI) ──────────────────────────

@router.post("/transcribe")
async def transcribe(audio: UploadFile, _: None = Depends(require_api_key)):
    """Transcribe audio upload via Whisper. Returns {text, language}."""
    audio_bytes = await audio.read()
    result = await ai_processor.transcribe_audio(audio_bytes, audio.filename or "audio.webm")

    # Write transcript to audio dir for context watcher pickup
    try:
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        transcript_text = result.get("text", "") if isinstance(result, dict) else str(result)
        if transcript_text:
            transcript_file = _AUDIO_DIR / f"transcript_{ts}.txt"
            _AUDIO_DIR.mkdir(parents=True, exist_ok=True)
            transcript_file.write_text(
                f"TRANSCRIPTION: {transcript_text}\n"
                f"SOURCE: {audio.filename or 'audio.webm'}\n",
                encoding="utf-8",
            )
    except Exception as e:
        logger.warning("Failed to write transcribe feed: %s", e)

    return result


# ── Links Feed ──────────────────────────────────────────────────────────────

_LINKS_DIR = _PROJECT_ROOT / "links"


class LinkRequest(BaseModel):
    url: str
    notes: str = ""


def _is_youtube(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url


def _extract_youtube(url: str) -> tuple[str, str]:
    """Return (content_type, transcript_text). Tries youtube-transcript-api, falls back to yt-dlp."""
    # Extract video ID
    import re
    vid_id = None
    m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    if m:
        vid_id = m.group(1)

    if vid_id:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            api = YouTubeTranscriptApi()
            fetched = api.fetch(vid_id, languages=["en", "en-US", "a.en"])
            text = " ".join(seg.text for seg in fetched)
            return "youtube", text
        except Exception as e:
            logger.warning("youtube-transcript-api failed for %s: %s", vid_id, e)

    # Fallback: yt-dlp auto-captions
    try:
        import subprocess, json, tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    "yt-dlp", "--write-auto-sub", "--sub-lang", "en",
                    "--skip-download", "--sub-format", "vtt",
                    "-o", f"{tmpdir}/sub", url,
                ],
                capture_output=True, text=True, timeout=60,
            )
            import glob as _glob
            vtt_files = _glob.glob(f"{tmpdir}/*.vtt")
            if vtt_files:
                raw = Path(vtt_files[0]).read_text(encoding="utf-8")
                # Strip VTT formatting: remove timestamps and tags
                import re
                lines = []
                for line in raw.splitlines():
                    line = line.strip()
                    if not line or "-->" in line or line.startswith("WEBVTT") or re.match(r"^\d+$", line):
                        continue
                    clean = re.sub(r"<[^>]+>", "", line)
                    if clean:
                        lines.append(clean)
                return "youtube", " ".join(lines)
    except Exception as e:
        logger.warning("yt-dlp caption fallback failed for %s: %s", url, e)

    return "youtube", "[TRANSCRIPT UNAVAILABLE]"


def _extract_pdf(content: bytes) -> str:
    try:
        import pdfplumber, io
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
        return "\n".join(pages)
    except Exception as e:
        logger.warning("pdfplumber failed, trying PyPDF2: %s", e)
    try:
        import PyPDF2, io
        reader = PyPDF2.PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as e2:
        logger.warning("PyPDF2 also failed: %s", e2)
        return "[PDF EXTRACTION FAILED]"


def _extract_article(url: str) -> tuple[str, str]:
    """Returns (content_type, text). Handles PDF, Twitter/X, and generic HTML."""
    import requests as _requests
    from bs4 import BeautifulSoup

    headers = {"User-Agent": "Mozilla/5.0 (JOAO/2.0)"}
    resp = _requests.get(url, timeout=20, headers=headers)
    resp.raise_for_status()

    content_type_header = resp.headers.get("Content-Type", "").lower()

    if "pdf" in content_type_header or url.lower().endswith(".pdf"):
        return "pdf", _extract_pdf(resp.content)

    is_twitter = "twitter.com" in url or "x.com" in url
    content_type_label = "twitter" if is_twitter else "article"

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return content_type_label, text


async def _run_learning_analysis(url: str, content_type: str, content: str) -> tuple[str, list[str], list[str]]:
    """Send content to Claude for learning analysis. Returns (analysis_text, key_insights, applied_to)."""
    import anthropic as _anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return "[LEARNING ANALYSIS SKIPPED: no API key]", [], []

    # Trim content to avoid token overflow (keep first 6000 words)
    words = content.split()
    trimmed = " ".join(words[:6000])
    if len(words) > 6000:
        trimmed += "\n\n[CONTENT TRIMMED]"

    prompt = (
        f"URL: {url}\nContent type: {content_type}\n\n"
        f"CONTENT:\n{trimmed}\n\n"
        "---\n"
        "You are JOAO's learning engine. Read this content and extract:\n"
        "1) Key concepts and insights\n"
        "2) Anything applicable to TAOP projects (dopamine.watch, dopamine.chat, Dr. Data, TAOP Connect, JOAO)\n"
        "3) Any tools, frameworks, or techniques worth adding to our stack. Be specific and direct. No emojis.\n\n"
        "Format your response as:\n"
        "KEY_INSIGHTS: <comma-separated list of 3-7 short insights>\n"
        "APPLIED_TO: <comma-separated list of relevant TAOP projects, or 'none'>\n"
        "ANALYSIS:\n<your full analysis>"
    )

    try:
        # Route through LLMRouter — uses Ollama locally, OpenRouter when key set
        raw = await llm_complete(
            messages=[{"role": "user", "content": prompt}],
            task_type="summarization",
            max_tokens=512,
        )

        # Parse structured fields
        key_insights: list[str] = []
        applied_to: list[str] = []
        analysis = raw

        for line in raw.splitlines():
            if line.startswith("KEY_INSIGHTS:"):
                key_insights = [x.strip() for x in line[len("KEY_INSIGHTS:"):].split(",") if x.strip()]
            elif line.startswith("APPLIED_TO:"):
                applied_to = [x.strip() for x in line[len("APPLIED_TO:"):].split(",") if x.strip() and x.strip().lower() != "none"]

        return raw, key_insights, applied_to
    except Exception as e:
        logger.error("Learning analysis failed: %s", e)
        return f"[LEARNING ANALYSIS ERROR: {e}]", [], []


@router.post("/links")
async def save_link(req: LinkRequest):
    """Extract content from any URL (YouTube, PDF, article, Twitter), run Claude learning analysis, feed to session log."""
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    _LINKS_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Extract content
    content_type = "article"
    raw_content = ""
    try:
        if _is_youtube(req.url):
            content_type, raw_content = _extract_youtube(req.url)
        else:
            content_type, raw_content = _extract_article(req.url)
    except Exception as e:
        logger.warning("Content extraction failed for %s: %s", req.url, e)
        raw_content = f"[EXTRACTION FAILED: {e}]"

    word_count = len(raw_content.split())

    # Step 2: Learning analysis via Claude
    analysis_text, key_insights, applied_to = await _run_learning_analysis(req.url, content_type, raw_content)

    # Step 3: Write to links dir
    link_file = _LINKS_DIR / f"link_{ts}.txt"
    file_body = (
        f"URL: {req.url}\n"
        f"CONTENT_TYPE: {content_type}\n"
        f"NOTES: {req.notes}\n"
        f"WORD_COUNT: {word_count}\n"
        f"TIMESTAMP: {ts}\n\n"
        f"## EXTRACTED CONTENT\n\n{raw_content}\n\n"
        f"## JOAO LEARNING ANALYSIS\n\n{analysis_text}\n"
    )
    link_file.write_text(file_body, encoding="utf-8")

    # Step 4: Append to spine session log for context watcher
    try:
        spine_log = _SPINE_SESSION_LOG
        session_entry = (
            f"\n## LINK INGESTED [{ts}]\n"
            f"URL: {req.url}\n"
            f"TYPE: {content_type} | WORDS: {word_count}\n"
            f"KEY_INSIGHTS: {', '.join(key_insights) if key_insights else 'none'}\n"
            f"APPLIED_TO: {', '.join(applied_to) if applied_to else 'none'}\n\n"
            f"### CONTENT (first 1000 chars)\n{raw_content[:1000]}\n\n"
            f"### LEARNING ANALYSIS\n{analysis_text}\n"
        )
        with open(spine_log, "a", encoding="utf-8") as f:
            f.write(session_entry)
    except Exception as e:
        logger.warning("Failed to append link to session log: %s", e)

    return {
        "url": req.url,
        "content_type": content_type,
        "word_count": word_count,
        "key_insights": key_insights,
        "applied_to": applied_to,
        "file": str(link_file),
    }


# ── Download: fintech roadmap PPT ─────────────────────────────────────────

_FINTECH_PPT_PATH = Path(__file__).parent.parent / "static" / "fintech-roadmap.pptx"


@router.get("/download/fintech-roadmap")
async def download_fintech_roadmap():
    """Serve the Fintech AI Deployment Roadmap PowerPoint file."""
    if not _FINTECH_PPT_PATH.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(
        path=str(_FINTECH_PPT_PATH),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename="fintech-ai-deployment-roadmap.pptx",
    )


# ── Content Intelligence ─────────────────────────────────────────────────────

class LinkRequest(BaseModel):
    url: str


@router.post("/links")
async def links(req: LinkRequest):
    """Ingest any URL: YouTube gets transcript+analysis, all others get web scrape+analysis."""
    from services.content_intelligence import _extract_video_id, handle_youtube, handle_web_link

    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=422, detail="url is required")

    try:
        if _extract_video_id(url):
            result = await handle_youtube(url)
        else:
            result = await handle_web_link(url)
        return result
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        logger.exception("links endpoint error for %s", req.url)
        raise HTTPException(status_code=500, detail="Content processing failed.")


_ALLOWED_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".csv", ".docx"}

_OUTPUTS_DIR = _PROJECT_ROOT / "outputs"


@router.post("/upload")
async def upload(file: UploadFile):
    """Ingest a file: PDF, Excel/CSV, or DOCX. Returns HTML report path + analysis."""
    from services import content_intelligence as ci

    filename = file.filename or "upload"
    ext = Path(filename).suffix.lower()

    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type: {ext}. Allowed: {', '.join(_ALLOWED_EXTENSIONS)}",
        )

    data = await file.read()

    try:
        if ext == ".pdf":
            result = await ci.handle_pdf(filename, data)
        elif ext in {".xlsx", ".xls", ".csv"}:
            result = await ci.handle_spreadsheet(filename, data)
        elif ext == ".docx":
            result = await ci.handle_docx(filename, data)
        else:
            raise HTTPException(status_code=422, detail="Unsupported file type.")
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        logger.exception("upload endpoint error for %s", filename)
        raise HTTPException(status_code=500, detail="File processing failed.")


@router.get("/outputs/{filename}")
async def get_output(filename: str):
    """Serve a generated HTML report from the outputs directory."""
    # Sanitize: no path traversal
    safe_name = Path(filename).name
    file_path = _OUTPUTS_DIR / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Report not found.")
    return FileResponse(path=str(file_path), media_type="text/html")


@router.get("/outputs")
async def outputs_index():
    """List all generated intelligence reports."""
    index_path = _OUTPUTS_DIR / "index.html"
    if not index_path.exists():
        from services.content_intelligence import _update_outputs_index
        _update_outputs_index()
    return FileResponse(path=str(index_path), media_type="text/html")


# ── DR DATA BROWSER TOOL ENDPOINTS ──────────────────────────────────────────

def _extract_session_hint(request: Request, payload: dict, prefix: str) -> tuple[str, str]:
    session_id = (
        request.headers.get("x-session-id")
        or payload.get("session_id")
        or payload.get("conversation_id")
        or f"{prefix}-{int(time.time() * 1000)}"
    )
    source = request.headers.get("x-client-source") or payload.get("source") or prefix
    return session_id, source


def _coerce_message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                else:
                    parts.append(str(item))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def _extract_last_user_text(payload: dict) -> str:
    messages = payload.get("messages") or []
    if isinstance(messages, list):
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                return _coerce_message_text(msg.get("content", ""))
    prompt = payload.get("prompt")
    return prompt if isinstance(prompt, str) else str(prompt or "")


async def _persist_proxy_session(request: Request, payload: dict, response_text: str, prefix: str, model_hint: str = "") -> None:
    session_id, source = _extract_session_hint(request, payload, prefix)
    user_text = _extract_last_user_text(payload)
    messages = payload.get("messages") or []
    normalized = []
    if isinstance(messages, list) and messages:
        for msg in messages:
            if isinstance(msg, dict):
                normalized.append({"role": msg.get("role", "user"), "content": _coerce_message_text(msg.get("content", ""))})
    elif user_text:
        normalized.append({"role": "user", "content": user_text})
    await _persist_chat_session(
        session_id=session_id,
        messages=normalized,
        assistant_text=response_text,
        source=source,
        model=model_hint,
        mode=prefix,
    )


@router.options("/claude-proxy")
async def claude_proxy_options(request: Request):
    from fastapi.responses import Response
    return Response(status_code=200, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, x-api-key, anthropic-version",
    })

@router.post("/claude-proxy")
async def claude_proxy(request: Request):
    """Streaming Claude CORS proxy for Dr. Data browser tool."""
    import httpx, os, json
    from fastapi.responses import StreamingResponse
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
    body_bytes = await request.body()
    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        payload = {}
    async def stream():
        full_text = ""
        async with httpx.AsyncClient(timeout=180) as client:
            async with client.stream(
                "POST", "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                content=body_bytes,
            ) as resp:
                async for chunk in resp.aiter_bytes():
                    try:
                        decoded = chunk.decode("utf-8", errors="ignore")
                        for line in decoded.splitlines():
                            if line.startswith("data: "):
                                data = line[6:]
                                if data and data != "[DONE]":
                                    obj = json.loads(data)
                                    if obj.get("type") == "content_block_delta":
                                        delta = obj.get("delta", {})
                                        full_text += delta.get("text", "")
                    except Exception:
                        pass
                    yield chunk
        if full_text:
            await _persist_proxy_session(request, payload, full_text, prefix="claude-proxy", model_hint=payload.get("model", "claude"))
    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type",
        "Cache-Control": "no-cache",
    })

@router.options("/drdata-config")
async def drdata_config_options(request: Request):
    from fastapi.responses import Response
    return Response(status_code=200, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    })

@router.get("/drdata-config")
async def drdata_config():
    """Config endpoint — health check for Dr. Data. Reports available brains."""
    import os, httpx
    claude_ok = bool(os.getenv("ANTHROPIC_API_KEY", ""))
    gpt_ok = bool(os.getenv("OPENAI_API_KEY", ""))
    ollama_ok = False
    ollama_models = []
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            if resp.status_code == 200:
                ollama_ok = True
                ollama_models = [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        pass
    return {
        "claude_available": claude_ok,
        "gpt_available": gpt_ok,
        "ollama_available": ollama_ok,
        "ollama_models": ollama_models,
        "claude_proxy": "https://joao.theartofthepossible.io/joao/claude-proxy",
        "gpt_proxy": "https://joao.theartofthepossible.io/joao/gpt-proxy",
        "ollama_proxy": "https://joao.theartofthepossible.io/joao/ollama-proxy",
        "version": "3.0",
    }


# ------------------------------------------------------------------ #
#  GPT-4o Proxy (server-side key — user pays nothing)                 #
# ------------------------------------------------------------------ #

@router.options("/gpt-proxy")
async def gpt_proxy_options(request: Request):
    from fastapi.responses import Response
    return Response(status_code=200, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
    })

@router.post("/gpt-proxy")
async def gpt_proxy(request: Request):
    """Streaming GPT-4o CORS proxy for Dr. Data. Server-side OPENAI_API_KEY."""
    import httpx, os, json
    from fastapi.responses import StreamingResponse
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured")
    body_bytes = await request.body()
    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        payload = {}
    async def stream():
        full_text = ""
        async with httpx.AsyncClient(timeout=180) as client:
            async with client.stream(
                "POST", "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                content=body_bytes,
            ) as resp:
                async for chunk in resp.aiter_bytes():
                    try:
                        decoded = chunk.decode("utf-8", errors="ignore")
                        for line in decoded.splitlines():
                            if line.startswith("data: "):
                                data = line[6:]
                                if data and data != "[DONE]":
                                    obj = json.loads(data)
                                    choices = obj.get("choices") or []
                                    if choices:
                                        delta = choices[0].get("delta", {})
                                        full_text += delta.get("content", "") or ""
                    except Exception:
                        pass
                    yield chunk
        if full_text:
            await _persist_proxy_session(request, payload, full_text, prefix="gpt-proxy", model_hint=payload.get("model", "gpt"))
    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type",
        "Cache-Control": "no-cache",
    })


# ------------------------------------------------------------------ #
#  Ollama Proxy (localhost:11434 — free, no API key)                  #
# ------------------------------------------------------------------ #

@router.options("/ollama-proxy")
async def ollama_proxy_options(request: Request):
    from fastapi.responses import Response
    return Response(status_code=200, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    })

@router.post("/ollama-proxy")
async def ollama_proxy(request: Request):
    """Ollama proxy — routes to localhost:11434/api/generate. Free, local."""
    import httpx
    from fastapi.responses import JSONResponse
    body = await request.json()
    model = body.get("model", "deepseek-coder-v2")
    prompt = body.get("prompt", "")
    stream_flag = body.get("stream", False)
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "http://localhost:11434/api/generate",
                json={"model": model, "prompt": prompt, "stream": stream_flag},
            )
            result = resp.json()
        response_text = result.get("response", "") if isinstance(result, dict) else str(result)
        if response_text:
            await _persist_proxy_session(request, body, response_text, prefix="ollama-proxy", model_hint=model)
        return JSONResponse(content=result, headers={
            "Access-Control-Allow-Origin": "*",
        })
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama error: {e}")


# ------------------------------------------------------------------ #
#  Dual-Brain Context — Claude + GPT with Supabase persistence        #
# ------------------------------------------------------------------ #

class BrainMessageRequest(BaseModel):
    message: str
    system: str = ""

class BrainMemoryRequest(BaseModel):
    key: str
    value: str


@router.post("/claude")
async def brain_claude(payload: BrainMessageRequest):
    """Send a message to Claude with persistent context and memory."""
    from services.brain_manager import ask_claude
    try:
        reply = ask_claude(payload.message, payload.system)
        return {"model": "claude", "reply": reply}
    except Exception as e:
        logger.error("Brain Claude error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/gpt")
async def brain_gpt(payload: BrainMessageRequest):
    """Send a message to GPT with persistent context and memory."""
    from services.brain_manager import ask_gpt
    try:
        reply = ask_gpt(payload.message, payload.system)
        return {"model": "gpt", "reply": reply}
    except Exception as e:
        logger.error("Brain GPT error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory")
async def brain_memory_set(payload: BrainMemoryRequest):
    """Upsert a key-value pair in brain_memory."""
    from services.brain_manager import set_memory
    try:
        set_memory(payload.key, payload.value)
        return {"status": "ok", "key": payload.key}
    except Exception as e:
        logger.error("Brain memory set error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory")
async def brain_memory_get():
    """Return all brain_memory as JSON."""
    from services.brain_manager import get_memory
    try:
        return {"memory": get_memory()}
    except Exception as e:
        logger.error("Brain memory get error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
