#!/usr/bin/env python3
"""
Triple-A-Tropics · ARMOR3D weekly subsurface generator
======================================================

Complements the daily AOML TCHP/D26 maps with a weekly, anomaly-capable
view sourced from Copernicus Marine's ARMOR3D L4 product. ARMOR3D goes
back to 1993, so unlike the AOML archive (2022–present) we can build a
full 1993–2020 climatology and render proper anomalies.

Products rendered per weekly run
--------------------------------
For each of the 18 map regions (same list as generate_subsurface_plots.py):

    armor3d/<region>_tchp.png          # ARMOR3D-derived TCHP (absolute)
    armor3d/<region>_tchp_labels.png
    armor3d/<region>_d26.png           # ARMOR3D-derived D26 (absolute)
    armor3d/<region>_d26_labels.png
    armor3d/<region>_tchp_anom.png     # TCHP anomaly vs 1993–2020
    armor3d/<region>_tchp_anom_labels.png

For each of the 4 equatorial cross-section regions (5°S–5°N zonal mean):

    armor3d/<region>_crosssection.png  # longitude–depth T anomaly panel

Plus:
    armor3d/armor3d_meta.json

Data source
-----------
Copernicus Marine product MULTIOBS_GLO_PHY_TSUV_3D_MYNRT_015_012
("Global Ocean ARMOR3D L4 analysis and multi-year reanalysis").

Two datasets inside the product:
  * near-real-time weekly (NRT) — used for the current slice
  * multi-year reanalysis weekly (MY) — used offline to build climo

Fetched via the `copernicusmarine` Python client. Authentication uses
the CMEMS credentials the GitHub workflow exposes as env vars:
    COPERNICUSMARINE_SERVICE_USERNAME
    COPERNICUSMARINE_SERVICE_PASSWORD

Dataset + variable IDs can change during CMEMS platform migrations. If
the weekly workflow errors with "dataset not found", verify the current
IDs at https://data.marine.copernicus.eu and update the constants in
the "Data source" block below.

Climatology dependency
----------------------
TCHP anomaly maps require the pre-computed climatology file:
    armor3d/armor3d_climatology.nc
built once by `build_armor3d_climatology.py`. If the file is missing,
anomaly rendering is skipped (with a clear log line) and the raw maps
+ cross-sections still render.

No cartopy dependency — basemap is the same Natural Earth GeoJSON the
other workflows already cache.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")
mpl.rcParams["hatch.color"] = "#000000"
mpl.rcParams["hatch.linewidth"] = 0.7
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import xarray as xr

# Reuse plotting helpers from the existing subsurface module so the
# ARMOR3D maps visually match the AOML maps (same basemap drawing,
# wrap-aware subsetting, axis styling, watermark, colorbar placement).
import generate_subsurface_plots as gss

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE
ARMOR_DIR = OUTPUT_DIR / "armor3d"
ARMOR_DIR.mkdir(parents=True, exist_ok=True)

CACHE_DIR = HERE / ".armor3d_cache"
CACHE_DIR.mkdir(exist_ok=True)

CLIMATOLOGY_PATH = ARMOR_DIR / "armor3d_climatology.nc"


# --- Data source --------------------------------------------------------

# ARMOR3D NRT & multi-year reanalysis, 0.125° daily. CMEMS retired the
# P1W "weekly" aggregations during the 2025 catalog migration, so we now
# hit the daily product and naturally get the freshest single slice per
# workflow run. IDs verified against the CMEMS catalog as of 2026-04.
# If CMEMS renames them again, update both here and in
# build_armor3d_climatology.py.
ARMOR3D_NRT_DATASET = "cmems_obs-mob_glo_phy_nrt_0.125deg_P1D-m"
# Multi-year reanalysis for climatology (1993 -> recent).
ARMOR3D_MY_DATASET = "cmems_obs-mob_glo_phy_my_0.125deg_P1D-m"

# Variables we need from ARMOR3D for TCHP/D26 calculation. `to` is the
# ocean temperature (°C); `so` (salinity) is not required for TCHP but
# useful for future extensions (density-based heat content).
VARIABLES = ["to"]

# Depth range needed for TCHP integration — TCHP is ∫(T-26)+ dz from 0
# down to D26, which is always shallower than ~300 m in the tropics.
# We grab a little extra (0-500 m) to give the D26 interpolation room.
DEPTH_MIN = 0.0
DEPTH_MAX = 500.0


# --- TCHP / D26 constants ----------------------------------------------

# Thermodynamic constants for TCHP integration. TCHP = ρ · Cp · ∫(T-26)+ dz
# Standard operational values; match AOML's published algorithm.
SEAWATER_DENSITY = 1026.0      # kg/m³
SPECIFIC_HEAT    = 3996.0      # J / (kg · K)
# TCHP unit: integral gives J/m² — convert to kJ/cm² (standard display)
# by dividing by 1e7 (1 kJ = 1e3 J, 1 m² = 1e4 cm², so J/m² -> kJ/cm²
# is ÷ (1e3 * 1e4) = ÷ 1e7).
TCHP_UNIT_FACTOR = 1.0e7

# Reference isotherm — by convention 26 °C for TCHP / D26.
T_REF = 26.0


# --- Cross-section regions (ARMOR3D-specific) --------------------------

# Four equatorial regions where the 5°S-5°N zonal slice produces the
# canonical ENSO / Atlantic Niño / IOD / basin-tropical diagnostic.
# Longitudes are 0-360 convention so they match the ARMOR3D grid.
CROSSSECTION_REGIONS = {
    "enso": {
        "label": "ENSO Regions",
        "lon_min": 120.0,
        "lon_max": 290.0,
        "lat_min": -5.0,
        "lat_max":  5.0,
        "figsize": (14.0, 7.0),
    },
    "equatorial-atlantic": {
        "label": "Equatorial Atlantic",
        "lon_min": 310.0,   # 50°W
        "lon_max": 375.0,   # 15°E (wraps past 360)
        "lat_min": -5.0,
        "lat_max":  5.0,
        "figsize": (11.0, 7.0),
    },
    "indian-ocean": {
        "label": "Equatorial Indian Ocean",
        "lon_min":  40.0,
        "lon_max": 100.0,
        "lat_min":  -5.0,
        "lat_max":   5.0,
        "figsize": (11.0, 7.0),
    },
    "global-tropics": {
        "label": "Global Tropics (5°S–5°N)",
        "lon_min":  30.0,
        "lon_max": 390.0,   # full 360° wrap, Pacific-centered
        "lat_min":  -5.0,
        "lat_max":   5.0,
        "figsize": (15.0, 6.5),
    },
}


# --- Colormap for TCHP anomaly -----------------------------------------

# Symmetric diverging: deep blue (-60) -> white (0) -> deep red (+60)
# kJ/cm². Matches the palette used on cyclonicwx.com for TCHP anomaly.
TCHP_ANOM_VMAX = 60.0
TCHP_ANOM_CMAP = mcolors.LinearSegmentedColormap.from_list(
    "tchp_anom",
    [
        (0.00, "#053061"),
        (0.15, "#2166ac"),
        (0.30, "#4393c3"),
        (0.45, "#92c5de"),
        (0.50, "#f7f7f7"),
        (0.55, "#f4a582"),
        (0.70, "#d6604d"),
        (0.85, "#b2182b"),
        (1.00, "#67001d"),
    ],
)
# Paint NaN pixels (land + shallow-water gaps) with the same LAND_COLOR
# the SST/CRW plots use, so the subsurface products visually match on
# the site. Default behaviour would bleed through to the axes facecolor
# (a slightly different navy), creating subtle land-fill mismatches.
TCHP_ANOM_CMAP.set_bad(color=gss.LAND_COLOR, alpha=1.0)

# Cross-section temperature-anomaly colormap — matches the magenta/red/
# blue style of the cyclonicwx.com reference image.
CS_ANOM_VMAX = 6.0
CS_ANOM_CMAP = mcolors.LinearSegmentedColormap.from_list(
    "cs_anom",
    [
        (0.00, "#de2d87"),  # magenta (very cold anomaly)
        (0.12, "#6a2a86"),
        (0.25, "#3f51b5"),
        (0.38, "#4a90d9"),
        (0.44, "#b0d4ed"),
        (0.50, "#ffffff"),
        (0.56, "#fdd9a0"),
        (0.65, "#f89e4f"),
        (0.75, "#e04b2e"),
        (0.88, "#a21916"),
        (1.00, "#5e0a0a"),
    ],
)
CS_ANOM_CMAP.set_bad(color=gss.LAND_COLOR, alpha=1.0)

# Cross-section absolute-temperature colormap — used when no climatology
# file is available yet and we fall back to rendering raw T instead of
# an anomaly. Sequential cool→warm palette so deep cold water reads as
# deep blue and the surface mixed layer reads as orange/red, without
# falsely implying "anomaly".
CS_ABS_VMIN = 0.0
CS_ABS_VMAX = 30.0
CS_ABS_CMAP = mcolors.LinearSegmentedColormap.from_list(
    "cs_abs",
    [
        (0.00, "#08306b"),  # deep cold abyss
        (0.20, "#2171b5"),
        (0.40, "#6baed6"),
        (0.55, "#c6dbef"),
        (0.65, "#fee08b"),
        (0.80, "#f46d43"),
        (1.00, "#a50026"),  # warm surface mixed layer
    ],
)
CS_ABS_CMAP.set_bad(color=gss.LAND_COLOR, alpha=1.0)


# --- Copernicus Marine fetch -------------------------------------------


def _have_credentials() -> bool:
    """Check that CMEMS credentials are actually exposed to the process.

    copernicusmarine also supports a credentials file and `.netrc`, but
    in CI we rely exclusively on the two env vars so a missing secret
    fails fast with a clear error rather than hanging on an interactive
    prompt."""
    return bool(
        os.environ.get("COPERNICUSMARINE_SERVICE_USERNAME")
        and os.environ.get("COPERNICUSMARINE_SERVICE_PASSWORD")
    )


def _cmems_subset(
    dataset_id: str,
    start: dt.datetime,
    end: dt.datetime,
    *,
    lon_min: float = -180.0,
    lon_max: float = 180.0,
    lat_min: float = -75.0,
    lat_max: float =  75.0,
    depth_min: float = DEPTH_MIN,
    depth_max: float = DEPTH_MAX,
    variables: list[str] | None = None,
    out_path: Path,
    log: str = "[armor3d]",
) -> Path:
    """Download a subset of an ARMOR3D dataset to a local NetCDF.

    Thin wrapper around copernicusmarine.subset() so the rest of the
    script doesn't have to know the exact kwarg conventions. Retries
    twice on transient errors."""
    import copernicusmarine  # imported lazily — avoid import cost when unused

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    vars_arg = variables if variables is not None else VARIABLES
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            copernicusmarine.subset(
                dataset_id=dataset_id,
                variables=vars_arg,
                minimum_longitude=lon_min,
                maximum_longitude=lon_max,
                minimum_latitude=lat_min,
                maximum_latitude=lat_max,
                minimum_depth=depth_min,
                maximum_depth=depth_max,
                start_datetime=start.strftime("%Y-%m-%dT00:00:00"),
                end_datetime=end.strftime("%Y-%m-%dT23:59:59"),
                output_filename=out_path.name,
                output_directory=str(out_path.parent),
                overwrite=True,
                disable_progress_bar=True,
            )
            if out_path.exists() and out_path.stat().st_size > 0:
                return out_path
            raise RuntimeError(f"{dataset_id}: no output file produced")
        except Exception as exc:
            last_err = exc
            print(f"{log}   WARN attempt {attempt+1}/3 failed: {exc}")
            time.sleep(8 * (attempt + 1))
    assert last_err is not None
    raise last_err


def fetch_latest_slice(log: str = "[armor3d]") -> tuple[dt.datetime, Path]:
    """Download the most recent weekly ARMOR3D slice (global, top 500 m).

    ARMOR3D weekly has ~1-week latency, so we request a two-week window
    ending *today* and pick the last time step returned."""
    if not _have_credentials():
        raise RuntimeError(
            "CMEMS credentials missing. Set COPERNICUSMARINE_SERVICE_USERNAME "
            "and COPERNICUSMARINE_SERVICE_PASSWORD (repo secrets in CI)."
        )

    today = dt.datetime.utcnow().date()
    start = today - dt.timedelta(days=14)
    end   = today
    cache_file = CACHE_DIR / f"armor3d_latest_{today.isoformat()}.nc"

    if cache_file.exists() and cache_file.stat().st_size > 0:
        print(f"{log} using cached slice {cache_file.name}")
    else:
        print(
            f"{log} requesting ARMOR3D NRT slice "
            f"({start} → {end}) from CMEMS..."
        )
        _cmems_subset(
            dataset_id=ARMOR3D_NRT_DATASET,
            start=dt.datetime.combine(start, dt.time.min),
            end=dt.datetime.combine(end, dt.time.min),
            lon_min=-180.0, lon_max=180.0,
            lat_min=-75.0,  lat_max=75.0,
            depth_min=DEPTH_MIN, depth_max=DEPTH_MAX,
            variables=VARIABLES,
            out_path=cache_file,
            log=log,
        )

    # Identify the valid date of the most recent time step.
    with xr.open_dataset(cache_file) as ds:
        time_coord = ds.coords.get("time")
        if time_coord is None or time_coord.size == 0:
            raise RuntimeError("ARMOR3D slice has no time coordinate")
        latest = np.array(time_coord[-1])
        data_date = _np_datetime_to_date(latest)
    print(f"{log}   latest valid date: {data_date.isoformat()}")
    return dt.datetime.combine(data_date, dt.time.min), cache_file


def _np_datetime_to_date(ts) -> dt.date:
    """numpy.datetime64 / cftime / pandas.Timestamp → date."""
    try:
        return dt.datetime.utcfromtimestamp(
            (np.datetime64(ts) - np.datetime64("1970-01-01"))
            / np.timedelta64(1, "s")
        ).date()
    except Exception:
        # Fallback for cftime/pandas — fall back to string parsing.
        return dt.datetime.strptime(str(ts)[:10], "%Y-%m-%d").date()


# --- ARMOR3D data loading / TCHP + D26 computation ---------------------


def load_armor3d_temperature(
    path: Path, log: str = "[armor3d]",
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Load the latest weekly T(depth, lat, lon) field from `path`.

    Returns (T [K? °C?], lat, lon, depth) with lon rolled to 0-360 so
    it matches the subsurface/OISST convention."""
    with xr.open_dataset(path) as ds:
        # ARMOR3D variable name is "to" (temperature, °C).
        if "to" not in ds.variables:
            raise RuntimeError(
                f"variable 'to' missing from {path.name} — "
                f"available: {list(ds.data_vars)}"
            )
        t = ds["to"]
        # Squeeze to the latest time step if multiple times were fetched.
        if "time" in t.dims:
            t = t.isel(time=-1)
        t_arr = t.values.astype(np.float32)  # shape: (depth, lat, lon)
        depth = ds["depth"].values.astype(np.float32)
        lat = ds["latitude"].values.astype(np.float32)
        lon = ds["longitude"].values.astype(np.float32)

    # Roll -180..180 -> 0..360 to match AOML/OISST convention downstream.
    if lon.min() < 0:
        shift = int(np.sum(lon < 0))
        lon = np.concatenate([lon[shift:], lon[:shift] + 360])
        t_arr = np.concatenate(
            [t_arr[:, :, shift:], t_arr[:, :, :shift]], axis=2
        )

    print(
        f"{log}   ARMOR3D grid: depth={depth.size} lat={lat.size} "
        f"lon={lon.size} (lon {lon.min():.2f}..{lon.max():.2f})"
    )
    return t_arr, lat, lon, depth


def compute_d26(
    t_arr: np.ndarray, depth: np.ndarray,
) -> np.ndarray:
    """Depth of the 26 °C isotherm (m).

    For each (lat, lon), find the shallowest depth where T crosses 26°C
    from above. Linearly interpolate between bracket levels. Returns
    NaN where the surface is already colder than 26°C or where the
    column is too shallow / below-threshold throughout."""
    # t_arr shape: (depth, lat, lon). Reorder axes so depth is last to
    # make the vectorized crossing search cleaner.
    T = np.moveaxis(t_arr, 0, -1)  # (lat, lon, depth)
    above = T > T_REF  # where T > 26

    # Handle NaNs: treat them as "below" (i.e., not above 26).
    above = np.where(np.isnan(T), False, above)

    # For each column, find the deepest level that is still above 26.
    # Moving downward the column goes warm -> cold, so the crossing is
    # at the first index k where above[k]=True AND above[k+1]=False.
    nk = T.shape[-1]
    # Index of last True along depth. We reverse + argmax for this.
    rev_above = above[..., ::-1]
    # If no level is above 26, argmax returns 0 (first element). Guard
    # against that with an `any_above` check.
    any_above = above.any(axis=-1)
    last_above_rev_idx = np.argmax(rev_above, axis=-1)  # (lat, lon)
    last_above_idx = (nk - 1) - last_above_rev_idx  # index k

    # Interpolate depth where T == 26 between depth[k] and depth[k+1].
    # If k is already the last depth (no crossing captured within the
    # fetched range), skip — we treat that as "D26 deeper than 500 m"
    # and return NaN so the map shows a masked pixel.
    d26 = np.full(T.shape[:-1], np.nan, dtype=np.float32)
    valid = any_above & (last_above_idx < nk - 1)

    # Gather neighbors (requires explicit take-along for float data).
    # Clip idx_k1 to stay in-bounds for columns where the warm layer
    # reaches the deepest fetched level (idx_k == nk-1). The `valid`
    # mask below already excludes those columns from the final output,
    # but the gather itself has to not IndexError first.
    idx_k  = last_above_idx
    idx_k1 = np.minimum(idx_k + 1, nk - 1)
    ii, jj = np.indices(T.shape[:-1])
    T_k  = T[ii, jj, idx_k]
    T_k1 = T[ii, jj, idx_k1]
    z_k  = depth[idx_k]
    z_k1 = depth[idx_k1]

    # Linear interpolation: T(z) = T_k + (T_k1 - T_k) * (z - z_k)/(z_k1-z_k)
    # Solve T=T_REF → z = z_k + (T_REF - T_k) * (z_k1 - z_k)/(T_k1 - T_k)
    denom = T_k1 - T_k
    with np.errstate(divide="ignore", invalid="ignore"):
        frac = np.where(denom != 0, (T_REF - T_k) / denom, 0.0)
    z_cross = z_k + frac * (z_k1 - z_k)
    d26[valid] = z_cross[valid]

    return d26


def compute_tchp(
    t_arr: np.ndarray, depth: np.ndarray, d26: np.ndarray,
) -> np.ndarray:
    """Tropical Cyclone Heat Potential (kJ/cm²).

    TCHP = ρ · Cp · ∫(T − T_ref)+ dz from surface to D26.

    We integrate the positive part of (T − 26) over the column using
    trapezoidal pieces between the fetched depth levels. Columns with
    no D26 (T never crosses 26) are set to 0 rather than NaN — that's
    the operational convention (no heat available to fuel a TC)."""
    # (lat, lon, depth)
    T = np.moveaxis(t_arr, 0, -1)
    dT = T - T_REF
    # Clip negative contributions — we only count heat *above* 26°C.
    dT = np.where(np.isnan(dT), 0.0, dT)
    dT = np.clip(dT, 0.0, None)

    # Trapezoidal integration over depth — sums each column's positive-
    # anomaly integral [°C · m]. NumPy 2.0 renamed trapz -> trapezoid;
    # fall through to the old name so this works on both numpy majors.
    _trapz = getattr(np, "trapezoid", None) or np.trapz
    integral = _trapz(dT, x=depth, axis=-1)  # (lat, lon), units: °C·m

    # Convert °C·m -> J/m² -> kJ/cm². ρ·Cp carries the "energy per K·m³"
    # units, times 1 m² cross-section = J/m²; divide by TCHP_UNIT_FACTOR
    # to reach kJ/cm².
    tchp = integral * SEAWATER_DENSITY * SPECIFIC_HEAT / TCHP_UNIT_FACTOR

    # Where D26 is NaN (no warm layer), TCHP is 0 by definition.
    tchp = np.where(np.isnan(d26), 0.0, tchp).astype(np.float32)
    return tchp


# --- Climatology (optional — only present once pre-compute has run) ----


def load_tchp_climatology() -> xr.DataArray | None:
    """Return the TCHP climatology DataArray or None if not yet built."""
    if not CLIMATOLOGY_PATH.exists():
        return None
    try:
        ds = xr.open_dataset(CLIMATOLOGY_PATH)
        if "tchp_climo" not in ds.variables:
            return None
        return ds["tchp_climo"]
    except Exception as exc:
        print(f"[armor3d]   WARN: could not open climatology: {exc}")
        return None


def climatology_slice_for(
    climo: xr.DataArray, valid_date: dt.date,
) -> np.ndarray:
    """Pick the climatology's nearest week-of-year to `valid_date`.

    Climatology is indexed by week (1..52). We round the day-of-year to
    the nearest 7-day block so summer's valid anomaly for the current
    week is compared against the same calendar week's 1993-2020 mean."""
    woy = max(1, min(52, (valid_date.timetuple().tm_yday - 1) // 7 + 1))
    return climo.sel(week=woy).values.astype(np.float32)


# --- Cross-section rendering -------------------------------------------


def plot_cross_section(
    t_arr_now: np.ndarray,
    t_arr_climo: np.ndarray | None,
    lat: np.ndarray, lon: np.ndarray, depth: np.ndarray,
    region_key: str, region: dict,
    valid_date: dt.date,
    countries, coast,
    out_path: Path,
    climo_lon: np.ndarray | None = None,
) -> bool:
    """5°S-5°N longitude-depth cross-section with 20 °C isotherm overlay.

    Mirrors the cyclonicwx.com reference:
      * small inset map at the top showing the averaged latitude band
      * main panel: longitude × depth, temperature anomaly colour fill
      * solid line = current 20 °C isotherm
      * dashed line = climatological 20 °C isotherm (if climo available)

    ``t_arr_climo`` may be either:
      * 2D (depth, lon) — already 5°S–5°N zonal-averaged (new climo format
        written by build_armor3d_climatology.py as `t_climo_eq`), or
      * 3D (depth, lat, lon) — legacy form averaged on the fly here.
    In the 2D case, `climo_lon` must supply the climatology's lon grid so
    we can align it with the current-data lon grid before interpolation.
    """
    lon_min = region["lon_min"]; lon_max = region["lon_max"]
    lat_min = region["lat_min"]; lat_max = region["lat_max"]

    # Build the zonal-mean cross-section by averaging over the narrow
    # lat band. We slice lat first, then lon (with wrap support for
    # regions whose lon_max > 360).
    lat_mask = (lat >= lat_min) & (lat <= lat_max)
    if not lat_mask.any():
        return False

    # Handle lon wrap for extent > 360 (Eq Atlantic, Global Tropics).
    lon_w = lon.copy()
    t_w = t_arr_now
    # Track climo handling separately — may arrive as 2D or 3D.
    climo_is_2d = t_arr_climo is not None and t_arr_climo.ndim == 2
    climo_is_3d = t_arr_climo is not None and t_arr_climo.ndim == 3
    climo_lon_w = None
    if climo_is_2d:
        if climo_lon is None:
            # If the caller didn't supply a separate climo lon grid,
            # assume the climo is on the same grid as the current data.
            climo_lon_w = lon.copy()
        else:
            climo_lon_w = climo_lon.copy()

    if lon_max > 360:
        wrap_cut = lon_max - 360
        wrap_mask = lon_w < wrap_cut
        if np.any(wrap_mask):
            lon_w = np.concatenate([lon_w, lon_w[wrap_mask] + 360])
            t_w = np.concatenate(
                [t_w, t_w[:, :, wrap_mask]], axis=2
            )
            if climo_is_3d:
                t_arr_climo = np.concatenate(
                    [t_arr_climo,
                     t_arr_climo[:, :, wrap_mask]], axis=2
                )
        if climo_is_2d and climo_lon_w is not None:
            wrap_mask_c = climo_lon_w < wrap_cut
            if np.any(wrap_mask_c):
                climo_lon_w = np.concatenate(
                    [climo_lon_w, climo_lon_w[wrap_mask_c] + 360]
                )
                t_arr_climo = np.concatenate(
                    [t_arr_climo, t_arr_climo[:, wrap_mask_c]], axis=1
                )

    lon_mask = (lon_w >= lon_min) & (lon_w <= lon_max)
    if not lon_mask.any():
        return False

    # Slice + zonal mean over lat for the "now" field.
    T_now   = t_w[:, lat_mask][:, :, lon_mask]    # (depth, lat, lon)
    T_now_m = np.nanmean(T_now, axis=1)            # (depth, lon)
    lon_cs  = lon_w[lon_mask]

    # Anomaly field (vs climatology if present). If no climatology is
    # available yet we switch to absolute-temperature mode below rather
    # than faking an anomaly — T_now − 26°C on a diverging ±6°C colormap
    # saturates the whole water column and is deeply misleading.
    has_climo = True
    if climo_is_2d:
        # Interpolate the climo (depth, lon) onto our lon_cs grid.
        T_cl_m = np.empty_like(T_now_m)
        for di in range(T_cl_m.shape[0]):
            # np.interp needs strictly monotonically increasing x.
            sort_ix = np.argsort(climo_lon_w)
            T_cl_m[di, :] = np.interp(
                lon_cs, climo_lon_w[sort_ix], t_arr_climo[di, sort_ix],
                left=np.nan, right=np.nan,
            )
        anom = T_now_m - T_cl_m
    elif climo_is_3d:
        T_cl = t_arr_climo[:, lat_mask][:, :, lon_mask]
        T_cl_m = np.nanmean(T_cl, axis=1)
        anom = T_now_m - T_cl_m
    else:
        T_cl_m = None
        has_climo = False
        anom = None  # unused in absolute-T mode

    # --- Figure layout: title band | inset map | cross-section -------
    # Three vertical bands so nothing overlaps:
    #   * top ~14% is a dedicated title/subtitle band (no axes in it)
    #   * next ~24% is the inset map, drawn at aspect="equal" so the
    #     continents never look squished no matter how wide the lon
    #     window is
    #   * remaining ~62% is the cross-section panel
    fig = plt.figure(figsize=region["figsize"], facecolor=gss.BG_COLOR)
    gs = fig.add_gridspec(
        nrows=2, ncols=1, height_ratios=[1, 2.5],
        left=0.06, right=0.93,
        top=0.86, bottom=0.09, hspace=0.22,
    )
    ax_map = fig.add_subplot(gs[0, 0])
    ax_cs  = fig.add_subplot(gs[1, 0])

    # Inset: tight lat padding (±10°) so the highlighted 5°S–5°N band
    # is visually meaningful. aspect="auto" makes the inset fill the full
    # width of its gridspec cell so it matches the cross-section panel below.
    ax_map.set_xlim(lon_min, lon_max)
    ax_map.set_ylim(lat_min - 10, lat_max + 10)
    ax_map.set_aspect("auto")
    ax_map.set_facecolor(gss.OCEAN_COLOR)
    gss._draw_filled_land(
        ax_map,
        (lon_min, lon_max, lat_min - 10, lat_max + 10),
        countries,
    )
    gss._draw_basemap(
        ax_map,
        (lon_min, lon_max, lat_min - 10, lat_max + 10),
        countries, coast,
    )
    # Shaded lat band so the viewer sees which slice was averaged.
    ax_map.axhspan(lat_min, lat_max, color="#ffffff", alpha=0.15, zorder=4)
    ax_map.set_xticks([])
    ax_map.set_yticks([])
    for spine in ax_map.spines.values():
        spine.set_color(gss.MUTED_COLOR)
        spine.set_linewidth(0.4)

    # contourf (not pcolormesh) for smooth bands and to cleanly skip NaN
    # columns in the 5°S–5°N band near the Maritime Continent (which
    # previously rendered as vertical stripes via .set_bad()).
    LON2, DEPTH2 = np.meshgrid(lon_cs, depth)
    ax_cs.set_facecolor(gss.OCEAN_COLOR)
    if has_climo:
        norm = mcolors.Normalize(vmin=-CS_ANOM_VMAX, vmax=CS_ANOM_VMAX)
        cf_levels = np.linspace(-CS_ANOM_VMAX, CS_ANOM_VMAX, 25)
        pcm = ax_cs.contourf(
            LON2, DEPTH2, anom, levels=cf_levels,
            cmap=CS_ANOM_CMAP, norm=norm, extend="both", zorder=1,
        )
    else:
        norm = mcolors.Normalize(vmin=CS_ABS_VMIN, vmax=CS_ABS_VMAX)
        cf_levels = np.linspace(CS_ABS_VMIN, CS_ABS_VMAX, 25)
        pcm = ax_cs.contourf(
            LON2, DEPTH2, T_now_m, levels=cf_levels,
            cmap=CS_ABS_CMAP, norm=norm, extend="both", zorder=1,
        )
    for coll in pcm.collections:
        coll.set_edgecolor("face")
        coll.set_antialiased(True)
    ax_cs.invert_yaxis()
    ax_cs.set_ylim(500, 0)

    # 20°C isotherm overlays — current (solid) + climo (dashed).
    try:
        cs_now = ax_cs.contour(
            LON2, DEPTH2, T_now_m, levels=[20.0],
            colors="#000000", linewidths=1.6, zorder=3,
        )
    except Exception:
        cs_now = None
    if T_cl_m is not None:
        try:
            ax_cs.contour(
                LON2, DEPTH2, T_cl_m, levels=[20.0],
                colors="#000000", linewidths=1.4,
                linestyles="dashed", zorder=3,
            )
        except Exception:
            pass

    # Legend marker for the two isotherms.
    from matplotlib.lines import Line2D
    legend_items = [
        Line2D([0], [0], color="#000000", linewidth=1.6,
               label="20 °C Isotherm"),
    ]
    if T_cl_m is not None:
        legend_items.append(
            Line2D([0], [0], color="#000000", linewidth=1.4,
                   linestyle="dashed",
                   label="20 °C Isotherm Climatology"),
        )
    ax_cs.legend(
        handles=legend_items, loc="lower right",
        fontsize=8, framealpha=0.85,
    )

    # Axis styling — longitude ticks with E/W folding, depth ticks.
    ax_cs.xaxis.set_major_locator(mticker.MultipleLocator(
        20 if (lon_max - lon_min) <= 180 else 30
    ))
    ax_cs.xaxis.set_major_formatter(
        mticker.FuncFormatter(gss._lon_tick_label)
    )
    ax_cs.set_xlim(lon_min, lon_max)
    ax_cs.set_ylabel("Depth (m)", color=gss.TEXT_COLOR, fontsize=10)
    ax_cs.tick_params(colors=gss.MUTED_COLOR, labelsize=9)
    ax_cs.grid(True, linewidth=0.3, color="#2a3e5c", alpha=0.35, zorder=2)
    for spine in ax_cs.spines.values():
        spine.set_color(gss.MUTED_COLOR)
        spine.set_linewidth(0.5)
    # facecolor is set before the contourf call so NaN gaps show through
    # as OCEAN_COLOR — do not override it here.

    # Title block — same style as the maps. Wording switches with mode
    # so we never label raw T as an "anomaly".
    date_label = valid_date.strftime("%B %-d, %Y")
    if has_climo:
        title = f"{region['label']} · Subsurface Temperature Anomalies"
        subtitle = (
            f"Valid: {date_label}  ·  ARMOR3D · 5°S–5°N zonal mean"
        )
        cb_label = "Temperature anomaly (°C)"
    else:
        title = f"{region['label']} · Subsurface Temperature"
        subtitle = (
            f"Valid: {date_label}  ·  ARMOR3D · 5°S–5°N zonal mean"
        )
        cb_label = "Temperature (°C)"
    # Title + subtitle sit in the dedicated top band (fig y ≈ 0.86-1.0).
    # Spread them out so a 15-pt bold title and a 10-pt subtitle can't
    # collide no matter how matplotlib renders them.
    fig.suptitle(
        title, color=gss.TEXT_COLOR, fontsize=15, fontweight="bold",
        x=0.06, ha="left", y=0.955,
    )
    fig.text(
        0.06, 0.905, subtitle,
        color=gss.MUTED_COLOR, fontsize=10, ha="left",
    )

    # Colorbar to the right of the main panel. Anchor its vertical
    # extent to the cross-section axis so it tracks whatever layout
    # the gridspec produces, instead of hard-coding y0/height.
    cs_pos = ax_cs.get_position()
    cax = fig.add_axes([cs_pos.x1 + 0.012, cs_pos.y0,
                        0.012, cs_pos.height])
    cb = fig.colorbar(pcm, cax=cax, extend="both")
    cb.set_label(cb_label, color=gss.TEXT_COLOR, fontsize=9)
    cb.ax.yaxis.set_tick_params(color=gss.MUTED_COLOR,
                                labelcolor=gss.MUTED_COLOR, labelsize=8)
    cb.outline.set_edgecolor(gss.MUTED_COLOR)
    cb.outline.set_linewidth(0.4)

    # Watermark on the main panel.
    gss._draw_watermark(ax_cs)

    fig.savefig(out_path, dpi=150, facecolor=gss.BG_COLOR)
    plt.close(fig)
    return True


# --- TCHP anomaly map helper -------------------------------------------


def plot_tchp_anom(
    anom_field: np.ndarray,
    lat: np.ndarray, lon: np.ndarray,
    extent: tuple, figsize: tuple,
    title: str, subtitle: str,
    countries, coast,
    out_path: Path,
) -> bool:
    """TCHP-anomaly map (kJ/cm² relative to 1993–2020 climatology).

    Reuses generate_subsurface_plots.py's _plot_field plumbing by
    hand-rolling a diverging-colormap call — _plot_field is hard-wired
    to extend='max', which doesn't fit signed anomalies."""
    sub, la, lo = gss._subset_to_extent(anom_field, lat, lon, extent)
    if sub.size == 0:
        return False
    fig, ax = plt.subplots(figsize=figsize, facecolor=gss.BG_COLOR)
    LON2, LAT2 = np.meshgrid(lo, la)
    norm = mcolors.Normalize(vmin=-TCHP_ANOM_VMAX, vmax=TCHP_ANOM_VMAX)
    pcm = ax.pcolormesh(
        LON2, LAT2, sub, cmap=TCHP_ANOM_CMAP, norm=norm,
        shading="auto", zorder=1, rasterized=True,
    )
    # Zero-line contour so the viewer can quickly read warm vs cool.
    try:
        ax.contour(
            LON2, LAT2, sub, levels=[0.0],
            colors="#1a1a1a", linewidths=0.6, alpha=0.7, zorder=1.5,
        )
    except Exception:
        pass
    gss._draw_basemap(ax, extent, countries, coast)
    gss._style_axes(ax, extent, title, subtitle)
    # _style_axes sets the axes face to PANEL_COLOR (slightly darker
    # than LAND_COLOR). For the anomaly plot that creates a visible
    # seam at the +/-60 deg climatology lat band (outside the band we
    # have no climo so pcolormesh draws nothing and the axes face
    # shows through; inside the band our NaN mask paints LAND_COLOR
    # via set_bad). Force the axes face to LAND_COLOR so land, masked
    # ocean, and the poles all render as the same uniform navy.
    ax.set_facecolor(gss.LAND_COLOR)

    cax = fig.add_axes([0.91, 0.18, 0.018, 0.64])
    cb = fig.colorbar(pcm, cax=cax, extend="both",
                      ticks=np.arange(-60, 61, 20))
    cb.set_label("TCHP anomaly (kJ/cm²)",
                 color=gss.TEXT_COLOR, fontsize=10)
    cb.ax.yaxis.set_tick_params(color=gss.MUTED_COLOR,
                                labelcolor=gss.MUTED_COLOR, labelsize=9)
    cb.outline.set_edgecolor(gss.MUTED_COLOR)
    cb.outline.set_linewidth(0.4)

    gss._draw_watermark(ax)
    fig.subplots_adjust(left=0.05, right=0.89, top=0.86, bottom=0.08)
    fig.savefig(out_path, dpi=150, facecolor=gss.BG_COLOR)

    # Labels variant — add labeled contours at ±10/20/40 kJ/cm².
    try:
        cs = ax.contour(
            LON2, LAT2, sub,
            levels=[-40, -20, -10, 10, 20, 40],
            colors="#000000", linewidths=0.7, alpha=0.8, zorder=1.55,
        )
        labels = ax.clabel(cs, inline=True, inline_spacing=3,
                           fontsize=7, fmt="%+d", colors="#000000")
        from matplotlib import patheffects as pe
        for lbl in labels:
            lbl.set_path_effects([
                pe.withStroke(linewidth=1.6, foreground="#ffffff"),
            ])
            lbl.set_fontweight("bold")
        fig.savefig(gss._labels_path(out_path), dpi=150, facecolor=gss.BG_COLOR)
    except Exception:
        pass
    plt.close(fig)
    return True


# --- Main ---------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render weekly ARMOR3D subsurface products "
                    "(TCHP, D26, TCHP anomaly, equatorial cross-sections)."
    )
    parser.add_argument(
        "--region", default=None,
        help="Optional: render one map region only (for quick iteration).",
    )
    parser.add_argument(
        "--cross-section-only", action="store_true",
        help="Skip map rendering; render cross-sections only.",
    )
    parser.add_argument(
        "--maps-only", action="store_true",
        help="Skip cross-sections; render map products only.",
    )
    parser.add_argument(
        "--no-anomaly", action="store_true",
        help="Skip TCHP anomaly even if climatology is available.",
    )
    args = parser.parse_args(argv)

    log = "[armor3d]"
    print(f"{log} starting ARMOR3D generator")

    # 1) Fetch latest weekly slice.
    valid_dt, cache_file = fetch_latest_slice(log)
    valid_date = valid_dt.date()

    # 2) Load T(depth, lat, lon) — lon normalized to 0-360.
    t_now, lat, lon, depth = load_armor3d_temperature(cache_file, log)

    # 3) Load climatology (may be None if not yet built).
    climo_da = load_tchp_climatology()
    tchp_climo_slice = None
    if climo_da is not None and not args.no_anomaly:
        try:
            tchp_climo_slice = climatology_slice_for(climo_da, valid_date)
            print(f"{log}   TCHP climatology loaded "
                  f"(week-of-year nearest {valid_date}).")
        except Exception as exc:
            print(f"{log}   WARN: climo slice failed: {exc}")
            tchp_climo_slice = None
    else:
        print(f"{log}   TCHP climatology: NOT AVAILABLE — anomaly maps "
              f"will be skipped. Run build_armor3d_climatology.py to "
              f"generate armor3d/armor3d_climatology.nc.")

    # 4) Compute derived fields (TCHP, D26).
    print(f"{log} computing D26 + TCHP from T(z)...")
    d26 = compute_d26(t_now, depth)
    tchp = compute_tchp(t_now, depth, d26)
    print(
        f"{log}   TCHP range: "
        f"{float(np.nanmin(tchp)):.1f} → {float(np.nanmax(tchp)):.1f} kJ/cm²"
    )
    print(
        f"{log}   D26  range: "
        f"{float(np.nanmin(d26)):.1f} → {float(np.nanmax(d26)):.1f} m"
    )

    # 5) Basemaps.
    countries = gss._load_geojson("ne_50m_admin_0_countries.geojson")
    coast = gss._load_geojson("ne_50m_coastline.geojson")

    date_label = valid_date.strftime("%B %-d, %Y")
    rendered: list[str] = []

    # 6) Map products — TCHP, D26, (TCHP anomaly if climo present).
    if not args.cross_section_only:
        regions_to_render = (
            {args.region: gss.REGIONS[args.region]}
            if args.region and args.region in gss.REGIONS
            else gss.REGIONS
        )
        for rkey, r in regions_to_render.items():
            extent = r["extent"]; figsize = r["figsize"]; label = r["label"]

            # TCHP (absolute)
            out = ARMOR_DIR / f"{rkey}_tchp.png"
            if gss.plot_tchp(
                tchp, lat, lon, extent, figsize,
                title=f"{label} · Tropical Cyclone Heat Potential",
                subtitle=f"Valid: {date_label}  ·  ARMOR3D",
                countries=countries, coast=coast,
                out_path=out,
            ):
                print(f"{log}   ✓ {out.name}")
                rendered.append(out.name)

            # D26 (absolute)
            out = ARMOR_DIR / f"{rkey}_d26.png"
            if gss.plot_d26(
                d26, lat, lon, extent, figsize,
                title=f"{label} · Depth of 26 °C Isotherm",
                subtitle=f"Valid: {date_label}  ·  ARMOR3D",
                countries=countries, coast=coast,
                out_path=out,
            ):
                print(f"{log}   ✓ {out.name}")
                rendered.append(out.name)

            # TCHP anomaly — skip if climo not built yet.
            if tchp_climo_slice is not None:
                # NRT data comes on the full ARMOR3D native grid
                # (~-75..+75 lat, 1200 pts) while the climatology was
                # built on -60..+60 only (LAT_MIN/MAX in
                # build_armor3d_climatology.py -> 960 pts). Crop the
                # NRT arrays to the climo's exact lat range so the
                # shapes line up before subtraction.
                climo_lat = climo_da["latitude"].values
                eps = 1e-4
                _mask = (
                    (lat >= float(climo_lat.min()) - eps)
                    & (lat <= float(climo_lat.max()) + eps)
                )
                tchp_crop = tchp[_mask, :]
                anom_lat = lat[_mask]
                if tchp_crop.shape != tchp_climo_slice.shape:
                    print(
                        f"{log}   WARN: anom shape mismatch "
                        f"{tchp_crop.shape} vs {tchp_climo_slice.shape} "
                        "-- skipping anomaly"
                    )
                else:
                    # Mask to NaN where both NRT and climo show
                    # essentially no TCHP -- land, polar water, cold
                    # tongue areas without a 26C isotherm. Without
                    # this, "0 - 0 = 0" paints the colormap's cream
                    # zero-color straight across the continents and
                    # obscures them. 1 kJ/cm^2 is well below any real
                    # signal (active regions are 50+ kJ/cm^2).
                    _near0 = 1.0
                    _nosig = (
                        (np.abs(tchp_crop) < _near0)
                        & (np.abs(tchp_climo_slice) < _near0)
                    )
                    anom_raw = tchp_crop - tchp_climo_slice
                    anom = np.where(_nosig, np.nan, anom_raw).astype(np.float32)
                    out = ARMOR_DIR / f"{rkey}_tchp_anom.png"
                    if plot_tchp_anom(
                        anom, anom_lat, lon, extent, figsize,
                        title=f"{label} · TCHP Anomaly",
                        subtitle=(
                            f"Valid: {date_label}  ·  ARMOR3D · "
                            f"anomaly vs. 1993-2020 weekly climatology"
                        ),
                        countries=countries, coast=coast,
                        out_path=out,
                    ):
                        print(f"{log}   ✓ {out.name}")
                        rendered.append(out.name)

    # 7) Cross-sections — 4 equatorial regions.
    if not args.maps_only:
        # Climatology for cross-sections is the 5°S–5°N zonal-mean T
        # pre-computed by build_armor3d_climatology.py and stored as
        # `t_climo_eq` with shape (week, depth, lon). If the file is
        # missing or lacks that variable, we still render the cross
        # sections — just without climatology overlay/anomaly.
        t_climo_slice_eq = None
        climo_lon = None
        if CLIMATOLOGY_PATH.exists():
            try:
                with xr.open_dataset(CLIMATOLOGY_PATH) as ds:
                    if "t_climo_eq" in ds.variables:
                        woy = max(
                            1, min(52,
                                   (valid_date.timetuple().tm_yday - 1)//7 + 1),
                        )
                        t_climo_slice_eq = (
                            ds["t_climo_eq"].sel(week=woy).values.astype(np.float32)
                        )
                        climo_lon = ds["longitude"].values.astype(np.float32)
            except Exception as exc:
                print(f"{log}   WARN: could not load t_climo_eq: {exc}")

        for rkey, region in CROSSSECTION_REGIONS.items():
            out = ARMOR_DIR / f"{rkey}_crosssection.png"
            if plot_cross_section(
                t_now, t_climo_slice_eq,
                lat, lon, depth,
                rkey, region,
                valid_date,
                countries, coast,
                out_path=out,
                climo_lon=climo_lon,
            ):
                print(f"{log}   ✓ {out.name}")
                rendered.append(out.name)

    # 8) Metadata.
    meta = {
        "date": valid_date.isoformat(),
        "updated_utc": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "source": "Copernicus Marine ARMOR3D (MULTIOBS_GLO_PHY_TSUV_3D_MYNRT_015_012)",
        "regions": list(gss.REGIONS.keys()),
        "cross_section_regions": list(CROSSSECTION_REGIONS.keys()),
        "climatology_available": tchp_climo_slice is not None,
        "rendered": sorted(rendered),
    }
    with open(ARMOR_DIR / "armor3d_meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    print(f"{log}   ✓ armor3d_meta.json ({len(rendered)} PNGs)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
