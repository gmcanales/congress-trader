"""
Recommendation endpoint.

  GET /api/recommendations/profiles
      → list available risk profiles + their underlying strategy configs

  GET /api/recommendations/freshness
      → how current the underlying data is (last recorded trade, latest
        filing / price-bar dates, newest snapshot). Drives the freshness
        banner at the top of the Recommendations page.

  GET /api/recommendations?as_of=YYYY-MM-DD&risk_profile=conservative
      → today's recommendations (open / holding / close) for the chosen
        risk profile. `as_of` defaults to today.

Stateless. Reconstructs every position the strategy would hold given
filings observed on or before `as_of`, applying the same exit rules as
the rules-backtest. The historical backtest and the live recommendation
share `services.recommendations.compute_recommendations` so they stay
consistent.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Query
from sqlalchemy import func

from app.services.recommendations import (
    compute_recommendations, list_risk_profiles, get_strategy_comparison,
)
from app.services.snapshot import (
    get_cached, get_latest_for_profile, refresh_all_profiles,
    MAX_DAYS_PER_PROFILE,
)
from app.database import SessionLocal
from app.services.ingest import get_last_ingest
from app.services.breakdown import compute_breakdown, DIMENSIONS
from app import models


router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("/profiles")
def get_profiles():
    return {"profiles": list_risk_profiles()}


@router.get("/cache")
def cache_status():
    """List every cached snapshot row, newest first. Useful as a sanity
    check that the daily 6am-ET job actually ran."""
    db = SessionLocal()
    try:
        rows = (
            db.query(models.RecommendationSnapshot)
            .order_by(
                models.RecommendationSnapshot.as_of.desc(),
                models.RecommendationSnapshot.risk_profile.asc(),
            )
            .all()
        )
        return {
            "max_days_per_profile": MAX_DAYS_PER_PROFILE,
            "count": len(rows),
            "snapshots": [
                {
                    "id": r.id,
                    "as_of": r.as_of.isoformat(),
                    "risk_profile": r.risk_profile,
                    "new_window_days": r.new_window_days,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "summary": (r.payload or {}).get("summary"),
                }
                for r in rows
            ],
        }
    finally:
        db.close()


@router.post("/refresh")
def refresh_cache(background_tasks: BackgroundTasks):
    """Manually trigger the same daily-cron job. Returns immediately;
    the actual compute happens in the background. Poll /cache afterwards."""
    background_tasks.add_task(refresh_all_profiles, None)
    return {"queued": True}


@router.get("/freshness")
def data_freshness():
    """Data-recency summary for the banner at the top of the Recommendations
    page, so a reader can tell at a glance how current the underlying
    disclosures are.

    Deliberately NOT cached: every field is a MAX() or a LIMIT 1 lookup, and
    the whole point is to describe how stale the *data* is — a cached answer
    to that question couldn't stay honest.
    """
    today = date.today()
    db = SessionLocal()
    try:
        latest_filing = db.query(func.max(models.Transaction.filing_date)).scalar()

        # Clamp to today. A handful of PTR parses yield garbage years (there
        # are rows dated 2225-04-04 and 2202-09-19 in the table), and an
        # unclamped MAX() would headline the banner with one of them.
        latest_tx = (
            db.query(func.max(models.Transaction.transaction_date))
            .filter(models.Transaction.transaction_date <= today)
            .scalar()
        )

        # The single most recently disclosed trade — filing_date is the
        # actionable anchor, so "last recorded trade" means last *filed*.
        last = (
            db.query(models.Transaction, models.Member)
            .join(models.Member, models.Transaction.member_id == models.Member.id)
            .filter(models.Transaction.filing_date == latest_filing,
                    models.Transaction.transaction_date <= today)
            .order_by(models.Transaction.transaction_date.desc(),
                      models.Transaction.id.desc())
            .first()
        )
        last_trade = None
        if last is not None:
            tx, member = last
            last_trade = {
                "ticker": tx.ticker,
                "asset_name": tx.asset_name,
                "transaction_type": tx.transaction_type,
                "transaction_date": tx.transaction_date.isoformat(),
                "filing_date": tx.filing_date.isoformat() if tx.filing_date else None,
                "owner": tx.owner,
                "member_name": f"{member.first_name} {member.last_name}".strip(),
                "party": member.party,
                "chamber": member.chamber,
                "filer_type": member.filer_type,
            }

        latest_price = db.query(func.max(models.PriceBar.date)).scalar()

        latest_snapshot = (
            db.query(models.RecommendationSnapshot)
            .order_by(models.RecommendationSnapshot.as_of.desc(),
                      models.RecommendationSnapshot.created_at.desc())
            .first()
        )

        # Distinguishes "Congress filed nothing" from "our scraper stopped
        # running" — without this the banner can't tell those apart, and the
        # second one is the failure that actually needs attention.
        last_ingest = get_last_ingest()
        ingest = None
        if last_ingest:
            ingest = {
                "ok": last_ingest.get("ok"),
                "finished_at": last_ingest.get("finished_at"),
                "recorded_at": last_ingest.get("recorded_at"),
                "transactions_added": last_ingest.get("transactions_added"),
            }

        return {
            "as_of": today.isoformat(),
            "ingest": ingest,
            "latest_filing_date": latest_filing.isoformat() if latest_filing else None,
            "latest_filing_days_ago": (today - latest_filing).days if latest_filing else None,
            "latest_transaction_date": latest_tx.isoformat() if latest_tx else None,
            "latest_price_bar_date": latest_price.isoformat() if latest_price else None,
            "n_transactions": db.query(func.count(models.Transaction.id)).scalar(),
            "last_trade": last_trade,
            "snapshot": {
                "as_of": latest_snapshot.as_of.isoformat(),
                "created_at": (latest_snapshot.created_at.isoformat()
                               if latest_snapshot.created_at else None),
            } if latest_snapshot else None,
        }
    finally:
        db.close()


@router.get("/breakdown")
def breakdown(
    window_days: int = Query(90, ge=7, le=1095,
        description="How many days back from `end` to include, by filing date."),
    end: Optional[date] = Query(None, description="YYYY-MM-DD. Defaults to today."),
    filer_types: str = Query("member",
        description="Comma-separated: member | executive."),
):
    """Buy/sell composition of disclosed trades, cut five ways — sector,
    attention, turnover, market cap, and owner. Descriptive, not a backtest.
    Reads through the canonical-row filter so amended filings don't double."""
    types = [t.strip() for t in filer_types.split(",") if t.strip()]
    return compute_breakdown(window_days=window_days, end=end, filer_types=types)


@router.get("/breakdown/dimensions")
def breakdown_dimensions():
    return {"dimensions": [{"key": k, "label": v} for k, v in DIMENSIONS.items()]}


@router.get("/strategies")
def strategies(
    band_window: int = Query(21, ge=2, le=252,
        description="Rolling window (trading days) for the volatility band."),
    band_k: float = Query(1.0, ge=0.0, le=5.0,
        description="Band half-width in σ. 1 = ±1σ."),
):
    """Compare out-of-sample NAV curves across the three risk profiles,
    each with a rolling daily-vol envelope. Frontend uses this to plot
    the three strategies overlaid with a margin-of-error shade."""
    return get_strategy_comparison(band_k=band_k, band_window=band_window)


@router.get("/")
def get_recommendations(
    as_of: Optional[date] = Query(None,
        description="YYYY-MM-DD. Defaults to today."),
    risk_profile: str = Query("conservative",
        description="One of: conservative | moderate | aggressive | spouse_favored."),
    new_window_days: Optional[int] = Query(None,
        description="Override how many days back count as 'open today'."),
    trailing_stop_pct: Optional[float] = Query(None,
        description="Override the profile's trailing stop. Used only if "
                    "the profile's exit_policy includes a trailing stop."),
    time_stop_days: Optional[int] = Query(None,
        description="Override the profile's time-stop fallback (trading days)."),
    no_cache: bool = Query(False,
        description="Force a live recompute even if a cached snapshot exists."),
):
    as_of = as_of or date.today()
    overrides: dict = {}
    if new_window_days is not None:
        overrides["new_window_days"] = new_window_days
    if trailing_stop_pct is not None:
        overrides["trailing_stop_pct"] = trailing_stop_pct
    if time_stop_days is not None:
        overrides["time_stop_days"] = time_stop_days

    # Cache fast-path: the daily 6am-ET job writes snapshots for the default
    # profile config (no trailing_stop / time_stop overrides). We only hit
    # the cache when the user is asking for that default shape.
    cacheable = not no_cache and trailing_stop_pct is None and time_stop_days is None
    if cacheable:
        hit = get_cached(as_of=as_of, risk_profile=risk_profile,
                          new_window_days=new_window_days)
        if hit is not None:
            return hit
        # No exact-day snapshot; if the caller wants today (or later), fall
        # back to the freshest snapshot in the same window slot so the page
        # still loads instantly. Mark stale.
        #
        # The window is resolved inside get_latest_for_profile, so an explicit
        # `new_window_days` equal to the profile default still hits here. That
        # matters: the UI *always* sends new_window_days (14 by default), so
        # the previous `new_window_days is None` guard made every UI load miss
        # this path and pay a ~75s live compute — every day between midnight
        # ET and the 6am-ET cron, and all day whenever the cron failed.
        if as_of >= date.today():
            stale = get_latest_for_profile(risk_profile, new_window_days)
            if stale is not None:
                stale["as_of_requested"] = as_of.isoformat()
                return stale

    result = compute_recommendations(
        as_of=as_of, risk_profile=risk_profile, overrides=overrides or None,
    )
    result["cache"] = {"hit": False, "reason": "live_compute"}
    return result
