"""User portfolio endpoints.

  GET    /api/holdings/         → list of UserHolding rows, each enriched
                                  with the latest available close, current
                                  value, and unrealized P/L.
  POST   /api/holdings/         → create one holding
  PATCH  /api/holdings/{id}     → partial update
  DELETE /api/holdings/{id}     → delete one holding
  GET    /api/holdings/nav      → portfolio NAV time series
  POST   /api/holdings/upload   → bulk CSV upload

Every endpoint that touches user data is scoped by the verified CF Access
JWT email (`current_user_email` dependency in app/auth.py). PATCH and
DELETE return 404 for rows owned by another user — no info-leak on
ownership.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.auth import current_user_email
from app.database import get_db, SessionLocal
from app import models
from app.services.backtest import ensure_prices, _close_series


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/holdings", tags=["holdings"])


class HoldingIn(BaseModel):
    ticker: str
    shares: float
    cost_basis: float
    buy_date: date
    notes: Optional[str] = None
    target_exit_price: Optional[float] = None
    target_exit_date: Optional[date] = None


class HoldingPatch(BaseModel):
    ticker: Optional[str] = None
    shares: Optional[float] = None
    cost_basis: Optional[float] = None
    buy_date: Optional[date] = None
    notes: Optional[str] = None
    target_exit_price: Optional[float] = None
    target_exit_date: Optional[date] = None


def _latest_close(db: Session, ticker: str) -> tuple[Optional[float], Optional[date]]:
    row = (
        db.query(models.PriceBar.close, models.PriceBar.date)
        .filter(models.PriceBar.ticker == ticker)
        .order_by(desc(models.PriceBar.date))
        .first()
    )
    if not row:
        return None, None
    return float(row[0]) if row[0] is not None else None, row[1]


def _serialize(h: models.UserHolding, db: Session) -> dict:
    current_price, price_date = _latest_close(db, h.ticker)
    cost_total = h.cost_basis * h.shares if h.cost_basis is not None else None
    current_value = current_price * h.shares if current_price is not None else None
    unrealized = (current_value - cost_total) if (
        current_value is not None and cost_total is not None
    ) else None
    unrealized_pct = (
        (current_price / h.cost_basis - 1)
        if current_price is not None and h.cost_basis
        else None
    )
    return {
        "id": h.id,
        "ticker": h.ticker,
        "shares": h.shares,
        "cost_basis": h.cost_basis,
        "buy_date": h.buy_date.isoformat() if h.buy_date else None,
        "notes": h.notes,
        "target_exit_price": h.target_exit_price,
        "target_exit_date": h.target_exit_date.isoformat() if h.target_exit_date else None,
        "created_at": h.created_at.isoformat() if h.created_at else None,
        "updated_at": h.updated_at.isoformat() if h.updated_at else None,
        # Derived, for the UI:
        "current_price": current_price,
        "current_price_date": price_date.isoformat() if price_date else None,
        "cost_total": cost_total,
        "current_value": current_value,
        "unrealized_pl": unrealized,
        "unrealized_pl_pct": unrealized_pct,
        "days_held": (date.today() - h.buy_date).days if h.buy_date else None,
    }


def _owned_holding(db: Session, holding_id: int, email: str) -> models.UserHolding:
    """Fetch a holding only if the caller owns it. 404 otherwise so that
    'not yours' and 'not found' are indistinguishable to the caller."""
    h = (
        db.query(models.UserHolding)
        .filter(models.UserHolding.id == holding_id,
                models.UserHolding.user_email == email)
        .first()
    )
    if not h:
        raise HTTPException(status_code=404, detail="holding not found")
    return h


@router.get("/")
def list_holdings(
    db: Session = Depends(get_db),
    email: str = Depends(current_user_email),
):
    rows = (
        db.query(models.UserHolding)
        .filter(models.UserHolding.user_email == email)
        .order_by(models.UserHolding.buy_date.desc())
        .all()
    )
    return [_serialize(h, db) for h in rows]


@router.post("/")
def create_holding(
    payload: HoldingIn,
    db: Session = Depends(get_db),
    email: str = Depends(current_user_email),
):
    if payload.shares <= 0:
        raise HTTPException(status_code=400, detail="shares must be > 0")
    if payload.cost_basis < 0:
        raise HTTPException(status_code=400, detail="cost_basis must be ≥ 0")
    h = models.UserHolding(
        user_email=email,
        ticker=payload.ticker.upper().strip(),
        shares=payload.shares,
        cost_basis=payload.cost_basis,
        buy_date=payload.buy_date,
        notes=payload.notes,
        target_exit_price=payload.target_exit_price,
        target_exit_date=payload.target_exit_date,
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return _serialize(h, db)


@router.patch("/{holding_id}")
def patch_holding(
    holding_id: int,
    patch: HoldingPatch,
    db: Session = Depends(get_db),
    email: str = Depends(current_user_email),
):
    h = _owned_holding(db, holding_id, email)
    data = patch.model_dump(exclude_unset=True)
    if "ticker" in data and data["ticker"] is not None:
        data["ticker"] = data["ticker"].upper().strip()
    for k, v in data.items():
        setattr(h, k, v)
    h.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(h)
    return _serialize(h, db)


@router.delete("/{holding_id}")
def delete_holding(
    holding_id: int,
    db: Session = Depends(get_db),
    email: str = Depends(current_user_email),
):
    h = _owned_holding(db, holding_id, email)
    db.delete(h)
    db.commit()
    return {"deleted": holding_id}


# ── Portfolio NAV time series (for chart overlay) ──────────────────────────

@router.get("/nav")
def portfolio_nav(
    start: date = Query(..., description="Series start (YYYY-MM-DD)"),
    end: date = Query(..., description="Series end (YYYY-MM-DD)"),
    email: str = Depends(current_user_email),
):
    """Daily NAV of the user's portfolio over [start, end]. NAV(t) is
    `market_value(t) / cumulative_invested(t)` where:

      cumulative_invested(t) = Σ shares × cost_basis for holdings whose
                               buy_date ≤ t
      market_value(t)        = Σ shares × close[ticker, t] for same

    NAV(t) = 1.0 on the very first day any holding has been bought (cost
    basis = market value at that instant — although in practice the
    market value uses that day's close, so it can already drift on day 0).
    Returns an empty series if there are no holdings.
    """
    db = SessionLocal()
    try:
        holdings = (
            db.query(models.UserHolding)
            .filter(models.UserHolding.user_email == email)
            .all()
        )
        if not holdings:
            return {"start": start.isoformat(), "end": end.isoformat(),
                    "series": [], "note": "no holdings"}

        tickers = sorted({h.ticker for h in holdings})
        # Need SPY for a stable trading-day calendar and to mirror what the
        # strategy chart uses.
        ensure_prices(set(tickers) | {"SPY"},
                      min(start, min(h.buy_date for h in holdings)) - timedelta(days=7),
                      end + timedelta(days=2))

        cal_series = _close_series(db, "SPY")
        if cal_series is None:
            raise HTTPException(status_code=503, detail="no SPY prices available")
        cal = cal_series[(cal_series.index >= pd.Timestamp(start)) &
                          (cal_series.index <= pd.Timestamp(end))].index

        # Per-ticker close series, reindexed to the trading calendar and ffilled.
        close_by_ticker: dict[str, pd.Series] = {}
        for t in tickers:
            ser = _close_series(db, t)
            if ser is None:
                close_by_ticker[t] = pd.Series(index=cal, dtype=float)
            else:
                close_by_ticker[t] = ser.reindex(cal, method="ffill")

        series = []
        for ts in cal:
            d = ts.date()
            invested = 0.0
            mv = 0.0
            n_active = 0
            for h in holdings:
                if h.buy_date > d:
                    continue
                shares = float(h.shares or 0)
                cb = float(h.cost_basis or 0)
                invested += shares * cb
                close = close_by_ticker.get(h.ticker)
                px = None
                if close is not None and ts in close.index:
                    val = close.loc[ts]
                    if pd.notna(val):
                        px = float(val)
                if px is None:
                    # Fall back to cost basis if we have no price (treat
                    # the position as flat that day — keeps NAV continuous).
                    px = cb
                mv += shares * px
                n_active += 1
            if invested <= 0 or n_active == 0:
                continue
            series.append({
                "date": d.isoformat(),
                "nav": round(mv / invested, 6),
                "invested": round(invested, 2),
                "market_value": round(mv, 2),
                "n_active": n_active,
            })

        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "n_holdings": len(holdings),
            "n_tickers": len(tickers),
            "series": series,
        }
    finally:
        db.close()


# ── Bulk CSV upload ────────────────────────────────────────────────────────

CSV_REQUIRED = ("ticker", "shares", "cost_basis", "buy_date")
CSV_OPTIONAL = ("notes", "target_exit_price", "target_exit_date")
CSV_SAMPLE = (
    "ticker,shares,cost_basis,buy_date,notes,target_exit_price,target_exit_date\n"
    "AAPL,10,175.50,2025-01-15,long-term core,,\n"
    "NVDA,5,120.00,2025-03-22,trim above target,200,2026-12-31\n"
)


@router.get("/csv/sample")
def csv_sample():
    """Return a template CSV the user can save, fill in, and re-upload."""
    return {"filename": "portfolio_template.csv", "content": CSV_SAMPLE}


def _parse_optional_float(v: str | None) -> Optional[float]:
    if v is None: return None
    s = str(v).strip()
    if s == "": return None
    return float(s)


def _parse_optional_date(v: str | None) -> Optional[date]:
    if v is None: return None
    s = str(v).strip()
    if s == "": return None
    return date.fromisoformat(s)


@router.post("/upload")
async def upload_csv(
    file: UploadFile = File(..., description="CSV with header row"),
    mode: str = Query("append",
        description="`append` (default) inserts every row as a new holding. "
                    "`replace_by_ticker` deletes any existing holding with the "
                    "same ticker first."),
    db: Session = Depends(get_db),
    email: str = Depends(current_user_email),
):
    """Bulk-create holdings from a CSV. Returns per-row outcome so the user
    can tell which rows failed validation. Does NOT abort the whole upload
    on a bad row — well-formed rows are still inserted."""
    if mode not in ("append", "replace_by_ticker"):
        raise HTTPException(status_code=400, detail="mode must be append or replace_by_ticker")

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="file must be UTF-8 text")

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise HTTPException(status_code=400, detail="CSV is empty")
    missing = [c for c in CSV_REQUIRED if c not in reader.fieldnames]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"missing required columns: {missing}. "
                   f"Required: {list(CSV_REQUIRED)}. Optional: {list(CSV_OPTIONAL)}",
        )

    results: list[dict] = []
    inserted = 0
    failed = 0

    for i, row in enumerate(reader, start=2):  # 1 is header, 2 is first data row
        try:
            ticker = (row.get("ticker") or "").strip().upper()
            if not ticker:
                raise ValueError("ticker is empty")
            shares = float(row.get("shares") or 0)
            if shares <= 0:
                raise ValueError("shares must be > 0")
            cost_basis = float(row.get("cost_basis") or 0)
            if cost_basis < 0:
                raise ValueError("cost_basis must be ≥ 0")
            buy_date = date.fromisoformat((row.get("buy_date") or "").strip())
            notes = (row.get("notes") or "").strip() or None
            tgt_px = _parse_optional_float(row.get("target_exit_price"))
            tgt_date = _parse_optional_date(row.get("target_exit_date"))

            if mode == "replace_by_ticker":
                (db.query(models.UserHolding)
                    .filter(models.UserHolding.ticker == ticker,
                            models.UserHolding.user_email == email)
                    .delete(synchronize_session=False))

            h = models.UserHolding(
                user_email=email,
                ticker=ticker, shares=shares, cost_basis=cost_basis,
                buy_date=buy_date, notes=notes,
                target_exit_price=tgt_px, target_exit_date=tgt_date,
            )
            db.add(h)
            db.flush()
            inserted += 1
            results.append({"row": i, "ok": True, "id": h.id, "ticker": ticker})
        except Exception as exc:
            failed += 1
            results.append({"row": i, "ok": False, "error": str(exc)})

    db.commit()
    return {
        "inserted": inserted,
        "failed": failed,
        "results": results,
    }
