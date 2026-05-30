---
name: parameterized-analysis-runner
description: Replace one-off analysis scripts with parameterized, traceable analysis pipelines. Use whenever the user asks to "quickly check" something, "compute" a result, or "see if X is true" in a data project. The default should be: extend the existing parameterized engine with whatever new knob the question needs, run it, persist the params + scores + full results to an AnalysisRun-style table, then read the answer from there. NEVER one-off scripts that produce results no one can reproduce or compare.
---

# Parameterized Analysis Runner

## The anti-pattern

User asks a question. You write a quick Python script in `scratch.py`,
print some numbers, paste them back. Three weeks later: nobody knows
which data subset that ran on, what filters were applied, what version
of the codebase, what counts as "edge." The number is unreproducible
and incomparable to the next number.

This is the single most expensive failure mode in research projects.
It compounds — each one-off bleeds into the next, none of them
auditable, contradictory results get attributed to "noise" when really
they reflect quietly different inputs.

## The pattern

Every analytical question goes through:

1. **A parameterized engine** (CLI, function, or service) that exposes
   every meaningful knob — time windows, anchors, filters, benchmarks,
   horizons, threshold grids
2. **A persistent run record** with params + scores + full results saved
   to a table (in this project: `AnalysisRun` rows in the
   `analysis_runs` table)
3. **A report generator** that consumes runs by id and produces the
   human-readable artifact (HTML, PDF, notebook export)

The "quick question" becomes: extend the engine with a new param if
needed, run it once, save the AnalysisRun, point the report at the
new id. Three weeks later, anyone can rerun the exact same analysis,
or compare it head-to-head against the previous version.

## When to invoke

Invoke this skill when:
- User says "can you check if X" or "compute Y" or "what does the data
  say about Z" on any project that already has a parameterized engine
- User is about to write or has written a one-off `scratch_*.py`
- User asks for a result that should be comparable to prior results
  (run #N vs run #M)
- A new question needs a knob that doesn't yet exist on the engine

## When NOT to invoke

- One-off interactive exploration in a Jupyter session, never persisted
  to git, where the cost of formalizing exceeds the value
- The project doesn't have an engine yet — in which case the first
  step is to BUILD the engine, not the one-off

## Pattern in this project

The full pattern lives across three files:

1. **`backend/app/services/backtest.py`** — the engine. Functions take
   anchors, windows, filters, horizons as explicit parameters; persist
   `params` dict in the result; `event_study()`, `rules_backtest()`,
   `train_test()` all follow the same shape.

2. **`backend/analyze.py`** — the CLI wrapper. Every parameter the
   engine accepts is exposed as a CLI flag with sensible defaults.
   Reads JSON, prints summary tables, writes the full result to
   `--output path.json`. Documented examples in the docstring.

3. **`backend/app/models.py`** — `AnalysisRun` table. Persists params,
   scores, full results (as JSON), timestamps. Indexed for retrieval
   by id.

4. **`backend/generate_report.py`** — the report generator. Consumes
   `AnalysisRun` rows by id, emits HTML with embedded Plotly charts.
   Pin specific run ids in the output filename for traceability:
   `reports/report_runs7-8_2026-05-22.html`.

## Adding a new parameter — the right way

User asks: "What if we only counted purchases over $50k?"

Wrong: write a one-off script that filters and re-runs.

Right:
1. Add `min_trade_value: float = 0.0` to the engine signature
2. Apply the filter in the engine
3. Expose `--min-trade-value` on the CLI
4. Document the new flag in the docstring
5. Run it: `python analyze.py --min-trade-value 50000`
6. Save the AnalysisRun
7. Reference the run id in the answer back to the user

Now anyone can rerun, compare, or escalate to a wider grid sweep.

## Anti-pattern signals to watch for

- New file named `quick_*.py`, `check_*.py`, `temp_*.py`, `scratch_*.py`
- Inline pandas/numpy in a chat answer without a corresponding engine
  change
- Numbers cited in a conversation that aren't traceable to a run id
- "Just for this question" carve-outs in the engine logic
- Hardcoded date ranges or thresholds in the engine body (those are
  parameters, not defaults)

## Reference

- `backend/analyze.py` — exemplary CLI wrapper
- `backend/app/services/backtest.py` — engine with full param surface
- `backend/generate_report.py` — report generator pattern
- This project's user-feedback memory `feedback_parameterized_analysis.md`
  has this as a long-standing principle

## Beyond this project

Same pattern applies to any research project: ML experiments
(wandb/mlflow run IDs), policy analysis (parameter sweeps over scenarios),
financial backtests (param grids), simulation studies (seed-replicable
runs), causal-inference studies (model-spec robustness checks).
