"""
Guards the /models/ JS cache-busting (scripts/stamp_model_assets.py).

Asserts every local /models/*.js <script src> in models/index.html carries a
?v=<content-hash> AND that the hash is CURRENT (matches the file on disk). If
someone edits a model JS without re-stamping, this fails with a clear nudge - so
a stale deploy can never silently ship. Run: python -m unittest discover tests
"""
import os
import pathlib
import re
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import stamp_model_assets as stamp  # noqa: E402


class TestAssetVersioning(unittest.TestCase):
    def setUp(self):
        self.html = (REPO / "models" / "index.html").read_text()

    def test_html_is_current(self):
        # Re-stamping a current HTML is a no-op; if changed, the committed hashes
        # are stale relative to the JS files on disk.
        new_html, changed, versions = stamp.stamp_html(self.html, REPO)
        self.assertFalse(
            changed,
            "models/index.html asset ?v= hashes are STALE - run "
            "`python scripts/stamp_model_assets.py` and commit.")
        self.assertTrue(versions, "no /models/*.js <script> tags were versioned")

    def test_every_local_model_js_is_versioned(self):
        # Every committed /models/*.js script src must already carry a ?v= hash.
        for m in re.finditer(r'<script\b[^>]*\bsrc="(/models/[^"]+\.js)([^"]*)"', self.html):
            src, query = m.group(1), m.group(2)
            self.assertRegex(query, r"^\?v=[0-9a-f]{6,}$",
                             f"{src} is not cache-busted (src='{src}{query}')")

    def test_hash_changes_when_file_changes(self):
        # A content change to a model JS must change its stamped ?v= (the whole
        # point: a fresh deploy busts the cache). Simulate by stamping an HTML
        # against a mutated copy of the repo.
        import tempfile, shutil
        before = stamp.stamp_html(self.html, REPO)[2]
        self.assertIn("/models/enscenters.js", before)
        with tempfile.TemporaryDirectory() as d:
            tmp = pathlib.Path(d)
            (tmp / "models").mkdir()
            for name in ("enscenters.js", "regions.js", "hafs.js"):
                shutil.copy(REPO / "models" / name, tmp / "models" / name)
            # mutate one file
            f = tmp / "models" / "enscenters.js"
            f.write_text(f.read_text() + "\n// cache-bust probe\n")
            after = stamp.stamp_html(self.html, tmp)[2]
        self.assertNotEqual(before["/models/enscenters.js"], after["/models/enscenters.js"])
        self.assertEqual(before["/models/regions.js"], after["/models/regions.js"])  # unchanged file, same hash


if __name__ == "__main__":
    unittest.main()
