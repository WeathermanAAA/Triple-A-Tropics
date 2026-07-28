#!/usr/bin/env python3
"""The aid catalog + the per-storm guidance document builder.

Most of what matters here is what the code REFUSES to say. The guidance page's
value is that it does not overclaim, so these tests are mostly about the three
overclaims that are easy to make by accident:

  * calling a single model's ensemble MEAN a multi-model CONSENSUS;
  * showing a consensus in a JTWC basin, where none exists;
  * rendering a WITHHELD consensus member as merely "absent", which implies it
    did not run rather than that we are not allowed to see it.

Run: ``python -m unittest discover tests``
"""
import datetime as dt
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from guidance import aids  # noqa: E402
from guidance import atcf  # noqa: E402
from guidance import build_guidance as bg  # noqa: E402
from guidance.aids import AidKind, AidTiming  # noqa: E402


def _row(tech="AVNI", tau=0, lat="218N", lon="0651W", vmax="65", mslp="985",
         rad="34", dtg="2026072812", basin="EP", cy=7):
    return (f"{basin}, {cy:02d}, {dtg},   , {tech}, {tau:4d}, {lat}, {lon}, "
            f"{vmax}, {mslp}, XX,  {rad}, NEQ,    0,    0,    0,    0,")


class TestAidKinds(unittest.TestCase):

    def test_ensemble_mean_is_not_consensus(self):
        """THE defect this catalog exists to prevent. AEMN is the GEFS mean -
        one model averaged with itself. Calling it consensus tells the reader
        several independent models agree, which is a different claim."""
        for t in ("AEMN", "AEMI", "GDMN", "GDMI", "CEMN"):
            kind, _ = aids.classify(t)
            self.assertIs(kind, AidKind.ENSEMBLE_MEAN, t)
            self.assertIsNot(kind, AidKind.CONSENSUS, t)

    def test_multi_model_consensus_is_consensus(self):
        for t in ("TVCN", "IVCN", "RVCN", "HCCA", "NNIC"):
            kind, _ = aids.classify(t, "al")
            self.assertIs(kind, AidKind.CONSENSUS, t)

    def test_skill_baseline_is_not_a_forecast(self):
        for t in ("OCD5", "CLP5", "SHF5"):
            kind, _ = aids.classify(t)
            self.assertIs(kind, AidKind.SKILL_BASELINE, t)

    def test_official_and_best(self):
        self.assertIs(aids.classify("OFCL")[0], AidKind.OFFICIAL)
        self.assertIs(aids.classify("BEST")[0], AidKind.BEST)

    def test_gefs_members_are_classified_by_rule_not_by_table(self):
        """31 members; tabling them individually would rot."""
        self.assertIs(aids.classify("AC00")[0], AidKind.ENSEMBLE_MEMBER)
        for n in (1, 7, 30):
            self.assertIs(aids.classify(f"AP{n:02d}")[0],
                          AidKind.ENSEMBLE_MEMBER, n)
        # Out of range / malformed must NOT be swept in.
        self.assertIsNot(aids.classify("AP31")[0], AidKind.ENSEMBLE_MEMBER)
        self.assertIsNot(aids.classify("APXX")[0], AidKind.ENSEMBLE_MEMBER)

    def test_hafs_live_ids_not_the_stale_techlist_ids(self):
        """HFSA/HFSB are the live ids. HAFA/HAFB are in nhc_techlist.dat with
        zero live rows - keying on them would wait forever."""
        self.assertIs(aids.classify("HFSA")[0], AidKind.DYNAMICAL)
        self.assertIs(aids.classify("HFSB")[0], AidKind.DYNAMICAL)
        self.assertIn("HAFS-A", aids.label("HFSA"))


class TestEarlyLate(unittest.TestCase):

    def test_raw_dynamical_runs_are_late(self):
        """Raw model output lands after the forecast deadline, so comparing it
        to the official forecast flatters it - part of its apparent skill is
        hindsight."""
        for t in ("AVNO", "HWRF", "HMON", "HFSA", "HFSB", "CMC", "NVGM",
                  "CTCX", "UKX"):
            self.assertIs(aids.classify(t)[1], AidTiming.LATE, t)

    def test_interpolated_twins_are_early(self):
        for t in ("AVNI", "HWFI", "HMNI", "HFAI", "HFBI", "CMCI", "NVGI",
                  "CTCI", "UKXI"):
            self.assertIs(aids.classify(t)[1], AidTiming.EARLY, t)

    def test_cycle_time_products_are_early(self):
        for t in ("OFCL", "TVCN", "IVCN", "DSHP", "SHIP", "LGEM", "OCD5"):
            self.assertIs(aids.classify(t)[1], AidTiming.EARLY, t)

    def test_suffix_rule_covers_unknown_aids(self):
        """An aid added upstream after this table was written must still be
        classified rather than silently defaulting to one bucket."""
        self.assertIs(aids.classify("ZZZI")[1], AidTiming.EARLY)
        self.assertIs(aids.classify("ZZZ2")[1], AidTiming.EARLY)
        self.assertIs(aids.classify("ZZZZ")[1], AidTiming.LATE)


class TestBasinGate(unittest.TestCase):
    """JTWC-basin decks have never carried official, consensus or statistical
    aids. A consensus there is fabricated, not degraded."""

    def test_consensus_is_refused_in_a_jtwc_basin(self):
        for b in ("wp", "io", "sh"):
            for t in ("TVCN", "IVCN", "RVCN", "HCCA"):
                kind, _ = aids.classify(t, b)
                self.assertIsNot(kind, AidKind.CONSENSUS, f"{t}/{b}")

    def test_same_aid_is_consensus_in_an_nhc_basin(self):
        for b in ("al", "ep", "cp"):
            self.assertIs(aids.classify("TVCN", b)[0], AidKind.CONSENSUS, b)

    def test_capability_tiers(self):
        for b in ("al", "ep", "cp"):
            cap = aids.basin_capability(b)
            self.assertEqual(cap["tier"], "full", b)
            self.assertTrue(cap["has_consensus"] and cap["has_official"]
                            and cap["has_skill_baseline"], b)
        for b in ("wp", "io", "sh"):
            cap = aids.basin_capability(b)
            self.assertEqual(cap["tier"], "ensemble_only", b)
            self.assertFalse(cap["has_consensus"] or cap["has_official"]
                             or cap["has_statistical"]
                             or cap["has_skill_baseline"], b)
            self.assertIn("never carried official", cap["note"])

    def test_unknown_basin_claims_nothing(self):
        cap = aids.basin_capability("zz")
        self.assertEqual(cap["tier"], "unknown")
        self.assertFalse(cap["has_consensus"])


class TestConsensusMembership(unittest.TestCase):

    def test_three_distinct_states(self):
        """present / absent / WITHHELD. Collapsing withheld into absent would
        imply the member did not run, when the truth is that NHC does not ship
        it to the public feed."""
        rows = bg.consensus_membership(["TVCN", "AVNI", "HWFI"], "al")
        self.assertEqual(len(rows), 1)
        by = {m["tech"]: m["state"] for m in rows[0]["members"]}
        self.assertEqual(by["AVNI"], "present")
        self.assertEqual(by["HWFI"], "present")
        self.assertEqual(by["EMXI"], "withheld")   # ECMWF-derived
        self.assertEqual(by["EMNI"], "withheld")
        self.assertEqual(by["EGRI"], "absent")     # simply not in this deck
        self.assertFalse(rows[0]["reproducible"])

    def test_counts_add_up(self):
        rows = bg.consensus_membership(["TVCN", "AVNI"], "ep")
        r = rows[0]
        self.assertEqual(r["n_present"] + r["n_withheld"] + r["n_absent"],
                         len(r["members"]))

    def test_reproducible_only_when_nothing_is_withheld(self):
        rows = bg.consensus_membership(["TVCN"], "al")
        self.assertFalse(rows[0]["reproducible"],
                         "TVCN nominally includes EMXI, which is withheld")

    def test_jtwc_basin_gets_no_membership_at_all(self):
        for b in ("wp", "io", "sh"):
            self.assertEqual(bg.consensus_membership(["TVCN", "AEMN"], b), [],
                             b)

    def test_absent_consensus_aid_is_not_reported(self):
        self.assertEqual(bg.consensus_membership(["AVNI"], "al"), [])


class TestSid(unittest.TestCase):

    def test_agency_prefix_by_basin(self):
        self.assertEqual(bg.sid_for("al", 2, 2026), "NHC_AL022026")
        self.assertEqual(bg.sid_for("ep", 7, 2026), "NHC_EP072026")
        self.assertEqual(bg.sid_for("cp", 92, 2026), "NHC_CP922026")
        self.assertEqual(bg.sid_for("wp", 12, 2026), "JTWC_WP122026")
        self.assertEqual(bg.sid_for("io", 3, 2026), "JTWC_IO032026")


class TestDeckRouting(unittest.TestCase):
    """ftp.nhc.noaa.gov carries ZERO WP/IO/SH decks; UCAR is the only source."""

    def test_nhc_basins_use_nhc(self):
        u = atcf.deck_url("al", 2, 2026, "a")
        self.assertIn("ftp.nhc.noaa.gov", u)
        self.assertTrue(u.endswith("aal022026.dat.gz"))

    def test_jtwc_basins_use_ucar(self):
        u = atcf.deck_url("wp", 12, 2026, "a")
        self.assertIn("hurricanes.ral.ucar.edu", u)
        self.assertTrue(u.endswith("awp122026.dat"), u)

    def test_ucar_bdecks_are_year_nested(self):
        u = atcf.deck_url("wp", 12, 2026, "b")
        self.assertIn("/bdecks_open/2026/bwp122026.dat", u)

    def test_nhc_bdeck_is_flat(self):
        self.assertTrue(atcf.deck_url("ep", 7, 2026, "b")
                        .endswith("/atcf/btk/bep072026.dat"))


class TestBuildDocument(unittest.TestCase):

    def _adeck(self, techs, basin="EP", cy=7):
        lines = []
        for t in techs:
            for tau in (0, 12, 24):
                lines.append(_row(tech=t, tau=tau, basin=basin, cy=cy,
                                  lat=f"{218 + tau}N", vmax=str(65 + tau)))
        return "\n".join(lines)

    def test_nhc_storm_gets_the_full_suite(self):
        text = self._adeck(["OFCL", "OCD5", "TVCN", "AVNI", "AVNO", "HWFI"])
        doc = bg.build_document(text, None, basin="ep", cy=7, year=2026)
        self.assertEqual(doc["capability"]["tier"], "full")
        self.assertEqual(doc["official"], "OFCL")
        self.assertEqual(doc["skill_baseline"], "OCD5")
        self.assertIn("TVCN", doc["consensus_aids"])
        self.assertIn("AVNI", doc["early_aids"])
        self.assertIn("AVNO", doc["late_aids"])
        self.assertTrue(doc["consensus_membership"])

    def test_jtwc_storm_gets_ensembles_only(self):
        """Even when the deck somehow contains a consensus-looking id, the
        document must not claim one."""
        text = self._adeck(["AEMN", "AC00", "AP01", "TVCN"], basin="WP", cy=12)
        doc = bg.build_document(text, None, basin="wp", cy=12, year=2026)
        self.assertEqual(doc["capability"]["tier"], "ensemble_only")
        self.assertEqual(doc["consensus_aids"], [])
        self.assertEqual(doc["consensus_membership"], [])
        self.assertIsNone(doc["official"])
        self.assertIsNone(doc["skill_baseline"])
        self.assertIn("AEMN", doc["ensemble_mean_aids"])
        self.assertIn("AC00", doc["ensemble_members"])

    def test_only_the_latest_cycle_is_published(self):
        old = self._adeck(["AVNI"]).replace("2026072812", "2026072806")
        new = self._adeck(["AVNI", "OFCL"])
        doc = bg.build_document(old + "\n" + new, None, basin="ep", cy=7,
                                year=2026)
        self.assertEqual(doc["init_cycle"], "2026072812")
        self.assertIn("OFCL", doc["present_aids"])

    def test_radii_rows_do_not_triple_a_trace(self):
        """The 50 and 64 kt rows repeat the same position; counting them would
        make every trace three times as long. RAD is part of the primary key so
        they are distinguishable, not deduplicated away."""
        rows = []
        for rad in ("34", "50", "64"):
            rows.append(_row(tech="AVNI", tau=0, rad=rad))
        doc = bg.build_document("\n".join(rows), None, basin="ep", cy=7,
                                year=2026)
        self.assertEqual(len(doc["aids"]["AVNI"]), 1)

    def test_best_track_comes_from_the_bdeck(self):
        b = "\n".join(
            _row(tech="BEST", tau=0, dtg=f"20260728{h:02d}", basin="EP", cy=7)
            for h in (0, 6, 12))
        doc = bg.build_document(self._adeck(["AVNI"]), b, basin="ep", cy=7,
                                year=2026)
        self.assertEqual(len(doc["best_track"]), 3)
        self.assertEqual(doc["best_track"][0]["dtg"], "2026072800")

    def test_sentinels_never_reach_the_document(self):
        text = _row(tech="AVNI", tau=0, vmax="0", mslp="0")
        doc = bg.build_document(text, None, basin="ep", cy=7, year=2026)
        p = doc["aids"]["AVNI"][0]
        self.assertIsNone(p["vmax"])
        self.assertIsNone(p["mslp"])

    def test_zero_position_sentinel_yields_no_track(self):
        text = "\n".join(_row(tech="IVCN", tau=t, lat="0N", lon="0W")
                         for t in (0, 12, 24))
        doc = bg.build_document(text, None, basin="ep", cy=7, year=2026)
        self.assertFalse(doc["aid_meta"]["IVCN"]["has_track"])
        self.assertTrue(doc["aid_meta"]["IVCN"]["has_intensity"])

    def test_document_carries_the_filtered_deck_notice(self):
        doc = bg.build_document(self._adeck(["TVCN", "AVNI"]), None,
                                basin="ep", cy=7, year=2026)
        fd = doc["filtered_deck"]
        self.assertIn("EMXI", fd["withheld"])
        self.assertIn("independently reproducible", fd["note"])

    def test_qc_report_is_carried_for_the_page_to_show(self):
        doc = bg.build_document(self._adeck(["AVNI"]), None, basin="ep", cy=7,
                                year=2026)
        self.assertIn("rows_kept", doc["qc"])

    def test_document_is_json_serializable(self):
        import json
        doc = bg.build_document(self._adeck(["OFCL", "TVCN", "AVNI"]), None,
                                basin="ep", cy=7, year=2026)
        json.dumps(doc)   # must not raise

    def test_empty_deck_yields_no_aids(self):
        doc = bg.build_document("", None, basin="ep", cy=7, year=2026)
        self.assertEqual(doc["present_aids"], [])
        self.assertIsNone(doc["init_cycle"])


class TestDiscovery(unittest.TestCase):

    def test_test_decks_are_excluded(self):
        """Cyclone numbers 80-89 are GSTEST/ATCFTEST fixtures with physically
        absurd values; they must never reach a page."""
        listing = ("aal012026.dat.gz aal852026.dat.gz aep072026.dat.gz "
                   "aep902026.dat.gz aal012025.dat.gz").encode()
        got = bg.discover_storms(2026, opener=lambda url: listing)
        self.assertIn(("al", 1), got)
        self.assertIn(("ep", 7), got)
        self.assertIn(("ep", 90), got, "invests DO get pages")
        self.assertNotIn(("al", 85), got, "test deck must be excluded")
        self.assertNotIn(("al", 1, 2025), got)
        self.assertEqual(len([g for g in got if g[0] == "al"]), 1)


if __name__ == "__main__":
    unittest.main()
