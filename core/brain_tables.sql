
-- Run this in Supabase Dashboard > SQL Editor
-- Project: wkfewpynskakgbetscsa

CREATE TABLE IF NOT EXISTS brain_memory (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    key text UNIQUE NOT NULL,
    value text NOT NULL,
    updated_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS brain_context (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model text NOT NULL,
    role text NOT NULL,
    content text NOT NULL,
    token_estimate int DEFAULT 0,
    created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_brain_context_model ON brain_context(model, created_at DESC);

-- Enable RLS but allow service role full access
ALTER TABLE brain_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE brain_context ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access" ON brain_memory FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON brain_context FOR ALL USING (true) WITH CHECK (true);
