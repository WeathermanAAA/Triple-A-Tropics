#!/usr/bin/env bash
# One-shot deploy for the tester bug-board Worker (workers/bugs-api.js).
#
# Prereqs: `npx wrangler login` done once on this machine (browser OAuth),
# and GH_PUSH_TOKEN in the env (the durable classic PAT the Codespace
# already uses — repo scope covers Issues RW; verified 2026-07-13).
#
# Rerunning is safe; it ROTATES the admin key. (No tester passcode —
# testers submit freely; honeypot + per-IP/day rate limit are the gate.)
set -euo pipefail
cd "$(dirname "$0")"

: "${GH_PUSH_TOKEN:?export GH_PUSH_TOKEN first (durable classic PAT)}"

echo "== deploy worker =="
# auth: either a prior `npx wrangler login` OR CLOUDFLARE_API_TOKEN in env
# (needs Account->Workers Scripts:Edit + Zone->Workers Routes:Edit)
npx wrangler deploy -c bugs-api.toml

echo "== wire secrets (values never echoed except the admin key you must keep) =="
printf '%s' "$GH_PUSH_TOKEN" | npx wrangler secret put GITHUB_TOKEN -c bugs-api.toml

ADMIN=$(openssl rand -hex 16)
printf '%s' "$ADMIN" | npx wrangler secret put ADMIN_KEY -c bugs-api.toml
# (TESTER_PASSCODE secret retired + deleted 2026-07-13; verified absent)

echo "== smoke test (files + closes one nit issue) =="
BASE="https://triple-a-tropics.com/bugs-api"
# each `secret put` deploys a new Worker version; edge propagation can take
# ~30 s, during which POST 503s "board backend not configured yet" — retry
RESP=""
for i in $(seq 1 12); do
  RESP=$(curl -sf -X POST "$BASE/issues" -H 'content-type: application/json' -d "{
    \"tester\": \"deploy-smoke\", \"area\": \"Other\", \"severity\": \"nit\",
    \"title\": \"bug board deploy smoke test\",
    \"detail\": \"Filed and closed automatically by deploy-bugs.sh to prove the loop.\",
    \"website\": \"\"}") && break
  echo "  POST not ready yet (attempt $i/12), waiting 10 s..."
  sleep 10
done
[ -n "$RESP" ] || { echo "smoke POST never succeeded"; exit 1; }
echo "POST -> $RESP"
NUM=$(echo "$RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["number"])')
curl -sf -X PATCH "$BASE/issues/$NUM" -H 'content-type: application/json' \
  -H "x-admin-key: $ADMIN" -d '{"state":"closed"}' >/dev/null
echo "PATCH close #$NUM -> ok"
curl -sf "$BASE/issues" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(f"GET -> {len(d)} report(s) on the board")'
# take the smoke issue off the board (strip the label) so testers see a
# clean slate; the closed issue itself stays as deploy provenance
GH_TOKEN=$GH_PUSH_TOKEN gh api -X DELETE \
  "repos/WeathermanAAA/Triple-A-Tropics/issues/$NUM/labels/tester-report" \
  >/dev/null && echo "smoke issue #$NUM de-boarded"

echo
echo "================================================================"
echo " ADMIN KEY (yours; open /bugs/#admin=$ADMIN once to enable"
echo " the Mark-fixed buttons; stored in that browser's localStorage)"
echo "================================================================"
