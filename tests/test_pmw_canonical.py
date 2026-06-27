"""Colortable EXACTNESS guard for the four observed-MW products.

Asserts each canonical NRL Tb color table reproduces, within <=2/255 per
channel, the exact linear interpolation of its published RGB->value anchors
across every segment; that the value ranges are exact; that the offset
value-ranges render as hard steps (discrete-stepped behavior); and that the
two RGB recipes match their canonical closed form. Fails the build on any
deviation, so the tables can never silently drift from canonical.
"""
import os
import sys
import unittest

import numpy as np
from matplotlib.colors import ColorConverter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tcprimed import pmw_canonical as P  # noqa: E402

CC = ColorConverter()


def _rgb(c):
    return np.array(CC.to_rgb(c))


def _at(cmap, norm, v):
    return np.array(cmap(norm(float(v)))[:3])


class TestCanonicalColormaps(unittest.TestCase):
    def _check_table(self, cmap, norm, vals, colors):
        for (s, e), (c0, c1) in zip(vals, colors):
            span = e - s
            for f in (0.08, 0.25, 0.5, 0.75, 0.92):
                v = s + f * span
                exp = _rgb(c0) + (_rgb(c1) - _rgb(c0)) * f
                got = _at(cmap, norm, v)
                dmax = float(np.max(np.abs(got - exp))) * 255.0
                self.assertLessEqual(
                    dmax, 2.0,
                    f"{cmap.name} @ {v:.2f}K: got {tuple((got * 255).round())} "
                    f"exp {tuple((exp * 255).round())} (d={dmax:.2f}/255)")

    def test_37h_table_exact(self):
        self._check_table(P.cmap_37h(), P.norm_37h(), P._37H_VALS, P._37H_COLORS)

    def test_91h_table_exact(self):
        self._check_table(P.cmap_91h(), P.norm_91h(), P._91H_VALS, P._91H_COLORS)

    def test_value_ranges_exact(self):
        self.assertEqual((P.norm_37h().vmin, P.norm_37h().vmax), (125.0, 310.0))
        self.assertEqual((P.norm_91h().vmin, P.norm_91h().vmax), (105.0, 305.0))
        self.assertEqual(P._37H_TICKS[0], 125)
        self.assertEqual(P._37H_TICKS[-1], 310)
        self.assertEqual(P._91H_TICKS[0], 105)
        self.assertEqual(P._91H_TICKS[-1], 305)

    def test_91h_hard_steps(self):
        # the canonical 228 -> 228.1 and 254 -> 254.1 offsets are HARD color
        # steps (discrete-stepped behavior), not continuous ramps.
        c, n = P.cmap_91h(), P.norm_91h()
        for bnd in (228.0, 254.0):
            below = _at(c, n, bnd - 1.0)
            above = _at(c, n, bnd + 1.0)
            jump = float(np.max(np.abs(above - below))) * 255.0
            self.assertGreater(jump, 40.0, f"expected a hard step at {bnd}K (got {jump:.0f})")

    def test_color37_recipe_exact(self):
        v = np.array([260.0, 200.0, 285.0])
        h = np.array([200.0, 200.0, 120.0])
        r, g, b = P.color37_rgb(v, h)
        exp_r = np.clip((280.0 - (2.181 * v - 1.181 * h)) / 20.0, 0, 1)
        exp_g = np.clip((v - 180.0) / 120.0, 0, 1)
        exp_b = np.clip((h - 160.0) / 140.0, 0, 1)
        np.testing.assert_allclose(r, exp_r, atol=1e-6)
        np.testing.assert_allclose(g, exp_g, atol=1e-6)
        np.testing.assert_allclose(b, exp_b, atol=1e-6)

    def test_color91_recipe_exact(self):
        v = np.array([280.0, 220.0, 300.0])
        h = np.array([230.0, 210.0, 250.0])
        r, g, b = P.color91_rgb(v, h)
        exp_r = np.clip((310.0 - (1.818 * v - 0.818 * h)) / 90.0, 0, 1)
        exp_g = np.clip((h - 240.0) / 60.0, 0, 1)
        exp_b = np.clip((v - 270.0) / 20.0, 0, 1)
        np.testing.assert_allclose(r, exp_r, atol=1e-6)
        np.testing.assert_allclose(g, exp_g, atol=1e-6)
        np.testing.assert_allclose(b, exp_b, atol=1e-6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
