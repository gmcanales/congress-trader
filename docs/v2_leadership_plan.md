# Congress Trader — v2 Leadership Plan

**Status (2026-05-20):** step 1 done (bioguide_id backfilled), step 2 in
progress (committees/positions/party_control ingestion).
**Extends:** [v1 modeling plan](../CLAUDE.md) — v1 confirmed null on the
tradeable (filing_date) anchor; v2 narrows the universe to the
theory-driven leadership subset.
**Triggered by:** Wei & Zhou, *"Captain Gains" on Capitol Hill*, NBER WP, November 2025

---

## 1. Why v2

v1 found **no edge** in broad congressional disclosures: rank-and-file
trades show ~coin-flip win rates on both filing date and transaction date,
buys post slightly negative excess returns measured from the trade day, and
grid-searched configurations that look great in-sample collapse out-of-sample
(classic overfit, caught by the train/test split).

Wei & Zhou (2025), working on transaction-level data 1995–2021, **agree on
the rank-and-file null** — but isolate a subgroup that does not: the 20
lawmakers in their sample who ascended into leadership positions and traded
both before and after. Those leaders underperform their matched peers
*before* ascension and outperform them by **~47 percentage points/year
after** ascension. They attribute the alpha to two channels:

1. **Political influence** — higher returns when the leader's party
   controls the chamber; sales of stocks shortly preceding regulatory action
   against the issuer; purchases of stocks whose issuers receive more
   government contracts or favorable party support on bills.
2. **Corporate access** — trades that predict subsequent corporate news;
   greater returns on donor-owned and home-state firms.

This is a **theory-driven** subgroup — not a fishing expedition — so it
addresses the v1 warning to avoid post-hoc subgroup slicing.

## 2. What we'd need

### 2.1 Schema additions

| Table | Purpose | Notes |
|---|---|---|
| `members.bioguide_id` (new col) | Canonical join key to external datasets | Currently missing. Backfill via fuzzy match on (last_name, first_name, state_dst, chamber). |
| `committees` | Standing/select/joint/sub committee directory | id, thomas_id, name, chamber, type, parent_committee_id, jurisdiction |
| `positions` | Time-bounded role tenure per member | member_id, role_type ∈ {leadership, chair, ranking, member}, committee_id (nullable), title, congress, start_date, end_date (null=current), source |
| `party_control` | Chamber majority by congress | chamber, congress, majority_party, start_date, end_date |
| `v_stock_transactions_leadership` (view) | Trade-level view tagged with position-at-transaction-date and party_in_majority flag | Built on top of `v_stock_transactions`; what the v2 backtest consumes |

`v_stock_transactions` stays as-is so the v1 null result remains
reproducible against the same view we used originally.

### 2.2 Data sources

**Primary — Congressional leadership/committees:**
[`unitedstates/congress-legislators`](https://github.com/unitedstates/congress-legislators)
(public-domain YAML on GitHub).

* `legislators-{current,historical}.yaml` — bios + leadership tenure with
  start/end dates back to 1789 ✓
* `committees-{current,historical}.yaml` — committees back to the 93rd
  Congress (1973) ✓
* `committee-membership-current.yaml` — **current snapshot only**. The
  historical committee chair/ranking-member roster is the one gap; we'll
  supplement from Congress.gov bulk committee XML for 2021–2025.

**Deferred (v2.5+) — Federal judges:**
[Free Law Project judicial disclosures](https://free.law/projects/judge-db/)
(32k disclosure docs, 1.9M investment records, bulk CSV via S3) and the
[US Courts JEFS database](https://pub.jefs.uscourts.gov/) (2022+). Catch:
judges file the annual **AO-10**, not a periodic transaction report. You
get year-end holdings and a year-of-transactions summary, **not**
individual trade dates. That changes the methodology — holdings-based
portfolio replication rather than event studies. Worth doing later;
doesn't plug into the existing engine without rework.

**Deferred (v2.5+) — Cabinet / executive branch officials:**
OGE **Form 278-T** (Periodic Transaction Report) — same shape as Congress's
PTRs, so it *would* plug into the existing engine cleanly. Catch: no bulk
API. Public output is PDFs released via OGE Form 201 request. This
reproduces the existing House PDF scraping problem at a smaller scale (~25
cabinet + ~50 senior sub-cabinet officials). Reuse `services/ptr_parser.py`
and `services/ocr.py`. Same ~12% scanned-form failure rate expected.

**Deferred (v3) — International political leadership:**
Structurally harder. Most jurisdictions (UK Register of Members' Financial
Interests, Canadian COI disclosures, Australian Register of Members'
Interests) publish **holdings, not trades**, so the event-study framework
doesn't apply directly. "Leadership" itself is fuzzier outside the US
committee structure. Deferred until v2 produces or fails to produce a
result.

### 2.3 Coverage estimate

Our data is 2021–2025; Wei & Zhou is 1995–2021 → **only ~1 year of
overlap**. This means we **cannot** replicate their within-member
pre/post-ascension natural experiment (they used the full 26-year panel to
identify changes coincident with ascension). We can only do the
**cross-sectional** cut — leadership-tenure trades vs. rank-and-file trades
in the same window — which is statistically weaker.

Order-of-magnitude prior: at any time there are ~30 formal leadership slots
(Speaker, Majority/Minority Leader, Whip, Conference Chair + Senate
equivalents) plus ~36 committee chairs and ~36 ranking members ≈ **~100 of
535 members (~19%)** hold a "powerful" position. Trading is concentrated
in a few prolific names, so the share of *transactions* in leadership
windows could skew higher or lower than 19% depending on whether those
names sit in those slots.

**Prior on coverage: 1.5k–3k of the ~12k transactions** fall in a
leadership/chair window. Real number gets measured once `positions` is
populated; that's step 4 in the sequence below.

## 3. Sequencing

| # | Step | Status | Deliverable |
|---|---|---|---|
| 1 | Add `bioguide_id` to `members` + backfill | ✅ done | 311/313 matched (`backend/backfill_bioguide.py`); 2 unmatched are parser garbage |
| 2 | Ingest `unitedstates/congress-legislators` | 🟡 in progress | `committees` + `positions` + `party_control` populated for 2021–2025 via `backend/ingest_leadership.py` |
| 3 | Fill historical-chair gap from Congress.gov bulk data | pending | All 2021–2025 committee chairs and ranking members in `positions` |
| 4 | Build `v_stock_transactions_leadership` + coverage report | pending | Measured count + distribution of trades in leadership windows |
| 5 | Re-run backtest with leadership filter + Wei & Zhou stratifications | pending | New `AnalysisRun` rows on **filing_date** (the actionable anchor): leadership-only event study, party-controls-chamber subset, etc. |
| 6 | Decide on v2.5 generalization | pending | Based on v2 result, decide whether to refactor `members` → `filers` to host judges + cabinet |

## 4. Risks and caveats

* **Small sample.** Even with the leadership cut, our 4-year window is
  thin. A non-result here is consistent with both "there's no edge" and
  "the edge exists but our sample is underpowered." We should size
  expectations accordingly and report confidence intervals, not point
  estimates.
* **Cross-sectional only.** Without the pre/post-ascension panel we can't
  rule out that leaders are simply better stock-pickers ex ante. Wei &
  Zhou's identification depends on the within-member change at the moment
  of ascension; we can't reproduce that.
* **Date anchor changes.** v1 anchored on filing_date (because of the
  disclosure-lag concern). v2 should anchor on **transaction_date** —
  Wei & Zhou's mechanisms are informational and front-run public events,
  not the disclosure itself.
* **Mechanism stratifications need extra data.** Government-contract
  recipients (USAspending.gov), regulatory actions (agency press releases
  / Federal Register), bill sponsorship/support — none of these are in
  our current schema. Add only what we need; don't over-engineer the
  ingest pipeline until v2 shows a signal worth chasing.
* **Concentration in a few traders.** Pelosi, Tuberville, Crenshaw and a
  handful of others dominate the trade-volume distribution. If any of
  them hold a leadership/chair position, they'll drive the result
  mechanically. Report per-member contribution.

## 5. Memory pointers

* `project-v2-leadership-plan` (Claude memory, not in this repo)
  — short-form record of this plan.
* `reference-political-disclosure-sources` (Claude memory, not in this repo)
  — external data source pointers for legislators, judges, executive
  branch.
* `project-modeling-plan` (Claude memory, not in this repo)
  — v1 plan and outcome.

## 6. Citations

* Wei, S.-J. & Zhou, Y. (2025). *"Captain Gains" on Capitol Hill*. NBER
  Working Paper. SSRN 5837992.
* VoxEU column: <https://cepr.org/voxeu/columns/political-power-and-profitable-trades-us-congress>
