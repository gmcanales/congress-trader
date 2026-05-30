---
name: rules-vs-event-study-dual-report
description: For backtests on clustered-entry universes (where many positions cluster on the same dates or in the same windows), always report BOTH a rules-engine NAV result AND a per-trade event-study mean. They can disagree by 8+ percentage points — the rules result can be inflated by sequencing artifacts unrelated to the underlying signal edge. Reporting only one is a real risk of overstating a strategy. Use when interpreting or building any backtest on disclosed-trade universes, sector-rotation strategies, calendar-effect strategies, or any other entry rule that produces clustered position dates.
---

# Rules-vs-Event-Study Dual Report

## The trap

Two different ways to measure a backtest's "edge" can disagree by
8+ percentage points on the SAME underlying data and rules:

1. **Rules-engine NAV result.** Simulate a portfolio. Apply the entry/
   exit rules. Compute the final portfolio value vs the benchmark.
   Headline number: total return or annualized excess.

2. **Per-trade event-study mean.** For each individual entry, compute
   the excess return over the next N days vs the benchmark. Average
   across all entries. Headline number: mean (or median) per-trade
   excess.

When entries cluster — e.g., all the "buy" signals fire on the same
3-month window because everyone disclosed in the same quarter — the
NAV result picks up sequencing artifacts (capital deployment timing,
position-sizing interactions, calendar correlation between positions)
that are NOT in the per-trade event-study mean.

In this project's v2.5 work, the rules-backtest NAV showed a strategy
beating SPY by ~8 percentage points while the per-trade event-study
on the same trades showed essentially zero excess. The "edge" was
sequencing, not signal.

## Hard rule

For any backtest on a universe where entries can cluster:
**report both numbers, in the same place, with equal prominence.**

If they agree (within ~1pp), the strategy probably has real edge —
the signal is robust to sequencing.

If they disagree by >2pp, the gap IS the sequencing premium. Report it
explicitly. The per-trade event-study is the more honest measure of
"would this signal generalize to other deployment regimes?" — the rules
NAV is the more honest measure of "what would this specific deployment
have actually returned?"

Both are legitimate; reporting only one is the trap.

## When this skill applies

Clustered-entry universes:
- Disclosure-based strategies (everyone files in the same quarter)
- Calendar-effect strategies (entries cluster on specific dates)
- Earnings-event strategies (clusters around earnings season)
- Sector-rotation strategies (entries cluster on sector tilts)
- Insider-trading following (clusters around Form 4 windows)
- Any strategy where N positions can fire within a short window

Not applicable when:
- Entries are independent and time-spread by construction (e.g., random
  daily sampling, single-stock single-entry)
- Position sizing is capped per-day or per-window such that sequencing
  effects are bounded

## What to report

For every headline backtest result, include:

```
=== HEADLINE RESULT ===

Rules-engine NAV       : +X.XX% vs SPY   (n=YYY entries)
Per-trade event-study  : +A.AA% mean excess
                         (median: +B.BB%, win-rate: C%)

Anchor: filing_date (tradeable)
Window: 2025-01-01 .. 2025-12-31

[If the two numbers disagree by >2pp]
Sequencing premium: ~Z pp
  → the rules result is inflated relative to the underlying signal;
    the per-trade event-study is the more honest measure of generalizable edge.
```

## Implementation

Both measurements should come out of the same parameterized engine
(see the **parameterized-analysis-runner** skill). The engine should
expose two functions on the same input dataset:

```python
def event_study(events, ..., date_field, horizons):
    """Per-trade event study. Returns per-horizon mean/median/win-rate."""

def rules_backtest(events, ..., entry_rules, exit_rules, position_sizing):
    """Rules-engine NAV simulation. Returns time-series NAV + summary."""
```

The report consumer pulls both for the same AnalysisRun and surfaces
both numbers in the headline section.

## How to interpret a large gap

A 5+ pp gap typically signals one of:

1. **Capital deployment timing.** All entries cluster early/late in
   the window; the cumulative effect of being long during a particular
   market regime swamps the signal.

2. **Position-sizing artifacts.** If position sizing scales with
   cluster intensity, big clusters get big bets — but the signal
   per cluster member isn't stronger.

3. **Survival bias in long-only NAV.** Losers exit at fixed horizons;
   winners can compound. The NAV inherits this asymmetry; the
   event-study doesn't.

4. **Concentration in a few large entries.** If 3 positions dominate
   the NAV, the rules result is reflecting those 3 trades' luck;
   the per-trade mean averages them down properly.

When the gap is this large, the per-trade event-study is the honest
generalization estimate. Treat the rules NAV as "what THIS specific
deployment would have done," not "what this signal generally does."

## Reference

- `backend/app/services/backtest.py` — exposes both `event_study()` and
  `rules_backtest()`; `train_test()` orchestrates a paired comparison
- `docs/decision_log.md` — search for the v2.5 spouse-cut writeup;
  the rules-vs-event-study disagreement was the methodological catch
  that made the project's recommendation hierarchy more honest
- `backend/generate_report.py` — both numbers should appear in the
  headline section of every report

## Cross-references

- **disclosure-backtest-discipline**: anchor selection is the OTHER
  necessary discipline; this skill assumes you've already fixed the
  anchor correctly
- **parameterized-analysis-runner**: both measurements should be
  parameterized side-effects of the same engine, not separate scripts
