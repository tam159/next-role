ALTER TABLE assistant_versions 
ADD COLUMN IF NOT EXISTS name TEXT;

UPDATE assistant_versions
SET name = assistant.name
FROM assistant
WHERE assistant_versions.assistant_id = assistant.assistant_id
AND (assistant_versions.name IS NULL);