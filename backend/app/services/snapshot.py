"""Recommendation snapshot cache.

Daily at 6am ET (see app.main:scheduler), `refresh_all_profiles()` computes
the recommendations for today across all three risk profiles and persists
each as a row in `recommendation_snapshots`. The page then reads from the
cache instead of recomputing live.

We keep at most MAX_DAYS_PER_PROFILE snapshots per (risk_profile,
new_window_days) tuple. Older rows are pruned after each successful write.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from sqlalchemy import select

from app.database import SessionLocal
from app import models
from app.services.recommendations import (
    RISK_PROFILES, compute_recommendations, _resolve_profile,
)


logger = logging.getLogger(__name__)


MAX_DAYS_PER_PROFILE = 14


def _window_for(profile_key: str, override: Optional[int] = None) -> int:
    if override is not None:
        return override
    cfg = _resolve_profile(profile_key, None)
    return cfg["new_window_days"]


def get_cached(as_of: date, risk_profile: str,
               new_window_days: Optional[int] = None) -> Optional[dict]:
    """Return the cached payload if one exists for the given key, else None."""
    window = _window_for(risk_profile, new_window_days)
    db = SessionLocal()
    try:
        row = (
            db.query(models.RecommendationSnapshot)
            .filter(
                models.RecommendationSnapshot.as_of == as_of,
                models.RecommendationSnapshot.risk_profile == risk_profile,
                models.RecommendationSnapshot.new_window_days == window,
            )
            .one_or_none()
        )
        if not row:
            return None
        payload = dict(row.payload)
        payload["cache"] = {
            "hit": True,
            "snapshot_id": row.id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        return payload
    finally:
        db.close()


def _prune_old(db, risk_profile: str, new_window_days: int) -> int:
    """Delete oldest snapshots in this slot beyond MAX_DAYS_PER_PROFILE."""
    rows = (
        db.query(models.RecommendationSnapshot)
        .filter(
            models.RecommendationSnapshot.risk_profile == risk_profile,
            models.RecommendationSnapshot.new_window_days == new_window_days,
        )
        .order_by(models.RecommendationSnapshot.as_of.desc())
        .all()
    )
    to_delete = rows[MAX_DAYS_PER_PROFILE:]
    for r in to_delete:
        db.delete(r)
    return len(to_delete)


def compute_and_store(as_of: date, risk_profile: str,
                      new_window_days: Optional[int] = None) -> dict:
    """Compute today's recs for one profile and upsert into the cache.
    Returns the freshly computed payload (with cache hit=False).
    """
    window = _window_for(risk_profile, new_window_days)
    payload = compute_recommendations(
        as_of=as_of, risk_profile=risk_profile,
        overrides={"new_window_days": window} if new_window_days is not None else None,
    )

    db = SessionLocal()
    try:
        existing = (
            db.query(models.RecommendationSnapshot)
            .filter(
                models.RecommendationSnapshot.as_of == as_of,
                models.RecommendationSnapshot.risk_profile == risk_profile,
                models.RecommendationSnapshot.new_window_days == window,
            )
            .one_or_none()
        )
        if existing:
            existing.payload = payload
            row = existing
        else:
            row = models.RecommendationSnapshot(
                as_of=as_of, risk_profile=risk_profile,
                new_window_days=window, payload=payload,
            )
            db.add(row)
        db.commit()
        db.refresh(row)
        pruned = _prune_old(db, risk_profile, window)
        if pruned:
            db.commit()
            logger.info(f"snapshot.{risk_profile}: pruned {pruned} old row(s)")
        logger.info(
            f"snapshot.{risk_profile} as_of={as_of} window={window} "
            f"open={payload['summary']['n_open_today']} "
            f"hold={payload['summary']['n_holding']} "
            f"close={payload['summary']['n_close_today']}"
        )
        payload["cache"] = {
            "hit": False, "snapshot_id": row.id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        return payload
    finally:
        db.close()


def refresh_all_profiles(as_of: Optional[date] = None) -> dict:
    """The 6am-ET cron entry point. Refreshes today's recs for every
    profile, using each profile's default new_window_days."""
    as_of = as_of or date.today()
    results: dict = {"as_of": as_of.isoformat(), "profiles": {}}
    for key in RISK_PROFILES.keys():
        try:
            res = compute_and_store(as_of=as_of, risk_profile=key)
            results["profiles"][key] = {
                "ok": True, "snapshot_id": res.get("cache", {}).get("snapshot_id"),
                "summary": res.get("summary"),
            }
        except Exception as exc:
            logger.exception(f"snapshot.{key} failed")
            results["profiles"][key] = {"ok": False, "error": str(exc)}
    return results


def get_latest_for_profile(risk_profile: str) -> Optional[dict]:
    """Return the freshest cached payload for a profile, regardless of as_of.
    Used as a fallback when the user asks for `today` but no snapshot for
    today exists yet."""
    db = SessionLocal()
    try:
        row = (
            db.query(models.RecommendationSnapshot)
            .filter(models.RecommendationSnapshot.risk_profile == risk_profile)
            .order_by(models.RecommendationSnapshot.as_of.desc(),
                      models.RecommendationSnapshot.created_at.desc())
            .first()
        )
        if not row:
            return None
        payload = dict(row.payload)
        payload["cache"] = {
            "hit": True, "stale": True,
            "snapshot_id": row.id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        return payload
    finally:
        db.close()
