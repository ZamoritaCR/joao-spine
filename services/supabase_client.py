"""Async Supabase CRUD + queries."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx
from supabase import create_client, Client

from models.schemas import AgentOutputRecord, DispatchLogRecord, IdeaVaultRecord, SessionLogRecord, SubCheck

logger = logging.getLogger(__name__)

_client: Client | None = None


def _get_key() -> str:
    """Prefer SUPABASE_SERVICE_ROLE_KEY, fall back to SUPABASE_KEY."""
    return os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_KEY"]


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        _client = create_client(url, _get_key())
    return _client


async def health_check() -> SubCheck:
    """Probe Supabase REST API connectivity and auth."""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        return SubCheck(ok=False, error="SUPABASE_URL or key not configured")
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{url}/rest/v1/",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
            )
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        if resp.status_code < 500:
            return SubCheck(ok=True, latency_ms=latency_ms)
        return SubCheck(ok=False, latency_ms=latency_ms, error=f"HTTP {resp.status_code}")
    except Exception as e:
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        return SubCheck(ok=False, latency_ms=latency_ms, error=str(e)[:200])


async def insert_idea_vault(record: IdeaVaultRecord) -> dict[str, Any]:
    client = get_client()
    data = record.model_dump()
    result = client.table("idea_vault").insert(data).execute()
    row = result.data[0] if result.data else {}
    logger.info("idea_vault insert id=%s", row.get("id"))
    return row


async def insert_session_log(record: SessionLogRecord) -> dict[str, Any]:
    client = get_client()
    data = record.model_dump()
    result = client.table("session_log").insert(data).execute()
    row = result.data[0] if result.data else {}
    logger.debug("session_log insert id=%s", row.get("id"))
    return row


async def insert_agent_output(record: AgentOutputRecord) -> dict[str, Any]:
    client = get_client()
    data = record.model_dump()
    result = client.table("agent_outputs").insert(data).execute()
    row = result.data[0] if result.data else {}
    logger.info("agent_outputs insert id=%s", row.get("id"))
    return row


async def insert_dispatch_log(record: DispatchLogRecord) -> dict[str, Any]:
    """Log a council dispatch to Supabase for audit trail."""
    client = get_client()
    data = record.model_dump()
    result = client.table("dispatch_log").insert(data).execute()
    row = result.data[0] if result.data else {}
    logger.info("dispatch_log insert id=%s agent=%s", row.get("id"), record.agent)
    return row


async def query_recent_activity(limit: int = 10) -> list[dict[str, Any]]:
    client = get_client()
    result = (
        client.table("dispatch_log")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


async def query_memory(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search idea_vault by text match on title, summary, or content."""
    client = get_client()
    pattern = f"%{query}%"
    result = (
        client.table("idea_vault")
        .select("*")
        .or_(f"title.ilike.{pattern},summary.ilike.{pattern},content.ilike.{pattern}")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []
