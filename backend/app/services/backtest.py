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
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import SessionLocal
from app.services import symbols
from app.services import visibility as visibility_svc
from app import models

logger = logging.getLogger(__name__)

DEFAULT_BENCHMARK = "SPY"                    # cap-weighted S&P 500
HORIZONS = {"1w": 5, "1m": 21, "3m": 63}     # name -> trading days
BUCKET_THRESHOLD = 0.02                      # +/-2% excess return = "in-line"
PRICE_BATCH = 50                             # tickers per Yahoo batch download
SELL_TYPES = ("sale", "sale_partial")
# Position role_types considered "leadership" for the leadership_only filter.
# Includes formal chamber leadership (Speaker, Majority Leader, etc.), once
# chair tenure is ingested in v2 step 3, committee chairs, and (2026-05-23)
# executive-branch cabinet posts tracked via OGE 278-T. Treating cabinet as
# leadership extends Wei & Zhou's "influence + access" theory from
# Congressional leadership to executive officials with comparable policy
# authority; the trade-off is that historical leadership_only=True runs no
# longer have an exact semantic equivalent (cabinet trades start showing up
# automatically after the data lands). See docs/decision_log.md §6.3.
LEADERSHIP_ROLE_TYPES = ("leadership", "chair", "executive_cabinet")

# Owner-of-asset filter values for the owner_filter parameter. Maps to the
# Transaction.owner column populated by the PTR parsers:
#   None → "self" (filer's own holding)   SP → spouse-only
#   JT   → joint (member + spouse)        DC → dependent child
# The JT vs SP split is largely a household reporting convention (how the
# household titles its accounts), not a clean signal about who initiated the
# trade — so the analytical bucket of interest is "spouse_or_joint".
OWNER_FILTERS = ("any", "self", "spouse_or_joint", "spouse_only",
                 "joint_only", "dependent")


FILER_TYPES = ("member", "executive")


def _apply_filer_types(q, filer_types):
    """Filter a SQLAlchemy query of Transactions by Member.filer_type.
    Uses `Transaction.member_id IN (SELECT id FROM members WHERE …)`
    rather than an EXISTS subquery, because some call sites
    (recommendations.compute_recommendations) already JOIN Member in
    the outer query and SQLAlchemy auto-correlation strips the
    subquery's FROM clause in that case. The IN-subquery has no such
    correlation gotcha.

    `filer_types` is None (no filter — all filer types) or a non-empty
    list of values from FILER_TYPES. Existing call sites pass None to
    preserve pre-OGE-278T behavior; cabinet-only analyses pass
    ['executive']; congressional-only analyses pass ['member']."""
    if filer_types is None:
        return q
    if not isinstance(filer_types, (list, tuple)) or not filer_types:
        raise ValueError("filer_types must be a non-empty list or None")
    for ft in filer_types:
        if ft not in FILER_TYPES:
            raise ValueError(f"unknown filer_type: {ft!r} (expected one of {FILER_TYPES})")
    return q.filter(
        models.Transaction.member_id.in_(
            select(models.Member.id)
            .where(models.Member.filer_type.in_(filer_types))
        )
    )


def _apply_owner_filter(q, owner_filter: str):
    """Apply the owner_filter clause to a SQLAlchemy query over Transaction.
    `owner_filter` must be one of OWNER_FILTERS; raises ValueError otherwise.
    `any` is a no-op."""
    t = models.Transaction
    if owner_filter == "any":
        return q
    if owner_filter == "self":
        return q.filter(t.owner.is_(None))
    if owner_filter == "spouse_or_joint":
        return q.filter(t.owner.in_(("SP", "JT")))
    if owner_filter == "spouse_only":
        return q.filter(t.owner == "SP")
    if owner_filter == "joint_only":
        return q.filter(t.owner == "JT")
    if owner_filter == "dependent":
        return q.filter(t.owner == "DC")
    raise ValueError(f"unknown owner_filter: {owner_filter!r} (expected one of {OWNER_FILTERS})")


def _canonical_tx_subquery():
    """Ids of one row per distinct disclosed trade.

    The raw `transactions` table deliberately keeps every line exactly as
    filed, and that is correct: a PTR really does list the same security
    several times across accounts, and an amended filing really does
    re-report trades already disclosed. Both are faithful to the source
    document. Both also double-count in analysis.

    Measured on 2026-10-24: 765 excess rows repeated inside a single filing
    and 560 repeated across filings, out of 13,290 ticker-bearing rows.

    The surviving row is the one with the EARLIEST filing date. That is the
    methodological point, not a tiebreak — a re-report does not make a trade
    newly public, so keeping the later filing would re-enter a years-old
    trade as a fresh `filing_date` signal. 161 of the cross-filing groups
    were re-filed more than a year after the original.
    """
    t = models.Transaction
    ranked = select(
        t.id.label("id"),
        func.row_number().over(
            # asset_name is load-bearing, not decoration: most non-ticker
            # rows (bonds, funds, notes) share a date, type and amount range
            # and are told apart ONLY by their name. Leaving it out collapses
            # 278 distinct bonds in one filing into a single row.
            partition_by=[t.member_id, t.ticker, t.asset_name, t.transaction_date,
                          t.transaction_type, t.amount_min, t.owner],
            order_by=[t.filing_date.asc().nullslast(), t.id.asc()],
        ).label("rn"),
    ).subquery()
    return select(ranked.c.id).where(ranked.c.rn == 1)


def _apply_canonical(q):
    """Restrict a Transaction query to one row per distinct disclosed trade."""
    return q.filter(models.Transaction.id.in_(_canonical_tx_subquery()))


def _apply_visibility(q, visibility: str, date_col,
                      high_pct: float = visibility_svc.DEFAULT_HIGH_PCT):
    """Restrict events by how much public attention the name had *at the time*.

    'high' keeps household names (top `high_pct` of cross-sectional median
    daily dollar volume), 'low' keeps everything else; 'any' is a no-op.
    See services/visibility.py for why dollar volume and not market cap.

    The subquery resolves against the latest month strictly before the event
    month, so a name is judged by what it was when the trade became public
    rather than by what it later became. Tickers with no visibility row yet
    (no price history at that point) are excluded from both tiers rather
    than defaulted into 'low' — unclassifiable is not the same as obscure,
    and defaulting would quietly stuff the low tier with unpriceable junk.
    """
    if visibility == "any":
        return q
    if visibility not in visibility_svc.VISIBILITY_CHOICES:
        raise ValueError(f"unknown visibility: {visibility!r} "
                         f"(expected one of {visibility_svc.VISIBILITY_CHOICES})")

    t = models.Transaction
    pct = (
        select(models.TickerVisibility.pct_rank)
        .where(models.TickerVisibility.ticker == t.ticker)
        .where(models.TickerVisibility.as_of < func.date_trunc("month", date_col))
        .order_by(models.TickerVisibility.as_of.desc())
        .limit(1)
        .scalar_subquery()
    )
    if visibility == "high":
        return q.filter(pct >= high_pct)
    return q.filter(pct < high_pct)

# GICS top-level sector → SPDR sector ETF used for sector-neutral excess return.
# These are the well-known SPDR sector funds that closely track GICS sectors.
GICS_TO_ETF = {
    "Information Technology": "XLK",
    "Technology":              "XLK",   # yfinance sometimes uses the shorter label
    "Health Care":             "XLV",
    "Healthcare":              "XLV",
    "Financials":              "XLF",
    "Financial Services":      "XLF",
    "Communication Services":  "XLC",
    "Consumer Discretionary":  "XLY",
    "Consumer Cyclical":       "XLY",
    "Industrials":             "XLI",
    "Consumer Staples":        "XLP",
    "Consumer Defensive":      "XLP",
    "Energy":                  "XLE",
    "Utilities":               "XLU",
    "Real Estate":             "XLRE",
    "Materials":               "XLB",
    "Basic Materials":         "XLB",
}
ALL_SECTOR_ETFS = sorted(set(GICS_TO_ETF.values()))


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


def _load_sector_etf_lookup(db) -> dict[str, str]:
    """Return {stock_symbol: sector_etf_symbol} for every ticker we have
    sector data on. Tickers missing from this dict have no GICS sector and
    will be excluded from sector-neutral analysis."""
    out: dict[str, str] = {}
    for ti in db.scalars(select(models.TickerInfo)).all():
        etf = GICS_TO_ETF.get(ti.sector or "")
        if etf:
            out[ti.symbol] = etf
    return out


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


# How long before we ask the vendor again about a symbol it had no data for.
# Delisted names never come back, but a symbol can also come up empty because
# the request failed, so this retries occasionally instead of never — just not
# on every run. Grows with consecutive failures, capped.
MARKET_STALE_DAYS = 5          # bars this far behind the market = not trading
NO_DATA_RETRY_DAYS = 14
NO_DATA_RETRY_MAX_DAYS = 90


def _price_meta(db, symbols: set[str]) -> dict:
    rows = (
        db.query(models.TickerInfo)
        .filter(models.TickerInfo.symbol.in_(list(symbols)))
        .all()
    ) if symbols else []
    return {r.symbol: r for r in rows}


def _due_for_attempt(meta, now: datetime, start: date, end: date) -> bool:
    """Whether to call the vendor again for a symbol whose coverage is short.

    The gate is "have we already asked for this window recently", not "has it
    failed" — those come apart in both directions:

    * A healthy symbol's last fetch *succeeded* (price_attempts == 0) and it
      still needs today's bar, so it must stay on a daily cadence. Backing off
      on success would quietly freeze every price in the database.
    * A delisted symbol's fetch also succeeds — it returns the same historical
      bars every time — so failure count alone never identifies it. What marks
      it is that we keep asking for a window ending today and the data never
      reaches today.
    """
    if meta is None or meta.price_last_attempt is None:
        return True
    if meta.price_status == "not_a_ticker":
        return False          # resolution already decided; no vendor call helps

    # Have we already requested at least this much history, and asked for an
    # end at least this recent? If not, this is a genuinely new request.
    asked_start = meta.price_window_start
    asked_end = meta.price_window_end
    already_asked = (
        asked_start is not None and asked_start <= start
        and asked_end is not None
        and asked_end >= end - timedelta(days=MARKET_STALE_DAYS)
    )
    if not already_asked:
        return True

    # Same window as last time. Consecutive failures stretch the interval;
    # a symbol whose last attempt succeeded is simply on the daily cadence.
    backoff_days = (1 if meta.price_attempts == 0
                    else min(NO_DATA_RETRY_DAYS * meta.price_attempts,
                             NO_DATA_RETRY_MAX_DAYS))
    return (now - meta.price_last_attempt) >= timedelta(days=backoff_days)


def _record_price_attempt(db, ticker: str, fetch_symbol: str | None,
                          status: str, now: datetime,
                          start: date | None = None,
                          end: date | None = None) -> None:
    """Upsert fetch bookkeeping. Resets the failure count on success, and
    widens the recorded request window so we never re-ask for history the
    vendor has already declined to provide."""
    col = models.TickerInfo.__table__.c
    stmt = pg_insert(models.TickerInfo.__table__).values(
        symbol=ticker, fetch_symbol=fetch_symbol, price_status=status,
        price_last_attempt=now, price_attempts=0 if status == "ok" else 1,
        price_window_start=start, price_window_end=end,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol"],
        set_={
            "fetch_symbol": fetch_symbol,
            "price_status": status,
            "price_last_attempt": now,
            "price_attempts": (
                0 if status == "ok" else col.price_attempts + 1
            ),
            "price_window_start": func.least(
                func.coalesce(col.price_window_start, start), start),
            "price_window_end": func.greatest(
                func.coalesce(col.price_window_end, end), end),
        },
    )
    db.execute(stmt)


def ensure_prices(tickers: set[str], start: date, end: date) -> dict:
    """Fetch price history for any tickers not already covering [start, end].

    Bars are stored under the *disclosed* ticker even when the vendor is
    queried under a different spelling (BRK.B -> BRK-B), so every existing
    join against transactions.ticker keeps working.
    """
    db = SessionLocal()
    now = datetime.utcnow()
    stats = {"requested": len(tickers), "fetched": 0, "missing": 0,
             "skipped_unresolvable": 0, "skipped_backoff": 0}
    try:
        meta = _price_meta(db, set(tickers))

        needed: dict[str, str] = {}      # disclosed ticker -> fetch symbol
        for t in tickers:
            mn, mx = (
                db.query(func.min(models.PriceBar.date), func.max(models.PriceBar.date))
                .filter(models.PriceBar.ticker == t)
                .first()
            )
            m = meta.get(t)

            # Two separate questions, and conflating them is what made this
            # re-fetch everything forever:
            #   history — do we have bars back to `start`, OR have we already
            #             asked that far back and been given all there is?
            #             (a 2023 IPO can never have 2020 bars)
            #   recent  — are we current with the market? A symbol whose last
            #             bar sits well behind the market's last bar has
            #             stopped trading; asking again tomorrow won't help.
            asked_back_far_enough = (m is not None
                                     and m.price_window_start is not None
                                     and m.price_window_start <= start)
            has_history = mn is not None and mn <= start + timedelta(days=10)
            covered_history = has_history or (mn is not None and asked_back_far_enough)

            covered_recent = mx is not None and mx >= end - timedelta(
                days=MARKET_STALE_DAYS)

            if covered_history and covered_recent:
                continue

            fetch_symbol = symbols.resolve(t)
            if fetch_symbol is None:
                # Not a ticker at all — record it once so we never ask again.
                if m is None or m.price_status != "not_a_ticker":
                    _record_price_attempt(db, t, None, "not_a_ticker", now)
                stats["skipped_unresolvable"] += 1
                continue

            # Already asked for this window recently and got nothing more —
            # whether the symbol is dead or simply absent from the vendor.
            if not _due_for_attempt(m, now, start, end):
                stats["skipped_backoff"] += 1
                continue

            needed[t] = fetch_symbol
        db.commit()

        logger.info(
            f"ensure_prices: {len(needed)}/{len(tickers)} tickers need fetching "
            f"({stats['skipped_unresolvable']} unresolvable, "
            f"{stats['skipped_backoff']} in backoff)"
        )

        items = list(needed.items())
        for i in range(0, len(items), PRICE_BATCH):
            batch = items[i:i + PRICE_BATCH]
            by_fetch = {fs: t for t, fs in batch}
            try:
                fetched = _fetch_batch([fs for _, fs in batch], start, end)
                for fetch_symbol, df in fetched.items():
                    disclosed = by_fetch.get(fetch_symbol, fetch_symbol)
                    _store_prices(db, disclosed, df)
                    _record_price_attempt(db, disclosed, fetch_symbol, "ok", now, start, end)
                for t, fs in batch:
                    if fs not in fetched:
                        _record_price_attempt(db, t, fs, "no_data", now, start, end)
                stats["fetched"] += len(fetched)
                stats["missing"] += len(batch) - len(fetched)
            except Exception as exc:
                logger.warning(f"price batch failed ({batch[0][1]}…): {exc}")
                for t, fs in batch:
                    _record_price_attempt(db, t, fs, "error", now, start, end)
                stats["missing"] += len(batch)
            db.commit()
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
                leadership_only: bool = False,
                benchmark_mode: str = "ticker",
                owner_filter: str = "any",
                visibility: str = "any",
                filer_types: list[str] | None = None) -> dict:
    """Mean excess return after congressional buys vs sells, by horizon & chamber.

    `date_field` chooses the event anchor: 'filing_date' (the public date — for
    a tradeable strategy) or 'transaction_date' (when the member actually traded).
    `benchmark` is the ticker excess returns are measured against (default SPY,
    cap-weighted; RSP is the equal-weight S&P 500 alternative).
    `leadership_only` restricts events to trades made while the member held
    a leadership or committee-chair position (theory-driven cut per Wei & Zhou).
    `owner_filter` restricts events by who held the asset (see OWNER_FILTERS).
    `benchmark_mode`:
      'ticker'  (default) — excess vs the single `benchmark` ticker (SPY/RSP)
      'sector'            — excess vs the stock's GICS sector SPDR ETF
                            (XLK/XLV/XLF/…); trades without sector data are
                            excluded with a count.
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
        q = _apply_owner_filter(q, owner_filter)
        q = _apply_visibility(q, visibility, date_col)
        q = _apply_canonical(q)
        q = _apply_filer_types(q, filer_types)
        rows = q.all()
        tickers = {r.ticker for r in rows}

        sector_lookup: dict[str, str] = {}
        if benchmark_mode == "sector":
            sector_lookup = _load_sector_etf_lookup(db)
            needed_etfs = {sector_lookup[t] for t in tickers if t in sector_lookup}
            ensure_prices(tickers | needed_etfs | {benchmark}, start, end + timedelta(days=130))
        else:
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
        missing_sector = 0
        for r in rows:
            ser = series(r.ticker)
            if ser is None:
                missing_price += 1
                continue
            # Pick the benchmark series for this trade — global ticker, or this
            # ticker's GICS sector ETF in sector mode.
            if benchmark_mode == "sector":
                etf = sector_lookup.get(r.ticker)
                if etf is None:
                    missing_sector += 1
                    continue
                bench_ser = series(etf)
                if bench_ser is None:
                    missing_price += 1
                    continue
            else:
                bench_ser = spy
            side = "sell" if r.transaction_type in SELL_TYPES else "buy"
            for hname, hdays in horizons.items():
                stock = _forward_return(ser, r.event_date, hdays)
                bench = _forward_return(bench_ser, r.event_date, hdays)
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
            "benchmark_mode": benchmark_mode,
            "leadership_only": leadership_only,
            "owner_filter": owner_filter,
            "visibility": visibility,
            "filer_types": filer_types,
            "events_total": len(rows),
            "events_missing_price": missing_price,
            "events_missing_sector": missing_sector,
            "results": result,
        }
    finally:
        db.close()


# ── Rules backtest (calendar NAV simulation) ────────────────────────────────

def rules_backtest(start: date, end: date, horizon_days: int = 63,
                   min_members: int = 1, date_field: str = "filing_date",
                   benchmark: str = DEFAULT_BENCHMARK,
                   leadership_only: bool = False,
                   benchmark_mode: str = "ticker",
                   exit_policy: str = "fixed_horizon",
                   trailing_stop_pct: float = 0.15,
                   time_stop_days: int = 252,
                   owner_filter: str = "any",
                   visibility: str = "any",
                   filer_types: list[str] | None = None) -> dict:
    """Simulate an equal-weight 'follow the buys' portfolio vs a benchmark.

    Each qualifying purchase opens a position the trading day after its event
    date. With min_members>1, only tickers bought by that many distinct
    members within a trailing 30-day window qualify (conviction filter).
    `date_field` is the event anchor (filing_date or transaction_date).

    `exit_policy`:
      'fixed_horizon' (default) — every position closes after `horizon_days`
                                  trading days. Event-study-shaped baseline.
      'mirror_sell_with_trailing_stop' — per-(member, ticker, entry) position
                                  tracking. Closes on the earliest of:
                                    (a) the same member discloses a sale of
                                        the same ticker (mirror exit, on the
                                        sale's filing_date);
                                    (b) the stock falls `trailing_stop_pct`
                                        from its trailing peak since entry;
                                    (c) `time_stop_days` trading days elapse
                                        (fallback when the member never
                                        sells in the data);
                                    (d) end of the simulation calendar.
                                  Closer to a literal-follower strategy.

    `benchmark_mode`:
      'ticker' (default) — `benchmark_return` is buy-and-hold of the single
                           `benchmark` ticker (SPY/RSP).
      'sector'           — `benchmark_return` is the NAV of an equal-weight
                           shadow portfolio that, each day, holds the SPDR
                           sector ETF for every currently-open position. This
                           neutralizes sector exposure, so the residual is
                           within-sector picking skill rather than sector tilt.
                           Trades whose ticker has no GICS sector are excluded.
    """
    MIRROR_POLICIES = {
        "mirror_sell":                          "none",
        "mirror_sell_with_trailing_stop":       "fixed",
        "mirror_sell_with_beta_trailing_stop":  "beta_scaled",
    }
    if exit_policy in MIRROR_POLICIES:
        return _rules_backtest_mirror(
            start, end,
            min_members=min_members, date_field=date_field,
            benchmark=benchmark, leadership_only=leadership_only,
            benchmark_mode=benchmark_mode,
            trailing_stop_pct=trailing_stop_pct,
            time_stop_days=time_stop_days,
            exit_policy=exit_policy,
            trailing_stop_mode=MIRROR_POLICIES[exit_policy],
            owner_filter=owner_filter,
            visibility=visibility,
            filer_types=filer_types,
        )
    elif exit_policy != "fixed_horizon":
        return {"error": f"unknown exit_policy: {exit_policy!r}"}
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
        q = _apply_owner_filter(q, owner_filter)
        q = _apply_visibility(q, visibility, date_col)
        q = _apply_canonical(q)
        q = _apply_filer_types(q, filer_types)
        rows = q.order_by(date_col).all()
        tickers = {r.ticker for r in rows}

        sector_lookup: dict[str, str] = {}
        if benchmark_mode == "sector":
            sector_lookup = _load_sector_etf_lookup(db)
            needed_etfs = set(sector_lookup.values())
            ensure_prices(tickers | needed_etfs | {benchmark}, start, end + timedelta(days=130))
        else:
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
        sec_port = pd.Series(0.0, index=calendar)   # parallel shadow: sector ETF returns of currently-held positions
        n_open_sec = pd.Series(0, index=calendar)
        positions = 0
        skipped_no_sector = 0
        for r in events:
            sector_etf = None
            if benchmark_mode == "sector":
                sector_etf = sector_lookup.get(r.ticker)
                if sector_etf is None:
                    skipped_no_sector += 1
                    continue
            ret = returns(r.ticker)
            entry_pos = calendar.searchsorted(pd.Timestamp(r.event_date))
            window = calendar[entry_pos + 1: entry_pos + 1 + horizon_days]
            if len(window) == 0:
                continue
            positions += 1
            day_ret = ret.loc[window]
            port.loc[window] += day_ret.fillna(0.0)
            n_open.loc[window] += day_ret.notna().astype(int)
            if sector_etf is not None:
                etf_ret = returns(sector_etf).loc[window]
                sec_port.loc[window] += etf_ret.fillna(0.0)
                n_open_sec.loc[window] += etf_ret.notna().astype(int)

        daily = (port / n_open.replace(0, np.nan)).fillna(0.0)
        nav = (1 + daily).cumprod()

        if benchmark_mode == "sector":
            sec_daily = (sec_port / n_open_sec.replace(0, np.nan)).fillna(0.0)
            bench_nav = (1 + sec_daily).cumprod()
        else:
            bench_nav = (1 + spy.pct_change().fillna(0.0)).cumprod()

        # Measure strategy + benchmark at the LAST calendar index on or before
        # `end` — the standard mark-to-market at end-of-period. Reporting at
        # the calendar tail (which extends past `end` to let late positions
        # mature) would systematically inflate benchmark drift relative to a
        # strategy that goes flat after positions close.
        end_idx = max(0, calendar.searchsorted(pd.Timestamp(end), side="right") - 1)
        strat_total = float(nav.iloc[end_idx] - 1) if len(nav) else 0.0
        bench_total = float(bench_nav.iloc[end_idx] - 1) if len(bench_nav) else 0.0
        invested_days = int((n_open > 0).sum())

        return {
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "params": {"horizon_days": horizon_days, "min_members": min_members,
                       "date_field": date_field, "benchmark": benchmark,
                       "benchmark_mode": benchmark_mode,
                       "leadership_only": leadership_only,
                       "owner_filter": owner_filter,
                       "visibility": visibility,
                       "filer_types": filer_types,
                       "exit_policy": "fixed_horizon"},
            "positions_opened": positions,
            "events_skipped_no_sector": skipped_no_sector,
            "days_invested": invested_days,
            "trading_days": len(calendar),
            "strategy_return": round(strat_total, 4),
            "benchmark_return": round(bench_total, 4),
            "excess_return": round(strat_total - bench_total, 4),
            "max_concurrent_positions": int(n_open.max()) if len(n_open) else 0,
            "nav_curve": [
                {"date": d.date().isoformat(),
                 "strategy": round(float(s), 4),
                 "benchmark": round(float(b), 4)}
                for d, s, b in zip(calendar, nav, bench_nav)
            ],
        }
    finally:
        db.close()


# ── Mirror-sell + trailing-stop rules backtest ──────────────────────────────

def _find_first_exit_idx(price_window: pd.Series, trailing_stop_pct: float) -> int | None:
    """Index into `price_window` (0 = day after entry) at which the trailing
    stop fires. Returns None if it never fires within the window."""
    if len(price_window) == 0:
        return None
    arr = price_window.values
    # Running peak from entry forward. Trailing stop fires the first day
    # the price closes ≥ stop_pct below the running peak.
    peak = arr[0]
    threshold = peak * (1.0 - trailing_stop_pct)
    for i in range(len(arr)):
        if pd.isna(arr[i]):
            continue
        if arr[i] > peak:
            peak = arr[i]
            threshold = peak * (1.0 - trailing_stop_pct)
        if arr[i] <= threshold:
            return i
    return None


def _compute_beta(stock_series: pd.Series, market_series: pd.Series,
                  entry_date: date, lookback: int = 252) -> float | None:
    """Trailing-window OLS beta of stock returns vs market (SPY) returns,
    computed over the last `lookback` trading days strictly before
    `entry_date`. Returns None if there isn't enough overlap to estimate."""
    if stock_series is None or market_series is None:
        return None
    end_ts = pd.Timestamp(entry_date)
    pos = stock_series.index.searchsorted(end_ts, side="left")
    if pos < lookback + 2:
        return None
    window_idx = stock_series.index[pos - lookback - 1:pos]
    stk = stock_series.reindex(window_idx).pct_change().dropna()
    mkt = market_series.reindex(window_idx).pct_change().reindex(stk.index).dropna()
    aligned = pd.concat([stk, mkt], axis=1).dropna()
    if len(aligned) < lookback // 2:
        return None
    s = aligned.iloc[:, 0]
    m = aligned.iloc[:, 1]
    var_m = float(m.var())
    if var_m <= 0:
        return None
    return float(s.cov(m) / var_m)


def _rules_backtest_mirror(start: date, end: date, *,
                            min_members: int, date_field: str,
                            benchmark: str, leadership_only: bool,
                            benchmark_mode: str,
                            trailing_stop_pct: float,
                            time_stop_days: int,
                            exit_policy: str,
                            trailing_stop_mode: str,
                            owner_filter: str = "any",
                            visibility: str = "any",
                            filer_types: list[str] | None = None) -> dict:
    """Per-(member, ticker, entry) position simulator with signal-driven
    exits. See `rules_backtest` for the public interface and the precise
    exit-priority semantics."""
    db = SessionLocal()
    try:
        date_col = getattr(models.Transaction, date_field)

        # Entries: all purchases in the window matching the universe filter.
        eq = (db.query(
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
            ))
        if leadership_only:
            eq = eq.filter(_leadership_exists_filter())
        eq = _apply_owner_filter(eq, owner_filter)
        eq = _apply_filer_types(eq, filer_types)
        entries = eq.order_by(date_col).all()

        # Sales: every sale we might need to mirror — by any member who
        # bought in our window, on any ticker they bought, at any time.
        # Not restricted to the window because a sale could close a position
        # that opened late in the window.
        member_ids = list({r.member_id for r in entries})
        tickers = list({r.ticker for r in entries})
        sales_rows = []
        if member_ids and tickers:
            sq = (db.query(
                    models.Transaction.ticker,
                    date_col,
                    models.Transaction.member_id,
                )
                .filter(
                    models.Transaction.member_id.in_(member_ids),
                    models.Transaction.ticker.in_(tickers),
                    date_col.isnot(None),
                    models.Transaction.transaction_type.in_(SELL_TYPES),
                ))
            # Mirror-sell on a same-owner-class, same-filer-type sale: a
            # spouse-buy strategy should exit when the spouse sells; a
            # cabinet strategy mirrors cabinet sales, not member sales of
            # the same ticker.
            sq = _apply_owner_filter(sq, owner_filter)
            sq = _apply_filer_types(sq, filer_types)
            sales_rows = sq.order_by(date_col).all()

        # Build a lookup: (member_id, ticker) -> sorted list of sale dates.
        sales_by_key: dict[tuple, list[date]] = {}
        for s_ticker, s_date, s_member in sales_rows:
            sales_by_key.setdefault((s_member, s_ticker), []).append(s_date)

        # Conviction filter (same logic as fixed_horizon).
        events = entries
        if min_members > 1:
            by_ticker: dict[str, list] = {}
            for r in entries:
                by_ticker.setdefault(r.ticker, []).append(r)
            kept = []
            for r in entries:
                members = {
                    o.member_id for o in by_ticker[r.ticker]
                    if 0 <= (r.event_date - o.event_date).days <= 30
                }
                if len(members) >= min_members:
                    kept.append(r)
            events = kept

        # Price fetch: tickers + sector ETFs (if needed) + the calendar
        # anchor (SPY). Calendar extends well past `end` so positions
        # opened late can mature against their full time_stop window.
        # If using beta-scaled trailing stops we also need ~1 year of
        # history BEFORE `start` to estimate beta on the earliest entries.
        tickers_set = set(tickers)
        sector_lookup: dict[str, str] = {}
        if benchmark_mode == "sector":
            sector_lookup = _load_sector_etf_lookup(db)
            extra = set(sector_lookup.values())
        else:
            extra = set()
        if trailing_stop_mode == "beta_scaled":
            extra = extra | {"SPY"}  # beta is always vs SPY by convention
            fetch_start = start - timedelta(days=400)  # ~252 trading days
        else:
            fetch_start = start
        buffer_days = int(time_stop_days * 1.6) + 30
        ensure_prices(tickers_set | extra | {benchmark},
                      fetch_start, end + timedelta(days=buffer_days))

        cal_series = _close_series(db, benchmark)
        if cal_series is None:
            return {"error": f"no benchmark price data for {benchmark}"}
        cal_end_ts = pd.Timestamp(end) + timedelta(days=buffer_days)
        cal_series = cal_series[
            (cal_series.index >= pd.Timestamp(start)) &
            (cal_series.index <= cal_end_ts)
        ]
        calendar = cal_series.index

        # Per-ticker series caches.
        close_cache: dict[str, pd.Series | None] = {}
        ret_cache: dict[str, pd.Series] = {}

        def closes(t: str) -> pd.Series | None:
            if t not in close_cache:
                close_cache[t] = _close_series(db, t)
            return close_cache[t]

        def returns(t: str) -> pd.Series:
            if t not in ret_cache:
                ser = closes(t)
                ret_cache[t] = (
                    ser.pct_change().reindex(calendar) if ser is not None
                    else pd.Series(np.nan, index=calendar)
                )
            return ret_cache[t]

        port = pd.Series(0.0, index=calendar)
        n_open = pd.Series(0, index=calendar)
        sec_port = pd.Series(0.0, index=calendar)
        n_open_sec = pd.Series(0, index=calendar)

        # SPY series for beta computation (lookback-aware). Always
        # available because we requested it in ensure_prices above.
        spy_series_full = _close_series(db, "SPY") if trailing_stop_mode == "beta_scaled" else None

        positions = 0
        skipped_no_sector = 0
        exit_reasons = {"mirror_sell": 0, "trailing_stop": 0,
                        "time_stop": 0, "end_of_window": 0}
        beta_samples: list[float] = []   # diagnostic — distribution of betas used
        stop_pct_samples: list[float] = []

        for r in events:
            sector_etf = None
            if benchmark_mode == "sector":
                sector_etf = sector_lookup.get(r.ticker)
                if sector_etf is None:
                    skipped_no_sector += 1
                    continue
            entry_idx = calendar.searchsorted(pd.Timestamp(r.event_date))
            # Position opens the day AFTER the event.
            open_idx = entry_idx + 1
            if open_idx >= len(calendar):
                continue

            # Candidate exit calendar indices, smaller = sooner.
            cand: list[tuple[int, str]] = []

            # (a) mirror exit on next same-member, same-ticker sale.
            future_sales = [
                d for d in sales_by_key.get((r.member_id, r.ticker), [])
                if d > r.event_date
            ]
            if future_sales:
                mirror_date = min(future_sales)
                mirror_idx = calendar.searchsorted(pd.Timestamp(mirror_date))
                if mirror_idx < len(calendar):
                    cand.append((mirror_idx, "mirror_sell"))

            # (b) trailing stop, scanned over [open_idx, cal_end]. Skipped
            # entirely for the mirror_sell-only policy.
            if trailing_stop_mode != "none":
                ser = closes(r.ticker)
                if ser is not None:
                    if trailing_stop_mode == "beta_scaled":
                        beta = _compute_beta(ser, spy_series_full, r.event_date)
                        # Clip to [0.5, 3.0] so defensive names don't get a
                        # 7% stop and ultra-high-beta names don't get a 45%
                        # stop; fall back to 1.0 when beta can't be estimated.
                        scale = 1.0 if beta is None else max(0.5, min(beta, 3.0))
                        eff_stop = trailing_stop_pct * scale
                        beta_samples.append(beta if beta is not None else float("nan"))
                    else:
                        eff_stop = trailing_stop_pct
                    stop_pct_samples.append(eff_stop)
                    price_window = ser.reindex(calendar).iloc[open_idx:]
                    ts_offset = _find_first_exit_idx(price_window, eff_stop)
                    if ts_offset is not None:
                        cand.append((open_idx + ts_offset, "trailing_stop"))

            # (c) time stop fallback.
            ts_idx = open_idx + time_stop_days
            if ts_idx < len(calendar):
                cand.append((ts_idx, "time_stop"))

            # (d) end-of-window catch-all.
            cand.append((len(calendar) - 1, "end_of_window"))

            exit_idx, reason = min(cand, key=lambda x: x[0])
            if exit_idx <= open_idx:
                continue
            exit_reasons[reason] += 1
            positions += 1

            window = calendar[open_idx:exit_idx + 1]
            day_ret = returns(r.ticker).loc[window]
            port.loc[window] += day_ret.fillna(0.0)
            n_open.loc[window] += day_ret.notna().astype(int)
            if sector_etf is not None:
                etf_ret = returns(sector_etf).loc[window]
                sec_port.loc[window] += etf_ret.fillna(0.0)
                n_open_sec.loc[window] += etf_ret.notna().astype(int)

        daily = (port / n_open.replace(0, np.nan)).fillna(0.0)
        nav = (1 + daily).cumprod()

        if benchmark_mode == "sector":
            sec_daily = (sec_port / n_open_sec.replace(0, np.nan)).fillna(0.0)
            bench_nav = (1 + sec_daily).cumprod()
        else:
            bench_nav = (1 + cal_series.pct_change().fillna(0.0)).cumprod()

        # Mark to market at end-of-period (see note in the fixed_horizon path).
        end_idx = max(0, calendar.searchsorted(pd.Timestamp(end), side="right") - 1)
        strat_total = float(nav.iloc[end_idx] - 1) if len(nav) else 0.0
        bench_total = float(bench_nav.iloc[end_idx] - 1) if len(bench_nav) else 0.0
        invested_days = int((n_open > 0).sum())

        beta_stats = None
        if beta_samples:
            arr = np.array([b for b in beta_samples if not np.isnan(b)], dtype=float)
            if len(arr):
                beta_stats = {
                    "n": int(len(arr)),
                    "median": float(np.median(arr)),
                    "p10": float(np.percentile(arr, 10)),
                    "p90": float(np.percentile(arr, 90)),
                }
        stop_pct_stats = None
        if stop_pct_samples:
            arr = np.array(stop_pct_samples, dtype=float)
            stop_pct_stats = {
                "n": int(len(arr)),
                "median": float(np.median(arr)),
                "p10": float(np.percentile(arr, 10)),
                "p90": float(np.percentile(arr, 90)),
            }

        return {
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "params": {
                "min_members": min_members, "date_field": date_field,
                "benchmark": benchmark, "benchmark_mode": benchmark_mode,
                "leadership_only": leadership_only,
                "owner_filter": owner_filter,
                "visibility": visibility,
                "filer_types": filer_types,
                "exit_policy": exit_policy,
                "trailing_stop_mode": trailing_stop_mode,
                "trailing_stop_pct": trailing_stop_pct,
                "time_stop_days": time_stop_days,
            },
            "positions_opened": positions,
            "events_skipped_no_sector": skipped_no_sector,
            "exit_reasons": exit_reasons,
            "beta_stats": beta_stats,
            "stop_pct_stats": stop_pct_stats,
            "days_invested": invested_days,
            "trading_days": len(calendar),
            "strategy_return": round(strat_total, 4),
            "benchmark_return": round(bench_total, 4),
            "excess_return": round(strat_total - bench_total, 4),
            "max_concurrent_positions": int(n_open.max()) if len(n_open) else 0,
            "nav_curve": [
                {"date": d.date().isoformat(),
                 "strategy": round(float(s), 4),
                 "benchmark": round(float(b), 4)}
                for d, s, b in zip(calendar, nav, bench_nav)
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
                        leadership_only: bool = False,
                        benchmark_mode: str = "ticker",
                        exit_policy: str = "fixed_horizon",
                        trailing_stop_pct: float = 0.15,
                        time_stop_days: int = 252,
                        owner_filter: str = "any",
                        visibility: str = "any",
                        filer_types: list[str] | None = None) -> dict:
    """Grid-search the strategy on the training years, then evaluate the single
    best configuration once on the held-out test year. The test period is
    touched exactly once, with parameters locked from training."""
    if owner_filter not in OWNER_FILTERS:
        raise ValueError(f"unknown owner_filter: {owner_filter!r} (expected one of {OWNER_FILTERS})")
    param_grid = param_grid or PARAM_GRID
    es_train = event_study(train_start, train_end, date_field, horizons,
                           benchmark=benchmark, leadership_only=leadership_only,
                           benchmark_mode=benchmark_mode,
                           owner_filter=owner_filter,
                           visibility=visibility, filer_types=filer_types)
    es_test = event_study(test_start, test_end, date_field, horizons,
                          benchmark=benchmark, leadership_only=leadership_only,
                          benchmark_mode=benchmark_mode,
                          owner_filter=owner_filter,
                          visibility=visibility, filer_types=filer_types)

    grid = []
    for params in param_grid:
        grid.append({"params": params,
                     "result": rules_backtest(train_start, train_end,
                                              date_field=date_field,
                                              benchmark=benchmark,
                                              leadership_only=leadership_only,
                                              benchmark_mode=benchmark_mode,
                                              exit_policy=exit_policy,
                                              trailing_stop_pct=trailing_stop_pct,
                                              time_stop_days=time_stop_days,
                                              owner_filter=owner_filter,
                                              visibility=visibility,
                                              filer_types=filer_types,
                                              **params)})

    best = max(grid, key=lambda g: g["result"].get("excess_return", -1e9))
    selected_train_result = best["result"]
    out_of_sample = rules_backtest(test_start, test_end,
                                   date_field=date_field,
                                   benchmark=benchmark,
                                   leadership_only=leadership_only,
                                   benchmark_mode=benchmark_mode,
                                   exit_policy=exit_policy,
                                   trailing_stop_pct=trailing_stop_pct,
                                   time_stop_days=time_stop_days,
                                   owner_filter=owner_filter,
                                   visibility=visibility,
                                   filer_types=filer_types,
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
        "benchmark_mode": benchmark_mode,
        "leadership_only": leadership_only,
        "owner_filter": owner_filter,
        "visibility": visibility,
        "filer_types": filer_types,
        "exit_policy": exit_policy,
        "trailing_stop_pct": trailing_stop_pct,
        "time_stop_days": time_stop_days,
        "train_window": {"start": train_start.isoformat(), "end": train_end.isoformat()},
        "test_window": {"start": test_start.isoformat(), "end": test_end.isoformat()},
        "event_study": {"train": es_train, "test": es_test},
        "grid_search_train": grid_summary,
        "selected_params": best["params"],
        "selected_train_result": selected_train_result,
        "out_of_sample_test": out_of_sample,
    }
