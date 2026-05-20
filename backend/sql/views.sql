-- Analysis convenience views. Idempotent (CREATE OR REPLACE) — applied on
-- application startup after table creation. See app/main.py.

-- Canonical "analyzable trades" surface: stock-ticker transactions with member
-- and disclosure attributes denormalized. Analysis tools should read this.
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
    d.year AS filing_year
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
