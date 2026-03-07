"""8 REST endpoints + shared _content_pipeline + content intelligence."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
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
        # Fallback: try Cloudflare tunnel (needed when running on Railway)
        dispatch_url = os.environ.get(
            "JOAO_TUNNEL_URL",
            "https://convicted-subjects-slow-impressive.trycloudflare.com",
        )
        # Tunnel goes to local dispatch on 7777, not spine on 7778
        # Re-route: tunnel points at spine (7778), dispatch is on 7777 locally
        # So we need the dispatch tunnel, not the spine tunnel
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
                for name, info in agents.items():
                    status = "ONLINE" if info.get("active") else "OFFLINE"
                    lines.append(f"  {name}: {status}")
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
                if len(output) > 1200:
                    output = "...\n" + output[-1200:]
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
                        f"http://localhost:8000/joao/council/qa/{dispatch_id}/override",
                        params={"action": action, "override_by": "johan"},
                    )
                    if resp.status_code == 200:
                        return f"QA override: {action} applied for dispatch {dispatch_id}"
                    else:
                        return f"QA override failed: {resp.text}"

                return f"Unknown qa_review action: {action}"

            else:
                return f"Unknown tool: {tool_name}"

    except Exception as e:
        logger.error("Council tool %s failed: %s", tool_name, e)
        return f"ERROR executing {tool_name}: {e}"


async def _load_context() -> tuple[str, str]:
    """Load context and session log from local files or tunnel."""
    context_text = ""
    session_log_text = ""
    if _CONTEXT_FILE.exists():
        context_text = _CONTEXT_FILE.read_text(encoding="utf-8")
        if _SESSION_LOG_FILE.exists():
            full_log = _SESSION_LOG_FILE.read_text(encoding="utf-8")
            session_log_text = full_log[-4000:] if len(full_log) > 4000 else full_log
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

    context_text, session_log_text = await _load_context()

    system_prompt = context_text or "You are JOÃO, a persistent AI companion."
    if session_log_text:
        system_prompt += f"\n\n---\n\n## Session Log (recent)\n\n{session_log_text}"

    system_prompt += (
        "\n\n---\n\n## Council Dispatch (MANDATORY)\n\n"
        "You have 4 tools: council_status, council_dispatch, council_session_output, qa_review.\n"
        "RULES:\n"
        "- When Johan mentions ANY agent (ARIA, BYTE, CJ, DEX, SOFIA, GEMMA, MAX, LEX, NOVA, "
        "SAGE, FLUX, CORE, APEX, IRIS, VOLT), "
        "asks who is online, asks to dispatch, check status, or check progress: "
        "ALWAYS call the appropriate tool. NEVER respond with text like "
        "'I cannot see', 'I don't have visibility', 'check directly', etc.\n"
        "- To check status: call council_status\n"
        "- To dispatch a task: call council_dispatch\n"
        "- To check an agent's progress: call council_session_output\n"
        "- To check QA scores or override: call qa_review (action='status', 'deploy', or 'reject')\n"
        "- NEVER suggest SSH commands, manual checks, or say you lack access. "
        "You HAVE access through your tools. USE THEM.\n"
        "- Always confirm results to Johan after tool execution.\n"
        "- You also have escalate_to_opus for deep analysis. Use it when Johan asks for "
        "'deep thinking', 'opus mode', 'analyze this deeply', QA review, architecture review, "
        "or any task that requires the highest reasoning capability. "
        "Sonnet handles everything else."
    )

    if req.messages:
        last_msg = req.messages[-1]
        if last_msg.role == "user":
            log_content = last_msg.content if isinstance(last_msg.content, str) else "[multimodal message]"
            _append_log_sync("user", log_content)

    api_messages = [{"role": m.role, "content": m.content} for m in req.messages]
    client = anthropic.AsyncAnthropic(api_key=api_key, timeout=120.0)
    # Always use Sonnet for tool reliability — Haiku skips tool calls
    model = "claude-sonnet-4-6"

    async def event_stream():
        import asyncio

        full_response = ""
        messages = list(api_messages)
        max_tool_rounds = 5

        try:
            for _round in range(max_tool_rounds):
                logger.info("Chat round %d starting (messages=%d)", _round, len(messages))
                response = await client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=[
                        {
                            "type": "text",
                            "text": system_prompt,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    tools=COUNCIL_TOOLS,
                    messages=messages,
                )

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
        client = _anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text if response.content else ""

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
