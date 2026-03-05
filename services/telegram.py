"""Fire-and-forget Telegram Bot API notifications."""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org"


async def _send(
    token: str,
    chat_id: str,
    text: str,
    parse_mode: str = "Markdown",
) -> bool:
    """Send a message. Falls back to plain text if Markdown parsing fails."""
    url = f"{_TELEGRAM_API}/bot{token}/sendMessage"
    if len(text) > 4096:
        text = text[:4093] + "..."

    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            # Markdown parse errors return 400 -- retry without formatting
            if resp.status_code == 400 and parse_mode:
                logger.warning("Telegram Markdown failed, retrying as plain text")
                payload.pop("parse_mode")
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    return True
            logger.warning("Telegram API returned %s: %s", resp.status_code, resp.text)
            return False
    except Exception:
        logger.exception("Telegram send failed")
        return False


async def send_notification(message: str) -> None:
    """Send a Telegram message. Failures are logged but never propagated."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        logger.warning("Telegram not configured — skipping notification")
        return

    ok = await _send(token, chat_id, message)
    if ok:
        logger.debug("Telegram notification sent")


async def send_reply(chat_id: str | int, text: str) -> bool:
    """Send a reply to a specific chat. Returns True on success."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.warning("Telegram not configured — cannot send reply")
        return False

    return await _send(token, str(chat_id), text)
