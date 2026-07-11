# Agent status — Triple-A-Tropics autonomous work log

Maintained by Claude while Andrew is away. Updated after each meaningful step;
newest state first. Raw URL:
`https://raw.githubusercontent.com/WeathermanAAA/Triple-A-Tropics/main/AGENT_STATUS.md`

_Last update: 2026-07-11 ~21:10 UTC — 🔥 PRODUCTION OUTAGE handled: Railway project vanished 15:08Z (floaters + live feeds + guidance all frozen); GH-Actions stopgap worker restored floater production 21:03Z; see Q15 for Andrew's Railway steps_

---

## 🔥 OUTAGE — 2026-07-11 15:08 UTC — RAILWAY PROJECT GONE (mitigated 21:03 UTC)

**Root cause:** the Railway project hosting the `/render` service
(`web-production-b88d.up.railway.app`) AND the always-on workers returned
platform-level "Application not found" from ~15:08 UTC — the app is gone
from Railway's edge (deleted/suspended/billing; only the dashboard knows).
NOT an R2 problem (every published frame fetches 200), NOT the player
(unchanged for weeks), NOT upstream satellites (GOES + Himawari storms all
froze at the same minute).

**Blast radius (all frozen 15:02–15:08Z):** floater frames for every storm
(the reported symptom — loops went stale/choppy/black); the home map's
`global_storms.geojson`; live ACE/tracks feeds (`feeds/*` — basin pages
degraded to their 6-h baked fallbacks by design); CycloLab guidance JSONs;
plus the interactive `/render` API (explorer custom-zoom, objfix WP floater
path, loop export) — the latter CANNOT be stopgapped from Actions.

**Mitigation (live):** TAT workflow `floater-worker.yml` @cb93aca3+abd67735
— chained GH-Actions runs (crons :07/:37, serializing concurrency) boot the
UNMODIFIED FastAPI `/render` on localhost and run the UNMODIFIED
`floater_poller.py` (foreground) + `intensity_poller.py` +
`cyclolab_guidance_poller.py` (background riders) for ~45 min per run.
Restart-safe + dual-writer-safe by the pollers' own design (R2 manifest
resync, content-hash dedupe, atomic per-key puts) — coexists with Railway
whenever it returns. Floater production VERIFIED resumed 21:03Z (BAVI hot
bands ir/irbd first, cold bands following).

**Verified restored (21:03–21:18Z):** floater frames for ALL 4 storms
(BAVI wp09 + cp90/wp97/wp98, GOES + Himawari paths, healthy ~5–8 min IR
cadence, every sampled loop frame fetches 200); per-basin live feeds
(`feeds/wp_tracks_data.json` fresh 21:10); CycloLab guidance
(`cyclolab/JTWC_WP092026/guidance.json` fresh 21:18). The intensity
rider's global-map key needed Railway-env parity
(`GLOBAL_GEOJSON_KEY=global_storms.geojson` — the code default is the
shadow-cutover key) @30d083a3; the home map heals on the current run.

**Secondary (user-reported) also shipped:** solid-black coastlines mudded
the storm on rainbow_ir — tsr main @ebcf004 restyles coast to light cyan +
borders/state lines off-white, each over a thin near-black halo (legible
on dark IR tops AND daytime scenes; 23 render-quality tests green). The
Actions worker picks it up the same hour.

**Andrew:** Q15 in the queued manual steps — restore the Railway project
(or say the word and it stays on Actions), then re-point `RENDER_API` if
the domain changed, then disable this stopgap's schedule. Until Railway is
back, the explorer custom-zoom, objfix WP floater input, and loop export
(the interactive `/render` API) stay down — Actions can't host those.

---

## IN PROGRESS — 2026-07-11 — MW-IMAGER INTENSITY + OBJECTIVE CONSENSUS

Building consensus member #2 (PMW-imager intensity from 89/37-GHz PCT) +
the SATCON-method blend (Velden & Herndon 2020) for the TC-Diagnostics board.

- **Landed so far** (all pushed to main, tests green):
  - `tcprimed/mwi.py` @11355611 — SHARED predictor extraction (crop → render
    regrid → PCT ring/sector superset; line-faithful Kieper & Jiang 37-GHz
    ring via the Jiang et al. 2018 Table 2 cyan/pink classes + fitted
    annulus + 90% closure; NE land gate; committed-model evaluator).
    Identical in training and the CI cron — no train/serve skew.
  - `tcprimed/mwi_train.py` + `mwi_fit.py` — offline TC-PRIMED trainer
    (inventory → 6-h thin/stratify → parallel extract → forward-selected
    linear fit, leave-one-YEAR-out validation, error tables by bin/sensor/
    year, provenance-carrying model JSON).
  - `tcprimed/build.py` @2d508db5 — both cron tiers write per-overpass
    `intensity{}` records + a manifest `intensity_model` card (no-op until
    the model JSON is committed; never sinks a render).
  - `satellite/explorer/satcon.js` @3d2c8d86 — the SATCON-method consensus
    (V&H 2020 verbatim 3-member equation, situational-RMSE weights, CIMSS
    3→6 h MW age decay, ≥2-member rule, ±10 kt band floor, departures D1–D8
    documented) + dashboard section 4 replacing the SOON stub; honest empty
    states while the model isn't deployed. `SATCON-METHODS.md` = the
    OBJFIX-METHODS-style provenance doc. Tests: tests/test_mwi.py (16),
    tests/test_satcon.cjs (30+ assertions), tests/satcon_dom_smoke.cjs.
- **Running now (Codespace)**: training extraction over 6,600 TC-PRIMED
  final-tier overpasses (2014–2024, all basins, GMI/AMSR2/SSMIS, ~85 GB
  streamed, restart-safe shards) — restarted after two REAL catches: the
  ring criterion needed per-pixel joint (PCT37, H37) classes, and the
  inherited PCT display-clip [105,290] was flattening the Cat-5 minima
  signal (widened to [50,350]). Plus the 12-agent research workflow
  (4/6 topics reported; SATCON + SHIPS-MI + K&J + TC-PRIMED all verified).
- **Next**: fit + validate → commit `tcprimed/mwi_model_v1.json` → force a
  tcprimed re-render so intensity{} lands on R2 → live-verify the panel.

---

## LANDED — 2026-07-11 ~03:10 UTC — TC-DIAGNOSTICS DASHBOARD + HOVMÖLLER + DAV

**The headline feature is live** (TAT @82135cdb + wiring): TC-Diagnostics is
now a full STORM-ANALYSIS WORKSHEET — stage splits imagery | board; picking
a storm auto-runs the loop workup; §1 objfix docked wide (scene | stats), §2
IR Hovmöller, §3 DAV, §4 honest SOON tiles (SATCON / WN-1 / eye-CDO /
sat-intensity-fixes / env / GLM).

- **IR Hovmöller** (Kossin 2002; Dunion et al. 2014; Ditchek et al. 2019):
  azimuthal-mean BT per 10 km ring about each frame's OBJECTIVE center,
  computed INSIDE the objfix worker while the BT grid is alive (loop-memory
  rule intact). Coverage-gated rings (never interpolated), low-confidence
  columns dimmed + amber-flagged, true time axis with honest gaps, PNG
  export, TM-scrub column highlight. Rides Track JSON as a `hovmoller` block.
- **DAV** (Piñeros/Ritchie/Tyo 2008/2011/2012/2014; Hu et al. 2020): Sobel
  gradient DIRECTIONS vs radial, folded ±90°, sample variance deg² on a
  ~10 km work grid; published regime bands + uniform-random 2700 line;
  trailing 24-h mean; **no DAV→intensity sigmoid** (would overstate skill).
  `dav_deg2` rides Track JSON per point.
- **VERIFIED — Irma 2017 archive workup** (17 frames, 2017-09-05 ±12 h, via
  the NEW native-era btpng): Hovmöller shows the cold inner core through the
  cat-5 RI day; DAV mid-window ≈1000 deg² sits in the published
  hurricane-typical band with the trailing mean FALLING (organizing) through
  the RI — and the window-edge frames flag themselves low-confidence (the
  archive anchor is the view center; a mover drifts off it at the edges —
  known v1 limit, honestly flagged). Shots:
  `_shots/tcd_irma17_hovmoller.jpg`, `_shots/tcd_irma17_dav.jpg`.
- **VERIFIED — BAVI live**: worksheet + objfix workup render correctly
  (`_shots/tcd_worksheet_bavi_live.jpg`); the LIVE loop time axis needs the
  calibrated-BT series to accumulate — the hourly emit workflow now also
  emits `himawari9-wpac-ir` + `goes19-fd-ir` (2 frames each already), so
  Hovmöller/DAV on live storms fill in over the next hours automatically.
- **format=btpng for the native ABI era** (tsr main @92d4483, live on
  Railway): GOES `fetch_regular` regrids one band to a regular lat/lon grid
  (the truecolor pattern) — per-frame objfix/Hovmöller/DAV now work for the
  ENTIRE 2017+ archive (Irma verified end-to-end), not just pre-2017 tiers.
- Unit tests: `node tests/test_diag_core.cjs` (axisym vortex → DAV≈0; noise
  → ≈2700; eyewall-ring recovery; coverage honesty) — all green.
- Gotcha for future sessions: the render service rate-limits 10 req/min/IP —
  do NOT probe it while a TM window or archive workup is loading (that's
  what emptied the first Irma attempt's window).

---

## LANDED — 2026-07-11 ~02:30 UTC

- **World composite FIXED + LIVE-VERIFIED**: the emit-geo-global workflow's
  first run succeeded — fresh frame `20260711T005020Z` with ALL THREE members
  (GOES-E 00:50 · GOES-W 00:50 · HW-9 00:50, listed in the manifest's
  `members[]` and shown in the pane header). Hourly cron keeps it fresh.
  Shot: `_shots/world_geo_ring_fixed_3sat.jpg` — seamless Americas + WPac
  (BAVI's eye visible), honest labeled Meteosat gap awaiting the Q13 key.
  The stale half-world 07-10 frame stays in the loop honestly and ages out.
- **GridSat-GOES deep tier SHIPPED** (tsr main `d11966b`, Railway
  auto-deploys /render): Time Machine 1994-10..2017-12 is now PER-SATELLITE
  ~4 km HOURLY with visible + 3.9 µm + WV + IR window — ordered tier ladder
  (GridSat-GOES → MergIR → GridSat-B1) with resolve-time fall-through and
  honest per-tier headers. Frontend era gating: TAT `2e423c3d` (hourly
  windows + c02/c07 unlocked in the GOES era). Resolution proof:
  `_shots/tm_katrina05_gridsat_goes_4km.jpg` (GOES-12 pinhole eye) vs
  `_shots/tm_katrina05_gridsat_b1_8km.jpg`.

## IN FLIGHT — 2026-07-11 (core-fix pass per Andrew's priority messages)

Order per Andrew: ① core explorer fixes ② Meteosat global-gap fill (URGENT)
③ deep-archive native overhaul (research-first) — TC-Diagnostics dashboard
build is PAUSED (WIP committed @7d0096ad, honest placeholders; Hovmöller+DAV
research is done and banked).

### World composite — ROOT-CAUSED, fix chain landed
The "blob + duplicate coastlines" World view: **the data pipeline was
correct** (proved: Andes anchor at −67.9/−68.7/−69.2 in the live bt.png;
local tile-cutter reproduces the CDN tiles at IoU 0.998; mercator addresses
exact). The real defects: the ONE frame on R2 (2026-07-10 03:40, emitted
once — box cron not running) is missing its **Himawari lobe**: suite emits
pin the scan to the GOES anchor, and the AHI loader TRUSTED the pin — a
mid-upload/housekeeping FLDK slot failed the whole member (honest degrade →
half a world). Fixes:
- tsr `3ef26d1` — AHI pinned-slot fallback to the newest COMPLETE slot
  (himawari-suite pins pre-verified → byte-identical there).
- tsr `7359e5f` — **Meteosat SEVIRI ring members** (0° HRSEVIRI + IODC
  45.5°E), REST Data Store client, licence-compliant ≥60 min delay,
  creds-gated (no key → wedge stays the honest labeled gap). Manifest gains
  `members[]` (per-satellite valid times — the Meteosat skew is surfaced).
- TAT `92c9b64f` — **emit-geo-global workflow**: hourly GH-Actions stopgap
  emitter (R2 secrets live there) until the box cron runs. First run in
  flight → the World view gets a FRESH GOES-E+W+Himawari frame today.
- TAT `e0b43a7a` — viewer core pass: product switches never flash (outgoing
  frames stay until incoming tiles land; last product kept resident =
  instant switch-back), per-product zoom-out pinning (CONUS↔World both
  correct now), manifest cache, world-copy hygiene, lost-view guard,
  members-driven gap badge + per-member valid-time chrome.

### QUEUED for Andrew (one-time, when back)
- **EUMETSAT key** (lights Meteosat in the World ring): free account at
  user.eumetsat.int → API key at api.eumetsat.int/api-key/ → accept the free
  "Meteosat L1 ≥1 h latency" licence → add `EUMETSAT_CONSUMER_KEY` +
  `EUMETSAT_CONSUMER_SECRET` as TAT repo Actions secrets AND to the box
  `.env`. Everything else is wired (satpy installs in the workflow already).
- **GH_PUSH_TOKEN as a TAT Actions secret** was NOT created (secret-store
  writes are yours to make; turned out unnecessary — tsr is public).
- Box s2 emit-cron session (existing Q11-family step) — once running,
  disable the GH emit cron (keep dispatch).

### Deep-archive research (Andrew's 3rd priority) — DONE, build pending
Native GVAR (1 km) is NOT render-on-demand anywhere public (CLASS =
order-staged; SSEC McFetch = .edu-licence-incompatible). The honest tier:
**GridSat-GOES** (NCEI, direct HTTPS, VERIFIED with Katrina GOES-12
2005-08-28 18Z, 63 MB netCDF): 4 km / HOURLY / per-satellite / 6 channels —
vs GridSat-B1's 8 km / 3-hourly / blended. Meteosat archive: SEVIRI full
archive (2004+) + MVIRI FCDR (1983+) on the Data Store with the same free
token. Era-by-era build next.

---

## OVERNIGHT REPORT — 2026-07-10 (Andrew asleep; autonomous)

### LANDED (newest first, all pushed)
- **TM draw-box foundation + archive render queue** — TAT `c25effab` +
  shots `d0cf60c8`. A drawn AOI is the archive crop for ALL panes (box the
  storm → scrub it); no box = the first render's extent FROZEN for the
  session (this also fixed the self-resizing archive pane — it was viewport
  drift changing each request's aspect). EVERY archive render goes through
  one serialized queue with de-dupe + cache (same band+time+box = one render,
  reused instantly), 429 back-off/retry, concurrency 1. Per-pane band picks
  in TM (box+time shared; a switch re-renders just that crop cache-first and
  lazily refills that pane's window). Archive coverage: Clean IR / C14 IR
  window (honest map to the archive's ~11 µm channel) / Dvorak BD / 6.7 µm
  WV; vis+RGB grey pre-2017 as before. Live playback decode-ahead (next 2
  frames' tiles mount invisibly; the flip is a pure opacity toggle).
  **ACCEPTED on Irma 2017-09-05 18Z, 4-pane** (Clean IR / BD / WV / True
  Color, boxed, scrubbed): 4/4 panes filled, 0 blocked, 0 failures — the 2
  raw 429s the limiter threw were absorbed by retry. Shot:
  `_shots/tm_irma17_4pane_boxed.jpg`.
- **main → s2-sat-ingest MERGE (veg-green skew fixed)** — tsr `fd98aa96`.
  20 conflict hunks resolved keeping BOTH lineages' behavior: box branch's
  custom rate limiter (+ /export converted to it), header_get +
  second-precision frame keys, antimeridian gridliner (now behind main's
  gridlines toggle), concurrent truecolor band-resolve (+ main's downsample
  threading), multiband AHI slot prober. Suite: **929 passed**. Veg-green
  VERIFIED on a fresh wpac truecolor emit: Borneo/Sumatra forest green
  (`_shots/veg_green_merged_branch.jpg`). tat-palettes stays pinned v0.2.2.
- **emit-cron foolproofed** — tsr `84da1685`: bare `compose up` now runs the
  FULL suite set (conus fd himawari9-wpac himawari9-fd geo-global); env still
  overrides.
- **Rail accent** → real site teal #49b6c8 — TAT `47cf4821`.
- Earlier tonight: rails redesign, MergIR tier (Q12-gated), TM multi-pane
  honesty, global-default composite, TM deep archive, unified header,
  Himawari-9 suite, TC-Diagnostics — see the sections below.

### IN PROGRESS / HONEST GAPS
- Live-loop jank: decode-ahead landed; real-browser feel needs Andrew's
  eyeball (headless can't measure smoothness). If still janky, next lever is
  pre-warming the whole window on play start.
- Storm-following archive box: needs a per-time historical track source
  (HURDAT/objfix chaining is banned as first-guess) — designed, not built.
- TCD SOON diagnostics (DAV/WN-1/eye-CDO/Hovmöller): scaffolded, archive
  hook contract ready, not implemented.

### ANDREW QUEUE (box + secrets; full detail in the memory queue file)
1. **Box session (Q11, updated):** in the tsr dir — `git fetch && git
   checkout s2-sat-ingest && git pull` (now INCLUDES the main merge + cron
   default), `docker compose -p tat-s2 -f docker-compose.s2.yml build emit`,
   then `--profile cron up -d --force-recreate emit-cron` (no env edit
   needed anymore). **For imagery NOW before the cron warms:** one-shots
   `run --rm emit --suite himawari9-wpac --store r2 --prefix shadow
   --max-zoom 5` and the same with `--suite geo-global` light the Himawari
   domains, the global default, and objfix's WP real-BT path immediately.
2. **Q12 Earthdata:** authorize "NASA GESDISC DATA ARCHIVE" on the profile,
   re-run the `fetch-mergir-sample` workflow (green = fixed), add the
   credential to the Railway render env → MergIR serves 2000-2017 at 4 km.
3. Q9/Q10/Q8/Q4 unchanged.

### BLOCKERS
- MergIR live verification blocked on Q12 (GridSat serves those dates
  honestly meanwhile). Nothing else blocked.

### HEALTH
- tsr main deployed tiers live via Railway auto-deploy (confirm one pre-2017
  render once it cycles). Box branches untouched since the last box session;
  the merged s2-sat-ingest is ready for Q11. All suites green at push time
  (TAT 496−4 pre-existing env; tsr s2 929; tsr main 692+21). Feeds normal.

---

## LANDED 2026-07-10 (overnight batch 2)

### 0a. Himawari-9 as a FULL suite satellite — SHIPPED both repos, locally live-verified

The roadmapped "BT feed → full Himawari suite" step. tsr `s2-sat-ingest`
@ef3a983 + TAT @c8bba3e0 (+ products.js regen). "Add a satellite = registry
rows" held: 28 wpac + 27 fd tiled products (all 16 AHI bands, B02 native
green, NO fake cirrus; True Color/Sandwich/Air Mass/Dust/Ash/Day Convection/
Natural Color/Snow-Fog/Fire Temp/Day Cloud Phase/Night Micro; BT raster per
emissive band) over the EXISTING recipe engine/pyramid/Q7/prune plumbing.

- **AHI recipes are first-class, JMA-verified** (17-agent verification pass
  against JMA MSC Quick Guides Ver 1.0 + Tech Note 65): JMA RETUNED the AHI
  thresholds (Murata & Shimizu 2017) — dust/ash/nightmicro/airmass/
  dayconvection/daycloudphase/firetemp/daylandcloud all differ from BOTH
  SEVIRI heritage and the ABI guides; every number test-locked
  (`tests/test_s2_ahi.py`, 23 tests). Snow-Fog ships ABI-heritage renumbered
  (JMA's exact blue gun is a derived 3.9 µm solar-reflectance product — a
  documented departure, not forced). JMA's printed tables use flipped
  difference orders — verifiers decoded sign conventions against the guides'
  own imagery; do NOT re-transcribe naively.
- **Infra:** AHI band-disk fetch off ONE complete FLDK slot (per-sector
  strides keep a full-disk B03 at ~60 MB instead of ~1 GB), shared-trig
  sampling with CHUNKED interpolation (one-shot scipy RGI = ~2 GB transient,
  proven to thrash), antimeridian-aware webmerc (fd bbox 60→221°E, dual
  x-ranges, wrap-aware sampling; MapLibre world copies on hw-fd).
  `--suite himawari9-wpac|himawari9-fd`; emit-cron iterates `S2_CRON_SUITES`.
  FD excludes truecolor (B03 disk budget, mirrors goes19-fd); wpac truecolor
  renders at the 2 km-class raster (Rayleigh at 0.5 km class = minutes/GBs).
- **Cockpit:** Himawari-9 satellite + W Pacific/Full Disk domains, FIELD rail
  swaps to the AHI set, availability per-domain off its own products.json
  (satellite/domain rows self-enable when the box emits — greyed until then).
  C##↔B## field mapping on cross-sat switch; TM refuses himawari domains
  honestly (GOES-East archive only, for now — see deep-archive spec below).
- **objfix WP cutover:** WP storms consume the wpac suite's calibrated AHI
  B13 `bt.png` (2560 px ≈ 3.7 km, `bt_px` registry knob); rainbow_ir LUT
  inversion RETIRED for WP when the suite manifest is live (labeled fallback
  until box emit + for CP). Anchors stay floater box centers. **BAVI
  before/after** (real FLDK scans, local emit): center Δ ~7 km; calibrated
  BT reads colder tops (−68.5 vs −65.0 °C cloud) → T# 3.5/~55 kt vs
  3.3/~51 kt; ARCHER conf dropped (0.34→0.08 — 3.7 km input is coarser than
  the 1.5 km floater pixels; honest tradeoff, both labeled low; bt_px can go
  higher later if the box budget allows). Also fixed a LATENT fd-path decode
  TypeError (`id.data.data`) that never fired live because every storm to
  date had a floater. Shots: `_shots/himawari_wpac_truecolor_airmass.jpg`,
  `_shots/objfix_bavi_before_lut.jpg` / `objfix_bavi_after_ahi_b13.jpg`.
- **KNOWN SKEW (pre-existing, surfaced by verification):** tsr
  `s2-sat-ingest` does NOT contain main @74f9298 (veg-green both sensors) —
  suite truecolor renders the pre-veg-green look until main is merged into
  the branch. Queue a `git merge main` on s2-sat-ingest as a follow-up.
- **BOX STEP QUEUED** (lights everything): see the runbook line appended to
  the queued-manual list — `git pull` on s2-sat-ingest, rebuild the tat-s2
  image, set `S2_CRON_SUITES="conus fd himawari9-wpac himawari9-fd"` in
  `.env`, restart emit-cron. The cockpit + objfix WP path self-enable off
  the manifests the moment the first himawari suite emit completes.

### 0b. TC-Diagnostics MODE scaffold + objfix panel polish — SHIPPED @827debc9 / @ac41d47c

Third cockpit mode alongside Live/Time Machine: storm selector drives a
per-storm dashboard; anchor pane frames the storm with the objective center
marked; Obj Fix docks as card #1 (same DOM node, state survives); 8 greyed
SOON cards (Sat Intensity Fixes, SATCON, DAV, WN-1, Eye/CDO, IR Hovmöller,
Env Favorability, GLM) — scope visible, nothing faked. Panel fixes: scene
header per the burned-in convention (provenance line + watermark + caveat
badge, no stacking) + grouped stats (Center & Fix / Intensity / Scene &
Structure). Live-verified on BAVI; shots in `_shots/`.

### 0c. Unified archive-grade burned-in header + multi-pane TM/TCD — SHIPPED @fdf9146a

Live tile panes carry the archive render's EXACT header (centered
SAT·INSTRUMENT·CHANNEL·VALID UTC, right product·palette tag, per-pane
watermark+attribution, REAL viewport min/max BT off the calibrated bt.png,
colorbar) — no lesser overlay; exports draw the same layout; compact at
2/4-pane (shot: `_shots/unified_header_4pane.jpg`). Time Machine loops load
per-pane on every servable pane; TCD frames the storm on all panes.

### 0d. TIME MACHINE DEEP ARCHIVE to 1980 — SHIPPED (tsr main c616602+86716a7f → Railway auto-deploys the live /render; TAT @0d896f59)

- **GridSat-B1 tier** (`gridsat.py`): explicit-time /render requests before
  2017-03-01 (previously 502) serve the NOAA CDR (noaa-cdr-gridsat-b1-pds,
  3-hourly, ~8 km, GLOBAL, 1980→present; lazy S3 range-crops, wrap-aware).
  11 µm IR window + 6.7 µm WV only; multi-band 422s honestly. Era header
  baked: "GridSat-B1 · 11 µm IR window · 3-hourly · ~8 km". `format=btpng`
  returns the frame's calibrated BT (u16, suite-encoding) — deep tier only
  (ABI's curvilinear grid would misregister the linear decode). ADDITIVE:
  live/2017+ paths untouched; full main suite 692 green. MergIR (2000-17,
  4 km/30-min) DEFERRED (GES DISC Earthdata auth) — GridSat serves those
  dates honestly labeled; noted as the future middle tier.
- **Cockpit:** date picker to 1980; pre-2017 flips `cx-tm-deep` (IR-only
  fields; RGBs grey "not available before 2017"); deep era skips GOES disk
  clamps. Render auto-builds a **12 h scrub window +6 h buffers**
  (center-out render-ahead, rate-limit paced, time-sorted — the timeline
  scrubber drags real archive frames while it fills); loop export downloads
  the window with the era header baked in.
- **Per-frame TC-Diagnostics:** TCD now COEXISTS with TM; every scrubbed
  frame recomputes objfix from GridSat calibrated BT (ArchiveSource →
  btpng; first guess = view center, stated; independent single-frame
  estimates with the full honesty ladder). `TCDiag.onArchiveFrame` is the
  archive-capability contract for every future diagnostic (DAV/WN-1/etc
  remain SOON cards, to be built archive-capable from day one).
- **Verified end-to-end on Hurricane Gilbert 1988-09-13** against a local
  backend: 888 mb eye rendered, era gating flips, window fills, scrubbing
  recomputes the center per frame (18Z → 21Z track motion visible), archive
  loop downloaded. Shots: `_shots/tm_gilbert88_scrub_objfix.jpg` +
  `tm_gilbert88_gridsat_render.jpg`. NOTE: the LIVE Railway /render picks
  the tier up on its auto-deploy from main — poll a pre-2017 render to
  confirm once deployed.

---

### 0e. GLOBAL-DEFAULT EXPLORER — geo-ring composite SHIPPED (tsr s2 @e277fc78 + TAT @b96a187b)

The cockpit now OPENS ON THE WORLD: GOES-19 + GOES-18 + Himawari-9 full
disks stitched nadir-nearest (10° BT cross-fade, 65° limb cutoff) into one
global webmerc pyramid (`sat/geo/global/{ir,irbd,wv}`); satellite/domain/
region controls are drill-downs from it. Meteosat sector = honest
transparent gap + dashed 'no ingest yet · coming' map badge (never
stretched). BT fields only on the global view (BT blends across sensors);
RGBs stay per-satellite, manifest-gated. Global 2560px BT raster feeds the
inspector. Q7 z0-5 cron + shape-driven prune as the other suites.
Live-verified on a full 3-disk local emit: seamless E/W overlap, BAVI on
the Himawari side, honest gap (shots `_shots/global_default_meteosat_gap.jpg`
+ `global_composite_z2_tiles.jpg`). Auto-default waits for pane readiness
and yields to any user steer. BOX: add `geo-global` to S2_CRON_SUITES (Q11).

### 0f. MergIR tier + TM multi-pane honesty — SHIPPED (tsr main e518e548+b63fd29c, TAT da8b6290)

**MergIR (2000-02→2017, 4 km/30-min):** access VERIFIED — NO anonymous path
exists (the AWS Open Data 'nasa-gpmmergir' listing = the CONTROLLED
requester-pays GES DISC bucket; PPS doesn't mirror the Tb granules). Tier
implemented against GES DISC HTTPS with Earthdata creds from env; the
best-available selector prefers MergIR for clean_ir ≥2000-02 ONLY when it
can actually fetch — otherwise GridSat serves the date HONESTLY (verified
via HTTP: 2005 render → X-Satellite: GridSat-B1). Era header 'NASA MergIR ·
11 µm IR window · 30-min · ~4 km'. WV + pre-2000 stay GridSat. 21 tests
(synthetic merg-granule decode/crop seam; live Katrina test creds-gated).
**BLOCKED ON Q12** (Earthdata account: authorize 'NASA GESDISC DATA
ARCHIVE' app, re-run the fetch-mergir-sample workflow, put creds in the
Railway render env) — then the Katrina 4 km re-render verifies itself.
Deferred by decision: native GOES archives (NOAA CLASS/AWS) for pre-2017
Americas.

**TM multi-pane honesty (bug fix):** entering TM in 4-pane no longer leaves
non-servable panes showing LIVE imagery beside the archive. Every pane
participates: servable panes render at the scrubbed time (Render + window
builder now cover ALL servable panes, per-pane frame lists, stamp-matched
followers); the rest BLOCK with the honest reason (RGBs pre-2017, MW/ASCAT
live-only, era-absent channels e.g. 1992 WV '35% valid — VAS era' via a
paired backend coverage message). Verified 4-pane on Andrew 1992-08-23 18Z
(shot `_shots/tm4_andrew92_honest_panes.jpg`). NEXT IN FLIGHT: rail
redesign (blue-chrome register, real-LUT swatches, ring provenance).

### 0g. Rail redesign — blue-chrome channel register SHIPPED (TAT + tsr e7ff90cd)

Rails restyled to the approved mockup (restyle/reorg ONLY — imagery, plate,
burned-in header, toolbar, panes, widths all untouched): the site nav's REAL
navy chrome gradient + bevel on both rails; active states in the mockup teal
#35d0a5 (no teal existed in site CSS — flagged). LEFT = channel register:
spectral-region subheaders (VISIBLE/NIR · WATER VAPOR · INFRARED; COMPOSITES
· MULTISPECTRAL RGB), band-ordered rows with REAL-LUT palette swatches
(horizontal ramps generated from the frozen tat_palettes cmap+norm; discrete
Dvorak renders discretely; RGBs get quick-guide hue dots, never fake ramps).
RIGHT: satellite ring with orbital provenance (75.2°W / 137.2°W / 0° /
45.5°E / 140.7°E) incl. quiet Meteosat-0° + IODC 'soon' rows; domains
World · Full Disk · CONUS · Meso; availability honesty kept, chips quiet.
Shots: `_shots/rails_redesign_channels.jpg` + `rails_redesign_rgb.jpg`.

## LANDED tonight (2026-07-09 evening → 07-10)

### 1. Objective center + intensity (ARCHER/ADT) — SHIPPED, live-verified on BAVI + 97W

On the explorer dev route (`/satellite/explorer/`, still unlinked+noindex).
Commits: spec §D addendum @f2b294 → compute module @ca3bb4 → panel+sources
@a2f22d → loop-memory fix @116683. Eyeball crops (also in repo):
`satellite/explorer/_shots/objfix_bavi_loop.jpg` + `objfix_97w_degraded.jpg`.

- **Faithful, not fast:** `objfix.js` is a line-faithful PORT — ARCHER from
  ajwimmers/archer @ d09f5c7 (log-compressed-gradient spiral score ×15−20,
  cube-root ring score ×250 then 0.0167, LINEAR 0.33/deg penalty, no-penalty
  prominence → per-sensor alpha → real 50%/95% certainty radii, quality
  gates + weak-center demotion, feature/surface ladder) and ADT v8.x from
  the SSEC McIDAS-V Java (ring/sector/FFT stats, exact scene-score cutoffs,
  10° log-spiral curved-band, BD-category base-table Raw T#, Rule 8 clamps,
  3 h Final T#, Rule 9 CI + rapid-diss, full 0.1-step Dvorak tables). Every
  UNCONFIRMED in the frozen spec was resolved from primary source pre-build
  (two spec corrections recorded in §D); the 7 unavoidable departures are
  D1–D7 in the file header, each flagged inline. Unit tests
  (`tests/test_objfix.cjs`): synthetic-vortex center recovery <0.15°,
  eye/shear scene classification, Rule 8/9 behavior, table exactness.
- **Data paths per spec:** AL/EP = fd `bt.png` (calibrated u16, ~14 km,
  labeled); WP = floater WebP **LUT inversion self-calibrated from each
  frame's own baked colorbar** (layout from tsr render.py axes rects,
  graticule-verified ±2 px; chrome/coast pixels masked + median-filled,
  labeled DEGRADED PRECISION). First guess per frame = the floater box
  center (official-track anchor) — chaining ARCHER's own fixes measurably
  drifted (22.9°N vs 20.5°N on BAVI) and is banned in-code.
- **Panel** (Obj Fix · beta, toolbar): honesty banner AUTOMATED OBJECTIVE
  SATELLITE ESTIMATE / not official / see NHC-JTWC; scene canvas with solid
  crosshair fix, faint rejected-candidate crosshairs, dashed weak center,
  r50/r95 circles, eye ring; confidence tier + km radii + eye probability;
  scene type + ADT skill tier (eye r≈0.70 / cloud r≈0.50); Raw/Final T#,
  CI, ~Vmax, ~MSLP (table, unadjusted; Pacific column for WP per the ADT
  source); Raw/Final/CI **trend chart** over a 26 h loop; center-track JSON
  (download + `window.ObjFix.tracks`) = the Hovmöller's input. Compute runs
  in a Web Worker (~2 min for a 41-frame loop).
- **Live results:** BAVI 41-frame loop → fix 20.48N 127.30E, conf 0.66
  (r50 23 km), UNIFORM CDO, CI 3.9 → ~63 kt / ~978 mb vs official 90 kt —
  the expected ADT no-eye-CDO low bias, surfaced as "skill: low" + degraded
  input. 97W invest → POOR FIX (gates rejected → weak center, dashed),
  SHEAR scene, r95 253 km, numbers stamped "UNRELIABLE (poor fix)" — the
  degradation path working as contracted.

### 2. MW + ASCAT as NATIVE cockpit fields/layers — SHIPPED, live-verified

The `?embed=1` stage takeover is retired (`#cx-embed` deleted). Eyeball
crops: `_shots/mw_91h_pane.jpg`, `_shots/ascat_layer_over_ir.jpg`,
`_shots/ascat_pane.jpg`.

- **Fields:** MW (37/91 color, 37H/91H, smoothed/raw) and ASCAT winds are
  pickable per pane like any field in 1/2/4-pane: MW overpass tiles are
  georeferenced MapLibre image sources (smoothed/raw = raster resampling),
  ASCAT barbs a camera-synced per-pane canvas overlay — pan/zoom/linked
  cameras/time-lock all apply (the clock pulls MW panes to the
  nearest-in-time overpass unless pinned).
- **Layers:** "MW pass" + "ASCAT winds" in the Overlays rail layer onto the
  ACTIVE pane's base field (91H over Clean IR, barbs with dark halos over
  IR) with a burned-in provenance badge; PNG/WebM exports composite both.
- **Re-hosted, not rebuilt:** the legacy engines now export their
  primitives (`MicrowaveViewer.PRODUCTS/tileRel/boundsOf`,
  `AscatViewer.STYLES/KT_SCALE*/drawBarb`) and the cockpit adapters
  (`cockpit_fields.js`) consume them; per-pane controls live in the rail
  tabs (MW: storm/overpass/product/display; ASCAT: view/pass/density/style,
  **high-contrast default**). Standalone pages unchanged for the below-fold
  section.
- Verified live end-to-end (puppeteer, zero pageerrors): field modes, layer
  modes, per-pane controls, chrome/legends, reset regression, exports path.

Also: `tests/` suite green except 4 pre-existing HAFS errors (installed
tat-palettes 0.1.0 lacks `era5_isotach_cmap` — environment, not code) and
the stale models asset-stamp failure which I fixed @bb7ee1. CLAUDE.md
gained the objfix/MW-ASCAT gotchas. **Next up (queue):** the Hovmöller can
now consume `ObjFix.tracks`; MW-channel ARCHER (constants already in hand)
is a natural follow-on.

---

## MY QUEUE (Andrew's hands / decisions, ordered)

**① Box (~1 min) — pull for the prune + Snow-Fog:** the emit-cron is already
running (loop is backfilling on its own) and you ran the fd suite — the ONE
remaining box step is:

```bash
git pull      # s2-sat-ingest, in the tsr dir
docker compose -p tat-s2 -f docker-compose.s2.yml --profile cron up -d --build emit-cron prune-cron
```

This starts `prune-cron` (the object-level shadow TTL that replaces the
permission-blocked bucket-lifecycle — 14 d, keep-min 2/product) and picks up
**Day Snow-Fog** (its greyed picker entry lights on the next emit).

**② Decision — history hygiene, optional follow-on:** main's history rewrite
is DONE and verified (see LANDED). Residual, outside last night's main-only
scope: ~20 stale June feature branches still carry the old shipped-file
third-party names in their historical blobs (NOT the planning artifacts —
those only ever existed on main and are fully gone). Say "sweep the branches"
and the same replace-text pass runs per contaminated branch (bundle-based,
cheap); or accept them as low-risk residue (stale branches, no artifact
files). Also optional: ask GitHub support to force-GC orphaned pre-rewrite
SHAs (they stay fetchable by direct SHA until then). Forks: ZERO, so no fork
residue exists.

**③ Decision (art): HAFS env-color v0.12 on the live worker** — say go and
Claude repins `hafs-render-worker` hafs-render v0.11.0→v0.12.0 (Railway
auto-rebuilds; restyles the 9 env products to the palette look on main).

**④ Decision (art, month-old): TCHP records hatching** — crops at
`https://cdn.triple-a-tropics.com/sst/records/review/<region>_tchp_anom.png`.
Go (merge + disclosure + temp-workflow cleanup) or park.

**⑤ One-clicks, LOW:** close stale [PR #24](https://github.com/WeathermanAAA/Triple-A-Tropics/pull/24)
(shipped via main; permission gate blocked Claude closing it); optional $10
AWS budget alarm (console/root only). NOTE: PR #24 and the stale June
branches reference pre-rewrite history — harmless, but closing/deleting them
is tidier post-rewrite.

## LANDED overnight (with SHAs)

- **HISTORY REWRITE — done and verified** (2026-07-09 ~05:30Z, per the
  overnight go): `git filter-repo` over all 2,254 main commits — the 16
  planning-artifact paths removed from every commit, 21 replace-text rules
  scrubbed the third-party names from all remaining blobs AND commit
  messages. A cron commit that raced the rewrite was cherry-picked on top;
  force-pushed (old tip `730bd656` → new `6ea658f0`). **Verification in the
  rewritten history: every name pickaxe = 0; every removed file's log =
  empty; HEAD grep clean.** Backup: `/workspaces/_backups/TAT-main-pre-expunge.bundle`
  (97 MB, pre-rewrite main; also on /tmp) + this Codespace's old objects
  (left un-gc'd). Pages redeployed from the rewritten main; site + explorer
  200. Caveats flagged: orphaned old SHAs remain fetchable on GitHub until
  support GC; zero forks exist; stale-branch residue = queue ②. tsr HEAD was
  also scrubbed earlier (`731bbef`).

- **Objective center + intensity — research phase DONE, spec committed**
  (`cadefb7`, `satellite/explorer/OBJFIX-METHODS.md`): ARCHER (Wimmers &
  Velden 2010/2016 + the author's released reference code — 5° spiral score,
  ring score with IR weight 0.0167, first-guess penalty 0.33, 0.75°
  peak-prominence confidence, gamma error CDF, quality gates) and ADT
  (Users' Guide v8.2.1 read in full — 24 km eye / 24-136 km cloud annulus
  geometry, BD categories, v8.x scene regressions, Dvorak CI→Vmax table,
  Rule-8/9 time constraints, per-scene skill). Unverifiable constants are
  marked UNCONFIRMED in the spec rather than guessed. Data path established:
  fd `bt.png` rasters for AL/EP; rainbow_ir LUT inversion (norm −95→40 °C,
  verified) for WP floaters incl. BAVI; first guess from the live feeds.
  Implementation = next session against this frozen spec (accuracy-critical;
  not winged at 5am). Honesty contract is written into the spec.

- **Cockpit v3 — single watermark, RGB keys, TIME MACHINE** (`9a64ad7` +
  `9725ba5`): one watermark for the view; every cbar-less pane carries a
  quick-guide interpretation key (overlay + exports) so no pane is bare;
  Time Machine mode drives the existing render-on-demand archive backend
  (field = rail with 8 servable fields, region = viewport with an 80°-cap
  clamp + a great-circle limb guard — off-disk corners 500 the renderer,
  found live; UTC picker bounded to GOES-R era; ≤12-frame rate-paced archive
  loops; exports reuse the ≤10MB/HQ path; PNG saves the backend's branded
  render directly). Live-verified with a real 2026-07-08 18Z archive render.
  The standalone custom-snapshot panel is hidden in the embedded legacy
  section — its function lives in the cockpit now. Scripts carry ?v=
  cache-busting (the CDN edge masked a fix once — lesson noted).

- **Cockpit v2 — branded panes, linked cameras, unified rail, scroll page**
  (`9012388`): per-pane burned-in chrome composited into exports; linked
  cameras with a Link toggle; subnav folded into the FIELD rail (Microwave +
  Scatterometer mount the existing viewers via ?embed=1, gated off their own
  manifests); page scrolls to the legacy loops/imagery section (floaters,
  meso, VIIRS/MODIS unchanged). Full Disk went live mid-pass (fd suite on
  R2, domain self-enabled).

- Earlier in the session (see git log for full detail): cockpit full-bleed
  layout (`63c4202`), cockpit design pass (`a789981`), the full §6 cockpit
  shell (`7525ecc`), object-level shadow prune (tsr `1d5046d`, 114 s2 tests),
  Day Snow-Fog RGB (tsr `cacbf64`), box-emit live verification (27/27
  products, BT probe physically correct), explorer preview gate DROPPED
  (Andrew's call — page stays unlinked+noindex by design).

## IN PROGRESS

- **Objective center + intensity build** — methods researched, spec + data
  path committed (`OBJFIX-METHODS.md`); the compute module + cockpit panel
  land next session. Nothing half-shipped: no UI is exposed yet.
- MRMS / METAR / model-field overlays, Chart, GOES-18 West, meso domains:
  honest "SOON" stubs in the cockpit awaiting their own pipelines.

## BLOCKERS

- None. Queue ① is a one-minute box pull at your convenience; everything
  else is decisions.

## HEALTH (as of the wind-down)

- Site + explorer 200 on the rewritten main; Pages deploying normally
  (rewritten history included — deploys `6ea658f0`+).
- Box emit-cron live and backfilling (5+ conus frames and climbing, fd suite
  emitted); ACE/tracks cron commits landing on schedule through the night
  (two raced my pushes and were rebased/cherry-picked cleanly).
- Typhoon BAVI (WP09, C3 110 kt) + Invest 97W active in the feeds.
- Codespace disk: /workspaces at 90% (the 3 GB backup experiments were
  cleaned; the 97 MB bundle kept). The Codespace may idle-suspend — all work
  is committed and pushed; nothing uncommitted anywhere.
