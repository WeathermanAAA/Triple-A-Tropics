"""JTWC public warning text — the storm TYPE that tcvitals cannot supply.

WHY THIS EXISTS
---------------
``ace_core.tcvitals`` recovers JTWC's numbers (position, 1-min wind, MSLP, RMW,
34 kt radii) from NCEP's model-init pipeline. What it cannot recover is the
**development level**: the tcvitals record has no nature/type field at all. ACE
counts only tropical and subtropical fixes, so without a type there is no
honest ACE — this leg is the actual blocker, not the intensity.

JTWC's public warning text carries it, plus everything tcvitals is missing:

    SUBJ/TYPHOON 11W (NOUL) WARNING NR 010//
       MAX SUSTAINED WINDS BASED ON ONE-MINUTE AVERAGE      <- units, stated
       MAX SUSTAINED WINDS - 080 KT, GUSTS 100 KT           <- native kt
       RADIUS OF 064 KT WINDS - 035 NM NORTHEAST QUADRANT   <- R64, R50, R34
       ...

and, at the end of a storm's life, the ACE stop condition:

    THIS IS THE FINAL WARNING ON THIS SYSTEM BY THE JOINT TYPHOON WRNCEN ...

VERIFIED ENDPOINTS (2026-07-25)
-------------------------------
Two independent routes, neither part of the EMC ATCF shutdown:

* ``tgftp.nws.noaa.gov/data/raw/wt/wt{XX}{NN}.pgtw..txt`` — NOAA's public raw
  bulletin feed, carrying JTWC's WMO products verbatim. **This is the primary**:
  it is NOAA infrastructure, so it answers from cloud/CI address space, where
  ``metoc.navy.mil`` has historically 403'd (see the JTWC note in CLAUDE.md).
* ``www.metoc.navy.mil/jtwc/products/{basin}{nn}{yy}web.txt`` — JTWC's own copy.
  Byte-identical content. The surrounding site is JS-walled and ``jtwc.html``
  403s, but the ``.txt`` products themselves are plain files and fetch fine when
  the address space is not blocked. Kept as the secondary.

Bulletin families (``XX`` = basin, ``NN`` = a SLOT index, not a storm number):
    wtpn = NW Pacific    wtio = N Indian    wtps = S Pacific    wtxs = S Indian
    ``NN`` 21-24  Tropical Cyclone Formation Alert (invests; no fix data)
    ``NN`` 31-35  full warning prose   <- dev level in SUBJ, FINAL WARNING text
    ``NN`` 51-54  ATCG MIL fixed-field <- machine-readable numbers + full stamp

THE SLOT TRAP (this bit is load-bearing)
----------------------------------------
tgftp NEVER clears a slot. It holds the last bulletin written there, forever.
Sampled live on 2026-07-25, ``wtpn51`` held the current 11W/NOUL warning while
``wtpn53`` still held 25W/NEOGURI from 2025-09-29 and ``wtpn54`` held 27W/USAGI
from **2024**-11-16. A poller that trusts "slot 3x exists" as "storm is active"
will resurrect a two-year-old typhoon.

The ATCG (5x) line is the defence: it begins with a full ``YYYYMMDDHH`` synoptic
stamp, where the prose (3x) header only carries ``DDHHMM`` with no month or
year. ``parse_atcg`` reads that stamp and ``select_current`` refuses anything
outside the caller's freshness window. Never gate on slot presence.

TYPE RESOLUTION — WHAT A WARNING ACTUALLY PROVES
------------------------------------------------
JTWC has no subtropical and no extratropical warning class. It issues warnings
on tropical cyclones and it issues a FINAL WARNING at the moment the system
dissipates, moves inland without expected re-emergence, or completes
extratropical transition. So an in-force warning is direct evidence of tropical
status at its synoptic hour, and the final warning is the stop condition — this
is a documented semantic, not an inference from wind.

The dev level in SUBJ (TROPICAL DEPRESSION / TROPICAL STORM / TYPHOON / SUPER
TYPHOON / the generic TROPICAL CYCLONE used in the SH and N Indian) is kept as
the display label and mapped to a NATURE for the ACE gate. Observed vocabulary
is in ``DEV_LEVELS``; an unrecognised phrase resolves to indeterminate rather
than defaulting to tropical.

WHAT THIS LEG STILL CANNOT DO
-----------------------------
tgftp holds only the LATEST bulletin per slot, so there is no warning history.
Type for a fix from an earlier storm cannot be recovered here — it comes from
the b-deck's own dev-level column, which remains authoritative wherever the
b-deck reaches. This leg resolves the leading edge, which is the only place the
b-deck does not reach.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

#: NOAA's raw bulletin feed. PRIMARY — answers from CI address space.
TGFTP_BASE = "https://tgftp.nws.noaa.gov/data/raw/wt"

#: JTWC's own product directory. SECONDARY — same bytes, blocked from some nets.
METOC_BASE = "https://www.metoc.navy.mil/jtwc/products"

#: WMO bulletin basin tokens -> the site basin ``short`` they feed.
#: JTWC splits the Southern Hemisphere into South Pacific (ps) and South Indian
#: (xs); both land on our ``sh``. N Indian (io) covers Arabian Sea + Bay of
#: Bengal. NOTE these are the SLOT's nominal basin — the storm id inside the
#: bulletin is authoritative (``wtxs31`` was observed carrying 27P, a South
#: Pacific storm), so routing is always done on the parsed id, never the token.
BULLETIN_BASINS = {
    "pn": "wp",
    "io": "io",
    "ps": "sh",
    "xs": "sh",
}

#: Slot indices to sweep per basin token. 3x = prose (dev level + final
#: warning), 5x = ATCG machine-readable (numbers + full synoptic stamp).
PROSE_SLOTS = (31, 32, 33, 34, 35)
ATCG_SLOTS = (51, 52, 53, 54, 55)
#: Formation alerts (invests). Parsed for the TCFA flag only — no fix data.
TCFA_SLOTS = (21, 22, 23, 24)


def _utcnow() -> dt.datetime:
    """Naive UTC now. Naive on purpose: every timestamp in this codebase
    (parse_bdeck, parse_tcvitals, parse_atcg) is a naive UTC datetime, and
    mixing in an aware one makes every comparison raise."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def tgftp_url(token: str, slot: int) -> str:
    """Bulletin URL on NOAA's raw feed. The doubled dot is literal — tgftp
    names these ``wtpn31.pgtw..txt`` (the empty field is the BBB/amendment
    indicator), and dropping it 404s."""
    return f"{TGFTP_BASE}/wt{token}{slot:02d}.pgtw..txt"


# ---------------------------------------------------------------------------
# Development level
# ---------------------------------------------------------------------------

#: SUBJ development-level phrase -> (nature, display label). Longest phrase
#: first: "SUPER TYPHOON" must win over "TYPHOON", and "TROPICAL DEPRESSION"
#: over the generic "TROPICAL CYCLONE".
#:
#: Every one of these is TROPICAL ("TS"). That is not a simplification — JTWC
#: has no subtropical warning class, so the subtropical half of our ACE policy
#: (ACE_NATURES["wp"] = {"TS","SS","SD"}) simply never fires on JTWC data. The
#: policy is preserved untouched; it just has nothing to match.
DEV_LEVELS = (
    ("SUPER TYPHOON",       ("TS", "STY")),
    ("TROPICAL DEPRESSION", ("TS", "TD")),
    ("TROPICAL STORM",      ("TS", "TS")),
    ("TROPICAL CYCLONE",    ("TS", "TC")),
    ("TYPHOON",             ("TS", "TY")),
)

#: SUBJ lines that are NOT a warning on a designated system.
_TCFA_RE = re.compile(r"TROPICAL CYCLONE FORMATION ALERT", re.I)

_SUBJ_RE = re.compile(
    r"SUBJ/(?P<body>.*?)//", re.S | re.I)

#: The numbered RMKS body line: "1. TROPICAL DEPRESSION 32W (TORAJI) WARNING NR 007".
#:
#: This is the AUTHORITATIVE storm line, and reading it is not optional. When
#: more than one system is active JTWC drops the specific subject in favour of a
#: bare ``SUBJ/TROPICAL CYCLONE WARNING//`` and names the storm only here — so a
#: SUBJ-only parser loses the type precisely when the basin is busiest. It also
#: exposes slot/basin mismatches that would otherwise pass silently: ``wtio33``
#: (an INDIAN OCEAN slot) was observed carrying 32W TORAJI, a NW Pacific storm.
_BODY_STORM_RE = re.compile(
    r"^\s*\d+\.\s+(?P<body>[A-Z ]*?\b\d{2}[A-Z]\b.*?WARNING\s+NR\s+\d+)",
    re.M | re.I)

#: "TYPHOON 11W (NOUL) WARNING NR 010" — id and optional name.
_STORM_RE = re.compile(
    r"\b(?P<id>\d{2}[A-Z])\b(?:\s*\((?P<name>[A-Z0-9\- ]+)\))?", re.I)

_WARNING_NR_RE = re.compile(r"WARNING\s+NR\s+(?P<nr>\d+)", re.I)

#: The stop condition. Matched against whitespace-normalised text because JTWC
#: hard-wraps the sentence at column ~68 and the break lands in a different
#: place every time ("THIS IS THE\nFINAL WARNING", "THIS IS THE FINAL WARNING ON\n
#: THIS SYSTEM", ...). Normalising first is what makes this robust.
_FINAL_RE = re.compile(
    r"THIS IS THE FINAL WARNING ON THIS SYSTEM", re.I)

#: Why the storm ended, searched in the prose AROUND the final-warning sentence.
#: Ordered by specificity. The boilerplate that follows every final warning
#: ("THE SYSTEM WILL BE CLOSELY MONITORED FOR SIGNS OF REGENERATION") carries no
#: information and is deliberately NOT a pattern here.
FINAL_REASONS = (
    ("extratropical", re.compile(
        r"EXTRATROPICAL TRANSITION|\bETT\b|BAROCLINIC AND FRONTAL"
        r"|EXTRATROPICAL LOW", re.I)),
    ("inland", re.compile(
        r"LAND INTERACTION|MAKE LANDFALL|MADE LANDFALL|MOVED INLAND"
        r"|OVER LAND|FRICTIONAL EFFECTS", re.I)),
    ("dissipated", re.compile(
        r"DISSIPAT|DECAY|DETERIORAT|WEAKEN", re.I)),
)


def classify_dev_level(subj: str) -> tuple[Optional[str], Optional[str]]:
    """SUBJ text -> ``(nature, label)``.

    ``("TS", "TY")`` for a typhoon warning; ``(None, "TCFA")`` for a formation
    alert (an invest, which never accrues ACE anyway — see ``storm_ace``'s
    invest guard); ``(None, None)`` when the phrase is not one we recognise.

    Returning None rather than defaulting to tropical is the point: an
    unrecognised dev level must surface as indeterminate, not quietly count.
    """
    s = " ".join((subj or "").split()).upper()
    if not s:
        return None, None
    if _TCFA_RE.search(s):
        return None, "TCFA"
    for phrase, (nature, label) in DEV_LEVELS:
        if phrase in s:
            return nature, label
    return None, None


# ---------------------------------------------------------------------------
# ATCG (5x) machine-readable parser
# ---------------------------------------------------------------------------

#: "2026072512 11W NOUL       010  01 310 08 SATL RADR 040"
_ATCG_FIX_RE = re.compile(
    r"^(?P<stamp>\d{10})\s+(?P<id>\d{2}[A-Z])\s+(?P<name>\S+)\s+"
    r"(?P<nr>\d{3})\b", re.M)

#: "T000 218N 1159E 080 R064 035 NE QD ... R050 ... R034 ..."
#: T000 is the analysis; T012/T024/... are forecasts and are ignored.
_ATCG_T000_RE = re.compile(
    r"^T000\s+(?P<lat>\d+[NS])\s+(?P<lon>\d+[EW])\s+(?P<kt>\d+)"
    r"(?P<rest>.*)$", re.M)

_ATCG_RAD_RE = re.compile(
    r"R(?P<thr>034|050|064)\s+"
    r"(?P<ne>\d+)\s+NE\s+QD\s+(?P<se>\d+)\s+SE\s+QD\s+"
    r"(?P<sw>\d+)\s+SW\s+QD\s+(?P<nw>\d+)\s+NW\s+QD", re.I)


def parse_atcg(text: str) -> Optional[dict]:
    """Parse a 5x ATCG MIL bulletin into one analysis fix.

    Returns ``{"atcf_id", "name", "warning_nr", "time", "lat", "lon",
    "wind_kt", "radii", "is_final", "final_reason"}`` or None if the bulletin
    carries no parseable T000 analysis.

    ``radii`` maps threshold -> ``[ne, se, sw, nw]`` in nautical miles, for the
    thresholds actually present. Unlike tcvitals, **all three thresholds
    (34/50/64) are carried** whenever the storm is strong enough to have them.

    ``time`` comes from the leading ``YYYYMMDDHH`` stamp — the only place in the
    whole JTWC text product set with an unambiguous year. That is what makes
    the slot trap detectable.
    """
    if not text or "ATCG" not in text.upper():
        return None
    m_fix = _ATCG_FIX_RE.search(text)
    m_t000 = _ATCG_T000_RE.search(text)
    if not m_fix or not m_t000:
        return None
    try:
        t = dt.datetime.strptime(m_fix.group("stamp"), "%Y%m%d%H")
    except ValueError:
        return None
    ll = _parse_latlon(m_t000.group("lat"), m_t000.group("lon"))
    if ll is None:
        return None
    try:
        wind_kt = float(m_t000.group("kt"))
    except (TypeError, ValueError):
        return None

    radii: dict[int, list[int]] = {}
    for m in _ATCG_RAD_RE.finditer(m_t000.group("rest") or ""):
        thr = int(m.group("thr"))
        radii[thr] = [int(m.group(q)) for q in ("ne", "se", "sw", "nw")]

    is_final, reason = detect_final(text)
    name = (m_fix.group("name") or "").strip().upper()
    return {
        "atcf_id": m_fix.group("id").upper(),
        "name": name,
        "warning_nr": int(m_fix.group("nr")),
        "time": t,
        "lat": ll[0],
        "lon": ll[1],
        "wind_kt": wind_kt,
        "radii": radii,
        "is_final": is_final,
        "final_reason": reason,
    }


def _parse_latlon(lat_raw: str, lon_raw: str) -> Optional[tuple]:
    """'218N'/'1159E' -> (21.8, 115.9); tenths of a degree, like tcvitals."""
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
    return lat, lon


# ---------------------------------------------------------------------------
# Prose (3x) parser
# ---------------------------------------------------------------------------

#: Everything below this heading is FORECAST, not analysis. Both sections use
#: the same "DDHHMMZ --- lat lon" / "MAX SUSTAINED WINDS - NNN KT" / "RADIUS OF"
#: shapes, so the analysis parsers MUST run on the truncated text or they will
#: happily read a +12 h forecast as the current fix. Observed live on 12W
#: (2026-07-29): analysis 130 kt, first forecast 140 kt — a whole SSHWS
#: category apart, and the forecast is the one that reads as "ahead".
_FORECAST_CUT_RE = re.compile(r"^\s*FORECASTS?:", re.M | re.I)

#: The analysis position line, under "WARNING POSITION:":
#:     291200Z --- NEAR 15.2N 167.7E
#: The ``NEAR`` is the analysis marker — forecast lines are bare ``---``. Both
#: that word and the section cut above are required; either alone has been
#: enough to mis-read a bulletin in some basin's formatting.
_PROSE_POS_RE = re.compile(
    r"^\s*(?P<ddhhmm>\d{6})Z\s+-+\s+NEAR\s+"
    r"(?P<lat>\d+(?:\.\d+)?)\s*(?P<ns>[NS])\s+"
    r"(?P<lon>\d+(?:\.\d+)?)\s*(?P<ew>[EW])", re.M | re.I)

#: "MAX SUSTAINED WINDS - 130 KT, GUSTS 160 KT". The units sentence higher up
#: ("MAX SUSTAINED WINDS BASED ON ONE-MINUTE AVERAGE") has no "- NNN KT" and
#: correctly does not match.
_PROSE_WIND_RE = re.compile(
    r"MAX\s+SUSTAINED\s+WINDS\s*-\s*(?P<kt>\d+)\s*KT"
    r"(?:\s*,\s*GUSTS\s+(?P<gust>\d+)\s*KT)?", re.I)

#: "RADIUS OF 064 KT WINDS - 025 NM NORTHEAST QUADRANT" plus three continuation
#: lines. JTWC hard-wraps and indents the other three quadrants, so this spans
#: newlines by design.
_PROSE_RAD_RE = re.compile(
    r"RADIUS\s+OF\s+0*(?P<thr>\d{2,3})\s+KT\s+WINDS\s*-\s*"
    r"(?P<ne>\d+)\s*NM\s+NORTHEAST\s+QUADRANT\s+"
    r"(?P<se>\d+)\s*NM\s+SOUTHEAST\s+QUADRANT\s+"
    r"(?P<sw>\d+)\s*NM\s+SOUTHWEST\s+QUADRANT\s+"
    r"(?P<nw>\d+)\s*NM\s+NORTHWEST\s+QUADRANT", re.I)


def analysis_section(text: str) -> str:
    """The part of a prose bulletin that describes NOW, not the forecast.

    Everything from the top down to the ``FORECASTS:`` heading. Returns the
    whole text when there is no forecast section (final warnings often have
    none), which is safe — with no forecast block there are no forecast lines
    to confuse the analysis parsers.
    """
    if not text:
        return ""
    m = _FORECAST_CUT_RE.search(text)
    return text[:m.start()] if m else text


def detect_final(text: str) -> tuple[bool, Optional[str]]:
    """``(is_final, reason)`` from a warning bulletin.

    The sentence is hard-wrapped at an arbitrary column, so the text is
    whitespace-normalised before matching (see ``_FINAL_RE``). The reason is
    read from the 600 characters preceding the sentence — JTWC states the cause
    in the forecast discussion just above it, and the text that FOLLOWS is
    fixed boilerplate about monitoring for regeneration, which would otherwise
    match "regeneration" style patterns on every single final warning.

    Returns ``(False, None)`` for an in-force warning. A final warning whose
    cause we cannot classify returns ``(True, None)`` — the stop condition is
    still certain even when the reason is not.
    """
    flat = " ".join((text or "").split())
    m = _FINAL_RE.search(flat)
    if not m:
        return False, None
    window = flat[max(0, m.start() - 600):m.start()]
    for reason, pat in FINAL_REASONS:
        if pat.search(window):
            return True, reason
    return True, None


def parse_prose(text: str) -> Optional[dict]:
    """Parse a 3x warning bulletin for the fields the ATCG form lacks.

    Returns ``{"atcf_id", "name", "warning_nr", "nature", "dev_label",
    "is_final", "final_reason", "is_tcfa"}`` or None when the bulletin carries
    neither a SUBJ line nor a numbered storm line (tgftp sometimes serves a
    continuation segment that begins mid-sentence — ``wtio31`` was observed
    this way; only the final-warning flag is recoverable from those, and the
    caller's id guard correctly leaves the type indeterminate).

    The numbered RMKS body line wins over SUBJ when both are present: SUBJ goes
    generic ("TROPICAL CYCLONE WARNING") whenever multiple systems are active,
    while the body line always names the storm. See ``_BODY_STORM_RE``.

    No fix time is returned: the prose header carries only ``DDHHMM`` with no
    month or year, which cannot be resolved safely against a stale slot. Pair
    this with the matching ATCG bulletin (same slot index, and cross-checked on
    ``atcf_id`` + ``warning_nr``) for the timestamp.
    """
    if not text:
        return None
    m_subj = _SUBJ_RE.search(text)
    m_body = _BODY_STORM_RE.search(text)
    if m_subj is None and m_body is None:
        return None
    subj = " ".join(m_subj.group("body").split()) if m_subj else ""
    body = " ".join(m_body.group("body").split()) if m_body else ""
    is_tcfa = bool(_TCFA_RE.search(subj))
    # Prefer whichever line actually identifies a storm; the body line is the
    # specific one, so it is tried first.
    primary = body or subj
    nature, label = classify_dev_level(primary)
    if nature is None and label is None and primary is not subj:
        nature, label = classify_dev_level(subj)

    storm_id = name = None
    for candidate in (body, subj):
        m_storm = _STORM_RE.search(candidate) if candidate else None
        if m_storm:
            storm_id = (m_storm.group("id") or "").upper()
            nm = (m_storm.group("name") or "").strip().upper()
            name = nm or None
            break
    m_nr = _WARNING_NR_RE.search(primary) or _WARNING_NR_RE.search(subj)
    is_final, reason = detect_final(text)

    # --- the analysis fix -------------------------------------------------
    # Read ONLY from above the FORECASTS: heading. ``fix_ddhhmm`` is returned
    # raw because it cannot be resolved to an absolute time here — see
    # ``resolve_prose_time``, which needs an anchor the parser does not have.
    section = analysis_section(text)
    m_pos = _PROSE_POS_RE.search(section)
    fix_ddhhmm = lat = lon = None
    if m_pos:
        fix_ddhhmm = m_pos.group("ddhhmm")
        try:
            lat = float(m_pos.group("lat"))
            lon = float(m_pos.group("lon"))
        except (TypeError, ValueError):
            fix_ddhhmm = lat = lon = None
        else:
            # Prose is DECIMAL degrees ("15.2N"), unlike the ATCG form's tenths
            # ("152N"). Do not route this through _parse_latlon.
            if m_pos.group("ns").upper() == "S":
                lat = -lat
            if m_pos.group("ew").upper() == "W":
                lon = -lon

    m_wind = _PROSE_WIND_RE.search(section)
    wind_kt = gust_kt = None
    if m_wind:
        try:
            wind_kt = float(m_wind.group("kt"))
        except (TypeError, ValueError):
            wind_kt = None
        if m_wind.group("gust"):
            try:
                gust_kt = float(m_wind.group("gust"))
            except (TypeError, ValueError):
                gust_kt = None

    radii: dict[int, list[int]] = {}
    for m in _PROSE_RAD_RE.finditer(section):
        try:
            radii[int(m.group("thr"))] = [
                int(m.group(q)) for q in ("ne", "se", "sw", "nw")]
        except (TypeError, ValueError):
            continue

    return {
        "atcf_id": storm_id,
        "name": name,
        "warning_nr": int(m_nr.group("nr")) if m_nr else None,
        "nature": nature,
        "dev_label": label,
        "is_final": is_final,
        "final_reason": reason,
        "is_tcfa": is_tcfa,
        "fix_ddhhmm": fix_ddhhmm,
        "lat": lat,
        "lon": lon,
        "wind_kt": wind_kt,
        "gust_kt": gust_kt,
        "radii": radii,
    }


# ---------------------------------------------------------------------------
# Slot selection
# ---------------------------------------------------------------------------

#: How old an ATCG synoptic stamp may be before the bulletin is treated as a
#: leftover slot rather than a live warning. JTWC warns every 6 h; two missed
#: cycles plus issuance lag is comfortably inside 24 h, and the observed stale
#: slots were months to years old, so there is no ambiguity to split.
MAX_BULLETIN_AGE_H = 24.0


def select_current(fixes: Iterable[dict], now: Optional[dt.datetime] = None,
                   max_age_h: float = MAX_BULLETIN_AGE_H) -> list[dict]:
    """Keep only bulletins whose own synoptic stamp is recent.

    THE defence against the slot trap documented in the module docstring. A
    bulletin is judged by the timestamp it carries, never by the fact that its
    slot returned 200.

    Bulletins dated in the future are dropped too: a clock-skew or a
    mis-stamped product should degrade to "no warning" (indeterminate), not
    silently become the newest thing we know.
    """
    now = now or _utcnow()
    out = []
    for f in fixes:
        t = f.get("time")
        if t is None:
            continue
        age_h = (now - t).total_seconds() / 3600.0
        if -1.0 <= age_h <= max_age_h:
            out.append(f)
    return out


#: How far AHEAD of its ATCG twin a prose analysis may resolve. JTWC warns
#: 6-hourly and the prose bulletin is written first, so one full cycle ahead is
#: the normal divergence and two is the generous bound. Anything further apart
#: is a slot mismatch, not a lead.
PROSE_MAX_LEAD_H = 12.0
#: How far BEHIND its twin it may resolve. Only used to decide the DDHHMM is
#: plausible; a prose fix older than the ATCG never overrides it.
PROSE_MAX_LAG_H = 30.0


def resolve_prose_time(ddhhmm: Optional[str], anchor: Optional[dt.datetime],
                       now: Optional[dt.datetime] = None,
                       max_lead_h: float = PROSE_MAX_LEAD_H,
                       max_lag_h: float = PROSE_MAX_LAG_H
                       ) -> Optional[dt.datetime]:
    """Resolve a prose ``DDHHMM`` against the ATCG's full ``YYYYMMDDHH`` stamp.

    The prose header and its position line carry no month and no year, which is
    why this module long refused to read a fix out of them. An ANCHOR removes
    the ambiguity: the ATCG twin from the same slot pair, already validated by
    ``select_current``, supplies the year and month, and the day-of-month then
    picks a unique datetime within that neighbourhood.

    Candidates are built in the anchor's month and both adjacent months, so a
    bulletin written on the 1st that analyses the 31st still resolves. The
    closest candidate to the anchor wins, and it is rejected outright unless it
    lands inside ``[anchor - max_lag_h, anchor + max_lead_h]``.

    Returns None with no anchor. That is deliberate and is the whole stale-slot
    defence: a leftover bulletin's DDHHMM would otherwise resolve against the
    current month and could land within hours of now purely by coincidence
    (a ~1-in-15 chance per stale slot, which is not a safety margin). No anchor,
    no fix — the type half of the prose is still used, exactly as before.
    """
    if not ddhhmm or anchor is None:
        return None
    raw = str(ddhhmm).strip()
    if len(raw) != 6 or not raw.isdigit():
        return None
    day, hour, minute = int(raw[0:2]), int(raw[2:4]), int(raw[4:6])
    if not (1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    best = None
    for delta in (-1, 0, 1):
        month = anchor.month + delta
        year = anchor.year
        if month < 1:
            month, year = 12, year - 1
        elif month > 12:
            month, year = 1, year + 1
        try:
            cand = dt.datetime(year, month, day, hour, minute)
        except ValueError:
            continue           # e.g. the 31st of a 30-day month
        if best is None or abs(cand - anchor) < abs(best - anchor):
            best = cand
    if best is None:
        return None

    lead_h = (best - anchor).total_seconds() / 3600.0
    if lead_h > max_lead_h or lead_h < -max_lag_h:
        return None
    # An analysis is never in the future. Clock skew or a mis-stamped product
    # must degrade to "no prose fix", never to the newest thing we know.
    now = now or _utcnow()
    if (best - now).total_seconds() / 3600.0 > 1.0:
        return None
    return best


def merge_slot(atcg: Optional[dict], prose: Optional[dict],
               now: Optional[dt.datetime] = None) -> Optional[dict]:
    """Combine a slot's ATCG (numbers + time) and prose (type) halves.

    The two are only merged when they agree on ``atcf_id``; JTWC writes the
    same storm to the same slot index in both families, but a slot rotation
    between the two fetches would otherwise splice one storm's type onto
    another's position. On disagreement the ATCG half is kept alone and the
    type stays indeterminate — losing a type is recoverable, mixing two storms
    is not.

    ``is_final`` is the OR of both halves: JTWC repeats the final-warning
    sentence in both families, and either one asserting it is enough to stop.

    THE LEADING EDGE. The two families are written from the same warning but
    are not published in lockstep, and the prose is the one that goes first.
    Observed live on 12W (2026-07-29 19:07Z): ``wtpn31`` held WARNING NR 011,
    analysis 291200Z at 130 kt, while ``wtpn51`` still held NR 010, analysis
    290600Z at 120 kt — a 6 h, 10 kt gap in the direction that matters. When
    the prose analysis resolves NEWER than its twin's, it supersedes the
    numbers wholesale: time, position, wind and radii together, never a mix of
    the two hours. ``fix_source`` records which family the numbers came from.
    """
    if atcg is None:
        return None
    out = dict(atcg)
    out.setdefault("nature", None)
    out.setdefault("dev_label", None)
    out["fix_source"] = "atcg"
    if prose is None:
        return out
    if prose.get("atcf_id") and prose["atcf_id"] != atcg.get("atcf_id"):
        return out
    out["nature"] = prose.get("nature")
    out["dev_label"] = prose.get("dev_label")
    out["name"] = atcg.get("name") or prose.get("name")
    out["is_final"] = bool(atcg.get("is_final")) or bool(prose.get("is_final"))
    out["final_reason"] = (atcg.get("final_reason")
                           or prose.get("final_reason"))

    p_time = resolve_prose_time(prose.get("fix_ddhhmm"), atcg.get("time"),
                                now=now)
    if (p_time is not None and atcg.get("time") is not None
            and p_time > atcg["time"]
            and prose.get("lat") is not None
            and prose.get("lon") is not None
            and prose.get("wind_kt") is not None):
        out["time"] = p_time
        out["lat"] = prose["lat"]
        out["lon"] = prose["lon"]
        out["wind_kt"] = prose["wind_kt"]
        # Radii belong to the hour they were measured. Taking the prose's
        # position at 12Z with the ATCG's radii from 06Z would publish a wind
        # field that never existed; carry the prose's own or carry none.
        out["radii"] = prose.get("radii") or {}
        if prose.get("warning_nr") is not None:
            out["warning_nr"] = prose["warning_nr"]
        out["fix_source"] = "prose"
    return out
