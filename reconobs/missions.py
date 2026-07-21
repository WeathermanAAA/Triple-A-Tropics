"""reconobs.missions - decode HDOB/VDM/sonde messages into missions + storms.

A "mission" is one aircraft sortie (all HDOB messages sharing a mission_id,
e.g. AF302-0514A-MILTON). We decode each message (normalising the leading
blank line so a single mission_row works for both archive .txt and the live
.shtml), null any QC-flagged plotted variable per the recon contract, and group
points by mission. Missions tie to a storm by the name in their id.
"""
from __future__ import annotations

import math
import re

from .decode import decode_hdob, decode_vdm, decode_dropsonde

_INVEST_NAMES = ("INVEST", "GENESIS", "AREA", "SUSPECT", "DISTURB")
# 2nd-token / name markers for NON-tropical sorties (research, training,
# ferry, air-quality + field campaigns, generic placeholders) - excluded from
# the storm list. HS3/GRIP/IFEX/NAMMA/HS-series are NASA/NOAA research
# campaigns; CYCLONE/STORM/SYSTEM are generic non-name placeholders.
_NON_TC = ("TRAIN", "SURVEY", "TEST", "FERRY", "LOGISTIC", "RESEARCH",
           "TEXAQS", "CALVAL", "CALIB", "AEROSE", "OWLES", "SHOUT",
           "HS3", "HS2", "GRIP", "IFEX", "NAMMA", "GLOBAL", "HAWK", "AVAPS",
           "CYCLONE", "SYSTEM")
# Placeholder tokens the tasking agency puts in the storm-name slot of a REAL
# TC sortie when the system has no name yet (observed live: an unnamed
# depression flown as "CYCLONE"). A placeholder alone still reads non-TC —
# only a placeholder PLUS a tasked flight id (see tasked_storm) rescues it.
_PLACEHOLDER_NAMES = ("CYCLONE", "SYSTEM", "STORM")
# Tasked flight ids encode the target: 2-char mission code, then the 2-digit
# storm number and basin letter (e.g. 0102A = mission 01 into storm 02,
# Atlantic; WA05E = mission WA into storm 05, E-Pac). Research/training ids
# (WXWX*, long coded strings) never match.
_TASKED_FLIGHT = re.compile(r"^[0-9A-Z]{2}(\d{2})([AECW])$")
_TASK_BASIN = {"A": "al", "E": "ep", "C": "cp", "W": "wp"}
# Designation for an unnamed system by storm number (the standard number-word
# convention used for depressions); 90-99 are invest slots.
_NUMBER_WORDS = (
    "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE",
    "TEN", "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN", "SIXTEEN",
    "SEVENTEEN", "EIGHTEEN", "NINETEEN", "TWENTY", "TWENTY-ONE", "TWENTY-TWO",
    "TWENTY-THREE", "TWENTY-FOUR", "TWENTY-FIVE", "TWENTY-SIX",
    "TWENTY-SEVEN", "TWENTY-EIGHT", "TWENTY-NINE", "THIRTY")


def tasked_storm(flight: str) -> tuple[int, str] | None:
    """(storm_number, atcf_basin_prefix) encoded in a tasked flight id, or
    None when the flight slot is not a standard tasked-mission code."""
    m = _TASKED_FLIGHT.match((flight or "").strip().upper())
    if not m:
        return None
    return int(m.group(1)), _TASK_BASIN[m.group(2)]


def storm_name_for_number(num: int) -> str | None:
    """Designation for storm ``num``: number-word for a depression slot,
    INVEST for 90-99, None outside the known range."""
    if 90 <= num <= 99:
        return "INVEST"
    if 1 <= num <= len(_NUMBER_WORDS):
        return _NUMBER_WORDS[num - 1]
    return None
_VDM_LATLON = re.compile(r"([\d.]+)\s*deg\s*([NS])\s+([\d.]+)\s*deg\s*([EW])")
# MSLP is the D. line (EXTRAP/DROP ... mb), NOT the C. standard-level height.
_VDM_MSLP = re.compile(r"^\s*[DK]\.\s*[A-Z ]*?(\d{3,4})\s*mb", re.I | re.M)
_VDM_TIME = re.compile(r"^\s*A\.\s*(\d{2})/(\d{2}):(\d{2}):(\d{2})Z", re.M)


def _clean(v):
    """NaN/inf -> None; numpy scalar -> python; else passthrough (JSON-safe)."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 3)
    except (TypeError, ValueError):
        return v


def _norm(content: str) -> str:
    """Strip leading blank lines so the seq line is line 0, the WMO header
    line 1 and the mission header line 2 -> mission_row=2 decodes archive
    .txt and live .shtml identically."""
    lines = content.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines)


def decode_hdob_message(content: str):
    """Decode one HDOB bulletin -> DataFrame, or None on failure.

    Sanity-guards the decoded timestamps: the vendored HDOB decoder trusts a date
    token in the bulletin header, and a malformed/garbled header can stamp obs
    with an impossible year (observed: 2095 on a 2014 backfill bulletin), which
    poisons the per-storm date range, the manifest sort and the viewer's time
    axis. Rows whose year falls outside [2006, now+1] are dropped; a bulletin
    that decodes to NOTHING sane is treated as a failed decode (None)."""
    try:
        df = decode_hdob(_norm(content), mission_row=2)
    except Exception:                            # noqa: BLE001 - malformed msg
        return None
    if df is None or not len(df):
        return None
    try:
        import datetime as _dt
        y_max = _dt.datetime.utcnow().year + 1
        years = df["time"].map(lambda t: getattr(t, "year", None))
        df = df[years.between(2006, y_max)]
    except Exception:                            # noqa: BLE001 - never break on guard
        pass
    return df if len(df) else None


def storm_from_mission_id(mid: str) -> tuple[str, str, str]:
    """(aircraft, flight, storm_name) from a mission_id 'AF302-0514A-MILTON'."""
    parts = (mid or "").split("-")
    aircraft = parts[0] if parts else ""
    flight = parts[1] if len(parts) > 1 else ""
    name = parts[-1] if len(parts) > 2 else ""
    return aircraft, flight, name.strip().upper()


# Old-format (pre-2012) HDOB bulletins suffix the storm-name token with a
# varying storm-number ("IKE"/"IKE1"/"IKE2"/"IKE4" all = Ike 2008) -- the digit
# even flips WITHIN one mission, so a single flight gets split across IKE1/2/4.
# Stripping a trailing digit-run from an otherwise-alphabetic name (>=3 letters)
# collapses the fragments to the canonical storm. Leaves clean names, INVEST and
# short/numeric/coded designations untouched (so it cannot mangle modern data or
# the live current season).
_NAME_NUM_SUFFIX = re.compile(r"^([A-Z]{3,})\d+$")


def canonical_storm_name(name: str) -> str:
    up = (name or "").strip().upper()
    m = _NAME_NUM_SUFFIX.match(up)
    return m.group(1) if m else up


def is_invest_name(name: str) -> bool:
    up = (name or "").upper()
    return (not up) or any(k in up for k in _INVEST_NAMES) or up.isdigit()


def is_tropical_mission(name: str, flight: str) -> bool:
    """True iff a sortie targets a TC/invest (not a research/training/ferry
    flight). Research flights carry a WXWX* code in the flight slot; training
    and campaign sorties carry a known non-TC name; a long all-digit 'name'
    is a malformed research id."""
    up, fl = (name or "").upper(), (flight or "").upper()
    if fl.startswith("WXWX"):
        return False
    # A placeholder (or empty) name on a TASKED flight id is a real TC sortie
    # into an unnamed system — the flight slot, not the name, is authoritative.
    if (not up or up in _PLACEHOLDER_NAMES) and tasked_storm(fl):
        return True
    if not up or (up.isdigit() and len(up) > 2):
        return False
    if any(up.startswith(k) for k in _NON_TC):
        return False
    return True


# Flagged vars that are NULLED (not plotted). SFMR + rain are the exception:
# V2 keeps their raw value and flags it `sfmr_suspect` so the time-series can
# mark suspect points rather than silently drop them (the SFMR caveat is shown
# on the panel). The flight-level + thermo vars still null on flag.
_NULL_ON_FLAG = ("p_sfc", "temp", "dwpt", "wdir", "wspd", "pkwnd",
                 "plane_p", "plane_z")


def _row_records(df) -> list[dict]:
    """DataFrame -> JSON-safe track points. Flagged flight-level/thermo vars
    are nulled; SFMR + rain keep their raw value but carry ``sfmr_suspect``."""
    out = []
    for _, r in df.iterrows():
        flag = list(r.get("flag") or [])
        rec = {
            "t": r["time"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "lat": _clean(r["lat"]), "lon": _clean(r["lon"]),
            "plane_p": _clean(r.get("plane_p")),
            "plane_z": _clean(r.get("plane_z")),   # geopotential height (m)
            "p_sfc": _clean(r.get("p_sfc")),
            "wspd": _clean(r.get("wspd")), "wdir": _clean(r.get("wdir")),
            "pkwnd": _clean(r.get("pkwnd")), "sfmr": _clean(r.get("sfmr")),
            "rain": _clean(r.get("rain")),
            "temp": _clean(r.get("temp")), "dwpt": _clean(r.get("dwpt")),
            "sfmr_suspect": ("sfmr" in flag) or ("rain" in flag),
        }
        for var in _NULL_ON_FLAG:
            if var in flag and var in rec:
                rec[var] = None
        if rec["lat"] is None or rec["lon"] is None:
            continue
        out.append(rec)
    return out


def build_missions(hdob_contents: list[str]) -> dict[str, dict]:
    """Decode + group a batch of HDOB bulletins into missions keyed by
    mission_id. Idempotent: re-decoding the same bulletin yields the same
    points (dedup by timestamp on merge)."""
    missions: dict[str, dict] = {}
    for content in hdob_contents:
        df = decode_hdob_message(content)
        if df is None:
            continue
        mid_raw = str(df["mission_id"].iloc[0])
        aircraft, flight, name = storm_from_mission_id(mid_raw)
        # Collapse the old-format storm-number suffix (IKE1/IKE2/IKE4 -> IKE) at
        # the KEY, so a flight split across those variants merges into one
        # mission (points dedup by timestamp below) under the canonical name.
        cname = canonical_storm_name(name)
        # An unnamed system flies under a placeholder (or empty) name token;
        # the tasked flight id carries the real identity. Re-label with the
        # number-word designation (key included, so a later token flip to the
        # designation merges into the same mission) and remember the storm
        # number + basin so finalize can derive the atcf once the obs year is
        # known — grouping then unifies on it exactly as a VDM-supplied atcf.
        tasked = tasked_storm(flight)
        derived_task = None
        if tasked and (not cname or cname in _PLACEHOLDER_NAMES):
            desig = storm_name_for_number(tasked[0])
            if desig:
                cname = desig
                # Invest slots (90-99) relabel to INVEST but do NOT derive an
                # atcf: all invests share the (basin, INVEST) grouping key, so
                # a derived id would leak via atcf_by_key onto every other
                # invest mission in the basin and merge distinct invests.
                if desig != "INVEST":
                    derived_task = tasked
        mid = mid_raw if cname == name else f"{mid_raw.rsplit('-', 1)[0]}-{cname}"
        m = missions.setdefault(mid, {
            "mission_id": mid, "aircraft": aircraft, "flight": flight,
            "storm_name": cname, "is_invest": is_invest_name(cname),
            "is_tropical": is_tropical_mission(cname, flight),
            "atcf": None, "_points": {}, "_tasked": derived_task,
        })
        for rec in _row_records(df):
            m["_points"][rec["t"]] = rec          # dedup by timestamp
    # finalize: sort points, compute window + extremes
    for m in missions.values():
        pts = sorted(m.pop("_points").values(), key=lambda p: p["t"])
        m["track"] = pts
        m["n_obs"] = len(pts)
        m["valid_start"] = pts[0]["t"] if pts else None
        m["valid_end"] = pts[-1]["t"] if pts else None
        # Flight-code-derived atcf for a placeholder-named tasked sortie (needs
        # the obs year, so it lands here). Setting it now also PINS the id:
        # add_vdm seeds atcf only when unset, so a time-misbound VDM from a
        # concurrent storm cannot re-identify this mission (the flight code
        # comes from the mission's own header and both agree when correct).
        tk = m.pop("_tasked", None)
        if tk and m["atcf"] is None and m["valid_start"]:
            m["atcf"] = f"{tk[1]}{tk[0]:02d}{m['valid_start'][:4]}"
        sfmr = [p["sfmr"] for p in pts
                if p["sfmr"] is not None and not p.get("sfmr_suspect")]
        wspd = [p["wspd"] for p in pts if p["wspd"] is not None]
        psfc = [p["p_sfc"] for p in pts if p["p_sfc"] is not None]
        m["peak_sfmr_kt"] = max(sfmr) if sfmr else None
        m["peak_fl_wind_kt"] = max(wspd) if wspd else None
        m["min_p_sfc_hpa"] = min(psfc) if psfc else None
        m["vdm_centers"] = []
        m["sondes"] = []
    return missions


# ---- VDM enrichment (schema v2): defensive parsers over decode_vdm's dict.
# decode_vdm values pass through its isNA(): 'NA' -> NaN, numeric -> float,
# anything else -> lowercased string ('058 deg 34 kt', '19 c / 2448 m').
_RE_KT = re.compile(r"(-?\d+(?:\.\d+)?)\s*kt", re.I)
_RE_DEG = re.compile(r"(\d+(?:\.\d+)?)\s*deg", re.I)
_RE_C = re.compile(r"(-?\d+(?:\.\d+)?)\s*c\b", re.I)
_RE_M = re.compile(r"(-?\d+(?:\.\d+)?)\s*m\b", re.I)   # '2448 m'; skips 'nm'/'mb'
_VDM_ATCF_YEAR = re.compile(r"\b[A-Z]{2}\d{2}(\d{4})\b")


def _str(v, cap: int | None = None):
    """Decoder string value -> whitespace-collapsed str or None (NaN/absent)."""
    if not isinstance(v, str):
        return None
    s = re.sub(r"\s+", " ", v).strip()
    if cap and len(s) > cap:
        s = s[:cap].rstrip()
    return s or None


def _rng(v, lo: float, hi: float):
    """Numeric decoder value clamped to a sane range; strings/NaN -> None.
    The clamp is the guard against a wrong-FORMAT decode (e.g. an mb line
    read as kt) ever publishing a bogus value."""
    if isinstance(v, (str, bool)):
        return None
    f = _clean(v)
    return f if isinstance(f, (int, float)) and lo <= f <= hi else None


def _num_unit(v, rx, lo: float, hi: float, bare: bool = False):
    """First ``rx``-matched number inside a decoder STRING value; a bare
    numeric passes only when ``bare`` (the key itself IS that quantity).
    Absent/garbled/out-of-range -> None, never raises."""
    if not isinstance(v, str):
        return _rng(v, lo, hi) if bare else None
    m = rx.search(v)
    if not m:
        return None
    try:
        f = float(m.group(1))
    except ValueError:
        return None
    return _clean(f) if lo <= f <= hi else None


def _dir_spd(v):
    """'058 deg 34 kt' / '34 kt' / bare 34.0 -> (dir_deg, spd_kt)."""
    if not isinstance(v, str):
        return None, _rng(v, 0.0, 250.0)
    return (_num_unit(v, _RE_DEG, 0.0, 360.0),
            _num_unit(v, _RE_KT, 0.0, 250.0))


def _vdm_ref(content: str, ref_year: int | None = None):
    """Reference datetime for decode_vdm. Its FORMAT fork keys on the YEAR
    (>=2018 selects the modern URNT12 layout); passing None always raised,
    which silently nulled every enrichment field on every published record.
    Year: the bulletin's own ATCF id (AL022026 -> 2026), else the caller's
    mission-window year, else now. Day + time-of-day: the A. line, else the
    WMO header DDHHMM. The bulletin carries no month; July (31 days) keeps
    the datetime constructible - the decoded 'time' is never consumed
    (regex vdm_day/vdm_tod drive the mission attach)."""
    import datetime as _dt
    now_y = _dt.datetime.now(_dt.timezone.utc).year
    y = None
    ma = _VDM_ATCF_YEAR.search(content)
    if ma and 1989 <= int(ma.group(1)) <= now_y + 1:
        y = int(ma.group(1))
    if y is None:
        y = ref_year if (ref_year and 1989 <= ref_year <= now_y + 1) else now_y
    day, hh, mi, ss = 15, 0, 0, 0
    mt = _VDM_TIME.search(content)
    if mt:
        day, hh, mi, ss = (int(g) for g in mt.groups())
    else:
        mh = _SONDE_HDR_TS.search(content)
        if mh:
            day, hh, mi = (int(g) for g in mh.groups())
    if not 1 <= day <= 31:
        day = 15
    return _dt.datetime(y, 7, day, hh, mi, ss)


def _vdm_enrich(d: dict) -> dict:
    """decode_vdm's dict (either FORMAT's keys, or {} on decode failure) ->
    the flat v2 enrichment fields, every key always present (None-filled).
    FORMAT 1 naming trap: the modern E-line (center DROPSONDE surface wind,
    'E. 110 deg 10 kt') is filed by the vendored decoder under 'Location of
    Estimated Maximum Surface Wind Inbound'; the 1999-2017 format has the
    correctly named center-drop keys instead - both are handled."""
    fl_in_dir, fl_in_kt = _dir_spd(
        d.get("Maximum Flight Level Wind Inbound (kt)",
              d.get("Maximum Flight Level Wind Inbound")))
    fl_out_dir, fl_out_kt = _dir_spd(
        d.get("Maximum Flight Level Wind Outbound (kt)"))
    sfc_in = _num_unit(d.get("Estimated Maximum Surface Wind Inbound (kt)"),
                       _RE_KT, 0.0, 250.0, bare=True)
    sfc_out = _num_unit(d.get("Estimated Maximum Surface Wind Outbound (kt)"),
                        _RE_KT, 0.0, 250.0, bare=True)
    cd_dir, cd_kt = _dir_spd(
        d.get("Location of Estimated Maximum Surface Wind Inbound"))
    if cd_kt is None:
        cd_kt = _rng(d.get("Dropsonde Surface Wind Speed at Center (kt)"),
                     0.0, 250.0)
        cd_dir = _rng(d.get("Dropsonde Surface Wind Direction at Center (deg)"),
                      0.0, 360.0)
    t_out = d.get("Maximum Flight Level Temp & Pressure Altitude Outside Eye",
                  d.get("Maximum Flight Level Temp Outside Eye (C)"))
    t_in = d.get("Maximum Flight Level Temp & Pressure Altitude Inside Eye",
                 d.get("Maximum Flight Level Temp Inside Eye (C)"))
    dp = d.get("Dewpoint Temp (collected at same location as temp inside eye)",
               d.get("Dew Point Inside Eye (C)"))
    press_alt = _num_unit(t_out, _RE_M, 0.0, 20000.0)  # string-only: an F>=2
    if press_alt is None:                              # bare temp is NOT an alt
        press_alt = _num_unit(t_in, _RE_M, 0.0, 20000.0)
    fix_note = "; ".join(x for x in (_str(d.get("Fix"), 60),
                                     _str(d.get("Accuracy"), 60)) if x) or None
    sfc = [x for x in (sfc_in, sfc_out) if x is not None]
    return {
        "max_sfc_wind_kt": max(sfc) if sfc else None,   # back-compat key
        "max_sfc_wind_in_kt": sfc_in, "max_sfc_wind_out_kt": sfc_out,
        "max_sfc_wind_in_loc": _str(d.get(
            "Location & Time of the Estimated Maximum Surface Wind Inbound"), 80),
        "max_sfc_wind_out_loc": _str(d.get(
            "Location & Time of the Estimated Maximum Surface Wind Outbound"), 80),
        "max_fl_wind_in_kt": fl_in_kt, "max_fl_wind_in_dir_deg": fl_in_dir,
        "max_fl_wind_out_kt": fl_out_kt, "max_fl_wind_out_dir_deg": fl_out_dir,
        "max_fl_wind_in_loc": _str(d.get(
            "Location & Time of the Maximum Flight Level Wind Inbound",
            d.get("Location of the Maximum Flight Level Wind Inbound")), 80),
        "max_fl_wind_out_loc": _str(d.get(
            "Location & Time of the Maximum Flight Level Wind Outbound"), 80),
        "center_drop_sfc_wind_kt": cd_kt,
        "center_drop_sfc_wind_dir_deg": cd_dir,
        "eye_character": _str(d.get("Eye character"), 60),
        "eye_shape": _str(d.get("Eye Shape"), 30),
        "eye_diameter_nmi": _rng(d.get("Eye Diameter (nmi)",
                                       d.get("Eye Diameter 1 (nmi)")), 0, 400),
        "eye_diameter2_nmi": _rng(d.get("Eye Diameter 2 (nmi)"), 0, 400),
        "eye_major_nmi": _rng(d.get("Eye Major Axis (nmi)"), 0, 400),
        # the vendored decoder files the MAJOR-axis token under the minor
        # key too (elliptical G/M lines); a minor equal to the major is
        # that bug, not data - suppress it rather than publish a false axis
        "eye_minor_nmi": (lambda mn, mj: None if (mn is not None and
                          mn == mj) else mn)(
            _rng(d.get("Eye Minor Axis (nmi)"), 0, 400),
            _rng(d.get("Eye Major Axis (nmi)"), 0, 400)),
        "eye_orientation_deg": _rng(d.get("Orientation"), 0, 360),
        "temp_out_eye_c": _num_unit(t_out, _RE_C, -100, 60, bare=True),
        "temp_in_eye_c": _num_unit(t_in, _RE_C, -100, 60, bare=True),
        "dewpoint_in_eye_c": _num_unit(dp, _RE_C, -100, 60, bare=True),
        "press_alt_m": press_alt,
        "std_level_hpa": _rng(d.get("Standard Level (hPa)"), 100, 1070),
        "min_height_m": _rng(d.get("Minimum Height at Standard Level (m)"),
                             0, 6000),
        "fix_note": fix_note, "remarks": _str(d.get("Remarks"), 300),
    }


def _parse_vdm(content: str, ref_year: int | None = None) -> dict | None:
    """VDM center-fix parse (regex-primary, so it never trips the way
    tropycal's decode_vdm does on some real bulletins). atcf + center lat/lon
    are required; MSLP (D. line) + fix day/time-of-day are regex best-effort;
    the v2 enrichment fields come from the vendored decoder (never required -
    on decode failure every enrichment key is present but None). decode_vdm
    returns (missionname, dict) and NEEDS a reference datetime - the old call
    passed None (always raised) and checked the tuple with isinstance(dict),
    so max_sfc_wind_kt was null on every published record."""
    mll = _VDM_LATLON.search(content)
    if not mll:
        return None
    lat = float(mll.group(1)) * (1 if mll.group(2) == "N" else -1)
    lon = float(mll.group(3)) * (1 if mll.group(4) == "E" else -1)
    atcf = None
    ma = re.search(r"\b([A-Z]{2}\d{6})\b", content)
    if ma:
        atcf = ma.group(1).lower()
    mslp = None
    mm = _VDM_MSLP.search(content)
    if mm:
        v = float(mm.group(1))
        mslp = v if 850 <= v <= 1050 else None
    day = tod = None
    mt = _VDM_TIME.search(content)
    if mt:
        day = int(mt.group(1))
        tod = int(mt.group(2)) * 3600 + int(mt.group(3)) * 60 + int(mt.group(4))
    d = {}
    try:                                         # enrich (never required)
        _, dec = decode_vdm(_norm(content), _vdm_ref(content, ref_year))
        if isinstance(dec, dict):
            d = dec
    except Exception:                            # noqa: BLE001
        d = {}
    rec = {"atcf": atcf, "lat": round(lat, 2), "lon": round(lon, 2),
           "mslp_hpa": mslp, "vdm_day": day, "vdm_tod": tod}
    rec.update(_vdm_enrich(d))
    return rec


def _attach_nearest(missions: dict[str, dict], item: dict, key: str) -> None:
    """Append ``item`` to the time-nearest mission's ``key`` list (seeding the
    mission's atcf). If the item carries a day/time-of-day (vdm_day/vdm_tod),
    build a candidate datetime per mission from that mission's own year/month
    (all missions in a run share a window) and pick the smallest gap; stamp the
    item's ``t`` from the match. Else attach to the first mission with a track.
    Only tropical missions should be passed in."""
    if item.get("t") is None and item.get("vdm_day") is not None:
        best, best_gap, best_t = None, None, None
        for mm in missions.values():
            if not mm["track"]:
                continue
            mref = _iso(mm["valid_start"])
            try:
                cand = mref.replace(day=item["vdm_day"],
                                    hour=item["vdm_tod"] // 3600,
                                    minute=(item["vdm_tod"] % 3600) // 60,
                                    second=item["vdm_tod"] % 60)
            except ValueError:
                continue
            mid = _iso(mm["track"][len(mm["track"]) // 2]["t"])
            gap = abs((cand - mid).total_seconds())
            if best_gap is None or gap < best_gap:
                best, best_gap, best_t = mm, gap, cand
        if best is not None and best_gap is not None and best_gap < 18 * 3600:
            item = dict(item, t=best_t.strftime("%Y-%m-%dT%H:%M:%SZ"))
            item.pop("vdm_day", None)
            item.pop("vdm_tod", None)
            best[key].append(item)
            if item.get("atcf") and not best.get("atcf"):
                best["atcf"] = item["atcf"]
            return
    # no usable time: attach to the first mission with a track
    for mm in missions.values():
        if mm["track"]:
            item = {k: v for k, v in item.items()
                    if k not in ("vdm_day", "vdm_tod")}
            mm[key].append(item)
            if item.get("atcf") and not mm.get("atcf"):
                mm["atcf"] = item["atcf"]
            return


def add_vdm(missions: dict[str, dict], vdm_contents: list[str]) -> None:
    """Parse VDM center fixes and attach to the nearest mission (+ atcf)."""
    # Mission-window year: _vdm_ref's fallback for bulletins whose ATCF id
    # line is absent (mostly pre-2018 archives), so backfills still pick the
    # right decode_vdm FORMAT.
    ry = next((int(mm["valid_start"][:4]) for mm in missions.values()
               if mm.get("valid_start")), None)
    for content in vdm_contents:
        c = _parse_vdm(content, ref_year=ry)
        if c and c.get("lat") is not None:
            _attach_nearest(missions, c, "vdm_centers")


_SONDE_HDR_TS = re.compile(r"^[A-Z]{4}\d{2}\s+\w{4}\s+(\d{2})(\d{2})(\d{2})",
                           re.M)


def _sonde_levels(d: dict) -> list:
    """decode_dropsonde data['levels'] (a DataFrame: pres/hgt/temp/dwpt/wdir/
    wspd, the merged XXAA+XXBB+21212 profile) -> JSON rows [pres_hpa, hgt_m,
    temp_c, dwpt_c, wdir_deg, wspd_kt], surface->top (descending pressure).
    Rows without a pressure are dropped; capped at 200 rows (a real profile
    is ~15-45). 'levels' is NaN when the bulletin had no usable XXAA."""
    lv = d.get("levels")
    if lv is None or not hasattr(lv, "iterrows"):
        return []
    rows = []
    for _, r in lv.iterrows():
        p = _clean(r.get("pres"))
        if p is None:
            continue
        rows.append([p, _clean(r.get("hgt")), _clean(r.get("temp")),
                     _clean(r.get("dwpt")), _clean(r.get("wdir")),
                     _clean(r.get("wspd"))])
    rows.sort(key=lambda x: -x[0])
    return rows[:200]


def add_sondes(missions: dict[str, dict], sonde_contents: list[str]) -> None:
    """Decode dropsonde surface fixes -> markers on the nearest mission.

    The vendored ``decode_dropsonde`` returns ``(missionname, data)`` (a tuple,
    not a dict) and derives the release time from the bulletin RELATIVE to a
    reference ``date`` (it only knows the time-of-day, not the day/month/year).
    We seed that reference from the bulletin's own WMO header DDHHMM combined
    with a mission's year/month (all missions in a run share the ingest window),
    so ``TOPtime`` resolves to a real datetime; without it every sonde decodes
    with ``TOPtime=None`` and is silently dropped at the nearest-mission step."""
    if not missions:
        return
    # a year/month/day reference for the run (sondes share the ingest window)
    ref = None
    for mm in missions.values():
        if mm.get("track"):
            ref = _iso(mm["track"][len(mm["track"]) // 2]["t"])
            break
    if ref is None:
        return
    for content in sonde_contents:
        norm = _norm(content)
        # reference date for this bulletin: run year/month + header day/HHMM
        date = ref
        mh = _SONDE_HDR_TS.search(norm)
        if mh:
            try:
                date = ref.replace(day=int(mh.group(1)),
                                   hour=int(mh.group(2)),
                                   minute=int(mh.group(3)),
                                   second=0, microsecond=0)
            except ValueError:
                date = ref
        date = date.replace(tzinfo=None)         # decoder uses naive datetimes
        try:
            _name, d = decode_dropsonde(norm, date)
        except Exception:                        # noqa: BLE001
            continue
        if not isinstance(d, dict):
            continue
        lat = _clean(d.get("TOPlat") if d.get("TOPlat") is not None
                     else d.get("lat"))
        lon = _clean(d.get("TOPlon") if d.get("TOPlon") is not None
                     else d.get("lon"))
        if lat is None or lon is None:
            continue
        t = d.get("TOPtime")
        b_lat, b_lon = _clean(d.get("BOTTOMlat")), _clean(d.get("BOTTOMlon"))
        bt = d.get("BOTTOMtime")
        try:
            obsnum = int(d.get("obsnum"))
        except (TypeError, ValueError):
            obsnum = None
        sonde = {"t": (t.strftime("%Y-%m-%dT%H:%M:%SZ")
                       if hasattr(t, "strftime") else None),
                 "lat": lat, "lon": lon,
                 "sfc_wind_kt": _clean(d.get("WL150spd")),
                 # v2 enrichment (additive): full profile + splash + scalars
                 "levels": _sonde_levels(d),
                 "slp_hpa": _clean(d.get("slp")),
                 "top_hpa": _clean(d.get("top")),
                 "splash": ({"lat": b_lat, "lon": b_lon,
                             "t": (bt.strftime("%Y-%m-%dT%H:%M:%SZ")
                                   if hasattr(bt, "strftime") else None)}
                            if b_lat is not None and b_lon is not None
                            else None),
                 "location": _str(d.get("location"), 20),
                 "octant": _str(d.get("octant"), 4), "obsnum": obsnum,
                 "mbl_dir_deg": _clean(d.get("MBLdir")),
                 "mbl_spd_kt": _clean(d.get("MBLspd")),
                 "dlm_dir_deg": _clean(d.get("DLMdir")),
                 "dlm_spd_kt": _clean(d.get("DLMspd")),
                 "wl150_dir_deg": _clean(d.get("WL150dir"))}
        # nearest-in-time mission; fall back to the first mission with a track
        # so a sonde whose time can't be stamped is NOT silently dropped.
        best, best_dt = None, None
        if sonde["t"] is not None:
            for mm in missions.values():
                if not mm["track"]:
                    continue
                mid_t = mm["track"][len(mm["track"]) // 2]["t"]
                gap = abs((_iso(mid_t) - _iso(sonde["t"])).total_seconds())
                if best_dt is None or gap < best_dt:
                    best, best_dt = mm, gap
            if best is not None and best_dt is not None and best_dt >= 12 * 3600:
                best = None                      # too far in time; fall through
        if best is None:
            best = next((mm for mm in missions.values() if mm["track"]), None)
        if best is not None:
            best["sondes"].append(sonde)


def _iso(s):
    import datetime as _dt
    return _dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=_dt.timezone.utc)
