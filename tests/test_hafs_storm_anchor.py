#!/usr/bin/env python3
"""Unit tests for the HAFS storm-anchoring machinery (hafs_plot): the ATCF
track parser, the stat scope reductions, the radius mask (incl. dateline
wrap), and the fix snapping. Pure functions, no network, no GRIB.

Skipped wholesale when the hafs_render dep stack (matplotlib/herbie) is not
installed - the rest of the repo's test suite must keep running without it.
"""
import unittest

try:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hafs_render"))
    import numpy as np
    from hafs_render import hafs_plot as hp
except Exception as e:  # noqa: BLE001 - optional dep stack
    raise unittest.SkipTest(f"hafs_render stack unavailable: {e}")


DECK = """\
EP, 01, 2026060406, 03, HFSA, 000, 118N, 1294W,  37, 1007, XX,  34, NEQ, 0050, 0000, 0029, 0050, 1011
EP, 01, 2026060406, 03, HFSA, 006, 121N, 1299W,  69,  997, XX,  34, NEQ, 0050, 0037, 0037, 0047, 1005
EP, 01, 2026060406, 03, HFSA, 006, 121N, 1299W,  69,  997, XX,  50, NEQ, 0020, 0000, 0000, 0018, 1005
EP, 01, 2026060406, 03, HFSA, 006, 121N, 1299W,  69,  997, XX,  64, NEQ, 0011, 0000, 0000, 0010, 1005
WP, 06, 2026060406, 03, HFSA, 012, 153N, 1413E,  55,  990, XX,  34, NEQ, 0080, 0060, 0050, 0070, 1006
SH, 22, 2026060406, 03, HFSA, 018, 85S, 0621E,  45,  995, XX,  34, NEQ, 0, 0, 0, 0, 0
this line is junk and must be skipped
EP, 01, 2026060406, 03, HFSA, BAD, 118N, 1294W,  37, 1007
EP, 01, 2026060406, 03, HFSA, 024, XXXX, YYYY,  37, 1007, XX, 34, NEQ, 0, 0, 0, 0, 0
"""


class TestParseAtcfTrack(unittest.TestCase):
    def test_parses_positions_and_dedupes_radii_lines(self):
        track = hp.parse_atcf_track(DECK)
        # tau 006 appears 3x (34/50/64 kt radii) -> ONE fix; junk lines skipped
        self.assertEqual(sorted(track), [0, 6, 12, 18])
        self.assertEqual(track[0], (11.8, -129.4))      # 118N 1294W
        self.assertEqual(track[6], (12.1, -129.9))
        self.assertEqual(track[12], (15.3, 141.3))      # WP: 1413E
        self.assertEqual(track[18], (-8.5, 62.1))       # SH: 85S 0621E

    def test_empty_and_garbage_never_raise(self):
        self.assertEqual(hp.parse_atcf_track(""), {})
        self.assertEqual(hp.parse_atcf_track("total, garbage\nno, commas"), {})


def _mini_frame(lon, lat):
    """A minimal duck-typed frame for the mask/snap helpers (lon/lat only)."""
    class F:  # noqa: D401 - test stub
        pass
    f = F()
    f.lon = np.asarray(lon, dtype=float)
    f.lat = np.asarray(lat, dtype=float)
    return f


class TestRadiusMaskAndSnap(unittest.TestCase):
    def test_radius_mask_basic(self):
        f = _mini_frame(np.arange(-140.0, -119.9, 0.5), np.arange(0.0, 30.1, 0.5))
        m = hp._radius_mask(f, 15.0, -130.0, 3.0)
        LON, LAT = np.meshgrid(f.lon, f.lat)
        # the fix cell is inside; a cell 5 deg away is outside
        self.assertTrue(m[np.argmin(np.abs(f.lat - 15.0)),
                          np.argmin(np.abs(f.lon + 130.0))])
        self.assertFalse(m[np.argmin(np.abs(f.lat - 15.0)),
                           np.argmin(np.abs(f.lon + 135.5))])
        self.assertFalse(m[np.argmin(np.abs(f.lat - 20.0)),
                           np.argmin(np.abs(f.lon + 130.0))])

    def test_radius_mask_dateline_wrap(self):
        # WP continuous frame (168..188); a fix at -175 (=185 E) must wrap in
        f = _mini_frame(np.arange(168.0, 188.1, 0.5), np.arange(5.0, 25.1, 0.5))
        m = hp._radius_mask(f, 15.0, -175.0, 3.0)
        self.assertTrue(m[np.argmin(np.abs(f.lat - 15.0)),
                          np.argmin(np.abs(f.lon - 185.0))])

    def test_snap_fix_on_and_off_grid(self):
        f = _mini_frame(np.arange(-140.0, -119.9, 0.5), np.arange(0.0, 30.1, 0.5))
        self.assertEqual(hp._snap_fix(f, 15.02, -130.01), (15.0, -130.0))
        self.assertIsNone(hp._snap_fix(f, None, -130.0))
        self.assertIsNone(hp._snap_fix(f, 15.0, None))
        # far off the grid (> 1 deg margin) -> None, never a clamped wrong point
        self.assertIsNone(hp._snap_fix(f, 45.0, -130.0))
        self.assertIsNone(hp._snap_fix(f, 15.0, -60.0))


class TestStatScope(unittest.TestCase):
    def test_scope_reductions_respect_mask(self):
        arr = np.array([[1.0, 2.0], [3.0, 4.0]])
        mask = np.array([[True, False], [True, False]])
        s = hp.StatScope(mask=mask, tracked=True)
        self.assertEqual(hp.scope_max(arr, s), 3.0)
        self.assertEqual(hp.scope_min(arr, s), 1.0)
        self.assertEqual(hp.scope_mean(arr, s), 2.0)
        # domain scope (mask None) and None scope = whole-array reductions
        self.assertEqual(hp.scope_max(arr, hp.StatScope()), 4.0)
        self.assertEqual(hp.scope_max(arr, None), 4.0)

    def test_scope_all_nan_is_nan_not_raise(self):
        arr = np.full((2, 2), np.nan)
        s = hp.StatScope(mask=np.ones((2, 2), bool), tracked=True)
        self.assertTrue(np.isnan(hp.scope_max(arr, s)))
        self.assertTrue(np.isnan(hp.scope_mean(arr, s)))

    def test_mask_excluding_everything_is_nan(self):
        arr = np.array([[1.0, 2.0]])
        s = hp.StatScope(mask=np.zeros((1, 2), bool), tracked=True)
        self.assertTrue(np.isnan(hp.scope_min(arr, s)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
