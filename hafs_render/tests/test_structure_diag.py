#!/usr/bin/env python3
"""#25/#7 behavioural tests - synthetic vortices with KNOWN structure, both
hemispheres, and a centre hard against the antimeridian."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hafs_render import structure_diag as sd  # noqa: E402
from hafs_render.structure_diag import KT_PER_MS  # noqa: E402


def _grid(cen_lat, cen_lon, half_deg=6.0, step=0.05):
    lat = np.arange(cen_lat - half_deg, cen_lat + half_deg + 1e-9, step)
    lon = np.arange(cen_lon - half_deg, cen_lon + half_deg + 1e-9, step)
    return lat, lon


def _vortex_uv(lat, lon, cen_lat, cen_lon, peak_kt, rmw_km, hemi,
               asym_kt=0.0, asym_dir_deg=0.0):
    """Rankine vortex in kt: cyclonic spin per hemisphere, optional
    wavenumber-0 wind asymmetry added along one bearing (for radii tests)."""
    lon2, lat2 = np.meshgrid(lon, lat)
    dlon = (lon2 - cen_lon + 180.0) % 360.0 - 180.0
    x = dlon * 111.195 * np.cos(np.radians(lat2))
    y = (lat2 - cen_lat) * 111.195
    r = np.hypot(x, y)
    rs = np.where(r > 1e-6, r, 1e-6)
    vt = np.where(r <= rmw_km, peak_kt * r / rmw_km, peak_kt * rmw_km / rs)
    spin = 1.0 if hemi == "N" else -1.0
    u = -spin * vt * y / rs
    v = spin * vt * x / rs
    if asym_kt:
        a = np.radians(asym_dir_deg)
        brg = np.degrees(np.arctan2(x, y)) % 360.0
        boost = asym_kt * np.exp(-(((brg - asym_dir_deg + 180) % 360 - 180) / 40.0) ** 2)
        u += boost * np.sin(a) * 0  # asymmetry as SPEED boost, not vector add
        vt_boost = boost
        u += -spin * vt_boost * y / rs
        v += spin * vt_boost * x / rs
    return u, v


class TestAzimuthalStructure(unittest.TestCase):
    def _fields(self, lat, lon, cen, peak, rmw, hemi):
        u, v = _vortex_uv(lat, lon, cen[0], cen[1], peak, rmw, hemi)
        return {"u_kt": u, "v_kt": v,
                "u_850": u / KT_PER_MS, "v_850": v / KT_PER_MS}

    def test_recovers_rmw_and_peak_nh(self):
        cen = (18.0, -55.0)
        lat, lon = _grid(*cen)
        st = sd.azimuthal_structure(self._fields(lat, lon, cen, 100.0, 60.0, "N"),
                                    lat, lon, *cen)
        self.assertAlmostEqual(st["rmw_km"], 60.0, delta=6.0)
        self.assertAlmostEqual(st["vt_max_kt"], 100.0, delta=5.0)
        # NH cyclonic = CCW = positive vt at the peak ring
        i = int(np.nanargmax(np.abs(st["vt_kt"]["10m"])))
        self.assertGreater(st["vt_kt"]["10m"][i], 0)
        # upper level converted from m/s: same profile
        self.assertAlmostEqual(np.nanmax(np.abs(st["vt_kt"]["850"])),
                               100.0, delta=5.0)

    def test_sh_mirror_same_magnitude_opposite_sign(self):
        n_cen, s_cen = (18.0, 75.0), (-18.0, 75.0)
        lat_n, lon_n = _grid(*n_cen)
        lat_s, lon_s = _grid(*s_cen)
        st_n = sd.azimuthal_structure(self._fields(lat_n, lon_n, n_cen, 80.0, 45.0, "N"),
                                      lat_n, lon_n, *n_cen)
        st_s = sd.azimuthal_structure(self._fields(lat_s, lon_s, s_cen, 80.0, 45.0, "S"),
                                      lat_s, lon_s, *s_cen)
        self.assertAlmostEqual(st_n["vt_max_kt"], st_s["vt_max_kt"], delta=1.0)
        self.assertAlmostEqual(st_n["rmw_km"], st_s["rmw_km"], delta=3.0)
        i_n = int(np.nanargmax(np.abs(st_n["vt_kt"]["10m"])))
        i_s = int(np.nanargmax(np.abs(st_s["vt_kt"]["10m"])))
        self.assertGreater(st_n["vt_kt"]["10m"][i_n], 0)   # NH cyclonic: CCW +
        self.assertLess(st_s["vt_kt"]["10m"][i_s], 0)      # SH cyclonic: CW -

    def test_warm_core_from_thickness(self):
        cen = (20.0, -40.0)
        lat, lon = _grid(*cen)
        f = self._fields(lat, lon, cen, 60.0, 40.0, "N")
        # Gaussian thickness bump: +80 m at centre over ~4000 m background ->
        # dT = 80 * g/(R ln1.7) ~ +5.1 C at r=0, ~0 at the far field.
        lon2, lat2 = np.meshgrid(lon, lat)
        dlon = (lon2 - cen[1] + 180.0) % 360.0 - 180.0
        x = dlon * 111.195 * np.cos(np.radians(lat2))
        y = (lat2 - cen[0]) * 111.195
        r2 = x * x + y * y
        f["gh_850"] = np.full_like(x, 1500.0)
        f["gh_500"] = 1500.0 + 4000.0 + 80.0 * np.exp(-r2 / (80.0 ** 2))
        st = sd.azimuthal_structure(f, lat, lon, *cen)
        self.assertIsNotNone(st["t_anom_c"])
        self.assertAlmostEqual(st["t_anom_c"][0], 80.0 * sd._DT_PER_M, delta=0.6)
        self.assertAlmostEqual(st["t_anom_c"][-1], 0.0, delta=0.3)

    def test_no_centre_or_no_wind_returns_none(self):
        lat, lon = _grid(15.0, -40.0)
        self.assertIsNone(sd.azimuthal_structure({}, lat, lon, None, -40.0))
        self.assertIsNone(sd.azimuthal_structure({}, lat, lon, 15.0, -40.0))


class TestQuadrantRadii(unittest.TestCase):
    def test_method_stated_and_asymmetry_captured(self):
        cen = (20.0, -55.0)
        lat, lon = _grid(*cen, half_deg=8.0, step=0.08)
        # 60 kt vortex, RMW 50 km, +25 kt speed boost toward the NE (bearing
        # 45): NE R34 must exceed SW R34 decisively.
        u, v = _vortex_uv(lat, lon, cen[0], cen[1], 60.0, 50.0, "N",
                          asym_kt=25.0, asym_dir_deg=45.0)
        wind = np.hypot(u, v)
        rr = sd.quadrant_radii(wind, lat, lon, *cen)
        self.assertEqual(rr["method"], "quadrant_max")
        self.assertEqual(rr["units"], "nm")
        self.assertEqual(rr["quadrants"], ["NE", "SE", "SW", "NW"])
        ne, se, sw, nw = rr["r34"]
        self.assertGreater(ne, sw * 1.3)
        # r64: only inside/near the boosted sector reaches 64 kt (peak 60+25)
        self.assertIsNotNone(rr["r64"][0])
        self.assertIsNone(rr["r64"][2])

    def test_quadrant_max_exceeds_a_mean_by_construction(self):
        """The sanity check that catches publishing means: for an asymmetric
        field the NE quadrant-max is far beyond the azimuthal-mean R34."""
        cen = (20.0, -55.0)
        lat, lon = _grid(*cen, half_deg=8.0, step=0.08)
        u, v = _vortex_uv(lat, lon, cen[0], cen[1], 45.0, 50.0, "N",
                          asym_kt=30.0, asym_dir_deg=45.0)
        wind = np.hypot(u, v)
        rr = sd.quadrant_radii(wind, lat, lon, *cen)
        from hafs_render import polar
        pg = polar.polar_grid(lat, lon, *cen)
        edges = np.arange(0.0, sd.RADII_MAX_KM, 10.0)
        mean_prof, _ = polar.ring_mean(wind, pg, edges)
        mids = (edges[:-1] + edges[1:]) / 2
        above = mids[np.nan_to_num(mean_prof) >= 34.0]
        mean_r34_nm = (above.max() / sd.KM_PER_NM) if above.size else 0.0
        self.assertGreater(rr["r34"][0], mean_r34_nm * 1.2)

    def test_antimeridian_centre(self):
        """Centre at 179.8 on a signed-longitude grid: radii must match the
        same storm far from the seam."""
        ref_cen = (22.0, -60.0)
        lat, lon = _grid(*ref_cen, half_deg=8.0, step=0.08)
        u, v = _vortex_uv(lat, lon, *ref_cen, 70.0, 60.0, "N")
        ref = sd.quadrant_radii(np.hypot(u, v), lat, lon, *ref_cen)

        am_cen = (22.0, 179.8)
        lat2, lon_raw = _grid(*am_cen, half_deg=8.0, step=0.08)
        u2, v2 = _vortex_uv(lat2, lon_raw, *am_cen, 70.0, 60.0, "N")
        lon_signed = (lon_raw + 180.0) % 360.0 - 180.0
        am = sd.quadrant_radii(np.hypot(u2, v2), lat2, lon_signed, *am_cen)
        for t in ("r34", "r50", "r64"):
            for a, b in zip(ref[t], am[t]):
                if a is None or b is None:
                    self.assertEqual(a, b, t)
                else:
                    self.assertLessEqual(abs(a - b), 3, t)

    def test_all_below_threshold_returns_none(self):
        cen = (20.0, -55.0)
        lat, lon = _grid(*cen)
        wind = np.full((len(lat), len(lon)), 20.0)
        self.assertIsNone(sd.quadrant_radii(wind, lat, lon, *cen))


if __name__ == "__main__":
    unittest.main()
