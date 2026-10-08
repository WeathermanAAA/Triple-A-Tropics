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


def recent_keys(sat, prod="CMIPC", ch="C02", keep=KEEP_H):
    """keys of one ABI product/channel from the last `keep` hours, oldest first"""
    now = datetime.now(timezone.utc); keys = []
    for t in [now - timedelta(hours=h) for h in range(int(keep) + 1, -1, -1)]:
        pre = f"ABI-L2-{prod}/{t:%Y}/{t.timetuple().tm_yday:03d}/{t:%H}/OR_ABI-L2-{prod}-M6{ch}_"
        x = urllib.request.urlopen(urllib.request.Request(f"{BUCKET[sat]}/?list-type=2&prefix={pre}", headers=UA), timeout=30).read().decode()
        keys += re.findall(r"<Key>([^<]+)</Key>", x)
    cut = now - timedelta(hours=keep)
    return sorted(k for k in set(keys) if key_time(k) >= cut)


def key_time(k):
    m = re.search(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})", k)
    return datetime(int(m.group(1)), 1, 1, tzinfo=timezone.utc) + timedelta(days=int(m.group(2)) - 1, hours=int(m.group(3)), minutes=int(m.group(4)), seconds=int(m.group(5)))


def sample(path, clon, clat, hlon=HALF_LON, hlat=HALF_LAT, ow=OUT_W):
    """physical values (reflectance or K) on a Web Mercator grid around (clon, clat), plus the valid mask and lon/lat box"""
    import h5py
    f = h5py.File(path, "r"); c = f["CMI"]; xv = f["x"]; yv = f["y"]
    at = lambda d, k: float(np.ravel(d.attrs[k])[0])
    x0 = xv[0] * at(xv, "scale_factor") + at(xv, "add_offset"); dx = at(xv, "scale_factor") * float(xv[1] - xv[0])
    y0 = yv[0] * at(yv, "scale_factor") + at(yv, "add_offset"); dy = at(yv, "scale_factor") * float(yv[1] - yv[0])
    lon0 = at(f["goes_imager_projection"], "longitude_of_projection_origin"); sf, ao, fill = at(c, "scale_factor"), at(c, "add_offset"), at(c, "_FillValue")
    w, e, s, n = clon - hlon, clon + hlon, clat - hlat, clat + hlat
    my = lambda la: math.log(math.tan(math.pi / 4 + math.radians(la) / 2))
    Hh = int(round(ow * (my(n) - my(s)) / math.radians(e - w)))
    lons = w + (np.arange(ow) + .5) / ow * (e - w)
    lats = np.degrees(2 * np.arctan(np.exp(my(n) - (np.arange(Hh) + .5) / Hh * (my(n) - my(s)))) - math.pi / 2)
    LO, LA = np.meshgrid(lons, lats)
    X, Y = to_xy(LO, LA, lon0)
    I = np.round((X - x0) / dx); J = np.round((Y - y0) / dy)
    ok = np.isfinite(I) & np.isfinite(J)
    I = np.where(ok, I, 0).astype(int); J = np.where(ok, J, 0).astype(int)
    ok &= (I >= 0) & (I < c.shape[1]) & (J >= 0) & (J < c.shape[0])
    if not ok.any(): return None
    j0, j1, i0, i1 = J[ok].min(), J[ok].max() + 1, I[ok].min(), I[ok].max() + 1
    sub = c[j0:j1, i0:i1]                                       # read only the window
    raw = sub[np.clip(J - j0, 0, j1 - j0 - 1), np.clip(I - i0, 0, i1 - i0 - 1)]
    ok &= raw != fill
    return raw * sf + ao, ok, [[w, n], [e, n], [e, s], [w, s]]


def render(path, clon, clat):
    """visible: grey JPEG, square-root stretch as the meso VIS loop does"""
    from PIL import Image
    r = sample(path, clon, clat)
    if not r: return None
    v, ok, coords = r
    g = np.where(ok, np.clip(np.sqrt(np.clip(v, 0, 1.2) / 1.0) * 255, 0, 255), 0).astype(np.uint8)
    b = io.BytesIO(); Image.fromarray(g, "L").save(b, "JPEG", quality=84, optimize=True)   # grey JPEG: ~5x smaller than PNG for cloud texture
    return b.getvalue(), coords


# Brightness temperature frames for storms outside the 5-minute sectors (full disk, every 10 min). Lossless, so the
# page colours them exactly: pixel v (1..251) = T of -100 + (v - 1) * 0.6 degC; 0 = no data.
BT_HALF_LON, BT_HALF_LAT, BT_W, BT_KEEP_H = 13.0, 9.5, 1300, 6.25
FD_LON0 = {"goes19": -75.2, "goes18": -137.0}


def render_bt(path, clon, clat):
    from PIL import Image
    r = sample(path, clon, clat, BT_HALF_LON, BT_HALF_LAT, BT_W)
    if not r: return None
    v, ok, coords = r
    q = np.clip(np.round((v - 273.15 + 100) / .6) + 1, 1, 251)
    g = np.where(ok, q, 0).astype(np.uint8)
    b = io.BytesIO(); Image.fromarray(g, "L").save(b, "WEBP", lossless=True, quality=100, method=4)
    return b.getvalue(), coords


def fd_sat(lon, lat):
    """the GOES full disk that sees a point best (nearest sub-satellite longitude, well inside the disk)"""
    best = None
    for sat, l0 in FD_LON0.items():
        d = abs(((lon - l0 + 540) % 360) - 180)
        if d < 62 and abs(lat) < 58 and (best is None or d < best[0]): best = (d, sat)
    return best[1] if best else None


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


class FDWriter:
    """IR and water-vapour brightness temperature around each open-room storm that no 5-minute sector covers,
    from the GOES full disk (every 10 min): situation/fd/<sid>/<band>/<YYYYMMDDTHHMMZ>.webp + index.json"""
    BANDS = {"ir": "C13", "wv": "C08"}

    def __init__(self, put, out_dir, dry=False):
        self.put, self.dir, self.dry, self.idx = put, os.path.join(out_dir, "situation", "fd"), dry, {}

    def tick(self, storms):
        for st in storms:
            sid = st["sid"]; lon, lat = st["lon"], st["lat"]
            if sector_for(lon, lat): continue                      # the page reads the 5-minute sector itself
            sat = fd_sat(lon, lat)
            if not sat: continue
            for band, ch in self.BANDS.items():
                try:
                    k = (sid, band); idx = self.idx.get(k) or self._prior(sid, band); self.idx[k] = idx
                    have = {f["img"] for f in idx["frames"]}
                    keys = recent_keys(sat, "CMIPF", ch, BT_KEEP_H)
                    todo = [x for x in reversed(keys) if f"{key_time(x):%Y%m%dT%H%MZ}.webp" not in have][:3]
                    for x in todo: self._one(sid, band, sat, x, lon, lat)
                except Exception as ex:
                    print(f"fd: {sid} {band} failed: {ex}", flush=True)

    def _one(self, sid, band, sat, k, lon, lat):
        tmp = tempfile.NamedTemporaryFile(suffix=".nc", delete=False); tmp.close()
        with urllib.request.urlopen(urllib.request.Request(f"{BUCKET[sat]}/{k}", headers=UA), timeout=180) as r, open(tmp.name, "wb") as o:
            while True:
                b = r.read(1 << 20)
                if not b: break
                o.write(b)
        try: res = render_bt(tmp.name, round(lon), round(lat))
        finally: os.unlink(tmp.name)
        if not res: return
        img, coords = res; t = key_time(k); name = f"{t:%Y%m%dT%H%MZ}.webp"
        d = os.path.join(self.dir, sid, band); os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name); open(p, "wb").write(img)
        if not self.put(p, f"fd/{sid}/{band}/{name}", "public, max-age=86400", "image/webp"): return
        idx = self.idx[(sid, band)]
        idx["frames"] = [f for f in idx["frames"] if f["img"] != name] + [{"t": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "img": name, "coords": coords}]
        cut = (datetime.now(timezone.utc) - timedelta(hours=BT_KEEP_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
        idx["frames"] = sorted([f for f in idx["frames"] if f["t"] >= cut], key=lambda f: f["t"])
        idx.update(sat=sat, label=("GOES-19" if sat == "goes19" else "GOES-18") + " Full Disk", band=self.BANDS[band],
                   enc={"t0": -100, "step": .6, "nodata": 0}, updated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        ip = os.path.join(d, "index.json"); json.dump(idx, open(ip, "w"), separators=(",", ":"))
        self.put(ip, f"fd/{sid}/{band}/index.json", "no-store, max-age=0", "application/json")
        print(f"fd: {sid} {band} {name} ({len(img) // 1024} KB, {len(idx['frames'])} frames)", flush=True)

    def _prior(self, sid, band):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(f"https://cdn.triple-a-tropics.com/situation/fd/{sid}/{band}/index.json?t={int(time.time())}",
                                                                          headers={**UA, "Origin": "https://triple-a-tropics.com"}), timeout=20).read())
        except Exception:
            return {"frames": []}
