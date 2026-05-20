from datetime import date
from typing import Optional, List
from pydantic import BaseModel


class MemberOut(BaseModel):
    id: int
    last_name: str
    first_name: str
    prefix: Optional[str] = None
    suffix: Optional[str] = None
    state_dst: Optional[str] = None
    party: Optional[str] = None
    chamber: Optional[str] = None
    bioguide_id: Optional[str] = None

    model_config = {"from_attributes": True}


class TransactionOut(BaseModel):
    id: int
    transaction_date: date
    notification_date: Optional[date] = None
    filing_date: Optional[date] = None
    ticker: Optional[str] = None
    asset_name: Optional[str] = None
    asset_type: Optional[str] = None
    transaction_type: str
    owner: Optional[str] = None
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    cap_gains: bool = False
    confidence: Optional[str] = None
    member: MemberOut

    model_config = {"from_attributes": True}


class TrendingTicker(BaseModel):
    ticker: str
    trade_count: int
    buy_count: int
    sell_count: int
    member_count: int
    last_traded: date


class PriceBarOut(BaseModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float

    model_config = {"from_attributes": True}


class ScrapeRequest(BaseModel):
    year: int
    max_docs: Optional[int] = None
    chamber: str = "house"  # house | senate


class ScrapeStatus(BaseModel):
    status: str
    year: int
    chamber: str
