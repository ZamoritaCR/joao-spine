"""
Intent + Outcome Ledgers -- the memory of every JOAO operation.

Dual-write: Supabase (if configured) + local JSONL (always).
Local JSONL is append-only and serves as the source of truth when Supabase is down.
"""

import json
import os
import hashlib
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

PROVENANCE_DIR = Path("/home/zamoritacr/joao-spine/provenance")
PROVENANCE_DIR.mkdir(parents=True, exist_ok=True)

INTENT_FILE = PROVENANCE_DIR / "intents.jsonl"
OUTCOME_FILE = PROVENANCE_DIR / "outcomes.jsonl"
DELTA_FILE = PROVENANCE_DIR / "deltas.jsonl"
LOCK_FILE = PROVENANCE_DIR / "locks.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix: str = "") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}{ts}-{uuid.uuid4().hex[:8]}"


def _append_jsonl(path: Path, record: dict):
    """Append one JSON record to a JSONL file."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _read_jsonl(path: Path, last_n: int = 0) -> list[dict]:
    """Read all or last N records from a JSONL file."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    lines = [l for l in lines if l.strip()]
    if last_n > 0:
        lines = lines[-last_n:]
    return [json.loads(l) for l in lines]


def _supabase_insert(table: str, record: dict) -> bool:
    """Try to insert into Supabase. Returns False if unavailable."""
    try:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            return False
        from supabase import create_client
        client = create_client(url, key)
        client.table(table).insert(record).execute()
        return True
    except Exception:
        return False


def _supabase_query(table: str, filters: dict = None, order_by: str = "ts",
                     limit: int = 50, descending: bool = True) -> list[dict]:
    """Query Supabase table. Returns [] if unavailable."""
    try:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            return []
        from supabase import create_client
        client = create_client(url, key)
        q = client.table(table).select("*")
        if filters:
            for k, v in filters.items():
                q = q.eq(k, v)
        q = q.order(order_by, desc=descending).limit(limit)
        result = q.execute()
        return result.data or []
    except Exception:
        return []


# ──────────────────────────────────────────────
# INTENT LEDGER
# ──────────────────────────────────────────────

def record_intent(
    raw_input: str,
    parsed_intent: str,
    project: str = "",
    autonomy_level: str = "L1",
    learning_mode: str = "EDGE",
    capability_chain: list = None,
    chosen_brains: list = None,
    context_pack_hash: str = "",
    definition_of_done: str = "",
    constraints: dict = None,
    predicted_effort: str = "",
    confidence: float = 0.0,
) -> dict:
    """Record an intent. Returns the intent record with its ID."""
    intent = {
        "intent_id": _gen_id("int-"),
        "ts": _now(),
        "raw_input": raw_input,
        "parsed_intent": parsed_intent,
        "project": project,
        "autonomy_level": autonomy_level,
        "learning_mode": learning_mode,
        "capability_chain": capability_chain or [],
        "chosen_brains": chosen_brains or [],
        "context_pack_hash": context_pack_hash,
        "definition_of_done": definition_of_done,
        "constraints": constraints or {},
        "predicted_effort": predicted_effort,
        "confidence": confidence,
    }

    # Dual write
    _append_jsonl(INTENT_FILE, intent)
    _supabase_insert("joao_intents", intent)

    return intent


def get_intents(last_n: int = 20, project: str = None) -> list[dict]:
    """Get recent intents."""
    # Try Supabase first
    filters = {"project": project} if project else None
    sb = _supabase_query("joao_intents", filters=filters, limit=last_n)
    if sb:
        return sb
    # Fall back to local
    all_intents = _read_jsonl(INTENT_FILE, last_n=last_n * 2)
    if project:
        all_intents = [i for i in all_intents if i.get("project") == project]
    return all_intents[-last_n:]


def get_intent(intent_id: str) -> Optional[dict]:
    """Get a specific intent by ID."""
    sb = _supabase_query("joao_intents", filters={"intent_id": intent_id}, limit=1)
    if sb:
        return sb[0]
    for i in reversed(_read_jsonl(INTENT_FILE)):
        if i.get("intent_id") == intent_id:
            return i
    return None


# ──────────────────────────────────────────────
# OUTCOME LEDGER
# ──────────────────────────────────────────────

def record_outcome(
    linked_intent_id: str,
    artifacts: list = None,
    success: bool = True,
    reason: str = "",
    time_to_artifact_ms: int = 0,
    rework_count: int = 0,
    undo_used: bool = False,
    undo_steps: list = None,
    user_feedback: str = "",
    egress_summary: dict = None,
) -> dict:
    """Record an outcome linked to an intent."""
    outcome = {
        "outcome_id": _gen_id("out-"),
        "linked_intent_id": linked_intent_id,
        "ts": _now(),
        "artifacts": artifacts or [],
        "success": success,
        "reason": reason,
        "time_to_artifact_ms": time_to_artifact_ms,
        "rework_count": rework_count,
        "undo_used": undo_used,
        "undo_steps": undo_steps or [],
        "user_feedback": user_feedback,
        "egress_summary": egress_summary or {"external_apis_called": [], "data_sent_externally": False},
    }

    _append_jsonl(OUTCOME_FILE, outcome)
    _supabase_insert("joao_outcomes", outcome)

    return outcome


def get_outcomes(last_n: int = 20, intent_id: str = None) -> list[dict]:
    """Get recent outcomes, optionally filtered by intent."""
    filters = {"linked_intent_id": intent_id} if intent_id else None
    sb = _supabase_query("joao_outcomes", filters=filters, limit=last_n)
    if sb:
        return sb
    all_outcomes = _read_jsonl(OUTCOME_FILE, last_n=last_n * 2)
    if intent_id:
        all_outcomes = [o for o in all_outcomes if o.get("linked_intent_id") == intent_id]
    return all_outcomes[-last_n:]


def get_outcome(outcome_id: str) -> Optional[dict]:
    """Get a specific outcome by ID."""
    sb = _supabase_query("joao_outcomes", filters={"outcome_id": outcome_id}, limit=1)
    if sb:
        return sb[0]
    for o in reversed(_read_jsonl(OUTCOME_FILE)):
        if o.get("outcome_id") == outcome_id:
            return o
    return None


# ──────────────────────────────────────────────
# LOCKS
# ──────────────────────────────────────────────

def grant_lock(lock_type: str, scope: str, duration_minutes: int = 30,
               granted_by: str = "johan") -> dict:
    """Grant a WRITE_LOCK or SHIP_LOCK."""
    now = datetime.now(timezone.utc)
    lock = {
        "lock_id": _gen_id("lock-"),
        "lock_type": lock_type,
        "scope": scope,
        "granted_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=duration_minutes)).isoformat(),
        "granted_by": granted_by,
        "active": True,
    }
    _append_jsonl(LOCK_FILE, lock)
    _supabase_insert("joao_locks", lock)
    return lock


def check_lock(lock_type: str, scope: str) -> Optional[dict]:
    """Check if an active, non-expired lock exists for type+scope."""
    now = datetime.now(timezone.utc)
    # Check local first (faster)
    for lock in reversed(_read_jsonl(LOCK_FILE)):
        if (lock.get("lock_type") == lock_type and
            lock.get("scope") == scope and
            lock.get("active") and
            datetime.fromisoformat(lock["expires_at"]) > now):
            return lock
    return None


def get_active_locks() -> list[dict]:
    """Get all currently active locks."""
    now = datetime.now(timezone.utc)
    locks = _read_jsonl(LOCK_FILE)
    return [
        l for l in locks
        if l.get("active") and datetime.fromisoformat(l["expires_at"]) > now
    ]


# ──────────────────────────────────────────────
# AUTONOMY PARSER
# ──────────────────────────────────────────────

import re

_AUTONOMY_RE = re.compile(r'\bL([0-4])\b')
_LEARN_RE = re.compile(r'\bLEARN:(OFF|EDGE|CORE|ALL)\b', re.IGNORECASE)
_WRITE_LOCK_RE = re.compile(
    r'WRITE_LOCK=(\d+)([mhd])\s+scope=(\S+)', re.IGNORECASE
)
_SHIP_LOCK_RE = re.compile(
    r'SHIP_LOCK=(\d+)([mhd])\s+scope=(\S+)', re.IGNORECASE
)


def _parse_duration(val: str, unit: str) -> int:
    """Convert duration to minutes."""
    n = int(val)
    if unit == "h":
        return n * 60
    if unit == "d":
        return n * 1440
    return n  # default minutes


def parse_control_flags(text: str) -> dict:
    """Parse autonomy, learning, and lock flags from user input.

    Returns dict with: autonomy, learning, locks_granted, clean_text
    """
    result = {
        "autonomy": "L1",
        "learning": "EDGE",
        "locks_granted": [],
        "clean_text": text,
    }

    # Parse autonomy level
    m = _AUTONOMY_RE.search(text)
    if m:
        result["autonomy"] = f"L{m.group(1)}"

    # Parse learning mode
    m = _LEARN_RE.search(text)
    if m:
        result["learning"] = m.group(1).upper()

    # Parse locks
    for m in _WRITE_LOCK_RE.finditer(text):
        dur = _parse_duration(m.group(1), m.group(2))
        lock = grant_lock("WRITE_LOCK", m.group(3), dur)
        result["locks_granted"].append(lock)

    for m in _SHIP_LOCK_RE.finditer(text):
        dur = _parse_duration(m.group(1), m.group(2))
        lock = grant_lock("SHIP_LOCK", m.group(3), dur)
        result["locks_granted"].append(lock)

    # Clean control flags from text
    clean = text
    clean = _AUTONOMY_RE.sub("", clean)
    clean = _LEARN_RE.sub("", clean)
    clean = _WRITE_LOCK_RE.sub("", clean)
    clean = _SHIP_LOCK_RE.sub("", clean)
    result["clean_text"] = clean.strip()

    return result


# ──────────────────────────────────────────────
# EXPORT
# ──────────────────────────────────────────────

def export_intents_jsonl() -> str:
    """Return path to intents JSONL file."""
    return str(INTENT_FILE)


def export_outcomes_jsonl() -> str:
    """Return path to outcomes JSONL file."""
    return str(OUTCOME_FILE)
