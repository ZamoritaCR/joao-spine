"""Async Supabase CRUD + queries."""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any

import httpx
from supabase import Client, create_client

from models.schemas import AgentOutputRecord, DispatchLogRecord, IdeaVaultRecord, SessionLogRecord, SubCheck

logger = logging.getLogger(__name__)

_client: Client | None = None
_SESSION_ID_NAMESPACE = uuid.UUID("f5e9a4e2-0e8f-4d98-b6c0-8d2f3e6d3d61")


def normalize_session_id(session_id: str) -> str:
    raw = (session_id or "").strip()
    if not raw:
        return str(uuid.uuid4())
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        return str(uuid.uuid5(_SESSION_ID_NAMESPACE, raw))


def _get_key() -> str:
    """Prefer SUPABASE_SERVICE_ROLE_KEY, fall back to SUPABASE_KEY."""
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY", "")
    if not key:
        raise RuntimeError("Neither SUPABASE_SERVICE_ROLE_KEY nor SUPABASE_KEY is set")
    return key


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        _client = create_client(url, _get_key())
    return _client


def _is_missing_table_error(exc: Exception) -> bool:
    text = str(exc)
    return "Could not find the table" in text or "does not exist" in text or "schema cache" in text


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


async def insert_joao_memory(
    source: str,
    content: str,
    summary: str = "",
    tags: list[str] | None = None,
    project_ref: str | None = None,
    pinned: bool = False,
    summarized: bool = False,
) -> dict[str, Any]:
    client = get_client()
    data = {
        "source": source,
        "content": content,
        "summary": summary or content[:200],
        "tags": tags or [],
        "project_ref": project_ref,
        "pinned": pinned,
        "summarized": summarized,
    }
    try:
        result = client.table("joao_memory").insert(data).execute()
        row = result.data[0] if result.data else {}
        logger.info("joao_memory insert id=%s source=%s", row.get("id"), source)
        return row
    except Exception:
        logger.warning("insert_joao_memory failed", exc_info=True)
        return {}


async def upsert_joao_session(
    session_id: str,
    messages: list[dict[str, Any]],
    name: str = "",
    summary: str = "",
    project_refs: list[str] | None = None,
    source: str = "joao",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    client = get_client()
    normalized_session_id = normalize_session_id(session_id)
    metadata_payload = dict(metadata or {})
    if session_id and session_id != normalized_session_id:
        metadata_payload.setdefault("session_alias", session_id)
    payload = {
        "id": normalized_session_id,
        "messages": {
            "name": name,
            "source": source,
            "metadata": metadata_payload,
            "messages": messages,
        },
        "summary": summary[:200] if summary else "",
        "key_decisions": [],
        "project_refs": project_refs or [],
        "summarized": False,
    }
    try:
        result = client.table("joao_sessions").upsert(payload).execute()
        row = result.data[0] if result.data else {}
        logger.info("joao_sessions upsert id=%s source=%s", normalized_session_id, source)
        return row
    except Exception:
        logger.warning("upsert_joao_session failed", exc_info=True)
        return {}


async def get_joao_session(session_id: str) -> dict[str, Any]:
    client = get_client()
    normalized_session_id = normalize_session_id(session_id)
    try:
        result = client.table("joao_sessions").select("*").eq("id", normalized_session_id).limit(1).execute()
        return result.data[0] if result.data else {}
    except Exception:
        logger.warning("get_joao_session failed", exc_info=True)
        return {}


async def list_joao_sessions(limit: int = 50) -> list[dict[str, Any]]:
    client = get_client()
    try:
        result = client.table("joao_sessions").select("*").order("created_at", desc=True).limit(limit).execute()
        return result.data or []
    except Exception:
        logger.warning("list_joao_sessions failed", exc_info=True)
        return []


async def insert_idea_vault(record: IdeaVaultRecord) -> dict[str, Any]:
    client = get_client()
    data = record.model_dump()
    try:
        result = client.table("idea_vault").insert(data).execute()
        row = result.data[0] if result.data else {}
        logger.info("idea_vault insert id=%s", row.get("id"))
        return row
    except Exception as e:
        if not _is_missing_table_error(e):
            logger.warning("insert_idea_vault failed", exc_info=True)
        return await insert_joao_memory(
            source=f"idea_vault:{record.source}",
            content=record.content,
            summary=record.summary,
            tags=record.tags,
            project_ref=(record.metadata or {}).get("project_ref"),
        )


async def insert_session_log(record: SessionLogRecord) -> dict[str, Any]:
    client = get_client()
    data = record.model_dump()
    try:
        result = client.table("session_log").insert(data).execute()
        row = result.data[0] if result.data else {}
        logger.debug("session_log insert id=%s", row.get("id"))
        return row
    except Exception as e:
        if not _is_missing_table_error(e):
            logger.warning("insert_session_log failed", exc_info=True)
        return await insert_joao_memory(
            source=f"session_log:{record.action}",
            content=f"[{record.endpoint}] {record.input_summary}\n\n{record.output_summary}",
            summary=record.output_summary or record.input_summary,
            tags=[record.action, "session_log"],
            project_ref=record.metadata.get("project_ref") if record.metadata else None,
        )


async def insert_agent_output(record: AgentOutputRecord) -> dict[str, Any]:
    client = get_client()
    data = record.model_dump()
    try:
        result = client.table("agent_outputs").insert(data).execute()
        row = result.data[0] if result.data else {}
        logger.info("agent_outputs insert id=%s", row.get("id"))
        return row
    except Exception as e:
        if not _is_missing_table_error(e):
            logger.warning("insert_agent_output failed", exc_info=True)
        project_ref = record.metadata.get("project_ref") if record.metadata else None
        fallback = {
            "agent": record.session_name,
            "task": record.command,
            "output": record.output,
            "project_tag": project_ref,
            "status": record.status,
            "completed_at": None,
        }
        try:
            result = client.table("hub_dispatches").insert(fallback).execute()
            row = result.data[0] if result.data else {}
            logger.info("hub_dispatches fallback insert id=%s", row.get("id"))
            return row
        except Exception:
            logger.warning("insert_agent_output fallback failed", exc_info=True)
            return {}


async def insert_dispatch_log(record: DispatchLogRecord) -> dict[str, Any]:
    """Log a council dispatch to Supabase for audit trail."""
    client = get_client()
    data = record.model_dump()
    try:
        result = client.table("dispatch_log").insert(data).execute()
        row = result.data[0] if result.data else {}
        logger.info("dispatch_log insert id=%s agent=%s", row.get("id"), record.agent)
        return row
    except Exception:
        logger.warning("insert_dispatch_log failed (table may not exist)", exc_info=True)
        return {}


async def query_recent_activity(limit: int = 10) -> list[dict[str, Any]]:
    client = get_client()
    for table, order_field in (("session_log", "created_at"), ("dispatch_log", "created_at"), ("joao_memory", "created_at")):
        try:
            result = client.table(table).select("*").order(order_field, desc=True).limit(limit).execute()
            if result.data:
                return result.data or []
        except Exception:
            continue
    return []


async def query_memory(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search idea_vault by text match on title, summary, or content; fall back to joao_memory."""
    client = get_client()
    pattern = f"%{query}%"
    try:
        result = (
            client.table("idea_vault")
            .select("*")
            .or_(f"title.ilike.{pattern},summary.ilike.{pattern},content.ilike.{pattern}")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception:
        pass
    try:
        result = (
            client.table("joao_memory")
            .select("*")
            .or_(f"summary.ilike.{pattern},content.ilike.{pattern},source.ilike.{pattern}")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception:
        logger.warning("query_memory failed", exc_info=True)
        return []
