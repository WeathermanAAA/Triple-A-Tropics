"""Unit tests for the pure ARMOR3D TCHP records accumulator.

Drives bake_armor3d_tchp_records.update_minmax on synthetic per-year week
fields — no Copernicus Marine / no I/O — so the core MAX/MIN/year-stamp logic
the box runs for hours is proven before it ever touches the archive.
"""
import unittest
import numpy as np

from bake_armor3d_tchp_records import update_minmax, NO_YEAR


def _fresh(shape):
    return (np.full(shape, -np.inf, np.float32), np.full(shape, NO_YEAR, np.int16),
            np.full(shape, np.inf, np.float32), np.full(shape, NO_YEAR, np.int16))


class TestUpdateMinMax(unittest.TestCase):
    def test_seed_then_extremes_and_year_stamps(self):
        rmax, ymax, rmin, ymin = _fresh((3,))
        update_minmax(rmax, ymax, rmin, ymin, np.array([10., 5., 8.], np.float32), 1993)
        np.testing.assert_array_equal(rmax, [10, 5, 8])
        np.testing.assert_array_equal(rmin, [10, 5, 8])
        np.testing.assert_array_equal(ymax, [1993, 1993, 1993])
        np.testing.assert_array_equal(ymin, [1993, 1993, 1993])
        # a hotter + a colder year
        update_minmax(rmax, ymax, rmin, ymin, np.array([12., 5., 2.], np.float32), 1998)
        np.testing.assert_array_equal(rmax, [12, 5, 8])   # cell0 new high
        np.testing.assert_array_equal(rmin, [10, 5, 2])   # cell2 new low
        np.testing.assert_array_equal(ymax, [1998, 1993, 1993])  # only cell0 re-stamped
        np.testing.assert_array_equal(ymin, [1993, 1993, 1998])  # only cell2 re-stamped

    def test_tie_keeps_earlier_year(self):
        rmax, ymax, rmin, ymin = _fresh((1,))
        update_minmax(rmax, ymax, rmin, ymin, np.array([7.], np.float32), 1995)
        update_minmax(rmax, ymax, rmin, ymin, np.array([7.], np.float32), 2005)  # tie
        self.assertEqual(int(ymax[0]), 1995)   # earlier year retained
        self.assertEqual(int(ymin[0]), 1995)

    def test_nan_obs_ignored(self):
        rmax, ymax, rmin, ymin = _fresh((2,))
        update_minmax(rmax, ymax, rmin, ymin, np.array([4., 9.], np.float32), 2000)
        update_minmax(rmax, ymax, rmin, ymin, np.array([np.nan, 1.], np.float32), 2010)
        np.testing.assert_array_equal(rmax, [4, 9])           # cell0 unchanged
        np.testing.assert_array_equal(rmin, [4, 1])           # cell1 new low
        np.testing.assert_array_equal(ymax, [2000, 2000])     # no NaN re-stamp
        np.testing.assert_array_equal(ymin, [2000, 2010])

    def test_never_observed_stays_seed(self):
        rmax, ymax, rmin, ymin = _fresh((1,))
        update_minmax(rmax, ymax, rmin, ymin, np.array([np.nan], np.float32), 1993)
        self.assertFalse(np.isfinite(rmax[0]))               # still -inf seed
        self.assertEqual(int(ymax[0]), int(NO_YEAR))          # never stamped


if __name__ == "__main__":
    unittest.main()
