"""rmm_wh04.py — Wheeler & Hendon (2004) RMM projection for the GEFS MJO
forecast (subseasonal Phase 3 Group B item 4).

Everything here is pinned to primary sources (verified 2026-07-16; see the
AGENT_STATUS research log): the combined EOF structures are the original
BoM-distributed WH04 file (subseasonal/wh04_eofs.txt, sha1 5d8a1137...,
cross-checked byte-equal against the DTC/METplus reference copies —
432-point vector = OLR(144) + u850(144) + u200(144) at longitudes
0:2.5:357.5, 15S-15N averaged, unit norm), the field normalizations ship
IN the file (OLR 15.11623 W m-2 · u850 1.81355 m s-1 · u200 4.80978
m s-1), and the PC normalizations are the observed 1979-2001 PC standard
deviations per the METplus reference implementation (8.618352504159244,
8.40736449709697 — NOT sqrt(eigenvalues); don't substitute).

Projection procedure (WH04 + Gottschalck et al. 2010, verbatim order):
  1. remove the observed daily climatology (seasonal cycle),
  2. remove the mean of the most recent 120 days of ANOMALY data — for
     forecast day N that trailing window is the obs/analysis anomalies
     plus the first N forecast days (the obs+forecast concatenation,
     quoted in G2010), which is also what joins the forecast continuously
     onto the observed track,
  3. average 15S-15N,
  4. divide each field by its normalization,
  5. project onto EOF1/EOF2 (plain dot product — WH04: the EOF pair
     "acts as an effective filter for the intraseasonal frequencies...
     without the need for conventional time filtering"),
  6. divide the PCs by the observed PC standard deviations.
The WH04 SST/ENSO regression step is used only in DERIVING the EOFs and
is NOT applied in projection (G2010/Lin et al. 2008; BoM's own file
header: "Only the 120-day has been removed").

This module is pure math on prepared (time, 144-lon) anomaly series —
fetching/climatology live with the callers. All series must already be
15S-15N cos-weighted means on the 2.5-degree RMM longitudes."""
from __future__ import annotations

from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
EOF_FILE = HERE / "wh04_eofs.txt"

# RMM longitude grid (144 points, 0..357.5 step 2.5)
RMM_LONS = np.arange(0.0, 360.0, 2.5)

# field normalizations (also embedded in the EOF file; asserted at load)
NORM_OLR = 15.11623
NORM_U850 = 1.81355
NORM_U200 = 4.80978
# observed PC standard deviations (1979-2001; METplus reference values)
PC1_NORM = 8.618352504159244
PC2_NORM = 8.40736449709697

WINDOW_120 = 120


def load_eofs(path: Path = EOF_FILE):
    """(eof1[432], eof2[432]) from the vendored WH04 file, with the
    embedded normalization block asserted against the pinned constants."""
    vals = []
    for ln in Path(path).read_text().splitlines():
        try:
            nums = [float(x) for x in ln.split()]
        except ValueError:
            continue
        if nums:
            vals.append(nums)
    two = [v for v in vals if len(v) == 2]
    if len(two) < 433:
        raise ValueError(f"EOF file malformed: {len(two)} 2-col rows")
    arr = np.array(two[1:433])          # row 0 = the two eigenvalues
    for col in (0, 1):
        n = float((arr[:, col] ** 2).sum())
        if abs(n - 1.0) > 1e-4:
            raise ValueError(f"EOF{col + 1} not unit norm ({n})")
    flat = np.array([x for v in vals for x in v])
    for want in (NORM_OLR, NORM_U850, NORM_U200):
        if (np.abs(flat - want) < 5e-6).sum() < 144:
            raise ValueError(f"normalization {want} missing from EOF file")
    return arr[:, 0], arr[:, 1]


def remove_trailing_mean(anom: np.ndarray, valid: np.ndarray | None = None,
                         window: int = WINDOW_120) -> np.ndarray:
    """Step 2: at each time t, subtract the mean of the most recent
    `window` days of anomaly data (rows t-window+1..t), per longitude.
    `valid` marks rows that hold real data (NaN rows are excluded from
    the window mean); rows without >= window/2 valid antecedents get the
    mean of whatever is available (short-history tolerance)."""
    nt = anom.shape[0]
    out = np.full_like(anom, np.nan, dtype=float)
    if valid is None:
        valid = np.isfinite(anom).all(axis=1)
    for t in range(nt):
        lo = max(0, t - window + 1)
        rows = anom[lo:t + 1][valid[lo:t + 1]]
        if rows.shape[0] == 0:
            continue
        out[t] = anom[t] - rows.mean(axis=0)
    return out


def project(olr_anom: np.ndarray, u850_anom: np.ndarray,
            u200_anom: np.ndarray, eof1: np.ndarray, eof2: np.ndarray):
    """Steps 4-6 for already-120-day-removed (time, 144) anomalies ->
    (pc1[t], pc2[t]). NaN rows in any field yield NaN PCs for that day."""
    combo = np.concatenate([olr_anom / NORM_OLR,
                            u850_anom / NORM_U850,
                            u200_anom / NORM_U200], axis=1)   # (t, 432)
    pc1 = combo @ eof1 / PC1_NORM
    pc2 = combo @ eof2 / PC2_NORM
    return pc1, pc2


def phase_of(pc1: float, pc2: float) -> int:
    """WH04 octant number 1..8 for a (RMM1, RMM2) point."""
    ang = np.degrees(np.arctan2(pc2, pc1)) % 360.0
    # phase 5 opens at the +RMM1 axis (angle 0), phases advance CCW
    return int(((ang + 180.0) % 360.0) // 45.0) + 1


def rmm_series(olr_anom, u850_anom, u200_anom, valid=None,
               eofs=None):
    """Full steps 2-6 on seasonal-cycle-removed, 15S-15N-averaged
    (time, 144) series -> (pc1, pc2, amp, phase[]) arrays."""
    e1, e2 = eofs if eofs is not None else load_eofs()
    o = remove_trailing_mean(olr_anom, valid)
    a = remove_trailing_mean(u850_anom, valid)
    b = remove_trailing_mean(u200_anom, valid)
    pc1, pc2 = project(o, a, b, e1, e2)
    amp = np.sqrt(pc1 ** 2 + pc2 ** 2)
    ph = np.array([phase_of(x, y) if np.isfinite(x) and np.isfinite(y)
                   else 0 for x, y in zip(pc1, pc2)])
    return pc1, pc2, amp, ph
