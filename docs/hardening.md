# Congress Trader — Site Hardening

**Status (2026-06-23):** public leaderboard API shipped — `/public_api/*`
prefix, per-game posting key, three-layer nginx rate limiting.
**Scope:** how `goosebase.org` separates its authenticated surface from the
deliberately-public surface, and the abuse controls on the public side.

---

## 1. The access model

Everything on `goosebase.org` is fronted by **Cloudflare Access** (email-OTP).
The Access application gates these path patterns at the CF edge:

```
/api/*
/trader*
/splash-admin*
```

Gating happens **at the edge** — a request without a valid CF Access JWT never
reaches the NUC. The backend itself does not reject unauthenticated requests
for most routes; it only reads the verified JWT (`Cf-Access-Jwt-Assertion`) for
*per-user data scoping* in `holdings` / `me` (see `backend/app/auth.py`). In
other words, the privacy of `/api/*` is provided entirely by the CF Access
policy, not by the application.

This has been great for keeping the trader UI and admin tooling private. The
problem it created: the goose-snake arcade leaderboard needs to be **writable
by the public** (anyone playing the game), but it lives in the same Postgres
DB behind the same gate.

## 2. The decision: a separate public prefix, not a CF bypass

There were two ways to expose the leaderboard:

| Option | Shape | Risk |
|---|---|---|
| CF Access **Bypass** policy on `/api/games/*` | Allow-list an exception *inside* the gated surface | Default-allow-then-poke-a-hole. Bypass rules are order-sensitive; one misordered or too-broad rule leaks a sensitive `/api/*` route. |
| **Separate `/public_api/*` prefix** (chosen) | A path CF Access never matches | Default-deny by path. Only what we deliberately mount under `/public_api` is public; the entire `/api/*` surface stays gated with no chance of accidental leakage. |

We chose the separate prefix. It is a smaller blast radius and a clearer mental
model: **`/api/*` = private by default; `/public_api/*` = the explicit public
allow-list.**

> **Cloudflare caveat.** This relies on the Access application matching
> *specific path patterns* (`/api/*`, `/trader*`, `/splash-admin*`) rather than
> the whole `goosebase.org` hostname. If the app ever gates the whole host with
> per-path bypasses instead, a new `/public_api/*` path would be gated too and
> would need its own Bypass policy (same pattern as the existing `/spacex/*`
> public module). Confirm in Zero Trust → Access → Applications.

## 3. Implementation

### 3.1 Backend — public mount + per-game posting key

- **Prefix.** `settings.public_api_prefix` (default `/public_api`) is defined in
  `backend/app/config.py`. In `backend/app/main.py` the `games` router is
  mounted under it — *outside* `api_prefix` — so it is not covered by the CF
  Access gate:

  ```python
  app.include_router(games.router, prefix=settings.public_api_prefix)
  ```

- **Reads are open, writes require a key.** `GET /public_api/games/leaderboard`
  takes no key (reading a public scoreboard is harmless).
  `POST /public_api/games/leaderboard` requires an `X-Game-Key` header.
  `_require_post_key()` in `backend/app/routers/games.py`:
  - looks the key up **by game slug** in the registry, so a key issued for one
    game can't write to another's board;
  - `403` if the slug has no registered key (the game isn't authorized);
  - `401` if the key is missing or wrong;
  - uses `secrets.compare_digest` (constant-time) to avoid leaking the key via
    response timing.

- **The key registry doubles as the slug allow-list.** Because an unregistered
  slug has no key, it is rejected — which closes the *slug-cardinality* abuse
  vector (an attacker spamming thousands of distinct `game` slugs, each keeping
  its top-N rows, to grow the table unbounded). No separate allow-list needed.

- **Existing input hardening (already in the router).** Names are sanitized to
  uppercase alnum ≤ 5 chars with a profanity/leet-fold block; scores are
  range-checked (`0 .. 100_000_000`); the board is trimmed to the top
  `MAX_ENTRIES` per game on every write so the table can't grow unbounded.

### 3.2 Edge — nginx rate limiting

`frontend/nginx.conf` throttles `/public_api/` three ways. The CF Access gate
used to be the de-facto throttle on `/api/*`; the public surface needs its own.

1. **Per-IP, split by method.** Reads happen on every game-over and are cheap,
   so GET is generous; writes are rare and the thing worth limiting, so POST is
   strict. A `map` on `$request_method` yields the client IP for the targeted
   method and an empty string otherwise — and `limit_req` ignores empty keys, so
   each zone counts only its own method.
2. **Global rate budget (queues excess).** One shared bucket across all clients
   and both methods. Excess is **queued** — `limit_req` *without* `nodelay`
   delays requests cheaply in nginx's event loop and releases them at the
   configured rate, so the Python backend / Postgres only ever see the throttled
   rate. This is the "total NUC budget" — it bounds aggregate load even under a
   distributed flood (many IPs each under the per-IP cap).
3. **Global concurrency ceiling.** `limit_conn` caps simultaneous in-flight
   requests. Unlike the rate budget it **rejects** (does not queue) past the
   ceiling — a safety net against slow requests piling up.

Rejections return **429** (`limit_req_status` / `limit_conn_status`) instead of
the default 503. The game treats any error as "global board unavailable" and
falls back to its local-cache leaderboard, so players still see a board.

## 4. Configuration reference

### Backend — `.env`

```ini
# Per-game leaderboard posting keys. Comma-separated "slug:key" pairs.
# A game must send its key in the X-Game-Key header to POST a score.
# NOT a secret — it ships in client-side game code (see §5). Must match the
# key baked into each game's client.
GAME_POST_KEYS=gooooose:dc8b097b042a263d1a3b2905a06bfdc4
```

`public_api_prefix` defaults to `/public_api` in `config.py` and normally needs
no override.

### Edge — nginx `/public_api/` limits

| Layer | Directive | Setting |
|---|---|---|
| Per-IP GET | `limit_req zone=public_get`  | rate **30 r/m**, burst 20, `nodelay` |
| Per-IP POST | `limit_req zone=public_post` | rate **6 r/m**, burst 5, `nodelay` |
| Global rate (queues) | `limit_req zone=public_api_all` | rate **5 r/s**, burst 40 (no `nodelay`) |
| Global concurrency | `limit_conn public_api_conn` | **12** simultaneous |
| Reject status | `limit_req_status` / `limit_conn_status` | **429** |

`nodelay` on the per-IP zones lets a normal play session's burst through
instantly, then the sustained per-IP rate applies. The global zones sit on top
of both methods. All values are tunable in `frontend/nginx.conf` — the global
budget and concurrency cap are sized so legitimate traffic from a handful of
players never approaches them; they only engage under an actual flood.

## 5. Threat model & limitations

- **The posting key is not a secret.** It ships in the game's client-side
  JavaScript and is visible via View Source / the Network tab. It provides
  *authorization of known games* and stops casual/drive-by writes — it is **not
  authentication and not anti-cheat**.
- **Scores are not tamper-proof.** A determined person can read the key and
  `POST` a forged score. For a client-scored game this is fundamentally
  unsolvable without simulating the game server-side, which is not worth it for
  an arcade board. Do not treat the leaderboard as trustworthy data.
- **What the controls *do* protect:** unbounded DB growth (slug allow-list +
  per-game trim + global rate budget), profanity/garbage names, out-of-range
  scores, and casual/bot/flood abuse (per-IP + global rate + concurrency caps).

## 6. Adding a future game to the public leaderboard

1. Mint a key: `python -c "import secrets; print(secrets.token_hex(16))"`.
2. Append `,<slug>:<key>` to `GAME_POST_KEYS` in the NUC `.env`; restart the
   backend.
3. Embed the same key in the game client and send it as `X-Game-Key` on POST,
   targeting `/public_api/games/leaderboard` with `game=<slug>`.

No schema, router, or nginx change is needed — the single `leaderboard_scores`
table namespaces by slug, and the `/public_api/` rate limits already cover all
games sharing the prefix.

## 7. Deploying

```bash
# On the NUC, in ~/congress_trader/
#   1. Add the GAME_POST_KEYS line to .env (see §4).
#   2. Backend code + nginx.conf changed, so rebuild both:
docker compose up -d --build backend frontend
#   3. The leaderboard_scores table is created by Base.metadata.create_all
#      on backend startup — no migration step needed.
```

The goose-snake client lives in the sibling `goose_snake/` repo (bind-mounted);
its changes go live with a plain `git pull`, no rebuild.
