"""
Backfill `members.party` from unitedstates/congress-legislators.

`bioguide_id` (populated in step 1 of v2) is used as the join key. For each
member we look at every term that legislator served, pick the most recent
term whose [start, end] overlaps our data window (2021-2025), and take that
term's `party` field. The YAML's "Democrat" / "Republican" / "Independent"
labels are normalized to "Democratic" / "Republican" / "Independent" so they
match `party_control.majority_party` for the
`v_stock_transactions_leadership.party_in_majority` join.

Run:

  docker compose run --rm --no-deps backend python backfill_party.py --dry-run
  docker compose run --rm --no-deps backend python backfill_party.py --commit
  docker compose run --rm --no-deps backend python backfill_party.py --commit --force

Default is dry-run. The script never overwrites an existing party value
unless `--force` is given; that way it's safe to run incrementally as new
members get bioguide_ids attached.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date
from pathlib import Path

import requests

from app.database import SessionLocal
from app.models import Member


DATA_BASE = "https://unitedstates.github.io/congress-legislators"
LEGISLATORS_CURRENT_URL = f"{DATA_BASE}/legislators-current.json"
LEGISLATORS_HISTORICAL_URL = f"{DATA_BASE}/legislators-historical.json"
CACHE_DIR = Path("/tmp/congress-legislators-cache")
CACHE_MAX_AGE_SEC = 86_400

# Our trading-data window. Used to pick the most-relevant term when a
# legislator served multiple times.
DATA_WINDOW_START = date(2021, 1, 1)
DATA_WINDOW_END = date(2025, 12, 31)

# Repo uses these labels; party_control was seeded with "Democratic".
PARTY_NORMALIZE = {
    "Democrat": "Democratic",
    "Democratic": "Democratic",
    "Republican": "Republican",
    "Independent": "Independent",
}


def _fetch_json(url: str, cache_path: Path) -> list[dict]:
    if cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < CACHE_MAX_AGE_SEC:
        logging.info(f"Using cached {cache_path.name}")
        return json.loads(cache_path.read_text())
    logging.info(f"Downloading {url}")
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(r.text)
    return r.json()


def _term_dates(term: dict) -> tuple[date | None, date | None]:
    def parse(s):
        if not s:
            return None
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None
    return parse(term.get("start")), parse(term.get("end"))


def _pick_term_party(legislator: dict) -> str | None:
    """Most recent term whose [start, end] overlaps our data window. Falls
    back to the legislator's latest term if no term touches the window."""
    terms = legislator.get("terms") or []
    if not terms:
        return None
    overlapping = []
    latest = None
    for t in terms:
        ts, te = _term_dates(t)
        if ts is None:
            continue
        if latest is None or ts > _term_dates(latest)[0]:
            latest = t
        if te is None or te < ts:
            te = date(9999, 12, 31)
        if ts <= DATA_WINDOW_END and te >= DATA_WINDOW_START:
            overlapping.append((ts, t))
    if overlapping:
        overlapping.sort(key=lambda x: x[0], reverse=True)
        chosen = overlapping[0][1]
    else:
        chosen = latest
    if chosen is None:
        return None
    raw = chosen.get("party")
    if not raw:
        return None
    return PARTY_NORMALIZE.get(raw, raw)


def backfill(commit: bool, force: bool, report_path: str | None) -> dict:
    current = _fetch_json(LEGISLATORS_CURRENT_URL, CACHE_DIR / "legislators-current.json")
    historical = _fetch_json(LEGISLATORS_HISTORICAL_URL, CACHE_DIR / "legislators-historical.json")
    by_bio = {(leg.get("id") or {}).get("bioguide"): leg
              for leg in current + historical
              if (leg.get("id") or {}).get("bioguide")}

    s = SessionLocal()
    updated: list[dict] = []
    skipped_existing: list[int] = []
    skipped_no_bio: list[int] = []
    unmatched: list[dict] = []
    changed: list[dict] = []  # force=True and value actually differs

    try:
        members = s.query(Member).all()
        for m in members:
            if not m.bioguide_id:
                skipped_no_bio.append(m.id)
                continue
            leg = by_bio.get(m.bioguide_id)
            if leg is None:
                unmatched.append({"member_id": m.id, "bioguide_id": m.bioguide_id,
                                   "name": f"{m.first_name} {m.last_name}"})
                continue
            party = _pick_term_party(leg)
            if party is None:
                unmatched.append({"member_id": m.id, "bioguide_id": m.bioguide_id,
                                   "name": f"{m.first_name} {m.last_name}",
                                   "reason": "no_party_on_term"})
                continue
            if m.party and not force:
                skipped_existing.append(m.id)
                continue
            if m.party != party:
                if m.party:  # force=True overwriting different value
                    changed.append({"member_id": m.id, "name": f"{m.first_name} {m.last_name}",
                                     "old": m.party, "new": party})
                if commit:
                    m.party = party
                updated.append({"member_id": m.id, "bioguide_id": m.bioguide_id,
                                "name": f"{m.first_name} {m.last_name}", "party": party})

        if commit:
            s.commit()
        else:
            s.rollback()
    finally:
        s.close()

    summary = {
        "commit": commit, "force": force,
        "total_members": len(updated) + len(skipped_existing) + len(skipped_no_bio) + len(unmatched),
        "updated": len(updated),
        "skipped_existing": len(skipped_existing),
        "skipped_no_bioguide": len(skipped_no_bio),
        "unmatched": len(unmatched),
        "changed_under_force": len(changed),
        "changed_examples": changed[:10],
        "unmatched_rows": unmatched,
    }
    if report_path:
        Path(report_path).write_text(json.dumps(summary, indent=2))
        logging.info(f"Report written to {report_path}")
    return summary


def _print(s: dict) -> None:
    print()
    print(f"Party backfill summary  (commit={s['commit']}, force={s['force']})")
    print(f"  total members        : {s['total_members']:>5}")
    print(f"  updated              : {s['updated']:>5}")
    print(f"  skipped (already set): {s['skipped_existing']:>5}")
    print(f"  skipped (no bioguide): {s['skipped_no_bioguide']:>5}")
    print(f"  unmatched            : {s['unmatched']:>5}")
    if s["changed_under_force"]:
        print(f"\n  CHANGED values (under --force, sample):")
        for c in s["changed_examples"]:
            print(f"    #{c['member_id']:>4}  {c['name']:<30}  {c['old']!r:<14} → {c['new']!r}")
    if s["unmatched"]:
        print(f"\n  Unmatched (no legislator or no party):")
        for r in s["unmatched_rows"][:20]:
            print(f"    #{r['member_id']:>4}  {r.get('bioguide_id',''):<10}  {r['name']:<30}  "
                  f"reason={r.get('reason','no_legislator')}")
        if len(s["unmatched_rows"]) > 20:
            print(f"    … {len(s['unmatched_rows']) - 20} more")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--commit", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="Overwrite party even when already set.")
    p.add_argument("--report", help="Write the full summary as JSON to this path.")
    p.add_argument("--dry-run", action="store_true", help="(default) Compatibility alias.")
    args = p.parse_args()

    summary = backfill(commit=args.commit, force=args.force, report_path=args.report)
    _print(summary)
    sys.exit(0 if summary["unmatched"] == 0 else 2)


if __name__ == "__main__":
    main()
