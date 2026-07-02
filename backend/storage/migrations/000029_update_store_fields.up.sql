DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'ltree') THEN
        CREATE EXTENSION ltree;
    END IF;
END
$$;


ALTER TABLE store
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;

-- For faster listing of namespaces & lookups by namespace with prefix/suffix matching
CREATE INDEX concurrently IF NOT EXISTS store_prefix_idx ON store USING btree (prefix text_pattern_ops);