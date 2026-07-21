"""Unit tests for the MJO OLR reconstruction (subseasonal/mjo_reconstruct)
and the Hovmöller's forecast-PC loader (generate_hovmollers._load_fc_pcs).

The reconstruction is the exact inverse of the WH04 projection in
rmm_wh04.py: these tests pin (a) the de-normalization constants (a full
432-vector rank-2 reconstruction must re-project to the SAME PCs), (b)
unit sanity (peak MJO amplitude ~2 gives O(15-30) W m-2 OLR anomalies),
and (c) the loader's no-fallback contract (missing / stale / malformed
PC files skip the forecast tail — never raw model OLR).
"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "subseasonal"))
import mjo_reconstruct  # noqa: E402
import rmm_wh04  # noqa: E402


class TestOlrFromPcs(unittest.TestCase):
    def test_shapes_and_scalars(self):
        out = mjo_reconstruct.olr_from_pcs(1.0, 0.5)
        self.assertEqual(out.shape, (1, 144))
        out = mjo_reconstruct.olr_from_pcs([1.0, -1.0, 0.0],
                                           [0.0, 2.0, 0.0])
        self.assertEqual(out.shape, (3, 144))
        # zero PCs reconstruct exactly zero anomaly
        self.assertTrue(np.all(out[2] == 0.0))

    def test_mismatched_lengths_raise(self):
        with self.assertRaises(ValueError):
            mjo_reconstruct.olr_from_pcs([1.0, 2.0], [1.0])

    def test_linearity(self):
        a = mjo_reconstruct.olr_from_pcs(0.7, -1.2)
        b = mjo_reconstruct.olr_from_pcs(1.4, -2.4)
        np.testing.assert_allclose(b, 2.0 * a, rtol=1e-12)

    def test_nan_pcs_yield_nan_rows(self):
        out = mjo_reconstruct.olr_from_pcs([1.0, np.nan], [0.5, 1.0])
        self.assertTrue(np.isfinite(out[0]).all())
        self.assertTrue(np.isnan(out[1]).all())

    def test_roundtrip_recovers_pcs(self):
        """De-normalization inversion, exactly: reconstruct all three
        fields from (pc1, pc2) with the documented formula and re-project
        through rmm_wh04.project — the PCs must come back identically
        (EOF orthonormality). This pins the constants AND the block
        order (OLR = the first 144 entries)."""
        e1, e2 = rmm_wh04.load_eofs()
        pc1 = np.array([1.3, -0.4, 2.0])
        pc2 = np.array([-0.6, 1.8, 0.0])
        c1 = pc1 * rmm_wh04.PC1_NORM
        c2 = pc2 * rmm_wh04.PC2_NORM
        olr = mjo_reconstruct.olr_from_pcs(pc1, pc2, eofs=(e1, e2))
        u850 = rmm_wh04.NORM_U850 * (np.outer(c1, e1[144:288])
                                     + np.outer(c2, e2[144:288]))
        u200 = rmm_wh04.NORM_U200 * (np.outer(c1, e1[288:])
                                     + np.outer(c2, e2[288:]))
        p1, p2 = rmm_wh04.project(olr, u850, u200, e1, e2)
        # tolerance loosened only by EOF near- (not exact) orthogonality
        np.testing.assert_allclose(p1, pc1, atol=2e-2)
        np.testing.assert_allclose(p2, pc2, atol=2e-2)

    def test_unit_sanity_peak_mjo(self):
        """Amplitude-2 MJO must give O(15-30) W m-2 peak OLR anomalies,
        in every phase orientation."""
        for ang in np.arange(0.0, 360.0, 45.0):
            pc1 = 2.0 * np.cos(np.radians(ang))
            pc2 = 2.0 * np.sin(np.radians(ang))
            peak = float(np.abs(
                mjo_reconstruct.olr_from_pcs(pc1, pc2)).max())
            self.assertGreater(peak, 10.0,
                               f"phase angle {ang}: peak {peak}")
            self.assertLess(peak, 45.0,
                            f"phase angle {ang}: peak {peak}")


class TestLoadFcPcs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(REPO))
        import generate_hovmollers
        cls.gh = generate_hovmollers

    def _write(self, d: Path, file_model: str, **over):
        sfx = {"gefs": "", "ifs": "_ifs", "ens": "_ens"}[file_model]
        init = dt.datetime.now(dt.timezone.utc).replace(
            tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
        doc = {"model": file_model, "label": file_model.upper(),
               "init": init.strftime("%Y-%m-%dT%H:%M:%SZ"),
               "dates": [(init.date() + dt.timedelta(days=i + 1))
                         .isoformat() for i in range(5)],
               "mean_pc1": [1.0, 1.1, 1.2, 1.1, 1.0],
               "mean_pc2": [0.5, 0.4, 0.3, 0.2, 0.1]}
        doc.update(over)
        (d / f"mjo_fc_pcs{sfx}.json").write_text(json.dumps(doc))
        return init

    def test_fresh_file_loads(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            init = self._write(d, "gefs")
            got = self.gh._load_fc_pcs(d, "gefs")
            self.assertIsNotNone(got)
            g_init, g_dates, pc1, pc2, label = got
            self.assertEqual(g_init, init)
            self.assertEqual(len(g_dates), 5)
            self.assertEqual(pc1.shape, (5,))
            self.assertEqual(label, "GEFS")

    def test_suffix_per_model(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._write(d, "ens")
            self.assertIsNone(self.gh._load_fc_pcs(d, "gefs"))
            self.assertIsNotNone(self.gh._load_fc_pcs(d, "ens"))

    def test_missing_file_skips(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(self.gh._load_fc_pcs(Path(td), "gefs"))

    def test_stale_init_skips(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            old = dt.datetime.now(dt.timezone.utc).replace(
                tzinfo=None) - dt.timedelta(days=5)
            self._write(d, "gefs",
                        init=old.strftime("%Y-%m-%dT%H:%M:%SZ"))
            self.assertIsNone(self.gh._load_fc_pcs(d, "gefs"))

    def test_model_mismatch_skips(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._write(d, "gefs", model="ens")
            self.assertIsNone(self.gh._load_fc_pcs(d, "gefs"))

    def test_length_mismatch_skips(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._write(d, "gefs", mean_pc1=[1.0, 2.0])
            self.assertIsNone(self.gh._load_fc_pcs(d, "gefs"))

    def test_malformed_json_skips(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "mjo_fc_pcs.json").write_text("{not json")
            self.assertIsNone(self.gh._load_fc_pcs(d, "gefs"))


class TestOlrWaveSets(unittest.TestCase):
    """The OLR panel's wave-set contract: MJO default, Kelvin the only
    optional second mode, no 'all'; retired keys covered by compat."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(REPO))
        import generate_hovmollers
        cls.gh = generate_hovmollers

    def test_olr_wave_sets(self):
        self.assertEqual(list(self.gh.OLR_WAVE_SETS),
                         ["mjo", "kelvin", "mjo+kelvin", "none"])
        self.assertEqual(self.gh.OLR_WAVE_SETS["mjo"], ["mjo"])
        self.assertEqual(self.gh.OLR_WAVE_SETS["mjo+kelvin"],
                         ["mjo", "kelvin"])
        self.assertNotIn("all", self.gh.OLR_WAVE_SETS)

    def test_compat_covers_retired_keys(self):
        old = {"all", "mjo", "kelvin", "er", "mrgtd", "mrgtd_er",
               "lowfreq", "none"}
        covered = set(self.gh.OLR_WAVE_SETS) | set(self.gh.OLR_WAVE_COMPAT)
        self.assertTrue(old <= covered, old - covered)

    def test_wk_filter_boxes_match_spec(self):
        """MJO eastward k=1-5 / 30-96 d; Kelvin eastward k=1-14 /
        2.5-20 d (NOAA PSL conventions)."""
        sys.path.insert(0, str(REPO / "subseasonal"))
        import wk_filter
        self.assertEqual(wk_filter.MODES["mjo"]["k"], (1.0, 5.0))
        self.assertEqual(wk_filter.MODES["mjo"]["t"], (30.0, 96.0))
        self.assertEqual(wk_filter.MODES["kelvin"]["k"], (1.0, 14.0))
        self.assertEqual(wk_filter.MODES["kelvin"]["t"], (2.5, 20.0))


if __name__ == "__main__":
    unittest.main()
