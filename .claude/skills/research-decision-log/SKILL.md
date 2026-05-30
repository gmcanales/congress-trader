---
name: research-decision-log
description: Maintain a chronological decision_log.md alongside research projects that captures every scope decision, methodological pivot, null result, and "why we changed direction" with cross-references to commits and analysis-run ids. Lets future-you (or a collaborator) pick the project up cold without losing context. Use when the user is doing iterative research work — scope changes, methodology tweaks, null results that warrant follow-up — particularly in projects where the chain of decisions matters as much as the final state.
---

# Research Decision Log

A `docs/decision_log.md` next to a research project that captures the
chronology of decisions — what was tried, what was found, what was
discarded, what's still open. Companion to a live-state CLAUDE.md (the
"where things stand right now") and the rendered findings in `reports/`
(the "what the data showed").

The decision log is the bridge between code (`git log` tells you what
changed) and findings (the reports tell you the result). It tells you
**why** each pivot happened.

## When to use

- The user is doing iterative research with branching scope (v1 → v1.5
  → v2 → v2.5 → v2.6 …)
- A scope decision was just made that won't be obvious from the diff
- A null result was just confirmed (these especially need documentation
  or they get forgotten and rediscovered)
- A methodology was tweaked and the prior approach should be deprecated
- A collaborator asks "why did we do it that way?"
- The project has been dormant and needs to be picked back up

## Structure

Lead with a brief preamble:
- The project's central question(s)
- The literature anchor or hypothesis the project is testing against
- A pointer to live state (CLAUDE.md) and rendered findings (reports/)

Then chronological sections, numbered hierarchically:

```
## 0. Project context
   Central question, constraints, lit anchor.

## 1. v1 baseline
   ### 1.1 Data ingestion scope
   ### 1.2 Backtest engine v1
   ### 1.3 v1 findings
   ### 1.4 v1 takeaway

## 2. v2 leadership cut
   ### 2.1 Why a v2 — what v1 left open
   ### 2.2 Schema additions
   ### 2.3 v2 findings
   ### 2.4 v2 takeaway

## 3. v2.5 owner stratification
   ...

## 4. v2.6 cabinet ingest
   ...
```

Each section gets:
- **Decision** — what was decided in one sentence
- **Why** — the prior result or external nudge that motivated it
- **Cross-references** — git commit short-hash (`b10f932`), AnalysisRun
  id range (`runs 7–8`), report file path
- **Outcome** — what was found (including nulls)

## Writing conventions

1. **Date scope expansions explicitly.** "v2.5 spouse-cut work begins
   2026-05-21" — relative dates rot.

2. **Capture nulls with the same prominence as positive findings.**
   "P(β>0)=0.58, HDI [-2.1%, +2.5%], not credibly distinguishable
   from zero" — null results document what's NOT in the data, which is
   load-bearing context for future work.

3. **Name what was discarded and why.** "Considered using insider
   trade-amounts; rejected because PTRs only report ranges; would
   require sensitivity testing we deferred to v3."

4. **Link to artifacts, not just describe them.** Inline link the
   report file, the AnalysisRun ids, the commit. Future-you wants
   to click through.

5. **Distinguish principled decisions from convenience.** "Used SPY as
   benchmark because cap-weighted matches member-trade weighting"
   (principled) vs "Used 2025 as test window because 2026 data wasn't
   loaded yet" (convenience — flag for follow-up).

6. **Preserve dead-end pointers.** If a path was explored and
   abandoned, note it so future-you doesn't redo the work. "Considered
   ingesting congressional-staff PTRs as additional surface area;
   investigated and ruled out — 2013 S.716 amendment removed staff
   filings from public online disclosure (docs/us_state_…md §8)."

7. **One trailing 'open questions' section at the end** with what's
   live but not yet decided. Move items out as they're resolved.

## Pointer pattern

Once the decision log exists, add a single line to CLAUDE.md pointing
at it:

> The full chronological narrative — every major decision, scope
> expansion, and methodological pivot, with cross-references to commits
> and AnalysisRun ids — is in [`docs/decision_log.md`](docs/decision_log.md).
> Read that first if you're picking the project up cold.

This is the highest-ROI single line in the project — anyone landing on
the repo cold finds the chain of reasoning in one click.

## When to update the log

Update IMMEDIATELY when:
- A scope decision is finalized in conversation
- A null result is confirmed (don't wait — null results get forgotten)
- A methodology gotcha is identified (e.g., the rules-vs-event-study
  discrepancy in this project)
- A version transition happens (v1 → v2)

Don't update for:
- Trivial code refactors (those belong in git log)
- Implementation bugfixes (git log)
- UI / non-analytical changes

## Reference

- `docs/decision_log.md` in this project — the exemplar, currently §0–§8
  with the v1 null, v2 leadership cut, v2.5 spouse cut, v2.6 cabinet
  ingest, and NUC migration narrative
- `CLAUDE.md` "### Project history" section — the canonical pointer

## Cross-references

- **parameterized-analysis-runner**: enables this skill — without
  AnalysisRun ids, the log can't cite specific results unambiguously
- **disclosure-backtest-discipline**: the anchor-choice principle is
  the kind of methodology rule the log preserves
- **rules-vs-event-study-dual-report**: the v2.5 catch is a perfect
  example of what the log needs to capture
