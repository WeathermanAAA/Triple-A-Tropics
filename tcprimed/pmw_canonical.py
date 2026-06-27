"""Canonical NRL passive-microwave Tb color tables (community-standard tc_pmw).

The four observed-MW products use the EXACT published NRL color tables, not
hand-tuned approximations:

  * 37H   - single-channel 37 GHz H-pol brightness temperature, K, on the
            canonical 37 GHz stepped colormap (warm low-level emission = red/black).
  * 91H   - single-channel high-frequency (85/89/91 GHz) H-pol brightness
            temperature, K, on the canonical high-freq colormap (cold scattering
            cores = black/red).
  * color37 - NRL 37 GHz true-color RGB from the 37 V/H pair.
  * color91 - NRL high-freq true-color RGB from the 89/91 V/H pair.

The two scalar colormaps are built with the canonical piecewise
linear-segmented construction (``_linear_segmented``), an exact port of the
reference colormap builder: each (start,end) value range maps to a (start,end)
color that is linearly interpolated within the segment, with a hard step wherever
two consecutive ranges are offset (e.g. 228 -> 228.1). This reproduces the
canonical table pixel-for-pixel.

These tables are the community standard; this module is the single source of
truth for both the renderer and the colorbar-exactness test.
"""
from __future__ import annotations

import numpy as np
from matplotlib.colors import ColorConverter, LinearSegmentedColormap, Normalize

# ---------------------------------------------------------------------------
# Canonical linear-segmented colormap builder (exact port of the reference).
# transition_vals : list of (start_val, end_val) tuples spanning [vmin, vmax].
# transition_colors : list of (start_color, end_color) tuples (named or hex).
# Within a segment the color interpolates start->end; a gap between one
# segment's end_val and the next's start_val produces a hard step.
# ---------------------------------------------------------------------------
def _linear_segmented(name, vmin, vmax, transition_vals, transition_colors):
    cc = ColorConverter()
    red, grn, blu = (), (), ()
    old_end = (0.0, 0.0, 0.0)
    start_color = end_color = None
    end_val = vmin
    for (start_val, end_val), (c0, c1) in zip(transition_vals, transition_colors):
        tp = (start_val - vmin) / float(vmax - vmin)
        start_color = cc.to_rgb(c0)
        end_color = cc.to_rgb(c1)
        red += ((tp, old_end[0], start_color[0]),)
        grn += ((tp, old_end[1], start_color[1]),)
        blu += ((tp, old_end[2], start_color[2]),)
        old_end = end_color
    tp = (end_val - vmin) / float(vmax - vmin)
    red += ((tp, old_end[0], start_color[0]),)
    grn += ((tp, old_end[1], start_color[1]),)
    blu += ((tp, old_end[2], start_color[2]),)
    # N=1024 samples the canonical piecewise table finely enough that the
    # discretization error stays < 1/255 within every segment (so the rendered
    # colorbar matches the continuous canonical interpolation to <=2/255), while
    # the offset value-ranges (e.g. 228 -> 228.1) still resolve as hard steps.
    return LinearSegmentedColormap(
        name, {"red": red, "green": grn, "blue": blu}, N=1024)


# ---- 37H: 37 GHz H-pol brightness temperature (K), range [125, 310] --------
_37H_VMIN, _37H_VMAX = 125.0, 310.0
_37H_VALS = [(125, 180), (180, 195), (195, 210), (210, 220), (220, 230),
             (230, 240), (240, 260), (260, 280), (280, 310)]
_37H_COLORS = [("lightyellow", "darkmagenta"), ("#80007F", "#0080FF"),
               ("#0080FF", "#3AB9FF"), ("#3AB9FF", "#7DFDFF"),
               ("#7DFDFF", "#80FF82"), ("#80FF82", "#FFFF80"),
               ("#FFFF80", "#FF8000"), ("#FF8000", "#800000"), ("silver", "black")]
_37H_TICKS = [125, 150, 180, 200, 220, 240, 260, 280, 300, 310]

# ---- 91H: high-freq (85/89/91 GHz) H-pol brightness temperature (K) --------
_91H_VMIN, _91H_VMAX = 105.0, 305.0
_91H_VALS = [(105, 180), (180, 212), (212, 228), (228.1, 254), (254.1, 280),
             (280, 305)]
_91H_COLORS = [("white", "black"), ("#A4641A", "#FC0603"),
               ("#F4CD03", "#F2F403"), ("#8CF303", "#0FB503"),
               ("#06DCFD", "#0708B5"), ("navy", "white")]
_91H_TICKS = [105, 150, 180, 212, 228, 254, 280, 305]


def cmap_37h() -> LinearSegmentedColormap:
    cm = _linear_segmented("pmw_37h", _37H_VMIN, _37H_VMAX, _37H_VALS, _37H_COLORS)
    cm.set_bad((0, 0, 0, 0))
    return cm


def cmap_91h() -> LinearSegmentedColormap:
    cm = _linear_segmented("pmw_91h", _91H_VMIN, _91H_VMAX, _91H_VALS, _91H_COLORS)
    cm.set_bad((0, 0, 0, 0))
    return cm


def norm_37h() -> Normalize:
    return Normalize(vmin=_37H_VMIN, vmax=_37H_VMAX)


def norm_91h() -> Normalize:
    return Normalize(vmin=_91H_VMIN, vmax=_91H_VMAX)


# ---------------------------------------------------------------------------
# RGB recipes (canonical NRL color37 / color89-91). gamma is 1.0 (a no-op).
# apply_data_range(x, lo, hi, inverse): crop to [lo,hi] then normalize to [0,1];
# inverse flips so a LOW input -> HIGH output. Implemented as the exact closed
# form: inverse -> clip((hi - x)/(hi-lo), 0, 1); else clip((x - lo)/(hi-lo),0,1).
# ---------------------------------------------------------------------------
def _drange(x, lo, hi, inverse=False):
    if inverse:
        return np.clip((hi - x) / (hi - lo), 0.0, 1.0)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def color37_rgb(v37, h37):
    """Canonical NRL color37 R,G,B (each 0..1) from 37 GHz V/H Tb in Kelvin."""
    red = _drange(2.181 * v37 - 1.181 * h37, 260.0, 280.0, inverse=True)
    grn = _drange(v37, 180.0, 300.0)
    blu = _drange(h37, 160.0, 300.0)
    return red, grn, blu


def color91_rgb(v89, h89):
    """Canonical NRL color89/91 R,G,B (each 0..1) from high-freq V/H Tb (K)."""
    red = _drange(1.818 * v89 - 0.818 * h89, 220.0, 310.0, inverse=True)
    grn = _drange(h89, 240.0, 300.0)
    blu = _drange(v89, 270.0, 290.0)
    return red, grn, blu


PRODUCTS = ("color37", "color91", "37H", "91H")
