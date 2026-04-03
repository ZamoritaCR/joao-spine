-- AI Arena preference logging table
CREATE TABLE IF NOT EXISTS arena_preferences (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp   timestamptz NOT NULL DEFAULT now(),
    user_input  text NOT NULL,
    claude_response text NOT NULL,
    gpt_response    text NOT NULL,
    preferred_model text NOT NULL,
    debate_claude   text,
    debate_gpt      text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Index for querying preferences over time
CREATE INDEX IF NOT EXISTS idx_arena_preferences_created
    ON arena_preferences (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_arena_preferences_model
    ON arena_preferences (preferred_model);

-- RLS: service role full access
ALTER TABLE arena_preferences ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'arena_preferences' AND policyname = 'service_role_all'
    ) THEN
        CREATE POLICY service_role_all ON arena_preferences
            FOR ALL
            TO service_role
            USING (true)
            WITH CHECK (true);
    END IF;
END $$;
