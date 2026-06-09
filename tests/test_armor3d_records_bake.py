"""Unit tests for the pure ARMOR3D TCHP records accumulator.

Drives bake_armor3d_tchp_records.update_minmax on synthetic per-year week
fields — no Copernicus Marine / no I/O — so the core MAX/MIN/year-stamp logic
the box runs for hours is proven before it ever touches the archive.
"""
import contextlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import bake_armor3d_tchp_records as bake
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


class TestRunPassResumeSafety(unittest.TestCase):
    """run_pass must never finalize/skip a year whose weeks failed to fetch
    (silent NaN holes past the resume marker), and must drop a completed
    year's raw weekly files (ENOSPC killed the 2026-06-09 run at year 2003)."""

    @contextlib.contextmanager
    def _state_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            with mock.patch.object(bake, "STATE_DIR", state), \
                 mock.patch.object(bake, "MARKER_PATH",
                                   state / "last_year_completed.txt"):
                yield state

    def test_fetch_failure_stops_loudly_without_marker(self):
        with self._state_dir(), \
             mock.patch.object(bake, "_fetch_year", return_value=([], 3)):
            with self.assertRaisesRegex(RuntimeError, "3 week fetch"):
                bake.run_pass(1993, 1994, None)
            self.assertIsNone(bake._read_marker())

    def test_zero_raws_aborts_instead_of_silent_skip(self):
        with self._state_dir(), \
             mock.patch.object(bake, "_fetch_year", return_value=([], 0)):
            with self.assertRaisesRegex(RuntimeError, "zero raw weeks"):
                bake.run_pass(1993, 1993, None)
            self.assertIsNone(bake._read_marker())

    def test_completed_year_writes_marker_and_drops_its_raws(self):
        with self._state_dir() as state:
            raw = state / "_raw_1993_w01.nc"
            raw.write_bytes(b"x")
            orphan = state / "_raw_1993_w02.nc.tmp123"  # crashed netCDF tmp
            orphan.write_bytes(b"y")
            other_year = state / "_raw_1994_w01.nc"
            other_year.write_bytes(b"z")
            with mock.patch.object(bake, "_fetch_year",
                                   return_value=([raw], 0)), \
                 mock.patch.object(bake, "_process_year") as proc:
                done = bake.run_pass(1993, 1993, None)
            self.assertEqual(done, 1)
            self.assertEqual(bake._read_marker(), 1993)
            proc.assert_called_once_with(1993, [raw])
            self.assertFalse(raw.exists())
            self.assertFalse(orphan.exists())
            self.assertTrue(other_year.exists())   # next year's cache kept

    def test_budget_stop_stays_clean(self):
        with self._state_dir(), \
             mock.patch.object(bake, "_fetch_year") as fetch:
            done = bake.run_pass(1993, 1993, time.monotonic() - 1.0)
            self.assertEqual(done, 0)
            self.assertIsNone(bake._read_marker())
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
