"""sarobs.cli - entry point for the SAR winds poller tick / backfill.

  python generate_sar_winds.py --store r2                  # one poller tick
  python generate_sar_winds.py --store local:/tmp/sar      # local test
  python generate_sar_winds.py --store r2 --year 2025 --max-new 40 --sweep
                                                           # season backfill

Kill switch: SAR_ENABLED=0 exits 0 without touching the store.
"""
from __future__ import annotations

import argparse
import os
import sys

from .build import build


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sarobs")
    p.add_argument("--store", default="local:/tmp/tat-sar",
                   help="'r2' or 'local:/path'")
    p.add_argument("--year", type=int, default=None,
                   help="season to poll (default: current year)")
    p.add_argument("--extra-years", default="",
                   help="comma list of additional seasons to sweep")
    p.add_argument("--max-new", type=int, default=6,
                   help="max new passes rendered per tick (backfill "
                        "continues next tick)")
    p.add_argument("--sweep", action="store_true",
                   help="force a full re-listing of every in-scope storm")
    p.add_argument("--geo-dir", default=".",
                   help="dir holding the vendored Natural Earth GeoJSON")
    args = p.parse_args(argv)

    if (os.environ.get("SAR_ENABLED", "1") or "1").lower() in \
            ("0", "false", "no"):
        print("sar: disabled (kill switch) - no write")
        return 0
    extra = tuple(int(y) for y in args.extra_years.split(",") if y.strip())
    try:
        build(args.store, year=args.year, max_new=args.max_new,
              geo_dir=args.geo_dir, force_sweep=args.sweep,
              extra_years=extra)
    except Exception as e:                       # noqa: BLE001
        print(f"sar: tick FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
