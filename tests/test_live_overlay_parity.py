"""Byte-parity between the Python per-basin renderers and their JS
mirrors in LIVE_BASIN_JS (the live overlay).

The live page swaps the cron-baked storm layers for client-rendered ones
built from the SAME feed shape — these tests prove the two renderers
produce byte-identical markup for the same input, so the swap is
invisible except for data freshness.

Needs `node` on PATH (GitHub runners and codespaces ship it); tests
skip cleanly when it is absent.

Optional live check (network): LIVE_FEED_PARITY=1 python -m unittest
tests.test_live_overlay_parity — re-runs the parity assertion against
the real R2 feeds for all three basins.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from overlay_test_util import (NODE, gtp, python_fragments,  # noqa: E402
                               run_harness)

BASINS = ("wp", "al", "ep")
YEAR = 2026

# Exact .5-tie values that JS toFixed() would round away from zero but
# Python's f"{x:.Nf}" rounds to even — pyFixed() must match Python.
FMT1_VALUES = [1.75, 5.25, 0.25, 0.75, 2.5, 612.25, -5.25, 0.05,
               123.45, 0.15, 0.15000000000000002, 729.4000000000001]
FMT2_VALUES = [12.345, 0.365, 1.005, 50.978, 0.0, 3.4567, 0.125, 2.375]


def _pt(t, lat, lon, wind, pres, cls, nature):
    return {"t": t, "lat": lat, "lon": lon, "wind_kt": wind,
            "pressure_mb": pres, "cls": cls, "nature": nature}


def make_fixture_storms(extent) -> list[dict]:
    """Storms exercising every renderer branch, positioned inside the
    given basin extent so the same fixtures work for wp/al/ep."""
    lon0, lon1, lat0, lat1 = extent

    def L(frac):
        return lon0 + (lon1 - lon0) * frac

    def A(frac):
        return lat0 + (lat1 - lat0) * frac

    return [
        # 1: active named TS+ -> spinning icon; polyline; TD/TS/C1 dot
        #    radii; a "non" triangle point; a None pressure; a name with
        #    an embedded double quote (stripped in data-name).
        {
            "sid": "TEST_011", "name": 'AMA"NDA', "atcf_id": None,
            "is_active": True, "is_invest": False,
            "peak_wind_kt": 70.0, "peak_pressure_mb": 985.0,
            "max_category": "C1", "current_category": "TS",
            "ace": 3.4567, "start": "2026-06-01T00:00:00",
            "end": "2026-06-03T12:00:00",
            "points": [
                _pt("2026-06-01T00:00:00", A(0.30), L(0.60), 25.0, 1006.0, "TD", "DS"),
                _pt("2026-06-01T06:00:00", A(0.32), L(0.58), 40.0, 1000.0, "TS", "TS"),
                _pt("2026-06-01T12:00:00", A(0.34), L(0.56), 70.0, 985.0, "C1", "TS"),
                _pt("2026-06-01T18:00:00", A(0.36), L(0.54), 45.0, None, "TS", "TS"),
                _pt("2026-06-02T00:00:00", A(0.38), L(0.52), 50.0, 995.0, "TS", "TS"),
            ],
        },
        # 2: ACTIVE invest -> dashed line, white past triangles, deferred
        #    current SKIPPED in the second pass, bold red "L" icon.
        {
            "sid": "TEST_902", "name": "90X", "atcf_id": "90x",
            "is_active": True, "is_invest": True,
            "peak_wind_kt": 25.0, "peak_pressure_mb": 1008.0,
            "max_category": "TD", "current_category": "TD",
            "ace": 0.0, "start": "2026-06-02T00:00:00",
            "end": "2026-06-02T12:00:00",
            "points": [
                _pt("2026-06-02T00:00:00", A(0.20), L(0.30), 20.0, 1009.0, "TD", "DS"),
                _pt("2026-06-02T06:00:00", A(0.22), L(0.31), 25.0, 1008.0, "TD", "DS"),
                _pt("2026-06-02T12:00:00", A(0.24), L(0.32), 25.0, 1008.0, "TD", "DS"),
            ],
        },
        # 3: inactive invest -> past triangle + red glowing X + label.
        {
            "sid": "TEST_913", "name": "INVEST", "atcf_id": "91X",
            "is_active": False, "is_invest": True,
            "peak_wind_kt": 20.0, "peak_pressure_mb": 1010.0,
            "max_category": "TD", "current_category": "TD",
            "ace": 0.0, "start": "2026-05-28T00:00:00",
            "end": "2026-05-28T06:00:00",
            "points": [
                _pt("2026-05-28T00:00:00", A(0.15), L(0.70), 15.0, 1011.0, "TD", "DS"),
                _pt("2026-05-28T06:00:00", A(0.16), L(0.71), 20.0, 1010.0, "TD", "DS"),
            ],
        },
        # 4: active numbered TD (not an invest) -> hollow cyan ring.
        {
            "sid": "TEST_024", "name": "TWO", "atcf_id": "02X",
            "is_active": True, "is_invest": False,
            "peak_wind_kt": 30.0, "peak_pressure_mb": 1004.0,
            "max_category": "TD", "current_category": "TD",
            "ace": 0.0, "start": "2026-06-03T00:00:00",
            "end": "2026-06-03T06:00:00",
            "points": [
                _pt("2026-06-03T00:00:00", A(0.40), L(0.20), 25.0, 1006.0, "TD", "TS"),
                _pt("2026-06-03T06:00:00", A(0.42), L(0.21), 30.0, 1004.0, "TD", "TS"),
            ],
        },
        # 5: finished hurricane -> no marker; SS square, ET triangle,
        #    blank-nature circle, C2..C5 colors; null ACE -> "0.00";
        #    null start -> "-" date range; null peak pressure.
        {
            "sid": "TEST_055", "name": 'KAT"E', "atcf_id": None,
            "is_active": False, "is_invest": False,
            "peak_wind_kt": 140.0, "peak_pressure_mb": None,
            "max_category": "C5", "current_category": "TD",
            "ace": None, "start": None, "end": "2026-05-20T00:00:00",
            "points": [
                _pt("2026-05-15T00:00:00", A(0.50), L(0.80), 30.0, 1005.0, "TD", ""),
                _pt("2026-05-15T06:00:00", A(0.52), L(0.78), 60.0, 990.0, "TS", "SS"),
                _pt("2026-05-15T12:00:00", A(0.54), L(0.76), 80.0, 975.0, "C1", "NR"),
                _pt("2026-05-15T18:00:00", A(0.56), L(0.74), 90.0, 965.0, "C2", "TS"),
                _pt("2026-05-16T00:00:00", A(0.58), L(0.72), 100.0, 950.0, "C3", "TS"),
                _pt("2026-05-16T06:00:00", A(0.60), L(0.70), 120.0, 930.0, "C4", "TS"),
                _pt("2026-05-16T12:00:00", A(0.62), L(0.68), 140.0, 905.0, "C5", "TS"),
                _pt("2026-05-17T00:00:00", A(0.64), L(0.66), 50.0, 985.0, "TS", "ET"),
            ],
        },
        # 6: single-point storm -> no polyline; start == end date range.
        {
            "sid": "TEST_066", "name": "MONO", "atcf_id": None,
            "is_active": False, "is_invest": False,
            "peak_wind_kt": 35.0, "peak_pressure_mb": 1002.0,
            "max_category": "TS", "current_category": "TS",
            "ace": 0.1225, "start": "2026-05-10T06:00:00",
            "end": "2026-05-10T06:00:00",
            "points": [
                _pt("2026-05-10T06:00:00", A(0.25), L(0.45), 35.0, 1002.0, "TS", "TS"),
            ],
        },
        # 7: raw lon east of 180 -> exercises the per-point wrap branch
        #    (ep/al: lon -= 360) and, where it stays unwrapped (wp), the
        #    JUMP_THRESHOLD "M"-break in the polyline.
        {
            "sid": "TEST_077", "name": "WRAP", "atcf_id": None,
            "is_active": False, "is_invest": False,
            "peak_wind_kt": 45.0, "peak_pressure_mb": 998.0,
            "max_category": "TS", "current_category": "TS",
            "ace": 0.4225, "start": "2026-05-22T00:00:00",
            "end": "2026-05-22T06:00:00",
            "points": [
                _pt("2026-05-22T00:00:00", A(0.33), L(0.10), 40.0, 1000.0, "TS", "TS"),
                _pt("2026-05-22T06:00:00", A(0.35), 200.0, 45.0, 998.0, "TS", "TS"),
            ],
        },
    ]


def make_payload(basin: str, storms: list[dict]) -> dict:
    return {
        "storms": storms,
        "year": YEAR,
        "header": {"named": 3, "cat1plus": 2, "cat3plus": 1, "cat5": 1,
                   "total_ace": 50.978},
        "vocab": gtp.BASINS[basin]["vocab"],
        "fmt1_values": FMT1_VALUES,
        "fmt2_values": FMT2_VALUES,
    }


FRAGMENTS = ("tracks", "active", "cards", "panel_title", "stats",
             "fmt1", "fmt2")


@unittest.skipIf(NODE is None, "node not on PATH")
class TestOverlayParity(unittest.TestCase):
    maxDiff = None

    def assert_parity(self, basin: str, payload: dict) -> None:
        # Round-trip through JSON so Python sees the exact float values
        # the browser's JSON.parse would produce.
        payload = json.loads(json.dumps(payload))
        js = run_harness(basin, payload)
        py = python_fragments(basin, payload)
        for frag in FRAGMENTS:
            self.assertEqual(py[frag], js[frag],
                             f"{basin}: JS {frag} diverged from Python")

    def test_fixture_parity_all_basins(self):
        for basin in BASINS:
            with self.subTest(basin=basin):
                storms = make_fixture_storms(gtp.BASINS[basin]["extent"])
                self.assert_parity(basin, make_payload(basin, storms))

    def test_empty_season_parity(self):
        for basin in BASINS:
            with self.subTest(basin=basin):
                self.assert_parity(basin, make_payload(basin, []))


@unittest.skipIf(NODE is None, "node not on PATH")
@unittest.skipUnless(os.environ.get("LIVE_FEED_PARITY") == "1",
                     "set LIVE_FEED_PARITY=1 to run against the live R2 feeds")
class TestLiveFeedParity(unittest.TestCase):
    """The pre-merge side-by-side: prove the overlay renders the LIVE
    poller feed byte-identically to what the Python generator would bake
    from the same data."""
    maxDiff = None

    def test_live_feeds(self):
        for basin in BASINS:
            with self.subTest(basin=basin):
                url = f"{gtp.FEEDS_BASE_URL}{basin}_tracks_data.json"
                req = urllib.request.Request(
                    url, headers={"User-Agent": gtp.FETCH_UA})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    feed = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(feed.get("basin"), basin)
                payload = {
                    "storms": feed["storms"],
                    "year": feed["year"],
                    "header": feed["header"],
                    "vocab": feed["vocab"],
                }
                js = run_harness(basin, payload)
                py = python_fragments(basin, payload)
                for frag in ("tracks", "active", "cards", "panel_title",
                             "stats"):
                    self.assertEqual(py[frag], js[frag],
                                     f"{basin}: live-feed {frag} diverged")


if __name__ == "__main__":
    unittest.main()
