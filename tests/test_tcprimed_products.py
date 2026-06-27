"""render_overpass emits ALL FOUR canonical MW products (color37, color91, 37H,
91H) + their chrome-free map tiles for a valid overpass — the "all four current
for every overpass" guarantee. Uses a synthetic storm swath (no TC-PRIMED file)."""
import datetime as dt
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tcprimed import render as R  # noqa: E402


def _synth_meta():
    clat, clon = 18.0, 235.0
    n = 90
    lat = np.linspace(clat - 5, clat + 5, n)
    lon = np.linspace(clon - 5, clon + 5, n)
    LON, LAT = np.meshgrid(lon, lat)
    r = np.sqrt((LAT - clat) ** 2 + ((LON - clon) * np.cos(np.radians(clat))) ** 2)
    tb89h = np.clip(120 + 175 * (r / 5.0) ** 0.7, 105, 305)
    tb89v = np.clip(tb89h + 18, 105, 305)
    tb37h = np.clip(150 + 120 * (r / 5.0), 125, 310)
    tb37v = np.clip(tb37h + 35, 125, 310)
    return {
        "sensor": "GMI", "platform": "GPM", "atcf": "EP052026", "basin": "EP",
        "year": 2026, "valid": dt.datetime(2026, 6, 27, 0, 32),
        "intensity_kt": 115, "min_p_hpa": 948, "dev_level": "CAT4",
        "clat": clat, "clon": clon,
        "lat89": LAT, "lon89": LON, "tb89v": tb89v, "tb89h": tb89h,
        "lat37": LAT, "lon37": LON, "tb37v": tb37v, "tb37h": tb37h,
    }


class TestFourProducts(unittest.TestCase):
    def test_render_overpass_emits_all_four(self):
        with tempfile.TemporaryDirectory() as td:
            res = R.render_overpass(_synth_meta(), td, "ov1")
            self.assertEqual(set(res["products"]), {"color37", "color91", "37H", "91H"})
            for key, base in res["products"].items():
                self.assertTrue(os.path.exists(os.path.join(td, base)),
                                f"{key} png missing")
            # chrome-free map tiles emitted alongside every product
            self.assertEqual(set(res["tiles"]), {"color37", "color91", "37H", "91H"})

    def test_product_renderers_keyed_canonically(self):
        # the renderer registry IS the canonical four-product set, in order
        self.assertEqual([k for k, _ in R._PRODUCT_RENDERERS],
                         ["color37", "color91", "37H", "91H"])

    def test_legends_cover_all_four(self):
        self.assertEqual(set(R.mw_legends()), {"color37", "color91", "37H", "91H"})


class TestSwathEdgeMask(unittest.TestCase):
    """The _regrid swath-edge distance mask removes the convex-hull "sliver"
    fills (the diagonal streak/fan artifacts at the swath edge) while preserving
    the real swath interior. Modelled on a partial diagonal-band swath, whose
    convex hull spills into the box corners with no real data."""

    @staticmethod
    def _concave_swath(clat=18.0, clon=235.0, n=90, half=6.0, arc=3.0):
        """A swath whose BOTTOM edge bulges UP at the centre (a concave-up arc,
        like a real conical-scanner scan edge). griddata's convex hull bridges the
        dip below the arc with sliver triangles that linearly interpolate across
        the empty gap -- the streak/fan artifact. Valid = above the arc."""
        lat = np.linspace(clat - half, clat + half, n)
        lon = np.linspace(clon - half, clon + half, n)
        LON, LAT = np.meshgrid(lon, lat)
        u = (LON - clon) / 5.0
        bottom = (clat - 5.0) + arc * np.clip(1.0 - u * u, 0.0, 1.0)
        valid = (LAT > bottom) & (LAT < clat + 5.0)
        tb = np.where(valid, 220.0, np.nan)
        return LAT, LON, tb

    def _regrid_both(self):
        LAT, LON, tb = self._concave_swath()
        old = R.EDGE_MASK_ENABLED
        try:
            R.EDGE_MASK_ENABLED = False
            _, (raw,) = R._regrid(LAT, LON, [tb], 18.0, 235.0, half=5.0, step=0.05)
            R.EDGE_MASK_ENABLED = True
            _, (msk,) = R._regrid(LAT, LON, [tb], 18.0, 235.0, half=5.0, step=0.05)
        finally:
            R.EDGE_MASK_ENABLED = old
        return raw, msk

    def test_mask_removes_slivers(self):
        raw, msk = self._regrid_both()
        raw_valid = int(np.isfinite(raw).sum())
        msk_valid = int(np.isfinite(msk).sum())
        # the convex-hull fill of a concave swath is materially larger than the
        # true band -> the mask removes a meaningful chunk of fake cells
        self.assertLess(msk_valid, 0.9 * raw_valid)

    def test_mask_only_subtracts_never_adds(self):
        raw, msk = self._regrid_both()
        # every cell the mask KEEPS was already real in the raw fill (the mask is
        # pure subtraction; it never invents data the linear interp didn't have)
        self.assertTrue(np.all(np.isfinite(raw)[np.isfinite(msk)]))

    def test_mask_keeps_the_swath_core(self):
        raw, msk = self._regrid_both()
        # the band passes through the box centre -> the centre cell (dense real
        # data) must survive the mask
        c = msk.shape[0] // 2
        self.assertTrue(np.isfinite(msk[c, c]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
