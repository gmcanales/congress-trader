# Congress Trader

Docker app for ingesting congressional financial disclosures (Periodic
Transaction Reports), charting member trades against stock prices, and
backtesting whether following those trades has any edge.

## Stack

- **Backend** — FastAPI (Python 3.12), SQLAlchemy, served on `:8000`.
- **Frontend** — static HTML/CSS + vanilla JS + D3 v7, served by nginx on `:80`
  (nginx also proxies `/api` → backend).
- **Database** — PostgreSQL **on an external NUC server** (`db.internal.example:5432`,
  db `trading_db`). Connection string is in `.env` as `DATABASE_URL`.
- **Orchestration** — `docker-compose.yml` (services: `backend`, `frontend`,
  and a now-unused local `db` container kept only as a data backup).

## Running it

```
docker compose up -d --build      # start everything
docker compose logs -f backend    # watch backend
```

Open `http://localhost` — two views via the header toggle:
- **Trades** — pick a Most-Traded ticker (or search one), see member buys/sells
  plotted on the price chart.
- **Analysis** — snapshot datasets, run backtests, compare runs.

Dev preview without nginx: `.claude/dev_preview.py` serves the frontend and
proxies `/api` to `:8000` (used for the preview tooling).

## Data pipeline

Disclosures only exist as **PDFs** (House) or **HTML** (Senate) — there is no
clean bulk transaction feed. Ingestion (Admin panel → `POST /api/scrape/`):

- **House** (`services/scraper.py`) — downloads the year's index ZIP, then each
  PTR PDF. `services/ptr_parser.py` extracts text via `pdftotext -layout` and
  regex-parses the transaction table. Scanned filings (~12%) fall back to
  `services/ocr.py` (Tesseract) — but old checkbox-grid forms parse poorly and
  are a **deferred** problem (see memory).
- **Senate** (`services/senate_scraper.py`) — handles the efdsearch.senate.gov
  agreement/CSRF flow and parses the electronic PTR HTML tables.

Every transaction carries `confidence` (high/medium/low) and `filing_date`
(the public date — **use this for any backtest**, not `transaction_date`).

## Model / analysis

- `services/backtest.py` — the parameterized engine: event study + grid-searched
  rules backtest + train/test orchestration. All knobs are arguments.
- `analyze.py` — CLI wrapper: `docker compose exec backend python analyze.py --help`
- API: `POST /api/model/run` (background), `GET /api/model/runs`, `/runs/{id}`.
- **v1 outcome: no edge found.** Following disclosures does not beat SPY; the
  signal is absent even on `transaction_date`. Details in the memory files.

**Principle:** all analysis must run through parameterized, traceable tools —
never one-off scripts. Datasets + analysis_runs tables exist for traceability.

## Database schema

Tables: `members`, `disclosures`, `transactions`, `price_bars`, `datasets`
(named analysis snapshots), `analysis_runs` (params + scores + full results).

Views (`backend/sql/views.sql`, auto-applied on startup): `v_stock_transactions`
(canonical analyzable trades), `v_member_activity`, `v_ticker_activity`.

## Key files

```
backend/app/main.py            FastAPI app, router wiring, view application
backend/app/models.py          SQLAlchemy models
backend/app/routers/           members, transactions, prices, scraper, model, datasets
backend/app/services/          scraper, senate_scraper, ptr_parser, ocr, price_service, backtest
backend/sql/views.sql          analysis views
backend/analyze.py             backtest CLI
frontend/js/{app,chart,analysis,api}.js
docker-compose.yml  .env
```

## Current state

- Data: 2021–2025 House + Senate (~12k transactions) ingested and migrated to
  the NUC. Local `db` container holds a backup copy.
- v1 model complete (negative result). No further modeling in progress.
- Known gap: ~12% scanned/checkbox-grid filings parse poorly (deferred).

Project context and decisions are also tracked in Claude's memory files under
`~/.claude/projects/.../memory/` — read `MEMORY.md` there first.
