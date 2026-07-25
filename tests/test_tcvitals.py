"""tcvitals + JTWC-warnings two-leg feed.

Fixtures are VERBATIM live records captured 2026-07-25, not hand-written
approximations — the whole value of these tests is that they pin the real
wire format, including its ugly parts (the doubled dot in tgftp filenames,
the arbitrary line wrap in the final-warning sentence, the generic SUBJ line
when two storms are active).
"""

import datetime as dt
import unittest

import ace_core as ac
from ace_core import jtwc_live as jl
from ace_core import jtwc_warnings as jw
from ace_core import tcvitals as tcv

WP = {
    "short": "wp",
    "agency_name": "JTWC",
    "invest_letter": "W",
}

# --- live captures ---------------------------------------------------------

# combined_tcvitals.2026.dat, JTWC records for 11W. The 0723 pair is the
# unnamed ("ELEVEN") stage, which must relabel to "#11", not keep the cardinal.
TCV_11W = "\n".join([
    "JTWC 11W ELEVEN    20260723 0600 174N 1284E 310 067 1002 1005 0537 13 092 -999 -999 -999 -999 M",
    "JTWC 11W NOUL      20260723 1800 183N 1251E 300 088 0998 1006 0555 18 074 0130 0259 0278 0093 D",
    "JTWC 11W NOUL      20260725 0000 208N 1183E 300 067 0980 1005 0333 36 031 0204 0185 0158 0185 D",
    "JTWC 11W NOUL      20260725 0600 213N 1166E 295 067 0975 1004 0324 38 031 0204 0185 0158 0185 D",
    "JTWC 11W NOUL      20260725 1200 218N 1159E 300 062 0970 1003 0315 41 027 0222 0204 0167 0195 D",
    "JTWC 11W NOUL      20260725 1800 224N 1151E 300 057 0967 1004 0370 43 027 0222 0204 0167 0195 D",
])

# An NHC record (150-col extended form) and a South-Indian JTWC one, to prove
# basin + center routing.
TCV_MIXED = "\n".join([
    "NHC  06E FAUSTO    20260725 1200 190N 1359W 280 062 0971 1010 0426 44 037 0334 0241 0185 0334 D 0111 0074 0074 0130 72 210N 1496W 0056 0037 0028 0065",
    "JTWC 27P NARELLE   20260326 1200 212S 1108E 225 057 0938 1006 0333 59 019 0296 0333 0241 0222 D",
    "JTWC 11W NOUL      20260725 1200 218N 1159E 300 062 0970 1003 0315 41 027 0222 0204 0167 0195 D",
])

# wtpn51.pgtw..txt — ATCG MIL form. Full YYYYMMDDHH stamp + all three radii.
ATCG_11W = """WTPN51 PGTW 251500
WARNING    ATCG MIL 11W NWP 260725133033
2026072512 11W NOUL       010  01 310 08 SATL RADR 040
T000 218N 1159E 080 R064 035 NE QD 030 SE QD 025 SW QD 030 NW QD R050 060 NE QD 055 SE QD 045 SW QD 055 NW QD R034 120 NE QD 110 SE QD 090 SW QD 105 NW QD
T012 230N 1146E 075 R064 020 NE QD 025 SE QD 000 SW QD 000 NW QD
"""

# wtpn31.pgtw..txt — prose form, storm named in SUBJ.
PROSE_11W = """WTPN31 PGTW 251500
MSGID/GENADMIN/JOINT TYPHOON WRNCEN PEARL HARBOR HI//
SUBJ/TYPHOON 11W (NOUL) WARNING NR 010//
RMKS/
1. TYPHOON 11W (NOUL) WARNING NR 010
   01 ACTIVE TROPICAL CYCLONE IN NORTHWESTPAC
   MAX SUSTAINED WINDS BASED ON ONE-MINUTE AVERAGE
   WARNING POSITION:
   251200Z --- NEAR 21.8N 115.9E
   MAX SUSTAINED WINDS - 080 KT, GUSTS 100 KT
//
NNNN
"""

# wtio33 — the generic-SUBJ case. Two storms active, so SUBJ is bare and the
# storm is named ONLY on the numbered body line. Also an out-of-basin slot
# (an Indian Ocean slot carrying a NW Pacific storm), and a final warning
# whose sentence is wrapped mid-phrase.
PROSE_GENERIC_FINAL = """WTIO33 PGTW 202100
MSGID/GENADMIN/JOINT TYPHOON WRNCEN PEARL HARBOR HI//
SUBJ/TROPICAL CYCLONE WARNING//
RMKS/
1. TROPICAL DEPRESSION 32W (TORAJI) WARNING NR 007
   03 ACTIVE TROPICAL CYCLONES IN NORTHWESTPAC
   THE SYSTEM, LOCATED SOUTHEAST OF PHUKHET, THAILAND, HAS TRACKED WEST-
   SOUTHWESTWARD AT 20 KNOTS AND WILL MAKE LANDFALL SHORTLY.
   THIS IS THE FINAL WARNING ON THIS SYSTEM BY THE JOINT TYPHOON
   WRNCEN PEARL HARBOR HI. THE SYSTEM WILL BE CLOSELY MONITORED FOR
   SIGNS OF REGENERATION.
//
NNNN
"""

PROSE_ET_FINAL = """WTPN33 PGTW 290300
MSGID/GENADMIN/JOINT TYPHOON WRNCEN PEARL HARBOR HI//
SUBJ/TYPHOON 25W (NEOGURI) WARNING NR 043//
RMKS/
1. TYPHOON 25W (NEOGURI) WARNING NR 043
   THE SYSTEM HAS BEGUN EXTRATROPICAL TRANSITION (ETT) AND WILL FULLY
   TRANSITION TO A STORM FORCE EXTRATROPICAL LOW WITHIN 12 HOURS.
   THIS IS THE FINAL WARNING ON THIS SYSTEM BY
   THE JOINT TYPHOON WRNCEN PEARL HARBOR HI. THE SYSTEM WILL BE CLOSELY
   MONITORED FOR SIGNS OF REGENERATION.
//
NNNN
"""

# wtpn54 — a LEFTOVER slot. tgftp never clears these; this one held a 2024
# storm while the basin had a live 2026 typhoon in another slot.
ATCG_STALE_2024 = """WTPN54 PGTW 160900
WARNING    ATCG MIL 27W NWP 241116075900
2024111606 27W USAGI      022  02 125 03 SATL RADR SYNP 060
T000 220N 1204E 025
"""

NOW = dt.datetime(2026, 7, 25, 20, 30)


class TestParseTcvitals(unittest.TestCase):
    def test_intensity_snaps_to_the_agency_5kt_grid(self):
        """m/s storage is a lossy round trip; snapping recovers JTWC's own
        value. 43 m/s -> 83.6 kt -> 85 kt, which is Category 2 -- the live
        symptom that started this work."""
        self.assertEqual(tcv._snap_kt(43), 85)
        self.assertEqual(tcv._snap_kt(41), 80)
        self.assertEqual(tcv._snap_kt(38), 75)
        self.assertEqual(tcv._snap_kt(36), 70)
        self.assertEqual(tcv._snap_kt(33), 65)

    def test_noul_18z_is_category_2(self):
        df = tcv.parse_tcvitals(TCV_11W, 2026, WP, center="JTWC")
        last = df[df.time == dt.datetime(2026, 7, 25, 18)].iloc[0]
        self.assertEqual(last.wind_kt, 85.0)
        self.assertEqual(last.pressure_mb, 967.0)
        self.assertEqual(ac.sshs_class(last.wind_kt), "C2")

    def test_km_fields_convert_to_nautical_miles(self):
        """Verified field-by-field against the 11W b-deck at 2026072512:
        R34 222/204/167/195 km == 120/110/90/105 nm, RMW 27 km == 15 nm."""
        df = tcv.parse_tcvitals(TCV_11W, 2026, WP, center="JTWC")
        r = df[df.time == dt.datetime(2026, 7, 25, 12)].iloc[0]
        self.assertEqual([r.r34_ne, r.r34_se, r.r34_sw, r.r34_nw],
                         [120, 110, 90, 105])
        self.assertEqual(r.rmw_nm, 15)

    def test_50kt_and_64kt_radii_are_absent_not_zero(self):
        """JTWC records are 95-column record type 1: R34 only. Reporting 0
        would claim a measured 'no extent' we do not have."""
        df = tcv.parse_tcvitals(TCV_11W, 2026, WP, center="JTWC")
        r = df.iloc[-1]
        for col in ("r50_ne", "r50_se", "r50_sw", "r50_nw",
                    "r64_ne", "r64_se", "r64_sw", "r64_nw"):
            self.assertIsNone(r[col], col)

    def test_missing_radii_sentinel_is_not_a_measurement(self):
        """-999 quadrants (weak/early system) must not become a real 0.

        Asserted with isna rather than ``is None`` because pandas widens a
        mixed None/int column to float64 and turns the Nones into NaN -- the
        same coercion that made a None nature dangerous. Here it is harmless
        (both read as 'no data'), and it matches parse_bdeck's own behaviour.
        """
        import pandas as pd
        df = tcv.parse_tcvitals(TCV_11W, 2026, WP, center="JTWC")
        r = df[df.time == dt.datetime(2026, 7, 23, 6)].iloc[0]
        self.assertTrue(pd.isna(r.r34_ne))
        self.assertFalse(r.r34_ne == 0)

    def test_sid_matches_parse_bdeck(self):
        """The whole integration rests on schema identity with parse_bdeck."""
        df = tcv.parse_tcvitals(TCV_11W, 2026, WP, center="JTWC")
        self.assertEqual(set(df.SID), {"JTWC_WP112026"})

    def test_unnamed_cardinal_is_not_treated_as_a_name(self):
        df = tcv.parse_tcvitals(TCV_11W, 2026, WP, center="JTWC")
        early = df[df.time == dt.datetime(2026, 7, 23, 6)].iloc[0]
        self.assertEqual(early.NAME, "#11")

    def test_center_and_basin_routing(self):
        """NHC records and other-basin JTWC records must not leak into wp."""
        df = tcv.parse_tcvitals(TCV_MIXED, 2026, WP, center="JTWC")
        self.assertEqual(list(df.SID), ["JTWC_WP112026"])

    def test_southern_hemisphere_letters_collapse_to_one_token(self):
        sh = {"short": "sh", "agency_name": "JTWC"}
        df = tcv.parse_tcvitals(TCV_MIXED, 2026, sh, center="JTWC")
        self.assertEqual(list(df.SID), ["JTWC_SH272026"])
        self.assertEqual(df.iloc[0].lat, -21.2)

    def test_reparsing_is_idempotent(self):
        once = tcv.parse_tcvitals(TCV_11W, 2026, WP, center="JTWC")
        twice = tcv.parse_tcvitals(TCV_11W + "\n" + TCV_11W, 2026, WP,
                                   center="JTWC")
        self.assertEqual(len(once), len(twice))


class TestIndeterminateNeverCounts(unittest.TestCase):
    """The regression that matters most.

    An unresolved fix carries the sentinel nature "IND". Using None here was
    silently broken: pandas coerces a None into a column beside a string to
    float NaN, nature_eligible maps NaN -> "", and the provisional escape
    hatch ACCEPTS "" -- so every untyped fix would have accrued ACE.
    """

    def test_sentinel_fails_the_ace_gate_in_every_basin(self):
        for basin in ("wp", "al", "ep"):
            for provisional in (True, False):
                self.assertFalse(
                    ac.nature_eligible(tcv.NATURE_INDETERMINATE, basin,
                                       provisional),
                    f"{basin} provisional={provisional}")

    def test_blank_and_nan_would_have_passed(self):
        """Pins WHY the sentinel exists: the values it replaced are accepted."""
        self.assertTrue(ac.nature_eligible("", "wp", provisional=True))
        self.assertTrue(ac.nature_eligible(float("nan"), "wp",
                                           provisional=True))

    def test_unresolved_fixes_accrue_no_ace(self):
        df = tcv.parse_tcvitals(TCV_11W, 2026, WP, center="JTWC")
        self.assertEqual(ac.storm_ace(df.to_dict("records"), "wp"), 0.0)

    def test_parser_never_writes_a_null_nature(self):
        df = tcv.parse_tcvitals(TCV_11W, 2026, WP, center="JTWC")
        for n in df.ace_nature:
            self.assertTrue(isinstance(n, str) and n,
                            f"null nature leaked: {n!r}")

    def test_is_resolved_rejects_every_null_shape(self):
        for bad in (None, float("nan"), "", "IND", "ind"):
            self.assertFalse(tcv.is_resolved(bad), repr(bad))
        self.assertTrue(tcv.is_resolved("TS"))


class TestWarnings(unittest.TestCase):
    def test_atcg_carries_all_three_radii(self):
        """This is what tcvitals cannot give us."""
        r = jw.parse_atcg(ATCG_11W)
        self.assertEqual(r["atcf_id"], "11W")
        self.assertEqual(r["wind_kt"], 80.0)
        self.assertEqual(r["time"], dt.datetime(2026, 7, 25, 12))
        self.assertEqual(r["radii"][34], [120, 110, 90, 105])
        self.assertEqual(r["radii"][50], [60, 55, 45, 55])
        self.assertEqual(r["radii"][64], [35, 30, 25, 30])

    def test_forecast_rows_are_not_analysis(self):
        r = jw.parse_atcg(ATCG_11W)
        self.assertEqual(r["lat"], 21.8)     # T000, not T012's 23.0
        self.assertEqual(r["lon"], 115.9)

    def test_dev_level_from_subj(self):
        p = jw.parse_prose(PROSE_11W)
        self.assertEqual((p["atcf_id"], p["dev_label"], p["nature"]),
                         ("11W", "TY", "TS"))
        self.assertFalse(p["is_final"])

    def test_generic_subj_falls_back_to_the_body_line(self):
        """With 2+ storms active JTWC drops the specific SUBJ. Losing the type
        exactly when the basin is busiest is not acceptable."""
        p = jw.parse_prose(PROSE_GENERIC_FINAL)
        self.assertEqual(p["atcf_id"], "32W")
        self.assertEqual(p["name"], "TORAJI")
        self.assertEqual(p["dev_label"], "TD")
        self.assertEqual(p["nature"], "TS")

    def test_final_warning_survives_arbitrary_line_wrapping(self):
        self.assertEqual(jw.detect_final(PROSE_GENERIC_FINAL),
                         (True, "inland"))
        self.assertEqual(jw.detect_final(PROSE_ET_FINAL),
                         (True, "extratropical"))
        self.assertEqual(jw.detect_final(PROSE_11W), (False, None))

    def test_regeneration_boilerplate_is_not_a_reason(self):
        """Every final warning ends with 'MONITORED FOR SIGNS OF
        REGENERATION'; treating it as signal would mislabel all of them."""
        ok, reason = jw.detect_final(PROSE_ET_FINAL)
        self.assertTrue(ok)
        self.assertEqual(reason, "extratropical")

    def test_unrecognised_dev_level_is_indeterminate_not_tropical(self):
        self.assertEqual(jw.classify_dev_level("SUBJ/SOMETHING NEW//"),
                         (None, None))

    def test_formation_alert_is_not_a_warning(self):
        n, label = jw.classify_dev_level(
            "TROPICAL CYCLONE FORMATION ALERT (INVEST 92W)")
        self.assertIsNone(n)
        self.assertEqual(label, "TCFA")

    def test_stale_slot_is_rejected(self):
        """tgftp never clears a slot; a 2024 storm must not resurrect."""
        stale = jw.parse_atcg(ATCG_STALE_2024)
        self.assertEqual(stale["time"].year, 2024)
        self.assertEqual(jw.select_current([stale], now=NOW), [])

    def test_current_slot_is_kept(self):
        live = jw.parse_atcg(ATCG_11W)
        self.assertEqual(len(jw.select_current([live], now=NOW)), 1)

    def test_future_dated_bulletin_is_rejected(self):
        live = jw.parse_atcg(ATCG_11W)
        early = dt.datetime(2026, 7, 20, 0, 0)
        self.assertEqual(jw.select_current([live], now=early), [])

    def test_merge_refuses_to_splice_two_storms(self):
        atcg = jw.parse_atcg(ATCG_11W)
        other = jw.parse_prose(PROSE_ET_FINAL)      # 25W
        merged = jw.merge_slot(atcg, other)
        self.assertEqual(merged["atcf_id"], "11W")
        self.assertIsNone(merged["nature"])
        self.assertFalse(merged["is_final"])

    def test_merge_pairs_matching_storm(self):
        merged = jw.merge_slot(jw.parse_atcg(ATCG_11W),
                               jw.parse_prose(PROSE_11W))
        self.assertEqual(merged["dev_label"], "TY")
        self.assertEqual(merged["nature"], "TS")
        self.assertEqual(merged["radii"][64], [35, 30, 25, 30])


class TestJoin(unittest.TestCase):
    def _fixes(self):
        return tcv.parse_tcvitals(TCV_11W, 2026, WP, center="JTWC")

    def _warning(self, **over):
        w = jw.merge_slot(jw.parse_atcg(ATCG_11W), jw.parse_prose(PROSE_11W))
        w.update(over)
        return [w]

    def test_exact_synoptic_match_is_observed(self):
        out = tcv.resolve_fix_types(self._fixes(), self._warning(), now=NOW)
        r = out[out.time == dt.datetime(2026, 7, 25, 12)].iloc[0]
        self.assertEqual(r.type_status, tcv.TYPE_OBSERVED)
        self.assertEqual(r.ace_nature, "TS")

    def test_earlier_fixes_inside_the_warning_sequence_are_warned(self):
        """Distinct from 'observed': coverage inferred from warning NR, not a
        bulletin read for that hour."""
        out = tcv.resolve_fix_types(self._fixes(), self._warning(), now=NOW)
        r = out[out.time == dt.datetime(2026, 7, 23, 18)].iloc[0]
        self.assertEqual(r.type_status, tcv.TYPE_WARNED)
        self.assertEqual(r.ace_nature, "TS")

    def test_fixes_before_the_first_warning_stay_indeterminate(self):
        """A low warning number cannot vouch for fixes days earlier."""
        out = tcv.resolve_fix_types(self._fixes(),
                                    self._warning(warning_nr=2), now=NOW)
        r = out[out.time == dt.datetime(2026, 7, 23, 6)].iloc[0]
        self.assertEqual(r.type_status, tcv.TYPE_INDETERMINATE)
        self.assertFalse(tcv.is_resolved(r.ace_nature))

    def test_next_cycle_is_carried_and_counts(self):
        """The 18Z fix exists before the 18Z warning is issued. Carrying one
        cycle is what keeps real-time ACE from lagging every single cycle."""
        out = tcv.resolve_fix_types(self._fixes(), self._warning(), now=NOW)
        r = out[out.time == dt.datetime(2026, 7, 25, 18)].iloc[0]
        self.assertEqual(r.type_status, tcv.TYPE_CARRIED)
        self.assertEqual(r.ace_nature, "TS")

    def test_carry_can_be_switched_off(self):
        out = tcv.resolve_fix_types(self._fixes(), self._warning(), now=NOW,
                                    count_carried=False)
        r = out[out.time == dt.datetime(2026, 7, 25, 18)].iloc[0]
        self.assertEqual(r.type_status, tcv.TYPE_CARRIED)
        self.assertFalse(tcv.is_resolved(r.ace_nature))

    def test_final_warning_stops_ace(self):
        """The documented ACE stop condition. Fixes after a final warning are
        'ended' and accrue nothing, however strong the wind."""
        out = tcv.resolve_fix_types(self._fixes(),
                                    self._warning(is_final=True), now=NOW)
        r = out[out.time == dt.datetime(2026, 7, 25, 18)].iloc[0]
        self.assertEqual(r.type_status, tcv.TYPE_ENDED)
        self.assertFalse(tcv.is_resolved(r.ace_nature))
        self.assertEqual(ac.storm_ace([r.to_dict()], "wp"), 0.0)

    def test_no_warning_leaves_everything_indeterminate(self):
        """Type leg down: fixes still exist (so intensity is not stale) but
        none of them counts."""
        out = tcv.resolve_fix_types(self._fixes(), [], now=NOW)
        self.assertEqual(set(out.type_status), {tcv.TYPE_INDETERMINATE})
        self.assertEqual(ac.storm_ace(out.to_dict("records"), "wp"), 0.0)

    def test_summary_counts_indeterminate_honestly(self):
        """A warning early in its sequence cannot vouch for the whole track;
        the fixes it does not cover must show up as indeterminate, not be
        folded silently into the eligible count."""
        out = tcv.resolve_fix_types(self._fixes(),
                                    self._warning(warning_nr=2), now=NOW)
        s = tcv.type_summary(out)
        self.assertEqual(s["ace_eligible"],
                         sum(1 for n in out.ace_nature if tcv.is_resolved(n)))
        self.assertGreater(s[tcv.TYPE_INDETERMINATE], 0)
        self.assertLess(s["ace_eligible"], len(out))

    def test_ace_counts_only_resolved_fixes(self):
        out = tcv.resolve_fix_types(self._fixes(), self._warning(), now=NOW)
        expected = sum(ac.fix_increment(r.wind_kt)
                       for _, r in out.iterrows()
                       if tcv.is_resolved(r.ace_nature) and r.wind_kt >= 34)
        self.assertAlmostEqual(ac.storm_ace(out.to_dict("records"), "wp"),
                               ac.round_ace(expected), places=3)


class TestPreferBdeck(unittest.TestCase):
    BDECK = "\n".join([
        "WP, 11, 2026072512,   , BEST,   0, 218N, 1159E,  80,  970, TY,  34, NEQ,  120,  110,   90,  105, 1003,  170,  15,   0,  20,   W,   0,    ,   0,   0,       NOUL, D,",
        "WP, 11, 2026072512,   , BEST,   0, 218N, 1159E,  80,  970, TY,  64, NEQ,   35,   30,   25,   30, 1003,  170,  15,   0,  20,   W,   0,    ,   0,   0,       NOUL, D,",
    ])

    def test_bdeck_fixes_are_not_overwritten(self):
        bd = ac.parse_bdeck(self.BDECK, 2026, WP)
        tv = tcv.parse_tcvitals(TCV_11W, 2026, WP, center="JTWC")
        kept = tcv.prefer_bdeck(bd, tv)
        self.assertNotIn(dt.datetime(2026, 7, 25, 12), set(kept.time))
        self.assertIn(dt.datetime(2026, 7, 25, 18), set(kept.time))

    def test_bdeck_retains_its_64kt_radii(self):
        """The reason b-deck wins: it carries thresholds tcvitals cannot."""
        bd = ac.parse_bdeck(self.BDECK, 2026, WP)
        self.assertEqual(bd.iloc[0].r64_ne, 35)

    def test_no_bdeck_keeps_everything(self):
        import pandas as pd
        tv = tcv.parse_tcvitals(TCV_11W, 2026, WP, center="JTWC")
        self.assertEqual(len(tcv.prefer_bdeck(pd.DataFrame(), tv)), len(tv))

    def test_coverage_reports_the_extension(self):
        bd = ac.parse_bdeck(self.BDECK, 2026, WP)
        tv = tcv.parse_tcvitals(TCV_11W, 2026, WP, center="JTWC")
        rep = tcv.coverage_report(bd, tcv.prefer_bdeck(bd, tv))
        self.assertEqual(rep["JTWC_WP112026"]["extends_hours"], 6.0)


class TestGuardedPoller(unittest.TestCase):
    """Per-source isolation, offline (``getter`` is injected)."""

    def test_dead_source_yields_nothing_and_does_not_raise(self):
        df, results = jl.fetch_tcvitals(2026, WP, getter=lambda u: None)
        self.assertTrue(df.empty)
        self.assertTrue(all(not r.ok for r in results))

    def test_html_error_page_is_not_parsed_as_data(self):
        """Several of these hosts answer a missing file with a 200 + HTML."""
        html = "<!DOCTYPE HTML><html><head><title>301</title></head></html>"
        df, _ = jl.fetch_tcvitals(2026, WP, getter=lambda u: html)
        self.assertTrue(df.empty)

    def test_season_source_short_circuits_the_cycle_sweep(self):
        seen = []

        def getter(url):
            seen.append(url)
            return TCV_11W if "combined_tcvitals" in url else None

        df, results = jl.fetch_tcvitals(2026, WP, getter=getter)
        self.assertFalse(df.empty)
        self.assertEqual(len(seen), 1)
        self.assertTrue(results[0].ok)

    def test_cycle_sweep_backfills_when_season_file_is_down(self):
        def getter(url):
            return None if "combined_tcvitals" in url else TCV_11W

        df, results = jl.fetch_tcvitals(2026, WP, now=NOW, getter=getter)
        self.assertFalse(df.empty)
        self.assertEqual(df.SID.nunique(), 1)
        names = {r.name: r for r in results}
        self.assertFalse(names["ucar-season"].ok)
        self.assertTrue(names["nomads-cycles"].ok)

    def test_watermark_bounds_the_backfill_sweep(self):
        """Never-miss must not mean re-fetching the whole retention window
        every run."""
        wide, narrow = [], []

        def mk(sink):
            def g(url):
                if "combined_tcvitals" in url:
                    return None
                sink.append(url)
                return None
            return g

        jl.fetch_tcvitals(2026, WP, now=NOW, getter=mk(wide))
        jl.fetch_tcvitals(2026, WP, now=NOW,
                          watermark=NOW - dt.timedelta(hours=12),
                          getter=mk(narrow))
        self.assertLess(len(narrow), len(wide))

    def test_legs_fail_independently(self):
        """Type leg down must not take intensity down with it."""
        def getter(url):
            return TCV_11W if "combined_tcvitals" in url else None

        out = jl.poll_jtwc(2026, WP, now=NOW, getter=getter)
        self.assertFalse(out["fixes"].empty)
        self.assertEqual(out["warnings"], [])
        self.assertEqual(set(out["fixes"].type_status),
                         {tcv.TYPE_INDETERMINATE})

    def test_intensity_leg_down_still_reports_status(self):
        out = jl.poll_jtwc(2026, WP, now=NOW, getter=lambda u: None)
        self.assertTrue(out["fixes"].empty)
        self.assertTrue(any(not s.ok for s in out["sources"]))


class TestEndOfFeed(unittest.TestCase):
    def test_feed_ending_does_not_freeze_or_invent_a_fix(self):
        """JTWC stops tcvitals at ET / inland. The storm must age out on its
        real last timestamp, never carry forward at its last intensity."""
        df = tcv.parse_tcvitals(TCV_11W, 2026, WP, center="JTWC")
        much_later = dt.datetime(2026, 8, 1, 0, 0)
        out = tcv.resolve_fix_types(df, [], now=much_later)
        self.assertEqual(out.time.max(), dt.datetime(2026, 7, 25, 18))
        self.assertEqual(len(out), len(df))


if __name__ == "__main__":
    unittest.main()


class TestLeadWindow(unittest.TestCase):
    """The scope bound. Without it the season file silently back-fills
    January into a live render — 73 fixes across 18 storms on a real WP run."""

    def _getter(self, url):
        return TCV_11W if "combined_tcvitals" in url else None

    def test_old_fixes_are_not_published(self):
        out = jl.poll_jtwc(2026, WP, now=NOW, getter=self._getter)
        oldest = out["fixes"].time.min()
        self.assertGreaterEqual(
            oldest, NOW - dt.timedelta(hours=jl.LEAD_WINDOW_H))
        self.assertGreater(out["outside_lead_window"], 0)

    def test_leading_edge_survives_the_bound(self):
        out = jl.poll_jtwc(2026, WP, now=NOW, getter=self._getter)
        self.assertIn(dt.datetime(2026, 7, 25, 18), set(out["fixes"].time))

    def test_bound_is_reported_not_silent(self):
        out = jl.poll_jtwc(2026, WP, now=NOW, getter=self._getter)
        self.assertIsInstance(out["outside_lead_window"], int)

    def test_bound_can_be_widened_for_an_offline_backfill(self):
        out = jl.poll_jtwc(2026, WP, now=NOW, lead_window_h=0,
                           getter=self._getter)
        self.assertEqual(out["outside_lead_window"], 0)
        self.assertIn(dt.datetime(2026, 7, 23, 6), set(out["fixes"].time))
