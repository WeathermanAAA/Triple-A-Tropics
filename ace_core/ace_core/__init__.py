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
    storm_is_invest(points)                          # ATCF 90-99 invest gate
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
import re
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


def _is_real_name(value) -> bool:
    """True if ``value`` is a genuine storm name (not a blank/placeholder).
    Also rejects parse_bdeck's "#NN" designation-without-name fallback so the
    Gantt derives an ATCF-id label instead of showing "#04"."""
    n = str(value or "").strip()
    if not n or n.upper() in _PLACEHOLDER_NAMES:
        return False
    if n.startswith("#") and n[1:].isdigit():
        return False
    return True


def _spell_cardinals(n_max: int = 99) -> "frozenset[str]":
    """The English cardinals 1..n_max in upper case ('ONE'..'NINETY-NINE')."""
    ones = ["", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT",
            "NINE", "TEN", "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN",
            "SIXTEEN", "SEVENTEEN", "EIGHTEEN", "NINETEEN"]
    tens = ["", "", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY",
            "EIGHTY", "NINETY"]
    out: set[str] = set()
    for n in range(1, n_max + 1):
        if n < 20:
            out.add(ones[n])
        else:
            t, o = divmod(n, 10)
            out.add(tens[t] if o == 0 else f"{tens[t]}-{ones[o]}")
    return frozenset(out)


# An ATCF b-deck names a DESIGNATED-BUT-UNNAMED system with the spelled-out
# cardinal of its storm number ("TEN" for JTWC TD 10W; NHC uses the same for an
# unnamed Atlantic TD). That is a designation, NOT a real storm name, so the
# tracks feed relabels it to the "##<letter>" ATCF id — the label the invests
# already wear ("91W") and the one operators expect for a numbered depression.
_ATCF_NUMBER_WORDS = _spell_cardinals(99)


def _is_atcf_number_name(value) -> bool:
    """True if ``value`` is the spelled-out cardinal an ATCF b-deck uses as the
    'name' of a designated-but-unnamed system ('TEN' for TD 10W) — a designation
    rather than a real storm name. No JMA/NHC name is an English cardinal, so
    this never suppresses a genuine name."""
    return str(value or "").strip().upper() in _ATCF_NUMBER_WORDS


# ATCF basin prefix (USA_ATCF_ID first two letters) -> the trailing storm-id
# letter used on the climatology Gantt (e.g. "EP" -> "E", so EP012023 -> 01E).
_ATCF_BASIN_LETTER = {
    "AL": "L", "EP": "E", "CP": "C", "WP": "W", "IO": "I", "SH": "S",
}
_ATCF_LETTER_BASIN = {v: k for k, v in _ATCF_BASIN_LETTER.items()}


def atcf_short_id(usa_atcf_id) -> Optional[str]:
    """'EP012023' -> '01E'; None if unparseable / invest (>=90) / 00."""
    s = str(usa_atcf_id or "").strip().upper()
    if len(s) < 8:
        return None
    pre, num = s[:2], s[2:4]
    if pre not in _ATCF_BASIN_LETTER or not num.isdigit():
        return None
    n = int(num)
    if n <= 0 or n >= 90:
        return None
    return f"{n:02d}{_ATCF_BASIN_LETTER[pre]}"


def _sid_atcf_letter(sid) -> Optional[str]:
    """'NHC_CP902026' -> 'C': the trailing ATCF letter implied by an agency
    SID's own basin token. None when the SID carries no recognizable token
    (IBTrACS-style numeric SIDs)."""
    m = re.search(r"_([A-Z]{2})\d", str(sid or "").upper())
    return _ATCF_BASIN_LETTER.get(m.group(1)) if m else None


def short_id_from_storm_num(storm_num, basin_short) -> Optional[str]:
    """Live-feed analogue of ``atcf_short_id``: (4, 'ep') -> '04E'. None for
    invests (>=90), 0, or an unknown basin."""
    letter = {"al": "L", "ep": "E", "wp": "W", "cp": "C"}.get(basin_short)
    try:
        n = int(storm_num)
    except (TypeError, ValueError):
        return None
    if letter is None or n <= 0 or n >= 90:
        return None
    return f"{n:02d}{letter}"


def agency_sid_from_atcf_id(usa_atcf_id, basin_cfg, year) -> Optional[str]:
    """Map an IBTrACS ``USA_ATCF_ID`` ('WP092026') to the agency SID the live
    b-deck / knackwx path emits ('JTWC_WP092026'), so a current-season IBTrACS
    entry and its live JTWC/NHC designation collapse onto ONE sid and merge in
    ``merge_and_extract_storms`` instead of rendering as a duplicate UNNAMED
    ghost on the tracks/home map.

    Returns None (caller keeps the raw IBTrACS sid) when the id is unparseable,
    an invest (>=90) or 00, a DIFFERENT basin than ``basin_cfg`` (so a
    basin-crossed storm is never mis-merged onto this basin's numbering), or a
    different year than the season being built. ``basin_cfg`` must carry
    ``agency_name`` + ``short`` (the tracks BASINS dicts); the produced sid is
    byte-identical to parse_bdeck's
    ``f"{agency_name}_{short.upper()}{NN}{year}"``."""
    s = str(usa_atcf_id or "").strip().upper()
    if len(s) < 8:
        return None
    pre, num_s, yr_s = s[:2], s[2:4], s[4:8]
    if not (num_s.isdigit() and yr_s.isdigit()):
        return None
    short = str(basin_cfg.get("short") or "").strip()
    agency = str(basin_cfg.get("agency_name") or "").strip()
    # The ATCF basin prefix must match THIS basin (WP<->wp, AL<->al, EP<->ep) so
    # the remapped sid matches what this basin's live fetch produces; a mismatch
    # (e.g. an EP-origin storm carried in the WP file) keeps the raw sid.
    # Exception: the EP page also carries the Central Pacific (IBTrACS files
    # CP storms under BASIN=EP with USA_ATCF_ID "CP##...", e.g. Ioke = CP01),
    # and the live bcp b-deck sweep emits NHC_CP##<year> for them — so a CP
    # prefix under the EP page maps to the CP-token SID, keeping the
    # provisional IBTrACS row and the live designation ONE storm.
    if not short or not agency:
        return None
    if pre != short.upper() and not (pre == "CP" and short == "ep"):
        return None
    n = int(num_s)
    if n <= 0 or n >= 90:
        return None
    if int(yr_s) != int(year):
        return None
    return f"{agency}_{pre}{n:02d}{int(year)}"


def designation_label(short_id, peak_wind_kt) -> str:
    """'01E', 95 -> 'HU 01E'; stage from peak (TD<34<=TS<64<=HU)."""
    try:
        pk = float(peak_wind_kt)
    except (TypeError, ValueError):
        pk = 0.0
    stage = "TD" if pk < 34 else ("TS" if pk < 64 else "HU")
    return f"{stage} {short_id}"


# ATCF "SPAWNINVEST, alNN<year> to alMM<year>" trailing tag (any basin). The
# DESTINATION (group 1 = its basin token, group 2 = the MM digits) is the
# 90-99 invest the designated system spawned — parse_bdeck records both per
# storm for the PTC->invest handoff. The token matters: the dedup must not
# let "… to ep902026" drop an unrelated 90C (same number, different basin).
_SPAWNINVEST_RE = re.compile(
    r"SPAWNINVEST,\s*[A-Za-z]{2}\d{6}\s+to\s+([A-Za-z]{2})(\d{2})\d{4}")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _is_nan(v) -> bool:
    return isinstance(v, float) and math.isnan(v)


def _nature_str(v) -> str:
    """NATURE as a clean string. A missing NATURE cell reaches us from
    pandas as float NaN, which is TRUTHY — the old ``(v or "")`` idiom
    let it through to ``.strip()`` and crashed the whole generator run
    (first seen 2026-06-07: a live WP b-deck fix with a blank NATURE
    column). None/NaN map to "" — the long-standing blank-NATURE
    semantics (ACE-eligible only on provisional data, see
    nature_eligible)."""
    return "" if v is None or _is_nan(v) else str(v).strip()


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
    n = _nature_str(nature).upper()
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


def storm_is_invest(points: Iterable[dict]) -> bool:
    """True if the storm's CURRENT designation is an ATCF invest number
    (90-99 by the JTWC/NHC convention; ``parse_bdeck`` sets ``storm_num``
    on live rows, IBTrACS rows have none/NaN). The NEWEST fix that carries
    a storm number decides: a promoted invest (96E -> TD 05E) may retain
    invest-numbered rows in its merged history, but the moment its latest
    fixes wear the designated number the storm stops being an invest —
    the number flips on designation and every downstream consumer (marker,
    cards, ACE guard) must flip with it (2026-07-14 home-map marker bug:
    the old any-row semantics could hold a promoted system in invest dress
    forever). The ONE invest definition, shared by the ACE invest guard
    below and the tracks-feed assembly (``merge_and_extract_storms``)."""
    for p in reversed(list(points)):
        n = p.get("storm_num")
        if n is None or _is_nan(n):
            continue
        return int(n) >= 90
    return False


_DESIG_NUM_RE = re.compile(r"^(\d{1,2})[A-Z]$")
_SID_NUM_RE = re.compile(r"^[A-Z]+_[A-Z]{2}(\d{2})\d{4}$")


def wears_invest_x(storm: dict) -> bool:
    """Marker-identity gate — the ATCF NUMBER decides (Andrew's 2026-07-14
    rule, overriding the earlier PTC-wears-invest-visuals design): 90-99 =
    invest area (red X); 01-89 = DESIGNATED system, which renders by
    intensity (current_category glyph) even while NHC is still advising it
    as a Potential Tropical Cyclone. Reads the storm's own designation
    (``atcf_id`` like "05E"/"90C", then the SID number token), falling back
    to the is_invest flag only when no number is parseable. Mirrored in JS
    by ``wearsInvestX`` (LIVE_BASIN_JS + the global-map template) — any
    edit here must update both (parity suite)."""
    m = _DESIG_NUM_RE.match(str(storm.get("atcf_id") or "").strip().upper())
    if m:
        return int(m.group(1)) >= 90
    m = _SID_NUM_RE.match(str(storm.get("sid") or "").strip().upper())
    if m:
        return int(m.group(1)) >= 90
    return bool(storm.get("is_invest"))


def storm_ace(points: Iterable[dict], basin: str,
              provisional: bool = True) -> float:
    """The ACE of ONE storm: sum of eligible 6-hourly fix increments, rounded by
    the single policy. ``points`` is an iterable of fix dicts carrying ``time``,
    ``wind_kt`` and an ACE nature (``ace_nature`` or ``nature``).

    INVEST GUARD (by construction, not circumstance): an ATCF invest (storm
    number 90-99) NEVER accrues ACE, regardless of wind or mapped nature.
    Operationally an invest is a pre-genesis AREA, not a designated TC; no
    agency counts one toward ACE or the named tally even when a b-deck fix
    reaches 34 kt with a tropical dev-level (a TD-coded 91W maps to nature
    "TS" and would otherwise pass the nature gate). Typical DB/LO-coded
    invests were already blocked by the nature gate - this makes the rule
    explicit for tropical-coded ones too."""
    points = list(points)
    if storm_is_invest(points):
        return round_ace(0.0)
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


# ATCF wind-radii thresholds (kt) and the per-threshold quadrant column suffixes,
# in the NEQ quadrant order RAD1..RAD4 = NE, SE, SW, NW. The 12 radii columns
# parse_bdeck emits are the cross product: r{thr}_{quad}.
RADII_THRESHOLDS = (34, 50, 64)
RADII_QUADS = ("ne", "se", "sw", "nw")
RADII_COLS = [f"r{thr}_{q}" for thr in RADII_THRESHOLDS for q in RADII_QUADS]


def _radii_int(raw: str) -> int:
    """A single ATCF quadrant-radius cell -> non-negative int nautical miles.
    Blank / non-numeric / negative cells mean "no extent in that quadrant" -> 0
    (per the spec: a present threshold row with a blank quadrant is a real 0,
    not missing data)."""
    try:
        v = int(float((raw or "").strip()))
    except (TypeError, ValueError):
        return 0
    return v if v > 0 else 0


def _parse_radii_row(parts: list[str]) -> Optional[tuple[int, list[int]]]:
    """Extract (threshold, [ne, se, sw, nw]) from one ATCF b-deck row, or None if
    the row carries no recognised wind-radius threshold.

    Column layout (0-indexed after a `,`-split + strip):
        parts[11] = RAD       (34 | 50 | 64)
        parts[12] = WINDCODE  (NEQ = quadrants NE/SE/SW/NW starting NE;
                               AAA = symmetric full circle)
        parts[13..16] = RAD1..RAD4 (nautical miles)

    WINDCODE handling:
      * NEQ -> RAD1..4 map straight to NE, SE, SW, NW.
      * AAA -> the single symmetric radius (RAD1) is replicated to all four
        quadrants.
      * anything else / blank with a real threshold -> treat RAD1..4 positionally
        (defensive; real decks only emit NEQ/AAA)."""
    try:
        thr = int(parts[11])
    except (IndexError, ValueError):
        return None
    if thr not in RADII_THRESHOLDS:
        return None
    windcode = (parts[12] if len(parts) > 12 else "").strip().upper()
    r1 = _radii_int(parts[13]) if len(parts) > 13 else 0
    if windcode == "AAA":
        return thr, [r1, r1, r1, r1]
    r2 = _radii_int(parts[14]) if len(parts) > 14 else 0
    r3 = _radii_int(parts[15]) if len(parts) > 15 else 0
    r4 = _radii_int(parts[16]) if len(parts) > 16 else 0
    return thr, [r1, r2, r3, r4]


def parse_bdeck(text: str, season: int, basin_cfg: dict):
    """Parse an ATCF b-deck file into a DataFrame of 6-hourly fixes - the SINGLE
    parser both generators use, so a named storm has the same fix set (hence the
    same peak wind + ACE) everywhere.

    Columns: SID, NAME, season, time, lat, lon, wind_kt, pressure_mb, nature,
    ace_nature, source, storm_num, and the 12 wind-radii columns in
    ``RADII_COLS`` (r34_ne..r64_nw). ``nature``/``ace_nature`` come from the ATCF
    dev-level via ``STATUS_TO_NATURE`` (wind-based fallback for unmapped codes).

    Each observation repeats up to 3x in a b-deck, once per wind-radius
    threshold (34/50/64 kt). We build ONE fix per (storm, timestamp) from the
    FIRST row (the blank/0/34 kt row, exactly as before - identical SID, time,
    wind, pressure, nature, ACE math, dedup), then ACCUMULATE quadrant radii
    from EVERY threshold row of that same fix (34/50/64). A threshold with no
    row for the fix keeps its four columns at None (absent); a threshold whose
    row has blank quadrants records 0 (real "no extent"). See ``_parse_radii_row``
    for the WINDCODE (NEQ/AAA) handling.
    """
    import pandas as pd

    rows = []
    name_by_storm: dict[int, str] = {}
    # storm_num -> the 90-99 invest number this designated system SPAWNED,
    # parsed from the ATCF "SPAWNINVEST, alNN<yr> to alMM<yr>" trailing tag
    # (MM = the invest). When a Potential Tropical Cyclone (e.g. AL01) is the
    # same system as a live invest (AL90), this is the authoritative link that
    # lets merge_and_extract_storms RETIRE the invest marker so we never show
    # both 01L-X and 90L-X for one system. Storm-level (mirrors name_by_storm).
    spawn_by_storm: dict[int, int] = {}
    # storm_num -> the spawned invest's OWN trailing letter ("C" from
    # "… to cp902026"), so the handoff dedup is (letter, number)-keyed and a
    # same-numbered invest in another basin sharing the page never gets
    # dropped by mistake. Absent for unrecognized tokens (number-only match).
    spawn_letter_by_storm: dict[int, str] = {}
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
        # GENESIS### is the ATCF genesis-area tag NHC/CPHC decks carry in the
        # name column before a real name/cardinal lands — a placeholder, not
        # a name (a young designation would otherwise briefly show it).
        if (name_col and name_col not in {"", "NAMELESS", "INVEST"}
                and not re.fullmatch(r"GENESIS\d+", name_col)):
            name_by_storm[storm_num] = name_col
        m_spawn = _SPAWNINVEST_RE.search(line)
        if m_spawn:
            spawn_by_storm[storm_num] = int(m_spawn.group(2))
            tok = m_spawn.group(1).upper()
            if tok in _ATCF_BASIN_LETTER:
                spawn_letter_by_storm[storm_num] = _ATCF_BASIN_LETTER[tok]

    # (storm_num, tstamp) -> index of that fix's record in ``rows``, so the
    # 50/64 kt (and a duplicate 34 kt) rows of the SAME observation merge their
    # radii into the one fix the first row created.
    fix_index: dict[tuple[int, str], int] = {}
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
        key = (storm_num, tstamp)
        radii = _parse_radii_row(parts)
        if key in fix_index:
            # The fix already exists (built from its first row). Later rows
            # contribute ONLY radii - every other field stays from row 1.
            if radii is not None:
                thr, quads = radii
                rec = rows[fix_index[key]]
                for q, val in zip(RADII_QUADS, quads):
                    rec[f"r{thr}_{q}"] = val
            continue
        # A fix is CREATED only from its first blank/0/34 kt row, exactly as
        # before - a stray 50/64 row whose 34 row was filtered out (bad
        # lat/lon, off-synoptic hour) does NOT manufacture a fix. This keeps
        # every non-radii field byte-identical to the pre-radii parser.
        if rad not in ("", "0", "34"):
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
        devlvl_u = (devlvl or "").strip().upper()
        nature = STATUS_TO_NATURE.get(devlvl_u, "")
        if not nature:
            nature = "TS" if (vmax and not _is_nan(vmax_f) and vmax_f > 0) else "DS"
        # The row's OWN ATCF basin token (field 0: "CP" in a bcp deck) wins
        # over the page basin for the SID + invest fallback name — the EP
        # page also covers Central Pacific systems, which are "90C"/NHC_CP…,
        # not "90E"/NHC_EP…. An unrecognized token keeps the page-basin
        # behavior (today every deck token equals the page basin, so this is
        # a byte-identical no-op until a cross-token deck is ever parsed).
        deck_basin = parts[0].upper()
        if deck_basin not in _ATCF_BASIN_LETTER:
            deck_basin = basin_cfg["short"].upper()
        if storm_num >= 90:
            fallback_name = (f"{storm_num}"
                             f"{_ATCF_BASIN_LETTER.get(deck_basin) or basin_cfg.get('invest_letter', '')}")
        else:
            fallback_name = f"#{storm_num:02d}"
        rec = {
            "SID": f"{basin_cfg['agency_name']}_{deck_basin}"
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
            # 90-99 invest this designated system spawned (None unless tagged),
            # plus that invest's own trailing letter (None when untagged or
            # the token was unrecognized -> number-only dedup fallback).
            "spawn_invest": spawn_by_storm.get(storm_num),
            "spawn_invest_letter": spawn_letter_by_storm.get(storm_num),
        }
        # Radii columns default to None (threshold absent for this fix); the
        # first row's own threshold (if any) is recorded immediately.
        for col in RADII_COLS:
            rec[col] = None
        if radii is not None:
            thr, quads = radii
            for q, val in zip(RADII_QUADS, quads):
                rec[f"r{thr}_{q}"] = val
        fix_index[key] = len(rows)
        rows.append(rec)
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
# "now" AND it still has tropical-strength winds + a tropical nature.
# 24 h (was 60): an active storm gets b-deck fixes every ~6 h, so 24 h is
# four missed cycles. The 60 h window kept dissipated storms "ACTIVE" on
# every surface for ~2.5 days, because NHC writes no terminal EX/DS row
# for a TD that simply stops (CRISTINA's final fix: TD 30 kt, nature=TS)
# — the nature gate never fires and staleness was doing all the work.
ACTIVE_WINDOW_HOURS = 24

# FINAL-ADVISORY RETIREMENT (the prompt path, complementing the window):
# NHC removes a storm from CurrentStorms.json the moment advisories end
# (dissipated / post-tropical / remnant low) — the authoritative signal.
# A named AL/EP/CP storm absent from a cleanly-fetched CurrentStorms list
# whose latest fix is older than this grace window is retired — STATUS
# ONLY: the storm keeps its full track in the feeds/maps and its season
# ACE / named-storm contribution; it just stops being "active" (drops
# from active counts, loses the live marker/label). The grace keeps a
# storm with fresh fixes afloat through a transient listing hiccup.
NHC_RETIRE_GRACE_H = 12

# Live ATCF-style sid inside feed SIDs ("NHC_EP032026" / "EP032026").
# Group 1 = the bare ATCF id, group 2 = basin, group 3 = storm number
# (90-99 = invests, which CurrentStorms never lists — exempt).
_NHC_SID_RE = re.compile(r"^(?:[A-Z]+_)?((AL|EP|CP)(\d{2})\d{4})$")

CURRENT_STORMS_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"

# A Potential Tropical Cyclone (PTC) is a DESIGNATED system (number 01-49,
# NOT a 90-99 invest) that NHC is actively advising on while it is still a
# pre-genesis disturbance. Its b-deck dev-level is DB/LO, which STATUS_TO_NATURE
# maps to the non-tropical "DS" — so the normal is_active "tropical" gate hides
# it even though NHC has issued a full forecast/advisory + cone + watches. These
# two sets let merge_and_extract_storms recognise the PTC case so it can ACTIVATE
# such a system (PTC ACTIVATION, the mirror of the final-advisory RETIREMENT).
#   * PTC_NATURES   — b-deck-derived natures that read as "not yet a TC".
#   * PTC_CLASSIFICATIONS — CurrentStorms.json `classification` codes for a
#     disturbance / potential TC (the field fetch_nhc_active_sids now returns),
#     so a PTC is distinguishable from a genuine tropical depression (whose
#     nature is the tropical "TS" and which therefore activates the normal way).
PTC_NATURES = {"DB", "DS"}
# CurrentStorms `classification` codes for a pre-genesis disturbance NHC is
# advising on. Disturbance-only ON PURPOSE: a genuine TC carries TD/TS/HU/STD/
# STS/EX, NONE of which appear here, so this can never reclassify a real
# tropical/subtropical/post-tropical system as a PTC. The b-deck NATURE ("DS")
# is the primary PTC signal observed in the wild (dev-level DB/LO -> DS); this
# set is the secondary disambiguator (e.g. a provisional blank-NATURE fix that
# NHC nonetheless calls a disturbance). Observed in the wild: NHC CurrentStorms
# tags a Potential Tropical Cyclone with classification "PC" (AL012026 "One",
# 2026-06-16); "DB"/"PTC" are kept as defensive synonyms.
PTC_CLASSIFICATIONS = {"PC", "PTC", "DB", "LO", "WV", "MD", "DISTURBANCE"}


def fetch_nhc_active_sids(timeout: float = 20.0,
                          retries: int = 2) -> "dict[str, str] | None":
    """ATCF id -> NHC `classification` code (e.g. {"AL012026": "DB"}) for every
    currently-NHC-active storm per CurrentStorms.json, or None when the fetch
    fails — callers MUST treat None as "no information" (never RETIRE or
    ACTIVATE anything on a failed fetch).

    Was ``set[str] | None`` through ace-core-v0.7.1; widened to a mapping here.
    This is a SUPERSET of the old contract — every consumer only does ``sid in
    result`` (membership), which tests dict KEYS, so a set caller and a dict
    caller behave identically for retirement. The added value (the classification
    code) lets a caller tell a Potential Tropical Cyclone / disturbance ("DB")
    apart from a tropical depression ("TD"): a PTC's b-deck nature is the
    non-tropical "DS", so without this signal the activation could not name it.

    stdlib urllib on purpose: ace_core stays pandas/numpy-only, and the cron
    generators' minimal installs ship no requests (0.7.0's lazy `import
    requests` crashed the update-ace tracks regen at call time)."""
    import json as _json
    import time as _time
    import urllib.request as _url

    last_exc = None
    for attempt in range(retries + 1):
        try:
            req = _url.Request(
                CURRENT_STORMS_URL,
                headers={"User-Agent": "triple-a-tropics feed builder"})
            with _url.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise OSError(f"HTTP {resp.status}")
                data = _json.loads(resp.read().decode("utf-8"))
            out: dict[str, str] = {}
            for s in (data.get("activeStorms") or []):
                sid = str(s.get("id") or "").strip().upper()
                if re.fullmatch(r"(?:AL|EP|CP)\d{6}", sid):
                    out[sid] = str(s.get("classification") or "").strip().upper()
            return out
        except Exception as exc:  # noqa: BLE001 - any failure -> None
            last_exc = exc
            if attempt < retries:
                _time.sleep(2 ** attempt)
    print(f"[ace_core] WARN: CurrentStorms fetch failed ({last_exc}); "
          "active-status retirement skipped this cycle.")
    return None


# --- Saffir-Simpson Hurricane Wind Scale vocabulary (shared) ---

SSHS_COLORS = {
    "TD": "#3fa4ff",    # depression - blue
    "TS": "#46c56a",    # tropical storm - green
    "C1": "#ffe14d",    # cat 1 - yellow
    "C2": "#ff9a2f",    # cat 2 - orange
    "C3": "#f5333c",    # cat 3 - clean red (distinct from C2 orange; was #ff4d3b which read orange)
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


def extract_gantt_storms_by_year(points: pd.DataFrame,
                                 min_year: int = 1970) -> dict:
    """Per-storm Storm-Activity-Gantt summaries — the TD-inclusive sibling of
    ``extract_storms_by_year``. Operates on a tropical-all-intensity frame
    (NATURE-tropical at ANY wind, including peak<=33 kt depressions) with
    columns: season, doy, SID, NAME, ISO_TIME, WIND_KT, ATCF. Each storm's bar
    spans its full tropical life (TD genesis through dissipation); ACE stays
    identical because ``ace_total`` only sums rows >= 34 kt (TD-only -> 0).

    Name: real NAME if any; else an ATCF-id designation label (e.g. "TD 04E")
    derived from the group's USA_ATCF_ID; else the storm is SKIPPED (this kills
    the phantom "UNNAMED"). Pre-``min_year`` seasons are excluded.
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
        # ACE is byte-identical to the eligible-points path: only >= 34 kt
        # rows contribute, so a TD-only system scores 0.
        ace_total = 0.0
        if "WIND_KT" in rows.columns:
            w = pd.to_numeric(rows["WIND_KT"], errors="coerce")
            elig_w = w[w >= 34]
            if not elig_w.empty:
                ace_total = float((elig_w ** 2 / 10_000.0).sum())
        # Name: real NAME first; else an ATCF-id designation label; else skip.
        name = ""
        for n in rows["NAME"].fillna("").astype(str):
            if _is_real_name(n):
                name = n.strip().upper()
                break
        if not name:
            short_id = None
            if "ATCF" in rows.columns:
                for a in rows["ATCF"]:
                    short_id = atcf_short_id(a)
                    if short_id:
                        break
            if not short_id:
                continue  # no real name and no derivable id -> phantom, drop
            name = designation_label(short_id, peak_w)
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
        # Invest fixes (ATCF 90-99) never enter the ACE curve - same rule
        # as storm_ace's invest guard, applied at the by-DOY row level.
        if storm_is_invest((r,)):
            continue
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
    canonical_peak_wind (the single peak definition). Every DESIGNATED tropical
    system appears (including TD-strength systems with ace_total 0); invests and
    nameless/id-less phantoms are dropped (mirrors extract_gantt_storms_by_year
    for past years)."""
    if canon.empty:
        return []
    short = basin_cfg["short"]
    out: list[dict] = []
    for _sid, group in canon.groupby("SID", sort=False):
        pts = group.sort_values("time").to_dict("records")
        # Invests never appear in the season's storm list (grey/red-X glyph
        # elsewhere). storm_ace would return 0.0 for them anyway - the explicit
        # skip keeps the rule visible.
        if storm_is_invest(pts):
            continue
        # TD-inclusive Storm-Activity Gantt: keep any DESIGNATED tropical system
        # (reached at least TD), not just the ACE-eligible (>=34 kt) ones. The
        # bar spans the full designated tropical life. Pure DB/LO disturbances
        # that never reached TD never produce a tropical-eligible fix -> dropped.
        trop_fixes = [p for p in pts
                      if is_six_hourly(p["time"])
                      and nature_eligible(p.get("ace_nature", p.get("nature")),
                                          short, provisional=True)]
        if not trop_fixes:
            continue
        ace_total = storm_ace(pts, short)  # 0 for TD-only systems (unchanged)
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
        # Name: real NAME first; else an ATCF-id designation label derived from
        # the live storm_num (<90); else skip (phantom, no real name or id).
        name = ""
        for p in pts:
            if _is_real_name(p.get("NAME")):
                name = str(p.get("NAME")).strip().upper()
                break
        if not name:
            # The designation letter follows the storm's OWN SID basin token
            # (NHC_CP012026 -> "01C" on the EP page), page basin as fallback —
            # mirrors the tracks-side own-letter rule so the two feeds never
            # disagree on a young unnamed designation's label.
            own = _sid_atcf_letter(_sid)
            own_short = _ATCF_LETTER_BASIN.get(own, "").lower() or short
            short_id = None
            for p in pts:
                short_id = short_id_from_storm_num(p.get("storm_num"), own_short)
                if short_id:
                    break
            if not short_id:
                continue
            name = designation_label(short_id,
                                     None if math.isnan(peak_w) else peak_w)
        out.append({
            "name": name,
            "formation": trop_fixes[0]["time"].isoformat(),
            "dissipation": trop_fixes[-1]["time"].isoformat(),
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

def _radii_quad_ints(p: dict, thr: int):
    """The four quadrant radii [ne, se, sw, nw] for one threshold of one fix as
    ints, or None when this threshold has NO row for the fix.

    A present threshold has at least one non-absent quadrant; absent quadrants
    are stored as None (parse_bdeck never wrote them) or surface as NaN after
    pandas coerces a mixed None/int column - both mean "no row" and collapse the
    whole threshold to None. A present-but-zero quadrant (real "no extent") is
    kept as 0."""
    vals = []
    present = False
    for q in RADII_QUADS:
        v = p.get(f"r{thr}_{q}")
        if v is None or (isinstance(v, float) and math.isnan(v)):
            vals.append(0)
        else:
            present = True
            vals.append(int(v))
    return vals if present else None


def _fix_radii(p: dict):
    """Compact per-fix wind-radii dict for the feed:
    ``{"34": [ne,se,sw,nw], "50": [...], "64": [...]}`` with int nm values.
    A threshold key is omitted when that threshold has no data for the fix; the
    whole dict is None (-> the caller omits the "radii" key) when no threshold
    has data. Size discipline: the feed is polled every 30s."""
    out = {}
    for thr in RADII_THRESHOLDS:
        quads = _radii_quad_ints(p, thr)
        if quads is not None:
            out[str(thr)] = quads
    return out or None


def _serialize_point(p: dict) -> dict:
    """One per-fix dict for the tracks feed. Existing fields (t/lat/lon/wind_kt/
    pressure_mb/cls/nature) are byte-identical to the pre-radii feed; ``radii``
    is ADDITIVE and only present when the fix carries wind-radii data."""
    pt = {
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
        # triangles (see render_tracks_svg). NaN-safe via _nature_str
        # (0.5.1: a blank b-deck NATURE arrives as truthy float NaN and the
        # old ``(v or "").strip()`` crashed the run) - merged forward into
        # the 0.6.0 radii serializer.
        "nature": _nature_str(p.get("nature")),
    }
    radii = _fix_radii(p)
    if radii is not None:
        pt["radii"] = radii
    return pt


def merge_and_extract_storms(ibtracs: pd.DataFrame, live: pd.DataFrame,
                             basin_cfg: dict,
                             nhc_active_sids: "dict[str, str] | set[str] | None"
                             = None,
                             ) -> list[dict]:
    """Merge IBTrACS + live. For each named storm in BOTH sources, we keep
    whichever source has more observations for that storm — live tends
    to be more complete for currently-active storms (it has real-time
    advisories) while IBTrACS tends to be more complete for past/archived
    storms (JTWC may leave only a stub in its active directory once a
    storm dissipates). One source per storm, so no duplicate cards in
    the sidebar.

    ``nhc_active_sids`` (from fetch_nhc_active_sids) is the authoritative
    NHC-advising membership and drives TWO mirror behaviors, both STATUS ONLY
    (tracks, points, ACE, and every season total are computed identically
    regardless): (1) the final-advisory RETIREMENT of is_active — see
    NHC_RETIRE_GRACE_H; (2) the PTC ACTIVATION — a designated DB/DS system
    NHC lists is promoted to is_active + is_ptc (a Potential Tropical Cyclone),
    which the nature gate would otherwise hide. Accepts a dict[sid->NHC
    classification] (current contract — the classification distinguishes a PTC
    from a TD) OR a bare set[sid] (legacy callers); membership works on both.
    None (fetch failed / caller offline) applies NEITHER and never changes
    which storms exist. This function also performs the PTC->invest HANDOFF: an
    invest superseded by an active designated storm (b-deck SPAWNINVEST link or
    coincident latest position) is dropped, so one system is never shown as both
    its invest (90L) and its designation (01L)."""

    # Shared IBTrACS-vs-live merge (ace_core): keep the source with more 6-hourly
    # obs per named storm. SAME function + same inputs as the ACE feed, so both
    # pick the same source -> identical canonical track per storm.
    ibtracs, live = merge_named_sources(ibtracs, live, name_col="NAME")

    # CROSS-SOURCE SID COLLISION (the WPAC provisional-twin case). With the
    # IBTrACS current-season sid remapped to the agency form (load_ibtracs_
    # current_year, via USA_ATCF_ID), a freshly-formed system appears in BOTH
    # sources under ONE sid while carrying DIFFERENT names — the live b-deck has
    # its JTWC number / JMA name (+ storm_num), IBTrACS still has it as an
    # UNNAMED provisional NR entry. merge_named_sources contests by NAME and
    # cannot reconcile that (the IBTrACS side is the UNNAMED placeholder it
    # deliberately skips), so the two reach the concat below under one sid.
    #
    # We keep the UNION of both sources (drop NEITHER) with LIVE ORDERED FIRST,
    # so drop_duplicates(keep="first") lets the live b-deck WIN every overlapping
    # synoptic fix while IBTrACS-only fixes still fill genuine gaps. Keeping the
    # union — rather than dropping the obs-lighter source — is load-bearing: the
    # designation carrier (live's storm_num + real/ATCF name) MUST survive so the
    # per-storm loop's `nums` is non-empty and the designation relabel fires
    # (10W / the JMA name), never a bare UNNAMED. Dropping live whenever IBTrACS
    # merely out-COUNTS it would discard that carrier and re-draw the very
    # UNNAMED ghost this fix removes — and IBTrACS legitimately out-counts live
    # during the fresh-designation window (b-deck starts at TCFA with 1-2 fixes),
    # for a dissipated storm JTWC has trimmed to a stub, or generally in WPAC
    # (IBTrACS carries the longer JMA-tracked pre-genesis phase). Live-first also
    # keeps the winning track pristine (b-deck tropical fixes beat IBTrACS's
    # lagged NR duplicates at shared times). PRE-REMAP this ordering is a no-op:
    # raw IBTrACS sids never collided with live sids, so no cross-source
    # (sid, time) pair existed to arbitrate.
    frames = [df for df in (live, ibtracs) if not df.empty]
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
    # PTC->invest HANDOFF accumulators (filled in the loop, applied after it).
    # superseding_invests: the 90-99 invest (letter, number)s that ACTIVE DESIGNATED
    # storms SPAWNED (b-deck SPAWNINVEST tag); invest_candidates: every invest's
    # (sid, number). An invest is dropped iff its number is one a live
    # designation spawned — see the dedup below. (A coincident-position fallback
    # was considered and REJECTED: it cannot tell a real same-system pair, whose
    # invest + designation share an identical track, apart from two unrelated
    # storms that merely overlap — the invest-ACE-guard fixtures are exactly
    # that pathological coincidence. The SPAWNINVEST link is authoritative and,
    # because it dedups by NUMBER, works even when the invest was sourced
    # outside parse_bdeck, e.g. the poller's knackwx invests.)
    # Superseding entries are (letter, number); letter is None when the
    # producer didn't know it (legacy feeds, unrecognized tokens) and the
    # match then falls back to number-only — the pre-letter behavior.
    superseding_invests: set[tuple] = set()
    invest_candidates: list[tuple[str, "int | None", "str | None"]] = []
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
        # Jangmi 2026 — last fix 25 kt, TS-nature). Both renderers draw
        # every active designated storm with the spinning glyph whose
        # letter/color come from current_category (a current TD wears the
        # blue "D" glyph). Gating on 34 kt would make designated TDs
        # silently drop off both the per-basin and home-page maps.
        # Weakening/dissipation is still caught by the nature gate: a
        # system that decays to a remnant low or goes extratropical flips
        # to DS/ET and falls out of "active".
        recent_obs = (len(points) > 0
                      and points[-1]["time"] >= active_cutoff)
        is_active = False
        is_ptc = False
        if len(points) > 0:
            last = points[-1]
            has_wind = pd.notna(last["wind_kt"]) and last["wind_kt"] > 0
            tropical = (last["nature"] or "") not in {"ET", "DS"}
            is_active = recent_obs and has_wind and tropical
        # FINAL-ADVISORY RETIREMENT (status only). The nature gate above
        # cannot see a storm whose b-deck simply STOPS (no terminal EX/DS
        # row), so without this a dissipated TD stays "active" until the
        # staleness window expires. A named AL/EP/CP storm (invests 90-99
        # exempt — CurrentStorms never lists them; WP/JTWC + IBTrACS-form
        # sids exempt — no CurrentStorms coverage) that is absent from a
        # CLEANLY-FETCHED CurrentStorms list and whose latest fix is older
        # than the grace window is no longer active. Track + ACE are
        # untouched: only this flag flips.
        if is_active and nhc_active_sids is not None:
            m = _NHC_SID_RE.match(str(sid).strip().upper())
            if (m and int(m.group(3)) < 90
                    and m.group(1) not in nhc_active_sids
                    and points[-1]["time"] <
                    now - dt.timedelta(hours=NHC_RETIRE_GRACE_H)):
                is_active = False
        # PTC RECOGNITION + ACTIVATION (status only) — the MIRROR of the
        # retirement above. A Potential Tropical Cyclone is a DESIGNATED AL/EP/CP
        # system (number 01-49, NOT a 90-99 invest) that NHC is actively
        # advising on while it is still a pre-genesis disturbance: its latest
        # b-deck dev-level is DB/LO -> the non-tropical "DS" nature, so the
        # `tropical` gate above leaves is_active=False even though NHC has issued
        # a full forecast/advisory + cone + watches. We RECOGNISE it on the
        # authoritative NHC-advising signal (membership in a CLEANLY-FETCHED
        # CurrentStorms list — the same source the retirement trusts) for a
        # designated number with a fresh fix + valid wind whose b-deck NATURE is
        # DB/DS OR whose NHC `classification` is a disturbance code, and:
        #   * set is_ptc=True (the marker + page then wear the INVEST visual
        #     identity — grey + red X — under the system's REAL designation), and
        #   * ACTIVATE it (is_active=True) when the nature gate had hidden it.
        # This NEVER touches invests (90-99 are never in CurrentStorms) and never
        # promotes a normal TC: a TD/TS/HU is neither DB/DS-natured nor
        # disturbance-classified, so is_ptc stays False and is_active is whatever
        # the normal gate already decided. Track + ACE untouched: a PTC accrues
        # no ACE (its DS fixes fail nature-eligibility) and counts as no category.
        if (recent_obs and len(points) > 0 and nhc_active_sids is not None):
            last = points[-1]
            has_wind = pd.notna(last["wind_kt"]) and last["wind_kt"] > 0
            m = _NHC_SID_RE.match(str(sid).strip().upper())
            if (m and int(m.group(3)) < 90 and has_wind
                    and m.group(1) in nhc_active_sids):
                last_nat = (last["nature"] or "").upper()
                nhc_cls = (nhc_active_sids.get(m.group(1))
                           if isinstance(nhc_active_sids, dict) else "") or ""
                if (last_nat in PTC_NATURES
                        or nhc_cls.upper() in PTC_CLASSIFICATIONS):
                    is_ptc = True
                    is_active = True
        # Invest = ATCF storm-number 90-99 (JTWC/NHC convention). Pulled
        # from any row in the group; IBTrACS rows have NaN and are ignored
        # since IBTrACS doesn't archive invests.
        nums = [p.get("storm_num") for p in points
                if p.get("storm_num") is not None
                and not (isinstance(p["storm_num"], float)
                         and math.isnan(p["storm_num"]))]
        is_invest = storm_is_invest(points)
        # Recent invest = invest with a fresh observation. JTWC/NHC cycle
        # 90-99 numbers across the season, so without this filter the
        # card grid accumulates ~10 stale invests by mid-season (most of
        # which never developed) that just clutter the inactive section.
        recent_invest = is_invest and recent_obs
        # ATCF id (e.g. "91W" / "92L" / "93E") for the invest renderer's
        # red-X label. Only set for invests; numbered TCs surface their
        # name via the spinning-icon label instead.
        atcf_id = None
        # The trailing letter comes from the storm's OWN SID basin token
        # ("NHC_CP902026" -> "C"), NOT the page basin's invest_letter: the EP
        # page also carries Central Pacific systems, which ATCF designates
        # with a "C", and the page-level "E" mislabeled them (the 90E/91E
        # home-map bug, 2026-07-13). IBTrACS-style SIDs (no basin token) keep
        # the page-basin fallback.
        own_letter = _sid_atcf_letter(sid) or basin_cfg.get("invest_letter", "")
        if is_invest and nums:
            atcf_id = f"{int(nums[0])}{own_letter}"
        elif is_ptc and nums:
            # A PTC wears its REAL designation ("01L"): a designated number
            # (small, so zero-pad to 2 digits to keep the leading zero), NOT a
            # 90-99 invest. Drives the invest-X label + the /cyclolab/{sid}/ id.
            atcf_id = f"{int(nums[0]):02d}{own_letter}"
        # Current intensity = SSHWS of the most recent observation
        last_wind = points[-1]["wind_kt"] if points else float("nan")
        current_cls = sshs_class(last_wind)

        # Name selection: prefer a real name over placeholders
        names = [p["NAME"] for p in points if p["NAME"]
                 and p["NAME"] not in {"UNNAMED", "INVEST", "NAMELESS"}]
        name = names[0] if names else (points[0]["NAME"] if points else "UNNAMED")
        # DESIGNATED-BUT-UNNAMED label. A JTWC/NHC-numbered system with no real
        # name yet — its "name" is the ATCF spelled-out cardinal ("TEN" for TD
        # 10W), a "#NN" parse_bdeck fallback, or a bare placeholder — surfaces
        # its "##<letter>" ATCF designation (10W), the label the invests already
        # wear and operators expect for a numbered depression. A genuine JMA/NHC
        # name (BAVI) is kept. Invests keep the invest_x path's atcf_id label and
        # PTCs keep their real designation, so both are excluded here; the
        # designation only replaces a non-name.
        if (not is_invest and not is_ptc and nums
                and (not _is_real_name(name) or _is_atcf_number_name(name))):
            # own_letter (the storm's SID-token letter, page fallback) keeps a
            # designated-but-unnamed CP system "01C" on the EP page — the same
            # wrong-page-letter class the invest atcf_id fix above closed.
            try:
                n0 = int(nums[0])
            except (TypeError, ValueError):
                n0 = 0
            desig = (f"{n0:02d}{own_letter}" if own_letter and 0 < n0 < 90
                     else short_id_from_storm_num(nums[0], basin_short))
            if desig:
                name = desig

        # PTC->invest handoff bookkeeping (applied after the loop). An ACTIVE
        # DESIGNATED storm contributes the invest numbers it spawned (b-deck
        # SPAWNINVEST tag -> point["spawn_invest"]); every invest registers its
        # number so the dedup can drop a superseded invest.
        spawn_pt = next((p for p in points
                         if p.get("spawn_invest") is not None
                         and not (isinstance(p["spawn_invest"], float)
                                  and math.isnan(p["spawn_invest"]))), None)
        spawn_num = int(spawn_pt["spawn_invest"]) if spawn_pt else None
        # The spawned invest's own trailing letter, when the producer tagged
        # it. Sanitized hard: DataFrame column alignment turns an absent field
        # into NaN, and legacy producers never set it at all.
        spawn_letter = None
        if spawn_pt is not None:
            raw = spawn_pt.get("spawn_invest_letter")
            if isinstance(raw, str) and raw.strip().upper() in _ATCF_LETTER_BASIN:
                spawn_letter = raw.strip().upper()
        if is_active and not is_invest and spawn_num is not None:
            superseding_invests.add((spawn_letter, spawn_num))
        if is_invest:
            invest_candidates.append(
                (sid, int(nums[0]) if nums else None, _sid_atcf_letter(sid)))
        # The sid of the invest this designated system spawned (e.g. AL01 ->
        # "NHC_AL902026"). Surfaced so the PTC's CycloLab page can fall back to
        # its spawning invest's formation.json (the NHC TWO odds live under the
        # invest area, not the designation). Same SID format as parse_bdeck —
        # including the invest's OWN basin token when the tag carried one.
        spawn_sid = (
            f"{basin_cfg['agency_name']}_"
            f"{_ATCF_LETTER_BASIN.get(spawn_letter, basin_cfg['short'].upper())}"
            f"{spawn_num:02d}{int(points[0]['season'])}"
            if spawn_num is not None else None)

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
            # A Potential Tropical Cyclone: designated + NHC-advised + still a
            # DB/DS disturbance. Drives the invest visual identity on the marker
            # and the PTC branch on the CycloLab page. Never true for invests.
            "is_ptc": bool(is_ptc),
            # The spawning invest's sid (None unless a SPAWNINVEST tag linked
            # one) - the PTC page reads its formation.json for the chance pill.
            "spawn_sid": spawn_sid,
            "recent_invest": bool(recent_invest),
            "atcf_id": atcf_id,
            "points": [_serialize_point(p) for p in points],
        })
    # PTC->invest HANDOFF (the 90L<-01L dedup). When a designated system is
    # active, the live invest it manifested as (same track, b-deck SPAWNINVEST
    # link) must NOT also show — else the map paints both 01L-X and 90L-X for
    # one system. Drop an invest iff its number is one an ACTIVE DESIGNATED
    # storm spawned. Only ever drops an invest in favour of an active designated
    # system; never touches designated-vs-designated. Basin-scoped (per basin).
    # (Letter-aware: "… to ep902026" drops 90E but never an unrelated 90C
    # sharing the page. A None letter on either side falls back to the
    # number-only match, i.e. exactly the pre-letter behavior.)
    if superseding_invests:
        drop_invest_sids = {
            sid_i for sid_i, num_i, letter_i in invest_candidates
            if num_i is not None and any(
                sn == num_i and (sl is None or letter_i is None
                                 or sl == letter_i)
                for sl, sn in superseding_invests)}
        if drop_invest_sids:
            storms = [s for s in storms if s["sid"] not in drop_invest_sids]
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
    # Designated TCs only - invests (ATCF 90-99, is_invest) AND Potential
    # Tropical Cyclones (is_ptc — designated but still a DB/DS disturbance NHC
    # is advising on) never count toward the named/category tallies or the
    # season ACE, even if a fix reached TS strength while still pre-genesis.
    # Their per-storm ace is already 0.0 by construction (storm_ace's invest
    # guard / DS nature-ineligibility); filtering here makes the COUNT rule just
    # as explicit, so a windy PTC can never inflate the named/category counts.
    tcs = [s for s in storms
           if not s.get("is_invest") and not s.get("is_ptc")]
    named = sum(1 for s in tcs if _sshs_rank(s["max_category"]) >= 1)  # TS+
    cat1plus = sum(1 for s in tcs if _sshs_rank(s["max_category"]) >= 2)  # C1+
    cat3plus = sum(1 for s in tcs if _sshs_rank(s["max_category"]) >= 4)  # C3+
    cat5 = sum(1 for s in tcs if s["max_category"] == "C5")
    # Season ACE via the single authority: sum of the (already rounded) per-storm
    # ACE values, so it equals the ACE feed's season ACE exactly.
    total_ace = season_ace([s["ace"] for s in tcs])
    return {
        "named": named,
        "cat1plus": cat1plus,
        "cat3plus": cat3plus,
        "cat5": cat5,
        "total_ace": total_ace,
    }



# ===========================================================================
# Global-map GeoJSON assembly - moved here from generate_tracks_plot.py
# (Phase 3, poller-primary storm-display) so the cron generator AND the
# streaming intensity poller emit the IDENTICAL FeatureCollection for
# /global_tracks.html and can never drift. Same relocation pattern as the
# feed-assembly move (commit a7719b5). Pure functions of the storms list;
# no clock, no I/O.
# ===========================================================================

def _split_at_antimeridian(coords: list[list[float]]) -> list[list[list[float]]]:
    """Split a [lon, lat] coordinate list into segments wherever successive
    longitudes jump > 180° (the dateline-crossing tell). MapLibre's
    renderWorldCopies handles infinite horizontal pan on its own, but the
    GeoJSON spec still requires LineStrings to not cross ±180° as a single
    feature — otherwise the renderer draws a horizontal line across the
    whole world. Each output segment has at least 2 points so it remains a
    valid LineString geometry.

    The split CARRIES the crossing point: the latitude where the great-circle
    leg meets ±180° is interpolated and appended to the outgoing segment as
    ±180 and prepended to the incoming one as ∓180, so the two halves abut
    exactly on the dateline. Without it each half stopped at its last real
    fix and the map showed a hole as wide as one 6-hourly leg — the visible
    "track break" on DOLPHIN (WP12 2026), whose fixes run -178.9 -> +179.8
    and left a 1.3° gap that read as missing data. Splitting is a rendering
    requirement, so it must not also LOOK like a gap in the observations; a
    genuine gap (a real reporting hole) still renders as a break, because
    only the ±180 leg gets the inserted vertex."""
    if len(coords) < 2:
        return []
    segments: list[list[list[float]]] = [[coords[0]]]
    for i in range(1, len(coords)):
        prev_lon, prev_lat = coords[i - 1][0], coords[i - 1][1]
        curr_lon, curr_lat = coords[i][0], coords[i][1]
        if abs(curr_lon - prev_lon) > 180:
            # Unwrap the destination into the previous fix's continuous frame
            # so the leg is the SHORT hop across the dateline, then solve for
            # the latitude at the meridian it actually crosses.
            shifted = curr_lon + (360.0 if curr_lon < prev_lon else -360.0)
            edge = 180.0 if shifted > prev_lon else -180.0
            span = shifted - prev_lon
            if not span:
                # +180 and -180 are the SAME meridian: the pair is 360 apart
                # numerically but coincident on the globe, so there is no leg
                # to bridge. Split (the raw LineString would still sweep the
                # world) but insert nothing - bridging it would emit a
                # segment running the full width of the map.
                segments.append([coords[i]])
                continue
            # Clamp so the crossing vertex can never be EXTRAPOLATED off the
            # leg. frac is naturally in [0,1] for longitudes in [-180,180];
            # a feed using the 0-360 convention (lon 185) would otherwise
            # place edge outside [prev, shifted] and invent a latitude
            # outside the two fixes entirely.
            frac = min(1.0, max(0.0, (edge - prev_lon) / span))
            lat_x = round(prev_lat + (curr_lat - prev_lat) * frac, 4)
            # Skip a duplicate vertex when a fix sits exactly on ±180.
            if [edge, lat_x] != segments[-1][-1]:
                segments[-1].append([edge, lat_x])
            nxt: list[list[float]] = []
            if [-edge, lat_x] != coords[i]:
                nxt.append([-edge, lat_x])
            nxt.append(coords[i])
            segments.append(nxt)
        else:
            segments[-1].append(coords[i])
    return [s for s in segments if len(s) >= 2]


def _clean_mslp(val) -> int | None:
    """Minimum sea-level pressure as a positive integer mb, or None.

    b-decks often omit pressure on weak/early fixes, where the upstream value
    is NaN or 0. Returning None there keeps the field out of the popup entirely
    (the JS omits the row) and, critically, never lets a NaN reach json.dumps -
    which would emit a literal ``NaN`` token and break the geojson's JSON.parse
    on the client. Purely a presentation/serialization guard; no wind/ACE path
    touches this."""
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if f != f or f <= 0:        # NaN (f != f) or non-positive -> absent
        return None
    return int(round(f))


def _last_point(storm: dict):
    pts = storm.get("points") or []
    return pts[-1] if pts else None


def _first_point(storm: dict):
    pts = storm.get("points") or []
    return pts[0] if pts else None


def _nm_between(lat1, lon1, lat2, lon2) -> float:
    """Great-circle nautical miles, antimeridian-safe."""
    import math
    dlon = abs(lon1 - lon2)
    if dlon > 180.0:                       # 179.7E vs -179.0 is 1.3 deg, not 358.7
        dlon = 360.0 - dlon
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(dlon) / 2) ** 2)
    return 3440.065 * 2 * math.asin(min(1.0, math.sqrt(a)))


# A designation that crosses a basin boundary is invisible to the per-basin
# retirement in merge_and_extract_storms: that runs inside ONE basin's frame,
# collecting superseding invests from designated storms in the SAME frame. When
# 92C (carried by the EP page as NHC_CP922026) crossed the dateline and became
# 12W/DOLPHIN (the WP page, JTWC_WP122026), the two never shared a frame, so no
# amount of correct SPAWNINVEST tagging could retire the invest. It survived
# only until the 24 h staleness window aged it out -- roughly half a day of the
# home map drawing one system as two: a red X beside a named storm on one track.
#
# The global map is the ONLY surface that sees both, so the cross-basin rule
# belongs here. The surviving sources give no explicit link (the WP b-deck
# carries no SPAWNINVEST, and knackwx's transitioned_from named 93C, an invest
# that exists in neither the CP decks nor tcvitals), so the link is established
# the way a forecaster reads it: CONTINUITY. An invest whose last fix sits
# within a short window and a short distance of a designated storm's FIRST fix
# is the same system, and the invest retires.
#
# Deliberately conservative -- it only ever drops an INVEST in favour of an
# ACTIVE DESIGNATED storm, never designated-vs-designated, and the thresholds
# are tight enough that two genuinely distinct systems are not merged.
HANDOFF_MAX_GAP_H = 12.0     # 2 synoptic cycles: designation follows the last invest fix
HANDOFF_MAX_NM = 300.0       # a designation does not relocate the centre by more


def cross_basin_superseded_sids(storms: list[dict]) -> set:
    """SIDs of invests that a designated storm in ANOTHER basin took over.

    Returns a set so callers can filter without caring how the match was made.
    """
    import datetime as _dt

    def _t(p):
        v = (p or {}).get("time")
        if isinstance(v, _dt.datetime):
            return v
        try:
            return _dt.datetime.fromisoformat(str(v).replace("Z", ""))
        except (TypeError, ValueError):
            return None

    designated = []
    for s in storms:
        if not s.get("is_active") or s.get("is_invest"):
            continue
        fp = _first_point(s)
        ft = _t(fp)
        if fp and ft is not None:
            designated.append((s, fp, ft))
    if not designated:
        return set()

    dropped = set()
    for s in storms:
        if not s.get("is_invest"):
            continue
        lp = _last_point(s)
        lt = _t(lp)
        if lp is None or lt is None:
            continue
        for d, fp, ft in designated:
            if (d.get("basin") or "") == (s.get("basin") or ""):
                continue                      # same basin: already handled upstream
            gap_h = (ft - lt).total_seconds() / 3600.0
            if not (-1.0 <= gap_h <= HANDOFF_MAX_GAP_H):
                continue                      # designation must FOLLOW the invest
            if _nm_between(float(lp["lat"]), float(lp["lon"]),
                           float(fp["lat"]), float(fp["lon"])) > HANDOFF_MAX_NM:
                continue
            dropped.add(s.get("sid"))
            break
    return dropped


def build_global_geojson(storms: list[dict]) -> dict:
    """Assemble the FeatureCollection consumed by MapLibre on
    /global_tracks.html.

    Three feature kinds are emitted:
      * "track" — one LineString per storm (split at the antimeridian),
        carries storm-level metadata for styling/hover.
      * "observation" — one Point per 6-hour fix, carrying intensity,
        pressure, time, and SSHWS class. The MapLibre `circle` layer
        styles these with the TAT palette.
      * "active_marker" — one Point per active storm/invest, carrying
        marker_type ("hurricane" for EVERY active designated storm's
        spinning glyph — current_category drives the letter/color, so a
        current TD wears a blue "D" glyph; "invest_x" for EVERY invest's
        red X). Rendered as HTML markers, not GL layers, so existing
        spin animations and label layouts work without WebGL plumbing.
    """
    features: list[dict] = []
    # One system, one marker: retire an invest that a designated storm in
    # another basin has taken over (see cross_basin_superseded_sids).
    superseded = cross_basin_superseded_sids(storms)
    for storm in storms:
        sid = storm.get("sid") or ""
        if sid in superseded and bool(storm.get("is_invest")):
            continue
        name = storm.get("name") or "UNNAMED"
        basin = storm.get("basin") or ""
        peak_kt = storm.get("peak_wind_kt")
        is_active = bool(storm.get("is_active"))
        is_invest = bool(storm.get("is_invest"))
        is_ptc = bool(storm.get("is_ptc"))
        designation = storm.get("atcf_id") or ""
        max_cls = storm.get("max_category") or "TD"
        points = storm.get("points") or []

        # Track LineString(s): split into one feature per dateline segment
        if len(points) >= 2:
            coords = [[float(p["lon"]), float(p["lat"])] for p in points]
            for seg in _split_at_antimeridian(coords):
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": seg},
                    "properties": {
                        "kind": "track",
                        "storm_id": sid,
                        "name": name,
                        "basin": basin,
                        "peak_intensity": max_cls,
                        "peak_kt": peak_kt,
                        "is_active": is_active,
                        "is_invest": is_invest,
                        # A PTC's track wears the invest visual identity (the
                        # MapLibre layer filters group it with invest tracks).
                        "is_ptc": is_ptc,
                        "designation": designation,
                    },
                })

        # Per-observation Points
        for p in points:
            wind = p.get("wind_kt")
            cls = p.get("cls") or "TD"
            nature = (p.get("nature") or "").upper()
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(p["lon"]), float(p["lat"])],
                },
                "properties": {
                    "kind": "observation",
                    "storm_id": sid,
                    "storm_name": name,
                    "basin": basin,
                    "intensity_kt": (None if wind is None else float(wind)),
                    # NaN-safe (b-decks omit pressure on weak fixes); None keeps
                    # a literal NaN out of the serialized geojson.
                    "mslp_mb": _clean_mslp(p.get("pressure_mb")),
                    "time_iso": p.get("t"),
                    "sshws_cat": cls,
                    "is_subtropical": (nature == "SS"),
                    "is_nontropical": (nature in {"ET", "DS", "DB", "LO"}),
                },
            })

        # Current-position marker — one Point at the latest position.
        # Two flavors, matching the per-basin SVG convention exactly:
        #   * "invest_x": is_invest, ACTIVE OR NOT. Small red glowing X
        #     with a red designation label to the right — the NHC
        #     convention for invest areas. One invest = one marker,
        #     regardless of how its latest fix happens to be coded (the
        #     old fork split active invests off to a big red "L", so two
        #     invests could wear two different icons purely on fix
        #     freshness/dev-level; the "L" path is retired — nothing
        #     non-invest ever used it). Per-basin emits this from
        #     render_tracks_svg's invest_current_positions second pass.
        #   * "hurricane": is_active AND NOT is_invest — EVERY designated
        #     storm, regardless of peak intensity. Spinning glyph + name;
        #     the letter/color inside come from current_category, so the
        #     marker is stage-driven where it matters (a current TD wears
        #     a blue glyph with "D"). The old "td_circle" tier (hollow
        #     ring for peak < 34 kt) is RETIRED: it keyed on PEAK wind,
        #     so a weakened storm (peaked TS, now TD) and a fresh TD at
        #     the same current stage wore different markers (the
        #     AMANDA-glyph vs TWO-E-ring inconsistency). Renderers keep a
        #     legacy fold (td_circle -> glyph) for geojson written by a
        #     pre-0.5.0 ace_core during the poller repin gap.
        #   * A POTENTIAL TROPICAL CYCLONE (is_ptc) is a designated system NHC
        #     is advising on while still a DB/DS disturbance. It is NOT an
        #     invest (number 01-49) and — per Andrew's 2026-07-14 marker rule,
        #     which RETIRES the earlier PTC-wears-invest-visuals design — it
        #     renders like every designated system: the intensity glyph
        #     (current_category letter/color), never the invest X. The ATCF
        #     NUMBER is the gate (wears_invest_x): 90-99 = X, 01-89 = glyph.
        #     is_ptc stays in the properties for the popup + its CycloLab page
        #     (which keeps the cone + advisories + Models a pure invest hides).
        # All live under kind="active_marker" so the JS marker iteration
        # loop picks them up uniformly; marker_type drives the rendered
        # shape.
        marker_type = None
        if wears_invest_x(storm):
            marker_type = "invest_x"
        elif is_active or is_ptc:
            marker_type = "hurricane"
        if marker_type and points:
            last = points[-1]
            current_kt = last.get("wind_kt")
            current_cls = storm.get("current_category") or "TD"
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(last["lon"]), float(last["lat"])],
                },
                "properties": {
                    "kind": "active_marker",
                    "storm_id": sid,
                    "name": name,
                    "designation": designation or name,
                    "current_intensity_kt": (None if current_kt is None
                                             else float(current_kt)),
                    "current_category": current_cls,
                    # Latest-fix MSLP from the SAME fix as current_intensity_kt
                    # (the last observation). None when the b-deck lacks it, so
                    # the popup omits the Pressure row rather than show "0 mb".
                    "current_mslp_mb": _clean_mslp(last.get("pressure_mb")),
                    "marker_type": marker_type,
                    # PTC vs invest are both invest_x; this lets a consumer
                    # (e.g. the popup) name a PTC by its real designation.
                    "is_ptc": is_ptc,
                    # Timestamp of the most recent observation, surfaced as
                    # "Last fix" in the active-marker hover/click popup.
                    "last_fix": last.get("t"),
                },
            })

    return {"type": "FeatureCollection", "features": features}
