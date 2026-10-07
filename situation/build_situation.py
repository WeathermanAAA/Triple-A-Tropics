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

def _get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=SB.UA), timeout=30).read()

def _kml(url):
    z = zipfile.ZipFile(io.BytesIO(_get(url))); return z.read([n for n in z.namelist() if n.endswith(".kml")][0]).decode("utf8", "replace")

def nhc():
    """active NHC storms with forecast points + cone, from CurrentStorms.json and the advisory KMZs"""
    out = []
    for s in json.loads(_get("https://www.nhc.noaa.gov/CurrentStorms.json"))["activeStorms"]:
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
        out.append(st)
    return out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out-dir", default="situation_out"); a = ap.parse_args()
    od = os.path.join(a.out_dir, "situation"); os.makedirs(od, exist_ok=True)
    try: rooms = json.load(open(os.path.join(HERE, "rooms.json")))
    except Exception: rooms = {}
    rooms = {k.lower(): v for k, v in rooms.items() if not k.startswith("_") and v}
    storms = nhc(); made = []
    for s in storms:
        sid = (s.get("id") or "").lower()
        try:
            b = SB.bundle(sid, storms); b["room_open"] = sid in rooms
            json.dump(b, open(os.path.join(od, f"{sid}.json"), "w"), separators=(",", ":")); made.append(sid)
            print(f"situation: {sid} {s.get('name')} ok", flush=True)
        except Exception as e:
            print(f"situation: {sid} failed: {e}", flush=True)
    idx = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rooms": sorted(rooms),
           "active": [{"id": (s.get("id") or "").lower(), "name": s.get("name"), "cls": s.get("classification"), "kt": s.get("intensity"),
                       "has_bundle": (s.get("id") or "").lower() in made} for s in storms]}
    json.dump(idx, open(os.path.join(od, "index.json"), "w"), separators=(",", ":"))
    json.dump({k: True for k in rooms}, open(os.path.join(od, "rooms.json"), "w"))
    print(f"situation: {len(made)} bundle(s), {len(rooms)} open room(s)")

if __name__ == "__main__":
    main()
