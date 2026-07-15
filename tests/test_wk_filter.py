"""Locks for subseasonal/wk_filter.py — the numpy Wheeler-Kiladis filter.

Every test synthesizes a known plane wave and checks the filter keeps or
kills it, replicating the empirical verification run against the actual
NOAA-PSL reference implementation during the 2026-07-14 research pass
(kept variance ratio ~1.0, removed ~1e-28 there; thresholds here are
looser only for the taper's edge bleed).

    python -m unittest tests.test_wk_filter
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "subseasonal"))
import wk_filter  # noqa: E402


def synth(nt, nlon, s, period_days, eastward=True, amp=1.0):
    """cos wave of physical zonal wavenumber |s| moving east or west."""
    t = np.arange(nt, dtype=float)[:, None]
    x = np.arange(nlon, dtype=float)[None, :]
    sign = 1.0 if eastward else -1.0
    return amp * np.cos(2 * np.pi * (sign * s * x / nlon - t / period_days))


def kept_fraction(raw, filtered):
    denom = float((raw ** 2).sum())
    return float((filtered ** 2).sum()) / denom if denom else 0.0


class TestDirectionConvention(unittest.TestCase):
    """THE classic bug: numpy's positive zonal index is westward for
    retained omega>=0. The filter must map physical eastward k>0 right."""

    def test_eastward_wave_passes_eastward_box(self):
        w = synth(720, 360, s=5, period_days=8, eastward=True)
        f = wk_filter.kf_filter(w, 1.0, t_min=2.5, t_max=20, k_min=1, k_max=14)
        self.assertGreater(kept_fraction(w, f), 0.90)

    def test_westward_wave_killed_by_eastward_box(self):
        w = synth(720, 360, s=5, period_days=8, eastward=False)
        f = wk_filter.kf_filter(w, 1.0, t_min=2.5, t_max=20, k_min=1, k_max=14)
        self.assertLess(kept_fraction(w, f), 0.02)

    def test_westward_wave_passes_westward_box(self):
        w = synth(720, 360, s=4, period_days=20, eastward=False)
        f = wk_filter.kf_filter(w, 1.0, t_min=9.7, t_max=48, k_min=-10, k_max=-1)
        self.assertGreater(kept_fraction(w, f), 0.90)

    def test_no_off_by_one_at_box_edges(self):
        # The PSL port's blind axis reversal shifts the box by one
        # wavenumber (verified numerically there). Lock exact edges:
        inside = synth(720, 360, s=5, period_days=8, eastward=True)
        below = synth(720, 360, s=4, period_days=8, eastward=True)
        box = dict(t_min=2.5, t_max=20, k_min=5, k_max=7)
        self.assertGreater(kept_fraction(
            inside, wk_filter.kf_filter(inside, 1.0, **box)), 0.90)
        self.assertLess(kept_fraction(
            below, wk_filter.kf_filter(below, 1.0, **box)), 0.02)


class TestPeriodBounds(unittest.TestCase):
    def test_period_outside_box_killed(self):
        w = synth(720, 360, s=2, period_days=15, eastward=True)   # too fast for MJO
        f = wk_filter.filter_mode(w, 1.0, "mjo")
        self.assertLess(kept_fraction(w, f), 0.02)

    def test_mjo_band_passes(self):
        w = synth(720, 360, s=2, period_days=45, eastward=True)
        f = wk_filter.filter_mode(w, 1.0, "mjo")
        self.assertGreater(kept_fraction(w, f), 0.85)

    def test_mjo_westward_killed(self):
        w = synth(720, 360, s=2, period_days=45, eastward=False)
        f = wk_filter.filter_mode(w, 1.0, "mjo")
        self.assertLess(kept_fraction(w, f), 0.02)


class TestKelvinDispersion(unittest.TestCase):
    """h = 8-90 m bounds: phase speeds ~8.9-29.7 m/s at the equator."""

    def _kelvin_wave(self, s, h):
        # period implied by omega = k*c for equivalent depth h
        c = np.sqrt(wk_filter.G * h)
        k_dim = s / wk_filter.EARTH_RADIUS
        period_days = 2 * np.pi / (k_dim * c) / wk_filter.SECONDS_PER_DAY
        return synth(720, 360, s=s, period_days=period_days, eastward=True), period_days

    def test_on_dispersion_wave_passes(self):
        w, per = self._kelvin_wave(5, h=25.0)     # inside 8-90 m
        self.assertTrue(2.5 < per < 20, per)
        f = wk_filter.filter_mode(w, 1.0, "kelvin")
        self.assertGreater(kept_fraction(w, f), 0.85)

    def test_too_slow_wave_killed(self):
        w, per = self._kelvin_wave(5, h=2.0)      # below the h=8 curve
        f = wk_filter.filter_mode(w, 1.0, "kelvin")
        self.assertLess(kept_fraction(w, f), 0.05)


class TestERDispersion(unittest.TestCase):
    def test_er_wave_on_curve_passes(self):
        s, h = -4, 25.0
        c = np.sqrt(wk_filter.G * h)
        k_dim = s / wk_filter.EARTH_RADIUS
        om = abs(-wk_filter.BETA * k_dim / (k_dim ** 2 + 3 * wk_filter.BETA / c))
        period_days = 2 * np.pi / om / wk_filter.SECONDS_PER_DAY
        self.assertTrue(9.7 < period_days < 48, period_days)
        w = synth(720, 360, s=abs(s), period_days=period_days, eastward=False)
        f = wk_filter.filter_mode(w, 1.0, "er")
        self.assertGreater(kept_fraction(w, f), 0.85)


class TestLowFreq(unittest.TestCase):
    """WW01 low-frequency band: >=120-day periods, |k| <= 10, either
    direction; the pure time-mean must never pass."""

    def test_slow_planetary_wave_passes_both_directions(self):
        for eastward in (True, False):
            w = synth(720, 360, s=3, period_days=200, eastward=eastward)
            f = wk_filter.filter_mode(w, 1.0, "lowfreq")
            self.assertGreater(kept_fraction(w, f), 0.80, eastward)

    def test_intraseasonal_wave_killed(self):
        w = synth(720, 360, s=2, period_days=45, eastward=True)   # MJO-band
        f = wk_filter.filter_mode(w, 1.0, "lowfreq")
        self.assertLess(kept_fraction(w, f), 0.02)

    def test_time_mean_killed(self):
        w = np.ones((720, 360))
        f = wk_filter.filter_mode(w, 1.0, "lowfreq")
        # detrend removes the mean before the FFT anyway; belt and braces
        self.assertLess(float(np.abs(f).max()), 1e-8)


class TestPreprocessing(unittest.TestCase):
    def test_detrend_removes_mean_and_trend(self):
        nt, nlon = 400, 60
        t = np.arange(nt, dtype=float)[:, None]
        data = 5.0 + 0.03 * t + np.zeros((nt, nlon))
        out = wk_filter.detrend_taper(data)
        self.assertLess(float(np.abs(out).max()), 1e-8)

    def test_filter_output_shape_and_realness(self):
        rng = np.random.default_rng(7)
        w = rng.standard_normal((365, 360))
        f = wk_filter.kf_filter(w, 1.0, 2.5, 20, 1, 14)
        self.assertEqual(f.shape, (365, 360))
        self.assertTrue(np.isrealobj(f))

    def test_filtered_variance_is_subset(self):
        rng = np.random.default_rng(11)
        w = rng.standard_normal((365, 360))
        f = wk_filter.filter_mode(w, 1.0, "mjo")
        self.assertLess(kept_fraction(w, f), 0.25)   # narrow box keeps little noise


if __name__ == "__main__":
    unittest.main()
