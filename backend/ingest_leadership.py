"""
Ingest congressional leadership data from unitedstates/congress-legislators.

Populates three v2 tables:

  * `committees`     — committee directory (current + historical) with
                       thomas_id, chamber, parent linkage for subcommittees.
  * `positions`      — one row per leadership-role tenure per member,
                       keyed on the bioguide_id backfilled in step 1.
  * `party_control`  — hard-coded majority party per (chamber, congress)
                       for the 117th–119th Congress, covering our 2021–2025
                       data window. (Easy to extend backward when needed.)

Committee chairs / ranking members are deferred — the YAML only ships
`committee-membership-current.yaml`, and historical chair tenure requires
a separate source (Congress.gov bulk committee XML). This script intentionally
does *not* try to fake that data.

Run inside the backend container:

  docker compose run --rm --no-deps backend python ingest_leadership.py --dry-run
  docker compose run --rm --no-deps backend python ingest_leadership.py --commit
  docker compose run --rm --no-deps backend python ingest_leadership.py --commit \\
      --report /tmp/leadership_ingest.json

Default is `--dry-run`: nothing written. Counts and per-table changes are
printed (and optionally JSON-dumped) so the result can be reviewed before
persisting. The script is idempotent under `--commit`: re-running upserts
on natural keys.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Iterable

import requests
from sqlalchemy import select

from app.database import SessionLocal, engine, Base
from app.models import (
    Committee, Member, PartyControl, Position,
)


DATA_BASE = "https://unitedstates.github.io/congress-legislators"
LEGISLATORS_CURRENT_URL = f"{DATA_BASE}/legislators-current.json"
LEGISLATORS_HISTORICAL_URL = f"{DATA_BASE}/legislators-historical.json"
COMMITTEES_CURRENT_URL = f"{DATA_BASE}/committees-current.json"
COMMITTEES_HISTORICAL_URL = f"{DATA_BASE}/committees-historical.json"
CACHE_DIR = Path("/tmp/congress-legislators-cache")
CACHE_MAX_AGE_SEC = 86_400
SOURCE_TAG = "unitedstates/congress-legislators"

# Hard-coded chamber majority by Congress. Sources: official chamber records;
# 117th Senate was 50-50 with VP Harris (D) breaking ties → effectively D.
# Extend this list when our data window grows.
PARTY_CONTROL_SEED = [
    # (chamber, congress, majority_party, start_date, end_date)
    ("house",  117, "Democratic", date(2021, 1, 3), date(2023, 1, 3)),
    ("senate", 117, "Democratic", date(2021, 1, 3), date(2023, 1, 3)),
    ("house",  118, "Republican", date(2023, 1, 3), date(2025, 1, 3)),
    ("senate", 118, "Democratic", date(2023, 1, 3), date(2025, 1, 3)),
    ("house",  119, "Republican", date(2025, 1, 3), date(2027, 1, 3)),
    ("senate", 119, "Republican", date(2025, 1, 3), date(2027, 1, 3)),
]


# ── Fetch helpers ───────────────────────────────────────────────────────────

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


# ── Date / chamber helpers ──────────────────────────────────────────────────

def _to_date(s: str | None) -> date | None:
    """The repo uses YYYY-MM-DD when known and sometimes just YYYY for older
    leadership rows. Accept both."""
    if not s:
        return None
    if len(s) == 4 and s.isdigit():
        return date(int(s), 1, 1)
    try:
        return date.fromisoformat(s)
    except ValueError:
        logging.warning(f"unparseable date: {s!r}")
        return None


def _congress_for_date(d: date | None) -> int | None:
    """117th Congress = 2021-01-03 to 2023-01-03, etc. Each Congress spans 2
    calendar years starting on Jan 3 of odd years."""
    if d is None:
        return None
    # 117th Congress starts 2021. Formula: ((year - 1789) // 2) + 1
    # Anchor: 1789 = 1st, 1791 = 2nd, …, 2021 = 117th, 2023 = 118th.
    year = d.year - (1 if d.month == 1 and d.day < 3 else 0)
    return ((year - 1789) // 2) + 1


# ── Committees ──────────────────────────────────────────────────────────────

def _committee_chamber(c_type: str) -> str:
    return {"house": "house", "senate": "senate", "joint": "joint"}.get(c_type, c_type)


def ingest_committees(session, committee_payloads: Iterable[dict]) -> dict:
    """Upsert committees + subcommittees keyed on thomas_id."""
    existing = {c.thomas_id: c for c in session.scalars(select(Committee)).all()}
    inserted = 0
    updated = 0

    for c in committee_payloads:
        thomas_id = c.get("thomas_id")
        if not thomas_id:
            continue
        name = c.get("name") or ""
        chamber = _committee_chamber(c.get("type") or "")
        row = existing.get(thomas_id)
        if row is None:
            row = Committee(
                thomas_id=thomas_id, name=name, chamber=chamber,
                committee_type="standing" if chamber != "joint" else "joint",
                parent_committee_id=None,
            )
            session.add(row)
            session.flush()
            existing[thomas_id] = row
            inserted += 1
        else:
            if row.name != name or row.chamber != chamber:
                row.name = name
                row.chamber = chamber
                updated += 1

        for sub in c.get("subcommittees") or []:
            sub_local = sub.get("thomas_id")
            if not sub_local:
                continue
            sub_thomas = f"{thomas_id}_{sub_local}"
            sub_name = sub.get("name") or ""
            sub_row = existing.get(sub_thomas)
            if sub_row is None:
                sub_row = Committee(
                    thomas_id=sub_thomas, name=sub_name, chamber=chamber,
                    committee_type="subcommittee", parent_committee_id=row.id,
                )
                session.add(sub_row)
                existing[sub_thomas] = sub_row
                inserted += 1
            elif sub_row.name != sub_name or sub_row.parent_committee_id != row.id:
                sub_row.name = sub_name
                sub_row.parent_committee_id = row.id
                updated += 1

    return {"inserted": inserted, "updated": updated, "total": len(existing)}


# ── Leadership positions ────────────────────────────────────────────────────

def _position_key(member_id: int, title: str, start: date) -> tuple:
    return (member_id, title, start)


def ingest_positions(session, legislators: list[dict]) -> dict:
    """Map each leadership_roles entry → a `positions` row.

    Joins to members via bioguide_id (must be backfilled in step 1). Members
    whose bioguide we don't have are skipped — they have no rows in our
    transactions data anyway.
    """
    members_by_bio = {m.bioguide_id: m for m in session.scalars(select(Member)).all() if m.bioguide_id}

    # Existing positions, keyed by natural key so re-runs are idempotent.
    existing: dict[tuple, Position] = {}
    for p in session.scalars(select(Position).where(Position.role_type == "leadership")).all():
        existing[_position_key(p.member_id, p.title, p.start_date)] = p

    inserted = 0
    skipped_no_member = 0
    skipped_no_role = 0
    skipped_no_start = 0

    for leg in legislators:
        bio = (leg.get("id") or {}).get("bioguide")
        if not bio:
            continue
        member = members_by_bio.get(bio)
        if member is None:
            # This legislator has no trades in our dataset — fine, skip.
            skipped_no_member += 1
            continue
        roles = leg.get("leadership_roles") or []
        if not roles:
            skipped_no_role += 1
            continue
        for role in roles:
            title = role.get("title")
            if not title:
                continue
            start = _to_date(role.get("start"))
            end = _to_date(role.get("end"))
            if start is None:
                skipped_no_start += 1
                continue
            key = _position_key(member.id, title, start)
            if key in existing:
                continue
            pos = Position(
                member_id=member.id,
                role_type="leadership",
                title=title,
                committee_id=None,
                congress=_congress_for_date(start),
                start_date=start,
                end_date=end,
                source=SOURCE_TAG,
            )
            session.add(pos)
            existing[key] = pos
            inserted += 1

    return {
        "inserted": inserted,
        "skipped_no_member": skipped_no_member,
        "skipped_no_role": skipped_no_role,
        "skipped_no_start": skipped_no_start,
        "total_after": len(existing),
    }


# ── Party control ───────────────────────────────────────────────────────────

def ingest_party_control(session) -> dict:
    existing = {(pc.chamber, pc.congress): pc
                for pc in session.scalars(select(PartyControl)).all()}
    inserted = 0
    for chamber, congress, party, start, end in PARTY_CONTROL_SEED:
        if (chamber, congress) in existing:
            continue
        session.add(PartyControl(chamber=chamber, congress=congress,
                                  majority_party=party,
                                  start_date=start, end_date=end))
        inserted += 1
    return {"inserted": inserted, "total_after": len(existing) + inserted}


# ── Orchestration ───────────────────────────────────────────────────────────

def run(commit: bool, report_path: str | None) -> dict:
    # Ensure new tables exist when the script runs outside the FastAPI lifespan.
    Base.metadata.create_all(bind=engine)

    current = _fetch_json(LEGISLATORS_CURRENT_URL, CACHE_DIR / "legislators-current.json")
    historical = _fetch_json(LEGISLATORS_HISTORICAL_URL, CACHE_DIR / "legislators-historical.json")
    committees_current = _fetch_json(COMMITTEES_CURRENT_URL, CACHE_DIR / "committees-current.json")
    committees_historical = _fetch_json(COMMITTEES_HISTORICAL_URL, CACHE_DIR / "committees-historical.json")
    legislators = current + historical
    committees_all = committees_current + committees_historical

    session = SessionLocal()
    try:
        committees_stats = ingest_committees(session, committees_all)
        positions_stats = ingest_positions(session, legislators)
        party_stats = ingest_party_control(session)
        if commit:
            session.commit()
        else:
            session.rollback()
    finally:
        session.close()

    summary = {
        "commit": commit,
        "legislators_loaded": len(legislators),
        "committees_loaded": len(committees_all),
        "committees": committees_stats,
        "positions": positions_stats,
        "party_control": party_stats,
    }
    if report_path:
        Path(report_path).write_text(json.dumps(summary, indent=2))
        logging.info(f"Report written to {report_path}")
    return summary


def _print(s: dict) -> None:
    print()
    print(f"Leadership ingest summary  (commit={s['commit']})")
    print(f"  legislators_loaded   : {s['legislators_loaded']:>6}")
    print(f"  committees_loaded    : {s['committees_loaded']:>6}")
    print()
    c = s["committees"]
    print(f"  committees           : inserted={c['inserted']:>5}  updated={c['updated']:>5}  total={c['total']:>5}")
    p = s["positions"]
    print(f"  positions (lead)     : inserted={p['inserted']:>5}  "
          f"skip_no_member={p['skipped_no_member']:>4}  skip_no_role={p['skipped_no_role']:>5}  "
          f"skip_no_start={p['skipped_no_start']:>3}  total_after={p['total_after']:>4}")
    pc = s["party_control"]
    print(f"  party_control        : inserted={pc['inserted']:>5}  total_after={pc['total_after']:>5}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--commit", action="store_true",
                   help="Persist inserts/updates. Default is dry-run.")
    p.add_argument("--report", help="Write the full ingest summary as JSON to this path.")
    args = p.parse_args()

    summary = run(commit=args.commit, report_path=args.report)
    _print(summary)


if __name__ == "__main__":
    main()
