#!/usr/bin/env python3
"""Behavioural tests for shear_diag - synthetic fields with KNOWN answers.

The point of every test here is a number we can compute by hand: impose an
environmental shear, add a vortex, and demand the removal give back what was
imposed. The hemisphere and antimeridian tests are the load-bearing ones -
TAT serves SHEM, and the WPAC crosses 180 every season.
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hafs_render.shear_diag import (  # noqa: E402
    heading_deg, vortex_removed_shear)


def _grid(cen_lat, cen_lon, half_deg=8.0, step=0.12):
    """1-D lat/lon axes for a square grid centred on (cen_lat, cen_lon).
    Longitudes are kept RAW (may exceed 180) - callers pick the frame."""
    lat = np.arange(cen_lat - half_deg, cen_lat + half_deg + 1e-9, step)
    lon = np.arange(cen_lon - half_deg, cen_lon + half_deg + 1e-9, step)
    return lat, lon


def _vortex_diff_field(lat, lon, cen_lat, cen_lon, peak_kt, hemi,
                       shear_u=0.0, shear_v=0.0, rmw_km=80.0):
    """Layer-difference field = uniform imposed shear + a Rankine-like vortex
    residual. A real TC is stronger at 850 than 200, so the layer difference
    (200 minus 850) contains MINUS the low-level vortex; ``hemi`` sets the
    cyclonic spin (CCW in NH, CW in SH)."""
    lon2, lat2 = np.meshgrid(lon, lat)
    dlon = (lon2 - cen_lon + 180.0) % 360.0 - 180.0
    x = dlon * 111.195 * np.cos(np.radians(lat2))
    y = (lat2 - cen_lat) * 111.195
    r = np.hypot(x, y)
    r_safe = np.where(r > 1e-6, r, 1e-6)
    # Rankine: solid-body inside rmw, 1/r decay outside.
    vt = np.where(r <= rmw_km, peak_kt * r / rmw_km, peak_kt * rmw_km / r_safe)
    spin = 1.0 if hemi == "N" else -1.0     # cyclonic: CCW in NH, CW in SH
    thx, thy = -y / r_safe, x / r_safe      # CCW tangent
    # 200-minus-850 removes the low-level vortex -> minus sign.
    du = shear_u - spin * vt * thx
    dv = shear_v - spin * vt * thy
    return du, dv


class TestHeadingConvention(unittest.TestCase):
    def test_cardinal_headings(self):
        self.assertEqual(heading_deg(0.0, 10.0), 0.0)     # points north
        self.assertEqual(heading_deg(10.0, 0.0), 90.0)    # points east
        self.assertEqual(heading_deg(0.0, -10.0), 180.0)
        self.assertEqual(heading_deg(-10.0, 0.0), 270.0)


class TestRecovery(unittest.TestCase):
    """Removal must give back the imposed environmental shear."""

    def _run(self, cen_lat, cen_lon, hemi, peak_kt=90.0,
             shear_u=12.0, shear_v=9.0):
        lat, lon = _grid(cen_lat, cen_lon)
        du, dv = _vortex_diff_field(lat, lon, cen_lat, cen_lon,
                                    peak_kt, hemi, shear_u, shear_v)
        return vortex_removed_shear(du, dv, lat, lon, cen_lat, cen_lon)

    def test_nh_recovers_imposed_shear(self):
        out = self._run(18.0, -55.0, "N")
        self.assertAlmostEqual(out["mag_kt"], 15.0, delta=0.5)
        self.assertAlmostEqual(out["hdg_deg"], heading_deg(12.0, 9.0), delta=2.0)

    def test_sh_recovers_imposed_shear_identically(self):
        """SHEM: same imposed shear, mirrored latitude, opposite spin. The
        recovered vector must be the SAME - the geometry carries no
        hemisphere assumption."""
        n = self._run(18.0, 75.0, "N")
        s = self._run(-18.0, 75.0, "S")
        self.assertAlmostEqual(n["mag_kt"], s["mag_kt"], delta=0.3)
        self.assertAlmostEqual(n["hdg_deg"], s["hdg_deg"], delta=1.5)

    def test_pure_shear_no_vortex(self):
        lat, lon = _grid(20.0, -45.0)
        du = np.full((len(lat), len(lon)), 20.0)
        dv = np.zeros_like(du)
        out = vortex_removed_shear(du, dv, lat, lon, 20.0, -45.0)
        self.assertAlmostEqual(out["mag_kt"], 20.0, delta=0.2)
        self.assertAlmostEqual(out["naive_mag_kt"], 20.0, delta=0.2)
        self.assertAlmostEqual(out["hdg_deg"], 90.0, delta=0.5)


class TestAntimeridian(unittest.TestCase):
    """WPAC storms cross 180. The numbers must be invariant under longitude
    translation - a centre at 179.8E on a signed-longitude grid is the
    canonical sign-flip trap."""

    def test_translation_invariance_across_180(self):
        cen_lat, peak = 22.0, 75.0
        # Reference: same storm placed far from the antimeridian.
        lat, lon_ref = _grid(cen_lat, -60.0)
        du_r, dv_r = _vortex_diff_field(lat, lon_ref, cen_lat, -60.0,
                                        peak, "N", 15.0, -5.0)
        ref = vortex_removed_shear(du_r, dv_r, lat, lon_ref, cen_lat, -60.0)

        # Same storm straddling 180, longitudes in the SIGNED frame
        # (172 .. 180 -> -180 .. -172), centre given as 179.8.
        lat2, lon_raw = _grid(cen_lat, 179.8)
        du_a, dv_a = _vortex_diff_field(lat2, lon_raw, cen_lat, 179.8,
                                        peak, "N", 15.0, -5.0)
        lon_signed = (lon_raw + 180.0) % 360.0 - 180.0
        am = vortex_removed_shear(du_a, dv_a, lat2, lon_signed,
                                  cen_lat, 179.8)
        self.assertIsNotNone(am)
        self.assertAlmostEqual(am["mag_kt"], ref["mag_kt"], delta=0.3)
        self.assertAlmostEqual(am["hdg_deg"], ref["hdg_deg"], delta=1.0)
        self.assertAlmostEqual(am["naive_mag_kt"], ref["naive_mag_kt"],
                               delta=0.3)
        # And with the centre expressed in the continuous frame (>180):
        am2 = vortex_removed_shear(du_a, dv_a, lat2, lon_signed,
                                   cen_lat, 179.8 + 360.0)
        self.assertAlmostEqual(am2["mag_kt"], am["mag_kt"], delta=0.05)


class TestContamination(unittest.TestCase):
    """Where naive and removed genuinely diverge, the divergence must grow
    with vortex intensity - that growth is the evidence something was
    removed. A centred symmetric vortex self-cancels under a full-disc
    average, so the test clips the disc (storm near the domain edge), which
    is the real-world leak path."""

    def _edge_case(self, peak_kt):
        cen_lat, cen_lon = 20.0, -50.0
        # Grid whose western edge cuts 1 degree from the centre: a deep clip
        # (coverage ~0.64, just above the publish guard).
        lat = np.arange(cen_lat - 8.0, cen_lat + 8.0, 0.12)
        lon = np.arange(cen_lon - 1.0, cen_lon + 8.0, 0.12)
        du, dv = _vortex_diff_field(lat, lon, cen_lat, cen_lon,
                                    peak_kt, "N", 10.0, 0.0)
        return vortex_removed_shear(du, dv, lat, lon, cen_lat, cen_lon)

    def test_removed_is_intensity_independent(self):
        """The strongest statement that the vortex is GONE: on the same
        clipped grid the removed number must not move as the vortex triples,
        while the naive number visibly does. (The removed value carries a
        small intensity-INdependent bias - partial rings remove a slice of
        the uniform environment too - bounded by the coverage guard.)"""
        outs = {p: self._edge_case(p) for p in (30.0, 100.0, 120.0)}
        rem = [o["mag_kt"] for o in outs.values()]
        nai = [o["naive_mag_kt"] for o in outs.values()]
        self.assertLess(max(rem) - min(rem), 0.3)      # removed: flat
        self.assertGreater(max(nai) - min(nai), 3.0)   # naive: drifts up

    def test_clipped_disc_naive_contaminated_removed_clean(self):
        out = self._edge_case(120.0)
        self.assertIsNotNone(out)
        err_naive = abs(out["naive_mag_kt"] - 10.0)
        err_rem = abs(out["mag_kt"] - 10.0)
        self.assertGreater(err_naive, 5.0)     # vortex leaked into naive
        self.assertLess(err_rem, 2.5)          # bounded method bias only

    def test_contamination_grows_with_intensity(self):
        weak = self._edge_case(30.0)
        strong = self._edge_case(120.0)
        gap_w = abs(weak["naive_mag_kt"] - weak["mag_kt"])
        gap_s = abs(strong["naive_mag_kt"] - strong["mag_kt"])
        self.assertGreater(gap_s, gap_w * 2.5)


class TestGuards(unittest.TestCase):
    def test_missing_centre_returns_none(self):
        lat, lon = _grid(15.0, -40.0)
        du = np.zeros((len(lat), len(lon)))
        self.assertIsNone(vortex_removed_shear(du, du, lat, lon, None, -40.0))
        self.assertIsNone(vortex_removed_shear(du, du, lat, lon, 15.0, None))

    def test_poor_coverage_returns_none(self):
        # Centre 6 degrees off the grid: only a sliver of the disc has data.
        lat, lon = _grid(15.0, -40.0, half_deg=3.0)
        du = np.full((len(lat), len(lon)), 10.0)
        self.assertIsNone(
            vortex_removed_shear(du, du, lat, lon, 15.0, -47.5))

    def test_all_nan_returns_none(self):
        lat, lon = _grid(15.0, -40.0)
        du = np.full((len(lat), len(lon)), np.nan)
        self.assertIsNone(vortex_removed_shear(du, du, lat, lon, 15.0, -40.0))

    def test_helmholtz_is_a_named_seam(self):
        lat, lon = _grid(15.0, -40.0)
        du = np.zeros((len(lat), len(lon)))
        with self.assertRaises(NotImplementedError):
            vortex_removed_shear(du, du, lat, lon, 15.0, -40.0,
                                 method="helmholtz")

    def test_unknown_method_rejected(self):
        lat, lon = _grid(15.0, -40.0)
        du = np.zeros((len(lat), len(lon)))
        with self.assertRaises(ValueError):
            vortex_removed_shear(du, du, lat, lon, 15.0, -40.0,
                                 method="magic")


if __name__ == "__main__":
    unittest.main()


class TestBuilderPass(unittest.TestCase):
    """_shear_diag_pass end-to-end: synthetic cache entry -> storm_meta."""

    def test_pass_attaches_numbers_and_skips_unfixed(self):
        import tempfile
        import xarray as xr
        from hafs_render import generate_hafs_plots as g

        cen_lat, cen_lon = 18.0, -55.0
        lat, lon = _grid(cen_lat, cen_lon)
        du, dv = _vortex_diff_field(lat, lon, cen_lat, cen_lon,
                                    90.0, "N", 12.0, 9.0)
        with tempfile.TemporaryDirectory() as td:
            # A "parent" entry carrying shear vectors AND 10 m wind, so the
            # extended pass (#7 radii, #25 parent profile) is exercised too.
            cpath = str(Path(td) / "parent" / "f012.nc")
            Path(cpath).parent.mkdir(parents=True)
            spd = np.hypot(du, dv) + 40.0        # >34 kt everywhere near centre
            xr.Dataset(
                {"shru_200_850": (("lat", "lon"), du),
                 "shrv_200_850": (("lat", "lon"), dv),
                 "wind_kt": (("lat", "lon"), spd),
                 "u_kt": (("lat", "lon"), du + 5.0),
                 "v_kt": (("lat", "lon"), dv + 5.0)},
                coords={"lat": lat, "lon": lon}).to_netcdf(cpath)
            meta = {"07x": {"frames": {}}}
            env_frames = [("hafsa", "07x", 12, cpath),
                          ("hafsa", "07x", 18, cpath),   # no track fix
                          ("hafsa", "07x", 24, cpath)]   # ingest failed
            tracks = {("hafsa", "07x"): ({12: (cen_lat, cen_lon)}, {})}
            ok = {g._frame_key("hafsa", "07x", "parent.atm", 12),
                  g._frame_key("hafsa", "07x", "parent.atm", 18)}
            g._shear_diag_pass(env_frames, tracks, ok, meta)
            shear = meta["07x"]["shear"]
            self.assertEqual(shear["params"]["method"], "azimuthal_mean")
            self.assertEqual(shear["params"]["layer_hpa"], [200, 850])
            self.assertEqual(list(shear["hours"]["hafsa"]), ["12"])
            got = shear["hours"]["hafsa"]["12"]
            self.assertAlmostEqual(got["kt"], 15.0, delta=0.5)
            self.assertIn("naive_kt", got)
            # #7 rode the same pass: quadrant-max radii with the method STATED.
            radii = meta["07x"]["radii"]
            self.assertEqual(radii["params"]["method"], "quadrant_max")
            self.assertEqual(radii["params"]["units"], "nm")
            r34 = radii["hours"]["hafsa"]["12"]["r34"]
            self.assertTrue(all(isinstance(v, int) for v in r34))
            # #25 parent-profile scalars land under structure (no nest sibling
            # in this fixture, so hours may be absent - parent-only prof is
            # not published without the nest); assert no crash + shear intact.
