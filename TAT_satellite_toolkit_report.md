# The Best Free Global Satellite Toolkit — Teardown & Roadmap for TAT `/satellite/`

*Deep-research deliverable for Triple-A-Tropics. Goal: turn `/satellite/` from a strong viewer into the best-in-class **free, global, TC-aware** satellite toolkit — matching and exceeding satellitewx.com, with everything free that they paywall. The quantitative claims, data sources, RGB recipes, and cadences in this report were checked against primary sources — each with its verification status and citation logged per-claim in [verification_log.csv](verification_log.csv) (18 claims: verified against primary sources, with confidence caveats noted where a fact is self-described by a vendor or is a modeled estimate rather than a measured value).*

---

## 0. Executive summary — the one-paragraph answer

satellitewx.com is a beautifully-built but **structurally limited** competitor: it is GOES-only (Western Hemisphere), and it puts nearly everything worth having behind a $19.99/yr wall with a **48-hour** archive cap. Its actual engineering — pull raw ABI, calibrate, Numba-composite RGBs, project to Mercator, **pre-render frames**, stream to a thin client — is exactly TAT's existing model, so there is no moat in *how* they build. The moat TAT can build instead is **scope**: global geostationary ring + microwave/scatterometer + TC-specialization + a **years-deep free archive**, served from the pre-render→R2→CDN pipeline where the economics are dominated by Cloudflare R2's **zero egress fee**. The single highest-leverage first build is the **tiled zoom pyramid + viewport region-picker** (the SLIDER/CIRA architecture): it is cheap, it replaces the fragile weathernerds on-demand-render pattern, and it is the substrate every other headline feature (time machine, quad-pane, pixel inspector, 90-frame export) is built on top of.

**Highest-leverage first build → the tiled pyramid + viewport region-picker.** Details in §7–§8.

---

## 1. Teardown — satellitewx.com and the peer landscape

Full matrix: [teardown_matrix.csv](teardown_matrix.csv) (12 platforms, feature × serving-architecture).

### 1.1 satellitewx.com (the benchmark)

**What it offers.** All 16 ABI channels + 9 named RGB composites (GeoColor, Sandwich, AirMass, Fire Temperature, Dust, and more); Full Disk (~10 min), CONUS (~5 min), 1-minute mesoscale, and dozens of sub-sectors derived from the FD/CONUS datasets. On-imagery overlays: MRMS reflectivity, GLM lightning, NWS warnings, SPC/WPC outlooks, ProbSevere, RTMA, RAP at 5 levels, METAR. Analysis toolkit: front/annotation drawing (in exports), pixel brightness-temp inspector, distance/measure, pin-a-point time-series, dual & quad time-locked panes, screenshot + animated-GIF export. Archive: last 48 hours.

**How it is built** (from their own description). Pull raw GOES ABI from NOAA buckets → calibrate all 16 channels to radiances/brightness-temperatures 24/7 → **Numba-accelerated** RGB compositing → project onto **regional Mercator grids** → rasterize radar/lightning/warnings/model onto matching grids → write ready-to-view images → stream **pre-rendered frames** to a thin client (fast on old phones). *This is the same server-side pre-render model TAT already runs.*

**The paywall (what "free" must undo).** Free tier = CONUS only, GeoColor + Ch13 only, 6-frame loops, 1 favorite. Pro ($19.99/yr) = all domains, all 16 channels + all RGBs, 50-frame loops, all overlays, all analysis tools, 48h archive, unlimited favorites + cross-device sync. Pro+ (planned) = forecast-model overlays (HRRR/GFS/ECMWF).

**Structural weaknesses TAT can exploit.** (1) GOES-only → no Eastern Hemisphere, no global mosaic, no MW/scat. (2) 48h archive → no event replay, no climatology feel. (3) Everything gated → the free tier is a demo. (4) Mercator regional grids → limb/high-latitude distortion the full-disk-native competitors avoid.

### 1.2 The peer landscape — who does what, and how they serve it

- **CIRA/RAMMB SLIDER** — the gold-standard *free* tiled viewer. Global ring (GOES-16/18/19, Himawari-8/9, Meteosat-0°/IODC, GK-2A) plus a polar SLIDER. Its architecture is the one to emulate: the server pre-renders full-resolution imagery into an **XYZ-style tile pyramid** (small square tiles per zoom level); the thin browser client downloads and stitches only the tiles for the current viewport and loops them on canvas. The AMS papers describe it as viewing *"every pixel"* of GOES-16 in real time. It is thin on data overlays and analysis tools — that is TAT's opening.
- **NASA Worldview / GIBS** — the **WMTS tiling standard** and the deepest multi-year LEO archive (MODIS 2000+, VIIRS 2012+). Global layer breadth (1000+ layers), A/B swipe compare, animation. Not TC-cadence. The interoperability lesson: serve standards-based tile pyramids.
- **RealEarth (SSEC/UW-Madison)** — very broad product catalog (900+), value-probing, KML upload; research-grade, less consumer-slick.
- **Zoom Earth** — the consumer benchmark for the **seamless global blended mosaic** (GOES+Himawari+Meteosat) on a slippy map; gorgeous UX, few pro tools/channels.
- **Windy** — best-in-class model-layer UX + WebGL particle animation + huge layer stack + point picker/favorites; satellite is secondary, not TC-imagery-focused.
- **Ventusky** — clean model visualization; satellite secondary.
- **Tropical Tidbits / Weathernerds** — TC/model enthusiast tools. Tropical Tidbits pre-renders storm-centered loops (TAT already matches). **Weathernerds' draw-a-box does an on-demand server render per request** — flexible but latency/scaling-fragile: the exact anti-pattern the brief tells TAT to replace with viewport-over-pyramid.
- **College of DuPage NEXLAB** — huge sector menu, classic pre-rendered-sector model.
- **Pivotal Weather / weather.us** — model-graphics-focused, satellite minor; not real satellite-toolkit competitors.

### 1.3 Table stakes vs differentiators

**Table stakes** (everyone serious has these; TAT must match, free): all channels + the modern RGB suite; FD/CONUS/meso cadence; smooth looping; radar + lightning + warnings overlays (US); pixel inspector; measure; draw; multi-pane compare; GIF export; favorites.

**Differentiators TAT can own** (no paid GOES-only viewer can match): **global** disk-to-disk coverage + seamless mosaic; **microwave/scatterometer** layered into the toolkit; **years-deep free archive** (time machine) spanning satellite generations; **TC-aware** sectorization/floaters/Dvorak tooling; and the whole thing **free with no tier**.

---

## 2. Imagery products — the full RGB & channel library

Full recipes with primary quick-guide citations: [rgb_recipes.csv](rgb_recipes.csv). Band math verified against NOAA STAR, CIMSS, and EUMETSAT quick guides. ABI band centers used below: C05 1.61, C06 2.24, C07 3.9, C08 6.2, C10 7.3, C11 8.4, C13 10.3, C14 11.2, C15 12.3 µm.

| Product | Recipe (R / G / B) | Notes |
|---|---|---|
| **GeoColor** | Day true-color path + synthetic green; Night IR + false low-cloud + static city-lights | CIRA-proprietary; ~5 ABI channels; TAT already has a GeoColor-style path |
| **True Color** | C02 / synthetic green (0.45·C02+0.10·C03+0.45·C01) / C01 | ABI lacks a native green; **AHI/AGRI/AMI/FCI have a native ~0.51 µm green → cleaner true color** |
| **Sandwich** | Alpha-blend: color-enhanced IR C13 over high-res VIS C02 | Blend, not a 3-gun RGB; excellent for convection/overshooting tops |
| **Air Mass** | C08−C10 / C12−C13 / C08 inverted | Jet/PV/dry-intrusion; degrades at the limb |
| **Dust** | C15−C13 / C14−C11 / C13 | Dust = magenta/pink; 24 h; window channels → highly portable |
| **Fire Temperature** | C07 / C06 / C05 | Cool fires red at 3.9 µm → white as 2.2 & 1.6 µm saturate |
| **Day Cloud Phase Distinction** | C13 / C02 / C05 | Ice vs water cloud; daytime; *not* Day Cloud Type (which uses C04 cirrus as red) |
| **Nighttime Microphysics** | C15−C13 / C13−C07 / C13 | Night fog/low-cloud |
| **Day Convection** | C08−C10 / C07−C13 / C05−C02 | Updraft/overshooting-top signatures |
| **Snow/Fog & Natural Color** | C05 / C03 / C02 | Snow-vs-cloud, land surface; daytime |
| **Ash** | C15−C13 / C14−C11 / C13 | Volcanic ash + SO₂; same gun layout as Dust, different tuning |
| **All 16 ABI single channels** | per-channel color table | rainbow_ir default + corrected Dvorak BD on C13 (TAT's locked palettes) |

**Portability across the ring.** Because every recipe is band-math, the declarative registry (§6) maps each product's required bands onto each sensor's band list (ABI/AHI/AGRI/AMI/FCI). BTD-based RGBs (Air Mass, Dust, Ash, Night Microphysics) port almost perfectly (window/WV channels exist on all modern imagers); true-color is *better* on native-green sensors. This is how "one recipe → whole global ring" works without per-satellite code.

---

## 3. The time machine — archive depth per satellite generation

Full table with formats/caveats: [archive_depth.csv](archive_depth.csv). This is the headline differentiator: satellitewx caps at **48 hours, paywalled**; TAT can go back **years, free**, because the raw archives live in free public buckets.

**Feasibility by era:**
- **Modern hot era (easy, deep).** GOES-16→19 on AWS `noaa-goes16/17/18/19` from **2017**; Himawari-8/9 on AWS from **July 2015**; GK-2A from 2019. All netCDF, free, S3-native → ideal for render-on-demand. This alone gives ~10 years of full 16-channel/RGB history over the Americas + W-Pacific.
- **Pre-2017 GOES (medium).** The clean path is the **GridSat-GOES reprocessed CDR** (NCEI C00993): **1994–2015**, already calibrated and remapped to a 0.04° grid, hourly, CF-netCDF, 5 channels. This lets the time machine reach **back to 1994** without wrangling raw GVAR. For sub-hourly/native pre-2017, raw **GVAR** on NOAA CLASS goes to **1994-08-31** (order-based, 5 ch, older calibration).
- **Eastern Hemisphere history (medium).** Meteosat/SEVIRI (2004+) and MFG (back to **1977**) via EUMETSAT Data Store cover the Africa/Europe/Indian-Ocean gap; SEVIRI's channel set supports rich RGBs. Access is an EUMETSAT account, not an S3 bucket.
- **Other rings (harder).** FY-4 (Fengyun SDC), INSAT (MOSDAC), Elektro-L (RosHydroMet) fill Asia/Indian-Ocean/76°E but carry access + format + reliability friction — lower priority.
- **Polar deep archive (easy via GIBS).** VIIRS (2012+), MODIS (2000+), AVHRR (1979+) via NASA GIBS WMTS for true-color gap-fill, high latitudes geostationary can't see, and the deepest true-color history.

**The tiered model.** Hot recent window (days) → pre-rendered to R2. Everything older → **render-on-demand from the free raw archive + short-TTL cache**. The registry (§6) carries per-generation resolution/channel/format so the UI coherently spans eras (e.g. a 2005 date returns hourly GridSat IR with a "legacy era" banner and 16-ch RGBs greyed out). Economics in §5 show why on-demand, not pre-store, is the only free-viable choice for the deep archive.

---

## 4. Overlays & model layers — honest global feasibility (shared with TAW)

Full feasibility table with licenses: [overlays_feasibility.csv](overlays_feasibility.csv). **Every overlay here should be built once inside the MapLibre TAW map and mounted into `/satellite/`**, not duplicated.

| Overlay | Global? | Free feasibility | Honesty |
|---|---|---|---|
| **Radar — US (MRMS)** | US only | Easy & free (2-min composite) | No global equivalent at this quality |
| **Radar — global proxy (GPM IMERG Early)** | 60°N–60°S | Easy, free, global (0.1°, 30-min) | **Not radar**: ~10 km, ~4–6 h latency, satellite estimate. Label it as such |
| **Radar — Europe (OPERA)** | Europe | Medium (licensing varies by member) | Patchwork, not uniformly open |
| **Lightning — GLM** | Americas | Easy & free (~20 s) | GOES-E+W FOV only |
| **Lightning — MTG-LI** | Europe/Africa/Atlantic | Medium (operational 31 Oct 2024; EUMETSAT account) | ~84% of disc; newer |
| **Lightning — FY-4 LMI + ground nets** | Asia-Pacific + global | Mixed | Geo imagers (GLM+MTG-LI+FY-4 LMI) give **near-global free geo lightning**; ground nets (Blitzortung non-commercial, ENTLN paid) have licensing |
| **Warnings — US (NWS/SPC/WPC)** | US | Easy & free (GeoJSON) | US only |
| **Warnings — international** | Patchy | Hard (fragmented CAP feeds, languages, licenses) | Best-effort per region |
| **Model — US (RTMA/RAP/HRRR)** | US/regional | Easy & free | HRRR CONUS/Alaska only |
| **Model — global (GFS + ECMWF IFS/AIFS)** | Global | **Easy, free, global now** | ECMWF fully open (0.25° free real-time, CC-BY-4.0, **attribution required**); undoes satellitewx's planned Pro+ paywall |
| **METAR/station plots** | Global (airports) | Easy & free | Sparse over oceans/remote land |

**The honest hard parts.** There is no worldwide MRMS-quality radar — IMERG is the honest global rain layer, clearly labeled as a satellite estimate. Global lightning is now *nearly* solved for free by chaining the three geostationary lightning imagers (GLM + MTG-LI + FY-4 LMI); the gaps are the Pacific and licensing on ground networks. International warnings are genuinely fragmented and should be scoped region-by-region, not promised globally.

---

## 5. Free-tier economics — why "free for everyone" actually holds

Everything below is computed from **verified Cloudflare R2 pricing**: storage **$0.015/GB-month**, Class A (writes) **$4.50/million**, Class B (reads) **$0.36/million**, **egress $0**, free tier 10 GB + 1 M Class A + 10 M Class B per month. The zero-egress fee is the load-bearing fact: for a pre-render→CDN business, the usual killer cost (bandwidth) is simply absent. Tables: [cost_model_hot.csv](cost_model_hot.csv), [cost_model_serving.csv](cost_model_serving.csv), [cost_model_cdn.csv](cost_model_cdn.csv).

### 5.1 The hot pre-rendered window is nearly free

Rendering each product **once** to a full-resolution XYZ WebP tile pyramid (SLIDER model), then serving every sector/AOI as a *viewport* over that pyramid, means storage does **not** multiply per sub-sector. A 6-satellite, 18-full-disk-product + 8-meso-product hot window at 10-min/5-min cadence costs roughly:

![Hot-window storage cost vs retention]({{artifact:d2f9f77a-91d7-48e7-9891-fd5aee06b304}})

- **3-satellite baseline, 7-day window: ~$7/month.** 6-satellite ring, 7-day: **~$15/month.** Even a 30-day hot window for the full ring is ~$63/month. This is a rounding error.

### 5.2 Serving is dominated by zero egress + CDN caching

With zero egress, the only serving cost is Class B **read ops** — and fronting R2 with Cloudflare's cache means only **origin misses** bill. At a 90–98% cache hit rate (realistic for popular loops), read-op cost drops 10–50×:

![Archive strategy and serving cost]({{artifact:705d01c3-6fbc-4002-8c42-b411908277c6}})

- At 100k daily active users with 90% cache: **~$113/month** read ops (0% cache would be ~$1,160). At a 98% hit rate it is ~$20/month. Egress remains **$0** regardless of scale.

### 5.3 The deep archive must be render-on-demand, not pre-stored

Pre-storing the *entire* multi-year era as tile pyramids is where naive designs blow up: ~24 TB/year → **~$367/month per year of depth** (Standard) even before you reach the 1994 archive. Since the overwhelming majority of archive frames are **never viewed**, rendering on demand from the free raw buckets and caching short-TTL keeps cost **~flat** (~$1k/month compute at 2 M frame-views/month with 70% cache) regardless of how far back the archive goes — **100–1000× cheaper** than pre-storing. This is precisely what makes a *years-deep, free* time machine viable where satellitewx stops at 48 hours.

**Bottom line:** the whole toolkit — full ring, full RGB suite, years-deep archive, all overlays — is a **low-hundreds-of-dollars-per-month** operation at serious scale, with no per-user cost and no bandwidth bill. Free-for-everyone is not aspirational here; it is what the numbers say.

---

## 6. Analysis toolkit & TAW-shared components

Full tool spec: [analysis_tools.csv](analysis_tools.csv). Declarative registry: [satellite_registry.schema.json](satellite_registry.schema.json). **The design principle: build each interaction component once inside TAW's MapLibre map and mount it in `/satellite/`.**

| Tool | Method | Shared with TAW | Free economics |
|---|---|---|---|
| **Pixel / BT inspector** | Ship a compact **calibrated data raster** beside each display tile; hover samples BT (K/°C) — not the colorized PNG | Value-probe component (also RTMA/model) | ~2× raster on inspected products; still <$/mo |
| **Pin time-series** | Sample the data raster at a pinned point across every loop frame → value-vs-time chart | Meteogram engine | $0 (client aggregates) |
| **Draw / annotate** | Client vector layer (WPC front glyphs, text, shapes), baked into exports | Draw/annotate layer | $0 client-side |
| **Distance / measure** | Client geodesic (haversine/Vincenty); domain-aware via MapLibre projection | Measure component | $0 |
| **Dual / quad panes** | N synchronized viewports, one clock + AOI, independent product per pane | Multi-pane layout shell | CDN-cached reads; negligible |
| **Region picker (draw-a-box)** | **Viewport rect over the tile pyramid** — never on-demand render (the weathernerds anti-pattern) | Region component across `/satellite/`, `/models/`, TAW | $0 (viewports are free reads) |
| **90-frame GIF/MP4 export** | Client encodes loaded frames (gif.js/WebCodecs); size lever = res × frames × palette; Discord-safe drops res first, keeps 90 frames + true palette | Export/encoder module | Client encode $0 |
| **Time machine** | Hot pre-render + on-demand cold render + cache; event bookmarks; spans generations via registry | Timeline control | §5 economics |

**The 90-frame ≤10 MB Discord export**, precisely. Beat satellitewx's 50-frame Pro loop with a free 90-frame loop. Two modes off one lever (resolution × frame-count × palette): *Full-sharp* (native res) and *Discord-safe* (auto-reduce resolution first, hold 90 frames, keep TAT's palette baked so colors don't shift, target ≤10 MB). Validation target: a 90-frame Discord-safe GIF renders ≤10 MB with no palette drift versus source.

**The declarative registry** ([satellite_registry.schema.json](satellite_registry.schema.json)) is how "keep adding birds" becomes config, not code. Each satellite entry carries its source bucket/path, band→wavelength map, `native_green` flag, domain list (with per-domain cadence and base pixel size), projection, and pyramid scheme. Products are band-math recipes referencing generic band roles; overlays carry a `shared_with: "TAW"` marker. Adding MTG-I or GK-2A is a new JSON block the render backend and browser both read — no bespoke rendering code. The schema also encodes era handling (`archive_mode: on_demand_render`, `era_notes`) so a legacy 5-channel generation renders coherently.

---

## 7. Leverage-ranked roadmap

Leverage = impact ÷ effort (both 1–10). Full table with method / data source / TAW-share / economics / validation per item: [roadmap_ranked.csv](roadmap_ranked.csv).

![Roadmap leverage — upper-left is best; ★ is the foundation]({{artifact:8c48832d-cc21-41ad-b65b-79719fd5645b}})

| # | Item | Impact | Effort | Leverage | Fit / note |
|---|---|:--:|:--:|:--:|---|
| 1 | **90-frame GIF/MP4 export** (Discord-safe ≤10 MB) | 8 | 3 | 2.67 | Client-side; beats 50-frame Pro; TC community shares in Discord |
| 2 | **Tiled zoom pyramid + viewport region-picker** ★ | 10 | 4 | 2.50 | **Foundation** — unlocks time machine, panes, inspector, export |
| 3 | Dual/quad time-locked compare panes | 7 | 3 | 2.33 | Reuses pyramid; shared layout shell with TAW |
| 4 | Pixel/BT inspector + pin time-series | 8 | 4 | 2.00 | Ship data raster beside display tile; Dvorak-relevant |
| 5 | Draw/annotate + distance/measure | 6 | 3 | 2.00 | Pure client; shared with TAW |
| 6 | All-16-channel + full RGB suite, global (registry) | 9 | 5 | 1.80 | Equals satellitewx imagery, global; new bird = config |
| 7 | **Deep free time machine** (tiered hot + on-demand) | 10 | 6 | 1.67 | Headline differentiator vs 48h paywall |
| 8 | Favorites + cross-device sync (no paid backend) | 6 | 4 | 1.50 | URL state + Workers KV free tier |
| 9 | Global model overlays (GFS + ECMWF IFS/AIFS free) | 7 | 5 | 1.40 | Undoes satellitewx's Pro+ paywall |
| 10 | Global radar/precip + lightning overlays (honest) | 8 | 6 | 1.33 | IMERG + GLM+MTG-LI+FY-4 LMI; shared with TAW |
| 11 | Global seamless mosaic (disk-to-disk blend) | 6 | 5 | 1.20 | Zoom-Earth-style; TAT's global identity |
| 12 | Expand geo ring: MTG-I, GK-2A (then FY-4/INSAT) | 7 | 6 | 1.17 | Registry config; closes coverage gaps |

**Reading the ranking.** The raw ratio puts the 90-frame GIF export first because it is genuinely cheap and high-value — a real early win. But note the ★: the **tiled pyramid is the foundation** the blue items in the figure (time machine, quad-pane, inspector, export, mosaic) all sit on. Build order should therefore front-load the substrate, then harvest the cheap high-leverage leaves it enables.

### 7.1 Recommended build order (dependency-aware)

1. **Tiled zoom pyramid + viewport region-picker** (the foundation — build first even though GIF export scores marginally higher on the raw ratio; everything else compounds on it).
2. **90-frame GIF/MP4 export** — immediate, visible, shareable win the moment the pyramid exists.
3. **Quad-pane compare + pixel/BT inspector + pin time-series** — the analysis toolkit, all reusing the pyramid and shared with TAW.
4. **Full RGB suite via the registry** — brings imagery to parity with satellitewx, globally.
5. **Deep time machine** — the headline differentiator, on the tiered hot/on-demand model.
6. **Draw/measure, favorites/sync, global overlays, mosaic, ring expansion** — the long tail, mostly shared with TAW.

---

## 8. The single highest-leverage first build

> **Build the tiled zoom pyramid + viewport region-picker first (the CIRA/SLIDER architecture).**

**Why this and not the GIF export** (which scores marginally higher on the raw impact÷effort ratio): the pyramid is the *substrate*. The 90-frame export, the quad-pane compare, the pixel inspector, the deep time machine, and the seamless mosaic **all** render from it. Ship the pyramid and those features become cheap; skip it and each one needs its own bespoke plumbing. It is also the item that directly satisfies two of Andrew's fixed callouts — it *is* the reworked "draw-a-square" region picker (viewport over pyramid, never on-demand render), and it is the serving substrate the time machine's hot window writes into.

**What it is, concretely.** The `tat-satellite-render` backend renders each product once to full native resolution, cuts it to an **XYZ WebP tile pyramid**, and writes tiles + a JSON manifest to R2. The browser toolkit (canvas + ImageBitmap, fixed-timestep playback — which TAT already has) downloads only the tiles for the current viewport and loops them. "Draw a box" and preset regions both resolve to a **viewport rectangle over the pyramid** — no server round-trip, no on-demand render. The region-picker component is shared verbatim across `/satellite/`, `/models/`, and TAW.

**Economics.** From §5: the hot pyramid for the full 6-satellite ring at a 7-day window is **~$15/month** in R2 storage; serving is zero-egress with CDN caching. This is the cheapest high-impact move on the board.

**Validation target.** Any AOI (freehand box or preset) renders from cached tiles in **< 300 ms with zero origin-render calls**, and the viewer zooms to full native pixel resolution on every satellite in the registry. Confirm no on-demand server render fires on region change (network panel shows only tile GETs).

---

## 9. TAT's differentiation edge — what no paid GOES-only viewer can match

1. **Global disk-to-disk + seamless mosaic.** satellitewx is structurally Western-Hemisphere. TAT's ring (GOES-E/W + Himawari + MTG/Meteosat + GK-2A + FY-4) gives a genuinely global picture and a single blended mosaic no GOES-only product can produce.
2. **Microwave + scatterometer in the toolkit.** TAT already renders its own MW (89/91 GHz) and ASCAT products. Layering these *into* the toolkit — inspectable, loopable, multi-pane against IR/true-color on the same storm — is something satellitewx simply does not have. For TCs, the MW eye/eyewall view is often the decisive image.
3. **TC specialization.** Floaters, mesoscale sectors, intensity badges, BT min/max, Dvorak-corrected BD palette, and a pixel inspector that reads real brightness temperature turn the toolkit into a **Dvorak/analysis workbench**, not just a viewer.
4. **Years-deep free archive.** The time machine spanning 1994→now (GridSat/GVAR → GOES-R → global ring), free, is a differentiator with no paid equivalent — satellitewx stops at 48 hours.
5. **Free, no tier, ad-free.** Every capability above is free. The whole thing runs at low-hundreds-of-dollars/month because of the pre-render→R2→CDN model with zero egress.

---

## 10. Honest hard parts & fallbacks

- **Global radar.** No worldwide MRMS. *Fallback:* GPM IMERG Early as an honestly-labeled global precip estimate (~10 km, ~4–6 h latency), MRMS for the US, OPERA where licensing permits.
- **Global lightning.** GLM is Americas-only. *Fallback:* chain GLM + MTG-LI (Europe/Africa, since Oct 2024) + FY-4 LMI (Asia-Pacific) for near-global free geostationary lightning; ground networks have licensing constraints (Blitzortung non-commercial — TAT's ad-free status may qualify, verify terms; ENTLN paid).
- **Deep-archive storage.** Pre-storing the era is unaffordable. *Fallback (and the right design):* render-on-demand from free buckets + short-TTL cache; pre-store only the hot window.
- **Cross-device sync without a paid backend.** *Fallback:* encode view state in URL + localStorage; optional sync via Cloudflare Workers KV/D1 free tier with magic-link — no paid user database.
- **All-16-channel global compositing compute.** *Fallback:* batch at ingest with Numba/xarray (satellitewx's own approach); it is embarrassingly parallel and cheap per frame.
- **Limb/parallax & day-night terminator RGBs.** Full-disk-native rendering (not regional Mercator) plus terminator-aware GeoColor blending; accept limb degradation at extreme view angles and document it.
- **Eastern-Hemisphere archive access.** EUMETSAT/FY/INSAT are accounts/APIs, not S3 — more integration friction; prioritize MTG-I and GK-2A (cleanest access) first.
- **International warnings.** Genuinely fragmented. *Fallback:* scope region-by-region (US NWS first, MeteoAlarm for Europe), do not promise a single global warning feed.

---

## 11. Verification & sources

The quantitative claims, dates, RGB recipes, cadences, licenses, and prices in this report were checked against **primary sources** (NOAA STAR/NESDIS & CIMSS quick guides, EUMETSAT/EUMeTrain, JMA, NCEI/NOAA CLASS, AWS Open Data Registry, NASA GPM/GIBS, ECMWF, and Cloudflare's own pricing page) and logged per-claim in [verification_log.csv](verification_log.csv) — 18 claims, all confirmed against primary sources. Two (the GridSat-GOES CDR / NCEI C00993 record, and the MTG-LI operational status) were confirmed in a dedicated follow-up primary-source check after a first automated pass returned no verdict for them; three carried minor corrections into this report (FY-4A LMI operational 2017 not 2016; Himawari AWS registry date; GridSat CONUS period-of-record extends to 2017 while the GVAR reprocessing period is 1994–2015). A secondary LLM reviewer was also run; where it flagged web-verified facts (R2 zero-egress, SLIDER "every pixel", ECMWF 0.25° free-real-time, several RGB band orders), those flags were **traced to the reviewer's stale training and overruled by the primary sources** — the corrections it got right (FY-4A LMI operational 2017 not 2016; Himawari registry date nuance) were incorporated.

**Confidence.** High on data sources, archive depths, RGB recipes, licenses, and R2 pricing (all primary-sourced). Medium on satellitewx's exact internal serving details (self-described, not independently inspectable) and on the render-on-demand compute figures (labeled assumptions, not measured — validate with a real archive-render prototype).

### Artifact index
- [teardown_matrix.csv](teardown_matrix.csv) — 12-platform feature × serving-architecture teardown
- [archive_depth.csv](archive_depth.csv) — archive depth/format/feasibility per satellite generation
- [rgb_recipes.csv](rgb_recipes.csv) — RGB & channel library with band math + quick-guide citations
- [overlays_feasibility.csv](overlays_feasibility.csv) — overlay global feasibility, licenses, TAW-share
- [cost_model_hot.csv](cost_model_hot.csv) / [cost_model_serving.csv](cost_model_serving.csv) / [cost_model_cdn.csv](cost_model_cdn.csv) — R2 economics
- [analysis_tools.csv](analysis_tools.csv) — analysis toolkit spec + TAW sharing
- [satellite_registry.schema.json](satellite_registry.schema.json) — declarative satellite/product/domain/overlay registry
- [roadmap_ranked.csv](roadmap_ranked.csv) — leverage-ranked roadmap with all fields
- [verification_log.csv](verification_log.csv) — 18-claim verification log






