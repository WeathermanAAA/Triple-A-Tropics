"""SHIPS statistical-dynamical intensity guidance for the Situation Room.

NHC posts one plain-text SHIPS file per storm per cycle at ftp.nhc.noaa.gov/atcf/stext/<YYMMDDHH><BB><NN><YY>_ships.txt.
parse() turns the newest file into the bundle's "ships" block: the forecast table (land / no-land / LGEM intensity, storm
type, environment), each predictor's contribution to the intensity change, the rapid-intensification probabilities
(every scheme NHC prints, with climatology) and the RII predictor table, plus the annular and secondary-eyewall indices.
The three cycles before it add only their intensity rows, for the run-to-run trend.
"""
import re

BASE = "https://ftp.nhc.noaa.gov/atcf/stext/"
ROWS = {"V (KT) NO LAND": "vnl", "V (KT) LAND": "v", "V (KT) LGEM": "lgem", "Storm Type": "type", "SHEAR (KT)": "shear",
        "SHEAR DIR": "sdir", "SST (C)": "sst", "POT. INT. (KT)": "pot", "200 MB T (C)": "t200", "TH_E DEV (C)": "thed",
        "700-500 MB RH": "rh", "MODEL VTX (KT)": "vtx", "850 MB ENV VOR": "vor", "200 MB DIV": "div", "700-850 TADV": "tadv",
        "LAND (KM)": "land", "LAT (DEG N)": "lat", "LONG(DEG W)": "lon", "STM SPEED (KT)": "spd", "HEAT CONTENT": "ohc"}


def _num(x):
    try: return float(x)
    except ValueError: return None


def code(sid):
    """al092026 -> AL0926"""
    return f"{sid[:2].upper()}{sid[2:4]}{sid[6:8]}"


def files(get, sid):
    idx = get(BASE, 300).decode("utf8", "replace")
    return sorted(set(re.findall(r'href="(\d{8}' + code(sid) + r'_ships\.txt)"', idx)))


def table(t):
    """the fixed-width forecast table: label in columns 0-14, values after"""
    taus, out = None, {}
    for ln in t.splitlines():
        lab = ln[:15].strip()
        if lab == "TIME (HR)": taus = [int(x) for x in ln[15:].split()]; continue
        k = ROWS.get(lab)
        if not k or taus is None or k in out: continue
        vals = ln[15:].split()
        out[k] = [v if k == "type" and v not in ("N/A", "LOST", "DIS") else (None if k == "type" else _num(v)) for v in vals][:len(taus)]
    return taus, out


def parse(t):
    taus, rows = table(t)
    if not taus or "v" not in rows: return None
    if "OHC NOT AVAILABLE" in t or not any(rows.get("ohc") or []): rows.pop("ohc", None)
    h = re.search(r"\*\s+(\S.*?)\s+([A-Z]{2}\d{6})\s+(\d\d)/(\d\d)/(\d\d)\s+(\d\d) UTC", t)
    init = f"20{h.group(5)}-{h.group(3)}-{h.group(4)}T{h.group(6)}:00:00Z" if h else None
    # individual contributions to the intensity change
    contrib, ctaus = [], None
    m = re.search(r"INDIVIDUAL CONTRIBUTIONS TO INTENSITY CHANGE\s*\n(.*?)\n\s*-{20,}\n(.*?)\n\s*-{20,}\n(.*?)\n", t, re.S)
    if m:
        ctaus = [int(x) for x in m.group(1).split()]
        for ln in m.group(2).splitlines():
            if not ln.strip(): continue
            name = ln[:24].strip(); vals = [_num(x.rstrip(".")) for x in ln[24:].split()]
            contrib.append({"n": name, "v": vals[:len(ctaus)]})
        tot = [_num(x.rstrip(".")) for x in m.group(3).split()[2:]]
        contrib_total = tot[:len(ctaus)]
    else: contrib_total = []
    # rapid intensification
    ri = [{"k": f"{a}/{b}", "p": float(p), "x": float(x), "c": float(c)} for a, b, p, x, c in
          re.findall(r"SHIPS Prob RI for\s+(\d+)kt/\s*(\d+)hr RI threshold=\s*([\d.]+)% is\s+([\d.]+) times climatological mean \(\s*([\d.]+)%\)", t)]
    mx = {}
    mm = re.search(r"RI \(kt / h\)\s*\|(.*?)\n-+\n(.*?)\n\s*\n", t, re.S)
    cols = [c.strip() for c in mm.group(1).split("|")] if mm else []
    if mm:
        for name, vals in re.findall(r"^\s*([A-Za-z][\w\-]*):\s+((?:[\d.]+%\s*)+)$", mm.group(2), re.M):
            mx[name] = [float(v) for v in re.findall(r"([\d.]+)%", vals)]
    rii = [{"n": a.strip(), "v": float(b), "lo": float(c), "hi": float(d), "s": float(e), "pc": float(f)} for a, b, c, d, e, f in
           re.findall(r"^\s*(\S[^:\n]*?)\s*:\s*(-?[\d.]+)\s+(-?[\d.]+)\s+to\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s*$", t, re.M)]
    rii_title = (re.search(r"\(SHIPS-RII PREDICTOR TABLE for (.*?)\)", t) or [None, None])[1]
    misc = {}
    for k, rx in {"ir_sd": r"STD DEV\.\s+50-200 KM RAD:\s*([\d.]+)", "ir_cold": r"T < -20 C\s+50-200 KM RAD:\s*([\d.]+)",
                  "prelim_ri": r"PRELIM RI PROB \(DV \.GE\. 35 KT IN 36 HR\):\s*([\d.]+)", "t12": r"T-12 MAX WIND:\s*(\d+)",
                  "ahi": r"AHI=\s*(\d+)", "steer_p": r"PRESSURE OF STEERING LEVEL \(MB\):\s*(\d+)"}.items():
        g = re.search(rx, t)
        if g: misc[k] = float(g.group(1))
    e = re.search(r"PROB\(%\)\s+(\d+)\s+(\d+)\(\s*(\d+)\)\s+(\d+)\(\s*(\d+)\)\s+(\d+)\(\s*(\d+)\)", t)
    if e: misc["erc"] = [int(e.group(i)) for i in (1, 3, 5, 7)]       # cumulative 0-12, 0-24, 0-36, 0-48 h
    trk = re.search(r"FORECAST TRACK FROM (\w+)", t)
    return {"init": init, "taus": taus, "rows": rows, "ctaus": ctaus, "contrib": contrib, "contrib_total": contrib_total,
            "ri": ri, "ri_cols": cols, "ri_matrix": mx, "rii": rii, "rii_title": rii_title, "misc": misc,
            "track": trk.group(1) if trk else None, "model": "GFS" if "GFS version" in t else None}


def ships(get, sid):
    try:
        fs = files(get, sid)
        if not fs: return None
        d = parse(get(BASE + fs[-1], 300).decode("utf8", "replace"))
        if not d: return None
        prior = []
        for f in reversed(fs[-4:-1]):
            try:
                p = parse(get(BASE + f, 6 * 3600).decode("utf8", "replace"))
                if p: prior.append({"init": p["init"], "taus": p["taus"], "v": p["rows"].get("v"), "vnl": p["rows"].get("vnl"), "lgem": p["rows"].get("lgem")})
            except Exception: pass
        d["prior"] = prior
        return d
    except Exception as ex:
        return {"err": str(ex)[:200]}
