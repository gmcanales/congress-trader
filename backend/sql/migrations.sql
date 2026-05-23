-- Idempotent schema migrations, applied on application startup *after*
-- Base.metadata.create_all() (which only creates missing tables) and *before*
-- views.sql. Use this file to add columns / indexes to existing tables without
-- pulling in alembic. Every statement must be safely re-runnable.

-- v2 leadership work: bioguide_id is the canonical join key with
-- unitedstates/congress-legislators (and ProPublica/GovTrack).
ALTER TABLE members ADD COLUMN IF NOT EXISTS bioguide_id VARCHAR;
CREATE INDEX IF NOT EXISTS ix_members_bioguide_id ON members(bioguide_id);

-- Tables `committees`, `positions`, and `party_control` (v2 leadership work)
-- are declared on the SQLAlchemy models and created via Base.metadata.create_all
-- at backend startup. No ALTER statements are needed here unless we later add
-- columns to those tables on existing deployments.

-- Generic key/value store for small UI/app settings (e.g. active landing
-- splash image). Keep this tiny — anything substantial should get its own
-- table.
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT now()
);

-- Per-user portfolio scoping. Backfill existing rows to the sole user
-- before this change (gmcanales / owner@example.com), then lock the
-- column. Identity source is the verified CF Access JWT email — see
-- backend/app/auth.py.
ALTER TABLE user_holdings ADD COLUMN IF NOT EXISTS user_email TEXT;
UPDATE user_holdings SET user_email = 'owner@example.com' WHERE user_email IS NULL;
ALTER TABLE user_holdings ALTER COLUMN user_email SET NOT NULL;
CREATE INDEX IF NOT EXISTS ix_user_holdings_user_email ON user_holdings(user_email);
