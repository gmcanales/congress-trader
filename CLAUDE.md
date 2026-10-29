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

**Keeping cloudflared patched.** The apt source shipped pointing at
`https://pkg.cloudflare.com/cloudflared trixie`, a suite Cloudflare does
**not** publish — it 404s, so `apt` silently had no upgrade path and the
NUC sat on 2026.5.0 for five months without ever reporting an available
update. Corrected 2026-10-29 to the `any` suite (right choice for a static
Go binary; `bookworm` also works, `trixie` does not):

```
deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] \
    https://pkg.cloudflare.com/cloudflared any main
```

The unit runs with `--no-autoupdate`, so patches are manual:
`apt update && apt install --only-upgrade cloudflared && systemctl restart cloudflared`.
Restarting drops the tunnel for a few seconds — this is the only connector,
so the public site is briefly down. Check `journalctl -u cloudflared` for
four `Registered tunnel connection` lines afterwards.

To allow more users: Zero Trust dashboard → Access → Applications → edit
the app's policy → add their email. (The OTP login method requires no
identity-provider setup; CF emails a 6-digit code on demand.)

Gotcha worth knowing: newly-registered `.org` domains get DNS-blocked by
security-focused VPNs (NordVPN Threat Protection, WARP+Gateway, corporate
filters) for ~24–72h until threat-intel feeds mark them benign. If a
collaborator can't reach the URL, that's the most likely cause — have them
try `https://goosebase.org/cdn-cgi/trace` as a CF-only liveness check.

The NUC database (`db.internal.example:5432`) remains LAN-only.

**Public leaderboard (`/public_api/`).** The arcade games use a separate URL
prefix that CF Access does *not* gate. Reads (`GET /public_api/games/leaderboard`)
are open; writes require an `X-Game-Key` header whose value must match
`GAME_POST_KEYS` in `.env` (forwarded into the container via `docker-compose.yml`).
Three-layer nginx rate limiting throttles the public surface — see
[`docs/hardening.md`](docs/hardening.md) for the full design.

### NUC deployment (live as of 2026-05-23)

Backend, frontend, and cloudflared now run on the NUC. The dev box can
sleep nightly. Bayesian fitting stays on the dev box (too RAM-hungry for
the NUC). Backtests run ~15–30% slower on the NUC's i5-1240P; the
loopback DB connection partially offsets the CPU drop.

NUC spec: **3.6 GB RAM / ~2.6 GB available, 2 GB swap, i5-1240P (12C/16T),
NVMe SSD.** Headroom from: disabling `nodm` (~600 MB), disabling `nordvpnd`
(~100 MB), adding 2 GB swapfile with `vm.swappiness=10`.

Architecture and full migration history in [decision_log.md §8](docs/decision_log.md).

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

## Secret scanning (pre-commit)

`gitleaks` blocks commits that introduce secrets. Enable it once per clone:

```bash
git config core.hooksPath scripts/hooks     # hook lives in scripts/hooks/pre-commit
```

Install the binary from the gitleaks releases page (single static Go binary,
no runtime deps). Without it the hook warns and passes rather than blocking,
so a machine that lacks it is not silently unprotected — it says so.

Rules are in `.gitleaks.toml`. **The stock ruleset is not sufficient here**:
scanning this repo's history with defaults alone reports "no leaks found",
while the Postgres password committed in `docker-compose.nuc.yml` sits in two
commits untouched — eight characters, 2.75 bits of entropy per character,
matching no provider pattern. The custom rules target the shapes this project
actually leaks: database URLs with inline passwords (no entropy floor, because
a weak password is still a leaked password), JWT/tunnel tokens, and generic
`PASSWORD=`/`SECRET=`/`TOKEN=` assignments.

The hook scans **staged changes only**. That is deliberate: `.env` is
gitignored and holds live credentials, so a working-tree scan would flag it on
every commit and train everyone to reach for `--no-verify`.

Known accepted finding: commit `1b44cfb` contains the pre-rotation Postgres
password. Rotated 2026-10-29, so it is inert; history is not being rewritten
over a dead credential.

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

## Recommendations surface

Undocumented until 2026-10-24; this is a substantial part of the app.

- `services/recommendations.py` — `RISK_PROFILES` (conservative, moderate,
  aggressive, spouse_favored) and `compute_recommendations()`, shared with
  the rules backtest so live advice and history stay consistent. Profiles
  pin `filer_types=['member']`, so cabinet data never leaks into them.
- `services/snapshot.py` — the daily 06:00 ET job precomputes one snapshot
  per profile into `recommendation_snapshots` (14 days retained). The API
  serves the cache, falling back to the freshest snapshot in the same
  window slot when the requested day isn't built yet, so a page load never
  pays the ~5.6s live recompute.
- `routers/recommendations.py` — `/`, `/profiles`, `/strategies`,
  `/cache`, `/refresh`, and `/freshness` (data-recency banner: last
  recorded trade, filing/price dates, ingest status).
- `routers/holdings.py` + `user_holdings` — per-user portfolio tracking,
  scoped by the verified CF Access email, with CSV bulk upload.
- Frontend: the **Recommendations** tab in `frontend/index.html` +
  `frontend/js/recommendations.js`.

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

Status: **v2 complete (null), v2.5 spouse-cut complete (suggestive).** See
`docs/decision_log.md` §3-§5 for the full narrative.

## Key files

```
backend/app/main.py            FastAPI app, router wiring, migrations + views applier
backend/app/models.py          SQLAlchemy models
backend/app/routers/games.py   public leaderboard API — mounted under /public_api/ (NOT gated by CF Access)
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
docs/international_disclosure_regimes.md  region-by-region survey of ~25 international parliamentary disclosure regimes; conclusion: US STOCK Act shape is essentially unique
docs/us_state_disclosure_regimes.md       50-state survey of legislator disclosure regimes; conclusion: same annual-snapshot problem as international, plus 50× engineering surface. (§8 documents that the originally-recommended congressional-staff PTRs alternative was investigated and ruled out — the 2013 S.716 amendment removed staff filings from public online disclosure.)
reports/                       generated HTML reports (report_runs<ids>_<date>.html)
docker-compose.yml  .env
```

## Current state

- Data: 2020–2026 House + Senate + executive (OGE 278-T) on the NUC —
  **19,809 transactions, 415 filers** (337 congressional, 78 executive).
  Local `db` container holds a backup copy.
- **Ingest is scheduled** (added 2026-10-24): `services/ingest.py` runs at
  04:00 ET, two hours ahead of the 06:00 ET snapshot job, and records its
  outcome to `app_settings['ingest.last_run']`. Before this, scraping only
  happened when someone clicked it in the Admin panel, and the data had
  gone 9 days stale unnoticed. `POST /api/scrape/daily` runs it on demand;
  `GET /api/scrape/status` reports the last run.
- **Senate eFD is closed to automated access** (since ~2026-10-07). The
  agreement POST returns an edge-level "Access Denied", so this is an
  access decision at the source, not a bug. House ingest is unaffected.
  Senate is 46% of congressional transactions, so the panel is
  increasingly House-only going forward — **check the chamber mix before
  calling a post-2026-10 result "congressional."** Working hypothesis is
  an overbroad CDN bot rule rather than policy: nothing published forbids
  automated access and nothing replaced it. Do not route around it; see
  `decision_log.md §6.6` for the full diagnosis and the outreach decided
  on instead.
- **Price coverage**: 93.8% of disclosed-ticker transactions price.
  `services/symbols.py` resolves disclosed strings to vendor symbols
  (BRK.B → BRK-B, spin-off and placeholder cells, non-ticker rejection);
  `tickers` carries fetch bookkeeping so delisted names aren't re-requested
  every run. A live recommendation recompute went 74s → 5.6s. 248 tickers
  remain unresolved — genuine 2025-26 acquisitions and renames that need a
  **sourced** corporate-action mapping (deliberately not guessed, since
  splicing price series across a corporate action fabricates returns).
- v1 backtest complete — null on the actionable (`filing_date`) anchor.
- v2 leadership work complete through step 3: bioguide_id backfilled,
  positions table populated (37 chamber leadership + 120 committee
  chairs/rankings = 157 tenures), sector data ingested, Bayesian
  hierarchical model fitted.
- `AnalysisRun` rows catalogued through #71 (rules-backtests + 2 Bayesian
  fits + the visibility cut). Note `analyze.py` does **not** persist a run —
  only `POST /api/model/run` does. Latest canonical report:
  `reports/report_runs1-2-3-4-5-6-7-8-9-10-11-12-13-14-15-16-17-18_2026-05-22.html`.
- **v2 finding:** Bayesian posterior on `leadership_at_trade` after
  controlling for member baseline, in-majority status, post-trifecta
  period, party, and sector: P(β > 0) = 0.58, 94% HDI [−2.1%, +2.5%].
  Not credibly distinguishable from zero. The rules-backtest "leadership
  edge" was largely the trifecta-period dummy (P=0.90) + sector
  composition (energy long, cyclicals/materials/industrials short)
  mis-attributed because the leadership filter overlaps the trifecta
  window in our data.
- **v2.5 finding (2026-05-23):** Stratifying by `owner` (filer vs.
  spouse/joint) — the strongest non-sector signal so far, but still not
  credible at 95%. Bayesian posterior on `is_spouse_owned` after the
  same controls: P(β > 0) = 0.892, mean +1.11%, HDI [−0.61%, +2.85%].
  Engine now supports `owner_filter ∈ (any, self, spouse_or_joint,
  spouse_only, joint_only, dependent)`. Operationalized as the
  `spouse_favored` recommendation profile. Also surfaced an important
  methodological gotcha: for clustered-entry universes, the
  rules-backtest NAV result and the per-trade event-study mean can
  disagree by 8+ percentage points (rules can be inflated by sequencing,
  not edge) — always report both.
- **v2.6 — cabinet / OGE 278-T data ingested (2026-05-23).** 318/318
  PAS-official filings scraped from `extapps2.oge.gov` via Playwright,
  parsed with a new 278-T variant of `ptr_parser.py`, written to
  `members.filer_type='executive'`. Universe: 78 unique cabinet /
  sub-cabinet filers, 5,033 transactions, 3,057 (60.7%) ticker-
  resolvable. Coverage 2020–2026. Engine now has a `filer_types`
  parameter; existing RISK_PROFILES pinned to `['member']` so the
  cabinet data lives alongside Congress but doesn't silently appear
  in live recommendation snapshots. **First-cut directional finding:**
  cabinet *purchases* underperform SPY in 2025 (−0.93%/trade, t=−1.32,
  n=348) where congressional purchases mildly outperform (+0.67%/trade,
  t=1.04, n=782). Sign-opposite from Congress for the first time in
  the project, though not significant yet. Formal AnalysisRun
  persistence + Bayesian re-fit with cabinet rows is §6.4 in the
  decision log, in progress. See [`docs/decision_log.md §6.3`](docs/decision_log.md) for
  the full ingest writeup.
- **⚠ Every run before #72 used a duplicated universe.** The dedupe in
  `decision_log.md §6.6` removed 9.0% of ticker-bearing rows (re-reported
  amendments and repeated line items). Duplicate rows inflate `n` without
  adding independent observations, so **point estimates are largely fine but
  every t-statistic, interval and posterior probability from runs #1–71 is
  too narrow.** The v2.5 `is_spouse_owned` posterior (P(β > 0) = 0.892) and
  the v2 leadership fit both need re-fitting on canonical rows — not yet
  done. See `§6.7`.
- **Visibility finding (2026-10-24, runs #69–71; superseded by #72–74):** new engine parameter
  `visibility ∈ (any, high, low)`, splitting names by point-in-time median
  daily dollar volume (p95 cut; market cap and turnover both fail — see
  `decision_log.md §6.5`). The hypothesis that obscure names carry more
  information is **backwards**: low-visibility buys are significantly
  negative across 2021–24. **On the deduplicated re-run (#72–74) that result
  does not survive**: −0.75%, t=−1.91, below the conventional threshold, so
  the "niche trades underperform" claim is retracted. High-visibility 2025
  OOS excess is +11.57% deduplicated, but it is *not* an edge — ten of 249
  trades carry ~70% of the mean (INTC, MSTR, NVDA, LLY, TSLA), it is a single
  test year, and "high visibility in 2025" nearly defines the AI/crypto
  momentum trade. Net read: **no tier shows a credible effect.** A momentum
  control in the Bayesian spec is the obvious next step.
- Known data gap: ~12% scanned/checkbox-grid filings parse poorly
  (deferred). Trump's 05/08/2026 (2) 278-T filing is OCR-garbled
  in its embedded text layer and would need re-OCR to recover its
  ~1,838 transactions (deferred).
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
(As of 2026-10-24 that directory is empty on this host; `decision_log.md`
is the authoritative narrative.)
