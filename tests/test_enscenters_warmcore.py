"""
Tests for the warm-core (tropical-only) filter (enscenters.warmcore).

Synthetic 300-500 thickness fields on a real-shaped grid: the filter must keep a
warm-core bump (even riding the climatological meridional gradient), and drop a
cold-core low, a low that just sits at its latitude background (extratropical), a
sub-threshold "developing" bump, a |lat| > 50 system, and a high-terrain thermal
low. No network. Run: python -m unittest discover tests
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from enscenters import warmcore as wc

LATS = np.arange(60.0, -60.25, -0.25)
LONS = np.arange(-180.0, 180.0, 0.25)
_LON, _LAT = np.meshgrid(LONS, LATS)
# realistic background: warm/thick tropics -> cold/thin extratropics, SMOOTH
# (parabolic, no equator kink) so a smooth-background subtraction nets ~0.
BASE = 3840.0 - 0.015 * _LAT ** 2


def _with_bump(clat, clon, amp, width=2.0):
    f = BASE.copy()
    dlon = ((_LON - clon + 180) % 360) - 180
    d2 = (_LAT - clat) ** 2 + (dlon * np.cos(np.radians(clat))) ** 2
    f += amp * np.exp(-d2 / (2 * width ** 2))
    return f


def _center(lat, lon, mslp=985.0):
    return {"lat": lat, "lon": lon, "mslp_hpa": mslp, "vmax_kt": 50.0}


class TestWarmCore(unittest.TestCase):
    def test_warm_core_on_gradient_kept(self):
        f = _with_bump(15.0, 150.0, 30.0)              # +30 m core on the warm gradient
        kept = wc.filter_centers([_center(15.0, 150.0)], f, LATS, LONS)
        self.assertEqual(len(kept), 1)

    def test_cold_core_dropped(self):
        f = _with_bump(15.0, 150.0, -30.0)             # thickness MINIMUM at the low
        self.assertEqual(wc.filter_centers([_center(15.0, 150.0)], f, LATS, LONS), [])

    def test_background_low_dropped(self):
        # extratropical-like: a deep low sitting at its latitude background, no core
        self.assertEqual(wc.filter_centers([_center(40.0, 150.0, 975.0)], BASE, LATS, LONS), [])

    def test_developing_subthreshold_dropped(self):
        f = _with_bump(12.0, 150.0, 3.0)               # +3 m: pre-warm-core, < warm_anom_min
        self.assertEqual(wc.filter_centers([_center(12.0, 150.0, 1005.0)], f, LATS, LONS), [])

    def test_nearby_warm_feature_does_not_leak(self):
        # a sharp warm bump ~1 deg away (inside the 1deg search window) but the
        # low's OWN center is sub-threshold -> dropped (the collocation fix).
        f = _with_bump(15.0, 151.0, 30.0, width=0.5)
        self.assertEqual(wc.filter_centers([_center(15.0, 150.0)], f, LATS, LONS), [])

    def test_lat_gate(self):
        f = _with_bump(62.0, 150.0, 30.0)              # warm core but |lat| > 50
        self.assertEqual(wc.filter_centers([_center(62.0, 150.0, 960.0)], f, LATS, LONS), [])

    def test_terrain_gate(self):
        f = _with_bump(31.0, 88.0, 30.0)               # warm core over Tibet (~5130 m)
        self.assertEqual(wc.filter_centers([_center(31.0, 88.0, 1000.0)], f, LATS, LONS), [])

    def test_thk_none_passthrough(self):
        cs = [_center(15.0, 150.0)]
        self.assertEqual(wc.filter_centers(cs, None, LATS, LONS), cs)

    def test_anomaly_removes_smooth_gradient(self):
        anom = wc.thickness_anomaly(BASE, 0.25, 0.25, 10.0)
        i0 = int(np.argmin(np.abs(LATS)))              # interior (equator), away from edges
        self.assertLess(abs(float(anom[i0, len(LONS) // 2])), 1.0)

    def test_elevation_lookup(self):
        self.assertGreater(wc.elevation_at(31.0, 88.0), 4000.0)   # Tibet
        self.assertLess(abs(wc.elevation_at(0.0, -150.0)), 50.0)  # open ocean


if __name__ == "__main__":
    unittest.main()
