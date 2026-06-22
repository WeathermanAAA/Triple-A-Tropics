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
        # row 1 flag '03' -> sfmr nulled; row 2 flag '00' -> sfmr kept (48)
        self.assertIsNone(t[0]["sfmr"])
        self.assertEqual(t[1]["sfmr"], 48.0)
        # flight-level wind always present (fallback when sfmr flagged)
        self.assertEqual(t[0]["wspd"], 15.0)
        # peak SFMR uses only the kept value
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


if __name__ == "__main__":
    unittest.main()
