"""vp_windows: the linearity identity the chi archive rests on, the
Lanczos 20-100-day bandpass response, and window coverage gating."""

import datetime as dt
import pathlib
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent
                       / "subseasonal"))
import chi_core  # noqa: E402
import vp_windows  # noqa: E402


def _synthetic_wind(seed: int, lats, lons):
    """Smooth random large-scale wind field."""
    rng = np.random.default_rng(seed)
    lam = np.radians(lons)[None, :]
    phi = np.radians(lats)[:, None]
    u = np.zeros((lats.size, lons.size))
    v = np.zeros_like(u)
    for m in range(1, 4):
        for n in range(1, 3):
            a, b, c, d = rng.normal(size=4)
            u += a * np.cos(m * lam) * np.cos(n * phi) + \
                b * np.sin(m * lam) * np.sin(phi) * np.cos(phi)
            v += c * np.sin(m * lam) * np.cos(n * phi) + \
                d * np.cos(m * lam) * np.sin(phi) * np.cos(phi)
    return 10 * u, 10 * v


class TestLinearity(unittest.TestCase):
    """mean(chi(u_i, v_i)) == chi(mean(u), mean(v)) — the identity that
    lets the archive store daily chi instead of daily winds."""

    def test_mean_of_chi_equals_chi_of_mean(self):
        lats = np.arange(90, -90.1, -2.5)
        lons = np.arange(0, 360, 2.5)
        us, vs, chis = [], [], []
        for seed in range(4):
            u, v = _synthetic_wind(seed, lats, lons)
            us.append(u)
            vs.append(v)
            chi, _, _ = chi_core.chi_from_uv(u, v, lats, lons)
            chis.append(chi)
        chi_of_mean, _, _ = chi_core.chi_from_uv(
            np.mean(us, axis=0), np.mean(vs, axis=0), lats, lons)
        mean_of_chi = np.mean(chis, axis=0)
        scale = np.abs(chi_of_mean).max()
        self.assertGreater(scale, 0)
        np.testing.assert_allclose(mean_of_chi / scale,
                                   chi_of_mean / scale, atol=1e-6)


class TestLanczos(unittest.TestCase):
    def _response(self, period_days: float) -> float:
        """Amplitude response of the full (centered) filter at a period."""
        w = vp_windows.lanczos_bandpass_weights()
        n = 3000
        t = np.arange(n)
        sig = np.sin(2 * np.pi * t / period_days)
        filt = np.convolve(sig, w, mode="same")
        core = slice(len(w), n - len(w))
        return float(np.abs(filt[core]).max())

    def test_passband(self):
        self.assertGreater(self._response(50.0), 0.8)
        self.assertGreater(self._response(30.0), 0.8)

    def test_stopbands(self):
        self.assertLess(self._response(8.0), 0.12)
        self.assertLess(self._response(365.0), 0.12)

    def test_mean_mostly_removed(self):
        # a 121-tap truncated Lanczos leaks a little DC (~2.6%); the input
        # is climo-removed anomaly, so that residual is negligible — but
        # keep it bounded so a future edit can't silently break the band
        w = vp_windows.lanczos_bandpass_weights()
        self.assertLess(abs(w.sum()), 0.03)

    def test_realtime_endpoint(self):
        w = vp_windows.lanczos_bandpass_weights()
        rng = np.random.default_rng(0)
        anom = rng.normal(size=(400, 3, 4))
        filt, retained = vp_windows.bandpass_latest(anom, w)
        self.assertEqual(filt.shape, (3, 4))
        self.assertTrue(0.3 < retained < 0.8)   # one-sided => damped, stated
        # too-short archive refuses instead of faking a map
        with self.assertRaises(ValueError):
            vp_windows.bandpass_latest(anom[:30], w)


class TestWindowMean(unittest.TestCase):
    def test_mean_and_gating(self):
        end = dt.date(2026, 7, 12)
        times = [end - dt.timedelta(days=i) for i in range(40)][::-1]
        stack = np.arange(40, dtype=float)[:, None] * np.ones((40, 2))
        mean, used = vp_windows.window_mean(times, stack, 5, end)
        self.assertEqual(used, 5)
        np.testing.assert_allclose(mean, np.mean(stack[-5:], axis=0))
        # drop most of the window's days -> refuses
        sparse_times = times[:-25]
        with self.assertRaises(ValueError):
            vp_windows.window_mean(sparse_times, stack[:-25], 30, end)


class TestArchiveRoundtrip(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "arch.nc"
            times = [dt.date(2026, 7, 1) + dt.timedelta(days=i)
                     for i in range(3)]
            lats = np.arange(90, -90.1, -1.0)
            lons = np.arange(0, 360, 1.0)
            chi = np.random.default_rng(1).normal(
                size=(3, 2, lats.size, lons.size)).astype(np.float32)
            vp_windows.save_archive(p, times, [200.0, 850.0], lats, lons,
                                    chi, np.array([4, 4, 3]))
            t2, lev2, la2, lo2, chi2, nc2 = vp_windows.load_archive(p)
            self.assertEqual(t2, times)
            np.testing.assert_allclose(chi2, chi, atol=1e-6)
            self.assertEqual(list(nc2), [4, 4, 3])


if __name__ == "__main__":
    unittest.main()
