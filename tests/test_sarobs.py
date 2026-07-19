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
        def fake_render(nc_bytes, meta, geo_dir="."):
            return (b"PNG", b"JPG", {"max_ms": 30.0, "mean_ms": 10.0,
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
        def flaky(nc_bytes, meta, geo_dir="."):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("bad file")
            return (b"PNG", b"JPG", {"max_ms": 1.0, "mean_ms": 1.0,
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
