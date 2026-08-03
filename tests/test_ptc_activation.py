"""Potential Tropical Cyclone (PTC) activation, handoff + marker (ace_core).

A PTC is a DESIGNATED AL/EP/CP system (number 01-49) that NHC is actively
advising on while it is still a pre-genesis disturbance. Its b-deck dev-level
is DB/LO -> the non-tropical "DS" nature, so the normal is_active "tropical"
gate hides it even though NHC has issued a full forecast/advisory + cone +
watches (the AL012026 / "ONE" case, 2026-06-16). These tests pin the durable
fix:

  * ACTIVATION (the mirror of the final-advisory retirement): a designated
    DB/DS system LISTED in a cleanly-fetched CurrentStorms is is_active=True +
    is_ptc=True; absent/None never activates. Invests (90-99) and normal
    tropical-natured TCs are unaffected.
  * IDENTITY: a PTC wears the invest visual identity — marker_type "invest_x"
    — under its REAL designation (atcf_id "01L", zero-padded), NOT the 90L it
    spawned from. It accrues NO ACE and counts as NO category.
  * HANDOFF: the live invest a PTC spawned (b-deck SPAWNINVEST link) is dropped
    so one system is never shown as both 01L-X and 90L-X.
"""
from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

import pandas as pd

# Repo-source-first (see test_invest_ace_guard): test the ace_core under
# review, not a possibly-lagging pip-installed copy.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ace_core"))

from ace_core import (  # noqa: E402
    PTC_CLASSIFICATIONS,
    PTC_NATURES,
    build_global_geojson,
    compute_header_stats,
    merge_and_extract_storms,
    parse_bdeck,
    _SPAWNINVEST_RE,
)

AL_CFG = {"short": "al", "agency_name": "NHC", "invest_letter": "L",
          "ibtracs_file_code": "NA"}
EP_CFG = {"short": "ep", "agency_name": "NHC", "invest_letter": "E"}
WP_CFG = {"short": "wp", "agency_name": "JTWC", "invest_letter": "W"}


def _recent_synoptic(n: int) -> list[dt.datetime]:
    now = dt.datetime.utcnow()
    anchor = now.replace(hour=(now.hour // 6) * 6, minute=0,
                         second=0, microsecond=0)
    return [anchor - dt.timedelta(hours=6 * (n - 1 - i)) for i in range(n)]


def _rows(sid_prefix, num, *, nature, name, n=3, wind=20.0, spawn=None,
          season=2026):
    """n recent 6-hourly live-shaped fixes. ``spawn`` (if given) is recorded
    on the middle fix, like parse_bdeck records a SPAWNINVEST tag."""
    times = _recent_synoptic(n)
    return [{
        "SID": f"{sid_prefix}{num:02d}{season}", "NAME": name, "season": season,
        "time": t, "lat": 26.3 + 0.3 * i, "lon": -100.0 + 0.5 * i,
        "wind_kt": wind, "pressure_mb": 1007.0,
        "nature": nature, "ace_nature": nature,
        "source": f"live-{sid_prefix.split('_')[0]}", "storm_num": num,
        "spawn_invest": (spawn if (spawn is not None and i == 1) else None),
    } for i, t in enumerate(times)]


def _one(rows, cfg, sids):
    storms = merge_and_extract_storms(
        pd.DataFrame(), pd.DataFrame(rows), cfg, nhc_active_sids=sids)
    return storms


def _by_sid(storms, sid):
    return next((s for s in storms if s["sid"] == sid), None)


def _marker_type(storm):
    fc = build_global_geojson([storm])
    ms = [f for f in fc["features"]
          if f["properties"]["kind"] == "active_marker"]
    return ms[0]["properties"]["marker_type"] if ms else None


class PTCActivationTests(unittest.TestCase):
    def test_designated_db_listed_is_active_ptc(self):
        rows = _rows("NHC_AL", 1, nature="DS", name="ONE")
        storms = _one(rows, AL_CFG, {"AL012026": "DB"})
        s = _by_sid(storms, "NHC_AL012026")
        self.assertTrue(s["is_active"])
        self.assertTrue(s["is_ptc"])
        self.assertFalse(s["is_invest"])
        self.assertEqual(s["atcf_id"], "01L")          # REAL designation, padded
        # NUMBER RULE (2026-07-14): a designated PTC (01-89) renders by
        # intensity — the glyph, never the invest X.
        self.assertEqual(_marker_type(s), "hurricane")

    def test_absent_from_currentstorms_stays_invisible(self):
        # CurrentStorms cleanly fetched but empty (lagging adv #1) -> the PTC is
        # NOT activated; it has no marker until NHC lists it.
        rows = _rows("NHC_AL", 1, nature="DS", name="ONE")
        s = _by_sid(_one(rows, AL_CFG, {}), "NHC_AL012026")
        self.assertFalse(s["is_active"])
        self.assertFalse(s["is_ptc"])
        self.assertIsNone(_marker_type(s))

    def test_failed_fetch_none_never_activates(self):
        rows = _rows("NHC_AL", 1, nature="DS", name="ONE")
        s = _by_sid(_one(rows, AL_CFG, None), "NHC_AL012026")
        self.assertFalse(s["is_active"])
        self.assertFalse(s["is_ptc"])

    def test_classification_only_signal(self):
        # b-deck nature is the tropical-ish edge "" but NHC classification says
        # disturbance: classification alone (a dict value) can name the PTC.
        # "PC" is the code NHC uses in the wild for a Potential Tropical
        # Cyclone (AL012026 "One", 2026-06-16).
        rows = _rows("NHC_AL", 3, nature="", name="THREE")
        s = _by_sid(_one(rows, AL_CFG, {"AL032026": "PC"}), "NHC_AL032026")
        self.assertTrue(s["is_ptc"])
        self.assertIn("PC", PTC_CLASSIFICATIONS)

    def test_nhc_ptc_code_means_post_tropical_not_potential(self):
        # NHC's CurrentStorms "PTC" classification means POST-Tropical Cyclone
        # (Genevieve EP072026 advisory 39, systemType "POST-TROPICAL CYCLONE").
        # It must NOT live in the potential-TC synonym set: that collision
        # helped mislabel a decayed former C5 as pre-genesis (2026-08-03
        # global-header bug, 200.59 vs 226.70).
        self.assertNotIn("PTC", PTC_CLASSIFICATIONS)

    def test_legacy_set_membership_still_activates(self):
        # A bare set (the pre-0.8 contract) carries no classification, so the
        # DS NATURE drives activation. Back-compat for old callers.
        rows = _rows("NHC_AL", 1, nature="DS", name="ONE")
        s = _by_sid(_one(rows, AL_CFG, {"AL012026"}), "NHC_AL012026")
        self.assertTrue(s["is_ptc"])
        self.assertEqual(s["atcf_id"], "01L")

    def test_normal_td_is_not_ptc(self):
        # A genuine tropical depression (tropical nature) activates the normal
        # way and is NOT a PTC, even when NHC lists it.
        rows = _rows("NHC_AL", 2, nature="TS", name="TWO", wind=30.0)
        s = _by_sid(_one(rows, AL_CFG, {"AL022026": "TD"}), "NHC_AL022026")
        self.assertTrue(s["is_active"])
        self.assertFalse(s["is_ptc"])
        self.assertIsNone(s["atcf_id"])
        self.assertEqual(_marker_type(s), "hurricane")

    def test_invest_never_becomes_ptc(self):
        # 90-99 are never in CurrentStorms; even if forced in, the <90 gate
        # excludes them — an invest stays an invest_x, never is_ptc.
        rows = _rows("NHC_AL", 94, nature="DS", name="INVEST")
        s = _by_sid(_one(rows, AL_CFG, {"AL942026": "DB"}), "NHC_AL942026")
        self.assertTrue(s["is_invest"])
        self.assertFalse(s["is_ptc"])
        self.assertEqual(_marker_type(s), "invest_x")

    def test_wp_jtwc_exempt(self):
        # JTWC/WP has no CurrentStorms coverage; a DS-natured WP system is not
        # activated as a PTC (it would never be listed).
        rows = _rows("JTWC_WP", 5, nature="DS", name="FIVE")
        s = _by_sid(_one(rows, WP_CFG, {}), "JTWC_WP052026")
        self.assertFalse(s["is_ptc"])


class PTCNoAceNoCategoryTests(unittest.TestCase):
    def test_ptc_accrues_no_ace(self):
        rows = _rows("NHC_AL", 1, nature="DS", name="ONE")
        s = _by_sid(_one(rows, AL_CFG, {"AL012026": "DB"}), "NHC_AL012026")
        self.assertEqual(s["ace"], 0.0)

    def test_windy_ptc_excluded_from_header(self):
        # Even a >34 kt PTC (still DS-natured, NHC-advised pre-genesis) must not
        # inflate named/category counts or season ACE.
        rows = _rows("NHC_AL", 1, nature="DS", name="ONE", wind=45.0)
        storms = _one(rows, AL_CFG, {"AL012026": "DB"})
        h = compute_header_stats(storms)
        self.assertEqual(h["named"], 0)
        self.assertEqual(h["total_ace"], 0.0)


class PTCPostTropicalGuardTests(unittest.TestCase):
    """The GENEVIEVE bug (2026-08-03). A former TC decaying through an
    NHC-advised remnant low matches the PTC signature — designated, listed in
    CurrentStorms, latest fix DB/LO -> "DS" — for the day or two of
    post-tropical advisories at the end of its life. The pre-genesis guard
    (TC history: any TS/SS/ET/MX-natured fix, or accrued ACE) must block the
    promotion, and the header must keep the storm's season ACE either way."""

    @staticmethod
    def _decayed_former_hurricane():
        # TS-natured hurricane-strength history, final fix decayed to a 35 kt
        # remnant low (LO -> "DS") — Genevieve's b-deck shape in miniature.
        rows = _rows("NHC_EP", 7, nature="TS", name="GENEVIEVE", n=4,
                     wind=120.0)
        rows[-1]["nature"] = "DS"
        rows[-1]["ace_nature"] = "DS"
        rows[-1]["wind_kt"] = 35.0
        return rows

    def test_post_tropical_former_tc_never_ptc(self):
        # Even listed in CurrentStorms (NHC's "PTC" = POST-tropical) with a
        # DS-natured last fix, a storm with TC history is never promoted; the
        # plain nature gate governs its decay (DS last fix -> inactive).
        rows = self._decayed_former_hurricane()
        s = _by_sid(_one(rows, EP_CFG, {"EP072026": "PTC"}), "NHC_EP072026")
        self.assertFalse(s["is_ptc"])
        self.assertFalse(s["is_active"])
        self.assertGreater(s["ace"], 0)
        self.assertEqual(s["max_category"], "C4")   # 120 kt peak preserved

    def test_post_tropical_former_tc_counts_in_header(self):
        rows = self._decayed_former_hurricane()
        storms = _one(rows, EP_CFG, {"EP072026": "PTC"})
        s = _by_sid(storms, "NHC_EP072026")
        h = compute_header_stats(storms)
        self.assertEqual(h["named"], 1)
        self.assertEqual(h["cat1plus"], 1)
        self.assertEqual(h["total_ace"], s["ace"])

    def test_ace_history_alone_blocks_promotion(self):
        # Provisional NR-natured history (blank/NR passes the provisional ACE
        # gate, so ACE accrues) with a DS-natured last fix: no TS/SS/ET/MX
        # nature anywhere, but the accrued ACE proves genesis happened.
        rows = _rows("NHC_EP", 8, nature="NR", name="EIGHT", n=4, wind=50.0)
        rows[-1]["nature"] = "DS"
        rows[-1]["ace_nature"] = "DS"
        rows[-1]["wind_kt"] = 30.0
        s = _by_sid(_one(rows, EP_CFG, {"EP082026": "PTC"}), "NHC_EP082026")
        self.assertGreater(s["ace"], 0)
        self.assertFalse(s["is_ptc"])

    def test_genuine_ptc_still_promoted(self):
        # The guard must NOT break real pre-genesis PTCs: an all-DS history
        # has no TC-history nature and no ACE, so promotion still fires.
        rows = _rows("NHC_AL", 1, nature="DS", name="ONE")
        s = _by_sid(_one(rows, AL_CFG, {"AL012026": "DB"}), "NHC_AL012026")
        self.assertTrue(s["is_ptc"])
        self.assertTrue(s["is_active"])

    def test_presentational_ts_fallback_does_not_block_promotion(self):
        # Adversarial-review pin (2026-08-03): the tracks generator's
        # _best_nature wind fallback deliberately types pre-genesis
        # provisional fixes as presentational "TS" while ace_nature stays the
        # raw "NR". TC history must be judged on the ACE nature, so a genuine
        # pre-genesis PTC with such a fill row is still promoted.
        rows = _rows("NHC_AL", 1, nature="DS", name="ONE")
        rows[1]["nature"] = "TS"        # presentational fallback
        rows[1]["ace_nature"] = "NR"    # raw agency truth
        s = _by_sid(_one(rows, AL_CFG, {"AL012026": "PC"}), "NHC_AL012026")
        self.assertTrue(s["is_ptc"])
        self.assertTrue(s["is_active"])

    def test_frontal_origin_et_fix_does_not_block_promotion(self):
        # Adversarial-review pin (2026-08-03): a frontal-origin pre-genesis
        # low carries EX-coded fixes (-> "ET") BEFORE designation. ET is not
        # TC history — the PTC must still be promoted. (A real former TC has
        # TS/SS fixes, so dropping ET loses no Genevieve-class coverage.)
        rows = _rows("NHC_AL", 1, nature="DS", name="ONE")
        rows[0]["nature"] = "ET"
        rows[0]["ace_nature"] = "ET"
        s = _by_sid(_one(rows, AL_CFG, {"AL012026": "PC"}), "NHC_AL012026")
        self.assertTrue(s["is_ptc"])
        self.assertTrue(s["is_active"])

    def test_explicit_pc_classification_beats_provisional_ace(self):
        # Adversarial-review pin (2026-08-03): blank/NR provisional natures
        # at >=34 kt accrue ACE via the provisional loophole, but NHC's
        # explicit "PC" classification is the authoritative pre-genesis
        # statement — the ACE arm yields to it, the system keeps its PTC
        # identity, and the count/ACE split stays consistent with the strip
        # (excluded from named, its ledger ACE still in the total).
        rows = _rows("NHC_AL", 1, nature="", name="ONE", n=4, wind=40.0)
        storms = _one(rows, AL_CFG, {"AL012026": "PC"})
        s = _by_sid(storms, "NHC_AL012026")
        self.assertGreater(s["ace"], 0)     # provisional loophole accrued
        self.assertTrue(s["is_ptc"])
        h = compute_header_stats(storms)
        self.assertEqual(h["named"], 0)
        self.assertEqual(h["total_ace"], s["ace"])

    def test_header_ace_immune_to_wrong_ptc_flag(self):
        # Defense in depth at the header layer: even if a storm ARRIVES
        # wearing is_ptc=True with nonzero ace (a mis-flagging producer, a
        # legacy feed), season ACE must include it — only the named/category
        # COUNTS trust the flag. This is what keeps the baked header equal to
        # the ACE feed's season total under any flag bug.
        storms = [{"sid": "NHC_EP072026", "name": "GENEVIEVE", "ace": 26.117,
                   "max_category": "C5", "is_invest": False, "is_ptc": True,
                   "is_active": True, "start": "2026-07-24T18:00:00"}]
        h = compute_header_stats(storms)
        self.assertEqual(h["total_ace"], 26.117)
        self.assertEqual(h["named"], 0)
        self.assertEqual(h["cat5"], 0)


class PTCInvestHandoffTests(unittest.TestCase):
    def test_spawned_invest_dropped(self):
        # AL01 (PTC) spawned invest AL90; the invest has the identical track.
        # Only 01L should remain.
        al01 = _rows("NHC_AL", 1, nature="DS", name="ONE", spawn=90)
        al90 = _rows("NHC_AL", 90, nature="DS", name="INVEST")
        storms = _one(al01 + al90, AL_CFG, {"AL012026": "DB"})
        ptc = _by_sid(storms, "NHC_AL012026")
        self.assertIsNotNone(ptc)
        self.assertIsNone(_by_sid(storms, "NHC_AL902026"),
                          "the spawned invest must be dropped (handoff)")
        # The PTC carries its spawning invest's sid so the page can read that
        # invest's formation.json (the NHC TWO odds live under the invest).
        self.assertEqual(ptc["spawn_sid"], "NHC_AL902026")

    def test_unlinked_invest_kept(self):
        # An invest with NO spawn link is kept even if its track coincides — the
        # handoff keys on the SPAWNINVEST link only (the invest-guard fixtures
        # prove a coincidence test would mis-drop).
        al01 = _rows("NHC_AL", 1, nature="DS", name="ONE")          # no spawn
        al95 = _rows("NHC_AL", 95, nature="DS", name="INVEST")
        storms = _one(al01 + al95, AL_CFG, {"AL012026": "DB"})
        self.assertIsNotNone(_by_sid(storms, "NHC_AL952026"))

    def test_invest_kept_while_designation_inactive(self):
        # If CurrentStorms still lags (PTC not yet active), the spawned invest
        # stays visible — the handoff only fires once the designation is active.
        al01 = _rows("NHC_AL", 1, nature="DS", name="ONE", spawn=90)
        al90 = _rows("NHC_AL", 90, nature="DS", name="INVEST")
        storms = _one(al01 + al90, AL_CFG, {})   # clean, empty -> PTC inactive
        self.assertIsNotNone(_by_sid(storms, "NHC_AL902026"))


class ParseBdeckSpawnTests(unittest.TestCase):
    SAMPLE = (
        "AL, 01, 2026061512,   , BEST,   0, 250N, 1008W,  15, 1011, DB,   0, "
        "   ,    0,    0,    0,    0,    0,    0,   0,   0,   0,   L,   0,    , "
        "  0,   0, GENESIS001,  ,  0,    ,    0,    0,    0,    0, genesis-num, 001, \n"
        "AL, 01, 2026061518,   , BEST,   0, 257N, 1005W,  15, 1011, DB,   0, "
        "   ,    0,    0,    0,    0, 1013,  150, 120,   0,   0,   L,   0,    , "
        "  0,   0,     INVEST, S,  0,    ,    0,    0,    0,    0, genesis-num, "
        "001, SPAWNINVEST, al712026 to al902026, \n"
    )

    def test_spawn_regex(self):
        # Captures the DESTINATION (the spawned invest), never the source:
        # group 1 = its basin token, group 2 = its 90-99 number.
        m = _SPAWNINVEST_RE.search("SPAWNINVEST, al712026 to al902026,")
        self.assertEqual(m.group(1), "al")
        self.assertEqual(m.group(2), "90")

    def test_parse_captures_spawn_invest(self):
        df = parse_bdeck(self.SAMPLE, 2026, AL_CFG)
        self.assertFalse(df.empty)
        # Every fix of storm 01 carries the storm-level spawn link (90).
        self.assertTrue((df["spawn_invest"] == 90).any())
        # NATURE maps DB -> DS (the PTC signature).
        self.assertTrue((df["nature"] == "DS").all())

    def test_nature_set_membership(self):
        self.assertIn("DS", PTC_NATURES)
        self.assertIn("DB", PTC_NATURES)


if __name__ == "__main__":
    unittest.main()
