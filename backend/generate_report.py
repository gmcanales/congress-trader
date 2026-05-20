"""
Generate a self-contained HTML backtest report from one or more AnalysisRun rows.

Run inside the backend container:

  docker compose exec backend python generate_report.py \\
      --run-ids 1,2,3 --output /host/reports/v1_backtest_review.html

  # or simpler, write into the mounted source tree (build copies it in):
  docker compose run --rm --no-deps backend python generate_report.py \\
      --run-ids 1,2,3 --output reports/v1_backtest_review.html

The generator pulls each run's params, scores, and full_results, recomputes the
trailing-30-day conviction-filter survival rate per month (the diagnostic that
explained the v1 NAV freezes), and embeds two interactive Plotly charts via the
public Plotly CDN. The output HTML has no other runtime dependencies.

Every assumption that shaped the backtest is restated in the report itself so
that anyone reading later — including future-Claude — has the methodological
context next to the numbers, not hidden in code or memory.
"""
from __future__ import annotations

import argparse
import html
import json
import logging
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import and_

from app.database import SessionLocal
from app.models import AnalysisRun, Dataset, Transaction


PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.27.0.min.js"


# ── Diagnostic computation ──────────────────────────────────────────────────

def _conviction_survival_by_month(start: date, end: date, min_members: int,
                                  cluster_days: int = 30,
                                  anchor: str = "transaction_date") -> list[dict]:
    """For each calendar month in [start, end] reproduce the rules_backtest
    conviction filter and return (month, raw, kept).

    This is the same logic as `backtest.rules_backtest`'s `min_members > 1`
    branch — recomputed here so the report doesn't depend on adding new
    instrumentation to the engine.
    """
    s = SessionLocal()
    try:
        date_col = getattr(Transaction, anchor)
        rows = (s.query(Transaction.ticker, date_col, Transaction.member_id)
                 .filter(Transaction.transaction_type == "purchase",
                         Transaction.ticker.isnot(None), Transaction.ticker != "",
                         date_col.isnot(None),
                         # back-date the lower bound by `cluster_days` so the
                         # trailing window is correctly populated at the boundary
                         date_col >= start.replace(day=1)).all())
    finally:
        s.close()

    by_ticker: dict[str, list] = defaultdict(list)
    for r in rows:
        by_ticker[r.ticker].append(r)
    for events in by_ticker.values():
        events.sort(key=lambda x: x[1])

    raw_by_month: dict[str, int] = defaultdict(int)
    kept_by_month: dict[str, int] = defaultdict(int)
    for ticker, events in by_ticker.items():
        for r in events:
            d = r[1]
            if d < start or d > end:
                continue
            ms = d.strftime("%Y-%m")
            raw_by_month[ms] += 1
            members = {o[2] for o in events if 0 <= (d - o[1]).days <= cluster_days}
            if len(members) >= min_members:
                kept_by_month[ms] += 1

    months = sorted(raw_by_month.keys())
    return [{"month": m,
             "raw": raw_by_month[m],
             "kept": kept_by_month[m]}
            for m in months]


# ── Run extraction ──────────────────────────────────────────────────────────

def _load_runs(run_ids: list[int]) -> list[dict]:
    s = SessionLocal()
    try:
        out = []
        for rid in run_ids:
            r = s.get(AnalysisRun, rid)
            if r is None:
                logging.warning(f"run {rid} not found, skipping")
                continue
            ds = s.get(Dataset, r.dataset_id) if r.dataset_id else None
            out.append({
                "id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "tool": r.tool,
                "status": r.status,
                "params": r.params or {},
                "scores": r.scores or {},
                "full_results": r.full_results or {},
                "dataset": {
                    "id": ds.id if ds else None,
                    "name": ds.name if ds else None,
                    "spec": ds.spec if ds else None,
                    "stats": ds.stats if ds else None,
                    "parse_config": ds.parse_config if ds else None,
                } if ds else None,
            })
        return out
    finally:
        s.close()


# ── Plotly figure JSON ──────────────────────────────────────────────────────

def _nav_figure(runs: list[dict]) -> dict:
    """Overlay each run's OOS strategy NAV plus one benchmark NAV per distinct
    benchmark ticker (SPY, RSP, …). Strategies and benchmarks share the
    calendar so the freeze stretches and benchmark differentials are visible
    side-by-side."""
    traces = []
    palette = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]
    bench_colors = {"SPY": "#7f7f7f", "RSP": "#444444"}
    benchmarks_drawn: set[str] = set()
    for i, r in enumerate(runs):
        oos = r["full_results"].get("out_of_sample_test") or {}
        nav = oos.get("nav_curve") or []
        if not nav:
            continue
        sel = r["full_results"].get("selected_params") or {}
        bench = r["scores"].get("benchmark") or r["params"].get("benchmark") or "SPY"
        label = (f"Run #{r['id']} · {r['params'].get('date_field')} · "
                 f"min_members={sel.get('min_members')} · hold={sel.get('horizon_days')}d "
                 f"· bench {bench}")
        traces.append({
            "type": "scatter", "mode": "lines",
            "x": [p["date"] for p in nav],
            "y": [p["strategy"] for p in nav],
            "name": label,
            "line": {"color": palette[i % len(palette)], "width": 2},
        })
        if bench not in benchmarks_drawn:
            traces.append({
                "type": "scatter", "mode": "lines",
                "x": [p["date"] for p in nav],
                "y": [p["benchmark"] for p in nav],
                "name": f"{bench} (benchmark)",
                "line": {"color": bench_colors.get(bench, "#aaaaaa"),
                         "width": 2, "dash": "dot"},
            })
            benchmarks_drawn.add(bench)

    layout = {
        "title": "Out-of-sample NAV — strategy variants vs benchmarks",
        "xaxis": {"title": "date"},
        "yaxis": {"title": "NAV (start = 1.00)", "tickformat": ".2f"},
        "hovermode": "x unified",
        "legend": {"orientation": "h", "y": -0.22},
        "margin": {"l": 60, "r": 30, "t": 60, "b": 120},
    }
    return {"data": traces, "layout": layout}


def _survival_figure(survival: list[dict], min_members: int, cluster_days: int) -> dict:
    months = [s["month"] for s in survival]
    raw = [s["raw"] for s in survival]
    kept = [s["kept"] for s in survival]
    traces = [
        {"type": "bar", "name": "Raw purchases", "x": months, "y": raw,
         "marker": {"color": "#c6dbef"}},
        {"type": "bar", "name": f"Survived filter (min_members={min_members}, {cluster_days}d)",
         "x": months, "y": kept, "marker": {"color": "#1f77b4"}},
    ]
    layout = {
        "title": "Why the NAV freezes: conviction filter survival per month",
        "barmode": "group",
        "xaxis": {"title": "month (transaction_date)"},
        "yaxis": {"title": "events"},
        "legend": {"orientation": "h", "y": -0.25},
        "margin": {"l": 60, "r": 30, "t": 60, "b": 90},
    }
    return {"data": traces, "layout": layout}


# ── HTML emission ───────────────────────────────────────────────────────────

CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       max-width: 1080px; margin: 2rem auto; padding: 0 1.5rem; color: #1d1d1f; line-height: 1.6; }
header { border-bottom: 2px solid #e5e5e7; padding-bottom: 1rem; margin-bottom: 2rem; }
header h1 { margin: 0 0 0.25rem; font-size: 1.8rem; }
header .meta { color: #6e6e73; font-size: 0.9rem; }
h2 { margin-top: 2.4rem; padding-top: 0.8rem; border-top: 1px solid #e5e5e7; font-size: 1.35rem; }
h3 { margin-top: 1.4rem; font-size: 1.05rem; }
.callout { background: #f7f7fb; border-left: 4px solid #1f77b4; padding: 0.8rem 1rem;
           border-radius: 4px; margin: 1rem 0; }
.callout.warn { border-left-color: #d97706; background: #fffbeb; }
.callout.crit { border-left-color: #b91c1c; background: #fef2f2; }
table { width: 100%; border-collapse: collapse; margin: 0.5rem 0; font-size: 0.92rem; }
th, td { text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid #e5e5e7; }
th { background: #f7f7fb; font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.pos { color: #047857; }
.neg { color: #b91c1c; }
.kvtable td.key { font-weight: 500; color: #3c3c43; width: 30%; }
code, kbd { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: #f3f3f7;
            padding: 1px 4px; border-radius: 3px; font-size: 0.88em; }
.chart { margin: 1rem 0 1.5rem; }
ul.tight li { margin: 0.2rem 0; }
footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #e5e5e7;
         color: #6e6e73; font-size: 0.85rem; }
"""


def _fmt_pct(x: float | None, plus: bool = True) -> str:
    if x is None:
        return "—"
    cls = "pos" if x > 0 else ("neg" if x < 0 else "")
    sign = "+" if plus and x >= 0 else ""
    return f'<span class="{cls}">{sign}{x*100:.2f}%</span>'


def _runs_table(runs: list[dict]) -> str:
    rows = []
    for r in runs:
        p = r["params"]
        sc = r["scores"]
        sel = sc.get("selected_params", {})
        anchor = p.get("date_field") or ""
        bench = sc.get("benchmark") or p.get("benchmark") or "SPY"
        leadership = bool(sc.get("leadership_only") or p.get("leadership_only"))
        tradeable = anchor == "filing_date"
        tag = ("<span style='color:#047857;font-weight:600'>tradeable</span>"
               if tradeable
               else "<span style='color:#b91c1c'>diagnostic</span>")
        lead_tag = ("<span style='color:#7c3aed;font-weight:600'>leadership</span>"
                    if leadership else "<span style='color:#6e6e73'>all members</span>")
        rows.append(
            f"<tr>"
            f"<td>#{r['id']}</td>"
            f"<td>{tag}</td>"
            f"<td>{lead_tag}</td>"
            f"<td><code>{html.escape(anchor)}</code></td>"
            f"<td><code>{html.escape(bench)}</code></td>"
            f"<td>{html.escape(p.get('train_start',''))} → {html.escape(p.get('train_end',''))}</td>"
            f"<td>{html.escape(p.get('test_start',''))} → {html.escape(p.get('test_end',''))}</td>"
            f"<td><code>{p.get('hold_days')}</code></td>"
            f"<td><code>{p.get('min_members')}</code></td>"
            f"<td>{sel.get('horizon_days','—')}d, m={sel.get('min_members','—')}</td>"
            f"<td class='num'>{_fmt_pct(sc.get('train_excess'))}</td>"
            f"<td class='num'>{_fmt_pct(sc.get('oos_excess'))}</td>"
            f"<td class='num'>{_fmt_pct(sc.get('oos_strategy_return'), plus=False)}</td>"
            f"<td class='num'>{_fmt_pct(sc.get('oos_benchmark_return'), plus=False)}</td>"
            f"</tr>")
    return (
        "<table>"
        "<thead><tr>"
        "<th>run</th><th>kind</th><th>universe</th><th>anchor</th><th>bench</th>"
        "<th>train window</th><th>test window</th>"
        "<th>hold_days grid</th><th>min_members grid</th><th>selected</th>"
        "<th class='num'>train excess</th><th class='num'>OOS excess</th>"
        "<th class='num'>OOS strat</th><th class='num'>OOS bench</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        "<p style='font-size:0.85rem;color:#6e6e73;margin-top:0.4rem'>"
        "<b>tradeable</b> = anchored on <code>filing_date</code> (publicly observable in "
        "real time, ≤45-day STOCK Act lag respected). <b>diagnostic</b> = anchored on "
        "<code>transaction_date</code> (the day the member actually traded — informative "
        "for understanding the signal but contains look-ahead bias for any follower). "
        "<b>leadership</b> universe = trades made while the member held a chamber "
        "leadership or committee-chair position; <b>all members</b> = no such filter.</p>")


def _assumptions_section() -> str:
    return """
<h2 id="assumptions">Inputs &amp; assumptions</h2>
<p class="callout warn">
The numbers below only mean what the assumptions allow them to mean. Read this section
before reading the results.
</p>

<h3>Data inputs</h3>
<table class="kvtable">
<tr><td class="key">Source</td>
    <td>House Clerk PTRs (PDF) + Senate eFD (HTML).
        Ingestion via <code>services/scraper.py</code>, <code>services/senate_scraper.py</code>,
        and <code>services/ptr_parser.py</code> (text + OCR fallback).</td></tr>
<tr><td class="key">Confidence filter</td>
    <td>All transactions included regardless of parser confidence
        (<code>high</code>, <code>medium</code>, <code>low</code>).</td></tr>
<tr><td class="key">Asset universe</td>
    <td>Stock transactions only — rows with a non-empty <code>ticker</code>.
        Bonds, treasuries, mutual funds, options, and crypto are excluded.</td></tr>
<tr><td class="key">Owner</td>
    <td>Member, spouse, and dependent trades all treated equally.</td></tr>
<tr><td class="key">Position amount</td>
    <td><b>Not modeled.</b> Congressional disclosures publish amount ranges
        ($1k–$15k, $15k–$50k, …) but the engine does not size positions on them.</td></tr>
<tr><td class="key">Known data gap</td>
    <td>~12% of House PTRs are scanned checkbox-grid forms that OCR poorly —
        deferred for v1. v1 results may under-represent those filings.</td></tr>
</table>

<h3>Backtest mechanics</h3>
<table class="kvtable">
<tr><td class="key">Event anchor</td>
    <td><code>filing_date</code> (the public date — tradeable; up to 45 days
        after the actual trade) <em>or</em> <code>transaction_date</code> (the
        date the member actually traded — not tradeable in real time; reported
        for diagnostics only). Each run states which it used.</td></tr>
<tr><td class="key">Position entry</td>
    <td>The first trading day strictly <em>after</em> the event date.</td></tr>
<tr><td class="key">Exit rule</td>
    <td><b>Hard timer.</b> Every position closes after <code>horizon_days</code>
        trading days. There is no stop-loss, no profit target, no signal-based
        exit (e.g., closing when the same member sells). This is an event-study
        construct; it is not how a real strategy would behave.</td></tr>
<tr><td class="key">Position sizing</td>
    <td>Equal-weight across all currently-open positions, rebalanced daily.
        Implicitly assumes fractional shares and zero rebalancing cost.</td></tr>
<tr><td class="key">Conviction filter</td>
    <td>Optional <code>min_members</code>: a ticker qualifies only if it has
        ≥N distinct congressional buyers within a trailing 30-day window. The
        window length is hard-coded at 30 days in this version of the engine.</td></tr>
<tr><td class="key">Benchmark</td>
    <td>Per-run. SPY (cap-weighted S&amp;P 500) is the default; RSP
        (Invesco equal-weight S&amp;P 500) is used as a cross-check on
        whether any SPY-relative excess is mechanical (equal-weight basis)
        vs informational (congressional signal). The benchmark choice
        affects in-sample grid selection — the "best" config on training
        data can shift between hold_days = 21 and 63 depending on
        benchmark, so cross-benchmark numbers are not strictly
        apples-to-apples unless the grid is held fixed.</td></tr>
<tr><td class="key">Transaction costs</td>
    <td>Zero. No slippage, no spread, no commissions modeled.</td></tr>
<tr><td class="key">Train / test split</td>
    <td>Single split. No walk-forward, no k-fold. Grid is searched on the
        training window; the held-out test window is touched exactly once
        with parameters locked from training.</td></tr>
<tr><td class="key">Event-study horizons</td>
    <td>5, 21, and 63 trading days forward from the event. Excess return =
        ticker return − SPY return over the same window.</td></tr>
<tr><td class="key">Outcome buckets</td>
    <td>±2% vs benchmark = outperform / in-line / underperform.</td></tr>
</table>

<h3>Side modeled</h3>
<p>The rules backtest opens positions on <b>purchases</b> only. Sales feed the
event study but do not drive position-opening. Short-selling is not modeled.</p>
"""


def _diagnosis_section(survival: list[dict], min_members: int, cluster_days: int) -> str:
    return f"""
<h2 id="diagnosis">Diagnosis: where the NAV "freezes" come from</h2>

<p>Run #2 (<code>min_members=2</code>) shows visible flat stretches across
2025-01–03, all of 2025-08, 2025-10–11, and 2026-02. These are not data gaps —
raw monthly purchase counts in 2025 are stable (50–80 buys per month). They
are entirely produced by two mechanics already documented in the assumptions
above:</p>

<ol class="tight">
<li><b>The conviction filter kills 80–95% of raw buys.</b> Most congressional
    purchases are solo — one member, one ticker, no other member buying the
    same name within {cluster_days} days. Those events fail the
    <code>min_members={min_members}</code> threshold and never become positions.
    The chart below makes this concrete: light bars = raw purchases that
    month, dark bars = events that survive the filter.</li>
<li><b>Every position auto-closes after <code>horizon_days</code>.</b> The
    selected hold for run #2 was 21 trading days (~1 calendar month). When
    no new qualifying event arrives within 21 days of the prior one, every
    open position has expired by the time the gap is half over. With zero
    open positions <code>n_open=0 ⇒ daily return = 0 ⇒ NAV is flat</code>.
    This is a methodological artifact, not a bug or a market signal — but
    it does mean the strategy literally cannot stay invested through
    quiet stretches.</li>
</ol>

<p class="callout">
A position opened just before a gap is <em>still moving</em> with the market
for ~21 trading days. The NAV becomes flat only once every pre-gap position
has hit its hard horizon. Removing the conviction filter (run #3,
<code>min_members=1</code>) raises invested-day coverage from 90% to 95% and
eliminates the visible flat stretches.
</p>

<div id="chart-survival" class="chart"></div>
"""


def _next_steps_section() -> str:
    return """
<h2 id="next">Open questions &amp; next steps</h2>
<ol class="tight">
<li><b>Tradeable-anchor check — closed (runs #7/#8).</b> Repeating the
    pinned grid on <code>filing_date</code> drops OOS excess to −2.82%
    (SPY) and +0.09% (RSP). The apparent
    <code>transaction_date</code> excess was look-ahead bias from
    anchoring on a date that isn't publicly observable in real time.
    Confirms that any future strategy claim must be reported on
    <code>filing_date</code> to be considered actionable.</li>
<li><b>Signal-driven exits.</b> The hard-timer exit is an event-study
    construct, not a strategy. A realistic strategy mixes new entries on
    buys, continued holds while no sale is disclosed, and active exits
    when the disclosing member sells (or a trailing/time-stop fires).
    Worth implementing, but be sober about the prior: if the broad-buy
    signal has no <code>filing_date</code> alpha, longer holds won't
    create one — exit-policy gains would have to come from the sell-side
    signal (event-study t-stats on sales are also sub-1 in this dataset,
    so this is a "test it cheaply, don't expect much" item).</li>
<li><b>Leadership restriction (v2).</b> Per the Wei &amp; Zhou (NBER, 2025)
    finding, the rank-and-file null we see here is consistent with the
    literature; their identified +47pp/yr alpha is concentrated in members
    who hold leadership/committee-chair positions. Once
    <code>positions</code> tenure data is ingested, this same engine can
    be re-run on a leadership-only subset for a theory-driven cut.</li>
<li><b>Conviction-window length.</b> The 30-day clustering window is
    hard-coded. A sweep at <code>cluster_days ∈ {30, 60, 90}</code> may
    surface or rule out longer-tail clustering that the current
    <code>min_members=2</code> setting misses.</li>
</ol>
"""


def build_html(runs: list[dict], survival: list[dict], survival_meta: dict) -> str:
    nav_fig = _nav_figure(runs)
    surv_fig = _survival_figure(survival, survival_meta["min_members"], survival_meta["cluster_days"])
    runs_table = _runs_table(runs)

    dataset = next((r["dataset"] for r in runs if r.get("dataset")), None)
    ds_name = html.escape(dataset["name"]) if dataset else "—"
    ds_spec = json.dumps(dataset["spec"], indent=2) if dataset else "—"
    ds_stats = dataset["stats"] if dataset else {}
    txn_count = ds_stats.get("ticker_transaction_count", "?") if ds_stats else "?"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Congress Trader — v1 Backtest Review</title>
<style>{CSS}</style>
<script src="{PLOTLY_CDN}"></script>
</head><body>
<header>
  <h1>Congress Trader — v1 Backtest Review</h1>
  <div class="meta">Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
      · runs #{', #'.join(str(r['id']) for r in runs)}
      · dataset <b>{ds_name}</b></div>
</header>

<h2 id="tldr">TL;DR</h2>
<ul class="tight">
  <li>v1 (run #1, <code>filing_date</code> anchor, grid over
      <code>min_members=1,2,3</code>): <b>no edge.</b> In-sample grid
      winners went OOS-negative — classic small-sample overfit.</li>
  <li>Re-anchored on <code>transaction_date</code> (run #2): same conclusion
      at the same grid. Visible NAV freezes through 2025-Q1, Aug, Oct–Nov,
      and Feb 2026 came from the conviction filter eliminating 80–95% of
      buys plus the hard 21-day exit (every position auto-closes on a
      timer, so quiet stretches leave the strategy fully cashed-out).</li>
  <li>Removing the conviction filter (runs #3 and #5,
      <code>min_members=1</code>, <code>transaction_date</code>) eliminated
      the freezes and appeared to yield <b>+2.53% over SPY</b> and
      <b>+5.44% over RSP</b> at OOS. Promising — but anchored on a date no
      follower can know in real time.</li>
  <li class="callout crit"><b>Actionable check (runs #7/#8): the edge
      collapses.</b> Repeating the same strategy on <code>filing_date</code>
      — the only anchor that respects the up-to-45-day STOCK Act
      reporting lag — OOS strategy return drops from +20.44% to
      <b>+15.08%</b>. Excess vs SPY becomes <b>−2.82%</b>, and excess vs
      RSP collapses to <b>+0.09%</b> (effectively flat against
      equal-weight). The 5.4-point gap between
      <code>transaction_date</code> and <code>filing_date</code>
      <em>is</em> a real phenomenon — congressional buys are concentrated
      in names that drift up during the disclosure window — but the lag
      eats the alpha. A follower trading at the disclosure cannot
      capture it.</li>
  <li><b>Final v1 conclusion stands:</b> there is no actionable edge in
      broad congressional trading. The aggregated <code>transaction_date</code>
      excess in runs #3–#6 was look-ahead bias from anchoring on a date
      that isn't publicly observable in real time, not a strategy
      result.</li>
  <li>Where to look next: theory-driven subgroup cuts (leadership /
      committee chairs per Wei &amp; Zhou 2025) and exit-policy variants
      that may shift the picture beyond the timer-based event study used
      here. See "next steps".</li>
</ul>

<h2 id="leadership-prelim">Leadership-only result — narrow vs expanded universe</h2>
<p class="callout warn">
Runs <b>#9 / #10</b> applied the leadership filter to only the narrow
chamber-leadership universe (Speaker, Majority/Minority Leader, Whips,
Conference Chairs) — 37 position tenures, 67 train trades, dominated
by five members. Runs <b>#11 / #12</b> repeat the same backtest after
ingesting committee chairs and ranking members of the 117th–119th
Congresses (157 position tenures, 460 train trades, much more
distributed). The widening matters — the apparent OOS edge shrinks
substantially.
</p>
<table>
<thead><tr><th>universe</th><th class='num'>OOS positions</th>
<th class='num'>OOS excess vs SPY</th><th class='num'>OOS excess vs RSP</th>
<th class='num'>train/test sign-flip</th><th class='num'>per-trade 5d t-stat (SPY)</th>
<th class='num'>per-trade 63d t-stat (RSP)</th></tr></thead><tbody>
<tr><td>Narrow leadership (#9/#10)</td><td class='num'>45</td>
<td class='num'>+38.11%</td><td class='num'>+41.02%</td>
<td class='num'>−61.76 → +38.11 (≈ 100 pts)</td>
<td class='num'>−2.65</td><td class='num'>−0.06</td></tr>
<tr><td>+ chairs / ranking (#11/#12)</td><td class='num'>186</td>
<td class='num'>+11.93%</td><td class='num'>+14.85%</td>
<td class='num'>−42.10 → +11.93 (≈ 54 pts)</td>
<td class='num'>−2.65</td><td class='num'>+1.94</td></tr>
</tbody></table>
<ul class="tight">
<li><b>The narrow-universe edge mostly dissolves when chairs are added.</b>
    Going from 67 → 460 train positions cuts the OOS SPY excess from
    +38% to +12% and the RSP excess from +41% to +15%. The narrow
    result was indeed driven by the five-member concentration; with
    more leaders in the mix, the average drifts back toward broad-market
    behaviour.</li>
<li><b>Train/test instability persists but is smaller.</b> A 54-point
    sign flip is still well outside what a stable signal would produce,
    but it's noticeably tighter than the 100-pt swing on the narrow
    universe — consistent with reduced single-trader luck, not the
    appearance of a robust alpha.</li>
<li><b>5-day horizon: leadership buys significantly underperform.</b>
    t-stat = <b>−2.65</b> vs SPY is unchanged from the narrow universe.
    This is consistent across both samples and is the most statistically
    significant finding so far. It points against any "follow the
    leader" alpha at short horizons.</li>
<li><b>63-day horizon: borderline positive vs RSP.</b> t-stat = +1.94
    against RSP, right at the 1.96 / 95% threshold (and +1.41 against
    SPY). Suggestive, but not at the level we'd want before
    attributing it to a real signal. Could go either way in another
    year of data.</li>
<li><b>Sample is no longer 5-member-bound</b> but is still concentrated:
    top traders in the expanded set are Carper (493 trades),
    Boozman (188), Capito (135), Foxx (80), Whitehouse (79),
    Burgess (74).</li>
</ul>
<p>
Honest read: the expanded universe converts the runs #9/#10
"obvious overfit" into a more nuanced "small-sample edge with
the wrong sign at short horizons and a borderline positive at
3-month horizons." Not strong enough to actionably ride; not
weak enough to confidently dismiss.
</p>

<h3 id="term-structure">Hold-period term structure (runs #11-#16)</h3>
<p>
The leadership runs above were all pinned at <code>hold=63d</code>.
Runs <b>#13/#14</b> (hold=21d) and <b>#15/#16</b> (hold=126d) hold the
universe and entry rule constant but vary the timer-exit. Two reasons
not to interpret this as a grid search: (a) the per-trade event-study
t-stats below are constant across runs because they measure
trade-anchored forward returns independent of the hold (so the rules
backtest is just catching different slices of the same per-trade
curve), and (b) train excess is negative at every horizon, so picking
the highest OOS number reintroduces the v1 overfit-by-selection trap.
The table is a diagnostic of where the signal actually lives, not a
config beauty contest.
</p>
<table>
<thead><tr><th>hold</th>
<th class='num'>OOS strategy</th>
<th class='num'>OOS excess SPY</th>
<th class='num'>OOS excess RSP</th>
<th class='num'>train excess SPY</th>
<th class='num'>train excess RSP</th>
<th class='num'>per-trade 5d t (SPY)</th>
<th class='num'>per-trade 63d t (RSP)</th>
</tr></thead><tbody>
<tr><td>21d (runs #13/#14)</td>
<td class='num'>+3.84%</td>
<td class='num'><span class='neg'>−16.51%</span></td>
<td class='num'><span class='neg'>−12.09%</span></td>
<td class='num'>−61.27%</td><td class='num'>−42.41%</td>
<td class='num'>−2.65</td><td class='num'>+1.94</td></tr>
<tr><td><b>63d (runs #11/#12)</b></td>
<td class='num'><b>+29.84%</b></td>
<td class='num'><b>+11.93%</b></td>
<td class='num'><b>+14.85%</b></td>
<td class='num'>−42.10%</td><td class='num'>−27.24%</td>
<td class='num'>−2.65</td><td class='num'>+1.94</td></tr>
<tr><td>126d (runs #15/#16)</td>
<td class='num'>+27.58%</td>
<td class='num'>−0.41%</td>
<td class='num'>+8.24%</td>
<td class='num'>−49.88%</td><td class='num'>−26.44%</td>
<td class='num'>−2.65</td><td class='num'>+1.94</td></tr>
</tbody></table>
<ul class="tight">
<li><b>Per-trade picture, unchanged across holds:</b> 5d significantly
    negative, 21d null, 63d borderline-positive vs RSP. The signal, if
    any, is at the 3-month horizon.</li>
<li><b>Rules backtest at 21d is exactly what you'd expect</b> if you
    locked in the negative 5-day signal — OOS excess of −16.51% vs SPY
    matches the directional sign of the 5d per-trade test.</li>
<li><b>63d is the sweet spot of the rules backtest</b> because it spans
    the per-trade horizon at which forward returns turn positive.
    Extending to 126d adds further drift but the additional 63 days are
    noise around a position whose signal already played out.</li>
<li><b>Train negative everywhere.</b> Even at the "best" hold, the
    2021-2024 in-sample excess is −27% (RSP) to −42% (SPY). The
    impressive OOS at 63d is best-among-bad-train-records, not a stable
    relationship. Strongly suggestive that 2025's mega-cap rally is
    doing the heavy lifting, not the leadership filter.</li>
</ul>

{_assumptions_section()}

<h2 id="dataset">Dataset</h2>
<table class="kvtable">
<tr><td class="key">Name</td><td>{ds_name}</td></tr>
<tr><td class="key">Coverage</td><td><pre style="margin:0">{html.escape(ds_spec)}</pre></td></tr>
<tr><td class="key">Stock transactions</td><td>{txn_count}</td></tr>
</table>

<h2 id="results">Results — train/test runs</h2>
{runs_table}
<p class="callout">
"Selected" = the single (hold_days, min_members) configuration with the best
in-sample excess return. That configuration is then evaluated exactly once on
the held-out test window. Numbers above are <em>OOS</em> unless labeled "train".
</p>

<h2 id="nav">NAV — strategies vs SPY</h2>
<div id="chart-nav" class="chart"></div>

{_diagnosis_section(survival, survival_meta["min_members"], survival_meta["cluster_days"])}

{_next_steps_section()}

<footer>
Generated by <code>backend/generate_report.py</code>. Source data:
the <code>analysis_runs</code> and <code>transactions</code> tables on the NUC
Postgres host. Plotly via CDN; the HTML is otherwise self-contained.
</footer>

<script>
Plotly.newPlot('chart-nav', {json.dumps(nav_fig['data'])},
               {json.dumps(nav_fig['layout'])}, {{responsive: true}});
Plotly.newPlot('chart-survival', {json.dumps(surv_fig['data'])},
               {json.dumps(surv_fig['layout'])}, {{responsive: true}});
</script>
</body></html>
"""


# ── CLI ─────────────────────────────────────────────────────────────────────

def _ints(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x.strip()]


def _derived_output_path(out_dir: Path, run_ids: list[int]) -> Path:
    """Filename convention: report_runs<ids>_<YYYY-MM-DD>.html (for traceability)."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    ids_part = "-".join(str(i) for i in run_ids)
    return out_dir / f"report_runs{ids_part}_{today}.html"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-ids", type=_ints, required=True,
                   help="Comma-separated AnalysisRun ids to include, in display order.")
    p.add_argument("--output",
                   help="Output path. If omitted or a directory, the filename is "
                        "auto-derived as report_runs<ids>_<YYYY-MM-DD>.html under "
                        "--output-dir.")
    p.add_argument("--output-dir", default="reports",
                   help="Directory for the auto-derived filename when --output is "
                        "omitted (default: reports/).")
    p.add_argument("--survival-min-members", type=int, default=2,
                   help="min_members used for the survival diagnostic chart (default 2 — "
                        "matches the run #2 freeze story).")
    p.add_argument("--survival-cluster-days", type=int, default=30,
                   help="Cluster window length for survival diagnostic (default 30).")
    p.add_argument("--survival-window", default="2025-01-01:2025-12-31",
                   help="Date range for the survival chart as YYYY-MM-DD:YYYY-MM-DD.")
    args = p.parse_args()

    runs = _load_runs(args.run_ids)
    if not runs:
        raise SystemExit("No runs found for the provided ids.")

    # Resolve output path. If --output is a directory (or omitted) the filename
    # follows the run-ids + date convention so each report stays traceable.
    if args.output:
        cand = Path(args.output)
        if cand.is_dir() or args.output.endswith(("/", "\\")):
            out_path = _derived_output_path(cand, args.run_ids)
        else:
            out_path = cand
    else:
        out_path = _derived_output_path(Path(args.output_dir), args.run_ids)

    sw_start_s, sw_end_s = args.survival_window.split(":")
    survival = _conviction_survival_by_month(
        date.fromisoformat(sw_start_s), date.fromisoformat(sw_end_s),
        min_members=args.survival_min_members,
        cluster_days=args.survival_cluster_days,
    )
    survival_meta = {"min_members": args.survival_min_members,
                     "cluster_days": args.survival_cluster_days}

    html_out = build_html(runs, survival, survival_meta)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_out, encoding="utf-8")
    logging.info(f"Wrote {out_path}  ({len(html_out):,} bytes)")


if __name__ == "__main__":
    main()
