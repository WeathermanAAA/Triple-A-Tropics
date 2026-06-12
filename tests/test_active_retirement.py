"""Upstream is_active retirement (ace_core): final-advisory + 24 h window.

The lingering-CRISTINA bug, upstream edition: NHC writes no terminal EX/DS
b-deck row for a TD that simply stops, so the nature gate never fires and
is_active degraded to a 60 h staleness window — every surface fed by the
flag (home global-tracks live markers, LIVE STATUS active counts, per-basin
live overlays) showed a dissipated storm as ACTIVE for ~2.5 days.

The fix is STATUS ONLY by construction: these tests pin that a retired
storm keeps its full track, its points, and its ACE contribution — it just
stops being active.
"""
from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ace_core as ac  # noqa: E402

EP_CFG = {"short": "ep", "agency_name": "NHC", "invest_letter": "E"}
WP_CFG = {"short": "wp", "agency_name": "JTWC", "invest_letter": "W"}


def _rows(sid_prefix, num, season, *, hours_stale, n=3, nature="TS",
          wind=30.0, name="CRISTINA"):
    """n 6-hourly live-shaped fixes ending ``hours_stale`` hours ago."""
    now = dt.datetime.utcnow()
    last = now - dt.timedelta(hours=hours_stale)
    return [{
        "SID": f"{sid_prefix}{num:02d}{season}",
        "NAME": name, "season": season,
        "time": last - dt.timedelta(hours=6 * (n - 1 - i)),
        "lat": 12.0 + 0.2 * i, "lon": -89.0 - 0.2 * i,
        "wind_kt": wind, "pressure_mb": 1006.0,
        "nature": nature, "ace_nature": nature,
        "source": "live-NHC", "storm_num": num,
    } for i in range(n)]


def _extract(rows, cfg, sids):
    storms = ac.merge_and_extract_storms(
        pd.DataFrame(), pd.DataFrame(rows), cfg, nhc_active_sids=sids)
    assert len(storms) == 1, storms
    return storms[0]


class FinalAdvisoryRetirementTests(unittest.TestCase):
    def test_cristina_case_retired_status_only(self):
        """18 h stale (inside the 24 h window — the window alone keeps her
        active), absent from a cleanly-fetched CurrentStorms -> retired.
        Track + points + ACE byte-identical to the un-retired build."""
        rows = _rows("NHC_EP", 3, 2026, hours_stale=18)
        retired = _extract(rows, EP_CFG, set())          # clean fetch, empty
        kept = _extract(rows, EP_CFG, {"EP032026"})      # NHC still lists her
        no_info = _extract(rows, EP_CFG, None)           # fetch failed

        self.assertFalse(retired["is_active"])
        self.assertTrue(kept["is_active"])
        self.assertTrue(no_info["is_active"])
        # STATUS ONLY: everything except the flag is identical.
        for k in ("sid", "name", "ace", "points", "peak_wind_kt",
                  "start", "end", "latest_fix_valid_utc"):
            self.assertEqual(retired[k], kept[k], k)
        self.assertEqual(len(retired["points"]), 3)

    def test_fresh_fix_survives_listing_hiccup(self):
        # 3 h stale, absent from the list: inside NHC_RETIRE_GRACE_H, so a
        # transient CurrentStorms hiccup can't retire a storm with fresh fixes.
        s = _extract(_rows("NHC_EP", 5, 2026, hours_stale=3), EP_CFG, set())
        self.assertTrue(s["is_active"])

    def test_wp_storms_exempt(self):
        # JTWC basin: CurrentStorms has no coverage; the window governs.
        s = _extract(_rows("JTWC_WP", 6, 2026, hours_stale=18, name="JANGMI"),
                     WP_CFG, set())
        self.assertTrue(s["is_active"])

    def test_invests_exempt(self):
        # CurrentStorms never lists invests (90-99): absence must not retire.
        s = _extract(_rows("NHC_EP", 92, 2026, hours_stale=18, name="INVEST"),
                     EP_CFG, set())
        self.assertTrue(s["is_invest"])
        self.assertTrue(s["is_active"])

    def test_marker_drops_with_retirement(self):
        """The home map's live marker comes from build_global_geojson's
        is_active fork: retired -> no active marker, but the track feature
        set is otherwise produced from the same storm dict."""
        rows = _rows("NHC_EP", 3, 2026, hours_stale=18)
        retired = _extract(rows, EP_CFG, set())
        kept = _extract(rows, EP_CFG, {"EP032026"})

        def marker(s):
            fc = ac.build_global_geojson([s])
            ms = [f for f in fc["features"]
                  if f["properties"]["kind"] == "active_marker"]
            return ms[0]["properties"]["marker_type"] if ms else None

        self.assertEqual(marker(kept), "hurricane")
        self.assertIsNone(marker(retired))

    def test_window_tightened_to_24h(self):
        self.assertEqual(ac.ACTIVE_WINDOW_HOURS, 24)
        # 30 h stale: inactive purely on the window, even when NHC's list
        # still carries the id (CurrentStorms lags are bounded by the window).
        s = _extract(_rows("NHC_EP", 7, 2026, hours_stale=30), EP_CFG,
                     {"EP072026"})
        self.assertFalse(s["is_active"])

    def test_season_totals_unchanged_by_retirement(self):
        """THE critical constraint: retirement must not erase the storm from
        the season — named/category tallies and season ACE are computed from
        the same storm dicts and must be identical either way."""
        rows = _rows("NHC_EP", 3, 2026, hours_stale=18, wind=40.0)
        retired = _extract(rows, EP_CFG, set())
        kept = _extract(rows, EP_CFG, {"EP032026"})
        self.assertEqual(ac.compute_header_stats([retired]),
                         ac.compute_header_stats([kept]))
        self.assertEqual(retired["ace"], kept["ace"])


if __name__ == "__main__":
    unittest.main()
