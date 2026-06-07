"""A live-deck fix with a missing NATURE (pandas float NaN) must not
crash the feed build — and must count exactly like the long-standing
blank-NATURE ("") case.

Regression for the 2026-06-07 16:34Z update-ace failure: a WP b-deck
fix arrived with a blank NATURE column; pandas surfaced it as NaN, and
``(nature or "").strip()`` crashed (NaN is truthy) in nature_eligible —
killing the WHOLE per-basin regeneration run, every cron, until the
deck row aged out. The fix maps None/NaN to "" at the _nature_str choke
point, preserving the documented blank-NATURE semantics (ACE-eligible
only on provisional data, so a 34 kt+ provisional fix isn't dropped).
"""
from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

import pandas as pd

# Repo-source-first (see overlay_test_util): test the ace_core under
# review, not a possibly-lagging pip-installed copy.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ace_core"))

from ace_core import (  # noqa: E402
    merge_and_extract_storms,
    nature_eligible,
)

CFG = {"short": "wp", "agency_name": "JTWC", "invest_letter": "W"}


def _rows(nature):
    now = dt.datetime.utcnow()
    anchor = now.replace(hour=(now.hour // 6) * 6, minute=0,
                         second=0, microsecond=0)
    return [{
        "SID": "JTWC_WP032026", "NAME": "TESTSTORM", "season": 2026,
        "time": anchor - dt.timedelta(hours=6 * (1 - i)),
        "lat": 15.0 + i, "lon": 130.0 + i,
        "wind_kt": 45.0, "pressure_mb": 995.0,
        "nature": nature, "ace_nature": nature,
        "source": "live-JTWC", "storm_num": 3,
    } for i in range(2)]


class TestNanNature(unittest.TestCase):

    def test_nature_eligible_nan_equals_blank(self):
        for basin in ("wp", "al", "ep"):
            for provisional in (True, False):
                with self.subTest(basin=basin, provisional=provisional):
                    self.assertEqual(
                        nature_eligible(float("nan"), basin, provisional),
                        nature_eligible("", basin, provisional))

    def test_nan_nature_feed_builds_and_matches_blank(self):
        # The crash path: NaN nature through the REAL pipeline.
        nan_storms = merge_and_extract_storms(
            pd.DataFrame(), pd.DataFrame(_rows(float("nan"))), CFG)
        blank_storms = merge_and_extract_storms(
            pd.DataFrame(), pd.DataFrame(_rows("")), CFG)
        self.assertTrue(nan_storms and blank_storms)
        nan_s, blank_s = nan_storms[0], blank_storms[0]
        # Identical ACE — NaN must mean exactly what blank means.
        self.assertEqual(nan_s["ace"], blank_s["ace"])
        # The serialized point carries a clean string, never NaN.
        for p in nan_s["points"]:
            self.assertEqual(p["nature"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
