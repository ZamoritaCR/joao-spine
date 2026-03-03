"""QA Consensus Pipeline — Ollama does work, Sonnet + GPT + Opus review."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import anthropic
import httpx

logger = logging.getLogger(__name__)

# Shared review prompt template
_REVIEW_PROMPT = """Review this code change for deployment readiness.

TASK: {task_summary}
AGENT: {agent}
FILES CHANGED: {files_changed}
CODE DIFF:
{code_diff}
TEST RESULTS: {test_results}

Score 1-10 on each:
- correctness: Does it do what was asked?
- security: Any vulnerabilities?
- quality: Clean, maintainable code?
- completeness: All requirements met?

Return ONLY valid JSON (no markdown, no code fences):
{{"score": <1-10 average of above>, "verdict": "<pass|fail|needs_revision>", "feedback": "<brief explanation>"}}
"""


def _build_review_prompt(
    task_summary: str,
    agent: str,
    code_diff: str,
    files_changed: list[str] | None = None,
    test_results: str | None = None,
) -> str:
    return _REVIEW_PROMPT.format(
        task_summary=task_summary,
        agent=agent,
        files_changed=", ".join(files_changed) if files_changed else "N/A",
        code_diff=code_diff or "No diff provided",
        test_results=test_results or "No test results provided",
    )


def _parse_review_json(raw: str) -> dict:
    """Extract JSON from model response, handling markdown fences."""
    text = raw.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
        score = int(data.get("score", 0))
        verdict = data.get("verdict", "fail")
        if verdict not in ("pass", "fail", "needs_revision"):
            verdict = "fail"
        return {
            "score": max(1, min(10, score)),
            "verdict": verdict,
            "feedback": str(data.get("feedback", "")),
        }
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning("Failed to parse review JSON: %s — raw: %s", e, text[:200])
        return {"score": 0, "verdict": "fail", "feedback": f"Parse error: {text[:500]}"}


async def review_with_sonnet(
    task_summary: str,
    agent: str,
    code_diff: str,
    files_changed: list[str] | None = None,
    test_results: str | None = None,
) -> dict:
    """Review code using Claude Sonnet. Returns {score, verdict, feedback, model}."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"score": 0, "verdict": "fail", "feedback": "ANTHROPIC_API_KEY not set", "model": "sonnet"}

    prompt = _build_review_prompt(task_summary, agent, code_diff, files_changed, test_results)
    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in response.content if b.type == "text")
        result = _parse_review_json(raw)
        result["model"] = "sonnet"
        return result
    except Exception as e:
        logger.error("Sonnet review failed: %s", e)
        return {"score": 0, "verdict": "fail", "feedback": f"Sonnet error: {e}", "model": "sonnet"}


async def review_with_gpt(
    task_summary: str,
    agent: str,
    code_diff: str,
    files_changed: list[str] | None = None,
    test_results: str | None = None,
) -> dict:
    """Review code using GPT-4o. Returns {score, verdict, feedback, model}."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"score": 0, "verdict": "fail", "feedback": "OPENAI_API_KEY not set", "model": "gpt-4o"}

    prompt = _build_review_prompt(task_summary, agent, code_diff, files_changed, test_results)
    model = os.environ.get("OPENAI_MODEL", "gpt-4o")
    try:
        async with httpx.AsyncClient(timeout=60.0) as http:
            resp = await http.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            raw = data["choices"][0]["message"]["content"]
            result = _parse_review_json(raw)
            result["model"] = model
            return result
    except Exception as e:
        logger.error("GPT review failed: %s", e)
        return {"score": 0, "verdict": "fail", "feedback": f"GPT error: {e}", "model": model}


async def review_with_opus(
    task_summary: str,
    agent: str,
    code_diff: str,
    files_changed: list[str] | None = None,
    test_results: str | None = None,
) -> dict:
    """Review code using Claude Opus. Returns {score, verdict, feedback, model}."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"score": 0, "verdict": "fail", "feedback": "ANTHROPIC_API_KEY not set", "model": "opus"}

    prompt = _build_review_prompt(task_summary, agent, code_diff, files_changed, test_results)
    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in response.content if b.type == "text")
        result = _parse_review_json(raw)
        result["model"] = "opus"
        return result
    except Exception as e:
        logger.error("Opus review failed: %s", e)
        return {"score": 0, "verdict": "fail", "feedback": f"Opus error: {e}", "model": "opus"}


def _compute_consensus(
    sonnet: dict, gpt: dict, opus: dict
) -> tuple[str, float]:
    """Compute consensus verdict and average score.

    Returns (consensus_verdict, avg_score):
    - 'deploy'  — all 3 scored >= 8
    - 'review'  — 2 of 3 scored >= 8
    - 'reject'  — any score < 5
    - 'review'  — everything else
    """
    scores = [sonnet["score"], gpt["score"], opus["score"]]
    avg = sum(scores) / 3.0

    # Any score < 5 → reject
    if any(s < 5 for s in scores):
        return "reject", round(avg, 2)

    # All 3 >= 8 → auto-deploy
    high = sum(1 for s in scores if s >= 8)
    if high == 3:
        return "deploy", round(avg, 2)

    # 2 of 3 >= 8 → flag for review
    if high >= 2:
        return "review", round(avg, 2)

    return "review", round(avg, 2)


async def run_qa_consensus(
    dispatch_id: str,
    agent: str,
    task_summary: str,
    code_diff: str,
    files_changed: list[str] | None = None,
    test_results: str | None = None,
) -> dict:
    """Run all 3 reviewers in parallel and return consensus result.

    Returns:
        {
            "dispatch_id": str,
            "reviews": {"sonnet": {...}, "gpt": {...}, "opus": {...}},
            "consensus_verdict": "deploy" | "review" | "reject",
            "avg_score": float,
            "deploy_ready": bool,
        }
    """
    sonnet_result, gpt_result, opus_result = await asyncio.gather(
        review_with_sonnet(task_summary, agent, code_diff, files_changed, test_results),
        review_with_gpt(task_summary, agent, code_diff, files_changed, test_results),
        review_with_opus(task_summary, agent, code_diff, files_changed, test_results),
    )

    consensus_verdict, avg_score = _compute_consensus(sonnet_result, gpt_result, opus_result)

    return {
        "dispatch_id": dispatch_id,
        "reviews": {
            "sonnet": sonnet_result,
            "gpt": gpt_result,
            "opus": opus_result,
        },
        "consensus_verdict": consensus_verdict,
        "avg_score": avg_score,
        "deploy_ready": consensus_verdict == "deploy",
    }
