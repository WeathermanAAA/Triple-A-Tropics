#!/usr/bin/env python3
"""The SHIPS bulletin parser (``guidance.ships``).

Weighted toward the traps that silently produce plausible-but-wrong numbers:
fixed-width labels that touch their first data cell, row-specific sentinels,
an RI table whose ordering and label set both drift, and the rounding residual
that means the printed components do NOT sum to the printed total.

Run: ``python -m unittest discover tests``
"""
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from guidance.ships import (CON_TAUS, TAUS, intensity_traces,  # noqa: E402
                            parse_ships)

# A minimal but STRUCTURALLY REAL bulletin: exact column grid, the two 22-char
# contribution labels, sentinels in the rows that actually carry them.
SAMPLE = """\
                                 *                  GFS version                   *
                                 * EAST PACIFIC 2026 SHIPS INTENSITY FORECAST     *
                                 * IR SAT DATA AVAILABLE,       OHC AVAILABLE     *
                                 *  GENEVIEVE   EP072026  07/28/26  18 UTC        *

TIME (HR)          0     6    12    18    24    36    48    60    72    84    96   108   120   132   144   156   168
V (KT) NO LAND   105    98    93    90    85    78    75    70    65    58    52    47    41    39    36    32    28
V (KT) LAND      105    98    93    90    85    78    75    70    65    58    52    47    41    39    36    32    28
V (KT) LGEM      105    98    92    86    80    69    60    52    46    41    36    31    27    23    21    19    17
Storm Type      TROP  TROP  TROP  TROP  TROP  TROP  TROP  TROP  TROP  TROP  TROP  TROP  TROP  TROP  TROP  TROP  TROP

SHEAR (KT)        11    12    16    15    11     7     2     6     6     8    14    18    18    20    22    28    31
SST (C)         28.5  28.2  27.9  27.4  27.1  26.4  25.1  25.1  24.9  25.6  24.2  23.4  23.4  23.1  23.3  23.3  23.4
MODEL VTX (KT)    29    30  LOST    33    31    31    31    27    25    22    21    19    16    15    15    14    13
LAT (DEG N)     17.9  18.2  18.4  18.7  19.0  19.7  20.4  21.1  21.8  22.5  23.4  24.3  25.2  xx.x  xx.x  xx.x  xx.x
LONG(DEG W)    116.5 117.4 118.2 119.1 119.9 121.8 124.2 126.6 129.1 131.7 134.5 137.4 140.2 xxx.x xxx.x xxx.x xxx.x
LAND (KM)        875   919   952   987  1029  1120  1245  1390  1571  1722  1832  1869  1626  1469  1377  1305  1237

  FORECAST TRACK FROM OFCI      INITIAL HEADING/SPEED (DEG/KT):290/  8      CX,CY:  -7/  3
  T-12 MAX WIND: 120            PRESSURE OF STEERING LEVEL (MB):  603  (MEAN=586)
  GOES IR BRIGHTNESS TEMP. STD DEV.  50-200 KM RAD:   9.8 (MEAN=14.5)
  % GOES IR PIXELS WITH T < -20 C    50-200 KM RAD:  99.0 (MEAN=65.0)
  PRELIM RI PROB (DV .GE. 35 KT IN 36 HR):            0.1

                        INDIVIDUAL CONTRIBUTIONS TO INTENSITY CHANGE
                         6    12    18    24    36    48    60    72    84    96   108   120   132   144   156   168
                        --------------------------------------------------------------------------------------------
  SAMPLE MEAN CHANGE     0.    1.    1.    1.    2.    2.    2.    1.    1.    0.   -0.   -1.   -2.   -2.   -3.   -3.
  850 MB ENV VORTICITY  -1.   -2.   -3.   -4.   -5.   -6.   -7.   -8.   -9.  -10.  -11.  -12.  -13.  -14.  -15.  -16.
  DAYS FROM CLIM. PEAK  -2.   -0.   -0.   -0.   -0.   -0.   -0.   -0.   -0.   -0.   -0.   -0.   -0.   -1.   -1.   -1.
                        --------------------------------------------------------------------------------------------
  TOTAL CHANGE          -4.   -1.   -2.   -3.   -3.   -4.   -5.   -7.   -8.  -10.  -12.  -13.  -15.  -17.  -19.  -20.

                CURRENT MAX WIND (KT):  105. LAT, LON:   17.9   116.5

       **2026 E. Pacific RI INDEX EP072026 GENEVIEVE  07/28/26  18 UTC **
 (SHIPS-RII PREDICTOR TABLE for 30 KT OR MORE MAXIMUM WIND INCREASE IN NEXT 24-h)

     Predictor                  Value   RI Predictor Range  Scaled Value(0-1) % Contribution
 POT = MPI-VMAX (KT)         :   36.9       30.0  to    148.5        0.06             0.4
 12 HR PERSISTENCE (KT)      :  -15.0      -22.0  to     44.0        0.11             0.6
 OCEAN HEAT CONTENT(KJ/CM2)  :   10.0        0.0  to    107.8        0.09             0.3

 SHIPS Prob RI for 20kt/ 12hr RI threshold=    3% is    0.5 times climatological mean ( 6.3%)
 SHIPS Prob RI for 25kt/ 24hr RI threshold=    8% is    0.7 times climatological mean (12.5%)

Matrix of RI probabilities
------------------------------------------------------------------------------
  RI (kt / h)  | 20/12 | 25/24 | 30/24 | 35/24 | 40/24 | 45/36 | 55/48  |65/72
------------------------------------------------------------------------------
   SHIPS-RII:     3.3%    8.3%    7.8%    3.1%    0.0%    0.0%    0.0%    0.0%
    Logistic:     0.1%    0.1%    0.0%    0.0%    0.0%    0.0%    0.0%    0.0%

   ##         ANNULAR HURRICANE INDEX (AHI) EP072026 GENEVIEVE  07/28/26  18 UTC         ##
   ## AHI=  1   (AHI OF 100 IS BEST FIT TO ANN. STRUC., 1 IS MARGINAL, 0 IS NOT ANNULAR) ##
"""


class TestHeader(unittest.TestCase):
    def setUp(self):
        self.d = parse_ships(SAMPLE)

    def test_identity(self):
        h = self.d["header"]
        self.assertEqual(h["name"], "GENEVIEVE")
        self.assertEqual(h["atcf"], "EP072026")
        self.assertEqual(h["hour"], 18)

    def test_banner_year_is_the_COEFFICIENT_year(self):
        """Not the storm year. Files in Mar-Apr 2026 print 2025 while carrying
        2026 ids - reading it as the storm year mislabels them."""
        self.assertEqual(self.d["header"]["coefficient_year"], 2026)


class TestFixedWidth(unittest.TestCase):
    def setUp(self):
        self.d = parse_ships(SAMPLE)

    def test_env_grid(self):
        self.assertEqual(len(self.d["env"]["SHEAR (KT)"]), len(TAUS))
        self.assertEqual(self.d["env"]["SHEAR (KT)"][:5], [11, 12, 16, 15, 11])
        self.assertEqual(self.d["env"]["SST (C)"][0], 28.5)

    def test_22_char_labels_do_not_swallow_the_first_value(self):
        """'850 MB ENV VORTICITY' and 'DAYS FROM CLIM. PEAK' are exactly 22
        chars and touch the first cell. A whitespace split merges them into
        'VORTICITY-1.'; the grid must be clamped to start at column 22."""
        by = {c["label"]: c["values"] for c in self.d["contributions"]}
        self.assertIn("850 MB ENV VORTICITY", by)
        self.assertIn("DAYS FROM CLIM. PEAK", by)
        self.assertEqual(by["850 MB ENV VORTICITY"][0], -1.0)
        self.assertEqual(by["DAYS FROM CLIM. PEAK"][0], -2.0)

    def test_env_block_survives_its_internal_blank_line(self):
        """Section A has a blank line between the intensity rows and the
        environment rows; breaking on it loses every environmental series."""
        self.assertIn("V (KT) LGEM", self.d["env"])
        self.assertIn("LAND (KM)", self.d["env"])

    def test_contribution_axis_excludes_hour_zero(self):
        self.assertEqual(self.d["contribution_taus"], list(CON_TAUS))
        self.assertEqual(self.d["contribution_taus"][0], 6)


class TestSentinels(unittest.TestCase):
    def setUp(self):
        self.d = parse_ships(SAMPLE)

    def test_lost_on_model_vtx(self):
        self.assertIsNone(self.d["env"]["MODEL VTX (KT)"][2])
        self.assertEqual(self.d["env"]["MODEL VTX (KT)"][0], 29)

    def test_position_sentinels(self):
        """xx.x / xxx.x mean the track ran out, not a position near zero."""
        self.assertIsNone(self.d["env"]["LAT (DEG N)"][-1])
        self.assertIsNone(self.d["env"]["LONG(DEG W)"][-1])
        self.assertEqual(self.d["env"]["LAT (DEG N)"][0], 17.9)

    def test_land_km_keeps_its_sign(self):
        """LAND (KM) goes NEGATIVE when the centre is inland; treating it as an
        unsigned distance inverts the land-interaction signal."""
        d = parse_ships(SAMPLE.replace("LAND (KM)        875",
                                       "LAND (KM)        -11"))
        self.assertEqual(d["env"]["LAND (KM)"][0], -11)


class TestScalars(unittest.TestCase):
    def setUp(self):
        self.d = parse_ships(SAMPLE)["scalars"]

    def test_glued_separators_parse(self):
        """'(DEG/KT):290/  8' has no space after the colon; CX,CY spacing is
        sign-dependent. Regex only - fixed offsets mis-slice these."""
        self.assertEqual(self.d["initial_heading_deg"], 290)
        self.assertEqual(self.d["initial_speed_kt"], 8)
        self.assertEqual(self.d["cx"], -7)
        self.assertEqual(self.d["cy"], 3)

    def test_track_source_is_a_string_not_a_number(self):
        self.assertEqual(self.d["forecast_track_from"], "OFCI")

    def test_current_max_wind_is_the_t0_authority(self):
        """V (KT) LAND[0] is not always the initial intensity - one file prints
        0 there while CURRENT MAX WIND says 15."""
        self.assertEqual(self.d["current_max_wind_kt"], 105.0)

    def test_longitude_stays_in_the_source_convention(self):
        """LONG(DEG W) is POSITIVE-WEST and exceeds 180 past the dateline
        (CP92 reached 194.7). Negating it here would plot CPac in the
        Atlantic, so the field is published as-is and named for it."""
        self.assertEqual(self.d["current_lon_degw"], 116.5)


class TestRoundingResidual(unittest.TestCase):
    """The premise handed to this build - that TOTAL CHANGE equals the sum of
    its components exactly - is FALSE. Measured 43.5% exact over the 2026
    season with residuals to 4 kt, because both sides are rounded to whole
    knots. The residual is published so the waterfall can close honestly."""

    def setUp(self):
        self.d = parse_ships(SAMPLE)

    def test_components_do_not_sum_to_the_total(self):
        k = 0
        s = sum(c["values"][k] for c in self.d["contributions"])
        self.assertEqual(s, -3.0)
        self.assertEqual(self.d["total_change"][k], -4.0)
        self.assertNotEqual(s, self.d["total_change"][k])

    def test_residual_closes_the_gap_exactly(self):
        for k in range(len(CON_TAUS)):
            s = sum(c["values"][k] for c in self.d["contributions"])
            self.assertAlmostEqual(
                s + self.d["rounding_residual"][k],
                self.d["total_change"][k], places=6, msg=f"col {k}")

    def test_residual_is_present_for_every_column(self):
        self.assertEqual(len(self.d["rounding_residual"]), len(CON_TAUS))


class TestRISections(unittest.TestCase):
    def setUp(self):
        self.d = parse_ships(SAMPLE)

    def test_predictors_keyed_by_label_not_position(self):
        """Ordering differs by basin AND coefficient year (2 orderings in the
        2026 season alone), so a positional read attributes one basin's
        persistence value to another's POT slot."""
        keys = [k.strip() for k in self.d["ri_predictors"]]
        self.assertIn("POT = MPI-VMAX (KT)", keys)
        self.assertIn("12 HR PERSISTENCE (KT)", keys)
        row = [v for k, v in self.d["ri_predictors"].items()
               if k.strip() == "POT = MPI-VMAX (KT)"][0]
        self.assertEqual(row["value"], 36.9)
        self.assertEqual(row["range_lo"], 30.0)
        self.assertEqual(row["range_hi"], 148.5)
        self.assertEqual(row["scaled"], 0.06)

    def test_inverted_ranges_are_preserved(self):
        """Range endpoints are frequently hi<lo; normalising them would flip
        the meaning of the scaled value."""
        d = parse_ships(SAMPLE.replace(
            " POT = MPI-VMAX (KT)         :   36.9       30.0  to    148.5        0.06             0.4",
            " POT = MPI-VMAX (KT)         :   36.9      148.5  to     30.0        0.06             0.4"))
        row = [v for k, v in d["ri_predictors"].items()
               if k.strip() == "POT = MPI-VMAX (KT)"][0]
        self.assertEqual(row["range_lo"], 148.5)
        self.assertEqual(row["range_hi"], 30.0)

    def test_probabilities_carry_their_climatology(self):
        p = self.d["ri_probabilities"]
        self.assertEqual(len(p), 2)
        self.assertEqual(p[0], {"dv_kt": 20, "hours": 12, "prob_pct": 3,
                                "times_climo": 0.5, "climo_pct": 6.3})

    def test_matrix_row_set_is_not_assumed(self):
        """DTOPS/SDCON can be absent - file line counts range 99-116."""
        m = self.d["ri_matrix"]
        self.assertEqual(len(m["columns"]), 8)
        self.assertEqual(sorted(m["rows"]), ["Logistic", "SHIPS-RII"])
        self.assertEqual(m["rows"]["SHIPS-RII"][:2], [3.3, 8.3])

    def test_annularity(self):
        self.assertEqual(self.d["annularity"]["value"], 1)
        self.assertFalse(self.d["annularity"]["error"])

    def test_truncated_annularity_is_flagged_not_crashed(self):
        d = parse_ships(SAMPLE.replace(
            "   ## AHI=  1   (AHI OF 100 IS BEST FIT TO ANN. STRUC., 1 IS MARGINAL, 0 IS NOT ANNULAR) ##",
            "   ## ERR=2, BOTH IR FILES BAD OR MISSING ##"))
        self.assertTrue(d["annularity"]["error"])
        self.assertIsNone(d["annularity"]["value"])


class TestRobustness(unittest.TestCase):

    def test_atlantic_only_blocks_are_flagged(self):
        self.assertFalse(parse_ships(SAMPLE)["has_seef"])

    def test_missing_sections_do_not_crash(self):
        head = "\n".join(SAMPLE.splitlines()[:12])
        d = parse_ships(head)
        self.assertTrue(d["env"])
        self.assertEqual(d["contributions"], [])
        self.assertIsNone(d["total_change"])
        self.assertEqual(d["ri_probabilities"], [])

    def test_empty_input(self):
        d = parse_ships("")
        self.assertEqual(d["env"], {})
        self.assertEqual(d["ri_predictors"], {})

    def test_traces_helper(self):
        t = intensity_traces(parse_ships(SAMPLE))
        self.assertEqual(t["current_max_wind"], 105.0)
        self.assertEqual(t["lgem"][:3], [105, 98, 92])
        self.assertEqual(len(t["taus"]), len(TAUS))

    def test_output_is_json_serializable(self):
        import json
        json.dumps(parse_ships(SAMPLE))


if __name__ == "__main__":
    unittest.main()
