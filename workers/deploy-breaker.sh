#!/usr/bin/env bash
# One-shot deploy for the R2 circuit-breaker Worker (workers/r2-breaker.js).
# Mirrors deploy-bugs.sh. Headless: no `wrangler login`, needs
#   CLOUDFLARE_API_TOKEN  Workers Scripts:Edit + Workers Routes:Edit (+ D1
#                         query via REST for the schema fallback)
#   GH_PUSH_TOKEN         durable classic PAT, repo scope (issues RW)
# Optional:
#   CF_GRAPHQL_TOKEN      scoped Account Analytics:Read token for the
#                         GraphQL read; falls back to CLOUDFLARE_API_TOKEN
#                         with a loud warning (that token can deploy
#                         Workers, which a read-only breaker should not hold)
#   BREAKER_ADMIN_KEY     reuse an existing admin key (the boxes hold it as
#                         TAT_BREAKER_ADMIN_KEY in /root/tsr-s2/.env); when
#                         unset a NEW key is generated and printed ONCE, and
#                         the box .env must be updated to match.
#
# Rerunning is safe. Steps: apply the D1 schema (idempotent), deploy, wire
# secrets, smoke /status. The first cron tick fires within 5 minutes; until
# then /status reports last_tick=null (heartbeat_age_s=null).
set -euo pipefail
cd "$(dirname "$0")"

: "${CLOUDFLARE_API_TOKEN:?export CLOUDFLARE_API_TOKEN first (Workers Scripts:Edit + Routes:Edit)}"
: "${GH_PUSH_TOKEN:?export GH_PUSH_TOKEN first (durable classic PAT, repo scope)}"

ACCOUNT_TAG="33bb26c164250e1893f2ca61d293d44d"
D1_NAME="tat-breaker"
D1_UUID="b071cd4b-ac24-4692-ab14-977ba051b99d"
BASE="https://triple-a-tropics.com/r2-breaker"

echo "== apply D1 schema (idempotent) =="
if npx wrangler d1 execute "$D1_NAME" --remote --file r2-breaker.sql -c r2-breaker.toml; then
  echo "  schema applied via wrangler"
else
  # The Codespace token can query D1 through REST even where wrangler's
  # D1 subcommand cannot (verified 2026-08-25). One statement per request:
  # the query endpoint accepts a single SQL string with ';'-separated
  # statements, but splitting keeps a failure attributable.
  echo "  wrangler d1 execute failed; falling back to the REST query endpoint"
  python3 - "$ACCOUNT_TAG" "$D1_UUID" r2-breaker.sql <<'PY'
import json, os, sys, urllib.request
acct, uuid, path = sys.argv[1:4]
tok = os.environ["CLOUDFLARE_API_TOKEN"]
sql = "\n".join(l for l in open(path).read().splitlines() if not l.strip().startswith("--"))
stmts = [s.strip() for s in sql.split(";") if s.strip()]
url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/d1/database/{uuid}/query"
for s in stmts:
    req = urllib.request.Request(url, data=json.dumps({"sql": s}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        doc = json.load(r)
    if not doc.get("success"):
        sys.exit(f"D1 REST query failed: {doc.get('errors')}\n  stmt: {s}")
    print(f"  ok: {s[:60]}...")
PY
fi

echo "== deploy worker =="
npx wrangler deploy -c r2-breaker.toml

echo "== wire secrets (values never echoed except the admin key you must keep) =="
if [ -n "${CF_GRAPHQL_TOKEN:-}" ]; then
  printf '%s' "$CF_GRAPHQL_TOKEN" | npx wrangler secret put CF_GRAPHQL_TOKEN -c r2-breaker.toml
else
  echo "  WARNING: CF_GRAPHQL_TOKEN not set; using CLOUDFLARE_API_TOKEN for the"
  echo "  GraphQL read. Replace it with a scoped Account Analytics:Read token"
  echo "  (dash -> API Tokens -> Account Analytics:Read) and rerun:"
  echo "    printf '%s' \"\$TOKEN\" | npx wrangler secret put CF_GRAPHQL_TOKEN -c workers/r2-breaker.toml"
  printf '%s' "$CLOUDFLARE_API_TOKEN" | npx wrangler secret put CF_GRAPHQL_TOKEN -c r2-breaker.toml
fi
printf '%s' "$GH_PUSH_TOKEN" | npx wrangler secret put GITHUB_TOKEN -c r2-breaker.toml

if [ -n "${BREAKER_ADMIN_KEY:-}" ]; then
  ADMIN="$BREAKER_ADMIN_KEY"
  echo "  reusing BREAKER_ADMIN_KEY from the environment (not rotated)"
else
  ADMIN=$(openssl rand -hex 16)
  echo "  generated a NEW admin key (BREAKER_ADMIN_KEY was not set): the boxes'"
  echo "  /root/tsr-s2/.env TAT_BREAKER_ADMIN_KEY must be updated to match"
fi
printf '%s' "$ADMIN" | npx wrangler secret put ADMIN_KEY -c r2-breaker.toml

echo
echo "================================================================"
echo " BREAKER ADMIN KEY: $ADMIN"
echo " Put it in /root/tsr-s2/.env on box1 as TAT_BREAKER_ADMIN_KEY (for"
echo " scripts/fleet.sh breaker arm|disarm|trip|reset). Printed ONCE, and"
echo " printed BEFORE the smoke test so a smoke failure can never lose it."
echo " Mode is ALERT-ONLY until: fleet.sh breaker arm"
echo "================================================================"
echo

echo "== smoke: GET $BASE/status =="
# each `secret put` deploys a new Worker version; edge propagation can take
# ~30 s during which the route may 404/5xx -> retry
STATUS=""
for i in $(seq 1 12); do
  STATUS=$(curl -sf -m 15 "$BASE/status") && break
  echo "  /status not ready yet (attempt $i/12), waiting 10 s..."
  sleep 10
done
[ -n "$STATUS" ] || { echo "smoke GET /status never succeeded"; exit 1; }
# The parser is a quoted heredoc (no bash escaping inside Python), fed the
# document through the environment; tests/test_r2_breaker.py executes this
# exact block against a sample document, so a syntax slip fails CI, not the
# deploy. (A `python3 -c '...'` with escaped quotes inside an f-string was
# a SyntaxError on every deploy, after the secrets were already wired.)
STATUS_JSON="$STATUS" python3 - <<'PY'
import json, os, sys
d = json.loads(os.environ["STATUS_JSON"])
if d.get("v") != 1:
    sys.exit("smoke: unexpected /status document: %s" % json.dumps(d)[:300])
print("  v=%s mode=%s writes_enabled=%s last_tick=%s warn=%s trip=%s" % (
    d.get("v"), d.get("mode"), d.get("writes_enabled"), d.get("last_tick"),
    d.get("warn_hourly"), d.get("trip_hourly")))
PY
echo "  admin verbs: 403 without the key ->" \
  "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/arm")"
echo "smoke OK (the admin key is in the banner above)"
