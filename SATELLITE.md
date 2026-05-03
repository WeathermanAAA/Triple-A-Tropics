# Satellite custom-zoom tool

custom-snapshots-style tool: user picks a bbox + time + channel + enhancement on triple-a-tropics.com/satellite/, gets back a clean cropped GOES PNG.

Two pieces:
- **Frontend:** `satellite/index.html` (Leaflet + leaflet-draw) on this repo, served by GitHub Pages.
- **Backend:** [WeathermanAAA/tat-satellite-render](https://github.com/WeathermanAAA/tat-satellite-render) — FastAPI on Railway, fetches NOAA GOES NetCDFs, crops in geos-projection space, renders with matplotlib + cartopy.

Frontend talks to backend via `POST /render`. Backend handles caching and rate-limiting.

## What the backend does

1. Receives `{bbox, time, channel, enhancement}`.
2. Lists `noaa-goes19` AWS bucket for the resolved hour.
3. Picks smallest covering product: Mesoscale (after coverage check via NetCDF global-attrs) → CONUS → Full Disk.
4. Downloads the file to ephemeral /tmp, crops in (x, y) scan-angle space via the ABI projection, materializes only the bbox window.
5. Renders with matplotlib + cartopy 10m features. Three enhancements: `tat_neon`, `dvorak_bd`, `grayscale`.
6. Caches the PNG in an LRU keyed by hash(bbox, snapped_time, channel, enhancement). 5 min TTL on `"latest"`.

## Deploy updates

Backend repo is Railway-watched. Push to its `main` triggers a redeploy via nixpacks:

```bash
cd /path/to/tat-satellite-render
git add ...
git commit -m '...'
git push   # Railway watches origin/main; redeploys automatically
```

For first-time bootstrap or to force a fresh build:

```bash
railway login
railway link            # if not already linked
railway up              # build via nixpacks, deploy
railway domain          # mints a public *.up.railway.app URL on first run
```

Set Variables in the Railway dashboard:
- `ALLOWED_ORIGINS=https://triple-a-tropics.com,https://www.triple-a-tropics.com`
- (optional) `MAX_CONCURRENT_RENDERS`, `RATE_LIMIT`, `LATEST_CACHE_TTL`, `GOES_BUCKET`

`GOES_BUCKET` defaults to `noaa-goes19` (operational GOES-East since Apr 2025). Override to `noaa-goes16` for 2017-era historical demos (Irma etc.) — that bucket holds data through 2025.

## Free-tier limits

| Resource | Limit |
| --- | --- |
| Cache | 200 entries / 100 MB total |
| Rate limit | 10 renders/min/IP (slowapi, X-Forwarded-For aware; cache hits exempt) |
| Concurrency | 2 simultaneous renders |
| "Latest" TTL | 5 min |

Render times (codespace baseline, expect 1.5–2× slower on Railway):
- IR (C13/C08) Full Disk: ~2–4 s
- Visible (C02) Full Disk: ~15 s (downloads 150–300 MB NetCDF)
- Mesoscale anything: ~1–2 s
- Cache hit: <50 ms

Estimated Railway cost at low/medium traffic: $3–7/mo. $5 hobby plan covers it.

## Monitoring

```bash
railway logs                # JSON-formatted log lines
railway logs --tail         # follow

# Production smoke test
curl -s https://<railway-url>/health | jq
curl -s -X POST https://<railway-url>/render \
  -H 'Content-Type: application/json' \
  -d '{"bbox":[-85,15,-65,30],"time":"latest","channel":13,"enhancement":"tat_neon"}' \
  -o /tmp/test.png
```

Healthcheck path is `/health`; Railway pings it on deploy. Returns:
```json
{"status":"ok","goes_bucket":"noaa-goes19","goes_bucket_reachable":true,"cache_entries":N,"cache_bytes":N}
```

## When the operational GOES sat changes

GOES-19 took over GOES-East from GOES-16 in April 2025. If/when GOES-19 is succeeded (likely GOES-21 or later, ~2030s), update both:
- Backend: `GOES_BUCKET` env var (no redeploy needed if you change it via Railway dashboard).
- Frontend (this repo): `satellite/index.html` page header text + any sat-name labels.

The render pipeline is sat-agnostic — `goes_sat_label()` reads the bucket name and produces "GOES-NN" automatically.
