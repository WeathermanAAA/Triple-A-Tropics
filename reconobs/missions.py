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


def _parse_vdm(content: str) -> dict | None:
    """VDM center-fix parse (regex-primary, so it never trips the way
    tropycal's decode_vdm does on some real bulletins). atcf + center lat/lon
    are required; MSLP (D. line) + fix day/time-of-day + an optional max-wind
    enrichment from the vendored decoder are best-effort. Returns
    {atcf, lat, lon, mslp_hpa, max_sfc_wind_kt, vdm_day, vdm_tod} or None."""
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
    max_wind = None
    try:                                         # enrich (never required)
        d = decode_vdm(_norm(content), None)
        if isinstance(d, dict):
            max_wind = _clean(
                d.get("Estimated Maximum Surface Wind Inbound (kt)"))
    except Exception:                            # noqa: BLE001
        pass
    return {"atcf": atcf, "lat": round(lat, 2), "lon": round(lon, 2),
            "mslp_hpa": mslp, "max_sfc_wind_kt": max_wind,
            "vdm_day": day, "vdm_tod": tod}


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
    for content in vdm_contents:
        c = _parse_vdm(content)
        if c and c.get("lat") is not None:
            _attach_nearest(missions, c, "vdm_centers")


_SONDE_HDR_TS = re.compile(r"^[A-Z]{4}\d{2}\s+\w{4}\s+(\d{2})(\d{2})(\d{2})",
                           re.M)


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
        sonde = {"t": (t.strftime("%Y-%m-%dT%H:%M:%SZ")
                       if hasattr(t, "strftime") else None),
                 "lat": lat, "lon": lon,
                 "sfc_wind_kt": _clean(d.get("WL150spd"))}
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
