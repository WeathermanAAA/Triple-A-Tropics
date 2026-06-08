"""ATCF wind-radii (34/50/64 kt by quadrant) in parse_bdeck + the tracks feed.

A b-deck repeats each observation up to 3x, once per wind-radius threshold
(34/50/64 kt). The pre-radii parser kept ONLY the first (blank/0/34 kt) row of
each fix and dropped the 50/64 kt duplicates. parse_bdeck now ACCUMULATES the
quadrant radii from every threshold row of the same fix into 12 columns
(r34_ne..r64_nw) while leaving every other field - and the ACE math, NATURE
handling, and (storm, timestamp) dedup - byte-identical. merge_and_extract_storms
then emits a compact additive per-fix ``radii`` dict
(``{"34":[ne,se,sw,nw],...}``) that the two new CycloLab plots (track-history =
latest fix's radii; cumulative wind-swath = radii at every fix) consume.

Fixtures are REAL-shaped ATCF b-deck lines, including the live Two-E (EP02 2026)
deck the work order live-verified against. The column layout under test:
    col 11 = RAD (34|50|64), col 12 = WINDCODE (NEQ|AAA),
    cols 13-16 = RAD1..RAD4 (nm).
"""
from __future__ import annotations

import json
import unittest

import pandas as pd

from ace_core import (
    RADII_COLS,
    RADII_QUADS,
    RADII_THRESHOLDS,
    merge_and_extract_storms,
    parse_bdeck,
)

SEASON = 2026

WP_CFG = {"short": "wp", "agency_name": "JTWC", "invest_letter": "W"}
EP_CFG = {"short": "ep", "agency_name": "NHC", "invest_letter": "E"}


# A real-shaped WP best-track with the full menu of radii cases:
#   00Z: NEQ across all three thresholds 34/50/64 -> accumulate
#   06Z: NEQ 34 only -> 50/64 absent (omitted in feed)
#   12Z: AAA symmetric 34 -> single radius replicated to all four quadrants
#   18Z: NEQ 34 with a blank SW quadrant -> that quadrant is a real 0
#   00Z+1: pre-radii row (RAD blank) -> NO radii at all (key omitted)
WP_DECK = """\
WP, 05, 2026060700,   , BEST,   0, 150N, 1300E,  85,  960, TY,  34, NEQ,  120,  100,   80,   90, 1004,  200,  40,   0,   0,   D,   0,    ,   0,   0,        SYNTHO, M,
WP, 05, 2026060700,   , BEST,   0, 150N, 1300E,  85,  960, TY,  50, NEQ,   70,   60,   40,   50, 1004,  200,  40,   0,   0,   D,
WP, 05, 2026060700,   , BEST,   0, 150N, 1300E,  85,  960, TY,  64, NEQ,   40,   30,   20,   25, 1004,  200,  40,   0,   0,   D,
WP, 05, 2026060706,   , BEST,   0, 152N, 1295E,  75,  970, TY,  34, NEQ,  110,   90,   70,   80, 1004,  200,  40,   0,   0,   D,
WP, 05, 2026060712,   , BEST,   0, 154N, 1290E,  55,  985, TS,  34, AAA,  100,    0,    0,    0, 1004,  200,  40,   0,   0,   D,
WP, 05, 2026060718,   , BEST,   0, 156N, 1285E,  45,  995, TS,  34, NEQ,   60,   50,    0,   40, 1004,  200,  40,   0,   0,   D,
WP, 05, 2026060800,   , BEST,   0, 158N, 1280E,  30, 1004, TD,   0,    ,    0,    0,    0,    0, 1004,  200,  40,   0,   0,   D,
"""


# The live Two-E (EP02 2026) best-track captured 2026-06-07: invest -> TD,
# only the terminal 18Z fix carries a 34-kt NEQ row, and its quadrants are all
# zero (a present-but-zero threshold, distinct from absent).
TWO_E_DECK = """\
EP, 02, 2026060518,   , BEST,   0, 142N, 1046W,  20, 1009, DB,   0,    ,    0,    0,    0,    0, 1011,  180, 120,   0,   0,   E,   0,    ,   0,   0,     INVEST, S,
EP, 02, 2026060600,   , BEST,   0, 140N, 1042W,  20, 1009, DB,   0,    ,    0,    0,    0,    0, 1011,  180, 120,   0,   0,   E,   0,    ,   0,   0,     INVEST, S,
EP, 02, 2026060606,   , BEST,   0, 139N, 1034W,  20, 1009, DB,   0,    ,    0,    0,    0,    0, 1011,  180, 120,   0,   0,   E,   0,    ,   0,   0,     INVEST, S,
EP, 02, 2026060612,   , BEST,   0, 140N, 1027W,  20, 1008, DB,   0,    ,    0,    0,    0,    0, 1010,  180, 120,  30,   0,   E,   0,    ,   0,   0,     INVEST, S,
EP, 02, 2026060618,   , BEST,   0, 143N, 1020W,  20, 1008, DB,   0,    ,    0,    0,    0,    0, 1010,  180, 120,  30,   0,   E,   0,    ,   0,   0,     INVEST, S,
EP, 02, 2026060700,   , BEST,   0, 146N, 1011W,  25, 1007, DB,   0,    ,    0,    0,    0,    0, 1010,  180,  90,  35,   0,   E,   0,    ,   0,   0,     INVEST, S,
EP, 02, 2026060706,   , BEST,   0, 151N, 1006W,  30, 1007, DB,   0,    ,    0,    0,    0,    0, 1009,  180,  90,  30,   0,   E,   0,    ,   0,   0,     INVEST, S,
EP, 02, 2026060712,   , BEST,   0, 154N, 1002W,  30, 1005, TD,   0,    ,    0,    0,    0,    0, 1008,  300,  90,  40,   0,   E,   0,    ,   0,   0,        TWO, M,
EP, 02, 2026060718,   , BEST,   0, 156N, 1000W,  30, 1005, TD,  34, NEQ,    0,    0,    0,    0, 1008,  220,  80,  40,   0,   E,   0,    ,   0,   0,        TWO, M,
"""


def _by_time(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values("time").reset_index(drop=True)


def _radii_cols(row) -> dict:
    return {c: row[c] for c in RADII_COLS}


class TestRadiiColumns(unittest.TestCase):
    """Column-level accumulation in parse_bdeck."""

    def setUp(self):
        self.df = _by_time(parse_bdeck(WP_DECK, SEASON, WP_CFG))

    def test_one_fix_per_observation(self):
        # 7 lines collapse to 5 fixes (the 50/64 rows of 00Z merge into one).
        self.assertEqual(len(self.df), 5)
        self.assertEqual(
            list(self.df["time"].dt.strftime("%Y%m%d%H")),
            ["2026060700", "2026060706", "2026060712",
             "2026060718", "2026060800"],
        )

    def test_radii_columns_present(self):
        for c in RADII_COLS:
            self.assertIn(c, self.df.columns)
        self.assertEqual(len(RADII_COLS), 12)

    def test_neq_accumulates_all_thresholds(self):
        r = self.df.iloc[0]  # 00Z
        self.assertEqual([r.r34_ne, r.r34_se, r.r34_sw, r.r34_nw],
                         [120, 100, 80, 90])
        self.assertEqual([r.r50_ne, r.r50_se, r.r50_sw, r.r50_nw],
                         [70, 60, 40, 50])
        self.assertEqual([r.r64_ne, r.r64_se, r.r64_sw, r.r64_nw],
                         [40, 30, 20, 25])

    def test_absent_thresholds_are_nan(self):
        # 06Z has a 34 kt row but no 50/64 -> those columns stay absent.
        r = self.df.iloc[1]
        self.assertEqual([r.r34_ne, r.r34_se, r.r34_sw, r.r34_nw],
                         [110, 90, 70, 80])
        for c in ("r50_ne", "r50_se", "r50_sw", "r50_nw",
                  "r64_ne", "r64_se", "r64_sw", "r64_nw"):
            self.assertTrue(pd.isna(r[c]), f"{c} should be absent (NaN/None)")

    def test_aaa_replicates_single_radius(self):
        # 12Z is AAA symmetric -> the one radius fills all four quadrants.
        r = self.df.iloc[2]
        self.assertEqual([r.r34_ne, r.r34_se, r.r34_sw, r.r34_nw],
                         [100, 100, 100, 100])

    def test_blank_quadrant_is_real_zero(self):
        # 18Z NEQ has a blank SW cell -> 0 (no extent), NOT absent.
        r = self.df.iloc[3]
        self.assertEqual([r.r34_ne, r.r34_se, r.r34_sw, r.r34_nw],
                         [60, 50, 0, 40])

    def test_no_radii_row_leaves_all_absent(self):
        # 00Z+1 pre-radii row (RAD blank) -> every radii column absent.
        r = self.df.iloc[4]
        for c in RADII_COLS:
            self.assertTrue(pd.isna(r[c]), f"{c} should be absent")


class TestExistingFieldsUnchanged(unittest.TestCase):
    """Regression: every NON-radii column is byte-identical to what the
    pre-radii parser produced. We reconstruct the old parser's exact
    per-fix field set inline and assert equality, so a future edit that
    perturbs wind/nature/dedup is caught here, not in production."""

    OLD_COLS = ["SID", "NAME", "season", "time", "lat", "lon", "wind_kt",
                "pressure_mb", "nature", "ace_nature", "source", "storm_num"]

    def _old_parse(self, text: str, season: int, cfg: dict) -> list[dict]:
        """A faithful copy of the PRE-radii parse loop (keep first blank/0/34
        row per fix, skip 50/64, emit the 12 documented columns)."""
        import datetime as dt
        from ace_core import STATUS_TO_NATURE, SIX_HOURLY, _parse_atcf_latlon

        def _is_nan(v):
            import math
            return isinstance(v, float) and math.isnan(v)

        name_by_storm = {}
        for line in text.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 11:
                continue
            try:
                storm_num = int(parts[1]); tech = parts[4]
                name_col = parts[27] if len(parts) > 27 else ""
            except (IndexError, ValueError):
                continue
            if tech != "BEST":
                continue
            if name_col and name_col not in {"", "NAMELESS", "INVEST"}:
                name_by_storm[storm_num] = name_col

        seen = set()
        rows = []
        for line in text.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 11:
                continue
            try:
                storm_num = int(parts[1]); tstamp = parts[2]; tech = parts[4]
                lat_raw = parts[6]; lon_raw = parts[7]; vmax = parts[8]
                mslp = parts[9]; devlvl = parts[10]
                rad = parts[11] if len(parts) > 11 else ""
            except (IndexError, ValueError):
                continue
            if tech != "BEST":
                continue
            if rad not in ("", "0", "34"):
                continue
            key = (storm_num, tstamp)
            if key in seen:
                continue
            seen.add(key)
            try:
                t = dt.datetime.strptime(tstamp, "%Y%m%d%H")
            except ValueError:
                continue
            if t.hour not in SIX_HOURLY:
                continue
            try:
                vmax_f = float(vmax) if vmax else float("nan")
            except ValueError:
                vmax_f = float("nan")
            try:
                mslp_f = float(mslp) if mslp and mslp != "0" else float("nan")
            except ValueError:
                mslp_f = float("nan")
            ll = _parse_atcf_latlon(lat_raw, lon_raw)
            if ll is None:
                continue
            lat, lon = ll
            devlvl_u = (devlvl or "").strip().upper()
            nature = STATUS_TO_NATURE.get(devlvl_u, "")
            if not nature:
                nature = "TS" if (vmax and not _is_nan(vmax_f) and vmax_f > 0) else "DS"
            if storm_num >= 90:
                fallback_name = f"{storm_num}{cfg.get('invest_letter', '')}"
            else:
                fallback_name = f"#{storm_num:02d}"
            rows.append({
                "SID": f"{cfg['agency_name']}_{cfg['short'].upper()}"
                       f"{storm_num:02d}{season}",
                "NAME": name_by_storm.get(storm_num, fallback_name),
                "season": season, "time": t, "lat": lat, "lon": lon,
                "wind_kt": vmax_f, "pressure_mb": mslp_f, "nature": nature,
                "ace_nature": nature, "source": f"live-{cfg['agency_name']}",
                "storm_num": storm_num,
            })
        return rows

    def _assert_old_fields_match(self, text, cfg):
        new = _by_time(parse_bdeck(text, SEASON, cfg))
        old = pd.DataFrame(self._old_parse(text, SEASON, cfg))
        old = old.sort_values("time").reset_index(drop=True)
        self.assertEqual(len(new), len(old))
        for col in self.OLD_COLS:
            self.assertIn(col, new.columns)
            # equal_nan so float NaN (wind/pressure) compares equal.
            for i in range(len(new)):
                a, b = new.iloc[i][col], old.iloc[i][col]
                if isinstance(a, float) and isinstance(b, float):
                    self.assertTrue(
                        (a == b) or (pd.isna(a) and pd.isna(b)),
                        f"{col}[{i}]: {a!r} != {b!r}")
                else:
                    self.assertEqual(a, b, f"{col}[{i}]")

    def test_wp_existing_fields_byte_identical(self):
        self._assert_old_fields_match(WP_DECK, WP_CFG)

    def test_two_e_existing_fields_byte_identical(self):
        self._assert_old_fields_match(TWO_E_DECK, EP_CFG)


class TestFeedRadiiShape(unittest.TestCase):
    """The additive per-fix ``radii`` key in the tracks feed."""

    def setUp(self):
        live = parse_bdeck(WP_DECK, SEASON, WP_CFG)
        self.storms = merge_and_extract_storms(pd.DataFrame(), live, WP_CFG)
        self.assertEqual(len(self.storms), 1)
        self.points = self.storms[0]["points"]

    def test_point_keeps_existing_keys(self):
        # Additive: existing per-fix keys still present and unchanged.
        p0 = self.points[0]
        for k in ("t", "lat", "lon", "wind_kt", "pressure_mb", "cls", "nature"):
            self.assertIn(k, p0)

    def test_full_threshold_fix_emits_all_three(self):
        p0 = self.points[0]  # 00Z
        self.assertEqual(p0["radii"], {
            "34": [120, 100, 80, 90],
            "50": [70, 60, 40, 50],
            "64": [40, 30, 20, 25],
        })
        # int values only - no floats bloat the polled feed.
        for arr in p0["radii"].values():
            self.assertTrue(all(isinstance(v, int) for v in arr))

    def test_absent_threshold_keys_omitted(self):
        p1 = self.points[1]  # 06Z: 34 only
        self.assertEqual(set(p1["radii"].keys()), {"34"})
        self.assertEqual(p1["radii"]["34"], [110, 90, 70, 80])

    def test_aaa_fix_in_feed(self):
        p2 = self.points[2]  # 12Z AAA
        self.assertEqual(p2["radii"], {"34": [100, 100, 100, 100]})

    def test_blank_quadrant_zero_in_feed(self):
        p3 = self.points[3]  # 18Z blank SW
        self.assertEqual(p3["radii"]["34"], [60, 50, 0, 40])

    def test_no_radii_key_when_no_data(self):
        p4 = self.points[4]  # 00Z+1 pre-radii row
        self.assertNotIn("radii", p4)

    def test_feed_is_json_serializable(self):
        # Whole storms list must round-trip through json (no NaN/np types).
        s = json.dumps(self.storms)
        self.assertIn('"radii"', s)
        self.assertNotIn("NaN", s)


class TestTwoELiveShape(unittest.TestCase):
    """The live Two-E deck: only the terminal 34-kt fix carries radii, and its
    quadrants are a present-but-zero 34 (distinct from absent)."""

    def setUp(self):
        live = parse_bdeck(TWO_E_DECK, SEASON, EP_CFG)
        self.storm = merge_and_extract_storms(pd.DataFrame(), live, EP_CFG)[0]
        self.points = self.storm["points"]

    def test_storm_carries_vmax_pmin_ace(self):
        # The new plot legend reads these straight off the storm entry.
        for k in ("peak_wind_kt", "peak_pressure_mb", "ace"):
            self.assertIn(k, self.storm)
        self.assertEqual(self.storm["ace"], 0.0)  # invest-life / sub-TS

    def test_only_terminal_fix_has_radii(self):
        with_radii = [p for p in self.points if "radii" in p]
        self.assertEqual(len(with_radii), 1)
        self.assertEqual(with_radii[0]["t"], "2026-06-07T18:00:00")

    def test_present_but_zero_threshold_kept(self):
        last = self.points[-1]
        self.assertEqual(last["radii"], {"34": [0, 0, 0, 0]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
