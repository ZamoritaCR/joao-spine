-- Build Log table for AI Workforce Activity tracking
-- Run in Supabase SQL Editor: https://supabase.com/dashboard/project/wkfewpynskakgbetscsa/sql

CREATE TABLE IF NOT EXISTS build_log (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now(),
  agent TEXT,
  task_summary TEXT,
  files_touched TEXT[],
  git_commit TEXT,
  git_message TEXT,
  qa_result TEXT DEFAULT 'PENDING',
  qa_notes TEXT,
  model_used TEXT,
  tokens_used INTEGER DEFAULT 0,
  dispatch_id TEXT
);

-- RLS policy for service_role access
ALTER TABLE build_log ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "service_all_build_log" ON build_log;
CREATE POLICY "service_all_build_log" ON build_log FOR ALL USING (true) WITH CHECK (true);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_build_log_created ON build_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_build_log_agent ON build_log (agent);
CREATE INDEX IF NOT EXISTS idx_build_log_qa ON build_log (qa_result);
