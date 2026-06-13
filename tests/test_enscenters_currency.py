"""
Tests for the never-miss currency core (enscenters.currency) + the ECMWF
completeness gate (enscenters.ingest) + the multi-cycle manifest merge.

No network: the gate is exercised through an injected ``present_fn`` and
``run_currency`` through stub hooks, so the backfill/prune/exit-code logic is
verified deterministically. Run: python -m unittest discover tests
"""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from enscenters import registry as reg
from enscenters.currency import plan_backfill, run_currency, synoptic_cycles_back
from enscenters.ingest import cycle_complete, cycle_requests, list_complete_cycles
from enscenters.pipeline import merge_manifest_multi, published_cycles

SPEC = reg.get_spec("ecens")


def C(s):  # "2026061300" -> datetime
    return dt.datetime.strptime(s, "%Y%m%d%H")


def manifest_with(cycles):
    return {"models": [{"slug": "ecens", "label": "ECMWF ENS", "cycles": list(cycles)}]}


class TestPlanBackfill(unittest.TestCase):
    # 8 contiguous 6-hourly cycles, newest last
    ALL = ["2026061100", "2026061106", "2026061112", "2026061118",
           "2026061200", "2026061206", "2026061212", "2026061218"]

    def test_gap_in_middle_backfills_only_that_cycle(self):
        gap = self.ALL[3]
        published = [c for c in self.ALL if c != gap]   # one mid cycle dropped
        plan = plan_backfill(published, self.ALL, retain=8, max_per_run=3)
        self.assertEqual(plan, [gap])

    def test_no_gap_publishes_only_the_new_cycle(self):
        published = self.ALL[:-1]            # everything but the newest
        plan = plan_backfill(published, self.ALL, retain=8, max_per_run=3)
        self.assertEqual(plan, [self.ALL[-1]])

    def test_nothing_missing_is_empty(self):
        self.assertEqual(plan_backfill(self.ALL, self.ALL, retain=8, max_per_run=3), [])

    def test_cap_bounds_and_is_oldest_first(self):
        published = [self.ALL[-1]]           # only the newest is published; 7 missing
        plan = plan_backfill(published, self.ALL, retain=8, max_per_run=3)
        self.assertEqual(len(plan), 3)
        self.assertEqual(plan, self.ALL[:3])   # the 3 OLDEST missing, in order

    def test_window_bounded_does_not_ingest_beyond_retain(self):
        # complete has 8 but retain=4: only cycles inside the newest-4 window are
        # candidates, so an old complete-but-unpublished cycle is NOT ingested.
        published = [self.ALL[-1]]
        plan = plan_backfill(published, self.ALL, retain=4, max_per_run=10)
        window = self.ALL[-4:]               # newest 4
        self.assertTrue(set(plan).issubset(set(window)))
        self.assertNotIn(self.ALL[0], plan)  # oldest is outside the window

    def test_incomplete_newest_not_planned(self):
        # The gate excluded the newest (still disseminating): it's absent from
        # `complete`, so it is never planned even though it's "missing".
        complete = self.ALL[:-1]
        published = self.ALL[:-2]
        plan = plan_backfill(published, complete, retain=8, max_per_run=3)
        self.assertEqual(plan, [self.ALL[-2]])
        self.assertNotIn(self.ALL[-1], plan)


class TestSynopticCyclesBack(unittest.TestCase):
    def test_floors_to_grid_and_counts_back(self):
        now = dt.datetime(2026, 6, 13, 22, 14, 37)
        cycles = synoptic_cycles_back(now, 4)
        self.assertEqual([f"{c:%Y%m%d%H}" for c in cycles],
                         ["2026061318", "2026061312", "2026061306", "2026061300"])

    def test_exact_synoptic_time_is_included(self):
        now = dt.datetime(2026, 6, 13, 18, 0, 0)
        self.assertEqual(f"{synoptic_cycles_back(now, 1)[0]:%Y%m%d%H}", "2026061318")


class TestCompletenessGate(unittest.TestCase):
    def test_cycle_requests_terminal_steps_per_hour(self):
        reqs00 = cycle_requests(SPEC, C("2026061300"))
        steps00 = {(r["stream"], r["type"]): r["step"] for r in reqs00}
        self.assertEqual(steps00[("enfo", "pf")], 360)   # perturbed terminal 00/12Z
        self.assertEqual(steps00[("oper", "fc")], 240)   # control terminal 00/12Z
        reqs06 = cycle_requests(SPEC, C("2026061306"))
        steps06 = {(r["stream"], r["type"]): r["step"] for r in reqs06}
        self.assertEqual(steps06[("enfo", "pf")], 144)   # perturbed terminal 06/18Z
        self.assertEqual(steps06[("oper", "fc")], 90)    # control terminal 06/18Z

    def test_all_present_is_complete(self):
        self.assertTrue(cycle_complete(SPEC, C("2026061300"), lambda cyc, req: True))

    def test_control_missing_refuses_half_disseminated(self):
        # perturbed terminal present, control (oper/fc) terminal NOT -> incomplete
        def present(cyc, req):
            return req["stream"] != "oper"
        self.assertFalse(cycle_complete(SPEC, C("2026061300"), present))

    def test_perturbed_missing_refuses(self):
        def present(cyc, req):
            return req["stream"] != "enfo"
        self.assertFalse(cycle_complete(SPEC, C("2026061300"), present))

    def test_list_complete_cycles_filters_and_sorts(self):
        cands = [C("2026061318"), C("2026061312"), C("2026061306")]
        # 18Z still disseminating -> its terminal absent
        def present(cyc, req):
            return cyc != C("2026061318")
        out = list_complete_cycles(SPEC, cands, present)
        self.assertEqual([f"{c:%Y%m%d%H}" for c in out], ["2026061306", "2026061312"])


class TestMergeManifestMulti(unittest.TestCase):
    def test_multi_upsert_sorted_latest(self):
        prior = manifest_with(["2026061218"])
        m, prune = merge_manifest_multi(prior, SPEC, ["2026061206", "2026061212"], retain=8)
        self.assertEqual(m["models"][0]["cycles"],
                         ["2026061218", "2026061212", "2026061206"])
        self.assertEqual(m["models"][0]["latest"], "2026061218")
        self.assertEqual(prune, [])

    def test_backfilled_old_plus_new_prunes_from_final_set(self):
        prior = manifest_with(["2026061212", "2026061206"])
        # backfill an old gap + a new cycle; retain=3 keeps newest 3, prunes 1
        m, prune = merge_manifest_multi(prior, SPEC, ["2026061200", "2026061218"], retain=3)
        self.assertEqual(m["models"][0]["cycles"],
                         ["2026061218", "2026061212", "2026061206"])
        self.assertEqual(prune, ["ecens/2026061200.json"])  # the just-backfilled-but-overflow old one

    def test_published_cycles_watermark(self):
        self.assertEqual(published_cycles(manifest_with(["a", "b"]), "ecens"), {"a", "b"})
        self.assertEqual(published_cycles(None, "ecens"), set())
        self.assertEqual(published_cycles({"models": "bad"}, "ecens"), set())


class _Recorder:
    """Stub ingest_cycle hook: records calls, optionally raises for given cycles."""
    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def __call__(self, cycle):
        cyc = f"{cycle:%Y%m%d%H}"
        self.calls.append(cyc)
        if cyc in self.fail:
            raise RuntimeError(f"quorum fail {cyc}")
        return {"cycle": cyc}


class TestRunCurrency(unittest.TestCase):
    ALL = ["2026061200", "2026061206", "2026061212", "2026061218"]

    def _complete(self, cycles):
        return lambda lookback: [C(c) for c in cycles]

    def _run(self, published, complete, ingest, **kw):
        with tempfile.TemporaryDirectory() as d:
            summary = run_currency(
                spec=SPEC, out_dir=d,
                list_complete_cycles=self._complete(complete),
                ingest_cycle=ingest,
                prior_manifest=manifest_with(published),
                fetch_prior=lambda: manifest_with(published),
                **kw)
            man_path = os.path.join(d, "manifest.json")
            manifest = json.load(open(man_path)) if os.path.exists(man_path) else None
            prune = (open(os.path.join(d, "prune_keys.txt")).read().split()
                     if os.path.exists(os.path.join(d, "prune_keys.txt")) else None)
            return summary, manifest, prune

    def test_gap_backfills_only_that_cycle(self):
        gap = "2026061206"
        published = [c for c in self.ALL if c != gap]
        rec = _Recorder()
        summary, manifest, _ = self._run(published, self.ALL, rec)
        self.assertEqual(rec.calls, [gap])                 # ONLY the gap ingested
        self.assertEqual(summary["published"], [gap])
        self.assertEqual(manifest["models"][0]["latest"], "2026061218")
        self.assertEqual(set(manifest["models"][0]["cycles"]), set(self.ALL))

    def test_no_gap_publishes_only_new(self):
        published = self.ALL[:-1]
        rec = _Recorder()
        summary, manifest, _ = self._run(published, self.ALL, rec)
        self.assertEqual(rec.calls, [self.ALL[-1]])
        self.assertEqual(manifest["models"][0]["latest"], self.ALL[-1])

    def test_nothing_missing_writes_no_manifest(self):
        rec = _Recorder()
        summary, manifest, _ = self._run(self.ALL, self.ALL, rec)
        self.assertTrue(summary["skipped"])
        self.assertEqual(rec.calls, [])
        self.assertIsNone(manifest)            # no manifest written -> workflow leaves R2 alone

    def test_cap_bounds_ingest(self):
        published = [self.ALL[-1]]
        rec = _Recorder()
        summary, manifest, _ = self._run(published, self.ALL, rec, max_per_run=2)
        self.assertEqual(len(rec.calls), 2)
        self.assertEqual(rec.calls, self.ALL[:2])          # oldest 2

    def test_all_fail_raises(self):
        published = self.ALL[:-1]
        rec = _Recorder(fail={self.ALL[-1]})
        with self.assertRaises(RuntimeError):
            self._run(published, self.ALL, rec)

    def test_partial_fail_publishes_the_good_one(self):
        published = self.ALL[:-2]              # two missing: ALL[-2], ALL[-1]
        rec = _Recorder(fail={self.ALL[-2]})   # the older of the two fails
        summary, manifest, _ = self._run(published, self.ALL, rec, max_per_run=3)
        self.assertEqual(summary["published"], [self.ALL[-1]])
        self.assertEqual(summary["failed"], [self.ALL[-2]])
        self.assertIn(self.ALL[-1], manifest["models"][0]["cycles"])
        self.assertNotIn(self.ALL[-2], manifest["models"][0]["cycles"])  # stays missing, retries

    def test_hard_manifest_read_failure_aborts(self):
        def boom():
            raise RuntimeError("CDN 500")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):
                run_currency(spec=SPEC, out_dir=d,
                             list_complete_cycles=self._complete(self.ALL),
                             ingest_cycle=_Recorder(),
                             prior_manifest=None, fetch_prior=boom)


if __name__ == "__main__":
    unittest.main()
