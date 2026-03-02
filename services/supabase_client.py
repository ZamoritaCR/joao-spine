"""Async Supabase CRUD + queries."""

from __future__ import annotations

import logging
import os
from typing import Any

from supabase import create_client, Client

from models.schemas import AgentOutputRecord, IdeaVaultRecord, SessionLogRecord

logger = logging.getLogger(__name__)

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        _client = create_client(url, key)
    return _client


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


async def query_recent_activity(limit: int = 10) -> list[dict[str, Any]]:
    client = get_client()
    result = (
        client.table("session_log")
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
