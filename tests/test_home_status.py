"""Behavioral tests for the homepage live-status strip (the inline hydrate
script in index.html), driven through tests/home_status_harness.cjs under
node with a minimal DOM/fetch shim. First coverage for this script.

Pins the invest-aware count-cell vocabulary:
  * >=1 active designated TC      -> "N active" + green dot (invests never
                                     add to N, even when also present)
  * 0 active TCs, >=1 active invest -> "N invest(s)" + green dot
  * nothing active                -> "N named YTD", dot off
  * feed missing                  -> fail-soft "0 named YTD", dot off
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INDEX = REPO / "index.html"
HARNESS = Path(__file__).resolve().parent / "home_status_harness.cjs"
NODE = shutil.which("node")


def run_strip(fixtures: dict) -> dict:
    with tempfile.TemporaryDirectory() as td:
        fx = Path(td) / "fixtures.json"
        fx.write_text(json.dumps(fixtures), encoding="utf-8")
        proc = subprocess.run(
            [NODE, str(HARNESS), str(INDEX), str(fx)],
            capture_output=True, text=True, timeout=60,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"node harness failed:\n{proc.stderr}")
    return json.loads(proc.stdout)["basins"]


def storm(*, active, invest):
    return {"is_active": active, "is_invest": invest}


def tracks(named, storms):
    return {"header": {"named": named}, "storms": storms}


@unittest.skipIf(NODE is None, "node not on PATH")
class TestHomeStatusStrip(unittest.TestCase):

    def test_invest_only_basin_lights_green_as_invest(self):
        # The WPAC-with-91W case: zero active TCs, one active invest.
        out = run_strip({
            "wp_tracks_data.json": tracks(5, [
                storm(active=False, invest=False),
                storm(active=True, invest=True),
            ]),
        })
        self.assertTrue(out["wp"]["active"])
        self.assertEqual(out["wp"]["count"], "<b>1</b> invest")

    def test_two_invests_pluralize(self):
        out = run_strip({
            "wp_tracks_data.json": tracks(5, [
                storm(active=True, invest=True),
                storm(active=True, invest=True),
            ]),
        })
        self.assertTrue(out["wp"]["active"])
        self.assertEqual(out["wp"]["count"], "<b>2</b> invests")

    def test_active_tc_wins_and_invests_dont_add(self):
        # The EPAC-with-Amanda case (+ a hypothetical invest): the count is
        # designated TCs only, invests never inflate it.
        out = run_strip({
            "ep_tracks_data.json": tracks(1, [
                storm(active=True, invest=False),
                storm(active=True, invest=True),
            ]),
        })
        self.assertTrue(out["ep"]["active"])
        self.assertEqual(out["ep"]["count"], "<b>1</b> active")

    def test_inactive_invest_does_not_light(self):
        # A stale invest_x (recent_invest card, not active) keeps the named
        # fallback + dot off.
        out = run_strip({
            "al_tracks_data.json": tracks(3, [
                storm(active=False, invest=True),
            ]),
        })
        self.assertFalse(out["al"]["active"])
        self.assertEqual(out["al"]["count"], "<b>3</b> named YTD")

    def test_quiet_basin_named_ytd(self):
        out = run_strip({
            "al_tracks_data.json": tracks(0, []),
        })
        self.assertFalse(out["al"]["active"])
        self.assertEqual(out["al"]["count"], "<b>0</b> named YTD")

    def test_missing_feed_fails_soft(self):
        out = run_strip({})
        for b in ("al", "ep", "wp"):
            self.assertFalse(out[b]["active"])
            self.assertEqual(out[b]["count"], "<b>0</b> named YTD")

    def test_stale_feed_without_is_invest_degrades_to_old_behavior(self):
        # A cached/stale feed whose storms predate the is_invest field:
        # !s.is_invest is true for undefined, so every active system counts
        # as a TC — exactly the pre-change behavior, never a crash or a
        # spurious "invest" label.
        out = run_strip({
            "wp_tracks_data.json": tracks(5, [
                {"is_active": True},          # no is_invest key at all
                {"is_active": False},
            ]),
        })
        self.assertTrue(out["wp"]["active"])
        self.assertEqual(out["wp"]["count"], "<b>1</b> active")


if __name__ == "__main__":
    unittest.main(verbosity=2)
