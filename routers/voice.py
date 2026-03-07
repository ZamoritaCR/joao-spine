"""Voice command endpoints — transcribe, parse intent, dispatch + LiveKit token."""

from __future__ import annotations

import logging
import os
import time
import uuid

import httpx

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from middleware.auth import require_api_key, validate_agent_name, validate_command_safety, ALLOWED_AGENTS
from models.schemas import (
    AgentOutputRecord,
    IdeaVaultRecord,
    SessionLogRecord,
    VoiceCommandResponse,
    VoiceIntent,
)
from services import ai_processor, dispatch, supabase_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/joao/voice", tags=["voice"])


class ParseRequest(BaseModel):
    text: str


@router.post("/transcribe", dependencies=[Depends(require_api_key)])
async def transcribe(audio: UploadFile):
    """Transcribe audio blob via OpenAI Whisper."""
    audio_bytes = await audio.read()
    result = await ai_processor.transcribe_audio(audio_bytes, audio.filename or "audio.webm")
    return result


@router.post("/parse", dependencies=[Depends(require_api_key)])
async def parse(req: ParseRequest):
    """Parse transcribed text into a structured intent."""
    intent = await ai_processor.parse_intent(req.text)
    return intent


@router.post("/command", response_model=VoiceCommandResponse, dependencies=[Depends(require_api_key)])
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


@router.post("/token")
async def voice_token():
    """Generate a LiveKit access token for browser voice sessions. No auth required."""
    from livekit import api as lkapi

    api_key = os.environ.get("LIVEKIT_API_KEY", "")
    api_secret = os.environ.get("LIVEKIT_API_SECRET", "")
    livekit_url = os.environ.get("LIVEKIT_URL", "")

    if not api_key or not api_secret:
        logger.error("LIVEKIT_API_KEY or LIVEKIT_API_SECRET not set")
        raise HTTPException(status_code=500, detail="LiveKit not configured")

    room_name = f"joao-voice-{uuid.uuid4().hex[:8]}"
    identity = f"web-user-{uuid.uuid4().hex[:6]}"

    jwt_token = (
        lkapi.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name("JOAO User")
        .with_grants(lkapi.VideoGrants(
            room_join=True,
            room=room_name,
        ))
        .to_jwt()
    )

    return {
        "token": jwt_token,
        "room": room_name,
        "livekit_url": livekit_url,
    }


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

    try:
        validate_command_safety(task)
    except ValueError:
        return {"status": "error", "response": "Task contains disallowed characters"}

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

    result = await dispatch.capture_pane(session_name=intent.agent.upper())
    return {
        "status": result.get("status", "ok"),
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


@router.post("/audio")
async def voice_audio_pipeline(file: UploadFile):
    """STT → Claude brain → TTS pipeline. Returns transcript, reply, and base64 audio."""
    import base64

    audio_bytes = await file.read()

    # 1. Deepgram Nova-3 STT
    dg_key = os.getenv("DEEPGRAM_API_KEY")
    if not dg_key:
        raise HTTPException(status_code=503, detail="DEEPGRAM_API_KEY not configured")

    async with httpx.AsyncClient(timeout=30.0) as client:
        dg_resp = await client.post(
            "https://api.deepgram.com/v1/listen?model=nova-3&smart_format=true",
            headers={"Authorization": f"Token {dg_key}", "Content-Type": "audio/wav"},
            content=audio_bytes,
        )
    if dg_resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Deepgram error: {dg_resp.text}")
    try:
        transcript = dg_resp.json()["results"]["channels"][0]["alternatives"][0]["transcript"]
    except (KeyError, IndexError) as e:
        raise HTTPException(status_code=502, detail=f"Deepgram parse error: {e}")

    if not transcript.strip():
        return {"transcript": "", "reply": "I didn't catch that.", "audio_b64": None, "format": "mp3"}

    # 2. Claude brain
    import anthropic as _anthropic

    claude = _anthropic.Anthropic()
    msg = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        system="You are JOÃO, Johan's AI sidekick. Be concise — this is a voice response.",
        messages=[{"role": "user", "content": transcript}],
    )
    reply = msg.content[0].text

    # 3. ElevenLabs TTS
    el_key = os.getenv("ELEVENLABS_API_KEY")
    if not el_key:
        raise HTTPException(status_code=503, detail="ELEVENLABS_API_KEY not configured")

    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    async with httpx.AsyncClient(timeout=30.0) as client:
        tts_resp = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": el_key, "Content-Type": "application/json"},
            json={
                "text": reply,
                "model_id": "eleven_turbo_v2_5",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
        )
    if tts_resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"ElevenLabs error: {tts_resp.text}")

    audio_b64 = base64.b64encode(tts_resp.content).decode()
    return {"transcript": transcript, "reply": reply, "audio_b64": audio_b64, "format": "mp3"}
