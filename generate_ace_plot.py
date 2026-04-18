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

    return d[["season", "doy", "ACE", "SID", "NAME"]].rename(
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

HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{basin_full_name} TC ACE · {current_year}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root {{
    --bg: #07101c; --panel: #0f1a2a; --fg: #e5edf6; --muted: #8ea2bd;
    --faint: #4a5a75; --grid: #1a2840; --axis: #2f4666;
    --climo: #5dd3ff; --prior: #c084fc; --current: #ffb83a;
  }}
  html, body {{ margin: 0; background: var(--bg); color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    -webkit-font-smoothing: antialiased; }}
  .wrap {{ max-width: 1100px; margin: 0 auto; padding: 12px 16px 16px; }}
  h1 {{ font-size: 18px; margin: 0 0 2px; font-weight: 600; color: #f1f7fd;
    letter-spacing: 0.2px; }}
  .sub {{ font-size: 12px; color: var(--muted); }}
  .legend {{ font-size: 12px; color: var(--muted); display: flex; flex-wrap: wrap;
    gap: 14px; margin: 8px 0 4px; align-items: center; }}
  .legend .sw {{ display: inline-block; width: 22px; height: 10px;
    vertical-align: middle; margin-right: 6px; border-radius: 2px; }}
  .legend .ln {{ display: inline-block; width: 22px; height: 0; vertical-align: middle;
    margin-right: 6px; border-top: 2px solid; }}
  .row-main {{ display: flex; gap: 14px; align-items: stretch;
    margin-top: 6px; }}
  .chartbox {{ position: relative; flex: 1 1 auto; min-width: 0;
    background: var(--panel); border: 1px solid #1a2840; border-radius: 8px;
    padding: 6px 6px 2px; }}
  svg {{ width: 100%; height: auto; display: block; touch-action: none; }}
  .rank-wrap {{ flex: 0 0 240px; display: flex; flex-direction: column;
    max-height: 640px; }}
  .rank-title {{ font-size: 12px; color: var(--muted); margin-bottom: 4px; }}
  .rank-title b {{ color: var(--current); font-weight: 700; }}
  .rank-scroll {{ overflow-y: auto; border: 1px solid #1a2840;
    border-radius: 8px; background: var(--panel);
    scrollbar-color: #2a3e5c transparent; }}
  .rank-scroll::-webkit-scrollbar {{ width: 8px; }}
  .rank-scroll::-webkit-scrollbar-thumb {{ background: #2a3e5c; border-radius: 4px; }}
  table.rank {{ width: 100%; border-collapse: collapse; font-size: 12px;
    table-layout: fixed; }}
  table.rank thead th {{ position: sticky; top: 0; background: #0f7c7b;
    color: #e8fbfb; font-weight: 700; padding: 6px 4px; text-align: center;
    border-right: 1px solid rgba(255,255,255,0.22); line-height: 1.2;
    text-shadow: 0 1px 0 rgba(0,0,0,0.28); }}
  table.rank thead th:last-child {{ border-right: 0; }}
  table.rank td {{ padding: 6px 4px; text-align: center;
    border: 1px solid #1a2840; color: #d7e1ef; }}
  table.rank td.rank-col {{ color: var(--muted); font-variant-numeric: tabular-nums; }}
  table.rank td.num {{ font-variant-numeric: tabular-nums; }}
  table.rank tr {{ cursor: pointer; transition: background 0.12s; }}
  table.rank tr:nth-child(odd) td {{ background: #0c1624; }}
  table.rank tr:nth-child(even) td {{ background: #111e30; }}
  table.rank tr:hover td {{ background: #1a2a44; }}
  table.rank tr.is-current td {{ background: #7a4a0b; color: #fff4d6;
    font-weight: 700; box-shadow: inset 0 0 0 9999px rgba(255,184,58,0.16); }}
  table.rank tr.is-current:hover td {{ background: #8b5610; }}
  table.rank tr.is-selected td {{ background: #5b2a7a; color: #ffe4f7;
    font-weight: 700; box-shadow: inset 0 0 0 9999px rgba(255,77,210,0.18); }}
  table.rank tr.is-selected:hover td {{ background: #6c318e; }}
  .clear-btn {{ margin-left: 8px; font-size: 11px; padding: 2px 8px;
    background: transparent; border: 1px solid var(--border);
    color: var(--muted); border-radius: 999px; cursor: pointer;
    display: none; }}
  .clear-btn:hover {{ color: #ff4dd2; border-color: #ff4dd2; }}
  .clear-btn.show {{ display: inline-block; }}
  @media (max-width: 760px) {{
    .row-main {{ flex-direction: column; }}
    .rank-wrap {{ flex: 0 0 auto; max-height: 420px; }}
  }}
  .tooltip {{ position: absolute; pointer-events: none; background: #15243a;
    border: 1px solid #26385a; border-radius: 6px; padding: 6px 9px;
    font-size: 12px; color: #e5edf6;
    box-shadow: 0 4px 14px rgba(0,0,0,0.45);
    transform: translate(-50%, -100%); white-space: nowrap; opacity: 0;
    transition: opacity 0.12s; }}
  .tooltip .row {{ display: flex; align-items: center; gap: 6px; }}
  .tooltip .dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; }}
  .tooltip .date {{ font-weight: 600; margin-bottom: 3px; color: #f1f7fd; }}
  footer {{ font-size: 11px; color: var(--muted); margin-top: 8px; }}
  footer a {{ color: var(--muted); }}
</style>
</head>
<body>
<div class="wrap">
  <h1>{basin_full_name} · Accumulated Cyclone Energy</h1>
  <div class="sub">Season {current_year} vs {climo_start}–{climo_end} climatology · last updated {updated}</div>
  <div class="legend">
    <span><span class="ln" style="border-color:var(--climo);border-style:dashed"></span>Climo mean ({climo_start}–{climo_end})</span>
    <span><span class="ln" style="border-color:var(--prior)"></span><span id="priorLabel">prior</span></span>
    <span><span class="ln" style="border-color:var(--current);border-width:3px"></span><b style="color:var(--current)" id="currentLabel">current</b></span>
  </div>
  <div class="row-main">
    <div class="chartbox" id="chartbox">
      <svg id="chart" viewBox="0 0 1000 560" preserveAspectRatio="xMidYMid meet"></svg>
      <div class="tooltip" id="tip"></div>
    </div>
    <div class="rank-wrap">
      <div class="rank-title">
        Click a year to overlay · <b id="currentRankLbl">—</b>
        <button type="button" class="clear-btn" id="clearSelBtn" title="Clear selected year">clear ×</button>
      </div>
      <div class="rank-scroll" id="rankScroll">
        <table class="rank">
          <thead>
            <tr>
              <th>#</th><th>Year</th><th>ACE<br>To Date</th><th>Total<br>ACE</th>
            </tr>
          </thead>
          <tbody id="rankBody"></tbody>
        </table>
      </div>
    </div>
  </div>
  <footer>
    Source: IBTrACS v04r01 (NOAA NCEI){live_note}. ACE = Σ wind²/10⁴ at 6-hourly
    resolution for tropical-phase points with 1-min sustained winds ≥ 34 kt.
  </footer>
</div>
<script>
const DATA = {payload};

(function () {{
  const svg = document.getElementById('chart');
  const tip = document.getElementById('tip');
  const box = document.getElementById('chartbox');
  document.getElementById('currentLabel').textContent = DATA.current.label + ' (current)';
  document.getElementById('priorLabel').textContent = (DATA.prior_year.label || '') + ' (prior)';

  const W = 1000, H = 560;
  const M = {{ l: 60, r: 18, t: 12, b: 40 }};
  const PW = W - M.l - M.r, PH = H - M.t - M.b;

  const xMin = 1, xMax = 366;
  const yMax = Math.max(
    DATA.climo.max[DATA.climo.max.length - 1],
    DATA.current.values[DATA.current.values.length - 1] || 0,
    (DATA.prior_year.values ? DATA.prior_year.values[DATA.prior_year.values.length - 1] : 0)
  ) * 1.05 || 1;

  const xs = d => M.l + (d - xMin) / (xMax - xMin) * PW;
  const ys = v => M.t + PH - (v / yMax) * PH;

  const NS = 'http://www.w3.org/2000/svg';
  const el = (tag, attrs = {{}}, parent = svg) => {{
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    parent.appendChild(e);
    return e;
  }};

  const ySteps = 5;
  const yStep = niceStep(yMax / ySteps);
  for (let v = 0; v <= yMax; v += yStep) {{
    el('line', {{ x1: M.l, x2: M.l + PW, y1: ys(v), y2: ys(v),
      stroke: '#1a2840', 'stroke-width': 1 }});
    el('text', {{ x: M.l - 8, y: ys(v) + 4, 'text-anchor': 'end',
      'font-size': 11, fill: '#8ea2bd' }}).textContent = Math.round(v);
  }}
  el('text', {{ x: 14, y: M.t + PH / 2, 'text-anchor': 'middle',
    'font-size': 12, fill: '#a8b8d1',
    transform: `rotate(-90 14 ${{M.t + PH / 2}})` }})
    .textContent = 'Cumulative ACE (×10⁴ kt²)';

  const monthStarts = [1,32,60,91,121,152,182,213,244,274,305,335];
  const monthLabels = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  monthStarts.forEach((d, i) => {{
    el('line', {{ x1: xs(d), x2: xs(d), y1: M.t, y2: M.t + PH,
      stroke: '#142036', 'stroke-width': 1 }});
    el('text', {{ x: xs(d + 15), y: M.t + PH + 18, 'text-anchor': 'middle',
      'font-size': 11, fill: '#8ea2bd' }}).textContent = monthLabels[i];
  }});
  el('line', {{ x1: M.l, x2: M.l + PW, y1: M.t + PH, y2: M.t + PH,
    stroke: '#2f4666', 'stroke-width': 1 }});

  function band(upper, lower, fill) {{
    let d = '';
    for (let i = 0; i < DATA.doy.length; i++)
      d += (i === 0 ? 'M' : 'L') + xs(DATA.doy[i]) + ',' + ys(upper[i]) + ' ';
    for (let i = DATA.doy.length - 1; i >= 0; i--)
      d += 'L' + xs(DATA.doy[i]) + ',' + ys(lower[i]) + ' ';
    d += 'Z';
    el('path', {{ d, fill, stroke: 'none' }});
  }}
  band(DATA.climo.max, DATA.climo.min, 'rgba(70,140,200,0.12)');
  band(DATA.climo.p90, DATA.climo.p10, 'rgba(70,180,220,0.20)');
  band(DATA.climo.p75, DATA.climo.p25, 'rgba(80,210,240,0.30)');

  function linePath(xs_arr, ys_arr, stroke, width, dash, opacity) {{
    let d = '';
    for (let i = 0; i < xs_arr.length; i++)
      d += (i === 0 ? 'M' : 'L') + xs(xs_arr[i]) + ',' + ys(ys_arr[i]) + ' ';
    const attrs = {{ d, fill: 'none', stroke, 'stroke-width': width,
      'stroke-linejoin': 'round', 'stroke-linecap': 'round' }};
    if (dash) attrs['stroke-dasharray'] = dash;
    if (opacity != null) attrs['stroke-opacity'] = opacity;
    el('path', attrs);
  }}
  linePath(DATA.doy, DATA.climo.min, '#567894', 0.9, null, 0.55);
  linePath(DATA.doy, DATA.climo.max, '#567894', 0.9, null, 0.55);
  linePath(DATA.doy, DATA.climo.p10, '#4db8e0', 1.0, null, 0.70);
  linePath(DATA.doy, DATA.climo.p90, '#4db8e0', 1.0, null, 0.70);
  linePath(DATA.doy, DATA.climo.p25, '#8ce3ff', 1.4, null, 0.90);
  linePath(DATA.doy, DATA.climo.p75, '#8ce3ff', 1.4, null, 0.90);

  function line(xs_arr, ys_arr, stroke, width, dash) {{
    let d = '';
    for (let i = 0; i < xs_arr.length; i++)
      d += (i === 0 ? 'M' : 'L') + xs(xs_arr[i]) + ',' + ys(ys_arr[i]) + ' ';
    const attrs = {{ d, fill: 'none', stroke, 'stroke-width': width,
      'stroke-linejoin': 'round', 'stroke-linecap': 'round' }};
    if (dash) attrs['stroke-dasharray'] = dash;
    el('path', attrs);
  }}

  line(DATA.doy, DATA.climo.mean, '#5dd3ff', 2, '6 4');
  if (DATA.prior_year.values)
    line(DATA.doy, DATA.prior_year.values, '#c084fc', 2);

  const selGroup = el('g', {{ id: 'selGroup' }});
  let selectedYear = null;

  line(DATA.current.doy, DATA.current.values, '#ffb83a', 3);

  if (DATA.today_doy) {{
    el('circle', {{ cx: xs(DATA.today_doy), cy: ys(DATA.current.latest_value),
      r: 5, fill: '#ffb83a', stroke: '#07101c', 'stroke-width': 2 }});
  }}

  el('text', {{
    x: M.l + PW - 10, y: M.t + 28,
    'text-anchor': 'end', 'font-size': 26, 'font-weight': 700,
    fill: '#e5edf6', 'fill-opacity': 0.22, 'letter-spacing': 0.5,
    'font-family': '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif'
  }}).textContent = '@WeathermanAAA_';

  const cross = el('line', {{ x1: 0, x2: 0, y1: M.t, y2: M.t + PH,
    stroke: '#8ea2bd', 'stroke-width': 1, 'stroke-dasharray': '3 3',
    opacity: 0 }});
  const dotCurrent = el('circle', {{ r: 4, fill: '#ffb83a',
    stroke: '#07101c', 'stroke-width': 1.5, opacity: 0 }});
  const dotPrior = el('circle', {{ r: 3.5, fill: '#c084fc',
    stroke: '#07101c', 'stroke-width': 1.5, opacity: 0 }});
  const dotMean = el('circle', {{ r: 3.5, fill: '#5dd3ff',
    stroke: '#07101c', 'stroke-width': 1.5, opacity: 0 }});
  const dotSel = el('circle', {{ r: 4, fill: '#ff4dd2',
    stroke: '#07101c', 'stroke-width': 1.5, opacity: 0 }});

  window.WPAceChart = {{
    setSelectedYear(year) {{
      while (selGroup.firstChild) selGroup.removeChild(selGroup.firstChild);
      selectedYear = null;
      dotSel.setAttribute('opacity', 0);
      if (year == null) return;
      const vals = DATA.all_years && DATA.all_years[year];
      if (!vals) return;
      selectedYear = year;
      let d = '';
      for (let i = 0; i < vals.length; i++)
        d += (i === 0 ? 'M' : 'L') + xs(DATA.doy[i]) + ',' + ys(vals[i]) + ' ';
      const p = document.createElementNS(NS, 'path');
      p.setAttribute('d', d);
      p.setAttribute('fill', 'none');
      p.setAttribute('stroke', '#ff4dd2');
      p.setAttribute('stroke-width', 2.5);
      p.setAttribute('stroke-linejoin', 'round');
      p.setAttribute('stroke-linecap', 'round');
      selGroup.appendChild(p);
      const lastIdx = vals.length - 1;
      const endDot = document.createElementNS(NS, 'circle');
      endDot.setAttribute('cx', xs(DATA.doy[lastIdx]));
      endDot.setAttribute('cy', ys(vals[lastIdx]));
      endDot.setAttribute('r', 3.5);
      endDot.setAttribute('fill', '#ff4dd2');
      endDot.setAttribute('stroke', '#07101c');
      endDot.setAttribute('stroke-width', 1.5);
      selGroup.appendChild(endDot);
      const label = document.createElementNS(NS, 'text');
      label.setAttribute('x', xs(DATA.doy[lastIdx]) - 6);
      label.setAttribute('y', ys(vals[lastIdx]) - 6);
      label.setAttribute('text-anchor', 'end');
      label.setAttribute('font-size', 12);
      label.setAttribute('font-weight', 700);
      label.setAttribute('fill', '#ff4dd2');
      label.textContent = year;
      selGroup.appendChild(label);
    }},
    getSelectedYear() {{ return selectedYear; }}
  }};

  function doyToDate(doy, year) {{
    const d = new Date(Date.UTC(year, 0, 1));
    d.setUTCDate(doy);
    return d.toLocaleDateString(undefined,
      {{ month: 'short', day: 'numeric' }});
  }}

  function onMove(evt) {{
    const pt = svg.createSVGPoint();
    const src = evt.touches ? evt.touches[0] : evt;
    pt.x = src.clientX; pt.y = src.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM().inverse());
    if (p.x < M.l || p.x > M.l + PW) {{ onLeave(); return; }}
    const doy = Math.max(xMin, Math.min(xMax,
      Math.round(xMin + (p.x - M.l) / PW * (xMax - xMin))));

    cross.setAttribute('x1', xs(doy));
    cross.setAttribute('x2', xs(doy));
    cross.setAttribute('opacity', 1);

    const idx = doy - 1;
    const curIdx = DATA.current.doy.indexOf(doy);
    const curVal = curIdx >= 0 ? DATA.current.values[curIdx] :
      (doy > DATA.current.doy[DATA.current.doy.length - 1]
        ? null : DATA.current.values[DATA.current.values.length - 1]);
    const meanVal = DATA.climo.mean[idx];
    const priorVal = DATA.prior_year.values ? DATA.prior_year.values[idx] : null;
    const selVal = selectedYear && DATA.all_years[selectedYear]
      ? DATA.all_years[selectedYear][idx] : null;

    if (curVal != null) {{
      dotCurrent.setAttribute('cx', xs(doy));
      dotCurrent.setAttribute('cy', ys(curVal));
      dotCurrent.setAttribute('opacity', 1);
    }} else dotCurrent.setAttribute('opacity', 0);
    dotMean.setAttribute('cx', xs(doy));
    dotMean.setAttribute('cy', ys(meanVal));
    dotMean.setAttribute('opacity', 1);
    if (priorVal != null) {{
      dotPrior.setAttribute('cx', xs(doy));
      dotPrior.setAttribute('cy', ys(priorVal));
      dotPrior.setAttribute('opacity', 1);
    }} else dotPrior.setAttribute('opacity', 0);
    if (selVal != null) {{
      dotSel.setAttribute('cx', xs(doy));
      dotSel.setAttribute('cy', ys(selVal));
      dotSel.setAttribute('opacity', 1);
    }} else dotSel.setAttribute('opacity', 0);

    const rect = box.getBoundingClientRect();
    const svgRect = svg.getBoundingClientRect();
    const scale = svgRect.width / W;
    const tipX = (xs(doy) * scale) + (svgRect.left - rect.left);
    const tipY = (M.t * scale) + (svgRect.top - rect.top) + 6;
    tip.style.left = tipX + 'px';
    tip.style.top = tipY + 'px';
    tip.style.opacity = 1;
    const fmt = v => (v == null ? '—' : v.toFixed(1));
    const label = doyToDate(doy, parseInt(DATA.current.label));
    tip.innerHTML =
      '<div class="date">' + label + ' (DOY ' + doy + ')</div>' +
      (curVal != null ? row('#ffb83a', DATA.current.label, fmt(curVal)) : '') +
      (selVal != null ? row('#ff4dd2', String(selectedYear), fmt(selVal)) : '') +
      (priorVal != null ? row('#c084fc', DATA.prior_year.label, fmt(priorVal)) : '') +
      row('#5dd3ff', 'Climo mean', fmt(meanVal)) +
      row('transparent', '10–90%',
        fmt(DATA.climo.p10[idx]) + ' – ' + fmt(DATA.climo.p90[idx]));
  }}
  function row(c, name, val) {{
    return '<div class="row"><span class="dot" style="background:'
      + c + '"></span><span>' + name + ':</span><b>' + val + '</b></div>';
  }}
  function onLeave() {{
    cross.setAttribute('opacity', 0);
    dotCurrent.setAttribute('opacity', 0);
    dotPrior.setAttribute('opacity', 0);
    dotMean.setAttribute('opacity', 0);
    dotSel.setAttribute('opacity', 0);
    tip.style.opacity = 0;
  }}

  svg.addEventListener('mousemove', onMove);
  svg.addEventListener('mouseleave', onLeave);
  svg.addEventListener('touchmove', onMove, {{ passive: true }});
  svg.addEventListener('touchend', onLeave);

  function niceStep(x) {{
    if (!isFinite(x) || x <= 0) return 1;
    const pow = Math.pow(10, Math.floor(Math.log10(x)));
    const n = x / pow;
    let step;
    if (n < 1.5) step = 1;
    else if (n < 3) step = 2;
    else if (n < 7) step = 5;
    else step = 10;
    return step * pow;
  }}
}})();

(function () {{
  const body = document.getElementById('rankBody');
  const lbl = document.getElementById('currentRankLbl');
  const scroll = document.getElementById('rankScroll');
  const clearBtn = document.getElementById('clearSelBtn');
  const rows = DATA.rankings || [];
  let currentRow = null;
  let selectedTr = null;

  function applyRankLabel(year) {{
    if (year) {{
      lbl.textContent = 'Viewing ' + year;
      lbl.style.color = '#ff4dd2';
      clearBtn.classList.add('show');
    }} else if (DATA.current_rank) {{
      lbl.textContent = DATA.current.label + ' · Rank ' + DATA.current_rank +
        ' of ' + DATA.total_seasons;
      lbl.style.color = '';
      clearBtn.classList.remove('show');
    }}
  }}

  function select(tr, year) {{
    const wasSelected = (selectedTr === tr);
    if (selectedTr) selectedTr.classList.remove('is-selected');
    if (wasSelected) {{
      selectedTr = null;
      window.WPAceChart && window.WPAceChart.setSelectedYear(null);
      applyRankLabel(null);
      return;
    }}
    selectedTr = tr;
    tr.classList.add('is-selected');
    window.WPAceChart && window.WPAceChart.setSelectedYear(year);
    applyRankLabel(year);
  }}

  rows.forEach(r => {{
    const tr = document.createElement('tr');
    if (r.current) tr.classList.add('is-current');
    tr.dataset.year = r.year;
    tr.innerHTML =
      '<td class="rank-col">' + r.rank + '</td>' +
      '<td>' + r.year + '</td>' +
      '<td class="num">' + r.ytd.toFixed(2) + '</td>' +
      '<td class="num">' + r.total.toFixed(2) + '</td>';
    tr.addEventListener('click', () => select(tr, r.year));
    body.appendChild(tr);
    if (r.current) currentRow = tr;
  }});

  clearBtn.addEventListener('click', () => {{
    if (selectedTr) select(selectedTr, Number(selectedTr.dataset.year));
  }});

  applyRankLabel(null);

  if (currentRow) {{
    requestAnimationFrame(() => {{
      const topOffset = currentRow.offsetTop - scroll.clientHeight / 2 +
        currentRow.offsetHeight / 2;
      scroll.scrollTop = Math.max(0, topOffset);
    }});
  }}
}})();
</script>
</body>
</html>
"""


def build_payload(cum: pd.DataFrame, climo: pd.DataFrame, current_year: int,
                  prior_year: int | None, last_obs_doy: dict[int, int]) -> dict:
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
    }


def render_html(payload: dict, basin_cfg: dict, current_year: int,
                climo_start: int, climo_end: int, live_used: bool) -> str:
    live_note = f" + live {basin_cfg['agency_name']} b-deck" if live_used else ""
    return HTML_TEMPLATE.format(
        payload=json.dumps(payload, separators=(",", ":")),
        basin_full_name=basin_cfg["full_name"],
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
            # Merge strategy: only drop IBTrACS rows for storms whose NAMES
            # are in live data. This preserves IBTrACS storms that live
            # didn't pick up (e.g., early-season storms whose ATCF files
            # got archived). Matches the tracks generator's behavior so
            # the two charts stay consistent.
            live_names = {
                str(n).strip().upper()
                for n in live["NAME"].unique()
                if pd.notna(n) and str(n).strip()
                and str(n).strip().upper() not in {"", "UNNAMED", "INVEST", "NAMELESS"}
            }
            if live_names:
                current_year_mask = points["season"] == current_year
                name_mask = points["NAME"].fillna("").str.strip().str.upper().isin(live_names)
                drop_mask = current_year_mask & name_mask
                dropped = int(drop_mask.sum())
                if dropped:
                    print(f"{log}   merge: dropped {dropped} IBTrACS row(s) "
                          f"for storms covered by live: {sorted(live_names)}")
                points = points[~drop_mask].copy()
            else:
                # Live has unnamed storms only — drop the whole current
                # year from IBTrACS to avoid duplicates anyway.
                points = points[points["season"] != current_year]
            points = pd.concat([points, live], ignore_index=True)
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
    payload = build_payload(cum, climo, current_year, prior_year, last_obs_doy)
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
