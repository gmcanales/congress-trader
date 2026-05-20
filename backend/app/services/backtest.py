"""
v1 backtest / event-study engine for congressional trades.

Design principles (to keep the analysis honest):
  * Every event is keyed on `filing_date` — the date the trade became public —
    never `transaction_date`. This respects the up-to-45-day reporting lag.
  * Forward returns are measured in trading days and benchmarked against SPY,
    so we isolate stock-picking from overall market direction (beta).
  * Outcomes are bucketed *relative to the benchmark* (outperform / in-line /
    underperform), not in absolute gain terms.
"""
import logging
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import SessionLocal
from app import models

logger = logging.getLogger(__name__)

DEFAULT_BENCHMARK = "SPY"                    # cap-weighted S&P 500
HORIZONS = {"1w": 5, "1m": 21, "3m": 63}     # name -> trading days
BUCKET_THRESHOLD = 0.02                      # +/-2% excess return = "in-line"
PRICE_BATCH = 50                             # tickers per Yahoo batch download
SELL_TYPES = ("sale", "sale_partial")
# Position role_types considered "leadership" for the leadership_only filter.
# Includes formal chamber leadership (Speaker, Majority Leader, etc.) and,
# once chair tenure is ingested in v2 step 3, committee chairs.
LEADERSHIP_ROLE_TYPES = ("leadership", "chair")


def _leadership_exists_filter():
    """SQLAlchemy EXISTS subquery: only trades where the member held a
    leadership / chair position on the *transaction date*. The Wei & Zhou
    mechanism is informational — the leader uses inside knowledge at the
    moment they trade — so the position check anchors on transaction_date
    regardless of the backtest's event-entry anchor."""
    p = models.Position
    t = models.Transaction
    return (
        select(1)
        .where(p.member_id == t.member_id)
        .where(p.role_type.in_(LEADERSHIP_ROLE_TYPES))
        .where(t.transaction_date >= p.start_date)
        .where(t.transaction_date <= func.coalesce(p.end_date, func.current_date()))
        .exists()
    )


# ── Price data ──────────────────────────────────────────────────────────────

def _store_prices(db, ticker: str, df: pd.DataFrame) -> int:
    rows = []
    for ts, row in df.iterrows():
        close = row.get("Close")
        if close is None or pd.isna(close):
            continue
        rows.append({
            "ticker": ticker, "date": ts.date(),
            "open": float(row.get("Open", close) or close),
            "high": float(row.get("High", close) or close),
            "low": float(row.get("Low", close) or close),
            "close": float(close),
            "volume": float(row.get("Volume", 0) or 0),
        })
    if rows:
        stmt = pg_insert(models.PriceBar.__table__).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["ticker", "date"])
        db.execute(stmt)
        db.commit()
    return len(rows)


def _fetch_batch(tickers: list[str], start: date, end: date) -> dict:
    """Yahoo batch download -> {ticker: date-indexed OHLCV DataFrame}."""
    raw = yf.download(
        tickers, start=start.isoformat(), end=end.isoformat(),
        auto_adjust=True, progress=False, group_by="ticker", threads=True,
    )
    out = {}
    if isinstance(raw.columns, pd.MultiIndex):
        available = set(raw.columns.get_level_values(0))
        for t in tickers:
            if t in available:
                df = raw[t].dropna(how="all")
                if not df.empty:
                    out[t] = df
    elif not raw.empty:  # single-ticker download
        out[tickers[0]] = raw.dropna(how="all")
    return out


def ensure_prices(tickers: set[str], start: date, end: date) -> dict:
    """Fetch price history for any tickers not already covering [start, end]."""
    db = SessionLocal()
    stats = {"requested": len(tickers), "fetched": 0, "missing": 0}
    try:
        needed = []
        for t in tickers:
            mn, mx = (
                db.query(func.min(models.PriceBar.date), func.max(models.PriceBar.date))
                .filter(models.PriceBar.ticker == t)
                .first()
            )
            if mn is None or mn > start + timedelta(days=10) or mx < end - timedelta(days=10):
                needed.append(t)

        logger.info(f"ensure_prices: {len(needed)}/{len(tickers)} tickers need fetching")
        for i in range(0, len(needed), PRICE_BATCH):
            batch = needed[i:i + PRICE_BATCH]
            try:
                fetched = _fetch_batch(batch, start, end)
                for t, df in fetched.items():
                    _store_prices(db, t, df)
                stats["fetched"] += len(fetched)
                stats["missing"] += len(batch) - len(fetched)
            except Exception as exc:
                logger.warning(f"price batch failed ({batch[0]}…): {exc}")
                stats["missing"] += len(batch)
            time.sleep(1)  # be polite to Yahoo
    finally:
        db.close()
    return stats


def _close_series(db, ticker: str) -> pd.Series | None:
    rows = (
        db.query(models.PriceBar.date, models.PriceBar.close)
        .filter(models.PriceBar.ticker == ticker)
        .order_by(models.PriceBar.date)
        .all()
    )
    if not rows:
        return None
    return pd.Series(
        [float(c) for _, c in rows],
        index=pd.to_datetime([d for d, _ in rows]),
    )


def _forward_return(series: pd.Series, entry: date, n_days: int) -> float | None:
    """Return over `n_days` trading days from the first bar on/after `entry`."""
    pos = series.index.searchsorted(pd.Timestamp(entry))
    exit_pos = pos + n_days
    if pos >= len(series) or exit_pos >= len(series):
        return None
    p0, p1 = series.iloc[pos], series.iloc[exit_pos]
    return (p1 / p0 - 1) if p0 > 0 else None


# ── Statistics ──────────────────────────────────────────────────────────────

def _bucket(excess: float) -> str:
    if excess > BUCKET_THRESHOLD:
        return "outperform"
    if excess < -BUCKET_THRESHOLD:
        return "underperform"
    return "in-line"


def _summarize(excess_returns: list[float]) -> dict:
    arr = np.array(excess_returns, dtype=float)
    n = len(arr)
    if n == 0:
        return {"n": 0}
    std = float(arr.std(ddof=1)) if n > 1 else 0.0
    buckets = {"outperform": 0, "in-line": 0, "underperform": 0}
    for x in arr:
        buckets[_bucket(x)] += 1
    return {
        "n": n,
        "mean_excess": round(float(arr.mean()), 4),
        "median_excess": round(float(np.median(arr)), 4),
        "win_rate": round(float((arr > 0).mean()), 4),
        "t_stat": round(float(arr.mean() / (std / np.sqrt(n))), 2) if std > 0 else None,
        "buckets": {k: round(v / n, 4) for k, v in buckets.items()},
    }


# ── Event study ─────────────────────────────────────────────────────────────

def event_study(start: date, end: date, date_field: str = "filing_date",
                horizons: dict | None = None,
                benchmark: str = DEFAULT_BENCHMARK,
                leadership_only: bool = False) -> dict:
    """Mean excess return after congressional buys vs sells, by horizon & chamber.

    `date_field` chooses the event anchor: 'filing_date' (the public date — for
    a tradeable strategy) or 'transaction_date' (when the member actually traded).
    `benchmark` is the ticker excess returns are measured against (default SPY,
    cap-weighted; RSP is the equal-weight S&P 500 alternative).
    `leadership_only` restricts events to trades made while the member held
    a leadership or committee-chair position (theory-driven cut per Wei & Zhou).
    """
    horizons = horizons or HORIZONS
    db = SessionLocal()
    try:
        date_col = getattr(models.Transaction, date_field)
        q = (
            db.query(
                models.Transaction.ticker.label("ticker"),
                date_col.label("event_date"),
                models.Transaction.transaction_type.label("transaction_type"),
                models.Member.chamber.label("chamber"),
            )
            .join(models.Member, models.Member.id == models.Transaction.member_id)
            .filter(
                models.Transaction.ticker.isnot(None),
                models.Transaction.ticker != "",
                date_col.isnot(None),
                date_col >= start,
                date_col <= end,
            )
        )
        if leadership_only:
            q = q.filter(_leadership_exists_filter())
        rows = q.all()
        tickers = {r.ticker for r in rows}
        ensure_prices(tickers | {benchmark}, start, end + timedelta(days=130))

        spy = _close_series(db, benchmark)
        series_cache: dict[str, pd.Series | None] = {}

        def series(t):
            if t not in series_cache:
                series_cache[t] = _close_series(db, t)
            return series_cache[t]

        # accumulator: excess[side][horizon][chamber|"all"] -> list
        excess: dict = {"buy": {}, "sell": {}}
        for side in excess:
            for h in horizons:
                excess[side][h] = {"all": [], "house": [], "senate": []}

        missing_price = 0
        for r in rows:
            ser = series(r.ticker)
            if ser is None:
                missing_price += 1
                continue
            side = "sell" if r.transaction_type in SELL_TYPES else "buy"
            for hname, hdays in horizons.items():
                stock = _forward_return(ser, r.event_date, hdays)
                bench = _forward_return(spy, r.event_date, hdays)
                if stock is None or bench is None:
                    continue
                x = stock - bench
                excess[side][hname]["all"].append(x)
                if r.chamber in ("house", "senate"):
                    excess[side][hname][r.chamber].append(x)

        result = {"buy": {}, "sell": {}}
        for side in result:
            for hname in horizons:
                result[side][hname] = {
                    grp: _summarize(vals)
                    for grp, vals in excess[side][hname].items()
                }
        return {
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "date_field": date_field,
            "benchmark": benchmark,
            "leadership_only": leadership_only,
            "events_total": len(rows),
            "events_missing_price": missing_price,
            "results": result,
        }
    finally:
        db.close()


# ── Rules backtest (calendar NAV simulation) ────────────────────────────────

def rules_backtest(start: date, end: date, horizon_days: int = 63,
                   min_members: int = 1, date_field: str = "filing_date",
                   benchmark: str = DEFAULT_BENCHMARK,
                   leadership_only: bool = False) -> dict:
    """Simulate an equal-weight 'follow the buys' portfolio vs SPY buy-and-hold.

    Each qualifying purchase opens a position the trading day after its event
    date, held `horizon_days`. With min_members>1, only tickers bought by that
    many distinct members within a trailing 30-day window qualify (conviction
    filter). `date_field` is the event anchor (filing_date or transaction_date)."""
    db = SessionLocal()
    try:
        date_col = getattr(models.Transaction, date_field)
        q = (
            db.query(
                models.Transaction.ticker.label("ticker"),
                date_col.label("event_date"),
                models.Transaction.member_id.label("member_id"),
            )
            .filter(
                models.Transaction.ticker.isnot(None),
                models.Transaction.ticker != "",
                date_col.isnot(None),
                date_col >= start,
                date_col <= end,
                models.Transaction.transaction_type == "purchase",
            )
        )
        if leadership_only:
            q = q.filter(_leadership_exists_filter())
        rows = q.order_by(date_col).all()
        tickers = {r.ticker for r in rows}
        ensure_prices(tickers | {benchmark}, start, end + timedelta(days=130))

        spy = _close_series(db, benchmark)
        if spy is None:
            return {"error": f"no benchmark price data for {benchmark}"}
        # extend the calendar past `end` so positions opened late still mature
        cal_end = pd.Timestamp(end) + timedelta(days=int(horizon_days * 1.6))
        spy = spy[(spy.index >= pd.Timestamp(start)) & (spy.index <= cal_end)]
        calendar = spy.index

        # conviction filter: ticker needs min_members distinct buyers in a
        # trailing 30-day window (grouped by ticker to stay fast)
        events = rows
        if min_members > 1:
            by_ticker: dict = {}
            for r in rows:
                by_ticker.setdefault(r.ticker, []).append(r)
            kept = []
            for r in rows:
                members = {
                    o.member_id for o in by_ticker[r.ticker]
                    if 0 <= (r.event_date - o.event_date).days <= 30
                }
                if len(members) >= min_members:
                    kept.append(r)
            events = kept

        # per-ticker daily returns aligned to the master calendar
        ret_cache: dict[str, pd.Series] = {}

        def returns(t):
            if t not in ret_cache:
                ser = _close_series(db, t)
                ret_cache[t] = (
                    ser.pct_change().reindex(calendar) if ser is not None
                    else pd.Series(np.nan, index=calendar)
                )
            return ret_cache[t]

        port = pd.Series(0.0, index=calendar)
        n_open = pd.Series(0, index=calendar)
        positions = 0
        for r in events:
            ret = returns(r.ticker)
            entry_pos = calendar.searchsorted(pd.Timestamp(r.event_date))
            window = calendar[entry_pos + 1: entry_pos + 1 + horizon_days]
            if len(window) == 0:
                continue
            positions += 1
            day_ret = ret.loc[window]
            port.loc[window] += day_ret.fillna(0.0)
            n_open.loc[window] += day_ret.notna().astype(int)

        daily = (port / n_open.replace(0, np.nan)).fillna(0.0)
        nav = (1 + daily).cumprod()
        spy_nav = (1 + spy.pct_change().fillna(0.0)).cumprod()

        strat_total = float(nav.iloc[-1] - 1) if len(nav) else 0.0
        spy_total = float(spy_nav.iloc[-1] - 1) if len(spy_nav) else 0.0
        invested_days = int((n_open > 0).sum())

        return {
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "params": {"horizon_days": horizon_days, "min_members": min_members,
                       "date_field": date_field, "benchmark": benchmark,
                       "leadership_only": leadership_only},
            "positions_opened": positions,
            "days_invested": invested_days,
            "trading_days": len(calendar),
            "strategy_return": round(strat_total, 4),
            "benchmark_return": round(spy_total, 4),
            "excess_return": round(strat_total - spy_total, 4),
            "max_concurrent_positions": int(n_open.max()) if len(n_open) else 0,
            "nav_curve": [
                {"date": d.date().isoformat(),
                 "strategy": round(float(s), 4),
                 "benchmark": round(float(b), 4)}
                for d, s, b in zip(calendar, nav, spy_nav)
            ],
        }
    finally:
        db.close()


# ── Train / test orchestration ──────────────────────────────────────────────

PARAM_GRID = [
    {"horizon_days": 21, "min_members": 1},
    {"horizon_days": 21, "min_members": 2},
    {"horizon_days": 63, "min_members": 1},
    {"horizon_days": 63, "min_members": 2},
    {"horizon_days": 63, "min_members": 3},
]


def train_test_analysis(train_start: date, train_end: date,
                        test_start: date, test_end: date,
                        date_field: str = "filing_date",
                        param_grid: list | None = None,
                        horizons: dict | None = None,
                        benchmark: str = DEFAULT_BENCHMARK,
                        leadership_only: bool = False) -> dict:
    """Grid-search the strategy on the training years, then evaluate the single
    best configuration once on the held-out test year. The test period is
    touched exactly once, with parameters locked from training."""
    param_grid = param_grid or PARAM_GRID
    es_train = event_study(train_start, train_end, date_field, horizons,
                           benchmark=benchmark, leadership_only=leadership_only)
    es_test = event_study(test_start, test_end, date_field, horizons,
                          benchmark=benchmark, leadership_only=leadership_only)

    grid = []
    for params in param_grid:
        grid.append({"params": params,
                     "result": rules_backtest(train_start, train_end,
                                              date_field=date_field,
                                              benchmark=benchmark,
                                              leadership_only=leadership_only,
                                              **params)})

    best = max(grid, key=lambda g: g["result"].get("excess_return", -1e9))
    selected_train_result = best["result"]
    out_of_sample = rules_backtest(test_start, test_end,
                                   date_field=date_field,
                                   benchmark=benchmark,
                                   leadership_only=leadership_only,
                                   **best["params"])

    # strip NAV curves from the grid comparison to keep the payload small
    grid_summary = [
        {"params": g["params"],
         "result": {k: v for k, v in g["result"].items() if k != "nav_curve"}}
        for g in grid
    ]

    return {
        "date_field": date_field,
        "benchmark": benchmark,
        "leadership_only": leadership_only,
        "train_window": {"start": train_start.isoformat(), "end": train_end.isoformat()},
        "test_window": {"start": test_start.isoformat(), "end": test_end.isoformat()},
        "event_study": {"train": es_train, "test": es_test},
        "grid_search_train": grid_summary,
        "selected_params": best["params"],
        "selected_train_result": selected_train_result,
        "out_of_sample_test": out_of_sample,
    }
