CREATE TABLE IF NOT EXISTS checkpoint_delete_queue (
    id            BIGSERIAL   PRIMARY KEY,
    thread_id     UUID        NOT NULL,
    checkpoint_ns TEXT        NOT NULL DEFAULT '',
    checkpoint_id TEXT        NOT NULL,
    enqueued_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Dedup: one entry per (thread, ns, checkpoint).
CREATE UNIQUE INDEX IF NOT EXISTS idx_cdq_dedup
    ON checkpoint_delete_queue (thread_id, checkpoint_ns, checkpoint_id);
