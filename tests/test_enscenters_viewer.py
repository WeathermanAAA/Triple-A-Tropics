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
        {"key": "lt950", "label": "<950 hPa", "lo": None, "hi": 950},
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


if __name__ == "__main__":
    unittest.main()
