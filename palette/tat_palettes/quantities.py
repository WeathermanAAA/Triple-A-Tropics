#!/usr/bin/env python3
"""Palette registry keyed by PHYSICAL QUANTITY - not by product, not by model.

The problem this solves shows up at scale. Today one product renders one field
with one hand-tuned range, and that is fine because there is one model. At fifty
models the same physical quantity is rendered by dozens of products, and unless
every one of them uses the SAME fixed levels, a side-by-side comparison is
measuring the palette rather than the forecast: a 40 kt shear field on a 0-60
scale and the same 40 kt on a 0-100 scale are different colors, and the eye reads
that difference as a difference between the models. Per-product, per-model, or
auto-scaled ranges make cross-model comparison quietly invalid.

So the SCALE is owned here, once per quantity, globally fixed:

  * ``vmin`` / ``vmax`` - the value range the color ramp spans;
  * ``ticks`` - the colorbar ticks, so two renders of a quantity are annotated
    identically;
  * ``extend`` - which ends get an out-of-range arrow;
  * ``mask_below`` - the value under which cells render transparent;
  * ``step`` / ``contour_interval`` - the nominal level spacing.

Granularity rule: a quantity key is granular enough to pin ONE fixed scale.
Where the same physical quantity has genuinely different magnitudes at different
levels (850 vs 500 mb cyclonic vorticity, 200-850 vs 500-850 shear), those are
SEPARATE keys sharing a colormap - because the comparison that must stay valid
is "model A's 500 mb vorticity vs model B's 500 mb vorticity", not 850 vs 500.

Colormap ownership. Most ramps live in this package, so their entry carries a
``cmap_factory`` directly. Two do not: the wind ramp and the reflectivity table
are built by the renderer (``hafs_render.hafs_plot`` - the latter parsed from a
``.pal`` asset at render time). Rather than invert the dependency and drag the
renderer into this package, those entries declare ``cmap_owner`` and are bound
at the owner's import time via :func:`bind_cmap`. The registry still owns their
SCALE, which is the part that has to be global.

Colorbar LABELS are deliberately NOT owned here. Two products can render the
same quantity and honestly label it differently ("10 m wind speed (kt)" vs
"Wind speed (kt)" for a pressure-level fill), so the label stays with the
product while the numbers stay here.

This module imports nothing outside matplotlib/stdlib.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Optional

import matplotlib.colors as mcolors

from tat_palettes import (
    PWAT_TICKS_MM, PWAT_VMIN_MM, PWAT_VMAX_MM,
    RH_TICKS, SST_ACTUAL_VMIN_C, SST_ACTUAL_VMAX_C, VORT_MASK_BELOW,
    cyclonic_vort_cmap, era5_isotach_cmap, era5_z500_cmap, pwat_cmap,
    rh_cmap, sst_actual_cmap,
)


def _seg(name, hexes, *, under=None, over=None):
    """A linear ramp with NaN transparent - the shared env-ramp constructor.

    Byte-identical to ``hafs_registry._env_cmap``, which it replaces: these four
    ramps used to be defined privately inside the renderer, which is exactly the
    per-product ownership this registry exists to end.
    """
    cm = mcolors.LinearSegmentedColormap.from_list(name, hexes, N=256)
    cm.set_bad(alpha=0.0)                      # NaN / off-domain -> transparent
    if under is not None:
        cm.set_under(under)
    if over is not None:
        cm.set_over(over)
    return cm


# --- ramps that were previously private to the renderer --------------------
def env_precip_cmap():
    return _seg("tat_env_precip",
                ["#bfe3c9", "#7cc88e", "#3fa58f", "#2f80b0", "#2b5697",
                 "#5a3f9c", "#8a2f78", "#b23048"])


def env_cape_cmap():
    return _seg("tat_env_cape",
                ["#243a3a", "#3f6f5a", "#7fae5a", "#d9c14a", "#d98f43",
                 "#c0453a"], over="#9b2f28")


def env_srh_cmap():
    return _seg("tat_env_srh",
                ["#2b2f4a", "#3f5f8f", "#5fae9e", "#d9c14a", "#d9803a",
                 "#c0453a"], over="#9b2f28")


def env_lhtfl_cmap():
    return _seg("tat_env_lhtfl",
                ["#1f3b4a", "#2f7f8f", "#5fae8f", "#d9c14a", "#d98f43"],
                over="#c0453a")


@dataclass(frozen=True)
class QuantitySpec:
    """One physical quantity's globally-fixed color scale."""

    #: Canonical quantity id (snake_case, units-suffixed where ambiguous).
    key: str
    #: Human name of the QUANTITY (not of any product that renders it).
    label: str
    #: Display units, as they appear on a colorbar.
    units: str

    #: The fixed value range the ramp spans. None for a quantity that carries no
    #: single global range (brightness temperature, whose range is chosen by the
    #: selected enhancement; geopotential height, drawn as line contours).
    vmin: Optional[float] = None
    vmax: Optional[float] = None
    #: Colorbar ticks. Fixed so two renders annotate identically.
    ticks: tuple = ()
    #: Colorbar out-of-range arrows: "neither" | "min" | "max" | "both".
    extend: str = "neither"
    #: Cells strictly below this render transparent (None = no masking).
    mask_below: Optional[float] = None
    #: Nominal spacing between levels, for a value readout or a ruler.
    step: Optional[float] = None
    # PHYSICAL decode range for the value plane where the DISPLAY range is
    # per-enhancement (vmin/vmax None): the fixed bounds the 8-bit value
    # plane quantizes against, wide enough for every enhancement's span.
    value_range: tuple = None
    #: Line-contour spacing, for quantities drawn as labeled contours.
    contour_interval: Optional[float] = None

    #: Builds the colormap. None when another package owns it (see cmap_owner).
    cmap_factory: Optional[Callable] = None
    #: Package that owns the colormap when ``cmap_factory`` is None; that
    #: package calls :func:`bind_cmap` at import to complete the entry.
    cmap_owner: str = ""

    #: For a one-to-MANY quantity: the selectable enhancement names. Brightness
    #: temperature is the only such quantity - IR/WV/microwave each offer several
    #: enhancements, and each carries its OWN vmin/vmax/ticks (see
    #: ``tat_palettes.ENHANCEMENTS``), so the fixed-levels guarantee holds per
    #: enhancement rather than per quantity.
    enhancements: tuple = ()

    #: Why this scale is what it is - kept with the numbers so a future edit
    #: has to argue with the reason rather than just the value.
    note: str = ""

    def cmap(self):
        """The colormap for this quantity.

        Raises if an externally-owned entry was never bound - a silent None
        would surface as an unrelated matplotlib error deep in a render.
        """
        if self.cmap_factory is None:
            raise LookupError(
                f"quantity {self.key!r} has no colormap: it is owned by "
                f"{self.cmap_owner or 'an external package'}, which must call "
                f"tat_palettes.quantities.bind_cmap({self.key!r}, factory) at "
                f"import time")
        return self.cmap_factory()

    def norm(self):
        """The ``Normalize`` for this quantity's fixed range."""
        if self.vmin is None or self.vmax is None:
            raise LookupError(
                f"quantity {self.key!r} carries no global range (vmin/vmax are "
                f"None); its scale is per-enhancement or it is not a fill")
        return mcolors.Normalize(self.vmin, self.vmax)

    def value_plane(self) -> dict:
        """The manifest VALUE-PLANE block for this quantity.

        This is what lets the frontend turn a pixel's color back into a NUMBER
        (a value readout) without shipping the data: the range the ramp spans,
        the tick positions, the masking floor, and the nominal step.
        """
        return {
            "quantity": self.key,
            "label": self.label,
            "units": self.units,
            "vmin": self.vmin,
            "vmax": self.vmax,
            "ticks": list(self.ticks),
            "extend": self.extend,
            "mask_below": self.mask_below,
            "step": self.step,
            "value_range": (list(self.value_range)
                            if self.value_range else None),
            "enhancements": list(self.enhancements),
        }


# ---------------------------------------------------------------------------
# The quantity table. Every value here is the one the renderer used before this
# registry existed - the refactor moved ownership, it did not restyle anything
# (``tests/test_quantity_registry.py`` pins the resulting RGBA against a
# pre-refactor snapshot).
# ---------------------------------------------------------------------------
_QUANTITIES = (
    QuantitySpec(
        key="wind_speed_kt", label="Wind speed", units="kt",
        vmin=0.0, vmax=165.0,
        # The SSHWS category thresholds, so the bar doubles as a category key.
        ticks=(34, 64, 83, 96, 113, 137), extend="max", step=1.0,
        cmap_owner="hafs_render.hafs_plot",
        note="0-165 kt spans calm through the strongest observed TC; ticks are "
             "the Saffir-Simpson thresholds. Shared by the 10 m fill and every "
             "pressure-level wind fill so a given speed is one color everywhere.",
    ),
    QuantitySpec(
        key="reflectivity_dbz", label="Composite reflectivity", units="dBZ",
        vmin=10.0, vmax=100.0, extend="max", step=5.0,
        cmap_owner="hafs_render.hafs_plot",
        note="Discrete table parsed from the TAT-radar .pal asset at render "
             "time; the steps ARE the levels, so the scale cannot drift.",
    ),
    QuantitySpec(
        key="brightness_temperature_c", label="Brightness temperature",
        units="°C",
        # No single global range: each enhancement pins its own (rainbow_ir
        # -95..40, wv_tat -90..0, ice89h -168..15 degC == 105..288 K).
        enhancements=("rainbow_ir", "dvorak_bd", "tat_neon", "wv_tat",
                      "ir_gray", "ice89h"),
        # ice89h spans -168..15; -170..40 covers every enhancement at 1 degC.
        value_range=(-170.0, 40.0), step=1.0,
        note="One-to-MANY: the fixed-levels guarantee holds PER ENHANCEMENT, "
             "since an enhancement is precisely a choice of levels. Two models' "
             "simulated IR are comparable when both use the same enhancement.",
    ),
    QuantitySpec(
        key="precipitable_water_mm", label="Precipitable water", units="mm",
        vmin=PWAT_VMIN_MM, vmax=PWAT_VMAX_MM, ticks=tuple(PWAT_TICKS_MM),
        extend="max", step=10.0, cmap_factory=pwat_cmap,
        note="0-90 mm covers dry subtropical air through the deepest tropical "
             "moisture plumes.",
    ),
    QuantitySpec(
        key="geopotential_height_dam", label="Geopotential height", units="dam",
        # Line contours, not a fill: no ramp, only a spacing. 850/700 mb use 3
        # dam; 500 mb uses 6 dam (NWS standard) because heights vary more across
        # a synoptic window there and 3 dam would crowd the lines.
        contour_interval=3.0,
        note="Contour INTERVAL is the fixed quantity here. The 500 mb entry "
             "overrides it to 6 dam - see geopotential_height_500_dam.",
    ),
    QuantitySpec(
        key="geopotential_height_500_dam", label="500 mb geopotential height",
        units="dam", contour_interval=6.0,
        note="Wider interval than the lower levels: 500 mb heights span a much "
             "larger range across the parent window (NWS standard spacing).",
    ),
    QuantitySpec(
        key="cyclonic_vorticity_850_1e5", label="850 mb cyclonic vorticity",
        units="10^-5 /s", vmin=0.0, vmax=300.0,
        ticks=tuple(range(0, 301, 50)), extend="max", step=50.0,
        mask_below=VORT_MASK_BELOW, cmap_factory=cyclonic_vort_cmap,
        note="Low-level vorticity reaches far higher values than mid-level, so "
             "850 and 500 are separate keys - a shared scale would flatten one "
             "of them. Calm/anticyclonic air below the mask renders transparent.",
    ),
    QuantitySpec(
        key="cyclonic_vorticity_500_1e5", label="500 mb cyclonic vorticity",
        units="10^-5 /s", vmin=0.0, vmax=150.0,
        ticks=tuple(range(0, 151, 30)), extend="max", step=30.0,
        mask_below=VORT_MASK_BELOW, cmap_factory=cyclonic_vort_cmap,
        note="Half the 850 mb range; see that entry.",
    ),
    QuantitySpec(
        key="relative_humidity_pct", label="Relative humidity", units="%",
        vmin=0.0, vmax=100.0, ticks=tuple(RH_TICKS), extend="neither",
        step=10.0, cmap_factory=rh_cmap,
        note="Physically bounded 0-100, so the scale is the quantity's own "
             "range and no tuning is possible.",
    ),
    QuantitySpec(
        key="total_precip_in", label="Total precipitation", units="in",
        vmin=0.0, vmax=8.0, ticks=tuple(range(0, 9)), extend="max", step=1.0,
        mask_below=0.05, cmap_factory=env_precip_cmap,
        note="0-8 in over a forecast window; the 0.05 in floor keeps trace "
             "amounts from tinting the whole basin.",
    ),
    QuantitySpec(
        key="shear_200_850_kt", label="200-850 mb deep-layer shear", units="kt",
        vmin=0.0, vmax=80.0, ticks=(0, 20, 40, 60, 80), extend="max", step=20.0,
        cmap_factory=era5_isotach_cmap,
        note="Deep-layer shear runs stronger than mid-level, so it keeps its "
             "own 0-80 scale; a shared scale with 500-850 would compress both.",
    ),
    QuantitySpec(
        key="shear_500_850_kt", label="500-850 mb mid-level shear", units="kt",
        vmin=0.0, vmax=60.0, ticks=(0, 15, 30, 45, 60), extend="max", step=15.0,
        cmap_factory=era5_isotach_cmap,
        note="0-60: mid-level shear rarely reaches deep-layer magnitudes.",
    ),
    QuantitySpec(
        key="potential_vorticity_pvu", label="200 mb potential vorticity",
        units="PVU", vmin=0.0, vmax=10.0, ticks=(0, 2, 4, 6, 8, 10),
        extend="max", step=2.0, cmap_factory=cyclonic_vort_cmap,
        note="0-10 PVU brackets the dynamic tropopause (2 PVU) with room for "
             "stratospheric intrusions.",
    ),
    QuantitySpec(
        key="sst_c", label="Sea-surface temperature", units="°C",
        vmin=SST_ACTUAL_VMIN_C, vmax=SST_ACTUAL_VMAX_C,
        ticks=(0, 8, 16, 24, 32), extend="both", step=8.0,
        cmap_factory=sst_actual_cmap,
        note="Exactly the site SST palette's design range, so an SST here reads "
             "the same color as on the /sst/ maps - cross-PRODUCT comparability, "
             "the same principle one level up.",
    ),
    QuantitySpec(
        key="tropopause_temp_c", label="Tropopause temperature", units="°C",
        vmin=-85.0, vmax=-45.0, ticks=(-85, -75, -65, -55, -45), extend="both",
        step=10.0, cmap_factory=era5_z500_cmap,
        note="-85..-45 C covers the tropical tropopause through mid-latitude "
             "values.",
    ),
    QuantitySpec(
        key="cape_jkg", label="Surface CAPE", units="J/kg",
        vmin=0.0, vmax=4000.0, ticks=(0, 1000, 2000, 3000, 4000), extend="max",
        step=1000.0, mask_below=100.0, cmap_factory=env_cape_cmap,
        note="0-4000 J/kg; below 100 is not meaningfully unstable and is masked "
             "so the map reads through.",
    ),
    QuantitySpec(
        key="srh_03km_m2s2", label="0-3 km storm-relative helicity",
        units="m²/s²", vmin=0.0, vmax=500.0,
        ticks=(0, 100, 200, 300, 400, 500), extend="max", step=100.0,
        mask_below=25.0, cmap_factory=env_srh_cmap,
        note="0-500 m²/s²; the 25 floor drops ambient values.",
    ),
    QuantitySpec(
        key="latent_heat_flux_wm2", label="Latent heat flux", units="W/m²",
        vmin=0.0, vmax=400.0, ticks=(0, 100, 200, 300, 400), extend="both",
        step=100.0, cmap_factory=env_lhtfl_cmap,
        note="0-400 W/m² spans quiescent ocean through the strong air-sea "
             "exchange under a mature TC.",
    ),
)

#: key -> QuantitySpec, declaration-ordered.
QUANTITIES = {q.key: q for q in _QUANTITIES}


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------
def get_quantity(key: str) -> QuantitySpec:
    """The QuantitySpec for a key; raises KeyError if unknown."""
    return QUANTITIES[key]


def has_quantity(key: str) -> bool:
    return key in QUANTITIES


def ordered_quantities() -> list:
    return list(QUANTITIES.values())


def bind_cmap(key: str, factory: Callable) -> None:
    """Attach a colormap factory to an externally-owned quantity.

    Called at import time by the package named in ``cmap_owner`` (today
    ``hafs_render.hafs_plot``, for the wind ramp and the .pal reflectivity
    table). Idempotent, so a re-import cannot corrupt the entry; refuses to
    rebind an entry that already ships its own factory, which would let a
    consumer silently restyle a shared quantity.
    """
    q = QUANTITIES[key]
    if q.cmap_factory is not None and not q.cmap_owner:
        raise ValueError(
            f"quantity {key!r} owns its colormap in tat_palettes and must not "
            f"be rebound by a consumer")
    QUANTITIES[key] = replace(q, cmap_factory=factory)


def unbound_quantities() -> list:
    """Keys still awaiting a :func:`bind_cmap` call, excluding the entries that
    legitimately carry no ramp (brightness temperature, the height contours)."""
    return [k for k, q in QUANTITIES.items()
            if q.cmap_factory is None and q.vmin is not None]


def value_planes() -> dict:
    """``{key: value_plane}`` for every quantity - the manifest block."""
    return {k: q.value_plane() for k, q in QUANTITIES.items()}
