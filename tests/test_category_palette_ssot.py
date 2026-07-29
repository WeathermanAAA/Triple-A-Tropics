"""The SSHWS category palette has exactly ONE definition.

Before the 2026-07-29 consolidation the seven category colors were defined in
at least five places that had visibly drifted from one another - the HAFS chip
table, models/enscenters.js, season_animation.js, recon/recon.js and
ace_core - plus copies in the tracks generators, the CycloLab shell, the
records explorer and the active banner. Fixing that once is easy; keeping it
fixed is what this test is for.

Three guarantees:

1. ``test_no_stray_category_hex`` - a category hex appears NOWHERE in the tree
   except the palette module and the two files generated from it. This is the
   one that actually prevents regression: the cheap way to "fix" a color
   locally is to paste the hex, and that fails here.
2. ``test_generated_files_are_fresh`` - ``tat_palette.js`` / ``tat_palette.css``
   equal a fresh emit, so the browser mirror cannot lag the Python source (and
   a hand-edit of either generated file is caught).
3. ``test_ace_core_uses_shared_palette`` + the threshold/contrast checks - the
   feed's ``SSHS_COLORS`` really is the shared table, and the palette's own
   structural invariants hold.

Run: ``python -m unittest discover tests``
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "palette"))
# Repo-source-first, per the house pattern: an installed ace-core can lag the
# diff and green-light a broken edit (see tests/overlay_test_util.py).
sys.path.insert(0, str(REPO / "ace_core"))

from tat_palettes import categories as cat  # noqa: E402
from tat_palettes import emit  # noqa: E402

# The palette's own files: the definition plus everything generated from it.
# Anything else in the tree holding one of these hexes is drift.
ALLOWED = {
    "palette/tat_palettes/categories.py",
    "tat_palette.js",
    "tat_palette.css",
}

# Build output, dependencies, and caches - not source under review.
SKIP_DIR_PARTS = {".git", "node_modules", "__pycache__", "build", ".venv",
                  "site-packages", ".egg-info"}

# GENERATED site artifacts. These are BAKED pages: the generators inject the
# palette at render time (see generate_tracks_plot._apply_icon_tokens), so the
# committed output necessarily contains literal hexes and must not be scanned.
# Listed explicitly rather than pattern-skipped so a NEW baked page has to be
# added here deliberately, with a human deciding it really is generated.
GENERATED = {
    "global_tracks.html",
    "al_tracks.html", "ep_tracks.html", "wp_tracks.html",
    "al_ace.html", "ep_ace.html", "wp_ace.html",
}

# Extensions worth scanning: everything that can carry a color to a browser.
SCAN_SUFFIXES = {".py", ".js", ".cjs", ".mjs", ".css", ".html", ".json",
                 ".yml", ".yaml", ".md", ".svg"}

HEXES = {c: cat.CATEGORY_HEX[c] for c in cat.CATEGORY_ORDER}


def _iter_source_files():
    """Every tracked source file, minus build output and generated pages."""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO,
                         capture_output=True, text=True, check=True)
    for name in out.stdout.split("\0"):
        if not name:
            continue
        rel = pathlib.PurePosixPath(name)
        if any(p in SKIP_DIR_PARTS for p in rel.parts):
            continue
        if name in ALLOWED or name in GENERATED:
            continue
        if rel.suffix.lower() not in SCAN_SUFFIXES:
            continue
        path = REPO / name
        if path.is_file():
            yield name, path


class TestCategoryPaletteSSOT(unittest.TestCase):

    def test_no_stray_category_hex(self):
        """No category hex outside the palette module and its generated files."""
        # Case-insensitive: #E63222 is the same color to a browser, and would
        # be the same drift.
        pattern = re.compile("|".join(re.escape(h) for h in HEXES.values()),
                             re.IGNORECASE)
        strays = []
        for name, path in _iter_source_files():
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                m = pattern.search(line)
                if m:
                    strays.append(f"{name}:{lineno}: {m.group(0)}  |  {line.strip()[:90]}")
        self.assertEqual(
            [], strays,
            "category hex(es) found outside palette/tat_palettes/categories.py.\n"
            "Import the palette instead of pasting the color:\n"
            "  python  -> from tat_palettes.categories import CATEGORY_HEX\n"
            "  js      -> window.TATPalette.cats  (load /tat_palette.js first)\n"
            "  css     -> var(--cat-c3)           (link /tat_palette.css)\n\n"
            + "\n".join(strays))

    def test_generated_files_are_fresh(self):
        """tat_palette.js / .css equal a fresh emit from the Python source."""
        with tempfile.TemporaryDirectory() as td:
            emit.write(pathlib.Path(td))
            for fname in (emit.JS_NAME, emit.CSS_NAME):
                fresh = (pathlib.Path(td) / fname).read_text(encoding="utf-8")
                committed = (REPO / fname).read_text(encoding="utf-8")
                self.assertEqual(
                    fresh, committed,
                    f"{fname} is stale or hand-edited - regenerate with "
                    "`python -m tat_palettes.emit --out-dir .` and commit.")

    def test_palette_invariants(self):
        """Thresholds tile the wind axis, every swatch stays legible, and the
        fine obs ramp is category-exact at each threshold."""
        self.assertEqual(7, cat.verify_thresholds())
        self.assertEqual(7, cat.verify_contrast())
        self.assertEqual(7, cat.verify_wind_ramp())

    def test_thresholds_are_the_published_sshws_bins(self):
        """The recolor must not have moved a boundary."""
        self.assertEqual(
            {"TD": 0, "TS": 34, "C1": 64, "C2": 83, "C3": 96, "C4": 113,
             "C5": 137},
            dict(cat.CATEGORY_MIN_KT))

    def test_ace_core_uses_shared_palette(self):
        """The feed's SSHS_COLORS IS the shared table, not a copy of it."""
        import ace_core
        self.assertIs(ace_core.SSHS_COLORS, cat.CATEGORY_HEX)
        # And the classifier still agrees with the shared thresholds.
        for kt, expected in ((0, "TD"), (33, "TD"), (34, "TS"), (63, "TS"),
                             (64, "C1"), (82, "C1"), (83, "C2"), (95, "C2"),
                             (96, "C3"), (112, "C3"), (113, "C4"), (136, "C4"),
                             (137, "C5"), (200, "C5")):
            self.assertEqual(expected, ace_core.sshs_class(kt), f"{kt} kt")
            self.assertEqual(expected, cat.category_for_kt(kt), f"{kt} kt")

    def test_js_mirror_agrees_with_python(self):
        """The browser mirror classifies and colors identically to Python."""
        import json
        import shutil
        node = shutil.which("node")
        if node is None:
            self.skipTest("node not on PATH")
        probe = """
          require(process.argv[2]);
          var P = globalThis.TATPalette, out = {cats: P.cats, ink: P.ink,
            glyphs: P.glyphs, minKt: P.minKt, order: P.order,
            byKt: {}, wind: {}};
          [0,33,34,63,64,82,83,95,96,112,113,136,137,200].forEach(function (k) {
            out.byKt[k] = [P.catForKt(k), P.colorForKt(k)];
          });
          P.windRamp.forEach(function (r) { out.wind[r[0]] = r[1]; });
          console.log(JSON.stringify(out));
        """
        with tempfile.TemporaryDirectory() as td:
            probe_path = pathlib.Path(td) / "probe.cjs"
            probe_path.write_text(probe, encoding="utf-8")
            res = subprocess.run(
                [node, str(probe_path), str(REPO / "tat_palette.js")],
                capture_output=True, text=True, timeout=60)
        self.assertEqual(0, res.returncode, res.stderr)
        got = json.loads(res.stdout)

        self.assertEqual(dict(cat.CATEGORY_HEX), got["cats"])
        self.assertEqual(dict(cat.CATEGORY_INK), got["ink"])
        self.assertEqual(dict(cat.CATEGORY_GLYPH), got["glyphs"])
        self.assertEqual(dict(cat.CATEGORY_MIN_KT), got["minKt"])
        self.assertEqual(list(cat.CATEGORY_ORDER), got["order"])
        for kt_str, (js_cat, js_color) in got["byKt"].items():
            kt = int(kt_str)
            self.assertEqual(cat.category_for_kt(kt), js_cat, f"{kt} kt class")
            self.assertEqual(cat.color_for_kt(kt), js_color, f"{kt} kt color")
        self.assertEqual({str(k): v for k, v in cat.wind_ramp()}, got["wind"])


if __name__ == "__main__":
    unittest.main()
