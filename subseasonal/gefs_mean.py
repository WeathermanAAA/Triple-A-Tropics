"""gefs_mean.py — GEFS ensemble-MEAN (geavg) daily-mean global winds via
Herbie/AWS, shared by the Hovmöller forecast tails and the VP forecast-
lead maps (subseasonal Phase 3 Group B).

One fetch per (init, forecast day): u AND v at 200/850 hPa ride the same
byte-ranged messages of the geavg pgrb2a 0.5-deg file, so a forecast day
costs at most four small subsets (the 00/06/12/18Z valid instants of that
calendar day; day 16 only has its 00Z instant inside GEFS's 384-h limit —
callers must surface that day's thinner sampling if they show it).
Everything returns on the global 1-degree grid (0.5 subsampled ::2),
lats +90..-90, lons 0..359 — callers align to their own archive grids by
label, never by position.

The ensemble MEAN is the point (per the Phase-3 spec): member spread is
deliberately smoothed away and every plot built from this must say so
on-plot. Fetch/decode is eccodes/cfgrib — NOT thread-safe — so the pool
helper uses a spawned ProcessPoolExecutor exactly like the generators'
analysis backfills.
"""
from __future__ import annotations

import datetime as dt
import warnings

import numpy as np

LEVELS = (200.0, 850.0)
MAX_LEAD_H = 384          # GEFS pgrb2a limit -> ~16-day forecast horizon
MAX_DAYS = 16


def day_fxxs(dd: int) -> list[int]:
    """Forecast-day dd (1-based calendar day after a 00Z init) -> the fxx
    steps of its 00/06/12/18Z valid instants that exist within 384 h."""
    return [f for f in (24 * dd, 24 * dd + 6, 24 * dd + 12, 24 * dd + 18)
            if f <= MAX_LEAD_H]


def fetch_mean_day(args):
    """((init datetime, dd)) -> (dd, date, u[2,181,360], v[2,181,360],
    nsteps) or None. Module-level for ProcessPoolExecutor(spawn)."""
    init, dd = args
    from herbie import Herbie
    warnings.filterwarnings("ignore")
    got_u, got_v = [], []
    for fxx in day_fxxs(dd):
        try:
            h = Herbie(init, model="gefs", product="atmos.5",
                       member="avg", fxx=fxx, verbose=False)
            if not h.grib:
                continue
            ds = h.xarray(":(UGRD|VGRD):(200|850) mb")
            if isinstance(ds, list):
                import xarray as xr
                ds = xr.merge(ds, compat="override")
            arrs = {}
            for var in ("u", "v"):
                sub = ds[var].sel(isobaricInhPa=list(LEVELS))
                arr = np.stack([sub.sel(isobaricInhPa=lv).values[::2, ::2]
                                for lv in LEVELS])
                arrs[var] = arr.astype(float)
            got_u.append(arrs["u"])
            got_v.append(arrs["v"])
        except Exception as e:  # noqa: BLE001 — a missing step is expected
            print(f"    gefs mean d{dd} f{fxx:03d}: {e}")
    if not got_u:
        return None
    date = (init + dt.timedelta(days=dd)).date()
    return dd, date, np.mean(got_u, axis=0), np.mean(got_v, axis=0), len(got_u)


def grid():
    """The 1-deg grid fetch_mean_day returns on: lats +90..-90, lons 0..359."""
    return np.arange(90.0, -90.1, -1.0), np.arange(0.0, 360.0, 1.0)


def newest_complete_init(now: dt.datetime | None = None) -> dt.datetime:
    """The newest 00Z GEFS cycle whose long-range files are reliably up on
    AWS (~6 h after init covers the full 384-h set with margin)."""
    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    init = dt.datetime(now.year, now.month, now.day, 0)
    if now.hour < 7:
        init -= dt.timedelta(days=1)
    return init


def _worker_path_init(path: str) -> None:
    """Spawned workers re-import this module by name — subseasonal/ must
    be on THEIR sys.path too (the parent inserted it manually)."""
    import sys
    if path not in sys.path:
        sys.path.insert(0, path)


def fetch_tail(init: dt.datetime, days: int = MAX_DAYS, workers: int = 4):
    """The whole forecast tail: (dates, u[nd,2,181,360], v[nd,...],
    nsteps[nd]) for forecast days 1..days, spawned pool. Days with no
    reachable data are dropped (a hole ends the tail at the first gap so
    the plotted tail is always contiguous)."""
    import multiprocessing as mp
    import os
    from concurrent.futures import ProcessPoolExecutor
    ctx = mp.get_context("spawn")
    here = os.path.dirname(os.path.abspath(__file__))
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx,
                             initializer=_worker_path_init,
                             initargs=(here,)) as ex:
        results = list(ex.map(fetch_mean_day,
                              [(init, dd) for dd in range(1, days + 1)],
                              chunksize=2))
    by_dd = {r[0]: r for r in results if r is not None}
    dates, us, vs, ns = [], [], [], []
    for dd in range(1, days + 1):
        if dd not in by_dd:
            break                      # contiguous tail only
        _, date, u, v, n = by_dd[dd]
        dates.append(date)
        us.append(u)
        vs.append(v)
        ns.append(n)
    if not dates:
        return [], None, None, []
    return dates, np.stack(us), np.stack(vs), ns
