"""
Geo-trace regression for the shared basemap antimeridian fill (models/regions.js).

Runs regions_basemap_smoke.cjs under jsdom (skips cleanly when node/jsdom absent)
and asserts the FILL path is chord-free: a contiguous landmass near the seam is one
continuous subpath, a ring that truly straddles the seam splits into two closed
subpolygons, and neither ever draws a segment across the polygon interior (the old
triangular-wedge bug). Run: python -m unittest discover tests
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "regions_basemap_smoke.cjs"
REGIONS = REPO / "models" / "regions.js"
NODE = shutil.which("node")


def jsdom_available() -> bool:
    if NODE is None:
        return False
    probe = subprocess.run([NODE, "-e", "require('jsdom')"], cwd=str(REPO),
                           capture_output=True, text=True)
    return probe.returncode == 0


class TestRegionsBasemap(unittest.TestCase):
    def setUp(self):
        if not jsdom_available():
            self.skipTest("node + jsdom required")
        proc = subprocess.run([NODE, str(HARNESS), str(REGIONS)], cwd=str(REPO),
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"harness failed:\n{proc.stderr}")
        self.r = json.loads(proc.stdout)

    def test_no_chord_across_interior(self):
        # NO fill subpath segment may jump more than half the map width: such a jump
        # is a chord closing across the polygon interior (the wedge artifact).
        half = self.r["halfW"]
        for case in ("wedge", "seam", "plain"):
            self.assertLess(self.r[case]["maxJump"], half,
                            f"{case}: chord detected (maxJump {self.r[case]['maxJump']} >= {half})")

    def test_seam_crosser_splits_into_two_closed_subpolys(self):
        # A ring straddling the antimeridian relative to the extent is drawn as TWO
        # closed subpolygons (one per visible edge), never one chorded loop.
        self.assertEqual(self.r["seam"]["moves"], 2, "seam ring should split into 2 subpaths")
        self.assertEqual(self.r["seam"]["closes"], 2, "both seam subpaths must be closed")

    def test_contiguous_landmass_is_one_closed_subpath(self):
        # The Australia-like landmass near the unwrap boundary stays a single
        # continuous closed ring (no spurious split, no chord).
        self.assertEqual(self.r["wedge"]["moves"], 1)
        self.assertGreaterEqual(self.r["wedge"]["closes"], 1)
        self.assertEqual(self.r["plain"]["moves"], 1)
        self.assertGreaterEqual(self.r["plain"]["closes"], 1)


if __name__ == "__main__":
    unittest.main()
