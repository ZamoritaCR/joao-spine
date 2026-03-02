"""OpenAI JSON-mode summarize/extract for all content types."""

from __future__ import annotations

import json
import logging
import os

from openai import AsyncOpenAI

from models.schemas import AIResult, VoiceIntent

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


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


async def transcribe_audio(audio_bytes: bytes, filename: str = "audio.webm") -> dict:
    """Transcribe audio using OpenAI Whisper API."""
    client = _get_client()
    content_type = "audio/webm" if filename.endswith(".webm") else "audio/ogg"
    resp = await client.audio.transcriptions.create(
        model="whisper-1",
        file=(filename, audio_bytes, content_type),
    )
    return {"text": resp.text, "language": "en"}


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


async def process_text(text: str, context: str = "") -> AIResult:
    system = (
        "You are a text analyst. Summarize and extract key information. "
        + _JSON_SCHEMA_INSTRUCTION
    )
    user_msg = text
    if context:
        user_msg += f"\nContext: {context}"
    return await _call_openai(system, user_msg)
