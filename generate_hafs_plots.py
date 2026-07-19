#!/usr/bin/env python3
"""Thin entrypoint shim for the Actions cron (``update-hafs.yml``).

The HAFS render code now lives in the installable ``hafs_render`` package - ONE
source of truth shared by this cron and the tat-satellite-render box render
worker. This shim preserves the cron's ``python generate_hafs_plots.py`` call;
all logic is in ``hafs_render.generate_hafs_plots``. Run from the repo root so
the default --out-dir resolves to ``./models/hafs`` (the workflow then syncs it
to R2). The package is installed in CI via ``pip install ./hafs_render``.
"""
import sys

from hafs_render.generate_hafs_plots import main

if __name__ == "__main__":
    sys.exit(main())
