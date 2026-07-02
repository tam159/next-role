CREATE INDEX CONCURRENTLY IF NOT EXISTS thread_owner_updated_idx
ON thread (
    (metadata->>'owner'),
    updated_at DESC,
    thread_id DESC
)
WHERE metadata ? 'owner';
