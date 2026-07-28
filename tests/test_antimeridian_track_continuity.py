"""A dateline split must not read as a hole in the observations.

Why this exists (2026-07-28): the home map's 2026 Global TC Tracks layer
showed a visible break mid-track on DOLPHIN (JTWC_WP122026), with the
non-tropical triangle markers of a weak TD either side of it. The fixes were
NOT missing -- the b-deck is continuous 6-hourly from 07-25T00 to 07-28T12,
and the break sat exactly on the antimeridian crossing between the 07-26T12
fix at -178.9 and the 07-26T18 fix at +179.8.

``_split_at_antimeridian`` correctly refuses to emit a LineString that crosses
+/-180 (GeoJSON forbids it; a renderer would draw a horizontal line across the
whole world), but it used to cut BETWEEN the two real fixes and hand each half
to the map ending at its own last fix. The halves therefore stopped 1.1 deg and
0.2 deg short of the dateline, leaving a ~1.3 deg hole that looked exactly like
missing data.

The split now carries the crossing point: the latitude where the leg meets
+/-180 is interpolated and appended to the outgoing half as +/-180 and
prepended to the incoming half as -/+180, so the two abut on the dateline.

The honesty half of the contract matters as much as the continuity half: only
the leg that actually crosses +/-180 gets an inserted vertex. A genuine
reporting gap -- a storm with hours of missing fixes -- still renders as a
break, because nothing interpolates across it.
"""
import importlib.util
import os
import unittest

# Load ace_core from THIS WORKING TREE by explicit path, not via sys.path.
# A built ace-core is usually pip-installed in the same environment (the
# generators do a plain `import ace_core`), and under `unittest discover` an
# earlier test module can import that copy first - after which sys.modules
# caches it and a later sys.path.insert is a no-op. The suite then silently
# tests the INSTALLED package instead of the tree, which is how a change to
# this file can look green while never having been executed at all.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ACE_INIT = os.path.join(_REPO, "ace_core", "ace_core", "__init__.py")
_spec = importlib.util.spec_from_file_location("_ace_core_worktree",
                                               _ACE_INIT)
_ace = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ace)

_split_at_antimeridian = _ace._split_at_antimeridian

# The real JTWC WP12 2026 b-deck track, [lon, lat], in time order. Westbound;
# the crossing sits between index 6 (-178.9) and index 7 (+179.8).
DOLPHIN = [
    [-171.6, 10.4], [-172.7, 10.7], [-173.8, 11.0], [-174.9, 11.4],
    [-176.0, 11.7], [-177.4, 12.0], [-178.9, 12.5], [179.8, 12.8],
    [178.3, 13.1], [176.7, 13.2], [175.2, 13.2], [173.7, 13.1],
    [172.8, 13.1], [171.7, 13.3], [170.7, 13.4],
]


class TestAntimeridianSplit(unittest.TestCase):

    def test_exercises_the_working_tree_not_an_installed_copy(self):
        self.assertEqual(os.path.realpath(_ace.__file__),
                         os.path.realpath(_ACE_INIT),
                         "these tests must run against the repo's ace_core")

    def _assert_segments_abut(self, segs):
        """Consecutive segments must meet exactly on the dateline."""
        for a, b in zip(segs, segs[1:]):
            self.assertEqual(abs(a[-1][0]), 180.0,
                             f"outgoing segment ends at {a[-1]}, not +/-180")
            self.assertEqual(abs(b[0][0]), 180.0,
                             f"incoming segment starts at {b[0]}, not +/-180")
            self.assertEqual(a[-1][0], -b[0][0],
                             "the two halves must meet on OPPOSITE signs of "
                             "the same meridian")
            self.assertAlmostEqual(
                a[-1][1], b[0][1], places=6,
                msg=f"latitude jumps across the dateline: {a[-1]} -> {b[0]}")

    def test_dolphin_track_has_no_hole_at_the_dateline(self):
        segs = _split_at_antimeridian(DOLPHIN)
        self.assertEqual(len(segs), 2, "one crossing -> exactly two segments")
        self._assert_segments_abut(segs)
        # every original fix survives, in order, on exactly one segment
        emitted = [c for s in segs for c in s if abs(c[0]) != 180.0]
        self.assertEqual(emitted, DOLPHIN,
                         "splitting dropped or reordered real fixes")

    def test_crossing_latitude_is_interpolated_not_snapped(self):
        # -178.9 -> 179.8 unwraps to a 1.3 deg leg; +/-180 sits 1.1 deg along
        # it, so lat = 12.5 + (12.8-12.5) * (1.1/1.3) = 12.7538...
        segs = _split_at_antimeridian(DOLPHIN)
        self.assertAlmostEqual(segs[0][-1][1], 12.7538, places=3)
        self.assertNotIn(segs[0][-1][1], (12.5, 12.8),
                         "crossing latitude was snapped to a neighbour "
                         "instead of interpolated")

    def test_eastbound_crossing(self):
        segs = _split_at_antimeridian(
            [[178.0, 10.0], [179.5, 11.0], [-179.0, 12.0], [-177.0, 13.0]])
        self.assertEqual(len(segs), 2)
        self._assert_segments_abut(segs)
        self.assertEqual(segs[0][-1][0], 180.0, "eastbound exits at +180")
        self.assertEqual(segs[1][0][0], -180.0, "eastbound re-enters at -180")

    def test_fix_exactly_on_the_dateline_is_not_duplicated(self):
        for edge in (180.0, -180.0):
            with self.subTest(edge=edge):
                segs = _split_at_antimeridian(
                    [[178.0, 10.0], [edge, 11.0], [-178.0, 12.0]])
                self.assertEqual(len(segs), 2)
                self._assert_segments_abut(segs)
                for s in segs:
                    self.assertEqual(len(s), len(set(map(tuple, s))),
                                     f"duplicate vertex in {s}")

    def test_multiple_crossings(self):
        segs = _split_at_antimeridian(
            [[179.0, 10.0], [-179.0, 11.0], [179.0, 12.0], [178.0, 13.0]])
        self.assertEqual(len(segs), 3, "two crossings -> three segments")
        self._assert_segments_abut(segs)

    # ---- the parts that must NOT change -------------------------------

    def test_non_crossing_track_is_untouched(self):
        atlantic = [[-60.0, 15.0], [-62.0, 16.0], [-64.0, 17.0]]
        self.assertEqual(_split_at_antimeridian(atlantic), [atlantic],
                         "a storm that never crosses +/-180 must pass "
                         "through unchanged")

    def test_a_real_gap_still_renders_as_a_break(self):
        # Two systems' worth of fixes with a wide gap that does NOT cross the
        # dateline: nothing is inserted, and it stays ONE polyline -- the
        # splitter's job is the dateline, not gap detection. What matters is
        # that it invents no geometry.
        gappy = [[150.0, 10.0], [152.0, 11.0], [175.0, 20.0], [176.0, 21.0]]
        self.assertEqual(_split_at_antimeridian(gappy), [gappy])

    def test_coincident_dateline_pair_emits_no_world_spanning_segment(self):
        # +180 and -180 are the SAME meridian: numerically 360 apart, so the
        # crossing test fires, but there is no leg to bridge. Bridging it
        # anyway produced a segment running 180 -> -180, i.e. a line across
        # the entire map - the exact artifact this function exists to prevent.
        segs = _split_at_antimeridian(
            [[180.0, 10.0], [-180.0, 12.0], [-178.0, 13.0]])
        for s in segs:
            lons = [c[0] for c in s]
            self.assertLess(
                max(lons) - min(lons), 360.0,
                f"segment sweeps the whole world: {s}")
            self.assertNotIn(
                [180.0, -180.0], [[lons[i], lons[i + 1]]
                                  for i in range(len(lons) - 1)],
                f"world-spanning leg in {s}")

    def test_crossing_vertex_is_never_extrapolated_off_the_leg(self):
        # A feed using the 0-360 convention (lon 185) puts +/-180 OUTSIDE the
        # [prev, curr] interval, so an unclamped interpolation invents a
        # latitude outside both fixes (10 and 12 -> a vertex at 8).
        segs = _split_at_antimeridian([[185.0, 10.0], [-170.0, 12.0]])
        inserted = [c for s in segs for c in s if abs(c[0]) == 180.0]
        self.assertTrue(inserted, "expected a crossing vertex")
        for lon, lat in inserted:
            self.assertGreaterEqual(lat, 10.0)
            self.assertLessEqual(lat, 12.0)

    def test_degenerate_inputs(self):
        self.assertEqual(_split_at_antimeridian([]), [])
        self.assertEqual(_split_at_antimeridian([[179.0, 10.0]]), [],
                         "a single fix cannot be a LineString")

    def test_every_segment_is_a_valid_linestring(self):
        for name, track in (("dolphin", DOLPHIN),
                            ("two-crossing", [[179.0, 10.0], [-179.0, 11.0],
                                              [179.0, 12.0]])):
            with self.subTest(name):
                for s in _split_at_antimeridian(track):
                    self.assertGreaterEqual(len(s), 2)
                    for lon, lat in s:
                        self.assertGreaterEqual(lon, -180.0)
                        self.assertLessEqual(lon, 180.0)
                        self.assertGreaterEqual(lat, -90.0)
                        self.assertLessEqual(lat, 90.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
