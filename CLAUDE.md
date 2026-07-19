# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working agreement (standing rule — in effect until Andrew says otherwise)

Andrew is frequently **unavailable** (traveling, laptop closed) for reviews, decisions, and manual steps. Work **autonomously on best judgment** — do not block waiting on him.

- **Never leave work uncommitted or stranded on a local branch.** A dead Codespace once cost 600+ uncommitted lines. As soon as a piece is done, tested, and safe, **commit and push it to `main`** so it deploys. This overrides the generic "branch first / commit only when asked" default for this repo — the site's whole model is commit-to-`main`.
- **Make design/art calls yourself** using the documented house style (sober, data-forward; the locked palettes + tokens; no pulse rings/fireworks/em-dashes/cutesy taglines — see the plot-style memory). Where you'd normally stop for art sign-off, decide and proceed; jot down what you landed so Andrew can eyeball it later, but don't hold work for it.
- **Keep the gates that protect the live site.** ACE/data-critical and cross-repo (`ace_core`) changes still get an adversarial review + a byte-identical ACE check (compare `--no-live` data output before/after) **before** merge. No irreversible/destructive ops. But once a change passes its own gate, **land it** — don't hold it for review.
- **Only real-secret steps (e.g. adding credentials to the render box `.env`) truly need Andrew's hands.** For those: commit + push the code anyway (nothing left uncommitted), and append the exact manual step (repo, command, why) to a single running **QUEUED manual steps** list so it's ready when he's back.

## What this repo is

Triple-A-Tropics is a static GitHub Pages site (`triple-a-tropics.com`). Python generator scripts run on scheduled GitHub Actions, re-render data products (charts, maps, GIFs, interactive pages) into plain HTML/PNG/JSON files, and commit them back to `main`. No server, no database — the repo *is* the site. Understand this model before making changes: if a commit lands on `main`, it is live within ~60 s.

## High-level architecture

Generators are grouped by data product. Each generator is a **self-contained, basin/region-agnostic Python script** that:
1. Downloads raw data (IBTrACS CSV, OISST NetCDF, AOML OPeNDAP, CMEMS ARMOR3D, NHC/JTWC ATCF, NOAA CRW),
2. Processes it and writes output files alongside itself (or into `armor3d/`, `sst/`, `subsurface/`, `historical/`),
3. Is invoked once per basin/region by a matching `.github/workflows/*.yml`.

| Generator | Workflow | Cadence | Outputs |
| --- | --- | --- | --- |
| `generate_ace_plot.py --basin {wp,al,ep}` | `update-ace.yml` | every 6h | `{basin}_ace.html` + `{basin}_ace_data.json` |
| `generate_tracks_plot.py --basin {wp,al,ep}` | `update-ace.yml` | every 6h | `{basin}_tracks.html` + `{basin}_tracks_data.json` |
| `generate_sst_plots.py` | `update-sst.yml` | daily 13:17 UTC | `sst/*.png` (OISST + CRW, 18 regions) |
| `generate_subsurface_plots.py` | `update-subsurface.yml` | daily 14:23 UTC | `subsurface/*.png` (AOML TCHP + D26) |
| `generate_armor3d_plots.py` | `update-armor3d.yml` | daily 14:43 UTC | `armor3d/*.png` (TCHP/D26/anom/cross-sections) |
| `generate_season_gif.py --basin --year` | `update-season-gifs.yml` | daily 03:23 UTC | `{wpac,atl,epac}_{YEAR}_season.gif` |
| `generate_hafs_plots.py` | `update-hafs.yml` | every 6h (00/06/12/18Z + :27 backup) | `models/hafs/{model}/{storm}/{domain}/f{FFF}.png` + `models/hafs/manifest.json` — **R2-only, not committed** |
| `generate_mjo_rmm.py` + `generate_velocity_potential.py` | `update-subseasonal.yml` | daily 15:41 UTC (+16:11 backup) | `subseasonal/{mjo_*,chi_anom_*}.png` + meta JSONs — **R2-only, not committed** (chi solve = `subseasonal/chi_core.py` on pyshtools; climo = committed `subseasonal/chi_climo_1991_2020.nc`, rebuild via `subseasonal/build_chi_climatology.py`) |
| `build_armor3d_climatology.py` | `build-armor3d-climatology.yml` | manual, resumable | `armor3d/armor3d_climatology.nc` (one-off, committed) |
| `build_historical_tracks.py` | (local) | manual | `historical/{basin}/tracks/tracks_{YYYY}.json` |
| `generate_pacific_tchp_gif.py` | `make-pacific-tchp-gif.yml` | manual, artifact only | GIF as artifact (not committed) |
| `generate_global_crw_anom_gif.py` | `make-global-crw-anom-gif.yml` | manual, artifact only | GIF as artifact (not committed) |

### Shared patterns across workflows

- **Staggered crons on non-round minutes.** GitHub drops scheduled runs during high-load windows (especially :00/:15/:30/:45). All scheduled workflows use offset minutes (:07, :17, :23, :43, :47, :53) and most add a backup cron 30 min later. Never "clean up" these to round times — the staggering is load-bearing.
- **`concurrency:` group per workflow** keeps the backup cron from racing the primary, and keeps long runs (SST, ARMOR3D) from overlapping themselves.
- **Rebase-and-push loop** on every commit step: SST, subsurface, ARMOR3D, ACE workflows can all push to `main` concurrently. Each commits *only its own output files*, so conflicts are theoretically impossible; a plain `git push` would still lose the race. The 5-attempt `git push` / `git fetch` / `git rebase origin/main` loop at the end of each workflow is the fix — preserve it when editing. Metadata files (`sst/*_meta.json`, etc.) auto-resolve via `--theirs` because the next scheduled run rewrites them, so picking either side is safe; conflicts on anything else abort and fail loudly for human review.
- **Natural Earth GeoJSON** (`ne_50m_*.geojson`, `ne_110m_*.geojson`) is the basemap substitute for cartopy. Most workflows re-download them if absent. Do **not** add cartopy as a dep — the whole install footprint is intentionally minimal matplotlib + netCDF4 + xarray.
- **Basin config dicts** (`BASINS = {...}`) at the top of `generate_ace_plot.py`, `generate_tracks_plot.py`, `build_historical_tracks.py`, `generate_season_gif.py` must stay aligned. Onboarding a new basin means adding an entry to each + adding a workflow step + creating the `climatology/{basin}/index.html` subpage. The algorithms are already basin-agnostic.
- **Unified SST static widget** (`sst/index.html` → `sst/static_widget.js` + `sst/static_manifest.json`) replaces what used to be three separate static-plot sections (OISST, CRW, subsurface). Adding a new data source or variant is a manifest edit, not a JS edit. Per-source `regions` arrays in the manifest drive dropdown filtering — if a generator stops emitting a region's PNG, drop the slug from that source's list so the widget doesn't show a 404 option. The ARMOR3D equatorial cross-section stays a separate small widget on the same page.

### ACE methodology (NHC vs JTWC)

`generate_ace_plot.py` BASINS entries encode per-agency rules that differ on purpose — do not homogenize:
- **AL/EP (NHC):** count `NATURE ∈ {TS, SS}` (tropical + subtropical), wind source `USA_WIND` (1-min).
- **WP (JTWC):** count `NATURE == TS` only (no subtropicals), wind source `USA_WIND` → `WMO_WIND` / `TOKYO_WIND` (10-min, ÷0.88 conversion).

Climatology bands are 1991–2020 (NHC standard); min/max envelope uses **all** seasons excluding the current incomplete one, so outlier years (1933 AL, 1997 WP) don't burst the "max" envelope when selected.

### Known gotchas (carried from README)

- **HAFS plots (`generate_hafs_plots.py` / `update-hafs.yml` / `/models/`) are read-only → R2**, like GIBS: `permissions: contents: read`, commits nothing to `main`; all output is `aws s3 sync`'d to `s3://triple-a-tropics-media/models/hafs/` and the frontend (`models/index.html` + `models/hafs.js`, both tracked; `models/hafs/` output is gitignored) reads it from `cdn.triple-a-tropics.com`. Fetch+render reuse `hafs_plot.py`'s `fetch_hafs_frame`/`render_frame` (AWS-first Herbie template override; no cartopy — vendored Natural Earth GeoJSON). Storms are enumerated from the public bucket with an S3 `delimiter='.'` list (storm id is each key's filename prefix). The builder renders only the **latest *complete* cycle** (storm nest reached `f126`) and **per-pair** requires the terminal frame, so a still-uploading HAFS-B/parent domain is skipped, not published half-written. Storm **name = the ATCF id** (e.g. `13L`) on purpose — the HAFS trak deck has no usable name. On a **total render failure** (storms found but 0 frames) the builder exits non-zero so the workflow aborts before the pruning `--delete` sync and the prior cycle stays live; genuine off-season writes an empty manifest and the prune correctly clears R2.
- **Current year = `dt.date.today().year`**, not `max(points.season)`. Otherwise pre-season basins relabel last year as "current".
- **IBTrACS `NATURE == "NR"` is tropical** when `TRACK_TYPE == "PROVISIONAL"` (current season, before NCEI QC backfill). Without this, current-season ACE is zero.
- **JTWC `metoc.navy.mil` 403s on GH Actions IPs.** ATCF fetches try in order: our Cloudflare Worker proxy → natyphoon.top mirror → JTWC. The proxy is the primary — do not remove it.
- **NHC `ftp.nhc.noaa.gov/atcf/btk/`** is always reachable.
- **Chart HTML is inlined in a `HTML_TEMPLATE` string** inside each generator — no CDN deps so iframes work standalone. Don't introduce Plotly/D3/bundlers.
- **Per-basin tracks pages are LIVE via an inline overlay (no CDN deps).** `{basin}_tracks.html` is still a fully-baked static SVG, but it ships `LIVE_BASIN_JS` (in `generate_tracks_plot.py`) which refetches `feeds/{basin}_tracks_data.json` from R2 at view time and atomically redraws all storm layers, cards, stats, and the "As of" line — the cron-baked render is the no-JS/fetch-fail fallback. The JS builders are **byte-identical mirrors** of the Python renderers (`render_tracks_svg`, `render_active_icons`, `render_storm_card`, …) and the marker classification (`invest_x` / `hurricane` / `null` — purely invest-vs-active; the old peak-keyed `td_circle` ring is retired, current stage only picks the glyph letter/color) mirrors `ace_core.build_global_geojson`'s `marker_type` fork. **Any edit to either side must update both** — run `python -m unittest discover tests` (needs `node` on PATH; GH runners and codespaces have it) to prove parity before committing.
- **GitHub Actions `ubuntu-latest` does NOT ship ffmpeg** — workflows that need it must `apt-get install ffmpeg` explicitly (and `ffmpeg -version` after, to fail fast on PATH issues rather than mid-encode).
- After every commit, verify `git push` succeeded — branches can diverge silently when scheduled-workflow commits land on origin/main between local commit and push, leaving local 'done' work invisible to the live site (Pages serves origin/main, not local). Always confirm with `git rev-parse @ @{u}` showing equal SHAs after push, or `gh run list --workflow=pages-build-deployment --limit 1` showing the expected SHA deployed.
- **SST animator frame-cache invalidation is path-based.** When changing `FRAME_DPI`, ffmpeg encode settings, or any other input that invalidates the on-disk frame structure in `generate_sst_animations.py`, bump `cache_version` on every affected entry in `CORE_PRODUCTS`. `_product_cache_key()` reads that version into the path (e.g. `anomaly_v2/`), so the path-level cache busting kicks in automatically — old paths simply aren't looked at, new paths cold-render, and `_prune_old_frames` ages out the orphans. **No `actions/cache` key bump is needed; the path scheme handles invalidation.** When evaluating whether a fix actually ran in CI, always check `gh run view <id> --json headSha` to confirm which commit a run is on before treating its timing as post-fix evidence — a cache-hit-fast run can look like a regression when it's actually just a pre-fix run you mis-attributed.
- **Poster JPGs are encoded at PIL `quality=92` in `_write_poster`.** Don't drop below 90 — these images contain sharp text, tick labels, and contour lines that fall apart visually under aggressive JPEG compression even when the source frame is high-DPI. Frame DPI and `cache_version` do not govern poster sharpness; the JPEG quality knob is independent and lossy compression sets the ceiling. Posters are regenerated unconditionally on every `_encode_all` invocation (no skip logic), so a quality bump propagates to the orphan branch on the next workflow run without needing path-level invalidation.
- **The SST animator frame cache lives in R2, NOT the GitHub Actions cache.** The ~9.5 GB rolling 90-day frame set (8 products × 18 regions × ~104 days of FRAME_DPI=150 PNGs) cannot live in the GitHub Actions cache: the old design saved two ~9.5 GB full-set copies per run (a `-oisst` entry from Job 1 + a `-full` entry from Job 2 — and the restore-keys fallthrough meant `-oisst` already carried the prior run's CRW frames, so both were full sets), and two of them against GitHub's 10 GB/repo cache cap evicted each other **every run**, forcing a ~6 h cold re-render that re-stamped the OISST final/prelim seam (render-once never held — the visible "speeds up partway through" bug). Now a single durable tar lives in R2 (`triple-a-tropics-media/_buildcache/sst_frame_cache_v3.tar`) via `scripts/sst_frame_cache_r2.sh restore|save`, streamed (no per-file S3 overhead, no 9.5 GB intermediate on the runner disk), with an atomic temp-key swap on save so a truncated upload never replaces the good copy. Both jobs restore before render and save after; Job 1's save → Job 2's restore is the cross-job handoff. **Forcing a cold re-render** = bump the `_vN` suffix in `scripts/sst_frame_cache_r2.sh` (the R2 analogue of the old `-vN-` GitHub key); bootstrap is ~6 h, warm runs ~1–2 h (encode-bound) + ~5–10 min of R2 tar transfer. Do NOT reintroduce `actions/cache` for these frames — it physically cannot hold them.
- **The SST workflow is split into two sequential jobs to fit the GitHub Actions 6-hour-per-job hard limit.** Cold-rendering all 8 product families in one job exceeded 6 h, which created a self-reinforcing failure loop (Actions hard-kill bypasses post-steps including `actions/cache/save@v4`, so cache never warmed and every run started cold). The split: Job 1 `static-and-render-oisst` publishes the static PNGs to main FIRST (so they update even if the OISST shard later trips), then renders the four OISST-family products with `python generate_sst_animations.py --render-only --products actual anomaly anomaly_records anomaly_gmr` and saves the cache. Job 2 `render-crw-and-publish` (with `needs: static-and-render-oisst`) restores Job 1's frame cache from R2 (see the R2-cache gotcha above), runs the animator with no flags so OISST warm-skips and CRW cold-renders, encodes ALL 144 MP4s in one consolidated pass (so the manifest is unified — partial-`--products` runs would each overwrite manifest.json with their subset), then force-pushes to the orphan branch. The frame cache restore/save are `scripts/sst_frame_cache_r2.sh` calls (R2), NOT `actions/cache` — the save runs `if: always()` + `continue-on-error: true` so a cancel or a transient R2 hiccup never loses rendered frames or fails the job. Don't merge the jobs back together without re-checking the cold-render budget — the workload only grows as new products land.
- **Explorer objfix (`satellite/explorer/objfix*.js`) is a line-faithful ARCHER/ADT port** — every constant traces to primary source (see `OBJFIX-METHODS.md` §D + the provenance header in `objfix.js`). Do not "simplify" the math; departures D1–D7 are documented in the header and flagged inline. The WP floater input path georeferences frames from **tsr `render.py` layout constants** (`objfix_sources.js` LAYOUT: axes `[0.04,0.04,0.84,0.90]`, colorbar `[0.905,0.08,0.016,0.82]`, cartopy square-centering) — if tsr ever changes the floater figure layout, LAYOUT must follow (the per-frame colorbar self-calibration guards the LUT, not the geometry; it throws loudly on a mislocated colorbar). ARCHER's per-frame first guess must stay the **official-track anchor** (floater box center) — chaining its own fixes un-anchors the penalty term and the track drifts. Loop analyses must NOT retain per-frame BT grids/images (~0.5 GB in 40 frames — tab-killer); only the newest frame keeps its field.
- **MW/ASCAT are native cockpit fields/layers (`satellite/explorer/cockpit_fields.js`), and the legacy engines export reuse primitives**: `microwave.js` (`MicrowaveViewer.PRODUCTS/tileRel/boundsOf`) and `ascat.js` (`AscatViewer.STYLES/KT_SCALE*/drawBarb/stormMatch`) are loaded by the explorer page for those statics — keep the exports when editing either viewer. The `?embed=1` stage takeover is retired for MW/ASCAT (the standalone pages remain for the below-fold section and direct links).
- **`git fetch origin` with the default all-heads refspec re-downloads the SST orphan branch (multi-GB of MP4s) after EVERY force-push** — disjoint history, no deltas; aborted fetches also strand multi-GB `tmp_pack_*` files in `.git/objects/pack/` until the disk fills. Fetch `origin main` only; this Codespace's refspec is narrowed to main (2026-07-15) — don't widen it back. If the disk mysteriously fills, check for `tmp_pack_*` leftovers first.
- **PSL THREDDS silently returns ALL-ZERO data for large multi-timestep DAP subsets** (verified on the OLR LTM aggregation: the full 365-step read is 0.0 everywhere while any ≤60-step slab is correct — no error, no warning). Never load a long time axis in one DAP request; use `generate_hovmollers._load_slabbed` + `_guard_degenerate` (fails loudly on degenerate reads) as the pattern.
- **SST animator frame cache is RENDER-ONCE.** Once a date has any cached PNG, that PNG is reused for the rest of the date's lifetime in the rolling window — no auto-upgrade from prelim→final, no re-download of the source NetCDF. We always TRY prelim first because it's the only consistent processing pipeline available across the entire 90-day window; final's quality control varies by reprocessing batch and creates a perceptual seam wherever the prelim/final boundary lands inside the window (visible as a ~50% jump in frame-to-frame motion magnitude). Operational reference sites use the same strategy. After ~60–90 days of daily runs every frame in the window has been originally rendered from prelim and the seam fully disappears. Bootstrap runs (after `FRAME_DPI` / cache-key bumps) have a residual seam at ~day 30 (NCEI's prelim retention boundary, where the fetcher's final-URL fallback kicks in) that fades over the transition period. CRW has no two-stage publish so the policy is a no-op there. Plumbing: `gsp.fetch_day_versioned` resolves prelim cache → final cache → legacy → prelim URL → final URL, never overwrites; `_needs_render` skips any date with a cached PNG.

## Common commands

Install (one-time, for the full suite):

```bash
pip install pandas numpy matplotlib netCDF4 xarray requests Pillow imageio "copernicusmarine>=2.1,<3.0"
```

Different scripts need different subsets — see each workflow's install step for the minimal set.

Run locally (examples):

```bash
# Download the basin CSV first (huge, gitignored)
curl -O https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.WP.list.v04r01.csv

# ACE chart for one basin. --no-live skips the ATCF fetch (useful offline).
python generate_ace_plot.py --basin wp
python generate_ace_plot.py --basin wp --no-live

# Tracks map for one basin
python generate_tracks_plot.py --basin wp

# Season GIF for a specific year (historical) or current year (live)
python generate_season_gif.py --basin al --year 2005
python generate_season_gif.py --basin wp --year $(date -u +%Y)

# SST / subsurface — no args; render all 18 regions
python generate_sst_plots.py
python generate_subsurface_plots.py

# ARMOR3D requires CMEMS credentials in env
COPERNICUSMARINE_SERVICE_USERNAME=... COPERNICUSMARINE_SERVICE_PASSWORD=... \
  python generate_armor3d_plots.py
```

Trigger workflows from the `Actions` tab or:

```bash
gh workflow run update-ace.yml
gh workflow run update-sst.yml
```

There are no tests, linters, or build steps. The pages are plain HTML served as-is.

## When extending

- **New basin:** add a `BASINS` entry to all four basin-config files listed above, add workflow steps mirroring the existing ones, and create `climatology/{basin}/index.html` by copying an existing subpage and swapping the iframe `src`.
- **New data product:** match the existing pattern — one generator script + one workflow with staggered cron, concurrency group, and rebase-and-push loop. Output into its own subdirectory (e.g. `sst/`, `armor3d/`). Commit only the freshly rendered files (not intermediate caches or raw NetCDFs).
- **Chart or map tweak:** the inline `HTML_TEMPLATE` in each generator is the single source of truth. Keep it self-contained (no CDN scripts, no external CSS other than `/styles.css` where iframed).
- **Site-wide styling:** `styles.css` at repo root is shared by all pages.

## Manual IBTrACS fetcher

`manual-fetch/` holds OS-specific one-click fetchers for the WP IBTrACS CSV (for users cloning the repo to run generators locally). Keep the four variants (`.sh` / `.bat` / `.ps1` / `.py`) in sync if the URL ever changes.
