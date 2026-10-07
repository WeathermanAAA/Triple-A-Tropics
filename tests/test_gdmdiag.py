"""gdmdiag/build.py: header-name parsing, antimeridian unwrapping, equal-weight-per-model pooling, RI and landfall."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gdmdiag"))
import build as B  # noqa: E402

HDR = ("# licence preamble\n# BEGIN DATA\n"
       "lead_time_hours,track_id,init_time,sample,valid_time,lead_time,lat,lon,minimum_sea_level_pressure_hpa,"
       "maximum_sustained_wind_speed_knots,radius_of_maximum_winds_km,"
       + ",".join(B.RAD) + "\n")


def row(h, s, lat, lon, v, r34=100.0):
    rad = [r34] * 4 + [0.0] * 8
    return f"{h},WP012026,2026-10-06 12:00:00,{s},x,x,{lat},{lon},990,{v},40," + ",".join(map(str, rad)) + "\n"


class GdmDiagTests(unittest.TestCase):
    def test_parse_by_header_name_and_unwrap(self):
        # columns deliberately reordered; the track crosses 180
        csv = HDR + "".join(row(h, 0, 15 + h / 24, 178 + h / 12, 40 + h / 6) for h in range(0, 49, 6))
        tracks, hasr = B.parse(csv.encode())
        self.assertTrue(hasr)
        t = tracks["WP012026"][0]
        self.assertEqual(t.shape[1], 18)
        E = B.Ens([t], [1.0], lon0=178.0)
        lons = E.col(2)[0][:9]
        self.assertTrue(np.all(np.diff(lons) > 0), "longitudes must stay continuous across 180")
        self.assertGreater(lons[-1], 180)

    def test_ri_and_quantiles(self):
        # member 0 gains 40 kt in 24 h (RI), member 1 stays flat
        fast = [[h, 15, 140, 40 + (40 if h >= 24 else 0), 990, 40] + [80.0] * 4 + [0.0] * 8 for h in range(0, 49, 6)]
        flat = [[h, 15, 141, 40, 1000, 40] + [80.0] * 4 + [0.0] * 8 for h in range(0, 49, 6)]
        E = B.Ens([np.array(fast, float), np.array(flat, float)], [1, 1], 140.0)
        d = B.diag(E)
        self.assertAlmostEqual(d["ri_cum"][B.LIDX[24.0]], 0.5, places=3)
        self.assertAlmostEqual(d["ri_cum"][B.LIDX[0.0]], 0.0, places=3)
        self.assertEqual(d["vmax_q"][B.LIDX[48.0]][-1], 80.0)

    def test_pool_is_equal_weight_per_model(self):
        # model A: 1 member at 100 kt; model B: 3 members at 40 kt -> pooled median sits between, P(>=64) = 0.5
        a = [np.array([[h, 15, 140, 100, 950, 30] + [0.0] * 12 for h in range(0, 25, 6)], float)]
        b = [np.array([[h, 15, 140, 40, 1000, 30] + [0.0] * 12 for h in range(0, 25, 6)], float) for _ in range(3)]
        E = B.Ens(a + b, [1 / 2] + [1 / 6] * 3, 140.0)
        d = B.diag(E, False)
        row24 = [r for r in d["cat"] if r["h"] == 24][0]
        hur = sum(row24[k] for k in ("C1", "C2", "C3", "C4", "C5"))
        self.assertAlmostEqual(hur, 0.5, places=3)


if __name__ == "__main__":
    unittest.main()
