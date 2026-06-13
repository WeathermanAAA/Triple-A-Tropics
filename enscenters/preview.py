"""
Static preview renderer for cross-checking the detection + the region crop
against Andrew's reference. NOT used in CI - a local verification tool only. It
mirrors the web viewer (per-region equirectangular extent incl. dateline wrap,
the final pressure-bin ring colors, the navy basemap) so the PNG is a faithful
proxy of what models/enscenters.js + models/regions.js draw.

Usage:
    python -m enscenters.preview <cycle.json> <out.png> [region_key] [step_index]
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Mirrors models/enscenters.js PRESSURE_BIN_COLORS / BASEMAP (Andrew's FINAL).
BIN_COLORS = {
    "gt1000": "#dfe8ff", "p990_1000": "#1f9bff", "p970_990": "#ffd21a",
    "p950_970": "#ff1f47", "lt950": "#ff3d9a",
}
BIN_ORDER = ["gt1000", "p990_1000", "p970_990", "p950_970", "lt950"]
OCEAN, LAND, COAST = "#07101c", "#2f3f59", (0.59, 0.69, 0.80, 0.28)
PAGE_BG, FG, MUTED = "#07101c", "#e8ebef", "#9199a4"

# A subset of models/regions.js GROUPS, enough for the verify previews. w/e/s/n
# in deg; w > e crosses the dateline.
REGIONS = {
    "atlantic": ("Atlantic", -100, -5, 0, 55),
    "wpac": ("West Pacific", 100, 180, 0, 45),
    "swpac": ("SW Pacific", 140, -160, -35, 5),
    "npac": ("North Pacific", 120, -110, 10, 60),
    "global": ("Global", -180, 180, -88, 88),
}


def _bin_key(p):
    if p < 950: return "lt950"
    if p < 970: return "p950_970"
    if p < 990: return "p970_990"
    if p < 1000: return "p990_1000"
    return "gt1000"


def _in_region(lon, lat, r):
    _, w, e, s, n = r
    if lat < s or lat > n:
        return False
    if w <= e:
        return w <= lon <= e
    return lon >= w or lon <= e


def _extent_of(r):
    _, w, e, s, n = r
    if w <= -180 and e >= 180:
        return [0.0, 360.0, float(s), float(n)]
    lon_max = e if e >= w else e + 360
    return [float(w), float(lon_max), float(s), float(n)]


def _to_frame(lon, ext):
    """Fold a -180..180 lon into the extent's display frame."""
    L = lon
    if ext[1] > 180 and L < ext[0]:
        L += 360
    return L


def _load_geo(name):
    with open(os.path.join(HERE, name)) as f:
        return json.load(f)


def _split_runs(coords, ext):
    """Project lons into the extent frame and split a ring/line where it jumps
    the seam (> half the lon span)."""
    span = ext[1] - ext[0]
    runs, cur, prev = [], [], None
    for lon, lat in coords:
        L = _to_frame(lon, ext)
        if prev is not None and abs(L - prev) > span * 0.5:
            if len(cur) > 1:
                runs.append(np.asarray(cur))
            cur = []
        cur.append((L, lat))
        prev = L
    if len(cur) > 1:
        runs.append(np.asarray(cur))
    return runs


def _draw_basemap(ax, ext):
    from matplotlib.patches import Polygon as MplPoly
    ax.set_facecolor(OCEAN)
    countries = _load_geo("ne_110m_admin_0_countries.geojson")
    for feat in countries["features"]:
        geom = feat.get("geometry") or {}
        polys = ([geom["coordinates"]] if geom.get("type") == "Polygon"
                 else geom["coordinates"] if geom.get("type") == "MultiPolygon" else [])
        for poly in polys:
            for ring in poly:
                for run in _split_runs(ring, ext):
                    ax.add_patch(MplPoly(run, closed=True, facecolor=LAND, edgecolor="none", zorder=1))
    coast = _load_geo("ne_110m_coastline.geojson")
    for feat in coast["features"]:
        geom = feat.get("geometry") or {}
        lines = ([geom["coordinates"]] if geom.get("type") == "LineString"
                 else geom["coordinates"] if geom.get("type") == "MultiLineString" else [])
        for ln in lines:
            for run in _split_runs(ln, ext):
                ax.plot(run[:, 0], run[:, 1], color=COAST, lw=0.5, zorder=2)
    ax.set_xlim(ext[0], ext[1])
    ax.set_ylim(ext[2], ext[3])
    ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_color("#243049")


def render_step(cycle_json, out_png, region_key="atlantic", step_index=0):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    with open(cycle_json) as f:
        d = json.load(f)
    r = REGIONS.get(region_key, REGIONS["atlantic"])
    ext = _extent_of(r)
    steps = d["run_steps"]
    step_index = max(0, min(step_index, len(steps) - 1))
    step_h = steps[step_index]

    pts = [(c[1], c[2], c[3]) for m in d["members"] for c in m["centers"]
           if c[0] == step_h and _in_region(c[2], c[1], r)]
    lats = np.array([p[0] for p in pts])
    lons = np.array([_to_frame(p[1], ext) for p in pts])
    mslp = np.array([p[2] for p in pts])

    aspect = (ext[1] - ext[0]) / (ext[3] - ext[2])
    fig = plt.figure(figsize=(13, max(4.5, 13 / max(aspect, 0.9) * 0.5 + 2.2)), facecolor=PAGE_BG)
    gs = fig.add_gridspec(1, 2, width_ratios=[4.2, 1.0], wspace=0.04)
    ax = fig.add_subplot(gs[0, 0])
    _draw_basemap(ax, ext)

    for key in BIN_ORDER:
        mask = np.array([_bin_key(p) == key for p in mslp]) if len(mslp) else np.array([], dtype=bool)
        if mask.any():
            ax.scatter(lons[mask], lats[mask], s=30, facecolors="none",
                       edgecolors=BIN_COLORS[key], linewidths=1.2, alpha=0.92, zorder=3)

    init = d["init_time"].replace("T", " ").replace(":00:00Z", "Z")
    fig.suptitle("Ensemble Cyclone Centers  -  " + r[0], color=FG, fontsize=18,
                 fontweight="bold", x=0.30, y=0.99, ha="center")
    ax.set_title(
        f"{d['model_label']}   init {init}   {d['n_members']} members   "
        f"valid F{step_h:03d}   {len(pts)} centers in region",
        color=MUTED, fontsize=11, pad=8)

    handles = [Line2D([0], [0], marker="o", color="none", markerfacecolor="none",
                      markeredgecolor=BIN_COLORS[b["key"]], markeredgewidth=1.6,
                      markersize=8, label=b["label"]) for b in d["pressure_bins"]]
    ax.legend(handles=handles, loc="lower left", fontsize=8.5, framealpha=0.3,
              facecolor="#0d1626", edgecolor="#243049", labelcolor=FG)

    # region-scoped peak table
    axt = fig.add_subplot(gs[0, 1]); axt.axis("off")
    rows = []
    for m in d["members"]:
        best = None
        for c in m["centers"]:
            if _in_region(c[2], c[1], r) and (best is None or c[3] < best[0]):
                best = (c[3], c[4])
        if best:
            rows.append((m["id"], best[0], best[1]))
    rows.sort(key=lambda t: t[1])
    axt.text(0.0, 1.0, "Peak in " + r[0], color=FG, fontsize=10.5, fontweight="bold",
             transform=axt.transAxes, va="top")
    axt.text(0.0, 0.965, "Member   Pmin   Vmax", color=MUTED, fontsize=8.5,
             family="monospace", transform=axt.transAxes, va="top")
    y = 0.94
    for mid, pmin, vmax in rows[:46]:
        col = BIN_COLORS[_bin_key(pmin)]
        axt.text(0.0, y, f"{mid:<5} {pmin:6.0f} {vmax:5.0f}",
                 color=col if mid != "CTL" else "#ffffff", fontsize=8.0,
                 family="monospace", transform=axt.transAxes, va="top",
                 fontweight="bold" if mid == "CTL" else "normal")
        y -= 0.02

    fig.savefig(out_png, dpi=110, facecolor=PAGE_BG, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out_png}  region={region_key} F{step_h:03d}  {len(pts)} centers, {len(rows)} peaks")
    return out_png


if __name__ == "__main__":
    cj = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/ens_preview.png"
    rk = sys.argv[3] if len(sys.argv) > 3 else "atlantic"
    si = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    render_step(cj, out, rk, si)
