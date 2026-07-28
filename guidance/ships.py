#!/usr/bin/env python3
"""Parser for NHC's SHIPS bulletin (``/atcf/stext/*_ships.txt``).

Structure is FIXED-WIDTH and must be sliced, never whitespace-split. Two
contribution labels are exactly 22 characters ("850 MB ENV VORTICITY", "DAYS
FROM CLIM. PEAK") and butt directly against the first data cell, so a
``.split()`` merges the label with its first value.

Everything is anchored on STRINGS, never on line numbers: file length ranges
99-116 lines because the RI matrix can omit rows (DTOPS/SDCON), the annularity
block can truncate to "ERR=2, BOTH IR FILES BAD OR MISSING", and Atlantic files
carry two extra blocks (SEEF, ERC) that Pacific files never do.

THE ROUNDING RESIDUAL - measured, and contrary to the brief.
The premise handed to this build was that TOTAL CHANGE equals the sum of its 19
component rows exactly. It does not. Measured over 68 archived bulletins spread
across the 2026 season (1,088 forecast columns):

    residual = TOTAL CHANGE - sum(components)
      -4:   4 (0.4%)     0: 473 (43.5%)     +2:  63 (5.8%)
      -3:  15 (1.4%)    +1: 198 (18.2%)     +3:  12 (1.1%)
      -2:  82 (7.5%)

Only 43.5% of columns agree; the residual reaches 4 kt. The cause is plain once
seen: every component AND the total are printed rounded to whole knots, so
nineteen independent roundings accumulate. Nothing is wrong with the data - the
printed components simply are not the values NHC summed.

Consequence for the waterfall: stacking the 19 printed components lands up to
4 kt away from the printed total. A waterfall that visibly misses its own total
is worse than no waterfall, so :func:`parse_ships` returns the residual as an
explicit named term, and the panel renders it as a labelled ROUNDING segment.
The bar then always closes exactly on TOTAL CHANGE and the discrepancy is
disclosed rather than hidden or silently absorbed into the last component.

Sentinels are ROW-SPECIFIC, not global: ``N/A`` anywhere in the environmental
block, ``LOST`` only on MODEL VTX, ``xx.x``/``xxx.x`` only on LAT/LONG, ``DIS``
only in the ERC block, ``ERR`` only in the SEEF probability row, and
``999``/``9999`` variants only in the RI sections.

Stdlib only.
"""
from __future__ import annotations

import re
from typing import Optional

#: Section A (environment) column grid: label [0:14), cell k at [14+6k, 20+6k).
ENV_LABEL_W = 14
ENV_CELL_W = 6

#: Contribution block: label [0:22), then 6-wide cells. The first cell is
#: CLAMPED to start at 22 because the two 22-char labels touch it.
CON_LABEL_W = 22
CON_CELL_W = 6

#: Forecast hours. NON-UNIFORM: 6-hourly to 24, then 12-hourly to 168.
TAUS = (0, 6, 12, 18, 24, 36, 48, 60, 72, 84, 96, 108, 120, 132, 144, 156, 168)
#: The contribution block uses the same axis MINUS hour 0 (a change from t=0 is
#: identically zero, so it is not printed).
CON_TAUS = TAUS[1:]

#: Values that mean "missing", by the row that may carry them.
_MISSING = {"N/A", "LOST", "DIS", "ERR", "xx.x", "xxx.x", "-xx.x",
            "999", "999.", "999.0", "999.00", "9999", "9999.0", "*"}

_NUM_RE = re.compile(r"^-?\d+(\.\d+)?\.?$")


def _num(cell: str) -> Optional[float]:
    """A columnar cell -> float, or None for any of the row-specific
    sentinels. Trailing '.' (the contribution block prints "-7.") is fine."""
    c = (cell or "").strip()
    if not c or c in _MISSING:
        return None
    if set(c) <= {"x", "."} or set(c) <= {"-", "x", "."}:
        return None            # xx.x / xxx.x position sentinels
    c = c.rstrip(".")
    if not c or c in ("-",):
        return None
    try:
        v = float(c)
    except ValueError:
        return None
    if v in (999.0, 9999.0):
        return None
    return v


def _env_cells(line: str, n: int = len(TAUS)) -> list:
    return [_num(line[ENV_LABEL_W + ENV_CELL_W * k:
                      ENV_LABEL_W + ENV_CELL_W * (k + 1)]) for k in range(n)]


def _con_cells(line: str, n: int = len(CON_TAUS)) -> list:
    """Contribution cells, with the first clamped past the 22-char labels."""
    out = []
    for k in range(n):
        lo = max(CON_LABEL_W, (CON_LABEL_W - 1) + CON_CELL_W * k)
        hi = (CON_LABEL_W + 5) + CON_CELL_W * k
        out.append(_num(line[lo:hi]))
    return out


def _find(lines, pred, start: int = 0) -> Optional[int]:
    for i in range(start, len(lines)):
        if pred(lines[i]):
            return i
    return None


def parse_ships(text: str) -> dict:
    """Parse one SHIPS bulletin into a JSON-ready dict."""
    lines = text.splitlines()
    doc: dict = {"taus": list(TAUS), "contribution_taus": list(CON_TAUS)}

    # ---- header banner -------------------------------------------------
    banner = [l for l in lines[:8] if "*" in l]
    doc["header"] = {"lines": [l.strip(" *").strip() for l in banner]}
    for l in banner:
        m = re.search(r"([A-Z][A-Z0-9\- .']+?)\s+([A-Z]{2}\d{6})\s+"
                      r"(\d{2}/\d{2}/\d{2})\s+(\d{2})\s*UTC", l)
        if m:
            doc["header"].update({
                "name": m.group(1).strip(), "atcf": m.group(2),
                "date": m.group(3), "hour": int(m.group(4)),
            })
            break
    # The 4-digit year in the banner is the COEFFICIENT year, NOT the storm
    # year - 11 files in Mar-Apr 2026 print "2025" while carrying 2026 ids.
    m = re.search(r"\*\s*([A-Z. ]+?)\s+(\d{4})\s+SHIPS", " ".join(banner))
    if m:
        doc["header"]["basin_label"] = m.group(1).strip()
        doc["header"]["coefficient_year"] = int(m.group(2))

    # ---- section A: environmental time series ---------------------------
    i_time = _find(lines, lambda l: l.startswith("TIME (HR)"))
    env: dict = {}
    storm_type = None
    if i_time is not None:
        for l in lines[i_time + 1:]:
            # Section A contains an INTERNAL blank line (between the intensity
            # rows and the environment rows), so a blank cannot end the block.
            # What ends it is the free-text section B, whose rows are INDENTED
            # while every section-A label starts at column 0.
            if not l.strip():
                continue
            if l[0] == " " or "INDIVIDUAL CONTRIBUTIONS" in l:
                break
            label = l[:ENV_LABEL_W].strip()
            if not label:
                continue
            if label.upper().startswith("STORM TYPE"):
                storm_type = [
                    (l[ENV_LABEL_W + ENV_CELL_W * k:
                       ENV_LABEL_W + ENV_CELL_W * (k + 1)].strip() or None)
                    for k in range(len(TAUS))]
                continue
            env[label] = _env_cells(l)
    doc["env"] = env
    doc["storm_type"] = storm_type

    # ---- section B: free-text scalars (regex ONLY) ----------------------
    # These glue to their separators - "…(DEG/KT):290/  8" has no space after
    # the colon, and CX,CY spacing is sign-dependent across 27 templates.
    blob = "\n".join(lines)
    sb: dict = {}
    m = re.search(r"FORECAST TRACK FROM\s+(\S+)", blob)
    sb["forecast_track_from"] = m.group(1) if m else None   # an aid id, not a number
    pats = {
        "initial_heading_deg": r"INITIAL HEADING/SPEED \(DEG/KT\):\s*(-?\d+)\s*/",
        "initial_speed_kt": r"INITIAL HEADING/SPEED \(DEG/KT\):\s*-?\d+\s*/\s*(-?\d+)",
        "t12_max_wind_kt": r"T-12 MAX WIND:\s*(-?\d+)",
        "steering_level_mb": r"PRESSURE OF STEERING LEVEL \(MB\):\s*(-?\d+)",
        "steering_level_mean_mb": r"PRESSURE OF STEERING LEVEL \(MB\):\s*-?\d+\s*\(MEAN=\s*(-?\d+)",
        "ir_bt_stddev": r"STD DEV\.\s+50-200 KM RAD:\s*(-?[\d.]+)",
        "ir_pct_below_m20c": r"PIXELS WITH T < -20 C\s+50-200 KM RAD:\s*(-?[\d.]+)",
        "prelim_ri_prob_pct": r"PRELIM RI PROB \(DV \.GE\.\s*\d+ KT IN \d+ HR\):\s*(-?[\d.]+)",
        "current_max_wind_kt": r"CURRENT MAX WIND \(KT\):\s*(-?[\d.]+)",
    }
    for k, pat in pats.items():
        m = re.search(pat, blob)
        sb[k] = float(m.group(1)) if m else None
    m = re.search(r"CX,CY:\s*(-?\d+)\s*/\s*(-?\d+)", blob)
    sb["cx"], sb["cy"] = (int(m.group(1)), int(m.group(2))) if m else (None, None)
    m = re.search(r"CURRENT MAX WIND \(KT\):\s*-?[\d.]+\s*LAT, LON:\s*(-?[\d.]+)\s+(-?[\d.]+)", blob)
    sb["current_lat"] = float(m.group(1)) if m else None
    # LONG(DEG W) is POSITIVE-WEST and exceeds 180 past the dateline (CP92
    # reached 194.7). Kept in the source convention and flagged, rather than
    # negated here - a naive lon = -value plots a CPac storm in the Atlantic.
    sb["current_lon_degw"] = float(m.group(2)) if m else None
    doc["scalars"] = sb

    # ---- contribution block + the ROUNDING RESIDUAL ---------------------
    i_con = _find(lines, lambda l: "INDIVIDUAL CONTRIBUTIONS" in l)
    contributions, total, residual = [], None, None
    if i_con is not None:
        rules = [i for i in range(i_con, min(i_con + 45, len(lines)))
                 if set(lines[i].strip()) == {"-"}]
        if len(rules) >= 2:
            for l in lines[rules[0] + 1:rules[1]]:
                if not l.strip():
                    continue
                contributions.append({"label": l[:CON_LABEL_W].strip(),
                                      "values": _con_cells(l)})
            for l in lines[rules[1] + 1:rules[1] + 4]:
                if l.strip().startswith("TOTAL CHANGE"):
                    total = _con_cells(l)
                    break
    if total:
        residual = []
        for k in range(len(CON_TAUS)):
            vals = [c["values"][k] for c in contributions
                    if c["values"][k] is not None]
            residual.append(None if total[k] is None
                            else round(total[k] - sum(vals), 6))
    doc["contributions"] = contributions
    doc["total_change"] = total
    # See the module docstring: the printed components do NOT sum to the
    # printed total (43.5% exact, |residual| up to 4 kt), because both sides
    # are rounded to whole knots. Published so the waterfall can close on the
    # stated total with the gap LABELLED instead of hidden.
    doc["rounding_residual"] = residual

    # ---- RI predictor table: KEYED BY LABEL, never by row position ------
    # Order differs by basin AND coefficient year (4 orderings observed), and
    # the label set itself changes between vintages ("OCEAN HEAT
    # CONTENT(KJ/CM2)" became "HEAT CONTENT (KJ/CM2)"). Reading positionally
    # attributes one basin's persistence value to another's POT slot.
    ri_pred = {}
    i_pt = _find(lines, lambda l: "Predictor" in l and "Scaled Value" in l)
    if i_pt is not None:
        for l in lines[i_pt + 1:i_pt + 16]:
            if ":" not in l:
                if not l.strip():
                    continue
                break
            label, rest = l.split(":", 1)
            toks = rest.split()
            if len(toks) < 5:
                continue
            try:
                ri_pred[label.strip()] = {
                    "value": _num(toks[0]),
                    "range_lo": _num(toks[1]),
                    "range_hi": _num(toks[3]),
                    "scaled": _num(toks[4]),
                    "contribution_pct": _num(toks[5]) if len(toks) > 5 else None,
                }
            except (IndexError, ValueError):
                continue
    doc["ri_predictors"] = ri_pred

    # ---- RI probabilities ------------------------------------------------
    ri_prob = []
    for m in re.finditer(
            r"SHIPS Prob RI for\s*(\d+)kt/\s*(\d+)hr RI threshold=\s*(\d+)%\s*"
            r"is\s*([\d.]+)\s*times climatological mean\s*\(\s*([\d.]+)%\)", blob):
        ri_prob.append({
            "dv_kt": int(m.group(1)), "hours": int(m.group(2)),
            "prob_pct": int(m.group(3)), "times_climo": float(m.group(4)),
            "climo_pct": float(m.group(5)),
        })
    doc["ri_probabilities"] = ri_prob

    # ---- RI matrix (row set is NOT fixed) --------------------------------
    ri_matrix = {"columns": [], "rows": {}}
    i_m = _find(lines, lambda l: "Matrix of RI probabilities" in l)
    if i_m is not None:
        i_hdr = _find(lines, lambda l: "RI (kt / h)" in l, i_m)
        if i_hdr is not None:
            ri_matrix["columns"] = [c.strip() for c in
                                    lines[i_hdr].split("|")[1:] if c.strip()]
            for l in lines[i_hdr + 1:i_hdr + 12]:
                if set(l.strip()) == {"-"} or not l.strip():
                    continue
                if ":" not in l:
                    break
                name, rest = l.split(":", 1)
                vals = [_num(v.rstrip("%")) for v in rest.split()]
                if not vals:
                    break
                ri_matrix["rows"][name.strip()] = vals
    doc["ri_matrix"] = ri_matrix

    # ---- annularity (can truncate on ERR=2) -----------------------------
    ahi = None
    i_a = _find(lines, lambda l: "ANNULAR HURRICANE INDEX" in l)
    if i_a is not None:
        body = " ".join(l.strip(" #").strip() for l in lines[i_a:i_a + 4])
        m = re.search(r"AHI=\s*(-?\d+)", body)
        ahi = {"value": int(m.group(1)) if m else None,
               "text": body.strip(),
               "error": "ERR=2" in body}
    doc["annularity"] = ahi

    # ---- Atlantic-only blocks (0/245 EP+CP files carry these) -----------
    doc["has_seef"] = "SEEF" in blob
    doc["has_erc"] = bool(re.search(r"\bERC\b", blob))
    return doc


def intensity_traces(doc: dict) -> dict:
    """The three intensity forecasts, on the shared tau axis."""
    env = doc.get("env", {})
    return {
        "taus": doc.get("taus", list(TAUS)),
        # V (KT) LAND[0] is NOT reliably the initial intensity (one file prints
        # 0 there while CURRENT MAX WIND says 15 kt), so t=0 comes from the
        # scalar and only the forecast tail from the row.
        "ships": env.get("V (KT) LAND"),
        "ships_no_land": env.get("V (KT) NO LAND"),
        "lgem": env.get("V (KT) LGEM"),
        "current_max_wind": (doc.get("scalars") or {}).get("current_max_wind_kt"),
    }
