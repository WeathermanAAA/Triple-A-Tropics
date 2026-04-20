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

# Baseline & records window (OISST)
CLIMO_START = 1991
CLIMO_END = 2020
RECORDS_START = 1982  # full OISST era

# CRW baseline — match OISST's modern 1991–2020 window instead of NOAA
# CRW's default 1985–2012 climatology. This makes CRW + OISST anomalies
# directly comparable in the viewer; the 1985–2012 default is coral-
# bleaching-centric and runs ~0.1–0.2 °C "colder" than 1991–2020 because
# of the post-2012 warming trend.
CRW_CLIMO_START = 1991
CRW_CLIMO_END = 2020

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


def read_sst_grid(path: Path, var_name: str = "sst"
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (field 2-D, lat 1-D, lon 1-D) for a named variable out of an
    OISST daily NetCDF. Default variable is 'sst'; pass 'anom' to get the
    NOAA-computed anomaly (useful for change-maps: subtracting two `anom`
    snapshots gives true SSTA change, with no seasonal cycle baked in)."""
    with Dataset(path, "r") as ds:
        raw = ds.variables[var_name][:]
        # Some OISST files have (time, zlev, lat, lon); squeeze 1-length axes.
        field = np.ma.squeeze(raw)
        field = np.ma.filled(field.astype(np.float32), np.nan)
        lat = ds.variables["lat"][:].astype(np.float32)
        lon = ds.variables["lon"][:].astype(np.float32)
    return field, lat, lon


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
    """Read a CRW NetCDF file. Normalizes lat to ascending order and lon
    to 0..360 convention so the plot code can share logic with OISST.

    The SST product (coraltemp_v3.1) stores lat ascending; the SSTA and
    other derived products store it descending. Without flipping, those
    derived products render upside-down over the basemap.
    """
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

    # Flip if lat is descending so output is always ascending (-90 → +90)
    if lat.size >= 2 and lat[0] > lat[-1]:
        lat = lat[::-1]
        data = data[::-1, :]

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


def mean_crw_years(files_by_year: dict[int, Path], var_name: str,
                   ) -> tuple[np.ndarray | None, list[int]]:
    """Return the per-pixel nanmean across years WITHOUT stacking all
    grids in memory at once.

    A CRW 5 km global grid is ~7200×3600 float32 (~100 MB). Stacking 30
    years is ~3 GB per DOY; stacking for several DOYs (climatology at
    multiple change-map endpoints and running-mean window centers) would
    blow the 14 GB runner budget. We accumulate a running sum + a
    per-pixel valid-count instead, so peak memory is O(2 grids).
    """
    years = sorted(files_by_year)
    running_sum: np.ndarray | None = None
    running_count: np.ndarray | None = None
    years_used: list[int] = []
    for y in years:
        try:
            g, _, _ = read_crw_grid(files_by_year[y], var_name)
        except Exception:  # noqa: BLE001
            continue
        valid = ~np.isnan(g)
        if running_sum is None:
            running_sum = np.where(valid, g, 0.0).astype(np.float64)
            running_count = valid.astype(np.int32)
        else:
            if g.shape != running_sum.shape:
                continue
            running_sum += np.where(valid, g, 0.0)
            running_count += valid.astype(np.int32)
        years_used.append(y)
    if running_sum is None or running_count is None:
        return None, []
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = running_sum / np.where(running_count > 0, running_count, 1)
    mean = np.where(running_count > 0, mean, np.nan).astype(np.float32)
    return mean, years_used


def compute_crw_climo_for_date(d: dt.date, log_prefix: str
                               ) -> tuple[np.ndarray | None, list[int]]:
    """Fetch CRW SST for every year in [CRW_CLIMO_START, CRW_CLIMO_END]
    at the same month/day as `d`, then return the per-pixel nanmean.

    Used to build a 1991–2020 daily climatology at any day-of-year we
    need (target DOY, change-map endpoint DOYs, running-mean window
    centers). Falls back to whatever subset of years successfully
    downloaded — callers check the returned climo is not None.
    """
    year_range = range(CRW_CLIMO_START, CRW_CLIMO_END + 1)
    files = day_of_year_crw_files(
        d.month, d.day, year_range, "sst", log_prefix,
    )
    if not files:
        return None, []
    climo, years_used = mean_crw_years(files, "analysed_sst")
    if climo is None:
        return None, []
    print(
        f"{log_prefix} CRW climo {CRW_CLIMO_START}–{CRW_CLIMO_END} "
        f"for {d:%m-%d}: {len(years_used)} years"
    )
    return climo, years_used


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
    # Full-earth overview — Pacific-centered. Extent spans 360° but
    # starts at 30°E so the split runs through eastern Africa/Arabian
    # peninsula (minimal landmass bisection) and the full Pacific basin
    # sits at the image center (~210°E = 150°W). _subset_to_extent +
    # _draw_basemap duplicate the 0–30°E sliver onto the right edge for
    # a seamless wrap.
    "global": {
        "label": "Global",
        "extent": (30.0, 390.0, -75.0, 75.0),
        "figsize": (14.5, 7.2),
    },
    # Tropical belt — same Pacific-centered wrap, narrower latitude band.
    "global-tropics": {
        "label": "Global Tropics",
        "extent": (30.0, 390.0, -45.0, 45.0),
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
    0→360 vs -180→180 roll automatically.

    Also supports Pacific-centered globe views whose `lon_max` exceeds
    360° (e.g. the `global` region uses (30, 390)). In that case we
    duplicate the leftmost sliver of the 0→360 grid onto the right edge
    with a +360 shift, yielding a continuous strip from `lon_min` to
    `lon_max` with no seam."""
    lon_min, lon_max, lat_min, lat_max = extent
    lat_mask = (lat >= lat_min) & (lat <= lat_max)

    lon_adj = _normalize_lons_for_extent(lon, extent)
    # Re-sort longitudes so they ascend monotonically inside the extent.
    order = np.argsort(lon_adj)
    lon_sorted = lon_adj[order]
    sst_sorted = sst[:, order]

    # Pacific-centered global wrap: duplicate the [0, lon_max-360)
    # sliver at the right edge so the seam between continents
    # disappears.
    if lon_max > 360:
        wrap_cut = lon_max - 360
        wrap_mask = lon_sorted < wrap_cut
        if np.any(wrap_mask):
            lon_sorted = np.concatenate(
                [lon_sorted, lon_sorted[wrap_mask] + 360]
            )
            sst_sorted = np.concatenate(
                [sst_sorted, sst_sorted[:, wrap_mask]], axis=1
            )

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
    wraps_globe = lon_max > 360

    def _wrap_coord(x):
        if wraps_dateline and x < 0:
            return x + 360
        return x

    def _draw_feature_lines(features, color, linewidth, zorder, shift=0.0):
        for feat in features:
            for ring in _feature_linestrings(feat):
                if not ring:
                    continue
                xs = [_wrap_coord(x) + shift for x, _ in ring]
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
        if wraps_globe:
            # Pacific-centered wrap: redraw the 0→(lon_max-360)°E sliver
            # shifted to the right edge of the plot (360→lon_max).
            _draw_feature_lines(countries.get("features", []),
                                BORDER_COLOR, 0.7, 3, shift=360.0)
    if coast:
        _draw_feature_lines(coast.get("features", []),
                            COAST_COLOR, 0.8, 3)
        if wraps_globe:
            _draw_feature_lines(coast.get("features", []),
                                COAST_COLOR, 0.8, 3, shift=360.0)


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
    # Lat/lon gridlines. Pick a tick step that keeps labels readable
    # across both narrow regional views (~30° wide) and the 360° wide
    # Pacific-centered global view.
    lon_range = lon_max - lon_min
    if lon_range >= 270:
        lon_step = 30  # global / global-tropics
    elif lon_range >= 90:
        lon_step = 20  # most regional views
    else:
        lon_step = 10  # small basins (e.g. Mediterranean)
    ax.xaxis.set_major_locator(mticker.MultipleLocator(lon_step))
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


def compute_global_mean(field: np.ndarray, lat: np.ndarray) -> float:
    """Area-weighted global mean of a (lat, lon) 2-D field.

    Weights each grid cell by cos(latitude) to account for the fact that
    equal-degree cells get smaller as you go poleward. NaN-safe: cells
    that are NaN (land, masked) are excluded from both numerator and
    denominator so the result reflects only valid ocean pixels.
    """
    if field.ndim != 2 or lat.ndim != 1 or lat.size != field.shape[0]:
        return float("nan")
    w = np.cos(np.deg2rad(lat.astype(np.float64)))
    w2d = np.broadcast_to(w[:, None], field.shape)
    valid = np.isfinite(field)
    num = np.nansum(np.where(valid, field, 0.0) * w2d * valid)
    den = np.nansum(w2d * valid)
    if den <= 0:
        return float("nan")
    return float(num / den)


def _labels_path(p: Path) -> Path:
    """Return the sibling PNG path with `_labels` appended before the
    extension, so out_path=foo/bar.png → foo/bar_labels.png."""
    return p.with_name(p.stem + "_labels" + p.suffix)


def plot_actual(
    sst_today, lat, lon, extent, figsize, title, subtitle,
    countries, coast, out_path: Path,
):
    """Rainbow actual-SST plot. Saves TWO versions per call:
      • out_path                — filled colormap + thin 1 °C contours
      • _labels_path(out_path) — same plus inline 5 °C value labels

    The no-labels version is the site default; the labels version is
    shown when the user toggles "Show values" on the SST page."""
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
    # Thin black contour lines at every integer degree (always shown).
    try:
        ax.contour(
            LON2, LAT2, sub, levels=np.arange(0, 33, 1),
            colors="#000000", linewidths=0.25, alpha=0.5, zorder=1.5,
        )
    except Exception:
        pass
    _draw_basemap(ax, extent, countries, coast)
    _style_axes(ax, extent, title, subtitle)
    _add_colorbar(fig, pcm, "Sea-surface temperature (°C)",
                  ticks=np.arange(0, 33, 4))
    _draw_watermark(ax)
    fig.subplots_adjust(left=0.05, right=0.89, top=0.86, bottom=0.08)
    # Save the clean (no-labels) version
    fig.savefig(out_path, dpi=150, facecolor=BG_COLOR)

    # Add labeled 5 °C contours and save the labels version
    try:
        cs5 = ax.contour(
            LON2, LAT2, sub, levels=np.arange(5, 31, 5),
            colors="#000000", linewidths=0.7, alpha=0.8, zorder=1.55,
        )
        labels = ax.clabel(
            cs5, inline=True, inline_spacing=3, fontsize=7,
            fmt="%d°", colors="#000000",
        )
        from matplotlib import patheffects as pe
        for lbl in labels:
            lbl.set_path_effects([
                pe.withStroke(linewidth=1.6, foreground="#ffffff"),
            ])
            lbl.set_fontweight("bold")
        fig.savefig(_labels_path(out_path), dpi=150, facecolor=BG_COLOR)
    except Exception:
        pass
    plt.close(fig)


def plot_anomaly(
    anom, lat, lon, extent, figsize, title, subtitle,
    countries, coast, out_path: Path,
    records_high=None, records_low=None, vlim=5.0,
    cbar_label: str = "SST anomaly (°C)  vs 1991–2020 mean",
    cbar_ticks=None,
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
    if cbar_ticks is None:
        step = max(int(round(vlim / 5)), 1)
        cbar_ticks = np.arange(-int(round(vlim)), int(round(vlim)) + 1, step)
    _add_colorbar(fig, pcm, cbar_label, ticks=cbar_ticks)
    _draw_watermark(ax)
    fig.subplots_adjust(left=0.05, right=0.89, top=0.86, bottom=0.08)
    # Save clean (no-labels) version first
    fig.savefig(out_path, dpi=150, facecolor=BG_COLOR)

    # Add labeled contour lines at 0.5 °C steps for typical anomaly
    # ranges, 1 °C for larger. Zero excluded to keep the center quiet.
    # Format "+%.1f" so the reader gets full precision (e.g. "+1.5").
    try:
        step = 0.5 if vlim <= 5.5 else 1.0
        lvl = np.arange(-vlim, vlim + step / 2, step)
        label_levels = [float(v) for v in lvl if abs(v) > 1e-6]
        if label_levels:
            cs_lab = ax.contour(
                LON2, LAT2, sub, levels=label_levels,
                colors="#000000", linewidths=0.45, alpha=0.65, zorder=1.65,
            )
            labels = ax.clabel(
                cs_lab, inline=True, inline_spacing=3, fontsize=6,
                fmt="%+.1f", colors="#000000",
            )
            from matplotlib import patheffects as pe
            for lbl in labels:
                lbl.set_path_effects([
                    pe.withStroke(linewidth=1.4, foreground="#ffffff"),
                ])
                lbl.set_fontweight("bold")
        fig.savefig(_labels_path(out_path), dpi=150, facecolor=BG_COLOR)
    except Exception:
        pass
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

    # 4. Fetch 7/15/30-day-ago snapshots for N-day change maps. We use
    #    NOAA's built-in `anom` variable from the daily OISST file on
    #    BOTH endpoints, so change = anom_today - anom_prev is a true
    #    SSTA change — no seasonal cycle bleed-through. Using `sst`
    #    here would have spring mid-latitude warming (~3-5 °C/30 days)
    #    show up as artificial "change."
    CHANGE_PERIODS = [7, 15, 30]
    # Read today's NOAA anom directly from the file we already fetched.
    anom_today_noaa, _, _ = read_sst_grid(today_path, var_name="anom")
    change_fields: dict[int, tuple[np.ndarray, dt.date]] = {}
    for days in CHANGE_PERIODS:
        prev_date = target - dt.timedelta(days=days)
        print(f"{log} fetching {days}d-ago snapshot {prev_date} ...")
        prev_path = fetch_day(prev_date, log, verbose=True)
        if prev_path is None:
            print(f"{log}   skip {days}d change — could not fetch {prev_date}")
            continue
        prev_anom, _, _ = read_sst_grid(prev_path, var_name="anom")
        if prev_anom.shape != anom_today_noaa.shape:
            print(f"{log}   skip {days}d change — shape mismatch")
            continue
        change_fields[days] = (anom_today_noaa - prev_anom, prev_date)
        print(f"{log}   {days}d SSTA change ready (Δ since {prev_date})")

    # Global-mean SSTA — area-weighted over valid ocean pixels. Used as
    # the reference value for the "global-mean-removed" variant below.
    global_mean_oisst = compute_global_mean(anomaly, lat)
    print(f"{log} global-mean SSTA: {global_mean_oisst:+.3f} °C")
    # Global-mean-removed anomaly field: SSTA - globally-averaged SSTA.
    # Highlights spatial patterns by factoring out the uniform global
    # warming signal.
    anomaly_gmr = anomaly - global_mean_oisst

    # 15-day running means at multiple offsets (today, 7/15/30 days ago).
    # Each running mean is the 15-day window ENDING at that offset.
    # We fetch today-0 through today-44 consecutive days so every
    # window lines up. Uses NOAA's built-in `anom` variable so the
    # anomaly means are against a consistent baseline.
    RM_MAX_OFFSET = 45  # today-0 … today-44 → covers mean(today-30..today-44)
    rm_fetch_dates = {d: target - dt.timedelta(days=d)
                      for d in range(1, RM_MAX_OFFSET)}
    print(f"{log} fetching {len(rm_fetch_dates)} previous days for "
          "running-mean stack ...")
    with cf.ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        rm_future_by_offset = {
            d: pool.submit(fetch_day, rm_fetch_dates[d], log, False)
            for d in rm_fetch_dates
        }
    # Store: offset -> (sst_grid, anom_grid)
    rm_sst: dict[int, np.ndarray] = {0: sst_today}
    rm_anom: dict[int, np.ndarray] = {}
    # Today's anom from the file we already have
    try:
        today_anom_noaa, _, _ = read_sst_grid(today_path, var_name="anom")
        if today_anom_noaa.shape == sst_today.shape:
            rm_anom[0] = today_anom_noaa
    except Exception:
        pass
    for d, fut in rm_future_by_offset.items():
        p = fut.result()
        if p is None:
            continue
        try:
            g_sst, _, _ = read_sst_grid(p, var_name="sst")
            if g_sst.shape == sst_today.shape:
                rm_sst[d] = g_sst
            g_anom, _, _ = read_sst_grid(p, var_name="anom")
            if g_anom.shape == sst_today.shape:
                rm_anom[d] = g_anom
        except Exception as e:  # noqa: BLE001
            print(f"{log}   running {d}d error: {e}", file=sys.stderr)
    print(f"{log} running-mean stack: {len(rm_sst)} SST days, "
          f"{len(rm_anom)} anom days fetched")

    def window_mean(source: dict[int, np.ndarray],
                    end_offset: int) -> np.ndarray | None:
        """Return mean of days in the 15-day window ending at
        today-end_offset (i.e., offsets end_offset..end_offset+14)."""
        days = [source[end_offset + i] for i in range(15)
                if (end_offset + i) in source]
        if len(days) < 8:
            return None
        return np.nanmean(np.stack(days, axis=0), axis=0)

    # Running means at today, and at 7/15/30 days ago (for change maps).
    rm_sst_today = window_mean(rm_sst, 0)
    rm_anom_today = window_mean(rm_anom, 0)
    rm_anom_7 = window_mean(rm_anom, 7)
    rm_anom_15 = window_mean(rm_anom, 15)
    rm_anom_30 = window_mean(rm_anom, 30)

    # Global-mean-removed running anomaly
    rm_anom_gmr = None
    if rm_anom_today is not None:
        rm_gmr_scalar = compute_global_mean(rm_anom_today, lat)
        if np.isfinite(rm_gmr_scalar):
            rm_anom_gmr = rm_anom_today - rm_gmr_scalar
            print(f"{log} running-mean global-mean SSTA: "
                  f"{rm_gmr_scalar:+.3f} °C")

    # Running-mean change fields (if both endpoints exist)
    rm_change = {}
    if rm_anom_today is not None and rm_anom_7 is not None:
        rm_change[7] = rm_anom_today - rm_anom_7
    if rm_anom_today is not None and rm_anom_15 is not None:
        rm_change[15] = rm_anom_today - rm_anom_15
    if rm_anom_today is not None and rm_anom_30 is not None:
        rm_change[30] = rm_anom_today - rm_anom_30

    # Records mask for the 15-day running mean variant. Compared against
    # the same 1982-present per-day-of-year record envelope, but the
    # "current" side is now the 15-day average instead of today's single
    # day. Stricter / more physically meaningful: a hatched pixel means
    # the 15-day mean exceeded the historical single-day extreme, which
    # implies sustained record conditions.
    rm_records_high = rm_records_low = None
    if (rm_sst_today is not None and records_mask_high is not None
            and 'record_max' in locals()):
        eps = 0.001
        rm_records_high = rm_sst_today > (record_max - eps)
        rm_records_low = rm_sst_today < (record_min + eps)
        rm_records_high = np.where(
            np.isnan(rm_sst_today) | np.isnan(record_max),
            False, rm_records_high)
        rm_records_low = np.where(
            np.isnan(rm_sst_today) | np.isnan(record_min),
            False, rm_records_low)
        print(f"{log} running-mean records: "
              f"highs {int(rm_records_high.sum())} px, "
              f"lows {int(rm_records_low.sum())} px")

    # 5. Render each region × variant
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

        # Global-mean-removed anomaly (same math as `anomaly` but with
        # the daily global-mean SSTA subtracted from every pixel, so the
        # colorbar centers on the relative pattern rather than the trend).
        p_anom_gmr = SST_DIR / f"{region_key}_anomaly_gmr.png"
        print(f"{log} rendering {region_key} · anomaly_gmr")
        plot_anomaly(
            anomaly_gmr, lat, lon, extent, figsize,
            f"{label} · SSTA − Global Mean SSTA",
            subtitle
            + f"  ·  Global mean: {global_mean_oisst:+.2f} °C (subtracted)",
            countries, coast, p_anom_gmr,
            cbar_label="SSTA − global mean (°C)",
        )

        # 15-day running-mean variants — one per tab. Each is rendered
        # only if the required running-mean field could be computed
        # (≥8 valid historical days in the window).
        subtitle_15d = subtitle + "  ·  15-day running mean"
        if rm_sst_today is not None:
            plot_actual(
                rm_sst_today, lat, lon, extent, figsize,
                f"{label} · 15-Day Running Mean SST",
                subtitle_15d, countries, coast,
                SST_DIR / f"{region_key}_actual_15d.png",
            )
        if rm_anom_today is not None:
            plot_anomaly(
                rm_anom_today, lat, lon, extent, figsize,
                f"{label} · 15-Day Running Mean SSTA",
                subtitle_15d + "  ·  Baseline 1991–2020",
                countries, coast,
                SST_DIR / f"{region_key}_anomaly_15d.png",
                cbar_label="15-day mean SSTA (°C)",
            )
        if rm_anom_gmr is not None:
            plot_anomaly(
                rm_anom_gmr, lat, lon, extent, figsize,
                f"{label} · 15-Day Mean SSTA − Global Mean",
                subtitle_15d, countries, coast,
                SST_DIR / f"{region_key}_anomaly_gmr_15d.png",
                cbar_label="15-day mean SSTA − global mean (°C)",
            )
        # Records overlay on the running mean — compares the 15-day
        # running-mean SST to the historical single-day record envelope.
        # Hatching shows where the running mean meets/exceeds the
        # strongest single day ever recorded on this DOY since 1982.
        if rm_anom_today is not None and rm_records_high is not None:
            plot_anomaly(
                rm_anom_today, lat, lon, extent, figsize,
                f"{label} · 15-Day Mean SSTA vs Daily Records",
                subtitle_15d + f"  ·  Records vs {RECORDS_START}–{target.year - 1}",
                countries, coast,
                SST_DIR / f"{region_key}_anomaly_records_15d.png",
                records_high=rm_records_high,
                records_low=rm_records_low,
                cbar_label="15-day mean SSTA (°C)",
            )
        # Running-mean change maps: smoothed N-day anomaly change.
        for days, chg in rm_change.items():
            plot_anomaly(
                chg, lat, lon, extent, figsize,
                f"{label} · OISST {days}-Day SSTA Change (15-day mean)",
                subtitle_15d, countries, coast,
                SST_DIR / f"{region_key}_change{days}d_15d.png",
                vlim=5.0,
                cbar_label=f"{days}-day SSTA change (15-day mean, °C)",
            )

        # N-day SSTA change maps
        for days, (change_field, prev_date) in sorted(change_fields.items()):
            out_change = SST_DIR / f"{region_key}_change{days}d.png"
            print(f"{log} rendering {region_key} · change{days}d")
            plot_anomaly(
                change_field, lat, lon, extent, figsize,
                f"{label} · OISST {days}-Day SSTA Change",
                f"Latest: {date_label}  ·  "
                f"Δ since {prev_date.strftime('%B %-d, %Y')}  ·  "
                "Baseline 1991–2020",
                countries, coast, out_change,
                vlim=5.0,
                cbar_label=f"{days}-day SSTA change (°C)",
            )

    # 6. Sidecar metadata JSON for the HTML pages to render timestamps, etc.
    meta = {
        "date": target.isoformat(),
        "updated_utc": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "climo_years": sorted(climo_files.keys()),
        "record_years": sorted(records_years),
        "regions": list(REGIONS.keys()),
        "baseline_start": CLIMO_START,
        "baseline_end": CLIMO_END,
        "record_start": RECORDS_START,
        "global_mean_ssta": global_mean_oisst,
        "change_periods": sorted(change_fields.keys()),
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
        crw_sst, crw_lat, crw_lon = read_crw_grid(crw_sst_path, "analysed_sst")
        print(f"{crw_log} grid: sst {crw_sst.shape}")

        # Records vs the full 1985-present CRW era. Download in parallel.
        # The records stack also doubles as the target-DOY climatology
        # source: its 1991-2020 subset is exactly what we need for the
        # new baseline, so no separate climo fetch for today.
        crw_hist_years = range(CRW_RECORDS_START, crw_target.year)
        crw_records_high = crw_records_low = None
        crw_record_years: list[int] = []
        crw_climo_target: np.ndarray | None = None
        crw_climo_years_target: list[int] = []
        rmax = rmin = None
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
                # Target-DOY 1991–2020 climo, carved out of the records
                # stack for free (no extra network fetches).
                climo_idx = [i for i, y in enumerate(crw_record_years)
                             if CRW_CLIMO_START <= y <= CRW_CLIMO_END]
                if climo_idx:
                    crw_climo_years_target = [crw_record_years[i]
                                              for i in climo_idx]
                    crw_climo_target = np.nanmean(
                        crw_stack[climo_idx], axis=0
                    ).astype(np.float32)
                    print(
                        f"{crw_log} CRW climo "
                        f"{CRW_CLIMO_START}–{CRW_CLIMO_END} "
                        f"for target DOY: "
                        f"{len(crw_climo_years_target)} years"
                    )
                # Release the 40-year records stack before we start
                # pulling more per-DOY climo stacks for change maps and
                # running-mean window centers — otherwise peak memory
                # on the runner spikes hard.
                del crw_stack

        if crw_climo_target is None:
            # We can still render "actual" + records without a climo,
            # but every anomaly-based product is blocked.
            raise RuntimeError(
                f"CRW climo {CRW_CLIMO_START}–{CRW_CLIMO_END} could not be "
                f"assembled for target DOY (no historical files usable)"
            )

        # Main anomaly: today's SST vs the 1991–2020 daily mean at this DOY.
        crw_anom = (crw_sst - crw_climo_target).astype(np.float32)

        # N-day SSTA change maps on the new 1991–2020 baseline. Each
        # endpoint needs its OWN DOY climatology so the seasonal cycle
        # cancels out correctly: change = (sst_today - climo_today_doy)
        # - (sst_prev - climo_prev_doy). That's 30 extra historical
        # fetches per endpoint, done in parallel via the DOY helper.
        crw_change_fields: dict[int, tuple[np.ndarray, dt.date]] = {}
        # Cache climo-by-date so change + running-mean centers can
        # share fetches when their DOYs coincide (target-7 is both a
        # change endpoint and today's 15-day-window center).
        crw_doy_climo_cache: dict[dt.date, np.ndarray] = {}

        def climo_for(d: dt.date) -> np.ndarray | None:
            if d in crw_doy_climo_cache:
                return crw_doy_climo_cache[d]
            climo, _ = compute_crw_climo_for_date(d, crw_log)
            if climo is not None:
                crw_doy_climo_cache[d] = climo
            return climo

        for days in (7, 15, 30):
            prev_date = crw_target - dt.timedelta(days=days)
            print(f"{crw_log} CRW {days}d change: fetching prev SST "
                  f"{prev_date} + building 1991–2020 climo for its DOY")
            prev_path = fetch_crw_day(prev_date, "sst", crw_log, verbose=True)
            if prev_path is None:
                print(f"{crw_log}   skip CRW {days}d change — {prev_date} missing")
                continue
            try:
                prev_sst, _, _ = read_crw_grid(prev_path, "analysed_sst")
            except Exception as e:  # noqa: BLE001
                print(f"{crw_log}   skip CRW {days}d change — read error: {e}")
                continue
            if prev_sst.shape != crw_sst.shape:
                print(f"{crw_log}   skip CRW {days}d change — shape mismatch")
                continue
            climo_prev = climo_for(prev_date)
            if climo_prev is None:
                print(f"{crw_log}   skip CRW {days}d change — no climo "
                      f"for {prev_date:%m-%d}")
                continue
            prev_anom = (prev_sst - climo_prev).astype(np.float32)
            crw_change_fields[days] = (crw_anom - prev_anom, prev_date)
            print(f"{crw_log}   CRW {days}d change ready (Δ since {prev_date})")

        # Global-mean SSTA + global-mean-removed anomaly for CRW
        global_mean_crw = compute_global_mean(crw_anom, crw_lat)
        print(f"{crw_log} CRW global-mean SSTA: {global_mean_crw:+.3f} °C")
        crw_anom_gmr = crw_anom - global_mean_crw

        # 15-day running means at multiple offsets for CRW. Fetch 44 days
        # of SST (not SSTA — we build anomalies ourselves now against
        # 1991–2020). For the running-mean anomalies at each window, we
        # use the climatology at the window's CENTER DOY as a per-window
        # approximation. Exact per-DOY climo across all 15 days would
        # require ~1300 extra historical fetches every run; central-DOY
        # caps residual seasonal error at <0.3 °C even in steep-gradient
        # regions, which is comfortably below the color-scale resolution.
        CRW_RM_MAX_OFFSET = 45
        crw_rm_dates = {d: crw_target - dt.timedelta(days=d)
                        for d in range(1, CRW_RM_MAX_OFFSET)}
        print(f"{crw_log} fetching {len(crw_rm_dates)} previous days of "
              "CRW SST for running-mean stack ...")
        with cf.ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
            crw_sst_futs = {
                d: pool.submit(fetch_crw_day, crw_rm_dates[d], "sst",
                               crw_log, False)
                for d in crw_rm_dates
            }
        crw_rm_sst: dict[int, np.ndarray] = {0: crw_sst}
        for d, fut in crw_sst_futs.items():
            p = fut.result()
            if p is None:
                continue
            try:
                g, _, _ = read_crw_grid(p, "analysed_sst")
                if g.shape == crw_sst.shape:
                    crw_rm_sst[d] = g
            except Exception:
                pass
        print(f"{crw_log} running-mean stack: {len(crw_rm_sst)} SST days")

        def crw_window_mean(source, end_offset):
            days = [source[end_offset + i] for i in range(15)
                    if (end_offset + i) in source]
            if len(days) < 8:
                return None
            return np.nanmean(np.stack(days, axis=0), axis=0)

        crw_rm_sst_today = crw_window_mean(crw_rm_sst, 0)

        # Window-center DOY climos. 4 windows ending at offsets
        # 0/7/15/30 → centers at target-7/-14/-22/-37.
        window_spec = [
            (0, 7),    # today's 15-day window centered at target-7
            (7, 14),   # 7-ago window centered at target-14
            (15, 22),  # 15-ago window centered at target-22
            (30, 37),  # 30-ago window centered at target-37
        ]
        crw_rm_anom_by_offset: dict[int, np.ndarray] = {}
        for end_offset, center_days in window_spec:
            win_mean = crw_window_mean(crw_rm_sst, end_offset)
            if win_mean is None:
                continue
            center_date = crw_target - dt.timedelta(days=center_days)
            climo_center = climo_for(center_date)
            if climo_center is None:
                print(f"{crw_log}   skip RM anom offset {end_offset} — "
                      f"no climo for center DOY {center_date:%m-%d}")
                continue
            crw_rm_anom_by_offset[end_offset] = (
                win_mean - climo_center
            ).astype(np.float32)

        crw_rm_anom_today = crw_rm_anom_by_offset.get(0)
        crw_rm_anom_7 = crw_rm_anom_by_offset.get(7)
        crw_rm_anom_15 = crw_rm_anom_by_offset.get(15)
        crw_rm_anom_30 = crw_rm_anom_by_offset.get(30)

        crw_rm_anom_gmr = None
        if crw_rm_anom_today is not None:
            gm = compute_global_mean(crw_rm_anom_today, crw_lat)
            if np.isfinite(gm):
                crw_rm_anom_gmr = crw_rm_anom_today - gm
                print(f"{crw_log} running-mean global-mean SSTA: "
                      f"{gm:+.3f} °C")
        crw_rm_change = {}
        if crw_rm_anom_today is not None and crw_rm_anom_7 is not None:
            crw_rm_change[7] = crw_rm_anom_today - crw_rm_anom_7
        if crw_rm_anom_today is not None and crw_rm_anom_15 is not None:
            crw_rm_change[15] = crw_rm_anom_today - crw_rm_anom_15
        if crw_rm_anom_today is not None and crw_rm_anom_30 is not None:
            crw_rm_change[30] = crw_rm_anom_today - crw_rm_anom_30

        # CRW records mask for the running-mean variant: compare the
        # 15-day running-mean SST to the full 1985-present per-DOY
        # single-day record envelope. Hatched where sustained conditions
        # beat the historical single-day extreme.
        crw_rm_records_high = crw_rm_records_low = None
        if (crw_rm_sst_today is not None and crw_records_high is not None
                and rmax is not None and rmin is not None):
            eps = 0.001
            crw_rm_records_high = crw_rm_sst_today > (rmax - eps)
            crw_rm_records_low = crw_rm_sst_today < (rmin + eps)
            crw_rm_records_high = np.where(
                np.isnan(crw_rm_sst_today) | np.isnan(rmax),
                False, crw_rm_records_high)
            crw_rm_records_low = np.where(
                np.isnan(crw_rm_sst_today) | np.isnan(rmin),
                False, crw_rm_records_low)
            print(f"{crw_log} running-mean records: "
                  f"highs {int(crw_rm_records_high.sum())} px, "
                  f"lows {int(crw_rm_records_low.sum())} px")

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
                subtitle + "  ·  Baseline 1991–2020",
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

            # Global-mean-removed anomaly (CRW)
            crw_p_anom_gmr = SST_DIR / f"crw_{region_key}_anomaly_gmr.png"
            print(f"{crw_log} rendering {region_key} · anomaly_gmr")
            plot_anomaly(
                crw_anom_gmr, crw_lat, crw_lon, extent, figsize,
                f"{label} · CRW SSTA − Global Mean SSTA (5 km)",
                subtitle
                + f"  ·  Global mean: {global_mean_crw:+.2f} °C (subtracted)",
                countries, coast, crw_p_anom_gmr,
                cbar_label="SSTA − global mean (°C)",
            )

            # 15-day running-mean variants (CRW) — one per tab
            crw_subtitle_15d = subtitle + "  ·  15-day running mean"
            if crw_rm_sst_today is not None:
                plot_actual(
                    crw_rm_sst_today, crw_lat, crw_lon, extent, figsize,
                    f"{label} · 15-Day Mean CRW SST (5 km)",
                    crw_subtitle_15d, countries, coast,
                    SST_DIR / f"crw_{region_key}_actual_15d.png",
                )
            if crw_rm_anom_today is not None:
                plot_anomaly(
                    crw_rm_anom_today, crw_lat, crw_lon, extent, figsize,
                    f"{label} · 15-Day Mean CRW SSTA (5 km)",
                    crw_subtitle_15d + "  ·  Baseline 1991–2020",
                    countries, coast,
                    SST_DIR / f"crw_{region_key}_anomaly_15d.png",
                    cbar_label="15-day mean SSTA (°C)",
                )
            if crw_rm_anom_gmr is not None:
                plot_anomaly(
                    crw_rm_anom_gmr, crw_lat, crw_lon, extent, figsize,
                    f"{label} · 15-Day Mean CRW SSTA − Gbl Mean (5 km)",
                    crw_subtitle_15d, countries, coast,
                    SST_DIR / f"crw_{region_key}_anomaly_gmr_15d.png",
                    cbar_label="15-day mean SSTA − global mean (°C)",
                )
            if crw_rm_anom_today is not None and crw_rm_records_high is not None:
                plot_anomaly(
                    crw_rm_anom_today, crw_lat, crw_lon, extent, figsize,
                    f"{label} · 15-Day Mean CRW SSTA vs Daily Records (5 km)",
                    crw_subtitle_15d
                    + f"  ·  Records vs {CRW_RECORDS_START}–{crw_target.year - 1}",
                    countries, coast,
                    SST_DIR / f"crw_{region_key}_anomaly_records_15d.png",
                    records_high=crw_rm_records_high,
                    records_low=crw_rm_records_low,
                    cbar_label="15-day mean SSTA (°C)",
                )
            for days, chg in crw_rm_change.items():
                plot_anomaly(
                    chg, crw_lat, crw_lon, extent, figsize,
                    f"{label} · CRW {days}-Day SSTA Change (15-day mean, 5 km)",
                    crw_subtitle_15d, countries, coast,
                    SST_DIR / f"crw_{region_key}_change{days}d_15d.png",
                    vlim=5.0,
                    cbar_label=f"{days}-day SSTA change (15-day mean, °C)",
                )

            # N-day SSTA change maps (CRW)
            for days, (chg, prev_date) in sorted(crw_change_fields.items()):
                out_change = SST_DIR / f"crw_{region_key}_change{days}d.png"
                print(f"{crw_log} rendering {region_key} · change{days}d")
                plot_anomaly(
                    chg, crw_lat, crw_lon, extent, figsize,
                    f"{label} · CRW {days}-Day SSTA Change (5 km)",
                    f"Latest: {crw_date_label}  ·  "
                    f"Δ since {prev_date.strftime('%B %-d, %Y')}  ·  "
                    "Baseline 1991–2020",
                    countries, coast, out_change,
                    vlim=5.0,
                    cbar_label=f"{days}-day SSTA change (°C)",
                )

        crw_meta = {
            "date": crw_target.isoformat(),
            "updated_utc": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "record_years": sorted(crw_record_years),
            "regions": list(REGIONS.keys()),
            "baseline_note": (
                f"{CRW_CLIMO_START}–{CRW_CLIMO_END} daily climatology "
                "(computed from CRW 5 km CoralTemp v3.1, matches OISST baseline)"
            ),
            "baseline_start": CRW_CLIMO_START,
            "baseline_end": CRW_CLIMO_END,
            "climo_years": sorted(crw_climo_years_target),
            "record_start": CRW_RECORDS_START,
            "global_mean_ssta": global_mean_crw,
            "change_periods": sorted(crw_change_fields.keys()),
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
