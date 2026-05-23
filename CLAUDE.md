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
./scripts/deploy.sh               # build with cache-busting hash + start everything
docker compose logs -f backend    # watch backend
```

`deploy.sh` injects the current git short-hash as `?v=<hash>` into all local
JS/CSS asset URLs at image build time, so Cloudflare and browsers never serve
stale files after a deploy. After running on the NUC, also purge the Cloudflare
cache (dashboard → your domain → Caching → Purge Everything).

Plain `docker compose up -d --build` still works but assets default to `?v=dev`
(no cache busting).

Open `http://localhost` — two views via the header toggle:
- **Trades** — pick a Most-Traded ticker (or search one), see member buys/sells
  plotted on the price chart.
- **Analysis** — snapshot datasets, run backtests, compare runs.

Dev preview without nginx: `.claude/dev_preview.py` serves the frontend and
proxies `/api` to `:8000` (used for the preview tooling).

## Public deployment

The app is reachable at **https://goosebase.org**, gated by Cloudflare Access
(one-time-PIN by email to an explicit allowlist). Domain registered via
Cloudflare Registrar.

Transport: `cloudflared` runs as a **systemd service on the NUC** (primary),
holds a connector token registered to a named tunnel, and its single Public
Hostname rule maps `goosebase.org` → `http://localhost:80` (nginx). TLS is
terminated at Cloudflare's edge — the connector → origin hop is plain HTTP,
which is why the dashboard's `https://localhost:8080` placeholder is wrong
for this setup. Auth team subdomain: `YOUR-TEAM.cloudflareaccess.com`.

To allow more users: Zero Trust dashboard → Access → Applications → edit
the app's policy → add their email. (The OTP login method requires no
identity-provider setup; CF emails a 6-digit code on demand.)

Gotcha worth knowing: newly-registered `.org` domains get DNS-blocked by
security-focused VPNs (NordVPN Threat Protection, WARP+Gateway, corporate
filters) for ~24–72h until threat-intel feeds mark them benign. If a
collaborator can't reach the URL, that's the most likely cause — have them
try `https://goosebase.org/cdn-cgi/trace` as a CF-only liveness check.

The NUC database (`db.internal.example:5432`) remains LAN-only.

### NUC deployment (live as of 2026-05-23)

Backend, frontend, and cloudflared now run on the NUC. The dev box can
sleep nightly. Bayesian fitting stays on the dev box (too RAM-hungry for
the NUC). Backtests run ~15–30% slower on the NUC's i5-1240P; the
loopback DB connection partially offsets the CPU drop.

NUC spec: **3.6 GB RAM / ~2.6 GB available, 2 GB swap, i5-1240P (12C/16T),
NVMe SSD.** Headroom from: disabling `nodm` (~600 MB), disabling `nordvpnd`
(~100 MB), adding 2 GB swapfile with `vm.swappiness=10`.

Architecture and full migration history in [decision_log.md §7](docs/decision_log.md).

### Dev override (switching traffic to the dev box)

`scripts/dev-on.ps1` and `scripts/dev-off.ps1` (run from the dev box)
swap the active cloudflared connector so the dev box serves traffic while
you work, then hand back to the NUC when done:

```powershell
# Take over from NUC
.\scripts\dev-on.ps1

# Hand back to NUC
.\scripts\dev-off.ps1
```

**One-time SSH setup** (dev box → NUC) required before first use:
```powershell
ssh-keygen -t ed25519   # if no key exists
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh root@db.internal.example "cat >> ~/.ssh/authorized_keys"
```

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
(the public date — **use this for any actionable backtest**, never
`transaction_date`; see "Anchor choice" below).

## Model / analysis

- `services/backtest.py` — the parameterized engine: event study + grid-searched
  rules backtest + train/test orchestration. `benchmark` is a parameter
  (SPY cap-weighted, RSP equal-weighted, …).
- `analyze.py` — CLI wrapper: `docker compose exec backend python analyze.py --help`
- API: `POST /api/model/run` (background), `GET /api/model/runs`, `/runs/{id}`.
- `generate_report.py` — parameterized HTML report generator. Pulls runs by
  id, emits `reports/report_runs<ids>_<YYYY-MM-DD>.html` with embedded
  Plotly charts via CDN.

### v1 outcome — confirmed null

Eight runs landed at the same conclusion: **no actionable edge in broad
congressional trading.** The cleanest cut (runs #7/#8 — pinned grid
`hold=63, min_members=1`, anchored on `filing_date`) shows OOS strategy
returns of +15.08% vs SPY +17.90% (excess −2.82%) and RSP +14.99%
(excess +0.09%, i.e. flat against equal-weight).

The mirror runs on `transaction_date` (#5/#6) appeared to show +2.53% / +5.44%
excess but that's pure look-ahead: the 5-point gap is the disclosure-lag
premium (congressional buys drift up during the ≤45-day reporting window).
A follower can only act at disclosure — by which point the move is gone.
Full diagnosis in the latest `reports/report_runs*_*.html` file.

### Anchor choice

The engine accepts either `date_field`:

- `filing_date` — **tradeable.** The public observation date. Any claim of
  "edge" must use this anchor.
- `transaction_date` — **diagnostic only.** The actual trade date is not
  publicly observable until the disclosure files (up to 45 days later);
  using it builds in look-ahead bias.

**Principle:** all analysis must run through parameterized, traceable tools —
never one-off scripts. `datasets` + `analysis_runs` tables exist for
traceability. `generate_report.py` consumes those rows.

## Database schema

Core tables: `members`, `disclosures`, `transactions`, `price_bars`,
`datasets` (named analysis snapshots), `analysis_runs` (params + scores +
full results).

v2-prep tables (see "v2 leadership work" below): `committees`, `positions`,
`party_control`. `members.bioguide_id` is the canonical join key with
external congressional datasets.

Schema migrations are applied at backend startup from
`backend/sql/migrations.sql` (idempotent ALTER / CREATE statements — no
alembic). Views from `backend/sql/views.sql` are applied next:
`v_stock_transactions` (canonical analyzable trades), `v_member_activity`,
`v_ticker_activity`, and `v_stock_transactions_leadership` (transactions
tagged with the position the member held on the transaction date, plus
party-controls-chamber flag).

## v2 leadership work

Motivated by Wei & Zhou (NBER, 2025): rank-and-file congressional trades
have no edge (matches our v1) but ~47pp/yr alpha for the 20 lawmakers who
ascended into leadership. Scope and sequencing live in
`docs/v2_leadership_plan.md`.

Status:
- **Step 1 done** — `members.bioguide_id` backfilled (311 / 313 matched
  via `backend/backfill_bioguide.py`).
- **Step 2 in progress** — committees / positions / party_control
  ingestion from `unitedstates/congress-legislators`
  (`backend/ingest_leadership.py`).
- Step 3 → re-run the backtest restricted to trades made *during* a
  leadership / chair tenure, on `filing_date`, with both SPY and RSP.

## Key files

```
backend/app/main.py            FastAPI app, router wiring, migrations + views applier
backend/app/models.py          SQLAlchemy models
backend/app/routers/           members, transactions, prices, scraper, model, datasets
backend/app/services/          scraper, senate_scraper, ptr_parser, ocr, price_service, backtest
backend/sql/migrations.sql     idempotent schema migrations (applied on startup)
backend/sql/views.sql          analysis views (applied on startup)
backend/analyze.py             backtest CLI (parameterized: anchor, benchmark, grid)
backend/generate_report.py     parameterized HTML report generator
backend/backfill_bioguide.py   one-shot: backfill bioguide_id from congress-legislators
backend/ingest_leadership.py   one-shot: ingest committees/positions/party_control
frontend/js/{app,chart,analysis,api}.js
docs/v2_leadership_plan.md     v2 scope, schema, data sources
reports/                       generated HTML reports (report_runs<ids>_<date>.html)
docker-compose.yml  .env
```

## Current state

- Data: 2021–2025 House + Senate (~12k transactions) ingested on the NUC.
  Local `db` container holds a backup copy.
- v1 backtest complete — null on the actionable (`filing_date`) anchor.
- v2 leadership work complete through step 3: bioguide_id backfilled,
  positions table populated (37 chamber leadership + 120 committee
  chairs/rankings = 157 tenures), sector data ingested, Bayesian
  hierarchical model fitted.
- 18 `AnalysisRun` rows catalogued (#1-#17 rules-backtests + #18
  Bayesian hierarchical). Current canonical report:
  `reports/report_runs1-2-3-4-5-6-7-8-9-10-11-12-13-14-15-16-17-18_2026-05-21.html`.
- **v2 finding:** Bayesian posterior on `leadership_at_trade` after
  controlling for member baseline, in-majority status, post-trifecta
  period, party, and sector: P(β > 0) = 0.58, 94% HDI [−2.1%, +2.5%].
  Not credibly distinguishable from zero. The rules-backtest "leadership
  edge" was largely the trifecta-period dummy (P=0.90) + sector
  composition (energy long, cyclicals/materials/industrials short)
  mis-attributed because the leadership filter overlaps the trifecta
  window in our data.
- Known data gap: ~12% scanned/checkbox-grid filings parse poorly
  (deferred).
- App is publicly reachable at https://goosebase.org via Cloudflare Tunnel
  + Access (email-OTP allowlist). See "Public deployment" above.
- **NUC migration complete (2026-05-23).** Backend + frontend + cloudflared
  running on NUC. Dev box can sleep nightly. Dev override via
  `scripts/dev-on.ps1` / `scripts/dev-off.ps1`.

### Project history

The full chronological narrative — every major decision, scope
expansion, and methodological pivot, with cross-references to commits
and AnalysisRun ids — is in [`docs/decision_log.md`](docs/decision_log.md).
Read that first if you're picking the project up cold.

Project context and decisions are also tracked in Claude's memory files
under `~/.claude/projects/.../memory/` — read `MEMORY.md` there first.
