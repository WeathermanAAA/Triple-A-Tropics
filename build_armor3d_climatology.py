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


# --- Time budget ------------------------------------------------------
#
# CI runners (GitHub Actions) enforce a hard per-step timeout that kills
# the process mid-write — which in turn corrupts any `_raw_*.nc` file
# that xarray was still flushing to disk and leaves tar unable to read
# the .armor3d_climo_state/ directory for the cache-save step. To avoid
# that, we track a wall-clock deadline and exit *cleanly* once we're
# within the safety margin. This way the last week we fetched is
# complete on disk, the OS has time to sync buffers, and the Actions
# cache saves successfully so the next run can resume.
#
# Both values are set from main() based on the --time-budget-minutes
# flag. A None deadline means "no budget, run until finished".

_TIME_BUDGET_DEADLINE: float | None = None


def _budget_exhausted() -> bool:
    """Has the wall-clock deadline passed?"""
    return (_TIME_BUDGET_DEADLINE is not None
            and time.monotonic() >= _TIME_BUDGET_DEADLINE)


def _budget_remaining_s() -> float | None:
    if _TIME_BUDGET_DEADLINE is None:
        return None
    return max(0.0, _TIME_BUDGET_DEADLINE - time.monotonic())


# -- Helpers ------------------------------------------------------------


def _week_of_year(d: dt.date) -> int:
    """Map a date to a week-of-year in 1..52.

    We use the same convention as the weekly generator: DOY 1..7 → week 1,
    DOY 8..14 → week 2, ..., DOY 358..365/366 → week 52 (clipped)."""
    return max(1, min(52, (d.timetuple().tm_yday - 1) // 7 + 1))


def _doy_for_week(year: int, week: int) -> dt.date:
    """Representative sample date for a week bucket.

    We sample day-4 of each 7-day window (DOY 4, 11, 18, …) so the pull
    lands near the middle of the week rather than on an edge. ARMOR3D
    daily means vary slowly so any single day inside the window is
    representative; center-of-window avoids accidental overlap with
    adjacent week buckets when a year edge straddles a weekend."""
    doy = (week - 1) * 7 + 4
    return dt.date(year, 1, 1) + dt.timedelta(days=doy - 1)


def _year_state_path(year: int) -> Path:
    return STATE_DIR / f"year_{year}.nc"


def _raw_week_path(year: int, week: int) -> Path:
    """Cache path for one per-week raw CMEMS download."""
    return STATE_DIR / f"_raw_{year}_w{week:02d}.nc"


def _purge_partial_raw_files(log: str) -> None:
    """Remove any `_raw_*.nc` file that can't be opened by xarray.

    Runs at script startup. If a prior run was force-killed mid-
    download, the partial NetCDF file on disk will look size-nonzero
    but fail to open. Leaving it around would make _fetch_year skip
    that week (it only checks existence + size > 0) and then
    _process_year would crash trying to read it. We'd rather detect
    and re-fetch it now."""
    partial = 0
    for p in sorted(STATE_DIR.glob("_raw_*.nc")):
        try:
            with xr.open_dataset(p) as _:
                pass
        except Exception:
            print(f"{log}   cleanup: removing partial {p.name}")
            try:
                p.unlink()
                partial += 1
            except OSError:
                pass
    if partial:
        print(f"{log}   cleanup: removed {partial} partial raw file(s).")


def _fetch_year(
    year: int, log: str,
) -> list[Path]:
    """Download 52 per-week ARMOR3D MY samples for one calendar year.

    Subsampled from the daily (P1D-m) product — one day per week bucket —
    because CMEMS dropped its P1W aggregate during the 2025 catalog
    migration. Pulling only 52 timesteps per year (vs 365) cuts download
    volume ~7× and shortens each year's wall time enough to fit the full
    28-year build inside GitHub's 6-hour job ceiling.

    Each per-week file is cached under `.armor3d_climo_state/` so if the
    runner is canceled mid-year, re-running picks up at the exact week
    we stopped at — not at the year boundary.

    Returns the list of raw per-week paths actually on disk (some may be
    skipped on CMEMS errors; the caller tolerates partial years)."""
    paths: list[Path] = []
    for week in range(1, 53):
        out = _raw_week_path(year, week)
        if out.exists() and out.stat().st_size > 0:
            paths.append(out)
            continue

        # Honor the CI time budget: if we're past the deadline, stop
        # *before* starting another weekly download. Returning here
        # leaves the disk quiescent and lets the cache-save step
        # archive `.armor3d_climo_state/` cleanly. The weeks we already
        # finished are on disk and will be picked up on the next run.
        if _budget_exhausted():
            print(f"{log}   TIME BUDGET EXHAUSTED at {year} week {week:02d} — "
                  f"stopping cleanly so cache save can succeed.")
            return paths

        sample = _doy_for_week(year, week)
        # Week-52 DOY 4 = DOY 361; safely inside every calendar year, so
        # no overflow guard needed. Leap years still map DOY 1..366 → 52
        # buckets via _week_of_year's clip.
        start = dt.datetime.combine(sample, dt.time.min)
        end   = dt.datetime.combine(sample, dt.time.max)
        remaining = _budget_remaining_s()
        suffix = f" (budget {remaining/60:.1f} min left)" if remaining is not None else ""
        print(f"{log}   {year} week {week:02d} ({sample.isoformat()}){suffix}…")
        try:
            a3d._cmems_subset(
                dataset_id=a3d.ARMOR3D_MY_DATASET,
                start=start, end=end,
                lon_min=-180.0, lon_max=180.0,
                lat_min=LAT_MIN, lat_max=LAT_MAX,
                depth_min=a3d.DEPTH_MIN, depth_max=a3d.DEPTH_MAX,
                variables=a3d.VARIABLES,
                out_path=out,
                log=log,
            )
            paths.append(out)
        except Exception as exc:
            # A few missing weeks per year are tolerable — the climo
            # averages across 28 years, so one missing week/year just
            # reduces that WOY bucket's sample count by ~3%.
            print(f"{log}   WARN {year} week {week:02d} failed: {exc}")
            # If the week file partially wrote before the exception,
            # remove it so a retry (this run or next) sees a clean slate.
            if out.exists() and out.stat().st_size == 0:
                try:
                    out.unlink()
                except OSError:
                    pass
            continue
    return paths


def _process_year(
    year: int, raw_paths: list[Path], log: str,
) -> Path:
    """Derive per-year weekly TCHP + t_climo_eq accumulator from raw T.

    Iterates over the list of per-week raw NetCDFs produced by
    _fetch_year. Accumulates the same TCHP + equatorial-T sums the
    old single-file version produced. Using per-week files instead of
    one big year-long file keeps peak memory low (~220 MB/week vs ~30
    GB for a full year) and lets us resume cleanly if the job is
    killed mid-year.

    Writes a per-year NetCDF with:
        tchp_sum   (week, lat, lon)  — sum of TCHP across that year's
                                        timesteps that fell into the WOY
        tchp_count (week, lat, lon)  — number of contributing timesteps
        t_sum_eq   (week, depth, lon) — sum of 5°S–5°N-averaged T
        t_count_eq (week, depth, lon)
    """
    if not raw_paths:
        raise RuntimeError(f"year {year}: no per-week raw files to process")

    print(f"{log}   processing year {year} ({len(raw_paths)} weekly files) "
          f"→ TCHP + equatorial T sums…")

    # Grid metadata (lat / lon / depth) is lazy-initialized from the first
    # raw file; every subsequent week is assumed to sit on the same
    # ARMOR3D grid (CMEMS has held that grid constant since ~2010 across
    # all versions we care about here).
    tchp_sum = tchp_count = None
    t_sum_eq = t_count_eq = None
    lat_arr = lon_new = depth_arr = eq_mask = None
    lon_roll_shift = 0

    for raw_path in raw_paths:
        with xr.open_dataset(raw_path) as ds:
            t = ds["to"]  # (time, depth, lat, lon)

            if tchp_sum is None:
                depth_arr = ds["depth"].values.astype(np.float32)
                lat_arr   = ds["latitude"].values.astype(np.float32)
                lon       = ds["longitude"].values.astype(np.float32)

                # Roll to 0-360 so storage layout matches the runtime
                # generator.
                lon_roll_shift = int(np.sum(lon < 0))
                if lon_roll_shift:
                    lon_new = np.concatenate(
                        [lon[lon_roll_shift:], lon[:lon_roll_shift] + 360]
                    )
                else:
                    lon_new = lon.copy()

                nlat = lat_arr.size
                nlon = lon_new.size
                ndep = depth_arr.size

                tchp_sum   = np.zeros((52, nlat, nlon), dtype=np.float64)
                tchp_count = np.zeros((52, nlat, nlon), dtype=np.int32)
                eq_mask    = (lat_arr >= EQ_LAT_MIN) & (lat_arr <= EQ_LAT_MAX)
                t_sum_eq   = np.zeros((52, ndep, nlon), dtype=np.float64)
                t_count_eq = np.zeros((52, ndep, nlon), dtype=np.int32)

            times = ds["time"].values
            for ti, t_val in enumerate(times):
                valid_date = a3d._np_datetime_to_date(t_val)
                if valid_date.year != year:
                    # CMEMS occasionally returns the prior/following day
                    # when a bucket straddles midnight UTC; drop anything
                    # outside the year we're accumulating.
                    continue
                woy = _week_of_year(valid_date)
                wi = woy - 1

                # Extract this timestep's T field and roll lon.
                t_arr = t.isel(time=ti).values.astype(np.float32)
                if lon_roll_shift:
                    t_arr = np.concatenate(
                        [t_arr[:, :, lon_roll_shift:],
                         t_arr[:, :, :lon_roll_shift]],
                        axis=2,
                    )

                # TCHP.
                d26  = a3d.compute_d26(t_arr, depth_arr)
                tchp = a3d.compute_tchp(t_arr, depth_arr, d26)  # (lat, lon)
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

    if tchp_sum is None:
        # Should only happen if every raw_path was empty (never observed
        # in practice; defensive).
        raise RuntimeError(f"year {year}: processed zero timesteps")

    state = xr.Dataset(
        {
            "tchp_sum":   (["week", "latitude", "longitude"], tchp_sum),
            "tchp_count": (["week", "latitude", "longitude"], tchp_count),
            "t_sum_eq":   (["week", "depth", "longitude"],    t_sum_eq),
            "t_count_eq": (["week", "depth", "longitude"],    t_count_eq),
        },
        coords={
            "week":      np.arange(1, 53, dtype=np.int16),
            "latitude":  lat_arr,
            "longitude": lon_new,
            "depth":     depth_arr,
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

    # Final climatology written for the site consumer. `tchp_count` is
    # intentionally omitted here — it's a build-time diagnostic used only
    # during `_combine_years` aggregation of per-year state files, and
    # generate_armor3d_plots.py only reads `tchp_climo` and `t_climo_eq`.
    # Keeping it would push the file past GitHub's 100 MB per-file limit.
    out_ds = xr.Dataset(
        {
            "tchp_climo": (["week", "latitude", "longitude"], tchp_climo,
                           {"units": "kJ/cm^2", "long_name": "TCHP 1993-2020 weekly climatology"}),
            "t_climo_eq": (["week", "depth", "longitude"], t_climo_eq,
                           {"units": "degree_C",
                            "long_name": "Temperature 5S-5N zonal mean climatology"}),
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
    # complevel=9 is max zlib — slower to write but we only write this
    # file a handful of times ever, so the extra CPU cost is free.
    enc = {
        "tchp_climo": {"zlib": True, "complevel": 9,
                       "dtype": "int16",
                       "scale_factor": np.float32(0.004),
                       "add_offset":   np.float32(100.0),
                       "_FillValue":   np.int16(-32768)},
        "t_climo_eq": {"zlib": True, "complevel": 9,
                       "dtype": "int16",
                       "scale_factor": np.float32(0.001),
                       "add_offset":   np.float32(15.0),
                       "_FillValue":   np.int16(-32768)},
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
    parser.add_argument(
        "--time-budget-minutes", type=float, default=None,
        help=(
            "Wall-clock budget in minutes. When elapsed, the script stops "
            "cleanly between weekly downloads (so the last week on disk is "
            "complete) and exits 0. Used in CI to finish before GitHub's "
            "hard step timeout kills the process mid-write. Default: no "
            "budget (run until all years are done)."
        ),
    )
    args = parser.parse_args(argv)

    log = "[armor3d-climo]"
    print(f"{log} target baseline: {args.start_year}..{args.end_year}")

    # Arm the time budget. `time.monotonic()` + `time.sleep` are the only
    # two clock calls we make after this; both are monotonic-safe so a
    # mid-run NTP correction can't shift the deadline.
    global _TIME_BUDGET_DEADLINE
    if args.time_budget_minutes is not None:
        _TIME_BUDGET_DEADLINE = time.monotonic() + args.time_budget_minutes * 60.0
        print(f"{log} time budget: {args.time_budget_minutes:.1f} min — "
              f"script will exit cleanly at deadline.")

    # Defensive cleanup: a previous run may have left a partial
    # `_raw_YYYY_wNN.nc` on disk if the process was force-killed mid-
    # download. Any file that xarray can't open is deleted now so the
    # fetch loop re-downloads it cleanly.
    _purge_partial_raw_files(log)

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

            # Check the budget at the year boundary too — if we're out
            # of time we want to stop BEFORE starting a new year's
            # worth of fetches (otherwise we'd partly fetch a year and
            # then have to discard since year-state is only written
            # when the full year's worth of weeks is processed).
            if _budget_exhausted():
                print(f"{log}   TIME BUDGET EXHAUSTED at year boundary ({year}) "
                      f"— stopping so cache save can succeed.")
                break

            try:
                raw_paths = _fetch_year(year, log)

                # If the budget ran out mid-year, `_fetch_year` returns
                # only the weeks actually on disk so far. We don't
                # process a partial year — just persist the per-week
                # raws and let the next run finish the year.
                if _budget_exhausted() and len(raw_paths) < 52:
                    print(f"{log}   year {year}: partial ({len(raw_paths)}/52 "
                          f"weeks) — not processing, will resume next run.")
                    break

                if not raw_paths:
                    print(f"{log}   year {year}: no weekly samples pulled — skipping.")
                    continue
                _process_year(year, raw_paths, log)
                year_paths.append(state_path)
                # Delete the per-week raw cache files once the year's
                # accumulator is safely on disk. Keeps the Actions cache
                # from growing unboundedly (each raw week ~150 MB ×
                # 52 × 28 years would otherwise be >200 GB).
                if not args.keep_raw:
                    for rp in raw_paths:
                        try:
                            rp.unlink()
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
