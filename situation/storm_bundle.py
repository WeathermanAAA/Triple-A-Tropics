"""Situation Room data: one storm, everything about it, in one JSON (written to R2 as situation/<sid>.json by build_situation.py).

  nhc       advisory: intensity, motion, forecast points, cone (serve.nhc())
  text      the public advisory: headline, summary block, watches/warnings, hazards
  best      best track history (ATCF b-deck)
  models    guidance per aid, newest cycle each aid ran in (ATCF a-deck): early (interpolated) aids, late models, GEFS members
  mw        passive-microwave overpasses (TAT microwave pipeline; falls back to the precursor invest's slug)
  recon     current mission (if any is in the storm's basin) + this storm's tasked flights from today's TCPOD
  sat       GOES-19 TAT IR frame times (tiles served by /api/sat)
"""
import gzip, json, re, time, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

CDN = "https://cdn.triple-a-tropics.com"
UA = {"User-Agent": "triple-a-tropics situation"}
_C = {}

def get(url, ttl=120, binary=False):
    e = _C.get(url)
    if e and time.time() - e[0] < ttl: return e[1]
    b = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()
    _C[url] = (time.time(), b)
    if len(_C) > 300: _C.pop(next(iter(_C)))
    return b

def jget(url, ttl=120):
    return json.loads(get(url, ttl))

def iso(dtg):  # 2026100618 -> 2026-10-06T18:00Z
    return f"{dtg[:4]}-{dtg[4:6]}-{dtg[6:8]}T{dtg[8:10]}:00Z"

def ll(a, b):
    lat = int(a[:-1]) / 10 * (-1 if a[-1] == "S" else 1); lon = int(b[:-1]) / 10 * (-1 if b[-1] == "W" else 1)
    return round(lat, 1), round(lon, 1)

def deck(kind, sid):
    """ATCF deck rows: (dtg, tech, tau, lat, lon, kt, mb, type, extra)"""
    b, n, y = sid[:2], sid[2:4], sid[4:]
    url = (f"https://ftp.nhc.noaa.gov/atcf/aid_public/a{b}{n}{y}.dat.gz" if kind == "a"
           else f"https://ftp.nhc.noaa.gov/atcf/btk/b{b}{n}{y}.dat")
    raw = get(url, 600)
    if kind == "a": raw = gzip.decompress(raw)
    out = []
    for line in raw.decode("utf8", "replace").splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) < 9 or not f[6] or not f[7]: continue
        try:
            lat, lon = ll(f[6], f[7])
            out.append((f[2], f[4], int(f[5] or 0), lat, lon, int(f[8] or 0), int(f[9] or 0) if len(f) > 9 and f[9] else 0,
                        f[10] if len(f) > 10 else "", line))
        except ValueError: continue
    return out

def best(sid):
    pts, seen, precursor = [], set(), None
    for dtg, tech, tau, lat, lon, kt, mb, ty, line in deck("b", sid):
        m = re.search(r"TRANSITIONED, \w\w([A-Z0-9])(\d)(\d{4}) to", line)
        if m:   # 'alB22026' = invest 92
            precursor = f"{sid[:2]}9{m.group(2)}{m.group(3)}"
        if dtg in seen: continue
        seen.add(dtg); pts.append({"t": iso(dtg), "lat": lat, "lon": lon, "kt": kt, "mb": mb, "ty": ty})
    return pts, precursor

# aid groups, as the page draws them
EARLY = ["TVCN", "IVCN", "HCCA", "OFCL", "AVNI", "EMXI", "HFAI", "HFBI", "HMNI", "HWFI", "CMCI", "UKXI", "NVG2", "CTCI", "AEMI", "DSHP", "LGEM", "SHIP"]
LATE = ["AVNO", "ECMF", "EMX", "HFSA", "HFSB", "HMON", "HWRF", "CMC", "UKX", "NVGM", "CTCX", "AEMN"]

def models(sid):
    try: rows = deck("a", sid)
    except Exception: return {"cycle": None, "aids": {}, "gefs": {}}
    by = {}
    for dtg, tech, tau, lat, lon, kt, mb, ty, _ in rows:
        by.setdefault(tech, {}).setdefault(dtg, {})[tau] = (tau, lat, lon, kt)
    cyc = max((d for t in by.values() for d in t), default=None)
    def latest(tech, within=12):
        c = by.get(tech)
        if not c: return None
        d = max(c)
        if cyc and (datetime.strptime(cyc, "%Y%m%d%H") - datetime.strptime(d, "%Y%m%d%H")).total_seconds() > within * 3600: return None
        return {"cycle": iso(d), "pts": [list(v) for _, v in sorted(c[d].items())]}
    aids = {t: a for t in EARLY + LATE if (a := latest(t))}
    gefs = {t: a for t in sorted(by) if re.fullmatch(r"AP\d\d", t) and (a := latest(t))}
    return {"cycle": iso(cyc) if cyc else None, "aids": aids, "gefs": gefs}

def mw(slugs):
    for s in slugs:
        if not s: continue
        try: d = jget(f"{CDN}/microwave/{s}/overpasses.json", 300)
        except Exception: continue
        ov = []
        for o in d.get("overpasses", [])[-14:]:
            it = o.get("intensity") or {}
            ov.append({"id": o["id"], "t": o["valid_utc"], "sensor": o.get("sensor"), "platform": o.get("platform"),
                       "kt": it.get("vmax_kt") if it.get("usable") else None, "mb": it.get("mslp_hpa") if it.get("usable") else None,
                       "conf": it.get("confidence"), "why": (it.get("reasons") or [])[:1],
                       "img": o.get("products", {}), "geo": o.get("tiles", {}), "bounds": o.get("bounds_wgs84")})
        return {"slug": s, "name": d.get("name"), "overpasses": ov}
    return None

FL = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10}
def _when(s, y, mo):
    m = re.match(r"(\d\d)/(\d\d)(\d\d)Z", s.strip())
    return f"{y}-{mo:02d}-{m.group(1)}T{m.group(2)}:{m.group(3)}Z" if m else None

def tcpod(tokens):
    """the storm's tasked flights from today's Plan of the Day (two flights per row, side by side)"""
    try: d = jget(f"{CDN}/recon/tcpod.json", 600)
    except Exception: return None
    raw = d.get("raw", ""); vf = d.get("valid_from_utc") or datetime.utcnow().strftime("%Y-%m")
    y, mo = int(vf[:4]), int(vf[5:7])
    items, cur = [], None
    for line in raw.splitlines():
        h = re.match(r"\s{2,6}(\d)\.\s+(.*)", line)
        if h: cur = {"head": h.group(2).strip(), "lines": []}; items.append(cur); continue
        if re.match(r"\s{0,4}I+\.\s", line): cur = None; continue
        if cur is not None: cur["lines"].append(line)
    hit = lambda s: any(t in s.upper() for t in tokens)
    flights, outlook = [], []
    for it in items:
        if hit(it["head"]):
            col, blk = None, []
            for line in it["lines"] + [""]:
                fl = [m.start() for m in re.finditer(r"FLIGHT \w+ - ", line)]
                if fl:
                    for b in blk: flights.append(b)
                    col = fl[1] if len(fl) > 1 else None
                    names = [line[fl[0]:col].strip()] + ([line[col:].strip()] if col else [])
                    blk = [{"flight": n} for n in names]; continue
                m = re.match(r"\s+([A-I])\.\s", line)
                if m and blk:
                    parts = [line[:col], line[col:]] if col else [line]
                    for b, p in zip(blk, parts):
                        mm = re.match(r"\s*([A-I])\.\s+(.*)", p)
                        if mm: b[mm.group(1)] = mm.group(2).strip()
            for b in blk: flights.append(b)
        elif "OUTLOOK" in it["head"].upper() or "REMARK" in it["head"].upper():
            txt = re.sub(r"\s+", " ", " ".join([it["head"]] + it["lines"]))
            for sent in re.split(r"(?<=\.)\s+(?=[A-Z]\.\s)", txt):
                if hit(sent): outlook.append(sent.strip())
    out = []
    for b in flights:
        if "A" not in b: continue
        fixes = [_when(x if "/" in x else b["A"][:3] + x, y, mo) for x in b["A"].split(",")]
        pos = re.match(r"([\d.]+)N\s+([\d.]+)W", b.get("D", ""))
        win = re.match(r"(\S+) TO (\S+)", b.get("E", ""))
        out.append({"flight": b["flight"], "mission": b.get("B", ""), "fix": [f for f in fixes if f], "depart": _when(b.get("C", ""), y, mo),
                    "pos": [-float(pos.group(2)), float(pos.group(1))] if pos else None,
                    "window": [_when(win.group(1), y, mo), _when(win.group(2), y, mo)] if win else None,
                    "alt": b.get("F", ""), "task": b.get("G", "")})
    return {"number": d.get("tcpod_number"), "valid": [d.get("valid_from_utc"), d.get("valid_to_utc")], "flights": out, "outlook": outlook}

def recon(sid, name, basin, tokens):
    out = {"current": None, "missions": [], "plan": tcpod(tokens)}
    try:
        man = jget(f"{CDN}/recon/manifest.json", 120)
        for s in man.get("storms", []):
            if (s.get("atcf") or "").lower() == sid or s.get("slug") == sid: out["missions"].append(s)
    except Exception: pass
    try:
        c = jget(f"{CDN}/recon/current.json", 60)
        m = c.get("mission") or {}
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(m.get("valid_end", "2000-01-01T00:00:00Z").replace("Z", "+00:00"))).total_seconds() / 3600
        if c.get("has_active") and (m.get("basin") or "").lower() == basin and age < 12:
            t0, t1 = m.get("valid_start", ""), m.get("valid_end", "")
            out["current"] = {k: m.get(k) for k in ("mission_id", "aircraft", "flight", "storm_name", "valid_start", "valid_end", "n_obs",
                                                    "peak_sfmr_kt", "peak_fl_wind_kt", "min_p_sfc_hpa", "vdm_centers")}
            out["current"]["ours"] = (m.get("slug") or "") == sid
            out["current"]["track"] = [[p["lon"], p["lat"], p.get("wspd"), p.get("p_sfc"), p["t"], round((p.get("plane_z") or 0))] for p in m.get("track", [])]
            out["current"]["sondes"] = [{"t": s["t"], "lon": s["lon"], "lat": s["lat"], "kt": s.get("sfc_wind_kt"), "p": (s.get("levels") or [[None]])[0][0]}
                                        for s in m.get("sondes", []) if t0 <= s["t"] <= t1]
    except Exception: pass
    return out

def advisory_text(url):
    try: t = get(url, 300).decode("utf8", "replace")
    except Exception: return None
    m = re.search(r"<pre[^>]*>(.*?)</pre>", t, re.S)
    t = m.group(1) if m else t
    heads = re.findall(r"^\.\.\.(.+?)\.\.\.\s*$", t, re.M)
    summ = {}
    for k, v in re.findall(r"^(LOCATION|ABOUT \d+ MI|MAXIMUM SUSTAINED WINDS|PRESENT MOVEMENT|MINIMUM CENTRAL PRESSURE)\.{3}(.*)$", t, re.M):
        if k.startswith("ABOUT"): summ.setdefault("about", []).append((k + " " + v).replace("...", " "))
        else: summ[k.lower()] = v.replace("...", " ")
    def sec(h):
        m = re.search(h + r"\s*\n-+\n(.*?)(?:\n\s*\n[A-Z][A-Z \-]+\n-+|\n\$\$|\Z)", t, re.S)
        return re.sub(r"[ \t]+\n", "\n", m.group(1)).strip() if m else ""
    ww = sec("WATCHES AND WARNINGS"); hz = sec("HAZARDS AFFECTING LAND")
    hazards = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", hz) if re.match(r"\s*[A-Z ]+:", p)]
    nxt = re.search(r"Next (?:intermediate|complete) advisory at (.*?)\.", re.sub(r"\s+", " ", t))
    return {"headlines": heads[:3], "summary": summ, "watches": re.sub(r"\s+", " ", ww), "hazards": hazards,
            "next": nxt.group(0) if nxt else "", "issued": (re.search(r"^\d{3,4} [AP]M \w{3} \w{3} \w{3} \d\d \d{4}", t, re.M) or [None])[0]}

def sat():
    try:
        m = jget(f"{CDN}/shadow/sat/goes19/fd/ir/latest_times.json", 120)
        ts = sorted(m.get("times", [])); last = datetime.strptime(ts[-1], "%Y%m%dT%H%M%SZ") if ts else None
        ts = [t for t in ts if last and (last - datetime.strptime(t, "%Y%m%dT%H%M%SZ")).total_seconds() <= 4.5 * 3600]
        return {"times": ts, "latest": m.get("latest"), "maxzoom": m.get("maxzoom", 5), "bounds": m.get("bounds")}
    except Exception: return None

_IDX = {}
def sat_tile(t, z, x, y):
    base = f"{CDN}/shadow/sat/goes19/fd/ir/{t}"
    if base not in _IDX:
        try: _IDX[base] = jget(f"{base}/tiles.z5.json", 3600)
        except Exception: _IDX[base] = None
        if len(_IDX) > 200: _IDX.pop(next(iter(_IDX)))
    ix = _IDX[base]; key = f"{z}/{x}/{y}.webp"
    if ix and ix.get("tiles") is not None:
        m = ix["tiles"].get(key)
        if not m: return b""
        r = urllib.request.urlopen(urllib.request.Request(f"{base}/{m[0]}", headers={**UA, "Range": f"bytes={m[1]}-{m[1] + m[2] - 1}"}), timeout=30)
        b = r.read()
        if r.status == 200 and len(b) > m[2]: b = b[m[1]:m[1] + m[2]]
        return b
    try: return get(f"{base}/{key}", 86400)
    except urllib.error.HTTPError: return b""

def bundle(sid, nhc_list):
    sid = sid.lower()
    st = next((s for s in nhc_list if (s.get("id") or "").lower() == sid), None)
    if not st: return {"err": f"{sid} is not an active NHC storm", "active": [s.get("id") for s in nhc_list]}
    bt, pre = [], None
    try: bt, pre = best(sid)
    except Exception: pass
    name = (st.get("name") or "").upper()
    tokens = [name, sid[:2].upper() + sid[2:4]] + ([pre[:2].upper() + pre[2:4]] if pre else [])
    if st.get("classification") in ("TD", "PTC") and name: tokens.append(f"DEPRESSION {name}")
    txt = None
    try:
        cs = jget("https://www.nhc.noaa.gov/CurrentStorms.json", 120)
        raw = next((s for s in cs["activeStorms"] if s["id"] == sid), {})
        if raw.get("publicAdvisory", {}).get("url"): txt = advisory_text(raw["publicAdvisory"]["url"])
    except Exception: pass
    return {"sid": sid, "nhc": st, "text": txt, "best": bt, "precursor": pre, "models": models(sid),
            "mw": mw([sid, pre]), "recon": recon(sid, name, sid[:2], [t for t in tokens if t and t != "AL" and len(t) > 2]),
            "sat": sat(), "active": [{"id": s.get("id"), "name": s.get("name"), "cls": s.get("classification"), "kt": s.get("intensity")} for s in nhc_list],
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
