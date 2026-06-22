"""reconobs.tcpod - parse the Tropical Cyclone Plan of the Day.

NHC PIL REPRPD (issued by CARCAH). 24-hr recon tasking: a header (issuance +
VALID window + TCPOD number), then ``I. ATLANTIC REQUIREMENTS`` and
``II. PACIFIC REQUIREMENTS`` sections. Each numbered requirement is either
"NEGATIVE RECONNAISSANCE REQUIREMENTS", a tasking with lettered fields
A-H (fix time / aircraft / takeoff / target lat-lon / fix window / altitude /
mission type / remarks), or "OUTLOOK FOR SUCCEEDING DAY" (free narrative).

parse_tcpod(text) -> dict (JSON-ready). Tolerant: off-season "NEGATIVE"
plans, AMENDMENT/CORRECTION suffixes, and multi-line wrapped fields all parse;
anything unrecognised lands in ``raw`` so nothing is silently lost.
"""
from __future__ import annotations

import datetime as _dt
import re

_MONTHS = {m: i for i, m in enumerate(
    ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST",
     "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"], start=1)}

_NUM = re.compile(r"^\s*(\d+)\.\s+(.*)$")
_LETTER = re.compile(r"^\s*([A-H])\.\s*(.*)$")
_VALID = re.compile(r"VALID\s+(\d{2})/(\d{4})Z\s+TO\s+(\d{2})/(\d{4})Z"
                    r"\s+([A-Z]+)\s+(\d{4})")
_TCNUM = re.compile(r"TCPOD\s+NUMBER\.*\s*([0-9]{2}-[0-9]{3})\s*(\w+)?")
_LATLON = re.compile(r"(\d+(?:\.\d+)?)\s*([NS])\s+(\d+(?:\.\d+)?)\s*([EW])")
_HDR_DTG = re.compile(r"^[A-Z]{4}\d{2}\s+\w+\s+(\d{6})\s*$")

_FIELD_NAMES = {
    "A": "fix_time", "B": "aircraft", "C": "takeoff",
    "D": "target", "E": "fix_window", "F": "altitude",
    "G": "mission_type", "H": "remarks",
}


def _valid_dt(day: str, hhmm: str, month: int, year: int) -> str | None:
    try:
        return _dt.datetime(year, month, int(day), int(hhmm[:2]),
                            int(hhmm[2:]), tzinfo=_dt.timezone.utc
                            ).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _parse_target(value: str) -> dict | None:
    m = _LATLON.search(value)
    if not m:
        return None
    lat = float(m.group(1)) * (1 if m.group(2) == "N" else -1)
    lon = float(m.group(3)) * (1 if m.group(4) == "E" else -1)
    return {"lat": round(lat, 2), "lon": round(lon, 2), "raw": m.group(0)}


def _finish_item(num, title, fields, outlook_lines):
    """Turn an accumulated numbered item into a structured dict."""
    title = (title or "").strip()
    up = title.upper()
    base = {"n": num, "title": title}
    if "NEGATIVE" in up and "RECON" in up:
        base["kind"] = "negative"
        return base
    if "OUTLOOK" in up and "SUCCEEDING" in up:
        base["kind"] = "outlook"
        base["text"] = " ".join(x.strip() for x in outlook_lines).strip()
        return base
    # a tasking: split title into task + status (".....STATUS" trailer)
    status = None
    mt = re.match(r"(.*?)\.{2,}\s*(.+)$", title)
    if mt:
        base["title"], status = mt.group(1).strip(), mt.group(2).strip()
    base["kind"] = "mission"
    base["status"] = status
    for letter, raw in fields.items():
        name = _FIELD_NAMES.get(letter, letter)
        val = " ".join(raw).strip()
        if name == "target":
            base["target"] = _parse_target(val) or {"raw": val}
        else:
            base[name] = val
    return base


def _parse_section(lines: list[str], month: int, year: int) -> dict:
    """Parse one basin section's numbered items."""
    items: list[dict] = []
    cur_num = cur_title = None
    cur_fields: dict[str, list[str]] = {}
    cur_letter = None
    outlook_lines: list[str] = []
    in_outlook = False

    def flush():
        nonlocal cur_num
        if cur_num is not None:
            items.append(_finish_item(cur_num, cur_title, cur_fields,
                                      outlook_lines))

    for ln in lines:
        mnum = _NUM.match(ln)
        if mnum and not _LETTER.match(ln):
            flush()
            cur_num, cur_title = mnum.group(1), mnum.group(2)
            cur_fields, cur_letter = {}, None
            outlook_lines = []
            in_outlook = ("OUTLOOK" in cur_title.upper()
                          and "SUCCEEDING" in cur_title.upper())
            continue
        if cur_num is None:
            continue
        mlet = _LETTER.match(ln)
        if mlet and not in_outlook:
            cur_letter = mlet.group(1)
            cur_fields.setdefault(cur_letter, []).append(mlet.group(2))
        elif in_outlook:
            outlook_lines.append(ln)
        elif cur_letter is not None and ln.strip():
            cur_fields[cur_letter].append(ln.strip())   # wrapped continuation
    flush()
    missions = [i for i in items if i.get("kind") == "mission"]
    outlook = [i for i in items if i.get("kind") == "outlook"]
    negative = any(i.get("kind") == "negative" for i in items) and not missions
    return {"negative": negative, "missions": missions,
            "outlook": [o.get("text", "") for o in outlook]}


def parse_tcpod(text: str) -> dict:
    """Parse a REPRPD TCPOD bulletin into JSON-ready structure."""
    raw = text or ""
    lines = raw.split("\n")
    out: dict = {"pil": "REPRPD", "raw": raw}

    # ---- header ----
    issued_utc = None
    for ln in lines[:6]:
        m = _HDR_DTG.match(ln.strip())
        if m:                                  # DDHHMM of the current month/yr
            out["issued_dtg"] = m.group(1)
    for ln in lines:
        if "CARCAH" not in ln and re.search(r"\d{4}\s+(AM|PM)\s+\w{3}\s+", ln):
            out["issued_local"] = ln.strip()
            break
    mv = _VALID.search(raw)
    month = year = None
    if mv:
        month = _MONTHS.get(mv.group(5), None)
        year = int(mv.group(6))
        out["valid_from"] = f"{mv.group(1)}/{mv.group(2)}Z"
        out["valid_to"] = f"{mv.group(3)}/{mv.group(4)}Z"
        out["valid_month"], out["valid_year"] = mv.group(5), year
        if month:
            out["valid_from_utc"] = _valid_dt(mv.group(1), mv.group(2),
                                              month, year)
            # to-window may roll into next month
            to_m, to_y = month, year
            if int(mv.group(3)) < int(mv.group(1)):
                to_m = month + 1
                if to_m > 12:
                    to_m, to_y = 1, year + 1
            out["valid_to_utc"] = _valid_dt(mv.group(3), mv.group(4),
                                            to_m, to_y)
    mt = _TCNUM.search(raw)
    if mt:
        out["tcpod_number"] = mt.group(1)
        out["amendment"] = bool(mt.group(2) and mt.group(2).upper()
                                in ("AMENDMENT", "CORRECTION", "CORRECTED"))
        out["amendment_kind"] = (mt.group(2) or "").upper() or None

    # ---- sections ----
    def section_slice(start_re):
        si = None
        for i, ln in enumerate(lines):
            if re.match(start_re, ln):
                si = i
                break
        if si is None:
            return []
        body = []
        for ln in lines[si + 1:]:
            if re.match(r"^\s*I{1,3}\.\s+[A-Z]+\s+REQUIREMENTS", ln):
                break
            if ln.strip() in ("$$", "NNNN"):
                break
            body.append(ln)
        return body

    atl = section_slice(r"^\s*I\.\s+ATLANTIC")
    pac = section_slice(r"^\s*II\.\s+PACIFIC")
    out["basins"] = {
        "atlantic": _parse_section(atl, month or 1, year or 2000),
        "pacific": _parse_section(pac, month or 1, year or 2000),
    }
    out["has_active_missions"] = bool(
        out["basins"]["atlantic"]["missions"]
        or out["basins"]["pacific"]["missions"])
    return out
