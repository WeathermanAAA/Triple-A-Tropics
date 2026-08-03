#!/usr/bin/env python3
"""Vortex-removed deep-layer shear - the NUMBER, computed honestly.

Every site in the class plots a raw 850-200 hPa layer difference. Near the
storm that is quietly wrong: the TC's own circulation is present at both
levels and does not cancel, so the "shear" contains the vortex, and the error
grows with intensity - a strong storm looks sheared when it is not.

METHOD (matches SHIPS SHRD by construction): subtract the azimuthal-mean
tangential and radial wind about the vortex centre out to ``radius_km`` at
each level, difference the layers, and area-average over the same disc.

ONE LINEARITY FACT shapes the implementation: the azimuthal-mean operator is
LINEAR in the wind field (both the cylindrical decomposition and the
reconstruction are linear maps), so removing the axisymmetric component at
each level and then differencing is mathematically identical to removing the
axisymmetric component of the LAYER-DIFFERENCE field. The field cache already
stores that difference (``shru_/shrv_200_850``, knots, parent domain, v5+),
which is why this needs NO ingest change and no cache-version bump.

The centre must be the MODEL's own vortex (its trak.atcfunix fix at this
forecast hour), not the b-deck: we are removing the vortex the model has,
wherever the model put it. The compute surface is the PARENT domain - this is
an environmental quantity and a 500 km disc can exit the moving nest.

ANTIMERIDIAN: every longitude difference is normalised into (-180, 180]
BEFORE it becomes a distance or an angle. Azimuthal averaging about a centre
near 180 is exactly the sign-flip class that has bitten this site repeatedly
(tracks splitting, plot extents over Africa); a raw ``lon - cen_lon`` there
manufactures ~360-degree offsets and garbage radii.

HEMISPHERES: the geometry is hemisphere-neutral on purpose - radial and
tangential unit vectors are pure functions of position around the centre, and
nothing here depends on the sign of the Coriolis parameter or the direction
of cyclonic rotation. The hemisphere-dependent PHYSICS (where convection
favours relative to the shear vector) belongs to display text, never to this
module.

``method`` is a strategy seam. ``"azimuthal_mean"`` is implemented;
``"helmholtz"`` (rotational/divergent partitioning of the vortex, the
rigorous version) is a named seam that raises until someone builds it -
callers can already ask for it by name.

Stdlib + numpy only. All wind inputs/outputs in KNOTS, headings in degrees
using the "direction the shear points TOWARD" convention (0 = northward,
90 = eastward) - the natural convention for drawing the arrow and rotating
the shear-relative view. NOTE: SHIPS SHTD reports the direction the shear
comes FROM (met convention); add 180 before comparing. Verified empirically
(EP07 2026-07-31 18Z, n=8 taus): raw offset vs SHTD is ~180 deg with +/-15
scatter; after the flip the directions agree to ~14 deg median.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

#: Default layer/radius: 850-200 hPa over 0-500 km, because that is SHIPS SHRD.
DEFAULT_RADIUS_KM = 500.0
DEFAULT_LAYER = (200, 850)

#: km per degree of latitude (spherical mean radius).
_KM_PER_DEG = 111.195

#: Radial bins for the azimuthal mean. 25 bins over 500 km = 20 km rings on a
#: ~6 km parent grid: fine enough to resolve the vortex profile, coarse enough
#: that every ring holds many samples.
_N_RADIAL_BINS = 25

#: Minimum fraction of the disc's area that must carry finite data. Below
#: this the disc is clipped badly enough (domain edge) that the numbers would
#: be about the clipping, not the environment.
_MIN_COVERAGE = 0.6


def _norm_dlon(lon, cen_lon):
    """Longitude difference normalised into (-180, 180]. THE antimeridian
    guard: applied before anything becomes a distance or an angle."""
    return (np.asarray(lon, dtype=float) - float(cen_lon) + 180.0) % 360.0 - 180.0


def heading_deg(u: float, v: float) -> float:
    """Vector (u east, v north) -> direction it points TOWARD, degrees from
    north, clockwise. Matches SHIPS SHTD."""
    return float(np.degrees(np.arctan2(u, v)) % 360.0)


def _azimuthal_mean_removal(du, dv, x_km, y_km, r_km, weights, inside,
                            radius_km, n_bins=_N_RADIAL_BINS):
    """Subtract the reconstructed axisymmetric component of (du, dv).

    Decompose the field into radial/tangential components about the centre,
    take the area-weighted azimuthal mean per radial ring, reconstruct the
    axisymmetric vector field from the ring means, and subtract it.
    """
    r_safe = np.where(r_km > 1e-6, r_km, 1e-6)
    rhx, rhy = x_km / r_safe, y_km / r_safe          # r-hat
    thx, thy = -y_km / r_safe, x_km / r_safe         # theta-hat (CCW; pure geometry)
    vr = du * rhx + dv * rhy
    vt = du * thx + dv * thy

    edges = np.linspace(0.0, radius_km, n_bins + 1)
    idx = np.clip(np.digitize(r_km, edges) - 1, 0, n_bins - 1)
    w = np.where(inside, weights, 0.0)

    vr_mean = np.zeros(n_bins)
    vt_mean = np.zeros(n_bins)
    wsum = np.bincount(idx[inside].ravel(), weights=w[inside].ravel(),
                       minlength=n_bins)
    vr_sum = np.bincount(idx[inside].ravel(),
                         weights=(w * np.where(inside, vr, 0.0))[inside].ravel(),
                         minlength=n_bins)
    vt_sum = np.bincount(idx[inside].ravel(),
                         weights=(w * np.where(inside, vt, 0.0))[inside].ravel(),
                         minlength=n_bins)
    ok = wsum > 0
    vr_mean[ok] = vr_sum[ok] / wsum[ok]
    vt_mean[ok] = vt_sum[ok] / wsum[ok]

    # Reconstruct the axisymmetric field at every point and subtract.
    axi_u = vr_mean[idx] * rhx + vt_mean[idx] * thx
    axi_v = vr_mean[idx] * rhy + vt_mean[idx] * thy
    return du - axi_u, dv - axi_v


def _weighted_mean(field, weights, inside):
    w = np.where(inside, weights, 0.0)
    tot = w.sum()
    if tot <= 0:
        return np.nan
    return float((field * w)[inside].sum() / tot)


def vortex_removed_shear(du_kt, dv_kt, lat, lon, cen_lat, cen_lon, *,
                         radius_km: float = DEFAULT_RADIUS_KM,
                         layer: tuple = DEFAULT_LAYER,
                         method: str = "azimuthal_mean") -> Optional[dict]:
    """The vortex-removed and naive deep-layer shear numbers for one frame.

    ``du_kt/dv_kt`` is the layer wind-difference field in KNOTS on the parent
    grid (by linearity, removal on the difference == per-level removal then
    differencing). ``lat/lon`` are 1-D axes or 2-D meshes; ``cen_*`` is the
    MODEL's own vortex fix. Returns None when the centre is missing or the
    disc's data coverage is too poor to be honest (the caller publishes
    nothing rather than a clipping artifact).
    """
    if cen_lat is None or cen_lon is None:
        return None
    if method == "helmholtz":
        raise NotImplementedError(
            "helmholtz vortex removal is a named seam, not yet built - use "
            "method='azimuthal_mean'")
    if method != "azimuthal_mean":
        raise ValueError(f"unknown removal method {method!r}")

    du = np.asarray(du_kt, dtype=float)
    dv = np.asarray(dv_kt, dtype=float)
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    if lat.ndim == 1:
        lon2, lat2 = np.meshgrid(lon, lat)
    else:
        lon2, lat2 = lon, lat

    # Local plane about the centre. dlon is normalised FIRST (antimeridian),
    # then scaled by cos(lat) of each point for the zonal metric.
    dlon = _norm_dlon(lon2, cen_lon)
    x_km = dlon * _KM_PER_DEG * np.cos(np.radians(lat2))
    y_km = (lat2 - float(cen_lat)) * _KM_PER_DEG
    r_km = np.hypot(x_km, y_km)

    finite = np.isfinite(du) & np.isfinite(dv)
    inside = (r_km <= radius_km) & finite
    n = int(inside.sum())
    if n < 16:
        return None

    # Area weights on a lat/lon grid: cell area goes as cos(lat).
    weights = np.cos(np.radians(lat2))

    # Coverage: how much of the ideal disc actually carries data. Estimated
    # from grid spacing; a disc half off the parent (or over all-NaN data)
    # reports low coverage and the frame publishes nothing.
    if lat.ndim == 1 and len(lat) > 1 and len(lon) > 1:
        dlat_g = abs(float(lat[1] - lat[0]))
        dlon_g = abs(float(_norm_dlon(lon[1], lon[0])))
        cell_km2 = (dlat_g * _KM_PER_DEG) * (
            dlon_g * _KM_PER_DEG * float(np.cos(np.radians(cen_lat))))
        disc_km2 = np.pi * radius_km ** 2
        coverage = min(1.0, (n * cell_km2) / disc_km2)
    else:
        coverage = 1.0
    if coverage < _MIN_COVERAGE:
        return None

    naive_u = _weighted_mean(du, weights, inside)
    naive_v = _weighted_mean(dv, weights, inside)

    rem_du, rem_dv = _azimuthal_mean_removal(
        du, dv, x_km, y_km, r_km, weights, inside, radius_km)
    rem_u = _weighted_mean(rem_du, weights, inside)
    rem_v = _weighted_mean(rem_dv, weights, inside)

    return {
        "mag_kt": round(float(np.hypot(rem_u, rem_v)), 1),
        "hdg_deg": round(heading_deg(rem_u, rem_v), 1),
        "naive_mag_kt": round(float(np.hypot(naive_u, naive_v)), 1),
        "naive_hdg_deg": round(heading_deg(naive_u, naive_v), 1),
        "method": method,
        "layer_hpa": [int(layer[0]), int(layer[1])],
        "radius_km": float(radius_km),
        "n_grid": n,
        "coverage": round(float(coverage), 3),
    }
