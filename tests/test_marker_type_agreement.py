"""The marker classification exists twice on purpose:

  * server: ace_core.build_global_geojson's marker_type fork (drives the
    global MapLibre map's active markers), and
  * client: LIVE_BASIN_JS markerType() (drives the per-basin live
    overlay's active icons + invest X routing).

This test asserts they agree on every marker case (plus the no-marker
case and the None-peak edge) so the two implementations cannot drift
silently. If this fails, ace_core's fork and the JS mirror were edited
out of lockstep — fix BOTH.

THE INVEST RULE (unified 2026-06-05): an invest (ATCF 90-99) is
"invest_x" — the red NHC invest-area X — REGARDLESS of active state.
The old fork gave an ACTIVE invest a big red "L" instead, so two invests
could wear two different icons purely on fix freshness/dev-level (the
91W-L vs 91E-X inconsistency). TestEveryInvestGetsTheX pins the rule
through the REAL feed pipeline for the whole invest number range.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from overlay_test_util import NODE, gtp, run_harness  # noqa: E402

from ace_core import build_global_geojson  # noqa: E402

_BASE_POINTS = [
    {"t": "2026-06-01T00:00:00", "lat": 12.0, "lon": -120.0,
     "wind_kt": 25.0, "pressure_mb": 1006.0, "cls": "TD", "nature": "TS"},
    {"t": "2026-06-01T06:00:00", "lat": 12.5, "lon": -120.5,
     "wind_kt": 30.0, "pressure_mb": 1004.0, "cls": "TD", "nature": "TS"},
]


def _storm(sid, *, is_active, is_invest, peak_wind_kt):
    return {
        "sid": sid, "name": sid, "atcf_id": "90E" if is_invest else None,
        "basin": "ep", "is_active": is_active, "is_invest": is_invest,
        "peak_wind_kt": peak_wind_kt, "peak_pressure_mb": 1004.0,
        "max_category": "TD", "current_category": "TD",
        "ace": 0.0, "start": "2026-06-01T00:00:00",
        "end": "2026-06-01T06:00:00",
        "points": [dict(p) for p in _BASE_POINTS],
    }


# (storm, expected marker_type) — expectations follow the "Three flavors"
# block in ace_core/ace_core/__init__.py: invests are invest_x ALWAYS.
CASES = [
    (_storm("ACTIVE_INVEST", is_active=True, is_invest=True,
            peak_wind_kt=25.0), "invest_x"),
    (_storm("ACTIVE_STRONG_INVEST", is_active=True, is_invest=True,
            peak_wind_kt=40.0), "invest_x"),
    (_storm("ACTIVE_TD", is_active=True, is_invest=False,
            peak_wind_kt=30.0), "td_circle"),
    (_storm("ACTIVE_TS", is_active=True, is_invest=False,
            peak_wind_kt=50.0), "hurricane"),
    (_storm("INACTIVE_INVEST", is_active=False, is_invest=True,
            peak_wind_kt=20.0), "invest_x"),
    (_storm("FINISHED_TC", is_active=False, is_invest=False,
            peak_wind_kt=80.0), None),
    # peak None: ace_core substitutes 0.0 -> td_circle; JS must too.
    (_storm("ACTIVE_NO_PEAK", is_active=True, is_invest=False,
            peak_wind_kt=None), "td_circle"),
]


def ace_core_marker_type(storm: dict):
    fc = build_global_geojson([storm])
    markers = [f for f in fc["features"]
               if f["properties"]["kind"] == "active_marker"]
    return markers[0]["properties"]["marker_type"] if markers else None


class TestEveryInvestGetsTheX(unittest.TestCase):
    """The user-facing rule, pinned through the REAL pipeline: build live
    rows for EVERY invest storm number (90-99), once with a fresh
    tropical-coded fix (is_active True - the old "L" case) and once with
    a stale fix (is_active False), run merge_and_extract_storms ->
    build_global_geojson, and assert the marker is invest_x every time.
    A regression that re-splits invests by active state fails 10/20 of
    these immediately."""

    def _rows(self, num, *, active):
        """Two fresh 6-hourly fixes. active=True -> tropical-coded with
        wind (the 91W shape, old "L" case); active=False -> DB/LO-coded
        disturbance nature (the 91E shape: recent_invest, NOT is_active,
        always the X case)."""
        import datetime as dt
        now = dt.datetime.utcnow()
        anchor = now.replace(hour=(now.hour // 6) * 6, minute=0,
                             second=0, microsecond=0)
        nature = "TS" if active else "DS"
        return [{
            "SID": f"JTWC_WP{num}2026", "NAME": "INVEST", "season": 2026,
            "time": anchor - dt.timedelta(hours=6 * (1 - i)),
            "lat": 15.0 + i, "lon": 130.0 + i,
            "wind_kt": 25.0, "pressure_mb": 1004.0,
            "nature": nature, "ace_nature": nature,
            "source": "live-JTWC", "storm_num": num,
        } for i in range(2)]

    def test_invest_range_always_invest_x(self):
        import pandas as pd
        from ace_core import merge_and_extract_storms
        cfg = {"short": "wp", "agency_name": "JTWC", "invest_letter": "W"}
        for num in range(90, 100):
            for active in (True, False):
                with self.subTest(storm_num=num, active=active):
                    storms = merge_and_extract_storms(
                        pd.DataFrame(),
                        pd.DataFrame(self._rows(num, active=active)), cfg)
                    self.assertTrue(storms, f"{num} missing from feed")
                    self.assertTrue(storms[0]["is_invest"])
                    self.assertEqual(storms[0]["is_active"], active)
                    self.assertEqual(ace_core_marker_type(storms[0]),
                                     "invest_x",
                                     f"{num}W (active={active}) must wear the X")


@unittest.skipIf(NODE is None, "node not on PATH")
class TestMarkerTypeAgreement(unittest.TestCase):

    def test_agreement_on_all_cases(self):
        storms = [json.loads(json.dumps(s)) for s, _ in CASES]
        payload = {
            "storms": storms, "year": 2026,
            "header": {"named": 0, "cat1plus": 0, "cat3plus": 0, "cat5": 0,
                       "total_ace": 0.0},
            "vocab": gtp.BASINS["ep"]["vocab"],
        }
        js_types = run_harness("ep", payload)["marker_types"]
        for (storm, expected), js_mt in zip(CASES, js_types):
            with self.subTest(storm=storm["sid"]):
                py_mt = ace_core_marker_type(storm)
                self.assertEqual(py_mt, expected,
                                 f"ace_core classification changed for {storm['sid']}")
                self.assertEqual(js_mt, py_mt,
                                 f"JS markerType() disagrees with ace_core for {storm['sid']}")


if __name__ == "__main__":
    unittest.main()
