"""Poller-path units: the placeholder-name tasked-flight gate (the live bug
that hid an unnamed depression flown under a generic name token), flight-code
atcf derivation, multi-product block splitting, the bulletin cache, and the
publish script's fingerprint/shrink-guard logic."""
import datetime as dt
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reconobs import ingest, missions  # noqa: E402

YEAR = dt.datetime.now(dt.timezone.utc).year

# One live-shape HDOB bulletin for an unnamed depression: placeholder name
# token, tasked flight id 0102A (mission 01 -> storm 02, Atlantic).
HDOB_PLACEHOLDER = f"""000
URNT15 KNHC 191753
AF301 0102A CYCLONE            HDOB 10 {YEAR}0719
174400 2812N 08530W 9250 00793 0128 +206 +188 048010 011 /// /// 03
174430 2812N 08529W 9250 00793 0129 +208 +179 053009 010 /// /// 03
$$"""

# Same mission, later block where the name token flipped to the designation.
HDOB_FLIPPED = f"""000
URNT15 KNHC 191903
AF301 0102A TWO                HDOB 11 {YEAR}0719
185400 2812N 08530W 9250 00793 0128 +206 +188 048010 011 /// /// 03
185430 2813N 08531W 9251 00792 0128 +206 +188 048010 011 /// /// 03
$$"""

# A genuine training flight: WXWX flight code must STAY non-tropical.
HDOB_TRAIN = f"""000
URPN15 KWBC 172217
NOAA9 WXWXA TRAIN              HDOB 16 {YEAR}0717
220800 2907N 09015W 1630 13765 0688 -671 //// ////// /// /// /// 09
220830 2907N 09011W 1630 13766 0689 -660 //// ////// /// /// /// 09
$$"""


class TaskedFlightGate(unittest.TestCase):
    def test_placeholder_name_with_tasked_flight_is_tropical(self):
        for name in ("CYCLONE", "SYSTEM", "STORM", ""):
            self.assertTrue(missions.is_tropical_mission(name, "0102A"), name)

    def test_placeholder_without_tasked_flight_stays_non_tc(self):
        self.assertFalse(missions.is_tropical_mission("CYCLONE", "WXWXA"))
        self.assertFalse(missions.is_tropical_mission("CYCLONE", ""))

    def test_training_flight_still_non_tropical(self):
        self.assertFalse(missions.is_tropical_mission("TRAIN", "WXWXA"))

    def test_tasked_storm_parses(self):
        self.assertEqual(missions.tasked_storm("0102A"), (2, "al"))
        self.assertEqual(missions.tasked_storm("WA05E"), (5, "ep"))
        self.assertIsNone(missions.tasked_storm("WXWXA"))
        self.assertIsNone(missions.tasked_storm("05WSE"))
        self.assertIsNone(missions.tasked_storm(""))

    def test_number_word_designations(self):
        self.assertEqual(missions.storm_name_for_number(2), "TWO")
        self.assertEqual(missions.storm_name_for_number(21), "TWENTY-ONE")
        self.assertEqual(missions.storm_name_for_number(95), "INVEST")
        self.assertIsNone(missions.storm_name_for_number(45))

    def test_invest_slot_relabels_without_derived_atcf(self):
        # 90-99: rescued + named INVEST, but NO derived atcf — a derived id
        # would leak via the (basin, INVEST) grouping key onto other invests.
        blk = HDOB_PLACEHOLDER.replace("0102A", "0192A")
        mis = missions.build_missions([blk])
        m = next(iter(mis.values()))
        self.assertTrue(m["is_tropical"])
        self.assertTrue(m["is_invest"])
        self.assertEqual(m["storm_name"], "INVEST")
        self.assertIsNone(m["atcf"])

    def test_build_missions_relabels_and_derives_atcf(self):
        mis = missions.build_missions([HDOB_PLACEHOLDER])
        self.assertEqual(len(mis), 1)
        m = next(iter(mis.values()))
        self.assertTrue(m["is_tropical"])
        self.assertFalse(m["is_invest"])
        self.assertEqual(m["storm_name"], "TWO")
        self.assertEqual(m["mission_id"], "AF301-0102A-TWO")
        self.assertEqual(m["atcf"], f"al02{YEAR}")
        self.assertEqual(m["n_obs"], 2)

    def test_token_flip_merges_into_one_mission(self):
        mis = missions.build_missions([HDOB_PLACEHOLDER, HDOB_FLIPPED])
        self.assertEqual(len(mis), 1)
        m = next(iter(mis.values()))
        self.assertEqual(m["mission_id"], "AF301-0102A-TWO")
        self.assertEqual(m["n_obs"], 4)

    def test_derived_atcf_not_overwritten_by_vdm(self):
        mis = missions.build_missions([HDOB_PLACEHOLDER])
        m = next(iter(mis.values()))
        self.assertEqual(m["atcf"], f"al02{YEAR}")
        # a (mis-)attached VDM carrying a different atcf must not re-identify
        missions._attach_nearest(mis, {"atcf": "al992026", "lat": 28.0,
                                       "lon": -85.0, "t": None,
                                       "vdm_day": 19, "vdm_tod": 63000},
                                 "vdm_centers")
        self.assertEqual(m["atcf"], f"al02{YEAR}")

    def test_training_mission_dropped_end_to_end(self):
        mis = missions.build_missions([HDOB_TRAIN])
        self.assertEqual(len(mis), 1)
        m = next(iter(mis.values()))
        self.assertFalse(m["is_tropical"])


class BlockSplitting(unittest.TestCase):
    def test_split_multi_product_payload(self):
        payload = ("\n763 \n" + HDOB_PLACEHOLDER.split("\n", 1)[1]
                   + "\n\n231 \n" + HDOB_FLIPPED.split("\n", 1)[1] + "\n")
        blocks = ingest.split_hdob_blocks(payload)
        self.assertEqual(len(blocks), 2)
        for b in blocks:
            self.assertTrue(b.startswith("000\n"))
        # re-framed blocks decode: layout seq/WMO/mission rows holds
        mis = missions.build_missions(blocks)
        self.assertEqual(len(mis), 1)
        self.assertEqual(next(iter(mis.values()))["n_obs"], 4)

    def test_split_empty(self):
        self.assertEqual(ingest.split_hdob_blocks(None), [])
        self.assertEqual(ingest.split_hdob_blocks("no headers here"), [])


class BulletinCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_cache_hit_skips_network(self):
        calls = []
        with mock.patch.object(ingest.fetch, "get",
                               side_effect=lambda u, **k: calls.append(u)
                               or HDOB_PLACEHOLDER):
            a = ingest._cached_fetch("http://x/f.202607191753.txt",
                                     "f.202607191753.txt", "PILX",
                                     self.tmp, 0.0)
            b = ingest._cached_fetch("http://x/f.202607191753.txt",
                                     "f.202607191753.txt", "PILX",
                                     self.tmp, 0.0)
        self.assertEqual(a, HDOB_PLACEHOLDER)
        self.assertEqual(b, HDOB_PLACEHOLDER)
        self.assertEqual(len(calls), 1)           # second read came from disk

    def test_failed_fetch_not_cached(self):
        with mock.patch.object(ingest.fetch, "get", return_value=None):
            self.assertIsNone(ingest._cached_fetch(
                "http://x/f.202607191753.txt", "f.202607191753.txt",
                "PILX", self.tmp, 0.0))
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp, "PILX", "f.202607191753.txt")))

    def test_prune_drops_out_of_window_files(self):
        d = os.path.join(self.tmp, "PILX")
        os.makedirs(d)
        old = os.path.join(d, "f.202001010000.txt")
        new = os.path.join(d, f"f.{YEAR}07190000.txt")
        for p in (old, new):
            open(p, "w").write("x")
        since = dt.datetime(YEAR, 7, 1, tzinfo=dt.timezone.utc)
        ingest._prune_cache(self.tmp, since)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))


def _load_publish():
    spec = importlib.util.spec_from_file_location(
        "recon_r2_publish",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "scripts", "recon_r2_publish.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PublishGate(unittest.TestCase):
    def setUp(self):
        self.pub = _load_publish()
        self.manifest = {"generated_utc": "2026-07-19T12:00:00Z",
                         "current_slug": "al022026", "has_active_recon": True,
                         "tcpod_number": "26-049",
                         "storms": [{"slug": "al022026",
                                     "last_ob_utc": "2026-07-19T17:53:30Z",
                                     "mission_count": 1,
                                     "latest_mission_id": "AF301-0102A-TWO",
                                     "peak_sfmr_kt": None,
                                     "min_p_sfc_hpa": 1005.3}]}
        self.current = {"generated_utc": "2026-07-19T12:00:00Z",
                        "storm_slug": "al022026", "has_active": True,
                        "mission": {"mission_id": "AF301-0102A-TWO",
                                    "valid_end": "2026-07-19T17:53:30Z",
                                    "n_obs": 40, "vdm_centers": [],
                                    "sondes": []}}
        self.tcpod = {"raw": "TCPOD 26-049 ...", "fetched_utc": "x"}

    def test_fingerprint_ignores_volatile_stamps(self):
        a = self.pub._fingerprint(self.manifest, self.current, self.tcpod)
        m2 = dict(self.manifest, generated_utc="2026-07-19T12:10:00Z")
        c2 = dict(self.current, generated_utc="2026-07-19T12:10:00Z")
        t2 = dict(self.tcpod, fetched_utc="y")
        self.assertEqual(a, self.pub._fingerprint(m2, c2, t2))

    def test_fingerprint_changes_on_new_obs(self):
        a = self.pub._fingerprint(self.manifest, self.current, self.tcpod)
        c2 = json.loads(json.dumps(self.current))
        c2["mission"]["n_obs"] = 60
        c2["mission"]["valid_end"] = "2026-07-19T18:03:30Z"
        self.assertNotEqual(a, self.pub._fingerprint(self.manifest, c2,
                                                     self.tcpod))

    def test_fingerprint_changes_on_new_vdm_and_tcpod(self):
        a = self.pub._fingerprint(self.manifest, self.current, self.tcpod)
        c2 = json.loads(json.dumps(self.current))
        c2["mission"]["vdm_centers"] = [{"lat": 1}]
        self.assertNotEqual(a, self.pub._fingerprint(self.manifest, c2,
                                                     self.tcpod))
        t2 = dict(self.tcpod, raw="TCPOD 26-050 ...")
        self.assertNotEqual(a, self.pub._fingerprint(self.manifest,
                                                     self.current, t2))

    def test_fingerprint_none_safe(self):
        self.assertIsInstance(self.pub._fingerprint(None, None, None), str)

    def test_age_parsing(self):
        self.assertEqual(self.pub._age_s(None), float("inf"))
        self.assertEqual(self.pub._age_s("garbage"), float("inf"))
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertLess(self.pub._age_s(now), 5.0)


class DesignationNaming(unittest.TestCase):
    """Number-word designations must lose to a real name in _best_name."""
    def _mk(self, name):
        return {"storm_name": name, "is_invest": False}

    def test_real_name_beats_longer_designation(self):
        from reconobs.build import _best_name
        self.assertEqual(_best_name([self._mk("THIRTEEN"),
                                     self._mk("LORENZO")]), "Lorenzo")

    def test_designation_only_storm_keeps_designation(self):
        from reconobs.build import _best_name
        self.assertEqual(_best_name([self._mk("TWO")]), "Two")


class StrictPriorManifest(unittest.TestCase):
    def test_unreadable_http_prior_raises(self):
        import reconobs.build as bmod_pkg
        import sys
        bmod = sys.modules["reconobs.build"]
        with mock.patch.object(bmod.fetch, "get", return_value=None):
            with self.assertRaises(RuntimeError):
                bmod._fetch_prior_manifest("https://x.example/manifest.json")

    def test_none_url_starts_fresh(self):
        import sys
        import reconobs.build  # noqa: F401
        bmod = sys.modules["reconobs.build"]
        self.assertIsNone(bmod._fetch_prior_manifest(None))
        self.assertIsNone(bmod._fetch_prior_manifest(""))


class CacheValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_non_bulletin_body_returned_but_not_cached(self):
        html = "<html><body>maintenance</body></html>"
        with mock.patch.object(ingest.fetch, "get", return_value=html):
            out = ingest._cached_fetch("http://x/f.202607191753.txt",
                                       "f.202607191753.txt", "PILX",
                                       self.tmp, 0.0)
        self.assertEqual(out, html)               # this tick still sees it
        self.assertFalse(os.path.exists(          # ...but it cannot poison
            os.path.join(self.tmp, "PILX", "f.202607191753.txt")))

    def test_real_bulletin_cached(self):
        with mock.patch.object(ingest.fetch, "get",
                               return_value=HDOB_PLACEHOLDER):
            ingest._cached_fetch("http://x/f.202607191753.txt",
                                 "f.202607191753.txt", "PILX", self.tmp, 0.0)
        self.assertTrue(os.path.exists(
            os.path.join(self.tmp, "PILX", "f.202607191753.txt")))


if __name__ == "__main__":
    unittest.main()
