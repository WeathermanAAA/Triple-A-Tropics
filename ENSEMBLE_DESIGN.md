# Ensemble Cyclone Centers - multi-model TC ensemble platform (DESIGN)

**Status: STAGE 1 BUILT 2026-06-13 - ECMWF ENS only. Andrew's reference design is locked in (final ring colors, navy basemap, Pacific-centered map); pending his final look/smoothness sign-off on the live page (his Mac). Clusters and models 2 to 5 are later stages and are NOT in scope here.**

This is the design canon for the `/models/` "Ensemble Cyclone Centers" product.
It follows the house convention of `SATELLITE.md` / `CYCLOLAB_DESIGN.md`:
decisions are stated with their rationale, traps are flagged in bold, and the
rollout is staged with hard-stop gates. The product is **R2-only** (nothing is
committed to `main`), exactly like HAFS.

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

## 8. Automation

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

## 9. Roadmap - five models, two views, one super-ensemble

Each stage is gated; do not start the next without sign-off.

- **Stage 1 (this):** ECMWF ENS centers, end to end. GATE: Andrew's look + the
  bin colors / map centering from his reference plot.
- **Stage 2:** AIFS-ENS centers (ECMWF open-data `model="aifs-ens"`). New
  registry spec + ingest reuse. Same schema, same viewer (model selector grows).
- **Stage 3:** GEFS centers (NOMADS/AWS GRIB; new ingest adapter). Same schema.
- **Stage 4:** GDM-FNV3 and GDM-GenCast centers (Google DeepMind models). New
  ingest adapters; same schema.
- **View 2 (clusters):** the "Ensemble Cyclone Clusters" view (group members
  into scenario clusters). A second viewer over the SAME per-cycle JSON.
- **Super-ensemble:** a DERIVED model entry (`slug: "super"`) pooling all
  models' centers. **Disclosure mandatory** - the viewer must label it derived
  and name the pooling method (`method_version`). Pooling is not a real model.

## 10. Open questions for Andrew

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
