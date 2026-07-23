"""Unit tests for the explorer emitter (tc_records/explorer.py).

Covers the rules the map client depends on: per-storm longitude unwrapping
across the dateline, landfall extraction (only valid-position L rows),
report-link routing, fix flag bits, and NaN-free strict-JSON output.
"""

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from tc_records import explorer, metrics, sources  # noqa: E402


def fix(t, wind, lat=15.0, lon=-40.0, pres=np.nan, rec="",
        sid="HURDAT2_AL011999", name="TEST", season=1999, trop=True):
    ts = pd.Timestamp(t)
    return {"sid": sid, "atcf": "AL011999", "name": name, "season": season,
            "time": ts, "lat": lat, "lon": lon, "wind": float(wind),
            "pres": pres, "status": "HU", "trop": trop,
            "syn": ts.hour in {0, 6, 12, 18} and ts.minute == 0,
            "src": "test", "rec": rec}


def run_emit(rows, basin="al"):
    fixes = pd.DataFrame(rows, columns=sources.CANON_COLS)
    storms = metrics.compute_storms(fixes)
    results = {basin: {
        "cfg": {"name": "Test", "wind_note": "n", "ace_note": "a",
                "records_since": 1851, "satellite_era": 1966},
        "fixes": fixes, "storms": storms,
        "boards": [{"key": "k", "page": "p", "title": "T",
                    "rows": [{"sid": rows[0]["sid"], "rank": 1}]}],
    }}
    out = Path(tempfile.mkdtemp())
    explorer.emit_explorer(results, out, 2026)
    cat = json.loads((out / f"catalog_{basin}.json").read_text())
    dec = (rows[0]["season"] // 10) * 10
    tracks = json.loads((out / f"tracks_{basin}_{dec}.json").read_text())
    return cat, tracks


class TestUnwrap(unittest.TestCase):
    def test_dateline_continuity_eastward(self):
        lons = np.array([178.0, 179.5, -179.0, -177.5])
        out = explorer._unwrap_lons(lons)
        self.assertTrue(all(abs(np.diff(out)) < 20))
        self.assertAlmostEqual(out[2], 181.0)

    def test_dateline_continuity_westward(self):
        lons = np.array([-178.0, -179.9, 179.8, 178.0])
        out = explorer._unwrap_lons(lons)
        self.assertTrue(all(abs(np.diff(out)) < 20))
        self.assertAlmostEqual(out[2], -180.2)


class TestEmit(unittest.TestCase):
    def storm_rows(self):
        t0 = dt.datetime(1999, 9, 1, 0)
        rows = []
        for i in range(6):
            rows.append(fix(t0 + dt.timedelta(hours=6 * i), 80 + 5 * i,
                            lat=20.0 + i, lon=-70.0 - i, pres=980 - i))
        # landfall special row (non-synoptic) + one with missing position
        rows.append(fix(t0 + dt.timedelta(hours=33, minutes=30), 100,
                        lat=26.0, lon=-77.0, rec="L"))
        rows.append(fix(t0 + dt.timedelta(hours=45, minutes=15), 95,
                        lat=np.nan, lon=np.nan, rec="L"))
        return rows

    def test_catalog_and_flags(self):
        cat, tracks = run_emit(self.storm_rows())
        s = cat["storms"][0]
        # only the valid-position landfall row survives
        self.assertEqual(len(s["lf"]), 1)
        self.assertEqual(s["lf"][0][0], "199909020930")
        self.assertEqual(s["rec"], [["k", "p", "T", 1]])
        pts = tracks["tracks"][str(s["i"])]
        # NaN-position rows are dropped from the track entirely
        self.assertEqual(len(pts), 7)
        lf_pts = [p for p in pts if p[5] & explorer.FLAG_LANDFALL]
        self.assertEqual(len(lf_pts), 1)
        self.assertFalse(lf_pts[0][5] & explorer.FLAG_SYNOPTIC)
        syn_pts = [p for p in pts if p[5] & explorer.FLAG_SYNOPTIC]
        self.assertEqual(len(syn_pts), 6)
        # strict JSON round-trips (allow_nan=False held)
        json.dumps(cat, allow_nan=False)

    def test_report_links(self):
        self.assertIn("AL122005_Katrina.pdf",
                      explorer._report_link("al", "AL122005", "KATRINA",
                                            2005)[1])
        self.assertEqual(explorer._report_link("al", "AL081999", "X",
                                               1998)[0], "NHC archive")
        self.assertEqual(explorer._report_link("wp", "WP132026", "Y",
                                               2026)[0],
                         "JTWC annual report")
        self.assertIsNone(explorer._report_link("al", "AL011900", "Z", 1900))


if __name__ == "__main__":
    unittest.main()
