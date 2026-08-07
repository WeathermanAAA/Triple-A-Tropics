#!/usr/bin/env python3
"""Azimuthal-mean storm structure (#25) + four-quadrant wind radii (#7).

Both are consumers of the ONE shared polar machinery (polar.py, extracted
from #26's vortex removal) - the point of the extraction is that this module
contains no second azimuthal-mean implementation and no second storm-centred
geometry. Everything is computed about the MODEL's own vortex fix.

#25 - the spec calls the azimuthal-mean profile "the single most
information-dense TC structure diagnostic": tangential wind v_t(r) per level
with the RMW, radial wind v_r(r) (inflow negative), and the warm core. The
nest cache carries NO temperature levels, so the warm core is derived
hydrostatically from the 850-500 hPa THICKNESS anomaly the cache does carry
(gh_500 - gh_850, metres): dT_mean = g*dZ / (R * ln(850/500)) - stated as
thickness-derived wherever it is shown. The resolution caveat is made
VISIBLE by computing the same profile from the 2 km nest and the 6 km parent
and showing both.

#7 - QUADRANT-MAX, NOT AZIMUTHAL MEAN: ATCF/b-deck/RVCN wind radii are the
maximum radial extent of the threshold wind in each compass quadrant (the
NEQ code), so only a quadrant-max compares to the published numbers; a mean
reads systematically small and would be worse than not shipping. The output
carries "method": "quadrant_max" so nobody has to trust a comment. Computed
on the PARENT 10 m wind (R34 can exceed the nest half-width) in n mi,
ATCF's unit.

Antimeridian: inherited from polar (dlon normalised before any distance or
angle). Hemispheres: v_t is CCW-positive geometry; panels/labels convert to
"cyclonic" per-hemisphere at DISPLAY time only.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from hafs_render import polar

KT_PER_MS = 1.94384

#: Profile extent/rings: 0-300 km at 6 km rings (2 km nest grid -> >=3
#: samples per ring per azimuth band; the parent overlay shares the edges).
PROFILE_MAX_KM = 300.0
PROFILE_RING_KM = 6.0

#: Warm-core far-field reference: the outermost rings (250-300 km), where the
#: thickness perturbation of the vortex has decayed.
FARFIELD_KM = (250.0, 300.0)

#: Hydrostatic conversion for the 850-500 thickness anomaly:
#: dZ = (R/g) * ln(850/500) * dT_mean  ->  dT = dZ * g / (R * ln(1.7)).
_DT_PER_M = 9.80665 / (287.05 * np.log(850.0 / 500.0))

#: Wind-radii thresholds (kt) and search cap. 600 n mi (1111 km) comfortably
#: exceeds any published R34; the parent crop is wider still.
RADII_THRESHOLDS_KT = (34, 50, 64)
RADII_MAX_KM = 1111.0
KM_PER_NM = 1.852

#: Structure levels: (label, u key, v key, unit conversion to kt). 10 m wind
#: is cached in kt already; the upper-air u/v are raw GRIB m/s.
_LEVELS = (
    ("10m", "u_kt", "v_kt", 1.0),
    ("850", "u_850", "v_850", KT_PER_MS),
    ("700", "u_700", "v_700", KT_PER_MS),
    ("500", "u_500", "v_500", KT_PER_MS),
)


def _edges() -> np.ndarray:
    return np.arange(0.0, PROFILE_MAX_KM + PROFILE_RING_KM / 2, PROFILE_RING_KM)


def azimuthal_structure(fields: dict, lat, lon, cen_lat, cen_lon) -> Optional[dict]:
    """Azimuthal-mean profiles about the model's own fix.

    ``fields`` maps the keys in _LEVELS (any subset; 10 m required) plus
    optionally gh_850/gh_500 (metres) for the warm core. Returns
    {"edges_km", "r_km" (ring midpoints), "vt_kt": {lvl: [...]},
    "vr_kt": {lvl: [...]}, "t_anom_c": [...]|None, "rmw_km", "vt_max_kt"}
    or None without a centre / without 10 m wind coverage.
    """
    if cen_lat is None or cen_lon is None:
        return None
    pg = polar.polar_grid(lat, lon, cen_lat, cen_lon)
    edges = _edges()
    mids = (edges[:-1] + edges[1:]) / 2.0

    out_vt, out_vr = {}, {}
    for name, uk, vk, s in _LEVELS:
        if uk not in fields or vk not in fields:
            continue
        vt, vr = polar.tangential_radial(
            np.asarray(fields[uk], dtype=float) * s,
            np.asarray(fields[vk], dtype=float) * s, pg)
        vt_m, _ = polar.ring_mean(vt, pg, edges)
        vr_m, _ = polar.ring_mean(vr, pg, edges)
        out_vt[name] = vt_m
        out_vr[name] = vr_m
    if "10m" not in out_vt or not np.isfinite(out_vt["10m"]).any():
        return None

    # RMW from the 10 m tangential profile: the CYCLONIC peak, i.e. the peak
    # of |vt| (hemisphere-neutral; sign is CCW-geometry).
    vt10 = out_vt["10m"]
    i = int(np.nanargmax(np.abs(vt10)))
    rmw_km = float(mids[i])
    vt_max_kt = float(np.abs(vt10[i]))

    t_anom = None
    if "gh_850" in fields and "gh_500" in fields:
        thk = (np.asarray(fields["gh_500"], dtype=float) -
               np.asarray(fields["gh_850"], dtype=float))     # metres
        thk_m, _ = polar.ring_mean(thk, pg, edges)
        far = (mids >= FARFIELD_KM[0]) & (mids <= FARFIELD_KM[1])
        if np.isfinite(thk_m[far]).any():
            ref = float(np.nanmean(thk_m[far]))
            # Thickness-derived MEAN-LAYER (850-500) temperature anomaly.
            t_anom = (thk_m - ref) * _DT_PER_M

    return {
        "edges_km": edges, "r_km": mids,
        "vt_kt": out_vt, "vr_kt": out_vr,
        "t_anom_c": t_anom,
        "rmw_km": rmw_km, "vt_max_kt": round(vt_max_kt, 1),
    }


def quadrant_radii(wind_kt, lat, lon, cen_lat, cen_lon) -> Optional[dict]:
    """Four-quadrant wind radii from a 10 m wind field, ATCF-comparable.

    METHOD IS QUADRANT-MAX (stated in the output): per threshold, the maximum
    radial extent of wind >= threshold in each of NE/SE/SW/NW, in whole n mi,
    None where never reached. Compute on the PARENT domain.
    """
    if cen_lat is None or cen_lon is None:
        return None
    pg = polar.polar_grid(lat, lon, cen_lat, cen_lon)
    out = {"method": "quadrant_max", "units": "nm",
           "quadrants": ["NE", "SE", "SW", "NW"]}
    any_hit = False
    for thr in RADII_THRESHOLDS_KT:
        km = polar.quadrant_max_radius(np.asarray(wind_kt, dtype=float),
                                       pg, float(thr), RADII_MAX_KM)
        nm = [int(round(v / KM_PER_NM)) if v is not None else None for v in km]
        out[f"r{thr}"] = nm
        any_hit = any_hit or any(v is not None for v in nm)
    return out if any_hit else None
