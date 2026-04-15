"""
Twilio service for JOAO — SMS alerts, voice call initiation, WhatsApp.
"""
import os, logging, httpx
from base64 import b64encode

logger = logging.getLogger("joao.twilio")

ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN", "")
FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "")
BASE_URL    = f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}"

def _auth_header() -> str:
    return "Basic " + b64encode(f"{ACCOUNT_SID}:{AUTH_TOKEN}".encode()).decode()

async def send_sms(to: str, body: str) -> dict:
    if not ACCOUNT_SID or not AUTH_TOKEN:
        return {"ok": False, "error": "TWILIO credentials not configured"}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.post(f"{BASE_URL}/Messages.json",
                headers={"Authorization": _auth_header()},
                data={"From": FROM_NUMBER, "To": to, "Body": body})
            data = resp.json()
            ok = resp.status_code in (200, 201)
            return {"ok": ok, "sid": data.get("sid",""), "error": data.get("message","") if not ok else ""}
    except Exception as e:
        logger.error(f"Twilio SMS error: {e}")
        return {"ok": False, "error": str(e)}

async def send_alert_sms(message: str) -> dict:
    to = os.environ.get("TWILIO_ALERT_NUMBER", os.environ.get("JOAO_PHONE_NUMBER",""))
    if not to:
        return {"ok": False, "error": "TWILIO_ALERT_NUMBER not set"}
    return await send_sms(to, f"JOAO ALERT: {message}")

async def health_check() -> dict:
    configured = bool(ACCOUNT_SID and AUTH_TOKEN)
    return {"ok": configured, "configured": configured, "from_number": bool(FROM_NUMBER)}