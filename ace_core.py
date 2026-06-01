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
