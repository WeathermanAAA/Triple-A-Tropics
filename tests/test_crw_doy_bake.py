"""Offline unit tests for the CRW day-of-year SST bakes.

Covers the PURE logic of build_crw_doy_climatology.py and
bake_crw_doy_records.py — NO network. Specifically:

  * DOY index contract (the leap-keyed date(2000,m,d).tm_yday mapping):
    Feb-29 → 60, Mar-01 → 61, Dec-31 → 366, plus round-trip via
    month_day_for_doy.
  * int16 pack/unpack round-trip (incl. NaN → fill).
  * belt-subset row slicing math on a synthetic lat axis.
  * fmax record accumulator + record_year update on synthetic arrays,
    including the -inf seed, NaN-standing, and tie cases.
  * resumability: an already-written per-DOY climo file is skipped by the
    bake_range driver (with a monkeypatched compute so the skip path runs
    without touching the network).
  * a 2-DOY SMOKE of build_crw_doy_climatology.bake_range with the CRW
    fetcher + native read monkeypatched to synthetic grids — proves the
    end-to-end write path (compute → pack → NetCDF → resume-skip) offline.

Network functions (gsp.fetch_crw_day, real CRW downloads) are never
exercised; they are monkeypatched to deterministic synthetic data.
"""
from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_crw_doy_climatology as climo  # noqa: E402
import bake_crw_doy_records as rec  # noqa: E402
from netCDF4 import Dataset  # noqa: E402


class DoyIndexContract(unittest.TestCase):
    def test_pinned_anchors(self):
        self.assertEqual(climo.doy_index(2, 29), 60)
        self.assertEqual(climo.doy_index(3, 1), 61)
        self.assertEqual(climo.doy_index(12, 31), 366)
        self.assertEqual(climo.doy_index(1, 1), 1)
        self.assertEqual(climo.doy_index(2, 28), 59)

    def test_round_trip_all_366(self):
        # Every 1..366 → (m,d) → index is identity.
        for doy in range(1, 367):
            m, d = climo.month_day_for_doy(doy)
            self.assertEqual(climo.doy_index(m, d), doy,
                             f"round-trip broke at DOY {doy} -> {m}-{d}")

    def test_records_share_the_same_contract(self):
        # The records bake imports doy_index from the climo module — same
        # function object, so the contract is provably identical.
        self.assertIs(rec.doy_index, climo.doy_index)
        self.assertEqual(rec.doy_index(2, 29), 60)


class Int16PackRoundTrip(unittest.TestCase):
    def test_roundtrip_within_quantum(self):
        rng = np.random.default_rng(0)
        # SST-like values plus some NaN.
        f = rng.uniform(-2.0, 35.0, size=(50, 80)).astype(np.float32)
        f[f < 0] = np.nan  # scatter some fill pixels
        packed = climo.pack_int16(f)
        back = climo.unpack_int16(packed)
        fin = np.isfinite(f)
        # Within half a quantum (scale 0.01 → 0.005).
        self.assertTrue(np.all(np.abs(back[fin] - f[fin]) <= 0.005 + 1e-6))
        # NaN pixels round-trip to fill → NaN.
        self.assertTrue(np.all(np.isnan(back[~fin])))
        self.assertTrue(np.all(packed[~fin] == climo.FILL_VALUE))

    def test_exact_values(self):
        f = np.array([[0.0, 12.34, -1.5]], np.float32)
        packed = climo.pack_int16(f)
        # (12.34 - 0)/0.01 = 1234.
        self.assertEqual(int(packed[0, 1]), 1234)
        self.assertEqual(int(packed[0, 0]), 0)
        back = climo.unpack_int16(packed)
        self.assertAlmostEqual(float(back[0, 1]), 12.34, places=2)


class BeltSubsetMath(unittest.TestCase):
    def test_belt_indices_bounds(self):
        # CRW native lat: -89.975 .. 89.975 at 0.05° (3600 rows). Use a
        # coarse synthetic ascending axis covering the belt edges.
        lat = np.arange(-89.975, 90.0, 0.05).astype(np.float32)
        mask = climo.belt_indices(lat)
        kept = lat[mask]
        self.assertGreaterEqual(float(kept.min()), climo.BELT_LAT_MIN)
        self.assertLessEqual(float(kept.max()), climo.BELT_LAT_MAX)
        # Nothing just outside the belt survives.
        below = lat[lat < climo.BELT_LAT_MIN]
        above = lat[lat > climo.BELT_LAT_MAX]
        self.assertTrue(np.all(~climo.belt_indices(below)))
        self.assertTrue(np.all(~climo.belt_indices(above)))

    def test_belt_subset_applied_to_grid(self):
        lat = np.linspace(-60.0, 60.0, 13).astype(np.float32)  # -60..60 step10
        grid = np.arange(13 * 5, dtype=np.float32).reshape(13, 5)
        mask = climo.belt_indices(lat)
        sub = grid[mask, :]
        # -40..50 inclusive on the step-10 axis → rows for -40,-30,...,50 = 10
        self.assertEqual(sub.shape, (10, 5))
        self.assertEqual(float(lat[mask].min()), -40.0)
        self.assertEqual(float(lat[mask].max()), 50.0)


class RecordAccumulator(unittest.TestCase):
    def test_seed_nan_and_tie(self):
        mx = np.array([[-np.inf, 10.0], [20.0, np.nan]], np.float32)
        yr = np.full((2, 2), rec.NO_RECORD_YEAR, np.int16)
        obs = np.array([[5.0, np.nan], [25.0, 8.0]], np.float32)
        rec.update_record(mx, yr, obs, 1990)
        # -inf seed takes obs; nan obs ignored; 25>20 wins; nan-standing
        # gets the finite obs (fmax) AND stamps the year.
        self.assertEqual(float(mx[0, 0]), 5.0)
        self.assertEqual(int(yr[0, 0]), 1990)
        self.assertEqual(float(mx[0, 1]), 10.0)
        self.assertEqual(int(yr[0, 1]), rec.NO_RECORD_YEAR)
        self.assertEqual(float(mx[1, 0]), 25.0)
        self.assertEqual(int(yr[1, 0]), 1990)
        self.assertEqual(float(mx[1, 1]), 8.0)
        self.assertEqual(int(yr[1, 1]), 1990)

    def test_tie_keeps_earlier_year(self):
        mx = np.array([[5.0]], np.float32)
        yr = np.array([[1990]], np.int16)
        rec.update_record(mx, yr, np.array([[5.0]], np.float32), 1995)
        self.assertEqual(float(mx[0, 0]), 5.0)
        self.assertEqual(int(yr[0, 0]), 1990)  # tie → earlier year stands

    def test_later_higher_overwrites_year(self):
        mx = np.array([[5.0]], np.float32)
        yr = np.array([[1990]], np.int16)
        rec.update_record(mx, yr, np.array([[7.0]], np.float32), 1995)
        self.assertEqual(float(mx[0, 0]), 7.0)
        self.assertEqual(int(yr[0, 0]), 1995)

    def test_idempotent_replay(self):
        # Re-applying the same date twice (resume re-runs a partial date)
        # must not change the result.
        mx = np.full((3, 3), -np.inf, np.float32)
        yr = np.full((3, 3), rec.NO_RECORD_YEAR, np.int16)
        obs = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], np.float32)
        rec.update_record(mx, yr, obs, 2001)
        snap_mx, snap_yr = mx.copy(), yr.copy()
        rec.update_record(mx, yr, obs, 2001)  # replay
        np.testing.assert_array_equal(mx, snap_mx)
        np.testing.assert_array_equal(yr, snap_yr)

    def test_record_year_optional(self):
        # year_acc=None path must still fmax cleanly.
        mx = np.array([[1.0, 2.0]], np.float32)
        rec.update_record(mx, None, np.array([[3.0, 1.0]], np.float32), 2010)
        np.testing.assert_array_equal(mx, np.array([[3.0, 2.0]], np.float32))


# --- Smoke + resumability with a monkeypatched fetcher ------------------


def _fake_native_grid(seed: int):
    """Deterministic (data, lat, lon) standing in for a CoralTemp file.

    Coarse synthetic grid spanning the belt edges + native -180..180 lon,
    ascending lat, with a couple of NaN (land) pixels."""
    lat = np.linspace(-50.0, 55.0, 22).astype(np.float32)   # spans belt
    lon = np.linspace(-180.0, 179.0, 30).astype(np.float32)  # native -180..180
    rng = np.random.default_rng(seed)
    data = rng.uniform(0.0, 32.0, size=(lat.size, lon.size)).astype(np.float32)
    # Mark a "land" pixel INSIDE the belt so the subset keeps it: pick the
    # row whose lat is closest to the equator (always within -40..50).
    eq_row = int(np.argmin(np.abs(lat)))
    data[eq_row, 0] = np.nan
    return data, lat, lon


class SmokeAndResume(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        # Redirect the bake's state dir + output paths into the tmp dir.
        self._orig_state = climo.STATE_DIR
        climo.STATE_DIR = self.tmp
        # Monkeypatch the network: fetch returns a sentinel path keyed by
        # date; our patched _read_crw_native turns that into a synthetic grid.
        self._orig_fetch = climo.gsp.fetch_crw_day
        self._orig_read = climo._read_crw_native

        def fake_fetch(d, product, log="", verbose=False):
            return Path(f"/synthetic/{d.isoformat()}.nc")  # never opened

        def fake_read(path):
            # Seed off the year embedded in the sentinel path so each year's
            # grid differs (exercises the streaming nanmean across years).
            stem = Path(path).stem  # e.g. 1991-07-15
            year = int(stem.split("-")[0])
            return _fake_native_grid(year)

        climo.gsp.fetch_crw_day = fake_fetch
        climo._read_crw_native = fake_read

    def tearDown(self):
        climo.STATE_DIR = self._orig_state
        climo.gsp.fetch_crw_day = self._orig_fetch
        climo._read_crw_native = self._orig_read
        self._tmp.cleanup()

    def test_two_doy_smoke_and_resume(self):
        # Bake DOY 200 and 201 (no upload, no remote check). Offline.
        rc = climo.bake_range(200, 201, upload=False,
                              skip_existing_remote=False)
        self.assertEqual(rc, 0)
        p200 = climo.doy_output_path(200)
        p201 = climo.doy_output_path(201)
        self.assertTrue(p200.exists() and p200.stat().st_size > 0)
        self.assertTrue(p201.exists())

        # Validate the written NetCDF: belt-only lat, native lon, int16 var,
        # contract attrs, and that unpacked values sit in the SST range.
        with Dataset(p200, "r") as ds:
            self.assertEqual(int(ds.doy_index), 200)
            self.assertEqual(ds.lon_convention, "-180..180")
            self.assertEqual(
                ds.doy_index_contract,
                "date(2000, month, day).timetuple().tm_yday")
            lat = ds.variables["lat"][:]
            lon = ds.variables["lon"][:]
            self.assertGreaterEqual(float(lat.min()), climo.BELT_LAT_MIN)
            self.assertLessEqual(float(lat.max()), climo.BELT_LAT_MAX)
            # Native lon retained (has negatives), NOT rolled to 0-360.
            self.assertLess(float(lon.min()), 0.0)
            v = ds.variables["sst_climo"]
            v.set_auto_maskandscale(False)
            unpacked = climo.unpack_int16(np.asarray(v[:]))
            fin = np.isfinite(unpacked)
            self.assertTrue(fin.any())
            self.assertTrue(np.all(unpacked[fin] >= -5.0))
            self.assertTrue(np.all(unpacked[fin] <= 40.0))
            # The equator-row land pixel (NaN in every year) stayed NaN
            # through the streaming mean. Its belt-output row is the
            # equator row's position within the belt subset.
            full_lat = np.linspace(-50.0, 55.0, 22).astype(np.float32)
            eq_full = int(np.argmin(np.abs(full_lat)))
            belt_mask = climo.belt_indices(full_lat)
            eq_belt_row = int(np.sum(belt_mask[:eq_full]))
            self.assertTrue(np.isnan(unpacked[eq_belt_row, 0]))

        # RESUMABILITY: re-run the same range. Existing files are skipped —
        # if they were re-baked, the call would re-open the (still
        # monkeypatched) fetcher; we assert the files' mtimes are unchanged.
        m200 = p200.stat().st_mtime_ns
        m201 = p201.stat().st_mtime_ns
        rc2 = climo.bake_range(200, 201, upload=False,
                               skip_existing_remote=False)
        self.assertEqual(rc2, 0)
        self.assertEqual(p200.stat().st_mtime_ns, m200,
                         "DOY 200 was re-baked despite existing (resume bug)")
        self.assertEqual(p201.stat().st_mtime_ns, m201)


if __name__ == "__main__":
    unittest.main()
