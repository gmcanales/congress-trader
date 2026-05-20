from datetime import date, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, case
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.get("/", response_model=List[schemas.TransactionOut])
def list_transactions(
    ticker: Optional[str] = None,
    member_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    transaction_type: Optional[str] = None,
    limit: int = Query(500, le=2000),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    query = db.query(models.Transaction).options(
        joinedload(models.Transaction.member)
    )
    if ticker:
        query = query.filter(models.Transaction.ticker.ilike(ticker.strip()))
    if member_id:
        query = query.filter(models.Transaction.member_id == member_id)
    if start_date:
        query = query.filter(models.Transaction.transaction_date >= start_date)
    if end_date:
        query = query.filter(models.Transaction.transaction_date <= end_date)
    if transaction_type:
        query = query.filter(models.Transaction.transaction_type == transaction_type)
    return (
        query.order_by(models.Transaction.transaction_date.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.get("/trending", response_model=List[schemas.TrendingTicker])
def trending_tickers(
    months: int = Query(6, ge=1, le=24),
    limit: int = Query(30, le=100),
    db: Session = Depends(get_db),
):
    """Most-traded tickers over the last N months, ranked by trade count."""
    cutoff = date.today() - timedelta(days=30 * months)
    is_buy = case((models.Transaction.transaction_type == "purchase", 1), else_=0)
    is_sell = case((models.Transaction.transaction_type.like("sale%"), 1), else_=0)

    rows = (
        db.query(
            models.Transaction.ticker.label("ticker"),
            func.count(models.Transaction.id).label("trade_count"),
            func.sum(is_buy).label("buy_count"),
            func.sum(is_sell).label("sell_count"),
            func.count(func.distinct(models.Transaction.member_id)).label("member_count"),
            func.max(models.Transaction.transaction_date).label("last_traded"),
        )
        .filter(
            models.Transaction.ticker.isnot(None),
            models.Transaction.ticker != "",
            models.Transaction.transaction_date >= cutoff,
        )
        .group_by(models.Transaction.ticker)
        .order_by(func.count(models.Transaction.id).desc())
        .limit(limit)
        .all()
    )
    return [
        schemas.TrendingTicker(
            ticker=r.ticker,
            trade_count=r.trade_count,
            buy_count=int(r.buy_count or 0),
            sell_count=int(r.sell_count or 0),
            member_count=r.member_count,
            last_traded=r.last_traded,
        )
        for r in rows
    ]


@router.get("/tickers")
def list_tickers(db: Session = Depends(get_db)):
    rows = (
        db.query(models.Transaction.ticker)
        .filter(
            models.Transaction.ticker.isnot(None),
            models.Transaction.ticker != "",
        )
        .distinct()
        .order_by(models.Transaction.ticker)
        .all()
    )
    return [r.ticker for r in rows]
