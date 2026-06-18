"""Discoverable driver for the /satellite/ "Last 6h" loop-window smoke test.

Runs tests/sat_loop_window_smoke.cjs under node (jsdom). The .cjs loads
satellite/index.html, drives the Live Storm Floater viewer with stubbed
fetch/Image/rAF, and asserts the loop window + scrubber clamp behaviour:
default "All" is unchanged, "Last 6h" clamps the loop + scrubber to the
trailing 6h (correct boundary), the loop wraps to the window start (not 0),
manual stepping is clamped, and <6h of frames loops everything.

Skips cleanly when node or jsdom is unavailable (the .cjs harness, like the
other tests/*.cjs ones, needs `npm install --no-save jsdom`).
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "sat_loop_window_smoke.cjs"
PAGE = REPO / "satellite" / "index.html"
NODE = shutil.which("node")


def _jsdom_available() -> bool:
    if NODE is None:
        return False
    try:
        r = subprocess.run(
            [NODE, "-e", "require('jsdom')"],
            cwd=str(REPO), capture_output=True, text=True, timeout=30,
        )
        return r.returncode == 0
    except Exception:
        return False


@unittest.skipIf(NODE is None, "node not on PATH")
@unittest.skipUnless(_jsdom_available(), "jsdom not installed (npm install --no-save jsdom)")
class TestSatLoopWindow(unittest.TestCase):
    def test_loop_window_smoke(self):
        proc = subprocess.run(
            [NODE, str(HARNESS), str(PAGE)],
            cwd=str(REPO), capture_output=True, text=True, timeout=120,
        )
        # surface the harness's per-check log on failure for a readable diff
        self.assertEqual(
            proc.returncode, 0,
            msg=f"sat loop-window harness failed:\n{proc.stdout}\n{proc.stderr}",
        )
        self.assertIn("ALL CHECKS PASSED", proc.stdout)


if __name__ == "__main__":
    unittest.main()
