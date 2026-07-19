# Satellite custom-zoom tool

Custom-snapshot tool: user picks a bbox + time + channel + enhancement on triple-a-tropics.com/satellite/, gets back a clean cropped GOES PNG.

Two pieces:
- **Frontend:** `satellite/index.html` (Leaflet + leaflet-draw) on this repo, served by GitHub Pages.
- **Backend:** [WeathermanAAA/tat-satellite-render](https://github.com/WeathermanAAA/tat-satellite-render) — FastAPI on the render box (`render.triple-a-tropics.com`, Caddy + docker compose; see the repo's RUNBOOK-RENDER.md), fetches NOAA GOES NetCDFs, crops in geos-projection space, renders with matplotlib + cartopy.

Frontend talks to backend via `POST /render`. Backend handles caching and rate-limiting.

## What the backend does

1. Receives `{bbox, time, channel, enhancement}`.
2. Lists `noaa-goes19` AWS bucket for the resolved hour.
3. Picks smallest covering product: Mesoscale (after coverage check via NetCDF global-attrs) → CONUS → Full Disk.
4. Downloads the file to ephemeral /tmp, crops in (x, y) scan-angle space via the ABI projection, materializes only the bbox window.
5. Renders with matplotlib + cartopy 10m features. Three enhancements: `tat_neon`, `dvorak_bd`, `grayscale`.
6. Caches the PNG in an LRU keyed by hash(bbox, snapped_time, channel, enhancement). 5 min TTL on `"latest"`.

## Deploy updates

The backend runs on the render box as docker compose services behind Caddy
(`render.triple-a-tropics.com` resolves straight to the box). Deploys:

```bash
# on the box (see tat-satellite-render RUNBOOK-RENDER.md for details)
cd /root/tat-satellite-render && git pull
docker compose -p tat-render -f docker-compose.render.yml build render
docker compose -p tat-render -f docker-compose.render.yml up -d render
```

Env lives in the box `.env` (R2 credentials etc.). Monitoring:

```bash
docker logs tat-render-render-1 --tail 50     # service logs
curl -s https://render.triple-a-tropics.com/health | jq
```

## When the operational GOES sat changes

GOES-19 took over GOES-East from GOES-16 in April 2025. If/when GOES-19 is succeeded (likely GOES-21 or later, ~2030s), update both:
- Backend: `GOES_BUCKET` env var (set in the box `.env`; `up -d render` applies it).
- Frontend (this repo): `satellite/index.html` page header text + any sat-name labels.

The render pipeline is sat-agnostic — `goes_sat_label()` reads the bucket name and produces "GOES-NN" automatically.
