"""
Static preview renderer for cross-checking the detection against Andrew's
reference plot. NOT used in CI - a local verification tool only. It mirrors the
web viewer (Pacific-centered equirectangular projection, the final pressure-bin
ring colors, the navy basemap) so the PNG is a faithful proxy of what
models/enscenters.js draws for a given forecast step.

Usage:
    python -m enscenters.preview <cycle.json> <out.png> [step_index]
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


def _bin_key(p):
    if p < 950: return "lt950"
    if p < 970: return "p950_970"
    if p < 990: return "p970_990"
    if p < 1000: return "p990_1000"
    return "gt1000"


def _load_geo(name):
    with open(os.path.join(HERE, name)) as f:
        return json.load(f)


def _split_ring(coords):
    """Pacific-center (lon -> 0..360) and split a ring/line at the 0/360 seam
    (start a new run when consecutive lon jumps > 180 deg). Returns list of
    Nx2 arrays."""
    runs, cur, prev = [], [], None
    for lon, lat in coords:
        L = lon % 360.0
        if prev is not None and abs(L - prev) > 180.0:
            if len(cur) > 1:
                runs.append(np.asarray(cur))
            cur = []
        cur.append((L, lat))
        prev = L
    if len(cur) > 1:
        runs.append(np.asarray(cur))
    return runs


def _draw_basemap(ax):
    from matplotlib.patches import Polygon as MplPoly
    ax.set_facecolor(OCEAN)
    countries = _load_geo("ne_110m_admin_0_countries.geojson")
    for feat in countries["features"]:
        geom = feat.get("geometry") or {}
        polys = ([geom["coordinates"]] if geom.get("type") == "Polygon"
                 else geom["coordinates"] if geom.get("type") == "MultiPolygon" else [])
        for poly in polys:
            for ring in poly:
                for run in _split_ring(ring):
                    ax.add_patch(MplPoly(run, closed=True, facecolor=LAND, edgecolor="none", zorder=1))
    coast = _load_geo("ne_110m_coastline.geojson")
    for feat in coast["features"]:
        geom = feat.get("geometry") or {}
        lines = ([geom["coordinates"]] if geom.get("type") == "LineString"
                 else geom["coordinates"] if geom.get("type") == "MultiLineString" else [])
        for ln in lines:
            for run in _split_ring(ln):
                ax.plot(run[:, 0], run[:, 1], color=COAST, lw=0.5, zorder=2)
    ax.set_xlim(0, 360)
    ax.set_ylim(-90, 90)
    ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color("#243049")


def render_step(cycle_json, out_png, step_index=0):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    with open(cycle_json) as f:
        d = json.load(f)
    steps = d["run_steps"]
    step_index = max(0, min(step_index, len(steps) - 1))
    step_h = steps[step_index]

    pts = [(c[1], c[2], c[3]) for m in d["members"] for c in m["centers"] if c[0] == step_h]
    lats = np.array([p[0] for p in pts])
    lons = np.array([p[1] % 360.0 for p in pts])   # Pacific-center
    mslp = np.array([p[2] for p in pts])

    fig = plt.figure(figsize=(15, 7.6), facecolor=PAGE_BG)
    gs = fig.add_gridspec(1, 2, width_ratios=[4.2, 1.0], wspace=0.04)
    ax = fig.add_subplot(gs[0, 0])
    _draw_basemap(ax)

    for key in BIN_ORDER:  # deepest last (on top); HOLLOW rings (no fill)
        mask = np.array([_bin_key(p) == key for p in mslp]) if len(mslp) else np.array([], dtype=bool)
        if mask.any():
            ax.scatter(lons[mask], lats[mask], s=26, facecolors="none",
                       edgecolors=BIN_COLORS[key], linewidths=1.1, alpha=0.92, zorder=3)

    init = d["init_time"].replace("T", " ").replace(":00:00Z", "Z")
    fig.suptitle("Ensemble Cyclone Centers", color=FG, fontsize=20,
                 fontweight="bold", x=0.30, y=0.97, ha="center")
    ax.set_title(
        f"{d['model_label']}   init {init}   {d['n_members']} members   "
        f"valid F{step_h:03d}   {len(pts)} centers",
        color=MUTED, fontsize=11.5, pad=8)

    handles = [Line2D([0], [0], marker="o", color="none", markerfacecolor="none",
                      markeredgecolor=BIN_COLORS[b["key"]], markeredgewidth=1.6,
                      markersize=8, label=b["label"]) for b in d["pressure_bins"]]
    ax.legend(handles=handles, loc="lower left", fontsize=9, framealpha=0.3,
              facecolor="#0d1626", edgecolor="#243049", labelcolor=FG)

    axt = fig.add_subplot(gs[0, 1]); axt.axis("off")
    rows = sorted([(m["id"], m["peak"]) for m in d["members"] if m["peak"]],
                  key=lambda r: r[1]["mslp_hpa"])
    axt.text(0.0, 1.0, "Peak by member", color=FG, fontsize=11, fontweight="bold",
             transform=axt.transAxes, va="top")
    axt.text(0.0, 0.965, "Member   Pmin   Vmax", color=MUTED, fontsize=8.5,
             family="monospace", transform=axt.transAxes, va="top")
    y = 0.94
    for mid, pk in rows[:48]:
        col = BIN_COLORS[_bin_key(pk["mslp_hpa"])]
        axt.text(0.0, y, f"{mid:<5} {pk['mslp_hpa']:6.0f} {pk['vmax_kt']:5.0f}",
                 color=col if mid != "CTL" else "#ffffff", fontsize=8.2,
                 family="monospace", transform=axt.transAxes, va="top",
                 fontweight="bold" if mid == "CTL" else "normal")
        y -= 0.0195

    fig.savefig(out_png, dpi=110, facecolor=PAGE_BG, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out_png}  step F{step_h:03d}  {len(pts)} centers, {len(rows)} member peaks")
    return out_png


if __name__ == "__main__":
    cj = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/ens_preview.png"
    si = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    render_step(cj, out, si)
