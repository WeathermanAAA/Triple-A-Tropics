"""qscatobs units: colocation-table parse (obs time vs the misleading
filename timestamp), listing regex, slug policy, and the build tick's
skip/budget behavior against a local store."""
import datetime as dt
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qscatobs import build as qbuild  # noqa: E402
from qscatobs import fetch  # noqa: E402
from sarobs.store import LocalStore  # noqa: E402

COLOC_FIXTURE = """
 Colocations of QuikSCAT storm passes with best track (header junk)
    MO/DY/YR  Storm_Name  Season  Number  Type  Basin  Max_Speed  Year  Day  QS_Rev  QS_Time       Lat       Lon
    08/28/05     KATRINA    2005      11    H5     AL        145  2005  240   32244   112711   25.6523  -87.6078
    08/23/05     KATRINA    2005      11    TD     AL         30  2005  235   32179   223959   23.2000  -75.5000
    09/20/03      RITA-X    2005      12    H1     AL         80  2005  263   99999   000000   24.0000  -80.0000
"""

LISTING_FIXTURE = """
<a href="QS_S1B32244.20052401859.avewr_BYU_KATRINA_082805_WRave3.gz">f</a>
<a href="QS_S1B32179.20052351910.avewr_BYU_KATRINA_082305_WRave3.gz">f</a>
<a href="QS_S1B32244.20052401859.avewr_BYU_KATRINA_082805_WRave2.gz">f</a>
<a href="QS_S1B32244_WRave3_map.gif">g</a>
"""


class Colocation(unittest.TestCase):
    def test_parse_uses_obs_time_not_filename(self):
        rows = fetch.load_colocation(COLOC_FIXTURE)
        self.assertEqual(len(rows), 3)
        r = [x for x in rows if x["rev"] == 32244][0]
        # DOY 240 + 11:27:11 == the OBSERVATION time; the filename says
        # 20052401859 (JPL creation time) and must never be used
        self.assertEqual(r["t"], dt.datetime(2005, 8, 28, 11, 27, 11,
                                             tzinfo=dt.timezone.utc))
        self.assertEqual(r["type"], "H5")
        self.assertEqual(r["bt_wind_kt"], 145)
        self.assertAlmostEqual(r["bt_lat"], 25.6523)

    def test_storm_filter(self):
        rows = fetch.load_colocation(COLOC_FIXTURE)
        kat = fetch.storm_colocs(rows, "al", 2005, "katrina")
        self.assertEqual(sorted(kat), [32179, 32244])

    def test_listing_wrave3_only_dedup(self):
        with mock.patch.object(fetch, "get_text",
                               return_value=LISTING_FIXTURE):
            ps = fetch.list_passes("AL", 2005, "KATRINA")
        self.assertEqual([p["rev"] for p in ps], [32179, 32244])
        self.assertTrue(all(p["file"].endswith("WRave3.gz") for p in ps))


class SlugPolicy(unittest.TestCase):
    def test_no_storm_number_in_slug(self):
        # BYU's per-season count mismatches ATCF (Katrina AL12 vs BYU 11):
        # the slug must not carry a number at all
        self.assertEqual(qbuild.storm_slug("AL", 11, 2005, "KATRINA"),
                         "al2005_katrina")


class BuildTick(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = LocalStore(self.tmp)
        self.rows = fetch.load_colocation(COLOC_FIXTURE)

    def _run(self, max_new=99, fetched=b"GZ"):
        passes = [{"file": "a.gz", "rev": 32179},
                  {"file": "b.gz", "rev": 32244}]
        with mock.patch.object(qbuild.fetch, "list_passes",
                               return_value=passes), \
             mock.patch.object(qbuild.fetch, "get_bytes",
                               return_value=fetched), \
             mock.patch.object(qbuild.decode, "load_byu_hrwind",
                               return_value={"ok": True}), \
             mock.patch.object(qbuild.render, "render_pass",
                               return_value=(b"PNG", {
                                   "valid_frac": 0.8,
                                   "t": "2005-08-28T11:27:11Z"})):
            return qbuild.build_storm(self.store, "AL", 2005, "KATRINA",
                                      self.rows, max_new=max_new,
                                      log=lambda *a: None)

    def test_build_indexes_and_resumes(self):
        s1 = self._run(max_new=1)
        self.assertEqual(s1["new"], 1)
        s2 = self._run()
        self.assertEqual(s2["new"], 1)             # only the missing pass
        idx = self.store.get_json("qscat/al2005_katrina/index.json")
        self.assertEqual([p["rev"] for p in idx["passes"]], [32179, 32244])
        man = self.store.get_json("qscat/manifest.json")
        self.assertEqual(man["storms"][0]["n_passes"], 2)
        self.assertEqual(man["storms"][0]["peak_bt_kt"], 145)

    def test_fetch_failure_skips_not_fails(self):
        s = self._run(fetched=None)
        self.assertEqual(s["new"], 0)
        self.assertIsNone(
            self.store.get_json("qscat/al2005_katrina/index.json"))


if __name__ == "__main__":
    unittest.main()
