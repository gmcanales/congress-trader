"""
Backfill `members.bioguide_id` from unitedstates/congress-legislators.

bioguide_id is the canonical join key with external congressional datasets
(GovTrack, ProPublica, Congress.gov bulk data). Once every member row carries
one, we can ingest committee tenure and leadership-position tables against it
without ad-hoc name matching every time. See `docs/v2_leadership_plan.md`.

Run inside the backend container:

  docker compose exec backend python backfill_bioguide.py --dry-run
  docker compose exec backend python backfill_bioguide.py --commit
  docker compose exec backend python backfill_bioguide.py --commit \\
      --report /tmp/bioguide_backfill.json

Default is `--dry-run`: nothing is written; counts and unmatched/ambiguous
lists are printed (and optionally written to --report). Use --commit to
persist.

Matching strategy
-----------------
1. Pull `legislators-current.json` + `legislators-historical.json` from
   github.com/unitedstates/congress-legislators (cached locally for 24h).
2. For each legislator term, build an index keyed by
   `(state, district_or_None, normalized_last_name)`. House terms supply a
   district; Senate terms set district = None.
3. For each member, derive the same key from `chamber + state_dst + last_name`
   and look up candidate legislators.
4. Disambiguate ties by first-name match: exact > nickname/official-full >
   prefix. If still ambiguous after first-name tie-breaking, the member is
   reported for manual resolution rather than guessed at.

The script never overwrites an already-set bioguide_id unless --force is
passed.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import requests

from app.database import SessionLocal
from app.models import Member


# JSON variants live on the project's GitHub Pages site (the canonical repo
# only checks in YAML on `main`). HTTP 200 confirmed against this host.
DATA_BASE = "https://unitedstates.github.io/congress-legislators"
LEGISLATORS_CURRENT_URL = f"{DATA_BASE}/legislators-current.json"
LEGISLATORS_HISTORICAL_URL = f"{DATA_BASE}/legislators-historical.json"
CACHE_DIR = Path("/tmp/congress-legislators-cache")
CACHE_MAX_AGE_SEC = 86_400  # 24h

# Generational + professional/credential suffixes commonly tacked onto a
# last_name field in PTR filings ("Dunn, MD, FACS", "King, Jr.", etc.).
_SUFFIX_RE = re.compile(
    r"[,\s]+(jr|sr|ii|iii|iv|v|m\.?d\.?|d\.?o\.?|d\.?d\.?s\.?|ph\.?d\.?|j\.?d\.?|esq|cpa|facs|fasc|fapha)\.?$",
    re.IGNORECASE,
)
# Strip whitespace, hyphens, periods, commas, and both straight + curly apostrophes.
_PUNCT_RE = re.compile(r"[\s\-'’\.,]")
_TOKEN_SPLIT_RE = re.compile(r"[\s\-]+")


def _strip_diacritics(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c))


def _norm_last(name: str) -> str:
    name = _strip_diacritics(name).lower().strip()
    prev = None
    while name != prev:  # strip suffixes iteratively in case multiple stack
        prev = name
        name = _SUFFIX_RE.sub("", name).strip()
    return _PUNCT_RE.sub("", name)


def _norm_first(name: str) -> str:
    return _PUNCT_RE.sub("", _strip_diacritics(name).lower().strip())


def _last_name_keys(last_raw: str) -> set[str]:
    """Multi-token-aware keys for a last_name field.

    Returns the full normalized form plus each individual token, so
    "Hinson Arenholz" → {"hinsonarenholz", "hinson", "arenholz"} and a
    YAML last="McMorris Rodgers" gets indexed under both "mcmorrisrodgers"
    *and* "rodgers", which is what the PTR filing actually carries.
    """
    if not last_raw:
        return set()
    raw_norm = _strip_diacritics(last_raw).lower().strip()
    raw_norm = _SUFFIX_RE.sub("", raw_norm).strip()
    keys = {_PUNCT_RE.sub("", raw_norm)}
    for tok in _TOKEN_SPLIT_RE.split(raw_norm):
        tok_clean = _PUNCT_RE.sub("", tok)
        if tok_clean:
            keys.add(tok_clean)
    return {k for k in keys if k}


def _member_last_keys(member: Member) -> set[str]:
    """Last-name keys for a Member, with cross-field rescue.

    Filings occasionally split a compound surname into the wrong fields —
    e.g. first="Cathy McMorris", last="Rodgers". We also emit the
    combination of the last first-name token with the last_name field, so
    "mcmorrisrodgers" is a hit against the legislator key.
    """
    keys = _last_name_keys(member.last_name)
    first_tokens = _TOKEN_SPLIT_RE.split(
        _strip_diacritics(member.first_name or "").lower().strip()
    )
    if len(first_tokens) >= 2 and first_tokens[-1]:
        combined = f"{first_tokens[-1]} {member.last_name or ''}".strip()
        keys.update(_last_name_keys(combined))
    return keys


def _name_variants(name_obj: dict) -> set[str]:
    """All first-name variants we'll accept as a match against a member."""
    variants: set[str] = set()
    for key in ("first", "nickname", "middle"):
        v = name_obj.get(key)
        if v:
            variants.add(_norm_first(v))
    official = name_obj.get("official_full") or ""
    # Strip "Last, First …" formatting if present.
    head = official.split(",", 1)[-1].strip()
    for token in head.split():
        variants.add(_norm_first(token))
    variants.discard("")
    return variants


def _parse_state_dst(chamber: str | None, state_dst: str | None) -> tuple[str | None, int | None]:
    if not state_dst:
        return None, None
    state_dst = state_dst.strip()
    if chamber == "house":
        state = state_dst[:2].upper()
        rest = state_dst[2:]
        try:
            district = int(rest)
        except ValueError:
            district = None
        return state, district
    if chamber == "senate":
        return state_dst[:2].upper(), None
    return None, None


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


def _load_legislators() -> list[dict]:
    current = _fetch_json(LEGISLATORS_CURRENT_URL, CACHE_DIR / "legislators-current.json")
    historical = _fetch_json(LEGISLATORS_HISTORICAL_URL, CACHE_DIR / "legislators-historical.json")
    logging.info(f"Loaded {len(current)} current + {len(historical)} historical legislators")
    return current + historical


def _build_index(legislators: Iterable[dict]) -> dict[tuple, list[dict]]:
    """
    key = (state, district, last_key) -> list of {leg, term, term_end}

    Indexed under EVERY last-name key for the legislator (full normalized
    form + each token), so multi-word surnames in either dataset still
    join. Senate terms set district = None.
    """
    index: dict[tuple, list[dict]] = defaultdict(list)
    for leg in legislators:
        last_keys = _last_name_keys(leg.get("name", {}).get("last", ""))
        if not last_keys:
            continue
        for term in leg.get("terms", []):
            ttype = term.get("type")
            if ttype not in ("rep", "sen"):
                continue
            state = (term.get("state") or "").upper()
            if ttype == "rep":
                district = term.get("district")
                if isinstance(district, str):
                    try:
                        district = int(district)
                    except ValueError:
                        district = None
            else:
                district = None
            record = {"leg": leg, "term": term, "term_end": term.get("end", "")}
            for k in last_keys:
                index[(state, district, k)].append(record)
    return index


def _build_senate_name_index(legislators: Iterable[dict]) -> dict[str, list[dict]]:
    """Fallback index for Senate members whose state_dst is empty.

    The Senate scraper currently does not populate `state_dst`, so the
    primary (state, district, last_key) index never hits. This name-only
    index lets us still recover bioguide_id by chamber + last-name token.
    """
    index: dict[str, list[dict]] = defaultdict(list)
    for leg in legislators:
        last_keys = _last_name_keys(leg.get("name", {}).get("last", ""))
        if not last_keys:
            continue
        for term in leg.get("terms", []):
            if term.get("type") != "sen":
                continue
            record = {"leg": leg, "term": term, "term_end": term.get("end", "")}
            for k in last_keys:
                index[k].append(record)
    return index


def _pick_match(member: Member, candidates: list[dict]) -> tuple[str | None, str]:
    """Return (bioguide_id, reason)."""
    if not candidates:
        return None, "unmatched"

    # Dedupe to one record per legislator (keep latest term).
    by_bio: dict[str, dict] = {}
    for c in candidates:
        bio = (c["leg"].get("id") or {}).get("bioguide")
        if not bio:
            continue
        cur = by_bio.get(bio)
        if cur is None or (c["term_end"] or "") > (cur["term_end"] or ""):
            by_bio[bio] = c
    if not by_bio:
        return None, "unmatched"
    if len(by_bio) == 1:
        return next(iter(by_bio.keys())), "unique"

    # First-name token set for the member ("John N" → {"john", "n"}).
    member_first_norm = _norm_first(member.first_name or "")
    member_first_tokens = {t for t in _TOKEN_SPLIT_RE.split(member_first_norm) if t}
    if not member_first_tokens:
        # Without a first name we can't disambiguate among multiple last-name hits.
        return None, "ambiguous"

    # Rank 1 = any first-name token exactly matches a name variant.
    # Rank 2 = prefix match either direction.
    # Rank 3 = no first-name overlap.
    ranked: list[tuple[int, str, str]] = []  # (rank, bio, term_end)
    for bio, c in by_bio.items():
        variants = _name_variants(c["leg"].get("name", {}))
        if member_first_tokens & variants:
            rank = 1
        elif any(
            tok and v and (tok.startswith(v) or v.startswith(tok))
            for tok in member_first_tokens
            for v in variants
        ):
            rank = 2
        else:
            rank = 3
        ranked.append((rank, bio, c["term_end"] or ""))

    best_rank = min(r for r, _, _ in ranked)
    pool = [(b, end) for r, b, end in ranked if r == best_rank]
    if len(pool) == 1:
        return pool[0][0], f"first_name_rank{best_rank}"
    # Same rank: prefer the most-recent term. Distinguishing tie only.
    pool.sort(key=lambda x: x[1], reverse=True)
    if pool[0][1] and (len(pool) == 1 or pool[0][1] > pool[1][1]):
        return pool[0][0], f"first_name_rank{best_rank}_recent"
    return None, "ambiguous"


def _candidate_diag(candidates: list[dict]) -> list[dict]:
    """Compact form of candidate legislators for the unmatched/ambiguous report."""
    out = []
    seen = set()
    for c in candidates:
        bio = (c["leg"].get("id") or {}).get("bioguide")
        if not bio or bio in seen:
            continue
        seen.add(bio)
        n = c["leg"].get("name", {})
        out.append({
            "bioguide": bio,
            "name": f"{n.get('first', '')} {n.get('last', '')}".strip(),
            "nickname": n.get("nickname"),
            "term_end": c["term_end"],
        })
    return out


def backfill(commit: bool, force: bool, report_path: str | None) -> dict:
    legislators = _load_legislators()
    primary_index = _build_index(legislators)
    senate_name_index = _build_senate_name_index(legislators)

    session = SessionLocal()
    matched: list[dict] = []
    skipped_existing: list[int] = []
    unmatched: list[dict] = []
    ambiguous: list[dict] = []

    try:
        members = session.query(Member).all()
        logging.info(f"{len(members)} members to consider")

        for m in members:
            if m.bioguide_id and not force:
                skipped_existing.append(m.id)
                continue
            state, district = _parse_state_dst(m.chamber, m.state_dst)
            last_keys = _member_last_keys(m)
            row = {
                "member_id": m.id,
                "name": f"{m.first_name} {m.last_name}",
                "chamber": m.chamber,
                "state_dst": m.state_dst,
            }
            if not last_keys:
                unmatched.append({**row, "reason": "missing_last", "candidates": []})
                continue

            candidates: list[dict] = []
            match_path = "primary"
            if state:
                seen_bios = set()
                for k in last_keys:
                    for c in primary_index.get((state, district, k), []):
                        bio = (c["leg"].get("id") or {}).get("bioguide")
                        if bio and bio not in seen_bios:
                            seen_bios.add(bio)
                            candidates.append(c)
            # Senate fallback: Senate scraper doesn't populate state_dst, so when
            # state is empty we match by chamber + last-name token only.
            if not candidates and m.chamber == "senate":
                match_path = "senate_name_only"
                seen_bios = set()
                for k in last_keys:
                    for c in senate_name_index.get(k, []):
                        bio = (c["leg"].get("id") or {}).get("bioguide")
                        if bio and bio not in seen_bios:
                            seen_bios.add(bio)
                            candidates.append(c)

            bio, reason = _pick_match(m, candidates)
            if bio:
                matched.append({**row, "bioguide_id": bio, "reason": f"{match_path}:{reason}"})
                if commit:
                    m.bioguide_id = bio
            elif reason == "ambiguous":
                ambiguous.append({**row, "match_path": match_path,
                                  "candidates": _candidate_diag(candidates)})
            else:
                unmatched.append({**row, "reason": reason, "match_path": match_path,
                                  "candidates": _candidate_diag(candidates)})

        if commit:
            session.commit()
            logging.info(f"Committed {len(matched)} bioguide_id updates")
        else:
            session.rollback()
            logging.info(f"DRY RUN — would update {len(matched)} bioguide_id values")
    finally:
        session.close()

    summary = {
        "total_members": len(matched) + len(unmatched) + len(ambiguous) + len(skipped_existing),
        "matched": len(matched),
        "ambiguous": len(ambiguous),
        "unmatched": len(unmatched),
        "skipped_existing": len(skipped_existing),
        "commit": commit,
        "force": force,
        "matches": matched,
        "ambiguous_rows": ambiguous,
        "unmatched_rows": unmatched,
    }

    if report_path:
        Path(report_path).write_text(json.dumps(summary, indent=2))
        logging.info(f"Report written to {report_path}")

    return summary


def _print_summary(s: dict) -> None:
    print()
    print(f"Backfill summary  (commit={s['commit']}, force={s['force']})")
    print(f"  total members      : {s['total_members']}")
    print(f"  matched            : {s['matched']}")
    print(f"  ambiguous          : {s['ambiguous']}")
    print(f"  unmatched          : {s['unmatched']}")
    print(f"  skipped (existing) : {s['skipped_existing']}")
    if s["ambiguous"]:
        print("\nAmbiguous (need manual resolution):")
        for r in s["ambiguous_rows"]:
            cand_names = ", ".join(f"{c['name']} [{c['bioguide']}]" for c in r["candidates"])
            print(f"  #{r['member_id']:>5}  {r['name']:<30}  {r['chamber']:<6} {r['state_dst']:<5}"
                  f"  candidates: {cand_names}")
    if s["unmatched"]:
        print("\nUnmatched:")
        for r in s["unmatched_rows"]:
            cand_str = ""
            if r.get("candidates"):
                cand_str = "  candidates: " + ", ".join(
                    f"{c['name']} [{c['bioguide']}]" for c in r["candidates"][:5])
            print(f"  #{r['member_id']:>5}  {r['name']:<30}  {r['chamber']:<6} {r['state_dst']:<5}"
                  f"  ({r['reason']}){cand_str}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--commit", action="store_true",
                   help="Persist bioguide_id updates. Default is dry-run.")
    p.add_argument("--force", action="store_true",
                   help="Overwrite bioguide_id even if already set.")
    p.add_argument("--report", help="Write the full match report (incl. ambiguous + unmatched rows) as JSON to this path.")
    args = p.parse_args()

    summary = backfill(commit=args.commit, force=args.force, report_path=args.report)
    _print_summary(summary)

    # Non-zero exit if ambiguous/unmatched exist, so CI/automation can catch it.
    sys.exit(0 if summary["ambiguous"] == 0 and summary["unmatched"] == 0 else 2)


if __name__ == "__main__":
    main()
