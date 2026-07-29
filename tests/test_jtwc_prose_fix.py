"""The prose (3x) warning as a FIX source, not just a type source.

Background — measured on 12W (Dolphin) at 2026-07-29 19:07Z, when the site was
showing C4/130 kt and the question was where the leading edge was lost:

    b-deck mirror   290600Z   120 kt      13 h old
    ATCG wtpn51     290600Z   120 kt      13 h old   <- the machine-readable half
    tcvitals        291200Z   130 kt       7 h old
    prose wtpn31    291200Z   130 kt       7 h old   <- published FIRST

``merge_slot`` began ``if atcg is None: return None`` and the prose half only
ever contributed nature/dev_label/is_final, so the newest analysis JTWC had
published was unreachable whenever tcvitals lagged a cycle. These tests pin the
fix and, just as importantly, the guards on it — a prose bulletin's timestamp is
ambiguous by itself and its forecast block looks exactly like its analysis
block, so both are one careless regex away from publishing a wrong fix.

ace_core is loaded BY PATH: a stale pip-installed copy shadows the working tree
and would let every one of these pass vacuously.
"""
import datetime as dt
import importlib.util
import pathlib
import sys
import unittest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PKG = _ROOT / "ace_core"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

_spec = importlib.util.spec_from_file_location(
    "_jw_under_test", _PKG / "ace_core" / "jtwc_warnings.py")
jw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jw)


# The real 12W bulletin, trimmed. The forecast block is retained verbatim
# because it is the trap: same line shapes, 10 kt higher.
PROSE_12W = """\
WTPN31 PGTW 291500
MSGID/GENADMIN/JOINT TYPHOON WRNCEN PEARL HARBOR HI//
SUBJ/SUPER TYPHOON 12W (DOLPHIN) WARNING NR 011//
RMKS/
1. SUPER TYPHOON 12W (DOLPHIN) WARNING NR 011
   UPGRADED FROM TYPHOON 12W
   MAX SUSTAINED WINDS BASED ON ONE-MINUTE AVERAGE
    ---
   WARNING POSITION:
   291200Z --- NEAR 15.2N 167.7E
     MOVEMENT PAST SIX HOURS - 310 DEGREES AT 09 KTS
   PRESENT WIND DISTRIBUTION:
   MAX SUSTAINED WINDS - 130 KT, GUSTS 160 KT
   RADIUS OF 064 KT WINDS - 025 NM NORTHEAST QUADRANT
                            015 NM SOUTHEAST QUADRANT
                            015 NM SOUTHWEST QUADRANT
                            025 NM NORTHWEST QUADRANT
   RADIUS OF 050 KT WINDS - 050 NM NORTHEAST QUADRANT
                            040 NM SOUTHEAST QUADRANT
                            040 NM SOUTHWEST QUADRANT
                            050 NM NORTHWEST QUADRANT
   RADIUS OF 034 KT WINDS - 090 NM NORTHEAST QUADRANT
                            070 NM SOUTHEAST QUADRANT
                            070 NM SOUTHWEST QUADRANT
                            090 NM NORTHWEST QUADRANT
   REPEAT POSIT: 15.2N 167.7E
    ---
   FORECASTS:
   12 HRS, VALID AT:
   300000Z --- 16.2N 166.0E
   MAX SUSTAINED WINDS - 140 KT, GUSTS 170 KT
   RADIUS OF 064 KT WINDS - 030 NM NORTHEAST QUADRANT
                            020 NM SOUTHEAST QUADRANT
                            020 NM SOUTHWEST QUADRANT
                            030 NM NORTHWEST QUADRANT
"""

ATCG_12W = """\
WTPN51 PGTW 290900
WARNING    ATCG MIL 12W NWP 260729072523
2026072906 12W DOLPHIN    010  01 300 08 SATL 020
T000 145N 1684E 120 R064 025 NE QD 015 SE QD 015 SW QD 025 NW QD \
R050 040 NE QD 040 SE QD 035 SW QD 040 NW QD \
R034 075 NE QD 070 SE QD 060 SW QD 075 NW QD
"""

NOW = dt.datetime(2026, 7, 29, 19, 7)


class AnalysisSectionTests(unittest.TestCase):
    def test_cuts_at_forecasts(self):
        sec = jw.analysis_section(PROSE_12W)
        self.assertIn("291200Z", sec)
        self.assertNotIn("300000Z", sec)
        self.assertNotIn("140 KT", sec)

    def test_no_forecast_block_keeps_whole_text(self):
        txt = "WARNING POSITION:\n   291200Z --- NEAR 15.2N 167.7E\n"
        self.assertEqual(jw.analysis_section(txt), txt)

    def test_empty(self):
        self.assertEqual(jw.analysis_section(""), "")
        self.assertEqual(jw.analysis_section(None), "")


class ParseProseFixTests(unittest.TestCase):
    def setUp(self):
        self.p = jw.parse_prose(PROSE_12W)

    def test_identity_still_parsed(self):
        self.assertEqual(self.p["atcf_id"], "12W")
        self.assertEqual(self.p["name"], "DOLPHIN")
        self.assertEqual(self.p["warning_nr"], 11)
        self.assertEqual(self.p["dev_label"], "STY")
        self.assertEqual(self.p["nature"], "TS")

    def test_analysis_position_and_wind(self):
        self.assertEqual(self.p["fix_ddhhmm"], "291200")
        self.assertAlmostEqual(self.p["lat"], 15.2)
        self.assertAlmostEqual(self.p["lon"], 167.7)
        self.assertEqual(self.p["wind_kt"], 130.0)
        self.assertEqual(self.p["gust_kt"], 160.0)

    def test_forecast_is_never_read_as_the_analysis(self):
        """THE trap. The +12 h forecast is 140 kt at 16.2N/166.0E."""
        self.assertNotEqual(self.p["wind_kt"], 140.0)
        self.assertNotAlmostEqual(self.p["lat"], 16.2)
        self.assertNotEqual(self.p["fix_ddhhmm"], "300000")

    def test_all_three_radii_thresholds_from_the_analysis(self):
        self.assertEqual(self.p["radii"][64], [25, 15, 15, 25])
        self.assertEqual(self.p["radii"][50], [50, 40, 40, 50])
        self.assertEqual(self.p["radii"][34], [90, 70, 70, 90])

    def test_southern_and_western_hemispheres(self):
        txt = ("SUBJ/TROPICAL CYCLONE 04S (FREDDY) WARNING NR 003//\n"
               "   WARNING POSITION:\n"
               "   051800Z --- NEAR 12.5S 55.4W\n"
               "   MAX SUSTAINED WINDS - 065 KT, GUSTS 080 KT\n")
        p = jw.parse_prose(txt)
        self.assertAlmostEqual(p["lat"], -12.5)
        self.assertAlmostEqual(p["lon"], -55.4)
        self.assertEqual(p["wind_kt"], 65.0)

    def test_bulletin_without_a_fix_returns_none_fields(self):
        txt = "SUBJ/TYPHOON 11W (NOUL) WARNING NR 010//\n"
        p = jw.parse_prose(txt)
        self.assertIsNone(p["fix_ddhhmm"])
        self.assertIsNone(p["lat"])
        self.assertIsNone(p["wind_kt"])
        self.assertEqual(p["radii"], {})
        self.assertEqual(p["atcf_id"], "11W")   # type half still works


class ResolveProseTimeTests(unittest.TestCase):
    def test_resolves_one_cycle_ahead_of_its_anchor(self):
        got = jw.resolve_prose_time("291200", dt.datetime(2026, 7, 29, 6),
                                    now=NOW)
        self.assertEqual(got, dt.datetime(2026, 7, 29, 12))

    def test_month_rollover(self):
        """Bulletin written on the 1st, anchor still in the previous month."""
        got = jw.resolve_prose_time("010000", dt.datetime(2026, 7, 31, 18),
                                    now=dt.datetime(2026, 8, 1, 3))
        self.assertEqual(got, dt.datetime(2026, 8, 1, 0))

    def test_year_rollover(self):
        got = jw.resolve_prose_time("010600", dt.datetime(2025, 12, 31, 18),
                                    now=dt.datetime(2026, 1, 1, 9))
        self.assertEqual(got, dt.datetime(2026, 1, 1, 6))

    def test_no_anchor_is_no_fix(self):
        """The stale-slot defence: an unanchored DDHHMM is never resolved."""
        self.assertIsNone(jw.resolve_prose_time("291200", None, now=NOW))

    def test_future_is_rejected(self):
        self.assertIsNone(jw.resolve_prose_time(
            "301200", dt.datetime(2026, 7, 30, 6), now=NOW))

    def test_too_far_ahead_of_anchor_is_rejected(self):
        """A 2-day gap is a slot mismatch, not a lead."""
        self.assertIsNone(jw.resolve_prose_time(
            "311200", dt.datetime(2026, 7, 29, 6),
            now=dt.datetime(2026, 8, 1, 0)))

    def test_malformed_input(self):
        anchor = dt.datetime(2026, 7, 29, 6)
        for bad in (None, "", "2912", "29120", "abcdef", "991200", "292500"):
            self.assertIsNone(jw.resolve_prose_time(bad, anchor, now=NOW), bad)


class MergeSlotLeadingEdgeTests(unittest.TestCase):
    def setUp(self):
        self.atcg = jw.parse_atcg(ATCG_12W)
        self.prose = jw.parse_prose(PROSE_12W)

    def test_newer_prose_supersedes_the_atcg_numbers(self):
        m = jw.merge_slot(self.atcg, self.prose, now=NOW)
        self.assertEqual(m["fix_source"], "prose")
        self.assertEqual(m["time"], dt.datetime(2026, 7, 29, 12))
        self.assertEqual(m["wind_kt"], 130.0)
        self.assertAlmostEqual(m["lat"], 15.2)
        self.assertAlmostEqual(m["lon"], 167.7)
        self.assertEqual(m["warning_nr"], 11)

    def test_radii_come_from_the_same_hour_as_the_position(self):
        """Never 12Z position with 06Z radii — that wind field never existed."""
        m = jw.merge_slot(self.atcg, self.prose, now=NOW)
        self.assertEqual(m["radii"][34], [90, 70, 70, 90])     # prose, 12Z
        self.assertNotEqual(m["radii"][34], [75, 70, 60, 75])  # ATCG, 06Z

    def test_older_prose_does_not_override(self):
        atcg = dict(self.atcg, time=dt.datetime(2026, 7, 29, 18), wind_kt=140.0)
        m = jw.merge_slot(atcg, self.prose, now=NOW)
        self.assertEqual(m["fix_source"], "atcg")
        self.assertEqual(m["time"], dt.datetime(2026, 7, 29, 18))
        self.assertEqual(m["wind_kt"], 140.0)

    def test_equal_time_does_not_override(self):
        atcg = dict(self.atcg, time=dt.datetime(2026, 7, 29, 12), wind_kt=133.0)
        m = jw.merge_slot(atcg, self.prose, now=NOW)
        self.assertEqual(m["fix_source"], "atcg")
        self.assertEqual(m["wind_kt"], 133.0)

    def test_storm_id_mismatch_blocks_everything(self):
        prose = dict(self.prose, atcf_id="13W")
        m = jw.merge_slot(self.atcg, prose, now=NOW)
        self.assertEqual(m["fix_source"], "atcg")
        self.assertEqual(m["time"], dt.datetime(2026, 7, 29, 6))
        self.assertIsNone(m["nature"])

    def test_atcg_absent_is_still_no_warning(self):
        """No anchor means no resolvable time, so no fix — unchanged."""
        self.assertIsNone(jw.merge_slot(None, self.prose, now=NOW))

    def test_partial_prose_fix_is_not_used(self):
        for missing in ("lat", "lon", "wind_kt"):
            prose = dict(self.prose, **{missing: None})
            m = jw.merge_slot(self.atcg, prose, now=NOW)
            self.assertEqual(m["fix_source"], "atcg", missing)
            self.assertEqual(m["time"], dt.datetime(2026, 7, 29, 6), missing)


class WarningFixLegTests(unittest.TestCase):
    """The warning analysis promoted into parse_bdeck-schema fix rows.

    The property under test is that this leg is PURELY ADDITIVE: it may only
    fill an (SID, hour) that neither the b-deck nor tcvitals reaches. Anything
    else would put two rows at one synoptic time, which is the double-count
    the ACE sum has no defence against.
    """

    @classmethod
    def setUpClass(cls):
        # sys.path[0] is the repo's ace_core (set at module import), so this
        # resolves to the working tree ahead of any pip-installed ace-core.
        import ace_core
        assert str(_ROOT) in ace_core.__file__, (
            f"ace_core resolved to {ace_core.__file__}, not the working tree "
            "— a stale installed copy would make these tests vacuous")
        from ace_core import jtwc_live as jl
        cls.ac, cls.jl = ace_core, jl
        cls.cfg = {"short": "wp", "agency_name": "JTWC"}

    def _warning(self, **kw):
        base = dict(atcf_id="12W", name="DOLPHIN",
                    time=dt.datetime(2026, 7, 29, 12),
                    lat=15.2, lon=167.7, wind_kt=130.0,
                    radii={34: [90, 70, 70, 90], 50: [50, 40, 40, 50],
                           64: [25, 15, 15, 25]},
                    nature="TS", dev_label="STY", warning_nr=11,
                    is_final=False, final_reason=None, fix_source="prose")
        base.update(kw)
        return base

    def test_emits_one_row_in_bdeck_schema(self):
        df = self.jl.warning_fixes([self._warning()], 2026, self.cfg)
        self.assertEqual(len(df), 1)
        r = df.iloc[0]
        self.assertEqual(r["SID"], "JTWC_WP122026")
        self.assertEqual(r["NAME"], "DOLPHIN")
        self.assertEqual(r["atcf_short"], "12W")
        self.assertEqual(r["wind_kt"], 130.0)
        self.assertEqual(r["source"], self.jl.WARNING_SOURCE)
        self.assertNotEqual(r["pressure_mb"], r["pressure_mb"])   # NaN

    def test_carries_all_three_radii_thresholds(self):
        df = self.jl.warning_fixes([self._warning()], 2026, self.cfg)
        r = df.iloc[0]
        self.assertEqual(int(r["r34_ne"]), 90)
        self.assertEqual(int(r["r50_ne"]), 50)
        self.assertEqual(int(r["r64_ne"]), 25)

    def test_type_is_left_for_resolve_fix_types(self):
        """One type-resolution path, not two."""
        from ace_core import tcvitals as tcv
        df = self.jl.warning_fixes([self._warning()], 2026, self.cfg)
        self.assertEqual(df.iloc[0]["nature"], tcv.NATURE_INDETERMINATE)
        self.assertEqual(df.iloc[0]["type_status"], tcv.TYPE_INDETERMINATE)
        out = tcv.resolve_fix_types(df, [self._warning()], now=NOW)
        self.assertEqual(out.iloc[0]["type_status"], tcv.TYPE_OBSERVED)
        self.assertEqual(out.iloc[0]["nature"], "TS")

    def test_off_synoptic_bulletin_never_enters_the_fix_set(self):
        w = self._warning(time=dt.datetime(2026, 7, 29, 14, 30))
        self.assertTrue(self.jl.warning_fixes([w], 2026, self.cfg).empty)

    def test_other_basins_are_filtered_out(self):
        w = self._warning(atcf_id="04S")
        self.assertTrue(self.jl.warning_fixes([w], 2026, self.cfg).empty)

    def test_incomplete_warning_is_skipped(self):
        for missing in ("lat", "lon", "wind_kt", "time"):
            w = self._warning(**{missing: None})
            self.assertTrue(
                self.jl.warning_fixes([w], 2026, self.cfg).empty, missing)

    def test_additive_only_against_tcvitals(self):
        """The double-count guard: same (SID, hour) -> the richer source wins."""
        from ace_core import tcvitals as tcv
        import pandas as pd
        w = self._warning()
        wfx = self.jl.warning_fixes([w], 2026, self.cfg)
        existing = pd.DataFrame([{"SID": "JTWC_WP122026",
                                  "time": dt.datetime(2026, 7, 29, 12)}])
        kept = tcv.prefer_bdeck(existing, wfx)
        self.assertEqual(len(kept), 0)

    def test_extends_when_nothing_else_reaches_the_hour(self):
        from ace_core import tcvitals as tcv
        import pandas as pd
        wfx = self.jl.warning_fixes([self._warning()], 2026, self.cfg)
        existing = pd.DataFrame([{"SID": "JTWC_WP122026",
                                  "time": dt.datetime(2026, 7, 29, 6)}])
        kept = tcv.prefer_bdeck(existing, wfx)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept.iloc[0]["time"], dt.datetime(2026, 7, 29, 12))

    def test_no_duplicate_dtg_after_the_merge(self):
        """End state: exactly one row per (SID, synoptic hour)."""
        from ace_core import tcvitals as tcv
        import pandas as pd
        wfx = self.jl.warning_fixes([self._warning()], 2026, self.cfg)
        bdeck = pd.DataFrame([
            {"SID": "JTWC_WP122026", "time": dt.datetime(2026, 7, 29, h)}
            for h in (0, 6)])
        tcvf = pd.DataFrame([{"SID": "JTWC_WP122026",
                              "time": dt.datetime(2026, 7, 29, 12)}])
        merged = pd.concat(
            [bdeck, tcvf, tcv.prefer_bdeck(tcvf, tcv.prefer_bdeck(bdeck, wfx))],
            ignore_index=True)
        counts = merged.groupby(["SID", "time"]).size()
        self.assertEqual(int((counts > 1).sum()), 0)
        self.assertEqual(len(merged), 3)

    def test_repeated_warnings_for_one_storm_collapse(self):
        w = self._warning()
        df = self.jl.warning_fixes([w, dict(w)], 2026, self.cfg)
        self.assertEqual(len(df), 1)


if __name__ == "__main__":
    unittest.main()
