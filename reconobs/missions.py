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

# variables that get nulled when their name appears in the HDOB row `flag`
_FLAGGABLE = ("p_sfc", "temp", "dwpt", "wdir", "wspd", "pkwnd", "sfmr", "rain")
_INVEST_NAMES = ("INVEST", "GENESIS", "AREA", "SUSPECT", "DISTURB")
# 2nd-token / name markers for NON-tropical sorties (research, training,
# ferry, air-quality campaigns) - excluded from the storm list.
_NON_TC = ("TRAIN", "SURVEY", "TEST", "FERRY", "LOGISTIC", "RESEARCH",
           "TEXAQS", "CALVAL", "CALIB", "AEROSE", "OWLES", "SHOUT")
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
    """Decode one HDOB bulletin -> DataFrame, or None on failure."""
    try:
        df = decode_hdob(_norm(content), mission_row=2)
        return df if len(df) else None
    except Exception:                            # noqa: BLE001 - malformed msg
        return None


def storm_from_mission_id(mid: str) -> tuple[str, str, str]:
    """(aircraft, flight, storm_name) from a mission_id 'AF302-0514A-MILTON'."""
    parts = (mid or "").split("-")
    aircraft = parts[0] if parts else ""
    flight = parts[1] if len(parts) > 1 else ""
    name = parts[-1] if len(parts) > 2 else ""
    return aircraft, flight, name.strip().upper()


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
    if not up or (up.isdigit() and len(up) > 2):
        return False
    if any(up.startswith(k) for k in _NON_TC):
        return False
    return True


def _row_records(df) -> list[dict]:
    """DataFrame -> JSON-safe track points, nulling QC-flagged variables."""
    out = []
    for _, r in df.iterrows():
        flag = list(r.get("flag") or [])
        rec = {
            "t": r["time"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "lat": _clean(r["lat"]), "lon": _clean(r["lon"]),
            "plane_p": _clean(r.get("plane_p")),
            "p_sfc": _clean(r.get("p_sfc")),
            "wspd": _clean(r.get("wspd")), "wdir": _clean(r.get("wdir")),
            "pkwnd": _clean(r.get("pkwnd")), "sfmr": _clean(r.get("sfmr")),
            "temp": _clean(r.get("temp")), "dwpt": _clean(r.get("dwpt")),
        }
        # null any flagged plotted variable (spec: drop before min/max/color)
        for var in _FLAGGABLE:
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
        mid = str(df["mission_id"].iloc[0])
        aircraft, flight, name = storm_from_mission_id(mid)
        m = missions.setdefault(mid, {
            "mission_id": mid, "aircraft": aircraft, "flight": flight,
            "storm_name": name, "is_invest": is_invest_name(name),
            "is_tropical": is_tropical_mission(name, flight),
            "atcf": None, "_points": {},
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
        sfmr = [p["sfmr"] for p in pts if p["sfmr"] is not None]
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


def add_sondes(missions: dict[str, dict], sonde_contents: list[str]) -> None:
    """Decode dropsonde surface fixes -> markers on the nearest mission."""
    for content in sonde_contents:
        try:
            d = decode_dropsonde(_norm(content), None)
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
        t = d.get("time")
        sonde = {"t": t.strftime("%Y-%m-%dT%H:%M:%SZ")
                 if hasattr(t, "strftime") else None,
                 "lat": lat, "lon": lon,
                 "sfc_wind_kt": _clean(d.get("sfc_wnd_spd"))}
        # nearest-in-time mission
        best, best_dt = None, None
        for mm in missions.values():
            if not mm["track"] or not sonde["t"]:
                continue
            mid_t = mm["track"][len(mm["track"]) // 2]["t"]
            gap = abs((_iso(mid_t) - _iso(sonde["t"])).total_seconds())
            if best_dt is None or gap < best_dt:
                best, best_dt = mm, gap
        if best is not None and (best_dt is None or best_dt < 12 * 3600):
            best["sondes"].append(sonde)


def _iso(s):
    import datetime as _dt
    return _dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=_dt.timezone.utc)
