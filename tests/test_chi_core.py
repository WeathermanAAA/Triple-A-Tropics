"""Analytic validation of the chi_core spectral Poisson solve: build a wind
field that is the exact gradient of a known low-order velocity potential and
demand the solver recover that potential (pattern correlation ~1, amplitude
within the finite-difference error of the 1-degree grid)."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "subseasonal"))
import chi_core  # noqa: E402


def spherical_harmonic_chi(lats, lons, amp=5e6):
    """chi = amp * Re[Y_3^2] (up to normalization): a smooth, zero-mean,
    planetary-scale potential comfortably inside T21."""
    phi = np.radians(lats)[:, None]
    lam = np.radians(lons)[None, :]
    # associated Legendre P_3^2(sin phi) = 15 sin(phi) cos^2(phi)
    p32 = 15.0 * np.sin(phi) * np.cos(phi) ** 2
    return amp * p32 * np.cos(2 * lam)


class TestChiRecovery(unittest.TestCase):
    def setUp(self):
        self.lats = np.arange(-90.0, 90.1, 1.0)
        self.lons = np.arange(0.0, 360.0, 1.0)
        self.chi_true = spherical_harmonic_chi(self.lats, self.lons)
        # wind = grad(chi): u = dchi/dx / (a cos), v = dchi/dy / a
        phi = np.radians(self.lats)
        lam = np.radians(self.lons)
        cos = np.cos(phi)[:, None]
        cos_safe = np.where(np.abs(cos) < 1e-6, 1e-6, cos)
        dchidl = (np.roll(self.chi_true, -1, axis=1)
                  - np.roll(self.chi_true, 1, axis=1)) / (
            (np.roll(lam, -1) - np.roll(lam, 1)) % (2 * np.pi))[None, :]
        self.u = dchidl / (chi_core.A_EARTH * cos_safe)
        self.v = np.gradient(self.chi_true, phi, axis=0) / chi_core.A_EARTH
        self.u[0, :] = self.u[-1, :] = 0.0

    def test_chi_recovered(self):
        chi, u_chi, v_chi = chi_core.chi_from_uv(self.u, self.v,
                                                 self.lats, self.lons)
        # compare away from the poles (FD pole rows are display-clamped)
        sel = np.abs(self.lats) <= 80
        a = chi[sel, :].ravel()
        b = self.chi_true[sel, :].ravel()
        r = np.corrcoef(a, b)[0, 1]
        self.assertGreater(r, 0.995, f"pattern correlation {r:.4f}")
        scale = np.polyfit(b, a, 1)[0]
        self.assertAlmostEqual(scale, 1.0, delta=0.05,
                               msg=f"amplitude scale {scale:.3f}")

    def test_divergent_wind_matches_input(self):
        chi, u_chi, v_chi = chi_core.chi_from_uv(self.u, self.v,
                                                 self.lats, self.lons)
        sel = np.abs(self.lats) <= 70
        r_u = np.corrcoef(u_chi[sel, :].ravel(), self.u[sel, :].ravel())[0, 1]
        r_v = np.corrcoef(v_chi[sel, :].ravel(), self.v[sel, :].ravel())[0, 1]
        self.assertGreater(r_u, 0.99)
        self.assertGreater(r_v, 0.99)

    def test_t21_kills_small_scales(self):
        # add a high-wavenumber (m=40) ripple to the wind: T21 output must not
        # contain it (truncation is the point of the product)
        lam = np.radians(self.lons)[None, :]
        ripple = 3.0 * np.cos(40 * lam) * np.ones((self.lats.size, 1))
        chi, _, _ = chi_core.chi_from_uv(self.u + ripple, self.v,
                                         self.lats, self.lons)
        # zonal wavenumber-40 power in the equatorial band
        band = chi[np.abs(self.lats) <= 10, :]
        spec = np.abs(np.fft.rfft(band, axis=1)).mean(axis=0)
        self.assertLess(spec[40], spec[2] * 1e-3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
