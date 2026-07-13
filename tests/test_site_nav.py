"""Site-wide top-nav canonicalization.

The nav is hand-duplicated in every page (static site, no build step), so
every new top-level section historically went missing from most pages'
navs (Satellite, then Models, then Recon, then Subseasonal...). This test
kills that bug class: it globs EVERY tracked .html file containing a
nav-links block and asserts the canonical link set, order, and
active-state. Adding a new top-level section = update CANONICAL here in
the same commit that adds the link everywhere (this test is the reminder).
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# (href, label) in display order. Twitter is checked for target/rel too.
CANONICAL = [
    ("/", "Home"),
    ("/climatology/", "Climatology"),
    ("/sst/", "SST"),
    ("/satellite/", "Satellite"),
    ("/models/", "Models"),
    ("/subseasonal/", "Subseasonal"),
    ("/recon/", "Recon"),
    ("https://twitter.com/WeathermanAAA_", "Twitter"),
]

# top-level dir (or "" for the home page) -> href that must carry .active
ACTIVE_FOR_SECTION = {
    "": "/",
    "climatology": "/climatology/",
    "sst": "/sst/",
    "satellite": "/satellite/",
    "models": "/models/",
    "subseasonal": "/subseasonal/",
    "recon": "/recon/",
}

# nav-hidden utility pages: standard chrome, but NO section -> zero active
NO_ACTIVE_SECTIONS = {"bugs"}

NAV_BLOCK = re.compile(r'<div class="nav-links">(.*?)</div>', re.S)
ANCHOR = re.compile(r'<a\s+href="([^"]+)"([^>]*)>([^<]+)</a>')

SKIP_DIRS = {"node_modules", ".git", ".claude"}  # .claude holds old page snapshots


def nav_pages():
    for p in sorted(ROOT.rglob("*.html")):
        if SKIP_DIRS & set(part for part in p.parts):
            continue
        text = p.read_text(errors="replace")
        m = NAV_BLOCK.search(text)
        if m:
            yield p, m.group(1)


class TestSiteNav(unittest.TestCase):
    def test_every_nav_is_canonical(self):
        pages = list(nav_pages())
        self.assertGreaterEqual(
            len(pages), 17, "nav-links sweep found suspiciously few pages"
        )
        for path, block in pages:
            rel = path.relative_to(ROOT)
            links = ANCHOR.findall(block)
            with self.subTest(page=str(rel)):
                self.assertEqual(
                    [(h, t.strip()) for h, t, _ in
                     [(h, txt, attrs) for h, attrs, txt in links]],
                    CANONICAL,
                    f"{rel}: nav links differ from the canonical set/order",
                )
                actives = [h for h, attrs, _ in links if "active" in attrs]
                section = rel.parts[0] if len(rel.parts) > 1 else ""
                if section in NO_ACTIVE_SECTIONS:
                    self.assertEqual(
                        len(actives), 0,
                        f"{rel}: nav-hidden page must mark nothing active",
                    )
                else:
                    self.assertEqual(
                        len(actives), 1,
                        f"{rel}: expected exactly one active link",
                    )
                    expected = ACTIVE_FOR_SECTION.get(section)
                    if expected is not None:
                        self.assertEqual(
                            actives[0], expected,
                            f"{rel}: active link should be {expected}",
                        )
                # The external link must not leak the opener.
                tw_attrs = [a for h, a, _ in links if h.startswith("https://twitter")]
                self.assertIn("noopener", tw_attrs[0], f"{rel}: Twitter rel=noopener")


if __name__ == "__main__":
    unittest.main()
