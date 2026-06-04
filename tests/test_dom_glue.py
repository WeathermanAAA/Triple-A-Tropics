"""Browser-grade test of LIVE_BASIN_JS's DOM glue (the half the byte-parity
tests cannot reach): fetch -> validateFeed -> atomic swap -> as-of update,
plus the fail-closed paths (fetch error, year mismatch, basin mismatch).

Hermetic: the baked page is rendered in-test from an empty-season fixture
payload via render_html (no IBTrACS CSV, no Natural Earth, no network);
the "live feed" is the same fixture set used by the parity tests.

Needs node + jsdom. jsdom is NOT a repo dependency — install transiently
with `npm install --no-save jsdom` (repo root or anywhere on NODE_PATH);
the test skips cleanly when it is absent.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from overlay_test_util import NODE, REPO, gtp  # noqa: E402
from test_live_overlay_parity import make_fixture_storms, make_payload  # noqa: E402

SMOKE = Path(__file__).resolve().parent / "dom_smoke.cjs"
BASIN = "ep"
YEAR = 2026


def jsdom_available() -> bool:
    if NODE is None:
        return False
    probe = subprocess.run([NODE, "-e", "require('jsdom')"],
                           cwd=REPO, capture_output=True, text=True)
    return probe.returncode == 0


def render_baked_page() -> str:
    """The page exactly as main() would write it for an empty season —
    render_html + _apply_icon_tokens, basemap omitted (grid-only)."""
    payload = {
        "basin": BASIN,
        "basin_name": gtp.BASINS[BASIN]["full_name"],
        "year": YEAR,
        "updated": "2026-06-04 00:00 UTC",
        "header": {"named": 0, "cat1plus": 0, "cat3plus": 0, "cat5": 0,
                   "total_ace": 0.0},
        "vocab": gtp.BASINS[BASIN]["vocab"],
        "storms": [],
    }
    extent = gtp.BASINS[BASIN]["extent"]
    return gtp._apply_icon_tokens(gtp.render_html(payload, extent, None, None))


def fixture_feed() -> dict:
    storms = make_fixture_storms(gtp.BASINS[BASIN]["extent"])
    payload = make_payload(BASIN, storms)
    return {
        "basin": BASIN,
        "year": YEAR,
        "updated": "2026-06-04 12:34 UTC",
        "header": payload["header"],
        "vocab": payload["vocab"],
        "storms": storms,
    }


@unittest.skipIf(NODE is None, "node not on PATH")
@unittest.skipUnless(jsdom_available(),
                     "jsdom not resolvable — npm install --no-save jsdom")
class TestDomGlue(unittest.TestCase):

    def test_swap_and_fail_closed_paths(self):
        with tempfile.TemporaryDirectory() as td:
            page = Path(td) / "page.html"
            feed = Path(td) / "feed.json"
            page.write_text(render_baked_page(), encoding="utf-8")
            feed.write_text(json.dumps(fixture_feed()), encoding="utf-8")
            proc = subprocess.run(
                [NODE, str(SMOKE), str(page), str(feed)],
                cwd=REPO, capture_output=True, text=True, timeout=120,
            )
        self.assertEqual(
            proc.returncode, 0,
            f"DOM smoke failed:\n{proc.stdout}\n{proc.stderr}")


if __name__ == "__main__":
    unittest.main()
