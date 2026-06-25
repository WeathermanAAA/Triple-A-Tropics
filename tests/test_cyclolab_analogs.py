"""Unit tests for the cyclolab_analogs pure-math layer (no tropycal / no network):
haversine, resample (shape + lead-time), the TSAI point-separation score + polygon
area, SSHS categories, confidence labels, and sid normalization."""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cyclolab_analogs as A


class TestHaversine(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(A.haversine_km(25, -80, 25, -80), 0.0)

    def test_one_degree_lat(self):
        # 1 deg latitude ~ 111.2 km anywhere
        self.assertAlmostEqual(A.haversine_km(0, 0, 1, 0), 111.19, delta=0.5)
        self.assertAlmostEqual(A.haversine_km(40, -70, 41, -70), 111.19, delta=0.5)

    def test_lon_shrinks_with_lat(self):
        # 1 deg lon is ~111 km at the equator, ~78 km at 45N (cos45)
        self.assertAlmostEqual(A.haversine_km(0, 0, 0, 1), 111.19, delta=0.5)
        self.assertAlmostEqual(A.haversine_km(45, 0, 45, 1), 111.19 * math.cos(math.radians(45)), delta=1.0)


class TestResampleShape(unittest.TestCase):
    def test_length_and_endpoints(self):
        lats = [10, 11, 12, 13, 14]
        lons = [-40, -42, -44, -46, -48]
        vmax = [30, 50, 70, 90, 110]
        rlat, rlon, rv = A.resample_shape(lats, lons, vmax, n=20)
        self.assertEqual(len(rlat), 20)
        self.assertAlmostEqual(rlat[0], 10, places=3)
        self.assertAlmostEqual(rlat[-1], 14, places=3)
        self.assertAlmostEqual(rv[0], 30, places=3)
        self.assertAlmostEqual(rv[-1], 110, places=3)

    def test_equal_spacing_on_straight_track(self):
        # A meridional track resampled to N points -> equal latitude steps.
        lats = list(range(10, 31))         # 10..30
        lons = [-50.0] * len(lats)
        vmax = [40.0] * len(lats)
        rlat, _, _ = A.resample_shape(lats, lons, vmax, n=21)
        steps = [round(rlat[i + 1] - rlat[i], 4) for i in range(len(rlat) - 1)]
        self.assertTrue(max(steps) - min(steps) < 0.05, steps)

    def test_antimeridian(self):
        # crossing 180 should not blow up the cumulative distance / output range
        lats = [10, 11, 12]
        lons = [179, -179, -178]
        rlat, rlon, rv = A.resample_shape(lats, lons, [40, 40, 40], n=20)
        self.assertEqual(len(rlon), 20)
        self.assertTrue(all(-180 <= x <= 180 for x in rlon))


class TestScore(unittest.TestCase):
    def test_identical_is_zero(self):
        a = ([10, 11, 12], [-40, -41, -42], [50, 60, 70])
        score, dt, di, area = A.score_pair(a, a)
        self.assertAlmostEqual(score, 0.0, places=6)
        self.assertAlmostEqual(dt, 0.0, places=6)
        self.assertAlmostEqual(di, 0.0, places=6)
        self.assertAlmostEqual(area, 0.0, places=3)

    def test_known_offsets(self):
        # b is a's track shifted 1 deg north (~111km) at every point, +10 kt
        a = ([10, 10, 10], [-40, -41, -42], [50, 50, 50])
        b = ([11, 11, 11], [-40, -41, -42], [60, 60, 60])
        score, d_track, d_int, _ = A.score_pair(a, b)
        self.assertAlmostEqual(d_track, 111.19, delta=0.5)
        self.assertAlmostEqual(d_int, 10.0, places=6)      # RMS of constant 10
        expect = A.W_TRACK * (d_track / A.TRACK_SCALE_KM) + A.W_INT * (10.0 / A.INT_SCALE_KT)
        self.assertAlmostEqual(score, expect, places=6)

    def test_rms_not_mean(self):
        # D_int is RMS: [0, 20] -> sqrt(200) ~ 14.14, not mean 10
        a = ([0, 0], [0, 0], [50, 50])
        b = ([0, 0], [0, 0], [50, 70])
        _, _, d_int, _ = A.score_pair(a, b)
        self.assertAlmostEqual(d_int, math.sqrt((0 + 400) / 2), places=4)

    def test_polygon_area_square(self):
        # two parallel tracks 1 deg apart over ~1 deg lon span near equator
        # area ~ 111km * 111km ~ 12353 km^2 (rectangle)
        a = ([0, 0], [0, 1], [50, 50])
        b = ([1, 1], [0, 1], [50, 50])
        _, _, _, area = A.score_pair(a, b)
        self.assertAlmostEqual(area, 111.19 * 111.19, delta=200)


class TestLeadtime(unittest.TestCase):
    def test_overlap_window(self):
        # a lives 0..48h, b lives 0..72h -> overlap 48h
        ha = [0, 24, 48]; hb = [0, 24, 48, 72]
        a, b, overlap = A.resample_leadtime(
            ha, [10, 12, 14], [-40, -41, -42], [40, 60, 80],
            hb, [10, 11, 12, 13], [-40, -40.5, -41, -41.5], [40, 50, 60, 70])
        self.assertEqual(overlap, 48.0)
        self.assertEqual(len(a[0]), 20)
        self.assertEqual(len(b[0]), 20)

    def test_no_overlap(self):
        a, b, overlap = A.resample_leadtime(
            [0], [10], [-40], [40], [0], [10], [-40], [40])
        self.assertEqual(overlap, 0.0)
        self.assertIsNone(a)


class TestCategoriesAndConfidence(unittest.TestCase):
    def test_sshs(self):
        self.assertEqual(A.sshs_category(20), "TD")
        self.assertEqual(A.sshs_category(34), "TS")
        self.assertEqual(A.sshs_category(64), "C1")
        self.assertEqual(A.sshs_category(96), "C3")
        self.assertEqual(A.sshs_category(137), "C5")
        self.assertEqual(A.sshs_category(None), "TD")

    def test_confidence_thresholds(self):
        self.assertEqual(A.confidence_label(0.5, None, "shape"), "high")
        self.assertEqual(A.confidence_label(0.6, None, "shape"), "high")      # boundary
        self.assertEqual(A.confidence_label(0.61, None, "shape"), "moderate")
        self.assertEqual(A.confidence_label(1.2, None, "shape"), "moderate")  # boundary
        self.assertEqual(A.confidence_label(1.21, None, "shape"), "low")

    def test_confidence_leadtime_short_overlap_is_low(self):
        # great score but too little shared life -> not trustworthy
        self.assertEqual(A.confidence_label(0.3, 12.0, "leadtime"), "low")
        self.assertEqual(A.confidence_label(0.3, 23.9, "leadtime"), "low")
        self.assertEqual(A.confidence_label(0.3, 24.0, "leadtime"), "high")   # boundary
        self.assertEqual(A.confidence_label(0.3, 72.0, "leadtime"), "high")


class TestCanonicalAndRecon(unittest.TestCase):
    def test_canonical_sid(self):
        self.assertEqual(A.canonical_sid("AL092004", "AL"), "NHC_AL092004")
        self.assertEqual(A.canonical_sid("EP012024", "EP"), "NHC_EP012024")
        self.assertEqual(A.canonical_sid("WP072026", "WP"), "JTWC_WP072026")

    def test_recon_available(self):
        A.set_recon_index(atcfs=["al012026", "ep112025"], slugs=["al_melissa_2025"])
        try:
            self.assertTrue(A.recon_available("AL012026", "Arthur", 2026, "AL"))   # atcf
            self.assertTrue(A.recon_available("AL142025", "Melissa", 2025, "AL"))  # name-slug
            self.assertFalse(A.recon_available("AL091995", "Felix", 1995, "AL"))   # pre-2007
            self.assertFalse(A.recon_available("AL992026", "Ninety", 2026, "AL"))  # absent
        finally:
            A.set_recon_index()   # reset module state


class TestNormalizeSid(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(A.normalize_sid("JTWC_WP072026"), ("WP072026", "WP"))
        self.assertEqual(A.normalize_sid("wp072026"), ("WP072026", "WP"))
        self.assertEqual(A.normalize_sid("AL012026"), ("AL012026", "AL"))
        self.assertEqual(A.normalize_sid("al142005"), ("AL142005", "AL"))

    def test_rejects_non_designated(self):
        self.assertEqual(A.normalize_sid("AL902026"), ("AL902026", "AL"))  # invest-id IS parseable structurally
        self.assertEqual(A.normalize_sid("garbage"), (None, None))
        self.assertEqual(A.normalize_sid(""), (None, None))


if __name__ == "__main__":
    unittest.main()
