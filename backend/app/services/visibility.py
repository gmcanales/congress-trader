"""Ticker attention ("visibility") tiers.

Separates names that are constantly in the news from names that aren't, so
the engine can ask whether congressional trades in obscure companies carry
more information than trades in household names. Theory-wise this is the
same Wei & Zhou informational channel the rest of the project tests: a
position in Medpace is more plausibly a deliberate, researched pick than a
position in Alphabet, which an advisor could have put on by default.

**Why dollar volume.** Market cap doesn't capture it — Eaton and Parker
Hannifin are ~$100B industrials that essentially never appear in mainstream
coverage. Turnover (volume / market cap) actively inverts it, because
mega-caps churn less relative to their size: on live data it ranked GOOGL
below Medpace. Median daily dollar volume is how much money actually moves
through a name, and it lines up with intuition — a p95 cut puts
MU/NVDA/GOOGL/TSLA/PLTR above the line and ETN/PH/FAST/MEDP below it.

**Point-in-time.** Ranking on today's volume would be look-ahead: NVDA's
current turnover reflects the AI boom, not what it looked like in 2021.
Stats are bucketed by month and ranked cross-sectionally within that month,
and lookups resolve to a month strictly *before* the filing date, so a trade
is classified by what the name was at the time it became public.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import text

from app.database import engine

logger = logging.getLogger(__name__)

# Above this cross-sectional percentile a name counts as high-visibility.
# ~p95 is where the examples separate; it is deliberately a narrow slice,
# because "names the public has heard of" is a narrow slice of the market.
DEFAULT_HIGH_PCT = 0.95

VISIBILITY_CHOICES = ("any", "high", "low")

# Monthly medians, smoothed over a trailing quarter so one frantic month
# doesn't reclassify a name, then ranked within each month. The window only
# ever looks backwards, which is what keeps this point-in-time.
_BUILD_SQL = """
WITH monthly AS (
    SELECT ticker,
           date_trunc('month', date)::date AS m,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY close * volume) AS med
    FROM price_bars
    WHERE volume > 0 AND close > 0
    GROUP BY 1, 2
), smoothed AS (
    SELECT ticker, m,
           AVG(med) OVER (PARTITION BY ticker ORDER BY m
                          ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS adv
    FROM monthly
)
INSERT INTO ticker_visibility (ticker, as_of, adv, pct_rank)
SELECT ticker, m, adv,
       PERCENT_RANK() OVER (PARTITION BY m ORDER BY adv)
FROM smoothed
WHERE adv IS NOT NULL
ON CONFLICT (ticker, as_of) DO UPDATE
   SET adv = EXCLUDED.adv, pct_rank = EXCLUDED.pct_rank
"""


def rebuild() -> int:
    """Recompute every monthly visibility row. Cheap enough to just redo
    (one pass over price_bars), and it has to be redone whenever price
    coverage changes, since the cross-sectional ranking shifts with the
    universe."""
    with engine.begin() as conn:
        conn.execute(text(_BUILD_SQL))
        n = conn.execute(text("SELECT count(*) FROM ticker_visibility")).scalar()
    logger.info(f"ticker_visibility rebuilt: {n} rows")
    return n


def tier_sql(alias: str = "t", high_pct: float = DEFAULT_HIGH_PCT) -> str:
    """SQL fragment yielding 'high'/'low' for a transactions row aliased
    `alias`, resolved against the latest month strictly before its filing
    date."""
    return f"""
        CASE WHEN (
            SELECT v.pct_rank FROM ticker_visibility v
            WHERE v.ticker = {alias}.ticker
              AND v.as_of < date_trunc('month', {alias}.filing_date)
            ORDER BY v.as_of DESC LIMIT 1
        ) >= {high_pct} THEN 'high' ELSE 'low' END
    """


def lookup(ticker: str, as_of: date) -> dict | None:
    """Visibility of one ticker as it stood before `as_of`. Returns None when
    the name has no price history yet — unclassifiable, not low."""
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT as_of, adv, pct_rank FROM ticker_visibility
            WHERE ticker = :t AND as_of < date_trunc('month', CAST(:d AS date))
            ORDER BY as_of DESC LIMIT 1
        """), {"t": ticker, "d": as_of}).first()
    if not row:
        return None
    return {"as_of": row[0].isoformat(), "adv": float(row[1]),
            "pct_rank": float(row[2]),
            "tier": "high" if row[2] >= DEFAULT_HIGH_PCT else "low"}
