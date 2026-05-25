#!/usr/bin/env python3
"""Daily SST climatology curves (kouya-style), one per region.

The "curve" product is the spaghetti plot meteorologists know from
ClimateReanalyzer / kouya: for a given region, every historical year's
region-mean SST is drawn as a faint line across the day-of-year axis,
with the 1991–2020 daily mean and the most-recent years highlighted on
top. It answers "where does today sit against the whole record?" at a
glance.

THE DATA PROBLEM. The curve needs region-mean SST for *every day of
every year* (OISST 1982-present ≈ 16k daily grids). Re-downloading 16k
grids on every daily run is infeasible, so we keep a compact cache of
just the scalar region means:

    _sst_clim_build/sst_region_daily_means.npz
        dates   int32  (N,)             YYYYMMDD per day we have
        means   float32 (n_regions, N)  cos-lat-weighted region mean SST
        regions <U..   (n_regions,)      region slugs, row order

That's 18 regions × ~16k days × 4 B ≈ 1.2 MB raw (~0.7 MB compressed) —
small, but it is a build INPUT that is rewritten daily, so it lives on
R2 (cdn.triple-a-tropics.com/sst/sst_region_daily_means.npz) rather than
in git, exactly like armor3d/armor3d_climatology.nc. Never commit it.

Two entry points:
  --backfill   one-time (manual workflow): walk 1982→present, computing
               the per-region means and discarding each grid immediately
               (26 GB of downloads won't fit on a runner). Resumable:
               re-runs skip dates already in the cache, and a
               --time-budget-minutes deadline lets the job stop cleanly
               before GitHub's 6-h hard-kill so the cache survives.
  --update     daily (folded into update-sst.yml): append the newest
               available day's means to the cache.

The region-mean math reuses generate_sst_plots' own _subset_to_extent +
compute_global_mean so the cached scalar equals the cos-lat-weighted
ocean mean of exactly the area each published region map shows.

Phase 2 adds the actual curve rendering; this module is the data layer.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import sys
import time
from pathlib import Path

import numpy as np

# Reuse the OISST plumbing + region definitions from the map generator.
# Same directory, so a plain import works both locally and in CI.
import generate_sst_plots as gsp
from generate_sst_plots import (
    REGIONS,
    _subset_to_extent,
    compute_global_mean,
)

HERE = Path(__file__).resolve().parent
SST_DIR = HERE / "sst"
SST_DIR.mkdir(parents=True, exist_ok=True)

# Local staging dir for the cache npz (gitignored). The canonical copy
# lives on R2; the workflow fetches it here before --update/--backfill
# and uploads it back afterwards.
CLIM_BUILD_DIR = HERE / "_sst_clim_build"
CLIM_BUILD_DIR.mkdir(exist_ok=True)
CACHE_NAME = "sst_region_daily_means.npz"
DEFAULT_CACHE_PATH = CLIM_BUILD_DIR / CACHE_NAME
# R2 object key (under the sst/ prefix so it's served from the same CDN
# path the rest of the SST products use).
R2_CACHE_KEY = f"sst/{CACHE_NAME}"

OISST_START = dt.date(1982, 1, 1)

# Baseline window for the daily climatology mean — NHC-standard 1991–2020,
# matching generate_sst_plots' CLIMO_START/END so the curve and the map
# anomalies share a baseline.
CLIMO_START = gsp.CLIMO_START   # 1991
CLIMO_END = gsp.CLIMO_END       # 2020

# Row order of the means matrix is locked to REGIONS insertion order so
# the cache stays stable across runs even if REGIONS grows.
REGION_SLUGS: list[str] = list(REGIONS.keys())

LOG = "[sst-clim]"

# A leap reference year gives a fixed 366-slot day-of-year axis so all
# years align on calendar date (March 1 is always position 61), and
# Feb 29 has a real slot that only leap years populate.
REF_YEAR = 2000


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


# --- Day-of-year helpers ------------------------------------------------

def doy_pos(d: dt.date) -> int:
    """Calendar-aligned day-of-year position, 1..366.

    Uses a fixed leap reference year so the seasonal cycle lines up across
    leap and non-leap years (unlike raw tm_yday, which shifts everything
    after Feb 28 by a day between the two)."""
    return (dt.date(REF_YEAR, d.month, d.day) - dt.date(REF_YEAR, 1, 1)).days + 1


def month_tick_positions() -> tuple[list[int], list[str]]:
    """(positions, labels) for the 1st of each month on the doy axis."""
    pos = [doy_pos(dt.date(REF_YEAR, m, 1)) for m in range(1, 13)]
    labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return pos, labels


# --- Cache I/O ----------------------------------------------------------

def load_records(path: Path) -> dict[dt.date, np.ndarray]:
    """Load the cache npz into {date: means(n_regions,)}.

    Returns {} if the cache is absent. Rows are reordered to the current
    REGION_SLUGS order so adding a region later doesn't scramble history
    (missing rows for old regions are filled with NaN)."""
    if not path.exists():
        return {}
    z = np.load(path, allow_pickle=False)
    ymd = z["dates"].astype(np.int64)
    means = z["means"].astype(np.float32)
    cached_slugs = [str(s) for s in z["regions"]]

    # Map cached rows onto the current slug order.
    row_of = {slug: i for i, slug in enumerate(cached_slugs)}
    records: dict[dt.date, np.ndarray] = {}
    for j, v in enumerate(ymd):
        v = int(v)
        d = dt.date(v // 10000, (v // 100) % 100, v % 100)
        row = np.full(len(REGION_SLUGS), np.nan, np.float32)
        for i, slug in enumerate(REGION_SLUGS):
            src = row_of.get(slug)
            if src is not None:
                row[i] = means[src, j]
        records[d] = row
    return records


def save_records(path: Path, records: dict[dt.date, np.ndarray]) -> None:
    """Serialize {date: means} to the compact npz (dates sorted ascending)."""
    dates = sorted(records)
    ymd = np.array([d.year * 10000 + d.month * 100 + d.day for d in dates],
                   dtype=np.int32)
    if dates:
        means = np.stack([records[d] for d in dates], axis=1).astype(np.float32)
    else:
        means = np.empty((len(REGION_SLUGS), 0), np.float32)
    # Write through an open handle so numpy doesn't helpfully re-append
    # ".npz" to the temp path (it does that when given a str/Path).
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        np.savez_compressed(
            f,
            dates=ymd,
            means=means,
            regions=np.array(REGION_SLUGS),
        )
    tmp.replace(path)


# --- Region-mean computation --------------------------------------------

def region_means_for_grid(sst: np.ndarray, lat: np.ndarray,
                          lon: np.ndarray) -> np.ndarray:
    """cos-lat-weighted, land-excluded mean SST per region for one grid.

    Returns float32 (n_regions,). NaN for any region whose subset is
    empty (shouldn't happen for the standard 18, but defensive)."""
    out = np.full(len(REGION_SLUGS), np.nan, np.float32)
    for i, slug in enumerate(REGION_SLUGS):
        sub, la, _lo = _subset_to_extent(sst, lat, lon, REGIONS[slug]["extent"])
        if sub.size:
            out[i] = compute_global_mean(sub, la)
    return out


def _iter_days(start: dt.date, end_exclusive: dt.date):
    d = start
    one = dt.timedelta(days=1)
    while d < end_exclusive:
        yield d
        d += one


def _fetch_days_concurrent(days: list[dt.date]) -> dict[dt.date, Path]:
    """Download a batch of OISST days in parallel; return {date: ncpath}."""
    out: dict[dt.date, Path] = {}
    with cf.ThreadPoolExecutor(max_workers=gsp.FETCH_WORKERS) as pool:
        futs = {pool.submit(gsp.fetch_day, d, LOG): d for d in days}
        for fut in cf.as_completed(futs):
            d = futs[fut]
            try:
                p = fut.result()
            except Exception:  # noqa: BLE001
                p = None
            if p is not None:
                out[d] = p
    return out


# --- Commands -----------------------------------------------------------

def cmd_backfill(cache_path: Path, start_year: int, end_year: int | None) -> int:
    """Build/extend the per-region daily-mean cache, oldest day first.

    Processes one calendar year at a time so disk stays bounded (~600 MB
    of NetCDF per year, deleted before moving on) and so we have a clean
    checkpoint after each year. Resumable: dates already cached are
    skipped; honors the --time-budget deadline between years AND between
    download batches within a year."""
    records = load_records(cache_path)
    print(f"{LOG} cache loaded: {len(records)} days already present "
          f"({cache_path})")

    today = dt.datetime.utcnow().date()
    last_year = end_year if end_year is not None else today.year
    rem = _budget_remaining_min()
    if rem is not None:
        print(f"{LOG} time budget: {rem:.1f} min")

    total_added = 0
    for year in range(start_year, last_year + 1):
        if _budget_exhausted():
            print(f"{LOG} budget exhausted before {year}; stopping cleanly.")
            break

        y_start = max(dt.date(year, 1, 1), OISST_START)
        y_end = min(dt.date(year + 1, 1, 1), today)  # exclusive; up to yesterday
        if y_start >= y_end:
            continue

        missing = [d for d in _iter_days(y_start, y_end) if d not in records]
        if not missing:
            print(f"{LOG} {year}: complete ({(y_end - y_start).days} days), skip.")
            continue

        print(f"{LOG} {year}: fetching {len(missing)} missing day(s) ...")
        added_this_year = 0
        # Sub-batch within the year so a mid-year budget stop still
        # checkpoints the partial year, and disk never holds a full year
        # of grids at once on slow links.
        BATCH = 120
        for k in range(0, len(missing), BATCH):
            if _budget_exhausted():
                print(f"{LOG} {year}: budget hit mid-year after "
                      f"{added_this_year} day(s).")
                break
            batch = missing[k:k + BATCH]
            paths = _fetch_days_concurrent(batch)
            for d in batch:
                p = paths.get(d)
                if p is None:
                    continue  # NCEI gap / 404 — leave the day out
                try:
                    sst, lat, lon = gsp.read_sst_grid(p)
                    records[d] = region_means_for_grid(sst, lat, lon)
                    added_this_year += 1
                except Exception as e:  # noqa: BLE001
                    print(f"{LOG}   read error {d}: {type(e).__name__}: {e}",
                          file=sys.stderr)
                finally:
                    # Discard the grid immediately — 16k × 1.6 MB won't fit.
                    try:
                        p.unlink()
                    except OSError:
                        pass

        if added_this_year:
            save_records(cache_path, records)  # checkpoint after each year
            total_added += added_this_year
            rem = _budget_remaining_min()
            tail = f" · {rem:.1f} min left" if rem is not None else ""
            print(f"{LOG} {year}: +{added_this_year} day(s), "
                  f"cache now {len(records)} days{tail}")

    save_records(cache_path, records)
    span = (min(records), max(records)) if records else (None, None)
    print(f"{LOG} backfill done: +{total_added} this run, "
          f"{len(records)} days total, span {span[0]}..{span[1]}")
    return 0


def cmd_update(cache_path: Path) -> int:
    """Append the newest available OISST day's region means to the cache."""
    records = load_records(cache_path)
    before = len(records)
    d, p = gsp.latest_available_day(LOG)  # cache hit if plots step ran first
    if d in records:
        print(f"{LOG} latest available {d} already in cache "
              f"({before} days); nothing to append.")
        return 0
    sst, lat, lon = gsp.read_sst_grid(p)
    records[d] = region_means_for_grid(sst, lat, lon)
    save_records(cache_path, records)
    print(f"{LOG} appended {d}; cache now {len(records)} days "
          f"(was {before}).")
    return 0


# --- CLI ----------------------------------------------------------------

def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--backfill", action="store_true",
                      help="One-time: build the full 1982-present cache "
                           "(resumable).")
    mode.add_argument("--update", action="store_true",
                      help="Daily: append the latest available day.")
    p.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH,
                   help="Cache npz path (default: %(default)s).")
    p.add_argument("--start-year", type=int, default=OISST_START.year,
                   help="Backfill start year (default: %(default)s).")
    p.add_argument("--end-year", type=int, default=None,
                   help="Backfill end year inclusive (default: current).")
    p.add_argument("--time-budget-minutes", type=float, default=None,
                   help="Stop backfill cleanly after this many minutes so "
                        "CI can save state before the 6-h hard-kill.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    _arm_budget(args.time_budget_minutes)
    if args.backfill:
        return cmd_backfill(args.cache, args.start_year, args.end_year)
    return cmd_update(args.cache)


if __name__ == "__main__":
    raise SystemExit(main())
