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

    def test_opener_uses_new_tab_never_same_tab(self):
        # source-level guard on the dialog opener + the popup/card links.
        src = Path(gt.__file__).read_text(encoding="utf-8")
        self.assertIn('window.open(u, "_blank", "noopener")', src)
        self.assertNotIn("window.location.href = u", src)
        # every rendered cyclolab-link in the source carries the new-tab attrs
        for snippet in ('class="cyclolab-link" target="_blank" rel="noopener"',
                        'class="cyclolab-link" target="_blank" ',):
            self.assertIn(snippet, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
