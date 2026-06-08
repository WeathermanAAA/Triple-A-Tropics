"""Cross-repo SST palette contract - the MAIN-REPO side.

CycloLab's storm-centered hero layers (tat-satellite-render
cyclolab_sst.py) MIRROR the house SST ramps so the per-storm panels
read as the same product family as the site's SST pages. The mirror is
a byte-pinned copy on each side (the cyclolab-router resolve() pattern:
same literals pinned in both repos' suites).

THIS test pins the house generator's stops to the shared literals by
READING THE SOURCE (no heavy imports - generate_sst_plots pulls
netCDF4/matplotlib at module import). If you change a ramp here,
change cyclolab_sst.py in tat-satellite-render and BOTH pins.
"""
import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "generate_sst_plots.py"

ACTUAL_STOPS = [
    (0.00, "#2c0b4a"), (0.08, "#2a1794"), (0.18, "#2f4bc4"),
    (0.28, "#2e8bd0"), (0.38, "#2fc4c9"), (0.50, "#6bd98e"),
    (0.62, "#e7ee5f"), (0.72, "#f5b23d"), (0.82, "#e84b2a"),
    (0.92, "#b01a26"), (1.00, "#6b0d18"),
]
ANOM_STOPS = [
    (0.00, "#1a0c5f"), (0.08, "#1a2b9e"), (0.18, "#2261c7"),
    (0.30, "#4695db"), (0.40, "#8bc0ea"), (0.47, "#cde5f5"),
    (0.495, "#f2f7fb"), (0.50, "#ffffff"), (0.506, "#fdf4ea"),
    (0.53, "#f8d5b8"), (0.58, "#efac86"), (0.65, "#df815f"),
    (0.73, "#cc4836"), (0.82, "#9f1e26"), (0.90, "#6d1321"),
    (0.96, "#3f0c23"), (1.00, "#ef37b8"),
]
LAND_GRAY = "#5f6b7a"


def _stops_from_source(func_name: str) -> list[tuple[float, str]]:
    src = SRC.read_text(encoding="utf-8")
    start = src.index(f"def {func_name}(")
    nxt = src.find("\ndef ", start + 1)
    body = src[start:nxt if nxt > 0 else len(src)]
    return [(float(a), b.lower()) for a, b in
            re.findall(r"\(\s*([\d.]+)\s*,\s*\"(#[0-9a-fA-F]{6})\"\s*\)",
                       body)]


class TestHouseRampPins(unittest.TestCase):
    def test_actual_ramp_matches_the_shared_literals(self):
        got = _stops_from_source("_sst_actual_cmap")
        self.assertEqual(got, ACTUAL_STOPS,
                         "house sst_actual ramp drifted from the "
                         "cross-repo pin - update cyclolab_sst.py in "
                         "tat-satellite-render AND both pins")

    def test_anom_ramp_matches_the_shared_literals(self):
        got = _stops_from_source("_sst_anom_cmap")
        self.assertEqual(got, ANOM_STOPS,
                         "house sst_anom ramp drifted from the "
                         "cross-repo pin - update cyclolab_sst.py in "
                         "tat-satellite-render AND both pins")

    def test_land_gray_is_pinned(self):
        src = SRC.read_text(encoding="utf-8")
        self.assertIn(LAND_GRAY, src,
                      "the NaN/land gray left the house generator")


if __name__ == "__main__":
    unittest.main(verbosity=2)
