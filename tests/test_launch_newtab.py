"""Launch-flow contract: the CycloLab entry opens the lab in a NEW TAB.

Pins (per the corrective spec): the entry <a> carries target="_blank" +
rel="noopener" and resolves to /cyclolab/{sid}/; the dialog opener uses
window.open(... "_blank" ...) and never navigates same-tab; invest cards
have no launch path.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import generate_tracks_plot as gt  # noqa: E402

ACTIVE = {
    "sid": "NHC_EP022026", "name": "TWO-E", "max_category": "TD",
    "current_category": "TD", "is_active": True, "is_invest": False,
    "peak_wind_kt": 30, "peak_pressure_mb": 1003, "ace": 0.0,
    "start": "2026-06-07", "end": None,
    "points": [{"lat": 15.3, "lon": -99.5, "wind_kt": 30,
                "t": "2026-06-08T00:00:00Z", "nature": "TS"}],
}
INVEST = {**ACTIVE, "sid": "NHC_EP902026", "name": "90E",
          "is_active": False, "is_invest": True}


class TestLaunchNewTab(unittest.TestCase):

    def test_active_card_link_opens_new_tab_to_storm_page(self):
        card = gt.render_storm_card(ACTIVE)
        self.assertIn('class="cyclolab-link"', card)
        self.assertIn('target="_blank"', card)
        self.assertIn('rel="noopener"', card)
        self.assertIn('href="/cyclolab/NHC_EP022026/"', card)

    def test_invest_card_has_no_launch_path(self):
        card = gt.render_storm_card(INVEST)
        self.assertNotIn("cyclolab-link", card)
        self.assertNotIn("/cyclolab/", card)

    def _render_basin_page(self, storms):
        vocab = {"named": "named", "cat1plus": "≥C1", "cat3plus": "≥C3",
                 "cat5": "C5", "ace": "ACE"}
        payload = {"basin": "ep", "basin_name": "East Pacific", "year": 2026,
                   "storms": storms, "title": "EP 2026", "as_of": "x",
                   "updated": "x", "vocab": vocab,
                   "header": {"named": 1, "cat1plus": 0, "cat3plus": 0,
                              "cat5": 0, "total_ace": 0.0}}
        return gt.render_html(payload, gt.BASINS["ep"]["extent"], None, None)

    def test_per_basin_marker_is_a_newtab_anchor_to_the_storm(self):
        # the storm glyph itself is wrapped in a native new-tab anchor - no
        # dialog, no JS handler (survives the live redraw because it is rebuilt
        # as an anchor each time).
        html = self._render_basin_page([ACTIVE])
        self.assertIn(
            '<a href="/cyclolab/NHC_EP022026/" target="_blank" '
            'rel="noopener"><g class="active-icon"', html)
        # the live page has NO pre-launch dialog and NO same-tab navigation.
        self.assertNotIn("cl-launch", html)
        self.assertNotIn("openDialog", html)
        self.assertNotIn("window.location.href = u", html)

    def test_global_map_popup_info_only_and_marker_anchor(self):
        src = gt.GLOBAL_MAPLIBRE_HTML
        # info-only hover popup: no interactive cyclolab link inside it.
        self.assertNotIn("cyclolab-link", src)
        # the active-hurricane glyph is wrapped in a new-tab anchor; no
        # click->pin handler remains (click is the native anchor).
        self.assertIn("encodeURIComponent(props.storm_id)", src)
        self.assertIn('target="_blank" rel="noopener"', src)
        self.assertNotIn("pinned = true", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
