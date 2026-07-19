#!/usr/bin/env python3
"""Thin shim: run the SAR winds discover/render/publish tick. See sarobs.cli."""
from sarobs.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
