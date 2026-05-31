"""Colormaps + enhancements for Triple-A-Tropics satellite rendering.

CANONICAL SHARED PALETTE MODULE (tat_palettes). This is the single source of
truth for every TAT color palette, imported by BOTH the satellite backend
(tat-satellite-render: render.py / app.py / floater_poller.py) and the main
repo's HAFS model pipeline. It lives in the main Pages repo under palette/ and
is pip-installed from there by each consumer, so a color edit here propagates to
every product from ONE edit. The palette definitions below were extracted
VERBATIM from tat-satellite-render/colormaps.py: identical anchors, breakpoints,
normalization, and names. Do NOT recolor here without intending to recolor every
consumer. Run ``python -m tat_palettes`` after any edit to prove zero drift.

REBUILD (2026-05-28) - temperature-domain architecture.

Every IR/WV enhancement is defined directly over a brightness-temperature
domain in DEGREES CELSIUS and ships with:
  - a matplotlib Colormap oriented cold->warm (cmap index 0 == coldest color),
  - the °C domain endpoints (vmin_c = coldest, vmax_c = warmest),
  - an explicit list of °C tick values for the right-side colorbar,
  - a human label and the set of channel "domains" it's valid for.

render.py plots the data in °C with the enhancement's cmap + a fresh
Normalize(vmin_c, vmax_c) and draws a labeled vertical colorbar on the right,
restyled to the TAT dark theme. Because the data is plotted in real
temperature units, the colorbar ticks are physical °C with no manual remap.

DEFAULT preset: rainbow_ir (the TAT signature look).

Presets:
  - rainbow_ir : TAT signature rainbow IR (TWC/TT-family but personalized) -
                 GRAYSCALE warm half (landmasses/coastlines/clear air visible),
                 cyan+teal accent at the cold-cloud edge, then green -> yellow
                 -> orange -> red -> maroon -> magenta cold sweep, white-hot
                 overshoot core. The site default.
  - dvorak_bd  : canonical Dvorak BD - stepped grayscale with the diagnostic
                 black (-63..-69) -> white (-69..-75) flip. Breakpoints per
                 Andrew's reference table (+9,-30,-41,-53,-63,-69,-75,-80,-85).
  - tat_neon   : Triple-A-Tropics neon - cyan/violet/magenta/gold on dark.
  - wv_tat     : dedicated water-vapor table (dry=amber, moist=teal/blue/white).
  - ir_gray    : standard IR grayscale (cold = white). Alias: "grayscale".
"""

import numpy as np
from matplotlib.colors import LinearSegmentedColormap, Normalize


# ---------------------------------------------------------------------------
# builder helper
# ---------------------------------------------------------------------------
def _seg_cmap(name, anchors):
    """Build a LinearSegmentedColormap from (temp_C, r, g, b) anchors.

    `anchors` MUST be sorted by increasing temperature (coldest first). The
    coldest anchor maps to cmap position 0.0, the warmest to 1.0, so a
    Normalize(vmin=coldest, vmax=warmest) lines real temperatures up with the
    right colors. Two anchors at the (near-)same temperature produce a hard
    step / flip; spread them apart for a ramp.
    """
    t_cold = anchors[0][0]
    t_warm = anchors[-1][0]
    span = t_warm - t_cold
    rs, gs, bs = [], [], []
    last_x = -1.0
    for t, r, g, b in anchors:
        x = (t - t_cold) / span
        if x <= last_x:
            x = last_x + 1e-6  # keep strictly increasing -> sharp edge
        last_x = x
        rs.append((x, r, r))
        gs.append((x, g, g))
        bs.append((x, b, b))
    return LinearSegmentedColormap(name, {"red": rs, "green": gs, "blue": bs}, N=1024)


# ---------------------------------------------------------------------------
# rainbow_ir - TAT signature (TWC-family, personalized). DEFAULT.
# ---------------------------------------------------------------------------
# cold -> warm. TT-family warm half: GRAYSCALE warmer than the cold-cloud edge
# (so landmasses/coastlines/clear air show, not flooded with blue); cyan/teal
# TAT accent at the cold-cloud edge; green/yellow/orange/red/maroon/magenta cold
# sweep; white-hot overshoot core. Domain -95 .. +40 C. (Warm tail changed from
# blue to gray 2026-05-28 per Andrew - copy TT so landmasses are visible.)
_RAINBOW_IR_ANCHORS = [
    (-95.0, 1.00, 1.00, 1.00),  # white-hot overshoot core
    (-90.0, 0.88, 0.25, 0.98),  # magenta
    (-84.0, 0.62, 0.06, 0.62),  # purple
    (-78.0, 0.42, 0.03, 0.22),  # maroon-purple
    (-72.0, 0.55, 0.02, 0.06),  # dark maroon
    (-66.0, 0.80, 0.05, 0.03),  # deep red
    (-58.0, 0.96, 0.16, 0.04),  # red
    (-50.0, 1.00, 0.45, 0.05),  # orange
    (-44.0, 1.00, 0.70, 0.08),  # amber
    (-38.0, 1.00, 0.93, 0.18),  # yellow
    (-32.0, 0.72, 0.92, 0.20),  # yellow-green
    (-26.0, 0.18, 0.80, 0.22),  # green
    (-20.0, 0.08, 0.66, 0.48),  # teal-green (TAT)
    (-14.0, 0.16, 0.72, 0.90),  # cyan accent (TAT) - cold-cloud edge
    # Warm half = GRAYSCALE (TT-style) so landmasses, coastlines, and clear
    # air read through instead of being flooded with color. Cold = lighter,
    # warmest land/sea = dark gray. Lets the cyan coastlines + terrain show.
    (-9.0,  0.85, 0.93, 0.97),  # cyan -> near-white transition
    (0.0,   0.80, 0.80, 0.80),  # light gray (low cloud)
    (12.0,  0.58, 0.58, 0.58),  # medium gray
    (25.0,  0.40, 0.40, 0.40),  # gray - clear ocean / land visible
    (40.0,  0.18, 0.18, 0.18),  # dark gray (warmest land/sea)
]

# ---------------------------------------------------------------------------
# dvorak - BD grayscale LUT (exact)
# ---------------------------------------------------------------------------
# Piecewise tb(Kelvin)->gray(0-255) BD enhancement, reproduced verbatim from a
# reference implementation. Kelvin thresholds -> °C (tb-273.15),
# gray bytes -> 0..1. Hard steps via duplicate temps; the two ramps are linear:
#     tb<193 (< -80.15)      -> 85   (0.333)
#     193-198 (-80.15..-75.15) -> 135 (0.529)
#     198-204 (-75.15..-69.15) -> 255 (white)
#     204-210 (-69.15..-63.15) -> 0   (black)   <<< B/W flip
#     210-220 (-63.15..-53.15) -> 160 (0.627)
#     220-232 (-53.15..-41.15) -> 110 (0.431)
#     232-243 (-41.15..-30.15) -> 60  (0.235)
#     243-282 (-30.15..+8.85)  -> ramp 202->109 (0.792->0.427)  cirrus
#     282-303 (+8.85..+29.85)  -> ramp 255->0   (1.0->0.0)      low cloud/warm
#                                  (note the hard jump up to white at +9)
#     > 303                    -> 0
# Domain -95 .. +40 C.
_DVORAK_BD_ANCHORS = [
    (-95.00, 0.33333, 0.33333, 0.33333),  # tb<193  -> 85
    (-80.15, 0.33333, 0.33333, 0.33333),
    (-80.15, 0.52941, 0.52941, 0.52941),  # 193-198 -> 135
    (-75.15, 0.52941, 0.52941, 0.52941),
    (-75.15, 1.00000, 1.00000, 1.00000),  # 198-204 -> 255 white
    (-69.15, 1.00000, 1.00000, 1.00000),
    (-69.15, 0.00000, 0.00000, 0.00000),  # 204-210 -> 0 black  <<< flip
    (-63.15, 0.00000, 0.00000, 0.00000),
    (-63.15, 0.62745, 0.62745, 0.62745),  # 210-220 -> 160
    (-53.15, 0.62745, 0.62745, 0.62745),
    (-53.15, 0.43137, 0.43137, 0.43137),  # 220-232 -> 110
    (-41.15, 0.43137, 0.43137, 0.43137),
    (-41.15, 0.23529, 0.23529, 0.23529),  # 232-243 -> 60
    (-30.15, 0.23529, 0.23529, 0.23529),
    (-30.15, 0.79216, 0.79216, 0.79216),  # 243-282 ramp 202->109 (cirrus)
    (8.85,   0.42745, 0.42745, 0.42745),
    (8.85,   1.00000, 1.00000, 1.00000),  # 282-303 ramp 255->0 (low cloud/warm)
    (29.85,  0.00000, 0.00000, 0.00000),
    (40.00,  0.00000, 0.00000, 0.00000),  # > 303 -> 0
]

# ---------------------------------------------------------------------------
# tat_neon - refined Triple-A-Tropics signature
# ---------------------------------------------------------------------------
_TAT_NEON_ANCHORS = [
    (-90.0,  1.00, 1.00, 1.00),  # white-hot overshoot core
    (-82.0,  1.00, 0.86, 0.42),  # gold
    (-74.0,  1.00, 0.42, 0.58),  # hot pink
    (-66.0,  0.96, 0.22, 0.86),  # magenta
    (-56.0,  0.55, 0.32, 0.96),  # violet
    (-44.0,  0.22, 0.86, 0.88),  # cyan
    (-32.0,  0.12, 0.58, 0.78),  # teal
    (-18.0,  0.12, 0.30, 0.52),  # blue
    (0.0,    0.08, 0.16, 0.30),  # cool navy
    (30.0,   0.03, 0.05, 0.09),  # warm -> near black
]

# ---------------------------------------------------------------------------
# wv_tat - "Water Vapor" (WV14 enhancement)
# ---------------------------------------------------------------------------
# EXACT colors AND temperatures pixel-sampled from the WV14 legend/colorbar in
# Andrew's reference (GOES-16 Ch09 WV). Legend spans 0..-90 °C: warm/dry
# orange -> red -> maroon -> black(~-21) -> grays -> near-white(-39) ->
# lavender -> blue(-60) -> teal(-69) -> green(-75) -> pale yellow(-90).
# Both RGB (bytes/255) and °C are exact. Domain vmin_c=-90, vmax_c=0.
_WV_TAT_ANCHORS = [
    ( -90.0, 0.973, 0.973, 0.902),  # pale yellow-white (coldest)
    ( -87.0, 0.945, 0.945, 0.792),
    ( -84.0, 0.851, 0.902, 0.612),  # yellow-green
    ( -81.0, 0.588, 0.902, 0.525),
    ( -78.0, 0.424, 0.871, 0.529),  # green
    ( -75.0, 0.290, 0.804, 0.596),
    ( -72.0, 0.161, 0.741, 0.659),  # teal-green
    ( -69.0, 0.071, 0.671, 0.694),  # teal
    ( -66.0, 0.067, 0.565, 0.667),
    ( -63.0, 0.063, 0.447, 0.631),  # blue
    ( -60.0, 0.059, 0.337, 0.604),  # dark blue
    ( -57.0, 0.220, 0.416, 0.686),
    ( -54.0, 0.396, 0.506, 0.776),
    ( -51.0, 0.565, 0.596, 0.867),  # periwinkle
    ( -48.0, 0.706, 0.706, 0.922),
    ( -45.0, 0.808, 0.808, 0.949),  # pale lavender
    ( -42.0, 0.925, 0.925, 0.980),
    ( -39.0, 0.965, 0.965, 0.965),  # near-white
    ( -36.0, 0.820, 0.820, 0.820),  # light gray
    ( -33.0, 0.678, 0.678, 0.678),
    ( -30.0, 0.553, 0.553, 0.553),  # gray
    ( -27.0, 0.408, 0.408, 0.408),
    ( -24.0, 0.282, 0.282, 0.282),  # dark gray
    ( -21.0, 0.137, 0.137, 0.137),  # near-black
    ( -18.0, 0.231, 0.118, 0.125),  # dark maroon
    ( -15.0, 0.435, 0.145, 0.169),  # maroon
    ( -12.0, 0.620, 0.173, 0.204),
    (  -9.0, 0.776, 0.259, 0.208),  # red
    (  -6.0, 0.847, 0.439, 0.153),  # orange-red
    (   0.0, 0.918, 0.624, 0.098),  # orange (warm/dry)
]

# standard IR grayscale: cold = white, warm = black.
_IR_GRAY_ANCHORS = [
    (-90.0, 1.00, 1.00, 1.00),
    (30.0,  0.00, 0.00, 0.00),
]

RAINBOW_IR_CMAP = _seg_cmap("rainbow_ir", _RAINBOW_IR_ANCHORS)
DVORAK_BD_CMAP = _seg_cmap("dvorak_bd", _DVORAK_BD_ANCHORS)
TAT_NEON_CMAP = _seg_cmap("tat_neon", _TAT_NEON_ANCHORS)
WV_TAT_CMAP = _seg_cmap("wv_tat", _WV_TAT_ANCHORS)
IR_GRAY_CMAP = _seg_cmap("ir_gray", _IR_GRAY_ANCHORS)


# ---------------------------------------------------------------------------
# colorbar tick sets (°C)
# ---------------------------------------------------------------------------
_RAINBOW_TICKS = [40, 30, 20, 10, 0, -10, -20, -30, -40, -50, -60, -70, -80, -90]
_IR_TICKS = [30, 20, 10, 0, -10, -20, -30, -40, -50, -60, -70, -80, -90]
# Dvorak BD: label the exact diagnostic breakpoints + a couple of warm refs.
_BD_TICKS = [30, 9, 0, -30, -41, -53, -63, -69, -75, -80, -85]
_WV_TICKS = [0, -10, -20, -30, -40, -50, -60, -70, -80, -90]

_CBAR_LABEL_BT = "Brightness Temperature (°C)"


# ---------------------------------------------------------------------------
# registry (insertion order = UI order; rainbow_ir first == default)
# ---------------------------------------------------------------------------
# domain: which channel kinds the preset is offered for in the UI.
#   "ir" -> longwave/clean/shortwave IR ;  "wv" -> water vapor channels
ENHANCEMENTS = {
    "rainbow_ir": {
        "label": "Rainbow IR", "cmap": RAINBOW_IR_CMAP,
        "vmin_c": -95.0, "vmax_c": 40.0, "ticks": _RAINBOW_TICKS,
        "domains": ("ir",), "kind": "ir", "cbar_label": _CBAR_LABEL_BT,
    },
    "dvorak": {
        "label": "Dvorak", "cmap": DVORAK_BD_CMAP,
        "vmin_c": -95.0, "vmax_c": 40.0, "ticks": _BD_TICKS,
        "domains": ("ir",), "kind": "ir", "cbar_label": _CBAR_LABEL_BT,
    },
    "tat_neon": {
        "label": "TAT Neon", "cmap": TAT_NEON_CMAP,
        "vmin_c": -90.0, "vmax_c": 30.0, "ticks": _IR_TICKS,
        "domains": ("ir",), "kind": "ir", "cbar_label": _CBAR_LABEL_BT,
    },
    "wv_tat": {
        "label": "Water Vapor", "cmap": WV_TAT_CMAP,
        "vmin_c": -90.0, "vmax_c": 0.0, "ticks": _WV_TICKS,
        "domains": ("wv",), "kind": "ir", "cbar_label": _CBAR_LABEL_BT,
    },
    "ir_gray": {
        "label": "Grayscale IR", "cmap": IR_GRAY_CMAP,
        "vmin_c": -90.0, "vmax_c": 30.0, "ticks": _IR_TICKS,
        "domains": ("ir", "wv"), "kind": "gray", "cbar_label": _CBAR_LABEL_BT,
    },
}

# Back-compat aliases (hidden from the UI): old names that legacy share-links
# / older callers may still send. "grayscale"->ir_gray, "dvorak_bd"->dvorak.
ENHANCEMENTS["grayscale"] = ENHANCEMENTS["ir_gray"]
ENHANCEMENTS["dvorak_bd"] = ENHANCEMENTS["dvorak"]
_ALIASES = ("grayscale", "dvorak_bd")

# Default preset (used by app.py + frontend).
DEFAULT_ENHANCEMENT = "rainbow_ir"


def get_enhancement(name: str):
    if name not in ENHANCEMENTS:
        raise ValueError(f"unknown enhancement {name!r}")
    return ENHANCEMENTS[name]


def enhancement_norm(name: str) -> Normalize:
    """Fresh Normalize over the preset's °C domain (per-call, not shared)."""
    e = get_enhancement(name)
    return Normalize(vmin=e["vmin_c"], vmax=e["vmax_c"])


def list_enhancements_for_domain(domain: str):
    """UI helper: enhancement keys valid for a channel domain ('ir'|'wv')."""
    return [k for k, e in ENHANCEMENTS.items()
            if k not in _ALIASES and domain in e["domains"]]


def normalize_visible(reflectance: np.ndarray) -> np.ndarray:
    """Sqrt-gamma stretch for visible reflectance (0..1)."""
    x = np.clip(reflectance, 0.0, 1.0)
    return np.sqrt(x)
