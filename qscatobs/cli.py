"""qscatobs.cli - archive build entrypoint (manual/resumable; static source).

  python build_quikscat_storms.py --store local:/tmp/q --basin AL \
      --season 2005 --storm KATRINA
  python build_quikscat_storms.py --store r2 --basin AL --season 2005
  python build_quikscat_storms.py --store r2 --seasons 1999-2009 --basins AL,EP

Storm-less forms sweep every storm dir the archive lists for the
basin/season(s). Fully resumable: indexed passes are never redone.
"""
from __future__ import annotations

import argparse

from sarobs.store import make_store

from . import build, fetch


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="qscatobs")
    p.add_argument("--store", default="local:/tmp/tat-qscat")
    p.add_argument("--basin", default=None, help="single basin (AL/EP/CP)")
    p.add_argument("--basins", default=None, help="comma list for sweeps")
    p.add_argument("--season", type=int, default=None)
    p.add_argument("--seasons", default=None,
                   help="range for sweeps, e.g. 1999-2009")
    p.add_argument("--storm", default=None, help="single storm name")
    p.add_argument("--max-new", type=int, default=999,
                   help="pass budget per storm per run")
    p.add_argument("--geo-dir", default=".")
    args = p.parse_args(argv)

    store = make_store(args.store)
    basins = ([args.basin] if args.basin
              else (args.basins or "AL").split(","))
    if args.seasons:
        a, _, b = args.seasons.partition("-")
        seasons = list(range(int(a), int(b or a) + 1))
    else:
        seasons = [args.season] if args.season else []
    if not seasons:
        p.error("need --season or --seasons")

    coloc = fetch.load_colocation()
    print(f"qscat: colocation rows {len(coloc)}")
    for season in seasons:
        for basin in [b.strip().upper() for b in basins if b.strip()]:
            storms = ([args.storm.upper()] if args.storm
                      else fetch.list_storms(basin, season))
            for storm in storms:
                s = build.build_storm(store, basin, season, storm,
                                      coloc, geo_dir=args.geo_dir,
                                      max_new=args.max_new)
                print(f"qscat: {basin} {season} {storm}: {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
