"""generate_hovmollers.py — time-longitude Hovmöllers of the tropics with
Wheeler–Kiladis equatorial-wave overlays (subseasonal Phase 2).

Variables
  olr     NOAA OLR CDR v2 daily means (NOAA PSL OPeNDAP), anomalies vs the
          SAME dataset's 1991–2020 daily LTM smoothed to mean + first 3
          harmonics (Wheeler & Weickmann 2001) MINUS the previous-120-day
          running mean per longitude (the WH04 step-2 treatment — removes
          ENSO/low-frequency variability so the intraseasonal signal
          carries the panel). Shading = the unfiltered anomaly (blue =
          negative = enhanced convection); contours = space-time-filtered
          anomalies (subseasonal/wk_filter.py) for the MJO band (default)
          with Kelvin as the only optional second mode, drawn at a 1-std
          interval of each filtered field over the displayed window,
          filtered on the newest 365 days zero-padded to 1024 (WW01
          real-time method — the last ~2 weeks are amplitude-damped and
          every plot says so). The FORECAST half is MJO-reconstructed
          OLR from each model's ensemble-mean RMM forecast (the
          mjo_fc_pcs*.json files generate_mjo_rmm writes earlier in the
          same job, inverted by subseasonal/mjo_reconstruct) — never raw
          ensemble-mean OLR, whose phase-incoherent member averaging
          collapses the wave.
          NOTE: the classic PSL *interpolated* OLR (olr.day.mean.nc) has
          not updated since 2022 — the CDR v2 daily product is the
          PSL-hosted operational equivalent (1 deg, ~3-5 day lag).
  u850 / u200
          GFS 1-deg analyses (daily means of the 00/06/12/18Z cycles via
          Herbie, AWS-first), kept in a small rolling archive in R2
          exactly like the chi product's; anomalies vs the committed ERA5
          1991–2020 monthly u climatology (subseasonal/u_climo_1991_2020.nc,
          build_u_climatology.py — like-vs-modern-like, the chi
          precedent). Red = westerly anomaly.
  chi200  The chi product's own daily T21 archive (chi_daily_archive.nc,
          restored from R2 by the workflow before this script runs),
          anomalies vs subseasonal/chi_climo_1991_2020.nc. Green =
          negative chi' = anomalous upper-level divergence.

TC-genesis markers on every panel: the first tcvitals entry of each
DESIGNATED system (ATCF number 01-49; invests 90-99 never mark) from the
UCAR combined tcvitals archive, plotted at (genesis longitude, date) with
the storm's name when |genesis lat| <= 25.

The render matrix is driven by the selector framework on /subseasonal/:
variable x wave (OLR only) x band x days x region, one PNG each, plus
hov_meta.json naming what rendered and each variable's data currency.

Outputs: subseasonal/out/hov/*.png + subseasonal/out/hov_meta.json
Usage:   python generate_hovmollers.py [--out DIR]
             [--u-archive PATH] [--u-backfill-days N] [--u-target-depth N]
             [--chi-archive PATH] [--skip-olr] [--skip-u] [--skip-chi]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sys
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "subseasonal"))
import gefs_mean  # noqa: E402
import wk_filter  # noqa: E402

# ------------------------------------------------------------------ config

PSL = "https://psl.noaa.gov/thredds/dodsC/Datasets/olr_cdr_day/"
OLR_URL = PSL + "olr.day.mean.v2.nc"
OLR_LTM_URL = PSL + "olr.day.ltm.v2.1991-2020.nc"
VITALS_URL = ("https://hurricanes.ral.ucar.edu/repository/data/"
              "tcvitals_open/combined_tcvitals.{year}.dat")

U_CLIMO_NC = HERE / "subseasonal" / "u_climo_1991_2020.nc"
CHI_CLIMO_NC = HERE / "subseasonal" / "chi_climo_1991_2020.nc"

U_LEVELS = (200.0, 850.0)
U_LATS = np.arange(-22.0, 22.1, 2.0)      # GFS 1p00 rows kept in the archive
MIN_CYCLES_PER_DAY = 2

FETCH_DAYS = 490          # OLR pull depth (365-day filter window + the
                          # 120-day running-mean spin-up + slack)
FILTER_DAYS = 365         # WW01 real-time window
PAD_TO = 1024             # zero-pad length for the space-time filter

BANDS = {                  # key -> (lat_lo, lat_hi, label)
    "eq":   (-7.5, 7.5, "7.5°S–7.5°N"),
    "trop": (-15.0, 15.0, "15°S–15°N"),
    "nh":   (0.0, 15.0, "0°–15°N"),
    "sh":   (-15.0, 0.0, "15°S–0°"),
}
DAYS = (60, 120, 180)
REGIONS = {                # key -> (lon_lo, lon_hi, label)
    "glob": (0.0, 360.0, "Global"),
    "ipac": (40.0, 200.0, "Indo-Pacific"),
}
WAVE_SETS = {              # wave-overlay selector -> modes drawn (u/v/chi)
    "all": ["mjo", "kelvin", "er", "mrg_td"],
    "mjo": ["mjo"], "kelvin": ["kelvin"], "er": ["er"],
    "mrgtd": ["mrg_td"], "mrgtd_er": ["mrg_td", "er"],
    "lowfreq": ["lowfreq"], "none": [],
}
# The OLR panel is the MJO diagnostic: MJO contours by default, Kelvin as
# the only optional second mode ("all" is retired there; the wind/chi
# panels keep the full WAVE_SETS matrix). The retired OLR wave keys stay
# resolvable for one release cycle via copies of the "mjo" panel, so a
# cached page running the previous JS/meta keeps loading images.
OLR_WAVE_SETS = {
    "mjo": ["mjo"], "kelvin": ["kelvin"],
    "mjo+kelvin": ["mjo", "kelvin"], "none": [],
}
OLR_WAVE_COMPAT = ("all", "er", "mrgtd", "mrgtd_er", "lowfreq")
WAVE_STYLE = {             # mode -> (label, color) on the dark canvas
    # MJO draws CHARCOAL: near-white contours washed out on the light OLR
    # shading; charcoal reads on both the warm and cool fills and stays
    # distinct from Kelvin/ER/MRG-TD. Its legend chip gets a light stroke
    # so the dark label survives the dark header.
    "mjo":    ("MJO", "#262c34"),
    "kelvin": ("Kelvin", "#56c8ff"),
    "er":     ("ER", "#ffb83a"),
    "mrg_td": ("MRG–TD", "#ff7a8a"),
    "lowfreq": ("Low-freq", "#b18ce8"),
}
# wave-contour levels are +-(1..4) x wave_step, so each variable's
# contours scale with its own shading step (OLR keeps its historic
# +-10/20/30/40 W m^-2 via step 10; u
# gets +-2..8 m s^-1; chi +-step..4*step x 1e6 m^2 s^-1)
WAVE_CLEV_MULTS = (1.0, 2.0, 3.0, 4.0)

BG = "#07101c"
TEXT = "#e5edf6"
MUTED = "#8ea2bd"
GRID = "#22304a"
WATERMARK = "@WeathermanAAA_"


# ------------------------------------------------------------------- utils

def _open_dods(url):
    import xarray as xr
    warnings.filterwarnings("ignore")
    return xr.open_dataset(url, decode_times=True)


def _load_slabbed(da, step: int = 60):
    """Load a (time, ...) DataArray in <=step-day slabs. PSL's THREDDS
    silently returns ALL-ZERO data for large multi-timestep DAP subsets
    of the LTM aggregation (verified 2026-07-15: the full 365-step read
    is 0.0 everywhere while any <=60-step slab is correct) — never load
    a long time axis in one request."""
    import xarray as xr
    nt = da.sizes["time"]
    parts = [da.isel(time=slice(i, min(i + step, nt))).load()
             for i in range(0, nt, step)]
    return xr.concat(parts, dim="time") if len(parts) > 1 else parts[0]


def _guard_degenerate(name: str, arr: np.ndarray, min_expected_max: float):
    """Fail LOUDLY if a DAP read came back degenerate (all zero/NaN) —
    the silent-zeros THREDDS failure mode must never render."""
    mx = float(np.nanmax(np.abs(arr))) if arr.size else 0.0
    if not np.isfinite(mx) or mx < min_expected_max:
        raise RuntimeError(
            f"{name}: degenerate values from DAP (max |x| = {mx}); "
            "refusing to render from corrupt data")


def smooth_climo_3harm(ltm: np.ndarray) -> np.ndarray:
    """WW01 seasonal cycle: annual mean + first 3 harmonics of the daily
    LTM along axis 0 (365 days). Returns the smoothed (365, ...) array."""
    F = np.fft.rfft(ltm, axis=0)
    F[4:] = 0.0                       # keep mean + harmonics 1..3
    return np.fft.irfft(F, n=ltm.shape[0], axis=0)


def band_mean(arr: np.ndarray, lats: np.ndarray, lo: float, hi: float
              ) -> np.ndarray:
    """cos(lat)-weighted mean of arr(..., lat, lon) over lo<=lat<=hi
    -> arr(..., lon)."""
    sel = (lats >= lo) & (lats <= hi)
    w = np.cos(np.deg2rad(lats[sel]))
    sub = arr[..., sel, :]
    return (sub * w[:, None]).sum(axis=-2) / w.sum()


def monthly_climo_for(ds, var: str, date: dt.date, level: float,
                      lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Month-center-interpolated monthly climatology regridded to
    (lats, lons) — the chi product's climo_chi_for, generalized."""
    import xarray as xr
    doy = date.timetuple().tm_yday
    year = date.year
    centers = []
    for m in range(1, 13):
        d0 = dt.date(year, m, 1)
        d1 = (dt.date(year + (m == 12), (m % 12) + 1, 1)
              - dt.timedelta(days=1))
        centers.append((d0.timetuple().tm_yday + d1.timetuple().tm_yday) / 2)
    centers = np.array(centers)
    if doy <= centers[0] or doy >= centers[-1]:
        m0, m1 = 12, 1
        span = 365 - centers[-1] + centers[0]
        w = ((doy - centers[-1]) % 365) / span
    else:
        m1 = int(np.searchsorted(centers, doy)) + 1
        m0 = m1 - 1
        w = (doy - centers[m0 - 1]) / (centers[m1 - 1] - centers[m0 - 1])
    c0 = ds[var].sel(level=level, month=m0)
    c1 = ds[var].sel(level=level, month=m1)
    blend = (1 - w) * c0 + w * c1
    wrap = blend.isel(lon=0).assign_coords(lon=float(blend.lon[0]) + 360.0)
    blend = xr.concat([blend, wrap], dim="lon")
    out = blend.interp(lat=np.clip(lats, float(blend.lat.min()),
                                   float(blend.lat.max())),
                       lon=lons, method="linear")
    return out.values


def filter_realtime(anom: np.ndarray, mode: str, obs_per_day: float = 1.0
                    ) -> np.ndarray:
    """WW01 real-time filtering: zero-pad the (time, lon) anomaly series
    out to PAD_TO days, filter, return the original span."""
    nt = anom.shape[0]
    padded = np.zeros((PAD_TO, anom.shape[1]), anom.dtype)
    padded[:nt] = anom
    filt = wk_filter.filter_mode(padded, obs_per_day, mode)
    return filt[:nt]


def _fill_for_filter(fb: np.ndarray) -> np.ndarray:
    """Finite copy of a (time, lon) band-mean series for the FFT filter:
    interior NaN days linearly interpolated per longitude, anything
    outside the observed span zeroed (zero anomalies add no variance —
    the WW01 pad is zeros anyway). The SHADING keeps its honest NaN
    gaps; only the filter input is filled."""
    out = fb.astype(float, copy=True)
    nt = out.shape[0]
    x = np.arange(nt, dtype=float)
    for j in range(out.shape[1]):
        col = out[:, j]
        good = np.isfinite(col)
        ngood = int(good.sum())
        if ngood == 0:
            out[:, j] = 0.0
            continue
        if ngood < nt:
            filled = np.interp(x, x[good], col[good])
            first = int(np.argmax(good))
            last = nt - 1 - int(np.argmax(good[::-1]))
            filled[:first] = 0.0
            filled[last + 1:] = 0.0
            out[:, j] = filled
    return out


def wave_filts(bm_full: np.ndarray, nf: int) -> dict:
    """Per-mode WW01 real-time filtered fields for ONE band-mean series
    (time, lon): filter the newest `nf` days after gap-filling. Returns
    {mode: (nf, lon) array} for every mode in WAVE_STYLE."""
    fb = _fill_for_filter(bm_full[-nf:])
    return {m: filter_realtime(fb, m) for m in WAVE_STYLE}


def fetch_olr_ltm_rmm() -> np.ndarray:
    """The CDR daily LTM seasonal cycle (mean + first 3 harmonics) as
    15S-15N cos-weighted means on the 144 RMM longitudes -> (365, 144).
    Feeds the GEFS RMM forecast (generate_mjo_rmm) with the IDENTICAL
    seasonal cycle the obs-side OLR anomalies use."""
    ltm_ds = _open_dods(OLR_LTM_URL)
    sub = ltm_ds.olr.sel(lat=slice(-15, 15))
    ltm = _load_slabbed(sub).values.astype(float)
    lats = sub.lat.values.astype(float)
    lons = sub.lon.values.astype(float)
    ltm_ds.close()
    _guard_degenerate("OLR LTM (RMM band)", ltm, 150.0)
    bm = band_mean(smooth_climo_3harm(ltm), lats, -15, 15)   # (365, lon)
    rmm_lons = np.arange(0.0, 360.0, 2.5)
    ext_l = np.concatenate([lons, lons[:1] + 360.0])
    out = np.empty((365, rmm_lons.size))
    for t in range(365):
        ext = np.concatenate([bm[t], bm[t][:1]])
        out[t] = np.interp(rmm_lons, ext_l, ext)
    return out


# ---------------------------------------------- GEFS ensemble-mean tail

def load_gefs_tail(path: Path):
    """(init, dates, u[nd,2,glat,glon], v, nsteps) from the tail the VP
    generator saved earlier in this same workflow run, or None (missing,
    unreadable, or from an old init)."""
    import xarray as xr
    if not Path(path).exists():
        return None
    try:
        ds = xr.open_dataset(path)
        init = dt.datetime.strptime(ds.attrs["init"], "%Y-%m-%dT%H:%M:%SZ")
        dates = [dt.date.fromordinal(int(o)) for o in ds.timeord.values]
        out = (init, dates, ds.u.values.copy(), ds.v.values.copy(),
               [int(n) for n in ds.nsteps.values])
        ds.close()
    except Exception as e:  # noqa: BLE001 — a corrupt cache refetches
        print(f"gefs tail cache unreadable ({e})")
        return None
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if (now - init).total_seconds() > 2 * 86400:
        return None
    return out


def get_gefs_tail(out: Path):
    """The GEFS ensemble-mean forecast tail: reuse the VP product's fetch
    (gefs_tail.nc in the shared out dir) or fetch fresh; None on failure
    (the Hovmöllers then render analysis-only, exactly as before)."""
    t = load_gefs_tail(out / "gefs_tail.nc")
    if t:
        print(f"gefs tail: reusing VP fetch ({len(t[1])} day(s), "
              f"init {t[0]:%Y-%m-%d} 00Z)")
        return t
    try:
        init = gefs_mean.newest_complete_init()
        dates, u, v, ns = gefs_mean.fetch_tail(init)
        if dates:
            print(f"gefs tail: fetched {len(dates)} day(s), "
                  f"init {init:%Y-%m-%d} 00Z")
            return init, dates, u, v, ns
    except Exception as e:  # noqa: BLE001 — the forecast layer is additive
        print(f"gefs tail fetch failed ({type(e).__name__}: {e})")
    return None


# per-model init-line label pieces: (lead-in, smoothing wording, limit).
# gefs reproduces the original strings byte-for-byte; ifs is a single run
# so "ensemble-mean-smoothed" would be dishonest there.
_FC_NOTE = {
    "gefs": ("GEFS ensemble mean", "ensemble-mean-smoothed",
             "~16-day limit"),
    "ifs": ("ECMWF IFS (high-res run)", "single run",
            "~15-day limit"),
    "ens": ("ECMWF ENS mean", "ensemble-mean-smoothed",
            "~15-day limit"),
}


def gefs_fc_note(init: dt.datetime, nsteps: list,
                 to_date=None, model: str = "gefs") -> str:
    """The on-plot init-line label (init + valid-to + smoothing + limit,
    per the Phase-3 forecast-header spec)."""
    lead, smooth, limit = _FC_NOTE[model]
    note = (lead
            + (f", valid to {to_date:%Y-%m-%d}" if to_date else "")
            + f" · init {init:%Y-%m-%d} 00Z · "
            f"{smooth} · {limit}")
    if nsteps and nsteps[-1] == 1:
        note += " · final day 00Z only"
    return note


# ------------------------------------------------------------------ OLR

def fetch_olr():
    """-> (dates, lats, lons, anom) for the newest FETCH_DAYS days over
    20S-20N, seasonal cycle (LTM mean + 3 harmonics) removed."""
    ds = _open_dods(OLR_URL)
    sub = ds.olr.isel(time=slice(-FETCH_DAYS, None)).sel(lat=slice(-20, 20))
    sub = _load_slabbed(sub)
    dates = [dt.date(int(str(t)[:4]), int(str(t)[5:7]), int(str(t)[8:10]))
             for t in sub.time.values.astype("datetime64[D]")]
    lats = sub.lat.values.astype(float)
    lons = sub.lon.values.astype(float)
    raw = sub.values.astype(float)
    ds.close()
    _guard_degenerate("OLR daily", raw, 150.0)

    ltm_ds = _open_dods(OLR_LTM_URL)
    ltm = _load_slabbed(
        ltm_ds.olr.sel(lat=slice(-20, 20))).values.astype(float)
    ltm_ds.close()
    _guard_degenerate("OLR LTM", ltm, 150.0)
    ltm_sm = smooth_climo_3harm(ltm)                     # (365, lat, lon)

    doys = np.array([min(d.timetuple().tm_yday, 365) for d in dates])
    anom = raw - ltm_sm[doys - 1]

    # the space-time filter needs a continuous daily series: fill any short
    # interior gaps by time interpolation (and say so in the meta)
    good = np.isfinite(anom).all(axis=(1, 2))
    n_bad = int((~good).sum())
    if n_bad:
        idx = np.arange(len(dates))
        for j in range(anom.shape[1]):
            for k in range(anom.shape[2]):
                col = anom[:, j, k]
                bad = ~np.isfinite(col)
                if bad.any():
                    col[bad] = np.interp(idx[bad], idx[~bad], col[~bad])
    return dates, lats, lons, anom, n_bad


# ------------------------------------------------- GFS u archive (like chi)
# Archive tuple: (times, levels, lats, lons, u, v, ncycles). The v field
# was added for the v850 Hovmöller; archives written before it load with
# v = all-NaN so old days simply read "archive still building" while the
# rolling backfill fills v forward — no cold restart of the u history.

def load_u_archive(path: Path):
    import xarray as xr
    if not Path(path).exists():
        return None
    try:
        ds = xr.open_dataset(path)
        times = [dt.date.fromordinal(int(o)) for o in ds.timeord.values]
        v = (ds.v.values.copy() if "v" in ds
             else np.full(ds.u.shape, np.nan, np.float32))
        out = (times, ds.level.values.copy(), ds.lat.values.copy(),
               ds.lon.values.copy(), ds.u.values.copy(), v,
               ds.ncycles.values.copy())
        ds.close()
        return out
    except Exception as e:  # noqa: BLE001 — a corrupt cache cold-starts
        print(f"u archive unreadable ({e}) — starting fresh")
        return None


def save_u_archive(path: Path, times, levels, lats, lons, u, v, ncycles):
    import xarray as xr
    order = np.argsort([t.toordinal() for t in times])
    ds = xr.Dataset(
        {"u": (("time", "level", "lat", "lon"),
               u[order].astype(np.float32),
               {"units": "m s-1",
                "long_name": "daily-mean zonal wind, GFS 1p00 analyses"}),
         "v": (("time", "level", "lat", "lon"),
               v[order].astype(np.float32),
               {"units": "m s-1",
                "long_name": "daily-mean meridional wind, GFS 1p00 "
                             "analyses"}),
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
    ds.to_netcdf(tmp, encoding={"u": {"zlib": True, "complevel": 4},
                                "v": {"zlib": True, "complevel": 4}})
    tmp.replace(path)


def fetch_day_u(day: dt.date):
    """Daily-mean u AND v at U_LEVELS over the U_LATS rows ->
    (u[level,lat,lon], v[level,lat,lon], ncycles) or None. Herbie GFS
    1p00, AWS-first, byte-range subsets."""
    from herbie import Herbie
    warnings.filterwarnings("ignore")
    got_u, got_v = [], []
    for hour in (0, 6, 12, 18):
        t = dt.datetime(day.year, day.month, day.day, hour)
        if t > dt.datetime.now(dt.timezone.utc).replace(tzinfo=None):
            continue
        try:
            h = Herbie(t, model="gfs", product="pgrb2.1p00", fxx=0,
                       verbose=False)
            if not h.grib:
                continue
            ds = h.xarray(":(UGRD|VGRD):(200|850) mb")
            if isinstance(ds, list):
                import xarray as xr
                ds = xr.merge(ds, compat="override")
            arrs = []
            for var in ("u", "v"):
                sub = ds[var].sel(latitude=U_LATS,
                                  isobaricInhPa=list(U_LEVELS))
                # (level, lat, lon), level order pinned to U_LEVELS
                arrs.append(np.stack([sub.sel(isobaricInhPa=lv).values
                                      for lv in U_LEVELS]).astype(float))
            got_u.append(arrs[0])
            got_v.append(arrs[1])
        except Exception as e:  # noqa: BLE001 — a missing cycle is expected
            print(f"    u cycle {t:%Y-%m-%d %HZ}: {e}")
    if len(got_u) < MIN_CYCLES_PER_DAY:
        print(f"  u {day}: only {len(got_u)} cycles — skipped")
        return None
    print(f"  u {day}: {len(got_u)} cycles ok")
    return np.mean(got_u, axis=0), np.mean(got_v, axis=0), len(got_u)


def backfill_u(archive, target_depth: int, max_fetch: int, workers: int = 4):
    """Same shape as the chi product's backfill: newest missing days first,
    spawned process pool (eccodes/cfgrib are not thread-safe)."""
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor

    today = dt.datetime.now(dt.timezone.utc).date()
    wanted = [today - dt.timedelta(days=i) for i in range(target_depth + 1)]
    if archive is None:
        times, levels = [], np.array(U_LEVELS, float)
        lats, lons = U_LATS.copy(), np.arange(0.0, 360.0, 1.0)
        u = v = None
        ncyc = np.zeros(0, np.int8)
    else:
        times, levels, lats, lons, u, v, ncyc = archive
    have = set(times)
    missing = [d for d in wanted if d not in have][:max_fetch]
    if not missing:
        print("u archive complete — no backfill needed")
        return times, levels, lats, lons, u, v, ncyc
    print(f"u backfill: {len(missing)} day(s), {workers} workers")
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
        results = list(ex.map(fetch_day_u, missing, chunksize=4))
    for day, r in zip(missing, results):
        if r is None:
            continue
        day_u, day_v, n = r
        if u is None:
            u = day_u[None]
            v = day_v[None]
            times = [day]
            ncyc = np.array([n], np.int8)
        else:
            u = np.concatenate([u, day_u[None]], axis=0)
            v = np.concatenate([v, day_v[None]], axis=0)
            times.append(day)
            ncyc = np.append(ncyc, np.int8(n))
    if u is None:
        raise RuntimeError("no GFS analyses reachable at all")
    keep = [i for i, t in enumerate(times)
            if (today - t).days <= max(400, target_depth)]
    order = sorted(keep, key=lambda i: times[i])
    return ([times[i] for i in order], levels, lats, lons,
            u[order], v[order], ncyc[order])


# ------------------------------------------------------------- tcvitals

_VITALS_RE = re.compile(
    r"^\S+\s+(\d{2})([A-Z])\s+(\S+)\s+(\d{8})\s+(\d{4})\s+"
    r"(\d{2,3})([NS])\s+(\d{3,4})([EW])")


def parse_vitals_line(line: str):
    """One tcvitals row -> (id, name, datetime, lat, lon0_360) or None."""
    m = _VITALS_RE.match(line)
    if not m:
        return None
    num = int(m.group(1))
    sid = f"{m.group(1)}{m.group(2)}"
    name = m.group(3)
    try:
        when = dt.datetime.strptime(m.group(4) + m.group(5), "%Y%m%d%H%M")
    except ValueError:
        return None
    lat = int(m.group(6)) / 10.0 * (1 if m.group(7) == "N" else -1)
    lon = int(m.group(8)) / 10.0 * (1 if m.group(9) == "E" else -1)
    return num, sid, name, when, lat, lon % 360.0


def fetch_genesis(start: dt.date, end: dt.date):
    """Earliest tcvitals fix of each DESIGNATED system (number 01-49)
    inside [start, end], |genesis lat| <= 25 -> [{date, lon, name, id}].

    Systems are keyed by ATCF id, NOT (id, name): a storm's vitals name
    evolves (INVEST -> number-word -> assigned name) and a name-keyed
    dict marks the same system two or three times. Rows within 60 days
    of a known system collapse onto it (ids recycle across seasons);
    the marker sits at the EARLIEST fix and the label wears the LATEST
    name, so a never-named depression stays e.g. TEN."""
    import urllib.request
    systems: dict[str, list] = {}
    for year in sorted({start.year, end.year}):
        url = VITALS_URL.format(year=year)
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                text = r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001 — markers are optional
            print(f"tcvitals {year}: {e} — genesis markers skipped")
            continue
        for line in text.splitlines():
            p = parse_vitals_line(line)
            if p is None:
                continue
            num, sid, name, when, lat, lon = p
            if not (1 <= num <= 49):
                continue                    # invests (90-99) never mark
            for rec in systems.setdefault(sid, []):
                if abs((when - rec["first"]).days) <= 60:
                    if when < rec["first"]:
                        rec["first"], rec["lat"], rec["lon"] = when, lat, lon
                    if when >= rec["latest"]:
                        rec["latest"], rec["name"] = when, name
                    break
            else:
                systems[sid].append({"first": when, "latest": when,
                                     "lat": lat, "lon": lon, "name": name})
    out = []
    for sid, recs in systems.items():
        for rec in recs:
            d = rec["first"].date()
            # 30N cutoff (was 25N) so tropical NATL/Gulf genesis — which forms
            # 5-10 deg poleward of the WPAC/EPAC main development regions
            # (e.g. Arthur/Two 2026 at ~27N) — is included; clearly subtropical
            # / extratropical lows (Fernand 36N, Karen 44N) are still excluded.
            if start <= d <= end and abs(rec["lat"]) <= 30.0:
                out.append({"date": d, "lon": rec["lon"], "lat": rec["lat"],
                            "name": rec["name"], "id": sid})
    out.sort(key=lambda g: g["date"])
    return out


# ------------------------------------------------------------- rendering

def render_hov(field, dates, lons, *, cmap, vmax, step, cb_label,
               title, band_label, note_a, note_b, credit, overlays,
               genesis, region, out_png: Path, wave_step: float = 10.0,
               sigma_contours: bool = False,
               fc_start=None, fc_note: str = "", fc_label: str = ""):
    """One time-longitude panel: shading + wave contours + genesis marks.
    field is (time, lon) newest-last; time runs DOWN the page.

    Explicit figure geometry (no tight_layout): a three-row header
    (title | band+region, note_a | wave legend, note_b) and a footer row
    (colorbar with its label BESIDE the bar, credits + watermark below)
    so nothing can collide however long the notes get."""
    lon_lo, lon_hi, region_label = REGIONS[region]
    t = mdates.date2num([dt.datetime(d.year, d.month, d.day) for d in dates])

    fig = plt.figure(figsize=(9.6, 12.0), facecolor=BG)
    ax = fig.add_axes([0.085, 0.115, 0.885, 0.785])
    ax.set_facecolor(BG)
    levels = np.arange(-vmax, vmax + step / 2, step)
    cf = ax.contourf(lons, t, field, levels=levels, cmap=cmap,
                     extend="both")
    # multi-mode views ("all waves") draw every mode at once - full-weight
    # solid+dashed contours turn into spaghetti over the shading, so there
    # they thin to enhanced-only, hairline, translucent; the OLR shading is
    # the primary read. Single/dual-mode views keep both signs full weight.
    thin = len(overlays) > 2
    for mode, filt in overlays:
        label, color = WAVE_STYLE[mode]
        if sigma_contours:
            # MJO-diagnostics spec (the OLR panel): contour interval =
            # 1 std of THIS filtered field over the displayed window
            # (rows shown x the region's longitudes), levels at +-1..4
            # std; positive SOLID / negative DASHED (standard convention
            # - the negative, enhanced-convection side is dashed here).
            sel = (lons >= lon_lo) & (lons <= lon_hi)
            sig = float(np.nanstd(filt[:, sel])) if sel.any() else 0.0
            if not np.isfinite(sig) or sig <= 0.0:
                continue
            ax.contour(lons, t, filt,
                       levels=[m * sig for m in WAVE_CLEV_MULTS],
                       colors=color, linewidths=1.4, linestyles="solid")
            ax.contour(lons, t, filt,
                       levels=[-m * sig for m in
                               reversed(WAVE_CLEV_MULTS)],
                       colors=color, linewidths=1.0, linestyles="dashed",
                       alpha=0.75)
            continue
        neg = [-m * wave_step for m in reversed(WAVE_CLEV_MULTS)]
        pos = [m * wave_step for m in WAVE_CLEV_MULTS]
        ax.contour(lons, t, filt, levels=neg, colors=color,
                   linewidths=0.7 if thin else 1.4, linestyles="solid",
                   alpha=0.55 if thin else 1.0)
        if not thin:
            ax.contour(lons, t, filt, levels=pos, colors=color,
                       linewidths=1.0, linestyles="dashed", alpha=0.75)

    # genesis markers + decluttered labels. Markers: a bright dot with a dark
    # ring + a dark outer halo so they read on BOTH the tan (suppressed) and
    # teal (enhanced) shading. Labels: edge-aware anchoring (never clip a
    # panel edge) + a greedy vertical stagger with leader lines so no two
    # collide.
    gvis = []
    for g in genesis:
        if not (lon_lo <= g["lon"] <= lon_hi):
            continue
        gy = mdates.date2num(dt.datetime(g["date"].year, g["date"].month,
                                         g["date"].day))
        if gy < t[0] or gy > t[-1]:
            continue
        gvis.append((g, gy))
    xspan = (lon_hi - lon_lo) or 1.0
    yspan = (t[0] - t[-1]) or 1.0                 # note: y-axis is inverted
    placed = []                                    # (xn, label_yn) already set
    for g, gy in sorted(gvis, key=lambda z: z[1]):
        xn = (g["lon"] - lon_lo) / xspan
        yn = (gy - t[-1]) / yspan
        # dark halo ring, then a bright dot with a dark edge (high contrast)
        ax.plot(g["lon"], gy, marker="o", ms=9.5, mfc="none",
                mec="#0a0d12", mew=3.0, zorder=6)
        ax.plot(g["lon"], gy, marker="o", ms=6.5, mfc="#f2f6fb",
                mec="#0a0d12", mew=1.3, zorder=7)
        # label placement: anchor away from the near edge, then stagger down
        right = xn > 0.8
        ha = "right" if right else "left"
        lab_xn = xn + (-0.012 if right else 0.012)
        lab_yn = yn + 0.014
        for _i in range(8):
            clash = any(abs(lab_xn - px) < 0.14 and abs(lab_yn - py) < 0.022
                        for px, py in placed)
            if not clash:
                break
            lab_yn += 0.024
        lab_yn = min(0.985, max(0.015, lab_yn))
        placed.append((lab_xn, lab_yn))
        lab_lon = lon_lo + lab_xn * xspan
        lab_gy = t[-1] + lab_yn * yspan
        leader = abs(lab_yn - yn) > 0.02
        ax.annotate(
            g["name"], xy=(g["lon"], gy), xytext=(lab_lon, lab_gy),
            textcoords="data", color=TEXT, fontsize=7.5, fontweight="bold",
            ha=ha, va="center", zorder=8,
            arrowprops=(dict(arrowstyle="-", color=MUTED, lw=0.6, alpha=0.8,
                             shrinkA=0, shrinkB=3) if leader else None),
            path_effects=[matplotlib.patheffects.withStroke(
                linewidth=2.4, foreground="#0a0d12")])

    # init line: analysis above, forecast tail below (time runs DOWN the
    # page). ONE bold SOLID line, tagged "<MODEL> init · YYYY-MM-DD" by
    # the caller via fc_label - the full forecast sentence lives in the
    # figure footer, never floating over the data.
    if fc_start is not None:
        yline = mdates.date2num(dt.datetime(fc_start.year, fc_start.month,
                                            fc_start.day)) - 0.5
        ax.axhline(yline, color=TEXT, lw=1.6, ls="solid", zorder=5)
        if fc_label:
            ax.text(lon_lo + 0.995 * (lon_hi - lon_lo), yline,
                    fc_label + "  ", color=TEXT, fontsize=8,
                    fontweight="bold", ha="right", va="top", zorder=6,
                    path_effects=[matplotlib.patheffects.withStroke(
                        linewidth=2.2, foreground="#10131a")])

    ax.set_xlim(lon_lo, lon_hi)
    ax.set_ylim(t[-1], t[0])                     # newest at the BOTTOM
    xticks = np.arange(np.ceil(lon_lo / 30) * 30, lon_hi + 1, 30)
    ax.set_xticks(xticks)
    ax.set_xticklabels([f"{int(x % 360)}°E" if x % 360 <= 180
                        else f"{int(360 - x % 360)}°W" for x in xticks])
    ax.yaxis.set_major_locator(mdates.AutoDateLocator(minticks=6,
                                                      maxticks=12))
    ax.yaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.tick_params(colors=MUTED, labelsize=9)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.grid(color=GRID, lw=0.4, alpha=0.5)

    ax.text(0.0, 1.058, title, transform=ax.transAxes, color=TEXT,
            fontsize=13, fontweight="bold")
    ax.text(1.0, 1.058, f"{band_label} · {region_label}",
            transform=ax.transAxes, color=MUTED, fontsize=10,
            fontweight="bold", ha="right")
    ax.text(0.0, 1.030, note_a, transform=ax.transAxes, color=MUTED,
            fontsize=8.5)
    ax.text(0.0, 1.008, note_b, transform=ax.transAxes, color=MUTED,
            fontsize=8.5)
    if overlays:
        x = 1.0
        for mode, _ in reversed(overlays):
            label, color = WAVE_STYLE[mode]
            ax.text(x, 1.030, label, transform=ax.transAxes, color=color,
                    fontsize=9, fontweight="bold", ha="right",
                    path_effects=([matplotlib.patheffects.withStroke(
                        linewidth=2.2, foreground="#c9d6e6")]
                        if mode == "mjo" else None))
            x -= 0.028 + 0.0115 * len(label)
        ax.text(x, 1.030, "waves:", transform=ax.transAxes, color=MUTED,
                fontsize=8.5, ha="right")

    # bar shortened 0.58 -> 0.52 so the longest label ("OLR anomaly
    # (W m-2), blue = enhanced convection") ends ~0.95, inside the figure
    # (pixel-probed at dpi 140); the old 0.685 start clipped its tail
    cax = fig.add_axes([0.085, 0.058, 0.52, 0.012])
    cb = fig.colorbar(cf, cax=cax, orientation="horizontal")
    cb.ax.tick_params(colors=MUTED, labelsize=8)
    cb.outline.set_edgecolor(GRID)
    fig.text(0.625, 0.064, cb_label, color=MUTED, fontsize=8.5,
             va="center")
    if fc_note:
        fig.text(0.085, 0.026, "forecast: " + fc_note, color=MUTED,
                 fontsize=8)
    fig.text(0.085, 0.010, credit + " · rendered by Triple-A-Tropics",
             color=MUTED, alpha=0.9, fontsize=7.5)
    fig.text(0.970, 0.013, WATERMARK, color=MUTED, alpha=0.9,
             fontsize=7.5, ha="right")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=140, facecolor=BG)
    plt.close(fig)


def region_days_slices(dates, days):
    """Newest `days` slice of a daily date list -> (dates_sub, index)."""
    n = min(len(dates), days)
    return dates[-n:], slice(len(dates) - n, len(dates))


# ------------------------------------------------------------- sections

# per-model OLR display strings: (title short-name, credit source piece).
_OLR_FC_LABEL = {
    # short name kept title-length-safe (the fixed band label sits at the
    # title's right); the init-line note + credit carry the full source.
    # Credit pieces are length-budgeted: the whole credit line must end
    # left of the watermark (pixel-probed at dpi 140) — don't lengthen.
    "gefs": ("GEFS", "NCEP GEFS (RMM fcst)"),
    "ifs": ("IFS", "ECMWF IFS RMM fcst (CC BY 4.0)"),
    "ens": ("ENS", "ECMWF ENS RMM fcst (CC BY 4.0)"),
}
# forecast-half provenance per model: (init-line tag, fc_note wording).
# The forecast half is MJO-RECONSTRUCTED OLR from the model's mean RMM
# forecast - said exactly, per model honestly (IFS is a single run).
_FC_RECON = {
    "gefs": ("GEFS", "GEFS ensemble-mean RMM"),
    "ifs": ("ECMWF-IFS", "ECMWF IFS single-run RMM"),
    "ens": ("ECMWF-ENS", "ECMWF ENS ensemble-mean RMM"),
}
# bands that carry the forecast tail: the reconstruction is the WH04
# 15S-15N structure, honest for the equatorial/tropical band-means only;
# nh/sh render analysis-only.
_FC_BANDS = ("eq", "trop")


def _load_fc_pcs(out_dir: Path, model: str, max_age_days: float = 2.0):
    """(init, dates, pc1, pc2, label) from the mjo_fc_pcs{suffix}.json
    generate_mjo_rmm wrote earlier in this same CI job, or None (missing,
    malformed, wrong model, or stale init). NO fallback to raw model OLR:
    a missing/stale file simply skips that model's forecast tail."""
    sfx = {"gefs": "", "ifs": "_ifs", "ens": "_ens"}[model]
    path = out_dir / f"mjo_fc_pcs{sfx}.json"
    if not path.exists():
        print(f"OLR fc [{model}]: {path.name} missing — tail skipped")
        return None
    try:
        doc = json.loads(path.read_text())
        if doc.get("model") != model:
            raise ValueError(f"model mismatch ({doc.get('model')!r})")
        init = dt.datetime.strptime(doc["init"], "%Y-%m-%dT%H:%M:%SZ")
        fdates = [dt.date.fromisoformat(d) for d in doc["dates"]]
        pc1 = np.asarray(doc["mean_pc1"], float)
        pc2 = np.asarray(doc["mean_pc2"], float)
        if not fdates or not (len(fdates) == pc1.size == pc2.size):
            raise ValueError("empty / length mismatch")
    except Exception as e:  # noqa: BLE001 — a bad file just skips the tail
        print(f"OLR fc [{model}]: {path.name} unreadable ({e}) — "
              "tail skipped")
        return None
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if (now - init).total_seconds() > max_age_days * 86400:
        print(f"OLR fc [{model}]: init {init:%Y-%m-%d} stale — "
              "tail skipped")
        return None
    return init, fdates, pc1, pc2, doc.get("label", model)


def do_olr(hov: Path, meta: dict, genesis: list,
           fc_models=("gefs",)) -> None:
    import mjo_reconstruct
    import rmm_wh04
    print("OLR: fetching CDR v2 + LTM ...")
    dates, lats, lons, anom, n_filled = fetch_olr()
    print(f"OLR through {dates[-1]} ({len(dates)} days, "
          f"{n_filled} gap-filled)")
    nf = min(len(dates), FILTER_DAYS)
    f_dates = dates[-nf:]

    # per-band analysis series, WH04-consistent: cos-weighted band-mean,
    # then remove the previous-120-day running mean per longitude
    # (rmm_wh04's exact convention). This is the same treatment the
    # forecast reconstruction carries by construction — its PCs come from
    # 120-day-removed anomalies — so both halves live in one anomaly
    # space; it also removes the ENSO/low-frequency shading. Computed on
    # the full FETCH_DAYS fetch so every displayed row has a full window.
    bms = {}
    for bkey, (lo, hi, _bl) in BANDS.items():
        bm_full = band_mean(anom, lats, lo, hi)
        bms[bkey] = rmm_wh04.remove_trailing_mean(bm_full)[-nf:]

    # ---- forecast half = MJO-RECONSTRUCTED OLR from each model's
    # ensemble-mean RMM forecast (the mjo_fc_pcs{suffix}.json files
    # generate_mjo_rmm wrote earlier in this same job). Raw ensemble-mean
    # OLR is NOT used: phase-incoherent member averaging collapses the
    # propagating wave (the old washed-out forecast half). A missing or
    # stale PC file skips that model's tail — never a raw-OLR fallback.
    fc_by = {}          # model -> (rows on CDR lons, kept dates, init)
    ext_l = np.concatenate([mjo_reconstruct.RMM_LONS,
                            mjo_reconstruct.RMM_LONS[:1] + 360.0])
    for model in fc_models:
        pcs = _load_fc_pcs(hov.parent, model)
        if pcs is None:
            continue
        fc_init, fdates, pc1, pc2, _label = pcs
        keep = [k for k, d in enumerate(fdates) if d > f_dates[-1]]
        if not keep:
            continue
        rec = mjo_reconstruct.olr_from_pcs(pc1[keep], pc2[keep])
        rows = np.empty((len(keep), lons.size))
        for k in range(len(keep)):
            ext = np.concatenate([rec[k], rec[k][:1]])
            rows[k] = np.interp(lons % 360.0, ext_l, ext)
        print(f"OLR fc [{model}]: {len(keep)} reconstructed day(s), "
              f"init {fc_init:%Y-%m-%d} 00Z, range "
              f"{np.nanmin(rec):+.1f} .. {np.nanmax(rec):+.1f} W m-2")
        fc_by[model] = (rows, [fdates[k] for k in keep], fc_init)

    # render per model: gefs always renders (analysis-only if its PC file
    # is absent) at unchanged names + the OLR wave matrix; ifs/ens render
    # the "mjo" set only, suffixed _ifs/_ens, and only with a tail.
    render_order = list(dict.fromkeys(["gefs", *fc_models]))
    for model in render_order:
        fc_rows_all, fc_kept, fc_init = fc_by.get(model, (None, [], None))
        if model != "gefs" and not fc_kept:
            continue
        msfx = "" if model == "gefs" else f"_{model}"
        short, cred_src = _OLR_FC_LABEL[model]
        tag, recon_src = _FC_RECON[model]
        wave_sets = (OLR_WAVE_SETS if model == "gefs"
                     else {"mjo": OLR_WAVE_SETS["mjo"]})
        for bkey, (lo, hi, blabel) in BANDS.items():
            bm = bms[bkey]                                  # (nf, lon)
            # the tail rides the equatorial/tropical bands only: the
            # reconstruction is the WH04 15S-15N structure and has no
            # latitude dependence to offer nh/sh (analysis-only there)
            band_fc = (fc_kept if (fc_rows_all is not None
                                   and bkey in _FC_BANDS) else [])
            fc_axis, fc_rows = (_fc_axis_for(f_dates, band_fc, fc_rows_all)
                                if band_fc else ([], None))
            bm_ext = np.vstack([bm, fc_rows]) if fc_rows is not None else bm
            # BRIDGE the CDR->forecast latency gap for the DISPLAY: the CDR
            # ends ~4 d before the init, leaving blank rows between analysis
            # and forecast. Fill them per longitude by interpolating from the
            # last analysis day to the first forecast day so the shading is
            # continuous through the init line (honest: it is interpolation,
            # noted below).
            if fc_rows is not None:
                fc_rows = _bridge_gap_rows(bm, fc_rows)
            nfc = len(fc_axis)
            nf2 = min(nf + nfc, FILTER_DAYS)
            filts = wave_filts(bm_ext, nf2)
            title = ("OLR anomaly + equatorial waves · through "
                     f"{f_dates[-1]:%Y-%m-%d}"
                     + (f" (+{short})" if nfc else ""))
            for nd in DAYS:
                d_sub, sl = region_days_slices(f_dates, nd)
                n = len(d_sub)
                d_plot = d_sub + fc_axis
                field_plot = (np.vstack([bm[sl], fc_rows])
                              if fc_rows is not None else bm[sl])
                for wkey, modes in wave_sets.items():
                    overlays = [(m, filts[m][-(n + nfc):]) for m in modes]
                    # one line only — measured to fit the figure width at
                    # fontsize 8.5; the full forecast sentence (with the
                    # gap-bridge disclosure) lives in the fc footer line
                    note_b = ("" if not modes else
                              "contours: wavenumber-frequency filtering "
                              "per NOAA PSL conventions, 1-std interval, "
                              "+ solid / − dashed (enhanced)"
                              + (" · fcst concat, gap bridged" if nfc
                                 else " · ~2 wk damped (WW01)"))
                    for rkey in REGIONS:
                        out_png = hov / (f"hov_olr_{wkey}_{bkey}_"
                                         f"{nd}_{rkey}{msfx}.png")
                        render_hov(
                            field_plot, d_plot, lons,
                            cmap="RdBu_r", vmax=60, step=10,
                            cb_label=("OLR anomaly (W m⁻²), "
                                      "blue = enhanced convection"),
                            title=title,
                            band_label=blabel,
                            # length-budgeted: must end left of the
                            # two-chip wave legend (pixel-probed ~0.70
                            # axes-frac vs legend left edge ~0.79)
                            note_a=("shading: OLR anomaly vs 1991–2020 "
                                    "climatology (3 harmonics) minus "
                                    "the previous 120-day mean"),
                            note_b=note_b,
                            credit=((f"Data: NOAA OLR CDR + {cred_src} · "
                                     "○ genesis: tcvitals (UCAR/RAL)")
                                    if nfc else
                                    ("Data: NOAA OLR CDR (via NOAA PSL) · "
                                     "○ genesis: tcvitals (UCAR/RAL)")),
                            overlays=overlays, genesis=genesis,
                            region=rkey,
                            out_png=out_png,
                            sigma_contours=True,
                            fc_start=fc_axis[0] if fc_axis else None,
                            fc_note=(f"MJO-reconstructed OLR from "
                                     f"{recon_src} (RMM1/RMM2 × WH04 "
                                     f"EOFs), valid to "
                                     f"{band_fc[-1]:%Y-%m-%d}, CDR gap "
                                     f"bridged"
                                     if fc_axis else ""),
                            fc_label=(f"{tag} init · {fc_init:%Y-%m-%d}"
                                      if fc_axis else ""))
                        if wkey == "mjo":
                            # retired wave keys resolve for one release
                            # cycle (cached pages / old meta): the MJO
                            # default stands in for the old sets
                            for old in (OLR_WAVE_COMPAT
                                        if model == "gefs" else ("all",)):
                                shutil.copyfile(
                                    out_png,
                                    hov / (f"hov_olr_{old}_{bkey}_"
                                           f"{nd}_{rkey}{msfx}.png"))
        if model == "gefs":
            meta["vars"]["olr"] = {"through": f_dates[-1].isoformat(),
                                   "gap_filled_days": n_filled,
                                   "waves": list(OLR_WAVE_SETS),
                                   **({"fc_to": fc_kept[-1].isoformat(),
                                       "fc_init": fc_init.strftime(
                                           "%Y-%m-%dT%H:%M:%SZ")}
                                      if fc_kept else {})}
        else:
            meta["vars"]["olr"].setdefault("models", {})[model] = {
                "fc_to": fc_kept[-1].isoformat(),
                "fc_init": fc_init.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "label": short}
    print("OLR panels done")


def _bridge_gap_rows(bm, fc_rows):
    """Fill leading/interior NaN rows in ``fc_rows`` (the forecast band-mean
    on a continuous daily axis) by per-longitude linear interpolation, using
    the last analysis row ``bm[-1]`` as the anchor just before the forecast.
    Bridges the CDR→forecast latency gap so the shading is continuous; a
    fully-NaN column (no forecast at that lon) is left as-is."""
    out = fc_rows.astype(float, copy=True)
    nrow = out.shape[0]
    idx = np.arange(nrow, dtype=float)
    for j in range(out.shape[1]):
        col = out[:, j]
        fin = np.isfinite(col)
        if fin.all() or not fin.any():
            continue
        anchor_x = np.concatenate(([-1.0], idx[fin]))     # -1 = analysis end
        anchor_y = np.concatenate(([bm[-1, j]], col[fin]))
        bad = ~fin
        col[bad] = np.interp(idx[bad], anchor_x, anchor_y)
    return out


def _fc_axis_for(full, fc_dates_kept, bm_fc):
    """Continuous daily extension of `full` out to the last forecast day:
    (fc_axis dates, rows) with unfetched days left NaN so the render shows
    an honest gap row instead of silently compressing time."""
    if not fc_dates_kept:
        return [], None
    ext_days = (fc_dates_kept[-1] - full[-1]).days
    fc_axis = [full[-1] + dt.timedelta(days=i + 1) for i in range(ext_days)]
    rows = np.full((len(fc_axis), bm_fc.shape[1]), np.nan)
    fidx = {d: i for i, d in enumerate(fc_axis)}
    for m2, d in enumerate(fc_dates_kept):
        rows[fidx[d]] = bm_fc[m2]
    return fc_axis, rows


def do_u(hov: Path, meta: dict, genesis: list, u_archive_path: Path,
         target_depth: int, backfill_days: int, gefs_tail=None) -> None:
    import xarray as xr
    if not U_CLIMO_NC.exists():
        print("u climo missing — u panels skipped")
        return
    archive = load_u_archive(u_archive_path)
    if archive:
        print(f"u archive: {len(archive[0])} days")
    times, levels, lats_u, lons_u, u, v, ncyc = backfill_u(
        archive, target_depth, backfill_days)
    save_u_archive(u_archive_path, times, levels, lats_u, lons_u,
                   u, v, ncyc)
    print(f"u archive saved: {len(times)} days "
          f"({times[0]} .. {times[-1]})")
    uclim = xr.open_dataset(U_CLIMO_NC)
    # continuous daily axis with NaN gaps so the y-axis is honest
    full = [times[0] + dt.timedelta(days=i)
            for i in range((times[-1] - times[0]).days + 1)]
    index = {d: i for i, d in enumerate(times)}
    for li, level in enumerate(U_LEVELS):
        vkey = f"u{int(level)}"
        anom_full = np.full((len(full), lats_u.size, lons_u.size),
                            np.nan)
        for di, d in enumerate(full):
            i = index.get(d)
            if i is None:
                continue
            anom_full[di] = u[i, li] - monthly_climo_for(
                uclim, "u", d, float(level), lats_u, lons_u)

        # GEFS ensemble-mean forecast anomaly at this level (Phase 3):
        # failure only drops the tail, never the analysis panels
        fc_anom, fc_kept, fc_init, fc_ns = None, [], None, []
        if gefs_tail:
            try:
                fc_init, fdates, fu, fv, fc_ns = gefs_tail
                glats, glons = gefs_mean.grid()
                li_g = list(gefs_mean.LEVELS).index(float(level))
                ii = np.array([int(np.where(np.isclose(glats, x))[0][0])
                               for x in lats_u])
                jj = np.array([int(np.where(np.isclose(glons, x))[0][0])
                               for x in lons_u])
                keep = [k for k, d in enumerate(fdates) if d > full[-1]]
                fc_kept = [fdates[k] for k in keep]
                if fc_kept:
                    fc_anom = np.empty((len(keep), lats_u.size,
                                        lons_u.size))
                    for m2, k in enumerate(keep):
                        fc_anom[m2] = (fu[k, li_g][np.ix_(ii, jj)]
                                       - monthly_climo_for(
                                           uclim, "u", fdates[k],
                                           float(level), lats_u, lons_u))
            except Exception as e:  # noqa: BLE001 — tail is additive
                print(f"{vkey} fc tail failed ({type(e).__name__}: {e}) "
                      f"— analysis only")
                fc_anom, fc_kept = None, []

        nf = min(len(full) + len(fc_kept), FILTER_DAYS)
        title = (f"{int(level)}-hPa zonal wind anomaly · through "
                 f"{times[-1]:%Y-%m-%d}"
                 + (" (+GEFS)" if fc_kept else ""))
        for bkey, (lo, hi, blabel) in BANDS.items():
            bm = band_mean(anom_full, lats_u, lo, hi)
            bm_fc = (band_mean(fc_anom, lats_u, lo, hi)
                     if fc_anom is not None else None)
            fc_axis, fc_rows = _fc_axis_for(full, fc_kept, bm_fc) \
                if bm_fc is not None else ([], None)
            bm_ext = np.vstack([bm, fc_rows]) if fc_rows is not None else bm
            nfc = len(fc_axis)
            filts = wave_filts(bm_ext, nf)      # analysis+forecast concat
            for nd in DAYS:
                d_sub, sl = region_days_slices(full, nd)
                n = len(d_sub)
                d_plot = d_sub + fc_axis
                field_plot = (np.vstack([bm[sl], fc_rows])
                              if fc_rows is not None else bm[sl])
                shallow = int(np.isfinite(
                    bm[sl]).all(axis=1).sum())
                arch_note = (f"{shallow} of {len(d_sub)} days "
                             f"archived"
                             + (" · archive still building"
                                if shallow < len(d_sub) else ""))
                for wkey, modes in WAVE_SETS.items():
                    overlays = [(m, filts[m][-(n + nfc):]) for m in modes]
                    note_b = arch_note if not overlays else (
                        arch_note + " · wave contours ±2 m s⁻¹ steps, "
                        "− solid / + dashed · "
                        + ("filtered on the analysis+forecast concat"
                           if nfc else "newest ~2 wk damped (WW01)"))
                    for rkey in REGIONS:
                        out_png = hov / (f"hov_{vkey}_{wkey}_{bkey}_"
                                         f"{nd}_{rkey}.png")
                        render_hov(
                            field_plot, d_plot, lons_u,
                            cmap="RdBu_r", vmax=12, step=2,
                            cb_label=f"u{int(level)} anomaly (m s⁻¹)",
                            title=title,
                            band_label=blabel,
                            note_a=("GFS 1p00 daily-mean analyses · "
                                    "anomalies vs ERA5 monthly "
                                    "climatology 1991–2020 · red = "
                                    "westerly anomaly"),
                            note_b=note_b,
                            credit=("Data: NCEP GFS + GEFS mean (fcst) · "
                                    "climatology: ERA5 (via UH APDRC) · "
                                    "○ genesis: tcvitals (UCAR/RAL)"
                                    if nfc else
                                    "Data: NCEP GFS · climatology: ERA5 (via "
                                    "UH APDRC) · ○ genesis: tcvitals "
                                    "(UCAR/RAL)"),
                            overlays=overlays, genesis=genesis,
                            region=rkey,
                            out_png=out_png, wave_step=2.0,
                            fc_start=fc_axis[0] if fc_axis else None,
                            fc_note=(gefs_fc_note(fc_init, fc_ns,
                                                  fc_kept[-1])
                                     if fc_axis else ""),
                            fc_label=(f"GEFS init · {fc_init:%Y-%m-%d}"
                                      if fc_axis else ""))
                        if wkey == "none":
                            # legacy wave-less name: kept one release cycle
                            # so a cached page (old JS) still resolves
                            shutil.copyfile(
                                out_png, hov / (f"hov_{vkey}_{bkey}_"
                                                f"{nd}_{rkey}.png"))
        meta["vars"][vkey] = {"through": times[-1].isoformat(),
                              "days_archived": len(times),
                              "waves": list(WAVE_SETS),
                              **({"fc_to": fc_kept[-1].isoformat(),
                                  "fc_init": fc_init.strftime(
                                      "%Y-%m-%dT%H:%M:%SZ")}
                                 if fc_kept else {})}

    # ---- v850: the MRG / TD-type home, same archive + climo + WK path.
    # Gated on the climatology carrying v (lands with the rebuilt .nc) and
    # on any finite v in the archive (old archives load v as all-NaN and
    # backfill forward) — until both hold, the section skips honestly.
    if "v" not in uclim:
        print("v850 skipped: u climo has no v yet "
              "(rebuild build_u_climatology.py + commit the .nc)")
    elif not np.isfinite(v).any():
        print("v850 skipped: no v days in the archive yet")
    else:
        li850 = list(U_LEVELS).index(850.0)
        anom_full = np.full((len(full), lats_u.size, lons_u.size),
                            np.nan)
        for di, d in enumerate(full):
            i = index.get(d)
            if i is None or not np.isfinite(v[i, li850]).any():
                continue
            anom_full[di] = v[i, li850] - monthly_climo_for(
                uclim, "v", d, 850.0, lats_u, lons_u)

        fc_anom, fc_kept, fc_init, fc_ns = None, [], None, []
        if gefs_tail:
            try:
                fc_init, fdates, fu, fv, fc_ns = gefs_tail
                glats, glons = gefs_mean.grid()
                li_g = list(gefs_mean.LEVELS).index(850.0)
                ii = np.array([int(np.where(np.isclose(glats, x))[0][0])
                               for x in lats_u])
                jj = np.array([int(np.where(np.isclose(glons, x))[0][0])
                               for x in lons_u])
                keep = [k for k, d in enumerate(fdates) if d > full[-1]]
                fc_kept = [fdates[k] for k in keep]
                if fc_kept:
                    fc_anom = np.empty((len(keep), lats_u.size,
                                        lons_u.size))
                    for m2, k in enumerate(keep):
                        fc_anom[m2] = (fv[k, li_g][np.ix_(ii, jj)]
                                       - monthly_climo_for(
                                           uclim, "v", fdates[k],
                                           850.0, lats_u, lons_u))
            except Exception as e:  # noqa: BLE001 — tail is additive
                print(f"v850 fc tail failed ({type(e).__name__}: {e}) "
                      f"— analysis only")
                fc_anom, fc_kept = None, []

        nf = min(len(full) + len(fc_kept), FILTER_DAYS)
        title = (f"850-hPa meridional wind anomaly · through "
                 f"{times[-1]:%Y-%m-%d}"
                 + (" (+GEFS)" if fc_kept else ""))
        for bkey, (lo, hi, blabel) in BANDS.items():
            bm = band_mean(anom_full, lats_u, lo, hi)
            bm_fc = (band_mean(fc_anom, lats_u, lo, hi)
                     if fc_anom is not None else None)
            fc_axis, fc_rows = _fc_axis_for(full, fc_kept, bm_fc) \
                if bm_fc is not None else ([], None)
            bm_ext = np.vstack([bm, fc_rows]) if fc_rows is not None else bm
            nfc = len(fc_axis)
            filts = wave_filts(bm_ext, nf)
            for nd in DAYS:
                d_sub, sl = region_days_slices(full, nd)
                n = len(d_sub)
                d_plot = d_sub + fc_axis
                field_plot = (np.vstack([bm[sl], fc_rows])
                              if fc_rows is not None else bm[sl])
                shallow = int(np.isfinite(
                    bm[sl]).all(axis=1).sum())
                arch_note = (f"{shallow} of {len(d_sub)} days "
                             f"archived"
                             + (" · archive still building"
                                if shallow < len(d_sub) else ""))
                for wkey, modes in WAVE_SETS.items():
                    overlays = [(m, filts[m][-(n + nfc):]) for m in modes]
                    note_b = arch_note if not overlays else (
                        arch_note + " · wave contours ±2 m s⁻¹ steps, "
                        "− solid / + dashed · "
                        + ("filtered on the analysis+forecast concat"
                           if nfc else "newest ~2 wk damped (WW01)"))
                    for rkey in REGIONS:
                        out_png = hov / (f"hov_v850_{wkey}_{bkey}_"
                                         f"{nd}_{rkey}.png")
                        render_hov(
                            field_plot, d_plot, lons_u,
                            cmap="RdBu_r", vmax=10, step=2,
                            cb_label="v850 anomaly (m s⁻¹)",
                            title=title,
                            band_label=blabel,
                            note_a=("GFS 1p00 daily-mean analyses · "
                                    "anomalies vs ERA5 monthly "
                                    "climatology 1991–2020 · red = "
                                    "southerly anomaly"),
                            note_b=note_b,
                            credit=("Data: NCEP GFS + GEFS mean (fcst) · "
                                    "climatology: ERA5 (via UH APDRC) · "
                                    "○ genesis: tcvitals (UCAR/RAL)"
                                    if nfc else
                                    "Data: NCEP GFS · climatology: ERA5 (via "
                                    "UH APDRC) · ○ genesis: tcvitals "
                                    "(UCAR/RAL)"),
                            overlays=overlays, genesis=genesis,
                            region=rkey,
                            out_png=out_png, wave_step=2.0,
                            fc_start=fc_axis[0] if fc_axis else None,
                            fc_note=(gefs_fc_note(fc_init, fc_ns,
                                                  fc_kept[-1])
                                     if fc_axis else ""),
                            fc_label=(f"GEFS init · {fc_init:%Y-%m-%d}"
                                      if fc_axis else ""))
                        if wkey == "none":
                            shutil.copyfile(
                                out_png, hov / (f"hov_v850_{bkey}_"
                                                f"{nd}_{rkey}.png"))
        meta["vars"]["v850"] = {"through": times[-1].isoformat(),
                                "days_archived": int(np.isfinite(
                                    v[:, li850]).any(axis=(1, 2)).sum()),
                                "waves": list(WAVE_SETS),
                                **({"fc_to": fc_kept[-1].isoformat(),
                                    "fc_init": fc_init.strftime(
                                        "%Y-%m-%dT%H:%M:%SZ")}
                                   if fc_kept else {})}
        print("v850 panels done")
    uclim.close()
    print("u panels done")

def do_chi(hov: Path, meta: dict, genesis: list,
           chi_archive_path: Path, gefs_tail=None) -> None:
    import xarray as xr
    import vp_windows
    archive = vp_windows.load_archive(chi_archive_path)
    if archive is None:
        print("chi archive missing — chi200 panels skipped "
              "(the workflow restores it before this step)")
        return
    if not CHI_CLIMO_NC.exists():
        print("chi climo missing — chi200 panels skipped")
        return
    times, levels, lats_c, lons_c, chi, _ = archive
    li = list(np.asarray(levels, float)).index(200.0)
    cclim = xr.open_dataset(CHI_CLIMO_NC)
    full = [times[0] + dt.timedelta(days=i)
            for i in range((times[-1] - times[0]).days + 1)]
    index = {d: i for i, d in enumerate(times)}
    anom_full = np.full((len(full), lats_c.size, lons_c.size),
                        np.nan)
    for di, d in enumerate(full):
        i = index.get(d)
        if i is None:
            continue
        anom_full[di] = chi[i, li] - monthly_climo_for(
            cclim, "chi", d, 200.0, lats_c, lons_c)

    # GEFS ensemble-mean chi tail: solve chi from the mean wind per
    # forecast day (the solve is linear, so this IS the mean chi)
    fc_anom, fc_kept, fc_init, fc_ns = None, [], None, []
    if gefs_tail:
        try:
            import chi_core
            fc_init, fdates, fu, fv, fc_ns = gefs_tail
            glats, glons = gefs_mean.grid()
            li_g = list(gefs_mean.LEVELS).index(200.0)
            ii = np.array([int(np.where(np.isclose(glats, x))[0][0])
                           for x in lats_c])
            jj = np.array([int(np.where(np.isclose(glons, x))[0][0])
                           for x in lons_c])
            keep = [k for k, d in enumerate(fdates) if d > full[-1]]
            fc_kept = [fdates[k] for k in keep]
            if fc_kept:
                fc_anom = np.empty((len(keep), lats_c.size, lons_c.size))
                for m2, k in enumerate(keep):
                    chi_fc, _, _ = chi_core.chi_from_uv(
                        fu[k, li_g], fv[k, li_g], glats, glons)
                    fc_anom[m2] = (chi_fc[np.ix_(ii, jj)]
                                   - monthly_climo_for(
                                       cclim, "chi", fdates[k], 200.0,
                                       lats_c, lons_c))
        except Exception as e:  # noqa: BLE001 — tail is additive
            print(f"chi200 fc tail failed ({type(e).__name__}: {e}) "
                  f"— analysis only")
            fc_anom, fc_kept = None, []
    cclim.close()
    nf = min(len(full) + len(fc_kept), FILTER_DAYS)
    title = (f"200-hPa velocity potential anomaly · through "
             f"{times[-1]:%Y-%m-%d}"
             + (" (+GEFS)" if fc_kept else ""))
    for bkey, (lo, hi, blabel) in BANDS.items():
        bm = band_mean(anom_full, lats_c, lo, hi) / 1e6
        bm_fc = (band_mean(fc_anom, lats_c, lo, hi) / 1e6
                 if fc_anom is not None else None)
        fc_axis, fc_rows = _fc_axis_for(full, fc_kept, bm_fc) \
            if bm_fc is not None else ([], None)
        bm_ext = np.vstack([bm, fc_rows]) if fc_rows is not None else bm
        nfc = len(fc_axis)
        filts = wave_filts(bm_ext, nf)          # analysis+forecast concat
        for nd in DAYS:
            d_sub, sl = region_days_slices(full, nd)
            n = len(d_sub)
            d_plot = d_sub + fc_axis
            field_plot = (np.vstack([bm[sl], fc_rows])
                          if fc_rows is not None else bm[sl])
            vmax = max(4.0, float(np.ceil(np.nanpercentile(
                np.abs(bm[sl]), 99) / 2) * 2))
            step = 1.0 if vmax <= 8 else 2.0
            for wkey, modes in WAVE_SETS.items():
                overlays = [(m, filts[m][-(n + nfc):]) for m in modes]
                note_b = ("green = negative χ′ = anomalous "
                          "upper-level divergence (enhanced "
                          "convection)")
                if overlays:
                    note_b += (" · wave contours: divergent (−χ′) solid "
                               "/ convergent dashed · "
                               + ("analysis+forecast concat" if nfc
                                  else "~2 wk damped (WW01)"))
                for rkey in REGIONS:
                    out_png = hov / (f"hov_chi200_{wkey}_{bkey}_"
                                     f"{nd}_{rkey}.png")
                    render_hov(
                        field_plot, d_plot, lons_c,
                        cmap="BrBG_r", vmax=vmax, step=step,
                        cb_label="χ200 anomaly (10⁶ m² s⁻¹)",
                        title=title,
                        band_label=blabel,
                        note_a=("daily T21 χ from GFS analyses (the χ "
                                "product's archive) · anomalies vs "
                                "ERA5 monthly climatology 1991–2020"),
                        note_b=note_b,
                        credit=("Data: NCEP GFS + GEFS mean (fcst) · "
                                "climatology: ERA5 (via UH APDRC) · "
                                "○ genesis: tcvitals (UCAR/RAL)"
                                if nfc else
                                "Data: NCEP GFS · climatology: ERA5 (via "
                                "UH APDRC) · ○ genesis: tcvitals "
                                "(UCAR/RAL)"),
                        overlays=overlays, genesis=genesis, region=rkey,
                        out_png=out_png, wave_step=step,
                        fc_start=fc_axis[0] if fc_axis else None,
                        fc_note=(gefs_fc_note(fc_init, fc_ns, fc_kept[-1])
                                 if fc_axis else ""),
                        fc_label=(f"GEFS init · {fc_init:%Y-%m-%d}"
                                  if fc_axis else ""))
                    if wkey == "none":
                        # legacy wave-less name: kept one release cycle
                        # so a cached page (old JS) still resolves
                        shutil.copyfile(
                            out_png, hov / (f"hov_chi200_{bkey}_"
                                            f"{nd}_{rkey}.png"))
    meta["vars"]["chi200"] = {"through": times[-1].isoformat(),
                              "days_archived": len(times),
                              "waves": list(WAVE_SETS),
                              **({"fc_to": fc_kept[-1].isoformat(),
                                  "fc_init": fc_init.strftime(
                                      "%Y-%m-%dT%H:%M:%SZ")}
                                 if fc_kept else {})}
    print("chi200 panels done")


# ------------------------------------------------------------------ main

def main() -> None:
    import matplotlib.patheffects  # noqa: F401  (registered for annotate)

    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(HERE / "subseasonal" / "out"))
    p.add_argument("--u-archive", default=None)
    p.add_argument("--u-backfill-days", type=int, default=45)
    p.add_argument("--u-target-depth", type=int, default=220)
    p.add_argument("--chi-archive", default=None)
    p.add_argument("--skip-olr", action="store_true")
    p.add_argument("--skip-u", action="store_true")
    p.add_argument("--skip-chi", action="store_true")
    p.add_argument("--fc-models", default="gefs",
                   help="comma list of OLR forecast-tail models "
                        "(gefs,ifs,ens); tails are MJO-reconstructed from "
                        "each model's mjo_fc_pcs json. gefs renders the OLR "
                        "wave matrix at unchanged names; ifs/ens add "
                        "_ifs/_ens-suffixed 'mjo'-wave panels (ECMWF open "
                        "data, CC BY 4.0).")
    args = p.parse_args()
    _toks = [m.strip() for m in args.fc_models.split(",") if m.strip()]
    fc_models = tuple(m for m in _toks
                      if m in ("gefs", "ifs", "ens")) or ("gefs",)
    _bad = [m for m in _toks if m not in ("gefs", "ifs", "ens")]
    if _bad:
        print(f"--fc-models: ignoring unknown model(s) {_bad} "
              f"(using {list(fc_models)})", file=sys.stderr)
    out = Path(args.out)
    hov = out / "hov"
    hov.mkdir(parents=True, exist_ok=True)
    u_archive_path = Path(args.u_archive) if args.u_archive else \
        out / "u_daily_archive.nc"
    chi_archive_path = Path(args.chi_archive) if args.chi_archive else \
        out / "chi_daily_archive.nc"

    today = dt.datetime.now(dt.timezone.utc).date()
    # meta["waves"] is the DEFAULT (OLR) wave list — the page opens on OLR
    # and old JS validates its wave state against it. Per-variable lists
    # ride meta["vars"][v]["waves"] (arrays; old JS truth-tests them).
    meta = {"vars": {}, "waves": list(OLR_WAVE_SETS),
            "bands": {k: v[2] for k, v in BANDS.items()},
            "days": list(DAYS),
            "regions": {k: v[2] for k, v in REGIONS.items()},
            "template_olr": "hov/hov_olr_{wave}_{band}_{days}_{region}.png",
            "template_olr_model":
                "hov/hov_olr_{wave}_{band}_{days}_{region}_{model}.png",
            "template": "hov/hov_{var}_{band}_{days}_{region}.png",
            # every variable carries the wave overlays now; the wave-less
            # `template` stays (plus legacy-name copies) so a cached page
            # running the previous JS keeps resolving for one cycle
            "template_wave":
                "hov/hov_{var}_{wave}_{band}_{days}_{region}.png"}
    genesis = fetch_genesis(today - dt.timedelta(days=max(DAYS) + 5), today)
    print(f"genesis markers: {len(genesis)}")

    # GEFS ensemble-mean forecast tail (Phase 3 Group B): fetched once
    # (or reused from the VP product's gefs_tail.nc in the same run) and
    # threaded into the u/v/chi sections; None = analysis-only, as before.
    # The OLR section no longer touches it — its forecast half is the MJO
    # reconstruction from generate_mjo_rmm's PC files.
    gefs_tail = (None if (args.skip_u and args.skip_chi)
                 else get_gefs_tail(out))

    # one flaky upstream (PSL THREDDS, AWS GFS, R2) must not take down
    # the other variables' panels — isolate each section, fail loudly
    # only if EVERYTHING failed (or nothing rendered at all)
    sections = []
    if not args.skip_olr:
        sections.append(("OLR", lambda: do_olr(hov, meta, genesis,
                                                fc_models=fc_models)))
    if not args.skip_u:
        sections.append(("u", lambda: do_u(
            hov, meta, genesis, u_archive_path,
            args.u_target_depth, args.u_backfill_days,
            gefs_tail=gefs_tail)))
    if not args.skip_chi:
        sections.append(("chi200", lambda: do_chi(
            hov, meta, genesis, chi_archive_path,
            gefs_tail=gefs_tail)))
    failed = []
    for name, fn in sections:
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — section isolation
            failed.append(name)
            print(f"{name} section FAILED ({type(e).__name__}: {e}) "
                  f"- continuing with the other sections", file=sys.stderr)

    meta["genesis_markers"] = len(genesis)
    meta["generated_utc"] = dt.datetime.now(dt.timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    (out / "hov_meta.json").write_text(json.dumps(meta))
    n = len(list(hov.glob("*.png")))
    print(f"wrote {n} hovmöller panels + hov_meta.json"
          + (f" (FAILED sections: {', '.join(failed)})" if failed else ""))
    if n == 0:
        raise SystemExit("no panels rendered — failing loudly")
    if sections and len(failed) == len(sections):
        raise SystemExit("every section failed — failing loudly")


if __name__ == "__main__":
    main()
