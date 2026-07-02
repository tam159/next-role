CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_run_thread_running 
ON run (thread_id) 
WHERE status = 'running';