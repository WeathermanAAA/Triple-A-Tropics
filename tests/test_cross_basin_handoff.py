"""An invest must retire when a designated storm in ANOTHER basin takes it over.

Why this exists (2026-07-27): 92C, carried by the EP page as NHC_CP922026,
crossed the dateline and was designated 12W/DOLPHIN on the WP page as
JTWC_WP122026. Retirement in merge_and_extract_storms runs inside ONE basin's
frame, so the two never shared a DataFrame and the invest could not be retired
by any amount of correct SPAWNINVEST tagging. It survived on the 24 h staleness
window alone -- roughly half a day of the home map drawing one system as two:
a red X sitting beside a named storm on the same track.

The surviving sources give no explicit link (the WP b-deck carries no
SPAWNINVEST tag, and knackwx's transitioned_from said 93C -- an invest present
in neither the CP decks nor tcvitals), so the match is made on CONTINUITY, and
these tests pin both that it fires on the real geometry and that it does not
merge systems which merely happen to be near each other.
"""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "ace_core"))

from ace_core import (build_global_geojson,          # noqa: E402
                      cross_basin_superseded_sids)


def pt(t, lat, lon, kt=35):
    return {"time": t, "lat": lat, "lon": lon, "wind_kt": kt,
            "pressure_mb": 1000.0, "category": "TS"}


def storm(sid, basin, pts, *, invest=False, active=True, name="X"):
    return {"sid": sid, "basin": basin, "name": name, "points": pts,
            "is_invest": invest, "is_active": active,
            "peak_wind_kt": 40.0, "max_category": "TS",
            "current_category": "TS", "atcf_id": sid[-4:]}


# The real geometry, from the live UCAR tcvitals file:
#   JTWC 92C INVEST  20260726 1800 127N 1797E   (12.7N 179.7E)
#   JTWC 12W TWELVE  20260727 0000 ...          (designated, 6 h later)
INVEST_LAST = dt.datetime(2026, 7, 26, 18)
DESIG_FIRST = dt.datetime(2026, 7, 27, 0)


class TestCrossBasinHandoff(unittest.TestCase):
    def _real_pair(self):
        inv = storm("NHC_CP922026", "ep",
                    [pt(dt.datetime(2026, 7, 26, 12), 12.2, 178.9),
                     pt(INVEST_LAST, 12.7, 179.7)], invest=True, name="92C")
        des = storm("JTWC_WP122026", "wp",
                    [pt(DESIG_FIRST, 13.0, -179.4),        # just west of the dateline
                     pt(dt.datetime(2026, 7, 27, 6), 13.4, 176.7)], name="DOLPHIN")
        return inv, des

    def test_retires_on_the_real_92c_dolphin_geometry(self):
        inv, des = self._real_pair()
        self.assertEqual(cross_basin_superseded_sids([inv, des]),
                         {"NHC_CP922026"})

    def test_global_map_draws_one_marker_not_two(self):
        inv, des = self._real_pair()
        gj = build_global_geojson([inv, des])
        markers = [f for f in gj["features"]
                   if f["properties"].get("marker_type")]
        self.assertEqual(len(markers), 1, "one system must wear one marker")
        self.assertEqual(markers[0]["properties"].get("name"), "DOLPHIN")
        self.assertEqual(markers[0]["properties"].get("marker_type"), "hurricane")

    def test_antimeridian_distance_is_not_358_degrees(self):
        """179.7E -> 179.4W is 0.9 deg apart, not 359.1."""
        inv, des = self._real_pair()
        self.assertTrue(cross_basin_superseded_sids([inv, des]),
                        "a naive lon subtraction would put these ~21000 nm apart")

    def test_same_basin_pair_is_left_to_the_existing_logic(self):
        inv, des = self._real_pair()
        des["basin"] = "ep"                      # now same basin
        self.assertEqual(cross_basin_superseded_sids([inv, des]), set())

    def test_does_not_retire_a_distant_invest(self):
        inv, des = self._real_pair()
        des["points"] = [pt(DESIG_FIRST, 13.0, 150.0),
                         pt(dt.datetime(2026, 7, 27, 6), 13.4, 149.0)]
        self.assertEqual(cross_basin_superseded_sids([inv, des]), set())

    def test_does_not_retire_when_the_designation_came_first(self):
        """A storm designated BEFORE the invest's last fix is a different system."""
        inv, des = self._real_pair()
        des["points"] = [pt(dt.datetime(2026, 7, 25, 0), 13.0, -179.4),
                         pt(dt.datetime(2026, 7, 25, 6), 13.4, 176.7)]
        self.assertEqual(cross_basin_superseded_sids([inv, des]), set())

    def test_does_not_retire_across_a_long_gap(self):
        inv, des = self._real_pair()
        des["points"] = [pt(dt.datetime(2026, 7, 28, 12), 13.0, -179.4)]
        self.assertEqual(cross_basin_superseded_sids([inv, des]), set())

    def test_never_retires_a_designated_storm(self):
        inv, des = self._real_pair()
        inv["is_invest"] = False                 # two designated systems
        self.assertEqual(cross_basin_superseded_sids([inv, des]), set())

    def test_inactive_designation_does_not_retire(self):
        inv, des = self._real_pair()
        des["is_active"] = False
        self.assertEqual(cross_basin_superseded_sids([inv, des]), set())

    def test_no_storms_no_crash(self):
        self.assertEqual(cross_basin_superseded_sids([]), set())
        self.assertEqual(build_global_geojson([])["features"], [])


if __name__ == "__main__":
    unittest.main()
