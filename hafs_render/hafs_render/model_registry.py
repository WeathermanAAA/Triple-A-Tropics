#!/usr/bin/env python3
"""Typed MODEL registry for ``/models/`` - the ONE source of model truth.

``hafs_registry`` answers "what does this PRODUCT look like". This module answers
"what IS this MODEL, and what is it therefore allowed to render". The two are
deliberately separate: a product is a rendering recipe, a model is a physical
claim about how the atmosphere was simulated, and the *intersection* of the two
is what decides whether a given frame is meaningful at all.

Two model attributes carry essentially all of that weight:

``convection_explicit`` (bool)
    True when the model resolves deep convection EXPLICITLY (convection-
    permitting; roughly dx <= 4 km, no deep-convective parameterisation on the
    grid being rendered). False when deep convection is PARAMETERISED - the
    model never produces a convective updraft, only a sub-grid tendency.

    This is not a stylistic preference, it is a correctness gate. A "composite
    reflectivity" field from a parameterised-convection model is not a weak
    signal, it is a *category error*: the hydrometeor field it would be computed
    from is a diagnostic of the microphysics scheme applied to grid-scale
    saturation, and the convection that would actually produce the echo was
    removed from the grid by the cumulus scheme. The same holds for simulated
    89 GHz microwave, whose entire signal is ice scattering by convective cores.
    Rendering either one anyway produces a plausible-looking image that means
    nothing, which is worse than rendering nothing.

    So the gate is STRUCTURAL, not advisory (see ``hafs_registry.product_allowed``
    / ``assert_renderable``): such a pair is dropped at render-job planning, is
    refused with an exception if some caller reaches the renderer anyway, never
    reaches the manifest, and is therefore never offered by the frontend.

``ai_paradigm`` (enum)
    How the forecast was produced. ``AIParadigm.PHYSICS`` is a conventional NWP
    integration; every other value is a machine-learning emulator of some kind.
    Two consequences ride on it:

      * the frontend shows an AI BADGE for any non-PHYSICS model, because a user
        comparing fields across 50 models must be able to see at a glance which
        ones are learned emulators; and
      * the INTENSITY header statistic is SUPPRESSED for non-PHYSICS models.
        Current-generation AI models are trained on reanalysis at ~0.25 deg and
        systematically under-deepen tropical cyclones; their VMAX/MSLP extrema
        are artifacts of resolution and of the training loss (which rewards
        smooth, blur-toward-the-mean fields), not forecasts of intensity. The
        track from an AI model is often excellent and the intensity is not
        meaningful, so we show the field and withhold the number.

Scope today: the render platform is HAFS-only, so ``MODELS`` carries hafsa /
hafsb. The schema is deliberately model-neutral - the roadmap item
``model-scaling-50`` scales this table to roughly 50 models, and the guidance
layer (ATCF a-deck aids) attaches to the SAME records through ``atcf_techs``, so
one model id links its rendered fields and its deck guidance on one storm-keyed
page. Nothing here imports the product registry (the dependency runs
hafs_registry -> model_registry, never back), and nothing here imports numpy or
matplotlib, so this module stays cheap to import from manifest/CLI paths.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class AIParadigm(Enum):
    """How a model's forecast is produced.

    ``PHYSICS`` is the only value that is NOT a machine-learning emulator; the
    frontend badge and the intensity-statistic suppression key off ``is_ai``
    rather than off any individual member, so adding a new paradigm does not
    require touching either consumer.
    """

    #: Conventional NWP: discretised primitive equations integrated forward.
    PHYSICS = "physics"
    #: Deterministic learned emulator trained on reanalysis (GraphCast, Pangu,
    #: FourCastNet, AIFS deterministic, ...). One trajectory per run.
    DETERMINISTIC_AI = "deterministic_ai"
    #: Generative/diffusion emulator producing a sampled ensemble by design
    #: (GenCast, AIFS-CRPS, ...). Members are draws, not perturbed physics runs.
    DIFFUSION_AI = "diffusion_ai"
    #: Learned components inside a physics core, or a physics core driving a
    #: learned parameterisation (NeuralGCM-style). Neither purely learned nor
    #: purely physical.
    HYBRID_AI = "hybrid_ai"

    @property
    def is_ai(self) -> bool:
        """True for every learned-emulator paradigm; False only for PHYSICS."""
        return self is not AIParadigm.PHYSICS


@dataclass(frozen=True)
class ModelSpec:
    """One model's identity + the two structural claims that gate its products.

    ``slug`` is the R2 path segment and manifest key; ``label`` is what the
    frontend prints. Everything else is a claim about the simulation that some
    consumer is entitled to act on.
    """

    # --- identity (manifest + frontend toggle + R2 path segment) ---
    slug: str
    label: str

    # --- the two structural attributes (see the module docstring) ---
    convection_explicit: bool
    ai_paradigm: AIParadigm

    # --- provenance / display ---
    #: Producing centre, shown in the model tooltip ("NOAA/EMC", "ECMWF", ...).
    center: str = ""
    #: Nominal horizontal grid spacing IN KILOMETERS of the grid being rendered.
    #: Informational: it explains the convection flag rather than deciding it
    #: (a 3 km grid that still runs a cumulus scheme is NOT convection-explicit).
    grid_km: Optional[float] = None
    #: True when the model runs a moving, storm-following inner nest. The
    #: ensemble-mean policy denies the mean on ANY field from such a nest (the
    #: grids do not share coordinates between members, so a cell-wise mean is
    #: not a well-defined operation) - see ``hafs_registry.ensemble_mean_policy``.
    storm_following_nest: bool = False

    # --- guidance-layer link (Phase 2) ---
    #: ATCF ``TECH`` ids in the a-deck that correspond to THIS model, so the
    #: field viewer and the deck guidance resolve to one model record on one
    #: storm-keyed page. Empty when the model publishes no aid we ingest.
    atcf_techs: tuple = ()

    # --- ensemble identity ---
    #: True when this record IS an ensemble (its frames are member-derived).
    #: A deterministic model can still be denied the mean - the two are
    #: independent - but only an ensemble can ever be *offered* one.
    is_ensemble: bool = False
    #: Member count for an ensemble, else None.
    n_members: Optional[int] = None

    @property
    def is_ai(self) -> bool:
        """Convenience mirror of ``ai_paradigm.is_ai`` (badge + stat gating)."""
        return self.ai_paradigm.is_ai

    @property
    def show_intensity_stat(self) -> bool:
        """Whether the header may print a VMAX/MSLP intensity statistic.

        False for every AI paradigm: current learned emulators under-deepen TCs
        as a structural consequence of training resolution and loss, so their
        intensity extrema are not forecasts. The field still renders; only the
        number is withheld.
        """
        return not self.is_ai

    def model_meta(self) -> dict:
        """The manifest/frontend dict shape for one model.

        ``{slug,label}`` is the pre-existing shape and stays FIRST so an older
        frontend reading only those two keys is unaffected; the rest is additive.
        """
        return {
            "slug": self.slug,
            "label": self.label,
            "convection_explicit": self.convection_explicit,
            "ai_paradigm": self.ai_paradigm.value,
            "is_ai": self.is_ai,
            "show_intensity_stat": self.show_intensity_stat,
            "center": self.center,
            "grid_km": self.grid_km,
            "storm_following_nest": self.storm_following_nest,
            "is_ensemble": self.is_ensemble,
            "n_members": self.n_members,
            "atcf_techs": list(self.atcf_techs),
        }


# ---------------------------------------------------------------------------
# The model table. HAFS-A / HAFS-B are the only models the render platform wires
# today. Both are convection-permitting on the 2 km storm nest AND on the ~6 km
# parent (HAFS runs no deep-convective parameterisation on either domain - the
# parent is inside the "grey zone" but is still explicit), so the reflectivity
# and simulated-89 GHz products are legitimate on both, which is why they render
# on both today. Both are physics models, so both keep their intensity stat.
# ---------------------------------------------------------------------------
_MODELS = (
    ModelSpec(
        slug="hafsa", label="HAFS-A",
        convection_explicit=True, ai_paradigm=AIParadigm.PHYSICS,
        center="NOAA/EMC", grid_km=2.0, storm_following_nest=True,
        # HFSA is the live a-deck id (HAFA is a real id in nhc_techlist.dat with
        # zero live occurrences - do NOT key on it; see the guidance ingest).
        atcf_techs=("HFSA",),
    ),
    ModelSpec(
        slug="hafsb", label="HAFS-B",
        convection_explicit=True, ai_paradigm=AIParadigm.PHYSICS,
        center="NOAA/EMC", grid_km=2.0, storm_following_nest=True,
        atcf_techs=("HFSB",),
    ),
)

#: slug -> ModelSpec, insertion-ordered.
MODELS = {m.slug: m for m in _MODELS}


# ---------------------------------------------------------------------------
# Accessors (the public surface other modules use).
# ---------------------------------------------------------------------------
def get_model(slug: str) -> ModelSpec:
    """The ModelSpec for a slug; raises KeyError if unknown."""
    return MODELS[slug]


def has_model(slug: str) -> bool:
    return slug in MODELS


def ordered_models() -> list:
    """Models in declaration order (the frontend toggle order)."""
    return list(MODELS.values())


def default_order() -> list:
    """Model slugs in declaration order."""
    return [m.slug for m in _MODELS]


def models_dict() -> dict:
    """``{slug: {...meta}}`` in order - the generator's MODELS table."""
    return {m.slug: m.model_meta() for m in ordered_models()}


def model_labels() -> dict:
    """``{slug: label}`` - the legacy MODEL_LABEL mapping, derived."""
    return {m.slug: m.label for m in ordered_models()}


def tech_to_model() -> dict:
    """``{ATCF TECH id: model slug}`` for every model that publishes an aid.

    The guidance layer uses this to resolve an a-deck row back to the SAME model
    record whose fields are rendered, so one page can show both.
    """
    return {t: m.slug for m in ordered_models() for t in m.atcf_techs}
