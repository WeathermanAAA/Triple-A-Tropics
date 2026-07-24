"""Guards for the /roadmap/ shadow board and its source of truth, roadmap.yml.

Three layers:
  - TestRoadmapData: tests/roadmap_smoke.cjs `data` mode (node, no jsdom) —
    the page's own parser accepts roadmap.yml, the schema validates, and the
    derived model is coherent (statuses/areas enums, shipped dating).
  - TestRoadmapBoardDom: `dom` mode under jsdom — full render/filter/modal/
    content-gated-refresh smoke (skips cleanly when jsdom is missing, like
    the other tests/*.cjs harnesses).
  - TestRoadmapShadow: pure-python honesty guards — the page stays a shadow
    page (noindex, robots-disallowed, unlinked from the rest of the site)
    and the CSS area classes stay in lockstep with AREAS in roadmap.js.

If PyYAML happens to be installed, TestPyYamlCompat cross-parses
roadmap.yml with a real YAML parser and compares the item set — keeps the
file valid standard YAML, not just subset-parseable.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
HARNESS = pathlib.Path(__file__).resolve().parent / "roadmap_smoke.cjs"
NODE = shutil.which("node")

STATUSES = ["shipped", "active", "shadow", "needs-andrew", "next", "planned", "blocked"]
AREAS = ["satellite", "obs", "records", "models", "infra", "apps", "community"]


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
class TestRoadmapData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        proc = subprocess.run(
            [NODE, str(HARNESS), "data"],
            cwd=str(REPO), capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, f"data-mode harness failed:\n{proc.stdout}\n{proc.stderr}"
        cls.model = json.loads(proc.stdout)

    def test_counts_are_coherent(self):
        m = self.model
        self.assertEqual(m["shipped"] + m["open"], m["total"])
        self.assertEqual(sum(len(v) for v in m["byStatus"].values()), m["total"])
        self.assertEqual(sorted(m["byStatus"].keys()), sorted(STATUSES))

    def test_enums(self):
        for it in self.model["items"]:
            self.assertIn(it["status"], STATUSES, it["id"])
            self.assertIn(it["area"], AREAS, it["id"])

    def test_recent_strip_is_newest_first(self):
        dates = [r["date"] for r in self.model["recent"]]
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertLessEqual(len(dates), 10)


@unittest.skipIf(NODE is None, "node not on PATH")
@unittest.skipUnless(_jsdom_available(), "jsdom not installed (npm install --no-save jsdom)")
class TestRoadmapBoardDom(unittest.TestCase):
    def test_dom_smoke(self):
        proc = subprocess.run(
            [NODE, str(HARNESS), "dom"],
            cwd=str(REPO), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(
            proc.returncode, 0,
            msg=f"roadmap dom harness failed:\n{proc.stdout}\n{proc.stderr}",
        )
        self.assertIn("ALL CHECKS PASSED", proc.stdout)


class TestRoadmapShadow(unittest.TestCase):
    """The board must stay a shadow page until Andrew promotes it."""

    def test_robots_disallows_roadmap(self):
        robots = (REPO / "robots.txt").read_text()
        self.assertIn("Disallow: /roadmap/", robots)

    def test_page_is_noindexed(self):
        html = (REPO / "roadmap" / "index.html").read_text()
        self.assertRegex(html, r'<meta name="robots" content="noindex,\s*nofollow">')

    def test_no_site_page_links_to_roadmap(self):
        # Unlinked = no tracked page outside roadmap/ references /roadmap/.
        tracked = subprocess.run(
            ["git", "ls-files", "*.html", "*.js"],
            cwd=str(REPO), capture_output=True, text=True, timeout=30,
        ).stdout.splitlines()
        offenders = []
        for rel in tracked:
            if rel.startswith(("roadmap/", "tests/", "node_modules/")):
                continue
            p = REPO / rel
            if not p.is_file():
                continue
            if re.search(r'href="/roadmap/"', p.read_text(errors="replace")):
                offenders.append(rel)
        self.assertEqual(offenders, [], "shadow page must stay unlinked")

    def test_css_area_classes_match_engine(self):
        html = (REPO / "roadmap" / "index.html").read_text()
        js = (REPO / "roadmap" / "roadmap.js").read_text()
        for area in AREAS:
            self.assertIn(f".a-{area}", html, f"index.html missing .a-{area} color pair")
            self.assertIn(f'key: "{area}"', js, f"roadmap.js missing area {area}")
        # same 7 in both directions: no stray .a-* class defs
        css_areas = set(re.findall(r"\.a-([a-z]+)\s*\{ --ad:", html))
        self.assertEqual(css_areas, set(AREAS))


def _pyyaml():
    try:
        import yaml  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(_pyyaml(), "PyYAML not installed")
@unittest.skipIf(NODE is None, "node not on PATH")
class TestPyYamlCompat(unittest.TestCase):
    """roadmap.yml must stay valid STANDARD yaml, not just subset-parseable."""

    def test_pyyaml_agrees_with_subset_parser(self):
        import yaml
        doc = yaml.safe_load((REPO / "roadmap.yml").read_text())
        # Full round-trip through the page's OWN parser (roadmap.js) so the
        # comparison covers note/title/links text, not just structural fields —
        # block-scalar folding must match standard YAML byte-for-byte.
        proc = subprocess.run(
            [NODE, "-e",
             "const R=require('./roadmap/roadmap.js'),fs=require('fs');"
             "const m=R.validate(R.parseYaml(fs.readFileSync('roadmap.yml','utf8')));"
             "process.stdout.write(JSON.stringify(m.items));"],
            cwd=str(REPO), capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        js = {i["id"]: i for i in json.loads(proc.stdout)}
        self.assertEqual(set(js), {i["id"] for i in doc["items"]}, "item id sets differ")
        for it in doc["items"]:
            j = js[it["id"]]
            for f in ("status", "area", "title"):
                self.assertEqual(it.get(f), j.get(f), f"{it['id']}: {f} differs")
            self.assertEqual(it.get("note"), j.get("note"),
                             f"{it['id']}: note text differs from standard YAML")
            self.assertEqual(
                [l["url"] for l in it.get("links", [])],
                [l["url"] for l in j.get("links", [])],
                f"{it['id']}: link urls differ")


if __name__ == "__main__":
    unittest.main()
