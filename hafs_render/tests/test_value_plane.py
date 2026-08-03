#!/usr/bin/env python3
"""Value-plane writer (#28): quantize/crop/orientation roundtrip."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hafs_render import hafs_plot as hp  # noqa: E402


class _F:
    pass


class TestValuePlane(unittest.TestCase):
    def test_roundtrip_orientation_and_decode(self):
        from PIL import Image
        lat = np.arange(10.0, 20.05, 0.1)
        lon = np.arange(-50.0, -39.95, 0.1)
        LON, LAT = np.meshgrid(lon, lat)
        field = LAT * 2.0            # value = 2*lat: north edge = 40, south 20
        f = _F(); f.lat, f.lon = lat, lon
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "f000.values.png"
            hp._write_value_plane(field, f, "wind_speed_kt",
                                  (-48.0, 12.0, -42.0, 18.0), out)
            img = np.asarray(Image.open(out))
            self.assertEqual(img.dtype, np.uint8)
            # wind: vmin 0, step 1, range 165 <= 254 -> effective step 1.
            # Row 0 must be the NORTH edge of the bbox (lat 18 -> value 36).
            top = img[0, img.shape[1] // 2]
            bot = img[-1, img.shape[1] // 2]
            self.assertAlmostEqual(0 + (int(top) - 1) * 1.0, 36.0, delta=1.0)
            self.assertAlmostEqual(0 + (int(bot) - 1) * 1.0, 24.0, delta=1.0)

    def test_nan_is_zero_and_step_doubles_to_fit(self):
        from PIL import Image
        lat = np.arange(0.0, 5.05, 0.1)
        lon = np.arange(0.0, 5.05, 0.1)
        field = np.full((len(lat), len(lon)), np.nan)
        field[10, 10] = -60.0        # one valid BT pixel
        f = _F(); f.lat, f.lon = lat, lon
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "f000.values.png"
            # brightness_temperature_c spans well beyond 254 x its step? If it
            # fits, the assertion below still holds with k=0 - the point is
            # the decode contract, computed the same way the client does.
            hp._write_value_plane(field, f, "brightness_temperature_c",
                                  (0.0, 0.0, 5.0, 5.0), out)
            img = np.asarray(Image.open(out))
            self.assertEqual(int(img[0, 0]), 0)          # NaN -> no-data
            from tat_palettes import quantities as tq
            q = tq.value_planes()["brightness_temperature_c"]
            vmin2, vmax2 = q["value_range"]
            step = q.get("step") or 1.0
            while (vmax2 - vmin2) / step > 254:
                step *= 2.0
            raw = int(img.max())
            self.assertGreater(raw, 0)
            v = vmin2 + (raw - 1) * step
            self.assertAlmostEqual(v, -60.0, delta=step)


if __name__ == "__main__":
    unittest.main()
