# SOPS adoption — scope

Scoping document, written 2026-10-29. Nothing here is implemented yet.

## Why, honestly

The failure this is meant to prevent already happened twice: the Postgres
password was committed in `docker-compose.nuc.yml` (removed 2026-10-29, still
in git history, since rotated), and the cloudflared tunnel token sat inline in
a mode-0644 systemd unit.

But be clear about what SOPS does and doesn't fix. It would **not** have
stopped either of those — both were secrets typed into tracked files in
plaintext, and nothing prevents that except not doing it. What SOPS provides
is a *safe place to put secrets in tracked files*, so the right habit stops
being inconvenient.

The concrete, immediate win is **the two-machine sync problem**. Rotating the
database password on 2026-10-29 required manually copying it to the dev box's
`.env`, out of band, or `dev-on.ps1` breaks. With SOPS that becomes `git pull`.
That is the thing worth the setup cost; the audit-trail and
safe-in-git properties are a bonus.

## What is actually secret

`.env` currently mixes secrets with ordinary config:

| key | classification | notes |
|---|---|---|
| `DATABASE_URL` | **secret** | holds the rotated Postgres password, percent-encoded |
| `CF_ACCESS_AUD` | **secret** | Cloudflare Access application audience tag |
| `POSTGRES_PASSWORD` | secret, unused | only the local `db` fallback container reads it |
| `GAME_POST_KEYS` | low | documented as *not* secret — it ships in client-side JS |
| `COMPOSE_FILE` | **config, load-bearing** | see the constraint below |
| `POSTGRES_USER`, `POSTGRES_DB`, `DB_PORT`, `CF_ACCESS_TEAM_DOMAIN`, `CF_USER_DISPLAY_NAMES` | config | no value in encrypting |

So the real payload is two values, three if `POSTGRES_PASSWORD` is kept.

## The constraint that shapes the design

`COMPOSE_FILE=docker-compose.yml:docker-compose.nuc.yml` lives in `.env`, and
it is what makes the NUC overlay apply. It has to be readable by
`docker compose` **before** anything else runs.

That matters because of the bug fixed on 2026-10-29 (`decision_log.md §8`):
when the overlay is missing, compose silently falls back to the base config —
the backend loses `extra_hosts` and reaches Postgres by LAN IP instead of the
host gateway. It keeps working, which is exactly why nobody noticed for five
months.

**Encrypting the whole `.env` would reintroduce that failure mode.** Any
`docker compose` run without a prior decrypt — and there are many in normal
use — would find no `.env`, drop the overlay, and carry on quietly.

## Design: split, don't encrypt wholesale

| file | tracked? | contents |
|---|---|---|
| `.env` | gitignored (unchanged) | `COMPOSE_FILE` + non-secret config only |
| `secrets.env.sops` | **committed, encrypted** | `DATABASE_URL`, `CF_ACCESS_AUD`, `POSTGRES_PASSWORD` |
| `.sops.yaml` | committed, plaintext | which age public keys may decrypt |

`deploy.sh` wraps compose rather than writing a decrypted file to disk:

```bash
sops exec-env secrets.env.sops 'docker compose up -d --build "$@"'
```

`sops exec-env` injects into the process environment, which is where compose
reads `${VAR}` interpolation from. Plaintext secrets never touch the
filesystem, and `COMPOSE_FILE` is still read from a plain `.env`, so the
overlay cannot silently vanish.

Failure mode if someone runs bare `docker compose up` without sops: the secret
variables are empty, compose warns, and the backend fails to start with an
authentication error. **Loud, not silent** — which is the point.

## Phases

**1 — Prerequisites.** Install `sops` and `age` on the NUC and the dev box
(both ship standalone binaries; no package manager or Node required).
Generate one age keypair per machine. Store both private keys in Bitwarden —
**if every private key is lost, the encrypted files are unrecoverable**, and
that is the main new risk this introduces. Commit `.sops.yaml` listing both
public keys.

**2 — Migrate.** Split `.env`, create and encrypt `secrets.env.sops`, verify
decryption independently on both machines *before* deleting anything.

**3 — Integrate.** Update `scripts/deploy.sh`. Update `scripts/dev-on.ps1` —
the dev box is Windows, so this needs the Windows `sops` binary and a
PowerShell equivalent of `exec-env`, which is the least certain part of this
plan. Document the workflow in `CLAUDE.md` beside the existing deploy notes.

**4 — Optional, probably skip.** The cloudflared tokens
(`/etc/cloudflared/*`) are outside the repo, owned by systemd, already
0600, and never in git. Bringing them under SOPS adds moving parts for
little benefit. The one genuine improvement there is unrelated: switching
`cloudflared-mysecretmenu.service` to `--token-file`, as goosebase's unit
already does.

## What this does not solve

- **The old password in git history.** Two commits still contain it.
  Rotation on 2026-10-29 made it worthless; history rewriting is not worth it.
- **Secrets on a compromised host.** Anything that gets root reads the age
  key and decrypts everything. SOPS protects secrets *at rest in git*, not on
  a live machine.
- **The discipline problem.** Nothing stops someone pasting a password into a
  tracked YAML file. A pre-commit secret scanner (gitleaks, trufflehog) would
  address that, and is a separate, arguably higher-value change.

## Effort and recommendation

Phases 1–3 are roughly an hour on the NUC, plus however long the Windows side
takes — that is the unknown. Worth doing, but it is a convenience and hygiene
improvement, not a fix for an active exposure. Nothing is currently leaking.

If only one thing gets done, a **pre-commit secret scanner** prevents the
class of mistake that actually occurred here, at a fraction of the setup cost.
SOPS is the better end state; the scanner is the better first step.
