"""Designated Central Pacific systems on the EP page (2026-07-13).

CPHC numbers its own bcp decks (TD 01C = bcp01<year>), which the EP page's
bep-only sweep never fetched — a 90C that designated would VANISH from the
live layer until IBTrACS provisional backfilled it, even though the
historical basis already includes CP (IBTrACS files CP storms under
BASIN=EP with USA_ATCF_ID "CP##…", e.g. Ioke 2006 = CP01).

THE FIX (ace-core-v0.8.4 + both generators):
- EP config gains ``atcf_patterns_extra`` (the bcp proxy chain); the
  numbered-TC sweep runs once per chain. parse_bdeck (v0.8.3) already keys
  the SID off each row's own basin field, so bcp rows land as
  ``NHC_CP##<year>`` — no collision with same-numbered bep decks.
- ``agency_sid_from_atcf_id`` maps a CP-prefixed USA_ATCF_ID under the EP
  page to the CP-token SID, so the provisional IBTrACS row and the live
  designation collapse onto ONE storm (no UNNAMED ghost twin).
- ``GENESIS###`` in a deck's name column is a placeholder, not a name; a
  designated-but-unnamed storm's relabel ("#01" -> designation) uses the
  storm's OWN letter (01C on the EP page, never 01E).
"""
from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_tracks_plot as gtp  # noqa: E402
from ace_core import (  # noqa: E402
    agency_sid_from_atcf_id,
    current_year_storms,
    merge_and_extract_storms,
    parse_bdeck,
)

EP_CFG_MIN = {"short": "ep", "agency_name": "NHC"}


def _deck_line(basin, num, stamp, name, dev="TD", wind=30):
    return (f"{basin}, {num:02d}, {stamp},   , BEST,   0, 141N, 1521W, "
            f"{wind:3d}, 1004, {dev},  34, NEQ,    0,    0,    0,    0, 1010,"
            f"  150,  40,   0,   0, L,   0,    , 0, 0, {name}, M,\n")


class AgencySidMappingTest(unittest.TestCase):
    def test_cp_id_under_ep_maps_to_cp_sid(self):
        self.assertEqual(
            agency_sid_from_atcf_id("CP012026", EP_CFG_MIN, 2026),
            "NHC_CP012026")

    def test_ep_id_unchanged(self):
        self.assertEqual(
            agency_sid_from_atcf_id("EP042026", EP_CFG_MIN, 2026),
            "NHC_EP042026")

    def test_foreign_basins_still_rejected(self):
        self.assertIsNone(agency_sid_from_atcf_id("WP092026", EP_CFG_MIN, 2026))
        self.assertIsNone(agency_sid_from_atcf_id(
            "CP012026", {"short": "al", "agency_name": "NHC"}, 2026))

    def test_invest_and_wrong_year_still_rejected(self):
        self.assertIsNone(agency_sid_from_atcf_id("CP902026", EP_CFG_MIN, 2026))
        self.assertIsNone(agency_sid_from_atcf_id("CP012025", EP_CFG_MIN, 2026))


class GenesisPlaceholderTest(unittest.TestCase):
    def test_genesis_only_deck_falls_back_to_number(self):
        deck = (_deck_line("CP", 1, "2026071306", "GENESIS001")
                + _deck_line("CP", 1, "2026071312", "GENESIS001"))
        df = parse_bdeck(deck, 2026, dict(EP_CFG_MIN, invest_letter="E"))
        self.assertEqual(set(df["NAME"]), {"#01"})

    def test_real_name_still_wins_over_genesis(self):
        deck = (_deck_line("CP", 1, "2026071306", "GENESIS001")
                + _deck_line("CP", 1, "2026071312", "ONE-C"))
        df = parse_bdeck(deck, 2026, dict(EP_CFG_MIN, invest_letter="E"))
        self.assertEqual(set(df["NAME"]), {"ONE-C"})

    def test_unnamed_cp_designation_relabels_with_its_own_letter(self):
        # "#01" from a GENESIS-only bcp deck must surface as "01C" on the EP
        # page, not "01E" (the page-letter relabel bug class).
        import datetime as dt
        now = dt.datetime.utcnow()
        anchor = now.replace(hour=(now.hour // 6) * 6, minute=0,
                             second=0, microsecond=0)
        rows = [{
            "SID": "NHC_CP012026", "NAME": "#01", "season": 2026,
            "time": anchor - dt.timedelta(hours=6 * (1 - i)),
            "lat": 14.0 + i, "lon": -152.0 + i,
            "wind_kt": 30.0, "pressure_mb": 1004.0,
            "nature": "TS", "ace_nature": "TS",
            "source": "live-NHC", "storm_num": 1,
        } for i in range(2)]
        storms = merge_and_extract_storms(
            pd.DataFrame(), pd.DataFrame(rows),
            dict(EP_CFG_MIN, invest_letter="E"))
        self.assertEqual(storms[0]["name"], "01C")


class AceGanttLetterTest(unittest.TestCase):
    def test_unnamed_cp_designation_gantt_label_is_01c(self):
        # The ACE feed's per-storm gantt must agree with the tracks feed:
        # a young unnamed CP designation reads "TD 01C", never "TD 01E".
        import datetime as dt
        rows = [{
            "SID": "NHC_CP012026", "NAME": "#01", "season": 2026,
            "time": dt.datetime(2026, 7, 13, 6 * i), "lat": 14.0, "lon": -152.0,
            "wind_kt": 30.0, "pressure_mb": 1004.0,
            "nature": "TS", "ace_nature": "TS",
            "source": "live-NHC", "storm_num": 1,
        } for i in range(2)]
        storms = current_year_storms(
            pd.DataFrame(rows), dict(EP_CFG_MIN, invest_letter="E"), 2026)
        self.assertEqual(len(storms), 1)
        self.assertEqual(storms[0]["name"], "TD 01C")

    def test_feed_base_ships_extra_patterns(self):
        import build_feed_base as bfb
        cfg = bfb._poller_cfg("ep")
        self.assertTrue(cfg.get("atcf_patterns_extra"),
                        "EP poller cfg must carry the bcp chain")
        self.assertIn("bcp{nn}{year}.dat", cfg["atcf_patterns_extra"][0][0])
        self.assertEqual(bfb._poller_cfg("al").get("atcf_patterns_extra"), [])


class _FakeHTTP:
    """urlopen stand-in: exact-URL -> deck text; anything else 404s."""

    def __init__(self, decks: dict):
        self.decks = decks
        self.urls = []

    def __call__(self, req, timeout=None):
        url = getattr(req, "full_url", req)
        self.urls.append(url)
        for frag, text in self.decks.items():
            if frag in url:
                body = text.encode()
                resp = io.BytesIO(body)
                resp.status = 200
                resp.read = lambda b=body: b
                resp.__enter__ = lambda s=resp: s
                resp.__exit__ = lambda s, *a: False
                return resp
        raise urllib.error.HTTPError(url, 404, "nf", None, None)


class PatternSetSweepTest(unittest.TestCase):
    def setUp(self):
        import urllib.request
        self._orig = urllib.request.urlopen
        self._orig_invests = gtp.fetch_live_invests
        gtp.fetch_live_invests = lambda *a, **k: pd.DataFrame()

    def tearDown(self):
        import urllib.request
        urllib.request.urlopen = self._orig
        gtp.fetch_live_invests = self._orig_invests

    def test_ep_sweeps_bep_and_bcp(self):
        import urllib.request
        fake = _FakeHTTP({
            "bep012026.dat": _deck_line("EP", 1, "2026071306", "AMANDA", "TS", 45),
            "bcp012026.dat": _deck_line("CP", 1, "2026071306", "ONE-C"),
        })
        urllib.request.urlopen = fake
        df = gtp.fetch_live_season(2026, gtp.BASINS["ep"], "[ep-test]")
        self.assertEqual(set(df["SID"]), {"NHC_EP012026", "NHC_CP012026"})
        self.assertEqual(set(df["NAME"]), {"AMANDA", "ONE-C"})
        self.assertTrue(any("bcp01" in u for u in fake.urls),
                        "bcp chain was never swept")

    def test_al_has_no_extra_sweep(self):
        import urllib.request
        fake = _FakeHTTP({
            "bal012026.dat": _deck_line("AL", 1, "2026071306", "ANDREA", "TS", 40),
        })
        urllib.request.urlopen = fake
        df = gtp.fetch_live_season(2026, gtp.BASINS["al"], "[al-test]")
        self.assertEqual(set(df["SID"]), {"NHC_AL012026"})
        self.assertFalse(any("bcp" in u for u in fake.urls),
                         "AL must not sweep bcp decks")


if __name__ == "__main__":
    unittest.main()
