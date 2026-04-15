"""SendGrid email delivery service for joao-spine."""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

_SENDGRID_API = "https://api.sendgrid.com/v3/mail/send"


async def send_email(
    subject: str,
    html_body: str,
    to_email: str | None = None,
    from_email: str | None = None,
    from_name: str = "JOAO",
) -> bool:
    """Send an HTML email via SendGrid.

    Returns True on success, False on failure. Never raises.
    """
    api_key = os.environ.get("SENDGRID_API_KEY", "")
    if not api_key:
        logger.warning("SENDGRID_API_KEY not set — email not sent")
        return False

    to_addr = to_email or os.environ.get("SENDGRID_TO", os.environ.get("EMAIL_TO", ""))
    if not to_addr:
        logger.warning("No recipient configured — set SENDGRID_TO or pass to_email")
        return False

    from_addr = from_email or os.environ.get("SENDGRID_FROM", "joao@theartofthepossible.io")

    payload = {
        "personalizations": [{"to": [{"email": to_addr}]}],
        "from": {"email": from_addr, "name": from_name},
        "subject": subject,
        "content": [{"type": "text/html", "value": html_body}],
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _SENDGRID_API,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code in (200, 201, 202):
                logger.info("Email sent via SendGrid to %s", to_addr)
                return True
            logger.warning("SendGrid returned %s: %s", resp.status_code, resp.text)
            return False
    except Exception:
        logger.exception("SendGrid send failed")
        return False
