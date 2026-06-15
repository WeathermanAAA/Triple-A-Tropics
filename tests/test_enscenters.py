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
        self.assertEqual(reg.model_slugs(), ["ecens", "ecaie", "gefs", "fnv3"])

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


def _stub_member_worker(spec, cycle, member_id, steps, tmpdir, source):
    """Module-level (picklable) stand-in for pipeline.process_member used by the
    watchdog test: P99 hangs (simulating a stalled, no-timeout download); every
    other member returns one synthetic center immediately."""
    import time
    if member_id == "P99":
        time.sleep(120)            # longer than the test's member_deadline_s
    peak = {"mslp_hpa": 1000.0, "vmax_kt": 30.0, "lat": 12.0, "lon": 130.0, "step_h": 0}
    return member_id, peak, [[0, 12.0, 130.0, 1000.0, 30.0]]


class TestMemberWatchdog(unittest.TestCase):
    """A stalled member must NOT wedge the cycle to the wall-clock limit: the
    parallel gather has a hard deadline that abandons the un-finished member(s)
    and force-kills the workers, then the quorum + never-miss path takes over.
    Regression guard for the 2026-06-14 ECMWF/AIFS hang (ecmwf-opendata /
    multiurl download has no hard timeout)."""

    def test_stalled_member_is_abandoned_not_hung(self):
        import time, tempfile, datetime as dt
        from enscenters import pipeline as pl
        spec = reg.get_spec("gefs")   # no prepare() hook -> watchdog test stays offline
        # 8 members, one (P99) hangs; quorum 0.75 -> need 6, so 7 good publishes.
        members = ["P0%d" % i for i in range(1, 8)] + ["P99"]
        orig = pl.process_member
        pl.process_member = _stub_member_worker
        t0 = time.time()
        try:
            with tempfile.TemporaryDirectory() as d:
                res = pl.build_one_cycle(
                    spec, dt.datetime(2026, 6, 14, 12), d,
                    members=members, steps=[0], jobs=4,
                    member_deadline_s=4, progress=lambda *a, **k: None)
        finally:
            pl.process_member = orig
        elapsed = time.time() - t0
        # Returned promptly after the deadline (not after the 120 s stall).
        self.assertLess(elapsed, 60, "watchdog did not bound the stalled member")
        self.assertEqual(res["members"], 7)          # the 7 good members published
        self.assertIn("P99", res["failures"])        # the stalled one was abandoned


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


class TestDetectRefinement(unittest.TestCase):
    """FIX 2: sub-grid parabolic refinement de-grids the reported position without
    changing Pmin / counts. (`detect`, `_parabolic_offset`.)"""

    @staticmethod
    def _low(lats, lons, tlat, tlon, depth=22.0, sig=1.6, base=1013.0):
        from enscenters.detect import _normalize_lon
        LA, LO = np.meshgrid(lats, lons, indexing="ij")
        dlon = ((LO - tlon + 180) % 360) - 180
        return base - depth * np.exp(-((LA - tlat) ** 2 + dlon ** 2) / (2 * sig ** 2))

    def test_parabolic_offset_math(self):
        from enscenters.detect import _parabolic_offset
        self.assertEqual(_parabolic_offset(5, 5, 5), 0.0)          # flat -> node
        self.assertEqual(_parabolic_offset(1, 5, 1), 0.0)          # concave (max) -> 0
        # minimum skewed toward the ym1 side -> negative offset
        self.assertLess(_parabolic_offset(10.0, 1.0, 12.0), 0.0)
        self.assertGreater(_parabolic_offset(12.0, 1.0, 10.0), 0.0)
        # symmetric convex -> vertex at the node
        self.assertAlmostEqual(_parabolic_offset(11.0, 10.0, 11.0), 0.0)
        # clamp
        self.assertLessEqual(abs(_parabolic_offset(2.0, 1.0, 1.0000001)), 0.5)

    def test_off_grid_low_is_refined_off_node(self):
        from enscenters.detect import detect_centers
        lats = 25.0 - np.arange(81) * 0.5     # descending, 25..-15
        lons = 120.0 + np.arange(120) * 0.5
        tlat, tlon = 10.3, 150.7              # deliberately between nodes
        field = self._low(lats, lons, tlat, tlon)
        cs = detect_centers(field, lats, lons)
        self.assertTrue(cs)
        c = min(cs, key=lambda d: abs(d["lat"] - tlat) + abs(d["lon"] - tlon))
        self.assertLess(abs(c["lat"] - tlat), 0.1)        # within <=0.1 deg of true min
        self.assertLess(abs(c["lon"] - tlon), 0.1)
        # NOT snapped to a node (nearest node is 10.5 / 150.5)
        self.assertGreater(abs(c["lat"] - 10.5), 0.05)
        self.assertGreater(abs(c["lon"] - 150.5), 0.05)

    def test_grid_aligned_low_stays_on_node(self):
        from enscenters.detect import detect_centers
        lats = 25.0 - np.arange(81) * 0.5
        lons = 120.0 + np.arange(120) * 0.5
        field = self._low(lats, lons, 10.0, 150.0)        # exactly on nodes
        cs = detect_centers(field, lats, lons)
        c = min(cs, key=lambda d: abs(d["lat"] - 10.0) + abs(d["lon"] - 150.0))
        self.assertLess(abs(c["lat"] - 10.0), 0.05)
        self.assertLess(abs(c["lon"] - 150.0), 0.05)

    def test_refinement_preserves_pmin_and_count(self):
        from enscenters.detect import detect_centers
        lats = 25.0 - np.arange(81) * 0.5
        lons = 120.0 + np.arange(120) * 0.5
        # two well-separated off-grid lows -> exactly two centers, each Pmin = the
        # grid-cell minimum (refinement is position-only).
        f = np.minimum(self._low(lats, lons, 8.3, 140.7),
                       self._low(lats, lons, -5.7, 165.2))
        cs = detect_centers(f, lats, lons)
        self.assertEqual(len(cs), 2)
        gridmin = round(float(np.min(f)), 1)
        self.assertEqual(min(c["mslp_hpa"] for c in cs), gridmin)   # Pmin = grid min
        for c in cs:
            self.assertFalse(np.isnan(c["lat"]) or np.isnan(c["lon"]))

    def test_flat_field_no_centers_no_nan(self):
        from enscenters.detect import detect_centers
        lats = 25.0 - np.arange(40) * 0.5
        lons = 120.0 + np.arange(60) * 0.5
        self.assertEqual(detect_centers(np.full((40, 60), 1010.0), lats, lons), [])


class TestEcmwfGcsIndex(unittest.TestCase):
    """FIX 1: the JSON-Lines .index filter for the Google byte-range backend."""

    SAMPLE = "\n".join([
        '{"type":"pf","stream":"enfo","step":"0","levtype":"sfc","number":"1","param":"msl","_offset":"100","_length":"50"}',
        '{"type":"pf","stream":"enfo","step":"0","levtype":"pl","levelist":"300","number":"1","param":"gh","_offset":"200","_length":"60"}',
        '{"type":"pf","stream":"enfo","step":"0","levtype":"pl","levelist":"500","number":"1","param":"gh","_offset":"300","_length":"60"}',
        '{"type":"pf","stream":"enfo","step":"0","levtype":"pl","levelist":"850","number":"1","param":"gh","_offset":"400","_length":"60"}',
        '{"type":"pf","stream":"enfo","step":"0","levtype":"sfc","number":"2","param":"msl","_offset":"500","_length":"50"}',
        '{"type":"pf","stream":"enfo","step":"0","levtype":"pl","levelist":"300","number":"2","param":"t","_offset":"560","_length":"60"}',
        '{"type":"fc","stream":"oper","step":"0","levtype":"sfc","param":"msl","_offset":"900","_length":"50"}',
        '{"type":"fc","stream":"oper","step":"0","levtype":"pl","levelist":"300","param":"gh","_offset":"960","_length":"60"}',
        '{"type":"fc","stream":"oper","step":"0","levtype":"pl","levelist":"500","param":"gh","_offset":"1020","_length":"60"}',
    ])

    def test_filter_index_ifs(self):
        from enscenters import ecmwf_byterange_ingest as gi
        spec = reg.get_spec("ecens")    # gh_param=gh, levels (300,500), pf/fc
        out = gi.filter_index(self.SAMPLE, spec)
        self.assertEqual(set(out["1"]), {"msl", "gh300", "gh500"})
        self.assertEqual(out["1"]["msl"], (100, 50))
        self.assertEqual(out["1"]["gh300"], (200, 60))
        self.assertEqual(out["1"]["gh500"], (300, 60))
        self.assertEqual(set(out["2"]), {"msl"})        # no gh for member 2 in sample
        self.assertEqual(set(out["ctl"]), {"msl", "gh300", "gh500"})   # control (type fc, no number)

    def test_filter_index_aifs_uses_z(self):
        from enscenters import ecmwf_byterange_ingest as gi
        spec = reg.get_spec("ecaie")    # gh_param=z, control_type=cf
        sample = self.SAMPLE.replace('"param":"gh"', '"param":"z"').replace('"type":"fc"', '"type":"cf"')
        out = gi.filter_index(sample, spec)
        self.assertEqual(set(out["1"]), {"msl", "gh300", "gh500"})   # z mapped to gh<level> keys
        self.assertIn("ctl", out)

    def test_path_is_mirror_independent(self):
        from enscenters import ecmwf_byterange_ingest as gi
        # the same object PATH is recovered from either mirror's URL, and each
        # mirror's base + path reconstructs that mirror's URL.
        p = gi._path_of("https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/20260614/12z/ifs/0p25/enfo/x-ef.grib2")
        self.assertEqual(p, "20260614/12z/ifs/0p25/enfo/x-ef.grib2")
        names = [m[0] for m in gi.MIRRORS]
        self.assertEqual(names, ["gcs", "aws"])     # GCS preferred
        bases = dict(gi.MIRRORS)
        self.assertEqual(bases["gcs"] + "/" + p,
                         "https://storage.googleapis.com/ecmwf-open-data/20260614/12z/ifs/0p25/enfo/x-ef.grib2")


class _FakeResp:
    def __init__(self, status, content=b""):
        self.status_code = status
        self.content = content


class _FakeSession:
    """Routes GET/HEAD by which mirror host the URL targets, via a handler
    ``(mirror_name, is_head) -> _FakeResp`` and records the mirrors hit."""

    def __init__(self, handler):
        self.handler = handler
        self.get_mirrors: list = []
        self.head_mirrors: list = []

    @staticmethod
    def _mirror(url):
        return "gcs" if "storage.googleapis.com" in url else "aws"

    def get(self, url, headers=None, timeout=None):
        m = self._mirror(url)
        self.get_mirrors.append(m)
        r = self.handler(m, False)
        if isinstance(r, Exception):
            raise r
        return r

    def head(self, url, timeout=None):
        m = self._mirror(url)
        self.head_mirrors.append(m)
        r = self.handler(m, True)
        if isinstance(r, Exception):
            raise r
        return r


class TestEcmwfMirrorFallback(unittest.TestCase):
    """FIX (multi-homing): mirror fallback + sticky circuit-breaker + mirror-aware
    completeness gate for the ECMWF/AIFS byte-range backend."""

    def setUp(self):
        from enscenters import ecmwf_byterange_ingest as gi
        self.gi = gi
        gi.reset_breaker()
        self._bk = gi._BACKOFF_S
        gi._BACKOFF_S = 0.0           # no sleeps in tests
        self.addCleanup(setattr, gi, "_BACKOFF_S", self._bk)
        self.addCleanup(gi.reset_breaker)

    def _client(self, handler):
        c = self.gi._Client(None, _FakeSession(handler))
        return c

    def test_fallback_to_aws_when_gcs_fails_identical_bytes(self):
        DATA = b"GRIB....7777"
        # GCS hard-fails (503); AWS serves the bytes.
        c = self._client(lambda m, head: _FakeResp(503) if m == "gcs" else _FakeResp(206, DATA))
        data, mirror = self.gi.fetch(c, "p/x.grib2", headers={"Range": "bytes=0-11"})
        self.assertEqual(data, DATA)
        self.assertEqual(mirror, "aws")
        # identical to the GCS-served bytes when GCS is up
        c2 = self._client(lambda m, head: _FakeResp(206, DATA))
        d2, m2 = self.gi.fetch(c2, "p/x.grib2", headers={"Range": "bytes=0-11"})
        self.assertEqual(d2, data)
        self.assertEqual(m2, "gcs")           # prefers GCS when healthy

    def test_sticky_demotion_after_k_failures(self):
        gi = self.gi
        c = self._client(lambda m, head: _FakeResp(503) if m == "gcs" else _FakeResp(206, b"ok"))
        for _ in range(gi._DEMOTE_AFTER):
            gi.fetch(c, "p/x.grib2")
        self.assertIn("gcs", gi._DEMOTED)                 # demoted after K
        c.session.get_mirrors.clear()
        gi.fetch(c, "p/y.grib2")
        self.assertNotIn("gcs", c.session.get_mirrors)    # subsequent requests skip GCS
        self.assertIn("aws", c.session.get_mirrors)

    def test_404_falls_through_without_demoting(self):
        gi = self.gi
        # GCS 404 (cycle not yet published there) -> AWS serves; NOT a breaker hit.
        c = self._client(lambda m, head: _FakeResp(404) if m == "gcs" else _FakeResp(206, b"D"))
        data, mirror = gi.fetch(c, "p/x.grib2")
        self.assertEqual((data, mirror), (b"D", "aws"))
        self.assertEqual(gi._FAILS.get("gcs", 0), 0)      # 404 is "absent", not "fail"

    def test_both_mirrors_down_clean_error(self):
        c = self._client(lambda m, head: _FakeResp(503))
        data, mirror = self.gi.fetch(c, "p/x.grib2")
        self.assertIsNone(data)
        self.assertIsNone(mirror)                          # clean error, no hang

    def test_both_mirrors_network_exception_clean_error(self):
        c = self._client(lambda m, head: ConnectionError("dead host"))
        data, mirror = self.gi.fetch(c, "p/x.grib2")
        self.assertEqual((data, mirror), (None, None))

    def test_gate_complete_on_aws_only(self):
        gi = self.gi
        spec = reg.get_spec("ecens")
        c = gi.make_client("aws", spec.od_model)
        c.session = _FakeSession(lambda m, head: _FakeResp(200) if m == "aws" else _FakeResp(404))
        import datetime as dt
        self.assertTrue(gi.cycle_complete(spec, dt.datetime(2026, 6, 14, 12), c))  # AWS-only -> ingestable

    def test_gate_prefers_gcs(self):
        gi = self.gi
        spec = reg.get_spec("ecens")
        c = gi.make_client("aws", spec.od_model)
        c.session = _FakeSession(lambda m, head: _FakeResp(200))   # both up
        import datetime as dt
        path = gi._index_path(gi.resolve_path(c, dt.datetime(2026, 6, 14, 12),
                                              spec.ens_stream, spec.pf_type, spec.pf_terminal_step(12)))
        self.assertEqual(gi.head_any(c, path), "gcs")              # prefers GCS

    def test_gate_incomplete_on_both(self):
        gi = self.gi
        spec = reg.get_spec("ecens")
        c = gi.make_client("aws", spec.od_model)
        c.session = _FakeSession(lambda m, head: _FakeResp(404))
        import datetime as dt
        self.assertFalse(gi.cycle_complete(spec, dt.datetime(2026, 6, 14, 12), c))

    def test_resolve_path_is_bucket_relative_regardless_of_source(self):
        # Path resolution must yield a BUCKET-RELATIVE key ("{date}/...") that both
        # mirror bases expect - even when the CLI --source is the portal ("ecmwf",
        # which would otherwise prepend "/forecasts") or "google" ("ecmwf-open-data/").
        import datetime as dt
        gi = self.gi
        for src in ("ecmwf", "google", "aws"):
            c = gi.make_client(src, "ifs")
            p = gi.resolve_path(c, dt.datetime(2026, 6, 14, 12), "enfo", "pf", 0)
            self.assertTrue(p.startswith("20260614/"), f"{src}: {p}")
            self.assertFalse(p.startswith(("forecasts/", "ecmwf-open-data/")), f"{src}: {p}")
            self.assertTrue(p.endswith(".grib2"))


class TestFnv3Ingest(unittest.TestCase):
    """FNV3 (Weather Lab) native TC-track CSV parsing -> shared schema. No network."""

    CSV = "\n".join([
        "# If this file contains data ... TERMS OF USE ...",
        "#   https://storage.googleapis.com/weathernext-public/terms-of-use.pdf",
        "init_time,track_id,sample,valid_time,lead_time,lead_time_hours,lat,lon,"
        "minimum_sea_level_pressure_hpa,maximum_sustained_wind_speed_knots,radius_of_maximum_winds_km",
        "2026-06-14 18:00:00,12,0.0,2026-06-26 06:00:00,11 days,276,9.7,137.53,1005.0,22.5,109.0",
        "2026-06-14 18:00:00,12,0.0,2026-06-26 12:00:00,11 days,282,9.9,136.02,1004.2,24.4,60.0",
        "2026-06-14 18:00:00,5,0.0,2026-06-20 00:00:00,5 days,126,15.0,140.0,975.0,88.0,30.0",   # member 0 deepest
        "2026-06-14 18:00:00,EP93,1.0,2026-06-14 18:00:00,0 days,0,8.1,-132.0,1007.0,25.0,185.0",
        "2026-06-14 18:00:00,EP93,1.0,2026-06-14 18:00:00,0 days,7,8.2,-132.5,1006.0,26.0,180.0",   # bad step (not %6) -> dropped
    ])

    def test_parse_cyclogenesis_to_schema(self):
        from enscenters import fnv3_ingest as fi
        members, total, run_steps = fi.parse_csv(self.CSV)
        self.assertEqual([m["id"] for m in members], ["M00", "M01"])      # grouped by sample
        m0 = members[0]
        self.assertEqual(m0["label"], "Member 0")
        self.assertEqual(m0["n_centers"], 3)                              # 276,282,126
        # centers sorted by step, CENTER_FIELDS order [step,lat,lon,mslp,vmax]
        self.assertEqual([c[0] for c in m0["centers"]], [126, 276, 282])
        self.assertEqual(m0["centers"][0], [126, 15.0, 140.0, 975.0, 88.0])
        # NATIVE vmax preserved (not AH): 88 kt at 975 hPa would be ~AH 50 kt
        self.assertEqual(m0["peak"]["vmax_kt"], 88.0)
        self.assertEqual(m0["peak"]["mslp_hpa"], 975.0)                   # deepest center
        m1 = members[1]
        self.assertEqual(m1["n_centers"], 1)                             # step 7 (not 6-hourly) dropped
        self.assertEqual(m1["centers"][0], [0, 8.1, -132.0, 1007.0, 25.0])
        self.assertEqual(total, 4)
        self.assertEqual(run_steps[0], 0)
        self.assertEqual(run_steps[-1], 282)                             # trimmed to max step
        self.assertEqual(run_steps, list(range(0, 283, 6)))              # 6-hourly grid
        self.assertEqual(fi.CENTER_FIELDS, CENTER_FIELDS)               # same positional schema

    def test_url_shape(self):
        import datetime as dt
        from enscenters import fnv3_ingest as fi
        u = fi.cycle_url(dt.datetime(2026, 6, 14, 18))
        self.assertEqual(u, "https://deepmind.google.com/science/weatherlab/download/cyclones/"
                            "FNV3/ensemble/cyclogenesis/csv/FNV3_2026_06_14T18_00_cyclogenesis.csv")

    def test_registry_entry(self):
        s = reg.get_spec("fnv3")
        self.assertEqual(s.source_kind, "track_csv")
        self.assertFalse(s.warm_core)                                    # native objects, no warmcore
        self.assertEqual(s.label, "FNV3 (50)")                          # member count unambiguous
        self.assertIn("not for real-world use", s.caption)              # experimental disclaimer
        self.assertIn("Weather Lab", s.caption)                        # attribution
        self.assertIn("fnv3", reg.model_slugs())


class TestFiveModelReconcile(unittest.TestCase):
    """Step 4: the shared derive-from-R2 reconcile must scale to all models -
    adding FNV3 must NOT change ecens/ecaie/gefs, must keep canonical order, and
    must survive a per-model write that only lists its own (newest) cycle."""

    def _guard(self):
        import importlib.util
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "scripts", "enscenters_manifest_guard.py")
        spec = importlib.util.spec_from_file_location("ens_guard5", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _run(self, new, live):
        import json, tempfile
        g = self._guard()
        with tempfile.TemporaryDirectory() as d:
            np_, lp = os.path.join(d, "n.json"), os.path.join(d, "l.json")
            json.dump(new, open(np_, "w")); json.dump(live, open(lp, "w"))
            g.main(["x", np_, lp])
            return json.load(open(np_))

    def test_fnv3_publish_preserves_all_siblings(self):
        # the FNV3 workflow publishes only its own fresh cycle; the live R2
        # manifest already has the four other entries -> all must survive, in
        # canonical registry order, with their cycle_versions verbatim.
        new = {"default_model": "fnv3", "models": [
            {"slug": "fnv3", "label": "FNV3 (50)", "cycles": ["2026061418"], "latest": "2026061418",
             "cycle_versions": {"2026061418": "fv"}}]}
        live = {"default_model": "ecens", "models": [
            {"slug": "ecens", "cycles": ["2026061412"], "latest": "2026061412", "cycle_versions": {"2026061412": "ev"}},
            {"slug": "ecaie", "cycles": ["2026061418"], "latest": "2026061418", "cycle_versions": {"2026061418": "av"}},
            {"slug": "gefs",  "cycles": ["2026061412"], "latest": "2026061412", "cycle_versions": {"2026061412": "gv"}},
            {"slug": "fnv3",  "cycles": ["2026061412"], "latest": "2026061412", "cycle_versions": {"2026061412": "f0"}}]}
        out = self._run(new, live)
        by = {m["slug"]: m for m in out["models"]}
        self.assertEqual([m["slug"] for m in out["models"]], ["ecens", "ecaie", "gefs", "fnv3"])  # canonical order
        # the three field models are byte-untouched
        self.assertEqual(by["ecens"]["latest"], "2026061412")
        self.assertEqual(by["ecaie"]["cycle_versions"], {"2026061418": "av"})
        self.assertEqual(by["gefs"]["latest"], "2026061412")
        # fnv3 advanced + unioned its prior live cycle (monotone history)
        self.assertEqual(by["fnv3"]["latest"], "2026061418")
        self.assertEqual(by["fnv3"]["cycles"], ["2026061418", "2026061412"])


if __name__ == "__main__":
    unittest.main()
