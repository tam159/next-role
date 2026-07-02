CREATE INDEX CONCURRENTLY if not exists idx_cron_next_run_enabled ON cron(next_run_date)
  WHERE enabled = TRUE;
