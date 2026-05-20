"""
Scrapes Senate Periodic Transaction Reports from the Senate eFD system.

Unlike the House (bulk ZIP + PDFs), the Senate has no bulk download:
  1. GET  /search/home/        -> agreement page, sets a csrftoken cookie
  2. POST /search/home/        -> accept the agreement (prohibition_agreement=1)
  3. POST /search/report/data/ -> paginated JSON search results
  4. GET  /search/view/ptr/{uuid}/ -> an HTML page; electronic PTRs carry a
     clean transaction table. Paper (scanned) filings are skipped.
"""
import logging
import re
import time
from datetime import date, datetime
from typing import Iterator, Optional

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app import models
from app.services.ptr_parser import _parse_amount

logger = logging.getLogger(__name__)

BASE = "https://efdsearch.senate.gov"
HOME_URL = BASE + "/search/home/"
SEARCH_URL = BASE + "/search/"
DATA_URL = BASE + "/search/report/data/"
USER_AGENT = "Mozilla/5.0 (congress-trader data ingest)"

PTR_REPORT_TYPE = "[11]"   # Senate eFD report-type id for Periodic Transaction Reports
PAGE_SIZE = 100
REQUEST_DELAY = 0.6        # seconds between requests, to stay under rate limits

OWNER_MAP = {
    "self": None, "spouse": "SP", "joint": "JT",
    "child": "DC", "dependent child": "DC",
}


def _map_owner(raw: str) -> Optional[str]:
    return OWNER_MAP.get((raw or "").strip().lower(), None)


def _map_type(raw: str) -> str:
    r = (raw or "").strip().lower()
    if "purchas" in r:
        return "purchase"
    if "exchang" in r:
        return "exchange"
    if "partial" in r:
        return "sale_partial"
    if "sale" in r:
        return "sale"
    return "unknown"


def _parse_date(raw: str) -> Optional[date]:
    try:
        return datetime.strptime((raw or "").strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


# ── Session / search ────────────────────────────────────────────────────────

def _authed_client() -> tuple[httpx.Client, str]:
    """Open a session and accept the eFD agreement. Returns (client, csrf token)."""
    client = httpx.Client(timeout=60, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT})
    resp = client.get(HOME_URL)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    token_el = soup.find("input", {"name": "csrfmiddlewaretoken"})
    csrf = token_el["value"] if token_el else client.cookies.get("csrftoken", "")
    client.post(
        HOME_URL,
        data={"csrfmiddlewaretoken": csrf, "prohibition_agreement": "1"},
        headers={"Referer": HOME_URL},
    )
    return client, csrf


def _search_ptrs(client: httpx.Client, csrf: str,
                 start_date: str, end_date: str) -> Iterator[list]:
    """Yield raw search-result rows for PTRs filed in the date range."""
    offset = 0
    while True:
        payload = {
            "csrfmiddlewaretoken": csrf,
            "draw": "1", "start": str(offset), "length": str(PAGE_SIZE),
            "report_types": PTR_REPORT_TYPE, "filer_types": "[]",
            "submitted_start_date": f"{start_date} 00:00:00",
            "submitted_end_date": f"{end_date} 23:59:59",
            "candidate_state": "", "senator_state": "", "office_id": "",
            "first_name": "", "last_name": "",
        }
        resp = client.post(DATA_URL, data=payload, headers={
            "Referer": SEARCH_URL,
            "X-CSRFToken": csrf,
            "X-Requested-With": "XMLHttpRequest",
        })
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("data", [])
        if not rows:
            break
        yield from rows
        offset += PAGE_SIZE
        if offset >= data.get("recordsTotal", 0):
            break
        time.sleep(REQUEST_DELAY)


def _parse_search_row(row: list) -> dict:
    """Extract filer + report-link info from one search-result row."""
    link_html = row[3] if len(row) > 3 else ""
    href_m = re.search(r'href="([^"]+)"', link_html)
    href = href_m.group(1) if href_m else None
    doc_id = href.rstrip("/").rsplit("/", 1)[-1] if href else None
    return {
        "first": (row[0] or "").strip(),
        "last": (row[1] or "").strip(),
        "href": href,
        "doc_id": doc_id,
        "is_paper": bool(href and "/paper/" in href),
        "date_received": _parse_date(row[4]) if len(row) > 4 else None,
    }


# ── PTR HTML parsing ────────────────────────────────────────────────────────

def _parse_ptr_html(html: str) -> list[dict]:
    """Parse the transaction table from an electronic Senate PTR page."""
    soup = BeautifulSoup(html, "html.parser")
    table = next(
        (t for t in soup.find_all("table")
         if "Transaction Date" in t.get_text()),
        None,
    )
    if table is None:
        return []

    transactions = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 8:  # header row or malformed
            continue
        cells = [td.get_text(" ", strip=True) for td in tds]
        # columns: #, TxDate, Owner, Ticker, AssetName, AssetType, Type, Amount, Comment
        tx_date = _parse_date(cells[1])
        if not tx_date:
            continue
        ticker = cells[3].strip()
        ticker = None if ticker in ("--", "", "N/A") else ticker
        amount_min, amount_max = _parse_amount(cells[7])
        transactions.append({
            "transaction_date": tx_date,
            "owner": _map_owner(cells[2]),
            "ticker": ticker,
            "asset_name": cells[4] or None,
            "asset_type": cells[5] or None,
            "transaction_type": _map_type(cells[6]),
            "amount_min": amount_min,
            "amount_max": amount_max,
        })
    return transactions


# ── Storage ─────────────────────────────────────────────────────────────────

def _get_or_create_member(db: Session, last: str, first: str) -> models.Member:
    member = (
        db.query(models.Member)
        .filter(
            models.Member.last_name == last,
            models.Member.first_name == first,
            models.Member.chamber == "senate",
        )
        .first()
    )
    if not member:
        member = models.Member(
            last_name=last, first_name=first, state_dst="", chamber="senate",
        )
        db.add(member)
        db.flush()
    return member


def _store(db: Session, info: dict, transactions: list[dict]) -> int:
    member = _get_or_create_member(db, info["last"], info["first"])

    disclosure = (
        db.query(models.Disclosure)
        .filter(models.Disclosure.doc_id == info["doc_id"])
        .first()
    )
    if not disclosure:
        disclosure = models.Disclosure(
            doc_id=info["doc_id"], member_id=member.id, filing_type="P",
        )
        db.add(disclosure)
    disclosure.year = info["date_received"].year if info["date_received"] else None
    disclosure.filing_date = info["date_received"]
    disclosure.source_url = BASE + info["href"]
    disclosure.parse_method = "digital"
    disclosure.parsed = True
    db.flush()

    count = 0
    for t in transactions:
        db.add(models.Transaction(
            disclosure_id=disclosure.id,
            member_id=member.id,
            transaction_date=t["transaction_date"],
            filing_date=info["date_received"],
            ticker=t["ticker"],
            asset_name=t["asset_name"],
            asset_type=t["asset_type"],
            transaction_type=t["transaction_type"],
            owner=t["owner"],
            amount_min=t["amount_min"],
            amount_max=t["amount_max"],
            confidence="high",
        ))
        count += 1
    db.flush()
    return count


# ── Public API ──────────────────────────────────────────────────────────────

def scrape_senate(year: int, max_docs: Optional[int] = None) -> dict:
    """Scrape Senate PTRs filed within the given year."""
    db = SessionLocal()
    stats = {
        "docs_found": 0, "docs_parsed": 0, "transactions_added": 0,
        "paper_skipped": 0, "errors": 0,
    }
    try:
        client, csrf = _authed_client()
        rows = list(_search_ptrs(client, csrf, f"01/01/{year}", f"12/31/{year}"))
        stats["docs_found"] = len(rows)
        if max_docs:
            rows = rows[:max_docs]
        logger.info(f"{stats['docs_found']} Senate PTRs for {year}; processing {len(rows)}")

        for row in rows:
            info = _parse_search_row(row)
            if not info["doc_id"]:
                continue
            if info["is_paper"]:
                stats["paper_skipped"] += 1
                continue

            done = (
                db.query(models.Disclosure)
                .filter(models.Disclosure.doc_id == info["doc_id"],
                        models.Disclosure.parsed.is_(True))
                .first()
            )
            if done:
                continue

            try:
                resp = client.get(BASE + info["href"])
                resp.raise_for_status()
                transactions = _parse_ptr_html(resp.text)
                n = _store(db, info, transactions)
                stats["docs_parsed"] += 1
                stats["transactions_added"] += n

                if stats["docs_parsed"] % 20 == 0:
                    db.commit()
                    logger.info(
                        f"  {stats['docs_parsed']} docs, "
                        f"{stats['transactions_added']} transactions"
                    )
                time.sleep(REQUEST_DELAY)
            except Exception as exc:
                logger.warning(f"Failed Senate doc {info['doc_id']}: {exc}")
                stats["errors"] += 1
                db.rollback()

        db.commit()
        logger.info(f"Senate scrape complete: {stats}")
    except Exception as exc:
        logger.error(f"Senate scrape failed: {exc}")
        db.rollback()
        raise
    finally:
        client_close = locals().get("client")
        if client_close:
            client_close.close()
        db.close()
    return stats
