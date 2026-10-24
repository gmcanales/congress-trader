"""Buy/sell composition of disclosed trades, cut by the analytical dimensions
the project has built up: sector, attention, turnover, size, and ownership.

This is descriptive, not a backtest — "what did Congress buy and sell, and in
what kind of company", not "did it pay". It reads through the canonical-row
filter so an amended filing or a repeated line item doesn't count twice.

Point-in-time, and where it isn't:
  * attention (`visibility`) resolves against the month BEFORE the filing, so
    a name is bucketed by what it was when the trade became public.
  * size and turnover use `tickers.market_cap`, which is a CURRENT snapshot —
    there is no market-cap history in this database. A 2021 trade in a company
    that has since 10x'd is therefore bucketed by today's size. That is
    acceptable for a descriptive breakdown and would NOT be acceptable in a
    backtest; `market_cap_is_current` is returned so the UI can say so.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import text

from app.database import engine

# Bucket edges. Cap thresholds are the conventional US equity bands; turnover
# quartile cuts are computed from the data rather than assumed.
CAP_BANDS = [
    ("Mega  >$200B", 200e9, None),
    ("Large $10-200B", 10e9, 200e9),
    ("Mid   $2-10B", 2e9, 10e9),
    ("Small <$2B", None, 2e9),
]

DIMENSIONS = {
    "sector":     "Sector",
    "visibility": "Attention",
    "churn":      "Turnover",
    "cap":        "Market cap",
    "owner":      "Owner",
}

# One row per canonical trade in the window, with every dimension attached.
# Kept as one query so the five breakdowns always describe the same trade set.
_BASE_SQL = """
WITH canon AS (
    SELECT id FROM (
        SELECT t.id,
               row_number() OVER (
                   PARTITION BY t.member_id, t.ticker, t.asset_name,
                                t.transaction_date, t.transaction_type,
                                t.amount_min, t.owner
                   ORDER BY t.filing_date ASC NULLS LAST, t.id ASC) rn
        FROM transactions t) r
    WHERE rn = 1
), trades AS (
    SELECT t.id, t.ticker, t.filing_date, t.transaction_type, t.owner,
           COALESCE((t.amount_min + COALESCE(t.amount_max, t.amount_min)) / 2.0, 0) AS amt,
           ti.sector, ti.market_cap,
           (SELECT v.pct_rank FROM ticker_visibility v
             WHERE v.ticker = t.ticker
               AND v.as_of < date_trunc('month', t.filing_date)
             ORDER BY v.as_of DESC LIMIT 1) AS vis_pct,
           (SELECT v.adv FROM ticker_visibility v
             WHERE v.ticker = t.ticker
               AND v.as_of < date_trunc('month', t.filing_date)
             ORDER BY v.as_of DESC LIMIT 1) AS adv
    FROM transactions t
    JOIN canon c  ON c.id = t.id
    JOIN members m ON m.id = t.member_id
    LEFT JOIN tickers ti ON ti.symbol = t.ticker
    WHERE t.ticker IS NOT NULL AND t.ticker <> ''
      AND t.filing_date IS NOT NULL
      AND t.filing_date >= :start AND t.filing_date <= :end
      AND m.filer_type = ANY(:filer_types)
)
SELECT * FROM trades
"""


def _side(tx_type: str) -> str | None:
    t = (tx_type or "").lower()
    if "purchas" in t:
        return "buy"
    if "sale" in t or "sell" in t:
        return "sell"
    return None          # exchanges and unknowns are counted in neither


def _cap_bucket(mc):
    if mc is None or mc <= 0:
        return "Unknown"
    for label, lo, hi in CAP_BANDS:
        if (lo is None or mc >= lo) and (hi is None or mc < hi):
            return label
    return "Unknown"


def _vis_bucket(pct):
    if pct is None:
        return "Unknown"
    if pct >= 0.95:
        return "Household (top 5%)"
    if pct >= 0.75:
        return "Well-known (top 25%)"
    if pct >= 0.50:
        return "Mid"
    return "Obscure (bottom 50%)"


def _owner_bucket(owner):
    return {"SP": "Spouse", "JT": "Joint", "DC": "Dependent"}.get(
        (owner or "").strip().upper(), "Self")


# Above this window a bucket holds too many names for a per-ticker segment to
# be more than a hairline, so the breakdown stays aggregate.
SEGMENT_MAX_WINDOW_DAYS = 30


def compute_breakdown(window_days: int = 90,
                      end: date | None = None,
                      filer_types: list[str] | None = None) -> dict:
    end = end or date.today()
    start = end - timedelta(days=window_days)
    filer_types = filer_types or ["member"]
    segment = window_days <= SEGMENT_MAX_WINDOW_DAYS

    with engine.begin() as conn:
        rows = conn.execute(text(_BASE_SQL),
                            {"start": start, "end": end,
                             "filer_types": filer_types}).mappings().all()

    # Turnover quartiles are cut on the trades actually in the window, so the
    # bands describe this sample rather than an assumed distribution.
    turnovers = sorted(
        r["adv"] / r["market_cap"]
        for r in rows
        if r["adv"] and r["market_cap"] and r["market_cap"] > 0)

    def q(p):
        if not turnovers:
            return None
        return turnovers[min(int(len(turnovers) * p), len(turnovers) - 1)]

    q25, q50, q75 = q(0.25), q(0.50), q(0.75)

    def churn_bucket(r):
        if not r["adv"] or not r["market_cap"] or r["market_cap"] <= 0 or q25 is None:
            return "Unknown"
        t = r["adv"] / r["market_cap"]
        if t >= q75:
            return "Heavily traded (top 25%)"
        if t >= q50:
            return "Above median"
        if t >= q25:
            return "Below median"
        return "Thinly traded (bottom 25%)"

    bucketers = {
        "sector":     lambda r: r["sector"] or "Unknown",
        "visibility": lambda r: _vis_bucket(r["vis_pct"]),
        "churn":      churn_bucket,
        "cap":        lambda r: _cap_bucket(r["market_cap"]),
        "owner":      lambda r: _owner_bucket(r["owner"]),
    }

    dims: dict[str, dict] = {}
    n_buy = n_sell = 0
    for key, label in DIMENSIONS.items():
        acc: dict[str, dict] = {}
        for r in rows:
            side = _side(r["transaction_type"])
            if side is None:
                continue
            b = acc.setdefault(bucketers[key](r),
                               {"bucket": "", "buys": 0, "sells": 0,
                                "buy_amount": 0.0, "sell_amount": 0.0,
                                "_buy_tickers": {}, "_sell_tickers": {}})
            amt = float(r["amt"] or 0)
            b["buys" if side == "buy" else "sells"] += 1
            b["buy_amount" if side == "buy" else "sell_amount"] += amt
            if segment:
                tick = b[f"_{side}_tickers"].setdefault(
                    r["ticker"], {"n": 0, "amount": 0.0})
                tick["n"] += 1
                tick["amount"] += amt
        for name, b in acc.items():
            b["bucket"] = name
            b["net"] = b["buys"] - b["sells"]
            b["total"] = b["buys"] + b["sells"]
            if segment:
                # Per-ticker segments, biggest first. Width is driven by the
                # trade count and thickness by the dollar midpoint, so both
                # are carried per side.
                for side in ("buy", "sell"):
                    raw = b.pop(f"_{side}_tickers", {})
                    b[f"{side}_tickers"] = sorted(
                        ({"ticker": t, "n": v["n"], "amount": round(v["amount"], 2)}
                         for t, v in raw.items()),
                        key=lambda d: (-d["n"], -d["amount"], d["ticker"]))
            else:
                b.pop("_buy_tickers", None)
                b.pop("_sell_tickers", None)
        # Largest total first; "Unknown" always sinks to the bottom so a data
        # gap never leads the chart.
        buckets = sorted(acc.values(),
                         key=lambda b: (b["bucket"] == "Unknown", -b["total"]))
        dims[key] = {"key": key, "label": label, "buckets": buckets}

    for r in rows:
        s = _side(r["transaction_type"])
        if s == "buy":
            n_buy += 1
        elif s == "sell":
            n_sell += 1

    return {
        "as_of": end.isoformat(),
        "start": start.isoformat(),
        "window_days": window_days,
        "filer_types": filer_types,
        "n_trades": len(rows),
        "n_buys": n_buy,
        "n_sells": n_sell,
        "market_cap_is_current": True,
        "segmented": segment,
        "segment_max_window_days": SEGMENT_MAX_WINDOW_DAYS,
        "dimensions": dims,
    }
