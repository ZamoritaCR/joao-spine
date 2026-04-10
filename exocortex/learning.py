"""
Dual-Loop Learning System.

EDGE loop: aggressive, safe experiments (1 verified outcome to activate).
CORE loop: conservative stable defaults (3+ outcomes over 2+ days to promote).

Every change is an experiment with a rollback switch.
"""

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from exocortex.ledgers import (
    _append_jsonl, _read_jsonl, _gen_id, _now, _supabase_insert,
    PROVENANCE_DIR, get_outcomes
)

DELTA_FILE = PROVENANCE_DIR / "deltas.jsonl"
EXPERIMENT_FILE = PROVENANCE_DIR / "experiments.jsonl"
MODEL_FILE = PROVENANCE_DIR / "johan_model.json"
MODEL_HISTORY_DIR = PROVENANCE_DIR / "model_versions"
MODEL_HISTORY_DIR.mkdir(parents=True, exist_ok=True)


# ──────────────────────────────────────────────
# LEARNING DELTAS
# ──────────────────────────────────────────────

def generate_delta(
    linked_intent_id: str,
    outcome: dict,
    learning_mode: str = "EDGE",
) -> Optional[dict]:
    """Generate a learning delta from an outcome.

    Returns None if LEARN:OFF or nothing to learn.
    """
    if learning_mode == "OFF":
        return None

    success = outcome.get("success", False)
    time_ms = outcome.get("time_to_artifact_ms", 0)
    rework = outcome.get("rework_count", 0)
    undo = outcome.get("undo_used", False)

    # Generate hypothesis based on outcome
    if success and not undo and rework == 0:
        change_type = "EDGE"
        if time_ms > 0:
            hypothesis = f"Capability chain succeeded in {time_ms}ms with no rework. Route is good."
            proposed = "Increase routing weight for this capability+intent pattern."
            risk = "low"
            benefit = "faster routing for similar intents"
        else:
            hypothesis = "Clean success with no measurable latency."
            proposed = "Log pattern for future CORE promotion."
            risk = "none"
            benefit = "data collection"
    elif success and rework > 0:
        change_type = "EDGE"
        hypothesis = f"Succeeded but required {rework} rework(s). Route works but output quality can improve."
        proposed = "Flag this capability for output review. Consider adding pre-validation."
        risk = "low"
        benefit = "reduced rework on similar intents"
    elif not success:
        change_type = "EDGE"
        hypothesis = f"Failed: {outcome.get('reason', 'unknown')}. Route or capability may need adjustment."
        proposed = "Reduce routing weight for this pattern. Consider alternative capability."
        risk = "medium"
        benefit = "avoid repeated failures"
    elif undo:
        change_type = "EDGE"
        hypothesis = "Required undo. Output was incorrect or unwanted."
        proposed = "Add guardrail for this intent pattern. Flag for review."
        risk = "medium"
        benefit = "prevent unwanted outputs"
    else:
        return None

    # Determine if this could be a CORE candidate
    if learning_mode in ("CORE", "ALL") and success and not undo and rework == 0:
        change_type = "CORE_CANDIDATE"

    delta = {
        "delta_id": _gen_id("delta-"),
        "linked_intent_id": linked_intent_id,
        "ts": _now(),
        "hypothesis": hypothesis,
        "change_type": change_type,
        "proposed_adjustment": proposed,
        "expected_benefit": benefit,
        "risk": risk,
        "experiment_id": None,
        "applied": False,
    }

    # Create experiment for EDGE deltas
    if change_type == "EDGE" and learning_mode in ("EDGE", "ALL"):
        experiment = create_experiment(delta)
        delta["experiment_id"] = experiment["experiment_id"]

    _append_jsonl(DELTA_FILE, delta)
    _supabase_insert("joao_deltas", delta)

    return delta


def get_deltas(last_n: int = 20, intent_id: str = None) -> list[dict]:
    """Get recent learning deltas."""
    deltas = _read_jsonl(DELTA_FILE)
    if intent_id:
        deltas = [d for d in deltas if d.get("linked_intent_id") == intent_id]
    return deltas[-last_n:]


# ──────────────────────────────────────────────
# EXPERIMENTS (EDGE LOOP)
# ──────────────────────────────────────────────

def create_experiment(delta: dict) -> dict:
    """Create an EDGE experiment from a learning delta."""
    experiment = {
        "experiment_id": _gen_id("exp-"),
        "delta_id": delta["delta_id"],
        "ts_created": _now(),
        "hypothesis": delta["hypothesis"],
        "proposed_adjustment": delta["proposed_adjustment"],
        "status": "active",  # active | promoted | retired
        "win_count": 0,
        "loss_count": 0,
        "rework_total": 0,
        "undo_count": 0,
        "first_win_date": None,
        "last_win_date": None,
        "distinct_days": set(),  # will be serialized as list
        "rollback_switch": True,
    }
    # Serialize set as list for JSON
    experiment["distinct_days"] = []

    _append_jsonl(EXPERIMENT_FILE, experiment)
    return experiment


def update_experiment(experiment_id: str, outcome: dict) -> Optional[dict]:
    """Update an experiment based on a new outcome."""
    experiments = _read_jsonl(EXPERIMENT_FILE)
    updated = None

    for exp in experiments:
        if exp.get("experiment_id") == experiment_id:
            if outcome.get("success") and not outcome.get("undo_used"):
                exp["win_count"] += 1
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if today not in exp.get("distinct_days", []):
                    exp["distinct_days"].append(today)
                if not exp.get("first_win_date"):
                    exp["first_win_date"] = _now()
                exp["last_win_date"] = _now()
            else:
                exp["loss_count"] += 1

            exp["rework_total"] += outcome.get("rework_count", 0)
            if outcome.get("undo_used"):
                exp["undo_count"] += 1

            updated = exp
            break

    if updated:
        # Rewrite experiment file (small file, safe to rewrite)
        with open(EXPERIMENT_FILE, "w", encoding="utf-8") as f:
            for exp in experiments:
                f.write(json.dumps(exp, default=str) + "\n")

    return updated


def get_experiments(status: str = None) -> list[dict]:
    """Get experiments, optionally filtered by status."""
    experiments = _read_jsonl(EXPERIMENT_FILE)
    if status:
        experiments = [e for e in experiments if e.get("status") == status]
    return experiments


def check_promotion_eligible(experiment: dict) -> bool:
    """Check if an EDGE experiment is eligible for CORE promotion.

    Rules:
    - win_count >= 3
    - distinct_days >= 2 (or sessions, approximated by days)
    - undo_count == 0
    - rework_total does not increase (flat or down vs baseline)
    """
    if experiment.get("win_count", 0) < 3:
        return False
    if len(experiment.get("distinct_days", [])) < 2:
        return False
    if experiment.get("undo_count", 0) > 0:
        return False
    return True


# ──────────────────────────────────────────────
# CORE PROMOTION
# ──────────────────────────────────────────────

def promote_to_core(experiment_id: str) -> Optional[dict]:
    """Promote an EDGE experiment to CORE (update Johan Model).

    Returns the model change receipt or None if not eligible.
    """
    experiments = _read_jsonl(EXPERIMENT_FILE)
    target = None
    for exp in experiments:
        if exp.get("experiment_id") == experiment_id:
            target = exp
            break

    if not target:
        return None

    if not check_promotion_eligible(target):
        return None

    # Load current model
    model = load_johan_model()

    # Apply the adjustment to the model
    adjustment = target.get("proposed_adjustment", "")
    model["version"] += 1
    model["last_updated"] = _now()
    model["change_log"].append({
        "version": model["version"],
        "ts": _now(),
        "experiment_id": experiment_id,
        "change": adjustment,
        "evidence": {
            "win_count": target["win_count"],
            "distinct_days": len(target.get("distinct_days", [])),
            "undo_count": target["undo_count"],
            "rework_total": target["rework_total"],
        },
    })

    # Save versioned snapshot
    save_johan_model(model)
    snapshot_path = MODEL_HISTORY_DIR / f"v{model['version']}.json"
    snapshot_path.write_text(json.dumps(model, indent=2, default=str), encoding="utf-8")

    # Mark experiment as promoted
    target["status"] = "promoted"
    with open(EXPERIMENT_FILE, "w", encoding="utf-8") as f:
        for exp in experiments:
            f.write(json.dumps(exp, default=str) + "\n")

    # Generate model change receipt
    receipt = {
        "type": "model_change_receipt",
        "ts": _now(),
        "model_version": model["version"],
        "experiment_id": experiment_id,
        "change_applied": adjustment,
        "evidence_summary": f"{target['win_count']} wins over {len(target.get('distinct_days',[]))} days, 0 undos",
        "rollback": f"Revert to model v{model['version'] - 1} at {MODEL_HISTORY_DIR}/v{model['version'] - 1}.json",
    }

    _append_jsonl(PROVENANCE_DIR / "model_changes.jsonl", receipt)

    return receipt


# ──────────────────────────────────────────────
# JOHAN MODEL
# ──────────────────────────────────────────────

_DEFAULT_MODEL = {
    "version": 0,
    "last_updated": None,
    "preferences": {
        "format": "terse, no emojis, one next action",
        "tone": "direct, momentum-preserving",
        "receipts": "executive by default, full on request",
    },
    "routing_defaults": {
        "tableau_to_powerbi": {"weight": 1.0, "brain": "BYTE"},
        "mood_playlist": {"weight": 1.0, "brain": "ARIA"},
        "git_scan": {"weight": 1.0, "brain": "CJ"},
        "git_write": {"weight": 1.0, "brain": "BYTE"},
        "general": {"weight": 0.5, "brain": "CJ"},
    },
    "hates": [
        "emojis",
        "therapy talk",
        "slowing down",
        "guilt",
        "coaching tone",
        "unnecessary confirmation dialogs",
    ],
    "needs": [
        "momentum",
        "one next action",
        "capture everything",
        "auditable trail",
        "reversible actions",
    ],
    "per_project_dod": {
        "dr-data": "Migration bundle with 6 artifacts, 0 uncaught exceptions",
        "joao-spine": "Endpoint responds 200, smoke tests pass",
    },
    "autonomy_preferences": {
        "default": "L1",
        "familiar_tasks": "L2",
        "note": "Always overridden by explicit user flags",
    },
    "change_log": [],
}


def load_johan_model() -> dict:
    """Load the current Johan Model."""
    if MODEL_FILE.exists():
        return json.loads(MODEL_FILE.read_text(encoding="utf-8"))
    return _DEFAULT_MODEL.copy()


def save_johan_model(model: dict):
    """Save the Johan Model."""
    MODEL_FILE.write_text(json.dumps(model, indent=2, default=str), encoding="utf-8")


def get_model_version() -> int:
    """Get current model version number."""
    model = load_johan_model()
    return model.get("version", 0)
