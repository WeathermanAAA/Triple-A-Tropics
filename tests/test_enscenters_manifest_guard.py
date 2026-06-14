"""
Tests for the atomic, monotonic shared-manifest reconcile
(``scripts/enscenters_manifest_guard.py``).

The three ensemble models (ecens, ecaie, gefs) each publish to the SAME
``models/enscenters/manifest.json`` from their OWN workflow. These tests pin the
two invariants that make a clobber impossible -- NEVER drop a live model, NEVER
regress a model's latest -- and simulate the three workflows racing on the shared
manifest, asserting all three survive at their newest cycles.

No network: ``reconcile`` is a pure function over manifest dicts. Run:
    python -m unittest discover tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import enscenters_manifest_guard as guard  # noqa: E402


def entry(slug, cycles, label=None, versions=None):
    cycles = list(cycles)
    return {
        "slug": slug,
        "label": label or slug.upper(),
        "cycles": cycles,
        "latest": max(cycles) if cycles else None,
        "cycle_versions": dict(versions or {}),
    }


def manifest(*entries, default="ecens"):
    return {"schema_version": 1, "default_model": default,
            "models": [e for e in entries]}


def latest_of(man, slug):
    for m in man["models"]:
        if m["slug"] == slug:
            return m["latest"]
    return None


def slugs_of(man):
    return [m["slug"] for m in man["models"]]


class TestReconcileBasics(unittest.TestCase):
    def test_sibling_preserved_when_new_is_thin(self):
        # AIFS run publishes ONLY its own entry; ecens + gefs must survive.
        live = manifest(
            entry("ecens", ["2026061312", "2026061306"]),
            entry("ecaie", ["2026061306"]),
            entry("gefs", ["2026061312"]),
        )
        new = manifest(entry("ecaie", ["2026061312", "2026061306"]))
        out, ok, reason = guard.reconcile(new, live, "ok")
        self.assertTrue(ok, reason)
        self.assertEqual(set(slugs_of(out)), {"ecens", "ecaie", "gefs"})
        self.assertEqual(latest_of(out, "ecens"), "2026061312")   # preserved
        self.assertEqual(latest_of(out, "gefs"), "2026061312")    # preserved
        self.assertEqual(latest_of(out, "ecaie"), "2026061312")   # advanced

    def test_own_latest_never_regresses(self):
        # THE prod bug: ecens's builder had an empty watermark (CDN 403) and
        # rebuilt OLD cycles -> a thin, REGRESSED new entry. Reconcile against the
        # authoritative live must keep ecens at its newer latest.
        live = manifest(
            entry("ecens", ["2026061312", "2026061306", "2026061300"]),
            entry("ecaie", ["2026061312"]),
            entry("gefs", ["2026061312"]),
        )
        new = manifest(entry("ecens", ["2026061218", "2026061212", "2026061206"]))
        out, ok, reason = guard.reconcile(new, live, "ok")
        self.assertTrue(ok, reason)
        self.assertEqual(latest_of(out, "ecens"), "2026061312")   # NOT regressed
        self.assertEqual(set(slugs_of(out)), {"ecens", "ecaie", "gefs"})

    def test_advance_moves_latest_forward_and_prunes(self):
        live = manifest(entry("ecens", ["2026061312", "2026061306"]))
        new = manifest(entry("ecens", ["2026061318", "2026061312", "2026061306"]))
        out, ok, _ = guard.reconcile(new, live, "ok", retain=2)
        self.assertTrue(ok)
        self.assertEqual(latest_of(out, "ecens"), "2026061318")
        self.assertEqual([m for m in out["models"] if m["slug"] == "ecens"][0]["cycles"],
                         ["2026061318", "2026061312"])             # trimmed to retain=2

    def test_cycle_versions_union_new_wins(self):
        live = manifest(entry("gefs", ["2026061312"], versions={"2026061312": "old"}))
        new = manifest(entry("gefs", ["2026061318", "2026061312"],
                             versions={"2026061318": "v318", "2026061312": "new"}))
        out, ok, _ = guard.reconcile(new, live, "ok")
        self.assertTrue(ok)
        gv = [m for m in out["models"] if m["slug"] == "gefs"][0]["cycle_versions"]
        self.assertEqual(gv["2026061318"], "v318")
        self.assertEqual(gv["2026061312"], "new")                 # this run's version wins

    def test_default_model_stays_ecens_when_present(self):
        live = manifest(entry("ecaie", ["2026061312"]), default="ecaie")
        new = manifest(entry("ecens", ["2026061312"]))
        out, ok, _ = guard.reconcile(new, live, "ok")
        self.assertTrue(ok)
        self.assertEqual(out["default_model"], "ecens")

    def test_registry_order_in_output(self):
        live = manifest(entry("gefs", ["2026061312"]))
        new = manifest(entry("ecaie", ["2026061312"]), entry("ecens", ["2026061312"]))
        out, ok, _ = guard.reconcile(new, live, "ok")
        self.assertTrue(ok)
        self.assertEqual(slugs_of(out), ["ecens", "ecaie", "gefs"])


class TestReconcileAbort(unittest.TestCase):
    def test_read_failed_aborts(self):
        new = manifest(entry("ecens", ["2026061312"]))
        out, ok, reason = guard.reconcile(new, {}, "failed")
        self.assertFalse(ok)
        self.assertIsNone(out)
        self.assertIn("failed", reason.lower())

    def test_empty_new_aborts(self):
        live = manifest(entry("ecens", ["2026061312"]))
        out, ok, reason = guard.reconcile({"models": []}, live, "ok")
        self.assertFalse(ok)
        self.assertIsNone(out)

    def test_absent_live_first_run_publishes_new(self):
        new = manifest(entry("ecens", ["2026061312"]))
        out, ok, _ = guard.reconcile(new, {}, "absent")
        self.assertTrue(ok)
        self.assertEqual(slugs_of(out), ["ecens"])

    def test_refuse_to_write_gate_trips_on_drop(self):
        # Defensive backstop: if a bug ever made the merge omit a live model, the
        # refuse-to-write gate must catch it and abort. retain=0 forces every model
        # to be trimmed away, so a live model with cycles ends up dropped -> abort.
        live = manifest(
            entry("ecens", ["2026061312"]),
            entry("gefs", ["2026061312"]),
        )
        new = manifest(entry("ecens", ["2026061312"]))
        out, ok, reason = guard.reconcile(new, live, "ok", retain=0)
        self.assertFalse(ok)
        self.assertIsNone(out)
        self.assertIn("DROP", reason)


class TestThreeModelRace(unittest.TestCase):
    """Simulate the three per-model workflows racing on the shared manifest.

    The shared manifest is a single object each workflow re-reads, reconciles its
    own (possibly thin/stale) build against, and writes back -- exactly the
    re-read -> merge-by-model -> write the workflows perform. Whatever the order,
    all three must end present at their newest cycles, none dropped, none
    regressed.
    """
    START = manifest(
        entry("ecens", ["2026061312", "2026061306", "2026061300"]),
        entry("ecaie", ["2026061312", "2026061306"]),
        entry("gefs", ["2026061312", "2026061306"]),
    )
    # Each model's freshly-built (THIN -- only its own entry) manifest this run.
    # ecens is the adversarial one: its build REGRESSED (empty watermark rebuilt
    # old cycles), so its thin new lists only Jun-12 cycles.
    BUILDS = {
        "ecens": manifest(entry("ecens", ["2026061218", "2026061212", "2026061206"])),
        "ecaie": manifest(entry("ecaie", ["2026061318", "2026061312", "2026061306"])),
        "gefs": manifest(entry("gefs", ["2026061318", "2026061312", "2026061306"])),
    }

    def _run_sequence(self, order):
        shared = self.START
        for slug in order:
            out, ok, reason = guard.reconcile(self.BUILDS[slug], shared, "ok")
            self.assertTrue(ok, f"{slug}: {reason}")
            shared = out          # committed back to the shared manifest
        return shared

    def test_all_orderings_keep_three_models_at_newest(self):
        import itertools
        for order in itertools.permutations(["ecens", "ecaie", "gefs"]):
            final = self._run_sequence(order)
            self.assertEqual(set(slugs_of(final)), {"ecens", "ecaie", "gefs"},
                             f"order={order}")
            # ecens NEVER regresses below its live latest, despite a regressed build
            self.assertEqual(latest_of(final, "ecens"), "2026061312", f"order={order}")
            # ecaie + gefs advance to their newest
            self.assertEqual(latest_of(final, "ecaie"), "2026061318", f"order={order}")
            self.assertEqual(latest_of(final, "gefs"), "2026061318", f"order={order}")

    def test_lost_update_self_heals_next_run(self):
        # Worst case the re-read-before-write CAS can still miss: two workflows
        # both read the SAME live, both write, and the LATER write was reconciled
        # against the now-stale snapshot -> it transiently regresses a sibling.
        # Prove the regressed model's NEXT run heals it (the steady-state guarantee).
        v0 = self.START
        # ecens advances to 18 and writes v1.
        v1, ok, _ = guard.reconcile(
            manifest(entry("ecens", ["2026061318", "2026061312", "2026061306"])), v0, "ok")
        self.assertTrue(ok)
        self.assertEqual(latest_of(v1, "ecens"), "2026061318")
        # gefs read v0 (stale, before v1) and now writes -> ecens seen as 12 here.
        v2, ok, _ = guard.reconcile(
            manifest(entry("gefs", ["2026061318", "2026061312", "2026061306"])), v0, "ok")
        self.assertTrue(ok)
        # v2 lost ecens's 18 advance (transient regression) -- documents the race:
        self.assertEqual(latest_of(v2, "ecens"), "2026061312")
        self.assertEqual(latest_of(v2, "gefs"), "2026061318")
        # ecens's NEXT run re-reads v2, its build is at 18, reconcile heals it:
        v3, ok, _ = guard.reconcile(
            manifest(entry("ecens", ["2026061318", "2026061312", "2026061306"])), v2, "ok")
        self.assertTrue(ok)
        self.assertEqual(latest_of(v3, "ecens"), "2026061318")   # healed
        self.assertEqual(latest_of(v3, "gefs"), "2026061318")    # preserved
        self.assertEqual(set(slugs_of(v3)), {"ecens", "ecaie", "gefs"})


if __name__ == "__main__":
    unittest.main()
