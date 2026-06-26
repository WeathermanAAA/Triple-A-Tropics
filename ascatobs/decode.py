"""ascatobs.decode - decode an ASCAT L2 12.5 km coastal NetCDF into a pass dict.

Schema + conventions are pinned to the OSI SAF / KNMI *ASCAT Wind Product User
Manual* v1.16 (SAF/OSI/CDOP/KNMI/TEC/MA/126):

  vars (dims NUMROWS x NUMCELLS): lat (degrees_north), lon (degrees_east),
    wind_speed (m s-1), wind_dir (degree), wvc_quality_flag (int bitmask),
    time (seconds since 1990-01-01).
  * wind_dir is OCEANOGRAPHIC ("0 deg = flowing to North") in the NetCDF product
    (the manual is explicit, and the file's :comment repeats it). Wind BARBS
    point FROM the wind source (meteorological), so we store FROM-direction:
        from_dir = (wind_dir + 180) mod 360.
  * Quality: a cell is GOOD when none of the rejection bits are set. We reject
    knmi_quality_control_fails (bit 17, the manual's catch-all that also catches
    rain via the inversion residual), variational_quality_control_fails (16),
    product_monitoring_event_flag (18) and rain_detected (9). We do NOT reject
    over-land (bit 15) - keeping near-coast cells is the whole point of the
    coastal product. Low/high-wind bits (11/12) are informational, not rejects.
  * Packing: scale_factor / add_offset / _FillValue are applied automatically by
    netCDF4 (auto maskandscale, default on) - speeds come back as float m/s,
    masked where fill. We convert m/s -> kt (x1.94384).
  * The NetCDF carries only the SELECTED (de-aliased) wind solution - no
    ambiguity dimension to choose from.

The C-band scatterometer is relatively rain-insensitive but loses sensitivity
above ~25 m/s, so it UNDERESTIMATES extreme TC-core winds (good for the broad
gale/wind field, not peak intensity) - the viewer discloses this.

Output (one "pass" = one ~100-min orbit file):
  {
    "sat": "metopb", "sensor": "ASCAT-B",
    "start_utc","end_utc","mid_utc": ISO-Z (true overpass times from `time`),
    "bbox": [w,e,s,n], "n_wvc": int, "max_kt": float, "stride": int,
    "wvc": {"la":[...], "lo":[...], "kt":[...], "dir":[...]},  # dir = FROM, deg
    "path": [{"lat","lon","t"} ...],   # ~per-row centreline anchors (small),
                                       # for storm association + orbit context
  }
"""
from __future__ import annotations

import datetime as _dt

import numpy as np

MS_TO_KT = 1.94384
SENSOR_LABEL = {"metopb": "ASCAT-B", "metopc": "ASCAT-C"}
_EPOCH = _dt.datetime(1990, 1, 1, tzinfo=_dt.timezone.utc)

# Rejection bits (manual v1.16 flag_masks). Reject a cell if ANY are set.
QC_KNMI = 1 << 17          # knmi_quality_control_fails (catch-all incl. rain residual)
QC_VAR = 1 << 16           # variational_quality_control_fails
QC_MON = 1 << 18           # product_monitoring_event_flag
QC_RAIN = 1 << 9           # rain_detected
QC_REJECT = QC_KNMI | QC_VAR | QC_MON | QC_RAIN


def quality_mask(qc) -> "np.ndarray":
    """Boolean GOOD mask (True = keep) from wvc_quality_flag, per the manual's
    recommended rejection set. A masked/fill flag value counts as bad."""
    q = np.ma.asarray(qc)
    flag = np.ma.filled(q, QC_REJECT).astype("int64")   # fill -> rejected
    good = (flag & QC_REJECT) == 0
    if np.ma.is_masked(q):
        good &= ~np.ma.getmaskarray(q)
    return np.asarray(good)


def _iso(t: "_dt.datetime") -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _times_to_dt(time_arr):
    """seconds-since-1990 -> datetime ndarray (object). Masked -> None."""
    t = np.ma.asarray(time_arr, dtype="float64")
    out = np.empty(t.shape, dtype=object)
    flat_v = np.ma.filled(t, np.nan).ravel()
    flat_o = out.ravel()
    for i, v in enumerate(flat_v):
        if not np.isfinite(v):
            flat_o[i] = None
        else:
            flat_o[i] = _EPOCH + _dt.timedelta(seconds=float(v))
    return out


def _open(path):
    import netCDF4                                   # local import (heavy dep)
    return netCDF4.Dataset(path, "r")


def _read(ds, name):
    if name not in ds.variables:
        raise KeyError(f"ascat: variable '{name}' missing from NetCDF")
    v = ds.variables[name]
    try:
        v.set_auto_maskandscale(True)
    except Exception:                                # noqa: BLE001
        pass
    return v[:]


def decode(path, *, sat: str | None = None, stride: int = 2,
           max_points: int = 60000) -> dict:
    """Decode one ASCAT coastal NetCDF into a pass dict (see module docstring).
    ``stride`` decimates along BOTH axes (every Nth row and cell ~25 km at
    stride 2); if that still exceeds ``max_points`` the stride is widened until it
    fits, so a single pass JSON stays bounded. ``sat`` ('metopb'/'metopc')
    overrides the sensor label; otherwise it is read from the file if present.
    Raises on a structurally bad file (the caller guards)."""
    ds = _open(path)
    try:
        lat = np.ma.asarray(_read(ds, "lat"), dtype="float64")
        lon = np.ma.asarray(_read(ds, "lon"), dtype="float64")
        spd_ms = np.ma.asarray(_read(ds, "wind_speed"), dtype="float64")
        wdir = np.ma.asarray(_read(ds, "wind_dir"), dtype="float64")
        qc = _read(ds, "wvc_quality_flag")
        times = _times_to_dt(_read(ds, "time")) if "time" in ds.variables else None
        if sat is None:
            sat = _sat_from_attrs(ds)
    finally:
        ds.close()

    if lat.ndim != 2:
        raise ValueError(f"ascat: expected 2-D lat (NUMROWS,NUMCELLS), got {lat.shape}")
    nrows, ncells = lat.shape

    good = quality_mask(qc)
    # also require finite geolocation + speed/direction (NaN where fill)
    finite = (~np.ma.getmaskarray(np.ma.masked_invalid(lat))
              & ~np.ma.getmaskarray(np.ma.masked_invalid(lon))
              & np.isfinite(np.ma.filled(spd_ms, np.nan))
              & np.isfinite(np.ma.filled(wdir, np.nan)))
    good = good & finite

    # widen stride until the kept-point count fits max_points (bounds JSON size)
    s = max(1, int(stride))
    while True:
        sel = np.zeros((nrows, ncells), dtype=bool)
        sel[::s, ::s] = True
        keep = good & sel
        if int(keep.sum()) <= max_points or s >= 16:
            break
        s += 1

    la = np.round(np.asarray(lat[keep], dtype="float64"), 2)
    lo = np.round(_wrap180(np.asarray(lon[keep], dtype="float64")), 2)
    kt = np.asarray(spd_ms[keep], dtype="float64") * MS_TO_KT
    # oceanographic (going-to) -> meteorological FROM, for barbs
    frm = np.mod(np.asarray(wdir[keep], dtype="float64") + 180.0, 360.0)

    wvc = {
        "la": la.tolist(),
        "lo": lo.tolist(),
        "kt": [int(round(x)) for x in kt.tolist()],
        "dir": [int(round(x)) % 360 for x in frm.tolist()],
    }
    n = len(wvc["la"])

    # bbox over kept cells (dateline-aware): if the kept lons straddle the seam
    # widely, keep [-180,180]; else a tight bbox.
    bbox = _bbox(lo, la) if n else [-180.0, 180.0, -80.0, 80.0]

    # centreline path (~1 anchor per `path_step` rows) for association + context
    path, t_lo, t_hi = _centreline(lat, lon, good, times, nrows, ncells)

    start = _iso(t_lo) if t_lo else None
    end = _iso(t_hi) if t_hi else None
    mid = _iso(t_lo + (t_hi - t_lo) / 2) if (t_lo and t_hi) else None

    return {
        "sat": sat or "metop", "sensor": SENSOR_LABEL.get(sat or "", "ASCAT"),
        "start_utc": start, "end_utc": end, "mid_utc": mid,
        "bbox": bbox, "n_wvc": n,
        "max_kt": (round(float(max(wvc["kt"])), 1) if n else 0.0),
        "stride": s, "wvc": wvc, "path": path,
    }


def _sat_from_attrs(ds) -> str | None:
    for attr in ("platform", "source", "satellite_identifier"):
        v = getattr(ds, attr, None)
        if isinstance(v, str):
            lv = v.lower()
            if "metop-b" in lv or "metopb" in lv or "metop_b" in lv:
                return "metopb"
            if "metop-c" in lv or "metopc" in lv or "metop_c" in lv:
                return "metopc"
    return None


def _wrap180(lon):
    return ((np.asarray(lon) + 180.0) % 360.0) - 180.0


def _bbox(lo, la):
    """Tight [w,e,s,n] over kept points; dateline-aware. If points span the seam
    (gap in the sorted longitudes wider than the complementary span) the bbox is
    expressed as w>e (wrapping), matching TATRegions' wrapping-extent convention."""
    s = float(np.min(la)); n = float(np.max(la))
    lons = np.sort(np.asarray(lo))
    w = float(lons[0]); e = float(lons[-1])
    if lons.size > 2:
        gaps = np.diff(lons)
        gi = int(np.argmax(gaps))
        biggest = float(gaps[gi])
        wrap_gap = (lons[0] + 360.0) - lons[-1]
        if biggest > wrap_gap and biggest > 60.0:
            # the data clusters on both sides of a wide interior gap: wrap across it
            w = float(lons[gi + 1]); e = float(lons[gi])
    pad_lat = max(0.5, (n - s) * 0.06)
    return [round(w, 2), round(e, 2), round(s - pad_lat, 2), round(n + pad_lat, 2)]


def _centreline(lat, lon, good, times, nrows, ncells):
    """A small (lat,lon,t) anchor list down the swath centre - one per ~max(1,
    nrows//120) rows - taken from a valid cell near the centre column. Returns
    (path, t_lo, t_hi). Used for storm association (per-storm overpass time =
    nearest anchor) and an optional faint orbit line."""
    cc = ncells // 2
    step = max(1, nrows // 120)
    path = []
    t_lo = t_hi = None
    for r in range(0, nrows, step):
        row_good = good[r]
        if not row_good.any():
            continue
        # nearest valid cell to the centre column
        cols = np.where(row_good)[0]
        c = int(cols[np.argmin(np.abs(cols - cc))])
        la = float(lat[r, c]); lo = float(_wrap180(np.array([lon[r, c]]))[0])
        if not (np.isfinite(la) and np.isfinite(lo)):
            continue
        t = times[r, c] if times is not None else None
        if t is not None:
            if t_lo is None or t < t_lo:
                t_lo = t
            if t_hi is None or t > t_hi:
                t_hi = t
        path.append({"lat": round(la, 2), "lon": round(lo, 2),
                     "t": _iso(t) if t else None})
    return path, t_lo, t_hi
