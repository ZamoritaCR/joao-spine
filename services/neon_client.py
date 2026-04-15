"""
Neon PostgreSQL client for JOAO high-frequency telemetry.
Stores LLM routing decisions, API call logs, execution proofs.
Complements Supabase (which handles user data, memory, sessions).
"""
import os
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("joao.neon")

NEON_URL = os.environ.get("NEON_DATABASE_URL", "")

_pool = None


async def _get_pool():
    global _pool
    if _pool is None:
        try:
            import asyncpg
            _pool = await asyncpg.create_pool(NEON_URL, min_size=1, max_size=5, command_timeout=10)
            logger.info("Neon pool connected")
        except ImportError:
            logger.error("asyncpg not installed — run: pip install asyncpg")
            raise
    return _pool


async def ensure_tables() -> bool:
    """Create tables if they don't exist."""
    if not NEON_URL:
        return False
    try:
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_routing_log (
                    id BIGSERIAL PRIMARY KEY,
                    ts TIMESTAMPTZ DEFAULT now(),
                    task_type TEXT,
                    provider TEXT,
                    model TEXT,
                    tokens_in INT,
                    tokens_out INT,
                    latency_ms INT,
                    ok BOOLEAN
                );
                CREATE TABLE IF NOT EXISTS execution_proof (
                    id BIGSERIAL PRIMARY KEY,
                    ts TIMESTAMPTZ DEFAULT now(),
                    agent TEXT,
                    task_summary TEXT,
                    result_summary TEXT,
                    ok BOOLEAN,
                    metadata JSONB
                );
            """)
        return True
    except Exception as e:
        logger.error(f"Neon ensure_tables failed: {e}")
        return False


async def log_llm_call(task_type: str, provider: str, model: str, tokens_in: int = 0, tokens_out: int = 0, latency_ms: int = 0, ok: bool = True) -> None:
    """Log an LLM routing decision to Neon. Fire-and-forget."""
    if not NEON_URL:
        return
    try:
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO llm_routing_log(task_type,provider,model,tokens_in,tokens_out,latency_ms,ok) VALUES($1,$2,$3,$4,$5,$6,$7)",
                task_type, provider, model, tokens_in, tokens_out, latency_ms, ok
            )
    except Exception as e:
        logger.warning(f"Neon log_llm_call failed (non-fatal): {e}")


async def log_execution_proof(agent: str, task_summary: str, result_summary: str, ok: bool = True, metadata: Optional[dict] = None) -> None:
    """Log a Council agent execution proof."""
    if not NEON_URL:
        return
    try:
        import json
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO execution_proof(agent,task_summary,result_summary,ok,metadata) VALUES($1,$2,$3,$4,$5)",
                agent, task_summary[:500], result_summary[:500], ok, json.dumps(metadata or {})
            )
    except Exception as e:
        logger.warning(f"Neon log_execution_proof failed (non-fatal): {e}")


async def health_check() -> dict:
    if not NEON_URL:
        return {"ok": False, "error": "NEON_DATABASE_URL not set"}
    try:
        pool = await _get_pool()
        async with pool.acquire() as conn:
            result = await conn.fetchval("SELECT 1")
            return {"ok": True, "ping": result == 1}
    except Exception as e:
        return {"ok": False, "error": str(e)}
