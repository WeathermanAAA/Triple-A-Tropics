"""Unit tests for the pure ARMOR3D TCHP records accumulator.

Drives bake_armor3d_tchp_records.update_minmax on synthetic per-year week
fields — no Copernicus Marine / no I/O — so the core MAX/MIN/year-stamp logic
the box runs for hours is proven before it ever touches the archive.
"""
import contextlib
import datetime as dt
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import bake_armor3d_tchp_records as bake
import generate_armor3d_plots as a3d
from bake_armor3d_tchp_records import update_minmax, NO_YEAR


class CoordinatesOutOfDatasetBounds(Exception):
    """Name-alike of copernicusmarine's bounds exception (the classifier
    matches class names along the cause chain, never imports the package)."""


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


@contextlib.contextmanager
def _state_dir():
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        with mock.patch.object(bake, "STATE_DIR", state), \
             mock.patch.object(bake, "MARKER_PATH",
                               state / "last_year_completed.txt"), \
             mock.patch.object(bake, "BEYOND_MARKER_PATH",
                               state / "beyond_bounds_year.txt"):
            yield state


class TestRunPassResumeSafety(unittest.TestCase):
    """run_pass must never finalize/skip a year whose weeks failed to fetch
    (silent NaN holes past the resume marker), and must drop a completed
    year's raw weekly files (ENOSPC killed the 2026-06-09 run at year 2003)."""

    def _state_dir(self):
        return _state_dir()

    def test_fetch_failure_stops_loudly_without_marker(self):
        with self._state_dir(), \
             mock.patch.object(bake, "_fetch_year", return_value=([], 3, 0)):
            with self.assertRaisesRegex(RuntimeError, "3 week fetch"):
                bake.run_pass(1993, 1994, None)
            self.assertIsNone(bake._read_marker())

    def test_zero_raws_aborts_instead_of_silent_skip(self):
        with self._state_dir(), \
             mock.patch.object(bake, "_fetch_year", return_value=([], 0, 0)):
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
                                   return_value=([raw], 0, 0)), \
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


class TestEndOfArchiveDetection(unittest.TestCase):
    """A trailing year whose 52 weeks are ALL beyond the dataset's time bounds
    is the normal end of the reanalysis archive (it ends 2024-12-31 as of
    2026), NOT a failure: run_pass must stop clean, record the detected edge,
    and pass_is_complete must then report COMPLETE so finalize can run —
    while partial/mixed failures keep the loud halt."""

    def _state_dir(self):
        return _state_dir()

    def test_fully_beyond_year_completes_pass_at_marker(self):
        with self._state_dir():
            bake._write_marker(2024)            # 1993-2024 already baked
            with mock.patch.object(bake, "_fetch_year",
                                   return_value=([], 0, 52)):
                done = bake.run_pass(1993, 2025, None)   # probes only 2025
            self.assertEqual(done, 0)                    # no new year, no raise
            self.assertEqual(bake._read_marker(), 2024)  # marker untouched
            self.assertEqual(bake._read_beyond_year(), 2025)
            ok, why = bake.pass_is_complete(today=dt.date(2026, 6, 10))
            self.assertTrue(ok, why)
            self.assertIn("2024", why)

    def test_partially_beyond_year_still_halts_loudly(self):
        with self._state_dir() as state:
            raw = state / "_raw_2025_w01.nc"
            raw.write_bytes(b"x")
            with mock.patch.object(bake, "_fetch_year",
                                   return_value=([raw], 0, 26)):
                with self.assertRaisesRegex(RuntimeError,
                                            "beyond the dataset's time bounds"):
                    bake.run_pass(2025, 2025, None)
            self.assertIsNone(bake._read_beyond_year())  # no edge recorded
            self.assertIsNone(bake._read_marker())

    def test_beyond_plus_transient_failures_still_halt(self):
        with self._state_dir():
            with mock.patch.object(bake, "_fetch_year",
                                   return_value=([], 2, 50)):
                with self.assertRaisesRegex(RuntimeError, "2 week fetch"):
                    bake.run_pass(2025, 2025, None)
            self.assertIsNone(bake._read_beyond_year())

    def test_completed_year_at_edge_clears_stale_edge(self):
        # the archive extended past a previously-detected edge -> re-arm
        with self._state_dir() as state:
            bake._write_beyond_year(1993)
            raw = state / "_raw_1993_w01.nc"
            raw.write_bytes(b"x")
            with mock.patch.object(bake, "_fetch_year",
                                   return_value=([raw], 0, 0)), \
                 mock.patch.object(bake, "_process_year"):
                bake.run_pass(1993, 1993, None)
            self.assertEqual(bake._read_marker(), 1993)
            self.assertIsNone(bake._read_beyond_year())

    def test_pass_incomplete_without_edge_or_recent_marker(self):
        with self._state_dir():
            ok, _ = bake.pass_is_complete(today=dt.date(2026, 6, 10))
            self.assertFalse(ok)                        # no years at all
            bake._write_marker(2024)
            ok, why = bake.pass_is_complete(today=dt.date(2026, 6, 10))
            self.assertFalse(ok, why)   # no detected edge -> target is 2025

    def test_pass_complete_when_marker_reaches_head(self):
        with self._state_dir():
            bake._write_marker(2025)
            ok, why = bake.pass_is_complete(today=dt.date(2026, 6, 10))
            self.assertTrue(ok, why)


class TestCmemsBoundsClassification(unittest.TestCase):
    """CoordinatesOutOfDatasetBounds is PERMANENT for a given request —
    classify it distinctly and never burn retries on it."""

    def test_bounds_class_name_classified_beyond(self):
        exc = CoordinatesOutOfDatasetBounds("time exceeds bounds")
        self.assertEqual(a3d._classify_cmems_error(exc), "beyond_dataset")

    def test_bounds_in_cause_chain_classified_beyond(self):
        outer = RuntimeError("subset failed")
        outer.__cause__ = CoordinatesOutOfDatasetBounds("nope")
        self.assertEqual(a3d._classify_cmems_error(outer), "beyond_dataset")

    def test_bounds_message_marker_classified_beyond(self):
        exc = Exception("Some of your subset selection [2025-01-04 ..] for "
                        "the time dimension exceed the dataset coordinates "
                        "[1993-01-06 .. 2024-12-29]")
        self.assertEqual(a3d._classify_cmems_error(exc), "beyond_dataset")

    def test_existing_kinds_unchanged(self):
        self.assertEqual(a3d._classify_cmems_error(ConnectionError("x")),
                         "transient")
        fatal = type("InvalidUsernameOrPassword", (Exception,), {})("bad")
        self.assertEqual(a3d._classify_cmems_error(fatal), "fatal")
        self.assertEqual(a3d._classify_cmems_error(ValueError("boom")),
                         "other")

    def test_cmems_subset_does_not_retry_beyond_bounds(self):
        stub = types.SimpleNamespace(subset=mock.Mock(
            side_effect=CoordinatesOutOfDatasetBounds("exceed bounds")))
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(sys.modules, {"copernicusmarine": stub}), \
             mock.patch.object(a3d.time, "sleep",
                               side_effect=AssertionError("retried!")):
            with self.assertRaises(CoordinatesOutOfDatasetBounds):
                a3d._cmems_subset(
                    dataset_id="d", start=dt.datetime(2025, 1, 4),
                    end=dt.datetime(2025, 1, 4, 23, 59),
                    out_path=Path(tmp) / "out.nc")
        self.assertEqual(stub.subset.call_count, 1)   # single attempt


if __name__ == "__main__":
    unittest.main()
