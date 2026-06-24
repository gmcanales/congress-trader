from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Date, DateTime,
    Float, Boolean, ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from app.database import Base


class Member(Base):
    """A financial-disclosure filer. Originally exclusively congressional
    members; expanded 2026-05-23 to include executive-branch PAS officials
    (cabinet + senior sub-cabinet) who file OGE Form 278-T. Schema kept on
    this table rather than splitting because every analysis path consumes
    a single transaction-with-filer view; partitioning by `filer_type` is
    enough to separate populations at query time."""
    __tablename__ = "members"

    id = Column(Integer, primary_key=True)
    last_name = Column(String, nullable=False)
    first_name = Column(String, nullable=False)
    prefix = Column(String)
    suffix = Column(String)
    state_dst = Column(String)  # House: "CA12" district; Senate: "CA" state; NULL for executive
    party = Column(String)
    chamber = Column(String)  # house | senate; NULL for executive
    # Canonical join key with external datasets (unitedstates/congress-legislators,
    # ProPublica, GovTrack). Populated by `backend/backfill_bioguide.py`.
    # NULL for executive filers (different identifier regime).
    bioguide_id = Column(String, index=True)
    # 'member' (congressional) | 'executive' (PAS / cabinet via OGE 278-T).
    # Backfill in migrations.sql; default 'member' so existing call sites unchanged.
    filer_type = Column(String, nullable=False, default="member", index=True)
    # Executive position title — "Secretary of Treasury", etc. NULL for congressional.
    position_title = Column(String)

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


class TickerInfo(Base):
    """Ticker → GICS sector / industry mapping for sector-neutral analysis.

    Populated by `backend/ingest_sectors.py` from yfinance. The `sector` field
    follows GICS top-level labels (Information Technology, Health Care,
    Financials, …) and is what the backtest engine joins to its
    GICS → SPDR sector-ETF map (XLK, XLV, XLF, …) when running with
    `benchmark_mode="sector"`.
    """
    __tablename__ = "tickers"

    symbol = Column(String, primary_key=True)
    sector = Column(String, index=True)
    industry = Column(String)
    market_cap = Column(Float)
    last_updated = Column(DateTime, default=datetime.utcnow)
    source = Column(String)  # e.g. "yfinance"


# ── v2 leadership tables ────────────────────────────────────────────────────

class Committee(Base):
    """Congressional committees, sub-committees, and joint committees.

    Sourced from unitedstates/congress-legislators (committees-current.yaml,
    committees-historical.yaml). The `thomas_id` is the canonical short
    code used across Congress.gov / GovTrack / ProPublica.
    """
    __tablename__ = "committees"

    id = Column(Integer, primary_key=True)
    thomas_id = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    chamber = Column(String)  # house | senate | joint
    committee_type = Column(String)  # standing | select | joint | special | subcommittee
    parent_committee_id = Column(Integer, ForeignKey("committees.id"))
    jurisdiction = Column(String)


class Position(Base):
    """Time-bounded leadership / committee tenure for a member.

    One row per (member, role, term). For chamber-leadership rows
    (Speaker, Majority/Minority Leader, Whips, etc.) `committee_id` is
    NULL. For committee chair / ranking-member rows `committee_id` is
    populated. `role_type` partitions the universe so analysis tools can
    filter by class.
    """
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True)
    member_id = Column(Integer, ForeignKey("members.id"), nullable=False, index=True)
    role_type = Column(String, nullable=False, index=True)
    # leadership | chair | ranking | committee_member
    title = Column(String, nullable=False)
    committee_id = Column(Integer, ForeignKey("committees.id"), index=True)
    congress = Column(Integer, index=True)
    start_date = Column(Date, nullable=False, index=True)
    end_date = Column(Date, index=True)  # NULL = currently held
    source = Column(String)  # e.g. "unitedstates/congress-legislators@<sha>"


class RecommendationSnapshot(Base):
    """A pre-computed recommendation payload for a specific (as_of, profile,
    new_window_days) tuple. Written by the daily scheduler job at 6am ET so
    the recs page loads instantly. We keep at most ~14 days of history per
    profile (see services/snapshot.py:MAX_DAYS_PER_PROFILE) — the rest is
    pruned after each successful write.
    """
    __tablename__ = "recommendation_snapshots"

    id = Column(Integer, primary_key=True)
    as_of = Column(Date, nullable=False, index=True)
    risk_profile = Column(String, nullable=False, index=True)
    new_window_days = Column(Integer, nullable=False)
    payload = Column(JSONB, nullable=False)   # full compute_recommendations() output
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("as_of", "risk_profile", "new_window_days",
                         name="uq_rec_snapshot"),
    )


class UserHolding(Base):
    """A position the user currently (or historically) holds, tracked
    in the frontend portfolio. Scoped per-user by `user_email` — the
    verified CF Access JWT email (see backend/app/auth.py). `cost_basis`
    is per-share, in the same currency as `price_bars.close`.
    """
    __tablename__ = "user_holdings"

    id = Column(Integer, primary_key=True)
    user_email = Column(String, nullable=False, index=True)
    ticker = Column(String, nullable=False, index=True)
    shares = Column(Float, nullable=False)
    cost_basis = Column(Float, nullable=False)   # per-share
    buy_date = Column(Date, nullable=False)
    notes = Column(String)
    target_exit_price = Column(Float)
    target_exit_date = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PartyControl(Base):
    """Which party held the majority of a chamber over a given period.

    Used to compute the `party_in_majority` flag on
    v_stock_transactions_leadership, since the Wei & Zhou mechanism
    (political-influence channel) requires the leader's party to control
    the chamber.
    """
    __tablename__ = "party_control"

    id = Column(Integer, primary_key=True)
    chamber = Column(String, nullable=False)  # house | senate
    congress = Column(Integer, nullable=False)
    majority_party = Column(String, nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date)

    __table_args__ = (
        UniqueConstraint("chamber", "congress", name="uq_party_control_congress"),
    )


class LeaderboardScore(Base):
    """Arcade high-score entry, shared across every Goosebase game. The `game`
    column namespaces entries so a single table backs all games' leaderboards —
    adding a new game needs no schema change, just a new slug. Scores are
    submitted by the (client-side) game, so they are trusted only loosely:
    names are sanitized and scores range-checked, but full anti-cheat isn't a
    goal here."""
    __tablename__ = "leaderboard_scores"

    id = Column(Integer, primary_key=True)
    game = Column(String, nullable=False)          # slug, e.g. "gooooose"
    name = Column(String, nullable=False)          # uppercase alnum, <= 5 chars
    score = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        # Top-N-per-game query: WHERE game = ? ORDER BY score DESC.
        Index("ix_leaderboard_game_score", "game", "score"),
    )
