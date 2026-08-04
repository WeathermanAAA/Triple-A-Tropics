"""Locks the NHC-ingest discriminator (scripts/freshness_probe.py).

The discriminator tells a dead AL/EP b-deck ingest apart from a genuinely
quiet basin by comparing the btk listing's per-file Last-Modified (what NHC
last wrote) with the feed's latest_fix_valid_utc (what we last ingested).
Properties locked here, in order of how expensive losing them would be:

  - an unreachable btk listing (or unreadable feed) is a LOUD stale row,
    never a silent skip — the monitor going blind must itself alarm;
  - the rows carry no suppression (known_down is always False), and the
    source keeps the re-arm rule comment for anyone tempted to add one;
  - the 12 h tolerance absorbs NHC's write-after-valid skew (~2.5 h
    measured on AL02 2026's final fix) without alarming;
  - invest decks (b??9x) never alarm — fetch_live_season sweeps numbered
    decks 01–40 only, so invest churn is not ingestable lag.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import freshness_probe as fp  # noqa: E402

UTC = dt.timezone.utc

# Mirrors the real Apache fancy-index rows served by ftp.nhc.noaa.gov/atcf/btk/
LISTING = """
<a href="bal012026.dat">bal012026.dat</a>           2026-06-18 02:47   95K
<a href="bal022026.dat">bal022026.dat</a>           2026-07-24 02:32   19K
<a href="bal902026.dat">bal902026.dat</a>           2026-08-04 00:15    2K
<a href="bal032025.dat">bal032025.dat</a>           2026-08-03 12:00   40K
<a href="bep062026.dat">bep062026.dat</a>           2026-07-29 14:32   33K
<a href="bep072026.dat">bep072026.dat</a>           2026-08-02 20:32   21K
<a href="bep952026.dat">bep952026.dat</a>           2026-08-03 18:00    1K
"""


def _z(s):
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=UTC)


class TestBtkNewestLm(unittest.TestCase):
    def test_numbered_decks_only(self):
        # bal902026 (invest, newer) and bal032025 (wrong season) are ignored.
        self.assertEqual(fp.btk_newest_lm(LISTING, "al", 2026),
                         _z("2026-07-24 02:32"))
        # bep952026 (invest) ignored; GENEVIEVE's deck wins.
        self.assertEqual(fp.btk_newest_lm(LISTING, "ep", 2026),
                         _z("2026-08-02 20:32"))

    def test_no_decks_for_season(self):
        self.assertIsNone(fp.btk_newest_lm(LISTING, "al", 2027))


class TestEvaluateIngestLag(unittest.TestCase):
    def test_write_after_valid_skew_is_fresh(self):
        # AL02's real final fix: valid 00Z, deck written 02:32Z — the exact
        # skew the 12 h tolerance exists to absorb.
        row = fp.evaluate_ingest_lag(
            "x", _z("2026-07-24 02:32"), _z("2026-07-24 00:00"))
        self.assertFalse(row["stale"])
        self.assertEqual(row["age_min"], 152.0)

    def test_lag_beyond_tolerance_is_stale(self):
        row = fp.evaluate_ingest_lag(
            "x", _z("2026-08-03 13:00"), _z("2026-08-03 00:00"))
        self.assertTrue(row["stale"])
        self.assertIn("+13.0 h ahead", row["note"])

    def test_lag_at_tolerance_boundary_is_fresh(self):
        row = fp.evaluate_ingest_lag(
            "x", _z("2026-08-03 12:00"), _z("2026-08-03 00:00"))
        self.assertFalse(row["stale"])

    def test_quiet_preseason_is_fresh(self):
        row = fp.evaluate_ingest_lag("x", None, None)
        self.assertFalse(row["stale"])
        self.assertIn("nothing to ingest", row["note"])

    def test_feed_missing_fix_while_upstream_has_decks_is_stale(self):
        row = fp.evaluate_ingest_lag("x", _z("2026-07-24 02:32"), None)
        self.assertTrue(row["stale"])
        self.assertIn("ingested nothing", row["note"])


class TestLoudBlindness(unittest.TestCase):
    NOW = dt.datetime(2026, 8, 4, 3, 0, tzinfo=UTC)

    def test_unreachable_listing_alarms_loudly(self):
        def dead_listing():
            raise OSError("connection refused")
        rows = fp.ingest_lag_rows(
            self.NOW, fetch_listing=dead_listing,
            fetch_feed=lambda b: {"latest_fix_valid_utc": "2026-08-04T00:00:00Z"})
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertTrue(row["stale"])
            self.assertIn("discriminator blind", row["note"])

    def test_unreadable_feed_alarms_loudly(self):
        def dead_feed(basin):
            raise ValueError("bad json")
        rows = fp.ingest_lag_rows(
            self.NOW, fetch_listing=lambda: LISTING, fetch_feed=dead_feed)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertTrue(row["stale"])
            self.assertIn("discriminator blind", row["note"])

    def test_healthy_pass_end_to_end(self):
        rows = fp.ingest_lag_rows(
            self.NOW, fetch_listing=lambda: LISTING,
            fetch_feed=lambda b: {"latest_fix_valid_utc":
                                  {"al": "2026-07-24T00:00:00Z",
                                   "ep": "2026-08-02T18:00:00Z"}[b]})
        self.assertEqual([r["stale"] for r in rows], [False, False])
        self.assertEqual(rows[0]["last_utc"], "2026-07-24T02:32:00Z")
        self.assertEqual(rows[1]["last_utc"], "2026-08-02T20:32:00Z")


class TestNoSuppression(unittest.TestCase):
    def test_rows_never_known_down(self):
        for rows in (
            fp.ingest_lag_rows(
                dt.datetime(2026, 8, 4, tzinfo=UTC),
                fetch_listing=lambda: LISTING,
                fetch_feed=lambda b: {"latest_fix_valid_utc":
                                      "2026-07-24T00:00:00Z"}),
            fp.ingest_lag_rows(
                dt.datetime(2026, 8, 4, tzinfo=UTC),
                fetch_listing=lambda: (_ for _ in ()).throw(OSError("down")),
                fetch_feed=lambda b: {}),
        ):
            for row in rows:
                self.assertFalse(row["known_down"])

    def test_source_keeps_the_rearm_rule(self):
        # The inline comment IS the policy for anyone adding a mute later;
        # deleting it silently is how the HAFS blindfold happened.
        src = (REPO / "scripts" / "freshness_probe.py").read_text()
        self.assertIn("NO suppression", src)
        self.assertIn("re-arm condition", src)


if __name__ == "__main__":
    unittest.main()
