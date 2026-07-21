"""Schema-v2 recon enrichment units: the VDM ref-datetime fix (decode_vdm was
called with date=None, which ALWAYS raised and nulled every enrichment field),
FL-wind string parsing, the FORMAT 1 center-drop E-line naming trap, eye
fields, the sonde level-profile array, and the allow_nan=False JSON guarantee
every published value must satisfy."""
import datetime as dt
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reconobs import missions as M  # noqa: E402

# A real live-format URNT12 VDM, verbatim (trimmed seq line) from the NHC
# recon archive: 2026/REPNT2/REPNT2-KWBC.202607211257.txt (TS Bertha,
# NOAA3 0602A, fix 21/12:17:56Z). No eye reported (F/G NA); the E. line is
# the center DROPSONDE surface wind, J/N are 'deg ... kt' FL-wind strings.
VDM_BERTHA = """000
URNT12 KWBC 211257
VORTEX DATA MESSAGE  AL022026
A. 21/12:17:56Z
B. 28.89 deg N 086.13 deg W
C. NA
D. 998 mb
E. 110 deg 10 kt
F. NA
G. NA
H. NA
I. NA
J. 058 deg 34 kt
K. 324 deg 53 nm 12:04:44Z
L. NA
M. NA
N. 214 deg 54 kt
O. 140 deg 17 nm 12:22:17Z
P. 14 C / 2448 m
Q. 19 C / 2449 m
R. 11 C / NA
S. 1345 / NA
T. 0.01 / 2.5 nm
U. NOAA3 0602A BERTHA OB 17
MAX FL WIND 54 KT 140 / 17 NM 12:22:17Z
$$
"""

# Same URNT12 shape with a reported eye + populated sfc winds (H. is a bare
# float kt, L. a '96 kt' string - both real variants), circular 15 nmi eye.
VDM_EYE = """000
URNT12 KNHC 092151
VORTEX DATA MESSAGE  AL142024
A. 09/21:14:00Z
B. 27.16 deg N 083.34 deg W
C. 700 mb 2472 m
D. 936 mb
E. 240 deg 8 kt
F. CLOSED WALL
G. C15
H. 105 kt
I. 268 deg 10 nm 21:08:30Z
J. 296 deg 118 kt
K. 271 deg 12 nm 21:07:00Z
L. 96 kt
M. 92 deg 11 nm 21:26:30Z
N. 101 deg 111 kt
O. 91 deg 10 nm 21:27:00Z
P. 12 C / 3052 m
Q. 22 C / 3050 m
R. 20 C / NA
S. 12345 / 7
T. 0.02 / 1 nm
U. AF309 1414A MILTON OB 20
$$
"""

# A real UZNT13 TEMP DROP, verbatim, from the NHC recon archive:
# 2026/REPNT3/REPNT3-KWBC.202607211330.txt (TS Bertha, NOAA3 0602A, released
# 21/1319Z at 29.39N 86.07W, splash 29.42N 86.10W 1322Z). XXAA + XXBB + 21212
# merge to a ~14-level profile; '168//' is a missing-dewpoint group.
SONDE = """000
UZNT13 KWBC 211330
XXAA  71138 99294 70861 08196 99001 26617 13049 00011 26417 13049
92697 22404 14058 85434 20227 13048 88999 77999
31313 09608 81319
61616 NOAA3 0602A BERTHA       OB 20
62626 MBL WND 14058 AEV 40101 DLM WND 13552 001762 WL150 12551 08
1 REL 2939N08607W 131947 SPG 2942N08610W 132241 =
XXBB  71138 99294 70861 08196 00001 26617 11956 23203 22850 20227
33832 19209 44758 16836 55757 168//
21212 00001 13049 11984 13053 22977 14061 33952 14564 44912 14054
55885 13555 66850 13048 77762 13049
31313 09608 81319
61616 NOAA3 0602A BERTHA       OB 20
62626 MBL WND 14058 AEV 40101 DLM WND 13552 001762 WL150 12551 08
1 REL 2939N08607W 131947 SPG 2942N08610W 132241 =
;
"""


def _mission(mid="AF303-0602A-BERTHA"):
    """Minimal in-window Bertha mission for the attach paths."""
    track = [{"t": f"2026-07-21T{h:02d}:00:00Z"} for h in (12, 13, 14)]
    return {mid: {"mission_id": mid, "aircraft": "NOAA3", "flight": "0602A",
                  "storm_name": "BERTHA", "atcf": None, "track": track,
                  "valid_start": track[0]["t"], "valid_end": track[-1]["t"],
                  "vdm_centers": [], "sondes": []}}


class VdmRefDatetime(unittest.TestCase):
    """The live bug: decode_vdm(_norm(content), None) raised on date.year for
    EVERY bulletin (swallowed), so enrichment was null on every record."""

    def test_ref_year_from_atcf_id(self):
        self.assertEqual(M._vdm_ref(VDM_BERTHA).year, 2026)
        self.assertEqual(M._vdm_ref(VDM_EYE).year, 2024)

    def test_ref_day_time_from_a_line(self):
        r = M._vdm_ref(VDM_BERTHA)
        self.assertEqual((r.day, r.hour, r.minute, r.second), (21, 12, 17, 56))

    def test_ref_year_falls_back_to_mission_window(self):
        no_id = VDM_BERTHA.replace("VORTEX DATA MESSAGE  AL022026\n", "")
        self.assertEqual(M._vdm_ref(no_id, ref_year=2005).year, 2005)

    def test_decode_now_succeeds_and_populates(self):
        rec = M._parse_vdm(VDM_BERTHA)
        self.assertIsNotNone(rec)
        # enrichment populated at all == the decoder ran without raising
        self.assertEqual(rec["max_fl_wind_in_kt"], 34.0)
        self.assertEqual(rec["temp_in_eye_c"], 19.0)
        # v1 keys unchanged
        self.assertEqual(rec["atcf"], "al022026")
        self.assertEqual(rec["mslp_hpa"], 998.0)
        self.assertAlmostEqual(rec["lat"], 28.89)
        self.assertAlmostEqual(rec["lon"], -86.13)


class VdmFlWindParsing(unittest.TestCase):
    def test_deg_kt_strings(self):
        rec = M._parse_vdm(VDM_BERTHA)
        self.assertEqual(rec["max_fl_wind_in_kt"], 34.0)
        self.assertEqual(rec["max_fl_wind_in_dir_deg"], 58.0)
        self.assertEqual(rec["max_fl_wind_out_kt"], 54.0)
        self.assertEqual(rec["max_fl_wind_out_dir_deg"], 214.0)
        self.assertEqual(rec["max_fl_wind_in_loc"], "324 deg 53 nm 12:04:44z")
        self.assertEqual(rec["max_fl_wind_out_loc"], "140 deg 17 nm 12:22:17z")

    def test_dir_spd_variants(self):
        self.assertEqual(M._dir_spd("058 deg 34 kt"), (58.0, 34.0))
        self.assertEqual(M._dir_spd("96 kt"), (None, 96.0))
        self.assertEqual(M._dir_spd(118.0), (None, 118.0))
        self.assertEqual(M._dir_spd(float("nan")), (None, None))
        self.assertEqual(M._dir_spd(None), (None, None))
        self.assertEqual(M._dir_spd("na"), (None, None))
        # range clamp: a wrong-FORMAT mb value must not publish as kt
        self.assertEqual(M._dir_spd(961.0), (None, None))

    def test_sfc_winds_na_stay_none(self):
        rec = M._parse_vdm(VDM_BERTHA)                # H./L. are NA
        self.assertIsNone(rec["max_sfc_wind_in_kt"])
        self.assertIsNone(rec["max_sfc_wind_out_kt"])
        self.assertIsNone(rec["max_sfc_wind_kt"])

    def test_sfc_winds_float_and_string(self):
        rec = M._parse_vdm(VDM_EYE)                   # H. 105 kt, L. '96 kt'
        self.assertEqual(rec["max_sfc_wind_in_kt"], 105.0)
        self.assertEqual(rec["max_sfc_wind_out_kt"], 96.0)
        self.assertEqual(rec["max_sfc_wind_kt"], 105.0)   # back-compat = max
        self.assertEqual(rec["max_sfc_wind_in_loc"], "268 deg 10 nm 21:08:30z")


class VdmCenterDrop(unittest.TestCase):
    def test_format1_e_line_naming_trap(self):
        # the modern E. line is the CENTER DROPSONDE surface wind, but the
        # vendored decoder files it under 'Location of Estimated Maximum
        # Surface Wind Inbound' - it must land in the center-drop fields.
        rec = M._parse_vdm(VDM_BERTHA)
        self.assertEqual(rec["center_drop_sfc_wind_kt"], 10.0)
        self.assertEqual(rec["center_drop_sfc_wind_dir_deg"], 110.0)

    def test_format2_named_keys(self):
        out = M._vdm_enrich({
            "Dropsonde Surface Wind Speed at Center (kt)": 25.0,
            "Dropsonde Surface Wind Direction at Center (deg)": 90.0,
            "Maximum Flight Level Wind Inbound": "85 kt",
            "Location of the Maximum Flight Level Wind Inbound": "12 nm se",
            "Estimated Maximum Surface Wind Inbound (kt)": 70.0,
            "Maximum Flight Level Temp Outside Eye (C)": 12.0})
        self.assertEqual(out["center_drop_sfc_wind_kt"], 25.0)
        self.assertEqual(out["center_drop_sfc_wind_dir_deg"], 90.0)
        self.assertEqual(out["max_fl_wind_in_kt"], 85.0)
        self.assertEqual(out["max_fl_wind_in_loc"], "12 nm se")
        self.assertEqual(out["max_sfc_wind_kt"], 70.0)
        self.assertEqual(out["temp_out_eye_c"], 12.0)
        # a bare FORMAT>=2 temp float must NOT leak into the altitude field
        self.assertIsNone(out["press_alt_m"])


class VdmEyeAndThermo(unittest.TestCase):
    def test_eye_fields(self):
        rec = M._parse_vdm(VDM_EYE)
        self.assertEqual(rec["eye_character"], "closed wall")
        self.assertEqual(rec["eye_shape"], "circular")
        self.assertEqual(rec["eye_diameter_nmi"], 15.0)
        self.assertIsNone(rec["eye_diameter2_nmi"])
        self.assertIsNone(rec["eye_major_nmi"])

    def test_no_eye_stays_none(self):
        rec = M._parse_vdm(VDM_BERTHA)                # F./G. are NA
        self.assertIsNone(rec["eye_character"])
        self.assertIsNone(rec["eye_shape"])
        self.assertIsNone(rec["eye_diameter_nmi"])

    def test_elliptical_mapping(self):
        out = M._vdm_enrich({"Eye Shape": "elliptical", "Orientation": 90.0,
                             "Eye Major Axis (nmi)": 20.0,
                             "Eye Minor Axis (nmi)": 12.0})
        self.assertEqual(out["eye_shape"], "elliptical")
        self.assertEqual(out["eye_orientation_deg"], 90.0)
        self.assertEqual(out["eye_major_nmi"], 20.0)
        self.assertEqual(out["eye_minor_nmi"], 12.0)

    def test_thermo_and_fix(self):
        rec = M._parse_vdm(VDM_EYE)
        self.assertEqual(rec["temp_out_eye_c"], 12.0)
        self.assertEqual(rec["temp_in_eye_c"], 22.0)
        self.assertEqual(rec["dewpoint_in_eye_c"], 20.0)
        self.assertEqual(rec["press_alt_m"], 3052.0)
        self.assertEqual(rec["std_level_hpa"], 700.0)
        self.assertEqual(rec["min_height_m"], 2472.0)
        self.assertEqual(rec["fix_note"], "12345 / 7; 0.02 / 1 nm")

    def test_enrich_of_empty_decode_is_all_none(self):
        out = M._vdm_enrich({})
        self.assertTrue(all(v is None for v in out.values()))
        self.assertIn("max_fl_wind_in_kt", out)       # keys always present
        self.assertIn("remarks", out)


class SondeLevels(unittest.TestCase):
    def _sonde(self):
        mis = _mission()
        M.add_sondes(mis, [SONDE])
        m = next(iter(mis.values()))
        self.assertEqual(len(m["sondes"]), 1)
        return m["sondes"][0]

    def test_profile_shape_order_and_nones(self):
        s = self._sonde()
        lv = s["levels"]
        self.assertGreaterEqual(len(lv), 8)
        for row in lv:
            self.assertEqual(len(row), 6)
            self.assertIsInstance(row[0], float)      # pres always present
            for x in row:
                self.assertTrue(x is None or isinstance(x, float))
        pres = [r[0] for r in lv]
        self.assertEqual(pres, sorted(pres, reverse=True))  # surface->top
        # '168//' XXBB group: temp present, dewpoint None on that row
        r757 = next(r for r in lv if r[0] == 757.0)
        self.assertEqual(r757[2], 16.8)
        self.assertIsNone(r757[3])

    def test_scalars_splash_and_layers(self):
        s = self._sonde()
        self.assertEqual(s["t"], "2026-07-21T13:19:00Z")
        self.assertAlmostEqual(s["lat"], 29.39)
        self.assertAlmostEqual(s["lon"], -86.07)
        self.assertEqual(s["slp_hpa"], 1001.0)
        self.assertEqual(s["top_hpa"], 757.0)
        self.assertEqual(s["splash"], {"lat": 29.42, "lon": -86.1,
                                       "t": "2026-07-21T13:22:00Z"})
        self.assertEqual(s["sfc_wind_kt"], 51.0)      # WL150 mean (v1 key)
        self.assertEqual(s["wl150_dir_deg"], 125.0)
        self.assertEqual(s["mbl_dir_deg"], 140.0)
        self.assertEqual(s["mbl_spd_kt"], 58.0)
        self.assertEqual(s["dlm_dir_deg"], 135.0)
        self.assertEqual(s["dlm_spd_kt"], 52.0)
        self.assertEqual(s["obsnum"], 20)
        self.assertIsNone(s["location"])              # no 62626 location token

    def test_levels_cap(self):
        class Fake:
            def iterrows(self):
                for i in range(300):
                    yield i, {"pres": 1000.0 - i, "hgt": None, "temp": None,
                              "dwpt": None, "wdir": None, "wspd": None}
        rows = M._sonde_levels({"levels": Fake()})
        self.assertEqual(len(rows), 200)
        self.assertEqual(rows[0][0], 1000.0)          # cap keeps the surface end


class JsonSafety(unittest.TestCase):
    """build._write uses json.dump(..., allow_nan=False): every enriched value
    must already be NaN-free (the missions._clean guarantee)."""

    def test_vdm_record_round_trips(self):
        mis = _mission()
        M.add_vdm(mis, [VDM_BERTHA])
        m = next(iter(mis.values()))
        self.assertEqual(len(m["vdm_centers"]), 1)
        c = m["vdm_centers"][0]
        self.assertEqual(c["atcf"], "al022026")
        self.assertEqual(m["atcf"], "al022026")       # atcf seeded on attach
        self.assertEqual(c["t"], "2026-07-21T12:17:56Z")
        body = json.dumps(c, allow_nan=False)
        self.assertEqual(json.loads(body)["max_fl_wind_in_kt"], 34.0)

    def test_sonde_record_round_trips(self):
        mis = _mission()
        M.add_sondes(mis, [SONDE])
        s = next(iter(mis.values()))["sondes"][0]
        body = json.dumps(s, allow_nan=False)         # raises on any NaN
        back = json.loads(body)
        self.assertEqual(back["top_hpa"], 757.0)
        self.assertIsNone(next(r for r in back["levels"]
                               if r[0] == 757.0)[3])  # None survived as null

    def test_decode_failure_record_still_serializes(self):
        # regex fields survive, every enrichment key present but None
        broken = VDM_BERTHA.replace("U. NOAA3 0602A BERTHA OB 17\n", "")
        rec = M._parse_vdm(broken)                    # no U. line -> decode raises
        self.assertIsNotNone(rec)
        self.assertEqual(rec["mslp_hpa"], 998.0)
        self.assertIsNone(rec["max_fl_wind_in_kt"])
        json.dumps(rec, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
