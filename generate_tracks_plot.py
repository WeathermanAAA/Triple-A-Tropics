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
import re
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
from ace_core import jtwc_live
from ace_core import (
    SSHS_COLORS,
    build_global_geojson,
    compute_header_stats,
    fetch_nhc_active_sids as ace_fetch_nhc_active_sids,
    merge_and_extract_storms,
    sshs_class,
    sshs_label,
    wears_invest_x,
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
        # Second leg for JTWC basins — see the matching comment in
        # generate_ace_plot.py BASINS. Mirrors that flag exactly; the two
        # BASINS dicts must stay aligned.
        "tcvitals": True,
        # Geographic extent for the rendered map (lon_min, lon_max, lat_min, lat_max)
        "extent": (100.0, 180.0, 0.0, 65.0),
        # Labels at bottom of the map (matching standard basin terminology)
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
        # The EP page/extent covers the whole NE Pacific INCLUDING the Central
        # Pacific (extent runs to -180), and IBTrACS files CP storms under the
        # EP basin — but ATCF designates CP systems with their own trailing
        # letter ("90C") and b-deck prefix ("bcp"). Accept both letters here so
        # CPHC invests (90C/91C, 2026-07-13) surface; a "C" row keeps its C in
        # the display name and gets a CP-prefixed SID so it can never collide
        # with a same-numbered simultaneous "E" invest.
        "invest_letters": ["E", "C"],
        "agency_name": "NHC",
        "agency_url": "https://www.nhc.noaa.gov/",
        "atcf_patterns": [
            "https://triple-a-tropics-proxy.coloradoskier2018.workers.dev/atcf/btk/bep{nn}{year}.dat",
            "https://ftp.nhc.noaa.gov/atcf/btk/bep{nn}{year}.dat",
            # NHC only (proxy -> ftp.nhc); natyphoon.top is WP/JTWC-only (see AL).
        ],
        # Second sweep for DESIGNATED Central Pacific systems: CPHC numbers
        # its own bcp decks (TD 01C = bcp01<year>), which the bep sweep never
        # touches — without this a 90C that designates VANISHES from the live
        # layer until IBTrACS provisional backfills. Historical parity is
        # already there (IBTrACS files CP storms under BASIN=EP, e.g. Ioke =
        # CP01), and parse_bdeck keys the SID off each row's own basin field,
        # so bcp rows land as NHC_CP##<year> — no collision with bep numbers.
        "atcf_patterns_extra": [[
            "https://triple-a-tropics-proxy.coloradoskier2018.workers.dev/atcf/btk/bcp{nn}{year}.dat",
            "https://ftp.nhc.noaa.gov/atcf/btk/bcp{nn}{year}.dat",
        ]],
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

    # SID remap: IBTrACS keys current-season entries by its own SID
    # (season+genesis position), but each row carries USA_ATCF_ID cross-
    # referencing the agency ATCF id ("WP092026"). Remap to the agency sid form
    # ("JTWC_WP092026" — the sid the live b-deck/knackwx path emits) so a
    # freshly-formed system's UNNAMED provisional IBTrACS entry and its live
    # designation share ONE sid and MERGE in merge_and_extract_storms, instead
    # of rendering as a duplicate UNNAMED ghost on the tracks/home map. Done at
    # the STORM level (one representative USA_ATCF_ID per raw sid) so a storm
    # whose earliest fixes predate the id backfill is never split across two
    # sids. A blank/foreign/invest USA_ATCF_ID leaves the raw sid untouched.
    sid_remap: dict = {}
    if "USA_ATCF_ID" in d.columns:
        for raw_sid, grp in d.groupby("SID"):
            for a in grp["USA_ATCF_ID"].dropna():
                mapped = ac.agency_sid_from_atcf_id(a, basin_cfg, year)
                if mapped:
                    sid_remap[raw_sid] = mapped
                    break

    rows = []
    for _, row in d.iterrows():
        ll = _parse_ibtracs_latlon(row)
        if ll is None:
            continue
        lat, lon = ll
        rows.append({
            "SID": sid_remap.get(row["SID"], row["SID"]),
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

    # Accepted ATCF trailing letters for this basin's map: the primary
    # invest_letter, plus any extras (EP also takes "C" — the Central Pacific
    # shares the EP page/extent but ATCF designates its systems 90C/bcp).
    letters = {x.strip().upper() for x in
               (basin_cfg.get("invest_letters")
                or [basin_cfg.get("invest_letter", "")]) if x.strip()}
    if not letters:
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
        atcf_id = (it.get("atcf_id") or "").strip().upper()
        # Basin match off the ATCF id's trailing letter (the authoritative ATCF
        # basin designator: "93E" -> E.Pac, "92W" -> W.Pac, "96P" -> S.Pac).
        # knackwx's separate origin_basin field is sometimes null (observed on
        # EP invests like 93E, which froze them off the tracks map), so deriving
        # the basin from the id itself is what keeps EP/AL invests from being
        # silently dropped; origin_basin is only a fallback when the id has no
        # usable trailing letter.
        id_letter = atcf_id[-1] if atcf_id[-1:].isalpha() else ""
        basin_letter = id_letter or (it.get("origin_basin") or "").strip().upper()
        if basin_letter not in letters:
            continue
        # "93E" -> 93 (drop the trailing basin letter).
        try:
            storm_num = int(atcf_id[:-1])
        except (ValueError, IndexError):
            continue
        is_jtwc = (basin_cfg.get("agency_name") or "").strip().upper() == "JTWC"
        is_invest_num = 90 <= storm_num <= 99
        # JTWC has no CurrentStorms equivalent, so a JUST-designated JTWC TD
        # (the former invest, before its b-deck BEST file is written) falls
        # through both the b-deck sweep (file absent) and the 90-99 invest
        # filter. Treat knackwx as the JTWC live-DESIGNATION source: accept
        # designated numbers 1..49 too. NHC basins keep CurrentStorms
        # authoritative, so this branch is a no-op there (is_jtwc False).
        is_designated = is_jtwc and (1 <= storm_num <= 49)
        if not (is_invest_num or is_designated):
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
            name = f"{storm_num}{basin_letter}"
        # 92W->07W carry. knackwx gives the prior invest as transitioned_from
        # ("92W"). Feed its NUMBER + letter as spawn_invest(_letter) so
        # ace_core's letter-aware superseding-invest dedup
        # (merge_and_extract_storms) retires the prior invest the cycle this
        # designation appears.
        # FRAME-COINCIDENT (recycle-safe, stateless): only carry while the
        # SAME knackwx payload STILL lists that 9x invest -- mirrors the
        # floater's "drop the invest the cycle the link appears". Once knackwx
        # stops listing 92W there is nothing to suppress, and a future RECYCLED
        # 92W (a different system) is never silently dropped.
        spawn_invest = None
        spawn_invest_letter = None
        if is_designated:
            tf = (it.get("transitioned_from") or "").strip().upper()
            mtf = re.fullmatch(r"(\d{1,2})[A-Z]", tf)
            if mtf:
                tf_num = int(mtf.group(1))
                tf_letter = tf[-1] if tf[-1:].isalpha() else ""
                if 90 <= tf_num <= 99 and tf_letter in letters and any(
                    (str((d.get("atcf_id") or "")).strip().upper())
                        == f"{tf_num:02d}{tf_letter}"
                    for d in data):
                    spawn_invest = tf_num
                    # The prior invest's OWN letter ("92W" -> W, "90C" -> C)
                    # rides along so ace_core's dedup stays letter-aware and
                    # can never retire a same-numbered invest in the other
                    # basin sharing this page.
                    spawn_invest_letter = tf_letter
        # SID basin token follows the row's OWN ATCF letter ("C" -> CP), not
        # the page basin, so 90C and a simultaneous 90E never share a SID.
        sid_basin = {"L": "AL", "E": "EP", "C": "CP", "W": "WP"}.get(
            basin_letter, basin_cfg["short"].upper())
        rows.append({
            # SID matches the b-deck path's SID format so a future
            # promotion to a numbered TC (with a real b-deck) doesn't
            # collide with this invest row.
            "SID": f"{basin_cfg['agency_name']}_{sid_basin}"
                   f"{storm_num:02d}{season}",
            "NAME": name,
            "season": season,
            "time": t,
            "lat": lat,
            "lon": lon,
            "wind_kt": vmax,
            "pressure_mb": pres,
            "nature": nature,
            "source": "live-knackwx-designated" if is_designated else "live-knackwx",
            "storm_num": storm_num,
            "spawn_invest": spawn_invest,
            "spawn_invest_letter": spawn_invest_letter,
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

    def _try_fetch_one(nn: int, patterns) -> bool:
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

    # Numbered TCs (01-40), once per deck-prefix chain (EP also sweeps the
    # CPHC bcp decks via atcf_patterns_extra). Bail after 3 consecutive
    # misses per chain to keep the fetch fast — typical seasons have 1-3
    # active storms at a time.
    for patterns in ([basin_cfg["atcf_patterns"]]
                     + list(basin_cfg.get("atcf_patterns_extra") or [])):
        consecutive_misses = 0
        for nn in range(1, 41):
            hit = _try_fetch_one(nn, patterns)
            consecutive_misses = 0 if hit else consecutive_misses + 1
            if consecutive_misses >= 3:
                break

    # Second leg (JTWC basins only — see the "tcvitals" flag in BASINS). Runs
    # BEFORE the invest merge so it sees only b-deck rows as its watermark, and
    # so its output goes through the same knackwx dedup below.
    bdeck_only = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    extended, _ = jtwc_live.extend_with_tcvitals(
        bdeck_only, season, basin_cfg, log_prefix=log_prefix)
    frames = [extended] if not extended.empty else []

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
    # b-deck PRIMARY: where the per-storm b-deck sweep already produced a
    # designated SID, prefer it (richer 6-hourly track). knackwx-designated
    # rows only FILL designated systems the b-deck has not yet written, so drop
    # any knackwx-designated row whose SID the b-deck already carries.
    if "source" in out.columns:
        bdeck_sids = set(
            out.loc[out["source"] == f"live-{basin_cfg['agency_name']}", "SID"])
        drop = ((out["source"] == "live-knackwx-designated")
                & out["SID"].isin(bdeck_sids))
        if drop.any():
            out = out[~drop].reset_index(drop=True)
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

    SYNC: mirrored line-for-line by LIVE_BASIN_JS buildTracksSvg() — the
    live overlay must produce byte-identical markup for the same storms
    list (tests/test_live_overlay_parity.py). Change BOTH or the live
    page will drift from the baked cron fallback.
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
            # Dashed line for invests (90-99) AND Potential Tropical Cyclones
            # (designated but still a DB/DS disturbance NHC is advising on) —
            # the uncertain/pre-genesis systems; solid for numbered TCs (01-89).
            # Keeping the dot styling identical so the wind-class colors
            # still convey intensity — only the connecting line changes.
            dash_attr = (' stroke-dasharray="4 3"'
                         if (storm.get("is_invest") or storm.get("is_ptc"))
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
        # The ATCF NUMBER gates this pass (wears_invest_x, Andrew's 2026-07-14
        # marker rule): 90-99 wear the X; a DESIGNATED system (01-89) renders
        # by intensity below even while NHC advises it as a Potential Tropical
        # Cyclone — the old PTC-wears-the-X design put TD 05E on the map as an
        # invest and is retired. Mirrored in the JS second pass (parity suite).
        if wears_invest_x(storm):
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
    # (triangles, dots, polygons) regardless of source order. ALL invests
    # get the X — active or not (NHC convention for invest areas; the
    # old active-invest "L" from render_active_icons is retired, so one
    # invest can no longer wear two different icons depending on how
    # fresh its latest fix is).
    for storm, x, y, p in invest_current_positions:
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
        # Launchable: wrap the X group + its label sibling in a /cyclolab/{sid}/
        # new-tab anchor (Stage C - invests now have a grey/red-X CycloLab page),
        # exactly like the active-hurricane glyph. The X path stays centred on the
        # group origin and the label stays an offset sibling, so the anchoring
        # invariant (test_invest_x_anchor) is untouched.
        parts.append(
            f'<a href="/cyclolab/{sid}/" target="_blank" rel="noopener">')
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
        parts.append('</a>')
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
    """For each active NON-INVEST storm, place the spinning, glowing TAT
    hurricane icon at its most recent position. The category letter and
    color inside come from current_category, so the marker is
    stage-driven: a current TD wears a blue glyph with "D", a TS a green
    glyph with "S", etc.

    Invests are NOT painted here regardless of active state — every
    invest (90-99) carries the red X + designation from
    render_tracks_svg's invest_current_positions second pass, the NHC
    convention for invest areas. (Historically an ACTIVE invest got a
    bold red "L" from this function instead, which meant two invests
    could wear two different icons purely on fix freshness/dev-level;
    the "L" path is retired — nothing non-invest ever used it.)

    The old hollow-ring marker for peak < 34 kt storms is RETIRED: it
    keyed on PEAK wind, not current stage, so a weakened storm (peaked
    TS, currently TD) and a freshly-designated TD at the same current
    stage wore different markers (the AMANDA-glyph vs TWO-E-ring
    inconsistency of 2026-06-07). Designated storms at the same current
    stage now always wear the same marker.

    SYNC: mirrored line-for-line by LIVE_BASIN_JS buildActiveSvg() — the
    live overlay must produce byte-identical markup for the same storms
    list (tests/test_live_overlay_parity.py). The fork below is the same
    classification as ace_core.build_global_geojson's marker_type
    ("hurricane" for EVERY active designated storm; invests are
    "invest_x" and skipped here); the JS routes through its markerType()
    mirror. Change BOTH."""
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
        is_invest = bool(storm.get("is_invest"))
        is_ptc = bool(storm.get("is_ptc"))
        # Native-tooltip <title> (no JS) showing the storm name + the
        # timestamp of its most recent fix. Inserted as the first child of
        # whichever marker group is drawn below, so hovering any active
        # marker on the static page surfaces "NAME — Last fix: … UTC".
        disp_name = storm.get("name") or storm.get("atcf_id") or ""
        last_fix = _fmt_last_fix(last.get("t"))
        title_txt = (f"{disp_name} - Last fix: {last_fix}"
                     if last_fix else disp_name)
        title_el = f'<title>{_xml_escape(title_txt)}</title>' if title_txt else ''
        # Only 90-99-numbered systems never reach the glyph below — they carry
        # the red X from render_tracks_svg's second pass. The gate is the ATCF
        # NUMBER (wears_invest_x): a designated PTC (01-89) draws the intensity
        # glyph here and is skipped by the X pass, so nothing double-draws.
        if wears_invest_x(storm):
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
        parts.append(f'''<a href="/cyclolab/{sid}/" target="_blank" rel="noopener"><g class="active-icon" data-sid="{sid}" transform="translate({x:.1f},{y:.1f})" style="filter:drop-shadow(0 0 6px {color});">{title_el}
  <g transform="scale({ICON_GLYPH_SCALE})">
    <g class="spin-wrap">
      <path d="M 16.37,-28.27 C 13.58,-28.13 11.51,-27.90 9.23,-27.49 C 1.27,-26.06 -5.88,-22.70 -10.92,-18.02 C -14.83,-14.40 -17.41,-10.06 -18.49,-5.32 C -18.95,-3.30 -19.15,-1.42 -19.15,0.91 C -19.15,2.53 -19.09,3.28 -18.89,4.45 C -18.38,7.38 -17.47,9.46 -15.41,12.37 C -13.88,14.54 -13.43,15.31 -13.20,16.13 C -13.11,16.44 -13.09,16.62 -13.09,17.14 C -13.10,17.93 -13.20,18.32 -13.67,19.28 C -15.30,22.59 -18.65,24.93 -23.49,26.14 C -25.26,26.58 -27.29,26.87 -29.18,26.95 L -30.00,26.98 L -29.65,27.06 C -27.33,27.62 -24.41,28.05 -21.57,28.27 C -20.04,28.38 -16.31,28.38 -14.80,28.27 C -12.93,28.13 -11.43,27.95 -9.77,27.67 C -0.59,26.14 7.56,22.03 12.68,16.37 C 16.22,12.45 18.28,8.10 18.93,3.13 C 19.64,-2.25 18.99,-6.47 16.84,-10.16 C 16.48,-10.80 15.79,-11.82 14.99,-12.95 C 13.61,-14.89 13.18,-15.77 13.12,-16.83 C 13.07,-17.61 13.23,-18.26 13.71,-19.23 C 14.97,-21.79 17.38,-23.84 20.67,-25.16 C 23.13,-26.14 26.24,-26.77 29.15,-26.87 L 30.00,-26.90 L 29.67,-26.98 C 29.13,-27.12 27.57,-27.44 26.66,-27.58 C 24.96,-27.87 23.39,-28.05 21.66,-28.18 C 20.72,-28.25 17.16,-28.30 16.37,-28.27 Z" fill="{color}"/>
      <animateTransform attributeName="transform" attributeType="XML" type="rotate" from="360" to="0" dur="2.6s" repeatCount="indefinite"/>
    </g>
  </g>
  <text y="0" text-anchor="middle" dominant-baseline="central" font-size="{ICON_LETTER_PT}" font-weight="900" fill="#ffffff" paint-order="stroke" stroke="rgba(0,0,0,0.55)" stroke-width="1.8" stroke-linejoin="round">{label}</text>
  <text class="name" x="{ICON_NAME_X}" y="{ICON_NAME_Y}" text-anchor="start">{name}</text>
</g></a>''')
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
    "C2": "#ff9a2f", "C3": "#f5333c", "C4": "#e33ad4", "C5": "#b03bff"
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
    card.addEventListener("click", function(ev) {
      // a real link inside the card (the CycloLab entry) navigates;
      // the placard toggle must not swallow it.
      if (ev.target.closest("a")) return;
      toggleInline(card.dataset.sid);
    });
  });
  // Spinning map icon → reveal that active storm's wind-history chart
  // in the sidebar card.
  document.querySelectorAll(".active-icon").forEach(function(g) {
    g.addEventListener("click", function(e) {
      e.stopPropagation();
      openInline(g.dataset.sid);
    });
  });

  // ---- CycloLab pre-launch dialog (FG-R3 #3) --------------------------------
  // "Open in CycloLab ▸" (storm cards + map popup) opens a house dialog
  // the FIRST time: storm name, a category-accent Launch button, and an
  // optional wind-units choice. The choice is persisted in localStorage
  // (SAME-ORIGIN, so the CycloLab shell reads it) and handed off via
  // ?units= too; after the first launch the click goes straight in. A
  // modified click (new tab) or no-JS keeps the plain <a href>.
  (function () {
    var LKEY = "cyclolab:launched", SKEY = "cyclolab:settings";
    var UNITS = [["kt", "kt"], ["mph", "mph"], ["kmh", "km/h"]];
    function ls(get, key, val) {
      try { return get ? localStorage.getItem(key)
                       : localStorage.setItem(key, val); }
      catch (e) { return null; }
    }
    function storedUnit() {
      try { var s = JSON.parse(ls(1, SKEY) || "{}");
        return s && s.windUnits || "kt"; } catch (e) { return "kt"; }
    }
    function go(href, unit) {
      ls(0, LKEY, "1");
      ls(0, SKEY, JSON.stringify({ windUnits: unit }));
      var u = href + (href.indexOf("?") < 0 ? "?" : "&") + "units=" + unit;
      // CycloLab is a web app and lives in its OWN tab: open a NEW tab so
      // the map stays behind in the original tab. Nothing loads same-tab.
      window.open(u, "_blank", "noopener");
    }
    var dlg = null, pickUnit = "kt";
    function ensureDialog() {
      if (dlg) return dlg;
      var css = document.createElement("style");
      css.textContent =
        ".cl-launch-back{position:fixed;inset:0;z-index:9999;display:flex;" +
        "align-items:center;justify-content:center;background:rgba(4,8,14,.6);" +
        "padding:20px}.cl-launch-back[hidden]{display:none}" +
        ".cl-launch{width:min(380px,100%);background:#121a26;border:1px solid " +
        "#26354a;border-radius:16px;padding:22px 22px 20px;color:#e8eef5;" +
        "font-family:Metropolis,system-ui,sans-serif;box-shadow:0 20px 60px " +
        "rgba(0,0,0,.55)}.cl-launch h2{margin:0 0 2px;font-size:19px;" +
        "font-weight:800;letter-spacing:.2px}.cl-launch .cl-eyebrow{font-size:" +
        "11px;font-weight:700;letter-spacing:1.4px;text-transform:uppercase;" +
        "color:#8ba0bd;margin-bottom:10px}.cl-launch .cl-go{display:block;" +
        "width:100%;margin:16px 0 4px;padding:12px;border:0;border-radius:10px;" +
        "font:inherit;font-size:15px;font-weight:800;color:#06101c;cursor:" +
        "pointer}.cl-launch .cl-customize{background:none;border:0;color:" +
        "#8ba0bd;font:inherit;font-size:12.5px;font-weight:700;cursor:pointer;" +
        "padding:6px 0;text-decoration:underline}.cl-launch .cl-units{display:" +
        "none;margin:10px 0 2px}.cl-launch .cl-units.open{display:block}" +
        ".cl-launch .cl-units-lbl{font-size:11px;font-weight:700;letter-" +
        "spacing:1px;text-transform:uppercase;color:#8ba0bd;margin-bottom:7px}" +
        ".cl-seg{display:flex}.cl-seg button{flex:1;background:#0d141f;color:" +
        "#8ba0bd;border:1px solid #26354a;padding:9px 0;font:inherit;font-" +
        "size:13px;font-weight:700;cursor:pointer}.cl-seg button:first-child{" +
        "border-radius:8px 0 0 8px}.cl-seg button:last-child{border-radius:0 " +
        "8px 8px 0;border-left:0}.cl-seg button:not(:first-child):not(:last-" +
        "child){border-left:0}.cl-seg button.on{color:#06101c}.cl-note{margin:" +
        "12px 0 0;font-size:11px;line-height:1.5;color:#7e90a9}";
      document.head.appendChild(css);
      var back = document.createElement("div");
      back.className = "cl-launch-back"; back.hidden = true;
      back.innerHTML =
        '<div class="cl-launch" role="dialog" aria-modal="true" ' +
        'aria-label="Launch CycloLab"><div class="cl-eyebrow">CycloLab</div>' +
        '<h2 class="cl-storm"></h2>' +
        '<button class="cl-go" type="button">Launch CycloLab</button>' +
        '<button class="cl-customize" type="button">Customize settings</button>' +
        '<div class="cl-units"><div class="cl-units-lbl">Wind units</div>' +
        '<div class="cl-seg"></div>' +
        '<p class="cl-note">Display only. Agency forecasts are issued in ' +
        'knots; other units are converted in CycloLab.</p></div></div>';
      document.body.appendChild(back);
      var seg = back.querySelector(".cl-seg");
      UNITS.forEach(function (u) {
        var b = document.createElement("button");
        b.type = "button"; b.textContent = u[1];
        b.setAttribute("data-u", u[0]);
        b.addEventListener("click", function () {
          pickUnit = u[0]; paintSeg(); });
        seg.appendChild(b);
      });
      function paintSeg() {
        seg.querySelectorAll("button").forEach(function (b) {
          var on = b.getAttribute("data-u") === pickUnit;
          b.classList.toggle("on", on);
          b.style.background = on ? back._accent : "";
          b.style.borderColor = on ? back._accent : "";
        });
      }
      back._paintSeg = paintSeg;
      back.querySelector(".cl-customize").addEventListener("click",
        function () { back.querySelector(".cl-units").classList.toggle("open"); });
      back.addEventListener("click", function (e) {
        if (e.target === back) back.hidden = true; });
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") back.hidden = true; });
      dlg = back;
      return back;
    }
    function openDialog(href, name, accent) {
      var d = ensureDialog();
      pickUnit = storedUnit();
      d._accent = accent || "#3b82f6";
      d.querySelector(".cl-storm").textContent = name || "This storm";
      var go1 = d.querySelector(".cl-go");
      go1.style.background = d._accent;
      d.querySelector(".cl-units").classList.remove("open");
      d._paintSeg();
      go1.onclick = function () { d.hidden = true; go(href, pickUnit); };
      d.hidden = false;
      go1.focus();
    }
    document.addEventListener("click", function (e) {
      var a = e.target.closest && e.target.closest("a.cyclolab-link");
      if (!a) return;
      // modified click / non-left button: let the browser do its thing
      // (new tab etc.); no-JS already navigates the href.
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey ||
          e.shiftKey || e.altKey) return;
      e.preventDefault();
      var href = a.getAttribute("href");
      if (ls(1, LKEY)) { go(href, storedUnit()); return; }  // straight in
      openDialog(href, a.getAttribute("data-name"),
                 a.getAttribute("data-accent"));
    });
  })();

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

# ---------------------------------------------------------------------------
# ONE canonical active-storm icon definition (site-wide visual consistency).
# BOTH renderers derive from these - the per-basin SVG (render_active_icons /
# render_tracks_svg f-strings + the __ICON_*__ tokens in HTML_TEMPLATE's CSS)
# and the global MapLibre template (its CSS/JS carry the same tokens, applied
# by _apply_icon_tokens at render time). Tweak sizes HERE ONLY. Canonical =
# the per-basin tracks-page appearance at 1:1 viewBox scale; the global
# markers pin their CSS box to their viewBox (1 SVG unit == 1 CSS px) so the
# same numbers render identically at default zoom. Banners and storm cards
# (active-banner.js, the sidebar placard) intentionally keep their own sizing.
ICON_GLYPH_SCALE = 0.7   # x the +/-30-unit HURRICANE_PATH -> 42px glyph
ICON_LETTER_PT = 14      # SSHS category letter inside the glyph
ICON_NAME_PT = 16        # storm-name/designation labels (was 12/13 - bigger
                         # proportion vs the glyph so names read clearly)
ICON_NAME_X = 28         # name anchor right of the glyph centre
ICON_NAME_Y = 5
ICON_HBOX = 68           # hurricane marker viewBox == CSS box (1:1)
# (ICON_TD_R / ICON_TDBOX_* are gone with the retired hollow-TD ring —
# designated TDs wear the standard glyph with a blue "D" now.)


def _apply_icon_tokens(html: str) -> str:
    """Inject the canonical icon sizes into a rendered page (both page
    templates carry __ICON_*__ tokens), so marker geometry has exactly one
    source of truth above."""
    for token, value in (
        ("__ICON_GLYPH_SCALE__", ICON_GLYPH_SCALE),
        ("__ICON_LETTER_PT__", ICON_LETTER_PT),
        ("__ICON_NAME_PT__", ICON_NAME_PT),
        ("__ICON_NAME_X__", ICON_NAME_X),
        ("__ICON_NAME_Y__", ICON_NAME_Y),
        ("__ICON_HBOX__", ICON_HBOX),
    ):
        html = html.replace(token, str(value))
    return html



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
    --c1: #ffe14d; --c2: #ff9a2f; --c3: #f5333c;
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
  .active-icon .name {{ fill: #f1f7fd; font-size: __ICON_NAME_PT__px; font-weight: 700;
    paint-order: stroke; stroke: #07101c; stroke-width: 3;
    stroke-linejoin: round; pointer-events: none; }}
  body.interactive .active-icon {{ cursor: pointer; }}
  /* Invest current-position label (atcf_id like "91W"). Same typography
     as .active-icon .name but tinted red to match the X glow. */
  .invest-label {{ fill: #ff5050; font-size: __ICON_NAME_PT__px; font-weight: 700;
    paint-order: stroke; stroke: #07101c; stroke-width: 3;
    stroke-linejoin: round; pointer-events: none;
    dominant-baseline: middle; }}

  /* Sidebar */
  .side {{ flex: 1 1 520px; min-width: 320px; display: flex; flex-direction: column; gap: 8px; }}
  .panel-title {{ color: var(--muted); font-size: 12px; margin: 2px 2px 0;
    text-transform: uppercase; letter-spacing: 0.8px; font-weight: 700; }}
  .storm-list {{ background: var(--panel); border: 1px solid var(--border);
    border-radius: 10px; max-height: 820px; overflow-y: auto;
    scrollbar-color: #2a3e5c transparent;
    /* Responsive grid: cards flow in rows AND columns, auto-filling the panel
       width (one column on narrow phones, two+ on wide screens). */
    display: grid; grid-template-columns: repeat(auto-fill, minmax(232px, 1fr));
    gap: 8px; padding: 8px; align-items: start; align-content: start; }}
  .storm-list::-webkit-scrollbar {{ width: 8px; }}
  .storm-list::-webkit-scrollbar-thumb {{ background: #2a3e5c; border-radius: 4px; }}
  .storm-card {{ padding: 10px 12px; border: 1px solid var(--border);
    border-radius: 8px; background: var(--panel); }}
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
  /* PTC card — the grey identity the marker + page wear (html[data-ptc]): no
     amber active-tint, NO TD category chip (a PTC is not a depression), a grey
     PTC tag. Named storms + invests are untouched. */
  .storm-card.ptc {{ background: rgba(154,166,182,0.09); }}
  .storm-card.ptc .storm-cat {{ display: none; }}
  .storm-ptc {{ font-size: 10px; color: #9aa6b6; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.6px; margin-left: 6px; }}
  .storm-ptc::before {{ content: "◌ "; }}

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
  /* Red-X glyph for the invest-position legend row. Mirrors the
     current-position X every invest carries on the map (#ff2a2a,
     round caps) — two crossed bars built from pseudo-elements. */
  .legend .investx {{ width: 10px; height: 10px; position: relative; }}
  .legend .investx::before, .legend .investx::after {{ content: "";
    position: absolute; left: -1px; right: -1px; top: 4px; height: 2.4px;
    border-radius: 2px; background: #ff2a2a; }}
  .legend .investx::before {{ transform: rotate(45deg); }}
  .legend .investx::after {{ transform: rotate(-45deg); }}
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

  /* Clickable storm cards. The live overlay (LIVE_BASIN_JS) wires a delegated
     click handler on #storms on every per-basin page, so the cards are always
     interactive — pointer + hover, no body.interactive gate needed. */
  .storm-card.clickable {{ cursor: pointer; transition: background 0.15s; }}
  .storm-card.clickable:hover {{ background: rgba(255,255,255,0.04); }}
  .storm-card.clickable.active:hover {{ background: rgba(255,184,58,0.14); }}
  .click-hint {{ font-size: 10px; color: var(--muted); margin-top: 4px;
    text-transform: uppercase; letter-spacing: 0.6px; font-weight: 700;
    opacity: 0.6; }}
  .storm-card.clickable.active .click-hint {{ color: var(--c1); opacity: 0.75; }}
  .storm-card.clickable.open .click-hint {{ opacity: 0.25; }}
  .cyclolab-link {{ display: inline-block; margin-top: 7px; padding: 3px 11px;
    border: 1px solid rgba(159,198,245,0.45); border-radius: 999px;
    color: #9fc6f5; font-size: 10.5px; font-weight: 700;
    letter-spacing: 0.5px; text-decoration: none; }}
  .cyclolab-link:hover {{ border-color: #9fc6f5; color: #cfe4ff;
    background: rgba(159,198,245,0.08); }}
  /* Phone legibility: the 10px uppercase micro-labels drop under the
     readable floor on small screens - bump them (CSS-only; the card
     MARKUP is byte-parity-mirrored in LIVE_BASIN_JS, this is not). */
  @media (max-width: 560px) {{
    .click-hint {{ font-size: 12px; }}
    .storm-active, .storm-invest {{ font-size: 12px; }}
  }}

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
        <!-- id="as-of" mirrors the global MapLibre page: the live overlay
             (LIVE_BASIN_JS) overwrites it from the feed's `updated` stamp;
             the baked build-time text stands when JS/fetch fails. -->
        <div class="sub" id="as-of">As of {updated}</div>
      </div>
      <div class="stats" id="season-stats">{stats_html}</div>
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
        <div class="item"><span class="investx"></span>Invest position</div>
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
# Per-basin LIVE overlay (Phase 4: poller-primary storm display, cron backup)
# ---------------------------------------------------------------------------
# Injected into HTML_TEMPLATE's {interactive_js} slot. At view time it
# refetches the SAME live tracks feed the page's parent already pulls for
# the "Active Now" banner (feeds/{basin}_tracks_data.json on R2, written
# by the streaming poller every cycle since WRITE_LIVE_FEEDS=false), then
# atomically replaces every storm-derived fragment of the baked page:
#   <g class="tracks">, <g class="active-storms">, #season-stats,
#   #panel-title, #storms (cards), and the #as-of freshness line.
# The basemap (the ~2 MB of Natural Earth paths) is never touched.
#
# FALLBACK CONTRACT: any failure — fetch error, wrong feed shape, basin or
# season-rollover year mismatch, SVG parse error — leaves the cron-baked
# render fully intact (console.warn only). The 6-hourly cron is therefore
# automatically the backup writer; there is no WRITE_*_ON_CRON gate to
# flip because the data feed is already poller-owned.
#
# SYNC CONTRACT: the build* functions below are line-for-line JS mirrors
# of the Python renderers (render_tracks_svg, render_active_icons,
# render_storm_card, render_cards_html, render_panel_title_html,
# render_stats_html) and MUST stay byte-identical for the same input —
# tests/test_live_overlay_parity.py enforces this. Change both sides.
#
# Kept as a raw string (no .format()) like TRACKS_JS; per-basin values
# arrive via __LIVE_*__ tokens (build_live_overlay_js) and icon geometry
# via the canonical __ICON_*__ tokens (_apply_icon_tokens on the page).

FEEDS_BASE_URL = "https://cdn.triple-a-tropics.com/feeds/"

LIVE_BASIN_JS = r"""
(function () {
  "use strict";

  var CFG = {
    basin: "__LIVE_BASIN__",
    year: __LIVE_YEAR__,
    extent: __LIVE_EXTENT__,          // (lon_min, lon_max, lat_min, lat_max) from BASINS
    mapW: __LIVE_MAP_W__,
    mapH: __LIVE_MAP_H__,
    feedUrl: "__LIVE_FEED_URL__",
    colors: __LIVE_SSHS_COLORS__      // ace_core.SSHS_COLORS
  };

  // Mirrors ace_core.sshs_label() — the SSHS letter inside the glyph.
  var SSHS_LABELS = {"TD": "D", "TS": "S",
                     "C1": "1", "C2": "2", "C3": "3", "C4": "4", "C5": "5"};

  // ---- Python-formatting mirrors -----------------------------------------

  function pyFixed(x, d) {
    // Mirrors Python f"{x:.<d>f}": correctly-rounded decimal output with
    // ties-to-even, decided on the double's EXACT decimal expansion.
    // toFixed() alone misrounds true binary ties (it breaks them away
    // from zero, and exact .25/.75 hundredths DO occur in projected
    // coords — WP's lon scale is 17.5 px/deg), while any pre-scaling
    // like x*10 collapses near-ties onto exact ties (0.05*10 === 0.5).
    // So inspect toFixed(20) — exact per spec, and 1e-20 resolves far
    // below the half-ulp of every magnitude this page formats.
    var neg = x < 0 || (x === 0 && 1 / x < 0);
    var s = Math.abs(x).toFixed(20);
    var dot = s.indexOf(".");
    var intPart = s.slice(0, dot);
    var keep = s.slice(dot + 1, dot + 1 + d);
    var rest = s.slice(dot + 1 + d);
    var first = rest.charAt(0);
    var roundUp;
    if (first > "5") {
      roundUp = true;
    } else if (first < "5") {
      roundUp = false;
    } else if (/[1-9]/.test(rest.slice(1))) {
      roundUp = true;   // strictly above the midpoint
    } else {
      // exact tie -> round to even
      var lastKept = (d > 0 ? keep.charCodeAt(d - 1)
                            : intPart.charCodeAt(intPart.length - 1)) - 48;
      roundUp = (lastKept % 2) === 1;
    }
    if (roundUp) {
      var digits = (intPart + keep).split("");
      var i = digits.length - 1;
      for (; i >= 0; i--) {
        if (digits[i] === "9") {
          digits[i] = "0";
        } else {
          digits[i] = String.fromCharCode(digits[i].charCodeAt(0) + 1);
          break;
        }
      }
      if (i < 0) digits.unshift("1");
      var all = digits.join("");
      intPart = all.slice(0, all.length - d) || "0";
      keep = all.slice(all.length - d);
    }
    var out = d > 0 ? intPart + "." + keep : intPart;
    return neg ? "-" + out : out;
  }
  function fmt1(x) { return pyFixed(x, 1); }

  function pyNum(v) {
    // Mirrors Python f"{v}" on the feed's numeric fields. The feeds are
    // json.dumps'd from pandas floats, so integral values arrive in the
    // JSON text as "25.0" — JSON.parse erases the int/float distinction,
    // so put the ".0" back on integral values. (A feed that ever emitted
    // int-typed numbers would diverge; the live-feed parity check in
    // tests/test_live_overlay_parity.py guards that assumption.)
    if (v == null) return "";
    return Number.isInteger(v) ? v + ".0" : String(v);
  }

  function escapeXml(s) {
    // Mirrors _xml_escape() — order matters (& first).
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function fmtLastFix(iso) {
    // Mirrors _fmt_last_fix(): ISO fix time -> "YYYY-MM-DD HH:MM UTC",
    // raw string when unparseable (feed fixes always carry a time part).
    if (!iso) return "";
    var s = String(iso);
    var m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})/.exec(s);
    if (!m) return s;
    return m[1] + " " + m[2] + ":" + m[3] + " UTC";
  }

  function fmtDateOnly(iso) {
    // Mirrors _fmt_date_range()'s inner fmt(): "%b %-d", "?" on failure.
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso));
    if (!m) return "?";
    var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    var mi = parseInt(m[2], 10) - 1;
    if (mi < 0 || mi > 11) return "?";
    return months[mi] + " " + parseInt(m[3], 10);
  }

  function fmtDateRange(start, end) {
    // Mirrors _fmt_date_range().
    if (!start) return "-";
    var s = fmtDateOnly(start);
    var e = end ? fmtDateOnly(end) : s;
    return s !== e ? s + " – " + e : s;
  }

  // ---- Geometry ----------------------------------------------------------

  function project(lon, lat) {
    // Mirrors build_projection(): pure linear equirectangular, including
    // the antimeridian wrap guard (inert for the three per-basin extents,
    // whose lon_max <= 180 — kept so the math is config-driven like the
    // Python closure).
    if (CFG.extent[1] > 180 && lon < CFG.extent[0]) lon += 360;
    var x = (lon - CFG.extent[0]) / (CFG.extent[1] - CFG.extent[0]) * CFG.mapW;
    var y = (CFG.extent[3] - lat) / (CFG.extent[3] - CFG.extent[2]) * CFG.mapH;
    return [x, y];
  }

  // ---- Marker classification ----------------------------------------------

  function wearsInvestX(storm) {
    // Mirror of ace_core.wears_invest_x - the ATCF NUMBER gates the invest
    // X (Andrew's 2026-07-14 marker rule): 90-99 = invest area, 01-89 =
    // designated system rendering by intensity (a PTC included). Reads the
    // storm's own designation, then the SID number token, then falls back
    // to the is_invest flag when no number is parseable.
    var m = /^(\d{1,2})[A-Z]$/.exec(String(storm.atcf_id || "").toUpperCase());
    if (m) return parseInt(m[1], 10) >= 90;
    var s = /^[A-Z]+_[A-Z]{2}(\d{2})\d{4}$/.exec(String(storm.sid || "").toUpperCase());
    if (s) return parseInt(s[1], 10) >= 90;
    return !!storm.is_invest;
  }

  function markerType(storm) {
    // THE single client-side source of the marker classification.
    // Mirrors ace_core.build_global_geojson's marker_type fork
    // (ace_core/ace_core/__init__.py, the "Two flavors" block):
    //   ATCF number 90-99 (active or not) -> "invest_x"  (NHC invest-area X)
    //   designated 01-89, active OR a PTC -> "hurricane" (glyph;
    //                                        current_category picks the
    //                                        letter/color - a PTC renders
    //                                        by intensity, the old
    //                                        PTC-wears-the-X design is
    //                                        retired per Andrew 2026-07-14)
    //   otherwise              -> null (no current-position marker)
    // (The old active-invest "L" is retired - every invest wears the X.
    // The old "td_circle" peak<34 ring is retired too: keying on PEAK
    // wind gave a weakened storm and a fresh TD at the SAME current
    // stage different markers. Stage now only picks the glyph letter.)
    // tests/test_marker_type_agreement.py asserts the two implementations
    // agree on every case - keep them in lockstep.
    if (wearsInvestX(storm)) return "invest_x";
    if (storm.is_active || storm.is_ptc) return "hurricane";
    return null;
  }

  // ---- SVG builders (line-for-line mirrors of the Python renderers) ------

  function buildTracksSvg(storms) {
    // SYNC: mirrors render_tracks_svg() byte-for-byte (parity-tested).
    // Same two-pass structure: per-storm polyline, then invest triangles
    // OR phase dots, then EVERY invest's red X + label (active or not)
    // drawn last so it sits on top.
    var parts = ['<g class="tracks">'];
    var investCurrent = [];   // mirrors invest_current_positions
    for (var si = 0; si < storms.length; si++) {
      var storm = storms[si];
      var pts = storm.points || [];
      if (pts.length < 1) continue;
      var sid = storm.sid || "";
      var sname = String(storm.name || "UNNAMED").replace(/"/g, "");
      var xy = [];
      for (var i = 0; i < pts.length; i++) {
        var lon = pts[i].lon;
        // Wrap longitudes into the basin extent if needed (WP goes east of 180)
        if (CFG.extent[1] > 180 && lon < CFG.extent[0]) lon += 360;
        if (CFG.extent[1] <= 180 && CFG.extent[0] < 0 && lon > 180) lon -= 360;
        xy.push(project(lon, pts[i].lat));
      }
      if (xy.length >= 2) {
        var JUMP_THRESHOLD = CFG.mapW * 0.5;
        var dParts = [];
        var prevX = null;
        for (var j = 0; j < xy.length; j++) {
          if (prevX === null || Math.abs(xy[j][0] - prevX) > JUMP_THRESHOLD) {
            dParts.push("M " + fmt1(xy[j][0]) + "," + fmt1(xy[j][1]));
          } else {
            dParts.push("L " + fmt1(xy[j][0]) + "," + fmt1(xy[j][1]));
          }
          prevX = xy[j][0];
        }
        var d = dParts.join(" ");
        var dashAttr = (storm.is_invest || storm.is_ptc) ? ' stroke-dasharray="4 3"' : "";
        parts.push('<path d="' + d + '" fill="none" stroke="#ffffff" ' +
                   'stroke-width="1.2" stroke-opacity="0.5" ' +
                   'stroke-linejoin="round" stroke-linecap="round"' +
                   dashAttr + '/>');
      }

      // ATCF-number gate, mirroring the Python second pass: only 90-99
      // wear the invest treatment; a designated PTC renders by intensity.
      if (wearsInvestX(storm)) {
        var lastIdx = xy.length - 1;
        for (var k = 0; k < xy.length; k++) {
          var x = xy[k][0], y = xy[k][1];
          var p = pts[k];
          var t = p.t || "";
          var cls = p.cls || "TD";
          var windAttr = p.wind_kt != null ? pyNum(p.wind_kt) : "";
          var presAttr = p.pressure_mb != null ? pyNum(p.pressure_mb) : "";
          var commonAttrs =
            'data-sid="' + sid + '" data-name="' + sname + '" data-t="' + t + '" ' +
            'data-wind="' + windAttr + '" data-pres="' + presAttr + '" ' +
            'data-cls="' + cls + '" data-phase="invest"';
          if (k < lastIdx) {
            var r = 3.5;
            var half = r * 0.866;
            var p1 = fmt1(x) + "," + fmt1(y - r);
            var p2 = fmt1(x + half) + "," + fmt1(y + r * 0.5);
            var p3 = fmt1(x - half) + "," + fmt1(y + r * 0.5);
            parts.push('<polygon class="track-dot invest-past" ' +
                       'points="' + p1 + ' ' + p2 + ' ' + p3 + '" ' +
                       'fill="#ffffff" stroke="#ffffff" ' +
                       'stroke-width="0.9" stroke-opacity="0.85" ' +
                       commonAttrs + '/>');
          } else {
            investCurrent.push([storm, x, y, p]);
          }
        }
        continue;
      }

      for (var k2 = 0; k2 < xy.length; k2++) {
        var x2 = xy[k2][0], y2 = xy[k2][1];
        var p2o = pts[k2];
        var cls2 = p2o.cls || "TD";
        var t2 = p2o.t || "";
        var nature = String(p2o.nature || "").toUpperCase();
        var color = CFG.colors[cls2] !== undefined ? CFG.colors[cls2] : CFG.colors.TD;
        var r2 = cls2 === "TD" ? 3 : (cls2 === "TS" ? 4 : 5);
        var phase;
        if (nature === "SS") {
          phase = "st";
        } else if (nature === "ET" || nature === "DS" ||
                   nature === "DB" || nature === "LO") {
          phase = "non";
        } else {
          phase = "tc";
        }
        var windAttr2 = p2o.wind_kt != null ? pyNum(p2o.wind_kt) : "";
        var presAttr2 = p2o.pressure_mb != null ? pyNum(p2o.pressure_mb) : "";
        var commonAttrs2 =
          'fill="' + color + '" stroke="#ffffff" stroke-width="0.9" ' +
          'stroke-opacity="0.85" ' +
          'data-sid="' + sid + '" data-name="' + sname + '" data-t="' + t2 + '" ' +
          'data-wind="' + windAttr2 + '" data-pres="' + presAttr2 + '" ' +
          'data-cls="' + cls2 + '" data-phase="' + phase + '"';
        if (phase === "tc") {
          parts.push('<circle class="track-dot" cx="' + fmt1(x2) + '" cy="' + fmt1(y2) + '" ' +
                     'r="' + r2 + '" ' + commonAttrs2 + '/>');
        } else if (phase === "st") {
          parts.push('<rect class="track-dot" x="' + fmt1(x2 - r2) + '" y="' + fmt1(y2 - r2) + '" ' +
                     'width="' + (r2 * 2) + '" height="' + (r2 * 2) + '" ' + commonAttrs2 + '/>');
        } else {
          var half2 = r2 * 0.866;
          var q1 = fmt1(x2) + "," + fmt1(y2 - r2);
          var q2 = fmt1(x2 + half2) + "," + fmt1(y2 + r2 * 0.5);
          var q3 = fmt1(x2 - half2) + "," + fmt1(y2 + r2 * 0.5);
          parts.push('<polygon class="track-dot" points="' + q1 + ' ' + q2 + ' ' + q3 + '" ' +
                     commonAttrs2 + '/>');
        }
      }
    }

    for (var m = 0; m < investCurrent.length; m++) {
      var st = investCurrent[m][0];
      // Every deferred entry is an invest and every invest is
      // markerType() "invest_x" (active or not) — the gate is purely
      // defensive and mirrors the Python pass drawing ALL invests.
      if (markerType(st) !== "invest_x") continue;
      var ix = investCurrent[m][1], iy = investCurrent[m][2];
      var ip = investCurrent[m][3];
      var sid3 = st.sid || "";
      var sname3 = String(st.name || "UNNAMED").replace(/"/g, "");
      var atcfId = st.atcf_id || sname3;
      var t3 = ip.t || "";
      var cls3 = ip.cls || "TD";
      var windAttr3 = ip.wind_kt != null ? pyNum(ip.wind_kt) : "";
      var presAttr3 = ip.pressure_mb != null ? pyNum(ip.pressure_mb) : "";
      var commonAttrs3 =
        'data-sid="' + sid3 + '" data-name="' + sname3 + '" data-t="' + t3 + '" ' +
        'data-wind="' + windAttr3 + '" data-pres="' + presAttr3 + '" ' +
        'data-cls="' + cls3 + '" data-phase="invest"';
      // Launchable: /cyclolab/{sid}/ new-tab anchor wrapping the X + label
      // (byte-identical to render_tracks_svg's Python pass).
      parts.push('<a href="/cyclolab/' + sid3 + '/" target="_blank" rel="noopener">');
      parts.push('<g class="invest-current" ' +
                 'transform="translate(' + fmt1(ix) + ',' + fmt1(iy) + ')" ' +
                 'filter="url(#invest-red-glow)">' +
                 '<path class="track-dot" ' +
                 'd="M -7 -7 L 7 7 M -7 7 L 7 -7" ' +
                 'stroke="#ff2a2a" stroke-width="2.4" ' +
                 'stroke-linecap="round" fill="none" ' +
                 commonAttrs3 + '/>' +
                 '</g>');
      parts.push('<text class="invest-label" ' +
                 'x="' + fmt1(ix + 11) + '" y="' + fmt1(iy + 4) + '" ' +
                 'text-anchor="start">' + atcfId + '</text>');
      parts.push('</a>');
    }
    parts.push('</g>');
    return parts.join("\n");
  }

  function buildActiveSvg(storms) {
    // SYNC: mirrors render_active_icons() byte-for-byte (parity-tested).
    // Every "hurricane" (= every active designated storm) gets the
    // spinning glyph — current_category picks its letter/color; null and
    // "invest_x" (every invest, active or not) are skipped here — the
    // X comes from buildTracksSvg's second pass.
    var parts = ['<g class="active-storms">'];
    for (var si = 0; si < storms.length; si++) {
      var storm = storms[si];
      var mt = markerType(storm);
      if (mt === null || mt === "invest_x") continue;  // not storm.is_active
      var pts = storm.points || [];
      if (!pts.length) continue;
      var last = pts[pts.length - 1];
      var lon = last.lon;
      // Deliberately only the +360 guard here — render_active_icons does
      // not carry the lon-=360 branch; mirror it exactly.
      if (CFG.extent[1] > 180 && lon < CFG.extent[0]) lon += 360;
      var xyA = project(lon, last.lat);
      var x = xyA[0], y = xyA[1];
      var sid = storm.sid || "";
      var dispName = storm.name || storm.atcf_id || "";
      var lastFix = fmtLastFix(last.t);
      var titleTxt = lastFix ? (dispName + " - Last fix: " + lastFix) : dispName;
      var titleEl = titleTxt ? "<title>" + escapeXml(titleTxt) + "</title>" : "";

      var cls = storm.current_category || "TD";
      var color = CFG.colors[cls] !== undefined ? CFG.colors[cls] : CFG.colors.TD;
      var label = SSHS_LABELS[cls];
      var name = storm.name || "";
      parts.push('<a href="/cyclolab/' + sid + '/" target="_blank" rel="noopener">' +
                 '<g class="active-icon" data-sid="' + sid + '" ' +
                 'transform="translate(' + fmt1(x) + ',' + fmt1(y) + ')" ' +
                 'style="filter:drop-shadow(0 0 6px ' + color + ');">' + titleEl + '\n' +
                 '  <g transform="scale(__ICON_GLYPH_SCALE__)">\n' +
                 '    <g class="spin-wrap">\n' +
                 '      <path d="__LIVE_HURRICANE_PATH__" fill="' + color + '"/>\n' +
                 '      <animateTransform attributeName="transform" attributeType="XML" type="rotate" from="360" to="0" dur="2.6s" repeatCount="indefinite"/>\n' +
                 '    </g>\n' +
                 '  </g>\n' +
                 '  <text y="0" text-anchor="middle" dominant-baseline="central" font-size="__ICON_LETTER_PT__" font-weight="900" fill="#ffffff" paint-order="stroke" stroke="rgba(0,0,0,0.55)" stroke-width="1.8" stroke-linejoin="round">' + label + '</text>\n' +
                 '  <text class="name" x="__ICON_NAME_X__" y="__ICON_NAME_Y__" text-anchor="start">' + name + '</text>\n' +
                 '</g></a>');
    }
    parts.push('</g>');
    return parts.join("\n");
  }

  // ---- HTML builders (sidebar / header mirrors) ---------------------------

  function stormCountLabel(storms) {
    // Mirrors _storm_count_label().
    var invests = 0;
    for (var i = 0; i < storms.length; i++) {
      if (storms[i].is_invest) invests++;
    }
    var tcs = storms.length - invests;
    if (invests === 0) return tcs + " Storms";
    var word = invests === 1 ? "Invest" : "Invests";
    return tcs + " Storms · " + invests + " " + word;
  }

  function buildStormCard(storm) {
    // SYNC: mirrors render_storm_card() byte-for-byte (parity-tested).
    var cat = storm.max_category !== undefined ? storm.max_category : "TD";
    var color = CFG.colors[cat] !== undefined ? CFG.colors[cat] : CFG.colors.TD;
    var label = String(cat).replace(/C/g, "Cat ");
    var isActive = storm.is_active;
    var isInvest = storm.is_invest;
    var isPtc = storm.is_ptc;
    var activeTag;
    if (isPtc) {
      activeTag = '<span class="storm-ptc">PTC</span>';
    } else if (isActive) {
      activeTag = '<span class="storm-active">Active</span>';
    } else if (isInvest) {
      activeTag = '<span class="storm-invest">Invest</span>';
    } else {
      activeTag = '';
    }
    var classes = "storm-card clickable";
    if (isPtc) classes += " ptc";
    else if (isActive) classes += " active";
    if (isInvest) classes += " invest";
    var peakWind = storm.peak_wind_kt;
    var peakPres = storm.peak_pressure_mb;
    var ace = storm.ace || 0;
    var sid = storm.sid || "";
    var hintText = isActive ? "Click for wind history" : "Click for peak intensity";
    var clickHint = '<div class="click-hint">▸ ' + hintText + '</div>';
    // SYNC: CycloLab entry link - active designated storms AND invests (Stage C:
    // invests have a grey/red-X CycloLab page). The data-name/data-accent feed the
    // pre-launch settings dialog (FG-R3 #3); the href is the no-JS / modified-click
    // fallback.
    var cyclolabBtn = (isActive && sid)
      ? '<a class="cyclolab-link" target="_blank" rel="noopener" href="/cyclolab/' + sid + '/" data-name="' + (storm.name || "UNNAMED") + '" data-accent="' + color + '">Open in CycloLab ▸</a>'
      : '';
    var placardSlot = '<div class="storm-placard" id="placard-' + sid + '" hidden></div>';
    return "\n" +
      '<div class="' + classes + '" id="card-' + sid + '" data-sid="' + sid + '">' + "\n" +
      '  <div class="storm-top">' + "\n" +
      '    <div class="storm-name">' + (storm.name || "UNNAMED") + activeTag + '</div>' + "\n" +
      '    <div class="storm-cat" style="background:' + color + '">' + label + '</div>' + "\n" +
      '  </div>' + "\n" +
      '  <div class="storm-meta">' + "\n" +
      '    <div class="row"><span class="lbl">Active</span><span class="val">' + fmtDateRange(storm.start, storm.end) + '</span></div>' + "\n" +
      '    <div class="row"><span class="lbl">Peak wind</span><span class="val">' + (peakWind != null ? pyNum(peakWind) : "-") + ' kt</span></div>' + "\n" +
      '    <div class="row"><span class="lbl">Peak pressure</span><span class="val">' + (peakPres != null ? pyNum(peakPres) : "-") + ' mb</span></div>' + "\n" +
      '    <div class="row"><span class="lbl">ACE</span><span class="val">' + pyFixed(ace, 2) + '</span></div>' + "\n" +
      '  </div>' + "\n" +
      '  ' + clickHint + "\n" +
      '  ' + cyclolabBtn + "\n" +
      '  ' + placardSlot + "\n" +
      '</div>' + "\n";
  }

  function buildCardsHtml(storms) {
    // SYNC: mirrors render_cards_html(). Cards are ordered strictly by
    // formation date (earliest first) across ALL storms — no active-pin,
    // no per-ACE ranking. Sort a COPY so the caller's storms array (which
    // still drives the map layers in feed order) is left untouched.
    var ordered = storms.slice().sort(function (a, b) {
      var sa = (a.start || ""), sb = (b.start || "");
      if (sa < sb) return -1;
      if (sa > sb) return 1;
      var ia = (a.sid || ""), ib = (b.sid || "");
      if (ia < ib) return -1;
      if (ia > ib) return 1;
      return 0;
    });
    var cards = [];
    for (var i = 0; i < ordered.length; i++) {
      cards.push(buildStormCard(ordered[i]));
    }
    return cards.join("\n") || ('<div class="storm-card"><div class="storm-meta">' +
                                'No storms yet this year.</div></div>');
  }

  function buildPanelTitle(storms, year) {
    // SYNC: mirrors render_panel_title_html().
    return year + " Season &middot; " + stormCountLabel(storms);
  }

  function buildStatsHtml(header, vocab) {
    // SYNC: mirrors render_stats_html().
    return '<b>' + header.named + '</b> ' + vocab.named + ' · ' +
           '<b>' + header.cat1plus + '</b> ' + vocab.cat1plus + ' · ' +
           '<b>' + header.cat5 + '</b> ' + vocab.cat5 + ' · ' +
           '<span class="ace">' + pyFixed(header.total_ace, 2) + ' ACE</span>';
  }

  // ---- DOM glue (browser only; node imports the builders for parity) -----

  function parseSvgGroup(markup) {
    var doc = new DOMParser().parseFromString(
      '<svg xmlns="http://www.w3.org/2000/svg">' + markup + '</svg>',
      "image/svg+xml");
    if (doc.querySelector("parsererror")) return null;
    var g = doc.documentElement.firstElementChild;
    return g ? document.importNode(g, true) : null;
  }

  function validateFeed(data) {
    if (!data || !Array.isArray(data.storms) || !data.header || !data.vocab) {
      throw new Error("feed shape unexpected");
    }
    if (data.basin !== CFG.basin) {
      throw new Error("feed basin '" + data.basin + "' != page basin '" + CFG.basin + "'");
    }
    if (data.year !== CFG.year) {
      // Season-rollover guard: never paint next year's storms onto this
      // page's baked "<year> Tracks" frame — wait for the cron rebuild.
      throw new Error("feed year " + data.year + " != page year " + CFG.year);
    }
  }

  // ---- Storm-card placard interactivity (click a card -> inline peak / active
  // wind-history chart). These live in the overlay (not the baked page) so they
  // survive applyLive()'s card redraw, and they read the live feed's storms (which
  // carry per-fix `points`) via LIVE_STORMS, keyed by sid. The click is EVENT-
  // DELEGATED on the stable #storms container, so one listener outlives every
  // innerHTML swap (the regression was per-card listeners wiped by the redraw). ----
  var LIVE_STORMS = {};
  var PLACARD_CAT_LABELS = {
    "TD": "Depression", "TS": "Tropical Storm", "C1": "Category 1",
    "C2": "Category 2", "C3": "Category 3", "C4": "Category 4", "C5": "Category 5"
  };
  function ktToMph(k) { return Math.round(k * 1.15077945); }
  function ktToMph5(k) { return Math.round(k * 1.15077945 / 5) * 5; }
  function ktToKmh(k) { return Math.round(k * 1.852); }
  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function plFmtTime(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    var m = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    var hh = String(d.getUTCHours()).padStart(2, "0");
    var mm = String(d.getUTCMinutes()).padStart(2, "0");
    return m[d.getUTCMonth()] + " " + d.getUTCDate() + ", " + hh + ":" + mm + "Z";
  }
  function plFmtLatLon(lat, lon) {
    while (lon > 180) lon -= 360;
    while (lon < -180) lon += 360;
    var la = Math.abs(lat).toFixed(1) + "° " + (lat >= 0 ? "N" : "S");
    var lo = Math.abs(lon).toFixed(1) + "° " + (lon >= 0 ? "E" : "W");
    return la + "   " + lo;
  }
  function plCompass(b) {
    var dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
    return dirs[Math.round(b / 22.5) % 16];
  }
  function plSshsLabel(cls) {
    if (cls === "TD") return "D";
    if (cls === "TS") return "S";
    return (cls || "").replace("C", "") || "D";
  }
  function plSpinner(color, cls) {
    var label = plSshsLabel(cls);
    return '<div class="placard-spinner">' +
      '<svg viewBox="-34 -34 68 68">' +
        '<g>' +
          '<path d="__LIVE_HURRICANE_PATH__" fill="' + color + '" ' +
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
  function plMovement(pts) {
    for (var i = pts.length - 2; i >= 0; i--) {
      var a = pts[i], b = pts[pts.length - 1];
      var ta = new Date(a.t).getTime(), tb = new Date(b.t).getTime();
      var dtH = (tb - ta) / 3600000;
      if (dtH < 1) continue;
      var latm = (b.lat - a.lat) * 60;
      var lonm = (b.lon - a.lon) * 60 * Math.cos((a.lat + b.lat) / 2 * Math.PI / 180);
      var distNm = Math.sqrt(latm * latm + lonm * lonm);
      if (distNm < 0.5) return "Nearly stationary";
      var speedKt = distNm / dtH;
      var bearing = (Math.atan2(lonm, latm) * 180 / Math.PI + 360) % 360;
      return plCompass(bearing) + " at " + ktToMph(speedKt) + " mph";
    }
    return "-";
  }
  function plBannerTextColor(cls) {
    return (cls === "TS" || cls === "C1" || cls === "C2") ? "#0a1324" : "#ffffff";
  }
  function renderWindChart(pts) {
    if (!pts.length) return '<div class="chart-empty">No wind observations yet.</div>';
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
      [0, 34, CFG.colors.TD], [34, 64, CFG.colors.TS], [64, 83, CFG.colors.C1],
      [83, 96, CFG.colors.C2], [96, 113, CFG.colors.C3], [113, 137, CFG.colors.C4],
      [137, maxWind, CFG.colors.C5]
    ];
    var bandRects = bands.map(function(b) {
      var lo = b[0], hi = b[1], c = b[2];
      var y1 = yScale(Math.min(hi, maxWind));
      var y2 = yScale(lo);
      return '<rect x="' + padL + '" y="' + y1 + '" width="' + plotW +
             '" height="' + (y2 - y1) + '" fill="' + c + '" fill-opacity="0.38"/>';
    }).join("");
    var pathD = "M " + pts.map(function(p) {
      return xScale(new Date(p.t).getTime()).toFixed(1) + "," + yScale(p.wind_kt || 0).toFixed(1);
    }).join(" L ");
    var dotsSvg = pts.map(function(p) {
      var x = xScale(new Date(p.t).getTime()).toFixed(1);
      var y = yScale(p.wind_kt || 0).toFixed(1);
      return '<circle cx="' + x + '" cy="' + y + '" r="3" fill="#0a1324" stroke="#ffffff" stroke-width="1.3"/>';
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
      '<svg class="wind-chart" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet">' +
        '<rect x="' + padL + '" y="' + padT + '" width="' + plotW + '" height="' + plotH + '" fill="#07101c"/>' +
        bandRects + yLabels + xLabels +
        '<rect x="' + padL + '" y="' + padT + '" width="' + plotW + '" height="' + plotH + '" fill="none" stroke="#243452"/>' +
        '<path d="' + pathD + '" fill="none" stroke="#ffffff" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>' +
        dotsSvg +
      '</svg>'
    );
  }
  function renderPeakPlacard(storm) {
    if (!storm) return '<div class="chart-empty">No data.</div>';
    var pts = (storm.points || []).slice();
    var validPts = pts.filter(function(p) { return p.wind_kt != null; });
    if (!validPts.length) return '<div class="chart-empty">No wind observations.</div>';
    var peak = validPts[0];
    for (var i = 1; i < validPts.length; i++) {
      if (validPts[i].wind_kt > peak.wind_kt) peak = validPts[i];
    }
    var minPres = null;
    validPts.forEach(function(p) {
      if (p.pressure_mb != null && (minPres == null || p.pressure_mb < minPres)) minPres = p.pressure_mb;
    });
    var cls = peak.cls || "TD";
    var color = CFG.colors[cls] || "#888";
    var catLabel = PLACARD_CAT_LABELS[cls] || cls;
    var windKt = peak.wind_kt;
    var loc = plFmtLatLon(peak.lat, peak.lon);
    var chart = renderWindChart(validPts);
    var txtColor = plBannerTextColor(cls);
    return (
      '<div class="placard-banner" style="background:' + color + ';color:' + txtColor + '">' +
        plSpinner(color, cls) +
        '<div class="pl-row1"><span class="pl-cat">PEAK · ' + catLabel + '</span><b>' +
          escapeHtml(storm.name || "UNNAMED") + '</b></div>' +
        '<div class="pl-intensity">' +
          '<div class="pl-big">' + ktToMph5(windKt) + '</div>' +
          '<div class="pl-units">mph<br>' + ktToKmh(windKt) + ' km/h</div>' +
        '</div>' +
        '<div class="pl-deets">' +
          '<div><span>Reached</span><b>' + plFmtTime(peak.t) + '</b></div>' +
          '<div><span>Location</span><b>' + loc + '</b></div>' +
          '<div><span>Min pressure</span><b>' + (minPres ? Math.round(minPres) + " mb" : "-") + '</b></div>' +
          '<div><span>ACE</span><b>' + (storm.ace != null ? storm.ace.toFixed(2) : "-") + '</b></div>' +
        '</div>' +
      '</div>' +
      '<div class="placard-chart-label">Wind history</div>' +
      chart
    );
  }
  function renderActiveInline(storm) {
    var pts = (storm.points || []).filter(function(p) { return p.wind_kt != null; });
    return '<div class="placard-chart-label">Wind history</div>' + renderWindChart(pts);
  }
  function openInline(sid) {
    var el = document.getElementById("placard-" + sid);
    var card = document.getElementById("card-" + sid);
    var s = LIVE_STORMS[sid];
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
    var s = LIVE_STORMS[sid];
    if (!el || !s) return;
    if (!el.dataset.rendered) {
      el.innerHTML = s.is_active ? renderActiveInline(s) : renderPeakPlacard(s);
      el.dataset.rendered = "1";
    }
    var nowOpen = el.hidden;
    el.hidden = !el.hidden;
    if (card) card.classList.toggle("open", nowOpen);
    if (nowOpen && card) card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  // ONE delegated listener on the stable #storms container survives every
  // applyLive() innerHTML swap; the active-icon listener is delegated on document
  // because the active SVG layer is replaced wholesale on each redraw.
  function wireCardInteractivity() {
    if (typeof document === "undefined") return;
    var host = document.getElementById("storms");
    if (host && !host.dataset.clickWired) {
      host.dataset.clickWired = "1";
      host.addEventListener("click", function(ev) {
        if (ev.target.closest("a")) return;          // CycloLab link still navigates
        var card = ev.target.closest(".storm-card.clickable");
        if (card && card.dataset.sid) toggleInline(card.dataset.sid);
      });
    }
    if (document.body && !document.body.dataset.iconWired) {
      document.body.dataset.iconWired = "1";
      document.addEventListener("click", function(e) {
        var g = e.target.closest(".active-icon");
        if (g && g.dataset.sid) { e.stopPropagation(); openInline(g.dataset.sid); }
      });
    }
  }

  function applyLive(data) {
    var tracksEl = document.querySelector("#chart > g.tracks");
    var activeEl = document.querySelector("#chart > g.active-storms");
    var statsEl = document.getElementById("season-stats");
    var titleEl = document.getElementById("panel-title");
    var listEl = document.getElementById("storms");
    if (!tracksEl || !activeEl || !statsEl || !titleEl || !listEl) {
      throw new Error("expected page elements missing");
    }
    // Re-index the live storms (with per-fix points) so a card click expands the
    // CURRENT intensity/peak, not a stale baked copy.
    LIVE_STORMS = {};
    (data.storms || []).forEach(function(s) { if (s && s.sid) LIVE_STORMS[s.sid] = s; });
    // Build + parse EVERYTHING before touching the DOM so a failure
    // anywhere leaves the baked render fully intact (atomic swap).
    var freshTracks = parseSvgGroup(buildTracksSvg(data.storms));
    var freshActive = parseSvgGroup(buildActiveSvg(data.storms));
    if (!freshTracks || !freshActive) {
      throw new Error("SVG fragment parse failed");
    }
    var statsHtml = buildStatsHtml(data.header, data.vocab);
    var titleHtml = buildPanelTitle(data.storms, data.year);
    var cardsHtml = buildCardsHtml(data.storms);
    tracksEl.parentNode.replaceChild(freshTracks, tracksEl);
    activeEl.parentNode.replaceChild(freshActive, activeEl);
    statsEl.innerHTML = statsHtml;
    titleEl.innerHTML = titleHtml;
    listEl.innerHTML = cardsHtml;
    if (data.updated) {
      // Same contract as the global MapLibre page: surface the feed's
      // TRUE freshness; on any failure the baked build-time text stands.
      var asOf = document.getElementById("as-of");
      if (asOf) asOf.textContent = "As of " + data.updated;
    }
  }

  if (typeof window !== "undefined" && typeof document !== "undefined") {
    // Wire the (delegated) card-click handler immediately on the baked DOM so
    // clicks work the moment the cards exist; LIVE_STORMS fills in on first fetch.
    wireCardInteractivity();
    try {
      // Same fetch discipline as active-banner.js: cache-bust + no-store
      // so a freshly poller-written R2 object is always picked up.
      fetch(CFG.feedUrl + "?t=" + Date.now(), { cache: "no-store" })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (data) {
          validateFeed(data);
          applyLive(data);
        })
        .catch(function (e) {
          console.warn("[live-tracks] keeping baked render:", e);
        });
    } catch (e) {
      console.warn("[live-tracks] keeping baked render:", e);
    }
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      CFG: CFG,
      project: project,
      markerType: markerType,
      buildTracksSvg: buildTracksSvg,
      buildActiveSvg: buildActiveSvg,
      buildStormCard: buildStormCard,
      buildCardsHtml: buildCardsHtml,
      buildPanelTitle: buildPanelTitle,
      buildStatsHtml: buildStatsHtml,
      pyFixed: pyFixed,
      pyNum: pyNum
    };
  }
})();
"""


def build_live_overlay_js(basin: str, year: int) -> str:
    """Render the per-basin live-overlay <script> for HTML_TEMPLATE's
    {interactive_js} slot. The __ICON_*__ geometry tokens are left in
    place — _apply_icon_tokens() substitutes them on the whole page at
    write time, so icon sizing keeps its one canonical definition."""
    cfg = BASINS[basin]
    js = (LIVE_BASIN_JS
          .replace("__LIVE_BASIN__", basin)
          .replace("__LIVE_YEAR__", str(year))
          .replace("__LIVE_EXTENT__", json.dumps(list(cfg["extent"])))
          .replace("__LIVE_MAP_W__", str(MAP_W))
          .replace("__LIVE_MAP_H__", str(MAP_H))
          .replace("__LIVE_FEED_URL__", f"{FEEDS_BASE_URL}{basin}_tracks_data.json")
          .replace("__LIVE_SSHS_COLORS__", json.dumps(SSHS_COLORS))
          .replace("__LIVE_HURRICANE_PATH__", HURRICANE_PATH))
    return f"<script>\n{js}\n</script>"


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
    --c1: #ffe14d; --c2: #ff9a2f; --c3: #f5333c;
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
  /* Red-X glyph for the invest-position legend row — mirrors the
     current-position X every invest carries (#ff2a2a, round caps). */
  .legend .investx { width: 10px; height: 10px; position: relative; }
  .legend .investx::before, .legend .investx::after { content: "";
    position: absolute; left: -1px; right: -1px; top: 4px; height: 2.4px;
    border-radius: 2px; background: #ff2a2a; }
  .legend .investx::before { transform: rotate(45deg); }
  .legend .investx::after { transform: rotate(-45deg); }
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
     Two flavours match the per-basin SVG renderer:
       * .invest-x-marker   — EVERY recent invest, active or not (small
                              red glowing X with red side-label, the NHC
                              invest-area convention). Per-basin
                              reference: render_tracks_svg
                              invest_current_positions. (The old
                              active-invest big-L marker is retired.)
       * .active-hurricane  — EVERY active designated storm (spinning
                              hurricane glyph with current-stage letter
                              inside + name beside — a current TD wears a
                              blue "D" glyph). Per-basin reference:
                              render_active_icons. (The old .active-td
                              hollow ring is retired — it keyed on PEAK
                              wind, so same-stage storms could wear
                              different markers.)
     Sizing here is in *unzoomed* CSS pixels — MapLibre keeps marker
     elements at constant pixel size while the map zooms, exactly like
     the per-basin SVG when its viewBox stays put.
     ANCHORING INVARIANT: every marker element is anchored at its CENTER
     (maplibregl.Marker anchor:"center"), so each flavour's SVG viewBox
     must be SYMMETRIC about (0,0) with the glyph centered there — side
     labels hang outside the box via overflow:visible and must never be
     given room inside the viewBox (that's what mis-anchored the invest
     X 24px west of its fix). tests/test_invest_x_anchor.py pins this. */
  .active-marker { position: absolute; transform: translate(-50%, -50%);
    pointer-events: none; }
  /* entry-flow: the active-hurricane glyph is wrapped in an <a target=_blank>
     to /cyclolab/{sid}/ - the anchor must fill the marker so the whole glyph
     is the click target (native new-tab, no JS). */
  .active-marker a { display: block; width: 100%; height: 100%;
    cursor: pointer; }
  .active-marker svg { display: block; overflow: visible;
    width: 100%; height: 100%; }

  /* Hurricane spinner */
  .active-marker.active-hurricane { width: __ICON_HBOX__px; height: __ICON_HBOX__px; }
  .active-marker.active-hurricane svg {
    filter: drop-shadow(0 0 6px currentColor); }
  @keyframes tat-spin { from { transform: rotate(360deg); }
                        to   { transform: rotate(0deg); } }
  .active-marker .spinning {
    animation: tat-spin 2.6s linear infinite;
    transform-origin: 50% 50%; transform-box: fill-box; }
  .active-marker .hurricane-label { font-size: __ICON_LETTER_PT__px; font-weight: 900;
    fill: #ffffff; paint-order: stroke;
    stroke: rgba(0,0,0,0.55); stroke-width: 1.8;
    stroke-linejoin: round; }
  .active-marker .hurricane-name { fill: #f1f7fd; font-size: __ICON_NAME_PT__px;
    font-weight: 700; paint-order: stroke;
    stroke: #07101c; stroke-width: 3; stroke-linejoin: round; }

  /* Recent invest "X" (active or not) — text/path styling lifted
     verbatim from render_tracks_svg invest_current_positions (path
     stroke #ff2a2a / width 2.4) and the .invest-label CSS rule
     (#ff5050 / 12px / weight 700 / dark stroke 3). The box is the X
     glyph ONLY (32×32, symmetric about the crosshair centre = the
     anchored fix); the designation label starts at x=11 and overflows
     the box to the right, exactly like .hurricane-name does — so label
     width can never shift the X off the fix. */
  .active-marker.invest-x-marker { width: 32px; height: 32px; }
  .active-marker .invest-label { fill: #ff5050; font-size: __ICON_NAME_PT__px;
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
        <div class="item"><span class="investx"></span>Invest position</div>
      </div>
      <div class="zoom-hint">Drag to pan &middot; Ctrl+scroll to zoom &middot; Double-click to reset</div>
      <div class="brand-wm">@WeathermanAAA_</div>
    </div>
  </div>
</div>

<script>
(function () {
  var SSHS_COLORS = {
    "TD": "#3fa4ff", "TS": "#46c56a", "C1": "#ffe14d",
    "C2": "#ff9a2f", "C3": "#f5333c", "C4": "#e33ad4", "C5": "#b03bff"
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
    attributionControl: false,
    // MOBILE: one-finger swipes scroll the PAGE (this map is embedded
    // in the tall homepage via iframe); two-finger pan / pinch works
    // the map, with MapLibre's built-in "use two fingers" hint. On
    // desktop, ctrl/cmd+scroll zooms - plain wheel scrolls the page.
    cooperativeGestures: true
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
    // Splitting by is_invest in the layer filter is the standard fix. A PTC
    // (is_ptc) wears the invest identity, so it joins the dashed layer and is
    // excluded from the solid layer — the same uncertain/pre-genesis grouping
    // as the per-basin dash_attr fork.
    map.addLayer({
      id: "tracks-line-solid",
      type: "line",
      source: "storms",
      filter: ["all",
        ["==", ["geometry-type"], "LineString"],
        ["!=", ["get", "is_invest"], true],
        ["!=", ["get", "is_ptc"], true]
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
        ["any",
          ["==", ["get", "is_invest"], true],
          ["==", ["get", "is_ptc"], true]
        ]
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
      96,  "#f5333c",  // 96-112: C3
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
    // INFO-ONLY popup (entry-flow rewrite): the CycloLab entry is the marker
    // glyph itself (a native <a target="_blank">), so the hover popup carries
    // NO interactive elements - just name / category / wind / pressure / last
    // fix. This kills the hover-race glitch structurally.
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

      // DEFENSIVE number gate (Andrew's 2026-07-14 marker rule): the ATCF
      // NUMBER decides the X, not the feed's marker_type — a geojson written
      // by a pre-0.8.5 ace_core (the box poller until its next rebuild)
      // still stamps designated PTCs (TD 05E) as invest_x. 90-99 = invest X;
      // a parseable 01-89 designation renders by intensity REGARDLESS of the
      // stamped type; no parseable number falls back to marker_type.
      var investLike = (function () {
        var m = /^(\d{1,2})[A-Z]$/.exec(designation);
        if (m) return parseInt(m[1], 10) >= 90;
        var s = /^[A-Z]+_[A-Z]{2}(\d{2})\d{4}$/.exec(
          String(props.storm_id || "").toUpperCase());
        if (s) return parseInt(s[1], 10) >= 90;
        return props.marker_type === "invest_x" || props.marker_type === "L";
      })();

      if (investLike) {
        // EVERY recent invest, active or not (the NHC invest-area X).
        // Per-basin's render_tracks_svg invest_current_positions emits a
        // small red glowing X (path "M -7 -7 L 7 7 M -7 7 L 7 -7") at
        // stroke #ff2a2a / 2.4 width, with a red invest-label to the
        // right (offset +11 +4 from the X centre). Replicate verbatim.
        // LEGACY: "L" was the old active-invest type; a geojson written
        // by a pre-0.4.0 ace_core (poller repin gap, cached object)
        // still carries it — render it as the unified X.
        el.classList.add("invest-x-marker");
        var fid = "invest-red-glow-" + (++investGlowSeq);
        // ANCHOR: the viewBox is symmetric about (0,0) — the X crosshair
        // centre — so anchor:"center" puts the crosshair EXACTLY on the
        // fix. The label is an offset sibling (<text x="11">) that
        // overflows the box via overflow:visible; giving it room inside
        // the viewBox (the old "-22 -16 92 32") shifted the X 24px west
        // of the fix by exactly half the label allowance.
        el.innerHTML =
          '<a href="/cyclolab/' + encodeURIComponent(props.storm_id) +
            '/" target="_blank" rel="noopener">' +
          '<svg viewBox="-16 -16 32 32" xmlns="http://www.w3.org/2000/svg">' +
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
          '</svg></a>';
      } else {
        // EVERY active designated storm — spinning hurricane glyph;
        // current_category picks the letter/color (a current TD wears a
        // blue "D" glyph, same as a weakened ex-TS at TD strength).
        // LEGACY: "td_circle" was the old peak<34 hollow-ring type; a
        // geojson written by a pre-0.5.0 ace_core (poller repin gap,
        // cached object) still carries it — render it as the unified
        // glyph, which the same feature's current_category fully
        // describes.
        el.classList.add("active-hurricane");
        var cls = props.current_category || "TD";
        var color = SSHS_COLORS[cls] || "#888";
        var label = sshsLabel(cls);
        el.style.color = color;  // drop-shadow inherits via currentColor
        el.innerHTML =
          '<a href="/cyclolab/' + encodeURIComponent(props.storm_id) +
            '/" target="_blank" rel="noopener">' +
          '<svg viewBox="-34 -34 __ICON_HBOX__ __ICON_HBOX__" xmlns="http://www.w3.org/2000/svg">' +
            '<g transform="scale(__ICON_GLYPH_SCALE__)">' +
              '<g class="spinning">' +
                '<path d="__HURRICANE_PATH__" fill="' + color + '" />' +
              '</g>' +
            '</g>' +
            '<text class="hurricane-label" x="0" y="0" ' +
              'text-anchor="middle" dominant-baseline="central">' +
              label + '</text>' +
            '<text class="hurricane-name" x="__ICON_NAME_X__" y="__ICON_NAME_Y__" ' +
              'text-anchor="start">' + escapeHtml(props.name || "") + '</text>' +
          '</svg></a>';
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
      // Click is the native <a target="_blank"> wrapping the glyph AND the
      // invest X (Stage C: invests now launch their grey/red-X CycloLab page) -
      // so NO click handler here. Hover shows the INFO-ONLY popup; nothing
      // interactive lives in it (no hover-race).
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
    # SYNC: mirrored line-for-line by LIVE_BASIN_JS buildStormCard() —
    # tests/test_live_overlay_parity.py asserts byte-identical output.
    # Change BOTH or the live page will drift from the baked fallback.
    cat = storm.get("max_category", "TD")
    color, label = _cat_style(cat)
    is_active = storm.get("is_active")
    is_invest = storm.get("is_invest")
    is_ptc = storm.get("is_ptc")
    # A PTC wears the grey identity (checked FIRST — it is is_active too): a
    # "PTC" tag, not "Active", and the grey 'ptc' card class hides the TD chip.
    # Active TCs get the "Active" tag (also gets the spinning map icon);
    # invests that aren't active TCs get an "INVEST" tag instead. The
    # two are mutually exclusive — an invest that briefly hit 34 kt would
    # show "Active", which is correct since the b-deck would still call
    # it 91W until JTWC/NHC numbers it.
    if is_ptc:
        active_tag = '<span class="storm-ptc">PTC</span>'
    elif is_active:
        active_tag = '<span class="storm-active">Active</span>'
    elif is_invest:
        active_tag = '<span class="storm-invest">Invest</span>'
    else:
        active_tag = ''
    # Every card is clickable — active cards open the pinned live placard
    # at the top; inactive cards expand an inline peak-intensity placard.
    classes = "storm-card clickable"
    if is_ptc:
        classes += " ptc"
    elif is_active:
        classes += " active"
    if is_invest:
        classes += " invest"
    peak_wind = storm.get("peak_wind_kt")
    peak_pres = storm.get("peak_pressure_mb")
    ace = storm.get("ace") or 0
    sid = storm.get("sid") or ""
    hint_text = "Click for wind history" if is_active else "Click for peak intensity"
    click_hint = f'<div class="click-hint">▸ {hint_text}</div>'
    # CycloLab entry (Stage 5): ACTIVE designated storms AND invests (Stage C:
    # invests now have a grey/red-X CycloLab page). A REAL link - it works even
    # where card click handlers are absent, and the card handler skips <a> clicks
    # so the placard toggle never swallows navigation.
    cyclolab_btn = (f'<a class="cyclolab-link" target="_blank" rel="noopener" '
                    f'href="/cyclolab/{sid}/" '
                    f'data-name="{storm.get("name") or "UNNAMED"}" '
                    f'data-accent="{color}">Open in CycloLab ▸</a>'
                    if (is_active and sid) else '')
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
  {cyclolab_btn}
  {placard_slot}
</div>
"""


def render_stats_html(header: dict, vocab: dict) -> str:
    """Season-stats line (innerHTML of #season-stats in HTML_TEMPLATE).
    SYNC: mirrored by LIVE_BASIN_JS buildStatsHtml() — parity-tested,
    change both."""
    return (f'<b>{header["named"]}</b> {vocab["named"]} · '
            f'<b>{header["cat1plus"]}</b> {vocab["cat1plus"]} · '
            f'<b>{header["cat5"]}</b> {vocab["cat5"]} · '
            f'<span class="ace">{header["total_ace"]:.2f} ACE</span>')


def render_cards_html(storms: list[dict]) -> str:
    """Storm-card list (innerHTML of #storms). Cards are ordered strictly
    by formation date (earliest first) across ALL storms — active systems
    are NOT pinned to the top, and there is no per-ACE ranking. SYNC:
    mirrored by LIVE_BASIN_JS buildCardsHtml() — parity-tested, change both.
    (The feed's storms array carries its own ace_core ordering; card order
    is owned here so it stays chronological regardless of feed order.)"""
    ordered = sorted(storms, key=lambda s: ((s.get("start") or ""),
                                            (s.get("sid") or "")))
    return "\n".join(render_storm_card(s) for s in ordered) or (
        '<div class="storm-card"><div class="storm-meta">'
        'No storms yet this year.</div></div>'
    )


def render_panel_title_html(storms: list[dict], year: int) -> str:
    """Side-panel title (innerHTML of #panel-title). SYNC: mirrored by
    LIVE_BASIN_JS buildPanelTitle() — parity-tested, change both."""
    return f'{year} Season &middot; {_storm_count_label(storms)}'


def render_html(payload: dict, extent, countries_geojson, coastline_geojson) -> str:
    """Render a per-basin static SVG tracks page. Global mode is no longer
    routed here — see render_global_maplibre_html for the MapLibre path.

    The page is still a fully-baked static SVG (the no-JS / fetch-fail
    fallback), but it now ships LIVE_BASIN_JS in the {interactive_js}
    slot: at view time the overlay refetches the live tracks feed from R2
    and atomically redraws every storm-derived fragment, demoting this
    baked render to the backup. See LIVE_BASIN_JS for the contract."""
    map_w = MAP_W
    map_h = MAP_H
    basemap_svg = render_basemap_svg(extent, countries_geojson, coastline_geojson,
                                     map_w, map_h)
    tracks_svg = render_tracks_svg(payload["storms"], extent, map_w, map_h)
    active_svg = render_active_icons(payload["storms"], extent, map_w, map_h)

    side_panel_html = (
        f'<div class="side">'
        f'<div class="panel-title" id="panel-title">'
        f'{render_panel_title_html(payload["storms"], payload["year"])}</div>'
        f'<div class="storm-list" id="storms">'
        f'{render_cards_html(payload["storms"])}'
        f'</div>'
        f'</div>'
    )

    header = payload["header"]
    vocab = payload["vocab"]
    return HTML_TEMPLATE.format(
        basin_name=payload["basin_name"],
        year=payload["year"],
        updated=payload["updated"],
        stats_html=render_stats_html(header, vocab),
        map_w=map_w, map_h=map_h,
        defs=SVG_DEFS,
        basemap_svg=basemap_svg,
        tracks_svg=tracks_svg,
        active_svg=active_svg,
        wm_x=map_w - 20, wm_y=40,
        side_panel_html=side_panel_html,
        zoom_hint_html="",
        interactive_js=build_live_overlay_js(payload["basin"], payload["year"]),
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
        html = _apply_icon_tokens(render_global_maplibre_html(payload))
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
    nhc_active_sids = None
    if FETCH_LIVE and not args.no_live:
        print(f"{log} attempting live {basin_cfg['agency_name']} fetch for {year} ...")
        live_frame = fetch_live_season(year, basin_cfg, log)
        # CurrentStorms = NHC's authoritative active list, enabling the prompt
        # final-advisory retirement of is_active (status only; tracks + ACE
        # identical). None on fetch failure -> no retirement this build.
        nhc_active_sids = ace_fetch_nhc_active_sids()

    storms = merge_and_extract_storms(ibtracs_frame, live_frame, basin_cfg,
                                      nhc_active_sids=nhc_active_sids)
    # Hardening: a system present in a LIVE source (knackwx / b-deck) but absent
    # from the published feed = the next discovery crack. Log it loudly. A
    # handoff legitimately retires the prior 9x invest once its designation
    # appears (spawn_invest), so suppress those to avoid a false WARN.
    if not live_frame.empty and "SID" in live_frame.columns:
        feed_sids = {s.get("sid") for s in storms}
        superseded = set()
        if "spawn_invest" in live_frame.columns:
            for n in live_frame["spawn_invest"].dropna().unique():
                superseded.add(f"{basin_cfg['agency_name']}_"
                               f"{basin_cfg['short'].upper()}{int(n):02d}{year}")
        missing = (set(live_frame["SID"]) - feed_sids) - superseded
        if missing:
            print(f"{log} WARN: {len(missing)} system(s) present in a live "
                  f"source but absent from the published feed: "
                  f"{sorted(missing)}", file=sys.stderr)
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

        html = _apply_icon_tokens(render_html(payload, basin_cfg["extent"], countries, coast))
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
