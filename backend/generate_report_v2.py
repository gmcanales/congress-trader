"""
Report generator v2 — strategy-framework focused.

The v1 generator (`generate_report.py`) tells the alpha-hunting story:
"is there a signal?". It catalogs every backtest from #1 onward, including
runs #1-22 which used the pre-MTM-fix `cal_end` convention and aren't
directly comparable to the corrected runs.

v2 refocuses around the strategy framework that emerged once Phase 1
landed:

  1. The engine supports four exit policies (fixed_horizon, mirror_sell,
     mirror+15%stop, mirror+β stop) and the recommendation system can
     pick among them.
  2. The Bayesian model already told us the per-trade leadership effect
     isn't credible. Strategy choice is about realized-return capture,
     not alpha discovery.
  3. Two defensible defaults emerged: mirror+β stop for broad-equity
     risk-managed exposure, mirror_sell-only on the leadership subset
     for higher-conviction concentration.

v2 only includes runs on the corrected MTM convention (#23+). Pre-fix
runs (#1-22) are visible in `generate_report.py` v1 output if a reader
needs the historical narrative.

Run:

  docker compose run --rm --no-deps \\
      -v "$(pwd)/reports:/app/reports" backend \\
      python generate_report_v2.py --run-ids 23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38 \\
      --output-dir /app/reports

Default output filename:
  reports/strategy_report_runs<ids>_<YYYY-MM-DD>.html
"""
from __future__ import annotations

import argparse
import html
import json
import logging
from datetime import date, datetime
from pathlib import Path

from app.database import SessionLocal
from app.models import AnalysisRun, Dataset


PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.27.0.min.js"
EXIT_LABELS = {
    "fixed_horizon": "fixed_horizon",
    "mirror_sell": "mirror_only",
    "mirror_sell_with_trailing_stop": "mirror+15%stop",
    "mirror_sell_with_beta_trailing_stop": "mirror+β stop",
}


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
                "tool": r.tool,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "params": r.params or {},
                "scores": r.scores or {},
                "full_results": r.full_results or {},
                "dataset": ({"id": ds.id, "name": ds.name, "spec": ds.spec,
                             "stats": ds.stats} if ds else None),
            })
        return out
    finally:
        s.close()


def _fmt_pct(x: float | None, plus: bool = True) -> str:
    if x is None:
        return "—"
    cls = "pos" if x > 0 else ("neg" if x < 0 else "")
    sign = "+" if plus and x >= 0 else ""
    return f'<span class="{cls}">{sign}{x*100:.2f}%</span>'


# ── Plotly figure: NAV across the matrix ────────────────────────────────────

def _nav_figure(runs: list[dict]) -> dict:
    """One trace per (exit_policy, universe) at SPY benchmark, plus the SPY
    line itself. Filters to SPY runs to keep the plot readable; the RSP
    runs are visible in the table.
    """
    traces = []
    palette = {
        "fixed_horizon":                          "#94a3b8",
        "mirror_sell":                            "#0ea5e9",
        "mirror_sell_with_trailing_stop":         "#ef4444",
        "mirror_sell_with_beta_trailing_stop":    "#10b981",
    }
    bench_drawn = False
    for r in runs:
        p = r["params"]
        sc = r["scores"]
        if sc.get("benchmark") != "SPY":
            continue
        oos = r["full_results"].get("out_of_sample_test") or {}
        nav = oos.get("nav_curve") or []
        if not nav:
            continue
        exit_policy = sc.get("exit_policy") or "fixed_horizon"
        univ = "leadership" if sc.get("leadership_only") else "all"
        label = f"#{r['id']}  {EXIT_LABELS.get(exit_policy, exit_policy)} · {univ}"
        traces.append({
            "type": "scatter", "mode": "lines",
            "x": [d["date"] for d in nav],
            "y": [d["strategy"] for d in nav],
            "name": label,
            "line": {"color": palette.get(exit_policy, "#444444"),
                     "width": 1.6,
                     "dash": "solid" if univ == "all" else "dot"},
        })
        if not bench_drawn:
            traces.append({
                "type": "scatter", "mode": "lines",
                "x": [d["date"] for d in nav],
                "y": [d["benchmark"] for d in nav],
                "name": "SPY (benchmark)",
                "line": {"color": "#1f2937", "width": 2, "dash": "dash"},
            })
            bench_drawn = True

    return {
        "data": traces,
        "layout": {
            "title": "OOS strategy NAV — every exit policy × universe (SPY-benchmark runs)",
            "xaxis": {"title": "date"},
            "yaxis": {"title": "NAV (start = 1.00)", "tickformat": ".2f"},
            "hovermode": "x unified",
            "legend": {"orientation": "h", "y": -0.20},
            "margin": {"l": 60, "r": 30, "t": 60, "b": 130},
        }
    }


# ── Strategy matrix table ───────────────────────────────────────────────────

def _matrix_table(runs: list[dict]) -> str:
    """One row per (exit_policy, universe). Aggregates SPY + RSP columns
    side-by-side."""
    rows: dict[tuple, dict] = {}
    for r in runs:
        sc = r["scores"]
        if not sc:
            continue
        exit_policy = sc.get("exit_policy", "fixed_horizon")
        univ = "leadership" if sc.get("leadership_only") else "all members"
        bench = sc.get("benchmark") or "SPY"
        oos = r["full_results"].get("out_of_sample_test") or {}
        key = (exit_policy, univ)
        if key not in rows:
            rows[key] = {
                "ids": [], "strategy_return": None,
                "spy": None, "rsp": None,
                "spy_excess": None, "rsp_excess": None,
                "train_spy_excess": None, "train_rsp_excess": None,
                "positions": oos.get("positions_opened"),
                "exit_reasons": oos.get("exit_reasons"),
            }
        rows[key]["ids"].append(r["id"])
        if rows[key]["strategy_return"] is None:
            rows[key]["strategy_return"] = sc.get("oos_strategy_return")
        if bench == "SPY":
            rows[key]["spy"] = sc.get("oos_benchmark_return")
            rows[key]["spy_excess"] = sc.get("oos_excess")
            rows[key]["train_spy_excess"] = sc.get("train_excess")
        elif bench == "RSP":
            rows[key]["rsp"] = sc.get("oos_benchmark_return")
            rows[key]["rsp_excess"] = sc.get("oos_excess")
            rows[key]["train_rsp_excess"] = sc.get("train_excess")

    # Order: fixed_horizon, mirror_sell, mirror+15%, mirror+β  × all, leadership
    order = ["fixed_horizon", "mirror_sell",
             "mirror_sell_with_trailing_stop",
             "mirror_sell_with_beta_trailing_stop"]
    body = []
    best_spy = max((v["spy_excess"] for v in rows.values() if v["spy_excess"] is not None),
                   default=None)
    best_rsp = max((v["rsp_excess"] for v in rows.values() if v["rsp_excess"] is not None),
                   default=None)
    for ep in order:
        for univ in ("all members", "leadership"):
            key = (ep, univ)
            v = rows.get(key)
            if v is None:
                continue
            highlight = ""
            if (v["spy_excess"] == best_spy or v["rsp_excess"] == best_rsp):
                highlight = " style='background:#ecfdf5'"
            ids = ",".join(f"#{i}" for i in sorted(v["ids"]))
            body.append(
                f"<tr{highlight}>"
                f"<td>{EXIT_LABELS.get(ep, ep)}</td>"
                f"<td>{univ}</td>"
                f"<td><code>{ids}</code></td>"
                f"<td class='num'>{v['positions']}</td>"
                f"<td class='num'>{_fmt_pct(v['strategy_return'], plus=False)}</td>"
                f"<td class='num'>{_fmt_pct(v['spy_excess'])}</td>"
                f"<td class='num'>{_fmt_pct(v['rsp_excess'])}</td>"
                f"<td class='num'>{_fmt_pct(v['train_spy_excess'])}</td>"
                f"</tr>")
    return (
        "<table>"
        "<thead><tr>"
        "<th>exit policy</th><th>universe</th><th>runs</th>"
        "<th class='num'>positions</th>"
        "<th class='num'>OOS strat return</th>"
        "<th class='num'>OOS excess vs SPY</th>"
        "<th class='num'>OOS excess vs RSP</th>"
        "<th class='num'>Train excess vs SPY</th>"
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table>")


# ── Bayesian recap (short) ─────────────────────────────────────────────────

def _bayes_recap(bayes_runs: list[dict]) -> str:
    if not bayes_runs:
        return ""
    r = bayes_runs[0]
    sc = r["scores"]
    p_lead = sc.get("p_leadership_gt_0")
    lead_mean = sc.get("leadership_mean")
    lead_lo = sc.get("leadership_hdi_3")
    lead_hi = sc.get("leadership_hdi_97")
    return f"""
<h2 id='bayes'>What the data actually supports</h2>
<p>
The Bayesian hierarchical model (run #{r['id']}, PyMC, NUTS sampling)
regressed per-trade 63-day filing-date excess return on
<code>leadership_at_trade</code> + <code>is_post_trifecta</code> +
<code>in_majority</code> + <code>party_R</code> + sector dummies, with
per-member random intercepts. Headline:
</p>
<p class='callout'>
<b>P(leadership effect &gt; 0 | data, controls) =
{p_lead:.3f}</b>.
Posterior mean is {_fmt_pct(lead_mean)} per 63-day window with a 94% HDI
of [{_fmt_pct(lead_lo)}, {_fmt_pct(lead_hi)}] — straddling zero. The
rules-backtest \"leadership edge\" decomposes into a credibly-positive
<code>is_post_trifecta</code> coefficient (+1.25%, P=0.90) and credibly-
positive Energy sector (+3.7%, P=0.99). The leadership filter just
happens to overlap with the trifecta window and the sector-tilted
2025 cohort.
</p>
<p>
Strategy choice in this report is about <em>capturing more of the
realized return</em> with a defensible exit policy. The Bayesian result
means we don't claim alpha beyond what the regressors above support.
</p>
"""


# ── HTML emission ──────────────────────────────────────────────────────────

CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       max-width: 1100px; margin: 2rem auto; padding: 0 1.5rem; color: #1d1d1f; line-height: 1.6; }
header { border-bottom: 2px solid #e5e5e7; padding-bottom: 1rem; margin-bottom: 2rem; }
header h1 { margin: 0 0 0.25rem; font-size: 1.8rem; }
header .meta { color: #6e6e73; font-size: 0.9rem; }
h2 { margin-top: 2.4rem; padding-top: 0.8rem; border-top: 1px solid #e5e5e7; font-size: 1.35rem; }
h3 { margin-top: 1.4rem; font-size: 1.05rem; }
.callout { background: #f7f7fb; border-left: 4px solid #1f77b4; padding: 0.8rem 1rem;
           border-radius: 4px; margin: 1rem 0; }
.callout.warn { border-left-color: #d97706; background: #fffbeb; }
.callout.crit { border-left-color: #b91c1c; background: #fef2f2; }
.callout.good { border-left-color: #047857; background: #ecfdf5; }
table { width: 100%; border-collapse: collapse; margin: 0.5rem 0; font-size: 0.92rem; }
th, td { text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid #e5e5e7; }
th { background: #f7f7fb; font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.pos { color: #047857; }
.neg { color: #b91c1c; }
code, kbd { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: #f3f3f7;
            padding: 1px 4px; border-radius: 3px; font-size: 0.88em; }
.chart { margin: 1rem 0 1.5rem; }
ul.tight li, ol.tight li { margin: 0.2rem 0; }
.kvtable td.key { font-weight: 500; color: #3c3c43; width: 30%; }
footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #e5e5e7;
         color: #6e6e73; font-size: 0.85rem; }
"""


def build_html(runs: list[dict]) -> str:
    backtest_runs = [r for r in runs if r["tool"] == "train_test_analysis"]
    bayes_runs = [r for r in runs if r["tool"] == "bayesian_hierarchical"]
    nav_fig = _nav_figure(backtest_runs)
    matrix = _matrix_table(backtest_runs)
    bayes_html = _bayes_recap(bayes_runs)

    test_window = "—"
    for r in backtest_runs:
        p = r["params"]
        ts, te = p.get("test_start"), p.get("test_end")
        if ts and te:
            test_window = f"{ts} → {te}"
            break

    return f"""<!DOCTYPE html>
<html lang='en'><head><meta charset='utf-8'>
<title>Congress Trader — Strategy Framework Report</title>
<style>{CSS}</style>
<script src='{PLOTLY_CDN}'></script>
</head><body>
<header>
  <h1>Congress Trader — Strategy Framework</h1>
  <div class='meta'>
    Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
    · runs #{', #'.join(str(r['id']) for r in backtest_runs)}
    {f"· bayes #{bayes_runs[0]['id']}" if bayes_runs else ''}
    · OOS window <b>{test_window}</b>
  </div>
</header>

<h2 id='context'>Context</h2>
<p>
This report supersedes the v1 alpha-hunting narrative. After Phase 1 of
the strategy-framework work, two things became clear:
</p>
<ol class='tight'>
<li><b>The engine had a methodological bug</b> through run #22: returns
were reported at the calendar's extended tail
(<code>cal_end</code>) rather than at <code>test_end</code>. This
systematically biased numbers in favor of buy-and-hold benchmarks
(by up to ~400 days of free drift for mirror_sell runs). Fixed
starting with run #23. <b>v2 only shows post-fix runs.</b> The full
historical narrative is preserved in
<code>generate_report.py</code> v1 output.</li>
<li><b>The leadership hypothesis cannot be cleanly resolved with our
data</b>, per the Bayesian model. The OOS excess on the strategy
matrix below is captured by <code>is_post_trifecta</code> and
sector composition, not leadership alpha. So this report is about
<em>which exit policy captures the most of the realized return
honestly</em>, not about claiming an alpha source.</li>
</ol>

<h2 id='engine'>Engine capabilities</h2>
<p>The rules-backtest engine supports four exit policies. The recommendation
system can dispatch to any of them based on a risk profile.</p>
<table class='kvtable'>
<tr><td class='key'><code>fixed_horizon</code></td>
    <td>Hard timer — every position closes after <code>horizon_days</code>.
        Event-study-shaped; useful as the methodological baseline but not
        a realistic strategy.</td></tr>
<tr><td class='key'><code>mirror_sell</code></td>
    <td>Close on the earliest of: same-member sale disclosure
        (filing_date), 252-day time stop fallback, or end-of-window.
        No trailing stop. Literal-follower strategy.</td></tr>
<tr><td class='key'><code>mirror_sell_with_trailing_stop</code></td>
    <td>Adds a fixed <code>trailing_stop_pct</code> (default 15%)
        trailing stop to <code>mirror_sell</code>. Risk-management
        layer; over-triggers in high-vol regimes.</td></tr>
<tr><td class='key'><code>mirror_sell_with_beta_trailing_stop</code></td>
    <td>As above, but with the stop scaled per-ticker by trailing-252d
        beta vs SPY: <code>stop_pct × clip(β, 0.5, 3.0)</code>.
        Adaptive risk control — low-vol names get tight stops, high-beta
        names get wide ones.</td></tr>
</table>

<h2 id='matrix'>Strategy matrix — OOS results</h2>
<p>
Every cell is a single run at <code>filing_date</code> anchor,
<code>hold_days=63</code> (where applicable), <code>min_members=1</code>,
<code>trailing_stop_pct=15%</code> (where applicable),
<code>time_stop_days=252</code>. All measured at end-of-test-window
(mark-to-market on the corrected MTM convention).
</p>
{matrix}
<p style='font-size:0.85rem;color:#6e6e73'>
Green-highlighted rows: best in their respective benchmark column.
<b>positions</b> = OOS positions opened.
<b>OOS strat return</b> is independent of benchmark.
<b>Train excess vs SPY</b> is shown to flag train/test instability.
</p>

<h2 id='nav'>NAV trajectories (SPY-benchmark runs)</h2>
<div id='chart-nav' class='chart'></div>

{bayes_html}

<h2 id='recommendations'>Recommendation framework</h2>
<p>
The matrix above feeds the <code>GET /api/recommendations</code>
endpoint. Risk profiles map to specific strategy configurations:
</p>
<table class='kvtable'>
<tr><td class='key'>conservative</td>
    <td>Broad universe (all members), <code>mirror+β stop</code>.
        Risk-managed by per-ticker beta-scaled stop; widest universe;
        most diversified.</td></tr>
<tr><td class='key'>moderate</td>
    <td>Leadership universe, <code>mirror+β stop</code>. Narrower but
        risk-controlled. Falls between the two extremes.</td></tr>
<tr><td class='key'>aggressive</td>
    <td>Leadership universe, <code>mirror_sell</code> (no trailing stop).
        Highest historical OOS excess (+11.6% over SPY in 2025) but no
        intra-position drawdown protection; relies entirely on the
        literal-follower signal.</td></tr>
</table>
<p class='callout warn'>
Risk-profile labels reflect the relative <em>portfolio characteristics</em>
of each strategy, not a guarantee about future returns. Train/test
instability persists across all configurations; the matrix above gives
the empirical historical comparison.
</p>

<h2 id='whats-next'>What's next</h2>
<ol class='tight'>
<li><b>Recommendation endpoint live</b> — see
    <code>/api/recommendations?as_of=YYYY-MM-DD&risk_profile=...</code></li>
<li><b>Frontend</b> — risk-profile picker on the Analysis page renders
    today's recommendations.</li>
<li><b>Longer panel</b> — extending the train data back to STOCK
    Act-era 2013 filings would let the Bayesian model see multiple
    control changes inside the training window, which is the cleanest
    way to disentangle leadership from cohort effects.</li>
<li><b>Judges</b> — federal judge AO-10 disclosures via Free Law
    Project as a non-overlapping political-figure population (lifetime
    appointments → not coupled to the 2025 trifecta change). Requires
    a separate holdings-based analytical framework.</li>
</ol>

<footer>
Generated by <code>backend/generate_report_v2.py</code>. The historical
alpha-hunting narrative (including pre-MTM-fix runs #1-22) is preserved
in <code>generate_report.py</code> v1 output. Source data:
<code>analysis_runs</code> on the NUC Postgres instance.
</footer>

<script>
Plotly.newPlot('chart-nav', {json.dumps(nav_fig['data'])},
               {json.dumps(nav_fig['layout'])}, {{responsive: true}});
</script>
</body></html>
"""


def _derived_path(out_dir: Path, run_ids: list[int]) -> Path:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    ids_part = "-".join(str(i) for i in run_ids)
    return out_dir / f"strategy_report_runs{ids_part}_{today}.html"


def _ints(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x.strip()]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-ids", type=_ints, required=True,
                   help="Run IDs to include. Should be post-MTM-fix runs (#23+).")
    p.add_argument("--output", help="Explicit output path; otherwise derived.")
    p.add_argument("--output-dir", default="reports")
    args = p.parse_args()

    runs = _load_runs(args.run_ids)
    if not runs:
        raise SystemExit("No runs found.")

    if args.output:
        out_path = Path(args.output)
        if out_path.is_dir() or args.output.endswith(("/", "\\")):
            out_path = _derived_path(out_path, args.run_ids)
    else:
        out_path = _derived_path(Path(args.output_dir), args.run_ids)

    html_out = build_html(runs)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_out, encoding="utf-8")
    logging.info(f"Wrote {out_path}  ({len(html_out):,} bytes)")


if __name__ == "__main__":
    main()
