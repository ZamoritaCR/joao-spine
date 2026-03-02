-- Sprint 2: JOAO Autonomous Dispatch — Database Migration
-- Run this in Supabase SQL Editor: https://supabase.com/dashboard/project/wkfewpynskakgbetscsa/sql
-- Date: 2026-03-02

-- Dispatch log: audit trail for council agent dispatches
CREATE TABLE IF NOT EXISTS dispatch_log (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    agent TEXT NOT NULL,
    task TEXT NOT NULL,
    priority TEXT DEFAULT 'normal',
    project TEXT,
    context TEXT,
    status TEXT DEFAULT 'dispatched',
    session TEXT,
    result TEXT,
    error TEXT,
    dispatched_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dispatch_log_agent ON dispatch_log(agent);
CREATE INDEX IF NOT EXISTS idx_dispatch_log_status ON dispatch_log(status);
CREATE INDEX IF NOT EXISTS idx_dispatch_log_dispatched_at ON dispatch_log(dispatched_at DESC);

-- RLS: service role only (Railway spine uses service role key)
ALTER TABLE dispatch_log ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'dispatch_log' AND policyname = 'Service role full access'
    ) THEN
        CREATE POLICY "Service role full access" ON dispatch_log
            FOR ALL USING (auth.role() = 'service_role');
    END IF;
END $$;
