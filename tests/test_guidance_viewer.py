"""Drives ``tests/guidance_viewer_harness.cjs`` under the repo's playwright so
the guidance component's honesty properties are covered by the normal suite.

The harness renders ``guidance/guidance.js`` against two REAL-shaped documents -
an NHC-basin storm (full suite) and a JTWC-basin storm (raw ensembles only) -
and asserts what the viewer must REFUSE to draw:

  * no consensus tab, envelope or skill baseline in a JTWC basin;
  * an ensemble MEAN is never presented as a consensus;
  * withheld consensus members render distinctly from absent ones;
  * late aids are badged rather than blended with early ones.

Skipped when node or playwright is unavailable (the browser is not part of the
minimal install), so the suite stays runnable on a bare checkout.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

HARNESS = Path(__file__).resolve().parent / "guidance_viewer_harness.cjs"
NODE = shutil.which("node")


def _have_playwright() -> bool:
    if not NODE:
        return False
    r = subprocess.run(
        [NODE, "-e", "require.resolve('playwright')"],
        cwd=str(REPO), capture_output=True, text=True)
    return r.returncode == 0


def _adeck(techs, basin="EP", cy=7, dtg="2026072812"):
    out = []
    for t in techs:
        for tau in (0, 12, 24, 36, 48):
            out.append(
                f"{basin}, {cy:02d}, {dtg},   , {t}, {tau:4d}, "
                f"{218 + tau}N, 0{651 + tau}W, {90 - tau}, {970 + tau}, XX, "
                f" 34, NEQ,    0,    0,    0,    0,")
    return "\n".join(out)


@unittest.skipUnless(_have_playwright(), "node + playwright required")
class TestGuidanceViewer(unittest.TestCase):

    def test_harness_assertions_all_pass(self):
        from guidance import build_guidance as bg

        # NHC basin: the full suite, including a LATE raw aid so the badging
        # and the dashed-stroke legend have something to report.
        ep = bg.build_document(
            _adeck(["OFCL", "OCD5", "TVCN", "IVCN", "AVNI", "HWFI", "CTCI",
                    "AVNO", "DSHP"]),
            _adeck(["BEST"], dtg="2026072800"),
            basin="ep", cy=7, year=2026)
        # JTWC basin: raw ensembles only.
        wp = bg.build_document(
            _adeck(["AEMN", "AC00", "AP01", "AP02"], basin="WP", cy=12),
            None, basin="wp", cy=12, year=2026)

        self.assertTrue(ep["consensus_membership"], "fixture must exercise the strip")
        self.assertEqual(wp["consensus_membership"], [])

        with tempfile.TemporaryDirectory() as td:
            ep_p, wp_p = Path(td) / "ep.json", Path(td) / "wp.json"
            ep_p.write_text(json.dumps(ep), encoding="utf-8")
            wp_p.write_text(json.dumps(wp), encoding="utf-8")
            proc = subprocess.run(
                [NODE, str(HARNESS), str(ep_p), str(wp_p)],
                cwd=str(REPO), capture_output=True, text=True, timeout=180,
                env={**__import__("os").environ, "GV_SHOT_DIR": td})
        if proc.returncode != 0:
            self.fail("guidance viewer harness failed:\n"
                      + proc.stdout + "\n" + proc.stderr)
        self.assertIn("all assertions passed", proc.stdout)


if __name__ == "__main__":
    unittest.main()
