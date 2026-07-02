CREATE TABLE IF NOT EXISTS thread_ttl (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id UUID NOT NULL REFERENCES thread(thread_id) ON DELETE CASCADE,
    strategy TEXT NOT NULL DEFAULT 'delete',
    ttl_minutes NUMERIC NOT NULL CHECK (ttl_minutes >= 0),
    -- Always assume UTC to treat as immutable (not just "stable")
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (
      CURRENT_TIMESTAMP AT TIME ZONE 'UTC'
    ),

    expires_at TIMESTAMP WITHOUT TIME ZONE GENERATED ALWAYS AS (
        created_at + (ttl_minutes * interval '1 minute')
    ) STORED
);

CREATE INDEX idx_thread_ttl_expires_at ON thread_ttl (expires_at);
CREATE INDEX idx_thread_ttl_thread_id ON thread_ttl (thread_id);
CREATE UNIQUE INDEX idx_thread_ttl_thread_strategy ON thread_ttl (thread_id, strategy);