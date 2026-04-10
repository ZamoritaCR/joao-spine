"""
Daily "Shipped Reality" Digest + Switchboard + Metrics.
"""

import os
import subprocess
from datetime import datetime, timezone, timedelta
from typing import Optional

from exocortex.ledgers import (
    get_intents, get_outcomes, get_active_locks, _read_jsonl,
    INTENT_FILE, OUTCOME_FILE, _now,
)
from exocortex.learning import (
    get_experiments, load_johan_model, get_model_version, get_deltas,
)


# ──────────────────────────────────────────────
# SWITCHBOARD
# ──────────────────────────────────────────────

def get_switchboard() -> dict:
    """The one-screen status of the entire JOAO system."""

    # Brains online
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=5,
        )
        sessions = [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]
    except Exception:
        sessions = []

    primary_brains = ["BYTE", "ARIA", "CJ", "SOFIA", "DEX", "GEMMA", "MAX"]
    brains_online = [b for b in primary_brains if b in sessions]

    # Capabilities
    try:
        from capability.registry import list_capabilities
        caps = list_capabilities()
    except Exception:
        caps = []

    # Active locks
    locks = get_active_locks()

    # Model
    model = load_johan_model()

    # Recent intents
    recent = get_intents(last_n=10)

    # Today's shipped count
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_outcomes = [
        o for o in get_outcomes(last_n=100)
        if o.get("ts", "").startswith(today) and o.get("success")
    ]

    # Active experiments
    active_experiments = get_experiments(status="active")

    return {
        "ts": _now(),
        "brains": {
            "online": brains_online,
            "expected": primary_brains,
            "all_sessions": sessions,
        },
        "capabilities": caps,
        "autonomy": {
            "default": model.get("autonomy_preferences", {}).get("default", "L1"),
            "active_locks": [
                {"type": l["lock_type"], "scope": l["scope"], "expires": l["expires_at"]}
                for l in locks
            ],
        },
        "learning": {
            "default_mode": "EDGE",
            "active_experiments": len(active_experiments),
            "model_version": model.get("version", 0),
        },
        "recent_intents": [
            {
                "id": i.get("intent_id"),
                "ts": i.get("ts"),
                "intent": i.get("parsed_intent", i.get("raw_input", ""))[:80],
                "autonomy": i.get("autonomy_level"),
                "status": "recorded",
            }
            for i in recent
        ],
        "today": {
            "shipped_count": len(today_outcomes),
            "date": today,
        },
    }


# ──────────────────────────────────────────────
# METRICS
# ──────────────────────────────────────────────

def get_metrics(days: int = 7) -> dict:
    """Flywheel metrics for the last N days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    outcomes = [
        o for o in get_outcomes(last_n=500)
        if o.get("ts", "") >= cutoff
    ]

    if not outcomes:
        return {
            "period_days": days,
            "total_runs": 0,
            "success_rate": 0,
            "avg_time_to_artifact_ms": 0,
            "total_reworks": 0,
            "undo_rate": 0,
            "experiments_active": 0,
            "experiments_promoted": 0,
            "model_version": get_model_version(),
        }

    total = len(outcomes)
    successes = sum(1 for o in outcomes if o.get("success"))
    undos = sum(1 for o in outcomes if o.get("undo_used"))
    reworks = sum(o.get("rework_count", 0) for o in outcomes)
    times = [o.get("time_to_artifact_ms", 0) for o in outcomes if o.get("time_to_artifact_ms")]
    avg_time = round(sum(times) / len(times)) if times else 0

    experiments = get_experiments()
    active = sum(1 for e in experiments if e.get("status") == "active")
    promoted = sum(1 for e in experiments if e.get("status") == "promoted")

    return {
        "period_days": days,
        "total_runs": total,
        "success_rate": round(successes / total, 2) if total else 0,
        "avg_time_to_artifact_ms": avg_time,
        "total_reworks": reworks,
        "undo_rate": round(undos / total, 2) if total else 0,
        "experiments_active": active,
        "experiments_promoted": promoted,
        "model_version": get_model_version(),
    }


# ──────────────────────────────────────────────
# DAILY DIGEST
# ──────────────────────────────────────────────

def generate_daily_digest(date: str = None) -> str:
    """Generate the daily 'Shipped Reality' digest.

    Args:
        date: YYYY-MM-DD string, defaults to today
    """
    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    intents = [
        i for i in get_intents(last_n=200)
        if i.get("ts", "").startswith(date)
    ]
    outcomes = [
        o for o in get_outcomes(last_n=200)
        if o.get("ts", "").startswith(date)
    ]

    shipped = [o for o in outcomes if o.get("success")]
    failed = [o for o in outcomes if not o.get("success")]
    open_intents = []
    outcome_intent_ids = {o.get("linked_intent_id") for o in outcomes}
    for i in intents:
        if i.get("intent_id") not in outcome_intent_ids:
            open_intents.append(i)

    # Gather artifacts
    all_artifacts = []
    for o in shipped:
        for a in o.get("artifacts", []):
            name = a.get("name") if isinstance(a, dict) else a
            all_artifacts.append(name)

    # Risks
    risks = []
    undo_count = sum(1 for o in outcomes if o.get("undo_used"))
    rework_total = sum(o.get("rework_count", 0) for o in outcomes)
    if undo_count > 0:
        risks.append(f"{undo_count} run(s) required undo -- review output quality")
    if rework_total > 2:
        risks.append(f"{rework_total} total reworks -- consider adding pre-validation")
    if failed:
        risks.append(f"{len(failed)} failed run(s) -- check error reasons")
    if not risks:
        risks.append("No significant risks today")

    digest = f"""# Shipped Reality Digest -- {date}

## Summary
- Intents recorded: {len(intents)}
- Shipped (success): {len(shipped)}
- Failed: {len(failed)}
- Open loops: {len(open_intents)}

## Shipped Artifacts
"""
    if all_artifacts:
        for a in all_artifacts:
            digest += f"- {a}\n"
    else:
        digest += "- No artifacts shipped today\n"

    digest += """
## Open Loops (intents without outcomes)
"""
    if open_intents:
        for i in open_intents[:5]:
            digest += f"- [{i.get('intent_id')}] {i.get('parsed_intent', i.get('raw_input', '?'))[:60]}\n"
        if len(open_intents) > 5:
            digest += f"- ... and {len(open_intents) - 5} more\n"
    else:
        digest += "- All intents have outcomes\n"

    digest += f"""
## Top Risks
"""
    for r in risks[:3]:
        digest += f"- {r}\n"

    digest += f"""
## Tomorrow's One Lane
"""
    if open_intents:
        digest += f"Close the top open loop: {open_intents[0].get('parsed_intent', '?')[:60]}"
    elif failed:
        digest += f"Investigate failed run: {failed[0].get('reason', 'unknown')[:60]}"
    else:
        digest += "Clean slate. Pick the highest-leverage intent and ship it."

    return digest


async def send_digest_telegram(digest: str) -> bool:
    """Send daily digest via Telegram if configured."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            # Telegram has a 4096 char limit; truncate if needed
            text = digest[:4000]
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            )
            return resp.status_code == 200
    except Exception:
        return False
