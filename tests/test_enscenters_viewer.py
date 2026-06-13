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
        # the single-model auto-hide must hide only the model group, not the bar
        self.assertFalse(s["controlbarHidden"])
        self.assertTrue(s["modelgroupHidden"])
        self.assertEqual(s["pickerCardCount"], 23)        # all registry regions
        self.assertEqual(s["globalExtent"], [0, 360, -88, 88])   # Pacific-centered
        self.assertEqual(s["wpacExtent"], [100, 180, 0, 45])
        self.assertTrue(s["allVisibleInWpac"])            # scatter filtered to region
        self.assertEqual(s["lastRegionSaved"], "wpac")    # localStorage persistence

        # Run (cycle) selector: built from the manifest cycle list, newest first,
        # latest labelled and selected.
        self.assertEqual(s["runOptionCount"], 2)
        self.assertEqual(s["runValue"], "2026061300")
        self.assertEqual(s["runFirstLabel"], "Jun 13 00Z (latest)")
        self.assertEqual(s["runSecondLabel"], "Jun 12 18Z")


if __name__ == "__main__":
    unittest.main()
