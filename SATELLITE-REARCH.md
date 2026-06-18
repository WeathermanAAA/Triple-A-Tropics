# SATELLITE.md — Satellite Imagery Backend Re-Architecture

> **Filename note:** delivered as `SATELLITE-REARCH.md` and **committed to the Pages repo `main` for review visibility** — a tracked design doc at repo root like `CYCLOLAB_DESIGN.md` and `ENSEMBLE_DESIGN.md`, **not** a rendered page (the Pages build does not surface raw `.md`). It is named `…-REARCH.md` so it does not clobber the existing tracked `SATELLITE.md` (the live custom-zoom-tool doc). On sign-off, `git mv SATELLITE-REARCH.md SATELLITE.md` in **tat-satellite-render** (its true home). The Stage-0 guarantee in §12 is unaffected: committing a design doc is not a build, deploy, R2 write, SQS/SNS resource, renderer edit, or viewer change.

**Stage 0 — design + adversarial review only. A hard gate. Nothing is built or deployed until Andrew signs off.**

Status: **r2** — DRAFT for sign-off, revised once after the Stage-0 adversarial review (companion `SATELLITE-REVIEW.md`). Gate verdict: **proceed with required changes** (see §13).

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

`render.py`, `truecolor.py`, and `tat_palettes` are a vendored binary: the re-arch **calls** them and **moves where they run**, never edits them. The frozen unit is exactly `(satellites.fetch* → render_png → transcode_frame)`; identical inputs → **identical decoded pixels** (the cutover gate compares decoded pixels at the §7.2 tolerance, which absorbs any sub-pixel encoder/AA jitter) → every existing product is pixel-identical **by construction**. "Identical inputs" includes the **crop bbox**, which for floaters/meso is a timing-sensitive function of the feed extrapolation (§5.4, flag below) — the by-construction guarantee for those products is therefore conditional on the bbox being a *captured* input, not recomputed (§7.2). **Sanctioned reasons to extend a renderer** are (a) a measured perf forcing-function (e.g. full-disk RAM, §5/§11) and (b) the additive **chrome-free map raster + its `EPSG:3857` reprojection** for the new tiled product (§6.3, §5.5) — both behind a **per-product pixel-diff gate**, nothing else. These are the exception, never the default.

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

- **Subscription filter policy** narrows to rendered products before a message reaches us. NOAA's topics deliver **S3-event payloads** — the object key lives in the message **body** (`Records[].s3.object.key`), with **no usable message attributes**. The SNS default `FilterPolicyScope=MessageAttributes` would therefore match **nothing** and silently drop every event. Each SQS subscription **MUST set `FilterPolicyScope=MessageBody`** with a body-path policy, e.g. GOES `{"Records":{"s3":{"object":{"key":[{"prefix":"ABI-L2-CMIPC/"},{"prefix":"ABI-L2-CMIPF/"},{"prefix":"ABI-L2-CMIPM/"},{"prefix":"ABI-L2-MCMIPC/"},{"prefix":"ABI-L2-MCMIPF/"},{"prefix":"ABI-L2-MCMIPM/"}]}}}}`, Himawari `{"prefix":"AHI-L1b-FLDK/"}`. SNS has **no glob/brace shorthand** — prefix matching is an array of `{"prefix":…}` objects; OR-alternatives are enumerated. Stay within the policy limits (≤5 keys, value-combination product ≤150, ≤256 KB). GOES key layout `<Product>/<Year>/<JULIANday>/<Hour>/…`; Himawari `AHI-L1b-FLDK/<Y>/<M>/<D>/<HHMM>/…`. **Cost note:** body filtering is **payload-based** ($0.09/GB scanned, billing matched *and* unmatched messages) — **not free** (only attribute filtering is). It cuts SQS-receive volume but adds a small scan charge; the cheaper alternative (subscribe each topic to one queue and filter in the long-poll worker, paying SQS receive only) is decided in S1. The slot key is parsed from the object key per `(product, sat, channel/segment, s-slot)` **independent of the delivery filter**, so filter breadth never affects slot identity. **S1 acceptance check:** assert CloudWatch `NumberOfMessagesReceived` is non-zero for traffic that should pass and `NumberOfNotificationsFilteredOut-*` is ~0 against a captured raw NOAA notification — a silent filter no-op must not pass as green (the §3.3 backfill would otherwise mask it: system renders, primary path silently dead).
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
2. **Watermark + backfill poll (fallback).** Per `(sat, product)` keep the last fully-published slot watermark. A cron tick (= product scan interval: Meso 60 s, CONUS 5 min, FD/FLDK 10 min) **`ListObjectsV2`-reconciles** the recent prefix (current + previous hour only, per the §3.1 key layout — a single page; anonymous LIST on NOAA NODD buckets is free) against the ledger; any complete slot newer than the watermark that SQS never delivered is enqueued — **backfill newest-first** (the enscenters lesson: oldest-first freezes "latest" behind a 404 gap). `s3fs listings_expiry_time=30` keeps a just-published object visible within a tick.
3. **Idempotency (safety net).** Render keys are **deterministic** from `(product, sat, band/variant, s-slot)`. A re-delivered SQS message or a racing backfill resolves to the *same* R2 key; **sha256 content-hash dedup** + "skip if key already current" makes the duplicate a no-op. At-least-once delivery is therefore safe.

**No silent caps:** every dropped/aged-out slot, backfill, and DLQ message is `log()`-ed with its slot id — "we covered everything" is auditable, never false-green.

**Raw vs derived coverage — two authorities.** Layers 1–3 above prove never-miss of **raw NOAA slots**. But floaters and meso are **derived per-storm crops** on an extrapolated center (§5.4); a complete raw slot can produce zero crops, and the set of frames that *must* exist is driven by the storm list, which has no triggering raw event. So a second authority is required: a **derived-product ledger keyed on `(storm/sector slug, band, s-slot)`**, fed by joining a complete raw slot with the current storm/sector list. The **storm feed is an explicit ingest trigger** alongside SNS — when a storm enters coverage or its extrapolated bbox shifts, re-fire/backfill the latest complete raw slot so a newly-entered storm does not wait a full slot (or never get re-evaluated). The derived gate: for each active storm/sector at slot *t*, a frame exists in R2 **or** is explicitly logged `no-data/off-sat`. The S1/S3 never-miss audit compares **published frames** against the expected `(storm × band × slot)` set — not raw objects against `ListObjectsV2` (which is the upstream-coverage layer only). S1's GOES-19 meso-2 product is near-1:1 (raw slot ≈ frame) and does not exercise this join; S3 must carry the frame-coverage gate, not just pixel-identity.

### 3.4 Failure handling

- **DLQ** per queue (`maxReceiveCount=5`): a poison object (unreadable NetCDF, a band that never completes) lands in the DLQ after 5 receives; a named alarm (§3.5) drains/inspects it instead of wedging the live queue. A **sustained R2 outage** can also exhaust `maxReceiveCount` and DLQ live slots — but those slots are **not lost**: the §3.3 watermark never advanced past a failed PUT, so the backfill reconcile re-detects and re-enqueues them once R2 recovers (DLQ-redrive-to-source is the operator action). Never-miss is preserved by the fallback authority, not by SQS alone.
- **Delete the SQS message only after the R2 PUT succeeds** (or after the gate explicitly decides incomplete-and-aged-out → ack). A render/PUT failure → message reappears after the visibility timeout → retried, bounded by `maxReceiveCount`.
- **Per-source isolation + always-on heartbeat** — reuse `poller_framework.py`'s spine for the watermark/backfill sources (one bad prefix never silences another; the VPS exposes a health heartbeat). The floater poller's bespoke loop folds onto this.

### 3.5 Observability & alerting (the consumer of the audit)

§3.3 emits the signals (`log()`, the health heartbeat, `latest_times.json` `as_of`); this section names what **consumes** them, since a log on an unwatched box is exactly the false-green it warns against. Detection is layered *on top of* the backfill correctness net (which already guarantees a slot is eventually rendered) — the alarms exist so the system never runs silently on the degraded fallback path.

- **Cheapest, most robust detector — staleness of `latest_times.json` `as_of`** (R2 is the user-visible truth): an external uptime check pages when `as_of` is older than **2× the product cadence**. No new infra.
- **SQS DLQ `ApproximateNumberOfMessagesVisible` > 0** → notify; **main-queue `ApproximateAgeOfOldestMessage` > visibility-timeout × 2** → page (a wedged/lagging consumer).
- **SNS `NumberOfNotificationsFilteredOut-InvalidMessageBody` > 0** → notify (a NOAA payload/JSON-shape change); a sustained spike in **`-MessageBody`** → page (an over-broad or mis-scoped filter silently dropping wanted slots — the §3.1 / "primary path silently dead" failure mode).
- **Heartbeat absent for > N intervals**, **PUT error-rate**, **`disk_free_mb` below threshold** (§10) → page. Name the alert sink and the on-call owner.

These also instrument the event-driven-only silent-stall modes the old poll loop did not have: a mis-configured SQS subscription delivering nothing, a dead backfill unit beside a live SQS unit, disk-full PUTs after dedup says "changed."

### 3.6 Cold start & ledger durability

The ledger (§3.2) and per-`(sat, product)` watermarks (§3.3) are a **cache, not the source of truth — R2 is.** On a box rebuild (the only real DR for the §11 SPOF) both are empty, so the rebuild path is explicit: **on an empty/unset watermark, seed it from R2 reality** — `ListObjectsV2` the existing `sat/*` prefix for the newest present *complete* slot per `(sat, product)` and set the watermark there (the enscenters "derive from R2 reality" lesson, applied to bootstrap, not just backfill ordering). The newest-first backfill then fills any slots published while the box was down. This makes a rebuild neither re-PUT the whole rolling window nor silently skip the rebuild-window gap.

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
- **R2 economics: egress free; Class-A PUT (and LIST) is THE cost driver; GET cheap.** Tiling *multiplies* PUTs: a full-disk pyramid z0–4 = 1+4+16+64+256 = **341 tiles/band/frame** is the **global-grid upper bound** (the geostationary disk occupies only a subset of each level — the S1-measured non-empty count feeds the real budget). The tiled map uses MapLibre's default **global Web-Mercator XYZ scheme (rooted at the world z0 tile)**; **z4 ≈ 4.9 km/px** at the equator for a 512-px tile — coarser than GOES-F native 2 km, so **z0–4 is an overview pyramid**; native detail needs z>4 (gated by §11-C). Single full-frame = **1 PUT/frame**.
- **Dedup is a storage/PUT saving on low-interest tiles only — NOT a load-bearing PUT bound.** Content-hash dedup suppresses the static/clear tiles (anti-correlated with interest); the active cloudy/storm tiles change every frame and **re-PUT every frame**. The **z0–4 count cap** is the bound. The 341 arithmetic is already the no-dedup worst case; S1 measures the actual hit-rate keyed to fraction-of-disk-clear.
- **Render-compute is independent of dedup.** Dedup suppresses the *PUT*, not the *render* that produces the bytes to hash — see §5.2/§10 for the wall-clock/throughput budget; "PUT cost" is not "cost."
- **So the split is dictated by cost *and* aligns with zero-change:**
  - **Floater + meso → single full-frame WebP, 1 PUT/frame, unchanged look** (the existing pixel-identical *and* cheap path). No tiling.
  - **Only the genuinely-zoomable wide products → a bounded pyramid.** Bound PUTs hard: few zoom levels (z0–4, not z0–8), render-on-change only (content-hash dedup → a static ocean tile re-PUTs ~never), aggressive lifecycle TTL.

### 4.3 R2 discipline

- **Stay on Standard** for hot frames (IA's per-GB savings are eaten by IA Class-B/retrieval on constantly-read live-storm frames).
- **Tiered lifecycle TTL via R2 object-lifecycle rules** (up to 1000) keyed by prefix: floater/meso `~7–14 d`, full-disk pyramid `~30 d`, extras per cadence. Lifecycle is the floor; the app-side manifest prune still runs so `latest_times.json` never points past TTL.
- **Minimize Class-A ops (PUT + LIST):** deterministic keys + content-hash dedup + render-on-change. Bytes matching the prior slot are not re-PUT. The backfill reconcile scans only the recent prefix (~1 LIST/tick).
- **Cache-Control:** immutable frames/tiles `max-age=31536000, immutable`; `latest_times.json` `max-age=15–30`.

---

## 5. RENDER / WORKER — OVH VPS, crop-before-composite

### 5.1 Worker home

The **OVH US VPS** (not Lambda): Satpy/GDAL/pyspectral/cartopy deps + RAM bursts fight Lambda's limits, and a long-poll consumer wants a long-lived process. Size **8–16 GB**, `systemd`, **`Restart=always`**. Two roles (separate units, shared code): the **SQS consumer/renderer** and the **watermark/backfill cron**. This is where the floater poller + meso lanes consolidate off Railway.

### 5.2 The RAM/crop trap (load-bearing)

**Crop the sector BEFORE compositing.** Full-disk true-color compositing can exceed **24 GB** (satpy #1902) — Rayleigh/resample blow up on full-res full-disk arrays. `satellites.py` already **geos-crops before inverse-project** and `assemble_truecolor` operates on **already-cropped** bands, so today's pattern is correct and **must be preserved**. Constraint: **never composite a full disk.** For the new "full-disk" tiled product, render it as a **mosaic of cropped sub-sectors/tiles**, each cropped-then-composited within the per-tile RAM envelope, then assembled — *not* one 24 GB composite. Each unit's peak RSS is bounded and asserted in the heartbeat (`process.peak_rss_mb`).

**The mosaic is concurrency-limited, not array-size-limited.** satpy #1902's 24 GB is a per-invocation dask/Rayleigh/GDAL-cache floor, not a clean function of array size — so cropping bounds *each* tile but 341 crops/cycle do not divide RAM by 341. S1 must **measure the per-tile peak-RSS floor** (incl. pyspectral LUT, Rayleigh intermediates, dask/GDAL overhead), derive **max safe tile-concurrency = (box_RAM − ingest_reserve) / floor**, and meet a stated acceptance number ("one true-color FD tile under X GB, N-way concurrency fits the box, or the FD-mosaic product is not viable on a 16 GB box"). Pin `DASK_NUM_WORKERS` / chunk size in §7.3 so the floor is reproducible. **Seams:** when assembling cropped sub-sectors, crop each with a small **source-pixel halo** sized to the resample kernel (a few px — Rayleigh/sun-norm/tone-curve are per-pixel and tiling-invariant; only the final resample is a neighbourhood op), reproject, then trim before assembly; the per-tile RAM envelope is essentially unchanged. **Render throughput** (independent of dedup): per-sub-sector render-s × sub-sectors/band/frame × bands ÷ cadence vs core count — see §10 and the §8-S2 gate.

### 5.3 Reuse the renderer verbatim

The consumer's inner call is exactly today's `(fetch* → render_png → transcode_frame)`. Two integration choices, both keep the renderer frozen:

- **(A, preferred) in-process import** — the worker imports `render.py`/`satellites.py` directly (no HTTP hop, no rate limiter, lowest latency). `/render` can stay for the on-demand custom-zoom fallback + `/health`.
- **(B) keep `/render` HTTP** — the worker POSTs `/render` over localhost, exactly as the floater poller does today. Zero renderer change; one extra hop.

Either way: **delete the SQS message only after the R2 PUT succeeds; DLQ on poison; the degenerate-frame guard stays.**

### 5.4 Recentering is server-side

Floaters and meso are **recentered on the server** (the crop bbox follows the extrapolated center, as `floater_poller._extrapolate` does today). The viewer receives already-centered frames — no client reprojection, so playback stays the dumb `<img>` swap. The bbox is a **timing-sensitive input** to the by-construction guarantee (§2): prod must **log the bbox per (storm, band, slot)** so the §7.2 shadow render reuses the *same* bbox and the diff isolates the renderer from valid recentering.

### 5.5 Reprojection to EPSG:3857

MapLibre raster XYZ tiles are Web-Mercator; the frozen renderer emits PlateCarree. The chrome-free FD raster is therefore reprojected geos/PlateCarree → EPSG:3857 (GDAL warp / pyresample, bilinear/Lanczos, halo folded into §5.2) **after** the chrome-free render and **before** the XYZ tile-cut — a **second sanctioned renderer-scope exception** beyond chrome (§2, §6.3, §11-B), placed in the §2 diagram and §8 staging, with its per-cycle RAM/CPU in §10. Web-Mercator clips beyond **±85.05°** — a known, gated FD framing delta.

### 5.6 Memory protection & co-tenancy

Crop-before-composite is the *design* bound but observe-only at runtime. Run the render unit under cgroup-v2 **`MemoryMax`** (with `MemoryHigh` ~10–20% lower) sized to reserve a slice for ingest, **`OOMScoreAdjust`** making the renderer the preferred OOM victim, and `MemoryLow`/negative score on the never-miss ingest units — so a runaway composite is OOM-killed in its **own** cgroup and `Restart=always` retries that one job while ingest survives. Reconcile the single-box decision with the repo's HAFS telemetry (peak ~23 GB; one VPS insufficient at peak; render split from pollers): gate on S1's measured floor + reproject — split the FD renderer onto its own box if it won't fit a 16 GB box with headroom.

### 5.7 Security & IAM (blast radius of the consolidated box)

Scope the R2 token to **write+delete on `sat/*` and `shadow/*` only** (never bucket-wide; the box runs prune + lifecycle deletes); keep the viewer/Worker on a **separate read-only** token. NOAA's NODD topics are **public** — the cross-account authz we owe is a queue policy on our queues (`Principal sns.amazonaws.com`, `sqs:SendMessage`, `aws:SourceArn` pinned to the topic ARNs) + minimal IAM (`sns:Subscribe`, `Receive/Delete/GetQueueAttributes` on our queues only) — NOT a NOAA-side resource policy. Store secrets in a `systemd EnvironmentFile` (0600, not in repo — mirroring the `GH_PUSH_TOKEN` discipline) with a rotation plan. **Blast radius of a box compromise:** can wipe `sat/*` + `shadow/*` media.

### 5.8 Failure domain & host-loss

One VPS hosts the SQS consumer + render + floater + meso + FD mosaic; if the host/disk/network dies, **all satellite imagery goes stale until recovery** (`Restart=always` restarts a process, not a dead host; the move drops Railway's managed host migration). Recovery is a documented **cold-rebuild runbook** — the SQS queues + R2 frames survive the box, so a fresh VPS + §3.6 watermark-from-R2 + backfill self-heals — with a **max-staleness SLO**, or an explicit accepted-risk statement for sign-off.

---

## 6. PLAYBACK / VIEWER — keep the look, add zoom

### 6.1 Two playback engines, by product shape

| Product | Engine | Why |
| --- | --- | --- |
| **Floater + meso loops** (single full-frame, server-centered) | **The existing dumb `<img>` playback engine, UNCHANGED** (one `<img>`, preloaded `Image()`, bare rAF + `src` swap, `decode()` gate, single `SAT_LOOP_TOKEN`); the **data layer is reduced to the §4.1 template + `times`** (the URL-derivation/manifest-parse code changes — proven by the §9 sequence/derivation gate, not just the per-frame pixel diff). | It is the zero-visual-change playback *and* the design that survived after the clever canvas player was abandoned. ⚠ **Contradicts the brief's "bespoke canvas + createImageBitmap (Worker) + LRU/.close()" for floater/meso — §11-A.** Recommend: keep the dumb player. |
| **Zoom/pan tiled map** (regional / full-disk pyramid) | **MapLibre GL JS, RasterTileSource** (a tile pyramid — *not* the documented ImageSource `updateImage` idiom, which is a single fixed-corner image with no pyramid/zoom and is rejected because the product requires true multi-zoom pan/zoom; that idiom's job is already covered by the kept dumb `<img>` player + GIBS layers). Animate by **pre-adding one raster source per frame** with **`raster-fade-duration:0`** and toggling `raster-opacity` — *not* `setTiles` (rebuilds/stales the tile cache per frame → stutter). Bound the **concurrent working set** to a small sliding window of frame layers, toggling inactive frames to **`visibility:none`** (not `opacity:0`, which still loads tiles and holds GPU textures — MapLibre owns eviction per-source, offscreen-only, so a same-extent stack never evicts and would accrete unbounded texture residency). **MapLibre flags high per-source render overhead — this working-set bound is load-bearing, do not remove.** Before flipping a frame's opacity 0→1, **gate on readiness** via `Map.isSourceLoaded`/`Map.areTilesLoaded`/the `idle` event (first-class APIs — *not* the `maplibre-preload` plugin); on first-loop/slow-link **HOLD on the prior frame** rather than painting partial/blank tiles (the tiled analogue of the `im.decode()` gate). The per-frame-source + opacity-toggle technique is adapted from weather-radar tile-animation practice (MapLibre's own examples cover only ImageSource animation); it is gated by the §6.3 reference-render diff and the §8-S2 perf criterion. | True zoom/pan over a pre-rendered pyramid; MapLibre is already a proven repo dep (4.7.1). |

### 6.2 Region picker = viewport change over the pyramid

The draw-box / region picker is a **viewport change over the PRE-RENDERED pyramid — no on-demand server render.** Reuse **`TATRegions`** (basin groups, `extentOf`, thumbnail modal) to set the MapLibre camera. ⚠ This **replaces** today's Leaflet-draw → on-demand `/render` arbitrary-bbox flow with a *fixed set of pre-rendered regions* (§11-D); recommend keeping `/render` on-demand as a labelled "custom box" fallback through migration.

### 6.3 The chrome problem for tiles (the crux of "by construction")

The renderer **bakes chrome** (title strip, colorbar, watermark, badge, BT min/max, *and* coastlines/borders/graticule) into one composed figure. A slippy raster map cannot use that as tiles (header/colorbar would tile into the map). The tiled map needs a **map-raster-only** source: the cartopy map content (imagery + **the same** black coastlines/borders + dashed graticule, same theme, same colortables) **without** the title/colorbar/watermark chrome; the chrome becomes an **HTML/MapLibre overlay** matched to the matplotlib chrome (same fonts, colors, text, positions, the credit).

The one place "by construction" weakens, stated plainly:
- **A chrome-free map raster + its `EPSG:3857` reprojection (§5.5) are additive renderer modes** — the sanctioned exceptions beyond chrome, gated by a **per-product pixel-diff** against a reference render of the map area. **Build them in a SEPARATE module** that imports only the pure `tat_palettes` colortables + a stateless imagery-render function and shares **no mutable import-time state** (figure-DPI constants, font cache, rcParams) with `render.py`/`truecolor.py`, so the frozen unit stays genuinely untouched and the floater "by construction" claim stays unconditional. If instead `render.py` is edited in place, the floater guarantee becomes **contingent** on that edit not touching shared state, and §9's S3/S4 pixel-diff is the hard regression gate re-proving floater/meso byte-identity after *every* map-mode change (not only at first cutover).
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
4. **Cutover = env-prefix feature flag** (mirror `WRITE_LIVE_FEEDS`): a `SAT_WRITE_PREFIX` flag flips the **writer** from `shadow/` to prod **per product**, with a **canary %** and a **one-line writer revert** (flip back). The **viewer read-path is a separate one-time cutover** (see the r2 amendments below — the prod key namespace and manifest schema both change, so the reader is *not* a no-redeploy env flip), plus a **CDN cache bust** (purge the prefix).
5. **Decommission only after** prod runs clean on the new pipeline for a defined soak (quantified in the r2 amendment below), per product, gate green throughout.

**r2 migration amendments (folded from the Stage-0 review):**

- **Gate (step 2) — tier it by guarantee.** For the **by-construction products** (floater, meso, custom-zoom) the pass criterion is a **strict-identity gate** (sha256 of the transcoded WebP, or pixelmatch/odiff with threshold-0 and antialiasing-detection OFF) — achievable because step 3 pins libwebp; **any non-zero diff blocks**, matching the §9 "fails loudly" prose. The **0.1% AA-filtered + SSIM-fallback budget applies only to the reference-render-gated tiled map** (§6.3), where no prod baseline exists. Reword §9's "= 0 over budget" to **"zero frames over budget."** SSIM only *classifies* an over-budget frame for triage; it never auto-passes one.
- **Toolchain pins (step 3) — add execution-environment determinism** alongside the package pins: `OMP_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `VECLIB_MAXIMUM_THREADS=1`, `NUMEXPR_NUM_THREADS=1` identically on both hosts (insurance — the pixel path is element-wise + per-pixel LUT, no threaded accumulating reduction). Reconcile step 1's "prod untouched" with step 3's "same pinned image": deploy the **same frozen image to BOTH** the OVH shadow worker and the Railway prod poller ("untouched" → "frozen at the same pin"), OR characterize the host-move floor first by re-rendering historical prod slots on OVH and diffing vs the Railway-emitted WebPs. Soften "any diff is real drift, not env noise" to acknowledge a residual cross-host (CPU/AA) floor that the step-2 budget absorbs. **S1 prerequisite:** prove the OVH box reproduces the Railway box within the gate on a frozen input set *before* the live shadow diff is trusted.
- **Cutover (step 4) — split writer from reader; add the writer hand-off.** The per-product env-prefix flag flips the **WRITER** only (no redeploy). Because the prod key namespace changes (`floaters/…` → `sat/…`) AND the manifest schema changes (`manifest.json` → `latest_times.json`), the **viewer is re-pointed by one coordinated all-products deploy** (viewer rollback = redeploy, *not* an env flip — the `WRITE_LIVE_FEEDS` precedent holds only because its live key is fixed; the serve path is static-CDN-direct, no read proxy). **At the moment a product's write-flag flips, the OLD poller MUST stop writing AND pruning that product's prod keys** (per-product scoped kill-switch, confirmed before the new writer is authoritative) — frame-byte idempotency (§3.3) does NOT cover prune, so the soak runs with the old poller read-only/idle for flipped products. **Manifest-schema sequencing:** during soak either dual-write both schemas at the prod prefix, or ship a dual-schema-tolerant viewer (try `latest_times.json`, fall back to `manifest.json`) before the first flip; an S3 gate item asserts the viewer renders correctly against a **mixed-schema prod**.
- **Rollback recovery model (step 4).** The Railway old poller keeps writing the prod prefix through the soak (read-only/idle for flipped products is fine for *reads*, but it must keep the rollback substrate fresh) — so the §4.3 lifecycle TTL + app-side prune are safe because the recovery target window stays current; rollback = flip the writer back + redeploy the viewer at the old namespace/schema, asserting zero 404 frames after a worst-case TTL/prune interval (a §8 rollback-drill).
- **Decommission (step 5) — define the soak.** Decommission a product **only after ≥14 calendar days AND ≥1 full active-storm cycle** for it, §8 gate green throughout — specifically (i) zero missed slots vs the independent `ListObjectsV2` ground-truth, (ii) zero over-budget pixel-diff frames, (iii) zero DLQ-stuck slots. The Railway old poller stays deployed as the **rollback target** for the entire soak; decommission requires explicit sign-off mirroring the Stage-0 gate. Until that sign-off the one-line writer revert remains live.

---

## 8. STAGING — each stage gated by the visual-regression diff; renderer + colortables + layout untouched

| Stage | Deliverable | Gate |
| --- | --- | --- |
| **S1 — Ingest backbone, ONE product** | SNS→SQS→VPS long-poll + completeness gate + watermark/backfill + DLQ, proven **never-miss on one product** (e.g. GOES-19 meso-2 clean-IR) → `/shadow/`. No viewer change. | Multi-day never-miss audit: zero missed slots vs an independent ListObjectsV2 ground-truth; shadow frames pixel-identical to prod for that product. |
| **S2 — Tile viewer** | MapLibre raster map + `latest_times.json` SSOT + opacity-toggle animation over S1's pyramid. | Reference-render diff of the map view (§6.3), explicitly diffing **mosaic seam lines** against a single-pass crop; never-miss holds; one full FD cycle (composite all sub-sectors + reproject + cut z0–4 + transcode, all bands) **completes in < FD cadence with headroom**, the concurrent floater 60 s loop **shows no degradation**, and pan/zoom interactivity stays smooth with the full frame set on a mid device (else fall back to single-image `updateImage`). |
| **S3 — Floaters rebuilt** | All 6 floater bands re-ingested through the new pipeline → `/shadow/floaters/…`, **same renderer, same single-frame WebP, same dumb player.** | **Pixel-identical** shadow-vs-prod across every floater band/frame (the by-construction guarantee, enforced); the new template+`times` derivation yields the **identical ordered URL sequence + `latest` pointer** as today's manifest for a captured slot set (per-frame pixel identity does not prove frame-order identity); the shadow render uses the **bbox prod logged** for each (storm, band, slot) so the diff isolates the renderer from timing-dependent recentering; plus the **frame-coverage** audit per §3.3 (published frames vs the expected `(storm × band × slot)` set, not raw objects). |
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

A non-zero diff on any "by construction" product is a **real bug in the wrapper** (wrong inputs/crop, a non-pinned dep) — the gate fails loudly (strict-identity tier, §7 r2 amendment), not a tolerance to widen.

### 9.x Logic testing (beyond pixels) — a per-stage gate alongside the pixel diff

Pixel-diff cannot prove the gate didn't schedule a partial render, that backfill caught a dropped SQS event, that a duplicate was a no-op, or that cold-start bootstrapped correctly — those frames look identical to correct ones. Require deterministic unit/integration tests (moto/localstack for SNS/SQS, moto/minio for R2 — the existing tsr moto harness + the `python -m unittest discover tests` convention) for: the **completeness-gate truth table** (1-file MCMIP / 5-band CMIP true-color / AHI segments×bands / missing-band / late-band); **ledger accounting**; **backfill reconcile** (inject a dropped event → assert enqueue; inject a duplicate → assert no-op PUT); **idempotent-key derivation**; **DLQ-after-`maxReceiveCount`**; **cold-start bootstrap** from an empty ledger (§3.6). The S1 `ListObjectsV2` audit stays as the live confirmation layer *on top* of these, not the sole correctness check (the enscenters `reconcile()` regressions are the precedent that this logic ships with bugs).

---

## 10. Cost & capacity sketch

- **VPS:** one always-on 8–16 GB box; peak RSS bounded *by design* by crop-before-composite and *at runtime* by a cgroup `MemoryMax` (§5.6); SQS long-poll + backfill cron under systemd. The FD-mosaic renderer co-resides with ingest **only if** S1's measured per-tile RAM floor + wall-clock fit with headroom — else split it onto its own box (§5.6, the HAFS precedent).
- **R2 Class-A PUTs (worst case, confirm hit-rate in S1):** floaters ≈ Σ storms × 6 bands × frames/day (dedup suppresses only static frames — §4.2); meso ≈ sectors × bands × cadence; the tiled full-disk is the risk — `341 tiles × 144 frames/day (@10-min) × tiled-bands × sats`. A **single tiled FD band already ≈ 1.47M PUT/mo, over the 1M/mo Class-A free tier** (at **$4.50/M**) — "the free tier covers it" is not assumable; **z0–4 + render-on-change + TTL** bound it; a z0–8 every-10-min pyramid is explicitly excluded.
- **R2 Class-A LIST:** the backfill reconcile = `Σ ticks/day/product × products` (recent-prefix scan, ~1 op/tick) at $4.50/M — trivial but budgeted; the viewer never lists R2.
- **Render throughput** (independent of dedup — dedup suppresses the PUT, not the render): per-sub-sector render-s × sub-sectors/band/frame × bands ÷ cadence vs cores — the FD-mosaic wall-clock budget (§5.2), gated by §8-S2.
- **Local disk:** per-FD-slot NetCDF/segment staging × consumer concurrency + ledger growth + tile staging (confirm in S1); delete source after render+PUT; a `disk_free_mb` heartbeat assertion + alert fires *before* the gate keeps saying "go."
- **Client (tiled animation only):** *viewport-bound* tiles/frame (visible tiles at active zoom, **not** the 341 pyramid figure) × frames = GETs + peak resident decoded-bitmap memory on a mid mobile device (Mobile Phase B), with a ceiling + fallback to single-image `updateImage` / `/render`. Floater/meso (one `Image()`/frame) unaffected.
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

**I. Render memory protection, wall-clock, and seams on a shared box.** The FD-mosaic renderer (§5.2) co-resides with the never-miss ingest. (1) `peak_rss_mb` is observe-only — add a cgroup `MemoryMax`/`OOMScoreAdjust` hard ceiling (§5.6) so a runaway composite is killed in its own cgroup, not the ingest co-tenant. (2) Wall-clock is unbudgeted: hundreds of sub-sector composites + reproject + tile-cut every 10 min contend with the 60 s floater/meso loops — S2 must prove one FD cycle fits cadence without degrading the hot loop; cite the HAFS "one VPS insufficient at peak" precedent and the descope levers. (3) The mosaic needs a source-pixel resample halo to avoid tile-edge seams.

**J. Observability & alerting (§3.5).** The only alerting in r1 was "a slow alarm." Every signal is a producer with no consumer — `latest_times.json` `as_of` staleness, DLQ depth, queue age, filter-out spikes, heartbeat absence, disk-free must each page someone, or "auditable, never false-green" is false on the consumer side.

**K. Security & IAM blast radius (§5.7).** One box holds a prod R2 write+delete token + cross-account SQS access + the Worker/feed paths. Scope the R2 token to `sat/*`+`shadow/*`, separate read-only token for the viewer, minimal SQS IAM + our queue policy (NODD topics are public), secret storage + rotation. Name the box-compromise blast radius.

**L. Failure domain / host-loss (§5.8).** Single VPS = SPOF for all sat imagery; `Restart=always` does not cover a dead host. Recovery = cold-rebuild runbook (queues + R2 survive; §3.6 self-heals) + a max-staleness SLO, or accepted-risk sign-off.

**M. Burst backpressure & priority.** GOES Mode-6 publishes are synchronized (FD+CONUS+2 mesos coincide every 10 min, each ×16 bands). One box + `Semaphore(2)` gives no priority — a 10-min FD mosaic can head-of-line-block the 60 s hot loop, and a backed-up queue surfaces only as a stale timestamp. Need a throughput budget, a hot/cold priority scheme (the `webp-frames-meso` precedent), a shed policy, and queue-depth alerts (flag J).

**N. Logic testing & tiled-client weight.** (1) The new gate/ledger/backfill/idempotency/DLQ/cold-start logic — the part most likely to have bugs (cf. the enscenters `reconcile()` regressions) — has no test plan; pixel-diff structurally cannot exercise it (§9.x). (2) The MapLibre per-frame-source animation is far heavier than the dumb player; bound the client decode/memory/GET budget (§10) and the texture-residency working set (§6.1).

---

## 12. Explicitly NOT in scope at Stage 0

Nothing is built, deployed, or branched. No renderer edit. No R2 write. No SQS/SNS resource. No viewer change. This document + the adversarial-review findings are the only outputs; both are for sign-off.

---

## 13. Revision log

- **r1 (this draft)** — first full design from ground-truth of `tat-satellite-render@main` + `satellite/index.html`.
- **r2** — folds the Stage-0 adversarial review (`SATELLITE-REVIEW.md`). 46 findings across 6 dimensions + a completeness critic, each skeptic-refuted; 2 refuted (INGEST-4 s-time grouping is fact-correct; MAPLIBRE-2 rested on a fabricated doc claim + inverted bug), 44 survived (0 critical after refutation, ~16 major). Gate verdict: **proceed with required changes.** Net deltas beyond §11 A–H: **INGEST** — SNS filter must be `FilterPolicyScope=MessageBody` or it matches nothing (the primary trigger was unwired by default); a derived per-storm-crop never-miss ledger/audit distinct from the raw-slot ledger; SNS payload-filter cost; observability (§3.5) + cold-start bootstrap (§3.6). **CUTOVER** — per-product writer hand-off (old poller stops writing+pruning at flip); writer/reader split (the namespace+schema change makes the viewer revert a redeploy, not a one-line flip); manifest-schema sequencing; rollback recovery model; concrete soak definition; gate tiered (strict-identity for by-construction, 0.1%-AA+SSIM only for the tiled map). **RAM/COST** — `EPSG:3857` reprojection named as a second sanctioned renderer exception (§5.5); per-tile RAM floor + concurrency, render wall-clock, cgroup `MemoryMax` (§5.6), seam halo, PUT/LIST/disk/client-decode budget lines (§10). **MAPLIBRE** — `LRU-evict offscreen` is a category error → sliding-window `visibility:none`; readiness gate; `updateImage` ruled out (no pyramid). **COMPLETENESS** — new §11 flags I–N (memory/wall-clock/seams, observability, security/IAM §5.7, host-loss SPOF §5.8, backpressure, logic-testing §9.x). §2's by-construction claim refined: "identical decoded pixels," crop bbox is a captured timing-sensitive input, renderer-extension exceptions broadened to chrome + reproject. The architecture (frozen renderer, event-driven never-miss, manifest SSOT, shadow-first pixel-diff cutover) is sound and approved in principle; r2 captures the deltas on top of the doc's already-mature §11.
