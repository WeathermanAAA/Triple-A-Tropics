"""mjo_reconstruct.py — MJO OLR anomaly reconstruction from RMM PCs.

Inverts the Wheeler & Hendon (2004) RMM projection implemented in
subseasonal/rmm_wh04.py to recover the MJO's OLR anomaly field from a
(RMM1, RMM2) pair. This is what the OLR Hovmöller's forecast half shows:
the phase-coherent MJO signal carried by a model's ensemble-mean RMM
forecast, instead of raw ensemble-mean OLR (whose phase-incoherent
averaging collapses the wave).

Reconstruction formula
----------------------
The forward projection (rmm_wh04.project) builds the normalized combined
vector x = [OLR'/NORM_OLR, u850'/NORM_U850, u200'/NORM_U200] (432 points,
three 144-longitude blocks, 15S-15N means on longitudes 0..357.5 by 2.5)
and computes

    RMM1 = (x . EOF1) / PC1_NORM,    RMM2 = (x . EOF2) / PC2_NORM.

EOF1/EOF2 are orthonormal in the 432-point space, so the rank-2
(MJO-only) least-squares reconstruction of x is

    x_mjo = RMM1 * PC1_NORM * EOF1 + RMM2 * PC2_NORM * EOF2

and the OLR block (the FIRST 144 entries of each EOF) de-normalizes back
to physical units by multiplying with the same global normalization the
preprocessing divided by (NORM_OLR = 15.11623 W m-2, shipped inside the
WH04 EOF file and asserted at load):

    OLR'(lon) = NORM_OLR * (RMM1 * PC1_NORM * EOF1_olr(lon)
                            + RMM2 * PC2_NORM * EOF2_olr(lon))

with PC1_NORM = 8.618352504159244 and PC2_NORM = 8.40736449709697 (the
observed 1979-2001 PC standard deviations, METplus reference values —
see rmm_wh04.py's provenance header).

The result lives in the same anomaly space the projection consumed:
seasonal cycle (daily climatology, mean + first 3 harmonics) removed AND
the previous-120-day running mean removed. Callers displaying it beside
an analysis field must give the analysis the identical treatment.

Unit sanity: a peak MJO (amplitude ~2) yields O(15-30) W m-2 OLR
anomalies (the EOF OLR blocks peak near 0.11 in normalized units, so
2 * 8.6 * 0.11 * 15.1 ≈ 29 W m-2).
"""
from __future__ import annotations

import numpy as np

import rmm_wh04

# the OLR block is the first 144 entries of the 432-point combined EOFs
N_LON = 144
RMM_LONS = rmm_wh04.RMM_LONS


def olr_from_pcs(pc1, pc2, eofs=None) -> np.ndarray:
    """MJO OLR anomaly rows (W m-2) reconstructed from RMM PCs.

    ``pc1`` / ``pc2`` are scalars or equal-length 1-D arrays of RMM1/RMM2
    values (standard normalized units, e.g. a model's ensemble-mean RMM
    forecast). Returns a (ntime, 144) array of 15S-15N-mean OLR anomalies
    on the RMM longitudes (0..357.5 by 2.5), per the formula in the
    module docstring. Non-finite PC pairs yield NaN rows.
    """
    p1 = np.atleast_1d(np.asarray(pc1, dtype=float))
    p2 = np.atleast_1d(np.asarray(pc2, dtype=float))
    if p1.shape != p2.shape or p1.ndim != 1:
        raise ValueError(f"pc1/pc2 must be equal-length 1-D "
                         f"(got {p1.shape} vs {p2.shape})")
    e1, e2 = eofs if eofs is not None else rmm_wh04.load_eofs()
    out = rmm_wh04.NORM_OLR * (
        np.outer(p1 * rmm_wh04.PC1_NORM, e1[:N_LON])
        + np.outer(p2 * rmm_wh04.PC2_NORM, e2[:N_LON]))
    bad = ~(np.isfinite(p1) & np.isfinite(p2))
    if bad.any():
        out[bad] = np.nan
    return out
