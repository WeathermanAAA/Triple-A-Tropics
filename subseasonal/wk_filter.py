"""wk_filter.py — Wheeler–Kiladis wavenumber-frequency filtering in plain
numpy (no NCL, no tropical_diagnostics dependency).

Line-faithful to Carl Schreck's kf_filter.ncl with the NOAA-PSL
tropical_diagnostics port cross-checked; every convention below was
verified against primary sources + the actual PSL code run on synthetic
waves (2026-07-14 research pass, see the Phase-2 build brief):

* Input: anomaly array shaped (time, lon) — one latitude band, already
  seasonal-cycle-removed (long-term mean + first 3 harmonics; WW01).
* Preprocessing (Schreck lines 36–49): per-longitude least-squares
  DETREND (mean + trend removed), then a split-cosine-bell TAPER over
  5% of the series total (2.5% each end) toward the series MEAN.
  (The PSL python port skips both — a known port gap, do NOT copy it.)
* 2D FFT orientation — THE classic bug: with numpy's exp(+i…) inverse
  convention and rfft along TIME (ω ≥ 0 retained), an EASTWARD-moving
  wave of physical zonal wavenumber s > 0 lives at zonal-FFT index
  m = Nlon − s, i.e. numpy's "positive m" half is WESTWARD. We map
  explicitly by wavenumber arithmetic (never a blind axis reversal —
  the PSL port's plain [::-1] reversal introduces a one-wavenumber
  offset, verified numerically: asking eastward k=[5,7] passes k=6..8).
* Dispersion masks (shallow-water, verbatim from both sources):
    Kelvin:  ω = k·c
    ER:      ω = −β·k / (k² + 3β/c)
    MRG:     ω = √(βc) at k=0; k·c·(0.5 ± 0.5·√(1+4β/(k²c))) for k≷0
    IG1:     ω = √(3βc + k²c²)
  with c = √(g·h), k dimensionalized by Earth's radius, β = 2Ω/a.
* Real-time endpoint (Wheeler & Weickmann 2001; Schreck's operational
  NCICS monitor): filter the most recent 365 days of anomalies padded
  with zeros out to 1024 days. The padding makes the LAST ~1–2 weeks
  amplitude-damped for the lowest-frequency bands (MJO) — callers must
  surface that honestly on the plot.

Filtering each latitude independently and averaging afterwards equals
filtering the band-mean (the transform is linear); we filter the
band-mean directly.
"""
from __future__ import annotations

import numpy as np

G = 9.81                    # m s-2
EARTH_RADIUS = 6.371e6      # m
OMEGA = 7.292e-5            # s-1
BETA = 2.0 * OMEGA / EARTH_RADIUS   # equatorial beta, m-1 s-1
SECONDS_PER_DAY = 86400.0


def detrend_taper(data: np.ndarray, taper_frac: float = 0.05) -> np.ndarray:
    """Per-longitude linear detrend (mean+trend) then split-cosine-bell
    taper toward the (post-detrend) series mean, i.e. toward zero.
    ``data`` is (time, lon); returns a new array."""
    nt = data.shape[0]
    t = np.arange(nt, dtype=float)
    # least-squares line per longitude column
    t_mean = t.mean()
    t_var = ((t - t_mean) ** 2).sum()
    slope = ((t - t_mean)[:, None] * (data - data.mean(axis=0))).sum(axis=0) / t_var
    out = data - (data.mean(axis=0)[None, :] + (t - t_mean)[:, None] * slope[None, :])
    # split cosine bell over taper_frac of the series TOTAL (half each end),
    # Bloomfield 1976 form; post-detrend mean is 0 so tapering toward the
    # mean == multiplying the ends down to 0.
    m = int(np.floor(taper_frac * nt / 2.0))
    if m > 0:
        w = np.ones(nt)
        ramp = 0.5 * (1.0 - np.cos(np.pi * (np.arange(m) + 0.5) / m))
        w[:m] = ramp
        w[nt - m:] = ramp[::-1]
        out = out * w[:, None]
    return out


def _wave_frequency_bounds(wave: str, k_dim: np.ndarray, h: float) -> np.ndarray:
    """Dispersion frequency ω(k) in s-1 for equivalent depth ``h`` (m).
    ``k_dim`` is the dimensional zonal wavenumber (rad m-1, signed:
    positive = eastward). Returns NaN where the mode has no solution."""
    c = np.sqrt(G * h)
    with np.errstate(divide="ignore", invalid="ignore"):
        if wave == "kelvin":
            om = k_dim * c
        elif wave == "er":
            om = -BETA * k_dim / (k_dim ** 2 + 3.0 * BETA / c)
        elif wave == "mrg":
            om = np.where(
                k_dim == 0.0,
                np.sqrt(BETA * c),
                k_dim * c * (0.5 + 0.5 * np.sqrt(
                    np.maximum(1.0 + 4.0 * BETA / (k_dim ** 2 * c), 0.0))),
            )
            # NCL uses the +root for k=0/k<0 branch handling; for k>0 the
            # MRG branch is the -root and Schreck masks it out of the MRG
            # box anyway (MRG is westward: kMin/kMax < 0).
            om = np.where(
                k_dim > 0.0,
                k_dim * c * (0.5 - 0.5 * np.sqrt(
                    np.maximum(1.0 + 4.0 * BETA / (k_dim ** 2 * c), 0.0))),
                om,
            )
        elif wave == "ig1":
            om = np.sqrt(3.0 * BETA * c + k_dim ** 2 * c ** 2)
        else:
            raise ValueError(f"unknown wave {wave!r}")
    return np.abs(om)


def kf_filter(data: np.ndarray, obs_per_day: float,
              t_min: float, t_max: float,
              k_min: float, k_max: float,
              h_min: float | None = None, h_max: float | None = None,
              wave: str | None = None) -> np.ndarray:
    """Space-time filter an anomaly array shaped (time, lon).

    Sign convention: ``k_min``/``k_max`` are PHYSICAL zonal planetary
    wavenumbers, positive = EASTWARD propagation (Kelvin/MJO), negative
    = westward (ER/MRG/TD). Periods ``t_min``/``t_max`` in days.
    ``h_min``/``h_max`` (m) bound the mask between the ``wave`` mode's
    dispersion curves; either may be None to drop that bound.

    Returns the filtered (time, lon) array (real).
    """
    nt, nlon = data.shape
    pre = detrend_taper(data)

    # rfft along TIME keeps omega >= 0; full FFT along LON.
    # F has shape (nfreq, nlon) after transposing the rfft2 output layout:
    # do it explicitly for clarity.
    F = np.fft.rfft(pre, axis=0)          # (nfreq, nlon), time -> freq
    F = np.fft.fft(F, axis=1)             # lon -> zonal index m

    freqs = np.fft.rfftfreq(nt, d=1.0 / obs_per_day)     # cycles per day, >= 0
    m_idx = np.fft.fftfreq(nlon, d=1.0 / nlon)           # integer zonal index

    # With omega >= 0 retained and numpy's exp(+i(m x + j t)/N) inverse,
    # a wave exp(i(s x - omega t)) (EASTWARD, physical s > 0) appears at
    # m = -s (i.e. numpy index Nlon - s). So physical eastward wavenumber
    # k_phys = -m_idx. Verified against the PSL reference on synthetic
    # eastward/westward waves (variance ratio 1.0 kept / ~1e-28 removed).
    k_phys = -m_idx                                       # (nlon,)

    K = np.broadcast_to(k_phys[None, :], F.shape)
    W = np.broadcast_to(freqs[:, None], F.shape)          # cpd

    keep = (K >= k_min) & (K <= k_max) & (W >= 1.0 / t_max) & (W <= 1.0 / t_min)
    # The pure-mean / zero-frequency plane never passes (t_max finite), and
    # k bounds are inclusive like the NCL original.

    if wave is not None:
        k_dim = K * 2.0 * np.pi / (2.0 * np.pi * EARTH_RADIUS)   # rad m-1 = K / a
        w_dim = W * 2.0 * np.pi / SECONDS_PER_DAY                # rad s-1
        if h_min is not None:
            om_lo = _wave_frequency_bounds(wave, k_dim, float(h_min))
            keep &= w_dim >= om_lo
        if h_max is not None:
            om_hi = _wave_frequency_bounds(wave, k_dim, float(h_max))
            keep &= w_dim <= om_hi

    F = np.where(keep, F, 0.0)
    out = np.fft.ifft(F, axis=1)
    out = np.fft.irfft(out, n=nt, axis=0)
    return np.real(out)


# ---------------------------------------------------------------------------
# Operational mode boxes (constants filled from the verified WK99/WW01 /
# operational-monitoring research; see generate_hovmollers.py for use).
# Each entry: t (days), k (physical, +east), h (equivalent depth m) or None,
# wave (dispersion mask) or None.
# ---------------------------------------------------------------------------
MODES = {
    # Wheeler & Weickmann 2001 Table 1 / Schreck operational monitor:
    "mjo":    dict(t=(30.0, 96.0), k=(1.0, 5.0), h=None, wave=None),
    "kelvin": dict(t=(2.5, 20.0), k=(1.0, 14.0), h=(8.0, 90.0), wave="kelvin"),
    "er":     dict(t=(9.7, 48.0), k=(-10.0, -1.0), h=(8.0, 90.0), wave="er"),
    # Merged MRG/TD-type ("easterly wave") band per Schreck's monitor:
    "mrg_td": dict(t=(2.5, 10.0), k=(-14.0, -1.0), h=None, wave=None),
    # WW01 low-frequency band: periods >= 120 days at planetary scales
    # (both directions — standing/ENSO-timescale variability). In the
    # real-time zero-padded window this is the MOST endpoint-damped band
    # of all; callers must surface the WW01 caveat on the plot. The
    # t_max=9999 bound still excludes the pure time-mean (omega = 0
    # fails W >= 1/t_max).
    "lowfreq": dict(t=(120.0, 9999.0), k=(-10.0, 10.0), h=None, wave=None),
}


def filter_mode(data: np.ndarray, obs_per_day: float, mode: str) -> np.ndarray:
    spec = MODES[mode]
    h_min, h_max = spec["h"] if spec["h"] else (None, None)
    return kf_filter(data, obs_per_day,
                     t_min=spec["t"][0], t_max=spec["t"][1],
                     k_min=spec["k"][0], k_max=spec["k"][1],
                     h_min=h_min, h_max=h_max, wave=spec["wave"])
