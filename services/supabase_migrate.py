"""
Supabase startup migration for joao-spine.

Runs on app startup if SUPABASE_DB_PASSWORD is set in the environment.
Uses direct psycopg2 connection to the Supabase pooler.

To apply migrations: set SUPABASE_DB_PASSWORD in Railway environment variables.
  Railway dashboard > joao-spine service > Variables > SUPABASE_DB_PASSWORD
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_SUPABASE_PROJECT_REF = "wkfewpynskakgbetscsa"

_MIGRATIONS = [
    "ALTER TABLE public.scout_archive ADD COLUMN IF NOT EXISTS hash text",
    "ALTER TABLE public.scout_archive ADD COLUMN IF NOT EXISTS category text",
    "ALTER TABLE public.scout_archive ADD COLUMN IF NOT EXISTS tier text",
    "ALTER TABLE public.scout_archive ADD COLUMN IF NOT EXISTS source text",
    "ALTER TABLE public.scout_intel ADD COLUMN IF NOT EXISTS dispatches jsonb DEFAULT '[]'::jsonb",
    "ALTER TABLE public.scout_intel ADD COLUMN IF NOT EXISTS action_plan text",
    "ALTER TABLE public.scout_intel ADD COLUMN IF NOT EXISTS tier text",
    "ALTER TABLE public.scout_intel ADD COLUMN IF NOT EXISTS source text",
    "ALTER TABLE public.scout_intel ADD COLUMN IF NOT EXISTS hash text",
]


def run_startup_migrations() -> None:
    """Run schema migrations on startup if SUPABASE_DB_PASSWORD is configured."""
    db_password = os.environ.get("SUPABASE_DB_PASSWORD", "")
    if not db_password:
        logger.info(
            "SUPABASE_DB_PASSWORD not set — skipping startup migrations. "
            "Set this env var in Railway to auto-apply schema changes on next deploy."
        )
        return

    try:
        import psycopg2
    except ImportError:
        logger.warning("psycopg2 not available — cannot run startup migrations")
        return

    conn_str = (
        f"postgresql://postgres.{_SUPABASE_PROJECT_REF}:{db_password}"
        f"@aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require"
    )
    try:
        conn = psycopg2.connect(conn_str, connect_timeout=10)
        conn.autocommit = True
        cur = conn.cursor()
        applied = 0
        for sql in _MIGRATIONS:
            cur.execute(sql)
            applied += 1
        conn.close()
        logger.info("Startup migrations complete: %d statements applied", applied)
    except Exception as e:
        logger.error("Startup migration failed: %s", e)
        logger.error(
            "To fix manually, run the SQL from ~/scripts/fix_schema.py in the Supabase SQL Editor: "
            "https://supabase.com/dashboard/project/%s/sql",
            _SUPABASE_PROJECT_REF,
        )
