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

-- OGE Form 278-T (executive branch / cabinet) ingest, 2026-05-23.
-- `members` becomes a generic "filers" table — congressional members AND
-- executive-branch PAS officials live in it. New rows: filer_type = 'executive'.
-- Cabinet positions are stored in the existing `positions` table with
-- role_type = 'executive_cabinet', and the backtest engine treats them as
-- leadership (see backend/app/services/backtest.py LEADERSHIP_ROLE_TYPES).
ALTER TABLE members ADD COLUMN IF NOT EXISTS filer_type TEXT NOT NULL DEFAULT 'member';
ALTER TABLE members ADD COLUMN IF NOT EXISTS position_title TEXT;
CREATE INDEX IF NOT EXISTS ix_members_filer_type ON members(filer_type);
-- Existing rows pre-date this column; the DEFAULT handles new rows but
-- explicit backfill keeps the value uniform regardless of DB engine quirks.
UPDATE members SET filer_type = 'member' WHERE filer_type IS NULL OR filer_type = '';

-- chamber and state_dst are NULL-OK for executive filers (they don't sit in
-- either chamber and don't represent a district). Existing constraints already
-- allow NULL — these statements are documentation, no schema change required.

-- Price-fetch bookkeeping, so the engine stops re-requesting symbols the
-- vendor has already refused. Before this, ensure_prices() marked a ticker
-- as "needed" whenever its bars didn't reach the window end — which is
-- permanently true for every delisted name — so ~300 dead symbols were
-- re-fetched on every snapshot and backtest run and failed every time.
--   fetch_symbol       vendor spelling we actually request (BRK.B -> BRK-B);
--                      NULL once resolution decides the string isn't a ticker
--   price_status       ok | no_data | not_a_ticker | error
--   price_attempts     consecutive failures, drives the retry backoff
ALTER TABLE tickers ADD COLUMN IF NOT EXISTS fetch_symbol       TEXT;
ALTER TABLE tickers ADD COLUMN IF NOT EXISTS price_status       TEXT;
ALTER TABLE tickers ADD COLUMN IF NOT EXISTS price_last_attempt TIMESTAMP;
ALTER TABLE tickers ADD COLUMN IF NOT EXISTS price_attempts     INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS ix_tickers_price_status ON tickers(price_status);

-- What window we last *asked* the vendor for. Coverage alone can't tell
-- "we never requested this history" from "this is all the history there is":
-- a 2023 IPO can never have 2020 bars, so a start-of-window test re-requests
-- it on every run forever. Recording the request lets us ask once.
ALTER TABLE tickers ADD COLUMN IF NOT EXISTS price_window_start DATE;
ALTER TABLE tickers ADD COLUMN IF NOT EXISTS price_window_end   DATE;
