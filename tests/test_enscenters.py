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

    def test_model_slugs_in_registry_order(self):
        self.assertEqual(reg.model_slugs(), ["ecens", "ecaie", "gefs"])

    def test_aifs_ens_spec(self):
        # AIFS-ENS ("ecaie"): ECMWF ENS's AI twin - config only.
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

    def test_gefs_spec(self):
        # GEFS: genesis-track path - NOT field self-detect, NOT warm-core filtered
        # by us (NOAA's tracker already TC-filters), 31 members, own caption.
        s = reg.get_spec("gefs")
        self.assertEqual(s.source_kind, "genesis_tracks")
        self.assertFalse(s.warm_core)
        self.assertEqual(len(s.member_ids()), 31)       # control + 30 perturbed
        self.assertEqual(s.member_ids()[0], "CTL")
        self.assertEqual(s.member_ids()[-1], "P30")
        self.assertIsNotNone(s.caption)                  # model-aware viewer caption
        self.assertIn("Atkinson", s.caption)             # says vmax is NOT an AH estimate

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


class TestGefsTracks(unittest.TestCase):
    """GEFS genesis-track ATCF parsing (enscenters.tracks). No network."""

    # A crafted atcf_gen snippet: control (AC00) + two perturbed (AP01, AP05),
    # one ensemble-mean row (AEMN -> must be skipped), a tenths lat/lon, an
    # EAST-of-dateline lon (1750E), a bad pressure (mslp 0 -> skip), and a
    # duplicate id-row (same member/step/pos -> de-duped).
    SAMPLE = "\n".join([
        # basin, cycid, init,        ??, tech, tau, lat,   lon,    vmax, mslp
        "AL, 90, 2026061400, 03, AC00, 000, 150N, 0600W, 0035, 1004",
        "AL, 90, 2026061400, 03, AC00, 006, 158N, 0612W, 0042, 0998",
        "AL, 90, 2026061400, 03, AC00, 006, 158N, 0612W, 0042, 0998",  # dup -> dropped
        "WP, 91, 2026061400, 03, AP01, 000, 120N, 1750E, 0028, 1006",  # east lon
        "WP, 91, 2026061400, 03, AP01, 012, 130N, 1755E, 0050, 0990",
        "AL, 92, 2026061400, 03, AP05, 000, 200N, 0700W, 0000, 0000",  # mslp 0 -> skip
        "AL, 92, 2026061400, 03, AP05, 006, 205N, 0705W, 0060, 0975",
        "AL, 99, 2026061400, 03, AEMN, 000, 180N, 0650W, 0040, 1000",  # mean -> skip
    ])

    def test_member_id_mapping(self):
        from enscenters.tracks import _member_id
        self.assertEqual(_member_id("AC00"), "CTL")
        self.assertEqual(_member_id("AP01"), "P01")
        self.assertEqual(_member_id("AP30"), "P30")
        self.assertIsNone(_member_id("AEMN"))   # ensemble mean - skipped
        self.assertIsNone(_member_id("AVNO"))   # other model - skipped

    def test_parse_latlon(self):
        from enscenters.tracks import _parse_latlon
        self.assertAlmostEqual(_parse_latlon("150N"), 15.0)
        self.assertAlmostEqual(_parse_latlon("0600W"), -60.0)
        self.assertAlmostEqual(_parse_latlon("1750E"), 175.0)
        self.assertAlmostEqual(_parse_latlon("0250S"), -25.0)

    def test_parse_atcf_genesis(self):
        from enscenters.tracks import parse_atcf_genesis
        m = parse_atcf_genesis(self.SAMPLE)
        self.assertEqual(set(m), {"CTL", "P01", "P05"})       # AEMN skipped
        # control: 2 unique centers (dup dropped); schema row [step,lat,lon,mslp,vmax]
        self.assertEqual(len(m["CTL"]), 2)
        self.assertEqual(m["CTL"][0], [0, 15.0, -60.0, 1004.0, 35.0])
        self.assertEqual(m["CTL"][1][0], 6)
        # P01 east-of-dateline lon preserved positive
        self.assertAlmostEqual(m["P01"][0][2], 175.0)
        # P05: the mslp=0 row is dropped, only the good one remains
        self.assertEqual(len(m["P05"]), 1)
        self.assertEqual(m["P05"][0][3], 975.0)
        # vmax is the model's own ATCF wind, carried straight through
        self.assertEqual(m["P05"][0][4], 60.0)

    def test_build_gefs_cycle_writes_schema(self):
        import datetime as dt
        import json
        import tempfile
        from unittest import mock
        from enscenters import tracks
        spec = reg.get_spec("gefs")
        cyc = dt.datetime(2026, 6, 14, 0)
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(tracks, "find_genesis_url", return_value="http://x/f"), \
                 mock.patch.object(tracks, "fetch_genesis_text", return_value=self.SAMPLE):
                res = tracks.build_gefs_cycle(spec, cyc, d)
            data = json.load(open(os.path.join(d, "gefs", "2026061400.json")))
        self.assertEqual(data["model"], "gefs")
        self.assertEqual(data["source"], "genesis_tracks")
        self.assertEqual(data["center_fields"],
                         ["step_h", "lat", "lon", "mslp_hpa", "vmax_kt"])
        self.assertEqual(data["n_members"], 3)
        self.assertIsNotNone(data["caption"])
        ids = [mm["id"] for mm in data["members"]]
        self.assertEqual(ids, ["CTL", "P01", "P05"])          # canonical order
        self.assertEqual(res["cycle"], "2026061400")


if __name__ == "__main__":
    unittest.main()
