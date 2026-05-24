-- Analysis convenience views. Idempotent (CREATE OR REPLACE) — applied on
-- application startup after table creation. See app/main.py.

-- Canonical "analyzable trades" surface: stock-ticker transactions with member
-- and disclosure attributes denormalized. Analysis tools should read this.
-- New columns (filer_type, position_title — 2026-05-23 OGE 278-T work)
-- MUST be appended at the END. Postgres CREATE OR REPLACE VIEW only
-- accepts the new schema if columns added are appended and existing
-- columns keep position+type. Inserting in the middle = "cannot change
-- name of view column".
DROP VIEW IF EXISTS v_stock_transactions_leadership;  -- depends on v_stock_transactions
CREATE OR REPLACE VIEW v_stock_transactions AS
SELECT
    t.id,
    t.transaction_date,
    t.notification_date,
    t.filing_date,
    t.ticker,
    t.asset_name,
    t.asset_type,
    t.transaction_type,
    t.owner,
    t.amount_min,
    t.amount_max,
    t.confidence,
    m.id   AS member_id,
    m.last_name,
    m.first_name,
    m.chamber,
    m.party,
    m.state_dst,
    d.doc_id,
    d.parse_method,
    d.year AS filing_year,
    m.filer_type,
    m.position_title
FROM transactions t
JOIN members m ON m.id = t.member_id
LEFT JOIN disclosures d ON d.id = t.disclosure_id
WHERE t.ticker IS NOT NULL AND t.ticker <> '';

-- Per-member rollup.
CREATE OR REPLACE VIEW v_member_activity AS
SELECT
    m.id AS member_id,
    m.last_name,
    m.first_name,
    m.chamber,
    m.party,
    m.state_dst,
    count(t.id) AS total_transactions,
    count(t.id) FILTER (WHERE t.ticker IS NOT NULL AND t.ticker <> '') AS stock_transactions,
    count(*)    FILTER (WHERE t.transaction_type = 'purchase')         AS buys,
    count(*)    FILTER (WHERE t.transaction_type LIKE 'sale%')          AS sells,
    count(DISTINCT t.ticker) AS distinct_tickers,
    min(t.transaction_date)  AS first_trade,
    max(t.transaction_date)  AS last_trade
FROM members m
LEFT JOIN transactions t ON t.member_id = m.id
GROUP BY m.id;

-- Per-ticker rollup.
CREATE OR REPLACE VIEW v_ticker_activity AS
SELECT
    t.ticker,
    count(*) AS trade_count,
    count(*) FILTER (WHERE t.transaction_type = 'purchase') AS buys,
    count(*) FILTER (WHERE t.transaction_type LIKE 'sale%')  AS sells,
    count(DISTINCT t.member_id) AS distinct_members,
    min(t.transaction_date) AS first_trade,
    max(t.transaction_date) AS last_trade
FROM transactions t
WHERE t.ticker IS NOT NULL AND t.ticker <> ''
GROUP BY t.ticker;

-- v2 leadership analysis surface: every analyzable trade tagged with whether
-- the member held a leadership / committee-chair / ranking-member position on
-- the transaction date, and whether their party controlled the chamber.
--
-- This is what the v2 backtest should consume when restricting to the
-- "captain gains" subset per Wei & Zhou (NBER, 2025). One row per
-- transaction; the EXISTS / scalar-subquery pattern avoids row multiplication
-- when a member holds multiple positions at the same time (e.g. Speaker +
-- committee chair).
CREATE OR REPLACE VIEW v_stock_transactions_leadership AS
SELECT
    vst.*,
    EXISTS (
        SELECT 1 FROM positions p
        WHERE p.member_id = vst.member_id
          AND p.role_type IN ('leadership', 'chair')
          AND vst.transaction_date BETWEEN p.start_date AND COALESCE(p.end_date, CURRENT_DATE)
    ) AS in_leadership_window,
    (
        SELECT string_agg(p.title, ' | ' ORDER BY p.start_date)
        FROM positions p
        WHERE p.member_id = vst.member_id
          AND vst.transaction_date BETWEEN p.start_date AND COALESCE(p.end_date, CURRENT_DATE)
    ) AS positions_held,
    (
        SELECT pc.majority_party
        FROM party_control pc
        WHERE pc.chamber = vst.chamber
          AND vst.transaction_date BETWEEN pc.start_date AND COALESCE(pc.end_date, CURRENT_DATE)
        LIMIT 1
    ) AS chamber_majority_party,
    (
        SELECT vst.party = pc.majority_party
        FROM party_control pc
        WHERE pc.chamber = vst.chamber
          AND vst.transaction_date BETWEEN pc.start_date AND COALESCE(pc.end_date, CURRENT_DATE)
        LIMIT 1
    ) AS party_in_majority
FROM v_stock_transactions vst;
