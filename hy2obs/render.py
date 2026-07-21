"""hy2obs.render - one region crop of a daily HSCAT granule, house style
(shared SAR/QuikSCAT colormap + basemap; identical scale site-wide)."""
from __future__ import annotations

import io

import numpy as np

from sarobs.render import (BG, BORDER, FG, MUTED, OCEAN, VMAX, _draw_basemap,
                           sar_cmap)


def render_region(d: dict, cfg, *, sat: str, dirn: str, date) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import math

    w, e, s, n, label = cfg
    lons = np.where(d["lons"] > 180.0, d["lons"] - 360.0, d["lons"])
    order = np.argsort(lons)
    lons = lons[order]
    lats = d["lats"]
    spd = d["speed"][:, order]
    ii = (lats >= s) & (lats <= n)
    jj = (lons >= w) & (lons <= e)
    sub = np.ma.masked_invalid(spd[np.ix_(ii, jj)])

    fig = plt.figure(figsize=(11.4, 7.4), dpi=140, facecolor=BG)
    ax = fig.add_axes([0.055, 0.085, 0.83, 0.80], facecolor=OCEAN)
    ax.set_xlim(w, e)
    ax.set_ylim(s, n)
    ax.set_aspect(1.0 / max(0.2, math.cos(math.radians(0.5 * (s + n)))))
    _draw_basemap(ax, (w, e, s, n), ".")
    ax.pcolormesh(lons[jj], lats[ii], sub, cmap=sar_cmap(), vmin=0.0,
                  vmax=VMAX, shading="nearest", zorder=2, rasterized=True)
    for sp in ax.spines.values():
        sp.set_color(BORDER)
    ax.tick_params(colors=MUTED, labelsize=8.5, length=3)
    ax.grid(color=MUTED, alpha=0.16, lw=0.5, zorder=4)
    ax.set_xticks(ax.get_xticks())
    ax.set_xticklabels([f"{abs(x):.0f}{'W' if x < 0 else 'E'}"
                        for x in ax.get_xticks()])
    ax.set_yticks(ax.get_yticks())
    ax.set_yticklabels([f"{abs(y):.0f}{'N' if y >= 0 else 'S'}"
                        for y in ax.get_yticks()])
    ax.set_xlim(w, e)
    ax.set_ylim(s, n)

    tspan = ""
    if d["tmin"] and d["tmax"]:
        tspan = (f" · passes {d['tmin']:%H:%M}-{d['tmax']:%H:%M} UTC")
    fig.text(0.055, 0.955, f"{label} · {sat.upper()} HSCAT 25-km wind",
             color=FG, fontsize=14, fontweight="bold", ha="left")
    fig.text(0.885, 0.9575, f"{date:%Y-%m-%d} · {dirn} passes",
             color=MUTED, fontsize=9.5, ha="right")
    fig.text(0.055, 0.918,
             f"DELAYED look: files publish ~1-2 days behind sensing{tspan}"
             " · rain/land/low-quality cells removed · Ku-band reads high "
             "in residual rain", color=MUTED, fontsize=9, ha="left")
    fig.text(0.055, 0.022,
             "EUMETSAT/OSI SAF/KNMI · Generated using E.U. Copernicus "
             "Marine Service Information (doi:10.48670/moi-00182) · "
             "OSI-114-a · HSCAT L1B courtesy NSOAS/CNSA",
             color=MUTED, fontsize=6.8, ha="left")
    fig.text(0.885, 0.022, "@WeathermanAAA_", color=MUTED, fontsize=8,
             ha="right", fontweight="bold")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG)
    plt.close(fig)
    return buf.getvalue()
