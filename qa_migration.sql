-- QA Reviews table for the multi-tier consensus pipeline
CREATE TABLE qa_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dispatch_id UUID REFERENCES dispatch_log(id),
    agent TEXT NOT NULL,
    task_summary TEXT,
    code_diff TEXT,
    -- Sonnet review
    sonnet_score INTEGER,
    sonnet_verdict TEXT,
    sonnet_feedback TEXT,
    -- GPT review
    gpt_score INTEGER,
    gpt_verdict TEXT,
    gpt_feedback TEXT,
    -- Opus review
    opus_score INTEGER,
    opus_verdict TEXT,
    opus_feedback TEXT,
    -- Consensus
    consensus_verdict TEXT,  -- 'deploy', 'review', 'reject'
    avg_score FLOAT,
    deployed BOOLEAN DEFAULT FALSE,
    override_by TEXT,  -- NULL or 'johan'
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast lookup by dispatch_id
CREATE INDEX idx_qa_reviews_dispatch_id ON qa_reviews(dispatch_id);

-- Index for finding undeployed reviews needing attention
CREATE INDEX idx_qa_reviews_pending ON qa_reviews(consensus_verdict) WHERE deployed = FALSE;
