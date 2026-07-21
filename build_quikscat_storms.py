#!/usr/bin/env python3
"""Thin shim: build the archived QuikSCAT storm-pass winds. See qscatobs.cli.

Manual + resumable (the BYU source archive is static, 1999-2009).
"""
from qscatobs.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
