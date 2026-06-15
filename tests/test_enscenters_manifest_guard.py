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

    def test_suspected_listing_failure_aborts(self):
        # R2 listing came back EMPTY but live has models -> a bad listing, not a
        # genuinely-empty bucket -> refuse to publish (would clobber).
        live = manifest(entry("ecens", ["2026061312"]), entry("gefs", ["2026061312"]))
        out, ok, reason = guard.reconcile({"models": []}, live, "ok", r2_present={})
        self.assertFalse(ok)
        self.assertIsNone(out)
        self.assertIn("listing", reason.lower())

    def test_absent_live_first_run_publishes_new(self):
        new = manifest(entry("ecens", ["2026061312"]))
        out, ok, _ = guard.reconcile(new, {}, "absent")
        self.assertTrue(ok)
        self.assertEqual(slugs_of(out), ["ecens"])

    def test_nothing_anywhere_aborts(self):
        out, ok, reason = guard.reconcile({"models": []}, {}, "ok", r2_present={})
        self.assertFalse(ok)
        self.assertIsNone(out)


class TestReconcileFromR2(unittest.TestCase):
    """The real path: the manifest is DERIVED from the R2 object listing
    (``r2_present``), not the (possibly stale) live manifest entry."""

    def test_nonrunning_model_advances_to_newest_on_r2(self):
        # THE prod bug: the live manifest is frozen at ecens@Jun-12 while newer
        # ecens cycles already exist on R2. A gefs run (which does not touch ecens)
        # must re-point ecens at the newest cycle present on R2.
        live = manifest(entry("ecens", ["2026061206"]), entry("gefs", ["2026061306"]))
        new = manifest(entry("gefs", ["2026061318"]))   # this run built gefs
        r2 = {"ecens": ["2026061206", "2026061212"],     # ecens has a NEWER object on R2
              "gefs": ["2026061318", "2026061306"]}
        out, ok, reason = guard.reconcile(new, live, "ok", r2_present=r2)
        self.assertTrue(ok, reason)
        self.assertEqual(latest_of(out, "ecens"), "2026061212")   # advanced from R2, not stale Jun-12-06
        self.assertEqual(latest_of(out, "gefs"), "2026061318")

    def test_latest_is_newest_object_on_r2(self):
        live = manifest(entry("ecens", ["2026061312"]))
        new = manifest(entry("ecens", ["2026061312"]))
        r2 = {"ecens": ["2026061300", "2026061306", "2026061312", "2026061318"]}
        out, ok, _ = guard.reconcile(new, live, "ok", r2_present=r2)
        self.assertTrue(ok)
        self.assertEqual(latest_of(out, "ecens"), "2026061318")   # newest object wins

    def test_model_only_on_r2_is_resurrected(self):
        # gefs has objects on R2 but is absent from the live manifest (dropped) and
        # isn't this run's model -> it must reappear, labelled from the registry.
        live = manifest(entry("ecens", ["2026061312"]))
        new = manifest(entry("ecens", ["2026061312"]))
        r2 = {"ecens": ["2026061312"], "gefs": ["2026061312"]}
        out, ok, _ = guard.reconcile(new, live, "ok", r2_present=r2)
        self.assertTrue(ok)
        self.assertIn("gefs", slugs_of(out))
        gefs = [m for m in out["models"] if m["slug"] == "gefs"][0]
        self.assertEqual(gefs["label"], "GEFS")

    def test_new_cycle_folded_in_before_listing_catches_up(self):
        # The just-built cycle may not be in the listing yet (eventual consistency);
        # union it in so this run's own publish reflects it immediately.
        live = manifest(entry("gefs", ["2026061312"]))
        new = manifest(entry("gefs", ["2026061318", "2026061312"]))
        r2 = {"gefs": ["2026061312"]}                    # listing lags the sync
        out, ok, _ = guard.reconcile(new, live, "ok", r2_present=r2)
        self.assertTrue(ok)
        self.assertEqual(latest_of(out, "gefs"), "2026061318")

    def test_retain_trims_r2_listing(self):
        live = manifest(entry("ecens", ["2026061318"]))
        new = manifest(entry("ecens", ["2026061400"]))
        r2 = {"ecens": ["2026061318", "2026061312", "2026061306", "2026061300"]}
        out, ok, _ = guard.reconcile(new, live, "ok", retain=2, r2_present=r2)
        self.assertTrue(ok)
        self.assertEqual([m for m in out["models"] if m["slug"] == "ecens"][0]["cycles"],
                         ["2026061400", "2026061318"])   # newest 2 of (R2 + new)

    def test_versions_preserved_from_live_and_new(self):
        live = manifest(entry("ecens", ["2026061312"], versions={"2026061312": "lv"}))
        new = manifest(entry("ecens", ["2026061318"], versions={"2026061318": "nv"}))
        r2 = {"ecens": ["2026061312", "2026061318"]}
        out, ok, _ = guard.reconcile(new, live, "ok", r2_present=r2)
        self.assertTrue(ok)
        cv = [m for m in out["models"] if m["slug"] == "ecens"][0]["cycle_versions"]
        self.assertEqual(cv, {"2026061312": "lv", "2026061318": "nv"})


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

    def test_r2_truth_makes_race_trivially_correct(self):
        # The real path: every run derives from the SHARED R2 listing. Once all three
        # models' objects are on R2, ANY model's run produces the full, current
        # manifest regardless of order or whose build it is -- no lost update.
        r2 = {"ecens": ["2026061318", "2026061312"],
              "ecaie": ["2026061318", "2026061312"],
              "gefs": ["2026061318", "2026061312"]}
        live = manifest(entry("ecens", ["2026061306"]))   # arbitrarily stale
        for slug in ("ecens", "ecaie", "gefs"):
            out, ok, reason = guard.reconcile(manifest(entry(slug, ["2026061318"])),
                                              live, "ok", r2_present=r2)
            self.assertTrue(ok, reason)
            self.assertEqual(set(slugs_of(out)), {"ecens", "ecaie", "gefs"}, slug)
            for m in ("ecens", "ecaie", "gefs"):
                self.assertEqual(latest_of(out, m), "2026061318", f"{slug}->{m}")


class TestTracksVersionsFromR2(unittest.TestCase):
    """tracks_versions is DERIVED from the R2 .tracks.json listing (race-proof),
    not just merged from live+new - so a concurrent sibling publish that drops a
    model's tracks token in the live+new merge is self-healed from R2 reality."""

    def test_tracks_derived_even_when_token_missing(self):
        # live + new carry NO tracks token for ecaie, but R2 HAS its .tracks.json
        new = manifest(entry("ecens", ["2026061418"]), entry("ecaie", ["2026061418"]))
        live = manifest(entry("ecens", ["2026061418"]), entry("ecaie", ["2026061418"]))
        r2 = {"ecens": ["2026061418"], "ecaie": ["2026061418"]}
        r2_tracks = {"ecens": ["2026061418"], "ecaie": ["2026061418"]}
        out, ok, _ = guard.reconcile(new, live, "ok", r2_present=r2, r2_tracks=r2_tracks)
        self.assertTrue(ok)
        for m in out["models"]:
            self.assertIn("2026061418", m.get("tracks_versions", {}),
                          f"{m['slug']} lost its tracks_version despite the R2 file")

    def test_tracks_dropped_when_no_r2_object(self):
        # a stale tracks token in live, but NO .tracks.json on R2 -> dropped
        live = manifest(entry("ecens", ["2026061418"]))
        live["models"][0]["tracks_versions"] = {"2026061418": "stale"}
        new = manifest(entry("ecens", ["2026061418"]))
        out, ok, _ = guard.reconcile(new, live, "ok",
                                     r2_present={"ecens": ["2026061418"]},
                                     r2_tracks={"ecens": []})
        self.assertNotIn("tracks_versions", out["models"][0])

    def test_precise_token_kept_when_available(self):
        new = manifest(entry("ecens", ["2026061418"]))
        new["models"][0]["tracks_versions"] = {"2026061418": "v-precise"}
        out, ok, _ = guard.reconcile(new, {}, "ok",
                                     r2_present={"ecens": ["2026061418"]},
                                     r2_tracks={"ecens": ["2026061418"]})
        self.assertEqual(out["models"][0]["tracks_versions"]["2026061418"], "v-precise")


if __name__ == "__main__":
    unittest.main()
