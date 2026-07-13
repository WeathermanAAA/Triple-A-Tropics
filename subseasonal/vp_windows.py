"""vp_windows.py — time-window machinery for the velocity-potential
anomaly product: the rolling daily-chi archive, window means, and the
real-time 20-100-day (MJO-band) Lanczos filter.

WHY AN ARCHIVE OF chi AND NOT WINDS: the Poisson solve (chi_core) is
LINEAR in (u, v) — divergence, the spectral inversion, and the T21
truncation are all linear operators. Therefore

    mean_over_window(chi_daily) == chi(mean_over_window(u, v))

exactly (test-locked in tests/test_vp_windows.py), and the same holds for
any linear filter, including the Lanczos bandpass. So the archive stores
ONE small field per day/level (T21 chi on the 1-deg grid, ~50 KB zlib'd)
and every product — pentad/30-day/90-day means, the MJO bandpass, and the
divergent-wind quiver (grad chi, also linear) — is derived from it without
refetching winds.

Archive file: NetCDF, dims (time, level, lat, lon) + ncycles(time)
(how many of the day's 00/06/12/18Z analyses went into the daily mean).
Lives in R2 (_buildcache/chi_daily_archive.nc), pulled/pushed by the
workflow around each run; the generator only sees a local path.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np

# window key -> days averaged; "mjo" is the bandpass, handled separately
MEAN_WINDOWS = {"pentad": 5, "30d": 30, "90d": 90}
MJO_BAND_DAYS = (20.0, 100.0)     # bandpass period band
MJO_NWTS = 121                    # Lanczos taps (center +/- 60 days)
MIN_DAY_FRACTION = 0.8            # a mean window must have >=80% of its days


def lanczos_bandpass_weights(nwts: int = MJO_NWTS,
                             band: tuple[float, float] = MJO_BAND_DAYS
                             ) -> np.ndarray:
    """Duchon (1979) Lanczos bandpass weights for daily data.

    band = (short_period, long_period) in days. Weights sum to ~0 (the
    mean is in the stop band). nwts must be odd.
    """
    assert nwts % 2 == 1, "nwts must be odd"
    f1 = 1.0 / band[1]           # low cutoff (cycles/day)
    f2 = 1.0 / band[0]           # high cutoff
    half = (nwts - 1) // 2
    k = np.arange(-half, half + 1, dtype=float)
    w = np.zeros(nwts)
    # central weight
    w[half] = 2.0 * (f2 - f1)
    kk = k[k != 0]
    sigma = np.sin(np.pi * kk / half) / (np.pi * kk / half)   # Lanczos taper
    w[k != 0] = ((np.sin(2 * np.pi * f2 * kk)
                  - np.sin(2 * np.pi * f1 * kk)) / (np.pi * kk)) * sigma
    return w


def bandpass_latest(anom: np.ndarray, weights: np.ndarray
                    ) -> tuple[np.ndarray, float]:
    """Filter the daily anomaly stack (time, ...) and return the LATEST
    day's filtered field plus the endpoint retention factor.

    Real-time endpoint handling (the standard operational compromise):
    the future half of the filter window is zero-padded, which damps the
    endpoint amplitude. The retention factor reported is the l1 fraction
    of filter mass that actually saw data — the caption prints it so the
    map never overstates itself.
    """
    n = anom.shape[0]
    half = (len(weights) - 1) // 2
    if n < half + 1:
        raise ValueError(f"need >= {half + 1} days of anomalies, have {n}")
    # weights aligned so index -1 (latest day) sits at the filter center;
    # the future half (k > 0) is missing -> zero-padded by omission
    usable = weights[: half + 1]                    # k = -half .. 0
    take = min(n, half + 1)
    w = usable[half + 1 - take:]
    data = anom[n - take:]
    filt = np.tensordot(w, data, axes=(0, 0))
    retained = float(np.abs(w).sum() / np.abs(weights).sum())
    return filt, retained


def window_mean(times: list[dt.date], stack: np.ndarray, days: int,
                end: dt.date) -> tuple[np.ndarray, int]:
    """Mean of the newest `days` calendar days ending at `end` (inclusive).
    Returns (mean field, n_days_used); raises if coverage < MIN_DAY_FRACTION.
    """
    start = end - dt.timedelta(days=days - 1)
    idx = [i for i, t in enumerate(times) if start <= t <= end]
    need = int(np.ceil(days * MIN_DAY_FRACTION))
    if len(idx) < need:
        raise ValueError(
            f"{days}-day window has {len(idx)} of {days} days (need {need})")
    return stack[idx].mean(axis=0), len(idx)


# ---------------------------------------------------------------- archive io

def load_archive(path: Path):
    """-> (times: list[date], levels, lats, lons, chi(time,level,lat,lon),
    ncycles) or None if absent/unreadable."""
    import xarray as xr
    if not Path(path).exists():
        return None
    try:
        ds = xr.open_dataset(path)
        times = [dt.date.fromordinal(int(o)) for o in ds.timeord.values]
        out = (times, ds.level.values.copy(), ds.lat.values.copy(),
               ds.lon.values.copy(), ds.chi.values.copy(),
               ds.ncycles.values.copy())
        ds.close()
        return out
    except Exception as e:  # noqa: BLE001 — a corrupt cache cold-starts
        print(f"archive unreadable ({e}) — starting fresh")
        return None


def save_archive(path: Path, times: list[dt.date], levels, lats, lons,
                 chi: np.ndarray, ncycles: np.ndarray) -> None:
    import xarray as xr
    order = np.argsort([t.toordinal() for t in times])
    ds = xr.Dataset(
        {"chi": (("time", "level", "lat", "lon"),
                 chi[order].astype(np.float32),
                 {"units": "m2 s-1",
                  "long_name": "daily-mean velocity potential, T21"}),
         "ncycles": (("time",), np.asarray(ncycles)[order].astype(np.int8),
                     {"long_name": "GFS analyses in the daily mean (of 4)"}),
         "timeord": (("time",),
                     np.array([times[i].toordinal() for i in order],
                              np.int32),
                     {"long_name": "proleptic Gregorian ordinal date"})},
        coords={"time": np.arange(len(times), dtype=np.int32),
                "level": np.asarray(levels, np.float32),
                "lat": np.asarray(lats, np.float32),
                "lon": np.asarray(lons, np.float32)})
    tmp = Path(str(path) + ".tmp")
    ds.to_netcdf(tmp, encoding={"chi": {"zlib": True, "complevel": 4}})
    tmp.replace(path)
