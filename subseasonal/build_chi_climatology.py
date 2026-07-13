"""build_chi_climatology.py — ONE-OFF builder for the 1991-2020 monthly
velocity-potential climatology the /subseasonal/ product anomalizes
against. Downloads ERA5 MONTHLY-MEAN u/v (true 'moda' means) from the
APDRC (Univ. Hawaii) anonymous OPeNDAP server, subsamples to 1 deg,
averages 1991-2020 per calendar month at 200 & 850 hPa, solves chi the
SAME way the daily product does (T21, chi_core), and writes
subseasonal/chi_climo_1991_2020.nc (committed, ~1 MB).

METHOD NOTE (2026-07-13 rebuild): v1 used NCEP/NCAR R1 (1948-era, T62)
as the baseline under modern GFS analyses — a cross-model mismatch that
inflates/distorts the anomaly. ERA5 vs the GFS analysis is
like-vs-modern-like at the T21 planetary scales this product keeps, and
the solve pipeline (1-deg winds -> spectral chi -> T21) is IDENTICAL on
both sides of the subtraction.

The chi solve is linear in wind, so chi(monthly-mean wind) IS the
monthly-mean chi — no daily fields needed (test-locked linearity,
tests/test_vp_windows.py).

Run locally / manually:  python subseasonal/build_chi_climatology.py
(~375 MB moved over OPeNDAP, ~20-30 min; single university server, be
patient — 5-year slabs with retry/backoff.)
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chi_core  # noqa: E402

APDRC = ("https://apdrc.soest.hawaii.edu/dods/public_data/Reanalysis_Data/"
         "ERA5/monthly_3d/Wind_velocities")
LEVELS = (200.0, 850.0)
YEARS = (1991, 2020)
STRIDE = 4          # 0.25 deg -> 1 deg (plenty above T21; matches GFS 1p00)
SLAB_YEARS = 5      # per-request slab; APDRC is a single GDS server
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "chi_climo_1991_2020.nc")


def fetch_monthlies():
    """-> (times, lats, lons, u, v) with u/v shaped (month, level, lat, lon)
    for 1991-2020 at 1 deg. Slabbed + retried; label-based time selection."""
    import warnings

    import xarray as xr
    warnings.filterwarnings("ignore")   # GrADS 'days since 1-1-1' calendar

    ds = xr.open_dataset(APDRC, engine="netcdf4")
    sub = ds[["u", "v"]].sel(lev=list(LEVELS)).isel(
        lat=slice(0, None, STRIDE), lon=slice(0, None, STRIDE))

    slabs = []
    for y0 in range(YEARS[0], YEARS[1] + 1, SLAB_YEARS):
        y1 = min(y0 + SLAB_YEARS - 1, YEARS[1])
        for attempt in range(4):
            try:
                t0 = time.time()
                s = sub.sel(time=slice(f"{y0}-01-01", f"{y1}-12-31")).load()
                n = s.time.size
                assert n == 12 * (y1 - y0 + 1), f"{n} months in {y0}-{y1}"
                print(f"  {y0}-{y1}: {n} months in {time.time() - t0:.0f}s")
                slabs.append(s)
                break
            except Exception as e:  # noqa: BLE001 — university server, retry
                wait = 20 * (attempt + 1)
                print(f"  {y0}-{y1} attempt {attempt + 1} failed ({e}); "
                      f"retrying in {wait}s")
                time.sleep(wait)
        else:
            raise RuntimeError(f"APDRC slab {y0}-{y1} failed 4 times")
    all_ = xr.concat(slabs, dim="time")
    assert all_.time.size == 12 * (YEARS[1] - YEARS[0] + 1)
    return all_


def main() -> None:
    import xarray as xr

    print(f"fetching ERA5 monthly u/v {YEARS[0]}-{YEARS[1]} from APDRC ...")
    data = fetch_monthlies()
    lats = data.lat.values.astype(float)     # -90 -> 90 ascending
    lons = data.lon.values.astype(float)     # 0 -> 359.x

    months = np.arange(1, 13)
    chi_climo = np.zeros((len(LEVELS), 12, lats.size, lons.size), np.float32)
    for li, lev in enumerate(LEVELS):
        u_climo = data.u.sel(lev=lev).groupby("time.month").mean("time")
        v_climo = data.v.sel(lev=lev).groupby("time.month").mean("time")
        for mi, m in enumerate(months):
            chi, _, _ = chi_core.chi_from_uv(
                u_climo.sel(month=m).values.astype(float),
                v_climo.sel(month=m).values.astype(float), lats, lons)
            chi_climo[li, mi] = chi.astype(np.float32)
            print(f"  level {lev:.0f} month {m:2d}: chi range "
                  f"[{chi.min():.2e}, {chi.max():.2e}] m^2/s")

    out = xr.Dataset(
        {"chi": (("level", "month", "lat", "lon"), chi_climo,
                 {"units": "m2 s-1",
                  "long_name": ("velocity potential, T21, monthly "
                                f"climatology {YEARS[0]}-{YEARS[1]}"),
                  "source": ("ERA5 monthly-mean winds (C3S/ECMWF via APDRC "
                             "OPeNDAP, 1 deg subsample); chi solved by "
                             "subseasonal/chi_core.py, T21")})},
        coords={"level": np.array(LEVELS, np.float32),
                "month": months.astype(np.int32),
                "lat": lats.astype(np.float32),
                "lon": lons.astype(np.float32)})
    enc = {"chi": {"zlib": True, "complevel": 6}}
    out.to_netcdf(OUT + ".new", encoding=enc)

    # sanity vs the previous committed climatology (if present): the
    # planetary chi pattern should correlate strongly source-to-source
    if os.path.exists(OUT):
        old = xr.open_dataset(OUT)
        try:
            for li, lev in enumerate(LEVELS):
                o = old.chi.sel(level=lev, month=7)
                n = out.chi.sel(level=lev, month=7).interp(
                    lat=old.lat, lon=old.lon)
                r = np.corrcoef(o.values.ravel(), n.values.ravel())[0, 1]
                print(f"  July pattern r vs previous climo @ {lev:.0f}: "
                      f"{r:.3f}")
        finally:
            old.close()
    os.replace(OUT + ".new", OUT)
    print(f"wrote {OUT} ({os.path.getsize(OUT) / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
