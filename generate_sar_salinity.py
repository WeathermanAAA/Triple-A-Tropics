#!/usr/bin/env python3
"""Thin shim: publish the SMAP SSS reliability grid for the SAR wind overlay.

See sarobs.salinity_cli. 8-day cadence, watermark-gated, additive to /obs/sar/.
"""
from sarobs.salinity_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
