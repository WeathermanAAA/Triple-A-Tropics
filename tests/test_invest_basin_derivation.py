"""knackwx invest discovery must key the basin off the ATCF id's trailing
letter, NOT the separate `origin_basin` field.

THE 2026-06-14 BUG: knackwx serves East-Pacific invest 93E with
`origin_basin: null`. The old filter `(it["origin_basin"] or "").upper()
!= letter` therefore dropped 93E from the EP tracks feed (""  != "E"),
so it never reached the home global tracks map - even though its b-deck
existed and HAFS was already running it. WPAC's 92W (origin_basin "W")
was unaffected, which masked the gap.

THE FIX (mirrored in tat-satellite-render/intensity_poller.py): the
trailing letter of the atcf_id ("93E" -> E) is the authoritative ATCF
basin designator; origin_basin is only a fallback. So an invest with a
null/blank origin_basin but a well-formed id is still placed in its
basin, and an invest from another basin (e.g. South-Pacific "96P") is
still excluded.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_tracks_plot as gtp  # noqa: E402


# A faithful slice of the live knackwx /atcf/v2 payload from 2026-06-14:
# 93E carries origin_basin=null (the bug trigger), 92W carries "W", and
# 96P is a South-Pacific invest that belongs to no basin we publish.
KNACKWX_SAMPLE = [
    {"atcf_id": "93E", "origin_basin": None, "storm_name": "INVEST",
     "cyclone_nature": "DB", "latitude": 8.1, "longitude": -132.0,
     "winds": 25, "pressure": 1009, "analysis_time": "2026-06-14T18:00:00.000Z"},
    {"atcf_id": "92W", "origin_basin": "W", "storm_name": "INVEST",
     "cyclone_nature": "WV", "latitude": 9.1, "longitude": 164.2,
     "winds": 20, "pressure": 1008, "analysis_time": "2026-06-14T12:00:00.000Z"},
    {"atcf_id": "96P", "origin_basin": "P", "storm_name": "INVEST",
     "cyclone_nature": "SD", "latitude": -15.0, "longitude": 169.3,
     "winds": 25, "pressure": 1005, "analysis_time": "2026-06-14T12:00:00.000Z"},
]


class _FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")
        self.status = 200

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class InvestBasinDerivationTest(unittest.TestCase):
    def setUp(self):
        import urllib.request
        self._orig = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: _FakeResp(KNACKWX_SAMPLE)

    def tearDown(self):
        import urllib.request
        urllib.request.urlopen = self._orig

    def _fetch(self, basin: str):
        return gtp.fetch_live_invests(2026, gtp.BASINS[basin], f"[{basin}-test]")

    def test_ep_keeps_93e_despite_null_origin_basin(self):
        df = self._fetch("ep")
        self.assertFalse(df.empty, "EP invest feed was empty - 93E dropped")
        self.assertEqual(set(df["storm_num"]), {93})
        self.assertEqual(df.iloc[0]["NAME"], "93E")
        # SID format must match the b-deck path so a future promotion to a
        # numbered TC doesn't collide with this invest row.
        self.assertEqual(df.iloc[0]["SID"], "NHC_EP932026")

    def test_wp_still_keeps_92w(self):
        df = self._fetch("wp")
        self.assertEqual(set(df["storm_num"]), {92})
        self.assertEqual(df.iloc[0]["NAME"], "92W")

    def test_other_basin_invest_excluded(self):
        # 96P (South Pacific) must not leak into AL/EP/WP feeds.
        for basin in ("ep", "wp", "al"):
            df = self._fetch(basin)
            nums = set(df["storm_num"]) if not df.empty else set()
            self.assertNotIn(96, nums, f"96P leaked into {basin}")

    def test_al_has_no_invests_in_this_sample(self):
        df = self._fetch("al")
        self.assertTrue(df.empty, "no AL (letter 'L') invest in the sample")


if __name__ == "__main__":
    unittest.main()
