#!/usr/bin/env python3
"""
Triple-A-Tropics · Subsurface (TCHP + D26) map generator
========================================================

Renders two PNG products per region from NOAA AOML's global TCHP/D26
dataset, served via OPeNDAP:

  * TCHP  (Tropical Cyclone Heat Potential, kJ/cm²)
  * D26   (Depth of the 26 °C isotherm, m)

Both variables are the oceanic "fuel" metrics most commonly cited in
tropical cyclone intensity forecasting. Higher TCHP → more integrated
warm water available before mixing reaches cooler depths. Deeper D26 →
less vulnerability to wind-induced cold-wake upwelling (SST cooling
that normally kicks in under intensifying storms doesn't kick in as
hard if the warm layer is thicker).

Data source
-----------
NOAA AOML PhOD TCHP_D26 Fields, OPeNDAP:
    https://cwcgom.aoml.noaa.gov/thredds/dodsC/TCHP/TCHP.nc

The dataset is a single consolidated NetCDF (time, lat, lon) with
variables `Tropical_Cyclone_Heat_Potential` and `D26`. Resolution is
0.25° global; coverage begins 2022-01-01 and updates daily with ~1-day
latency.

This is the same data source used by Sohum Patel's GoldStandardBot
(https://cwcgom.aoml.noaa.gov/thredds/dodsC/TCHP/TCHP.nc), which in
turn is the most commonly cited TCHP source in operational TC
discussions.

Regions + outputs
-----------------
18 region definitions (matching SST generator). Outputs:
    subsurface/<region>_tchp.png
    subsurface/<region>_tchp_labels.png
    subsurface/<region>_d26.png
    subsurface/<region>_d26_labels.png
    subsurface/subsurface_meta.json

Notes
-----
The dataset is only ~4 years long (2022–present) so there is no
long-enough climatology to compute stable anomalies at the V1 cut.
We therefore render absolute values only. If the record ever gets
long enough (say, 10+ years) we can add anomaly/percentile panes
using the same pattern as the SST generator.

No cartopy dependency. Basemap uses the Natural Earth GeoJSON files
the tracks and SST workflows already cache.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")
mpl.rcParams["hatch.color"] = "#000000"
mpl.rcParams["hatch.linewidth"] = 0.7
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import xarray as xr

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE
SUB_DIR = OUTPUT_DIR / "subsurface"
SUB_DIR.mkdir(parents=True, exist_ok=True)

# --- Data source --------------------------------------------------------

OPENDAP_URL = "https://cwcgom.aoml.noaa.gov/thredds/dodsC/TCHP/TCHP.nc"
TCHP_VAR = "Tropical_Cyclone_Heat_Potential"
D26_VAR = "D26"

# Small local cache so a same-day re-run doesn't re-hit the server.
CACHE_DIR = HERE / ".subsurface_cache"
CACHE_DIR.mkdir(exist_ok=True)

# Keep the latest few days around; anything older gets pruned to avoid
# an unbounded cache in CI.
CACHE_KEEP = 5


def cache_path(d: dt.date) -> Path:
    return CACHE_DIR / f"tchp_d26.{d:%Y%m%d}.nc"


def prune_cache() -> None:
    """Keep only the most recent CACHE_KEEP files in the cache."""
    files = sorted(
        CACHE_DIR.glob("tchp_d26.*.nc"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in files[CACHE_KEEP:]:
        try:
            p.unlink()
        except OSError:
            pass


def fetch_latest_slice(log_prefix: str = "[subsurface]"
                       ) -> tuple[dt.date, Path]:
    """Open the AOML OPeNDAP dataset, find the most recent time step,
    and persist just that slice (both TCHP + D26) to a local NetCDF.

    Returns (date, cache_path). Opening OPeNDAP and loading one time
    slice of both vars at 0.25° is ~2 seconds on a GH runner — cheap
    enough to do every run even without the local cache, but we keep
    the cache so ad-hoc re-runs in the same UTC day are instant.
    """
    print(f"{log_prefix} opening {OPENDAP_URL}")
    t0 = time.time()
    ds = xr.open_dataset(OPENDAP_URL)
    try:
        # Latest time step
        latest_time = ds.time.values[-1]
        d = dt.datetime.utcfromtimestamp(
            (latest_time - np.datetime64("1970-01-01T00:00:00"))
            / np.timedelta64(1, "s")
        ).date()

        cp = cache_path(d)
        if cp.exists() and cp.stat().st_size > 100_000:
            print(f"{log_prefix}   cache hit for {d}")
            return d, cp

        print(f"{log_prefix}   loading latest slice ({d}) …")
        sub = ds[[TCHP_VAR, D26_VAR]].isel(time=-1).load()
        # Strip out the (already squeezed) time coordinate so downstream
        # readers see simple (lat, lon) arrays.
        if "time" in sub.coords:
            sub = sub.drop_vars("time", errors="ignore")
        sub.to_netcdf(cp)
        print(
            f"{log_prefix}   wrote {cp.name} "
            f"({cp.stat().st_size / 1e6:.1f} MB) in {time.time()-t0:.1f}s"
        )
    finally:
        ds.close()

    prune_cache()
    return d, cp


def read_subsurface_grid(path: Path, var_name: str
                         ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Read a TCHP or D26 grid from a local cached NetCDF.

    Normalizes lat ascending + rolls lon into 0..360 so downstream
    subset/plot code matches the SST generator's conventions exactly
    (they both use _subset_to_extent with 0..360 internal coords).
    """
    with xr.open_dataset(path) as ds:
        raw = ds[var_name].values
        data = np.squeeze(raw).astype(np.float32)
        lat = ds["lat"].values.astype(np.float32)
        lon = ds["lon"].values.astype(np.float32)

    # Ensure ascending lat
    if lat.size >= 2 and lat[0] > lat[-1]:
        lat = lat[::-1]
        data = data[::-1, :]

    # AOML uses -180..+180; roll to 0..360 for shared subsetting.
    if float(np.nanmin(lon)) < 0:
        lon_rolled = np.where(lon < 0, lon + 360.0, lon)
        order = np.argsort(lon_rolled)
        lon = lon_rolled[order]
        data = data[:, order]

    return data, lat, lon


# --- Region definitions (parallel to generate_sst_plots.py) -------------

REGIONS: dict[str, dict] = {
    # Full-earth overview — Pacific-centered to match the OISST + CRW
    # global view on the /sst/ page. _subset_to_extent and _draw_basemap
    # handle the 30→390° wrap transparently.
    "global": {
        "label": "Global",
        "extent": (30.0, 390.0, -75.0, 75.0),
        "figsize": (14.5, 7.2),
    },
    "global-tropics": {
        "label": "Global Tropics",
        "extent": (30.0, 390.0, -45.0, 45.0),
        "figsize": (14.5, 5.6),
    },
    "enso": {
        "label": "ENSO Regions",
        "extent": (120.0, 290.0, -15.0, 15.0),
        "figsize": (15.0, 3.8),
    },

    # Atlantic
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

    # Pacific
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
        "extent": (100.0, 260.0, 0.0, 65.0),
        "figsize": (14.5, 6.5),
    },
    "southwest-pacific": {
        "label": "Southwest Pacific",
        "extent": (140.0, 220.0, -45.0, 0.0),
        "figsize": (11.0, 6.5),
    },
    "southeast-pacific": {
        "label": "Southeast Pacific",
        "extent": (-140.0, -70.0, -50.0, 0.0),
        "figsize": (10.5, 7.5),
    },

    # Other
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
# TCHP: oceanic heat content, 0–200 kJ/cm². Operational thresholds:
#   16  kJ/cm²  — minimum to sustain a TC per Leipper & Volgenau (1972)
#   60  kJ/cm²  — threshold typically cited for tropical development
#   100 kJ/cm²  — "favorable for intensification"
#   125 kJ/cm²  — rapid intensification more likely
#   160 kJ/cm²  — extreme/explosive-deepening potential (cat 4+ fuel)
# The colormap is tuned so these transitions land on visible color
# breaks rather than disappearing inside a single band.

def _tchp_cmap() -> mcolors.LinearSegmentedColormap:
    stops = [
        (0.00,  "#12253f"),   # unified OCEAN_COLOR at zero
        (0.06,  "#1b2b7a"),   # deep blue (~12)
        (0.10,  "#2459c0"),   # blue (~20) — just above 16 threshold
        (0.22,  "#2896dc"),   # sky (~44)
        (0.32,  "#2dd4c5"),   # cyan (~64) — just past 60 threshold
        (0.42,  "#5ceb82"),   # green (~84)
        (0.52,  "#f1f045"),   # yellow (~104) — over 100 threshold
        (0.62,  "#f6a631"),   # amber (~124) — approaching RI
        (0.72,  "#ef5d24"),   # orange (~144)
        (0.82,  "#c81e28"),   # red (~164) — past 160 threshold
        (0.92,  "#7b0d27"),   # dark red (~184)
        (1.00,  "#ef37b8"),   # hot magenta (≥ 200)
    ]
    return mcolors.LinearSegmentedColormap.from_list(
        "tchp", stops, N=256
    )


def _d26_cmap() -> mcolors.LinearSegmentedColormap:
    """D26 depth (0–200 m). Shallow = cool pastels, deep = warm saturated
    colors. Reads intuitively as "more warm-water buffer = more fuel"."""
    stops = [
        (0.00,  "#12253f"),   # unified OCEAN_COLOR at zero
        (0.07,  "#102c5b"),   # deep navy (~14 m)
        (0.15,  "#1a51a3"),   # royal blue (~30 m)
        (0.25,  "#2d8fd6"),   # bright blue (~50 m)
        (0.38,  "#44c9d6"),   # cyan-teal (~76 m)
        (0.50,  "#71e28d"),   # soft green (~100 m)
        (0.60,  "#f0e95a"),   # yellow (~120 m)
        (0.72,  "#f6a82f"),   # orange (~144 m)
        (0.85,  "#da3c2d"),   # red (~170 m)
        (1.00,  "#8a0d35"),   # oxblood (≥ 200 m)
    ]
    return mcolors.LinearSegmentedColormap.from_list(
        "d26", stops, N=256
    )


CMAP_TCHP = _tchp_cmap()
CMAP_D26 = _d26_cmap()
# NaN pixels render as OCEAN_COLOR navy (#12253f), not LAND_COLOR gray.
# This makes ARMOR3D's extratropical NaN ocean match AOML's extratropical
# TCHP=0 ocean (which paints navy via CMAP(0)). Continents are drawn
# separately via _draw_filled_land in _plot_field, so land still shows
# gray on top of this navy fill.
CMAP_TCHP.set_bad(color="#12253f", alpha=1.0)
CMAP_D26.set_bad(color="#12253f", alpha=1.0)


# --- Styling ------------------------------------------------------------

BG_COLOR = "#07101c"
PANEL_COLOR = "#0a1324"
LAND_COLOR = "#5f6b7a"         # unified neutral gray for all landmasses
OCEAN_COLOR = "#12253f"        # unified blue for subsurface ocean panels
COAST_COLOR = "#ffffff"
BORDER_COLOR = "#ffffff"
TEXT_COLOR = "#e5edf6"
MUTED_COLOR = "#8ea2bd"
WATERMARK = "@WeathermanAAA_"


# --- Basemap (Natural Earth GeoJSON) ------------------------------------


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


def _normalize_lons_for_extent(lon: np.ndarray, extent: tuple) -> np.ndarray:
    lon_min, _, _, _ = extent
    if lon_min < 0:
        return np.where(lon > 180, lon - 360, lon)
    return lon


def _subset_to_extent(data, lat, lon, extent):
    """Crop `data` to `extent`. Mirrors generate_sst_plots.py's helper,
    including the Pacific-centered wrap where `lon_max` exceeds 360°:
    we duplicate the [0, lon_max-360) sliver onto the right edge so the
    globe view has no seam."""
    lon_min, lon_max, lat_min, lat_max = extent
    lat_mask = (lat >= lat_min) & (lat <= lat_max)
    lon_adj = _normalize_lons_for_extent(lon, extent)
    order = np.argsort(lon_adj)
    lon_sorted = lon_adj[order]
    data_sorted = data[:, order]

    if lon_max > 360:
        wrap_cut = lon_max - 360
        wrap_mask = lon_sorted < wrap_cut
        if np.any(wrap_mask):
            lon_sorted = np.concatenate(
                [lon_sorted, lon_sorted[wrap_mask] + 360]
            )
            data_sorted = np.concatenate(
                [data_sorted, data_sorted[:, wrap_mask]], axis=1
            )

    lon_mask = (lon_sorted >= lon_min) & (lon_sorted <= lon_max)
    return (
        data_sorted[np.ix_(lat_mask, lon_mask)],
        lat[lat_mask],
        lon_sorted[lon_mask],
    )


def _draw_basemap(ax, extent, countries, coast,
                  draw_borders: bool = True) -> None:
    """Draw coastline + country border LINES only. Same rationale as
    generate_sst_plots.py: filled country polygons break over the
    dateline. Pass ``draw_borders=False`` on small inset maps where
    filled land + coastline is enough info and country borders just
    add visual clutter."""
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

    if countries and draw_borders:
        _draw_feature_lines(countries.get("features", []),
                            BORDER_COLOR, 0.7, 3)
        if wraps_globe:
            _draw_feature_lines(countries.get("features", []),
                                BORDER_COLOR, 0.7, 3, shift=360.0)
    if coast:
        _draw_feature_lines(coast.get("features", []),
                            COAST_COLOR, 0.8, 3)
        if wraps_globe:
            _draw_feature_lines(coast.get("features", []),
                                COAST_COLOR, 0.8, 3, shift=360.0)


def _feature_polygons(feat: dict) -> list[list[list[tuple[float, float]]]]:
    """Return a list of polygons (each a list of rings) from a GeoJSON
    feature. Non-polygon geometries are ignored."""
    geom = feat.get("geometry") or {}
    t = geom.get("type")
    out: list[list[list[tuple[float, float]]]] = []
    if t == "Polygon":
        rings = [[(p[0], p[1]) for p in ring] for ring in geom["coordinates"]]
        out.append(rings)
    elif t == "MultiPolygon":
        for poly in geom["coordinates"]:
            rings = [[(p[0], p[1]) for p in ring] for ring in poly]
            out.append(rings)
    return out


def _draw_filled_land(ax, extent, countries) -> None:
    """Fill country polygons with LAND_COLOR for a gray-land look.
    Natural Earth's ne_50m_admin_0_countries supplies the polygons
    (Polygon / MultiPolygon); the coast file is LineString-only.

    Dateline-safe at any width: we wrap lon into [0, 360] space when the
    extent crosses the antimeridian, and draw a shifted copy for globe-
    tropics panels that duplicate the leftmost lon sliver onto the right
    edge. Natural Earth's country polygons are already split at the
    dateline, so no bow-tie handling is needed.

    Used on BOTH regional insets and global TCHP/D26/TCHP-anom maps,
    because AOML and ARMOR3D subsurface grids carry 0.0 (not NaN) over
    land. That means the colormap's .set_bad() never fires on land there,
    and CMAP(0) == OCEAN_COLOR navy — so without this helper, land and
    deep ocean render as the same color and continents disappear outside
    the tropics. Drawing the polygons explicitly at zorder=2 (above
    pcolormesh, below coastlines) gives the unified gray-land SST look.
    """
    import matplotlib.patches as mpatches
    from matplotlib.path import Path as MplPath

    if not countries:
        return

    lon_min, lon_max, lat_min, lat_max = extent
    wraps_dateline = lon_max > 180

    def _wrap_coord(x):
        if wraps_dateline and x < 0:
            return x + 360
        return x

    def _add_poly(rings_xy, shift=0.0):
        outer = rings_xy[0]
        if not outer:
            return
        xs = [p[0] + shift for p in outer]
        ys = [p[1] for p in outer]
        if max(ys) < lat_min or min(ys) > lat_max:
            return
        if max(xs) < lon_min or min(xs) > lon_max:
            return
        verts: list[tuple[float, float]] = []
        codes: list[int] = []
        for ring in rings_xy:
            if len(ring) < 3:
                continue
            ring_xs = [p[0] + shift for p in ring]
            ring_ys = [p[1] for p in ring]
            verts.append((ring_xs[0], ring_ys[0]))
            codes.append(MplPath.MOVETO)
            for x, y in zip(ring_xs[1:], ring_ys[1:]):
                verts.append((x, y))
                codes.append(MplPath.LINETO)
            verts.append((ring_xs[0], ring_ys[0]))
            codes.append(MplPath.CLOSEPOLY)
        if not verts:
            return
        path = MplPath(verts, codes)
        patch = mpatches.PathPatch(
            path, facecolor=LAND_COLOR, edgecolor="none",
            zorder=2, linewidth=0,
        )
        ax.add_patch(patch)

    for feat in countries.get("features", []):
        for poly in _feature_polygons(feat):
            wrapped = [[(_wrap_coord(x), y) for (x, y) in ring]
                       for ring in poly]
            _add_poly(wrapped)
            if lon_max > 360:
                _add_poly(wrapped, shift=360.0)


def _lon_tick_label(x, _pos):
    v = float(x)
    while v > 180:
        v -= 360
    while v < -180:
        v += 360
    iv = int(round(v))
    if iv == 0 or iv == 180 or iv == -180:
        return f"{abs(iv)}°"
    return f"{iv}°E" if iv > 0 else f"{-iv}°W"


def _lat_tick_label(y, _pos):
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
    # Same adaptive tick spacing as generate_sst_plots.py so the 360°
    # Pacific-centered global view doesn't get 18 cramped ticks.
    lon_range = lon_max - lon_min
    if lon_range >= 270:
        lon_step = 30
    elif lon_range >= 90:
        lon_step = 20
    else:
        lon_step = 10
    ax.xaxis.set_major_locator(mticker.MultipleLocator(lon_step))
    ax.yaxis.set_major_locator(mticker.MultipleLocator(10))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(_lon_tick_label))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_lat_tick_label))
    ax.grid(True, linewidth=0.3, color="#2a3e5c", alpha=0.5, zorder=1)
    ax.tick_params(colors=MUTED_COLOR, labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(MUTED_COLOR)
        spine.set_linewidth(0.5)
    ax.text(0.0, 1.07, title, color=TEXT_COLOR, fontsize=14,
            fontweight="bold", transform=ax.transAxes, va="bottom")
    if subtitle:
        ax.text(0.0, 1.015, subtitle, color=MUTED_COLOR, fontsize=10,
                transform=ax.transAxes, va="bottom")


def _mpl_stroke(color, alpha, width):
    from matplotlib import patheffects as pe
    return pe.withStroke(linewidth=width,
                         foreground=mcolors.to_rgba(color, alpha))


def _draw_watermark(ax):
    ax.text(
        0.995, 0.01, WATERMARK, transform=ax.transAxes,
        ha="right", va="bottom", fontsize=11, fontweight="bold",
        color="#ffffff", alpha=0.35,
        path_effects=[_mpl_stroke("#000000", 0.55, 1.2)],
    )


def _add_colorbar(fig, mappable, label, ticks=None, extend="max"):
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


def _labels_path(p: Path) -> Path:
    return p.with_name(p.stem + "_labels" + p.suffix)


# --- Plot functions -----------------------------------------------------


def _plot_field(
    field, lat, lon, extent, figsize, title, subtitle,
    countries, coast, out_path: Path,
    cmap, vmin: float, vmax: float,
    cbar_label: str, cbar_ticks,
    line_contour_levels,          # thin always-on contour levels
    label_contour_levels,         # labeled-only-in-labels version
    label_fmt: str,
):
    """Render one subsurface field (TCHP or D26) with:
      • filled pcolormesh
      • thin always-on contour lines at `line_contour_levels`
      • optional labels version: thicker contours + numeric clabels

    Saves TWO PNGs per call (clean + labels) — same pattern as SST.
    """
    sub, la, lo = _subset_to_extent(field, lat, lon, extent)
    if sub.size == 0:
        return False
    fig, ax = plt.subplots(figsize=figsize, facecolor=BG_COLOR)
    LON2, LAT2 = np.meshgrid(lo, la)
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
    pcm = ax.pcolormesh(
        LON2, LAT2, sub, cmap=cmap, norm=norm,
        shading="auto", zorder=1, rasterized=True,
    )
    try:
        ax.contour(
            LON2, LAT2, sub, levels=line_contour_levels,
            colors="#000000", linewidths=0.25, alpha=0.5, zorder=1.5,
        )
    except Exception:
        pass
    # Overlay LAND_COLOR-filled country polygons so continents match the
    # unified SST/CRW/OISST look. AOML and ARMOR3D TCHP/D26 grids carry
    # 0.0 (not NaN) over land, so CMAP.set_bad() never fires — without
    # this overlay, land and deep/cold ocean render as the same dark navy
    # (#12253f) and continents disappear outside the tropics.
    _draw_filled_land(ax, extent, countries)
    _draw_basemap(ax, extent, countries, coast)
    _style_axes(ax, extent, title, subtitle)
    _add_colorbar(fig, pcm, cbar_label, ticks=cbar_ticks, extend="max")
    _draw_watermark(ax)
    fig.subplots_adjust(left=0.05, right=0.89, top=0.86, bottom=0.08)
    fig.savefig(out_path, dpi=150, facecolor=BG_COLOR)

    # Labels version
    try:
        cs = ax.contour(
            LON2, LAT2, sub, levels=label_contour_levels,
            colors="#000000", linewidths=0.7, alpha=0.8, zorder=1.55,
        )
        labels = ax.clabel(
            cs, inline=True, inline_spacing=3, fontsize=7,
            fmt=label_fmt, colors="#000000",
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
    return True


def plot_tchp(field, lat, lon, extent, figsize, title, subtitle,
              countries, coast, out_path: Path):
    return _plot_field(
        field, lat, lon, extent, figsize, title, subtitle,
        countries, coast, out_path,
        cmap=CMAP_TCHP, vmin=0.0, vmax=200.0,
        cbar_label="Tropical Cyclone Heat Potential (kJ/cm²)",
        cbar_ticks=np.arange(0, 201, 25),
        # Thin contours every 20 kJ/cm² for visual structure
        line_contour_levels=np.arange(20, 201, 20),
        # Labeled contours at the operational thresholds + round nums
        label_contour_levels=[16, 60, 100, 125, 160],
        label_fmt="%d",
    )


def plot_d26(field, lat, lon, extent, figsize, title, subtitle,
             countries, coast, out_path: Path):
    return _plot_field(
        field, lat, lon, extent, figsize, title, subtitle,
        countries, coast, out_path,
        cmap=CMAP_D26, vmin=0.0, vmax=200.0,
        cbar_label="Depth of 26 °C isotherm (m)",
        cbar_ticks=np.arange(0, 201, 25),
        line_contour_levels=np.arange(25, 201, 25),
        label_contour_levels=[25, 50, 75, 100, 125, 150],
        label_fmt="%dm",
    )


# --- Main ---------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render TCHP + D26 maps for all regions "
                    "from AOML's OPeNDAP TCHP/D26 dataset."
    )
    parser.add_argument(
        "--region", default=None,
        help="Optional: render just one region (for quick iteration).",
    )
    parser.add_argument(
        "--no-labels", action="store_true",
        help="Skip the _labels.png variants (faster local runs).",
    )
    args = parser.parse_args(argv)

    log = "[subsurface]"
    print(f"{log} starting subsurface generator")

    # 1) Fetch latest slice from OPeNDAP
    data_date, cache_file = fetch_latest_slice(log)
    print(f"{log} using slice for {data_date}")

    # 2) Load both variables
    tchp_field, lat_t, lon_t = read_subsurface_grid(cache_file, TCHP_VAR)
    d26_field,  lat_d, lon_d = read_subsurface_grid(cache_file, D26_VAR)
    print(
        f"{log}   TCHP range: {float(np.nanmin(tchp_field)):.1f} "
        f"→ {float(np.nanmax(tchp_field)):.1f} kJ/cm²"
    )
    print(
        f"{log}   D26  range: {float(np.nanmin(d26_field)):.1f} "
        f"→ {float(np.nanmax(d26_field)):.1f} m"
    )

    # 3) Load basemaps (shared with SST/tracks workflows)
    countries = _load_geojson("ne_50m_admin_0_countries.geojson")
    coast = _load_geojson("ne_50m_coastline.geojson")
    if not countries or not coast:
        print(
            f"{log} WARN: Natural Earth GeoJSONs missing — plots will "
            f"render without basemap. (The workflow downloads these "
            f"from nvkelso/natural-earth-vector.)"
        )

    # 4) Render each region
    regions_to_render = (
        {args.region: REGIONS[args.region]}
        if args.region and args.region in REGIONS
        else REGIONS
    )

    # Subtitle format matches the OISST + CRW plots so all three look
    # the same inside the shared SST page. SST uses:
    #   "Valid: <Month Day, Year>  ·  <product>"
    date_label = data_date.strftime("%B %-d, %Y")

    rendered: list[str] = []
    for rkey, r in regions_to_render.items():
        extent = r["extent"]
        figsize = r["figsize"]
        label = r["label"]

        # TCHP
        out_tchp = SUB_DIR / f"{rkey}_tchp.png"
        ok = plot_tchp(
            tchp_field, lat_t, lon_t, extent, figsize,
            title=f"{label} · Tropical Cyclone Heat Potential",
            subtitle=f"Valid: {date_label}  ·  NOAA AOML",
            countries=countries, coast=coast,
            out_path=out_tchp,
        )
        if ok:
            print(f"{log}   ✓ {out_tchp.name}")
            rendered.append(out_tchp.name)

        # D26
        out_d26 = SUB_DIR / f"{rkey}_d26.png"
        ok = plot_d26(
            d26_field, lat_d, lon_d, extent, figsize,
            title=f"{label} · Depth of 26 °C Isotherm",
            subtitle=f"Valid: {date_label}  ·  NOAA AOML",
            countries=countries, coast=coast,
            out_path=out_d26,
        )
        if ok:
            print(f"{log}   ✓ {out_d26.name}")
            rendered.append(out_d26.name)

    # 5) Write meta.json
    meta = {
        "date": data_date.isoformat(),
        "updated_utc": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "regions": list(REGIONS.keys()),
        "products": ["tchp", "d26"],
        "source": (
            "NOAA AOML/PhOD TCHP_D26 Fields V2.1 · "
            "cwcgom.aoml.noaa.gov/thredds (OPeNDAP)"
        ),
        "tchp_range_kJcm2": [0, 200],
        "d26_range_m": [0, 200],
        "rendered_files": rendered,
    }
    (SUB_DIR / "subsurface_meta.json").write_text(
        json.dumps(meta, indent=2)
    )
    print(f"{log} wrote subsurface/subsurface_meta.json")
    print(f"{log} done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
