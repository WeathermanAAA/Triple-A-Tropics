#!/usr/bin/env python3
"""Thin shim: run the recon ingest/build. See reconobs.cli for options."""
from reconobs.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
