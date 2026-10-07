"""Situation Room visible imagery: GOES ABI band 2 (0.64 um, 0.5 km) around each storm with an open room, every 5 min.

The 5-minute CONUS/PACUS visible file is ~60 MB, far too big for a browser loop, so the live writer (live.py) runs this
in a background thread: it reads the newest band-2 file from NOAA's bucket, crops a storm-centred window, reprojects it
to Web Mercator and publishes a small grey PNG plus a rolling index:

  situation/vis/<sid>/<YYYYMMDDTHHMMZ>.jpg      one frame (black outside the sector)
  situation/vis/<sid>/index.json                {"frames": [{"t": iso, "img": name, "coords": [[w,n],[e,n],[e,s],[w,s]]}], ...}

The page draws these frames in its 5-MIN source exactly like the IR/WV loops it decodes itself.
"""
import io, json, math, os, re, subprocess, tempfile, time, urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np

BUCKET = {"goes19": "https://noaa-goes19.s3.amazonaws.com", "goes18": "https://noaa-goes18.s3.amazonaws.com"}
SECTORS = {"goes19": {"lon0": -75.0, "x": (-0.101332, 0.038612), "y": (0.044268, 0.128212), "name": "GOES-19 CONUS"},
           "goes18": {"lon0": -137.0, "x": (-0.069972, 0.069972), "y": (0.044268, 0.128212), "name": "GOES-18 PACUS"}}
REQ, RPOL, H = 6378137.0, 6356752.31414, 35786023.0 + 6378137.0
E2 = REQ * REQ / (RPOL * RPOL)
HALF_LON, HALF_LAT, OUT_W, KEEP_H = 9.0, 7.0, 1400, 3.25
UA = {"User-Agent": "triple-a-tropics situation"}


def to_xy(lon, lat, lon0):
    la = np.radians(lat); dl = np.radians(lon - lon0)
    pc = np.arctan(np.tan(la) / E2)
    rc = RPOL / np.sqrt(1 - (1 - 1 / E2) * np.cos(pc) ** 2)
    sx = H - rc * np.cos(pc) * np.cos(dl); sy = -rc * np.cos(pc) * np.sin(dl); sz = rc * np.sin(pc)
    vis = H * (H - sx) >= sy * sy + E2 * sz * sz
    x = np.arcsin(-sy / np.sqrt(sx * sx + sy * sy + sz * sz)); y = np.arctan(sz / sx)
    return np.where(vis, x, np.nan), np.where(vis, y, np.nan)


def sector_for(lon, lat):
    best = None
    for sat, S in SECTORS.items():
        x, y = to_xy(np.array([lon]), np.array([lat]), S["lon0"]); x, y = float(x[0]), float(y[0])
        if not (np.isfinite(x) and S["x"][0] + .006 < x < S["x"][1] - .006 and S["y"][0] + .006 < y < S["y"][1] - .006): continue
        d = abs(((lon - S["lon0"] + 540) % 360) - 180)
        if best is None or d < best[0]: best = (d, sat)
    return best[1] if best else None


def recent_keys(sat):
    """band-2 keys from the last KEEP_H hours, oldest first"""
    now = datetime.now(timezone.utc); keys = []
    for t in [now - timedelta(hours=h) for h in range(int(KEEP_H) + 1, -1, -1)]:
        pre = f"ABI-L2-CMIPC/{t:%Y}/{t.timetuple().tm_yday:03d}/{t:%H}/OR_ABI-L2-CMIPC-M6C02_"
        x = urllib.request.urlopen(urllib.request.Request(f"{BUCKET[sat]}/?list-type=2&prefix={pre}", headers=UA), timeout=30).read().decode()
        keys += re.findall(r"<Key>([^<]+)</Key>", x)
    cut = now - timedelta(hours=KEEP_H)
    return sorted(k for k in set(keys) if key_time(k) >= cut)


def key_time(k):
    m = re.search(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})", k)
    return datetime(int(m.group(1)), 1, 1, tzinfo=timezone.utc) + timedelta(days=int(m.group(2)) - 1, hours=int(m.group(3)), minutes=int(m.group(4)), seconds=int(m.group(5)))


def render(path, clon, clat):
    import h5py
    from PIL import Image
    f = h5py.File(path, "r"); c = f["CMI"]; xv = f["x"]; yv = f["y"]
    at = lambda d, k: float(np.ravel(d.attrs[k])[0])
    x0 = xv[0] * at(xv, "scale_factor") + at(xv, "add_offset"); dx = at(xv, "scale_factor") * float(xv[1] - xv[0])
    y0 = yv[0] * at(yv, "scale_factor") + at(yv, "add_offset"); dy = at(yv, "scale_factor") * float(yv[1] - yv[0])
    lon0 = at(f["goes_imager_projection"], "longitude_of_projection_origin"); sf, ao, fill = at(c, "scale_factor"), at(c, "add_offset"), at(c, "_FillValue")
    w, e, s, n = clon - HALF_LON, clon + HALF_LON, clat - HALF_LAT, clat + HALF_LAT
    my = lambda la: math.log(math.tan(math.pi / 4 + math.radians(la) / 2))
    Hh = int(round(OUT_W * (my(n) - my(s)) / math.radians(e - w)))
    lons = w + (np.arange(OUT_W) + .5) / OUT_W * (e - w)
    lats = np.degrees(2 * np.arctan(np.exp(my(n) - (np.arange(Hh) + .5) / Hh * (my(n) - my(s)))) - math.pi / 2)
    LO, LA = np.meshgrid(lons, lats)
    X, Y = to_xy(LO, LA, lon0)
    I = np.round((X - x0) / dx); J = np.round((Y - y0) / dy)
    ok = np.isfinite(I) & np.isfinite(J)
    I = np.where(ok, I, 0).astype(int); J = np.where(ok, J, 0).astype(int)
    ok &= (I >= 0) & (I < c.shape[1]) & (J >= 0) & (J < c.shape[0])
    if not ok.any(): return None
    j0, j1, i0, i1 = J[ok].min(), J[ok].max() + 1, I[ok].min(), I[ok].max() + 1
    sub = c[j0:j1, i0:i1]                                       # read only the window (row-chunked file, ~0.3 s)
    raw = sub[np.clip(J - j0, 0, j1 - j0 - 1), np.clip(I - i0, 0, i1 - i0 - 1)]
    ok &= raw != fill
    refl = np.clip(raw * sf + ao, 0, 1.2)
    g = np.clip(np.sqrt(refl / 1.0) * 255, 0, 255).astype(np.uint8)   # square-root stretch, as the meso VIS loop does
    g = np.where(ok, g, 0).astype(np.uint8)
    b = io.BytesIO(); Image.fromarray(g, "L").save(b, "JPEG", quality=84, optimize=True)   # grey JPEG: ~5x smaller than PNG for cloud texture
    return b.getvalue(), [[w, n], [e, n], [e, s], [w, s]]


class VisWriter:
    """called every ~60 s from the live loop's background thread"""
    def __init__(self, put, out_dir, dry=False):
        self.put, self.dir, self.dry, self.idx, self.last = put, os.path.join(out_dir, "situation", "vis"), dry, {}, {}

    def tick(self, storms):
        for st in storms:
            sid = st["sid"]; lon, lat = st["lon"], st["lat"]
            sat = sector_for(lon, lat)
            if not sat: continue
            try:
                idx = self.idx.get(sid) or self._prior(sid); self.idx[sid] = idx
                have = {f["img"] for f in idx["frames"]}
                keys = recent_keys(sat)
                # newest first, then fill the window back at 10-min spacing (a restart or a new room starts with a full loop)
                todo = [k for k in reversed(keys) if f"{key_time(k):%Y%m%dT%H%MZ}.jpg" not in have and (k == keys[-1] or key_time(k).minute % 10 < 5)][:3]
                for k in todo: self._one(sid, sat, k, lon, lat)
            except Exception as ex:
                print(f"vis: {sid} failed: {ex}", flush=True)

    def _one(self, sid, sat, k, lon, lat):
        tmp = tempfile.NamedTemporaryFile(suffix=".nc", delete=False); tmp.close()
        with urllib.request.urlopen(urllib.request.Request(f"{BUCKET[sat]}/{k}", headers=UA), timeout=120) as r, open(tmp.name, "wb") as o:
            while True:
                b = r.read(1 << 20)
                if not b: break
                o.write(b)
        try: res = render(tmp.name, round(lon), round(lat))     # centre snapped to 1 degree: frames line up while the storm drifts
        finally: os.unlink(tmp.name)
        if not res: return
        png, coords = res; t = key_time(k); name = f"{t:%Y%m%dT%H%MZ}.jpg"
        d = os.path.join(self.dir, sid); os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name); open(p, "wb").write(png)
        if not self.put(p, f"vis/{sid}/{name}", "public, max-age=86400", "image/jpeg"): return
        idx = self.idx[sid]
        idx["frames"] = [f for f in idx["frames"] if f["img"] != name] + [{"t": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "img": name, "coords": coords}]
        cut = (t - timedelta(hours=KEEP_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
        idx["frames"] = sorted([f for f in idx["frames"] if f["t"] >= cut], key=lambda f: f["t"])
        idx.update(sat=SECTORS[sat]["name"], band="C02", updated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.idx[sid] = idx
        ip = os.path.join(d, "index.json"); json.dump(idx, open(ip, "w"), separators=(",", ":"))
        self.put(ip, f"vis/{sid}/index.json", "no-store, max-age=0", "application/json")
        print(f"vis: {sid} {name} ({len(png) // 1024} KB, {len(idx['frames'])} frames)", flush=True)

    def _prior(self, sid):
        """resume the rolling window from the published index after a restart"""
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(f"https://cdn.triple-a-tropics.com/situation/vis/{sid}/index.json?t={int(time.time())}",
                                                                          headers={**UA, "Origin": "https://triple-a-tropics.com"}), timeout=20).read())
        except Exception:
            return {"frames": []}
