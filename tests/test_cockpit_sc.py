"""ASCAT cockpit field: satellite backdrop + barb hover tooltips
(2026-07-13). Drives tests/cockpit_sc_smoke.cjs — the jsdom harness walks
the REAL cockpit_fields.js paths: clean-IR backdrop swap (pane.product
untouched, imagery re-shown), thinned-cell retention, nearest-barb tooltip
content (kt + FROM° + lat/lon + sensor + pass time), backdrop 'none'
restore, and clearPaneField restore. Needs node on PATH (GH runners and
codespaces have it)."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SMOKE = ROOT / "tests" / "cockpit_sc_smoke.cjs"
TARGET = ROOT / "satellite" / "explorer" / "cockpit_fields.js"


class CockpitScSmokeTest(unittest.TestCase):
    def test_backdrop_and_hover(self):
        if shutil.which("node") is None:
            self.skipTest("node not on PATH")
        r = subprocess.run(
            ["node", str(SMOKE), str(TARGET)],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(
            r.returncode, 0,
            f"cockpit_sc smoke failed:\n{r.stdout}\n{r.stderr}")
        self.assertIn("PASS", r.stdout)


if __name__ == "__main__":
    unittest.main()
