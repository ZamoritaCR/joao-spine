"""Real-time voice chat — WebSocket + REST endpoints for JOAO voice interface."""

from __future__ import annotations

import base64
import json
import logging
import os
import uuid
from collections import defaultdict

import anthropic
import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["voice-chat"])

# ── Session store (in-memory) ────────────────────────────────────────────────
_sessions: dict[str, list[dict]] = defaultdict(list)

JOAO_SYSTEM_PROMPT = (
    "You are JOÃO, Johan Zamora's personal AI exocortex. "
    "You know Johan — his projects, his style, his Council of 16 AI agents, "
    "TAOP products, his ADHD superpower. You are direct, energetic, no fluff. "
    "You remember context within the conversation. You are the brain behind "
    "The Art of The Possible. Keep voice responses concise — 2-3 sentences max "
    "unless the user asks for detail."
)


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""


class TTSRequest(BaseModel):
    text: str


# ── REST: text chat ──────────────────────────────────────────────────────────

@router.post("/api/chat")
async def chat(req: ChatRequest):
    """REST endpoint for text chat with streaming disabled."""
    session_id = req.session_id or str(uuid.uuid4())
    history = _sessions[session_id]
    history.append({"role": "user", "content": req.message})

    # Keep last 40 messages to avoid token overflow
    if len(history) > 40:
        history[:] = history[-40:]

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        system=JOAO_SYSTEM_PROMPT,
        messages=history,
    )
    reply = response.content[0].text
    history.append({"role": "assistant", "content": reply})

    return {"reply": reply, "session_id": session_id}


# ── REST: TTS via ElevenLabs ─────────────────────────────────────────────────

@router.post("/api/tts")
async def tts(req: TTSRequest):
    """Generate TTS audio. Returns base64 mp3 or null if ElevenLabs not configured."""
    el_key = os.getenv("ELEVENLABS_API_KEY")
    if not el_key:
        return {"audio_b64": None, "format": "mp3", "error": "ELEVENLABS_API_KEY not configured"}

    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": el_key, "Content-Type": "application/json"},
            json={
                "text": req.text,
                "model_id": "eleven_turbo_v2_5",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
        )
    if resp.status_code != 200:
        return {"audio_b64": None, "format": "mp3", "error": f"ElevenLabs {resp.status_code}"}

    audio_b64 = base64.b64encode(resp.content).decode()
    return {"audio_b64": audio_b64, "format": "mp3"}


# ── REST: STT via Deepgram ───────────────────────────────────────────────────

@router.post("/api/stt")
async def stt(audio: bytes):
    """Server-side STT fallback. Browser Speech API is preferred."""
    dg_key = os.getenv("DEEPGRAM_API_KEY")
    if not dg_key:
        return {"transcript": None, "error": "DEEPGRAM_API_KEY not configured — use browser Speech API"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.deepgram.com/v1/listen?model=nova-3&smart_format=true",
            headers={"Authorization": f"Token {dg_key}", "Content-Type": "audio/wav"},
            content=audio,
        )
    if resp.status_code != 200:
        return {"transcript": None, "error": f"Deepgram {resp.status_code}"}

    transcript = resp.json()["results"]["channels"][0]["alternatives"][0]["transcript"]
    return {"transcript": transcript}


# ── WebSocket: streaming voice chat ─────────────────────────────────────────

@router.websocket("/ws/voice")
async def voice_ws(ws: WebSocket):
    """
    WebSocket protocol:
    Client sends JSON:
      {"type": "message", "text": "...", "session_id": "..."}
      {"type": "ping"}

    Server sends JSON:
      {"type": "token", "text": "..."}           — streaming token
      {"type": "done", "full_text": "..."}        — end of response
      {"type": "tts", "audio_b64": "...", "format": "mp3"}  — TTS audio
      {"type": "error", "message": "..."}
      {"type": "pong"}
    """
    await ws.accept()
    logger.info("Voice WebSocket connected")

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = msg.get("type", "")

            if msg_type == "ping":
                await ws.send_json({"type": "pong"})
                continue

            if msg_type != "message":
                await ws.send_json({"type": "error", "message": f"Unknown type: {msg_type}"})
                continue

            text = msg.get("text", "").strip()
            if not text:
                await ws.send_json({"type": "error", "message": "Empty message"})
                continue

            session_id = msg.get("session_id", str(uuid.uuid4()))
            history = _sessions[session_id]
            history.append({"role": "user", "content": text})

            if len(history) > 40:
                history[:] = history[-40:]

            # Stream Claude response
            full_text = ""
            try:
                client = anthropic.Anthropic()
                with client.messages.stream(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1024,
                    system=JOAO_SYSTEM_PROMPT,
                    messages=history,
                ) as stream:
                    for token in stream.text_stream:
                        full_text += token
                        await ws.send_json({"type": "token", "text": token})

                history.append({"role": "assistant", "content": full_text})
                await ws.send_json({"type": "done", "full_text": full_text, "session_id": session_id})

                # Attempt TTS
                el_key = os.getenv("ELEVENLABS_API_KEY")
                if el_key:
                    try:
                        voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
                        async with httpx.AsyncClient(timeout=30.0) as http:
                            tts_resp = await http.post(
                                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                                headers={"xi-api-key": el_key, "Content-Type": "application/json"},
                                json={
                                    "text": full_text[:500],
                                    "model_id": "eleven_turbo_v2_5",
                                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                                },
                            )
                        if tts_resp.status_code == 200:
                            audio_b64 = base64.b64encode(tts_resp.content).decode()
                            await ws.send_json({"type": "tts", "audio_b64": audio_b64, "format": "mp3"})
                    except Exception as e:
                        logger.warning("TTS failed: %s", e)

            except Exception as e:
                logger.exception("Claude streaming error")
                await ws.send_json({"type": "error", "message": str(e)})

    except WebSocketDisconnect:
        logger.info("Voice WebSocket disconnected")
    except Exception as e:
        logger.exception("Voice WebSocket error: %s", e)
