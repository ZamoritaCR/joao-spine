-- ============================================================================
-- JOÃO Supabase receipt-pipeline fix  (gap-closure-20260416)
-- ============================================================================
-- Root cause:
--   services/supabase_client.py::insert_session_log() writes SessionLogRecord
--   fields (endpoint, action, input_summary, ...) to dispatch_log table, which
--   has a completely different schema (agent, task, priority, ...).
--   Every write since this code deployed has thrown PGRST204 silently.
--   insert_agent_output() writes to agent_outputs which doesn't exist.
--
-- This migration creates the two missing tables with the correct schemas.
-- dispatch_log is left untouched (already works for DispatchLogRecord writes).
--
-- SAFE TO RE-RUN. Uses IF NOT EXISTS.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- session_log : matches models.schemas.SessionLogRecord
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.session_log (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    endpoint         TEXT NOT NULL,
    action           TEXT NOT NULL,
    input_summary    TEXT,
    output_summary   TEXT,
    status           TEXT NOT NULL,
    duration_ms      INTEGER,
    metadata         JSONB NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_session_log_created_at ON public.session_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_session_log_endpoint   ON public.session_log (endpoint);
CREATE INDEX IF NOT EXISTS idx_session_log_action     ON public.session_log (action);
CREATE INDEX IF NOT EXISTS idx_session_log_status     ON public.session_log (status);

-- ----------------------------------------------------------------------------
-- agent_outputs : matches models.schemas.AgentOutputRecord
-- This is the RECEIPT table for dispatch echo-back.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.agent_outputs (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_name   TEXT NOT NULL,
    command        TEXT NOT NULL,
    output         TEXT,
    status         TEXT NOT NULL,
    metadata       JSONB NOT NULL DEFAULT '{}',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_outputs_created_at   ON public.agent_outputs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_outputs_session_name ON public.agent_outputs (session_name);
CREATE INDEX IF NOT EXISTS idx_agent_outputs_status       ON public.agent_outputs (status);

-- ----------------------------------------------------------------------------
-- v_recent_dispatches_with_output : joins hub_dispatches (UI read source)
-- with agent_outputs (receipt). Lets the hub show dispatch+result together.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW public.v_recent_dispatches_with_output AS
SELECT
    d.id            AS dispatch_id,
    d.agent,
    d.task,
    d.status        AS dispatch_status,
    d.dispatched_at,
    d.completed_at,
    ao.id           AS output_id,
    ao.output,
    ao.status       AS output_status,
    ao.created_at   AS output_created_at,
    (ao.id IS NOT NULL) AS has_receipt
FROM public.hub_dispatches d
LEFT JOIN LATERAL (
    SELECT *
    FROM public.agent_outputs a
    WHERE a.session_name = d.agent
      AND a.created_at >= d.dispatched_at
      AND a.created_at <= COALESCE(d.completed_at, d.dispatched_at + INTERVAL '10 minutes')
    ORDER BY a.created_at ASC
    LIMIT 1
) ao ON TRUE
ORDER BY d.dispatched_at DESC;

-- ----------------------------------------------------------------------------
-- RLS
-- ----------------------------------------------------------------------------
ALTER TABLE public.session_log   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_outputs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_read_session_log"   ON public.session_log;
DROP POLICY IF EXISTS "anon_read_agent_outputs" ON public.agent_outputs;
CREATE POLICY "anon_read_session_log"   ON public.session_log   FOR SELECT USING (true);
CREATE POLICY "anon_read_agent_outputs" ON public.agent_outputs FOR SELECT USING (true);
