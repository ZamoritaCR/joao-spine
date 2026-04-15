"""
Daily.co video room service for JOAO — MrDP video sessions, TAOP Connect.
"""
import os, logging, time, httpx

logger = logging.getLogger("joao.daily")

DAILY_API_KEY = os.environ.get("DAILY_API_KEY", "")
DAILY_API_URL = "https://api.daily.co/v1"

async def create_room(name: str = "", privacy: str = "private", exp_minutes: int = 60) -> dict:
    if not DAILY_API_KEY:
        return {"ok": False, "error": "DAILY_API_KEY not configured"}
    payload = {"privacy": privacy, "properties": {"exp": int(time.time()) + (exp_minutes * 60), "enable_chat": True}}
    if name:
        payload["name"] = name
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.post(f"{DAILY_API_URL}/rooms",
                headers={"Authorization": f"Bearer {DAILY_API_KEY}", "Content-Type": "application/json"},
                json=payload)
            data = resp.json()
            ok = resp.status_code == 200
            return {"ok": ok, "url": data.get("url",""), "name": data.get("name",""), "error": data.get("error","") if not ok else ""}
    except Exception as e:
        logger.error(f"Daily.co error: {e}")
        return {"ok": False, "error": str(e)}

async def create_mrdp_session(session_id: str) -> dict:
    return await create_room(name=f"mrdp-{session_id}", privacy="private", exp_minutes=90)

async def health_check() -> dict:
    if not DAILY_API_KEY:
        return {"ok": False, "configured": False}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.get(f"{DAILY_API_URL}/rooms",
                headers={"Authorization": f"Bearer {DAILY_API_KEY}"}, params={"limit": 1})
            return {"ok": resp.status_code == 200, "configured": True, "status_code": resp.status_code}
    except Exception as e:
        return {"ok": False, "configured": True, "error": str(e)}