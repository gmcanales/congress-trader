# International parliamentarian financial disclosure regimes

**Date compiled:** 2026-05-23
**Purpose:** Determine whether any jurisdiction outside the US publishes
**per-trade financial events with values via bulk data**, so the existing
event-study engine (`backend/app/services/backtest.py`) can be applied
unchanged to non-US data.
**Method:** WebSearch / WebFetch survey across ~25 jurisdictions, with
preference for already-comparative sources (World Bank Financial
Disclosure Law Library, OECD asset-declaration reports, Transparency
International knowledge hub). Country-specific deep-dives where the
comparative source was thin.

---

## 1. Executive summary

**The US STOCK Act (2012) regime is genuinely an outlier.** No other
major democracy publishes per-trade events with monetary values via a
bulk feed. The dominant pattern internationally is one of:

- **Annual holdings declarations** (UK Commons, EU MEP, France HATVP,
  Germany Bundestag, Japan, Korea, India, South Africa, Israel,
  Hungary, etc.), or
- **Pre-election asset affidavits** (Brazil TSE, India Election
  Commission), or
- **Confidential filings to an oversight body** (Israel State Comptroller).

Even where the data is public and well-structured (France HATVP XML
bulk, Brazil DivulgaCandContas, India MyNeta), it captures holdings or
declarations at a moment in time, not the individual transactions
between those moments. The US's "every transaction >$1,000 must be
reported within 45 days" rule is, as best I can tell, unique among OECD
democracies.

**Implication for our project:** the existing event-study + rules-NAV
engine — which expects `(member, ticker, transaction_date,
filing_date, transaction_type, amount, owner)` tuples — cannot be
straightforwardly applied to any other jurisdiction without either
(a) lossy mapping (treat each annual declaration as a fake "transaction"
on the registration date), or (b) a parallel methodology
(holdings-based portfolio replication). See §6 for the engineering
implications.

---

## 2. Methodology criteria

For each jurisdiction we score on four axes that determine whether the
existing engine can plug in:

| Axis | What "yes" means | Why it matters |
|---|---|---|
| **Event-driven?** | Each addition/removal of a holding is individually dated within days of the trade | Without per-event timestamps, the event study has no `transaction_date` to anchor forward returns on |
| **Bulk data?** | Machine-readable structured feed (CSV / JSON / XML / Parquet) | Without it we burn engineering on PDF parsing — a fixed cost per jurisdiction |
| **Values disclosed?** | Either exact amounts or bracketed ranges per holding | Without amount data the rules-backtest can't weight positions; we lose the `amount_min` / `amount_max` semantics |
| **Universe size** | At least a few hundred declarants, multi-year history | Below ~100 declarants × few years, the analysis is underpowered |

US PTRs hit all four. The question for each other jurisdiction is how
many of the four it hits.

---

## 3. Findings by region

### 3.1 Americas

| Country | Event-driven? | Bulk data? | Values? | Universe | Source |
|---|---|---|---|---|---|
| **United States** (baseline) | **Yes** (per-trade, ≤45-day reporting) | Yes (House ZIPs + Senate eFD) | Brackets ($1,001–$15,000, etc.) | 535 members, 2012– | STOCK Act / EthicsinGovernment |
| **Brazil** | **No** — declaration *only at candidacy*, every 2 / 4 years | Yes — DivulgaCandContas (TSE) | Yes (BRL exact) | All federal candidates per cycle | TSE [DivulgaCandContas](https://divulgacandcontas.tse.jus.br/) |
| **Mexico** | No — annual + appointment/termination | Limited (federal SDF; states vary) | Mixed | Two-tier (top officials + analogues) | Secretaría de la Función Pública |
| **Argentina** | No — annual | Limited | Yes | All elected + senior appointees | Anti-Corruption Office (MJyDH) |
| **Chile** | No — annual; family included | Limited | Yes | All elected + senior officials | Ley 20.880 (2016) |
| **Canada** | Change-triggered (within reasonable time) | Searchable registry, no obvious bulk | **No** — source and nature only, *not value* | ~338 MPs | [prciec-rpccie.parl.gc.ca](https://prciec-rpccie.parl.gc.ca/) |

**Brazil is the most data-rich.** TSE's DivulgaCandContas publishes every
candidate's full patrimônio (asset declaration) with valued line items,
no login, LGPD-exempt for elected officials. **But it only fires at
election cycles** — for federal deputies that's every 4 years, for
senators 8 years. Not per-trade.

**Canada is closest in shape to a US PTR feed** (change-triggered, public
registry) but explicitly omits values per statute, which is fatal for our
engine.

### 3.2 Europe — Western

| Country | Event-driven? | Bulk data? | Values? | Universe | Source |
|---|---|---|---|---|---|
| **UK Commons** | No — annual + change updates | **Yes** — mySociety parquet feed | Threshold buckets (≥15% / >£70k) | ~110 MPs with shareholdings | [pages.mysociety.org/parl_register_interests](https://pages.mysociety.org/parl_register_interests/) |
| **France (HATVP)** | No — annual + modificative declarations | **Yes** — XML bulk on data.gouv.fr (CC-BY/Etalab) | Yes (likely; sample empty) | ~18k officials (national + local) | [hatvp.fr/open-data](https://www.hatvp.fr/open-data/) |
| **Germany Bundestag** | No | Per-MP pages on bundestag.de only | Threshold only (25%+ voting rights) | ~736 MPs | Bundestag transparency rules |
| **EU Parliament** | No — annual | Open Data Portal API | Likely yes; 21% imprecise per TI EU | 705 MEPs | [data.europarl.europa.eu](https://data.europarl.europa.eu/) |
| **Italy / Spain / Portugal / Netherlands / Belgium** | Annual (mostly) | Mixed (mostly PDF) | Varies | Per-parliament |  Per-country |
| **Nordics (NO/SE/FI/DK/IS)** | Annual | Mixed; usually online HTML | Varies | Small parliaments | Per-country |
| **Ireland** | Annual | PDF on oireachtas.ie | Limited | 160 TDs + 60 Senators | SIPO (Standards in Public Office) |

**France HATVP is the most engineering-friendly non-US source** — proper
XML bulk feed under an explicit open license, comprehensive coverage,
13-year history. But the data shape is annual interest declarations +
modificative updates, not per-trade events. The `participationFinanciereDto`
node carries shareholding declarations as items inside each annual filing.

**UK Commons via mySociety** is the second-best engineering shape, but
the £70k / 15% threshold means most retail-scale equity activity goes
undeclared. Public-company holdings within the threshold are a small
subset; private-company stakes dominate.

### 3.3 Europe — Central / Eastern

| Country | Event-driven? | Bulk data? | Values? | Universe | Source |
|---|---|---|---|---|---|
| **Ukraine** | Annual + significant-change | **Yes** — NACP e-declarations (largest e-declaration DB in the world) | **Yes** (exact values, family included) | ~80,000 declarants | nazk.gov.ua |
| **Poland** | Annual | Limited (hand-filled in many cases) | Yes | 460 Sejm + 100 Senat | Per-house |
| **Czech Republic** | Annual | Limited | Yes | 200 Chamber + 81 Senate | Central Register |
| **Romania** | Annual | Patchy | Yes | 329 Chamber + 136 Senate | ANI |
| **Hungary** | Annual + family | Limited online | Yes (real estate, movable assets, cash, debts) | 199 MPs + civil service | 2007 Wealth Declaration Law |
| **Slovakia / Bulgaria / Lithuania / Latvia / Estonia / Slovenia** | Annual | Varies; some online portals | Yes | Per-parliament | Per-country |

**Ukraine is structurally the closest to "comprehensive" in this group**
— the NACP e-declaration system is one of the most ambitious in the world,
with exact values and family members. But declarations are annual, not
per-trade, and the wartime political context complicates analytical use.

### 3.4 Asia-Pacific

| Country | Event-driven? | Bulk data? | Values? | Universe | Source |
|---|---|---|---|---|---|
| **Japan** | No — annual | **No** — in-person inspection only at Diet offices | Yes (security value, # shares, company name) | 651 Diet members | 1993 Law on Disclosure of Lawmakers' Assets |
| **South Korea** | No — annual | Partial (gazette + PETI) | Yes (cash, deposits, securities, real estate; member + spouse + ascendants/descendants) | 300 National Assembly | Public Official Ethics Act; [docs.openwatch.kr](https://docs.openwatch.kr/data/national-assembly/asset-disclosure) aggregates |
| **Taiwan** | No — annual | Limited online | Yes | ~113 Legislative Yuan | Ministry of Justice |
| **Singapore** | Confidential to Cabinet | **Not public** | Yes (internal) | Cabinet only | PSC Code |
| **Hong Kong** | Annual (LegCo) | Online register | Limited | ~90 LegCo | LegCo Members' Register |
| **India** | No — declaration within 90 days of taking seat + at election | **Yes** — Election Commission + MyNeta aggregator | Yes (movable, immovable, deposits, securities, debentures, shares; self + spouse + dependent children) | 543 Lok Sabha + 245 Rajya Sabha | [myneta.info](https://www.myneta.info/) |
| **Australia** | No — only *processing* date recorded, not lodgement; statements often backdated | PDFs (House); poor-search DB (Senate, 2023+) | **No** — companies named, values omitted | ~227 federal MPs+Senators | aph.gov.au registers |
| **Philippines / Indonesia / Thailand / Malaysia** | Annual (SALN-style or equivalent) | Limited online | Yes (Philippines SALN values; others partial) | Per-parliament | Per-country |

**India is data-rich and theoretically promising** — MyNeta has aggregated
candidate affidavits and post-seat declarations into structured form, and
NSE/BSE tickers are accessible via yfinance with `.NS` / `.BO` suffixes.
But the declarations are essentially "post-election snapshot of holdings,"
repeated at the next election. Not a per-trade feed. Also: candidate
affidavits famously have under-declaration problems documented by ADR.

**Japan is event-shaped only in the most generous sense.** The 1993 Act
requires Diet members to disclose securities holdings with exact values
and counts, but access is by **in-person request at the chamber office**.
There is no online publication and no bulk feed. Engineering cost to
build a dataset is effectively prohibitive without a partner inside Japan.

**South Korea has the most comprehensive declaration scope in the
region** (member + spouse + lineal ascendants/descendants), and
OpenWatch.kr has begun publishing structured data from the gazette
filings. Annual cadence, not per-trade.

### 3.5 MENA

| Country | Event-driven? | Bulk data? | Values? | Universe | Source |
|---|---|---|---|---|---|
| **Israel** | Confidential to State Comptroller | **Not public** | Yes (internal) | 120 Knesset | Basic Law: State Comptroller |
| **Turkey** | Annual | Confidential | Yes (internal) | 600 TBMM | Public Officials Declarations Law |
| **UAE / Saudi Arabia / Qatar / Kuwait** | None (no public disclosure regime) | — | — | — | — |
| **Lebanon / Jordan / Egypt** | Annual (where exists) | Confidential or limited | Yes (internal) | Per-country | Per-country |
| **Tunisia / Morocco** | Annual | Some online (Tunisia INLUCC; Morocco partial) | Yes | Per-parliament | Per-country |

**The entire region is essentially unavailable for our methodology.**
Most jurisdictions either don't have a disclosure law (Gulf monarchies)
or keep declarations confidential (Israel, Turkey, Egypt, Jordan). Even
where public, none publish bulk machine-readable feeds.

### 3.6 Africa

| Country | Event-driven? | Bulk data? | Values? | Universe | Source |
|---|---|---|---|---|---|
| **South Africa** | No — annual | **PDF only, no search** | Yes (categories: shares, directorships, etc.) | 400 NA + 90 NCOP | [parliament.gov.za](https://www.parliament.gov.za/ethics-and-members-interests); aggregated at [pa.org.za/interests](https://www.pa.org.za/interests/) |
| **Kenya** | Annual | EACC internal | Yes | 290 NA + 67 Senate | Ethics and Anti-Corruption Commission |
| **Nigeria** | Annual | Code of Conduct Bureau (limited public access) | Yes | 360 House + 109 Senate | CCB |
| **Ghana** | Confidential | **Not public** | Yes (internal) | 275 MPs | Audit Service |
| **Botswana / Namibia / Tanzania / Uganda / Rwanda** | Annual or partial | Limited | Varies | Per-parliament | Per-country |

**Best African data is South African,** thanks to civil-society aggregation
at People's Assembly (pa.org.za) and parlimeter.org.za. But the source
register is a PDF; meaningful analysis requires hand-parsing, and the
quality issues noted by parlimeter.org.za (vague descriptions, late
filings) match what we see in UK Commons.

---

## 4. Master comparison table — best candidates ranked

Sorted by methodology fit (highest first). "Fit score" = sum of (Event ×
2 + Bulk + Values) where each axis is 0–2:

| Jurisdiction | Event | Bulk | Values | Universe | Score | Verdict |
|---|---|---|---|---|---|---|
| **US Congress** | 2 (per-trade) | 2 | 2 | 535 | **8/8** | Baseline — only fully-fit regime |
| **France HATVP** | 1 (modificative possible) | 2 (XML/CSV/Etalab) | 1 (likely) | ~18k | 5/8 | Best non-US data infrastructure; wrong shape |
| **Ukraine NACP** | 1 (change-triggered) | 2 (e-declarations) | 2 | ~80k | 5/8 | Best scope; political context limits use |
| **UK Commons** | 0 (snapshot) | 2 (mySociety) | 1 (threshold) | ~110 | 3/8 | Already characterized |
| **Brazil TSE** | 0 (per-election only) | 2 (DivulgaCandContas) | 2 | All candidates | 4/8 | Public + valued + bulk, but pre-election only |
| **India MyNeta** | 0 (snapshot + election) | 1 (aggregated, not official) | 2 | 543+245 | 3/8 | Theoretically promising, under-declaration concerns |
| **South Korea** | 0 (annual) | 1 (PETI partial) | 2 | 300 | 3/8 | Best scope per declarant; not per-trade |
| **South Africa** | 0 (annual) | 0 (PDF only) | 1 (categories) | 400 | 1/8 | Hand-parsing required |
| **Japan** | 0 (annual) | 0 (in-person only) | 2 | 651 | 2/8 | Excellent scope; physically inaccessible |
| **Australia** | 0 (processing-date only) | 0 (PDFs) | 0 (values omitted) | ~227 | 0/8 | Triple miss |
| **Canada** | 1 (change-triggered) | 1 (registry, no bulk) | 0 (values omitted) | ~338 | 3/8 | Disqualified by no-value rule |
| **Israel** | n/a | 0 (not public) | n/a | 120 | 0/8 | Confidential |
| **Germany** | 0 (snapshot, 25% threshold) | 0 (per-MP pages only) | 0 (threshold only) | 736 | 0/8 | Coverage too narrow |

**Nothing scores above 5/8.** The closest analogue to US PTR shape is
**Ukraine's NACP e-declarations** on coverage and structure, but the
event-shape gap (annual, not per-trade) is still load-bearing.

---

## 5. Where the apparent counterexamples actually fail

A few jurisdictions look better than they are at first glance. Worth
naming the trap so future-Claude / future-collaborator doesn't re-walk
the same ground:

- **Brazil TSE.** Looks ideal (public, valued, structured) until you
  realize declarations only fire at election cycles. A federal deputy
  declares at candidacy (every 4 years) — interim trading is invisible.
- **France HATVP.** Looks ideal (open-data XML, comprehensive coverage)
  until you read the schema — `participationFinanciereDto` is a node
  inside each annual *Déclaration d'intérêts*, and the per-shareholding
  rows don't carry transaction dates. The "modificative" updates are
  also annual-shaped.
- **India MyNeta.** Looks ideal (aggregated, public, valued, includes
  securities) until you realize the data is a *post-election snapshot*
  filed within 90 days of taking the seat — repeated at the next election.
- **South Korea PETI.** Looks ideal (comprehensive scope including
  ascendants/descendants) until you realize the gazette publication is
  *annual* and PETI is a query interface, not a bulk feed.
- **Canada Public Registry.** Looks ideal (change-triggered, online,
  searchable) until you read the statute — "source and nature, but not
  the value" is explicit.

The common trap: comparative survey sources (World Bank StAR, OECD,
Transparency International) score countries on *whether they have a
disclosure regime at all*, not on whether the data is event-shaped and
machine-accessible. A "yes" on the WB Financial Disclosure Law Library
just means a law exists.

---

## 6. Implications for our analysis engine

The engine's contract today is
`(member, ticker, transaction_date, filing_date, transaction_type, amount, owner) → forward return`.
Across the ~25 jurisdictions surveyed, **none has the same shape** as
US PTRs. To extend the engine internationally we have three options,
with very different costs:

| Option | What it costs | What we get |
|---|---|---|
| **(a) Forced fit.** Treat each annual / modificative declaration as a fake "transaction" with `transaction_date` = registration date | Cheap (~1 week per jurisdiction) | Lossy semantics; the "edge" measured is "do disclosed-at-registration holdings drift up after?" which is not the v1/v2/v2.5 question |
| **(b) Parallel holdings-replication engine.** Build a second analysis track that ingests holdings snapshots and computes time-weighted portfolio returns vs benchmark | Significant (~4-6 weeks for first jurisdiction, faster after) | Genuine cross-jurisdictional comparison on the *correct* question for that regime ("does disclosed politician wealth outperform"). Different metric, defensible methodology. |
| **(c) Stick with US-only and pivot the question.** Promote the policy-event-window approach that's already on the roadmap (§5 of decision_log) — stratify existing US data by trades around specific events | Cheap (~1 week, reuses everything) | A fresh, theoretically-motivated test we haven't run, using the engine we have, on the data we have |

**Recommendation: (c) is the highest ratio of new signal to engineering
cost.** International data is uniformly the wrong shape; we'd be paying
substantial integration cost for marginal information value. The
policy-event-window approach is the natural next test of the same
information-channel hypothesis on the data we already trust.

(b) becomes worth doing only if (c) lands a result strong enough to
justify cross-jurisdictional validation as a follow-up.

---

## 7. Caveats and gaps in this report

- **Search-based research, not primary documents.** Findings rely on
  WebSearch summaries plus a handful of direct page fetches. Country
  profiles vary in confidence; high-confidence are US, UK, France,
  Australia, Brazil, India, South Africa, Japan, Korea (where I have
  direct schema or law-text inspection). Lower confidence on the
  Eastern European and African coverage.
- **Translation gaps.** I haven't read any non-English / non-French
  primary law text. A bilingual local source for any flagged jurisdiction
  could overturn my fit assessment.
- **The World Bank Financial Disclosure Law Library** (176 jurisdictions,
  1,000+ laws) is the obvious place to ground-truth this report. I
  attempted to fetch it directly during this survey but the host
  refused the connection at the time of writing. If/when accessible,
  cross-checking the rankings here against the WB axes ("information
  disclosed: stocks/securities", "public availability", "value
  disclosed") would either confirm the picture or surface specific
  jurisdictions worth a second look.
- **Quasi-events I haven't fully explored.** France's "modificative"
  declarations, Canada's change-triggered updates, and Australia's 35-day
  rule are all *quasi-event-driven* but with caveats. A deeper dive
  into any one of them could reveal whether the change-event subset is
  rich enough to be analytically useful even though the annual snapshot
  isn't.

---

## 8. Sources

**Comparative / cross-country databases**
- [World Bank Financial Disclosure Law Library](https://publicofficialsfinancialdisclosure.worldbank.org/) — 176 jurisdictions, 1,000+ laws, search by axis.
- [OECD: Asset Declarations for Public Officials (2011)](https://www.oecd.org/content/dam/oecd/en/publications/reports/2011/03/asset-declarations-for-public-officials_g1g11b78/9789264095281-en.pdf) — focused on Eastern Europe / Central Asia.
- [Transparency International: Income and Asset Disclosure Topic Guide](https://knowledgehub.transparency.org/assets/uploads/kproducts/Topic_Guide_Income_and_Asset_Disclosure.pdf)
- [StAR Initiative: Public Office, Private Interests](https://star.worldbank.org/sites/star/files/Public%20Office%20Private%20Interests.pdf) — methodological framework.
- [Transparency International: Holding politicians to account](https://www.transparency.org/en/news/holding-politicians-to-account-asset-declarations)
- [UNODC: Income and Asset Declarations — Tools and Trade-offs](https://www.unodc.org/documents/corruption/Publications/StAR/StAR_Publication_-_Income_and_Asset_Declarations.pdf)

**Per-jurisdiction primary or aggregator**
- US: STOCK Act 2012; House Clerk ZIPs; efdsearch.senate.gov
- UK Commons: [mySociety parl_register_interests](https://pages.mysociety.org/parl_register_interests/) (CC-BY 4.0)
- France: [HATVP open data](https://www.hatvp.fr/open-data/) (Etalab open license)
- EU: [European Parliament Open Data Portal](https://data.europarl.europa.eu/)
- Germany: [Library of Congress: Germany Transparency Rules (2021 tightening)](https://www.loc.gov/item/global-legal-monitor/2021-10-21/germany-transparency-rules-for-members-of-parliament-tightened/)
- Brazil: [TSE DivulgaCandContas](https://divulgacandcontas.tse.jus.br/)
- India: [MyNeta / ADR](https://www.myneta.info/)
- South Africa: [Parliament Ethics and Members' Interests](https://www.parliament.gov.za/ethics-and-members-interests); [People's Assembly aggregator](https://www.pa.org.za/interests/); [parlimeter.org.za critique](https://www.parlimeter.org.za/post/members-interests)
- Japan: [Japanese Law Translation — Act on Disclosure of Diet Members' Assets](https://www.japaneselawtranslation.go.jp/en/laws/view/1228); [G20 Japan AD Country Profile](https://g20.org.tr/wp-content/uploads/2015/11/Japan-AD-Country-Profile.pdf)
- South Korea: [docs.openwatch.kr asset disclosure](https://docs.openwatch.kr/data/national-assembly/asset-disclosure); [UNODC Korea thematic compilation](https://www.unodc.org/documents/corruption/WG-Prevention/Art_8_Financial_disclosure_declaration_of_assets/Republic_of_Korea.pdf)
- Australia: [openpolitics.au analysis](https://openpolitics.au/analysis/the-missing-piece-of-the-integrity-puzzle-politicians-private-interests); [aph.gov.au registers](https://www.aph.gov.au/Senators_and_Members/Members/Register)
- Canada: [Office of the Conflict of Interest and Ethics Commissioner Public Registry](https://prciec-rpccie.parl.gc.ca/)
- Israel: [Wikipedia: State Comptroller of Israel](https://en.wikipedia.org/wiki/State_Comptroller_of_Israel); [Lehman-Wilzig: Stopping Ministerial Corruption (Times of Israel)](https://blogs.timesofisrael.com/stopping-ministerial-corruption-full-financial-disclosure/)
- Ukraine: [NACP register of declarations](https://nazk.gov.ua/en/news/register-of-declarations-is-the-largest-database-of-officials-assets-nacp-presents-experience-of-declaration-at-international-conference-in-poland/)
- Hungary / Poland / Czech Republic / Romania: [Transparencee CEE asset declarations report](https://assetdeclarations.transparencee.org/the-report); [EuroPAM Hungary profile](https://europam.eu/?module=country-profile&country=Hungary)

---

## 9. What this report does not answer

- **Per-jurisdiction empirical sample size.** I haven't downloaded and
  inspected the actual data for most jurisdictions — the fit assessment
  is based on regime design, not on counting analyzable rows. France
  and UK Commons I've sampled directly; the rest are described from
  secondary sources.
- **Quasi-event subset richness.** Could not assess how many rows in
  France's modificative declarations would map to identifiable
  in-office acquisitions of public-traded equities. Similar question
  for Canada's change-triggered updates.
- **Political-event annotation feasibility.** The companion question
  (policy-event windows on existing US data) isn't covered here. That
  belongs in a separate scoping doc once the direction is confirmed.
