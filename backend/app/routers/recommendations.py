"""
Recommendation endpoint.

  GET /api/recommendations/profiles
      → list available risk profiles + their underlying strategy configs

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

from app.services.recommendations import (
    compute_recommendations, list_risk_profiles, get_strategy_comparison,
)
from app.services.snapshot import (
    get_cached, get_latest_for_profile, refresh_all_profiles,
    MAX_DAYS_PER_PROFILE,
)
from app.database import SessionLocal
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
        # No exact-day snapshot; if user asked for today, fall back to the
        # latest cached snapshot so the page still loads fast. Mark stale.
        if as_of == date.today() and new_window_days is None:
            stale = get_latest_for_profile(risk_profile)
            if stale is not None:
                return stale

    result = compute_recommendations(
        as_of=as_of, risk_profile=risk_profile, overrides=overrides or None,
    )
    result["cache"] = {"hit": False, "reason": "live_compute"}
    return result
