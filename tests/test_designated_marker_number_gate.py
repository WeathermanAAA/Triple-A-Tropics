"""THE NUMBER GATE (2026-07-14): the ATCF storm number decides the map
marker — 90-99 wear the invest X; 01-89 are DESIGNATED systems and render
by intensity, invest flag / PTC status / stale feed stamps notwithstanding.

Live regression this locks: TD FIVE-E (NHC_EP052026, designation 05E,
promoted from invest 96E) rendered on the home map with the red invest X
because the marker fork routed is_ptc -> invest_x. A designated system
must NEVER wear the X.

    python -m unittest tests.test_designated_marker_number_gate
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
# Repo-source-first (the overlay_test_util convention): resolve
# `import ace_core` to ace_core/ace_core so an installed copy never
# green-lights a broken edit. Under `unittest discover`, an earlier test
# module may have cached the repo-root ace_core/ DIRECTORY as an empty
# namespace package — evict it so the real package wins.
sys.path.insert(0, str(REPO / "ace_core"))
import ace_core  # noqa: E402
if not hasattr(ace_core, "build_global_geojson"):
    sys.modules.pop("ace_core", None)
    import ace_core  # noqa: E402,F811

from ace_core import build_global_geojson, storm_is_invest, wears_invest_x  # noqa: E402

_POINTS = [
    {"t": "2026-07-14T12:00:00", "lat": 14.2, "lon": -108.1,
     "wind_kt": 25.0, "pressure_mb": 1007.0, "cls": "TD", "nature": "DS"},
    {"t": "2026-07-14T18:00:00", "lat": 14.7, "lon": -108.6,
     "wind_kt": 30.0, "pressure_mb": 1006.0, "cls": "TD", "nature": "DS"},
]


def _storm(**kw):
    base = {
        "sid": "NHC_EP052026", "name": "FIVE-E", "atcf_id": "05E",
        "basin": "ep", "is_active": True, "is_invest": False,
        "is_ptc": False, "peak_wind_kt": 30.0, "peak_pressure_mb": 1006.0,
        "max_category": "TD", "current_category": "TD", "ace": 0.0,
        "start": _POINTS[0]["t"], "end": _POINTS[-1]["t"],
        "points": [dict(p) for p in _POINTS],
    }
    base.update(kw)
    return base


def _marker(storm):
    fc = build_global_geojson([storm])
    ms = [f["properties"] for f in fc["features"]
          if f["properties"]["kind"] == "active_marker"]
    return ms[0] if ms else None


class TestDesignatedNeverWearsTheX(unittest.TestCase):
    def test_the_live_case_td_05e_as_ptc(self):
        # Exactly the shipped 05E feed row: designated, is_ptc True.
        m = _marker(_storm(is_ptc=True))
        self.assertIsNotNone(m)
        self.assertEqual(m["marker_type"], "hurricane")
        self.assertEqual(m["designation"], "05E")

    def test_designated_with_stale_invest_flag(self):
        # A promotion that left is_invest stuck True: the NUMBER wins.
        m = _marker(_storm(is_invest=True))
        self.assertEqual(m["marker_type"], "hurricane")

    def test_all_designated_numbers_render_by_intensity(self):
        for num in (1, 5, 12, 49, 89):
            with self.subTest(num=num):
                m = _marker(_storm(
                    sid=f"NHC_EP{num:02d}2026", atcf_id=f"{num:02d}E",
                    is_ptc=True))
                self.assertEqual(m["marker_type"], "hurricane")

    def test_all_invest_numbers_keep_the_x(self):
        for num in range(90, 100):
            with self.subTest(num=num):
                m = _marker(_storm(
                    sid=f"NHC_EP{num}2026", atcf_id=f"{num}E",
                    is_invest=True, is_active=False))
                self.assertEqual(m["marker_type"], "invest_x")

    def test_number_beats_flags_both_ways(self):
        # invest number + no flags at all -> still the X
        self.assertTrue(wears_invest_x(
            {"atcf_id": "91C", "sid": "NHC_CP912026"}))
        # designated number + every wrong flag -> never the X
        self.assertFalse(wears_invest_x(
            {"atcf_id": "05E", "sid": "NHC_EP052026",
             "is_invest": True, "is_ptc": True}))

    def test_flag_fallback_when_no_number_parseable(self):
        self.assertTrue(wears_invest_x({"sid": "WEIRD", "is_invest": True}))
        self.assertFalse(wears_invest_x({"sid": "WEIRD", "is_invest": False}))


class TestPromotionFlipsInvest(unittest.TestCase):
    """storm_is_invest: the NEWEST numbered fix decides, so an invest ->
    designated promotion whose merged history retains 90-99 rows flips to
    non-invest the moment its latest fixes wear the designated number."""

    def test_latest_designated_row_wins(self):
        pts = [{"storm_num": 96}, {"storm_num": 96}, {"storm_num": 5}]
        self.assertFalse(storm_is_invest(pts))

    def test_latest_invest_row_wins(self):
        pts = [{"storm_num": 96}, {"storm_num": None}]
        self.assertTrue(storm_is_invest(pts))

    def test_no_numbers_is_not_invest(self):
        self.assertFalse(storm_is_invest([{"storm_num": None}, {}]))


if __name__ == "__main__":
    unittest.main()
