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
from hafs_render import model_registry as mr
import tat_palettes as tp
# The PHYSICAL-QUANTITY palette registry: the single owner of every
# rendered quantity's globally-fixed scale (vmin/vmax/ticks/mask).
from tat_palettes import quantities as tq


class FillMethod(Enum):
    """How the filled field is rasterized onto the map axes."""
    PCOLORMESH = "pcolormesh"               # smooth raster gradient (wind, BT)
    CONTOURF_DISCRETE = "contourf_discrete"  # contourf over a palette's step edges (refl)


class MeanSubstitute(Enum):
    """What to show INSTEAD of an ensemble mean, where the mean is denied.

    Every product that sets ``ensemble_mean_allowed=False`` must name one of
    these, so the denial is constructive: the frontend never has to fall back to
    "this is unavailable", it always has a defined better answer to offer.
    """

    #: The mean is allowed for this product; no substitute applies.
    NOT_APPLICABLE = "not_applicable"
    #: Draw every member's trace/contour, unaveraged (tracks, intensity traces,
    #: single-value-per-member quantities where the spread IS the message).
    SPAGHETTI = "spaghetti"
    #: Exceedance probability: P(field > threshold) across members. The right
    #: answer for extremes (precip maxima, CAPE, vorticity, PV intrusion) where
    #: the mean is diluted toward zero by member-to-member displacement.
    PROBABILITY = "probability"
    #: A named percentile field (10th/50th/90th). Preserves the field's own
    #: distribution shape instead of the cell-wise average.
    PERCENTILE = "percentile"
    #: Member min-max envelope plus the median - the spread as a band.
    ENVELOPE = "envelope"
    #: Force an explicit single-member choice. The only honest option for a
    #: simulated SENSOR field: averaging brightness temperature or reflectivity
    #: across members produces a radiance no instrument could ever observe.
    MEMBER_PICKER = "member_picker"


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

    # --- derived polarization-corrected channel (89 PCT) ---
    # (V_parm, H_parm): when set, the product's BT field is the POLARIZATION-
    # CORRECTED temperature PCT85 = 1.818*V - 0.818*H computed from these two .sat
    # channels (V warmer over clear ocean), instead of a single sat_parm channel.
    # The cycle then decodes BOTH parms; sat_parm stays None for such a product.
    sat_pct: Optional[tuple] = None

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

    # --- PARENT-domain SYNOPTIC environmental products -------------------------
    # ``synoptic_parent``: render the FULL parent extent (NOT the 40-deg storm-
    # centered crop), drop the L center marker AND the SSHWS category pill, and
    # reduce the header stats over the whole domain - these are broad
    # environmental maps, not storm-centered. The env products set it True.
    synoptic_parent: bool = False
    # ``streamline_provider``: (frame) -> (u, v) in display units for a light
    # streamline overlay (the deep-layer-shear products draw the shear VECTOR as
    # streamlines instead of barbs). None for every other product.
    streamline_provider: Optional[Callable] = None
    # ``domains``: restrict a product to these HAFS domains (raw names, e.g.
    # ("parent.atm",)). Empty = every domain (the storm-centered products). The
    # env products set ("parent.atm",) so they never render on the storm nest -
    # generate_hafs_plots gates the render jobs on this.
    domains: tuple = ()

    # --- PHYSICAL QUANTITY --------------------------------------------------
    # The key into tat_palettes.quantities - the single owner of this product's
    # color SCALE (vmin/vmax/ticks/mask/step). Two products that render the same
    # quantity name the same key and therefore cannot drift onto different
    # scales, which is what keeps a cross-model comparison a comparison of
    # forecasts rather than of palettes. It also supplies the manifest's
    # value-plane block, so the frontend can turn a pixel back into a number.
    # Empty only for a product whose fill has no registered quantity (none
    # today); ``_validate_specs`` rejects an unknown key.
    quantity: str = ""

    # --- STRUCTURAL model gate: requires convection-permitting physics --------
    # True for products whose signal IS resolved deep convection. On a model
    # that PARAMETERISES convection the field is not merely noisy, it is a
    # category error (see model_registry's docstring), so such a (model,
    # product) pair is dropped at render-job planning, refused with an exception
    # at the renderer, never written to the manifest, and never offered by the
    # frontend. Set on the reflectivity and simulated-microwave products only:
    # simulated IR / water-vapor brightness temperature is dominated by
    # grid-scale cloud and upper-tropospheric humidity and remains meaningful
    # (if smoother) under a cumulus scheme, so those two are NOT gated.
    requires_explicit_convection: bool = False

    # --- ENSEMBLE-MEAN policy -------------------------------------------------
    # ``ensemble_mean_allowed``: may a cell-wise mean across members be rendered
    # for this product? The flag governs the product's FILL FIELD - the quantity
    # the product is actually about. Two further rules ride on top of it in
    # ``ensemble_mean_policy`` so that no spec has to restate them:
    #
    #   (a) a STORM-FOLLOWING NEST denies the mean for EVERY product. Members'
    #       nests are centred on their own forecast positions, so the grids do
    #       not share coordinates and a cell-wise mean is not a well-defined
    #       operation - it averages different places.
    #   (b) the MSLP MINIMUM overlay (the bold L marker + its value label) is
    #       suppressed in ANY mean render, on every product that draws one. A
    #       minimum of an averaged pressure field is not a forecast minimum.
    #       This is why the height products can stay allowed despite drawing an
    #       L: their fill and their height contours are mean-safe, and the one
    #       part that is not is dropped rather than dragging the product down.
    #
    # Denied fills: TC intensity (10 m wind), precipitation MAXIMA, any
    # simulated SENSOR radiance, and the sharp displacement-sensitive extremum
    # fields (vorticity, PV, CAPE, SRH). Allowed fills: the smooth synoptic
    # mass / environment quantities - pressure-level wind and height, deep-layer
    # shear, PWAT, layer RH, SST, tropopause temperature, surface fluxes.
    #
    # The failure mode being prevented: averaging a displaced feature across
    # members yields a field NO member contains - two members with a 950 mb
    # centre 200 km apart average to a broad 985 mb trough, which is not a
    # forecast of anything. For an extremum the mean is biased toward the
    # climatological middle by construction; for a radiance it is unphysical.
    ensemble_mean_allowed: bool = False
    # What to offer instead where the mean is denied. MUST be NOT_APPLICABLE iff
    # ``ensemble_mean_allowed`` is True - ``_validate_specs`` enforces the
    # biconditional at import so the two fields can never silently disagree.
    mean_substitute: MeanSubstitute = MeanSubstitute.MEMBER_PICKER

    def resolve_enhancement(self, override: Optional[str]) -> Optional[str]:
        """The enhancement name to color with: the caller override or the spec
        default (None for non-BT families)."""
        if self.default_enhancement is None:
            return None
        return override or self.default_enhancement

    def product_meta(self) -> dict:
        """The manifest/PRODUCTS dict shape.

        ``{slug,label,short}`` stay FIRST and unchanged (the frontend reads only
        those three today); the rest is additive. ``quantity`` points at the
        manifest's ``quantities`` block, which carries the value plane - so the
        frontend resolves product -> quantity -> vmin/vmax/ticks/units without
        any per-product color knowledge in JS.
        """
        return {
            "slug": self.slug,
            "label": self.label,
            "short": self.short,
            "quantity": self.quantity,
            "ensemble_mean_allowed": self.ensemble_mean_allowed,
            "mean_substitute": self.mean_substitute.value,
            "requires_explicit_convection": self.requires_explicit_convection,
            "selectable_enhancements": list(self.selectable_enhancements),
            # "map" = a georeferenced raster (geometry affine, shear overlay
            # and value readout apply); "panel" = a diagnostic plate (they
            # must NOT - the frontend gates on this).
            "figure": "map",
        }


# ---------------------------------------------------------------------------
# Color factories (cmap + norm + field), one per GRIB/color family. These are
# the EXACT calls render_frame used to make inline.
# ---------------------------------------------------------------------------
def _wind_colors(spec: ProductSpec, frame, enh_name) -> FillColors:
    # 10 m wind on the SHARED wind scale - the same 0-165 kt ramp the
    # pressure-level wind fills use, so a given speed is one color everywhere.
    q = tq.get_quantity("wind_speed_kt")
    return FillColors(cmap=_q_cmap("wind_speed_kt"), norm=q.norm(),
                      field=frame.wind_kt)


def _refl_colors(spec: ProductSpec, frame, enh_name) -> FillColors:
    pal = hp._refl_pal()
    return FillColors(cmap=pal.cmap, norm=pal.norm, field=frame.refl_dbz, pal=pal)


def _pwat_colors(spec: ProductSpec, frame, enh_name) -> FillColors:
    # tat_pwat smooth fill on the registered PWAT scale; masked NaN renders
    # transparent (the cmap's set_bad). Never mutated.
    q = tq.get_quantity("precipitable_water_mm")
    return FillColors(cmap=_q_cmap("precipitable_water_mm"), norm=q.norm(),
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
    """Fill = wind SPEED (kt) at ``lev`` on the shared wind_speed_kt scale."""
    q = tq.get_quantity("wind_speed_kt")

    def f(spec, frame, enh_name) -> FillColors:
        _, _, spd = _level_wind_kt(frame, lev)
        return FillColors(cmap=_q_cmap("wind_speed_kt"), norm=q.norm(), field=spd)
    return f


def _vort_colors(lev, quantity):
    """Fill = cyclonic relative vorticity at ``lev`` in 1e-5 s^-1 on the scale
    registered for ``quantity``. 850 and 500 mb are SEPARATE quantities (low-level
    vorticity reaches far higher values), each with its own fixed range and its
    own ticks. Calm/anticyclonic air below the registered mask renders NaN so the
    dark map shows through."""
    q = tq.get_quantity(quantity)

    def f(spec, frame, enh_name) -> FillColors:
        field = frame.upper[f"relvort_{lev}"] * 1e5      # 1/s -> 1e-5/s
        field = np.where(field < q.mask_below, np.nan, field)
        return FillColors(cmap=_q_cmap(quantity), norm=q.norm(), field=field)
    return f


def _rh_colors(spec: ProductSpec, frame, enh_name) -> FillColors:
    q = tq.get_quantity("relative_humidity_pct")
    return FillColors(cmap=_q_cmap("relative_humidity_pct"), norm=q.norm(),
                      field=frame.upper["rh_layer_700_300"])


def _hgt_wind_colorbar(fig, cax, cf, colors: FillColors):
    # Same registered wind scale/ticks as the 10 m bar, but an honest label (the
    # fill is pressure-level wind, not 10 m). mslp_wind keeps _wind_colorbar.
    q = tq.get_quantity("wind_speed_kt")
    cb = fig.colorbar(cf, cax=cax, extend=q.extend, ticks=list(q.ticks))
    cb.set_label("Wind speed (kt)", color=hp.TEXT_COLOR, fontsize=10)
    return cb


def _vort_colorbar(quantity):
    """Ticks come from the quantity (0..300 by 50 at 850 mb, 0..150 by 30 at
    500 mb) instead of being re-derived from the norm's vmax at draw time."""
    q = tq.get_quantity(quantity)

    def f(fig, cax, cf, colors: FillColors):
        cb = fig.colorbar(cf, cax=cax, extend=q.extend, ticks=list(q.ticks))
        cb.set_label("Cyclonic vorticity (10^-5 /s)", color=hp.TEXT_COLOR,
                     fontsize=10)
        return cb
    return f


def _rh_colorbar(fig, cax, cf, colors: FillColors):
    q = tq.get_quantity("relative_humidity_pct")
    cb = fig.colorbar(cf, cax=cax, ticks=list(q.ticks))
    cb.set_label("Relative humidity (%)", color=hp.TEXT_COLOR, fontsize=10)
    return cb


# ---------------------------------------------------------------------------
# Colorbar builders. Each draws into the caller-provided cax and sets its label;
# the shared tick/outline restyling is applied by render_frame afterwards.
# ---------------------------------------------------------------------------
def _wind_colorbar(fig, cax, cf, colors: FillColors):
    q = tq.get_quantity("wind_speed_kt")
    cb = fig.colorbar(cf, cax=cax, extend=q.extend, ticks=list(q.ticks))
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
    # Units flag (default degC; "K" only for the 89 PCT / ice89h enhancement).
    # The norm is ALWAYS in degC, so for Kelvin we place the ticks at their degC
    # positions (K-273.15) but LABEL them in Kelvin -- the data/clip are untouched.
    if enh.get("units") == "K":
        cb = fig.colorbar(cf, cax=cax, ticks=[k - 273.15 for k in enh["ticks"]])
        cb.set_ticklabels([f"{k:g}" for k in enh["ticks"]])
    else:
        cb = fig.colorbar(cf, cax=cax, ticks=enh["ticks"])
    cb.set_label(enh["cbar_label"], color=hp.TEXT_COLOR, fontsize=10)
    return cb


def _bt_units(spec: ProductSpec) -> str:
    """Display units for a BT product's stat readout: degC unless its default
    enhancement carries units='K' (the 89 PCT). Single source = the enhancement,
    so the colorbar (enh-driven) and the stat (spec-driven) never disagree."""
    if spec.default_enhancement:
        return tp.get_enhancement(spec.default_enhancement).get("units", "C")
    return "C"


def _pwat_colorbar(fig, cax, cf, colors: FillColors):
    q = tq.get_quantity("precipitable_water_mm")
    cb = fig.colorbar(cf, cax=cax, extend=q.extend, ticks=list(q.ticks))
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
    btmin = hp.scope_min(frame.bt_c, scope)   # always computed on the RAW degC field
    # Sat carries no MSLP overlay now, so the subtitle drops "& MSLP (mb)".
    # The right-stat keeps the MSLP {pmin} value as an informational readout.
    # 89 PCT reads out in Kelvin (units='K'); all other BT products stay degC.
    subtitle = f"Simulated Satellite - {spec.channel}  /  {domain_label}"
    if _bt_units(spec) == "K":
        bt = f"MIN BT {btmin + 273.15:.1f} K"
    else:
        bt = f"MIN BT {btmin:.1f}°C"
    right_stat = f"{bt}   /   MSLP {pmin:.1f} mb{scope.label}"
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
# PARENT-domain ENVIRONMENTAL products (the synoptic family). Sober, one muted
# ramp per field, high contrast confined to the SUBJECT layer (the house style).
# Each fill reads frame.env[<key>]; the wind/streamline overlays read the cached
# env winds / shear vectors; the header stat reduces over the whole domain (these
# are broad synoptic maps, so scope is domain-wide with no honesty suffix).
# ---------------------------------------------------------------------------
# The four muted env ramps that used to be defined privately here now live in
# the shared quantity registry (tat_palettes.quantities) alongside the scale
# they belong to - per-product ownership of a shared quantity's colors is
# exactly what that registry exists to end. Colors are unchanged; only the
# owner moved. Ramps that were ALREADY shared (isotach / cyclonic-vort / SST /
# z500 rainbow) are reached through the same registry now rather than being
# re-derived here.
#
# Each colormap is built ONCE per quantity and memoized, because the pre-refactor
# module-level _CMAP_* constants were single instances and matplotlib artists
# compare them by identity in places.
_CMAP_CACHE: dict = {}


def _q_cmap(quantity: str):
    """The (memoized) colormap for a quantity key."""
    cm = _CMAP_CACHE.get(quantity)
    if cm is None:
        cm = _CMAP_CACHE[quantity] = tq.get_quantity(quantity).cmap()
    return cm


def _env_colors(key, quantity):
    """Fill = ``frame.env[key]`` on the fixed scale registered for ``quantity``.

    vmin/vmax and the transparency floor come from the quantity registry, NOT
    from this call site - that is the whole point: two products rendering the
    same quantity cannot drift onto different scales.
    """
    q = tq.get_quantity(quantity)

    def f(spec, frame, enh_name) -> FillColors:
        field = np.asarray(frame.env[key], dtype=float)
        if q.mask_below is not None:
            field = np.where(field < q.mask_below, np.nan, field)
        return FillColors(cmap=_q_cmap(quantity), norm=q.norm(), field=field)
    return f


def _env_colorbar(label, quantity):
    """Colorbar for an env product: ticks + extend from the quantity registry,
    LABEL from the product (two products may honestly word the same quantity
    differently)."""
    q = tq.get_quantity(quantity)

    def f(fig, cax, cf, colors: FillColors):
        ticks = list(q.ticks) or None
        cb = (fig.colorbar(cf, cax=cax, extend=q.extend, ticks=ticks) if ticks
              else fig.colorbar(cf, cax=cax, extend=q.extend))
        cb.set_label(label, color=hp.TEXT_COLOR, fontsize=10)
        return cb
    return f


def _env_stat(key, reducer, fmt, subtitle):
    """Domain-wide header stat for a synoptic env product. ``reducer`` is one of
    hp.scope_max/min/mean (scope is domain-wide here), ``fmt`` formats the value."""
    def f(spec: ProductSpec, frame, domain_label, vmax, pmin, scope):
        val = reducer(frame.env[key], scope)
        return f"{subtitle}  /  {domain_label}", fmt.format(val=val)
    return f


def _shear_streamlines(up, lo):
    """streamline_provider for a deep-layer-shear product: the shear VECTOR (kt)."""
    return lambda frame: (frame.env[f"shru_{up}_{lo}"], frame.env[f"shrv_{up}_{lo}"])


def _env200_wind(frame):
    """wind_provider for the PV map: the 200 mb wind (kt) already in env."""
    u, v = frame.env["u_200"], frame.env["v_200"]
    return u, v, np.hypot(u, v)


# ---------------------------------------------------------------------------
# The HAFS product specs, in toggle order. mslp_wind stays first so the default
# frontend view is unchanged (Wind), and the four pre-existing products keep
# their original order; mslp_pwat is appended LAST so adding it perturbs neither
# the default view nor the existing toggle order. The first four products' values
# are byte-for-byte the ones the pre-refactor render_frame / SAT_PRODUCTS used.
# ---------------------------------------------------------------------------
_SPECS = (
    ProductSpec(
        key="mslp_wind", slug="mslp_wind", quantity="wind_speed_kt", label="MSLP + 10 m Wind",
        short="Wind", order=0,
        grib="atm", sat_parm=None, field_attr="wind_kt", requires_attr=None,
        default_enhancement=None, channel=None, make_colors=_wind_colors,
        fill_method=FillMethod.PCOLORMESH, make_colorbar=_wind_colorbar,
        draw_barbs=True,
        coast_color=hp.COAST_COLOR, coast_lw=1.2, coast_halo=0.0,
        make_stat=_wind_stat,
        # The wind product's signature isotach lines (now decoupled from barbs).
        draw_wind_contours=True,
        # TC INTENSITY. The mean of member 10 m wind is the canonical wrong
        # answer: displacement across members averages two 120 kt cores into one
        # 60 kt smear. Members' intensity traces are the honest view.
        ensemble_mean_allowed=False, mean_substitute=MeanSubstitute.SPAGHETTI,
    ),
    ProductSpec(
        key="refl", slug="refl", quantity="reflectivity_dbz", label="Composite Reflectivity + MSLP",
        short="Reflectivity", order=1,
        grib="atm", sat_parm=None, field_attr="refl_dbz", requires_attr="refl_dbz",
        default_enhancement=None, channel=None, make_colors=_refl_colors,
        fill_method=FillMethod.CONTOURF_DISCRETE, make_colorbar=_refl_colorbar,
        draw_barbs=False,
        coast_color=hp.REFL_COAST_COLOR, coast_lw=1.3, coast_halo=0.0,
        make_stat=_refl_stat,
        # Reflectivity IS resolved deep convection - meaningless off a model
        # that parameterises it. Hard gate, not a preference.
        requires_explicit_convection=True,
        # Simulated sensor: an averaged echo is a radar return no radar could
        # produce. Pick a member.
        ensemble_mean_allowed=False, mean_substitute=MeanSubstitute.MEMBER_PICKER,
    ),
    ProductSpec(
        key="clean_ir", slug="clean_ir", quantity="brightness_temperature_c",
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
        # NOT convection-gated: 10.3 um BT is set by grid-scale cloud top and
        # upper-tropospheric humidity, which a parameterised-convection model
        # still produces (smoother, but not a category error like reflectivity).
        # Simulated sensor -> the mean is an unphysical radiance.
        ensemble_mean_allowed=False, mean_substitute=MeanSubstitute.MEMBER_PICKER,
    ),
    ProductSpec(
        key="water_vapor", slug="water_vapor", quantity="brightness_temperature_c",
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
        # As clean_ir: 6.2 um BT tracks the mid/upper humidity field, so it is
        # not convection-gated; simulated sensor, so no mean.
        ensemble_mean_allowed=False, mean_substitute=MeanSubstitute.MEMBER_PICKER,
    ),
    ProductSpec(
        # Simulated 89 GHz microwave (SSMIS-F17, 91.7 GHz) -- the canonical
        # NRL/CIMSS "89 GHz color" (blue-ocean look) is the
        # POLARIZATION-CORRECTED temperature PCT85 = 1.818*V - 0.818*H, NOT a
        # single channel. The raw H-pol channel alone reads a green ocean (low
        # H-pol emissivity); PCT removes that so clear ocean is ~ -5 degC (blue)
        # while ice-scattering cores stay cold. V=parm63, H=parm62 (both in every
        # storm+parent .sat). Decoded to degC, fill-masked, clipped [105,290] K.
        key="sim_89h", slug="sim_89h", quantity="brightness_temperature_c",
        label="Simulated 89 PCT (from HAFS)", short="89H", order=3.5,
        grib="sat", sat_parm=None, sat_pct=(63, 62),
        field_attr="bt_c", requires_attr="bt_c",
        default_enhancement="ice89h",
        channel="89 PCT (91.7 GHz, SSMIS-F17 pol-corrected)",
        make_colors=_bt_colors,
        fill_method=FillMethod.PCOLORMESH, make_colorbar=_bt_colorbar,
        draw_barbs=False,
        # No MSLP overlay (see clean_ir): BT fill + black coasts only.
        draw_mslp_isobars=False, draw_mslp_markers=False,
        coast_color=hp.COAST_COLOR, coast_lw=1.1, coast_halo=0.0,
        make_stat=_bt_stat,
        selectable_enhancements=tuple(tp.list_enhancements_for_domain("mw")),
        # SIMULATED MICROWAVE. The entire 89 GHz signal is ice scattering by
        # convective cores; with convection parameterised there are no cores to
        # scatter, so the render would be a blue field with no storm in it.
        # Hard gate, exactly as for reflectivity.
        requires_explicit_convection=True,
        ensemble_mean_allowed=False, mean_substitute=MeanSubstitute.MEMBER_PICKER,
    ),
    ProductSpec(
        key="mslp_pwat", slug="mslp_pwat", quantity="precipitable_water_mm", label="MSLP & PWAT",
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
        # PWAT is a smooth column-integrated moisture field - the ensemble mean
        # IS a meaningful moisture forecast (and a standard operational product).
        # Its MSLP L marker is dropped in a mean render by rule (b).
        ensemble_mean_allowed=True, mean_substitute=MeanSubstitute.NOT_APPLICABLE,
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
        key="hgt_wind_850", slug="hgt_wind_850", quantity="wind_speed_kt", label="850 mb Height & Wind",
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
        # Pressure-level wind + geopotential height: smooth synoptic mass
        # fields, the archetypal mean-safe quantities (rule (b) drops the L).
        ensemble_mean_allowed=True, mean_substitute=MeanSubstitute.NOT_APPLICABLE,
    ),
    ProductSpec(
        key="hgt_wind_700", slug="hgt_wind_700", quantity="wind_speed_kt", label="700 mb Height & Wind",
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
        # As 850 mb: smooth mass field, mean-safe.
        ensemble_mean_allowed=True, mean_substitute=MeanSubstitute.NOT_APPLICABLE,
    ),
    ProductSpec(
        key="hgt_wind_500", slug="hgt_wind_500", quantity="wind_speed_kt", label="500 mb Height & Wind",
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
        # 500 mb height is the steering-flow field the spec names
        # explicitly as mean-safe; the ensemble mean 500 mb chart is the
        # single most standard ensemble product there is.
        ensemble_mean_allowed=True, mean_substitute=MeanSubstitute.NOT_APPLICABLE,
    ),
    ProductSpec(
        key="vort_wind_850", slug="vort_wind_850", quantity="cyclonic_vorticity_850_1e5",
        label="850 mb Cyclonic Vorticity & Wind", short="850 Vort", order=8,
        grib="atm", sat_parm=None, field_attr="upper", requires_attr="upper",
        default_enhancement=None, channel=None,
        make_colors=_vort_colors(850, "cyclonic_vorticity_850_1e5"),
        fill_method=FillMethod.PCOLORMESH,
        make_colorbar=_vort_colorbar("cyclonic_vorticity_850_1e5"),
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
        # Vorticity is a sharp, displacement-sensitive extremum field: the
        # mean of N offset vorticity maxima is a broad low-amplitude blob no
        # member contains. P(vort > threshold) is the honest ensemble view.
        ensemble_mean_allowed=False, mean_substitute=MeanSubstitute.PROBABILITY,
    ),
    ProductSpec(
        key="vort_wind_500", slug="vort_wind_500", quantity="cyclonic_vorticity_500_1e5",
        label="500 mb Cyclonic Vorticity & Wind", short="500 Vort", order=9,
        grib="atm", sat_parm=None, field_attr="upper", requires_attr="upper",
        default_enhancement=None, channel=None,
        make_colors=_vort_colors(500, "cyclonic_vorticity_500_1e5"),
        fill_method=FillMethod.PCOLORMESH,
        make_colorbar=_vort_colorbar("cyclonic_vorticity_500_1e5"),
        # No overlays (as at 850 mb): no isobars, no L marker, no height contours.
        draw_barbs=True, draw_wind_contours=False,
        draw_mslp_isobars=False, draw_mslp_markers=False,
        wind_provider=_level_wind_provider(500),
        coast_color=hp.REFL_COAST_COLOR, coast_lw=1.3, coast_halo=0.0,
        make_stat=_vort_stat(500),
        # As 850 mb vorticity: displacement-sensitive extremum, no mean.
        ensemble_mean_allowed=False, mean_substitute=MeanSubstitute.PROBABILITY,
    ),
    ProductSpec(
        key="rh_layer", slug="rh_layer", quantity="relative_humidity_pct",
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
        # Layer-mean relative humidity is a smooth environmental moisture
        # field; the ensemble mean is a meaningful dry-air-intrusion signal.
        ensemble_mean_allowed=True, mean_substitute=MeanSubstitute.NOT_APPLICABLE,
        # Thin black RH contours every 10% from 50 (only those inside a frame's
        # layer-RH range draw), same style as the PWAT / wind category contours.
        field_contour_levels=(50, 60, 70, 80, 90),
    ),

    # --- PARENT-domain SYNOPTIC ENVIRONMENTAL products (orders 11..19). All are
    # parent.atm ONLY (domains=("parent.atm",)) and synoptic_parent=True: the full
    # parent extent, no storm-centered crop, no L marker, no SSHWS pill, stats
    # reduced over the whole domain. They read frame.env (requires_attr="env"),
    # which generate_hafs_plots ingests for parent frames only. One muted ramp
    # per field; MSLP isobars carry the synoptic context (off where streamlines /
    # PV barbs already do). Appended last so existing toggle order is unchanged. ---
    ProductSpec(
        key="env_precip", slug="env_precip", quantity="total_precip_in", label="Total Precipitation (in)",
        short="Precip", order=11,
        grib="atm", sat_parm=None, field_attr="env", requires_attr="env",
        default_enhancement=None, channel=None,
        make_colors=_env_colors("apcp_in", "total_precip_in"),
        fill_method=FillMethod.PCOLORMESH,
        make_colorbar=_env_colorbar("Total precip (in)", "total_precip_in"),
        draw_barbs=False, draw_mslp_isobars=True, draw_mslp_markers=False,
        coast_color=hp.COAST_COLOR, coast_lw=1.1, coast_halo=0.0,
        make_stat=_env_stat("apcp_in", hp.scope_max, "MAX PRECIP {val:.1f} in",
                            "Total Precipitation (in) & MSLP"),
        # PRECIP MAXIMA. Averaging displaced rain shields collapses two
        # 8 in bullseyes into one 2 in blur. P(precip > threshold) instead.
        ensemble_mean_allowed=False, mean_substitute=MeanSubstitute.PROBABILITY,
        synoptic_parent=True, domains=("parent.atm",),
    ),
    ProductSpec(
        key="env_shear_200_850", slug="env_shear_200_850", quantity="shear_200_850_kt",
        label="200-850 mb Wind Shear (kt)", short="Shear 200-850", order=12,
        grib="atm", sat_parm=None, field_attr="env", requires_attr="env",
        default_enhancement=None, channel=None,
        make_colors=_env_colors("shrmag_200_850", "shear_200_850_kt"),
        fill_method=FillMethod.PCOLORMESH,
        make_colorbar=_env_colorbar("200-850 mb shear (kt)", "shear_200_850_kt"),
        draw_barbs=False, draw_mslp_isobars=False, draw_mslp_markers=False,
        coast_color=hp.COAST_COLOR, coast_lw=1.1, coast_halo=0.0,
        make_stat=_env_stat("shrmag_200_850", hp.scope_max, "MAX SHEAR {val:.0f} kt",
                            "200-850 mb Deep-Layer Shear (kt)"),
        # Deep-layer shear is the environmental field the spec names as
        # mean-safe: broad, slowly varying, and what the mean is FOR.
        ensemble_mean_allowed=True, mean_substitute=MeanSubstitute.NOT_APPLICABLE,
        synoptic_parent=True, streamline_provider=_shear_streamlines(200, 850),
        domains=("parent.atm",),
    ),
    ProductSpec(
        key="env_shear_500_850", slug="env_shear_500_850", quantity="shear_500_850_kt",
        label="500-850 mb Wind Shear (kt)", short="Shear 500-850", order=13,
        grib="atm", sat_parm=None, field_attr="env", requires_attr="env",
        default_enhancement=None, channel=None,
        make_colors=_env_colors("shrmag_500_850", "shear_500_850_kt"),
        fill_method=FillMethod.PCOLORMESH,
        make_colorbar=_env_colorbar("500-850 mb shear (kt)", "shear_500_850_kt"),
        draw_barbs=False, draw_mslp_isobars=False, draw_mslp_markers=False,
        coast_color=hp.COAST_COLOR, coast_lw=1.1, coast_halo=0.0,
        make_stat=_env_stat("shrmag_500_850", hp.scope_max, "MAX SHEAR {val:.0f} kt",
                            "500-850 mb Mid-Level Shear (kt)"),
        # As the 200-850 shear: broad environmental field, mean-safe.
        ensemble_mean_allowed=True, mean_substitute=MeanSubstitute.NOT_APPLICABLE,
        synoptic_parent=True, streamline_provider=_shear_streamlines(500, 850),
        domains=("parent.atm",),
    ),
    ProductSpec(
        key="env_pv_200", slug="env_pv_200", quantity="potential_vorticity_pvu",
        label="200 mb Potential Vorticity (PVU)", short="200 PV", order=14,
        grib="atm", sat_parm=None, field_attr="env", requires_attr="env",
        default_enhancement=None, channel=None,
        make_colors=_env_colors("pv_200", "potential_vorticity_pvu"),
        fill_method=FillMethod.PCOLORMESH,
        make_colorbar=_env_colorbar("200 mb PV (PVU)", "potential_vorticity_pvu"),
        # 200 mb wind barbs over the PV fill; no isobars / L (upper-level field).
        draw_barbs=True, wind_provider=_env200_wind,
        draw_mslp_isobars=False, draw_mslp_markers=False,
        coast_color=hp.COAST_COLOR, coast_lw=1.1, coast_halo=0.0,
        make_stat=_env_stat("pv_200", hp.scope_max, "MAX PV {val:.1f} PVU",
                            "200 mb Potential Vorticity (PVU) & Wind"),
        # PV is the textbook case AGAINST a mean: upper-level PV lives in
        # thin filaments, and averaging filaments across members yields a
        # smeared sheet that misrepresents every member's dynamics.
        # P(PV > 2 PVU) is the meaningful tropopause-intrusion product.
        ensemble_mean_allowed=False, mean_substitute=MeanSubstitute.PROBABILITY,
        synoptic_parent=True, domains=("parent.atm",),
    ),
    ProductSpec(
        key="env_sst", slug="env_sst", quantity="sst_c", label="Sea-Surface Temp (°C)",
        short="SST", order=15,
        grib="atm", sat_parm=None, field_attr="env", requires_attr="env",
        default_enhancement=None, channel=None,
        # 0..32 °C == the SST_ACTUAL palette's design range (violet 0 -> oxblood
        # 32), so a given SST reads the SAME color as the site SST maps.
        make_colors=_env_colors("sst_c", "sst_c"),
        fill_method=FillMethod.PCOLORMESH,
        make_colorbar=_env_colorbar("SST (°C)", "sst_c"),
        # MSLP isobars for synoptic context (task: SST + MSLP contours).
        draw_barbs=False, draw_mslp_isobars=True, draw_mslp_markers=False,
        coast_color=hp.COAST_COLOR, coast_lw=1.1, coast_halo=0.0,
        make_stat=_env_stat("sst_c", hp.scope_max, "MAX SST {val:.1f}°C",
                            "Sea-Surface Temperature (°C) & MSLP"),
        # SST: slowly varying lower boundary condition, explicitly mean-safe.
        ensemble_mean_allowed=True, mean_substitute=MeanSubstitute.NOT_APPLICABLE,
        synoptic_parent=True, domains=("parent.atm",),
    ),
    ProductSpec(
        key="env_tropt", slug="env_tropt", quantity="tropopause_temp_c", label="Tropopause Temperature (°C)",
        short="Tropo T", order=16,
        grib="atm", sat_parm=None, field_attr="env", requires_attr="env",
        default_enhancement=None, channel=None,
        make_colors=_env_colors("tropt_c", "tropopause_temp_c"),
        fill_method=FillMethod.PCOLORMESH,
        make_colorbar=_env_colorbar("Tropopause temp (°C)", "tropopause_temp_c"),
        draw_barbs=False, draw_mslp_isobars=True, draw_mslp_markers=False,
        coast_color=hp.COAST_COLOR, coast_lw=1.1, coast_halo=0.0,
        make_stat=_env_stat("tropt_c", hp.scope_min, "MIN TROP T {val:.1f}°C",
                            "Tropopause Temperature (°C) & MSLP"),
        # Tropopause temperature is a smooth large-scale thermal field.
        ensemble_mean_allowed=True, mean_substitute=MeanSubstitute.NOT_APPLICABLE,
        synoptic_parent=True, domains=("parent.atm",),
    ),
    ProductSpec(
        key="env_cape", slug="env_cape", quantity="cape_jkg", label="Surface CAPE (J/kg)",
        short="CAPE", order=17,
        grib="atm", sat_parm=None, field_attr="env", requires_attr="env",
        default_enhancement=None, channel=None,
        make_colors=_env_colors("cape_jkg", "cape_jkg"),
        fill_method=FillMethod.PCOLORMESH,
        make_colorbar=_env_colorbar("Surface CAPE (J/kg)", "cape_jkg"),
        # 10 m wind barbs (default provider) + MSLP isobars.
        draw_barbs=True, draw_mslp_isobars=True, draw_mslp_markers=False,
        coast_color=hp.COAST_COLOR, coast_lw=1.1, coast_halo=0.0,
        make_stat=_env_stat("cape_jkg", hp.scope_max, "MAX CAPE {val:.0f} J/kg",
                            "Surface CAPE (J/kg) & 10 m Wind"),
        # CAPE is a nonlinear extremum: the mean is biased low everywhere
        # and marks no member's actual instability axis.
        ensemble_mean_allowed=False, mean_substitute=MeanSubstitute.PROBABILITY,
        synoptic_parent=True, domains=("parent.atm",),
    ),
    ProductSpec(
        key="env_srh", slug="env_srh", quantity="srh_03km_m2s2",
        label="0-3 km Storm-Relative Helicity (m²/s²)", short="0-3 km SRH",
        order=18,
        grib="atm", sat_parm=None, field_attr="env", requires_attr="env",
        default_enhancement=None, channel=None,
        make_colors=_env_colors("srh_03km", "srh_03km_m2s2"),
        fill_method=FillMethod.PCOLORMESH,
        make_colorbar=_env_colorbar("0-3 km SRH (m²/s²)", "srh_03km_m2s2"),
        draw_barbs=False, draw_mslp_isobars=True, draw_mslp_markers=False,
        coast_color=hp.COAST_COLOR, coast_lw=1.1, coast_halo=0.0,
        make_stat=_env_stat("srh_03km", hp.scope_max, "MAX SRH {val:.0f} m2/s2",
                            "0-3 km Storm-Relative Helicity & MSLP"),
        # SRH is sharp and displacement-sensitive, like vorticity.
        ensemble_mean_allowed=False, mean_substitute=MeanSubstitute.PROBABILITY,
        synoptic_parent=True, domains=("parent.atm",),
    ),
    ProductSpec(
        key="env_lhtfl", slug="env_lhtfl", quantity="latent_heat_flux_wm2", label="Latent Heat Flux (W/m²)",
        short="Latent Flux", order=19,
        grib="atm", sat_parm=None, field_attr="env", requires_attr="env",
        default_enhancement=None, channel=None,
        make_colors=_env_colors("lhtfl_wm2", "latent_heat_flux_wm2"),
        fill_method=FillMethod.PCOLORMESH,
        make_colorbar=_env_colorbar("Latent heat flux (W/m²)", "latent_heat_flux_wm2"),
        # 10 m wind barbs (default provider) + MSLP isobars.
        draw_barbs=True, draw_mslp_isobars=True, draw_mslp_markers=False,
        coast_color=hp.COAST_COLOR, coast_lw=1.1, coast_halo=0.0,
        make_stat=_env_stat("lhtfl_wm2", hp.scope_max, "MAX LHF {val:.0f} W/m2",
                            "Latent Heat Flux (W/m²) & 10 m Wind"),
        # Surface latent heat flux is a broad air-sea exchange field whose
        # mean is a meaningful basin-scale energy-supply signal.
        ensemble_mean_allowed=True, mean_substitute=MeanSubstitute.NOT_APPLICABLE,
        synoptic_parent=True, domains=("parent.atm",),
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

#: FIGURE products (spec #25): diagnostic PLATES, not field rasters - they
#: bypass the ProductSpec color/fill machinery entirely (the builder routes
#: them to their own renderer) and carry figure="panel" so the frontend never
#: applies map semantics (geometry affine, shear overlay, value readout) to
#: them. Registry-level so the manifest, the toggle order, and the model gate
#: all see one product list.
FIGURE_PRODUCTS = {
    "structure": {
        "slug": "structure",
        "label": "Azimuthal-Mean Structure",
        "short": "Structure",
        "quantity": None,
        "ensemble_mean_allowed": False,
        "mean_substitute": "spaghetti",
        "requires_explicit_convection": False,
        "selectable_enhancements": [],
        "figure": "panel",
        # storm domain only: the profiles are nest-computed (the parent rides
        # inside the plate as the resolution comparison, not as its own frames).
        "domains": ("storm.atm",),
    },
}


def default_order() -> list:
    """Product keys in default toggle/render order (mslp_wind first; figure
    plates after the field products)."""
    return [s.key for s in ordered_specs()] + list(FIGURE_PRODUCTS)

def products_dict() -> dict:
    """``{key: {slug,label,short}}`` in order - the generator's PRODUCTS table."""
    out = {s.key: s.product_meta() for s in ordered_specs()}
    for k, m in FIGURE_PRODUCTS.items():
        out[k] = {kk: vv for kk, vv in m.items() if kk != "domains"}
    return out

def sat_parm(key: str) -> Optional[int]:
    """GRIB2 parameterNumber for a product's .sat channel, or None for .atm
    products / unknown keys."""
    s = REGISTRY.get(key)
    return s.sat_parm if s else None


def sat_pct(key: str) -> Optional[tuple]:
    """(V_parm, H_parm) for a polarization-corrected (PCT) product, else None."""
    s = REGISTRY.get(key)
    return s.sat_pct if s else None


def grib_parms(key: str) -> tuple:
    """ALL .sat GRIB2 parameterNumbers a product needs decoded: the two PCT
    channels for a PCT product, the single channel otherwise, () for .atm."""
    s = REGISTRY.get(key)
    if not s:
        return ()
    if s.sat_pct:
        return tuple(int(p) for p in s.sat_pct)
    return (int(s.sat_parm),) if s.sat_parm is not None else ()


# ---------------------------------------------------------------------------
# STRUCTURAL model x product gate.
#
# The rule this enforces is a correctness rule, not a style rule, so it is
# enforced in DEPTH rather than in one place: ``generate_hafs_plots`` calls
# ``allowed_products_for_model`` when planning render jobs (the pair is never
# scheduled), ``hafs_plot.render_frame`` calls ``assert_renderable`` (a caller
# that reaches the renderer by another path is refused, loudly), and the
# manifest is composed from the planned jobs (so the pair is never advertised
# and the frontend never offers it). A product that a model cannot physically
# support is therefore UNRENDERABLE, not merely discouraged.
# ---------------------------------------------------------------------------
class IncompatibleProduct(ValueError):
    """Raised when a (model, product) pair is physically meaningless.

    Carries both ids so the failure names the pair rather than the symptom.
    """

    def __init__(self, model_slug: str, product_key: str, reason: str):
        self.model_slug = model_slug
        self.product_key = product_key
        self.reason = reason
        super().__init__(f"product {product_key!r} is not renderable for model "
                         f"{model_slug!r}: {reason}")


def incompatibility_reason(model_slug: str, product_key: str) -> Optional[str]:
    """Why this (model, product) pair is refused, or None if it is allowed.

    Unknown ids are refused rather than waved through - a typo'd model or
    product must not silently render.
    """
    if product_key in FIGURE_PRODUCTS:
        # Figure plates read cached winds/heights any carried model has;
        # no convection gate applies (nothing here IS resolved convection).
        return (None if mr.has_model(model_slug)
                else f"unknown model {model_slug!r}")
    spec = REGISTRY.get(product_key)
    if spec is None:
        return f"unknown product {product_key!r}"
    if not mr.has_model(model_slug):
        return f"unknown model {model_slug!r}"
    model = mr.get_model(model_slug)
    if spec.requires_explicit_convection and not model.convection_explicit:
        return (f"{spec.label} is a resolved-deep-convection product and "
                f"{model.label} parameterises convection, so the field would be "
                f"a category error rather than a weak signal")
    return None


def product_allowed(model_slug: str, product_key: str) -> bool:
    """True when this model may render this product."""
    return incompatibility_reason(model_slug, product_key) is None


def assert_renderable(model_slug: str, product_key: str) -> None:
    """Raise :class:`IncompatibleProduct` unless the pair is renderable.

    The renderer's own guard - the last line of the structural gate.
    """
    reason = incompatibility_reason(model_slug, product_key)
    if reason is not None:
        raise IncompatibleProduct(model_slug, product_key, reason)


def allowed_products_for_model(model_slug: str,
                               products: Optional[list] = None) -> list:
    """The subset of ``products`` (default: every product, in toggle order)
    that ``model_slug`` may render. The job planner's filter."""
    keys = products if products is not None else default_order()
    return [k for k in keys if product_allowed(model_slug, k)]


# ---------------------------------------------------------------------------
# ENSEMBLE-MEAN policy.
#
# ``ProductSpec.ensemble_mean_allowed`` states the FILL field's policy; this
# function layers on the two structural rules documented on that field, so no
# caller has to remember them and no spec has to restate them.
# ---------------------------------------------------------------------------
def ensemble_mean_policy(product_key: str, domain: Optional[str] = None,
                         model_slug: Optional[str] = None) -> dict:
    """Whether an ensemble MEAN may be rendered, and what to show if not.

    Returns ``{"allowed": bool, "substitute": <MeanSubstitute value str>,
    "reason": str|None}``. ``domain`` is the raw HAFS domain name (e.g.
    ``"storm.atm"``); ``model_slug`` lets a model declare a storm-following nest
    so the rule applies to any model, not just to a hardcoded domain name.

    Rule (a): a storm-following nest denies the mean for EVERY product - members
    centre their nests on their own forecast positions, so a cell-wise mean
    averages different places and is not a defined operation.
    """
    spec = REGISTRY.get(product_key)
    if spec is None:
        return {"allowed": False,
                "substitute": MeanSubstitute.MEMBER_PICKER.value,
                "reason": f"unknown product {product_key!r}"}

    # Rule (a): storm-following nest overrides any product-level allowance.
    on_moving_nest = bool(domain and domain.startswith("storm"))
    if model_slug and mr.has_model(model_slug):
        on_moving_nest = on_moving_nest and mr.get_model(model_slug).storm_following_nest
    if on_moving_nest:
        return {
            "allowed": False,
            "substitute": MeanSubstitute.MEMBER_PICKER.value,
            "reason": ("fields on a storm-following nest have no shared grid "
                       "across members, so a cell-wise mean averages different "
                       "places"),
        }

    if spec.ensemble_mean_allowed:
        return {"allowed": True,
                "substitute": MeanSubstitute.NOT_APPLICABLE.value,
                "reason": None}
    return {"allowed": False, "substitute": spec.mean_substitute.value,
            "reason": f"{spec.label} is not a mean-safe quantity"}


def suppress_mslp_marker_in_mean(spec: ProductSpec) -> bool:
    """Rule (b): an MSLP MINIMUM marker is never mean-safe, so a mean render
    drops it on every product that draws one. True when there is one to drop."""
    return spec.draw_mslp_markers


# ---------------------------------------------------------------------------
# AI intensity-statistic suppression.
#
# ``ModelSpec.show_intensity_stat`` is False for every learned emulator. The
# header's intensity claims are the VMAX reading, the MSLP minimum, and the
# SSHWS category pill (which is derived from VMAX); those three are withheld,
# while the product's OWN statistic (MIN BT, MAX dBZ, MAX PWAT, ...) is kept -
# an AI model's cloud field is still its cloud field, only its intensity is not
# a forecast.
#
# This runs ONLY on the AI path. Physics models never call it, so their headers
# are byte-identical to before this existed - a requirement, since every cached
# HAFS frame would otherwise need a cold re-render.
# ---------------------------------------------------------------------------
#: The separator every make_stat builder uses between header stat segments.
STAT_SEP = "   /   "

#: Segment prefixes that constitute an INTENSITY claim.
INTENSITY_PREFIXES = ("VMAX ", "MSLP ")

#: Shown when suppression removes every segment (the wind product, whose stat is
#: nothing BUT intensity). Says why rather than leaving the corner blank.
INTENSITY_WITHHELD = "intensity not shown (AI model)"


def strip_intensity(right_stat: str, scope_label: str = "") -> str:
    """Drop the VMAX / MSLP segments from a header right-stat.

    ``scope_label`` is the honesty suffix (e.g. ``"  (domain-wide)"``) that the
    stat builders append to their LAST segment - usually the MSLP one. Dropping
    that segment would drop the label with it, so it is re-attached to whatever
    survives. Returns :data:`INTENSITY_WITHHELD` when nothing survives.
    """
    segs = [s for s in right_stat.split(STAT_SEP) if s]
    kept = [s for s in segs if not s.startswith(INTENSITY_PREFIXES)]
    if not kept:
        return INTENSITY_WITHHELD + (scope_label or "")
    out = STAT_SEP.join(kept)
    # Re-attach the scope label exactly once: a kept segment may already carry
    # it (when MSLP was not the last segment), in which case adding it again
    # would read "(domain-wide)  (domain-wide)".
    if scope_label and not out.endswith(scope_label):
        out += scope_label
    return out


# ---------------------------------------------------------------------------
# Import-time validation. These are invariants a future edit could silently
# break, so they are checked once at import rather than left to a test that may
# not run before a deploy.
# ---------------------------------------------------------------------------
def _validate_specs() -> None:
    seen_orders: dict = {}
    for s in _SPECS:
        if s.slug != s.key:
            raise ValueError(f"spec {s.key!r}: slug {s.slug!r} must equal key")
        if s.order in seen_orders:
            raise ValueError(f"spec {s.key!r}: duplicate order {s.order} "
                             f"(also {seen_orders[s.order]!r})")
        seen_orders[s.order] = s.key
        # The mean policy must be stated consistently: a substitute is named
        # if and ONLY if the mean is denied. Without this biconditional a spec
        # could claim the mean is allowed while also naming a substitute, and
        # the frontend would have two contradictory instructions.
        allowed = s.ensemble_mean_allowed
        na = s.mean_substitute is MeanSubstitute.NOT_APPLICABLE
        if allowed != na:
            raise ValueError(
                f"spec {s.key!r}: ensemble_mean_allowed={allowed} but "
                f"mean_substitute={s.mean_substitute.name} - a substitute must "
                f"be named iff the mean is denied")
        # Every product names a REGISTERED quantity. A typo would silently
        # detach the product from the shared scale - exactly the drift the
        # quantity registry exists to prevent - so it fails at import.
        if not s.quantity:
            raise ValueError(f"spec {s.key!r}: no quantity key")
        if not tq.has_quantity(s.quantity):
            raise ValueError(f"spec {s.key!r}: unknown quantity "
                             f"{s.quantity!r} (not in tat_palettes.quantities)")
        # A convection-gated product must be reachable by at least one model,
        # or it is dead weight that silently renders nowhere.
        if s.requires_explicit_convection and not any(
                m.convection_explicit for m in mr.ordered_models()):
            raise ValueError(
                f"spec {s.key!r} requires explicit convection but no registered "
                f"model provides it - the product would never render")


_validate_specs()
