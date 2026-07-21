"""sarobs.salinity_cli - publish the SMAP SSS reliability grid (8-day cadence).

  python generate_sar_salinity.py --store r2          # one poller tick
  python generate_sar_salinity.py --store local:/tmp/s

Watermark-gated on the source DOY (an unchanged 8-day file is a no-op), so a
tight box loop is cheap. Kill switch: SAR_ENABLED=0 exits 0 without writing.
"""
from __future__ import annotations

import argparse
import os
import sys

from .salinity import build
from .store import make_store


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sarobs-salinity")
    p.add_argument("--store", default="local:/tmp/tat-sar")
    args = p.parse_args(argv)
    if (os.environ.get("SAR_ENABLED", "1") or "1").lower() in \
            ("0", "false", "no"):
        print("salinity: disabled (kill switch) - no write")
        return 0
    try:
        build(make_store(args.store))
    except Exception as e:                       # noqa: BLE001
        print(f"salinity: tick FAILED: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
