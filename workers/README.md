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

### Deploy (user action — CF account, ~3 minutes)

```bash
cd workers
npx wrangler login                      # once per machine
npx wrangler secret put PREVIEW_TOKEN -c explorer-gate.toml
#   paste the preview token (Claude has it; also in AGENT_STATUS.md queue)
npx wrangler deploy -c explorer-gate.toml
```

### Gate check (run after deploy)

```bash
curl -sI https://triple-a-tropics.com/satellite/explorer/ | head -3
#   expect: HTTP/2 404 (no token -> looks nonexistent)
curl -sI "https://triple-a-tropics.com/satellite/explorer/?k=<TOKEN>" | head -5
#   expect: HTTP/2 302 + set-cookie tat_explorer_preview=... -> clean URL
#   then the cookie-carrying browser gets 200s on the page + assets
```
