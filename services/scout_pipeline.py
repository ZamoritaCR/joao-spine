"""
SCOUT Intel Scoring Pipeline

Routes intel by tier, generates Claude-powered analysis,
dispatches to Council agents, and writes to Supabase.

Tiers:
  8-10  Critical  -> Claude action plan + Telegram + Supabase + Council dispatch
  5-7   Moderate  -> Claude summary   + Telegram + Supabase
  1-4   Archive   -> Supabase + SQLite (no notification)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).resolve().parent.parent / "scout_intel.db"

# ---------------------------------------------------------------------------
# Claude API
# ---------------------------------------------------------------------------
_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-sonnet-4-20250514"

_CRITICAL_SYSTEM_PROMPT = """\
You are SCOUT's intel analyst for JOAO — Johan's AI Operating System.

JOAO projects: dopamine.watch (data viz marketplace), dopamine.chat (AI chat), TAOP Connect (consulting).

Council agents and roles:
- ARIA: Chief of Staff, orchestration, planning
- BYTE: Frontend/UI engineer
- CJ: Product manager, market analysis
- DEX: DevOps, infrastructure, deployment
- ECHO: Communications, copywriting, social media
- GEMMA: Research, deep analysis, knowledge synthesis
- KODA: Backend engineer, APIs, databases
- NOVA: Creative director, design, branding
- SAGE: Strategy, business development
- SCOUT: Intel scanning (you)

Given an intel signal, output a structured action plan:

**WHAT HAPPENED**
(one sentence)

**WHY IT MATTERS**
- Impact on dopamine.watch: ...
- Impact on dopamine.chat: ...
- Impact on TAOP Connect: ...
(skip projects with no relevance)

**RECOMMENDED ACTIONS**
1. Owner: AGENT — action description
2. Owner: AGENT — action description
(assign concrete agents from the Council)

**URGENCY**: CRITICAL / HIGH / MEDIUM
"""

_MODERATE_SYSTEM_PROMPT = """\
You are SCOUT's intel analyst. Provide brief assessments for a batch of signals.

For each signal, output:
- One-line assessment
- GEMMA research value: high / medium / low
- CJ product impact: high / medium / low

End with one paragraph overall summary of the batch.
"""


async def _call_claude(system: str, user_msg: str) -> str:
    """Call Claude API. Returns text or empty string on failure."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — skipping Claude analysis")
        return ""

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": _MODEL,
        "max_tokens": 1024,
        "system": system,
        "messages": [{"role": "user", "content": user_msg}],
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(_ANTHROPIC_API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            blocks = data.get("content", [])
            return blocks[0]["text"] if blocks else ""
    except Exception:
        logger.exception("Claude API call failed")
        return ""


# ---------------------------------------------------------------------------
# Claude analysis generators
# ---------------------------------------------------------------------------
async def _generate_action_plan(item: dict[str, Any]) -> str:
    user_msg = (
        f"Signal: {item['title']}\n"
        f"Source: {item.get('source', '')}\n"
        f"Score: {item.get('score', 0)}/10\n"
        f"Summary: {item.get('summary', '')}\n"
        f"URL: {item.get('url', '')}"
    )
    return await _call_claude(_CRITICAL_SYSTEM_PROMPT, user_msg)


async def _generate_moderate_summary(items: list[dict[str, Any]]) -> str:
    lines = []
    for i, item in enumerate(items, 1):
        lines.append(
            f"{i}. [{item.get('score', 0)}/10] {item['title']}\n"
            f"   Source: {item.get('source', '')} | {item.get('summary', '')[:120]}"
        )
    user_msg = "Assess these signals:\n\n" + "\n".join(lines)
    return await _call_claude(_MODERATE_SYSTEM_PROMPT, user_msg)


# ---------------------------------------------------------------------------
# Council dispatch
# ---------------------------------------------------------------------------
async def _dispatch_council_task(
    agent: str, task: str, priority: str = "urgent", context: str = ""
) -> bool:
    url = os.environ.get("JOAO_LOCAL_DISPATCH_URL", "")
    secret = os.environ.get("JOAO_DISPATCH_SECRET", "")
    if not url or not secret:
        logger.warning("Council dispatch not configured — skipping")
        return False

    payload = {
        "agent": agent,
        "task": task,
        "priority": priority,
        "lane": "interactive",
        "context": context,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{url}/dispatch",
                json=payload,
                headers={"Authorization": f"Bearer {secret}"},
            )
            if resp.status_code in (200, 201):
                logger.info("Dispatched to %s: %s", agent, task[:80])
                return True
            logger.warning("Dispatch to %s returned %s", agent, resp.status_code)
            return False
    except Exception:
        logger.exception("Council dispatch to %s failed", agent)
        return False


async def _dispatch_followups(plan: str, item: dict[str, Any]) -> list[dict[str, str]]:
    """Parse 'Owner: AGENT' lines from plan and dispatch each."""
    dispatches = []
    pattern = re.compile(r"Owner:\s*(\w+)\s*[—–-]\s*(.+)", re.IGNORECASE)
    for match in pattern.finditer(plan):
        agent = match.group(1).upper()
        action = match.group(2).strip()
        task_text = f"[SCOUT INTEL] {item['title'][:60]} -- {action}"
        ok = await _dispatch_council_task(agent, task_text, priority="urgent", context=plan[:500])
        dispatches.append({"agent": agent, "action": action, "ok": ok})
    return dispatches


# ---------------------------------------------------------------------------
# Supabase (graceful)
# ---------------------------------------------------------------------------
import re as _re

# Known columns per table from current Supabase schema (updated by startup migration)
# These are the columns confirmed present; pipeline will skip unknown ones
_SUPABASE_KNOWN_COLUMNS: dict[str, set[str]] = {
    "scout_intel": {"source", "category", "title", "summary", "url", "score",
                    "action_plan", "tier", "hash", "dispatches"},
    "scout_archive": {"source", "category", "title", "summary", "url", "score", "tier", "hash"},
}


async def _write_supabase(table: str, record: dict[str, Any]) -> bool:
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        logger.debug("Supabase not configured — skipping %s write", table)
        return False

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    # Strip any columns not yet in the schema to prevent PGRST204 errors
    known = _SUPABASE_KNOWN_COLUMNS.get(table)
    if known:
        dropped = [k for k in record if k not in known]
        if dropped:
            logger.error(
                "Supabase %s: dropping unknown columns %s — schema migration needed. "
                "Set SUPABASE_DB_PASSWORD in Railway and redeploy to fix. "
                "Manual fix: run ~/scripts/fix_schema.py",
                table, dropped,
            )
            record = {k: v for k, v in record.items() if k in known}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{url}/rest/v1/{table}", json=record, headers=headers)
            if resp.status_code in (200, 201):
                logger.debug("Supabase %s write OK", table)
                return True
            # PGRST204: column still missing from schema cache — extract and strip
            if resp.status_code == 400:
                try:
                    err = resp.json()
                    if err.get("code") == "PGRST204":
                        msg = err.get("message", "")
                        m = _re.search(r"'(\w+)' column", msg)
                        if m:
                            bad_col = m.group(1)
                            logger.error(
                                "Supabase %s column '%s' not in schema cache — stripping and retrying",
                                table, bad_col,
                            )
                            # Update known columns cache so future writes skip this column too
                            if table in _SUPABASE_KNOWN_COLUMNS:
                                _SUPABASE_KNOWN_COLUMNS[table].discard(bad_col)
                            filtered = {k: v for k, v in record.items() if k != bad_col}
                            resp2 = await client.post(f"{url}/rest/v1/{table}", json=filtered, headers=headers)
                            if resp2.status_code in (200, 201):
                                return True
                except Exception:
                    pass
            logger.warning("Supabase %s write returned %s: %s", table, resp.status_code, resp.text[:200])
            return False
    except Exception:
        logger.warning("Supabase %s write failed", table, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# SQLite archive (local fallback for score 1-4)
# ---------------------------------------------------------------------------
def _write_sqlite_archive(item: dict[str, Any]) -> None:
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        conn.execute(
            "INSERT OR IGNORE INTO scout_archive "
            "(source, category, title, summary, url, score, hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                item.get("source", ""),
                item.get("category", ""),
                item["title"],
                item.get("summary", ""),
                item.get("url", ""),
                item.get("score", 0),
                item.get("hash", ""),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        logger.warning("SQLite archive write failed", exc_info=True)


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------
def _format_action_plan_telegram(item: dict[str, Any], plan: str) -> str:
    score = item.get("score", 0)
    title = item["title"][:100]
    source = item.get("source", "")
    url = item.get("url", "")

    lines = [
        f"<b>[SCOUT CRITICAL {score}/10]</b>",
        f"<b>{title}</b>",
        f"Source: {source}",
        "",
        plan[:3000],
    ]
    if url:
        lines.append(f'\n<a href="{url}">Source Link</a>')
    return "\n".join(lines)


def _format_moderate_telegram(items: list[dict[str, Any]], summary: str) -> str:
    header = f"<b>[SCOUT INTEL] {len(items)} moderate signals (5-7)</b>\n{'—' * 20}\n"
    item_lines = []
    for item in items[:10]:
        score = item.get("score", 0)
        title = item["title"][:80]
        item_lines.append(f"• [{score}/10] {title}")

    body = "\n".join(item_lines)
    analysis = f"\n\n<b>Analysis:</b>\n{summary[:2000]}" if summary else ""
    return f"{header}\n{body}{analysis}"


# ---------------------------------------------------------------------------
# Telegram sender (reuse from scout module)
# ---------------------------------------------------------------------------
_TELEGRAM_CHAT_ID_FALLBACK = "7670125439"  # Johan's numeric chat ID


def _resolve_telegram_chat_id() -> str:
    """Return numeric chat ID. Falls back to known good ID if env has @username."""
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not chat_id:
        logger.warning("TELEGRAM_CHAT_ID not set — using fallback %s", _TELEGRAM_CHAT_ID_FALLBACK)
        return _TELEGRAM_CHAT_ID_FALLBACK
    if not chat_id.lstrip("-").isdigit():
        logger.warning(
            "TELEGRAM_CHAT_ID=%r is not numeric — using fallback %s. "
            "Fix: set TELEGRAM_CHAT_ID=%s in Railway environment variables.",
            chat_id, _TELEGRAM_CHAT_ID_FALLBACK, _TELEGRAM_CHAT_ID_FALLBACK,
        )
        return _TELEGRAM_CHAT_ID_FALLBACK
    return chat_id


async def _send_telegram(message: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = _resolve_telegram_chat_id()
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not configured")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            if resp.status_code == 400:
                logger.warning("Telegram HTML parse failed, retrying as plain text")
                payload.pop("parse_mode")
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    return True
            logger.warning("Telegram returned %s: %s", resp.status_code, resp.text[:200])
            return False
    except Exception:
        logger.exception("Telegram send failed")
        return False


# ---------------------------------------------------------------------------
# Tier handlers
# ---------------------------------------------------------------------------
async def _handle_critical_tier(items: list[dict[str, Any]]) -> None:
    """Score 8-10: Claude action plan -> Telegram -> Supabase -> Council dispatch."""
    logger.info("Pipeline: %d critical items (8-10)", len(items))
    for item in items:
        # 1. Generate action plan via Claude
        plan = await _generate_action_plan(item)
        if not plan:
            plan = f"(Auto-analysis unavailable)\nTitle: {item['title']}\nScore: {item.get('score', 0)}/10"

        # 2. Telegram: full structured plan
        msg = _format_action_plan_telegram(item, plan)
        await _send_telegram(msg)

        # 3. Supabase: write to scout_intel
        dispatches: list[dict[str, str]] = []
        record = {
            "source": item.get("source", ""),
            "category": item.get("category", ""),
            "title": item["title"],
            "summary": item.get("summary", ""),
            "url": item.get("url", ""),
            "score": item.get("score", 0),
            "action_plan": plan,
            "tier": "critical",
            "hash": item.get("hash", ""),
            "dispatches": json.dumps(dispatches),
        }

        # 4. Council dispatch (fire-and-forget)
        dispatches = await _dispatch_followups(plan, item)
        record["dispatches"] = json.dumps(dispatches)

        await _write_supabase("scout_intel", record)

        await asyncio.sleep(0.5)  # Rate limiting


async def _handle_moderate_tier(items: list[dict[str, Any]]) -> None:
    """Score 5-7: Claude batch summary -> Telegram -> Supabase."""
    if not items:
        return
    logger.info("Pipeline: %d moderate items (5-7)", len(items))

    # 1. Claude batch assessment
    summary = await _generate_moderate_summary(items)

    # 2. Telegram: summary
    msg = _format_moderate_telegram(items, summary)
    await _send_telegram(msg)

    # 3. Supabase: write each item
    for item in items:
        record = {
            "source": item.get("source", ""),
            "category": item.get("category", ""),
            "title": item["title"],
            "summary": item.get("summary", ""),
            "url": item.get("url", ""),
            "score": item.get("score", 0),
            "action_plan": "",
            "tier": "moderate",
            "hash": item.get("hash", ""),
            "dispatches": "[]",
        }
        await _write_supabase("scout_intel", record)


async def _handle_archive_tier(items: list[dict[str, Any]]) -> None:
    """Score 1-4: Supabase + SQLite archive, no notification."""
    if not items:
        return
    logger.info("Pipeline: %d archive items (1-4)", len(items))

    for item in items:
        # 1. Supabase archive
        record = {
            "source": item.get("source", ""),
            "category": item.get("category", ""),
            "title": item["title"],
            "summary": item.get("summary", ""),
            "url": item.get("url", ""),
            "score": item.get("score", 0),
            "hash": item.get("hash", ""),
        }
        await _write_supabase("scout_archive", record)

        # 2. SQLite fallback
        _write_sqlite_archive(item)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def process_intel_pipeline(new_items: list[dict[str, Any]]) -> None:
    """Route new intel items through the scoring pipeline by tier."""
    if not new_items:
        logger.info("Pipeline: no new items to process")
        return

    critical = [i for i in new_items if i.get("score", 0) >= 8]
    moderate = [i for i in new_items if 5 <= i.get("score", 0) <= 7]
    archive = [i for i in new_items if i.get("score", 0) <= 4]

    logger.info(
        "Pipeline: %d items -> %d critical, %d moderate, %d archive",
        len(new_items), len(critical), len(moderate), len(archive),
    )

    # Run tiers in parallel
    await asyncio.gather(
        _handle_critical_tier(critical),
        _handle_moderate_tier(moderate),
        _handle_archive_tier(archive),
        return_exceptions=True,
    )

    logger.info("Pipeline: processing complete")
