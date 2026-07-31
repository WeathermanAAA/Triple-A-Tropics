"""Offline behavioral tests for the HAFS progressive-frames viewer
(models/hafs.js), driven through tests/hafs_viewer_harness.cjs under node with a
minimal DOM shim. No network, no browser.

Covers the manifest-v2 contract (/tmp/manifest_v2_contract.md):
  1. legacy manifest (no cycles[]) -> old behavior: fxxList = rendered list,
     URL via path_template + ?v= bust, hour grid spans rendered hours only.
  2. v2 manifest -> cycle-picker data; default = newest-with-frames;
     pre-announce entry skipped for the default but flagged.
  3. expected-grid construction (0..fxx_end step fxx_step): rendered hours are
     lit + clickable buttons, pending hours greyed + disabled (inert clicks).
  4. diff-merge: a second manifest with MORE fxx for the current selection
     relights the hour buttons WITHOUT resetting
     cycle/storm/model/domain/product/hour.
  5. URL building in cycles-mode: {cycle} substituted, NO ?v= cache-bust.
  6. a NEW cycle appearing mid-session -> badge state set, selection NOT
     switched; clicking the badge switches.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HAFS_JS = REPO / "models" / "hafs.js"
HARNESS = Path(__file__).resolve().parent / "hafs_viewer_harness.cjs"
NODE = shutil.which("node")

BASE = "https://cdn.triple-a-tropics.com/models/hafs/"


def run_plan(manifests, actions=None, viewer_opts=None):
    """Drive the viewer through `manifests` (first = initial load, rest fed to
    successive _poll() actions) and `actions`; return the list of state
    snapshots (steps[0] = after first load). viewer_opts = the second-mount
    constructor config (manifestUrl/stormLock/els_injected)."""
    plan = {"manifests": manifests, "actions": actions or []}
    if viewer_opts:
        plan["viewer_opts"] = viewer_opts
    with tempfile.TemporaryDirectory() as td:
        plan_path = Path(td) / "plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        proc = subprocess.run(
            [NODE, str(HARNESS), str(HAFS_JS), str(plan_path)],
            capture_output=True, text=True, timeout=60,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"node harness failed:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(proc.stdout)["steps"]


# ---- manifest fixtures -----------------------------------------------------

PRODUCTS = [
    {"slug": "mslp_wind", "label": "MSLP + 10 m Wind", "short": "Wind"},
    {"slug": "refl", "label": "Composite Reflectivity", "short": "Reflectivity"},
]
MODELS = [{"slug": "hafsa", "label": "HAFS-A"}, {"slug": "hafsb", "label": "HAFS-B"}]
DOMAINS = [
    {"slug": "storm", "label": "Storm nest (~2 km)", "raw": "storm.atm"},
    {"slug": "parent", "label": "Parent (~6 km)", "raw": "parent.atm"},
]


def storm(sid="13l", name="13L", basin="al", cycle="2026060418",
          init="2026-06-04T18:00:00Z", hours=None):
    """A storm carrying both products on the storm nest of HAFS-A."""
    hrs = list(range(0, 127, 3)) if hours is None else hours
    return {
        "id": sid, "name": name, "basin": basin, "basin_label": "North Atlantic",
        "cycle": cycle, "init": init,
        "frames": {"hafsa": {"storm": {"mslp_wind": hrs, "refl": hrs}}},
    }


def legacy_manifest(hours=None):
    """v1 (no cycles[]): the pre-progressive shape this frontend must still
    drive byte-for-byte under deploy skew."""
    hrs = list(range(0, 127, 3)) if hours is None else hours
    return {
        "generated_at": "2026-06-04T19:00:00Z",
        "products": PRODUCTS, "models": MODELS, "domains": DOMAINS,
        "fxx_step": 3, "fxx_pad": 3,
        "path_template": "2026060418/{model}/{storm}/{domain}/{product}/f{fxx}.png",
        "cycle": "2026060418",
        "storms": [storm(hours=hrs)],
    }


def v2_manifest(cycles, generated="2026-06-04T19:30:00Z"):
    return {
        "generated_at": generated,
        "products": PRODUCTS, "models": MODELS, "domains": DOMAINS,
        "fxx_step": 3, "fxx_pad": 3, "fxx_end": 126,
        "path_template_cycles":
            "{cycle}/{model}/{storm}/{domain}/{product}/f{fxx}.png",
        "path_template":
            cycles[-1]["cycle"] + "/{model}/{storm}/{domain}/{product}/f{fxx}.png",
        "cycle": cycles[-1]["cycle"],
        "storms": cycles[-1]["storms"],
        "cycles": cycles,
    }


def cycle_entry(key, hours, in_progress=True, storms=None, init=None,
                frames_done=None, frames_expected=172):
    if storms is None:
        init = init or (key[:4] + "-" + key[4:6] + "-" + key[6:8] +
                        "T" + key[8:10] + ":00:00Z")
        storms = [storm(cycle=key, init=init, hours=hours)]
    return {
        "cycle": key, "in_progress": in_progress,
        "frames_done": frames_done if frames_done is not None else len(hours) * 2,
        "frames_expected": frames_expected,
        "started_utc": "2026-06-04T21:12:00Z",
        "storms": storms,
    }


@unittest.skipIf(NODE is None, "node not on PATH")
class TestHafsViewer(unittest.TestCase):

    # 1 ---------------------------------------------------------------------
    def test_legacy_manifest_old_behavior(self):
        hrs = [0, 3, 6, 9, 12]
        steps = run_plan([legacy_manifest(hours=hrs)])
        s = steps[0]
        self.assertTrue(s["legacyMode"])
        self.assertFalse(s["cyclePickerShown"])
        # fxxList == the rendered list; grid degenerates to it (no fxx_end).
        self.assertEqual(s["fxxList"], hrs)
        self.assertEqual(s["fxxGrid"], hrs)
        # hour grid spans rendered hours only - every button lit + enabled,
        # zero-padded labels.
        self.assertEqual([h["fxx"] for h in s["hours"]], hrs)
        self.assertTrue(all(h["lit"] and not h["disabled"] for h in s["hours"]))
        self.assertEqual(s["hours"][0]["label"], "000")
        # default selection: HAFS-A / storm / mslp_wind, F000.
        self.assertEqual(s["model"], "hafsa")
        self.assertEqual(s["domain"], "storm")
        self.assertEqual(s["product"], "mslp_wind")
        self.assertEqual(s["fxx"], 0)
        # LEGACY url: path_template (cycle baked in literally) + ?v= bust.
        self.assertEqual(
            s["imgSrc"],
            BASE + "2026060418/hafsa/13l/storm/mslp_wind/f000.png"
            "?v=2026-06-04T19%3A00%3A00Z",
        )

    # 2 ---------------------------------------------------------------------
    def test_v2_default_is_newest_with_frames_preannounce_flagged(self):
        # newest cycle (18Z) is an empty pre-announce shell; 12Z has frames.
        empty = cycle_entry("2026060418", [], in_progress=True,
                            storms=[], frames_done=0)
        # storms=[] path: build an explicit empty-storms cycle.
        empty["storms"] = []
        full = cycle_entry("2026060412", [0, 3, 6, 9, 12, 15],
                           in_progress=False)
        steps = run_plan([v2_manifest([empty, full])])
        s = steps[0]
        self.assertFalse(s["legacyMode"])
        # cycle picker present with BOTH cycles, newest first.
        self.assertTrue(s["cyclePickerShown"])
        self.assertEqual(s["cycleKeys"], ["2026060418", "2026060412"])
        # default selection skips the empty newest -> 12Z, but flags pre-announce.
        self.assertEqual(s["selectedCycle"], "2026060412")
        self.assertTrue(s["preAnnounce"])
        self.assertTrue(s["badgeShown"])
        self.assertIn("18Z", s["badgeText"])
        # the active 12Z cycle is complete -> no pill.
        self.assertFalse(s["pillShown"])

    def test_v2_default_newest_when_it_has_frames(self):
        newest = cycle_entry("2026060418", [0, 3, 6], in_progress=True)
        older = cycle_entry("2026060412", list(range(0, 127, 3)),
                            in_progress=False)
        steps = run_plan([v2_manifest([newest, older])])
        s = steps[0]
        self.assertEqual(s["selectedCycle"], "2026060418")
        self.assertFalse(s["preAnnounce"])
        # in-progress pill shows max rendered / fxx_end.
        self.assertTrue(s["pillShown"])
        self.assertIn("F006/F126", s["pillText"])

    # 3 ---------------------------------------------------------------------
    def test_expected_grid_lit_clickable_pending_disabled(self):
        # rendered through F018 only; grid runs the full 0..126 step 3.
        rendered = [0, 3, 6, 9, 12, 15, 18]
        c = cycle_entry("2026060418", rendered, in_progress=True)
        full_grid = list(range(0, 127, 3))
        m = v2_manifest([c, cycle_entry("2026060412", full_grid,
                                        in_progress=False)])
        steps = run_plan([m])
        s = steps[0]
        self.assertEqual(s["fxxGrid"], full_grid)
        self.assertEqual(s["fxxList"], rendered)
        # hour buttons span the full grid, one per expected hour.
        self.assertEqual([h["fxx"] for h in s["hours"]], full_grid)
        # first 7 lit + clickable, rest pending + disabled (visible but inert).
        lit = [i for i, h in enumerate(s["hours"]) if h["lit"]]
        pending = [i for i, h in enumerate(s["hours"]) if h["pending"]]
        self.assertEqual(lit, list(range(7)))
        self.assertEqual(pending, list(range(7, len(full_grid))))
        self.assertTrue(all(not s["hours"][i]["disabled"] for i in lit))
        self.assertTrue(all(s["hours"][i]["disabled"] for i in pending))

        # Clicking a pending hour (F030) is inert; clicking lit F018 selects it
        # and moves the highlight there.
        steps2 = run_plan([m], actions=[{"op": "clickHour", "fxx": 30},
                                        {"op": "clickHour", "fxx": 18}])
        inert = steps2[1]
        self.assertEqual(inert["fxx"], 0)        # unchanged by the dead click
        picked = steps2[2]
        self.assertEqual(picked["fxx"], 18)
        self.assertEqual(picked["idx"], 6)
        current = [h["fxx"] for h in picked["hours"] if h["current"]]
        self.assertEqual(current, [18])

    def test_domain_switch_snaps_pending_hour_to_nearest_lower(self):
        # Pins the snap-to-nearest-rendered (prefer LOWER) contract of
        # _renderedIndexNear, which still runs whenever a selection switch
        # finds the held hour pending in the new fxx list. The nest has F009
        # rendered; the parent only [0, 6, 12]. Keeping the hour across the
        # domain switch finds F009 pending -> snaps DOWN to F006. A
        # prefer-higher regression would land on F012 instead.
        nest_hours = [0, 3, 6, 9, 12, 15, 18]
        parent_hours = [0, 6, 12]
        st = storm(hours=nest_hours)
        st["frames"]["hafsa"]["parent"] = {"mslp_wind": parent_hours,
                                           "refl": parent_hours}
        m = v2_manifest([cycle_entry("2026060418", nest_hours,
                                     in_progress=True, storms=[st])])
        steps = run_plan([m], actions=[
            {"op": "clickHour", "fxx": 9},
            {"op": "selectDomain", "slug": "parent"},
        ])
        self.assertEqual(steps[1]["fxx"], 9)
        snapped = steps[2]
        self.assertEqual(snapped["domain"], "parent")
        self.assertEqual(snapped["fxx"], 6)      # prefer-lower, NOT 12
        self.assertEqual(snapped["idx"], 1)
        self.assertEqual([h["fxx"] for h in snapped["hours"] if h["current"]],
                         [6])
        # the parent grid still spans the full expected hours; only the
        # parent's rendered set is lit.
        self.assertEqual([h["fxx"] for h in snapped["hours"] if h["lit"]],
                         parent_hours)

    # 4 ---------------------------------------------------------------------
    def test_diff_merge_grows_grid_without_resetting_selection(self):
        rendered1 = [0, 3, 6, 9]
        rendered2 = [0, 3, 6, 9, 12, 15, 18, 21]
        full_grid = list(range(0, 127, 3))
        m1 = v2_manifest([cycle_entry("2026060418", rendered1, in_progress=True),
                          cycle_entry("2026060412", full_grid, in_progress=False)])
        m2 = v2_manifest(
            [cycle_entry("2026060418", rendered2, in_progress=True),
             cycle_entry("2026060412", full_grid, in_progress=False)],
            generated="2026-06-04T19:45:00Z")
        # Switch to HAFS-A reflectivity, jump to F009, THEN poll the grown
        # manifest: selection (model/domain/product/hour) must survive.
        actions = [
            {"op": "selectProduct", "slug": "refl"},
            {"op": "clickHour", "fxx": 9},   # F009 (rendered) -> idx 3
            {"op": "poll"},
        ]
        steps = run_plan([m1, m2], actions=actions)
        before = steps[3 - 1]   # after the hour click, before the poll
        after = steps[3]        # after the poll/diff-merge
        # selection preserved across the poll.
        self.assertEqual(after["selectedCycle"], "2026060418")
        self.assertEqual(after["storm"], before["storm"])
        self.assertEqual(after["model"], "hafsa")
        self.assertEqual(after["domain"], "storm")
        self.assertEqual(after["product"], "refl")
        self.assertEqual(after["fxx"], 9)        # same forecast hour
        # the lit hour buttons relit from 4 -> 8; F009 still highlighted.
        lit_before = sum(1 for h in before["hours"] if h["lit"])
        lit_after = sum(1 for h in after["hours"] if h["lit"])
        self.assertEqual(lit_before, 4)
        self.assertEqual(lit_after, 8)
        self.assertEqual([h["fxx"] for h in after["hours"] if h["current"]],
                         [9])
        # grid unchanged (already full).
        self.assertEqual(after["fxxGrid"], full_grid)
        self.assertEqual(after["fxxList"], rendered2)

    # 5 ---------------------------------------------------------------------
    def test_cycles_mode_url_has_cycle_no_version_bust(self):
        c = cycle_entry("2026060418", [0, 3, 6], in_progress=True)
        m = v2_manifest([c, cycle_entry("2026060412", [0, 3], in_progress=False)])
        steps = run_plan([m])
        s = steps[0]
        # {cycle} substituted, no ?v= (immutable cycle-scoped key).
        self.assertEqual(
            s["imgSrc"],
            BASE + "2026060418/hafsa/13l/storm/mslp_wind/f000.png",
        )
        self.assertNotIn("?v=", s["imgSrc"])

    # 6 ---------------------------------------------------------------------
    def test_new_cycle_appears_badges_without_switching(self):
        # Session starts on 12Z (newest is 12Z, complete). A poll then reveals a
        # newer 18Z WITH frames -> badge set, selection NOT switched.
        full_grid = list(range(0, 127, 3))
        m1 = v2_manifest([cycle_entry("2026060412", full_grid, in_progress=False)])
        m2 = v2_manifest(
            [cycle_entry("2026060418", [0, 3, 6], in_progress=True),
             cycle_entry("2026060412", full_grid, in_progress=False)],
            generated="2026-06-04T19:45:00Z")
        steps = run_plan([m1, m2], actions=[{"op": "poll"}, {"op": "clickBadge"}])
        load = steps[0]
        self.assertEqual(load["selectedCycle"], "2026060412")
        self.assertFalse(load["cyclePickerShown"])  # only 1 cycle at load

        after_poll = steps[1]
        # selection NOT yanked.
        self.assertEqual(after_poll["selectedCycle"], "2026060412")
        self.assertEqual(after_poll["pendingCycleKey"], "2026060418")
        self.assertTrue(after_poll["badgeShown"])
        self.assertIn("18Z", after_poll["badgeText"])
        self.assertIn("view", after_poll["badgeText"])
        # picker now shows both cycles (set changed).
        self.assertTrue(after_poll["cyclePickerShown"])
        self.assertEqual(after_poll["cycleKeys"], ["2026060418", "2026060412"])

        # clicking the badge switches to the newer cycle and clears the badge.
        after_click = steps[2]
        self.assertEqual(after_click["selectedCycle"], "2026060418")
        self.assertIsNone(after_click["pendingCycleKey"])
        self.assertFalse(after_click["badgeShown"])

    # extra coverage ---------------------------------------------------------
    def test_off_season_empty_state(self):
        # v2 manifest with a single empty cycle (no storms) -> empty state.
        empty = cycle_entry("2026060418", [], in_progress=False, storms=[])
        steps = run_plan([v2_manifest([empty])])
        s = steps[0]
        self.assertTrue(s["emptyShown"])
        self.assertIsNone(s["storm"])

    def test_legacy_product_toggle_holds_hour(self):
        # The pre-progressive sticky-selection path: switch product, the
        # forecast HOUR is preserved (Wind/Reflectivity share an fxx list).
        steps = run_plan(
            [legacy_manifest(hours=[0, 3, 6, 9, 12])],
            actions=[{"op": "clickHour", "fxx": 9},
                     {"op": "selectProduct", "slug": "refl"}],
        )
        self.assertEqual(steps[1]["fxx"], 9)
        self.assertEqual(steps[2]["product"], "refl")
        self.assertEqual(steps[2]["fxx"], 9)   # hour held across the toggle
        # legacy refl url keeps the ?v= bust.
        self.assertIn("/refl/f009.png?v=", steps[2]["imgSrc"])

    def test_poll_cadence_helper_via_in_progress_flag(self):
        # Sanity: a cycle marked in_progress should keep the pill alive after a
        # no-op poll (state stable, no reset).
        c = cycle_entry("2026060418", [0, 3, 6], in_progress=True)
        m = v2_manifest([c])
        steps = run_plan([m], actions=[{"op": "poll"}])
        self.assertTrue(steps[1]["pillShown"])
        self.assertEqual(steps[1]["selectedCycle"], "2026060418")



class TestMountConfig(unittest.TestCase):
    """The CycloLab second mount (CYCLOLAB_DESIGN §7.3): HafsViewer(root,
    {manifestUrl, els, stormLock}) - one impl, two mounts, no fork. The
    /models/ mount passes no opts; these prove the injected config works
    AND that the default path is untouched (the rest of this suite runs
    optless and stayed green through the componentization)."""

    def test_storm_lock_filters_and_hides_picker(self):
        two = cycle_entry("2026060500", list(range(0, 13, 3)), in_progress=False,
                          storms=[storm(sid="13l", name="13L", cycle="2026060500"),
                                  storm(sid="01e", name="AMANDA", cycle="2026060500")])
        steps = run_plan([v2_manifest([two])],
                         viewer_opts={"stormLock": "01e"})
        st = steps[0]
        self.assertEqual(st["storm"], "01e")
        self.assertEqual(st["stormOptions"], ["01e"])
        self.assertTrue(st["stormSelHidden"],
                        "locked mount must hide the storm picker")
        self.assertTrue(st["fxxList"], "locked storm's frames load")

    def test_storm_lock_picks_newest_cycle_with_frames_for_that_storm(self):
        # newest cycle carries ONLY the other storm -> the locked mount must
        # fall back to the older cycle that has the locked storm's frames.
        old = cycle_entry("2026060418", list(range(0, 25, 3)), in_progress=False,
                          storms=[storm(sid="01e", name="AMANDA",
                                        cycle="2026060418")])
        new = cycle_entry("2026060500", list(range(0, 13, 3)), in_progress=True,
                          storms=[storm(sid="13l", name="13L",
                                        cycle="2026060500")])
        steps = run_plan([v2_manifest([new, old])],
                         viewer_opts={"stormLock": "01e"})
        st = steps[0]
        self.assertEqual(st["selectedCycle"], "2026060418")
        self.assertEqual(st["storm"], "01e")
        self.assertFalse(st["emptyShown"])

    def test_storm_lock_absent_storm_shows_empty_state(self):
        only_other = cycle_entry("2026060500", list(range(0, 13, 3)),
                                 in_progress=False,
                                 storms=[storm(sid="13l", cycle="2026060500")])
        steps = run_plan([v2_manifest([only_other])],
                         viewer_opts={"stormLock": "01e"})
        self.assertTrue(steps[0]["emptyShown"])
        self.assertIsNone(steps[0]["storm"])

    def test_storm_lock_cycle_without_storm_is_safe_and_recoverable(self):
        # THE adversarial-review crash: under lock, the cycle picker still
        # lists cycles that lack the locked storm; clicking one used to
        # TypeError (_selectStorm(undefined)) and kill the tab. Now it is
        # a per-cycle empty state and switching back recovers.
        old = cycle_entry("2026060418", list(range(0, 25, 3)),
                          in_progress=False,
                          storms=[storm(sid="01e", name="AMANDA",
                                        cycle="2026060418")])
        new = cycle_entry("2026060500", list(range(0, 13, 3)),
                          in_progress=True,
                          storms=[storm(sid="13l", name="13L",
                                        cycle="2026060500")])
        steps = run_plan([v2_manifest([new, old])],
                         actions=[
                             {"op": "selectCycle", "key": "2026060500"},
                             {"op": "selectCycle", "key": "2026060418"},
                         ],
                         viewer_opts={"stormLock": "01e"})
        self.assertEqual(steps[0]["selectedCycle"], "2026060418")
        empty = steps[1]
        self.assertTrue(empty["emptyShown"],
                        "cycle without the locked storm must show the "
                        "empty state, not crash")
        self.assertIsNone(empty["storm"])
        self.assertEqual(empty["cycleKeys"],
                         ["2026060500", "2026060418"],
                         "cycle picker must survive the empty selection")
        back = steps[2]
        self.assertFalse(back["emptyShown"])
        self.assertEqual(back["storm"], "01e")
        self.assertTrue(back["fxxList"], "recovery restores frames")

    def test_unlocked_empty_preannounce_cycle_click_is_safe(self):
        # The same latent crash existed UNLOCKED on /models/: hand-selecting
        # an empty pre-announce shell. Crash -> graceful empty state is the
        # only sanctioned behavior change for the optless mount.
        shell = cycle_entry("2026060500", [], in_progress=True, storms=[],
                            frames_done=0)
        old = cycle_entry("2026060418", list(range(0, 25, 3)),
                          in_progress=False)
        steps = run_plan([v2_manifest([shell, old])],
                         actions=[{"op": "selectCycle", "key": "2026060500"},
                                  {"op": "selectCycle", "key": "2026060418"}])
        self.assertTrue(steps[1]["emptyShown"])
        self.assertFalse(steps[2]["emptyShown"])
        self.assertEqual(steps[2]["storm"], "13l")

    def test_injected_manifest_url_is_fetched(self):
        url = "https://example.test/cyclolab/hafs-manifest.json"
        steps = run_plan([legacy_manifest()],
                         viewer_opts={"manifestUrl": url})
        self.assertTrue(steps[0]["fetchedUrls"][0].startswith(url + "?"),
                        steps[0]["fetchedUrls"])

    def test_default_mount_fetches_models_manifest_and_shows_picker(self):
        steps = run_plan([legacy_manifest()])
        self.assertIn("/models/hafs/manifest.json", steps[0]["fetchedUrls"][0])
        self.assertFalse(steps[0]["stormSelHidden"])

    def test_injected_els_table_no_global_lookups(self):
        # els_injected poisons document.getElementById for hafs-* ids - any
        # residual global lookup crashes the harness. The viewer must drive
        # the injected table end-to-end.
        steps = run_plan([legacy_manifest()],
                         viewer_opts={"els_injected": True})
        st = steps[0]
        self.assertEqual(st["storm"], "13l")
        self.assertTrue(st["fxxList"])
        self.assertTrue(st["imgSrc"], "frame rendered via injected els")


if __name__ == "__main__":
    unittest.main(verbosity=2)

# ---------------------------------------------------------------------------
# Item 17: the five-state availability model. PENDING vs UNAVAILABLE is the
# state that matters most - "wait ~90 seconds" vs "stop waiting" - and it
# needs the manifest's per-pair `expected` hours: present hours alone cannot
# tell a frame that has not rendered YET from one that never will.
# ---------------------------------------------------------------------------
def _man_expected(in_progress, frames, expected, fxx_end=12):
    return {
        "generated_at": "t", "fxx_step": 3, "fxx_pad": 3, "fxx_end": fxx_end,
        "products": [{"slug": "mslp_wind", "label": "Wind", "short": "Wind"}],
        "models": [{"slug": "hafsa", "label": "HAFS-A"}],
        "domains": [{"slug": "storm", "label": "Storm nest", "raw": "storm.atm"}],
        "path_template_cycles": "{cycle}/{model}/{storm}/{domain}/{product}/f{fxx}.png",
        "cycles": [{
            "cycle": "2026073100", "in_progress": in_progress,
            "frames_done": len(frames), "frames_expected": len(expected),
            "started_utc": "t",
            "storms": [{
                "id": "07e", "name": "07E", "basin": "ep",
                "cycle": "2026073100", "init": "2026-07-31T00:00:00Z",
                "frames": {"hafsa": {"storm": {"mslp_wind": frames}}},
                "expected": {"hafsa": {"storm": expected}},
            }],
        }],
    }


class TestFiveStateAvailability(unittest.TestCase):

    def _states(self, step):
        return {h["fxx"]: h["state"] for h in step["hours"]}

    def test_building_cycle_pending_vs_unscheduled(self):
        """While the cycle builds: rendered hours are lit, expected-but-absent
        hours are PENDING (keep waiting), hours upstream never posted are
        UNSCHEDULED - three visibly different answers, not one grey."""
        steps = run_plan([_man_expected(True, [0, 3], [0, 3, 6, 9])])
        st = self._states(steps[0])
        self.assertEqual(st[0], "lit")
        self.assertEqual(st[3], "lit")
        self.assertEqual(st[6], "pending")
        self.assertEqual(st[9], "pending")
        self.assertEqual(st[12], "unsched")

    def test_complete_cycle_flips_pending_to_unavailable(self):
        """The SAME missing hours on a COMPLETE cycle mean something different:
        the frame failed and is not coming. Stop waiting - which is precisely
        the distinction the two-state strip could not make."""
        steps = run_plan([_man_expected(False, [0, 3], [0, 3, 6, 9])])
        st = self._states(steps[0])
        self.assertEqual(st[6], "unavail")
        self.assertEqual(st[9], "unavail")
        self.assertEqual(st[12], "unsched")

    def test_only_rendered_hours_are_interactive(self):
        steps = run_plan([_man_expected(True, [0, 3], [0, 3, 6, 9])])
        for h in steps[0]["hours"]:
            self.assertEqual(not h["disabled"], h["state"] == "lit",
                             f"fxx {h['fxx']}")

    def test_manifest_without_expected_degrades_to_two_state(self):
        """An older manifest (no `expected`) must render exactly the old
        behaviour: every non-rendered hour is generic pending. No guessing."""
        m = _man_expected(False, [0, 3], [0, 3, 6, 9])
        del m["cycles"][0]["storms"][0]["expected"]
        steps = run_plan([m])
        st = self._states(steps[0])
        self.assertEqual(st[6], "pending")
        self.assertEqual(st[12], "pending")

    def test_expected_survives_the_poll_merge(self):
        """The 45 s poll replaces cycle entries; the five states must be
        recomputed from the NEW manifest, not frozen from the first paint."""
        m1 = _man_expected(True, [0, 3], [0, 3, 6, 9])
        m2 = _man_expected(False, [0, 3, 6], [0, 3, 6, 9])
        steps = run_plan([m1, m2], actions=[{"op": "poll"}])
        st = self._states(steps[-1])
        self.assertEqual(st[6], "lit")
        self.assertEqual(st[9], "unavail")

