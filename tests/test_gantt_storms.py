"""Regression + unit coverage for the TD-inclusive Storm-Activity Gantt (item A).

Guards two things that escaped the original CI:
  1. compute_ace_timeseries now returns a 2-TUPLE (ace_points, trop_points). BOTH
     callers (generate_ace_plot.main AND build_feed_base) must unpack it — a
     single-value caller crashes the whole update-ace workflow. We assert the
     contract here and that build_feed_base unpacks it.
  2. extract_gantt_storms_by_year + the ATCF-id label helpers: TD-strength
     systems get a bar (ace_total 0, ACE untouched); unnamed-but-designated
     systems are labelled "TD NNx", never "UNNAMED"; truly id-less phantoms are
     dropped.
"""
import datetime as dt
import inspect
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ace_core as ac  # noqa: E402
import generate_ace_plot as gap  # noqa: E402


def _synthetic_ibtracs():
    """A tiny EP IBTrACS frame: one named TS (peaks 55 kt), one TD-only system
    (peaks 30 kt, NATURE TS), and one unnamed-but-designated TD (EP04, 30 kt)."""
    rows = []

    def storm(sid, name, atcf, winds, start):
        for i, w in enumerate(winds):
            t = start + dt.timedelta(hours=6 * i)
            rows.append({
                "BASIN": "EP", "TRACK_TYPE": "main", "SEASON": 2023,
                "SID": sid, "NAME": name, "NATURE": "TS",
                "USA_ATCF_ID": atcf, "USA_WIND": w,
                "ISO_TIME": t.strftime("%Y-%m-%d %H:%M:%S"),
            })

    base = dt.datetime(2023, 8, 1, 0, 0, 0)
    storm("2023A", "ADRIAN", "EP012023", [25, 35, 55, 45, 30], base)
    storm("2023B", "UNNAMED", None, [25, 30, 30, 25], base + dt.timedelta(days=5))
    storm("2023C", "UNNAMED", "EP042023", [25, 30, 30], base + dt.timedelta(days=10))
    return pd.DataFrame(rows)


class TestComputeAceContract(unittest.TestCase):
    def test_returns_two_tuple(self):
        df = _synthetic_ibtracs()
        result = gap.compute_ace_timeseries(df, gap.BASINS["ep"], log_prefix="[t]")
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        points, trop = result
        self.assertIsInstance(points, pd.DataFrame)
        self.assertIsInstance(trop, pd.DataFrame)

    def test_points_is_ace_eligible_only(self):
        df = _synthetic_ibtracs()
        points, trop = gap.compute_ace_timeseries(df, gap.BASINS["ep"], "[t]")
        # ACE frame: only >=34 kt fixes, with an ace_increment column.
        self.assertIn("ace_increment", points.columns)
        self.assertTrue((points["WIND_KT"] >= 34).all())
        # trop frame: the TD-strength fixes survive (ANY wind) and carry ATCF.
        self.assertIn("ATCF", trop.columns)
        self.assertTrue((trop["WIND_KT"] < 34).any())
        # The TD-only storm (2023B) contributes 0 rows to points but >=1 to trop.
        self.assertEqual((points["SID"] == "2023B").sum(), 0)
        self.assertGreater((trop["SID"] == "2023B").sum(), 0)

    def test_build_feed_base_unpacks_the_tuple(self):
        # Static guard: build_feed_base must unpack the 2-tuple, not treat the
        # return as a DataFrame (the regression that crashed update-ace).
        import build_feed_base
        src = inspect.getsource(build_feed_base)
        self.assertIn("compute_ace_timeseries", src)
        # the call must be a tuple-unpack: "<a>, <b> = ...compute_ace_timeseries("
        self.assertRegex(
            src, r",\s*\w+\s*=\s*\w+\.compute_ace_timeseries\(",
            "build_feed_base must unpack the (points, trop) tuple")


class TestGanttExtraction(unittest.TestCase):
    def _trop(self):
        """A trop-style frame: named TS, TD-only, unnamed-designated, and an
        unnamed system with NO ATCF id (must be dropped)."""
        rows = []

        def fix(season, sid, name, atcf, wind, day):
            t = dt.datetime(season, 8, day, 0, 0, 0)
            rows.append({"season": season, "doy": t.timetuple().tm_yday,
                         "SID": sid, "NAME": name, "ISO_TIME": pd.Timestamp(t),
                         "WIND_KT": wind, "ATCF": atcf})

        for d, w in zip((1, 1, 2), (25, 55, 35)):
            fix(2023, "A", "ADRIAN", "EP012023", w, d if d == 1 else d + 1)
        fix(2023, "A", "ADRIAN", "EP012023", 55, 2)
        for d, w in zip((5, 6), (30, 30)):           # TD-only
            fix(2023, "B", "UNNAMED", None, w, d)
        for d, w in zip((10, 11), (30, 30)):         # unnamed but designated EP04
            fix(2023, "C", "UNNAMED", "EP042023", w, d)
        for d, w in zip((15, 16), (28, 28)):         # unnamed, NO id -> dropped
            fix(2023, "D", "", None, w, d)
        return pd.DataFrame(rows)

    def test_td_inclusive_and_labels(self):
        out = ac.extract_gantt_storms_by_year(self._trop(), min_year=1970)
        self.assertIn(2023, out)
        by_name = {s["name"]: s for s in out[2023]}
        # Named TS present with real ACE (>0).
        self.assertIn("ADRIAN", by_name)
        self.assertGreater(by_name["ADRIAN"]["ace_total"], 0.0)
        # TD-only system (B) -> appears as "TD 0?" via... it has NO atcf -> wait:
        # B has no ATCF and is unnamed -> must be DROPPED (phantom). Only C (has
        # an id) survives as "TD 04E".
        self.assertIn("TD 04E", by_name)
        self.assertEqual(by_name["TD 04E"]["ace_total"], 0.0)  # TD -> 0 ACE
        # Neither "UNNAMED" nor the id-less phantom (D) appears.
        names = set(by_name)
        self.assertNotIn("UNNAMED", names)
        # B (TD-only, no id) and D (blank, no id) are both dropped.
        self.assertEqual(len([n for n in names if n not in
                              ("ADRIAN", "TD 04E")]), 0)


class TestLabelHelpers(unittest.TestCase):
    def test_atcf_short_id(self):
        self.assertEqual(ac.atcf_short_id("EP042023"), "04E")
        self.assertEqual(ac.atcf_short_id("AL092024"), "09L")
        self.assertEqual(ac.atcf_short_id("WP152023"), "15W")
        self.assertIsNone(ac.atcf_short_id("AL992024"))   # invest
        self.assertIsNone(ac.atcf_short_id("AL002024"))   # 00
        self.assertIsNone(ac.atcf_short_id(""))
        self.assertIsNone(ac.atcf_short_id(None))

    def test_short_id_from_storm_num(self):
        self.assertEqual(ac.short_id_from_storm_num(4, "ep"), "04E")
        self.assertEqual(ac.short_id_from_storm_num(9, "al"), "09L")
        self.assertIsNone(ac.short_id_from_storm_num(91, "wp"))   # invest
        self.assertIsNone(ac.short_id_from_storm_num(float("nan"), "ep"))
        self.assertIsNone(ac.short_id_from_storm_num(5, "zz"))    # unknown basin

    def test_designation_label(self):
        self.assertEqual(ac.designation_label("04E", 30), "TD 04E")
        self.assertEqual(ac.designation_label("04E", 50), "TS 04E")
        self.assertEqual(ac.designation_label("04E", 95), "HU 04E")
        self.assertEqual(ac.designation_label("04E", None), "TD 04E")

    def test_is_real_name(self):
        self.assertTrue(ac._is_real_name("ADRIAN"))
        self.assertFalse(ac._is_real_name("UNNAMED"))
        self.assertFalse(ac._is_real_name(""))
        self.assertFalse(ac._is_real_name(None))
        self.assertFalse(ac._is_real_name("#04"))   # parse_bdeck numbered fallback


if __name__ == "__main__":
    unittest.main()
