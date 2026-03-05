"""
SCOUT -- Signals. Curated. Opportunity. Updates. Telemetry.

Async intel-scanning service: fetches sources, scores actionability,
deduplicates via content hash, delivers via Telegram + email.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import sqlite3
import smtplib
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import httpx

from services.scout_pipeline import process_intel_pipeline

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_DB_PATH = Path(__file__).resolve().parent.parent / "scout_intel.db"
_INTEL_MD_PATH = Path(__file__).resolve().parent.parent / "SCOUT_INTEL.md"
_SCAN_INTERVAL_SECONDS = 3600        # 1 hour
_BRIEF_INTERVAL_SECONDS = 6 * 3600   # 6 hours
_CRITICAL_THRESHOLD = 9
_DIGEST_THRESHOLD = 7
_EMAIL_TO = "johan@theartofthepossible.io"

# Keyword scoring weights
_SCORE_KEYWORDS: dict[str, int] = {
    # Critical / high-signal
    "claude": 3, "anthropic": 3, "opus": 2, "sonnet": 2, "haiku": 2,
    "gpt-5": 3, "gpt5": 3, "openai": 2, "gemini": 2,
    "llm": 2, "large language model": 2, "foundation model": 2,
    "agent": 2, "agentic": 2, "mcp": 2, "model context protocol": 3,
    "rag": 2, "retrieval augmented": 2,
    "fine-tune": 2, "fine tune": 2, "finetuning": 2,
    "open source": 1, "open-source": 1, "oss": 1,
    "reasoning": 2, "chain of thought": 2, "cot": 1,
    "tool use": 2, "function calling": 2,
    "multimodal": 2, "vision": 1, "audio": 1,
    "embedding": 1, "vector": 1,
    "benchmark": 1, "eval": 1, "leaderboard": 1,
    "startup": 1, "funding": 1, "acquisition": 1,
    "api": 1, "sdk": 1, "framework": 1,
    "python": 1, "typescript": 1, "rust": 1,
    "tableau": 2, "power bi": 2, "powerbi": 2, "pbix": 2,
    "streamlit": 1, "fastapi": 1,
    "cursor": 1, "copilot": 1, "windsurf": 1, "claude code": 3,
    "devin": 2, "swe-bench": 2,
    "breakthrough": 2, "state of the art": 2, "sota": 2,
    "safety": 1, "alignment": 1, "rlhf": 1,
}

# Source definitions
SOURCES: list[dict[str, str]] = [
    {"name": "Anthropic News", "type": "rss", "url": "https://raw.githubusercontent.com/taobojlen/anthropic-rss-feed/main/anthropic_news_rss.xml"},
    {"name": "HN AI/LLM/Claude", "type": "rss", "url": "https://hnrss.org/newest?q=AI+LLM+Claude"},
    {"name": "ArXiv cs.AI", "type": "rss", "url": "https://rss.arxiv.org/rss/cs.AI"},
    {"name": "TechCrunch AI", "type": "rss", "url": "https://techcrunch.com/tag/artificial-intelligence/feed/"},
    {"name": "Product Hunt", "type": "rss", "url": "https://www.producthunt.com/feed"},
    {"name": "GitHub Trending Python", "type": "github_trending", "url": "https://api.github.com/search/repositories?q=language:python+created:>TODAY&sort=stars&order=desc&per_page=15"},
    {"name": "HN Algolia Top AI", "type": "hn_algolia", "url": "https://hn.algolia.com/api/v1/search_by_date?query=AI+LLM&tags=story&hitsPerPage=20"},
]

# ---------------------------------------------------------------------------
# Database (SQLite local, Supabase when configured)
# ---------------------------------------------------------------------------
_db_initialized = False


def _init_db() -> None:
    global _db_initialized
    if _db_initialized:
        return
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scout_intel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            source TEXT NOT NULL,
            category TEXT DEFAULT '',
            title TEXT NOT NULL,
            summary TEXT DEFAULT '',
            url TEXT DEFAULT '',
            score INTEGER DEFAULT 0,
            delivered INTEGER DEFAULT 0,
            hash TEXT UNIQUE NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scout_hash ON scout_intel(hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scout_score ON scout_intel(score DESC)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scout_archive (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            source TEXT NOT NULL,
            category TEXT DEFAULT '',
            title TEXT NOT NULL,
            summary TEXT DEFAULT '',
            url TEXT DEFAULT '',
            score INTEGER DEFAULT 0,
            hash TEXT UNIQUE NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scout_archive_hash ON scout_archive(hash)")
    conn.commit()
    conn.close()
    _db_initialized = True
    logger.info("SCOUT DB initialized at %s", _DB_PATH)


def _hash_content(title: str, url: str) -> str:
    raw = f"{title.strip().lower()}|{url.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _insert_intel(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Insert items, skip duplicates. Returns list of newly inserted items."""
    _init_db()
    conn = sqlite3.connect(str(_DB_PATH))
    new_items = []
    for item in items:
        h = _hash_content(item["title"], item.get("url", ""))
        try:
            conn.execute(
                "INSERT INTO scout_intel (source, category, title, summary, url, score, hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (item["source"], item.get("category", ""), item["title"],
                 item.get("summary", ""), item.get("url", ""), item.get("score", 0), h),
            )
            new_items.append({**item, "hash": h})
        except sqlite3.IntegrityError:
            pass  # duplicate
    conn.commit()
    conn.close()
    return new_items


def _mark_delivered(hashes: list[str]) -> None:
    if not hashes:
        return
    _init_db()
    conn = sqlite3.connect(str(_DB_PATH))
    placeholders = ",".join("?" for _ in hashes)
    conn.execute(f"UPDATE scout_intel SET delivered = 1 WHERE hash IN ({placeholders})", hashes)
    conn.commit()
    conn.close()


def get_recent_intel(limit: int = 20, min_score: int = 7) -> list[dict[str, Any]]:
    _init_db()
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM scout_intel WHERE score >= ? ORDER BY created_at DESC LIMIT ?",
        (min_score, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_status() -> dict[str, Any]:
    _init_db()
    conn = sqlite3.connect(str(_DB_PATH))
    total = conn.execute("SELECT COUNT(*) FROM scout_intel").fetchone()[0]
    high = conn.execute("SELECT COUNT(*) FROM scout_intel WHERE score >= 7").fetchone()[0]
    critical = conn.execute("SELECT COUNT(*) FROM scout_intel WHERE score >= 9").fetchone()[0]
    latest = conn.execute("SELECT created_at FROM scout_intel ORDER BY created_at DESC LIMIT 1").fetchone()
    conn.close()
    return {
        "status": "running" if _scheduler_running else "stopped",
        "total_signals": total,
        "high_value": high,
        "critical": critical,
        "last_scan": latest[0] if latest else None,
        "db_path": str(_DB_PATH),
    }


# ---------------------------------------------------------------------------
# Scoring Engine
# ---------------------------------------------------------------------------
def _score_item(title: str, summary: str = "") -> int:
    text = f"{title} {summary}".lower()
    score = 0
    for keyword, weight in _SCORE_KEYWORDS.items():
        if keyword in text:
            score += weight
    return min(10, max(1, score))


# ---------------------------------------------------------------------------
# Source Fetchers
# ---------------------------------------------------------------------------
async def _fetch_rss(source: dict[str, str]) -> list[dict[str, Any]]:
    try:
        import feedparser
    except ImportError:
        logger.error("feedparser not installed -- skipping RSS source %s", source["name"])
        return []

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(source["url"], headers={"User-Agent": "SCOUT/1.0"})
            resp.raise_for_status()
    except Exception:
        logger.warning("Failed to fetch RSS: %s", source["name"], exc_info=True)
        return []

    feed = feedparser.parse(resp.text)
    items = []
    for entry in feed.entries[:20]:
        title = entry.get("title", "").strip()
        link = entry.get("link", "")
        summary = entry.get("summary", "")[:300]
        # Strip HTML tags from summary
        summary = re.sub(r"<[^>]+>", "", summary).strip()
        if not title:
            continue
        score = _score_item(title, summary)
        items.append({
            "source": source["name"],
            "category": "rss",
            "title": title,
            "summary": summary,
            "url": link,
            "score": score,
        })
    return items


async def _fetch_github_trending(source: dict[str, str]) -> list[dict[str, Any]]:
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "SCOUT/1.0"}
    if token:
        headers["Authorization"] = f"token {token}"

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    url = source["url"].replace("TODAY", today)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.warning("Failed to fetch GitHub trending", exc_info=True)
        return []

    items = []
    for repo in data.get("items", [])[:15]:
        title = f"{repo['full_name']} ({repo.get('stargazers_count', 0)} stars)"
        summary = (repo.get("description") or "")[:300]
        score = _score_item(title, summary)
        items.append({
            "source": source["name"],
            "category": "github",
            "title": title,
            "summary": summary,
            "url": repo.get("html_url", ""),
            "score": score,
        })
    return items


async def _fetch_hn_algolia(source: dict[str, str]) -> list[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(source["url"])
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.warning("Failed to fetch HN Algolia", exc_info=True)
        return []

    items = []
    for hit in data.get("hits", [])[:20]:
        title = hit.get("title", "").strip()
        if not title:
            continue
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
        points = hit.get("points", 0) or 0
        summary = f"{points} points | {hit.get('num_comments', 0)} comments"
        score = _score_item(title, summary)
        # Boost HN items with high engagement
        if points > 100:
            score = min(10, score + 1)
        if points > 300:
            score = min(10, score + 1)
        items.append({
            "source": source["name"],
            "category": "hn",
            "title": title,
            "summary": summary,
            "url": url,
            "score": score,
        })
    return items


_FETCHER_MAP = {
    "rss": _fetch_rss,
    "github_trending": _fetch_github_trending,
    "hn_algolia": _fetch_hn_algolia,
}


# ---------------------------------------------------------------------------
# Delivery: Telegram
# ---------------------------------------------------------------------------
async def _send_telegram(message: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("Telegram not configured")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            # HTML parse errors return 400 -- retry without formatting
            if resp.status_code == 400 and "parse_mode" in payload:
                logger.warning("Telegram HTML parse failed, retrying as plain text")
                payload.pop("parse_mode")
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    return True
            logger.warning("Telegram returned %s: %s", resp.status_code, resp.text)
            return False
    except Exception:
        logger.exception("Telegram send failed")
        return False


def _format_telegram_item(item: dict[str, Any]) -> str:
    score = item.get("score", 0)
    if score >= 9:
        badge = "[CRITICAL]"
    elif score >= 7:
        badge = "[HOT]"
    else:
        badge = f"[{score}/10]"

    title = item["title"][:100]
    summary = item.get("summary", "")[:120]
    url = item.get("url", "")
    source = item.get("source", "")

    lines = [
        f"<b>{badge} {title}</b>",
        summary,
        f"Score: {score}/10 | {source}",
    ]
    if url:
        lines.append(f'<a href="{url}">Link</a>')
    return "\n".join(lines)


async def _deliver_critical_telegram(items: list[dict[str, Any]]) -> None:
    """Immediately push critical items (score 9-10)."""
    critical = [i for i in items if i.get("score", 0) >= _CRITICAL_THRESHOLD]
    for item in critical:
        msg = f"[SCOUT ALERT]\n\n{_format_telegram_item(item)}"
        await _send_telegram(msg)
        await asyncio.sleep(0.5)


async def _deliver_digest_telegram(items: list[dict[str, Any]]) -> None:
    """Send batched digest of score 7+ items."""
    worthy = [i for i in items if i.get("score", 0) >= _DIGEST_THRESHOLD]
    if not worthy:
        return

    header = f"<b>[SCOUT HOURLY DIGEST] -- {len(worthy)} signals</b>\n{'=' * 30}\n"
    chunks = []
    current = header
    for item in worthy:
        entry = f"\n{_format_telegram_item(item)}\n"
        if len(current) + len(entry) > 4000:
            chunks.append(current)
            current = entry
        else:
            current += entry
    if current:
        chunks.append(current)

    for chunk in chunks:
        await _send_telegram(chunk)
        await asyncio.sleep(0.5)


# ---------------------------------------------------------------------------
# Delivery: Email
# ---------------------------------------------------------------------------
def _build_email_html(items: list[dict[str, Any]], date_str: str) -> str:
    critical = [i for i in items if i.get("score", 0) >= 9]
    hot = [i for i in items if 7 <= i.get("score", 0) < 9]
    stack = [i for i in items if i.get("category") in ("github", "rss") and i.get("score", 0) >= 5]

    def _section(title: str, color: str, section_items: list[dict[str, Any]]) -> str:
        if not section_items:
            return ""
        rows = ""
        for it in section_items:
            url = it.get("url", "#")
            rows += f"""
            <tr>
                <td style="padding:8px 12px;border-bottom:1px solid #eee;">
                    <a href="{url}" style="color:#1a73e8;font-weight:bold;text-decoration:none;">{it['title'][:80]}</a>
                    <br><span style="color:#666;font-size:13px;">{it.get('summary', '')[:150]}</span>
                    <br><span style="background:{color};color:white;padding:2px 6px;border-radius:3px;font-size:11px;font-weight:bold;">{it.get('score', 0)}/10</span>
                    <span style="color:#999;font-size:11px;margin-left:8px;">{it.get('source', '')}</span>
                </td>
            </tr>"""
        return f"""
        <div style="margin:20px 0;">
            <h2 style="color:{color};border-bottom:2px solid {color};padding-bottom:4px;">{title}</h2>
            <table style="width:100%;border-collapse:collapse;">{rows}</table>
        </div>"""

    body = f"""
    <html>
    <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:700px;margin:0 auto;padding:20px;background:#fafafa;">
        <div style="background:linear-gradient(135deg,#1a1a2e,#16213e);color:white;padding:20px;border-radius:8px;text-align:center;">
            <h1 style="margin:0;font-size:24px;">SCOUT BRIEF</h1>
            <p style="margin:5px 0 0;opacity:0.8;">{date_str} -- {len(items)} signals captured</p>
        </div>
        {_section("CRITICAL", "#dc3545", critical)}
        {_section("HOT INTEL", "#fd7e14", hot)}
        {_section("STACK UPDATES", "#0d6efd", stack[:10])}
        <div style="text-align:center;color:#999;font-size:12px;margin-top:30px;padding-top:10px;border-top:1px solid #eee;">
            SCOUT -- Signals. Curated. Opportunity. Updates. Telemetry.<br>
            Built for Johan @ The Art of the Possible
        </div>
    </body>
    </html>"""
    return body


async def send_email_brief(items: list[dict[str, Any]] | None = None) -> bool:
    """Send HTML email brief. Uses SMTP env vars or SendGrid."""
    if items is None:
        items = get_recent_intel(limit=30, min_score=5)
    if not items:
        logger.info("No items for email brief")
        return False

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subject = f"SCOUT BRIEF -- {date_str} -- {len(items)} signals captured"
    html = _build_email_html(items, date_str)

    # Try SendGrid first
    sendgrid_key = os.environ.get("SENDGRID_API_KEY", "")
    if sendgrid_key:
        return await _send_via_sendgrid(sendgrid_key, subject, html)

    # Fall back to SMTP
    smtp_host = os.environ.get("SMTP_HOST", "")
    if smtp_host:
        return _send_via_smtp(subject, html)

    logger.warning("No email transport configured (set SENDGRID_API_KEY or SMTP_HOST)")
    return False


async def _send_via_sendgrid(api_key: str, subject: str, html: str) -> bool:
    from_email = os.environ.get("SENDGRID_FROM", "scout@theartofthepossible.io")
    payload = {
        "personalizations": [{"to": [{"email": _EMAIL_TO}]}],
        "from": {"email": from_email, "name": "SCOUT"},
        "subject": subject,
        "content": [{"type": "text/html", "value": html}],
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            if resp.status_code in (200, 201, 202):
                logger.info("Email sent via SendGrid")
                return True
            logger.warning("SendGrid returned %s: %s", resp.status_code, resp.text)
            return False
    except Exception:
        logger.exception("SendGrid send failed")
        return False


def _send_via_smtp(subject: str, html: str) -> bool:
    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    from_addr = os.environ.get("SMTP_FROM", user)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"SCOUT <{from_addr}>"
    msg["To"] = _EMAIL_TO
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(host, port) as server:
            server.starttls()
            if user and password:
                server.login(user, password)
            server.sendmail(from_addr, _EMAIL_TO, msg.as_string())
        logger.info("Email sent via SMTP")
        return True
    except Exception:
        logger.exception("SMTP send failed")
        return False


# ---------------------------------------------------------------------------
# Intel Markdown Writer
# ---------------------------------------------------------------------------
def write_intel_md() -> None:
    items = get_recent_intel(limit=20, min_score=7)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# SCOUT INTEL",
        f"Last updated: {now}",
        f"Signals: {len(items)} (score 7+)",
        "",
        "---",
        "",
    ]

    for item in items:
        score = item.get("score", 0)
        if score >= 9:
            tag = "[CRITICAL]"
        elif score >= 7:
            tag = "[HOT]"
        else:
            tag = f"[{score}]"

        lines.append(f"### {tag} {item['title'][:100]}")
        lines.append(f"**Source:** {item.get('source', '')} | **Score:** {score}/10")
        if item.get("summary"):
            lines.append(f"> {item['summary'][:200]}")
        if item.get("url"):
            lines.append(f"[Link]({item['url']})")
        lines.append(f"*{item.get('created_at', '')}*")
        lines.append("")

    _INTEL_MD_PATH.write_text("\n".join(lines), encoding="utf-8")
    logger.info("SCOUT_INTEL.md updated with %d items", len(items))


# ---------------------------------------------------------------------------
# Core Scan Loop
# ---------------------------------------------------------------------------
async def run_scan() -> list[dict[str, Any]]:
    """Run a full scan across all sources. Returns newly discovered items."""
    logger.info("SCOUT scan starting...")
    _init_db()

    all_items: list[dict[str, Any]] = []
    tasks = []
    for source in SOURCES:
        fetcher = _FETCHER_MAP.get(source["type"])
        if fetcher:
            tasks.append(fetcher(source))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            logger.warning("Fetcher error: %s", result)
            continue
        all_items.extend(result)

    new_items = _insert_intel(all_items)
    logger.info("SCOUT scan complete: %d fetched, %d new", len(all_items), len(new_items))

    # Route through intel scoring pipeline (handles all Telegram, Supabase, Council dispatch)
    await process_intel_pipeline(new_items)

    # Write intel markdown
    write_intel_md()

    return new_items


async def run_hourly() -> None:
    """Hourly task: scan (pipeline handles all delivery)."""
    await run_scan()


async def run_brief() -> None:
    """6-hour task: send email brief."""
    await send_email_brief()


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
_scheduler_running = False
_scheduler_task: asyncio.Task | None = None


async def _scheduler_loop() -> None:
    """Simple async scheduler -- no APScheduler dependency needed."""
    global _scheduler_running
    _scheduler_running = True
    last_scan = 0.0
    last_brief = 0.0
    logger.info("SCOUT scheduler started")

    while _scheduler_running:
        now = time.monotonic()

        if now - last_scan >= _SCAN_INTERVAL_SECONDS:
            try:
                await run_hourly()
            except Exception:
                logger.exception("Hourly scan failed")
            last_scan = now

        if now - last_brief >= _BRIEF_INTERVAL_SECONDS:
            try:
                await run_brief()
            except Exception:
                logger.exception("Brief send failed")
            last_brief = now

        await asyncio.sleep(60)


def start_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        logger.warning("SCOUT scheduler already running")
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info("SCOUT scheduler task created")


def stop_scheduler() -> None:
    global _scheduler_running, _scheduler_task
    _scheduler_running = False
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
    _scheduler_task = None
    logger.info("SCOUT scheduler stopped")


# ---------------------------------------------------------------------------
# Proof-of-life
# ---------------------------------------------------------------------------
async def send_proof_of_life() -> bool:
    """Send initial Telegram message confirming SCOUT is live."""
    msg = (
        "<b>[SCOUT] ONLINE</b>\n\n"
        "SCOUT is LIVE. First intel drop incoming...\n\n"
        "Signals. Curated. Opportunity. Updates. Telemetry.\n"
        "Scanning sources now."
    )
    return await _send_telegram(msg)
