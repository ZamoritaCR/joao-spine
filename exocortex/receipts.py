"""
Trust Receipts + Executive Receipts -- productized transparency.

Every completed run generates:
- trust_report.md (full provenance)
- executive_receipt.md (2-minute read)
"""

from datetime import datetime, timezone
from typing import Optional
from exocortex.ledgers import get_intent, get_outcomes
from capability.artifact_store import save_artifact


def generate_trust_report(intent: dict, outcome: dict, job_id: str = "") -> str:
    """Generate a full trust report for a completed run."""
    intent_id = intent.get("intent_id", "?")
    outcome_id = outcome.get("outcome_id", "?")
    artifacts = outcome.get("artifacts", [])
    egress = outcome.get("egress_summary", {})
    undo_steps = outcome.get("undo_steps", [])

    report = f"""# Trust Report

## Run Identity
- Intent ID: `{intent_id}`
- Outcome ID: `{outcome_id}`
- Timestamp: {outcome.get("ts", "?")}
- Autonomy Level: {intent.get("autonomy_level", "L1")}
- Learning Mode: {intent.get("learning_mode", "EDGE")}

## Intent
- Raw input: {intent.get("raw_input", "?")}
- Parsed intent: {intent.get("parsed_intent", "?")}
- Project: {intent.get("project", "none")}
- Capability chain: {", ".join(intent.get("capability_chain", []))}
- Brains assigned: {", ".join(intent.get("chosen_brains", []))}
- Confidence: {intent.get("confidence", 0):.0%}

## Outcome
- Success: {"YES" if outcome.get("success") else "NO"}
- Reason: {outcome.get("reason", "completed normally")}
- Time to artifact: {outcome.get("time_to_artifact_ms", 0)}ms
- Rework count: {outcome.get("rework_count", 0)}
- Undo used: {"YES" if outcome.get("undo_used") else "NO"}

## Artifacts Produced
"""
    for a in artifacts:
        if isinstance(a, dict):
            report += f"- `{a.get('name', '?')}` (hash: {a.get('hash', 'n/a')})\n"
        else:
            report += f"- `{a}`\n"

    report += f"""
## Egress Summary
- External APIs called: {", ".join(egress.get("external_apis_called", [])) or "none"}
- Data sent externally: {"YES" if egress.get("data_sent_externally") else "NO"}

## Locks
"""
    locks = intent.get("locks_held", []) or []
    if locks:
        for l in locks:
            report += f"- {l.get('lock_type', '?')} scope={l.get('scope', '?')} expires={l.get('expires_at', '?')}\n"
    else:
        report += "- No locks held\n"

    report += f"""
## Undo Recipe
"""
    if undo_steps:
        for step in undo_steps:
            report += f"- {step}\n"
    else:
        report += "- Delete artifact directory for this job\n"

    report += f"""
## Context
- Context pack hash: `{intent.get("context_pack_hash", "none")}`
- Definition of done: {intent.get("definition_of_done", "not specified")}
- Constraints: {intent.get("constraints", {})}
"""

    # Save as artifact if job_id provided
    if job_id:
        save_artifact(job_id, "trust_report.md", report)

    return report


def generate_executive_receipt(intent: dict, outcome: dict, job_id: str = "") -> str:
    """Generate a 2-minute executive receipt."""
    success = outcome.get("success", False)
    artifacts = outcome.get("artifacts", [])
    artifact_names = [
        (a.get("name") if isinstance(a, dict) else a) for a in artifacts
    ]

    receipt = f"""# Executive Receipt

**{intent.get("parsed_intent", intent.get("raw_input", "?"))}**

| Field | Value |
|-------|-------|
| Status | {"SHIPPED" if success else "FAILED"} |
| Time | {outcome.get("time_to_artifact_ms", 0)}ms |
| Autonomy | {intent.get("autonomy_level", "L1")} |
| Brain | {", ".join(intent.get("chosen_brains", ["?"]))} |
| Artifacts | {len(artifacts)} |
| Rework | {outcome.get("rework_count", 0)} |
| Undo | {"used" if outcome.get("undo_used") else "none"} |

## What Shipped
{chr(10).join(f"- {a}" for a in artifact_names) if artifact_names else "- (no artifacts)"}

## Risks
"""

    risks = []
    egress = outcome.get("egress_summary", {})
    if egress.get("data_sent_externally"):
        risks.append("- Data was sent to external API")
    if outcome.get("undo_used"):
        risks.append("- Undo was required (output may have been incorrect)")
    if outcome.get("rework_count", 0) > 0:
        risks.append(f"- Required {outcome['rework_count']} rework(s)")
    if not success:
        risks.append(f"- FAILED: {outcome.get('reason', 'unknown')}")

    receipt += "\n".join(risks) if risks else "- None flagged"

    receipt += f"""

## One Next Lever
"""
    if not success:
        receipt += f"Investigate failure: {outcome.get('reason', 'check logs')}"
    elif outcome.get("rework_count", 0) > 0:
        receipt += "Review output quality -- rework suggests the first pass missed something."
    else:
        receipt += "Ship is clean. Move to next intent."

    if job_id:
        save_artifact(job_id, "executive_receipt.md", receipt)

    return receipt
