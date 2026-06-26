"""ascatobs - ASCAT scatterometer ocean-surface winds (KNMI Open Data).

A self-contained, isolated ingest for EUMETSAT/OSI SAF ASCAT-B + ASCAT-C 12.5 km
COASTAL Level-2 ocean-surface winds. It pulls full-orbit NetCDF from the KNMI Data
Platform Open Data API, decodes + quality-masks + decimates each orbit into a
compact per-pass JSON (wind-vector cells = lat/lon + speed-kt + barb FROM-dir),
tags passes with the active storms they overfly, and writes a per-pass tree to R2
under ``ascat/``. The /ascat/ canvas viewer and the CycloLab per-storm tab both
hydrate from that tree.

Isolated feed: it never reads or writes the track / ACE / climatology pipeline.
Storm association reuses the PUBLISHED ``global_storms.geojson`` feed READ-ONLY
(see ``ascatobs.storms``), exactly like ``tcprimed.storms``.

Mirrors the reconobs build pattern: incremental rolling-window ingest, a growing
manifest union, an idempotent filename-timestamp watermark, a kill switch, and
fail-loud-but-non-destructive error handling so a bad run leaves last-known-good
R2 live.
"""
__version__ = "0.1.0"

SCHEMA_VERSION = 1

# Credit: the product is EUMETSAT/OSI SAF (processed at KNMI), distributed via the
# KNMI Data Platform. "(c) <year> EUMETSAT" is the required attribution.
SOURCE = "EUMETSAT / OSI SAF / KNMI - ASCAT 12.5 km coastal ocean winds"
CREDIT = "EUMETSAT"

# Honest, data-forward caveats shown on the viewer (no fluff):
#  (1) C-band ASCAT underestimates extreme TC-core winds (saturation above
#      ~25 m/s); good for the broad gale/wind field, not peak intensity.
#  (2) rain-flagged cells are removed (KNMI QC).
#  (3) swaths are intermittent (multi-hour gaps between passes over any one storm);
#      this is a near-real-time feed (OSI SAF coastal latency ~3 h, per orbit), so
#      the most recent pass may be a few hours old.
DISCLOSURE = (
    "ASCAT C-band scatterometer ocean-surface winds (EUMETSAT/OSI SAF). C-band "
    "underestimates extreme tropical-cyclone core winds (saturation and rain) - "
    "best for the broad gale-force wind field, not peak intensity. Rain- and "
    "quality-flagged cells are removed. Scatterometer swaths are intermittent "
    "(multi-hour gaps between passes over any one storm); this is a near-real-time "
    "feed, so the most recent pass may be a few hours old."
)

from .build import build  # noqa: E402,F401
