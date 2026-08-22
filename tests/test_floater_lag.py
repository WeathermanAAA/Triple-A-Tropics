"""Locks the floater frame-recency discriminator (scripts/freshness_probe.py).

The floater manifest freshness rows gate on generated_utc, which the box
floater poller re-stamps every sweep even when it renders no new frame — so a
producing-but-lagging floater (frames stuck while the manifest re-ticks) is
invisible there. This discriminator reads the newest FRAME time across active
storms and alarms when the freshest floater frame is older than the tolerance.

Properties locked here:
  - off-season (no active storms) is fresh, never a false alarm;
  - active storms listed but NO readable frame is a LOUD stale row (producer
    blind), never a silent skip;
  - a fresh frame under tolerance passes; over tolerance alarms;
  - the row carries no suppression (known_down always False);
  - an unreachable top manifest alarms loudly;
  - one unreadable storm manifest does not blind the discriminator as long as
    another storm has a readable frame.
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
NOW = dt.datetime(2026, 8, 22, 16, 0, tzinfo=UTC)


def _t(mins_ago):
    return NOW - dt.timedelta(minutes=mins_ago)


class TestEvaluateFloaterLag(unittest.TestCase):
    def test_off_season_is_fresh(self):
        r = fp.evaluate_floater_lag("d", None, 0, NOW)
        self.assertFalse(r["stale"])
        self.assertIn("no active floaters", r["note"])

    def test_active_but_no_frame_is_loud(self):
        r = fp.evaluate_floater_lag("d", None, 3, NOW)
        self.assertTrue(r["stale"])
        self.assertIn("blind", r["note"])

    def test_fresh_frame_passes(self):
        r = fp.evaluate_floater_lag("d", _t(20), 4, NOW)
        self.assertFalse(r["stale"])
        self.assertEqual(r["age_min"], 20.0)

    def test_lagging_frame_alarms(self):
        r = fp.evaluate_floater_lag("d", _t(100), 4, NOW)
        self.assertTrue(r["stale"])
        self.assertIn("FROZEN", r["note"])

    def test_boundary(self):
        self.assertFalse(fp.evaluate_floater_lag("d", _t(90), 2, NOW)["stale"])
        self.assertTrue(fp.evaluate_floater_lag("d", _t(91), 2, NOW)["stale"])

    def test_no_suppression(self):
        for a in (10, 50, 0):
            r = fp.evaluate_floater_lag("d", _t(a) if a else None, 2, NOW)
            self.assertFalse(r["known_down"])


class TestFloaterFrameLagRows(unittest.TestCase):
    def _storm(self, *frame_mins):
        return {"bands": {"ir": {"frames": [
            {"t": _t(m).strftime("%Y-%m-%dT%H:%M:%SZ"), "key": "k"}
            for m in frame_mins]}}}

    def test_freshest_across_storms(self):
        top = {"storms": [{"manifest": "a"}, {"manifest": "b"}]}
        store = {"a": self._storm(60, 55), "b": self._storm(40, 30)}
        rows = fp.floater_frame_lag_rows(
            NOW, fetch_top=lambda: top, fetch_storm=lambda r: store[r])
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["stale"])          # freshest (30) < 45
        self.assertEqual(rows[0]["age_min"], 30.0)

    def test_all_storms_lagging_alarms(self):
        top = {"storms": [{"manifest": "a"}, {"manifest": "b"}]}
        store = {"a": self._storm(120), "b": self._storm(100)}
        rows = fp.floater_frame_lag_rows(
            NOW, fetch_top=lambda: top, fetch_storm=lambda r: store[r])
        self.assertTrue(rows[0]["stale"])           # freshest (100) > 90

    def test_one_unreadable_storm_not_blind(self):
        top = {"storms": [{"manifest": "a"}, {"manifest": "b"}]}

        def fetch(r):
            if r == "a":
                raise RuntimeError("boom")
            return self._storm(20)
        rows = fp.floater_frame_lag_rows(NOW, fetch_top=lambda: top, fetch_storm=fetch)
        self.assertFalse(rows[0]["stale"])          # b is fresh

    def test_unreachable_top_is_loud(self):
        def boom():
            raise RuntimeError("cdn down")
        rows = fp.floater_frame_lag_rows(NOW, fetch_top=boom)
        self.assertTrue(rows[0]["stale"])
        self.assertIn("blind", rows[0]["note"])

    def test_off_season_fresh(self):
        rows = fp.floater_frame_lag_rows(NOW, fetch_top=lambda: {"storms": []})
        self.assertFalse(rows[0]["stale"])


if __name__ == "__main__":
    unittest.main()
