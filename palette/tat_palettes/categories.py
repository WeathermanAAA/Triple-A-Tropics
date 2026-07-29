#!/usr/bin/env python3
"""SSHWS storm-category palette - the SINGLE SOURCE OF TRUTH for every surface.

Why this module exists
----------------------
The satellite colortables were consolidated into ``tat_palettes`` long ago; the
CATEGORY colors never were, and they drifted badly. Before this module there
were at least five disagreeing definitions in the tree - the HAFS chip table (a
desaturated variant, TD ``#4d8bb0``), ``models/enscenters.js`` (the de-facto
house ramp), ``season_animation.js`` (its own coral/rose C3-C4), ``recon.js``
(a fourth ramp, whose comment called ``#f5333c`` "the canonical TAT red" when
nothing else in the tree used it), plus copies in the home map, the tracks
generators, the CycloLab shell, the records explorer and the active banner.
Every one of them rendered the same storm a different color.

So the seven colors live HERE, once, and every consumer - Python and JavaScript
and CSS, in this repo and in tsr - reads them from this module. Nothing
downstream may hold a literal category hex;
``tests/test_category_palette_ssot.py`` fails the build if one appears anywhere
outside this package and the two files generated from it.

Provenance of the values
------------------------
Color-picked by Andrew from his reference image (modal color per swatch,
anti-aliased text pixels excluded) and adopted VERBATIM on 2026-07-29. They are
data, not a starting point: do not re-derive, re-sample, or "harmonise" them.
A future recolor is an edit to ``CATEGORY_HEX`` and nothing else.

Dependency floor
----------------
Pure stdlib, on purpose. ``tat_palettes/__init__.py`` builds matplotlib
colormaps at import time, but the ACE / tracks / records / feed path runs on a
deliberately minimal ``pandas + numpy`` footprint and imports these colors
through ``ace_core``. Keeping this module free of third-party imports means the
category palette costs that path nothing and cannot break on a matplotlib skew.

Ink (the text color drawn ON a swatch) is defined here too, because it is a
property of the swatch rather than of any one consumer: the banner, the HAFS
header chip and the records chips all had their own idea of it. It is an
explicit table rather than a computed pick - for ``C3`` the white-vs-dark
contrast ratios come out 4.34 vs 4.28, a knife edge that would flip on a
rounding change and silently restyle every chip on the site. The self-test
(:func:`verify_contrast`) enforces a floor instead, so a future palette edit
that makes a swatch illegible fails loudly rather than shipping.
"""
from __future__ import annotations

# --- the seven colors -------------------------------------------------------
# Verbatim from Andrew's reference image (2026-07-29). See module docstring.
CATEGORY_HEX = {
    "TD": "#6eebf9",   # tropical depression - cyan
    "TS": "#9cf94d",   # tropical storm      - lime
    "C1": "#fdfc53",   # category 1          - yellow
    "C2": "#f1af3d",   # category 2          - amber
    "C3": "#e63222",   # category 3          - red
    "C4": "#e732f4",   # category 4          - magenta
    "C5": "#f6c5fb",   # category 5          - pale violet
}

# Weakest -> strongest. Iterate this rather than ``CATEGORY_HEX`` when order
# matters (legends, colorbars, step expressions): dict order is an
# implementation detail, this is a promise.
CATEGORY_ORDER = ("TD", "TS", "C1", "C2", "C3", "C4", "C5")

# --- thresholds (kt, 1-minute sustained) ------------------------------------
# UNCHANGED by the 2026-07-29 recolor - the bins are the published SSHWS ones:
#   <34 TD | 34-63 TS | 64-82 C1 | 83-95 C2 | 96-112 C3 | 113-136 C4 | >=137 C5
# Two forms because consumers genuinely need both: an inclusive lower bound
# ("this category starts at") and an inclusive upper bound ("kt <= this ->
# category"), the latter being what the site's ``[[33,"TD"],[63,"TS"],...]``
# lookup tables and maplibre step expressions are written against. They are
# derived from one another, so they cannot disagree.
CATEGORY_MIN_KT = {
    "TD": 0, "TS": 34, "C1": 64, "C2": 83, "C3": 96, "C4": 113, "C5": 137,
}
# Inclusive upper bound; C5 is open-ended (None).
CATEGORY_MAX_KT = {
    "TD": 33, "TS": 63, "C1": 82, "C2": 95, "C3": 112, "C4": 136, "C5": None,
}

# --- text / labels ----------------------------------------------------------
# Ink drawn ON the swatch. Not computed - see module docstring.
# C3 is the only genuinely dark swatch in the palette, so it is the only one
# that takes white; the 2026-07-29 colors put everything else - including C4,
# which used to be white-on-magenta and measures only 3.41:1 that way - on the
# house dark ink. That flip is forced by the colors, not a style preference.
CATEGORY_INK = {
    "TD": "#0a1324", "TS": "#0a1324", "C1": "#0a1324", "C2": "#0a1324",
    "C3": "#ffffff", "C4": "#0a1324", "C5": "#0a1324",
}
# Full label (active banner, records chips, tooltips).
CATEGORY_LABEL = {
    "TD": "Depression", "TS": "Tropical Storm", "C1": "Category 1",
    "C2": "Category 2", "C3": "Category 3", "C4": "Category 4",
    "C5": "Category 5",
}
# Single-character glyph letter (storm markers on the tracks/home maps).
CATEGORY_GLYPH = {
    "TD": "D", "TS": "S", "C1": "1", "C2": "2", "C3": "3", "C4": "4", "C5": "5",
}

# Fallback for a storm with no usable wind. TD is the weakest class, which is
# the honest reading of "we do not know" - and it is what ace_core.sshs_class
# has always returned, so this is not a behavior change.
UNKNOWN_CATEGORY = "TD"

__all__ = [
    "CATEGORY_HEX", "CATEGORY_ORDER", "CATEGORY_MIN_KT", "CATEGORY_MAX_KT",
    "CATEGORY_INK", "CATEGORY_LABEL", "CATEGORY_GLYPH", "UNKNOWN_CATEGORY",
    "category_for_kt", "color_for_kt", "ink_for_kt", "step_pairs",
    "relative_luminance", "contrast_ratio", "verify_contrast",
]


# ---------------------------------------------------------------------------
# lookups
# ---------------------------------------------------------------------------
def category_for_kt(kt) -> str:
    """Map a 1-minute sustained wind in kt to its SSHWS class code.

    ``None``/NaN -> :data:`UNKNOWN_CATEGORY`. Mirrors ``ace_core.sshs_class``
    for the wind-only case (ace_core additionally forces non-tropical NATUREs
    down to TD, which is an ACE-eligibility rule and stays there).
    """
    if kt is None:
        return UNKNOWN_CATEGORY
    try:
        kt = float(kt)
    except (TypeError, ValueError):
        return UNKNOWN_CATEGORY
    if kt != kt:                       # NaN
        return UNKNOWN_CATEGORY
    for cat in reversed(CATEGORY_ORDER):
        if kt >= CATEGORY_MIN_KT[cat]:
            return cat
    return UNKNOWN_CATEGORY


def color_for_kt(kt) -> str:
    """Swatch color for a wind speed (kt)."""
    return CATEGORY_HEX[category_for_kt(kt)]


def ink_for_kt(kt) -> str:
    """Text color to draw on the swatch for a wind speed (kt)."""
    return CATEGORY_INK[category_for_kt(kt)]


def step_pairs():
    """``[(min_kt, hex), ...]`` weakest-first, for step/threshold ramps.

    The shape maplibre ``["step", ...]`` expressions and canvas bin lookups
    want. The first pair is the ``TD`` default at 0 kt.
    """
    return [(CATEGORY_MIN_KT[c], CATEGORY_HEX[c]) for c in CATEGORY_ORDER]


# ---------------------------------------------------------------------------
# fine wind ramp (recon SFMR / flight-level barbs, ASCAT scatterometer)
# ---------------------------------------------------------------------------
# The obs products need more than seven steps: most recon and ASCAT winds sit
# below hurricane force, so a 7-color scale would flatten the whole 0-63 kt
# range the eye is actually reading. They used to solve that with a hand-tuned
# 15-bin ramp - duplicated VERBATIM between recon.js and ascat.js, plus a
# second hand-tuned "high contrast" copy of each, which is four more tables to
# drift. They are derived here instead.
#
# The rule: at every SSHWS threshold the bin is EXACTLY the category color, so
# the ramp still reads as Saffir-Simpson and agrees with every other surface.
# Bins between thresholds blend toward the next category, but only as far as
# BLEND_CEILING - stopping short keeps a visible step at each threshold, which
# is the whole point of a category scale. Blend all the way and 30 kt would be
# indistinguishable from a 34 kt tropical storm.
WIND_RAMP_EDGES_KT = (0, 10, 20, 30, 34, 40, 45, 50, 55, 60, 64, 83, 96,
                      113, 137)
BLEND_CEILING = 0.55


def _hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(v))):02x}" for v in rgb)


def _lerp_hex(a: str, b: str, t: float) -> str:
    ra, rb = _hex_to_rgb(a), _hex_to_rgb(b)
    return _rgb_to_hex(ra[i] + (rb[i] - ra[i]) * t for i in range(3))


def wind_ramp():
    """``[(min_kt, hex), ...]`` - the fine obs wind ramp, weakest-first.

    Category-exact at every threshold (see the note above); intermediate bins
    blend toward the next category by at most :data:`BLEND_CEILING`.
    """
    out = []
    for kt in WIND_RAMP_EDGES_KT:
        cat = category_for_kt(kt)
        idx = CATEGORY_ORDER.index(cat)
        base = CATEGORY_HEX[cat]
        if idx + 1 >= len(CATEGORY_ORDER):
            out.append((kt, base))
            continue
        nxt = CATEGORY_ORDER[idx + 1]
        span = CATEGORY_MIN_KT[nxt] - CATEGORY_MIN_KT[cat]
        t = (kt - CATEGORY_MIN_KT[cat]) / span * BLEND_CEILING if span else 0.0
        out.append((kt, _lerp_hex(base, CATEGORY_HEX[nxt], t)))
    return out


def verify_wind_ramp() -> int:
    """Assert the fine ramp is category-exact at every threshold. Returns count.

    The invariant that makes the ramp honest: a 64 kt ASCAT barb and a 64 kt
    storm dot on the tracks map must be the same color.
    """
    ramp = dict(wind_ramp())
    for cat in CATEGORY_ORDER:
        kt = CATEGORY_MIN_KT[cat]
        if kt not in ramp:
            raise AssertionError(f"{cat} threshold {kt} kt missing from ramp")
        if ramp[kt] != CATEGORY_HEX[cat]:
            raise AssertionError(
                f"{cat} at {kt} kt is {ramp[kt]}, not {CATEGORY_HEX[cat]}")
    return len(CATEGORY_ORDER)


# ---------------------------------------------------------------------------
# contrast (self-test support)
# ---------------------------------------------------------------------------
def _srgb_channel(v: float) -> float:
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    """WCAG 2.x relative luminance of an ``#rrggbb`` string."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return (0.2126 * _srgb_channel(r) + 0.7152 * _srgb_channel(g)
            + 0.0722 * _srgb_channel(b))


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.x contrast ratio between two ``#rrggbb`` strings (1.0 - 21.0)."""
    a, b = relative_luminance(fg), relative_luminance(bg)
    lo, hi = sorted((a, b))
    return (hi + 0.05) / (lo + 0.05)


def verify_contrast(minimum: float = 4.0) -> int:
    """Assert every swatch/ink pair clears ``minimum`` contrast. Returns count.

    The guard on a future recolor: swap a hex for something that makes its ink
    unreadable and this fails instead of shipping an illegible chip. 4.0 is a
    hair under WCAG AA (4.5) because these are large bold chips, not body text
    - and because the palette is fixed data we are matching, not tuning.
    """
    for cat in CATEGORY_ORDER:
        ratio = contrast_ratio(CATEGORY_INK[cat], CATEGORY_HEX[cat])
        if ratio < minimum:
            raise AssertionError(
                f"{cat}: ink {CATEGORY_INK[cat]} on {CATEGORY_HEX[cat]} "
                f"has contrast {ratio:.2f} < {minimum}")
    return len(CATEGORY_ORDER)


def verify_thresholds() -> int:
    """Assert the min/max threshold tables are consistent. Returns count.

    ``CATEGORY_MAX_KT[c]`` must be exactly one knot below the next category's
    minimum, and the categories must tile 0..inf with no gap or overlap.
    """
    for i, cat in enumerate(CATEGORY_ORDER[:-1]):
        nxt = CATEGORY_ORDER[i + 1]
        if CATEGORY_MAX_KT[cat] + 1 != CATEGORY_MIN_KT[nxt]:
            raise AssertionError(
                f"{cat} max {CATEGORY_MAX_KT[cat]} does not abut "
                f"{nxt} min {CATEGORY_MIN_KT[nxt]}")
    if CATEGORY_MIN_KT[CATEGORY_ORDER[0]] != 0:
        raise AssertionError("weakest category must start at 0 kt")
    if CATEGORY_MAX_KT[CATEGORY_ORDER[-1]] is not None:
        raise AssertionError("strongest category must be open-ended")
    return len(CATEGORY_ORDER)
