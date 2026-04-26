# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

- **Current year = `dt.date.today().year`**, not `max(points.season)`. Otherwise pre-season basins relabel last year as "current".
- **IBTrACS `NATURE == "NR"` is tropical** when `TRACK_TYPE == "PROVISIONAL"` (current season, before NCEI QC backfill). Without this, current-season ACE is zero.
- **JTWC `metoc.navy.mil` 403s on GH Actions IPs.** ATCF fetches try in order: our Cloudflare Worker proxy → natyphoon.top mirror → JTWC. The proxy is the primary — do not remove it.
- **NHC `ftp.nhc.noaa.gov/atcf/btk/`** is always reachable.
- **Chart HTML is inlined in a `HTML_TEMPLATE` string** inside each generator — no CDN deps so iframes work standalone. Don't introduce Plotly/D3/bundlers.
- **GitHub Actions `ubuntu-latest` does NOT ship ffmpeg** — workflows that need it must `apt-get install ffmpeg` explicitly (and `ffmpeg -version` after, to fail fast on PATH issues rather than mid-encode).
- After every commit, verify `git push` succeeded — branches can diverge silently when scheduled-workflow commits land on origin/main between local commit and push, leaving local 'done' work invisible to the live site (Pages serves origin/main, not local). Always confirm with `git rev-parse @ @{u}` showing equal SHAs after push, or `gh run list --workflow=pages-build-deployment --limit 1` showing the expected SHA deployed.
- **SST animator frame-cache invalidation is path-based.** When changing `FRAME_DPI`, ffmpeg encode settings, or any other input that invalidates the on-disk frame structure in `generate_sst_animations.py`, bump `cache_version` on every affected entry in `CORE_PRODUCTS`. `_product_cache_key()` reads that version into the path (e.g. `anomaly_v2/`), so the path-level cache busting kicks in automatically — old paths simply aren't looked at, new paths cold-render, and `_prune_old_frames` ages out the orphans. **No `actions/cache` key bump is needed; the path scheme handles invalidation.** When evaluating whether a fix actually ran in CI, always check `gh run view <id> --json headSha` to confirm which commit a run is on before treating its timing as post-fix evidence — a cache-hit-fast run can look like a regression when it's actually just a pre-fix run you mis-attributed.
- **Poster JPGs are encoded at PIL `quality=92` in `_write_poster`.** Don't drop below 90 — these images contain sharp text, tick labels, and contour lines that fall apart visually under aggressive JPEG compression even when the source frame is high-DPI. Frame DPI and `cache_version` do not govern poster sharpness; the JPEG quality knob is independent and lossy compression sets the ceiling. Posters are regenerated unconditionally on every `_encode_all` invocation (no skip logic), so a quality bump propagates to the orphan branch on the next workflow run without needing path-level invalidation.
- **Forcing a cold re-render of the SST animator** is done by bumping the `actions/cache` key prefix (`sst-mp4-frames-vN-...`) in `.github/workflows/update-sst.yml`. The on-disk frame paths are already prelim/final-aware via the path-versioning scheme, so a key bump is the only sledgehammer needed — no `cache_version` edits in `generate_sst_animations.py`. One workflow run after the bump is ~2.5 hr (cold render of all 6,480 frames + h.264 encode of 72 MP4s); subsequent warm runs return to ~5–8 min. Use this when fixing a frame-data bug whose effects would otherwise only clear over the natural ~13-day prelim→final aging window.
- **OISST publishes a preliminary file at T-1 then replaces it with a final ~T-14.** The MP4 animator's frame cache MUST encode the prelim/final tag in the PNG filename (`{YYYYMMDD}.{prelim,final}.png`), and `gsp.fetch_day_versioned` must always probe the final URL first so a cached prelim auto-promotes the moment the final lands. Without this you get a permanent prelim/final seam baked into the MP4s — visible as a ~50% jump in frame-to-frame motion magnitude ~13 days from the window end — that persists for ~76 days after any forced cold-rerender (e.g., `cache_version` bump, lost actions/cache). CRW has no equivalent two-stage publish, so this only affects OISST products (`actual` / `anomaly` / `anomaly_gmr`). The eager-upgrade window in `generate_sst_animations.py` (`UPGRADE_LATENCY_DAYS` to `+UPGRADE_GRACE_DAYS`) is what bounds steady-state cost: each warm run renders today's new frame as prelim plus one promotion at the ~14-day boundary, ~144 frames total instead of 72.

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
