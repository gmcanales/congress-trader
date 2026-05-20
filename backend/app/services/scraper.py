"""
Scrapes House Periodic Transaction Reports (PTRs) from the House Clerk.

  Index ZIP:  https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip
              -> contains {year}FD.txt, a tab-delimited index of every filing.
  PTR PDF:    https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{DocID}.pdf

Individual PTRs exist only as PDFs. Digital eFD filings have a text layer and
are parsed directly; scanned paper filings fall back to OCR. See ptr_parser.py.
"""
import io
import logging
import os
import tempfile
import zipfile
from datetime import date, datetime
from typing import Optional

import httpx
import pandas as pd
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app import models
from app.services import ptr_parser
from app.services.ocr import ocr_pdf

logger = logging.getLogger(__name__)

INDEX_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
PTR_PDF_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"
USER_AGENT = "Mozilla/5.0 (congress-trader data ingest)"


def _parse_date(raw: str) -> Optional[date]:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime((raw or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


def _get_or_create_member(
    db: Session, last: str, first: str, prefix: str, suffix: str, state_dst: str
) -> models.Member:
    member = (
        db.query(models.Member)
        .filter(
            models.Member.last_name == last,
            models.Member.first_name == first,
            models.Member.state_dst == state_dst,
        )
        .first()
    )
    if not member:
        member = models.Member(
            last_name=last, first_name=first,
            prefix=prefix, suffix=suffix, state_dst=state_dst,
            chamber="house",
        )
        db.add(member)
        db.flush()
    return member


def _download_index(year: int) -> pd.DataFrame:
    """Download and parse the year's filing index, returning only PTR rows."""
    url = INDEX_URL.format(year=year)
    logger.info(f"Downloading index: {url}")
    with httpx.Client(timeout=120, follow_redirects=True,
                      headers={"User-Agent": USER_AGENT}) as client:
        resp = client.get(url)
        resp.raise_for_status()

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    txt_name = next((n for n in zf.namelist() if n.lower().endswith(".txt")), None)
    if not txt_name:
        raise RuntimeError("No .txt index found in ZIP")

    raw = zf.read(txt_name).decode("utf-8", errors="replace")
    df = pd.read_csv(io.StringIO(raw), sep="\t", dtype=str).fillna("")
    df.columns = [c.strip() for c in df.columns]
    return df[df["FilingType"].str.strip() == "P"].reset_index(drop=True)


def _process_ptr_pdf(pdf_bytes: bytes) -> ptr_parser.ParsedPTR:
    """Extract text (digital, OCR fallback) and parse a single PTR PDF."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name
    try:
        text = ptr_parser.extract_text_digital(tmp_path)
        if ptr_parser.has_text_layer(text):
            return ptr_parser.parse_ptr_text(text, source="digital")
        # No usable text layer -> scanned filing, OCR it
        logger.info("No text layer; falling back to OCR")
        ocr_text = ocr_pdf(tmp_path)
        if ocr_text.strip():
            return ptr_parser.parse_ptr_text(ocr_text, source="ocr")
        return ptr_parser.ParsedPTR(parse_method="failed")
    finally:
        os.unlink(tmp_path)


def _store_ptr(
    db: Session, parsed: ptr_parser.ParsedPTR, doc_id: str,
    year: int, source_url: str, index_row: pd.Series,
) -> int:
    member = _get_or_create_member(
        db,
        last=str(index_row.get("Last", "")).strip(),
        first=str(index_row.get("First", "")).strip(),
        prefix=str(index_row.get("Prefix", "")).strip(),
        suffix=str(index_row.get("Suffix", "")).strip(),
        state_dst=str(index_row.get("StateDst", "")).strip(),
    )

    disclosure = db.query(models.Disclosure).filter(models.Disclosure.doc_id == doc_id).first()
    if not disclosure:
        disclosure = models.Disclosure(doc_id=doc_id, member_id=member.id, filing_type="P")
        db.add(disclosure)
    disclosure.year = year
    disclosure.filing_date = _parse_date(str(index_row.get("FilingDate", "")))
    disclosure.source_url = source_url
    disclosure.parse_method = parsed.parse_method
    disclosure.parsed = True
    db.flush()

    count = 0
    for tx in parsed.transactions:
        if tx.transaction_date is None:
            continue
        db.add(models.Transaction(
            disclosure_id=disclosure.id,
            member_id=member.id,
            transaction_date=tx.transaction_date,
            notification_date=tx.notification_date,
            filing_date=disclosure.filing_date,
            ticker=tx.ticker,
            asset_name=tx.asset_name,
            asset_type=tx.asset_type,
            transaction_type=tx.transaction_type,
            owner=tx.owner,
            amount_min=tx.amount_min,
            amount_max=tx.amount_max,
            confidence=tx.confidence,
            raw_text=tx.raw_text,
        ))
        count += 1
    db.flush()
    return count


def scrape_year(year: int, max_docs: Optional[int] = None) -> dict:
    """Download and parse every PTR filing for the given year."""
    db = SessionLocal()
    stats = {
        "docs_found": 0, "docs_parsed": 0, "transactions_added": 0,
        "digital": 0, "ocr": 0, "failed": 0, "errors": 0,
    }
    try:
        df = _download_index(year)
        stats["docs_found"] = len(df)
        if max_docs:
            df = df.head(max_docs)
        logger.info(f"{stats['docs_found']} PTR filings for {year}; processing {len(df)}")

        with httpx.Client(timeout=60, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT}) as client:
            for _, row in df.iterrows():
                doc_id = str(row.get("DocID", "")).strip()
                if not doc_id:
                    continue

                done = (
                    db.query(models.Disclosure)
                    .filter(models.Disclosure.doc_id == doc_id,
                            models.Disclosure.parsed.is_(True))
                    .first()
                )
                if done:
                    continue

                pdf_url = PTR_PDF_URL.format(year=year, doc_id=doc_id)
                try:
                    resp = client.get(pdf_url)
                    if resp.status_code == 404:
                        stats["failed"] += 1
                        continue
                    resp.raise_for_status()

                    parsed = _process_ptr_pdf(resp.content)
                    n = _store_ptr(db, parsed, doc_id, year, pdf_url, row)

                    stats["docs_parsed"] += 1
                    stats["transactions_added"] += n
                    stats[parsed.parse_method] = stats.get(parsed.parse_method, 0) + 1

                    if stats["docs_parsed"] % 25 == 0:
                        db.commit()
                        logger.info(
                            f"  {stats['docs_parsed']}/{len(df)} docs, "
                            f"{stats['transactions_added']} transactions"
                        )
                except Exception as exc:
                    logger.warning(f"Failed doc {doc_id}: {exc}")
                    stats["errors"] += 1
                    db.rollback()

        db.commit()
        logger.info(f"Scrape complete: {stats}")
    except Exception as exc:
        logger.error(f"Scrape failed: {exc}")
        db.rollback()
        raise
    finally:
        db.close()
    return stats
