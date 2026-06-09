"""Triple-A-Tropics · record-hatching overlay — ONE CANON, no fork.

The record-HIGH / record-LOW diagonal-hatch treatment was first written inline
in generate_sst_plots.py:_plot_anomaly (the OISST "anomaly + records" map). This
module EXTRACTS that exact treatment so every records map draws it identically:
the OISST/CRW SST records plot, the ARMOR3D TCHP records plot, and the future
CycloLab TCHP mount all import draw_records_overlay() — change the look here and
all of them follow.

Treatment (decided): opposing diagonals over a diverging anomaly fill, drawn
THIN enough that the data reads through, each contiguous record region thinly
outlined for clarity.
  record-HIGH -> forward "///"  near-black-red   (#2a0412)
  record-LOW  -> back    "\\\\" near-black-blue  (#05122e)
  outline     -> thin black contour around each region

The hatch stroke is driven by rcParams set PER CALL and restored in `finally`,
so this never leaks hatch state to other figures.
"""
import numpy as np
import matplotlib as mpl

# --- the canon (must match generate_sst_plots.py's records overlay) ---------
HATCH_LW = 0.55
HIGH_PATTERN, HIGH_COLOR = "///", "#2a0412"     # near-black red
LOW_PATTERN, LOW_COLOR = "\\\\", "#05122e"      # near-black blue
OUTLINE_COLOR, OUTLINE_LW, OUTLINE_ALPHA = "#000000", 0.6, 0.75
ZORDER = 1.8


def draw_records_overlay(ax, lon2, lat2, records_high=None, records_low=None,
                         *, hatch_lw=HATCH_LW, zorder=ZORDER):
    """Hatch + outline the record-high / record-low masks onto `ax`.

    lon2/lat2 are the meshgrid (same shape as each mask). records_high /
    records_low are boolean (or 0/1) arrays of that shape, or None. A mask
    with no True cells is skipped. rcParams hatch state is restored on exit.
    Returns the number of layers actually drawn (0, 1, or 2).
    """
    prev_lw = mpl.rcParams.get("hatch.linewidth", 1.0)
    prev_color = mpl.rcParams.get("hatch.color", "black")
    mpl.rcParams["hatch.linewidth"] = hatch_lw
    drawn = 0
    try:
        for mask, pattern, color in (
            (records_high, HIGH_PATTERN, HIGH_COLOR),
            (records_low, LOW_PATTERN, LOW_COLOR),
        ):
            if mask is None:
                continue
            mf = np.where(np.asarray(mask), 1.0, 0.0)
            if not (mf > 0.5).any():
                continue
            mpl.rcParams["hatch.color"] = color
            ax.contourf(lon2, lat2, mf, levels=[0.5, 1.5], colors="none",
                        hatches=[pattern], zorder=zorder)
            # thin outline around each contiguous record region (the
            # existing record-anom border style — reused, not reinvented)
            ax.contour(lon2, lat2, mf, levels=[0.5], colors=OUTLINE_COLOR,
                       linewidths=OUTLINE_LW, alpha=OUTLINE_ALPHA,
                       zorder=zorder + 0.1)
            drawn += 1
    finally:
        mpl.rcParams["hatch.linewidth"] = prev_lw
        mpl.rcParams["hatch.color"] = prev_color
    return drawn
