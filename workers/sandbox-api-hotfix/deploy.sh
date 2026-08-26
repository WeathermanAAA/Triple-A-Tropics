#!/usr/bin/env bash
# One-shot: fetch the CURRENT deployed tat-sandbox-api module from Cloudflare,
# apply native-base64.patch, deploy with wrangler (bindings from sandbox.toml,
# secrets retained), verify bindings + content + /api/health.
# Needs CLOUDFLARE_API_TOKEN (Workers Scripts:Edit) in env. Rerunnable.
# Rollback: redeploy version 6250f19f-5b8e-4be3-952a-8de1234e9d90 from the
# dashboard (Versions), or re-run with an unpatched module.
set -euo pipefail
cd "$(dirname "$0")"
ACCT=33bb26c164250e1893f2ca61d293d44d
H="Authorization: Bearer ${CLOUDFLARE_API_TOKEN:?export CLOUDFLARE_API_TOKEN}"
echo "== fetch current module"
curl -sf -H "$H" "https://api.cloudflare.com/client/v4/accounts/$ACCT/workers/scripts/tat-sandbox-api" -o raw.multipart
python3 - <<'PY'
import re
raw=open("raw.multipart","rb").read(); b=raw.split(b"\r\n",1)[0]
for p in raw.split(b):
    m=re.search(rb'name="([^"]+)"', p)
    if m and m.group(1)==b"worker.js":
        open("worker.orig.js","wb").write(p.split(b"\r\n\r\n",1)[1].rsplit(b"\r\n",1)[0]); print("  worker.orig.js written")
PY
rm -f raw.multipart
if grep -q 'Uint8Array.fromBase64' worker.orig.js; then echo "  already patched upstream; nothing to do"; exit 0; fi
echo "== apply patch"
cp worker.orig.js worker.patched.js
patch --quiet worker.patched.js native-base64.patch
node --check worker.patched.js
echo "== deploy"
npx wrangler deploy -c sandbox.toml
echo "== verify"
curl -sf -H "$H" "https://api.cloudflare.com/client/v4/accounts/$ACCT/workers/scripts/tat-sandbox-api/settings" | python3 -c 'import json,sys; r=json.load(sys.stdin)["result"]; names=sorted(b["name"] for b in r["bindings"]); print("  bindings:",names); assert names==["CLERK_AUTHORIZED_PARTY","CLERK_ISSUER","CLERK_SECRET_KEY","DB","DEV_AUTH"], "BINDINGS CHANGED: roll back"'
curl -sf -H "$H" "https://api.cloudflare.com/client/v4/accounts/$ACCT/workers/scripts/tat-sandbox-api" | grep -q 'Uint8Array.fromBase64' && echo "  deployed module carries the patch"
sleep 10; curl -s -o /dev/null -w '  /api/health -> %{http_code}\n' -m 15 https://api.triple-a-tropics.com/api/health
