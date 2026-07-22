#!/usr/bin/env python3
"""TC History records engine — per-basin records/seasons JSON for R2.

Computes the full archive-stats set (counts, ACE/PDI, intensity, duration,
motion, RI, timing, concurrency, geography — see tc_records/) from HURDAT2
(AL/EP authority) + IBTrACS v04r01 (WP authority, current-season spine)
joined to the live ATCF b-deck sweep shared with generate_ace_plot, then
validates sentinel records (Tip 870, Wilma 882 + deepening boards, Gilbert
888, John 1994 duration, Ivan 70.4 / Ioke 85.3 ACE) before writing a byte
of output. Non-zero exit on any validation failure — the workflow must not
upload.

Usage:
    python generate_tc_records.py                 # all basins, live join
    python generate_tc_records.py --basin al --no-live
    python generate_tc_records.py --out records_out

IBTrACS CSVs are read from the repo root (downloaded by the workflow / the
manual-fetch helpers); HURDAT2 files are auto-discovered from the NHC index
and cached in records_cache/ (both gitignored).
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from tc_records import publish  # noqa: E402

IBTRACS_FILES = {"al": "ibtracs.NA.list.v04r01.csv",
                 "ep": "ibtracs.EP.list.v04r01.csv",
                 "wp": "ibtracs.WP.list.v04r01.csv"}

FETCH_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/17.0 Safari/605.1.15")

_H2_NAME_RE = re.compile(r"(hurdat2-(?:nepac-)?\d{4}-(\d{4})-(\d{2})(\d{2})"
                         r"(\d{2,4})[a-z]?\.txt)")


def _fetch(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": FETCH_UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def resolve_hurdat2(basin: str, cache: Path) -> Path:
    """Latest HURDAT2 file for a basin: scrape the NHC index, pick the
    newest (end-season, datestamp) match, download once into the cache.
    Falls back to the pinned known-good name if the scrape fails."""
    cfg = publish.RECORDS_BASINS[basin]
    index, prefix = cfg["hurdat2_index"], cfg["hurdat2_prefix"]
    name = None
    try:
        html = _fetch(index).decode("utf-8", errors="ignore")
        cands = []
        for m in _H2_NAME_RE.finditer(html):
            fname = m.group(1)
            if not fname.startswith(prefix):
                continue
            end_season = int(m.group(2))
            mm, dd, yy = int(m.group(3)), int(m.group(4)), m.group(5)
            year = int(yy) if len(yy) == 4 else 2000 + int(yy)
            cands.append(((end_season, year, mm, dd), fname))
        if cands:
            cands.sort()
            name = cands[-1][1]
    except Exception as e:
        print(f"[records] WARN hurdat2 index scrape failed ({e}); "
              "using pinned fallback")
    if name is None:
        name = cfg["hurdat2_fallback"]
    dest = cache / name
    if not dest.exists():
        print(f"[records] downloading {name}")
        cache.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_fetch(index.rstrip("/") + "/" + name))
    else:
        print(f"[records] hurdat2 cached: {name}")
    return dest


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--basin", "-b", action="append",
                   choices=sorted(publish.RECORDS_BASINS.keys()),
                   help="basin(s) to compute (default: all)")
    p.add_argument("--out", default=str(HERE / "records_out"),
                   help="output directory for the JSON files")
    p.add_argument("--no-live", action="store_true",
                   help="skip the live ATCF b-deck fetch (offline dev)")
    p.add_argument("--ibtracs-dir", default=str(HERE),
                   help="directory holding the ibtracs.*.list.v04r01.csv "
                        "files")
    p.add_argument("--hurdat2-al", help="local HURDAT2 Atlantic file "
                                        "(skips download)")
    p.add_argument("--hurdat2-ep", help="local HURDAT2 nepac file "
                                        "(skips download)")
    p.add_argument("--skip-validate", action="store_true",
                   help="dev only — never use in the publish workflow")
    args = p.parse_args(argv)

    basins = args.basin or sorted(publish.RECORDS_BASINS.keys())
    current_year = dt.date.today().year
    cache = HERE / "records_cache"
    out_dir = Path(args.out)

    results: dict[str, dict] = {}
    hurdat2_names: dict[str, str] = {}
    for basin in basins:
        print(f"[records] === {basin.upper()} ===")
        ib = Path(args.ibtracs_dir) / IBTRACS_FILES[basin]
        if not ib.exists():
            print(f"[records] ERROR: missing {ib}", file=sys.stderr)
            return 1
        h2 = None
        if basin in ("al", "ep"):
            override = args.hurdat2_al if basin == "al" else args.hurdat2_ep
            h2 = Path(override) if override else resolve_hurdat2(basin, cache)
            hurdat2_names[basin] = h2.name
        results[basin] = publish.compute_basin(
            basin, ibtracs_path=ib, hurdat2_path=h2,
            fetch_live=not args.no_live, current_year=current_year)
        r = results[basin]
        print(f"[records]   {len(r['storms']):,} storms, "
              f"{len(r['boards'])} boards, "
              f"{len(r['seasons_tbl'])} seasons")

    if args.skip_validate:
        print("[records] WARNING: validation gate SKIPPED (dev flag)")
        validation = {"skipped": True}
    else:
        validation = publish.validate_or_die(results)

    publish.emit(results, out_dir, validation, hurdat2_names, current_year)
    print(f"[records] done → {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
