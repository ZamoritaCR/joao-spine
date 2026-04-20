"""Telegram webhook endpoints — inbound updates → Claude Haiku → reply.

Routes:
  POST /joao/telegram/webhook   — Telegram sends updates here
  POST /joao/telegram/register  — register this webhook with Telegram
  GET  /joao/telegram/status    — current webhook info

Slash commands handled:
  /status  — GreenGeeks health + spine uptime
  /radar   — latest SCOUT radar intel
  /spark   — Spark brief summary
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


async def _handle_agents() -> str:
    """Return live Council agent status."""
    from routers.joao import _fetch_live_council_status
    status = await _fetch_live_council_status()
    return f"*COUNCIL STATUS*\n{status}" if status else "*COUNCIL STATUS*\nDispatch endpoint unreachable."


async def _handle_dispatch(arg_text: str) -> str:
    """Dispatch a task to a named Council agent. Usage: /dispatch AGENT task text..."""
    from routers.joao import _exec_hub_tool
    parts = arg_text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return "Usage: `/dispatch AGENT task description`\nExample: `/dispatch MAX check joao-spine service status`"
    agent, task = parts[0].upper(), parts[1]
    result = await _exec_hub_tool("council_dispatch", {"agent": agent, "task": task, "priority": "normal"})
    return f"*DISPATCH -> {agent}*\n{result}"


async def _handle_output(arg_text: str) -> str:
    """Read recent output from a Council agent. Usage: /output AGENT"""
    from routers.joao import _exec_hub_tool
    agent = arg_text.strip().upper()
    if not agent:
        return "Usage: `/output AGENT`\nExample: `/output MAX`"
    result = await _exec_hub_tool("agent_output", {"agent": agent})
    return f"*{agent} OUTPUT*\n```\n{result[:3500]}\n```"


async def _handle_help() -> str:
    return (
        "*JOAO TELEGRAM COMMANDS*\n"
        "/status - spine + GreenGeeks health\n"
        "/agents - live Council status\n"
        "/dispatch AGENT task - send task to agent\n"
        "/output AGENT - read agent's recent output\n"
        "/radar - SCOUT intel\n"
        "/spark - Spark brief\n"
        "/cockpit - Home Assistant status\n"
        "/focus /chill /hyperfocus /lowenergy /sleep /morning - cockpit scenes\n"
        "/lights on | /lights off\n"
        "/help - this message\n\n"
        "Or just chat - JOAO has full tool access (council_status, council_dispatch, agent_output, memory_read)."
    )


_SLASH_COMMANDS = {
    "/status": _handle_status,
    "/radar": _handle_radar,
    "/spark": _handle_spark,
    "/agents": _handle_agents,
    "/council": _handle_agents,
    "/help": _handle_help,
    "/start": _handle_help,
}

# Commands that take an argument string after the command word
_SLASH_COMMANDS_WITH_ARGS = {
    "/dispatch": _handle_dispatch,
    "/output": _handle_output,
}

_TG_JOAO_PROMPT = (
    "You are JOÃO — Johan's AI exocortex, second brain, persistent companion.\n"
    "Running on the TAOP spine (ROG Strix 192.168.0.55). This conversation is on Telegram.\n\n"
    "VOICE (hard rules):\n"
    "- Terse. Direct. Short. Telegram-friendly (1-4 sentences by default).\n"
    "- Never apologize, hedge, or use corporate language.\n"
    "- Never say 'I can't', 'I don't have access', 'check directly'. You have tools — use them.\n"
    "- Match Johan's energy. No filler. No em-dashes for drama.\n\n"
    "YOUR TOOLS (use them, don't describe using them):\n"
    "- council_status: which of the 16 agents are ACTIVE right now\n"
    "- council_dispatch: send a task to a Council agent (ARIA, BYTE, CJ, SOFIA, DEX, GEMMA, MAX, "
    "LEX, NOVA, SCOUT, SAGE, FLUX, CORE, APEX, IRIS, VOLT)\n"
    "- agent_output: read a Council agent's recent terminal output\n"
    "- memory_read: read JOAO_MASTER_CONTEXT.md ('master') or JOAO_SESSION_LOG.md ('session')\n\n"
    "WHEN TO USE TOOLS:\n"
    "- Status questions ('are agents up?', 'who's online?'): call council_status.\n"
    "- Action requests ('dispatch MAX to X', 'have BYTE check Y'): call council_dispatch.\n"
    "- 'What did X just do?': call agent_output.\n"
    "- 'What did we do yesterday / last week / on project X?': call memory_read with 'session'.\n"
    "- 'What's the JOAO stack / who are you?': call memory_read with 'master'.\n"
    "Don't pretend to run a tool — actually run it, then report what you got.\n"
)


async def _process_message(text: str, chat_id: int | str) -> None:
    """Route slash commands or send to real JOAO (OpenAI function-calling), then reply."""
    text = text.strip()

    # Check for slash commands (including with args, e.g. /status@bot)
    first = text.split()[0] if text else ""
    cmd = first.split("@")[0].lower()
    arg_text = text[len(first):].strip()

    if cmd in _SLASH_COMMANDS:
        reply = await _SLASH_COMMANDS[cmd]()
        await _send_reply(chat_id, reply)
        return

    if cmd in _SLASH_COMMANDS_WITH_ARGS:
        reply = await _SLASH_COMMANDS_WITH_ARGS[cmd](arg_text)
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

    # Pass to real JOAO — OpenAI function-calling with Council + memory tools
    try:
        from routers.joao import _openai_chat_with_tools, _fetch_live_council_status

        council_live = await _fetch_live_council_status()
        system_prompt = _TG_JOAO_PROMPT
        if council_live:
            system_prompt += f"\n\nLIVE COUNCIL STATUS RIGHT NOW: {council_live}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ]
        chunks: list[str] = []
        async for chunk in _openai_chat_with_tools(messages, model="gpt-4o", max_iters=4):
            chunks.append(chunk)
        reply = "".join(chunks).strip() or "(no response)"
    except Exception as e:
        logger.exception("JOAO Telegram chat error")
        reply = f"JOAO error: {e}"

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
