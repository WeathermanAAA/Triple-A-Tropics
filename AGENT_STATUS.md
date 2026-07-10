# Agent status — Triple-A-Tropics autonomous work log

Maintained by Claude while Andrew is away. Updated after each meaningful step;
newest state first. Raw URL:
`https://raw.githubusercontent.com/WeathermanAAA/Triple-A-Tropics/main/AGENT_STATUS.md`

_Last update: 2026-07-10 ~02:15 UTC — Himawari-9 suite + TC-Diag mode + unified header + TM deep archive (1980) + GLOBAL-DEFAULT geo-ring composite ALL SHIPPED_

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
