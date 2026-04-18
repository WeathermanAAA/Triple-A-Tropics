#!/usr/bin/env python3
"""
Triple-A-Tropics · SST map generator
=====================================

Renders three PNG products per region from the latest NOAA OISST v2.1 daily
file:

  * Actual SST        (turbo rainbow, 0–32 °C, integer contours)
  * SST anomaly       (diverging blue→red, climatology = 1991–2020 mean)
  * Anomaly + records (same anomaly map with stippling where today's value
                       meets or exceeds the 1982-present record high or low
                       for this day of the year)

Data source
-----------
NOAA NCEI OISST v2.1 daily files, one per UTC day since 1982:
    https://www.ncei.noaa.gov/data/sea-surface-temperature-optimum-
    interpolation/v2.1/access/avhrr/{YYYYMM}/oisst-avhrr-v02r01.{YYYYMMDD}.nc

Each run downloads the latest available day plus every historical year's
file for the same day-of-year (~45 files, ~70 MB). That gives us today's
SST, the 1991-2020 climatology, and the 1982-present record envelope in
one pass — no persistent state needed between runs.

Regions
-------
    global        (-180 .. 180, -55 .. 60)
    atlantic      (-100 ..   0,   0 .. 60)
    west-pacific  ( 100 .. 180,   0 .. 60)  — crosses the dateline
    east-pacific  (-180 .. -80,   0 .. 50)

Outputs
-------
    sst/<region>_actual.png
    sst/<region>_anomaly.png
    sst/<region>_anomaly_records.png
    sst/sst_meta.json          (data date, counts used, bounds)

No cartopy dependency. Uses Natural Earth GeoJSON (same files the tracks
pages already download) for coastlines + country borders, which keeps the
workflow install footprint small.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import io
import json
import os
import sys
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")
# Black diagonal hatching for the records overlay (applied via contourf
# with colors="none" and hatches=["///"]). These rcParams set the lines'
# color and thickness globally — matplotlib picks them up at plot time.
mpl.rcParams["hatch.color"] = "#000000"
mpl.rcParams["hatch.linewidth"] = 0.7
import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import requests
from netCDF4 import Dataset

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE
SST_DIR = OUTPUT_DIR / "sst"
SST_DIR.mkdir(parents=True, exist_ok=True)

# --- Data source --------------------------------------------------------

OISST_BASE = (
    "https://www.ncei.noaa.gov/data/sea-surface-temperature-optimum-"
    "interpolation/v2.1/access/avhrr"
)
# OISST v2.1 has a ~1-day latency. We try today-1 first and fall back
# through earlier days if the latest file isn't up yet. 7 days of runway
# covers holiday gaps in NCEI's publishing schedule.
LATENCY_TRIES = 7

# Baseline & records window
CLIMO_START = 1991
CLIMO_END = 2020
RECORDS_START = 1982  # full OISST era

# Keep a small local cache of raw NetCDF files so re-runs in the same
# workday don't hammer NCEI for the same bytes.
CACHE_DIR = HERE / ".sst_cache"
CACHE_DIR.mkdir(exist_ok=True)

# Parallelism: 12 concurrent fetches is plenty on a GitHub runner without
# looking like a DoS to NCEI.
FETCH_WORKERS = 12
FETCH_TIMEOUT = 45
FETCH_RETRIES = 3


def oisst_url_candidates(d: dt.date) -> list[str]:
    """Return candidate URLs for a given UTC day.

    NCEI publishes OISST v2.1 in two flavors — the preliminary file
    (available within ~1-2 days of observation) and the final file
    (typically 1-2 weeks later, replacing the preliminary). For recent
    dates only the preliminary exists; for older dates only the final.
    We try both and take the first that returns 200.
    """
    base = f"{OISST_BASE}/{d.year:04d}{d.month:02d}"
    stamp = f"{d.year:04d}{d.month:02d}{d.day:02d}"
    return [
        f"{base}/oisst-avhrr-v02r01.{stamp}.nc",
        f"{base}/oisst-avhrr-v02r01.{stamp}_preliminary.nc",
    ]


def cache_path(d: dt.date) -> Path:
    return CACHE_DIR / f"oisst.{d:%Y%m%d}.nc"


def fetch_day(d: dt.date, log_prefix: str = "[sst]",
              verbose: bool = False) -> Path | None:
    """Download the OISST NetCDF for a specific UTC day. Cache-hit fast-path;
    falls back to the preliminary filename if the final one 404s."""
    cp = cache_path(d)
    if cp.exists() and cp.stat().st_size > 100_000:
        return cp
    last_status = None
    for url in oisst_url_candidates(d):
        for attempt in range(FETCH_RETRIES):
            try:
                r = requests.get(url, timeout=FETCH_TIMEOUT)
                last_status = r.status_code
                if r.status_code == 404:
                    break  # try next URL flavor
                r.raise_for_status()
                if len(r.content) < 100_000:
                    # Likely a redirect or error page, not a real NetCDF
                    break
                cp.write_bytes(r.content)
                if verbose:
                    print(f"{log_prefix}   ✓ {d} ← {url.split('/')[-1]}")
                return cp
            except Exception as e:  # noqa: BLE001
                if attempt == FETCH_RETRIES - 1:
                    if verbose:
                        print(
                            f"{log_prefix}   fetch error {d} ({url.split('/')[-1]}): "
                            f"{type(e).__name__}: {e}",
                            file=sys.stderr,
                        )
                    break
    if verbose and last_status is not None:
        print(f"{log_prefix}   ✗ {d} (last HTTP {last_status})")
    return None


def latest_available_day(log_prefix: str = "[sst]") -> tuple[dt.date, Path]:
    """Start at yesterday UTC and walk back until a day's file exists.

    Both the `.nc` (final) and `_preliminary.nc` flavors are tried per date;
    logs each URL attempt so a run's failure mode is inspectable."""
    today = dt.datetime.utcnow().date()
    for back in range(1, LATENCY_TRIES + 1):
        d = today - dt.timedelta(days=back)
        print(f"{log_prefix} trying {d} ...")
        p = fetch_day(d, log_prefix, verbose=True)
        if p is not None:
            print(f"{log_prefix} latest available: {d}")
            return d, p
    raise RuntimeError(
        f"Could not fetch any OISST file in the last {LATENCY_TRIES} days"
    )


def day_of_year_files(
    target_month: int, target_day: int, year_range: range,
    log_prefix: str = "[sst]",
) -> dict[int, Path]:
    """Fetch every year's NetCDF for (target_month, target_day).

    Handles the Feb-29 edge case by falling back to Feb-28 in non-leap years.
    Returns a dict keyed by year, only years whose fetch succeeded.
    """
    want: list[tuple[int, dt.date]] = []
    for y in year_range:
        try:
            want.append((y, dt.date(y, target_month, target_day)))
        except ValueError:
            # Feb 29 in a non-leap year → use Feb 28
            want.append((y, dt.date(y, target_month, 28)))

    out: dict[int, Path] = {}
    # Download concurrently
    with cf.ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        futures = {pool.submit(fetch_day, d, log_prefix): (y, d) for y, d in want}
        for fut in cf.as_completed(futures):
            y, d = futures[fut]
            p = fut.result()
            if p is not None:
                out[y] = p
    print(
        f"{log_prefix} downloaded {len(out)}/{len(want)} historical files "
        f"for {target_month:02d}-{target_day:02d}"
    )
    return out


# --- NetCDF reading -----------------------------------------------------


def read_sst_grid(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (sst_celsius 2-D, lat 1-D, lon 1-D). Lat/lon are in the file's
    native order (90 S → 90 N, 0 → 360 E). Masked/missing values become NaN."""
    with Dataset(path, "r") as ds:
        sst = ds.variables["sst"][:]
        # Some OISST files have (time, zlev, lat, lon); squeeze 1-length axes.
        sst = np.ma.squeeze(sst)
        sst = np.ma.filled(sst.astype(np.float32), np.nan)
        lat = ds.variables["lat"][:].astype(np.float32)
        lon = ds.variables["lon"][:].astype(np.float32)
    return sst, lat, lon


def stack_years(files_by_year: dict[int, Path]) -> tuple[np.ndarray, list[int]]:
    """Stack per-year SST grids into a (n_years, lat, lon) array."""
    years = sorted(files_by_year)
    grids = []
    for y in years:
        g, _, _ = read_sst_grid(files_by_year[y])
        grids.append(g)
    return np.stack(grids, axis=0), years


# ============================================================================
# CRW (NOAA Coral Reef Watch 5 km) data source
# ============================================================================
# CRW publishes a daily suite of 5 km gridded products. We use:
#   • coraltemp_v3.1_YYYYMMDD.nc   — "actual" SST analysis   (var: analysed_sst)
#   • ct5km_ssta_v3.1_YYYYMMDD.nc  — official SST anomaly    (var: sea_surface_temperature_anomaly)
# CRW stores longitudes as -180..+180; we normalize to 0..360 on read so the
# shared subset/plot code works uniformly with OISST.

CRW_BASE = (
    "https://www.star.nesdis.noaa.gov/pub/sod/mecb/crw/data/5km/"
    "v3.1_op/nc/v1.0/daily"
)
CRW_RECORDS_START = 1985  # CoralTemp v3.1 archive starts here


def crw_url_for(product: str, d: dt.date) -> str:
    """Build the URL for a given CRW product ('sst' or 'ssta') on date d."""
    y = d.year
    if product == "sst":
        fname = f"coraltemp_v3.1_{d:%Y%m%d}.nc"
    else:
        fname = f"ct5km_{product}_v3.1_{d:%Y%m%d}.nc"
    return f"{CRW_BASE}/{product}/{y:04d}/{fname}"


def crw_cache_path(d: dt.date, product: str) -> Path:
    return CACHE_DIR / f"crw_{product}.{d:%Y%m%d}.nc"


def fetch_crw_day(d: dt.date, product: str,
                  log_prefix: str = "[sst-crw]",
                  verbose: bool = False) -> Path | None:
    """Download a specific CRW product file for a specific day, cached."""
    cp = crw_cache_path(d, product)
    if cp.exists() and cp.stat().st_size > 100_000:
        return cp
    url = crw_url_for(product, d)
    for attempt in range(FETCH_RETRIES):
        try:
            r = requests.get(url, timeout=FETCH_TIMEOUT)
            if r.status_code == 404:
                if verbose:
                    print(f"{log_prefix}   ✗ CRW {product} {d} (404)")
                return None
            r.raise_for_status()
            if len(r.content) < 100_000:
                return None
            cp.write_bytes(r.content)
            if verbose:
                print(f"{log_prefix}   ✓ CRW {product} {d}")
            return cp
        except Exception as e:  # noqa: BLE001
            if attempt == FETCH_RETRIES - 1:
                if verbose:
                    print(f"{log_prefix}   CRW {product} {d} error: "
                          f"{type(e).__name__}: {e}", file=sys.stderr)
                return None
    return None


def latest_available_crw_day(product: str = "sst",
                             log_prefix: str = "[sst-crw]"
                             ) -> tuple[dt.date, Path]:
    """Walk back from yesterday UTC until a CRW file for `product` exists."""
    today = dt.datetime.utcnow().date()
    for back in range(1, LATENCY_TRIES + 1):
        d = today - dt.timedelta(days=back)
        print(f"{log_prefix} trying CRW {product} {d} ...")
        p = fetch_crw_day(d, product, log_prefix, verbose=True)
        if p is not None:
            print(f"{log_prefix} latest CRW {product}: {d}")
            return d, p
    raise RuntimeError(
        f"Could not fetch any CRW {product} file in the last {LATENCY_TRIES} days"
    )


def day_of_year_crw_files(target_month: int, target_day: int,
                          year_range: range,
                          product: str = "sst",
                          log_prefix: str = "[sst-crw]") -> dict[int, Path]:
    """Parallel fetch of CRW files at the same day-of-year across years."""
    want: list[tuple[int, dt.date]] = []
    for y in year_range:
        try:
            want.append((y, dt.date(y, target_month, target_day)))
        except ValueError:
            want.append((y, dt.date(y, target_month, 28)))

    out: dict[int, Path] = {}
    with cf.ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        futures = {
            pool.submit(fetch_crw_day, d, product, log_prefix, False):
            (y, d) for y, d in want
        }
        for fut in cf.as_completed(futures):
            y, d = futures[fut]
            p = fut.result()
            if p is not None:
                out[y] = p
    print(
        f"{log_prefix} downloaded {len(out)}/{len(want)} historical CRW "
        f"{product} files for {target_month:02d}-{target_day:02d}"
    )
    return out


def read_crw_grid(path: Path, var_name: str
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Read a CRW NetCDF file; normalize lon to 0..360 for consistency."""
    with Dataset(path, "r") as ds:
        # Auto-fallback if the expected variable isn't there
        if var_name not in ds.variables:
            for name, v in ds.variables.items():
                if name not in ("lat", "lon", "time") and v.ndim >= 2:
                    var_name = name
                    break
        raw = ds.variables[var_name][:]
        data = np.ma.squeeze(raw)
        data = np.ma.filled(data.astype(np.float32), np.nan)
        lat = ds.variables["lat"][:].astype(np.float32)
        lon = ds.variables["lon"][:].astype(np.float32)
    # Roll CRW's -180..180 into 0..360 so _subset_to_extent can share logic.
    if float(np.nanmin(lon)) < 0:
        lon_rolled = np.where(lon < 0, lon + 360.0, lon)
        order = np.argsort(lon_rolled)
        lon = lon_rolled[order]
        data = data[:, order]
    return data, lat, lon


def stack_crw_years(files_by_year: dict[int, Path], var_name: str
                    ) -> tuple[np.ndarray, list[int]]:
    years = sorted(files_by_year)
    grids = []
    for y in years:
        g, _, _ = read_crw_grid(files_by_year[y], var_name)
        grids.append(g)
    return np.stack(grids, axis=0), years


# --- Projection + basemap overlay ---------------------------------------
# Use the same ne_50m geojson files the tracks pages already use.


def _load_geojson(name: str) -> dict | None:
    p = HERE / name
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _feature_linestrings(feat: dict) -> list[list[tuple[float, float]]]:
    geom = feat.get("geometry") or {}
    t = geom.get("type")
    out: list[list[tuple[float, float]]] = []
    if t == "LineString":
        out.append([(p[0], p[1]) for p in geom["coordinates"]])
    elif t == "MultiLineString":
        for line in geom["coordinates"]:
            out.append([(p[0], p[1]) for p in line])
    elif t == "Polygon":
        for ring in geom["coordinates"]:
            out.append([(p[0], p[1]) for p in ring])
    elif t == "MultiPolygon":
        for poly in geom["coordinates"]:
            for ring in poly:
                out.append([(p[0], p[1]) for p in ring])
    return out


# --- Region definitions -------------------------------------------------

REGIONS: dict[str, dict] = {
    # Full-earth overview
    "global": {
        "label": "Global",
        "extent": (-180.0, 180.0, -75.0, 75.0),
        "figsize": (14.5, 7.2),
    },
    # Tropical belt (narrower latitude band than Global)
    "global-tropics": {
        "label": "Global Tropics",
        "extent": (-180.0, 180.0, -45.0, 45.0),
        "figsize": (14.5, 5.6),
    },
    # ENSO monitoring view — tropical Pacific spanning the dateline
    # (120°E through 70°W). `extent` longitudes use the 0-360 convention
    # so the subset code wraps cleanly across the dateline.
    "enso": {
        "label": "ENSO Regions",
        "extent": (120.0, 290.0, -15.0, 15.0),
        "figsize": (15.0, 3.8),
    },

    # --- Atlantic ---
    "north-atlantic": {
        "label": "North Atlantic",
        "extent": (-100.0, 0.0, 0.0, 65.0),
        "figsize": (10.5, 7.0),
    },
    "tropical-atlantic": {
        "label": "Tropical Atlantic",
        "extent": (-90.0, -10.0, 0.0, 30.0),
        "figsize": (11.5, 5.4),
    },
    "western-atlantic": {
        "label": "Western Atlantic",
        "extent": (-100.0, -55.0, 8.0, 42.0),
        "figsize": (8.8, 7.0),
    },
    "equatorial-atlantic": {
        "label": "Equatorial Atlantic",
        "extent": (-50.0, 15.0, -10.0, 15.0),
        "figsize": (11.0, 5.2),
    },
    "south-atlantic": {
        "label": "South Atlantic",
        "extent": (-70.0, 20.0, -55.0, 0.0),
        "figsize": (10.5, 7.0),
    },

    # --- Pacific ---
    "northeast-pacific": {
        "label": "Northeast Pacific",
        "extent": (-160.0, -80.0, 0.0, 50.0),
        "figsize": (11.0, 7.0),
    },
    "east-pacific": {
        "label": "East Pacific",
        "extent": (-140.0, -80.0, 5.0, 35.0),
        "figsize": (11.0, 5.8),
    },
    "central-pacific": {
        "label": "Central Pacific",
        "extent": (-180.0, -140.0, 0.0, 35.0),
        "figsize": (8.0, 7.0),
    },
    "northwest-pacific": {
        "label": "Northwest Pacific",
        "extent": (100.0, 180.0, 0.0, 60.0),
        "figsize": (10.5, 7.5),
    },
    "north-pacific": {
        "label": "North Pacific",
        "extent": (100.0, 260.0, 0.0, 65.0),   # crosses dateline
        "figsize": (14.5, 6.5),
    },
    "southwest-pacific": {
        "label": "Southwest Pacific",
        "extent": (140.0, 220.0, -45.0, 0.0),  # crosses dateline
        "figsize": (11.0, 6.5),
    },
    "southeast-pacific": {
        "label": "Southeast Pacific",
        "extent": (-140.0, -70.0, -50.0, 0.0),
        "figsize": (10.5, 7.5),
    },

    # --- Other basins ---
    "australia": {
        "label": "Australia",
        "extent": (95.0, 180.0, -45.0, 5.0),
        "figsize": (11.5, 7.0),
    },
    "indian-ocean": {
        "label": "Indian Ocean",
        "extent": (30.0, 130.0, -40.0, 30.0),
        "figsize": (11.0, 7.0),
    },
    "mediterranean": {
        "label": "Mediterranean",
        "extent": (-10.0, 42.0, 28.0, 48.0),
        "figsize": (11.5, 4.8),
    },
}


# --- Colormaps ----------------------------------------------------------

def _sst_actual_cmap() -> mcolors.LinearSegmentedColormap:
    """Rainbow-ish gradient matching the 2nd reference image.
    Violet/indigo at 0 °C → deep blue → cyan → green → yellow → orange →
    red → dark red at 32 °C."""
    stops = [
        (0.00, "#2c0b4a"),  # dark violet
        (0.08, "#2a1794"),  # indigo
        (0.18, "#2f4bc4"),  # blue
        (0.28, "#2e8bd0"),  # sky
        (0.38, "#2fc4c9"),  # cyan
        (0.50, "#6bd98e"),  # green
        (0.62, "#e7ee5f"),  # yellow
        (0.72, "#f5b23d"),  # orange
        (0.82, "#e84b2a"),  # red
        (0.92, "#b01a26"),  # dark red
        (1.00, "#6b0d18"),  # oxblood
    ]
    return mcolors.LinearSegmentedColormap.from_list(
        "sst_actual", stops, N=256
    )


def _sst_anom_cmap() -> mcolors.LinearSegmentedColormap:
    """Diverging cool-to-warm, tuned so small anomalies are already
    visibly colored (light blue below 0, light yellow above) while
    larger anomalies ramp through red for warm and indigo for cold.

    Cold: indigo ≤ -5 → royal blue → blue → mid blue → light blue →
           pale sky blue around -0.4 → very faint → zero.
    Warm: zero → pale yellow (+0.2) → light yellow (+0.5) → gold (+1) →
           coral (+1.8) → red (+2.6) → dark red (+3.4) → oxblood →
           hot-pink magenta ≥ +5.
    """
    stops = [
        (0.00, "#1a0c5f"),  # deep indigo (≤ -5)
        (0.08, "#1a2b9e"),  # royal blue-violet
        (0.18, "#2261c7"),  # blue (-3)
        (0.30, "#4695db"),  # mid blue (-2)
        (0.40, "#8bc0ea"),  # light blue (-1)
        (0.47, "#cde5f5"),  # pale sky blue (-0.3)
        (0.495, "#f2f7fb"), # near-zero cool
        (0.50, "#ffffff"),  # zero (thin)
        (0.506, "#fdf4ea"), # near-zero warm (barely)
        (0.53, "#f8d5b8"),  # pale peach (+0.3)
        (0.58, "#efac86"),  # light salmon (+0.8)
        (0.65, "#df815f"),  # coral (+1.5)
        (0.73, "#cc4836"),  # warm red (+2.3)
        (0.82, "#9f1e26"),  # red (+3.2)
        (0.90, "#6d1321"),  # dark red (+4)
        (0.96, "#3f0c23"),  # oxblood (+4.6)
        (1.00, "#ef37b8"),  # hot-pink magenta (≥ +5)
    ]
    return mcolors.LinearSegmentedColormap.from_list(
        "sst_anom", stops, N=256
    )


CMAP_ACTUAL = _sst_actual_cmap()
CMAP_ANOM = _sst_anom_cmap()
# NaN pixels (land, no-data) render as LAND_COLOR so we don't need to
# draw filled country polygons — simpler, and avoids the dateline-wrap
# rectangle bug on wide Pacific extents.
CMAP_ACTUAL.set_bad(color="#0b1a30", alpha=1.0)
CMAP_ANOM.set_bad(color="#0b1a30", alpha=1.0)


# --- Plotting -----------------------------------------------------------

# Plot background / foreground colors — match the site's dark theme.
BG_COLOR = "#07101c"
PANEL_COLOR = "#0a1324"
LAND_COLOR = "#0b1a30"          # where sea isn't, show near-black land
COAST_COLOR = "#ffffff"
BORDER_COLOR = "#ffffff"
TEXT_COLOR = "#e5edf6"
MUTED_COLOR = "#8ea2bd"
WATERMARK = "@WeathermanAAA_"


def _normalize_lons_for_extent(lon: np.ndarray, extent: tuple) -> np.ndarray:
    """OISST uses 0→360 longitudes. For regions whose extent uses -180→180
    (Atlantic, East Pacific, global), roll the longitude axis so it's
    monotone across the region. For West Pacific (100→180, crosses 180),
    keep 0→360."""
    lon_min, lon_max, _, _ = extent
    if lon_min < 0:
        # convert to -180..180
        return np.where(lon > 180, lon - 360, lon)
    return lon


def _subset_to_extent(
    sst: np.ndarray, lat: np.ndarray, lon: np.ndarray, extent: tuple
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return sst/lat/lon cropped to `extent`. Handles the dateline and the
    0→360 vs -180→180 roll automatically."""
    lon_min, lon_max, lat_min, lat_max = extent
    lat_mask = (lat >= lat_min) & (lat <= lat_max)

    lon_adj = _normalize_lons_for_extent(lon, extent)
    # Re-sort longitudes so they ascend monotonically inside the extent.
    order = np.argsort(lon_adj)
    lon_sorted = lon_adj[order]
    sst_sorted = sst[:, order]

    lon_mask = (lon_sorted >= lon_min) & (lon_sorted <= lon_max)
    return (
        sst_sorted[np.ix_(lat_mask, lon_mask)],
        lat[lat_mask],
        lon_sorted[lon_mask],
    )


def _draw_basemap(ax, extent: tuple, countries, coast) -> None:
    """Draw coastline + country border LINES (no filled polygons).

    Land is painted directly by the SST colormap via set_bad() on its
    NaN pixels. Skipping ax.fill() avoids a nasty bug: countries that
    span the dateline (Russia, USA) get coord-wrapped into polygons
    whose ring order is no longer sane, causing matplotlib.fill() to
    paint enormous spurious rectangles across the ocean — the "dark
    bands" we were seeing on North Pacific / ENSO / Southwest Pacific.
    """
    lon_min, lon_max, lat_min, lat_max = extent
    wraps_dateline = lon_max > 180

    def _wrap_coord(x):
        if wraps_dateline and x < 0:
            return x + 360
        return x

    def _draw_feature_lines(features, color, linewidth, zorder):
        for feat in features:
            for ring in _feature_linestrings(feat):
                if not ring:
                    continue
                xs = [_wrap_coord(x) for x, _ in ring]
                ys = [y for _, y in ring]
                if max(ys) < lat_min or min(ys) > lat_max:
                    continue
                if max(xs) < lon_min or min(xs) > lon_max:
                    continue
                # Break the line at any big longitude jump (>90°) — that
                # happens when a polygon edge crosses the wrap point
                # and we don't want a horizontal line slicing the map.
                segs: list[list[tuple[float, float]]] = [[]]
                prev_x = xs[0]
                for x, y in zip(xs, ys):
                    if segs[-1] and abs(x - prev_x) > 90:
                        segs.append([])
                    segs[-1].append((x, y))
                    prev_x = x
                for seg in segs:
                    if len(seg) < 2:
                        continue
                    ax.plot([p[0] for p in seg], [p[1] for p in seg],
                            color=color, linewidth=linewidth, zorder=zorder,
                            solid_capstyle="round", solid_joinstyle="round")

    if countries:
        _draw_feature_lines(countries.get("features", []),
                            BORDER_COLOR, 0.7, 3)
    if coast:
        _draw_feature_lines(coast.get("features", []),
                            COAST_COLOR, 0.8, 3)


def _lon_tick_label(x: float, _pos) -> str:
    """Format a longitude tick value.

    OISST uses 0–360° longitudes internally; for dateline-crossing
    extents we therefore pass tick values that can be >180. Convert
    those to the more familiar −180…+180 convention and append E/W."""
    v = float(x)
    # Fold >180 into the negative hemisphere for display
    while v > 180:
        v -= 360
    while v < -180:
        v += 360
    iv = int(round(v))
    if iv == 0 or iv == 180 or iv == -180:
        return f"{abs(iv)}°"
    if iv > 0:
        return f"{iv}°E"
    return f"{-iv}°W"


def _lat_tick_label(y: float, _pos) -> str:
    iv = int(round(float(y)))
    if iv == 0:
        return "0°"
    return f"{abs(iv)}°{'N' if iv > 0 else 'S'}"


def _style_axes(ax, extent, title, subtitle):
    lon_min, lon_max, lat_min, lat_max = extent
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_aspect("auto")
    ax.set_facecolor(PANEL_COLOR)
    # Lat/lon gridlines
    ax.xaxis.set_major_locator(mticker.MultipleLocator(20))
    ax.yaxis.set_major_locator(mticker.MultipleLocator(10))
    # Proper geographic formatting: -140°W, 120°E, etc. — even for
    # dateline-crossing extents that use >180 coordinates internally.
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(_lon_tick_label))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_lat_tick_label))
    ax.grid(True, linewidth=0.3, color="#2a3e5c", alpha=0.5, zorder=1)
    ax.tick_params(colors=MUTED_COLOR, labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(MUTED_COLOR)
        spine.set_linewidth(0.5)
    # Title block — placed in axes coordinates above the plot. Two
    # stacked text objects with explicit y offsets so the bold title
    # and the muted subtitle don't overlap each other.
    ax.text(0.0, 1.07, title, color=TEXT_COLOR, fontsize=14,
            fontweight="bold", transform=ax.transAxes, va="bottom")
    if subtitle:
        ax.text(0.0, 1.015, subtitle, color=MUTED_COLOR, fontsize=10,
                transform=ax.transAxes, va="bottom")


def _draw_watermark(ax):
    ax.text(
        0.995, 0.01, WATERMARK, transform=ax.transAxes,
        ha="right", va="bottom", fontsize=11, fontweight="bold",
        color="#ffffff", alpha=0.35,
        path_effects=[_mpl_stroke("#000000", 0.55, 1.2)],
    )


def _mpl_stroke(color, alpha, width):
    from matplotlib import patheffects as pe
    return pe.withStroke(linewidth=width, foreground=mcolors.to_rgba(color, alpha))


def _add_colorbar(fig, mappable, label, ticks=None, extend="both"):
    cax = fig.add_axes([0.91, 0.18, 0.018, 0.64])
    cb = fig.colorbar(mappable, cax=cax, extend=extend)
    cb.set_label(label, color=TEXT_COLOR, fontsize=10)
    cb.ax.yaxis.set_tick_params(color=MUTED_COLOR, labelcolor=MUTED_COLOR,
                                labelsize=9)
    cb.outline.set_edgecolor(MUTED_COLOR)
    cb.outline.set_linewidth(0.4)
    if ticks is not None:
        cb.set_ticks(ticks)
    return cb


def plot_actual(
    sst_today, lat, lon, extent, figsize, title, subtitle,
    countries, coast, out_path: Path,
):
    """Rainbow actual-SST plot with integer-degree contours.

    Uses pcolormesh (one rectangle per grid cell) for the fill so
    wide / dateline-crossing extents don't get triangulation banding
    artifacts the way contourf did."""
    sub, la, lo = _subset_to_extent(sst_today, lat, lon, extent)
    if sub.size == 0:
        return
    fig, ax = plt.subplots(figsize=figsize, facecolor=BG_COLOR)
    LON2, LAT2 = np.meshgrid(lo, la)
    norm = mcolors.Normalize(vmin=0.0, vmax=32.0)
    pcm = ax.pcolormesh(
        LON2, LAT2, sub, cmap=CMAP_ACTUAL, norm=norm,
        shading="auto", zorder=1, rasterized=True,
    )
    # Thin black contour lines at each integer degree for readability
    try:
        ax.contour(
            LON2, LAT2, sub, levels=np.arange(0, 33, 1),
            colors="#000000", linewidths=0.25, alpha=0.55, zorder=1.5,
        )
    except Exception:
        pass
    _draw_basemap(ax, extent, countries, coast)
    _style_axes(ax, extent, title, subtitle)
    _add_colorbar(fig, pcm, "Sea-surface temperature (°C)",
                  ticks=np.arange(0, 33, 4))
    _draw_watermark(ax)
    fig.subplots_adjust(left=0.05, right=0.89, top=0.86, bottom=0.08)
    fig.savefig(out_path, dpi=150, facecolor=BG_COLOR)
    plt.close(fig)


def plot_anomaly(
    anom, lat, lon, extent, figsize, title, subtitle,
    countries, coast, out_path: Path,
    records_high=None, records_low=None, vlim=5.0,
):
    """Diverging anomaly plot. If `records_high` / `records_low` are given,
    stipple the areas where today's value met or exceeded those records."""
    sub, la, lo = _subset_to_extent(anom, lat, lon, extent)
    if sub.size == 0:
        return
    fig, ax = plt.subplots(figsize=figsize, facecolor=BG_COLOR)
    LON2, LAT2 = np.meshgrid(lo, la)
    norm = mcolors.Normalize(vmin=-vlim, vmax=vlim)
    # pcolormesh (one rectangle per cell) avoids the horizontal banding
    # that contourf's triangulation produced on dateline-crossing
    # regions like North Pacific and ENSO.
    pcm = ax.pcolormesh(
        LON2, LAT2, sub, cmap=CMAP_ANOM, norm=norm,
        shading="auto", zorder=1, rasterized=True,
    )
    # Subtle zero-line contour for visual reference
    try:
        ax.contour(LON2, LAT2, sub, levels=[0.0], colors="#ffffff",
                   linewidths=0.4, alpha=0.4, zorder=1.6)
    except Exception:
        pass

    # Records overlay — diagonal hatching with a thin black outline,
    # matching the NOAA Coral Reef Watch / DCAreaWx visual style.
    # Forward slash "///" marks record HIGHS, back-slash "\\\\" marks
    # record LOWS so the two are distinguishable even at a glance.
    # Hatch stroke is driven by rcParams set per-call so we don't leak
    # state to other figures.
    prev_hatch_lw = mpl.rcParams.get("hatch.linewidth", 1.0)
    prev_hatch_color = mpl.rcParams.get("hatch.color", "black")
    mpl.rcParams["hatch.linewidth"] = 0.55
    try:
        for rm, pattern, hatch_color in (
            (records_high, "///",   "#2a0412"),  # near-black red
            (records_low,  "\\\\",  "#05122e"),  # near-black blue
        ):
            if rm is None:
                continue
            rm_sub, _, _ = _subset_to_extent(rm, lat, lon, extent)
            if rm_sub.shape != sub.shape:
                continue
            mask_float = np.where(rm_sub, 1.0, 0.0)
            if not (mask_float > 0.5).any():
                continue
            mpl.rcParams["hatch.color"] = hatch_color
            ax.contourf(
                LON2, LAT2, mask_float,
                levels=[0.5, 1.5],
                colors="none",
                hatches=[pattern],
                zorder=1.8,
            )
            # Thin outline around each record region for clarity
            ax.contour(
                LON2, LAT2, mask_float,
                levels=[0.5],
                colors="#000000", linewidths=0.6, alpha=0.75,
                zorder=1.9,
            )
    finally:
        mpl.rcParams["hatch.linewidth"] = prev_hatch_lw
        mpl.rcParams["hatch.color"] = prev_hatch_color

    _draw_basemap(ax, extent, countries, coast)
    _style_axes(ax, extent, title, subtitle)
    _add_colorbar(fig, pcm, "SST anomaly (°C)  vs 1991–2020 mean",
                  ticks=np.arange(-5, 6, 1))
    _draw_watermark(ax)
    fig.subplots_adjust(left=0.05, right=0.89, top=0.86, bottom=0.08)
    fig.savefig(out_path, dpi=150, facecolor=BG_COLOR)
    plt.close(fig)


# --- Main ---------------------------------------------------------------


def parse_args(argv):
    p = argparse.ArgumentParser()
    p.add_argument(
        "--regions",
        nargs="+",
        default=list(REGIONS.keys()),
        choices=list(REGIONS.keys()),
        help="Which regions to plot (default: all).",
    )
    p.add_argument(
        "--no-records",
        action="store_true",
        help="Skip the records overlay image (faster, fewer downloads).",
    )
    p.add_argument(
        "--date",
        help="Override target UTC date (YYYY-MM-DD). Default: latest available.",
    )
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    log = "[sst]"

    # 1. Latest available day
    if args.date:
        target = dt.date.fromisoformat(args.date)
        today_path = fetch_day(target, log)
        if today_path is None:
            print(f"{log} ERROR: no OISST file for {target}", file=sys.stderr)
            return 2
    else:
        target, today_path = latest_available_day(log)

    print(f"{log} target date: {target.isoformat()}")
    sst_today, lat, lon = read_sst_grid(today_path)

    # 2. Historical same-DOY files for climatology + records
    hist_years = range(RECORDS_START, target.year)
    hist = day_of_year_files(target.month, target.day, hist_years, log)

    # Climatology (1991-2020)
    climo_files = {y: p for y, p in hist.items() if CLIMO_START <= y <= CLIMO_END}
    if not climo_files:
        print(f"{log} ERROR: no climatology files available", file=sys.stderr)
        return 3
    climo_stack, climo_years = stack_years(climo_files)
    climo_mean = np.nanmean(climo_stack, axis=0)
    print(
        f"{log} climatology: {len(climo_years)} years "
        f"({min(climo_years)}–{max(climo_years)})"
    )

    # Anomaly
    anomaly = sst_today - climo_mean

    # Records (1982-present)
    records_mask_high = records_mask_low = None
    records_years: list[int] = []
    if not args.no_records and hist:
        all_stack, records_years = stack_years(hist)
        record_max = np.nanmax(all_stack, axis=0)
        record_min = np.nanmin(all_stack, axis=0)
        # Where today meets or exceeds the historical record (tied counts too,
        # since OISST is interpolated analysis, not raw obs).
        eps = 0.001
        records_mask_high = sst_today > (record_max - eps)
        records_mask_low = sst_today < (record_min + eps)
        # Drop NaN pixels from the mask
        records_mask_high = np.where(
            np.isnan(sst_today) | np.isnan(record_max),
            False, records_mask_high,
        )
        records_mask_low = np.where(
            np.isnan(sst_today) | np.isnan(record_min),
            False, records_mask_low,
        )
        print(
            f"{log} records: {len(records_years)} years — "
            f"highs {int(records_mask_high.sum())} px, "
            f"lows {int(records_mask_low.sum())} px"
        )

    # 3. Load basemap GeoJSONs (the tracks pages already cache these)
    countries = (
        _load_geojson("ne_50m_admin_0_countries.geojson")
        or _load_geojson("ne_110m_admin_0_countries.geojson")
    )
    coast = (
        _load_geojson("ne_50m_coastline.geojson")
        or _load_geojson("ne_110m_coastline.geojson")
    )
    if countries is None and coast is None:
        print(f"{log} WARN: no basemap GeoJSON found — plots will have no coastlines")

    # 4. Render each region × variant
    date_label = target.strftime("%B %-d, %Y")

    for region_key in args.regions:
        rcfg = REGIONS[region_key]
        extent = rcfg["extent"]
        figsize = rcfg["figsize"]
        label = rcfg["label"]
        subtitle = f"Valid: {date_label}  ·  OISST v2.1 (NOAA NCEI)"

        p_actual = SST_DIR / f"{region_key}_actual.png"
        p_anom = SST_DIR / f"{region_key}_anomaly.png"
        p_anom_rec = SST_DIR / f"{region_key}_anomaly_records.png"

        print(f"{log} rendering {region_key} · actual")
        plot_actual(
            sst_today, lat, lon, extent, figsize,
            f"{label} · Sea-Surface Temperature",
            subtitle, countries, coast, p_actual,
        )
        print(f"{log} rendering {region_key} · anomaly")
        plot_anomaly(
            anomaly, lat, lon, extent, figsize,
            f"{label} · SST Anomaly",
            subtitle + "  ·  Baseline 1991–2020",
            countries, coast, p_anom,
        )
        if records_mask_high is not None:
            print(f"{log} rendering {region_key} · anomaly+records")
            plot_anomaly(
                anomaly, lat, lon, extent, figsize,
                f"{label} · SST Anomaly with Daily Records",
                subtitle
                + f"  ·  Records vs {RECORDS_START}–{target.year - 1}",
                countries, coast, p_anom_rec,
                records_high=records_mask_high,
                records_low=records_mask_low,
            )

    # 5. Sidecar metadata JSON for the HTML pages to render timestamps, etc.
    meta = {
        "date": target.isoformat(),
        "updated_utc": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "climo_years": sorted(climo_files.keys()),
        "record_years": sorted(records_years),
        "regions": list(REGIONS.keys()),
        "baseline_start": CLIMO_START,
        "baseline_end": CLIMO_END,
        "record_start": RECORDS_START,
    }
    (SST_DIR / "sst_meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    print(f"{log} wrote {SST_DIR / 'sst_meta.json'}")

    # ========================================================================
    # CRW pipeline — runs after OISST so a CRW failure never blocks OISST.
    # Uses the same region list and plotting code; outputs files with the
    # `crw_` filename prefix so the HTML can target them independently.
    # ========================================================================
    crw_log = "[sst-crw]"
    try:
        crw_target, crw_sst_path = latest_available_crw_day("sst", crw_log)
        _, crw_ssta_path = latest_available_crw_day("ssta", crw_log)

        crw_sst, crw_lat, crw_lon = read_crw_grid(crw_sst_path, "analysed_sst")
        crw_anom, _, _ = read_crw_grid(
            crw_ssta_path, "sea_surface_temperature_anomaly"
        )
        print(f"{crw_log} grids: sst {crw_sst.shape} · anom {crw_anom.shape}")

        # Records vs the full 1985-present CRW era. Download in parallel.
        crw_hist_years = range(CRW_RECORDS_START, crw_target.year)
        crw_records_high = crw_records_low = None
        crw_record_years: list[int] = []
        if not args.no_records:
            crw_hist = day_of_year_crw_files(
                crw_target.month, crw_target.day, crw_hist_years,
                "sst", crw_log,
            )
            if crw_hist:
                crw_stack, crw_record_years = stack_crw_years(
                    crw_hist, "analysed_sst"
                )
                rmax = np.nanmax(crw_stack, axis=0)
                rmin = np.nanmin(crw_stack, axis=0)
                eps = 0.001
                crw_records_high = crw_sst > (rmax - eps)
                crw_records_low = crw_sst < (rmin + eps)
                crw_records_high = np.where(
                    np.isnan(crw_sst) | np.isnan(rmax),
                    False, crw_records_high,
                )
                crw_records_low = np.where(
                    np.isnan(crw_sst) | np.isnan(rmin),
                    False, crw_records_low,
                )
                print(
                    f"{crw_log} CRW records: {len(crw_record_years)} years "
                    f"— highs {int(crw_records_high.sum())} px, "
                    f"lows {int(crw_records_low.sum())} px"
                )

        crw_date_label = crw_target.strftime("%B %-d, %Y")
        for region_key in args.regions:
            rcfg = REGIONS[region_key]
            extent = rcfg["extent"]
            figsize = rcfg["figsize"]
            label = rcfg["label"]
            subtitle = (
                f"Valid: {crw_date_label}  ·  NOAA Coral Reef Watch 5 km v3.1"
            )
            p_actual = SST_DIR / f"crw_{region_key}_actual.png"
            p_anom = SST_DIR / f"crw_{region_key}_anomaly.png"
            p_anom_rec = SST_DIR / f"crw_{region_key}_anomaly_records.png"

            print(f"{crw_log} rendering {region_key} · actual")
            plot_actual(
                crw_sst, crw_lat, crw_lon, extent, figsize,
                f"{label} · CRW Sea-Surface Temperature (5 km)",
                subtitle, countries, coast, p_actual,
            )
            print(f"{crw_log} rendering {region_key} · anomaly")
            plot_anomaly(
                crw_anom, crw_lat, crw_lon, extent, figsize,
                f"{label} · CRW SST Anomaly (5 km)",
                subtitle + "  ·  Baseline 1985–2012 MMM",
                countries, coast, p_anom,
            )
            if crw_records_high is not None:
                print(f"{crw_log} rendering {region_key} · anomaly+records")
                plot_anomaly(
                    crw_anom, crw_lat, crw_lon, extent, figsize,
                    f"{label} · CRW SST Anomaly with Daily Records (5 km)",
                    subtitle
                    + f"  ·  Records vs {CRW_RECORDS_START}–{crw_target.year - 1}",
                    countries, coast, p_anom_rec,
                    records_high=crw_records_high,
                    records_low=crw_records_low,
                )

        crw_meta = {
            "date": crw_target.isoformat(),
            "updated_utc": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "record_years": sorted(crw_record_years),
            "regions": list(REGIONS.keys()),
            "baseline_note": "1985–2012 Monthly Max Mean (NOAA CRW official)",
            "record_start": CRW_RECORDS_START,
        }
        (SST_DIR / "crw_meta.json").write_text(
            json.dumps(crw_meta, indent=2), encoding="utf-8"
        )
        print(f"{crw_log} wrote {SST_DIR / 'crw_meta.json'}")
    except Exception as e:  # noqa: BLE001
        # Don't let CRW failure cascade — OISST already succeeded above.
        print(f"{crw_log} CRW pipeline failed: {type(e).__name__}: {e}",
              file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
