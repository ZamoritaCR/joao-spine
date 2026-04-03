-- JOAO Living OS -- Hub Tables
-- Run this in Supabase SQL Editor: https://supabase.com/dashboard/project/wkfewpynskakgbetscsa/sql

CREATE TABLE IF NOT EXISTS joao_memory (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  source text NOT NULL,
  content text NOT NULL,
  summary text,
  tags text[],
  project_ref text,
  pinned boolean DEFAULT false,
  summarized boolean DEFAULT false,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hub_dispatches (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  agent text NOT NULL,
  task text NOT NULL,
  output text,
  project_tag text,
  dispatched_at timestamptz DEFAULT now(),
  completed_at timestamptz,
  status text DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS joao_sessions (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  messages jsonb NOT NULL,
  summary text,
  key_decisions text[],
  project_refs text[],
  summarized boolean DEFAULT false,
  created_at timestamptz DEFAULT now()
);

-- RLS policies for service_role access
ALTER TABLE joao_memory ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "service_all_joao_memory" ON joao_memory;
CREATE POLICY "service_all_joao_memory" ON joao_memory FOR ALL USING (true) WITH CHECK (true);

ALTER TABLE hub_dispatches ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "service_all_hub_dispatches" ON hub_dispatches;
CREATE POLICY "service_all_hub_dispatches" ON hub_dispatches FOR ALL USING (true) WITH CHECK (true);

ALTER TABLE joao_sessions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "service_all_joao_sessions" ON joao_sessions;
CREATE POLICY "service_all_joao_sessions" ON joao_sessions FOR ALL USING (true) WITH CHECK (true);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_joao_memory_created ON joao_memory (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_joao_memory_pinned ON joao_memory (pinned) WHERE pinned = true;
CREATE INDEX IF NOT EXISTS idx_joao_memory_source ON joao_memory (source);
CREATE INDEX IF NOT EXISTS idx_hub_dispatches_agent ON hub_dispatches (agent);
CREATE INDEX IF NOT EXISTS idx_hub_dispatches_status ON hub_dispatches (status);
CREATE INDEX IF NOT EXISTS idx_hub_dispatches_dispatched ON hub_dispatches (dispatched_at DESC);
CREATE INDEX IF NOT EXISTS idx_joao_sessions_created ON joao_sessions (created_at DESC);
