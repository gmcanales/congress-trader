# Congress Trader

Docker app for ingesting congressional financial disclosures (Periodic
Transaction Reports), charting member trades against stock prices, and
backtesting whether following those trades has any edge.

**Current finding (as of run #18):** no actionable edge in broad
congressional trading on the tradeable `filing_date` anchor, and the
leadership-restricted Wei & Zhou-style cut is not credibly different from
zero after controlling for the 2025 trifecta cohort change and sector
composition. Full write-up:
[`reports/`](reports/) (latest dated HTML) and
[`docs/decision_log.md`](docs/decision_log.md).

## Prerequisites

- Docker Desktop or Docker Engine (Compose v2) — Linux, macOS, or Windows
- Network access to the project's PostgreSQL instance — the **NUC server at
  `db.internal.example:5432`, database `trading_db`**. You need a
  `DATABASE_URL` for this host. Ask the project owner if you don't have
  one.
- ~3 GB of disk for the backend image (PyMC + PyTensor are heavy)
- For yfinance data fetches (price history, sector lookups): outbound
  HTTPS

## First-run setup

```bash
git clone https://github.com/gmcanales/congress_trader
cd congress_trader

cp .env.example .env
# Edit .env and set DATABASE_URL to the NUC connection string, e.g.:
#   DATABASE_URL=postgresql://USER:PASSWORD@db.internal.example:5432/trading_db
# The POSTGRES_* vars are only used by the local backup `db` container
# (kept around as a fallback; not required for normal operation).

docker compose up -d --build       # builds image, starts backend + frontend + local db
docker compose logs -f backend     # confirm "Analysis views applied" + Uvicorn startup
```

On startup the backend applies idempotent SQL migrations
(`backend/sql/migrations.sql`) and analysis views (`backend/sql/views.sql`)
against the NUC. Nothing destructive runs — the migrations are
`ADD COLUMN IF NOT EXISTS` and `CREATE OR REPLACE VIEW`.

### Verify

```bash
# Health
curl http://localhost:8000/health
# → {"status":"ok"}

# Member count (sanity check we can read the existing data)
curl http://localhost:8000/api/members/ | python -c "import sys,json; print(len(json.load(sys.stdin)),'members')"

# Open the UI
open http://localhost      # macOS;  xdg-open / Windows Explorer for other platforms
```

The frontend has two views via the header toggle: **Trades** (a stock chart
with congressional buys/sells overlaid) and **Analysis** (snapshot datasets
and run backtests).

## Common commands

All CLI scripts run inside the backend container. The general form is
`docker compose run --rm --no-deps backend python <script> <flags>`.

```bash
# Run the parameterized event-study + rules backtest from the CLI.
docker compose exec backend python analyze.py \
    --train-start 2021-01-01 --train-end 2024-12-31 \
    --test-start  2025-01-01 --test-end  2025-12-31 \
    --date-field filing_date --benchmark SPY \
    --hold-days 21,63 --min-members 1

# Same, but restricted to trades made while the member held a
# leadership / committee-chair position.
docker compose exec backend python analyze.py --leadership-only \
    --date-field filing_date --benchmark SPY \
    --hold-days 63 --min-members 1

# Sector-neutral excess return instead of single-benchmark comparison.
docker compose exec backend python analyze.py \
    --benchmark-mode sector --leadership-only \
    --date-field filing_date --hold-days 63 --min-members 1

# Bayesian hierarchical model (PyMC, ~5-10 minutes).
docker compose run --rm --no-deps backend python bayesian_model.py

# Generate an HTML report bundling specific AnalysisRun ids.
docker compose run --rm --no-deps \
    -v "$(pwd)/reports:/app/reports" backend \
    python generate_report.py --run-ids 1,7,11,17,18 \
    --output-dir /app/reports
# Writes reports/report_runs<ids>_<YYYY-MM-DD>.html
```

### v2 data-prep tools (only needed for a fresh database)

The NUC instance already has these populated. Re-run only if you're
bootstrapping a new database or want to refresh from upstream sources.

```bash
# Backfill the canonical bioguide_id join key on members (uses
# unitedstates/congress-legislators).
docker compose run --rm --no-deps backend python backfill_bioguide.py --commit

# Backfill members.party from the same source.
docker compose run --rm --no-deps backend python backfill_party.py --commit

# Ingest committee + leadership positions + party_control for 2021-2025.
docker compose run --rm --no-deps backend python ingest_leadership.py --commit

# Ingest committee chairs / ranking members for the 117th-119th Congress
# (uses GitHub commits API for historical YAML snapshots).
docker compose run --rm --no-deps backend python ingest_committee_chairs.py --commit

# Ingest GICS sector + industry from yfinance for every traded ticker
# (~5-10 minutes, throttled to be polite to Yahoo).
docker compose run --rm --no-deps backend python ingest_sectors.py --commit
```

## Project layout

```
backend/                FastAPI app + analysis pipeline
  app/main.py             entrypoint, applies migrations + views on startup
  app/models.py           SQLAlchemy models
  app/routers/            members, transactions, prices, scraper, model, datasets
  app/services/           scraper, senate_scraper, ptr_parser, ocr,
                          price_service, backtest
  sql/migrations.sql      idempotent schema migrations (no Alembic)
  sql/views.sql           analysis views (v_stock_transactions,
                          v_stock_transactions_leadership, ...)
  analyze.py              backtest CLI (parameterized: anchor, benchmark,
                          grid, leadership_only, benchmark_mode)
  build_trade_dataset.py  per-trade DataFrame builder feeding the Bayesian
                          model
  bayesian_model.py       PyMC hierarchical regression on per-trade excess
  backfill_bioguide.py    populate members.bioguide_id from
                          unitedstates/congress-legislators
  backfill_party.py       populate members.party
  ingest_leadership.py    committees + chamber leadership + party_control
  ingest_committee_chairs.py  chairs / ranking members from historical YAML
  ingest_sectors.py       GICS sector / industry from yfinance
  generate_report.py      HTML report generator
frontend/               static HTML/CSS + vanilla JS + D3 v7
docs/
  decision_log.md         chronological narrative — read this first
  v2_leadership_plan.md   v2 scope, schema, sequencing
reports/                generated HTML backtest reports
docker-compose.yml
CLAUDE.md               live project doc / architecture spec
```

## Where to read next

- **[CLAUDE.md](CLAUDE.md)** — current architecture and project state.
- **[docs/decision_log.md](docs/decision_log.md)** — every analytical
  decision, data-scope expansion, and methodological pivot in chronological
  order, with cross-references to commits and AnalysisRun ids.
- **[docs/v2_leadership_plan.md](docs/v2_leadership_plan.md)** — v2 scope
  and the schema decisions behind it.
- **[reports/](reports/)** (latest dated file) — current canonical
  findings with embedded Plotly charts.

## Troubleshooting

- **`docker compose up` fails with port already in use** — another stack
  may be bound to 80 / 8000 / 5432. Either stop it
  (`docker ps` → `docker stop`) or change the port mappings in
  `docker-compose.yml`.
- **`Could not apply migrations` or `Could not apply views` in backend logs**
  — confirm `DATABASE_URL` in `.env` points at the right host and the
  user has DDL permissions. The applier logs but does not abort startup if
  application fails, so the backend will appear "up" while the schema is
  half-applied.
- **PyMC sampling is "severely degraded"** — the backend image needs `g++`
  for PyTensor to compile its samplers natively. Already in the Dockerfile;
  if you rebuilt from a fork that strips it out, you'll see a warning at
  import time and sampling will be unusably slow.
- **yfinance returns empty `info`** for many tickers (delisted, foreign,
  fund codes). Expected; `ingest_sectors.py` keeps a stub row so future
  runs don't re-fetch.

## License

Internal / personal-research repo. Not currently licensed for external
use. Treat the data on the NUC as sensitive (it's public-source but
joining it with personal modeling work isn't redistributable).
