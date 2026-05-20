from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Date, DateTime,
    Float, Boolean, ForeignKey, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from app.database import Base


class Member(Base):
    __tablename__ = "members"

    id = Column(Integer, primary_key=True)
    last_name = Column(String, nullable=False)
    first_name = Column(String, nullable=False)
    prefix = Column(String)
    suffix = Column(String)
    state_dst = Column(String)  # House: "CA12" district; Senate: "CA" state
    party = Column(String)
    chamber = Column(String)  # house | senate

    __table_args__ = (
        UniqueConstraint("last_name", "first_name", "state_dst", name="uq_member"),
    )

    disclosures = relationship("Disclosure", back_populates="member")
    transactions = relationship("Transaction", back_populates="member")


class Disclosure(Base):
    __tablename__ = "disclosures"

    id = Column(Integer, primary_key=True)
    doc_id = Column(String, unique=True, nullable=False)
    member_id = Column(Integer, ForeignKey("members.id"))
    filing_type = Column(String)
    year = Column(Integer)
    filing_date = Column(Date)
    source_url = Column(String)
    parsed = Column(Boolean, default=False)
    parse_method = Column(String)  # digital | ocr | failed

    member = relationship("Member", back_populates="disclosures")
    transactions = relationship("Transaction", back_populates="disclosure")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True)
    disclosure_id = Column(Integer, ForeignKey("disclosures.id"))
    member_id = Column(Integer, ForeignKey("members.id"))
    transaction_date = Column(Date, nullable=False)
    notification_date = Column(Date)
    filing_date = Column(Date)  # date the report was filed / became public
    ticker = Column(String)
    asset_name = Column(String)
    asset_type = Column(String)
    transaction_type = Column(String)
    owner = Column(String)
    amount_min = Column(Float)
    amount_max = Column(Float)
    cap_gains = Column(Boolean, default=False)
    confidence = Column(String, default="high")  # high | medium | low
    raw_text = Column(String)

    member = relationship("Member", back_populates="transactions")
    disclosure = relationship("Disclosure", back_populates="transactions")


class Dataset(Base):
    """A named, finalized snapshot of the ingested data — for traceability.

    It records *what* data was finalized (spec), *how* it was parsed
    (parse_config), and computed counts/breakdowns (stats) at snapshot time.
    """
    __tablename__ = "datasets"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    description = Column(String)
    spec = Column(JSONB)          # {years: [...], chambers: [...]}
    parse_config = Column(JSONB)  # parser + OCR settings, confidence policy
    stats = Column(JSONB)         # counts + parse-method / confidence / year breakdowns

    runs = relationship("AnalysisRun", back_populates="dataset")


class AnalysisRun(Base):
    """One execution of a parameterized analysis tool against a Dataset.

    Records the tool, every input parameter, headline scores (for cross-run
    comparison), and the full result payload — so any result is traceable.
    """
    __tablename__ = "analysis_runs"

    id = Column(Integer, primary_key=True)
    dataset_id = Column(Integer, ForeignKey("datasets.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    tool = Column(String)             # e.g. "train_test_analysis"
    status = Column(String, default="running")  # running | done | error
    error = Column(String)
    params = Column(JSONB)            # all input parameters
    scores = Column(JSONB)            # headline metrics for comparison
    full_results = Column(JSONB)      # complete result payload

    dataset = relationship("Dataset", back_populates="runs")


class PriceBar(Base):
    __tablename__ = "price_bars"

    id = Column(Integer, primary_key=True)
    ticker = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    fetched_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_price_bar"),
    )
