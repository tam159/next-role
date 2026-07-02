CREATE TABLE IF NOT EXISTS assistant (
    assistant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    graph_id TEXT NOT NULL,
    created_at timestamptz default now(),
    updated_at TIMESTAMPTZ default now(),
    config JSONB NOT NULL DEFAULT '{}',
    metadata JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS thread (
    thread_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz default now(),
    updated_at TIMESTAMPTZ default now(),
    metadata JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id UUID NOT NULL REFERENCES thread(thread_id),
    thread_ts TIMESTAMPTZ,
    parent_ts TIMESTAMPTZ,
    checkpoint BYTEA NOT NULL,
    PRIMARY KEY (thread_id, thread_ts)
);
