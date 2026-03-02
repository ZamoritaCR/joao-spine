"""Voice command endpoints — transcribe, parse intent, dispatch."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, UploadFile
from pydantic import BaseModel

from middleware.auth import require_api_key, validate_agent_name, ALLOWED_AGENTS
from models.schemas import (
    AgentOutputRecord,
    IdeaVaultRecord,
    SessionLogRecord,
    VoiceCommandResponse,
    VoiceIntent,
)
from services import ai_processor, dispatch, supabase_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/joao/voice", tags=["voice"], dependencies=[Depends(require_api_key)])


class ParseRequest(BaseModel):
    text: str


@router.post("/transcribe")
async def transcribe(audio: UploadFile):
    """Transcribe audio blob via OpenAI Whisper."""
    audio_bytes = await audio.read()
    result = await ai_processor.transcribe_audio(audio_bytes, audio.filename or "audio.webm")
    return result


@router.post("/parse")
async def parse(req: ParseRequest):
    """Parse transcribed text into a structured intent."""
    intent = await ai_processor.parse_intent(req.text)
    return intent


@router.post("/command", response_model=VoiceCommandResponse)
async def voice_command(audio: UploadFile):
    """Main voice command endpoint: transcribe → parse → execute."""
    t0 = time.time()

    # 1. Transcribe
    audio_bytes = await audio.read()
    transcription = await ai_processor.transcribe_audio(audio_bytes, audio.filename or "audio.webm")
    transcript = transcription["text"]

    # 2. Parse intent
    intent = await ai_processor.parse_intent(transcript)

    # 3. Execute based on intent
    result = await _execute_intent(intent, transcript)

    duration_ms = int((time.time() - t0) * 1000)

    # Log to session_log
    try:
        await supabase_client.insert_session_log(
            SessionLogRecord(
                endpoint="/joao/voice/command",
                action=f"voice_{intent.intent}",
                input_summary=transcript[:200],
                output_summary=str(result)[:200],
                status="ok",
                duration_ms=duration_ms,
            )
        )
    except Exception:
        logger.warning("Failed to log voice command to session_log")

    return VoiceCommandResponse(
        transcript=transcript,
        intent=intent,
        result=result,
    )


async def _execute_intent(intent: VoiceIntent, transcript: str) -> dict:
    """Route intent to the appropriate service."""
    if intent.intent == "dispatch":
        return await _handle_dispatch(intent)
    elif intent.intent == "status":
        return await _handle_status()
    elif intent.intent == "check":
        return await _handle_check(intent)
    elif intent.intent == "idea":
        return await _handle_idea(intent, transcript)
    else:
        return {
            "status": "unknown",
            "response": "I didn't catch that. Try: 'Tell BYTE to...', 'Who's online', or 'I have an idea...'",
        }


async def _handle_dispatch(intent: VoiceIntent) -> dict:
    if not intent.agent:
        return {"status": "error", "response": "No agent specified. Try: 'Tell BYTE to...'"}

    try:
        validate_agent_name(intent.agent)
    except ValueError:
        return {"status": "error", "response": f"Unknown agent '{intent.agent}'. Available: {sorted(ALLOWED_AGENTS)}"}

    task = intent.task or "No task specified"
    result = await dispatch.dispatch_command(
        session_name=intent.agent.upper(),
        command=task,
        wait=False,
    )

    try:
        await supabase_client.insert_agent_output(
            AgentOutputRecord(
                session_name=intent.agent.upper(),
                command=task,
                output=result.get("output", ""),
                status=result["status"],
                metadata={"source": "voice", "priority": intent.priority},
            )
        )
    except Exception:
        logger.warning("Failed to log agent output")

    return {
        "status": "dispatched",
        "agent": intent.agent.upper(),
        "task": task,
        "dispatch_status": result["status"],
    }


async def _handle_status() -> dict:
    try:
        ssh_check, tmux_check = await dispatch.health_check()
        agents = {name: name in tmux_check.sessions for name in sorted(ALLOWED_AGENTS)}
        return {
            "status": "ok",
            "agents": agents,
            "ssh_ok": ssh_check.ok,
            "sessions": tmux_check.sessions,
        }
    except Exception as e:
        return {"status": "error", "response": f"Could not reach Council: {e}"}


async def _handle_check(intent: VoiceIntent) -> dict:
    if not intent.agent:
        return {"status": "error", "response": "Which agent? Try: 'Check on BYTE'"}

    try:
        validate_agent_name(intent.agent)
    except ValueError:
        return {"status": "error", "response": f"Unknown agent '{intent.agent}'"}

    result = await dispatch.dispatch_command(
        session_name=intent.agent.upper(),
        command=f"tmux capture-pane -t {intent.agent.upper()} -p",
        wait=True,
    )
    return {
        "status": "ok",
        "agent": intent.agent.upper(),
        "output": result.get("output", "No output captured"),
    }


async def _handle_idea(intent: VoiceIntent, transcript: str) -> dict:
    try:
        row = await supabase_client.insert_idea_vault(
            IdeaVaultRecord(
                source="voice",
                title=intent.task or "Voice idea",
                content=transcript,
                summary=intent.task or transcript[:200],
                tags=[intent.project] if intent.project else [],
                metadata={"priority": intent.priority, "project": intent.project},
            )
        )
        return {
            "status": "saved",
            "idea_id": row.get("id", "n/a"),
            "title": intent.task or "Voice idea",
        }
    except Exception as e:
        return {"status": "error", "response": f"Failed to save idea: {e}"}
