"""
Tests for the Ensemble Cyclone Centers platform (enscenters package).

Covers the science (closed-low detection on synthetic MSLP fields, antimeridian
safety, Atkinson-Holliday P->V), the declarative registry, and the manifest
merge / rolling-window prune. No network: ingest is exercised separately by a
live smoke run, see ENSEMBLE_DESIGN.md.

Run: python -m unittest discover tests
"""
import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from enscenters.detect import ah_vmax_kt, detect_centers, _normalize_lon
from enscenters import registry as reg
from enscenters.pipeline import merge_manifest, CENTER_FIELDS

# Global 0.25 grid like ECMWF open-data ENS: lat 90..-90 (descending), lon -180..179.75
LATS = np.arange(90.0, -90.25, -0.25)
LONS = np.arange(-180.0, 180.0, 0.25)
_LON2D, _LAT2D = np.meshgrid(LONS, LATS)


def _gauss_low(field, clat, clon, depth_hpa, radius_deg):
    dlon = ((_LON2D - clon + 180) % 360) - 180
    d2 = (_LAT2D - clat) ** 2 + (dlon * math.cos(math.radians(clat))) ** 2
    field -= depth_hpa * np.exp(-d2 / (2 * radius_deg ** 2))


class TestDetect(unittest.TestCase):
    def test_three_closed_lows(self):
        f = np.full_like(_LAT2D, 1013.0)
        _gauss_low(f, 20.0, -60.0, 60.0, 3.0)   # ~953 hPa Atlantic
        _gauss_low(f, -15.0, 90.0, 30.0, 3.0)   # ~983 hPa S Indian
        _gauss_low(f, 45.0, 150.0, 45.0, 4.0)   # ~968 hPa NW Pacific
        centers = detect_centers(f, LATS, LONS)
        self.assertTrue(2 <= len(centers) <= 4, f"got {len(centers)}")
        deepest = min(centers, key=lambda c: c["mslp_hpa"])
        self.assertAlmostEqual(deepest["lat"], 20.0, delta=1.0)
        self.assertAlmostEqual(deepest["lon"], -60.0, delta=1.0)
        self.assertAlmostEqual(deepest["mslp_hpa"], 953.0, delta=4.0)
        # plain python floats, ready for JSON
        for c in centers:
            self.assertIsInstance(c["mslp_hpa"], float)
            self.assertIsInstance(c["lat"], float)

    def test_open_trough_rejected(self):
        f = np.full_like(_LAT2D, 1013.0)
        # meridional trough (infinite in latitude) -> never closes
        f -= 12.0 * np.exp(-((_LON2D - 10.0) ** 2) / (2 * 4.0 ** 2))
        centers = detect_centers(f, LATS, LONS)
        self.assertLessEqual(len(centers), 1, f"open trough not rejected: {len(centers)}")

    def test_dateline_low_located(self):
        f = np.full_like(_LAT2D, 1013.0)
        _gauss_low(f, 10.0, 179.8, 40.0, 3.0)
        centers = detect_centers(f, LATS, LONS)
        self.assertEqual(len(centers), 1)
        c = centers[0]
        self.assertAlmostEqual(c["lat"], 10.0, delta=0.5)
        dlon = abs(((c["lon"] - 179.8 + 180) % 360) - 180)
        self.assertLess(dlon, 0.5, f"dateline mislocated: {c}")

    def test_atkinson_holliday(self):
        self.assertEqual(ah_vmax_kt(1010.0), 0.0)
        self.assertEqual(ah_vmax_kt(1011.0), 0.0)  # clamp above env
        self.assertAlmostEqual(ah_vmax_kt(950.0), 6.7 * (60 ** 0.644), places=3)

    def test_normalize_lon(self):
        self.assertAlmostEqual(_normalize_lon(190.0), -170.0)
        self.assertAlmostEqual(_normalize_lon(-190.0), 170.0)
        self.assertAlmostEqual(_normalize_lon(179.9), 179.9)


class TestRegistry(unittest.TestCase):
    def test_member_ids(self):
        spec = reg.get_spec("ecens")
        ids = spec.member_ids()
        self.assertEqual(len(ids), 51)
        self.assertEqual(ids[0], "CTL")
        self.assertEqual(ids[1], "P01")
        self.assertEqual(ids[-1], "P50")

    def test_steps_for_cycle_hour(self):
        spec = reg.get_spec("ecens")
        self.assertEqual(len(spec.steps_for_cycle_hour(0)), 85)
        self.assertEqual(len(spec.steps_for_cycle_hour(12)), 85)
        self.assertEqual(len(spec.steps_for_cycle_hour(6)), 49)
        self.assertEqual(len(spec.steps_for_cycle_hour(18)), 49)
        self.assertEqual(spec.steps_for_cycle_hour(0)[-1], 360)
        self.assertEqual(spec.steps_for_cycle_hour(6)[-1], 144)

    def test_aifs_ens_spec(self):
        # AIFS-ENS ("ecaie"): ECMWF ENS's AI twin - config only.
        self.assertEqual(reg.model_slugs(), ["ecens", "ecaie"])
        s = reg.get_spec("ecaie")
        self.assertEqual(s.od_model, "aifs-ens")
        self.assertEqual((s.ens_stream, s.pf_type), ("enfo", "pf"))
        self.assertEqual((s.control_stream, s.control_type), ("enfo", "cf"))  # NOT oper/fc
        self.assertEqual(len(s.member_ids()), 51)                              # 50 pert + control
        # 6-hourly to 360 h for EVERY cycle hour (no long/short split)
        for h in (0, 6, 12, 18):
            self.assertEqual(len(s.steps_for_cycle_hour(h)), 61)
            self.assertEqual(s.pf_terminal_step(h), 360)
            self.assertEqual(s.control_terminal_step(h), 360)
        # AIFS has no gh: z (geopotential) at 300/500, /g -> thickness in gpm
        self.assertEqual(s.gh_param, "z")
        self.assertEqual(s.gh_param_id, 129)
        self.assertAlmostEqual(s.gh_to_gpm, 1.0 / 9.80665, places=6)
        self.assertTrue(s.warm_core)

    def test_pressure_bins(self):
        bins = reg.pressure_bins_json()
        self.assertEqual(len(bins), 5)
        self.assertEqual(bins[0]["key"], "gt1000")
        self.assertEqual(bins[-1]["key"], "lt950")
        # labels carry no em-dash (house on-screen-text rule)
        for b in bins:
            self.assertNotIn("—", b["label"])

    def test_member_label(self):
        self.assertEqual(reg.member_label("CTL"), "Control")
        self.assertEqual(reg.member_label("P07"), "Perturbed 07")

    def test_center_fields_order(self):
        self.assertEqual(CENTER_FIELDS, ["step_h", "lat", "lon", "mslp_hpa", "vmax_kt"])


class TestManifestMerge(unittest.TestCase):
    def setUp(self):
        self.spec = reg.get_spec("ecens")

    def test_fresh_manifest(self):
        m, prune = merge_manifest(None, self.spec, "2026061300", retain=8)
        self.assertEqual(m["default_model"], "ecens")
        self.assertEqual(len(m["models"]), 1)
        self.assertEqual(m["models"][0]["cycles"], ["2026061300"])
        self.assertEqual(m["models"][0]["latest"], "2026061300")
        self.assertEqual(prune, [])

    def test_upsert_and_sort(self):
        prior = {"models": [{"slug": "ecens", "label": "ECMWF ENS",
                             "cycles": ["2026061218", "2026061212"]}]}
        m, prune = merge_manifest(prior, self.spec, "2026061300", retain=8)
        self.assertEqual(m["models"][0]["cycles"], ["2026061300", "2026061218", "2026061212"])
        self.assertEqual(m["models"][0]["latest"], "2026061300")
        self.assertEqual(prune, [])

    def test_rolling_window_prune(self):
        prior = {"models": [{"slug": "ecens", "label": "ECMWF ENS",
                             "cycles": ["2026061218", "2026061212", "2026061206"]}]}
        m, prune = merge_manifest(prior, self.spec, "2026061300", retain=2)
        self.assertEqual(m["models"][0]["cycles"], ["2026061300", "2026061218"])
        # pruned keys are model-prefixed R2 keys
        self.assertEqual(set(prune), {"ecens/2026061212.json", "ecens/2026061206.json"})

    def test_dedup_same_cycle(self):
        prior = {"models": [{"slug": "ecens", "label": "ECMWF ENS",
                             "cycles": ["2026061300"]}]}
        m, prune = merge_manifest(prior, self.spec, "2026061300", retain=8)
        self.assertEqual(m["models"][0]["cycles"], ["2026061300"])
        self.assertEqual(prune, [])

    def test_malformed_prior_is_tolerated(self):
        # a corrupt/half-written CDN manifest must not crash the builder
        for bad in [
            {"models": "not-a-list"},
            {"models": [{"label": "no slug"}, {"slug": "ecens", "cycles": "oops"}]},
            {"models": [None, 42, {"slug": "ecens"}]},
            {},
            None,
        ]:
            m, prune = merge_manifest(bad, self.spec, "2026061300", retain=8)
            self.assertEqual(m["default_model"], "ecens")
            self.assertEqual(m["models"][0]["slug"], "ecens")
            self.assertEqual(m["models"][0]["cycles"], ["2026061300"])
            self.assertEqual(prune, [])


if __name__ == "__main__":
    unittest.main()
