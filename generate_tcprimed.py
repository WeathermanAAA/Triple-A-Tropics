#!/usr/bin/env python3
"""Thin shim: run the observed passive-MW build. See tcprimed.cli for options."""
from tcprimed.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
