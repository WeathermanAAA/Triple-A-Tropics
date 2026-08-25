# Cloudflare Workers (vendored sources)

Worker code that runs on the Cloudflare zone for `triple-a-tropics.com`
lives HERE, in-repo, and the deployed copy must match this directory.
(Lesson from the deck-fetch investigation: the ATCF proxy Worker's
behavior was unauditable because its source lived only in the CF
dashboard. New Workers are vendored; the proxy should be back-vendored
here when next touched.)

## cyclolab-router.js

Routes `triple-a-tropics.com/cyclolab/*` to R2 objects under the
`cyclolab/` prefix (CYCLOLAB_DESIGN.md §3.1). GET/HEAD only, no secrets,
serves stored Content-Type, branded HTML 404, no SPA rewriting.

### Deploy (user action — needs the CF account, ~5 minutes)

```bash
npm i -g wrangler            # or use npx wrangler
cd workers
wrangler login
wrangler deploy cyclolab-router.js --name cyclolab-router \
  --compatibility-date 2026-06-01
```

`wrangler.toml` (place next to the worker, or pass flags):

```toml
name = "cyclolab-router"
main = "cyclolab-router.js"
compatibility_date = "2026-06-01"

routes = [
  { pattern = "triple-a-tropics.com/cyclolab/*", zone_name = "triple-a-tropics.com" }
]

[[r2_buckets]]
binding = "BUCKET"
bucket_name = "triple-a-tropics-media"   # the bucket cdn.triple-a-tropics.com fronts
```

### Stage-0 gate check (run after deploy)

```bash
# 1. PUT a hand test page (any R2-writing service, or dashboard upload)
#    at key: cyclolab/test/index.html  with Content-Type: text/html
# 2. Then:
curl -sI https://triple-a-tropics.com/cyclolab/test/ | head -5
#    expect: HTTP/2 200  +  content-type: text/html
curl -sI https://triple-a-tropics.com/cyclolab/nope/ | head -3
#    expect: HTTP/2 404  +  content-type: text/html (branded page)
curl -sI https://triple-a-tropics.com/cyclolab/test | head -3
#    expect: HTTP/2 301 -> /cyclolab/test/
```

GitHub Pages is untouched: Pages never sees `/cyclolab/*` because the CF
route intercepts at the edge (the zone already proxies the apex).

## explorer-gate.js

LINK-ONLY preview gate for `/satellite/explorer/*` (incl. compare.html):
serves only with the secret preview token (`?k=<token>` sets a 90-day scoped
cookie; otherwise the site's REAL 404). Fail-closed if the secret is unset.
Un-gate for public launch = delete the Worker route (dashboard, or
`npx wrangler delete --name explorer-gate`) — the pages are already on Pages.

### Status: PARKED, NOT DEPLOYED (Andrew, 2026-07-09)

The gate was dropped before ever deploying: `/satellite/explorer/*` stays
live-but-unlinked + noindex/robots as its intended dev-preview state. The
worker + toml stay vendored here in case a launch-time gate is ever wanted.

### Deploy (if ever revived — headless, needs `CLOUDFLARE_API_TOKEN` in env)

Wrangler picks up a `CLOUDFLARE_API_TOKEN` env var natively (no
`wrangler login`). The preview token is generated fresh at deploy
time and lives ONLY in the Worker secret — never in this repo (the repo
is public). Rotation = rerun these two commands with a new value.

```bash
cd workers
npx wrangler deploy -c explorer-gate.toml
openssl rand -hex 16 | npx wrangler secret put PREVIEW_TOKEN -c explorer-gate.toml
```

Deploy-then-secret is safe: the Worker fails closed (404s everything)
while `PREVIEW_TOKEN` is unset.

### Gate check (run after deploy)

```bash
curl -sI https://triple-a-tropics.com/satellite/explorer/ | head -3
#   expect: HTTP/2 404 (no token -> looks nonexistent)
curl -sI "https://triple-a-tropics.com/satellite/explorer/?k=<TOKEN>" | head -5
#   expect: HTTP/2 302 + set-cookie tat_explorer_preview=... -> clean URL
#   then the cookie-carrying browser gets 200s on the page + assets
```

## bugs-api.js

`triple-a-tropics.com/bugs-api/*` — GitHub-issues-backed tester bug board
API for the nav-hidden `/bugs/` page. Testers submit anonymously; the
Worker holds a durable GitHub PAT server-side and files reports as
`tester-report`-labeled issues, so `fixes #N` in a commit crosses them off
the board. Anti-spam: honeypot + per-IP/day rate limit (no passcode)
(GitHub itself is the counter via an invisible HMAC ratekey tag — no KV).

Deploy (one-time; rotates the admin key on rerun):

```bash
npx wrangler login          # browser OAuth, once per machine
bash workers/deploy-bugs.sh # deploys, wires secrets, smoke-tests the loop
```

Local E2E without Cloudflare auth: `wrangler dev -c bugs-api.toml --local`
with a `.dev.vars` (gitignored) pointing `GH_BASE` at a mock GitHub —
the full suite (validation, honeypot, rate limit, PATCH guard)
was run that way at build time; real-PAT issue create/label/close was
verified separately via `gh`.

## r2-breaker.js

`triple-a-tropics.com/r2-breaker/*` (also `r2-breaker.<acct>.workers.dev`)
— the R2 Class-A cost circuit breaker (PHASE0_SPEC, 2026-08-25). A cron
(every 5 min, 2 min past each boundary: `2,7,...,57 * * * *`, because a
bucket read on its own boundary is only ~70% ingested) reads the account's
successful Class-A R2 operations (`ListObjects`, `PutObject`, multipart,
copy) from the Cloudflare GraphQL analytics API in 5-minute buckets, drops
the partial newest bucket, and judges `rate_1h` (12 complete buckets) and
`pace_15m` (3 buckets x 4)
against `WARN_HOURLY` (80K) / `TRIP_HOURLY` (150K; fast path at 2x trip on
the 15-min pace). Two consecutive "over" ticks open an **episode**: one
GitHub issue (label `breaker`) per episode, a comment with peak + duration
when the rate has been below warn for 30 minutes. State lives in the D1
database `tat-breaker` (binding `DB`; tables `state`, `ticks`, `events`;
schema in `r2-breaker.sql`, idempotent, also self-healed by the Worker).

Modes: **alert** (default; opens "WOULD HAVE TRIPPED" issues, never stops
anything) and **armed** (a trip sets `writes_enabled=false`, which the box
heartbeat mirrors into `fleet/breaker.json` and `tat_killswitch.py` honours
inside every writer). `writes_enabled` stays false until `POST /reset`; no
auto-resume, ever. Arming during an already-active episode (the natural
response to a "WOULD HAVE TRIPPED" issue) trips at once when the latest
reading is a fresh (< 10 min) sustained over, otherwise on the next
sustained over tick; either way the trip is a comment on the episode's
existing issue and the `/arm` response says which happened. Fail-open
everywhere: an analytics read failure never changes `writes_enabled` and
never resets `consecutive_over` (issue after 3 consecutive failures,
comment on recovery); an EMPTY analytics result counts as a read failure
(this account never idles for two hours, so zero rows is lag, not zero
load); a missing `GITHUB_TOKEN` only skips the alert; a missing
`ADMIN_KEY` makes the admin verbs 503.

Liveness (the breaker must prove it is alive): every tick stamps
`last_tick`; a gap > 20 min between ticks bumps `gaps_detected` and opens
an issue. External legs: `.github/workflows/breaker-liveness.yml` (half
hourly, red run when `/status` is unreachable, `heartbeat_age_s > 1200`,
`consecutive_errors >= 3` or `v != 1`), the box heartbeat mirror
(`breaker_age_s`), and the card at the top of `/fleet/`.

Endpoints: `GET /status` (contract v1, CORS `*`, `no-store`),
`GET /ticks?n=48` (max 288), `GET /events?n=50`; `POST /arm`, `/disarm`,
`/trip`, `/reset` with header `X-Admin-Key` (403 otherwise, 503 if unset).
`/disarm` does NOT re-enable writes; `/reset` is the only path back. A
reset clears `consecutive_over` and, for the next hour, only R2 ops in
buckets that start after the reset are counted (the trailing 60-min window
otherwise still holds the storm the trip stopped and would re-trip an
armed breaker within 5 min of every reset): `pace_15m` recovers within 15
min, `rate_1h` refills over the hour, and a storm that is still running
re-trips as soon as the post-reset buckets alone cross a threshold.

Deploy (headless; `CLOUDFLARE_API_TOKEN` + `GH_PUSH_TOKEN` in env):

```bash
bash workers/deploy-breaker.sh          # schema -> deploy -> secrets -> smoke
# optional env: CF_GRAPHQL_TOKEN (scoped Account Analytics:Read; falls back
# to CLOUDFLARE_API_TOKEN with a warning), BREAKER_ADMIN_KEY (reuse an
# existing key instead of generating a new one; the boxes hold it as
# TAT_BREAKER_ADMIN_KEY in /root/tsr-s2/.env)
```

Tests: `python -m unittest tests.test_r2_breaker` drives
`tests/r2_breaker_harness.cjs` (node; fake D1 + stubbed fetch, clock
controlled) and pins the toml/sql/workflow/fleet-card wiring. Budget: 288
cron invocations/day on the free plan; the boxes never poll the Worker; the
fleet card polls at the tick cadence (5 min) and never from a hidden tab.
