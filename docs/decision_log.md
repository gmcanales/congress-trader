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
`feedback_parameterized_analysis` (Claude memory, not in this repo).

### 1.3 v1 result — null (`AnalysisRun #1`)

**Run #1.** Default config: filing_date anchor, grid over
`hold_days × min_members` ∈ {21,63} × {1,2,3}, train 2021–2024, test 2025.

**Result.** No edge. In-sample grid winners went OOS-negative — the classic
small-sample overfit-by-selection signature.

**Memory record:** `project_modeling_plan` (Claude memory, not in this repo)
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
e.g. `report_runs1-2-3-4-5-6-7-8-9-10-11-12-13-14-15-16-17-18_2026-05-22.html`.
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

## 5. v2.5 spouse-cut follow-up (2026-05-23, runs #55–58 + #60)

### 5.1 Why a v2.5 was warranted

v2's null on `leadership_at_trade` (P(β > 0) = 0.58) closed the
Wei & Zhou hypothesis on this data but didn't rule out other
information-channel cuts. The PTR schema carries an **`owner`** column —
`SP` (spouse-only), `JT` (joint), `DC` (dependent child), or null (the
filer's own holding) — that we had been ignoring.

**Theory.** Members are subject to STOCK Act + public scrutiny + party
leadership pressure when their *own* trades are disclosed. Spouses share
the same household information edge but face less scrutiny and no
party-discipline pressure. If there's an information advantage hidden in
the population, the spouse-touched subset is where it should be cleanest.

This is a theory-driven cut, not a fishing expedition, so it fits the
v1 discipline.

### 5.2 Concentration check first (no engine work needed)

Before building anything, we ran the household-level distribution on
analyzable stock transactions, owner ∈ ('SP', 'JT'):

| Household | Chamber | SP | JT | Total SP+JT |
|---|---|---|---|---|
| Tuberville | senate | 0 | 1,246 | **1,246** |
| Carper | senate | 685 | 0 | 685 |
| Gottheimer | house | 0 | 422 | 422 |
| Mullin | senate | 82 | 276 | 358 |
| Boozman | senate | 0 | 346 | 346 |
| … | | | | |

Top-5 households = **50% of SP+JT volume**; top-10 = 66%. Any aggregate
"spouse edge" finding is mechanically driven by a handful of heavy
traders.

**Reporting-convention observation.** Tuberville is pure JT (1,246/0).
Carper is pure SP (685/0). Mullin is mixed. The SP-vs-JT split is
clearly a household-level account-titling convention — not a signal
about who initiated the trade. The analytically meaningful bucket is
**`spouse_or_joint`** (SP ∪ JT), not the raw owner code.

### 5.3 Engine extension — `owner_filter` parameter

Added a parameterized `owner_filter` to `services/backtest.py` mirroring
the existing `leadership_only` pattern: `any | self | spouse_or_joint |
spouse_only | joint_only | dependent`. Plumbed through `event_study`,
`rules_backtest`, `_rules_backtest_mirror`, `train_test_analysis`, plus
the CLI (`analyze.py --owner-filter ...`) and API
(`POST /api/model/run`, `owner_filter` field on `AnalyzeRequest`).

For mirror-sell exits, the same filter is applied to the **sale** query:
a spouse-buy strategy mirrors the *spouse's* sale, not the member's.

### 5.4 The rules-backtest / event-study discrepancy (2026-05-23 methodology insight)

Pinned grid (hold=63, min_members=1), filing_date anchor, train 2021–24
/ test 2025. Runs #55–58 paired against each benchmark:

| Run | Owner cut | Bench | Train excess | OOS event-study mean / t | OOS rules-backtest excess |
|---|---|---|---|---|---|
| #55 | spouse_or_joint | SPY | −12.60% | **+0.07% / t=0.07** | **+8.43%** |
| #56 | spouse_or_joint | RSP | +6.85% | +1.62% / t=1.74 | **+14.90%** |
| #57 | self | SPY | −37.30% | +1.17% / t=1.22 | −0.94% |
| #58 | self | RSP | −17.86% | +2.28% / **t=2.30** | +5.53% |

**The 8-pp gap on #55 between event-study (+0.07%) and rules-backtest
(+8.43%) is a NAV-sequencing artifact, not edge.** 354 SP+JT trades in
2025 concentrate in time (max concurrent positions = 146; days invested
= 304/318), so a handful of clustered weeks dominate the compounded NAV
even though the per-trade equal-weight mean is essentially zero. Win
rate vs SPY = 44.8% — most spouse trades *underperform* SPY
individually.

**Methodological consequence:** the rules-backtest scalar
(`excess_return`) overstates per-trade edge when entries cluster in
favorable windows. Any future analysis should report **both** the rules
NAV result and the event-study mean. They answer different questions:
"if I followed every trade with $1 equal-weight in time, what would I
end up with" (rules) vs. "what's the expected excess return on a
randomly-selected trade from this universe" (event study).

### 5.5 Per-household OOS decomposition (2025 SP+JT)

To confirm the headline isn't one trader:

| Household | n | mean excess vs SPY | mean excess vs RSP | win-rate vs SPY |
|---|---|---|---|---|
| Boozman | 99 | +2.52% | +3.82% | 48.5% |
| Mullin | 81 | −0.69% | +1.23% | 46.9% |
| Gottheimer | 23 | +1.79% | +2.49% | 56.5% |
| King, Jr. | 21 | +1.62% | +5.11% | 42.9% |
| Capito | 14 | +12.07% | +13.90% | 71.4% |
| (Top-3) | 203 | +1.16% | +2.64% | — |
| (Other) | 146 | −1.08% | +0.52% | — |
| **Pooled** | **349** | **+0.22%** | **+1.75%** | **45.0%** |

Effect doesn't collapse to one trader — top-3 contribute +1.16%, other
25 households contribute essentially zero. Capito's 14 trades at +12% are
a hot small-n tail, not a stable signal.

### 5.6 Bayesian update with `is_spouse_owned` (run #60)

Extended `bayesian_model.py` and `build_trade_dataset.py` to include an
`is_spouse_owned` boolean (owner ∈ ('SP', 'JT')) alongside the existing
v2 predictors. Re-fit on the same 3,617-row dataset, 4 chains × 3,000
draws, target_accept=0.95:

| Predictor | Posterior mean | 94% HDI | P(β > 0) |
|---|---|---|---|
| **`is_spouse_owned`** | **+1.11%** | [−0.61%, +2.85%] | **0.892** |
| `leadership_at_trade` | +0.19% | [−2.15%, +2.54%] | 0.561 |
| `is_post_trifecta` | +1.39% | [−0.40%, +3.17%] | 0.926 |
| `in_majority` | −0.25% | [−1.92%, +1.43%] | 0.385 |
| `party_R` | +0.89% | [−1.59%, +3.33%] | 0.754 |
| `sector_Energy` | +3.67% | [+0.92%, +6.35%] | 0.994 |
| `sector_Consumer Cyclical` | −2.78% | [−4.72%, −0.88%] | 0.003 |
| `sector_Basic Materials` | −3.75% | [−6.45%, −1.05%] | 0.005 |
| `sector_Industrials` | −1.95% | [−3.98%, +0.06%] | 0.034 |

Convergence: min ESS = 1,409, max R̂ = 1.000 — clean. (First fit #59
hit R̂ = 1.020 at 1,500 draws; #60 is the canonical doubled-sample run.)

### 5.7 Interpretation

- **`is_spouse_owned` is the strongest non-sector signal in the data,
  but still not credible at 95%.** P = 0.89, and the 94% HDI just
  barely includes zero. "Suggestive, not actionable."
- **The `leadership_at_trade` coefficient is stable across v2 and v2.5**
  (P = 0.58 → 0.56). Adding `is_spouse_owned` didn't reshuffle the
  leadership null.
- **`is_post_trifecta` carries most of the 2025 effect** (P = 0.93,
  +1.39%) — same finding as v2, unchanged.
- **The sector pattern is unchanged from v2** — Energy long, cyclicals
  / materials / industrials short. The +1.1% spouse coefficient
  estimates the *spouse premium after netting out sector composition*,
  not the raw difference.

### 5.8 Operationalizing the finding — `spouse_favored` profile

Because P = 0.89 is high enough to act on if you discount the strict
95% threshold (a defensible Bayesian stance: posterior probability is
the directly meaningful quantity, not p-values), we added a fourth risk
profile to `services/recommendations.py`:

| Profile | Universe | Exit policy | Backed by |
|---|---|---|---|
| conservative | all | mirror_sell_with_beta_trailing_stop | run #14-ish lineage |
| moderate | leadership | mirror_sell_with_beta_trailing_stop | run #11 lineage |
| aggressive | leadership | mirror_sell | run #11 lineage |
| **spouse_favored** | **owner ∈ (SP, JT)** | **mirror_sell_with_beta_trailing_stop** | **run #62** (mirror+beta-stop, RSP, test→2026-04-30 to match the other profiles' window) |

The recommendation engine itself remains stateless and rules-based; the
Bayesian model informs the *universe definition* (which population
shows the strongest posterior signal), not per-trade scoring. This is
the simplest integration that captures the v2.5 finding without
introducing model-prediction surface area to the API.

---

## 6. Data scope by stage

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
| Owner-cut extension | (v2.5) | Engine `owner_filter` param + `is_spouse_owned` column on the Bayesian dataset (still 3,617 rows; +1 boolean feature) |

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
- **International parliamentarian disclosures (2026-05-23 survey).**
  Comprehensive ~25-jurisdiction scoping confirmed the US STOCK Act
  regime is essentially unique: **no other major democracy publishes
  per-trade events with values via a bulk feed.** Best non-US data
  infrastructure is France's HATVP (XML/CSV on data.gouv.fr) and the UK
  Commons via mySociety, but both publish annual holdings declarations,
  not per-trade events — wrong shape for the existing engine. Full
  region-by-region survey, comparison table, and engineering
  implications in [`docs/international_disclosure_regimes.md`](international_disclosure_regimes.md).
  Verdict: international ingest deferred indefinitely; the policy-event
  approach below is the lower-cost / higher-signal next pivot.
- **US state-level disclosures (2026-05-23 survey).** Same exercise on
  the 50 US states + DC. Verdict: **no state has a STOCK Act analog**;
  all state regimes are annual snapshots filed with state ethics
  commissions, with the SWAMP Index "top performers" leading on
  *governance* (ethics enforcement, gift rules) rather than on
  event-driven data shape. California adds a unique jurisdiction-scoping
  problem (Form 700 only requires disclosure of CA-business-relevant
  holdings). 50-jurisdiction engineering surface for the same wrong
  data shape = strictly worse than the international option. Full
  survey in [`docs/us_state_disclosure_regimes.md`](us_state_disclosure_regimes.md).
  **The state-level scoping did surface a higher-leverage internal
  alternative: congressional senior staff PTRs** (~2,000 covered
  individuals, same House Clerk + Senate eFD filing system we already
  scrape, same engine compatibility, ~5× dataset expansion). See §6.2
  below.

### 6.2 Congressional senior staff PTRs — investigated, ruled out (2026-05-23)

**Initial roadmap entry (incorrect).** Surfaced during the state-level
scoping, I positioned senior congressional staff PTRs as the
highest-ratio internal expansion: ~2,000 STOCK-Act-covered filers
using the same House Clerk + Senate eFD systems we already scrape,
~5× dataset growth, same engine compatibility.

**Correction (same day).** The recommendation was based on stale
understanding of the law. The 2012 STOCK Act DID provide for staff
PTRs to be publicly posted online, but **S.716 (April 2013)** —
passed by unanimous consent in 14 seconds of House debate, signed
April 15, 2013 — explicitly walked back the online-publication
requirement for staff:

> "S.716 modified the online disclosure portion of the STOCK Act so
> that congressional staff and certain executive branch officers and
> employees were no longer required to publicly report financial
> disclosure statements online... The amendment eliminated the
> requirement for the creation of a searchable, sortable database of
> information in reports, and the requirement that reports be done in
> electronic format, rather than on paper."

Members of Congress, candidates, the President, and VP were
unaffected — they still file publicly online and via the same bulk
infrastructure.

**Empirical verification:**
- 2024 House FD index ZIP: 451 PTR rows, **all** with district codes
  (i.e., members only). Zero PTRs from non-members.
- `disclosures-clerk.house.gov/FinancialDisclosure/ViewSearch` exposes
  only `ViewMemberSearchResult` and `ViewCandidateSearchResult` form
  actions. No staff/officer search option.
- Senate eFD search page has no staff filer-type option either.

**Verdict.** Staff PTRs still exist (the underlying filing requirement
wasn't removed) but are accessible only at the Legislative Resource
Center (House) and Senate Library (Senate) — paper or per-document
request, not bulk. Engineering cost to acquire is prohibitive at the
~2,000-filer scale; the path I'd previously claimed reuses existing
infrastructure doesn't exist.

**What this means for the roadmap.** The "internal expansion that
matches the engine" slot is now §6.3 (OGE 278-T, executive branch),
not staff. §6.1 (policy-event windows on existing data) remains the
lowest-cost option.

**What was true and what wasn't:**
- ✓ STOCK Act DOES require senior staff to file PTRs
- ✓ The 2012 law DID originally provide for a searchable staff database
- ✗ The 2012 provision was rolled back by S.716 in 2013
- ✗ Staff PTRs are NOT bulk-accessible online today

### 6.3 OGE Form 278-T (cabinet / executive branch) — done, 2026-05-23

**Why.** With staff ruled out (§6.2), OGE 278-T was the next smallest
viable new-data option that's PTR-shape and engine-compatible. 278-T
is the Periodic Transaction Report for the executive branch — same
shape as Congress PTRs (value brackets, 30/45-day rule, per-trade
events).

**Data access — what's actually online (correction to earlier memory).**
The pre-work memory entry under `reference_political_disclosure_sources` (Claude memory, not in this repo)
claimed OGE 278-T data was "PDF only via Form 201 request." That was
incomplete. The actual state as of 2026-05-23:

- **For Presidentially-Appointed Senate-confirmed (PAS) officials** —
  Cabinet secretaries, deputy secretaries, agency heads, ~700+ filers
  by ProPublica's Trump-Town count — 278-T PDFs are **directly
  downloadable online** at `extapps2.oge.gov/201/Presiden.nsf/...`,
  no Form 201 request needed. This includes the President, VP, every
  cabinet member, and most sub-cabinet positions confirmed by the
  Senate.
- **Form 201 is for non-PAS executive employees** (lower-level GS
  officials who file under STOCK Act but were carved out of online
  publication by S.716 in 2013 — same carve-out that affected
  congressional staff). Those filings exist on paper at OGE's
  reading room.
- **6-7 year retention.** Federal law requires OGE to destroy most
  public financial disclosure reports 6-7 years after creation, so the
  online universe is bounded to roughly 2019+.

So Form 201 isn't part of our ingest path — direct online scraping is.

**Engineering shape (built, not theoretical).**

The OGE site is a Lotus Notes (IBM Domino XPages) app with a
JavaScript-rendered DataTables interface; bare curl can't enumerate
filings because the table data loads via an XPages REST endpoint
(`/API.xsp/v2/rest`) that needs session cookies. We drive it through
headless Chromium.

| Step | Tool | Notes |
|---|---|---|
| Index | Playwright headless Chromium drives the "PAS Filings by Date" view | Pages through 16,794 total entries via DataTables API; filters to the 318 with type='278 Transaction' and a direct doc URL |
| Download | httpx | Each PDF is ~10kB–13MB depending on form length |
| Parse | New `parse_278t_text()` in `ptr_parser.py` | Dispatched via `_is_278t()` detection on the form header; two layouts handled |
| Schema | `members.filer_type` ('member'/'executive') + `members.position_title` | Same `members` table; one Position row per filer with `role_type='executive_cabinet'` covering their filing window |
| Engine | New `filer_types: list[str] \| None` param on event_study / rules_backtest / train_test_analysis | Default None preserves existing behavior; analysis paths pass `['executive']` or `['member']` to scope |
| Recs | All 4 existing RISK_PROFILES pinned to `filer_types=['member']` | Cabinet data lives alongside Congress in the same `members` table but doesn't silently appear in live snapshots |

Pipeline lives in `backend/ingest_oge.py` as a two-phase CLI:
`python ingest_oge.py all` runs scrape + parse + DB-insert end-to-end.

**278-T parser specifics.** Three observed table layouts:
- Trump's pre-2024 form: row number and notification flag on a previous
  physical line; description / Type / Date / Amount inline.
- Burgum/Phelan-era 2024+ form: `#  description  See Endnote  Type  Date
  Yes|No  Amount` all on one line, with amount-wrap when the upper
  bound is large (e.g. `$1,000,001 -\n$5,000,000`).
- Trump's 05/08/2026 (2) filing: badly OCR-garbled text layer
  ("curchue" for "Purchase") — parses 0 rows; would need re-OCR to
  recover ~1,838 transactions, deferred.

Parser anchors on the (Type word + Date + Amount range) triple and
recovers description from the prefix. Tolerates poppler artifacts:
`$1 ,000,000` space-inside-numbers, `•` as range separator.

**Ticker extraction.** Reuses the House PTR `TICKER_LOOSE_RE` against
the description. ~60% of 278-T asset names embed the ticker in
trailing parens ("Microsoft Corp. (MSFT)", "Meta Platforms Inc.
Class A Common Stock (META)"); the other 40% are PE/VC funds,
fund-of-funds, and muni bonds with no resolvable public ticker.
A name→ticker lookup table could recover some equity rows that
don't include parens ("Meta Platforms Inc" without `(META)`, etc.);
deferred.

**Ingest results (2026-05-23):**

| Metric | Value |
|---|---|
| PAS filings in OGE index (all types) | 16,794 |
| Of those, "278 Transaction" type | 318 |
| Filings parsed | **318 / 318 (0 failures)** |
| Unique executive filers | 78 |
| Transactions extracted | **5,033** |
| Of those, with public-ticker resolvable | **3,057 (60.7%)** |
| Year coverage | 2020 – 2026 (full retention window) |

Top filers by trade volume:

| Filer | Role | n |
|---|---|---|
| Andrew Saul | SSA Commissioner (Trump 1) | 676 |
| Arjun Mody | Deputy Commissioner | 301 |
| Christopher A Wright | Energy Sec (Trump 2) | 301 |
| Eric S Lander | OSTP Director (Biden) | 263 |
| Donald J Trump | President | 247 |
| Nuria Fernandez | FTA Administrator (Biden) | 245 |
| Barbara M Barrett | (former Air Force Sec) | 196 |
| Donald Remy | Deputy Secretary | 165 |
| Linda E McMahon | (former SBA / Education) | 156 |
| Douglas J Burgum | Interior Sec (Trump 2) | 147 |

Year distribution shows the expected administration-transition surges:
2020: 736, 2021: 1,242 (Biden divestiture), 2022-24: 133-346, 2025:
1,808 (Trump 2 divestiture), 2026: 515.

**First-cut analysis (single-call event study, not yet a persisted
AnalysisRun — see §6.4).** OOS 2025 window, same as v1 #7/#8 / v2.5
#55-58:

| Population | n | mean excess vs SPY | t | mean excess vs RSP | t |
|---|---|---|---|---|---|
| Congress purchases | 782 | +0.67% | 1.04 | +1.98% | **3.0** |
| **Cabinet purchases** | **348** | **−0.93%** | **−1.32** | +0.33% | 0.48 |
| Cabinet sales | 522 | −1.31% | — | +0.59% | — |

**Genuine population contrast.** Cabinet purchases underperform SPY
where congressional purchases mildly outperform. Consistent with the
divestiture-selection story flagged up-front: cabinet officials forced
to dump winners to satisfy ethics rules end up with worse-than-average
buys. The vs-RSP gap (essentially zero for cabinet, significant for
Congress) suggests Congress retains an equal-weight tilt advantage
that cabinet doesn't.

This is the **first time the project has surfaced a directional
difference between two populations** — v1, v2, and v2.5 all landed
on near-null. Cabinet underperforming SPY at t=−1.32 isn't
statistically significant yet but the sign is the opposite of
congressional purchases, which is interesting in itself.

**Constraints to keep in mind when interpreting cabinet results.**

- **2025-01-03 trifecta cohort confound** is severe in the cabinet
  dataset. All cabinet officials turned over at admin change, so
  party / regime / leadership variables are highly collinear with
  `is_post_trifecta`. Worse than v2's leadership-cut version of the
  same confound because cabinet turnover is total.
- **Divestiture self-selection.** Cabinet officials often divest into
  qualified blind trusts (certificate of divestiture under 26 USC
  §1043). The cabinet members who still trade openly are a
  self-selected slice — structurally different from Congress, not
  just smaller.
- **Small N.** 3,057 ticker-resolvable trades across 5 years and 78
  filers is comparable in size to v2.5's spouse cut (2,358 SP+JT
  rows), so the HDI on any cabinet-specific Bayesian coefficient
  will be similarly wide.

### 6.4 v2.6 cabinet analysis runs (done — 2026-05-24)

Four `AnalysisRun` rows persisted (#64-67), mirroring the v1 #7/#8
(fixed_horizon) and v2.5 #62 (mirror+beta-stop with extended OOS
window for recommendation-comparable NAV curves) patterns. All four
scoped via `filer_types=['executive']`, leadership_only=False,
owner_filter='any', filing_date anchor, hold=63, min_members=1.

| Run | Exit | Bench | Test end | Train excess | OOS excess | OOS strat | OOS bench |
|---|---|---|---|---|---|---|---|
| #64 | fixed_horizon | SPY | 2025-12-31 | −46.84% | **−3.16%** | +14.85% | +18.01% |
| #65 | fixed_horizon | RSP | 2025-12-31 | −27.39% | +3.31% | +14.85% | +11.54% |
| #66 | mirror+beta-stop | SPY | 2026-04-30 | −7.67% | **−9.10%** | +15.60% | +24.71% |
| #67 | mirror+beta-stop | RSP | 2026-04-30 | +11.78% | −3.36% | +15.60% | +18.96% |

**Headline (rules-backtest):** the cabinet population underperforms
SPY consistently in 2025 OOS (−3.16% on the shorter window, −9.10%
on the longer window matched to recommendation profiles). Performance
against the equal-weight RSP is barely positive on the shorter window
(+3.31%) and negative on the longer (−3.36%). This is the same
directional signal as the one-off event study captured (cabinet
purchases −0.93%/trade vs SPY at t=−1.32, n=348) — the rules
backtest puts the population-level number in the same place.

**Train-period numbers are uniformly terrible** (−47% to +12%), but
that's an artifact of the data shape: 2021-2024 has only 1,972
cabinet trades (vs 1,808 in 2025 alone), and the early Biden cabinet
in particular concentrated heavily in divestiture-driven sales
during 2021. The strategy of "follow cabinet trades" on that period
just isn't long enough or balanced enough to backtest cleanly.

**Comparison to congressional baseline** (v1 #7/#8 / v2.5 #55-58
shape):

| Population | OOS excess vs SPY | OOS excess vs RSP |
|---|---|---|
| Congress (v1 #7/#8) | −2.82% | +0.09% |
| Spouse-touched (v2.5 #55/#56) | +8.43% (sequencing) / +0.07% (per-trade) | +14.90% (sequencing) / +1.62% (per-trade) |
| Self-only (v2.5 #57/#58) | −0.94% | +5.53% |
| **Cabinet (v2.6 #64/#65)** | **−3.16%** | +3.31% |
| **Cabinet (mirror, #66/#67)** | **−9.10%** | −3.36% |

Cabinet is the first population in the project that's **clearly
worse than Congress** vs SPY. Consistent with the divestiture
self-selection story called out up front (§6.3 constraints): cabinet
officials forced to dump positions to satisfy ethics rules don't
optimize for return, and the residual trading pool is structurally
adverse-selected.

**Bayesian re-fit (deferred).** A clean cabinet Bayesian model is
non-trivial because:
- Cabinet rows have NULL `chamber` and NULL `party`, so the
  existing `in_majority` / `party_R` features don't apply. They'd
  need to be coerced to 0 or replaced with cabinet-specific
  encodings (e.g., `is_trump_cabinet` vs `is_biden_cabinet`).
- 2025-01-03 trifecta is perfectly collinear with the cabinet
  party-change in the data: almost all post-trifecta cabinet trades
  are Trump-2-era. An `is_executive` coefficient and
  `is_post_trifecta` would be near-impossible to disentangle.
- A separate cabinet-only Bayesian model (78 filers, 3,057
  ticker-resolvable trades) would be feasible but loses the
  comparison-to-Congress angle.

Deferred to a follow-up session with a clearer methodological
choice on which Bayesian shape best preserves cabinet vs. Congress
interpretability. The rules-backtest result (#64-67) is the
primary v2.6 finding for now.

**Recommendation-engine note.** No `cabinet_favored` profile was
added. The rules-backtest result is negative against both benchmarks
on the longer-window mirror config (#66/#67 — the shape that backs
the other live profiles), so there's no useful strategy to expose
in the UI. Cabinet data sits in the database for analytical use; the
4 live profiles remain congressional-only (per the `filer_types=['member']`
pin landed in `b06e944`).

### 6.5 Price-coverage repair + the visibility cut (2026-10-24, runs #69–71)

**Why this came first.** Picking the project back up after ~5 months, two
infrastructure problems turned out to be load-bearing for the analysis, so
they got fixed before any new cut was run.

**Ingestion had silently stopped.** There was exactly one scheduled job in
the system — the 6am-ET recommendation snapshot. Scraping only ever
happened when someone opened the Admin panel and clicked it, and nothing
surfaced the omission: the snapshot job rebuilt recommendations every
morning on whatever was in the database. Found 9 days stale (19,797
transactions, latest filing 2026-10-15, both unchanged from the previous
session) while prices and snapshots kept updating normally. A second
APScheduler job now runs the scrape at 04:00 ET, two hours ahead of the
snapshot, and writes its outcome to `app_settings['ingest.last_run']`,
surfaced on the Recommendations freshness banner.

**Senate eFD is now closed to automated access.** The first scheduled run
isolated chambers, which immediately earned its keep: House ingested 7
filings / 12 transactions, Senate returned 403. The refusal is at the edge
— the *agreement POST itself* returns a WAF "Access Denied" page rather
than a Django error — so this is an access decision at the source, not a
bug to fix or something to work around. Senate data stops at 2026-10-07
and is 46% of the congressional dataset (6,819 of 14,776 transactions).
**This is an open data-scope problem**, not a solved one: any analysis run
after 2026-10 is increasingly House-only unless an authorized route is
found.

**Price coverage was biased, not just incomplete.** 335 of 2,003 disclosed
tickers had no price bars. Two separate causes:

1. *No symbol resolution.* `BRK.B` was never going to fetch — the vendor
   spells it `BRK-B`. That one string was the single largest gap (67
   transactions). Spin-off cells naming two entities ("T WBD", "JNJ KVUE"),
   `--` placeholder rows, and parser pickups that were never tickers
   (APPLE, ETF, NYSE) all failed the same way.
2. *No fetch bookkeeping.* `ensure_prices()` treated a ticker as needing a
   fetch whenever its bars didn't span the window — permanently true for
   any delisted name, and for any company that IPO'd after the window
   start. ~300 dead symbols were re-requested on every snapshot and
   backtest run and failed every time.

The analytically important part is **which** names were missing: they skew
toward companies that were acquired, so every backtest had been quietly
dropping them. Acquisitions typically close at a premium, which biases
measured returns *downward* — an unquantified bias sitting underneath the
project's headline null. Resolution deliberately does **not** map acquired
or renamed companies onto successor symbols; splicing two price series
across a corporate action would fabricate returns. Those 248 remaining
unresolved tickers need a sourced corporate-action mapping, which is the
natural follow-up.

Measured: tickers with bars 1,675 → 1,762; transactions priceable 93.8%;
a live recommendation recompute 74s → 5.6s.

**The visibility cut.** New engine parameter `visibility ∈ (any, high,
low)`, built to test whether trades in obscure companies carry more
information than trades in household names.

*Metric choice mattered more than expected.* Market cap misses the
distinction entirely — Eaton and Parker Hannifin are ~$100B industrials
that never make the news. Turnover (volume / market cap) actively inverts
it, since mega-caps churn less relative to their size: on live data it
ranked GOOGL (21.7) below Medpace (138.6). Median daily **dollar volume**
is what tracks attention, and a p95 cut classifies the motivating examples
correctly — MU/NVDA/GOOGL/TSLA high, ETN/PH/MEDP low. High visibility is a
narrow slice: 130 of 1,611 traded tickers.

*Point-in-time by construction.* Ranking on today's volume would be
look-ahead — NVDA's current turnover reflects the AI boom, not its 2021
standing. `ticker_visibility` buckets by month and ranks cross-sectionally
within the month; lookups resolve to a month strictly before the filing
date. NVDA's ADV runs $1.3B (2020) → $32.7B (2026) and the monthly top-10
genuinely turns over (2021: NIO, BABA; 2026: MU, SNDK, AMD).

**Result — the hypothesis is backwards.**

| tier | train excess | OOS excess | event-study buy 63d (test) |
|---|---|---|---|
| high | +25.12% | +8.14% | +2.41%, t=2.03, n=274 |
| low | −32.46% | +0.71% | −0.10%, t=−0.13, n=506 |
| any | −18.09% | +4.42% | +0.76%, t=1.19, n=790 |

Rules NAV and per-trade event study agree in direction (per §5.4's
dual-report rule). The *robust* number here is the training window, where
low-visibility buys are significantly **negative** across four years
(−0.90%, t=−2.36, n=2,073): niche congressional trades underperform rather
than outperform.

**The 2025 high-visibility number should not be read as an edge.** It is
fat-tailed — dropping 5 of 273 trades (INTC +86%, MSTR +57%, NVDA +49%,
LLY +45%, TSLA +44%) takes the mean from +2.46% to +1.46%. It is a single
test year, so the §3.7 trifecta-cohort confound applies. And "high
visibility" in 2025 is close to a definition of the AI/crypto momentum
trade, so this cut plausibly measures factor exposure rather than
congressional information. Establishing otherwise needs a momentum control
in the Bayesian spec, which is the obvious next step.

**Methodological note worth keeping.** The first pass at these runs used
`analyze.py` without `--filer-types`, which defaults to *all* filers and
silently mixed cabinet/OGE trades into a "congressional" comparison
(n=314 vs 274). `RISK_PROFILES` pins `filer_types=['member']` for exactly
this reason; the CLI does not. Also note `analyze.py` does **not** persist
an AnalysisRun — only `POST /api/model/run` does, which is why #69–71 were
re-run through the API.

---

### 6.6 Senate eFD access — investigation, and the decision not to route around it (2026-10-24)

§6.5 recorded the Senate block as a bare fact. This is what it actually
is, what was ruled out, and the decisions taken — including one
deliberate *non*-action.

**What the block is.** Not an application-level refusal and not (only)
an IP ban:

| Request | Result | Reading |
|---|---|---|
| `GET /search/home/` | 200, `server: gunicorn` | reaches the Django app normally |
| `POST /search/home/` (agreement) | 403, Akamai `Access Denied` + reference # | stopped at the CDN edge |
| `POST /search/report/data/` | 403, same | unreachable; the agreement gates it |

The `server: gunicorn` header on the GET and its absence on the 403 is
the diagnostic: the two requests are answered by different systems. Every
search requires the agreement POST, so the effect is total for any
non-browser client.

**Ruled out — CSRF handling.** The first hypothesis was our own bug:
Django rotates the CSRF token on POST and `senate_scraper._authed_client`
reuses the token scraped from the form. The token *does* rotate, but
correcting it changes nothing, because the POST never reaches Django.
Not our bug.

**Ruled out — datacenter-IP reputation, as a complete explanation.** The
most-cited community claim is that Akamai blocks datacenter ranges
([senate-efd-relay](https://github.com/Codejrangel/senate-efd-relay)).
Our egress is residential Comcast (AS7922) with no VPN — `nordvpnd`
inactive, no tunnel interfaces — and we are still blocked on the POST,
while projects on datacenter hosts report 403 on the *GET*
([Nova #1143](https://github.com/aaltaay/Nova/issues/1143)). The controls
are layered; IP reputation is one layer, not the whole story.

**Timeline, from four unrelated projects.**
[rocket #69](https://github.com/jhonnyisaacc/rocket/issues/69) 2026-10-06
(Akamai edge 403) · our last captured Senate filing 2026-10-07 ·
[13FRadar #144](https://github.com/mustafakocer/13FRadar/issues/144)
(agreement refused) · Nova #1143 2026-10-23.

**No policy behind it, and no replacement.** `robots.txt` 404; `/api/`,
`/api/v1/`, `/search/api/`, `/bulk/`, `/data/`, `/sitemap.xml`,
`/openapi.json` all 404; no banner, Senate release, Ethics notice, or
Federal Register item. The prohibition agreement quotes the Ethics in
Government Act (5 U.S.C. app. §105(c)), which restricts the *purposes* a
report may be obtained or used for — unlawful, commercial other than news
media, credit rating, soliciting money — and says nothing about method.
Nothing published forbids automated collection; nothing published offers
an alternative to it.

**Decision: do not circumvent.** TLS/JA3 fingerprint matching, UA
rotation, and routing through hosts whose ranges are unblocked (GitHub
Actions runners are reportedly not blocked) are all available and all
rejected. A deliberate control signals intent whether or not a rule is
written down. A diagnostic that cycled User-Agents was blocked by tooling
policy as bot-check circumvention, correctly. The ingest tags 403/429 as
`blocked` and keeps attempting in case it lifts; `INGEST_CHAMBERS=house`
disables the chamber.

**Decision: pursue supported access instead.** Tiered outreach packet
drafted 2026-10-24 — Tier 1 the offices that own the system (Office of
Public Records, Secretary of the Senate, Ethics Committee, Congressional
Data Task Force), Tier 2 data-access advocates, Tier 3 STOCK Act
watchdogs, Tier 4 press held in reserve.

**Decision: do NOT lead with the §105(c) vendor-enforcement angle.**
Tempting and rejected, for three reasons. (1) The statute exempts "news
and communications media for dissemination to the general public," which
a vendor with a public site and a newsroom can credibly claim — the
hypocrisy case is weaker in law than it feels. (2) No vendor publishes
its collection methodology, so asserting they scrape is unevidenced; they
may license, use human review, or have an arrangement we don't know
about. (3) Decisive: telling an institution "people are commercially
exploiting this data" invites *tightening*, not opening. It argues
against our own goal. The same underlying observation is kept, reframed
as access equity — the control doesn't stop commercial access, it only
stops the free and independent kind — which supports restoring access
rather than restricting it. Held in reserve as a *response*, usable only
if the Senate asserts that automated access is impermissible, at which
point uneven application becomes relevant.

**Working hypothesis: overbroad bot-management deployment, not policy.**
Favouring it: no announcement; no `robots.txt` (a real anti-automation
policy normally publishes directives); no replacement offered; the
GET/POST asymmetry is the classic signature of a default Bot Manager
ruleset scoring state-changing requests on TLS fingerprint; and many
government sites rolled out exactly this through 2025–26. Against it:
only the timing — the Senate
[blocked a congressional stock-trading bill on 2026-09-30](https://gvwire.com/2026/09/30/bill-to-limit-stock-trading-in-us-congress-blocked-in-senate/),
about a week prior. That is coincidence with no supporting evidence and
is explicitly excluded from the outreach; recorded here only so a future
reader doesn't rediscover it and over-read it.

This hypothesis sets the tone of the outreach: a misconfiguration gets
fixed by a polite technical email to the right engineer, whereas a public
accusation converts it into a defended policy position.

**Open — data scope.** Senate is 46% of congressional transactions (6,819
of 14,776) and stops at filing date 2026-10-07. Any cut run after that
date drifts House-only. **Check the chamber mix before describing a
post-2026-10 result as "congressional."**

---

### 6.7 Re-run on the deduplicated universe (2026-10-24, runs #72-74)

**Why.** §6.6's dedupe removed 1,194 of 13,290 ticker-bearing rows (9.0%).
Runs #69-71 were computed before it, so the visibility comparison was re-run
unchanged on the corrected universe.

**Rules-engine result moves up across the board.**

| tier | run | train excess | OOS excess | | run | train excess | OOS excess | ΔOOS |
|---|---|---|---|---|---|---|---|---|
| high | #69 | +25.12% | +8.14% | → | #72 | +24.14% | **+11.57%** | +3.43pp |
| low | #70 | −32.46% | +0.71% | → | #73 | −27.49% | **+2.88%** | +2.17pp |
| any | #71 | −18.09% | +4.42% | → | #74 | −15.54% | **+6.62%** | +2.20pp |

The ordering survives (high > any > low), so the qualitative read of §6.5
stands. The magnitudes do not.

**The per-trade event study is where the real damage is.** Buy @ 63d:

| tier | | train n | train mean | t | test n | test mean | t |
|---|---|---|---|---|---|---|---|
| high | before | 805 | +0.20% | 0.31 | 274 | +2.41% | 2.03 |
| high | after | 760 | −0.05% | −0.08 | 250 | +2.61% | **2.09** |
| low | before | 2073 | −0.90% | **−2.36** | 506 | −0.10% | −0.13 |
| low | after | 1972 | −0.75% | **−1.91** | 475 | +0.41% | 0.55 |
| any | before | 2929 | −0.64% | **−2.00** | 790 | +0.76% | 1.19 |
| any | after | 2778 | −0.60% | **−1.82** | 735 | +1.14% | 1.75 |

**§6.5's headline claim is retracted.** It read: "the robust number is the
training window, where low-visibility buys are significantly negative across
four years (−0.90%, t=−2.36, n=2,073): niche congressional trades
underperform." On the deduplicated universe that is **−0.75%, t=−1.91** and
no longer clears the conventional threshold. The broad-universe train result
drops below it too (−2.00 → −1.82). The effect was partly an artifact of
counting the same trade more than once.

**Why duplicates inflated significance — the generalisable point.** The
t-statistic is mean / (sd / √n). A re-reported or repeated line item adds to
n without adding an independent observation, so it shrinks the standard error
while leaving the mean roughly unchanged. Classic pseudo-replication: the
estimate barely moves and the confidence around it is fictitious.

**This is not limited to the visibility work.** Every run before #72 — the
v1 null, the v2 leadership cut, the v2.5 spouse cut, the v2.6 cabinet runs,
and both Bayesian fits — was computed on the duplicated universe. Point
estimates are largely unaffected (they barely moved here), but **every
reported interval, t-statistic and posterior probability is too narrow.**
The v2.5 `is_spouse_owned` posterior of P(β > 0) = 0.892 in particular was
never credible at 95% and should be expected to weaken further on a re-fit.
Re-fitting the Bayesian model on canonical rows is the obvious next step and
is not yet done.

**The high-visibility 2025 result remains fat-tailed and should not be read
as an edge.** On the deduplicated data the test mean is +2.66% over 249
trades, but the same five names carry it — INTC +86%, MSTR +57%, NVDA +49%,
LLY +45%, TSLA +44%. Excluding the top 5 the mean is +1.56%; excluding the
top 10, +0.76%. Ten of 249 trades carry roughly 70% of it. Combined with the
single test year and the fact that "high visibility in 2025" closely tracks
the AI/crypto momentum trade, the t = 2.09 is not evidence of congressional
information.

**Net read after the correction: no tier shows a credible effect.** The
niche-underperformance finding was partly duplication; the household-name
outperformance is a handful of momentum names in one year.

---

### 6.1 Policy-event windows (roadmap, on deck)

**Why.** Wei & Zhou's information-channel hypothesis predicts the
largest informational edge precisely *around* discrete policy events
(Fed announcements, debt-ceiling votes, committee markups, regulatory
action dates). v1/v2/v2.5 tested the broad population, the leadership
subgroup, and the spouse-touched subgroup — but never the
*event-windowed* slice across any of those.

**Status.** Roadmapped, not started. Promoted after the 2026-05-23
international-disclosure survey eliminated cross-jurisdictional
replication as a near-term path (see above).

**Shape.** Stratify existing US trades by whether they fall within a
±N-day window around dated policy events. Reuses the engine
unchanged — adds an event-window filter parameter analogous to
`leadership_only` / `owner_filter`.

**Data needed.** A dated event table. Initial scope candidates,
roughly in order of cost:
- FOMC meeting dates (FRED has these structured) — pure macro,
  affects everything; cleanest first test
- Major congressional vote dates (debt-ceiling, continuing-resolution
  deadlines, defense appropriations) — Congress.gov bulk data
- Committee markup dates by committee (would interact with v2
  positions data) — Congress.gov / GovTrack
- Regulatory action dates (Federal Register / agency press
  releases) — heavier ingest, defer

**Engine change required.** Small. Add a `policy_event_windows: list[date]`
(or a dedicated table) and a `window_days: int` parameter. Filter
events to those whose `filing_date` (or `transaction_date`, for
diagnostic) falls within `window_days` of *any* event in the list.

**What this does NOT do.** It doesn't add a new theoretical primitive
— it's still the same Wei & Zhou informational channel, just
tested with a tighter time-window prior. If the broad / leadership /
spouse cuts all came back null or borderline-null, an event-windowed
cut is a natural way to ask "was the signal there but diluted by
non-event noise?"

---

## 7. Run catalog

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
| #18 | 2026-05-21 | filing_date | — | — | — | — | **`bayesian_hierarchical`** (leadership only) |
| #55 | 2026-05-23 | filing_date | SPY | no | [63] | [1] | **owner=spouse_or_joint** (v2.5) |
| #56 | 2026-05-23 | filing_date | RSP | no | [63] | [1] | owner=spouse_or_joint, RSP |
| #57 | 2026-05-23 | filing_date | SPY | no | [63] | [1] | owner=self |
| #58 | 2026-05-23 | filing_date | RSP | no | [63] | [1] | owner=self, RSP |
| #59 | 2026-05-23 | filing_date | — | — | — | — | `bayesian_hierarchical` first fit (R̂=1.02; superseded) |
| #60 | 2026-05-23 | filing_date | — | — | — | — | **`bayesian_hierarchical` with `is_spouse_owned`** (canonical v2.5) |
| #62 | 2026-05-23 | filing_date | RSP | no | [63] | [1] | **`spouse_favored` backing run** (owner=spouse_or_joint, mirror+beta-stop, test→2026-04-30); OOS excess +27.17% — same sequencing caveat as §5.4 |
| #64 | 2026-05-24 | filing_date | SPY | no | [63] | [1] | **v2.6 cabinet** filer_types=['executive'], fixed_horizon, test→2025-12-31; **OOS excess −3.16%** vs SPY |
| #65 | 2026-05-24 | filing_date | RSP | no | [63] | [1] | v2.6 cabinet, fixed_horizon RSP; OOS excess +3.31% |
| #66 | 2026-05-24 | filing_date | SPY | no | [63] | [1] | v2.6 cabinet, mirror+beta-stop, test→2026-04-30; **OOS excess −9.10%** vs SPY |
| #67 | 2026-05-24 | filing_date | RSP | no | [63] | [1] | v2.6 cabinet, mirror+beta-stop RSP, test→2026-04-30; OOS excess −3.36% |
| #69 | 2026-10-24 | filing_date | SPY | no | [63] | [1] | **visibility=high** (p95 dollar-volume), filer_types=['member']; OOS excess **+8.14%** — fat-tailed, see §6.5 |
| #70 | 2026-10-24 | filing_date | SPY | no | [63] | [1] | **visibility=low**, filer_types=['member']; OOS excess +0.71%; train −32.46% (t=−2.36 per-trade) |
| #71 | 2026-10-24 | filing_date | SPY | no | [63] | [1] | visibility=any baseline, filer_types=['member']; OOS excess +4.42% |
| #72 | 2026-10-24 | filing_date | SPY | no | [63] | [1] | **visibility=high, DEDUPLICATED** (§6.6); OOS excess +11.57%; test t=2.09 but fat-tailed |
| #73 | 2026-10-24 | filing_date | SPY | no | [63] | [1] | **visibility=low, DEDUPLICATED**; OOS +2.88%; train t=−1.91 — §6.5's "significant" result does not survive |
| #74 | 2026-10-24 | filing_date | SPY | no | [63] | [1] | visibility=any baseline, DEDUPLICATED; OOS +6.62%; train t=−1.82 |

---

## 8. Public deployment via Cloudflare Tunnel + Access (2026-05-22)

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

### eMudhra certificate compromise — cloudflared patched (2026-10-29)

**What.** Cloudflare disclosed on 2026-10-19 that private keys for eight
eMudhra intermediate certificates were compromised, and that the keys are
weak enough to forge a certificate for any domain. Not a code defect: the
exposure is that `cloudflared`'s certificate handling trusted those
intermediates, so an attacker with network position could impersonate the
Cloudflare edge to our connector. Cloudflare's remediation for Tunnel is
"upgrade to 2026.10.2".

**Done.** NUC upgraded 2026.5.0 → 2026.10.5 (latest; above the stated
minimum), service restarted, all four tunnel connections re-registered,
`goosebase.org` verified serving. Previous binary kept for rollback during
the change.

**Root cause of being five months stale, which is the more useful finding.**
The apt source pointed at the `trixie` suite, which Cloudflare does not
publish — `dists/trixie/Release` returns 404 while `any`, `bookworm`,
`bullseye`, `jammy` and `noble` all return 200. `apt-get update` reported
the error, but nothing surfaced it and `apt-cache policy` simply showed the
installed version as its own candidate, so the box looked up to date. With
`--no-autoupdate` set on the unit, there was no second path. Source
corrected to `any`.

**eMudhra roots distrusted on the host (done).** The system store carried
four eMudhra **root** CAs that the compromised intermediates chain to —
`emSign Root CA - C1`, `emSign Root CA - G1`, `emSign ECC Root CA - C3`,
`emSign ECC Root CA - G3`. Debian's `ca-certificates` is at 20250419 with
no update available, so they were distrusted by hand: `!` prefix on those
four lines in `/etc/ca-certificates.conf`, then `update-ca-certificates`
(151 → 147 trusted, 0 `emSign` left in the bundle). Outbound TLS
re-verified afterwards to pkg.cloudflare.com, github.com, api.github.com,
Yahoo, disclosures-clerk.house.gov and the git remote — all fine. Note the
backend runs in a container with its own trust store, so container-side
fetches are a separate surface this does not touch.

**Tunnel token taken off the command line (done).** The unit held the token
inline in `ExecStart` in a mode-0644 file, so any local user could read it
— and systemd put it on the process command line, so `ps` exposed it too.
Moved to `/etc/cloudflared/tunnel-token` (0600 root:root) and the unit
switched to `--token-file`, which keeps it out of both. Verified: no secret
left in the unit, 4/4 connections registered, site serving.

**Debian security updates (done).** libgcrypt20, libgnutls30t64 and the
four krb5 libraries, all from `Debian-Security`. `needrestart` is not
installed, so nothing was bounced automatically. Postgres, the app and both
sites verified afterwards. TimescaleDB 2.27.0 → 2.27.1 was deliberately
left: it is a point release rather than a security fix and touches the
database.

**Two things NOT finished, both needing a decision:**

1. **A second tunnel on this host is still running the vulnerable binary.**
   `cloudflared-mysecretmenu.service` (mysecretmenu.com) has been up since
   2026-06-08, and `/proc/<pid>/exe` reports `(deleted)` — the upgrade
   replaced the binary underneath it, so it is still executing 2026.5.0 in
   memory. A `systemctl restart cloudflared-mysecretmenu` fixes it, costs
   a few seconds of downtime on that site, and was left alone because it
   is a workload outside this project. Its unit already uses an
   `EnvironmentFile`, but systemd expands `${TUNNEL_TOKEN}` onto the
   command line, so it has the same `ps` exposure that was just fixed here
   — `--token-file` would close it.
2. **Postgres still maps the pre-upgrade krb5 libraries.** Every backend
   has 22 deleted mappings, including `libkrb5`, `libgssapi_krb5`,
   `libk5crypto` and `libkrb5support`; the fix does not take effect until
   it restarts. Practical risk is low — this instance authenticates by
   password, not GSSAPI — but it is not zero, and a restart is a real
   outage for the project database.

---

### Planned migration of the app stack to the NUC (2026-05-22, scoped)

**Why now.** The dev box is online ~16h/day. Moving the user-facing app
stack (backend + frontend + cloudflared) onto the NUC lets the dev box
shut off at night without breaking the public URL. The NUC has run
Postgres for this project from the start and is already 24/7.

**Performance estimate.** The NUC's i5-1240P (12C/16T, 4P + 8E,
4.4 GHz P-core boost) benches at ~87% of the dev box (Ryzen 5 7600X) on
single-thread and ~62% on multi-thread (PassMark). Backtest workloads in
this codebase are mostly single-threaded pandas, so the expected
wall-clock penalty is ~15–30% — a 30-second backtest becomes 35–45s.
Loopback DB instead of LAN hop claws back ~5–15 ms per query, so the
Analysis-view net penalty is smaller still. Bayesian fitting would be
~1.5–2× slower **and** would OOM at 3.6 GB RAM, so it's explicitly
staying on the dev box (admin-only invocation anyway).

**NUC headroom prep, executed 2026-05-22:**

| Change | RAM impact | Rationale |
|---|---|---|
| Disabled `nodm` + XFCE session; default target → `multi-user.target` | freed ~600 MB | Headless server, no need for a graphical session |
| Disabled `nordvpnd` + `nordvpnd.socket`; brought down `nordlynx` WireGuard interface | freed ~100 MB + restored direct LAN routing | Had we left the VPN up, cloudflared's outbound to CF edge would have routed through NordVPN — extra latency and DPI risk |
| Added 2 GB `/swapfile`, `vm.swappiness=10`, persisted in `/etc/fstab` | converts OOM-kill into slow request | 3.6 GB RAM is tight; swap is cheap insurance |

Net before/after: **1.3 GB available → 2.6 GB available**, plus 2 GB
swap on standby.

**Shelved: RAM upgrade.** The NUC has 3.6 GB physical RAM (effective; the
remainder is reserved by integrated GPU / firmware). A 2×8 GB DDR4-3200
SODIMM kit currently runs ~$205 (vs. ~$40 a year ago), which isn't worth
it for the marginal headroom. Revisit if RAM prices come down or if the
app evolves to need substantially more memory than the current backend
profile.

**Migration plan (zero-downtime via dual cloudflared connectors) — completed 2026-05-23:**

| # | Step | Status |
|---|---|---|
| 1 | Install Docker + compose plugin on NUC; clone repo; write `.env` with NUC-local `DATABASE_URL` | ✓ done |
| 2 | Edit `pg_hba.conf` to allow Docker bridge (`172.16.0.0/12`); reload Postgres | ✓ done |
| 3 | `docker compose -f docker-compose.yml -f docker-compose.nuc.yml up -d --build backend frontend` | ✓ done |
| 4 | Install cloudflared on NUC (`cloudflared 2026.5.0`) with same tunnel token; systemd service | ✓ done |
| 5 | Verified CF dashboard shows two healthy connectors | ✓ done |
| 6 | Stopped cloudflared service on dev box | ✓ done |
| 7 | Dev box can now sleep nightly | ✓ done |

Cloudflare's tunnel layer load-balances across healthy connectors, which
is what makes step 4–5 a true zero-downtime cutover: traffic naturally
shifts between the two as we go, and step 6 just removes one of the two
healthy paths.

**Architectural choices:**
- **Bridge networking + `host-gateway` for the backend on the NUC.** The
  backend container uses `extra_hosts: host.docker.internal:host-gateway`
  and `DATABASE_URL` pointing at `host.docker.internal:5432`. This lets
  the container reach native Postgres without host networking, while nginx
  (frontend) still reaches the backend by service name on the Docker
  bridge. The `- "8000:8000"` host port mapping was removed from the base
  `docker-compose.yml` — nginx proxies internally, so the host binding was
  never needed and conflicted with an existing process on the NUC.
- **The compose `db` service is dropped on the NUC.** Postgres is already
  running natively on `:5432` — the compose service would port-conflict.
- **Image builds happen on the NUC.** First build will be slow (~3–5 min
  for backend) due to the CPU gap. If backend-code iteration becomes
  painful, switch to build-on-dev-box / `docker save | ssh nuc docker
  load`. Not worth it preemptively.

**Dev override pattern:**

When active dev work is happening on the dev box, `scripts/dev-on.ps1`
(PowerShell, run from the dev box) starts the dev containers + local
cloudflared, then SSHes to the NUC to stop its connector — giving the dev
box exclusive traffic. `scripts/dev-off.ps1` reverses this. Requires
SSH key auth from dev box to `root@db.internal.example` (one-time setup documented
in both the scripts and CLAUDE.md).

**Wi-Fi caveat.** The NUC is currently on `wlan0`. Cloudflared reconnects
gracefully on packet loss, but users see occasional 5xx during reconnect.
Wired ethernet would be a free stability upgrade if a port is available
near the NUC.

**What still lives where after the migration:**

| Component | Host | Invocation |
|---|---|---|
| Postgres + TimescaleDB | NUC (native) | already there |
| Backend (FastAPI) + Frontend (nginx) | NUC, Docker | `docker compose` |
| `cloudflared` | NUC, systemd | `systemctl status cloudflared` |
| Backtest jobs (`POST /api/model/run`) | NUC, in backend container | via the Analysis UI or API |
| Bayesian fitting (`bayesian_model.py`) | **dev box** | admin-only, run when needed |
| Ad-hoc CLI (`analyze.py`, one-shot scripts) | either box | depends on whether dev box is on |

---

## 9. Methodological choices documented elsewhere

- [docs/v2_leadership_plan.md](v2_leadership_plan.md) — v2 scope, schema
  design, sequencing, and explicit deferral of cabinet / judges /
  international populations.
- [CLAUDE.md](../CLAUDE.md) — live status snapshot, current schema,
  running instructions.
- [reports/report_runs1-…-18_2026-05-21.html](../reports/report_runs1-2-3-4-5-6-7-8-9-10-11-12-13-14-15-16-17-18_2026-05-22.html)
  — current canonical rendered findings.
- Memory under `~/.claude/projects/.../memory/`:
  - `project_modeling_plan.md` — v1 conclusion summary
  - `project_v2_leadership_plan.md` — v2 step status
  - `feedback_parameterized_analysis.md` — every analysis must run
    through parameterized, traceable tools (firmly enforced)
  - `reference_political_disclosure_sources.md` — external data sources
    catalogue

---

## 10. Things explicitly NOT done, and why

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

## 11. Where someone picking this up next should look

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
