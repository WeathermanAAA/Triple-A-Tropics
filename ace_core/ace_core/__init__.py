#!/usr/bin/env python3
"""
ace_core.py
-----------
SINGLE SOURCE OF TRUTH for Accumulated Cyclone Energy (ACE) across the site.

Before this module, ACE was computed twice by unrelated scripts:
``generate_ace_plot.py`` (homepage strip + climatology page) and
``generate_tracks_plot.py`` (tracks graphic) each had their own ACE loop, their
own live-vs-IBTrACS storm merge, and their own rounding. They drifted: the same
WPAC season read 49.672 in one feed and 49.71 in the other, and Sinlaku 2026
showed 154 kt / 39.935 ACE in one and 160 kt / 39.97 in the other.

Everything that determines an ACE number now lives HERE, and both generators
import it. Neither recomputes ACE on its own:

  - WIND PREFERENCE (per basin): USA_WIND (1-min) x1.0, falling back to
    WMO_WIND / TOKYO_WIND (10-min) divided by 0.88. The JTWC 1-minute basis is
    kept on purpose - it already matches CSU.
  - FORMULA: sum(v_kt^2 / 10000) over 6-hourly synoptic fixes (00/06/12/18 UTC,
    minute == 0) with v >= 34 kt.
  - PER-BASIN NATURE ELIGIBILITY (``ACE_NATURES``): defined in ONE place. AL/EP
    (NHC) and WP (JTWC) all count tropical AND subtropical, matching CSU. (WP was
    previously tropical-only; subtropical was added here.)
  - ONE ROUNDING POLICY: each storm's ACE is rounded to ``ACE_DECIMALS`` (3) and
    the season total is the sum of those rounded per-storm values. So season ACE
    == sum of per-storm ACE BY CONSTRUCTION - no round-then-sum drift.
  - ONE LIVE b-deck PARSER (``parse_bdeck``) and ONE IBTrACS-vs-live storm MERGE
    (``merge_named_sources``), so both generators build the IDENTICAL canonical
    track set for every storm (same peak wind, same ACE).

Public surface used by the generators:
    ACE_NATURES, WIND_PREFERENCE, STATUS_TO_NATURE, SIX_HOURLY, ACE_DECIMALS
    best_wind(row, basin)
    storm_ace(points, basin, provisional=True)      # rounded per-storm ACE
    season_ace(storm_aces)                           # sum of rounded per-storm
    canonical_peak_wind(points)                      # one peak-wind definition
    parse_bdeck(text, season, basin_cfg)             # shared ATCF b-deck parser
    merge_named_sources(ib_df, live_df, name_col)    # shared storm merge
    iso_z(t) / now_iso_z() / latest_fix_iso(times) / staleness_minutes(...)
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants / per-basin ACE config (the ONE place these live)
# ---------------------------------------------------------------------------

SIX_HOURLY = {0, 6, 12, 18}

# Per-storm ACE is rounded to this many decimals; the season total is the sum of
# the rounded per-storm values, so the two always agree.
ACE_DECIMALS = 3

# 10-minute -> 1-minute sustained-wind conversion (CSU/JTWC convention).
TEN_MIN_TO_ONE_MIN = 1.0 / 0.88

# Per-basin ACE-eligible NATURE codes - the single definition. AL/EP (NHC) count
# tropical (TS) + subtropical (SS); WP (JTWC) now does too (TS + SS + SD), to
# match CSU, which counts tropical AND subtropical. WP was tropical-only before.
ACE_NATURES: dict[str, set[str]] = {
    "wp": {"TS", "SS", "SD"},
    "al": {"TS", "SS"},
    "ep": {"TS", "SS"},
}

# Per-basin wind-column preference: (column, multiplier). USA_WIND is 1-min so
# x1.0; WMO/Tokyo are 10-min so /0.88. Single source for both generators.
WIND_PREFERENCE: dict[str, list[tuple[str, float]]] = {
    "wp": [("USA_WIND", 1.0), ("WMO_WIND", TEN_MIN_TO_ONE_MIN),
           ("TOKYO_WIND", TEN_MIN_TO_ONE_MIN)],
    "al": [("USA_WIND", 1.0), ("WMO_WIND", TEN_MIN_TO_ONE_MIN)],
    "ep": [("USA_WIND", 1.0), ("WMO_WIND", TEN_MIN_TO_ONE_MIN)],
}

# ATCF dev-level (STATUS) -> IBTrACS-style NATURE. Tropical codes collapse to
# "TS", subtropical to "SS", extratropical to "ET", pre-/non-cyclone to "DS".
STATUS_TO_NATURE: dict[str, str] = {
    # Tropical
    "TD": "TS", "TS": "TS", "TY": "TS", "HU": "TS",
    "ST": "TS", "STY": "TS", "TC": "TS",
    # Subtropical
    "SD": "SS", "SS": "SS",
    # Extratropical / post-tropical
    "EX": "ET", "PT": "ET",
    # Pre-TC / non-cyclone
    "DB": "DS", "LO": "DS", "WV": "DS", "MD": "DS",
    "DS": "DS", "IN": "DS",
}

_PLACEHOLDER_NAMES = {"", "UNNAMED", "INVEST", "NAMELESS"}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _is_nan(v) -> bool:
    return isinstance(v, float) and math.isnan(v)


def round_ace(x: float) -> float:
    """The single ACE rounding policy."""
    return round(float(x), ACE_DECIMALS)


def is_six_hourly(t: Optional[dt.datetime]) -> bool:
    """Standard ACE convention: 00/06/12/18 UTC, on the synoptic hour."""
    return (t is not None and getattr(t, "hour", None) in SIX_HOURLY
            and getattr(t, "minute", 0) == 0)


def fix_increment(wind_kt: float) -> float:
    """A single 6-hourly fix's ACE contribution (kt^2 / 10000)."""
    return (float(wind_kt) ** 2) / 10_000.0


def best_wind(row, basin: str) -> float:
    """First non-null wind in the basin's preference list, times its factor.
    ``row`` is any object with ``.get(col)`` (e.g. a pandas Series)."""
    for col, factor in WIND_PREFERENCE[basin]:
        v = row.get(col)
        if v is not None and not _is_nan(v):
            try:
                return float(v) * factor
            except (TypeError, ValueError):
                continue
    return float("nan")


def nature_eligible(nature: Optional[str], basin: str,
                    provisional: bool = True) -> bool:
    """True if a fix's NATURE counts toward ACE for the basin. On provisional /
    current-season data we also accept "NR" and blank (IBTrACS backfills NATURE
    only after post-season QC), so a 34 kt+ provisional fix is not silently
    dropped. The per-basin eligible set is ``ACE_NATURES``."""
    n = (nature or "").strip().upper()
    natset = ACE_NATURES[basin]
    if n in natset:
        return True
    if provisional and n in (natset | {"NR", ""}):
        return True
    return False


def _unpack(point) -> tuple:
    """Return (time, wind_kt, ace_nature) from a fix dict. ACE nature is read
    from 'ace_nature' if present, else 'nature'."""
    t = point.get("time")
    w = point.get("wind_kt")
    nat = point.get("ace_nature", point.get("nature"))
    return t, w, nat


def fix_ace_eligible(time, wind_kt, nature, basin: str,
                     provisional: bool = True) -> bool:
    """A fix contributes to ACE iff it is a 6-hourly synoptic fix, wind >= 34 kt,
    and its NATURE is ACE-eligible for the basin."""
    if not is_six_hourly(time):
        return False
    if wind_kt is None or _is_nan(wind_kt) or float(wind_kt) < 34:
        return False
    return nature_eligible(nature, basin, provisional)


def storm_ace(points: Iterable[dict], basin: str,
              provisional: bool = True) -> float:
    """The ACE of ONE storm: sum of eligible 6-hourly fix increments, rounded by
    the single policy. ``points`` is an iterable of fix dicts carrying ``time``,
    ``wind_kt`` and an ACE nature (``ace_nature`` or ``nature``)."""
    total = 0.0
    for p in points:
        t, w, nat = _unpack(p)
        if fix_ace_eligible(t, w, nat, basin, provisional):
            total += fix_increment(w)
    return round_ace(total)


def season_ace(storm_aces: Iterable[float]) -> float:
    """Season ACE = sum of the (already rounded) per-storm ACE values. Equal to
    'sum of per-storm ACE' by construction."""
    return round_ace(sum(storm_aces))


def canonical_peak_wind(points: Iterable[dict]) -> float:
    """The ONE peak-wind definition both feeds report: the maximum wind over a
    storm's 6-hourly synoptic fixes with a valid wind (no nature / 34 kt gate, so
    it is the storm's peak intensity). NaN if the storm has no valid fix."""
    peak = float("nan")
    for p in points:
        t, w, _ = _unpack(p)
        if is_six_hourly(t) and w is not None and not _is_nan(w):
            if math.isnan(peak) or float(w) > peak:
                peak = float(w)
    return peak


# ---------------------------------------------------------------------------
# Shared ATCF b-deck parser (one parser for both generators)
# ---------------------------------------------------------------------------

def _parse_atcf_latlon(lat_raw: str, lon_raw: str) -> Optional[tuple]:
    """ATCF format: '157N' -> 15.7 degN, '1234W' -> -123.4 deg."""
    def _one(raw: str, pos: str, neg: str) -> Optional[float]:
        raw = (raw or "").strip()
        if not raw or raw[-1] not in (pos, neg):
            return None
        try:
            val = int(raw[:-1]) / 10.0
        except ValueError:
            return None
        return -val if raw[-1] == neg else val

    lat = _one(lat_raw, "N", "S")
    lon = _one(lon_raw, "E", "W")
    if lat is None or lon is None:
        return None
    return lat, lon


def parse_bdeck(text: str, season: int, basin_cfg: dict):
    """Parse an ATCF b-deck file into a DataFrame of 6-hourly fixes - the SINGLE
    parser both generators use, so a named storm has the same fix set (hence the
    same peak wind + ACE) everywhere.

    Columns: SID, NAME, season, time, lat, lon, wind_kt, pressure_mb, nature,
    ace_nature, source, storm_num. ``nature``/``ace_nature`` come from the ATCF
    dev-level via ``STATUS_TO_NATURE`` (wind-based fallback for unmapped codes).
    Multiple wind-radius rows per obs (34/50/64 kt) are deduped to one fix per
    (storm, timestamp); 50/64 kt radius lines are skipped.
    """
    import pandas as pd

    rows = []
    name_by_storm: dict[int, str] = {}
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
        # Accept pre-radii lines ("" / "0") and the 34 kt row; skip 50/64 kt
        # radius duplicates of the same observation.
        if rad not in ("", "0", "34"):
            continue
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
        devlvl_u = (devlvl or "").strip().upper()
        nature = STATUS_TO_NATURE.get(devlvl_u, "")
        if not nature:
            nature = "TS" if (vmax and not _is_nan(vmax_f) and vmax_f > 0) else "DS"
        if storm_num >= 90:
            fallback_name = f"{storm_num}{basin_cfg.get('invest_letter', '')}"
        else:
            fallback_name = f"#{storm_num:02d}"
        rows.append({
            "SID": f"{basin_cfg['agency_name']}_{basin_cfg['short'].upper()}"
                   f"{storm_num:02d}{season}",
            "NAME": name_by_storm.get(storm_num, fallback_name),
            "season": season,
            "time": t,
            "lat": lat,
            "lon": lon,
            "wind_kt": vmax_f,
            "pressure_mb": mslp_f,
            "nature": nature,
            # For live fixes the ACE nature is the dev-level-mapped nature.
            "ace_nature": nature,
            "source": f"live-{basin_cfg['agency_name']}",
            "storm_num": storm_num,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Shared IBTrACS-vs-live storm merge (one storm per name, no double counting)
# ---------------------------------------------------------------------------

def merge_named_sources(ib_df, live_df, name_col: str = "NAME"):
    """For every named storm present in BOTH sources, keep whichever source has
    MORE 6-hourly observations for that storm and drop the other (ties go to
    live, which carries the current advisory). Returns ``(ib_df, live_df)`` with
    the losing rows removed; the caller concatenates them.

    Both generators call this with the SAME inputs - current-year IBTrACS fixes
    (all natures, pre-eligibility) and live fixes parsed by ``parse_bdeck`` - so
    the source choice, and therefore each storm's canonical track, is identical
    across feeds. This is what kills the Sinlaku 154-vs-160 split.
    """
    if ib_df.empty or live_df.empty:
        return ib_df, live_df

    def _norm(series):
        return series.fillna("").astype(str).str.strip().str.upper()

    ib_n = _norm(ib_df[name_col])
    live_n = _norm(live_df[name_col])
    ib_counts = ib_n.value_counts().to_dict()
    live_counts = live_n.value_counts().to_dict()

    contested = {n for n in live_n.unique()
                 if n and n not in _PLACEHOLDER_NAMES and ib_counts.get(n, 0) > 0}

    drop_from_ib: list[str] = []
    drop_from_live: list[str] = []
    for name in sorted(contested):
        if ib_counts.get(name, 0) > live_counts.get(name, 0):
            drop_from_live.append(name)
        else:
            drop_from_ib.append(name)

    if drop_from_ib:
        ib_df = ib_df[~ib_n.isin(drop_from_ib)].copy()
    if drop_from_live:
        live_df = live_df[~live_n.isin(drop_from_live)].copy()
    return ib_df, live_df


# ---------------------------------------------------------------------------
# Observability timestamps
# ---------------------------------------------------------------------------

def iso_z(t: Optional[dt.datetime]) -> Optional[str]:
    """ISO8601 with a trailing Z (seconds precision), or None. Naive datetimes
    are assumed to already be UTC."""
    if t is None:
        return None
    if isinstance(t, str):
        return t
    return t.replace(microsecond=0).isoformat() + "Z"


def now_iso_z(now: Optional[dt.datetime] = None) -> str:
    """Real build time as ISO8601 Z."""
    return iso_z(now or dt.datetime.utcnow())


def latest_fix_iso(times: Sequence[dt.datetime]) -> Optional[str]:
    """The valid-time of the NEWEST fix in ``times`` as ISO8601 Z, or None."""
    valid = [t for t in times if t is not None]
    if not valid:
        return None
    return iso_z(max(valid))


def staleness_minutes(latest_fix: Optional[dt.datetime],
                      now: Optional[dt.datetime] = None) -> Optional[int]:
    """Whole minutes between the newest fix valid-time and ``now`` (UTC), or
    None if there is no fix. Convenience for the frontend / monitoring."""
    if latest_fix is None:
        return None
    now = now or dt.datetime.utcnow()
    return int((now - latest_fix).total_seconds() // 60)

# ===========================================================================
# Feed assembly (the ACE + tracks JSON builders) - the SHARED pure functions.
# Moved here from generate_ace_plot.py / generate_tracks_plot.py so the cron
# generators AND the streaming intensity poller import the IDENTICAL assembly
# and the two writers can never drift. Output is byte-identical to the prior
# in-generator versions (same code, relocated; the `ac` alias is dropped since
# these now live alongside the math helpers they call).
# ===========================================================================

# Schema of the by-DOY "points" frame the cumulative curve consumes.
_POINT_COLS = ["season", "doy", "ace_increment", "SID", "NAME", "ISO_TIME", "WIND_KT"]

# A storm is "active" if its last observation is within this many hours of
# "now" AND it still has tropical-storm-strength winds.
ACTIVE_WINDOW_HOURS = 60


# --- Saffir-Simpson Hurricane Wind Scale vocabulary (shared) ---

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


_SSHS_RANK = {"TD": 0, "TS": 1, "C1": 2, "C2": 3, "C3": 4, "C4": 5, "C5": 6}


def _sshs_rank(cls: str) -> int:
    return _SSHS_RANK.get(cls, 0)



# --- ACE feed assembly ---

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
    formation = first 6-hourly TS+ observation, dissipation = last, peak
    wind + the time at which it occurred, and the storm's ACE contribution.
    Sorted by formation within each year.

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
        peak_time = None
        peak_w = None
        if "WIND_KT" in rows.columns and rows["WIND_KT"].notna().any():
            peak_idx = rows["WIND_KT"].idxmax()
            peak_w = float(rows.loc[peak_idx, "WIND_KT"])
            pt = rows.loc[peak_idx, "ISO_TIME"]
            if pd.notna(pt):
                peak_time = pt
        ace_total = float(rows["ace_increment"].sum()) \
            if "ace_increment" in rows.columns else 0.0
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
            "peak_wind_kt": peak_w,
            "peak_wind_time": peak_time.isoformat()
                              if peak_time is not None and hasattr(peak_time, "isoformat")
                              else None,
            "ace_total": round(ace_total, 3),
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


def eligible_points_from_canon(canon: pd.DataFrame, basin_cfg: dict,
                               year: int) -> pd.DataFrame:
    """Project the merged canonical fix set down to the by-DOY ``points`` frame
    the cumulative curve consumes, keeping ONLY ACE-eligible fixes (fix_ace_eligible)
    and using fix_increment for the per-fix contribution. This is the
    current-year slice that replaces the IBTrACS-derived one in ``points``."""
    short = basin_cfg["short"]
    rows = []
    for r in canon.to_dict("records"):
        t = r["time"]
        w = r["wind_kt"]
        nat = r.get("ace_nature", r.get("nature"))
        if fix_ace_eligible(t, w, nat, short):
            rows.append({
                "season": year,
                "doy": t.timetuple().tm_yday,
                "ace_increment": fix_increment(w),
                "SID": r["SID"],
                "NAME": r.get("NAME") or "UNNAMED",
                "ISO_TIME": t,
                "WIND_KT": float(w),
            })
    return pd.DataFrame(rows, columns=_POINT_COLS)


def current_year_storms(canon: pd.DataFrame, basin_cfg: dict,
                        year: int) -> list[dict]:
    """Per-storm gantt records for the current year, built from the merged
    canonical set so the homepage, climo page, and tracks feed agree on every
    storm. ACE via storm_ace (the single rounding policy), peak wind via
    canonical_peak_wind (the single peak definition). Only storms that
    produced ACE appear (same as extract_storms_by_year for past years)."""
    if canon.empty:
        return []
    short = basin_cfg["short"]
    out: list[dict] = []
    for _sid, group in canon.groupby("SID", sort=False):
        pts = group.sort_values("time").to_dict("records")
        ace_total = storm_ace(pts, short)
        if ace_total <= 0:
            continue
        elig = [p for p in pts
                if fix_ace_eligible(p["time"], p["wind_kt"],
                                       p.get("ace_nature", p.get("nature")), short)]
        if not elig:
            continue
        peak_w = canonical_peak_wind(pts)
        peak_time = None
        if not math.isnan(peak_w):
            for p in pts:
                w = p["wind_kt"]
                if (is_six_hourly(p["time"]) and w is not None
                        and not (isinstance(w, float) and math.isnan(w))
                        and float(w) == peak_w):
                    peak_time = p["time"]
                    break
        name = ""
        for p in pts:
            n = str(p.get("NAME") or "").strip()
            if n and n.upper() not in ("UNNAMED", "NAMELESS", "INVEST"):
                name = n.upper()
                break
        if not name:
            name = "UNNAMED"
        out.append({
            "name": name,
            "formation": elig[0]["time"].isoformat(),
            "dissipation": elig[-1]["time"].isoformat(),
            "peak_wind_kt": None if math.isnan(peak_w) else round(peak_w, 1),
            "peak_wind_time": peak_time.isoformat() if peak_time else None,
            "ace_total": ace_total,
        })
    out.sort(key=lambda s: s["formation"] or "")
    return out


def build_payload(cum: pd.DataFrame, climo: pd.DataFrame, current_year: int,
                  prior_year: int | None, last_obs_doy: dict[int, int],
                  storms_by_year: dict | None = None,
                  season_ace_current: float | None = None,
                  latest_fix_dt: dt.datetime | None = None,
                  build_now: dt.datetime | None = None) -> dict:
    doy = cum.index.tolist()
    today = dt.date.today()
    today_doy_real = today.timetuple().tm_yday if today.year == current_year else None
    build_now = build_now or dt.datetime.utcnow()
    # The current-season headline ACE is the single authority value from
    # ace_core (sum of per-storm ACE), NOT the by-DOY curve's endpoint (which can
    # differ by a sub-0.001 round-then-sum). When provided it overrides the
    # current-year series endpoint + ranking, so every surface shows one number.
    canonical_current = (round_ace(season_ace_current)
                         if season_ace_current is not None else None)

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
        out_vals = [round(float(v), 3) for v in vals_out]
        latest = round(float(vals_out[-1]) if len(vals_out) else 0.0, 3)
        # Snap the current-year endpoint (curve + headline) to the canonical
        # ace_core season total so the plotted dot sits exactly on the number
        # the rankings + tracks feed report.
        if year == current_year and canonical_current is not None:
            latest = canonical_current
            if out_vals:
                out_vals[-1] = canonical_current
        return {
            "label": str(year),
            "doy": [int(x) for x in doy_out],
            "values": out_vals,
            "latest_value": latest,
        }

    doy_cutoff = today_doy_real or 366
    rank_rows = []
    for year in sorted(cum.columns):
        col = cum[year].values.astype(float)
        total = float(col[-1])
        ytd = float(col[min(doy_cutoff, len(col)) - 1])
        is_current = int(year) == current_year
        # Current year's ranking uses the canonical ace_core season total, not
        # the curve endpoint, so the rank reflects the same number shown above.
        if is_current and canonical_current is not None:
            total = canonical_current
            ytd = canonical_current
        rank_rows.append({
            "year": int(year), "ytd": round(ytd, 2),
            "total": round(total, 2),
            "current": is_current,
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

    latest_fix_z = iso_z(latest_fix_dt)
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
        # Observability: real build time, valid-time of the newest 6-hourly fix
        # used (any nature - a 25 kt TD still counts for freshness), and the gap
        # between them. Mirrors the tracks feed's timestamp triplet.
        "generated_utc": now_iso_z(build_now),
        "latest_fix_valid_utc": latest_fix_z,
        "staleness_minutes": staleness_minutes(latest_fix_dt, build_now),
    }



# --- Tracks feed assembly ---

def merge_and_extract_storms(ibtracs: pd.DataFrame, live: pd.DataFrame,
                             basin_cfg: dict) -> list[dict]:
    """Merge IBTrACS + live. For each named storm in BOTH sources, we keep
    whichever source has more observations for that storm — live tends
    to be more complete for currently-active storms (it has real-time
    advisories) while IBTrACS tends to be more complete for past/archived
    storms (JTWC may leave only a stub in its active directory once a
    storm dissipates). One source per storm, so no duplicate cards in
    the sidebar."""

    # Shared IBTrACS-vs-live merge (ace_core): keep the source with more 6-hourly
    # obs per named storm. SAME function + same inputs as the ACE feed, so both
    # pick the same source -> identical canonical track per storm.
    ibtracs, live = merge_named_sources(ibtracs, live, name_col="NAME")

    frames = [df for df in (ibtracs, live) if not df.empty]
    if not frames:
        return []
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["SID", "time"])
    df = df.sort_values(["SID", "time"]).reset_index(drop=True)

    now = dt.datetime.utcnow()
    active_cutoff = now - dt.timedelta(hours=ACTIVE_WINDOW_HOURS)

    # storm_num is only set on live ATCF rows; IBTrACS contributes NaN.
    # Need it on the dataframe even when live is empty so the groupby
    # below can read it without KeyError.
    if "storm_num" not in df.columns:
        df["storm_num"] = float("nan")

    basin_short = basin_cfg["short"]
    storms: list[dict] = []
    for sid, group in df.groupby("SID"):
        points = group.to_dict("records")
        # ACE + peak wind come from ace_core (the single authority), so they are
        # identical to the ACE feed for every storm. peak_pressure / max category
        # / lifetime / ACE-window are local presentation stats.
        storm_ace_val = storm_ace(points, basin_short)
        peak_wind = canonical_peak_wind(points)
        peak_pres = float("nan")
        # Lifetime = first and last observation of ANY kind (TD included).
        # ACE-window = first and last obs at ACE-eligible (TS+) intensity. Storms
        # that never reach TS (e.g. NURI 2026 peaked at 29 kt) still need a
        # lifetime date range in the sidebar.
        life_start = None
        life_end = None
        ace_start = None
        ace_end = None
        max_cls = "TD"
        for p in points:
            w = p["wind_kt"]
            t = p["time"]
            if life_start is None:
                life_start = t
            life_end = t
            # ACE-window bounds use the SAME eligibility ace_core counts ACE on.
            if fix_ace_eligible(t, w, p.get("ace_nature", p.get("nature")),
                                   basin_short):
                if ace_start is None:
                    ace_start = t
                ace_end = t
            if pd.notna(w):
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
        # Newest fix valid-time for this storm (observability).
        latest_fix = max((p["time"] for p in points if p.get("time")),
                         default=None)

        # Active = (1) last observation is recent, AND (2) its nature is
        # still tropical/subtropical (not extratropical, not a pre-genesis
        # / post-dissipation disturbance), AND (3) it has a valid wind.
        # We deliberately do NOT require >=34 kt here: a designated
        # tropical depression is an active system too (e.g. JMA-recognised
        # Jangmi 2026 — last fix 25 kt, TS-nature). Both renderers already
        # carry a dedicated marker for the peak<34 kt case — the hollow
        # blue TD circle (render_active_icons' peak<34 branch and
        # build_global_geojson's "td_circle" marker_type). Gating on 34 kt
        # made that marker unreachable (is_active could never be True while
        # peak<34), so designated TDs silently dropped off both the
        # per-basin and home-page maps. Weakening/dissipation is still
        # caught by the nature gate: a system that decays to a remnant low
        # or goes extratropical flips to DS/ET and falls out of "active".
        recent_obs = (len(points) > 0
                      and points[-1]["time"] >= active_cutoff)
        is_active = False
        if len(points) > 0:
            last = points[-1]
            has_wind = pd.notna(last["wind_kt"]) and last["wind_kt"] > 0
            tropical = (last["nature"] or "") not in {"ET", "DS"}
            is_active = recent_obs and has_wind and tropical
        # Invest = ATCF storm-number 90-99 (JTWC/NHC convention). Pulled
        # from any row in the group; IBTrACS rows have NaN and are ignored
        # since IBTrACS doesn't archive invests.
        nums = [p.get("storm_num") for p in points
                if p.get("storm_num") is not None
                and not (isinstance(p["storm_num"], float)
                         and math.isnan(p["storm_num"]))]
        is_invest = bool(nums) and any(int(n) >= 90 for n in nums)
        # Recent invest = invest with a fresh observation. JTWC/NHC cycle
        # 90-99 numbers across the season, so without this filter the
        # card grid accumulates ~10 stale invests by mid-season (most of
        # which never developed) that just clutter the inactive section.
        recent_invest = is_invest and recent_obs
        # ATCF id (e.g. "91W" / "92L" / "93E") for the invest renderer's
        # red-X label. Only set for invests; numbered TCs surface their
        # name via the spinning-icon label instead.
        atcf_id = None
        if is_invest and nums:
            atcf_id = f"{int(nums[0])}{basin_cfg.get('invest_letter', '')}"
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
            # ACE is already rounded by ace_core's single policy (3 dp), so the
            # season total = sum of these by construction and matches the ACE feed.
            "ace": storm_ace_val,
            "latest_fix_valid_utc": iso_z(latest_fix),
            "max_category": max_cls,
            "current_category": current_cls,
            "is_active": bool(is_active),
            "is_invest": bool(is_invest),
            "recent_invest": bool(recent_invest),
            "atcf_id": atcf_id,
            "points": [{
                "t": p["time"].isoformat(),
                "lat": round(float(p["lat"]), 2),
                "lon": round(float(p["lon"]), 2),
                "wind_kt": None if pd.isna(p["wind_kt"]) else round(float(p["wind_kt"]), 1),
                "pressure_mb": None if pd.isna(p["pressure_mb"]) or p["pressure_mb"] <= 0
                               else round(float(p["pressure_mb"]), 1),
                "cls": sshs_class(p["wind_kt"]),
                # NATURE passes through from IBTrACS ("TS", "SS", "ET",
                # "DS", "NR", "MX", "") or the ATCF dev-level mapping in
                # parse_atcf_bdeck() ("TS", "SS", "ET", ""). The SVG
                # renderer uses this to draw non-tropical points as
                # triangles (see render_tracks_svg).
                "nature": (p.get("nature") or "").strip(),
            } for p in points],
        })
    # Drop stale invest cards. Numbered TCs (01-89) keep showing past
    # cards as part of the season summary; only invests need this
    # filter, because JTWC/NHC cycle 90-99 numbers continuously.
    storms = [s for s in storms
              if not s["is_invest"] or s["recent_invest"]]
    # Sort: active TCs → recent invests → past TCs by ACE → start.
    # Past invests are already filtered out above, so all remaining
    # invests are guaranteed recent — the existing key still works.
    storms.sort(key=lambda s: (not s["is_active"], not s["is_invest"],
                               -s["ace"], s["start"] or ""))
    return storms


def compute_header_stats(storms: list[dict]) -> dict:
    named = sum(1 for s in storms if _sshs_rank(s["max_category"]) >= 1)  # TS+
    cat1plus = sum(1 for s in storms if _sshs_rank(s["max_category"]) >= 2)  # C1+
    cat3plus = sum(1 for s in storms if _sshs_rank(s["max_category"]) >= 4)  # C3+
    cat5 = sum(1 for s in storms if s["max_category"] == "C5")
    # Season ACE via the single authority: sum of the (already rounded) per-storm
    # ACE values, so it equals the ACE feed's season ACE exactly.
    total_ace = season_ace([s["ace"] for s in storms])
    return {
        "named": named,
        "cat1plus": cat1plus,
        "cat3plus": cat3plus,
        "cat5": cat5,
        "total_ace": total_ace,
    }

