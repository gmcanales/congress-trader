"""
Ingest OGE Form 278-T (Periodic Transaction Report) filings for executive
branch / PAS officials. Two-phase tool:

  Phase 1 — INDEX:  drive the OGE PAS Filings-by-Date view via Playwright,
            collect every filing's metadata + document URL, save to JSON.
            Filters to entries with type='278 Transaction' (per-trade PTRs).

  Phase 2 — INGEST: download each 278-T PDF, parse with ptr_parser
            (auto-dispatches to parse_278t_text() for OGE forms), upsert
            into members/disclosures/transactions tables. Each filer gets
            a Member row with filer_type='executive' and a Position row
            with role_type='executive_cabinet' covering their filing
            window — so the existing engine's leadership_only filter
            picks them up alongside congressional leadership.

The PAS Filings-by-Date view is a Lotus Notes (XPages) DataTables UI;
the rows come from an XPages REST endpoint at /API.xsp/v2/rest. Bare
curl can't hit it (needs session cookies), so we drive it through a
real browser via Playwright.

Run:

  docker compose exec backend python ingest_oge.py index
  docker compose exec backend python ingest_oge.py ingest

  # Both phases:
  docker compose exec backend python ingest_oge.py all
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app import models
from app.services import ptr_parser
from app.services.ocr import ocr_pdf

logger = logging.getLogger(__name__)

PAS_FILINGS_URL = "https://extapps2.oge.gov/201/Presiden.nsf/PAS%20Filings%20by%20Date?OpenView"
INDEX_JSON = Path("/tmp/oge_278t_filings.json")
USER_AGENT = "Mozilla/5.0 (congress-trader v2.5 OGE-278T ingest)"

# Document type label that means "Periodic Transaction Report"
PTR_TYPE_LABEL = "278 Transaction"


# ── Phase 1: index scrape ───────────────────────────────────────────────────

def build_index(output: Path = INDEX_JSON, page_size: int = 100) -> dict:
    """Drive Playwright through the OGE PAS Filings-by-Date view, paging
    via the DataTables API, and persist every PTR filing's metadata.
    Returns a summary dict."""
    from playwright.sync_api import sync_playwright

    captured: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=USER_AGENT)

        # Intercept the DataTables AJAX response on every page advance.
        responses: list[dict] = []
        def _on_response(resp):
            if "/API.xsp/v2/rest" in resp.url and resp.status == 200:
                try:
                    responses.append(resp.json())
                except Exception:
                    pass
        page.on("response", _on_response)

        logger.info(f"Loading {PAS_FILINGS_URL}")
        page.goto(PAS_FILINGS_URL, wait_until="networkidle", timeout=90000)
        time.sleep(2)

        if not responses:
            browser.close()
            raise RuntimeError("OGE landing page did not return DataTables JSON")
        init = responses[0]
        total = init.get("recordsTotal", 0)
        all_rows = list(init.get("data", []))
        logger.info(f"recordsTotal={total}; fetched first {len(all_rows)} on landing")

        # Advance DataTables one page at a time, collecting the AJAX response.
        for start in range(page_size, total, page_size):
            responses.clear()
            js = (
                f"new Promise((resolve) => {{"
                f"  $('table.dataTable').first().DataTable()"
                f"   .page.len({page_size}).page({start // page_size}).draw('page');"
                f"  $(document).one('xhr.dt', () => resolve(true));"
                f"  setTimeout(() => resolve(false), 8000);"
                f"}})"
            )
            try:
                page.evaluate(js)
                time.sleep(0.4)
            except Exception as exc:
                logger.warning(f"page advance failed at start={start}: {exc}")
                continue
            if responses:
                all_rows.extend(responses[-1].get("data", []))
            if (start // page_size) % 20 == 0:
                logger.info(f"  start={start}: {len(all_rows)}/{total}")

        browser.close()

    logger.info(f"Index scrape complete: {len(all_rows)} of {total} rows")

    # Filter to 278 Transactions with an extractable doc_url.
    type_a_re = re.compile(r"<a[^>]+href=['\"]([^'\"]+)['\"][^>]*>([^<]+)</a>")
    ptr_rows = []
    for r in all_rows:
        type_html = r.get("type") or ""
        m = type_a_re.search(type_html)
        if not m:
            continue
        url, label = m.group(1), m.group(2)
        if PTR_TYPE_LABEL not in label and "Periodic" not in label:
            continue
        ptr_rows.append({
            "doc_url": url,
            "doc_label": label,
            "name": r.get("name") or "",
            "agency": r.get("agency") or "",
            "title": r.get("title") or "",
            "level": r.get("level") or "",
            "docDate": r.get("docDate") or "",
            "amended": r.get("amended") or "",
        })

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(ptr_rows))
    logger.info(f"Saved {len(ptr_rows)} 278-T filing pointers to {output}")
    return {"total_in_index": total, "ptr_filings": len(ptr_rows)}


# ── Phase 2: per-PDF ingest ─────────────────────────────────────────────────

NAME_RE = re.compile(r"^([^,]+),\s*(.+)$")  # "Last, First Middle"


def _split_name(full: str) -> tuple[str, str]:
    """Split 'Last, First Middle' to (first, last). Falls back to (full, '')."""
    m = NAME_RE.match(full.strip())
    if not m:
        return full.strip(), ""
    last = m.group(1).strip()
    first = m.group(2).strip()
    return first, last


def _doc_id_from_url(url: str) -> str:
    """The NOID hash inside the URL is unique per filing. Use it as doc_id."""
    m = re.search(r"/PAS\+Index/([0-9A-F]{20,40})/\$FILE/", url)
    return m.group(1) if m else url[-40:]


def _upsert_member(db: Session, name: str, agency: str, title: str) -> models.Member:
    first, last = _split_name(name)
    # filer_type='executive' rows live in the same `members` table — joins to
    # transactions / positions stay unchanged. state_dst NULL distinguishes
    # from congressional rows which all have state_dst populated (House) or
    # have empty-string state_dst for Senate. Use position_title to carry
    # the cabinet role since chamber/state_dst don't apply.
    member = (
        db.query(models.Member)
        .filter(
            models.Member.last_name == last,
            models.Member.first_name == first,
            models.Member.filer_type == "executive",
        )
        .first()
    )
    if not member:
        member = models.Member(
            last_name=last, first_name=first,
            chamber=None, state_dst=None,
            party=None,
            filer_type="executive",
            position_title=title or None,
        )
        db.add(member)
        db.flush()
    elif title and not member.position_title:
        member.position_title = title
    return member


def _upsert_position(db: Session, member: models.Member,
                     filing_dates: list[date]) -> Optional[models.Position]:
    """Create one Position row per executive filer covering their filing
    window. role_type='executive_cabinet' so the engine's leadership_only
    filter picks them up (LEADERSHIP_ROLE_TYPES includes this value)."""
    if not filing_dates:
        return None
    start = min(filing_dates)
    end = max(filing_dates)
    existing = (
        db.query(models.Position)
        .filter(
            models.Position.member_id == member.id,
            models.Position.role_type == "executive_cabinet",
        )
        .first()
    )
    if existing:
        # Expand the window if new filings extend it.
        if start < existing.start_date:
            existing.start_date = start
        if existing.end_date is None or end > existing.end_date:
            existing.end_date = end
        return existing
    pos = models.Position(
        member_id=member.id,
        role_type="executive_cabinet",
        title=member.position_title or "executive",
        committee_id=None,
        congress=None,
        start_date=start,
        end_date=end,
        source="OGE PAS Filings-by-Date view 2026-05-23",
    )
    db.add(pos)
    db.flush()
    return pos


def _process_one_pdf(client: httpx.Client, pdf_url: str) -> ptr_parser.ParsedPTR:
    """Download a PTR PDF and parse it (digital first, OCR fallback)."""
    resp = client.get(pdf_url)
    resp.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(resp.content)
        tmp_path = tmp.name
    try:
        text = ptr_parser.extract_text_digital(tmp_path)
        if ptr_parser.has_text_layer(text):
            return ptr_parser.parse_ptr_text(text, source="digital")
        ocr_text = ocr_pdf(tmp_path)
        if ocr_text.strip():
            return ptr_parser.parse_ptr_text(ocr_text, source="ocr")
        return ptr_parser.ParsedPTR(parse_method="failed")
    finally:
        os.unlink(tmp_path)


def ingest_from_index(index_path: Path = INDEX_JSON,
                      max_filings: Optional[int] = None) -> dict:
    """Read the saved 278-T filing pointers and ingest each PDF."""
    if not index_path.exists():
        raise FileNotFoundError(f"{index_path} missing — run `index` phase first")
    ptr_rows = json.loads(index_path.read_text())
    if max_filings:
        ptr_rows = ptr_rows[:max_filings]
    logger.info(f"Ingesting {len(ptr_rows)} 278-T filings from {index_path}")

    # Group by filer first so we can set Position windows correctly.
    by_filer: dict[str, list[dict]] = {}
    for r in ptr_rows:
        by_filer.setdefault(r["name"], []).append(r)

    db = SessionLocal()
    stats = {
        "filers_seen": len(by_filer),
        "filings_total": len(ptr_rows),
        "filings_ingested": 0,
        "filings_skipped_done": 0,
        "filings_failed": 0,
        "transactions_added": 0,
    }

    with httpx.Client(timeout=120, follow_redirects=True,
                      headers={"User-Agent": USER_AGENT}) as client:
        for name, filings in by_filer.items():
            agency = filings[0]["agency"]
            title = filings[0]["title"]
            member = _upsert_member(db, name, agency, title)

            filing_dates: list[date] = []
            for f in filings:
                doc_id = _doc_id_from_url(f["doc_url"])
                done = (
                    db.query(models.Disclosure)
                    .filter(models.Disclosure.doc_id == doc_id,
                            models.Disclosure.parsed.is_(True))
                    .first()
                )
                if done:
                    stats["filings_skipped_done"] += 1
                    if done.filing_date:
                        filing_dates.append(done.filing_date)
                    continue

                try:
                    parsed = _process_one_pdf(client, f["doc_url"])
                except Exception as exc:
                    logger.warning(f"download/parse failed for {name} {doc_id}: {exc}")
                    stats["filings_failed"] += 1
                    continue

                filing_date = None
                if f["docDate"]:
                    try:
                        filing_date = datetime.fromisoformat(
                            f["docDate"].replace("Z", "+00:00")
                        ).date()
                    except Exception:
                        pass

                disclosure = db.query(models.Disclosure).filter(
                    models.Disclosure.doc_id == doc_id
                ).first()
                if not disclosure:
                    disclosure = models.Disclosure(
                        doc_id=doc_id, member_id=member.id, filing_type="P",
                    )
                    db.add(disclosure)
                disclosure.filing_date = filing_date
                disclosure.source_url = f["doc_url"]
                disclosure.parse_method = parsed.parse_method
                disclosure.parsed = True
                # Approximate the filing year from docDate; tighter is
                # available from the PDF itself but the index date is enough.
                if filing_date:
                    disclosure.year = filing_date.year
                db.flush()

                n_tx = 0
                for tx in parsed.transactions:
                    if tx.transaction_date is None:
                        continue
                    db.add(models.Transaction(
                        disclosure_id=disclosure.id,
                        member_id=member.id,
                        transaction_date=tx.transaction_date,
                        notification_date=tx.notification_date,  # None for 278-T
                        filing_date=filing_date,
                        ticker=tx.ticker,  # None for 278-T; resolved downstream
                        asset_name=tx.asset_name,
                        asset_type=tx.asset_type,
                        transaction_type=tx.transaction_type,
                        owner=tx.owner,  # None for 278-T
                        amount_min=tx.amount_min,
                        amount_max=tx.amount_max,
                        confidence=tx.confidence,
                        raw_text=tx.raw_text,
                    ))
                    n_tx += 1
                db.flush()

                stats["filings_ingested"] += 1
                stats["transactions_added"] += n_tx
                if filing_date:
                    filing_dates.append(filing_date)

                if stats["filings_ingested"] % 10 == 0:
                    db.commit()
                    logger.info(
                        f"  {stats['filings_ingested']}/{stats['filings_total']} "
                        f"filings, {stats['transactions_added']} transactions"
                    )

            _upsert_position(db, member, filing_dates)

        db.commit()

    db.close()
    logger.info(f"Ingest complete: {stats}")
    return stats


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("phase", choices=["index", "ingest", "all"],
                   help="`index` scrape only, `ingest` from existing JSON, `all` runs both")
    p.add_argument("--max-filings", type=int, default=None,
                   help="Cap on number of PTR filings to ingest (smoke-test friendly)")
    p.add_argument("--index-path", type=Path, default=INDEX_JSON)
    args = p.parse_args()

    if args.phase in ("index", "all"):
        build_index(output=args.index_path)
    if args.phase in ("ingest", "all"):
        ingest_from_index(index_path=args.index_path, max_filings=args.max_filings)


if __name__ == "__main__":
    main()
