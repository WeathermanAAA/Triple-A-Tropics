# Ensemble Cyclone Centers - multi-model TC ensemble platform (DESIGN)

**Status: STAGES 1-3 LIVE - ECMWF ENS (2026-06-13) + AIFS-ENS + GEFS on /models/, with the shared Model Regions picker (section 8) and a model selector that grows from the manifest. Final ring colors, navy basemap, Pacific-centered map; ECMWF/AIFS fully global detection (region = client-side crop), GEFS via NOAA genesis tracks (already TC-filtered). Pending Andrew's final look/region-framing sign-off on the live page (his Mac). Clusters and models 4 to 5 (GDM) are later stages and are NOT in scope here.**

This is the design canon for the `/models/` "Ensemble Cyclone Centers" product.
It follows the house convention of `SATELLITE.md` / `CYCLOLAB_DESIGN.md`:
decisions are stated with their rationale, traps are flagged in bold, and the
rollout is staged with hard-stop gates. The product is **R2-only** (nothing is
committed to `main`), exactly like HAFS.

---

## 0. HARD RULE - every model plot's burned-in header carries forecast hour + valid time

Standing convention for ALL model plots (this product, HAFS, and any future
model plot): the **burned-in / shared header** - the pixels that travel when a
still is copied or a GIF/MP4 is exported - MUST show, per frame:

- the init / cycle time,
- the **current forecast hour** (Fxxx), and
- the **valid time** for that hour (UTC/Z).

A shared frame must be self-documenting: which hour, and when it is valid for.
Putting F-hour / valid time ONLY in the HTML control chrome is a bug, because the
chrome is lost on copy/export. It MUST be drawn in the per-frame render path (not
a static layer) so each exported frame carries its own hour + valid time. Keep
the HTML chrome too for the live UI; the burned-in canvas/PNG is the source of
truth for anything shared.

Compliance (2026-06-15): enscenters canvas header (`_drawHeader`, this product) -
fixed to add F-hour + valid; HAFS rendered PNG (`hafs_render/hafs_plot.py`
subtitle "Init -> F-hour -> Valid") - already compliant. New model plots inherit
this by default.

---

## 0b. CANONICAL TAT BASEMAP - filled, with muted borders (client + server)

Every model plot uses ONE basemap: a dark navy filled ocean + slate filled land
with muted coast + country + state borders. The fill reads far better under data
fields (reflectivity, vorticity, center clouds) than bare green coastline
outlines, and the borders give geographic reference without competing with the
data. Identical hex values in BOTH rendering stacks (client canvas + server
cartopy/matplotlib); widths may be tuned but borders stay MUTED/secondary.

  ocean fill         : #07101c
  land fill          : #2f3f59
  coastline          : rgba(150,175,205,0.28), width ~0.6
  country borders    : rgba(150,175,205,0.45), width ~0.7   (admin_0)
  state/prov borders : rgba(150,175,205,0.18), width ~0.4   (admin_1, subtle)

DRAW ORDER everywhere: ocean -> land fill -> [data field] -> coastline ->
country borders -> state borders, so coast + borders stay visible ON TOP of a
filled data field. Client: `TATRegions.drawBasemapFill` (under the data) +
`TATRegions.drawBasemapLines` (over the data) in `models/regions.js`; the admin_1
layer is `ne_50m_admin_1_states_provinces.geojson` (50m, optional/guarded - 10m is
too heavy for page load). Server (HAFS): land fill + `COASTLINE`/`BORDERS`/`STATES`
at the spec colors, zorder land < data < lines. New model plots (radar viewer,
etc.) use this filled basemap + borders by default.

---

## 1. What it is

For each ensemble run (ECMWF ENS now; AIFS-ENS, GEFS, GDM-FNV3, GDM-GenCast
later), we detect every member's closed-low cyclone **centers** at every
forecast step and publish them as one model-agnostic JSON. A hand-rolled
`/models/` viewer draws the centers as a dot cloud over a world basemap, colored
by central pressure, animatable across forecast steps, with a per-member peak
table. It reproduces the plot Andrew built once inline ("ECMWF EPS - Ensemble
Cyclone Centers", subtitle "MSLP minima from EPS GRIB2, closed-circulation
filter") and generalizes it to a five-model platform.

## 2. The TAT patterns this reuses

- **ingest / detect split** and a **declarative model registry** (one frozen
  dataclass per model, add-config-not-code) - the `hafs_render` pattern.
- **R2-only publish** with a tracked frontend reading `cdn.triple-a-tropics.com`
  cross-origin - the HAFS / GIBS model (`permissions: contents: read`).
- **manifest-driven viewer**: adding a model is a manifest entry, not a JS edit.
- **completeness-gating, not polling**: the builder resolves the latest cycle
  whose terminal step is published; an early run picks the prior complete cycle.
- **hand-rolled scatter, no chart libs**; transport UI lifted from the HAFS
  viewer's single-rAF stepper; basemap from the bundled Natural Earth GeoJSON
  (no cartopy, no tile CDN).
- **disclosure discipline** (CYCLOLAB section 8.5): a derived product names its
  method. The future **super-ensemble** is derived and must say so.

## 3. Architecture and R2 layout

```
                ecmwf-opendata (CC-BY-4.0)
                        |  byte-range subset param=msl, 51 members
                  enscenters/ingest.py      (the only network/GRIB module)
                        |  one member at a time -> {step: hPa field}
                  enscenters/detect.py      (closed-low centers, AH P->V)
                        |
                  enscenters/pipeline.py    (assemble JSON + merge manifest)
                        |  ./models/enscenters/*  (gitignored)
            update-enscenters.yml  --aws s3 sync-->  R2
                        |
   cdn.triple-a-tropics.com/models/enscenters/...  <-- models/enscenters.js
```

**R2 key layout** (served 1:1 at `cdn.triple-a-tropics.com/<key>`):

```
models/enscenters/manifest.json                  # latest + available cycles per model
models/enscenters/{model}/{YYYYMMDDHH}.json       # one per cycle, e.g. ecens/2026061300.json
```

`models/enscenters/` is **gitignored**; only `models/enscenters.js` +
`models/index.html` (the viewer) are tracked. Bucket
`triple-a-tropics-media`; upload via `aws s3 sync` (new cycle, no `--delete`) +
`aws s3 cp` (manifest) + scoped `aws s3 rm` (rolling-window prune). The
`prune_keys.txt` the builder emits drives the prune so a fresh CI checkout never
`--delete`s the cycles it didn't rebuild.

## 4. Schema - the per-cycle JSON

Model-agnostic by design (generic keys; GEFS/AIFS/GDM reuse it unchanged).
Minified on the wire; centers are **compact arrays** with a documented field
order to keep the payload small (a full 51-member cycle is a few thousand
centers per step).

```jsonc
{
  "schema_version": 1,
  "model": "ecens",
  "model_label": "ECMWF ENS",
  "init_time": "2026-06-13T00:00:00Z",
  "init_cycle": "2026061300",
  "cycle_hour": 0,
  "generated_at": "2026-06-13T08:41:30Z",
  "attribution": "ECMWF open data (CC-BY-4.0)",
  "grid": "0.25 deg",
  "run_steps": [0, 3, 6, ..., 144, 150, ..., 360],   // animation timeline (hours)
  "n_members": 51,
  "n_centers": 41234,
  "detect": { "closed_threshold_hpa": 2.0, "search_radius_km": 500.0, ... },
  "center_fields": ["step_h", "lat", "lon", "mslp_hpa", "vmax_kt"],
  "pressure_bins": [
    { "key": "gt1000",    "label": ">1000 hPa",      "lo": 1000, "hi": null },
    { "key": "p990_1000", "label": "990 to 1000 hPa","lo": 990,  "hi": 1000 },
    { "key": "p970_990",  "label": "970 to 990 hPa", "lo": 970,  "hi": 990 },
    { "key": "p950_970",  "label": "950 to 970 hPa", "lo": 950,  "hi": 970 },
    { "key": "lt950",     "label": "<950 hPa",       "lo": null, "hi": 950 }
  ],
  "members": [
    { "id": "CTL", "label": "Control",
      "peak": { "mslp_hpa": 951.2, "vmax_kt": 91.0, "lat": 21.3, "lon": -58.1, "step_h": 72 },
      "n_centers": 812,
      "centers": [ [0, 21.3, -58.1, 1004.2, 11.6], [3, 21.0, -58.4, 1002.1, 17.0], ... ] },
    { "id": "P01", "label": "Perturbed 01", "peak": { ... }, "centers": [ ... ] }
  ]
}
```

Notes:
- `valid_time` per center is **derived** in the viewer (`init_time + step_h`),
  not stored, to keep the file small. `run_steps` is the full animation timeline.
- A center is drawn as a hollow ring colored by `mslp_hpa` against
  `pressure_bins`. Ring **colors** live in the viewer (`PRESSURE_BIN_COLORS`),
  keyed by bin `key` - Andrew's final ramp (pale `#dfe8ff` >1000, blue `#1f9bff`
  990-1000, yellow `#ffd21a` 970-990, red `#ff1f47` 950-970, hot pink `#ff3d9a`
  <950).
- `vmax_kt` is Atkinson-Holliday from `mslp_hpa` (env 1010 hPa), so the peak
  table's V matches Andrew's original.

**Manifest** (`models/enscenters/manifest.json`):

```jsonc
{
  "schema_version": 1,
  "generated_at": "2026-06-13T08:41:30Z",
  "default_model": "ecens",
  "models": [
    { "slug": "ecens", "label": "ECMWF ENS", "latest": "2026061300",
      "cycles": ["2026061300", "2026061218", ...] }   // newest first, rolling window
  ]
}
```

Only models with at least one published cycle appear, so the viewer's model
selector grows automatically as models 2 to 5 come online (and auto-hides while
there is just one).

## 4b. Tracking + clustering keystone - the SIBLING tracks JSON

The lean centers JSON above is per-STEP (the fast Cheerios view). The keystone
(`enscenters/tracking.py`) turns those into per-MEMBER tracks and per-SYSTEM
clusters and writes an **additive sibling** file - the centers JSON is never
touched, so the default view stays fast and the richer file loads only when the
viewer needs lines / mean / plume / envelope (the viewer features are a follow-up;
this is the data layer). It runs per cycle right after each model's centers
ingest, reusing the never-miss currency + shared reconcile, and is idempotent.

- **R2 key:** `models/enscenters/{slug}/{cycle}.tracks.json` (synced by the same
  `*/*.json` rule; the R2 listing's `(\d{10})\.json$` regex does NOT match it, so
  it never pollutes the cycle reconcile).
- **Manifest reference:** a per-model `tracks_versions` map (cycle -> the tracks
  file's `generated_at`), a sibling of `cycle_versions`, carried through the guard
  reconcile and trimmed to retained cycles. A cycle without an entry simply has no
  tracks file (viewer falls back to centers-only). Pruned cycles drop BOTH JSONs.

Per-model routing: **self-detected** (ecens, ecaie, gefs) run Stage A then B+C;
**native tracks** (fnv3, genc) SKIP Stage A (already per-member linked by the CSV
`track_id`, handed in-memory to the tracks step - no re-fetch).

- **Stage A - linkage** (self-detected only): greedy great-circle nearest-neighbour
  stitch with an advected first guess (linear extrapolation of the last two fixes)
  and an intensity-continuity tie-break `w_pos*(gc/range)+w_int*(|dMSLP|/dMSLP_norm)`;
  range scales with step spacing (~5 deg at 6 h, ~3 deg at 3 h), `maxgap` 1-2 steps,
  an implausible-pressure-jump cap kills cross-system links, and tracks below the
  min duration (~24 h) or min path distance are dropped (stationary spurious).
- **Stage B - per-system clustering** (all models): genesis/overlap-proximity seeds
  (co-location over OVERLAPPING valid times, so a late-forming member is not mis-seeded
  by its displaced first fix), then HDBSCAN (`metric="precomputed"`) on a track-to-track
  distance = mean great-circle separation over overlapping leads. `min_cluster_size`
  ~= 13% of members, `min_samples` low. A post-HDBSCAN, scale-adaptive merge +
  noise re-absorption coalesces a coherent system that HDBSCAN would otherwise
  shatter (the uniform-blob failure mode) while leaving a genuinely divergent
  second system in the seed split, and far outliers as dropped noise. Each cluster
  records `member_count`, `coverage_fraction`, `population`, and a `low_confidence`
  flag for small clusters. *(Documented upgrade path if quality is insufficient:
  regression-mixture / cubic-polynomial clustering a la Kowaleski-Evans - not built.)*
- **Stage C - derived products** (per cluster, per lead, members present only):
  robust spherical **mean track** (geometric median of unit-sphere positions, with
  the supporting member count per point); intensity **plume** (p10/p25/p50/p75/p90
  + min/max of Vmax AND MSLP, separately, by lead); position **envelope** (per-lead
  50%/90% covariance ellipses on a local tangent plane, chained to a swath, with the
  2x2 km covariance for an obs z-score); and an `obs_support` helper returning an
  observed position's percentile rank + Mahalanobis offset within the nearest-lead
  member distribution.

All geometry is unit-sphere / haversine; display longitudes are UNWRAPPED to a
continuous, dateline-safe sequence. Sibling-JSON shape:

```jsonc
{
  "schema_version": 1, "model": "ecens", "init_cycle": "2026061418",
  "generated_at": "...", "source_kind": "self_detect", "spacing_h": 3.0,
  "n_members": 51, "n_member_tracks": 430, "n_clusters": 21,
  "members": [ { "id": "CTL", "tracks": [ [ [0,21.3,-58.1,1004.2,11.6], ... ] ] } ],
  "clusters": [ {
    "id": 0, "members": ["CTL","P01",...], "member_count": 49,
    "coverage_fraction": 0.96, "population": 72, "low_confidence": false,
    "genesis": {"lat": -15.82, "lon": 170.23, "step": 0},
    "mean_track": [ [0,-14.97,170.34,16], ... ],          // [step, lat, lon, n]
    "plume": { "vmax": {"lead":[...], "p10":[...], ..., "min":[...], "max":[...], "n":[...]},
               "mslp": { ... } },
    "envelope": [ { "step":72, "n":40, "mean_lat":..., "mean_lon":...,
                    "cov_km":[[..,..],[..,..]],
                    "ell50":{"a_km":340,"b_km":..,"bearing_deg":..,"poly":[[lat,lon],...]},
                    "ell90":{ ... } }, ... ]
  } ]
}
```

## 5. The model registry (registry-as-data)

`enscenters/registry.py`. One `EnsModelSpec` per model carries: identity
(`slug`, `label`), the ingest config (open-data stream/type/control source,
member count, param, step lists), and the **detect knobs** (`DetectParams`:
closed-isobar threshold, search radius, footprint, dedup, lat limit). Adding a
model = append a spec + (if the source differs) one ingest adapter. The detector
and assembler are already model-neutral.

`DetectParams` is where density is tuned to match a model's reference plot
(`closed_threshold_hpa` and `search_radius_km` are the two density knobs).

## 6. Detection method

Per member, per step, on the global MSLP field (`enscenters/detect.py`):

1. **local minima** via `scipy.ndimage.minimum_filter` (box footprint ~a few
   hundred km; latitude clamped, **longitude wrapped** for antimeridian safety).
   Connected tied/plateau pixels collapse to one representative (deepest) via
   `label` + `minimum_position`.
2. **closed-circulation filter**: keep a minimum only if MSLP rises by
   `closed_threshold_hpa` (default 2 hPa) in **every** radial direction within
   `search_radius_km` (default 500 km) - a closed isobar encircles it. Rejects
   open troughs and high-latitude monotonic-gradient noise.
3. **antimeridian-safe**: ray longitudes wrap modulo `nlon`; reported longitude
   normalized to [-180, 180).
4. **P -> V**: Atkinson-Holliday `vmax_kt = 6.7 * (1010 - Pc)^0.644`.

Detection is **fully global to `lat_limit` = ~|lat| 88** (all closed lows, all
latitudes) so the viewer's Hemisphere and Global region crops are populated; the
region is a client-side view crop, not a detection limit. The polar cap (>88) is
dropped (the 0.25 deg grid is degenerate and the closed test's geometry is
unreliable there).

Validated on synthetic fields (closed lows found at exact location/pressure,
open troughs rejected, a dateline-straddling low located correctly) - see
`tests/test_enscenters.py`.

## 7. The viewer

`models/enscenters.js` + a new isolated section in `models/index.html`
(`enscenters-*` ids; the HAFS viewer is untouched - separate IIFE, separate
boot guard). A **canvas** scatter (performant for thousands of markers): the
navy basemap (`#07101c` ocean, `#2f3f59` land) is pre-rendered once to an
offscreen canvas from `/ne_110m_*.geojson` on a Pacific-centered equirectangular
projection (central_longitude 180); each step blits the basemap then draws that
step's centers as bold **hollow rings** colored by pressure bin. Transport
(play/pause/scrub/speed, arrow keys) is the HAFS single-rAF stepper with the
image-decode gate removed (the redraw is synchronous). A per-member peak table
sits in the right gutter, sorted by minimum pressure, control included. Model
selector (ECENS only now) uses the HAFS `_buildToggle` so it auto-grows. No
em-dashes in on-screen text; no AI-disclosure on the page.

## 8. Shared Model Regions layer

`models/regions.js` is a SHARED layer (an operational-style "Model Regions"
picker) reused unchanged by every non-storm-nest model viewer: ECMWF ENS centers
now, AIFS-ENS / GEFS / GDM-FNV3 / GDM-GenCast and any future synoptic ensemble or
global product later. **Storm-NEST viewers (HAFS, which auto-centers on a storm)
are EXCLUDED** - they keep their storm-following framing and never load it.

- **Detection is fully global** (to ~|lat| 88, see section 6). The region is a
  **client-side VIEW CROP only** - no per-region files. Selecting a region sets
  the map extent, filters the displayed scatter to the box, and recomputes the
  per-member peak table for that box (so "Atlantic" ranks each member's deepest
  Atlantic system). Global/Hemisphere views legitimately include extratropical
  lows.
- **Region registry**: grouped (Tropics / United States / Land / Hemispheres),
  each a `{w, e, s, n}` box. **Pacific boxes cross the dateline** (`w > e` wraps
  past 180); `inRegion` and the display `extentOf` both handle the wrap, and
  full-globe boxes display Pacific-centered (`[0,360]`).
- **Picker UI**: a grouped, thumbnail-card modal (one mini basemap-crop per
  region, selected outlined), dark theme, self-injected CSS so any page that
  loads `regions.js` gets it. Default region **Atlantic**; the last pick is
  remembered in `localStorage` (`ens.region`).
- **Exports** `window.TATRegions`: `GROUPS`, `get`, `inRegion`, `extentOf`,
  `project`, `drawBasemap` (shared by the viewer canvas AND the thumbnails so
  they cannot drift), and `RegionPicker`. The transport, the ring ramp, and the
  Pacific-centered projection all stay; only the extent + scatter subset + peak
  table change per region.

## 9. Automation

`.github/workflows/update-enscenters.yml`, R2-only. Fires ~8.5 h after each
00/06/12/18Z cycle on a non-round, non-colliding minute (`:41` primary, `:11`
backup 30 min later), `concurrency: cancel-in-progress: false`. Installs
numpy/scipy/xarray/cfgrib/eccodes/ecmwf-opendata (no cartopy, no matplotlib).
The MSLP-only pull is ~2.8 GB at 00/12Z / ~1.6 GB at 06/18Z (measured live;
~0.6 MB/member/step) - comfortably inside a GitHub Actions runner, so **no
worker is needed**. Member ingest streams one member at a time (peak disk ~50
MB) and detection parallelizes across members with `--jobs`.

**Kill switch / rollback:** disable the schedule (or set the workflow's
`workflow_dispatch` only). The viewer degrades to an empty state if the manifest
is absent. Forcing a re-run: `gh workflow run update-enscenters.yml`.

## 10. Roadmap - five models, two views, one super-ensemble

Each stage is gated; do not start the next without sign-off.

- **Stage 1 (this):** ECMWF ENS centers, end to end. GATE: Andrew's look + the
  bin colors / map centering from his reference plot.
- **Stage 2 (LIVE):** AIFS-ENS centers (`slug: "ecaie"`, ECMWF open-data
  `model="aifs-ens"`). PURE CONFIG - a twin `EnsModelSpec` (control is enfo/cf not
  oper/fc; 6-hourly to 360 h every cycle; no `gh`, so the warm-core thickness uses
  `z`/g). It inherits the detector, warm-core filter, never-miss currency core,
  cache-version helper, viewer, regions, GIF, and run selector unchanged. Own
  workflow (`update-aifs-ens.yml`, :29/:59), same R2 prefix + shared manifest.
- **Stage 3 (LIVE):** GEFS centers (`slug: "gefs"`). DIVERGENT METHODOLOGY, not a
  GRIB field detect: GEFS parses NOAA's ensemble GENESIS TRACKER (`atcf_gen`,
  NCO `ens_tracker/prod/gefs.YYYYMMDD/CC/`), which is ALREADY warm-core / TC
  filtered. So `source_kind="genesis_tracks"` (vs `"self_detect"`) routes the CLI
  to a LIGHT ingest (`enscenters/tracks.py`): one small ATCF text file per cycle,
  parsed straight into the same model-agnostic JSON - NO GRIB pull, NO detector,
  NO warm-core compute. `warm_core=False`; `vmax` is the model's OWN ATCF wind,
  not an Atkinson-Holliday estimate (the per-cycle JSON carries its own `caption`
  saying so, which the viewer swaps in). 31 members (control + 30 perturbed), 4x
  /day to 384 h. Own light workflow (`update-gefs.yml`, numpy+scipy only, :37/:53,
  fires 6-9 h after each cycle when the tracker posts), same R2 prefix + shared
  manifest. **Shared-manifest safety:** every model publishes to the one
  `manifest.json` from its own workflow, so both the never-miss path
  (`run_currency`) and the forced `--cycle` path (`build_cycle`) RE-READ the
  manifest just before merging and `merge_manifest_multi` replaces only this
  model's entry; `fetch_prior_manifest` retries a transient 403/404 (a present-
  manifest blip must never be read as "absent" and fresh-start the merge, which
  would clobber the sibling models' entries).
- **Stage 4:** GDM-FNV3 and GDM-GenCast centers (Google DeepMind models). New
  ingest adapters; same schema.
- **View 2 (clusters):** the "Ensemble Cyclone Clusters" view (group members
  into scenario clusters). A second viewer over the SAME per-cycle JSON.
- **Super-ensemble:** a DERIVED model entry (`slug: "super"`) pooling all
  models' centers. **Disclosure mandatory** - the viewer must label it derived
  and name the pooling method (`method_version`). Pooling is not a real model.

## 11. Open questions for Andrew

1. ~~The five bin colors.~~ RESOLVED 2026-06-13: pale `#dfe8ff` / blue `#1f9bff`
   / yellow `#ffd21a` / red `#ff1f47` / hot pink `#ff3d9a`, bold hollow rings,
   navy `#07101c` panel, `#2f3f59` land.
2. ~~Map centering.~~ RESOLVED 2026-06-13: Pacific-centered, central_longitude 180.
3. **Detection density.** Default closed-isobar threshold 2 hPa / radius 500 km
   yields ~3,000 to 3,400 centers per step across 51 members (a dense cloud).
   If your reference is thinner, raise `closed_threshold_hpa` (3 to 4) and/or
   lower `max_central_hpa` (drops the weakest >1000 hPa rings); both are registry
   `DetectParams`.
4. **Rolling window.** Default 8 cycles (~2 days) retained on R2. Deeper?
5. **Peak table.** Every member's peak is currently a deep Southern-Ocean winter
   low (austral winter), so the table reads mostly hot pink. Keep it global, or
   restrict the peak to the tropics/a basin?
