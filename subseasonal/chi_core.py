"""chi_core.py — velocity potential (chi) + divergent wind from u/v on a
regular global lat-lon grid, via a spherical-harmonic Poisson solve.

Method (the standard one, e.g. windspharm's, reimplemented on pyshtools
because pyspharm has no wheels and its Fortran build is fragile in CI):

    1. divergence D = (1/(a cosphi)) * [du/dlambda + d(v cosphi)/dphi]
       (centered finite differences, periodic in longitude),
    2. expand D in spherical harmonics, solve  del^2 chi = D  spectrally:
       chi_lm = -a^2 D_lm / (l(l+1)),  l >= 1  (l=0 undefined, set 0),
    3. triangular-truncate at T (default 21 — the planetary scales the
       MJO/velocity-potential diagnostics are defined on),
    4. synthesize chi; divergent wind = grad(chi):
       u_chi = (1/(a cosphi)) dchi/dlambda,  v_chi = (1/a) dchi/dphi.

Grid convention IN and OUT: lats ascending or descending both accepted
(handled internally), lons 0..360 ascending, regular spacing. chi in m^2/s.
"""
from __future__ import annotations

import numpy as np

A_EARTH = 6.371e6  # m


def _to_dh2(field: np.ndarray, lats: np.ndarray, lons: np.ndarray, n: int = 90):
    """Bilinear-resample a regular lat-lon field to the (n, 2n) 'DH2'
    equisampled grid pyshtools expands exactly: lat from +90 going south in
    steps of 180/n (row 0 = north pole), lon from 0 in steps of 180/n."""
    dh_lat = 90.0 - 180.0 / n * np.arange(n)
    dh_lon = 360.0 / (2 * n) * np.arange(2 * n)
    la = lats
    fld = field
    if la[0] > la[-1]:               # make latitude ascending for interp
        la = la[::-1]
        fld = fld[::-1, :]
    # periodic longitude pad
    lo = np.concatenate([lons, [lons[0] + 360.0]])
    fld = np.concatenate([fld, fld[:, :1]], axis=1)
    # separable bilinear interpolation
    li = np.interp(dh_lat, la, np.arange(la.size))
    out_lat = np.empty((n, fld.shape[1]))
    i0 = np.clip(np.floor(li).astype(int), 0, la.size - 2)
    w = (li - i0)[:, None]
    out_lat = fld[i0, :] * (1 - w) + fld[i0 + 1, :] * w
    lj = np.interp(dh_lon, lo, np.arange(lo.size))
    j0 = np.clip(np.floor(lj).astype(int), 0, lo.size - 2)
    wj = (lj - j0)[None, :]
    return out_lat[:, j0] * (1 - wj) + out_lat[:, j0 + 1] * wj, dh_lat, dh_lon


def _from_dh2(field: np.ndarray, dh_lat: np.ndarray, dh_lon: np.ndarray,
              lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Bilinear back onto the caller's grid (poles clamped to nearest row)."""
    la = dh_lat[::-1]                # ascending
    fld = field[::-1, :]
    lo = np.concatenate([dh_lon, [360.0]])
    fld = np.concatenate([fld, fld[:, :1]], axis=1)
    tlat = np.clip(lats, la[0], la[-1])
    li = np.interp(tlat, la, np.arange(la.size))
    i0 = np.clip(np.floor(li).astype(int), 0, la.size - 2)
    w = (li - i0)[:, None]
    mid = fld[i0, :] * (1 - w) + fld[i0 + 1, :] * w
    lj = np.interp(np.mod(lons, 360.0), lo, np.arange(lo.size))
    j0 = np.clip(np.floor(lj).astype(int), 0, lo.size - 2)
    wj = (lj - j0)[None, :]
    return mid[:, j0] * (1 - wj) + mid[:, j0 + 1] * wj


def divergence(u: np.ndarray, v: np.ndarray, lats: np.ndarray,
               lons: np.ndarray) -> np.ndarray:
    """Spherical divergence by centered differences (periodic lon)."""
    phi = np.radians(lats)
    lam = np.radians(lons)
    cos = np.cos(phi)[:, None]
    # guard the pole rows (cos -> 0): values there are replaced below
    cos_safe = np.where(np.abs(cos) < 1e-6, 1e-6, cos)
    dudl = (np.roll(u, -1, axis=1) - np.roll(u, 1, axis=1)) / (
        (np.roll(lam, -1) - np.roll(lam, 1)) % (2 * np.pi))[None, :]
    vcos = v * cos
    dvdp = np.gradient(vcos, phi, axis=0)
    d = (dudl + dvdp) / (A_EARTH * cos_safe)
    # poles: replace with the neighboring row (display-scale fields only)
    if abs(lats[0]) > 89.9:
        d[0, :] = d[1, :]
    if abs(lats[-1]) > 89.9:
        d[-1, :] = d[-1 - 1, :]
    return d


def chi_from_uv(u: np.ndarray, v: np.ndarray, lats: np.ndarray,
                lons: np.ndarray, truncation: int = 21):
    """Velocity potential chi (m^2/s) + divergent wind (u_chi, v_chi), all on
    the input grid, spectrally solved and T-truncated (default T21)."""
    import pyshtools as sh

    d = divergence(u, v, lats, lons)
    dh, dh_lat, dh_lon = _to_dh2(d, lats, lons)
    grid = sh.SHGrid.from_array(dh, grid='DH')          # (n, 2n) sampling
    coeffs = grid.expand()
    l = np.arange(coeffs.coeffs.shape[1])
    with np.errstate(divide='ignore'):
        inv_lap = np.where(l > 0, -A_EARTH ** 2 / (l * (l + 1.0)), 0.0)
    c = coeffs.coeffs * inv_lap[None, :, None]
    c[:, truncation + 1:, :] = 0.0                       # triangular truncation
    chi_dh = sh.SHCoeffs.from_array(c, normalization=coeffs.normalization,
                                    csphase=coeffs.csphase) \
        .expand(grid='DH2').to_array()
    chi = _from_dh2(chi_dh, dh_lat, dh_lon, lats, lons)

    # divergent wind = grad(chi) on the (smooth, T21) synthesized field
    phi = np.radians(lats)
    lam = np.radians(lons)
    cos = np.cos(phi)[:, None]
    cos_safe = np.where(np.abs(cos) < 1e-6, 1e-6, cos)
    dchidl = (np.roll(chi, -1, axis=1) - np.roll(chi, 1, axis=1)) / (
        (np.roll(lam, -1) - np.roll(lam, 1)) % (2 * np.pi))[None, :]
    u_chi = dchidl / (A_EARTH * cos_safe)
    v_chi = np.gradient(chi, phi, axis=0) / A_EARTH
    if abs(lats[0]) > 89.9:
        u_chi[0, :] = 0.0
        v_chi[0, :] = v_chi[1, :]
    if abs(lats[-1]) > 89.9:
        u_chi[-1, :] = 0.0
        v_chi[-1, :] = v_chi[-2, :]
    return chi, u_chi, v_chi
