-- Better Auth 1.7.0-1.7.2 -> 1.7.3+ account-identity cleanup (Postgres).
--
-- 1.7.0-1.7.2 keyed accounts on (issuer, "accountId") behind a NOT NULL
-- `issuer` column and a unique compound index. 1.7.3 went back to the 1.6 key,
-- ("providerId", "accountId"), and stopped writing `issuer`, so on a database
-- that ran 1.7.0-1.7.2 the NOT NULL column rejects every sign-up and account
-- link until it is relaxed. Follows the upgrade guide:
-- https://better-auth.com/docs/guides/1-7-upgrade-guide (Account identity keeps the provider key).
--
-- Usage (see README "Authentication & multi-user" -> Upgrading Better Auth):
--   psql "$AUTH_DATABASE_URL" -v ON_ERROR_STOP=1 -f scripts/better-auth-1.7.3-relax-account-issuer.sql
--
-- Safe to re-run and a no-op on a database that never had the column (1.6, or
-- created on 1.7.3+). One transaction; it aborts (schema untouched) if two rows
-- share a ("providerId", "accountId") pair, which 1.7.3+ rejects at lookup.
-- The column itself is kept (nullable, no longer written) so the step stays
-- reversible; drop it whenever:  ALTER TABLE "account" DROP COLUMN "issuer";

BEGIN;

-- 1. Guard: the 1.6 key must be unique again before 1.7.3+ starts using it.
DO $$
DECLARE
  duplicates integer;
BEGIN
  SELECT count(*) INTO duplicates FROM (
    SELECT "providerId", "accountId"
      FROM "account"
     GROUP BY "providerId", "accountId"
    HAVING count(*) > 1
  ) d;
  IF duplicates > 0 THEN
    RAISE EXCEPTION 'better-auth 1.7.3 cleanup: % duplicate (providerId, accountId) pair(s). Resolve them per the upgrade guide (Check for duplicate account keys) before re-running.',
      duplicates;
  END IF;
END $$;

-- 2. Drop the index before touching the column (the guide's ordering).
DROP INDEX IF EXISTS "account_issuer_accountId_uidx";

-- 3. Relax the column when it exists.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
     WHERE table_schema = current_schema() AND table_name = 'account' AND column_name = 'issuer'
  ) THEN
    ALTER TABLE "account" ALTER COLUMN "issuer" DROP NOT NULL;
  END IF;
END $$;

COMMIT;
