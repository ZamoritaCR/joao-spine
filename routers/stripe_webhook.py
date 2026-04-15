"""
Stripe webhook handler for JOAO products.
"""
import os, logging, json, hashlib, hmac
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException

logger = logging.getLogger("joao.stripe")
router = APIRouter(prefix="/stripe", tags=["stripe"])

STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_SECRET_KEY     = os.environ.get("STRIPE_SECRET_KEY", "")

def _verify_signature(payload: bytes, sig_header: str) -> bool:
    if not STRIPE_WEBHOOK_SECRET:
        return True
    try:
        parts = {k: v for k, v in (p.split("=", 1) for p in sig_header.split(","))}
        timestamp = parts.get("t", "")
        sigs = [v for k, v in parts.items() if k == "v1"]
        signed = f"{timestamp}.{payload.decode()}"
        expected = hmac.new(STRIPE_WEBHOOK_SECRET.encode(), signed.encode(), hashlib.sha256).hexdigest()
        return any(hmac.compare_digest(expected, s) for s in sigs)
    except Exception:
        return False

@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    if sig and not _verify_signature(payload, sig):
        raise HTTPException(status_code=400, detail="Invalid signature")
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})
    logger.info(f"Stripe event: {event_type}")
    try:
        from services.supabase_client import get_client
        sb = get_client()
        if event_type == "customer.subscription.created":
            sb.table("user_subscriptions").upsert({
                "stripe_customer_id": data.get("customer",""),
                "stripe_subscription_id": data.get("id"),
                "status": "active",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        elif event_type == "customer.subscription.deleted":
            sb.table("user_subscriptions").update({"status":"cancelled"}).eq("stripe_subscription_id", data.get("id")).execute()
        elif event_type == "invoice.payment_failed":
            logger.warning(f"Payment failed: {data.get('customer')}")
    except Exception as e:
        logger.error(f"Stripe Supabase sync error: {e}")
    return {"received": True, "event": event_type}

@router.get("/health")
async def stripe_health():
    return {
        "ok": bool(STRIPE_SECRET_KEY),
        "configured": bool(STRIPE_SECRET_KEY),
        "webhook_secret": bool(STRIPE_WEBHOOK_SECRET),
        "mode": "test" if STRIPE_SECRET_KEY.startswith("sk_test_") else "live" if STRIPE_SECRET_KEY else "unconfigured",
    }