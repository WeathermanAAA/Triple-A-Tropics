#!/usr/bin/env python3
"""
Triple-A-Tropics · CRW day-of-year per-pixel daily-record MAX SST bake
======================================================================

RUN ENVIRONMENT
---------------
This script runs on a BEEFY RENDER BOX, NOT in GitHub Actions. It does a
SINGLE pass over the whole CoralTemp v3.1 archive (1985-01-01 .. today-2),
updating 366 per-DOY accumulators that live ON DISK as float32 memmaps so
peak RAM stays bounded (one belt grid in flight, ~50 MB) while the full
36 GB of accumulator state sits on disk.

Estimated cost (single pass):
  * Transfer:   ~15,100 daily files × ~10 MB  ≈ 150 GB
  * Wall clock: ~2-4 h on a 300 Mbps link (download-bound, not CPU-bound)
  * State dir:  366 DOYs × 2 grids (max + record_year) × ~50 MB float32
                ≈ 36 GB on disk. int16-state alternative (pack the max
                accumulator to int16 in-place) would halve the max-grid
                state to ~9 GB but loses sub-0.01 °C precision during
                accumulation — NOT recommended unless the box is disk-tight;
                the final published grids are int16 either way.

WHAT IT COMPUTES
----------------
For each DOY (same index contract as build_crw_doy_climatology.py:
index = date(2000, month, day).tm_yday, 1..366, leap-keyed) and each belt
pixel (lat -40..50, native CRW 0.05° grid, native -180..180 lon), the MAX
SST ever observed on that calendar day across 1985 .. last-complete day —
inclusive of the current year's elapsed days. A parallel int16 `record_year`
grid records the year the standing max was set (enables record margin/age
rendering later); ON by default, disable with --no-record-year if I/O is
tight.

WHY SINGLE-PASS WITH ON-DISK ACCUMULATORS
-----------------------------------------
Holding all 366 belt accumulators in RAM is 366 × 50 MB ≈ 18 GB (×2 with
record_year ≈ 36 GB) — too big. Sharding the DOY set and re-passing the
archive once per shard would multiply the 150 GB download by the shard
count (4 shards → 600 GB). Instead: ONE pass over the archive in date
order; for each day-file we update exactly ONE DOY accumulator with a
read-modify-write of a single ~50 MB memmap slab. np.fmax is idempotent,
so re-applying a partially-processed date on resume is harmless.

CHECKPOINTING / RESUME
----------------------
A `last_date_completed` marker file is written after each day-file is
applied. On start we resume from marker+1. Re-running an already-applied
date is safe (fmax idempotent), so the marker is advanced AFTER the write,
never losing data on a crash.

OUTPUT (after the full pass, via --finalize)
--------------------------------------------
    crw_doy_record_1985_present_{DDD:03d}.nc
        sst_record_max  int16  (scale 0.01, offset 0.0, fill -32768)
        record_year     int16  (fill -32768 where no obs)
        belt/grid/attrs contract identical to deliverable 1 + archive_span
    sst/records/manifest.json   (only at completion; the consumer's gate)

R2 layout
---------
    s3://triple-a-tropics-media/sst/records/crw_doy_record_1985_present_{DDD:03d}.nc
    s3://triple-a-tropics-media/sst/records/manifest.json
served at https://cdn.triple-a-tropics.com/sst/records/...

USAGE
-----
    # Full single pass over the archive (resumable; run on the render box):
    python bake_crw_doy_records.py --run

    # Resume after an interruption — picks up from the marker automatically:
    python bake_crw_doy_records.py --run

    # Optional: cap the pass for a test slice
    python bake_crw_doy_records.py --run --start-date 1985-01-01 \\
        --end-date 1985-03-31

    # Assemble per-DOY NetCDFs from the accumulators + write manifest:
    python bake_crw_doy_records.py --finalize --upload

Shared plumbing (CRW fetch/read, belt subset, int16 pack, DOY contract) is
imported from build_crw_doy_climatology.py so the two bakes are guaranteed
to share the same grid, lon convention, and quantization.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

import generate_sst_plots as gsp
import build_crw_doy_climatology as climo
from build_crw_doy_climatology import (
    BELT_LAT_MIN, BELT_LAT_MAX, SCALE_FACTOR, ADD_OFFSET, FILL_VALUE,
    belt_indices, pack_int16, unpack_int16, doy_index, month_day_for_doy,
    _read_crw_native, upload_to_r2,
)

HERE = Path(__file__).resolve().parent

STATE_DIR = HERE / "_crw_doy_record_state"
STATE_DIR.mkdir(exist_ok=True)

ARCHIVE_START = dt.date(1985, 1, 1)   # CoralTemp v3.1 archive start
ARCHIVE_LATENCY_DAYS = 2              # today-2 is the last reliably-final day

R2_PREFIX = "sst/records"
FILE_PATTERN = "crw_doy_record_1985_present_{doy:03d}.nc"
MARKER_PATH = STATE_DIR / "last_date_completed.txt"
GRID_META_PATH = STATE_DIR / "grid_meta.npz"

LOG = "[crw-doy-record]"

NO_RECORD_YEAR = -32768  # int16 fill for the record_year grid


# --- Accumulator memmaps ------------------------------------------------


def _max_state_path(doy: int) -> Path:
    return STATE_DIR / f"max_{doy:03d}.npy"


def _year_state_path(doy: int) -> Path:
    return STATE_DIR / f"year_{doy:03d}.npy"


def _save_grid_meta(lat: np.ndarray, lon: np.ndarray) -> None:
    """Persist the belt lat/lon axes once so --finalize can write proper
    coordinates without re-fetching a CRW file."""
    np.savez(GRID_META_PATH, lat=lat.astype(np.float32),
             lon=lon.astype(np.float32))


def _load_grid_meta() -> tuple[np.ndarray, np.ndarray] | None:
    if not GRID_META_PATH.exists():
        return None
    z = np.load(GRID_META_PATH)
    return z["lat"], z["lon"]


def _open_or_init_max(doy: int, shape: tuple[int, int]) -> np.ndarray:
    """Open the DOY max accumulator as a writable memmap, creating it (filled
    with -inf so the first fmax always takes the observed value) if absent."""
    p = _max_state_path(doy)
    if not p.exists():
        arr = np.lib.format.open_memmap(p, mode="w+", dtype=np.float32,
                                        shape=shape)
        arr[:] = -np.inf
        return arr
    return np.lib.format.open_memmap(p, mode="r+")


def _open_or_init_year(doy: int, shape: tuple[int, int]) -> np.ndarray:
    """Open the DOY record-year accumulator (int16), creating it filled with
    NO_RECORD_YEAR if absent."""
    p = _year_state_path(doy)
    if not p.exists():
        arr = np.lib.format.open_memmap(p, mode="w+", dtype=np.int16,
                                        shape=shape)
        arr[:] = NO_RECORD_YEAR
        return arr
    return np.lib.format.open_memmap(p, mode="r+")


# --- Pure accumulator update (unit-tested) ------------------------------


def update_record(max_acc: np.ndarray, year_acc: np.ndarray | None,
                  obs: np.ndarray, year: int) -> None:
    """In-place fmax of `obs` into `max_acc`; wherever the fmax actually
    raises the standing value, stamp `year` into `year_acc`.

    Pure logic, no I/O — the offline unit test drives this on synthetic
    arrays. `obs` may contain NaN (land/no-data); np.fmax ignores NaN so
    those pixels keep their standing value.

    The "wins" mask must catch BOTH cases where fmax changes the value:
      * a finite obs strictly exceeding a finite standing max, AND
      * a finite obs landing on a non-finite standing value (-inf seed,
        or a NaN carried from a never-observed pixel) — np.fmax(NaN, x)=x,
        so that pixel's record IS being set this year and must be stamped.
    Equivalently: stamp where `obs` is finite AND NOT (obs <= max_acc).
    Ties (obs == standing max) keep the EARLIER year, matching "year the
    record was set"."""
    if year_acc is not None:
        with np.errstate(invalid="ignore"):
            wins = np.isfinite(obs) & ~(obs <= max_acc)
        year_acc[wins] = np.int16(year)
    np.fmax(max_acc, obs, out=max_acc)


# --- Marker / resume ----------------------------------------------------


def _read_marker() -> dt.date | None:
    if not MARKER_PATH.exists():
        return None
    txt = MARKER_PATH.read_text().strip()
    if not txt:
        return None
    return dt.date.fromisoformat(txt)


def _write_marker(d: dt.date) -> None:
    tmp = MARKER_PATH.with_suffix(".tmp")
    tmp.write_text(d.isoformat())
    tmp.replace(MARKER_PATH)


def _iter_dates(start: dt.date, end_inclusive: dt.date):
    d = start
    one = dt.timedelta(days=1)
    while d <= end_inclusive:
        yield d
        d += one


# --- Single-pass driver -------------------------------------------------


def run_pass(start_date: dt.date | None, end_date: dt.date | None,
             record_year: bool) -> int:
    """Single pass over the archive, date order, one memmap update per file.

    Resumes from the marker (marker+1). For each day: fetch the CoralTemp
    file, read it native + belt-subset, fmax into that day's DOY
    accumulator, advance the marker. fmax is idempotent so a re-applied
    date is harmless."""
    today = dt.datetime.utcnow().date()
    archive_end = today - dt.timedelta(days=ARCHIVE_LATENCY_DAYS)

    lo = start_date or ARCHIVE_START
    hi = end_date or archive_end
    if hi > archive_end:
        hi = archive_end

    marker = _read_marker()
    if marker is not None and marker >= lo:
        lo = marker + dt.timedelta(days=1)
        print(f"{LOG} resuming after marker {marker} → start {lo}")
    if lo > hi:
        print(f"{LOG} nothing to do: start {lo} > end {hi} "
              f"(already at archive head).")
        return 0

    print(f"{LOG} single pass {lo} .. {hi} "
          f"(record_year={'on' if record_year else 'off'})")

    grid_shape: tuple[int, int] | None = None
    applied = 0
    missing = 0
    t0 = time.monotonic()

    for d in _iter_dates(lo, hi):
        p = gsp.fetch_crw_day(d, "sst", LOG, verbose=False)
        if p is None:
            missing += 1
            # Still advance the marker so a permanent CRW gap doesn't stall
            # every future resume on the same missing day.
            _write_marker(d)
            continue
        try:
            g, lat, lon = _read_crw_native(p)
        except Exception as exc:  # noqa: BLE001
            print(f"{LOG}   read error {d}: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            missing += 1
            _write_marker(d)
            continue

        mask = belt_indices(lat)
        obs = g[mask, :]
        if grid_shape is None:
            grid_shape = obs.shape
            _save_grid_meta(lat[mask], lon)
        elif obs.shape != grid_shape:
            print(f"{LOG}   shape {obs.shape} != {grid_shape} for {d}, skip",
                  file=sys.stderr)
            missing += 1
            _write_marker(d)
            continue

        doy = doy_index(d.month, d.day)
        max_acc = _open_or_init_max(doy, grid_shape)
        year_acc = (_open_or_init_year(doy, grid_shape)
                    if record_year else None)
        update_record(max_acc, year_acc, obs, d.year)
        # Flush the memmap slab and free the year grid before the next file.
        max_acc.flush()
        if year_acc is not None:
            year_acc.flush()
        del max_acc, year_acc, g, obs

        _write_marker(d)
        applied += 1
        # Discard the downloaded NetCDF — 15k × 10 MB won't fit on disk.
        try:
            p.unlink()
        except OSError:
            pass

        if applied % 365 == 0:
            rate = applied / max(time.monotonic() - t0, 1e-6)
            print(f"{LOG}   {d}: {applied} applied "
                  f"({rate:.1f} files/s, {missing} missing so far)")

    dt_s = time.monotonic() - t0
    print(f"{LOG} pass done: {applied} applied, {missing} missing, "
          f"{dt_s/3600:.2f} h. Marker now {_read_marker()}.")
    return 0


# --- Finalize (assemble per-DOY NetCDFs + manifest) ---------------------


def _write_record_nc(doy: int, max_belt: np.ndarray, year_belt: np.ndarray | None,
                     lat: np.ndarray, lon: np.ndarray, out_path: Path) -> None:
    """Write one DOY's record grid(s) to a chunked, int16-packed NetCDF,
    matching deliverable 1's belt/grid/attrs contract + archive_span."""
    month, day = month_day_for_doy(doy)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    nlat, nlon = max_belt.shape
    chunk_lat = min(450, nlat)
    chunk_lon = min(900, nlon)
    with Dataset(tmp, "w", format="NETCDF4") as ds:
        ds.createDimension("lat", nlat)
        ds.createDimension("lon", nlon)
        vlat = ds.createVariable("lat", "f4", ("lat",), zlib=True, complevel=4)
        vlon = ds.createVariable("lon", "f4", ("lon",), zlib=True, complevel=4)
        vlat[:] = lat
        vlon[:] = lon
        vlat.units = "degrees_north"
        vlon.units = "degrees_east"

        v = ds.createVariable(
            "sst_record_max", "i2", ("lat", "lon"),
            zlib=True, complevel=4, chunksizes=(chunk_lat, chunk_lon),
            fill_value=np.int16(FILL_VALUE),
        )
        v.scale_factor = np.float32(SCALE_FACTOR)
        v.add_offset = np.float32(ADD_OFFSET)
        v.units = "degree_C"
        v.long_name = ("CRW CoralTemp v3.1 per-pixel record-max SST for this "
                       "calendar day, 1985-present")
        v.set_auto_maskandscale(False)
        v[:, :] = pack_int16(max_belt)

        if year_belt is not None:
            vy = ds.createVariable(
                "record_year", "i2", ("lat", "lon"),
                zlib=True, complevel=4, chunksizes=(chunk_lat, chunk_lon),
                fill_value=np.int16(NO_RECORD_YEAR),
            )
            vy.long_name = "year the standing record-max was set"
            vy.set_auto_maskandscale(False)
            vy[:, :] = year_belt.astype(np.int16)

        ds.climatology_window = "1985-present"
        ds.source = ("NOAA Coral Reef Watch CoralTemp v3.1 daily 5km SST "
                     "(analysed_sst)")
        ds.method = ("per-pixel np.fmax of daily CoralTemp v3.1 across "
                     "1985-present for this calendar day; record_year = year "
                     "the standing max was set")
        ds.doy_index = int(doy)
        ds.month = int(month)
        ds.day = int(day)
        ds.doy_index_contract = "date(2000, month, day).timetuple().tm_yday"
        ds.lat_min = float(BELT_LAT_MIN)
        ds.lat_max = float(BELT_LAT_MAX)
        ds.lon_convention = "-180..180"
        marker = _read_marker()
        ds.archive_span = (f"{ARCHIVE_START.isoformat()}.."
                           f"{marker.isoformat() if marker else 'unknown'}")
        ds.built_utc = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp.replace(out_path)


def finalize(record_year: bool, upload: bool) -> int:
    """Assemble per-DOY NetCDFs from the on-disk accumulators, then write the
    manifest (the consumer's gate). Requires all 366 max accumulators."""
    meta = _load_grid_meta()
    if meta is None:
        print(f"{LOG} finalize: no grid_meta — run the pass first.",
              file=sys.stderr)
        return 1
    lat, lon = meta

    missing = [doy for doy in range(1, 367)
               if not _max_state_path(doy).exists()]
    if missing:
        print(f"{LOG} finalize: {len(missing)} DOY accumulator(s) MISSING — "
              f"manifest NOT written. First missing: {missing[:10]}",
              file=sys.stderr)
        return 1

    for doy in range(1, 367):
        max_acc = np.asarray(np.load(_max_state_path(doy), mmap_mode="r"))
        # -inf pixels (never observed) → NaN so pack_int16 emits the fill.
        max_belt = np.where(np.isfinite(max_acc), max_acc, np.nan
                            ).astype(np.float32)
        year_belt = None
        if record_year and _year_state_path(doy).exists():
            year_belt = np.asarray(np.load(_year_state_path(doy),
                                           mmap_mode="r"))
        out_path = STATE_DIR / FILE_PATTERN.format(doy=doy)
        _write_record_nc(doy, max_belt, year_belt, lat, lon, out_path)
        if upload:
            key = f"{R2_PREFIX}/{out_path.name}"
            upload_to_r2(out_path, key,
                         cache_control="public, max-age=2592000")
        if doy % 30 == 0:
            print(f"{LOG} finalize: wrote DOY {doy:03d}")

    marker = _read_marker()
    manifest = {
        "version": 1,
        "window": "1985-present",
        "archive_span": (f"{ARCHIVE_START.isoformat()}.."
                         f"{marker.isoformat() if marker else 'unknown'}"),
        "doy_count": 366,
        "lat_min": BELT_LAT_MIN,
        "lat_max": BELT_LAT_MAX,
        "resolution_deg": 0.05,
        "lon_convention": "-180..180",
        "scale_factor": SCALE_FACTOR,
        "fill_value": FILL_VALUE,
        "var": "sst_record_max",
        "record_year_var": "record_year" if record_year else None,
        "record_year_fill": NO_RECORD_YEAR,
        "doy_index": "date(2000,m,d).tm_yday",
        "file_pattern": FILE_PATTERN,
        "built_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    manifest_path = STATE_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{LOG} finalize: all 366 present; wrote {manifest_path.name}")
    if upload:
        upload_to_r2(manifest_path, f"{R2_PREFIX}/manifest.json",
                     content_type="application/json",
                     cache_control="public, max-age=60")
        print(f"{LOG} finalize: uploaded manifest → {R2_PREFIX}/manifest.json")
    return 0


# --- CLI ----------------------------------------------------------------


def _parse_date(s: str | None) -> dt.date | None:
    return None if not s else dt.date.fromisoformat(s)


def parse_args(argv):
    p = argparse.ArgumentParser(description="Bake CRW day-of-year per-pixel "
                                            "daily record-max SST grids → R2.")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", action="store_true",
                      help="Single resumable pass over the archive, updating "
                           "the on-disk accumulators.")
    mode.add_argument("--finalize", action="store_true",
                      help="Assemble per-DOY NetCDFs from the accumulators "
                           "and write+upload the manifest.")
    p.add_argument("--start-date", type=str, default=None,
                   help="Override pass start (YYYY-MM-DD; default archive "
                        "start or marker+1).")
    p.add_argument("--end-date", type=str, default=None,
                   help="Override pass end (YYYY-MM-DD; default today-2).")
    p.add_argument("--no-record-year", action="store_true",
                   help="Skip the record_year grid (halves write I/O).")
    p.add_argument("--upload", action="store_true",
                   help="Upload per-DOY .nc + manifest to R2 on --finalize.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    record_year = not args.no_record_year
    if args.run:
        return run_pass(_parse_date(args.start_date),
                        _parse_date(args.end_date), record_year)
    return finalize(record_year, args.upload)


if __name__ == "__main__":
    raise SystemExit(main())
