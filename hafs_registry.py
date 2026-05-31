#!/usr/bin/env python3
"""Typed product registry for the HAFS ``/models/`` plots - the ONE source of
product truth.

Every axis that used to differ by product inside ``hafs_plot.render_frame`` (the
fill field + cmap/norm, the fill method, the colorbar, the wind-barb overlay, the
coastline styling, and the header right-stat) is captured here as data + small
callables on a :class:`ProductSpec`. ``render_frame`` then dispatches off the
spec with no per-product-name ``if/elif`` chains, and ``generate_hafs_plots``
derives its ``PRODUCTS`` / ``DEFAULT_PRODUCTS`` / per-product GRIB parameter from
this same registry. Change a product's look or add a product in ONE place.

Scope: HAFS only for now. The spec fields are deliberately named in
model-neutral terms (``grib`` family, ``sat_parm``, ``field_attr``, color/fill/
colorbar/stat callables) so a future model (GFS, ECMWF, ...) could register its
own specs against the same schema - but only HAFS is wired today. A second model
would add its own ProductSpec entries (and the fetch/template plumbing in
``hafs_plot``); nothing here hardcodes a HAFS-only assumption into the schema
beyond the GRIB-family vocabulary, which is itself general.

Color source is the canonical shared ``tat_palettes`` package (no local copy),
exactly as the pre-refactor code used it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

import numpy as np
import matplotlib.cm as mcm

# Low-level render primitives stay in hafs_plot (used elsewhere there too); the
# registry references them. hafs_plot does NOT import this module at top level
# (render_frame imports it lazily), so there is no import cycle.
import hafs_plot as hp
import tat_palettes as tp


class FillMethod(Enum):
    """How the filled field is rasterized onto the map axes."""
    PCOLORMESH = "pcolormesh"               # smooth raster gradient (wind, BT)
    CONTOURF_DISCRETE = "contourf_discrete"  # contourf over a palette's step edges (refl)


@dataclass
class FillColors:
    """Resolved color state for one frame's fill, handed between spec callables.

    ``pal`` (the parsed .pal table) and ``enh`` (the tat_palettes enhancement
    dict) are the per-family extras the colorbar builders need; both stay None
    for families that don't use them.
    """
    cmap: object
    norm: object
    field: object
    pal: object = None
    enh: object = None


@dataclass(frozen=True)
class ProductSpec:
    """Everything that makes one rendered product differ from another.

    Identity feeds the manifest + frontend toggle; the data-source fields tell
    the fetcher which GRIB + channel to read and which HafsFrame attribute the
    fill comes from; the callables encapsulate the color/colorbar/stat variants
    so render_frame never branches on the product name.
    """
    # --- identity (manifest + frontend toggle) ---
    key: str            # internal + R2 path segment + manifest slug
    slug: str           # manifest slug (== key today; kept explicit for clarity)
    label: str          # long human label
    short: str          # segmented-toggle text on the frontend
    order: int          # position in the default product order (toggle order)

    # --- data source ---
    grib: str                       # HAFS GRIB family: "atm" or "sat"
    sat_parm: Optional[int]         # GRIB2 parameterNumber for .sat products, else None
    field_attr: str                 # HafsFrame attribute the fill reads (wind_kt/refl_dbz/bt_c)
    requires_attr: Optional[str]    # attribute that must be non-None before render (guard)

    # --- color ---
    default_enhancement: Optional[str]   # tat_palettes enhancement (BT only), else None
    channel: Optional[str]               # simulated-satellite channel label (BT only)
    make_colors: Callable                # (spec, frame, enh_name) -> FillColors

    # --- fill method ---
    fill_method: FillMethod

    # --- colorbar ---
    make_colorbar: Callable              # (fig, cax, cf, colors) -> colorbar

    # --- overlays ---
    draw_barbs: bool                     # wind barbs + wind-speed contour lines

    # --- coastline styling (border color == coast color for every product) ---
    coast_color: str
    coast_lw: float
    coast_halo: float

    # --- header right-stat / subtitle ---
    make_stat: Callable                  # (spec, frame, domain_label, vmax, pmin) -> (subtitle, right_stat)

    # --- color enhancement set (BT only; informational, not enforced) ---
    selectable_enhancements: tuple = ()

    def resolve_enhancement(self, override: Optional[str]) -> Optional[str]:
        """The enhancement name to color with: the caller override or the spec
        default (None for non-BT families)."""
        if self.default_enhancement is None:
            return None
        return override or self.default_enhancement

    def product_meta(self) -> dict:
        """The manifest/PRODUCTS dict shape ({slug, label, short})."""
        return {"slug": self.slug, "label": self.label, "short": self.short}


# ---------------------------------------------------------------------------
# Color factories (cmap + norm + field), one per GRIB/color family. These are
# the EXACT calls render_frame used to make inline.
# ---------------------------------------------------------------------------
def _wind_colors(spec: ProductSpec, frame, enh_name) -> FillColors:
    cmap, norm = hp._wind_cmap_norm()
    return FillColors(cmap=cmap, norm=norm, field=frame.wind_kt)


def _refl_colors(spec: ProductSpec, frame, enh_name) -> FillColors:
    pal = hp._refl_pal()
    return FillColors(cmap=pal.cmap, norm=pal.norm, field=frame.refl_dbz, pal=pal)


def _bt_colors(spec: ProductSpec, frame, enh_name) -> FillColors:
    enh = tp.get_enhancement(enh_name)
    # NaN (outside the nest) renders transparent; with_extremes returns a copy so
    # the shared registry cmap is never mutated.
    cmap = enh["cmap"].with_extremes(bad=(0.0, 0.0, 0.0, 0.0))
    norm = tp.enhancement_norm(enh_name)
    return FillColors(cmap=cmap, norm=norm, field=frame.bt_c, enh=enh)


# ---------------------------------------------------------------------------
# Colorbar builders. Each draws into the caller-provided cax and sets its label;
# the shared tick/outline restyling is applied by render_frame afterwards.
# ---------------------------------------------------------------------------
def _wind_colorbar(fig, cax, cf, colors: FillColors):
    cb = fig.colorbar(cf, cax=cax, extend="max", ticks=hp.CBAR_TICKS_KT)
    cb.set_label("10 m wind speed (kt)", color=hp.TEXT_COLOR, fontsize=10)
    return cb


def _refl_colorbar(fig, cax, cf, colors: FillColors):
    cb_cmap, cb_norm, cb_ticks = hp._refl_colorbar(colors.pal)
    sm = mcm.ScalarMappable(norm=cb_norm, cmap=cb_cmap)
    cb = fig.colorbar(sm, cax=cax, extend="max", ticks=cb_ticks)
    cb.set_label("Composite reflectivity (dBZ)", color=hp.TEXT_COLOR, fontsize=10)
    return cb


def _bt_colorbar(fig, cax, cf, colors: FillColors):
    enh = colors.enh
    cb = fig.colorbar(cf, cax=cax, ticks=enh["ticks"])
    cb.set_label(enh["cbar_label"], color=hp.TEXT_COLOR, fontsize=10)
    return cb


# ---------------------------------------------------------------------------
# Header right-stat + subtitle builders. The SSHWS VMAX pill and the MSLP
# minimum are shared (computed once in render_frame and passed in).
# ---------------------------------------------------------------------------
def _wind_stat(spec: ProductSpec, frame, domain_label, vmax, pmin):
    subtitle = f"10m Wind (kt) & MSLP (mb)  /  {domain_label}"
    right_stat = f"VMAX {vmax:.1f} kt   /   MSLP {pmin:.1f} mb"
    return subtitle, right_stat


def _refl_stat(spec: ProductSpec, frame, domain_label, vmax, pmin):
    rmax = (float(np.nanmax(frame.refl_dbz))
            if np.isfinite(frame.refl_dbz).any() else float("nan"))
    subtitle = f"Composite Reflectivity (dBZ) & MSLP (mb)  /  {domain_label}"
    right_stat = f"MAX {rmax:.0f} dBZ   /   MSLP {pmin:.1f} mb"
    return subtitle, right_stat


def _bt_stat(spec: ProductSpec, frame, domain_label, vmax, pmin):
    btmin = (float(np.nanmin(frame.bt_c))
             if np.isfinite(frame.bt_c).any() else float("nan"))
    subtitle = (f"Simulated Satellite - {spec.channel} & MSLP (mb)  /  "
                f"{domain_label}")
    right_stat = f"MIN BT {btmin:.1f}°C   /   MSLP {pmin:.1f} mb"
    return subtitle, right_stat


# ---------------------------------------------------------------------------
# The four HAFS product specs, in toggle order. mslp_wind stays first so the
# default frontend view is unchanged (Wind). Values are byte-for-byte the ones
# the pre-refactor render_frame / SAT_PRODUCTS used.
# ---------------------------------------------------------------------------
_SPECS = (
    ProductSpec(
        key="mslp_wind", slug="mslp_wind", label="MSLP + 10 m Wind",
        short="Wind", order=0,
        grib="atm", sat_parm=None, field_attr="wind_kt", requires_attr=None,
        default_enhancement=None, channel=None, make_colors=_wind_colors,
        fill_method=FillMethod.PCOLORMESH, make_colorbar=_wind_colorbar,
        draw_barbs=True,
        coast_color=hp.COAST_COLOR, coast_lw=1.2, coast_halo=0.0,
        make_stat=_wind_stat,
    ),
    ProductSpec(
        key="refl", slug="refl", label="Composite Reflectivity + MSLP",
        short="Reflectivity", order=1,
        grib="atm", sat_parm=None, field_attr="refl_dbz", requires_attr="refl_dbz",
        default_enhancement=None, channel=None, make_colors=_refl_colors,
        fill_method=FillMethod.CONTOURF_DISCRETE, make_colorbar=_refl_colorbar,
        draw_barbs=False,
        coast_color=hp.REFL_COAST_COLOR, coast_lw=1.3, coast_halo=0.0,
        make_stat=_refl_stat,
    ),
    ProductSpec(
        key="clean_ir", slug="clean_ir",
        label="Simulated Clean IR (10.3 um) + MSLP", short="Clean IR", order=2,
        grib="sat", sat_parm=58, field_attr="bt_c", requires_attr="bt_c",
        default_enhancement="rainbow_ir", channel="Clean IR (10.3 um)",
        make_colors=_bt_colors,
        fill_method=FillMethod.PCOLORMESH, make_colorbar=_bt_colorbar,
        draw_barbs=False,
        coast_color=hp.SAT_COAST_COLOR, coast_lw=1.1, coast_halo=1.6,
        make_stat=_bt_stat,
        selectable_enhancements=tuple(tp.list_enhancements_for_domain("ir")),
    ),
    ProductSpec(
        key="water_vapor", slug="water_vapor",
        label="Simulated Water Vapor (6.2 um) + MSLP", short="Water Vapor",
        order=3,
        grib="sat", sat_parm=53, field_attr="bt_c", requires_attr="bt_c",
        default_enhancement="wv_tat", channel="Water Vapor (6.2 um)",
        make_colors=_bt_colors,
        fill_method=FillMethod.PCOLORMESH, make_colorbar=_bt_colorbar,
        draw_barbs=False,
        coast_color=hp.SAT_COAST_COLOR, coast_lw=1.1, coast_halo=1.6,
        make_stat=_bt_stat,
        selectable_enhancements=tuple(tp.list_enhancements_for_domain("wv")),
    ),
)

# key -> spec, preserving order.
REGISTRY = {s.key: s for s in _SPECS}


# ---------------------------------------------------------------------------
# Accessors (the public surface other modules use).
# ---------------------------------------------------------------------------
def get_spec(key: str) -> ProductSpec:
    """The ProductSpec for a product key; raises KeyError if unknown."""
    return REGISTRY[key]

def has_spec(key: str) -> bool:
    return key in REGISTRY

def ordered_specs() -> list:
    """Specs in default toggle order."""
    return sorted(REGISTRY.values(), key=lambda s: s.order)

def default_order() -> list:
    """Product keys in default toggle/render order (mslp_wind first)."""
    return [s.key for s in ordered_specs()]

def products_dict() -> dict:
    """``{key: {slug,label,short}}`` in order - the generator's PRODUCTS table."""
    return {s.key: s.product_meta() for s in ordered_specs()}

def sat_parm(key: str) -> Optional[int]:
    """GRIB2 parameterNumber for a product's .sat channel, or None for .atm
    products / unknown keys."""
    s = REGISTRY.get(key)
    return s.sat_parm if s else None
