"""
Browser-grade smoke test of models/enscenters.js: hydrate the viewer from a
hermetic manifest + cycle JSON under jsdom and assert the data wiring
(frame-by-step indexing, peak table, model selector, legend) and the transport
(step / show / scrub) behave.

Needs node + jsdom (jsdom is NOT a repo dependency - install transiently with
`npm install --no-save jsdom`); the test skips cleanly when absent. Canvas is
stubbed in the harness, so this validates logic, not pixels.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "enscenters_viewer_smoke.cjs"
TRAIL_HARNESS = Path(__file__).resolve().parent / "enscenters_trail_smoke.cjs"
HEADER_HARNESS = Path(__file__).resolve().parent / "enscenters_header_smoke.cjs"
GIFNAME_HARNESS = Path(__file__).resolve().parent / "enscenters_gifname_smoke.cjs"
GIFSIZE_HARNESS = Path(__file__).resolve().parent / "enscenters_gifsize_smoke.cjs"
TOOLKIT_HARNESS = Path(__file__).resolve().parent / "enscenters_toolkit_smoke.cjs"
OBS_HARNESS = Path(__file__).resolve().parent / "enscenters_obs_smoke.cjs"
JS = REPO / "models" / "enscenters.js"
NODE = shutil.which("node")


def jsdom_available() -> bool:
    if NODE is None:
        return False
    probe = subprocess.run([NODE, "-e", "require('jsdom')"],
                           cwd=str(REPO), capture_output=True, text=True)
    return probe.returncode == 0


def _fixture():
    bins = [
        {"key": "gt1000", "label": ">1000 hPa", "lo": 1000, "hi": None},
        {"key": "p990_1000", "label": "990 to 1000 hPa", "lo": 990, "hi": 1000},
        {"key": "p970_990", "label": "970 to 990 hPa", "lo": 970, "hi": 990},
        {"key": "p950_970", "label": "950 to 970 hPa", "lo": 950, "hi": 970},
        {"key": "p930_950", "label": "930 to 950 hPa", "lo": 930, "hi": 950},
        {"key": "p910_930", "label": "910 to 930 hPa", "lo": 910, "hi": 930},
        {"key": "lt910", "label": "<910 hPa", "lo": None, "hi": 910},
    ]
    cycle = {
        "schema_version": 1, "model": "ecens", "model_label": "ECMWF ENS",
        "init_time": "2026-06-13T00:00:00Z", "init_cycle": "2026061300",
        "cycle_hour": 0, "generated_at": "2026-06-13T08:41:00Z",
        "attribution": "ECMWF open data (CC-BY-4.0)", "grid": "0.25 deg",
        "run_steps": [0, 24, 72], "n_members": 2, "n_centers": 5,
        "detect": {"closed_threshold_hpa": 2.0},
        "center_fields": ["step_h", "lat", "lon", "mslp_hpa", "vmax_kt"],
        "pressure_bins": bins,
        "members": [
            {"id": "CTL", "label": "Control",
             "peak": {"mslp_hpa": 960.0, "vmax_kt": 83.0, "lat": 20.0, "lon": -60.0, "step_h": 24},
             "n_centers": 3,
             "centers": [[0, 20.0, -60.0, 1005.0, 11.0], [24, 20.0, -60.0, 960.0, 83.0], [72, 21.0, -61.0, 975.0, 67.0]]},
            {"id": "P01", "label": "Perturbed 01",
             "peak": {"mslp_hpa": 985.0, "vmax_kt": 55.0, "lat": -15.0, "lon": 90.0, "step_h": 72},
             "n_centers": 2,
             "centers": [[0, -15.0, 90.0, 1000.0, 17.0], [72, -15.0, 90.0, 985.0, 55.0]]},
        ],
    }
    manifest = {
        "schema_version": 1, "generated_at": "2026-06-13T08:41:00Z",
        "default_model": "ecens",
        "models": [{"slug": "ecens", "label": "ECMWF ENS",
                    "cycles": ["2026061300", "2026061218"], "latest": "2026061300"}],
    }
    return manifest, cycle


@unittest.skipIf(NODE is None, "node not on PATH")
@unittest.skipUnless(jsdom_available(), "jsdom not resolvable - npm install --no-save jsdom")
class TestEnsCentersViewer(unittest.TestCase):
    def test_hydrate_and_transport(self):
        manifest, cycle = _fixture()
        with tempfile.TemporaryDirectory() as td:
            mp = Path(td) / "manifest.json"
            cp = Path(td) / "cycle.json"
            mp.write_text(json.dumps(manifest))
            cp.write_text(json.dumps(cycle))
            proc = subprocess.run(
                [NODE, str(HARNESS), str(JS), str(mp), str(cp)],
                cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"harness failed:\n{proc.stderr}")
        s = json.loads(proc.stdout)

        # data wiring + per-member region peaks
        self.assertEqual(s["regionFramesLen"], 3)
        self.assertEqual(s["runStepsLen"], 3)
        self.assertEqual(s["nCenters"], 5)
        self.assertEqual(s["nMembers"], 2)
        self.assertTrue(s["peaksHasCTL"])     # CTL's centers are in the Atlantic default
        self.assertTrue(s["peaksSorted"])     # ascending by Pmin
        # transport
        self.assertEqual(s["idxBefore"], 0)
        self.assertEqual(s["idxAfterStep"], 1)
        self.assertEqual(s["fhourAfterShow2"], "F072")
        # trail mode
        self.assertEqual(s["trailDefault"], "trail")
        self.assertEqual(s["trailAfter"], "current")

        # region layer (shared TATRegions)
        self.assertEqual(s["defaultRegion"], "atlantic")
        self.assertEqual(s["regionLabelText"], "Atlantic")
        # the model selector is ALWAYS visible (even with a single model), so the
        # active model is always labelled; neither the bar nor the group is hidden
        self.assertFalse(s["controlbarHidden"])
        self.assertFalse(s["modelgroupHidden"])
        self.assertEqual(s["pickerCardCount"], 23)        # all registry regions
        # the DISPLAY extent is framed to the fixed 2:1 box aspect (frameExtent):
        # global expands lat to the poles; wpac (too narrow) expands lon symmetrically.
        self.assertEqual(s["globalExtent"], [0, 360, -90, 90])   # Pacific-centered, framed to poles
        self.assertEqual(s["wpacExtent"], [95, 185, 0, 45])      # lon expanded 80 -> 90 to hit 2:1
        self.assertTrue(s["allVisibleInWpac"])            # scatter filtered to region (tight bounds, unframed)
        self.assertEqual(s["lastRegionSaved"], "wpac")    # localStorage persistence

        # Run (cycle) selector: built from the manifest cycle list, newest first,
        # latest labelled and selected.
        self.assertEqual(s["runOptionCount"], 2)
        self.assertEqual(s["runValue"], "2026061300")
        self.assertEqual(s["runFirstLabel"], "Jun 13 00Z (latest)")
        self.assertEqual(s["runSecondLabel"], "Jun 12 18Z")

    def test_gif_filename_per_model_and_google_labels(self):
        # FIX 1: each model's exported GIF is named for its OWN slug (was a
        # hardcoded "ecens"). FIX 2: the selector labels read "Google FNV3 (50)"
        # / "Google GenCast". Drives the real _makeGif download line per model.
        proc = subprocess.run([NODE, str(GIFNAME_HARNESS), str(JS)],
                              cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"gifname harness failed:\n{proc.stderr}")
        s = json.loads(proc.stdout)
        # FIX 1: filename starts with the model's own slug, never "ecens_" for others
        for slug, name in s["names"].items():
            self.assertTrue(name and name.startswith(slug + "_"),
                            f"{slug} GIF named {name!r}")
        self.assertNotEqual(s["names"]["fnv3"][:6], "ecens_")
        # FIX 2: Google prefix on the two Google models; others unchanged
        self.assertEqual(s["chips"],
                         ["ECMWF ENS", "AIFS-ENS", "GEFS", "Google FNV3 (50)", "Google GenCast"])

    def test_gif_size_quality_preset(self):
        # GIF preset toggle: the preset only moves export WIDTH + (for Discord)
        # frame count - color fidelity (quality:1, no dither, in _gifRun) is fixed.
        #   * Full caps width at 1600 and never trims frames; the size readout
        #     matches the blob and the >10 MB warning fires when it's too big.
        #   * Discord caps width at 900, AUTO-TRIMS frames to land under ~9.5 MB,
        #     but never below the floor (8) - and warns if it still can't fit.
        proc = subprocess.run([NODE, str(GIFSIZE_HARNESS), str(JS)],
                              cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"gifsize harness failed:\n{proc.stderr}")
        s = json.loads(proc.stdout)
        fn, fo, dt, df = s["fullNormal"], s["fullOver"], s["discordTrim"], s["discordFloor"]
        # Full: one pass at width 1600, no auto-trim
        self.assertEqual(len(fn["attempts"]), 1)
        self.assertEqual(fn["W"], 1600)
        self.assertIn("6.6 MB", fn["status"])          # readout matches the delivered blob
        self.assertFalse(fn["warned"])
        # Full too big: still one pass, but the over-cap warning fires
        self.assertEqual(len(fo["attempts"]), 1)
        self.assertTrue(fo["warned"])
        # Discord: width 900, trims frame count (22 -> fewer) and lands under cap
        self.assertEqual(dt["W"], 900)
        self.assertGreater(len(dt["attempts"]), 1)
        self.assertLess(dt["finalN"], 22)
        self.assertFalse(dt["warned"])
        # Discord impossible: trims only down to the floor (8), then warns
        self.assertEqual(df["finalN"], 8)
        self.assertTrue(df["warned"])
        # filename still carries the active model slug + region + cycle
        for r in (fn, fo, dt, df):
            self.assertEqual(r["download"], "fnv3_wpac_2026061412.gif")
        # HOUR RANGE: the base frame set is the chosen hour window, not a raw
        # count. With 31 hours available the 0->126h window selects exactly 22.
        self.assertEqual(fn["baseN"], 22)

    def test_gif_hour_range_selection(self):
        # The GIF spans a forecast-HOUR range: 0->72h on a 0,6,…,180h run yields
        # ONLY F000..F072 (13 frames); reversed input auto-swaps; "Skip every"
        # thins within the range but still ends on the chosen end hour.
        proc = subprocess.run([NODE, str(GIFSIZE_HARNESS), str(JS)],
                              cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"gifsize harness failed:\n{proc.stderr}")
        s = json.loads(proc.stdout)
        full = list(range(0, 73, 6))                       # F000..F072
        self.assertEqual(s["range_0_72"], full)            # only the in-range hours
        self.assertEqual(s["range_swapped"], full)         # end<start auto-swaps
        self.assertEqual(s["range_skip1"], [0, 12, 24, 36, 48, 60, 72])  # thinned, ends on 72

    def test_burned_in_header_has_fhour_and_valid_per_frame(self):
        # ITEM 1 hard rule: the burned-in canvas header (what travels in a copied
        # still / every GIF frame) must carry the CURRENT forecast hour + valid
        # time, and update per frame.
        proc = subprocess.run([NODE, str(HEADER_HARNESS), str(JS)],
                              cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"header harness failed:\n{proc.stderr}")
        s = json.loads(proc.stdout)
        h1, h3 = s["header_at_idx1"], s["header_at_idx3"]
        for h in (h1, h3):
            self.assertIn("init ", h)
            self.assertIn("valid ", h)
            self.assertNotIn("—", h)            # no em-dash
        # forecast hour present + INCREMENTS per frame (idx1 -> F024, idx3 -> F120)
        self.assertIn("F024", h1)
        self.assertIn("F120", h3)
        # valid time present + advances (Mon Jun 15 -> Fri Jun 19)
        self.assertIn("Jun 15", h1)
        self.assertIn("Jun 19", h3)
        self.assertNotEqual(h1, h3)

    def test_trail_clears_on_toggle_no_stale_rings(self):
        # Stale-trail regression: accumulate the trail to a late step, toggle
        # Trail OFF, move to an earlier step (idx=2), toggle Trail ON. The trail
        # must hold ONLY steps 0..1 - no leftover rings from the later steps.
        # Pre-fix (bare `trailUpTo = -1` without clearing the bitmap) this set
        # was [0,1,2,3,4]; the fix makes it [0,1].
        proc = subprocess.run(
            [NODE, str(TRAIL_HARNESS), str(JS)],
            cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"trail harness failed:\n{proc.stderr}")
        s = json.loads(proc.stdout)
        # sanity: the trail genuinely accumulated the later steps first
        self.assertEqual(s["afterAccumSteps"], [0, 1, 2, 3, 4])
        self.assertEqual(s["idx"], 2)
        # the fix: after the off->on toggle at idx=2, ONLY steps 0..1 remain
        self.assertEqual(s["trailDrawnSteps"], [0, 1],
                         "stale trail rings beyond step 1 survived the toggle")

    def test_toolkit_lines_mean_fallback_and_persistence(self):
        # Stage 2 Ensemble Toolkit: data-style (Cheerios/Lines), ensemble-mean +
        # plume overlay, lazy + graceful tracks loading, dateline-safe mean track,
        # localStorage persistence, and the Cheerios-byte-identity guard.
        proc = subprocess.run([NODE, str(TOOLKIT_HARNESS), str(JS)],
                              cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"toolkit harness failed:\n{proc.stderr}")
        s = json.loads(proc.stdout)
        # toolkit OFF (default): ONLY Cheerios runs - no track drawer touches the
        # frame, so the centers view is byte-identical to pre-toolkit.
        self.assertEqual(s["off"], {"lines": 0, "mean": 0, "step": 1, "plume": 0})
        # a model WITH tracks shows both toggles
        self.assertTrue(s["ecens_style_visible"] and s["ecens_mean_visible"])
        # Lines mode: lazily loaded tracks, drew lines, animated, NOT Cheerios
        self.assertTrue(s["lines_tracksReady"])
        self.assertGreaterEqual(s["lines_after"]["lines"], 2)
        self.assertEqual(s["lines_after"]["step"], 0)
        self.assertEqual(s["ls_style"], "lines")
        # Mean mode: mean track + plume drawn
        self.assertGreaterEqual(s["mean_after"]["mean"], 1)
        self.assertGreaterEqual(s["mean_after"]["plume"], 1)
        self.assertEqual(s["ls_mean"], "on")
        # the S. Pacific mean track is CONTINUOUS across the dateline (no projected
        # x jump anywhere near the half-map break threshold)
        self.assertGreater(s["dateline_jumpLimit"], 0)
        self.assertLess(s["dateline_maxJump"], s["dateline_jumpLimit"] * 0.5)
        # toggles persist across a reload (a fresh viewer reads localStorage)
        self.assertEqual(s["persist_style"], "lines")
        self.assertTrue(s["persist_mean"])
        # a model with NO tracks: toggles hidden, Cheerios only, no error
        self.assertFalse(s["noend_style_visible"] or s["noend_mean_visible"])
        self.assertEqual(s["noend_after"], {"lines": 0, "mean": 0, "step": 1})
        self.assertFalse(s["noend_threw"])
        # a model whose tracks.json fails to load: toggles hidden, no error
        self.assertFalse(s["failm_style_visible"] or s["failm_mean_visible"])
        self.assertFalse(s["failm_threw"])

    def test_obs_vs_envelope_match_rank_and_fallbacks(self):
        # Stage 2b: match a live observed system to its ensemble cluster, rank it in
        # the envelope, draw the focal marker, degrade cleanly, and read ONLY the
        # sanctioned global_storms.geojson feed (floater isolation).
        proc = subprocess.run([NODE, str(OBS_HARNESS), str(JS)],
                              cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"obs harness failed:\n{proc.stderr}")
        s = json.loads(proc.stdout)
        # ISOLATION: the obs feed is the home map's global_storms.geojson, and NO
        # floater URL is ever fetched.
        self.assertTrue(s["obs_fetched_url"].endswith("global_storms.geojson"))
        self.assertFalse(s["obs_fetched_any_floater"])
        # toggle shows (model has tracks), persists, draws the marker overlay
        self.assertTrue(s["obs_btn_visible"])
        self.assertEqual(s["ls_obs"], "on")
        self.assertTrue(s["persist_obs"])
        self.assertEqual(s["markers_drawn"], 1)   # envelope ellipses retired - marker only
        # matching: invest near a cluster matches; far invest does not; active named
        # storm (track is_active) matches via its latest observation fix
        self.assertEqual(s["resolved_n"], 3)
        self.assertTrue(s["invA_matched"])
        self.assertFalse(s["invB_matched"])
        self.assertTrue(s["stmS_matched"])
        # rank is sane (0..100) with a compass side; matched the dateline cluster
        # (lon 170) -> dateline-safe match
        self.assertGreaterEqual(s["invA_rank"]["pct"], 0)
        self.assertLessEqual(s["invA_rank"]["pct"], 100)
        self.assertIn(s["invA_rank"]["side"], ["N", "NE", "E", "SE", "S", "SW", "W", "NW"])
        self.assertEqual(s["invA_rank"]["clusterGenesisLon"], 170)
        # no active system in view -> note, no markers, no error
        self.assertEqual(s["natl_resolved"], 0)
        self.assertEqual(s["natl_markers"], 0)
        self.assertEqual(s["natl_note"], 1)
        # no-tracks model -> obs toggle hidden, no error
        self.assertFalse(s["noend_obs_visible"])
        self.assertFalse(s["noend_threw"])
        # obs feed fetch fails -> empty, clean no-op, no error
        self.assertEqual(s["fail_obs_len"], 0)
        self.assertFalse(s["fail_threw"])
        # #3 regression: a named storm with BOTH an active_marker ('hurricane') AND
        # an is_active track draws ONCE (green 'storm' glyph), not a storm+invest
        # dupe; the invest still draws one red 'invest' X.
        self.assertEqual(s["dup_total"], 2)
        self.assertEqual(s["dup_arthur_count"], 1)
        self.assertEqual(s["dup_arthur_kind"], "storm")
        self.assertEqual(s["dup_invC_count"], 1)
        self.assertEqual(s["dup_invC_kind"], "invest")


POOL_HARNESS = Path(__file__).resolve().parent / "enscenters_pool_smoke.cjs"
SUITES_HARNESS = Path(__file__).resolve().parent / "enscenters_suites_smoke.cjs"


@unittest.skipIf(NODE is None, "node not on PATH")
class TestSuperEnsemblePooling(unittest.TestCase):
    """Pure pooling helpers (EnsCentersViewer.Pool): cycle resolution with the
    <= 6 h fallback, valid-time alignment, common cadence, equal weight PER MODEL
    (mixture quantiles + mixture envelope), dateline-safe pooled mean, and
    cross-model cluster matching. No DOM needed."""

    @classmethod
    def setUpClass(cls):
        proc = subprocess.run([NODE, str(POOL_HARNESS), str(JS)],
                              cwd=str(REPO), capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError(f"pool harness failed:\n{proc.stderr}")
        cls.s = json.loads(proc.stdout)

    def test_cycle_resolution_and_pooled_runs(self):
        s = self.s
        # same cycle first; else the newest run <= 6 h older; else dropped
        self.assertEqual(s["resolve12"]["used"], [["fnv3", "2026100612", 0], ["wnv3", "2026100606", 6]])
        self.assertEqual(s["resolve12"]["dropped"], ["genc"])          # 12 h old -> out
        # a pooled run needs >= 2 contributing models
        self.assertEqual(s["poolCycles"], ["2026100612", "2026100606", "2026100600"])
        # off-cadence 3-hourly steps drop where a covering 6-hourly model lacks them
        self.assertEqual(s["commonSteps"], [0, 6, 12, 18])

    def test_pooled_centers_document(self):
        p = self.s["pooled"]
        self.assertEqual(p["model"], "super-google")
        self.assertEqual(p["label"], "Google super ensemble")
        self.assertEqual(p["n_members"], 114)                           # 50 FNV3 + 64 WN3
        self.assertEqual(p["init_cycle"], "2026100612")
        self.assertEqual(p["method"], "pool-v1")
        self.assertEqual(p["poolModels"], [["FNV3", 0, 50], ["WN3", 6, 64]])
        # members keep a model tag
        self.assertEqual(p["firstIds"], ["FNV3 M00", "WN3 M00"])
        self.assertEqual(p["firstTags"], ["FNV3", "WN3"])
        # the lagged WN3 06Z run is VALID-TIME aligned: its F006 center (MSLP 999,
        # 31 kt) is the pooled F000, and its F000 (before the pooled init) is dropped
        self.assertEqual(p["wnFirstCenter"], [0, 15, -50, 999, 31])
        self.assertEqual(p["wnSteps"], [0, 6, 12, 18])
        self.assertEqual(p["run_steps"], [0, 6, 12, 18])
        # mandatory disclosure, house text rules
        cap = p["caption"]
        self.assertIn("Derived product, not a model run", cap)
        self.assertIn("equal weight per model", cap)
        self.assertIn("pool-v1", cap)
        self.assertIn("WN3 (06Z run)", cap)
        self.assertIn("not for real-world use", cap)
        self.assertNotIn("—", cap)

    def test_equal_weight_per_model_not_per_member(self):
        m = self.s["mix"]
        # 50-member model ~100 kt vs 5-member model ~50 kt: an equal-model mixture
        # puts p25 at the small model's median and p75 at the large model's
        self.assertAlmostEqual(m["p25"], 50.0, places=3)
        self.assertAlmostEqual(m["p75"], 100.0, places=3)
        self.assertAlmostEqual(m["sameP50"], 100.0, places=3)           # identical models -> unchanged

    def test_pooled_cluster_geometry(self):
        d = self.s["dateline"]
        # 170E + 170W pool to the dateline (not Greenwich); counts add up
        self.assertAlmostEqual(abs(d["lon"]), 180.0, places=1)
        self.assertGreater(d["lat"], 10.0)                              # great-circle mean bows poleward
        self.assertLess(d["lat"], 10.5)
        self.assertEqual(d["member_count"], 50)
        self.assertEqual(d["n_models"], 2)
        # equal-weight Gaussian-mixture envelope: two point-mass models 1 deg of
        # longitude apart at the equator -> east variance (111.19 km)^2, no north
        e = self.s["envelope"]
        self.assertAlmostEqual(e["cxx"], 111.195 ** 2, delta=15)
        self.assertAlmostEqual(e["cyy"], 0.0, places=6)
        self.assertAlmostEqual(e["mean_lat"], 0.0, places=6)
        self.assertAlmostEqual(e["mean_lon"], 0.0, places=6)
        self.assertEqual(e["n_models"], 2)

    def test_cross_model_matching(self):
        # nearby clusters from different models group; a far system and a second
        # cluster from an already-represented model stay separate
        self.assertEqual(self.s["groups"], [["fnv3@-50", "wnv3@-51"], ["wnv3@130"], ["fnv3@-50.5"]])

    def test_pooled_tracks(self):
        t = self.s["tracks"]
        self.assertEqual(t["n_members"], 114)
        self.assertEqual(t["n_clusters"], 1)
        self.assertEqual(t["n_models"], 2)
        self.assertEqual(t["member_count"], 114)
        self.assertEqual(t["method"], "pool-v1")
        self.assertEqual(t["wnFirstStep"], 0)
        self.assertEqual(t["wnId"], "WN3 M0")
        self.assertEqual(t["models"], ["fnv3", "wnv3"])


@unittest.skipIf(NODE is None, "node not on PATH")
@unittest.skipUnless(jsdom_available(), "jsdom not resolvable")
class TestSuiteSwitcher(unittest.TestCase):
    """Suite row + per-suite model row (data-driven, fallback for older
    manifests), a registry model with no manifest entry, and the two derived
    super ensembles end-to-end in the viewer."""

    @classmethod
    def setUpClass(cls):
        proc = subprocess.run([NODE, str(SUITES_HARNESS), str(JS)],
                              cwd=str(REPO), capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError(f"suites harness failed:\n{proc.stderr}")
        cls.s = json.loads(proc.stdout)

    @staticmethod
    def _labels(chips):
        return [c["label"] for c in chips]

    def test_suite_rows_and_fallback(self):
        b = self.s["boot"]
        self.assertEqual(self._labels(b["suites"]), ["ECMWF", "NOAA", "Google", "All models"])
        # boots on the freshest model (AIFS 12Z beats the 06Z ECMWF ENS) in its suite
        self.assertEqual(b["model"], "ecaie")
        self.assertEqual(b["suite"], "ecmwf")
        self.assertEqual(self._labels(b["models"]), ["ECMWF ENS", "AIFS-ENS"])
        # Google suite (fnv3/genc entries carry NO suite field -> fallback map);
        # WN3 has no manifest entry -> a disabled chip, not an error
        g = self.s["google"]
        self.assertEqual(g["suite"], "google")
        self.assertEqual(g["model"], "fnv3")                           # freshest Google model
        self.assertEqual(self._labels(g["models"]),
                         ["Google FNV3 (50)", "Google WN3 (64)", "Google GenCast", "Google super ensemble"])
        self.assertEqual([c["disabled"] for c in g["models"]], [False, True, False, False])

    def test_google_super_ensemble(self):
        sg = self.s["superGoogle"]
        self.assertEqual(sg["model"], "super-google")
        self.assertEqual(sg["label"], "Google super ensemble")
        # GenCast has no 12Z: its 06Z run (6 h older) goes in, disclosed
        self.assertEqual(sg["pool"], [["fnv3", "2026100612", 0], ["genc", "2026100606", 6]])
        self.assertEqual(sg["n_members"], 4 + 5)
        self.assertEqual(sg["method"], "pool-v1")
        self.assertEqual(sg["runOptions"], ["2026100612", "2026100606"])
        self.assertIn("Derived product", sg["caption"])
        # burned-in header: init + F-hour + valid kept, plus the derived line
        h = sg["header"]
        for part in ("Google super ensemble", "init Oct 6 12Z", "F006", "valid Tue Oct 6, 18Z",
                     "Derived: pooled", "FNV3", "GenCast", "(06Z run)", "equal weight per model", "pool-v1"):
            self.assertIn(part, h)
        self.assertNotIn("—", h)
        # pooled tracks drive the mean / plume overlay (one system, two models)
        self.assertTrue(sg["tracksReady"])
        self.assertTrue(sg["meanVisible"])
        self.assertEqual(len(sg["clusters"]), 1)
        c = sg["clusters"][0]
        self.assertEqual(c["n_models"], 2)
        self.assertEqual(c["member_count"], 9)
        self.assertEqual(sorted(c["models"]), ["fnv3", "genc"])
        self.assertEqual(sg["tracksN"], 9)
        # equal-weight median sits between the two models' medians (60 vs 90 kt)
        self.assertGreaterEqual(c["p50_0"], 60)
        self.assertLessEqual(c["p50_0"], 90)
        self.assertEqual(sg["plumeSub"], ["9 of 9 members  ·  2 models"])

    def test_all_model_super_ensemble(self):
        a = self.s["all"]
        self.assertEqual(a["model"], "super-all")
        self.assertEqual(self._labels(a["models"]), ["All-model super ensemble"])
        self.assertEqual(a["pool"], [["ecens", 6], ["ecaie", 0], ["gefs", 0], ["fnv3", 0], ["genc", 6]])
        self.assertEqual(a["n_members"], 3 + 3 + 2 + 4 + 5)
        self.assertEqual(a["steps"], [0, 6, 12, 18, 24])                # common cadence
        self.assertEqual(a["dropped"], [])

    def test_single_model_after_super_and_late_publish(self):
        n = self.s["noaa"]
        self.assertEqual(n["model"], "gefs")
        self.assertFalse(n["pool"])
        # WN3's first publish enables its chip on the next poll and joins the pool
        ap = self.s["afterPoll"]
        self.assertEqual([c["disabled"] for c in ap["models"]], [False, False, False, False])
        self.assertEqual(ap["pool"], [["fnv3", 0], ["wnv3", 0], ["genc", 6]])
        self.assertEqual(ap["n_members"], 4 + 6 + 5)
        self.assertEqual(ap["wnModel"], "wnv3")
        self.assertEqual(ap["wnMembers"], 6)


DEFAULT_MODEL_HARNESS = (Path(__file__).resolve().parent
                         / "enscenters_default_model.cjs")


@unittest.skipIf(NODE is None, "node not on PATH")
@unittest.skipUnless(jsdom_available(), "jsdom not resolvable")
class TestDefaultModelSelection(unittest.TestCase):
    """The viewer opens on the FRESHEST model, not the hard default_model
    (ECMWF ENS disseminates ~1h late, so for a window each 00/12Z it is one
    cycle behind its peers - the user must not land on the laggard)."""

    def _run(self):
        proc = subprocess.run([NODE, str(DEFAULT_MODEL_HARNESS), str(JS)],
                              cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_default_model_selection(self):
        s = self._run()
        # mixed freshness -> the freshest model wins, even though the laggard
        # (ecens) is the manifest default_model.
        self.assertEqual(s["mixed"], "aifs")
        self.assertEqual(s["defaultBehindStillBeatsLaggard"], "fnv3")
        # all tied on the newest cycle -> preferred order (default_model) wins.
        self.assertEqual(s["allEqualPrefersDefault"], "ecens")
        # tied with no default match -> manifest order.
        self.assertEqual(s["tieNoDefaultKeepsOrder"], "gefs")
        # no model has a cycle yet -> fall back to the default_model.
        self.assertEqual(s["noCyclesFallsBackToDefault"], "ecens")
        # the full viewer opens on the freshest model on load...
        self.assertEqual(s["loadSelectedModel"], "aifs")
        # ...respects an explicit user model choice...
        self.assertEqual(s["afterUserClick"], "ecens")
        # ...and a subsequent poll NEVER overrides it (sticky).
        self.assertEqual(s["afterPoll"], "ecens")


if __name__ == "__main__":
    unittest.main()
