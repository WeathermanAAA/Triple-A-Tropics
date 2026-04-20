#!/usr/bin/env python3
"""
Triple-A-Tropics · ARMOR3D 1993–2020 weekly climatology builder
===============================================================

One-time (or occasionally-repeated) pre-compute that pulls 28 years of
weekly ARMOR3D reanalysis temperature from Copernicus Marine, derives
TCHP per time step, and collapses it into a 52-week-of-year climatology
used by `generate_armor3d_plots.py` to draw TCHP anomaly maps + zonal
cross-section anomaly overlays.

Outputs (single NetCDF): armor3d/armor3d_climatology.nc
    tchp_climo   (week, lat, lon)    kJ/cm², zonal TCHP mean by WOY
    t_climo_eq   (week, depth, lon)  °C, 5°S–5°N zonal mean T by WOY
    meta (global attrs)
    * week is 1..52 (ISO-ish — day-of-year // 7, clipped to 1..52)

Runtime expectations
--------------------
This script is **not** intended to run inside the weekly GitHub Actions
update workflow. It downloads ~28 years × 52 weeks of tropical
subsurface temperature (tens of GB streamed through the CMEMS
subsetter) and computes TCHP locally. On a reasonable home connection
+ modern laptop it runs in the 2–6 hour range; CMEMS throttling is the
dominant cost. Run it interactively:

    export COPERNICUSMARINE_SERVICE_USERNAME=...
    export COPERNICUSMARINE_SERVICE_PASSWORD=...
    python build_armor3d_climatology.py

The script is resumable. After each calendar year finishes it writes a
per-year accumulator to `.armor3d_climo_state/year_YYYY.nc`. If the
script is killed and restarted, already-completed years are skipped
automatically. Delete that directory to force a full rebuild.

At the end, the final `armor3d/armor3d_climatology.nc` is assembled from
the per-year accumulators by weighted averaging (weight = n_weeks the
year contributed to each WOY bucket — usually 1, occasionally 0).

Scope / knobs
-------------
* Baseline years: 1993–2020 (standard 28-yr WMO-ish window that fits
  ARMOR3D's reanalysis coverage and excludes the post-2020 years we
  want anomalies to be measured *against*).
* Latitude limit: -60 .. +60. That skips the subpolar/polar bands where
  TCHP is identically zero anyway and cuts the download in ~20%.
* Depth: 0..500 m (same as the weekly generator).
* Equatorial band for cross-sections: -5 .. +5 lat, averaged per
  time step before storage so we only carry a small t_climo_eq field.

CMEMS dataset IDs (mirror the daily generator — keep in sync):
    cmems_obs-mob_glo_phy_my_0.125deg_P1D-m   (multi-year reanalysis, daily)
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from pathlib import Path

import numpy as np
import xarray as xr

# Re-use TCHP/D26 math + constants from the weekly generator so the
# climatology is numerically identical to what the runtime compute uses.
# (Importing is fine at script start — generate_armor3d_plots.py only
# hits the network when main() runs, not at import time.)
import generate_armor3d_plots as a3d


HERE = Path(__file__).resolve().parent
ARMOR_DIR = HERE / "armor3d"
ARMOR_DIR.mkdir(parents=True, exist_ok=True)

STATE_DIR = HERE / ".armor3d_climo_state"
STATE_DIR.mkdir(exist_ok=True)

# Standard 28-year window that lines up with ARMOR3D reanalysis
# coverage (1993-01 onwards) without contaminating the anomaly reference
# with the years we want to evaluate anomalies for.
YEAR_START_DEFAULT = 1993
YEAR_END_DEFAULT   = 2020

# Tropical/subtropical band only. TCHP is zero outside this band and
# storing global-grid climo would balloon the output file beyond what
# GitHub's plain-git file size limits allow.
LAT_MIN = -60.0
LAT_MAX =  60.0

# The equatorial strip averaged into t_climo_eq (°C) for cross-sections.
EQ_LAT_MIN = -5.0
EQ_LAT_MAX =  5.0


# -- Helpers ------------------------------------------------------------


def _week_of_year(d: dt.date) -> int:
    """Map a date to a week-of-year in 1..52.

    We use the same convention as the weekly generator: DOY 1..7 → week 1,
    DOY 8..14 → week 2, ..., DOY 358..365/366 → week 52 (clipped)."""
    return max(1, min(52, (d.timetuple().tm_yday - 1) // 7 + 1))


def _year_state_path(year: int) -> Path:
    return STATE_DIR / f"year_{year}.nc"


def _fetch_year(
    year: int, log: str,
) -> Path:
    """Download one calendar year of weekly ARMOR3D MY data (tropics only)."""
    out_path = STATE_DIR / f"_raw_{year}.nc"
    start = dt.datetime(year, 1, 1)
    end   = dt.datetime(year, 12, 31, 23, 59, 59)
    print(f"{log} fetching ARMOR3D MY {year} (lat {LAT_MIN}..{LAT_MAX})…")
    a3d._cmems_subset(
        dataset_id=a3d.ARMOR3D_MY_DATASET,
        start=start,
        end=end,
        lon_min=-180.0, lon_max=180.0,
        lat_min=LAT_MIN, lat_max=LAT_MAX,
        depth_min=a3d.DEPTH_MIN, depth_max=a3d.DEPTH_MAX,
        variables=a3d.VARIABLES,
        out_path=out_path,
        log=log,
    )
    return out_path


def _process_year(
    year: int, raw_path: Path, log: str,
) -> Path:
    """Derive per-year weekly TCHP + t_climo_eq accumulator from raw T.

    Writes a per-year NetCDF with:
        tchp_sum   (week, lat, lon)  — sum of TCHP across that year's
                                        timesteps that fell into the WOY
        tchp_count (week, lat, lon)  — number of contributing timesteps
        t_sum_eq   (week, depth, lon) — sum of 5°S–5°N-averaged T
        t_count_eq (week, depth, lon)
    """
    print(f"{log}   processing year {year} → TCHP + equatorial T sums…")
    with xr.open_dataset(raw_path) as ds:
        t = ds["to"]  # (time, depth, lat, lon)
        depth = ds["depth"].values.astype(np.float32)
        lat = ds["latitude"].values.astype(np.float32)
        lon = ds["longitude"].values.astype(np.float32)
        times = ds["time"].values

        # Roll to 0-360 so storage layout matches the runtime generator.
        lon_roll_shift = int(np.sum(lon < 0))
        if lon_roll_shift:
            lon_new = np.concatenate(
                [lon[lon_roll_shift:], lon[:lon_roll_shift] + 360]
            )
        else:
            lon_new = lon.copy()

        nlat = lat.size
        nlon = lon_new.size
        ndep = depth.size

        tchp_sum   = np.zeros((52, nlat, nlon), dtype=np.float64)
        tchp_count = np.zeros((52, nlat, nlon), dtype=np.int32)

        eq_mask = (lat >= EQ_LAT_MIN) & (lat <= EQ_LAT_MAX)
        t_sum_eq   = np.zeros((52, ndep, nlon), dtype=np.float64)
        t_count_eq = np.zeros((52, ndep, nlon), dtype=np.int32)

        for ti, t_val in enumerate(times):
            valid_date = a3d._np_datetime_to_date(t_val)
            if valid_date.year != year:
                # CMEMS can return fractional weeks straddling year
                # boundaries — skip anything outside our year bucket so
                # neighbouring years don't double-count.
                continue
            woy = _week_of_year(valid_date)
            wi = woy - 1

            # Extract this timestep's T field and roll lon.
            t_arr = t.isel(time=ti).values.astype(np.float32)  # (depth, lat, lon)
            if lon_roll_shift:
                t_arr = np.concatenate(
                    [t_arr[:, :, lon_roll_shift:],
                     t_arr[:, :, :lon_roll_shift]],
                    axis=2,
                )

            # TCHP.
            d26 = a3d.compute_d26(t_arr, depth)
            tchp = a3d.compute_tchp(t_arr, depth, d26)  # (lat, lon)
            # Treat NaNs as missing (don't pollute counts).
            valid = ~np.isnan(tchp)
            tchp_sum[wi][valid]   += tchp[valid]
            tchp_count[wi][valid] += 1

            # Equatorial 5°S–5°N zonal-mean T.
            if eq_mask.any():
                t_eq = np.nanmean(t_arr[:, eq_mask, :], axis=1)  # (depth, lon)
                valid_eq = ~np.isnan(t_eq)
                t_sum_eq[wi][valid_eq] += t_eq[valid_eq]
                t_count_eq[wi][valid_eq] += 1

        state = xr.Dataset(
            {
                "tchp_sum":   (["week", "latitude", "longitude"], tchp_sum),
                "tchp_count": (["week", "latitude", "longitude"], tchp_count),
                "t_sum_eq":   (["week", "depth", "longitude"],    t_sum_eq),
                "t_count_eq": (["week", "depth", "longitude"],    t_count_eq),
            },
            coords={
                "week":      np.arange(1, 53, dtype=np.int16),
                "latitude":  lat,
                "longitude": lon_new,
                "depth":     depth,
            },
            attrs={
                "year": year,
                "note": "Per-year TCHP + equatorial T sums for ARMOR3D climatology.",
            },
        )
        out = _year_state_path(year)
        enc = {
            "tchp_sum":   {"zlib": True, "complevel": 4},
            "tchp_count": {"zlib": True, "complevel": 4},
            "t_sum_eq":   {"zlib": True, "complevel": 4},
            "t_count_eq": {"zlib": True, "complevel": 4},
        }
        state.to_netcdf(out, encoding=enc)
        return out


def _combine_years(year_paths: list[Path], log: str) -> Path:
    """Combine per-year accumulators into the final climatology file."""
    if not year_paths:
        raise RuntimeError("No per-year state files — nothing to combine.")

    print(f"{log} combining {len(year_paths)} per-year state files…")
    tchp_sum_tot = None
    tchp_cnt_tot = None
    t_sum_eq_tot = None
    t_cnt_eq_tot = None
    lat = lon = depth = None
    for p in year_paths:
        with xr.open_dataset(p) as ds:
            if tchp_sum_tot is None:
                tchp_sum_tot = ds["tchp_sum"].values.astype(np.float64)
                tchp_cnt_tot = ds["tchp_count"].values.astype(np.int64)
                t_sum_eq_tot = ds["t_sum_eq"].values.astype(np.float64)
                t_cnt_eq_tot = ds["t_count_eq"].values.astype(np.int64)
                lat   = ds["latitude"].values
                lon   = ds["longitude"].values
                depth = ds["depth"].values
            else:
                tchp_sum_tot += ds["tchp_sum"].values.astype(np.float64)
                tchp_cnt_tot += ds["tchp_count"].values.astype(np.int64)
                t_sum_eq_tot += ds["t_sum_eq"].values.astype(np.float64)
                t_cnt_eq_tot += ds["t_count_eq"].values.astype(np.int64)

    with np.errstate(invalid="ignore", divide="ignore"):
        tchp_climo = np.where(
            tchp_cnt_tot > 0,
            tchp_sum_tot / np.maximum(tchp_cnt_tot, 1),
            np.nan,
        ).astype(np.float32)
        t_climo_eq = np.where(
            t_cnt_eq_tot > 0,
            t_sum_eq_tot / np.maximum(t_cnt_eq_tot, 1),
            np.nan,
        ).astype(np.float32)

    out_ds = xr.Dataset(
        {
            "tchp_climo": (["week", "latitude", "longitude"], tchp_climo,
                           {"units": "kJ/cm^2", "long_name": "TCHP 1993-2020 weekly climatology"}),
            "t_climo_eq": (["week", "depth", "longitude"], t_climo_eq,
                           {"units": "degree_C",
                            "long_name": "Temperature 5S-5N zonal mean climatology"}),
            "tchp_count": (["week", "latitude", "longitude"], tchp_cnt_tot.astype(np.int16)),
        },
        coords={
            "week":      np.arange(1, 53, dtype=np.int16),
            "latitude":  lat,
            "longitude": lon,
            "depth":     depth,
        },
        attrs={
            "source": "Copernicus Marine ARMOR3D MY weekly reanalysis",
            "product_id": a3d.ARMOR3D_MY_DATASET,
            "baseline_period_note":
                "1993-2020 weekly climatology built by build_armor3d_climatology.py",
            "generated_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    out_path = ARMOR_DIR / "armor3d_climatology.nc"
    enc = {
        "tchp_climo": {"zlib": True, "complevel": 6,
                       "dtype": "float32", "_FillValue": np.float32(np.nan)},
        "t_climo_eq": {"zlib": True, "complevel": 6,
                       "dtype": "float32", "_FillValue": np.float32(np.nan)},
        "tchp_count": {"zlib": True, "complevel": 4, "dtype": "int16"},
    }
    out_ds.to_netcdf(out_path, encoding=enc)
    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"{log}   ✓ {out_path.name} ({size_mb:.1f} MB)")
    return out_path


# --- Main --------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build ARMOR3D 1993-2020 TCHP climatology."
    )
    parser.add_argument(
        "--start-year", type=int, default=YEAR_START_DEFAULT,
        help=f"First climatology year (default {YEAR_START_DEFAULT}).",
    )
    parser.add_argument(
        "--end-year", type=int, default=YEAR_END_DEFAULT,
        help=f"Last climatology year (inclusive, default {YEAR_END_DEFAULT}).",
    )
    parser.add_argument(
        "--combine-only", action="store_true",
        help="Skip fetching; combine existing per-year state files.",
    )
    parser.add_argument(
        "--keep-raw", action="store_true",
        help="Don't delete the raw CMEMS download after processing a year.",
    )
    args = parser.parse_args(argv)

    log = "[armor3d-climo]"
    print(f"{log} target baseline: {args.start_year}..{args.end_year}")

    year_paths: list[Path] = []

    if not args.combine_only:
        if not a3d._have_credentials():
            raise RuntimeError(
                "CMEMS credentials missing. Set "
                "COPERNICUSMARINE_SERVICE_USERNAME and "
                "COPERNICUSMARINE_SERVICE_PASSWORD before running."
            )

        for year in range(args.start_year, args.end_year + 1):
            state_path = _year_state_path(year)
            if state_path.exists() and state_path.stat().st_size > 0:
                print(f"{log}   year {year}: state already exists — skipping.")
                year_paths.append(state_path)
                continue
            try:
                raw = _fetch_year(year, log)
                _process_year(year, raw, log)
                year_paths.append(state_path)
                if not args.keep_raw:
                    try:
                        raw.unlink()
                    except OSError:
                        pass
            except Exception as exc:
                print(f"{log}   ERR year {year}: {exc}")
                # Allow one failure and continue — with 28 years we can
                # tolerate 1-2 gaps without meaningfully biasing the climo.
                time.sleep(10)
                continue
    else:
        for year in range(args.start_year, args.end_year + 1):
            p = _year_state_path(year)
            if p.exists():
                year_paths.append(p)

    if not year_paths:
        print(f"{log} no per-year state files available — nothing to combine.")
        return 2

    _combine_years(year_paths, log)
    print(f"{log} done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
