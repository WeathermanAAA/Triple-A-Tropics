#!/usr/bin/env python3
"""Ensemble paintball ingest (``guidance.build_ensemble``).

Two findings cost real effort to establish and are the whole point of this
file, because both fail SILENTLY into plausible-looking output:

  * the compressed-BUFR de-interleave (subset-minor, not member-major), and
  * ECMWF's storm identifier disagreeing with the agency's.

Run: ``python -m unittest discover tests``
"""
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from guidance import build_ensemble as be  # noqa: E402


class TestDemux(unittest.TestCase):
    """These messages are COMPRESSED with one subset per member, and eccodes
    returns the full array for every key regardless of ``extractSubset`` -
    that key silently does nothing, handing back identical arrays for every
    subset. The layout is subset-minor: value[occurrence*n_subsets + member].
    """

    def test_subset_minor_layout(self):
        # 3 members x 4 occurrences, laid out subset-minor.
        arr = [10, 20, 30,   11, 21, 31,   12, 22, 32,   13, 23, 33]
        self.assertEqual(be._demux(arr, 0, 3), [10, 11, 12, 13])
        self.assertEqual(be._demux(arr, 1, 3), [20, 21, 22, 23])
        self.assertEqual(be._demux(arr, 2, 3), [30, 31, 32, 33])

    def test_member_major_would_be_wrong(self):
        """The naive read. It does not error - it yields a track that jitters
        by whole degrees between 6-hourly steps, which is why smoothness is
        the check that distinguishes them."""
        arr = [10, 20, 30, 11, 21, 31, 12, 22, 32, 13, 23, 33]
        member_major = arr[0:4]
        self.assertNotEqual(member_major, be._demux(arr, 0, 3))

    def test_leading_skip(self):
        """lat/lon carry one analysis value before the per-step pairs."""
        arr = [99,  10, 20,  11, 21]
        self.assertEqual(be._demux(arr, 0, 2, skip=1), [10, 11])


class TestClean(unittest.TestCase):

    def test_range_is_checked_in_output_units(self):
        """BUFR ships Pa (96800) and the site speaks hPa. Scaling first and
        then testing Pa bounds rejected EVERY reading and silently emptied the
        whole pressure field - it decoded to all-None without any error."""
        self.assertEqual(be._clean(96800, 800, 1100, 0.01), 968.0)
        self.assertIsNone(be._clean(96800, 80000, 110000, 0.01),
                          "Pa bounds against an hPa value must reject - this "
                          "is the bug, pinned so it cannot come back")

    def test_missing_sentinels_are_rejected(self):
        self.assertIsNone(be._clean(1e11, -90, 90))
        self.assertIsNone(be._clean(None, -90, 90))
        self.assertIsNone(be._clean("nope", -90, 90))

    def test_ms_to_knots(self):
        self.assertAlmostEqual(be._clean(35.5, 0, 200, 1.943844), 69.01, places=1)


class TestStormMatching(unittest.TestCase):
    """ECMWF's storm number is its own sequence. On 2026-07-28 DOLPHIN was
    ``15W`` in the BUFR and ``WP12`` to JTWC. Matching on the identifier
    attaches one storm's ensemble to another storm's page, which is the worst
    failure this product can have."""

    STORMS = [
        {"storm_id": "07E", "name": "GENEVIEVE", "basin": "ep"},
        {"storm_id": "15W", "name": "DOLPHIN", "basin": "wp"},
        {"storm_id": "12W", "name": "OTHERSTORM", "basin": "wp"},
        {"storm_id": "70W", "name": "70W", "basin": "wp"},
    ]

    def test_name_wins_over_a_disagreeing_id(self):
        got = be.match_ecmwf(self.STORMS, "JTWC_WP122026", "DOLPHIN")
        self.assertEqual(got["storm_id"], "15W")

    def test_id_only_match_would_pick_the_wrong_storm(self):
        """WP12 exists in the BUFR as a DIFFERENT system, so the id fallback
        would confidently return it. The name must take precedence."""
        wrong = [s for s in self.STORMS if s["storm_id"] == "12W"][0]
        self.assertNotEqual(wrong["name"], "DOLPHIN")
        self.assertEqual(be.match_ecmwf(self.STORMS, "JTWC_WP122026",
                                        "DOLPHIN")["name"], "DOLPHIN")

    def test_agreeing_ids_still_match(self):
        self.assertEqual(
            be.match_ecmwf(self.STORMS, "NHC_EP072026", "GENEVIEVE")["storm_id"],
            "07E")

    def test_unnamed_falls_back_to_basin_and_number(self):
        self.assertEqual(
            be.match_ecmwf(self.STORMS, "JTWC_WP122026", "")["storm_id"], "12W")

    def test_ambiguous_name_is_dropped_not_guessed(self):
        dup = self.STORMS + [{"storm_id": "08E", "name": "GENEVIEVE",
                              "basin": "ep"}]
        self.assertIsNone(be.match_ecmwf(dup, "NHC_EP072026", "GENEVIEVE"))

    def test_invest_name_is_not_used_as_an_identity(self):
        storms = [{"storm_id": "70W", "name": "INVEST", "basin": "wp"}]
        # Falls through to the id path rather than matching every invest.
        self.assertIsNone(be.match_ecmwf(storms, "JTWC_WP122026", "INVEST"))

    def test_no_match_returns_none(self):
        self.assertIsNone(be.match_ecmwf(self.STORMS, "NHC_AL022026", "BERTHA"))


class TestBasinMap(unittest.TestCase):

    def test_every_ecmwf_basin_letter_maps(self):
        for letter, basin in (("L", "al"), ("E", "ep"), ("C", "cp"),
                              ("W", "wp"), ("A", "io"), ("B", "io"),
                              ("S", "sh"), ("P", "sh")):
            self.assertEqual(be._ECMWF_BASIN[letter], basin, letter)

    def test_ecmwf_publishes_only_00_and_12z(self):
        self.assertEqual(be.ECMWF_CYCLES, ("00", "12"))
        self.assertEqual(be.fetch_ecmwf("2026072806"), [],
                         "a 06Z cycle has no ENS track file; asking for one "
                         "must be a quiet no-op, not a fetch attempt")


if __name__ == "__main__":
    unittest.main()
