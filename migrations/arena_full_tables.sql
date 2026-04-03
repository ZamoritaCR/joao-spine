-- Arena Full Intelligence -- additional Supabase tables
-- Run after arena_preferences.sql

-- Conversations log (every chat exchange)
CREATE TABLE IF NOT EXISTS arena_conversations (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      text NOT NULL,
    timestamp       timestamptz NOT NULL DEFAULT now(),
    user_input      text NOT NULL,
    claude_response text,
    gpt_response    text,
    system_prompt   text,
    claude_model    text,
    gpt_model       text,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_arena_conv_session ON arena_conversations (session_id);
CREATE INDEX IF NOT EXISTS idx_arena_conv_created ON arena_conversations (created_at DESC);

ALTER TABLE arena_conversations ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='arena_conversations' AND policyname='service_role_all') THEN
        CREATE POLICY service_role_all ON arena_conversations FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;

-- Debates log
CREATE TABLE IF NOT EXISTS arena_debates (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid REFERENCES arena_conversations(id),
    session_id      text NOT NULL,
    timestamp       timestamptz NOT NULL DEFAULT now(),
    claude_critique text,
    gpt_critique    text,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_arena_debates_conv ON arena_debates (conversation_id);

ALTER TABLE arena_debates ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='arena_debates' AND policyname='service_role_all') THEN
        CREATE POLICY service_role_all ON arena_debates FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;

-- Tool calls audit trail
CREATE TABLE IF NOT EXISTS arena_tool_calls (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      text NOT NULL,
    conversation_id uuid REFERENCES arena_conversations(id),
    timestamp       timestamptz NOT NULL DEFAULT now(),
    model           text NOT NULL,
    tool_source     text NOT NULL,
    server_name     text,
    tool_name       text NOT NULL,
    input_summary   text,
    output_summary  text,
    success         boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_arena_tc_session ON arena_tool_calls (session_id);
CREATE INDEX IF NOT EXISTS idx_arena_tc_created ON arena_tool_calls (created_at DESC);

ALTER TABLE arena_tool_calls ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='arena_tool_calls' AND policyname='service_role_all') THEN
        CREATE POLICY service_role_all ON arena_tool_calls FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;

-- Command/file execution audit trail
CREATE TABLE IF NOT EXISTS arena_executions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      text NOT NULL,
    conversation_id uuid REFERENCES arena_conversations(id),
    timestamp       timestamptz NOT NULL DEFAULT now(),
    model           text NOT NULL,
    command         text NOT NULL,
    output          text,
    success         boolean NOT NULL DEFAULT true,
    git_branch      text,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_arena_exec_session ON arena_executions (session_id);

ALTER TABLE arena_executions ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='arena_executions' AND policyname='service_role_all') THEN
        CREATE POLICY service_role_all ON arena_executions FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;
