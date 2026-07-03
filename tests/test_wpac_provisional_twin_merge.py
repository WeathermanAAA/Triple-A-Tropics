"""WPAC provisional-twin merge — the UNNAMED-ghost fix.

THE BUG (2026-07, live home/global-tracks map): a freshly-formed WPAC system
showed up TWICE — once from the live JTWC b-deck/knackwx path under its agency
sid (``JTWC_WP102026``, named/numbered) and once from IBTrACS as an UNNAMED
provisional NR entry under IBTrACS's own sid (``2026183N17115``). The two never
merged, because ``merge_named_sources`` contests only by NAME and the IBTrACS
side is the UNNAMED placeholder it skips — so the map drew a duplicate UNNAMED
ghost alongside the real, named system. Same class as the 07W knackwx-designation
gap: JTWC has no CurrentStorms feed.

THE FIX, in three parts (all covered here):
  1. ``load_ibtracs_current_year`` remaps the IBTrACS current-season sid to the
     agency form via ``USA_ATCF_ID`` (``agency_sid_from_atcf_id``), so the twin
     and its live designation share ONE sid.
  2. ``merge_and_extract_storms`` resolves the resulting cross-source sid
     collision by keeping the source with more obs (the pristine live b-deck),
     dropping the sparse IBTrACS twin — no half-and-half track.
  3. A designated-but-unnamed system (its "name" is the ATCF spelled-out cardinal
     "TEN", a "#NN" fallback, or a placeholder) surfaces its ``##<letter>`` ATCF
     id (``10W``); a real JMA/NHC name (``BAVI``) is kept.
"""
from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ace_core as ac  # noqa: E402
import generate_tracks_plot as tg  # noqa: E402

WP_CFG = {"short": "wp", "agency_name": "JTWC", "invest_letter": "W"}
NOW = dt.datetime.utcnow()


def _bdeck_rows(num, season, *, name, nature, wind, n=6, lat0=15.0, lon0=130.0):
    """``n`` recent 6-hourly live b-deck fixes for a designated system, ending
    ~6 h ago (so is_active is True). Shaped exactly like ``parse_bdeck`` output."""
    last = NOW - dt.timedelta(hours=6)
    out = []
    for i in range(n):
        out.append({
            "SID": f"JTWC_WP{num:02d}{season}",
            "NAME": name, "season": season,
            "time": last - dt.timedelta(hours=6 * (n - 1 - i)),
            "lat": lat0 + 0.4 * i, "lon": lon0 - 0.5 * i,
            "wind_kt": float(wind), "pressure_mb": 1000.0,
            "nature": nature, "ace_nature": nature,
            "source": "live-JTWC", "storm_num": num, "spawn_invest": None,
        })
    return out


def _ibtracs_twin_rows(sid, season, *, n=3, lat0=15.2, lon0=130.3):
    """``n`` IBTrACS provisional-NR fixes for the SAME system, shaped like
    ``load_ibtracs_current_year`` output AFTER the sid remap (sid already the
    agency form). UNNAMED, NR-natured, no storm_num — the twin the map ghosted."""
    last = NOW - dt.timedelta(hours=18)   # lags the b-deck, fewer obs
    return [{
        "SID": sid, "NAME": "UNNAMED", "season": season,
        "time": last - dt.timedelta(hours=6 * (n - 1 - i)),
        "lat": lat0 + 0.4 * i, "lon": lon0 - 0.5 * i,
        "wind_kt": 25.0 + i, "pressure_mb": 1004.0,
        "nature": "NR", "ace_nature": "NR", "source": "IBTrACS",
    } for i in range(n)]


class AgencySidFromAtcfIdTests(unittest.TestCase):
    def test_maps_wp_designation(self):
        self.assertEqual(
            ac.agency_sid_from_atcf_id("WP092026", WP_CFG, 2026),
            "JTWC_WP092026")

    def test_maps_nhc_designation(self):
        self.assertEqual(
            ac.agency_sid_from_atcf_id("EP042026",
                                       {"short": "ep", "agency_name": "NHC"},
                                       2026),
            "NHC_EP042026")

    def test_rejects_invest_00_and_foreign_and_year(self):
        self.assertIsNone(ac.agency_sid_from_atcf_id("WP912026", WP_CFG, 2026))
        self.assertIsNone(ac.agency_sid_from_atcf_id("WP002026", WP_CFG, 2026))
        # foreign basin id carried in the WP file -> keep raw sid
        self.assertIsNone(ac.agency_sid_from_atcf_id("EP052026", WP_CFG, 2026))
        # year mismatch -> the sid would not match this build's b-deck
        self.assertIsNone(ac.agency_sid_from_atcf_id("WP102026", WP_CFG, 2025))

    def test_rejects_blank_and_garbage(self):
        for bad in ("", None, "  ", "WP", "XX9926", "WPzz2026"):
            self.assertIsNone(ac.agency_sid_from_atcf_id(bad, WP_CFG, 2026), bad)


class AtcfNumberNameTests(unittest.TestCase):
    def test_spelled_cardinals_are_designations(self):
        for w in ("TEN", "ten", "ONE", "NINETEEN", "TWENTY", "TWENTY-THREE",
                  "NINETY-NINE"):
            self.assertTrue(ac._is_atcf_number_name(w), w)

    def test_real_names_are_not(self):
        for w in ("BAVI", "DOUGLAS", "SINLAKU", "ARTHUR", "", None, "10W"):
            self.assertFalse(ac._is_atcf_number_name(w), w)


class MergeGhostTests(unittest.TestCase):
    def _extract(self, ibtracs_rows, live_rows):
        ib = pd.DataFrame(ibtracs_rows) if ibtracs_rows else pd.DataFrame()
        lv = pd.DataFrame(live_rows) if live_rows else pd.DataFrame()
        return ac.merge_and_extract_storms(ib, lv, WP_CFG)

    def test_td_twin_merges_and_labels_as_number_w(self):
        """UNNAMED IBTrACS twin + live TD b-deck whose ATCF name is 'TEN' ->
        ONE storm labelled '10W' (##W), NOT 'UNNAMED', NOT 'TEN'."""
        # A TD is tropical-natured ("TS") but sub-TS wind, so max_category "TD".
        live = _bdeck_rows(10, 2026, name="TEN", nature="TS", wind=30, n=8)
        twin = _ibtracs_twin_rows("JTWC_WP102026", 2026, n=3)
        storms = self._extract(twin, live)
        self.assertEqual(len(storms), 1, storms)
        s = storms[0]
        self.assertEqual(s["sid"], "JTWC_WP102026")
        self.assertEqual(s["name"], "10W")
        self.assertEqual(s["max_category"], "TD")
        self.assertTrue(s["is_active"])
        self.assertFalse(s["is_invest"])
        # pristine live track: all 8 b-deck fixes, NO NR fix from the twin
        self.assertEqual(len(s["points"]), 8)
        self.assertNotIn("NR", {p["nature"] for p in s["points"]})

    def test_ts_twin_merges_and_keeps_jma_name(self):
        """UNNAMED IBTrACS twin + live named-TS b-deck -> ONE storm keeping the
        JMA name 'BAVI'."""
        live = _bdeck_rows(9, 2026, name="BAVI", nature="TS", wind=70, n=10)
        twin = _ibtracs_twin_rows("JTWC_WP092026", 2026, n=5)
        storms = self._extract(twin, live)
        self.assertEqual(len(storms), 1, storms)
        s = storms[0]
        self.assertEqual(s["name"], "BAVI")
        self.assertEqual(s["sid"], "JTWC_WP092026")
        self.assertEqual(len(s["points"]), 10)
        self.assertNotIn("NR", {p["nature"] for p in s["points"]})

    def test_no_unnamed_in_global_geojson(self):
        """The home-map feed: after the merge no active WPAC marker/track is
        UNNAMED, and the labels are '10W' / 'BAVI'."""
        storms = self._extract(
            _ibtracs_twin_rows("JTWC_WP102026", 2026, n=3),
            _bdeck_rows(10, 2026, name="TEN", nature="TS", wind=30, n=8))
        storms += self._extract(
            _ibtracs_twin_rows("JTWC_WP092026", 2026, n=5),
            _bdeck_rows(9, 2026, name="BAVI", nature="TS", wind=70, n=10))
        for s in storms:
            s["basin"] = "wp"
        fc = ac.build_global_geojson(storms)
        names = {(p := f["properties"]).get("name") or p.get("storm_name")
                 for f in fc["features"]}
        self.assertNotIn("UNNAMED", names)
        markers = {f["properties"]["name"] for f in fc["features"]
                   if f["properties"]["kind"] == "active_marker"}
        self.assertEqual(markers, {"10W", "BAVI"})

    def test_designation_survives_when_ibtracs_is_fuller(self):
        """THE REGRESSION GUARD. Even when the IBTrACS twin has MORE obs than a
        short live b-deck (the fresh-designation window: b-deck starts at TCFA
        with 1-2 fixes while IBTrACS carries a longer provisional precursor), the
        merged storm MUST keep its designation ('03W') — never re-collapse to
        UNNAMED. The union keeps live's storm_num, so the relabel still fires."""
        live = _bdeck_rows(3, 2026, name="TEN", nature="TS", wind=30, n=2)   # short
        twin = _ibtracs_twin_rows("JTWC_WP032026", 2026, n=6)                # fuller
        storms = self._extract(twin, live)
        self.assertEqual(len(storms), 1, storms)
        s = storms[0]
        self.assertNotEqual(s["name"], "UNNAMED")   # ghost must NOT return
        self.assertEqual(s["name"], "03W")           # designation preserved
        self.assertTrue(s["is_active"])
        # union track: IBTrACS-only precursor fixes fill genuine gaps
        self.assertGreaterEqual(len(s["points"]), 6)
        # live's storm_num propagated (the designation carrier survived)
        self.assertTrue(any(p.get("storm_num") == 3 for p in
                            pd.DataFrame(twin + live).to_dict("records")))

    def test_jma_name_survives_when_ibtracs_is_fuller(self):
        """Same collision, but the short live b-deck already carries a real JMA
        name — it must WIN over the IBTrACS UNNAMED twin's larger obs count, not
        be discarded into a bare '05W'."""
        live = _bdeck_rows(5, 2026, name="HAGUPIT", nature="TS", wind=45, n=2)
        twin = _ibtracs_twin_rows("JTWC_WP052026", 2026, n=6)
        storms = self._extract(twin, live)
        self.assertEqual(len(storms), 1, storms)
        self.assertEqual(storms[0]["name"], "HAGUPIT")

    def test_dissipated_stub_plus_full_ibtracs_keeps_designation(self):
        """A dissipated JTWC TD trimmed to a 3-fix stub ('TEN', num=10) while
        IBTrACS holds the full ~12-fix UNNAMED track under the remapped sid: one
        storm, labelled '10W', track not lost — no UNNAMED ghost."""
        live = _bdeck_rows(10, 2026, name="TEN", nature="TS", wind=30, n=3)
        twin = _ibtracs_twin_rows("JTWC_WP102026", 2026, n=12,
                                  lat0=12.0, lon0=140.0)
        storms = self._extract(twin, live)
        self.assertEqual(len(storms), 1, storms)
        self.assertEqual(storms[0]["name"], "10W")
        self.assertGreaterEqual(len(storms[0]["points"]), 12)

    def test_genuinely_nameless_stays_unnamed(self):
        """A system with NO name and NO ATCF designation (no storm_num, raw
        IBTrACS sid) is the ONE case that legitimately stays UNNAMED."""
        rows = [{
            "SID": "2026183N17115", "NAME": "UNNAMED", "season": 2026,
            "time": NOW - dt.timedelta(hours=6 + 6 * (2 - i)),
            "lat": 17.0 + i, "lon": 113.0 - i,
            "wind_kt": 25.0, "pressure_mb": 1005.0,
            "nature": "NR", "ace_nature": "NR", "source": "IBTrACS",
        } for i in range(3)]
        storms = self._extract(rows, [])
        self.assertEqual(len(storms), 1)
        self.assertEqual(storms[0]["name"], "UNNAMED")


class LoadIbtracsRemapTests(unittest.TestCase):
    """Part 1: load_ibtracs_current_year remaps the sid via USA_ATCF_ID."""

    _COLS = ["SID", "SEASON", "BASIN", "NAME", "ISO_TIME", "NATURE",
             "LAT", "LON", "USA_ATCF_ID", "USA_STATUS", "TRACK_TYPE",
             "USA_WIND", "WMO_WIND", "TOKYO_WIND",
             "USA_PRES", "WMO_PRES", "TOKYO_PRES"]

    def _write_csv(self, path, rows):
        # IBTrACS ships a 2-row header (names, then units); load skips row 1.
        lines = [",".join(self._COLS), ",".join("_" for _ in self._COLS)]
        for r in rows:
            lines.append(",".join(str(r.get(c, "")) for c in self._COLS))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _row(self, sid, atcf, name, iso, lat, lon):
        return {"SID": sid, "SEASON": 2026, "BASIN": "WP", "NAME": name,
                "ISO_TIME": iso, "NATURE": "NR", "LAT": lat, "LON": lon,
                "USA_ATCF_ID": atcf, "USA_STATUS": "TD",
                "TRACK_TYPE": "PROVISIONAL", "USA_WIND": 30,
                "WMO_WIND": "", "TOKYO_WIND": "", "USA_PRES": 1004,
                "WMO_PRES": "", "TOKYO_PRES": ""}

    def test_sid_remapped_via_usa_atcf_id(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            csv = Path(td) / "ibtracs.WP.csv"
            # storm A: has USA_ATCF_ID -> sid must remap to the agency form.
            # storm B: blank USA_ATCF_ID -> raw IBTrACS sid must survive.
            self._write_csv(csv, [
                self._row("2026183N17115", "WP102026", "UNNAMED",
                          "2026-07-01 18:00:00", 17.3, 115.0),
                self._row("2026183N17115", "WP102026", "UNNAMED",
                          "2026-07-02 00:00:00", 17.6, 113.1),
                self._row("2026190N05150", "", "UNNAMED",
                          "2026-07-09 00:00:00", 5.0, 150.0),
                self._row("2026190N05150", "", "UNNAMED",
                          "2026-07-09 06:00:00", 5.2, 149.5),
            ])
            df = tg.load_ibtracs_current_year(csv, tg.BASINS["wp"], 2026)
        sids = set(df["SID"])
        self.assertIn("JTWC_WP102026", sids)          # remapped
        self.assertNotIn("2026183N17115", sids)        # raw form gone
        self.assertIn("2026190N05150", sids)           # blank id -> raw kept


if __name__ == "__main__":
    unittest.main()
