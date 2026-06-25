"""tcprimed.cli - entry point for the observed passive-MW build (CI cron).

  python generate_tcprimed.py --out-dir ./tcprimed_build            # current year
  python generate_tcprimed.py --year 2024 --basins AL               # one basin
  python generate_tcprimed.py --year 2024 --basins AL --storm AL092024 \
        --tier final --max-overpasses 3                             # one storm
  python generate_tcprimed.py --years 2018-2024                     # backfill

Kill switch: TCPRIMED_ENABLED=false (or --disabled) exits 0 without writing -
the workflow's R2 sync then has nothing to push, so the last-known-good R2 state
stays live. Any build exception -> exit 1 (the workflow guards the sync on the
manifest existing + exit code).
"""
from __future__ import annotations

import argparse
import os
import sys

from .build import build as run_build

DEFAULT_MANIFEST_URL = "https://cdn.triple-a-tropics.com/microwave/manifest.json"
DEFAULT_CDN_BASE = "https://cdn.triple-a-tropics.com/microwave"


def _parse_years(spec):
    """'2024' -> [2024]; '2018-2024' -> [2018..2024]; None -> None."""
    if not spec:
        return None
    spec = spec.strip()
    if "-" in spec:
        lo, hi = spec.split("-", 1)
        return list(range(int(lo), int(hi) + 1))
    return [int(spec)]


def _parse_tiers(spec):
    """'final' / 'preliminary' / 'both' / 'final,preliminary'."""
    if not spec or spec.lower() == "both":
        return ("final", "preliminary")
    return tuple(t.strip() for t in spec.split(",") if t.strip())


def main(argv=None) -> int:
    import datetime as dt
    p = argparse.ArgumentParser(prog="tcprimed")
    p.add_argument("--out-dir", default="./tcprimed_build")
    p.add_argument("--tier", default="both",
                   help="final | preliminary | both (default: both, "
                        "preferring final when a storm exists in both)")
    p.add_argument("--year", type=int, default=None,
                   help="single calendar year (default: current)")
    p.add_argument("--years", default=None,
                   help="range for backfill, e.g. 2018-2024")
    p.add_argument("--basins", default="AL,EP,WP,IO,SH")
    p.add_argument("--storm", default=None,
                   help="single ATCFID (e.g. AL092024) for testing")
    p.add_argument("--max-overpasses", type=int, default=None,
                   help="per-storm cap on overpasses rendered")
    p.add_argument("--force", action="store_true",
                   help="re-render every overpass even if already published "
                        "(use after a render-code change to refresh R2). Also "
                        "via TCPRIMED_FORCE=1.")
    p.add_argument("--live", action="store_true",
                   help="LIVE/NRT tier: render currently-active storms from PPS "
                        "NRT GPM 1C (needs PPS_EMAIL) instead of the TC-PRIMED "
                        "archive. Merges into the same microwave/ manifest.")
    p.add_argument("--window-hours", type=int, default=6,
                   help="live tier: NRT lookback window (default 6)")
    p.add_argument("--manifest-url", default=DEFAULT_MANIFEST_URL,
                   help="prior manifest to MERGE into (the growing union)")
    p.add_argument("--cdn-base", default=DEFAULT_CDN_BASE,
                   help="CDN base for fetching prior per-storm overpasses.json")
    p.add_argument("--disabled", action="store_true")
    args = p.parse_args(argv)

    enabled = (os.environ.get("TCPRIMED_ENABLED", "1") or "1").lower() \
        not in ("0", "false", "no")
    if args.disabled or not enabled:
        print("tcprimed: disabled (kill switch) - no write")
        return 0

    basins = tuple(b.strip().upper() for b in args.basins.split(",")
                   if b.strip())
    tiers = _parse_tiers(args.tier)
    years = _parse_years(args.years)
    year = args.year if years is None else None
    if years is None and year is None:
        year = dt.date.today().year

    force = args.force or (os.environ.get("TCPRIMED_FORCE", "") or "").lower() \
        in ("1", "true", "yes")
    live = args.live or (os.environ.get("TCPRIMED_LIVE", "") or "").lower() \
        in ("1", "true", "yes")

    os.makedirs(args.out_dir, exist_ok=True)
    try:
        if live:
            from .build import build_live
            build_live(args.out_dir, window_hours=args.window_hours,
                       prior_manifest_url=(args.manifest_url or None),
                       cdn_base=(args.cdn_base or None), force=force)
        else:
            run_build(args.out_dir, tiers=tiers, year=year, years=years,
                      basins=basins, storm=args.storm,
                      max_overpasses=args.max_overpasses,
                      prior_manifest_url=(args.manifest_url or None),
                      cdn_base=(args.cdn_base or None), force=force)
    except Exception as e:  # noqa: BLE001
        # Never leave a half-written tree that the sync would push: fail loud
        # but non-destructively (the workflow guards the sync on exit code).
        print(f"tcprimed: build FAILED: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
