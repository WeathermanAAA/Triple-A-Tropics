#!/usr/bin/env python3
"""ATCF guidance ingest + QC (``guidance.atcf``).

Every fixture below is shaped from the REAL public decks (verified 2026-07-28
across all 23 live 2026 files, 521,842 rows / 106 aids), not from the format
document - the traps this guards against are the ones the live data actually
contains. Run: ``python -m unittest discover tests``
"""
import datetime as dt
import gzip
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from guidance import atcf  # noqa: E402


def _row(basin="AL", cy=1, dtg="2026061618", tech="HFSA", tau=0,
         lat="218N", lon="0651W", vmax="65", mslp="985", rad="34",
         extra=""):
    """One 18-field ATCF row - the width 86% of live rows actually use."""
    base = (f"{basin}, {cy:02d}, {dtg},   , {tech}, {tau:4d}, {lat}, {lon}, "
            f"{vmax}, {mslp}, XX,  {rad}, NEQ,    0,    0,    0,    0,")
    return base + extra


class TestLatLon(unittest.TestCase):
    def test_tenths_of_a_degree_with_hemisphere(self):
        self.assertEqual(atcf.parse_latlon("218N", "0651W"), (21.8, -65.1))
        self.assertEqual(atcf.parse_latlon("155S", "1511E"), (-15.5, 151.1))

    def test_east_longitude_stays_positive(self):
        """~10k live rows cross into E. Assuming W flips the track 360 deg."""
        self.assertEqual(atcf.parse_latlon("120N", "1400E"), (12.0, 140.0))

    def test_antimeridian_is_encoded_east(self):
        """The exact value '1800E' occurs in the live decks."""
        self.assertEqual(atcf.parse_latlon("120N", "1800E"), (12.0, 180.0))

    def test_zero_position_sentinel_is_none(self):
        """0N/0W is null island, not a fix - 9,561 live rows carry it. This is
        the single most dangerous value in the format because it is
        syntactically valid and survives naive parsing."""
        self.assertIsNone(atcf.parse_latlon("0N", "0W"))
        self.assertIsNone(atcf.parse_latlon("000N", "0000W"))

    def test_a_real_fix_near_but_not_at_the_origin_survives(self):
        self.assertEqual(atcf.parse_latlon("001N", "0001W"), (0.1, -0.1))

    def test_garbage_is_none(self):
        for lat, lon in (("", "0651W"), ("218", "0651W"), ("218X", "0651W"),
                         ("abcN", "0651W")):
            self.assertIsNone(atcf.parse_latlon(lat, lon), (lat, lon))


class TestSentinels(unittest.TestCase):
    def test_zero_is_missing_for_vmax_and_mslp(self):
        """MSLP==0 is 28.89% of live rows and VMAX==0 is 8.50% - whole aid
        families never populate them. 'Zero millibars' is not a pressure."""
        self.assertIsNone(atcf._int_or_none("0"))
        self.assertIsNone(atcf._int_or_none("   0 "))

    def test_minus_99_is_a_second_independent_sentinel(self):
        """POUTER/ROUTER use -99, ~18,200 rows each."""
        self.assertIsNone(atcf._int_or_none("-99"))

    def test_minus_999_from_the_user_data_block(self):
        self.assertIsNone(atcf._int_or_none("-999"))

    def test_real_values_pass_through(self):
        self.assertEqual(atcf._int_or_none("985"), 985)
        self.assertEqual(atcf._int_or_none("65"), 65)
        self.assertEqual(atcf._int_or_none("-5", zero_is_missing=False), -5)

    def test_blank_and_garbage_are_none(self):
        for v in ("", "   ", "abc", None):
            self.assertIsNone(atcf._int_or_none(v), v)

    def test_sentinels_do_not_leak_into_rows(self):
        text = "\n".join([
            _row(vmax="0", mslp="0"),
            _row(tau=6, lat="0N", lon="0W"),
        ])
        rows, rep = atcf.parse_deck(text)
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0].vmax_kt)
        self.assertIsNone(rows[0].mslp_hpa)
        self.assertIsNone(rows[1].lat)
        self.assertIsNone(rows[1].lon)
        self.assertFalse(rows[1].has_position)
        self.assertEqual(rep.vmax_missing, 1)
        self.assertEqual(rep.mslp_missing, 1)
        self.assertEqual(rep.position_missing, 1)


class TestVariableWidth(unittest.TestCase):
    def test_minimum_width_row_parses(self):
        """18 fields is 86% of live rows; a parser that indexes a fixed high
        column IndexErrors on the bulk of the data."""
        rows, rep = atcf.parse_deck(_row())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rep.malformed, 0)

    def test_wide_row_parses(self):
        """46-field rows exist (HWRF/HFSA carry the THERMO PARAMS block)."""
        rows, rep = atcf.parse_deck(_row(extra=" , , , , , , 1013, 200, 15,"
                                               " -999, -999, -999,"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rep.malformed, 0)

    def test_truncated_row_is_rejected_not_crashed(self):
        rows, rep = atcf.parse_deck("AL, 01, 2026061618, , HFSA,")
        self.assertEqual(rows, [])
        self.assertEqual(rep.short_rows, 1)
        self.assertEqual(rep.malformed, 1)

    def test_bad_dtg_is_rejected(self):
        rows, rep = atcf.parse_deck(_row(dtg="notadate1"))
        self.assertEqual(rows, [])
        self.assertEqual(rep.bad_dtg, 1)

    def test_trailing_comma_does_not_become_data(self):
        rows, _ = atcf.parse_deck(_row())
        self.assertEqual(rows[0].tech, "HFSA")
        self.assertEqual(rows[0].basin, "al")


class TestPrimaryKey(unittest.TestCase):
    def test_rad_is_part_of_the_key(self):
        """(DTG, TECH, TAU) is NOT unique: 73,916 live triples carry >1 row
        because the 34/50/64 kt radii records share it. Deduplicating on the
        triple would silently discard ~110k genuine records."""
        text = "\n".join([_row(rad="34"), _row(rad="50"), _row(rad="64")])
        rows, rep = atcf.parse_deck(text)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({r.key for r in rows}), 3)
        self.assertEqual(rep.duplicate_keys, 0,
                         "multi-RAD rows must not be counted as duplicates")

    def test_genuine_duplicate_key_is_counted(self):
        # Same key twice, but not adjacent-identical (a middle row separates
        # them), so the byte-identical skip does not mask it.
        text = "\n".join([_row(rad="34"), _row(rad="50"), _row(rad="34")])
        rows, rep = atcf.parse_deck(text)
        self.assertEqual(rep.duplicate_keys, 1)

    def test_byte_identical_adjacent_lines_are_skipped(self):
        """38 such rows exist live, always as two adjacent identical lines."""
        line = _row()
        rows, rep = atcf.parse_deck(line + "\n" + line)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rep.exact_duplicate_rows, 1)


class TestForecastFiltering(unittest.TestCase):
    def test_negative_tau_is_excluded_by_default(self):
        """CARQ's -24/-18/-12/-6 rows are past positions for bogusing, not
        forecasts (1,328 live rows)."""
        rows, rep = atcf.parse_deck(_row(tech="CARQ", tau=-12))
        self.assertEqual(rows, [])
        self.assertEqual(rep.negative_tau, 1)

    def test_carq_at_tau_zero_is_still_excluded(self):
        rows, _ = atcf.parse_deck(_row(tech="CARQ", tau=0))
        self.assertEqual(rows, [])

    def test_non_forecast_rows_can_be_kept_explicitly(self):
        rows, _ = atcf.parse_deck(_row(tech="CARQ", tau=-12),
                                  keep_non_forecast=True)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0].is_forecast)

    def test_forecast_aid_is_kept(self):
        rows, _ = atcf.parse_deck(_row(tech="HFSA", tau=12))
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].is_forecast)


class TestImplausibleMotion(unittest.TestCase):
    def test_a_fast_but_real_track_is_not_flagged(self):
        # ~25 kt of translation: brisk, entirely normal.
        text = "\n".join([_row(tau=0, lat="200N", lon="0600W"),
                          _row(tau=6, lat="200N", lon="0627W")])
        _, rep = atcf.parse_deck(text)
        self.assertEqual(rep.implausible_speed, 0)

    def test_an_impossible_jump_is_flagged(self):
        text = "\n".join([_row(tau=0, lat="200N", lon="0600W"),
                          _row(tau=6, lat="200N", lon="0900W")])
        _, rep = atcf.parse_deck(text)
        self.assertEqual(rep.implausible_speed, 1)
        self.assertGreater(rep.speed_flags[0]["kt"], atcf.MAX_TRANSLATION_KT)

    def test_zero_position_rows_cannot_manufacture_a_flag(self):
        """THE ordering regression. Run before the 0N/0W sentinel is resolved,
        this check invents hundreds of false flags with implied speeds to 754 kt
        - the sentinel sits ~4,000 nm from any real storm. Position QC must come
        first, always."""
        text = "\n".join([_row(tech="SHIP", tau=132, lat="200N", lon="0600W"),
                          _row(tech="SHIP", tau=138, lat="0N", lon="0W")])
        _, rep = atcf.parse_deck(text)
        self.assertEqual(rep.implausible_speed, 0)

    def test_radii_rows_do_not_double_count_the_track(self):
        """50/64 kt rows repeat the same position; only the primary row feeds
        the motion check, or every fix would compare against itself."""
        text = "\n".join([_row(tau=0, rad="34"), _row(tau=0, rad="50"),
                          _row(tau=6, rad="34", lat="205N"),
                          _row(tau=6, rad="50", lat="205N")])
        _, rep = atcf.parse_deck(text)
        self.assertEqual(rep.implausible_speed, 0)

    def test_great_circle_is_sane(self):
        # 1 degree of latitude == 60 nm by definition.
        self.assertAlmostEqual(atcf.great_circle_nm((0, 0), (1, 0)), 60.0,
                               delta=0.2)


class TestFilteredDeckHonesty(unittest.TestCase):
    def test_every_ecmwf_derived_aid_is_listed_as_withheld(self):
        for t in ("EMX", "EMXI", "EMX2", "EEMN", "EMNI", "SHPE", "DSPE",
                  "LGME", "EAIO", "EAMN", "UKM", "UKMI", "UEMN", "FSSE",
                  "GFEX"):
            self.assertIn(t, atcf.WITHHELD_TECHS, t)

    def test_surviving_ukmet_variants_are_not_marked_withheld(self):
        """UKMET survives only as the GFS-tracker UKX* variants."""
        for t in ("UKX", "UKXI", "UKX2"):
            self.assertNotIn(t, atcf.WITHHELD_TECHS, t)

    def test_hafs_live_ids_are_not_the_techlist_ids(self):
        """HFSA/HFSB are the live ids (8,162 / 8,158 rows). HAFA/HAFB are
        defined in nhc_techlist.dat and have ZERO live rows - keying on them
        would wait forever for aids that never come."""
        self.assertNotIn("HFSA", atcf.WITHHELD_TECHS)
        self.assertNotIn("HFSB", atcf.WITHHELD_TECHS)

    def test_consensus_aids_report_their_withheld_members(self):
        prov = atcf.consensus_provenance(["TVCN", "RVCN", "AVNO", "HFSA"])
        by_tech = {p["tech"]: p for p in prov}
        self.assertIn("TVCN", by_tech)
        self.assertIn("EMXI", by_tech["TVCN"]["withheld_members"])
        self.assertFalse(by_tech["TVCN"]["reproducible"])
        self.assertNotIn("AVNO", by_tech, "a non-consensus aid must not appear")

    def test_absent_consensus_aid_is_not_reported(self):
        self.assertEqual(atcf.consensus_provenance(["AVNO"]), [])

    def test_notice_states_the_consequence(self):
        notice = atcf.filtered_deck_notice(["TVCN", "AVNO"])
        self.assertEqual(notice["withheld_present_anyway"], [])
        self.assertTrue(notice["consensus"])
        self.assertIn("not", notice["note"].lower())
        self.assertIn("independently reproducible", notice["note"])


class TestAgainstRealDeck(unittest.TestCase):
    """Runs only if a real deck has been fetched into the scratchpad; skipped in
    CI. Guards the parser against the actual bytes rather than a fixture."""

    DECK = pathlib.Path(
        "/tmp/claude-1000/-workspaces-Triple-A-Tropics/"
        "64965d72-db4c-490c-b66b-08cd6ec1eaf9/scratchpad/aal012026.dat.gz")

    def setUp(self):
        if not self.DECK.exists():
            self.skipTest("no real deck fetched")
        self.text = gzip.decompress(self.DECK.read_bytes()).decode(
            "utf-8", "replace")

    def test_parses_with_no_malformed_rows(self):
        rows, rep = atcf.parse_deck(self.text)
        self.assertGreater(rep.rows_kept, 1000)
        self.assertEqual(rep.malformed, 0)

    def test_no_withheld_aid_appears(self):
        _, rep = atcf.parse_deck(self.text)
        self.assertEqual(sorted(set(rep.techs) & set(atcf.WITHHELD_TECHS)), [])

    def test_no_sentinel_survives_into_a_row(self):
        rows, _ = atcf.parse_deck(self.text)
        self.assertFalse([r for r in rows if r.mslp_hpa == 0])
        self.assertFalse([r for r in rows if r.vmax_kt == 0])
        self.assertFalse([r for r in rows
                          if r.lat == 0.0 and r.lon == 0.0])

    def test_motion_flags_stay_near_zero_on_clean_data(self):
        """With position QC applied first, exactly one pair in the whole live
        2026 set exceeds the threshold. A regression that broke the sentinel
        handling would show up here as hundreds."""
        _, rep = atcf.parse_deck(self.text)
        self.assertLessEqual(rep.implausible_speed, 5)


class TestBDeck(unittest.TestCase):
    def test_b_deck_uses_the_same_parser(self):
        """b-decks are the SAME comma-delimited layout - TECH is always BEST and
        TAU always 0 - so there is no second format to maintain."""
        text = ("EP, 06, 2026071618,   , BEST,    0, 82N, 1001W,  20, 1008, "
                "DB,  34, NEQ,    0,    0,    0,    0,")
        rows, rep = atcf.parse_deck(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].tech, "BEST")
        self.assertEqual(rows[0].tau, 0)
        self.assertEqual(rows[0].basin, "ep")
        self.assertEqual(rows[0].lat, 8.2)
        self.assertEqual(rows[0].lon, -100.1)
        self.assertEqual(rep.malformed, 0)


class TestQCReport(unittest.TestCase):
    def test_report_is_json_serializable_and_bounded(self):
        import json
        text = "\n".join(_row(tau=t) for t in range(0, 30, 6))
        _, rep = atcf.parse_deck(text)
        d = rep.as_dict()
        json.dumps(d)   # must not raise
        self.assertLessEqual(len(d["speed_flags"]), 50)
        self.assertIsInstance(d["techs"], dict)

    def test_summary_mentions_what_was_dropped(self):
        _, rep = atcf.parse_deck(_row(vmax="0"))
        self.assertIn("rows kept", rep.summary())
        self.assertIn("MSLP", rep.summary())


class TestFetch(unittest.TestCase):
    """The opener is injected, so fetch logic is tested with no network."""

    def test_gzip_body_is_decompressed(self):
        payload = gzip.compress(_row().encode())
        text = atcf.fetch_deck("al", 1, 2026, opener=lambda url: payload)
        self.assertIn("HFSA", text)

    def test_already_inflated_body_is_accepted(self):
        text = atcf.fetch_deck("al", 1, 2026, opener=lambda url: _row().encode())
        self.assertIn("HFSA", text)

    def test_missing_deck_returns_none_not_an_exception(self):
        def boom(url):
            raise RuntimeError("404 Client Error")
        self.assertIsNone(atcf.fetch_deck("al", 99, 2026, opener=boom))

    def test_a_real_error_propagates(self):
        def boom(url):
            raise RuntimeError("500 Server Error")
        with self.assertRaises(RuntimeError):
            atcf.fetch_deck("al", 1, 2026, opener=boom)

    def test_url_shapes(self):
        seen = []

        def cap(url):
            seen.append(url)
            return _row().encode()
        atcf.fetch_deck("al", 5, 2026, kind="a", opener=cap)
        atcf.fetch_deck("ep", 12, 2026, kind="b", opener=cap)
        self.assertEqual(
            seen[0],
            "https://ftp.nhc.noaa.gov/atcf/aid_public/aal052026.dat.gz")
        self.assertEqual(
            seen[1], "https://ftp.nhc.noaa.gov/atcf/btk/bep122026.dat")


if __name__ == "__main__":
    unittest.main()
