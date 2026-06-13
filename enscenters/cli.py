"""
CLI entrypoint for the Ensemble Cyclone Centers builder.

Run by the scheduled GitHub Actions workflow (``update-enscenters.yml``):

    python generate_enscenters.py --jobs 4

Selects the latest COMPLETE cycle (like HAFS picks the latest complete cycle
rather than polling), ingests + detects, and writes the per-cycle JSON +
manifest into ``./models/enscenters`` for the workflow to sync to R2.

Exit codes:
  0  published a cycle, OR no complete cycle is available yet (nothing to do -
     do NOT fail the workflow or prune live data)
  1  a cycle was found but ingest produced nothing (total failure) - the
     workflow aborts before the R2 prune so the prior cycle stays live
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from typing import List, Optional

from .ingest import make_client, resolve_latest_complete
from .pipeline import DEFAULT_MIN_MEMBERS_FRAC, DEFAULT_RETAIN, R2_PREFIX, build_cycle
from .registry import get_spec


def _parse_cycle(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y%m%d%H")


def _parse_members(spec, raw: Optional[str]) -> Optional[List[str]]:
    if not raw or raw.lower() == "all":
        return None
    if raw.isdigit():
        return spec.member_ids()[: int(raw)]
    return [m.strip().upper() for m in raw.split(",") if m.strip()]


def _parse_steps(raw: Optional[str]) -> Optional[List[int]]:
    if not raw:
        return None
    return [int(x) for x in raw.split(",") if x.strip()]


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Build ensemble cyclone-center JSON for R2.")
    p.add_argument("--model", default="ecens", help="registry model slug (default ecens)")
    p.add_argument("--out-dir", default=f"./{R2_PREFIX}",
                   help="output dir == R2 key prefix tail (default ./models/enscenters)")
    p.add_argument("--cycle", help="force cycle YYYYMMDDHH (default: latest complete)")
    p.add_argument("--members", help="'all' (default), an int N (first N), or CTL,P01,...")
    p.add_argument("--steps", help="comma list of forecast hours to subset (default: all)")
    p.add_argument("--jobs", type=int, default=1, help="parallel member workers (processes)")
    p.add_argument("--retain", type=int, default=DEFAULT_RETAIN, help="cycles kept per model on R2")
    p.add_argument("--min-members-frac", type=float, default=DEFAULT_MIN_MEMBERS_FRAC,
                   help="quorum: refuse to publish if fewer than this fraction of members ingest")
    p.add_argument("--source", default="ecmwf", help="ecmwf-opendata source: ecmwf|aws|azure|google")
    args = p.parse_args(argv)

    spec = get_spec(args.model)

    if args.cycle:
        cycle = _parse_cycle(args.cycle)
    else:
        client = make_client(args.source)
        cycle = resolve_latest_complete(client, spec)
        if cycle is None:
            print("[ecens] no complete cycle available yet - nothing to publish.")
            return 0
    cycle = cycle.replace(minute=0, second=0, microsecond=0, tzinfo=None)

    members = _parse_members(spec, args.members)
    steps = _parse_steps(args.steps)

    try:
        summary = build_cycle(
            spec, cycle, args.out_dir,
            members=members, steps=steps, jobs=args.jobs,
            retain=args.retain, min_members_frac=args.min_members_frac,
            source=args.source,
        )
    except RuntimeError as e:
        print(f"[ecens] FATAL: {e}", file=sys.stderr)
        return 1

    print(f"[ecens] OK cycle={summary['cycle']} members={summary['members']} "
          f"centers={summary['n_centers']} json={summary['bytes_json']/1e6:.2f}MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
