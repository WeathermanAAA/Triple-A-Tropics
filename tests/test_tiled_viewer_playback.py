"""Discoverable driver for the explorer tiled-viewer playback smoke test.

Runs tests/tiled_viewer_playback_smoke.cjs under plain node (no jsdom: the
module's playback paths only touch maplibregl + fetch, both stubbed by the
harness). Proves the no-strobe contract of satellite/explorer/tiled_viewer.js:
gated reveals, full-loop residency (no in-loop eviction), staggered preload
with loading/loaded status, the out-of-order reveal token, and readiness
isolation across products that share stamps.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "tiled_viewer_playback_smoke.cjs"
NODE = shutil.which("node")


@unittest.skipIf(NODE is None, "node not on PATH")
class TestTiledViewerPlayback(unittest.TestCase):
    def test_playback_smoke(self):
        proc = subprocess.run(
            [NODE, str(HARNESS)],
            cwd=str(REPO), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(
            proc.returncode, 0,
            msg=f"tiled-viewer playback harness failed:\n{proc.stdout}\n{proc.stderr}",
        )
        self.assertIn("ALL CHECKS PASSED", proc.stdout)


if __name__ == "__main__":
    unittest.main()
