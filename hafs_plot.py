#!/usr/bin/env python3
"""HAFS forecast plots - vertical slice (v1: MSLP + 10 m wind).

First feature of the new ``/models/`` page on triple-a-tropics.com. This script
fetches ONE (model, storm, domain, forecast-hour) HAFS field set and renders a
TAT-styled PNG of mean-sea-level pressure (isobars) over 10 m wind speed
(filled, knots, Saffir-Simpson-flavored palette).

Run it standalone to validate a single frame, then ``generate_hafs_plots.py``
(the full-cycle builder) reuses the fetch + render functions here to loop every
active storm × {hafsa,hafsb} × {storm.atm,parent.atm} × forecast hour.

Data source - Herbie + AWS Open Data
------------------------------------
HAFS GRIB2 lives in the public ``noaa-nws-hafs-pds`` S3 bucket (archive back to
2023-06-19) and on NOMADS (recent cycles only). The Herbie HAFS template that
ships in ``herbie-data`` only knows NOMADS *and* builds its product list from a
**live** lookup of currently-active storms - so it can neither reach the
historical archive nor be constructed for a past storm (``storm_name`` resolves
to ``None`` → ``None.title()`` crash). ``install_hafs_templates()`` below
replaces ``herbie.models.hafsa``/``hafsb`` with AWS-first templates that don't
depend on that live lookup, which is what makes the dev cycle reachable.

Herbie does ``.idx`` byte-range subsetting, so even though a storm.atm file is
~240 MB we only pull the three messages we need (PRMSL + UGRD/VGRD 10 m).

Why no cartopy
--------------
Per CLAUDE.md the repo deliberately avoids cartopy; the vendored Natural Earth
GeoJSON (``ne_50m_*``) is the basemap. HAFS grids are regular lat/lon
(PlateCarree), so we plot straight in lon/lat and overlay the GeoJSON
coastlines/borders exactly like ``generate_sst_plots.py``.

Dev cycle (has data): ``2023-09-09 00:00``, ``hafsa``, storm ``13l`` (Lee),
``storm.atm``, fxx 12.

    python hafs_plot.py            # renders the dev cycle to hafs_test.png
    python hafs_plot.py --model hafsb --storm 13l --product parent.atm --fxx 24
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.ticker as mticker
from matplotlib import patheffects as pe

log = logging.getLogger("hafs-plot")

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# TAT render palette (the satellite-page look from the handoff brief - note
# these are the *image* colors, distinct from styles.css :root which themes the
# HTML page).
# ---------------------------------------------------------------------------
DARK_BG = "#0a0d12"
TEXT_COLOR = "#e8eef5"
ACCENT_COLOR = "#79f0d6"
MUTED_COLOR = "#9199a4"
GRID_COLOR = "#3a4252"
COAST_COLOR = "#000000"
BORDER_COLOR = "#000000"
WATERMARK = "@WeathermanAAA_"

KT_PER_MS = 1.94384  # m s-1 → knots

# ---------------------------------------------------------------------------
# Wind-speed colormap - vivid, high-contrast TAT table. A LinearSegmentedColormap
# normalized over 0 to 165 kt: deep indigo (calm), through blues and teal, to
# green and lime at the TS threshold, yellow and orange across Cat1-2, hot red
# and magenta-pink through Cat3-4, a purple break exactly at the Cat5 threshold
# (137 kt), and a pale-violet cap. A high N keeps the fill smooth; winds above
# 165 kt fold into set_over. The SSHWS thresholds (34/64/83/96/113/137 kt) still
# drive the colorbar ticks.
# ---------------------------------------------------------------------------
WIND_VMAX_KT = 165.0
WIND_OVER_COLOR = "#f3e0ff"
# (kt, hex) anchors; positions are normalized by WIND_VMAX_KT in _wind_cmap_norm.
_WIND_ANCHORS_KT = [
    (0,   "#14245f"),   # calm, deep indigo
    (12,  "#1f5fd0"),
    (22,  "#15a8e0"),
    (30,  "#14d6c0"),
    (34,  "#1fd17a"),   # TS threshold
    (42,  "#4ee23f"),
    (52,  "#b6f02a"),
    (64,  "#ffe617"),   # C1
    (74,  "#ffae12"),
    (83,  "#ff7d0a"),   # C2
    (96,  "#ff2f1c"),   # C3
    (113, "#ff1f8c"),   # C4, hot magenta-pink
    (125, "#e62ac0"),   # upper C4, magenta
    (137, "#9b30ee"),   # C5, purple break starts exactly here
    (150, "#bf72f2"),
    (165, "#f3e0ff"),   # extreme cap
]


def _wind_cmap_norm():
    """Vivid continuous 0-165 kt wind colormap and its Normalize."""
    anchors = [(kt / WIND_VMAX_KT, hexc) for kt, hexc in _WIND_ANCHORS_KT]
    cmap = mcolors.LinearSegmentedColormap.from_list("tat_wind", anchors, N=512)
    cmap.set_over(WIND_OVER_COLOR)
    cmap.set_under(_WIND_ANCHORS_KT[0][1])
    cmap.set_bad(alpha=0.0)  # NaN padding -> transparent (shows panel bg)
    norm = mcolors.Normalize(vmin=0.0, vmax=WIND_VMAX_KT)
    return cmap, norm


# ---------------------------------------------------------------------------
# Herbie HAFS template override (AWS-first, no live storm-name dependency)
# ---------------------------------------------------------------------------
def install_hafs_templates() -> None:
    """Monkeypatch ``herbie.models.hafsa``/``hafsb`` with AWS-first templates.

    Idempotent. See module docstring for why the stock template is unusable.
    The S3 key layout (verified against the live bucket) is::

        hfs{a,b}/{YYYYMMDD}/{HH}/{storm}.{YYYYMMDDHH}.hfs{a,b}.{product}.f{FFF}.grb2

    NOMADS keeps the same name with a ``hfs{a,b}.{YYYYMMDD}/{HH}/`` directory and
    serves as a recent-cycle fallback.
    """
    import herbie.models as models

    def _make_template(flavor: str):
        def template(self):  # called as models.hafsX.template(herbie_instance)
            storm = str(self.storm).lower()
            self.DESCRIPTION = f"Hurricane Analysis and Forecast System (HAFS-{flavor.upper()})"
            self.DETAILS = {
                "AWS Open Data": "https://registry.opendata.aws/noaa-nws-hafs-pds/",
                "HFIP": "https://hfip.org/hafs",
            }
            # Map each product key to itself - the stock template derived these
            # from a live storm-name lookup we deliberately drop. The fetch path
            # uses self.product directly, so the values are cosmetic.
            self.PRODUCTS = {
                "storm.atm": "storm-following atmospheric nest (~2 km)",
                "parent.atm": "parent atmospheric domain (~6 km)",
                "storm.sat": "storm-following synthetic satellite",
                "parent.sat": "parent synthetic satellite",
            }
            fname = (f"{storm}.{self.date:%Y%m%d%H}.hfs{flavor}"
                     f".{self.product}.f{self.fxx:03d}.grb2")
            self.SOURCES = {
                "aws": (f"https://noaa-nws-hafs-pds.s3.amazonaws.com/"
                        f"hfs{flavor}/{self.date:%Y%m%d}/{self.date:%H}/{fname}"),
                "nomads": (f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/hafs/prod/"
                           f"hfs{flavor}.{self.date:%Y%m%d}/{self.date:%H}/{fname}"),
            }
            self.IDX_SUFFIX = [".grb2.idx"]
            self.EXPECT_IDX_FILE = "remote"
            self.LOCALFILE = f"{self.get_remoteFileName}"

        return template

    for name, flavor in (("hafsa", "a"), ("hafsb", "b")):
        cls = type(name, (), {"template": _make_template(flavor)})
        setattr(models, name, cls)


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------
@dataclass
class HafsFrame:
    model: str            # "hafsa" / "hafsb"
    storm: str            # "13l"
    product: str          # "storm.atm" / "parent.atm"
    fxx: int
    init_time: dt.datetime
    valid_time: dt.datetime
    lon: np.ndarray       # 1-D, -180..180, ascending, trimmed to finite extent
    lat: np.ndarray       # 1-D, ascending, trimmed
    mslp_hpa: np.ndarray  # (lat, lon)
    wind_kt: np.ndarray   # (lat, lon) wind speed magnitude, knots
    u_kt: np.ndarray      # (lat, lon) 10 m eastward wind, knots (for barbs)
    v_kt: np.ndarray      # (lat, lon) 10 m northward wind, knots (for barbs)
    extent: tuple         # (lon_min, lon_max, lat_min, lat_max) of finite data


def _to_180(lon: np.ndarray) -> np.ndarray:
    return ((lon + 180.0) % 360.0) - 180.0


def _monotonic(a: np.ndarray) -> bool:
    d = np.diff(a)
    return bool(np.all(d > 0) or np.all(d < 0))


def _choose_lon_frame(raw: np.ndarray) -> np.ndarray:
    """Pick a MONOTONIC longitude axis for the nest.

    A regular contour/extent needs a monotonic X axis. Normal nests live in
    signed -180..180 (e.g. the Atlantic at -82..-52). A nest straddling the
    antimeridian (West Pacific) becomes non-monotonic under ``_to_180`` (it
    jumps +180 → -180), which would otherwise blow the extent out to ~360°. For
    those we keep a CONTINUOUS frame that runs past +180 (e.g. 168..188); the
    >180 values are labeled as °W by ``_lon_label`` and the basemap is wrapped
    into the same frame by ``_draw_feature_lines``.
    """
    lon180 = _to_180(np.asarray(raw, dtype=float))
    if _monotonic(lon180):
        return lon180
    cont = lon180.copy()
    cont[cont < 0] += 360.0       # stitch across the dateline → continuous
    if _monotonic(cont):
        return cont
    raw_f = np.asarray(raw, dtype=float)
    if _monotonic(raw_f):
        return raw_f
    return lon180                 # last resort - pathological grid


def _wrap_into(x: float, lon_min: float, lon_max: float) -> float:
    """Shift a basemap longitude by ±360 so it lands in the nest's frame.

    No-op when the extent is the usual -180..180 (so non-dateline plots are
    unchanged); for a continuous dateline frame (e.g. 168..188) it maps a
    coastline point at -175 (=185°E) to 185 so it draws in the right place.
    """
    while x < lon_min - 180.0:
        x += 360.0
    while x > lon_max + 180.0:
        x -= 360.0
    return x


def fetch_hafs_frame(
    model: str,
    storm: str,
    product: str,
    date: dt.datetime,
    fxx: int,
    save_dir: str,
    remove_grib: bool = False,
) -> HafsFrame:
    """Fetch PRMSL + 10 m wind for one HAFS frame and return a trimmed HafsFrame.

    Two separate byte-range subset reads (PRMSL is meanSea typeOfLevel, winds
    are heightAboveGround) so cfgrib doesn't have to reconcile two hypercubes.

    ``remove_grib=True`` deletes each idx-subset GRIB after it is read into
    xarray. The standalone slice keeps them (default ``False``) for inspection;
    the full-cycle builder sets it so hundreds of frames don't fill the runner
    disk. The two reads use different search strings → different subset files,
    so removing one never starves the other.
    """
    import herbie

    install_hafs_templates()
    H = herbie.Herbie(
        date, model=model, storm=storm, product=product, fxx=fxx,
        priority=["aws", "nomads"], save_dir=save_dir, verbose=False,
    )
    if H.grib is None:
        raise FileNotFoundError(
            f"no HAFS GRIB found for {model} {storm} {product} "
            f"{date:%Y-%m-%d %HZ} f{fxx:03d}"
        )

    ds_p = H.xarray(":PRMSL:mean sea level:", remove_grib=remove_grib)
    ds_w = H.xarray(":(UGRD|VGRD):10 m above ground:", remove_grib=remove_grib)
    if isinstance(ds_p, list):
        ds_p = ds_p[0]
    if isinstance(ds_w, list):
        ds_w = ds_w[0]

    lat = ds_p["latitude"].values
    # Monotonic longitude frame (continuous past +180 for dateline-crossing
    # West Pacific nests; plain signed -180..180 otherwise).
    lon = _choose_lon_frame(ds_p["longitude"].values)

    mslp = ds_p["prmsl"].values / 100.0  # Pa -> hPa
    # Keep the vector components (for barbs) AND the magnitude (for the fill);
    # all three are reordered/trimmed in lockstep below so they stay aligned.
    u_kt = ds_w["u10"].values * KT_PER_MS
    v_kt = ds_w["v10"].values * KT_PER_MS
    wind = np.hypot(u_kt, v_kt)

    # Ensure ascending lat/lon so contour/imshow orient correctly. Reordering
    # the axes only reorders grid columns/rows; the physical u (eastward) and
    # v (northward) values at each point are unchanged, so we slice them the
    # same way as wind/mslp without any sign flip.
    if lat[0] > lat[-1]:
        lat = lat[::-1]
        mslp = mslp[::-1, :]
        wind = wind[::-1, :]
        u_kt = u_kt[::-1, :]
        v_kt = v_kt[::-1, :]
    if lon[0] > lon[-1]:
        lon = lon[::-1]
        mslp = mslp[:, ::-1]
        wind = wind[:, ::-1]
        u_kt = u_kt[:, ::-1]
        v_kt = v_kt[:, ::-1]

    # Trim NaN padding: the nest is a sub-rectangle embedded in a NaN-filled
    # regular grid. Keep rows/cols that carry any finite data.
    finite = np.isfinite(mslp) | np.isfinite(wind)
    rows = np.where(finite.any(axis=1))[0]
    cols = np.where(finite.any(axis=0))[0]
    if rows.size == 0 or cols.size == 0:
        raise ValueError("all-NaN field after fetch, unexpected")
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    lat, lon = lat[r0:r1], lon[c0:c1]
    mslp, wind = mslp[r0:r1, c0:c1], wind[r0:r1, c0:c1]
    u_kt, v_kt = u_kt[r0:r1, c0:c1], v_kt[r0:r1, c0:c1]

    init_time = (ds_p["time"].values.astype("datetime64[s]").astype(dt.datetime))
    valid_time = (ds_p["valid_time"].values.astype("datetime64[s]").astype(dt.datetime))

    return HafsFrame(
        model=model, storm=storm, product=product, fxx=fxx,
        init_time=init_time, valid_time=valid_time,
        lon=lon, lat=lat, mslp_hpa=mslp, wind_kt=wind, u_kt=u_kt, v_kt=v_kt,
        extent=(float(lon.min()), float(lon.max()),
                float(lat.min()), float(lat.max())),
    )


# ---------------------------------------------------------------------------
# Basemap (Natural Earth GeoJSON) - adapted from generate_sst_plots.py
# ---------------------------------------------------------------------------
def _load_geojson(name: str) -> Optional[dict]:
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


def _draw_feature_lines(ax, features, extent, color, linewidth, zorder):
    """Plot GeoJSON line/polygon edges that intersect the extent.

    Coordinates are -180..180 (matching our converted lon). HAFS storm/parent
    domains are small and don't cross the antimeridian, so no wrap handling is
    needed; a feature is drawn if its bounding box overlaps the (margined)
    extent.
    """
    lon_min, lon_max, lat_min, lat_max = extent
    mlon = (lon_max - lon_min) * 0.05 + 1.0
    mlat = (lat_max - lat_min) * 0.05 + 1.0
    for feat in features:
        for ring in _feature_linestrings(feat):
            if len(ring) < 2:
                continue
            # Wrap each point into the nest's longitude frame so dateline-
            # crossing (West Pacific) nests still get their coastlines; a no-op
            # for ordinary -180..180 extents.
            xs = [_wrap_into(p[0], lon_min, lon_max) for p in ring]
            ys = [p[1] for p in ring]
            if (max(xs) < lon_min - mlon or min(xs) > lon_max + mlon
                    or max(ys) < lat_min - mlat or min(ys) > lat_max + mlat):
                continue
            ax.plot(xs, ys, color=color, linewidth=linewidth, zorder=zorder,
                    solid_capstyle="round", solid_joinstyle="round")


def _lon_label(x: float, _pos) -> str:
    v = x
    while v > 180:
        v -= 360
    while v < -180:
        v += 360
    iv = int(round(v))
    if iv in (0, 180, -180):
        return f"{abs(iv)}°"
    return f"{iv}°E" if iv > 0 else f"{-iv}°W"


def _lat_label(y: float, _pos) -> str:
    iv = int(round(y))
    if iv == 0:
        return "0°"
    return f"{abs(iv)}°{'N' if iv > 0 else 'S'}"


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
PRODUCT_LABEL = {
    "storm.atm": "Storm nest (~2 km)",
    "parent.atm": "Parent domain (~6 km)",
}
MODEL_LABEL = {"hafsa": "HAFS-A", "hafsb": "HAFS-B"}

# Colorbar ticks on the SSHWS category thresholds (kt) so the bar doubles as a
# Saffir-Simpson reference: 34 TS, 64 Cat1, 83 Cat2, 96 Cat3, 113 Cat4, 137 Cat5.
CBAR_TICKS_KT = [34, 64, 83, 96, 113, 137]

# Target number of wind barbs across each axis. The per-axis stride is derived
# from the grid size, so the (fine, small) nest and the (coarse, large) parent
# both land near this count and stay readable.
BARB_TARGET = 17

# Degrees to crop off EACH side of the data extent before plotting, so the storm
# fills more of the frame (larger data, lower on-screen isobar density). Clamped
# per-side to a fraction of the span so small domains are never over-cropped.
BBOX_TRIM_DEG = 1.5

# Header title-bar background, a touch lighter than the map bg so the band reads
# like the site nav bar.
BAND_BG = "#11161f"

# SSHWS category chip colors (TAT intensity system), keyed off the frame VMAX:
# TD blue, TS lime, C1 yellow, C2 amber, C3 red, C4 pink, C5 violet. Each entry
# is (kt threshold, label, fill, text color).
_CAT_CHIP = [
    (137, "C5", "#b06bd8", "#ffffff"),
    (113, "C4", "#ee5da6", "#0a1324"),
    (96,  "C3", "#ef4a3c", "#ffffff"),
    (83,  "C2", "#f7a83a", "#0a1324"),
    (64,  "C1", "#f2e641", "#0a1324"),
    (34,  "TS", "#8ce05a", "#0a1324"),
    (0,   "TD", "#4d8bb0", "#ffffff"),
]


def _sshws_chip(vmax_kt: float) -> tuple[str, str, str]:
    """Return (category label, chip fill, chip text color) for a 10 m VMAX (kt)."""
    if not np.isfinite(vmax_kt):
        return "NA", "#5a6b87", "#ffffff"
    for thresh, label, fill, txt in _CAT_CHIP:
        if vmax_kt >= thresh:
            return label, fill, txt
    return _CAT_CHIP[-1][1:]


def render_frame(frame: HafsFrame, out_path: str,
                 countries: Optional[dict], coast: Optional[dict]) -> None:
    lon_min, lon_max, lat_min, lat_max = frame.extent
    # Crop the view in by BBOX_TRIM_DEG per side (clamped to at most 15% of the
    # span so small domains keep their storm) to enlarge the data on the plot.
    # The fill, barbs, contours, and coastlines are still drawn on the full grid
    # and simply clipped to these limits, so nothing at the new edge is missing.
    tlon = min(BBOX_TRIM_DEG, 0.15 * (lon_max - lon_min))
    tlat = min(BBOX_TRIM_DEG, 0.15 * (lat_max - lat_min))
    lon_min, lon_max = lon_min + tlon, lon_max - tlon
    lat_min, lat_max = lat_min + tlat, lat_max - tlat
    mean_lat = 0.5 * (lat_min + lat_max)
    # PlateCarree aspect: 1 deg lon is cos(lat)x shorter than 1 deg lat.
    geo_aspect = 1.0 / max(np.cos(np.deg2rad(mean_lat)), 0.1)
    lon_span = lon_max - lon_min
    lat_span = (lat_max - lat_min) * geo_aspect

    # Inch-based layout (no tight bbox) so the full-width header band, the map,
    # the right colorbar, and the credit footer all land at exact positions.
    base = 10.5  # ~longest map axis, inches
    if lon_span >= lat_span:
        map_w, map_h = base, base * lat_span / lon_span
    else:
        map_w, map_h = base * lon_span / lat_span, base
    map_h = max(map_h, 4.2)
    left_in, cbar_in = 0.62, 1.55     # lat-label gutter / right colorbar gutter
    band_in, foot_in, botpad_in = 0.74, 0.40, 0.06
    fig_w = left_in + map_w + cbar_in
    fig_h = botpad_in + foot_in + map_h + band_in
    map_bottom = botpad_in + foot_in

    fig = plt.figure(figsize=(fig_w, fig_h), facecolor=DARK_BG)
    ax = fig.add_axes([left_in / fig_w, map_bottom / fig_h,
                       map_w / fig_w, map_h / fig_h])
    ax.set_facecolor(DARK_BG)

    cmap, norm = _wind_cmap_norm()
    Lon, Lat = np.meshgrid(frame.lon, frame.lat)

    # (1) 10 m wind-speed fill, the vivid 0-165 kt TAT colormap. pcolormesh
    # renders the continuous colormap as a smooth gradient (no banding). PNG
    # output rasterizes the whole figure at the save DPI, so the fill is raster
    # either way; the barbs / isobars / labels stay crisp via the high DPI plus
    # antialiased vector line drawing on top.
    wind = np.ma.masked_invalid(frame.wind_kt)
    cf = ax.pcolormesh(Lon, Lat, wind, cmap=cmap, norm=norm,
                       shading="nearest", zorder=2)

    # (2) 10 m wind barbs, subsampled to ~BARB_TARGET across each axis. White,
    # antialiased, kept vector (not rasterized) with a subtle dark halo so they
    # stay sharp and legible over both the cool (dark) and warm (bright) ends of
    # the fill palette; emptybarb=0 drops the calm-air circle.
    nlat, nlon = frame.wind_kt.shape
    si = max(1, int(round(nlat / BARB_TARGET)))
    sj = max(1, int(round(nlon / BARB_TARGET)))
    u = np.ma.masked_invalid(frame.u_kt)
    v = np.ma.masked_invalid(frame.v_kt)
    barbs = ax.barbs(
        Lon[::si, ::sj], Lat[::si, ::sj], u[::si, ::sj], v[::si, ::sj],
        length=6.8, linewidth=1.1, color="#ffffff", zorder=4,
        pivot="middle", sizes=dict(emptybarb=0.0), antialiased=True,
    )
    barbs.set_rasterized(False)
    # Subtle dark halo just narrower than the white line so the barbs read as
    # white (legible over the bright fill) with a thin dark edge, not as dark.
    barbs.set_path_effects([pe.withStroke(linewidth=2.0, foreground="#0a0d12")])

    # (3) MSLP isobars every 4 mb, thin white with a dark halo so they read over
    # both cool and warm wind colors. Inline labels every other contour. Vector.
    mslp = np.ma.masked_invalid(frame.mslp_hpa)
    if mslp.count():
        lo = int(np.floor(mslp.min() / 4.0) * 4)
        hi = int(np.ceil(mslp.max() / 4.0) * 4)
        clevs = np.arange(lo, hi + 4, 4)
        # Same 4 mb interval / density as before; lightly softened (alpha + a
        # thinner dark halo) so the vivid fill reads through the dense core.
        cs = ax.contour(Lon, Lat, mslp, levels=clevs, colors="#ffffff",
                        linewidths=0.75, alpha=0.9, zorder=5)
        # mpl >=3.8: ContourSet is itself a Collection (no .collections list).
        cs.set_rasterized(False)
        cs.set_path_effects([pe.withStroke(linewidth=1.4, foreground="#000000")])
        lbls = ax.clabel(cs, levels=clevs[::2], inline=True, fontsize=7,
                         fmt="%d")
        for t in lbls:
            t.set_color("#ffffff")
            t.set_zorder(7)
            t.set_rasterized(False)
            t.set_path_effects([pe.withStroke(linewidth=1.6, foreground="#000000")])

    # (4) Coastlines + borders (bold black) on top of the filled field.
    if coast:
        _draw_feature_lines(ax, coast.get("features", []), frame.extent,
                            COAST_COLOR, 1.2, 6)
    if countries:
        _draw_feature_lines(ax, countries.get("features", []), frame.extent,
                            BORDER_COLOR, 0.8, 6)

    # (5) Bold "L" at the MSLP minimum, with the minimum value just below it.
    if mslp.count():
        kmin = np.unravel_index(np.ma.argmin(mslp), mslp.shape)
        l_lon, l_lat = float(frame.lon[kmin[1]]), float(frame.lat[kmin[0]])
        pmin = float(mslp.min())
        l_off = (lat_max - lat_min) * 0.05
        ax.text(l_lon, l_lat, "L", ha="center", va="center", fontsize=24,
                fontweight="bold", color="#ffffff", zorder=8,
                path_effects=[pe.withStroke(linewidth=2.8, foreground="#000000")])
        ax.text(l_lon, l_lat - l_off, f"{pmin:.0f}", ha="center", va="top",
                fontsize=9, fontweight="bold", color="#ffffff", zorder=8,
                path_effects=[pe.withStroke(linewidth=1.8, foreground="#000000")])

    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_aspect(geo_aspect)
    ax.xaxis.set_major_locator(mticker.MaxNLocator(6, steps=[1, 2, 2.5, 5, 10]))
    ax.yaxis.set_major_locator(mticker.MaxNLocator(6, steps=[1, 2, 2.5, 5, 10]))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(_lon_label))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_lat_label))
    ax.grid(True, linewidth=0.3, color=GRID_COLOR, alpha=0.7, zorder=3)
    ax.tick_params(colors=MUTED_COLOR, labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(MUTED_COLOR)
        spine.set_linewidth(0.6)

    # (6) Right-side labeled colorbar (knots), ticks on the SS thresholds.
    cax = fig.add_axes([(left_in + map_w + 0.30) / fig_w,
                        (map_bottom + 0.05 * map_h) / fig_h,
                        0.16 / fig_w, (0.90 * map_h) / fig_h])
    cb = fig.colorbar(cf, cax=cax, extend="max", ticks=CBAR_TICKS_KT)
    cb.set_label("10 m wind speed (kt)", color=TEXT_COLOR, fontsize=10)
    cb.ax.yaxis.set_tick_params(color=MUTED_COLOR, labelcolor=MUTED_COLOR,
                                labelsize=8)
    cb.outline.set_edgecolor(MUTED_COLOR)
    cb.outline.set_linewidth(0.4)

    # (7) Header BAND: a slim dark title bar across the top (TAT nav-bar look),
    # NOT the reference two-line header. Left: bold model + id, an SSHWS category
    # chip keyed off VMAX, and a muted field/domain subtitle. Right: a teal
    # VMAX/MSLP line and a muted Init -> F-hour -> Valid time-flow line.
    vmax = float(np.nanmax(frame.wind_kt)) if np.isfinite(frame.wind_kt).any() else float("nan")
    pmin_hdr = float(np.nanmin(frame.mslp_hpa)) if np.isfinite(frame.mslp_hpa).any() else float("nan")
    model_label = MODEL_LABEL.get(frame.model, frame.model.upper())
    storm_disp = frame.storm.upper()
    domain_label = PRODUCT_LABEL.get(frame.product, frame.product)
    cat_label, chip_fill, chip_txt = _sshws_chip(vmax)

    band = fig.add_axes([0.0, (map_bottom + map_h) / fig_h, 1.0, band_in / fig_h])
    band.set_facecolor(BAND_BG)
    band.set_xlim(0, 1)
    band.set_ylim(0, 1)
    band.set_xticks([])
    band.set_yticks([])
    for s in band.spines.values():
        s.set_visible(False)

    pad_x = left_in / fig_w           # align band edges with the map
    y_top, y_bot = 0.62, 0.27
    t_title = band.text(pad_x, y_top, f"{model_label}  {storm_disp}",
                        ha="left", va="center", fontsize=15, fontweight="bold",
                        color=TEXT_COLOR, transform=band.transAxes)
    # Place the category chip immediately after the title via its measured width.
    try:
        rend = fig.canvas.get_renderer()
        x_after = band.transAxes.inverted().transform(
            (t_title.get_window_extent(renderer=rend).x1, 0.0))[0]
    except Exception:
        x_after = pad_x + 0.10
    band.text(x_after + 0.012, y_top, cat_label, ha="left", va="center",
              fontsize=11, fontweight="bold", color=chip_txt,
              transform=band.transAxes, zorder=3,
              bbox=dict(boxstyle="round,pad=0.34", facecolor=chip_fill,
                        edgecolor="none"))
    band.text(pad_x, y_bot, f"10m Wind (kt) & MSLP (mb)  /  {domain_label}",
              ha="left", va="center", fontsize=9.5, color=MUTED_COLOR,
              transform=band.transAxes)

    rx = 1.0 - pad_x
    band.text(rx, y_top, f"VMAX {vmax:.1f} kt   /   MSLP {pmin_hdr:.1f} mb",
              ha="right", va="center", fontsize=12, fontweight="bold",
              color=ACCENT_COLOR, transform=band.transAxes)
    band.text(rx, y_bot,
              f"Init {frame.init_time:%Y-%m-%d %HZ}  ->  F{frame.fxx:03d}"
              f"  ->  Valid {frame.valid_time:%Y-%m-%d %HZ}",
              ha="right", va="center", fontsize=9.5, color=MUTED_COLOR,
              transform=band.transAxes)

    # (8) Credit footer under the map, kept out of the header so the top stays
    # clean and does not read like the reference.
    fig.text(left_in / fig_w, (botpad_in + 0.12) / fig_h,
             f"{WATERMARK}  /  triple-a-tropics.com", ha="left", va="center",
             fontsize=9, color=MUTED_COLOR)

    fig.savefig(out_path, dpi=155, facecolor=DARK_BG)
    plt.close(fig)
    log.info("wrote %s", out_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="hafsa", choices=["hafsa", "hafsb"])
    ap.add_argument("--storm", default="13l", help="storm id, e.g. 13l")
    ap.add_argument("--product", default="storm.atm",
                    choices=["storm.atm", "parent.atm"])
    ap.add_argument("--date", default="2023-09-09 00:00",
                    help="cycle init time, 'YYYY-MM-DD HH:MM'")
    ap.add_argument("--fxx", type=int, default=12, help="forecast hour")
    ap.add_argument("--out", default="hafs_test.png")
    ap.add_argument("--save-dir", default=os.environ.get("HERBIE_DATA", "/tmp/herbie_data"))
    args = ap.parse_args()

    date = dt.datetime.strptime(args.date, "%Y-%m-%d %H:%M")

    log.info("fetching %s %s %s %s f%03d …", args.model, args.storm,
             args.product, args.date, args.fxx)
    frame = fetch_hafs_frame(args.model, args.storm, args.product, date,
                             args.fxx, args.save_dir)
    log.info("  grid %d×%d  extent lon[%.2f,%.2f] lat[%.2f,%.2f]  "
             "wind max %.0f kt  mslp min %.1f hPa",
             frame.lat.size, frame.lon.size, *frame.extent,
             np.nanmax(frame.wind_kt), np.nanmin(frame.mslp_hpa))

    countries = (_load_geojson("ne_50m_admin_0_countries.geojson")
                 or _load_geojson("ne_110m_admin_0_countries.geojson"))
    coast = (_load_geojson("ne_50m_coastline.geojson")
             or _load_geojson("ne_110m_coastline.geojson"))
    if not coast:
        log.warning("no coastline GeoJSON found - map will have no coastlines")

    render_frame(frame, args.out, countries, coast)
    return 0


if __name__ == "__main__":
    sys.exit(main())
