"""
CLI entrypoint for the Ensemble Cyclone Centers builder.

Run by the scheduled GitHub Actions workflow (``update-enscenters.yml``):

    python generate_enscenters.py --jobs 4 --source aws

NEVER-MISS currency: instead of greedily building "the latest complete cycle"
(which silently skips a cycle that disseminates late), it reads the published
watermark from R2, lists the cycles COMPLETE on the source within a lookback, and
BACKFILLS every complete cycle not yet published - oldest first, capped per run.
A late or dropped cycle is simply caught by a later run. ``--cycle`` forces one
specific cycle (manual backfill / dispatch), bypassing the gate.

Exit codes:
  0  published one or more cycles, OR nothing was missing (leave live data alone -
     do NOT fail the workflow or prune)
  1  cycles were planned but ALL failed to ingest, or the prior manifest could
     not be read - the workflow aborts before the R2 prune so prior data stays
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from typing import List, Optional

from . import ingest as _ingest
from .currency import DEFAULT_LOOKBACK, DEFAULT_MAX_PER_RUN, run_currency, synoptic_cycles_back
from .ingest import files_present, make_client
from .pipeline import DEFAULT_MIN_MEMBERS_FRAC, DEFAULT_RETAIN, R2_PREFIX, build_cycle, build_one_cycle
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


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Build ensemble cyclone-center JSON for R2 (never-miss).")
    p.add_argument("--model", default="ecens", help="registry model slug (default ecens)")
    p.add_argument("--out-dir", default=f"./{R2_PREFIX}",
                   help="output dir == R2 key prefix tail (default ./models/enscenters)")
    p.add_argument("--cycle", help="force one cycle YYYYMMDDHH (bypasses the gate; manual backfill)")
    p.add_argument("--members", help="'all' (default), an int N (first N), or CTL,P01,...")
    p.add_argument("--steps", help="comma list of forecast hours to subset (default: all)")
    p.add_argument("--jobs", type=int, default=1, help="parallel member workers (processes)")
    p.add_argument("--retain", type=int, default=DEFAULT_RETAIN, help="cycles kept per model on R2")
    p.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK,
                   help="synoptic cycles back to scan for missing-but-complete (default 10)")
    p.add_argument("--max-per-run", type=int, default=DEFAULT_MAX_PER_RUN,
                   help="cap on cycles ingested per run (oldest-first; leftovers next run)")
    p.add_argument("--min-members-frac", type=float, default=DEFAULT_MIN_MEMBERS_FRAC,
                   help="quorum: refuse to publish a cycle if fewer than this fraction of members ingest")
    p.add_argument("--source", default="ecmwf", help="ecmwf-opendata source: ecmwf|aws|azure|google")
    args = p.parse_args(argv)

    spec = get_spec(args.model)
    members = _parse_members(spec, args.members)
    steps = _parse_steps(args.steps)
    tag = spec.slug
    is_tracks = getattr(spec, "source_kind", "self_detect") == "genesis_tracks"

    # --- forced single cycle (manual backfill / dispatch): bypass the gate ---
    # build_cycle dispatches the per-model ingest internally (genesis tracks vs
    # field detect), so this path is model-agnostic.
    if args.cycle:
        cycle = _parse_cycle(args.cycle).replace(minute=0, second=0, microsecond=0)
        try:
            summary = build_cycle(
                spec, cycle, args.out_dir,
                members=members, steps=steps, jobs=args.jobs,
                retain=args.retain, min_members_frac=args.min_members_frac,
                source=args.source,
            )
        except RuntimeError as e:
            print(f"[{tag}] FATAL: {e}", file=sys.stderr)
            return 1
        print(f"[{tag}] OK forced cycle={summary['cycle']} members={summary['members']} "
              f"centers={summary['n_centers']} json={summary['bytes_json']/1e6:.2f}MB")
        return 0

    # --- never-miss path: backfill every complete-but-unpublished cycle ---
    now = _utcnow()

    if is_tracks:
        # GEFS: NOAA's genesis tracker - one small ATCF file per cycle, no GRIB
        # client, no field detection. The completeness gate is "the genesis file
        # for the cycle is fetchable"; ingest = parse + map to the shared schema.
        from . import tracks as _tracks

        def list_complete(lookback: int):
            candidates = synoptic_cycles_back(now, lookback)
            return _tracks.list_complete_cycles(spec, candidates)

        def ingest_one(cycle: dt.datetime) -> dict:
            return _tracks.build_gefs_cycle(spec, cycle, args.out_dir)
    elif spec.source == "noaa-gefs-aws":
        # GEFS: closed-low detection on the 0.5 deg fields pulled via S3 .idx
        # byte-range (enscenters.gefs_ingest). Same detect + warm-core + currency
        # path as the ECMWF models; only the completeness gate (terminal-step file
        # present on noaa-gefs-pds) and the per-member download differ.
        from . import gefs_ingest as _gefs
        gate_client = _gefs.make_client()

        def list_complete(lookback: int):
            candidates = synoptic_cycles_back(now, lookback)
            return _gefs.list_complete_cycles(spec, candidates, gate_client)

        def ingest_one(cycle: dt.datetime) -> dict:
            return build_one_cycle(
                spec, cycle, args.out_dir,
                members=members, steps=steps, jobs=args.jobs,
                min_members_frac=args.min_members_frac, source=args.source)
    elif spec.source == "ecmwf-opendata":
        # ECMWF ENS / AIFS-ENS: closed-low detection on the perturbed MSLP fields,
        # ingested via the multi-homed direct byte-range backend. The completeness
        # gate is MIRROR-AWARE: a cycle is ingestable if its terminal step is on
        # EITHER mirror (preferring GCS), so we never block on the slower-publishing
        # one. The per-member download + mirror fallback live in build_one_cycle.
        from . import ecmwf_byterange_ingest as _ecbr
        gate_client = _ecbr.make_client(args.source, spec.od_model)

        def list_complete(lookback: int):
            candidates = synoptic_cycles_back(now, lookback)
            return _ecbr.list_complete_cycles(spec, candidates, gate_client)

        def ingest_one(cycle: dt.datetime) -> dict:
            return build_one_cycle(
                spec, cycle, args.out_dir,
                members=members, steps=steps, jobs=args.jobs,
                min_members_frac=args.min_members_frac, source=args.source)
    else:
        # Legacy ecmwf-opendata client path (no current model uses it; kept as a
        # safety fallback for an unrecognized self-detect source).
        client = make_client(args.source, spec.od_model)

        def list_complete(lookback: int):
            candidates = synoptic_cycles_back(now, lookback)
            return _ingest.list_complete_cycles(
                spec, candidates, lambda cycle, req: files_present(client, cycle, req))

        def ingest_one(cycle: dt.datetime) -> dict:
            return build_one_cycle(
                spec, cycle, args.out_dir,
                members=members, steps=steps, jobs=args.jobs,
                min_members_frac=args.min_members_frac, source=args.source)

    try:
        summary = run_currency(
            spec=spec, out_dir=args.out_dir,
            list_complete_cycles=list_complete, ingest_cycle=ingest_one,
            retain=args.retain, lookback=args.lookback, max_per_run=args.max_per_run,
        )
    except RuntimeError as e:
        print(f"[{tag}] FATAL: {e}", file=sys.stderr)
        return 1

    if summary.get("skipped"):
        print(f"[{tag}] no missing complete cycle - nothing to publish.")
        return 0
    print(f"[{tag}] OK published={summary['published']} "
          f"failed={summary['failed']} pruned={len(summary.get('prune_keys', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
