"""Situation Room map tiles: the Tulsa-Live basemap, rendered anywhere, at any zoom (baked to R2 by bake_tiles.py).

Every tile is made the way tools/build-basemap.py makes Tulsa-Live's map:
  terrain   AWS Terrain Tiles (Terrarium DEM) -> the same hillshade (multi-directional light, same z-factor and light
            direction build-basemap.py chose for Oklahoma) -> the same colours: the per-channel quantile maps learned from
            img/basemap.jpg (data/basemap-lut.json, made by tools/build-place.py's learn())
  water     lakes in basemap.jpg's own lake colour, rivers in its river colour, the ocean a touch deeper
  overlay   county lines in overlay.png's ink, state lines, and the highways as roads.svg draws them (interstates bright
            white, US highways faint)
Water, rivers, roads and state lines come from OpenMapTiles vector tiles (OpenFreeMap), so they are right at every zoom;
counties from the same US Census file build-basemap.py uses. Tiles are 512 px (crisp at tileSize 256) and cached on disk.
"""
import io, os, gzip, json, math, struct, threading, urllib.request
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get("SITUATION_TILE_CACHE", os.path.join(HERE, ".cache"))
SRC = os.path.join(CACHE, "src"); TILES = os.path.join(CACHE, "tiles")
os.makedirs(SRC, exist_ok=True); os.makedirs(TILES, exist_ok=True)
T = 512                                   # output tile size (px)
LUT = json.load(open(os.path.join(HERE, "basemap-lut.json")))
SRC_IN = np.array(LUT["luts"]["in"]["src"]); DST_IN = [np.array(d) for d in LUT["luts"]["in"]["dst"]]
LAKE = np.array(LUT["lake"], np.float32); OCEAN = LAKE * 0.82; RIVER = np.array([40, 72, 150], np.float32)
ZF, FLIP = LUT["zf"], LUT["flip"]
UA = {"User-Agent": "triple-a-tropics situation"}
_locks = {}; _glock = threading.Lock()

def _lock(key):
    with _glock:
        return _locks.setdefault(key, threading.Lock())

def fetch(url, name, ttl=None):
    """download once into cache/src (ttl seconds: refetch when older)"""
    f = os.path.join(SRC, name)
    with _lock(f):
        if os.path.exists(f) and (ttl is None or os.path.getmtime(f) > __import__("time").time() - ttl):
            return open(f, "rb").read()
        err = None
        for _ in range(3):
            try:
                d = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read(); break
            except Exception as e: err = e
        else:
            if os.path.exists(f): return open(f, "rb").read()
            raise err
        os.makedirs(os.path.dirname(f), exist_ok=True); open(f + ".tmp", "wb").write(d); os.replace(f + ".tmp", f)
        return d

# ---------------- terrain ----------------
def dem_px(z, x, y):
    """elevation (m) for tile z/x/y at T+2 px (1 px border, so the hillshade has no seams)"""
    dz = min(z, 12); sh = z - dz; n = 2 ** dz
    # the source tile(s) covering z/x/y, with one neighbour on every side
    sx, sy = x >> sh, y >> sh
    big = np.zeros((768, 768), np.float32)
    for j in (-1, 0, 1):
        for i in (-1, 0, 1):
            tx, ty = (sx + i) % n, min(max(sy + j, 0), n - 1)
            im = np.asarray(Image.open(io.BytesIO(fetch(f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{dz}/{tx}/{ty}.png", f"dem/{dz}/{tx}/{ty}.png"))).convert("RGB"), np.float32)
            big[(j + 1) * 256:(j + 2) * 256, (i + 1) * 256:(i + 2) * 256] = im[..., 0] * 256 + im[..., 1] + im[..., 2] / 256 - 32768
    k = 2 ** sh; sub = 256 / k                              # our tile inside the 3x3 block, in source px
    ox = 256 + (x - sx * k) * sub; oy = 256 + (y - sy * k) * sub; px = sub / T
    box = (ox - px, oy - px, ox + sub + px, oy + sub + px)
    return np.asarray(Image.fromarray(big, "F").resize((T + 2, T + 2), Image.BILINEAR, box=box), np.float32)

def hillshade(e, z, y):
    """build-basemap.py's hillshade, with metres per pixel for this zoom"""
    n = 2 ** z; rows = (y + (np.arange(T + 2) - 1 + .5) / T) / n
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * rows))))
    mpp = 2 * math.pi * 6378137 / (n * T) * np.cos(np.radians(lat))[:, None]
    dy, dx = np.gradient(e); dx = dx / mpp * ZF; dy = dy / mpp * ZF
    slope = np.arctan(np.hypot(dx, dy)); aspect = np.arctan2(-dx, dy); out = 0
    for az, wgt in ((315, .6), (270, .2), (360, .2)):
        a = math.radians(360 - (az + (180 if FLIP else 0)) + 90); alt = math.radians(42)
        out = out + wgt * (math.sin(alt) * np.cos(slope) + math.cos(alt) * np.sin(slope) * np.cos(a - aspect))
    return np.clip(out, 0, 1)[1:-1, 1:-1]

# ---------------- vector tiles (OpenMapTiles, minimal MVT decoder) ----------------
_TILEURL = None
def tileurl():
    global _TILEURL
    if not _TILEURL:
        _TILEURL = json.loads(fetch("https://tiles.openfreemap.org/planet", "openfreemap.json", ttl=86400))["tiles"][0]
    return _TILEURL

def _varint(b, i):
    r = s = 0
    while True:
        c = b[i]; i += 1; r |= (c & 0x7f) << s; s += 7
        if c < 0x80: return r, i

def _fields(b):
    i = 0; n = len(b)
    while i < n:
        k, i = _varint(b, i); f, t = k >> 3, k & 7
        if t == 0: v, i = _varint(b, i)
        elif t == 2: L, i = _varint(b, i); v = b[i:i + L]; i += L
        elif t == 1: v = b[i:i + 8]; i += 8
        elif t == 5: v = b[i:i + 4]; i += 4
        else: raise ValueError("wire type")
        yield f, t, v

def _packed(b):
    out = []; i = 0
    while i < len(b): v, i = _varint(b, i); out.append(v)
    return out

def _value(b):
    for f, t, v in _fields(b):
        if f == 1: return v.decode("utf8", "replace")
        if f == 2: return struct.unpack("<f", v)[0]
        if f == 3: return struct.unpack("<d", v)[0]
        if f in (4, 5): return v
        if f == 6: return (v >> 1) ^ -(v & 1)
        if f == 7: return bool(v)

def mvt(z, x, y, want):
    """{layer: [(props, type, rings)]} for the wanted layers; rings in 0..1 tile units of z/x/y"""
    vz = min(z, 14); sh = z - vz; vx, vy = x >> sh, y >> sh
    raw = fetch(tileurl().format(z=vz, x=vx, y=vy), f"mvt/{vz}/{vx}/{vy}.pbf", ttl=30 * 86400)
    if raw[:2] == b"\x1f\x8b": raw = gzip.decompress(raw)
    k = 2 ** sh; fx, fy = x - vx * k, y - vy * k            # where our tile sits inside the vector tile
    out = {}
    for f, t, lb in _fields(raw):
        if f != 3: continue
        name = None; feats = []; keys = []; vals = []; ext = 4096
        for g, tt, v in _fields(lb):
            if g == 1: name = v.decode()
            elif g == 2: feats.append(v)
            elif g == 3: keys.append(v.decode())
            elif g == 4: vals.append(v)
            elif g == 5: ext = v
        if name not in want: continue
        vals = [_value(v) for v in vals]; L = out.setdefault(name, [])
        for fb in feats:
            tags = []; gt = 0; geom = []
            for g, tt, v in _fields(fb):
                if g == 2: tags = _packed(v)
                elif g == 3: gt = v
                elif g == 4: geom = _packed(v)
            props = {keys[tags[i]]: vals[tags[i + 1]] for i in range(0, len(tags) - 1, 2)}
            rings = []; cur = []; cx = cy = 0; i = 0
            while i < len(geom):
                cmd, cnt = geom[i] & 7, geom[i] >> 3; i += 1
                if cmd == 7:
                    if cur: cur.append(cur[0])
                    continue
                for _ in range(cnt):
                    dx, dy = geom[i], geom[i + 1]; i += 2
                    cx += (dx >> 1) ^ -(dx & 1); cy += (dy >> 1) ^ -(dy & 1)
                    if cmd == 1:
                        if cur: rings.append(cur)
                        cur = []
                    cur.append(((cx / ext) * k - fx, (cy / ext) * k - fy))
            if cur: rings.append(cur)
            L.append((props, gt, rings))
    return out

def _area(r):
    return sum(r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1] for i in range(len(r) - 1)) / 2

# ---------------- counties (the US Census file build-basemap.py uses) ----------------
_COUNTIES = None
def counties():
    global _COUNTIES
    if _COUNTIES is None:
        j = json.loads(fetch("https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json", "counties.json"))
        rings = []
        for f in j["features"]:
            g = f["geometry"]
            for poly in ([g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]):
                r = np.array(poly[0], np.float64); rings.append((r[:, 0].min(), r[:, 0].max(), r[:, 1].min(), r[:, 1].max(), r))
        _COUNTIES = rings
    return _COUNTIES

# ---------------- coastline (Natural Earth 10 m, the repo's own copy) ----------------
_COAST = None
def coastline():
    """the coast as ink lines in the lines layer, so the map keeps its outline where imagery (which sits under the lines
    layer) covers the base tiles' water edge"""
    global _COAST
    if _COAST is None:
        g = json.load(open(os.path.join(os.path.dirname(HERE), "ne_10m_coastline.geojson")))
        out = []
        for f in g["features"]:
            gm = f["geometry"]
            for ln in ([gm["coordinates"]] if gm["type"] == "LineString" else gm["coordinates"]):
                r = np.array(ln, np.float64)
                if len(r) > 1: out.append((r[:, 0].min(), r[:, 0].max(), r[:, 1].min(), r[:, 1].max(), r))
        _COAST = out
    return _COAST

def tile_bounds(z, x, y):
    n = 2 ** z; lon = lambda X: X / n * 360 - 180; lat = lambda Y: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * Y / n))))
    return lon(x), lon(x + 1), lat(y + 1), lat(y)

def ll_px(z, x, y, lon, lat):
    n = 2 ** z; X = (lon + 180) / 360 * n
    Y = (1 - np.log(np.tan(np.radians(lat)) + 1 / np.cos(np.radians(lat))) / np.pi) / 2 * n
    return (X - x) * T, (Y - y) * T

# ---------------- the tiles ----------------
def base_tile(z, x, y):
    """Tulsa-Live basemap: hillshaded land in basemap.jpg's colours, lakes/rivers/ocean"""
    hs = hillshade(dem_px(z, x, y), z, y)
    rgb = np.stack([np.interp(hs, SRC_IN, DST_IN[k]) for k in range(3)], -1).astype(np.float32)
    v = mvt(z, x, y, {"water", "waterway"})
    lake = Image.new("L", (T, T), 0); sea = Image.new("L", (T, T), 0); riv = Image.new("L", (T, T), 0)
    dl, ds, dr = ImageDraw.Draw(lake), ImageDraw.Draw(sea), ImageDraw.Draw(riv)
    for props, gt, rings in v.get("water", []):
        d = ds if props.get("class") == "ocean" else dl
        for r in rings:
            if len(r) < 3: continue
            pts = [(px * T, py * T) for px, py in r]
            d.polygon(pts, fill=255 if _area(r) > 0 else 0)
    wd = max(1, round(T / 256 * (1.2 if z < 8 else 1.8 if z < 11 else 2.6)))
    for props, gt, rings in v.get("waterway", []):
        if props.get("class") != "river": continue
        for r in rings: dr.line([(px * T, py * T) for px, py in r], fill=255, width=wd, joint="curve")
    riv = np.asarray(riv.filter(ImageFilter.GaussianBlur(.6)), np.float32)[..., None] / 255
    lake = np.asarray(lake, np.float32)[..., None] / 255; sea = np.asarray(sea, np.float32)[..., None] / 255
    rgb = rgb * (1 - riv) + RIVER * riv
    rgb = rgb * (1 - lake) + LAKE * lake
    rgb = rgb * (1 - sea) + OCEAN * sea
    b = io.BytesIO(); Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).save(b, "JPEG", quality=88); return b.getvalue()

def lines_tile(z, x, y):
    """overlay.png's county ink + country/state lines + the coastline, transparent"""
    s = T / 256; im = Image.new("RGBA", (T, T), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    ink = (14, 18, 28)
    if z >= 6:   # counties, like overlay.png (2 px at Tulsa's z9.5 scale)
        w, e, so, no = tile_bounds(z, x, y); pad = (e - w) * .02
        cw = max(1, round(s * (0.8 if z < 7 else 1.2 if z < 10 else 1.6)))
        for x0, x1, y0, y1, r in counties():
            if x1 < w - pad or x0 > e + pad or y1 < so - pad or y0 > no + pad: continue
            X, Y = ll_px(z, x, y, r[:, 0], r[:, 1]); d.line(list(zip(X.tolist(), Y.tolist())), fill=ink + (150,), width=cw)
    w, e, so, no = tile_bounds(z, x, y); pad = (e - w) * .02
    cwd = max(1, round(s * (3 if z >= 6 else 1.4)))   # same weight and ink as the country borders below
    for x0, x1, y0, y1, r in coastline():
        if x1 < w - pad or x0 > e + pad or y1 < so - pad or y0 > no + pad: continue
        X, Y = ll_px(z, x, y, r[:, 0], r[:, 1]); d.line(list(zip(X.tolist(), Y.tolist())), fill=ink + (235,), width=cwd, joint="curve")
    for props, gt, rings in mvt(z, x, y, {"boundary"}).get("boundary", []):
        if props.get("maritime") == 1: continue
        lvl = props.get("admin_level")
        if lvl not in (2, 4): continue
        wd = max(1, round(s * ((2.2 if lvl == 4 else 3) if z >= 6 else 1.4)))
        for r in rings: d.line([(px * T, py * T) for px, py in r], fill=ink + (235,), width=wd, joint="curve")
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()

def _empty_png():
    b = io.BytesIO(); Image.new("RGBA", (T, T), (0, 0, 0, 0)).save(b, "PNG"); return b.getvalue()

def roads_tile(z, x, y):
    """roads.svg's highways: interstates/freeways bright white (.85, 1.35 px), US highways faint (.42, .9 px)"""
    s = T / 256; im = Image.new("RGBA", (T, T), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    v = mvt(z, x, y, {"transportation"}).get("transportation", [])
    if z < 5: return _empty_png()
    for cls, op, wd in (("trunk", .42, .9), ("motorway", .85, 1.35)):
        if cls == "trunk" and z < 7: continue
        for props, gt, rings in v:
            if props.get("class") != cls or props.get("ramp") == 1 or props.get("brunnel") == "tunnel": continue
            for r in rings: d.line([(px * T, py * T) for px, py in r], fill=(255, 255, 255, round(255 * op)), width=max(1, round(s * wd * (1.3 if z >= 9 else 1))), joint="curve")
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()

def tile(kind, z, x, y):
    ext = "jpg" if kind == "base" else "png"
    f = os.path.join(TILES, kind, str(z), str(x), f"{y}.{ext}")
    if os.path.exists(f): return open(f, "rb").read()
    with _lock(f):
        if os.path.exists(f): return open(f, "rb").read()
        data = {"base": base_tile, "lines": lines_tile, "roads": roads_tile}[kind](z, x, y)
        os.makedirs(os.path.dirname(f), exist_ok=True); open(f + ".tmp", "wb").write(data); os.replace(f + ".tmp", f)
        return data
