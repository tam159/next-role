-- Better Auth 1.6 -> 1.7 account-identity backfill (Postgres).
--
-- 1.7 keys every account on the pair (issuer, "accountId") and requires a NOT
-- NULL `issuer` column with a unique compound index. `auth migrate` cannot
-- produce that on a populated table (adding a required column with no default
-- fails, and the CLI never makes an existing column NOT NULL), so this runs
-- first, then `auth migrate` reconciles the rest. Follows the upgrade guide:
-- https://better-auth.com/docs/guides/1-7-upgrade-guide (Account identity).
--
-- Usage (see README "Authentication & multi-user" -> Upgrading Better Auth):
--   psql "$AUTH_DATABASE_URL" -v ON_ERROR_STOP=1 -f scripts/better-auth-1.7-account-issuer-backfill.sql
--
-- Safe to re-run: every step is idempotent, the whole thing is one transaction,
-- and it aborts (leaving the schema untouched) if any row cannot be mapped to
-- an issuer or if two rows would collide on (issuer, "accountId").
-- Stop Better Auth 1.6 writers (the frontend) before running; deploy 1.7 after.

BEGIN;

-- 1. Add the column as nullable so existing rows can be backfilled.
ALTER TABLE "account" ADD COLUMN IF NOT EXISTS "issuer" text;

-- 2. Map every provider this app can have configured to its issuer namespace.
--    credential  -> local:credential          (email + password; accountId is the user id)
--    google      -> https://accounts.google.com (built-in Google OAuth provider)
--    Any other providerId is left NULL on purpose and trips the guard below:
--    add its issuer here (trusted OIDC issuer, or local:oauth:<providerId> for
--    plain OAuth) rather than guessing.
UPDATE "account" SET "issuer" = 'local:credential'
  WHERE "issuer" IS NULL AND "providerId" = 'credential';
UPDATE "account" SET "issuer" = 'https://accounts.google.com'
  WHERE "issuer" IS NULL AND "providerId" = 'google';

-- 3. Guards: no unmapped rows, no identity collisions.
DO $$
DECLARE
  unmapped integer;
  collisions integer;
  providers text;
BEGIN
  SELECT count(*), string_agg(DISTINCT "providerId", ', ')
    INTO unmapped, providers
    FROM "account" WHERE "issuer" IS NULL;
  IF unmapped > 0 THEN
    RAISE EXCEPTION 'better-auth 1.7 backfill: % account row(s) have no issuer mapping (providerId: %). Add the mapping to this script and re-run.',
      unmapped, providers;
  END IF;

  SELECT count(*) INTO collisions FROM (
    SELECT "issuer", "accountId"
      FROM "account"
     GROUP BY "issuer", "accountId"
    HAVING count(*) > 1
  ) c;
  IF collisions > 0 THEN
    RAISE EXCEPTION 'better-auth 1.7 backfill: % (issuer, accountId) collision(s). Resolve them per the upgrade guide before re-running.',
      collisions;
  END IF;
END $$;

-- 4. Enforce the 1.7 constraints.
ALTER TABLE "account" ALTER COLUMN "issuer" SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS "account_issuer_accountId_uidx"
  ON "account" ("issuer", "accountId");

COMMIT;
