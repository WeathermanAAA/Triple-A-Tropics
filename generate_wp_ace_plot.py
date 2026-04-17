#!/usr/bin/env python3
"""
generate_wp_ace_plot.py
-----------------------
Generate a real-time, interactive plot of Accumulated Cyclone Energy (ACE)
for the Western North Pacific basin (WPAC) and write it out as a standalone
HTML file you can embed on your website.

Outputs (next to this script, or wherever you point OUTPUT_DIR):
  - wp_ace.html       Self-contained interactive Plotly chart (CDN-loaded)
  - wp_ace_data.json  Processed data (same numbers that feed the chart)

Inputs:
  - IBTrACS WP CSV (historical climatology + current-season provisional data)
    Download: https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.WP.list.v04r01.csv
  - Optionally: live JTWC ATCF best-track files for the current season
    (the script tries a few URL patterns; if none are reachable it silently
    falls back to whatever the IBTrACS file already contains.)

Run it on a schedule (cron / systemd timer / scheduled task) and the HTML
updates itself. Drop the HTML into your site or iframe it in.

Author: generated for Andrew
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
IBTRACS_CSV = Path(
    os.environ.get(
        "IBTRACS_WP_CSV",
        str(HERE / "ibtracs.WP.list.v04r01.csv"),
    )
)
OUTPUT_DIR = Path(os.environ.get("WP_ACE_OUTPUT_DIR", str(HERE)))
OUTPUT_HTML = OUTPUT_DIR / "wp_ace.html"
OUTPUT_JSON = OUTPUT_DIR / "wp_ace_data.json"

# 30-year climatology window used by most operational centers
CLIMO_START = 1991
CLIMO_END = 2020

# Seasons considered "modern" (reliable satellite era). Older data exists
# but winds before the geostationary era are less comparable.
MODERN_START = 1970

# Fetch live JTWC data? Set to False to force pure-IBTrACS mode.
FETCH_LIVE = True
FETCH_TIMEOUT = 10  # seconds per request

# Where to pull current-season ATCF best-track files. JTWC doesn't publish
# a stable directory index, so we try several known patterns. The first
# one that returns storms wins. If none work, we just skip the fetch.
JTWC_ATCF_PATTERNS = [
    # Official JTWC public ATCF btk directory
    "https://www.metoc.navy.mil/jtwc/products/atcf/btk/bwp{nn}{yy}.dat",
    # NRL mirror variants
    "https://www.nrlmry.navy.mil/atcf_web/docs/tracks/{year}/bwp{nn}{yy}.dat",
    "https://www.nrlmry.navy.mil/atcf_web/docs/tracks/{year}/WP{nn}{year}/bwp{nn}{yy}.dat",
    # University of Wisconsin CIMSS real-time ATCF mirror
    "https://tropic.ssec.wisc.edu/real-time/atcf/btk/bwp{nn}{yy}.dat",
    # NOAA SSD mirror
    "https://www.ssd.noaa.gov/PS/TROP/DATA/ATCF/JTWC/bwp{nn}{yy}.dat",
]

# Browser-like User-Agent — plain "python-urllib" gets blocked by many
# .mil and .gov sites. Mimicking a browser is what every real-world
# tropical cyclone data scraper has to do.
FETCH_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/17.0 Safari/605.1.15")

# ---------------------------------------------------------------------------
# ACE helpers
# ---------------------------------------------------------------------------

SIX_HOURLY = {0, 6, 12, 18}
# ACE is summed for tropical systems at tropical-storm strength or greater.
# IBTrACS NATURE codes: TS = tropical, SS = subtropical, ET = extratropical,
# DS = disturbance, MX = mixed, NR = not reported. For WPac operational ACE
# the convention is tropical-only.
ACE_NATURES = {"TS"}


def _best_wind(row: pd.Series) -> float:
    """Prefer JTWC (USA_WIND, 1-min sustained kt); fall back to WMO/TOKYO
    (10-min sustained kt) converted to 1-min with the WMO factor of 0.88.
    Returns NaN if nothing is available."""
    w = row.get("USA_WIND")
    if pd.notna(w):
        return float(w)
    w = row.get("WMO_WIND")
    if pd.notna(w):
        return float(w) / 0.88
    w = row.get("TOKYO_WIND")
    if pd.notna(w):
        return float(w) / 0.88
    return np.nan


def compute_ace_timeseries(df: pd.DataFrame) -> pd.DataFrame:
    """Return a dataframe with columns [season, doy, ace_increment] at
    6-hourly resolution for the Western North Pacific."""
    d = df.copy()
    d = d[d["BASIN"] == "WP"]
    d = d[d["TRACK_TYPE"].isin(["main", "PROVISIONAL"])]
    d["ISO_TIME"] = pd.to_datetime(d["ISO_TIME"], errors="coerce")
    d = d.dropna(subset=["ISO_TIME"])

    # Keep only 00/06/12/18 UTC observations — standard ACE convention
    hours = d["ISO_TIME"].dt.hour
    minutes = d["ISO_TIME"].dt.minute
    d = d[hours.isin(SIX_HOURLY) & (minutes == 0)]

    # Unified 1-min sustained wind in knots
    for c in ("USA_WIND", "WMO_WIND", "TOKYO_WIND"):
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    d["WIND_KT"] = d.apply(_best_wind, axis=1)

    # Only count tropical-phase points at TS strength or greater.
    # IBTrACS backfills NATURE from "NR" to "TS" only after post-season QC,
    # so for PROVISIONAL tracks (current + very recent seasons) we accept
    # NR as tropical — JTWC wouldn't be issuing warnings on these points
    # otherwise.
    is_tropical = d["NATURE"].isin(ACE_NATURES) | (
        (d["TRACK_TYPE"] == "PROVISIONAL") & d["NATURE"].isin(ACE_NATURES | {"NR"})
    )
    d = d[is_tropical]
    d = d[d["WIND_KT"] >= 34]

    d["ACE"] = (d["WIND_KT"] ** 2) / 10_000.0
    d["doy"] = d["ISO_TIME"].dt.dayofyear
    d["season"] = d["SEASON"].astype(int)

    # Deduplicate: a storm can appear in IBTrACS multiple times (main +
    # spur) — after the TRACK_TYPE filter we should be clean, but be safe.
    d = d.drop_duplicates(subset=["SID", "ISO_TIME"])

    return d[["season", "doy", "ACE", "SID", "NAME"]].rename(columns={"ACE": "ace_increment"})


def cumulative_by_doy(points: pd.DataFrame) -> pd.DataFrame:
    """Given per-6-hour ACE increments, return a dense (season x doy)
    matrix of cumulative ACE. Day 1..366."""
    # Sum increments per (season, doy) then cumulative along doy
    g = points.groupby(["season", "doy"], as_index=False)["ace_increment"].sum()
    seasons = sorted(g["season"].unique())
    doys = np.arange(1, 367)
    out = pd.DataFrame(index=doys, columns=seasons, dtype=float).fillna(0.0)
    for (s, doy), inc in g.set_index(["season", "doy"])["ace_increment"].items():
        out.at[doy, s] = inc
    cum = out.cumsum(axis=0)
    cum.index.name = "doy"
    return cum  # rows=doy, cols=season


def climatology(cum: pd.DataFrame, start: int, end: int) -> pd.DataFrame:
    """Compute day-of-year climatology percentiles across the given
    season window."""
    cols = [s for s in cum.columns if start <= s <= end]
    climo = cum[cols]
    out = pd.DataFrame(index=cum.index)
    out["p10"] = climo.quantile(0.10, axis=1)
    out["p25"] = climo.quantile(0.25, axis=1)
    out["p50"] = climo.quantile(0.50, axis=1)
    out["p75"] = climo.quantile(0.75, axis=1)
    out["p90"] = climo.quantile(0.90, axis=1)
    out["mean"] = climo.mean(axis=1)
    out["min"] = climo.min(axis=1)
    out["max"] = climo.max(axis=1)
    return out


# ---------------------------------------------------------------------------
# Optional live fetch from JTWC ATCF
# ---------------------------------------------------------------------------

ATCF_LINE = re.compile(r"^(?P<basin>\w\w),\s*(?P<num>\d+),\s*(?P<ts>\d{10}),")


def _parse_atcf(text: str, season: int) -> pd.DataFrame:
    """Parse an ATCF b-deck file into a 6-hourly points dataframe with the
    same schema as compute_ace_timeseries output."""
    rows = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 10:
            continue
        try:
            basin = parts[0]
            storm_num = int(parts[1])
            tstamp = parts[2]
            # parts[3] is technical/model number (usually blank for BEST)
            tech = parts[4] if len(parts) > 4 else ""
            # parts[5] = TAU (forecast hour)
            lat_raw = parts[6]
            lon_raw = parts[7]
            vmax = parts[8]
            # parts[10] = development level (TS, TY, STY, TD, EX, ...)
            devlvl = parts[10] if len(parts) > 10 else ""
        except (IndexError, ValueError):
            continue
        if tech != "BEST":
            continue
        try:
            t = dt.datetime.strptime(tstamp, "%Y%m%d%H")
        except ValueError:
            continue
        if t.hour not in SIX_HOURLY:
            continue
        try:
            vmax = float(vmax)
        except ValueError:
            continue
        if vmax < 34:
            continue
        if devlvl not in {"TS", "TY", "STY", "HU"}:  # tropical only
            continue
        rows.append(
            {
                "season": season,
                "doy": t.timetuple().tm_yday,
                "ace_increment": (vmax ** 2) / 10_000.0,
                "SID": f"JTWC_WP{storm_num:02d}{season}",
                "NAME": "",
            }
        )
    return pd.DataFrame(rows)


def fetch_live_season(season: int) -> pd.DataFrame:
    """Try to pull current-season b-deck files from JTWC/NRL/mirrors.
    Returns an empty frame if no source is reachable. Logs a one-line
    diagnostic for each pattern so you can see exactly why it failed."""
    try:
        import urllib.request
    except Exception:
        return pd.DataFrame()

    yy = season % 100
    frames = []
    # Per-pattern diagnostic: fail type -> count, sample URL/error
    pattern_stats: dict[str, dict] = {p: {"ok": 0, "errors": {}} for p in JTWC_ATCF_PATTERNS}

    # Consecutive-miss short-circuit: if we go 3 storms in a row with no
    # file found on ANY pattern, assume there aren't more active storms.
    consecutive_misses = 0

    for nn in range(1, 41):
        hit_this_nn = False
        for pattern in JTWC_ATCF_PATTERNS:
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
                            frames.append(_parse_atcf(text, season))
                            pattern_stats[pattern]["ok"] += 1
                            hit_this_nn = True
                            break  # got this storm, move to next nn
            except urllib.error.HTTPError as e:
                err_key = f"http_{e.code}"
            except Exception as e:
                err_key = type(e).__name__
            if err_key:
                stats = pattern_stats[pattern]["errors"]
                stats[err_key] = stats.get(err_key, 0) + 1
        consecutive_misses = 0 if hit_this_nn else consecutive_misses + 1
        if consecutive_misses >= 3:
            break

    # Print a diagnostic so the GitHub Actions log shows what's going on
    total_hits = sum(s["ok"] for s in pattern_stats.values())
    print(f"[wp-ace]   live fetch results: {total_hits} storm file(s) found")
    for pattern, s in pattern_stats.items():
        host = pattern.split("/")[2]
        if s["ok"]:
            print(f"[wp-ace]     {host}: ✓ {s['ok']} ok")
        elif s["errors"]:
            err_summary = ", ".join(f"{k}×{v}" for k, v in s["errors"].items())
            print(f"[wp-ace]     {host}: {err_summary}")

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# HTML rendering (Plotly via CDN, no Python plotly dep required)
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>West Pacific TC ACE — {current_year}</title>
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
  <h1>Western North Pacific — Accumulated Cyclone Energy</h1>
  <div class="sub">Season {current_year} vs {climo_start}–{climo_end} climatology · last updated {updated}</div>
  <div class="legend">
    <span><span class="sw" style="background:var(--band-wide)"></span>Historical range</span>
    <span><span class="sw" style="background:var(--band-mid)"></span>10–90th pct</span>
    <span><span class="sw" style="background:var(--band-narrow)"></span>25–75th pct</span>
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
  ) * 1.05;

  const xs = d => M.l + (d - xMin) / (xMax - xMin) * PW;
  const ys = v => M.t + PH - (v / yMax) * PH;

  const NS = 'http://www.w3.org/2000/svg';
  const el = (tag, attrs = {{}}, parent = svg) => {{
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    parent.appendChild(e);
    return e;
  }};

  // Gridlines + y-axis ticks
  const ySteps = 5;
  const yStep = niceStep(yMax / ySteps);
  for (let v = 0; v <= yMax; v += yStep) {{
    el('line', {{ x1: M.l, x2: M.l + PW, y1: ys(v), y2: ys(v),
      stroke: '#1a2840', 'stroke-width': 1 }});
    el('text', {{ x: M.l - 8, y: ys(v) + 4, 'text-anchor': 'end',
      'font-size': 11, fill: '#8ea2bd' }}).textContent = Math.round(v);
  }}
  // Y-axis label
  el('text', {{ x: 14, y: M.t + PH / 2, 'text-anchor': 'middle',
    'font-size': 12, fill: '#a8b8d1',
    transform: `rotate(-90 14 ${{M.t + PH / 2}})` }})
    .textContent = 'Cumulative ACE (×10⁴ kt²)';

  // Month ticks
  const monthStarts = [1,32,60,91,121,152,182,213,244,274,305,335];
  const monthLabels = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  monthStarts.forEach((d, i) => {{
    el('line', {{ x1: xs(d), x2: xs(d), y1: M.t, y2: M.t + PH,
      stroke: '#142036', 'stroke-width': 1 }});
    el('text', {{ x: xs(d + 15), y: M.t + PH + 18, 'text-anchor': 'middle',
      'font-size': 11, fill: '#8ea2bd' }}).textContent = monthLabels[i];
  }});
  // X-axis baseline
  el('line', {{ x1: M.l, x2: M.l + PW, y1: M.t + PH, y2: M.t + PH,
    stroke: '#2f4666', 'stroke-width': 1 }});

  // Filled band helper
  function band(upper, lower, fill) {{
    let d = '';
    for (let i = 0; i < DATA.doy.length; i++)
      d += (i === 0 ? 'M' : 'L') + xs(DATA.doy[i]) + ',' + ys(upper[i]) + ' ';
    for (let i = DATA.doy.length - 1; i >= 0; i--)
      d += 'L' + xs(DATA.doy[i]) + ',' + ys(lower[i]) + ' ';
    d += 'Z';
    el('path', {{ d, fill, stroke: 'none' }});
  }}
  // Percentile bands — glowy blue fills that work on dark
  band(DATA.climo.max, DATA.climo.min, 'rgba(70,140,200,0.12)');
  band(DATA.climo.p90, DATA.climo.p10, 'rgba(70,180,220,0.20)');
  band(DATA.climo.p75, DATA.climo.p25, 'rgba(80,210,240,0.30)');

  // Percentile contour isopleths — thin solid lines at every percentile
  // boundary so the statistical envelope reads like a weather chart
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
  // Outer envelope (min/max)
  linePath(DATA.doy, DATA.climo.min, '#567894', 0.9, null, 0.55);
  linePath(DATA.doy, DATA.climo.max, '#567894', 0.9, null, 0.55);
  // 10th / 90th
  linePath(DATA.doy, DATA.climo.p10, '#4db8e0', 1.0, null, 0.70);
  linePath(DATA.doy, DATA.climo.p90, '#4db8e0', 1.0, null, 0.70);
  // 25th / 75th — brightest + slightly thicker so IQR reads as the core
  linePath(DATA.doy, DATA.climo.p25, '#8ce3ff', 1.4, null, 0.90);
  linePath(DATA.doy, DATA.climo.p75, '#8ce3ff', 1.4, null, 0.90);

  // Line helper
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

  // Dedicated group for the user-selected year so we can swap it easily
  const selGroup = el('g', {{ id: 'selGroup' }});
  let selectedYear = null;

  line(DATA.current.doy, DATA.current.values, '#ffb83a', 3);

  // "Today" marker
  if (DATA.today_doy) {{
    el('circle', {{ cx: xs(DATA.today_doy), cy: ys(DATA.current.latest_value),
      r: 5, fill: '#ffb83a', stroke: '#07101c', 'stroke-width': 2 }});
  }}

  // Watermark — large, semi-transparent, top-right corner of the plot area
  el('text', {{
    x: M.l + PW - 10, y: M.t + 28,
    'text-anchor': 'end', 'font-size': 26, 'font-weight': 700,
    fill: '#e5edf6', 'fill-opacity': 0.22, 'letter-spacing': 0.5,
    'font-family': '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif'
  }}).textContent = '@WeathermanAAA_';

  // Hover crosshair + dots
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

  // ---- Public API: let the rank table toggle a selected-year overlay ----
  window.WPAceChart = {{
    setSelectedYear(year) {{
      // Clear previous selection
      while (selGroup.firstChild) selGroup.removeChild(selGroup.firstChild);
      selectedYear = null;
      dotSel.setAttribute('opacity', 0);
      if (year == null) return;
      const vals = DATA.all_years && DATA.all_years[year];
      if (!vals) return;
      selectedYear = year;
      // Draw the selected-year line inside the group, pink to stand out
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
      // End-of-year label dot so it's obvious which curve is which
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

// ---- Side table: season ranking + click-to-overlay a year ----
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

  // Auto-scroll the current-year row into the middle of the scroll area
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

    def series(year: int | None) -> dict:
        if year is None or year not in cum.columns:
            return {}
        vals = cum[year].values.astype(float)
        if year == current_year:
            # End the current-year line at max(last_observation_doy, today).
            # That way if no storm has spun up yet, the line still reaches
            # today at ACE=0; and if the season is in a lull after a storm,
            # the flat portion between last storm and today is shown.
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

    # Per-season ranking: Total ACE (year-end cum) and YTD ACE (cum at
    # today's DOY). Sorted by YTD ACE descending — i.e., "where does this
    # year stand compared to every other year AT THIS POINT in the season".
    doy_cutoff = today_doy_real or 366
    rank_rows = []
    for year in sorted(cum.columns):
        col = cum[year].values.astype(float)
        total = float(col[-1])
        ytd = float(col[min(doy_cutoff, len(col)) - 1])
        rank_rows.append({"year": int(year), "ytd": round(ytd, 2),
                          "total": round(total, 2),
                          "current": int(year) == current_year})
    # Sort by YTD ACE desc, tiebreak on Total ACE desc, then newer year first
    rank_rows.sort(key=lambda r: (-r["ytd"], -r["total"], -r["year"]))
    # Assign 1-based rank
    for i, r in enumerate(rank_rows, start=1):
        r["rank"] = i
    current_rank = next((r["rank"] for r in rank_rows if r["current"]), None)

    # Every season's cumulative ACE curve (rounded to 1 decimal to keep
    # the payload compact — day-by-day values for 80+ years add up).
    all_years = {}
    for year in sorted(cum.columns):
        vals = cum[year].values.astype(float)
        # Only include years with at least one TS (skip pre-1945 empty rows)
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


def render_html(payload: dict, current_year: int, climo_start: int,
                climo_end: int, live_used: bool) -> str:
    live_note = " + live JTWC b-deck" if live_used else ""
    return HTML_TEMPLATE.format(
        payload=json.dumps(payload, separators=(",", ":")),
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
    if not IBTRACS_CSV.exists():
        print(f"ERROR: IBTrACS file not found at {IBTRACS_CSV}", file=sys.stderr)
        print("Set IBTRACS_WP_CSV env var or drop the CSV next to this script.", file=sys.stderr)
        return 2

    print(f"[wp-ace] loading {IBTRACS_CSV} ...")
    df = pd.read_csv(
        IBTRACS_CSV,
        skiprows=[1],  # drop the units header row
        low_memory=False,
        na_values=[" ", ""],
    )
    print(f"[wp-ace] {len(df):,} rows")

    points = compute_ace_timeseries(df)
    print(f"[wp-ace] {len(points):,} 6-hour points contribute to ACE "
          f"(seasons {points['season'].min()}–{points['season'].max()})")

    current_year = int(points["season"].max())
    prior_year = current_year - 1

    live_used = False
    if FETCH_LIVE:
        print(f"[wp-ace] attempting live JTWC fetch for {current_year} ...")
        live = fetch_live_season(current_year)
        if not live.empty:
            print(f"[wp-ace] pulled {len(live)} live 6-hour points from JTWC")
            # Replace IBTrACS current-year points with the fresher live feed
            points = points[points["season"] != current_year]
            points = pd.concat([points, live], ignore_index=True)
            live_used = True
        else:
            print("[wp-ace] live fetch returned nothing — using IBTrACS provisional data only")

    cum = cumulative_by_doy(points)
    climo = climatology(cum, CLIMO_START, CLIMO_END)

    last_obs_doy = points.groupby("season")["doy"].max().to_dict()
    payload = build_payload(cum, climo, current_year, prior_year, last_obs_doy)
    html = render_html(payload, current_year, CLIMO_START, CLIMO_END, live_used)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    ytd = payload["current"].get("latest_value", 0.0)
    climo_today = None
    if payload["today_doy"]:
        idx = payload["today_doy"] - 1
        if 0 <= idx < len(payload["climo"]["mean"]):
            climo_today = payload["climo"]["mean"][idx]

    print(f"[wp-ace] wrote {OUTPUT_HTML}")
    print(f"[wp-ace] wrote {OUTPUT_JSON}")
    print(f"[wp-ace] {current_year} YTD ACE: {ytd:.1f}"
          + (f"  (climo avg to-date: {climo_today:.1f})" if climo_today else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
