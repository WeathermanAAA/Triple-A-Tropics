#!/usr/bin/env python3
"""Container layout tests (spec #27): geometric plan, offset-verified blocks,
range-read fidelity, salvage semantics, and the #28 member reservation."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hafs_render import hafs_container as hc  # noqa: E402


class TestGeometricPlan(unittest.TestCase):
    def test_full_row_43_hours(self):
        fxx = list(range(0, 127, 3))          # 43 hours
        blocks = hc.plan_blocks(fxx)
        self.assertEqual([len(b) for b in blocks], [1, 2, 4, 8, 16, 12])
        self.assertEqual(blocks[0], [0])       # f000 alone: instant publish
        self.assertEqual(sum(blocks, []), fxx) # nothing lost, order kept

    def test_salvage_prefix_plans_the_same_way(self):
        """A deadline salvage's truncated hour list is a valid plan: complete
        leading blocks, one short trailing block - something ALWAYS ships."""
        fxx = list(range(0, 70, 3))            # f000..f069 (24 hours)
        blocks = hc.plan_blocks(fxx)
        self.assertEqual([len(b) for b in blocks], [1, 2, 4, 8, 9])
        self.assertEqual(blocks[0], [0])

    def test_degenerate_rows(self):
        self.assertEqual(hc.plan_blocks([]), [])
        self.assertEqual(hc.plan_blocks([0]), [[0]])
        self.assertEqual([len(b) for b in hc.plan_blocks([0, 3, 6, 9])],
                         [1, 2, 1])

    def test_gappy_hours_supported(self):
        """Missing mid-row hours (a failed frame) never break the plan - the
        plan packs what exists."""
        blocks = hc.plan_blocks([0, 6, 9, 24, 48])
        self.assertEqual(sum(blocks, []), [0, 6, 9, 24, 48])


class TestBlockWriteAndRangeRead(unittest.TestCase):
    def _frames(self, td, hours):
        out = {}
        for i, fxx in enumerate(hours):
            p = Path(td) / f"src{fxx:03d}.png"
            # Distinct, odd-sized payloads so padding math is exercised.
            p.write_bytes(bytes([i % 251]) * (1000 + 137 * i))
            out[fxx] = p
        return out

    def test_range_read_returns_exact_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            frames = self._frames(td, [0, 3, 6])
            blocks = hc.row_container_plan(frames, Path(td) / "row")
            for b in blocks:
                blob = (Path(td) / "row" / b["key"]).read_bytes()
                for fxx in b["fxx"]:
                    off, size = b["members"][hc.member_name(fxx)]
                    self.assertEqual(blob[off:off + size],
                                     frames[fxx].read_bytes(),
                                     f"range mismatch f{fxx:03d}")

    def test_offset_verification_catches_corruption(self):
        with tempfile.TemporaryDirectory() as td:
            frames = self._frames(td, [0])
            blocks = hc.row_container_plan(frames, Path(td) / "row")
            path = Path(td) / "row" / blocks[0]["key"]
            bad = {k: [v[0] + 512, v[1]]
                   for k, v in blocks[0]["members"].items()}
            with self.assertRaises(RuntimeError):
                hc._verify_block(path, bad)

    def test_value_plane_members_ride_the_same_block(self):
        """#28 reservation: extra kinds land beside their frame in the SAME
        block and the same index - readouts are one more ranged read, never a
        layout change."""
        with tempfile.TemporaryDirectory() as td:
            frames = self._frames(td, [0, 3])
            vp = {}
            for fxx, p in frames.items():
                v = Path(td) / f"vals{fxx:03d}.bin"
                v.write_bytes(b"V" * (500 + fxx))
                vp[fxx] = v
            blocks = hc.row_container_plan(frames, Path(td) / "row",
                                           extra_kinds={"values.png": vp})
            names = set()
            for b in blocks:
                names |= set(b["members"])
            self.assertIn("f000.png", names)
            self.assertIn("f000.values.png", names)
            b0 = blocks[0]
            blob = (Path(td) / "row" / b0["key"]).read_bytes()
            off, size = b0["members"]["f000.values.png"]
            self.assertEqual(blob[off:off + size], vp[0].read_bytes())

    def test_write_reduction_on_the_measured_catalog(self):
        """The headline number, from the REAL cycle shape (165 rows x 43
        hours, measured 2026-08-03 on cycle 2026072818): 6 blocks per row."""
        per_row = len(hc.plan_blocks(list(range(0, 127, 3))))
        self.assertEqual(per_row, 6)
        self.assertAlmostEqual((165 * per_row) / 7095, 0.1395, places=3)


if __name__ == "__main__":
    unittest.main()
