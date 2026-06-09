#!/usr/bin/env python3
"""
Triple-A-Tropics · ARMOR3D per-pixel per-week-of-year TCHP record envelope bake
===============================================================================

RUN ENVIRONMENT
---------------
This runs on the RENDER BOX (the machine that already has Copernicus Marine
credentials and produces the /sst/ ARMOR3D anomaly), NOT in GitHub Actions —
same as build_armor3d_climatology.py / bake_crw_doy_records.py. It does a single
resumable pass over the ARMOR3D MULTI-YEAR REANALYSIS (1993 → present) and, per
pixel and per WEEK-OF-YEAR (52 buckets, the same day-4 weekly sampling the
climatology uses), records the all-archive MAX and MIN TCHP plus the YEAR each
extreme was set.

WHY A SEPARATE BAKE (the climatology mean is NOT enough)
-------------------------------------------------------
armor3d_climatology.nc holds only `tchp_climo` — the 1993-2020 weekly MEAN. A
record needs the MAX/MIN ENVELOPE across the archive, which the mean cannot
give. This bake computes that envelope directly from the reanalysis.

WHAT IT COMPUTES (per WOY week 1..52, per pixel)
------------------------------------------------
    tchp_week_max  float  — highest day-4-weekly TCHP ever seen this WOY
    tchp_week_min  float  — lowest  "        "        "
    max_year       int16  — year the standing MAX was set (record margin/age)
    min_year       int16  — year the standing MIN was set
Coverage POR: 1993 → last complete reanalysis year/week. Ties keep the EARLIER
year (the year the record was first set).

RESUME / MEMORY
---------------
One per-YEAR state file (this year's day-4 weekly TCHP field) is written under
.armor3d_records_state/ after the year completes; the per-week CMEMS raw caches
under the same dir let a killed run resume mid-year (reused from the climatology
builder's fetch). `--finalize` combines all per-year states with np.fmax / np.fmin
+ year stamping (the pure `update_minmax`, unit-tested in tests/), then writes the
int16 NetCDF + manifest. Peak RAM = one year in flight (~52 × grid).

OUTPUT (after --finalize)
-------------------------
    sst/records/armor3d_tchp_record_1993_present.nc
        tchp_week_max int16  (scale 0.004, offset 100.0, fill -32768)  [week,lat,lon]
        tchp_week_min int16  (same packing)
        max_year      int16  (fill -32768)
        min_year      int16  (fill -32768)
    sst/records/manifest.json   (the consumer's gate: POR, generated_utc, grid)
R2: s3://triple-a-tropics-media/sst/records/...  →  cdn.triple-a-tropics.com/sst/records/

USAGE (on the box)
------------------
    python bake_armor3d_tchp_records.py --run                 # resumable pass
    python bake_armor3d_tchp_records.py --run --time-budget-minutes 320
    python bake_armor3d_tchp_records.py --finalize --upload    # combine -> NetCDF + R2

ZERO touch to ACE / track / climo. Reuses generate_armor3d_plots (CMEMS fetch +
compute_tchp) and build_armor3d_climatology (week helpers) so the records and the
climatology are guaranteed to share grid + sampling.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

import numpy as np
import xarray as xr

import generate_armor3d_plots as a3d
import build_armor3d_climatology as climo
from build_armor3d_climatology import (
    _week_of_year, LAT_MIN, LAT_MAX,
)

HERE = Path(__file__).resolve().parent
STATE_DIR = HERE / ".armor3d_records_state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR = HERE / "sst" / "records"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_NC = OUT_DIR / "armor3d_tchp_record_1993_present.nc"
MANIFEST = OUT_DIR / "manifest.json"
R2_PREFIX = "sst/records"
MARKER_PATH = STATE_DIR / "last_year_completed.txt"
LOG = "[armor3d-tchp-record]"

YEAR_START = 1993
NO_YEAR = np.int16(-32768)
# reanalysis latency: the MY product trails real time; the last fully-available
# year is conservatively "this year - 1" unless --end-year says otherwise.
REANALYSIS_LATENCY_YEARS = 1


# --- Pure accumulator (unit-tested in tests/test_armor3d_records_bake.py) ----

def update_minmax(max_acc, max_year, min_acc, min_year, obs, year):
    """In-place fold of `obs` (this year's week field) into the running
    per-week MAX and MIN envelopes, stamping `year` wherever it sets a new
    extreme. NaN in `obs` = missing (land/no-data); np.fmax / np.fmin ignore
    NaN so those pixels keep their standing value. Ties keep the EARLIER year.

    "wins the max" = obs finite AND NOT (obs <= max_acc)   (handles -inf/NaN seed)
    "wins the min" = obs finite AND NOT (obs >= min_acc)
    """
    with np.errstate(invalid="ignore"):
        hi_wins = np.isfinite(obs) & ~(obs <= max_acc)
        lo_wins = np.isfinite(obs) & ~(obs >= min_acc)
    max_year[hi_wins] = np.int16(year)
    min_year[lo_wins] = np.int16(year)
    np.fmax(max_acc, obs, out=max_acc)
    np.fmin(min_acc, obs, out=min_acc)


# --- marker / resume ---------------------------------------------------------

def _read_marker() -> int | None:
    if not MARKER_PATH.exists():
        return None
    t = MARKER_PATH.read_text().strip()
    return int(t) if t else None


def _write_marker(year: int) -> None:
    tmp = MARKER_PATH.with_suffix(".tmp")
    tmp.write_text(str(year))
    tmp.replace(MARKER_PATH)


def _year_state_path(year: int) -> Path:
    return STATE_DIR / f"recyear_{year}.nc"


# --- per-year fetch + derive (reuses a3d CMEMS + compute_tchp) ---------------

def _raw_week_path(year: int, week: int) -> Path:
    return STATE_DIR / f"_raw_{year}_w{week:02d}.nc"


def _fetch_year(year: int, deadline: float | None) -> list[Path]:
    """Download 52 day-4 weekly ARMOR3D MY samples for `year` (resumable;
    per-week raw cached). Mirrors build_armor3d_climatology._fetch_year but
    uses this bake's STATE_DIR. Honors a wall-clock deadline."""
    paths: list[Path] = []
    for week in range(1, 53):
        out = _raw_week_path(year, week)
        if out.exists() and out.stat().st_size > 0:
            paths.append(out)
            continue
        if deadline is not None and time.monotonic() >= deadline:
            print(f"{LOG}   TIME BUDGET reached at {year} w{week:02d} — stopping clean.")
            return paths
        doy = (week - 1) * 7 + 4
        sample = dt.date(year, 1, 1) + dt.timedelta(days=doy - 1)
        start = dt.datetime.combine(sample, dt.time.min)
        end = dt.datetime.combine(sample, dt.time.max)
        print(f"{LOG}   {year} w{week:02d} ({sample.isoformat()})…")
        try:
            a3d._cmems_subset(
                dataset_id=a3d.ARMOR3D_MY_DATASET, start=start, end=end,
                lon_min=-180.0, lon_max=180.0, lat_min=LAT_MIN, lat_max=LAT_MAX,
                depth_min=a3d.DEPTH_MIN, depth_max=a3d.DEPTH_MAX,
                variables=a3d.VARIABLES, out_path=out, log=LOG,
            )
            paths.append(out)
        except Exception as exc:
            print(f"{LOG}   WARN {year} w{week:02d} failed: {exc}")
            if out.exists() and out.stat().st_size == 0:
                try:
                    out.unlink()
                except OSError:
                    pass
    return paths


def _process_year(year: int, raw_paths: list[Path]):
    """Derive this year's per-week TCHP field (week, lat, lon) from the raw
    per-week temperature files — the same load + lon-roll + compute_tchp the
    climatology builder uses, so grid + sampling match exactly. A week with no
    valid sample stays NaN."""
    tchp_year = None
    lat_arr = lon_new = None
    for p in raw_paths:
        with xr.open_dataset(p) as ds:
            t = ds["to"] if "to" in ds else ds[list(ds.data_vars)[0]]
            depth_arr = ds["depth"].values.astype(np.float32)
            if tchp_year is None:
                lat_arr = ds["latitude"].values.astype(np.float32)
                lon = ds["longitude"].values.astype(np.float32)
                roll = int(np.sum(lon < 0))
                lon_new = (np.concatenate([lon[roll:], lon[:roll] + 360])
                           if roll else lon.copy())
                tchp_year = np.full((52, lat_arr.size, lon_new.size),
                                    np.nan, dtype=np.float32)
            else:
                lon = ds["longitude"].values.astype(np.float32)
                roll = int(np.sum(lon < 0))
            for ti, t_val in enumerate(ds["time"].values):
                d = a3d._np_datetime_to_date(t_val)
                if d.year != year:
                    continue
                wi = _week_of_year(d) - 1
                t_arr = t.isel(time=ti).values.astype(np.float32)
                if roll:
                    t_arr = np.concatenate(
                        [t_arr[:, :, roll:], t_arr[:, :, :roll]], axis=2)
                d26 = a3d.compute_d26(t_arr, depth_arr)
                tchp = a3d.compute_tchp(t_arr, depth_arr, d26)   # (lat, lon)
                # one day-4 sample per week-bucket; last write wins (rare dup)
                tchp_year[wi] = np.where(np.isnan(tchp), tchp_year[wi], tchp)
    if tchp_year is None:
        raise RuntimeError(f"year {year}: processed zero timesteps")
    out = _year_state_path(year)
    xr.Dataset(
        {"tchp_week": (["week", "latitude", "longitude"], tchp_year)},
        coords={"week": np.arange(1, 53, dtype=np.int16),
                "latitude": lat_arr, "longitude": lon_new},
        attrs={"year": year},
    ).to_netcdf(out, encoding={"tchp_week": {"zlib": True, "complevel": 4}})
    return out


# --- driver ------------------------------------------------------------------

def run_pass(start_year, end_year, deadline):
    today = dt.date.today()
    hi = end_year or (today.year - REANALYSIS_LATENCY_YEARS)
    lo = start_year or YEAR_START
    marker = _read_marker()
    if marker is not None and marker >= lo:
        lo = marker + 1
        print(f"{LOG} resuming after completed year {marker} → start {lo}")
    if lo > hi:
        print(f"{LOG} nothing to do: {lo} > {hi} (archive head).")
        return 0
    print(f"{LOG} single pass {lo}..{hi}")
    done = 0
    for year in range(lo, hi + 1):
        if deadline is not None and time.monotonic() >= deadline:
            print(f"{LOG} TIME BUDGET reached before year {year} — stopping clean.")
            break
        raws = _fetch_year(year, deadline)
        if deadline is not None and time.monotonic() >= deadline:
            print(f"{LOG} budget reached mid-fetch {year}; not finalizing the year.")
            break
        if not raws:
            print(f"{LOG} year {year}: no raw weeks; skipping.")
            continue
        _process_year(year, raws)
        _write_marker(year)
        done += 1
        print(f"{LOG} year {year} done.")
    return done


def finalize():
    states = sorted(STATE_DIR.glob("recyear_*.nc"))
    if not states:
        raise RuntimeError("no per-year state files; run --run first.")
    print(f"{LOG} combining {len(states)} years…")
    rmax = rmin = ymax = ymin = lat = lon = None
    years = []
    for p in states:
        with xr.open_dataset(p) as ds:
            tw = ds["tchp_week"].values.astype(np.float32)
            yr = int(ds.attrs["year"])
            years.append(yr)
            if rmax is None:
                shape = tw.shape
                rmax = np.full(shape, -np.inf, dtype=np.float32)
                rmin = np.full(shape, np.inf, dtype=np.float32)
                ymax = np.full(shape, NO_YEAR, dtype=np.int16)
                ymin = np.full(shape, NO_YEAR, dtype=np.int16)
                lat = ds["latitude"].values
                lon = ds["longitude"].values
            update_minmax(rmax, ymax, rmin, ymin, tw, yr)
    # cells never observed (all-NaN) → fill
    never = ~np.isfinite(rmax)
    rmax[never] = np.nan
    rmin[~np.isfinite(rmin)] = np.nan
    pack = {"zlib": True, "complevel": 9, "dtype": "int16",
            "scale_factor": np.float32(0.004), "add_offset": np.float32(100.0),
            "_FillValue": np.int16(-32768)}
    ypack = {"zlib": True, "complevel": 9, "_FillValue": np.int16(-32768)}
    xr.Dataset(
        {"tchp_week_max": (["week", "latitude", "longitude"], rmax,
                           {"units": "kJ/cm^2", "long_name": "per-WOY record-high TCHP"}),
         "tchp_week_min": (["week", "latitude", "longitude"], rmin,
                           {"units": "kJ/cm^2", "long_name": "per-WOY record-low TCHP"}),
         "max_year": (["week", "latitude", "longitude"], ymax),
         "min_year": (["week", "latitude", "longitude"], ymin)},
        coords={"week": np.arange(1, 53, dtype=np.int16),
                "latitude": lat, "longitude": lon},
        attrs={"source": "Copernicus Marine ARMOR3D MY weekly reanalysis",
               "product_id": a3d.ARMOR3D_MY_DATASET,
               "por_note": ("record within the ARMOR3D reanalysis archive "
                            f"({min(years)}-{max(years)}), not all-time"),
               "method": "per-pixel per-week-of-year (day-4 weekly sampling) MAX/MIN",
               "generated_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")},
    ).to_netcdf(OUT_NC, encoding={"tchp_week_max": pack, "tchp_week_min": pack,
                                  "max_year": ypack, "min_year": ypack})
    MANIFEST.write_text(json.dumps({
        "product": "armor3d_tchp_record",
        "file": OUT_NC.name,
        "por_start": min(years), "por_end": max(years),
        "weeks": 52, "grid_deg": 0.125,
        "source": "Copernicus Marine ARMOR3D MY reanalysis (MULTIOBS_GLO_PHY_TSUV_3D)",
        "honest_line": ("record within the ARMOR3D reanalysis archive "
                        f"(since {min(years)}), not all-time"),
        "generated_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, indent=2))
    print(f"{LOG} wrote {OUT_NC} ({OUT_NC.stat().st_size/1e6:.1f} MB) + manifest "
          f"(POR {min(years)}-{max(years)}).")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--finalize", action="store_true")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--start-year", type=int, default=None)
    ap.add_argument("--end-year", type=int, default=None)
    ap.add_argument("--time-budget-minutes", type=float, default=None)
    a = ap.parse_args()
    deadline = (time.monotonic() + a.time_budget_minutes * 60
                if a.time_budget_minutes else None)
    if a.run:
        if not a3d._have_credentials():
            raise SystemExit(f"{LOG} no Copernicus Marine credentials — run on the box.")
        run_pass(a.start_year, a.end_year, deadline)
    if a.finalize:
        finalize()
    if a.upload:
        from build_crw_doy_climatology import upload_to_r2
        upload_to_r2(OUT_NC, f"{R2_PREFIX}/{OUT_NC.name}")
        upload_to_r2(MANIFEST, f"{R2_PREFIX}/{MANIFEST.name}")
        print(f"{LOG} uploaded to R2 {R2_PREFIX}/.")
    if not (a.run or a.finalize or a.upload):
        ap.print_help()


if __name__ == "__main__":
    main()
