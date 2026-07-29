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

    def test_regex_robustness(self):
        # The stamper must: replace an existing ?v=, replace a DIFFERENT query
        # (not silently skip it), never touch a /models/*.json src or an external
        # script, and be idempotent.
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / "models").mkdir()
            (root / "models" / "a.js").write_text("A")
            (root / "models" / "b.js").write_text("B")
            (root / "models" / "data.json").write_text("{}")
            html = (
                '<script src="/models/a.js?v=deadbeef99"></script>\n'             # stale ?v= -> refreshed
                '<script src="/models/b.js?foo=bar"></script>\n'                  # other query -> replaced
                '<script src="/models/data.json"></script>\n'                     # .json -> untouched
                '<script src="https://cdnjs.cloudflare.com/x/gif.js"></script>\n' # external -> untouched
                '<script src="/models/a.js"></script>\n'                          # no query -> ?v= added
            )
            out, changed, _ = stamp.stamp_html(html, root)
            ha, hb = stamp.file_hash(root / "models" / "a.js"), stamp.file_hash(root / "models" / "b.js")
            self.assertTrue(changed)
            self.assertIn(f'/models/a.js?v={ha}"', out)
            self.assertIn(f'/models/b.js?v={hb}"', out)
            self.assertNotIn("foo=bar", out)            # different query was NOT skipped
            self.assertNotIn("deadbeef99", out)         # stale hash refreshed
            self.assertIn('/models/data.json"', out)    # .json untouched, not mangled to data.js?v=
            self.assertNotIn("data.js?v=", out)
            self.assertIn('cdnjs.cloudflare.com/x/gif.js"', out)  # external untouched
            out2, changed2, _ = stamp.stamp_html(out, root)
            self.assertFalse(changed2)                  # idempotent


class TestPaletteVersioning(unittest.TestCase):
    """The shared category palette is referenced site-wide, so a stale ?v=
    would leave one page rendering last week's colors - the exact drift the
    palette consolidation exists to prevent."""

    def test_palette_refs_are_current(self):
        changed, versions = stamp.stamp_palette(REPO, write=False)
        self.assertFalse(
            changed,
            "/tat_palette.{js,css} ?v= hashes are STALE - run "
            "`python scripts/stamp_model_assets.py` and commit.")
        self.assertTrue(versions, "no page references the shared palette")

    def test_every_palette_ref_is_versioned(self):
        bare = []
        for path in stamp.iter_html(REPO):
            html = path.read_text()
            for m in re.finditer(
                    r'(?:src|href)="(/tat_palette\.(?:js|css))([^"]*)"', html):
                if not re.fullmatch(r"\?v=[0-9a-f]{6,}", m.group(2)):
                    bare.append(f"{path.relative_to(REPO)}: {m.group(1)}{m.group(2)}")
        self.assertFalse(bare, "unversioned palette references: " + ", ".join(bare))


if __name__ == "__main__":
    unittest.main()
