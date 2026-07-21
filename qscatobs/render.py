"""qscatobs.render - one QuikSCAT storm pass -> the house wind-field PNG.

Same visual language as the SAR pass render (dark basemap, the shared
0-100 kt wind colormap, muted chrome): the archive reads as one product
family with /obs/sar/ and /obs/ascat/. Reuses sarobs' basemap + colormap
so the scale bar is identical site-wide.
"""
from __future__ import annotations

import datetime as dt
import io

import numpy as np

from sarobs.render import (BG, BORDER, FG, MUTED, OCEAN, VMAX, _draw_basemap,
                           sar_cmap)


def render_pass(dec: dict, meta: dict, *, geo_dir: str = ".") -> tuple:
    """dec = qscatobs.decode.load_byu_hrwind output. meta: {storm, basin,
    season, rev, t (datetime|None), bt_wind_kt, bt_lat, bt_lon, type}.
    Returns (png_bytes, stats)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    lat, lon = dec["lat"], dec["lon"]
    spd = np.array(dec["speed"], dtype=float)     # m/s, 0 = no retrieval
    ok = (dec["nchoice"] > 0) & ~dec["land"]
    spd_m = np.ma.MaskedArray(spd, mask=~ok)

    # extent: the storm-centered part of the swath (the file spans the
    # whole overpass; clip to +-6.5 deg around the best-track center when
    # known, else the valid-data bbox)
    if meta.get("bt_lat") is not None:
        cy, cx = float(meta["bt_lat"]), float(meta["bt_lon"])
        half = 6.5
        ext = (cx - half, cx + half, cy - half, cy + half)
    else:
        ii, jj = np.nonzero(ok)
        ext = (float(lon[ii, jj].min()), float(lon[ii, jj].max()),
               float(lat[ii, jj].min()), float(lat[ii, jj].max()))

    fig = plt.figure(figsize=(11.4, 8.2), dpi=150, facecolor=BG)
    import math
    midlat = 0.5 * (ext[2] + ext[3])
    ax = fig.add_axes([0.055, 0.075, 0.83, 0.83], facecolor=OCEAN)
    ax.set_xlim(ext[0], ext[1])
    ax.set_ylim(ext[2], ext[3])
    ax.set_aspect(1.0 / max(0.2, math.cos(math.radians(midlat))))
    _draw_basemap(ax, ext, geo_dir)

    pm = ax.pcolormesh(lon, lat, spd_m, cmap=sar_cmap(), vmin=0.0,
                       vmax=VMAX, shading="nearest", zorder=2,
                       rasterized=True)

    # best-track center at overpass time (from the colocation table)
    if meta.get("bt_lat") is not None:
        ax.plot(meta["bt_lon"], meta["bt_lat"], marker="+", ms=13,
                mew=2.6, color="#07101c", zorder=5)
        ax.plot(meta["bt_lon"], meta["bt_lat"], marker="+", ms=12,
                mew=1.2, color=FG, zorder=6)

    for spine in ax.spines.values():
        spine.set_color(BORDER)
    ax.tick_params(colors=MUTED, labelsize=8.5, length=3)
    ax.grid(color=MUTED, alpha=0.16, lw=0.5, zorder=4)

    def _lonlab(x):
        v = ((x + 180.0) % 360.0) - 180.0
        return f"{abs(v):.0f}{'W' if v < 0 else 'E'}"
    ax.set_xticks(ax.get_xticks())
    ax.set_xticklabels([_lonlab(x) for x in ax.get_xticks()])
    ax.set_yticks(ax.get_yticks())
    ax.set_yticklabels([f"{abs(y):.0f}{'N' if y >= 0 else 'S'}"
                        for y in ax.get_yticks()])
    ax.set_xlim(ext[0], ext[1])
    ax.set_ylim(ext[2], ext[3])

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

    name = (meta.get("storm") or "").title()
    t = meta.get("t")
    tstr = t.strftime("%Y-%m-%d %H:%M UTC") if t else "time unknown"
    fig.text(0.055, 0.955, f"{name} ({meta.get('season', '')}) · "
             "QuikSCAT 2.5-km wind", color=FG, fontsize=14,
             fontweight="bold", ha="left")
    fig.text(0.885, 0.9575, f"{tstr} · rev {meta.get('rev', '?')}",
             color=MUTED, fontsize=9.5, ha="right")
    # honest context line: best-track intensity + the retrieval caveat.
    # Deliberately NO "peak retrieved" headline: Ku-band rain contamination
    # is spatially coherent (whole convective bands retrieve ~50 m/s even
    # under a 30-kt TD), so no robust statistic exists — despeckling was
    # tried and still read 77 kt on a TD pass. The field speaks for itself
    # under the caveat.
    sub = ""
    if meta.get("bt_wind_kt"):
        sub = (f"best track at overpass: {meta['type']} "
               f"{meta['bt_wind_kt']} kt (+ = center) · ")
    sub += ("Ku-band winds saturate ~100 kt and read HIGH in rain "
            "(convective bands)")
    fig.text(0.055, 0.9225, sub, color=MUTED, fontsize=9.5, ha="left")
    fig.text(0.055, 0.022,
             "QuikSCAT/SeaWinds (NASA JPL) · enhanced-resolution winds: "
             "BYU Scatterometer Climate Pathfinder · 2.5 km",
             color=MUTED, fontsize=7.2, ha="left")
    fig.text(0.885, 0.022, "@WeathermanAAA_", color=MUTED, fontsize=8,
             ha="right", fontweight="bold")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG)
    plt.close(fig)

    stats = {"valid_frac": float(np.mean(ok)),
             "t": t.strftime("%Y-%m-%dT%H:%M:%SZ") if t else None}
    return buf.getvalue(), stats
