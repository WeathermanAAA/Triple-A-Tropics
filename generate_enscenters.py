#!/usr/bin/env python3
"""Thin entrypoint shim for the Actions cron (``update-enscenters.yml``).

The Ensemble Cyclone Centers builder lives in the ``enscenters`` package - one
self-contained, model-agnostic module (ingest -> detect -> R2 JSON). This shim
preserves the cron's ``python generate_enscenters.py`` call; all logic is in
``enscenters.cli``. Run from the repo root so the default --out-dir resolves to
``./models/enscenters`` (the workflow then syncs it to R2).
"""
import sys

from enscenters.cli import main

if __name__ == "__main__":
    sys.exit(main())
