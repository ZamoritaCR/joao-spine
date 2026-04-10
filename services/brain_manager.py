"""Dual-brain persistent context manager -- Claude + GPT with Supabase memory."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import anthropic
import openai
from supabase import create_client, Client

logger = logging.getLogger(__name__)

LIMITS = {"claude": 140_000, "gpt": 90_000}

_supabase: Client | None = None


def _get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_KEY"]
        _supabase = create_client(url, key)
    return _supabase


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


# ── Context (rolling conversation history per model) ──────────────────────


def get_context(model: str, limit: int = 50) -> list[dict]:
    """Pull rolling history from brain_context for a given model."""
    sb = _get_supabase()
    resp = (
        sb.table("brain_context")
        .select("role, content")
        .eq("model", model)
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    )
    return [{"role": r["role"], "content": r["content"]} for r in resp.data]


def append_message(model: str, role: str, content: str) -> None:
    """Store a message to brain_context."""
    sb = _get_supabase()
    sb.table("brain_context").insert({
        "model": model,
        "role": role,
        "content": content,
        "token_estimate": _estimate_tokens(content),
    }).execute()


# ── Memory (persistent key-value brain_memory) ───────────────────────────


def get_memory() -> dict[str, str]:
    """Return all brain_memory as {key: value}."""
    sb = _get_supabase()
    resp = sb.table("brain_memory").select("key, value").execute()
    return {r["key"]: r["value"] for r in resp.data}


def get_memory_string() -> str:
    """Format memory as a readable block for system prompts."""
    mem = get_memory()
    if not mem:
        return ""
    lines = [f"- {k}: {v}" for k, v in mem.items()]
    return "PERSISTENT MEMORY:\n" + "\n".join(lines)


def set_memory(key: str, value: str) -> None:
    """Upsert a key-value pair in brain_memory."""
    sb = _get_supabase()
    sb.table("brain_memory").upsert(
        {"key": key, "value": value, "updated_at": datetime.now(timezone.utc).isoformat()},
        on_conflict="key",
    ).execute()


# ── Compression ──────────────────────────────────────────────────────────


def _maybe_compress(model: str) -> None:
    """If total tokens for a model exceed the limit, summarize oldest half."""
    sb = _get_supabase()
    rows = (
        sb.table("brain_context")
        .select("id, token_estimate, content, role")
        .eq("model", model)
        .order("created_at", desc=False)
        .execute()
    ).data

    total = sum(r["token_estimate"] for r in rows)
    if total <= LIMITS.get(model, 90_000):
        return

    # Take oldest half
    half = len(rows) // 2
    old_rows = rows[:half]
    old_text = "\n".join(f"{r['role']}: {r['content']}" for r in old_rows)

    # Summarize using the same model
    summary_prompt = (
        "Summarize the following conversation history into a concise context paragraph. "
        "Preserve key facts, decisions, and data points:\n\n" + old_text[:8000]
    )

    try:
        if model == "claude":
            client = anthropic.Anthropic()
            resp = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                messages=[{"role": "user", "content": summary_prompt}],
            )
            summary = resp.content[0].text
        else:
            client = openai.OpenAI()
            resp = client.chat.completions.create(
                model="gpt-4o",
                max_tokens=1024,
                messages=[{"role": "user", "content": summary_prompt}],
            )
            summary = resp.choices[0].message.content
    except Exception as e:
        logger.error("Compression failed for %s: %s", model, e)
        return

    # Delete old rows
    old_ids = [r["id"] for r in old_rows]
    for oid in old_ids:
        sb.table("brain_context").delete().eq("id", oid).execute()

    # Insert summary as a system message
    sb.table("brain_context").insert({
        "model": model,
        "role": "system",
        "content": f"[COMPRESSED CONTEXT] {summary}",
        "token_estimate": _estimate_tokens(summary),
    }).execute()

    logger.info("Compressed %d rows for %s (was %d tokens)", half, model, total)


# ── Ask functions ────────────────────────────────────────────────────────


def ask_claude(user_input: str, system: str = "") -> str:
    """Send a message to Claude with persistent context and memory."""
    memory_str = get_memory_string()
    context = get_context("claude")

    system_parts = []
    if system:
        system_parts.append(system)
    if memory_str:
        system_parts.append(memory_str)
    system_text = "\n\n".join(system_parts) or "You are JOAO, an AI exocortex assistant."

    messages = context + [{"role": "user", "content": user_input}]

    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=system_text,
            messages=messages,
        )
        reply = resp.content[0].text
    except Exception as e:
        logger.error("Claude API error: %s", e)
        raise

    append_message("claude", "user", user_input)
    append_message("claude", "assistant", reply)
    _maybe_compress("claude")

    return reply


def ask_gpt(user_input: str, system: str = "") -> str:
    """Send a message to GPT with persistent context and memory."""
    memory_str = get_memory_string()
    context = get_context("gpt")

    system_parts = []
    if system:
        system_parts.append(system)
    if memory_str:
        system_parts.append(memory_str)
    system_text = "\n\n".join(system_parts) or "You are JOAO, an AI exocortex assistant."

    messages = [{"role": "system", "content": system_text}]
    messages.extend(context)
    messages.append({"role": "user", "content": user_input})

    try:
        client = openai.OpenAI()
        resp = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=4096,
            messages=messages,
        )
        reply = resp.choices[0].message.content
    except Exception as e:
        logger.error("GPT API error: %s", e)
        raise

    append_message("gpt", "user", user_input)
    append_message("gpt", "assistant", reply)
    _maybe_compress("gpt")

    return reply
