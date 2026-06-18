# SATELLITE.md — Satellite Imagery Backend Re-Architecture

> **Filename note:** delivered as `SATELLITE-REARCH.md` (untracked, **uncommitted**) so it does not clobber the existing tracked `SATELLITE.md` (the current custom-zoom-tool doc, live on the Pages site). On sign-off, `git mv SATELLITE-REARCH.md SATELLITE.md` in **tat-satellite-render** (its true home) — not in the Pages repo. Nothing here is committed, pushed, or deployed at Stage 0.

**Stage 0 — design + adversarial review only. A hard gate. Nothing is built or deployed until Andrew signs off.**

Status: DRAFT for sign-off · revised once after the Stage-0 adversarial review (companion `SATELLITE-REVIEW.md`).

---

## 0. The one constraint everything else serves

**Zero visual change. Every rendered plot stays pixel-identical to today** — true-color + IR/WV + visible, the black coastlines/borders, the dashed graticule, the burned-in title strip, the storm badge, the `@WeathermanAAA_` watermark, the BT min/max readout, the right-side colorbar, the colortables, the framing. The re-architecture is an **ingest + serve + playback** change wrapped *around* an untouched renderer, so "nothing looks different" is true **by construction**, not by careful re-creation.

Two scoping clarifications so "by construction" is honest, not a slogan:

1. **Pixel-identity is guaranteed for every product that exists today** — the 6-band storm floaters, the mesosector loops, and (if retained) the on-demand custom-zoom render. The construction: freeze `render.py` + `truecolor.py` + the `tat_palettes` colortables; change only what surrounds them; the WebP transcode is *already* in production (`transcode_frame`). A per-product pixel-diff gate enforces it.
2. **The new MapLibre zoom/pan tiled map is additive.** Nothing like it exists today, so it has no prod baseline to be *pixel-identical to*. "Keep the current look" for it means: same colortables, same coastline/graticule style, same dark theme + `@WeathermanAAA_` credit — reproduced faithfully and gated against a *reference render*, not against prod. §6 and §11 treat this head-on; it is the biggest design question for sign-off.

---

## 1. Where we are today (ground truth — so the delta is explicit)

Mapped from live `tat-satellite-render@main` + the main-repo `satellite/index.html`. The brief's decided specifics assume a few things `main` does not currently reflect; those are flagged ⚠ and revisited in §11.

| Layer | Today (on `main`) |
| --- | --- |
| **Renderer** | `render.py::render_png` → matplotlib(Agg)+cartopy composed figure: DARK_BG `#0a0d12`, title strip (6%), PlateCarree map, right colorbar (scalar only), **black** coastlines/borders (10m <90° / 50m <180° / 110m), dashed graticule, SS-colored storm badge, `@WeathermanAAA_ · NOAA/JMA {sat} {ABI/AHI}` watermark, IR/WV BT min/max. Output = **one composed full-frame PNG** (chrome baked in), `dpi=110`, ~1320 px. `transcode_frame` → Lanczos downscale to 1056 px → lossy WebP `q90 method=6`. Degenerate-frame guards raise (>55% NaN scalar / >50% NaN or near-black RGB) → `/render` 500 → poller retry/skip. |
| **True color** | `truecolor.py::assemble_truecolor` — CIMSS/CIRA GeoColor recipe on **already-cropped, co-registered** R/G/B/veggie/clean-IR bands. Synth-green ABI, native green AHI, Rayleigh (pyspectral), sun-norm + tone curve, GeoColor-lite night fade. |
| **Colortables** | `colormaps.py` re-exports `tat_palettes` (main repo `palette/`, the SSOT shared with HAFS sim-sat): rainbow_ir, dvorak (BD), tat_neon, wv_tat, ir_gray. |
| **Fetch / crop** | `satellites.py` — `s3fs(anon=True, listings_expiry_time=30)` over `noaa-goes19/18/16/17`, `noaa-himawari9/8`. **Geos-crop before inverse-project ("loads only the cropped window")** — crop-before-composite is already the pattern. Product picker M1/M2→CONUS/PACUS→FD; FLDK segment-glob. Bucket fallback by date. Antimeridian-safe centering. |
| **Ingest** | **Polled, not event-driven.** `floater_poller.py` (a worker, *not* the framework) discovers storms from `cdn…/feeds/{wp,al,ep}_tracks_data.json` + NHC b-decks + knackwx + CurrentStorms, builds a 12° square crop on an extrapolated center, POSTs `/render` (Railway private net) per (storm, band). Hot `ir`+`irbd` @60 s; cold bands stretch with storm count. **Never-miss today = sha256 content-hash dedup per (storm,band) + manifest-as-truth**; `_resync_hash` on restart; **no completeness gate, no ListObjectsV2 reconciliation.** |
| **Render service** | `app.py` FastAPI: `POST /render` (on-demand, 10/min/IP, `Semaphore(2)`, in-memory LRU 200/100 MB), `GET /health`. The poller calls it over private networking. |
| **Serve** | R2 `triple-a-tropics-media` via `cdn.triple-a-tropics.com`. Frames `floaters/{slug}/{band}/{YYYYMMDDTHHMMZ}.webp` (immutable, `max-age=31536000`). Manifests `floaters/{slug}/manifest.json` + `floaters/manifest.json` (`max-age=30`). Per-storm manifest lists **every frame** `{t,key}` + `latest` + `last_hash`. **App-managed pruning** (native ≤6 h → thin 5 min ≤24 h → drop), **no R2 lifecycle rules**. Class-A PUT minimized via content-hash dedup. |
| **Viewer** | `satellite/index.html` — **"the dumb player"** (sat-simple): ONE `<img>` per viewer, preloaded `Image()` array, bare rAF + `img.src` swap, `im.decode()` gate, **one** `SAT_LOOP_TOKEN` (only one of floater/meso animates; the other parks static + follows live on the 60 s poll). **No canvas / createImageBitmap / LRU** — those were *tried and abandoned* ("cleverness was the bug"). Fixed timestep, 6× dwell on newest. GIF export builds a canvas only at export time. |
| **Custom zoom** | Leaflet 1.9.4 + leaflet-draw rectangle → on-demand `/render` (the only "region picker" today; arbitrary bbox, 3–15 s, rate-limited). |
| **GIBS** | Already a **client** feature: a static `<img>` "Latest Polar Pass · MODIS/VIIRS True Color" with a region `<select>`, manifest `gibs/manifest.json` from `update-gibs.yml`, `?v=generated_utc` cache-bust. Not WMTS, not a basemap — a daily snapshot. |
| **Shared picker** | `models/regions.js` (`window.TATRegions`): basin-grouped list, `extentOf/project/inRegion/drawBasemap`, `RegionPicker` modal. **Not used by satellite today.** |
| **MapLibre** | Already in the repo — `global_tracks.html` uses MapLibre GL **4.7.1** (CDN). Proven dependency; just not in satellite yet. |
| **Worker home** | ⚠ On `main`: **all Railway** (render web + floater worker, private net). The dedicated **meso VPS ("Box 1")** + hot/cold meso lanes + 2.5-min Himawari live on the **unmerged** `webp-frames-meso` branch. The brief's "OVH US VPS" is the decided destination; this re-arch consolidates onto it. |

**Net delta:** poll → **event-driven** ingest (SNS→SQS→VPS long-poll) with an explicit **completeness gate** + **watermark/backfill** + **ListObjectsV2 reconciliation**; manifest-of-every-frame → **`latest_times.json` SSOT + deterministic paths**; on-demand custom-zoom → **pre-rendered pyramid + viewport crop**; a **MapLibre tiled** zoom/pan map alongside the kept dumb-player loops; Railway → **OVH VPS** home; **shadow-first migration with a visual-regression cutover gate.**

---

## 2. Core principle — keep the renderer; wrap it

```
                          ┌──────────────────────────────────────────────┐
   NOAA Open Data S3      │            OVH US VPS (8–16 GB)               │      Cloudflare R2
   noaa-goes19  ──SNS──▶  │  SQS long-poll ─▶ completeness gate ─▶       │   triple-a-tropics-media
   noaa-goes18  ──SNS──▶  │  fetch+CROP (satellites.py, unchanged) ─▶    │   /shadow/… then prod
   noaa-himawari9 ─SNS─▶  │  RENDER (render.py + truecolor.py, FROZEN)─▶ │   frames + latest_times.json
        │                 │  transcode_frame ─▶ R2 PUT ─▶ del SQS msg    │        │
        │  (fallback)     │  watermark + backfill + ListV2 reconcile     │        ▼
        └── poll ListV2 ─▶│  DLQ on poison · systemd Restart=always      │   cdn.triple-a-tropics.com
                          └──────────────────────────────────────────────┘        │
                                                                                   ▼
   Viewer:  dumb <img> player (floater/meso, UNCHANGED look)
            + MapLibre raster map (NEW zoom/pan, additive)
            + GIBS WMTS basemap (client, free) + TATRegions picker + /embed
```

`render.py`, `truecolor.py`, and `tat_palettes` are a vendored binary: the re-arch **calls** them and **moves where they run**, never edits them. The frozen unit is exactly `(satellites.fetch* → render_png → transcode_frame)`; identical inputs → identical output bytes → every existing product is pixel-identical **by construction**. The **only** sanctioned reason to touch a renderer is a measured perf forcing-function (e.g. full-disk RAM), behind a **per-product pixel-diff gate** — the exception (§5/§11), never the default.

---

## 3. INGEST — event-driven, never-miss

### 3.1 Wiring (SNS → SQS → VPS long-poll)

NOAA publishes new-object notifications on SNS in `us-east-1` (acct `123901341784`): `NewGOES19Object`, `NewGOES18Object`, **`NewHimawariNineObject`** (spelled-out *Nine*). These topics deliver **only to SQS or Lambda** — no arbitrary HTTPS subscriber. We pick **SQS** (Lambda rejected for the worker per §5). The OVH VPS **long-polls** SQS; Lambda is not in the path.

```
NewGOES19Object ─▶ SQS goes19-cmip    (filter-policy: ABI-L2-CMIPC/F/M*, MCMIP*)  ─┐
NewGOES18Object ─▶ SQS goes18-cmip    (same filter)                                ├─▶ VPS workers (long-poll, per queue)
NewHimawariNine ─▶ SQS himawari9-fldk (filter-policy: AHI-L1b-FLDK/*)              ─┘
                        each queue ──redrive(maxReceiveCount=5)──▶ DLQ <name>-dlq
```

- **Subscription filter policy** narrows to rendered products before a message reaches us (cuts SQS volume + cost). GOES key layout `<Product>/<Year>/<JULIANday>/<Hour>/…`, products `ABI-L2-CMIP{C,F,M}`+`MCMIP{C,F,M}`. Himawari `AHI-L1b-FLDK/<Y>/<M>/<D>/<HHMM>/…`.
- **The filename `s`-time is the canonical slot key** for watermark + dedup. Accounting is per `(product, sat, channel/segment, s-slot)`.
- **Visibility timeout** ≥ worst-case fetch+render (tens of seconds for true-color) + margin, so a slow render is not redelivered mid-flight.

### 3.2 The completeness gate (when a slot is renderable)

| Product | "Complete" = |
| --- | --- |
| **MCMIP{C,F,M}** | **1 file** (multiband composite) → done immediately. |
| **CMIP{C,F,M}** | **all required bands** for the slot. IR/WV/visible single-band products = 1 band; **true color = 5** (red 0.64, veggie 0.86, blue 0.47, [green 0.51 AHI], clean-IR 10.4). |
| **AHI FLDK** | **all segments × all required bands** (≈10 segments/band). |

Accumulate landed objects per slot in a small ledger (in-memory + on-disk sqlite/JSON); when the gate passes, enqueue an internal render job. **Mirror, do not replace, the renderer's degenerate-frame guard** — the gate prevents *scheduling* a partial render; the >50/55% NaN raise stays as the last line of defence against a "present but corrupt/partial" band. Both stay.

### 3.3 Never-miss = belt **and** suspenders (three layers, by authority)

1. **SQS events (primary).** Low-latency trigger; common path.
2. **Watermark + backfill poll (fallback).** Per `(sat, product)` keep the last fully-published slot watermark. A cron tick (= product scan interval: Meso 60 s, CONUS 5 min, FD/FLDK 10 min) **`ListObjectsV2`-reconciles** the recent prefix against the ledger; any complete slot newer than the watermark that SQS never delivered is enqueued — **backfill newest-first** (the enscenters lesson: oldest-first freezes "latest" behind a 404 gap). `s3fs listings_expiry_time=30` keeps a just-published object visible within a tick.
3. **Idempotency (safety net).** Render keys are **deterministic** from `(product, sat, band/variant, s-slot)`. A re-delivered SQS message or a racing backfill resolves to the *same* R2 key; **sha256 content-hash dedup** + "skip if key already current" makes the duplicate a no-op. At-least-once delivery is therefore safe.

**No silent caps:** every dropped/aged-out slot, backfill, and DLQ message is `log()`-ed with its slot id — "we covered everything" is auditable, never false-green.

### 3.4 Failure handling

- **DLQ** per queue (`maxReceiveCount=5`): a poison object (unreadable NetCDF, a band that never completes) lands in the DLQ after 5 receives; a slow alarm drains/inspects it instead of wedging the live queue.
- **Delete the SQS message only after the R2 PUT succeeds** (or after the gate explicitly decides incomplete-and-aged-out → ack). A render/PUT failure → message reappears after the visibility timeout → retried, bounded by `maxReceiveCount`.
- **Per-source isolation + always-on heartbeat** — reuse `poller_framework.py`'s spine for the watermark/backfill sources (one bad prefix never silences another; the VPS exposes a health heartbeat). The floater poller's bespoke loop folds onto this.

---

## 4. SERVE — manifest-SSOT, R2-cost-disciplined

### 4.1 Manifest as SSOT (SLIDER-style)

The viewer **never lists the bucket.** Per product a small **`latest_times.json`** is the SSOT; the viewer derives every URL from a **deterministic path template** + the times.

```jsonc
// single-frame product (floater/meso)   e.g. sat/goes19/meso2/ir/latest_times.json  (max-age 15–30)
{ "product":"goes19/meso2/ir", "path":"sat/goes19/meso2/ir/{t}.webp", "tile":null,
  "times":["20260617T1758Z","20260617T1759Z","20260617T1800Z"], "latest":"20260617T1800Z",
  "as_of":"2026-06-17T18:00:42Z" }

// tiled product (the MapLibre map)
{ "product":"goes19/fd/ir", "tile":"sat/goes19/fd/ir/{t}/{z}/{x}/{y}.webp",
  "minzoom":0, "maxzoom":4, "times":[...], "latest":"...", "as_of":"..." }
```

Tiny, cacheable, no per-frame manifest churn; the viewer computes URLs (no key list to ship); written once per slot. **Supersedes** today's "manifest lists every `{t,key}`" — a deliberate, reviewed change (deterministic path = the contract; the time list is the only variable).

### 4.2 Tiles vs full frames — the cost model forces the split

- **WebP, 512 px, served @2x** (a 512 CSS tile = a 1024-px asset on HiDPI). Lossy **q80–85** for photographic true-color/IR; **q90 / near-lossless only** for hard colortable edges (Dvorak-BD banding, any future dBZ). Today's floater frames (single 1056-px `q90` WebP) stay unchanged.
- **R2 economics: egress free; Class-A PUT (and LIST) is THE cost driver; GET cheap.** Tiling *multiplies* PUTs: a full-disk pyramid z0–4 ≈ 1+4+16+64+256 = **341 tiles/band/frame** × bands × 10-min = a PUT bomb. Single full-frame = **1 PUT/frame**.
- **So the split is dictated by cost *and* aligns with zero-change:**
  - **Floater + meso → single full-frame WebP, 1 PUT/frame, unchanged look** (the existing pixel-identical *and* cheap path). No tiling.
  - **Only the genuinely-zoomable wide products → a bounded pyramid.** Bound PUTs hard: few zoom levels (z0–4, not z0–8), render-on-change only (content-hash dedup → a static ocean tile re-PUTs ~never), aggressive lifecycle TTL.

### 4.3 R2 discipline

- **Stay on Standard** for hot frames (IA's per-GB savings are eaten by IA Class-B/retrieval on constantly-read live-storm frames).
- **Tiered lifecycle TTL via R2 object-lifecycle rules** (up to 1000) keyed by prefix: floater/meso `~7–14 d`, full-disk pyramid `~30 d`, extras per cadence. Lifecycle is the floor; the app-side manifest prune still runs so `latest_times.json` never points past TTL.
- **Minimize PUTs:** deterministic keys + content-hash dedup + render-on-change. Bytes matching the prior slot are not re-PUT.
- **Cache-Control:** immutable frames/tiles `max-age=31536000, immutable`; `latest_times.json` `max-age=15–30`.

---

## 5. RENDER / WORKER — OVH VPS, crop-before-composite

### 5.1 Worker home

The **OVH US VPS** (not Lambda): Satpy/GDAL/pyspectral/cartopy deps + RAM bursts fight Lambda's limits, and a long-poll consumer wants a long-lived process. Size **8–16 GB**, `systemd`, **`Restart=always`**. Two roles (separate units, shared code): the **SQS consumer/renderer** and the **watermark/backfill cron**. This is where the floater poller + meso lanes consolidate off Railway.

### 5.2 The RAM/crop trap (load-bearing)

**Crop the sector BEFORE compositing.** Full-disk true-color compositing can exceed **24 GB** (satpy #1902) — Rayleigh/resample blow up on full-res full-disk arrays. `satellites.py` already **geos-crops before inverse-project** and `assemble_truecolor` operates on **already-cropped** bands, so today's pattern is correct and **must be preserved**. Constraint: **never composite a full disk.** For the new "full-disk" tiled product, render it as a **mosaic of cropped sub-sectors/tiles**, each cropped-then-composited within the per-tile RAM envelope, then assembled — *not* one 24 GB composite. Each unit's peak RSS is bounded and asserted in the heartbeat (`process.peak_rss_mb`).

### 5.3 Reuse the renderer verbatim

The consumer's inner call is exactly today's `(fetch* → render_png → transcode_frame)`. Two integration choices, both keep the renderer frozen:

- **(A, preferred) in-process import** — the worker imports `render.py`/`satellites.py` directly (no HTTP hop, no rate limiter, lowest latency). `/render` can stay for the on-demand custom-zoom fallback + `/health`.
- **(B) keep `/render` HTTP** — the worker POSTs `/render` over localhost, exactly as the floater poller does today. Zero renderer change; one extra hop.

Either way: **delete the SQS message only after the R2 PUT succeeds; DLQ on poison; the degenerate-frame guard stays.**

### 5.4 Recentering is server-side

Floaters and meso are **recentered on the server** (the crop bbox follows the extrapolated center, as `floater_poller._extrapolate` does today). The viewer receives already-centered frames — no client reprojection, so playback stays the dumb `<img>` swap.

---

## 6. PLAYBACK / VIEWER — keep the look, add zoom

### 6.1 Two playback engines, by product shape

| Product | Engine | Why |
| --- | --- | --- |
| **Floater + meso loops** (single full-frame, server-centered) | **The existing dumb `<img>` player, UNCHANGED** (one `<img>`, preloaded `Image()`, bare rAF + `src` swap, `decode()` gate, single `SAT_LOOP_TOKEN`). | It is the zero-visual-change playback *and* the design that survived after the clever canvas player was abandoned. ⚠ **Contradicts the brief's "bespoke canvas + createImageBitmap (Worker) + LRU/.close()" for floater/meso — §11-A.** Recommend: keep the dumb player. |
| **Zoom/pan tiled map** (regional / full-disk pyramid) | **MapLibre GL JS, raster source.** Animate by **pre-adding one raster source per frame and toggling `raster-opacity`**, *not* `setTiles` (which rebuilds the tile cache each frame → stutter). LRU-evict offscreen frame-sources. | True zoom/pan over a pre-rendered pyramid; MapLibre is already a proven repo dep (4.7.1). |

### 6.2 Region picker = viewport change over the pyramid

The draw-box / region picker is a **viewport change over the PRE-RENDERED pyramid — no on-demand server render.** Reuse **`TATRegions`** (basin groups, `extentOf`, thumbnail modal) to set the MapLibre camera. ⚠ This **replaces** today's Leaflet-draw → on-demand `/render` arbitrary-bbox flow with a *fixed set of pre-rendered regions* (§11-D); recommend keeping `/render` on-demand as a labelled "custom box" fallback through migration.

### 6.3 The chrome problem for tiles (the crux of "by construction")

The renderer **bakes chrome** (title strip, colorbar, watermark, badge, BT min/max, *and* coastlines/borders/graticule) into one composed figure. A slippy raster map cannot use that as tiles (header/colorbar would tile into the map). The tiled map needs a **map-raster-only** source: the cartopy map content (imagery + **the same** black coastlines/borders + dashed graticule, same theme, same colortables) **without** the title/colorbar/watermark chrome; the chrome becomes an **HTML/MapLibre overlay** matched to the matplotlib chrome (same fonts, colors, text, positions, the credit).

The one place "by construction" weakens, stated plainly:
- **A chrome-free map raster is an additive renderer mode** — the sanctioned exception, gated by a **per-product pixel-diff** against a reference render of the map area.
- **The overlay chrome is a faithful re-creation, not a byte-copy** → can drift → gated by a **reference-render diff** of the composited view + visual sign-off.
- **The existing floater/meso/custom-zoom products keep the full composed render untouched** — their guarantee stays strictly by-construction. Only the *new* map carries the weaker (reference-gated) guarantee.

Sign-off decision (§11-B): **B1** chrome-free map tiles + pixel-matched HTML overlay, vs **B2** clean tiles + HTML chrome in "house style" with *no* pixel-identity claim. **B2 recommended** (lowest risk for "by construction" — it never claims identity to a baseline that doesn't exist); B1 only if the map must be indistinguishable from a floater.

### 6.4 Extras live in the viewer

- **GIBS as a client-fetched WMTS basemap/context layer** under the MapLibre map (XYZ/WMTS REST, no auth, zero egress cost, graceful-degrade if GIBS is down). The existing GIBS **daily-polar-pass `<img>` carve-out stays untouched** (separate section).
- **`/embed` (v1):** a query-flag/route renders a chromeless self-contained viewer for iframing (same precedent as `global_tracks.html` + postMessage auto-resize). No new infra.

---

## 7. MIGRATION — the shadow-first safety spine

1. **Shadow prefix.** The new pipeline writes a **parallel `/shadow/` R2 prefix** mirroring prod (`shadow/sat/…`). User-facing nothing. The current Railway floater poller keeps running untouched.
2. **Visual-regression GATE.** Diff **shadow vs prod** frame-for-frame: **`odiff` (or pixelmatch)** at a **0.1% AA-filtered** per-frame budget (fast across thousands of frames) + an **SSIM fallback** for the few that exceed it (real structural drift vs sub-pixel AA noise). **Any frame over budget BLOCKS cutover.** Checks are **pixel/ink, never DOM/metadata** (the false-green lesson; `test_cyclolab_visual.py`'s pixel-ink analysis is the precedent). The repo has **no image-diff tooling today** → odiff/pixelmatch is net-new (tsr CI, not the Pages repo).
3. **Pin the render toolchain** so any diff is *real drift, not env noise*: freeze `matplotlib==3.9.3`, `cartopy==0.24.1`, `numpy==2.1.3`, `Pillow==11.0.0`, `s3fs==2024.10.0`, `pyspectral/pyorbital`, fontconfig/freetype, and **libwebp** (text metrics, geometry, and WebP quantization all move pixels across versions). Run shadow on the **same pinned image** as prod.
4. **Cutover = env-prefix feature flag** (mirror `WRITE_LIVE_FEEDS`): a `SAT_WRITE_PREFIX`/`SAT_SERVE_PREFIX` flag flips reads/writes from `shadow/` to prod **per product**, with a **canary %**, a **one-line revert** (flip back), and a **CDN cache bust** (purge the prefix). No redeploy to cut over or roll back.
5. **Decommission only after** prod runs clean on the new pipeline for a defined soak, per product, gate green throughout.

---

## 8. STAGING — each stage gated by the visual-regression diff; renderer + colortables + layout untouched

| Stage | Deliverable | Gate |
| --- | --- | --- |
| **S1 — Ingest backbone, ONE product** | SNS→SQS→VPS long-poll + completeness gate + watermark/backfill + DLQ, proven **never-miss on one product** (e.g. GOES-19 meso-2 clean-IR) → `/shadow/`. No viewer change. | Multi-day never-miss audit: zero missed slots vs an independent ListObjectsV2 ground-truth; shadow frames pixel-identical to prod for that product. |
| **S2 — Tile viewer** | MapLibre raster map + `latest_times.json` SSOT + opacity-toggle animation over S1's pyramid. | Reference-render diff of the map view (§6.3); never-miss holds. |
| **S3 — Floaters rebuilt** | All 6 floater bands re-ingested through the new pipeline → `/shadow/floaters/…`, **same renderer, same single-frame WebP, same dumb player.** | **Pixel-identical** shadow-vs-prod across every floater band/frame (the by-construction guarantee, enforced). |
| **S4 — Mesosectors** | Meso lanes (hot/cold, antimeridian, FLDK completeness, 2.5-min Himawari option) consolidated on the VPS → `/shadow/meso/…`. | Pixel-identical shadow-vs-prod for every sector/band. |
| **S5 — Overlays / region-picker / thumbnail-wall** | TATRegions picker over the pyramid; product/region overlays; a thumbnail wall. | Reference-render + interaction diff; no S3/S4 regression. |
| **S6 — GIBS basemap + `/embed`** | GIBS WMTS context layer (client) + the iframe `/embed`. Existing GIBS carve-out untouched. | Graceful-degrade (GIBS down → map still works); embed parity with the full page. |

Cutover is **per stage, per product**, via the §7 flag — never big-bang.

---

## 9. Zero-visual-change — proof obligations (per product)

| Product | How identity is guaranteed | Gate |
| --- | --- | --- |
| Floater (6 bands) | Same `(fetch→render_png→transcode_frame)`; same single-frame WebP; same dumb player. | Pixel-diff shadow vs prod = 0 over budget. **By construction.** |
| Mesosectors | Same as above. | Pixel-diff = 0 over budget. **By construction.** |
| On-demand custom zoom (if kept) | `/render` unchanged. | Pixel-diff on a fixed bbox set. **By construction.** |
| **New tiled map** | Reuses colortables + coastline/graticule style + theme + watermark; chrome path per §6.3. | **Reference-render** diff + visual sign-off. *Additive; no prod baseline.* |

A non-zero diff on any "by construction" product is a **real bug in the wrapper** (wrong inputs/crop, a non-pinned dep) — the gate fails loudly, not a tolerance to widen.

---

## 10. Cost & capacity sketch

- **VPS:** one always-on 8–16 GB box; peak RSS bounded by crop-before-composite; SQS long-poll + backfill cron under systemd.
- **R2 PUTs/day (to confirm in S1):** floaters ≈ Σ storms × 6 bands × frames/day (dedup suppresses static frames); meso ≈ sectors × bands × cadence; the tiled full-disk is the risk — **z0–4 + render-on-change + TTL** bound it; a z0–8 every-10-min pyramid is explicitly excluded.
- **Egress:** free on R2 — viewer GETs + GIBS WMTS cost us nothing in transfer.

---

## 11. Open problems / flags (designed *against* the decided specifics; the real ones I found)

**A. ⚠ The canvas+createImageBitmap+LRU playback for floater/meso resurrects an abandoned approach.** The brief (§3) asks for a "bespoke canvas + createImageBitmap (Worker) + fixed-timestep + LRU/.close()" for the non-tiled loops. But the live player is the deliberately-**dumb `<img>`-swap** sat-simple player, adopted *after* a canvas/createImageBitmap player was built and **abandoned** ("cleverness was the bug"). The dumb player already has a decode gate (`im.decode()`) + the single-loop token. **Recommendation: keep the dumb player for floater/meso**; reserve canvas+bitmap strictly for a measured perf problem with a written before/after. The most consequential flag.

**B. ⚠ "Tiled map, keep the current look, zero change by construction" is internally in tension** because the renderer bakes chrome (§6.3). Resolve at sign-off: **B2** (clean tiles + HTML chrome, house style, no pixel-identity claim — recommended) vs **B1** (chrome-free map tiles + pixel-matched HTML overlay — only if the map must be indistinguishable from a floater).

**C. ⚠ Tiling vs R2-PUT-minimization is a direct conflict** the brief states both ways. Resolved in §4.2 by **only tiling the zoomable wide products** (floater/meso stay 1-PUT single-frame) and **bounding the pyramid** (z0–4, render-on-change, TTL). A future deep-zoom (z>4) product must re-derive the PUT budget first.

**D. ⚠ Pre-rendered pyramid replaces arbitrary-bbox custom zoom.** Today's Leaflet-draw → `/render` gives *any* box; the pyramid gives a *fixed region set*. Recommend **keeping `/render` on-demand as a "custom box" fallback** through migration so no capability is lost.

**E. ⚠ Current `main` has no meso VPS and no completeness gate.** "Box 1 / hot-cold meso" + the never-miss gate are partly on the unmerged `webp-frames-meso` branch / net-new. S1's never-miss audit must be run for real — there is no prod completeness gate to inherit.

**F. SQS visibility timeout vs true-color render time.** A full-sector true-color render can take tens of seconds; the visibility timeout must exceed worst-case fetch+render or SQS redelivers mid-flight (idempotency makes it *safe* but wastes the box). Size it from S1 measurements.

**G. GIBS WMTS as basemap must stay context-only**, graceful-degrade — never a data dependency. The existing GIBS daily-polar-pass carve-out is separate and stays untouched (confirmed not WMTS today).

**H. Watermark backfill must be newest-first** (the enscenters shared-manifest lesson): oldest-first freezes "latest" behind a 404 gap. §3.3 encodes it; flag so it is not "cleaned up" later.

---

## 12. Explicitly NOT in scope at Stage 0

Nothing is built, deployed, or branched. No renderer edit. No R2 write. No SQS/SNS resource. No viewer change. This document + the adversarial-review findings are the only outputs; both are for sign-off.

---

## 13. Revision log

- **r1 (this draft)** — first full design from ground-truth of `tat-satellite-render@main` + `satellite/index.html`.
- **r2** — folds the Stage-0 adversarial review (`SATELLITE-REVIEW.md`): _[filled after the review runs]._
