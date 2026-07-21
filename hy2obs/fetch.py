"""hy2obs.fetch - anonymous S3 listing/fetch + granule decode with QC mask.

The upstream KNMI-to-CMEMS chain stalls at times (multi-day, per-satellite;
in outage since 2026-07-16 as of writing) - every listing miss is a guarded
skip, never an error, and the build self-recovers when files resume.
"""
from __future__ import annotations

import datetime as dt
import re
import urllib.request

S3 = "https://s3.waw3-1.cloudferro.com/mdl-native-04"
PRODUCT = "native/WIND_GLO_PHY_L3_NRT_012_002"
SATS = ("hy2b", "hy2c")                 # hy2d suspended upstream - skipped
DIRS = ("asc", "des")
# QC bits masked before plotting: rain (Ku-band reads high), land,
# not-usable-for-visualisation
MASK_BITS = 512 | 32768 | 1024
_KEY = re.compile(r"<Key>([^<]+)</Key>")
_UA = {"User-Agent": "triple-a-tropics-hy2/1.0"}


def dataset_id(sat: str, direction: str) -> str:
    return (f"cmems_obs-wind_glo_phy_nrt_l3-{sat}-hscat-{direction}"
            f"-0.25deg_P1D-i_202311")


def _get(url: str, timeout: float = 120.0) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:                    # noqa: BLE001 — guarded skip
        return None


def list_month(sat: str, direction: str, year: int, month: int) -> list:
    """Object keys for one dataset month (anonymous ListObjectsV2)."""
    url = (f"{S3}?list-type=2&prefix={PRODUCT}/"
           f"{dataset_id(sat, direction)}/{year}/{month:02d}/")
    raw = _get(url, timeout=60)
    return sorted(_KEY.findall(raw.decode())) if raw else []


def newest_granule(sat: str, direction: str, *, max_age_days: int = 6,
                   now: dt.datetime | None = None):
    """(date, key) of the newest granule within max_age_days, else None
    (feed gap: skip, do not error). Scans this month + last at rollover."""
    now = now or dt.datetime.now(dt.timezone.utc)
    months = {(now.year, now.month)}
    prev = (now.replace(day=1) - dt.timedelta(days=1))
    months.add((prev.year, prev.month))
    keys = []
    for y, m in sorted(months):
        keys += list_month(sat, direction, y, m)
    best = None
    for k in keys:
        m = re.search(r"_(\d{8})\.nc$", k)
        if not m:
            continue
        d = dt.datetime.strptime(m.group(1), "%Y%m%d") \
            .replace(tzinfo=dt.timezone.utc)
        if (now - d).days <= max_age_days and (best is None or d > best[0]):
            best = (d, k)
    return best


def fetch_key(key: str) -> bytes | None:
    return _get(f"{S3}/{key}", timeout=240)


def decode(nc_bytes: bytes) -> dict:
    """Granule -> dict of QC-masked arrays on the 0.25-deg grid:
    {lats, lons, speed (m/s), u, v (m/s), tmin, tmax (datetimes)}.
    Cells failing the rain/land/not-usable mask are NaN."""
    import netCDF4
    import numpy as np
    ds = netCDF4.Dataset("inmem", memory=nc_bytes)
    try:
        def var(name):
            v = ds.variables[name][:]        # scale/fill auto-applied
            return np.ma.filled(np.squeeze(v).astype(float), np.nan)
        lats = np.array(ds.variables["lat"][:], dtype=float)
        lons = np.array(ds.variables["lon"][:], dtype=float)
        speed = var("wind_speed")
        u = var("eastward_wind")
        v = var("northward_wind")
        flags = np.squeeze(ds.variables["wvc_quality_flag"][:])
        flags = np.ma.filled(flags, 0).astype(np.int64)
        bad = (flags & MASK_BITS) != 0
        for a in (speed, u, v):
            a[bad] = np.nan
        t = ds.variables["measurement_time"]
        tv = np.ma.masked_invalid(np.squeeze(t[:]).astype(float))
        tv = np.ma.masked_where(np.isnan(speed), tv)
        epoch = dt.datetime(1990, 1, 1, tzinfo=dt.timezone.utc)
        tmin = tmax = None
        if tv.count():
            tmin = epoch + dt.timedelta(seconds=float(tv.min()))
            tmax = epoch + dt.timedelta(seconds=float(tv.max()))
        return {"lats": lats, "lons": lons, "speed": speed, "u": u,
                "v": v, "tmin": tmin, "tmax": tmax}
    finally:
        ds.close()
