---
name: disclosure-backtest-discipline
description: Avoid look-ahead bias when backtesting strategies on disclosure-based event data (insider trades, congressional PTRs, 13F filings, OGE 278-T, foreign-investor disclosures). Use when the user asks to build, modify, or interpret a backtest where the events are filings whose underlying actions occurred earlier and weren't publicly observable at the time. The single most expensive mistake in this domain is anchoring events on the underlying-action date rather than the disclosure date.
---

# Disclosure Backtest Discipline

When the events you're backtesting are *disclosures of past actions*
(congressional STOCK-Act PTRs, insider Form 4s, 13F holdings reports,
OGE 278-T executive disclosures, foreign-investor filings), there are
two dates per event:

- **Underlying-action date** — when the actor actually did the thing (e.g.,
  `transaction_date` for a stock trade)
- **Disclosure date** — when the action became publicly observable (e.g.,
  `filing_date`, with a reporting lag of up to 45 days under the STOCK Act)

A tradeable strategy can only react to disclosures. **Anchoring events
on the underlying-action date builds look-ahead bias directly into your
results** — your "edge" is whatever drift happened during the reporting
window before the public ever knew.

## When this skill applies

- User is building or modifying a backtest of a strategy that follows
  disclosed actions (politicians, insiders, foreign investors)
- User is interpreting results from such a backtest
- User is comparing strategies built on different anchors and surprised
  by the gap
- User is auditing a published finding that follows disclosed actions

## Hard rules

1. **Default anchor: the disclosure date.** Any claim of tradeable edge
   must come from a backtest anchored on the public observation date.

2. **The underlying-action date is diagnostic-only.** Use it to
   characterize the reporting-lag premium, to validate signal sources,
   or to compare against the tradeable result — but never as the basis
   for an actionable claim.

3. **Always run both for headline results.** The gap between
   action-anchored and disclosure-anchored returns IS the reporting-lag
   premium. Reporting that gap is itself a useful finding. In this
   project, runs #5/#6 (transaction_date) showed +2.5–5.4% excess
   while runs #7/#8 (filing_date) showed −2.8% excess; that 5-point
   gap was the disclosure-lag drift, recoverable only by an insider.

4. **Confidence-tag the parsed input data.** PDF / HTML / scanned-form
   ingestion produces a mix of high-confidence and parse-degraded rows.
   Tag each row at ingest with `confidence: high/medium/low` and let
   the backtest engine accept a confidence filter. Don't silently drop
   low-confidence rows from the corpus — make the filter explicit so
   results are reproducible and the impact of inclusion can be measured.

5. **Treat partial-disclosure regimes carefully.** Some regimes only
   disclose ranges (e.g., "$1,001–$15,000"), not exact amounts. Don't
   trade-weight without explicit handling — either use range midpoints
   with sensitivity testing, or move to equal-weighting and disclose
   the methodology.

## Implementation pattern

Make the anchor a first-class parameter, not a default-buried-in-code:

```python
def event_study(start: date, end: date,
                date_field: str = "filing_date",   # tradeable default
                ...):
    """
    date_field chooses the event anchor:
      'filing_date' (the public date — for a tradeable strategy)
      'transaction_date' (when the actor actually did it — diagnostic only,
                         builds in look-ahead bias)
    """
    date_col = getattr(models.Transaction, date_field)
    ...
```

Persist the anchor in the analysis-run record so results are not
mistakable later:

```python
result = {
    "params": {"date_field": date_field, ...},
    ...
}
```

Reports should display the anchor in the headline, not buried in
methodology — `event_study | anchor=filing_date | n=2,184` so a reader
can never confuse a diagnostic run with a tradeable claim.

## Telltale signs of a violated anchor

If you see ANY of these in a published "edge" finding, suspect
look-ahead:

- Sharpe > 2.5 on a long-only strategy following public disclosures
- Win rate > 60% on individual trades with short forward horizons
- Excess return concentrated in the first 0–5 trading days post-event
  (this is the reporting-lag drift, not real edge)
- Headline talks about "the trade date" or "when [actor] traded"
  without saying "the filing date" or "when it became public"

## Cross-references

- **rules-vs-event-study-dual-report** skill: the other layer of
  reporting discipline once you've fixed the anchor
- **parameterized-analysis-runner** skill: anchor parameter should be
  one of many exposed, traceable knobs
- This project's `backend/app/services/backtest.py` is the reference
  implementation; `docs/decision_log.md` §1.1 has the project's specific
  treatment

## Beyond this project

Same discipline applies to: insider Form 4 backtests, 13F-following
strategies, hedge-fund-letter mirror portfolios, foreign-investor
filings (Form CRS, SLT, 144), executive ethics disclosures, judicial
disclosures (Free Law), international parliamentary disclosure regimes.
