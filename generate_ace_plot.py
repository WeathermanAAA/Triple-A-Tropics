#!/usr/bin/env python3
"""
generate_ace_plot.py
--------------------
Generate a real-time, interactive plot of Accumulated Cyclone Energy (ACE)
for any of three tropical cyclone basins (Atlantic, East Pacific, West
Pacific) and write it out as a standalone HTML file you can embed.

Usage:
    python generate_ace_plot.py --basin wp    # Western Pacific (default)
    python generate_ace_plot.py --basin al    # North Atlantic
    python generate_ace_plot.py --basin ep    # Eastern Pacific

Outputs (in the same folder as this script, or wherever ACE_OUTPUT_DIR points):
    {basin}_ace.html        Self-contained interactive SVG chart
    {basin}_ace_data.json   Processed data that feeds the chart

Inputs:
    ibtracs.{CODE}.list.v04r01.csv   e.g. ibtracs.NA.list.v04r01.csv for AL
    (Download from NCEI — see DOWNLOAD_URL below.)

Live current-season data is optionally pulled from ATCF best-track files:
  - Atlantic  → NHC FTP archive (very reliable)
  - East Pac  → NHC FTP archive (very reliable)
  - West Pac  → JTWC / NRL mirrors (sometimes blocked from cloud runners)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Basin configuration — add another entry to onboard a new basin
# ---------------------------------------------------------------------------

BASINS: dict[str, dict] = {
    "wp": {
        "short": "wp",
        "name": "West Pacific",
        "full_name": "Western North Pacific",
        "ibtracs_file_code": "WP",
        "ibtracs_basin_col": ["WP"],    # accepted BASIN column values
        "atcf_prefix": "bwp",
        "agency_name": "JTWC",
        "agency_url": "https://www.metoc.navy.mil/jtwc/",
        # Primary is our Cloudflare Worker proxy (bypasses IP blocks).
        # Filename convention uses a 4-digit year (bXX##YYYY.dat), which
        # is how natyphoon.top / NHC FTP actually serve these files.
        "atcf_patterns": [
            "https://triple-a-tropics-proxy.coloradoskier2018.workers.dev/atcf/btk/bwp{nn}{year}.dat",
            "https://www.natyphoon.top/atcf/temp/bwp{nn}{year}.dat",
            "https://www.metoc.navy.mil/jtwc/products/atcf/btk/bwp{nn}{year}.dat",
        ],
        # JTWC: USA_WIND (1-min) → WMO → Tokyo (both 10-min, ÷0.88)
        "wind_preference": [
            ("USA_WIND", 1.0),
            ("WMO_WIND", 1.0 / 0.88),
            ("TOKYO_WIND", 1.0 / 0.88),
        ],
        # JTWC methodology: ACE counts TROPICAL phase only (not subtropical).
        "ace_natures": {"TS"},
        # Exclude only explicitly non-tropical codes. TD is caught by the
        # wind >= 34 kt filter. SS/SD excluded because JTWC is tropical-
        # only. Anything else JTWC might label (TS/TY/STY/HU/TD/etc.)
        # passes and is counted if wind >= 34.
        "atcf_dev_exclude": {"EX", "SS", "SD"},
        "download_url": "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.WP.list.v04r01.csv",
    },
    "al": {
        "short": "al",
        "name": "Atlantic",
        "full_name": "North Atlantic",
        "ibtracs_file_code": "NA",
        # Accept multiple basin codes — some IBTrACS files use 'NA',
        # some may use 'AL' (ATCF-style).
        "ibtracs_basin_col": ["NA", "AL"],
        "atcf_prefix": "bal",
        "agency_name": "NHC",
        "agency_url": "https://www.nhc.noaa.gov/",
        "atcf_patterns": [
            "https://triple-a-tropics-proxy.coloradoskier2018.workers.dev/atcf/btk/bal{nn}{year}.dat",
            "https://ftp.nhc.noaa.gov/atcf/btk/bal{nn}{year}.dat",
            "https://www.natyphoon.top/atcf/temp/bal{nn}{year}.dat",
        ],
        "wind_preference": [
            ("USA_WIND", 1.0),
            ("WMO_WIND", 1.0 / 0.88),
        ],
        # NHC methodology: ACE counts tropical AND subtropical storms at
        # 34 kt+. This matches the official published numbers (e.g. 2005
        # Atlantic ACE = 245.47 which counts Subtropical Storm Arlene).
        "ace_natures": {"TS", "SS"},
        # Exclude only extratropical. SS/SD stay included; TD filtered by
        # wind >= 34.
        "atcf_dev_exclude": {"EX"},
        "download_url": "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.NA.list.v04r01.csv",
    },
    "ep": {
        "short": "ep",
        "name": "East Pacific",
        "full_name": "Northeast Pacific",
        "ibtracs_file_code": "EP",
        "ibtracs_basin_col": ["EP"],
        "atcf_prefix": "bep",
        "agency_name": "NHC",
        "agency_url": "https://www.nhc.noaa.gov/",
        "atcf_patterns": [
            "https://triple-a-tropics-proxy.coloradoskier2018.workers.dev/atcf/btk/bep{nn}{year}.dat",
            "https://ftp.nhc.noaa.gov/atcf/btk/bep{nn}{year}.dat",
            "https://www.natyphoon.top/atcf/temp/bep{nn}{year}.dat",
        ],
        "wind_preference": [
            ("USA_WIND", 1.0),
            ("WMO_WIND", 1.0 / 0.88),
        ],
        # NHC methodology — same as Atlantic
        "ace_natures": {"TS", "SS"},
        "atcf_dev_exclude": {"EX"},
        "download_url": "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.EP.list.v04r01.csv",
    },
}

# ---------------------------------------------------------------------------
# Shared config
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = Path(os.environ.get("ACE_OUTPUT_DIR", str(HERE)))

CLIMO_START = 1991
CLIMO_END = 2020

FETCH_LIVE = True
FETCH_TIMEOUT = 10  # seconds

# Browser-like User-Agent — .mil/.gov sites routinely block plain urllib.
FETCH_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/17.0 Safari/605.1.15")

SIX_HOURLY = {0, 6, 12, 18}


# ---------------------------------------------------------------------------
# ACE computation (basin-parameterized)
# ---------------------------------------------------------------------------

def _best_wind(row: pd.Series, preference: list[tuple[str, float]]) -> float:
    """Walk the basin's wind-column preference list; return the first
    non-null value × its conversion factor (usually 1.0 for 1-min winds,
    1/0.88 for 10-min winds)."""
    for col, factor in preference:
        v = row.get(col)
        if pd.notna(v):
            return float(v) * factor
    return np.nan


def compute_ace_timeseries(df: pd.DataFrame, basin_cfg: dict,
                           log_prefix: str = "") -> pd.DataFrame:
    """Return a dataframe with columns [season, doy, ace_increment, SID, NAME]
    at 6-hourly resolution for one basin. Logs per-step row counts so we can
    see which filter drops everything if something goes wrong."""
    def step(label: str, d: pd.DataFrame) -> pd.DataFrame:
        print(f"{log_prefix}   after {label}: {len(d):,} rows")
        return d

    d = df.copy()
    print(f"{log_prefix}   raw rows: {len(d):,}")

    # BASIN column filter. We accept a list of codes to handle IBTrACS's
    # occasional basin-labeling quirks (crossover storms, alternate codes).
    basin_codes = basin_cfg["ibtracs_basin_col"]
    if isinstance(basin_codes, str):
        basin_codes = [basin_codes]
    # If BASIN filter drops everything, fall back to accepting all rows
    # (the file itself is already basin-scoped).
    before_basin = len(d)
    d_basin = d[d["BASIN"].isin(basin_codes)]
    if len(d_basin) == 0 and before_basin > 0:
        print(f"{log_prefix}   WARN: BASIN filter {basin_codes} matched 0 rows; "
              f"found values {sorted(d['BASIN'].dropna().unique().tolist())}. "
              f"Falling back to whole file.")
    else:
        d = d_basin
    d = step(f"BASIN in {basin_codes}", d.copy())

    d = step("TRACK_TYPE filter", d[d["TRACK_TYPE"].isin(["main", "PROVISIONAL"])].copy())
    d["ISO_TIME"] = pd.to_datetime(d["ISO_TIME"], errors="coerce")
    d = step("ISO_TIME parse", d.dropna(subset=["ISO_TIME"]))

    # Standard ACE convention: 00/06/12/18 UTC only
    hours = d["ISO_TIME"].dt.hour
    minutes = d["ISO_TIME"].dt.minute
    d = step("6-hourly synoptic", d[hours.isin(SIX_HOURLY) & (minutes == 0)])

    # Convert wind columns to numeric
    for col, _ in basin_cfg["wind_preference"]:
        if col in d.columns:
            d[col] = pd.to_numeric(d[col], errors="coerce")
    d["WIND_KT"] = d.apply(lambda r: _best_wind(r, basin_cfg["wind_preference"]), axis=1)

    # Nature filter. Basin-specific: JTWC WPac counts TS only; NHC basins
    # (Atlantic, EPac) count both tropical and subtropical (matches the
    # official published ACE methodology).
    # IBTrACS backfills NATURE="NR" to "TS" only after post-season QC, so
    # for PROVISIONAL rows we also accept NR.
    ace_natures = set(basin_cfg["ace_natures"])
    is_tropical = d["NATURE"].isin(ace_natures) | (
        (d["TRACK_TYPE"] == "PROVISIONAL") & d["NATURE"].isin(ace_natures | {"NR"})
    )
    d = step(f"NATURE in {sorted(ace_natures)} (+NR on provisional)", d[is_tropical])
    d = step("WIND_KT >= 34", d[d["WIND_KT"] >= 34])

    d["ACE"] = (d["WIND_KT"] ** 2) / 10_000.0
    d["doy"] = d["ISO_TIME"].dt.dayofyear
    d["season"] = d["SEASON"].astype(int)
    d = d.drop_duplicates(subset=["SID", "ISO_TIME"])

    return d[["season", "doy", "ACE", "SID", "NAME", "ISO_TIME", "WIND_KT"]].rename(
        columns={"ACE": "ace_increment"}
    )


def cumulative_by_doy(points: pd.DataFrame) -> pd.DataFrame:
    g = points.groupby(["season", "doy"], as_index=False)["ace_increment"].sum()
    doys = np.arange(1, 367)
    seasons = sorted(g["season"].unique())
    out = pd.DataFrame(index=doys, columns=seasons, dtype=float).fillna(0.0)
    for (s, doy), inc in g.set_index(["season", "doy"])["ace_increment"].items():
        out.at[doy, s] = inc
    cum = out.cumsum(axis=0)
    cum.index.name = "doy"
    return cum


def extract_storms_by_year(points: pd.DataFrame, min_year: int = 1970) -> dict:
    """Per-storm summaries grouped by season, derived from the same filtered
    points used for ACE. For each (season, SID) we record: storm name,
    formation = first 6-hourly TS+ observation, dissipation = last, and
    peak_wind_kt = max wind during the storm's TS+ life. Sorted by formation
    within each year.

    Pre-`min_year` seasons are excluded because the per-storm Gantt is the
    only consumer and IBTrACS metadata is sparse/unreliable that far back —
    early-1900s entries often have one observation, no name, no peak wind.
    """
    if points.empty or "ISO_TIME" not in points.columns:
        return {}
    out: dict[int, list[dict]] = {}
    grp = points.groupby(["season", "SID"], sort=False)
    for (season, sid), rows in grp:
        season_int = int(season)
        if season_int < min_year:
            continue
        if "ISO_TIME" not in rows.columns:
            continue
        formation = rows["ISO_TIME"].min()
        dissipation = rows["ISO_TIME"].max()
        if pd.isna(formation) or pd.isna(dissipation):
            continue
        peak_w = rows["WIND_KT"].max() if "WIND_KT" in rows.columns else None
        if pd.isna(peak_w):
            peak_w = None
        # NAME is sometimes blank/UNNAMED; use the first non-blank value
        # if any 6-hourly point in the storm's life had a name.
        name = ""
        for n in rows["NAME"].fillna("").astype(str):
            n = n.strip()
            if n and n.upper() not in ("UNNAMED", "NAMELESS", "INVEST"):
                name = n.upper()
                break
        if not name:
            name = "UNNAMED"
        record = {
            "name": name,
            "formation": formation.isoformat() if hasattr(formation, "isoformat")
                         else str(formation),
            "dissipation": dissipation.isoformat() if hasattr(dissipation, "isoformat")
                           else str(dissipation),
            "peak_wind_kt": float(peak_w) if peak_w is not None else None,
        }
        out.setdefault(season_int, []).append(record)
    for year in out:
        out[year].sort(key=lambda s: s["formation"] or "")
    return out


def climatology(cum: pd.DataFrame, start: int, end: int,
                exclude_years: set[int] | None = None) -> pd.DataFrame:
    """Percentile bands + mean come from the climatology window (1991-2020,
    matches NHC official normals). Min/max come from ALL past seasons so
    the outer envelope actually bounds historical extremes like Atlantic
    1933 or 2005.

    Excludes `exclude_years` (typically the current/in-progress year) from
    both, so a partial season's zeroes after today don't pull the min
    envelope down to zero."""
    exclude_years = exclude_years or set()
    climo_cols = [s for s in cum.columns
                  if start <= s <= end and s not in exclude_years]
    minmax_cols = [s for s in cum.columns if s not in exclude_years]
    climo = cum[climo_cols] if climo_cols else cum
    all_seasons = cum[minmax_cols] if minmax_cols else cum
    out = pd.DataFrame(index=cum.index)
    out["p10"] = climo.quantile(0.10, axis=1)
    out["p25"] = climo.quantile(0.25, axis=1)
    out["p50"] = climo.quantile(0.50, axis=1)
    out["p75"] = climo.quantile(0.75, axis=1)
    out["p90"] = climo.quantile(0.90, axis=1)
    out["mean"] = climo.mean(axis=1)
    out["min"] = all_seasons.min(axis=1)
    out["max"] = all_seasons.max(axis=1)
    return out


# ---------------------------------------------------------------------------
# Live ATCF fetch
# ---------------------------------------------------------------------------

def _parse_atcf(text: str, season: int, basin_cfg: dict) -> pd.DataFrame:
    """Parse an ATCF b-deck file. Works identically for JTWC and NHC.

    ATCF b-decks have multiple BEST lines per timestamp (one per
    wind-radius threshold 34/50/64 kt) for observations with radii data;
    earlier/weaker observations may have fewer columns. Dedupe with BOTH
    RAD=='34' filter AND (storm_num, tstamp) fallback so we accept
    shorter lines too. Exclude list matches what the tracks generator
    effectively does — only truly non-tropical codes."""
    rows = []
    name_by_storm: dict[int, str] = {}
    exclude = set(basin_cfg.get("atcf_dev_exclude", {"EX"}))
    # First pass: extract storm name from column 27 (if any line has it)
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 28:
            continue
        try:
            storm_num = int(parts[1])
            tech = parts[4]
            name_col = parts[27]
        except (IndexError, ValueError):
            continue
        if tech != "BEST":
            continue
        if name_col and name_col not in {"", "NAMELESS", "INVEST"}:
            name_by_storm[storm_num] = name_col

    # Second pass: extract observations
    seen: set[tuple[int, str]] = set()
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 11:
            continue
        try:
            storm_num = int(parts[1])
            tstamp = parts[2]
            tech = parts[4]
            vmax_s = parts[8]
            devlvl = parts[10]
            rad = parts[11] if len(parts) > 11 else ""
        except (IndexError, ValueError):
            continue
        if tech != "BEST":
            continue
        # Skip 50/64 kt radius duplicates. Accept blank RAD (lines with
        # no radii column) — those are single-observation lines.
        if rad not in ("", "34"):
            continue
        # Belt-and-suspenders dedupe: if two lines made it through the
        # RAD filter at the same (storm, tstamp), count only the first.
        key = (storm_num, tstamp)
        if key in seen:
            continue
        seen.add(key)
        try:
            t = dt.datetime.strptime(tstamp, "%Y%m%d%H")
        except ValueError:
            continue
        if t.hour not in SIX_HOURLY:
            continue
        try:
            vmax = float(vmax_s)
        except ValueError:
            continue
        if vmax < 34:
            continue
        if devlvl in exclude:
            continue
        rows.append({
            "season": season,
            "doy": t.timetuple().tm_yday,
            "ace_increment": (vmax ** 2) / 10_000.0,
            "SID": f"{basin_cfg['agency_name']}_{basin_cfg['short'].upper()}"
                   f"{storm_num:02d}{season}",
            "NAME": name_by_storm.get(storm_num, ""),
            "ISO_TIME": t,
            "WIND_KT": vmax,
        })
    return pd.DataFrame(rows)


def fetch_live_season(season: int, basin_cfg: dict, log_prefix: str) -> pd.DataFrame:
    """Try to pull current-season b-deck files. Empty frame on any failure,
    but always logs a per-pattern diagnostic so you can see why."""
    try:
        import urllib.request
        import urllib.error
    except Exception:
        return pd.DataFrame()

    yy = season % 100
    frames = []
    patterns = basin_cfg["atcf_patterns"]
    pattern_stats: dict[str, dict] = {p: {"ok": 0, "errors": {}} for p in patterns}

    consecutive_misses = 0
    for nn in range(1, 41):
        hit = False
        for pattern in patterns:
            url = pattern.format(nn=f"{nn:02d}", yy=f"{yy:02d}", year=season)
            err_key = None
            try:
                req = urllib.request.Request(url, headers={"User-Agent": FETCH_UA})
                with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
                    if r.status != 200:
                        err_key = f"http_{r.status}"
                    else:
                        text = r.read().decode("utf-8", errors="ignore")
                        if "BEST" not in text:
                            err_key = "no_BEST_lines"
                        else:
                            frames.append(_parse_atcf(text, season, basin_cfg))
                            pattern_stats[pattern]["ok"] += 1
                            hit = True
                            break
            except urllib.error.HTTPError as e:
                err_key = f"http_{e.code}"
            except Exception as e:
                err_key = type(e).__name__
            if err_key:
                pattern_stats[pattern]["errors"][err_key] = \
                    pattern_stats[pattern]["errors"].get(err_key, 0) + 1
        consecutive_misses = 0 if hit else consecutive_misses + 1
        if consecutive_misses >= 3:
            break

    total_hits = sum(s["ok"] for s in pattern_stats.values())
    print(f"{log_prefix}   live fetch: {total_hits} storm file(s) found")
    for pattern, s in pattern_stats.items():
        host = pattern.split("/")[2]
        if s["ok"]:
            print(f"{log_prefix}     {host}: ✓ {s['ok']} ok")
        elif s["errors"]:
            err_summary = ", ".join(f"{k}×{v}" for k, v in s["errors"].items())
            print(f"{log_prefix}     {host}: {err_summary}")

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# HTML rendering (self-contained dark SVG chart + ranking table)
# ---------------------------------------------------------------------------

from _ace_template import HTML_TEMPLATE  # noqa: E402


def build_payload(cum: pd.DataFrame, climo: pd.DataFrame, current_year: int,
                  prior_year: int | None, last_obs_doy: dict[int, int],
                  storms_by_year: dict | None = None) -> dict:
    doy = cum.index.tolist()
    today = dt.date.today()
    today_doy_real = today.timetuple().tm_yday if today.year == current_year else None

    def series(year):
        if year is None or year not in cum.columns:
            return {}
        vals = cum[year].values.astype(float)
        if year == current_year:
            last_obs = last_obs_doy.get(year, 1)
            end_doy = max(last_obs, today_doy_real or last_obs)
            end_doy = max(1, min(366, end_doy))
            vals_out = vals[:end_doy]
            doy_out = doy[:end_doy]
        else:
            vals_out = vals
            doy_out = doy
        return {
            "label": str(year),
            "doy": [int(x) for x in doy_out],
            "values": [round(float(v), 3) for v in vals_out],
            "latest_value": round(float(vals_out[-1]) if len(vals_out) else 0.0, 3),
        }

    doy_cutoff = today_doy_real or 366
    rank_rows = []
    for year in sorted(cum.columns):
        col = cum[year].values.astype(float)
        total = float(col[-1])
        ytd = float(col[min(doy_cutoff, len(col)) - 1])
        rank_rows.append({
            "year": int(year), "ytd": round(ytd, 2),
            "total": round(total, 2),
            "current": int(year) == current_year,
        })
    rank_rows.sort(key=lambda r: (-r["ytd"], -r["total"], -r["year"]))
    for i, r in enumerate(rank_rows, start=1):
        r["rank"] = i
    current_rank = next((r["rank"] for r in rank_rows if r["current"]), None)

    all_years = {}
    for year in sorted(cum.columns):
        vals = cum[year].values.astype(float)
        if float(vals[-1]) <= 0:
            continue
        all_years[int(year)] = [round(float(v), 1) for v in vals]

    storms_payload = {}
    if storms_by_year:
        # Stringify keys for stable JSON dict keys (numeric keys → strings)
        storms_payload = {str(y): v for y, v in storms_by_year.items()}

    return {
        "doy": [int(x) for x in doy],
        "climo": {
            k: [round(float(v), 3) for v in climo[k].values]
            for k in ("min", "p10", "p25", "mean", "p75", "p90", "max")
        },
        "current": series(current_year),
        "prior_year": series(prior_year) if prior_year else {},
        "today_doy": today_doy_real,
        "rankings": rank_rows,
        "current_rank": current_rank,
        "total_seasons": len(rank_rows),
        "all_years": all_years,
        "storms_by_year": storms_payload,
    }


_BASIN_SHORT_LABELS = {"wp": "WPAC", "al": "AL", "ep": "EPAC"}


def render_html(payload: dict, basin_cfg: dict, current_year: int,
                climo_start: int, climo_end: int, live_used: bool) -> str:
    live_note = f" + live {basin_cfg['agency_name']} b-deck" if live_used else ""
    short_label = _BASIN_SHORT_LABELS.get(basin_cfg["short"],
                                          basin_cfg["short"].upper())
    return HTML_TEMPLATE.format(
        payload=json.dumps(payload, separators=(",", ":")),
        basin_full_name=basin_cfg["full_name"],
        basin_short_label=short_label,
        current_year=current_year,
        climo_start=climo_start,
        climo_end=climo_end,
        updated=dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        live_note=live_note,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate an ACE climatology chart for one basin.")
    parser.add_argument("--basin", "-b", default="wp", choices=sorted(BASINS.keys()),
                        help="Basin to process: wp (West Pacific), al (Atlantic), ep (East Pacific)")
    parser.add_argument("--csv", help="Override the IBTrACS CSV path (otherwise looked up by basin).")
    parser.add_argument("--no-live", action="store_true",
                        help="Skip the live ATCF fetch; use only IBTrACS.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    basin = args.basin
    basin_cfg = BASINS[basin]
    log = f"[{basin}-ace]"

    csv_path = Path(args.csv) if args.csv else (
        Path(os.environ.get(f"IBTRACS_{basin.upper()}_CSV",
                            str(HERE / f"ibtracs.{basin_cfg['ibtracs_file_code']}.list.v04r01.csv")))
    )

    if not csv_path.exists():
        print(f"{log} ERROR: IBTrACS file not found at {csv_path}", file=sys.stderr)
        print(f"{log} Download from: {basin_cfg['download_url']}", file=sys.stderr)
        return 2

    print(f"{log} loading {csv_path} ...")
    df = pd.read_csv(csv_path, skiprows=[1], low_memory=False, na_values=[" ", ""])
    print(f"{log} {len(df):,} rows")

    points = compute_ace_timeseries(df, basin_cfg, log_prefix=log)
    if points.empty:
        print(f"{log} ERROR: no ACE-eligible points found after filtering.", file=sys.stderr)
        return 3
    print(f"{log} {len(points):,} 6-hour points contribute to ACE "
          f"(seasons {points['season'].min()}–{points['season'].max()})")

    # "Current year" = actual calendar year, not just the latest year with
    # data. This matters for basins like AL/EP that don't see any tropical
    # activity until June — in April we want the chart to say "2026 · 0.0
    # ACE so far", not relabel last year's finished season as "current".
    current_year = dt.date.today().year
    prior_year = current_year - 1

    live_used = False
    if FETCH_LIVE and not args.no_live:
        print(f"{log} attempting live {basin_cfg['agency_name']} fetch for {current_year} ...")
        live = fetch_live_season(current_year, basin_cfg, log)
        if not live.empty:
            print(f"{log} pulled {len(live)} live 6-hour points from {basin_cfg['agency_name']}")
            # Merge strategy: for each named storm appearing in BOTH
            # sources (live and current-year IBTrACS), keep whichever
            # source has more 6-hour observations for that storm. Live
            # is usually more complete for currently-active storms (it
            # has real-time advisories JTWC hasn't pushed to IBTrACS
            # yet); IBTrACS is sometimes more complete for dissipated
            # storms (JTWC may have stubbed or removed the bNN file).
            # One source per storm — no double-counting of ACE.
            placeholders = {"", "UNNAMED", "INVEST", "NAMELESS"}

            cur_mask = points["season"] == current_year
            ib_cur = points[cur_mask]
            ib_other = points[~cur_mask]

            ib_names = ib_cur["NAME"].fillna("").astype(str).str.strip().str.upper()
            live_names = live["NAME"].fillna("").astype(str).str.strip().str.upper()

            ib_counts = ib_names.value_counts().to_dict()
            live_counts = live_names.value_counts().to_dict()

            contested = {n for n in live_names.unique()
                         if n and n not in placeholders
                         and ib_counts.get(n, 0) > 0}

            drop_from_ib: list[str] = []
            drop_from_live: list[str] = []
            for name in contested:
                ib_c = ib_counts.get(name, 0)
                live_c = live_counts.get(name, 0)
                # Prefer the source with more observations. Ties go to
                # live (fresher, includes current JTWC/NHC advisory).
                if ib_c > live_c:
                    drop_from_live.append(name)
                else:
                    drop_from_ib.append(name)

            if drop_from_ib:
                ib_cur = ib_cur[~ib_names.isin(drop_from_ib)].copy()
            if drop_from_live:
                live = live[~live_names.isin(drop_from_live)].copy()
            if contested:
                print(f"{log}   merge: {len(contested)} storm(s) in both sources. "
                      f"Kept live for: {sorted(drop_from_ib)}. "
                      f"Kept IBTrACS for: {sorted(drop_from_live)}.")

            points = pd.concat([ib_other, ib_cur, live], ignore_index=True)
            live_used = True
        else:
            print(f"{log} live fetch returned nothing — using IBTrACS provisional data only")

    cum = cumulative_by_doy(points)

    # Ensure the current calendar year exists as a column even if IBTrACS
    # has no activity for it yet (pre-season). Keeps the chart honest.
    if current_year not in cum.columns:
        cum[current_year] = 0.0
        cum = cum.reindex(columns=sorted(cum.columns))

    climo = climatology(cum, CLIMO_START, CLIMO_END,
                        exclude_years={current_year})

    last_obs_doy = points.groupby("season")["doy"].max().to_dict()
    storms_by_year = extract_storms_by_year(points, min_year=1970)
    payload = build_payload(cum, climo, current_year, prior_year, last_obs_doy,
                            storms_by_year=storms_by_year)
    html = render_html(payload, basin_cfg, current_year, CLIMO_START, CLIMO_END, live_used)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    html_path = OUTPUT_DIR / f"{basin}_ace.html"
    json_path = OUTPUT_DIR / f"{basin}_ace_data.json"
    html_path.write_text(html, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    ytd = payload["current"].get("latest_value", 0.0)
    climo_today = None
    if payload["today_doy"]:
        idx = payload["today_doy"] - 1
        if 0 <= idx < len(payload["climo"]["mean"]):
            climo_today = payload["climo"]["mean"][idx]

    print(f"{log} wrote {html_path}")
    print(f"{log} wrote {json_path}")
    print(f"{log} {current_year} YTD ACE: {ytd:.1f}"
          + (f"  (climo avg to-date: {climo_today:.1f})" if climo_today else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
