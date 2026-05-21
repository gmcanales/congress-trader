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

from fastapi import APIRouter, Query

from app.services.recommendations import (
    compute_recommendations, list_risk_profiles,
)


router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("/profiles")
def get_profiles():
    return {"profiles": list_risk_profiles()}


@router.get("/")
def get_recommendations(
    as_of: Optional[date] = Query(None,
        description="YYYY-MM-DD. Defaults to today."),
    risk_profile: str = Query("conservative",
        description="One of: conservative | moderate | aggressive."),
    new_window_days: Optional[int] = Query(None,
        description="Override how many days back count as 'open today'."),
    trailing_stop_pct: Optional[float] = Query(None,
        description="Override the profile's trailing stop. Used only if "
                    "the profile's exit_policy includes a trailing stop."),
    time_stop_days: Optional[int] = Query(None,
        description="Override the profile's time-stop fallback (trading days)."),
):
    as_of = as_of or date.today()
    overrides: dict = {}
    if new_window_days is not None:
        overrides["new_window_days"] = new_window_days
    if trailing_stop_pct is not None:
        overrides["trailing_stop_pct"] = trailing_stop_pct
    if time_stop_days is not None:
        overrides["time_stop_days"] = time_stop_days
    return compute_recommendations(
        as_of=as_of, risk_profile=risk_profile, overrides=overrides or None,
    )
