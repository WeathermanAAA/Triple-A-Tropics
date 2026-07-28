#!/usr/bin/env python3
"""ATCF a-deck / b-deck ingest + QC for the ``/models/`` guidance layer.

Source of truth is NHC's own public FTP, which is now the SOLE authorized
publisher of these files:

    a-deck (aids/guidance)  https://ftp.nhc.noaa.gov/atcf/aid_public/a{bb}{nn}{yyyy}.dat.gz
    b-deck (best track)     https://ftp.nhc.noaa.gov/atcf/btk/b{bb}{nn}{yyyy}.dat

The mirrors that used to duplicate it are gone - EMC's public ATCF mirror was
withdrawn as pre-decisional, and ospo.noaa.gov/tropical-data/ATCF/ no longer
serves. That makes ftp.nhc.noaa.gov both the only source and, precisely because
everything else died of redundancy, the most durable one available.

=============================================================================
THE PUBLIC A-DECK IS FILTERED. This is the single most important fact here.
=============================================================================
Every ECMWF-derived aid is withheld from the public feed. Verified against all
23 live 2026 decks (521,842 rows, 106 distinct TECH ids) on 2026-07-28: EMX,
EMXI, EMX2, EEMN, EMNI, SHPE, DSPE, LGME, EAIO, EAMN, UKM, UKMI, UEMN, FSSE and
GFEX each appear ZERO times - not one row, and a raw byte grep finds the strings
nowhere. No id beginning with "E" exists in the public decks at all.

The withholding is deliberate rather than "the aid was not produced": NHC's own
techlist formally defines every one of them, and the post-season archive
(``/atcf/archive/{YYYY}/``) carries them in quantity - the 2025 Atlantic deck
for storm 05 has EMX=2150, EMXI=871, EEMN=1088, UKM=1168, UEMN=1342 rows.

There is NO public unfiltered fallback: ``/atcf/aid/`` returns 404, and the full
decks only appear post-season. ECMWF guidance is therefore structurally
unavailable in real time from this source, and no retry or alternate path
recovers it.

THE CONSEQUENCE WE MUST STATE ON THE PAGE: the consensus aids are plottable but
NOT independently reproducible. TVCN is defined as a consensus of AVNI / EGRI /
HWFI / EMXI / CTCI / EMNI and RVCN likewise includes EMXI - and EMXI is absent.
So the consensus values we can plot were computed upstream from a member set we
cannot see, and we cannot recompute or verify them. GFEX (a consensus of AVNI
and EMXI) is dropped entirely rather than degraded. A present consensus aid does
NOT imply its members are present. :func:`consensus_provenance` returns this in
a form the page can render, and it should be rendered - quietly claiming a
reproducible consensus would be the dishonest option.

=============================================================================
QC
=============================================================================
Every rule below is calibrated against the real 2026 decks, not against the
format document. Rates are from that same 521,842-row sample.

* **MSLP == 0 means MISSING, not "zero millibars"** - 28.89% of rows. Entire
  aid families never populate it (every interpolated ``*I`` aid, TVCN, IVCN,
  RVCN, HCCA, SHIP, DSHP, LGEM, OCD5, SHF5, CLP5, TABD/TABS/TABM, XTRP, TCLP);
  even OFCL is 88.8% zero. VMAX == 0 is the same sentinel, 8.50% of rows.
* **Position 0N/0W is a sentinel, not the Gulf of Guinea** - 9,561 rows. IVCN,
  SHF5, ICON, RI25/RI30/RI35/RI40 and IVRI are 100% zero-position by design
  (they are intensity-only aids); SHIP/DSHP/LGEM switch to it beyond TAU 132.
  This is the most dangerous trap in the format: it is syntactically valid, so
  it survives naive parsing and then poisons every motion calculation.
* **-99 is a SECOND missing sentinel**, distinct from 0, used in POUTER and
  ROUTER (~18,200 rows each). -999 appears in the trailing user-data block.
* **The primary key is (BASIN, CY, DTG, TECH, TAU, RAD)** - NOT (DTG, TECH,
  TAU). 73,916 (DTG, TECH, TAU) triples carry more than one row because the
  34/50/64 kt wind-radii records legitimately share the triple. Deduplicating on
  the triple silently discards ~110k genuine radii records.
* **Rows are VARIABLE width**, 18 to 46 comma-separated fields (18 fields covers
  86% of rows), always with a trailing comma yielding an empty final element.
  A parser that indexes a fixed high column IndexErrors on most of the data.
* **TAU can be NEGATIVE** - CARQ carries -24/-18/-12/-6 (the past-position block
  used for bogusing). Those are not forecasts and are excluded by default.
* **Longitude crosses into the E hemisphere** in ~10k rows, and the antimeridian
  itself is encoded ``1800E``. Sign handling that assumes W breaks the track.
* **Byte-identical adjacent duplicates do occur** - rare (38 rows) but real.

Numbers above are a SNAPSHOT: the decks grow all season, so they are reported
for calibration and are never asserted as invariants.

Dependency-light: stdlib only. The fetcher takes an injected opener so the
parser and QC are testable with no network.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import gzip
import math
import re
from collections import Counter, defaultdict
from typing import Callable, Iterable, Optional, Sequence

NHC_ADECK_URL = ("https://ftp.nhc.noaa.gov/atcf/aid_public/"
                 "a{basin}{cy:02d}{year}.dat.gz")
NHC_BDECK_URL = "https://ftp.nhc.noaa.gov/atcf/btk/b{basin}{cy:02d}{year}.dat"

#: Aids withheld from the PUBLIC a-deck. Verified absent (0 rows) across every
#: live 2026 deck while being formally defined in NHC's techlist and present in
#: the post-season archive. Used to explain an absence rather than to look for
#: these ids - code must never wait on them.
WITHHELD_TECHS = (
    # ECMWF deterministic + its interpolated / 2-cycle-old variants
    "EMX", "EMXI", "EMX2",
    # ECMWF ensemble mean + interpolated
    "EEMN", "EMNI",
    # ECMWF-driven statistical intensity aids
    "SHPE", "DSPE", "LGME",
    # ECMWF AI / AIFS-derived
    "EAIO", "EAMN",
    # UKMET native ids (only the GFS-tracker UKX* variants survive)
    "UKM", "UKMI", "UEMN",
    # consensus aids that require an ECMWF member
    "FSSE", "GFEX",
)

#: Consensus aids that ARE published but whose nominal member list includes a
#: withheld aid. tech -> (nominal members, withheld members). The page must say
#: these are not independently reproducible.
CONSENSUS_MEMBERS = {
    "TVCN": ("AVNI", "EGRI", "HWFI", "EMXI", "CTCI", "EMNI"),
    "TVCE": ("AVNI", "EGRI", "HWFI", "EMXI", "CTCI", "EMNI"),
    "IVCN": ("DSHP", "LGEM", "HWFI", "CTCI", "EMXI"),
    "RVCN": ("AVNI", "HWFI", "EMXI", "CTCI", "EMNI"),
    "HCCA": ("AVNO", "AVNI", "EMX", "EMXI", "HWFI", "CTCI"),
    "NNIC": ("DSHP", "LGEM", "SHIP", "EMXI"),
}

#: Aids that publish an intensity but never a position - they legitimately carry
#: the 0N/0W sentinel for EVERY row, so a missing position is not a defect here.
INTENSITY_ONLY_TECHS = frozenset({
    "IVCN", "SHF5", "ICON", "IVRI",
    "RI25", "RI30", "RI35", "RI40",
})

#: CARQ carries the analysis + PAST positions (negative TAU) for model bogusing.
#: Not a forecast aid.
NON_FORECAST_TECHS = frozenset({"CARQ", "WRNG"})

#: Wind-radii thresholds; part of the primary key (see the module docstring).
RADII_THRESHOLDS = (34, 50, 64)

#: A translation speed above this between consecutive fixes of one aid is
#: physically implausible for a tropical cyclone and indicates bad data. On the
#: real decks, with the 0N/0W sentinel correctly dropped first, exactly ONE pair
#: in 380,373 exceeds it (61.4 kt) - so the threshold is tight without being
#: trigger-happy. Applied AFTER position QC, never before: on raw rows the
#: sentinel alone manufactures 288 false flags with implied speeds to 754 kt.
MAX_TRANSLATION_KT = 60.0

_DTG_RE = re.compile(r"^\d{10}$")
#: Earth radius in nautical miles (great-circle distances).
_EARTH_NM = 3440.065


# ---------------------------------------------------------------------------
# Row model
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class AidRow:
    """One ATCF row, QC'd.

    ``None`` means MISSING throughout - the 0 / -99 / -999 sentinels are
    resolved to None at parse time so no consumer can mistake a sentinel for a
    measurement. That is the whole contract of this dataclass.
    """

    basin: str          # "al" | "ep" | "cp" | "wp" | ...
    cy: int             # storm number within the basin
    dtg: dt.datetime    # synoptic time (UTC)
    tech: str           # aid id, e.g. "HFSA", "AVNO", "OFCL"
    tau: int            # forecast hour; NEGATIVE for CARQ past positions
    rad: Optional[int]  # wind-radii threshold (34/50/64) or None

    lat: Optional[float]    # degrees N, None when the position is the sentinel
    lon: Optional[float]    # degrees E-positive, None when sentinel
    vmax_kt: Optional[int]  # None when the 0 sentinel
    mslp_hpa: Optional[int]  # None when the 0 sentinel

    @property
    def key(self) -> tuple:
        """The ATCF PRIMARY KEY. (DTG, TECH, TAU) is NOT unique - the 34/50/64 kt
        radii records share it by design - so RAD is part of the key."""
        return (self.basin, self.cy, self.dtg, self.tech, self.tau, self.rad)

    @property
    def has_position(self) -> bool:
        return self.lat is not None and self.lon is not None

    @property
    def is_forecast(self) -> bool:
        """CARQ/WRNG rows and negative TAUs are not forecasts."""
        return self.tech not in NON_FORECAST_TECHS and self.tau >= 0


@dataclasses.dataclass
class QCReport:
    """What the QC pass found. Surfaced on the page, not just logged.

    Silent dropping is how a guidance display starts lying: a track that quietly
    lost half its rows still draws a confident line. These counters exist so the
    page can say what it removed and why.
    """

    rows_seen: int = 0
    rows_kept: int = 0
    malformed: int = 0
    short_rows: int = 0
    bad_dtg: int = 0
    mslp_missing: int = 0
    vmax_missing: int = 0
    position_missing: int = 0
    position_missing_expected: int = 0   # intensity-only aids: by design
    exact_duplicate_rows: int = 0
    duplicate_keys: int = 0
    implausible_speed: int = 0
    negative_tau: int = 0
    techs: Counter = dataclasses.field(default_factory=Counter)
    speed_flags: list = dataclasses.field(default_factory=list)

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["techs"] = dict(self.techs)
        d["speed_flags"] = self.speed_flags[:50]   # bounded for the payload
        return d

    def summary(self) -> str:
        return (f"{self.rows_kept}/{self.rows_seen} rows kept across "
                f"{len(self.techs)} aids; dropped {self.malformed} malformed, "
                f"{self.exact_duplicate_rows} exact duplicates; missing: "
                f"MSLP {self.mslp_missing}, VMAX {self.vmax_missing}, "
                f"position {self.position_missing} "
                f"({self.position_missing_expected} expected); "
                f"{self.implausible_speed} implausible-motion flags")


# ---------------------------------------------------------------------------
# Field-level parsing. Each sentinel is resolved HERE so it cannot leak.
# ---------------------------------------------------------------------------
def _int_or_none(raw: str, *, zero_is_missing: bool = True) -> Optional[int]:
    """An ATCF integer cell, with the sentinels resolved to None.

    0 is missing for VMAX/MSLP (28.89% / 8.50% of live rows); -99 is a SECOND,
    independent sentinel used by POUTER/ROUTER; -999 appears in the trailing
    user-data block. All three become None.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        v = int(float(raw))
    except (TypeError, ValueError):
        return None
    if v in (-99, -999):
        return None
    if zero_is_missing and v == 0:
        return None
    return v


def parse_latlon(lat_raw: str, lon_raw: str) -> Optional[tuple]:
    """``('218N', '1511W') -> (21.8, -151.1)``; E longitudes stay POSITIVE.

    Returns None for the 0N/0W sentinel - the single most dangerous value in the
    format, because it is syntactically perfect and lands at null island. The
    antimeridian is encoded ``1800E``, so E must be handled as a real hemisphere
    rather than assumed away.
    """
    def _one(raw: str, pos: str, neg: str) -> Optional[float]:
        raw = (raw or "").strip().upper()
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
    # The sentinel: EXACTLY 0.0/0.0. A genuine fix at the equator on the prime
    # meridian is not a tropical cyclone, so this costs nothing real.
    if lat == 0.0 and lon == 0.0:
        return None
    return lat, lon


def _parse_dtg(raw: str) -> Optional[dt.datetime]:
    raw = (raw or "").strip()
    if not _DTG_RE.match(raw):
        return None
    try:
        return dt.datetime.strptime(raw, "%Y%m%d%H").replace(
            tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def great_circle_nm(a: tuple, b: tuple) -> float:
    """Great-circle distance in nautical miles between (lat, lon) pairs."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return 2 * _EARTH_NM * math.asin(min(1.0, math.sqrt(h)))


# ---------------------------------------------------------------------------
# Deck parsing
# ---------------------------------------------------------------------------
def parse_deck(text: str, *, report: Optional[QCReport] = None,
               keep_non_forecast: bool = False) -> tuple:
    """Parse an a-deck or b-deck into ``(rows, QCReport)``.

    b-decks use the SAME comma-delimited layout (TECH is always ``BEST``, TAU
    always 0), so one parser serves both - there is no second format to
    maintain.

    Rows are variable-width (18..46 fields), so every access past the required
    prefix is length-guarded. Byte-identical adjacent duplicates are skipped;
    genuine multi-RAD rows are NOT, because RAD is part of the primary key.
    """
    rep = report if report is not None else QCReport()
    rows: list = []
    prev_line = None

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        rep.rows_seen += 1
        # Cheap defence against the rare byte-identical adjacent duplicate
        # (38 such rows in the live sample, always two adjacent identical lines).
        if line == prev_line:
            rep.exact_duplicate_rows += 1
            continue
        prev_line = line

        parts = [p.strip() for p in line.split(",")]
        # 18 fields is the minimum real row and covers 86% of live data; the
        # trailing comma means the last element is always an empty string.
        if len(parts) < 11:
            rep.short_rows += 1
            rep.malformed += 1
            continue

        dtg = _parse_dtg(parts[2])
        if dtg is None:
            rep.bad_dtg += 1
            rep.malformed += 1
            continue

        tech = parts[4].upper()
        try:
            cy = int(parts[1])
            tau = int(parts[5])
        except ValueError:
            rep.malformed += 1
            continue

        if tau < 0:
            rep.negative_tau += 1
        if not keep_non_forecast and (tech in NON_FORECAST_TECHS or tau < 0):
            continue

        pos = parse_latlon(parts[6], parts[7])
        if pos is None:
            rep.position_missing += 1
            if tech in INTENSITY_ONLY_TECHS:
                rep.position_missing_expected += 1

        vmax = _int_or_none(parts[8]) if len(parts) > 8 else None
        mslp = _int_or_none(parts[9]) if len(parts) > 9 else None
        if vmax is None:
            rep.vmax_missing += 1
        if mslp is None:
            rep.mslp_missing += 1

        rad = None
        if len(parts) > 11:
            try:
                r = int(parts[11])
                rad = r if r in RADII_THRESHOLDS else None
            except ValueError:
                rad = None

        rows.append(AidRow(
            basin=parts[0].strip().lower(), cy=cy, dtg=dtg, tech=tech, tau=tau,
            rad=rad, lat=pos[0] if pos else None, lon=pos[1] if pos else None,
            vmax_kt=vmax, mslp_hpa=mslp))
        rep.techs[tech] += 1
        rep.rows_kept += 1

    # Duplicate PRIMARY keys (basin, cy, dtg, tech, tau, rad). Counted, not
    # dropped: a genuine repeat is a data defect worth reporting, but the
    # multi-RAD rows that share (dtg, tech, tau) are NOT duplicates and are
    # already distinguished by rad being in the key.
    seen: Counter = Counter(r.key for r in rows)
    rep.duplicate_keys = sum(c - 1 for c in seen.values() if c > 1)

    _flag_implausible_motion(rows, rep)
    return rows, rep


def _flag_implausible_motion(rows: Sequence[AidRow], rep: QCReport) -> None:
    """Flag consecutive fixes of one aid implying > MAX_TRANSLATION_KT.

    Runs on POSITIONED rows only. Order matters enormously: applied before the
    0N/0W sentinel is resolved to None, this manufactures hundreds of false
    flags (implied speeds to 754 kt) from aids that never had a position at all.
    """
    tracks: dict = defaultdict(list)
    for r in rows:
        # One track per (aid, cycle); RAD 50/64 rows repeat the same position,
        # so only the primary (rad None or 34) row contributes.
        if not r.has_position or r.rad not in (None, 34):
            continue
        tracks[(r.basin, r.cy, r.tech, r.dtg)].append(r)

    for key, seq in tracks.items():
        seq.sort(key=lambda r: r.tau)
        for a, b in zip(seq, seq[1:]):
            dh = b.tau - a.tau
            if dh <= 0:
                continue
            nm = great_circle_nm((a.lat, a.lon), (b.lat, b.lon))
            kt = nm / dh
            if kt > MAX_TRANSLATION_KT:
                rep.implausible_speed += 1
                rep.speed_flags.append({
                    "tech": a.tech, "basin": a.basin, "cy": a.cy,
                    "dtg": a.dtg.strftime("%Y%m%d%H"),
                    "tau_from": a.tau, "tau_to": b.tau, "kt": round(kt, 1),
                })


# ---------------------------------------------------------------------------
# Filtered-deck honesty
# ---------------------------------------------------------------------------
def consensus_provenance(present_techs: Iterable[str]) -> list:
    """Which published consensus aids were computed from a member set we cannot
    see, and which members are missing.

    Returned so the PAGE can say it. A consensus aid whose members are withheld
    is plottable but not independently reproducible, and presenting it without
    that caveat would imply a verification we cannot perform.
    """
    present = {t.upper() for t in present_techs}
    withheld = set(WITHHELD_TECHS)
    out = []
    for tech, members in CONSENSUS_MEMBERS.items():
        if tech not in present:
            continue
        missing = [m for m in members if m in withheld]
        if missing:
            out.append({
                "tech": tech,
                "nominal_members": list(members),
                "withheld_members": missing,
                "reproducible": False,
                "note": (f"{tech} is published, but {', '.join(missing)} "
                         f"{'is' if len(missing) == 1 else 'are'} withheld from "
                         f"the public a-deck, so its value cannot be "
                         f"independently recomputed or verified here."),
            })
    return out


def filtered_deck_notice(present_techs: Iterable[str]) -> dict:
    """The complete "what this feed does not contain" block for the page."""
    present = {t.upper() for t in present_techs}
    return {
        "source": "ftp.nhc.noaa.gov/atcf/aid_public",
        "withheld": list(WITHHELD_TECHS),
        "withheld_present_anyway": sorted(present & set(WITHHELD_TECHS)),
        "consensus": consensus_provenance(present),
        "note": (
            "NHC's public a-deck omits every ECMWF-derived aid (EMX/EMXI/EMX2, "
            "EEMN/EMNI, SHPE/DSPE/LGME, EAIO/EAMN) as well as UKM/UKMI, UEMN, "
            "FSSE and GFEX. They are withheld, not absent: NHC's techlist "
            "defines them and the post-season archive contains them. There is "
            "no public unfiltered feed, so ECMWF guidance cannot be shown here "
            "in real time. Consensus aids that nominally include an ECMWF "
            "member are still published and still plotted, but they were "
            "computed upstream from members we cannot see - so they are not "
            "independently reproducible."),
    }


# ---------------------------------------------------------------------------
# Fetch (opener injected, so parse + QC test with no network)
# ---------------------------------------------------------------------------
def _default_opener(url: str, timeout: float = 30.0) -> bytes:
    import requests
    r = requests.get(url, timeout=timeout,
                     headers={"User-Agent": "triple-a-tropics/guidance"})
    r.raise_for_status()
    return r.content


def fetch_deck(basin: str, cy: int, year: int, *, kind: str = "a",
               opener: Optional[Callable] = None) -> Optional[str]:
    """Fetch one deck's TEXT, or None if it does not exist (404).

    ``kind="a"`` is the gzipped aid deck, ``kind="b"`` the plain best track.
    A missing deck is a normal condition (the storm may not exist yet), so it
    returns None rather than raising; anything else propagates.
    """
    opener = opener or _default_opener
    tmpl = NHC_ADECK_URL if kind == "a" else NHC_BDECK_URL
    url = tmpl.format(basin=basin.lower(), cy=int(cy), year=int(year))
    try:
        raw = opener(url)
    except Exception as e:  # noqa: BLE001 - a 404 is an expected outcome
        if "404" in str(e):
            return None
        raise
    if raw is None:
        return None
    if kind == "a":
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError):
            pass   # already-decompressed body (some proxies inflate for us)
    return raw.decode("utf-8", errors="replace")
