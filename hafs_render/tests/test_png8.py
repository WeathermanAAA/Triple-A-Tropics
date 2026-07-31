#!/usr/bin/env python3
"""Fixed-palette PNG-8 encoding (``hafs_render.png8``).

The build's entire premise is bit-exactness on colortable fill pixels, so the
tests are about the guarantee, not the compression: the verification runs on
every pixel of every encode, an unsatisfiable colortable refuses rather than
nearest-mapping fills, and the production entry point falls back to PNG-24 on
anything unexpected.
"""
import io
import unittest

import numpy as np
from PIL import Image

from hafs_render import png8


def _png(rgb: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buf, format="PNG")
    return buf.getvalue()


def _decode(b: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(b)).convert("RGB"))


#: A small discrete colortable, like a .pal reflectivity table.
CT = np.array([[0, 0, 0], [0, 80, 255], [0, 200, 255], [255, 230, 0],
               [255, 60, 40], [255, 255, 255]], dtype=np.uint8)


def _fill_frame(n_aa: int = 0) -> np.ndarray:
    """Colortable fills on top, an AA block below - the regions NEVER overlap,
    so the colortable pixels stay present whatever n_aa is."""
    rgb = np.zeros((80, 60, 3), dtype=np.uint8)
    for i, c in enumerate(CT):
        rgb[i * 10:(i + 1) * 10, :, :] = c
    if n_aa:
        # n_aa distinct non-colortable colours, one per pixel slot, like the
        # anti-aliased blends around text and contour lines.
        aa = rgb[60:].reshape(-1, 3)
        for k in range(min(n_aa, len(aa))):
            aa[k] = [1 + k % 254, (k * 7) % 253 + 1, 255 - (k % 254)]
    return rgb


class TestEligibility(unittest.TestCase):

    def test_discrete_table_is_eligible(self):
        """The reflectivity .pal table is 14 colours - the case that fits."""
        ct = png8.product_colortable("refl")
        self.assertLessEqual(len(ct), png8.MAX_CT)
        self.assertTrue(png8.eligible("refl"))

    def test_continuous_ramps_are_not(self):
        """Measured on real frames: every LUT entry of a continuous ramp
        APPEARS in production output (a smooth field sweeps its whole ramp),
        and the ramps carry 257-871 entries. Quantizing them would nearest-map
        genuine fill colours - the outcome this encoder exists to refuse."""
        for product in ("mslp_wind", "clean_ir", "rh_layer", "env_precip"):
            ct = png8.product_colortable(product)
            self.assertGreater(len(ct), png8.MAX_CT, product)
            self.assertFalse(png8.eligible(product), product)

    def test_unknown_product_is_not_eligible(self):
        self.assertFalse(png8.eligible("no_such_product"))

    def test_colortable_is_memoized(self):
        a = png8.product_colortable("refl")
        b = png8.product_colortable("refl")
        self.assertIs(a, b)


class TestTranscode(unittest.TestCase):

    def test_small_frame_is_fully_lossless(self):
        """<=256 distinct colours: every pixel identical, not just the fills."""
        rgb = _fill_frame(n_aa=40)
        out, st = png8.transcode(_png(rgb), CT)
        self.assertTrue(st["lossless"])
        self.assertTrue((_decode(out) == rgb).all())
        self.assertEqual(st["aa_pixels_changed"], 0)

    def test_ct_pixels_bit_exact_when_aa_overflows(self):
        """>256 distinct colours: AA pixels may move, colortable pixels may
        NOT - and the transcode itself verifies that on every pixel before
        returning, so this test double-checks the checker."""
        rgb = _fill_frame(n_aa=400)
        out, st = png8.transcode(_png(rgb), CT)
        self.assertFalse(st["lossless"])
        back = _decode(out)
        ct_set = {tuple(c) for c in CT}
        mask = np.array([[tuple(px) in ct_set for px in row] for row in rgb])
        self.assertTrue((back[mask] == rgb[mask]).all(),
                        "a colortable pixel changed")
        self.assertGreater(st["aa_pixels_changed"], 0)
        # No size assertion here: on a tiny synthetic the fixed 768-byte
        # palette dominates. The byte win is a real-frame property - measured
        # 32.3% of PNG-24 on a production reflectivity frame.

    def test_oversized_colortable_refuses(self):
        """300+ colortable colours present cannot fit: raise, never
        nearest-map fills. This is the continuous-ramp case in miniature."""
        big_ct = np.stack([np.arange(300) % 256,
                           np.arange(300) // 2,
                           np.full(300, 7)], axis=1).astype(np.uint8)
        rgb = np.zeros((20, 300, 3), dtype=np.uint8)
        rgb[:, :, :] = big_ct[np.newaxis, :, :]
        with self.assertRaises(png8.Unrepresentable):
            png8.transcode(_png(rgb), big_ct)

    def test_stats_are_honest(self):
        rgb = _fill_frame(n_aa=400)
        _, st = png8.transcode(_png(rgb), CT)
        self.assertEqual(st["ct_colors_present"], len(CT))
        self.assertGreater(st["distinct_colors"], 256)
        self.assertGreaterEqual(st["aa_max_channel_delta"],
                                st["aa_mean_channel_delta"])


class TestSaveFig(unittest.TestCase):
    """The production entry point: PNG-8 for eligible products, byte-preserving
    PNG-24 fallback for everything else."""

    def _fig(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig = plt.figure(figsize=(2, 2))
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_axis_off()
        ax.imshow(np.tile(CT[np.newaxis, :, :], (6, 1, 1)),
                  interpolation="nearest")
        return plt, fig

    def test_eligible_product_writes_p_mode(self):
        import tempfile
        import os
        plt, fig = self._fig()
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "f.png")
            st = png8.save_fig(fig, p, product="refl", dpi=50, facecolor="black")
            self.assertIsNotNone(st)
            self.assertEqual(Image.open(p).mode, "P")
        plt.close(fig)

    def test_ineligible_product_writes_png24_unchanged(self):
        import tempfile
        import os
        plt, fig = self._fig()
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "f.png")
            st = png8.save_fig(fig, p, product="mslp_wind", dpi=50,
                               facecolor="black")
            self.assertIsNone(st)
            self.assertIn(Image.open(p).mode, ("RGB", "RGBA"))
        plt.close(fig)

    def test_unknown_product_falls_back_not_raises(self):
        import tempfile
        import os
        plt, fig = self._fig()
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "f.png")
            st = png8.save_fig(fig, p, product="never_heard_of_it", dpi=50,
                               facecolor="black")
            self.assertIsNone(st)
            self.assertTrue(os.path.getsize(p) > 0)
        plt.close(fig)


if __name__ == "__main__":
    unittest.main()
