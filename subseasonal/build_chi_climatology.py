"""build_chi_climatology.py — ONE-OFF builder for the 1991-2020 monthly
velocity-potential climatology the daily /subseasonal/ product anomalizes
against. Downloads NCEP/NCAR Reanalysis-1 monthly-mean u/v (PSL), averages
1991-2020 per calendar month at 200 & 850 hPa, solves chi (T21, chi_core) for
each monthly-mean wind, and writes subseasonal/chi_climo_1991_2020.nc
(~0.2 MB — committed to the repo like the ARMOR3D climatology).

Run locally / manually:  python subseasonal/build_chi_climatology.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chi_core  # noqa: E402

PSL = "https://downloads.psl.noaa.gov/Datasets/ncep.reanalysis.derived/pressure"
LEVELS = (200.0, 850.0)
YEARS = (1991, 2020)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "chi_climo_1991_2020.nc")


def fetch(name: str, cache_dir: str) -> str:
    import requests
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, name)
    if os.path.exists(path) and os.path.getsize(path) > 1e6:
        return path
    url = f"{PSL}/{name}"
    print(f"downloading {url} ...")
    with requests.get(url, stream=True, timeout=(15, 300)) as r:
        r.raise_for_status()
        with open(path + ".part", "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    os.replace(path + ".part", path)
    return path


def main() -> None:
    import xarray as xr

    cache = os.environ.get("CHI_CLIMO_CACHE", "/tmp/chi_climo_cache")
    du = xr.open_dataset(fetch("uwnd.mon.mean.nc", cache))
    dv = xr.open_dataset(fetch("vwnd.mon.mean.nc", cache))

    sel = dict(time=slice(f"{YEARS[0]}-01-01", f"{YEARS[1]}-12-31"))
    lats = du.lat.values          # 90 -> -90 (descending), 2.5 deg
    lons = du.lon.values          # 0 -> 357.5

    months = np.arange(1, 13)
    chi_climo = np.zeros((len(LEVELS), 12, lats.size, lons.size), np.float32)
    for li, lev in enumerate(LEVELS):
        u = du.uwnd.sel(level=lev, **sel)
        v = dv.vwnd.sel(level=lev, **sel)
        n_years = len(np.unique(u["time.year"]))
        assert n_years == YEARS[1] - YEARS[0] + 1, f"{n_years} years at {lev}"
        u_climo = u.groupby("time.month").mean("time")
        v_climo = v.groupby("time.month").mean("time")
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
                  "long_name": ("velocity potential, T21, monthly climatology "
                                f"{YEARS[0]}-{YEARS[1]}"),
                  "source": ("NCEP/NCAR Reanalysis 1 monthly means (NOAA PSL);"
                             " chi solved by subseasonal/chi_core.py")})},
        coords={"level": np.array(LEVELS, np.float32),
                "month": months.astype(np.int32),
                "lat": lats.astype(np.float32),
                "lon": lons.astype(np.float32)})
    enc = {"chi": {"zlib": True, "complevel": 6}}
    out.to_netcdf(OUT, encoding=enc)
    print(f"wrote {OUT} ({os.path.getsize(OUT)/1e6:.2f} MB)")


if __name__ == "__main__":
    main()
