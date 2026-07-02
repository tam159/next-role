ALTER TABLE store
ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS ttl_minutes INT;
-- Add indexes for efficient TTL sweeping
CREATE INDEX IF NOT EXISTS idx_store_expires_at ON store (expires_at)
WHERE expires_at IS NOT NULL;