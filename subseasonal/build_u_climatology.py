"""build_u_climatology.py — ONE-OFF builder for the 1991-2020 monthly
ZONAL-WIND climatology the /subseasonal/ Hovmöller product anomalizes
against. Downloads ERA5 MONTHLY-MEAN u (true 'moda' means) from the
APDRC (Univ. Hawaii) anonymous OPeNDAP server, subsamples to 1 deg,
averages 1991-2020 per calendar month at 200 & 850 hPa over the
35S-35N tropics band, and writes subseasonal/u_climo_1991_2020.nc
(committed, ~2 MB).

Same source + method family as build_chi_climatology.py (ERA5 monthly
means, like-vs-modern-like under the daily GFS analyses the live side
uses) — u is a direct field so there is no solve step, just the
monthly-mean average. The Hovmöller renderer interpolates the monthly
climatology to the date (month centers) exactly the way
generate_velocity_potential.climo_chi_for does.

Run locally / manually:  python subseasonal/build_u_climatology.py
(~190 MB moved over OPeNDAP, ~10-20 min; single university server, be
patient — 5-year slabs with retry/backoff.)
"""
from __future__ import annotations

import os
import time

import numpy as np

APDRC = ("https://apdrc.soest.hawaii.edu/dods/public_data/Reanalysis_Data/"
         "ERA5/monthly_3d/Wind_velocities")
LEVELS = (200.0, 850.0)
YEARS = (1991, 2020)
STRIDE = 4          # 0.25 deg -> 1 deg (matches GFS 1p00)
LAT_BAND = 35.0     # Hovmöllers band-average inside 15S-15N; keep margin
SLAB_YEARS = 5      # per-request slab; APDRC is a single GDS server
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "u_climo_1991_2020.nc")


def fetch_monthlies():
    """-> xarray dataset of monthly-mean u for 1991-2020 at 1 deg over
    the 35S-35N band. Slabbed + retried; label-based time selection."""
    import warnings

    import xarray as xr
    warnings.filterwarnings("ignore")   # GrADS 'days since 1-1-1' calendar

    ds = xr.open_dataset(APDRC, engine="netcdf4")
    sub = ds[["u"]].sel(lev=list(LEVELS)).isel(
        lat=slice(0, None, STRIDE), lon=slice(0, None, STRIDE))
    sub = sub.sel(lat=slice(-LAT_BAND, LAT_BAND)) \
        if float(sub.lat[0]) < float(sub.lat[-1]) \
        else sub.sel(lat=slice(LAT_BAND, -LAT_BAND))

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

    print(f"fetching ERA5 monthly u {YEARS[0]}-{YEARS[1]} from APDRC ...")
    data = fetch_monthlies()
    lats = data.lat.values.astype(float)
    lons = data.lon.values.astype(float)

    months = np.arange(1, 13)
    u_climo = np.zeros((len(LEVELS), 12, lats.size, lons.size), np.float32)
    for li, lev in enumerate(LEVELS):
        mc = data.u.sel(lev=lev).groupby("time.month").mean("time")
        for mi, m in enumerate(months):
            u_climo[li, mi] = mc.sel(month=m).values.astype(np.float32)
        print(f"  level {lev:.0f}: u range "
              f"[{u_climo[li].min():+.1f}, {u_climo[li].max():+.1f}] m/s")

    out = xr.Dataset(
        {"u": (("level", "month", "lat", "lon"), u_climo,
               {"units": "m s-1",
                "long_name": ("zonal wind, monthly climatology "
                              f"{YEARS[0]}-{YEARS[1]}, {LAT_BAND:.0f}S-"
                              f"{LAT_BAND:.0f}N band"),
                "source": ("ERA5 monthly-mean winds (C3S/ECMWF via APDRC "
                           "OPeNDAP, 1 deg subsample)")})},
        coords={"level": np.array(LEVELS, np.float32),
                "month": months.astype(np.int32),
                "lat": lats.astype(np.float32),
                "lon": lons.astype(np.float32)})
    enc = {"u": {"zlib": True, "complevel": 6}}
    out.to_netcdf(OUT + ".new", encoding=enc)

    # sanity: July 200-hPa tropical easterlies / 850 trades signature
    j200 = out.u.sel(level=200.0, month=7).sel(lat=slice(-10, 10)).mean()
    j850 = out.u.sel(level=850.0, month=7).sel(lat=slice(-10, 10)).mean()
    print(f"  sanity July eq-band mean: u200 {float(j200):+.1f} m/s "
          f"(expect easterly<0), u850 {float(j850):+.1f} m/s")

    os.replace(OUT + ".new", OUT)
    print(f"wrote {OUT} ({os.path.getsize(OUT) / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
