"""sarobs.salinity - SMAP sea-surface-salinity reliability mask for SAR winds.

C-band SAR ocean-wind retrieval assumes a typical open-ocean surface; over
LOW-salinity water (river plumes, heavy-rain fresh lenses) the surface
behaves differently and rain contamination correlates with fresh lenses, so
the retrieved winds are less reliable there. This module ingests the RSS
SMAP SSS 8-day running mean (rain-filtered field) and publishes a compact
global salinity grid to R2, which the SAR pass render overlays as an honest
reliability cue.

Source (anonymous HTTPS, no key): RSS SMAP SSS V06.0 FINAL L3 8-day running,
``sss_smap_RF`` (rain-filtered), 0.25 deg global, ~3-4 week latency (fine —
salinity is slowly varying and this is only a reliability mask). Credit RSS.
"""
from __future__ import annotations

import datetime as dt
import re

from . import fetch

BASE = "https://data.remss.com/smap/SSS/V06.0/FINAL/L3/8day_running/"
_FNAME = re.compile(
    r"RSS_smap_SSS_L3_8day_running_(\d{4})_(\d{3})_FNL_v06\.0\.nc")
# below this SSS (PSU) the C-band SAR wind retrieval is flagged less reliable
LOW_SSS_PSU = 33.0


def _year_url(year: int) -> str:
    return f"{BASE}{year}/"


def find_latest() -> tuple[int, int] | None:
    """(year, doy) of the newest published 8-day file, or None. Walks back a
    couple of years so a Jan run still finds December's file."""
    now = dt.datetime.now(dt.timezone.utc)
    for year in (now.year, now.year - 1):
        html = fetch.get_text(_year_url(year))
        if not html:
            continue
        doys = sorted({int(m.group(2)) for m in _FNAME.finditer(html)
                       if int(m.group(1)) == year})
        if doys:
            return year, doys[-1]
    return None


def file_url(year: int, doy: int) -> str:
    return (f"{_year_url(year)}RSS_smap_SSS_L3_8day_running_"
            f"{year}_{doy:03d}_FNL_v06.0.nc")


def fetch_grid(year: int, doy: int):
    """Download one 8-day file -> (lats, lons, sss[lat,lon]) with the
    rain-filtered SSS in PSU and fill/land masked to NaN. None on failure."""
    import netCDF4
    import numpy as np
    raw = fetch.get_bytes(file_url(year, doy), timeout=180)
    if not raw:
        return None
    ds = netCDF4.Dataset("inmem", memory=raw)
    try:
        sss = np.array(ds.variables["sss_smap_RF"][:], dtype=float)
        if sss.ndim == 3:                        # squeeze the length-1 time dim
            sss = sss[0]
        fill = getattr(ds.variables["sss_smap_RF"], "_FillValue", -9999.0)
        sss = np.where((sss <= fill) | (sss < 0) | (sss > 60) |
                       ~np.isfinite(sss), np.nan, sss)
        lats = np.array(ds.variables["lat"][:], dtype=float)
        lons = np.array(ds.variables["lon"][:], dtype=float)
        return lats, lons, sss
    finally:
        ds.close()


def _write_nc(ds, lats, lons, sss) -> None:
    """Populate an open write-mode Dataset with the compact grid."""
    import numpy as np
    ds.createDimension("lat", lats.size)
    ds.createDimension("lon", lons.size)
    ds.createVariable("lat", "f4", ("lat",))[:] = lats
    ds.createVariable("lon", "f4", ("lon",))[:] = lons
    v = ds.createVariable("sss_rf", "f4", ("lat", "lon"),
                          fill_value=np.float32(np.nan), zlib=True, complevel=4)
    v[:] = sss.astype("f4")
    ds.credit = "RSS SMAP SSS V06.0 (Remote Sensing Systems)"


def _pack(lats, lons, sss) -> bytes:
    """Compact single-var NetCDF (sss_rf + lat/lon) for the render to consume,
    NaN fill preserved -> ~1-4 MB at 0.25 deg. Prefers an in-memory close;
    falls back to a temp file on builds without ``close_memory``."""
    import netCDF4
    ds = netCDF4.Dataset("inmem", mode="w", memory=1 << 20, format="NETCDF4")
    try:
        _write_nc(ds, lats, lons, sss)
        return ds.close_memory().tobytes()       # available on newer builds
    except Exception:                            # noqa: BLE001 - no close_memory
        try:
            ds.close()
        except Exception:                        # noqa: BLE001
            pass
    import os
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".nc")
    os.close(fd)
    try:
        d2 = netCDF4.Dataset(path, mode="w", format="NETCDF4")
        try:
            _write_nc(d2, lats, lons, sss)
        finally:
            d2.close()
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

def read_grid(nc_bytes: bytes):
    """Compact mask.nc -> (lats, lons, sss[lat,lon])."""
    import netCDF4
    import numpy as np
    ds = netCDF4.Dataset("inmem", memory=nc_bytes)
    try:
        return (np.array(ds.variables["lat"][:], dtype=float),
                np.array(ds.variables["lon"][:], dtype=float),
                np.array(ds.variables["sss_rf"][:], dtype=float))
    finally:
        ds.close()


def build(store, *, log=print) -> dict:
    """Fetch the newest 8-day SSS and publish the compact grid + meta to R2
    (watermark-gated: an unchanged DOY is a no-op). Returns a summary."""
    import json
    latest = find_latest()
    if not latest:
        log("salinity: no SMAP SSS file listed (source unreachable) - no-op")
        return {"published": False, "reason": "no-source"}
    year, doy = latest
    date = (dt.date(year, 1, 1) + dt.timedelta(days=doy - 1)).isoformat()
    prior = store.get_json("sar/salinity/meta.json") or {}
    if prior.get("year") == year and prior.get("doy") == doy:
        log(f"salinity: {year} DOY {doy} already published - no-op")
        return {"published": False, "year": year, "doy": doy}
    grid = fetch_grid(year, doy)
    if grid is None:
        log(f"salinity: fetch failed for {year} DOY {doy} (retry next run)")
        return {"published": False, "reason": "fetch-failed"}
    lats, lons, sss = grid
    body = _pack(lats, lons, sss)
    store.put("sar/salinity/mask.nc", body, "application/x-netcdf",
              "public, max-age=3600")
    meta = {"year": year, "doy": doy, "date": date, "low_sss_psu": LOW_SSS_PSU,
            "generated_utc": dt.datetime.now(dt.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
            "source": "RSS SMAP SSS V06.0 8-day running (rain-filtered)",
            "credit": "Remote Sensing Systems (RSS)"}
    store.put("sar/salinity/meta.json",
              json.dumps(meta, separators=(",", ":")).encode(),
              "application/json", "public, max-age=3600")
    log(f"salinity: published {year} DOY {doy} ({date}), {body.__len__()} B")
    return {"published": True, "year": year, "doy": doy, "date": date}
