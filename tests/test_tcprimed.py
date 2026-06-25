"""Unit tests for the tcprimed (observed passive-MW) product.

Network-free: exercises the channel map, the filename parser, the 89 PCT math
(from Kelvin), the 37 GHz color recipe, and the storm-id formatting on synthetic
arrays. The real render + R2 path is covered by the workflow's live build.
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tcprimed  # noqa: E402
from tcprimed import fetch as fx  # noqa: E402


class TestChannelMap(unittest.TestCase):
    def test_imager_sensors(self):
        # Only the three imager sensors with an 89/37 V/H pair are processed.
        self.assertEqual(set(tcprimed.IMAGER_SENSORS),
                         {"AMSR2", "GMI", "SSMIS"})
        # Sounders must NOT be in the channel map.
        self.assertNotIn("ATMS", tcprimed.PMW_CHANNELS)
        self.assertNotIn("MHS", tcprimed.PMW_CHANNELS)

    def test_channel_map_shape(self):
        for sensor, chans in tcprimed.PMW_CHANNELS.items():
            self.assertIn("37", chans)
            self.assertIn("89", chans)
            for band in ("37", "89"):
                group, v, h = chans[band]
                self.assertTrue(group.startswith("S"))
                self.assertTrue(v.startswith("TB_"))
                self.assertTrue(h.startswith("TB_"))
                self.assertTrue(v.endswith("V"))
                self.assertTrue(h.endswith("H"))

    def test_source_credit(self):
        # Archive credit = NOAA/CIRA TC-PRIMED; live credit = NASA GPM/PPS.
        self.assertEqual(tcprimed.SOURCE_ARCHIVE, "NOAA/CIRA TC-PRIMED")
        self.assertIn("GPM", tcprimed.SOURCE_LIVE)
        self.assertIn("TC-PRIMED", tcprimed.SOURCE)   # manifest covers both
        self.assertIn("GPM", tcprimed.SOURCE)
        # Never credited as TC-ATLAS anywhere.
        for s in (tcprimed.SOURCE, tcprimed.SOURCE_ARCHIVE,
                  tcprimed.SOURCE_LIVE, tcprimed.DISCLOSURE):
            self.assertNotIn("TC-ATLAS", s)


class TestFilenameParse(unittest.TestCase):
    def test_amsr2_helene(self):
        fn = ("TCPRIMED_v01r01-final_AL092024_AMSR2_GCOMW1_065717_"
              "20240924074222.nc")
        m = fx.parse_overpass_filename(fn)
        self.assertIsNotNone(m)
        self.assertEqual(m["atcf"], "AL092024")
        self.assertEqual(m["sensor"], "AMSR2")
        self.assertEqual(m["platform"], "GCOMW1")
        self.assertEqual(m["stamp"], "20240924074222")
        self.assertEqual(m["id"], "AMSR2_GCOMW1_20240924074222")
        self.assertEqual(m["valid"].year, 2024)
        self.assertEqual(m["valid"].hour, 7)

    def test_sounder_rejected(self):
        # ATMS / MHS sounders are not processable -> parser returns None.
        fn = ("TCPRIMED_v01r01-final_AL092024_ATMS_NOAA20_012345_"
              "20240924074222.nc")
        self.assertIsNone(fx.parse_overpass_filename(fn))

    def test_full_key_search(self):
        key = ("v01r01/final/2024/AL/09/"
               "TCPRIMED_v01r01-final_AL092024_GMI_GPM_065717_"
               "20240926232023.nc")
        m = fx.parse_overpass_filename(key.rsplit("/", 1)[-1])
        self.assertEqual(m["sensor"], "GMI")
        self.assertEqual(m["platform"], "GPM")

    def test_garbage_rejected(self):
        self.assertIsNone(fx.parse_overpass_filename("not_a_tcprimed_file.nc"))

    def test_atcf_parts(self):
        self.assertEqual(fx.atcf_parts("AL092024"), ("AL", "09", 2024))
        self.assertEqual(fx.atcf_parts("wp152023"), ("WP", "15", 2023))


class TestPCT(unittest.TestCase):
    """compute_pct89 works in degC; we feed it Tb(K) - 273.15. Clear ocean (V~=H,
    ~280 K) yields ~warm PCT; deep ice scattering (cold H depression) yields a
    much colder PCT than either raw channel."""

    def setUp(self):
        from hafs_render.hafs_plot import compute_pct89
        self.compute = compute_pct89

    def test_clear_ocean(self):
        # Over clear ocean V and H are both warm and near-equal -> PCT ~ V.
        v_k = np.array([280.0, 281.0, 279.0])
        h_k = np.array([278.0, 279.0, 277.0])
        pct_c = self.compute({0: v_k - 273.15, 1: h_k - 273.15}, 0, 1)
        pct_k = pct_c + 273.15
        self.assertTrue(np.all(pct_k > 270.0))
        self.assertTrue(np.all(pct_k < 295.0))

    def test_ice_scattering_colder(self):
        # Deep convective ice scattering brings BOTH channels cold, so the PCT is
        # far colder than over clear (warm) ocean -- the recognizable 89 signal.
        v_clear = np.array([280.0]) - 273.15
        h_clear = np.array([278.0]) - 273.15
        pct_clear = self.compute({0: v_clear, 1: h_clear}, 0, 1) + 273.15
        v_ice = np.array([180.0]) - 273.15
        h_ice = np.array([160.0]) - 273.15
        pct_ice = self.compute({0: v_ice, 1: h_ice}, 0, 1) + 273.15
        self.assertLess(float(pct_ice[0]), float(pct_clear[0]) - 50.0)
        self.assertLess(float(pct_ice[0]), 220.0)

    def test_physical_clip(self):
        # Single-pixel scattering overshoot is clipped to the [105, 290] K range.
        v_k = np.array([130.0])
        h_k = np.array([300.0])
        pct_c = self.compute({0: v_k - 273.15, 1: h_k - 273.15}, 0, 1)
        pct_k = pct_c + 273.15
        self.assertGreaterEqual(float(pct_k[0]), 105.0 - 1e-6)

    def test_nan_safe(self):
        v_k = np.array([280.0, np.nan])
        h_k = np.array([278.0, 270.0])
        pct_c = self.compute({0: v_k - 273.15, 1: h_k - 273.15}, 0, 1)
        self.assertTrue(np.isnan(pct_c[1]))
        self.assertFalse(np.isnan(pct_c[0]))


class TestColor37(unittest.TestCase):
    """Canonical NRL 37 GHz true-color RGB (no colormap):
        pct37 = 2.181*V - 1.181*H
        R = clip((280 - pct37)/20)   G = clip((V-180)/120)   B = clip((H-160)/140)
    Scene: green=clear ocean, cyan=warm rain, magenta=deep convection, red=ice."""

    def setUp(self):
        from tcprimed.render import _color37_rgba
        self.color = _color37_rgba

    @staticmethod
    def _expect(v, h):
        pct37 = 2.181 * v - 1.181 * h
        return (max(0.0, min(1.0, (280.0 - pct37) / 20.0)),
                max(0.0, min(1.0, (v - 180.0) / 120.0)),
                max(0.0, min(1.0, (h - 160.0) / 140.0)))

    def test_recipe_arithmetic(self):
        # Spot-check the exact per-channel arithmetic at a mid scene point.
        v, h = 240.0, 200.0
        rgba = self.color(np.array([[v]]), np.array([[h]]))
        er, eg, eb = self._expect(v, h)
        self.assertAlmostEqual(rgba[0, 0, 0], er, places=5)
        self.assertAlmostEqual(rgba[0, 0, 1], eg, places=5)
        self.assertAlmostEqual(rgba[0, 0, 2], eb, places=5)
        self.assertEqual(rgba[0, 0, 3], 1.0)

    def test_ice_scattering_reads_red(self):
        # Deep convection depresses both 37 channels -> low pct37 -> R saturates,
        # G/B fall to ~0: the scene reads RED.
        rgba = self.color(np.array([[180.0]]), np.array([[160.0]]))
        self.assertAlmostEqual(rgba[0, 0, 0], 1.0, places=5)   # R ice
        self.assertAlmostEqual(rgba[0, 0, 1], 0.0, places=5)   # G
        self.assertAlmostEqual(rgba[0, 0, 2], 0.0, places=5)   # B
        self.assertEqual(rgba[0, 0, 3], 1.0)

    def test_clear_ocean_reads_green(self):
        # Warm 37V over a lower 37H (clear ocean) -> high pct37 (no ice, R=0) and
        # G the dominant channel.
        rgba = self.color(np.array([[250.0]]), np.array([[190.0]]))
        self.assertAlmostEqual(rgba[0, 0, 0], 0.0, places=5)   # no ice -> no red
        self.assertGreater(rgba[0, 0, 1], rgba[0, 0, 2])       # G > B -> green
        self.assertEqual(rgba[0, 0, 3], 1.0)

    def test_alpha_invalid_transparent(self):
        # invalid / non-finite / Tb <= 0 in either channel -> transparent.
        v = np.array([[250.0, np.nan, -5.0, 250.0]])
        h = np.array([[190.0, 190.0, 190.0, 0.0]])
        rgba = self.color(v, h)
        self.assertEqual(rgba[0, 0, 3], 1.0)   # both valid -> opaque
        self.assertEqual(rgba[0, 1, 3], 0.0)   # NaN V -> transparent
        self.assertEqual(rgba[0, 2, 3], 0.0)   # V <= 0 -> transparent
        self.assertEqual(rgba[0, 3, 3], 0.0)   # H <= 0 -> transparent


class TestNRL89Colormap(unittest.TestCase):
    """The 89 PCT uses the canonical NRL table over Normalize(105, 305) K."""

    def test_norm_range(self):
        from tcprimed.render import nrl89_norm
        n = nrl89_norm()
        self.assertEqual((n.vmin, n.vmax), (105.0, 305.0))

    def test_anchor_endpoints_and_monotonic_positions(self):
        from tcprimed.render import _NRL89_ANCHORS_K, _NRL89_CMAP
        ks = [k for k, _ in _NRL89_ANCHORS_K]
        # 12 anchors spanning exactly the norm endpoints, strictly increasing.
        self.assertEqual(len(_NRL89_ANCHORS_K), 12)
        self.assertEqual((ks[0], ks[-1]), (105, 305))
        self.assertTrue(all(b > a for a, b in zip(ks, ks[1:])))
        # Cold end (deep ice scattering) is dark; warm end (clear ocean) is light
        # blue -- the real-MW look, not an IR ramp.
        cold = _NRL89_CMAP(0.0)
        warm = _NRL89_CMAP(1.0)
        self.assertLess(sum(cold[:3]), 0.8)        # dark gray at 105 K
        self.assertGreater(warm[2], warm[0])       # blue-dominant at 305 K


class TestStormId(unittest.TestCase):
    def test_short_name(self):
        from tcprimed.render import storm_short_name, storm_display_id
        self.assertEqual(storm_short_name("AL092024"), "09L")
        self.assertEqual(storm_short_name("EP032024"), "03E")
        self.assertEqual(storm_short_name("WP152023"), "15W")
        self.assertEqual(storm_display_id("AL092024"), "09L 2024")


class TestUnwrap(unittest.TestCase):
    """Longitude unwrap into a center-relative frame is dateline-safe."""

    def test_unwrap_atlantic(self):
        from tcprimed.render import _unwrap
        # Gulf storm center 277E; nearby 276/278 stay put.
        u = _unwrap(np.array([276.0, 277.0, 278.0]), 277.0)
        np.testing.assert_allclose(u, [276.0, 277.0, 278.0])

    def test_unwrap_dateline(self):
        from tcprimed.render import _unwrap
        # WPac storm center 179E; a 182E pixel and a 359/1E ring must map to a
        # CONTINUOUS frame around 179 (no 360-jump within the swath).
        u = _unwrap(np.array([178.0, 179.0, 182.0]), 179.0)
        np.testing.assert_allclose(u, [178.0, 179.0, 182.0])
        # A center near the seam: center 359E, pixel at 1E -> 361 (continuous).
        u2 = _unwrap(np.array([358.0, 359.0, 1.0]), 359.0)
        np.testing.assert_allclose(u2, [358.0, 359.0, 361.0])


class TestNativeRender(unittest.TestCase):
    """The native-swath pcolormesh renderer writes a real PNG for both products
    from a synthetic storm-centered swath (no network, no resample)."""

    def _synthetic_meta(self):
        import datetime as dt
        # 30x30 swath centered at (20N, 277E) with a cold convective core so PCT
        # spans the palette and 37 V/H differ (R != G).
        lat0, lon0 = 20.0, 277.0
        yy, xx = np.meshgrid(np.linspace(-2, 2, 30), np.linspace(-2, 2, 30))
        lat = lat0 + yy
        lon = lon0 + xx
        r2 = xx ** 2 + yy ** 2
        # warm ocean ~285 K, cold core ~150 K near center
        v89 = 285.0 - 130.0 * np.exp(-r2)
        h89 = v89 - 25.0 * np.exp(-r2)        # H colder in convection
        v37 = 250.0 - 30.0 * np.exp(-r2)
        h37 = 200.0 - 30.0 * np.exp(-r2)      # H < V over ocean
        return {
            "sensor": "GMI", "platform": "GPM", "atcf": "AL092024",
            "basin": "AL", "year": 2024,
            "valid": dt.datetime(2024, 9, 26, 23, 20, tzinfo=dt.timezone.utc),
            "intensity_kt": 118, "min_p_hpa": 938, "dev_level": "HU",
            "clat": lat0, "clon": lon0,
            "lat89": lat, "lon89": lon, "tb89v": v89, "tb89h": h89,
            "lat37": lat, "lon37": lon, "tb37v": v37, "tb37h": h37,
        }

    def test_render_both_products(self):
        import tempfile
        from tcprimed.render import render_89pct, render_37color
        meta = self._synthetic_meta()
        with tempfile.TemporaryDirectory() as d:
            p89 = os.path.join(d, "test_89pct.png")
            p37 = os.path.join(d, "test_37color.png")
            render_89pct(meta, p89)
            render_37color(meta, p37)
            self.assertGreater(os.path.getsize(p89), 2000)
            self.assertGreater(os.path.getsize(p37), 2000)

    def test_89pct_v_only_proxy(self):
        # When the 89 H channel is entirely fill (some SSMIS-F17 passes), the 89
        # product still renders from V-pol alone as a PCT proxy rather than failing.
        import tempfile
        from tcprimed.render import render_89pct
        meta = self._synthetic_meta()
        meta["tb89h"] = np.full_like(meta["tb89h"], np.nan)
        with tempfile.TemporaryDirectory() as d:
            p89 = os.path.join(d, "proxy_89pct.png")
            render_89pct(meta, p89)
            self.assertGreater(os.path.getsize(p89), 2000)

    def test_89pct_no_valid_pixels_raises(self):
        # No usable 89 data at all -> ValueError (caller skips the product).
        import tempfile
        from tcprimed.render import render_89pct
        meta = self._synthetic_meta()
        meta["tb89v"] = np.full_like(meta["tb89v"], np.nan)
        meta["tb89h"] = np.full_like(meta["tb89h"], np.nan)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                render_89pct(meta, os.path.join(d, "x.png"))


class TestLiveTier(unittest.TestCase):
    """Live/NRT tier (tcprimed.pps + tcprimed.storms), network-free."""

    def test_parse_nrt_filename(self):
        from tcprimed.pps import parse_1c_filename
        # Real NRT names (no orbit field, .RT-NC suffix).
        m = parse_1c_filename(
            "1C.GPM.GMI.XCAL2016-C.20260624-S223954-E224452.V08A.RT-NC")
        self.assertEqual(m["sensor"], "GMI")
        self.assertEqual(m["platform"], "GPM")
        self.assertEqual(m["start"].hour, 22)
        m2 = parse_1c_filename(
            "1C.F17.SSMIS.XCAL2021-V.20260624-S010000-E025000.V08A.RT-NC")
        self.assertEqual((m2["sensor"], m2["platform"]), ("SSMIS", "F17"))
        m3 = parse_1c_filename(
            "1C.GCOMW1.AMSR2.XCAL2016-V.20260624-S041622-E055220.V08A.RT-NC")
        self.assertEqual((m3["sensor"], m3["platform"]), ("AMSR2", "GCOMW1"))
        # Sounder / non-imager -> None (no 37/89 imager pair).
        self.assertIsNone(parse_1c_filename(
            "1C.NPP.ATMS.XCAL.20260624-S00-E01.V08A.RT-NC"))

    def test_crossing_midnight(self):
        from tcprimed.pps import parse_1c_filename
        m = parse_1c_filename(
            "1C.F16.SSMIS.XCAL2021-V.20260624-S222954-E001443.V08A.RT-NC")
        self.assertGreater(m["end"], m["start"])   # end rolled to next day

    def test_read_1c_synthetic(self):
        """read_1c indexes the right channels out of a synthetic GMI 1C /S1/Tc."""
        import tempfile, h5py
        from tcprimed.pps import read_1c
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "g.h5")
            ns, npx, nch = 6, 5, 9
            tc = np.zeros((ns, npx, nch), dtype="f4")
            # tag each channel with its index so we can assert the mapping
            for c in range(nch):
                tc[:, :, c] = 100.0 + c
            with h5py.File(p, "w") as h:
                s1 = h.create_group("S1")
                s1.create_dataset("Tc", data=tc)
                s1.create_dataset("Latitude", data=np.full((ns, npx), 20.0, "f4"))
                s1.create_dataset("Longitude", data=np.full((ns, npx), -80.0, "f4"))
            out = read_1c(p, "GMI", "GPM")
            # GMI: 37V=ch5,37H=ch6,89V=ch7,89H=ch8
            self.assertAlmostEqual(float(out["tb37v"][0, 0]), 105.0, places=4)
            self.assertAlmostEqual(float(out["tb37h"][0, 0]), 106.0, places=4)
            self.assertAlmostEqual(float(out["tb89v"][0, 0]), 107.0, places=4)
            self.assertAlmostEqual(float(out["tb89h"][0, 0]), 108.0, places=4)

    def test_crop_swath(self):
        from tcprimed.pps import crop_swath
        lat = np.linspace(0, 40, 41)[:, None] * np.ones((1, 30))
        lon = np.ones((41, 1)) * np.linspace(-100, -70, 30)[None, :]
        v = np.zeros((41, 30)); h = np.zeros((41, 30))
        c = crop_swath(lat, lon, v, h, 20.0, -85.0, pad=5.0)
        self.assertIsNotNone(c)
        clat, clon, _, _ = c
        self.assertTrue(15 - 1 <= clat.min() and clat.max() <= 25 + 1)
        # A storm well outside the swath -> None.
        self.assertIsNone(crop_swath(lat, lon, v, h, 80.0, 0.0, pad=5.0))

    def test_active_storms_parse(self):
        from tcprimed import storms as st
        gj = {"features": [
            {"properties": {"kind": "active_marker", "storm_id": "JTWC_WP072026",
                            "name": "MEKKHALA", "current_intensity_kt": 55,
                            "current_category": "TS", "marker_type": "hurricane",
                            "last_fix": "2026-06-24T18:00:00"},
             "geometry": {"type": "Point", "coordinates": [125.0, 22.6]}},
            {"properties": {"kind": "active_marker", "storm_id": "NHC_EP942026",
                            "name": "94E", "marker_type": "invest_x"},
             "geometry": {"type": "Point", "coordinates": [-117.5, 18.2]}},
            {"properties": {"kind": "track"},   # ignored
             "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}},
        ]}
        rows = st.anchors if False else None  # noqa
        # parse via the module's geojson handler by monkeypatching the fetch
        import json as _json
        import urllib.request as _u

        class _Resp:
            def __init__(self, b): self._b = b
            def read(self): return self._b
            def __enter__(self): return self
            def __exit__(self, *a): return False
        orig = _u.urlopen
        _u.urlopen = lambda *a, **k: _Resp(_json.dumps(gj).encode())
        try:
            out = st.active_storms(include_invests=True)
        finally:
            _u.urlopen = orig
        by = {s["slug"]: s for s in out}
        self.assertIn("wp072026", by)
        self.assertEqual(by["wp072026"]["name"], "MEKKHALA")
        self.assertEqual(by["wp072026"]["atcf"], "WP072026")
        self.assertAlmostEqual(by["wp072026"]["lon"], 125.0)
        self.assertTrue(by["ep942026"]["is_invest"])
        # invests excluded when asked
        _u.urlopen = lambda *a, **k: _Resp(_json.dumps(gj).encode())
        try:
            out2 = st.active_storms(include_invests=False)
        finally:
            _u.urlopen = orig
        self.assertNotIn("ep942026", {s["slug"] for s in out2})


if __name__ == "__main__":
    unittest.main()
