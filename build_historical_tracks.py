"""Build historical season-track JSON files for AL / EP / WP from IBTrACS.

Produces one JSON per basin × year, matching the site's live tracks-data
schema (wp_tracks_data.json et al) so the animation player can use the
same code path for current and historical seasons.

Output layout:
  historical/{basin}/tracks/tracks_{YYYY}.json

Usage:
  python build_historical_tracks.py --basin wp
  python build_historical_tracks.py --basin al --min-year 1950
  python build_historical_tracks.py --all                   # all 3 basins
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Basin configuration — mirrors generate_tracks_plot.py in the repo so the
# animation and the existing live tracks map stay visually/semantically aligned.
# ---------------------------------------------------------------------------
BASINS: dict[str, dict] = {
    "wp": {
        "name": "West Pacific",
        "full_name": "Western North Pacific",
        "ibtracs_csv": "ibtracs_wp.csv",
        "basin_codes": {"WP"},
        "lon_wrap": "0-360",          # WP spans the dateline, use 0..360
        "ace_natures": {"TS"},
        "vocab": {"named": "named storms", "cat1plus": "typhoons",
                  "cat3plus": "major typhoons", "cat5": "super typhoons"},
    },
    "al": {
        "name": "Atlantic",
        "full_name": "North Atlantic",
        "ibtracs_csv": "ibtracs_na.csv",
        "basin_codes": {"NA", "AL"},
        "lon_wrap": "-180-180",
        "ace_natures": {"TS", "SS"},
        "vocab": {"named": "named storms", "cat1plus": "hurricanes",
                  "cat3plus": "major hurricanes", "cat5": "category 5s"},
    },
    "ep": {
        "name": "East Pacific",
        "full_name": "Northeast Pacific",
        "ibtracs_csv": "ibtracs_ep.csv",
        "basin_codes": {"EP"},
        "lon_wrap": "-180-180",
        "ace_natures": {"TS", "SS"},
        "vocab": {"named": "named storms", "cat1plus": "hurricanes",
                  "cat3plus": "major hurricanes", "cat5": "category 5s"},
    },
}

CAT_ORDER = ("LO", "TD", "TS", "C1", "C2", "C3", "C4", "C5")


def classify(wind_kt):
    """Saffir-Simpson-style bin using 1-min sustained wind (knots)."""
    if wind_kt is None:
        return "LO"
    if wind_kt < 25:
        return "LO"
    if wind_kt < 35:
        return "TD"
    if wind_kt < 64:
        return "TS"
    if wind_kt < 83:
        return "C1"
    if wind_kt < 96:
        return "C2"
    if wind_kt < 113:
        return "C3"
    if wind_kt < 137:
        return "C4"
    return "C5"


def _fnum(s):
    s = s.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _inum(s):
    f = _fnum(s)
    return None if f is None else int(round(f))


def _best_wind(row):
    """Prefer USA_WIND (JTWC/NHC 1-min), else convert WMO_WIND (10-min) → 1-min."""
    w = _inum(row.get("USA_WIND", ""))
    if w is not None:
        return w
    w10 = _fnum(row.get("WMO_WIND", ""))
    if w10 is not None:
        return int(round(w10 / 0.88))  # 10-min → 1-min
    return None


def _best_pres(row):
    return _inum(row.get("USA_PRES", "")) or _inum(row.get("WMO_PRES", ""))


def _normalize_lon(lon: float, wrap: str) -> float:
    if wrap == "0-360":
        return lon + 360.0 if lon < 0 else lon
    # wrap == "-180-180"
    return lon - 360.0 if lon > 180 else lon


def build_basin(basin_key: str, src_root: Path, out_root: Path,
                min_year: int | None = None, max_year: int | None = None,
                quiet: bool = False) -> list[int]:
    cfg = BASINS[basin_key]
    csv_path = src_root / cfg["ibtracs_csv"]
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Missing {csv_path}. Download from "
            "https://www.ncei.noaa.gov/data/international-best-track-archive"
            "-for-climate-stewardship-ibtracs/v04r01/access/csv/")

    # Pass 1 — group rows by (SID).
    by_sid: dict[str, list[dict]] = {}
    with csv_path.open("r", newline="") as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            if row.get("BASIN") not in cfg["basin_codes"]:
                continue
            season_str = row.get("SEASON", "").strip()
            if not season_str:
                continue
            try:
                yr = int(season_str)
            except ValueError:
                continue
            if min_year is not None and yr < min_year:
                continue
            if max_year is not None and yr > max_year:
                continue
            sid = row.get("SID", "").strip()
            if not sid:
                continue
            by_sid.setdefault(sid, []).append(row)

    # Pass 2 — build storms, group by year.
    by_year: dict[int, list[dict]] = {}
    for sid, rows in by_sid.items():
        # sort by time so start/end are correct
        rows.sort(key=lambda r: r.get("ISO_TIME", ""))
        season_str = rows[0].get("SEASON", "").strip()
        try:
            year = int(season_str)
        except ValueError:
            continue
        name = (rows[0].get("NAME") or "").strip() or "UNNAMED"
        if name in ("NOT_NAMED", "UNNAMED") or name.startswith("UNNAMED"):
            name = "UNNAMED"

        points: list[dict] = []
        ace = 0.0
        peak_w = 0
        peak_p = None
        max_cat_idx = -1
        max_cat = "LO"
        for r in rows:
            iso = r.get("ISO_TIME", "").strip()
            if not iso:
                continue
            try:
                t = datetime.strptime(iso, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            lat = _fnum(r.get("LAT", ""))
            lon = _fnum(r.get("LON", ""))
            if lat is None or lon is None:
                continue
            lon = _normalize_lon(lon, cfg["lon_wrap"])
            wind = _best_wind(r)
            pres = _best_pres(r)
            nature = (r.get("NATURE") or "").strip() or "NR"
            cls = classify(wind)

            # ACE: 6-hourly synoptic times, TS+ only, tropical/subtropical only.
            if (t.hour in (0, 6, 12, 18)
                    and wind is not None and wind >= 35
                    and nature in cfg["ace_natures"]):
                ace += (wind ** 2) / 10000.0

            if wind is not None and wind > peak_w:
                peak_w = wind
            if pres is not None:
                peak_p = pres if peak_p is None else min(peak_p, pres)
            ci = CAT_ORDER.index(cls) if cls in CAT_ORDER else 0
            if ci > max_cat_idx:
                max_cat_idx = ci
                max_cat = cls

            points.append({
                "t": t.strftime("%Y-%m-%dT%H:%M:%S"),
                "lat": round(lat, 2),
                "lon": round(lon, 2),
                "wind_kt": float(wind) if wind is not None else None,
                "pressure_mb": pres,
                "cls": cls,
                "nature": nature,
            })

        if not points:
            continue

        storm = {
            "sid": sid,
            "name": name,
            "season": year,
            "start": points[0]["t"],
            "end": points[-1]["t"],
            "peak_wind_kt": peak_w if peak_w else None,
            "peak_pressure_mb": peak_p,
            "ace": round(ace, 2),
            "max_category": max_cat,
            "current_category": points[-1]["cls"],
            "is_active": False,
            "points": points,
        }
        by_year.setdefault(year, []).append(storm)

    # Pass 3 — write one JSON per year.
    out_dir = out_root / basin_key / "tracks"
    out_dir.mkdir(parents=True, exist_ok=True)
    years_written: list[int] = []
    for year in sorted(by_year.keys()):
        storms = sorted(by_year[year], key=lambda s: s["start"])
        named = sum(1 for s in storms
                    if s["peak_wind_kt"] is not None and s["peak_wind_kt"] >= 34)
        cat1 = sum(1 for s in storms
                   if s["peak_wind_kt"] is not None and s["peak_wind_kt"] >= 64)
        cat3 = sum(1 for s in storms
                   if s["peak_wind_kt"] is not None and s["peak_wind_kt"] >= 96)
        cat5 = sum(1 for s in storms if s["max_category"] == "C5")
        total_ace = round(sum(s["ace"] for s in storms), 2)

        doc = {
            "basin": basin_key,
            "basin_name": cfg["full_name"],
            "year": year,
            "updated": f"{year}-12-31T23:59:59Z",
            "header": {
                "named": named,
                "cat1plus": cat1,
                "cat3plus": cat3,
                "cat5": cat5,
                "total_ace": total_ace,
            },
            "vocab": cfg["vocab"],
            "storms": storms,
        }
        out_path = out_dir / f"tracks_{year}.json"
        out_path.write_text(json.dumps(doc, separators=(",", ":")))
        years_written.append(year)
        if not quiet:
            print(f"  [{basin_key}] {year}: "
                  f"{len(storms):3d} storms  ACE {total_ace:6.1f}  "
                  f"named {named:2d} / c1+ {cat1:2d} / c3+ {cat3:2d} / c5 {cat5:2d}")

    # Manifest — list of years so the frontend can populate year dropdown.
    (out_root / basin_key / "years.json").write_text(
        json.dumps({"basin": basin_key, "years": years_written}))
    return years_written


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--basin", choices=list(BASINS.keys()),
                    help="Basin to build. Omit (or use --all) for all basins.")
    ap.add_argument("--all", action="store_true",
                    help="Build every basin (al, ep, wp).")
    ap.add_argument("--min-year", type=int, default=None)
    ap.add_argument("--max-year", type=int, default=None)
    ap.add_argument("--src-root", type=Path,
                    default=Path("/sessions/wizardly-elegant-mayer"))
    ap.add_argument("--out-root", type=Path,
                    default=Path("/sessions/wizardly-elegant-mayer/mnt/"
                                 "triple-a-tropics.com/historical"))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    basins = (["al", "ep", "wp"] if args.all or args.basin is None
              else [args.basin])
    for b in basins:
        print(f"=== {b.upper()} ===")
        yrs = build_basin(b, args.src_root, args.out_root,
                          min_year=args.min_year, max_year=args.max_year,
                          quiet=args.quiet)
        print(f"  {len(yrs)} seasons written "
              f"({yrs[0] if yrs else '-'}–{yrs[-1] if yrs else '-'})\n")


if __name__ == "__main__":
    main()
