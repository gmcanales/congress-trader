"""Scheduled disclosure ingest.

Until this existed, ingestion only happened when someone opened the Admin
panel and clicked Scrape. Nothing surfaced the omission, so the data could
(and did) sit untouched for days while the daily snapshot job cheerfully
rebuilt recommendations on top of it.

Re-running a year is cheap: both chamber scrapers skip any `doc_id` already
stored with `parsed=True`, so a daily pass costs one index fetch per chamber
plus whatever genuinely new filings showed up.

Runs a couple of hours ahead of the snapshot job (see app.main) so each day's
recommendations are built on that day's filings. The outcome is written to
`app_settings` under `ingest.last_run`, which the Recommendations freshness
banner reads — a failed or stalled ingest should be visible on the page
rather than inferred from data that stopped moving.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import date, datetime
from typing import Optional

import httpx
from sqlalchemy import text

from app.database import engine
from app.services.scraper import scrape_year
from app.services.senate_scraper import scrape_senate

logger = logging.getLogger(__name__)

SETTING_KEY = "ingest.last_run"

# Which chambers the daily pass attempts. Senate eFD began refusing automated
# POSTs at the edge in Oct 2026 (the agreement POST returns a WAF "Access
# Denied", not a Django error), so there is a case for running house-only —
# set INGEST_CHAMBERS=house. Left attempting both by default: the block may
# be lifted or re-scoped, and a silent stop is worse than a logged refusal.
CHAMBERS = [c.strip().lower()
            for c in os.getenv("INGEST_CHAMBERS", "house,senate").split(",")
            if c.strip()]

# One ingest at a time. The scheduler already enforces this for its own job
# (max_instances=1), but the manual trigger shares the same work and must not
# be able to start a second concurrent pass.
_lock = threading.Lock()


def _years_to_scrape(today: date) -> list[int]:
    """Current year always. Through January, the prior year as well: a trade
    made in December is typically filed in January, and late or amended
    filings keep landing in the prior year's index for weeks after the
    rollover."""
    years = [today.year]
    if today.month == 1:
        years.append(today.year - 1)
    return years


def _record(payload: dict) -> None:
    """Upsert the run summary. Never raises — a bookkeeping failure must not
    be what turns a successful ingest into a failed one."""
    try:
        import json
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_settings (key, value, updated_at) "
                    "VALUES (:k, CAST(:v AS JSONB), now()) "
                    "ON CONFLICT (key) DO UPDATE "
                    "SET value = EXCLUDED.value, updated_at = now()"
                ),
                {"k": SETTING_KEY, "v": json.dumps(payload)},
            )
    except Exception:
        logger.exception("could not record ingest status")


def get_last_ingest() -> Optional[dict]:
    """The most recent run summary, or None if the job has never run."""
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT value, updated_at FROM app_settings WHERE key = :k"),
                {"k": SETTING_KEY},
            ).first()
        if not row:
            return None
        payload = dict(row[0] or {})
        payload["recorded_at"] = row[1].isoformat() if row[1] else None
        return payload
    except Exception:
        logger.exception("could not read ingest status")
        return None


def run_daily_ingest(today: Optional[date] = None,
                     max_docs: Optional[int] = None) -> dict:
    """Scrape both chambers for the relevant year(s) and record the outcome.

    Each (chamber, year) pass is isolated: one chamber failing — the Senate
    flow in particular depends on an agreement/CSRF handshake that breaks
    from time to time — must not cost us the other chamber's filings.
    """
    if not _lock.acquire(blocking=False):
        logger.warning("ingest already running; skipping this trigger")
        return {"ok": False, "skipped": "already_running"}

    today = today or date.today()
    started = datetime.utcnow()
    years = _years_to_scrape(today)
    result: dict = {
        "started_at": started.isoformat(),
        "years": years,
        "passes": [],
        "transactions_added": 0,
        "ok": True,
    }

    try:
        for year in years:
            for chamber, fn in (("house", scrape_year), ("senate", scrape_senate)):
                if chamber not in CHAMBERS:
                    result["passes"].append(
                        {"chamber": chamber, "year": year, "skipped": "disabled"})
                    continue

                entry: dict = {"chamber": chamber, "year": year}
                try:
                    logger.info(f"ingest: {chamber} {year} starting")
                    stats = fn(year, max_docs) or {}
                    entry["ok"] = True
                    entry["stats"] = stats
                    result["transactions_added"] += stats.get("transactions_added", 0) or 0
                    logger.info(f"ingest: {chamber} {year} done — {stats}")
                except Exception as exc:
                    entry["ok"] = False
                    entry["error"] = f"{type(exc).__name__}: {exc}"
                    # Distinguish "the source is refusing us" from "our code
                    # broke". A 403/429 is a standing access decision at the
                    # far end and won't clear by retrying harder; anything
                    # else is ours to fix.
                    if isinstance(exc, httpx.HTTPStatusError) and \
                            exc.response.status_code in (403, 429):
                        entry["blocked"] = True
                        logger.error(
                            f"ingest: {chamber} {year} refused by source "
                            f"(HTTP {exc.response.status_code})")
                    else:
                        logger.exception(f"ingest: {chamber} {year} failed")
                    result["ok"] = False
                result["passes"].append(entry)

        finished = datetime.utcnow()
        result["finished_at"] = finished.isoformat()
        result["duration_seconds"] = round((finished - started).total_seconds(), 1)
        _record(result)
        logger.info(
            f"ingest complete: ok={result['ok']} "
            f"added={result['transactions_added']} "
            f"in {result['duration_seconds']}s"
        )
        return result
    finally:
        _lock.release()
