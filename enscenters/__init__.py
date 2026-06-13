"""
Ensemble Cyclone Centers - multi-model TC ensemble center platform (Stage 1).

A self-contained, model-agnostic pipeline: ingest ensemble MSLP, detect
closed-low cyclone centers per member/step, and publish a model-agnostic JSON to
R2 that a hand-rolled /models/ viewer animates. Stage 1 ships ECMWF ENS; the
registry (:mod:`enscenters.registry`) is built so AIFS-ENS, GEFS, GDM-FNV3,
GDM-GenCast and a super-ensemble slot in as data, not code.

See ``ENSEMBLE_DESIGN.md`` for the schema, registry, R2 layout and roadmap.
"""
from .registry import (  # noqa: F401
    DEFAULT_MODEL,
    PRESSURE_BINS,
    EnsModelSpec,
    DetectParams,
    get_spec,
    models_meta,
    pressure_bins_json,
)
from .detect import ah_vmax_kt, detect_centers  # noqa: F401

__version__ = "0.1.0"
