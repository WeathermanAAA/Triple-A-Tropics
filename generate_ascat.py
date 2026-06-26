#!/usr/bin/env python3
"""Thin shim: run the ASCAT ocean-winds ingest/build. See ascatobs.cli for options."""
from ascatobs.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
