"""sarobs units: listing parse (year block scoping, basin filter), stem
parsing, pass discovery, the build tick's watermark/gate/budget behavior
against a local store, and the color scale contract."""
import datetime as dt
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sarobs import build as sbuild  # noqa: E402
from sarobs import discover  # noqa: E402
from sarobs.store import LocalStore, make_store  # noqa: E402

# Trimmed real listing shapes (from the source pages).
YEAR_HTML = """
<div class="dropdown-menu">
<a class="dropdown-item" href="?year=2025&storm=AL092025_HUMBERTO">AL092025 / HUMBERTO</a>
<a class="dropdown-item" href="?year=2026&storm=WP072026_MEKKHALA">WP072026 / MEKKHALA</a>
</div>
<div id="2025"><ul><h3>Atlantic</h3>
<a href="?year=2025&storm=AL092025_HUMBERTO">AL092025 / HUMBERTO</a></br></ul></div>
<div id="2026"><ul><h3>Atlantic</h3>
<a href="?year=2026&storm=AL012026_ARTHUR">AL012026 / ARTHUR</a></br></ul>
<ul><h3>Eastern Pacific</h3>
<a href="?year=2026&storm=EP052026_ELIDA">EP052026 / ELIDA</a></br></ul>
<ul><h3>Southern Hemisphere</h3>
<a href="?year=2026&storm=SH282026_TWENTYEIGH">SH282026 / TWENTYEIGH</a></br></ul>
<ul><h3>Western Pacific</h3>
<a href="?year=2026&storm=WP072026_MEKKHALA">WP072026 / MEKKHALA</a></br></ul></div>
"""

STORM_HTML = """
<a href="AKDEMO_products/APL_winds/tropical/2026/AL012026_ARTHUR/RCM1_SHUB_2026_06_18_00_16_31_0835056991_093.70W_29.71N_VH_C-12_MERGED01_wind_level2.nc">nc</a>
<a href="AKDEMO_products/APL_winds/tropical/2026/AL012026_ARTHUR/RCM3_SHUB_2026_06_17_12_29_56_0835014596_096.84W_27.73N_VH_C-12_MERGED01_wind_level2.nc">nc</a>
<a href="AKDEMO_products/APL_winds/tropical/2026/AL012026_ARTHUR/RCM1_SHUB_2026_06_18_00_16_31_0835056991_093.70W_29.71N_VH_C-12_MERGED01_wind.png">png</a>
"""


class Discovery(unittest.TestCase):
    def test_year_block_scopes_to_requested_year(self):
        ids = discover.storms_for_year(2026, html=YEAR_HTML)
        self.assertIn("AL012026_ARTHUR", ids)
        self.assertIn("EP052026_ELIDA", ids)
        self.assertIn("WP072026_MEKKHALA", ids)
        self.assertNotIn("AL092025_HUMBERTO", ids)   # other year's block
        self.assertNotIn("SH282026_TWENTYEIGH", ids)  # out-of-scope basin

    def test_prior_year_block(self):
        self.assertEqual(discover.storms_for_year(2025, html=YEAR_HTML),
                         ["AL092025_HUMBERTO"])

    def test_passes_parse_and_order(self):
        ps = discover.passes_for_storm(2026, "AL012026_ARTHUR",
                                       html=STORM_HTML)
        self.assertEqual(len(ps), 2)               # png ignored, nc deduped
        self.assertTrue(ps[0]["t"] > ps[1]["t"])   # newest first
        self.assertEqual(ps[0]["sat"], "RCM1")
        self.assertEqual(ps[0]["pol"], "VH")
        self.assertTrue(ps[0]["url"].endswith("_wind_level2.nc"))

    def test_stem_fields(self):
        stem = ("RCM1_SHUB_2026_06_18_00_16_31_0835056991_093.70W_29.71N"
                "_VH_C-12_MERGED01")
        t = discover.stem_time(stem)
        self.assertEqual(t, dt.datetime(2026, 6, 18, 0, 16, 31,
                                        tzinfo=dt.timezone.utc))
        self.assertEqual(discover.stem_sat(stem), "RCM1")
        self.assertEqual(discover.stem_pol(stem), "VH")

    def test_storm_fields(self):
        f = discover.storm_fields("AL012026_ARTHUR")
        self.assertEqual(f, {"atcf": "al012026", "basin": "AL", "num": 1,
                             "year": 2026, "name": "Arthur"})
        self.assertEqual(discover.storm_slug("AL012026_ARTHUR"),
                         "al012026_arthur")


class BuildTick(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.spec = "local:" + self.tmp
        self.store = LocalStore(self.tmp)

    def _patch(self, passes, nc=b"NC", render=None):
        def fake_render(nc_bytes, meta, geo_dir=".", salinity=None):
            return (b"PNG", b"JPG", {"max_ms": 30.0, "peak_ms": 27.0,
                                     "peak_kt": 52, "peak_lat": 0.5,
                                     "peak_lon": 0.5, "mean_ms": 10.0,
                                     "n_cells": 5, "bbox": [0, 1, 0, 1],
                                     "t": (meta["t"].strftime(
                                         "%Y-%m-%dT%H:%M:%SZ")
                                         if meta.get("t") else None)})
        return (
            mock.patch.object(sbuild.discover, "storms_for_year",
                              return_value=["AL012026_ARTHUR"]),
            mock.patch.object(sbuild.discover, "passes_for_storm",
                              return_value=passes),
            mock.patch.object(sbuild.fetch, "get_bytes", return_value=nc),
            mock.patch.object(sbuild.render, "render_pass",
                              side_effect=render or fake_render),
        )

    PASSES = [
        {"stem": "S2", "url": "u2", "sat": "RCM1", "pol": "VH",
         "t": dt.datetime(2026, 6, 18, tzinfo=dt.timezone.utc)},
        {"stem": "S1", "url": "u1", "sat": "RCM3", "pol": "VH",
         "t": dt.datetime(2026, 6, 17, tzinfo=dt.timezone.utc)},
    ]

    def test_first_tick_renders_and_indexes(self):
        p1, p2, p3, p4 = self._patch(self.PASSES)
        with p1, p2, p3, p4:
            s = sbuild.build(self.spec, year=2026, max_new=6, log=lambda *a: None)
        self.assertEqual(s["new_passes"], 2)
        idx = self.store.get_json("sar/al012026_arthur/index.json")
        self.assertEqual([p["stem"] for p in idx["passes"]], ["S2", "S1"])
        man = self.store.get_json("sar/manifest.json")
        self.assertEqual(man["storms"][0]["n_passes"], 2)
        self.assertEqual(man["storms"][0]["latest_utc"], "2026-06-18T00:00:00Z")

    def test_second_tick_is_idempotent(self):
        p1, p2, p3, p4 = self._patch(self.PASSES)
        with p1, p2, p3, p4:
            sbuild.build(self.spec, year=2026, log=lambda *a: None)
            s = sbuild.build(self.spec, year=2026, log=lambda *a: None)
        self.assertEqual(s["new_passes"], 0)

    def test_budget_defers_and_backfills(self):
        p1, p2, p3, p4 = self._patch(self.PASSES)
        with p1, p2, p3, p4:
            s1 = sbuild.build(self.spec, year=2026, max_new=1,
                              log=lambda *a: None)
            s2 = sbuild.build(self.spec, year=2026, max_new=1,
                              log=lambda *a: None)
        self.assertEqual(s1["new_passes"], 1)
        self.assertEqual(s2["new_passes"], 1)      # backfill next tick
        idx = self.store.get_json("sar/al012026_arthur/index.json")
        self.assertEqual(len(idx["passes"]), 2)

    def test_failed_render_retries_next_tick(self):
        calls = {"n": 0}
        def flaky(nc_bytes, meta, geo_dir=".", salinity=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("bad file")
            return (b"PNG", b"JPG", {"max_ms": 1.0, "peak_ms": 1.0,
                                     "peak_kt": 2, "peak_lat": 0.0,
                                     "peak_lon": 0.0, "mean_ms": 1.0,
                                     "n_cells": 1, "bbox": [0, 1, 0, 1],
                                     "t": "2026-06-18T00:00:00Z"})
        p1, p2, p3, p4 = self._patch(self.PASSES[:1], render=flaky)
        with p1, p2, p3, p4:
            s1 = sbuild.build(self.spec, year=2026, log=lambda *a: None)
            s2 = sbuild.build(self.spec, year=2026, log=lambda *a: None)
        self.assertEqual(s1["new_passes"], 0)
        self.assertEqual(s2["new_passes"], 1)

    def test_offseason_noop(self):
        with mock.patch.object(sbuild.discover, "storms_for_year",
                               return_value=[]):
            s = sbuild.build(self.spec, year=2026, log=lambda *a: None)
        self.assertEqual(s, {"storms": 0, "new_passes": 0})
        self.assertIsNone(self.store.get_json("sar/manifest.json"))


class DespeckledPeak(unittest.TestCase):
    def test_hot_pixel_does_not_set_peak(self):
        import numpy as np
        from sarobs.render import despeckled_peak
        f = np.full((20, 20), 20.0)
        f[10, 10] = 99.0                          # lone hot pixel (speckle)
        # edge_margin=0 isolates the despeckle from the erosion here
        peak, (iy, ix) = despeckled_peak(np.ma.MaskedArray(f, mask=False),
                                         edge_margin=0)
        self.assertLess(peak, 30.0)               # 3x3 mean dilutes the spike
        self.assertGreater(peak, 20.0)

    def test_real_core_sets_peak_and_location(self):
        import numpy as np
        from sarobs.render import despeckled_peak
        f = np.full((20, 20), 15.0)
        f[5:9, 5:9] = 55.0                        # coherent 4x4 wind core
        peak, (iy, ix) = despeckled_peak(np.ma.MaskedArray(f, mask=False),
                                         edge_margin=0)
        self.assertGreater(peak, 50.0)
        self.assertTrue(5 <= iy <= 8 and 5 <= ix <= 8)

    def test_masked_edge_needs_neighbors(self):
        import numpy as np
        from sarobs.render import despeckled_peak
        f = np.full((10, 10), 10.0)
        m = np.ones_like(f, bool)
        m[0, 0] = False                           # single valid corner cell
        f[0, 0] = 80.0
        self.assertIsNone(despeckled_peak(np.ma.MaskedArray(f, mask=m),
                                          edge_margin=0))

    def test_edge_band_rejected_interior_kept(self):
        # a hot swath-edge band (top rows) must NOT win over a real interior
        # core once the valid mask is eroded inward.
        import numpy as np
        from sarobs.render import despeckled_peak
        f = np.full((80, 80), 12.0)
        f[0:3, :] = 45.0                          # coherent top-edge band
        f[40:46, 40:46] = 30.0                    # real interior core (lower)
        peak = despeckled_peak(np.ma.MaskedArray(f, mask=False), edge_margin=6)
        self.assertIsNotNone(peak)
        val, (iy, ix) = peak
        self.assertGreater(iy, 6)                 # not on the top edge
        self.assertLess(abs(val - 30.0), 5.0)     # the interior core, ~30

    def test_coastal_buffer_erodes_near_land(self):
        # cells hugging masked land (bay contamination) are eroded out.
        import numpy as np
        f = np.full((60, 60), 15.0)
        m = np.zeros_like(f, bool)
        m[:, :30] = True                          # left half is land (masked)
        f[8:13, 31:36] = 70.0                     # hot band hugging the coast
        f[38:43, 48:53] = 40.0                    # real offshore core (block)
        from sarobs.render import despeckled_peak
        peak = despeckled_peak(np.ma.MaskedArray(f, mask=m), edge_margin=6)
        self.assertIsNotNone(peak)
        val, (iy, ix) = peak
        self.assertGreater(ix, 36)                # well off the coast
        self.assertLess(abs(val - 40.0), 8.0)

    def test_incidence_gate_drops_far_range(self):
        import numpy as np
        from sarobs.render import despeckled_peak
        f = np.full((60, 60), 20.0)
        f[30, 55] = 60.0                          # hot far-range cell
        incid = np.full((60, 60), 30.0)
        incid[:, 50:] = 49.0                      # far-range high incidence
        peak = despeckled_peak(np.ma.MaskedArray(f, mask=False), incid,
                               edge_margin=3, incid_max=47.0)
        self.assertIsNotNone(peak)
        val, (iy, ix) = peak
        self.assertLess(ix, 50)                   # the high-incid strip dropped

    def test_edge_only_scene_returns_none(self):
        import numpy as np
        from sarobs.render import despeckled_peak
        f = np.full((8, 8), 40.0)                 # smaller than the erosion box
        self.assertIsNone(despeckled_peak(np.ma.MaskedArray(f, mask=False),
                                          edge_margin=10))


class RerenderFlag(unittest.TestCase):
    def test_rerender_replaces_entries(self):
        import tempfile, shutil
        from unittest import mock as m2
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        store = LocalStore(tmp)
        passes = BuildTick.PASSES
        def mk(peak):
            def r(nc_bytes, meta, geo_dir=".", salinity=None):
                return (b"PNG", b"JPG", {"max_ms": 30.0, "peak_ms": peak,
                                         "peak_kt": 1, "peak_lat": 0,
                                         "peak_lon": 0, "mean_ms": 1,
                                         "n_cells": 1, "bbox": [0, 1, 0, 1],
                                         "t": (meta["t"].strftime(
                                             "%Y-%m-%dT%H:%M:%SZ")
                                             if meta.get("t") else None)})
            return r
        with m2.patch.object(sbuild.discover, "storms_for_year",
                             return_value=["AL012026_ARTHUR"]),              m2.patch.object(sbuild.discover, "passes_for_storm",
                             return_value=passes),              m2.patch.object(sbuild.fetch, "get_bytes", return_value=b"NC"):
            with m2.patch.object(sbuild.render, "render_pass",
                                 side_effect=mk(10.0)):
                sbuild.build("local:" + tmp, year=2026, log=lambda *a: None)
            with m2.patch.object(sbuild.render, "render_pass",
                                 side_effect=mk(44.0)):
                s = sbuild.build("local:" + tmp, year=2026, rerender=True,
                                 log=lambda *a: None)
        self.assertEqual(s["new_passes"], 2)      # both re-rendered
        idx = store.get_json("sar/al012026_arthur/index.json")
        self.assertEqual(len(idx["passes"]), 2)   # replaced, not duplicated
        self.assertTrue(all(p["peak_ms"] == 44.0 for p in idx["passes"]))


class Salinity(unittest.TestCase):
    """SMAP SSS reliability grid: listing parse, pack/read roundtrip, the
    watermark-gated build, and the render overlay's low-salinity classifier."""

    def _grid(self):
        import numpy as np
        lats = np.arange(-89.875, 90.0, 0.25)      # 720, S->N
        lons = np.arange(0.125, 360.0, 0.25)       # 1440
        sss = np.full((lats.size, lons.size), 35.0)
        # a fresh plume patch near (10N, 300E == 60W) below the threshold
        jy = np.argmin(np.abs(lats - 10.0))
        jx = np.argmin(np.abs(lons - 300.0))
        sss[jy - 4:jy + 4, jx - 4:jx + 4] = 28.0
        sss[0:3, 0:3] = np.nan                     # a land/fill hole
        return lats, lons, sss

    def test_find_latest_parses_year_block(self):
        from sarobs import salinity
        html = ('junk RSS_smap_SSS_L3_8day_running_2026_161_FNL_v06.0.nc more '
                'RSS_smap_SSS_L3_8day_running_2026_177_FNL_v06.0.nc end '
                'RSS_smap_SSS_L3_8day_running_2025_360_FNL_v06.0.nc')
        with mock.patch.object(salinity.fetch, "get_text",
                               return_value=html):
            got = salinity.find_latest()
        self.assertEqual(got, (2026, 177))         # newest DOY in current year

    def test_pack_read_roundtrip_exact(self):
        import numpy as np
        from sarobs import salinity
        lats, lons, sss = self._grid()
        body = salinity._pack(lats, lons, sss)
        self.assertGreater(len(body), 0)
        rlat, rlon, rsss = salinity.read_grid(body)
        self.assertEqual(rsss.shape, sss.shape)
        finite = np.isfinite(sss)
        self.assertTrue(np.allclose(rsss[finite], sss[finite], atol=1e-3))
        self.assertTrue(np.isnan(rsss[0, 0]))      # NaN fill preserved

    def test_build_watermark_gates(self):
        import tempfile, shutil
        from sarobs import salinity
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        store = LocalStore(tmp)
        lats, lons, sss = self._grid()
        with mock.patch.object(salinity, "find_latest",
                               return_value=(2026, 177)),                mock.patch.object(salinity, "fetch_grid",
                               return_value=(lats, lons, sss)):
            s1 = salinity.build(store, log=lambda *a: None)
            s2 = salinity.build(store, log=lambda *a: None)   # same DOY -> no-op
        self.assertTrue(s1["published"])
        self.assertFalse(s2["published"])          # watermark gate held
        meta = store.get_json("sar/salinity/meta.json")
        self.assertEqual((meta["year"], meta["doy"]), (2026, 177))
        self.assertIsNotNone(store.get_bytes("sar/salinity/mask.nc"))

    def test_overlay_flags_only_low_water(self):
        import numpy as np
        from sarobs import render
        from matplotlib.figure import Figure
        lats, lons, sss = self._grid()
        fig = Figure(); ax = fig.subplots()
        # extent over the fresh plume (55W..65W, 6N..14N) -> should hatch
        shown = render._overlay_low_salinity(
            ax, (-65.0, -55.0, 6.0, 14.0), (lats, lons, sss))
        self.assertTrue(shown)
        # extent over open salty water far away -> nothing to hatch
        ax2 = fig.subplots()
        shown2 = render._overlay_low_salinity(
            ax2, (-40.0, -30.0, -20.0, -10.0), (lats, lons, sss))
        self.assertFalse(shown2)


class ColorScale(unittest.TestCase):
    def test_scale_contract(self):
        from sarobs.render import VMAX, _STOPS, sar_cmap
        self.assertAlmostEqual(VMAX, 51.44)        # 100 kt top of scale
        self.assertEqual(_STOPS[0][1], "#000000")  # calm black
        # the two documented hard breaks exist
        vals = [v for v, _ in _STOPS]
        self.assertIn(0.49, vals); self.assertIn(0.5, vals)
        self.assertIn(19.59, vals); self.assertIn(19.6, vals)
        cm = sar_cmap()
        self.assertEqual(cm.N, 256)

    def test_make_store_specs(self):
        self.assertIsInstance(make_store("local:/tmp/x"), LocalStore)
        with self.assertRaises(ValueError):
            make_store("bogus")


if __name__ == "__main__":
    unittest.main()
