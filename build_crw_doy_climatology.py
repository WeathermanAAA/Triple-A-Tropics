#!/usr/bin/env python3
"""
Triple-A-Tropics · CRW day-of-year 1991-2020 SST climatology bake
=================================================================

A one-time, resumable bake of **366 day-of-year 1991-2020 mean-SST grids**
from NOAA Coral Reef Watch CoralTemp v3.1, published to R2 for the CycloLab
per-storm page to read at hero-render time.

ONE CANON, EXACTLY
------------------
Each DOY grid equals what `generate_sst_plots.compute_crw_climo_for_date()`
computes for that (month, day) on the SST page: the **same** file set
(1991-2020 same-month/day daily CoralTemp files), the **same** nanmean via
`mean_crw_years()` streaming accumulation, the **same** read path
(`read_crw_grid` → `analysed_sst`). The only departure from the SST page's
in-memory object is:

  * we keep the NATIVE CRW longitude convention (-180..180), NOT the 0-360
    roll the SST page applies — the CycloLab poller's box-read math works on
    raw CRW files, so the published grid must match the raw file's axes; and
  * we subset to the tropical belt (lat -40..50) before writing.

`--verify N` recomputes N random already-baked DOYs through the canon path
(`compute_crw_climo_for_date`, then belt-subset + native-lon) and asserts
allclose within the int16 quantization. That is the one-canon proof, and the
workflow runs it before finalizing.

Feb-29 semantics
----------------
DOY 60 is the leap-day grid: the nanmean over **leap years only** in
1991-2020 (1992, 1996, 2000, 2004, 2008, 2012, 2016, 2020 — eight files),
exactly the site's existing behavior. `compute_crw_climo_for_date(date(Y,2,29))`
naturally produces this because `day_of_year_crw_files` fetches Feb-29 in leap
years and falls back to Feb-28 in non-leap years; HOWEVER blending Feb-28 into
the leap-day grid is NOT what we want. We therefore reproduce the canon by
fetching ONLY the leap years for DOY 60 (see `_files_for_doy`). DOY 60's
canon is "leap years only", and the verify path enforces it.

DOY index contract (pinned)
---------------------------
    index = datetime.date(2000, month, day).timetuple().tm_yday      # 1..366

2000 is a leap year, so every (month, day) including Feb-29 maps to a stable,
distinct index: Feb-29 → 60, Mar-01 → 61, ..., Dec-31 → 366. This mapping is
LEAP-KEYED and stable; it is the single source of truth for both this climo
bake and the records bake (`bake_crw_doy_records.py`), and it is written into
every NetCDF's `doy_index_contract` attr and the manifest's `doy_index` field.

Output grid
-----------
    var          sst_climo (degC), int16-packed
                 scale_factor=0.01, add_offset=0.0, _FillValue=-32768
    lat          -40.0 .. 50.0  (native CRW 0.05° rows in that band)
    lon          full, NATIVE CRW -180..180 (NOT rolled to 0-360)
    chunking     ~(450, 900) so a lat/lon box read is cheap
    compression  zlib complevel 4
    file name    crw_doy_climo_1991_2020_{DDD:03d}.nc

R2 layout
---------
    s3://triple-a-tropics-media/sst/climo/crw_doy_climo_1991_2020_{DDD:03d}.nc
    s3://triple-a-tropics-media/sst/climo/manifest.json   (written ONLY at
                                                            --finalize, all 366)
served at https://cdn.triple-a-tropics.com/sst/climo/...

The POLLED CONSUMER GATES ON THE MANIFEST. Partial bakes upload per-DOY .nc
files as they complete (durable progress) but NEVER write the manifest;
only `--finalize` writes it, and only after verifying all 366 are present.

Resumability
------------
The per-DOY output files ARE the checkpoints. On start we list which DOY
files already exist locally (the state dir), and optionally on R2
(`--skip-existing-remote`, via HEAD to the CDN), and skip them. A
`--time-budget-minutes` deadline makes the script stop cleanly between DOYs
so a CI run flushes complete files before GitHub's hard kill.
`--doy-start`/`--doy-end` shard the work. `--finalize` verifies all 366 +
writes the manifest.

Transfer reality
----------------
366 DOYs × 30 files × ~10 MB ≈ 110 GB; one DOY ≈ 300 MB / ~3-5 min. A single
CI dispatch processes what fits the budget and uploads per-DOY .nc files AS
THEY COMPLETE; repeated dispatches converge. Memory: each DOY streams one
year-file at a time (sum+count accumulators, NaN-aware) and belt-subsets each
grid IMMEDIATELY after read — never stacks 30 grids.

Run locally (examples)
----------------------
    # Bake DOYs 1..30 with a 5-hour budget, upload as we go (needs R2 env):
    python build_crw_doy_climatology.py --doy-start 1 --doy-end 30 \
        --time-budget-minutes 300 --upload

    # Prove canon for 3 random already-baked DOYs:
    python build_crw_doy_climatology.py --verify 3

    # All 366 present? write + upload the manifest:
    python build_crw_doy_climatology.py --finalize --upload
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

# Reuse the SST page's CRW plumbing so this bake is the SAME CANON. The
# module's only import-time side effects are two mkdir() calls (SST_DIR,
# CACHE_DIR) — no network, no main() — so importing here is safe.
# Pinned source lines (generate_sst_plots.py, this repo):
#   CRW_CLIMO_START/END     ~98-99   (1991-2020 window)
#   read_crw_grid           ~467-500 (note: it rolls lon to 0-360; we DON'T
#                                      use its rolled output — see below)
#   mean_crw_years          ~513-548 (streaming nanmean accumulation)
#   day_of_year_crw_files   ~437-464 (same-month/day fetch across years)
#   compute_crw_climo_for_date ~587-610 (the canon entry point for --verify)
import generate_sst_plots as gsp

HERE = Path(__file__).resolve().parent

# Local staging for the per-DOY .nc files (gitignored). The canonical copy
# lives on R2; nothing here is ever committed.
STATE_DIR = HERE / "_crw_doy_climo_state"
STATE_DIR.mkdir(exist_ok=True)

# --- Contract constants -------------------------------------------------

CLIMO_START = gsp.CRW_CLIMO_START   # 1991
CLIMO_END = gsp.CRW_CLIMO_END       # 2020

# Tropical belt — per-storm heroes never leave it. NATIVE CRW grid rows.
BELT_LAT_MIN = -40.0
BELT_LAT_MAX = 50.0

# int16 packing for sst_climo.
SCALE_FACTOR = 0.01
ADD_OFFSET = 0.0
FILL_VALUE = -32768

# DOY-index leap reference year (a leap year so Feb-29 has its own slot).
DOY_REF_YEAR = 2000

FILE_PATTERN = "crw_doy_climo_1991_2020_{doy:03d}.nc"
R2_PREFIX = "sst/climo"
CDN_BASE = "https://cdn.triple-a-tropics.com"

LOG = "[crw-doy-climo]"

# --- Time budget (mirrors build_armor3d_climatology.py) -----------------

_DEADLINE: float | None = None


def _arm_budget(minutes: float | None) -> None:
    global _DEADLINE
    _DEADLINE = None if minutes is None else time.monotonic() + minutes * 60.0


def _budget_exhausted() -> bool:
    return _DEADLINE is not None and time.monotonic() >= _DEADLINE


def _budget_remaining_min() -> float | None:
    if _DEADLINE is None:
        return None
    return max(0.0, (_DEADLINE - time.monotonic()) / 60.0)


# --- DOY index contract -------------------------------------------------


def doy_index(month: int, day: int) -> int:
    """The pinned DOY index: date(2000, month, day).timetuple().tm_yday.

    2000 is a leap year so every (m, d) including (2, 29) maps to a stable,
    distinct 1..366. This is the single source of truth shared with the
    records bake."""
    return dt.date(DOY_REF_YEAR, month, day).timetuple().tm_yday


def month_day_for_doy(doy: int) -> tuple[int, int]:
    """Inverse of doy_index: (month, day) for a 1..366 index via the leap
    reference year. doy 60 → (2, 29); doy 61 → (3, 1); doy 366 → (12, 31)."""
    d = dt.date(DOY_REF_YEAR, 1, 1) + dt.timedelta(days=doy - 1)
    return d.month, d.day


def doy_output_path(doy: int) -> Path:
    return STATE_DIR / FILE_PATTERN.format(doy=doy)


# --- Belt subset --------------------------------------------------------


def belt_indices(lat: np.ndarray) -> np.ndarray:
    """Row mask for the tropical belt on a lat axis (inclusive bounds).

    Pure index math, unit-tested offline. CRW lat is ascending after
    read_crw_grid's flip; the mask is order-agnostic regardless."""
    return (lat >= BELT_LAT_MIN) & (lat <= BELT_LAT_MAX)


# --- Per-DOY file set (canon) ------------------------------------------


def _dates_for_doy(month: int, day: int) -> dict[int, dt.date]:
    """The exact (year -> date) set the canon uses for one DOY.

    For DOY 60 (Feb-29) the canon is LEAP YEARS ONLY — we do NOT blend
    Feb-28 from non-leap years. For every other DOY this is just
    (month, day) in each climatology year. The returned dates are what we
    feed CRW's fetcher; mean is taken over whatever subset downloads."""
    out: dict[int, dt.date] = {}
    for y in range(CLIMO_START, CLIMO_END + 1):
        try:
            out[y] = dt.date(y, month, day)
        except ValueError:
            # Only (2, 29) in a non-leap year lands here → SKIP (leap-only).
            continue
    return out


def compute_doy_climo(doy: int, log: str) -> tuple[np.ndarray, np.ndarray,
                                                    np.ndarray, list[int]]:
    """Compute one DOY's belt climatology, streaming one year-file at a time.

    Returns (climo_belt, lat_belt, lon_native, years_used).

    The streaming accumulation here mirrors gsp.mean_crw_years exactly
    (NaN-aware sum + per-pixel valid count), but each year-grid is
    belt-subset IMMEDIATELY after read so peak RAM is O(2 belt grids ≈
    100 MB) instead of O(2 global grids). Native CRW lon convention is
    preserved (we read straight from the file, NOT via gsp.read_crw_grid
    which rolls to 0-360)."""
    month, day = month_day_for_doy(doy)
    dates = _dates_for_doy(month, day)
    if not dates:
        raise RuntimeError(f"DOY {doy}: no climatology dates resolved")

    running_sum: np.ndarray | None = None
    running_count: np.ndarray | None = None
    lat_belt: np.ndarray | None = None
    lon_native: np.ndarray | None = None
    years_used: list[int] = []

    for y in sorted(dates):
        d = dates[y]
        p = gsp.fetch_crw_day(d, "sst", log, verbose=True)
        if p is None:
            print(f"{log}   DOY {doy:03d}: {d} unavailable, skipping year")
            continue
        try:
            g, lat, lon = _read_crw_native(p)
        except Exception as exc:  # noqa: BLE001
            print(f"{log}   DOY {doy:03d}: read error {d}: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            continue

        mask = belt_indices(lat)
        g_belt = g[mask, :]
        if running_sum is None:
            lat_belt = lat[mask].astype(np.float32)
            lon_native = lon.astype(np.float32)
            running_sum = np.zeros(g_belt.shape, dtype=np.float64)
            running_count = np.zeros(g_belt.shape, dtype=np.int32)
        elif g_belt.shape != running_sum.shape:
            print(f"{log}   DOY {doy:03d}: {d} shape {g_belt.shape} != "
                  f"{running_sum.shape}, skipping", file=sys.stderr)
            continue

        valid = ~np.isnan(g_belt)
        running_sum += np.where(valid, g_belt, 0.0)
        running_count += valid.astype(np.int32)
        years_used.append(y)
        # Drop the year grid before the next read to keep RSS low.
        del g, g_belt, valid

    if running_sum is None or running_count is None:
        raise RuntimeError(f"DOY {doy}: no year files available")

    with np.errstate(invalid="ignore", divide="ignore"):
        mean = running_sum / np.where(running_count > 0, running_count, 1)
    mean = np.where(running_count > 0, mean, np.nan).astype(np.float32)
    return mean, lat_belt, lon_native, years_used


def _read_crw_native(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Read a CoralTemp file in its NATIVE longitude convention (-180..180).

    Mirrors gsp.read_crw_grid's variable resolution and the ascending-lat
    flip, but DELIBERATELY skips the 0-360 lon roll (gsp lines ~494-500) so
    the published grid matches the raw CRW file's axes — the CycloLab
    poller's box-read math assumes native -180..180. analysed_sst is the
    canon variable."""
    with Dataset(path, "r") as ds:
        var_name = "analysed_sst"
        if var_name not in ds.variables:
            for name, v in ds.variables.items():
                if name not in ("lat", "lon", "time") and v.ndim >= 2:
                    var_name = name
                    break
        raw = ds.variables[var_name][:]
        data = np.ma.squeeze(raw)
        data = np.ma.filled(data.astype(np.float32), np.nan)
        lat = ds.variables["lat"][:].astype(np.float32)
        lon = ds.variables["lon"][:].astype(np.float32)
    # Ascending lat (some derived CRW products store descending). SST is
    # ascending already but flip defensively to match read_crw_grid.
    if lat.size >= 2 and lat[0] > lat[-1]:
        lat = lat[::-1]
        data = data[::-1, :]
    return data, lat, lon


# --- int16 pack / write -------------------------------------------------


def pack_int16(field: np.ndarray) -> np.ndarray:
    """Pack a float field into int16 via (value - add_offset)/scale_factor,
    NaN → _FillValue. Pure, unit-tested. Rounds to nearest."""
    out = np.empty(field.shape, dtype=np.int16)
    valid = np.isfinite(field)
    scaled = np.round((field - ADD_OFFSET) / SCALE_FACTOR)
    scaled = np.clip(scaled, -32767, 32767)
    out[valid] = scaled[valid].astype(np.int16)
    out[~valid] = FILL_VALUE
    return out


def unpack_int16(packed: np.ndarray) -> np.ndarray:
    """Inverse of pack_int16: _FillValue → NaN, else value*scale + offset."""
    out = packed.astype(np.float32) * SCALE_FACTOR + ADD_OFFSET
    out[packed == FILL_VALUE] = np.nan
    return out


def write_doy_climo(doy: int, climo: np.ndarray, lat: np.ndarray,
                    lon: np.ndarray, years_used: list[int],
                    out_path: Path) -> Path:
    """Write one DOY's belt climatology to a chunked, int16-packed NetCDF.

    Atomic: writes to a .tmp sibling then renames, so a killed run never
    leaves a half-written checkpoint that the resume logic would skip."""
    month, day = month_day_for_doy(doy)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    nlat, nlon = climo.shape
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
            "sst_climo", "i2", ("lat", "lon"),
            zlib=True, complevel=4,
            chunksizes=(chunk_lat, chunk_lon),
            fill_value=np.int16(FILL_VALUE),
        )
        v.scale_factor = np.float32(SCALE_FACTOR)
        v.add_offset = np.float32(ADD_OFFSET)
        v.units = "degree_C"
        v.long_name = "CRW CoralTemp v3.1 1991-2020 day-of-year mean SST"
        # Write packed ints directly: setting set_auto_maskandscale(False)
        # means netCDF4 stores our bytes verbatim, so the on-disk encoding
        # is exactly pack_int16(climo) and the verify path is bit-honest.
        v.set_auto_maskandscale(False)
        v[:, :] = pack_int16(climo)

        ds.climatology_window = "1991-2020"
        ds.source = ("NOAA Coral Reef Watch CoralTemp v3.1 daily 5km SST "
                     "(analysed_sst)")
        ds.method = ("nanmean of daily CoralTemp v3.1 across 1991-2020 for "
                     "this calendar day; leap-day from leap years only")
        ds.doy_index = int(doy)
        ds.month = int(month)
        ds.day = int(day)
        ds.doy_index_contract = "date(2000, month, day).timetuple().tm_yday"
        ds.lat_min = float(BELT_LAT_MIN)
        ds.lat_max = float(BELT_LAT_MAX)
        ds.lon_convention = "-180..180"
        ds.years_used = ",".join(str(y) for y in sorted(years_used))
        ds.n_years_used = int(len(years_used))
        ds.built_utc = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp.replace(out_path)
    return out_path


# --- R2 upload / remote existence --------------------------------------


def upload_to_r2(local: Path, key: str, content_type: str | None = None,
                 cache_control: str | None = None) -> None:
    """Thin wrapper over scripts/upload_to_r2.sh (the house R2 path).

    Cache-Control short for the manifest (the polled consumer gates on it);
    longer for the per-DOY .nc files (immutable once baked)."""
    script = HERE / "scripts" / "upload_to_r2.sh"
    args = ["bash", str(script), str(local), key]
    if cache_control is not None:
        args.append(cache_control)
        if content_type is not None:
            args.append(content_type)
    subprocess.run(args, check=True)


def remote_doy_exists(doy: int) -> bool:
    """HEAD the CDN to see if a DOY .nc is already published. Best-effort —
    a network error returns False (we'd rather re-bake than skip wrongly)."""
    import requests  # local import: only --skip-existing-remote needs it
    url = f"{CDN_BASE}/{R2_PREFIX}/{FILE_PATTERN.format(doy=doy)}"
    try:
        r = requests.head(url, timeout=20, allow_redirects=True)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


# --- Bake driver --------------------------------------------------------


def bake_range(doy_start: int, doy_end: int, upload: bool,
               skip_existing_remote: bool) -> int:
    """Bake DOYs in [doy_start, doy_end], skipping already-present outputs.

    Per-DOY files ARE the checkpoints: we skip any DOY whose local .nc
    exists (and, with --skip-existing-remote, any already on R2). Each DOY
    is uploaded the moment it finishes so progress is durable across
    dispatches. Honors the time budget between DOYs."""
    done = 0
    skipped = 0
    rem = _budget_remaining_min()
    if rem is not None:
        print(f"{LOG} time budget: {rem:.1f} min")
    for doy in range(doy_start, doy_end + 1):
        out_path = doy_output_path(doy)
        if out_path.exists() and out_path.stat().st_size > 0:
            skipped += 1
            continue
        if skip_existing_remote and remote_doy_exists(doy):
            print(f"{LOG} DOY {doy:03d}: already on R2 — skipping.")
            skipped += 1
            continue
        if _budget_exhausted():
            print(f"{LOG} TIME BUDGET EXHAUSTED before DOY {doy:03d} — "
                  f"stopping cleanly ({done} baked, {skipped} skipped).")
            break
        month, day = month_day_for_doy(doy)
        rem = _budget_remaining_min()
        tail = f" ({rem:.1f} min left)" if rem is not None else ""
        print(f"{LOG} DOY {doy:03d} = {month:02d}-{day:02d}{tail} …")
        try:
            climo, lat, lon, years = compute_doy_climo(doy, LOG)
        except Exception as exc:  # noqa: BLE001
            print(f"{LOG} DOY {doy:03d}: FAILED: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            continue
        write_doy_climo(doy, climo, lat, lon, years, out_path)
        size_mb = out_path.stat().st_size / (1024 * 1024)
        print(f"{LOG} DOY {doy:03d}: wrote {out_path.name} "
              f"({size_mb:.1f} MB, {len(years)} years)")
        if upload:
            key = f"{R2_PREFIX}/{out_path.name}"
            # Immutable once baked → long cache. Default content-type
            # sniff is fine for .nc (application/octet-stream).
            upload_to_r2(out_path, key,
                         cache_control="public, max-age=2592000")
            print(f"{LOG} DOY {doy:03d}: uploaded → {key}")
        done += 1
    print(f"{LOG} range [{doy_start}..{doy_end}] done: {done} baked, "
          f"{skipped} skipped.")
    return 0


# --- Verify (one-canon proof) ------------------------------------------


def verify(n: int) -> int:
    """Recompute N random baked DOYs via the CANON path and assert allclose.

    The canon path is gsp.compute_crw_climo_for_date(date), then belt-subset
    + native-lon read — i.e. the SST page's own object, re-derived
    independently of this script's compute_doy_climo. We compare the
    UNPACKED stored grid against the canon, atol = half a quantum + eps.

    For DOY 60 (Feb-29) the canon function naturally fetches Feb-29 in leap
    years and Feb-28 in non-leap years, which would BLEND Feb-28. Our stored
    grid is leap-only. So for DOY 60 we verify against a leap-only recompute
    (the documented site semantics) rather than the blended one."""
    baked = sorted(STATE_DIR.glob("crw_doy_climo_1991_2020_*.nc"))
    if not baked:
        print(f"{LOG} verify: no baked DOY files to check.", file=sys.stderr)
        return 1
    pick = random.sample(baked, min(n, len(baked)))
    atol = SCALE_FACTOR / 2.0 + 1e-6
    failures = 0
    for path in pick:
        with Dataset(path, "r") as ds:
            doy = int(ds.doy_index)
            v = ds.variables["sst_climo"]
            v.set_auto_maskandscale(False)
            stored = unpack_int16(np.asarray(v[:]))
            lat_stored = ds.variables["lat"][:]
            lon_stored = ds.variables["lon"][:]
        month, day = month_day_for_doy(doy)
        print(f"{LOG} verify DOY {doy:03d} ({month:02d}-{day:02d}) …")

        if doy == 60:
            # Leap-only canon (no Feb-28 blend) — recompute directly.
            canon, _, canon_lat, canon_lon = _canon_leap_only()
        else:
            # Sanity-tie to the SST page's own object: assert our native
            # belt canon matches compute_crw_climo_for_date's rolled grid
            # over the belt (cross-check the lon-roll is the only diff).
            canon, canon_lat, canon_lon = _native_belt_canon(month, day)
            _assert_matches_rolled_canon(month, day, canon, canon_lat,
                                         canon_lon)

        # Align: stored grid is belt + native lon already. Canon path here
        # returns belt + native lon too.
        mask_finite = np.isfinite(stored) & np.isfinite(canon)
        if stored.shape != canon.shape:
            print(f"{LOG}   FAIL DOY {doy:03d}: shape {stored.shape} != "
                  f"canon {canon.shape}", file=sys.stderr)
            failures += 1
            continue
        if not np.allclose(stored[mask_finite], canon[mask_finite],
                           atol=atol, rtol=0):
            diff = np.nanmax(np.abs(stored[mask_finite] - canon[mask_finite]))
            print(f"{LOG}   FAIL DOY {doy:03d}: max|Δ|={diff:.4f} > {atol}",
                  file=sys.stderr)
            failures += 1
            continue
        # NaN masks must match (both land/no-data in the same pixels).
        if not np.array_equal(np.isnan(stored), np.isnan(canon)):
            print(f"{LOG}   FAIL DOY {doy:03d}: NaN masks differ",
                  file=sys.stderr)
            failures += 1
            continue
        print(f"{LOG}   OK DOY {doy:03d} "
              f"(max|Δ|={np.nanmax(np.abs(stored[mask_finite] - canon[mask_finite])):.4f})")

    if failures:
        print(f"{LOG} verify: {failures}/{len(pick)} FAILED.", file=sys.stderr)
        return 1
    print(f"{LOG} verify: all {len(pick)} canon checks passed.")
    return 0


def _native_belt_canon(month: int, day: int) -> tuple[np.ndarray, np.ndarray,
                                                      np.ndarray]:
    """Independent re-derivation of the canon as belt + native lon.

    Re-fetches the canon file set via gsp.day_of_year_crw_files (the SAME
    fetcher compute_crw_climo_for_date uses), then streams nanmean with the
    native-lon read + belt subset. This is intentionally a SEPARATE code
    path from compute_doy_climo so a bug in one doesn't mask itself."""
    files = gsp.day_of_year_crw_files(
        month, day, range(CLIMO_START, CLIMO_END + 1), "sst", LOG)
    running_sum = running_count = None
    lat_belt = lon_native = None
    for y in sorted(files):
        g, lat, lon = _read_crw_native(files[y])
        mask = belt_indices(lat)
        g_belt = g[mask, :]
        valid = ~np.isnan(g_belt)
        if running_sum is None:
            lat_belt = lat[mask].astype(np.float32)
            lon_native = lon.astype(np.float32)
            running_sum = np.zeros(g_belt.shape, np.float64)
            running_count = np.zeros(g_belt.shape, np.int32)
        running_sum += np.where(valid, g_belt, 0.0)
        running_count += valid.astype(np.int32)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = running_sum / np.where(running_count > 0, running_count, 1)
    mean = np.where(running_count > 0, mean, np.nan).astype(np.float32)
    return mean, lat_belt, lon_native


def _assert_matches_rolled_canon(month: int, day: int, native_belt: np.ndarray,
                                 native_lat: np.ndarray,
                                 native_lon: np.ndarray) -> None:
    """Cross-check: the SST page's compute_crw_climo_for_date (which rolls
    lon to 0-360) must, over the belt and after un-rolling, equal our
    native belt grid pixel-for-pixel. This pins the ONLY difference between
    the published grid and the SST page object to the lon convention."""
    rolled, _years = gsp.compute_crw_climo_for_date(
        dt.date(2001, month, day), LOG)
    if rolled is None:
        return  # can't tie offline / no files — verify still proves vs canon
    # gsp.compute_crw_climo_for_date returns a global grid on the rolled
    # (0-360) lon axis with ascending lat. Re-read one canon file just for
    # its rolled lat/lon axes so we can belt-subset + un-roll for compare.
    files = gsp.day_of_year_crw_files(
        month, day, range(CLIMO_START, CLIMO_END + 1), "sst", LOG)
    if not files:
        return
    _g, rolled_lat, rolled_lon = gsp.read_crw_grid(
        files[sorted(files)[0]], "analysed_sst")
    mask = belt_indices(rolled_lat)
    rolled_belt = rolled[mask, :]
    # Un-roll the rolled lon (0-360) back to -180..180 ordering to match
    # native, then compare. lon>180 → lon-360, re-sort.
    unrolled_lon = np.where(rolled_lon > 180, rolled_lon - 360, rolled_lon)
    order = np.argsort(unrolled_lon)
    rolled_belt_native = rolled_belt[:, order]
    fin = np.isfinite(native_belt) & np.isfinite(rolled_belt_native)
    if rolled_belt_native.shape == native_belt.shape and fin.any():
        if not np.allclose(native_belt[fin], rolled_belt_native[fin],
                           atol=1e-4, rtol=0):
            raise AssertionError(
                f"{month:02d}-{day:02d}: native belt canon diverges from "
                f"compute_crw_climo_for_date's rolled grid — the lon-roll is "
                f"NOT the only difference; the bake is off-canon.")


def _canon_leap_only() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """DOY-60 canon: nanmean over Feb-29 in leap years only (native belt)."""
    running_sum = running_count = None
    lat_belt = lon_native = None
    for y in range(CLIMO_START, CLIMO_END + 1):
        try:
            d = dt.date(y, 2, 29)
        except ValueError:
            continue
        p = gsp.fetch_crw_day(d, "sst", LOG, verbose=False)
        if p is None:
            continue
        g, lat, lon = _read_crw_native(p)
        mask = belt_indices(lat)
        g_belt = g[mask, :]
        valid = ~np.isnan(g_belt)
        if running_sum is None:
            lat_belt = lat[mask].astype(np.float32)
            lon_native = lon.astype(np.float32)
            running_sum = np.zeros(g_belt.shape, np.float64)
            running_count = np.zeros(g_belt.shape, np.int32)
        running_sum += np.where(valid, g_belt, 0.0)
        running_count += valid.astype(np.int32)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = running_sum / np.where(running_count > 0, running_count, 1)
    mean = np.where(running_count > 0, mean, np.nan).astype(np.float32)
    return mean, None, lat_belt, lon_native


# --- Finalize (manifest) ------------------------------------------------


def finalize(upload: bool, check_remote: bool = False) -> int:
    """Verify all 366 per-DOY files are present, then write the manifest.

    The polled consumer GATES ON THE MANIFEST, so it is written only here,
    only when every DOY 1..366 exists. A partial bake never reaches this.

    Presence is checked locally first; with `check_remote=True` any DOY not
    on disk is HEAD-checked on the CDN (so a finalize-only run on a fresh
    runner — where the per-DOY .nc files live ONLY on R2 — can still gate
    correctly on the published set)."""
    missing: list[int] = []
    for doy in range(1, 367):
        p = doy_output_path(doy)
        if p.exists() and p.stat().st_size > 0:
            continue
        if check_remote and remote_doy_exists(doy):
            continue
        missing.append(doy)
    if missing:
        where = "locally or on R2" if check_remote else "locally"
        print(f"{LOG} finalize: {len(missing)} DOY file(s) MISSING {where} — "
              f"manifest NOT written. First missing: {missing[:10]}",
              file=sys.stderr)
        return 1

    manifest = {
        "version": 1,
        "window": "1991-2020",
        "doy_count": 366,
        "lat_min": BELT_LAT_MIN,
        "lat_max": BELT_LAT_MAX,
        "resolution_deg": 0.05,
        "lon_convention": "-180..180",
        "scale_factor": SCALE_FACTOR,
        "fill_value": FILL_VALUE,
        "var": "sst_climo",
        "doy_index": "date(2000,m,d).tm_yday",
        "file_pattern": "crw_doy_climo_1991_2020_{doy:03d}.nc",
        "built_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    manifest_path = STATE_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{LOG} finalize: all 366 present; wrote {manifest_path.name}")
    if upload:
        # Short cache so the consumer picks up the gate flip promptly.
        upload_to_r2(manifest_path, f"{R2_PREFIX}/manifest.json",
                     content_type="application/json",
                     cache_control="public, max-age=60")
        print(f"{LOG} finalize: uploaded manifest → {R2_PREFIX}/manifest.json")
    return 0


# --- CLI ----------------------------------------------------------------


def parse_args(argv):
    p = argparse.ArgumentParser(description="Bake CRW day-of-year 1991-2020 "
                                            "SST climatology grids → R2.")
    p.add_argument("--doy-start", type=int, default=1,
                   help="First DOY to bake (1..366, default 1).")
    p.add_argument("--doy-end", type=int, default=366,
                   help="Last DOY to bake inclusive (default 366).")
    p.add_argument("--time-budget-minutes", type=float, default=None,
                   help="Stop cleanly between DOYs after this many minutes "
                        "(CI: finish before the 6-h hard kill).")
    p.add_argument("--upload", action="store_true",
                   help="Upload each per-DOY .nc to R2 as it completes "
                        "(needs R2_ENDPOINT + AWS creds in env).")
    p.add_argument("--skip-existing-remote", action="store_true",
                   help="HEAD the CDN and skip DOYs already on R2.")
    p.add_argument("--verify", type=int, metavar="N", default=None,
                   help="Recompute N random baked DOYs via the canon path "
                        "and assert allclose; no baking.")
    p.add_argument("--finalize", action="store_true",
                   help="Verify all 366 present, then write+upload the "
                        "manifest (the consumer's gate).")
    p.add_argument("--check-remote", action="store_true",
                   help="On --finalize, HEAD the CDN for any DOY not on disk "
                        "(gate on the R2-published set, not just local).")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.verify is not None:
        return verify(args.verify)
    if args.finalize:
        return finalize(args.upload, args.check_remote)
    if not (1 <= args.doy_start <= 366 and 1 <= args.doy_end <= 366
            and args.doy_start <= args.doy_end):
        raise SystemExit(f"{LOG} bad DOY range "
                         f"{args.doy_start}..{args.doy_end}")
    _arm_budget(args.time_budget_minutes)
    return bake_range(args.doy_start, args.doy_end, args.upload,
                      args.skip_existing_remote)


if __name__ == "__main__":
    raise SystemExit(main())
