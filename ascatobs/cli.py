"""ascatobs.cli - entry point for the ASCAT ingest/build (run by CI cron).

  python generate_ascat.py --out-dir ./ascat_build                 # incremental
  python generate_ascat.py --out-dir ./ascat_build --window-hours 48
  python generate_ascat.py --out-dir ./ascat_build --backfill-hours 96

Kill switch: ASCAT_ENABLED=0 (or --disabled) exits 0 without touching R2 - the
workflow's R2 sync then has nothing to push, so the last-known-good R2 state stays
live. Needs KNMI_API_KEY in the environment (never hardcode/commit it); without it
the build is a guarded no-op (last-known-good stays live).
"""
from __future__ import annotations

import argparse
import os
import sys

from .build import build as run_build


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ascatobs")
    p.add_argument("--out-dir", default="./ascat_build")
    p.add_argument("--window-hours", type=int, default=36,
                   help="display window; passes older than this are pruned")
    p.add_argument("--backfill-hours", type=int, default=None,
                   help="widen the INGEST reach for a manual catch-up (>= window)")
    p.add_argument("--sensors", default="metop-b,metop-c")
    p.add_argument("--stride", type=int, default=2,
                   help="WVC decimation (every Nth row+cell; 2 ~= 25 km)")
    p.add_argument("--max-new", type=int, default=240,
                   help="cap orbits downloaded per run")
    p.add_argument("--manifest-url",
                   default="https://cdn.triple-a-tropics.com/ascat/manifest.json",
                   help="prior manifest to MERGE into (the growing union)")
    p.add_argument("--disabled", action="store_true")
    args = p.parse_args(argv)

    enabled = (os.environ.get("ASCAT_ENABLED", "1") or "1").lower() \
        not in ("0", "false", "no")
    if args.disabled or not enabled:
        print("ascat: disabled (kill switch) - no write")
        return 0

    sensors = tuple(s.strip().lower() for s in args.sensors.split(",") if s.strip())
    os.makedirs(args.out_dir, exist_ok=True)
    try:
        run_build(args.out_dir, window_hours=args.window_hours,
                  backfill_hours=args.backfill_hours, sensors=sensors,
                  stride=args.stride, max_new_per_run=args.max_new,
                  prior_manifest_url=(args.manifest_url or None))
    except Exception as e:                           # noqa: BLE001
        # Never leave a half-written tree that the sync would push: fail loud but
        # non-destructively (the workflow guards the sync on the exit code + the
        # presence of manifest.json).
        print(f"ascat: build FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
