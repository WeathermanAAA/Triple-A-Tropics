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

# Single source of truth for ACE (formula, per-basin nature rule, wind
# preference, rounding), the shared ATCF b-deck parser, the IBTrACS-vs-live
# storm merge, and the observability timestamps. This generator no longer
# computes ACE or merges storms on its own - it routes through ace_core so the
# tracks feed reports the IDENTICAL per-storm peak wind + ACE (and season total)
# as the ACE feed.
import ace_core as ac
from ace_core import (
    SSHS_COLORS,
    build_global_geojson,
    compute_header_stats,
    merge_and_extract_storms,
    sshs_class,
    sshs_label,
    _sshs_rank,
)

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
        # JTWC/NHC convention: invests are named "<num><letter>" where the
        # letter is a single-character basin code (W=W.Pac, L=Atlantic,
        # E=E.Pac, C=Cen.Pac). Used to render invest names like "91W".
        "invest_letter": "W",
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
        "invest_letter": "L",
        "agency_name": "NHC",
        "agency_url": "https://www.nhc.noaa.gov/",
        "atcf_patterns": [
            "https://triple-a-tropics-proxy.coloradoskier2018.workers.dev/atcf/btk/bal{nn}{year}.dat",
            "https://ftp.nhc.noaa.gov/atcf/btk/bal{nn}{year}.dat",
            # NHC only (proxy -> ftp.nhc). natyphoon.top is a WP/JTWC mirror; it
            # does not serve AL/EP b-decks (404/SSL), reserved for WP.
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
        "invest_letter": "E",
        "agency_name": "NHC",
        "agency_url": "https://www.nhc.noaa.gov/",
        "atcf_patterns": [
            "https://triple-a-tropics-proxy.coloradoskier2018.workers.dev/atcf/btk/bep{nn}{year}.dat",
            "https://ftp.nhc.noaa.gov/atcf/btk/bep{nn}{year}.dat",
            # NHC only (proxy -> ftp.nhc); natyphoon.top is WP/JTWC-only (see AL).
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
    # Global mode: composes the three per-basin JSONs (al/ep/wp) onto
    # one Pacific-centered Mercator-style extent. Triggered by
    # `--basin global`. main() bypasses IBTrACS load + live ATCF fetch
    # for this entry — it reads the per-basin tracks JSONs that earlier
    # workflow steps already produced. See main() for the special path.
    "global": {
        "short": "global",
        "name": "Global",
        "full_name": "Global",
        # Pacific-centered: Africa LEFT (lon=-25), Asia/Pacific MIDDLE
        # (lon=180), Americas RIGHT (lon=270 = -90), with a sliver of
        # Africa wrapping around to the right edge (lon=335 = -25).
        # Latitude -90..90 gives the standard equirectangular 2:1 aspect
        # — the polar caps render empty (no TC activity above ~70°) but
        # the map looks right and matches other global TC views.
        "extent": (-25.0, 335.0, -90.0, 90.0),
        "vocab": {"named": "named storms",
                  "cat1plus": "category 1+ storms",
                  "cat3plus": "major (cat 3+) storms",
                  "cat5": "category 5s"},
        # Marker indicating main() should compose per-basin JSONs rather
        # than load IBTrACS directly. No agency / atcf / ibtracs_file_code
        # is meaningful for global; missing keys here would crash earlier
        # code paths if accidentally invoked.
        "compose_from_basins": ["al", "ep", "wp"],
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


# Map per-agency STATUS codes (ATCF-style) onto the IBTrACS-style NATURE
# vocabulary the renderer expects. The main win here is that STATUS
# columns carry disturbance granularity that NATURE strips out:
#   DB = disturbance, LO = remnant low, WV = tropical wave,
#   MD = monsoon depression — all pre-TC / non-cyclone, rendered as
# up-triangles. NATURE collapses most of these into "NR" or blank,
# which is why the pre-TC portions of a track look like circles when
# we rely on NATURE alone.
# Single-sourced from ace_core (the shared ATCF dev-level -> NATURE table) so
# the rendering nature mapping and the ACE-eligibility nature mapping never drift.
_STATUS_TO_NATURE = ac.STATUS_TO_NATURE


def _best_nature(row: pd.Series) -> str:
    """Derive a single nature code from IBTrACS NATURE + USA_STATUS +
    agency wind estimates. The goal is to match JTWC/NHC operational
    best-track visual convention, where pre-genesis disturbance points
    are rendered as triangles (not circles).

    Priority:
      1. USA_STATUS if it maps to a known code — this is JTWC's ATCF
         granular status (DB, LO, WV, TD, TS, TY, HU, EX, ...) and is
         the most authoritative signal for shape classification.
      2. Explicit non-tropical NATURE codes (ET, SS, DS) — trusted
         because they're unambiguous.
      3. For ambiguous NATURE ("TS" / "NR" / "MX" / "") without a
         USA_STATUS, fall back to wind data: if ANY major agency
         (JTWC / WMO / JMA) has a positive wind estimate, the point is
         tropical (or subtropical, if NATURE says so) — otherwise it's
         a pre-genesis / post-dissipation disturbance (rendered as
         triangle). This catches WPAC storms' early invest phase
         where NATURE='TS' gets forward-filled but no agency has
         actually classified intensity yet.
    """
    s = row.get("USA_STATUS")
    if pd.notna(s):
        s = str(s).strip().upper()
        if s in _STATUS_TO_NATURE:
            return _STATUS_TO_NATURE[s]
    n = (row.get("NATURE") or "").strip().upper()
    # Explicit non-tropical / explicit disturbance codes: trust them
    if n in {"ET", "SS", "DS"}:
        return n
    # Ambiguous: need wind evidence to call this a classified cyclone
    for wcol in ("USA_WIND", "WMO_WIND", "TOKYO_WIND"):
        w = row.get(wcol)
        if pd.notna(w):
            try:
                if float(str(w).strip()) > 0:
                    return n if n in {"TS", "SS"} else "TS"
            except (ValueError, TypeError):
                pass
    # No agency wind estimate → pre-TC disturbance (triangle)
    return "DS"


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
            # `nature` drives the SVG rendering (triangles for disturbances) and
            # uses the USA_STATUS-aware _best_nature. `ace_nature` is the RAW
            # IBTrACS NATURE that ace_core uses for ACE eligibility - the same
            # signal the ACE feed uses - so the two feeds agree on ACE.
            "nature": _best_nature(row),
            "ace_nature": (row.get("NATURE") or "").strip().upper(),
            "source": "IBTrACS",
        })
    out = pd.DataFrame(rows)
    print(f"{log_prefix}   {len(out):,} current-year observations")
    return out


# ---------------------------------------------------------------------------
# Live ATCF b-deck fetch
# ---------------------------------------------------------------------------

# NOTE: the ATCF b-deck parser (_parse_atcf_latlon + parse_atcf_bdeck) now
# lives in ace_core.parse_bdeck - the SINGLE parser both generators use.


KNACKWX_ATCF_URL = "https://api.knackwx.com/atcf/v2"


def fetch_live_invests(season: int, basin_cfg: dict, log_prefix: str
                       ) -> pd.DataFrame:
    """Pull currently-active invests (90-99) from the knackwx API.

    Replaces a brittle 90-99 b-deck sweep that depended on a stale
    natyphoon mirror (last updated 2026-01-14 at the time of writing).
    knackwx aggregates JTWC/NHC's currently-active systems into a single
    JSON array refreshed on every JTWC/NHC bulletin push, so it surfaces
    today's 91W where the b-deck mirror still shows January's expired
    91W. As a side benefit, knackwx only returns ACTIVE systems — past
    invests don't accumulate (the recent_invest card filter becomes
    belt-and-suspenders).

    Filters to invests in this basin only (storm_num 90-99 AND
    origin_basin matching the basin's invest_letter). Numbered TCs
    still come from the per-storm b-deck path in fetch_live_season,
    unchanged — knackwx may or may not list them and we haven't
    confirmed yet."""
    try:
        import urllib.request
    except Exception:
        return pd.DataFrame()

    letter = basin_cfg.get("invest_letter", "")
    if not letter:
        return pd.DataFrame()

    try:
        req = urllib.request.Request(KNACKWX_ATCF_URL,
                                     headers={"User-Agent": FETCH_UA})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            if r.status != 200:
                return pd.DataFrame()
            data = json.loads(r.read().decode("utf-8", errors="ignore"))
    except Exception as e:  # noqa: BLE001
        print(f"{log_prefix}   knackwx fetch failed: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        return pd.DataFrame()

    if not isinstance(data, list):
        return pd.DataFrame()

    rows = []
    for it in data:
        if (it.get("origin_basin") or "").upper() != letter:
            continue
        atcf_id = (it.get("atcf_id") or "").strip()
        # "91W" → 91 (last char is the basin letter we already matched).
        try:
            storm_num = int(atcf_id[:-1])
        except (ValueError, IndexError):
            continue
        if not (90 <= storm_num <= 99):
            continue
        ts = it.get("analysis_time")
        if not ts:
            continue
        try:
            t = dt.datetime.fromisoformat(
                ts.replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, AttributeError):
            continue
        try:
            lat = float(it.get("latitude"))
            lon = float(it.get("longitude"))
        except (TypeError, ValueError):
            continue
        try:
            vmax = (float(it["winds"]) if it.get("winds") is not None
                    else float("nan"))
        except (TypeError, ValueError):
            vmax = float("nan")
        pres_raw = it.get("pressure")
        try:
            pres = (float(pres_raw) if pres_raw not in (None, 0)
                    else float("nan"))
        except (TypeError, ValueError):
            pres = float("nan")
        # Map ATCF dev-level to IBTrACS-style nature using the same
        # table parse_atcf_bdeck uses, so downstream classification
        # (rendering, ACE eligibility) doesn't care which path the row
        # came from.
        devlvl = (it.get("cyclone_nature") or "").strip().upper()
        nature = _STATUS_TO_NATURE.get(devlvl, "")
        if not nature:
            nature = "TS" if (pd.notna(vmax) and vmax > 0) else "DS"
        # Display name: knackwx uses "INVEST" for unnamed invests; fall
        # back to "<num><letter>" (91W / 92L / 93E) — same convention
        # the b-deck path uses for unnamed invests.
        name_raw = (it.get("storm_name") or "").strip()
        if name_raw and name_raw not in {"INVEST", "NAMELESS", "UNNAMED"}:
            name = name_raw
        else:
            name = f"{storm_num}{letter}"
        rows.append({
            # SID matches the b-deck path's SID format so a future
            # promotion to a numbered TC (with a real b-deck) doesn't
            # collide with this invest row.
            "SID": f"{basin_cfg['agency_name']}_{basin_cfg['short'].upper()}"
                   f"{storm_num:02d}{season}",
            "NAME": name,
            "season": season,
            "time": t,
            "lat": lat,
            "lon": lon,
            "wind_kt": vmax,
            "pressure_mb": pres,
            "nature": nature,
            "source": "live-knackwx",
            "storm_num": storm_num,
        })
    out = pd.DataFrame(rows)
    if not out.empty:
        print(f"{log_prefix}   knackwx: {len(out)} invest point(s) "
              f"from {out['SID'].nunique()} system(s)")
    return out


def fetch_live_season(season: int, basin_cfg: dict, log_prefix: str) -> pd.DataFrame:
    try:
        import urllib.request
        import urllib.error
    except Exception:
        return pd.DataFrame()

    yy = season % 100
    frames: list[pd.DataFrame] = []
    patterns = basin_cfg["atcf_patterns"]

    def _try_fetch_one(nn: int) -> bool:
        """Try the proxy chain for a single storm number. Append a parsed
        frame on success and return True; return False on any miss."""
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
                # Shared parser (ace_core) so the live fix set per named storm is
                # IDENTICAL to the ACE feed's - the merge then picks the same
                # source and both feeds get the same peak wind + ACE.
                frames.append(ac.parse_bdeck(text, season, basin_cfg))
                return True
            except Exception:
                continue
        return False

    # Numbered TCs (01-40). Bail after 3 consecutive misses to keep the
    # fetch fast — typical seasons have 1-3 active storms at a time.
    consecutive_misses = 0
    for nn in range(1, 41):
        hit = _try_fetch_one(nn)
        consecutive_misses = 0 if hit else consecutive_misses + 1
        if consecutive_misses >= 3:
            break

    # Invests (90-99) come from the knackwx API instead of the b-deck
    # mirror chain — see fetch_live_invests for why (mirror was 3 months
    # stale and the b-deck for a freshly-spawned invest doesn't exist
    # yet at the point JTWC announces it in their text bulletin).
    invests = fetch_live_invests(season, basin_cfg, log_prefix)
    if not invests.empty:
        frames.append(invests)

    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    print(f"{log_prefix}   live fetch: {len(out)} points from "
          f"{out['SID'].nunique()} storm(s)")
    return out


# ---------------------------------------------------------------------------
# Basemap (SVG coastlines from Natural Earth GeoJSON)
# ---------------------------------------------------------------------------

# Geographic -> SVG pixel mapping parameters
MAP_W = 1400    # SVG viewport width
MAP_H = 900     # SVG viewport height


def build_projection(extent: tuple[float, float, float, float],
                     map_w: int = MAP_W, map_h: int = MAP_H):
    """Return (project, extent_info). project(lon, lat) -> (x, y) in the
    map's SVG coordinate system.

    map_w / map_h default to the module constants (1400×900) which give
    a slightly squashed look on per-basin extents. The global Pacific-
    centered extent overrides to 1400×700 so 360°×180° gets rendered at
    a true 2:1 equirectangular aspect.

    When the extent crosses the antimeridian (lon_max > 180), longitudes
    that fall below lon_min are wrapped by +360 so a Pacific-centered
    global view can render features at lon=-30 (Africa) at the right
    side of the canvas instead of off-canvas to the left."""
    lon_min, lon_max, lat_min, lat_max = extent
    crosses_antimeridian = lon_max > 180

    def project(lon: float, lat: float) -> tuple[float, float]:
        if crosses_antimeridian and lon < lon_min:
            lon += 360
        x = (lon - lon_min) / (lon_max - lon_min) * map_w
        y = (lat_max - lat) / (lat_max - lat_min) * map_h
        return (x, y)

    return project, {
        "lon_min": lon_min, "lon_max": lon_max,
        "lat_min": lat_min, "lat_max": lat_max,
        "width": map_w, "height": map_h,
    }


def _ring_to_svg_path(ring: list, project,
                      map_w: int = MAP_W, map_h: int = MAP_H) -> str:
    """Convert a GeoJSON LineString/ring to an SVG path `d` string.
    Clips very loosely by skipping coords outside the extent by a big margin.

    For the global Pacific-centered view, features that cross the
    projection's wrap boundary (e.g. Greenland straddling lon=-25 in a
    -25..335 extent) produce huge horizontal jumps in projected space.
    Detect those (jump > 50% of map_w) and start a new "M" subpath at
    the boundary so the polygon doesn't draw as a stripe across the
    whole canvas."""
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
    if max(xs) < -map_w or min(xs) > map_w * 2 or max(ys) < -map_h or min(ys) > map_h * 2:
        return ""
    JUMP_THRESHOLD = map_w * 0.5
    d_parts: list[str] = []
    prev_x: float | None = None
    for x, y in parts:
        if prev_x is None or abs(x - prev_x) > JUMP_THRESHOLD:
            d_parts.append(f"M {x:.1f},{y:.1f}")
        else:
            d_parts.append(f"L {x:.1f},{y:.1f}")
        prev_x = x
    return " ".join(d_parts)


def load_natural_earth(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def render_basemap_svg(extent, countries_geojson: dict | None,
                       coastline_geojson: dict | None,
                       map_w: int = MAP_W, map_h: int = MAP_H) -> str:
    """Build the SVG basemap: ocean fill, land polygons, coastlines, grid.

    Color palette (matches user-supplied reference):
      ocean   #2463a0  medium blue
      land    #aeb2b5  bright gray
      borders #ffffff  white, bold
      grid    dashed white w/ low opacity
    """
    project, _ = build_projection(extent, map_w, map_h)
    lon_min, lon_max, lat_min, lat_max = extent
    parts = []

    # Ocean background
    parts.append(f'<rect x="0" y="0" width="{map_w}" height="{map_h}" fill="#2463a0"/>')

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
                    d = _ring_to_svg_path(ring, project, map_w, map_h)
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
                    d = _ring_to_svg_path(ring, project, map_w, map_h)
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
                d = _ring_to_svg_path(line, project, map_w, map_h)
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
        parts.append(f'<text x="{x:.1f}" y="{map_h - 8}" text-anchor="middle">{label}</text>')
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

def render_tracks_svg(storms: list[dict], extent,
                      map_w: int = MAP_W, map_h: int = MAP_H) -> str:
    """Draw each storm as a thin connecting polyline plus a colored dot
    at every 6-hour observation. Colors follow SSHWS.

    Every dot carries data-* attributes so the client-side hover tooltip
    can show the storm's intensity at that observation without another
    fetch. Dots for the same storm share a data-sid so clicking one can
    open that storm's detail placard.
    """
    project, _ = build_projection(extent, map_w, map_h)
    parts = ['<g class="tracks">']
    # Stash per-invest current-position coordinates from the first pass
    # so the second pass can draw the red X + label on top of every
    # other storm's markers, regardless of source order.
    invest_current_positions: list[tuple[dict, float, float, dict]] = []
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
            # Build the path with "M" breaks on big horizontal jumps so a
            # track that crosses our projection's wrap boundary (relevant
            # only for the global Pacific-centered extent) doesn't draw
            # as a horizontal stripe.
            JUMP_THRESHOLD = map_w * 0.5
            d_parts: list[str] = []
            prev_x: float | None = None
            for x, y in xy:
                if prev_x is None or abs(x - prev_x) > JUMP_THRESHOLD:
                    d_parts.append(f"M {x:.1f},{y:.1f}")
                else:
                    d_parts.append(f"L {x:.1f},{y:.1f}")
                prev_x = x
            d = " ".join(d_parts)
            # Dashed line for invests (90-99); solid for numbered TCs.
            # Keeping the dot styling identical so the wind-class colors
            # still convey intensity — only the connecting line changes.
            dash_attr = (' stroke-dasharray="4 3"' if storm.get("is_invest")
                         else "")
            parts.append(f'<path d="{d}" fill="none" stroke="#ffffff" '
                         'stroke-width="1.2" stroke-opacity="0.5" '
                         'stroke-linejoin="round" stroke-linecap="round"'
                         f'{dash_attr}/>')

        # Invest treatment (storm_num 90-99): past observations render as
        # small white triangles, the most-recent point renders as a red
        # crossed-X with a glow (filter defined in SVG_DEFS) and an
        # atcf_id label. This bypasses the wind-class dot styling below
        # because invests aren't classified TC/ST/non-TC the same way —
        # they're just disturbances. Numbered TCs (01-89) keep the
        # circle/square/triangle-by-nature treatment.
        # TODO: scale invest X glow intensity with NHC/JTWC formation
        # probability (Low/Med/High or %) when a data source is wired
        # up — knackwx doesn't return it today. Tracked in POST_LAUNCH.md.
        if storm.get("is_invest"):
            last_idx = len(xy) - 1
            for i, ((x, y), p) in enumerate(zip(xy, pts)):
                wind = p.get("wind_kt")
                pres = p.get("pressure_mb")
                t = p.get("t") or ""
                cls = p.get("cls") or "TD"
                wind_attr = f"{wind}" if wind is not None else ""
                pres_attr = f"{pres}" if pres is not None else ""
                common_attrs = (
                    f'data-sid="{sid}" data-name="{sname}" data-t="{t}" '
                    f'data-wind="{wind_attr}" data-pres="{pres_attr}" '
                    f'data-cls="{cls}" data-phase="invest"'
                )
                if i < last_idx:
                    # Past observation: small white triangle.
                    r = 3.5
                    half = r * 0.866
                    p1 = f"{x:.1f},{y - r:.1f}"
                    p2 = f"{x + half:.1f},{y + r * 0.5:.1f}"
                    p3 = f"{x - half:.1f},{y + r * 0.5:.1f}"
                    parts.append(
                        f'<polygon class="track-dot invest-past" '
                        f'points="{p1} {p2} {p3}" '
                        f'fill="#ffffff" stroke="#ffffff" '
                        f'stroke-width="0.9" stroke-opacity="0.85" '
                        f'{common_attrs}/>'
                    )
                else:
                    # Defer the current-position X + label to the
                    # second pass so it renders on top of every other
                    # storm's past markers (e.g., a neighboring TC's
                    # triangle that would otherwise overlap the X).
                    invest_current_positions.append(
                        (storm, x, y, p)
                    )
            continue

        # Dots — radius depends on whether the point is at TS+ (bigger) or not.
        # Shape depends on the point's lifecycle phase (matches the
        # JMA/JTWC best-track convention in the reference image):
        #   * circle      = tropical cyclone (nature "TS", or NR/MX/""
        #                   defaulting to tropical since they appear in
        #                   the track)
        #   * square      = subtropical cyclone (nature "SS")
        #   * up-triangle = anything else: extratropical (ET) or pre-TC
        #                   disturbance (DS / DB / LO). Every non-TC
        #                   point uses the same shape.
        # Shape radii match the circle radii so the group of points for
        # one storm reads as a coherent size progression (TD r≈3,
        # TS r≈4, major r≈5).
        for (x, y), p in zip(xy, pts):
            cls = p.get("cls") or "TD"
            wind = p.get("wind_kt")
            pres = p.get("pressure_mb")
            t = p.get("t") or ""
            nature = (p.get("nature") or "").upper()
            color = SSHS_COLORS.get(cls, SSHS_COLORS["TD"])
            # Bigger marker for stronger systems (applies to both circle
            # and triangle — measured as radius from centroid to apex).
            if cls == "TD":
                r = 3
            elif cls == "TS":
                r = 4
            else:
                r = 5

            # Phase classification is purely by nature code — wind speed
            # is not a tiebreaker.
            #   SS                          → square (subtropical)
            #   ET / DS / DB / LO           → down-triangle (non-TC: either
            #     extratropical or pre-TC disturbance / remnant low)
            #   TS / NR / MX / "" / other   → circle (tropical; NR/MX/blank
            #     default to tropical because those points appear in a
            #     storm's track but weren't explicitly re-categorized)
            # ATCF rows are mapped upstream so TD→TS, EX→ET, etc.
            if nature == "SS":
                phase = "st"
            elif nature in {"ET", "DS", "DB", "LO"}:
                phase = "non"
            else:
                phase = "tc"

            wind_attr = f"{wind}" if wind is not None else ""
            pres_attr = f"{pres}" if pres is not None else ""
            common_attrs = (
                f'fill="{color}" stroke="#ffffff" stroke-width="0.9" '
                f'stroke-opacity="0.85" '
                f'data-sid="{sid}" data-name="{sname}" data-t="{t}" '
                f'data-wind="{wind_attr}" data-pres="{pres_attr}" '
                f'data-cls="{cls}" data-phase="{phase}"'
            )
            if phase == "tc":
                parts.append(
                    f'<circle class="track-dot" cx="{x:.1f}" cy="{y:.1f}" '
                    f'r="{r}" {common_attrs}/>'
                )
            elif phase == "st":
                # Square centered on (x, y). Side length = 2r so its
                # "half-diagonal" is a touch bigger than the circle — but
                # visually the bounding box reads as the same footprint.
                parts.append(
                    f'<rect class="track-dot" x="{x - r:.1f}" y="{y - r:.1f}" '
                    f'width="{r * 2}" height="{r * 2}" {common_attrs}/>'
                )
            else:
                # Up-triangle centered on (x, y), apex pointing up.
                # sqrt(3)/2 ≈ 0.866 gives an equilateral shape whose
                # circumscribed-circle radius matches r, so it reads at
                # the same visual size as the TC circle beside it.
                half = r * 0.866
                p1 = f"{x:.1f},{y - r:.1f}"
                p2 = f"{x + half:.1f},{y + r * 0.5:.1f}"
                p3 = f"{x - half:.1f},{y + r * 0.5:.1f}"
                parts.append(
                    f'<polygon class="track-dot" points="{p1} {p2} {p3}" '
                    f'{common_attrs}/>'
                )

    # Second pass: every invest's red glowing X + atcf_id label, drawn
    # last so it sits on top of any neighboring storm's past markers
    # (triangles, dots, polygons) regardless of source order.
    # Active invests are SKIPPED here — render_active_icons paints a
    # bold red "L" + designation marker over their current position,
    # which would otherwise stack on top of the X.
    for storm, x, y, p in invest_current_positions:
        if storm.get("is_active"):
            continue
        sid = storm.get("sid") or ""
        sname = (storm.get("name") or "UNNAMED").replace('"', '')
        atcf_id = storm.get("atcf_id") or sname
        wind = p.get("wind_kt")
        pres = p.get("pressure_mb")
        t = p.get("t") or ""
        cls = p.get("cls") or "TD"
        wind_attr = f"{wind}" if wind is not None else ""
        pres_attr = f"{pres}" if pres is not None else ""
        common_attrs = (
            f'data-sid="{sid}" data-name="{sname}" data-t="{t}" '
            f'data-wind="{wind_attr}" data-pres="{pres_attr}" '
            f'data-cls="{cls}" data-phase="invest"'
        )
        parts.append(
            f'<g class="invest-current" '
            f'transform="translate({x:.1f},{y:.1f})" '
            f'filter="url(#invest-red-glow)">'
            f'<path class="track-dot" '
            f'd="M -7 -7 L 7 7 M -7 7 L 7 -7" '
            f'stroke="#ff2a2a" stroke-width="2.4" '
            f'stroke-linecap="round" fill="none" '
            f'{common_attrs}/>'
            f'</g>'
        )
        parts.append(
            f'<text class="invest-label" '
            f'x="{x + 11:.1f}" y="{y + 4:.1f}" '
            f'text-anchor="start">{atcf_id}</text>'
        )
    parts.append('</g>')
    return "\n".join(parts)


def _xml_escape(s: str) -> str:
    """Minimal escaping for text inside SVG <title>/<text> nodes."""
    return (str(s).replace("&", "&amp;")
                  .replace("<", "&lt;").replace(">", "&gt;"))


def _fmt_last_fix(iso: str | None) -> str:
    """ISO timestamp -> 'YYYY-MM-DD HH:MM UTC'. Used for the native-tooltip
    <title> on active-storm markers — per-basin pages are intentionally
    static SVG (no JS, for copy/paste), so a browser-native <title> is the
    no-script way to surface the latest fix time on hover."""
    if not iso:
        return ""
    try:
        d = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return str(iso)
    return d.strftime("%Y-%m-%d %H:%M UTC")


def render_active_icons(storms: list[dict], extent,
                        map_w: int = MAP_W, map_h: int = MAP_H) -> str:
    """For each active storm, place a marker at its most recent position:
      - TS+ (peak ≥ 34 kt and not flagged as an invest): the spinning,
        glowing TAT hurricane icon with category label inside.
      - Active invest (90-99 designation): a bold red "L" + designation
        ("92W", "AL90") rendered last, overlaying any other markers.
      - Active designated TD (numbered TC, peak < 34 kt, not an invest):
        a hollow blue circle + designation/name label below.

    Active invests historically rendered only as a red X via
    render_tracks_svg's invest path, but that X reads as a track-history
    marker, not a "warning is in effect right now" marker. The "L"
    matches NHC's surface-analysis convention for low-pressure systems
    and tells the reader at a glance that the system is being warned on.

    The blue hollow circle for designated TDs (e.g. Hagupit at TD
    intensity, before/after its TS phase) closes the gap between the
    spinning TS+ icon and the invest "L" — these systems are
    operationally numbered tropical cyclones, not invests, and a marker
    distinct from both reflects their status correctly."""
    project, _ = build_projection(extent, map_w, map_h)
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
        sid = storm.get("sid") or ""
        peak_kt = storm.get("peak_wind_kt") or 0.0
        is_invest = bool(storm.get("is_invest"))
        # Native-tooltip <title> (no JS) showing the storm name + the
        # timestamp of its most recent fix. Inserted as the first child of
        # whichever marker group is drawn below, so hovering any active
        # marker on the static page surfaces "NAME — Last fix: … UTC".
        disp_name = storm.get("name") or storm.get("atcf_id") or ""
        last_fix = _fmt_last_fix(last.get("t"))
        title_txt = (f"{disp_name} - Last fix: {last_fix}"
                     if last_fix else disp_name)
        title_el = f'<title>{_xml_escape(title_txt)}</title>' if title_txt else ''
        # Three-way fork on the marker style:
        #   * is_invest  → red "L"          (operational invest 90-99)
        #   * peak < 34  → blue hollow ○    (designated TD, not yet TS)
        #   * else       → spinning glyph   (TS+ named system)
        if is_invest:
            atcf_id = storm.get("atcf_id") or storm.get("name") or ""
            atcf_id = str(atcf_id).replace('"', '').upper()
            parts.append(
                f'<g class="active-icon active-invest" data-sid="{sid}" '
                f'transform="translate({x:.1f},{y:.1f})" '
                f'style="filter:drop-shadow(0 0 4px rgba(0,0,0,0.7));">'
                f'{title_el}'
                f'<text text-anchor="middle" dominant-baseline="central" '
                f'font-size="34" font-weight="900" fill="#ef4444" '
                f'paint-order="stroke" stroke="rgba(0,0,0,0.55)" '
                f'stroke-width="2.5" stroke-linejoin="round">L</text>'
                f'<text x="0" y="22" text-anchor="middle" '
                f'dominant-baseline="hanging" font-size="13" '
                f'font-weight="800" fill="#ffffff" paint-order="stroke" '
                f'stroke="rgba(0,0,0,0.7)" stroke-width="2.5" '
                f'stroke-linejoin="round">{atcf_id}</text>'
                f'</g>'
            )
            continue

        if peak_kt < 34.0:
            label = storm.get("name") or storm.get("atcf_id") or ""
            label = str(label).replace('"', '').upper()
            # Hollow TD marker: a chunky bright-cyan ring (TAT accent-2
            # #5dd3ff) wrapped in a white outer halo, so a designated TD
            # carries the same visual weight as the TS+ spinning icons.
            # The white circle is drawn first (wider stroke) and the cyan
            # ring sits centred on top (narrower stroke), leaving white
            # peeking on both edges = a haloed ring. Centre stays hollow —
            # that's the TD signal, distinct from the filled TS+ dots.
            parts.append(
                f'<g class="active-icon active-td" data-sid="{sid}" '
                f'transform="translate({x:.1f},{y:.1f})" '
                f'style="filter:drop-shadow(0 0 5px rgba(0,0,0,0.65));">'
                f'{title_el}'
                f'<circle cx="0" cy="0" r="14" fill="none" '
                f'stroke="#ffffff" stroke-width="6.5"/>'
                f'<circle cx="0" cy="0" r="14" fill="none" '
                f'stroke="#5dd3ff" stroke-width="3.5"/>'
                f'<text x="0" y="26" text-anchor="middle" '
                f'dominant-baseline="hanging" font-size="13" '
                f'font-weight="800" fill="#ffffff" paint-order="stroke" '
                f'stroke="rgba(0,0,0,0.7)" stroke-width="2.5" '
                f'stroke-linejoin="round">{label}</text>'
                f'</g>'
            )
            continue

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
        parts.append(f'''<g class="active-icon" data-sid="{sid}" transform="translate({x:.1f},{y:.1f})" style="filter:drop-shadow(0 0 6px {color});">{title_el}
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


# SVG <defs> — only the active-icon geometry is inline; the invest
# current-position X uses a Gaussian-blur red-glow filter referenced
# via filter="url(#invest-red-glow)" from render_tracks_svg.
# stdDeviation tuned to bloom ~6 px past the X without dominating the
# basemap; flood-color is fixed full-saturation red (uniform intensity
# until formation_probability becomes available — see POST_LAUNCH.md).
SVG_DEFS = (
    '<defs>'
    '<filter id="invest-red-glow" x="-200%" y="-200%" '
    'width="500%" height="500%">'
    '<feGaussianBlur in="SourceAlpha" stdDeviation="3.2" result="blur"/>'
    '<feFlood flood-color="#ff0000" flood-opacity="0.95" result="red"/>'
    '<feComposite in="red" in2="blur" operator="in" result="redblur"/>'
    '<feMerge>'
    '<feMergeNode in="redblur"/>'
    '<feMergeNode in="redblur"/>'
    '<feMergeNode in="SourceGraphic"/>'
    '</feMerge>'
    '</filter>'
    '</defs>'
)


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
  // Storm intensity is conventionally reported in nearest 5 mph
  // increments (NHC/JTWC operational practice). Movement speed still
  // uses the 1-mph ktToMph() above.
  function ktToMph5(k) { return Math.round(k * 1.15077945 / 5) * 5; }
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
      ? (Math.round(windKt) + " kt · " + ktToMph5(windKt) + " mph")
      : "-";
    var presTxt = d.pres && d.pres !== "" ? (Math.round(parseFloat(d.pres)) + " mb") : "-";
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
  function sshsLabel(cls) {
    if (cls === "TD") return "D";
    if (cls === "TS") return "S";
    return (cls || "").replace("C", "") || "D";  // C1→1, C2→2, etc.
  }
  function spinnerSvg(color, cls) {
    // <animateTransform> spins ONLY the hurricane path, leaving the
    // center label (D/S/1-5) stationary. Label matches the map icon.
    var label = sshsLabel(cls);
    return '<div class="placard-spinner">' +
      '<svg viewBox="-34 -34 68 68">' +
        '<g>' +
          '<path d="' + HURRICANE_PATH + '" fill="' + color + '" ' +
            'stroke="rgba(0,0,0,0.35)" stroke-width="1.2"/>' +
          '<animateTransform attributeName="transform" attributeType="XML" ' +
            'type="rotate" from="360" to="0" dur="2.6s" repeatCount="indefinite"/>' +
        '</g>' +
        '<text x="0" y="0" text-anchor="middle" dominant-baseline="central" ' +
          'font-size="22" font-weight="900" fill="#ffffff" ' +
          'paint-order="stroke" stroke="rgba(0,0,0,0.55)" stroke-width="2" ' +
          'stroke-linejoin="round">' + label + '</text>' +
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
    return "-";
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
        spinnerSvg(color, cls) +
        '<div class="pl-row1"><span class="pl-cat">' + catLabel + '</span><b>' +
          escapeHtml(storm.name || "UNNAMED") + '</b></div>' +
        '<div class="pl-intensity">' +
          '<div class="pl-big">' + ktToMph5(windKt) + '</div>' +
          '<div class="pl-units">mph<br>' + ktToKmh(windKt) + ' km/h</div>' +
        '</div>' +
        '<div class="pl-deets">' +
          '<div><span>Updated</span><b>' + fmtTime(last.t) + '</b></div>' +
          '<div><span>Location</span><b>' + loc + '</b></div>' +
          '<div><span>Pressure</span><b>' + (pres ? Math.round(pres) + " mb" : "-") + '</b></div>' +
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
        spinnerSvg(color, cls) +
        '<div class="pl-row1"><span class="pl-cat">PEAK · ' + catLabel + '</span><b>' +
          escapeHtml(storm.name || "UNNAMED") + '</b></div>' +
        '<div class="pl-intensity">' +
          '<div class="pl-big">' + ktToMph5(windKt) + '</div>' +
          '<div class="pl-units">mph<br>' + ktToKmh(windKt) + ' km/h</div>' +
        '</div>' +
        '<div class="pl-deets">' +
          '<div><span>Reached</span><b>' + fmtTime(peak.t) + '</b></div>' +
          '<div><span>Location</span><b>' + loc + '</b></div>' +
          '<div><span>Min pressure</span><b>' + (minPres ? Math.round(minPres) + " mb" : "-") + '</b></div>' +
          '<div><span>ACE</span><b>' + (storm.ace != null ? storm.ace.toFixed(2) : "-") + '</b></div>' +
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
      return '<circle cx="' + x + '" cy="' + y + '" r="3" fill="#0a1324" ' +
             'stroke="#ffffff" stroke-width="1.3"/>';
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
    // Rendering order matters: background + bands + axis first, then the
    // outer border rect, then the line and dots ON TOP so nothing —
    // including band seams — paints over them.
    return (
      '<svg class="wind-chart" viewBox="0 0 ' + W + ' ' + H +
      '" preserveAspectRatio="xMidYMid meet">' +
        '<rect x="' + padL + '" y="' + padT + '" width="' + plotW +
          '" height="' + plotH + '" fill="#07101c"/>' +
        bandRects +
        yLabels + xLabels +
        '<rect x="' + padL + '" y="' + padT + '" width="' + plotW +
          '" height="' + plotH + '" fill="none" stroke="#243452"/>' +
        '<path d="' + pathD + '" fill="none" stroke="#ffffff" ' +
          'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>' +
        dotsSvg +
      '</svg>'
    );
  }
})();
"""


# ---------------------------------------------------------------------------
# Global GeoJSON (consumed client-side by MapLibre on /global_tracks.html)
# ---------------------------------------------------------------------------

# Hurricane glyph path used by both the per-basin SVG renderer and (now
# also) the MapLibre HTML markers on the global page.
HURRICANE_PATH = "M 16.37,-28.27 C 13.58,-28.13 11.51,-27.90 9.23,-27.49 C 1.27,-26.06 -5.88,-22.70 -10.92,-18.02 C -14.83,-14.40 -17.41,-10.06 -18.49,-5.32 C -18.95,-3.30 -19.15,-1.42 -19.15,0.91 C -19.15,2.53 -19.09,3.28 -18.89,4.45 C -18.38,7.38 -17.47,9.46 -15.41,12.37 C -13.88,14.54 -13.43,15.31 -13.20,16.13 C -13.11,16.44 -13.09,16.62 -13.09,17.14 C -13.10,17.93 -13.20,18.32 -13.67,19.28 C -15.30,22.59 -18.65,24.93 -23.49,26.14 C -25.26,26.58 -27.29,26.87 -29.18,26.95 L -30.00,26.98 L -29.65,27.06 C -27.33,27.62 -24.41,28.05 -21.57,28.27 C -20.04,28.38 -16.31,28.38 -14.80,28.27 C -12.93,28.13 -11.43,27.95 -9.77,27.67 C -0.59,26.14 7.56,22.03 12.68,16.37 C 16.22,12.45 18.28,8.10 18.93,3.13 C 19.64,-2.25 18.99,-6.47 16.84,-10.16 C 16.48,-10.80 15.79,-11.82 14.99,-12.95 C 13.61,-14.89 13.18,-15.77 13.12,-16.83 C 13.07,-17.61 13.23,-18.26 13.71,-19.23 C 14.97,-21.79 17.38,-23.84 20.67,-25.16 C 23.13,-26.14 26.24,-26.77 29.15,-26.87 L 30.00,-26.90 L 29.67,-26.98 C 29.13,-27.12 27.57,-27.44 26.66,-27.58 C 24.96,-27.87 23.39,-28.05 21.66,-28.18 C 20.72,-28.25 17.16,-28.30 16.37,-28.27 Z"


# build_global_geojson (+ its two pure-stdlib helpers _split_at_antimeridian
# and _clean_mslp) moved into ace_core so the streaming intensity poller can
# emit the SAME FeatureCollection (poller-primary storm-display, Phase 3).
# Imported above; the call site in global mode is unchanged.


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
  body.interactive .active-icon {{ cursor: pointer; }}
  /* Invest current-position label (atcf_id like "91W"). Same typography
     as .active-icon .name but tinted red to match the X glow. */
  .invest-label {{ fill: #ff5050; font-size: 12px; font-weight: 700;
    paint-order: stroke; stroke: #07101c; stroke-width: 3;
    stroke-linejoin: round; pointer-events: none;
    dominant-baseline: middle; }}

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
  /* Invest tag — yellow-ish neutral so it doesn't compete with the
     active-storm dot. Same typography otherwise. */
  .storm-invest {{ font-size: 10px; color: var(--td); font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.6px; margin-left: 6px; }}
  .storm-invest::before {{ content: "◌ "; }}

  /* SSHS color-bar legend (right of map) */
  .legend {{ position: absolute; top: 70px; right: 12px;
    background: rgba(11,26,48,0.85); border: 1px solid var(--border);
    border-radius: 8px; padding: 8px 10px; font-size: 11px;
    color: var(--muted); backdrop-filter: blur(4px); }}
  .legend .item {{ display: flex; align-items: center; gap: 6px;
    margin: 3px 0; }}
  .legend .dot {{ width: 10px; height: 10px; border-radius: 50%; }}
  /* Phase-marker shapes for the legend rows at the bottom. The square
     glyph is a plain block; the triangle uses the classic CSS-border
     trick (zero-size box with three coloured borders). Containers are
     all 10px wide so legend rows line up. */
  .legend .sq {{ width: 10px; height: 10px; background: #ffffff; }}
  .legend .tri {{ width: 10px; height: 10px; position: relative; }}
  .legend .tri::before {{ content: ""; position: absolute; left: 0; top: 1px;
    width: 0; height: 0;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-bottom: 9px solid #ffffff; }}
  /* Dashed-line glyph for the invest legend row. Mirrors the SVG
     stroke-dasharray="4 3" used in the track polylines. */
  .legend .dashline {{ width: 14px; height: 2px; position: relative; }}
  .legend .dashline::before {{ content: ""; position: absolute; inset: 0;
    background-image: linear-gradient(to right, #ffffff 4px,
      transparent 4px, transparent 7px); background-size: 7px 100%;
    background-repeat: repeat-x; opacity: 0.7; }}
  .legend .sep {{ height: 1px; background: var(--border);
    margin: 6px 0 4px; opacity: 0.6; }}

  /* Zoom/pan hint pill — bottom-left of the map. Visible on global
     mode only (the per-basin pages don't ship the zoom/pan JS, so the
     hint would lie). Mobile hides it because wheel/dblclick gestures
     don't translate to touch. */
  .zoom-hint {{ position: absolute; bottom: 8px; left: 12px;
    background: rgba(11,26,48,0.75); border: 1px solid var(--border);
    border-radius: 6px; padding: 4px 10px; font-size: 11px;
    color: var(--muted); letter-spacing: 0.3px; font-weight: 600;
    backdrop-filter: blur(3px); pointer-events: none; user-select: none; }}

  @media (max-width: 820px) {{
    .side {{ flex: 1 1 100%; }}
    .storm-list {{ max-height: 500px; }}
    .legend {{ display: none; }}
    .zoom-hint {{ display: none; }}
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

  /* Hover tooltip for track dots (applies to circle + polygon markers).
     `r: 6.5` is a no-op for polygons but still enlarges circles; the
     stroke-width bump + white stroke highlights both shapes uniformly.
     All cursor:pointer / hover-effect rules are GATED on the .interactive
     body class — global mode adds it, per-basin maps don't (they ship
     no JS and act as static SVG, so a pointer cursor would mislead). */
  body.interactive .track-dot {{ cursor: pointer; }}
  body.interactive .track-dot:hover {{ r: 6.5; stroke: #fff; stroke-width: 1.6;
    filter: brightness(1.15); }}
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

  /* Clickable storm cards. Same body.interactive gate as the dots —
     per-basin static maps have no toggle handler, so the card stays
     visually inert. */
  body.interactive .storm-card.clickable {{ cursor: pointer; transition: background 0.15s; }}
  body.interactive .storm-card.clickable:hover {{ background: rgba(255,255,255,0.04); }}
  body.interactive .storm-card.clickable.active:hover {{ background: rgba(255,184,58,0.14); }}
  .click-hint {{ font-size: 10px; color: var(--muted); margin-top: 4px;
    text-transform: uppercase; letter-spacing: 0.6px; font-weight: 700;
    opacity: 0.6; }}
  .storm-card.clickable.active .click-hint {{ color: var(--c1); opacity: 0.75; }}
  .storm-card.clickable.open .click-hint {{ opacity: 0.25; }}

  /* Inline detail placard (appears when a storm card is clicked).
     No 1px border or overflow:hidden — both were clipping the
     corner spinner's fins as they rotated past the rounded edge. */
  .storm-placard {{ margin-top: 10px; background: #0a1324;
    border-radius: 8px; }}
  /* Right padding leaves room for the spinning corner icon so it
     doesn't crowd the storm name. Banner gets its own rounded top
     so its colored background matches the wrapper. */
  .placard-banner {{ padding: 10px 56px 10px 12px; position: relative;
    border-top-left-radius: 8px; border-top-right-radius: 8px; }}
  .placard-banner .placard-spinner {{ position: absolute; top: 10px; right: 12px;
    width: 38px; height: 38px; opacity: 0.95;
    filter: drop-shadow(0 0 3px rgba(0,0,0,0.35)); }}
  /* overflow:visible is the real fix — SVGs default to overflow:hidden,
     which was clipping the spinning fins at the viewBox corners. */
  .placard-banner .placard-spinner svg {{ width: 100%; height: 100%;
    overflow: visible; }}
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
<body class="{body_class}">
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
      <svg id="chart" class="map" viewBox="0 0 {map_w} {map_h}" preserveAspectRatio="xMidYMid meet">
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
        <div class="sep"></div>
        <div class="item"><span class="sq"></span>Subtropical</div>
        <div class="item"><span class="tri"></span>Non-tropical (pre/post)</div>
        <div class="item"><span class="dashline"></span>Invest (90-99)</div>
      </div>
      {zoom_hint_html}
    </div>
  </div>

  {side_panel_html}
</div>

<div id="dot-tooltip" hidden></div>

{interactive_js}
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Global page: MapLibre GL JS + Protomaps vector tiles
# ---------------------------------------------------------------------------
# The global map is a separate code path from the per-basin SVG renderer.
# It loads MapLibre, fetches global_storms.geojson from R2 (cdn.triple-a-tropics.com) at runtime, and lets
# MapLibre handle pan/zoom/world-wrapping natively. Per-basin pages still
# render via HTML_TEMPLATE / render_html — the entire SVG pipeline above
# is for them.
#
# Protomaps API key is restricted by CORS to triple-a-tropics.com /
# www.triple-a-tropics.com / localhost:8000, so committing it to the
# public repo is safe.

PROTOMAPS_API_KEY = "9d1a52d8fc230b5f"

GLOBAL_MAPLIBRE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Global TC Tracks &middot; __YEAR__</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" rel="stylesheet">
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<style>
  :root {
    --bg: #07101c; --panel: #0f1a2a; --border: #1a2840;
    --fg: #e5edf6; --muted: #8ea2bd;
    --td: #3fa4ff; --ts: #46c56a;
    --c1: #ffe14d; --c2: #ff9a2f; --c3: #ff4d3b;
    --c4: #e33ad4; --c5: #b03bff;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; background: var(--bg); color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    -webkit-font-smoothing: antialiased; }
  .wrap { display: flex; gap: 12px; padding: 10px;
    max-width: 1400px; margin: 0 auto; flex-wrap: wrap; }
  .map-box { flex: 1 1 820px; min-width: 0;
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 10px; overflow: hidden; position: relative; }
  .map-head { padding: 10px 14px 6px; display: flex; justify-content: space-between;
    flex-wrap: wrap; gap: 8px; font-size: 14px; }
  .map-head .title { font-weight: 700; color: #f1f7fd; font-size: 15px; }
  .map-head .sub { color: var(--muted); font-size: 12px; margin-top: 2px; }
  .map-head .stats { color: var(--muted); font-size: 12px; text-align: right; }
  .map-head .stats b { color: #f1f7fd; }
  .map-head .stats .ace { color: var(--c1); font-weight: 700; }

  /* Map container — same 2:1 aspect ratio as the previous SVG viewBox
     (1400×700) so the iframe height matches what the homepage's
     resizeFrame() observed on the SVG version. The fallback height
     covers the case where aspect-ratio isn't honored (older browsers). */
  .map-svg-wrap { position: relative; }
  #globalMap { width: 100%; aspect-ratio: 2 / 1; min-height: 360px;
    background: #0b2a48; }

  /* Legend — overlaid top-right on the map. Lifted verbatim from the
     SVG version; only the .dot/.sq/.tri/.dashline glyphs survived since
     all observation dots now render as MapLibre circles. The shape
     legend rows stay so subtropical / non-tropical / invest classes are
     still indexed visually for the reader. */
  .legend { position: absolute; top: 12px; right: 12px;
    background: rgba(11,26,48,0.85); border: 1px solid var(--border);
    border-radius: 8px; padding: 8px 10px; font-size: 11px;
    color: var(--muted); backdrop-filter: blur(4px); z-index: 5;
    pointer-events: none; }
  .legend .item { display: flex; align-items: center; gap: 6px;
    margin: 3px 0; }
  .legend .dot { width: 10px; height: 10px; border-radius: 50%; }
  .legend .sq { width: 10px; height: 10px; background: #ffffff; }
  .legend .tri { width: 10px; height: 10px; position: relative; }
  .legend .tri::before { content: ""; position: absolute; left: 0; top: 1px;
    width: 0; height: 0;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-bottom: 9px solid #ffffff; }
  .legend .dashline { width: 14px; height: 2px; position: relative; }
  .legend .dashline::before { content: ""; position: absolute; inset: 0;
    background-image: linear-gradient(to right, #ffffff 4px,
      transparent 4px, transparent 7px); background-size: 7px 100%;
    background-repeat: repeat-x; opacity: 0.7; }
  .legend .sep { height: 1px; background: var(--border);
    margin: 6px 0 4px; opacity: 0.6; }

  /* Zoom hint pill — bottom-left. */
  .zoom-hint { position: absolute; bottom: 26px; left: 12px;
    background: rgba(11,26,48,0.75); border: 1px solid var(--border);
    border-radius: 6px; padding: 4px 10px; font-size: 11px;
    color: var(--muted); letter-spacing: 0.3px; font-weight: 600;
    backdrop-filter: blur(3px); pointer-events: none; user-select: none;
    z-index: 5; }

  /* Brand watermark — bottom-right. Replaces the SVG <text class="wm">
     element from the previous renderer. Same color/size/shadow recipe. */
  .brand-wm { position: absolute; bottom: 14px; right: 18px;
    color: rgba(255,255,255,0.55); font-weight: 700; font-size: 22px;
    letter-spacing: 0.5px; pointer-events: none;
    text-shadow: 0 1px 0 rgba(0,0,0,0.55), 0 0 4px rgba(0,0,0,0.45);
    z-index: 5; }

  /* MapLibre's NavigationControl matches the panel palette. */
  .maplibregl-ctrl-group { background: rgba(11,26,48,0.9) !important;
    border: 1px solid var(--border) !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.4) !important; }
  .maplibregl-ctrl-group button { background-color: transparent !important; }
  .maplibregl-ctrl-group button:hover {
    background-color: rgba(255,255,255,0.08) !important; }
  .maplibregl-ctrl-icon { filter: invert(0.85); }

  /* Popup styling — tooltip-flavored override. The default popup arrow
     gets dropped (display:none on the tip) and the inner panel adopts
     the TAT palette with same border radius, blur, and font scale as
     the legend. */
  .maplibregl-popup-tip { display: none !important; }
  .maplibregl-popup-content { background: rgba(10,18,34,0.96) !important;
    color: var(--fg) !important;
    border: 1px solid #2a3e5c; border-radius: 8px;
    padding: 8px 12px !important; font-size: 12px; line-height: 1.45;
    min-width: 170px;
    box-shadow: 0 6px 20px rgba(0,0,0,0.55); backdrop-filter: blur(4px); }
  .tt-name { font-weight: 800; color: #f1f7fd;
    font-size: 13px; letter-spacing: 0.3px; }
  .tt-time { color: var(--muted); font-size: 11px; margin-bottom: 4px; }
  .tt-row { display: flex; justify-content: space-between;
    gap: 10px; margin-top: 2px; }
  .tt-lbl { color: var(--muted); }
  .tt-val { color: var(--fg); font-variant-numeric: tabular-nums; }
  .tt-cat { display: inline-block; padding: 1px 8px;
    border-radius: 999px; font-size: 10px; font-weight: 700;
    color: #07101c; margin-top: 2px; }
  .tt-foot { color: var(--muted); font-size: 11px; margin-top: 5px;
    padding-top: 4px; border-top: 1px solid rgba(255,255,255,0.08); }

  /* Active-storm markers (HTML, not GL) so the existing spin animation
     and red marker appearance survive without a WebGL rebuild.
     Four flavours match the per-basin SVG renderer:
       * .invest-x-marker   — recent inactive invest (small red glowing X
                              with red side-label). Per-basin reference:
                              render_tracks_svg invest_current_positions.
       * .active-l          — active invest (big red L + white
                              designation below). Per-basin reference:
                              render_active_icons is_invest branch.
       * .active-td         — active designated TD that's not an invest
                              (hollow blue circle + white designation
                              below). Per-basin reference:
                              render_active_icons peak<34 branch.
       * .active-hurricane  — active TS+ storm (spinning hurricane glyph
                              with category letter inside + name beside).
                              Per-basin reference: render_active_icons.
     Sizing here is in *unzoomed* CSS pixels — MapLibre keeps marker
     elements at constant pixel size while the map zooms, exactly like
     the per-basin SVG when its viewBox stays put. */
  .active-marker { position: absolute; transform: translate(-50%, -50%);
    pointer-events: none; }
  .active-marker svg { display: block; overflow: visible;
    width: 100%; height: 100%; }

  /* Hurricane spinner */
  .active-marker.active-hurricane { width: 50px; height: 50px; }
  .active-marker.active-hurricane svg {
    filter: drop-shadow(0 0 6px currentColor); }
  @keyframes tat-spin { from { transform: rotate(360deg); }
                        to   { transform: rotate(0deg); } }
  .active-marker .spinning {
    animation: tat-spin 2.6s linear infinite;
    transform-origin: 50% 50%; transform-box: fill-box; }
  .active-marker .hurricane-label { font-size: 14px; font-weight: 900;
    fill: #ffffff; paint-order: stroke;
    stroke: rgba(0,0,0,0.55); stroke-width: 1.8;
    stroke-linejoin: round; }
  .active-marker .hurricane-name { fill: #f1f7fd; font-size: 12px;
    font-weight: 700; paint-order: stroke;
    stroke: #07101c; stroke-width: 3; stroke-linejoin: round; }

  /* Active invest "L" — text styling lifted verbatim from
     render_active_icons (font-size 34, weight 900, fill #ef4444, dark
     stroke 2.5) and label (font-size 13, weight 800, fill #ffffff,
     stroke 2.5). The container is sized to match that SVG group's
     bounding box so the marker reads at the same physical size as the
     per-basin version. */
  .active-marker.active-l { width: 60px; height: 50px;
    filter: drop-shadow(0 0 4px rgba(0,0,0,0.7)); }
  .active-marker .l-glyph { font-size: 34px; font-weight: 900;
    fill: #ef4444; paint-order: stroke;
    stroke: rgba(0,0,0,0.55); stroke-width: 2.5; stroke-linejoin: round; }
  .active-marker .l-label { font-size: 13px; font-weight: 800;
    fill: #ffffff; paint-order: stroke;
    stroke: rgba(0,0,0,0.7); stroke-width: 2.5; stroke-linejoin: round; }

  /* Active designated-TD circle — a chunky bright-cyan ring (TAT
     accent-2 #5dd3ff) wrapped in a white outer halo, hollow centre, with
     the white name/designation label below. Mirrors render_active_icons'
     peak<34 branch (r=14; white halo stroke 6.5 drawn first, cyan ring
     stroke 3.5 centred on top, leaving white peeking both edges; label at
     y=26). The halo gives the TD the same visual weight as the TS+
     spinning icons; the hollow centre keeps it distinct from filled
     TS+ observation dots. */
  .active-marker.active-td { width: 64px; height: 54px;
    filter: drop-shadow(0 0 5px rgba(0,0,0,0.65)); }
  .active-marker .td-halo { stroke: #ffffff; stroke-width: 6.5;
    fill: none; }
  .active-marker .td-circle { stroke: #5dd3ff; stroke-width: 3.5;
    fill: none; }
  .active-marker .td-label { font-size: 13px; font-weight: 800;
    fill: #ffffff; paint-order: stroke;
    stroke: rgba(0,0,0,0.7); stroke-width: 2.5; stroke-linejoin: round; }

  /* Inactive recent invest "X" — text/path styling lifted verbatim from
     render_tracks_svg invest_current_positions (path stroke #ff2a2a /
     width 2.4) and the .invest-label CSS rule (#ff5050 / 12px / weight
     700 / dark stroke 3). */
  .active-marker.invest-x-marker { width: 92px; height: 32px; }
  .active-marker .invest-label { fill: #ff5050; font-size: 12px;
    font-weight: 700; paint-order: stroke; stroke: #07101c;
    stroke-width: 3; stroke-linejoin: round;
    dominant-baseline: middle; }

  @media (max-width: 820px) {
    .legend { display: none; }
    .zoom-hint { display: none; }
    .brand-wm { font-size: 18px; bottom: 8px; right: 12px; }
  }
</style>
</head>
<body>
<div class="wrap">
  <div class="map-box">
    <div class="map-head">
      <div>
        <div class="title">__YEAR__ Global TC Tracks</div>
        <!-- Baked build-time stamp = fallback; when the geojson carries the
             poller's live freshness stamps (Phase 3), the fetch handler below
             overwrites this at runtime with the map's TRUE data freshness. -->
        <div class="sub" id="as-of">As of __UPDATED__</div>
      </div>
      <div class="stats">
        <b>__NAMED__</b> __NAMED_LABEL__ &middot; <b>__CAT1PLUS__</b> __CAT1PLUS_LABEL__ &middot;
        <b>__CAT5__</b> __CAT5_LABEL__ &middot; <span class="ace">__TOTAL_ACE__ ACE</span>
      </div>
    </div>
    <div class="map-svg-wrap">
      <div id="globalMap"></div>
      <div class="legend">
        <div class="item"><span class="dot" style="background:var(--td)"></span>TD (&lt;34 kt)</div>
        <div class="item"><span class="dot" style="background:var(--ts)"></span>TS (34&ndash;63)</div>
        <div class="item"><span class="dot" style="background:var(--c1)"></span>Cat 1 (64&ndash;82)</div>
        <div class="item"><span class="dot" style="background:var(--c2)"></span>Cat 2 (83&ndash;95)</div>
        <div class="item"><span class="dot" style="background:var(--c3)"></span>Cat 3 (96&ndash;112)</div>
        <div class="item"><span class="dot" style="background:var(--c4)"></span>Cat 4 (113&ndash;136)</div>
        <div class="item"><span class="dot" style="background:var(--c5)"></span>Cat 5 (&ge;137)</div>
        <div class="sep"></div>
        <div class="item"><span class="sq"></span>Subtropical</div>
        <div class="item"><span class="tri"></span>Non-tropical (pre/post)</div>
        <div class="item"><span class="dashline"></span>Invest (90-99)</div>
      </div>
      <div class="zoom-hint">Drag to pan &middot; Scroll to zoom &middot; Double-click to reset</div>
      <div class="brand-wm">@WeathermanAAA_</div>
    </div>
  </div>
</div>

<script>
(function () {
  var SSHS_COLORS = {
    "TD": "#3fa4ff", "TS": "#46c56a", "C1": "#ffe14d",
    "C2": "#ff9a2f", "C3": "#ff4d3b", "C4": "#e33ad4", "C5": "#b03bff"
  };
  var CAT_LABELS = {
    "TD": "Depression", "TS": "Tropical Storm",
    "C1": "Category 1", "C2": "Category 2", "C3": "Category 3",
    "C4": "Category 4", "C5": "Category 5"
  };
  function ktToMph5(k) { return Math.round(k * 1.15077945 / 5) * 5; }
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
  // "2026-05-27 12:00 UTC" — explicit calendar form used for the
  // "Last fix" line in the active-storm popup.
  function fmtUTC(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    var p = function(n) { return String(n).padStart(2, "0"); };
    return d.getUTCFullYear() + "-" + p(d.getUTCMonth() + 1) + "-" +
           p(d.getUTCDate()) + " " + p(d.getUTCHours()) + ":" +
           p(d.getUTCMinutes()) + " UTC";
  }
  function sshsLabel(cls) {
    if (cls === "TD") return "D";
    if (cls === "TS") return "S";
    return (cls || "").replace("C", "") || "D";
  }

  // ---- Style spec: TAT palette + Protomaps vector tiles ----
  var STYLE = {
    "version": 8,
    "name": "TAT Global TC Tracks",
    "sources": {
      "protomaps": {
        "type": "vector",
        "tiles": ["https://api.protomaps.com/tiles/v3/{z}/{x}/{y}.mvt?key=__PROTOMAPS_KEY__"],
        "minzoom": 0,
        "maxzoom": 14,
        "attribution": "&copy; <a href=\"https://protomaps.com\">Protomaps</a> &copy; <a href=\"https://openstreetmap.org/copyright\">OSM</a>"
      }
    },
    "layers": [
      // Background paints LAND color; water layer paints oceans on top.
      // Reason: at low zoom Protomaps' earth tiles can have hairline gaps
      // along polygon edges where the underlying background bleeds through
      // and anti-aliases the visible land toward the background hue. With
      // background=land, gaps in earth coverage default to the canonical
      // #aeb2b5 instead of cooling toward ocean blue. The water layer
      // covers oceans/seas reliably so this inversion doesn't lose
      // anything visually — it just plugs the AA seams.
      { "id": "background", "type": "background",
        "paint": { "background-color": "#aeb2b5" } },
      { "id": "water", "type": "fill",
        "source": "protomaps", "source-layer": "water",
        "paint": { "fill-color": "#2463a0" } },
      { "id": "earth", "type": "fill",
        "source": "protomaps", "source-layer": "earth",
        "paint": { "fill-color": "#aeb2b5" } },
      { "id": "coastline", "type": "line",
        "source": "protomaps", "source-layer": "earth",
        "paint": {
          "line-color": "#ffffff", "line-opacity": 0.85,
          "line-width": ["interpolate", ["linear"], ["zoom"],
            0, 0.4, 4, 0.8, 8, 1.2, 12, 1.8]
        } },
      { "id": "country-border", "type": "line",
        "source": "protomaps", "source-layer": "boundaries",
        "filter": ["==", ["get", "kind"], "country"],
        "paint": {
          "line-color": "#ffffff", "line-opacity": 0.9,
          "line-width": ["interpolate", ["linear"], ["zoom"],
            0, 0.6, 4, 1.0, 8, 1.4, 12, 2.0]
        } },
      { "id": "state-border", "type": "line",
        "source": "protomaps", "source-layer": "boundaries",
        "filter": ["==", ["get", "kind"], "region"],
        "minzoom": 4,
        "paint": {
          "line-color": "#ffffff", "line-opacity": 0.5,
          "line-width": ["interpolate", ["linear"], ["zoom"],
            4, 0.3, 8, 0.6, 12, 1.0]
        } }
    ]
  };

  var map = new maplibregl.Map({
    container: "globalMap",
    style: STYLE,
    center: [180, 10],
    zoom: 1.5,
    minZoom: 1,
    maxZoom: 14,
    renderWorldCopies: true,
    attributionControl: false
  });
  map.addControl(new maplibregl.NavigationControl({
    visualizePitch: false, showCompass: false
  }), "top-left");
  map.dragRotate.disable();
  map.touchZoomRotate.disableRotation();
  // Double-click resets to the home view (matches the previous SVG
  // dblclick-reset behavior). MapLibre's default dblclick-zoom is fine
  // to leave on as a fallback but the explicit reset matches operator
  // expectations from the SVG era.
  map.doubleClickZoom.disable();
  map.on("dblclick", function () {
    map.easeTo({ center: [180, 10], zoom: 1.5, duration: 400 });
  });

  // ---- Storm features + interactive layers ----
  var activeMarkers = [];
  function addStormLayers(geojson) {
    map.addSource("storms", { type: "geojson", data: geojson });

    // Two line layers because MapLibre's line-dasharray paint property
    // doesn't support data-driven feature expressions — only zoom-based.
    // Splitting by is_invest in the layer filter is the standard fix.
    map.addLayer({
      id: "tracks-line-solid",
      type: "line",
      source: "storms",
      filter: ["all",
        ["==", ["geometry-type"], "LineString"],
        ["!=", ["get", "is_invest"], true]
      ],
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": "#ffffff",
        "line-opacity": 0.4,
        "line-width": 1.0
      }
    });
    map.addLayer({
      id: "tracks-line-invest",
      type: "line",
      source: "storms",
      filter: ["all",
        ["==", ["geometry-type"], "LineString"],
        ["==", ["get", "is_invest"], true]
      ],
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": "#ffffff",
        "line-opacity": 0.4,
        "line-width": 1.0,
        "line-dasharray": [4, 3]
      }
    });

    // SSHWS color step expression — shared across the three phase-shape
    // layers (tropical circles, subtropical squares, non-tropical
    // triangles) so a 50 kt subtropical storm and a 50 kt tropical storm
    // read the same TS green. Step (not interpolate) because each
    // category is a flat color band; an "interpolate" would render a
    // 25 kt TD as 73% along the TD→TS gradient (visibly green),
    // mismatching the legend swatch.
    var COLOR_STEP = [
      "step", ["coalesce", ["get", "intensity_kt"], 0],
      "#3fa4ff",       // <34 kt: TD (default below first stop)
      34,  "#46c56a",  // 34-63: TS
      64,  "#ffe14d",  // 64-82: C1
      83,  "#ff9a2f",  // 83-95: C2
      96,  "#ff4d3b",  // 96-112: C3
      113, "#e33ad4",  // 113-136: C4
      137, "#b03bff"   // ≥137: C5
    ];
    // Shared zoom-radius/icon-size ramp so circles and symbols read at
    // the same physical pixel footprint (the per-basin SVG uses r=3 for
    // TD, r=4 for TS, r=5 for major; this approximates that progression
    // across MapLibre's zoom range).
    var ZOOM_RADIUS = ["interpolate", ["linear"], ["zoom"],
      0, 2.0, 4, 3.0, 8, 4.0, 12, 5.0];
    // For 24px-base SDF icons, icon-size = displayDiameter / 24.
    // Targeting ~4/6/8/10 px diameters at zoom 0/4/8/12.
    var ZOOM_ICON_SIZE = ["interpolate", ["linear"], ["zoom"],
      0, 0.20, 4, 0.28, 8, 0.36, 12, 0.45];

    // Tropical observations — filled circle, intensity-colored.
    // The white halo is dropped for TDs (<34 kt) because at 2-3 px
    // radii a 0.5 px white stroke covers a meaningful fraction of the
    // visible dot, washing the cream toward white. TS+ keep the halo
    // because its larger radius leaves the fill dominant.
    map.addLayer({
      id: "observations-tropical",
      type: "circle",
      source: "storms",
      filter: ["all",
        ["==", ["geometry-type"], "Point"],
        ["==", ["get", "kind"], "observation"],
        ["!=", ["get", "is_subtropical"], true],
        ["!=", ["get", "is_nontropical"], true]
      ],
      paint: {
        "circle-color": COLOR_STEP,
        "circle-radius": ZOOM_RADIUS,
        "circle-stroke-color": [
          "step", ["coalesce", ["get", "intensity_kt"], 0],
          "rgba(63,164,255,0)",  // TD: transparent (no visible halo)
          34, "#ffffff"          // TS+: white halo
        ],
        "circle-stroke-width": [
          "step", ["coalesce", ["get", "intensity_kt"], 0],
          0,    // TD: no stroke
          34, 0.5
        ],
        "circle-stroke-opacity": 0.7
      }
    });

    // Subtropical observations — filled square, intensity-colored,
    // white halo (matches the per-basin SVG's <rect fill=color
    // stroke=#ffffff stroke-width=0.9>).
    map.addLayer({
      id: "observations-subtropical",
      type: "symbol",
      source: "storms",
      filter: ["all",
        ["==", ["geometry-type"], "Point"],
        ["==", ["get", "kind"], "observation"],
        ["==", ["get", "is_subtropical"], true]
      ],
      layout: {
        "icon-image": "phase-square",
        "icon-size": ZOOM_ICON_SIZE,
        "icon-allow-overlap": true,
        "icon-ignore-placement": true
      },
      paint: {
        "icon-color": COLOR_STEP,
        "icon-halo-color": "#ffffff",
        "icon-halo-width": 1.0
      }
    });

    // Non-tropical observations (extratropical / pre-TC disturbance /
    // remnant low) — filled up-triangle, intensity-colored, white halo.
    map.addLayer({
      id: "observations-nontropical",
      type: "symbol",
      source: "storms",
      filter: ["all",
        ["==", ["geometry-type"], "Point"],
        ["==", ["get", "kind"], "observation"],
        ["==", ["get", "is_nontropical"], true]
      ],
      layout: {
        "icon-image": "phase-triangle",
        "icon-size": ZOOM_ICON_SIZE,
        "icon-allow-overlap": true,
        "icon-ignore-placement": true
      },
      paint: {
        "icon-color": COLOR_STEP,
        "icon-halo-color": "#ffffff",
        "icon-halo-width": 1.0
      }
    });

    // ---- Hover popup on observations (all three phase-shape layers) ----
    var OBS_LAYERS = [
      "observations-tropical",
      "observations-subtropical",
      "observations-nontropical"
    ];
    var popup = null;
    function onObsEnter(e) {
      map.getCanvas().style.cursor = "pointer";
      var f = e.features[0];
      var props = f.properties || {};
      var coords = f.geometry.coordinates.slice();
      // Snap horizontally so the popup tracks the wrapped copy nearest
      // the cursor (renderWorldCopies puts the same point at every
      // multiple of 360°).
      while (e.lngLat.lng - coords[0] > 180)  coords[0] += 360;
      while (e.lngLat.lng - coords[0] < -180) coords[0] -= 360;

      var kt = props.intensity_kt;
      var pres = props.mslp_mb;
      var cls = props.sshws_cat || "TD";
      var catLabel = CAT_LABELS[cls] || cls;
      var color = SSHS_COLORS[cls] || "#888";
      var windTxt = (kt != null && kt !== "" && !isNaN(parseFloat(kt)))
        ? (Math.round(parseFloat(kt)) + " kt &middot; " + ktToMph5(parseFloat(kt)) + " mph")
        : "-";
      var presTxt = (pres != null && pres !== "" && !isNaN(parseFloat(pres)))
        ? (Math.round(parseFloat(pres)) + " mb")
        : "-";
      var html =
        '<div class="tt-name">' + escapeHtml(props.storm_name || "Storm") + '</div>' +
        '<div class="tt-time">' + fmtTime(props.time_iso) + '</div>' +
        '<div class="tt-row"><span class="tt-cat" style="background:' +
          color + '">' + catLabel + '</span></div>' +
        '<div class="tt-row"><span class="tt-lbl">Wind</span>' +
          '<span class="tt-val">' + windTxt + '</span></div>' +
        '<div class="tt-row"><span class="tt-lbl">Pressure</span>' +
          '<span class="tt-val">' + presTxt + '</span></div>';
      if (popup) popup.remove();
      popup = new maplibregl.Popup({
        closeButton: false, closeOnClick: false,
        offset: 8, maxWidth: "240px"
      }).setLngLat(coords).setHTML(html).addTo(map);
    }
    function onObsMove(e) {
      if (!popup) return;
      var f = e.features[0];
      var coords = f.geometry.coordinates.slice();
      while (e.lngLat.lng - coords[0] > 180)  coords[0] += 360;
      while (e.lngLat.lng - coords[0] < -180) coords[0] -= 360;
      popup.setLngLat(coords);
    }
    function onObsLeave() {
      map.getCanvas().style.cursor = "";
      if (popup) { popup.remove(); popup = null; }
    }
    OBS_LAYERS.forEach(function (id) {
      map.on("mouseenter", id, onObsEnter);
      map.on("mousemove",  id, onObsMove);
      map.on("mouseleave", id, onObsLeave);
    });
  }

  // Popup body for an active-storm marker. Mirrors the observation hover
  // card (same tt-* classes) but reads the active_marker feature props:
  // name/designation, current SSHWS category, current intensity.
  function activeMarkerPopupHtml(props) {
    var cls = props.current_category || "TD";
    var catLabel = CAT_LABELS[cls] || cls;
    var color = SSHS_COLORS[cls] || "#888";
    var kt = props.current_intensity_kt;
    var windTxt = (kt != null && kt !== "" && !isNaN(parseFloat(kt)))
      ? (Math.round(parseFloat(kt)) + " kt &middot; " + ktToMph5(parseFloat(kt)) + " mph")
      : "-";
    // Pressure row, directly under Wind. b-decks often lack MSLP on weak/early
    // fixes (field is null then) -> OMIT the row entirely; never show "0 mb".
    var pres = props.current_mslp_mb;
    var presRow = (pres != null && pres !== "" && !isNaN(parseFloat(pres))
                   && parseFloat(pres) > 0)
      ? ('<div class="tt-row"><span class="tt-lbl">Pressure</span>' +
         '<span class="tt-val">' + Math.round(parseFloat(pres)) + ' mb</span></div>')
      : '';
    var title = props.name || props.designation || "Active system";
    var lastFixTxt = props.last_fix ? fmtUTC(props.last_fix) : "";
    return '<div class="tt-name">' + escapeHtml(title) + '</div>' +
      '<div class="tt-row"><span class="tt-cat" style="background:' +
        color + '">' + escapeHtml(catLabel) + '</span></div>' +
      '<div class="tt-row"><span class="tt-lbl">Wind</span>' +
        '<span class="tt-val">' + windTxt + '</span></div>' +
      presRow +
      (lastFixTxt ? '<div class="tt-foot">Last fix: ' + lastFixTxt + '</div>' : '');
  }

  // Per-marker uniqueness for SVG <filter> ids — multiple markers on one
  // page would otherwise collide on a shared "invest-red-glow" id.
  var investGlowSeq = 0;

  function addActiveMarkers(geojson) {
    // Clear any markers from a prior load.
    activeMarkers.forEach(function (m) { m.remove(); });
    activeMarkers = [];
    (geojson.features || []).forEach(function (f) {
      var props = f.properties || {};
      if (props.kind !== "active_marker") return;
      var lngLat = f.geometry.coordinates;
      var el = document.createElement("div");
      el.className = "active-marker";
      var designation = String(props.designation || props.name || "").toUpperCase();

      if (props.marker_type === "invest_x") {
        // Recent invest, NOT operationally active. Per-basin's
        // render_tracks_svg invest_current_positions emits a small red
        // glowing X (path "M -7 -7 L 7 7 M -7 7 L 7 -7") at stroke
        // #ff2a2a / 2.4 width, with a red invest-label to the right
        // (offset +11 +4 from the X centre). Replicate verbatim.
        el.classList.add("invest-x-marker");
        var fid = "invest-red-glow-" + (++investGlowSeq);
        // Anchor of the marker is the X center; the SVG's viewBox is
        // centred on (0,0) and sized large enough to contain both the
        // glowing X and the side label without clipping.
        el.innerHTML =
          '<svg viewBox="-22 -16 92 32" xmlns="http://www.w3.org/2000/svg">' +
            '<defs><filter id="' + fid + '" ' +
              'x="-200%" y="-200%" width="500%" height="500%">' +
              '<feGaussianBlur in="SourceAlpha" stdDeviation="3.2" ' +
                'result="blur"/>' +
              '<feFlood flood-color="#ff0000" flood-opacity="0.95" ' +
                'result="red"/>' +
              '<feComposite in="red" in2="blur" operator="in" ' +
                'result="redblur"/>' +
              '<feMerge><feMergeNode in="redblur"/>' +
                '<feMergeNode in="redblur"/>' +
                '<feMergeNode in="SourceGraphic"/></feMerge>' +
            '</filter></defs>' +
            '<g filter="url(#' + fid + ')">' +
              '<path d="M -7 -7 L 7 7 M -7 7 L 7 -7" ' +
                'stroke="#ff2a2a" stroke-width="2.4" ' +
                'stroke-linecap="round" fill="none"/>' +
            '</g>' +
            '<text class="invest-label" x="11" y="4" ' +
              'text-anchor="start">' + escapeHtml(designation) + '</text>' +
          '</svg>';
      } else if (props.marker_type === "L") {
        // Active invest (90-99 designation). Per-basin's
        // render_active_icons emits a bold red "L" (font-size 34, weight
        // 900, fill #ef4444, dark stroke 2.5) with a white designation
        // below (size 13, weight 800, dark stroke 2.5).
        el.classList.add("active-l");
        el.innerHTML =
          '<svg viewBox="-30 -22 60 50" xmlns="http://www.w3.org/2000/svg">' +
            '<text class="l-glyph" x="0" y="0" ' +
              'text-anchor="middle" dominant-baseline="central">L</text>' +
            '<text class="l-label" x="0" y="22" ' +
              'text-anchor="middle" dominant-baseline="hanging">' +
              escapeHtml(designation) + '</text>' +
          '</svg>';
      } else if (props.marker_type === "td_circle") {
        // Active designated TD that's not an invest (e.g. Hagupit at
        // TD strength). Per-basin's render_active_icons emits a hollow
        // blue circle (r=12, stroke 2.5, no fill) + white name/
        // designation label below — a third tier between the spinning
        // TS+ icon and the invest "L" so a numbered TD reads as an
        // operational TC rather than an invest.
        el.classList.add("active-td");
        el.innerHTML =
          '<svg viewBox="-30 -22 60 50" xmlns="http://www.w3.org/2000/svg">' +
            '<circle class="td-halo" cx="0" cy="0" r="14"/>' +
            '<circle class="td-circle" cx="0" cy="0" r="14"/>' +
            '<text class="td-label" x="0" y="26" ' +
              'text-anchor="middle" dominant-baseline="hanging">' +
              escapeHtml(designation) + '</text>' +
          '</svg>';
      } else {
        // Active TS+ — spinning hurricane glyph.
        el.classList.add("active-hurricane");
        var cls = props.current_category || "TD";
        var color = SSHS_COLORS[cls] || "#888";
        var label = sshsLabel(cls);
        el.style.color = color;  // drop-shadow inherits via currentColor
        el.innerHTML =
          '<svg viewBox="-34 -34 68 68" xmlns="http://www.w3.org/2000/svg">' +
            '<g class="spinning">' +
              '<path d="__HURRICANE_PATH__" fill="' + color + '" />' +
            '</g>' +
            '<text class="hurricane-label" x="0" y="0" ' +
              'text-anchor="middle" dominant-baseline="central">' +
              label + '</text>' +
            '<text class="hurricane-name" x="36" y="6" ' +
              'text-anchor="start">' + escapeHtml(props.name || "") + '</text>' +
          '</svg>';
      }
      var marker = new maplibregl.Marker({ element: el, anchor: "center" })
        .setLngLat(lngLat).addTo(map);
      activeMarkers.push(marker);

      // Restore hover/click interactivity lost in the SVG→MapLibre
      // migration. `.active-marker` CSS sets pointer-events:none, so
      // re-enable it on this element and wire a popup: hover to peek,
      // click to pin (with a close button). `props`/`lngLat` are fresh
      // per forEach iteration, so each marker closes over its own.
      el.style.pointerEvents = "auto";
      el.style.cursor = "pointer";
      var mPopup = null, pinned = false;
      var openActivePopup = function (withClose) {
        if (mPopup) mPopup.remove();
        mPopup = new maplibregl.Popup({
          closeButton: withClose, closeOnClick: false,
          offset: 16, maxWidth: "240px"
        }).setLngLat(lngLat)
          .setHTML(activeMarkerPopupHtml(props))
          .addTo(map);
        if (withClose) {
          mPopup.on("close", function () { pinned = false; mPopup = null; });
        }
      };
      el.addEventListener("mouseenter", function () {
        map.getCanvas().style.cursor = "pointer";
        if (!pinned) openActivePopup(false);
      });
      el.addEventListener("mouseleave", function () {
        map.getCanvas().style.cursor = "";
        if (!pinned && mPopup) { mPopup.remove(); mPopup = null; }
      });
      el.addEventListener("click", function (ev) {
        ev.stopPropagation();
        pinned = true;
        openActivePopup(true);
      });
    });
  }

  // Register SDF icons used by the subtropical/non-tropical observation
  // layers. We render filled black shapes onto a 24×24 transparent
  // canvas; with sdf:true, MapLibre treats the alpha channel as a mask
  // and tints with `icon-color` per feature. Pixels outside the shape
  // are alpha=0 so the surrounding canvas never paints. The 24 px base
  // pairs with the icon-size ramp above (display diameter = size × 24).
  function registerPhaseIcons() {
    var size = 24;
    function makeShape(draw) {
      var canvas = document.createElement("canvas");
      canvas.width = size; canvas.height = size;
      var ctx = canvas.getContext("2d");
      ctx.fillStyle = "#000000";
      draw(ctx, size);
      return ctx.getImageData(0, 0, size, size);
    }
    if (!map.hasImage("phase-square")) {
      map.addImage("phase-square", makeShape(function (ctx, s) {
        var pad = 4;
        ctx.fillRect(pad, pad, s - 2 * pad, s - 2 * pad);
      }), { sdf: true });
    }
    if (!map.hasImage("phase-triangle")) {
      map.addImage("phase-triangle", makeShape(function (ctx, s) {
        var pad = 3;
        ctx.beginPath();
        ctx.moveTo(s / 2, pad);
        ctx.lineTo(s - pad, s - pad);
        ctx.lineTo(pad, s - pad);
        ctx.closePath();
        ctx.fill();
      }), { sdf: true });
    }
  }

  map.on("load", function () {
    registerPhaseIcons();
    fetch("https://cdn.triple-a-tropics.com/global_storms.geojson", { cache: "no-cache" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        // Phase 3: the poller-written geojson carries live freshness stamps
        // (updated / generated_utc / latest_fix_valid_utc as RFC-7946 foreign
        // members) - surface the map's TRUE freshness instead of the page's
        // baked build time. The cron's bare FeatureCollection has no stamps,
        // so on rollback the baked "As of" text simply stands.
        if (data && data.updated) {
          var asOf = document.getElementById("as-of");
          if (asOf) asOf.textContent = "As of " + data.updated;
        }
        addStormLayers(data);
        addActiveMarkers(data);
      })
      .catch(function (e) {
        console.error("Failed to load global_storms.geojson:", e);
      });
  });
})();
</script>
</body>
</html>
"""


def render_global_maplibre_html(payload: dict) -> str:
    """Render the MapLibre-based global tracks page. The storms are
    served from global_storms.geojson on R2 (cdn.triple-a-tropics.com,
    fetched at runtime), so this template is essentially static across
    refreshes — only the header pill (year, updated, season-stats)
    changes."""
    header = payload["header"]
    vocab = payload["vocab"]
    return (
        GLOBAL_MAPLIBRE_HTML
        .replace("__YEAR__", str(payload["year"]))
        .replace("__UPDATED__", payload["updated"])
        .replace("__NAMED__", str(header["named"]))
        .replace("__NAMED_LABEL__", vocab["named"])
        .replace("__CAT1PLUS__", str(header["cat1plus"]))
        .replace("__CAT1PLUS_LABEL__", vocab["cat1plus"])
        .replace("__CAT5__", str(header["cat5"]))
        .replace("__CAT5_LABEL__", vocab["cat5"])
        .replace("__TOTAL_ACE__", f"{header['total_ace']:.2f}")
        .replace("__PROTOMAPS_KEY__", PROTOMAPS_API_KEY)
        .replace("__HURRICANE_PATH__", HURRICANE_PATH)
    )


def _cat_style(cls: str) -> tuple[str, str]:
    """Return (background color, short label) for a storm badge."""
    return SSHS_COLORS.get(cls, SSHS_COLORS["TD"]), cls.replace("C", "Cat ")


def _storm_count_label(storms: list[dict]) -> str:
    """Sidebar title fragment. When invests are present, surface them
    separately so the count "8 Storms" doesn't silently include 91W."""
    invests = sum(1 for s in storms if s.get("is_invest"))
    tcs = len(storms) - invests
    if invests == 0:
        return f"{tcs} Storms"
    invest_word = "Invest" if invests == 1 else "Invests"
    return f"{tcs} Storms · {invests} {invest_word}"


def _fmt_date_range(start: str | None, end: str | None) -> str:
    def fmt(iso):
        try:
            d = dt.datetime.fromisoformat(iso)
            return d.strftime("%b %-d")
        except Exception:
            return "?"
    if not start:
        return "-"
    s = fmt(start)
    e = fmt(end) if end else s
    return f"{s} – {e}" if s != e else s


def render_storm_card(storm: dict) -> str:
    cat = storm.get("max_category", "TD")
    color, label = _cat_style(cat)
    is_active = storm.get("is_active")
    is_invest = storm.get("is_invest")
    # Active TCs get the "Active" tag (also gets the spinning map icon);
    # invests that aren't active TCs get an "INVEST" tag instead. The
    # two are mutually exclusive — an invest that briefly hit 34 kt would
    # show "Active", which is correct since the b-deck would still call
    # it 91W until JTWC/NHC numbers it.
    if is_active:
        active_tag = '<span class="storm-active">Active</span>'
    elif is_invest:
        active_tag = '<span class="storm-invest">Invest</span>'
    else:
        active_tag = ''
    # Every card is clickable — active cards open the pinned live placard
    # at the top; inactive cards expand an inline peak-intensity placard.
    classes = "storm-card clickable"
    if is_active:
        classes += " active"
    if is_invest:
        classes += " invest"
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
    <div class="row"><span class="lbl">Peak wind</span><span class="val">{peak_wind if peak_wind is not None else '-'} kt</span></div>
    <div class="row"><span class="lbl">Peak pressure</span><span class="val">{peak_pres if peak_pres is not None else '-'} mb</span></div>
    <div class="row"><span class="lbl">ACE</span><span class="val">{ace:.2f}</span></div>
  </div>
  {click_hint}
  {placard_slot}
</div>
"""


def render_html(payload: dict, extent, countries_geojson, coastline_geojson) -> str:
    """Render a per-basin static SVG tracks page. Global mode is no longer
    routed here — see render_global_maplibre_html for the MapLibre path."""
    map_w = MAP_W
    map_h = MAP_H
    basemap_svg = render_basemap_svg(extent, countries_geojson, coastline_geojson,
                                     map_w, map_h)
    tracks_svg = render_tracks_svg(payload["storms"], extent, map_w, map_h)
    active_svg = render_active_icons(payload["storms"], extent, map_w, map_h)

    storm_cards = "\n".join(render_storm_card(s) for s in payload["storms"]) or (
        '<div class="storm-card"><div class="storm-meta">'
        'No storms yet this year.</div></div>'
    )
    side_panel_html = (
        f'<div class="side">'
        f'<div class="panel-title">{payload["year"]} Season &middot; '
        f'{_storm_count_label(payload["storms"])}</div>'
        f'<div class="storm-list" id="storms">'
        f'{storm_cards}'
        f'</div>'
        f'</div>'
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
        map_w=map_w, map_h=map_h,
        defs=SVG_DEFS,
        basemap_svg=basemap_svg,
        tracks_svg=tracks_svg,
        active_svg=active_svg,
        wm_x=map_w - 20, wm_y=40,
        side_panel_html=side_panel_html,
        zoom_hint_html="",
        interactive_js="",
        body_class="",
    )


def _staleness_from_z(z: str | None, now: dt.datetime) -> int | None:
    """Whole minutes between an ISO8601-Z fix time and ``now`` (UTC), or None."""
    if not z:
        return None
    try:
        t = dt.datetime.strptime(z, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return ac.staleness_minutes(t, now)


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
    parser.add_argument("--json-only", action="store_true",
                        help="Write {basin}_tracks_data.json but skip the heavy "
                             "{basin}_tracks.html SVG render. Used by the homepage "
                             "global map widget which only consumes the JSON.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    basin = args.basin
    basin_cfg = BASINS[basin]
    log = f"[{basin}-tracks]"
    year = dt.date.today().year

    # ---- Global mode: compose from per-basin JSONs and exit early ----
    # The per-basin generators have already done all the IBTrACS / live
    # ATCF work upstream in the workflow and written
    # /{basin}_tracks_data.json. Re-running that work here would
    # double the CI cost and risk drift between the per-basin maps and
    # the global one. Instead we read those JSONs directly.
    if basin == "global":
        compose = basin_cfg.get("compose_from_basins") or []
        storms: list[dict] = []
        latest_updated = ""
        for sub in compose:
            sub_json = OUTPUT_DIR / f"{sub}_tracks_data.json"
            if not sub_json.exists():
                print(f"{log} WARN: missing {sub_json} — skipping {sub}",
                      file=sys.stderr)
                continue
            try:
                sub_data = json.loads(sub_json.read_text())
            except Exception as e:
                print(f"{log} WARN: failed to parse {sub_json}: {e}",
                      file=sys.stderr)
                continue
            for s in sub_data.get("storms", []):
                # Stamp basin onto each storm so the storm-card renderer
                # can show a basin badge in the side panel.
                s["basin"] = sub
                storms.append(s)
            up = sub_data.get("updated") or ""
            if up > latest_updated:
                latest_updated = up
        header = compute_header_stats(storms)
        build_now = dt.datetime.utcnow()
        # Freshest fix across all composed basins (the per-storm field carries
        # through from each sub-feed).
        fix_times = [s.get("latest_fix_valid_utc") for s in storms
                     if s.get("latest_fix_valid_utc")]
        latest_fix_z = max(fix_times) if fix_times else None
        payload = {
            "basin": "global",
            "basin_name": basin_cfg["full_name"],
            "year": year,
            "updated": latest_updated or
                       build_now.strftime("%Y-%m-%d %H:%M UTC"),
            "generated_utc": ac.now_iso_z(build_now),
            "latest_fix_valid_utc": latest_fix_z,
            "staleness_minutes": _staleness_from_z(latest_fix_z, build_now),
            "header": header,
            "vocab": basin_cfg["vocab"],
            "storms": storms,
        }
        if args.dump_json:
            print(json.dumps(payload, indent=2, default=str))
            return 0
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        # No JSON output for global — the per-basin JSONs are the source
        # of truth. We emit the MapLibre HTML page + a sibling
        # global_storms.geojson that the page fetches at runtime.
        if args.json_only:
            print(f"{log} --json-only is a no-op for global mode "
                  f"(per-basin JSONs are the source).")
            return 0
        # MapLibre + Protomaps vector tiles renders the basemap natively,
        # so the Natural Earth loads from the SVG era are no longer needed
        # on the global page. (Per-basin pages still use them.)
        geojson_path = OUTPUT_DIR / "global_storms.geojson"
        geojson = build_global_geojson(storms)
        geojson_path.write_text(json.dumps(geojson, separators=(",", ":")),
                                encoding="utf-8")
        print(f"{log} wrote {geojson_path} "
              f"({len(geojson['features'])} features)")
        html = render_global_maplibre_html(payload)
        html_path = OUTPUT_DIR / "global_tracks.html"
        html_path.write_text(html, encoding="utf-8")
        print(f"{log} wrote {html_path}")
        active = [s for s in storms if s.get("is_active")]
        print(f"{log} {year}: {header['named']} named · "
              f"{header['cat1plus']} cat1+ · {header['cat5']} cat5 · "
              f"{header['total_ace']} ACE · {len(active)} active")
        return 0

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

    # Observability: separate the BUILD time from DATA freshness.
    #   generated_utc        - when this feed was built (ISO8601 Z)
    #   latest_fix_valid_utc - valid-time of the NEWEST 6-hourly fix used (the
    #                          freshest advisory across all storms this basin)
    #   staleness_minutes    - now - latest_fix_valid_utc, for the frontend
    #   updated              - kept for back-compat; it is the build time
    build_now = dt.datetime.utcnow()
    fix_times = [s["latest_fix_valid_utc"] for s in storms
                 if s.get("latest_fix_valid_utc")]
    latest_fix_z = max(fix_times) if fix_times else None
    payload = {
        "basin": basin,
        "basin_name": basin_cfg["full_name"],
        "year": year,
        "updated": build_now.strftime("%Y-%m-%d %H:%M UTC"),
        "generated_utc": ac.now_iso_z(build_now),
        "latest_fix_valid_utc": latest_fix_z,
        "staleness_minutes": _staleness_from_z(latest_fix_z, build_now),
        "header": header,
        "vocab": basin_cfg["vocab"],
        "storms": storms,
    }

    if args.dump_json:
        print(json.dumps(payload, indent=2, default=str))
        return 0

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_DIR / f"{basin}_tracks_data.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"{log} wrote {json_path}")

    if not args.json_only:
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
        html_path = OUTPUT_DIR / f"{basin}_tracks.html"
        html_path.write_text(html, encoding="utf-8")
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
