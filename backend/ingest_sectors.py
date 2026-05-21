"""
Ingest GICS sector / industry for every ticker referenced in `transactions`.

Source: yfinance `Ticker.info`. Each ticker is a separate API call, so this
takes a few minutes for ~500 tickers — but it's idempotent and cached in
the `tickers` table, so re-runs only refresh stale rows (or all rows with
--force).

Run:

  docker compose run --rm --no-deps backend python ingest_sectors.py --dry-run
  docker compose run --rm --no-deps backend python ingest_sectors.py --commit
  docker compose run --rm --no-deps backend python ingest_sectors.py --commit --force
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf
from sqlalchemy import select, distinct

from app.database import SessionLocal, engine, Base
from app.models import Transaction, TickerInfo


REFRESH_AFTER_DAYS = 30      # rows older than this are re-fetched without --force
THROTTLE_SECONDS = 0.6        # be polite to Yahoo; tunable
SOURCE_TAG = "yfinance"


def _is_stale(row: TickerInfo) -> bool:
    if not row.last_updated:
        return True
    return (datetime.utcnow() - row.last_updated) > timedelta(days=REFRESH_AFTER_DAYS)


def _fetch_ticker_info(symbol: str) -> dict | None:
    try:
        t = yf.Ticker(symbol)
        info = t.info or {}
    except Exception as exc:
        logging.warning(f"{symbol}: fetch failed: {exc}")
        return None
    # yfinance sometimes returns mostly-empty dicts for delisted/bad tickers.
    if not info.get("sector") and not info.get("industry"):
        return None
    return {
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": info.get("marketCap"),
    }


def ingest(commit: bool, force: bool, report_path: str | None) -> dict:
    Base.metadata.create_all(bind=engine)

    s = SessionLocal()
    try:
        all_symbols = [t for (t,) in s.execute(
            select(distinct(Transaction.ticker))
            .where(Transaction.ticker.isnot(None))
            .where(Transaction.ticker != "")
        ).all()]
        existing = {r.symbol: r for r in s.scalars(select(TickerInfo)).all()}

        fetched = 0
        cached = 0
        failed = 0
        empty = 0
        failed_symbols: list[str] = []
        empty_symbols: list[str] = []

        logging.info(f"{len(all_symbols)} distinct tickers in transactions")

        for i, symbol in enumerate(all_symbols):
            row = existing.get(symbol)
            if row is not None and not force and not _is_stale(row):
                cached += 1
                continue
            info = _fetch_ticker_info(symbol)
            if info is None:
                # Distinguish between "fetch failed entirely" (no yfinance row)
                # and "fetched but no sector data" — both rendered as no-info
                # but the second still gets a stub row so we don't re-fetch.
                if row is None:
                    s.add(TickerInfo(symbol=symbol, source=SOURCE_TAG,
                                     last_updated=datetime.utcnow()))
                    empty += 1
                    empty_symbols.append(symbol)
                else:
                    failed += 1
                    failed_symbols.append(symbol)
                time.sleep(THROTTLE_SECONDS)
                continue
            if row is None:
                row = TickerInfo(symbol=symbol, source=SOURCE_TAG)
                s.add(row)
            row.sector = info["sector"]
            row.industry = info["industry"]
            row.market_cap = info["market_cap"]
            row.last_updated = datetime.utcnow()
            fetched += 1
            if (i + 1) % 25 == 0:
                logging.info(f"  progress: {i + 1}/{len(all_symbols)}  "
                             f"fetched={fetched} cached={cached} empty={empty} failed={failed}")
            time.sleep(THROTTLE_SECONDS)

        if commit:
            s.commit()
        else:
            s.rollback()

        # Distribution by sector for the diagnostic.
        sector_counts: dict[str, int] = {}
        for r in s.scalars(select(TickerInfo)).all():
            sec = r.sector or "(unknown)"
            sector_counts[sec] = sector_counts.get(sec, 0) + 1
    finally:
        s.close()

    summary = {
        "commit": commit, "force": force,
        "total_symbols": len(all_symbols),
        "fetched": fetched,
        "cached": cached,
        "empty_after_fetch": empty,
        "fetch_failed": failed,
        "sector_distribution": dict(sorted(sector_counts.items(), key=lambda x: -x[1])),
        "empty_symbols": empty_symbols[:30],
        "failed_symbols": failed_symbols[:30],
    }
    if report_path:
        Path(report_path).write_text(json.dumps(summary, indent=2))
        logging.info(f"Report written to {report_path}")
    return summary


def _print(s: dict) -> None:
    print()
    print(f"Sector ingest summary  (commit={s['commit']}, force={s['force']})")
    print(f"  total tickers           : {s['total_symbols']:>5}")
    print(f"  fetched (new/refreshed) : {s['fetched']:>5}")
    print(f"  cached (still fresh)    : {s['cached']:>5}")
    print(f"  no sector after fetch   : {s['empty_after_fetch']:>5}")
    print(f"  fetch failed            : {s['fetch_failed']:>5}")
    print()
    print(f"  Sector distribution (current `tickers` table contents):")
    for sec, n in s["sector_distribution"].items():
        print(f"    {sec:<28} {n}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--commit", action="store_true")
    p.add_argument("--force", action="store_true",
                   help=f"Re-fetch every row regardless of last_updated. "
                        f"Default behavior refreshes only rows older than "
                        f"{REFRESH_AFTER_DAYS} days.")
    p.add_argument("--report", help="JSON output path for the full summary.")
    args = p.parse_args()
    summary = ingest(commit=args.commit, force=args.force, report_path=args.report)
    _print(summary)


if __name__ == "__main__":
    main()
