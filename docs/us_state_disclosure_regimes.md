# US state-level parliamentarian disclosure regimes

**Date compiled:** 2026-05-23
**Purpose:** Determine whether any US state publishes **per-trade
financial events with values via bulk data** for state legislators or
statewide elected officials, so the existing event-study engine
(`backend/app/services/backtest.py`) can be applied unchanged to
state-level data.
**Method:** WebSearch / WebFetch survey, anchored on comparative
state-ethics rankings (Coalition for Integrity SWAMP Index 2020, Center
for Public Integrity State Integrity Investigation 2015) plus
state-specific deep-dives on the top SWAMP performers and the largest
state legislatures.
**Companion doc:** [`docs/international_disclosure_regimes.md`](international_disclosure_regimes.md)
— same exercise for non-US jurisdictions.

---

## 1. Executive summary

**No US state has a STOCK Act equivalent.** The federal STOCK Act of
2012 — which requires Members of Congress and senior staff to file per-
transaction Periodic Transaction Reports within 30–45 days, with
bracketed values — has zero state-level analog. Every recent
legislative effort to ban or constrain congressional stock trading
(S.2463, S.2773, H.R.1679 in the 118th Congress) is federal, none of
which have passed and none of which have prompted state mimicking.

State disclosure regimes are **uniformly annual snapshots** of holdings
above a value threshold, filed on a fixed calendar (typically Jan 31 –
May 1 of the following year). The dominant pattern is:

- **Annual Statement of Economic Interests / Personal Financial
  Statement** (Form 700 in CA, PFS in TX, Form 6 in FL, Form 7E in NY,
  Statement of Economic Interests in IL, Statement of Financial
  Interests in MA / WA, etc.)
- Threshold-based disclosure (typically $2k–$10k minimum value)
- Categorical reporting in some states (just the company name and a
  bracket), exact-value reporting in others
- Public access varies wildly — from online searchable databases
  (NY COELIG, FL Form 6 portal) to PDF-on-request-only (TX, until the
  Texas Tribune began aggregating)

**No state publishes a machine-readable bulk data feed of legislator
disclosures.** A few third-party aggregators (Texas Tribune, OpenSecrets
state arm, FollowTheMoney.org for related-but-different campaign data)
have built scraping pipelines, but no state ethics commission exposes a
CSV/JSON/XML bulk endpoint analogous to the federal House Clerk
financial-pdfs ZIPs.

**California adds a unique limitation worth flagging:** Form 700 is
*jurisdiction-scoped*. A CA legislator is only required to disclose
investments in companies "located in, doing business in, or planning to
do business in" California. Out-of-state holdings are invisible. This
makes Form 700 the worst possible shape for our use case despite
California's reputation for transparency — even if it were event-driven
(it isn't), most of the analyzable trade universe would be cut off.

**Implication for our project:** state-level data has the same
fundamental shape problem as international — annual holdings snapshots
without per-transaction events — but adds two specific costs not
present in international:
1. **50× the engineering surface.** Each state has its own commission,
   form, online portal (or absence thereof), and quirks. Building 50
   ingest paths to get ~7,400 legislators × annual snapshots is far
   worse leverage than one engineering investment for, say, Brazil's
   TSE (which has a unified bulk feed) covering all federal candidates.
2. **No structural gain in selection.** State legislators are subject
   to *weaker* disclosure than members of Congress (no STOCK Act
   applies), so the population is plausibly more interesting — but
   without per-trade data, that hypothesized signal can't be measured.

---

## 2. Methodology criteria

Same axes as the international report:

| Axis | What "yes" means | Why it matters |
|---|---|---|
| **Event-driven?** | Each addition/removal is individually dated within days of the trade | Without per-event timestamps, the event study has no `transaction_date` |
| **Bulk data?** | Machine-readable structured feed (CSV / JSON / XML / Parquet) from official sources | Without it the engineering cost is dominated by PDF parsing — per state |
| **Values disclosed?** | Either exact amounts or bracketed ranges per holding | Without amount data the rules-backtest can't weight positions |
| **Universe size** | Sufficient legislators + multi-year history | Most state legislatures are 100–200 members; full national universe is ~7,400 across all 50 |
| **Public access** | Online publication (vs. records-request-only) | Determines if even a one-off pull is feasible without state-by-state legal effort |

Federal PTRs score 4/4 on the first four. State-level scores against
all five are below.

---

## 3. The state-level baseline regime (universal across states)

Every state I surveyed has *some* form of legislator financial
disclosure. The variation is in:

1. **Coverage** — most disclose income, real estate, securities,
   business interests; some omit securities entirely or roll them into
   "investments" without specifics.
2. **Granularity** — value brackets, exact values, or
   "have/don't have."
3. **Frequency** — universally annual, with no state requiring
   change-triggered or per-transaction updates.
4. **Format** — paper, online form, PDF, or (rarely) structured
   filing system.
5. **Public access** — searchable database, PDF on website, or
   records request only.

The Coalition for Integrity's **2020 SWAMP Index** ("States With
Anti-Corruption Measures for Public Officials") graded all 50 states +
DC on the broader ethics-and-transparency picture. Headline:
- **Top 4 (score 100/100):** Colorado, Florida, Minnesota, Rhode Island
- **Top 20th percentile (11 states):** the above + Massachusetts, West
  Virginia, California, Delaware, Kansas, Nevada, Texas

These scores are NOT a direct proxy for "good event-shape disclosure"
— they cover gifts, campaign finance, ethics enforcement, and lobbying
disclosure too. But they're the best comparative starting point.

The Center for Public Integrity's **2015 State Integrity
Investigation** (last full update before the project ended) was a
data-driven assessment across 330 indicators in 14 categories
including legislative accountability and ethics enforcement. Headline:
- **Only 3 states scored above D+** — none above C
- **11 states received an F**
- **Only 2 states do comprehensive annual independent audits** of
  lawmakers' asset disclosures

The takeaway across both surveys: even the "transparency leaders" are
working from annual-snapshot foundations. There's no event-driven
substrate to upgrade.

---

## 4. Spotlight: the most-cited state regimes

### 4.1 California — Form 700 (Statement of Economic Interests)

**Filer:** all state and local elected officials + candidates +
designated employees (city managers, department heads, etc.). The
state legislature population is 120 (80 Assembly + 40 Senate).

**Filed with:** Fair Political Practices Commission (FPPC).

**Schedule A-1:** investments held with <10% ownership in any single
business entity.
**Schedule A-2:** business entities with ≥10% ownership.

**Critical limitation — jurisdiction scope:** investments only need to
be disclosed if the company is "located in, doing business in, or
planning to do business in" the agency's jurisdiction. A CA assembly
member doesn't have to disclose Apple stock (HQ in CA, ✓) but
*technically* doesn't have to disclose a stock in a hypothetical
business with no California nexus.

**Threshold:** investments worth $2,000+.
**Value disclosure:** brackets ($2,000–$10,000, $10,001–$100,000,
$100,001–$1,000,000, $1,000,001+).
**Frequency:** annual, due April 1.
**Bulk data:** **none.** Form 700 PDFs are filed with the FPPC and
some are searchable via the FPPC's online portal, but no machine-
readable bulk feed exists.
**Verdict: 1/4** — value disclosure (brackets) only.

### 4.2 Texas — Personal Financial Statement (PFS)

**Filer:** state legislators, statewide elected officers, appointed
officers, executive heads of state agencies, members of various boards.
Legislature population: 181 (150 House + 31 Senate).

**Filed with:** Texas Ethics Commission.

**Coverage:** Chapter 572 of the Government Code. Includes sources of
income, ownership of stocks (no value), real estate, business
interests, gifts.

**Critical limitation — value omission:** "current laws don't require
the state to post Personal Financial Statements of Texas legislators or
candidates online, which forces constituents and the media to go
through a bureaucratic process just to see if there are any conflicts."
Stock ownership is disclosed by company name, but **no value/amount**
is required.

**Bulk data:** none. The Texas Ethics Commission only provides copies
on request. The Texas Tribune scrapes and re-publishes statewide
officials' PFS as a public service, but coverage is incomplete.
**Verdict: 0/4** — annual + no values + no bulk feed + records-request only.

### 4.3 New York — Annual Statement of Financial Disclosure (Form 7E)

**Filer:** members of the Legislature whose salary exceeds CSEA SG-24
(~$115k currently), or who are designated as policy makers.
Legislature population: 213 (150 Assembly + 63 Senate).

**Filing path:** Members file first with the Legislative Ethics
Commission (LEC), which forwards filings to the Commission on Ethics
and Lobbying in Government (COELIG, formerly JCOPE) by June 30 for
public posting.

**Coverage:** broad — income, holdings, real estate, debts, securities,
business interests, gifts. Value brackets disclosed.

**Public access:** statements available online at the COELIG website
(ethics.ny.gov/financial-disclosure-statements-elected-officials).
Searchable but not in bulk machine-readable format.

**Verdict: 1/4** — best publicly-accessible state regime, but still
annual snapshots and PDF-shaped data.

### 4.4 Florida — Form 6 (Full and Public Disclosure of Financial Interests)

**Filer:** state elected officials, including legislators, and senior
appointees. Legislature population: 160 (120 House + 40 Senate). Also
**local officials** (mayors, county commissioners) covered in 2023
when SB 774 expanded Form 6 to local elected officials — followed by
significant pushback and resignations from local offices because of
the disclosure burden.

**Filed with:** Florida Commission on Ethics.

**Coverage:** net worth, assets >$1,000, liabilities >$1,000, income
sources >$1,000, secondary income sources. **Exact dollar amounts
required** — Florida is one of the few states to require exact
values rather than brackets.

**Public access:** statements are public records and available via the
Florida Commission on Ethics; some materials searchable via the
official portal.

**Verdict: 2/4** — exact values disclosed, statewide universe, but
still annual snapshots and no bulk feed.

### 4.5 Minnesota — Statement of Economic Interest

**Filer:** state officers, candidates, local officials in certain
positions. Legislature population: 201 (134 House + 67 Senate).

**Filed with:** Campaign Finance and Public Disclosure Board.

**Coverage:** sources of income, securities holdings, real estate,
loans, fiduciary positions. Threshold-based.

**Public access:** filings publicly available; the Board does post
some materials online.

**Verdict: 1/4** — annual snapshot, no bulk feed, mid-tier value
disclosure.

### 4.6 Washington — F-1 Personal Financial Affairs Statement

**Filer:** elected officials and candidates; legislators must file.
Legislature population: 147 (98 House + 49 Senate).

**Filed with:** Public Disclosure Commission (PDC).

**Coverage:** assets, debts, income sources, business interests. Value
brackets disclosed.

**Notable strength:** WA has a strong open-data culture and the PDC
publishes F-1 filings online. The PDC also operates a public-facing
data API for campaign finance — but I did not find one for F-1
filings specifically.

**Verdict: 1/4** — annual snapshot but better online access than
most.

### 4.7 Illinois — Statement of Economic Interests

**Filer:** state officers, candidates, lobbyists.
Legislature population: 177 (118 House + 59 Senate).

**Filed with:** Secretary of State.

**Coverage:** sources of income, business interests, securities holdings
above a threshold. Categorical rather than valued.

**Verdict: 0/4** — annual, weak value disclosure, no bulk feed.

### 4.8 Massachusetts — Statement of Financial Interests (SFI)

**Filer:** state officials including legislators.
Legislature population: 200 (160 House + 40 Senate).

**Filed with:** State Ethics Commission.

**Coverage:** broad including securities, real estate, debts, gifts,
positions. Bracketed values.

**Verdict: 1/4** — annual snapshot.

---

## 5. Special-population spotlight

### 5.1 State Treasurers

The 50 state treasurers + analogous positions (CFO in FL, Comptroller
in NY/CT/IL, etc.) are an analytically interesting population because
they often *directly* manage state public-pension fund allocations and
have visibility into upcoming public-sector financial decisions.

**However:** their personal disclosures are filed under the same state
ethics regime as legislators — annual snapshots, no per-trade events.
**No state treasurer is subject to per-transaction reporting.**

The much-more-interesting datasets *related to* state treasurers are
the state pension fund holdings themselves, which are publicly disclosed
quarterly (13F-style). But those are *fund* holdings managed by
investment staff, not personal trades by the treasurer.

### 5.2 State Attorneys General

State AGs prosecute securities-fraud cases, sign on to multi-state
litigation against publicly traded companies (Big Tech antitrust,
opioid settlements, tobacco MSA, banking fraud), and increasingly bring
ESG-related cases. Conflict surface is high.

**Same disclosure shape** as legislators in each state. Annual,
threshold-based, no per-trade. No state AG-specific enhanced disclosure
regime found.

### 5.3 Governors

50 governors + 5 territorial governors. Same shape — annual disclosure
under each state's ethics law, sometimes with enhanced thresholds for
statewide elected officials. **No per-trade reporting.**

### 5.4 State Supreme Court Justices

The Center for Public Integrity's 2014 methodology piece on state
supreme court disclosure found that judicial disclosure varies widely
by state — some states require none beyond what's required of lawyers
in private practice. Universe is ~350 justices nationally. Annual at
best.

### 5.5 State Public Utility / Insurance / Banking Commissioners

Per-position disclosure under each state's general ethics law. Smaller
universes (often <10 per state) and very specific policy authority
(rate cases, mergers, etc.) make them interesting but data is the same
annual shape.

---

## 6. Master comparison table

Fit score = sum of (Event × 2 + Bulk + Values + Public access) where
each axis is 0–2:

| Jurisdiction | Event | Bulk | Values | Access | Score | Verdict |
|---|---|---|---|---|---|---|
| **US Congress** (baseline) | 2 (per-trade) | 2 | 2 | 2 (online portals) | **8/8** | Reference shape |
| **Florida (Form 6)** | 0 (annual) | 0 (no API) | 2 (exact) | 1 (request) | 3/8 | Best state on values |
| **New York (Form 7E)** | 0 | 0 | 1 (brackets) | 2 (online) | 3/8 | Best state on online access |
| **California (Form 700)** | 0 | 0 | 1 (brackets) | 1 (FPPC portal) | 2/8 | Crippled by jurisdiction-scoping |
| **Washington (F-1)** | 0 | 0 | 1 (brackets) | 1 (PDC online) | 2/8 | Modest |
| **Massachusetts (SFI)** | 0 | 0 | 1 (brackets) | 1 | 2/8 | Standard pattern |
| **Minnesota** | 0 | 0 | 1 (brackets) | 1 | 2/8 | Top SWAMP scorer, standard shape |
| **Colorado** | 0 | 0 | 1 | 1 | 2/8 | Top SWAMP scorer, standard shape |
| **Rhode Island** | 0 | 0 | 1 | 1 | 2/8 | Top SWAMP scorer, standard shape |
| **Illinois** | 0 | 0 | 0 (categorical) | 1 | 1/8 | Weak value disclosure |
| **Texas (PFS)** | 0 | 0 | 0 (no values) | 0 (records request) | 0/8 | Worst-fit large state |
| **Median state** | 0 | 0 | 1 | 1 | 2/8 | Annual, brackets, online-or-portal |

**Nothing scores above 3/8.** Every state is on annual-snapshot
foundation. Florida is best on value precision; NY is best on online
access; nobody publishes a bulk feed.

---

## 7. What the apparent counterexamples actually fail on

A few states look better than they are at first glance:

- **California Form 700.** Strong transparency reputation + FPPC online
  portal could suggest engineering tractability. But jurisdiction-
  scoping silently truncates the dataset — out-of-CA-business holdings
  are invisible by statute, so even a complete ingest gives us only
  a subset of legislators' actual portfolios.
- **Florida Form 6.** Exact values is genuinely better than brackets
  (would let us drop the `amount_min`/`amount_max` approximation we
  use for federal data). But still annual; the 2023 SB 774 expansion
  to local officials actually *caused* a wave of resignations because
  the regime is burdensome — suggesting the population's willingness to
  disclose is brittle, not stable.
- **New York COELIG online portal.** Best online searchability we've
  found, but the underlying data is annual filings, and the portal
  serves PDFs/HTML rendering rather than a structured API.
- **Washington PDC.** Strong open-data culture for campaign finance,
  but I didn't find an F-1-specific bulk feed despite confirming the
  PDC has an API surface for related campaign data.

The common trap across both this report and the international one:
"transparency leader" jurisdictions are leaders in *governance*
(ethics enforcement, complaint handling, gift rules) — not necessarily
in *event-driven data publication*. SWAMP scores and the State Integrity
Investigation grade the wrong thing for our specific need.

---

## 8. The congressional-staff alternative — investigated, ruled out (2026-05-23)

**Initial premise (incorrect).** While surveying state-level data I
noticed that the federal STOCK Act covers not just Members of Congress
but also senior congressional staff (~2,000 individuals earning ≥120%
of GS-15 base). The original 2012 STOCK Act required staff PTRs to be
filed through the same House Clerk + Senate eFD disclosure systems we
already scrape. I initially recommended this as a high-leverage
internal expansion: ~5× dataset, same engine, no methodology change.

**Correction.** That recommendation was based on stale understanding
of the law. **S.716 (April 2013)** — passed by unanimous consent in
the House after 14 seconds of debate, signed into law April 15, 2013 —
explicitly walked back the online-publication requirement for staff:

> "S.716 modified the online disclosure portion of the STOCK Act so
> that congressional staff and certain executive branch officers and
> employees were no longer required to publicly report financial
> disclosure statements online... The amendment eliminated the
> requirement for the creation of a searchable, sortable database of
> information in reports, and the requirement that reports be done in
> electronic format, rather than on paper."

The amendment did NOT touch members, candidates, the President, or
VP — they still file publicly online.

**Empirical confirmation (2026-05-23):**
- 2024 House FD index ZIP: 451 PTR rows; **all** carry district codes
  (i.e., all are members). Zero PTRs from non-members.
- House Clerk's search page (`disclosures-clerk.house.gov/FinancialDisclosure/ViewSearch`)
  exposes only `ViewMemberSearchResult` and `ViewCandidateSearchResult`
  form actions. No staff/officer/employee option.
- Senate eFD search page similarly has no staff filer-type option.

**Conclusion.** Staff PTRs still exist (the underlying filing
requirement wasn't removed), but they are available only at the
Legislative Resource Center (House) and Senate Library (Senate) on
paper or via per-document request. **Bulk ingest is not feasible.**
This is exactly the access pattern the S.716 amendment intended to
restore.

The 5× dataset expansion claim doesn't survive contact with the actual
data infrastructure. See `docs/decision_log.md` §6.2 for the corrected
roadmap entry.

**What was true and what wasn't:**
- ✓ STOCK Act DOES require senior staff to file PTRs
- ✓ The 2012 law DID provide for a searchable database
- ✗ The 2012 provision was rolled back in 2013
- ✗ Staff PTRs are NOT bulk-accessible online today

---

## 9. Implications for our analysis engine

The engine's contract today is
`(member, ticker, transaction_date, filing_date, transaction_type, amount, owner) → forward return`.

State legislators score **2/8 (median)** on the methodology fit axes —
worse than UK Commons (3/8) on our score system because state data is
filed PDF-style without even mySociety-equivalent aggregation, and worse
than France HATVP (5/8) because there's no Etalab-equivalent open data
portal.

Real options after correcting the staff finding (see §8):

| Option | Cost | Signal value |
|---|---|---|
| **(a) Forced fit of state annual snapshots.** Build 50 PDF parsers and treat each annual filing as a fake "transaction" on the filing date | Very high (50 jurisdictions) | Low — same lossy semantics as international, plus jurisdiction-scoping issues like CA Form 700 |
| **(b) Holdings-replication framework.** Build a parallel analysis track that ingests annual snapshots and computes time-weighted portfolio returns | High (~4-6 weeks setup, then per-jurisdiction marginal) | Genuine cross-jurisdictional comparison on the correct question for *that* data shape |
| ~~**(c) Congressional senior staff PTRs.**~~ | ~~Low~~ | **Not feasible.** S.716 (2013) removed online publication of staff filings; bulk access doesn't exist. See §8. |
| **(d) Policy-event windows on existing data.** Stratify existing US member trades by proximity to dated policy events | Low | Tests information-channel hypothesis with tighter temporal prior; uses everything we have |
| **(e) OGE Form 278-T (cabinet / executive branch).** ~25 cabinet + ~50 sub-cabinet officials file 278-T (same shape as Congressional PTR). Accessible via OGE Form 201 request — paper-based, weeks-of-turnaround, but feasible at small universe size | Medium (records-request workflow, weeks of latency, then PDF parsing reuses existing pipeline) | Small but PTR-shape and engine-compatible; constrained by 2025-01-03 trifecta cohort confound |

**Updated recommendation: (d) policy-event windows + (e) OGE 278-T.**

(d) remains the lowest-cost path and was always the right "uses what
we have" option. It stays on the roadmap (see `docs/decision_log.md`
§6.1).

(e) is the smallest viable new-data option now that staff is ruled out.
Executive-branch 278-T filings are publicly available but only via
request (not online bulk), so the engineering shape is: file Form 201
with OGE, wait weeks for PDFs, parse with our existing `ptr_parser.py`
+ `ocr.py` pipeline. Universe is ~75 officials. Constrained by the
same cohort confound v2 had: all cabinet officials turned over at
2025-01-03 trifecta, so leadership/regime variables are collinear.

---

## 10. Caveats and gaps in this report

- **Search-based research.** Findings rely on WebSearch summaries plus
  state-ethics-commission page references. State-by-state confidence
  varies; high for CA, TX, NY, FL, IL, WA, MA (where I have direct
  form references); medium for CO, MN, RI, KS, NV, DE, WV (top SWAMP
  scorers but not deeply inspected); low for the remaining 35 states
  where I'm relying on the SWAMP and State Integrity Investigation
  aggregate scoring.
- **NCSL pages were unreachable** (HTTP 403 to WebFetch). The NCSL
  Elected Officials Fiscal Disclosure Series is the natural
  comparative source and would be the next place to ground-truth the
  table in §6 if access becomes possible.
- **State pension fund holdings** are not covered here. They're
  publicly disclosed (quarterly 13F-style) but are *fund-level*
  manager activity, not personal trades by the treasurer or board.
  Worth a separate scoping exercise if we ever want to look at
  pension-fund-as-actor rather than politician-as-actor.
- **Local officials** (city councils, county commissioners, mayors)
  are out of scope. Florida's 2023 expansion of Form 6 to local
  officials is the only state-level move I'm aware of that affected
  the local-official universe; most cities have ad-hoc ethics codes,
  none with PTR-shape filings.
- **Recent state-legislative momentum on stock trading.** Several
  states have introduced (not passed) bills modeled on the federal
  STOCK Act. The picture could change in 2026+, but as of this
  survey nothing's enacted.

---

## 11. Sources

**Comparative**
- [Coalition for Integrity — SWAMP Index 2020](https://www.coalitionforintegrity.org/swamp2020/)
- [Center for Public Integrity — State Integrity Investigation (2015)](https://publicintegrity.org/politics/state-politics/state-integrity-investigation/)
- [Center for Public Integrity — Grading the Nation](https://publicintegrity.org/politics/state-politics/grading-the-nation-how-accountable-is-your-state/)
- [NCSL — Elected Officials Fiscal Disclosure Series](https://www.ncsl.org/ethics/fiscal-disclosure-series) (unreachable from this session; landing page only)
- [NCSL — Summary Financial Disclosures: Income](https://www.ncsl.org/ethics/financial-disclosures-income)

**Per-state primary**
- California: [FPPC Form 700](https://www.fppc.ca.gov/form-700/); [Schedule A-1/A-2 instructions](https://fppcada.fppc.ca.gov/form700/form700_2024-2025/04-schedule-a1a2-instructions.html)
- Texas: [Texas Ethics Commission PFS filing info](https://www.ethics.state.tx.us/filinginfo/pfs/); [Texas Government Code Chapter 572](https://statutes.capitol.texas.gov/Docs/GV/pdf/GV.572.pdf); [Texas Tribune PFS data app](https://www.texastribune.org/2010/07/13/search-texas-officials-financial-disclosures/); [Reform Austin PFS aggregator](https://www.reformaustin.org/campaign-finance/find-texas-legislators-and-candidates-personal-financial-statements-here/)
- New York: [COELIG Financial Disclosure](https://ethics.ny.gov/financial-disclosure); [COELIG Public Data](https://ethics.ny.gov/public-data); [LEC Guide to Annual Statement](https://legethics.ny.gov/sites/default/files/2022-03/Guide%20to%20the%20Annual%20Statement%20of%20Financial%20Disclosure_CY%202021.pdf)
- Florida: Florida Commission on Ethics (Form 6 is the canonical filing; SB 774 2023 expanded to local)
- Federal (referenced): [House Clerk Financial Disclosure database](https://disclosures-clerk.house.gov/FinancialDisclosure); [Senate Public Disclosure](https://www.disclosure.senate.gov/); [USOGE Annual Public Financial Disclosure System](https://www.oge.gov/web/oge.nsf/Resources/The+Annual+Public+Financial+Disclosure+System:+The+Ongoing+Work+of+Preventing+Conflicts+of+Interest)

**Federal STOCK Act context (for the recommended congressional-staff path)**
- [STOCK Act — Wikipedia](https://en.wikipedia.org/wiki/STOCK_Act)
- [Campaign Legal Center — Congressional Stock Trading and the STOCK Act](https://campaignlegal.org/update/congressional-stock-trading-and-stock-act)
- [Library of Congress CRS — Taking Stock of the STOCK Act](https://www.congress.gov/crs-product/TE10119)
- [CRS R47320 — Financial Disclosure in the U.S. Government FAQs](https://www.congress.gov/crs-product/R47320)

---

## 12. What this report does not answer

- **Empirical analyzable-row count per state.** Without ingesting any
  state's PDFs, I can't say what fraction of legislators' filings would
  yield publicly-traded equity rows post-parsing. The methodology fit
  is so weak that this exercise wasn't worth the effort.
- **Whether congressional staff PTRs are worth running unconditionally
  or only conditional on a v1/v2/v2.5 follow-up question.** Decided
  upstream in the project direction discussion.
- **State pension fund trustees / investment board personal trades.**
  Tangentially related; would require a separate scoping pass.
- **Localized US populations** (governors-of-states-with-broad-trade-
  policy-authority, treasurers-managing-largest-pension-funds, AGs-
  doing-most-securities-litigation). Plausibly interesting subgroups,
  but all share the same annual-snapshot disclosure shape, so the
  fit problem is identical.
