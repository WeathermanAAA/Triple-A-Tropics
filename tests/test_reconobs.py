"""Tests for the reconobs aircraft-recon backend (offline, no network).

  python -m pytest tests/test_reconobs.py -q
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reconobs import missions as M       # noqa: E402
from reconobs.tcpod import parse_tcpod   # noqa: E402

# --- a tiny real-format HDOB message; sfmr present but row flag '03' (=> the
# sfmr/rain group is QC-flagged and must be nulled), one clean row flag '00'.
HDOB = """000
URNT15 KNHC 171534
NOAA3 0301A ARTHUR             HDOB 41 20260617
214430 2752N 08214W 8745 01319 0164 +215 +068 215015 017 035 002 03
214500 2750N 08212W 8759 01307 0168 +211 +073 231040 044 048 003 00
$$
"""

ACTIVE_TCPOD = """000
NOUS42 KNHC 042115
REPRPD
WEATHER RECONNAISSANCE FLIGHTS
CARCAH, NATIONAL HURRICANE CENTER, MIAMI, FL.
0515 PM EDT FRI 04 OCTOBER 2024
SUBJECT: TROPICAL CYCLONE PLAN OF THE DAY (TCPOD)
         VALID 05/1100Z TO 06/1100Z OCTOBER 2024
         TCPOD NUMBER.....24-126 AMENDMENT

I.  ATLANTIC REQUIREMENTS
    1. HURRICANE MILTON (AL14).....REQUIRED
       A. 06/1200Z
       B. NOAA3 01HHA TDR
       C. 06/0800Z
       D. 22.0N 94.0W
       E. 06/1000Z TO 06/1400Z
       F. SFC TO 10,000 FT
       G. TAIL DOPPLER RADAR
    2. OUTLOOK FOR SUCCEEDING DAY:
       A. POSSIBLE INVEST MISSION NEAR 22.5N 92.5W FOR 06/1800Z.

II. PACIFIC REQUIREMENTS
    1. NEGATIVE RECONNAISSANCE REQUIREMENTS.
    2. OUTLOOK FOR SUCCEEDING DAY.....NEGATIVE.

$$
"""

NEG_TCPOD = """000
NOUS42 KNHC 221405
REPRPD
CARCAH, NATIONAL HURRICANE CENTER, MIAMI, FL.
SUBJECT: TROPICAL CYCLONE PLAN OF THE DAY (TCPOD)
         VALID 23/1100Z TO 24/1100Z JUNE 2026
         TCPOD NUMBER.....26-022
I.  ATLANTIC REQUIREMENTS
    1. NEGATIVE RECONNAISSANCE REQUIREMENTS.
    2. OUTLOOK FOR SUCCEEDING DAY.....NEGATIVE.
II. PACIFIC REQUIREMENTS
    1. NEGATIVE RECONNAISSANCE REQUIREMENTS.
    2. OUTLOOK FOR SUCCEEDING DAY.....NEGATIVE.
$$
"""

VDM = """000
URNT12 KNHC 171534
VORTEX DATA MESSAGE   AL012026
A. 17/15:01:10Z
B. 28.56 deg N 095.83 deg W
C. 925 mb 719 m
D. EXTRAP 1004 mb
$$
"""


class TestHdobDecodeAndFlags(unittest.TestCase):
    def test_decode_and_flag_drop(self):
        mis = M.build_missions([HDOB])
        self.assertEqual(len(mis), 1)
        m = next(iter(mis.values()))
        self.assertEqual(m["storm_name"], "ARTHUR")
        self.assertTrue(m["is_tropical"])
        self.assertFalse(m["is_invest"])
        self.assertEqual(m["n_obs"], 2)
        t = m["track"]
        # row 1 flag '03' -> SFMR/rain QC-flagged: value KEPT (raw) but marked
        # suspect; row 2 flag '00' -> clean.
        self.assertEqual(t[0]["sfmr"], 35.0)
        self.assertTrue(t[0]["sfmr_suspect"])
        self.assertIsNotNone(t[0]["rain"])
        self.assertEqual(t[1]["sfmr"], 48.0)
        self.assertFalse(t[1]["sfmr_suspect"])
        # flight-level wind always present (not in the SFMR flag group)
        self.assertEqual(t[0]["wspd"], 15.0)
        # peak SFMR EXCLUDES suspect points -> only row 2's clean 48
        self.assertEqual(m["peak_sfmr_kt"], 48.0)

    def test_vdm_attaches_with_surface_mslp_and_atcf(self):
        mis = M.build_missions([HDOB])
        M.add_vdm(mis, [VDM])
        m = next(iter(mis.values()))
        self.assertEqual(len(m["vdm_centers"]), 1)
        c = m["vdm_centers"][0]
        self.assertEqual(c["atcf"], "al012026")
        self.assertAlmostEqual(c["lat"], 28.56)
        self.assertAlmostEqual(c["lon"], -95.83)
        self.assertEqual(c["mslp_hpa"], 1004.0)   # D. line, not C. 925


class TestTropicalFilter(unittest.TestCase):
    def test_excludes_research_training(self):
        self.assertFalse(M.is_tropical_mission("TRAIN", "WXWXA"))
        self.assertFalse(M.is_tropical_mission("TEXAQS11", "WXWXA"))
        self.assertFalse(M.is_tropical_mission("SURVEY", "0101A"))
        self.assertFalse(M.is_tropical_mission("260306153502309", "WXWXE"))
        self.assertTrue(M.is_tropical_mission("ARTHUR", "0301A"))
        self.assertTrue(M.is_tropical_mission("INVEST", "0201A"))


class TestTcpod(unittest.TestCase):
    def test_active(self):
        t = parse_tcpod(ACTIVE_TCPOD)
        self.assertEqual(t["tcpod_number"], "24-126")
        self.assertTrue(t["amendment"])
        self.assertEqual(t["valid_from_utc"], "2024-10-05T11:00:00Z")
        self.assertTrue(t["has_active_missions"])
        atl = t["basins"]["atlantic"]
        self.assertFalse(atl["negative"])
        self.assertEqual(len(atl["missions"]), 1)
        mm = atl["missions"][0]
        self.assertIn("MILTON", mm["title"].upper())
        self.assertEqual(mm["status"], "REQUIRED")
        self.assertEqual(mm["target"]["lat"], 22.0)
        self.assertEqual(mm["target"]["lon"], -94.0)
        self.assertEqual(mm["mission_type"], "TAIL DOPPLER RADAR")
        self.assertTrue(atl["outlook"])
        self.assertTrue(t["basins"]["pacific"]["negative"])

    def test_negative(self):
        t = parse_tcpod(NEG_TCPOD)
        self.assertEqual(t["tcpod_number"], "26-022")
        self.assertFalse(t["has_active_missions"])
        self.assertTrue(t["basins"]["atlantic"]["negative"])
        self.assertTrue(t["basins"]["pacific"]["negative"])


HDOB_2013 = """000
URNT15 KNHC 060900
NOAA3 0301A ANDREA             HDOB 01 20130606
090000 2752N 08214W 8745 01319 0164 +215 +068 215040 044 048 002 00
090030 2750N 08212W 8759 01307 0168 +211 +073 231045 047 050 003 00
$$
"""


class TestBackfillMergesWithoutRegressingLive(unittest.TestCase):
    """A backfill run must EXTEND the manifest (add historical storms) and
    leave the live current-season data untouched: current.json/tcpod.json not
    written, current_slug/has_active/tcpod_number preserved from the prior
    manifest. The manifest is the growing union."""

    def setUp(self):
        import json
        import tempfile
        from reconobs.build import build as run_build
        self.run_build = run_build
        self.tmp = tempfile.mkdtemp()
        # a prior manifest representing the live current-season state
        self.prior = self.tmp + "/prior.json"
        with open(self.prior, "w") as f:
            json.dump({"schema_version": 1, "storms": [
                {"slug": "al012026", "name": "Arthur", "basin": "AL",
                 "year": 2026, "atcf": "al012026", "is_invest": False,
                 "mission_count": 2, "last_ob_utc": "2026-06-17T21:54:00Z",
                 "peak_sfmr_kt": 42, "min_p_sfc_hpa": 995.4}],
                "current_slug": "al012026", "has_active_recon": False,
                "tcpod_number": "26-022"}, f)

    def test_backfill_extends_and_preserves(self):
        import json
        import os
        from unittest import mock
        from reconobs import ingest
        bag = {"basins": {"AL": {"hdob": [HDOB_2013], "vdm": [], "sonde": []}},
               "dropped": 0}
        with mock.patch.object(ingest, "gather_window", return_value=bag), \
                mock.patch.object(ingest, "gather_live_hdob",
                                  return_value=[]), \
                mock.patch.object(ingest, "gather_tcpod",
                                  return_value=None):
            summary = self.run_build(self.tmp, backfill_year=2013,
                                   backfill_month=6, basins=("AL",),
                                   prior_manifest_url=self.prior)
        self.assertEqual(summary["mode"], "backfill")
        man = json.load(open(self.tmp + "/manifest.json"))
        slugs = {s["slug"] for s in man["storms"]}
        # union: the live storm preserved + the backfilled 2013 storm added
        self.assertIn("al012026", slugs)
        self.assertIn("al_andrea_2013", slugs)
        # live "current" fields preserved (NOT recomputed to a 2013 mission)
        self.assertEqual(man["current_slug"], "al012026")
        self.assertEqual(man["has_active_recon"], False)
        self.assertEqual(man["tcpod_number"], "26-022")
        # a backfill writes NEITHER current.json NOR tcpod.json
        self.assertFalse(os.path.exists(self.tmp + "/current.json"))
        self.assertFalse(os.path.exists(self.tmp + "/tcpod.json"))
        # the historical storm's per-storm JSON is written
        self.assertTrue(os.path.exists(self.tmp + "/al_andrea_2013/recon.json"))

    def test_incremental_merges_into_prior_union(self):
        # An incremental run must KEEP prior historical storms (upsert), not
        # drop them - else a cron run after backfill would erase the archive.
        import json
        from unittest import mock
        from reconobs import ingest
        # prior already has a historical 2013 storm
        with open(self.prior, "w") as f:
            json.dump({"schema_version": 1, "storms": [
                {"slug": "al_andrea_2013", "name": "Andrea", "basin": "AL",
                 "year": 2013, "atcf": None, "is_invest": False,
                 "mission_count": 1, "last_ob_utc": "2013-06-06T09:00:30Z"}],
                "current_slug": None, "has_active_recon": False}, f)
        bag = {"basins": {"AL": {"hdob": [], "vdm": [], "sonde": []}},
               "dropped": 0}
        with mock.patch.object(ingest, "gather_window", return_value=bag), \
                mock.patch.object(ingest, "gather_live_hdob",
                                  return_value=[HDOB_2013]), \
                mock.patch.object(ingest, "gather_tcpod", return_value=None):
            self.run_build(self.tmp, window_days=7, basins=("AL",),
                         prior_manifest_url=self.prior)
        man = json.load(open(self.tmp + "/manifest.json"))
        slugs = {s["slug"] for s in man["storms"]}
        self.assertIn("al_andrea_2013", slugs)   # prior historical preserved


class TestHdobArchivePathRouting(unittest.TestCase):
    """HDOB URLs must route to the PIL dir for 2012+ and to the per-agency
    pre-2012 subtree for 2007-2011, while VDM/sonde keep the PIL dir always.
    A pre-2007 year emits a skip-and-log and gathers no HDOB (but still VDM)."""

    def setUp(self):
        from reconobs import ingest
        self.ingest = ingest
        self.since = __import__("datetime").datetime(
            2000, 1, 1, tzinfo=__import__("datetime").timezone.utc)

    def test_year_ge_2012_uses_pil_dir(self):
        dirs = self.ingest.hdob_dirs(2016, "AL")
        self.assertEqual(dirs, [(self.ingest.ARCHIVE.format(
            year=2016, pil="AHONT1"), None)])
        # EP twin
        self.assertEqual(self.ingest.hdob_dirs(2018, "EP")[0][0],
                         self.ingest.ARCHIVE.format(year=2018, pil="AHOPN1"))

    def test_2008_2011_use_per_agency_subdir(self):
        dirs = self.ingest.hdob_dirs(2010, "AL")
        urls = [u for u, _ in dirs]
        self.assertEqual(urls, [
            "https://www.nhc.noaa.gov/archive/recon/2010/HDOB/USAF/URNT15/",
            "https://www.nhc.noaa.gov/archive/recon/2010/HDOB/NOAA/URNT15/"])
        self.assertTrue(all(p is None for _, p in dirs))  # subdir is pure
        # EP -> URPN15
        self.assertIn("URPN15", self.ingest.hdob_dirs(2009, "EP")[0][0])

    def test_2007_uses_flat_dir_with_prefix_filter(self):
        dirs = self.ingest.hdob_dirs(2007, "AL")
        self.assertEqual(dirs, [
            ("https://www.nhc.noaa.gov/archive/recon/2007/HDOB/USAF/",
             "URNT15"),
            ("https://www.nhc.noaa.gov/archive/recon/2007/HDOB/NOAA/",
             "URNT15")])

    def test_pre_2007_returns_no_dirs(self):
        self.assertEqual(self.ingest.hdob_dirs(2006, "AL"), [])
        self.assertEqual(self.ingest.hdob_dirs(2005, "EP"), [])

    def test_recent_hdob_merges_agencies_and_filters_flat_dir(self):
        from unittest import mock
        # 2007 flat dirs mix basins -> only URNT15 (AL) names must survive,
        # merged across both agencies, each fetched from its own dir URL.
        def fake_list(url):
            if "USAF" in url:
                return ["URNT15-KNHC.200708011648.txt",
                        "URPN15-KBIX.200708121953.txt"]  # EP noise, drop
            return ["URNT15-KWBC.200708151200.txt"]
        with mock.patch.object(self.ingest.fetch, "list_dir_txt",
                               side_effect=fake_list):
            pairs, _ = self.ingest._recent_hdob(2007, "AL", self.since)
        urls = {u for u, _ in pairs}
        names = {n for _, n in pairs}
        self.assertEqual(names, {"URNT15-KNHC.200708011648.txt",
                                 "URNT15-KWBC.200708151200.txt"})
        self.assertIn("/HDOB/USAF/URNT15-KNHC.200708011648.txt",
                      next(u for u in urls if "USAF" in u))
        self.assertTrue(any("/HDOB/NOAA/" in u for u in urls))

    def test_gather_window_routes_hdob_vs_vdm_and_logs_legacy(self):
        from unittest import mock
        seen = []

        def fake_list(url):
            seen.append(url)
            return []
        # 2010: HDOB hits the alt subtree, VDM/sonde hit the PIL dir.
        with mock.patch.object(self.ingest.fetch, "list_dir_txt",
                               side_effect=fake_list):
            self.ingest.gather_window(2010, self.since, basins=("AL",),
                                      log=lambda *a: None)
        self.assertTrue(any("/HDOB/USAF/URNT15/" in u for u in seen))
        self.assertTrue(any(u.endswith("/REPNT2/") for u in seen))   # VDM PIL
        self.assertFalse(any("/AHONT1/" in u for u in seen))         # no PIL HDOB
        # 2005: legacy -> skip-and-log, no HDOB listing, VDM still listed.
        seen.clear()
        logs = []
        with mock.patch.object(self.ingest.fetch, "list_dir_txt",
                               side_effect=fake_list):
            self.ingest.gather_window(2005, self.since, basins=("AL",),
                                      log=lambda *a: logs.append(
                                          " ".join(map(str, a))))
        self.assertFalse(any("/HDOB/" in u for u in seen))
        self.assertTrue(any(u.endswith("/REPNT2/") for u in seen))
        self.assertTrue(any("predates the modern HDOB archive" in m
                            for m in logs))


class TestStormNameConsolidation(unittest.TestCase):
    """Old-format HDOBs suffix the storm name with a varying storm-number
    (IKE/IKE1/IKE2/IKE4 = one Ike), fragmenting it across slugs. The fix
    canonicalizes the name + collapses the stale manifest fragments."""

    def test_canonical_storm_name_strips_digit_suffix(self):
        from reconobs.missions import canonical_storm_name as c
        # named storms with a trailing storm-number fragment -> canonical
        self.assertEqual(c("IKE1"), "IKE")
        self.assertEqual(c("IKE4"), "IKE")
        self.assertEqual(c("Hanna2"), "HANNA")
        self.assertEqual(c("ike2"), "IKE")
        # clean names + invests + short/numeric codes untouched (no mangling)
        for n in ("IKE", "GUSTAV", "INVEST", "LOW", "WAVE", "90L", "TD", "AL"):
            self.assertEqual(c(n), n.upper())
        self.assertEqual(c(""), "")

    def test_drop_fragment_entries_collapses_and_preserves(self):
        from reconobs.build import _drop_fragment_entries
        def e(slug, name, year=2008, atcf=None):
            return {"slug": slug, "name": name, "basin": "AL",
                    "year": year, "atcf": atcf}
        union = [
            e("al_ike_2008", "Ike"), e("al_ike1_2008", "Ike1"),
            e("al_ike2_2008", "Ike2"), e("al_ike4_2008", "Ike4"),
            e("al_hanna_2008", "Hanna"), e("al_hanna2_2008", "Hanna2"),
            e("al_invest_2008", "Invest"),
            e("al012026", "Andrea", 2026, "al012026"),
        ]
        kept = [s["slug"] for s in _drop_fragment_entries(union, "al012026")]
        # fragments dropped; canonical + invest + current-season kept
        self.assertEqual(kept, ["al_ike_2008", "al_hanna_2008",
                                "al_invest_2008", "al012026"])
        # idempotent
        again = _drop_fragment_entries(
            [s for s in union if s["slug"] in kept], "al012026")
        self.assertEqual([s["slug"] for s in again], kept)
        # a fragment with NO canonical sibling present is kept (no data loss)
        orphan = [e("al_fay1_2008", "Fay1")]
        self.assertEqual(
            [s["slug"] for s in _drop_fragment_entries(orphan, None)],
            ["al_fay1_2008"])
        # never drop the live current-season storm even if it looked like a frag
        cur = [e("al_ike_2008", "Ike"), e("al_ike1_2008", "Ike1")]
        self.assertIn("al_ike1_2008",
                      [s["slug"] for s in _drop_fragment_entries(cur, "al_ike1_2008")])


if __name__ == "__main__":
    unittest.main()
