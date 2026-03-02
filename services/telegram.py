"""Fire-and-forget Telegram Bot API notifications."""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org"


async def send_notification(message: str) -> None:
    """Send a Telegram message. Failures are logged but never propagated."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        logger.warning("Telegram not configured — skipping notification")
        return

    url = f"{_TELEGRAM_API}/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning("Telegram API returned %s: %s", resp.status_code, resp.text)
            else:
                logger.debug("Telegram notification sent")
    except Exception:
        logger.exception("Telegram notification failed")
