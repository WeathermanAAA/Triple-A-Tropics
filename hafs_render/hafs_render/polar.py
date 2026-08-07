#!/usr/bin/env python3
"""THE storm-centred polar machinery - one implementation, three consumers.

Extracted 2026-08-07 from shear_diag (spec #26), which proved it: the
normalised-dlon local plane, the cylindrical decomposition, and the
area-weighted ring mean are exactly the pieces #25 (azimuthal-mean structure)
and #7 (quadrant-max wind radii) need, and two azimuthal-mean implementations
drifting apart is the same class of duplication that once produced eight
SSHWS palettes in this repo. shear_diag now imports from here; so do the
structure diagnostics. If you are about to write another ring mean, radial
decomposition, or storm-centred distance grid: don't - extend this.

ANTIMERIDIAN: every longitude difference is normalised into (-180, 180]
BEFORE it becomes a distance or an angle (the repo's most repeated bug).
HEMISPHERES: r-hat/theta-hat are pure functions of position - nothing here
depends on the sign of Coriolis or the direction of cyclonic rotation, so a
positive tangential wind is CCW in BOTH hemispheres (cyclonic in the NH,
anticyclonic in the SH); interpretation belongs to callers and display text.

Stdlib + numpy only. Distances in km; winds in whatever the caller feeds.
"""
from __future__ import annotations

import numpy as np

#: km per degree of latitude (spherical mean radius).
KM_PER_DEG = 111.195


def norm_dlon(lon, cen_lon):
    """Longitude difference normalised into (-180, 180]. THE antimeridian
    guard: applied before anything becomes a distance or an angle."""
    return (np.asarray(lon, dtype=float) - float(cen_lon) + 180.0) % 360.0 - 180.0


def polar_grid(lat, lon, cen_lat, cen_lon) -> dict:
    """Storm-centred local plane for a lat/lon grid (1-D axes or 2-D meshes).

    Returns dict(x_km, y_km, r_km, lat2, lon2, weights) - weights are the
    cos(lat) cell-area factors every mean here must use. dlon is normalised
    FIRST, then scaled by cos(lat) of each point for the zonal metric.
    """
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    if lat.ndim == 1:
        lon2, lat2 = np.meshgrid(lon, lat)
    else:
        lon2, lat2 = lon, lat
    dlon = norm_dlon(lon2, cen_lon)
    x_km = dlon * KM_PER_DEG * np.cos(np.radians(lat2))
    y_km = (lat2 - float(cen_lat)) * KM_PER_DEG
    return {
        "x_km": x_km, "y_km": y_km, "r_km": np.hypot(x_km, y_km),
        "lat2": lat2, "lon2": lon2,
        "weights": np.cos(np.radians(lat2)),
    }


def tangential_radial(u, v, pg: dict):
    """Cylindrical decomposition of (u east, v north) about the grid's centre:
    returns (v_t, v_r). v_t is CCW-positive (pure geometry, hemisphere-
    neutral); v_r is outward-positive (inflow is negative)."""
    r_safe = np.where(pg["r_km"] > 1e-6, pg["r_km"], 1e-6)
    rhx, rhy = pg["x_km"] / r_safe, pg["y_km"] / r_safe
    thx, thy = -pg["y_km"] / r_safe, pg["x_km"] / r_safe
    u = np.asarray(u, dtype=float)
    v = np.asarray(v, dtype=float)
    return u * thx + v * thy, u * rhx + v * rhy


def ring_mean(values, pg: dict, edges, inside=None):
    """THE azimuthal-mean operator: area-weighted mean of ``values`` per
    radial ring bounded by ``edges`` (km). NaNs excluded per ring; rings with
    no finite samples return NaN. ``inside`` optionally restricts the sample
    (e.g. a coverage/finite mask). Returns (means, ring_weight_sums)."""
    values = np.asarray(values, dtype=float)
    n = len(edges) - 1
    idx = np.clip(np.digitize(pg["r_km"], edges) - 1, 0, n - 1)
    ok = np.isfinite(values) & (pg["r_km"] >= edges[0]) & (pg["r_km"] <= edges[-1])
    if inside is not None:
        ok = ok & inside
    w = np.where(ok, pg["weights"], 0.0)
    wsum = np.bincount(idx[ok].ravel(), weights=w[ok].ravel(), minlength=n)
    vsum = np.bincount(idx[ok].ravel(),
                       weights=(w * np.where(ok, values, 0.0))[ok].ravel(),
                       minlength=n)
    means = np.full(n, np.nan)
    good = wsum > 0
    means[good] = vsum[good] / wsum[good]
    return means, wsum


def quadrant_max_radius(wind, pg: dict, threshold: float,
                        max_r_km: float) -> list:
    """MAXIMUM RADIAL EXTENT of ``wind >= threshold`` per quadrant, in km:
    [NE, SE, SW, NW], None where the threshold is never reached.

    THIS IS QUADRANT-MAX, NOT AN AZIMUTHAL MEAN - deliberately, and it
    matters: the ATCF b-deck and RVCN encode wind radii as the maximum radial
    extent in each of four compass quadrants (the NEQ radius code), so only
    a quadrant-max compares to the published numbers. A mean would read
    systematically small and the product would be worse than not shipping it.
    Quadrants are COMPASS quadrants about the centre (NE = x>=0, y>=0 in the
    normalised local plane), hemisphere-neutral by construction.
    """
    wind = np.asarray(wind, dtype=float)
    x, y, r = pg["x_km"], pg["y_km"], pg["r_km"]
    hit = np.isfinite(wind) & (wind >= threshold) & (r <= max_r_km) & (r > 0)
    quads = [
        (x >= 0) & (y >= 0),   # NE
        (x >= 0) & (y < 0),    # SE
        (x < 0) & (y < 0),     # SW
        (x < 0) & (y >= 0),    # NW
    ]
    out = []
    for q in quads:
        m = hit & q
        out.append(float(r[m].max()) if m.any() else None)
    return out
