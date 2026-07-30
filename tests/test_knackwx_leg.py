"""The knackwx leg, and the multi-leg conflict resolution it forced.

THE CASE THAT SET THIS. 12W (Dolphin), 2026-07-30 03:16Z. Every other site had
the storm at 145 kt / 915 mb; ours said 140 / 921. The value was not missing —
it was in tcvitals, at an hour the b-deck also covered, and ``prefer_bdeck``
discarded it:

    DTG        b-deck            tcvitals
    29 12Z     130 kt / 934 mb   130 kt / 935 mb
    29 18Z     140 kt / 921 mb   145 kt / 915 mb   <- same position, 5 kt apart
    30 00Z     140 kt / 921 mb   140 kt / 921 mb

The deck had 18Z by 19:22Z on the 29th while tcvitals still stopped at 12Z, so
tcvitals published that hour LATER, and higher.

The dangerous part is not the override, it is what the override drops. A
tcvitals row carries no dev level, so an untyped winner replacing a typed deck
row silently removes the fix from ACE — measured at storm ACE 12.460 -> 2.805
with the track still looking perfectly healthy. ``test_untyped_winner_*`` pins
that.

ace_core is loaded BY PATH: a stale pip-installed copy would make all of this
pass vacuously.
"""
import datetime as dt
import json
import pathlib
import sys
import unittest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PKG = _ROOT / "ace_core"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

import ace_core as ac                                            # noqa: E402
from ace_core import knackwx as kx                               # noqa: E402
from ace_core import tcvitals as tcv                             # noqa: E402
from ace_core import jtwc_live as jl                             # noqa: E402
import pandas as pd                                              # noqa: E402

assert str(_ROOT) in ac.__file__, (
    f"ace_core resolved to {ac.__file__}, not the working tree")

CFG = {"short": "wp", "agency_name": "JTWC"}
NOW = dt.datetime(2026, 7, 30, 3, 16)
SID = "JTWC_WP122026"

PAYLOAD = [
    {"atcf_id": "12W", "storm_name": "DOLPHIN",
     "analysis_time": "2026-07-30T00:00:00.000Z",
     "latitude": 16.4, "longitude": 165.7, "cyclone_nature": "ST",
     "winds": 140, "pressure": 921, "origin_basin": "W", "basin": "WPAC"},
    {"atcf_id": "94W", "storm_name": "INVEST",
     "analysis_time": "2026-07-30T00:00:00.000Z",
     "latitude": 12.9, "longitude": 139.5, "cyclone_nature": "DB",
     "winds": 15, "pressure": 1007, "origin_basin": "W", "basin": "WPAC"},
    {"atcf_id": "07E", "storm_name": "GENEVIEVE",
     "analysis_time": "2026-07-30T00:00:00.000Z",
     "latitude": 19.4, "longitude": -120.4, "cyclone_nature": "HU",
     "winds": 85, "pressure": 970, "origin_basin": "E", "basin": "EPAC"},
]


def _row(source, t, wind, pres=float("nan"), nature="IND", sid=SID,
         status="indeterminate", **extra):
    rec = {"SID": sid, "NAME": "DOLPHIN", "season": 2026, "time": t,
           "lat": 15.8, "lon": 166.8, "wind_kt": float(wind),
           "pressure_mb": float(pres), "nature": nature, "ace_nature": nature,
           "source": source, "storm_num": 12, "atcf_short": "12W",
           "type_status": status, "rmw_nm": None}
    for c in ac.RADII_COLS:
        rec[c] = None
    rec.update(extra)
    return rec


class KnackwxParseTests(unittest.TestCase):
    def setUp(self):
        self.df = kx.parse_knackwx(json.dumps(PAYLOAD), 2026, CFG, now=NOW)

    def test_designated_storm_in_basin_only(self):
        self.assertEqual(len(self.df), 1)
        self.assertEqual(self.df.iloc[0]["SID"], SID)

    def test_invests_are_left_to_the_existing_path(self):
        self.assertNotIn("JTWC_WP942026", set(self.df["SID"]))

    def test_other_basins_filtered(self):
        self.assertNotIn("JTWC_EP072026", set(self.df["SID"]))

    def test_fields(self):
        r = self.df.iloc[0]
        self.assertEqual(r["time"], dt.datetime(2026, 7, 30, 0, 0))
        self.assertEqual(r["wind_kt"], 140.0)
        self.assertEqual(r["pressure_mb"], 921.0)
        self.assertAlmostEqual(r["lat"], 16.4)
        self.assertAlmostEqual(r["lon"], 165.7)
        self.assertEqual(r["source"], kx.KNACKWX_SOURCE)

    def test_self_typing(self):
        """ST -> TS at the source; never waits on the warnings leg."""
        r = self.df.iloc[0]
        self.assertEqual(r["nature"], "TS")
        self.assertEqual(r["ace_nature"], "TS")
        self.assertEqual(r["type_status"], tcv.TYPE_OBSERVED)
        self.assertTrue(tcv.is_resolved(r["ace_nature"]))

    def test_unmapped_dev_level_is_dropped_not_guessed(self):
        p = [dict(PAYLOAD[0], cyclone_nature="ZZ")]
        self.assertTrue(kx.parse_knackwx(json.dumps(p), 2026, CFG,
                                         now=NOW).empty)

    def test_absent_sentinel(self):
        p = [dict(PAYLOAD[0], pressure=-9999.99)]
        df = kx.parse_knackwx(json.dumps(p), 2026, CFG, now=NOW)
        self.assertNotEqual(df.iloc[0]["pressure_mb"],
                            df.iloc[0]["pressure_mb"])          # NaN

    def test_off_synoptic_rejected(self):
        p = [dict(PAYLOAD[0], analysis_time="2026-07-30T03:00:00.000Z")]
        self.assertTrue(kx.parse_knackwx(json.dumps(p), 2026, CFG,
                                         now=NOW).empty)

    def test_future_and_stale_rejected(self):
        future = [dict(PAYLOAD[0], analysis_time="2026-07-31T00:00:00.000Z")]
        stale = [dict(PAYLOAD[0], analysis_time="2026-07-25T00:00:00.000Z")]
        self.assertTrue(kx.parse_knackwx(json.dumps(future), 2026, CFG,
                                         now=NOW).empty)
        self.assertTrue(kx.parse_knackwx(json.dumps(stale), 2026, CFG,
                                         now=NOW).empty)

    def test_garbage_degrades_to_no_fixes(self):
        """A third-party schema change must cost the leg, not the frame."""
        for bad in ("", "null", "{}", "[1,2,3]", "not json",
                    json.dumps([{"atcf_id": "XX"}]),
                    json.dumps([{"atcf_id": "12W"}])):
            self.assertTrue(kx.parse_knackwx(bad, 2026, CFG, now=NOW).empty,
                            bad[:20])

    def test_fetch_reports_failure_honestly(self):
        df, ok, detail = kx.fetch_knackwx(2026, CFG, now=NOW,
                                          getter=lambda u: None)
        self.assertTrue(df.empty)
        self.assertFalse(ok)


class ResolveConflictsTests(unittest.TestCase):
    T18 = dt.datetime(2026, 7, 29, 18)
    T00 = dt.datetime(2026, 7, 30, 0)
    OLD = dt.datetime(2026, 7, 27, 12)

    def test_one_row_per_dtg_always(self):
        df = pd.DataFrame([
            _row("live-JTWC", self.T18, 140, 921, "TS"),
            _row("live-tcvitals", self.T18, 145, 915),
            _row("live-knackwx", self.T18, 142, 918, "TS"),
            _row("live-warning", self.T18, 141),
        ])
        out = ac.resolve_conflicts(df, now=NOW)
        self.assertEqual(len(out), 1)
        self.assertEqual(int((out.groupby(["SID", "time"]).size() > 1).sum()), 0)

    def test_leading_edge_tcvitals_beats_the_deck(self):
        """The 12W case: 145/915 must survive."""
        df = pd.DataFrame([
            _row("live-JTWC", self.T18, 140, 921, "TS"),
            _row("live-tcvitals", self.T18, 145, 915),
        ])
        out = ac.resolve_conflicts(df, now=NOW)
        self.assertEqual(out.iloc[0]["wind_kt"], 145.0)
        self.assertEqual(out.iloc[0]["pressure_mb"], 915.0)

    def test_settled_fixes_go_back_to_the_deck(self):
        """Outside the revision window the deck is post-analysis and wins."""
        df = pd.DataFrame([
            _row("live-JTWC", self.OLD, 40, 1000, "TS"),
            _row("live-tcvitals", self.OLD, 45, 998),
        ])
        out = ac.resolve_conflicts(df, now=NOW)
        self.assertEqual(out.iloc[0]["wind_kt"], 40.0)

    def test_knackwx_outranks_tcvitals_at_the_leading_edge(self):
        df = pd.DataFrame([
            _row("live-tcvitals", self.T00, 140, 921),
            _row("live-knackwx", self.T00, 145, 915, "TS"),
        ])
        out = ac.resolve_conflicts(df, now=NOW)
        self.assertEqual(out.iloc[0]["source"], "live-knackwx")
        self.assertEqual(out.iloc[0]["wind_kt"], 145.0)

    # ---- the ACE-integrity guard ------------------------------------------
    def test_untyped_winner_inherits_the_nature_of_a_typed_peer(self):
        """Without this, an override silently DELETES the fix from ACE."""
        df = pd.DataFrame([
            _row("live-JTWC", self.T18, 140, 921, "TS"),
            _row("live-tcvitals", self.T18, 145, 915),        # nature IND
        ])
        out = ac.resolve_conflicts(df, now=NOW)
        self.assertEqual(out.iloc[0]["wind_kt"], 145.0)
        self.assertEqual(out.iloc[0]["ace_nature"], "TS")
        self.assertTrue(tcv.is_resolved(out.iloc[0]["ace_nature"]))

    def test_untyped_winner_keeps_the_fix_ace_eligible(self):
        df = pd.DataFrame([
            _row("live-JTWC", self.T18, 140, 921, "TS"),
            _row("live-tcvitals", self.T18, 145, 915),
        ])
        out = ac.resolve_conflicts(df, now=NOW)
        pts = [{"time": r.time, "wind_kt": r.wind_kt,
                "ace_nature": r.ace_nature, "storm_num": r.storm_num}
               for r in out.itertuples()]
        self.assertAlmostEqual(ac.storm_ace(pts, "wp", True),
                               round(145 ** 2 / 1e4, 3), places=3)

    def test_nature_enrichment_does_not_invent_one(self):
        df = pd.DataFrame([
            _row("live-JTWC", self.T18, 140, 921),            # also IND
            _row("live-tcvitals", self.T18, 145, 915),
        ])
        out = ac.resolve_conflicts(df, now=NOW)
        self.assertFalse(tcv.is_resolved(out.iloc[0]["ace_nature"]))

    # ---- enrichment, not mixing -------------------------------------------
    def test_pressure_backfilled_when_winds_agree(self):
        df = pd.DataFrame([
            _row("live-tcvitals", self.T00, 140, 921),
            _row("live-warning", self.T00, 140),              # no MSLP
        ])
        out = ac.resolve_conflicts(df, now=NOW)
        self.assertEqual(out.iloc[0]["wind_kt"], 140.0)
        self.assertEqual(out.iloc[0]["pressure_mb"], 921.0)

    def test_pressure_not_stapled_across_disagreeing_intensities(self):
        """915 mb belongs to the 145 kt analysis; a 140 kt winner must not wear it."""
        df = pd.DataFrame([
            _row("live-knackwx", self.T00, 140, float("nan"), "TS"),
            _row("live-tcvitals", self.T00, 145, 915),
        ])
        out = ac.resolve_conflicts(df, now=NOW)
        self.assertEqual(out.iloc[0]["wind_kt"], 140.0)
        self.assertNotEqual(out.iloc[0]["pressure_mb"],
                            out.iloc[0]["pressure_mb"])       # stays NaN

    def test_radii_backfilled_from_an_agreeing_peer(self):
        deck = _row("live-JTWC", self.T00, 140, 921, "TS")
        deck["r34_ne"] = 120
        df = pd.DataFrame([deck, _row("live-knackwx", self.T00, 140, 921, "TS")])
        out = ac.resolve_conflicts(df, now=NOW)
        self.assertEqual(out.iloc[0]["source"], "live-knackwx")
        self.assertEqual(out.iloc[0]["r34_ne"], 120)

    def test_empty_and_missing_columns_pass_through(self):
        self.assertTrue(ac.resolve_conflicts(pd.DataFrame()).empty)
        odd = pd.DataFrame([{"a": 1}])
        self.assertEqual(len(ac.resolve_conflicts(odd)), 1)


class OverlapPartitionTests(unittest.TestCase):
    def test_partitions_exactly(self):
        """prefer_bdeck and _overlap_rows must lose nothing and duplicate nothing."""
        t = [dt.datetime(2026, 7, 29, h) for h in (6, 12, 18)]
        deck = pd.DataFrame([{"SID": SID, "time": t[0]},
                             {"SID": SID, "time": t[1]}])
        other = pd.DataFrame([_row("live-tcvitals", x, 100) for x in t])
        new = tcv.prefer_bdeck(deck, other)
        over = jl._overlap_rows(deck, other)
        self.assertEqual(len(new) + len(over), len(other))
        self.assertEqual(set(new["time"]) & set(over["time"]), set())
        self.assertEqual(set(new["time"]) | set(over["time"]), set(t))

    def test_no_deck_means_no_overlap(self):
        other = pd.DataFrame([_row("live-tcvitals", NOW, 100)])
        self.assertTrue(jl._overlap_rows(None, other).empty)
        self.assertTrue(jl._overlap_rows(pd.DataFrame(), other).empty)


if __name__ == "__main__":
    unittest.main()
