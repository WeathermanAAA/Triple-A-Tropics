"""ACE invest guard — the rule BY CONSTRUCTION, not circumstance.

A >=34 kt invest whose ATCF dev-level maps to a tropical nature ("TS")
used to be excluded from ACE/named only by circumstance: typical invests
are DB/LO-coded (-> "DS"), which the nature gate drops, and 91W-style
tropical-coded invests happened to stay under 34 kt. These tests pin the
explicit rule: an ATCF invest (storm number 90-99) contributes 0 ACE and
0 named/category counts NO MATTER its wind or nature — while still
appearing in the tracks-feed storms list (sidebar card / floater
discovery) and on the maps (red "L" active marker).

The synthetic case throughout: a 40 kt invest (91W) with tropical ("TS")
nature on recent 6-hourly synoptic fixes — exactly the shape that WOULD
have leaked ACE + a named count before the guard — plus a designated-TC
control (05W) with the same fix shape, proving the guard keys on the
invest number alone.
"""
from __future__ import annotations

import datetime as dt
import unittest

import pandas as pd

from ace_core import (
    build_global_geojson,
    compute_header_stats,
    current_year_storms,
    eligible_points_from_canon,
    fix_increment,
    merge_and_extract_storms,
    round_ace,
    storm_ace,
    storm_is_invest,
)

SEASON = 2026

BASIN_CFG = {
    "short": "wp",
    "agency_name": "JTWC",
    "invest_letter": "W",
}


def _recent_synoptic_times(n: int) -> list[dt.datetime]:
    """The last n 6-hourly synoptic times, ending at the most recent one —
    inside merge_and_extract_storms' ACTIVE_WINDOW_HOURS so the storm
    reads as active."""
    now = dt.datetime.utcnow()
    anchor = now.replace(hour=(now.hour // 6) * 6, minute=0,
                         second=0, microsecond=0)
    return [anchor - dt.timedelta(hours=6 * (n - 1 - i)) for i in range(n)]


def _live_rows(storm_num: int, name: str, winds: list[float]) -> list[dict]:
    """Synthetic live-b-deck rows in the exact canon schema parse_bdeck
    emits (tropical nature on every fix — the leak-shaped case)."""
    times = _recent_synoptic_times(len(winds))
    return [{
        "SID": f"JTWC_WP{storm_num:02d}{SEASON}",
        "NAME": name,
        "season": SEASON,
        "time": t,
        "lat": 15.0 + 0.5 * i,
        "lon": 130.0 + 1.0 * i,
        "wind_kt": float(w),
        "pressure_mb": 1000.0,
        "nature": "TS",
        "ace_nature": "TS",
        "source": "live-JTWC",
        "storm_num": storm_num,
    } for i, (t, w) in enumerate(zip(times, winds))]


def _canon(*row_groups: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([r for grp in row_groups for r in grp])


INVEST_WINDS = [35.0, 40.0, 40.0, 38.0]
TC_WINDS = [35.0, 45.0, 50.0, 45.0]

# A 40 kt tropical-natured invest: the storm that MUST NOT leak.
INVEST_ROWS = _live_rows(91, "INVEST", INVEST_WINDS)
# A designated-TC control: proves the guard does not over-apply.
TC_ROWS = _live_rows(5, "SYNTHO", TC_WINDS)

# What each WOULD contribute through the normal eligible-fix math (every
# fix 6-hourly, >=34 kt, tropical). Accumulated with a += loop exactly
# like storm_ace's (NOT builtin sum(): Python 3.12 sum() is Neumaier-
# compensated and can round differently at the 3rd decimal).


def _expected_ace(winds: list[float]) -> float:
    total = 0.0
    for w in winds:
        total += fix_increment(w)
    return round_ace(total)


LEAK_ACE = _expected_ace(INVEST_WINDS)
TC_ACE = _expected_ace(TC_WINDS)


class TestStormIsInvest(unittest.TestCase):

    def test_invest_numbers_are_invest(self):
        self.assertTrue(storm_is_invest(INVEST_ROWS))

    def test_designated_tc_is_not_invest(self):
        self.assertFalse(storm_is_invest(TC_ROWS))

    def test_missing_or_nan_storm_num_is_not_invest(self):
        # IBTrACS rows carry no/NaN storm_num — never invests.
        rows = [dict(r) for r in TC_ROWS]
        for r in rows:
            r["storm_num"] = float("nan")
        self.assertFalse(storm_is_invest(rows))
        for r in rows:
            del r["storm_num"]
        self.assertFalse(storm_is_invest(rows))

    def test_boundary_90_is_invest_89_is_not(self):
        # Pins BOTH sides of the >= 90 boundary (mutation coverage: a
        # '> 90' flip lets a 90-coded invest leak; an '>= 89' flip robs
        # a designated TC).
        inv90 = _live_rows(90, "INVEST", INVEST_WINDS)
        self.assertTrue(storm_is_invest(inv90))
        self.assertEqual(storm_ace(inv90, "wp"), 0.0)
        tc89 = _live_rows(89, "SYNTH89", TC_WINDS)
        self.assertFalse(storm_is_invest(tc89))
        self.assertEqual(storm_ace(tc89, "wp"), TC_ACE)


class TestAceMathGuard(unittest.TestCase):

    def test_storm_ace_zero_for_tropical_40kt_invest(self):
        # The guard itself: this storm would score LEAK_ACE without it.
        self.assertGreater(LEAK_ACE, 0.0)
        self.assertEqual(storm_ace(INVEST_ROWS, "wp"), 0.0)

    def test_storm_ace_control_designated_tc_accrues(self):
        # Identical fix shape, non-invest number -> normal ACE. The invest
        # number is the ONLY thing the guard keys on.
        self.assertEqual(storm_ace(TC_ROWS, "wp"), TC_ACE)

    def test_eligible_points_exclude_invest_fixes(self):
        pts = eligible_points_from_canon(_canon(INVEST_ROWS, TC_ROWS),
                                         BASIN_CFG, SEASON)
        self.assertFalse(pts.empty)
        self.assertEqual(set(pts["SID"].unique()), {f"JTWC_WP05{SEASON}"})
        # Invest-only canon -> empty by-DOY frame (0 ACE on the curve).
        pts_inv = eligible_points_from_canon(_canon(INVEST_ROWS),
                                             BASIN_CFG, SEASON)
        self.assertTrue(pts_inv.empty)

    def test_current_year_storms_exclude_invest(self):
        storms = current_year_storms(_canon(INVEST_ROWS, TC_ROWS),
                                     BASIN_CFG, SEASON)
        self.assertEqual([s["name"] for s in storms], ["SYNTHO"])
        self.assertEqual(storms[0]["ace_total"], TC_ACE)


class TestFeedAndHeaderGuard(unittest.TestCase):

    def setUp(self):
        live = _canon(INVEST_ROWS, TC_ROWS)
        self.storms = merge_and_extract_storms(pd.DataFrame(), live,
                                               BASIN_CFG)

    def _invest(self):
        return next(s for s in self.storms if s["is_invest"])

    def _tc(self):
        return next(s for s in self.storms if not s["is_invest"])

    def test_invest_still_in_storms_list(self):
        # The guard must NOT hide the invest: it stays a feed storm (card
        # grid + floater discovery + live overlay all read this list).
        inv = self._invest()
        self.assertEqual(inv["atcf_id"], "91W")
        self.assertTrue(inv["is_active"])
        self.assertTrue(inv["recent_invest"])

    def test_invest_ace_zero_in_feed(self):
        self.assertEqual(self._invest()["ace"], 0.0)
        self.assertEqual(self._tc()["ace"], TC_ACE)

    def test_header_counts_designated_tcs_only(self):
        # 40 kt peak -> max_category "TS"; without the guard the invest
        # would count as named AND leak LEAK_ACE into total_ace.
        self.assertEqual(self._invest()["max_category"], "TS")
        header = compute_header_stats(self.storms)
        self.assertEqual(header["named"], 1)
        self.assertEqual(header["cat1plus"], 0)
        self.assertEqual(header["total_ace"], TC_ACE)

    def test_header_zero_for_invest_only_basin(self):
        header = compute_header_stats([self._invest()])
        self.assertEqual(header["named"], 0)
        self.assertEqual(header["cat1plus"], 0)
        self.assertEqual(header["cat3plus"], 0)
        self.assertEqual(header["cat5"], 0)
        self.assertEqual(header["total_ace"], 0.0)

    def test_invest_keeps_red_x_marker_on_map(self):
        # Map presence is untouched: an ACTIVE invest still emits its
        # active_marker - the unified red invest X (see
        # test_marker_type_agreement for the full rule) - plus its track
        # + observation features. The ACE guard must never HIDE an invest.
        fc = build_global_geojson(self.storms)
        markers = {f["properties"]["storm_id"]: f["properties"]["marker_type"]
                   for f in fc["features"]
                   if f["properties"]["kind"] == "active_marker"}
        inv_sid = self._invest()["sid"]
        self.assertEqual(markers.get(inv_sid), "invest_x")
        kinds = {f["properties"]["kind"] for f in fc["features"]
                 if f["properties"].get("storm_id") == inv_sid}
        self.assertIn("track", kinds)
        self.assertIn("observation", kinds)


if __name__ == "__main__":
    unittest.main(verbosity=2)
