CREATE INDEX CONCURRENTLY IF NOT EXISTS run_pending_by_thread_time_cover
ON run(thread_id, created_at, run_id)
WHERE status = 'pending';

CREATE INDEX CONCURRENTLY IF NOT EXISTS run_thread_id_idx
ON run(thread_id);

-- These are all redundant and can be covered by the indexes introduced above
DROP INDEX IF EXISTS run_pending_idx;
DROP INDEX IF EXISTS run_thread_id_status_idx;
DROP INDEX IF EXISTS run_pending_by_thread_time;
