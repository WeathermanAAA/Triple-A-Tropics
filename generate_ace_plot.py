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
import math
import os
import sys
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

# Single source of truth for every ACE number, the live b-deck parser, the
# IBTrACS-vs-live storm merge, and the freshness timestamps. This generator no
# longer decides ACE methodology on its own - it routes through ace_core so the
# homepage strip, the climatology page, and the tracks graphic all report the
# IDENTICAL season ACE and per-storm peaks.
import ace_core as ac
from ace_core import jtwc_live
from ace_core import (
    build_payload,
    climatology,
    cumulative_by_doy,
    current_year_storms,
    eligible_points_from_canon,
    extract_gantt_storms_by_year,
    extract_storms_by_year,
)

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
        # Wind preference, ACE-eligible NATURE set, and the v^2/10000 formula
        # all live in ace_core now (ac.WIND_PREFERENCE / ac.ACE_NATURES). WP
        # counts tropical AND subtropical there ({TS, SS, SD}), to match CSU.
        #
        # Second leg for JTWC basins (ace_core.jtwc_live): NCEP tcvitals for the
        # numbers + JTWC public warning text for the storm type. JTWC's a-decks
        # are gone and the b-decks reach us only through an unofficial mirror
        # that lags a synoptic cycle, so this is both the freshness fix (Noul
        # was rendering C1 off a 12Z b-deck while 18Z tcvitals had it at C2) and
        # the fallback if that mirror stops. OFF for the NHC basins below:
        # ftp.nhc.noaa.gov serves their decks directly and on time, so there is
        # nothing to gain and their output stays byte-identical.
        "tcvitals": True,
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
            # NHC only (proxy -> ftp.nhc). natyphoon.top is a West-Pacific/JTWC
            # mirror; it does NOT serve AL/EP b-decks (404s here, SSL-fails from
            # some cloud hosts), so it is reserved for WP below.
        ],
        # Methodology (wind preference, NATURE set, formula) lives in ace_core.
        # NHC counts tropical AND subtropical at 34 kt+ (ac.ACE_NATURES["al"] =
        # {TS, SS}), matching the official published numbers (e.g. 2005 Atlantic
        # ACE = 245.47, which counts Subtropical Storm Arlene).
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
            # NHC only (proxy -> ftp.nhc); natyphoon.top is WP/JTWC-only (see AL).
        ],
        # Designated Central Pacific systems live in CPHC's own bcp decks
        # (TD 01C = bcp01<year>) — swept separately so their live ACE counts
        # here, matching the historical basis (IBTrACS files CP under
        # BASIN=EP; NOAA's EP seasonal ACE includes the Central Pacific).
        "atcf_patterns_extra": [[
            "https://triple-a-tropics-proxy.coloradoskier2018.workers.dev/atcf/btk/bcp{nn}{year}.dat",
            "https://ftp.nhc.noaa.gov/atcf/btk/bcp{nn}{year}.dat",
        ]],
        # Methodology lives in ace_core (NHC, same as Atlantic:
        # ac.ACE_NATURES["ep"] = {TS, SS}).
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

# R2 live-feed base. The ACE page's client-side overlay refetches
# {basin}_ace_data.json from here at view time (cache-busted + no-store) so the
# chart tracks the poller, not this cron's 6-hourly bake — the same live path
# the home status panel and global map already read. Must match the key the
# update-ace workflow uploads to (feeds/{basin}_ace_data.json).
FEEDS_BASE_URL = "https://cdn.triple-a-tropics.com/feeds/"

# Browser-like User-Agent — .mil/.gov sites routinely block plain urllib.
FETCH_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/17.0 Safari/605.1.15")

SIX_HOURLY = {0, 6, 12, 18}


# ---------------------------------------------------------------------------
# ACE computation (basin-parameterized)
# ---------------------------------------------------------------------------

def compute_ace_timeseries(df: pd.DataFrame, basin_cfg: dict,
                           log_prefix: str = "") -> tuple:
    """Return ``(points, trop)``:
    - ``points``: the ACE-eligible (>=34 kt, tropical/subtropical NATURE)
      6-hourly frame [season, doy, ace_increment, SID, NAME, ISO_TIME, WIND_KT]
      that feeds every ACE number (unchanged behavior).
    - ``trop``: the TD-inclusive tropical frame (same NATURE filter, ANY wind)
      [season, doy, SID, NAME, ISO_TIME, WIND_KT, ATCF] that feeds the
      Storm-Activity Gantt so TD-strength systems get a bar.
    Logs per-step row counts so we can see which filter drops everything if
    something goes wrong."""
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

    # Convert wind columns to numeric. Wind preference (column order +
    # 10-min->1-min factor) is single-sourced from ace_core.
    short = basin_cfg["short"]
    for col, _ in ac.WIND_PREFERENCE[short]:
        if col in d.columns:
            d[col] = pd.to_numeric(d[col], errors="coerce")
    d["WIND_KT"] = d.apply(lambda r: ac.best_wind(r, short), axis=1)

    # Nature filter. The per-basin ACE-eligible NATURE set is single-sourced
    # from ace_core (ac.ACE_NATURES): WP {TS, SS, SD}, AL/EP {TS, SS} - all
    # count tropical AND subtropical, matching CSU.
    # IBTrACS backfills NATURE="NR" to "TS" only after post-season QC, so
    # for PROVISIONAL rows we also accept NR.
    ace_natures = set(ac.ACE_NATURES[short])
    is_tropical = d["NATURE"].isin(ace_natures) | (
        (d["TRACK_TYPE"] == "PROVISIONAL") & d["NATURE"].isin(ace_natures | {"NR"})
    )
    d = step(f"NATURE in {sorted(ace_natures)} (+NR on provisional)", d[is_tropical])

    # TD-inclusive tropical frame (this is the post-NATURE, pre-34kt cut). Used
    # only for the Storm-Activity Gantt so TD-strength systems get a bar; it does
    # NOT touch any ACE number. Carry USA_ATCF_ID through (for unnamed-but-
    # designated labeling) and dedup once on (SID, ISO_TIME) like the ACE frame.
    d["doy"] = d["ISO_TIME"].dt.dayofyear
    d["season"] = d["SEASON"].astype(int)
    d = d.drop_duplicates(subset=["SID", "ISO_TIME"])
    if "USA_ATCF_ID" not in d.columns:
        d["USA_ATCF_ID"] = pd.NA
    trop = d[["season", "doy", "SID", "NAME", "ISO_TIME", "WIND_KT",
              "USA_ATCF_ID"]].rename(columns={"USA_ATCF_ID": "ATCF"})

    # ACE-eligible frame: apply the 34 kt cut to a SEPARATE copy so the ACE math
    # is byte-identical to before.
    pe = step("WIND_KT >= 34", d[d["WIND_KT"] >= 34].copy())
    pe["ACE"] = (pe["WIND_KT"] ** 2) / 10_000.0
    points = pe[["season", "doy", "ACE", "SID", "NAME", "ISO_TIME",
                 "WIND_KT"]].rename(columns={"ACE": "ace_increment"})
    return points, trop


# ---------------------------------------------------------------------------
# Live ATCF fetch
# ---------------------------------------------------------------------------

# The ATCF b-deck parser lives in ace_core.parse_bdeck now - the SINGLE parser
# both generators use, so a named storm has the same fix set (hence the same
# peak wind + ACE) everywhere. fetch_live_season returns parse_bdeck's schema
# (SID, NAME, season, time, wind_kt, nature, ace_nature, source, storm_num) -
# the FULL fix set (every nature, every wind); the ACE eligibility filter is
# applied later via ac.fix_ace_eligible, so the same frame also drives the
# freshness timestamp and the live-vs-IBTrACS merge.


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
    # One sweep per deck-prefix chain: EP also sweeps the CPHC bcp decks
    # via atcf_patterns_extra (designated Central Pacific systems).
    pattern_sets = ([basin_cfg["atcf_patterns"]]
                    + list(basin_cfg.get("atcf_patterns_extra") or []))
    pattern_stats: dict[str, dict] = {
        p: {"ok": 0, "errors": {}} for ps in pattern_sets for p in ps}

    for patterns in pattern_sets:
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
                                # Shared parser (ace_core) so the live fix set per
                                # named storm matches the tracks feed exactly.
                                frames.append(ac.parse_bdeck(text, season, basin_cfg))
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

    bdeck = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    # Second leg (JTWC basins only — see the "tcvitals" flag in BASINS). Adds
    # the fixes the b-deck has not written yet, typed from JTWC's own warning
    # text; anything it cannot type is carried but excluded from ACE. Degrades
    # to `bdeck` unchanged on any failure.
    extended, _ = jtwc_live.extend_with_tcvitals(
        bdeck, season, basin_cfg, log_prefix=log_prefix)
    return extended


# ---------------------------------------------------------------------------
# Current-year canonical set (single-sourced via ace_core)
# ---------------------------------------------------------------------------

# Schema of the parse_bdeck-style frames both halves of the merge share.
_CANON_COLS = ["SID", "NAME", "season", "time", "wind_kt",
               "nature", "ace_nature", "source", "storm_num"]


def current_year_ibtracs_fixes(df: pd.DataFrame, basin_cfg: dict, year: int,
                               log_prefix: str = "") -> pd.DataFrame:
    """The current-year IBTrACS half of the canonical set: ALL 6-hourly fixes,
    EVERY nature (no ACE filter), in parse_bdeck's schema so ac.merge_named_sources
    can union it with the live frame. ``ace_nature`` is the RAW IBTrACS NATURE -
    the same signal ace_core counts ACE on (and the same the tracks feed carries),
    so the two feeds agree. Wind via ac.best_wind."""
    d = df.copy()
    basin_codes = basin_cfg["ibtracs_basin_col"]
    if isinstance(basin_codes, str):
        basin_codes = [basin_codes]
    d_basin = d[d["BASIN"].isin(basin_codes)]
    if len(d_basin) > 0:
        d = d_basin
    d = d[d["TRACK_TYPE"].isin(["main", "PROVISIONAL"])].copy()
    d["ISO_TIME"] = pd.to_datetime(d["ISO_TIME"], errors="coerce")
    d = d.dropna(subset=["ISO_TIME"])
    d = d[d["SEASON"].astype("Int64") == year]
    if d.empty:
        return pd.DataFrame(columns=_CANON_COLS)
    hours = d["ISO_TIME"].dt.hour
    minutes = d["ISO_TIME"].dt.minute
    d = d[hours.isin(SIX_HOURLY) & (minutes == 0)].copy()
    short = basin_cfg["short"]
    for col, _ in ac.WIND_PREFERENCE[short]:
        if col in d.columns:
            d[col] = pd.to_numeric(d[col], errors="coerce")
    d["WIND_KT"] = d.apply(lambda r: ac.best_wind(r, short), axis=1)
    rows = []
    for _, row in d.iterrows():
        raw_nature = str(row.get("NATURE") or "").strip().upper()
        rows.append({
            "SID": row["SID"],
            "NAME": str(row.get("NAME") or "").strip() or "UNNAMED",
            "season": year,
            "time": row["ISO_TIME"].to_pydatetime(),
            "wind_kt": row["WIND_KT"],
            "nature": raw_nature,
            "ace_nature": raw_nature,
            "source": "IBTrACS",
            "storm_num": float("nan"),
        })
    out = pd.DataFrame(rows, columns=_CANON_COLS)
    print(f"{log_prefix}   current-year IBTrACS fixes (all natures): {len(out):,}")
    return out


# ---------------------------------------------------------------------------
# HTML rendering (self-contained dark SVG chart + ranking table)
# ---------------------------------------------------------------------------

from _ace_template import HTML_TEMPLATE  # noqa: E402


_BASIN_SHORT_LABELS = {"wp": "WPAC", "al": "AL", "ep": "EPAC"}


def render_html(payload: dict, basin_cfg: dict, current_year: int,
                climo_start: int, climo_end: int, live_used: bool,
                build_now: dt.datetime | None = None) -> str:
    live_note = f" + live {basin_cfg['agency_name']} b-deck" if live_used else ""
    short_label = _BASIN_SHORT_LABELS.get(basin_cfg["short"],
                                          basin_cfg["short"].upper())
    build_now = build_now or dt.datetime.utcnow()
    return HTML_TEMPLATE.format(
        payload=json.dumps(payload, separators=(",", ":")),
        basin_full_name=basin_cfg["full_name"],
        basin_short_label=short_label,
        current_year=current_year,
        climo_start=climo_start,
        climo_end=climo_end,
        updated=build_now.strftime("%Y-%m-%d %H:%M UTC"),
        # Basin-specific live feed the client-side overlay refetches at view
        # time; the URL alone pins the basin (the ACE feed carries no basin
        # field), so fetching {basin}_ace_data.json guarantees the right one.
        feed_url=f"{FEEDS_BASE_URL}{basin_cfg['short']}_ace_data.json",
        live_note=live_note,
        # Single source of truth for SSHWS category colors (TD blue … C1 yellow …
        # C5 purple). The chart never invents a palette.
        sshs_colors=json.dumps(ac.SSHS_COLORS, separators=(",", ":")),
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

    points, trop_points = compute_ace_timeseries(df, basin_cfg, log_prefix=log)
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

    build_now = dt.datetime.utcnow()

    # --- Current-year canonical ACE set (single-sourced via ace_core) --------
    # Build ONE current-year fix set every surface agrees on: full 6-hourly
    # IBTrACS fixes (every nature) merged with the live b-deck via the SAME
    # ace_core merge the tracks feed uses, then route every number (per-storm
    # ACE, season ACE, peak wind) through ace_core. This REPLACES the
    # IBTrACS-derived current-year slice that compute_ace_timeseries produced,
    # so the old per-generator merge + ACE loop is gone.
    ib_cur = current_year_ibtracs_fixes(df, basin_cfg, current_year, log)

    live_used = False
    live = pd.DataFrame(columns=_CANON_COLS)
    if FETCH_LIVE and not args.no_live:
        print(f"{log} attempting live {basin_cfg['agency_name']} fetch for {current_year} ...")
        live = fetch_live_season(current_year, basin_cfg, log)
        if not live.empty:
            print(f"{log} pulled {len(live)} live 6-hour fixes from {basin_cfg['agency_name']}")
            live_used = True
        else:
            print(f"{log} live fetch returned nothing — using IBTrACS provisional data only")

    # One source per named storm (ace_core), identical to the tracks feed -> the
    # same canonical track per storm, so Sinlaku can't read 154 here and 160 there.
    if not live.empty:
        ib_keep, live_keep = ac.merge_named_sources(ib_cur, live, name_col="NAME")
    else:
        ib_keep, live_keep = ib_cur, live
    canon_frames = [f for f in (ib_keep, live_keep) if not f.empty]
    canon_cur = (pd.concat(canon_frames, ignore_index=True)
                 if canon_frames else pd.DataFrame(columns=_CANON_COLS))
    if not canon_cur.empty:
        canon_cur = canon_cur.drop_duplicates(subset=["SID", "time"])

    # Freshness = valid-time of the newest 6-hourly fix of ANY nature (a 25 kt
    # designated TD still counts for freshness even though it adds no ACE).
    latest_fix_dt = None
    if not canon_cur.empty:
        fix_times = [t for t in canon_cur["time"] if t is not None]
        latest_fix_dt = max(fix_times) if fix_times else None

    # Per-storm + season ACE come straight from ace_core (the single authority),
    # so the season total = sum of per-storm ACE = the tracks feed's total_ace.
    cur_storms = current_year_storms(canon_cur, basin_cfg, current_year)
    season_ace_current = ac.season_ace([s["ace_total"] for s in cur_storms])
    print(f"{log} current-year canonical: {len(cur_storms)} ACE storm(s), "
          f"season ACE {season_ace_current:.3f}")

    # Swap the current-year slice of the by-DOY points for the canonical one.
    cur_points = eligible_points_from_canon(canon_cur, basin_cfg, current_year)
    points = points[points["season"] != current_year].copy()
    if not cur_points.empty:
        points = pd.concat([points, cur_points], ignore_index=True)

    cum = cumulative_by_doy(points)

    # Ensure the current calendar year exists as a column even if there is no
    # activity for it yet (pre-season). Keeps the chart honest.
    if current_year not in cum.columns:
        cum[current_year] = 0.0
        cum = cum.reindex(columns=sorted(cum.columns))

    climo = climatology(cum, CLIMO_START, CLIMO_END,
                        exclude_years={current_year})

    last_obs_doy = points.groupby("season")["doy"].max().to_dict()
    # Past-year Storm-Activity Gantt from the TD-inclusive tropical frame (so
    # depressions get a bar; ACE is unchanged - only >=34 kt rows score). The
    # current-year gantt is the canonical ace_core set (peak via
    # canonical_peak_wind, ACE via storm_ace), which overwrites the current year.
    storms_by_year = extract_gantt_storms_by_year(trop_points, min_year=1970)
    if cur_storms:
        storms_by_year[current_year] = cur_storms
    else:
        storms_by_year.pop(current_year, None)
    payload = build_payload(cum, climo, current_year, prior_year, last_obs_doy,
                            storms_by_year=storms_by_year,
                            season_ace_current=season_ace_current,
                            latest_fix_dt=latest_fix_dt,
                            build_now=build_now)
    html = render_html(payload, basin_cfg, current_year, CLIMO_START, CLIMO_END,
                       live_used, build_now=build_now)

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
