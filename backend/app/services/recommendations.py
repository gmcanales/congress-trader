"""
Recommendation engine.

Stateless: given a target `as_of` date and a strategy configuration,
reconstructs every position the strategy would have opened with
filing_date ≤ as_of, determines each position's status (open / closed),
and categorizes the result into three buckets a user sees today:

  open     — positions whose entry filing_date is in [as_of - new_window,
              as_of]. "What did the strategy open recently."
  holding  — positions opened before that window but still active (haven't
             hit an exit condition by as_of).
  close    — positions whose exit fires on `as_of` itself (mirror sale
             disclosed today, trailing stop hit today, or time stop
             fires today). "What to close today."

The engine reuses the same exit-policy logic as
`services.backtest._rules_backtest_mirror` so the historical backtest
and the live recommendation are guaranteed consistent.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, timedelta
from typing import Iterable

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.database import SessionLocal
from app import models
from app.services.backtest import (
    DEFAULT_BENCHMARK, SELL_TYPES, GICS_TO_ETF,
    LEADERSHIP_ROLE_TYPES, OWNER_FILTERS,
    ensure_prices, _close_series, _compute_beta, _find_first_exit_idx,
    _apply_owner_filter,
)


# When fetching the historical NAV for the profile-comparison chart we
# pull the most recent done AnalysisRun whose params satisfy this filter.
# Kept here (not in RISK_PROFILES) because it's a *discovery* rule for
# the backtest data, not a config knob of the strategy.
PROFILE_RUN_FILTERS: dict[str, dict] = {
    "conservative": {
        "leadership_only": False,
        "owner_filter": "any",
        "exit_policies": (
            "mirror_sell_with_beta_trailing_stop",
            "mirror_sell_with_trailing_stop",
        ),
    },
    "moderate": {
        "leadership_only": True,
        "owner_filter": "any",
        "exit_policies": (
            "mirror_sell_with_beta_trailing_stop",
            "mirror_sell_with_trailing_stop",
        ),
    },
    "aggressive": {
        "leadership_only": True,
        "owner_filter": "any",
        "exit_policies": ("mirror_sell",),
    },
    # v2.5 — operationalizes the Bayesian finding on `is_spouse_owned`
    # (run #60: P(β > 0) = 0.89, mean +1.11% after controlling for
    # leadership, sector, trifecta, party, in_majority). Backed by
    # owner-cut rules backtests #55 (SPY) and #56 (RSP).
    "spouse_favored": {
        "leadership_only": False,
        "owner_filter": "spouse_or_joint",
        "exit_policies": (
            "mirror_sell_with_beta_trailing_stop",
            "mirror_sell_with_trailing_stop",
            "fixed_horizon",  # #55/#56 are fixed_horizon — accept as fallback
        ),
    },
}


RISK_PROFILES: dict[str, dict] = {
    "conservative": {
        "label": "Conservative",
        "description": "Broad universe, beta-scaled risk control.",
        "universe": "all",
        "owner_filter": "any",
        "exit_policy": "mirror_sell_with_beta_trailing_stop",
        "trailing_stop_pct": 0.15,
        "time_stop_days": 252,
        "new_window_days": 14,    # how many days back counts as "open today" view
    },
    "moderate": {
        "label": "Moderate",
        "description": "Leadership universe, beta-scaled risk control.",
        "universe": "leadership",
        "owner_filter": "any",
        "exit_policy": "mirror_sell_with_beta_trailing_stop",
        "trailing_stop_pct": 0.15,
        "time_stop_days": 252,
        "new_window_days": 14,
    },
    "aggressive": {
        "label": "Aggressive",
        "description": "Leadership universe, no trailing stop. Highest historical "
                       "OOS excess but no intra-position drawdown protection.",
        "universe": "leadership",
        "owner_filter": "any",
        "exit_policy": "mirror_sell",
        "trailing_stop_pct": 0.0,
        "time_stop_days": 252,
        "new_window_days": 14,
    },
    # v2.5 — see `docs/decision_log.md §5.8`. The Bayesian model puts
    # `is_spouse_owned` at P(β > 0) = 0.89 (strongest non-sector signal,
    # not credible at 95% but defensible if you take the posterior
    # probability seriously). Universe = trades where owner ∈ ('SP', 'JT').
    "spouse_favored": {
        "label": "Spouse-favored",
        "description": "Owner is spouse or joint. v2.5 Bayesian finding: "
                       "P(β > 0) = 0.89 on `is_spouse_owned` after sector/"
                       "leadership/trifecta controls. Beta-stop exit.",
        "universe": "all",
        "owner_filter": "spouse_or_joint",
        "exit_policy": "mirror_sell_with_beta_trailing_stop",
        "trailing_stop_pct": 0.15,
        "time_stop_days": 252,
        "new_window_days": 14,
    },
}


@dataclass
class PositionView:
    member_id: int
    member_name: str
    bioguide_id: str | None
    chamber: str | None
    party: str | None
    ticker: str
    entry_filing_date: str
    entry_transaction_date: str
    entry_price: float | None
    current_price: float | None
    days_held: int
    return_since_entry: float | None
    peak_price_since_entry: float | None
    drawdown_from_peak: float | None
    beta: float | None
    effective_stop_pct: float | None
    exit_reason: str | None      # only on close items
    exit_date: str | None
    exit_price: float | None
    realized_return: float | None


def list_risk_profiles() -> list[dict]:
    out = []
    for key, prof in RISK_PROFILES.items():
        out.append({
            "key": key, "label": prof["label"],
            "description": prof["description"],
            "universe": prof["universe"],
            "owner_filter": prof.get("owner_filter", "any"),
            "exit_policy": prof["exit_policy"],
            "trailing_stop_pct": prof["trailing_stop_pct"],
            "time_stop_days": prof["time_stop_days"],
        })
    return out


def _resolve_profile(risk_profile: str | None, overrides: dict | None) -> dict:
    base = RISK_PROFILES.get(risk_profile or "conservative",
                              RISK_PROFILES["conservative"]).copy()
    if overrides:
        base.update({k: v for k, v in overrides.items() if v is not None})
    return base


def compute_recommendations(as_of: date,
                            risk_profile: str = "conservative",
                            overrides: dict | None = None,
                            lookback_days: int = 365) -> dict:
    """The headline function the API calls. Returns:
      {
        as_of, risk_profile, strategy_config,
        open: [PositionView, ...]   # entries with filing_date in last new_window_days
        holding: [PositionView, ...]  # currently held, not closing today
        close: [PositionView, ...]    # closing today (exit signal fires on as_of)
        summary: { ... }
      }
    """
    cfg = _resolve_profile(risk_profile, overrides)
    leadership_only = cfg["universe"] == "leadership"
    owner_filter = cfg.get("owner_filter", "any")
    if owner_filter not in OWNER_FILTERS:
        raise ValueError(f"unknown owner_filter: {owner_filter!r}")
    exit_policy = cfg["exit_policy"]
    trailing_stop_pct = cfg["trailing_stop_pct"]
    time_stop_days = cfg["time_stop_days"]
    new_window_days = cfg["new_window_days"]

    db = SessionLocal()
    try:
        # Pull all purchase events with filing_date ≤ as_of, going back
        # `lookback_days + time_stop_days` so we capture every position
        # that could still be open today.
        lookback_start = as_of - timedelta(days=lookback_days + time_stop_days)
        eq = (db.query(
                models.Transaction.id.label("trade_id"),
                models.Transaction.ticker.label("ticker"),
                models.Transaction.filing_date.label("filing_date"),
                models.Transaction.transaction_date.label("transaction_date"),
                models.Transaction.member_id.label("member_id"),
                models.Member.last_name, models.Member.first_name,
                models.Member.bioguide_id, models.Member.chamber, models.Member.party,
            )
            .join(models.Member, models.Member.id == models.Transaction.member_id)
            .filter(
                models.Transaction.ticker.isnot(None),
                models.Transaction.ticker != "",
                models.Transaction.filing_date.isnot(None),
                models.Transaction.filing_date >= lookback_start,
                models.Transaction.filing_date <= as_of,
                models.Transaction.transaction_type == "purchase",
            ))
        if leadership_only:
            p = models.Position
            t = models.Transaction
            from sqlalchemy import func
            leadership_filter = (
                select(1)
                .where(p.member_id == t.member_id)
                .where(p.role_type.in_(LEADERSHIP_ROLE_TYPES))
                .where(t.transaction_date >= p.start_date)
                .where(t.transaction_date <= func.coalesce(p.end_date, func.current_date()))
                .exists()
            )
            eq = eq.filter(leadership_filter)
        eq = _apply_owner_filter(eq, owner_filter)
        entries = eq.order_by(models.Transaction.filing_date).all()

        # All sales by relevant members on those tickers, up to AND including
        # `as_of` — needed to fire mirror exits.
        member_ids = list({e.member_id for e in entries})
        tickers = list({e.ticker for e in entries})
        sales_by_key: dict[tuple, list[date]] = {}
        if member_ids and tickers:
            sq = db.query(
                models.Transaction.ticker,
                models.Transaction.filing_date,
                models.Transaction.member_id,
            ).filter(
                models.Transaction.member_id.in_(member_ids),
                models.Transaction.ticker.in_(tickers),
                models.Transaction.filing_date.isnot(None),
                models.Transaction.filing_date <= as_of,
                models.Transaction.transaction_type.in_(SELL_TYPES),
            )
            # Mirror exits should match owner class: a spouse-buy strategy
            # exits when the spouse sells, not when the member sells.
            sq = _apply_owner_filter(sq, owner_filter)
            for s_ticker, s_date, s_member in sq.all():
                sales_by_key.setdefault((s_member, s_ticker), []).append(s_date)

        # Price data: tickers + SPY for beta. Fetch from a bit before lookback
        # start (need ~252 trading days history for beta) through as_of + 30d.
        needs_beta = exit_policy == "mirror_sell_with_beta_trailing_stop"
        price_start = (lookback_start - timedelta(days=400)) if needs_beta else lookback_start
        ensure_prices(set(tickers) | {"SPY"}, price_start, as_of + timedelta(days=30))

        spy_series = _close_series(db, "SPY") if needs_beta else None
        close_cache: dict[str, pd.Series | None] = {}

        def closes(t: str) -> pd.Series | None:
            if t not in close_cache:
                close_cache[t] = _close_series(db, t)
            return close_cache[t]

        as_of_ts = pd.Timestamp(as_of)
        new_threshold = as_of - timedelta(days=new_window_days)

        open_list: list[PositionView] = []
        holding_list: list[PositionView] = []
        close_list: list[PositionView] = []

        for e in entries:
            ser = closes(e.ticker)
            if ser is None:
                continue
            # Position opens the trading day strictly after the filing_date.
            entry_pos = ser.index.searchsorted(pd.Timestamp(e.filing_date), side="right")
            if entry_pos >= len(ser):
                continue
            open_idx = entry_pos
            open_price = float(ser.iloc[open_idx])

            # Determine exit (or that it hasn't happened yet by as_of).
            # Candidate exits, all measured against the trading-day calendar
            # in `ser.index`.
            cand: list[tuple[int, str]] = []

            # (a) mirror exit — earliest same-member, same-ticker sale after filing_date
            future_sales = [
                d for d in sales_by_key.get((e.member_id, e.ticker), [])
                if d > e.filing_date
            ]
            if future_sales:
                mirror_date = min(future_sales)
                mirror_idx = ser.index.searchsorted(pd.Timestamp(mirror_date))
                if mirror_idx < len(ser):
                    cand.append((mirror_idx, "mirror_sell"))

            # (b) trailing stop (if exit policy uses one)
            eff_stop = None
            beta = None
            if exit_policy in ("mirror_sell_with_trailing_stop",
                               "mirror_sell_with_beta_trailing_stop"):
                if exit_policy == "mirror_sell_with_beta_trailing_stop":
                    beta = _compute_beta(ser, spy_series, e.filing_date)
                    scale = 1.0 if beta is None else max(0.5, min(beta, 3.0))
                    eff_stop = trailing_stop_pct * scale
                else:
                    eff_stop = trailing_stop_pct
                price_window = ser.iloc[open_idx:]
                offset = _find_first_exit_idx(price_window, eff_stop)
                if offset is not None:
                    cand.append((open_idx + offset, "trailing_stop"))

            # (c) time stop
            ts_idx = open_idx + time_stop_days
            if ts_idx < len(ser):
                cand.append((ts_idx, "time_stop"))

            # Pick earliest. If earliest is AFTER as_of_ts (the trading day
            # index of as_of), the position is still open.
            as_of_idx = ser.index.searchsorted(as_of_ts, side="right") - 1
            if as_of_idx < 0:
                continue
            current_price = float(ser.iloc[as_of_idx])

            window_so_far = ser.iloc[open_idx:as_of_idx + 1]
            peak = float(window_so_far.max())
            drawdown = (current_price / peak - 1) if peak > 0 else None

            exit_chosen: tuple[int, str] | None = None
            for cand_idx, reason in sorted(cand, key=lambda x: x[0]):
                if cand_idx <= as_of_idx:
                    exit_chosen = (cand_idx, reason)
                    break

            base = PositionView(
                member_id=e.member_id,
                member_name=f"{e.first_name} {e.last_name}".strip(),
                bioguide_id=e.bioguide_id,
                chamber=e.chamber,
                party=e.party,
                ticker=e.ticker,
                entry_filing_date=e.filing_date.isoformat(),
                entry_transaction_date=e.transaction_date.isoformat() if e.transaction_date else None,
                entry_price=round(open_price, 4),
                current_price=round(current_price, 4),
                days_held=int(as_of_idx - open_idx),
                return_since_entry=round(current_price / open_price - 1, 4) if open_price else None,
                peak_price_since_entry=round(peak, 4),
                drawdown_from_peak=round(drawdown, 4) if drawdown is not None else None,
                beta=round(beta, 3) if beta is not None else None,
                effective_stop_pct=round(eff_stop, 4) if eff_stop is not None else None,
                exit_reason=None, exit_date=None, exit_price=None, realized_return=None,
            )

            if exit_chosen is not None:
                exit_idx, reason = exit_chosen
                exit_date = ser.index[exit_idx].date()
                exit_price = float(ser.iloc[exit_idx])
                # If the exit was today (or in the last new_window_days?) treat as `close`.
                # If exit happened earlier, the position is already gone — skip.
                if exit_date == as_of:
                    base.exit_reason = reason
                    base.exit_date = exit_date.isoformat()
                    base.exit_price = round(exit_price, 4)
                    base.realized_return = round(exit_price / open_price - 1, 4) if open_price else None
                    close_list.append(base)
                else:
                    # Already closed before today — irrelevant to today's view.
                    continue
            else:
                # Position still open. Bucket by recency of entry.
                if e.filing_date >= new_threshold:
                    open_list.append(base)
                else:
                    holding_list.append(base)
        # Sort sensibly.
        open_list.sort(key=lambda x: x.entry_filing_date, reverse=True)
        holding_list.sort(key=lambda x: x.days_held)
        close_list.sort(key=lambda x: (x.exit_reason or ""))

        summary = {
            "n_entries_considered": len(entries),
            "n_open_today": len(open_list),
            "n_holding": len(holding_list),
            "n_close_today": len(close_list),
            "as_of_trading_day": ser.index[as_of_idx].date().isoformat()
                                  if entries and len(ser) > 0 else None,
        }
        return {
            "as_of": as_of.isoformat(),
            "risk_profile": risk_profile,
            "strategy_config": {
                "universe": cfg["universe"],
                "owner_filter": owner_filter,
                "exit_policy": exit_policy,
                "trailing_stop_pct": trailing_stop_pct,
                "time_stop_days": time_stop_days,
                "new_window_days": new_window_days,
                "label": cfg["label"],
                "description": cfg["description"],
            },
            "open": [asdict(p) for p in open_list],
            "holding": [asdict(p) for p in holding_list],
            "close": [asdict(p) for p in close_list],
            "summary": summary,
        }
    finally:
        db.close()


def _find_run_for_profile(db, profile_key: str) -> models.AnalysisRun | None:
    """Pick the most recent done AnalysisRun whose params satisfy the
    profile's discovery filter. Returns None if no matching run exists."""
    flt = PROFILE_RUN_FILTERS.get(profile_key)
    if flt is None:
        return None
    rows = (
        db.query(models.AnalysisRun)
        .filter(models.AnalysisRun.status == "done")
        .filter(models.AnalysisRun.tool == "train_test_analysis")
        .order_by(models.AnalysisRun.created_at.desc())
        .all()
    )
    for r in rows:
        scores = r.scores or {}
        params = r.params or {}
        leadership_only = bool(scores.get("leadership_only",
                                          params.get("leadership_only", False)))
        owner_filter = scores.get("owner_filter",
                                   params.get("owner_filter", "any"))
        exit_policy = scores.get("exit_policy",
                                  params.get("exit_policy", "fixed_horizon"))
        # filing_date only; transaction_date is diagnostic
        date_field = scores.get("date_field",
                                 params.get("date_field", "filing_date"))
        if date_field != "filing_date":
            continue
        if leadership_only != flt["leadership_only"]:
            continue
        if owner_filter != flt.get("owner_filter", "any"):
            continue
        if exit_policy not in flt["exit_policies"]:
            continue
        return r
    return None


def _band_from_nav(nav: list[float], window: int = 21,
                   k: float = 1.0) -> tuple[list[float | None], list[float | None]]:
    """Rolling-volatility envelope around an NAV path.

    At each point t we compute σ_t = rolling stdev of the last `window`
    daily returns, then return NAV(t) × (1 ± k·σ_t). Visually this is a
    "daily volatility band" around the realized strategy path — wider in
    turbulent periods, tighter in calm ones. It is NOT a confidence
    interval on the cumulative return: see the chart caption.
    """
    if not nav:
        return [], []
    arr = np.asarray(nav, dtype=float)
    rets = np.diff(arr) / arr[:-1]
    rets = np.insert(rets, 0, np.nan)  # align with NAV indices
    # rolling stdev
    band_lo: list[float | None] = []
    band_hi: list[float | None] = []
    for i in range(len(arr)):
        if i < window:
            band_lo.append(None); band_hi.append(None); continue
        seg = rets[i - window + 1 : i + 1]
        sigma = float(np.nanstd(seg, ddof=1))
        if np.isnan(sigma):
            band_lo.append(None); band_hi.append(None); continue
        band_lo.append(round(float(arr[i] * (1 - k * sigma)), 6))
        band_hi.append(round(float(arr[i] * (1 + k * sigma)), 6))
    return band_lo, band_hi


def get_strategy_comparison(band_k: float = 1.0,
                            band_window: int = 21) -> dict:
    """Return out-of-sample NAV curves for each of the three risk
    profiles, plus a rolling-volatility envelope around each."""
    db = SessionLocal()
    try:
        out: dict = {"profiles": [], "missing": []}
        for key, prof in RISK_PROFILES.items():
            run = _find_run_for_profile(db, key)
            if run is None or not run.full_results:
                out["missing"].append({
                    "key": key, "label": prof["label"],
                    "reason": "no matching AnalysisRun found",
                })
                continue
            oos = (run.full_results or {}).get("out_of_sample_test") or {}
            curve = oos.get("nav_curve") or []
            if not curve:
                out["missing"].append({
                    "key": key, "label": prof["label"],
                    "reason": f"run #{run.id} has no nav_curve",
                })
                continue
            dates = [p["date"] for p in curve]
            strat = [float(p["strategy"]) for p in curve]
            bench = [float(p["benchmark"]) for p in curve]
            lo, hi = _band_from_nav(strat, window=band_window, k=band_k)
            scores = run.scores or {}
            out["profiles"].append({
                "key": key,
                "label": prof["label"],
                "description": prof["description"],
                "run_id": run.id,
                "window": oos.get("window") or run.full_results.get("window"),
                "dates": dates,
                "strategy": strat,
                "benchmark": bench,
                "band_lo": lo,
                "band_hi": hi,
                "summary": {
                    "oos_strategy_return": scores.get("oos_strategy_return"),
                    "oos_benchmark_return": scores.get("oos_benchmark_return"),
                    "oos_excess": scores.get("oos_excess"),
                    "exit_policy": scores.get("exit_policy"),
                    "leadership_only": scores.get("leadership_only"),
                    "owner_filter": scores.get("owner_filter", "any"),
                    "benchmark": scores.get("benchmark"),
                },
            })
        out["band"] = {
            "method": "rolling_daily_vol_envelope",
            "window": band_window,
            "k_sigma": band_k,
            "description": (
                f"NAV × (1 ± {band_k}σ) where σ is the trailing "
                f"{band_window}-day stdev of daily strategy returns. "
                "Visualizes recent volatility, not a CI on cumulative return."
            ),
        }
        return out
    finally:
        db.close()
