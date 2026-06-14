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
        # GEFS: now field SELF-DETECT (the same methodology as ECMWF/AIFS), pulled
        # from the 0.5 deg S3 fields, warm-core filtered, full 384 h, 31 members.
        s = reg.get_spec("gefs")
        self.assertEqual(s.source_kind, "self_detect")
        self.assertEqual(s.source, "noaa-gefs-aws")
        self.assertTrue(s.warm_core)                     # we run the thickness filter
        self.assertEqual(s.gh_param, "gh")               # GEFS HGT is geopotential height
        self.assertAlmostEqual(s.gh_to_gpm, 1.0)         # gpm already (no z/g conversion)
        self.assertEqual(s.gh_levels, (300, 500))
        self.assertEqual(s.grid_label, "0.5 deg")
        self.assertEqual(len(s.member_ids()), 31)        # gec00 control + gep01..gep30
        self.assertEqual(s.member_ids()[0], "CTL")
        self.assertEqual(s.member_ids()[-1], "P30")
        # full 384 h horizon for every cycle hour
        for h in (0, 6, 12, 18):
            self.assertEqual(s.steps_for_cycle_hour(h)[-1], 384)
        self.assertIsNotNone(s.caption)                  # model-aware viewer caption
        self.assertIn("Atkinson", s.caption)             # vmax IS an AH estimate now

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


class TestManifestGuard(unittest.TestCase):
    """The workflow reconcile guard (scripts/enscenters_manifest_guard.py) at the
    main()/file-IO level: a fresh/clobbered/stale new manifest must NEVER drop a
    model that's live on R2 and NEVER regress a model's latest. (The pure
    reconcile() logic is covered exhaustively in test_enscenters_manifest_guard.)"""

    def _guard(self):
        import importlib.util
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "scripts", "enscenters_manifest_guard.py")
        spec = importlib.util.spec_from_file_location("ens_guard", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _run(self, new, live):
        import json
        import tempfile
        g = self._guard()
        with tempfile.TemporaryDirectory() as d:
            np_, lp = os.path.join(d, "new.json"), os.path.join(d, "live.json")
            json.dump(new, open(np_, "w"))
            json.dump(live, open(lp, "w"))
            g.main(["x", np_, lp])
            return json.load(open(np_))

    def test_preserves_clobbered_sibling(self):
        # this run fresh-started (gefs only); live R2 still has ecais -> union back
        new = {"default_model": "gefs", "models": [
            {"slug": "gefs", "label": "GEFS", "cycles": ["2026061218"], "latest": "2026061218"}]}
        live = {"default_model": "ecaie", "models": [
            {"slug": "ecaie", "label": "AIFS-ENS", "cycles": ["2026061318"], "latest": "2026061318",
             "cycle_versions": {"2026061318": "av"}}]}
        out = self._run(new, live)
        slugs = [m["slug"] for m in out["models"]]
        self.assertIn("ecaie", slugs)                  # sibling preserved
        self.assertIn("gefs", slugs)                   # own entry kept
        self.assertEqual(slugs, ["ecaie", "gefs"])     # canonical order
        by = {m["slug"]: m for m in out["models"]}
        self.assertEqual(by["ecaie"]["cycle_versions"], {"2026061318": "av"})  # verbatim

    def test_own_entry_unions_never_drops_live_cycle(self):
        # new ADVANCES gefs but its build lists only the new cycle; the live gefs
        # cycle (within retention) is UNIONED back, never dropped -> monotone
        # history, and the latest moves forward, never backward.
        new = {"default_model": "gefs", "models": [
            {"slug": "gefs", "cycles": ["2026061218"], "latest": "2026061218"}]}
        live = {"models": [
            {"slug": "gefs", "cycles": ["2026061200"], "latest": "2026061200"},
            {"slug": "ecens", "cycles": ["2026061312"], "latest": "2026061312"}]}
        out = self._run(new, live)
        by = {m["slug"]: m for m in out["models"]}
        self.assertEqual(by["gefs"]["cycles"], ["2026061218", "2026061200"])  # unioned, newest first
        self.assertEqual(by["gefs"]["latest"], "2026061218")     # advanced
        self.assertIn("ecens", by)                                # other sibling preserved
        self.assertEqual(out["default_model"], "ecens")           # normalized to canonical default

    def test_own_latest_never_regresses(self):
        # the prod bug: new's gefs build REGRESSED (older cycles than live). The
        # guard must keep gefs at its live latest, not move it backward.
        new = {"default_model": "gefs", "models": [
            {"slug": "gefs", "cycles": ["2026061118", "2026061112"], "latest": "2026061118"}]}
        live = {"models": [
            {"slug": "gefs", "cycles": ["2026061218", "2026061212"], "latest": "2026061218"}]}
        out = self._run(new, live)
        by = {m["slug"]: m for m in out["models"]}
        self.assertEqual(by["gefs"]["latest"], "2026061218")     # NOT regressed

    def test_empty_live_is_noop(self):
        new = {"default_model": "gefs", "models": [
            {"slug": "gefs", "cycles": ["2026061218"], "latest": "2026061218"}]}
        out = self._run(new, {})
        self.assertEqual([m["slug"] for m in out["models"]], ["gefs"])


class TestGefsTracks(unittest.TestCase):
    """GEFS genesis-track parsing (enscenters.tracks). No network.

    The genesis "altg" ATCF (verified live) has an EXTRA storm-id column vs plain
    atcfunix, so the data columns are shifted: [6]=tau, [7]=lat, [8]=lon,
    [9]=vmax(kt), [10]=mslp(mb). Each file is ONE member; the member comes from
    the FILENAME (ac00 / apNN), not from a row column.
    """

    # One member's file. Real-format rows (tag, cand#, storm-id, init, technum,
    # tech, tau, lat, lon, vmax, mslp, type, radii...): two unique centers, a
    # duplicate (same step/pos -> dropped), an off-grid step (013 -> dropped), and
    # a non-positive pressure (-> dropped). East-of-dateline lon kept positive.
    CTL_FILE = "\n".join([
        "TG, 0022, 2026061312_F012_268N_1199E_FOF, 2026061312, 03, AC00, 012, 268N, 1199E,  26, 1003, XX, 34, NEQ",
        "TG, 0022, 2026061312_F018_277N_1223E_FOF, 2026061312, 03, AC00, 018, 277N, 1223E,  37, 1001, XX, 34, NEQ",
        "TG, 0022, 2026061312_F012_268N_1199E_FOF, 2026061312, 03, AC00, 012, 268N, 1199E,  26, 1003, XX, 34, NEQ",  # dup
        "TG, 0022, 2026061312_F013_280N_1240E_FOF, 2026061312, 03, AC00, 013, 280N, 1240E,  30, 1000, XX, 34, NEQ",  # off-grid step
        "TG, 0022, 2026061312_F024_300N_1260E_FOF, 2026061312, 03, AC00, 024, 300N, 1260E,  20,    0, XX, 34, NEQ",  # mslp 0
    ])
    # A perturbed member with one center at an east-of-dateline longitude.
    P03_FILE = "TG, 0027, 2026061312_F012_262N_1750E_FOF, 2026061312, 03, AP03, 012, 262N, 1750E,  29, 1004, XX, 34, NEQ"

    def test_member_id_mapping(self):
        from enscenters.tracks import _member_id
        self.assertEqual(_member_id("ac00"), "CTL")
        self.assertEqual(_member_id("ap01"), "P01")
        self.assertEqual(_member_id("ap30"), "P30")

    def test_member_file_roster(self):
        from enscenters.tracks import GEFS_MEMBER_FILES
        self.assertEqual(len(GEFS_MEMBER_FILES), 31)           # control + 30 perturbed
        self.assertEqual(GEFS_MEMBER_FILES[0], "ac00")
        self.assertEqual(GEFS_MEMBER_FILES[-1], "ap30")

    def test_parse_latlon(self):
        from enscenters.tracks import _parse_latlon
        self.assertAlmostEqual(_parse_latlon("268N"), 26.8)
        self.assertAlmostEqual(_parse_latlon("0600W"), -60.0)
        self.assertAlmostEqual(_parse_latlon("1199E"), 119.9)
        self.assertAlmostEqual(_parse_latlon("0250S"), -25.0)

    def test_parse_member_genesis_columns(self):
        from enscenters.tracks import parse_member_genesis
        c = parse_member_genesis(self.CTL_FILE)
        # 2 unique centers survive (dup + off-grid + mslp<=0 dropped)
        self.assertEqual(len(c), 2)
        # schema row [step_h, lat, lon, mslp_hpa, vmax_kt] from cols [6..10]
        self.assertEqual(c[0], [12, 26.8, 119.9, 1003.0, 26.0])
        self.assertEqual(c[1], [18, 27.7, 122.3, 1001.0, 37.0])
        # east-of-dateline lon stays positive; vmax is the model's own ATCF wind
        p = parse_member_genesis(self.P03_FILE)
        self.assertEqual(p[0], [12, 26.2, 175.0, 1004.0, 29.0])

    def _fake_get(self, base="http://x/genesis/"):
        """A _get stub: real content for ac00 + ap03, an empty-but-present file for
        every other member (so the quorum is met and they skip with 0 centers)."""
        def get(url, timeout=None):
            if "storms.ac00." in url:
                return self.CTL_FILE.encode()
            if "storms.ap03." in url:
                return self.P03_FILE.encode()
            return b""   # present but no candidates
        return get

    def test_build_gefs_cycle_writes_schema(self):
        import datetime as dt
        import json
        import tempfile
        from unittest import mock
        from enscenters import tracks
        spec = reg.get_spec("gefs")
        cyc = dt.datetime(2026, 6, 13, 12)
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(tracks, "genesis_dir", return_value="http://x/genesis/"), \
                 mock.patch.object(tracks, "_get", self._fake_get()):
                res = tracks.build_gefs_cycle(spec, cyc, d)
            data = json.load(open(os.path.join(d, "gefs", "2026061312.json")))
        self.assertEqual(data["model"], "gefs")
        self.assertEqual(data["source"], "genesis_tracks")
        self.assertEqual(data["center_fields"],
                         ["step_h", "lat", "lon", "mslp_hpa", "vmax_kt"])
        self.assertEqual(data["n_members"], 2)                 # only ac00 + ap03 had tracks
        self.assertIsNotNone(data["caption"])
        ids = [mm["id"] for mm in data["members"]]
        self.assertEqual(ids, ["CTL", "P03"])                  # canonical (file) order
        self.assertEqual(res["cycle"], "2026061312")
        # run_steps is TRIMMED to the data's real horizon (deepest center step=18),
        # not the full 384 h parse grid -> no dead trailing scrubber frames.
        self.assertEqual(data["run_steps"], [0, 6, 12, 18])

    def test_build_gefs_cycle_quiet_publishes_empty(self):
        # All 31 files present but no candidates -> publish an empty-but-valid
        # cycle (GEFS still appears in the selector), NOT a raise.
        import datetime as dt
        import json
        import tempfile
        from unittest import mock
        from enscenters import tracks
        spec = reg.get_spec("gefs")
        cyc = dt.datetime(2026, 6, 13, 12)
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(tracks, "genesis_dir", return_value="http://x/genesis/"), \
                 mock.patch.object(tracks, "_get", lambda url, timeout=None: b""):
                tracks.build_gefs_cycle(spec, cyc, d)
            data = json.load(open(os.path.join(d, "gefs", "2026061312.json")))
        self.assertEqual(data["n_members"], 0)
        self.assertEqual(data["n_centers"], 0)
        self.assertEqual(data["run_steps"], [0])               # quiet cycle keeps one frame

    def test_build_gefs_cycle_quorum_raises_on_partial(self):
        # Only a couple member files fetch (dir mid-dissemination) -> raise so the
        # currency core skips + retries instead of publishing a partial cycle.
        import datetime as dt
        import tempfile
        from unittest import mock
        from enscenters import tracks
        spec = reg.get_spec("gefs")
        cyc = dt.datetime(2026, 6, 13, 12)

        def sparse_get(url, timeout=None):
            return self.CTL_FILE.encode() if "storms.ac00." in url else None
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(tracks, "genesis_dir", return_value="http://x/genesis/"), \
                 mock.patch.object(tracks, "_get", sparse_get):
                with self.assertRaises(RuntimeError):
                    tracks.build_gefs_cycle(spec, cyc, d)


class TestGefsIngest(unittest.TestCase):
    """GEFS S3 .idx byte-range ingest (enscenters.gefs_ingest). Pure functions,
    no network: member mapping, URL construction, and .idx -> byte-range parsing."""

    def test_member_s3_mapping(self):
        from enscenters import gefs_ingest as gi
        self.assertEqual(gi.member_s3("CTL"), "gec00")
        self.assertEqual(gi.member_s3("P01"), "gep01")
        self.assertEqual(gi.member_s3("P30"), "gep30")

    def test_url_construction(self):
        import datetime as dt
        from enscenters import gefs_ingest as gi
        cyc = dt.datetime(2026, 6, 14, 12)
        url = gi.grib_url(cyc, "gec00", 384)
        self.assertTrue(url.endswith(
            "gefs.20260614/12/atmos/pgrb2ap5/gec00.t12z.pgrb2a.0p50.f384"))
        self.assertEqual(gi.idx_url(cyc, "gep01", 0),
                         gi.grib_url(cyc, "gep01", 0) + ".idx")

    def test_idx_parse_and_byte_ranges(self):
        from enscenters import gefs_ingest as gi
        idx = "\n".join([
            "1:0:d=2026061412:HGT:10 mb:anl:ENS=low-res ctl",
            "26:4485211:d=2026061412:HGT:300 mb:anl:ENS=low-res ctl",
            "27:4600000:d=2026061412:RH:300 mb:anl:ENS=low-res ctl",
            "31:5379112:d=2026061412:HGT:500 mb:anl:ENS=low-res ctl",
            "32:5500000:d=2026061412:RH:500 mb:anl:ENS=low-res ctl",
            "71:13324582:d=2026061412:PRMSL:mean sea level:anl:ENS=low-res ctl",
            "72:13420000:d=2026061412:APCP:surface:anl:ENS=low-res ctl",
        ])
        rows = gi.parse_idx(idx)
        self.assertEqual(len(rows), 7)
        ranges = gi.idx_byte_ranges(rows)
        # all three wanted records resolved, each [start, next_start)
        self.assertEqual(ranges[("PRMSL", "mean sea level")], (13324582, 13420000))
        self.assertEqual(ranges[("HGT", "300 mb")], (4485211, 4600000))
        self.assertEqual(ranges[("HGT", "500 mb")], (5379112, 5500000))

    def test_idx_last_record_open_ended(self):
        from enscenters import gefs_ingest as gi
        # PRMSL is the final record -> open-ended range (end None -> "bytes=start-")
        idx = "\n".join([
            "31:5379112:d=2026061412:HGT:500 mb:anl:x",
            "26:4485211:d=2026061412:HGT:300 mb:anl:x",
            "71:13324582:d=2026061412:PRMSL:mean sea level:anl:x",
        ])
        ranges = gi.idx_byte_ranges(gi.parse_idx(idx))
        self.assertEqual(ranges[("PRMSL", "mean sea level")], (13324582, None))

    def test_byte_ranges_missing_field(self):
        from enscenters import gefs_ingest as gi
        idx = "1:0:d=2026061412:HGT:500 mb:anl:x"   # no PRMSL, no HGT 300
        ranges = gi.idx_byte_ranges(gi.parse_idx(idx))
        self.assertNotIn(("PRMSL", "mean sea level"), ranges)
        self.assertLess(len(ranges), 3)             # _download_subset would skip the step


if __name__ == "__main__":
    unittest.main()
