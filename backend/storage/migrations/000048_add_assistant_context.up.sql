ALTER TABLE assistant
    ADD COLUMN IF NOT EXISTS context jsonb;

ALTER TABLE assistant_versions
  ADD COLUMN IF NOT EXISTS context jsonb;
