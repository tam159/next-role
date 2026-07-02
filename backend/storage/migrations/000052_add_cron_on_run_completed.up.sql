-- Add on_run_completed column to cron table
ALTER TABLE cron
ADD COLUMN on_run_completed VARCHAR(20) DEFAULT 'delete'
  CHECK (on_run_completed IN ('delete', 'keep'));

-- Add comment for documentation
COMMENT ON COLUMN cron.on_run_completed IS 'What to do with the thread after the run completes: delete removes the thread after execution, keep creates a new thread for each execution but does not clean them up. This parameter is only applicable when thread_id is NULL; when thread_id is present, on_run_completed is ignored.';
