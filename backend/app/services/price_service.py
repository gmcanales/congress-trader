import logging
from datetime import date
from typing import Optional

import pandas as pd
import yfinance as yf
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import SessionLocal
from app import models

logger = logging.getLogger(__name__)


def fetch_and_store_prices(ticker: str, start: date, end: date) -> int:
    """Fetch OHLCV data from Yahoo Finance and upsert into price_bars. Returns row count."""
    db = SessionLocal()
    try:
        logger.info(f"Fetching prices for {ticker} ({start} → {end})")
        data: pd.DataFrame = yf.download(
            ticker,
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=True,
            progress=False,
        )

        if data.empty:
            logger.warning(f"No price data returned for {ticker}")
            return 0

        # yfinance may return MultiIndex columns for some versions
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        rows = []
        for ts, row in data.iterrows():
            # Skip rows with NaN OHLCV
            if pd.isna(row["Close"]):
                continue
            rows.append(
                {
                    "ticker": ticker,
                    "date": ts.date(),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": float(row["Volume"]),
                }
            )

        if rows:
            stmt = pg_insert(models.PriceBar.__table__).values(rows)
            stmt = stmt.on_conflict_do_nothing(index_elements=["ticker", "date"])
            db.execute(stmt)
            db.commit()

        logger.info(f"Stored {len(rows)} price bars for {ticker}")
        return len(rows)

    except Exception as exc:
        logger.error(f"Price fetch failed for {ticker}: {exc}")
        db.rollback()
        return 0
    finally:
        db.close()
