"""
Ensemble cyclone-center detection on a global MSLP field.

Reproduces the method behind Andrew's "ECMWF EPS - Ensemble Cyclone Centers"
plot ("MSLP minima from EPS GRIB2, closed-circulation filter"):

  1. LOCAL MINIMA via ``scipy.ndimage.minimum_filter`` (field == filtered marks
     a local min within a footprint of ~a few hundred km). A separable box
     filter is used so per-axis boundary modes apply: latitude is clamped,
     longitude WRAPS so minima near +/-180 are found correctly.
  2. CLOSED-CIRCULATION FILTER: keep a minimum only if MSLP rises by at least a
     threshold (hPa) in EVERY radial direction within a search radius - i.e. a
     closed isobar of value Pc+threshold encircles it. This rejects open troughs
     and high-latitude monotonic-gradient noise.
  3. ANTIMERIDIAN-SAFE: longitude indices wrap modulo nlon, and the reported
     longitude is normalized to [-180, 180), so WPAC systems near +/-180 are not
     mislocated.
  4. P->V via Atkinson-Holliday: ``vmax_kt = 6.7 * (1010 - Pc_hPa) ** 0.644``
     (environmental pressure 1010 hPa).

Pure numpy + scipy. No I/O, no GRIB. ``detect_centers`` takes a 2-D hPa field
and returns plain-Python dicts ready for JSON.

This module is model-agnostic: the same detector serves ECMWF ENS, AIFS-ENS,
GEFS and the GDM models. Tuning knobs are passed in by the caller from each
model's registry entry (see :mod:`enscenters.registry`).
"""
from __future__ import annotations

import math
from typing import List, Optional

import numpy as np
from scipy.ndimage import label, minimum_filter, minimum_position

# Atkinson-Holliday wind-pressure relation (environmental pressure 1010 hPa).
AH_ENV_PRESSURE_HPA = 1010.0
AH_COEFF = 6.7
AH_EXP = 0.644


def ah_vmax_kt(pc_hpa: float, env: float = AH_ENV_PRESSURE_HPA) -> float:
    """Atkinson-Holliday peak 1-min wind (kt) from central pressure (hPa).

    Returns 0.0 when the central pressure is at or above the environmental
    pressure (no meaningful circulation).
    """
    deficit = env - pc_hpa
    if deficit <= 0:
        return 0.0
    return AH_COEFF * (deficit ** AH_EXP)


def _normalize_lon(lon: float) -> float:
    """Wrap a longitude to [-180, 180)."""
    return ((lon + 180.0) % 360.0) - 180.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(((lon2 - lon1 + 180) % 360) - 180)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def detect_centers(
    mslp_hpa: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    *,
    min_footprint_deg: float = 2.5,
    closed_threshold_hpa: float = 2.0,
    search_radius_km: float = 500.0,
    n_azimuth: int = 16,
    n_radial: int = 12,
    dedup_km: float = 250.0,
    lat_limit: Optional[float] = 75.0,
    max_central_hpa: float = 1015.0,
) -> List[dict]:
    """Detect closed-low cyclone centers in a global MSLP field.

    Parameters
    ----------
    mslp_hpa : 2-D array ``[nlat, nlon]`` in hPa.
    lats : 1-D ``[nlat]`` degrees (ascending or descending, evenly spaced).
    lons : 1-D ``[nlon]`` degrees (0..360 or -180..180, evenly spaced).
    min_footprint_deg : local-min footprint radius in degrees (~few hundred km).
    closed_threshold_hpa : required MSLP rise in all directions (closed isobar).
    search_radius_km : radius over which the closed test looks for that rise.
    n_azimuth, n_radial : sampling density of the closed-circulation test.
    dedup_km : non-max-suppression radius for plateaued/adjacent minima.
    lat_limit : drop centers poleward of this ``|lat|`` (polar grid-noise guard);
        ``None`` disables.
    max_central_hpa : ignore "minima" higher (weaker) than this; trims clutter.

    Returns
    -------
    list of dict ``{lat, lon, mslp_hpa, vmax_kt}``, sorted by ascending mslp,
    all values plain Python floats.
    """
    field = np.asarray(mslp_hpa, dtype=float)
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    nlat, nlon = field.shape

    dlat_deg = abs(float(lats[1] - lats[0]))
    dlon_deg = abs(float(lons[1] - lons[0]))
    lat_desc = lats[0] > lats[-1]
    lon0_grid = float(lons[0])

    # --- 1. local minima -------------------------------------------------
    radius_px = max(1, round(min_footprint_deg / max(dlat_deg, 1e-6)))
    box = 2 * radius_px + 1
    # Separable box filter so per-axis modes work: clamp latitude (axis 0),
    # wrap longitude (axis 1) for antimeridian-safe minima near +/-180.
    filt = minimum_filter(field, size=box, mode=("nearest", "wrap"))
    cand_mask = (field == filt) & (field <= max_central_hpa)
    # Collapse plateaus / adjacent ties (minimum_filter marks every pixel of a
    # flat minimum region) to ONE representative per connected component - the
    # deepest pixel in it. This keeps distinct lows separate while preventing a
    # flat trough floor from exploding into hundreds of candidates.
    # NOTE: label() does NOT wrap longitude, so a flat-bottomed low straddling
    # the +/-180 seam splits into 2 components; the haversine NMS below (dedup_km)
    # remerges them - reliable while dedup_km exceeds the plateau width at the
    # seam (a few tens of km), which it is by a wide margin.
    lbl, n_comp = label(cand_mask, structure=np.ones((3, 3), dtype=int))
    if n_comp == 0:
        return []
    pos = minimum_position(field, lbl, index=np.arange(1, n_comp + 1))
    if n_comp == 1:
        pos = [pos]
    ii = np.array([p[0] for p in pos])
    jj = np.array([p[1] for p in pos])

    # --- 2. closed-circulation filter -----------------------------------
    azimuths = np.linspace(0.0, 2.0 * math.pi, n_azimuth, endpoint=False)
    radii_km = np.linspace(search_radius_km / n_radial, search_radius_km, n_radial)

    def lat_to_idx(lat: float) -> int:
        if lat_desc:
            idx = round((lats[0] - lat) / dlat_deg)
        else:
            idx = round((lat - lats[0]) / dlat_deg)
        return int(min(max(idx, 0), nlat - 1))

    def lon_to_idx(lon: float) -> int:
        # works for 0..360 and -180..180 grids; wraps modulo nlon
        off = (lon - lon0_grid) / dlon_deg
        return int(round(off)) % nlon

    keep = []
    for i, j in zip(ii, jj):
        pc = float(field[i, j])
        lat0 = float(lats[i])
        if lat_limit is not None and abs(lat0) > lat_limit:
            continue
        target = pc + closed_threshold_hpa
        coslat = max(math.cos(math.radians(lat0)), 0.05)
        lon_j = float(lons[j])
        closed = True
        for az in azimuths:
            sin_az, cos_az = math.sin(az), math.cos(az)
            reached = False
            for d in radii_km:
                la = lat0 + (d * cos_az) / 111.0
                lo = lon_j + (d * sin_az) / (111.0 * coslat)
                if abs(la) > 89.5:
                    # Ray left the grid at the pole. Do NOT credit this as closed
                    # (crediting it would pass an OPEN monotonic polar gradient,
                    # whose equatorward rays rise while poleward rays run off the
                    # pole). The poleward side must close before the pole, else
                    # the center is rejected. lat_limit is the primary polar
                    # guard; this keeps the closed test honest if it is relaxed.
                    break
                if field[lat_to_idx(la), lon_to_idx(lo)] >= target:
                    reached = True
                    break
            if not reached:
                closed = False
                break
        if closed:
            keep.append((pc, lat0, _normalize_lon(lon_j)))

    # --- 3. non-max suppression (plateau / adjacent minima) -------------
    keep.sort(key=lambda t: t[0])  # ascending pressure (deepest first)
    accepted = []
    for pc, lat, lon in keep:
        ok = True
        for _, la, lo in accepted:
            if _haversine_km(lat, lon, la, lo) < dedup_km:
                ok = False
                break
        if ok:
            accepted.append((pc, lat, lon))

    return [
        {
            "lat": round(float(lat), 2),
            "lon": round(float(lon), 2),
            "mslp_hpa": round(float(pc), 1),
            "vmax_kt": round(ah_vmax_kt(pc), 1),
        }
        for pc, lat, lon in accepted
    ]
