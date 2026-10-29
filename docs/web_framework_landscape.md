# Web framework landscape — reference

Reference material, **not project state** — nothing here describes how
congress_trader is built. Written 2026-10-29 to compare the options against
the FastAPI backend this project uses.

Knowledge is current to roughly May 2026. The lineup below is stable, but
check specific versions, benchmarks and maintenance status before betting on
any of them.

One framing note before the table, because it drives a lot of bad choices:
**a framework's CVE count measures how much scrutiny it gets, not how weak it
is.** Django's auth has absorbed twenty years of adversarial attention; a
hand-rolled equivalent has absorbed none. Picking an obscure stack moves you
from *known, patchable* risk to *unknown, unpatched* risk. Obscurity does
deflect untargeted bot scanning — a real but shallow benefit, and no
substitute for a working patch cadence.

## The options

| Framework (lang) | What it is | Pros | Cons | vs FastAPI |
|---|---|---|---|---|
| **FastAPI** (Python) | Async API framework on Starlette + Pydantic; derives validation and OpenAPI docs from type hints | Type hints do double duty as validation and docs; auto-generated interactive API docs; async; very fast to write; full Python data/ML ecosystem | No ORM, auth, admin or migrations — you assemble them; easy to block the event loop with a sync call; younger, so fewer hardened reference patterns; GIL limits CPU parallelism | — *(baseline)* |
| **Django** (Python) | Batteries-included MVC: ORM, migrations, auth, admin, templating | Everything included and hardened by two decades of attack; the auto-admin is a genuine superpower; superb docs; strong security defaults (CSRF, XSS, SQLi) | Heavy and opinionated; ORM fights you on complex queries; async retrofitted and still partial | Trades FastAPI's type-driven API ergonomics for free auth/admin/ORM. **Django Ninja** gives you FastAPI-style routing *inside* Django — often the best of both |
| **Flask** (Python) | Minimal WSGI microframework | Tiny, simple, extremely mature, huge extension ecosystem | Sync by default; no built-in validation or docs; you assemble everything yourself | FastAPI is roughly "Flask + types + async + docs". Little reason to start new on Flask unless you specifically want sync and minimal |
| **Litestar** (Python) | Newer async framework, FastAPI-shaped but more included | Built-in DI, ORM integration, often benchmarks faster, more structured out of the box | Much smaller community — fewer Stack Overflow answers when stuck | Direct competitor: more batteries, far less mindshare. Switching costs more than it returns unless you need its DI |
| **Starlette** (Python) | The ASGI toolkit FastAPI is built on | Very light; full async control; no Pydantic overhead | You write your own validation and docs | FastAPI minus the ergonomics. Worth it only if Pydantic is measurably in your way |
| **Go stdlib `net/http`** (+ Chi/Echo) | Standard-library HTTP server, optionally a thin router | Compiles to **one static binary with near-zero dependencies** — collapses supply-chain surface; true parallelism (no GIL); excellent performance; memory-safe; instant cold start | More boilerplate; no ORM culture (sqlc/sqlx instead); less "magic" | The strongest answer if *dependency surface* is your security concern — fewer things that can be vulnerable, not just less popular. You lose Pydantic ergonomics and the Python data stack |
| **Axum / Actix-web** (Rust) | Async Rust web frameworks | Memory safety without GC; top-tier performance; compiler catches whole bug classes | Steep curve; async Rust is genuinely hard; long compiles; smaller talent pool | Far more performance and safety, far slower to write. Overkill unless performance or safety *is* the product |
| **Express** (Node/TS) | The long-standing minimal JS framework | Ubiquitous; vast middleware ecosystem; trivial to start | Callback-era design; untyped by default; npm dependency sprawl is a real supply-chain surface | Similar minimalism, weaker defaults. FastAPI's validation and auto-docs are a clear win |
| **Fastify** (Node/TS) | Faster, schema-first Express alternative | JSON-Schema validation built in; good TS support; notably quick | Smaller ecosystem than Express; still npm supply chain | The closest JS analogue to FastAPI — schema validation plus speed |
| **NestJS** (Node/TS) | Opinionated, Angular-inspired, DI-heavy | Real structure at scale; TypeScript-first; batteries included | Heavy abstraction; lots of boilerplate; steep curve | More structure and more ceremony. Pays off on large teams, costs on solo projects |
| **Hono** (TS) | Ultralight, edge-first (Workers, Deno, Bun) | Tiny; runs on edge runtimes; very fast | Young; edge constraints — no long-running processes, limited native deps | Great for edge/serverless APIs. **Cannot host pandas/PyMC-style work**, which rules it out for analysis backends |
| **Next.js** (TS/React) | Full-stack React framework | Frontend and backend together; SSR; huge ecosystem; strong DX for UIs | Heavy; opinionated; churns fast; the backend half is secondary | Different category — it would replace a *frontend*, not an analysis backend |
| **Phoenix** (Elixir) | BEAM-VM framework; LiveView gives realtime UI without writing JS | Exceptional concurrency and fault tolerance; LiveView is genuinely novel; great for realtime | Small talent pool; unfamiliar paradigm; weak numeric/ML ecosystem | Much better at realtime and concurrent connections, much worse for data science |
| **Rails** (Ruby) | The original batteries-included MVC | Extremely productive for CRUD; mature; strong conventions | Slower runtime; shrinking ecosystem; less API-first than it was | Faster to build a CRUD app, slower to run, weaker for APIs and data work |
| **ASP.NET Core** (C#) | Microsoft's modern cross-platform stack | Genuinely excellent performance; strong typing; first-class tooling and LTS | Microsoft ecosystem gravity; heavier runtime; smaller OSS web mindshare | Substantially faster with better enterprise tooling; less nimble, and no Python data stack |
| **Spring Boot** (Java/Kotlin) | The enterprise standard | Vast ecosystem; extremely battle-tested; every integration exists | Verbose; heavy; slow startup; configuration sprawl | Heavyweight by comparison. Chosen for organisational reasons more than technical ones |
| **Laravel / Symfony** (PHP) | Modern PHP MVC frameworks | Very productive; mature; excellent docs; hosting available everywhere | PHP's install base makes it the single biggest scanning target on the internet; classic per-request model (FrankenPHP/Swoole change this) | Comparable productivity for web apps. **Modern PHP 8 is nothing like PHP 5** — the reputation lags the language by a decade |

## How to actually choose

In rough priority order:

1. **What language is the rest of the work in?** This usually decides it. An
   analysis backend sitting on pandas, numpy and PyMC has effectively already
   chosen Python — a Go rewrite would mean reimplementing or shelling out to
   the entire scientific stack.
2. **Do you need batteries or a toolkit?** Auth, admin and an ORM you don't
   have to write are worth a lot. Django and Rails hand you those; FastAPI and
   Go hand you a clean slate.
3. **What's the concurrency shape?** Mostly I/O waiting on a database →
   anything async is fine. Thousands of live connections → Phoenix or Go. Heavy
   CPU → something without a GIL.
4. **How big is the dependency tree?** This is the security-relevant question,
   and it cuts the opposite way from obscurity: Go's near-empty `go.mod` is a
   smaller surface than any npm tree, regardless of how popular either is.
5. **Who maintains it at 3am?** A stack nobody else knows is a liability that
   compounds.

## When to build your own

For the web layer: **effectively never**, and least of all for security
reasons. Custom code has more bugs, not fewer — the difference is that nobody
is looking for yours.

It becomes defensible when:

- Requirements are genuinely unusual and the framework fights you constantly
- You need control a framework abstracts away (custom protocol, hard latency
  budget)
- The dependency tree *itself* is the risk
- You're learning, deliberately

There's a good middle ground worth naming: **using a language's standard
library instead of a framework.** Go's `net/http` is "rolling your own" in
the sense of having no framework, but on a foundation hammered by
Google-scale traffic. Small surface, without owning the hard parts.

The rule that generalises: don't write your own crypto, auth, session
handling or parsers. Not a competence question — correctness in those areas
is established by sustained attack, which one person cannot reproduce.

## What this would mean for congress_trader

Staying on FastAPI is right, for a reason that has nothing to do with the
framework: the backend is pandas, numpy, yfinance and PyMC. The web layer is
a thin shell around a Python analysis engine, so the language was chosen by
the analysis, not the API.

The realistic improvements are inside the current stack:

- **Django Ninja** would add a hardened admin and auth if the app ever grows
  real user accounts — today Cloudflare Access does that job.
- **A smaller dependency tree** is the security lever that would actually
  matter, not a language change.
- What protects this deployment is not the framework: Cloudflare Access with
  an email allowlist, a tiny public surface (`/public_api/*` behind three
  layers of rate limiting, plus `/spacex`), nginx serving static files rather
  than executing anything, and — the one that actually failed — patch cadence.
