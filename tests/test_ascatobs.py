"""Tests for the ASCAT ocean-winds ingest (ascatobs).

Covers the pieces that would silently corrupt the product if they regressed:
  * dataset/filename resolution (parse_filename),
  * NetCDF decode: quality/rain masking, m/s->kt, oceanographic->barb FROM-dir,
    decimation/stride, true overpass times, bbox, the empty-pass guard,
  * storm association: the 750 km distance test (dateline-aware) and the time
    gate, plus that the strict +/-3 h default differs from a generous pad,
  * manifest helpers: stable pass id + per-sat watermark.

The decode tests build a small SYNTHETIC NetCDF matching the real OSI SAF L2
coastal schema (packed ints + scale_factor/_FillValue), so they run in CI with
no network and no real granule. They are skipped if netCDF4 is unavailable.
"""
from __future__ import annotations

import datetime as dt
import os
import tempfile
import unittest

import numpy as np

import importlib

from ascatobs import fetch, storms
# the package re-exports build() the function, shadowing the submodule attribute,
# so reach the submodule (for its private helpers) via importlib.
build = importlib.import_module("ascatobs.build")

try:
    import netCDF4  # noqa: F401
    from ascatobs import decode
    _HAVE_NC = True
except Exception:  # noqa: BLE001
    _HAVE_NC = False

UTC = dt.timezone.utc


def _make_nc(path, *, nrows=12, ncells=8, base_lat=14.0, base_lon=-50.0,
             speed_ms=12.0, ocean_dir=90.0, flagged=None, fill=None,
             start=dt.datetime(2026, 6, 25, 23, 45, tzinfo=UTC)):
    """Write a synthetic ASCAT L2 coastal NetCDF (packed exactly like the real
    product) with a regular lat/lon grid, a uniform wind, and optional per-cell
    quality flags / fill. ``flagged``/``fill`` are sets of (row,cell)."""
    flagged = flagged or set()
    fill = fill or set()
    ds = netCDF4.Dataset(path, "w")
    ds.createDimension("NUMROWS", nrows)
    ds.createDimension("NUMCELLS", ncells)
    ds.comment = "All wind directions in oceanographic convention (0 deg. flowing North)"
    ds.source = "MetOp-B ASCAT"

    def mk(name, dtype, scale, fillv, units):
        v = ds.createVariable(name, dtype, ("NUMROWS", "NUMCELLS"),
                              fill_value=fillv)       # _FillValue must be set here
        v.scale_factor = scale
        v.add_offset = 0.0
        v.units = units
        return v

    lat = mk("lat", "i4", 1e-5, -2147483647, "degrees_north")
    lon = mk("lon", "i4", 1e-5, -2147483647, "degrees_east")
    spd = mk("wind_speed", "i2", 0.01, -32767, "m s-1")
    wdir = mk("wind_dir", "i2", 0.1, -32767, "degree")
    tim = mk("time", "i4", 1.0, -2147483647, "seconds since 1990-01-01 00:00:00")
    qc = ds.createVariable("wvc_quality_flag", "i4", ("NUMROWS", "NUMCELLS"),
                           fill_value=-2147483647)

    epoch = dt.datetime(1990, 1, 1, tzinfo=UTC)
    LA = np.full((nrows, ncells), np.nan)
    LO = np.full((nrows, ncells), np.nan)
    SP = np.full((nrows, ncells), np.nan)
    WD = np.full((nrows, ncells), np.nan)
    TI = np.full((nrows, ncells), np.nan)
    QC = np.zeros((nrows, ncells), dtype="int64")
    for r in range(nrows):
        for c in range(ncells):
            LA[r, c] = base_lat + r * 0.25
            LO[r, c] = base_lon + c * 0.25
            SP[r, c] = speed_ms
            WD[r, c] = ocean_dir
            TI[r, c] = (start + dt.timedelta(seconds=r * 60) - epoch).total_seconds()
            if (r, c) in flagged:
                QC[r, c] = decode.QC_KNMI            # a rejection bit
            if (r, c) in fill:
                SP[r, c] = np.nan                    # -> _FillValue on write
                LA[r, c] = np.nan
                LO[r, c] = np.nan
    lat[:] = np.ma.masked_invalid(LA)
    lon[:] = np.ma.masked_invalid(LO)
    spd[:] = np.ma.masked_invalid(SP)
    wdir[:] = np.ma.masked_invalid(WD)
    tim[:] = np.ma.masked_invalid(TI)
    qc[:] = QC
    ds.close()


class TestFilename(unittest.TestCase):
    def test_parse_b_and_c_gz_and_plain(self):
        m = fetch.parse_filename(
            "ascat_20260625_234500_metopb_71451_eps_o_coa_3301_ovw.l2.nc.gz")
        self.assertEqual(m["sat"], "metopb")
        self.assertEqual(m["orbit"], 71451)
        self.assertEqual(m["start"], dt.datetime(2026, 6, 25, 23, 45, tzinfo=UTC))
        m2 = fetch.parse_filename(
            "ascat_20260101_000600_metopc_39607_eps_o_coa_3301_ovw.l2.nc")
        self.assertEqual(m2["sat"], "metopc")

    def test_parse_rejects_junk(self):
        self.assertIsNone(fetch.parse_filename("notascat.nc"))
        self.assertIsNone(fetch.parse_filename(""))
        self.assertIsNone(fetch.parse_filename("ascat_bad_metopb.nc"))

    def test_datasets_pinned(self):
        self.assertEqual(fetch.DATASETS["metop-b"]["dataset"], "osisaf_ascat_b_coa")
        self.assertEqual(fetch.DATASETS["metop-c"]["dataset"], "osisaf_ascat_c_coa")
        self.assertEqual(fetch.DATASETS["metop-b"]["version"], "nrt")


@unittest.skipUnless(_HAVE_NC, "netCDF4 not available")
class TestDecode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ascat_test_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _nc(self, **kw):
        p = os.path.join(self.tmp, "t.nc")
        _make_nc(p, **kw)
        return p

    def test_quality_mask_bits(self):
        # rejection bits -> bad; land(15)/low(11)/high(12)/clean -> good
        qc = np.array([0, decode.QC_KNMI, decode.QC_VAR, decode.QC_MON,
                       decode.QC_RAIN, 1 << 15, 1 << 11, 1 << 12], dtype="int64")
        good = decode.quality_mask(qc)
        self.assertTrue(good[0])                      # clean
        for i in (1, 2, 3, 4):
            self.assertFalse(good[i], f"bit at idx {i} should reject")
        for i in (5, 6, 7):
            self.assertTrue(good[i], f"idx {i} (land/low/high) should keep")

    def test_decode_kt_and_from_dir(self):
        p = self._nc(speed_ms=10.0, ocean_dir=90.0)   # going-TO east
        d = decode.decode(p, sat="metopb", stride=1)
        self.assertEqual(d["sensor"], "ASCAT-B")
        self.assertTrue(d["n_wvc"] > 0)
        # 10 m/s -> ~19 kt
        self.assertTrue(all(k == round(10.0 * decode.MS_TO_KT) for k in d["wvc"]["kt"]))
        # oceanographic 90 (to East) -> barb FROM = 270 (from West)
        self.assertTrue(all(v == 270 for v in d["wvc"]["dir"]))

    def test_decode_masks_flagged_and_fill(self):
        p = self._nc(nrows=6, ncells=4, flagged={(0, 0), (1, 1)}, fill={(2, 2)})
        d = decode.decode(p, sat="metopb", stride=1)
        self.assertEqual(d["n_wvc"], 6 * 4 - 3)       # 3 cells dropped

    def test_decode_stride_decimates(self):
        p = self._nc(nrows=12, ncells=8)
        full = decode.decode(p, sat="metopb", stride=1)["n_wvc"]
        dec = decode.decode(p, sat="metopb", stride=2)["n_wvc"]
        self.assertLess(dec, full)
        self.assertGreaterEqual(dec, 1)

    def test_decode_empty_guard(self):
        all_cells = {(r, c) for r in range(6) for c in range(4)}
        p = self._nc(nrows=6, ncells=4, flagged=all_cells)
        d = decode.decode(p, sat="metopb", stride=1)
        self.assertEqual(d["n_wvc"], 0)
        self.assertEqual(d["max_kt"], 0.0)

    def test_decode_times_and_bbox(self):
        p = self._nc(nrows=10, ncells=6, base_lat=14.0, base_lon=-50.0)
        d = decode.decode(p, sat="metopb", stride=1)
        self.assertEqual(d["start_utc"], "2026-06-25T23:45:00Z")
        self.assertTrue(d["path"] and d["path"][0]["t"])
        w, e, s, n = d["bbox"]
        self.assertLessEqual(s, 14.0)
        self.assertGreaterEqual(n, 14.0 + 9 * 0.25)


class TestAssociate(unittest.TestCase):
    def _storm(self, lat, lon, last_fix):
        return {"slug": "al012026", "atcf": "AL012026", "name": "TEST",
                "basin": "AL", "year": 2026, "is_invest": False,
                "intensity_kt": 50, "lat": lat, "lon": lon, "last_fix": last_fix}

    def test_within_distance_and_time(self):
        # WVCs around (15,-50); storm at (15.2,-50.1) ~ a few km away
        la = [15.0, 15.1, 15.2]; lo = [-50.0, -50.1, -50.2]
        t = "2026-06-25T23:45:00Z"
        path = [{"lat": 15.1, "lon": -50.1, "t": t}]
        fix = dt.datetime(2026, 6, 25, 23, 0, tzinfo=UTC)   # 45 min from overpass
        hits = storms.associate([self._storm(15.2, -50.1, fix)], la, lo, path)
        self.assertEqual(len(hits), 1)
        self.assertLess(hits[0]["dist_km"], 100)
        self.assertEqual(hits[0]["overpass_utc"], t)

    def test_rejected_when_far(self):
        la = [15.0]; lo = [-50.0]
        path = [{"lat": 15.0, "lon": -50.0, "t": "2026-06-25T23:45:00Z"}]
        # storm 30 deg away (~3000 km) -> outside 750 km
        hits = storms.associate([self._storm(15.0, -20.0, None)], la, lo, path)
        self.assertEqual(hits, [])

    def test_time_gate_strict_vs_generous(self):
        la = [28.5]; lo = [129.3]
        path = [{"lat": 28.5, "lon": 129.3, "t": "2026-06-26T00:26:00Z"}]
        s = self._storm(28.5, 129.3, dt.datetime(2026, 6, 26, 12, 0, tzinfo=UTC))
        # 11.5 h apart: strict +/-3 h rejects, generous (window) pad keeps
        self.assertEqual(storms.associate([s], la, lo, path), [])
        keep = storms.associate([s], la, lo, path, max_dt_h=46.0)
        self.assertEqual(len(keep), 1)

    def test_dateline_distance(self):
        # WVCs just east of the antimeridian; storm just west of it -> near
        la = [10.0, 10.1]; lo = [-179.5, -179.6]
        path = [{"lat": 10.0, "lon": -179.5, "t": "2026-06-25T23:45:00Z"}]
        hits = storms.associate([self._storm(10.0, 179.5, None)], la, lo, path)
        self.assertEqual(len(hits), 1)
        self.assertLess(hits[0]["dist_km"], 200)


class TestBuildHelpers(unittest.TestCase):
    def test_pass_id(self):
        meta = {"sat": "metopb", "orbit": 71451,
                "start": dt.datetime(2026, 6, 25, 23, 45, tzinfo=UTC)}
        self.assertEqual(build._pass_id(meta), "metopb_71451_20260625T234500")

    def test_watermark_newest_per_sat(self):
        passes = [
            {"sat": "metopb", "start_utc": "2026-06-25T20:00:00Z"},
            {"sat": "metopb", "start_utc": "2026-06-25T23:45:00Z"},
            {"sat": "metopc", "start_utc": "2026-06-25T22:30:00Z"},
        ]
        wm = build._watermark(passes)
        self.assertEqual(wm["metopb"], "2026-06-25T23:45:00Z")
        self.assertEqual(wm["metopc"], "2026-06-25T22:30:00Z")

    def test_manifest_entry_has_no_wvc_arrays(self):
        p = {"sensor": "ASCAT-B", "sat": "metopb", "start_utc": "x", "end_utc": "y",
             "mid_utc": "m", "bbox": [1, 2, 3, 4], "n_wvc": 9, "max_kt": 42.0,
             "storms": [{"atcf": "AL012026"}], "wvc": {"la": [1]}}
        e = build._manifest_entry(p, "metopb_1_x")
        self.assertNotIn("wvc", e)
        self.assertEqual(e["id"], "metopb_1_x")
        self.assertEqual(e["n_wvc"], 9)
        self.assertEqual(e["storms"][0]["atcf"], "AL012026")


if __name__ == "__main__":
    unittest.main()
