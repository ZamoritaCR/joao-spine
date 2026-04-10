"""
Exocortex router -- v5 dual-loop learning + provenance + transparency.

All endpoints extend (never replace) existing v3/v4 routes.
Mounted under /joao/ prefix.
"""

import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from typing import Optional

from exocortex.ledgers import (
    record_intent, record_outcome, get_intents, get_outcomes,
    get_intent, get_outcome, parse_control_flags,
    get_active_locks, check_lock, grant_lock,
    export_intents_jsonl, export_outcomes_jsonl,
)
from exocortex.learning import (
    generate_delta, get_deltas, get_experiments,
    check_promotion_eligible, promote_to_core,
    load_johan_model, get_model_version,
)
from exocortex.receipts import generate_trust_report, generate_executive_receipt
from exocortex.digest import get_switchboard, get_metrics, generate_daily_digest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/joao", tags=["exocortex-v5"])


# ─── Models ───

class IntentRequest(BaseModel):
    raw_input: str
    project: Optional[str] = ""
    definition_of_done: Optional[str] = ""
    constraints: Optional[dict] = None

class OutcomeRequest(BaseModel):
    linked_intent_id: str
    artifacts: Optional[list] = None
    success: bool = True
    reason: Optional[str] = ""
    time_to_artifact_ms: Optional[int] = 0
    rework_count: Optional[int] = 0
    undo_used: Optional[bool] = False
    undo_steps: Optional[list] = None
    user_feedback: Optional[str] = ""
    egress_summary: Optional[dict] = None

class LockRequest(BaseModel):
    lock_type: str  # WRITE_LOCK or SHIP_LOCK
    scope: str
    duration_minutes: int = 30

class PromoteRequest(BaseModel):
    experiment_id: str


# ─── SWITCHBOARD ───

@router.get("/switchboard")
async def switchboard():
    """One-screen status of the entire JOAO system."""
    return get_switchboard()


# ─── METRICS ───

@router.get("/metrics")
async def metrics(days: int = 7):
    """Flywheel metrics for the last N days."""
    return get_metrics(days=days)


# ─── INTENT LEDGER ───

@router.post("/v5/intent")
async def create_intent(req: IntentRequest):
    """Record an intent with autonomy + learning flags parsed from input."""
    flags = parse_control_flags(req.raw_input)

    # Route the clean text
    try:
        from capability.registry import route
        routing = route(flags["clean_text"])
    except Exception:
        routing = {"capability": "general", "agent": "CJ", "confidence": 0.3}

    intent = record_intent(
        raw_input=req.raw_input,
        parsed_intent=flags["clean_text"],
        project=req.project,
        autonomy_level=flags["autonomy"],
        learning_mode=flags["learning"],
        capability_chain=[routing["capability"]],
        chosen_brains=[routing["agent"]],
        definition_of_done=req.definition_of_done,
        constraints=req.constraints,
        confidence=routing.get("confidence", 0),
    )

    return {
        "intent": intent,
        "control_flags": {
            "autonomy": flags["autonomy"],
            "learning": flags["learning"],
            "locks_granted": flags["locks_granted"],
        },
        "routing": routing,
    }


@router.get("/v5/intents")
async def list_intents(last: int = 20, project: str = None):
    """List recent intents."""
    return {"intents": get_intents(last_n=last, project=project)}


@router.get("/v5/intent/{intent_id}")
async def fetch_intent(intent_id: str):
    """Get a specific intent."""
    intent = get_intent(intent_id)
    if not intent:
        raise HTTPException(404, f"Intent not found: {intent_id}")
    return intent


# ─── OUTCOME LEDGER ───

@router.post("/v5/outcome")
async def create_outcome(req: OutcomeRequest):
    """Record an outcome and trigger learning."""
    # Record outcome
    outcome = record_outcome(
        linked_intent_id=req.linked_intent_id,
        artifacts=req.artifacts,
        success=req.success,
        reason=req.reason,
        time_to_artifact_ms=req.time_to_artifact_ms,
        rework_count=req.rework_count,
        undo_used=req.undo_used,
        undo_steps=req.undo_steps,
        user_feedback=req.user_feedback,
        egress_summary=req.egress_summary,
    )

    # Get linked intent for learning + receipts
    intent = get_intent(req.linked_intent_id)
    learning_mode = intent.get("learning_mode", "EDGE") if intent else "EDGE"

    # Generate learning delta
    delta = generate_delta(req.linked_intent_id, outcome, learning_mode)

    # Generate receipts
    trust_report = None
    executive_receipt = None
    if intent:
        trust_report = generate_trust_report(intent, outcome)
        executive_receipt = generate_executive_receipt(intent, outcome)

    return {
        "outcome": outcome,
        "learning_delta": delta,
        "receipts": {
            "trust_report_generated": trust_report is not None,
            "executive_receipt_generated": executive_receipt is not None,
        },
    }


@router.get("/v5/outcomes")
async def list_outcomes(last: int = 20, intent_id: str = None):
    """List recent outcomes."""
    return {"outcomes": get_outcomes(last_n=last, intent_id=intent_id)}


@router.get("/v5/outcome/{outcome_id}")
async def fetch_outcome(outcome_id: str):
    """Get a specific outcome."""
    outcome = get_outcome(outcome_id)
    if not outcome:
        raise HTTPException(404, f"Outcome not found: {outcome_id}")
    return outcome


# ─── TRUST RECEIPTS ───

@router.get("/v5/receipt/{intent_id}/trust", response_class=PlainTextResponse)
async def fetch_trust_report(intent_id: str):
    """Get full trust report for an intent+outcome pair."""
    intent = get_intent(intent_id)
    if not intent:
        raise HTTPException(404, "Intent not found")
    outcomes = get_outcomes(intent_id=intent_id)
    if not outcomes:
        raise HTTPException(404, "No outcomes for this intent")
    report = generate_trust_report(intent, outcomes[-1])
    return report


@router.get("/v5/receipt/{intent_id}/executive", response_class=PlainTextResponse)
async def fetch_executive_receipt(intent_id: str):
    """Get executive receipt (2-min read) for an intent+outcome pair."""
    intent = get_intent(intent_id)
    if not intent:
        raise HTTPException(404, "Intent not found")
    outcomes = get_outcomes(intent_id=intent_id)
    if not outcomes:
        raise HTTPException(404, "No outcomes for this intent")
    receipt = generate_executive_receipt(intent, outcomes[-1])
    return receipt


# ─── LEARNING ───

@router.get("/v5/deltas")
async def list_deltas(last: int = 20, intent_id: str = None):
    """List recent learning deltas."""
    return {"deltas": get_deltas(last_n=last, intent_id=intent_id)}


@router.get("/v5/experiments")
async def list_experiments(status: str = None):
    """List experiments (active, promoted, retired)."""
    return {"experiments": get_experiments(status=status)}


@router.post("/v5/promote")
async def promote_experiment(req: PromoteRequest):
    """Promote an EDGE experiment to CORE (update Johan Model)."""
    receipt = promote_to_core(req.experiment_id)
    if not receipt:
        raise HTTPException(
            400,
            "Promotion failed. Either experiment not found or not eligible "
            "(needs >=3 wins over >=2 days with 0 undos)."
        )
    return {"status": "promoted", "receipt": receipt}


# ─── JOHAN MODEL ───

@router.get("/v5/model")
async def fetch_model():
    """Get the current Johan Model."""
    return load_johan_model()


@router.get("/v5/model/version")
async def model_version():
    """Get current model version number."""
    return {"version": get_model_version()}


# ─── LOCKS ───

@router.post("/v5/lock")
async def create_lock(req: LockRequest):
    """Grant a WRITE_LOCK or SHIP_LOCK."""
    if req.lock_type not in ("WRITE_LOCK", "SHIP_LOCK"):
        raise HTTPException(400, "lock_type must be WRITE_LOCK or SHIP_LOCK")
    lock = grant_lock(req.lock_type, req.scope, req.duration_minutes)
    return {"lock": lock}


@router.get("/v5/locks")
async def list_locks():
    """List active locks."""
    return {"locks": get_active_locks()}


# ─── DAILY DIGEST ───

@router.get("/v5/digest", response_class=PlainTextResponse)
async def daily_digest(date: str = None):
    """Generate the daily 'Shipped Reality' digest."""
    return generate_daily_digest(date=date)


@router.post("/v5/digest/send")
async def send_digest(date: str = None):
    """Generate and send daily digest via Telegram."""
    from exocortex.digest import send_digest_telegram
    digest = generate_daily_digest(date=date)
    sent = await send_digest_telegram(digest)
    return {"digest_generated": True, "telegram_sent": sent, "content": digest}


# ─── EXPORT ───

@router.get("/v5/export/intents")
async def export_intents():
    """Get path to intents JSONL export."""
    return {"path": export_intents_jsonl()}


@router.get("/v5/export/outcomes")
async def export_outcomes():
    """Get path to outcomes JSONL export."""
    return {"path": export_outcomes_jsonl()}
