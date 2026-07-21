"""sarobs.render - one Level-2 SAR pass -> house-style PNG + thumbnail.

Rendered on STAR/SOCD's published SAR wind-speed color scale (sampled from
their product colorbar: a continuous ramp 0 to 51.44 m/s = 0 to 100 kt, with
hard visual breaks at ~0.5 m/s (calm black) and ~19.6 m/s (gale)). One muted
ramp, right-side colorbar in m/s with a knots scale opposite, plain title
carrying the valid time. Land/shore pixels are masked via the product's own
land mask; clamped speckle (>= 99 m/s) is dropped.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import math
import os

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

# House dark-map palette (mirrors the recon/ascat viewers' map styling).
BG = "#07101c"
OCEAN = "#0a1626"
LAND = "#19314e"
COAST = "#c9dbf2"
FG = "#e5edf6"
MUTED = "#8ea2bd"
BORDER = "#2a3e5c"

# STAR/SOCD SAR wind scale, sampled from the published product colorbar.
# (m/s, hex); top of scale 51.44 m/s = 100 kt; values above clamp.
VMAX = 51.44
_STOPS = [
    (0.0, "#000000"), (0.49, "#000000"), (0.5, "#000083"),
    (1, "#00008f"), (2, "#0000ab"), (4, "#0000e3"), (5, "#0003ff"),
    (6, "#001fff"), (8, "#005bff"), (10, "#0097ff"), (12, "#00d3ff"),
    (14, "#03fffb"), (16, "#2cffd2"), (18, "#56ffa8"), (19.59, "#6fff8f"),
    (19.6, "#dbff20"), (20, "#e2ff19"), (22, "#ffef00"), (24, "#ffd801"),
    (26, "#ffc10b"), (28, "#ff7807"), (30, "#ff3400"), (32, "#ff0300"),
    (34, "#d70000"), (36, "#af0000"), (38, "#70000f"), (40, "#591364"),
    (42, "#7e3b8d"), (44, "#9a70ab"), (46, "#b898c6"), (48, "#d3bbdb"),
    (50, "#e7d4e8"), (51.44, "#ecdfed"),
]


def sar_cmap() -> LinearSegmentedColormap:
    return LinearSegmentedColormap.from_list(
        "sar_wind", [(v / VMAX, c) for v, c in _STOPS])


def _load_geo(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return None


def _iter_lines(geom):
    if not geom:
        return
    t, c = geom.get("type"), geom.get("coordinates")
    if t == "LineString":
        yield c
    elif t in ("MultiLineString", "Polygon"):
        for part in c:
            yield part
    elif t == "MultiPolygon":
        for poly in c:
            for ring in poly:
                yield ring


def _draw_basemap(ax, ext, geo_dir="."):
    """Filled land + coastline from the vendored Natural Earth GeoJSON (the
    house basemap substitute; no cartopy)."""
    w, e, s, n = ext
    land = _load_geo(os.path.join(geo_dir, "ne_50m_admin_0_countries.geojson"))
    if land:
        for feat in land.get("features", []):
            g = feat.get("geometry") or {}
            polys = g.get("coordinates") or []
            if g.get("type") == "Polygon":
                polys = [polys]
            for poly in polys:
                for ring in poly[:1]:            # outer ring only
                    xs = [p[0] for p in ring]
                    ys = [p[1] for p in ring]
                    # dateline-shifted extents (e > 180): try the +360 copy too
                    for xs2 in ((xs, [x + 360.0 for x in xs]) if e > 180.0
                                else (xs,)):
                        if max(xs2) < w or min(xs2) > e or max(ys) < s \
                                or min(ys) > n:
                            continue
                        ax.fill(xs2, ys, color=LAND, zorder=1, lw=0)
    coast = _load_geo(os.path.join(geo_dir, "ne_50m_coastline.geojson"))
    if coast:
        for feat in coast.get("features", []):
            for line in _iter_lines(feat.get("geometry")):
                xs = [p[0] for p in line]
                ys = [p[1] for p in line]
                for xs2 in ((xs, [x + 360.0 for x in xs]) if e > 180.0
                            else (xs,)):
                    if max(xs2) < w or min(xs2) > e or max(ys) < s \
                            or min(ys) > n:
                        continue
                    ax.plot(xs2, ys, color=COAST, lw=0.7, alpha=0.9, zorder=3)


# Peak-statistic QC. The peak must be an INTERIOR, open-water, good-quality
# value — never a swath-edge/coastal artifact:
#   * EDGE_MARGIN_CELLS: erode the valid-data mask inward by this many cells
#     before taking the peak. At 500 m posting ~10 cells = ~5 km. Because
#     land, sea-ice, out-of-range and off-array cells are ALL "invalid," one
#     erosion buys the swath-edge buffer (SAR degrades at high incidence /
#     the radiometric edge, and coherent edge bands survive the despeckle),
#     the coastal/shallow-water buffer (post-landfall bay contamination the
#     product's own land mask passes), and an around-any-hole buffer, at once.
#   * INCID_MAX_DEG: additionally drop very-high-incidence far-range cells
#     when the product carries an incidence field.
EDGE_MARGIN_CELLS = 10
INCID_MAX_DEG = 47.0


def read_pass(nc_bytes: bytes) -> dict:
    """Level-2 nc -> {lon, lat, wind (masked ndarray), incid, t} arrays."""
    import netCDF4
    ds = netCDF4.Dataset("inmem", memory=nc_bytes)
    try:
        wind = np.array(ds.variables["sar_wind"][:], dtype=float)
        lon = np.array(ds.variables["longitude"][:], dtype=float)
        lat = np.array(ds.variables["latitude"][:], dtype=float)
        mask = np.array(ds.variables["mask"][:])
        fill = getattr(ds.variables["sar_wind"], "_FillValue", -999.0)
        bad = (mask != -1) | (wind == fill) | (wind < 0) | (wind >= 99.0) \
            | ~np.isfinite(wind)
        wind = np.ma.masked_where(bad, wind)
        incid = None
        if "incid" in ds.variables:
            incid = np.array(ds.variables["incid"][:], dtype=float)
        t = None
        try:
            acq = float(np.array(ds.variables["acquisition_time"][:]))
            t = (dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)
                 + dt.timedelta(seconds=acq))
        except Exception:                        # noqa: BLE001
            pass
        return {"lon": lon, "lat": lat, "wind": wind, "incid": incid, "t": t}
    finally:
        ds.close()


def _box_all_valid(valid: np.ndarray, m: int) -> np.ndarray:
    """Boolean array: True where the full (2m+1)^2 box centred on a cell is
    entirely valid (off-array counts as invalid, so the array edge erodes
    too). O(N) via a summed-area table — the erosion of the valid mask by an
    (2m+1)-square structuring element."""
    if m <= 0:
        return valid.astype(bool)
    v = valid.astype(np.int64)
    sat = np.pad(v, ((m + 1, m), (m + 1, m)), mode="constant")
    sat = sat.cumsum(0).cumsum(1)
    H, W = valid.shape
    k = 2 * m + 1
    # window sum over the k x k box centred on each original cell
    box = (sat[k:k + H, k:k + W] - sat[0:H, k:k + W]
           - sat[k:k + H, 0:W] + sat[0:H, 0:W])
    return box == k * k


def _boxsum(a: np.ndarray, r: int) -> np.ndarray:
    k = 2 * r + 1
    p = np.pad(a, r, mode="constant")
    out = np.zeros_like(a, dtype=float)
    for dy in range(k):
        for dx in range(k):
            out += p[dy:dy + a.shape[0], dx:dx + a.shape[1]]
    return out


def despeckled_peak(wind: np.ma.MaskedArray, incid=None, *,
                    edge_margin: int = EDGE_MARGIN_CELLS,
                    incid_max: float = INCID_MAX_DEG):
    """Robust interior open-water near-peak. The 3x3-mean-smoothed field
    (one hot pixel cannot fake a peak — the mean dilutes it), taken only over
    cells that are: valid ocean (the caller's land/ice/>=99 QC), INTERIOR
    (a fully-valid box of ``edge_margin`` cells around them — erodes swath
    edges, coastlines and holes), well-neighboured (full 3x3 + >=20/25 in
    5x5), and not very-high-incidence far-range (when ``incid`` is given).
    Returns (peak_ms, (iy, ix)) or None when nothing qualifies (tiny or
    edge-only scene) so the caller can honestly show 'peak n/a' rather than a
    boundary artifact."""
    valid = ~np.ma.getmaskarray(wind)            # always a full bool array
    filled = np.where(valid, np.ma.getdata(wind), 0.0)
    vf = valid.astype(float)

    nsum, ncnt = _boxsum(filled, 1), _boxsum(vf, 1)
    ncnt5 = _boxsum(vf, 2)
    with np.errstate(invalid="ignore", divide="ignore"):
        smooth = np.where(ncnt > 0, nsum / ncnt, 0.0)

    interior = _box_all_valid(valid, edge_margin)
    ok = valid & interior & (ncnt >= 9) & (ncnt5 >= 20)
    if incid is not None:
        with np.errstate(invalid="ignore"):
            ok &= (incid <= incid_max)           # nan incid -> False -> dropped
    if not ok.any():
        return None
    smooth = np.where(ok, smooth, -1.0)
    iy, ix = np.unravel_index(int(np.argmax(smooth)), smooth.shape)
    return float(smooth[iy, ix]), (int(iy), int(ix))


LOW_SSS_PSU = 33.0


def _overlay_low_salinity(ax, ext, salinity) -> bool:
    """Hatch low-salinity water (SMAP SSS < LOW_SSS_PSU) within the pass
    extent. Nearest-neighbour samples the regular 0.25 deg SSS grid onto a
    coarse grid over the extent, then hatches the sub-threshold region.
    Returns True iff any low-salinity water falls inside the scene."""
    import matplotlib as mpl
    slat, slon, sss = salinity
    gx = np.linspace(ext[0], ext[1], 140)
    gy = np.linspace(ext[2], ext[3], 100)
    GX, GY = np.meshgrid(gx, gy)
    dlon = float(slon[1] - slon[0]); dlat = float(slat[1] - slat[0])
    li = np.clip(np.round((GX % 360.0 - slon[0]) / dlon).astype(int),
                 0, slon.size - 1)
    lj = np.clip(np.round((GY - slat[0]) / dlat).astype(int), 0, slat.size - 1)
    samp = sss[lj, li]
    low = np.where(np.isfinite(samp) & (samp < LOW_SSS_PSU), 1.0, 0.0)
    if low.sum() < 2:
        return False
    old_c, old_lw = mpl.rcParams["hatch.color"], mpl.rcParams["hatch.linewidth"]
    mpl.rcParams["hatch.color"] = "#cfe0f5"
    mpl.rcParams["hatch.linewidth"] = 0.45
    try:
        ax.contourf(GX, GY, low, levels=[0.5, 1.5], colors=[(0, 0, 0, 0)],
                    hatches=["////"], zorder=2.6)
        ax.contour(GX, GY, low, levels=[0.5], colors="#cfe0f5",
                   linewidths=0.6, alpha=0.5, zorder=2.7)
    finally:
        mpl.rcParams["hatch.color"] = old_c
        mpl.rcParams["hatch.linewidth"] = old_lw
    return True


def render_pass(nc_bytes: bytes, meta: dict, *, geo_dir: str = ".",
                salinity=None) -> tuple[bytes, bytes, dict]:
    """Render one pass. ``meta``: {stem, sat, pol, t, storm_name, atcf}.
    ``salinity`` (optional): (lats, lons, sss_grid) SMAP 8-day SSS in PSU on a
    regular 0.25 deg grid (lons 0-360) — low-salinity water is hatched as a
    reliability cue. Returns (png_bytes, thumb_jpg_bytes, stats)."""
    d = read_pass(nc_bytes)
    lon, lat, wind, incid = d["lon"], d["lat"], d["wind"], d["incid"]
    t = meta.get("t") or d["t"]

    if wind.count() == 0:
        raise ValueError("no valid water wind cells in pass")
    # Antimeridian: a swath straddling 180 has lon values on both ends of
    # [-180, 180]; raw min/max would span the world and pcolormesh smears.
    # Shift the western-hemisphere half up by 360 so the grid is contiguous
    # (tick labels re-normalize for display below).
    if float(lon.max()) - float(lon.min()) > 180.0:
        lon = np.where(lon < 0, lon + 360.0, lon)
    vlon = lon[~wind.mask] if np.ma.is_masked(wind) else lon.ravel()
    vlat = lat[~wind.mask] if np.ma.is_masked(wind) else lat.ravel()
    w0, e0 = float(vlon.min()), float(vlon.max())
    s0, n0 = float(vlat.min()), float(vlat.max())
    # pad + keep on-screen degrees proportional (cos-lat aspect, house rule)
    pad_x = max(0.35, (e0 - w0) * 0.06)
    pad_y = max(0.35, (n0 - s0) * 0.06)
    ext = [w0 - pad_x, e0 + pad_x, s0 - pad_y, n0 + pad_y]
    midlat = 0.5 * (ext[2] + ext[3])
    aspect = 1.0 / max(0.2, math.cos(math.radians(midlat)))

    fig = plt.figure(figsize=(11.4, 8.2), dpi=150, facecolor=BG)
    ax = fig.add_axes([0.055, 0.075, 0.83, 0.83], facecolor=OCEAN)
    ax.set_xlim(ext[0], ext[1])
    ax.set_ylim(ext[2], ext[3])
    ax.set_aspect(aspect)

    _draw_basemap(ax, ext, geo_dir)
    pm = ax.pcolormesh(lon, lat, wind, cmap=sar_cmap(), vmin=0.0, vmax=VMAX,
                       shading="nearest", zorder=2, rasterized=True)

    # low-salinity reliability overlay (SMAP SSS): hatch water below the
    # threshold where C-band SAR winds are less reliable. Subtle diagonal
    # hatch over a transparent fill so the wind field still reads through.
    sal_shown = False
    if salinity is not None:
        try:
            sal_shown = _overlay_low_salinity(ax, ext, salinity)
        except Exception:                        # noqa: BLE001 — additive cue
            sal_shown = False

    # robust interior open-water near-peak (edge/coast-eroded, despeckled) +
    # a small dark-haloed hollow ring at its location — pinpoint, not firework.
    pk = despeckled_peak(wind, incid)
    if pk is not None:
        peak_ms, (piy, pix) = pk
        plon, plat = float(lon[piy, pix]), float(lat[piy, pix])
        ax.plot(plon, plat, marker="o", mfc="none", mec="#07101c",
                ms=8, mew=2.6, zorder=5, alpha=0.9)
        ax.plot(plon, plat, marker="o", mfc="none", mec=FG,
                ms=8, mew=1.1, zorder=6, alpha=0.95)
    else:
        peak_ms, plon, plat = None, None, None

    # faint graticule + labeled ticks, house-muted
    for spine in ax.spines.values():
        spine.set_color(BORDER)
    ax.tick_params(colors=MUTED, labelsize=8.5, length=3)
    ax.grid(color=MUTED, alpha=0.16, lw=0.5, zorder=4)
    def _lonlab(x):
        v = ((x + 180.0) % 360.0) - 180.0        # display in [-180, 180)
        return f"{abs(v):.1f}{'W' if v < 0 else 'E'}"
    ax.set_xticks(ax.get_xticks())
    ax.set_xticklabels([_lonlab(x) for x in ax.get_xticks()])
    ax.set_yticks(ax.get_yticks())
    ax.set_yticklabels([f"{abs(y):.1f}{'N' if y >= 0 else 'S'}"
                        for y in ax.get_yticks()])
    ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3])

    # right-side colorbar: m/s primary, knots opposite
    cax = fig.add_axes([0.905, 0.115, 0.018, 0.75])
    cb = fig.colorbar(pm, cax=cax)
    cb.set_ticks([0, 10, 20, 30, 40, 50])
    cb.ax.tick_params(colors=MUTED, labelsize=8.5)
    cb.outline.set_edgecolor(BORDER)
    cb.set_label("m/s", color=MUTED, fontsize=9)
    kt = cb.ax.secondary_yaxis(
        "left", functions=(lambda v: v * 1.94384, lambda v: v / 1.94384))
    kt.set_ylabel("kt", color=MUTED, fontsize=8, labelpad=1)
    kt.set_yticks([0, 20, 40, 60, 80, 100])
    kt.tick_params(colors=MUTED, labelsize=7.5, length=2)

    # plain title: identity left, valid time + platform right
    name = meta.get("storm_name") or ""
    atcf = (meta.get("atcf") or "").upper()
    tstr = t.strftime("%Y-%m-%d %H:%M UTC") if t else "time unknown"
    fig.text(0.055, 0.955, f"{name} ({atcf}) · SAR surface wind",
             color=FG, fontsize=14, fontweight="bold", ha="left")
    fig.text(0.885, 0.9575, f"{tstr} · {meta.get('sat', '')} {meta.get('pol') or ''}",
             color=MUTED, fontsize=9.5, ha="right")
    # near-peak readout: kt primary, m/s in parens. Honest label — an
    # instantaneous scene peak (interior open-water, despeckled), not a
    # sustained max. 'peak n/a' when no interior cell qualifies.
    if peak_ms is not None:
        peak_kt = peak_ms * 1.94384
        _dplat = f"{abs(plat):.1f}{'N' if plat >= 0 else 'S'}"
        _plond = ((plon + 180.0) % 360.0) - 180.0
        _dplon = f"{abs(_plond):.1f}{'W' if _plond < 0 else 'E'}"
        peak_txt = (f"Peak SAR wind ~{peak_kt:.0f} kt ({peak_ms:.0f} m/s) "
                    f"near {_dplat} {_dplon} · instantaneous scene peak, "
                    "interior open water")
    else:
        peak_kt = None
        peak_txt = ("Peak SAR wind n/a · no interior open-water cell "
                    "qualifies (edge-only scene)")
    fig.text(0.055, 0.9225, peak_txt, color=MUTED, fontsize=9.5, ha="left")
    # provider credit (required attribution, per constellation) + watermark
    yr = t.year if t else dt.datetime.now(dt.timezone.utc).year
    sat = (meta.get("sat") or "").upper()
    if sat.startswith("RCM"):
        imagery = ("RADARSAT Constellation Mission imagery © Government of "
                   f"Canada {yr}")
    elif sat.startswith("S1"):
        imagery = f"Contains modified Copernicus Sentinel-1 data {yr}"
    elif sat.startswith("RS2"):
        imagery = f"RADARSAT-2 imagery © MDA {yr}"
    else:
        imagery = "Satellite SAR imagery"
    fig.text(0.055, 0.022,
             f"{imagery} · Processed at NOAA/NESDIS/STAR/SOCD · ~500 m C-band"
             + (" · salinity: RSS SMAP" if sal_shown else ""),
             color=MUTED, fontsize=7.2, ha="left")
    if sal_shown:
        fig.text(0.055, 0.05,
                 f"Hatched: low-salinity water (SMAP 8-day SSS < "
                 f"{int(LOW_SSS_PSU)}), where C-band SAR winds are less "
                 "reliable", color=MUTED, fontsize=7.5, ha="left")
    fig.text(0.885, 0.022, "@WeathermanAAA_", color=MUTED, fontsize=8,
             ha="right", fontweight="bold")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG)
    plt.close(fig)
    png = buf.getvalue()

    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGB")
    im.thumbnail((360, 360))
    tb = io.BytesIO()
    im.save(tb, format="JPEG", quality=85)

    valid = wind.compressed()
    stats = {
        "max_ms": round(float(valid.max()), 1),
        "peak_ms": round(peak_ms, 1) if peak_ms is not None else None,
        "peak_kt": round(peak_kt) if peak_kt is not None else None,
        "peak_lat": round(plat, 2) if plat is not None else None,
        "peak_lon": (round(((plon + 180.0) % 360.0) - 180.0, 2)
                     if plon is not None else None),
        "mean_ms": round(float(valid.mean()), 1),
        "n_cells": int(valid.size),
        "bbox": [round(((w0 + 180.0) % 360.0) - 180.0, 2),
                 round(((e0 + 180.0) % 360.0) - 180.0, 2),
                 round(s0, 2), round(n0, 2)],
        "t": t.strftime("%Y-%m-%dT%H:%M:%SZ") if t else None,
    }
    return png, tb.getvalue(), stats
