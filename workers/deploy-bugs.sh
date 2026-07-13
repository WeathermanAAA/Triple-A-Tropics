#!/usr/bin/env bash
# One-shot deploy for the tester bug-board Worker (workers/bugs-api.js).
#
# Prereqs: `npx wrangler login` done once on this machine (browser OAuth),
# and GH_PUSH_TOKEN in the env (the durable classic PAT the Codespace
# already uses — repo scope covers Issues RW; verified 2026-07-13).
#
# Rerunning is safe; it ROTATES the tester passcode + admin key.
set -euo pipefail
cd "$(dirname "$0")"

: "${GH_PUSH_TOKEN:?export GH_PUSH_TOKEN first (durable classic PAT)}"

echo "== deploy worker =="
npx wrangler deploy -c bugs-api.toml

echo "== wire secrets (values never echoed except the two you must keep) =="
printf '%s' "$GH_PUSH_TOKEN" | npx wrangler secret put GITHUB_TOKEN -c bugs-api.toml

PASSCODE=$(openssl rand -hex 4)
ADMIN=$(openssl rand -hex 16)
printf '%s' "$PASSCODE" | npx wrangler secret put TESTER_PASSCODE -c bugs-api.toml
printf '%s' "$ADMIN"    | npx wrangler secret put ADMIN_KEY -c bugs-api.toml

echo "== smoke test (files + closes one nit issue) =="
sleep 5
BASE="https://triple-a-tropics.com/bugs-api"
RESP=$(curl -sf -X POST "$BASE/issues" -H 'content-type: application/json' -d "{
  \"tester\": \"deploy-smoke\", \"area\": \"Other\", \"severity\": \"nit\",
  \"title\": \"bug board deploy smoke test\",
  \"detail\": \"Filed and closed automatically by deploy-bugs.sh to prove the loop.\",
  \"passcode\": \"$PASSCODE\", \"website\": \"\"}")
echo "POST -> $RESP"
NUM=$(echo "$RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["number"])')
curl -sf -X PATCH "$BASE/issues/$NUM" -H 'content-type: application/json' \
  -H "x-admin-key: $ADMIN" -d '{"state":"closed"}' >/dev/null
echo "PATCH close #$NUM -> ok"
curl -sf "$BASE/issues" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(f"GET -> {len(d)} report(s) on the board")'

echo
echo "================================================================"
echo " TESTER PASSCODE (share with testers):  $PASSCODE"
echo " ADMIN KEY (yours; open /bugs/#admin=$ADMIN once to enable"
echo " the Mark-fixed buttons; stored in that browser's localStorage)"
echo "================================================================"
