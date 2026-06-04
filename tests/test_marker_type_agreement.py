"""The 4-way active/invest marker classification exists twice on purpose:

  * server: ace_core.build_global_geojson's marker_type fork (drives the
    global MapLibre map's active markers), and
  * client: LIVE_BASIN_JS markerType() (drives the per-basin live
    overlay's active icons + invest X routing).

This test asserts they agree on all four marker cases (plus the
no-marker case and the None-peak edge) so the two implementations cannot
drift silently. If this fails, ace_core's fork and the JS mirror were
edited out of lockstep — fix BOTH.
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


# (storm, expected marker_type) — expectations follow the "Four flavors"
# block in ace_core/ace_core/__init__.py.
CASES = [
    (_storm("ACTIVE_INVEST", is_active=True, is_invest=True,
            peak_wind_kt=25.0), "L"),
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
