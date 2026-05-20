"""Dataset endpoints: snapshot the ingested data into named, traceable records."""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.services import ptr_parser, ocr

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/datasets", tags=["datasets"])


class DatasetCreate(BaseModel):
    name: str
    description: Optional[str] = None


def _parse_config() -> dict:
    """Snapshot the parsing/aggregation method settings in effect."""
    return {
        "house_parser": "ptr_parser: pdftotext -layout + regex; OCR fallback for scans",
        "min_text_layer_chars": ptr_parser.MIN_TEXT_LAYER_CHARS,
        "ocr_dpi": ocr.OCR_DPI,
        "ocr_tesseract_config": ocr.TESSERACT_CONFIG,
        "senate_parser": "senate_scraper: efdsearch HTML transaction table",
        "scanned_filings": "OCR-attempted; checkbox-grid forms yield little (deferred)",
        "confidence_levels_included": ["high", "medium", "low"],
    }


def _compute_stats(db: Session) -> dict:
    txn = models.Transaction
    total = db.query(func.count(txn.id)).scalar() or 0
    with_ticker = (
        db.query(func.count(txn.id))
        .filter(txn.ticker.isnot(None), txn.ticker != "")
        .scalar() or 0
    )

    by_year = {
        str(int(y)): n for y, n in
        db.query(func.extract("year", txn.filing_date), func.count(txn.id))
        .filter(txn.filing_date.isnot(None))
        .group_by(func.extract("year", txn.filing_date))
        .all()
    }
    by_chamber = {
        c: n for c, n in
        db.query(models.Member.chamber, func.count(txn.id))
        .join(models.Member, models.Member.id == txn.member_id)
        .group_by(models.Member.chamber).all() if c
    }
    by_confidence = {
        c: n for c, n in
        db.query(txn.confidence, func.count(txn.id))
        .group_by(txn.confidence).all() if c
    }
    by_parse_method = {
        m: n for m, n in
        db.query(models.Disclosure.parse_method, func.count(txn.id))
        .join(models.Disclosure, models.Disclosure.id == txn.disclosure_id)
        .group_by(models.Disclosure.parse_method).all() if m
    }
    return {
        "transaction_count": total,
        "ticker_transaction_count": with_ticker,
        "by_year": dict(sorted(by_year.items())),
        "by_chamber": by_chamber,
        "by_confidence": by_confidence,
        "by_parse_method": by_parse_method,
    }


def _serialize(ds: models.Dataset, run_count: int = 0) -> dict:
    return {
        "id": ds.id,
        "name": ds.name,
        "description": ds.description,
        "created_at": ds.created_at.isoformat() if ds.created_at else None,
        "spec": ds.spec,
        "parse_config": ds.parse_config,
        "stats": ds.stats,
        "run_count": run_count,
    }


@router.post("/")
def create_dataset(req: DatasetCreate, db: Session = Depends(get_db)):
    """Finalize the current ingested data as a named, traceable snapshot."""
    stats = _compute_stats(db)
    if not stats["transaction_count"]:
        raise HTTPException(status_code=400, detail="No transactions to snapshot")

    dataset = models.Dataset(
        name=req.name,
        description=req.description,
        spec={
            "years": list(stats["by_year"].keys()),
            "chambers": list(stats["by_chamber"].keys()),
        },
        parse_config=_parse_config(),
        stats=stats,
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    return _serialize(dataset)


@router.get("/")
def list_datasets(db: Session = Depends(get_db)):
    rows = db.query(models.Dataset).order_by(models.Dataset.created_at.desc()).all()
    counts = dict(
        db.query(models.AnalysisRun.dataset_id, func.count(models.AnalysisRun.id))
        .group_by(models.AnalysisRun.dataset_id).all()
    )
    return [_serialize(ds, counts.get(ds.id, 0)) for ds in rows]
