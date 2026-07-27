"""tcprimed.render - storm-centered equirectangular PNGs for a TC overpass.

FOUR products per overpass, each rendered with the EXACT canonical NRL
passive-microwave color tables (vendored + exactness-guarded in pmw_canonical):

  * 37H      37 GHz H-pol brightness temperature (K) on the canonical 37 GHz
             stepped colormap (cream -> magenta/purple -> blue/cyan -> green ->
             orange -> red -> black; warm low-level emission reads red/black).
  * 91H      high-freq (85/89/91 GHz) H-pol brightness temperature (K) on the
             canonical high-freq colormap (white -> black -> red -> gold ->
             green -> blue -> light; cold scattering cores read black/red).
  * color37  NRL 37 GHz true-color RGB from the 37 V/H pair: green = clear ocean,
             cyan = warm rain, magenta = deep convection, red = ice scattering.
  * color91  NRL high-freq true-color RGB from the 89/91 V/H pair: teal/cyan
             ocean, convective ice scattering red -> black.

The swath is resampled onto a regular storm-centered grid by LINEAR (Delaunay)
interpolation in a CENTER-RELATIVE (unwrapped) longitude frame, then drawn with a
bilinear ``imshow`` -- the continuous NRL look, gap-free even for the
coarse imagers (SSMIS), with a clean swath edge (cells outside the data convex
hull stay transparent). Self-contained: no cartopy; the coastline/border drawer
reads the repo's ne_50m_*.geojson and breaks each ring at large longitude jumps.
The two H-pol colormaps + the two color recipes live in tcprimed.pmw_canonical.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import sys
from pathlib import Path
from typing import Optional

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.image as mpimg  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, Normalize  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

import netCDF4 as nc  # noqa: E402
from scipy.interpolate import griddata  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

from . import PMW_CHANNELS, SOURCE_ARCHIVE
from . import pmw_canonical as pmwc

# ---- look + layout (sober scientific aesthetic, matches the HAFS sim-MW frame)
BAND_BG = "#0b0e13"          # figure + header band background (dark navy-black)
PLOT_BG = "#0b0e13"          # map facecolor (transparent MW image over it)
TEXT_COLOR = "#e8edf4"
MUTED_COLOR = "#9fb0c3"
COAST_COLOR = "#c8d4e2"      # light/neutral coastline over the dark MW image
BORDER_COLOR = "#7d8aa3"     # dimmer neutral for political borders
TICK_COLOR = "#6b7a8d"
WATERMARK = "@WeathermanAAA_"

HALF_DEG = 5.0               # half-width of the square map, degrees
GRID_STEP = 0.02             # smooth-resample grid resolution (deg) -> ~500 px
FILL_THRESHOLD_K = 50.0      # Tb below this -> fill / bad pixel

# Swath-edge QC: griddata("linear") triangulates the CONVEX hull of the swath
# samples, so it fills any CONCAVITY (a swath entering/leaving the box at an
# angle, or a cross-track sample-density drop near the scan edge) with long thin
# "sliver" triangles that linearly interpolate across real gaps -> diagonal
# streak/fan artifacts radiating toward the swath edge (most visible toward the
# box bottom). The fix is a distance-to-nearest-sample mask: any regrid cell
# farther than EDGE_MASK_K x the local sample spacing from a real pixel is set
# back to NaN (transparent), recovering the TRUE concave swath boundary the
# convex hull does not. EDGE_MASK_K is sized so the dense interior (cells <~ one
# footprint from a sample) always survives while the slivers (many footprints
# wide) are cut. Env kill switch TCPRIMED_EDGE_MASK=0 restores the raw convex
# fill for an instant rollback.
EDGE_MASK_K = float(os.environ.get("TCPRIMED_EDGE_MASK_K", "1.8") or "1.8")
EDGE_MASK_ENABLED = (os.environ.get("TCPRIMED_EDGE_MASK", "1") or "1").lower() \
    not in ("0", "false", "no")

_HERE = Path(__file__).resolve().parent
# The ne_50m geojsons live at the repo root (one level up from this package).
_REPO_ROOT = _HERE.parent


# ---------------------------------------------------------------------------
# The four observed-MW products use the EXACT canonical NRL Tb color tables,
# vendored in pmw_canonical (single source of truth, guarded by an exactness
# test): 37H + 91H are single-channel H-pol brightness-temperature colormaps,
# color37 + color91 are V/H true-color RGB recipes. Kept separate from the HAFS
# sim-MW ``ice89h`` palette (tuned independently).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# NetCDF read helpers
# ---------------------------------------------------------------------------
def _s(v) -> str:
    """Coerce a NetCDF string/char scalar to a clean python str. String-typed
    vars return a python str already; char-array-backed vars (possible on the
    preliminary tier / a future reprocessing) return bytes/np.bytes_, which
    str() would render as "b'AMSR2'" and break the PMW_CHANNELS lookup."""
    if isinstance(v, (bytes, np.bytes_)):
        return v.decode("utf-8", "ignore").strip()
    return str(v).strip()


def _scan_arrays(ds, group: str, vname: str):
    """Return (lat, lon, tb) float arrays for a TB channel, fill -> NaN."""
    g = ds["passive_microwave"][group]
    lat = np.array(g["latitude"][:], dtype=float)
    lon = np.array(g["longitude"][:], dtype=float)   # 0..360
    raw = g[vname]
    fill = getattr(raw, "_FillValue", None)
    tb = np.ma.filled(np.ma.masked_invalid(np.ma.asarray(raw[:])), np.nan)
    tb = np.array(tb, dtype=float)
    if fill is not None:
        tb[tb == float(fill)] = np.nan
    tb[tb <= FILL_THRESHOLD_K] = np.nan
    return lat, lon, tb


def read_overpass(path: str) -> dict:
    """Read storm metadata + the 89 and 37 channel swaths from a TC-PRIMED file.

    Returns a dict with keys: sensor, platform, atcf, basin, year, valid (UTC
    datetime), intensity_kt, min_p_hpa, dev_level, clat, clon (degE 0..360), and
    the channel arrays. Raises KeyError if the sensor channel map is unknown or a
    needed group/channel is missing."""
    ds = nc.Dataset(path)
    try:
        om = ds["overpass_metadata"]
        sm = ds["overpass_storm_metadata"]
        sensor = _s(om["instrument_name"][0])
        platform = _s(om["platform_name"][0])
        basin = _s(om["basin"][0])
        season = int(om["season"][0])
        cyc = int(om["cyclone_number"][0])
        chan = PMW_CHANNELS.get(sensor)
        if chan is None:
            raise KeyError(f"unsupported sensor {sensor!r}")

        clat = float(sm["storm_latitude"][0])
        clon = float(sm["storm_longitude"][0]) % 360.0
        intensity = int(sm["intensity"][0])
        try:
            min_p = int(sm["central_min_pressure"][0])
        except Exception:  # noqa: BLE001
            min_p = None
        dev = _s(sm["development_level"][0])
        when = dt.datetime.fromtimestamp(int(sm["time"][0]), tz=dt.timezone.utc)

        g89, v89, h89 = chan["89"]
        lat89, lon89, tb89v = _scan_arrays(ds, g89, v89)
        _, _, tb89h = _scan_arrays(ds, g89, h89)

        g37, v37, h37 = chan["37"]
        lat37, lon37, tb37v = _scan_arrays(ds, g37, v37)
        _, _, tb37h = _scan_arrays(ds, g37, h37)

        atcf = f"{basin.upper()}{cyc:02d}{season}"
        return {
            "sensor": sensor, "platform": platform, "atcf": atcf,
            "basin": basin.upper(), "year": season,
            "valid": when, "intensity_kt": intensity,
            "min_p_hpa": (min_p if (min_p and min_p > 0) else None),
            "dev_level": dev, "clat": clat, "clon": clon,
            "lat89": lat89, "lon89": lon89, "tb89v": tb89v, "tb89h": tb89h,
            "lat37": lat37, "lon37": lon37, "tb37v": tb37v, "tb37h": tb37h,
        }
    finally:
        ds.close()


# ---------------------------------------------------------------------------
# Longitude unwrap to a center-relative frame (dateline-safe)
# ---------------------------------------------------------------------------
def _unwrap(lon: np.ndarray, clon: float) -> np.ndarray:
    """Shift lon (degE 0..360) into a center-relative frame so |lon-clon| <= 180.
    All plotting happens in this frame; ticks convert back via _lon_label."""
    u = np.asarray(lon, dtype=float).copy()
    d = u - clon
    u[d > 180.0] -= 360.0
    u[d < -180.0] += 360.0
    return u


def _lon_label(x: float) -> str:
    """Center-frame display lon -> a -180..180 tick label."""
    v = x
    while v > 180:
        v -= 360
    while v < -180:
        v += 360
    iv = int(round(v))
    if iv in (0, 180, -180):
        return f"{abs(iv)}°"
    return f"{iv}°E" if iv > 0 else f"{-iv}°W"


def _lat_label(y: float) -> str:
    iv = int(round(y))
    if iv == 0:
        return "0°"
    return f"{abs(iv)}°{'N' if iv > 0 else 'S'}"


# ---------------------------------------------------------------------------
# Smooth swath -> regular grid resample (linear interpolation, gap-free)
# ---------------------------------------------------------------------------
def _regrid(lat, lon, fields, clat, clon, half=HALF_DEG, step=GRID_STEP,
            method="linear"):
    """Resample swath ``fields`` (a list of 2-D arrays sharing lat/lon) onto a
    regular lat/lon grid centered on (clat, clon), in a center-unwrapped
    longitude frame. Returns (extent, [grid fields]) with
    extent = [clon-half, clon+half, clat-half, clat+half].

    ``method`` selects the resampling:
      * ``"linear"`` (default, the SMOOTHED look) - Delaunay interpolation that
        smooths the coarse imager footprints into the continuous NRL look (vs.
        blocky native quads) AND fills solid with NO inter-scanline gaps, because
        every interior target cell is interpolated from the surrounding pixels.
      * ``"nearest"`` (the RAW look) - each grid cell takes its nearest real
        sample's value, so the native sensor footprints read as crisp blocky
        quads (no inter-footprint blending). griddata("nearest") fills the ENTIRE
        bounding grid (no NaN), so the edge mask below is what clips it back to
        the true swath footprint.

    The convex hull (linear) alone fills swath CONCAVITIES (angled entry/exit,
    scan-edge density drops) with sliver triangles, so a distance-to-nearest-
    sample mask (EDGE_MASK_K, env-gated) nulls cells farther than ~one footprint
    from a real pixel -- recovering the true concave swath edge (transparent) and
    killing the diagonal streak/fan artifacts. The SAME mask (identical ``pts`` ->
    identical spacing) clips the nearest grid, so the raw and smoothed tiles share
    a pixel-identical swath boundary and overlay perfectly. A pixel is a valid
    source only where EVERY field it feeds is finite."""
    lon_u = _unwrap(lon, clon)
    valid = np.isfinite(lat) & np.isfinite(lon_u)
    for f in fields:
        valid &= np.isfinite(f)
    if valid.sum() < 3:
        raise ValueError("too few valid swath pixels to interpolate")

    pts = np.column_stack([lon_u[valid], lat[valid]])
    n = int(round(2.0 * half / step)) + 1
    gx = np.linspace(clon - half, clon + half, n)
    gy = np.linspace(clat - half, clat + half, n)
    GX, GY = np.meshgrid(gx, gy)
    out = []
    for f in fields:
        g = griddata(pts, f[valid], (GX, GY), method=method)
        out.append(g)

    # Swath-edge QC: mask regrid cells too far from any real sample so the
    # convex-hull slivers (the diagonal streak/fan artifacts) drop out, leaving
    # the true concave swath edge. Threshold = EDGE_MASK_K x the swath's own
    # sample spacing (median nearest-neighbour distance), so it adapts to the
    # sensor footprint (coarse SSMIS vs. fine AMSR2/GMI) instead of a fixed deg.
    if EDGE_MASK_ENABLED and pts.shape[0] >= 4:
        far = _edge_mask(pts, GX, GY, step)
        if far is not None:
            for g in out:
                g[far] = np.nan

    extent = [clon - half, clon + half, clat - half, clat + half]
    return extent, out


def _edge_mask(pts, GX, GY, step):
    """Boolean grid (GX/GY shape), True where a cell is farther than EDGE_MASK_K
    x the swath sample spacing from the nearest real sample -> a convex-hull
    sliver to be masked back to NaN. Spacing is the median nearest-neighbour
    distance among the samples (per-sensor adaptive); floored at one grid step so
    a degenerate (collinear/duplicate) swath never masks the whole field. Returns
    None if the spacing can't be estimated (caller then leaves the grid as-is)."""
    tree = cKDTree(pts)
    # Sample spacing from a strided subset (k=2 -> self + nearest neighbour); the
    # stride keeps this O(few thousand) regardless of swath size.
    stride = max(1, pts.shape[0] // 4000)
    nn, _ = tree.query(pts[::stride], k=2)
    nn = nn[:, 1]
    nn = nn[np.isfinite(nn) & (nn > 0.0)]
    if nn.size == 0:
        return None
    spacing = max(float(np.median(nn)), float(step))
    gd, _ = tree.query(np.column_stack([GX.ravel(), GY.ravel()]), k=1)
    return (gd > EDGE_MASK_K * spacing).reshape(GX.shape)


def _draw_scalar_image(ax, extent, grid, cmap, norm, zorder):
    """imshow a regular-grid scalar with bilinear smoothing; NaN -> transparent.
    Returns the AxesImage (a ScalarMappable, for the colorbar)."""
    return ax.imshow(np.ma.masked_invalid(grid), origin="lower", extent=extent,
                     cmap=cmap, norm=norm, interpolation="bilinear",
                     aspect="auto", zorder=zorder)


def _draw_rgba_image(ax, extent, rgba, zorder):
    """imshow a regular-grid RGBA composite with bilinear smoothing (alpha 0 where
    a channel was fill -> transparent outside the swath)."""
    ax.imshow(rgba, origin="lower", extent=extent, interpolation="bilinear",
              aspect="auto", zorder=zorder)


# ---------------------------------------------------------------------------
# Map-ready outputs (ADDITIVE): WGS84 bounds + chrome-free georeferenced tiles
# for mounting in the CycloLab stacking map (cyclolab_map.js type:image source).
# These never touch the chromed display PNGs.
# ---------------------------------------------------------------------------
def overpass_bounds_wgs84(meta: dict, half: float = HALF_DEG):
    """WGS84 ``[W, S, E, N]`` bbox of an overpass's storm-centered render box.

    The render grid spans ``clon +/- half`` x ``clat +/- half`` (see _regrid).
    ``clon`` is normalized to -180..180 so the consumer can mount the chrome-free
    tile as a MapLibre image source; a dateline box leaves E (or W) out of
    -180..180 range verbatim - MapLibre wraps it, and the consumer owns the single
    antimeridian unwrap site."""
    lonc = ((float(meta["clon"]) + 180.0) % 360.0) - 180.0
    clat = float(meta["clat"])
    return [lonc - half, clat - half, lonc + half, clat + half]


def _save_geotile(rgba, out_path: str) -> None:
    """Write a chrome-free, north-up RGBA PNG of a regridded product array (bare
    data pixels, transparent off-swath) for map mounting. The grid is row0=south
    (origin='lower' in the chromed render), so flip to PNG row0=north."""
    arr = np.clip(np.asarray(rgba, dtype=float), 0.0, 1.0)
    mpimg.imsave(out_path, np.flipud(arr))


def _hex(rgba) -> str:
    r, g, b = (int(round(float(c) * 255)) for c in rgba[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def mw_legends() -> dict:
    """Static legend descriptors for the map-mounted MW products (consumed by
    cyclolab_map.js). 89 PCT = discrete Kelvin stops sampled from the NRL ice-
    scattering table at its tick marks; 37 color = a qualitative RGB recipe (the
    composite has no scalar palette - _color37_rgba is a recipe, not a cmap)."""
    def _stops(cmap, norm, ticks):
        return [{"color": _hex(cmap(norm(float(k)))), "label": f"{k:g}"}
                for k in ticks]
    c37, n37 = pmwc.cmap_37h(), pmwc.norm_37h()
    c91, n91 = pmwc.cmap_91h(), pmwc.norm_91h()
    return {
        "37H": {"label": "37 GHz H-pol (K)", "discrete": True,
                "stops": _stops(c37, n37, pmwc._37H_TICKS)},
        "91H": {"label": "91 GHz H-pol (K)", "discrete": True,
                "stops": _stops(c91, n91, pmwc._91H_TICKS)},
        "color37": {"label": "37 GHz Color", "legendHtml":
                    "37V/37H composite - green = clear ocean, cyan = warm rain, "
                    "magenta/red = deep convection &amp; ice scattering."},
        "color91": {"label": "91 GHz Color", "legendHtml":
                    "89/91 V/H composite - teal/cyan ocean, red &rarr; black = "
                    "convective ice scattering."},
    }


def _geo_path(product_path: str) -> str:
    """Sibling chrome-free tile path for a chromed product PNG:
    ``..._89pct.png`` -> ``..._89pct_geo.png``."""
    base, ext = os.path.splitext(product_path)
    return f"{base}_geo{ext}"


def _raw_geo_path(product_path: str) -> str:
    """Sibling chrome-free RAW (native-footprint, nearest-neighbour) tile path:
    ``..._37H.png`` -> ``..._37H_geo_raw.png``. The viewer loads this when the
    user picks "Raw" so the native sensor footprints read as crisp blocky quads
    instead of the smoothed (linear) default tile."""
    base, ext = os.path.splitext(product_path)
    return f"{base}_geo_raw{ext}"


# ---------------------------------------------------------------------------
# Coastlines (center-unwrapped frame, ring-broken at >90 deg lon jumps)
# ---------------------------------------------------------------------------
_GEOJSON_CACHE: dict = {}


def _load_geojson(name: str) -> Optional[dict]:
    if name in _GEOJSON_CACHE:
        return _GEOJSON_CACHE[name]
    p = _REPO_ROOT / name
    data = None
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    _GEOJSON_CACHE[name] = data
    return data


def _feature_rings(feat: dict):
    geom = feat.get("geometry") or {}
    t = geom.get("type")
    if t == "LineString":
        yield geom["coordinates"]
    elif t == "MultiLineString":
        yield from geom["coordinates"]
    elif t == "Polygon":
        yield from geom["coordinates"]
    elif t == "MultiPolygon":
        for poly in geom["coordinates"]:
            yield from poly


def _draw_coast(ax, features, clon, extent, color, lw, zorder):
    """Draw geojson line/polygon rings in the center-unwrapped frame, broken at
    any >90 deg longitude jump (no stray slashes across the frame), clipped by the
    axes to the extent."""
    lon_min, lon_max, lat_min, lat_max = extent
    mlon = (lon_max - lon_min) * 0.1 + 0.5
    mlat = (lat_max - lat_min) * 0.1 + 0.5
    for feat in features:
        for ring in _feature_rings(feat):
            if len(ring) < 2:
                continue
            arr = np.asarray(ring, dtype=float)
            xs = _unwrap(arr[:, 0] % 360.0, clon)
            ys = arr[:, 1]
            if (xs.max() < lon_min - mlon or xs.min() > lon_max + mlon
                    or ys.max() < lat_min - mlat or ys.min() > lat_max + mlat):
                continue
            seam = np.where(np.abs(np.diff(xs)) > 90.0)[0] + 1
            if seam.size:
                xs = np.insert(xs, seam, np.nan)
                ys = np.insert(ys, seam, np.nan)
            ax.plot(xs, ys, color=color, linewidth=lw, zorder=zorder,
                    solid_capstyle="round", solid_joinstyle="round")


# ---------------------------------------------------------------------------
# 37 GHz color recipe (NRL 2-channel)
# ---------------------------------------------------------------------------
def _rgb_to_rgba(v: np.ndarray, h: np.ndarray, rgb) -> np.ndarray:
    """Stack canonical R,G,B gun arrays into an RGBA image, alpha 0 (transparent)
    where either channel is invalid (non-finite or Tb <= 0) so the swath edge and
    data gaps drop out cleanly."""
    r, g, b = rgb
    good = np.isfinite(v) & np.isfinite(h) & (v > 0.0) & (h > 0.0)
    rgba = np.zeros(v.shape + (4,), dtype=float)
    rgba[..., 0] = np.where(good, r, 0.0)
    rgba[..., 1] = np.where(good, g, 0.0)
    rgba[..., 2] = np.where(good, b, 0.0)
    rgba[..., 3] = np.where(good, 1.0, 0.0)
    return rgba


def _color37_rgba(tb37v: np.ndarray, tb37h: np.ndarray) -> np.ndarray:
    """Canonical NRL color37 true-color RGBA from the 37 V/H pair (exact recipe in
    pmw_canonical). Scene: green = clear ocean, cyan = warm rain, magenta = deep
    convection, red = ice scattering."""
    return _rgb_to_rgba(tb37v, tb37h, pmwc.color37_rgb(tb37v, tb37h))


def _color91_rgba(tb89v: np.ndarray, tb89h: np.ndarray) -> np.ndarray:
    """Canonical NRL color89/91 true-color RGBA from the high-freq V/H pair (exact
    recipe in pmw_canonical). Convective ice scattering reads red -> black; ocean
    teal/cyan."""
    return _rgb_to_rgba(tb89v, tb89h, pmwc.color91_rgb(tb89v, tb89h))


# ---------------------------------------------------------------------------
# Storm display id (ATCFID -> "09L 2024")
# ---------------------------------------------------------------------------
_BASIN_SUFFIX = {"AL": "L", "EP": "E", "CP": "C", "WP": "W",
                 "IO": "B", "SH": "S"}


def storm_short_name(atcf: str) -> str:
    """AL092024 -> '09L'  (basin-suffixed annual number; the ATCF id is honest)."""
    basin = atcf[:2].upper()
    nn = atcf[2:4]
    return f"{nn}{_BASIN_SUFFIX.get(basin, basin[:1])}"


def storm_display_id(atcf: str) -> str:
    """AL092024 -> '09L 2024'."""
    return f"{storm_short_name(atcf)} {atcf[4:8]}"


# ---------------------------------------------------------------------------
# The render
# ---------------------------------------------------------------------------
def _common_figure(meta: dict, product_label: str, sub_label: str,
                   right_stat: str):
    """Build the (fig, map ax) with the burned-in header band + footer credit.
    The caller draws the data, coast, ticks, and (optionally) a colorbar."""
    clat, clon = meta["clat"], meta["clon"]
    half = HALF_DEG
    extent = [clon - half, clon + half, clat - half, clat + half]

    geo_aspect = 1.0 / max(math.cos(math.radians(clat)), 0.15)

    base = 7.6
    map_w = map_h = base
    left_in, right_in = 0.62, 1.45
    band_in, foot_in, botpad_in = 0.80, 0.40, 0.06
    fig_w = left_in + map_w + right_in
    fig_h = botpad_in + foot_in + map_h + band_in
    map_bottom = botpad_in + foot_in

    fig = plt.figure(figsize=(fig_w, fig_h), facecolor=BAND_BG)
    ax = fig.add_axes([left_in / fig_w, map_bottom / fig_h,
                       map_w / fig_w, map_h / fig_h])
    ax.set_facecolor(PLOT_BG)
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    ax.set_aspect(geo_aspect)

    # Header band (top, full width).
    band = fig.add_axes([0.0, (map_bottom + map_h) / fig_h, 1.0,
                         band_in / fig_h])
    band.set_facecolor(BAND_BG)
    band.set_xlim(0, 1)
    band.set_ylim(0, 1)
    band.set_xticks([])
    band.set_yticks([])
    for sp in band.spines.values():
        sp.set_visible(False)

    pad_x = 0.012
    y_top, y_bot = 0.66, 0.24
    disp = storm_display_id(meta["atcf"])
    title = f"{meta['sensor']} {meta['platform']}   {disp}"
    band.text(pad_x, y_top, title, ha="left", va="center",
              color=TEXT_COLOR, fontsize=13, fontweight="bold",
              transform=band.transAxes)

    # Intensity chip after the title.
    fig.canvas.draw()
    chip = _intensity_chip(meta)
    renderer = fig.canvas.get_renderer()
    bb = band.texts[-1].get_window_extent(renderer=renderer)
    inv = band.transAxes.inverted()
    x_after = inv.transform((bb.x1, 0))[0]
    band.text(x_after + 0.012, y_top, chip, ha="left", va="center",
              color="#06222e", fontsize=10.5, fontweight="bold",
              transform=band.transAxes,
              bbox=dict(boxstyle="round,pad=0.34", facecolor="#5dd3ff",
                        edgecolor="none"))

    band.text(pad_x, y_bot, product_label + "   —   " + sub_label,
              ha="left", va="center", color=MUTED_COLOR, fontsize=10.5,
              transform=band.transAxes)

    rx = 1.0 - pad_x
    valid_str = meta["valid"].strftime("%Y-%m-%d %H:%MZ")
    # A pass mosaicked from several granules gets ONE header carrying the real
    # span, but only when the ends actually differ at minute resolution --
    # consecutive granules usually round to the same minute, and "15:04-15:04Z"
    # would be noise dressed up as precision.
    s_start, s_end = meta.get("span_start"), meta.get("span_end")
    if s_start is not None and s_end is not None:
        a, b = s_start.strftime("%H:%M"), s_end.strftime("%H:%M")
        if a != b:
            valid_str = (f"{s_start.strftime('%Y-%m-%d')} {a}-{b}Z"
                         if s_start.date() == s_end.date()
                         else f"{s_start.strftime('%Y-%m-%d %H:%M')}"
                              f"-{s_end.strftime('%Y-%m-%d %H:%M')}Z")
    # Say it plainly when the swath -- already merged at this point -- still
    # misses the centre, rather than letting a clipped pass imply full coverage.
    if meta.get("center_covered") is False:
        valid_str += "   ·   partial coverage (center outside swath)"
    band.text(rx, y_top, right_stat, ha="right", va="center",
              color=TEXT_COLOR, fontsize=11.5, fontweight="bold",
              transform=band.transAxes)
    band.text(rx, y_bot, f"Valid {valid_str}", ha="right", va="center",
              color=MUTED_COLOR, fontsize=10.5, transform=band.transAxes)

    # Footer credit (bottom-left watermark, bottom-right source). The source is
    # the actual provider: NASA GPM/PPS for live overpasses, TC-PRIMED for archive.
    src = meta.get("source_label", SOURCE_ARCHIVE)
    fig.text(left_in / fig_w + 0.004, (botpad_in * 0.4) / fig_h, WATERMARK,
             ha="left", va="bottom", color=MUTED_COLOR, fontsize=9)
    fig.text(1.0 - right_in / fig_w - 0.004, (botpad_in * 0.4) / fig_h, src,
             ha="right", va="bottom", color=MUTED_COLOR, fontsize=9)

    return fig, ax, extent, (fig_w, fig_h, left_in, right_in, map_bottom,
                             map_h, map_w, botpad_in, foot_in)


def _intensity_chip(meta: dict) -> str:
    kt = meta.get("intensity_kt")
    dev = (meta.get("dev_level") or "").strip()
    if kt and kt > 0:
        base = f"{kt} kt"
    else:
        base = dev or "n/a"
        return base
    return f"{base}  {dev}" if dev else base


def _decorate_axes(ax, clon, extent):
    """Coastlines, borders, lat/lon ticks."""
    coast = _load_geojson("ne_50m_coastline.geojson")
    countries = _load_geojson("ne_50m_admin_0_countries.geojson")
    if coast:
        _draw_coast(ax, coast.get("features", []), clon, extent,
                    COAST_COLOR, 0.9, 4)
    if countries:
        _draw_coast(ax, countries.get("features", []), clon, extent,
                    BORDER_COLOR, 0.6, 4)

    lon_min, lon_max, lat_min, lat_max = extent
    xt = np.arange(math.ceil(lon_min / 2) * 2, lon_max + 0.01, 2.0)
    yt = np.arange(math.ceil(lat_min / 2) * 2, lat_max + 0.01, 2.0)
    ax.set_xticks(xt)
    ax.set_yticks(yt)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: _lon_label(v)))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: _lat_label(v)))
    ax.tick_params(colors=MUTED_COLOR, labelsize=8.5, length=3)
    for sp in ax.spines.values():
        sp.set_color(TICK_COLOR)
        sp.set_linewidth(0.8)
    ax.grid(True, color="#243042", linewidth=0.4, alpha=0.55)


def _render_scalar_product(meta, out_path, *, tbkey, latkey, lonkey,
                           cmap, norm, ticks, product_label):
    """Render a single-channel H-pol BT product (37H / 91H) on its canonical
    colormap, with a Kelvin colorbar + chrome-free map tile. Returns out_path."""
    tb = meta[tbkey]
    if not np.isfinite(tb).any():
        raise ValueError(f"no valid pixels for {product_label}")
    # Smooth-resample onto a regular grid (smooth look, gap-free).
    extent, (g,) = _regrid(meta[latkey], meta[lonkey], [tb],
                           meta["clat"], meta["clon"])
    if not np.isfinite(g).any():
        raise ValueError(f"{product_label} swath does not cover the storm box")
    # MIN BT from the RAW swath (the smoothed grid warms extremes via interp).
    right_stat = f"MIN BT {float(np.nanmin(tb)):.1f} K"
    fig, ax, extent, geom = _common_figure(
        meta, product_label,
        f"{meta.get('source_label', SOURCE_ARCHIVE)} · {meta['platform']}",
        right_stat)
    cf = _draw_scalar_image(ax, extent, g, cmap, norm, zorder=2)
    _decorate_axes(ax, meta["clon"], extent)
    (fig_w, fig_h, left_in, right_in, map_bottom, map_h, map_w,
     botpad_in, foot_in) = geom
    cax = fig.add_axes([(left_in + map_w + 0.18) / fig_w, map_bottom / fig_h,
                        0.18 / fig_w, map_h / fig_h])
    cb = fig.colorbar(cf, cax=cax, ticks=ticks)
    cb.set_ticklabels([f"{k:g}" for k in ticks])
    cb.set_label("Brightness Temperature (K)", color=TEXT_COLOR, fontsize=9.5)
    cb.ax.tick_params(colors=MUTED_COLOR, labelsize=8)
    cb.outline.set_edgecolor(TICK_COLOR)
    fig.savefig(out_path, dpi=150, facecolor=BAND_BG)
    plt.close(fig)
    # ADDITIVE map tile: bare canonical-colored data pixels, transparent off-swath.
    try:
        _save_geotile(cmap(norm(np.ma.masked_invalid(g))), _geo_path(out_path))
    except Exception as e:  # noqa: BLE001
        print(f"tcprimed: {product_label} geotile skipped for "
              f"{os.path.basename(out_path)}: {type(e).__name__}: {e}",
              file=sys.stderr)
    # ADDITIVE raw (native-footprint) tile: nearest-neighbour regrid -> crisp
    # blocky quads (the "Raw" view). Same edge mask clips it to the swath, so it
    # overlays the smoothed tile exactly. Best-effort: a raw-tile failure never
    # drops the product (the viewer falls back to the smoothed tile).
    try:
        _, (gr,) = _regrid(meta[latkey], meta[lonkey], [tb],
                           meta["clat"], meta["clon"], method="nearest")
        _save_geotile(cmap(norm(np.ma.masked_invalid(gr))),
                      _raw_geo_path(out_path))
    except Exception as e:  # noqa: BLE001
        print(f"tcprimed: {product_label} raw geotile skipped for "
              f"{os.path.basename(out_path)}: {type(e).__name__}: {e}",
              file=sys.stderr)
    return out_path


def _render_rgb_product(meta, out_path, *, vkey, hkey, latkey, lonkey,
                        rgba_fn, product_label):
    """Render a V/H true-color product (color37 / color91) + map tile. Returns
    out_path. Channels are regridded FIRST, then the RGB recipe is applied so the
    color stays continuous."""
    if not (np.isfinite(meta[vkey]).any() and np.isfinite(meta[hkey]).any()):
        raise ValueError(f"no valid pixels for {product_label}")
    extent, (vg, hg) = _regrid(meta[latkey], meta[lonkey],
                               [meta[vkey], meta[hkey]],
                               meta["clat"], meta["clon"])
    rgba = rgba_fn(vg, hg)
    if not np.any(rgba[..., 3] > 0):
        raise ValueError(f"{product_label} swath does not cover the storm box")
    fig, ax, extent, geom = _common_figure(
        meta, product_label,
        f"{meta.get('source_label', SOURCE_ARCHIVE)} · {meta['platform']}", "")
    _draw_rgba_image(ax, extent, rgba, zorder=2)
    _decorate_axes(ax, meta["clon"], extent)
    fig.savefig(out_path, dpi=150, facecolor=BAND_BG)
    plt.close(fig)
    try:
        _save_geotile(rgba, _geo_path(out_path))
    except Exception as e:  # noqa: BLE001
        print(f"tcprimed: {product_label} geotile skipped for "
              f"{os.path.basename(out_path)}: {type(e).__name__}: {e}",
              file=sys.stderr)
    # ADDITIVE raw (native-footprint) tile: nearest-neighbour regrid of the
    # CHANNELS, then the same RGB recipe -> crisp blocky quads (the "Raw" view).
    try:
        _, (vgr, hgr) = _regrid(meta[latkey], meta[lonkey],
                                [meta[vkey], meta[hkey]],
                                meta["clat"], meta["clon"], method="nearest")
        _save_geotile(rgba_fn(vgr, hgr), _raw_geo_path(out_path))
    except Exception as e:  # noqa: BLE001
        print(f"tcprimed: {product_label} raw geotile skipped for "
              f"{os.path.basename(out_path)}: {type(e).__name__}: {e}",
              file=sys.stderr)
    return out_path


def render_color37(meta: dict, out_path: str) -> str:
    """37 GHz true-color (canonical NRL color37)."""
    return _render_rgb_product(
        meta, out_path, vkey="tb37v", hkey="tb37h", latkey="lat37",
        lonkey="lon37", rgba_fn=_color37_rgba, product_label="37 GHz Color")


def render_color91(meta: dict, out_path: str) -> str:
    """High-freq (89/91 GHz) true-color (canonical NRL color89/91)."""
    return _render_rgb_product(
        meta, out_path, vkey="tb89v", hkey="tb89h", latkey="lat89",
        lonkey="lon89", rgba_fn=_color91_rgba, product_label="91 GHz Color")


def render_37h(meta: dict, out_path: str) -> str:
    """37 GHz H-pol brightness temperature (canonical NRL 37H colormap)."""
    return _render_scalar_product(
        meta, out_path, tbkey="tb37h", latkey="lat37", lonkey="lon37",
        cmap=pmwc.cmap_37h(), norm=pmwc.norm_37h(), ticks=pmwc._37H_TICKS,
        product_label="37 GHz H-pol")


def render_91h(meta: dict, out_path: str) -> str:
    """High-freq (89/91 GHz) H-pol brightness temperature (canonical NRL 91H)."""
    return _render_scalar_product(
        meta, out_path, tbkey="tb89h", latkey="lat89", lonkey="lon89",
        cmap=pmwc.cmap_91h(), norm=pmwc.norm_91h(), ticks=pmwc._91H_TICKS,
        product_label="91 GHz H-pol")


# Product key -> renderer (the manifest, viewer, and map all key on these four).
_PRODUCT_RENDERERS = [
    ("color37", render_color37),
    ("color91", render_color91),
    ("37H", render_37h),
    ("91H", render_91h),
]


def render_overpass(meta: dict, out_dir: str, overpass_id: str) -> dict:
    """Render ALL FOUR products (color37, color91, 37H, 91H) for one overpass into
    out_dir; returns ``{"products": {key: png}, "tiles": {key: geo_png},
    "tiles_raw": {key: geo_raw_png}}`` (basenames) for whichever rendered.
    ``products`` are the chromed display PNGs; ``tiles`` are the matching
    chrome-free SMOOTHED (linear) map tiles; ``tiles_raw`` are the chrome-free RAW
    (nearest-neighbour, blocky native-footprint) tiles for the viewer's "Raw"
    toggle (present only where the geo write succeeded).

    Each product renders independently; a single-channel data gap (e.g. SSMIS F17
    with an all-fill 37 V channel) only drops that one product, not the overpass.
    Raises ValueError only if ALL FOUR fail (no usable imagery) so the caller can
    skip the overpass entirely; a partial render never leaves an orphan PNG without
    a manifest record."""
    os.makedirs(out_dir, exist_ok=True)
    out: dict = {}
    for key, fn in _PRODUCT_RENDERERS:
        p = os.path.join(out_dir, f"{overpass_id}_{key}.png")
        try:
            fn(meta, p)
            out[key] = os.path.basename(p)
        except Exception as e:  # noqa: BLE001
            if os.path.exists(p):
                os.remove(p)
            print(f"tcprimed: {key} skipped for {overpass_id}: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)

    if not out:
        raise ValueError("no usable imagery (all four products failed)")

    # Collect whichever chrome-free tiles were emitted alongside the products
    # (smoothed + raw native-footprint, each best-effort and independent).
    tiles: dict = {}
    tiles_raw: dict = {}
    for prod, base in out.items():
        geo = _geo_path(os.path.join(out_dir, base))
        if os.path.exists(geo):
            tiles[prod] = os.path.basename(geo)
        raw = _raw_geo_path(os.path.join(out_dir, base))
        if os.path.exists(raw):
            tiles_raw[prod] = os.path.basename(raw)
    return {"products": out, "tiles": tiles, "tiles_raw": tiles_raw}
