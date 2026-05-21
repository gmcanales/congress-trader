"""
Build the per-trade dataset that feeds the Bayesian hierarchical model.

For every purchase transaction in [--start, --end] with a usable forward
return at the chosen horizon, this emits a Parquet file with:

  trade_id, member_id, bioguide_id, last_name, first_name,
  chamber, party,
  filing_date, transaction_date,
  ticker, sector, sector_etf,
  leadership_at_trade   (bool — held leadership/chair at transaction_date)
  in_majority           (bool — member.party == chamber majority on tx date)
  is_post_trifecta      (bool — transaction_date >= 2025-01-03)
  excess_63d_spy        (stock 63d forward return - SPY 63d forward return)
  excess_63d_sector     (stock 63d forward return - sector-ETF 63d forward return; NaN if no sector)

Trades whose stock has no price data at horizon are dropped (with a count).

Run:

  docker compose run --rm --no-deps backend python build_trade_dataset.py \\
      --start 2021-01-01 --end 2025-12-31 --horizon 63 \\
      --output /tmp/trade_dataset.parquet
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

from app.database import SessionLocal, engine
from app.services.backtest import (
    GICS_TO_ETF, ALL_SECTOR_ETFS, ensure_prices, _close_series,
)


TRIFECTA_START = date(2025, 1, 3)


def _forward_return_n(series: pd.Series, entry: date, n_days: int) -> float | None:
    """Forward return over `n_days` trading days starting from the first bar
    on/after `entry`. Returns None if either bar is out of range."""
    pos = series.index.searchsorted(pd.Timestamp(entry))
    if pos >= len(series):
        return None
    exit_pos = pos + n_days
    if exit_pos >= len(series):
        return None
    p0 = series.iloc[pos]
    p1 = series.iloc[exit_pos]
    if not (p0 > 0):
        return None
    return float(p1 / p0 - 1)


def build(start: date, end: date, horizon: int, output: Path) -> dict:
    db = SessionLocal()
    try:
        # Pull a single fat result via the leadership view — gives us all the
        # tags we need (in_leadership_window aka leadership_at_trade,
        # party_in_majority, chamber_majority_party). Also join party / name.
        sql = text("""
            SELECT
              vst.id                AS trade_id,
              vst.member_id,
              m.bioguide_id,
              m.last_name, m.first_name,
              vst.chamber, vst.party,
              vst.filing_date, vst.transaction_date,
              vst.ticker,
              ti.sector,
              vst.in_leadership_window AS leadership_at_trade,
              vst.party_in_majority,
              vst.chamber_majority_party
            FROM v_stock_transactions_leadership vst
            JOIN members m ON m.id = vst.member_id
            LEFT JOIN tickers ti ON ti.symbol = vst.ticker
            WHERE vst.transaction_type = 'purchase'
              AND vst.filing_date IS NOT NULL
              AND vst.filing_date >= :start
              AND vst.filing_date <= :end
        """)
        rows = db.execute(sql, {"start": start, "end": end}).all()
        df = pd.DataFrame(rows, columns=[
            "trade_id", "member_id", "bioguide_id", "last_name", "first_name",
            "chamber", "party", "filing_date", "transaction_date", "ticker",
            "sector", "leadership_at_trade", "party_in_majority",
            "chamber_majority_party",
        ])
        logging.info(f"Pulled {len(df)} purchase trades in window")

        # Sector → SPDR ETF
        df["sector_etf"] = df["sector"].map(GICS_TO_ETF)
        df["is_post_trifecta"] = df["transaction_date"].apply(lambda d: d >= TRIFECTA_START)
        df["in_majority"] = df["party_in_majority"].fillna(False).astype(bool)
        df["leadership_at_trade"] = df["leadership_at_trade"].astype(bool)

        # Pull price history for every ticker + SPY + all sector ETFs we touched.
        tickers = set(df["ticker"].dropna().unique())
        etfs = set(df["sector_etf"].dropna().unique()) | {"SPY"}
        ensure_prices(tickers | etfs, start, end + timedelta(days=horizon * 2))

        # Build forward-return columns.
        spy_ser = _close_series(db, "SPY")
        etf_series: dict[str, pd.Series | None] = {e: _close_series(db, e) for e in etfs}
        stock_series_cache: dict[str, pd.Series | None] = {}

        def stock_series(t: str) -> pd.Series | None:
            if t not in stock_series_cache:
                stock_series_cache[t] = _close_series(db, t)
            return stock_series_cache[t]

        ex_spy: list[float | None] = []
        ex_sec: list[float | None] = []
        missing_price = 0
        for _, r in df.iterrows():
            ser = stock_series(r["ticker"])
            anchor = r["filing_date"]
            if ser is None:
                ex_spy.append(np.nan)
                ex_sec.append(np.nan)
                missing_price += 1
                continue
            stock_fwd = _forward_return_n(ser, anchor, horizon)
            spy_fwd = _forward_return_n(spy_ser, anchor, horizon)
            if stock_fwd is None or spy_fwd is None:
                ex_spy.append(np.nan)
            else:
                ex_spy.append(stock_fwd - spy_fwd)
            etf = r["sector_etf"]
            if etf is None or pd.isna(etf) or etf_series.get(etf) is None:
                ex_sec.append(np.nan)
            else:
                etf_fwd = _forward_return_n(etf_series[etf], anchor, horizon)
                if stock_fwd is None or etf_fwd is None:
                    ex_sec.append(np.nan)
                else:
                    ex_sec.append(stock_fwd - etf_fwd)
        df[f"excess_{horizon}d_spy"] = ex_spy
        df[f"excess_{horizon}d_sector"] = ex_sec

        # Drop rows missing the SPY excess (insufficient forward history). The
        # sector excess can be NaN and still keep the row for the SPY-anchored
        # model; the sector-anchored variant filters on it separately.
        before = len(df)
        df = df[df[f"excess_{horizon}d_spy"].notna()].reset_index(drop=True)
        dropped = before - len(df)
        logging.info(f"Dropped {dropped} rows missing forward-return data; final {len(df)} rows")

        # Standardize column types and save.
        df["chamber"] = df["chamber"].astype("string")
        df["party"] = df["party"].astype("string")
        df["sector"] = df["sector"].astype("string")
        df["sector_etf"] = df["sector_etf"].astype("string")

        output.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(output, index=False)
        logging.info(f"Wrote {output}  ({output.stat().st_size:,} bytes)")

        summary = {
            "total_rows": len(df),
            "dropped_no_fwd_return": dropped,
            "missing_stock_price": missing_price,
            "leadership_rows": int(df["leadership_at_trade"].sum()),
            "rankfile_rows": int((~df["leadership_at_trade"]).sum()),
            "rows_with_sector": int(df[f"excess_{horizon}d_sector"].notna().sum()),
            "sector_distribution": df["sector"].value_counts(dropna=False).to_dict(),
            "post_trifecta_rows": int(df["is_post_trifecta"].sum()),
        }
        return summary
    finally:
        db.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", type=date.fromisoformat, default=date(2021, 1, 1))
    p.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    p.add_argument("--horizon", type=int, default=63,
                   help="Forward-return horizon in trading days (default 63).")
    p.add_argument("--output", type=Path, default=Path("/tmp/trade_dataset.parquet"))
    args = p.parse_args()

    summary = build(args.start, args.end, args.horizon, args.output)
    print()
    print(f"Per-trade dataset summary  (output {args.output})")
    print(f"  total rows                : {summary['total_rows']:>6}")
    print(f"  leadership_at_trade=True  : {summary['leadership_rows']:>6}")
    print(f"  leadership_at_trade=False : {summary['rankfile_rows']:>6}")
    print(f"  rows with sector data     : {summary['rows_with_sector']:>6}")
    print(f"  post-trifecta rows        : {summary['post_trifecta_rows']:>6}")
    print(f"  dropped (no fwd return)   : {summary['dropped_no_fwd_return']:>6}")
    print(f"  sector distribution:")
    for sec, n in sorted(summary["sector_distribution"].items(), key=lambda x: -x[1]):
        print(f"    {str(sec):<28} {n}")


if __name__ == "__main__":
    main()
