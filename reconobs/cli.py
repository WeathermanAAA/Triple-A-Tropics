"""reconobs.cli - entry point for the recon ingest/build (run by CI cron).

  python generate_recon.py --out-dir ./recon_build               # incremental
  python generate_recon.py --out-dir ./recon_build --window-days 7
  python generate_recon.py --out-dir ./recon_build --backfill-year 2024
  python generate_recon.py --out-dir ./recon_build --basins AL

Kill switch: RECON_ENABLED=0 (or --disabled) exits 0 without touching R2 -
the workflow's R2 sync then has nothing to push, so the last-known-good R2
state stays live.
"""
from __future__ import annotations

import argparse
import os
import sys

from .build import build as run_build


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="reconobs")
    p.add_argument("--out-dir", default="./recon_build")
    p.add_argument("--window-days", type=int, default=4)
    p.add_argument("--backfill-year", type=int, default=None)
    p.add_argument("--backfill-month", type=int, default=None,
                   help="1-12; sub-chunk a busy backfill year by month")
    p.add_argument("--basins", default="AL,EP")
    p.add_argument("--stagger", type=float, default=0.0,
                   help="seconds between archive fetches (backfill politeness)")
    p.add_argument("--manifest-url",
                   default="https://cdn.triple-a-tropics.com/recon/manifest.json",
                   help="prior manifest to MERGE into (the growing union)")
    p.add_argument("--disabled", action="store_true")
    args = p.parse_args(argv)

    enabled = (os.environ.get("RECON_ENABLED", "1") or "1").lower() \
        not in ("0", "false", "no")
    if args.disabled or not enabled:
        print("recon: disabled (kill switch) - no write")
        return 0

    basins = tuple(b.strip().upper() for b in args.basins.split(",")
                   if b.strip())
    os.makedirs(args.out_dir, exist_ok=True)
    try:
        run_build(args.out_dir, window_days=args.window_days,
                  backfill_year=args.backfill_year,
                  backfill_month=args.backfill_month, basins=basins,
                  stagger_s=args.stagger,
                  prior_manifest_url=(args.manifest_url or None))
    except Exception as e:                       # noqa: BLE001
        # Never leave a half-written tree that the sync would push: fail loud
        # but non-destructively (the workflow guards the sync on exit code).
        print(f"recon: build FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
