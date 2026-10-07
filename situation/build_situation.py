"""Situation Room feed: one JSON per active NHC storm + an index, for the /situation/ page.

  python situation/build_situation.py --out-dir situation_out
  -> situation_out/situation/index.json         active storms + which rooms are open (situation/rooms.json)
  -> situation_out/situation/<sid>.json         the storm bundle (storm_bundle.bundle)
  -> situation_out/situation/rooms.json         copy of the repo's rooms file (the CycloLab router reads it)

Isolated: reads NHC + the site's own CDN products, writes only under situation/. Touches nothing in track/ACE/climo.
"""
import argparse, io, json, os, re, sys, time, zipfile, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import storm_bundle as SB

def _get(url, ttl=20):
    """through storm_bundle's cache, so the live loop re-reads NHC every ~20 s without re-pulling unchanged KMZs too often"""
    return SB.get(url, ttl)

def _kml(url):
    z = zipfile.ZipFile(io.BytesIO(_get(url, 60))); return z.read([n for n in z.namelist() if n.endswith(".kml")][0]).decode("utf8", "replace")

def nhc():
    """active NHC storms with forecast points + cone, from CurrentStorms.json and the advisory KMZs"""
    out = []
    for s in json.loads(_get("https://www.nhc.noaa.gov/CurrentStorms.json", 15))["activeStorms"]:
        st = {k: s.get(k) for k in ("id", "name", "classification", "intensity", "pressure", "latitudeNumeric", "longitudeNumeric",
                                    "movementDir", "movementSpeed", "lastUpdate", "binNumber")}
        try:
            t = _kml(s["forecastTrack"]["kmzFile"]); pts = []
            for pm in re.findall(r"<Placemark>(.*?)</Placemark>", t, re.S):
                co = re.search(r"<coordinates>\s*([-\d.]+),([-\d.]+)", pm); d = re.search(r"<description>(.*?)</description>", pm, re.S)
                if not co or not d or "Forecast Track" in d.group(1): continue
                d = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", d.group(1)))
                hr = re.search(r"(\d+) hr Forecast", d); va = re.search(r"Valid at: (.*?) Location", d); kt = re.search(r"Maximum Wind: (\d+) knots", d)
                sty = re.search(r"<styleUrl>#?(\w+)", pm)
                pts.append({"lon": float(co.group(1)), "lat": float(co.group(2)), "hr": int(hr.group(1)) if hr else 0,
                            "valid": va.group(1).strip() if va else "", "kt": int(kt.group(1)) if kt else None, "style": sty.group(1) if sty else ""})
            c = _kml(s["trackCone"]["kmzFile"])
            rings = [[[float(a.split(",")[0]), float(a.split(",")[1])] for a in r.split()] for r in re.findall(r"<coordinates>(.*?)</coordinates>", c, re.S)]
            adv = re.search(r"_(\d+)\w*adv", s["trackCone"]["kmzFile"])
            st.update(points=pts, cone=rings, advisory=adv.group(1).lstrip("0") if adv else "")
        except Exception as e:
            st.update(points=[], cone=[], err=str(e))
        st.update(radii=radii(s), ww=watches(s))
        out.append(st)
    return out

def _rings(pm):
    return [[[round(float(a.split(",")[0]), 3), round(float(a.split(",")[1]), 3)] for a in r.split()] for r in re.findall(r"<coordinates>(.*?)</coordinates>", pm, re.S)]

def radii(s):
    """wind field: the current 34/50/64-kt radii, and the forecast radii (NHC gives these without times: an envelope)"""
    out = {"initial": [], "forecast": []}
    for key, part in (("initialWindExtent", "initial"), ("forecastWindRadiiGIS", "forecast")):
        try:
            t = _kml((s.get(key) or {})["kmzFile"])
            for pm in re.findall(r"<Placemark>(.*?)</Placemark>", t, re.S):
                n = re.search(r"<name>\s*(\d+)\s*</name>", pm)
                if n: out[part].append({"kt": int(n.group(1)), "rings": _rings(pm)})
        except Exception: pass
    return out

def watches(s):
    """coastal watches and warnings (NHC's watch/warning KMZ, present only while any are in effect)"""
    out = []
    for key in ("windWatchesWarnings", "watchesWarnings"):
        k = (s.get(key) or {}).get("kmzFile") if isinstance(s.get(key), dict) else None
        if not k: continue
        try:
            t = _kml(k)
            for pm in re.findall(r"<Placemark>(.*?)</Placemark>", t, re.S):
                txt = re.sub(r"<[^>]+>", " ", " ".join(re.findall(r"<(?:name|styleUrl|description)>(.*?)</(?:name|styleUrl|description)>", pm, re.S)))
                kind = next((w for w in ("Hurricane Warning", "Hurricane Watch", "Tropical Storm Warning", "Tropical Storm Watch") if w.lower() in txt.lower()), None)
                if not kind:
                    u = txt.upper(); kind = "Hurricane Warning" if "HWR" in u else "Hurricane Watch" if "HWA" in u else "Tropical Storm Warning" if "TWR" in u else "Tropical Storm Watch" if "TWA" in u else None
                if kind: out.append({"type": kind, "lines": _rings(pm)})
        except Exception: pass
    return out

# ---------------- geostationary imagery (NASA GIBS, 10-min, every basin): which frames exist in the last 12 h ----------------
GIBS_LAYERS = [f"{sat}_{b}" for sat in ("GOES-East_ABI", "GOES-West_ABI") for b in
               ("Band13_Clean_Infrared", "Band2_Red_Visible_1km", "GeoColor", "Air_Mass", "Dust")] + \
              [f"Himawari_AHI_{b}" for b in ("Band13_Clean_Infrared", "Band3_Red_Visible_1km", "Air_Mass")]

def _iso(t): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
def _parse(s):
    import calendar
    return calendar.timegm(time.strptime(s.strip(), "%Y-%m-%dT%H:%M:%SZ"))

def gibs_times(hours=12):
    """{layer: [ISO times available in the last `hours`]} from the GIBS WMS capabilities time dimensions"""
    try: x = _get("https://gibs.earthdata.nasa.gov/wms/epsg3857/best/wms.cgi?SERVICE=WMS&REQUEST=GetCapabilities&VERSION=1.3.0", 240).decode("utf8", "replace")
    except Exception as e:
        print("gibs capabilities failed:", e); return {}
    lo, out = time.time() - hours * 3600, {}
    for lay in GIBS_LAYERS:
        m = re.search(r"<Name>" + re.escape(lay) + r"</Name>.*?<Dimension name=\"time\"[^>]*>([^<]*)</Dimension>", x, re.S)
        if not m: continue
        ts = []
        for part in m.group(1).split(","):
            p = part.strip().split("/")
            try:
                if len(p) == 3:
                    a, b = _parse(p[0]), _parse(p[1]); step = 600
                    t = max(a, b - ((b - lo) // step + 1) * step if b > lo else b + 1)
                    while t <= b:
                        if t >= lo: ts.append(t)
                        t += step
                elif p[0]:
                    t = _parse(p[0])
                    if t >= lo: ts.append(t)
            except Exception: continue
        if ts: out[lay] = [_iso(t) for t in sorted(set(ts))]
    return out

def load_rooms(live=False):
    """situation/rooms.json; the live loop re-reads the committed copy from GitHub so opening a room needs no restart"""
    try:
        r = json.loads(_get("https://raw.githubusercontent.com/WeathermanAAA/Triple-A-Tropics/main/situation/rooms.json", 120)) if live \
            else json.load(open(os.path.join(HERE, "rooms.json")))
    except Exception:
        try: r = json.load(open(os.path.join(HERE, "rooms.json")))
        except Exception: r = {}
    return {k.lower(): v for k, v in r.items() if not k.startswith("_") and v}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out-dir", default="situation_out"); a = ap.parse_args()
    build(a.out_dir, load_rooms())

def build(out_dir, rooms):
    od = os.path.join(out_dir, "situation"); os.makedirs(od, exist_ok=True)
    storms = nhc(); made = []
    for s in storms:
        sid = (s.get("id") or "").lower()
        try:
            b = SB.bundle(sid, storms); b["room_open"] = sid in rooms
            json.dump(b, open(os.path.join(od, f"{sid}.json"), "w"), separators=(",", ":")); made.append(sid)
            print(f"situation: {sid} {s.get('name')} ok", flush=True)
        except Exception as e:
            print(f"situation: {sid} failed: {e}", flush=True)
    idx = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rooms": sorted(rooms), "gibs": gibs_times(),
           "active": [{"id": (s.get("id") or "").lower(), "name": s.get("name"), "cls": s.get("classification"), "kt": s.get("intensity"),
                       "has_bundle": (s.get("id") or "").lower() in made} for s in storms]}
    json.dump(idx, open(os.path.join(od, "index.json"), "w"), separators=(",", ":"))
    json.dump({k: True for k in rooms}, open(os.path.join(od, "rooms.json"), "w"))
    print(f"situation: {len(made)} bundle(s), {len(rooms)} open room(s)", flush=True)
    return made

if __name__ == "__main__":
    main()
