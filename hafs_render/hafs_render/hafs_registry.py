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
import matplotlib.colors as mcolors

# Low-level render primitives stay in hafs_plot (used elsewhere there too); the
# registry references them. hafs_plot does NOT import this module at top level
# (render_frame imports it lazily), so there is no import cycle.
from hafs_render import hafs_plot as hp
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
class LineContourSpec:
    """A generic labeled line-contour overlay of a cached scalar field.

    NOT height-specific: ``source(frame)`` returns the 2-D field ALREADY in the
    display units to contour (the height products scale gh gpm -> dam), and the
    lines are drawn at multiples of ``interval`` that fall inside the frame's
    range. Styled thin with a halo for legibility over colorful or dark fills,
    labeled with ``label_fmt``. render_frame draws it when a spec carries one.
    """
    source: Callable                 # (frame) -> 2-D array in display units
    interval: float                  # contour spacing, display units
    color: str = "#000000"
    linewidth: float = 0.8
    alpha: float = 0.9
    label_fmt: str = "%d"
    halo: float = 1.6                # halo width added to linewidth (0 = none)
    halo_color: str = "#ffffff"


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
    draw_barbs: bool                     # draw wind barbs (source = wind_provider)

    # --- coastline styling (border color == coast color for every product) ---
    coast_color: str
    coast_lw: float
    coast_halo: float

    # --- header right-stat / subtitle ---
    make_stat: Callable                  # (spec, frame, domain_label, vmax, pmin, scope) -> (subtitle, right_stat)

    # --- color enhancement set (BT only; informational, not enforced) ---
    selectable_enhancements: tuple = ()

    # --- overlay: thin black UNLABELED contour lines of the FILL field ---
    # Drawn on top of the fill in the SAME style as the wind product's category
    # contours, SEPARATE from the white MSLP isobars. Each value is a level in the
    # fill field's units; only those inside a frame's range are drawn. Empty for
    # every product except PWAT (moisture contours), and the render path guards on
    # it, so the lines stay confined to the spec that sets them.
    field_contour_levels: tuple = ()

    # --- overlay: thin black Saffir-Simpson wind-speed CATEGORY contours ---
    # The wind product's signature isotach lines (CBAR_TICKS_KT thresholds of the
    # wind-speed field). Decoupled from draw_barbs so a product can carry barbs
    # WITHOUT these (the vorticity / RH products do). Default off; the wind and
    # upper-air height-wind products set it True.
    draw_wind_contours: bool = False

    # --- overlay: surface MSLP, split into two independently-gated layers ---
    # (a) the white isobar CONTOUR LINES, and (b) the bold L center MARKER at the
    # pressure minimum + its value label. Both default ON (surface-pressure
    # context). The upper-air height / vorticity products turn the ISOBARS off
    # (they draw height line-contours instead) but keep the MARKER on, matching
    # the reference "...& MSLP Centers (hPa)" plots; the RH and surface products
    # keep both.
    draw_mslp_isobars: bool = True
    draw_mslp_markers: bool = True

    # --- wind source for barbs + category contours ---
    # Returns ``(u_kt, v_kt, speed_kt)`` for a frame. None -> the 10 m fields
    # (frame.u_kt / v_kt / wind_kt), unchanged for the wind product. Upper-air
    # products set a provider that reads a pressure level / layer-mean from
    # frame.upper and converts m/s -> kt.
    wind_provider: Optional[Callable] = None

    # --- overlay: a generic labeled line-contour of a cached scalar ---
    # A LineContourSpec (below) or None. NOT height-specific: the height products
    # set one that reads gh in decameters, but any product could contour any
    # field. render_frame draws it (labeled, thin) when present.
    line_contour: Optional[object] = None

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


def _pwat_colors(spec: ProductSpec, frame, enh_name) -> FillColors:
    # tat_pwat smooth fill over 0..90 mm; masked NaN renders transparent (the
    # cmap's set_bad). The cmap is the shared canonical singleton - never mutated.
    return FillColors(cmap=tp.TAT_PWAT_CMAP, norm=tp.pwat_norm(),
                      field=frame.pwat)


def _bt_colors(spec: ProductSpec, frame, enh_name) -> FillColors:
    enh = tp.get_enhancement(enh_name)
    # NaN (outside the nest) renders transparent; with_extremes returns a copy so
    # the shared registry cmap is never mutated.
    cmap = enh["cmap"].with_extremes(bad=(0.0, 0.0, 0.0, 0.0))
    norm = tp.enhancement_norm(enh_name)
    return FillColors(cmap=cmap, norm=norm, field=frame.bt_c, enh=enh)


# ---------------------------------------------------------------------------
# Upper-air (pressure-level) helpers + color/colorbar/stat factories. The fields
# live in frame.upper (Phase 2 cache); the render guard (requires_attr="upper")
# ensures it is present before any of these run.
# ---------------------------------------------------------------------------
def _level_wind_kt(frame, lev):
    """(u, v, speed) in KNOTS at a pressure level, from the m/s cache fields."""
    u = frame.upper[f"u_{lev}"] * hp.KT_PER_MS
    v = frame.upper[f"v_{lev}"] * hp.KT_PER_MS
    return u, v, np.hypot(u, v)


def _layer_wind_kt(frame):
    """(u, v, speed) in KNOTS for the 700-300 mb layer-mean wind (RH product)."""
    u = frame.upper["ulayer_700_300"] * hp.KT_PER_MS
    v = frame.upper["vlayer_700_300"] * hp.KT_PER_MS
    return u, v, np.hypot(u, v)


def _level_wind_provider(lev):
    return lambda frame: _level_wind_kt(frame, lev)


def _height_dam(lev):
    """LineContourSpec source: geopotential height at ``lev`` in DECAMETERS."""
    return lambda frame: frame.upper[f"gh_{lev}"] / 10.0


def _hgt_wind_colors(lev):
    """Fill = wind SPEED (kt) at ``lev`` with the EXISTING tat_wind palette."""
    def f(spec, frame, enh_name) -> FillColors:
        cmap, norm = hp._wind_cmap_norm()
        _, _, spd = _level_wind_kt(frame, lev)
        return FillColors(cmap=cmap, norm=norm, field=spd)
    return f


def _vort_colors(lev, vmax):
    """Fill = cyclonic relative vorticity at ``lev`` in 1e-5 s^-1, tat_cyclonic_vort
    over Normalize(0, vmax). Calm/anticyclonic air (< VORT_MASK_BELOW) is masked to
    NaN so the dark map shows through."""
    def f(spec, frame, enh_name) -> FillColors:
        field = frame.upper[f"relvort_{lev}"] * 1e5      # 1/s -> 1e-5/s
        field = np.where(field < tp.VORT_MASK_BELOW, np.nan, field)
        return FillColors(cmap=tp.TAT_CYCLONIC_VORT_CMAP,
                          norm=mcolors.Normalize(0.0, vmax), field=field)
    return f


def _rh_colors(spec: ProductSpec, frame, enh_name) -> FillColors:
    return FillColors(cmap=tp.TAT_RH_CMAP, norm=mcolors.Normalize(0.0, 100.0),
                      field=frame.upper["rh_layer_700_300"])


def _hgt_wind_colorbar(fig, cax, cf, colors: FillColors):
    # Same knots palette/ticks as the 10 m wind bar, but an honest label (the
    # fill is pressure-level wind, not 10 m). mslp_wind keeps _wind_colorbar.
    cb = fig.colorbar(cf, cax=cax, extend="max", ticks=hp.CBAR_TICKS_KT)
    cb.set_label("Wind speed (kt)", color=hp.TEXT_COLOR, fontsize=10)
    return cb


def _vort_colorbar(fig, cax, cf, colors: FillColors):
    vmax = float(colors.norm.vmax)
    step = 50 if vmax >= 300 else 30          # 0..300 by 50, 0..150 by 30
    ticks = list(range(0, int(vmax) + 1, step))
    cb = fig.colorbar(cf, cax=cax, extend="max", ticks=ticks)
    cb.set_label("Cyclonic vorticity (10^-5 /s)", color=hp.TEXT_COLOR, fontsize=10)
    return cb


def _rh_colorbar(fig, cax, cf, colors: FillColors):
    cb = fig.colorbar(cf, cax=cax, ticks=tp.RH_TICKS)
    cb.set_label("Relative humidity (%)", color=hp.TEXT_COLOR, fontsize=10)
    return cb


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


def _pwat_colorbar(fig, cax, cf, colors: FillColors):
    cb = fig.colorbar(cf, cax=cax, extend="max", ticks=tp.PWAT_TICKS_MM)
    cb.set_label("Precipitable Water (mm)", color=hp.TEXT_COLOR, fontsize=10)
    return cb


# ---------------------------------------------------------------------------
# Header right-stat + subtitle builders. The SSHWS VMAX pill and the MSLP
# minimum are shared (computed once in render_frame and passed in). Every
# per-product extremum reduces over the STORM-ANCHORED ``scope`` (cells within
# hp.STAT_RADIUS_DEG of the namesake's track fix; the whole domain only as the
# documented fallback), and appends ``scope.label`` - the "  (domain-wide)"
# honesty suffix an untracked parent carries - so a domain extremum is never
# silently passed off as the namesake's.
# ---------------------------------------------------------------------------
def _wind_stat(spec: ProductSpec, frame, domain_label, vmax, pmin, scope):
    subtitle = f"10m Wind (kt) & MSLP (mb)  /  {domain_label}"
    right_stat = f"VMAX {vmax:.1f} kt   /   MSLP {pmin:.1f} mb{scope.label}"
    return subtitle, right_stat


def _refl_stat(spec: ProductSpec, frame, domain_label, vmax, pmin, scope):
    rmax = hp.scope_max(frame.refl_dbz, scope)
    subtitle = f"Composite Reflectivity (dBZ) & MSLP (mb)  /  {domain_label}"
    right_stat = f"MAX {rmax:.0f} dBZ   /   MSLP {pmin:.1f} mb{scope.label}"
    return subtitle, right_stat


def _bt_stat(spec: ProductSpec, frame, domain_label, vmax, pmin, scope):
    # The STORM's coldest cloud top (scope-reduced), not the domain's - on the
    # parent the domain minimum is routinely unrelated land convection.
    btmin = hp.scope_min(frame.bt_c, scope)
    # Sat carries no MSLP overlay now, so the subtitle drops "& MSLP (mb)".
    # The right-stat keeps the MSLP {pmin} value as an informational readout.
    subtitle = f"Simulated Satellite - {spec.channel}  /  {domain_label}"
    right_stat = f"MIN BT {btmin:.1f}°C   /   MSLP {pmin:.1f} mb{scope.label}"
    return subtitle, right_stat


def _pwat_stat(spec: ProductSpec, frame, domain_label, vmax, pmin, scope):
    # Right stat is the storm's moisture peak (MAX PWAT in mm); MSLP stays.
    pwmax = hp.scope_max(frame.pwat, scope)
    subtitle = f"Precipitable Water (mm) & MSLP (mb)  /  {domain_label}"
    right_stat = f"MAX PWAT {pwmax:.0f} mm   /   MSLP {pmin:.1f} mb{scope.label}"
    return subtitle, right_stat


def _hgt_wind_stat(lev):
    def f(spec: ProductSpec, frame, domain_label, vmax, pmin, scope):
        _, _, spd = _level_wind_kt(frame, lev)
        smax = hp.scope_max(spd, scope)
        subtitle = f"{lev} mb Height & Wind  /  {domain_label}"
        right_stat = f"MAX WIND {smax:.0f} kt @{lev}{scope.label}"
        return subtitle, right_stat
    return f


def _vort_stat(lev):
    def f(spec: ProductSpec, frame, domain_label, vmax, pmin, scope):
        v5 = frame.upper[f"relvort_{lev}"] * 1e5     # 1e-5 /s
        vmax_v = hp.scope_max(v5, scope)
        subtitle = f"{lev} mb Cyclonic Vorticity & Wind  /  {domain_label}"
        right_stat = f"MAX VORT {vmax_v:.0f} x10^-5/s @{lev}{scope.label}"
        return subtitle, right_stat
    return f


def _rh_layer_stat(spec: ProductSpec, frame, domain_label, vmax, pmin, scope):
    rh = frame.upper["rh_layer_700_300"]
    rmean = hp.scope_mean(rh, scope)
    subtitle = f"700-300 mb Relative Humidity & Wind  /  {domain_label}"
    right_stat = f"MEAN RH {rmean:.0f}%{scope.label}"
    return subtitle, right_stat


# ---------------------------------------------------------------------------
# The HAFS product specs, in toggle order. mslp_wind stays first so the default
# frontend view is unchanged (Wind), and the four pre-existing products keep
# their original order; mslp_pwat is appended LAST so adding it perturbs neither
# the default view nor the existing toggle order. The first four products' values
# are byte-for-byte the ones the pre-refactor render_frame / SAT_PRODUCTS used.
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
        # The wind product's signature isotach lines (now decoupled from barbs).
        draw_wind_contours=True,
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
        # Simulated satellite gets NO MSLP overlay: no isobars, no L marker.
        # Contour-line overlays and the L are a package (both or neither) and
        # belong only to the wind / refl / PWAT / RH and height products; sat is
        # BT fill + black coasts only.
        draw_mslp_isobars=False, draw_mslp_markers=False,
        coast_color=hp.COAST_COLOR, coast_lw=1.1, coast_halo=0.0,
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
        # No MSLP overlay (see clean_ir): sat is BT fill + black coasts only.
        draw_mslp_isobars=False, draw_mslp_markers=False,
        coast_color=hp.COAST_COLOR, coast_lw=1.1, coast_halo=0.0,
        make_stat=_bt_stat,
        selectable_enhancements=tuple(tp.list_enhancements_for_domain("wv")),
    ),
    ProductSpec(
        key="mslp_pwat", slug="mslp_pwat", label="MSLP & PWAT",
        short="PWAT", order=4,
        grib="atm", sat_parm=None, field_attr="pwat", requires_attr="pwat",
        default_enhancement=None, channel=None, make_colors=_pwat_colors,
        fill_method=FillMethod.PCOLORMESH, make_colorbar=_pwat_colorbar,
        draw_barbs=False,
        # PWAT is an .atm-surface product: its MSLP isobars + bold L + pressure
        # label are drawn exactly like mslp_wind (no wind barbs), and it shares
        # mslp_wind's black coasts.
        coast_color=hp.COAST_COLOR, coast_lw=1.1, coast_halo=0.0,
        make_stat=_pwat_stat,
        # Thin black moisture contours every 10 mm from 50 (only those inside a
        # frame's PWAT range draw), same style as the wind category contours.
        field_contour_levels=(50, 60, 70, 80, 90),
    ),

    # --- Phase 2 upper-air products (orders 5..10, appended after the existing
    # five so defaults / existing toggle order are unchanged). All read
    # frame.upper (requires_attr="upper"). Height products draw height
    # line-contours INSTEAD of MSLP isobars (draw_mslp_isobars=False) and keep the
    # L center marker (draw_mslp_markers=True); the vorticity products also draw
    # height contours but drop the L (the vorticity max is the feature); the RH
    # product keeps MSLP isobars + L. Coast color is per-fill: black over the
    # colorful height and RH fills, neon-green over the dark vorticity fill. ---
    ProductSpec(
        key="hgt_wind_850", slug="hgt_wind_850", label="850 mb Height & Wind",
        short="850 H/Wind", order=5,
        grib="atm", sat_parm=None, field_attr="upper", requires_attr="upper",
        default_enhancement=None, channel=None, make_colors=_hgt_wind_colors(850),
        fill_method=FillMethod.PCOLORMESH, make_colorbar=_hgt_wind_colorbar,
        # Height contours replace the MSLP isobars, but the L center marker stays.
        draw_barbs=True, draw_wind_contours=True,
        draw_mslp_isobars=False, draw_mslp_markers=True,
        wind_provider=_level_wind_provider(850),
        line_contour=LineContourSpec(source=_height_dam(850), interval=3.0),
        # Same coast styling as mslp_wind (shared colorful wind fill -> black).
        coast_color=hp.COAST_COLOR, coast_lw=1.2, coast_halo=0.0,
        make_stat=_hgt_wind_stat(850),
    ),
    ProductSpec(
        key="hgt_wind_700", slug="hgt_wind_700", label="700 mb Height & Wind",
        short="700 H/Wind", order=6,
        grib="atm", sat_parm=None, field_attr="upper", requires_attr="upper",
        default_enhancement=None, channel=None, make_colors=_hgt_wind_colors(700),
        fill_method=FillMethod.PCOLORMESH, make_colorbar=_hgt_wind_colorbar,
        draw_barbs=True, draw_wind_contours=True,
        draw_mslp_isobars=False, draw_mslp_markers=True,
        wind_provider=_level_wind_provider(700),
        line_contour=LineContourSpec(source=_height_dam(700), interval=3.0),
        coast_color=hp.COAST_COLOR, coast_lw=1.2, coast_halo=0.0,
        make_stat=_hgt_wind_stat(700),
    ),
    ProductSpec(
        key="hgt_wind_500", slug="hgt_wind_500", label="500 mb Height & Wind",
        short="500 H/Wind", order=7,
        grib="atm", sat_parm=None, field_attr="upper", requires_attr="upper",
        default_enhancement=None, channel=None, make_colors=_hgt_wind_colors(500),
        fill_method=FillMethod.PCOLORMESH, make_colorbar=_hgt_wind_colorbar,
        draw_barbs=True, draw_wind_contours=True,
        draw_mslp_isobars=False, draw_mslp_markers=True,
        wind_provider=_level_wind_provider(500),
        # 500 mb heights vary more across the parent window, so a wider 6 dam
        # interval keeps the lines uncrowded (NWS standard); 850/700 use 3 dam.
        line_contour=LineContourSpec(source=_height_dam(500), interval=6.0),
        coast_color=hp.COAST_COLOR, coast_lw=1.2, coast_halo=0.0,
        make_stat=_hgt_wind_stat(500),
    ),
    ProductSpec(
        key="vort_wind_850", slug="vort_wind_850",
        label="850 mb Cyclonic Vorticity & Wind", short="850 Vort", order=8,
        grib="atm", sat_parm=None, field_attr="upper", requires_attr="upper",
        default_enhancement=None, channel=None,
        make_colors=_vort_colors(850, 300.0),
        fill_method=FillMethod.PCOLORMESH, make_colorbar=_vort_colorbar,
        # No overlays at all: no MSLP isobars, no L marker, and no height
        # contours. Contour lines and the L are a package and the vorticity
        # products carry neither; the vorticity maximum is the feature of
        # interest, read straight off the fill + barbs.
        draw_barbs=True, draw_wind_contours=False,
        draw_mslp_isobars=False, draw_mslp_markers=False,
        wind_provider=_level_wind_provider(850),
        # The vorticity fill is transparent where calm, so the dark map shows
        # through; neon-green coasts read over it exactly like the refl product.
        coast_color=hp.REFL_COAST_COLOR, coast_lw=1.3, coast_halo=0.0,
        make_stat=_vort_stat(850),
    ),
    ProductSpec(
        key="vort_wind_500", slug="vort_wind_500",
        label="500 mb Cyclonic Vorticity & Wind", short="500 Vort", order=9,
        grib="atm", sat_parm=None, field_attr="upper", requires_attr="upper",
        default_enhancement=None, channel=None,
        make_colors=_vort_colors(500, 150.0),
        fill_method=FillMethod.PCOLORMESH, make_colorbar=_vort_colorbar,
        # No overlays (as at 850 mb): no isobars, no L marker, no height contours.
        draw_barbs=True, draw_wind_contours=False,
        draw_mslp_isobars=False, draw_mslp_markers=False,
        wind_provider=_level_wind_provider(500),
        coast_color=hp.REFL_COAST_COLOR, coast_lw=1.3, coast_halo=0.0,
        make_stat=_vort_stat(500),
    ),
    ProductSpec(
        key="rh_layer", slug="rh_layer",
        label="700-300 mb Relative Humidity & Wind", short="Layer RH", order=10,
        grib="atm", sat_parm=None, field_attr="upper", requires_attr="upper",
        default_enhancement=None, channel=None, make_colors=_rh_colors,
        fill_method=FillMethod.PCOLORMESH, make_colorbar=_rh_colorbar,
        # MSLP isobars + L (the surface-pressure context, NOT height contours);
        # barbs from the 700-300 mb layer-mean wind; no category contours.
        draw_barbs=True, draw_wind_contours=False,
        draw_mslp_isobars=True, draw_mslp_markers=True,
        wind_provider=_layer_wind_kt,
        # Moisture-field sibling of mslp_pwat: black coasts, matching it and
        # mslp_wind.
        coast_color=hp.COAST_COLOR, coast_lw=1.1, coast_halo=0.0,
        make_stat=_rh_layer_stat,
        # Thin black RH contours every 10% from 50 (only those inside a frame's
        # layer-RH range draw), same style as the PWAT / wind category contours.
        field_contour_levels=(50, 60, 70, 80, 90),
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
