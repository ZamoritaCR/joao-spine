"""Council tools — joao_council_dispatch, joao_council_status, joao_agent_output, joao_qa_review."""

from __future__ import annotations

import logging

from services import dispatch, qa_pipeline

logger = logging.getLogger(__name__)


async def joao_council_dispatch(
    agent: str,
    task: str,
    priority: str = "normal",
    context: str = "",
    project: str = "",
) -> str:
    """Dispatch a task to any Council agent (BYTE, ARIA, CJ, SOFIA, DEX, GEMMA, MAX, etc.).

    The agent receives the task in their dedicated tmux session on ROG Strix and
    executes autonomously. Use this to delegate work to the right specialist.

    Args:
        agent: Agent name — BYTE (engineering), ARIA (architecture), CJ (product),
               SOFIA (design), DEX (infrastructure), GEMMA (research), MAX (multi-LLM),
               LEX (legal), NOVA (growth), SAGE (strategy), FLUX (rapid scripts),
               CORE (documentation), APEX (data/ETL), IRIS (integrations), VOLT (CI/CD)
        task: Detailed task description — be specific, agents operate autonomously
        priority: normal | urgent | critical
        context: Additional context the agent should know
        project: Project name if task is scoped to a specific project
    """
    try:
        result = await dispatch.dispatch_to_agent(
            agent=agent,
            task=task,
            priority=priority,
            context=context or None,
            project=project or None,
        )
    except Exception as e:
        logger.exception("joao_council_dispatch failed: agent=%s", agent)
        return f"Dispatch failed: {e}"

    try:
        from models.schemas import DispatchLogRecord
        from services import supabase_client
        await supabase_client.insert_dispatch_log(
            DispatchLogRecord(
                agent=agent,
                task=task,
                priority=priority,
                project=project or None,
                status=result.get("status", "unknown"),
                session=result.get("session"),
            )
        )
    except Exception:
        logger.warning("Failed to log council dispatch to Supabase", exc_info=True)

    return (
        f"Dispatched to {agent}\n"
        f"Task: {task[:200]}\n"
        f"Priority: {priority}\n"
        f"Session: {result.get('session', 'unknown')}\n"
        f"Status: {result.get('status', 'unknown')}"
    )


async def joao_council_status() -> str:
    """Get status of all Council agents — which are active in their tmux sessions.

    Returns a list of all 16 Council agents and whether their tmux session
    is running on ROG Strix (192.168.0.55).
    """
    try:
        result = await dispatch.get_agents()
        agents = result.get("agents", {})
        lines = ["Council Status:"]
        for name, info in agents.items():
            status = "ACTIVE" if info.get("active") else "INACTIVE"
            lines.append(f"  {name}: {status} — session: {info.get('session', '?')}")
        return "\n".join(lines)
    except Exception as e:
        logger.exception("joao_council_status failed")
        return f"Failed to reach Council API: {e}"


async def joao_agent_output(agent: str) -> str:
    """Get recent terminal output from a specific Council agent's tmux session.

    Captures the last 100 lines of the agent's terminal buffer from ROG Strix.
    Use this to check what an agent is currently doing or what it produced.

    Args:
        agent: Agent name (BYTE, ARIA, CJ, SOFIA, DEX, GEMMA, MAX, LEX, NOVA, SAGE,
               FLUX, CORE, APEX, IRIS, VOLT, SCOUT)
    """
    try:
        result = await dispatch.get_session(agent)
        return (
            f"Agent: {result.get('agent', agent)}\n"
            f"Session: {result.get('session', '?')}\n"
            f"--- Output ---\n{result.get('output', 'No output')}"
        )
    except Exception as e:
        logger.exception("joao_agent_output failed: agent=%s", agent)
        return f"Failed to get output for {agent}: {e}"


async def joao_qa_review(
    agent: str,
    task_summary: str,
    code_diff: str,
    files_changed: str = "",
    test_results: str = "",
) -> str:
    """Trigger a 3-model QA review on agent output — Sonnet + GPT-4o + Opus consensus.

    Runs all three reviewers in parallel. Scores correctness, security, quality,
    and completeness. Returns individual scores plus a consensus verdict:
    'deploy' (all >= 8), 'review' (2 of 3 >= 8), or 'reject' (any < 5).

    Args:
        agent: Agent that produced the work (e.g. BYTE)
        task_summary: What the agent was asked to do
        code_diff: The code diff or output to review
        files_changed: Comma-separated list of changed files (optional)
        test_results: Test output if available (optional)
    """
    files_list = [f.strip() for f in files_changed.split(",") if f.strip()] if files_changed else None

    try:
        from datetime import datetime, timezone
        dispatch_id = datetime.now(timezone.utc).strftime("qa_%Y%m%dT%H%M%SZ")

        result = await qa_pipeline.run_qa_consensus(
            dispatch_id=dispatch_id,
            agent=agent,
            task_summary=task_summary,
            code_diff=code_diff,
            files_changed=files_list,
            test_results=test_results or None,
        )

        reviews = result.get("reviews", {})
        lines = [
            f"QA Review — {dispatch_id}",
            f"Agent: {agent}",
            f"Consensus: {result.get('consensus_verdict', '?').upper()}",
            f"Avg Score: {result.get('avg_score', 0)}/10",
            f"Deploy Ready: {'YES' if result.get('deploy_ready') else 'NO'}",
            "",
            "Individual Reviews:",
        ]
        for model, review in reviews.items():
            lines.append(
                f"  {model}: {review.get('score')}/10 [{review.get('verdict')}] "
                f"— {review.get('feedback', '')[:150]}"
            )
        return "\n".join(lines)

    except Exception as e:
        logger.exception("joao_qa_review failed")
        return f"QA review failed: {e}"
