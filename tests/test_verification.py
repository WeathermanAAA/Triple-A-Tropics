#!/usr/bin/env python3
"""The verification engine (``guidance.verify``).

Each test pins one of the rules that keeps the scoreboard honest rather than
decorative - the rules are the product, so they get the coverage.

Run: ``python -m unittest discover tests``
"""
import datetime as dt
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from guidance import atcf, verify  # noqa: E402


def _row(tech="AVNI", tau=0, dtg="2026072800", lat="200N", lon="0600W",
         vmax="60", mslp="990", rad="34", basin="EP", cy=7):
    return (f"{basin}, {cy:02d}, {dtg},   , {tech}, {tau:4d}, {lat}, {lon}, "
            f"{vmax}, {mslp}, XX,  {rad}, NEQ,    0,    0,    0,    0,")


def _mkcases(specs):
    """cases from a compact spec: (storm, init, tau, {tech: track_nm})."""
    out = []
    for storm, init, tau, errs in specs:
        out.append({"storm": storm, "init": init, "tau": tau,
                    "errs": {t: {"track_nm": v, "int_err_kt": v / 10,
                                 "int_bias_kt": v / 20}
                             for t, v in errs.items()}})
    return out


class TestCases(unittest.TestCase):

    def _decks(self, a_lines, b_lines):
        a_rows, _ = atcf.parse_deck("\n".join(a_lines))
        b_rows, _ = atcf.parse_deck("\n".join(b_lines))
        return a_rows, verify.truth_from_bdeck(b_rows)

    def test_forecast_verifies_against_the_exact_synoptic_fix(self):
        a, truth = self._decks(
            [_row(tech="AVNI", tau=24, dtg="2026072800",
                  lat="210N", lon="0610W")],
            [_row(tech="BEST", tau=0, dtg="2026072900",
                  lat="210N", lon="0610W")])
        cases = verify.cases_for_storm("EP07", a, truth)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["tau"], 24)
        self.assertAlmostEqual(cases[0]["errs"]["AVNI"]["track_nm"], 0.0)

    def test_no_truth_fix_means_no_case(self):
        """The storm ended before the valid time: the forecast is unverifiable,
        not wrong. No case is manufactured."""
        a, truth = self._decks(
            [_row(tech="AVNI", tau=48, dtg="2026072800")],
            [_row(tech="BEST", tau=0, dtg="2026072800")])   # only t0 exists
        self.assertEqual(verify.cases_for_storm("EP07", a, truth), [])

    def test_sentinel_position_yields_no_track_error(self):
        """0N/0W resolves to None in the parser, so a sentinel-position
        forecast contributes intensity only - it can never score a 4,000 nm
        'error' from null island."""
        a, truth = self._decks(
            [_row(tech="SHIP", tau=24, dtg="2026072800", lat="0N", lon="0W",
                  vmax="55")],
            [_row(tech="BEST", tau=0, dtg="2026072900", vmax="60")])
        cases = verify.cases_for_storm("EP07", a, truth)
        self.assertEqual(len(cases), 1)
        e = cases[0]["errs"]["SHIP"]
        self.assertNotIn("track_nm", e)
        self.assertEqual(e["int_err_kt"], 5)

    def test_radii_rows_do_not_duplicate_a_case(self):
        """The 50/64 kt rows share (DTG, TECH, TAU) with the primary row -
        the RAD-in-key rule upstream means they arrive as separate rows, and
        the case builder must take the primary once, not average three."""
        a, truth = self._decks(
            [_row(tech="AVNI", tau=24, rad="34"),
             _row(tech="AVNI", tau=24, rad="50"),
             _row(tech="AVNI", tau=24, rad="64")],
            [_row(tech="BEST", tau=0, dtg="2026072900")])
        cases = verify.cases_for_storm("EP07", a, truth)
        self.assertEqual(len(cases), 1)

    def test_intensity_bias_keeps_its_sign(self):
        a, truth = self._decks(
            [_row(tech="AVNI", tau=24, vmax="70")],
            [_row(tech="BEST", tau=0, dtg="2026072900", vmax="60")])
        e = verify.cases_for_storm("EP07", a, truth)[0]["errs"]["AVNI"]
        self.assertEqual(e["int_err_kt"], 10)
        self.assertEqual(e["int_bias_kt"], 10)


class TestHomogeneity(unittest.TestCase):
    """The hard filter: every model, every case, or the case is out."""

    def test_only_all_model_cases_survive(self):
        cases = _mkcases([
            ("S1", "a", 24, {"OFCL": 20, "AVNI": 30, "OCD5": 60}),
            ("S1", "b", 24, {"OFCL": 25, "OCD5": 70}),          # AVNI missing
            ("S2", "c", 24, {"OFCL": 22, "AVNI": 28, "OCD5": 65}),
        ])
        kept, hom, dropped = verify.homogeneous_panel(
            cases, ["OCD5", "OFCL", "AVNI"], 24, "track", min_cases=2)
        self.assertEqual(len(hom), 2)
        self.assertEqual(kept, ["OCD5", "OFCL", "AVNI"])
        self.assertEqual(dropped, [])

    def test_low_coverage_model_is_dropped_not_the_filter(self):
        """A model that skips cases would otherwise look better than one that
        attempts them; when it starves the sample, IT goes, and the removal is
        recorded for the page."""
        cases = _mkcases(
            [("S1", f"i{i}", 24, {"OFCL": 20 + i, "OCD5": 60 + i})
             for i in range(6)] +
            [("S1", "j", 24, {"OFCL": 30, "OCD5": 70, "RARE": 25})])
        kept, hom, dropped = verify.homogeneous_panel(
            cases, ["OCD5", "OFCL", "RARE"], 24, "track", min_cases=5)
        self.assertNotIn("RARE", kept)
        self.assertEqual(len(hom), 7)
        self.assertEqual(dropped[0]["tech"], "RARE")

    def test_the_baseline_is_never_dropped(self):
        """OCD5 is the no-skill reference; a board without it cannot say
        whether anything adds value, so coverage pressure may never evict it."""
        cases = _mkcases(
            [("S1", f"i{i}", 24, {"OFCL": 20, "AVNI": 25}) for i in range(9)] +
            [("S1", "j", 24, {"OFCL": 20, "AVNI": 25, "OCD5": 60})])
        kept, hom, dropped = verify.homogeneous_panel(
            cases, ["OCD5", "OFCL", "AVNI"], 24, "track", min_cases=8)
        self.assertIn("OCD5", kept)
        self.assertNotIn("OCD5", [d["tech"] for d in dropped])

    def test_short_sample_is_omitted_not_padded(self):
        cases = _mkcases([("S1", "a", 120, {"OFCL": 50, "OCD5": 200})])
        kept, hom, _ = verify.homogeneous_panel(
            cases, ["OCD5", "OFCL"], 120, "track", min_cases=8)
        self.assertLess(len(hom), 8)


class TestBootstrap(unittest.TestCase):

    def test_single_storm_has_no_interval(self):
        """A block bootstrap over one block is a fiction; the honest output is
        None, and the page prints 'single storm' instead of a fake CI."""
        self.assertIsNone(verify.storm_block_bootstrap({"S1": [1, 2, 3]}))

    def test_deterministic_with_fixed_seed(self):
        v = {"S1": [10.0, 12.0], "S2": [30.0, 28.0], "S3": [20.0]}
        self.assertEqual(verify.storm_block_bootstrap(v),
                         verify.storm_block_bootstrap(v))

    def test_blocks_are_storms_not_forecasts(self):
        """Two storms with internally-identical values: every replicate mean
        is a mix of whole-storm blocks, so the CI endpoints can only be means
        of {10-blocks, 30-blocks} - a per-forecast resample would produce
        intermediate values from splitting a storm."""
        v = {"S1": [10.0] * 8, "S2": [30.0] * 8}
        lo, hi = verify.storm_block_bootstrap(v)
        self.assertIn(lo, (10.0, 20.0, 30.0))
        self.assertIn(hi, (10.0, 20.0, 30.0))
        self.assertLessEqual(lo, hi)


class TestPanels(unittest.TestCase):

    def _cases(self):
        specs = []
        for s, storm in enumerate(("S1", "S2", "S3")):
            for i in range(4):
                errs = {"OCD5": 100 + i, "OFCL": 30 + i, "AVNI": 50 + i,
                        "AVNO": 45 + i, "NNIC": 400 + i}
                specs.append((storm, f"i{s}{i}", 24, errs))
        return _mkcases(specs)

    def test_early_and_late_are_never_pooled(self):
        cases = self._cases()
        early = verify.score_panel(cases, "ep", "early", "track")
        late = verify.score_panel(cases, "ep", "late", "track")
        e24 = early["per_tau"]["24"]["models"]
        l24 = late["per_tau"]["24"]["models"]
        self.assertIn("AVNI", e24)
        self.assertNotIn("AVNO", e24, "a late raw model leaked into early")
        self.assertIn("AVNO", l24)
        self.assertNotIn("AVNI", l24, "an early interpolated aid leaked into late")
        # The baseline anchors BOTH panels.
        self.assertIn("OCD5", e24)
        self.assertIn("OCD5", l24)

    def test_skill_is_relative_to_ocd5(self):
        p = verify.score_panel(self._cases(), "ep", "early", "track")
        m = p["per_tau"]["24"]["models"]
        base = m["OCD5"]["mean"]
        self.assertAlmostEqual(m["OFCL"]["skill_pct"],
                               round((1 - m["OFCL"]["mean"] / base) * 100, 1))
        self.assertNotIn("skill_pct", m["OCD5"])

    def test_intensity_consensus_never_scores_track(self):
        """NNIC's positions are not track forecasts - measured 628 nm at 120 h
        against TVCN's 82 in the same homogeneous sample. It stays fully
        eligible for intensity, which is what it is."""
        cases = self._cases()
        tr = verify.score_panel(cases, "ep", "early", "track")
        it = verify.score_panel(cases, "ep", "early", "intensity")
        self.assertNotIn("NNIC", tr["per_tau"]["24"].get("models", {}))
        self.assertIn("NNIC", it["per_tau"]["24"].get("models", {}))

    def test_n_and_storm_count_ride_with_every_tau(self):
        p = verify.score_panel(self._cases(), "ep", "early", "track")
        e = p["per_tau"]["24"]
        self.assertEqual(e["n"], 12)
        self.assertEqual(e["n_storms"], 3)

    def test_thin_taus_are_omitted_with_their_n(self):
        p = verify.score_panel(self._cases(), "ep", "early", "track")
        e120 = p["per_tau"]["120"]
        self.assertTrue(e120["omitted"])
        self.assertEqual(e120["n"], 0)

    def test_document_is_json_serializable(self):
        import json
        json.dumps(verify.score_panel(self._cases(), "ep", "early", "track"))


if __name__ == "__main__":
    unittest.main()
