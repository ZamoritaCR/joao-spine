-- joao-spine Supabase tables

-- Idea vault: captured ideas, notes, processed content
CREATE TABLE IF NOT EXISTS idea_vault (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    tags TEXT[] DEFAULT '{}',
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_idea_vault_created_at ON idea_vault (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_idea_vault_source ON idea_vault (source);
CREATE INDEX IF NOT EXISTS idx_idea_vault_tags ON idea_vault USING GIN (tags);

-- Session log: audit trail for every endpoint call
CREATE TABLE IF NOT EXISTS session_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    endpoint TEXT NOT NULL,
    action TEXT NOT NULL,
    input_summary TEXT NOT NULL DEFAULT '',
    output_summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ok',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_session_log_created_at ON session_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_session_log_endpoint ON session_log (endpoint);

-- Agent outputs: captured tmux/SSH command outputs
CREATE TABLE IF NOT EXISTS agent_outputs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_name TEXT NOT NULL,
    command TEXT NOT NULL,
    output TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ok',
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_agent_outputs_created_at ON agent_outputs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_outputs_session ON agent_outputs (session_name);
