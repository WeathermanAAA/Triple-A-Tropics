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
            "https://www.metoc.navy.mil/jtwc/products/atcf/btk/bwp{nn}{yy}.dat",
            "https://www.nrlmry.navy.mil/atcf_web/docs/tracks/{year}/bwp{nn}{yy}.dat",
            "https://tropic.ssec.wisc.edu/real-time/atcf/btk/bwp{nn}{yy}.dat",
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
            "https://ftp.nhc.noaa.gov/atcf/btk/bal{nn}{yy}.dat",
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
            "https://ftp.nhc.noaa.gov/atcf/btk/bep{nn}{yy}.dat",
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
    """Parse an ATCF b-deck file into the same schema as the IBTrACS frame."""
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
    """Merge IBTrACS + live frames, dedupe per-storm (live wins for
    the current-year storms it has), then group into per-storm records."""
    frames = [df for df in (ibtracs, live) if not df.empty]
    if not frames:
        return []
    df = pd.concat(frames, ignore_index=True)

    # If both IBTrACS and live have the same storm, prefer the live data
    # for observations that overlap. Key is (SID, time) for IBTrACS; for
    # live the SID won't match IBTrACS's, so they can coexist. We dedupe
    # by IBTrACS SID replacing it with live data when the live b-deck has
    # any observations for that storm number.
    #
    # Practical approach: if a live entry exists for the same storm
    # number and season (matched via ATCF naming), drop the IBTrACS rows
    # for that storm. Since we don't have a clean mapping, we instead
    # just keep both and dedupe on (time, rounded lat/lon) ties.
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
        start_t = None
        end_t = None
        max_cls = "TD"
        for p in points:
            w = p["wind_kt"]
            nat = p["nature"] or ""
            # Consider NR as eligible for current-season provisional data
            nat_ok = nat in eligible_natures or nat == "NR" or nat == ""
            if nat_ok and pd.notna(w) and w >= 34:
                storm_ace += (w ** 2) / 10_000.0
                if start_t is None:
                    start_t = p["time"]
                end_t = p["time"]
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
    """Build the SVG basemap: ocean fill, land polygons, coastlines, grid."""
    project, _ = build_projection(extent)
    lon_min, lon_max, lat_min, lat_max = extent
    parts = []

    # Ocean background
    parts.append(f'<rect x="0" y="0" width="{MAP_W}" height="{MAP_H}" fill="#0b2a48"/>')

    # Countries / land
    if countries_geojson is not None:
        parts.append('<g class="land" fill="#3a4f3b" stroke="none">')
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

    # Coastlines (drawn on top of countries for a crisp edge)
    if coastline_geojson is not None:
        parts.append('<g class="coast" fill="none" stroke="#1a1a1a" '
                     'stroke-width="0.8" stroke-linejoin="round" stroke-linecap="round">')
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

    # Graticule (lat/lon grid)
    parts.append('<g class="grid" stroke="#5a7090" stroke-width="0.4" stroke-dasharray="3 4" opacity="0.55">')
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

    # Axis labels (outside the map area would need margin — we draw inside
    # at the edges with a faint halo)
    parts.append('<g class="axis-labels" fill="#aec1df" font-size="13" '
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
    at every 6-hour observation. Colors follow SSHWS."""
    project, _ = build_projection(extent)
    parts = ['<g class="tracks">']
    for storm in storms:
        pts = storm.get("points") or []
        if len(pts) < 1:
            continue
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
            parts.append(f'<path d="{d}" fill="none" stroke="#0e1624" '
                         'stroke-width="1.2" stroke-opacity="0.9" '
                         'stroke-linejoin="round" stroke-linecap="round"/>')
        # Dots — radius depends on whether the point is at TS+ (bigger) or not
        for (x, y), p in zip(xy, pts):
            cls = p.get("cls") or "TD"
            wind = p.get("wind_kt")
            color = SSHS_COLORS.get(cls, SSHS_COLORS["TD"])
            # Bigger dot for stronger systems
            if cls == "TD":
                r = 3
            elif cls == "TS":
                r = 4
            else:
                r = 5
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{color}" '
                f'stroke="#081220" stroke-width="0.8"/>')
    parts.append('</g>')
    return "\n".join(parts)


def render_active_icons(storms: list[dict], extent) -> str:
    """For each active storm, place a spinning+glowing cyclone-swirl icon
    at its most recent position. Non-active storms get a small labeled
    badge at their last known point instead."""
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
        # Placement: outer <g> translates to storm position.
        # Sizing: <use> MUST have width/height/x/y as SVG attributes —
        # CSS-based sizing on <use> renders unreliably across browsers
        # (was showing massive + SE-displaced).
        # Spin: wrap the <use> in a <g> with <animateTransform> (SVG
        # native; always works). Don't try to CSS-rotate the <use> itself.
        size = 48            # overall icon size in map units
        half = size / 2
        parts.append(
            f'<g class="active-icon" transform="translate({x:.1f},{y:.1f})" '
            f'style="--glow:{color};">'
            f'<g class="spin-wrap" style="filter:drop-shadow(0 0 10px {color});">'
            f'<use href="#tc-swirl" x="{-half}" y="{-half}" '
            f'width="{size}" height="{size}" style="color:{color};"/>'
            f'<animateTransform attributeName="transform" attributeType="XML" '
            f'type="rotate" from="0" to="360" dur="2.4s" '
            f'repeatCount="indefinite"/>'
            f'</g>'
            f'<text class="label" y="5" text-anchor="middle" '
            f'font-size="15" font-weight="800" fill="#07101c">{label}</text>'
            f'<text class="name" x="{half + 6}" y="4" text-anchor="start">{name}</text>'
            f'</g>'
        )
    parts.append('</g>')
    return "\n".join(parts)


SVG_DEFS = """
<defs>
  <!-- Reusable cyclone-swirl icon. Three curved arms around a center,
       viewBox -50..50 so transform translate() places the center. -->
  <symbol id="tc-swirl" viewBox="-50 -50 100 100" overflow="visible">
    <!-- Soft halo ring (pulses) -->
    <circle cx="0" cy="0" r="34" fill="currentColor" fill-opacity="0.12"
            class="pulse"/>
    <!-- Swirl arms -->
    <g class="arms" fill="currentColor">
      <path d="M 0,-24 C 14,-24 24,-14 24,0 C 24,-8 18,-14 10,-14
               C 2,-14 -4,-8 -4,0 L -12,0 C -12,-13 -4,-24 0,-24 Z"
            opacity="0.95"/>
      <path d="M 0,24 C -14,24 -24,14 -24,0 C -24,8 -18,14 -10,14
               C -2,14 4,8 4,0 L 12,0 C 12,13 4,24 0,24 Z"
            opacity="0.95"/>
    </g>
    <!-- Eye -->
    <circle cx="0" cy="0" r="10" fill="#07101c" stroke="currentColor"
            stroke-width="1.5"/>
  </symbol>
</defs>
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

  /* Watermark */
  .wm {{ fill: #e5edf6; fill-opacity: 0.16; font-weight: 700;
    font-size: 26px; letter-spacing: 0.5px;
    font-family: -apple-system, Segoe UI, Roboto, sans-serif; }}
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
    classes = "storm-card active" if storm.get("is_active") else "storm-card"
    peak_wind = storm.get("peak_wind_kt")
    peak_pres = storm.get("peak_pressure_mb")
    ace = storm.get("ace") or 0
    return f"""
<div class="{classes}">
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

    # Load Natural Earth basemap (optional — graceful fallback if missing)
    countries = load_natural_earth(HERE / "ne_110m_admin_0_countries.geojson")
    coast = load_natural_earth(HERE / "ne_110m_coastline.geojson")
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
