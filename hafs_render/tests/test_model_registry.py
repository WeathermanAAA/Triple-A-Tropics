#!/usr/bin/env python3
"""Phase 0 foundations: the MODEL record and the two policies it drives.

Covers the structural (model x product) convection gate, the AI-paradigm badge /
intensity-statistic suppression, and the ensemble-mean policy + substitute
biconditional. The gate is a CORRECTNESS gate (a reflectivity render off a
parameterised-convection model is a category error, not a weak signal), so these
tests assert that it is genuinely unrenderable at every layer rather than merely
absent from a default list.
"""
import unittest

from hafs_render import hafs_registry as reg
from hafs_render import model_registry as mr
from hafs_render.hafs_registry import MeanSubstitute
from hafs_render.model_registry import AIParadigm, ModelSpec


# Two models that do NOT exist in the shipped registry, used to exercise the
# gate. Registered/unregistered per-test so the real table is never mutated.
COARSE_PHYSICS = ModelSpec(
    slug="_test_coarse", label="Test Coarse", convection_explicit=False,
    ai_paradigm=AIParadigm.PHYSICS, center="Test", grid_km=13.0)
COARSE_AI = ModelSpec(
    slug="_test_ai", label="Test AI", convection_explicit=False,
    ai_paradigm=AIParadigm.DETERMINISTIC_AI, center="Test", grid_km=28.0)
FINE_AI = ModelSpec(
    slug="_test_ai_fine", label="Test AI Fine", convection_explicit=True,
    ai_paradigm=AIParadigm.DIFFUSION_AI, center="Test", grid_km=3.0)


class _WithModels(unittest.TestCase):
    """Registers the synthetic models for the duration of one test."""

    EXTRA = ()

    def setUp(self):
        self._saved = dict(mr.MODELS)
        for m in self.EXTRA:
            mr.MODELS[m.slug] = m

    def tearDown(self):
        mr.MODELS.clear()
        mr.MODELS.update(self._saved)


class TestShippedRegistry(unittest.TestCase):
    """Invariants of the models actually shipped today."""

    def test_hafs_models_are_convection_permitting_physics(self):
        for slug in ("hafsa", "hafsb"):
            m = mr.get_model(slug)
            self.assertTrue(m.convection_explicit, slug)
            self.assertIs(m.ai_paradigm, AIParadigm.PHYSICS, slug)
            self.assertFalse(m.is_ai, slug)
            self.assertTrue(m.show_intensity_stat, slug)

    def test_hafs_renders_every_product(self):
        """The gate must be a no-op for HAFS - it exists for FUTURE models, and
        must not silently drop a product that renders on the live site today."""
        for slug in ("hafsa", "hafsb"):
            self.assertEqual(reg.allowed_products_for_model(slug),
                             reg.default_order(), slug)

    def test_model_meta_keeps_slug_label_first(self):
        """An older frontend reads only {slug,label}; the new keys are additive
        and must not displace those two."""
        meta = mr.get_model("hafsa").model_meta()
        self.assertEqual(list(meta)[:2], ["slug", "label"])
        self.assertEqual(meta["slug"], "hafsa")
        self.assertEqual(meta["label"], "HAFS-A")

    def test_atcf_techs_link_models_to_guidance(self):
        """The field viewer and the deck guidance must resolve to ONE record.
        HFSA/HFSB are the live a-deck ids (HAFA/HAFB are real ids with zero
        live occurrences - keying on them would silently match nothing)."""
        t2m = mr.tech_to_model()
        self.assertEqual(t2m.get("HFSA"), "hafsa")
        self.assertEqual(t2m.get("HFSB"), "hafsb")
        self.assertNotIn("HAFA", t2m)
        self.assertNotIn("HAFB", t2m)


class TestConvectionGate(_WithModels):
    EXTRA = (COARSE_PHYSICS, COARSE_AI, FINE_AI)

    #: The products whose signal IS resolved deep convection.
    GATED = ("refl", "sim_89h")

    def test_gated_products_are_exactly_refl_and_sim_mw(self):
        gated = [s.key for s in reg.ordered_specs()
                 if s.requires_explicit_convection]
        self.assertEqual(sorted(gated), sorted(self.GATED))

    def test_parameterised_model_is_denied_the_gated_products(self):
        for slug in (COARSE_PHYSICS.slug, COARSE_AI.slug):
            allowed = reg.allowed_products_for_model(slug)
            for p in self.GATED:
                self.assertNotIn(p, allowed, f"{slug}/{p}")
                self.assertFalse(reg.product_allowed(slug, p), f"{slug}/{p}")

    def test_convection_permitting_ai_model_keeps_the_gated_products(self):
        """The gate keys on CONVECTION, not on being AI. A convection-permitting
        emulator may render reflectivity; it just may not claim an intensity."""
        allowed = reg.allowed_products_for_model(FINE_AI.slug)
        for p in self.GATED:
            self.assertIn(p, allowed)

    def test_simulated_ir_and_wv_are_not_gated(self):
        """10.3/6.2 um BT is set by grid-scale cloud and upper-tropospheric
        humidity, which a cumulus scheme still produces - smoother, but not a
        category error. Gating them too would be over-application."""
        for p in ("clean_ir", "water_vapor"):
            self.assertTrue(reg.product_allowed(COARSE_PHYSICS.slug, p), p)

    def test_renderer_refuses_an_incompatible_pair(self):
        """Structural, not advisory: a caller that bypasses the planner is
        refused with an exception naming BOTH ids."""
        for p in self.GATED:
            with self.assertRaises(reg.IncompatibleProduct) as cm:
                reg.assert_renderable(COARSE_PHYSICS.slug, p)
            self.assertEqual(cm.exception.model_slug, COARSE_PHYSICS.slug)
            self.assertEqual(cm.exception.product_key, p)
            self.assertIn("parameterises convection", str(cm.exception))

    def test_allowed_pair_does_not_raise(self):
        reg.assert_renderable("hafsa", "refl")
        reg.assert_renderable(COARSE_PHYSICS.slug, "clean_ir")

    def test_unknown_ids_are_denied_not_waved_through(self):
        """A typo'd model or product must never silently render."""
        self.assertFalse(reg.product_allowed("no_such_model", "refl"))
        self.assertFalse(reg.product_allowed("hafsa", "no_such_product"))
        with self.assertRaises(reg.IncompatibleProduct):
            reg.assert_renderable("no_such_model", "refl")


class TestAIParadigm(_WithModels):
    EXTRA = (COARSE_AI, FINE_AI)

    def test_is_ai_is_true_for_every_non_physics_paradigm(self):
        self.assertFalse(AIParadigm.PHYSICS.is_ai)
        for p in (AIParadigm.DETERMINISTIC_AI, AIParadigm.DIFFUSION_AI,
                  AIParadigm.HYBRID_AI):
            self.assertTrue(p.is_ai, p)

    def test_ai_models_suppress_the_intensity_stat(self):
        for slug in (COARSE_AI.slug, FINE_AI.slug):
            self.assertTrue(mr.get_model(slug).is_ai)
            self.assertFalse(mr.get_model(slug).show_intensity_stat)

    def test_meta_exposes_badge_and_stat_flags(self):
        meta = mr.get_model(COARSE_AI.slug).model_meta()
        self.assertTrue(meta["is_ai"])
        self.assertFalse(meta["show_intensity_stat"])
        self.assertEqual(meta["ai_paradigm"], "deterministic_ai")


class TestStripIntensity(unittest.TestCase):
    """The header filter that runs ONLY on the AI path."""

    def test_drops_vmax_and_mslp_keeps_the_product_stat(self):
        self.assertEqual(
            reg.strip_intensity("MAX 62 dBZ   /   MSLP 948.2 mb"),
            "MAX 62 dBZ")
        self.assertEqual(
            reg.strip_intensity("MAX PWAT 71 mm   /   MSLP 948.2 mb"),
            "MAX PWAT 71 mm")

    def test_wind_stat_is_entirely_intensity_so_it_is_replaced(self):
        self.assertEqual(
            reg.strip_intensity("VMAX 118.4 kt   /   MSLP 948.2 mb"),
            reg.INTENSITY_WITHHELD)

    def test_scope_label_survives_the_dropped_segment(self):
        """The honesty suffix rides on the LAST segment, which is usually the
        MSLP one - dropping it must not drop the admission with it."""
        out = reg.strip_intensity(
            "MIN BT -78.4°C   /   MSLP 948.2 mb  (domain-wide)",
            "  (domain-wide)")
        self.assertEqual(out, "MIN BT -78.4°C  (domain-wide)")

    def test_scope_label_is_not_duplicated(self):
        out = reg.strip_intensity("MEAN RH 62%  (domain-wide)",
                                  "  (domain-wide)")
        self.assertEqual(out, "MEAN RH 62%  (domain-wide)")
        self.assertEqual(out.count("(domain-wide)"), 1)

    def test_non_intensity_stat_is_untouched(self):
        for s in ("MAX WIND 84 kt @500", "MAX VORT 210 x10^-5/s @850",
                  "MAX SHEAR 42 kt", "MAX SST 30.4°C"):
            self.assertEqual(reg.strip_intensity(s), s)


class TestEnsembleMeanPolicy(unittest.TestCase):

    def test_substitute_is_named_iff_the_mean_is_denied(self):
        """The biconditional the import-time validator enforces - restated here
        so a failure names the offending spec."""
        for s in reg.ordered_specs():
            na = s.mean_substitute is MeanSubstitute.NOT_APPLICABLE
            self.assertEqual(s.ensemble_mean_allowed, na, s.key)

    def test_spec_denials_match_the_documented_policy(self):
        denied = {s.key for s in reg.ordered_specs()
                  if not s.ensemble_mean_allowed}
        # TC intensity, every simulated sensor, precip maxima, and the sharp
        # displacement-sensitive extremum fields.
        self.assertEqual(denied, {
            "mslp_wind",                                  # TC intensity
            "refl", "clean_ir", "water_vapor", "sim_89h",  # simulated sensors
            "env_precip",                                 # precip maxima
            "vort_wind_850", "vort_wind_500",             # sharp extrema
            "env_pv_200", "env_cape", "env_srh",
        })

    def test_spec_allowances_match_the_documented_policy(self):
        allowed = {s.key for s in reg.ordered_specs() if s.ensemble_mean_allowed}
        self.assertEqual(allowed, {
            "hgt_wind_850", "hgt_wind_700", "hgt_wind_500",  # height/steering
            "env_shear_200_850", "env_shear_500_850",        # environmental shear
            "env_sst", "env_tropt",                          # SST + thermal
            "mslp_pwat", "rh_layer", "env_lhtfl",            # smooth moisture/flux
        })

    def test_simulated_sensors_demand_a_member_not_a_statistic(self):
        """Averaging a radiance across members yields a value no instrument
        could observe, so the substitute must be MEMBER_PICKER."""
        for k in ("refl", "clean_ir", "water_vapor", "sim_89h"):
            self.assertIs(reg.get_spec(k).mean_substitute,
                          MeanSubstitute.MEMBER_PICKER, k)

    def test_extrema_substitute_with_exceedance_probability(self):
        for k in ("env_precip", "env_cape", "env_srh", "env_pv_200",
                  "vort_wind_850", "vort_wind_500"):
            self.assertIs(reg.get_spec(k).mean_substitute,
                          MeanSubstitute.PROBABILITY, k)

    def test_intensity_substitutes_with_member_spaghetti(self):
        self.assertIs(reg.get_spec("mslp_wind").mean_substitute,
                      MeanSubstitute.SPAGHETTI)

    # --- rule (a): the storm-following nest overrides everything -------------
    def test_storm_following_nest_denies_the_mean_for_every_product(self):
        for s in reg.ordered_specs():
            pol = reg.ensemble_mean_policy(s.key, "storm.atm", "hafsa")
            self.assertFalse(pol["allowed"], s.key)
            self.assertIn("storm-following nest", pol["reason"], s.key)

    def test_same_product_is_allowed_on_the_parent(self):
        pol = reg.ensemble_mean_policy("hgt_wind_500", "parent.atm", "hafsa")
        self.assertTrue(pol["allowed"])
        self.assertEqual(pol["substitute"], "not_applicable")
        self.assertIsNone(pol["reason"])

    def test_nest_rule_needs_a_storm_following_model(self):
        """A model with a static nest is not subject to rule (a) - the rule is
        about members' grids not sharing coordinates, not about the name."""
        saved = dict(mr.MODELS)
        try:
            mr.MODELS["_static"] = ModelSpec(
                slug="_static", label="Static", convection_explicit=True,
                ai_paradigm=AIParadigm.PHYSICS, storm_following_nest=False)
            pol = reg.ensemble_mean_policy("hgt_wind_500", "storm.atm", "_static")
            self.assertTrue(pol["allowed"])
        finally:
            mr.MODELS.clear()
            mr.MODELS.update(saved)

    def test_denied_product_reports_its_substitute_on_the_parent(self):
        pol = reg.ensemble_mean_policy("env_precip", "parent.atm", "hafsa")
        self.assertFalse(pol["allowed"])
        self.assertEqual(pol["substitute"], "probability")
        self.assertIsNotNone(pol["reason"])

    def test_unknown_product_is_denied(self):
        pol = reg.ensemble_mean_policy("no_such_product", "parent.atm")
        self.assertFalse(pol["allowed"])
        self.assertIn("unknown product", pol["reason"])

    def test_mslp_marker_suppression_flag_tracks_the_overlay(self):
        """Rule (b): the L marker is dropped in ANY mean render. The helper must
        report True exactly for the products that draw one."""
        for s in reg.ordered_specs():
            self.assertEqual(reg.suppress_mslp_marker_in_mean(s),
                             s.draw_mslp_markers, s.key)


class TestImportTimeValidation(unittest.TestCase):
    """``_validate_specs`` runs at import; these prove it would actually catch
    the mistakes it claims to, rather than being decorative."""

    def test_inconsistent_mean_policy_is_rejected(self):
        bad = reg.get_spec("env_sst")
        broken = type(bad)(**{**bad.__dict__,
                              "ensemble_mean_allowed": True,
                              "mean_substitute": MeanSubstitute.PROBABILITY})
        saved = reg._SPECS
        try:
            reg._SPECS = (broken,)
            with self.assertRaises(ValueError) as cm:
                reg._validate_specs()
            self.assertIn("iff the mean is denied", str(cm.exception))
        finally:
            reg._SPECS = saved

    def test_duplicate_order_is_rejected(self):
        a = reg.get_spec("env_sst")
        b = type(a)(**{**reg.get_spec("env_tropt").__dict__, "order": a.order})
        saved = reg._SPECS
        try:
            reg._SPECS = (a, b)
            with self.assertRaises(ValueError) as cm:
                reg._validate_specs()
            self.assertIn("duplicate order", str(cm.exception))
        finally:
            reg._SPECS = saved

    def test_shipped_registry_validates(self):
        reg._validate_specs()   # must not raise


if __name__ == "__main__":
    unittest.main()
