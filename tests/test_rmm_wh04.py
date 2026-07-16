"""Unit tests for the WH04 RMM projection math (subseasonal/rmm_wh04.py).
Pure-math checks; the live closure against BoM's published RMM series is
a separate verification run (see the Phase-3 AGENT_STATUS entry)."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "subseasonal"))
import rmm_wh04  # noqa: E402


class TestEofFile(unittest.TestCase):
    def test_loads_unit_norm(self):
        e1, e2 = rmm_wh04.load_eofs()
        self.assertEqual(e1.shape, (432,))
        self.assertAlmostEqual(float((e1 ** 2).sum()), 1.0, places=4)
        self.assertAlmostEqual(float((e2 ** 2).sum()), 1.0, places=4)
        # near-orthogonal (distinct EOFs of the same decomposition)
        self.assertLess(abs(float(e1 @ e2)), 0.05)


class TestProjection(unittest.TestCase):
    def test_pure_eof1_projects_to_pc1(self):
        e1, e2 = rmm_wh04.load_eofs()
        k = 3.0
        # build field anomalies whose normalized concat == k * eof1
        olr = k * e1[:144][None, :] * rmm_wh04.NORM_OLR
        u850 = k * e1[144:288][None, :] * rmm_wh04.NORM_U850
        u200 = k * e1[288:][None, :] * rmm_wh04.NORM_U200
        pc1, pc2 = rmm_wh04.project(olr, u850, u200, e1, e2)
        self.assertAlmostEqual(float(pc1[0]), k / rmm_wh04.PC1_NORM,
                               places=6)
        self.assertLess(abs(float(pc2[0])), 0.02)

    def test_trailing_mean_removes_constant(self):
        rng = np.random.default_rng(1)
        osc = np.sin(np.arange(200) / 7)[:, None] * np.ones((1, 144))
        series = osc + 5.0 + rng.normal(0, 0.01, (200, 144))
        out = rmm_wh04.remove_trailing_mean(series)
        # after the 120-day window is full, the +5 offset is gone
        self.assertLess(abs(float(np.nanmean(out[150:]))), 0.15)

    def test_trailing_mean_skips_nan_rows(self):
        series = np.ones((130, 144))
        series[50] = np.nan
        out = rmm_wh04.remove_trailing_mean(series)
        self.assertTrue(np.isfinite(out[129]).all())
        self.assertAlmostEqual(float(out[129].mean()), 0.0, places=6)

    def test_phase_octants(self):
        # centers per the site's PHASE_ANGLE convention (WH04 figure)
        cases = {1: 202.5, 2: 247.5, 3: 292.5, 4: 337.5,
                 5: 22.5, 6: 67.5, 7: 112.5, 8: 157.5}
        for want, ang in cases.items():
            a = np.radians(ang)
            got = rmm_wh04.phase_of(np.cos(a) * 2, np.sin(a) * 2)
            self.assertEqual(got, want, f"angle {ang}")


if __name__ == "__main__":
    unittest.main()
