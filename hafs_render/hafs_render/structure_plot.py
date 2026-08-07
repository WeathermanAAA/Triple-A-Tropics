#!/usr/bin/env python3
"""The azimuthal-mean structure PANEL (spec #25) + radii rose (spec #7).

A 2x2 diagnostic plate in the house style, same canvas as every map frame
(hafs_plot.IMAGE_W_PX x IMAGE_H_PX) so nothing downstream meets a surprise
size. All numbers come from structure_diag (which consumes the ONE polar
core); this module only draws.

Panel A - azimuthal-mean CYCLONIC tangential wind vs radius, 10 m + 850/700/
500 hPa, NEST solid with the PARENT 10 m profile dashed beside it: the
resolution-driven intensity caveat drawn, not asserted (Davis 2018 - coarse
grids cap tangential wind; the gap between the two curves at the RMW IS the
caveat, and its number is printed).
Panel B - radial wind (outward positive; the inflow layer is the negative
region). Panel C - warm core: THICKNESS-DERIVED 850-500 mean-layer
temperature anomaly (the cache carries no temperature levels; the derivation
is hydrostatic and labelled). Panel D - the #7 quadrant-max wind radii rose
with the ATCF-comparable numbers printed per quadrant.

Cyclonic display sign: v_t from polar is CCW-positive geometry; the panels
show CYCLONIC wind positive in BOTH hemispheres (flip by sign(lat) at draw
time only), stated on the axis label.
"""
from __future__ import annotations

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from hafs_render import hafs_plot as hp
from hafs_render import structure_diag as st

_LEVEL_STYLE = {
    "10m": {"color": "#79f0d6", "lw": 2.2},
    "850": {"color": "#4193f0", "lw": 1.4},
    "700": {"color": "#bdbf1c", "lw": 1.4},
    "500": {"color": "#d35fa9", "lw": 1.4},
}
_PARENT_STYLE = {"color": "#ffffff", "lw": 1.6, "ls": "--", "alpha": 0.75}
_FG = "#dfe7f0"
_MUTED = "#8d949c"
_GRID = {"color": "#2a323d", "lw": 0.6}


def _style_axes(ax, title):
    ax.set_facecolor("#0d1117")
    ax.set_title(title, color=_FG, fontsize=11, fontweight="bold", loc="left",
                 pad=6)
    ax.tick_params(colors=_MUTED, labelsize=8)
    for s in ax.spines.values():
        s.set_color("#2a323d")
    ax.grid(True, **_GRID)


def render_structure(nest, parent, radii, meta: dict, out_path: str) -> None:
    """Draw the plate. ``nest``/``parent`` are azimuthal_structure() outputs
    (parent may be None), ``radii`` a quadrant_radii() output (may be None),
    ``meta`` carries model/storm/init/valid/fxx/hemi_sign labels."""
    hemi = meta.get("hemi_sign", 1.0)          # sign(cen_lat): cyclonic flip
    fig = plt.figure(figsize=(hp.IMAGE_W_PX / hp.DPI, hp.IMAGE_H_PX / hp.DPI),
                     facecolor=hp.BAND_BG)
    gs = fig.add_gridspec(2, 2, left=0.065, right=0.97, top=0.865, bottom=0.075,
                          hspace=0.33, wspace=0.24)
    axA = fig.add_subplot(gs[0, 0])
    axB = fig.add_subplot(gs[0, 1])
    axC = fig.add_subplot(gs[1, 0])
    axD = fig.add_subplot(gs[1, 1], projection="polar")

    r = nest["r_km"]

    # --- A: tangential wind ---
    _style_axes(axA, "AZIMUTHAL-MEAN CYCLONIC TANGENTIAL WIND (kt)")
    for lvl, sty in _LEVEL_STYLE.items():
        if lvl in nest["vt_kt"]:
            axA.plot(r, hemi * nest["vt_kt"][lvl], label=f"{lvl} nest", **sty)
    if parent is not None and "10m" in parent["vt_kt"]:
        axA.plot(parent["r_km"], hemi * parent["vt_kt"]["10m"],
                 label="10m parent (6 km)", **_PARENT_STYLE)
    axA.axvline(nest["rmw_km"], color="#ffffff", lw=0.8, alpha=0.5)
    axA.set_xlabel("radius (km)", color=_MUTED, fontsize=9)
    axA.set_xlim(0, st.PROFILE_MAX_KM)
    axA.legend(loc="upper right", fontsize=7.5, facecolor="#11161f",
               edgecolor="#2a323d", labelcolor=_FG)
    cav = ""
    if parent is not None:
        cav = (f"  ·  RESOLUTION: nest {nest['vt_max_kt']:.0f} kt vs "
               f"parent {parent['vt_max_kt']:.0f} kt peak")
    axA.text(0.02, 0.02,
             f"RMW {nest['rmw_km']:.0f} km · peak {nest['vt_max_kt']:.0f} kt{cav}",
             transform=axA.transAxes, color=_FG, fontsize=8.5)

    # --- B: radial wind ---
    _style_axes(axB, "AZIMUTHAL-MEAN RADIAL WIND (kt · outward +, inflow −)")
    for lvl, sty in _LEVEL_STYLE.items():
        if lvl in nest["vr_kt"]:
            axB.plot(r, nest["vr_kt"][lvl], label=lvl, **sty)
    axB.axhline(0, color=_MUTED, lw=0.8)
    axB.set_xlabel("radius (km)", color=_MUTED, fontsize=9)
    axB.set_xlim(0, st.PROFILE_MAX_KM)

    # --- C: warm core ---
    _style_axes(axC, "WARM CORE · 850–500 hPa MEAN-LAYER T ANOMALY (°C)")
    if nest.get("t_anom_c") is not None:
        axC.plot(r, nest["t_anom_c"], color="#f0a441", lw=2.0)
        axC.axhline(0, color=_MUTED, lw=0.8)
        tmax = float(np.nanmax(nest["t_anom_c"]))
        axC.text(0.02, 0.9, f"peak +{tmax:.1f} °C",
                 transform=axC.transAxes, color=_FG, fontsize=8.5)
    else:
        axC.text(0.5, 0.5, "thickness fields unavailable", color=_MUTED,
                 ha="center", transform=axC.transAxes, fontsize=10)
    axC.set_xlabel("radius (km)", color=_MUTED, fontsize=9)
    axC.set_xlim(0, st.PROFILE_MAX_KM)
    axC.text(0.02, 0.02, "derived hydrostatically from 850–500 thickness "
             "(no T levels in cache)", transform=axC.transAxes,
             color=_MUTED, fontsize=7.5)

    # --- D: quadrant radii rose ---
    axD.set_facecolor("#0d1117")
    axD.set_title("WIND RADII · QUADRANT MAX (ATCF convention, n mi)",
                  color=_FG, fontsize=11, fontweight="bold", pad=14)
    axD.set_theta_zero_location("N")
    axD.set_theta_direction(-1)                 # compass sense
    axD.tick_params(colors=_MUTED, labelsize=7)
    axD.grid(True, color="#2a323d", lw=0.6)
    if radii is not None:
        # quadrant centres: NE=45, SE=135, SW=225, NW=315 (compass degrees)
        theta = np.radians([45, 135, 225, 315])
        width = np.radians(88)
        colors = {"r34": "#4193f0", "r50": "#f0a441", "r64": "#cb2c0d"}
        rmax = 1.0
        for thr in ("r34", "r50", "r64"):
            vals = radii.get(thr) or []
            for t, v in zip(theta, vals):
                if v:
                    axD.bar(t, v, width=width, bottom=0, alpha=0.35,
                            color=colors[thr], edgecolor=colors[thr], lw=1.2)
                    rmax = max(rmax, v)
        axD.set_ylim(0, rmax * 1.15)
        rows = []
        for thr in ("r34", "r50", "r64"):
            vals = ["-" if v is None else str(v) for v in (radii.get(thr) or ["-"] * 4)]
            rows.append(f"{thr.upper():>4}  " + "  ".join(f"{v:>4}" for v in vals))
        axD.text(-0.12, -0.13, "      NE    SE    SW    NW\n" + "\n".join(rows),
                 transform=axD.transAxes, color=_FG, fontsize=8.5,
                 family="monospace")
    else:
        axD.text(0.5, 0.5, "no threshold wind reached", color=_MUTED,
                 ha="center", transform=axD.transAxes, fontsize=10)

    # --- header band (house 3-line pattern) ---
    fig.text(0.065, 0.965, f"{meta['model_label']}  ·  {meta['storm_label']}"
             f"  ·  Azimuthal-Mean Structure", color="#ffffff",
             fontsize=15, fontweight="bold")
    fig.text(0.065, 0.936, f"INIT {meta['init']}   F{meta['fxx']:03d}   "
             f"VALID {meta['valid']}", color=_FG, fontsize=10.5)
    fig.text(0.065, 0.91, "azimuthal mean about the model's own vortex fix · "
             "warm core thickness-derived · radii = QUADRANT MAX "
             "(mean would not compare to the b-deck) · cyclonic shown "
             "positive in both hemispheres",
             color=_MUTED, fontsize=8.5)
    fig.text(0.97, 0.965, "triple-a-tropics.com", color=_MUTED, fontsize=9,
             ha="right")

    fig.savefig(out_path, dpi=hp.DPI, facecolor=hp.BAND_BG)
    plt.close(fig)
