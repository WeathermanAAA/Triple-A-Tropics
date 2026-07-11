"""Unit tests for tcprimed.mwi - the MW-imager intensity extraction core.

Network-free synthetic-swath tests: known structures in, physics-facing
invariants out (ring statistics, azimuthal closure, land gate, model
evaluation + quality gate). The V=H trick makes every PCT variant equal the
constructed temperature field (all variant coefficient pairs differ by 1.0),
so ring values can be asserted exactly.
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tcprimed import mwi  # noqa: E402


def synthetic_meta(clat=15.0, clon=215.0, field_fn=None, nan_east=False):
    """A regular pseudo-swath over +/-5 deg with tb V=H=field_fn(r_km)."""
    step = 0.05
    lats = np.arange(clat - 5.0, clat + 5.0 + 1e-9, step)
    lons = np.arange(clon - 5.0, clon + 5.0 + 1e-9, step)
    LON, LAT = np.meshgrid(lons, lats)
    cosc = np.cos(np.radians(clat))
    r = np.hypot((LAT - clat) * mwi.KM_PER_DEG,
                 (LON - clon) * mwi.KM_PER_DEG * cosc)
    tb = field_fn(r) if field_fn else np.full_like(r, 280.0)
    tb = tb.astype(float)
    if nan_east:
        tb[:, lons > clon] = np.nan
    return {
        "clat": clat, "clon": clon,
        "lat89": LAT, "lon89": LON, "tb89v": tb.copy(), "tb89h": tb.copy(),
        "lat37": LAT, "lon37": LON, "tb37v": tb.copy(), "tb37h": tb.copy(),
    }


def cold_ring(r):
    """Warm 280 K everywhere, 200 K cold ring at 30-50 km."""
    out = np.full_like(r, 280.0)
    out[(r >= 30.0) & (r < 50.0)] = 200.0
    return out


class TestPct(unittest.TestCase):
    def test_formula_and_clip(self):
        v = np.array([280.0, 180.0, 40.0])
        h = np.array([278.0, 160.0, 300.0])
        p = mwi.pct(v, h, 1.818, 0.818)
        self.assertAlmostEqual(p[0], 1.818 * 280 - 0.818 * 278, places=6)
        self.assertAlmostEqual(p[1], 1.818 * 180 - 0.818 * 160, places=6)
        self.assertEqual(p[2], mwi.PCT_CLIP_LO)   # overshoot clipped

    def test_nan_safe(self):
        p = mwi.pct(np.array([np.nan]), np.array([200.0]), 1.7, 0.7)
        self.assertTrue(np.isnan(p[0]))


class TestRingSectorStats(unittest.TestCase):
    def test_cold_ring_recovered(self):
        meta = synthetic_meta(field_fn=cold_ring)
        stats = mwi.ring_sector_stats(meta)
        self.assertIsNotNone(stats)
        # every variant present (V=H synthetic -> all equal the field)
        for name in mwi.PCT_VARIANTS:
            self.assertIn(f"{name}_min", stats)
        mn = stats["pct89_min"]
        # rings fully inside the cold annulus (35-45 km -> rings 7,8) read 200
        self.assertLess(np.nanmax(mn[7]), 210.0)
        # a ring fully outside (100-105 km -> ring 20) reads warm
        self.assertGreater(np.nanmin(mn[20]), 270.0)
        # full coverage on a gap-free synthetic
        cov = stats["pct89_cnt"].sum() / stats["89_tot"].sum()
        self.assertGreater(cov, 0.98)

    def test_predictors_on_cold_ring(self):
        meta = synthetic_meta(field_fn=cold_ring)
        p = mwi.compute_predictors(mwi.ring_sector_stats(meta))
        self.assertLess(p["pct89_min50"], 205.0)
        self.assertLess(p["pct89_eyewall_min"], 205.0)
        # closed cold ring at the 220 K threshold across all sectors
        self.assertGreater(p["ring89_closure"], 0.95)
        # coldest azimuthal-mean ring inside the 30-50 km annulus
        self.assertGreaterEqual(p["cold_ring_radius89"], 25.0)
        self.assertLessEqual(p["cold_ring_radius89"], 55.0)
        # warm-rain 37 closure: field IS below 260 in the ring -> closed too
        self.assertGreater(p["ring37_closure"], 0.95)
        # K&J cyan+pink classes: with V=H the warm 280-K field has H >= 255
        # (bright cyan) everywhere and the 200-K ring is pink -> the fitted
        # annulus is fully closed and the flag trips
        self.assertGreater(p["kj_fracdark100"], 0.95)
        self.assertGreater(p["kj_ring_closure"], 0.95)
        self.assertEqual(p["ring37_flag"], 1.0)
        self.assertTrue(np.isfinite(p["kj_ring_radius_km"]))
        self.assertLess(p["pct89_cold275_100"], 0.35)   # only the ring is cold
        # deep-ocean box: no land
        self.assertLess(p["land_frac100"], 0.01)
        self.assertGreater(p["pct89_cov100"], 0.95)

    def test_half_swath_coverage_honesty(self):
        meta = synthetic_meta(field_fn=cold_ring, nan_east=True)
        p = mwi.compute_predictors(mwi.ring_sector_stats(meta))
        self.assertLess(p["pct89_cov100"], 0.65)
        self.assertGreater(p["pct89_cov100"], 0.35)
        # closure can only come from observed sectors
        self.assertLess(p["ring89_closure"], 0.65)

    def test_no_swath_returns_none(self):
        meta = synthetic_meta()
        for k in ("tb89v", "tb89h", "tb37v", "tb37h"):
            meta[k] = np.full_like(meta[k], np.nan)
        self.assertIsNone(mwi.ring_sector_stats(meta))


class TestLandGate(unittest.TestCase):
    def test_land_over_india(self):
        # 23N 80E is deep inside the subcontinent
        meta = synthetic_meta(clat=23.0, clon=80.0, field_fn=cold_ring)
        p = mwi.compute_predictors(mwi.ring_sector_stats(meta))
        self.assertGreater(p["land_frac100"], 0.9)

    def test_ocean_mid_pacific(self):
        gx, gy = mwi._grid_geometry(10.0, 215.0)   # 10N 145W
        self.assertFalse(mwi.land_mask_grid(gx, gy).any())


class TestModelEval(unittest.TestCase):
    MODEL = {
        "version": "test-0",
        "predictors": ["intercept", "pct89_min100", "ring37_closure"],
        "vmax": {"intercept": 300.0, "pct89_min100": -1.0,
                 "ring37_closure": 20.0},
        "mslp": {"intercept": 800.0, "pct89_min100": 0.8},
        "vmax_range": [15.0, 185.0],
        "mslp_range": [880.0, 1015.0],
        "gate": {"min_cov100": 0.6, "min_cov_eyewall": 0.6,
                 "max_land_frac100": 0.25},
        "error_by_bin": [{"lo": 0, "hi": 64, "mae": 9.0},
                         {"lo": 64, "hi": 200, "mae": 15.0}],
        "confidence_mae_cut": 13.0,
    }

    def preds(self, **over):
        base = {"pct89_min100": 200.0, "ring37_closure": 0.5,
                "pct89_cov100": 1.0, "pct89_cov_eyewall": 1.0,
                "land_frac100": 0.0}
        base.update(over)
        return base

    def test_linear_eval(self):
        est = mwi.apply_model(self.preds(), self.MODEL)
        self.assertTrue(est["usable"])
        self.assertAlmostEqual(est["vmax_kt"], 300 - 200 + 10.0, places=5)
        self.assertAlmostEqual(est["mslp_hpa"], 800 + 160.0, places=5)
        self.assertEqual(est["model_version"], "test-0")
        # 110 kt falls in the 64-200 bin -> mae 15 -> low confidence
        self.assertEqual(est["mae_kt"], 15.0)
        self.assertEqual(est["confidence"], "low")

    def test_confidence_moderate_in_low_bin(self):
        est = mwi.apply_model(self.preds(pct89_min100=260.0), self.MODEL)
        self.assertEqual(est["confidence"], "moderate")   # 50 kt -> mae 9

    def test_gate_land(self):
        est = mwi.apply_model(self.preds(land_frac100=0.5), self.MODEL)
        self.assertFalse(est["usable"])
        self.assertTrue(any("land" in r for r in est["reasons"]))

    def test_gate_coverage(self):
        est = mwi.apply_model(self.preds(pct89_cov100=0.3), self.MODEL)
        self.assertFalse(est["usable"])

    def test_gate_missing_predictor(self):
        est = mwi.apply_model(self.preds(ring37_closure=float("nan")),
                              self.MODEL)
        self.assertFalse(est["usable"])

    def test_range_clip(self):
        est = mwi.apply_model(self.preds(pct89_min100=320.0), self.MODEL)
        self.assertGreaterEqual(est["vmax_kt"], 15.0)

    def test_intensity_record_no_model_is_none(self):
        meta = synthetic_meta(field_fn=cold_ring)
        self.assertIsNone(
            mwi.intensity_record(meta, model=None, source="archive")
            if mwi.load_model() is None else None)

    def test_intensity_record_end_to_end(self):
        meta = synthetic_meta(field_fn=cold_ring)
        rec = mwi.intensity_record(meta, model=self.MODEL, source="live")
        self.assertIsNotNone(rec)
        self.assertTrue(rec["usable"])
        self.assertEqual(rec["source"], "live")
        self.assertIn("predictors", rec)
        # never raises on garbage input
        self.assertIsNone(mwi.intensity_record({"clat": 0.0, "clon": 0.0},
                                               model=self.MODEL, source="live"))


if __name__ == "__main__":
    unittest.main()
