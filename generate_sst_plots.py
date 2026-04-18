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
# OISST v2.1 has a ~1-day latency.  We try today-1 first and fall back
# through a few earlier days if the latest file isn't up yet.
LATENCY_TRIES = 4

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


def oisst_url(yyyymmdd: dt.date) -> str:
    return (
        f"{OISST_BASE}/{yyyymmdd.year:04d}{yyyymmdd.month:02d}/"
        f"oisst-avhrr-v02r01.{yyyymmdd.year:04d}"
        f"{yyyymmdd.month:02d}{yyyymmdd.day:02d}.nc"
    )


def cache_path(d: dt.date) -> Path:
    return CACHE_DIR / f"oisst.{d:%Y%m%d}.nc"


def fetch_day(d: dt.date, log_prefix: str = "[sst]") -> Path | None:
    """Download the OISST NetCDF for a specific UTC day, cache-hit, with retries."""
    cp = cache_path(d)
    if cp.exists() and cp.stat().st_size > 100_000:
        return cp
    url = oisst_url(d)
    for attempt in range(FETCH_RETRIES):
        try:
            r = requests.get(url, timeout=FETCH_TIMEOUT)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            cp.write_bytes(r.content)
            return cp
        except Exception as e:  # noqa: BLE001
            if attempt == FETCH_RETRIES - 1:
                print(
                    f"{log_prefix}   fetch failed {d}: "
                    f"{type(e).__name__}: {e}",
                    file=sys.stderr,
                )
                return None
    return None


def latest_available_day(log_prefix: str = "[sst]") -> tuple[dt.date, Path]:
    """Start at yesterday UTC and walk back until a day's file exists."""
    today = dt.datetime.utcnow().date()
    for back in range(1, LATENCY_TRIES + 1):
        d = today - dt.timedelta(days=back)
        print(f"{log_prefix} trying {d} ...")
        p = fetch_day(d, log_prefix)
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
    "global": {
        "label": "Global Tropics",
        "extent": (-180.0, 180.0, -55.0, 60.0),
        "figsize": (14.5, 6.2),
    },
    "atlantic": {
        "label": "Atlantic",
        "extent": (-100.0, 0.0, 0.0, 60.0),
        "figsize": (10.5, 7.0),
    },
    "west-pacific": {
        "label": "West Pacific",
        "extent": (100.0, 180.0, 0.0, 60.0),
        "figsize": (10.5, 7.5),
    },
    "east-pacific": {
        "label": "East Pacific",
        "extent": (-180.0, -80.0, 0.0, 50.0),
        "figsize": (12.0, 6.5),
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
    """Diverging cool-to-warm to match the 1st reference image.
    Dark blue/purple at strong negative → light cyan → near-white at 0 →
    yellow → orange → dark red → magenta at strong positive."""
    stops = [
        (0.00, "#1a1066"),  # deep purple (≤ -5)
        (0.08, "#283b9e"),
        (0.20, "#3f6cc2"),
        (0.32, "#8bb6e0"),
        (0.42, "#d4e4f5"),
        (0.50, "#ffffff"),  # zero
        (0.58, "#fbe6b9"),
        (0.68, "#f7b65d"),
        (0.80, "#e84b2a"),
        (0.92, "#9b0b20"),
        (1.00, "#ef37b8"),  # hot-pink magenta (≥ +5)
    ]
    return mcolors.LinearSegmentedColormap.from_list(
        "sst_anom", stops, N=256
    )


CMAP_ACTUAL = _sst_actual_cmap()
CMAP_ANOM = _sst_anom_cmap()


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
    """Plot land polygons + coastlines + country borders from Natural Earth.

    Handles longitude wrapping so polygons at the dateline draw correctly."""
    lon_min, lon_max, lat_min, lat_max = extent
    wraps_dateline = lon_max > 180

    def _wrap_coord(x):
        if wraps_dateline and x < 0:
            return x + 360
        return x

    # Countries: fill as land + white borders on top
    if countries:
        for feat in countries.get("features", []):
            for ring in _feature_linestrings(feat):
                xs = [_wrap_coord(x) for x, _ in ring]
                ys = [y for _, y in ring]
                # Skip polygons that don't touch the viewport (cheap bbox)
                if max(ys) < lat_min or min(ys) > lat_max:
                    continue
                if max(xs) < lon_min or min(xs) > lon_max:
                    continue
                # Polygon fill (land)
                ax.fill(xs, ys, color=LAND_COLOR, zorder=2, linewidth=0)
                # White border
                ax.plot(xs, ys, color=BORDER_COLOR, linewidth=0.8,
                        zorder=3, solid_capstyle="round")

    # Coastline lines — thinner, on top of fill, under anything else
    if coast:
        for feat in coast.get("features", []):
            for line in _feature_linestrings(feat):
                xs = [_wrap_coord(x) for x, _ in line]
                ys = [y for _, y in line]
                if max(ys) < lat_min or min(ys) > lat_max:
                    continue
                if max(xs) < lon_min or min(xs) > lon_max:
                    continue
                ax.plot(xs, ys, color=COAST_COLOR, linewidth=0.8, zorder=3)


def _style_axes(ax, extent, title, subtitle):
    lon_min, lon_max, lat_min, lat_max = extent
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_aspect("auto")
    ax.set_facecolor(PANEL_COLOR)
    # Lat/lon gridlines
    ax.xaxis.set_major_locator(mticker.MultipleLocator(20))
    ax.yaxis.set_major_locator(mticker.MultipleLocator(10))
    ax.grid(True, linewidth=0.3, color="#2a3e5c", alpha=0.5, zorder=1)
    ax.tick_params(colors=MUTED_COLOR, labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(MUTED_COLOR)
        spine.set_linewidth(0.5)
    # Title block
    ax.set_title(title, color=TEXT_COLOR, fontsize=14, fontweight="bold",
                 loc="left", pad=10)
    if subtitle:
        ax.text(0.0, 1.02, subtitle, color=MUTED_COLOR, transform=ax.transAxes,
                fontsize=10, va="bottom")


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
    """Rainbow actual-SST plot with integer-degree contours."""
    sub, la, lo = _subset_to_extent(sst_today, lat, lon, extent)
    if sub.size == 0:
        return
    fig, ax = plt.subplots(figsize=figsize, facecolor=BG_COLOR)
    LON2, LAT2 = np.meshgrid(lo, la)
    # Filled contours — discrete steps every 1 °C look like the reference.
    levels_fill = np.linspace(0, 32, 257)
    pcm = ax.contourf(
        LON2, LAT2, sub, levels=levels_fill,
        cmap=CMAP_ACTUAL, vmin=0, vmax=32, extend="both", zorder=1,
    )
    # Thin black contour lines at each integer degree to match reference
    ax.contour(
        LON2, LAT2, sub, levels=np.arange(0, 33, 1),
        colors="#000000", linewidths=0.25, alpha=0.55, zorder=1.5,
    )
    _draw_basemap(ax, extent, countries, coast)
    _style_axes(ax, extent, title, subtitle)
    _add_colorbar(fig, pcm, "Sea-surface temperature (°C)",
                  ticks=np.arange(0, 33, 4))
    _draw_watermark(ax)
    fig.subplots_adjust(left=0.05, right=0.89, top=0.92, bottom=0.08)
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
    levels = np.linspace(-vlim, vlim, 257)
    pcm = ax.contourf(
        LON2, LAT2, sub, levels=levels,
        cmap=CMAP_ANOM, vmin=-vlim, vmax=vlim, extend="both", zorder=1,
    )
    # Subtle zero-line contour for visual reference
    try:
        ax.contour(LON2, LAT2, sub, levels=[0.0], colors="#ffffff",
                   linewidths=0.4, alpha=0.4, zorder=1.6)
    except Exception:
        pass

    # Optional: records stippling (where today broke the 1982–present envelope)
    if records_high is not None:
        rh_sub, _, _ = _subset_to_extent(records_high, lat, lon, extent)
        if rh_sub.shape == sub.shape:
            mask = np.where(rh_sub, 1.0, np.nan)
            # Stipple using hatched scatter of small dots
            lat_step = max(1, la.size // 110)
            lon_step = max(1, lo.size // 220)
            yy, xx = np.where(~np.isnan(mask))
            # sub-sample to keep the dots sparse
            pick = (yy % lat_step == 0) & (xx % lon_step == 0)
            if pick.any():
                ax.scatter(
                    lo[xx[pick]], la[yy[pick]],
                    s=1.6, c="#2a0033", alpha=0.75, marker=".",
                    linewidths=0, zorder=1.8,
                )
    if records_low is not None:
        rl_sub, _, _ = _subset_to_extent(records_low, lat, lon, extent)
        if rl_sub.shape == sub.shape:
            lat_step = max(1, la.size // 110)
            lon_step = max(1, lo.size // 220)
            yy, xx = np.where(rl_sub)
            pick = (yy % lat_step == 0) & (xx % lon_step == 0)
            if pick.any():
                ax.scatter(
                    lo[xx[pick]], la[yy[pick]],
                    s=1.6, c="#00144d", alpha=0.75, marker=".",
                    linewidths=0, zorder=1.8,
                )

    _draw_basemap(ax, extent, countries, coast)
    _style_axes(ax, extent, title, subtitle)
    _add_colorbar(fig, pcm, "SST anomaly (°C)  vs 1991–2020 mean",
                  ticks=np.arange(-5, 6, 1))
    _draw_watermark(ax)
    fig.subplots_adjust(left=0.05, right=0.89, top=0.92, bottom=0.08)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
