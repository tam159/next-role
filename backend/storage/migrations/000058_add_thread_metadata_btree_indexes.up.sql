CREATE INDEX CONCURRENTLY IF NOT EXISTS thread_ls_user_id_idx
ON thread (
    (metadata->>'ls_user_id'),
    COALESCE(state_updated_at, updated_at) DESC,
    thread_id DESC
)
WHERE metadata ? 'ls_user_id';

CREATE INDEX CONCURRENTLY IF NOT EXISTS thread_assistant_id_idx
ON thread (
    (metadata->>'assistant_id'),
    COALESCE(state_updated_at, updated_at) DESC,
    thread_id DESC
)
WHERE metadata ? 'assistant_id';
