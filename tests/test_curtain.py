"""Pre-launch curtain: every public page loads /curtain.js first in <head>, and the key hash is set."""
import re, subprocess, unittest, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP = re.compile(r'^(historical/|tests/|stream/|stream\.html$|[a-z]+_(ace|tracks)\.html$|global_tracks\.html$)')


class CurtainTests(unittest.TestCase):
    def test_every_page_has_curtain_first(self):
        files = subprocess.check_output(["git", "ls-files", "*.html"], cwd=ROOT).decode().split()
        missing = []
        for f in files:
            if SKIP.search(f): continue
            s = open(os.path.join(ROOT, f), encoding="utf8").read()
            head = s[:s.lower().find("</head>")]
            scripts = re.findall(r"<script\b[^>]*>", head, re.I)
            if not scripts or "curtain.js" not in scripts[0]: missing.append(f)
        self.assertEqual(missing, [], "pages without the curtain as their first script")

    def test_hash_is_set(self):
        s = open(os.path.join(ROOT, "curtain.js"), encoding="utf8").read()
        h = re.search(r'var HASH = "([^"]*)"', s).group(1)
        self.assertRegex(h, r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
