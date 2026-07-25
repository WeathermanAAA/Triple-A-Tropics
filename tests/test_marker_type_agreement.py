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

THE STAGE RULE (unified 2026-06-07): EVERY active designated (non-invest)
storm is "hurricane" — the spinning glyph whose letter/color come from
current_category, so the marker is current-stage-driven where it shows.
The old fork keyed a "td_circle" hollow ring on PEAK wind < 34 kt, so a
weakened storm (peaked TS, currently TD) and a freshly-designated TD at
the same current stage wore different markers (the AMANDA-glyph vs
TWO-E-ring inconsistency). TestSameStageSameMarker pins the rule,
including the brand-new-storm minimal-fields case.
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


def _storm(sid, *, is_active, is_invest, peak_wind_kt, is_ptc=False):
    return {
        "sid": sid, "name": sid,
        "atcf_id": "90E" if is_invest else ("01E" if is_ptc else None),
        "basin": "ep", "is_active": is_active, "is_invest": is_invest,
        "is_ptc": is_ptc,
        "peak_wind_kt": peak_wind_kt, "peak_pressure_mb": 1004.0,
        "max_category": "TD", "current_category": "TD",
        "ace": 0.0, "start": "2026-06-01T00:00:00",
        "end": "2026-06-01T06:00:00",
        "points": [dict(p) for p in _BASE_POINTS],
    }


# (storm, expected marker_type) — expectations follow the "Two flavors"
# block in ace_core/ace_core/__init__.py: invests are invest_x ALWAYS;
# every other ACTIVE storm is "hurricane" regardless of peak intensity
# (current_category drives the rendered letter, not the classification).
CASES = [
    (_storm("ACTIVE_INVEST", is_active=True, is_invest=True,
            peak_wind_kt=25.0), "invest_x"),
    (_storm("ACTIVE_STRONG_INVEST", is_active=True, is_invest=True,
            peak_wind_kt=40.0), "invest_x"),
    (_storm("ACTIVE_TD", is_active=True, is_invest=False,
            peak_wind_kt=30.0), "hurricane"),
    (_storm("ACTIVE_TS", is_active=True, is_invest=False,
            peak_wind_kt=50.0), "hurricane"),
    (_storm("INACTIVE_INVEST", is_active=False, is_invest=True,
            peak_wind_kt=20.0), "invest_x"),
    (_storm("FINISHED_TC", is_active=False, is_invest=False,
            peak_wind_kt=80.0), None),
    # peak None (brand-new storm, no wind yet): still the glyph; peak
    # plays no part in classification anymore. JS must agree.
    (_storm("ACTIVE_NO_PEAK", is_active=True, is_invest=False,
            peak_wind_kt=None), "hurricane"),
    # THE NUMBER RULE (2026-07-14, the TD 05E home-map bug): a Potential
    # Tropical Cyclone is a DESIGNATED system (01-89) and renders by
    # intensity — the glyph, NEVER the invest X. The old
    # PTC-wears-the-invest-identity marker design is retired; is_ptc still
    # dresses the popup + CycloLab page, just not the map marker.
    (_storm("ACTIVE_PTC", is_active=True, is_invest=False, is_ptc=True,
            peak_wind_kt=20.0), "hurricane"),
    (_storm("INACTIVE_PTC", is_active=False, is_invest=False, is_ptc=True,
            peak_wind_kt=20.0), "hurricane"),
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


class TestSameStageSameMarker(unittest.TestCase):
    """THE STAGE RULE: designated storms at the same CURRENT stage wear
    the same marker. The old fork keyed on peak_wind_kt, so AMANDA
    (peaked 40 kt, weakened to a 30 kt TD) wore the glyph while TWO-E
    (fresh TD, peak 30 kt) wore the hollow ring — different markers for
    the same current stage. Classification must ignore peak entirely;
    current_category only picks the letter/color INSIDE the glyph."""

    @staticmethod
    def _weakened_ex_ts():
        # AMANDA-shaped: peaked as a TS, currently a TD.
        s = _storm("WEAKENED_EX_TS", is_active=True, is_invest=False,
                   peak_wind_kt=40.0)
        s["max_category"] = "TS"
        return s

    @staticmethod
    def _fresh_td():
        # TWO-E-shaped: freshly designated, never exceeded TD strength.
        return _storm("FRESH_TD", is_active=True, is_invest=False,
                      peak_wind_kt=30.0)

    def test_same_stage_same_marker_type(self):
        self.assertEqual(ace_core_marker_type(self._weakened_ex_ts()),
                         ace_core_marker_type(self._fresh_td()))
        self.assertEqual(ace_core_marker_type(self._fresh_td()),
                         "hurricane")

    def test_brand_new_storm_minimal_fields(self):
        # A storm dict carrying ONLY what a just-designated system is
        # guaranteed to have (no peak, no current_category, no atcf_id,
        # no max_category) classifies exactly like an established storm.
        minimal = {
            "sid": "BRAND_NEW", "name": "TWO-E", "is_active": True,
            "points": [{"t": "2026-06-07T12:00:00", "lat": 15.4,
                        "lon": -100.0, "wind_kt": 30.0, "cls": "TD",
                        "nature": "TS"}],
        }
        self.assertEqual(ace_core_marker_type(minimal), "hurricane")
        self.assertEqual(ace_core_marker_type(minimal),
                         ace_core_marker_type(self._weakened_ex_ts()))

    def test_brand_new_storm_through_real_pipeline(self):
        # Strongest form: a single-advisory non-invest TD built through
        # merge_and_extract_storms (the REAL feed path) must classify
        # identically to the established weakened storm.
        import datetime as dt
        import pandas as pd
        from ace_core import merge_and_extract_storms
        now = dt.datetime.utcnow()
        anchor = now.replace(hour=(now.hour // 6) * 6, minute=0,
                             second=0, microsecond=0)
        rows = [{
            "SID": "NHC_EP022026", "NAME": "TWO-E", "season": 2026,
            "time": anchor - dt.timedelta(hours=6 * (1 - i)),
            "lat": 15.0 + i, "lon": -100.0 - i,
            "wind_kt": 30.0, "pressure_mb": 1005.0,
            "nature": "TS", "ace_nature": "TS",
            "source": "live-NHC", "storm_num": 2,
        } for i in range(2)]
        cfg = {"short": "ep", "agency_name": "NHC", "invest_letter": "E"}
        storms = merge_and_extract_storms(
            pd.DataFrame(), pd.DataFrame(rows), cfg)
        self.assertTrue(storms, "fresh TD missing from feed")
        self.assertFalse(storms[0]["is_invest"])
        self.assertTrue(storms[0]["is_active"])
        self.assertEqual(ace_core_marker_type(storms[0]), "hurricane",
                         "a freshly-designated TD must wear the glyph")

    @unittest.skipIf(NODE is None, "node not on PATH")
    def test_rendered_glyphs_identical_for_same_stage(self):
        # Beyond the type: the RENDERED markup for two same-stage storms
        # must be identical once name/sid/position are equalized — peak
        # intensity must leave no trace in the marker. Asserted on the
        # Python renderer and the JS mirror.
        a = self._weakened_ex_ts()
        b = self._fresh_td()
        for s in (a, b):
            s["name"] = "SAME"
            s["sid"] = "SAME_SID"
        a, b = (json.loads(json.dumps(s)) for s in (a, b))
        py_a = gtp.render_active_icons([a], gtp.BASINS["ep"]["extent"])
        py_b = gtp.render_active_icons([b], gtp.BASINS["ep"]["extent"])
        self.assertEqual(py_a, py_b,
                         "peak intensity leaked into the rendered marker")
        self.assertIn('class="active-icon"', py_a)
        self.assertNotIn("active-td", py_a)
        payload = {
            "year": 2026,
            "header": {"named": 0, "cat1plus": 0, "cat3plus": 0, "cat5": 0,
                       "total_ace": 0.0},
            "vocab": gtp.BASINS["ep"]["vocab"],
        }
        js_a = run_harness("ep", dict(payload, storms=[a]))["active"]
        js_b = run_harness("ep", dict(payload, storms=[b]))["active"]
        self.assertEqual(js_a, js_b,
                         "peak intensity leaked into the JS-rendered marker")
        self.assertEqual(js_a, py_a, "JS/Python marker parity broke")


class TestEveryPTCRendersByIntensity(unittest.TestCase):
    """The NUMBER rule through the REAL pipeline (2026-07-14, the TD 05E
    home-map bug): a DESIGNATED (01-49) DB/DS system NHC lists in
    CurrentStorms is activated as a Potential Tropical Cyclone (is_ptc)
    and renders like every designated system — the intensity glyph under
    its REAL designation, NEVER the invest X. The old PTC-wears-the-X
    design is retired; a regression that routes a PTC back to "invest_x"
    fails here."""

    def _rows(self, num):
        import datetime as dt
        now = dt.datetime.utcnow()
        anchor = now.replace(hour=(now.hour // 6) * 6, minute=0,
                             second=0, microsecond=0)
        return [{
            "SID": f"NHC_AL{num:02d}2026", "NAME": "ONE", "season": 2026,
            "time": anchor - dt.timedelta(hours=6 * (1 - i)),
            "lat": 26.0 + i, "lon": -97.0 - i,
            "wind_kt": 20.0, "pressure_mb": 1007.0,
            "nature": "DS", "ace_nature": "DS",   # DB/LO dev-level -> DS
            "source": "live-NHC", "storm_num": num,
        } for i in range(2)]

    def test_designated_db_in_currentstorms_renders_by_intensity(self):
        import pandas as pd
        from ace_core import merge_and_extract_storms
        cfg = {"short": "al", "agency_name": "NHC", "invest_letter": "L"}
        for num in (1, 5, 23, 49):
            with self.subTest(storm_num=num):
                storms = merge_and_extract_storms(
                    pd.DataFrame(), pd.DataFrame(self._rows(num)), cfg,
                    nhc_active_sids={f"AL{num:02d}2026": "DB"})
                s = next(st for st in storms
                         if st["sid"] == f"NHC_AL{num:02d}2026")
                self.assertTrue(s["is_ptc"])
                self.assertFalse(s["is_invest"])
                self.assertEqual(s["atcf_id"], f"{num:02d}L")
                self.assertEqual(ace_core_marker_type(s), "hurricane",
                                 f"{num:02d}L (designated) must wear the "
                                 f"intensity glyph, never the invest X")

    @unittest.skipIf(NODE is None, "node not on PATH")
    def test_ptc_marker_parity_python_js(self):
        # The rendered marker for a PTC must be byte-identical across the
        # Python renderer and the JS mirror (the per-basin live overlay).
        s = _storm("PTC_PARITY", is_active=True, is_invest=False,
                   is_ptc=True, peak_wind_kt=20.0)
        s = json.loads(json.dumps(s))
        payload = {
            "year": 2026,
            "header": {"named": 0, "cat1plus": 0, "cat3plus": 0, "cat5": 0,
                       "total_ace": 0.0},
            "vocab": gtp.BASINS["ep"]["vocab"],
        }
        # A designated PTC (01-89) DRAWS the spinning glyph now — and must
        # not also carry the invest X from the tracks second pass.
        py_active = gtp.render_active_icons([s], gtp.BASINS["ep"]["extent"])
        self.assertIn("active-icon", py_active,
                      "a designated PTC gets the intensity glyph")
        js = run_harness("ep", dict(payload, storms=[s]))
        self.assertEqual(js["active"], py_active,
                         "PTC active-layer parity broke (Python vs JS)")
        self.assertEqual(js["marker_types"][0], "hurricane")
        # The tracks layer must NOT give a PTC the invest X treatment
        # (that would double-draw with the glyph above).
        py_tracks = gtp.render_tracks_svg([s], gtp.BASINS["ep"]["extent"])
        self.assertNotIn("invest-current", py_tracks,
                         "a designated PTC must not draw the invest X")
        self.assertEqual(js["tracks"], py_tracks,
                         "PTC tracks-layer parity broke (Python vs JS)")


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
