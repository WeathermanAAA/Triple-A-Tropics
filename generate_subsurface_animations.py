#!/usr/bin/env python3
"""
Triple-A-Tropics · Subsurface animation generator (90-day MP4 family)
=====================================================================

Renders one MP4 per (region × product) for six families:
  * aoml_tchp/                       — NOAA AOML TCHP (kJ/cm²)
  * aoml_d26/                        — NOAA AOML D26 (m)
  * armor3d_tchp/                    — ARMOR3D-derived TCHP (kJ/cm²)
  * armor3d_d26/                     — ARMOR3D-derived D26 (m)
  * armor3d_crosssection_actual/     — 5°S–5°N raw T cross-section (°C)
  * armor3d_crosssection_anomaly/    — 5°S–5°N T anomaly cross-section (°C)

Map families: 18 regions × 4 families = 72 clips. Cross-section families:
4 equatorial regions × 2 products = 8 clips. Total = 80 clips.

Architecture
------------
Sibling of `generate_subsurface_plots.py` (AOML) and
`generate_armor3d_plots.py` (ARMOR3D) — imports both modules and reuses
their plot helpers, colormaps, and TCHP/D26 compute kernels verbatim so
the animation visual style and numerical values both match the static
maps with no parallel pipeline to maintain. Mirrors
`generate_sst_animations.py` for the 90-day rolling-window pattern,
render-once frame caching, ffmpeg encode, and orphan-branch staging.

For each day in the 90-day window (today-89 → today inclusive):
  * AOML: read TCHP + D26 directly from the consolidated OPeNDAP file's
    time axis. Per-day NetCDF cache under `.subsurface_anim_cache/aoml/`.
  * ARMOR3D: fetch raw temperature in 30-day CMEMS chunks, compute
    TCHP + D26 per timestep, persist DERIVED grids under
    `.subsurface_anim_cache/armor3d/`. Raw chunks are deleted after
    compute to keep CI disk bounded.

Then for each (family × region):
  * Stitch the last WINDOW_DAYS cached frames into an h.264 MP4 via
    the system ffmpeg.
  * Save the last frame as a JPG poster.

Per-family `manifest.json` written under `_mp4_build/{family}/`,
ready to push to the `mp4-artifacts` orphan branch.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gc
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import matplotlib as mpl
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
from PIL import Image

# Reuse static-plot machinery for both sources. gss provides:
#   - REGIONS (the 18-region dict)
#   - CMAP_TCHP, CMAP_D26, BG_COLOR, etc.
#   - _subset_to_extent, _draw_basemap, _draw_filled_land, _style_axes,
#     _add_colorbar, _draw_watermark
#   - read_subsurface_grid (lat ascending + lon→0-360 normalization)
#   - OPENDAP_URL, TCHP_VAR, D26_VAR
# gar provides:
#   - _have_credentials, _cmems_subset, ARMOR3D_NRT_DATASET, VARIABLES,
#     DEPTH_MIN, DEPTH_MAX, _np_datetime_to_date
#   - compute_d26, compute_tchp
import generate_subsurface_plots as gss
import generate_armor3d_plots as gar


HERE = Path(__file__).resolve().parent
FRAME_CACHE_DIR = HERE / "_frame_cache" / "subsurface"
DATA_CACHE_DIR = HERE / ".subsurface_anim_cache"
AOML_CACHE_DIR = DATA_CACHE_DIR / "aoml"
ARMOR3D_CACHE_DIR = DATA_CACHE_DIR / "armor3d"
ARMOR3D_RAW_CACHE_DIR = DATA_CACHE_DIR / "armor3d_raw"
# Per-day equatorial-band T(depth, lat[-15..15], lon) cache for the
# cross-section animator. Materialized from the same CMEMS bulk-fetch
# that drives armor3d_tchp/d26 — we just slice the ±15° lat band out
# of each chunk's raw T before discarding the chunk.
ARMOR3D_CS_CACHE_DIR = DATA_CACHE_DIR / "armor3d_cs"
BUILD_DIR = HERE / "_mp4_build"

# Inset-map padding around the 5°S–5°N band. Has to match the value
# baked into gar.plot_cross_section so the cached lat band fully covers
# what the inset map needs to draw — narrower would clip continents in
# the inset; wider just wastes disk.
CS_LAT_PAD = 10.0

WINDOW_DAYS = 90
FPS = 15
# Match generate_subsurface_plots.py / generate_armor3d_plots.py
# savefig dpi (150) so animator frames have the same pixel dimensions
# as the on-site static PNG for each region. Dropping below this
# downscales the frame and reads as blur in the encoded MP4.
FRAME_DPI = 150
# Cushion past WINDOW_DAYS so a one-week skipped run (e.g. CMEMS
# outage) doesn't immediately invalidate the whole cache.
CACHE_KEEP_DAYS = WINDOW_DAYS + 14
# CMEMS subset bulk-fetch chunk. 30 days × 0.125° × 0-500 m at global
# extent compresses to roughly 0.5–1 GB per chunk on the wire and
# decodes to ~5 GB on disk before per-timestep iteration. Three
# chunks cover the full 90-day window. Smaller chunks add latency
# overhead per call (~30s/connection); larger chunks risk CMEMS
# server-side timeouts.
ARMOR3D_CHUNK_DAYS = 30


# ----------------------------------------------------------------------
# Family + product matrix
# ----------------------------------------------------------------------
# Each family maps 1:1 to an orphan-branch directory and a manifest.
# Adding a new dataset/product is a matrix edit; nothing else changes.
FAMILIES: dict[str, dict] = {
    "aoml_tchp":    {"source": "aoml",    "product": "tchp"},
    "aoml_d26":     {"source": "aoml",    "product": "d26"},
    "armor3d_tchp": {"source": "armor3d", "product": "tchp"},
    "armor3d_d26":  {"source": "armor3d", "product": "d26"},
    # Equatorial cross-sections — same ARMOR3D fetch, different render
    # geometry (longitude × depth instead of lat × lon). Source key
    # is "armor3d_cs" because the per-day data shape is different from
    # the TCHP/D26 reader and needs a separate ingest path.
    "armor3d_crosssection_actual":  {"source": "armor3d_cs",
                                     "product": "cs_actual"},
    "armor3d_crosssection_anomaly": {"source": "armor3d_cs",
                                     "product": "cs_anomaly"},
}

# Source labels embedded in each frame's subtitle. Match the static-page
# text exactly so the inline animator and the static map below it read
# as the same viz.
SOURCE_LABEL: dict[str, str] = {
    "aoml":       "NOAA AOML",
    "armor3d":    "ARMOR3D",
    "armor3d_cs": "ARMOR3D",
}

# Product configs are keyed by short slug. The `cmap`, `vmin/vmax`,
# colorbar ticks, and contour levels match the static plot helpers
# (gss.plot_tchp / gss.plot_d26) verbatim — frame visual style is
# numerically identical to the static maps the same data drives.
PRODUCTS: dict[str, dict] = {
    "tchp": {
        "slug": "tchp",
        "label": "TCHP (kJ/cm²)",
        "description": (
            "Tropical Cyclone Heat Potential — integrated heat content "
            "of the upper ocean column down to the 26 °C isotherm. "
            "Operational thresholds: 16 to sustain a TC, 60 for "
            "development, 100 for intensification, 125 for rapid "
            "intensification, 160+ for explosive deepening."
        ),
        "title_suffix": "Tropical Cyclone Heat Potential",
        "cmap": gss.CMAP_TCHP,
        "vmin": 0.0,
        "vmax": 200.0,
        "cbar_label": "Tropical Cyclone Heat Potential (kJ/cm²)",
        "cbar_ticks": np.arange(0, 201, 25),
        "cbar_extend": "max",
        # Thin always-on line contours every 20 kJ/cm². Matches
        # gss.plot_tchp's `line_contour_levels`.
        "line_contour_levels": np.arange(20, 201, 20),
        "cache_version": 1,
    },
    "d26": {
        "slug": "d26",
        "label": "D26 (m)",
        "description": (
            "Depth of the 26 °C isotherm — thickness of the warm-water "
            "buffer that fuels TCs. Shallow D26 (<30 m) means modest "
            "wind-driven mixing can cap intensity; deep D26 (100 m+) "
            "is why the Loop Current and Western Pacific warm pool "
            "support rapid intensification."
        ),
        "title_suffix": "Depth of 26 °C Isotherm",
        "cmap": gss.CMAP_D26,
        "vmin": 0.0,
        "vmax": 200.0,
        "cbar_label": "Depth of 26 °C isotherm (m)",
        "cbar_ticks": np.arange(0, 201, 25),
        "cbar_extend": "max",
        "line_contour_levels": np.arange(25, 201, 25),
        "cache_version": 1,
    },
    # Cross-section products — different render geometry (longitude ×
    # depth) and different ingest path. `kind="cross_section"` routes
    # rendering through gar.plot_cross_section; the slug becomes the
    # second half of the clip filename (e.g. enso_actual.mp4) and the
    # widget product label.
    "cs_actual": {
        "slug": "actual",
        "kind": "cross_section",
        "mode": "actual",
        "label": "Actual T",
        "description": (
            "5°S–5°N zonal-mean temperature versus depth (0–500 m), "
            "derived from ARMOR3D. Color fill = absolute temperature; "
            "solid black contour = current 20 °C isotherm; dashed "
            "black contour = 1993–2020 climatological 20 °C isotherm."
        ),
        "title_suffix": "Subsurface Temperature",
        # Bumped 1→2 when the overlay isotherm moved 26 °C → 20 °C: the
        # render-once frame cache won't auto-upgrade existing frames, so
        # a fresh cache_version path forces all 90 days to re-render with
        # the new 20 °C line (see _product_cache_key).
        "cache_version": 2,
    },
    "cs_anomaly": {
        "slug": "anomaly",
        "kind": "cross_section",
        "mode": "anomaly",
        "label": "Anomaly",
        "description": (
            "5°S–5°N zonal-mean temperature anomaly versus depth, "
            "ARMOR3D minus the 1993–2020 weekly climatology. Reds = "
            "warmer than normal, blues = cooler. Solid + dashed black "
            "lines mark the current and climatological 20 °C isotherm."
        ),
        "title_suffix": "Subsurface Temperature Anomalies",
        # Bumped 1→2 with the 26 °C → 20 °C overlay change (see cs_actual).
        "cache_version": 2,
    },
}


def _is_cs_product(product: dict) -> bool:
    """True for cross-section products (longitude × depth render)."""
    return product.get("kind") == "cross_section"


# Region set for cross-section families. The map families render every
# region in gss.REGIONS; cross-section families render only the four
# equatorial regions defined in gar.CROSSSECTION_REGIONS.
CS_REGIONS = list(gar.CROSSSECTION_REGIONS.keys())


# ----------------------------------------------------------------------
# Per-day data ingest
# ----------------------------------------------------------------------
def _date_from_np(ts) -> dt.date:
    """numpy.datetime64 / cftime / pandas.Timestamp → datetime.date."""
    return gar._np_datetime_to_date(ts)


def _aoml_index_dates(ds: xr.Dataset) -> dict[dt.date, int]:
    """Build {date → time-axis index} for the AOML OPeNDAP dataset."""
    out: dict[dt.date, int] = {}
    times = ds.time.values
    for i, t in enumerate(times):
        try:
            d = _date_from_np(t)
        except Exception:  # noqa: BLE001
            continue
        out[d] = i
    return out


def prefetch_aoml(missing_dates: list[dt.date],
                  log: str = "[sub-anim]") -> None:
    """Open AOML OPeNDAP once and write a per-day NetCDF cache for each
    missing date that has a matching time index. Single connection is
    much faster than per-day re-opens; per-day cache files mean warm
    runs and partial reruns don't re-hit the OPeNDAP server.

    Cache layout: `.subsurface_anim_cache/aoml/{YYYYMMDD}.nc`,
    holding `Tropical_Cyclone_Heat_Potential` and `D26` 2D fields."""
    if not missing_dates:
        return
    AOML_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"{log} AOML: opening {gss.OPENDAP_URL}")
    t0 = time.time()
    try:
        ds = xr.open_dataset(gss.OPENDAP_URL)
    except Exception as exc:  # noqa: BLE001
        print(f"{log} AOML OPeNDAP open failed: {exc}", file=sys.stderr)
        return
    try:
        date_to_idx = _aoml_index_dates(ds)
        if not date_to_idx:
            print(f"{log} AOML: no time axis in OPeNDAP dataset")
            return
        wrote = 0
        for d in missing_dates:
            cp = AOML_CACHE_DIR / f"{d:%Y%m%d}.nc"
            if cp.exists() and cp.stat().st_size > 100_000:
                continue
            idx = date_to_idx.get(d)
            if idx is None:
                continue  # AOML doesn't have this date yet
            try:
                sub = ds[[gss.TCHP_VAR, gss.D26_VAR]].isel(time=idx).load()
                if "time" in sub.coords:
                    sub = sub.drop_vars("time", errors="ignore")
                tmp = cp.with_suffix(".tmp.nc")
                sub.to_netcdf(tmp)
                tmp.replace(cp)
                wrote += 1
            except Exception as exc:  # noqa: BLE001
                print(f"{log}   ! AOML {d} fetch failed: {exc}",
                      file=sys.stderr)
                continue
        print(f"{log} AOML: wrote {wrote} day(s) "
              f"in {time.time() - t0:.1f}s")
    finally:
        ds.close()


def _read_aoml_day(d: dt.date
                   ) -> tuple[np.ndarray, np.ndarray,
                              np.ndarray, np.ndarray] | None:
    """Returns (tchp, d26, lat, lon) for one AOML day, or None.

    Both fields share lat/lon — read TCHP first, then read D26 (which
    re-reads the coords; cheap on a small file) and reuse them. lat is
    ascending and lon is on 0-360 (handled by gss.read_subsurface_grid)."""
    cp = AOML_CACHE_DIR / f"{d:%Y%m%d}.nc"
    if not cp.exists() or cp.stat().st_size < 100_000:
        return None
    try:
        tchp, lat, lon = gss.read_subsurface_grid(cp, gss.TCHP_VAR)
        d26, _, _ = gss.read_subsurface_grid(cp, gss.D26_VAR)
    except Exception as exc:  # noqa: BLE001
        print(f"[sub-anim] AOML {d} read error: {exc}", file=sys.stderr)
        return None
    return tchp, d26, lat, lon


def _chunk_date_ranges(dates: list[dt.date], chunk_days: int
                       ) -> list[tuple[dt.date, dt.date]]:
    """Group a sorted date list into contiguous ranges of at most
    `chunk_days` calendar days. Used to decide how to slice up CMEMS
    bulk fetches — gaps in `dates` are preserved so we never request
    days we don't need.

    Example: dates = [Jan 1, Jan 2, Jan 4, Jan 5, …, Feb 5] with
    chunk_days=30 → [(Jan 1, Jan 30), (Jan 31, Feb 5)]."""
    if not dates:
        return []
    sorted_dates = sorted(dates)
    chunks: list[tuple[dt.date, dt.date]] = []
    cur_start = sorted_dates[0]
    cur_end = sorted_dates[0]
    for d in sorted_dates[1:]:
        if (d - cur_start).days < chunk_days:
            cur_end = d
        else:
            chunks.append((cur_start, cur_end))
            cur_start = d
            cur_end = d
    chunks.append((cur_start, cur_end))
    return chunks


def prefetch_armor3d(missing_dates: list[dt.date],
                     log: str = "[sub-anim]",
                     *, want_cs: bool = False) -> None:
    """Bulk-fetch ARMOR3D raw temperature in 30-day chunks via CMEMS,
    compute TCHP + D26 per timestep, persist a derived NetCDF per day.

    Cache layout: `.subsurface_anim_cache/armor3d/{YYYYMMDD}.nc`,
    holding only `tchp` and `d26` 2D fields (raw 3D T discarded after
    compute to keep CI disk bounded).

    When ``want_cs=True``, also write the per-day equatorial-band T
    cache used by the cross-section animator (a separate file under
    `.subsurface_anim_cache/armor3d_cs/`). The slice is materialized
    from the same chunk fetch — no extra CMEMS calls, no parallel
    pipeline.

    A single CMEMS subset request that covers the entire 90-day window
    would be 4–8 GB on the wire and risks server-side timeouts; per-day
    requests pay ~30s connection overhead × 90 = waste. 30-day chunks
    are the sweet spot — three calls cover the cold window in ~30 min
    of CMEMS time, with each chunk's raw NetCDF deleted after iteration."""
    if not missing_dates:
        return
    if not gar._have_credentials():
        print(f"{log} ARMOR3D: CMEMS credentials missing — skipping")
        return
    ARMOR3D_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ARMOR3D_RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if want_cs:
        ARMOR3D_CS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    chunks = _chunk_date_ranges(missing_dates, ARMOR3D_CHUNK_DAYS)
    for chunk_start, chunk_end in chunks:
        chunk_path = (
            ARMOR3D_RAW_CACHE_DIR
            / f"raw_{chunk_start:%Y%m%d}_{chunk_end:%Y%m%d}.nc"
        )
        if not chunk_path.exists() or chunk_path.stat().st_size < 100_000:
            print(
                f"{log} ARMOR3D: fetching {chunk_start} → {chunk_end} "
                f"from CMEMS …"
            )
            t0 = time.time()
            try:
                gar._cmems_subset(
                    dataset_id=gar.ARMOR3D_NRT_DATASET,
                    start=dt.datetime.combine(chunk_start, dt.time.min),
                    end=dt.datetime.combine(chunk_end,
                                            dt.time(23, 59, 59)),
                    lon_min=-180.0, lon_max=180.0,
                    lat_min=-75.0, lat_max=75.0,
                    depth_min=gar.DEPTH_MIN, depth_max=gar.DEPTH_MAX,
                    variables=gar.VARIABLES,
                    out_path=chunk_path,
                    log=log,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"{log}   ! ARMOR3D chunk "
                      f"{chunk_start}→{chunk_end} failed: {exc}",
                      file=sys.stderr)
                continue
            print(f"{log}   chunk fetched "
                  f"({chunk_path.stat().st_size / 1e6:.0f} MB, "
                  f"{time.time() - t0:.0f}s)")

        try:
            _materialize_armor3d_chunk(chunk_path, log, want_cs=want_cs)
        finally:
            # Discard the raw chunk regardless — it's huge and the
            # derived per-day caches carry everything we need now.
            try:
                chunk_path.unlink()
            except OSError:
                pass
            gc.collect()


def _materialize_armor3d_chunk(
    chunk_path: Path, log: str,
    *, want_cs: bool = False,
) -> None:
    """Iterate timesteps in a downloaded CMEMS chunk and write per-day
    derived NetCDFs (TCHP + D26 only). Each timestep is loaded one at a
    time via xarray's lazy open — peak working set is one full 3D T
    field (~166 MB at 0.125° × 12 depth levels × 1200×2880 lat/lon).

    When ``want_cs=True``, also write the per-day equatorial-band T
    cache file used by the cross-section animator. The slice is
    lat ∈ [−5−CS_LAT_PAD, +5+CS_LAT_PAD] across all depths — small
    enough (~1 MB/day vs the full 33 MB) to keep cheap, but wide enough
    to cover gar.plot_cross_section's inset map."""
    try:
        with xr.open_dataset(chunk_path) as ds:
            if "time" not in ds.coords or ds.time.size == 0:
                print(f"{log}   ! chunk has no time axis")
                return
            time_vals = ds.time.values
            # ARMOR3D variable names: `to` (temperature), depth,
            # latitude, longitude. lat is ascending; lon may be
            # -180..180 — we roll to 0..360 to match downstream
            # subsetting expectations (gss._subset_to_extent uses
            # the AOML/OISST 0..360 convention).
            depth = ds["depth"].values.astype(np.float32)
            lat = ds["latitude"].values.astype(np.float32)
            lon_raw = ds["longitude"].values.astype(np.float32)
            if lon_raw.min() < 0:
                shift = int(np.sum(lon_raw < 0))
                lon = np.concatenate(
                    [lon_raw[shift:], lon_raw[:shift] + 360]
                )
            else:
                lon = lon_raw
                shift = 0
            if want_cs:
                ARMOR3D_CS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cs_lat_mask = (lat >= -5.0 - CS_LAT_PAD) & (
                    lat <= 5.0 + CS_LAT_PAD
                )
                cs_lat = lat[cs_lat_mask]
            else:
                cs_lat_mask = None
                cs_lat = None

            for ts_idx in range(len(time_vals)):
                try:
                    d = _date_from_np(time_vals[ts_idx])
                except Exception:  # noqa: BLE001
                    continue
                cp = ARMOR3D_CACHE_DIR / f"{d:%Y%m%d}.nc"
                cs_cp = ARMOR3D_CS_CACHE_DIR / f"{d:%Y%m%d}.nc"
                tchp_d26_done = (
                    cp.exists() and cp.stat().st_size > 100_000
                )
                cs_done = (
                    not want_cs
                    or (cs_cp.exists() and cs_cp.stat().st_size > 50_000)
                )
                if tchp_d26_done and cs_done:
                    continue
                try:
                    t_step = ds["to"].isel(time=ts_idx).values.astype(
                        np.float32
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"{log}   ! ARMOR3D {d} timestep load failed: "
                          f"{exc}", file=sys.stderr)
                    continue
                if shift:
                    t_step = np.concatenate(
                        [t_step[:, :, shift:], t_step[:, :, :shift]],
                        axis=2,
                    )

                if not tchp_d26_done:
                    d26 = gar.compute_d26(t_step, depth)
                    tchp = gar.compute_tchp(t_step, depth, d26)
                    try:
                        derived = xr.Dataset(
                            data_vars={
                                "tchp": (["lat", "lon"],
                                         tchp.astype(np.float32)),
                                "d26":  (["lat", "lon"],
                                         d26.astype(np.float32)),
                            },
                            coords={
                                "lat": lat.astype(np.float32),
                                "lon": lon.astype(np.float32),
                            },
                        )
                        tmp = cp.with_suffix(".tmp.nc")
                        derived.to_netcdf(tmp)
                        tmp.replace(cp)
                        derived.close()
                    except Exception as exc:  # noqa: BLE001
                        print(f"{log}   ! ARMOR3D {d} write failed: "
                              f"{exc}", file=sys.stderr)
                    finally:
                        del d26, tchp

                if want_cs and not cs_done and cs_lat_mask is not None:
                    try:
                        t_eq = t_step[:, cs_lat_mask, :].astype(np.float32)
                        cs_ds = xr.Dataset(
                            data_vars={
                                "t_eq": (["depth", "lat", "lon"], t_eq),
                            },
                            coords={
                                "depth": depth.astype(np.float32),
                                "lat":   cs_lat.astype(np.float32),
                                "lon":   lon.astype(np.float32),
                            },
                        )
                        tmp = cs_cp.with_suffix(".tmp.nc")
                        cs_ds.to_netcdf(tmp)
                        tmp.replace(cs_cp)
                        cs_ds.close()
                        del t_eq
                    except Exception as exc:  # noqa: BLE001
                        print(f"{log}   ! ARMOR3D CS {d} write failed: "
                              f"{exc}", file=sys.stderr)

                # Free per-timestep working set before the next one —
                # 166 MB held longer than needed will OOM-kill the
                # runner on the second or third chunk otherwise.
                del t_step
                gc.collect()
    except Exception as exc:  # noqa: BLE001
        print(f"{log}   ! ARMOR3D chunk read failed: {exc}",
              file=sys.stderr)


def _read_armor3d_day(d: dt.date
                      ) -> tuple[np.ndarray, np.ndarray,
                                 np.ndarray, np.ndarray] | None:
    """Returns (tchp, d26, lat, lon) for one ARMOR3D day, or None."""
    cp = ARMOR3D_CACHE_DIR / f"{d:%Y%m%d}.nc"
    if not cp.exists() or cp.stat().st_size < 50_000:
        return None
    try:
        with xr.open_dataset(cp) as ds:
            tchp = ds["tchp"].values.astype(np.float32)
            d26 = ds["d26"].values.astype(np.float32)
            lat = ds["lat"].values.astype(np.float32)
            lon = ds["lon"].values.astype(np.float32)
    except Exception as exc:  # noqa: BLE001
        print(f"[sub-anim] ARMOR3D {d} read error: {exc}",
              file=sys.stderr)
        return None
    if lat.size >= 2 and lat[0] > lat[-1]:
        lat = lat[::-1]
        tchp = tchp[::-1, :]
        d26 = d26[::-1, :]
    return tchp, d26, lat, lon


def _read_armor3d_cs_day(d: dt.date
                          ) -> tuple[np.ndarray, np.ndarray,
                                     np.ndarray, np.ndarray] | None:
    """Returns (T[depth, lat_band, lon], lat_band, lon, depth) for one
    ARMOR3D day's equatorial-band slice, or None if the cache is
    missing. Used by cross-section frame rendering."""
    cp = ARMOR3D_CS_CACHE_DIR / f"{d:%Y%m%d}.nc"
    if not cp.exists() or cp.stat().st_size < 50_000:
        return None
    try:
        with xr.open_dataset(cp) as ds:
            t_eq = ds["t_eq"].values.astype(np.float32)
            depth = ds["depth"].values.astype(np.float32)
            lat = ds["lat"].values.astype(np.float32)
            lon = ds["lon"].values.astype(np.float32)
    except Exception as exc:  # noqa: BLE001
        print(f"[sub-anim] ARMOR3D CS {d} read error: {exc}",
              file=sys.stderr)
        return None
    if lat.size >= 2 and lat[0] > lat[-1]:
        lat = lat[::-1]
        t_eq = t_eq[:, ::-1, :]
    return t_eq, lat, lon, depth


SOURCE_READERS = {
    "aoml":    _read_aoml_day,
    "armor3d": _read_armor3d_day,
}


# ----------------------------------------------------------------------
# Frame rendering
# ----------------------------------------------------------------------
def _render_frame(product: dict, data: np.ndarray, lat: np.ndarray,
                  lon: np.ndarray, region_cfg: dict, valid_date: dt.date,
                  countries, coast, out_path: Path,
                  source_label: str) -> bool:
    """Render one frame (one date × region × product) to `out_path`.

    Mirrors gss._plot_field's clean (no-labels) variant exactly:
    pcolormesh with the product's cmap/vmin/vmax, thin always-on
    contour lines, _draw_filled_land overlay so continents read as gray
    over the AOML/ARMOR3D zero-over-land convention, basemap, watermark.
    Returns False if the region subset is empty."""
    extent = region_cfg["extent"]
    figsize = region_cfg["figsize"]
    label = region_cfg["label"]

    sub, la, lo = gss._subset_to_extent(data, lat, lon, extent)
    if sub.size == 0:
        return False

    fig, ax = plt.subplots(figsize=figsize, facecolor=gss.BG_COLOR)
    LON2, LAT2 = np.meshgrid(lo, la)
    norm = mcolors.Normalize(vmin=product["vmin"], vmax=product["vmax"])
    pcm = ax.pcolormesh(
        LON2, LAT2, sub, cmap=product["cmap"], norm=norm,
        shading="auto", zorder=1, rasterized=True,
    )
    try:
        ax.contour(
            LON2, LAT2, sub,
            levels=product["line_contour_levels"],
            colors="#000000", linewidths=0.25, alpha=0.5, zorder=1.5,
        )
    except Exception:  # noqa: BLE001
        # Some narrow region subsets can produce a contour-input
        # geometry matplotlib refuses (e.g. all-NaN strips). Drop the
        # contour silently rather than fail the frame.
        pass

    # Filled-land + coastlines: AOML and ARMOR3D both carry 0.0 over
    # land (not NaN), so CMAP.set_bad() never fires there. Without
    # this overlay land would render as deep-ocean navy via CMAP(0).
    gss._draw_filled_land(ax, extent, countries)
    gss._draw_basemap(ax, extent, countries, coast)

    date_label = valid_date.strftime("%B %-d, %Y")
    title = f"{label} · {product['title_suffix']}"
    subtitle = f"Valid: {date_label}  ·  {source_label}"
    gss._style_axes(ax, extent, title, subtitle)
    gss._add_colorbar(
        fig, pcm, product["cbar_label"],
        ticks=product["cbar_ticks"],
        extend=product["cbar_extend"],
    )
    gss._draw_watermark(ax)
    fig.subplots_adjust(left=0.05, right=0.89, top=0.86, bottom=0.08)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=FRAME_DPI, facecolor=gss.BG_COLOR)
    plt.close(fig)
    return True


# ----------------------------------------------------------------------
# Cache helpers
# ----------------------------------------------------------------------
def _product_cache_key(product: dict) -> str:
    """Segment under FRAME_CACHE_DIR/{family}/{region}/. Mirrors the
    SST animator's `cache_version` knob: bump when frame rendering
    changes (e.g. a different colormap stop) and old frames are
    re-rendered into a fresh path while orphans age out via
    `_prune_old_frames`."""
    v = int(product.get("cache_version", 1))
    slug = product["slug"]
    return slug if v == 1 else f"{slug}_v{v}"


def _frame_path(family: str, region_key: str, product: dict,
                d: dt.date) -> Path:
    """Per-(family, region, product, date) PNG cache path. AOML and
    ARMOR3D have a single publish per day (no prelim/final dance), so
    no version tag in the filename."""
    return (FRAME_CACHE_DIR / family / region_key
            / _product_cache_key(product)
            / f"{d:%Y%m%d}.png")


def _needs_render(family: str, region_key: str, product: dict,
                  d: dt.date) -> bool:
    """True if (family, region, product, date) has no cached PNG yet.

    Render-once: any cached frame wins permanently. AOML / ARMOR3D
    don't have a prelim → final upgrade path the way OISST does, so
    this is also the simplest correct policy."""
    return not _frame_path(family, region_key, product, d).exists()


def _list_window_frames(family: str, region_key: str, product: dict,
                        window_dates: list[dt.date]) -> list[Path]:
    """Returns the cached PNGs for `window_dates` in chronological
    order. Dates with no cached frame are silently dropped."""
    out: list[Path] = []
    for d in window_dates:
        p = _frame_path(family, region_key, product, d)
        if p.exists():
            out.append(p)
    return out


def _prune_old_frames(window_end: dt.date) -> None:
    """Delete cached frames older than (window_end − CACHE_KEEP_DAYS).

    Keeps the cache from growing unbounded across many CI runs while
    tolerating a ~two-week skipped run without invalidating the whole
    window. Source-data caches are pruned by the same cutoff."""
    cutoff = window_end - dt.timedelta(days=CACHE_KEEP_DAYS)
    removed = 0
    for root in (FRAME_CACHE_DIR, AOML_CACHE_DIR, ARMOR3D_CACHE_DIR,
                 ARMOR3D_CS_CACHE_DIR):
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            stem = p.stem.split(".")[0]  # tolerate .nc and .png
            if len(stem) < 8 or not stem[:8].isdigit():
                continue
            try:
                d = dt.date(int(stem[:4]), int(stem[4:6]), int(stem[6:8]))
            except Exception:  # noqa: BLE001
                continue
            if d < cutoff:
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
    # Drop any leftover empty raw-chunk dir on the way out.
    if ARMOR3D_RAW_CACHE_DIR.exists():
        for p in ARMOR3D_RAW_CACHE_DIR.glob("*"):
            try:
                p.unlink()
            except OSError:
                pass
    if removed:
        print(f"[sub-anim] pruned {removed} cached files older than "
              f"{cutoff}")


# ----------------------------------------------------------------------
# ffmpeg encoding (verbatim from generate_sst_animations.py)
# ----------------------------------------------------------------------
def _encode_mp4(frames: list[Path], out_path: Path,
                fps: int = FPS) -> int:
    """Concat-decode a frame list into an h.264 MP4. See the SST
    animator's `_encode_mp4` for the rationale on the concat-demuxer
    duration trick + CRF/preset/tune choices."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    concat_file = out_path.with_suffix(".concat.txt")
    per_frame_s = 1.0 / fps
    with concat_file.open("w") as f:
        for fp in frames:
            f.write(f"file '{fp.resolve()}'\n")
            f.write(f"duration {per_frame_s}\n")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-vsync", "cfr", "-r", str(fps),
        "-frames:v", str(len(frames)),
        "-c:v", "libx264", "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-crf", "18", "-preset", "slow", "-tune", "stillimage",
        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2:0:0:black",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    concat_file.unlink(missing_ok=True)
    return out_path.stat().st_size


def _write_poster(last_frame: Path, out_path: Path) -> None:
    """Re-save the last frame as a small JPG for <video poster=…>.
    See the SST animator's `_write_poster` for the quality=92 rationale
    — sharp text and contour lines need the higher quality knob."""
    img = Image.open(last_frame).convert("RGB")
    max_w = 1200
    if img.width > max_w:
        h = int(img.height * (max_w / img.width))
        img = img.resize((max_w, h), Image.LANCZOS)
    img.save(out_path, "JPEG", quality=92, optimize=True)


# ----------------------------------------------------------------------
# Top-level render orchestration
# ----------------------------------------------------------------------
def _build_window_dates(end_date: dt.date) -> list[dt.date]:
    return [end_date - dt.timedelta(days=WINDOW_DAYS - 1 - i)
            for i in range(WINDOW_DAYS)]


def _date_from_frame(p: Path) -> dt.date | None:
    try:
        return dt.date(int(p.stem[:4]),
                       int(p.stem[4:6]),
                       int(p.stem[6:8]))
    except Exception:  # noqa: BLE001
        return None


def _families_for_source(source: str) -> list[str]:
    return [f for f, cfg in FAMILIES.items() if cfg["source"] == source]


def _map_families() -> list[str]:
    """Subset of FAMILIES rendered as 2D maps (TCHP/D26). Excludes the
    cross-section families which have their own ingest + render path."""
    return [f for f, cfg in FAMILIES.items()
            if not _is_cs_product(PRODUCTS[cfg["product"]])]


def _cs_families() -> list[str]:
    """Subset of FAMILIES rendered as longitude × depth cross-sections."""
    return [f for f, cfg in FAMILIES.items()
            if _is_cs_product(PRODUCTS[cfg["product"]])]


def _missing_dates_for_source(source: str, regions: list[str],
                              dates: list[dt.date]) -> list[dt.date]:
    """Dates for which at least one (family-of-source × region) frame
    is uncached. Used to decide which days actually need a CMEMS /
    OPeNDAP fetch. Considers map families only — cross-section
    bookkeeping is handled by `_missing_dates_for_cs`."""
    families = [f for f in _families_for_source(source)
                if not _is_cs_product(PRODUCTS[FAMILIES[f]["product"]])]
    out: list[dt.date] = []
    for d in dates:
        for fam in families:
            product = PRODUCTS[FAMILIES[fam]["product"]]
            for region in regions:
                if _needs_render(fam, region, product, d):
                    out.append(d)
                    break
            else:
                continue
            break
    return out


def _missing_dates_for_cs(dates: list[dt.date]) -> list[dt.date]:
    """Dates for which at least one (CS-family × CS-region) frame is
    uncached. Triggers the CS slice extraction during chunk processing."""
    out: list[dt.date] = []
    for d in dates:
        for fam in _cs_families():
            product = PRODUCTS[FAMILIES[fam]["product"]]
            for region in CS_REGIONS:
                if _needs_render(fam, region, product, d):
                    out.append(d)
                    break
            else:
                continue
            break
    return out


def pick_end_date(log: str = "[sub-anim]") -> dt.date:
    """Use AOML's latest available date as the canonical window end.
    AOML is consistently a day or two ahead of ARMOR3D NRT and lives
    on a faster public endpoint, so this minimizes the number of empty
    trailing frames in clips. Fall back to today if the OPeNDAP open
    fails (e.g. AOML maintenance window)."""
    try:
        ds = xr.open_dataset(gss.OPENDAP_URL)
        try:
            d = _date_from_np(ds.time.values[-1])
        finally:
            ds.close()
        print(f"{log} AOML latest available: {d}")
        return d
    except Exception as exc:  # noqa: BLE001
        print(f"{log} could not pick end_date from AOML "
              f"({exc}); falling back to today")
        return dt.date.today()


def _render_all_frames(dates: list[dt.date], regions: list[str],
                       countries, coast,
                       log: str = "[sub-anim]") -> dict:
    """For every (date, source, region, product), render to the frame
    cache if not already present. Iterates date-major × source-major
    so each per-day source read fans out across all regions/products
    before being freed.

    Map families only — cross-section families render through
    `_render_cs_all_frames` because their per-day data shape and
    rendering geometry are different."""
    stats = {"rendered": 0, "cached": 0, "skipped_unavailable": 0}
    t0 = time.time()
    map_fams = _map_families()
    sources = sorted({FAMILIES[f]["source"] for f in map_fams})

    total_targets = len(dates) * len(regions) * len(map_fams)
    progressed = 0

    for d in dates:
        for source in sources:
            families = [f for f in _families_for_source(source)
                        if f in map_fams]
            # Skip the day's source fetch entirely if all relevant
            # frames are already cached.
            need_any = False
            for fam in families:
                product = PRODUCTS[FAMILIES[fam]["product"]]
                for region in regions:
                    if _needs_render(fam, region, product, d):
                        need_any = True
                        break
                if need_any:
                    break
            if not need_any:
                stats["cached"] += len(families) * len(regions)
                progressed += len(families) * len(regions)
                continue

            grid = SOURCE_READERS[source](d)
            if grid is None:
                stats["skipped_unavailable"] += len(families) * len(regions)
                progressed += len(families) * len(regions)
                continue
            tchp, d26, lat, lon = grid
            field_by_product = {"tchp": tchp, "d26": d26}
            label = SOURCE_LABEL[source]

            for fam in families:
                product_slug = FAMILIES[fam]["product"]
                product = PRODUCTS[product_slug]
                field = field_by_product[product_slug]
                for region in regions:
                    target = _frame_path(fam, region, product, d)
                    if target.exists():
                        stats["cached"] += 1
                        progressed += 1
                        continue
                    rcfg = gss.REGIONS[region]
                    ok = _render_frame(
                        product, field, lat, lon, rcfg, d,
                        countries, coast, target, label,
                    )
                    if ok:
                        stats["rendered"] += 1
                    else:
                        stats["skipped_unavailable"] += 1
                    progressed += 1

            # Free this day's source field — without `del + gc` here,
            # the ARMOR3D 0.125° grids accumulate enough that the
            # 7 GB CI runner can OOM during the warm-cache pass.
            del grid, tchp, d26, field_by_product
            gc.collect()

        elapsed = time.time() - t0
        rate = (stats["rendered"] + stats["cached"]) / max(1, elapsed)
        print(f"{log} {d}  rendered={stats['rendered']} "
              f"cached={stats['cached']} "
              f"unavail={stats['skipped_unavailable']}  "
              f"({progressed}/{total_targets} targets, "
              f"{rate:.1f} frames/s)", flush=True)

    return stats


def _load_cs_climatology(log: str = "[sub-anim]"
                         ) -> tuple[xr.DataArray | None, np.ndarray | None]:
    """Load the cross-section climatology slice (`t_climo_eq`) plus its
    longitude coordinate from `armor3d/armor3d_climatology.nc`. Returns
    (None, None) when the file or variable is unavailable — the
    cross-section anomaly product then degrades to absolute-T fill."""
    if not gar.CLIMATOLOGY_PATH.exists():
        print(f"{log} CS climatology: NOT AVAILABLE — anomaly frames "
              f"will fall back to absolute T.")
        return None, None
    try:
        ds = xr.open_dataset(gar.CLIMATOLOGY_PATH)
        if "t_climo_eq" not in ds.variables:
            ds.close()
            print(f"{log} CS climatology: t_climo_eq missing — anomaly "
                  f"frames will fall back to absolute T.")
            return None, None
        # Load eagerly + pull the lon grid; we'll keep `ds` open via the
        # returned DataArray's backing store. Cheap (<5 MB).
        t_climo_eq = ds["t_climo_eq"].load()
        climo_lon = ds["longitude"].values.astype(np.float32)
        ds.close()
    except Exception as exc:  # noqa: BLE001
        print(f"{log} CS climatology load failed: {exc}", file=sys.stderr)
        return None, None
    return t_climo_eq, climo_lon


def _render_cs_all_frames(dates: list[dt.date],
                          climo_t_eq: xr.DataArray | None,
                          climo_lon: np.ndarray | None,
                          countries, coast,
                          log: str = "[sub-anim]") -> dict:
    """Cross-section render loop. Iterates dates × CS regions × CS
    families, reading the per-day equatorial-band T slice once per day
    and fanning out across both products (actual + anomaly) and all 4
    regions before freeing it.

    Calls `gar.plot_cross_section` directly so frame visual style and
    geometry stay identical to the static cross-section maps the same
    helper drives."""
    families = _cs_families()
    if not families:
        return {"rendered": 0, "cached": 0, "skipped_unavailable": 0}

    stats = {"rendered": 0, "cached": 0, "skipped_unavailable": 0}
    t0 = time.time()
    total_targets = len(dates) * len(CS_REGIONS) * len(families)
    progressed = 0

    for d in dates:
        # Skip the day's read entirely if every relevant frame already
        # exists on disk. Mirrors the early-out in _render_all_frames.
        need_any = False
        for fam in families:
            product = PRODUCTS[FAMILIES[fam]["product"]]
            for region_key in CS_REGIONS:
                if _needs_render(fam, region_key, product, d):
                    need_any = True
                    break
            if need_any:
                break
        if not need_any:
            stats["cached"] += len(families) * len(CS_REGIONS)
            progressed += len(families) * len(CS_REGIONS)
            continue

        cs_data = _read_armor3d_cs_day(d)
        if cs_data is None:
            stats["skipped_unavailable"] += len(families) * len(CS_REGIONS)
            progressed += len(families) * len(CS_REGIONS)
            continue
        t_eq, lat_band, lon, depth = cs_data

        # Climatology slice for this day's week-of-year. `t_climo_eq`
        # is (week, depth, lon); we sel(week=woy) once per day so each
        # region renders against the correct weekly mean. The same
        # week-of-year math the static generator uses (DOY-based).
        if climo_t_eq is not None:
            woy = max(1, min(52, (d.timetuple().tm_yday - 1) // 7 + 1))
            try:
                t_climo_slice = climo_t_eq.sel(week=woy).values.astype(
                    np.float32
                )
            except Exception as exc:  # noqa: BLE001
                print(f"{log}   ! CS climo slice {d} (woy={woy}) "
                      f"failed: {exc}", file=sys.stderr)
                t_climo_slice = None
        else:
            t_climo_slice = None

        for fam in families:
            product = PRODUCTS[FAMILIES[fam]["product"]]
            mode = product["mode"]
            for region_key in CS_REGIONS:
                target = _frame_path(fam, region_key, product, d)
                if target.exists():
                    stats["cached"] += 1
                    progressed += 1
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                region_cfg = gar.CROSSSECTION_REGIONS[region_key]
                try:
                    ok = gar.plot_cross_section(
                        t_eq, t_climo_slice,
                        lat_band, lon, depth,
                        region_key, region_cfg, d,
                        countries, coast,
                        out_path=target,
                        climo_lon=climo_lon,
                        mode=mode,
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"{log}   ! CS render {fam}/{region_key}/{d} "
                          f"failed: {exc}", file=sys.stderr)
                    ok = False
                if ok:
                    stats["rendered"] += 1
                else:
                    stats["skipped_unavailable"] += 1
                progressed += 1

        # Per-day t_eq is ~33 MB at full lat-band resolution. Drop
        # before the next iteration so 90 days don't pile up.
        del t_eq, cs_data
        gc.collect()

        elapsed = time.time() - t0
        rate = (stats["rendered"] + stats["cached"]) / max(1, elapsed)
        print(f"{log} CS {d}  rendered={stats['rendered']} "
              f"cached={stats['cached']} "
              f"unavail={stats['skipped_unavailable']}  "
              f"({progressed}/{total_targets} targets, "
              f"{rate:.1f} frames/s)", flush=True)

    return stats


def _encode_family(family: str, regions: list[str], end_date: dt.date,
                   log: str = "[sub-anim]") -> tuple[dict, Path]:
    """Encode one MP4 + poster per region for `family`. Returns the
    `clips` dict (manifest payload) and the build dir."""
    fam_cfg = FAMILIES[family]
    product = PRODUCTS[fam_cfg["product"]]
    slug = product["slug"]
    build_dir = BUILD_DIR / family
    build_dir.mkdir(parents=True, exist_ok=True)

    window_dates = _build_window_dates(end_date)
    clips: dict[str, dict] = {}

    for region in regions:
        frames = _list_window_frames(family, region, product, window_dates)
        if not frames:
            print(f"{log}   ! {family}/{region}: no cached frames, skip",
                  flush=True)
            continue
        mp4_name = f"{region}_{slug}.mp4"
        poster_name = f"{region}_{slug}.jpg"
        mp4_path = build_dir / mp4_name
        poster_path = build_dir / poster_name
        try:
            size = _encode_mp4(frames, mp4_path)
        except subprocess.CalledProcessError as exc:
            print(f"{log}   ! {family}/{region} ffmpeg failed: {exc}",
                  file=sys.stderr)
            continue
        try:
            _write_poster(frames[-1], poster_path)
        except Exception as exc:  # noqa: BLE001
            print(f"{log}   ! {family}/{region} poster failed: {exc}",
                  file=sys.stderr)

        first_d = _date_from_frame(frames[0])
        last_d = _date_from_frame(frames[-1])
        clips[f"{region}_{slug}"] = {
            "src": mp4_name,
            "poster": poster_name,
            "region": region,
            "product": slug,
            "first_frame": first_d.isoformat() if first_d else None,
            "last_frame":  last_d.isoformat()  if last_d  else None,
            "frames": len(frames),
            "duration_s": round(len(frames) / FPS, 3),
            "bytes": size,
        }
        print(f"{log}   ✓ {family}/{region}  "
              f"({len(frames)} frames, {size / 1024:.0f} KB)",
              flush=True)
    return clips, build_dir


def _write_family_manifest(family: str, clips: dict, regions: list[str],
                           end_date: dt.date, build_dir: Path) -> Path:
    """Write the per-family manifest the widget reads. Schema matches
    the SST animator's manifest exactly so no widget change is needed
    — each subsurface family is just another `family` URL the existing
    `data-sources` config can point at.

    Region metadata is sourced from `gss.REGIONS` for map families and
    from `gar.CROSSSECTION_REGIONS` for cross-section families. The
    schema is identical from the widget's perspective."""
    fam_cfg = FAMILIES[family]
    product = PRODUCTS[fam_cfg["product"]]
    is_cs = _is_cs_product(product)
    region_entries: list[dict] = []
    for r in regions:
        if is_cs:
            cs = gar.CROSSSECTION_REGIONS.get(r, {})
            region_entries.append({
                "slug": r,
                "label": cs.get("label", r),
                # Cross-section regions don't have a 2D extent in the
                # map sense; emit lon/lat bounds instead so the schema
                # is uniform but the values are honest.
                "extent": [
                    cs.get("lon_min"), cs.get("lon_max"),
                    cs.get("lat_min"), cs.get("lat_max"),
                ],
            })
        else:
            region_entries.append({
                "slug": r,
                "label": gss.REGIONS[r]["label"],
                "extent": list(gss.REGIONS[r]["extent"]),
            })
    manifest = {
        "family": family,
        "generated_at":
            dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "frame_rate_fps": FPS,
        "window": {"unit": "days", "length": WINDOW_DAYS},
        "regions": region_entries,
        "products": [
            {
                "slug": product["slug"],
                "label": product["label"],
                "description": product["description"],
            }
        ],
        "source": SOURCE_LABEL[fam_cfg["source"]],
        "clips": clips,
    }
    out = build_dir / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"[sub-anim] wrote {out}  ({len(clips)} clips)")
    return out


def _write_branch_readme() -> Path:
    """Same orphan-branch README the SST animator writes — overwritten
    only if missing. The SST workflow already creates this file the
    first time it runs, so this is a fallback for when the subsurface
    workflow lands first."""
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    out = BUILD_DIR / "README.md"
    if out.exists():
        return out
    out.write_text(
        "# Triple-A-Tropics — `mp4-artifacts` branch\n"
        "\n"
        "**This branch is regenerated by CI and force-pushed on every "
        "`update-{sst,subsurface}-animations.yml` workflow run.**\n"
        "\n"
        "Each subdirectory holds the latest MP4 animations + poster JPGs "
        "for one product family, plus a `manifest.json` the on-page "
        "`<video>` widget reads to populate its dropdowns.\n"
        "\n"
        "Layout:\n"
        "\n"
        "```\n"
        "sst/                            — 90-day daily-cadence SST animations\n"
        "aoml_tchp/                      — 90-day daily AOML TCHP\n"
        "aoml_d26/                       — 90-day daily AOML D26\n"
        "armor3d_tchp/                   — 90-day daily ARMOR3D TCHP\n"
        "armor3d_d26/                    — 90-day daily ARMOR3D D26\n"
        "armor3d_crosssection_actual/    — 90-day daily ARMOR3D 5°S–5°N T cross-section\n"
        "armor3d_crosssection_anomaly/   — 90-day daily ARMOR3D 5°S–5°N T anomaly cross-section\n"
        "```\n"
        "\n"
        "## Don't file PRs against this branch\n"
        "\n"
        "History is intentionally ephemeral — every workflow run does "
        "`git push --force` against just its own family directories. "
        "Source code, including the generators that produce these MP4s, "
        "lives on `main`.\n"
    )
    print(f"[sub-anim] wrote {out}")
    return out


# ----------------------------------------------------------------------
# Entrypoint
# ----------------------------------------------------------------------
def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Render 90-day MP4 animations for AOML + ARMOR3D "
                    "TCHP/D26.",
    )
    p.add_argument("--end-date",
                   help="Window end date YYYY-MM-DD (default: latest "
                        "AOML day available).")
    p.add_argument("--regions", nargs="*",
                   help="Subset of region keys to render (default: all "
                        "18).")
    p.add_argument("--families", nargs="*", choices=list(FAMILIES.keys()),
                   help="Subset of family keys to encode (default: all "
                        "four). Pre-fetch + render still runs for both "
                        "sources unless filtered.")
    p.add_argument("--skip-armor3d", action="store_true",
                   help="Skip ARMOR3D pre-fetch + render (useful when "
                        "iterating on AOML locally without CMEMS creds).")
    p.add_argument("--skip-aoml", action="store_true",
                   help="Skip AOML pre-fetch + render.")
    p.add_argument("--clean-build", action="store_true",
                   help="Wipe _mp4_build/ before staging (does NOT touch "
                        "frame or data caches).")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    log = "[sub-anim]"

    if args.end_date:
        end_date = dt.date.fromisoformat(args.end_date)
    else:
        end_date = pick_end_date(log)
    dates = _build_window_dates(end_date)
    print(f"{log} window: {dates[0]} → {end_date}  "
          f"({WINDOW_DAYS} days)")

    regions = args.regions or list(gss.REGIONS.keys())
    families = args.families or list(FAMILIES.keys())
    print(f"{log} regions: {len(regions)}  families: {len(families)}  "
          f"clips: {len(regions) * len(families)}")

    if args.clean_build and BUILD_DIR.exists():
        for fam in FAMILIES:
            fam_dir = BUILD_DIR / fam
            if fam_dir.exists():
                shutil.rmtree(fam_dir)

    countries = gss._load_geojson("ne_50m_admin_0_countries.geojson")
    coast = gss._load_geojson("ne_50m_coastline.geojson")
    if not countries or not coast:
        print(f"{log} WARN: Natural Earth GeoJSONs missing — frames will "
              f"render without basemap", file=sys.stderr)

    # Phase 1: per-source bulk pre-fetch. Each phase computes its own
    # missing-date list and is free to no-op if there's nothing to do.
    # The cross-section families share ARMOR3D's CMEMS fetch — when any
    # CS family is selected, the same chunk processing also writes the
    # equatorial-band T cache (no extra CMEMS calls).
    cs_families_selected = [f for f in families
                            if f in _cs_families()]
    map_families_selected = [f for f in families
                             if f in _map_families()]
    want_cs = bool(cs_families_selected) and not args.skip_armor3d

    if not args.skip_aoml:
        aoml_missing = _missing_dates_for_source("aoml", regions, dates)
        prefetch_aoml(aoml_missing, log)
    if not args.skip_armor3d:
        armor3d_missing = _missing_dates_for_source(
            "armor3d", regions, dates,
        )
        if want_cs:
            cs_missing = _missing_dates_for_cs(dates)
            armor3d_missing = sorted(set(armor3d_missing) | set(cs_missing))
        prefetch_armor3d(armor3d_missing, log, want_cs=want_cs)

    # Phase 2: render. Map families first (TCHP/D26), then cross-section
    # families. They iterate the same date list but read different per-
    # day caches so they can't be merged into one loop.
    t0 = time.time()
    stats = _render_all_frames(dates, regions, countries, coast, log)
    print(f"{log} map render phase: {time.time() - t0:.1f}s — {stats}")

    if cs_families_selected:
        t_cs = time.time()
        climo_t_eq, climo_lon_arr = _load_cs_climatology(log)
        cs_stats = _render_cs_all_frames(
            dates, climo_t_eq, climo_lon_arr,
            countries, coast, log,
        )
        print(f"{log} cs render phase: {time.time() - t_cs:.1f}s — "
              f"{cs_stats}")

    # Phase 3: encode + manifest per family
    t1 = time.time()
    total_clips = 0
    for family in families:
        fam_regions = (
            CS_REGIONS
            if _is_cs_product(PRODUCTS[FAMILIES[family]["product"]])
            else regions
        )
        clips, build_dir = _encode_family(family, fam_regions, end_date, log)
        _write_family_manifest(family, clips, fam_regions, end_date, build_dir)
        total_clips += len(clips)
    print(f"{log} encode phase: {time.time() - t1:.1f}s — "
          f"{total_clips} clips across {len(families)} families")

    _write_branch_readme()
    _prune_old_frames(end_date)

    print(f"{log} done in {time.time() - t0:.1f}s "
          f"(staged at {BUILD_DIR}/)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
