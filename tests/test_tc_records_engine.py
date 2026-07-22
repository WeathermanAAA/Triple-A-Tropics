"""Unit tests for the tc_records engine (parsers + metric rules).

The publish-time validation gate (Tip 870, Wilma deepening boards, Gilbert
888, John duration, Ivan/Ioke ACE) runs against the full datasets inside
generate_tc_records.py / update-tc-records.yml; these tests cover the
rule-level behavior on small synthetic inputs so a refactor that bends a
non-negotiable (synoptic-only sums, trop gating, missing-pressure handling,
dateline distance, genesis attribution) fails fast without the big CSVs.
"""

import datetime as dt
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from tc_records import metrics, sources  # noqa: E402
from tc_records.boards import _rank  # noqa: E402


def fix(t, wind, pres=np.nan, lat=15.0, lon=-40.0, status="HU",
        trop=True, sid="HURDAT2_AL011999", name="TEST", season=1999):
    return {"sid": sid, "atcf": "AL011999", "name": name, "season": season,
            "time": pd.Timestamp(t), "lat": lat, "lon": lon,
            "wind": float(wind), "pres": pres, "status": status,
            "trop": trop, "syn": pd.Timestamp(t).hour in {0, 6, 12, 18}
            and pd.Timestamp(t).minute == 0, "src": "test"}


def frame(rows):
    return pd.DataFrame(rows, columns=sources.CANON_COLS)


class TestHurdat2Parser(unittest.TestCase):
    SAMPLE = "\n".join([
        "AL092004,               IVAN,      4,",
        "20040902, 1800,  , TD, 9.7N,  27.6W,  25, 1009,    0,    0,"
        "    0,    0,    0,    0,    0,    0,    0,    0,    0,    0, -999",
        "20040903, 0000,  , TS, 9.5N,  28.9W,  35, 1005, -999, -999,"
        " -999, -999, -999, -999, -999, -999, -999, -999, -999, -999, -999",
        "20040903, 0315, L, TS, 9.5N,  29.5W,  40, 1003,    0,    0,"
        "    0,    0,    0,    0,    0,    0,    0,    0,    0,    0, -999",
        "20040903, 0600,  , EX, 9.4N, 170.1E,  45, -999,    0,    0,"
        "    0,    0,    0,    0,    0,    0,    0,    0,    0,    0, -999",
    ])

    def parse(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt",
                                         delete=False) as f:
            f.write(self.SAMPLE)
        return sources.parse_hurdat2(Path(f.name))

    def test_fields_and_flags(self):
        df = self.parse()
        self.assertEqual(len(df), 4)
        self.assertEqual(df["sid"].iloc[0], "HURDAT2_AL092004")
        self.assertEqual(df["name"].iloc[0], "IVAN")
        self.assertEqual(df["season"].iloc[0], 2004)
        # W lon negative, E lon positive
        self.assertAlmostEqual(df["lon"].iloc[0], -27.6)
        self.assertAlmostEqual(df["lon"].iloc[3], 170.1)
        # -999 pressure -> NaN, never a value
        self.assertTrue(np.isnan(df["pres"].iloc[3]))
        # the 0315 landfall special row is NOT synoptic
        self.assertEqual(list(df["syn"]), [True, True, False, True])
        # EX is not tropical
        self.assertEqual(list(df["trop"]), [True, True, True, False])

    def test_sums_ignore_special_and_ex_rows(self):
        df = self.parse()
        storms = metrics.compute_storms(df)
        s = storms.iloc[0]
        # ACE: only the 00Z 35 kt fix qualifies (18Z is TD 25 kt, the 40 kt
        # row is non-synoptic, the 45 kt row is EX). House rounding = 3 dp.
        self.assertAlmostEqual(s["ace"], round(35 * 35 / 1e4, 3), places=6)
        # but peak wind may come from the special row (an observation)
        self.assertEqual(s["peak_wind"], 40.0)
        # duration counts synoptic tropical fixes only: 2 -> 0.5 d
        self.assertAlmostEqual(s["dur_tc"], 0.5)


class TestWindowExtremes(unittest.TestCase):
    def test_deepening_windows_exact_pairs_only(self):
        t0 = dt.datetime(2005, 10, 18, 18)
        rows = []
        pres = [975, 946, 892, 882, 878, 880]
        for i, p in enumerate(pres):
            rows.append(fix(t0 + dt.timedelta(hours=6 * i), 120, pres=p))
        storms = metrics.compute_storms(frame(rows))
        s = storms.iloc[0]
        self.assertEqual(s["deep6"], 54.0)    # 946 -> 892
        self.assertEqual(s["deep12"], 83.0)   # 975 -> 892 (exact 12 h pair)
        self.assertEqual(s["deep24"], 97.0)   # 975 -> 878 (exact 24 h pair)

    def test_missing_pressure_never_ranks(self):
        t0 = dt.datetime(1999, 9, 1, 0)
        rows = [fix(t0 + dt.timedelta(hours=6 * i), 100) for i in range(5)]
        storms = metrics.compute_storms(frame(rows))
        s = storms.iloc[0]
        self.assertTrue(np.isnan(s["min_pres"]))
        self.assertTrue(np.isnan(s["deep24"]))
        ranked = _rank([{"value": s["min_pres"], "disp": "", "season": 1999}],
                       reverse=False)
        self.assertEqual(ranked, [])

    def test_wind_rise_and_fall(self):
        t0 = dt.datetime(2007, 8, 1, 0)
        winds = [35, 60, 90, 120, 140, 60, 35, 30]
        rows = [fix(t0 + dt.timedelta(hours=6 * i), w) for i, w in
                enumerate(winds)]
        storms = metrics.compute_storms(frame(rows))
        s = storms.iloc[0]
        self.assertEqual(s["rise24"], 105.0)  # 35 -> 140 (exact 24 h pair)
        self.assertEqual(s["fall24"], 90.0)   # 120 -> 30 (exact 24 h pair)
        self.assertTrue(s["ri"])


class TestMotion(unittest.TestCase):
    def test_dateline_distance_small(self):
        t0 = dt.datetime(2006, 8, 31, 0)
        rows = [fix(t0, 80, lat=20.0, lon=179.8, sid="X", season=2006),
                fix(t0 + dt.timedelta(hours=6), 80, lat=20.0, lon=-179.9,
                    sid="X", season=2006)]
        storms = metrics.compute_storms(frame(rows))
        d = storms.iloc[0]["dist_km"]
        self.assertLess(d, 50.0)   # 0.3 deg lon at 20N, not half the globe
        self.assertGreater(d, 10.0)

    def test_implausible_speed_dropped(self):
        t0 = dt.datetime(1950, 9, 1, 0)
        rows = [fix(t0, 50, lat=30.0, lon=-60.0, sid="Y", season=1950),
                fix(t0 + dt.timedelta(hours=6), 50, lat=30.0, lon=-75.0,
                    sid="Y", season=1950)]  # ~15 deg in 6 h ≈ 117 kt
        storms = metrics.compute_storms(frame(rows))
        self.assertTrue(np.isnan(storms.iloc[0]["max_speed"]))


class TestIdentityAndSeasons(unittest.TestCase):
    def test_genesis_season_attribution_jan_crosser(self):
        rows = [fix(dt.datetime(2005, 12, 30, 12), 45, sid="Z",
                    season=2005),
                fix(dt.datetime(2006, 1, 2, 12), 40, sid="Z", season=2005)]
        storms = metrics.compute_storms(frame(rows))
        self.assertEqual(storms.iloc[0]["season"], 2005)
        pace = metrics.pace_matrices(storms, frame(rows), [2005])
        # the Jan-2006 fix lands at doy 366 (clamped), season 2005
        self.assertGreater(pace["ace"][2005].iloc[-1], 0.0)

    def test_pace_index_leap_aligned(self):
        # Sep 10 must land on the same slot in leap and non-leap seasons
        # (the pace page's month axis assumes the leap calendar).
        leap = metrics._pace_idx(pd.Timestamp("2004-09-10"), 2004)
        nonleap = metrics._pace_idx(pd.Timestamp("2005-09-10"), 2005)
        self.assertEqual(leap, nonleap)
        self.assertEqual(metrics._pace_idx(pd.Timestamp("2005-03-01"), 2005),
                         metrics._pace_idx(pd.Timestamp("2004-03-01"), 2004))
        # pre-March dates are unshifted
        self.assertEqual(metrics._pace_idx(pd.Timestamp("2005-02-28"), 2005),
                         59)

    def test_merge_live_tail_appends_only_newer(self):
        arch = frame([fix(dt.datetime(2026, 7, 20, h), 40,
                          sid="2026200N15140", season=2026)
                      for h in (0, 6, 12)])
        live = frame([fix(dt.datetime(2026, 7, 20, h), 45,
                          sid="JTWC_WP052026", season=2026)
                      for h in (6, 12, 18)])
        merged = sources.merge_live_tail(arch, live)
        self.assertEqual(len(merged), 4)          # 3 archive + 1 new
        tail = merged[merged["time"] == pd.Timestamp("2026-07-20 18:00")]
        # live fix adopted the archive SID (one storm everywhere)
        self.assertEqual(tail["sid"].iloc[0], "2026200N15140")
        # archive wins on overlap: the 06/12Z winds stay 40
        mid = merged[merged["time"] == pd.Timestamp("2026-07-20 12:00")]
        self.assertEqual(mid["wind"].iloc[0], 40.0)


class TestRanking(unittest.TestCase):
    def test_competition_ranks_with_ties(self):
        rows = [{"value": v, "disp": str(v), "season": 2000 + i}
                for i, v in enumerate([10, 9, 9, 8])]
        ranked = _rank(rows, reverse=True)
        self.assertEqual([r["rank"] for r in ranked], [1, 2, 2, 4])


if __name__ == "__main__":
    unittest.main()
