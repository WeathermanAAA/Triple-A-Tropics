"""
Warm-core (tropical-only) filter for SELF-DETECTED ensemble cyclone centers.

The MSLP detector (:mod:`enscenters.detect`) keeps EVERY closed low, so the
extratropical storm-track band swamps the tropical systems. This module applies
the community-standard upper-level THICKNESS warm-core test - latitude / SST /
wind alone are unreliable discriminators:

  A tropical (warm-core) low has a MAXIMUM in upper-level geopotential thickness
  (THK = gh300 - gh500) collocated with the surface low: the warm column is
  thicker aloft. A cold-core extratropical low has a thickness MINIMUM aloft.

  We test this on the thickness PERTURBATION, not raw THK. Raw thickness fails on
  real fields two ways (verified): (1) the large-scale climatological gradient
  (thick warm tropics -> thin cold poles) is steeper than a storm's core, so the
  closed-contour test breaks on the warm side of a genuine tropical system; and
  (2) the cold extratropics are full of shallow LOCAL thickness maxima (fronts,
  warm seclusions) that pass a raw closure test - so a 47S Southern-Ocean storm
  sneaks through. The community-standard discriminator (cf. Hart's cyclone phase
  space) removes the smooth large-scale background and tests the storm-scale
  ANOMALY. So: keep a detected MSLP center only if the thickness anomaly has a
  local max within ~1 deg of it whose amplitude is >= ~6 m (the 58.8 m^2/s^2 / g
  warm-core threshold) and which is ENCLOSED by a closed contour falling >= ~6 m
  within ~6.5 deg. A pre-warm-core disturbance has no such closed anomaly max yet,
  so it correctly does not appear until it develops a core; an extratropical low
  sits near its latitude background (anomaly ~0) and is dropped.

Plus two cheap per-step AND-gates (this is per-step detection, NOT tracks, so no
persistence/track-stitch gate): drop |lat| > 50 deg, and drop centers over high
terrain (thermal lows over plateaus; bundled coarse orography). Cheap gates run
first so the expensive closure test only sees plausible candidates.

Reusable across every model that SELF-DETECTS MSLP (ECMWF ENS now, AIFS-ENS
later). The already-TC-only models (GEFS genesis, GDM-FNV3/GenCast, ECMWF BUFR
tf) ingest TC tracks directly and never call this.
"""
from __future__ import annotations

import math
import os
from typing import List, Optional

import numpy as np

_OROG = None  # lazily-loaded (elev_int16, lats, lons)


def _orography():
    global _OROG
    if _OROG is None:
        d = np.load(os.path.join(os.path.dirname(__file__), "orography.npz"))
        _OROG = (d["elev"], d["lats"].astype(float), d["lons"].astype(float))
    return _OROG


def thickness(gh300: np.ndarray, gh500: np.ndarray) -> np.ndarray:
    """300-500 hPa geopotential-thickness field (gpm). Warm-core proxy."""
    return np.asarray(gh300, dtype=float) - np.asarray(gh500, dtype=float)


def thickness_anomaly(thk: np.ndarray, dlat: float, dlon: float, bg_box_deg: float) -> np.ndarray:
    """Storm-scale thickness PERTURBATION: THK minus a ``bg_box_deg`` boxcar mean
    (latitude clamped, longitude wrapped). The wide mean captures the smooth
    climatological gradient (warm tropics / cold poles) so a compact warm core
    survives as a positive anomaly while a low sitting at its latitude background
    nets ~0. One cheap O(N) separable filter per step."""
    from scipy.ndimage import uniform_filter
    thk = np.asarray(thk, dtype=float)
    bx_lat = max(3, 2 * int(round(bg_box_deg / max(dlat, 1e-6))) + 1)
    bx_lon = max(3, 2 * int(round(bg_box_deg / max(dlon, 1e-6))) + 1)
    bg = uniform_filter(thk, size=(bx_lat, bx_lon), mode=("nearest", "wrap"))
    return thk - bg


def elevation_at(lat: float, lon: float) -> float:
    """Surface elevation (m) at a point, from the bundled ECMWF orography."""
    elev, lats, lons = _orography()
    dlat = abs(lats[1] - lats[0])
    i = int(round((lats[0] - lat) / dlat)) if lats[0] > lats[-1] else int(round((lat - lats[0]) / dlat))
    i = min(max(i, 0), len(lats) - 1)
    j = int(round((lon - lons[0]) / abs(lons[1] - lons[0]))) % len(lons)
    return float(elev[i, j])


def is_warm_core(
    lat0: float, lon0: float, anom: np.ndarray, lats: np.ndarray, lons: np.ndarray,
    *,
    search_max_deg: float = 1.0,
    warm_anom_min_m: float = 6.0,
    closed_drop_m: float = 6.0,
    closed_radius_deg: float = 6.5,
    n_azimuth: int = 16,
    n_radial: int = 12,
) -> bool:
    """True iff the surface low SITS ON a closed warm thickness-anomaly core: the
    anomaly at the MSLP center is >= ``warm_anom_min_m`` (a genuine warm core, not
    a cold low with a warm pixel merely NEAR it), and the nearby anomaly peak
    (within ``search_max_deg``, allowing slight vertical tilt) is enclosed by a
    closed contour falling >= ``closed_drop_m`` in EVERY azimuth within
    ``closed_radius_deg``. A cold-core / near-background low fails the collocated
    amplitude gate; an open gradient fails closure."""
    anom = np.asarray(anom, dtype=float)
    nlat, nlon = anom.shape
    dlat = abs(float(lats[1] - lats[0]))
    dlon = abs(float(lons[1] - lons[0]))
    lat_desc = lats[0] > lats[-1]
    lon0g = float(lons[0])

    def li(lat: float) -> int:
        idx = round((lats[0] - lat) / dlat) if lat_desc else round((lat - lats[0]) / dlat)
        return int(min(max(idx, 0), nlat - 1))

    def lj(lon: float) -> int:
        return int(round((lon - lon0g) / dlon)) % nlon

    # 1) COLLOCATED amplitude gate: the MSLP center itself must sit on a warm
    #    anomaly >= warm_anom_min_m. (Testing the center, not just any pixel within
    #    1 deg, is what rejects a cold low that merely has a warm feature nearby.)
    ci, cj = li(lat0), lj(lon0)
    if float(anom[ci, cj]) < warm_anom_min_m:
        return False

    # 2) closure reference = the anomaly peak within search_max_deg (small tilt).
    rpx = max(1, int(round(search_max_deg / dlat)))
    i_lo, i_hi = max(0, ci - rpx), min(nlat, ci + rpx + 1)
    jcols = (np.arange(cj - rpx, cj + rpx + 1)) % nlon   # wrap longitude
    sub = anom[i_lo:i_hi][:, jcols]
    a0, a1 = np.unravel_index(int(np.argmax(sub)), sub.shape)
    mi, mj = i_lo + int(a0), int(jcols[a1])
    peak = float(anom[mi, mj])
    mlat, mlon = float(lats[mi]), float(lons[mj])

    # 2) closed warm core: the anomaly must fall >= closed_drop_m within
    #    closed_radius_deg in EVERY direction (a closed anomaly contour).
    target = peak - closed_drop_m
    coslat = max(math.cos(math.radians(mlat)), 0.05)
    azimuths = np.linspace(0.0, 2.0 * math.pi, n_azimuth, endpoint=False)
    radii = np.linspace(closed_radius_deg / n_radial, closed_radius_deg, n_radial)
    for az in azimuths:
        s, c = math.sin(az), math.cos(az)
        reached = False
        for rd in radii:
            la = mlat + rd * c
            lo = mlon + rd * s / coslat
            if abs(la) > 89.5:
                break  # ran off the grid before closing -> treat as not closed
            if anom[li(la), lj(lo)] <= target:
                reached = True
                break
        if not reached:
            return False
    return True


def filter_centers(
    centers: List[dict], thk: Optional[np.ndarray], lats: np.ndarray, lons: np.ndarray,
    *,
    max_lat: float = 50.0,
    terrain_max_m: Optional[float] = 1000.0,
    bg_box_deg: float = 10.0,
    warm_anom_min_m: float = 6.0,
    search_max_deg: float = 1.0,
    closed_drop_m: float = 6.0,
    closed_radius_deg: float = 6.5,
    n_azimuth: int = 16,
    n_radial: int = 12,
    subtrop_lat: float = 30.0,
    subtrop_warm_anom_min_m: float = 12.0,
    subtrop_closed_drop_m: float = 12.0,
    subtrop_closed_radius_deg: float = 4.5,
) -> List[dict]:
    """Keep only warm-core tropical centers from ``detect_centers`` output. Cheap
    AND-gates (|lat| > max_lat, then high-terrain) run first; survivors face the
    thickness-anomaly closure test (anomaly field built ONCE per step), LATITUDE-
    GRADED: poleward of ``subtrop_lat`` a center must show a stronger, more compact
    closed warm core (``subtrop_*``) - this rejects broad subtropical/hybrid lows
    while keeping compact recurving TCs.

    FALLBACK when ``thk`` is None (gh/thickness unavailable for this step): do NOT
    pass the full storm track. Apply only the cheap lat+terrain gates, so the
    >max_lat storm-track band is still dropped (a STRICT lossy fallback, not the old
    fail-open that splattered extratropical noise). The caller logs how often this
    fires (see pipeline.process_member / build_one_cycle)."""
    if not centers:
        return centers
    if thk is None:
        return [c for c in centers
                if abs(c["lat"]) <= max_lat
                and not (terrain_max_m is not None and elevation_at(c["lat"], c["lon"]) > terrain_max_m)]
    dlat = abs(float(lats[1] - lats[0]))
    dlon = abs(float(lons[1] - lons[0]))
    anom = thickness_anomaly(thk, dlat, dlon, bg_box_deg)
    kept = []
    for ctr in centers:
        lat, lon = ctr["lat"], ctr["lon"]
        if abs(lat) > max_lat:
            continue
        if terrain_max_m is not None and elevation_at(lat, lon) > terrain_max_m:
            continue
        if abs(lat) > subtrop_lat:      # subtropics/midlatitudes: strict compact core
            wa, cd, cr = subtrop_warm_anom_min_m, subtrop_closed_drop_m, subtrop_closed_radius_deg
        else:                           # deep tropics: lenient (catch weak/forming TCs)
            wa, cd, cr = warm_anom_min_m, closed_drop_m, closed_radius_deg
        if not is_warm_core(lat, lon, anom, lats, lons,
                            search_max_deg=search_max_deg, warm_anom_min_m=wa,
                            closed_drop_m=cd, closed_radius_deg=cr,
                            n_azimuth=n_azimuth, n_radial=n_radial):
            continue
        kept.append(ctr)
    return kept
