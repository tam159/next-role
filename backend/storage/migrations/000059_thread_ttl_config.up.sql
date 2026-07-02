ALTER TABLE thread ADD COLUMN IF NOT EXISTS configured_ttl_strategy TEXT;
ALTER TABLE thread ADD COLUMN IF NOT EXISTS configured_ttl_minutes NUMERIC;
