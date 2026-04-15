"""OpenAI JSON-mode summarize/extract for all content types."""

from __future__ import annotations

import json
import logging
import os

from groq import AsyncGroq
from openai import AsyncOpenAI

from models.schemas import AIResult, VoiceIntent

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None
_groq_client: AsyncGroq | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def _get_groq_client() -> AsyncGroq | None:
    global _groq_client
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return None
    if _groq_client is None:
        _groq_client = AsyncGroq(api_key=api_key)
    return _groq_client


_JSON_SCHEMA_INSTRUCTION = (
    "Respond ONLY with a JSON object containing: "
    '"title" (string), "summary" (string, 2-3 sentences), '
    '"tags" (array of strings, max 5), "key_points" (array of strings).'
)


async def _call_openai(system: str, user_content: str | list) -> AIResult:
    client = _get_client()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]
    resp = await client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    raw = resp.choices[0].message.content or "{}"
    data = json.loads(raw)
    return AIResult(
        title=data.get("title", "Untitled"),
        summary=data.get("summary", ""),
        tags=data.get("tags", []),
        key_points=data.get("key_points", []),
    )


async def process_audio(audio_url: str, context: str = "") -> AIResult:
    system = (
        "You are an audio content analyst. The user provides a transcription URL or text. "
        "Extract the key information. " + _JSON_SCHEMA_INSTRUCTION
    )
    user_msg = f"Audio URL: {audio_url}"
    if context:
        user_msg += f"\nContext: {context}"
    return await _call_openai(system, user_msg)


async def process_meeting(transcript: str, participants: list[str] | None = None, context: str = "") -> AIResult:
    system = (
        "You are a meeting analyst. Summarize the meeting, extract action items and decisions. "
        + _JSON_SCHEMA_INSTRUCTION
    )
    user_msg = f"Transcript:\n{transcript}"
    if participants:
        user_msg += f"\nParticipants: {', '.join(participants)}"
    if context:
        user_msg += f"\nContext: {context}"
    return await _call_openai(system, user_msg)


async def process_vision(image_url: str, prompt: str = "") -> AIResult:
    system = (
        "You are a visual analyst. Describe and extract key information from images. "
        + _JSON_SCHEMA_INSTRUCTION
    )
    user_content = [
        {"type": "image_url", "image_url": {"url": image_url}},
    ]
    if prompt:
        user_content.insert(0, {"type": "text", "text": prompt})
    return await _call_openai(system, user_content)


_MIME_MAP = {
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".mp4": "audio/mp4",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
}


async def transcribe_audio(audio_bytes: bytes, filename: str = "audio.webm") -> dict:
    """Transcribe audio using Groq Whisper (primary) with OpenAI Whisper fallback."""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ".webm"
    content_type = _MIME_MAP.get(ext, "audio/webm")

    groq = _get_groq_client()
    if groq is not None:
        try:
            resp = await groq.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=(filename, audio_bytes, content_type),
                response_format="json",
            )
            return {"text": resp.text, "language": getattr(resp, "language", "en"), "provider": "groq"}
        except Exception as e:
            logger.warning("Groq Whisper transcription failed, falling back to OpenAI: %s", e)

    # OpenAI fallback
    client = _get_client()
    try:
        resp = await client.audio.transcriptions.create(
            model="whisper-1",
            file=(filename, audio_bytes, content_type),
        )
        return {"text": resp.text, "language": "en", "provider": "openai"}
    except Exception as e:
        logger.error("OpenAI Whisper transcription failed: %s", e)
        raise


_INTENT_SYSTEM_PROMPT = """You are JOAO's intent parser. Given a voice command from Johan, extract:
- intent: "dispatch", "status", "check", "idea", or "unknown"
- agent: BYTE, ARIA, CJ, SOFIA, DEX, GEMMA, MAX (if dispatch or check)
- task: the task description (if dispatch or idea)
- priority: normal, urgent, critical (default normal)
- project: project name if mentioned

Respond ONLY in JSON. No markdown, no explanation.

Examples:
"Tell BYTE to fix the login page" → {"intent":"dispatch","agent":"BYTE","task":"fix the login page","priority":"normal","project":null}
"Who's online" → {"intent":"status","agent":null,"task":null,"priority":"normal","project":null}
"Check on SOFIA" → {"intent":"check","agent":"SOFIA","task":null,"priority":"normal","project":null}
"I have an idea for a dark mode on dopamine watch" → {"intent":"idea","agent":null,"task":"dark mode on dopamine watch","priority":"normal","project":"dopamine.watch"}"""


async def parse_intent(text: str) -> VoiceIntent:
    """Parse voice command text into a structured intent."""
    client = _get_client()
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _INTENT_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        return VoiceIntent(
            intent=data.get("intent", "unknown"),
            agent=data.get("agent"),
            task=data.get("task"),
            priority=data.get("priority", "normal"),
            project=data.get("project"),
        )
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.warning("Intent parse failed: %s", e)
        return VoiceIntent(intent="unknown")
    except Exception:
        logger.exception("OpenAI intent parse error")
        return VoiceIntent(intent="unknown")


async def process_text(text: str, context: str = "") -> AIResult:
    system = (
        "You are a text analyst. Summarize and extract key information. "
        + _JSON_SCHEMA_INSTRUCTION
    )
    user_msg = text
    if context:
        user_msg += f"\nContext: {context}"
    return await _call_openai(system, user_msg)
