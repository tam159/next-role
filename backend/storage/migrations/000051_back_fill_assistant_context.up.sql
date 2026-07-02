-- Backfill context from config.configurable for assistants where context is null
UPDATE assistant 
SET context = config->'configurable'
WHERE context IS NULL 
  AND config ? 'configurable' 
  AND config->'configurable' IS NOT NULL;

-- Same for assistant_versions table
UPDATE assistant_versions
SET context = config->'configurable'
WHERE context IS NULL 
  AND config ? 'configurable' 
  AND config->'configurable' IS NOT NULL;
