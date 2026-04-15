"""Telegram webhook endpoints — inbound updates → LLMRouter (Haiku fallback) → reply.

Routes:
  POST /joao/telegram/webhook   — Telegram sends updates here
  POST /joao/telegram/register  — register this webhook with Telegram
  GET  /joao/telegram/status    — current webhook info

Slash commands handled:
  /status  — GreenGeeks health + spine uptime
  /radar   — latest SCOUT radar intel
  /spark   — Spark brief summary

AI routing:
  Primary: LLMRouter chat task (Ollama phi4 or OpenRouter, env-driven)
  Fallback: Claude Haiku (Anthropic API) if LLMRouter fails
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from services import llm_router

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/joao", tags=["telegram"])

_TELEGRAM_API = "https://api.telegram.org"
_HEALTH_JSON = Path.home() / "research" / "GREENGEEKS_HEALTH.json"
_SPARK_BRIEF = Path.home() / "research" / "SPARK_BRIEF.md"
_SCOUT_INTEL = Path.home() / "SCOUT_INTEL.md"

_start_time = time.time()

# ── Helpers ──────────────────────────────────────────────────────────────────

def _bot_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not set")
    return token


async def _tg_post(method: str, payload: dict) -> dict:
    token = _bot_token()
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{_TELEGRAM_API}/bot{token}/{method}", json=payload)
        return resp.json()


async def _send_reply(chat_id: int | str, text: str) -> None:
    if len(text) > 4096:
        text = text[:4093] + "..."
    try:
        await _tg_post("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        })
    except Exception:
        # retry as plain text
        try:
            await _tg_post("sendMessage", {"chat_id": chat_id, "text": text})
        except Exception:
            logger.exception("Failed to send Telegram reply to %s", chat_id)


def _haiku_client() -> anthropic.AsyncAnthropic:
    """Fallback Haiku client (used only when LLMRouter fails)."""
    return anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))


# ── Slash command handlers ────────────────────────────────────────────────────

async def _handle_status() -> str:
    """Return GreenGeeks health + spine uptime."""
    uptime_s = int(time.time() - _start_time)
    h, rem = divmod(uptime_s, 3600)
    m, s = divmod(rem, 60)
    uptime_str = f"{h}h {m}m {s}s"

    if not _HEALTH_JSON.exists():
        return (
            f"*JOAO STATUS*\n"
            f"Spine uptime: `{uptime_str}`\n"
            f"GreenGeeks: no health data yet (monitor not run)"
        )

    try:
        health = json.loads(_HEALTH_JSON.read_text())
    except Exception:
        return f"*JOAO STATUS*\nSpine uptime: `{uptime_str}`\nGreenGeeks: error reading health data"

    overall = health.get("overall", "unknown").upper()
    last_check = health.get("last_check", "unknown")[:19].replace("T", " ")

    domain_lines = []
    for d in health.get("domains", []):
        icon = "UP" if d.get("up") else "DOWN"
        ms = d.get("response_ms")
        ms_str = f" ({ms}ms)" if ms else ""
        domain_lines.append(f"  {d['domain']}: {icon}{ms_str}")

    disk = health.get("disk")
    disk_str = f"Disk: {disk['used_pct']}% ({disk['used_mb']}/{disk['total_mb']} MB)" if disk else "Disk: unknown"

    return (
        f"*JOAO STATUS*\n"
        f"Spine uptime: `{uptime_str}`\n\n"
        f"*GreenGeeks* [{overall}]\n"
        f"Last check: {last_check}\n"
        f"{disk_str}\n"
        + "\n".join(domain_lines)
    )


async def _handle_radar() -> str:
    """Return latest SCOUT radar intel."""
    if _SCOUT_INTEL.exists():
        content = _SCOUT_INTEL.read_text()
        # Return first 1500 chars of the intel file
        preview = content[:1500]
        if len(content) > 1500:
            preview += "\n...(truncated)"
        return f"*RADAR INTEL*\n\n{preview}"
    return "*RADAR INTEL*\nNo intel available. Run SCOUT scan first."


async def _handle_spark() -> str:
    """Return Spark brief."""
    if _SPARK_BRIEF.exists():
        content = _SPARK_BRIEF.read_text()
        preview = content[:1500]
        if len(content) > 1500:
            preview += "\n...(truncated)"
        return f"*SPARK BRIEF*\n\n{preview}"
    return "*SPARK BRIEF*\nNo brief available."


_SLASH_COMMANDS = {
    "/status": _handle_status,
    "/radar": _handle_radar,
    "/spark": _handle_spark,
}

_SYSTEM_PROMPT = (
    "You are JOAO, a concise personal AI assistant. "
    "Answer in 1-3 sentences unless more detail is explicitly needed. "
    "You can help with tasks, questions, and status checks. "
    "Keep replies short — this is a Telegram chat."
)


async def _process_message(text: str, chat_id: int | str) -> None:
    """Route slash commands or send to Claude Haiku, then reply."""
    text = text.strip()

    # Check for slash commands (including with args, e.g. /status@bot)
    cmd = text.split()[0].split("@")[0].lower() if text else ""
    if cmd in _SLASH_COMMANDS:
        reply = await _SLASH_COMMANDS[cmd]()
        await _send_reply(chat_id, reply)
        return

    # Cockpit commands
    _COCKPIT_CMDS = {
        "/focus": "focus", "/chill": "chill", "/hyperfocus": "hyperfocus",
        "/lowenergy": "low_energy", "/sleep": "sleep", "/morning": "morning",
        "/lights on": "lights_on", "/lights off": "lights_off",
    }
    text_lower = text.lower().strip()
    cockpit_scene = _COCKPIT_CMDS.get(cmd) or _COCKPIT_CMDS.get(text_lower)
    if cockpit_scene:
        try:
            from services.home_assistant import cockpit as ha, SCENES
            method = getattr(ha, SCENES[cockpit_scene])
            result = await method()
            if result.get("status") == "offline":
                reply = "Pi is offline. Check power at 192.168.0.31."
            else:
                reply = f"{cockpit_scene.replace('_', ' ').title()} mode activated."
        except Exception as e:
            reply = f"Scene failed: {e}"
        await _send_reply(chat_id, reply)
        return

    if cmd == "/cockpit":
        try:
            from services.home_assistant import cockpit as ha
            ping = await ha.ping()
            online = ping.get("status") == "online"
            dev_count = 0
            if online:
                states = await ha.get_states()
                dev_count = len(states) if isinstance(states, list) else 0
            reply = (
                f"COCKPIT STATUS\n"
                f"Pi: {ping.get('status', 'offline')}\n"
                f"Devices: {dev_count} entities\n"
                f"HA: {ping.get('version', 'unavailable')}\n"
                f"Last scene: {ha.last_scene}"
            )
        except Exception as e:
            reply = f"Cockpit status error: {e}"
        await _send_reply(chat_id, reply)
        return

    # Primary: LLMRouter chat task (Ollama or OpenRouter, env-driven)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    try:
        reply = await llm_router.complete(messages, task_type="chat", max_tokens=512)
        if not reply:
            reply = "No response."
    except Exception as router_err:
        logger.warning("LLMRouter failed (%s), falling back to Claude Haiku", router_err)
        # Fallback: Claude Haiku via Anthropic API
        try:
            client = _haiku_client()
            resp = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": text}],
            )
            reply = resp.content[0].text if resp.content else "No response."
        except Exception as haiku_err:
            logger.exception("Claude Haiku fallback also failed")
            reply = f"AI error: {haiku_err}"

    await _send_reply(chat_id, reply)


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> dict[str, str]:
    """Receive Telegram updates and process them."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    logger.debug("Telegram update received: %s", json.dumps(body)[:200])

    message = body.get("message") or body.get("edited_message")
    if not message:
        # Not a message update (could be callback query etc.) — acknowledge silently
        return {"ok": "true"}

    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if not chat_id or not text:
        return {"ok": "true"}

    # Process async (best-effort — Telegram expects fast 200 response)
    import asyncio
    asyncio.create_task(_process_message(text, chat_id))

    return {"ok": "true"}


class RegisterRequest(BaseModel):
    webhook_url: str


@router.post("/telegram/register")
async def telegram_register(req: RegisterRequest) -> dict[str, Any]:
    """Register this server as the Telegram bot webhook."""
    try:
        token = _bot_token()
    except ValueError as e:
        raise HTTPException(500, str(e))

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{_TELEGRAM_API}/bot{token}/setWebhook",
            json={"url": req.webhook_url, "allowed_updates": ["message", "edited_message"]},
        )
        data = resp.json()

    if not data.get("ok"):
        raise HTTPException(500, f"Telegram setWebhook failed: {data.get('description', data)}")

    logger.info("Telegram webhook registered: %s", req.webhook_url)
    return {"ok": True, "webhook_url": req.webhook_url, "telegram_response": data}


@router.get("/telegram/status")
async def telegram_status() -> dict[str, Any]:
    """Return current Telegram webhook info."""
    try:
        token = _bot_token()
    except ValueError as e:
        raise HTTPException(500, str(e))

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{_TELEGRAM_API}/bot{token}/getWebhookInfo")
        data = resp.json()

    if not data.get("ok"):
        raise HTTPException(500, f"Telegram getWebhookInfo failed: {data}")

    return {
        "ok": True,
        "webhook_info": data.get("result", {}),
        "bot_token_configured": True,
        "chat_id_configured": bool(os.environ.get("TELEGRAM_CHAT_ID")),
    }
