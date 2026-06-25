"""tcprimed.render - storm-centered equirectangular PNGs for a TC overpass.

Two products per overpass:
  * 89 GHz PCT  (polarization-corrected Tb, ice89h palette, displayed in K)
  * 37 GHz color (NRL 2-channel RGB: R=37H, G=37V, B=37V, each scaled 180->280 K)

The swath is drawn on its NATIVE 2-D lat/lon grid with ``pcolormesh`` (one filled
quad per footprint) in a CENTER-RELATIVE (unwrapped) longitude frame, then the
axes is cropped to a storm-centered square. Native quads fill solid with no
inter-scanline gaps even for the coarse imagers (SSMIS 37 GHz), so there is no
"venetian-blind" striping; the axes crop handles the swath edge. Self-contained:
no cartopy; the coastline/border drawer reads the repo's ne_50m_*.geojson and
breaks each ring at large longitude jumps. PCT math is hafs_render.compute_pct89
(degC); the palette is tat_palettes ice89h.
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
from matplotlib.ticker import FuncFormatter  # noqa: E402

import netCDF4 as nc  # noqa: E402
from scipy.interpolate import griddata  # noqa: E402

import tat_palettes as tp  # noqa: E402
from hafs_render.hafs_plot import compute_pct89  # noqa: E402

from . import PMW_CHANNELS, SOURCE_ARCHIVE

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

_HERE = Path(__file__).resolve().parent
# The ne_50m geojsons live at the repo root (one level up from this package).
_REPO_ROOT = _HERE.parent


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
# Native-swath mesh draw (pcolormesh, gap-free)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Smooth swath -> regular grid resample (linear interpolation, gap-free)
# ---------------------------------------------------------------------------
def _regrid(lat, lon, fields, clat, clon, half=HALF_DEG, step=GRID_STEP):
    """Resample swath ``fields`` (a list of 2-D arrays sharing lat/lon) onto a
    regular lat/lon grid centered on (clat, clon), via LINEAR interpolation in a
    center-unwrapped longitude frame. Returns (extent, [grid fields]) with
    extent = [clon-half, clon+half, clat-half, clat+half].

    Linear (Delaunay) interpolation smooths the coarse imager footprints into the
    continuous a-reference-site/NRL look (vs. blocky native quads) AND fills solid with
    NO inter-scanline gaps, because every interior target cell is interpolated
    from the surrounding pixels. Cells outside the swath's convex hull come back
    NaN, giving a clean swath edge (transparent) without a hand-tuned distance
    mask. A pixel is a valid source only where EVERY field it feeds is finite."""
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
        g = griddata(pts, f[valid], (GX, GY), method="linear")
        out.append(g)
    extent = [clon - half, clon + half, clat - half, clat + half]
    return extent, out


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
def _color37_rgba(tb37v: np.ndarray, tb37h: np.ndarray) -> np.ndarray:
    """R=37H, G=37V, B=37V, each scaled clip((Tb-180)/100, 0, 1). Alpha 0 where
    either channel is fill -> transparent. Cyan/green ocean, magenta/pink heavy
    convection, white cold ice."""
    def s(x):
        return np.clip((x - 180.0) / (280.0 - 180.0), 0.0, 1.0)
    r = s(tb37h)
    g = s(tb37v)
    b = s(tb37v)
    good = np.isfinite(tb37v) & np.isfinite(tb37h)
    rgba = np.zeros(tb37v.shape + (4,), dtype=float)
    rgba[..., 0] = np.where(good, r, 0.0)
    rgba[..., 1] = np.where(good, g, 0.0)
    rgba[..., 2] = np.where(good, b, 0.0)
    rgba[..., 3] = np.where(good, 1.0, 0.0)
    return rgba


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


def render_89pct(meta: dict, out_path: str) -> str:
    """Render the 89 GHz PCT product. Returns out_path."""
    v_c = meta["tb89v"] - 273.15
    h_c = meta["tb89h"] - 273.15
    pct_c = compute_pct89({0: v_c, 1: h_c}, 0, 1)   # degC, clipped [105,290] K
    if pct_c is None or not np.isfinite(pct_c).any():
        raise ValueError("no valid 89 GHz pixels")
    # Smooth-resample the PCT onto a regular grid (a-reference-site look, gap-free).
    extent, (pct_g,) = _regrid(meta["lat89"], meta["lon89"], [pct_c],
                               meta["clat"], meta["clon"])
    if not np.isfinite(pct_g).any():
        raise ValueError("89 GHz swath does not cover the storm-centered box")

    enh = tp.get_enhancement("ice89h")
    cmap = enh["cmap"].with_extremes(bad=(0.0, 0.0, 0.0, 0.0))
    norm = tp.enhancement_norm("ice89h")

    # MIN BT from the RAW swath (the true coldest pixel); the smoothed grid warms
    # extremes via interpolation, so it would under-report the scattering minimum.
    btmin_k = float(np.nanmin(pct_c)) + 273.15
    right_stat = f"MIN BT {btmin_k:.1f} K"

    fig, ax, extent, geom = _common_figure(
        meta, "89 GHz PCT (polarization-corrected)",
        f"{meta.get('source_label', SOURCE_ARCHIVE)} · {meta['platform']}",
        right_stat)

    cf = _draw_scalar_image(ax, extent, pct_g, cmap, norm, zorder=2)
    _decorate_axes(ax, meta["clon"], extent)

    # Kelvin-labelled colorbar (ticks placed at K-273.15 on the degC norm).
    (fig_w, fig_h, left_in, right_in, map_bottom, map_h, map_w,
     botpad_in, foot_in) = geom
    cax = fig.add_axes([(left_in + map_w + 0.18) / fig_w, map_bottom / fig_h,
                        0.18 / fig_w, map_h / fig_h])
    cb = fig.colorbar(cf, cax=cax, ticks=[k - 273.15 for k in enh["ticks"]])
    cb.set_ticklabels([f"{k:g}" for k in enh["ticks"]])
    cb.set_label("Brightness Temperature (K)", color=TEXT_COLOR, fontsize=9.5)
    cb.ax.tick_params(colors=MUTED_COLOR, labelsize=8)
    cb.outline.set_edgecolor(TICK_COLOR)

    fig.savefig(out_path, dpi=150, facecolor=BAND_BG)
    plt.close(fig)
    return out_path


def render_37color(meta: dict, out_path: str) -> str:
    """Render the 37 GHz color product. Returns out_path."""
    if not (np.isfinite(meta["tb37v"]).any() and np.isfinite(meta["tb37h"]).any()):
        raise ValueError("no valid 37 GHz pixels")
    # Smooth-resample each channel, THEN build the RGB so the color is continuous.
    extent, (v37_g, h37_g) = _regrid(meta["lat37"], meta["lon37"],
                                     [meta["tb37v"], meta["tb37h"]],
                                     meta["clat"], meta["clon"])
    rgba = _color37_rgba(v37_g, h37_g)
    if not np.any(rgba[..., 3] > 0):
        raise ValueError("37 GHz swath does not cover the storm-centered box")

    fig, ax, extent, geom = _common_figure(
        meta, "37 GHz Color",
        f"{meta.get('source_label', SOURCE_ARCHIVE)} · {meta['platform']}", "")

    _draw_rgba_image(ax, extent, rgba, zorder=2)
    _decorate_axes(ax, meta["clon"], extent)

    fig.savefig(out_path, dpi=150, facecolor=BAND_BG)
    plt.close(fig)
    return out_path


def render_overpass(meta: dict, out_dir: str, overpass_id: str) -> dict:
    """Render both products for one overpass into out_dir; returns relative
    product paths {'89pct': ..., '37color': ...} for whichever rendered.

    Each product is rendered independently and a single-product data gap (e.g.
    SSMIS F17 has its 37 GHz V channel all-fill in some passes, so only 89 PCT is
    available) is tolerated: the available product is published and the missing
    one is omitted from the returned dict. Raises ValueError only if BOTH fail
    (no usable imagery) so the caller can skip the overpass entirely; a partial
    render never leaves an orphan PNG without a manifest record."""
    os.makedirs(out_dir, exist_ok=True)
    out: dict = {}

    p89 = os.path.join(out_dir, f"{overpass_id}_89pct.png")
    try:
        render_89pct(meta, p89)
        out["89pct"] = os.path.basename(p89)
    except Exception as e:  # noqa: BLE001
        if os.path.exists(p89):
            os.remove(p89)
        print(f"tcprimed: 89pct skipped for {overpass_id}: "
              f"{type(e).__name__}: {e}", file=sys.stderr)

    p37 = os.path.join(out_dir, f"{overpass_id}_37color.png")
    try:
        render_37color(meta, p37)
        out["37color"] = os.path.basename(p37)
    except Exception as e:  # noqa: BLE001
        if os.path.exists(p37):
            os.remove(p37)
        print(f"tcprimed: 37color skipped for {overpass_id}: "
              f"{type(e).__name__}: {e}", file=sys.stderr)

    if not out:
        raise ValueError("no usable imagery (both products failed)")
    return out
