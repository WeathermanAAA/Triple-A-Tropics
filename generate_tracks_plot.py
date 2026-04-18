#!/usr/bin/env python3
"""
generate_tracks_plot.py
-----------------------
Generate a geographic "TC tracks" visualization for one basin.

For a given basin (wp / al / ep) and the current calendar year, this
script:

  1. Loads IBTrACS historical tracks for the year
  2. Overlays the latest JTWC/NHC ATCF b-deck data for active storms
  3. Extracts per-storm metadata (name, date range, peaks, ACE)
  4. Renders a self-contained dark-mode HTML page containing an SVG map
     with coastlines, colored track dots (SSHWS intensity), animated
     spinning icons for currently-active storms, and a side panel listing
     every storm of the year with stats.

Outputs:
    {basin}_tracks.html        Self-contained page
    {basin}_tracks_data.json   Processed storm data

Usage:
    python generate_tracks_plot.py --basin wp
    python generate_tracks_plot.py --basin al
    python generate_tracks_plot.py --basin ep

Inputs expected next to this script:
    ibtracs.{CODE}.list.v04r01.csv    (from NCEI, matches ACE generator)
    ne_110m_coastline.geojson         (one-time download, see workflow)
    ne_110m_admin_0_countries.geojson (optional, also from Natural Earth)
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

# ---------------------------------------------------------------------------
# Basin configuration (mirrors generate_ace_plot.py so they stay aligned)
# ---------------------------------------------------------------------------

BASINS: dict[str, dict] = {
    "wp": {
        "short": "wp",
        "name": "West Pacific",
        "full_name": "Western North Pacific",
        "ibtracs_file_code": "WP",
        "ibtracs_basin_col": ["WP"],
        "atcf_prefix": "bwp",
        "agency_name": "JTWC",
        "agency_url": "https://www.metoc.navy.mil/jtwc/",
        "atcf_patterns": [
            "https://triple-a-tropics-proxy.coloradoskier2018.workers.dev/atcf/btk/bwp{nn}{year}.dat",
            "https://www.natyphoon.top/atcf/temp/bwp{nn}{year}.dat",
            "https://www.metoc.navy.mil/jtwc/products/atcf/btk/bwp{nn}{year}.dat",
        ],
        "wind_preference": [
            ("USA_WIND", 1.0),
            ("WMO_WIND", 1.0 / 0.88),
            ("TOKYO_WIND", 1.0 / 0.88),
        ],
        "pressure_preference": ["USA_PRES", "WMO_PRES", "TOKYO_PRES"],
        "ace_natures": {"TS"},
        "atcf_dev_levels": {"TS", "TY", "STY", "HU"},
        # Geographic extent for the rendered map (lon_min, lon_max, lat_min, lat_max)
        "extent": (100.0, 180.0, 0.0, 65.0),
        # Labels at bottom of the map (matching a-reference-site terminology)
        "vocab": {"named": "named storms", "cat1plus": "typhoons",
                  "cat3plus": "major typhoons", "cat5": "super typhoons"},
    },
    "al": {
        "short": "al",
        "name": "Atlantic",
        "full_name": "North Atlantic",
        "ibtracs_file_code": "NA",
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
        "pressure_preference": ["USA_PRES", "WMO_PRES"],
        "ace_natures": {"TS", "SS"},
        "atcf_dev_levels": {"TS", "HU", "SS", "SD"},
        "extent": (-100.0, 0.0, 0.0, 55.0),
        "vocab": {"named": "named storms", "cat1plus": "hurricanes",
                  "cat3plus": "major hurricanes", "cat5": "category 5s"},
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
        "pressure_preference": ["USA_PRES", "WMO_PRES"],
        "ace_natures": {"TS", "SS"},
        "atcf_dev_levels": {"TS", "HU", "SS", "SD"},
        "extent": (-180.0, -80.0, 0.0, 40.0),
        "vocab": {"named": "named storms", "cat1plus": "hurricanes",
                  "cat3plus": "major hurricanes", "cat5": "category 5s"},
    },
}

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = Path(os.environ.get("TRACKS_OUTPUT_DIR", str(HERE)))

FETCH_LIVE = True
FETCH_TIMEOUT = 10
FETCH_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/17.0 Safari/605.1.15")

SIX_HOURLY = {0, 6, 12, 18}
# A storm is "active" if its last observation is within this many hours of
# "now" AND it still has tropical-storm-strength winds. 60 hours covers the
# typical 1–2 day IBTrACS provisional lag plus some slack for weekends.
ACTIVE_WINDOW_HOURS = 60

# Saffir-Simpson Hurricane Wind Scale thresholds (1-min sustained, kt)
SSHS_COLORS = {
    "TD": "#3fa4ff",    # depression - blue
    "TS": "#46c56a",    # tropical storm - green
    "C1": "#ffe14d",    # cat 1 - yellow
    "C2": "#ff9a2f",    # cat 2 - orange
    "C3": "#ff4d3b",    # cat 3 - red
    "C4": "#e33ad4",    # cat 4 - magenta/pink
    "C5": "#b03bff",    # cat 5 - purple
}


def sshs_class(wind_kt: float, nature: str | None = None) -> str:
    """Map a wind speed (kt, 1-min sustained) to SSHWS class code.
    Non-tropical storms fall through to TD (weakest)."""
    if wind_kt is None or (isinstance(wind_kt, float) and math.isnan(wind_kt)):
        return "TD"
    if wind_kt < 34:
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


def sshs_label(cls: str) -> str:
    """Short label shown inside the active-storm icon."""
    return {"TD": "D", "TS": "S",
            "C1": "1", "C2": "2", "C3": "3", "C4": "4", "C5": "5"}[cls]


# ---------------------------------------------------------------------------
# IBTrACS parsing
# ---------------------------------------------------------------------------

def _best_wind(row: pd.Series, preference: list[tuple[str, float]]) -> float:
    for col, factor in preference:
        v = row.get(col)
        if pd.notna(v):
            return float(v) * factor
    return np.nan


def _best_pressure(row: pd.Series, preference: list[str]) -> float:
    for col in preference:
        v = row.get(col)
        if pd.notna(v):
            return float(v)
    return np.nan


def _parse_ibtracs_latlon(row: pd.Series) -> tuple[float, float] | None:
    """Try USA_LAT/LON first, then LAT/LON. IBTrACS stores as decimal degrees."""
    for la_col, lo_col in [("USA_LAT", "USA_LON"), ("LAT", "LON")]:
        la = row.get(la_col)
        lo = row.get(lo_col)
        if pd.notna(la) and pd.notna(lo):
            try:
                return float(la), float(lo)
            except (ValueError, TypeError):
                continue
    return None


def load_ibtracs_current_year(csv_path: Path, basin_cfg: dict,
                              year: int, log_prefix: str = "") -> pd.DataFrame:
    """Return 6-hourly observations for the given calendar year only,
    as a DataFrame with columns:
        SID, NAME, season, time, lat, lon, wind_kt, pressure_mb, nature
    """
    print(f"{log_prefix} loading {csv_path} ...")
    df = pd.read_csv(csv_path, skiprows=[1], low_memory=False, na_values=[" ", ""])
    print(f"{log_prefix}   {len(df):,} raw rows")

    # Basin filter (with fallback)
    basin_codes = basin_cfg["ibtracs_basin_col"]
    if isinstance(basin_codes, str):
        basin_codes = [basin_codes]
    d = df[df["BASIN"].isin(basin_codes)].copy()
    if len(d) == 0 and len(df) > 0:
        print(f"{log_prefix}   WARN: BASIN filter {basin_codes} matched 0 rows; "
              f"falling back to whole file.")
        d = df.copy()

    d = d[d["TRACK_TYPE"].isin(["main", "PROVISIONAL"])].copy()
    d["ISO_TIME"] = pd.to_datetime(d["ISO_TIME"], errors="coerce")
    d = d.dropna(subset=["ISO_TIME"])
    d = d[d["SEASON"].astype(int) == year]

    # Keep 6-hourly synoptic points
    hours = d["ISO_TIME"].dt.hour
    minutes = d["ISO_TIME"].dt.minute
    d = d[hours.isin(SIX_HOURLY) & (minutes == 0)].copy()

    # Numeric conversions
    for col, _ in basin_cfg["wind_preference"]:
        if col in d.columns:
            d[col] = pd.to_numeric(d[col], errors="coerce")
    for col in basin_cfg["pressure_preference"]:
        if col in d.columns:
            d[col] = pd.to_numeric(d[col], errors="coerce")

    d["WIND_KT"] = d.apply(lambda r: _best_wind(r, basin_cfg["wind_preference"]), axis=1)
    d["PRES_MB"] = d.apply(lambda r: _best_pressure(r, basin_cfg["pressure_preference"]), axis=1)

    rows = []
    for _, row in d.iterrows():
        ll = _parse_ibtracs_latlon(row)
        if ll is None:
            continue
        lat, lon = ll
        rows.append({
            "SID": row["SID"],
            "NAME": (row.get("NAME") or "").strip() or "UNNAMED",
            "season": year,
            "time": row["ISO_TIME"].to_pydatetime(),
            "lat": lat,
            "lon": lon,
            "wind_kt": row["WIND_KT"],
            "pressure_mb": row["PRES_MB"],
            "nature": row.get("NATURE") or "",
            "source": "IBTrACS",
        })
    out = pd.DataFrame(rows)
    print(f"{log_prefix}   {len(out):,} current-year observations")
    return out


# ---------------------------------------------------------------------------
# Live ATCF b-deck fetch
# ---------------------------------------------------------------------------

def _parse_atcf_latlon(lat_raw: str, lon_raw: str) -> tuple[float, float] | None:
    """ATCF format: '157N' -> 15.7°N, '1234W' -> -123.4°."""
    try:
        lat_raw = lat_raw.strip()
        lon_raw = lon_raw.strip()
        lat_hem = lat_raw[-1]
        lon_hem = lon_raw[-1]
        lat_val = float(lat_raw[:-1]) / 10.0
        lon_val = float(lon_raw[:-1]) / 10.0
        if lat_hem == "S":
            lat_val = -lat_val
        if lon_hem == "W":
            lon_val = -lon_val
        return lat_val, lon_val
    except (ValueError, IndexError):
        return None


def parse_atcf_bdeck(text: str, season: int, basin_cfg: dict) -> pd.DataFrame:
    """Parse an ATCF b-deck file into the same schema as the IBTrACS frame.

    ATCF b-decks contain multiple BEST lines per timestamp (one per
    wind-radius threshold 34/50/64 kt). Filter to RAD=='34' to keep
    exactly one line per observation — matches the standard reference
    implementation."""
    rows = []
    name_by_storm: dict[int, str] = {}
    # First pass: grab the storm name (appears in the later columns, if set)
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 11:
            continue
        try:
            storm_num = int(parts[1])
            tech = parts[4]
            name_col = parts[27] if len(parts) > 27 else ""
        except (IndexError, ValueError):
            continue
        if tech != "BEST":
            continue
        if name_col and name_col not in {"", "NAMELESS", "INVEST"}:
            name_by_storm[storm_num] = name_col

    seen: set[tuple[int, str]] = set()
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 11:
            continue
        try:
            storm_num = int(parts[1])
            tstamp = parts[2]
            tech = parts[4]
            lat_raw = parts[6]
            lon_raw = parts[7]
            vmax = parts[8]
            mslp = parts[9]
            devlvl = parts[10]
            rad = parts[11] if len(parts) > 11 else ""
        except (IndexError, ValueError):
            continue
        if tech != "BEST":
            continue
        # Accept blank RAD (pre-radii observations) or "34"; skip 50/64
        # kt radii (they're duplicates of the 34 kt row for the same obs)
        if rad not in ("", "34"):
            continue
        # Belt-and-suspenders dedupe by (storm, timestamp) in case a file
        # ever has both a short 11-col line and a 12-col RAD=34 line for
        # the same obs.
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
            vmax_f = float(vmax) if vmax else float("nan")
        except ValueError:
            vmax_f = float("nan")
        try:
            mslp_f = float(mslp) if mslp and mslp != "0" else float("nan")
        except ValueError:
            mslp_f = float("nan")
        ll = _parse_atcf_latlon(lat_raw, lon_raw)
        if ll is None:
            continue
        lat, lon = ll
        # Map ATCF dev-level to IBTrACS-style nature
        if devlvl in {"TS", "TY", "STY", "HU"}:
            nature = "TS"
        elif devlvl in {"SS", "SD"}:
            nature = "SS"
        elif devlvl in {"TD"}:
            nature = "TS"  # pre-TS, still tropical
        elif devlvl in {"EX"}:
            nature = "ET"
        else:
            nature = ""
        rows.append({
            "SID": f"{basin_cfg['agency_name']}_{basin_cfg['short'].upper()}"
                   f"{storm_num:02d}{season}",
            "NAME": name_by_storm.get(storm_num, f"#{storm_num:02d}"),
            "season": season,
            "time": t,
            "lat": lat,
            "lon": lon,
            "wind_kt": vmax_f,
            "pressure_mb": mslp_f,
            "nature": nature,
            "source": f"live-{basin_cfg['agency_name']}",
        })
    return pd.DataFrame(rows)


def fetch_live_season(season: int, basin_cfg: dict, log_prefix: str) -> pd.DataFrame:
    try:
        import urllib.request
        import urllib.error
    except Exception:
        return pd.DataFrame()

    yy = season % 100
    frames: list[pd.DataFrame] = []
    patterns = basin_cfg["atcf_patterns"]
    consecutive_misses = 0
    for nn in range(1, 41):
        hit = False
        for pattern in patterns:
            url = pattern.format(nn=f"{nn:02d}", yy=f"{yy:02d}", year=season)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": FETCH_UA})
                with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
                    if r.status != 200:
                        continue
                    text = r.read().decode("utf-8", errors="ignore")
                if "BEST" not in text:
                    continue
                frames.append(parse_atcf_bdeck(text, season, basin_cfg))
                hit = True
                break
            except Exception:
                continue
        consecutive_misses = 0 if hit else consecutive_misses + 1
        if consecutive_misses >= 3:
            break
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    print(f"{log_prefix}   live fetch: {len(out)} points from "
          f"{out['SID'].nunique()} storm(s)")
    return out


# ---------------------------------------------------------------------------
# Per-storm aggregation
# ---------------------------------------------------------------------------

def merge_and_extract_storms(ibtracs: pd.DataFrame, live: pd.DataFrame,
                             basin_cfg: dict) -> list[dict]:
    """Merge IBTrACS + live. For each named storm in BOTH sources, we keep
    whichever source has more observations for that storm — live tends
    to be more complete for currently-active storms (it has real-time
    advisories) while IBTrACS tends to be more complete for past/archived
    storms (JTWC may leave only a stub in its active directory once a
    storm dissipates). One source per storm, so no duplicate cards in
    the sidebar."""
    placeholders = {"", "UNNAMED", "INVEST", "NAMELESS"}

    def _norm(series):
        return series.fillna("").astype(str).str.strip().str.upper()

    if not live.empty and not ibtracs.empty:
        ib_n = _norm(ibtracs["NAME"])
        live_n = _norm(live["NAME"])
        ib_counts = ib_n.value_counts().to_dict()
        live_counts = live_n.value_counts().to_dict()

        contested = {n for n in live_n.unique() if n not in placeholders
                     and ib_counts.get(n, 0) > 0}

        drop_from_ib: list[str] = []
        drop_from_live: list[str] = []
        for name in contested:
            ib_c = ib_counts.get(name, 0)
            live_c = live_counts.get(name, 0)
            # Prefer the source with more observations. Ties broken
            # toward live (fresher, includes JTWC's current advisory).
            if ib_c > live_c:
                drop_from_live.append(name)
            else:
                drop_from_ib.append(name)

        if drop_from_ib:
            ibtracs = ibtracs[~ib_n.isin(drop_from_ib)].copy()
        if drop_from_live:
            live = live[~live_n.isin(drop_from_live)].copy()
        if contested:
            print(f"   merge: {len(contested)} storm(s) in both sources. "
                  f"Kept live for: {sorted(drop_from_ib)}. "
                  f"Kept IBTrACS for: {sorted(drop_from_live)}.")

    frames = [df for df in (ibtracs, live) if not df.empty]
    if not frames:
        return []
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["SID", "time"])
    df = df.sort_values(["SID", "time"]).reset_index(drop=True)

    now = dt.datetime.utcnow()
    active_cutoff = now - dt.timedelta(hours=ACTIVE_WINDOW_HOURS)

    storms: list[dict] = []
    for sid, group in df.groupby("SID"):
        points = group.to_dict("records")
        # Compute per-storm stats based only on ACE-eligible points
        # (tropical/subtropical, wind >= 34 kt)
        eligible_natures = set(basin_cfg["ace_natures"])
        storm_ace = 0.0
        peak_wind = float("nan")
        peak_pres = float("nan")
        # Lifetime = first and last observation of ANY kind (TD included).
        # ACE-window = first and last obs at TS+ intensity. Storms that
        # never reach TS (e.g. NURI 2026 peaked at 29 kt) still need a
        # lifetime date range in the sidebar.
        life_start = None
        life_end = None
        ace_start = None
        ace_end = None
        max_cls = "TD"
        for p in points:
            w = p["wind_kt"]
            nat = p["nature"] or ""
            t = p["time"]
            if life_start is None:
                life_start = t
            life_end = t
            # Consider NR as eligible for current-season provisional data
            nat_ok = nat in eligible_natures or nat == "NR" or nat == ""
            if nat_ok and pd.notna(w) and w >= 34:
                storm_ace += (w ** 2) / 10_000.0
                if ace_start is None:
                    ace_start = t
                ace_end = t
            if pd.notna(w):
                if math.isnan(peak_wind) or w > peak_wind:
                    peak_wind = float(w)
                cls = sshs_class(w)
                if _sshs_rank(cls) > _sshs_rank(max_cls):
                    max_cls = cls
            pr = p["pressure_mb"]
            if pd.notna(pr) and pr > 0:
                if math.isnan(peak_pres) or pr < peak_pres:
                    peak_pres = float(pr)
        # Sidebar "Active" row shows overall lifetime (preferring ACE
        # window if the storm did reach TS; otherwise whole track).
        start_t = ace_start or life_start
        end_t = ace_end or life_end

        # Active = (1) last observation is recent, AND (2) it still shows
        # a valid tropical-storm-strength wind, AND (3) nature hasn't gone
        # extratropical. Otherwise the storm has weakened/dissipated and
        # shouldn't get the spinning icon.
        is_active = False
        if len(points) > 0:
            last = points[-1]
            recent = last["time"] >= active_cutoff
            strong = pd.notna(last["wind_kt"]) and last["wind_kt"] >= 34
            tropical = (last["nature"] or "") not in {"ET", "DS"}
            is_active = recent and strong and tropical
        # Current intensity = SSHWS of the most recent observation
        last_wind = points[-1]["wind_kt"] if points else float("nan")
        current_cls = sshs_class(last_wind)

        # Name selection: prefer a real name over placeholders
        names = [p["NAME"] for p in points if p["NAME"]
                 and p["NAME"] not in {"UNNAMED", "INVEST", "NAMELESS"}]
        name = names[0] if names else (points[0]["NAME"] if points else "UNNAMED")

        storms.append({
            "sid": sid,
            "name": name,
            "season": int(points[0]["season"]),
            "start": start_t.isoformat() if start_t else None,
            "end": end_t.isoformat() if end_t else None,
            "peak_wind_kt": None if math.isnan(peak_wind) else round(peak_wind, 1),
            "peak_pressure_mb": None if math.isnan(peak_pres) else round(peak_pres, 1),
            "ace": round(storm_ace, 2),
            "max_category": max_cls,
            "current_category": current_cls,
            "is_active": bool(is_active),
            "points": [{
                "t": p["time"].isoformat(),
                "lat": round(float(p["lat"]), 2),
                "lon": round(float(p["lon"]), 2),
                "wind_kt": None if pd.isna(p["wind_kt"]) else round(float(p["wind_kt"]), 1),
                "pressure_mb": None if pd.isna(p["pressure_mb"]) or p["pressure_mb"] <= 0
                               else round(float(p["pressure_mb"]), 1),
                "cls": sshs_class(p["wind_kt"]),
            } for p in points],
        })
    # Sort: active storms first, then by ACE desc, then by start date
    storms.sort(key=lambda s: (not s["is_active"], -s["ace"], s["start"] or ""))
    return storms


_SSHS_RANK = {"TD": 0, "TS": 1, "C1": 2, "C2": 3, "C3": 4, "C4": 5, "C5": 6}


def _sshs_rank(cls: str) -> int:
    return _SSHS_RANK.get(cls, 0)


# ---------------------------------------------------------------------------
# Basemap (SVG coastlines from Natural Earth GeoJSON)
# ---------------------------------------------------------------------------

# Geographic -> SVG pixel mapping parameters
MAP_W = 1400    # SVG viewport width
MAP_H = 900     # SVG viewport height


def build_projection(extent: tuple[float, float, float, float]):
    """Return (project, extent_info). project(lon, lat) -> (x, y) in the
    map's SVG coordinate system."""
    lon_min, lon_max, lat_min, lat_max = extent

    def project(lon: float, lat: float) -> tuple[float, float]:
        # Equirectangular (plate carrée). East pacific needs the caller
        # to pre-wrap positive longitudes into negative (or vice versa)
        # if they cross the date line. For our basins WP (100..180) and
        # EP (-180..-80) don't wrap.
        x = (lon - lon_min) / (lon_max - lon_min) * MAP_W
        y = (lat_max - lat) / (lat_max - lat_min) * MAP_H
        return (x, y)

    return project, {
        "lon_min": lon_min, "lon_max": lon_max,
        "lat_min": lat_min, "lat_max": lat_max,
        "width": MAP_W, "height": MAP_H,
    }


def _ring_to_svg_path(ring: list, project) -> str:
    """Convert a GeoJSON LineString/ring to an SVG path `d` string.
    Clips very loosely by skipping coords outside the extent by a big margin."""
    parts = []
    for lon, lat in ring:
        try:
            x, y = project(lon, lat)
        except Exception:
            continue
        parts.append((x, y))
    if not parts:
        return ""
    # Skip paths entirely outside the viewport
    xs = [p[0] for p in parts]
    ys = [p[1] for p in parts]
    if max(xs) < -MAP_W or min(xs) > MAP_W * 2 or max(ys) < -MAP_H or min(ys) > MAP_H * 2:
        return ""
    d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in parts)
    return d


def load_natural_earth(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def render_basemap_svg(extent, countries_geojson: dict | None,
                       coastline_geojson: dict | None) -> str:
    """Build the SVG basemap: ocean fill, land polygons, coastlines, grid.

    Color palette (matches user-supplied reference):
      ocean   #2463a0  medium blue
      land    #aeb2b5  bright gray
      borders #ffffff  white, bold
      grid    dashed white w/ low opacity
    """
    project, _ = build_projection(extent)
    lon_min, lon_max, lat_min, lat_max = extent
    parts = []

    # Ocean background
    parts.append(f'<rect x="0" y="0" width="{MAP_W}" height="{MAP_H}" fill="#2463a0"/>')

    # Countries / land polygons (filled)
    if countries_geojson is not None:
        parts.append('<g class="land" fill="#aeb2b5" stroke="none">')
        for feat in countries_geojson.get("features", []):
            geom = feat.get("geometry") or {}
            gtype = geom.get("type")
            coords = geom.get("coordinates") or []
            polys = []
            if gtype == "Polygon":
                polys = [coords]
            elif gtype == "MultiPolygon":
                polys = coords
            for poly in polys:
                for ring in poly:
                    d = _ring_to_svg_path(ring, project)
                    if d:
                        parts.append(f'<path d="{d}" />')
        parts.append('</g>')

        # Country borders (drawn on top) — white + bold to match reference
        parts.append('<g class="borders" fill="none" stroke="#ffffff" '
                     'stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" '
                     'stroke-opacity="0.95">')
        for feat in countries_geojson.get("features", []):
            geom = feat.get("geometry") or {}
            gtype = geom.get("type")
            coords = geom.get("coordinates") or []
            polys = []
            if gtype == "Polygon":
                polys = [coords]
            elif gtype == "MultiPolygon":
                polys = coords
            for poly in polys:
                for ring in poly:
                    d = _ring_to_svg_path(ring, project)
                    if d:
                        parts.append(f'<path d="{d}" />')
        parts.append('</g>')

    # Coastlines — drawn on top, thinner than borders to avoid doubling up
    # where coast and border coincide. If we don't have country polys,
    # this is the only landmass outline.
    if coastline_geojson is not None:
        stroke_w = "1.6" if countries_geojson is None else "0.8"
        parts.append(f'<g class="coast" fill="none" stroke="#ffffff" '
                     f'stroke-width="{stroke_w}" stroke-linejoin="round" stroke-linecap="round" '
                     f'stroke-opacity="0.85">')
        for feat in coastline_geojson.get("features", []):
            geom = feat.get("geometry") or {}
            gtype = geom.get("type")
            coords = geom.get("coordinates") or []
            lines = []
            if gtype == "LineString":
                lines = [coords]
            elif gtype == "MultiLineString":
                lines = coords
            for line in lines:
                d = _ring_to_svg_path(line, project)
                if d:
                    parts.append(f'<path d="{d}" />')
        parts.append('</g>')

    # Graticule (lat/lon grid) — white dashes, low opacity
    parts.append('<g class="grid" stroke="#ffffff" stroke-width="0.5" stroke-dasharray="4 5" opacity="0.22">')
    # 10° spacing
    lon = math.ceil(lon_min / 10) * 10
    while lon <= lon_max:
        x1, _ = project(lon, lat_min)
        x2, _ = project(lon, lat_max)
        _, y1 = project(lon, lat_min)
        _, y2 = project(lon, lat_max)
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>')
        lon += 10
    lat = math.ceil(lat_min / 10) * 10
    while lat <= lat_max:
        _, y1 = project(lon_min, lat)
        _, y2 = project(lon_max, lat)
        x1, _ = project(lon_min, lat)
        x2, _ = project(lon_max, lat)
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>')
        lat += 10
    parts.append('</g>')

    # Axis labels — white with dark shadow for legibility on any background
    parts.append('<g class="axis-labels" fill="#ffffff" font-size="13" '
                 'font-weight="600" paint-order="stroke" stroke="rgba(0,0,0,0.55)" '
                 'stroke-width="3" stroke-linejoin="round" '
                 'font-family="-apple-system, Segoe UI, Roboto, sans-serif">')
    lon = math.ceil(lon_min / 10) * 10
    while lon <= lon_max:
        x, _ = project(lon, lat_min)
        label = _fmt_lon(lon)
        parts.append(f'<text x="{x:.1f}" y="{MAP_H - 8}" text-anchor="middle">{label}</text>')
        lon += 10
    lat = math.ceil(lat_min / 10) * 10
    while lat <= lat_max:
        _, y = project(lon_min, lat)
        label = _fmt_lat(lat)
        parts.append(f'<text x="10" y="{y + 4:.1f}" text-anchor="start">{label}</text>')
        lat += 10
    parts.append('</g>')

    return "\n".join(parts)


def _fmt_lon(lon: float) -> str:
    if lon > 180:
        lon -= 360
    if lon < -180:
        lon += 360
    if lon == 0:
        return "0°"
    if lon > 0:
        return f"{int(lon)}°E"
    return f"{int(-lon)}°W"


def _fmt_lat(lat: float) -> str:
    if lat == 0:
        return "0°"
    if lat > 0:
        return f"{int(lat)}°N"
    return f"{int(-lat)}°S"


# ---------------------------------------------------------------------------
# Track + active-storm overlay rendering
# ---------------------------------------------------------------------------

def render_tracks_svg(storms: list[dict], extent) -> str:
    """Draw each storm as a thin connecting polyline plus a colored dot
    at every 6-hour observation. Colors follow SSHWS.

    Every dot carries data-* attributes so the client-side hover tooltip
    can show the storm's intensity at that observation without another
    fetch. Dots for the same storm share a data-sid so clicking one can
    open that storm's detail placard.
    """
    project, _ = build_projection(extent)
    parts = ['<g class="tracks">']
    for storm in storms:
        pts = storm.get("points") or []
        if len(pts) < 1:
            continue
        sid = storm.get("sid") or ""
        sname = (storm.get("name") or "UNNAMED").replace('"', '')
        # Track connecting line (thin, dark, under dots)
        xy = []
        for p in pts:
            lon = p["lon"]
            # Wrap longitudes into the basin extent if needed (WP goes east of 180)
            if extent[1] > 180 and lon < extent[0]:
                lon += 360
            if extent[1] <= 180 and extent[0] < 0 and lon > 180:
                lon -= 360
            x, y = project(lon, p["lat"])
            xy.append((x, y))
        if len(xy) >= 2:
            d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
            parts.append(f'<path d="{d}" fill="none" stroke="#ffffff" '
                         'stroke-width="1.2" stroke-opacity="0.5" '
                         'stroke-linejoin="round" stroke-linecap="round"/>')
        # Dots — radius depends on whether the point is at TS+ (bigger) or not
        for (x, y), p in zip(xy, pts):
            cls = p.get("cls") or "TD"
            wind = p.get("wind_kt")
            pres = p.get("pressure_mb")
            t = p.get("t") or ""
            color = SSHS_COLORS.get(cls, SSHS_COLORS["TD"])
            # Bigger dot for stronger systems
            if cls == "TD":
                r = 3
            elif cls == "TS":
                r = 4
            else:
                r = 5
            wind_attr = f"{wind}" if wind is not None else ""
            pres_attr = f"{pres}" if pres is not None else ""
            parts.append(
                f'<circle class="track-dot" cx="{x:.1f}" cy="{y:.1f}" r="{r}" '
                f'fill="{color}" stroke="#ffffff" stroke-width="0.9" '
                f'stroke-opacity="0.85" '
                f'data-sid="{sid}" data-name="{sname}" data-t="{t}" '
                f'data-wind="{wind_attr}" data-pres="{pres_attr}" '
                f'data-cls="{cls}"/>')
    parts.append('</g>')
    return "\n".join(parts)


def render_active_icons(storms: list[dict], extent) -> str:
    """For each active storm, place a spinning+glowing hurricane-symbol
    icon at its most recent position. Two comma-shaped arms in S-curve
    (NHC classic), rotating counterclockwise (NH cyclone direction),
    with a bold white category number in the center."""
    project, _ = build_projection(extent)
    parts = ['<g class="active-storms">']
    for storm in storms:
        if not storm.get("is_active"):
            continue
        pts = storm.get("points") or []
        if not pts:
            continue
        last = pts[-1]
        lon = last["lon"]
        if extent[1] > 180 and lon < extent[0]:
            lon += 360
        x, y = project(lon, last["lat"])
        cls = storm.get("current_category") or "TD"
        color = SSHS_COLORS.get(cls, SSHS_COLORS["TD"])
        label = sshs_label(cls)
        name = storm.get("name") or ""
        # Hurricane symbol path traced from the official NHC icon.
        # Single closed path outlining both commas as a continuous
        # S-curve. Pre-centered on (0,0). The spinning group is wrapped
        # in a scale() to shrink the icon; the scale is on an outer
        # group because <animateTransform> replaces its own element's
        # transform attribute. CCW spin for NH cyclones.
        sid = storm.get("sid") or ""
        parts.append(f'''<g class="active-icon" data-sid="{sid}" transform="translate({x:.1f},{y:.1f})" style="filter:drop-shadow(0 0 6px {color});">
  <g transform="scale(0.7)">
    <g class="spin-wrap">
      <path d="M 16.37,-28.27 C 13.58,-28.13 11.51,-27.90 9.23,-27.49 C 1.27,-26.06 -5.88,-22.70 -10.92,-18.02 C -14.83,-14.40 -17.41,-10.06 -18.49,-5.32 C -18.95,-3.30 -19.15,-1.42 -19.15,0.91 C -19.15,2.53 -19.09,3.28 -18.89,4.45 C -18.38,7.38 -17.47,9.46 -15.41,12.37 C -13.88,14.54 -13.43,15.31 -13.20,16.13 C -13.11,16.44 -13.09,16.62 -13.09,17.14 C -13.10,17.93 -13.20,18.32 -13.67,19.28 C -15.30,22.59 -18.65,24.93 -23.49,26.14 C -25.26,26.58 -27.29,26.87 -29.18,26.95 L -30.00,26.98 L -29.65,27.06 C -27.33,27.62 -24.41,28.05 -21.57,28.27 C -20.04,28.38 -16.31,28.38 -14.80,28.27 C -12.93,28.13 -11.43,27.95 -9.77,27.67 C -0.59,26.14 7.56,22.03 12.68,16.37 C 16.22,12.45 18.28,8.10 18.93,3.13 C 19.64,-2.25 18.99,-6.47 16.84,-10.16 C 16.48,-10.80 15.79,-11.82 14.99,-12.95 C 13.61,-14.89 13.18,-15.77 13.12,-16.83 C 13.07,-17.61 13.23,-18.26 13.71,-19.23 C 14.97,-21.79 17.38,-23.84 20.67,-25.16 C 23.13,-26.14 26.24,-26.77 29.15,-26.87 L 30.00,-26.90 L 29.67,-26.98 C 29.13,-27.12 27.57,-27.44 26.66,-27.58 C 24.96,-27.87 23.39,-28.05 21.66,-28.18 C 20.72,-28.25 17.16,-28.30 16.37,-28.27 Z" fill="{color}"/>
      <animateTransform attributeName="transform" attributeType="XML" type="rotate" from="360" to="0" dur="2.6s" repeatCount="indefinite"/>
    </g>
  </g>
  <text y="0" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="900" fill="#ffffff" paint-order="stroke" stroke="rgba(0,0,0,0.55)" stroke-width="1.8" stroke-linejoin="round">{label}</text>
  <text class="name" x="28" y="5" text-anchor="start">{name}</text>
</g>''')
    parts.append('</g>')
    return "\n".join(parts)


# No <defs>/<symbol> needed — geometry is drawn inline in render_active_icons.
SVG_DEFS = ""


# ---------------------------------------------------------------------------
# Client-side JS: hover tooltip on every dot, click-to-expand detail
# placard (current intensity banner + wind-history chart) on active storms.
# Kept as a raw string — no Python .format() — so we don't have to escape
# the many braces in the JS body.
# ---------------------------------------------------------------------------

TRACKS_JS = r"""
(function() {
  var SSHS_COLORS = {
    "TD": "#3fa4ff", "TS": "#46c56a", "C1": "#ffe14d",
    "C2": "#ff9a2f", "C3": "#ff4d3b", "C4": "#e33ad4", "C5": "#b03bff"
  };
  var CAT_LABELS = {
    "TD": "Depression", "TS": "Tropical Storm",
    "C1": "Category 1", "C2": "Category 2", "C3": "Category 3",
    "C4": "Category 4", "C5": "Category 5"
  };
  function ktToMph(k) { return Math.round(k * 1.15077945); }
  function ktToKmh(k) { return Math.round(k * 1.852); }
  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function fmtTime(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    var m = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    var hh = String(d.getUTCHours()).padStart(2,"0");
    var mm = String(d.getUTCMinutes()).padStart(2,"0");
    return m[d.getUTCMonth()] + " " + d.getUTCDate() + ", " + hh + ":" + mm + "Z";
  }
  function fmtLatLon(lat, lon) {
    while (lon > 180) lon -= 360;
    while (lon < -180) lon += 360;
    var la = Math.abs(lat).toFixed(1) + "\u00B0 " + (lat >= 0 ? "N" : "S");
    var lo = Math.abs(lon).toFixed(1) + "\u00B0 " + (lon >= 0 ? "E" : "W");
    return la + "   " + lo;
  }
  function compass(b) {
    var dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
    return dirs[Math.round(b / 22.5) % 16];
  }

  // ---- Load payload ----
  var payloadEl = document.getElementById("storms-payload");
  var STORMS = [];
  try { STORMS = JSON.parse(payloadEl.textContent || "[]"); }
  catch (e) { STORMS = []; }
  // Payload contains the full page object; pick off the storms list.
  if (STORMS && STORMS.storms) STORMS = STORMS.storms;
  var storemap = {};
  STORMS.forEach(function(s) { storemap[s.sid] = s; });

  // ---- Hover tooltip ----
  var tip = document.getElementById("dot-tooltip");
  function showTip(e) {
    var d = e.currentTarget.dataset;
    var windKt = d.wind ? parseFloat(d.wind) : null;
    var windTxt = windKt != null && !isNaN(windKt)
      ? (Math.round(windKt) + " kt · " + ktToMph(windKt) + " mph")
      : "\u2014";
    var presTxt = d.pres && d.pres !== "" ? (Math.round(parseFloat(d.pres)) + " mb") : "\u2014";
    var cls = d.cls || "TD";
    var catTxt = CAT_LABELS[cls] || cls;
    tip.innerHTML =
      '<div class="tt-name">' + escapeHtml(d.name) + '</div>' +
      '<div class="tt-time">' + fmtTime(d.t) + '</div>' +
      '<div class="tt-row"><span class="tt-cat" style="background:' +
        (SSHS_COLORS[cls] || "#888") + '">' + catTxt + '</span></div>' +
      '<div class="tt-row"><span class="tt-lbl">Wind</span><span class="tt-val">' + windTxt + '</span></div>' +
      '<div class="tt-row"><span class="tt-lbl">Pressure</span><span class="tt-val">' + presTxt + '</span></div>';
    tip.hidden = false;
    moveTip(e);
  }
  function moveTip(e) {
    var pad = 14;
    var x = e.clientX + pad;
    var y = e.clientY + pad;
    var tw = tip.offsetWidth, th = tip.offsetHeight;
    if (x + tw > window.innerWidth - 6) x = e.clientX - tw - pad;
    if (y + th > window.innerHeight - 6) y = e.clientY - th - pad;
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }
  function hideTip() { tip.hidden = true; }
  var dots = document.querySelectorAll(".track-dot");
  dots.forEach(function(dot) {
    dot.addEventListener("mouseenter", showTip);
    dot.addEventListener("mousemove", moveTip);
    dot.addEventListener("mouseleave", hideTip);
    dot.addEventListener("click", function(e) {
      var sid = dot.dataset.sid;
      if (sid && storemap[sid] && storemap[sid].is_active) {
        openPlacard(sid);
      }
    });
  });

  // ---- Click handling ----
  // Every storm card has an inline placard slot.
  //   - INACTIVE cards expand to show a peak-intensity banner + wind chart.
  //   - ACTIVE cards expand to show just the wind-history chart — the
  //     parent page already shows the live intensity banner at the top,
  //     so we don't repeat it here. Clicking the spinning map icon does
  //     the same thing (scrolls to the card and opens the chart).
  function renderActiveInline(storm) {
    var pts = (storm.points || []).filter(function(p) { return p.wind_kt != null; });
    return '<div class="placard-chart-label">Wind history</div>' +
           renderWindChart(pts);
  }
  function openInline(sid) {
    var el = document.getElementById("placard-" + sid);
    var card = document.getElementById("card-" + sid);
    var s = storemap[sid];
    if (!el || !s) return;
    if (!el.dataset.rendered) {
      el.innerHTML = s.is_active ? renderActiveInline(s) : renderPeakPlacard(s);
      el.dataset.rendered = "1";
    }
    el.hidden = false;
    if (card) {
      card.classList.add("open");
      card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }
  function toggleInline(sid) {
    var el = document.getElementById("placard-" + sid);
    var card = document.getElementById("card-" + sid);
    var s = storemap[sid];
    if (!el || !s) return;
    if (!el.dataset.rendered) {
      el.innerHTML = s.is_active ? renderActiveInline(s) : renderPeakPlacard(s);
      el.dataset.rendered = "1";
    }
    var nowOpen = el.hidden;
    el.hidden = !el.hidden;
    if (card) card.classList.toggle("open", nowOpen);
    if (nowOpen && card) {
      card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }
  document.querySelectorAll(".storm-card.clickable").forEach(function(card) {
    card.addEventListener("click", function() { toggleInline(card.dataset.sid); });
  });
  // Spinning map icon → reveal that active storm's wind-history chart
  // in the sidebar card.
  document.querySelectorAll(".active-icon").forEach(function(g) {
    g.addEventListener("click", function(e) {
      e.stopPropagation();
      openInline(g.dataset.sid);
    });
  });

  // ---- Placard rendering ----
  // Hurricane glyph path — same one used for the spinning map icons,
  // reused here as a small corner accent on each placard.
  var HURRICANE_PATH = "M 16.37,-28.27 C 13.58,-28.13 11.51,-27.90 9.23,-27.49 C 1.27,-26.06 -5.88,-22.70 -10.92,-18.02 C -14.83,-14.40 -17.41,-10.06 -18.49,-5.32 C -18.95,-3.30 -19.15,-1.42 -19.15,0.91 C -19.15,2.53 -19.09,3.28 -18.89,4.45 C -18.38,7.38 -17.47,9.46 -15.41,12.37 C -13.88,14.54 -13.43,15.31 -13.20,16.13 C -13.11,16.44 -13.09,16.62 -13.09,17.14 C -13.10,17.93 -13.20,18.32 -13.67,19.28 C -15.30,22.59 -18.65,24.93 -23.49,26.14 C -25.26,26.58 -27.29,26.87 -29.18,26.95 L -30.00,26.98 L -29.65,27.06 C -27.33,27.62 -24.41,28.05 -21.57,28.27 C -20.04,28.38 -16.31,28.38 -14.80,28.27 C -12.93,28.13 -11.43,27.95 -9.77,27.67 C -0.59,26.14 7.56,22.03 12.68,16.37 C 16.22,12.45 18.28,8.10 18.93,3.13 C 19.64,-2.25 18.99,-6.47 16.84,-10.16 C 16.48,-10.80 15.79,-11.82 14.99,-12.95 C 13.61,-14.89 13.18,-15.77 13.12,-16.83 C 13.07,-17.61 13.23,-18.26 13.71,-19.23 C 14.97,-21.79 17.38,-23.84 20.67,-25.16 C 23.13,-26.14 26.24,-26.77 29.15,-26.87 L 30.00,-26.90 L 29.67,-26.98 C 29.13,-27.12 27.57,-27.44 26.66,-27.58 C 24.96,-27.87 23.39,-28.05 21.66,-28.18 C 20.72,-28.25 17.16,-28.30 16.37,-28.27 Z";
  function spinnerSvg(color) {
    // Inner <g> picks up the CSS-driven rotation. The fill matches the
    // banner's category color but with a bit of white to stay readable
    // against the bright banner backgrounds.
    return '<div class="placard-spinner">' +
      '<svg viewBox="-34 -34 68 68">' +
        '<g><path d="' + HURRICANE_PATH + '" fill="' + color + '" ' +
          'stroke="rgba(0,0,0,0.35)" stroke-width="1.2"/></g>' +
      '</svg>' +
    '</div>';
  }
  function computeMovement(pts) {
    for (var i = pts.length - 2; i >= 0; i--) {
      var a = pts[i], b = pts[pts.length - 1];
      var ta = new Date(a.t).getTime(), tb = new Date(b.t).getTime();
      var dtH = (tb - ta) / 3600000;
      if (dtH < 1) continue;
      var latm = (b.lat - a.lat) * 60;
      var lonm = (b.lon - a.lon) * 60 * Math.cos((a.lat + b.lat) / 2 * Math.PI / 180);
      var distNm = Math.sqrt(latm*latm + lonm*lonm);
      if (distNm < 0.5) return "Nearly stationary";
      var speedKt = distNm / dtH;
      var bearing = (Math.atan2(lonm, latm) * 180 / Math.PI + 360) % 360;
      return compass(bearing) + " at " + ktToMph(speedKt) + " mph";
    }
    return "\u2014";
  }
  // Dark vs. white banner text based on category color luminance.
  function bannerTextColor(cls) {
    return (cls === "TS" || cls === "C1" || cls === "C2") ? "#0a1324" : "#ffffff";
  }
  // Live/current-intensity variant. Used by the pinned "Active Now" panel.
  function renderCurrentPlacard(storm) {
    if (!storm) return '<div class="chart-empty">No data.</div>';
    var pts = (storm.points || []).slice();
    var validPts = pts.filter(function(p) { return p.wind_kt != null; });
    var last = pts[pts.length - 1] || {};
    var lastValid = validPts[validPts.length - 1] || last;
    var cls = storm.current_category || "TD";
    var color = SSHS_COLORS[cls] || "#888";
    var catLabel = CAT_LABELS[cls] || cls;
    var windKt = lastValid.wind_kt || 0;
    var pres = lastValid.pressure_mb;
    var loc = fmtLatLon(last.lat, last.lon);
    var movement = computeMovement(pts);
    var chart = renderWindChart(validPts);
    var txtColor = bannerTextColor(cls);
    return (
      '<div class="storm-placard">' +
      '<div class="placard-banner" style="background:' + color + ';color:' + txtColor + '">' +
        spinnerSvg(color) +
        '<div class="pl-row1"><span class="pl-cat">' + catLabel + '</span><b>' +
          escapeHtml(storm.name || "UNNAMED") + '</b></div>' +
        '<div class="pl-intensity">' +
          '<div class="pl-big">' + ktToMph(windKt) + '</div>' +
          '<div class="pl-units">mph<br>' + ktToKmh(windKt) + ' km/h</div>' +
        '</div>' +
        '<div class="pl-deets">' +
          '<div><span>Updated</span><b>' + fmtTime(last.t) + '</b></div>' +
          '<div><span>Location</span><b>' + loc + '</b></div>' +
          '<div><span>Pressure</span><b>' + (pres ? Math.round(pres) + " mb" : "\u2014") + '</b></div>' +
          '<div><span>Movement</span><b>' + movement + '</b></div>' +
        '</div>' +
      '</div>' +
      '<div class="placard-chart-label">Wind history</div>' +
      chart +
      '</div>'
    );
  }
  // Peak-intensity variant. Used inline for every storm (including the
  // already-dissipated ones that have a historical max).
  function renderPeakPlacard(storm) {
    if (!storm) return '<div class="chart-empty">No data.</div>';
    var pts = (storm.points || []).slice();
    var validPts = pts.filter(function(p) { return p.wind_kt != null; });
    if (!validPts.length) return '<div class="chart-empty">No wind observations.</div>';
    // Observation with the strongest wind (ties broken by earliest).
    var peak = validPts[0];
    for (var i = 1; i < validPts.length; i++) {
      if (validPts[i].wind_kt > peak.wind_kt) peak = validPts[i];
    }
    // Lowest pressure may be at a different time; report it separately.
    var minPres = null;
    validPts.forEach(function(p) {
      if (p.pressure_mb != null && (minPres == null || p.pressure_mb < minPres)) {
        minPres = p.pressure_mb;
      }
    });
    var cls = peak.cls || "TD";
    var color = SSHS_COLORS[cls] || "#888";
    var catLabel = CAT_LABELS[cls] || cls;
    var windKt = peak.wind_kt;
    var loc = fmtLatLon(peak.lat, peak.lon);
    var chart = renderWindChart(validPts);
    var txtColor = bannerTextColor(cls);
    return (
      '<div class="placard-banner" style="background:' + color + ';color:' + txtColor + '">' +
        spinnerSvg(color) +
        '<div class="pl-row1"><span class="pl-cat">PEAK · ' + catLabel + '</span><b>' +
          escapeHtml(storm.name || "UNNAMED") + '</b></div>' +
        '<div class="pl-intensity">' +
          '<div class="pl-big">' + ktToMph(windKt) + '</div>' +
          '<div class="pl-units">mph<br>' + ktToKmh(windKt) + ' km/h</div>' +
        '</div>' +
        '<div class="pl-deets">' +
          '<div><span>Reached</span><b>' + fmtTime(peak.t) + '</b></div>' +
          '<div><span>Location</span><b>' + loc + '</b></div>' +
          '<div><span>Min pressure</span><b>' + (minPres ? Math.round(minPres) + " mb" : "\u2014") + '</b></div>' +
          '<div><span>ACE</span><b>' + (storm.ace != null ? storm.ace.toFixed(2) : "\u2014") + '</b></div>' +
        '</div>' +
      '</div>' +
      '<div class="placard-chart-label">Wind history</div>' +
      chart
    );
  }

  function renderWindChart(pts) {
    if (!pts.length) {
      return '<div class="chart-empty">No wind observations yet.</div>';
    }
    var W = 320, H = 190;
    var padL = 34, padR = 8, padT = 8, padB = 26;
    var plotW = W - padL - padR;
    var plotH = H - padT - padB;
    var times = pts.map(function(p) { return new Date(p.t).getTime(); });
    var tMin = Math.min.apply(null, times);
    var tMax = Math.max.apply(null, times);
    var maxWind = Math.max(160, Math.max.apply(null, pts.map(function(p) { return p.wind_kt || 0; })));
    function yScale(w) { return padT + plotH - (w / maxWind) * plotH; }
    function xScale(t) {
      if (tMax === tMin) return padL + plotW / 2;
      return padL + (t - tMin) / (tMax - tMin) * plotW;
    }
    var bands = [
      [0, 34, SSHS_COLORS.TD],
      [34, 64, SSHS_COLORS.TS],
      [64, 83, SSHS_COLORS.C1],
      [83, 96, SSHS_COLORS.C2],
      [96, 113, SSHS_COLORS.C3],
      [113, 137, SSHS_COLORS.C4],
      [137, maxWind, SSHS_COLORS.C5]
    ];
    var bandRects = bands.map(function(b) {
      var lo = b[0], hi = b[1], c = b[2];
      var y1 = yScale(Math.min(hi, maxWind));
      var y2 = yScale(lo);
      return '<rect x="' + padL + '" y="' + y1 + '" width="' + plotW +
             '" height="' + (y2 - y1) + '" fill="' + c + '" fill-opacity="0.38"/>';
    }).join("");
    var pathD = "M " + pts.map(function(p) {
      return xScale(new Date(p.t).getTime()).toFixed(1) + "," +
             yScale(p.wind_kt || 0).toFixed(1);
    }).join(" L ");
    var dotsSvg = pts.map(function(p) {
      var x = xScale(new Date(p.t).getTime()).toFixed(1);
      var y = yScale(p.wind_kt || 0).toFixed(1);
      return '<circle cx="' + x + '" cy="' + y + '" r="2.4" fill="#0a1324" ' +
             'stroke="#ffffff" stroke-width="0.8"/>';
    }).join("");
    var ticks = [0, 35, 65, 85, 100, 115, 140, 160];
    var yLabels = ticks.filter(function(v) { return v <= maxWind; }).map(function(v) {
      var y = yScale(v);
      return '<g><line x1="' + (padL - 3) + '" y1="' + y + '" x2="' + padL +
             '" y2="' + y + '" stroke="#3a4d6e" stroke-width="0.6"/>' +
             '<text x="' + (padL - 6) + '" y="' + (y + 3) +
             '" text-anchor="end" font-size="9" fill="#8ea2bd">' + v + '</text></g>';
    }).join("");
    var nTicks = 3;
    var xLabels = "";
    for (var i = 0; i < nTicks; i++) {
      var t = tMin + (i * (tMax - tMin) / (nTicks - 1 || 1));
      var x = xScale(t);
      var d = new Date(t);
      var m = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
      var label = m[d.getUTCMonth()] + " " + d.getUTCDate();
      xLabels += '<text x="' + x + '" y="' + (H - padB + 13) +
                 '" text-anchor="middle" font-size="9" fill="#8ea2bd">' + label + '</text>';
    }
    return (
      '<svg class="wind-chart" viewBox="0 0 ' + W + ' ' + H +
      '" preserveAspectRatio="xMidYMid meet">' +
        '<rect x="' + padL + '" y="' + padT + '" width="' + plotW +
          '" height="' + plotH + '" fill="#07101c"/>' +
        bandRects +
        '<path d="' + pathD + '" fill="none" stroke="#ffffff" ' +
          'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>' +
        dotsSvg +
        yLabels + xLabels +
        '<rect x="' + padL + '" y="' + padT + '" width="' + plotW +
          '" height="' + plotH + '" fill="none" stroke="#243452"/>' +
      '</svg>'
    );
  }
})();
"""


# ---------------------------------------------------------------------------
# Full HTML template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{basin_name} TC Tracks · {year}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root {{
    --bg: #07101c; --panel: #0f1a2a; --border: #1a2840;
    --fg: #e5edf6; --muted: #8ea2bd;
    --td: #3fa4ff; --ts: #46c56a;
    --c1: #ffe14d; --c2: #ff9a2f; --c3: #ff4d3b;
    --c4: #e33ad4; --c5: #b03bff;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; background: var(--bg); color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    -webkit-font-smoothing: antialiased; }}
  .wrap {{ display: flex; gap: 12px; padding: 10px;
    max-width: 1400px; margin: 0 auto; flex-wrap: wrap; }}
  .map-box {{ flex: 1 1 820px; min-width: 0;
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 10px; overflow: hidden; position: relative; }}
  .map-head {{ padding: 10px 14px 6px; display: flex; justify-content: space-between;
    flex-wrap: wrap; gap: 8px; font-size: 14px; }}
  .map-head .title {{ font-weight: 700; color: #f1f7fd; font-size: 15px; }}
  .map-head .sub {{ color: var(--muted); font-size: 12px; margin-top: 2px; }}
  .map-head .stats {{ color: var(--muted); font-size: 12px; text-align: right; }}
  .map-head .stats b {{ color: #f1f7fd; }}
  .map-head .stats .ace {{ color: var(--c1); font-weight: 700; }}
  .map-svg-wrap {{ position: relative; }}
  svg.map {{ width: 100%; height: auto; display: block; background: #0b2a48; }}

  /* Active-storm animated icon.
     Size + rotation come from SVG attributes / <animateTransform>
     (more reliable than CSS for <use> elements). Only the glow and
     label text styling stay in CSS. */
  .active-icon .label {{
    dominant-baseline: middle;
    pointer-events: none;
    paint-order: stroke; stroke: #07101c; stroke-width: 0;
  }}
  .active-icon .name {{ fill: #f1f7fd; font-size: 12px; font-weight: 700;
    paint-order: stroke; stroke: #07101c; stroke-width: 3;
    stroke-linejoin: round; pointer-events: none; }}
  .active-icon {{ cursor: pointer; }}

  /* Sidebar */
  .side {{ flex: 0 0 340px; display: flex; flex-direction: column; gap: 8px; }}
  .panel-title {{ color: var(--muted); font-size: 12px; margin: 2px 2px 0;
    text-transform: uppercase; letter-spacing: 0.8px; font-weight: 700; }}
  .storm-list {{ background: var(--panel); border: 1px solid var(--border);
    border-radius: 10px; overflow: hidden; max-height: 820px; overflow-y: auto;
    scrollbar-color: #2a3e5c transparent; }}
  .storm-list::-webkit-scrollbar {{ width: 8px; }}
  .storm-list::-webkit-scrollbar-thumb {{ background: #2a3e5c; border-radius: 4px; }}
  .storm-card {{ padding: 10px 12px; border-bottom: 1px solid var(--border); }}
  .storm-card:last-child {{ border-bottom: 0; }}
  .storm-card.active {{ background: rgba(255,184,58,0.08); }}
  .storm-top {{ display: flex; align-items: center; justify-content: space-between;
    gap: 8px; margin-bottom: 4px; }}
  .storm-name {{ font-weight: 700; color: #f1f7fd; font-size: 14px; }}
  .storm-cat {{ font-size: 11px; font-weight: 700; padding: 2px 8px;
    border-radius: 999px; color: #07101c; }}
  .storm-meta {{ font-size: 11px; color: var(--muted); line-height: 1.5; }}
  .storm-meta .row {{ display: flex; justify-content: space-between; }}
  .storm-meta .lbl {{ color: var(--faint); }}
  .storm-meta .val {{ color: var(--fg); font-variant-numeric: tabular-nums; }}
  .storm-active {{ font-size: 10px; color: var(--c1); font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.6px; margin-left: 6px; }}
  .storm-active::before {{ content: "● "; }}

  /* SSHS color-bar legend (right of map) */
  .legend {{ position: absolute; top: 70px; right: 12px;
    background: rgba(11,26,48,0.85); border: 1px solid var(--border);
    border-radius: 8px; padding: 8px 10px; font-size: 11px;
    color: var(--muted); backdrop-filter: blur(4px); }}
  .legend .item {{ display: flex; align-items: center; gap: 6px;
    margin: 3px 0; }}
  .legend .dot {{ width: 10px; height: 10px; border-radius: 50%; }}

  @media (max-width: 820px) {{
    .side {{ flex: 1 1 100%; }}
    .storm-list {{ max-height: 500px; }}
    .legend {{ display: none; }}
  }}

  /* Watermark — same size as before, just more visible. The dark
     stroke underneath lets it read clearly over both the bright
     ocean and light land areas. */
  .wm {{ fill: #ffffff; fill-opacity: 0.55; font-weight: 700;
    font-size: 26px; letter-spacing: 0.5px;
    stroke: #000000; stroke-opacity: 0.45; stroke-width: 1.1px;
    paint-order: stroke fill;
    font-family: -apple-system, Segoe UI, Roboto, sans-serif;
    pointer-events: none; }}

  /* Hover tooltip for track dots */
  .track-dot {{ cursor: pointer; }}
  .track-dot:hover {{ r: 6.5; stroke: #fff; stroke-width: 1.6; }}
  #dot-tooltip {{ position: fixed; z-index: 9000; pointer-events: none;
    background: rgba(10,18,34,0.96); color: var(--fg);
    border: 1px solid #2a3e5c; border-radius: 8px;
    padding: 8px 10px; font-size: 12px; line-height: 1.45;
    min-width: 170px; box-shadow: 0 6px 20px rgba(0,0,0,0.55);
    backdrop-filter: blur(4px); }}
  #dot-tooltip[hidden] {{ display: none; }}
  #dot-tooltip .tt-name {{ font-weight: 800; color: #f1f7fd;
    font-size: 13px; letter-spacing: 0.3px; }}
  #dot-tooltip .tt-time {{ color: var(--muted); font-size: 11px;
    margin-bottom: 4px; }}
  #dot-tooltip .tt-row {{ display: flex; justify-content: space-between;
    gap: 10px; margin-top: 2px; }}
  #dot-tooltip .tt-lbl {{ color: var(--muted); }}
  #dot-tooltip .tt-val {{ color: var(--fg); font-variant-numeric: tabular-nums; }}
  #dot-tooltip .tt-cat {{ display: inline-block; padding: 1px 8px;
    border-radius: 999px; font-size: 10px; font-weight: 700;
    color: #07101c; margin-top: 2px; }}

  /* Clickable storm cards (every card is clickable now) */
  .storm-card.clickable {{ cursor: pointer; transition: background 0.15s; }}
  .storm-card.clickable:hover {{ background: rgba(255,255,255,0.04); }}
  .storm-card.clickable.active:hover {{ background: rgba(255,184,58,0.14); }}
  .click-hint {{ font-size: 10px; color: var(--muted); margin-top: 4px;
    text-transform: uppercase; letter-spacing: 0.6px; font-weight: 700;
    opacity: 0.6; }}
  .storm-card.clickable.active .click-hint {{ color: var(--c1); opacity: 0.75; }}
  .storm-card.clickable.open .click-hint {{ opacity: 0.25; }}

  /* Inline detail placard (appears when a storm card is clicked) */
  .storm-placard {{ margin-top: 10px; background: #0a1324;
    border: 1px solid #243452; border-radius: 8px; overflow: hidden; }}
  /* Right padding leaves room for the spinning corner icon so it
     doesn't crowd the storm name. */
  .placard-banner {{ padding: 10px 56px 10px 12px; position: relative; }}
  .placard-banner .placard-spinner {{ position: absolute; top: 10px; right: 12px;
    width: 34px; height: 34px; opacity: 0.85;
    filter: drop-shadow(0 0 3px rgba(0,0,0,0.35)); }}
  .placard-banner .placard-spinner svg {{ width: 100%; height: 100%;
    animation: placard-spin 2.6s linear infinite;
    transform-origin: 50% 50%; }}
  @keyframes placard-spin {{
    from {{ transform: rotate(360deg); }}
    to {{ transform: rotate(0deg); }}
  }}
  .placard-banner .pl-row1 {{ font-size: 12px; font-weight: 800;
    letter-spacing: 0.4px; display: flex; align-items: center; gap: 8px;
    text-transform: uppercase; }}
  .placard-banner .pl-cat {{ display: inline-block; padding: 2px 8px;
    border-radius: 4px; background: rgba(0,0,0,0.15); color: inherit; }}
  .placard-banner .pl-intensity {{ display: flex; align-items: baseline;
    gap: 10px; margin-top: 6px; }}
  .placard-banner .pl-big {{ font-size: 44px; font-weight: 900;
    line-height: 1; font-variant-numeric: tabular-nums; }}
  .placard-banner .pl-units {{ font-size: 12px; font-weight: 700;
    line-height: 1.25; }}
  .placard-banner .pl-deets {{ display: grid;
    grid-template-columns: 1fr 1fr; gap: 2px 12px; margin-top: 8px;
    font-size: 11px; font-weight: 600; }}
  .placard-banner .pl-deets span {{ opacity: 0.7; margin-right: 4px; }}
  .placard-banner .pl-deets b {{ font-weight: 700;
    font-variant-numeric: tabular-nums; }}
  .placard-chart-label {{ padding: 8px 12px 2px; font-size: 11px;
    color: var(--muted); text-transform: uppercase; letter-spacing: 0.6px;
    font-weight: 700; }}
  .wind-chart {{ display: block; width: 100%; padding: 0 6px 8px; }}
  .chart-empty {{ padding: 12px; font-size: 12px; color: var(--muted); }}
</style>
</head>
<body>
<div class="wrap">
  <div class="map-box">
    <div class="map-head">
      <div>
        <div class="title">{year} {basin_name} TC Tracks</div>
        <div class="sub">As of {updated}</div>
      </div>
      <div class="stats">
        <b>{named}</b> {named_label} · <b>{cat1plus}</b> {cat1plus_label} ·
        <b>{cat5}</b> {cat5_label} · <span class="ace">{total_ace} ACE</span>
      </div>
    </div>
    <div class="map-svg-wrap">
      <svg class="map" viewBox="0 0 {map_w} {map_h}" preserveAspectRatio="xMidYMid meet">
        {defs}
        {basemap_svg}
        {tracks_svg}
        {active_svg}
        <text class="wm" x="{wm_x}" y="{wm_y}" text-anchor="end">@WeathermanAAA_</text>
      </svg>
      <div class="legend">
        <div class="item"><span class="dot" style="background:var(--td)"></span>TD (&lt;34 kt)</div>
        <div class="item"><span class="dot" style="background:var(--ts)"></span>TS (34–63)</div>
        <div class="item"><span class="dot" style="background:var(--c1)"></span>Cat 1 (64–82)</div>
        <div class="item"><span class="dot" style="background:var(--c2)"></span>Cat 2 (83–95)</div>
        <div class="item"><span class="dot" style="background:var(--c3)"></span>Cat 3 (96–112)</div>
        <div class="item"><span class="dot" style="background:var(--c4)"></span>Cat 4 (113–136)</div>
        <div class="item"><span class="dot" style="background:var(--c5)"></span>Cat 5 (≥137)</div>
      </div>
    </div>
  </div>

  <div class="side">
    <div class="panel-title">{year} Season · {storm_count} Storms</div>
    <div class="storm-list" id="storms">
      {storm_cards}
    </div>
  </div>
</div>

<div id="dot-tooltip" hidden></div>

<script id="storms-payload" type="application/json">{storms_json}</script>
<script>
{tracks_js}
</script>
</body>
</html>
"""


def _cat_style(cls: str) -> tuple[str, str]:
    """Return (background color, short label) for a storm badge."""
    return SSHS_COLORS.get(cls, SSHS_COLORS["TD"]), cls.replace("C", "Cat ")


def _fmt_date_range(start: str | None, end: str | None) -> str:
    def fmt(iso):
        try:
            d = dt.datetime.fromisoformat(iso)
            return d.strftime("%b %-d")
        except Exception:
            return "?"
    if not start:
        return "—"
    s = fmt(start)
    e = fmt(end) if end else s
    return f"{s} – {e}" if s != e else s


def render_storm_card(storm: dict) -> str:
    cat = storm.get("max_category", "TD")
    color, label = _cat_style(cat)
    active_tag = '<span class="storm-active">Active</span>' if storm.get("is_active") else ''
    is_active = storm.get("is_active")
    # Every card is clickable — active cards open the pinned live placard
    # at the top; inactive cards expand an inline peak-intensity placard.
    classes = "storm-card clickable"
    if is_active:
        classes += " active"
    peak_wind = storm.get("peak_wind_kt")
    peak_pres = storm.get("peak_pressure_mb")
    ace = storm.get("ace") or 0
    sid = storm.get("sid") or ""
    hint_text = "Click for wind history" if is_active else "Click for peak intensity"
    click_hint = f'<div class="click-hint">▸ {hint_text}</div>'
    placard_slot = f'<div class="storm-placard" id="placard-{sid}" hidden></div>'
    return f"""
<div class="{classes}" id="card-{sid}" data-sid="{sid}">
  <div class="storm-top">
    <div class="storm-name">{storm.get('name') or 'UNNAMED'}{active_tag}</div>
    <div class="storm-cat" style="background:{color}">{label}</div>
  </div>
  <div class="storm-meta">
    <div class="row"><span class="lbl">Active</span><span class="val">{_fmt_date_range(storm.get('start'), storm.get('end'))}</span></div>
    <div class="row"><span class="lbl">Peak wind</span><span class="val">{peak_wind if peak_wind is not None else '—'} kt</span></div>
    <div class="row"><span class="lbl">Peak pressure</span><span class="val">{peak_pres if peak_pres is not None else '—'} mb</span></div>
    <div class="row"><span class="lbl">ACE</span><span class="val">{ace:.2f}</span></div>
  </div>
  {click_hint}
  {placard_slot}
</div>
"""


def render_html(payload: dict, extent, countries_geojson, coastline_geojson) -> str:
    basemap_svg = render_basemap_svg(extent, countries_geojson, coastline_geojson)
    tracks_svg = render_tracks_svg(payload["storms"], extent)
    active_svg = render_active_icons(payload["storms"], extent)
    storm_cards = "\n".join(render_storm_card(s) for s in payload["storms"]) or (
        '<div class="storm-card"><div class="storm-meta">'
        'No storms yet this year.</div></div>'
    )
    header = payload["header"]
    vocab = payload["vocab"]
    # Slim payload for client-side — only the fields the placard/tooltip
    # actually read. Keeps the inline blob small.
    slim_storms = []
    for s in payload["storms"]:
        slim_storms.append({
            "sid": s.get("sid"),
            "name": s.get("name"),
            "is_active": s.get("is_active"),
            "current_category": s.get("current_category"),
            "max_category": s.get("max_category"),
            "ace": s.get("ace"),
            "points": s.get("points"),
        })
    storms_json = json.dumps({"storms": slim_storms}, separators=(",", ":"))
    # Guard against </script> accidentally appearing in a storm name —
    # standard JSON-in-script escape.
    storms_json = storms_json.replace("</", "<\\/")
    return HTML_TEMPLATE.format(
        basin_name=payload["basin_name"],
        year=payload["year"],
        updated=payload["updated"],
        named=header["named"], named_label=vocab["named"],
        cat1plus=header["cat1plus"], cat1plus_label=vocab["cat1plus"],
        cat5=header["cat5"], cat5_label=vocab["cat5"],
        total_ace=f"{header['total_ace']:.2f}",
        map_w=MAP_W, map_h=MAP_H,
        defs=SVG_DEFS,
        basemap_svg=basemap_svg,
        tracks_svg=tracks_svg,
        active_svg=active_svg,
        wm_x=MAP_W - 20, wm_y=40,
        storm_cards=storm_cards,
        storm_count=len(payload["storms"]),
        storms_json=storms_json,
        tracks_js=TRACKS_JS,
    )


# ---------------------------------------------------------------------------
# Header stats (named storms / typhoons / etc.)
# ---------------------------------------------------------------------------

def compute_header_stats(storms: list[dict]) -> dict:
    named = sum(1 for s in storms if _sshs_rank(s["max_category"]) >= 1)  # TS+
    cat1plus = sum(1 for s in storms if _sshs_rank(s["max_category"]) >= 2)  # C1+
    cat3plus = sum(1 for s in storms if _sshs_rank(s["max_category"]) >= 4)  # C3+
    cat5 = sum(1 for s in storms if s["max_category"] == "C5")
    total_ace = round(sum(s["ace"] for s in storms), 2)
    return {
        "named": named,
        "cat1plus": cat1plus,
        "cat3plus": cat3plus,
        "cat5": cat5,
        "total_ace": total_ace,
    }


# ---------------------------------------------------------------------------
# Main (partial — rendering comes in the next step)
# ---------------------------------------------------------------------------

def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--basin", "-b", default="wp", choices=sorted(BASINS.keys()))
    parser.add_argument("--csv", help="Override IBTrACS CSV path.")
    parser.add_argument("--no-live", action="store_true")
    parser.add_argument("--dump-json", action="store_true",
                        help="Just print the extracted storm data JSON and exit.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    basin = args.basin
    basin_cfg = BASINS[basin]
    log = f"[{basin}-tracks]"
    year = dt.date.today().year

    csv_path = Path(args.csv) if args.csv else (
        Path(os.environ.get(f"IBTRACS_{basin.upper()}_CSV",
                            str(HERE / f"ibtracs.{basin_cfg['ibtracs_file_code']}.list.v04r01.csv")))
    )
    if not csv_path.exists():
        print(f"{log} ERROR: IBTrACS file not found at {csv_path}", file=sys.stderr)
        return 2

    ibtracs_frame = load_ibtracs_current_year(csv_path, basin_cfg, year, log_prefix=log)

    live_frame = pd.DataFrame()
    if FETCH_LIVE and not args.no_live:
        print(f"{log} attempting live {basin_cfg['agency_name']} fetch for {year} ...")
        live_frame = fetch_live_season(year, basin_cfg, log)

    storms = merge_and_extract_storms(ibtracs_frame, live_frame, basin_cfg)
    header = compute_header_stats(storms)

    payload = {
        "basin": basin,
        "basin_name": basin_cfg["full_name"],
        "year": year,
        "updated": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "header": header,
        "vocab": basin_cfg["vocab"],
        "storms": storms,
    }

    if args.dump_json:
        print(json.dumps(payload, indent=2, default=str))
        return 0

    # Load Natural Earth basemap — prefer 50m (shows small islands) and
    # fall back to 110m if the higher-res files aren't present.
    countries = (load_natural_earth(HERE / "ne_50m_admin_0_countries.geojson")
                 or load_natural_earth(HERE / "ne_110m_admin_0_countries.geojson"))
    coast = (load_natural_earth(HERE / "ne_50m_coastline.geojson")
             or load_natural_earth(HERE / "ne_110m_coastline.geojson"))
    if countries is None and coast is None:
        print(f"{log} WARN: no Natural Earth GeoJSON found — basemap will "
              f"only show the grid. Workflow downloads these into the repo.")

    html = render_html(payload, basin_cfg["extent"], countries, coast)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_DIR / f"{basin}_tracks_data.json"
    html_path = OUTPUT_DIR / f"{basin}_tracks.html"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    html_path.write_text(html, encoding="utf-8")
    print(f"{log} wrote {json_path}")
    print(f"{log} wrote {html_path}")
    print(f"{log} {year}: {header['named']} named · "
          f"{header['cat1plus']} {basin_cfg['vocab']['cat1plus']} · "
          f"{header['cat5']} {basin_cfg['vocab']['cat5']} · "
          f"{header['total_ace']} ACE")
    active = [s for s in storms if s["is_active"]]
    if active:
        print(f"{log} active storms: " + ", ".join(
            f"{s['name']} ({s['current_category']}, {s['peak_wind_kt']} kt peak)" for s in active))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
