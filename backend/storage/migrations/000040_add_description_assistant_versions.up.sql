ALTER TABLE assistant_versions
  ADD COLUMN IF NOT EXISTS description TEXT;

UPDATE assistant_versions av
SET    description = a.description
FROM   assistant a
WHERE  av.assistant_id = a.assistant_id
       AND av.description IS NULL;