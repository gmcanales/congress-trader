# Congress Trader — Decision Log

A chronological record of the analytical decisions, scope expansions, and
methodological pivots through the project. Companion to the live status in
[CLAUDE.md](../CLAUDE.md) and the rendered findings in
[`reports/`](../reports/). Cite this when a future contributor asks "why did
we do it that way?"

The document is structured chronologically. Each section names the decision,
states the why, and cites the relevant commit + AnalysisRun ids so the work
can be reproduced or audited.

---

## 0. Project context

**The question we're testing.** Does following disclosed congressional trades
yield risk-adjusted excess returns? Specifically: can a retail follower beat
broad-market benchmarks by mirroring trades from the STOCK-Act Periodic
Transaction Reports?

**The original constraint.** Disclosures are PDF (House) or HTML (Senate)
with a known reporting lag of up to 45 days under the STOCK Act. Any
actionable strategy must use the **filing date**, not the transaction date —
the transaction date is not publicly observable in real time.

**The literature anchor.** The result this project tests its findings against
is Wei & Zhou, *"Captain Gains" on Capitol Hill* (NBER, Nov 2025). They find
rank-and-file congressional trades have no edge — but congressional leaders
who held positions like Speaker, Majority Leader, committee chair, etc.,
generate ~47pp/yr alpha after ascension. Their identification uses 26 years
of data and within-member pre/post-ascension differencing.

---

## 1. v1 baseline (commits up to `b10f932`)

### 1.1 Data ingestion scope

**Decision.** Ingest 2021–2025 House + Senate PTRs. Cover full STOCK Act-era
reporting we can access without paid sources.

**Data added.**
- House PTRs scraped from the House Clerk financial-pdfs ZIPs
  (`services/scraper.py`). Each PTR downloaded as PDF.
- House parser (`services/ptr_parser.py`) extracts transactions via
  `pdftotext -layout` + regex. Per-transaction `confidence` field
  (high/medium/low) flags parser certainty.
- Scanned filings (≈12% of House PTRs) fall back to Tesseract OCR
  (`services/ocr.py`). Many are checkbox-grid format and parse poorly.
- Senate PTRs scraped from efdsearch.senate.gov
  (`services/senate_scraper.py`). HTML-table format, much cleaner than
  House PDFs.

**Final v1 dataset.** ~12k transactions across House + Senate, ~8.5k with
non-empty `ticker` (i.e. stock transactions). 313 distinct members.

**Decisions explicitly deferred:**
- The ~12% of House PTRs that are scanned checkbox-grid forms parse poorly
  via OCR. Decided to **accept the gap for v1** and revisit only if a
  working signal emerged. We later confirmed there's no broad signal, so
  this gap remains deferred.

### 1.2 Backtest engine design (`services/backtest.py`)

**Decision.** Build a single parameterized engine with three layers:
1. **Event study** — per-trade forward excess returns (5d, 21d, 63d) with
   t-stats. Tests "is there a per-trade signal?"
2. **Rules backtest** — equal-weight portfolio with hard-timer exits and an
   optional conviction filter (min_members ≥ N distinct buyers within a
   trailing 30-day window). Tests "would a follower strategy have worked?"
3. **Train/test orchestration** — single split, in-sample grid search,
   held-out OOS evaluation.

**Key methodology choices (and why):**

| Choice | Why |
|---|---|
| Equal-weight positions | Disclosed amounts are coarse ranges; weighting introduces noise without obvious benefit at v1 |
| Hard timer exits | Event-study compatibility — every position closes after `horizon_days` so forward returns are measured at a fixed point. **Not a realistic strategy**, but the right tool for "is there signal" |
| SPY benchmark | Default; standard cap-weighted broad market |
| Single train/test split | Simplest; walk-forward was deliberately deferred |
| No transaction costs | Zero slippage/spread/commissions modeled |
| Filing-date anchor (default) | Respects STOCK Act lag; the only tradeable choice |
| Transaction-date as a configurable alternative | For diagnostic comparison only (look-ahead) |

All knobs are explicit arguments. The `analyze.py` CLI and
`POST /api/model/run` endpoint expose them. Every run is persisted as an
`AnalysisRun` row with full params, scores, and full_results for
traceability — a **firm methodological commitment** from
[`feedback_parameterized_analysis`](../.claude/projects/.../memory/feedback_parameterized_analysis.md).

### 1.3 v1 result — null (`AnalysisRun #1`)

**Run #1.** Default config: filing_date anchor, grid over
`hold_days × min_members` ∈ {21,63} × {1,2,3}, train 2021–2024, test 2025.

**Result.** No edge. In-sample grid winners went OOS-negative — the classic
small-sample overfit-by-selection signature.

**Memory record:** [`project_modeling_plan`](../.claude/projects/.../memory/project_modeling_plan.md)
updated to note v1 was null.

---

## 2. v1 diagnostic exploration

After confirming the rank-and-file null, every subsequent step was about
understanding *why* the result looked the way it did before committing to
any new modeling direction.

### 2.1 Reporting infrastructure (commit `5da17bb`)

**Decision.** Build `generate_report.py` as a parameterized HTML report
generator. Emit `reports/report_runs<ids>_<YYYY-MM-DD>.html` so every
published number is traceable to specific `AnalysisRun` ids.

**Tradeoff considered.** Markdown vs HTML for reports. Settled on HTML
because we want embedded interactive Plotly charts (sourced via CDN; reports
are self-contained otherwise).

**Naming convention** (commit `3b7aeaa`): `report_runs<ids>_<YYYY-MM-DD>.html`
e.g. `report_runs1-2-3-4-5-6-7-8-9-10-11-12-13-14-15-16-17-18_2026-05-21.html`.
List of run IDs is explicit (not range-collapsed) for unambiguous audit
trails.

### 2.2 Benchmark parameterization (commit `3b7aeaa`, runs #3–6)

**Trigger.** Initial runs anchored on transaction_date showed an apparent
edge. Needed to control for whether this was a cap-weight-vs-equal-weight
basis effect (the strategy holds 200+ tickers; cap-weighted SPY is
mechanically different from equal-weight broad equity).

**Decision.** Make benchmark a runtime parameter. Add RSP (equal-weight
S&P 500) as a comparison benchmark alongside SPY.

**Findings (transaction_date anchor):**
| Run | Bench | OOS strategy | OOS excess |
|---|---|---|---|
| #5 (SPY) | cap-weight | +20.44% | +2.53% |
| #6 (RSP) | equal-weight | +20.44% | +5.44% |

Strategy beat both benchmarks. The SPY excess was **not** a mechanical
equal-weight basis effect.

**Pinned-grid follow-up (#5/#6).** Earlier runs #3/#4 picked different
"best" configurations under SPY vs RSP, so the cross-benchmark numbers
weren't strictly apples-to-apples. The pinned-grid runs `hold=63, min=1`
gave us cleanly comparable results.

### 2.3 NAV freeze diagnosis (commit `5da17bb`)

**Trigger.** Visual inspection of the run #2 OOS NAV chart showed flat
stretches in Q1 2025, August 2025, Oct–Nov 2025, and Feb 2026.

**Diagnosis.** Two compounding effects:
1. The conviction filter (`min_members=2`) was killing 80–95% of all
   purchases — most congressional buys are solo. Surviving event counts:
   January 8, February 10, March 14, August 7, September 3, October 3
   (out of 50–80 raw buys per month).
2. The hard 21-day timer exit closed positions before new ones replaced
   them. Once `n_open` hit zero, the NAV literally couldn't move.

**Decision.** Removing the conviction filter (run #3, `min_members=1`)
eliminated the freezes: invested days 244/271 → 302/318.

**Key takeaway captured.** The hard-timer exit is an event-study construct,
not a realistic strategy. Documented in the report's "Inputs and
assumptions" section so future readers understand the headline numbers in
that context.

### 2.4 The tradeable-anchor check (commit `413f803`, runs #7/#8)

**Trigger.** The transaction_date pinned grid (#5/#6) showed +2.53% / +5.44%
excess. But `transaction_date` is not publicly observable in real time —
the disclosure lag is up to 45 days. The result was suspicious for
look-ahead bias.

**Decision.** Re-run the identical pinned grid (`hold=63, min=1`) with
`filing_date` as the anchor — the only configuration that respects the
STOCK Act lag.

**Result.** The edge collapses:
| Anchor | OOS strategy | vs SPY | vs RSP |
|---|---|---|---|
| transaction_date (#5/#6) | +20.44% | +2.53% | +5.44% |
| **filing_date (#7/#8)** | **+15.08%** | **−2.82%** | **+0.09%** |

The 5.4-point strategy-return gap between the two anchors is the
**disclosure-lag premium**: congressional buys are concentrated in names
that drift up *during* the reporting window. A follower trading at
disclosure cannot capture it.

**Methodological commitment recorded.** From this point forward, all
actionable / headline claims must use the `filing_date` anchor.
`transaction_date` retained for diagnostic comparisons only.

The runs table in the report now tags each row **tradeable** (filing_date)
or **diagnostic** (transaction_date) with a footnote explaining the
distinction.

---

## 3. v2 leadership pivot

The v1 broad null is consistent with the post-STOCK-Act literature, but
Wei & Zhou (2025) identified a *subgroup* where the signal does exist —
congressional leadership. This was the natural next direction.

### 3.1 Why v2 is a theory-driven cut, not data mining

**Crucial distinction documented.** The v1 plan explicitly warned against
chasing post-hoc subgroup signal (per-member, per-sector slicing) because
those slices would just re-overfit the small sample. Wei & Zhou is
**different**: they identify the subgroup ex-ante based on a theory
(political-influence channel, corporate-access channel) and find the
effect with within-member identification across 26 years.

This justifies adding a leadership filter even though our sample is small,
*provided we report it honestly and don't grid-search on top of it*.

The full scope, schema design, and sequencing live in
[docs/v2_leadership_plan.md](v2_leadership_plan.md).

### 3.2 Step 1: bioguide_id backfill (commit `b1fcb5e`)

**Need.** Joining to external congressional datasets requires a canonical
ID. Our `members` table only had name + state + chamber — no bioguide_id,
which is the universally-used join key in
`unitedstates/congress-legislators`, ProPublica, and GovTrack.

**Decision.** Add `bioguide_id` nullable column to `members`, populate via
a fuzzy-match CLI (`backfill_bioguide.py`) against the
`legislators-{current,historical}.json` files.

**Matching strategy chosen.**
1. Primary index: `(state, district, normalized_last_name)`. House terms
   supply district; Senate terms set district=NULL.
2. Multi-token last-name handling for compound surnames like
   "Hinson Arenholz" → match against legislator last="Hinson" via per-token
   indexing.
3. Cross-field rescue for cases like first="Cathy McMorris", last="Rodgers"
   matching against legislator last="McMorris Rodgers".
4. Senate fallback: if `state_dst` is empty (our Senate scraper doesn't
   populate it), fall back to chamber+name-only matching against senators.
5. First-name disambiguation: tokenize the member's first name and check
   against each candidate's name variants (first/nickname/middle/
   official_full); rank exact > prefix > none.
6. Final tiebreaker: most-recent term end date.

**Result.** 311 of 313 members matched (99.4%). The 2 unmatched are parser
garbage — names that don't correspond to any real congressional member
("Richard B. Reisdorf" MN01 and "Ada Norah Henriquez" PR00).

**Migrations approach.** Introduced `sql/migrations.sql` with idempotent
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` plus a small applier in
`main.py` lifespan. **Deliberately chose not to adopt Alembic** — for a
single-developer project with shared external DB, idempotent SQL is
simpler.

### 3.3 Step 2: positions + committees + party_control (commit `e2d2f1e`)

**Schema additions:**
- `committees` — directory of committees + subcommittees keyed on
  `thomas_id`. 559 rows (94 standing committees + 465 subcommittees).
- `positions` — time-bounded leadership / chair / ranking tenure per
  member. One row per (member, role, term).
- `party_control` — majority party per (chamber, congress) for 117th–119th
  Congress (2021–2027). 6 rows total, hard-coded from official sources.

**Initial population (`ingest_leadership.py`).** Pulled
`legislators-{current,historical}.json` for chamber leadership roles
(Speaker, Majority/Minority Leader, Whips, Conference Chair, etc.) — 37
position tenures matching members in our DB.

**Coverage at this stage:** 300 of 8,490 stock transactions (3.5%) sat
inside a leadership window. **Heavily concentrated in 5 traders**:
Capito (135 trades), McClain (61), Clark (48), Pelosi (30), McConnell (16).

**View added.** `v_stock_transactions_leadership` tags each trade with
`in_leadership_window`, `positions_held`, `chamber_majority_party`,
`party_in_majority`. Built with EXISTS / scalar-subquery so it stays one
row per trade (no row multiplication from multi-position members).

### 3.4 Step 2.5: leadership_only filter on the backtest (commit `58f6a4b`)

Added `leadership_only: bool` to the backtest engine via an EXISTS
subquery on `positions`. **Crucial design decision:** the position check
anchors on `transaction_date` regardless of the backtest's event-entry
anchor. Wei & Zhou's mechanism is informational — the leader uses inside
knowledge *at the moment they trade*, not when the disclosure files —
so the filter must use the trade date, not the filing date.

**Runs #9/#10 — narrow universe (37 chamber leaders).**
- OOS positions: 45 (from 67 train)
- OOS SPY excess: +38.11%
- OOS RSP excess: +41.02%

This looked dramatic, but several diagnostic signals warned against
taking it at face value:
1. Train excess was **−61.76%** at the same config → 100-point sign flip
2. Per-trade 5d event-study t-stat was **−2.65** (statistically significant
   underperformance per trade) yet the aggregate was positive
3. Sample concentrated in 5 members

The aggregate gain was therefore portfolio-composition behaviour, not
stock-picking.

The report's "Leadership preliminary" section flagged this as
**"almost certainly noise"** at the time.

### 3.5 Step 3: committee chairs + ranking members (commit `80881ef`)

**Need.** Wei & Zhou's universe of leadership-relevant members is broader
than chamber leadership alone — committee chairs hold agenda-setting
power too. Committee chair / ranking-member tenure is not in
`unitedstates/congress-legislators`' published JSON snapshots (only the
current snapshot is shipped). For historical chairs, we needed the YAML
*at specific commits*.

**Approach implemented (`ingest_committee_chairs.py`).**
- Use the GitHub commits API to find commits to
  `committee-membership-current.yaml` near a target date per Congress:
  117th → 2022-06-15, 118th → 2024-06-15, 119th → 2026-04-15. Picked
  mid-term dates to avoid the Dec 25 "clear committee assignments"
  commits that wipe the file between Congresses.
- For each commit, fetch the YAML via raw.githubusercontent.com.
- Add PyYAML to requirements.
- Extract `(rank=1, party=majority)` = chair and `(rank=1, party=minority)`
  = ranking member from every **full committee** (subcommittees
  intentionally skipped — additional noise on top of the leadership
  signal we're testing).

**Result.** 120 new chair / ranking position rows. Total positions
table now 157 (37 leadership + 64 chair + 56 ranking).

**Coverage expansion.** Leadership-window stock trades 300 → **1,477**
(4.9x). Top traders much more distributed: Carper 493, Boozman 188,
Capito 135, Foxx 80, Whitehouse 79, Burgess 74.

**Runs #11/#12 (expanded universe, same pinned grid):**
- OOS positions: 186 (from 460 train)
- OOS SPY excess: **+11.93%** (was +38.11% on narrow)
- OOS RSP excess: **+14.85%** (was +41.02%)
- Train excess: −42.10% / −27.24% (still very negative)
- Per-trade 5d t-stat: **−2.65** (unchanged)
- Per-trade 63d t-stat vs RSP: **+1.94** (borderline 95% threshold)

The narrow-universe result mostly dissolved when chairs were added.
Per-trade short-horizon underperformance was the most stable
statistically-significant finding across both samples.

### 3.6 Hold-period term structure (commit `8af338f`, runs #13–16)

**Question.** All leadership runs so far pinned `hold=63`. Did this lock
in the "wrong" hold for the leadership universe?

**Decision.** Run the same configuration at `hold ∈ {21, 126}` × `{SPY, RSP}`
to **show the term structure, not pick a winner**. Critical framing — a
grid search at this point would re-trigger the v1 overfit-by-selection
trap.

**Result (OOS excess by hold):**

| Hold | OOS strategy | vs SPY | vs RSP |
|---|---|---|---|
| 21d (#13/#14) | +3.84% | **−16.51%** | **−12.09%** |
| 63d (#11/#12) | +29.84% | +11.93% | +14.85% |
| 126d (#15/#16) | +27.58% | −0.41% | +8.24% |

Findings:
- 21d hold locks in the negative 5d per-trade signal → strategy
  underperforms badly
- 63d hold catches the borderline-positive 63d per-trade signal → best OOS
- 126d adds noise past the per-trade signal horizon → drift
- **Train excess negative at every horizon** (−27% to −61%). The OOS hump
  at 63d is best-among-bad-train-records, not a stable relationship.

This was the first signal that the OOS result wasn't a stable
horizon-dependent signal but a window-specific anomaly.

### 3.7 The trifecta-cohort confound (commit `6892e80`)

**Trigger.** User pointed out: 2025 has a Republican trifecta for the
first time in our data window. Is the train/test sign flip explained by
the cohort change rather than a leadership signal?

**Diagnostic SQL.** Broke down the leadership-window trades that drove
runs #11/#12 by majority status, member party, Congress, and top traders.

**Result.** Train and OOS samples are near-disjoint cohorts:

| Window | Buys | Composition | Top trader |
|---|---|---|---|
| Train 2021-2024 | 460 | Mixed D+R, 15 members. D Senate chairs + R House chairs. | Carper (D, EPW Chair, retired Jan 2025) |
| OOS 2025 | 186 | **100% Republican, 98% in-majority, 11 members.** 92% of trades in the 119th Congress (R trifecta). | **Boozman (R, new Senate Ag Chair) = 100 of 186 trades** |

**The leadership filter, when applied across the 2025-01-03 control
change, structurally measures a regime transition.** Train cohort = trades
by people who held chairs under D-leaning chambers. OOS cohort = trades
by newly-installed R chairs under the trifecta. Different people,
different chambers, different policy environment.

This was a structural finding, not a parameter to be tuned. The report
got a dedicated "Trifecta-shift confound" section.

### 3.8 Sector ingestion + sector-neutral excess (commit `9e95e76`, run #17)

**Question.** Does the trifecta-cohort effect persist after controlling
for sector exposure? I.e., is the R-cohort outperformance just "they
happened to overweight winning sectors" (energy, financials, mega-cap
tech) or is there leadership picking skill within sectors?

**Schema added.** `tickers` table with `(symbol, sector, industry,
market_cap, last_updated, source)`. Populated by `ingest_sectors.py` from
`yfinance.Ticker.info`. 1,021 of 1,477 distinct trade tickers got sector
data (the other ~30% are mostly delisted symbols, foreign tickers, and
mutual fund codes).

**Engine extension.** Added `benchmark_mode = "ticker" | "sector"`. In
sector mode, each trade's excess return is computed against its GICS
sector SPDR ETF (XLK / XLV / XLF / XLC / XLY / XLI / XLP / XLE / XLU /
XLRE / XLB). The portfolio-level benchmark is a parallel shadow portfolio
that, each day, equal-weights the sector ETFs of the strategy's currently
held positions. Trades on tickers without sector data are excluded with
a count.

**Run #17 result.**
- Sector-mix benchmark returned **+20.54%** in 2025 (vs SPY +17.90%, RSP
  +14.99%). So the strategy was sector-tilted toward 2025's winning
  sectors → ~5-6pp of the RSP-relative edge was sector composition.
- OOS sector-neutral excess: **+12.50%** (vs +14.85% vs RSP). Roughly
  80% of the apparent edge survived sector-neutralization.
- **Crucially:** train sector-neutral excess was **−27.09%** — essentially
  identical to the −27.24% vs RSP. The 40pp train/test sign flip survived
  sector-neutralization unchanged.

**Interpretation captured.** Sector tilt is real (explains ~6pp of the
OOS edge) but doesn't dissolve the headline or rescue the train. The
cohort-change confound is structural, not sectoral.

---

## 4. Bayesian closure (commit `d06c17a`, run #18)

### 4.1 Why Bayesian, why now

**User question** (paraphrased): "I assumed we were using a neural network.
Should we be using k-folds or different ML techniques?"

**Factual correction made explicit in the response.** We were not using ML
at all. The pipeline was descriptive statistics (event study) + a
deterministic rules backtest + a single train/test split. No learned
model.

**The methodologically serious upgrade** at our sample size is not ML
(random forest / gradient boosting would overfit instantly at ~3.6k
observations with ~150 random-effect groups) but a Bayesian hierarchical
model — which directly addresses the small-sample, hierarchical-structure,
partial-pooling needs we'd been working around with raw t-statistics.

### 4.2 Model spec choice (chosen, with alternatives)

**Three options were laid out:**

| Option | Description | Chosen? |
|---|---|---|
| Focused | per-trade excess at 63d, partial pooling on member only | no |
| Broader | multi-horizon (5d, 21d, 63d) with horizon as a level | no |
| **Most thorough** | **include rank-and-file as control population — leadership_alpha is the within-population difference** | **yes** |

**Why "most thorough" was chosen.** The trifecta-cohort confound made it
clear that leadership-only analysis can't disentangle the leadership
effect from the regime effect when those overlap. Including rank-and-file
as a control, with `is_post_trifecta` as a separate fixed effect, gives
the regression engine the predictors it needs to isolate leadership.

### 4.3 Framework choice — PyMC

**Three options:**
- **PyMC** — finance-friendly, gentle syntax, good NUTS sampler. **Chosen.**
- NumPyro (JAX) — faster sampling but JAX learning curve
- Stan via cmdstanpy — best diagnostics but heavier toolchain

**Why PyMC.** Most popular in finance/econometrics, pip-installable,
moderate fit time (≈5-10 minutes for our model size), good integration
with arviz for diagnostics.

**Engineering note.** PyMC 5 uses PyTensor; the container needed `g++`
installed (Dockerfile change in `d06c17a`). Without `g++`, PyTensor falls
back to a Python-only path which is "severely degraded" (would have made
NUTS effectively unusable).

### 4.4 Persistence choice

**Decision.** New `AnalysisRun` rows with `tool='bayesian_hierarchical'`
rather than replacing the existing event study. The rules-backtest
results are still useful as a one-number reference; the Bayesian model
is the proper uncertainty-quantified version that lives alongside.

### 4.5 Dataset for the model

**Tool added: `build_trade_dataset.py`.** Builds a per-trade DataFrame
with: trade_id, member_id, party, chamber, sector, sector_etf,
leadership_at_trade, in_majority, is_post_trifecta, excess_63d_spy,
excess_63d_sector. Parameterized by window and horizon.

**Final dataset.** 3,617 purchase trades / 157 members / 2021-01-01 to
2025-12-31. 612 leadership-window trades (17%) + 3,005 rank-and-file
(83%). Trades winsorized at 0.5 / 99.5 percentile on the outcome to
reduce single-trade dominance.

### 4.6 Model spec

```
y[i] = excess_63d_spy for trade i  (winsorized)

y[i] ~ Normal(mu[i], sigma)
mu[i] = alpha_member[member[i]]
      + beta[leadership_at_trade] * x_lead[i]
      + beta[in_majority]        * x_maj[i]
      + beta[is_post_trifecta]   * x_trifecta[i]
      + beta[party_R]            * x_R[i]
      + sum_s beta[sector_s]     * x_sector_s[i]    (Technology baseline)

alpha_member[m] ~ Normal(mu_alpha, tau_member)        [partial pooling]
mu_alpha     ~ Normal(0, 0.05)        weakly informative
tau_member   ~ HalfNormal(0.1)
sigma        ~ HalfNormal(0.5)
beta[*]      ~ Normal(0, 0.1)         weakly informative
```

NUTS with 4 chains × 1,500 draws, tune=1,500, target_accept=0.92.

### 4.7 Headline result (run #18)

| Predictor | Posterior mean | 94% HDI | P(β > 0) |
|---|---|---|---|
| **`leadership_at_trade`** | **+0.25%** | [−2.1%, +2.5%] | **0.582** |
| `is_post_trifecta` | +1.25% | [−0.6%, +3.0%] | 0.901 |
| `in_majority` | −0.26% | [−1.9%, +1.4%] | 0.386 |
| `party_R` | +0.83% | [−1.6%, +3.2%] | 0.756 |
| `sector_Energy` | +3.67% | [+1.0%, +6.3%] | 0.995 |
| `sector_Consumer Cyclical` | −2.75% | [−4.7%, −0.8%] | 0.004 |
| `sector_Basic Materials` | −3.76% | [−6.4%, −1.1%] | 0.005 |
| `sector_Industrials` | −1.95% | [−3.9%, +0.1%] | 0.035 |

Convergence: min ESS = 579, max R̂ = 1.010 (right at the threshold; could
tighten with more tune/draws but acceptable).

### 4.8 Interpretation

**The leadership effect itself is not credible** at this sample size,
priors, and design. The 94% HDI on `leadership_at_trade` comfortably
straddles zero. P(β > 0) = 0.58 is essentially a coin flip.

**What the rules-backtest was calling "leadership edge" got cleanly
reattributed** when the Bayesian model put it next to `is_post_trifecta`
(+1.25%, P=0.90) and the sector dummies. The leadership filter overlapped
with the trifecta window (because chairs all changed party at
2025-01-03), and the resulting cohort happened to be sector-tilted toward
2025 winners. Once both confounds get their own coefficients, the
leadership coefficient gets squeezed to noise.

This is the cleanest answer the existing data can produce on the Wei &
Zhou hypothesis.

---

## 5. Data scope by stage

How the data inventory grew across the project:

| Stage | Commit | Data added |
|---|---|---|
| Initial scrape | `b10f932` | ~12k transactions, ~8.5k with tickers; 313 members; 2021-2025 House+Senate |
| Reporting infra | `5da17bb` | (no data; engine & report) |
| Benchmark param | `3b7aeaa` | RSP price history (+1 ETF) |
| Bioguide backfill | `b1fcb5e` | `members.bioguide_id` (311/313) |
| Leadership (step 2) | `e2d2f1e` | 559 committees, 37 leadership positions, 6 party_control rows |
| Committee chairs (step 3) | `80881ef` | +120 chair/ranking positions = 157 total |
| Party backfill | `58f6a4b` | `members.party` (311/313) |
| Sector ingest | `9e95e76` | 1,021 / 1,477 ticker sectors; +11 sector ETFs in price_bars |
| Bayesian dataset | `d06c17a` | 3,617 per-trade rows with all features (in-memory; not persisted to DB) |

External data sources used (and why):

- **House Clerk financial-pdfs ZIPs** — only source of House PTRs.
- **efdsearch.senate.gov** — only source of Senate eFD PTRs. Requires
  agreement / CSRF flow handling.
- **`unitedstates/congress-legislators` (GitHub Pages JSON)** — bios +
  leadership tenure + committee directory. Public-domain.
- **`unitedstates/congress-legislators` raw YAML at historical commits**
  — only available way to get historical committee chair tenure without
  paid sources (ProPublica requires auth; Congress.gov bulk committee data
  is not chair-focused).
- **yfinance** — stock and ETF price history; sector / industry / market
  cap. Free, but rate-limited and sometimes returns empty for delisted
  symbols.

External data sources **considered but deferred**:
- **Free Law Project / CourtListener** judicial disclosures — would
  diversify the political-figure universe but uses annual AO-10 (not
  PTR-shaped), so it needs a different analytical framework. Deferred.
- **OGE Form 278-T (cabinet / executive branch)** — PTR-shaped, plugs
  into the existing engine. But all cabinet officials turned over at the
  2025-01-03 trifecta change, so it has the same cohort-confound problem
  as congress. Deferred.

---

## 6. Run catalog

`analysis_runs` rows persisted through the project (all in the NUC
database). Tool `train_test_analysis` unless noted.

| Run | Date | Anchor | Bench | Leadership | hold | min_members | Notes |
|---|---|---|---|---|---|---|---|
| #1 | 2026-05-16 | filing_date | SPY | no | grid 21/63 | grid 1-3 | v1 baseline; null |
| #2 | 2026-05-20 | transaction_date | SPY | no | grid 21/63 | grid 1-3 | v1 baseline alt anchor |
| #3 | 2026-05-20 | transaction_date | SPY | no | grid 21/63 | [1] | min=1 follow-up |
| #4 | 2026-05-20 | transaction_date | RSP | no | grid 21/63 | [1] | RSP comparison |
| #5 | 2026-05-20 | transaction_date | SPY | no | [63] | [1] | pinned grid SPY |
| #6 | 2026-05-20 | transaction_date | RSP | no | [63] | [1] | pinned grid RSP |
| #7 | 2026-05-20 | filing_date | SPY | no | [63] | [1] | **tradeable anchor (SPY)** |
| #8 | 2026-05-20 | filing_date | RSP | no | [63] | [1] | **tradeable anchor (RSP)** |
| #9 | 2026-05-20 | filing_date | SPY | yes (narrow) | [63] | [1] | leadership_only first cut |
| #10 | 2026-05-20 | filing_date | RSP | yes (narrow) | [63] | [1] | leadership_only RSP |
| #11 | 2026-05-20 | filing_date | SPY | yes (expanded) | [63] | [1] | + committee chairs |
| #12 | 2026-05-20 | filing_date | RSP | yes (expanded) | [63] | [1] | + committee chairs |
| #13 | 2026-05-20 | filing_date | SPY | yes (expanded) | [21] | [1] | term-structure short |
| #14 | 2026-05-20 | filing_date | RSP | yes (expanded) | [21] | [1] | |
| #15 | 2026-05-20 | filing_date | SPY | yes (expanded) | [126] | [1] | term-structure long |
| #16 | 2026-05-20 | filing_date | RSP | yes (expanded) | [126] | [1] | |
| #17 | 2026-05-21 | filing_date | sector | yes (expanded) | [63] | [1] | sector-neutral |
| #18 | 2026-05-21 | filing_date | — | — | — | — | **`bayesian_hierarchical`** |

---

## 7. Public deployment via Cloudflare Tunnel + Access (2026-05-22)

**Decision.** Expose the app at a stable, authenticated public URL
(`https://goosebase.org`) so it can be shared without port-forwarding or
exposing the NUC. This is an operational decision, not analytical — included
here because "is the app public?" is a real scope axis future contributors
will want context on.

**Why Cloudflare Tunnel over alternatives:**
- **vs. port-forwarding the dev box** — no NAT/firewall changes, no public
  IP exposure, no DDNS dance, no cert renewal.
- **vs. ngrok / Tailscale Funnel** — Tunnel gives a stable hostname on a
  domain we own (collaborator-shareable URL, not a rotating subdomain) plus
  built-in Access without adding a second product.
- **vs. moving the app to a VPS** — preserves the "code lives on the dev box,
  database lives on the NUC" topology that's been working. Nothing in the
  data path changes.

**Components and why:**
- **Domain via Cloudflare Registrar** — at-cost pricing, zone is
  auto-attached to the CF account, no nameserver-change wait.
- **Named tunnel (token-based, dashboard-managed)** — connector token
  installed as a Windows service on the dev box via
  `cloudflared service install <token>`. All ingress config (the Public
  Hostname rule) lives in the dashboard, not in a local `config.yml`. Chosen
  over the locally-managed flow because there's exactly one origin and we
  don't need ingress-as-code.
- **Public Hostname rule** — `goosebase.org` → `http://localhost:80`
  (nginx). TLS terminates at the CF edge; the connector→origin hop is plain
  HTTP, which is the bit the dashboard placeholder gets wrong.
- **Access policy** — Self-Hosted application bound to the same hostname,
  one-time-PIN identity provider (zero IdP setup), Allow rule with an
  explicit email allowlist. Team subdomain
  `YOUR-TEAM.cloudflareaccess.com`. To add a user: edit the
  application's policy and add their email — they'll get a 6-digit code by
  mail on first login.

**Gotchas worth recording** (any future setup of a similar tunnel will hit
these):

| Symptom | Cause | Fix |
|---|---|---|
| HTTP 502 from CF edge with body `error code: 502` | Public Hostname's Service URL is `https://localhost:80`. Origin is plain HTTP; the connector's TLS handshake to nginx fails | Use `http://localhost:80`. The dashboard placeholder example shows `https://`, which is misleading. |
| `dig`/`nslookup` time out from dev box for the new domain, but DoH works | Outbound port 53 is filtered on this network. System resolver hangs | Bypass for diagnostics: `curl --resolve goosebase.org:443:<CF-edge-IP>` or DoH at `https://cloudflare-dns.com/dns-query`. Not a deployment issue, just a diagnostic snag. |
| OTP emails not arriving | One-Time PIN provider not enabled at team level (even though the wizard implies it's default) | Zero Trust → Settings → Authentication → Login methods → add One-time PIN |
| Domain unreachable from collaborators on security-focused VPNs (NordVPN Threat Protection, WARP+Gateway, corporate filters) | Newly-registered domains get DNS-blocked for ~24–72h until threat-intel feeds mark them benign | Wait. Use `https://goosebase.org/cdn-cgi/trace` as a CF-only liveness probe to distinguish "VPN filter" from "tunnel down." |

**What's still NOT deployed:**
- The NUC database stays LAN-only. No tunnel to `:5432`.
- No Cloudflare Gateway / WARP enrollment for users.
- No mTLS or service-token auth — Access uses interactive OTP only.
- Backend API is reachable only via the same hostname's `/api` prefix
  (nginx proxies it). No separate API subdomain.

**Cross-refs:**
- Setup conversation: 2026-05-22.
- Live operational details in [CLAUDE.md](../CLAUDE.md) under "Public deployment".

---

## 8. Methodological choices documented elsewhere

- [docs/v2_leadership_plan.md](v2_leadership_plan.md) — v2 scope, schema
  design, sequencing, and explicit deferral of cabinet / judges /
  international populations.
- [CLAUDE.md](../CLAUDE.md) — live status snapshot, current schema,
  running instructions.
- [reports/report_runs1-…-18_2026-05-21.html](../reports/report_runs1-2-3-4-5-6-7-8-9-10-11-12-13-14-15-16-17-18_2026-05-21.html)
  — current canonical rendered findings.
- Memory under `~/.claude/projects/.../memory/`:
  - `project_modeling_plan.md` — v1 conclusion summary
  - `project_v2_leadership_plan.md` — v2 step status
  - `feedback_parameterized_analysis.md` — every analysis must run
    through parameterized, traceable tools (firmly enforced)
  - `reference_political_disclosure_sources.md` — external data sources
    catalogue

---

## 9. Things explicitly NOT done, and why

| Considered | Status | Why deferred |
|---|---|---|
| Walk-forward / k-fold CV | deferred | Single train/test was adequate for v1 null; the small sample doesn't reward more elaborate validation, and once we got to the Bayesian model the posterior intervals took the role of "uncertainty quantification" |
| ML methods (random forest, gradient boosting, NN) | deferred | At ~3.6k observations with ~150 random-effect groups, ML would overfit instantly. Bayesian hierarchical regression is the right level of methodological seriousness |
| Position sizing by `amount_min` / `amount_max` | deferred | Disclosure amounts are coarse ranges. Considered as cheap upgrade but not implemented; equal-weight remained the baseline |
| Beta-adjusted (CAPM-style) excess | deferred | Discussed; would fix a mechanical bias in the numerator but doesn't dissolve the trifecta confound that turned out to be the dominant issue |
| `mirror_sell` exit policy | deferred | Discussed extensively; deferred once it became clear the per-trade sell-side signal was also sub-1 t-stat, so a richer exit policy wouldn't recover signal |
| Cabinet (OGE 278-T) ingestion | deferred | Same 2025-01-03 cohort confound as congress; would need additional regime controls |
| Federal judges (AO-10) ingestion | deferred | Different data shape (annual, not PTR); needs separate analytical framework. Identified as a candidate for *future* work precisely because lifetime tenure decouples from the trifecta confound |
| Panel extension back to 2013 | deferred | Tractable but real engineering work to re-ingest older filings. Identified as the cleanest way to give the Bayesian model more train data spanning multiple control changes |
| International political disclosures (UK, CA, AU) | v3 scope | Different disclosure regimes; most publish holdings not trades, so methodology has to change |
| Re-running scanned/checkbox-grid OCR | deferred from day 1 | ~12% of House PTRs are scanned. Computer-vision parsing is non-trivial; v1 didn't show a signal worth chasing the gap for |

---

## 10. Where someone picking this up next should look

1. **Read this document and `CLAUDE.md`.**
2. **Open the most recent report** under `reports/` for the rendered
   numbers + figures.
3. **Read `docs/v2_leadership_plan.md`** for the architecture decisions
   that shaped the leadership work.
4. **`backend/bayesian_model.py`** is the most methodologically serious
   tool in the codebase; if you want to extend the analysis, modifying its
   model spec is probably the place to start.
5. **The natural next moves** (not yet done) are: (a) extend the panel
   back to 2013, or (b) ingest federal judges to get a separate population
   that decouples from the 2025 trifecta confound. Either would meaningfully
   change what the Bayesian posterior on `leadership_at_trade` can say.
