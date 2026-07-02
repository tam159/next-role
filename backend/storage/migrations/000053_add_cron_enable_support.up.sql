ALTER TABLE cron
ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN cron.enabled IS 'Enable or disable cron execution';
