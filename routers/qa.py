"""QA Council router — agents submit work, 3 reviewers score, consensus gate."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException

from models.schemas import QASubmission, QAReview, QAConsensus
from services.qa_pipeline import run_qa_consensus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/joao/council", tags=["qa"])


async def _save_qa_to_supabase(submission: QASubmission, consensus: dict) -> str | None:
    """Persist QA result to Supabase qa_reviews table. Returns row id or None."""
    try:
        from services.supabase_client import get_client

        client = get_client()
        if not client:
            logger.warning("Supabase not configured, skipping QA persist")
            return None

        reviews = consensus["reviews"]
        row = {
            "dispatch_id": submission.dispatch_id,
            "agent": submission.agent,
            "task_summary": submission.task_summary,
            "code_diff": (submission.code_diff or "")[:50000],  # cap size
            "sonnet_score": reviews["sonnet"]["score"],
            "sonnet_verdict": reviews["sonnet"]["verdict"],
            "sonnet_feedback": reviews["sonnet"]["feedback"],
            "gpt_score": reviews["gpt"]["score"],
            "gpt_verdict": reviews["gpt"]["verdict"],
            "gpt_feedback": reviews["gpt"]["feedback"],
            "opus_score": reviews["opus"]["score"],
            "opus_verdict": reviews["opus"]["verdict"],
            "opus_feedback": reviews["opus"]["feedback"],
            "consensus_verdict": consensus["consensus_verdict"],
            "avg_score": consensus["avg_score"],
            "deployed": consensus["deploy_ready"],
        }
        result = client.table("qa_reviews").insert(row).execute()
        if result.data:
            return result.data[0].get("id")
    except Exception as e:
        logger.warning("Failed to save QA to Supabase: %s", e)
    return None


# In-memory store for QA results (keyed by dispatch_id) as fallback
_qa_cache: dict[str, dict] = {}


@router.post("/qa")
async def submit_for_qa(submission: QASubmission):
    """Agent submits completed work for QA review by Sonnet + GPT + Opus."""
    logger.info(
        "QA submission received: dispatch_id=%s agent=%s",
        submission.dispatch_id,
        submission.agent,
    )

    consensus = await run_qa_consensus(
        dispatch_id=submission.dispatch_id,
        agent=submission.agent,
        task_summary=submission.task_summary,
        code_diff=submission.code_diff,
        files_changed=submission.files_changed,
        test_results=submission.test_results,
    )

    # Persist to Supabase
    row_id = await _save_qa_to_supabase(submission, consensus)

    # Cache for quick lookups
    _qa_cache[submission.dispatch_id] = {
        **consensus,
        "agent": submission.agent,
        "task_summary": submission.task_summary,
        "supabase_id": row_id,
    }

    return QAConsensus(
        dispatch_id=submission.dispatch_id,
        reviews=[
            QAReview(
                model=consensus["reviews"]["sonnet"]["model"],
                score=consensus["reviews"]["sonnet"]["score"],
                verdict=consensus["reviews"]["sonnet"]["verdict"],
                feedback=consensus["reviews"]["sonnet"]["feedback"],
            ),
            QAReview(
                model=consensus["reviews"]["gpt"]["model"],
                score=consensus["reviews"]["gpt"]["score"],
                verdict=consensus["reviews"]["gpt"]["verdict"],
                feedback=consensus["reviews"]["gpt"]["feedback"],
            ),
            QAReview(
                model=consensus["reviews"]["opus"]["model"],
                score=consensus["reviews"]["opus"]["score"],
                verdict=consensus["reviews"]["opus"]["verdict"],
                feedback=consensus["reviews"]["opus"]["feedback"],
            ),
        ],
        consensus_verdict=consensus["consensus_verdict"],
        avg_score=consensus["avg_score"],
        deploy_ready=consensus["deploy_ready"],
    )


@router.get("/qa/{dispatch_id}")
async def get_qa_status(dispatch_id: str):
    """Get QA status/results for a dispatch."""
    # Check in-memory cache first
    if dispatch_id in _qa_cache:
        return _qa_cache[dispatch_id]

    # Try Supabase
    try:
        from services.supabase_client import get_client

        client = get_client()
        if client:
            result = (
                client.table("qa_reviews")
                .select("*")
                .eq("dispatch_id", dispatch_id)
                .limit(1)
                .execute()
            )
            if result.data:
                return result.data[0]
    except Exception as e:
        logger.warning("Supabase lookup failed: %s", e)

    raise HTTPException(status_code=404, detail=f"No QA record for dispatch_id={dispatch_id}")


@router.post("/qa/{dispatch_id}/override")
async def override_qa(dispatch_id: str, action: str = "deploy", override_by: str = "johan"):
    """Johan can override and force deploy or reject."""
    if action not in ("deploy", "reject"):
        raise HTTPException(status_code=400, detail="action must be 'deploy' or 'reject'")

    # Update in-memory cache
    if dispatch_id in _qa_cache:
        _qa_cache[dispatch_id]["consensus_verdict"] = action
        _qa_cache[dispatch_id]["deploy_ready"] = action == "deploy"
        _qa_cache[dispatch_id]["override_by"] = override_by

    # Update Supabase
    try:
        from services.supabase_client import get_client

        client = get_client()
        if client:
            client.table("qa_reviews").update({
                "consensus_verdict": action,
                "deployed": action == "deploy",
                "override_by": override_by,
            }).eq("dispatch_id", dispatch_id).execute()
    except Exception as e:
        logger.warning("Supabase override update failed: %s", e)

    return {
        "dispatch_id": dispatch_id,
        "action": action,
        "override_by": override_by,
        "status": "overridden",
    }
