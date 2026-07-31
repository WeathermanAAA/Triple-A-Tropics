"""Drives ``tests/models_url_harness.cjs`` so the /models/ URL scheme and its
history policy are covered by the normal suite.

What it proves (in a real browser, against a local server + synthetic
manifest): a fully-specified shared link restores run/storm/model/domain/
product/hour; scrubbing every forecast hour grows history by ZERO entries
(debounced replaceState only); a product switch pushes exactly one entry;
back restores the previous view; and a link whose run has expired falls back
to the current run, keeps the rest of the link, and says so.

Skipped when node or playwright is unavailable.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

HARNESS = Path(__file__).resolve().parent / "models_url_harness.cjs"
NODE = shutil.which("node")


def _have_playwright() -> bool:
    if not NODE:
        return False
    r = subprocess.run([NODE, "-e", "require.resolve('playwright')"],
                       cwd=str(REPO), capture_output=True, text=True)
    return r.returncode == 0


@unittest.skipUnless(_have_playwright(), "node + playwright required")
class TestModelsUrlScheme(unittest.TestCase):

    def test_harness_assertions_all_pass(self):
        proc = subprocess.run([NODE, str(HARNESS)], cwd=str(REPO),
                              capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            self.fail("models URL harness failed:\n"
                      + proc.stdout + "\n" + proc.stderr)
        self.assertIn("all assertions passed", proc.stdout)


if __name__ == "__main__":
    unittest.main()
