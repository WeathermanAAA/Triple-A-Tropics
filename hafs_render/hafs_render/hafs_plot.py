#!/usr/bin/env python3
"""HAFS forecast plots - vertical slice (v1: MSLP + 10 m wind).

First feature of the new ``/models/`` page on triple-a-tropics.com. This script
fetches ONE (model, storm, domain, forecast-hour) HAFS field set and renders a
TAT-styled PNG of mean-sea-level pressure (isobars) over 10 m wind speed
(filled, knots, Saffir-Simpson-flavored palette).

Run it standalone to validate a single frame, then ``generate_hafs_plots.py``
(the full-cycle builder) reuses the fetch + render functions here to loop every
active storm × {hafsa,hafsb} × {storm.atm,parent.atm} × forecast hour.

Data source - Herbie + AWS Open Data
------------------------------------
HAFS GRIB2 lives in the public ``noaa-nws-hafs-pds`` S3 bucket (archive back to
2023-06-19) and on NOMADS (recent cycles only). The Herbie HAFS template that
ships in ``herbie-data`` only knows NOMADS *and* builds its product list from a
**live** lookup of currently-active storms - so it can neither reach the
historical archive nor be constructed for a past storm (``storm_name`` resolves
to ``None`` → ``None.title()`` crash). ``install_hafs_templates()`` below
replaces ``herbie.models.hafsa``/``hafsb`` with AWS-first templates that don't
depend on that live lookup, which is what makes the dev cycle reachable.

Herbie does ``.idx`` byte-range subsetting, so even though a storm.atm file is
~240 MB we only pull the three messages we need (PRMSL + UGRD/VGRD 10 m).

Why no cartopy
--------------
Per CLAUDE.md the repo deliberately avoids cartopy; the vendored Natural Earth
GeoJSON (``ne_10m_*``, with 50 m / 110 m fallbacks) is the basemap. HAFS
grids are regular lat/lon
(PlateCarree), so we plot straight in lon/lat and overlay the GeoJSON
coastlines/borders exactly like ``generate_sst_plots.py``.

Dev cycle (has data): ``2023-09-09 00:00``, ``hafsa``, storm ``13l`` (Lee),
``storm.atm``, fxx 12.

    python hafs_plot.py            # renders the dev cycle to hafs_test.png
    python hafs_plot.py --model hafsb --storm 13l --product parent.atm --fxx 24
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from collections import namedtuple
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.ticker as mticker
from matplotlib import patheffects as pe

# NOTE: the product catalog (which field/cmap/colorbar/coast/stat each product
# uses) lives in hafs_registry.ProductSpec. render_frame imports it LAZILY (in
# the function body) so there's no module-load cycle: hafs_registry imports the
# low-level color primitives from THIS module at its top. tat_palettes (the
# canonical shared color source for the simulated-satellite products) is now
# imported by hafs_registry, not here.

log = logging.getLogger("hafs-plot")

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# TAT render palette (the satellite-page look from the handoff brief - note
# these are the *image* colors, distinct from styles.css :root which themes the
# HTML page).
# ---------------------------------------------------------------------------
PLOT_BG = "#333333"   # plot interior (axes facecolor) ONLY; the figure margin /
                      # savefig frame use BAND_BG so the border matches the
                      # header band, not the plot interior.
# CANONICAL TAT BASEMAP (single source of truth - same hexes as the client
# models/regions.js). The dark navy ocean + slate land fill reads far better under
# data fields than bare outlines; borders are MUTED/secondary so they never
# overpower the data. Used for the field products (spec.filled_basemap True);
# the full-frame simulated-satellite products keep their own styling.
OCEAN_FILL = "#07101c"        # axes facecolor (ocean)
LAND_FILL = "#2f3f59"         # land polygons, UNDER the data field
COAST_BORDER = (150 / 255, 175 / 255, 205 / 255, 0.28)   # coastline, ON TOP
COUNTRY_BORDER = (150 / 255, 175 / 255, 205 / 255, 0.45)  # admin_0 borders, ON TOP
STATE_BORDER = (150 / 255, 175 / 255, 205 / 255, 0.18)   # admin_1 borders, subtle
TEXT_COLOR = "#e8eef5"
ACCENT_COLOR = "#79f0d6"
MUTED_COLOR = "#9199a4"
GRID_COLOR = "#3a4252"
COAST_COLOR = "#000000"
BORDER_COLOR = "#000000"
# Reflectivity-product coast/border color: bold neon green. White conflicted with
# the white MSLP isobars; neon green reads cleanly against both the dark ocean and
# the bright radar cores. Wind keeps the black coasts above.
REFL_COAST_COLOR = "#39ff14"
# Simulated-satellite (IR/WV) coast/border color: a bright near-white line drawn
# WITH a dark halo (see _draw_feature_lines ``halo``). Currently UNUSED - the
# sim-sat / PWAT / RH products switched to plain black coasts (COAST_COLOR,
# coast_halo=0.0); kept defined for reference / easy re-enable. The haloed
# near-white line was meant to read across BOTH the colorful cold-cloud tops of
# the rainbow_ir / wv_tat fills AND their grayscale warm halves.
SAT_COAST_COLOR = "#eef3f9"
SAT_COAST_HALO = "#0a0d12"
WATERMARK = "@WeathermanAAA_"

KT_PER_MS = 1.94384  # m s-1 → knots

# ---------------------------------------------------------------------------
# FIELD DTYPE POLICY - read this before changing any dtype below
# ---------------------------------------------------------------------------
# GRIB2 packs values as scaled integers and cfgrib unpacks them to float32:
# measured on a parent-domain frame, ALL 38 GRIB reads an ingest makes hand back
# float32. Every one of them then went through ``.astype(float)`` - float64 - so
# the pipeline carried, cached and rendered each field at twice the footprint
# while holding exactly the same information.
#
# The policy that replaces that, in three parts:
#
# 1. A field that is a PURE PASS-THROUGH of a decoded message is cached at
#    STORE_DTYPE (float32). Narrowing a float64 that came from float32 is
#    lossless, so the cached VALUE is bit-for-bit what it always was - verified
#    field-by-field, not assumed.
#
# 2. A DERIVED field (unit conversion, difference, integral, PV solve) is
#    computed in float64 and CACHED in float64. Narrowing it would round a value
#    that has no float32 original to return to. Measured, that rounding is ~6e-8
#    relative - and it is NOT harmless: see (3).
#
# 3. RENDER_DTYPE: _pack_frame widens every field back to float64 before it
#    reaches matplotlib, because matplotlib's own arithmetic is dtype-sensitive.
#    This is the counter-intuitive part and the reason the policy is written down
#    rather than inferred: handing streamplot a float32 field whose values are
#    BIT-IDENTICAL still moved 0.8% of the pixels on hgt_wind_700 and 5.0% on
#    env_shear_500_850 - streamline integration is an ODE solve, so it amplifies
#    a last-ulp difference into a visibly different trajectory. Wind/MSLP stay
#    float32 exactly as they always were (cfgrib float32 x a Python float is
#    float32 under NEP 50, so those four were never float64 to begin with).
#
# Net: the ingest holds less, while every array the renderer sees is bit-for-bit
# the array it saw before (verified: 87/87 PNGs byte-identical, 175/175 field
# values bit-identical, across 21 products x 5 real frames).
#
# The win here is MEMORY, not disk - measured, and worth stating because the
# obvious assumption is wrong. Narrowing the pass-through fields moved the cache
# entry only 1.3%, because a float64 widened from float32 has 29 trailing zero
# mantissa bits in every value and zlib was already compressing those away: on
# disk it was ALREADY costing about what float32 costs. In memory it was not -
# there it was the full 2x. (The derived float64 fields are genuine float64 and
# are the part that compresses poorly, which is what the old "zlib compresses
# float64 height/vort grids less" note in hafs_cache was really observing.)
# tests/test_field_memory.py pins the invariants; the render parity gate proves
# the output.
STORE_DTYPE = np.float32
RENDER_DTYPE = np.float64


def _as_store(a: np.ndarray) -> np.ndarray:
    """Narrow a PASS-THROUGH field to the cache storage dtype (see the policy
    above). Only valid where the value originated as float32 - the narrowing is
    then exact. Never apply to a derived field."""
    return np.asarray(a, dtype=STORE_DTYPE)


def _f8(a: np.ndarray) -> np.ndarray:
    """Widen to float64 for a DERIVATION that must keep its historical dtype."""
    return np.asarray(a, dtype=np.float64)


# ---------------------------------------------------------------------------
# Pressure-level (upper-air) field set - Phase 2 shared plumbing
# ---------------------------------------------------------------------------
# HAFS .atm carries 45 isobaric levels; we cache ONLY the specific levels/fields
# the planned upper-air products need (NOT all 45), per the cache-cost analysis.
# Geopotential height + wind components ride at three levels; relative vorticity
# (derived from ABSOLUTE vorticity, the only vorticity HAFS outputs) at two; and
# a single 700-300 mb layer-mean relative humidity (computed from 17 RH levels,
# none of which are themselves cached). Units, verified by spike on the 06W
# cycle: HGT gpm, UGRD/VGRD m/s (kept in m/s, NOT converted), RH %, ABSV 1/s.
UPPER_LEVELS = (850, 700, 500)        # geopotential height Z + u/v wind
VORT_LEVELS = (850, 500)              # relative vorticity (ABSV - f)
# The 17 RH levels (mb) between 700 and 300 inclusive, mass/pressure-averaged
# into ONE layer-mean field. Read transiently at ingest, never cached raw.
RH_LAYER_LEVELS = (700, 675, 650, 625, 600, 575, 550, 525, 500,
                   475, 450, 425, 400, 375, 350, 325, 300)
RH_LAYER_NAME = "rh_layer_700_300"
EARTH_OMEGA = 7.2921e-5               # Earth angular velocity, rad/s (for f)


def upper_field_names() -> tuple:
    """The cached upper-air field names, in a stable order. These are the ONLY
    pressure-level fields stored per frame (14 total): gh/u/v at each
    ``UPPER_LEVELS``, relative vorticity at each ``VORT_LEVELS``, the 700-300 mb
    layer-mean RH, and the 700-300 mb layer-mean u/v (for the RH product's
    barbs). ABSV and the raw RH/wind levels are transient (consumed to derive
    vorticity / the layer means) and never appear here."""
    names = []
    for lev in UPPER_LEVELS:
        names += [f"gh_{lev}", f"u_{lev}", f"v_{lev}"]
    for lev in VORT_LEVELS:
        names.append(f"relvort_{lev}")
    names += [RH_LAYER_NAME, "ulayer_700_300", "vlayer_700_300"]
    return tuple(names)


# ---------------------------------------------------------------------------
# Parent-domain ENVIRONMENTAL field set - the broad synoptic product family
# ---------------------------------------------------------------------------
# PARENT-domain ONLY, synoptic: no storm-center crop, no vortex removal. Most are
# direct single-level ``.atm`` reads (precip / SST / CAPE / latent-heat flux /
# tropopause T / 0-3 km SRH); the deep-layer shear is a simple layer wind
# difference (200-850 + 500-850 mb); 200 mb potential vorticity is computed with
# MetPy from a thin TMP/U/V pressure stack (HAFS carries no native PV field).
# Stored render-ready (display units) so the color factories read frame.env[...]
# straight, exactly like the upper-air fields read frame.upper[...].
SHEAR_LAYERS = ((200, 850), (500, 850))    # (upper, lower) mb deep-layer shear
PV_MAP_LEVEL = 200                          # mb level the PV map shows
# The stack MetPy differentiates for the 200 mb PV map. Exactly the level being
# mapped plus its two vertical neighbours - and no more, because that is all the
# calculation can see: potential_vorticity_baroclinic takes the vertical
# derivative with a 3-point stencil, so an INTERIOR level's PV depends only on
# itself and its immediate neighbours. Levels beyond that changed nothing while
# costing (nlev x ny x nx) float64 in every array MetPy allocates - and the PV
# solve is the single largest allocation in the whole parent-domain ingest
# (measured +1848 MB at 7 levels on a 1361x1681 parent grid). Verified, not
# assumed: PV at 200 mb from this 3-level stack is BIT-IDENTICAL to the old
# 7-level (300..100 mb) stack. 200 mb must stay INTERIOR here - putting the
# mapped level at either end would make its derivative one-sided and change the
# answer. See tests/test_field_memory.py.
PV_STACK_LEVELS = (225, 200, 175)
MM_PER_IN = 25.4
ENV_WIND_LEVELS = (200, 500, 850)           # mb winds the shear + PV-barbs need


def env_field_names() -> tuple:
    """Render-ready PARENT-domain environmental field names cached per frame, in
    a stable order (display units). Parent-domain only - these never appear on
    the storm nest. See the env product specs in hafs_registry. ``u_200`` /
    ``v_200`` (kt) ride along for the PV map's wind barbs; the 500/850 mb winds
    are transient (consumed into the shear vectors) and not cached."""
    names = ["apcp_in", "sst_c", "tropt_c", "cape_jkg", "lhtfl_wm2", "srh_03km",
             "pv_200", "u_200", "v_200"]
    for up, lo in SHEAR_LAYERS:
        names += [f"shrmag_{up}_{lo}", f"shru_{up}_{lo}", f"shrv_{up}_{lo}"]
    return tuple(names)


def _read_env_fields(H, remove_grib: bool) -> dict:
    """Read the parent-domain environmental fields from an already-constructed
    ``.atm`` Herbie object, returning native-grid (pre-trim, pre-reorder) arrays.

    Direct single-level reads (render-ready display units) for precip (in), SST
    (degC, ocean-masked via the land-sea mask), tropopause T (degC), surface CAPE
    (J/kg), latent-heat flux (W/m^2), and 0-3 km storm-relative helicity
    (m^2/s^2, the model's ``HLCY:3000-0 m`` field). The deep-layer shear winds at
    ENV_WIND_LEVELS are read in m/s -> kt and differenced into the shear vectors
    (200-850 + 500-850). A thin TMP/UGRD/VGRD pressure stack rides under the
    transient ``_pv_*`` keys; the caller turns it into 200 mb potential vorticity
    AFTER the lat reorder (the PV calc needs the final latitudes for Coriolis +
    grid deltas) and drops the stack, so it is never cached. cfgrib usually names
    the messages, with a lone-data-var fallback for any it leaves ``unknown``."""
    out: dict[str, np.ndarray] = {}

    def _read_2d(search: str, prefer: str | None = None) -> np.ndarray:
        """One single-level read, in its DECODED float32. Callers that derive
        from it widen with _f8; callers that store it straight do not, so a
        pass-through field never materialises a float64 copy at all."""
        ds = H.xarray(search, remove_grib=remove_grib)
        if isinstance(ds, list):
            ds = ds[0]
        var = prefer if (prefer and prefer in ds.data_vars) else list(ds.data_vars)[0]
        arr = np.ascontiguousarray(ds[var].values)
        ds.close()          # drop the cfgrib handle + its eccodes index now
        return arr

    def _read_levels(search: str, prefer: str | None = None):
        """Multi-level read -> (pressures hPa, stack (nlev, lat, lon)).

        The stack stays in its DECODED float32 here - it is the largest single
        allocation the ingest makes, and its only consumer (``_pv_at_level``)
        widens it to float64 itself after the lat reorder. Widening is exact and
        elementwise, so the float64 stack MetPy eventually sees is bit-for-bit
        the one this function used to build eagerly, at half the transient cost.
        """
        ds = H.xarray(search, remove_grib=remove_grib)
        if isinstance(ds, list):
            ds = ds[0]
        var = prefer if (prefer and prefer in ds.data_vars) else list(ds.data_vars)[0]
        da = ds[var]
        p = da["isobaricInhPa"].values.astype(float)
        vals = np.moveaxis(da.values, da.dims.index("isobaricInhPa"), 0)
        vals = np.ascontiguousarray(vals)   # detach from the dataset before close
        ds.close()
        return p, vals

    # --- direct single-level reads (render-ready units) ---
    # DERIVED (a unit conversion happens) -> float64, per the dtype policy.
    out["apcp_in"] = _f8(_read_2d(":APCP:surface:0-", "tp")) / MM_PER_IN
    wtmp = _f8(_read_2d(":WTMP:surface:", "wtmp"))
    try:                                   # mask SST to ocean (land-sea mask 1=land)
        land = _read_2d(":LAND:surface:", "lsm")
        wtmp = np.where(land >= 0.5, np.nan, wtmp)
        del land
    except Exception:                      # noqa: BLE001 - mask absent -> show all
        pass
    out["sst_c"] = wtmp - 273.15
    del wtmp
    out["tropt_c"] = _f8(_read_2d(":TMP:tropopause:", "t")) - 273.15
    # PASS-THROUGH (stored exactly as decoded) -> float32.
    out["cape_jkg"] = _as_store(_read_2d(":CAPE:surface:", "cape"))
    out["lhtfl_wm2"] = _as_store(_read_2d(":LHTFL:surface:", "lhtfl"))
    out["srh_03km"] = _as_store(_read_2d(":HLCY:3000-0 m above ground:", "hlcy"))

    # --- deep-layer shear (a simple layer wind difference; m/s -> kt) ---
    # Derived, so float64 throughout; the level winds are dropped as soon as the
    # vectors are built rather than living to the end of the function.
    lev_winds = {}
    for lev in ENV_WIND_LEVELS:
        lev_winds[lev] = (_f8(_read_2d(f":UGRD:{lev} mb:", "u")) * KT_PER_MS,
                          _f8(_read_2d(f":VGRD:{lev} mb:", "v")) * KT_PER_MS)
    out["u_200"], out["v_200"] = lev_winds[200]      # PV map's 200 mb wind barbs
    for up, lo in SHEAR_LAYERS:
        du = lev_winds[up][0] - lev_winds[lo][0]
        dv = lev_winds[up][1] - lev_winds[lo][1]
        out[f"shru_{up}_{lo}"] = du
        out[f"shrv_{up}_{lo}"] = dv
        out[f"shrmag_{up}_{lo}"] = np.hypot(du, dv)
    lev_winds.clear()

    # --- thin pressure stack for the 200 mb PV calc (computed after reorder) ---
    lev_alt = "(" + "|".join(str(lv) for lv in PV_STACK_LEVELS) + ")"
    p_t, out["_pv_t"] = _read_levels(f":TMP:{lev_alt} mb:", "t")
    _, out["_pv_u"] = _read_levels(f":UGRD:{lev_alt} mb:", "u")
    _, out["_pv_v"] = _read_levels(f":VGRD:{lev_alt} mb:", "v")
    out["_pv_p"] = p_t
    return out


def _pv_at_level(p_hpa, t_stack, u_stack, v_stack, lat, lon, level) -> np.ndarray:
    """200 mb baroclinic potential vorticity (PVU) via MetPy, smoothed.

    ``potential_vorticity_baroclinic`` is grid-vectorized (operates on the whole
    (level, y, x) stack at once, NOT per-column), so this is one cheap call. The
    requested map level is picked from the stack by nearest pressure; the result
    is x1e6 (SI -> PVU) and lightly Gaussian-smoothed before contouring."""
    import metpy.calc as mpcalc
    from metpy.units import units
    from scipy.ndimage import gaussian_filter

    order = np.argsort(p_hpa)[::-1]                 # descending pressure (sfc->top)
    # Widen the decoded float32 stacks HERE, so every derivative MetPy takes runs
    # in float64 exactly as it always did. The stacks arrive float32 purely to
    # keep the ingest's largest transient half-size (see _read_levels); widening
    # is exact, so the arrays below are bit-for-bit the old ones.
    p = p_hpa[order]
    t = np.asarray(t_stack[order], dtype=np.float64)
    u = np.asarray(u_stack[order], dtype=np.float64)
    v = np.asarray(v_stack[order], dtype=np.float64)
    pres = (p[:, None, None] * np.ones_like(t)) * units.hPa
    theta = mpcalc.potential_temperature(pres, t * units.K)
    dx, dy = mpcalc.lat_lon_grid_deltas(lon, lat)
    # lat_lon_grid_deltas returns 2-D deltas (ny, nx-1)/(ny-1, nx); add a leading
    # level axis so they BROADCAST over the (nlev, ny, nx) stack - without it
    # MetPy's first_derivative indexes a 2-D delta with a 3-D slice and raises.
    dx, dy = dx[np.newaxis, ...], dy[np.newaxis, ...]
    lat2d = (np.ones((lat.size, lon.size)) * lat[:, None]) * units.degrees
    pv = mpcalc.potential_vorticity_baroclinic(
        theta, pres, u * units("m/s"), v * units("m/s"),
        dx=dx, dy=dy, latitude=lat2d, vertical_dim=0, x_dim=-1, y_dim=-2)
    pv = np.asarray(
        pv.to("kelvin * meter ** 2 / (kilogram * second)").magnitude)
    k = int(np.argmin(np.abs(p - level)))
    field = pv[k] * 1e6                             # SI -> PVU
    finite = np.isfinite(field)
    if finite.any():                                # smooth before contouring
        filled = np.where(finite, field, float(np.nanmean(field[finite])))
        field = gaussian_filter(filled, sigma=1.2)
        field = np.where(finite, field, np.nan)
    return field


def _finalize_env(env: dict, lat: np.ndarray, lon: np.ndarray) -> None:
    """Turn the transient ``_pv_*`` pressure stack into the cached ``pv_200``
    field and drop the stack. Runs AFTER the lat/lon reorder so the PV calc sees
    the final ascending latitudes. A PV failure degrades to an all-NaN field
    (the product then renders empty rather than sinking the frame)."""
    p = env.pop("_pv_p", None)
    t = env.pop("_pv_t", None)
    u = env.pop("_pv_u", None)
    v = env.pop("_pv_v", None)
    pv = None
    if p is not None and t is not None and u is not None and v is not None:
        try:
            pv = _pv_at_level(p, t, u, v, lat, lon, PV_MAP_LEVEL)
        except Exception as e:                      # noqa: BLE001
            log.warning("env PV compute failed: %s: %s", type(e).__name__, e)
    # Derived (a MetPy solve) -> stays float64, per the dtype policy.
    env["pv_200"] = pv if pv is not None else np.full((lat.size, lon.size), np.nan)


# ---------------------------------------------------------------------------
# Wind-speed colormap - vivid, high-contrast TAT table. A LinearSegmentedColormap
# normalized over 0 to 165 kt: deep indigo (calm), through blues and teal, to
# green and lime at the TS threshold, yellow and orange across Cat1-2, hot red
# and magenta-pink through Cat3-4, a purple break exactly at the Cat5 threshold
# (137 kt), and a pale-violet cap. A high N keeps the fill smooth; winds above
# 165 kt fold into set_over. The SSHWS thresholds (34/64/83/96/113/137 kt) still
# drive the colorbar ticks.
# ---------------------------------------------------------------------------
WIND_VMAX_KT = 165.0
WIND_OVER_COLOR = "#f3e0ff"
# (kt, hex) anchors; positions are normalized by WIND_VMAX_KT in _wind_cmap_norm.
_WIND_ANCHORS_KT = [
    (0,   "#14245f"),   # calm, deep indigo
    (12,  "#1f5fd0"),
    (22,  "#15a8e0"),
    (30,  "#14d6c0"),
    (34,  "#1fd17a"),   # TS threshold
    (42,  "#4ee23f"),
    (52,  "#b6f02a"),
    (64,  "#ffe617"),   # C1
    (74,  "#ffae12"),
    (83,  "#ff7d0a"),   # C2
    (96,  "#ff2f1c"),   # C3
    (113, "#ff1f8c"),   # C4, hot magenta-pink
    (125, "#e62ac0"),   # upper C4, magenta
    (137, "#9b30ee"),   # C5, purple break starts exactly here
    (150, "#bf72f2"),
    (165, "#f3e0ff"),   # extreme cap
]


def _wind_cmap_norm():
    """Vivid continuous 0-165 kt wind colormap and its Normalize."""
    anchors = [(kt / WIND_VMAX_KT, hexc) for kt, hexc in _WIND_ANCHORS_KT]
    cmap = mcolors.LinearSegmentedColormap.from_list("tat_wind", anchors, N=512)
    cmap.set_over(WIND_OVER_COLOR)
    cmap.set_under(_WIND_ANCHORS_KT[0][1])
    cmap.set_bad(alpha=0.0)  # NaN padding -> transparent (shows panel bg)
    norm = mcolors.Normalize(vmin=0.0, vmax=WIND_VMAX_KT)
    return cmap, norm


# ---------------------------------------------------------------------------
# Reflectivity colortable from a .pal file (source of truth)
# ---------------------------------------------------------------------------
# A GRLevelX-style ``.pal`` color table parsed into a DISCRETE matplotlib
# colormap. NOT HAFS-specific: any file with ``step:`` and ``solidcolor:`` lines
# parses, so the same ``assets/TAT-radar.pal`` will drive the future site radar
# viewer. ``steps`` are the per-bin START values (dBZ); ``colors`` are RGBA; a
# change to the palette is a new ``.pal``, no code edit.
PalCmap = namedtuple("PalCmap", ["cmap", "norm", "steps", "colors", "step"])

REFL_PAL_PATH = HERE / "assets" / "TAT-radar.pal"
# The colorbar shows discrete blocks over [first step, REFL_CBAR_TOP]; values
# above fold into a single white set_over arrow rather than a long run of
# identical white blocks (the top of the table is solid white above ~70 dBZ).
REFL_CBAR_TOP = 70


def load_pal_cmap(pal_path, *, under=(0.0, 0.0, 0.0, 0.0), over=None) -> PalCmap:
    """Parse a ``.pal`` color table into a discrete ListedColormap + BoundaryNorm.

    Reads each ``solidcolor: <value> <r> <g> <b>`` line (0-255 channels). Each
    color is held FLAT across the bin ``[value, value + step)`` - stepped, NOT
    interpolated. Bin edges fall at every step value plus a final edge at
    ``last + step``. Values below the first edge map to ``under`` (default fully
    transparent so non-precip shows the map beneath); values above the top map
    to ``over`` (default = the top color, which for a radar table is white).

    Reusable / not HAFS-specific - parse a file once and hand the cmap/norm to
    any field in dBZ.
    """
    steps: list[float] = []
    colors: list[tuple] = []
    step: Optional[float] = None
    for raw in Path(pal_path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        key, _, rest = line.partition(":")
        key, rest = key.strip().lower(), rest.strip()
        if key == "step":
            try:
                step = float(rest.split()[0])
            except (ValueError, IndexError):
                pass
        elif key == "solidcolor":
            parts = rest.replace(",", " ").split()
            if len(parts) < 4:
                continue
            val, r, g, b = (float(parts[0]), float(parts[1]),
                            float(parts[2]), float(parts[3]))
            steps.append(val)
            colors.append((r / 255.0, g / 255.0, b / 255.0, 1.0))
    if not colors:
        raise ValueError(f"no solidcolor entries parsed from {pal_path}")
    if step is None:  # derive from spacing when the header omits it
        step = (steps[1] - steps[0]) if len(steps) > 1 else 5.0
    edges = steps + [steps[-1] + step]
    cmap = mcolors.ListedColormap(colors, name=f"pal_{Path(pal_path).stem}")
    cmap.set_under(under)
    cmap.set_over(over if over is not None else colors[-1])
    cmap.set_bad(alpha=0.0)
    norm = mcolors.BoundaryNorm(edges, ncolors=len(colors))
    return PalCmap(cmap=cmap, norm=norm, steps=steps, colors=colors, step=step)


def _refl_pal() -> PalCmap:
    """Discrete reflectivity colormap from ``assets/TAT-radar.pal`` (parsed at
    render time so a palette change is a .pal edit, no code change)."""
    return load_pal_cmap(REFL_PAL_PATH)


def _refl_colorbar(pal: PalCmap, top: float = REFL_CBAR_TOP):
    """Discrete colorbar artifacts from a PalCmap: one block per step over
    ``[first, top]``, with set_over capping everything above ``top`` as a single
    white arrow. Returns ``(cmap, norm, tick_edges)`` ready for ``fig.colorbar``.
    """
    keep = [(s, c) for s, c in zip(pal.steps, pal.colors) if s < top]
    cb_colors = [c for _, c in keep]
    cb_edges = [s for s, _ in keep] + [float(top)]
    cb_cmap = mcolors.ListedColormap(cb_colors)
    cb_cmap.set_over(pal.cmap.get_over())
    cb_cmap.set_under((0.0, 0.0, 0.0, 0.0))
    cb_norm = mcolors.BoundaryNorm(cb_edges, ncolors=len(cb_colors))
    return cb_cmap, cb_norm, cb_edges


# ---------------------------------------------------------------------------
# Quantity-registry binding.
#
# The quantity registry (tat_palettes.quantities) owns every rendered
# quantity's globally-fixed SCALE, which is what keeps a cross-model comparison
# honest. Two ramps are built HERE rather than there - the wind ramp, and the
# reflectivity table parsed from the .pal asset at render time - so this module
# completes those two entries at import instead of the registry importing the
# renderer. See tat_palettes.quantities.bind_cmap.
# ---------------------------------------------------------------------------
def _bind_quantity_cmaps() -> None:
    from tat_palettes import quantities as tq
    tq.bind_cmap("wind_speed_kt", lambda: _wind_cmap_norm()[0])
    tq.bind_cmap("reflectivity_dbz", lambda: _refl_pal().cmap)


_bind_quantity_cmaps()


# ---------------------------------------------------------------------------
# Herbie HAFS template override (AWS-first, no live storm-name dependency)
# ---------------------------------------------------------------------------
def install_hafs_templates() -> None:
    """Monkeypatch ``herbie.models.hafsa``/``hafsb`` with AWS-first templates.

    Idempotent. See module docstring for why the stock template is unusable.
    The S3 key layout (verified against the live bucket) is::

        hfs{a,b}/{YYYYMMDD}/{HH}/{storm}.{YYYYMMDDHH}.hfs{a,b}.{product}.f{FFF}.grb2

    NOMADS keeps the same name with a ``hfs{a,b}.{YYYYMMDD}/{HH}/`` directory and
    serves as a recent-cycle fallback.
    """
    import herbie.models as models

    def _make_template(flavor: str):
        def template(self):  # called as models.hafsX.template(herbie_instance)
            storm = str(self.storm).lower()
            self.DESCRIPTION = f"Hurricane Analysis and Forecast System (HAFS-{flavor.upper()})"
            self.DETAILS = {
                "AWS Open Data": "https://registry.opendata.aws/noaa-nws-hafs-pds/",
                "HFIP": "https://hfip.org/hafs",
            }
            # Map each product key to itself - the stock template derived these
            # from a live storm-name lookup we deliberately drop. The fetch path
            # uses self.product directly, so the values are cosmetic.
            self.PRODUCTS = {
                "storm.atm": "storm-following atmospheric nest (~2 km)",
                "parent.atm": "parent atmospheric domain (~6 km)",
                "storm.sat": "storm-following synthetic satellite",
                "parent.sat": "parent synthetic satellite",
            }
            fname = (f"{storm}.{self.date:%Y%m%d%H}.hfs{flavor}"
                     f".{self.product}.f{self.fxx:03d}.grb2")
            self.SOURCES = {
                "aws": (f"https://noaa-nws-hafs-pds.s3.amazonaws.com/"
                        f"hfs{flavor}/{self.date:%Y%m%d}/{self.date:%H}/{fname}"),
                "nomads": (f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/hafs/prod/"
                           f"hfs{flavor}.{self.date:%Y%m%d}/{self.date:%H}/{fname}"),
            }
            self.IDX_SUFFIX = [".grb2.idx"]
            self.EXPECT_IDX_FILE = "remote"
            self.LOCALFILE = f"{self.get_remoteFileName}"

        return template

    for name, flavor in (("hafsa", "a"), ("hafsb", "b")):
        cls = type(name, (), {"template": _make_template(flavor)})
        setattr(models, name, cls)


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------
@dataclass
class HafsFrame:
    model: str            # "hafsa" / "hafsb"
    storm: str            # "13l"
    product: str          # "storm.atm" / "parent.atm"
    fxx: int
    init_time: dt.datetime
    valid_time: dt.datetime
    lon: np.ndarray       # 1-D, -180..180, ascending, trimmed to finite extent
    lat: np.ndarray       # 1-D, ascending, trimmed
    mslp_hpa: np.ndarray  # (lat, lon)
    wind_kt: np.ndarray   # (lat, lon) wind speed magnitude, knots
    u_kt: np.ndarray      # (lat, lon) 10 m eastward wind, knots (for barbs)
    v_kt: np.ndarray      # (lat, lon) 10 m northward wind, knots (for barbs)
    extent: tuple         # (lon_min, lon_max, lat_min, lat_max) of finite data
    # Composite radar reflectivity (dBZ), (lat, lon). Only fetched when the
    # caller asks for the reflectivity product; None for a wind-only fetch.
    refl_dbz: Optional[np.ndarray] = None
    # Simulated-satellite brightness temperature (DEGREES CELSIUS), (lat, lon),
    # on the SAME grid as mslp/wind (the .sat and .atm nests are grid-identical),
    # so it is trimmed in lockstep. One GRIB channel from the sibling .sat file
    # (Clean IR band 13 or a Water Vapor band). None unless ``sat_parm`` is set.
    bt_c: Optional[np.ndarray] = None
    # Total-column precipitable water (mm, == kg/m^2), (lat, lon), from the
    # ``:PWAT:entire atmosphere:`` message of the SAME ``.atm`` file as MSLP/wind.
    # A single-layer field like reflectivity. Only fetched when the caller asks
    # for the PWAT product; None for any other fetch.
    pwat: Optional[np.ndarray] = None
    # Pressure-level (upper-air) fields, render-ready, keyed by name
    # (``upper_field_names()``): gh/u/v at 850/700/500 mb, relative vorticity at
    # 850/500 mb, and the 700-300 mb layer-mean RH. Each is a (lat, lon) array on
    # the SAME trimmed grid as mslp/wind. None unless fetched with want_upper; no
    # product consumes these yet (Phase 2 shared plumbing).
    upper: Optional[dict] = None
    # PARENT-domain environmental fields, render-ready, keyed by name
    # (``env_field_names()``): total precip (in), SST (degC), tropopause T (degC),
    # surface CAPE (J/kg), latent-heat flux (W/m^2), 0-3 km SRH (m^2/s^2), 200 mb
    # PV (PVU) + 200 mb wind (kt), and the 200-850 / 500-850 mb shear vectors +
    # magnitude (kt). Each is a (lat, lon) array on the SAME trimmed grid as
    # mslp/wind. None unless fetched with want_env; consumed only by the parent
    # synoptic environmental products (see hafs_registry).
    env: Optional[dict] = None


def _to_180(lon: np.ndarray) -> np.ndarray:
    return ((lon + 180.0) % 360.0) - 180.0


def _monotonic(a: np.ndarray) -> bool:
    d = np.diff(a)
    return bool(np.all(d > 0) or np.all(d < 0))


def _choose_lon_frame(raw: np.ndarray) -> np.ndarray:
    """Pick a MONOTONIC longitude axis for the nest.

    A regular contour/extent needs a monotonic X axis. Normal nests live in
    signed -180..180 (e.g. the Atlantic at -82..-52). A nest straddling the
    antimeridian (West Pacific) becomes non-monotonic under ``_to_180`` (it
    jumps +180 → -180), which would otherwise blow the extent out to ~360°. For
    those we keep a CONTINUOUS frame that runs past +180 (e.g. 168..188); the
    >180 values are labeled as °W by ``_lon_label`` and the basemap is wrapped
    into the same frame by ``_draw_feature_lines``.
    """
    lon180 = _to_180(np.asarray(raw, dtype=float))
    if _monotonic(lon180):
        return lon180
    cont = lon180.copy()
    cont[cont < 0] += 360.0       # stitch across the dateline → continuous
    if _monotonic(cont):
        return cont
    raw_f = np.asarray(raw, dtype=float)
    if _monotonic(raw_f):
        return raw_f
    return lon180                 # last resort - pathological grid


def _wrap_into(x: float, lon_min: float, lon_max: float) -> float:
    """Shift a basemap longitude by ±360 so it lands in the nest's frame.

    No-op when the extent is the usual -180..180 (so non-dateline plots are
    unchanged); for a continuous dateline frame (e.g. 168..188) it maps a
    coastline point at -175 (=185°E) to 185 so it draws in the right place.
    """
    while x < lon_min - 180.0:
        x += 360.0
    while x > lon_max + 180.0:
        x -= 360.0
    return x


def _read_upper_air(H, remove_grib: bool) -> dict:
    """Read the pressure-level fields the planned upper-air products need from an
    already-constructed ``.atm`` Herbie object, returning native-grid (pre-trim,
    pre-reorder) 2-D arrays.

    Each (variable, level) is one byte-range subset read with the spike-verified
    idx selectors. RH, u, and v are each pulled across the 17 layer levels in ONE
    multi-level read and collapsed to a single mass/pressure-weighted layer mean
    here (the raw levels are never returned) - RH gives ``rh_layer_700_300`` and
    the winds give ``ulayer_700_300`` / ``vlayer_700_300`` (m/s, for the RH
    product's barbs). The ABSV levels ARE returned (keys ``absv_<lev>``) so the
    caller can derive relative vorticity AFTER the lat reorder, using the final
    latitudes for the planetary term f; they are dropped before caching. cfgrib
    usually names the messages gh/u/v/r/absv, with a lone data-var fallback for
    any it leaves as ``unknown``.
    """
    out: dict[str, np.ndarray] = {}

    def _read_2d(search: str, prefer: str) -> np.ndarray:
        """One single-level read, in its DECODED float32 (see the dtype policy)."""
        ds = H.xarray(search, remove_grib=remove_grib)
        if isinstance(ds, list):
            ds = ds[0]
        var = prefer if prefer in ds.data_vars else list(ds.data_vars)[0]
        arr = np.ascontiguousarray(ds[var].values)
        ds.close()          # drop the cfgrib handle + its eccodes index now
        return arr

    def _layer_mean(search: str, prefer: str) -> np.ndarray:
        """ONE multi-level read over the 700-300 mb layer, collapsed to a
        mass/pressure-weighted (trapezoidal-in-pressure) vertical mean (2-D).

        The level SELECTION and ORDERING happen on the decoded float32 stack, in
        a single fancy-index, and the widening to float64 happens once on the
        result. That is bit-for-bit what selecting and ordering a pre-widened
        float64 stack produced - float32 -> float64 is exact and elementwise, so
        it commutes with permutation - but it never holds three full float64
        copies of a (17, ny, nx) stack at once, which on the parent grid was
        ~890 MB of the ingest's peak for a field that ends up 8.7 MB.
        """
        ds = H.xarray(search, remove_grib=remove_grib)
        if isinstance(ds, list):
            ds = ds[0]
        var = prefer if prefer in ds.data_vars else list(ds.data_vars)[0]
        da = ds[var]
        levname = "isobaricInhPa"
        p = da[levname].values.astype(float)
        keep = np.where(np.isin(np.round(p).astype(int),
                                np.array(RH_LAYER_LEVELS)))[0]
        p_keep = p[keep]
        order = np.argsort(p_keep)       # ascending pressure for the integral
        p_s = p_keep[order]
        stack = np.moveaxis(da.values, da.dims.index(levname), 0)[keep[order]]
        ds.close()
        del ds, da
        vals_s = np.asarray(stack, dtype=np.float64)
        del stack
        # Trapezoidal integral over pressure (axis 0), normalized by the layer
        # thickness. By hand (np.trapz removed in NumPy 2.x) -> version-independent.
        dp = np.diff(p_s)                              # (L-1,)
        seg = vals_s[1:] + vals_s[:-1]                 # (L-1, lat, lon)
        seg *= 0.5                                     # in place: one temp, not two
        del vals_s
        return np.tensordot(dp, seg, axes=(0, 0)) / (p_s[-1] - p_s[0])

    # PASS-THROUGH fields -> float32 (see the dtype policy).
    for lev in UPPER_LEVELS:
        out[f"gh_{lev}"] = _as_store(_read_2d(f":HGT:{lev} mb:", "gh"))
        out[f"u_{lev}"] = _as_store(_read_2d(f":UGRD:{lev} mb:", "u"))
        out[f"v_{lev}"] = _as_store(_read_2d(f":VGRD:{lev} mb:", "v"))
    for lev in VORT_LEVELS:
        # ABSV is TRANSIENT - the caller subtracts the planetary term after the
        # lat reorder and drops it - and that difference of two same-magnitude
        # ~1e-4 quantities is derived, so it is widened here and stays float64.
        out[f"absv_{lev}"] = _f8(_read_2d(f":ABSV:{lev} mb:", "absv"))

    # Layer means over the 17 levels (700..300 mb). RH selects on its own
    # isobaric levels; u/v use an explicit level alternation so the multi-level
    # read pulls ONLY those 17 (a bare :UGRD: would match all 45 isobaric levels).
    # DERIVED (a vertical integral) -> float64.
    lev_alt = "(" + "|".join(str(lev) for lev in RH_LAYER_LEVELS) + ")"
    out[RH_LAYER_NAME] = _layer_mean(f":RH:{lev_alt} mb:", "r")
    out["ulayer_700_300"] = _layer_mean(f":UGRD:{lev_alt} mb:", "u")
    out["vlayer_700_300"] = _layer_mean(f":VGRD:{lev_alt} mb:", "v")
    return out


def _read_raw_fields(
    model: str,
    storm: str,
    product: str,
    date: dt.datetime,
    fxx: int,
    save_dir: str,
    *,
    remove_grib: bool = False,
    want_refl: bool = False,
    want_pwat: bool = False,
    want_upper: bool = False,
    want_env: bool = False,
    sat_parms: Sequence[int] = (),
) -> dict:
    """INGEST STAGE core: fetch + decode each REQUIRED GRIB file ONCE and return
    the full reordered (pre-trim) field grid as a plain dict.

    This is the only place that touches the network/cfgrib. PRMSL + 10 m wind
    always come from the ``.atm`` file (two byte-range subset reads, meanSea vs
    heightAboveGround, so cfgrib doesn't reconcile two hypercubes). ``want_refl``
    adds the ``:REFC:`` read from the SAME ``.atm``; ``want_pwat`` likewise adds
    the ``:PWAT:entire atmosphere:`` read (kg/m^2 == mm, stored straight, no unit
    conversion). ``sat_parms`` (GRIB2
    parameterNumbers) each add a read from the SIBLING ``.sat`` file (one Herbie
    object for the file, one byte-range read per channel) - Kelvin is converted
    to degC. ``want_upper`` adds the pressure-level fields (see ``_read_upper_air``
    + the derived relative vorticity below). Decoding the union of every product's
    fields here, ONCE per frame, is what lets the cache serve all products without
    re-fetching the shared GRIB.

    Returns a dict of full-grid (ascending lat/lon, untrimmed) arrays:
    ``lon, lat, mslp_hpa, wind_kt, u_kt, v_kt`` always; ``refl_dbz`` when
    ``want_refl``; ``bt`` is ``{parm: array_degC}`` for each requested channel;
    plus ``init_time``/``valid_time``. ``_pack_frame`` (the RENDER-side trim +
    guard) turns this into the exact HafsFrame ``fetch_hafs_frame`` would build -
    so storing this dict (see hafs_cache) and packing later is byte-identical to
    a direct fetch. The trim is deliberately NOT done here so the cached grid is
    product-neutral and one cache entry feeds every product's own trim.
    """
    import herbie

    install_hafs_templates()
    H = herbie.Herbie(
        date, model=model, storm=storm, product=product, fxx=fxx,
        priority=["aws", "nomads"], save_dir=save_dir, verbose=False,
    )
    if H.grib is None:
        raise FileNotFoundError(
            f"no HAFS GRIB found for {model} {storm} {product} "
            f"{date:%Y-%m-%d %HZ} f{fxx:03d}"
        )

    ds_p = H.xarray(":PRMSL:mean sea level:", remove_grib=remove_grib)
    ds_w = H.xarray(":(UGRD|VGRD):10 m above ground:", remove_grib=remove_grib)
    if isinstance(ds_p, list):
        ds_p = ds_p[0]
    if isinstance(ds_w, list):
        ds_w = ds_w[0]

    refl = None
    if want_refl:
        ds_r = H.xarray(":REFC:", remove_grib=remove_grib)
        if isinstance(ds_r, list):
            ds_r = ds_r[0]
        # cfgrib names the message 'refc'; fall back to the lone data var.
        rvar = "refc" if "refc" in ds_r.data_vars else list(ds_r.data_vars)[0]
        refl = _as_store(ds_r[rvar].values)
        ds_r.close()
        del ds_r

    pwat = None
    if want_pwat:
        # Total-column precipitable water from the SAME .atm file (one more
        # byte-range subset read). Single-layer, like REFC; cfgrib names the
        # message 'pwat', with a lone-var fallback. The field is kg/m^2 which
        # equals mm of water, so NO unit conversion - stored straight as mm.
        # idx wording is "PWAT:entire atmosphere (considered as a single layer):"
        # so the search omits a trailing colon after "atmosphere" (it is a regex
        # str.contains match); this is the lone PWAT message in the .atm file.
        ds_pw = H.xarray(":PWAT:entire atmosphere", remove_grib=remove_grib)
        if isinstance(ds_pw, list):
            ds_pw = ds_pw[0]
        pvar = "pwat" if "pwat" in ds_pw.data_vars else list(ds_pw.data_vars)[0]
        pwat = _as_store(ds_pw[pvar].values)
        ds_pw.close()
        del ds_pw

    bt: dict[int, np.ndarray] = {}
    if sat_parms:
        sat_product = product.replace(".atm", ".sat")
        H2 = herbie.Herbie(
            date, model=model, storm=storm, product=sat_product, fxx=fxx,
            priority=["aws", "nomads"], save_dir=save_dir, verbose=False,
        )
        if H2.grib is None:
            raise FileNotFoundError(
                f"no HAFS sat GRIB found for {model} {storm} {sat_product} "
                f"{date:%Y-%m-%d %HZ} f{fxx:03d}"
            )
        for parm in sat_parms:
            # cfgrib can't name the message (missing local table) -> the lone
            # data var is 'unknown'. Select by parameterNumber via the idx regex.
            ds_s = H2.xarray(f"parm={parm}:", remove_grib=remove_grib)
            if isinstance(ds_s, list):
                ds_s = ds_s[0]
            svar = list(ds_s.data_vars)[0]
            vals = ds_s[svar].values.astype(float)   # derived (K -> degC below)
            ds_s.close()
            del ds_s
            # MASK FILL before anything: the GRIB missingValue (9999) marks the
            # ~56% of the storm-nest grid that is off-nest fill. cfgrib usually
            # pre-masks it to NaN, but mask >=9990 defensively so fill never
            # enters stats / PCT / colorize on any decode path. Applied to ALL
            # bt channels (each parm here).
            vals[vals >= 9990.0] = np.nan
            bt[int(parm)] = vals - 273.15  # K -> degC (derived -> float64)
            del vals

    # Pressure-level (upper-air) fields from the SAME .atm file. Native-grid 2-D
    # arrays incl. the transient ABSV levels; relative vorticity is derived below
    # (after the lat reorder) and ABSV is then dropped, so it is never cached.
    upper: dict[str, np.ndarray] = {}
    if want_upper:
        upper = _read_upper_air(H, remove_grib)

    # PARENT-domain environmental fields (parent-only products). Native-grid 2-D
    # render-ready arrays plus the transient ``_pv_*`` 3-D stack the PV calc
    # consumes after the lat reorder (see _finalize_env), so it is never cached.
    env: dict[str, np.ndarray] = {}
    if want_env:
        env = _read_env_fields(H, remove_grib)

    lat = ds_p["latitude"].values
    # Monotonic longitude frame (continuous past +180 for dateline-crossing
    # West Pacific nests; plain signed -180..180 otherwise).
    lon = _choose_lon_frame(ds_p["longitude"].values)

    mslp = _as_store(ds_p["prmsl"].values / 100.0)  # Pa -> hPa
    # Keep the vector components (for barbs) AND the magnitude (for the fill);
    # all three are reordered/trimmed in lockstep below so they stay aligned.
    u_kt = _as_store(ds_w["u10"].values * KT_PER_MS)
    v_kt = _as_store(ds_w["v10"].values * KT_PER_MS)
    wind = np.hypot(u_kt, v_kt)
    # init/valid time come off ds_p, which stays open until they are read below.
    ds_w.close()
    del ds_w

    # Ensure ascending lat/lon so contour/imshow orient correctly. Reordering
    # the axes only reorders grid columns/rows; the physical u (eastward) and
    # v (northward) values at each point are unchanged, so we slice them the
    # same way as wind/mslp without any sign flip.
    if lat[0] > lat[-1]:
        lat = lat[::-1]
        mslp = mslp[::-1, :]
        wind = wind[::-1, :]
        u_kt = u_kt[::-1, :]
        v_kt = v_kt[::-1, :]
        if refl is not None:
            refl = refl[::-1, :]
        if pwat is not None:
            pwat = pwat[::-1, :]
        bt = {p: a[::-1, :] for p, a in bt.items()}
        upper = {k: a[::-1, :] for k, a in upper.items()}
        # env holds 2-D fields AND the 3-D _pv_* stack; reverse the LAT axis
        # (axis -2) for both, leave the 1-D _pv_p level vector untouched.
        env = {k: (a[..., ::-1, :] if getattr(a, "ndim", 0) >= 2 else a)
               for k, a in env.items()}
    if lon[0] > lon[-1]:
        lon = lon[::-1]
        mslp = mslp[:, ::-1]
        wind = wind[:, ::-1]
        u_kt = u_kt[:, ::-1]
        v_kt = v_kt[:, ::-1]
        if refl is not None:
            refl = refl[:, ::-1]
        if pwat is not None:
            pwat = pwat[:, ::-1]
        bt = {p: a[:, ::-1] for p, a in bt.items()}
        upper = {k: a[:, ::-1] for k, a in upper.items()}
        env = {k: (a[..., :, ::-1] if getattr(a, "ndim", 0) >= 2 else a)
               for k, a in env.items()}

    # Derive relative vorticity = ABSV - f now that lat is final/ascending, so
    # the planetary term f = 2*Omega*sin(phi) lines up per-pixel with the
    # (reordered) ABSV grid. Cyclonic flow in the NH is POSITIVE relative
    # vorticity. ABSV is consumed here and never cached. f broadcasts over lon.
    if want_upper:
        f = 2.0 * EARTH_OMEGA * np.sin(np.deg2rad(lat.astype(float)))
        for lev in VORT_LEVELS:
            absv = upper.pop(f"absv_{lev}")
            # Derived, so the float64 difference is what gets cached (absv is
            # widened upstream for exactly this).
            upper[f"relvort_{lev}"] = absv - f[:, None]
            del absv

    # Compute 200 mb PV from the (now lat-final) pressure stack and drop the
    # transient _pv_* stack so only render-ready 2-D env fields are returned.
    if want_env:
        _finalize_env(env, lat.astype(float), lon.astype(float))

    init_time = (ds_p["time"].values.astype("datetime64[s]").astype(dt.datetime))
    valid_time = (ds_p["valid_time"].values.astype("datetime64[s]").astype(dt.datetime))
    ds_p.close()
    del ds_p

    return {
        "model": model, "storm": storm, "product": product, "fxx": fxx,
        "init_time": init_time, "valid_time": valid_time,
        "lon": lon, "lat": lat, "mslp_hpa": mslp, "wind_kt": wind,
        "u_kt": u_kt, "v_kt": v_kt, "refl_dbz": refl, "pwat": pwat, "bt": bt,
        "upper": upper, "env": env,
    }


# --- 89 GHz Polarization-Corrected Temperature (PCT85) ---------------------
# The RAW 91.7 GHz H-pol channel reads a GREEN ocean: the low-emissivity H-pol
# OCEAN depression sits near 227 K (-45 degC). The canonical NRL/CIMSS "89 GHz
# color" (the blue-ocean look) is the POLARIZATION-CORRECTED
# temperature, NOT a single channel: PCT = 1.818*V - 0.818*H, where V/H are the
# two SSMIS-F17 91.7 GHz channels (parm 63 = V warmer-over-ocean, parm 62 = H).
# It removes the ocean polarization signal (clear ocean -> ~270-281 K / blue)
# while ice-scattering cores stay cold and pop. The coefficients sum to 1, so
# PCT_degC = 1.818*V_degC - 0.818*H_degC exactly (compute directly in degC).
PCT_V_COEF = 1.818
PCT_H_COEF = 0.818
PCT_CLEAR_OCEAN_C = -23.15   # Tb > 250 K (both channels) -> a clear-ocean pixel
PCT_CLIP_LO_C = -168.15      # 105 K NRL scattering floor (CRTM single-pixel overshoots)
PCT_CLIP_HI_C = 16.85        # 290 K warm ceiling


def compute_pct89(bt: dict, v_parm: int, h_parm: int):
    """Polarization-corrected 89 GHz brightness temperature (PCT85), in degC,
    from the decoded V/H channels in ``bt`` (a {parm: degC array} dict).

    PCT = 1.818*V - 0.818*H, where V is the channel WARMER over clear ocean (the
    SSMIS V-pol; parm 63 on HAFS .sat, H=parm 62). A SELF-CHECK enforces that
    orientation directly from the clear-ocean medians and swaps V/H if the data
    labels them the other way, so the result is robust either way. (NB: a
    median-of-PCT sanity check does NOT work here -- over clear ocean V ~= H, so
    PCT ~= 280 K for EITHER assignment; the damage of a flip shows only over
    convection, so the warmer-channel test is what actually catches it.) The
    result is CLIPPED to the NRL physical range [105, 290] K so single-pixel CRTM
    scattering overshoots don't blow past the colorbar / MIN-BT readout. NaN-safe
    (fill stays NaN). Returns None if either channel is absent."""
    V = bt.get(int(v_parm))
    H = bt.get(int(h_parm))
    if V is None or H is None:
        return None
    # The correction is a DIFFERENCE of two same-magnitude channels, so it is
    # pinned to float64 regardless of how the channels arrive (they are cached
    # float64 today; this keeps the math right if that ever changes).
    V = np.asarray(V, dtype=np.float64)
    H = np.asarray(H, dtype=np.float64)
    clear = (V > PCT_CLEAR_OCEAN_C) & (H > PCT_CLEAR_OCEAN_C)
    if clear.any() and np.nanmedian(H[clear]) > np.nanmedian(V[clear]):
        V, H = H, V          # V must be the warmer-over-clear-ocean channel
    pct = PCT_V_COEF * V - PCT_H_COEF * H
    return np.clip(pct, PCT_CLIP_LO_C, PCT_CLIP_HI_C)


def _pack_frame(raw: dict, *, want_refl: bool = False,
                want_pwat: bool = False, want_upper: bool = False,
                want_env: bool = False,
                sat_parm: Optional[int] = None,
                sat_pct: "tuple | None" = None) -> HafsFrame:
    """RENDER-side core: trim a raw field grid (from ``_read_raw_fields`` or the
    field cache) to its finite extent and return the HafsFrame ``render_frame``
    consumes. Pure CPU - no network. Identical math whether ``raw`` is a freshly
    decoded full grid or a cache entry, so the produced HafsFrame is byte-for-byte
    what ``fetch_hafs_frame`` returned before the ingest/render split.

    ``want_refl`` / ``sat_parm`` select which optional fields this product needs:
    refl from ``raw['refl_dbz']``, the BT channel from ``raw['bt'][sat_parm]``.
    The finite-mask trim and the degenerate-BT guard match the pre-split fetch
    exactly (the bbox is built from mslp|wind, plus bt when present).
    """
    lon, lat = raw["lon"], raw["lat"]
    # RENDER BOUNDARY (see the dtype policy near STORE_DTYPE): every field that
    # reached matplotlib as float64 before the cache went float32 must reach it
    # as float64 still. Not cosmetic - streamline integration is an ODE solve and
    # amplifies a last-ulp dtype difference into a different trajectory, so a
    # float32 field with BIT-IDENTICAL values still moved 0.8% of the pixels on
    # hgt_wind_700 and 5.0% on env_shear_500_850 when this was measured.
    # mslp/wind/u/v are deliberately NOT widened: cfgrib hands back float32 and
    # NEP 50 keeps their unit conversions float32, so float32 is what the
    # renderer has always received for those four.
    # Applied to the TRIMMED slice below, not the full grid, so the widening
    # copies the sub-rectangle the product actually draws. A field already at
    # RENDER_DTYPE (the derived ones) passes through untouched, no copy.
    def _r8(a):
        return None if a is None else np.asarray(a, dtype=RENDER_DTYPE)

    mslp, wind = raw["mslp_hpa"], raw["wind_kt"]
    u_kt, v_kt = raw["u_kt"], raw["v_kt"]
    refl = raw["refl_dbz"] if want_refl else None
    pwat = raw.get("pwat") if want_pwat else None
    upper = raw.get("upper") if want_upper else None
    env = raw.get("env") if want_env else None
    # ``sat_pct`` (V,H parms) -> a derived POLARIZATION-CORRECTED channel computed
    # from two cached BT channels; otherwise a single channel by sat_parm. Either
    # way the result is the degC field this product colorizes (frame.bt_c).
    if sat_pct is not None:
        bt = compute_pct89(raw["bt"], sat_pct[0], sat_pct[1])
    else:
        bt = raw["bt"].get(int(sat_parm)) if sat_parm is not None else None

    # Trim NaN padding: the nest is a sub-rectangle embedded in a NaN-filled
    # regular grid. Keep rows/cols that carry any finite data. The mask is built
    # from mslp|wind only (refl's no-echo fill is a finite -20, so including it
    # would defeat the trim); refl is sliced to the SAME rectangle in lockstep.
    # bt IS genuinely NaN outside the nest, so it's safe to fold into the mask
    # (guarantees no valid brightness-temperature pixels are clipped).
    finite = np.isfinite(mslp) | np.isfinite(wind)
    if bt is not None:
        finite = finite | np.isfinite(bt)
    rows = np.where(finite.any(axis=1))[0]
    cols = np.where(finite.any(axis=0))[0]
    if rows.size == 0 or cols.size == 0:
        raise ValueError("all-NaN field after fetch, unexpected")
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    lat, lon = lat[r0:r1], lon[c0:c1]
    mslp, wind = mslp[r0:r1, c0:c1], wind[r0:r1, c0:c1]
    u_kt, v_kt = u_kt[r0:r1, c0:c1], v_kt[r0:r1, c0:c1]
    if refl is not None:
        refl = _r8(refl[r0:r1, c0:c1])
    if pwat is not None:
        # PWAT lives on the same .atm grid as mslp/wind, so the mslp|wind finite
        # mask already bounds it; slice to the SAME rectangle in lockstep (like
        # refl) - it is not folded into the mask.
        pwat = _r8(pwat[r0:r1, c0:c1])
    if upper is not None:
        # Upper-air fields share the .atm grid; slice to the SAME rectangle in
        # lockstep (the mslp|wind mask already bounds them).
        upper = {k: _r8(a[r0:r1, c0:c1]) for k, a in upper.items()}
    if env is not None:
        # Env fields share the .atm grid too; slice to the SAME rectangle in
        # lockstep (the mslp|wind mask already bounds them).
        env = {k: _r8(a[r0:r1, c0:c1]) for k, a in env.items()}
    if bt is not None:
        # _r8 like every other optional field. A NO-OP today (bt is derived, so
        # it is cached float64 already) and kept deliberately: it is the only
        # field that reached matplotlib without passing the render boundary, and
        # the dtype policy above claims every field passes it. Narrowing bt to
        # float32 later - the obvious next storage saving, since its float32
        # Kelvin original is exact - would otherwise hand streamplot/contour a
        # float32 array with nothing to catch it and a comment saying it cannot
        # happen.
        bt = _r8(bt[r0:r1, c0:c1])
        # Degenerate-frame guard (mirrors the satellite render's scalar-IR guard):
        # a healthy sim-sat nest is ~fully finite inside its trimmed rectangle, so
        # a mostly-NaN or flat (no spread) field means the channel didn't render -
        # skip it rather than publish an empty panel.
        fin = np.isfinite(bt)
        if fin.mean() < 0.5 or not fin.any() or float(np.nanmax(bt) - np.nanmin(bt)) < 1.0:
            raise ValueError("simulated-BT field is mostly-NaN or flat, skipping")

    return HafsFrame(
        model=raw["model"], storm=raw["storm"], product=raw["product"],
        fxx=raw["fxx"], init_time=raw["init_time"], valid_time=raw["valid_time"],
        lon=lon, lat=lat, mslp_hpa=mslp, wind_kt=wind, u_kt=u_kt, v_kt=v_kt,
        extent=(float(lon.min()), float(lon.max()),
                float(lat.min()), float(lat.max())),
        refl_dbz=refl,
        bt_c=bt,
        pwat=pwat,
        upper=upper,
        env=env,
    )


def fetch_hafs_frame(
    model: str,
    storm: str,
    product: str,
    date: dt.datetime,
    fxx: int,
    save_dir: str,
    remove_grib: bool = False,
    want_refl: bool = False,
    want_pwat: bool = False,
    want_upper: bool = False,
    want_env: bool = False,
    sat_parm: Optional[int] = None,
    sat_pct: "tuple | None" = None,
) -> HafsFrame:
    """Fetch + decode + trim one HAFS frame for ONE product into a HafsFrame.

    Thin wrapper over ``_read_raw_fields`` (the GRIB reads) + ``_pack_frame``
    (the trim/guard), kept for the standalone CLI and any direct caller. The
    full-cycle builder no longer calls this per product - it ingests the union of
    fields once per frame into a field cache and renders from there (see
    hafs_cache / generate_hafs_plots) - but the output here is unchanged.

    ``remove_grib=True`` deletes each idx-subset GRIB after it is read into
    xarray (the builder sets it so hundreds of frames don't fill the runner disk;
    the standalone slice keeps them for inspection).
    """
    parms = tuple(sat_pct) if sat_pct is not None else (
        (sat_parm,) if sat_parm is not None else ())
    raw = _read_raw_fields(
        model, storm, product, date, fxx, save_dir,
        remove_grib=remove_grib, want_refl=want_refl, want_pwat=want_pwat,
        want_upper=want_upper, want_env=want_env, sat_parms=parms,
    )
    return _pack_frame(raw, want_refl=want_refl, want_pwat=want_pwat,
                       want_upper=want_upper, want_env=want_env,
                       sat_parm=sat_parm, sat_pct=sat_pct)


# ---------------------------------------------------------------------------
# Storm track (the run's OWN forecast track - its ATCF trak.atcfunix deck).
# This is what anchors the L marker, the parent crop, and the headline stats
# to the run's NAMESAKE storm instead of the domain extremum (which on the
# huge parent domain is routinely a DIFFERENT system).
# ---------------------------------------------------------------------------
def parse_atcf_track(text: str) -> dict:
    """``{tau: (lat, lon)}`` from an ATCF aid deck (trak.atcfunix).

    One fix per forecast hour: the deck repeats a tau once per wind-radii
    threshold (34/50/64 kt lines) with the same position, so the FIRST line per
    tau wins. Lat/lon are ATCF tenths-of-degree with hemisphere letters
    (``118N`` -> 11.8, ``1294W`` -> -129.4). Unparseable lines are skipped -
    a partial deck (tracker lost the storm) simply yields fewer taus, which the
    renderer degrades on honestly. Never raises."""
    track: dict = {}
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 8:
            continue
        try:
            tau = int(parts[5])
        except ValueError:
            continue
        if tau in track:
            continue          # wind-radii line for a tau already parsed
        lat_s, lon_s = parts[6], parts[7]
        try:
            lat = int(lat_s[:-1]) / 10.0 * (1.0 if lat_s[-1].upper() == "N" else -1.0)
            lon = int(lon_s[:-1]) / 10.0 * (1.0 if lon_s[-1].upper() == "E" else -1.0)
        except (ValueError, IndexError):
            continue
        track[tau] = (lat, lon)
    return track


def pick_track_fix(track: dict, prev_track: dict, fxx: int) -> tuple:
    """``(cen, anchor)`` for one forecast hour, from the run's OWN deck with a
    PREVIOUS-CYCLE fallback (progressive rendering: a frame can render before
    its tau is in the own deck).

    ``cen`` (full anchoring - L marker, crop, stat disc): the own-deck fix at
    ``fxx``; else the PROVISIONAL fix - the 6 h-older run's position at the
    SAME VALID TIME (``prev_track[fxx + 6]``). Cycle-over-cycle drift at a
    fixed valid time is far below the 3-deg stat disc, so the provisional
    anchor is visually exact; a brand-new storm has no previous deck and
    degrades honestly (cen=None).

    ``anchor`` (framing-only fallback when cen is None): the last known own
    fix at-or-before ``fxx``, else the last previous-cycle fix at-or-before
    the same valid time. No extrapolation - honestly "last tracked here"."""
    cen = track.get(fxx)
    if cen is None:
        cen = prev_track.get(fxx + 6)
    prior = [t for t in sorted(track) if t <= fxx]
    if prior:
        anchor = track[prior[-1]]
    else:
        pprior = [t for t in sorted(prev_track) if t <= fxx + 6]
        anchor = prev_track[pprior[-1]] if pprior else None
    return cen, anchor


def fetch_hafs_track(model: str, storm: str, cycle: dt.datetime,
                     session=None, timeout: float = 30.0) -> dict:
    """The namesake storm's forecast track for one (model, storm, cycle):
    ``{fxx: (lat, lon)}`` parsed from the run's own ``trak.atcfunix`` on the
    public bucket. Returns ``{}`` on ANY failure (missing deck, network) - the
    renderer then degrades honestly (no L on the parent, stats labeled
    domain-wide) rather than tagging a different system with the storm's id."""
    flavor = {"hafsa": "a", "hafsb": "b"}.get(model)
    if flavor is None:
        return {}
    url = (f"https://noaa-nws-hafs-pds.s3.amazonaws.com/hfs{flavor}/"
           f"{cycle:%Y%m%d}/{cycle:%H}/{storm.lower()}.{cycle:%Y%m%d%H}"
           f".hfs{flavor}.trak.atcfunix")
    try:
        import requests
        r = (session or requests).get(url, timeout=timeout)
        r.raise_for_status()
        return parse_atcf_track(r.text)
    except Exception as e:  # noqa: BLE001 - track is an enhancement, never fatal
        log.warning("storm track deck unavailable (%s) - rendering untracked: %s",
                    url, e)
        return {}


# ---------------------------------------------------------------------------
# Basemap (Natural Earth GeoJSON) - adapted from generate_sst_plots.py
# ---------------------------------------------------------------------------
def _load_geojson(name: str) -> Optional[dict]:
    p = HERE / name
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _feature_linestrings(feat: dict) -> list[list[tuple[float, float]]]:
    geom = feat.get("geometry") or {}
    t = geom.get("type")
    out: list[list[tuple[float, float]]] = []
    if t == "LineString":
        out.append([(p[0], p[1]) for p in geom["coordinates"]])
    elif t == "MultiLineString":
        for line in geom["coordinates"]:
            out.append([(p[0], p[1]) for p in line])
    elif t == "Polygon":
        for ring in geom["coordinates"]:
            out.append([(p[0], p[1]) for p in ring])
    elif t == "MultiPolygon":
        for poly in geom["coordinates"]:
            for ring in poly:
                out.append([(p[0], p[1]) for p in ring])
    return out


def _draw_feature_lines(ax, features, extent, color, linewidth, zorder,
                        halo: float = 0.0):
    """Plot GeoJSON line/polygon edges that intersect the extent.

    Coordinates are -180..180 (matching our converted lon). Each point is wrapped
    into the view's longitude frame via ``_wrap_into`` so dateline-crossing (West
    Pacific) views still get their coastlines; a no-op for ordinary -180..180
    extents. A feature is drawn if its bounding box overlaps the (margined)
    extent, and the axes clip path trims it to the view.

    Antimeridian split: wrapping each point independently can place two
    *consecutive* points on opposite sides of the wrap seam (one ~+180, the next
    ~-180), which ``ax.plot`` would otherwise join with a long horizontal stripe
    straight across the map. We insert a NaN wherever consecutive wrapped points
    jump more than 180 deg of longitude, lifting the pen so each real coastline
    segment draws on its own side and no stripe appears.

    ``halo`` > 0 strokes each line with a dark outline that wide (added to
    ``linewidth``), so a bright coastline stays legible over the colorful and
    pale regions of the simulated-satellite fills alike. 0 = no halo (unchanged
    for the wind / reflectivity products).
    """
    lon_min, lon_max, lat_min, lat_max = extent
    mlon = (lon_max - lon_min) * 0.05 + 1.0
    mlat = (lat_max - lat_min) * 0.05 + 1.0
    for feat in features:
        for ring in _feature_linestrings(feat):
            if len(ring) < 2:
                continue
            xs = np.array([_wrap_into(p[0], lon_min, lon_max) for p in ring],
                          dtype=float)
            ys = np.array([p[1] for p in ring], dtype=float)
            if (xs.max() < lon_min - mlon or xs.min() > lon_max + mlon
                    or ys.max() < lat_min - mlat or ys.min() > lat_max + mlat):
                continue
            # Break the polyline at every antimeridian seam crossing so the wrap
            # never draws a stripe across the whole frame.
            seam = np.where(np.abs(np.diff(xs)) > 180.0)[0] + 1
            if seam.size:
                xs = np.insert(xs, seam, np.nan)
                ys = np.insert(ys, seam, np.nan)
            lines = ax.plot(xs, ys, color=color, linewidth=linewidth,
                            zorder=zorder, solid_capstyle="round",
                            solid_joinstyle="round")
            if halo:
                for ln in lines:
                    ln.set_path_effects([pe.withStroke(
                        linewidth=linewidth + halo, foreground=SAT_COAST_HALO)])


def _fill_polygons(ax, features, extent, color, zorder):
    """Fill GeoJSON land polygons that intersect the extent (the canonical slate
    land fill drawn UNDER the data). Per-ring fill with the same lon-wrap + extent
    crop as _draw_feature_lines; a ring that straddles the antimeridian seam is
    skipped (rare for storm-centered nests) so no fill stripe is drawn - the coast
    line still outlines it."""
    lon_min, lon_max, lat_min, lat_max = extent
    mlon = (lon_max - lon_min) * 0.05 + 1.0
    mlat = (lat_max - lat_min) * 0.05 + 1.0
    for feat in features:
        for ring in _feature_linestrings(feat):
            if len(ring) < 3:
                continue
            xs = np.array([_wrap_into(p[0], lon_min, lon_max) for p in ring], dtype=float)
            ys = np.array([p[1] for p in ring], dtype=float)
            if (xs.max() < lon_min - mlon or xs.min() > lon_max + mlon
                    or ys.max() < lat_min - mlat or ys.min() > lat_max + mlat):
                continue
            if xs.size > 1 and np.abs(np.diff(xs)).max() > 180.0:
                continue   # seam-straddling ring: skip fill (avoid a stripe)
            ax.fill(xs, ys, color=color, zorder=zorder, linewidth=0, antialiased=True)


def _lon_label(x: float, _pos) -> str:
    v = x
    while v > 180:
        v -= 360
    while v < -180:
        v += 360
    iv = int(round(v))
    if iv in (0, 180, -180):
        return f"{abs(iv)}°"
    return f"{iv}°E" if iv > 0 else f"{-iv}°W"


def _lat_label(y: float, _pos) -> str:
    iv = int(round(y))
    if iv == 0:
        return "0°"
    return f"{abs(iv)}°{'N' if iv > 0 else 'S'}"


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
PRODUCT_LABEL = {
    "storm.atm": "Storm nest (~2 km)",
    "parent.atm": "Parent domain (~6 km)",
}
MODEL_LABEL = {"hafsa": "HAFS-A", "hafsb": "HAFS-B"}

# The per-product catalog - including each simulated-satellite channel's GRIB2
# parameterNumber and default tat_palettes enhancement - now lives in
# hafs_registry (ProductSpec). The .sat file carries brightness temperature in
# Kelvin under discipline=3, parmCategory=192; because the local NCEP table is
# missing, cfgrib cannot name the message, so fetch_hafs_frame selects it with an
# idx regex on ``parm=<N>:`` (sat_parm comes from the spec) and reads the lone
# (``unknown``) var. Clean IR = parm 58 (ABI band 13, 10.3 um); Water Vapor =
# parm 53 (ABI band 8, 6.2 um, upper WV). Mid (54) / low (55) WV exist in the
# same file but are deferred (one clean "Water Vapor" product for now).

# Colorbar ticks on the SSHWS category thresholds (kt) so the bar doubles as a
# Saffir-Simpson reference: 34 TS, 64 Cat1, 83 Cat2, 96 Cat3, 113 Cat4, 137 Cat5.
CBAR_TICKS_KT = [34, 64, 83, 96, 113, 137]

# Output raster density. Named (rather than inlined at savefig) because the
# GEOMETRY block render_frame returns converts the axes rect from figure
# fractions to PIXELS with it - if the two ever disagreed, every client-side
# pixel -> lon/lat mapping would be silently offset.
DPI = 155

# --- Figure layout, inches -------------------------------------------------
# Promoted from locals inside render_frame to module constants because the
# manifest now publishes the derived pixel canvas, and a client maps pixels to
# lon/lat with it. render_frame reads these same names, so the published
# numbers and the drawn figure cannot drift apart.
MAP_BOX_IN = 10.5                       # square map box (pinned: see render_frame)
LEFT_GUTTER_IN, CBAR_GUTTER_IN = 0.62, 1.55   # lat-label / colorbar gutters
BAND_IN, FOOT_IN, BOTPAD_IN = 0.74, 0.40, 0.06  # header / credit / bottom pad
FIG_W_IN = LEFT_GUTTER_IN + MAP_BOX_IN + CBAR_GUTTER_IN
FIG_H_IN = BOTPAD_IN + FOOT_IN + MAP_BOX_IN + BAND_IN
# Agg truncates the canvas to whole pixels, so int() (not round()) matches the
# file matplotlib actually writes.
IMAGE_W_PX = int(FIG_W_IN * DPI)
IMAGE_H_PX = int(FIG_H_IN * DPI)

# Target number of wind barbs across each axis. The per-axis stride is derived
# from the grid size, so the (fine, small) nest and the (coarse, large) parent
# both land near this count and stay readable.
BARB_TARGET = 17

# Storm-nest framing: instead of the old data-centered per-side trim (which left
# the storm off-center "in a sea of blue" once it had moved within its nest), the
# STORM NEST is cropped to a FIXED square window of NEST_VIEW_DEG degrees CENTERED
# ON THE STORM (the same track fix the L marker / stats / parent crop use), clamped
# to the nest data extent so the box never opens a NaN gutter at the nest edge.
# ~5.5 deg frames the inner core + primary bands like the operational reference products. One tunable knob;
# storm.atm ONLY (the parent keeps its own PARENT_HALF_DEG window).
NEST_VIEW_DEG = 5.5

# Parent-domain framing: the parent (~6 km) covers a huge, frame-to-frame
# variable area, so instead of plotting its full extent we crop every parent
# frame to a FIXED square window of (2 x PARENT_HALF_DEG) degrees centered on the
# storm (the MSLP minimum, the same center the L marker uses). 20 deg per side =
# a 40 x 40 deg synoptic view. The nest is untouched.
PARENT_HALF_DEG = 20.0

# When no storm track fix is supplied, the parent center falls back to the
# pressure minimum within this many degrees of the peak 10 m wind, so a deeper
# but far-off midlatitude low cannot capture the crop. Wide enough to span a
# large TC circulation, tight enough to exclude a separate synoptic low.
PARENT_WIND_SEARCH_DEG = 8.0

# Storm-anchored HEADLINE STATS: when the run's own track deck gives the
# namesake storm's position at a forecast hour, the header VMAX / MSLP min /
# MIN BT / peak dBZ etc. are reduced over the cells within this great-circle-ish
# radius (degrees; lon scaled by cos lat) of that fix, NOT over the whole
# domain. 3 deg ~ 330 km covers the inner core + primary bands of any TC while
# excluding the separate systems a 100-deg-wide parent domain always contains
# (the f123 "968 mb low near Mexico labeled 01E" failure: namesake ~48 deg away).
STAT_RADIUS_DEG = 3.0

# The bold "L" center marker snaps to the MSLP minimum within this radius of
# the track fix - the fix is the tracker's vortex center to 0.1 deg, the snap
# just lets the L sit on the rendered field's actual local minimum.
L_SNAP_RADIUS_DEG = 2.0

# Header title-bar background, a touch lighter than the map bg so the band reads
# like the site nav bar.
BAND_BG = "#11161f"

# SSHWS category chip colors (TAT intensity system), keyed off the frame VMAX:
# TD blue, TS lime, C1 yellow, C2 amber, C3 red, C4 pink, C5 violet. Each entry
# is (kt threshold, label, fill, text color).
_CAT_CHIP = [
    (137, "C5", "#b06bd8", "#ffffff"),
    (113, "C4", "#ee5da6", "#0a1324"),
    (96,  "C3", "#ef4a3c", "#ffffff"),
    (83,  "C2", "#f7a83a", "#0a1324"),
    (64,  "C1", "#f2e641", "#0a1324"),
    (34,  "TS", "#8ce05a", "#0a1324"),
    (0,   "TD", "#4d8bb0", "#ffffff"),
]


def _sshws_chip(vmax_kt: float) -> tuple[str, str, str]:
    """Return (category label, chip fill, chip text color) for a 10 m VMAX (kt)."""
    if not np.isfinite(vmax_kt):
        return "NA", "#5a6b87", "#ffffff"
    for thresh, label, fill, txt in _CAT_CHIP:
        if vmax_kt >= thresh:
            return label, fill, txt
    return _CAT_CHIP[-1][1:]


# ---------------------------------------------------------------------------
# Stat scope - WHICH cells the headline stats (VMAX / MSLP min / MIN BT / peak
# dBZ / vort max / RH mean) reduce over. Tracked = a disc of STAT_RADIUS_DEG
# around the namesake's track fix, so the header always describes the run's own
# storm. Untracked NEST = the whole grid (the nest is storm-following, so its
# domain IS the storm - unchanged legacy behavior). Untracked PARENT = the whole
# domain but explicitly LABELED, because a domain extremum there is routinely a
# different system and must not be passed off as the namesake.
# ---------------------------------------------------------------------------
@dataclass
class StatScope:
    mask: Optional[np.ndarray] = None   # True INSIDE the stat radius; None = whole domain
    tracked: bool = False               # anchored to the namesake's track fix
    label: str = ""                     # honesty suffix for the header right-stat


def _scoped(arr, scope: Optional["StatScope"]) -> np.ndarray:
    """The array with cells outside the scope set to NaN (no-op for domain
    scope). Accepts None scope for back-compat callers."""
    a = np.asarray(arr, dtype=float)
    if scope is not None and scope.mask is not None:
        a = np.where(scope.mask, a, np.nan)
    return a


def scope_max(arr, scope: Optional["StatScope"]) -> float:
    a = _scoped(arr, scope)
    return float(np.nanmax(a)) if np.isfinite(a).any() else float("nan")


def scope_min(arr, scope: Optional["StatScope"]) -> float:
    a = _scoped(arr, scope)
    return float(np.nanmin(a)) if np.isfinite(a).any() else float("nan")


def scope_mean(arr, scope: Optional["StatScope"]) -> float:
    a = _scoped(arr, scope)
    return float(np.nanmean(a)) if np.isfinite(a).any() else float("nan")


def _radius_mask(frame, clat: float, clon: float,
                 radius_deg: float) -> np.ndarray:
    """(lat, lon)-shaped bool mask: True within ``radius_deg`` (great-circle-ish:
    lon scaled by cos lat) of (clat, clon). ``clon`` may be in any wrap; it is
    shifted into the frame's longitude system first."""
    flon = _wrap_into(clon, float(frame.lon.min()), float(frame.lon.max()))
    LON, LAT = np.meshgrid(frame.lon, frame.lat)
    d = np.hypot(LAT - clat,
                 (LON - flon) * np.cos(np.deg2rad(np.clip(clat, -80.0, 80.0))))
    return d <= radius_deg


def _snap_fix(frame, cen_lat: Optional[float],
              cen_lon: Optional[float]) -> Optional[tuple]:
    """The track fix snapped to the nearest grid cell, in ``frame.lon``
    coordinates - or None when no fix / fix is off the grid by > 1 cell-ish
    margin (a parent fix is always on-grid; a nest fix should be too since the
    nest follows the storm, but guard anyway)."""
    if cen_lat is None or cen_lon is None:
        return None
    lon, lat = frame.lon, frame.lat
    flon = _wrap_into(cen_lon, float(lon.min()), float(lon.max()))
    if not (float(lat.min()) - 1.0 <= cen_lat <= float(lat.max()) + 1.0
            and float(lon.min()) - 1.0 <= flon <= float(lon.max()) + 1.0):
        return None
    jc = int(np.argmin(np.abs(lat - cen_lat)))
    ic = int(np.argmin(np.abs(lon - flon)))
    return float(lat[jc]), float(lon[ic])


def _category_pill(frame: HafsFrame,
                   scope: Optional[StatScope] = None) -> tuple[float, tuple[str, str, str]]:
    """SSHWS category pill from the storm's 10 m wind VMAX - the SINGLE source
    for the header chip on EVERY product.

    Returns ``(vmax_kt, (label, fill, text_color))``. The pill is derived ONLY
    from ``frame.wind_kt`` (the same field the wind product fetches, never the
    plotted reflectivity), so the reflectivity frame shows the identical category
    and color as the wind frame for the same storm/model/domain/forecast-hour.
    With a tracked ``scope`` the VMAX is the NAMESAKE's (cells within
    STAT_RADIUS_DEG of its track fix), not the domain's.
    """
    vmax = scope_max(frame.wind_kt, scope)
    return vmax, _sshws_chip(vmax)


def _clamp_window(a: float, b: float, lo: float, hi: float) -> tuple:
    """Slide the window ``[a, b]`` to lie within data bounds ``[lo, hi]`` without
    changing its width, so a storm-centered crop near the parent edge shifts in
    rather than opening a black void. If the data is narrower than the window,
    fall back to the full data span."""
    w = b - a
    if hi - lo <= w:
        return lo, hi
    if a < lo:
        return lo, lo + w
    if b > hi:
        return hi - w, hi
    return a, b


def _parent_storm_center(frame, cen_lat=None, cen_lon=None,
                         anchor_lat=None, anchor_lon=None) -> tuple:
    """(lat, lon) the parent 40x40 box centers on, in ``frame.lon`` coordinates.

    Order of preference: the storm track fix (``cen_lat`` / ``cen_lon``, snapped
    to the nearest parent grid cell so it lands in the frame longitude system),
    then the LAST KNOWN fix (``anchor_lat`` / ``anchor_lon`` - when the tracker
    has lost the storm at long leads, framing the last tracked position keeps
    the view on the namesake's region instead of jumping to another system),
    then the pressure minimum within ``PARENT_WIND_SEARCH_DEG`` of the strongest
    winds (the TC eyewall is the windiest feature, so this locks onto the cyclone
    rather than a deeper but far-off midlatitude low), then the whole-domain
    pressure minimum, then the data-extent center."""
    lon, lat = frame.lon, frame.lat
    for plat, plon in ((cen_lat, cen_lon), (anchor_lat, anchor_lon)):
        snapped = _snap_fix(frame, plat, plon)
        if snapped is not None:
            return snapped
    mslp = np.ma.masked_invalid(frame.mslp_hpa)
    wind = np.ma.masked_invalid(frame.wind_kt)
    if mslp.count() and wind.count():
        jw, iw = np.unravel_index(int(np.ma.argmax(wind)), wind.shape)
        near = ((np.abs(lat[:, None] - float(lat[jw])) > PARENT_WIND_SEARCH_DEG)
                | (np.abs(lon[None, :] - float(lon[iw])) > PARENT_WIND_SEARCH_DEG))
        sub = np.ma.masked_where(near, mslp)
        if sub.count():
            kc = np.unravel_index(int(np.ma.argmin(sub)), sub.shape)
            return float(lat[kc[0]]), float(lon[kc[1]])
    if mslp.count():
        kc = np.unravel_index(int(np.ma.argmin(mslp)), mslp.shape)
        return float(lat[kc[0]]), float(lon[kc[1]])
    return (0.5 * (float(lat.min()) + float(lat.max())),
            0.5 * (float(lon.min()) + float(lon.max())))


def _largest_true_rect(mask: np.ndarray):
    """(r0, r1, c0, c1) inclusive-exclusive of the maximum-AREA all-True
    axis-aligned rectangle in a 2-D boolean ``mask``, or None if empty. Standard
    histogram / monotonic-stack method, O(H*W)."""
    H, W = mask.shape
    heights = np.zeros(W, dtype=np.int64)
    best_area = 0
    best = None
    for i in range(H):
        heights = np.where(mask[i], heights + 1, 0)
        h = heights.tolist()
        h.append(0)                       # sentinel flushes the stack
        stack: list[int] = []
        for j in range(W + 1):
            while stack and h[stack[-1]] >= h[j]:
                top = stack.pop()
                ht = h[top]
                left = stack[-1] + 1 if stack else 0
                area = ht * (j - left)
                if area > best_area:
                    best_area = area
                    best = (i - ht + 1, i + 1, left, j)
            stack.append(j)
    return best


def _synoptic_view_extent(frame: HafsFrame, max_cells: int = 360):
    """The largest rectangular (lon/lat) view fully INSIDE the parent-domain
    footprint, so the synoptic env maps render as a clean PlateCarree rectangle
    with straight edges instead of the native tilted/fanned footprint outline
    (whose NaN corners show the panel background).

    The HAFS parent is a tilted footprint embedded in a circumscribing regular
    lon/lat grid (NaN in the corners); ``frame.extent`` is that circumscribing box
    -> the fan. We instead crop the VIEW (set_xlim/set_ylim) to the largest
    inscribed all-finite rectangle of the footprint mask (``mslp|wind``), keeping
    essentially the same coverage minus the corner slivers. The fill is still
    drawn on the full grid and clipped to this view, so nothing inside is missing.

    Computed on a CONSERVATIVELY block-reduced mask (a coarse cell counts as data
    only if EVERY fine cell in it is finite) so the result is guaranteed all-finite
    while staying cheap (~tens of ms). Falls back to ``frame.extent`` if the mask
    is already a full rectangle (inscribed == full) or degenerate."""
    lon, lat = frame.lon, frame.lat
    full = frame.extent
    mask = np.isfinite(frame.mslp_hpa) | np.isfinite(frame.wind_kt)
    H, W = mask.shape
    if H == 0 or W == 0 or mask.all():
        return full                                   # already a full rectangle
    s = max(1, int(np.ceil(max(H, W) / max_cells)))   # block stride
    if s > 1:                                          # conservative block-AND reduce
        Hp, Wp = -(-H // s) * s, -(-W // s) * s        # pad up to a multiple of s
        padded = np.zeros((Hp, Wp), dtype=bool)
        padded[:H, :W] = mask
        ds = padded.reshape(Hp // s, s, Wp // s, s).all(axis=(1, 3))
    else:
        ds = mask
    rect = _largest_true_rect(ds)
    if rect is None:
        return full
    R0, R1, C0, C1 = rect
    r0, r1 = R0 * s, min(R1 * s, H)
    c0, c1 = C0 * s, min(C1 * s, W)
    if r1 - r0 < 2 or c1 - c0 < 2:
        return full                                   # degenerate -> no crop
    box = (float(lon[c0]), float(lon[c1 - 1]), float(lat[r0]), float(lat[r1 - 1]))
    # If the inscribed box is essentially the full extent, keep the full extent
    # (regular grids / the offline synthetic frame stay byte-identical).
    if (abs(box[0] - full[0]) < 1e-6 and abs(box[1] - full[1]) < 1e-6
            and abs(box[2] - full[2]) < 1e-6 and abs(box[3] - full[3]) < 1e-6):
        return full
    return box


def render_frame(frame: HafsFrame, out_path: str,
                 countries: Optional[dict], coast: Optional[dict],
                 states: Optional[dict] = None,
                 product: str = "mslp_wind",
                 cen_lat: Optional[float] = None,
                 cen_lon: Optional[float] = None,
                 anchor_lat: Optional[float] = None,
                 anchor_lon: Optional[float] = None,
                 enhancement: Optional[str] = None) -> dict:
    """Render one TAT-styled HAFS frame; return its GEOMETRY.

    Returns ``{"axes_px": [x, y, w, h], "bbox": [W, S, E, N]}`` - the drawn map
    axes in output-PNG pixels (origin top-left) and its geographic extent. The
    projection is plain linear lon/lat, so those eight numbers make pixel <->
    lon/lat an exact affine on the client: they are what a lat/lon readout, a
    value readout, and a ruler are built from, and the only way to do it on a
    storm-following nest whose extent moves every forecast hour. Previously
    ``None``; both call sites ignored the value, so returning one is
    backward-compatible.

    ``product`` selects the filled field and its legend/header text, sharing all
    of the layout, MSLP isobars, L marker, coastlines, header band, and footer:
      - ``"mslp_wind"`` (default): 10 m wind-speed fill + wind barbs, knots bar.
      - ``"refl"``: composite reflectivity fill (discrete .pal table), NO barbs,
        a dBZ bar. The SSHWS chip stays keyed off VMAX for both.
      - ``"clean_ir"`` / ``"water_vapor"``: simulated-satellite brightness
        temperature fill (``frame.bt_c``, degC) colored by a tat_palettes
        enhancement, NO barbs, a degC brightness-temperature bar, bright haloed
        coastlines. ``enhancement`` overrides the per-product default
        (``rainbow_ir`` for Clean IR, ``wv_tat`` for Water Vapor); ``dvorak`` is
        a valid selectable IR enhancement too. The SSHWS chip stays keyed off
        VMAX (from the .atm winds) and the right stat becomes the coldest BT.

    Domain framing differs by ``frame.product`` (the HAFS domain, NOT the field
    above): the STORM NEST is cropped to a fixed ``NEST_VIEW_DEG`` (5.5 deg)
    square window centered on the storm, while the PARENT is cropped to a fixed
    40 x 40 deg window centered on the storm (see ``PARENT_HALF_DEG``) with a
    calmer (wider-interval, softer) isobar field. (This line previously
    described a per-side ``BBOX_TRIM_DEG`` trim on the nest; that constant was
    removed when the nest moved to the storm-centered window and no longer
    exists anywhere in this module.) The
    parent center is ``cen_lat`` / ``cen_lon`` (the namesake's track fix at THIS
    forecast hour, from the run's own trak.atcfunix); ``anchor_lat`` /
    ``anchor_lon`` is the LAST KNOWN fix used for framing only when the tracker
    has lost the storm; without either the strongest-wind-anchored pressure
    minimum is used so the crop still locks onto a cyclone.

    STORM ANCHORING (the namesake-vs-domain-extremum fix): with a track fix, the
    L marker snaps to the MSLP minimum within ``L_SNAP_RADIUS_DEG`` of the fix
    and ALL headline stats (VMAX + SSHWS chip, MSLP min, MIN BT, peak dBZ, ...)
    reduce over cells within ``STAT_RADIUS_DEG`` of it - so the header always
    describes the run's own storm. WITHOUT a fix the renderer degrades honestly:
    the nest keeps domain stats (its domain IS the storm), but the parent gets
    NO L marker, an NA category chip, and stats explicitly labeled domain-wide -
    a different system's extremum is never passed off under the namesake's id.
    Both domains draw the same 10 m Natural Earth basemap.
    """
    # Look up the product's spec once; every per-product difference below (fill
    # field/cmap/norm, fill method, barbs, coast styling, colorbar, header stat)
    # is read off it - no product-name if/elif chains. Lazy import avoids a
    # module-load cycle (hafs_registry imports primitives from this module).
    from hafs_render import hafs_registry as reg
    # The MODEL registry: the structural (model x product) gate below and the AI
    # intensity suppression in the header both read it. Imported here alongside
    # reg for symmetry; it has no dependency on this module, so no cycle either
    # way.
    from hafs_render import model_registry as mr
    spec = reg.get_spec(product)
    # STRUCTURAL GATE, at the renderer itself. The job planner already drops an
    # incompatible (model, product) pair, so reaching here with one means some
    # other caller bypassed the planner - refuse rather than emit a physically
    # meaningless image. Skipped for a model absent from the registry so ad-hoc
    # and test renders of unregistered slugs still work.
    if mr.has_model(frame.model):
        reg.assert_renderable(frame.model, product)
    if spec.requires_attr and getattr(frame, spec.requires_attr) is None:
        raise ValueError(
            f"render_frame(product={product!r}) needs frame.{spec.requires_attr}; "
            "fetch with the matching want_refl / sat_parm option")
    enh_name = spec.resolve_enhancement(enhancement)
    is_parent = frame.product == "parent.atm"
    # Synoptic env products: the broad parent map (NO storm-centered crop), no L,
    # no SSHWS pill, stats reduced over the WHOLE domain (domain-wide IS the
    # product, so no honesty suffix). The storm-centered products keep their crop.
    synoptic = bool(spec.synoptic_parent) and is_parent
    # The namesake's track fix snapped into frame coordinates (None when the
    # tracker has no fix at this hour). This single value anchors the stat
    # scope, the L marker, and (on the parent) the crop window. Suppressed for the
    # synoptic env maps (they are not storm-centered).
    fix = None if synoptic else _snap_fix(frame, cen_lat, cen_lon)
    if synoptic:
        scope = StatScope()                  # whole domain, no honesty suffix
    elif fix is not None:
        scope = StatScope(mask=_radius_mask(frame, fix[0], fix[1],
                                            STAT_RADIUS_DEG), tracked=True)
    elif is_parent:
        # Untracked parent: domain stats would describe SOME system, just not
        # necessarily the namesake - keep them but say so.
        scope = StatScope(mask=None, tracked=False, label="  (domain-wide)")
    else:
        # Untracked nest: the storm-following nest's domain IS the storm, so
        # domain stats remain honest (and byte-identical to the legacy output).
        scope = StatScope()
    lon_min, lon_max, lat_min, lat_max = frame.extent
    d_lon_min, d_lon_max, d_lat_min, d_lat_max = frame.extent
    if synoptic:
        # Broad synoptic view, rendered as a clean rectangular PlateCarree extent:
        # crop the VIEW to the largest rectangle fully inside the parent footprint
        # (straight edges) instead of the circumscribing frame.extent (whose NaN
        # corners show the panel background as the native tilted/fanned outline).
        # The fill/overlays/coasts still draw on the full grid and clip to this view.
        lon_min, lon_max, lat_min, lat_max = _synoptic_view_extent(frame)
    elif is_parent:
        # Parent: a fixed 40 x 40 deg window centered on the STORM, not on the
        # parent-domain-wide pressure minimum (which snaps to a deeper midlatitude
        # low and shoves the TC to the edge). The center is the storm track fix
        # (``cen_lat`` / ``cen_lon``, the same vortex the storm-following nest is
        # built on), then the last-known fix (``anchor_lat`` / ``anchor_lon``),
        # then a wind-anchored pressure-minimum fallback. It comes back
        # in ``frame.lon`` monotonic coordinates (continuous past +180 for a West
        # Pacific dateline crosser) so the basemap wrap / labels keep handling the
        # antimeridian.
        clat, clon = _parent_storm_center(frame, cen_lat, cen_lon,
                                          anchor_lat, anchor_lon)
        lon_min, lon_max = clon - PARENT_HALF_DEG, clon + PARENT_HALF_DEG
        lat_min, lat_max = clat - PARENT_HALF_DEG, clat + PARENT_HALF_DEG
        # Keep the window inside the valid lat range and the parent data
        # footprint: if a side runs past the data edge, slide the window back in
        # (preserving width) so the frame never shows a black void.
        lat_min, lat_max = _clamp_window(lat_min, lat_max,
                                         max(d_lat_min, -90.0),
                                         min(d_lat_max, 90.0))
        lon_min, lon_max = _clamp_window(lon_min, lon_max, d_lon_min, d_lon_max)
    else:
        # Nest: a FIXED NEST_VIEW_DEG square centered on the STORM (the same center
        # the L marker / stats / parent crop use), clamped to the nest data extent.
        # Mirrors the parent's storm-centered crop at the nest scale, replacing the
        # old data-centered per-side trim that left the storm off-center once it had
        # moved within its nest. The fill, barbs, contours, and coastlines are still
        # drawn on the full grid and clipped to these limits (set_extent is a VIEW
        # crop, not a data change), so nothing inside the view is missing and the
        # headline stats / L marker / chrome are byte-identical.
        clat, clon = _parent_storm_center(frame, cen_lat, cen_lon,
                                          anchor_lat, anchor_lon)
        half = 0.5 * NEST_VIEW_DEG
        lon_min, lon_max = clon - half, clon + half
        lat_min, lat_max = clat - half, clat + half
        lat_min, lat_max = _clamp_window(lat_min, lat_max, d_lat_min, d_lat_max)
        lon_min, lon_max = _clamp_window(lon_min, lon_max, d_lon_min, d_lon_max)
    # Extent the coastline/border features are clipped against: the cropped view
    # for the parent (so far-away land is rejected), the data extent for the nest
    # (unchanged behavior). Axes clipping trims whatever crosses the edge.
    # Coastline/border clip extent = the cropped view for BOTH domains now (the
    # nest is storm-centered/cropped like the parent), so far-away land is rejected
    # before the axes clip; features inside the view are unchanged.
    feat_extent = (lon_min, lon_max, lat_min, lat_max)
    mean_lat = 0.5 * (lat_min + lat_max)
    # PlateCarree aspect: 1 deg lon is cos(lat)x shorter than 1 deg lat.
    geo_aspect = 1.0 / max(np.cos(np.deg2rad(mean_lat)), 0.1)
    lon_span = lon_max - lon_min
    lat_span = (lat_max - lat_min) * geo_aspect

    # Inch-based layout (no tight bbox) so the full-width header band, the map,
    # the right colorbar, and the credit footer all land at exact positions.
    # FIXED square map box so EVERY frame of a run renders an IDENTICAL figure, i.e.
    # identical output pixels, regardless of storm latitude (geo_aspect grows as the
    # storm recurves poleward) or any _clamp_window data-span fallback - those used
    # to drive map_w/map_h here and made the PNG physically grow/shrink per F-hour.
    # The geography is still framed by set_xlim/set_ylim + set_aspect(geo_aspect) on
    # the axes below (a VIEW crop), so the geographic framing is UNCHANGED; only the
    # canvas size is pinned. A non-square view simply letterboxes (BAND_BG) inside the
    # constant box; set_anchor('W') keeps the map left-justified so the lat labels
    # stay put and any slack sits between the map and the colorbar.
    map_w = map_h = MAP_BOX_IN
    left_in, cbar_in = LEFT_GUTTER_IN, CBAR_GUTTER_IN
    band_in, foot_in, botpad_in = BAND_IN, FOOT_IN, BOTPAD_IN
    fig_w, fig_h = FIG_W_IN, FIG_H_IN
    map_bottom = botpad_in + foot_in

    fig = plt.figure(figsize=(fig_w, fig_h), facecolor=BAND_BG)
    ax = fig.add_axes([left_in / fig_w, map_bottom / fig_h,
                       map_w / fig_w, map_h / fig_h])
    # Canonical filled basemap for the field products (grib "atm"); the full-frame
    # simulated-satellite products (grib "sat") fill the whole frame with the BT
    # image, so they keep the plain interior + their own coast styling.
    filled_basemap = spec.grib != "sat"
    ax.set_facecolor(OCEAN_FILL if filled_basemap else PLOT_BG)

    # Resolve the fill field + its cmap/norm through the spec's color factory
    # (wind = continuous 0-165 kt TAT table; refl = discrete .pal table; BT =
    # tat_palettes IR/WV enhancement with NaN -> transparent). ``colors`` also
    # carries the .pal / enhancement extras the colorbar builder needs.
    colors = spec.make_colors(spec, frame, enh_name)
    cmap, norm, field = colors.cmap, colors.norm, colors.field
    Lon, Lat = np.meshgrid(frame.lon, frame.lat)

    # (1) Filled field. Two rasterizations, chosen by the spec's fill method:
    # contourf over the palette's discrete dBZ step edges (reflectivity - the
    # interpolated band boundaries look smooth while the colors stay locked to
    # the .pal steps; it does NOT fill below the lowest level, so non-precip /
    # masked cells stay transparent and the dark map shows through; extend="max"
    # routes any >top value to the white set_over), or a smooth pcolormesh raster
    # gradient (wind + simulated-satellite BT; masked NaN cells stay transparent).
    fill = np.ma.masked_invalid(field)
    if spec.fill_method is reg.FillMethod.CONTOURF_DISCRETE:
        levels = list(norm.boundaries)        # the .pal step edges
        cf = ax.contourf(Lon, Lat, fill, levels=levels, cmap=cmap, norm=norm,
                         extend="max", antialiased=True, zorder=2)
    elif spec.grib == "sat":
        # Simulated-satellite BT/PCT products (clean IR / WV / 89 PCT): the nest
        # is a REGULAR lat/lon grid, so imshow with bilinear interpolation gives
        # the smooth continuous look (pcolormesh shading="nearest" shows pixel
        # blocks). DISPLAY ONLY -- the interpolation never touches frame.bt_c, so
        # MIN BT / stats downstream stay exact on the raw field. aspect="auto"
        # fills the axes box exactly like pcolormesh did (no reshaping).
        cf = ax.imshow(fill, origin="lower", cmap=cmap, norm=norm,
                       extent=[float(frame.lon.min()), float(frame.lon.max()),
                               float(frame.lat.min()), float(frame.lat.max())],
                       interpolation="bilinear", aspect="auto", zorder=2)
    else:
        cf = ax.pcolormesh(Lon, Lat, fill, cmap=cmap, norm=norm,
                           shading="nearest", zorder=2)

    # Resolve the wind source for barbs + category contours from the spec's
    # wind_provider, or default to the 10 m fields (frame.u_kt/v_kt/wind_kt) -
    # byte-identical to the original wind path. Upper-air products supply a
    # provider that reads a pressure level / layer-mean (m/s -> kt).
    if spec.draw_barbs or spec.draw_wind_contours:
        if spec.wind_provider is not None:
            bu_kt, bv_kt, bspd_kt = spec.wind_provider(frame)
        else:
            bu_kt, bv_kt, bspd_kt = frame.u_kt, frame.v_kt, frame.wind_kt

    # (2) Wind barbs, subsampled to ~BARB_TARGET across each axis. White,
    # antialiased, kept vector (not rasterized) with a subtle dark halo so they
    # stay sharp and legible over both the cool (dark) and warm (bright) ends of
    # the fill palette; emptybarb=0 drops the calm-air circle. The wind / height-
    # wind products carry barbs; the source is spec.wind_provider (10 m by
    # default, a pressure level / layer-mean for the upper-air products).
    if spec.draw_barbs:
        nlat, nlon = bspd_kt.shape
        # Stride targets ~BARB_TARGET barbs across the VISIBLE axis. The parent keeps
        # the full-grid stride (unchanged); the storm nest derives it from the cells
        # INSIDE the cropped NEST_VIEW_DEG view so the tighter zoom keeps a
        # consistent barb count instead of inheriting the sparser full-grid stride.
        if is_parent:
            vlat, vlon = nlat, nlon
        else:
            vlat = int(np.count_nonzero((frame.lat >= lat_min) &
                                        (frame.lat <= lat_max))) or nlat
            vlon = int(np.count_nonzero((frame.lon >= lon_min) &
                                        (frame.lon <= lon_max))) or nlon
        si = max(1, int(round(vlat / BARB_TARGET)))
        sj = max(1, int(round(vlon / BARB_TARGET)))
        u = np.ma.masked_invalid(bu_kt)
        v = np.ma.masked_invalid(bv_kt)
        barbs = ax.barbs(
            Lon[::si, ::sj], Lat[::si, ::sj], u[::si, ::sj], v[::si, ::sj],
            length=6.8, linewidth=1.1, color="#ffffff", zorder=4,
            pivot="middle", sizes=dict(emptybarb=0.0), antialiased=True,
        )
        barbs.set_rasterized(False)
        # Subtle dark halo just narrower than the white line so the barbs read as
        # white (legible over the bright fill) with a thin dark edge, not as dark.
        barbs.set_path_effects([pe.withStroke(linewidth=2.0, foreground="#0a0d12")])

    # (2a) Streamlines of a cached vector (the deep-layer-shear products draw the
    # shear VECTOR as light flowing lines, the synoptic way to read shear
    # direction + relative magnitude without barb clutter). streamline_provider
    # returns (u, v) in display units; streamplot needs a strictly-ascending,
    # roughly-regular grid (frame.lon/lat qualify) and chokes on NaN, so off-grid
    # NaN is filled with 0 just for the trace. Thin near-white with a dark halo.
    if spec.streamline_provider is not None:
        su, sv = spec.streamline_provider(frame)
        su = np.where(np.isfinite(su), su, 0.0)
        sv = np.where(np.isfinite(sv), sv, 0.0)
        try:
            strm = ax.streamplot(
                frame.lon, frame.lat, su, sv, density=1.5, linewidth=0.7,
                color="#e8eef5", arrowsize=0.8, zorder=4)
            strm.lines.set_path_effects(
                [pe.withStroke(linewidth=1.7, foreground="#0a0d12")])
            strm.lines.set_alpha(0.9)
        except Exception as e:                       # noqa: BLE001 - never fatal
            log.warning("streamplot failed (%s): %s", product, e)

    # (2b) Thin black wind-speed CATEGORY contour LINES over the fill (the wind /
    # height-wind products, spec.draw_wind_contours - decoupled from barbs so the
    # vorticity / RH products can carry barbs WITHOUT these). Levels are the
    # Saffir-Simpson thresholds (CBAR_TICKS_KT: 34 TS, 64 C1, 83 C2, 96 C3, 113
    # C4, 137 C5) - the SAME values the wind colorbar ticks - so each black line
    # marks a category boundary. Only thresholds inside the frame's wind range are
    # drawn. Thin/translucent, UNLABELED, SEPARATE from the white MSLP isobars.
    if spec.draw_wind_contours:
        wfill = np.ma.masked_invalid(bspd_kt)
        if wfill.count():
            wmin, wmax = float(wfill.min()), float(wfill.max())
            wlevs = [t for t in CBAR_TICKS_KT if wmin < t < wmax]
            if wlevs:
                wcs = ax.contour(Lon, Lat, wfill, levels=wlevs,
                                 colors="#000000", linewidths=0.6, alpha=0.7,
                                 zorder=3.5)
                wcs.set_rasterized(False)

    # (2c) Thin black contour LINES of the FILL field over the raster, in the
    # SAME style as the wind product's category contours above (black, lw 0.6,
    # alpha 0.7, zorder 3.5) so the products read consistently. Used today by the
    # PWAT product (moisture contours every 10 mm from 50). Only the levels that
    # fall strictly inside the frame's value range are drawn; the lines are
    # UNLABELED and SEPARATE from the white MSLP isobars below. Guarded by the
    # spec's field_contour_levels - empty for every other product - so the lines
    # never leak onto wind/refl/sim-sat (which leave it at its () default).
    if spec.field_contour_levels:
        flevs = [t for t in spec.field_contour_levels
                 if fill.count() and float(fill.min()) < t < float(fill.max())]
        if flevs:
            fcs = ax.contour(Lon, Lat, fill, levels=flevs, colors="#000000",
                             linewidths=0.6, alpha=0.7, zorder=3.5)
            fcs.set_rasterized(False)

    # (3) MSLP isobars. The NEST keeps a tight 4 mb interval, thin white with a
    # dark halo, labels every other contour. The PARENT spans ~40 deg where a
    # 4 mb interval stacks into an illegible thicket, so it widens to an 8 mb
    # interval and softens the lines (thinner, lower alpha, lighter halo) and
    # labels so the isobars read as gentle synoptic guidance rather than noise.
    # Gated by spec.draw_mslp_isobars (default True): the upper-air height /
    # vorticity products turn the ISOBARS off and draw height line-contours
    # (section 3b) instead, while keeping the L center marker (section 5).
    mslp = np.ma.masked_invalid(frame.mslp_hpa)
    if spec.draw_mslp_isobars and mslp.count():
        mslp_iv = 8 if is_parent else 4
        lw = 0.6 if is_parent else 0.75
        alpha = 0.65 if is_parent else 0.9
        halo = 1.0 if is_parent else 1.4
        lo = int(np.floor(mslp.min() / mslp_iv) * mslp_iv)
        hi = int(np.ceil(mslp.max() / mslp_iv) * mslp_iv)
        clevs = np.arange(lo, hi + mslp_iv, mslp_iv)
        cs = ax.contour(Lon, Lat, mslp, levels=clevs, colors="#ffffff",
                        linewidths=lw, alpha=alpha, zorder=5)
        # mpl >=3.8: ContourSet is itself a Collection (no .collections list).
        cs.set_rasterized(False)
        cs.set_path_effects([pe.withStroke(linewidth=halo, foreground="#000000")])
        # Thin the inline labels: every other contour on the nest, every third on
        # the wider parent so the few isobars there aren't crowded with numbers.
        label_levs = clevs[::3] if is_parent else clevs[::2]
        lbls = ax.clabel(cs, levels=label_levs, inline=True, fontsize=7,
                         fmt="%d")
        for t in lbls:
            t.set_color("#ffffff")
            t.set_zorder(7)
            t.set_rasterized(False)
            t.set_path_effects([pe.withStroke(linewidth=1.6, foreground="#000000")])

    # (3b) Generic labeled LINE-CONTOUR overlay of a cached scalar (spec.
    # line_contour), used by the upper-air height products to draw geopotential
    # height in decameters. NOT height-specific: the spec's source() returns the
    # field already in display units and the interval/label/color come from the
    # spec, so any product could contour any field. Levels are multiples of the
    # interval inside the frame's range; lines are thin with a halo + labeled so
    # they read over both the colorful wind fill and the dark vorticity fill.
    if spec.line_contour is not None:
        lc = spec.line_contour
        cfield = np.ma.masked_invalid(lc.source(frame))
        if cfield.count():
            iv = lc.interval
            lo = np.floor(float(cfield.min()) / iv) * iv
            hi = np.ceil(float(cfield.max()) / iv) * iv
            levs = np.arange(lo, hi + iv, iv)
            hcs = ax.contour(Lon, Lat, cfield, levels=levs, colors=lc.color,
                             linewidths=lc.linewidth, alpha=lc.alpha, zorder=5)
            hcs.set_rasterized(False)
            if lc.halo:
                hcs.set_path_effects([pe.withStroke(
                    linewidth=lc.linewidth + lc.halo, foreground=lc.halo_color)])
            hlbls = ax.clabel(hcs, inline=True, fontsize=7, fmt=lc.label_fmt)
            for t in hlbls:
                t.set_color(lc.color)
                t.set_zorder(7)
                t.set_rasterized(False)
                t.set_path_effects([pe.withStroke(
                    linewidth=1.6, foreground=lc.halo_color)])

    # (4) Coastlines + borders on top of the filled field. Wind uses bold BLACK
    # (reads over the colorful wind fill); reflectivity uses bold NEON GREEN
    # (#39ff14), which stands clean against both the dark ocean and the bright
    # radar cores without clashing with the white MSLP isobars. The refl coast is
    # a touch heavier so it reads as a crisp bold outline.
    # Per-product coast styling from the spec: black (wind) / neon green (refl) /
    # bright near-white with a dark halo (simulated satellite, legible over both
    # the colorful cold tops and grayscale warm halves of the IR/WV fills). The
    # coast and country borders share one color (equal for every product today).
    # Both domains draw the 10 m Natural Earth basemap (crisp coastlines at the
    # parent ~40 deg span as well as the nest). Features are clipped to
    # ``feat_extent`` (the storm-centered crop on the parent) so far-away land is
    # dropped.
    if filled_basemap:
        # CANONICAL filled basemap: slate land UNDER the data (zorder 0.6 < the
        # field's 2), then muted coast -> country -> state borders ON TOP (zorder
        # 6 > the field). Replaces the old per-product black/neon-green outlines.
        if countries:
            _fill_polygons(ax, countries.get("features", []), feat_extent,
                           LAND_FILL, 0.6)
        if coast:
            _draw_feature_lines(ax, coast.get("features", []), feat_extent,
                                COAST_BORDER, 0.6, 6)
        if countries:
            _draw_feature_lines(ax, countries.get("features", []), feat_extent,
                                COUNTRY_BORDER, 0.7, 6)
        if states:
            _draw_feature_lines(ax, states.get("features", []), feat_extent,
                                STATE_BORDER, 0.4, 6)
    else:
        # Full-frame simulated-satellite products: keep the per-product coast
        # styling (the BT image fills the frame, so land fill would hide it).
        coast_color = border_color = spec.coast_color
        coast_lw, coast_halo = spec.coast_lw, spec.coast_halo
        if coast:
            _draw_feature_lines(ax, coast.get("features", []), feat_extent,
                                coast_color, coast_lw, 6, halo=coast_halo)
        if countries:
            _draw_feature_lines(ax, countries.get("features", []), feat_extent,
                                border_color, 0.8, 6, halo=coast_halo)

    # (5) Bold "L" at the NAMESAKE's MSLP minimum, with the value just below it.
    # Part of the MSLP overlay, gated by spec.draw_mslp_markers (the upper-air
    # height / vorticity products KEEP the L center marker even though they omit
    # the isobars - matching the reference "MSLP Centers" plots). With a track
    # fix the L snaps to the pressure minimum within L_SNAP_RADIUS_DEG of the
    # fix on BOTH domains, so it can never label a different system with the
    # namesake's depth. Untracked: the nest keeps its full-grid search (its
    # domain IS the storm); the parent draws NO L at all - the honest
    # degradation - because any window/domain minimum there may be another
    # system entirely (the f123 "968 mb near Mexico" failure).
    lmslp = mslp
    l_allowed = True
    if fix is not None and mslp.count():
        snap = _radius_mask(frame, fix[0], fix[1], L_SNAP_RADIUS_DEG)
        win = np.ma.masked_where(~snap, mslp)
        if win.count():
            lmslp = win
        elif is_parent:
            # Defense-in-depth: an all-masked snap disc is unreachable on real
            # PRMSL grids (continuous field, padding trimmed), but if it ever
            # happened the fall-through would re-draw the L at the WHOLE-domain
            # minimum - the exact wrong-system mislabel this anchoring removes.
            # No L beats a wrong L.
            l_allowed = False
    elif is_parent:
        l_allowed = False
    if spec.draw_mslp_markers and l_allowed and lmslp.count():
        kmin = np.unravel_index(int(np.ma.argmin(lmslp)), lmslp.shape)
        l_lon, l_lat = float(frame.lon[kmin[1]]), float(frame.lat[kmin[0]])
        pmin = float(lmslp.min())
        l_off = (lat_max - lat_min) * 0.05
        ax.text(l_lon, l_lat, "L", ha="center", va="center", fontsize=24,
                fontweight="bold", color="#ffffff", zorder=8,
                path_effects=[pe.withStroke(linewidth=2.8, foreground="#000000")])
        ax.text(l_lon, l_lat - l_off, f"{pmin:.0f}", ha="center", va="top",
                fontsize=9, fontweight="bold", color="#ffffff", zorder=8,
                path_effects=[pe.withStroke(linewidth=1.8, foreground="#000000")])

    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_aspect(geo_aspect)
    # Pinned square figure (see the map-box note above): a non-square view letterboxes
    # inside the constant box. Anchor the shrunk axes to the WEST so the lat labels
    # stay at the fixed left gutter and the BAND_BG slack falls between the map and
    # the right colorbar, instead of splitting symmetrically around a centered map.
    ax.set_anchor("W")
    ax.xaxis.set_major_locator(mticker.MaxNLocator(6, steps=[1, 2, 2.5, 5, 10]))
    ax.yaxis.set_major_locator(mticker.MaxNLocator(6, steps=[1, 2, 2.5, 5, 10]))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(_lon_label))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_lat_label))
    ax.grid(True, linewidth=0.3, color=GRID_COLOR, alpha=0.7, zorder=3)
    ax.tick_params(colors=MUTED_COLOR, labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(MUTED_COLOR)
        spine.set_linewidth(0.6)

    # (6) Right-side labeled colorbar, built by the spec: continuous knots (wind,
    # ticks on the SS thresholds) / discrete dBZ blocks (refl) / physical degC
    # brightness temperature (BT). The tick + outline restyle below is shared.
    cax = fig.add_axes([(left_in + map_w + 0.30) / fig_w,
                        (map_bottom + 0.05 * map_h) / fig_h,
                        0.16 / fig_w, (0.90 * map_h) / fig_h])
    cb = spec.make_colorbar(fig, cax, cf, colors)
    cb.ax.yaxis.set_tick_params(color=MUTED_COLOR, labelcolor=MUTED_COLOR,
                                labelsize=8)
    cb.outline.set_edgecolor(MUTED_COLOR)
    cb.outline.set_linewidth(0.4)

    # (7) Header BAND: a slim dark title bar across the top (TAT nav-bar look),
    # NOT the reference two-line header. Left: bold model + id, an SSHWS category
    # chip keyed off VMAX, and a muted field/domain subtitle. Right: a teal
    # VMAX/MSLP line and a muted Init -> F-hour -> Valid time-flow line.
    # The SSHWS category pill is derived ONLY from the storm's 10 m wind VMAX,
    # via the shared _category_pill helper, on EVERY product - so the reflectivity
    # frame shows the identical category + color as the wind frame for the same
    # storm/model/domain/forecast-hour. It is never derived from the refl field.
    vmax, (cat_label, chip_fill, chip_txt) = _category_pill(frame, scope)
    if is_parent and not scope.tracked:
        # No track fix on the parent: the domain VMAX may belong to another
        # system, so a category chip would mislabel the namesake. NA, honestly.
        cat_label, chip_fill, chip_txt = _sshws_chip(float("nan"))
    pmin_hdr = scope_min(frame.mslp_hpa, scope)
    model_label = MODEL_LABEL.get(frame.model, frame.model.upper())
    storm_disp = frame.storm.upper()
    domain_label = PRODUCT_LABEL.get(frame.product, frame.product)

    # Per-product subtitle (lower-left) and right-side stat (upper-right) from the
    # spec: wind keeps VMAX, refl shows peak dBZ, BT shows the coldest cloud top;
    # all keep the shared MSLP minimum and the VMAX-derived SSHWS pill above.
    # Each reduces over the storm-anchored ``scope`` (and carries its honesty
    # label when the stats had to fall back to domain-wide).
    subtitle, right_stat = spec.make_stat(spec, frame, domain_label, vmax,
                                          pmin_hdr, scope)

    # AI models: withhold the INTENSITY claims (VMAX, the MSLP minimum, and the
    # VMAX-derived SSHWS pill) but keep the product's own statistic. Current
    # learned emulators are trained on ~0.25 deg reanalysis with a loss that
    # rewards smooth fields, so they systematically under-deepen TCs - their
    # intensity extrema are resolution artifacts, not forecasts. The track and
    # the field are still worth showing; the number is not.
    #
    # A model absent from the registry (an unregistered/experimental slug) is
    # treated as a physics model and keeps its stat, so this can never blank a
    # header by accident. Physics models skip this branch entirely, so their
    # headers stay byte-identical.
    _mspec = mr.MODELS.get(frame.model)
    _show_intensity = _mspec.show_intensity_stat if _mspec else True
    if not _show_intensity:
        right_stat = reg.strip_intensity(right_stat, scope.label)

    band = fig.add_axes([0.0, (map_bottom + map_h) / fig_h, 1.0, band_in / fig_h])
    band.set_facecolor(BAND_BG)
    band.set_xlim(0, 1)
    band.set_ylim(0, 1)
    band.set_xticks([])
    band.set_yticks([])
    for s in band.spines.values():
        s.set_visible(False)

    pad_x = left_in / fig_w           # align band edges with the map
    y_top, y_bot = 0.62, 0.27
    t_title = band.text(pad_x, y_top, f"{model_label}  {storm_disp}",
                        ha="left", va="center", fontsize=15, fontweight="bold",
                        color=TEXT_COLOR, transform=band.transAxes)
    # Place the category chip immediately after the title via its measured width.
    # Suppressed for the synoptic env maps: they are not storm-centered, so an
    # SSHWS category pill would imply a storm focus they don't have.
    # ...and suppressed for AI models: the pill is a pure VMAX restatement, so
    # showing it would reinstate exactly the intensity claim just withheld.
    if not synoptic and _show_intensity:
        try:
            rend = fig.canvas.get_renderer()
            x_after = band.transAxes.inverted().transform(
                (t_title.get_window_extent(renderer=rend).x1, 0.0))[0]
        except Exception:
            x_after = pad_x + 0.10
        band.text(x_after + 0.012, y_top, cat_label, ha="left", va="center",
                  fontsize=11, fontweight="bold", color=chip_txt,
                  transform=band.transAxes, zorder=3,
                  bbox=dict(boxstyle="round,pad=0.34", facecolor=chip_fill,
                            edgecolor="none"))
    band.text(pad_x, y_bot, subtitle,
              ha="left", va="center", fontsize=9.5, color=MUTED_COLOR,
              transform=band.transAxes)

    rx = 1.0 - pad_x
    band.text(rx, y_top, right_stat,
              ha="right", va="center", fontsize=12, fontweight="bold",
              color=ACCENT_COLOR, transform=band.transAxes)
    band.text(rx, y_bot,
              f"Init {frame.init_time:%Y-%m-%d %HZ}  ->  F{frame.fxx:03d}"
              f"  ->  Valid {frame.valid_time:%Y-%m-%d %HZ}",
              ha="right", va="center", fontsize=9.5, color=MUTED_COLOR,
              transform=band.transAxes)

    # (8) Credit footer under the map, kept out of the header so the top stays
    # clean and does not read like the reference.
    fig.text(left_in / fig_w, (botpad_in + 0.12) / fig_h,
             f"{WATERMARK}  /  triple-a-tropics.com", ha="left", va="center",
             fontsize=9, color=MUTED_COLOR)

    fig.savefig(out_path, dpi=DPI, facecolor=BAND_BG)

    # --- GEOMETRY capture (must happen AFTER savefig) ----------------------
    # The map axes SHRINKS inside its fixed square box: set_aspect(geo_aspect)
    # + set_anchor("W") letterbox a non-square view, and matplotlib only
    # resolves that during the draw. Read before savefig, ax.get_position()
    # returns the UNSHRUNK rect and every pixel->lon/lat mapping built from it
    # is wrong by the letterbox margin - so it is read here, once the figure
    # has actually been drawn.
    #
    # With a plain linear lon/lat axes (no cartopy, no projection kwarg) the
    # pixel <-> lon/lat mapping is an exact affine, so these eight numbers are
    # everything a client needs for a lat/lon readout, a value readout under
    # the cursor, or a ruler - including on a storm-following nest, whose
    # extent moves every forecast hour.
    pos = ax.get_position()
    px_w, px_h = fig.get_size_inches() * DPI
    geometry = {
        # Axes rect in PIXELS of the output PNG, origin TOP-LEFT (the image
        # convention the browser uses; matplotlib's y0 is bottom-up).
        "axes_px": [round(pos.x0 * px_w, 2),
                    round((1.0 - pos.y1) * px_h, 2),
                    round(pos.width * px_w, 2),
                    round(pos.height * px_h, 2)],
        # Geographic extent of that rect: [W, S, E, N] in degrees.
        #
        # LONGITUDES ARE IN THE FRAME'S CONTINUOUS AXIS AND MAY EXCEED +-180.
        # A nest straddling the antimeridian is non-monotonic in signed
        # -180..180 (it jumps +180 -> -180), which would blow the extent out to
        # ~360 deg, so _choose_lon_frame stitches it into a continuous frame
        # running past +180 (e.g. 168..188). That frame is what the axes was
        # actually drawn in, so it is the ONLY frame in which pixel <-> lon is
        # the exact affine this block exists to provide - normalising it here
        # would make it non-monotonic and silently wrong.
        #
        # A consumer must therefore do the affine in THIS frame and normalise
        # only at display time (`while v > 180: v -= 360`, matching _lon_label).
        # `lon_frame` in the manifest's projection block declares this, and
        # `crosses_antimeridian` flags the frames where it actually bites.
        "bbox": [round(float(lon_min), 4), round(float(lat_min), 4),
                 round(float(lon_max), 4), round(float(lat_max), 4)],
        "crosses_antimeridian": bool(lon_max > 180.0 or lon_min < -180.0),
    }

    plt.close(fig)
    log.info("wrote %s", out_path)
    return geometry


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="hafsa", choices=["hafsa", "hafsb"])
    ap.add_argument("--storm", default="13l", help="storm id, e.g. 13l")
    ap.add_argument("--product", default="storm.atm",
                    choices=["storm.atm", "parent.atm"],
                    help="HAFS domain (Herbie product key)")
    ap.add_argument("--field", default="wind",
                    choices=["wind", "refl", "clean_ir", "water_vapor", "pwat"],
                    help="which product to render: 10 m wind, composite "
                         "reflectivity, simulated Clean IR, simulated Water "
                         "Vapor, or precipitable water (all overlay MSLP)")
    ap.add_argument("--enhancement", default=None,
                    help="override the tat_palettes enhancement for the "
                         "sim-sat fields (e.g. dvorak for clean_ir)")
    ap.add_argument("--date", default="2023-09-09 00:00",
                    help="cycle init time, 'YYYY-MM-DD HH:MM'")
    ap.add_argument("--fxx", type=int, default=12, help="forecast hour")
    ap.add_argument("--out", default="hafs_test.png")
    ap.add_argument("--save-dir", default=os.environ.get("HERBIE_DATA", "/tmp/herbie_data"))
    args = ap.parse_args()

    from hafs_render import hafs_registry as reg
    date = dt.datetime.strptime(args.date, "%Y-%m-%d %H:%M")
    want_refl = args.field == "refl"
    want_pwat = args.field == "pwat"
    render_product = {"wind": "mslp_wind", "refl": "refl",
                      "pwat": "mslp_pwat"}.get(args.field, args.field)
    sat_parm = reg.sat_parm(render_product)

    log.info("fetching %s %s %s %s f%03d (field=%s) …", args.model, args.storm,
             args.product, args.date, args.fxx, args.field)
    frame = fetch_hafs_frame(args.model, args.storm, args.product, date,
                             args.fxx, args.save_dir, want_refl=want_refl,
                             want_pwat=want_pwat, sat_parm=sat_parm)
    rmax = (np.nanmax(frame.refl_dbz) if frame.refl_dbz is not None else float("nan"))
    btmin = (np.nanmin(frame.bt_c) if frame.bt_c is not None else float("nan"))
    pwmax = (np.nanmax(frame.pwat) if frame.pwat is not None else float("nan"))
    log.info("  grid %d×%d  extent lon[%.2f,%.2f] lat[%.2f,%.2f]  "
             "wind max %.0f kt  mslp min %.1f hPa  refl max %.1f dBZ  "
             "bt min %.1f degC  pwat max %.1f mm",
             frame.lat.size, frame.lon.size, *frame.extent,
             np.nanmax(frame.wind_kt), np.nanmin(frame.mslp_hpa), rmax, btmin,
             pwmax)

    countries = (_load_geojson("ne_10m_admin_0_countries.geojson")
                 or _load_geojson("ne_50m_admin_0_countries.geojson")
                 or _load_geojson("ne_110m_admin_0_countries.geojson"))
    coast = (_load_geojson("ne_10m_coastline.geojson")
             or _load_geojson("ne_50m_coastline.geojson")
             or _load_geojson("ne_110m_coastline.geojson"))
    if not coast:
        log.warning("no coastline GeoJSON found - map will have no coastlines")
    # admin_1 state/province borders for the canonical basemap (50m; optional -
    # a missing layer just omits state borders).
    states = _load_geojson("ne_50m_admin_1_states_provinces.geojson")

    # The namesake's track fix (this fhr) + last-known anchor, same as the
    # full-cycle builder wires in - so the standalone smoke shows production
    # framing/stats. Best-effort: {} degrades honestly inside render_frame.
    track = fetch_hafs_track(args.model, args.storm, date)
    cen = track.get(args.fxx)
    prior = [t for t in sorted(track) if t <= args.fxx]
    anchor = track[prior[-1]] if prior else None
    render_frame(frame, args.out, countries, coast, states=states, product=render_product,
                 cen_lat=cen[0] if cen else None,
                 cen_lon=cen[1] if cen else None,
                 anchor_lat=anchor[0] if anchor else None,
                 anchor_lon=anchor[1] if anchor else None,
                 enhancement=args.enhancement)
    return 0


if __name__ == "__main__":
    sys.exit(main())
