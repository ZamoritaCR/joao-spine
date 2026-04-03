"""AI Arena -- Claude vs GPT side-by-side chat with debate mode.

POST /arena/chat   -- send message, get parallel Claude + GPT responses
POST /arena/debate -- send each model the other's response to critique
POST /arena/prefer -- log preference to Supabase
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from services.supabase_client import get_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/arena", tags=["arena"])

# In-memory conversation history per session
# Structure: {session_id: {"claude": [messages], "gpt": [messages], "system_prompt": str}}
_sessions: dict[str, dict[str, Any]] = {}

MAX_SESSIONS = 50


def _prune_sessions():
    """Keep session count under MAX_SESSIONS by removing oldest."""
    if len(_sessions) > MAX_SESSIONS:
        oldest = sorted(_sessions.keys())[:len(_sessions) - MAX_SESSIONS]
        for k in oldest:
            del _sessions[k]


def _get_session(session_id: str) -> dict[str, Any]:
    if session_id not in _sessions:
        _prune_sessions()
        _sessions[session_id] = {
            "claude": [],
            "gpt": [],
            "system_prompt": "You are a helpful, intelligent assistant. Be concise and direct.",
        }
    return _sessions[session_id]


def _sb():
    """Get Supabase client, return None if unavailable."""
    try:
        return get_client()
    except Exception:
        return None


# -- Models ----------------------------------------------------------------

class ChatRequest(BaseModel):
    session_id: str
    message: str
    system_prompt: str = ""
    claude_model: str = "claude-sonnet-4-20250514"
    gpt_model: str = "gpt-4o"


class DebateRequest(BaseModel):
    session_id: str
    claude_response: str
    gpt_response: str
    original_prompt: str
    claude_model: str = "claude-sonnet-4-20250514"
    gpt_model: str = "gpt-4o"


class PreferenceRequest(BaseModel):
    session_id: str
    user_input: str
    claude_response: str
    gpt_response: str
    preferred_model: str
    debate_claude: str = ""
    debate_gpt: str = ""


# -- Claude API call -------------------------------------------------------

async def _call_claude(
    messages: list[dict],
    system_prompt: str,
    model: str,
) -> str:
    """Call Anthropic Messages API."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "[ERROR] ANTHROPIC_API_KEY not set"

    # Convert messages to Anthropic format (role: user/assistant)
    anthropic_messages = []
    for m in messages:
        anthropic_messages.append({
            "role": m["role"],
            "content": m["content"],
        })

    payload = {
        "model": model,
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": anthropic_messages,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
        )

    if resp.status_code != 200:
        logger.error("Claude API error %d: %s", resp.status_code, resp.text[:500])
        return f"[ERROR] Claude API returned {resp.status_code}: {resp.text[:200]}"

    data = resp.json()
    # Extract text from content blocks
    text_parts = []
    for block in data.get("content", []):
        if block.get("type") == "text":
            text_parts.append(block["text"])
    return "\n".join(text_parts) or "[No response]"


# -- GPT API call ----------------------------------------------------------

async def _call_gpt(
    messages: list[dict],
    system_prompt: str,
    model: str,
) -> str:
    """Call OpenAI Chat Completions API."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return "[ERROR] OPENAI_API_KEY not set"

    # Build OpenAI messages with system prompt
    openai_messages = [{"role": "system", "content": system_prompt}]
    for m in messages:
        openai_messages.append({
            "role": m["role"],
            "content": m["content"],
        })

    payload = {
        "model": model,
        "max_tokens": 4096,
        "messages": openai_messages,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if resp.status_code != 200:
        logger.error("GPT API error %d: %s", resp.status_code, resp.text[:500])
        return f"[ERROR] GPT API returned {resp.status_code}: {resp.text[:200]}"

    data = resp.json()
    choices = data.get("choices", [])
    if choices:
        return choices[0].get("message", {}).get("content", "[No response]")
    return "[No response]"


# -- POST /arena/chat ------------------------------------------------------

@router.post("/chat")
async def arena_chat(req: ChatRequest):
    session = _get_session(req.session_id)

    # Update system prompt if provided
    if req.system_prompt:
        session["system_prompt"] = req.system_prompt
    system_prompt = session["system_prompt"]

    # Add user message to both histories
    user_msg = {"role": "user", "content": req.message}
    session["claude"].append(user_msg)
    session["gpt"].append(user_msg)

    # Call both APIs in parallel
    claude_task = _call_claude(session["claude"], system_prompt, req.claude_model)
    gpt_task = _call_gpt(session["gpt"], system_prompt, req.gpt_model)

    claude_response, gpt_response = await asyncio.gather(
        claude_task, gpt_task, return_exceptions=True
    )

    # Handle exceptions
    if isinstance(claude_response, Exception):
        claude_response = f"[ERROR] {claude_response}"
    if isinstance(gpt_response, Exception):
        gpt_response = f"[ERROR] {gpt_response}"

    # Add assistant responses to respective histories
    session["claude"].append({"role": "assistant", "content": claude_response})
    session["gpt"].append({"role": "assistant", "content": gpt_response})

    return {
        "claude_response": claude_response,
        "gpt_response": gpt_response,
        "claude_model": req.claude_model,
        "gpt_model": req.gpt_model,
        "session_id": req.session_id,
    }


# -- POST /arena/debate ----------------------------------------------------

@router.post("/debate")
async def arena_debate(req: DebateRequest):
    session = _get_session(req.session_id)
    system_prompt = session["system_prompt"]

    debate_prompt_for_claude = (
        f"The user asked: \"{req.original_prompt}\"\n\n"
        f"Your response was:\n{req.claude_response}\n\n"
        f"Another AI (GPT) responded with:\n{req.gpt_response}\n\n"
        "Critique the other AI's response. What did it get right? What did it get wrong? "
        "Where does your answer differ and why is your approach better or worse? Be honest and direct."
    )

    debate_prompt_for_gpt = (
        f"The user asked: \"{req.original_prompt}\"\n\n"
        f"Your response was:\n{req.gpt_response}\n\n"
        f"Another AI (Claude) responded with:\n{req.claude_response}\n\n"
        "Critique the other AI's response. What did it get right? What did it get wrong? "
        "Where does your answer differ and why is your approach better or worse? Be honest and direct."
    )

    # Use fresh message lists for debate (don't pollute main history)
    claude_debate_msgs = [{"role": "user", "content": debate_prompt_for_claude}]
    gpt_debate_msgs = [{"role": "user", "content": debate_prompt_for_gpt}]

    claude_task = _call_claude(claude_debate_msgs, system_prompt, req.claude_model)
    gpt_task = _call_gpt(gpt_debate_msgs, system_prompt, req.gpt_model)

    claude_debate, gpt_debate = await asyncio.gather(
        claude_task, gpt_task, return_exceptions=True
    )

    if isinstance(claude_debate, Exception):
        claude_debate = f"[ERROR] {claude_debate}"
    if isinstance(gpt_debate, Exception):
        gpt_debate = f"[ERROR] {gpt_debate}"

    return {
        "claude_debate": claude_debate,
        "gpt_debate": gpt_debate,
    }


# -- POST /arena/prefer ----------------------------------------------------

@router.post("/prefer")
async def arena_prefer(req: PreferenceRequest):
    sb = _sb()
    if not sb:
        logger.warning("Supabase unavailable -- preference not logged")
        return {"status": "skipped", "reason": "supabase_unavailable"}

    row = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_input": req.user_input[:2000],
        "claude_response": req.claude_response[:5000],
        "gpt_response": req.gpt_response[:5000],
        "preferred_model": req.preferred_model,
        "debate_claude": req.debate_claude[:5000] if req.debate_claude else None,
        "debate_gpt": req.debate_gpt[:5000] if req.debate_gpt else None,
    }

    try:
        result = sb.table("arena_preferences").insert(row).execute()
        return {"status": "logged", "id": row["id"]}
    except Exception as e:
        err = str(e)
        if "does not exist" in err or "Could not find" in err:
            logger.warning("arena_preferences table not found -- run migration")
            return {"status": "skipped", "reason": "table_not_found"}
        logger.error("Failed to log preference: %s", e)
        return {"status": "error", "reason": str(e)[:200]}


# -- GET /arena (serve HTML) -----------------------------------------------

@router.get("", include_in_schema=False)
async def arena_page():
    from pathlib import Path
    html_path = Path(__file__).parent.parent / "static" / "arena.html"
    if html_path.exists():
        from fastapi.responses import FileResponse
        return FileResponse(html_path, media_type="text/html")
    raise HTTPException(status_code=404, detail="Arena page not found")
