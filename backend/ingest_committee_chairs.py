"""
Ingest committee chair / ranking-member tenure for the 117th, 118th, and
119th Congresses from unitedstates/congress-legislators.

The repo's `committee-membership-current.yaml` is, as the name says, a
current snapshot — but the file is in git history, so we can fetch
historical versions via the GitHub API by commit SHA. For each Congress
we pick a "mid-term" target date when committee assignments are stable
(not the December 25 "clear" commits that wipe the file between
Congresses), grab the commit nearest to it, fetch the YAML, and extract
the chair (rank=1, party=majority) and ranking member (rank=1,
party=minority) of every full committee. Subcommittee chairs are
intentionally skipped here — they're additional noise on top of the
leadership signal we're actually trying to test.

Run:

  docker compose run --rm --no-deps backend python ingest_committee_chairs.py --dry-run
  docker compose run --rm --no-deps backend python ingest_committee_chairs.py --commit
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import requests
import yaml
from sqlalchemy import select

from app.database import SessionLocal, engine, Base
from app.models import Committee, Member, Position


REPO_OWNER = "unitedstates"
REPO_NAME = "congress-legislators"
FILE_PATH = "committee-membership-current.yaml"
CACHE_DIR = Path("/tmp/congress-legislators-cache")
CACHE_MAX_AGE_SEC = 86_400
SOURCE_TAG = f"{REPO_OWNER}/{REPO_NAME}#chairs"


# (congress, mid-term target date, congress_start, congress_end)
# Pick dates a few months in so assignments are stable and not on a
# Dec 25 "clear committee assignments" commit.
CONGRESS_TARGETS = [
    (117, date(2022, 6, 15), date(2021, 1, 3),  date(2023, 1, 3)),
    (118, date(2024, 6, 15), date(2023, 1, 3),  date(2025, 1, 3)),
    (119, date(2026, 4, 15), date(2025, 1, 3),  date(2027, 1, 3)),
]


# ── GitHub API + raw content ────────────────────────────────────────────────

def _find_commit(target: date) -> str:
    """The most recent commit to committee-membership-current.yaml on or
    before `target`. Returns the SHA."""
    r = requests.get(
        f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/commits",
        params={
            "path": FILE_PATH,
            "until": f"{target.isoformat()}T23:59:59Z",
            "per_page": 1,
        },
        timeout=30,
    )
    r.raise_for_status()
    commits = r.json()
    if not commits:
        raise RuntimeError(f"no commits for {FILE_PATH} on/before {target}")
    return commits[0]["sha"]


def _fetch_yaml_at_commit(sha: str) -> dict:
    """Fetch the file at the given commit, with on-disk cache."""
    cache_path = CACHE_DIR / f"committee-membership-{sha[:12]}.yaml"
    if cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < CACHE_MAX_AGE_SEC:
        logging.info(f"Using cached {cache_path.name}")
        text = cache_path.read_text()
    else:
        url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{sha}/{FILE_PATH}"
        logging.info(f"Downloading {url}")
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        text = r.text
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text)
    return yaml.safe_load(text) or {}


# ── Chair extraction ────────────────────────────────────────────────────────

def _classify(member: dict) -> str | None:
    """Return 'chair' / 'ranking' / None.

    Format: each member has `rank` (int) and `party` ('majority'|'minority').
    The chair is rank 1 in majority; the ranking member is rank 1 in minority.
    """
    if member.get("rank") != 1:
        return None
    party = member.get("party")
    if party == "majority":
        return "chair"
    if party == "minority":
        return "ranking"
    return None


def _extract_chairs(membership_yaml: dict, full_committee_ids: set[str]) -> list[dict]:
    """Walk the membership file and yield chair / ranking-member entries for
    each FULL committee (excluding subcommittees). Returns dicts with
    bioguide_id, role_type, committee_thomas_id."""
    out: list[dict] = []
    for thomas_id, members in (membership_yaml or {}).items():
        if thomas_id not in full_committee_ids:
            continue  # skip subcommittees and unknown ids
        if not isinstance(members, list):
            continue
        for m in members:
            role = _classify(m)
            if not role:
                continue
            bio = m.get("bioguide")
            if not bio:
                continue
            out.append({
                "bioguide_id": bio,
                "role_type": role,
                "committee_thomas_id": thomas_id,
                "title": m.get("title") or ("Chair" if role == "chair" else "Ranking Member"),
            })
    return out


# ── Ingest orchestration ────────────────────────────────────────────────────

def _position_key(member_id: int, role_type: str, committee_id: int | None,
                  start_date: date) -> tuple:
    return (member_id, role_type, committee_id, start_date)


def ingest(commit: bool, report_path: str | None) -> dict:
    Base.metadata.create_all(bind=engine)

    s = SessionLocal()
    try:
        committees_by_thomas = {c.thomas_id: c for c in s.scalars(select(Committee)).all()}
        full_committee_ids = {tid for tid, c in committees_by_thomas.items()
                              if c.parent_committee_id is None}
        members_by_bio = {m.bioguide_id: m for m in s.scalars(select(Member)).all()
                          if m.bioguide_id}
        existing_positions = {
            _position_key(p.member_id, p.role_type, p.committee_id, p.start_date): p
            for p in s.scalars(select(Position)
                               .where(Position.role_type.in_(("chair", "ranking")))).all()
        }

        per_congress_stats = []
        total_inserted = 0
        total_skipped_no_member = 0
        total_skipped_no_committee = 0

        for congress, target, congress_start, congress_end in CONGRESS_TARGETS:
            sha = _find_commit(target)
            mem = _fetch_yaml_at_commit(sha)
            chairs = _extract_chairs(mem, full_committee_ids)
            inserted = 0
            skipped_no_member = 0
            skipped_no_committee = 0
            for entry in chairs:
                committee = committees_by_thomas.get(entry["committee_thomas_id"])
                if committee is None:
                    skipped_no_committee += 1
                    continue
                member = members_by_bio.get(entry["bioguide_id"])
                if member is None:
                    skipped_no_member += 1
                    continue
                key = _position_key(member.id, entry["role_type"],
                                    committee.id, congress_start)
                if key in existing_positions:
                    continue
                pos = Position(
                    member_id=member.id,
                    role_type=entry["role_type"],
                    title=entry["title"],
                    committee_id=committee.id,
                    congress=congress,
                    start_date=congress_start,
                    end_date=congress_end,
                    source=f"{SOURCE_TAG}@{sha[:10]}",
                )
                s.add(pos)
                existing_positions[key] = pos
                inserted += 1
            per_congress_stats.append({
                "congress": congress,
                "target": target.isoformat(),
                "sha": sha[:10],
                "chair_entries": len(chairs),
                "inserted": inserted,
                "skipped_no_member": skipped_no_member,
                "skipped_no_committee": skipped_no_committee,
            })
            total_inserted += inserted
            total_skipped_no_member += skipped_no_member
            total_skipped_no_committee += skipped_no_committee

        if commit:
            s.commit()
        else:
            s.rollback()
    finally:
        s.close()

    summary = {
        "commit": commit,
        "total_inserted": total_inserted,
        "total_skipped_no_member": total_skipped_no_member,
        "total_skipped_no_committee": total_skipped_no_committee,
        "per_congress": per_congress_stats,
    }
    if report_path:
        Path(report_path).write_text(json.dumps(summary, indent=2))
        logging.info(f"Report written to {report_path}")
    return summary


def _print(s: dict) -> None:
    print()
    print(f"Committee-chair ingest summary  (commit={s['commit']})")
    print(f"  total inserted             : {s['total_inserted']:>5}")
    print(f"  skipped (no member in DB)  : {s['total_skipped_no_member']:>5}")
    print(f"  skipped (no committee row) : {s['total_skipped_no_committee']:>5}")
    print()
    print(f"  {'congress':<10} {'target':<12} {'commit':<12} {'entries':<8} {'inserted':<8} {'skip_mem':<9} {'skip_cmte':<10}")
    for r in s["per_congress"]:
        print(f"  {r['congress']:<10} {r['target']:<12} {r['sha']:<12} "
              f"{r['chair_entries']:<8} {r['inserted']:<8} {r['skipped_no_member']:<9} {r['skipped_no_committee']:<10}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--commit", action="store_true")
    p.add_argument("--report", help="JSON output path for the full summary.")
    args = p.parse_args()
    summary = ingest(commit=args.commit, report_path=args.report)
    _print(summary)


if __name__ == "__main__":
    main()
