from datetime import date, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas
from app.services.price_service import fetch_and_store_prices

router = APIRouter(prefix="/prices", tags=["prices"])


@router.get("/{ticker}", response_model=List[schemas.PriceBarOut])
def get_prices(
    ticker: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
):
    ticker = ticker.upper()
    start = start_date or (date.today() - timedelta(days=365 * 5))
    end = end_date or date.today()

    bars = (
        db.query(models.PriceBar)
        .filter(
            models.PriceBar.ticker == ticker,
            models.PriceBar.date >= start,
            models.PriceBar.date <= end,
        )
        .order_by(models.PriceBar.date)
        .all()
    )

    if not bars:
        count = fetch_and_store_prices(ticker, start, end)
        if count == 0:
            raise HTTPException(status_code=404, detail=f"No price data found for {ticker}")
        bars = (
            db.query(models.PriceBar)
            .filter(
                models.PriceBar.ticker == ticker,
                models.PriceBar.date >= start,
                models.PriceBar.date <= end,
            )
            .order_by(models.PriceBar.date)
            .all()
        )

    return bars
