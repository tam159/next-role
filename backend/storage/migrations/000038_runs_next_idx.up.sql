-- Hard guarantee that at most one run per thread can be “running”.
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS run_running_one_per_thread
    ON run(thread_id)
    WHERE status = 'running';

-- Covers the ROW_NUMBER() window (partition key = thread_id, sort key = created_at).
CREATE INDEX CONCURRENTLY IF NOT EXISTS run_pending_by_thread_time
    ON run(thread_id, created_at)
    WHERE status = 'pending';
