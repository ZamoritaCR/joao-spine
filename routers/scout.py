"""SCOUT REST endpoints for joao-spine."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from services import scout

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/joao/scout", tags=["scout"])


@router.get("/status")
async def scout_status():
    """Current SCOUT status and stats."""
    return scout.get_status()


@router.get("/intel")
async def scout_intel(limit: int = 20, min_score: int = 7):
    """Get recent intel items."""
    items = scout.get_recent_intel(limit=limit, min_score=min_score)
    return {"count": len(items), "items": items}


@router.post("/trigger")
async def scout_trigger():
    """Manually trigger a scan cycle."""
    new_items = await scout.run_scan()
    return {
        "status": "scan_complete",
        "new_items": len(new_items),
        "items": new_items[:10],
    }


@router.post("/brief")
async def scout_brief():
    """Manually trigger an email brief."""
    sent = await scout.send_email_brief()
    return {"status": "sent" if sent else "no_transport", "email": scout._EMAIL_TO}
