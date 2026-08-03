#!/usr/bin/env python3
"""v2 manifest + never-regress merge tests for the HAFS dual-writer fix.

The cron (generate_hafs_plots) and the box render worker BOTH write
models/hafs/manifest.json. These tests pin (a) the cron's published shape to the
worker's compose_manifest_v2 schema, and (b) the never-regress merge that lets
the two writers coexist on one key without clobbering each other.

Run: python -m pytest hafs_render/tests/test_manifest_v2.py   (or python -m unittest)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hafs_render import generate_hafs_plots as g          # noqa: E402
from hafs_render.publish_manifest import merge_cycles      # noqa: E402

# The published v2 schema (the worker's compose_manifest_v2 output + the live
# manifest). The cron must produce EXACTLY this key set so neither writer
# clobbers the other with an incompatible shape.
V2_KEYS = {"generated_at", "product", "products", "models", "domains",
           "fxx_step", "fxx_pad", "fxx_end", "path_template_cycles", "cycles",
           "cycle", "storms", "path_template",
           # Phase 0 foundations. Frame-INVARIANT geometry (the projection and
           # the pixel canvas; the per-frame axes rect + lon/lat extent ride
           # under storms[].geometry) plus the quantity-keyed value planes.
           # Together these are what a lat/lon readout, a value readout, and a
           # ruler are built from. Additive: every pre-existing key is unchanged,
           # so the box render worker's manifest stays compatible.
           "projection", "image", "quantities",
           # #27: the container read path (geometric tar blocks; per-row
           # block lists ride under storms[].blocks).
           "containers"}
ENTRY_KEYS = {"cycle", "in_progress", "frames_done", "frames_expected",
              "started_utc", "storms"}


def _storms(cycle="2026061912"):
    return [{"id": "07w", "name": "07W", "basin": "wp", "cycle": cycle,
             "frames": {"hafsa": {"storm": {"clean_ir": [0, 3, 6]}}}}]


def _entry(cycle, in_progress=False, storms=None):
    return {"cycle": cycle, "in_progress": in_progress, "frames_done": 1,
            "frames_expected": 1, "started_utc": "t",
            "storms": storms if storms is not None else [{"id": "07w"}]}


def _man(cycles):
    return {"cycles": cycles, "product": "p", "products": ["p"], "models": [],
            "domains": [], "fxx_step": 3, "fxx_pad": 3, "fxx_end": 126,
            "path_template_cycles": "{cycle}/{model}/{storm}/{domain}/{product}/f{fxx}.png",
            "generated_at": "now"}


class V2ShapeTests(unittest.TestCase):
    def test_skeleton_is_v2_and_cycle_scoped(self):
        m = g._manifest_skeleton(["hafsa", "hafsb"], ["storm.atm", "parent.atm"],
                                 g.DEFAULT_PRODUCTS, 3, "2026061912", _storms(),
                                 started_utc="2026-06-19T18:00:00Z")
        self.assertEqual(set(m.keys()), V2_KEYS)
        self.assertEqual(set(m["cycles"][0].keys()), ENTRY_KEYS)
        self.assertEqual(m["path_template_cycles"],
                         "{cycle}/{model}/{storm}/{domain}/{product}/f{fxx}.png")
        # legacy path bakes the cycle so an old frontend resolves cycle-scoped keys
        self.assertTrue(m["path_template"].startswith("2026061912/"))
        self.assertEqual(m["cycle"], "2026061912")
        self.assertEqual(m["cycles"][0]["in_progress"], False)
        self.assertEqual(m["cycles"][0]["frames_done"], 3)  # 3 fxx rendered
        self.assertEqual(m["cycles"][0]["frames_done"],
                         m["cycles"][0]["frames_expected"])  # complete

    def test_off_season_empty_v2(self):
        m = g._manifest_skeleton(["hafsa"], ["storm.atm"], g.DEFAULT_PRODUCTS,
                                 3, None, [])
        self.assertEqual(set(m.keys()), V2_KEYS)
        self.assertEqual(m["cycles"], [])
        self.assertIsNone(m["cycle"])
        self.assertEqual(m["storms"], [])
        # no complete cycle -> generic (non-cycle) legacy template
        self.assertEqual(m["path_template"],
                         "{model}/{storm}/{domain}/{product}/f{fxx}.png")


class FrameLayoutTests(unittest.TestCase):
    def test_cycle_scoped_nests_under_cycle(self):
        from pathlib import Path
        p = g._frame_out_path(Path("models/hafs"), "2026061912", "hafsa", "07w",
                              "storm", "clean_ir", 6, cycle_scoped=True)
        self.assertEqual(
            p, "models/hafs/2026061912/hafsa/07w/storm/clean_ir/f006.png")

    def test_flat_default_is_legacy_layout(self):
        from pathlib import Path
        p = g._frame_out_path(Path("models/hafs"), "2026061912", "hafsa", "07w",
                              "storm", "clean_ir", 6, cycle_scoped=False)
        self.assertEqual(p, "models/hafs/hafsa/07w/storm/clean_ir/f006.png")


class MergeTests(unittest.TestCase):
    def test_upsert_keeps_other_cycles(self):
        existing = _man([_entry("2026061912", in_progress=True), _entry("2026061906")])
        fresh = _man([_entry("2026061906")])   # cron re-renders 06z complete
        m = merge_cycles(existing, fresh)
        self.assertEqual([c["cycle"] for c in m["cycles"]],
                         ["2026061912", "2026061906"])     # building 12z kept
        self.assertEqual(m["cycle"], "2026061906")          # newest COMPLETE -> legacy

    def test_cron_complete_replaces_worker_in_progress(self):
        existing = _man([_entry("2026061912", in_progress=True)])
        fresh = _man([_entry("2026061912", in_progress=False)])
        m = merge_cycles(existing, fresh)
        self.assertEqual(len(m["cycles"]), 1)
        self.assertFalse(m["cycles"][0]["in_progress"])     # upgraded to complete

    def test_never_regress_refuses_to_drop_newest(self):
        existing = _man([_entry("2026061918", in_progress=True), _entry("2026061912")])
        fresh = _man([_entry("2026061900")])   # cron renders a much OLDER cycle
        m = merge_cycles(existing, fresh)
        # newest building 18z must survive; with retain=2 it does (00z dropped)
        self.assertIsNotNone(m)
        self.assertIn("2026061918", [c["cycle"] for c in m["cycles"]])

    def test_retain_drops_oldest_not_newest(self):
        existing = _man([_entry("2026061918", in_progress=True), _entry("2026061912")])
        fresh = _man([_entry("2026061915")])   # a NEW middle cycle
        m = merge_cycles(existing, fresh, retain=2)
        self.assertEqual([c["cycle"] for c in m["cycles"]],
                         ["2026061918", "2026061915"])       # oldest 12z aged out

    def test_empty_render_is_noop(self):
        existing = _man([_entry("2026061912", in_progress=True)])
        self.assertIsNone(merge_cycles(existing, _man([])))  # never clobber

    def test_absent_existing_wraps_fresh(self):
        m = merge_cycles(None, _man([_entry("2026061912")]))
        self.assertEqual([c["cycle"] for c in m["cycles"]], ["2026061912"])
        self.assertIn("path_template_cycles", m)

    def test_legacy_existing_replaced_by_v2(self):
        legacy = {"cycle": "x", "storms": [], "path_template": "{model}/..."}
        m = merge_cycles(legacy, _man([_entry("2026061912")]))
        self.assertEqual([c["cycle"] for c in m["cycles"]], ["2026061912"])
        self.assertIn("cycles", m)


if __name__ == "__main__":
    unittest.main()
