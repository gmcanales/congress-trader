-- Idempotent schema migrations, applied on application startup *after*
-- Base.metadata.create_all() (which only creates missing tables) and *before*
-- views.sql. Use this file to add columns / indexes to existing tables without
-- pulling in alembic. Every statement must be safely re-runnable.

-- v2 leadership work: bioguide_id is the canonical join key with
-- unitedstates/congress-legislators (and ProPublica/GovTrack).
ALTER TABLE members ADD COLUMN IF NOT EXISTS bioguide_id VARCHAR;
CREATE INDEX IF NOT EXISTS ix_members_bioguide_id ON members(bioguide_id);
