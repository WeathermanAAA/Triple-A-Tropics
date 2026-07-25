"""NCEP tcvitals — the JTWC intensity source that survives ATCF distribution.

WHY THIS EXISTS
---------------
JTWC's public **a-decks are gone** (every ``a{basin}{nn}{yyyy}.dat`` path 404s
as of 2026-07), so the CARQ working-best-track content that used to carry the
current-cycle fix is no longer distributed over ATCF at all. The **b-decks are
still reachable** through our proxy/natyphoon mirror chain, but they are
*post-analysis* products: they land ~one synoptic cycle behind real time.

tcvitals rides a different pipe entirely. JTWC hands NCEP a vitals record per
storm per cycle so the global models can be **initialized** with a storm at the
right place and intensity; NCEP publishes that file as part of the GFS run.
NCAR/RAL documents that the open JTWC b-decks were themselves *constructed*
from these same tcvitals files. Because it rides model-init rather than ATCF
distribution, it survived the a-deck shutdown — and because it is produced
*for* the cycle rather than after it, it leads the b-deck.

Measured 2026-07-25 against the live 11W (Noul) b-deck:

    fix        b-deck            tcvitals
    2026072500 70 kt / 980 mb    36 m/s -> 70 kt / 980 mb    (exact)
    2026072506 75 kt / 975 mb    38 m/s -> 75 kt / 975 mb    (exact)
    2026072512 80 kt / 970 mb    41 m/s -> 80 kt / 970 mb    (exact)
    2026072518 (absent)          43 m/s -> 85 kt / 967 mb    <- the lead

That last row is the whole point: 85 kt is Category 2, and the site was
rendering C1 off the 12Z b-deck because 18Z had not been written yet.

THE ONE RULE: B-DECK WINS WHERE IT EXISTS
-----------------------------------------
Across all 268 fixes where a 2026 WP b-deck and tcvitals cover the SAME
timestamp, wind agrees exactly on 78.7% and is within +/-5 kt on 99.3%. The
disagreements are NOT decode error — they are concentrated in OLDER fixes,
because JTWC *revises* the b-deck in post-analysis while tcvitals preserves the
original real-time operational estimate. At the leading edge the two are
identical (every one of Noul's paired fixes above matches to the kt and the mb).

So tcvitals is used strictly to **extend past the b-deck's newest fix**, never
to overwrite a fix the b-deck already carries (``prefer_bdeck``). That keeps
the revised post-analysis track authoritative, keeps the full 34/50/64 kt radii
the b-deck carries, and still puts the current cycle on the map hours early.

WHAT TCVITALS CANNOT GIVE US
----------------------------
* **50 kt and 64 kt wind radii.** JTWC records are the 95-column *record type
  1*, which carries the 34 kt quadrant radii and nothing further (verified:
  857/857 JTWC records in the 2026 combined file and 1995/1995 parseable ones
  in 2025 are 95 columns; only NHC emits the 150-column extended record with
  R50/R64). ``r50_*``/``r64_*`` are therefore emitted as None = absent, exactly
  as ``parse_bdeck`` marks a threshold with no row. They are recovered from the
  b-deck as soon as it catches up.
* **A storm-nature / development-level code.** There is no NATURE field. See
  ``_NATURE_NOTE`` for the rule we apply and why it is sound.
* **Anything after JTWC stops issuing vitals** — they cease once a system moves
  inland without expected re-emergence, or after extratropical transition. The
  feed simply *stops carrying the storm*; there is no terminal marker. We never
  synthesize a fix to fill that gap, so a storm ages out of the active window on
  its real last timestamp instead of freezing at its last intensity.

UNITS (all verified field-by-field against the 11W b-deck, 2026-07-25 12Z)
    lat/lon    tenths of a degree, hemisphere suffix   -> degrees
    vmax       whole m/s                               -> kt (see _snap_kt)
    pcen/penv  whole hPa                               -> hPa (unchanged)
    rmw/rlci   whole km                                -> nm  (/1.852)
    r34 quads  whole km                                -> nm  (/1.852)

Format spec: https://www.emc.ncep.noaa.gov/mmb/data_processing/tcvitals_description.htm
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable, Optional

from . import (
    RADII_COLS,
    RADII_QUADS,
    SIX_HOURLY,
    _ATCF_BASIN_LETTER,
    _is_atcf_number_name,
    _PLACEHOLDER_NAMES,
)

# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

#: 1 international nautical mile in km. tcvitals distances are whole km; every
#: ATCF consumer downstream (and ``parse_bdeck``) speaks nautical miles.
KM_PER_NM = 1.852

#: m/s -> kt. The exact factor (3600/1852), not the 1.94 shorthand — the
#: rounding in ``_snap_kt`` is only safe against the exact value.
KT_PER_MPS = 3600.0 / 1852.0

#: tcvitals writes -99 / -999 (and -9 in some quadrant cells) for "missing".
#: Any value <= this sentinel threshold is missing, never a real measurement.
_MISSING_MAX = -1


def _utcnow() -> dt.datetime:
    """Naive UTC now. Naive on purpose: every timestamp in this codebase
    (parse_bdeck, parse_tcvitals, parse_atcg) is a naive UTC datetime, and
    mixing in an aware one makes every comparison raise."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _snap_kt(mps: float) -> int:
    """Whole m/s -> the kt value JTWC actually issued.

    JTWC issues intensities on a **5 kt grid** and NCEP stores them as whole
    m/s, which is a lossy round trip (1 m/s ~ 1.94 kt, so each 5 kt bin maps
    from 2-3 distinct integers). Converting and snapping back to the nearest
    5 kt inverts it exactly: 41 m/s -> 79.7 -> 80 kt, 43 -> 83.6 -> 85 kt,
    38 -> 73.9 -> 75 kt, 33 -> 64.1 -> 65 kt.

    This matters at category boundaries. C2 begins at 83 kt, and JTWC never
    issues 82/83/84 — snapping keeps us on the agency's own grid so the
    category is exactly the one JTWC assigned, rather than a rounding artifact
    of the m/s storage. Verified against the b-deck on every paired 2026 WP fix.
    """
    return int(5 * round(mps * KT_PER_MPS / 5.0))


def _km_to_nm(km: float) -> int:
    """Whole km -> whole nautical miles (b-deck units). Exact on real data:
    the 11W 34 kt quadrants 222/204/167/195 km round-trip to the b-deck's
    120/110/90/105 nm."""
    return int(round(km / KM_PER_NM))


# ---------------------------------------------------------------------------
# Record geometry (fixed-width; 1-indexed columns per the EMC spec)
# ---------------------------------------------------------------------------

#: (start, end) inclusive 1-indexed column spans of record type 1. Slicing by
#: column rather than whitespace-splitting is deliberate: storm names contain
#: no spaces today but the spec pads them into a fixed field, and a blank
#: numeric cell would silently shift a split-based parse by one field.
_COLS = {
    "center":  (1, 4),
    "stormid": (6, 8),
    "name":    (10, 18),
    "date":    (20, 27),
    "time":    (29, 32),
    "lat":     (34, 37),
    "lon":     (39, 43),
    "dir":     (45, 47),
    "speed":   (49, 51),
    "pcen":    (53, 56),
    "penv":    (58, 61),
    "rlci":    (63, 66),
    "vmax":    (68, 69),
    "rmw":     (71, 73),
    "r34ne":   (75, 78),
    "r34se":   (80, 83),
    "r34sw":   (85, 88),
    "r34nw":   (90, 93),
    "depth":   (95, 95),
}

#: Shortest record we will look at. Type-1 records are exactly 95 columns;
#: trailing whitespace is often trimmed by the archive, so accept anything that
#: reaches the last 34 kt quadrant and treat the depth flag as optional.
_MIN_LEN = 93


def _field(line: str, key: str) -> str:
    a, b = _COLS[key]
    return line[a - 1:b].strip()


def _int_or_none(raw: str) -> Optional[int]:
    """Whole-number cell -> int, or None when blank / non-numeric / a missing
    sentinel (-99, -999)."""
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return None
    return None if v <= _MISSING_MAX else v


# ---------------------------------------------------------------------------
# Basin routing
# ---------------------------------------------------------------------------

#: tcvitals storm-id trailing letter -> the ATCF basin token ``parse_bdeck``
#: builds SIDs from. JTWC splits the North Indian into Arabian Sea (A) and Bay
#: of Bengal (B) and the Southern Hemisphere into South Indian (S) and South
#: Pacific (P); ATCF collapses each pair to one token (IO, SH). The SH storm
#: NUMBER is shared across S and P (2026 ran ...13P, 14S, 15S, 16P...), so
#: collapsing them onto SH cannot collide.
LETTER_TO_ATCF = {
    "L": "AL", "E": "EP", "C": "CP", "W": "WP",
    "A": "IO", "B": "IO",
    "S": "SH", "P": "SH",
}

#: Basin ``short`` -> the tcvitals letters that belong to that page. The EP page
#: also carries the Central Pacific (see the BASINS comment in
#: generate_tracks_plot.py), matching ``atcf_patterns_extra``.
BASIN_LETTERS = {
    "wp": ("W",),
    "al": ("L",),
    "ep": ("E", "C"),
    "io": ("A", "B"),
    "sh": ("S", "P"),
}

_NATURE_NOTE = """\
tcvitals carries NO nature / development-level field, and we do not invent one.

An earlier draft of this module assigned every tcvitals fix "TS" on the theory
that JTWC only issues vitals for systems it is warning on. That reasoning is
sound as far as it goes, but it is still a GUESS dressed as data: it would have
silently accrued ACE for fixes whose type we had not actually observed, and the
one case it gets wrong — the hours around extratropical transition — is exactly
the case ACE is most sensitive to.

So the parser emits ``nature = ace_nature = None``: INDETERMINATE. A fix with a
None nature cannot pass ``nature_eligible`` (None -> "" is accepted only under
the provisional-data escape hatch, which the ACE writers disable for this
source), so an unresolved tcvitals fix is never counted.

Type is resolved by the SECOND leg — ``ace_core.jtwc_warnings`` — and stamped on
by ``resolve_fix_types``. See that module for how a JTWC warning establishes
tropical status and how the FINAL WARNING ends it.
"""

#: The nature an unresolved tcvitals fix carries. See ``_NATURE_NOTE``.
#:
#: This is an EXPLICIT SENTINEL STRING, not None, and that choice is load-bearing.
#: An earlier version used None and was silently wrong: assigning a column of
#: ``[None, None, "TS"]`` makes pandas coerce the Nones to float ``nan``, and
#: ``nature_eligible`` maps nan -> "" -> which the provisional-data escape hatch
#: ACCEPTS. Every indeterminate fix would have quietly counted toward ACE — the
#: exact silent-counting failure this whole leg exists to prevent.
#:
#: "IND" is in no basin's ACE_NATURES and is not "" or "NR", so it fails
#: ``nature_eligible`` under every basin and both provisional settings, by
#: construction rather than by the absence of a value surviving a dtype coercion.
#: ``tests/test_tcvitals.py`` pins this.
NATURE_INDETERMINATE = "IND"

#: Backwards-compatible alias.
TCVITALS_NATURE = NATURE_INDETERMINATE

# --- type_status vocabulary (set by resolve_fix_types) ---------------------
#: A JTWC warning covers this exact synoptic hour. Type is observed.
TYPE_OBSERVED = "observed"
#: The fix PREDATES the warning we hold, but falls inside the span that
#: warning's sequence number accounts for (NR *n* implies *n-1* earlier
#: 6-hourly warnings), so the system was under warning at the time. Distinct
#: from ``observed`` on purpose: we did not read a bulletin for this hour, we
#: inferred coverage from the sequence. Counted, but labelled for what it is.
TYPE_WARNED = "warned"
#: No warning at this hour, but the storm's newest warning is in force (not
#: final) and within ``WARNING_CARRY_HOURS``. Type is carried forward.
TYPE_CARRIED = "carried"
#: The storm is past its FINAL WARNING. JTWC has stopped; so do we.
TYPE_ENDED = "ended"
#: Nothing establishes the type. Never counted, always surfaced.
TYPE_INDETERMINATE = "indeterminate"

#: How far past its warning a fix may sit and still be covered by it.
#:
#: JTWC warns on a 6-hourly cycle and issues each warning ~3 h after its
#: synoptic hour, so tcvitals for a cycle routinely lands BEFORE that cycle's
#: warning: at 20:30Z on 2026-07-25 we held the 18Z tcvitals fix but only the
#: 12Z warning. One cycle of carry is what keeps real-time ACE from lagging a
#: full warning cycle behind the intensity we are already displaying.
#:
#: Carry is an inference, but not a guess: for the fix to exist at all JTWC had
#: to supply a vitals record for that hour, which they only do for systems they
#: are warning on, AND the last warning we hold is explicitly non-final. Two
#: independent signals, both pointing the same way. It is still tallied
#: separately and reported (see ``type_summary``) so it is never silent.
WARNING_CARRY_HOURS = 6.0

#: ``source`` column value, mirroring parse_bdeck's ``live-{agency}`` scheme.
TCVITALS_SOURCE = "live-tcvitals"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_tcvitals(text: str, season: int, basin_cfg: dict,
                   center: Optional[str] = None):
    """Parse an NCEP tcvitals file into ``parse_bdeck``'s DataFrame schema.

    Emitting the *identical* schema is the whole integration strategy: every
    downstream consumer (``merge_named_sources``, ``merge_and_extract_storms``,
    ``storm_ace``, the marker fork, the feed writers) then works on tcvitals
    rows with no changes at all.

    Columns: SID, NAME, season, time, lat, lon, wind_kt, pressure_mb, nature,
    ace_nature, source, storm_num, spawn_invest, spawn_invest_letter, and the
    12 ``RADII_COLS``. Of the radii only ``r34_*`` are ever populated — see the
    module docstring; ``r50_*``/``r64_*`` stay None (absent), the same marker
    ``parse_bdeck`` uses for a threshold with no row.

    Rows are kept only when they match this basin's letters (``BASIN_LETTERS``)
    and, when ``center`` is given, that issuing center. Records are 6-hourly
    synoptic by construction, but the hour is still gated on ``SIX_HOURLY`` so
    an off-cycle special record can never enter the ACE fix set.
    """
    import pandas as pd

    short = str(basin_cfg.get("short") or "").strip().lower()
    letters = set(BASIN_LETTERS.get(short, ()))
    agency = str(basin_cfg.get("agency_name") or "").strip()
    want_center = (center or "").strip().upper() or None

    rows: list[dict] = []
    # (SID, time) already emitted -> index, so a file that repeats a fix (the
    # combined archive concatenates per-cycle drops) yields ONE row. Idempotent
    # by construction: re-reading the same record set is a no-op.
    seen: dict[tuple, int] = {}

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n").rstrip()
        if len(line) < _MIN_LEN:
            continue
        if want_center and _field(line, "center").upper() != want_center:
            continue

        sid_raw = _field(line, "stormid").upper()
        m = re.fullmatch(r"(\d{2})([A-Z])", sid_raw)
        if not m:
            continue
        storm_num, letter = int(m.group(1)), m.group(2)
        if letters and letter not in letters:
            continue
        atcf_token = LETTER_TO_ATCF.get(letter)
        if atcf_token is None:
            continue

        try:
            t = dt.datetime.strptime(
                _field(line, "date") + _field(line, "time"), "%Y%m%d%H%M")
        except ValueError:
            continue
        if t.hour not in SIX_HOURLY or t.minute != 0:
            continue
        # The combined archive is a whole-year file; keep only this season.
        if t.year != int(season):
            continue

        ll = _parse_latlon(_field(line, "lat"), _field(line, "lon"))
        if ll is None:
            continue
        lat, lon = ll

        mps = _int_or_none(_field(line, "vmax"))
        wind_kt = float(_snap_kt(mps)) if mps is not None else float("nan")
        pcen = _int_or_none(_field(line, "pcen"))
        pressure_mb = float(pcen) if pcen is not None else float("nan")

        name = _clean_name(_field(line, "name"), storm_num, letter)

        rec = {
            "SID": f"{agency}_{atcf_token}{storm_num:02d}{int(season)}",
            "NAME": name,
            "season": int(season),
            "time": t,
            "lat": lat,
            "lon": lon,
            "wind_kt": wind_kt,
            "pressure_mb": pressure_mb,
            "nature": NATURE_INDETERMINATE,
            "ace_nature": NATURE_INDETERMINATE,
            "source": TCVITALS_SOURCE,
            "storm_num": storm_num,
            # The JTWC designation ("11W"), which is how the warnings leg names
            # a storm — the join key for resolve_fix_types.
            "atcf_short": f"{storm_num:02d}{letter}",
            # Set by resolve_fix_types once the warnings leg has spoken. Until
            # then every tcvitals fix is honestly unresolved.
            "type_status": TYPE_INDETERMINATE,
            # tcvitals has no SPAWNINVEST tag; the b-deck remains the only
            # source of the designated-system -> invest handoff link.
            "spawn_invest": None,
            "spawn_invest_letter": None,
        }
        for col in RADII_COLS:
            rec[col] = None
        # 34 kt quadrants only (record type 1). A quadrant that is present but
        # zero is a real "no extent"; -999 is missing and stays None.
        quads = [_int_or_none(_field(line, f"r34{q}")) for q in RADII_QUADS]
        if any(q is not None for q in quads):
            for q, val in zip(RADII_QUADS, quads):
                rec[f"r34_{q}"] = _km_to_nm(val) if val is not None else 0
        # Not part of parse_bdeck's schema, but free and useful: radius of
        # maximum wind, in nautical miles like every other ATCF distance.
        rmw = _int_or_none(_field(line, "rmw"))
        rec["rmw_nm"] = _km_to_nm(rmw) if rmw is not None else None

        key = (rec["SID"], t)
        if key in seen:
            rows[seen[key]] = rec      # later drop of the same fix supersedes
            continue
        seen[key] = len(rows)
        rows.append(rec)

    return pd.DataFrame(rows)


def _parse_latlon(lat_raw: str, lon_raw: str) -> Optional[tuple]:
    """tcvitals lat/lon: tenths of a degree with a hemisphere suffix.
    '218N' -> 21.8, '1159E' -> 115.9, '1359W' -> -135.9."""
    def _one(raw: str, pos: str, neg: str) -> Optional[float]:
        raw = (raw or "").strip().upper()
        if len(raw) < 2 or raw[-1] not in (pos, neg):
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
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 360.0):
        return None
    return lat, lon


def _clean_name(raw: str, storm_num: int, letter: str) -> str:
    """tcvitals name field -> the label the rest of the site expects.

    Mirrors ``parse_bdeck``: a real name passes through; the placeholders
    ("INVEST", blank) and the spelled-out cardinal an unnamed designation wears
    ("ELEVEN" for 11W, before it became NOUL) are NOT names, and fall back to
    the ATCF id for invests ("93W") or parse_bdeck's "#NN" for a designated but
    still-unnamed system."""
    n = (raw or "").strip().upper()
    if n and n not in _PLACEHOLDER_NAMES and not _is_atcf_number_name(n):
        return n
    if storm_num >= 90:
        return f"{storm_num}{letter}"
    return f"#{storm_num:02d}"


# ---------------------------------------------------------------------------
# Source precedence
# ---------------------------------------------------------------------------

def prefer_bdeck(bdeck_df, tcv_df):
    """Drop every tcvitals fix the b-deck already covers, and return the
    tcvitals rows that genuinely EXTEND it.

    This is the one rule from the module docstring, in code. The b-deck is
    JTWC's revised post-analysis and carries 50/64 kt radii; tcvitals is the
    original real-time estimate and carries only 34 kt. Where both describe the
    same (storm, hour) the b-deck wins — measured, not assumed: the pairs that
    disagree are all older fixes JTWC has since revised, while the leading edge
    matches exactly.

    Matching is on (SID, time), the same key ``parse_bdeck`` dedups on, so a
    storm is never double-counted in ACE or drawn twice on the map.
    """
    if tcv_df is None or getattr(tcv_df, "empty", True):
        return tcv_df
    if bdeck_df is None or getattr(bdeck_df, "empty", True):
        return tcv_df
    if "SID" not in bdeck_df.columns or "time" not in bdeck_df.columns:
        return tcv_df
    have = set(zip(bdeck_df["SID"], bdeck_df["time"]))
    if not have:
        return tcv_df
    keep = [k not in have for k in zip(tcv_df["SID"], tcv_df["time"])]
    return tcv_df[keep].reset_index(drop=True)


def resolve_fix_types(tcv_df, warnings: Iterable[dict],
                      now: Optional[dt.datetime] = None,
                      carry_hours: float = WARNING_CARRY_HOURS,
                      count_carried: bool = True):
    """THE JOIN. Stamp each tcvitals fix with a type from the warnings leg.

    ``tcv_df`` is ``parse_tcvitals`` output; ``warnings`` is an iterable of
    merged warning dicts from ``ace_core.jtwc_warnings`` (``merge_slot`` output,
    already passed through ``select_current`` so stale slots are gone). Joined
    on **storm id + synoptic time**: tcvitals supplies the numbers, warnings
    supply the type.

    Sets ``type_status`` per fix and, where the type resolves to tropical,
    fills ``nature``/``ace_nature`` so the fix passes the normal ACE gate. Where
    it does NOT resolve, nature stays None and the fix cannot accrue ACE — it is
    neither silently counted nor silently dropped, it is counted as
    indeterminate in ``type_summary`` and surfaced.

    Precedence per fix, given the storm's newest warning ``w``:

    ``ended``          ``w`` is a FINAL WARNING and the fix is after it. This is
                       the ACE stop condition. Never counted, even if tcvitals
                       keeps carrying the storm (it usually does not — JTWC
                       stops vitals at the same point, which is the corroborating
                       signal, not the primary one).
    ``observed``       ``w`` covers this exact synoptic hour, or the fix predates
                       ``w`` and ``w`` is not the first warning (warning NR *n*
                       implies *n-1* earlier warnings on the same system, so the
                       storm was under warning then too). Counted.
    ``carried``        Fix is within ``carry_hours`` after an in-force warning.
                       Counted when ``count_carried``; tallied separately.
    ``indeterminate``  No warning for this storm, an unrecognised dev level, or
                       the fix is further out than the carry window. Not counted.
    """
    if tcv_df is None or getattr(tcv_df, "empty", True):
        return tcv_df
    now = now or _utcnow()

    # Newest warning per storm id. A slot can only hold one bulletin, but two
    # slots occasionally carry the same storm mid-rotation; the later one wins.
    by_id: dict[str, dict] = {}
    for w in warnings or ():
        sid = str(w.get("atcf_id") or "").upper()
        wt = w.get("time")
        if not sid or wt is None:
            continue
        prev = by_id.get(sid)
        if prev is None or wt > prev["time"]:
            by_id[sid] = w

    out = tcv_df.copy()
    statuses, natures = [], []
    for short, t in zip(out["atcf_short"], out["time"]):
        w = by_id.get(str(short).upper())
        status, nature = TYPE_INDETERMINATE, None
        if w is not None:
            wt = w["time"]
            dh = (t - wt).total_seconds() / 3600.0
            if w.get("is_final") and dh > 0:
                status = TYPE_ENDED
            elif w.get("nature") is None:
                status = TYPE_INDETERMINATE      # unrecognised dev level
            elif dh == 0:
                status, nature = TYPE_OBSERVED, w["nature"]
            elif dh < 0:
                # Fix predates the warning we hold. Warning NR n implies n-1
                # earlier 6-hourly warnings, so the system was already under
                # warning back through roughly (n-1)*6 hours. JTWC's cadence is
                # 6-hourly; the occasional special/intermediate bulletin makes
                # this an upper bound on the span, which is why the status is
                # ``warned`` (coverage inferred from the sequence number) and
                # not ``observed`` (a bulletin actually read for that hour).
                nr = w.get("warning_nr") or 1
                if -dh <= (nr - 1) * 6.0:
                    status, nature = TYPE_WARNED, w["nature"]
            elif dh <= carry_hours:
                status, nature = TYPE_CARRIED, w["nature"]
        if status == TYPE_CARRIED and not count_carried:
            nature = None
        statuses.append(status)
        # Never write None into the frame — see NATURE_INDETERMINATE.
        natures.append(nature or NATURE_INDETERMINATE)

    out["type_status"] = statuses
    out["nature"] = natures
    out["ace_nature"] = natures
    return out


def is_resolved(nature) -> bool:
    """True if ``nature`` is a real, ACE-considerable nature rather than the
    indeterminate sentinel (or a null that a dtype coercion produced)."""
    if nature is None:
        return False
    if isinstance(nature, float) and nature != nature:      # NaN
        return False
    return str(nature).strip().upper() not in ("", NATURE_INDETERMINATE)


def type_summary(tcv_df) -> dict:
    """``{status: count}`` over resolved tcvitals fixes, plus ``ace_eligible``
    (fixes whose type resolved to something ACE can count).

    This is what makes the honesty requirement operational — a run prints it,
    and the feed carries it, so "3 fixes we could not type" is visible instead
    of being quietly folded into a total."""
    if tcv_df is None or getattr(tcv_df, "empty", True):
        return {}
    counts: dict[str, int] = {}
    for s in tcv_df.get("type_status", []):
        counts[str(s)] = counts.get(str(s), 0) + 1
    counts["ace_eligible"] = int(
        sum(1 for n in tcv_df.get("ace_nature", []) if is_resolved(n)))
    return counts


def coverage_report(bdeck_df, tcv_kept) -> dict:
    """Per-storm summary of what tcvitals added on top of the b-deck — the
    honest accounting a run prints, and what makes a silently-degrading source
    visible instead of invisible.

    Returns ``{sid: {"bdeck_last": t|None, "tcvitals_added": n,
    "tcvitals_last": t|None, "extends_hours": float|None}}``."""
    import pandas as pd  # noqa: F401  (schema comes from the callers' frames)

    out: dict[str, dict] = {}
    b_last: dict[str, object] = {}
    if bdeck_df is not None and not getattr(bdeck_df, "empty", True):
        for sid, grp in bdeck_df.groupby("SID"):
            b_last[str(sid)] = grp["time"].max()
    if tcv_kept is not None and not getattr(tcv_kept, "empty", True):
        for sid, grp in tcv_kept.groupby("SID"):
            sid = str(sid)
            last = grp["time"].max()
            prev = b_last.get(sid)
            out[sid] = {
                "bdeck_last": prev,
                "tcvitals_added": int(len(grp)),
                "tcvitals_last": last,
                "extends_hours": (
                    (last - prev).total_seconds() / 3600.0
                    if prev is not None else None),
            }
    for sid, last in b_last.items():
        out.setdefault(sid, {"bdeck_last": last, "tcvitals_added": 0,
                             "tcvitals_last": None, "extends_hours": None})
    return out
