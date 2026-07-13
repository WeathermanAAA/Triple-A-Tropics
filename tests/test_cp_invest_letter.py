"""A storm's rendered ATCF designation must carry ITS OWN basin letter,
not the page basin's.

THE 2026-07-13 BUG: the EP page (whose extent covers the Central Pacific)
correctly discovered CPHC invests 90C/91C and gave them CP-prefixed SIDs
(NHC_CP902026) and names ("90C") — but merge_and_extract_storms rebuilt
the invest-X label (`atcf_id`) from the PAGE basin's singular
invest_letter ("E" for EP), so the home map and EP tracks page labeled
them "90E"/"91E". The b-decks are unambiguous: only bcp902026.dat exists,
never bep902026.dat.

THE FIX (ace-core-v0.8.3, mirrored nowhere — this is the shared package
both writers install): the trailing letter is derived from the storm's
OWN SID basin token (NHC_CP… -> "C"), with the page invest_letter kept
only as the fallback for token-less (IBTrACS-style) SIDs. parse_bdeck
likewise keys the SID + invest fallback name off each deck row's own
basin field ("CP, 90, …"), a byte-identical no-op while every fetched
deck token equals the page basin.
"""
from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ace_core import (  # noqa: E402
    build_global_geojson,
    merge_and_extract_storms,
    parse_bdeck,
)

EP_CFG = {"short": "ep", "agency_name": "NHC", "invest_letter": "E",
          "invest_letters": ["E", "C"]}


def _invest_rows(sid: str, name: str, num: int, lon: float):
    now = dt.datetime.utcnow()
    anchor = now.replace(hour=(now.hour // 6) * 6, minute=0,
                         second=0, microsecond=0)
    return [{
        "SID": sid, "NAME": name, "season": 2026,
        "time": anchor - dt.timedelta(hours=6 * (1 - i)),
        "lat": 14.0 + i, "lon": lon + i,
        "wind_kt": 25.0, "pressure_mb": 1006.0,
        "nature": "DS", "ace_nature": "DS",
        "source": "live-knackwx", "storm_num": num,
    } for i in range(2)]


class CpInvestLetterTest(unittest.TestCase):
    def _storms(self, live_rows):
        return merge_and_extract_storms(
            pd.DataFrame(), pd.DataFrame(live_rows), EP_CFG)

    def test_cp_invest_keeps_its_c(self):
        storms = self._storms(
            _invest_rows("NHC_CP902026", "90C", 90, -152.0))
        self.assertEqual(len(storms), 1)
        self.assertTrue(storms[0]["is_invest"])
        self.assertEqual(storms[0]["atcf_id"], "90C",
                         "CP invest on the EP page must label 90C, not 90E")

    def test_ep_invest_still_e(self):
        storms = self._storms(
            _invest_rows("NHC_EP962026", "96E", 96, -120.0))
        self.assertEqual(storms[0]["atcf_id"], "96E")

    def test_geojson_designation_is_the_c_label(self):
        # The home map renders properties.designation — the field the bug
        # actually surfaced through.
        storms = self._storms(
            _invest_rows("NHC_CP912026", "91C", 91, -158.0))
        fc = build_global_geojson(storms)
        markers = [f for f in fc["features"]
                   if f["properties"]["kind"] == "active_marker"]
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["properties"]["marker_type"], "invest_x")
        self.assertEqual(markers[0]["properties"]["designation"], "91C")

    def test_tokenless_sid_falls_back_to_page_letter(self):
        # IBTrACS-style SIDs carry no agency basin token; the page
        # invest_letter fallback must survive for them.
        storms = self._storms(
            _invest_rows("2026190N14210", "INVEST", 93, -130.0))
        self.assertEqual(storms[0]["atcf_id"], "93E")

    def test_parse_bdeck_cp_deck_keeps_cp_sid_and_name(self):
        # A bcp deck parsed under the EP page cfg: the row's own "CP" field
        # must drive the SID + invest fallback name.
        deck = (
            "CP, 90, 2026071306,   , BEST,   0, 141N, 1521W,  25, 1006, DB,"
            "  34, NEQ,    0,    0,    0,    0,\n"
            "CP, 90, 2026071312,   , BEST,   0, 143N, 1524W,  25, 1005, DB,"
            "  34, NEQ,    0,    0,    0,    0,\n"
        )
        df = parse_bdeck(deck, 2026, EP_CFG)
        self.assertFalse(df.empty)
        self.assertEqual(set(df["SID"]), {"NHC_CP902026"})
        self.assertEqual(set(df["NAME"]), {"90C"})

    def test_spawninvest_dedup_is_letter_aware(self):
        # An ACTIVE EP designation spawned from 90E must retire 90E — and
        # ONLY 90E. An unrelated Central Pacific 90C sharing the page keeps
        # rendering (the letter-blind number match dropped both).
        now = dt.datetime.utcnow()
        anchor = now.replace(hour=(now.hour // 6) * 6, minute=0,
                             second=0, microsecond=0)
        designated = [{
            "SID": "NHC_EP012026", "NAME": "ONE-E", "season": 2026,
            "time": anchor - dt.timedelta(hours=6 * (1 - i)),
            "lat": 15.0 + i, "lon": -120.0 + i,
            "wind_kt": 30.0, "pressure_mb": 1004.0,
            "nature": "TS", "ace_nature": "TS",
            "source": "live-NHC", "storm_num": 1,
            "spawn_invest": 90, "spawn_invest_letter": "E",
        } for i in range(2)]
        rows = (designated
                + _invest_rows("NHC_EP902026", "90E", 90, -121.0)
                + _invest_rows("NHC_CP902026", "90C", 90, -152.0))
        storms = self._storms(rows)
        by_sid = {s["sid"]: s for s in storms}
        self.assertNotIn("NHC_EP902026", by_sid,
                         "the spawned 90E must be retired by its designation")
        self.assertIn("NHC_CP902026", by_sid,
                      "unrelated 90C must survive an ep902026 SPAWNINVEST")
        self.assertIn("NHC_EP012026", by_sid)

    def test_spawninvest_letterless_falls_back_to_number_match(self):
        # Legacy producers (old poller feeds) tag only the number; both
        # same-numbered invests then drop — exactly the pre-letter behavior.
        now = dt.datetime.utcnow()
        anchor = now.replace(hour=(now.hour // 6) * 6, minute=0,
                             second=0, microsecond=0)
        designated = [{
            "SID": "NHC_EP012026", "NAME": "ONE-E", "season": 2026,
            "time": anchor - dt.timedelta(hours=6 * (1 - i)),
            "lat": 15.0 + i, "lon": -120.0 + i,
            "wind_kt": 30.0, "pressure_mb": 1004.0,
            "nature": "TS", "ace_nature": "TS",
            "source": "live-NHC", "storm_num": 1,
            "spawn_invest": 90,
        } for i in range(2)]
        rows = (designated
                + _invest_rows("NHC_EP902026", "90E", 90, -121.0)
                + _invest_rows("NHC_CP902026", "90C", 90, -152.0))
        storms = self._storms(rows)
        sids = {s["sid"] for s in storms}
        self.assertNotIn("NHC_EP902026", sids)
        self.assertNotIn("NHC_CP902026", sids)

    def test_parse_bdeck_spawninvest_captures_letter(self):
        deck = (
            "EP, 01, 2026071306,   , BEST,   0, 141N, 1201W,  30, 1004, TD,"
            "  34, NEQ,    0,    0,    0,    0, 1010,  150,  40,   0,   0,"
            " L,   0,    , 0, 0, ONE-E, M, 0, , 0, 0, 0, 0,"
            " genesis-num, 001, SPAWNINVEST, ep012026 to ep902026,\n"
        )
        df = parse_bdeck(deck, 2026, EP_CFG)
        self.assertFalse(df.empty)
        self.assertEqual(int(df.iloc[0]["spawn_invest"]), 90)
        self.assertEqual(df.iloc[0]["spawn_invest_letter"], "E")

    def test_parse_bdeck_ep_deck_unchanged(self):
        # The no-op guarantee: an ordinary bep deck keeps its EP SID and
        # numbered-TC "#NN" fallback exactly as before.
        deck = (
            "EP, 04, 2026071306,   , BEST,   0, 141N, 1121W,  45, 1000, TS,"
            "  34, NEQ,   40,   30,   20,   30,\n"
        )
        df = parse_bdeck(deck, 2026, EP_CFG)
        self.assertEqual(set(df["SID"]), {"NHC_EP042026"})
        self.assertEqual(set(df["NAME"]), {"#04"})


if __name__ == "__main__":
    unittest.main()
