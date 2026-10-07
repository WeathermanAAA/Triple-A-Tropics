"""Google DeepMind Weather Lab ensemble diagnostics, per storm and per cycle, every basin.

  python gdmdiag/build.py --out-dir gdm_out [--prior gdm_out/models/gdmdiag/index.json] [--cycle YYYYMMDDHH]
  -> gdm_out/models/gdmdiag/<cycle>/<TRACKID>.json   every diagnostic for one storm, one cycle, every suite
  -> gdm_out/models/gdmdiag/index.json               rolling cycle list + per-storm summaries (drive the trends panel)

Source: the anonymous Weather Lab "paired" CSVs (storms matched to real systems, keyed by ATCF track_id, with RMW and
34/50/64-kt quadrant radii):
  FNV3                 50 members
  WNV3 (WeatherNext 3) 64 members
  GENC (GenCast)       50 members, track + intensity only (no radii)
  FNV3_LARGE_ENSEMBLE  1000 members (statistics only; members are not shipped to the browser)
plus a DERIVED Google super ensemble that pools FNV3 + WN3 + GenCast with equal weight per model (each member of a
model carries 1 / (models x that model's members)); quantities a model cannot supply (radii for GenCast) are pooled
over the models that can, and the output says which.

Columns are mapped by header name (Weather Lab has inserted columns before). Longitudes are unwrapped per storm around
the storm's initial longitude, so a track crossing 180 stays continuous; the browser draws in the same frame.
Weather Lab data is experimental and under Google DeepMind's terms (CC BY 4.0 once older than 48 h); attribution is
carried in every file. Isolated: writes only under models/gdmdiag/; touches no track/ACE/climo code or data.
"""
from __future__ import annotations

import argparse, base64, csv, datetime as dt, io, json, math, os, sys, time, urllib.request
from collections import defaultdict

import numpy as np

URL = ("https://deepmind.google.com/science/weatherlab/download/cyclones/{m}/ensemble/paired/csv/"
       "{m}_{c:%Y_%m_%dT%H}_00_paired.csv")
SUITES = {   # slug: (Weather Lab model, label, ship members to the browser)
    "fnv3": ("FNV3", "FNV3", True),
    "wnv3": ("WNV3", "WeatherNext 3", True),
    "genc": ("GENC", "GenCast", True),
    "fnv3x": ("FNV3_LARGE_ENSEMBLE", "FNV3 1000-member", False),
}
POOL = ["fnv3", "wnv3", "genc"]          # the Google super ensemble
METHOD = "gdm-pool-v1: equal weight per model (each member 1/(models x members)); radii-based products pool the models that carry radii"
ATTRIB = "Data: Google DeepMind Weather Lab (experimental, not for real-world use; CC BY 4.0 after 48 h)."
LEADS = list(range(0, 241, 6))
LOOKBACK = 8
CATS = [("diss", None), ("TD", 0), ("TS", 34), ("C1", 64), ("C2", 83), ("C3", 96), ("C4", 113), ("C5", 137)]
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
UA = {"User-Agent": "triple-a-tropics gdmdiag (+https://triple-a-tropics.com)"}


def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
            if i == tries - 1: raise
        except Exception:
            if i == tries - 1: raise
        time.sleep(3 + 5 * i)


# ---------------------------------------------------------------- parsing
RAD = [f"radius_{k}_knot_winds_{q}_km" for k in (34, 50, 64) for q in ("ne", "se", "sw", "nw")]

def parse(raw):
    """{track_id: {sample: np.array(rows x 17)}}; columns h, lat, lon, vmax, mslp, rmw, r34x4, r50x4, r64x4 (NaN = absent)"""
    lines = [l for l in raw.decode("utf-8", "replace").splitlines() if l and not l.startswith("#")]
    rd = csv.DictReader(lines)
    need = ["track_id", "sample", "lat", "lon", "maximum_sustained_wind_speed_knots", "minimum_sea_level_pressure_hpa"]
    miss = [c for c in need if c not in (rd.fieldnames or [])]
    if miss: raise ValueError(f"Weather Lab CSV lacks columns {miss}; has {rd.fieldnames}")
    hasr = all(c in rd.fieldnames for c in RAD)
    out = defaultdict(lambda: defaultdict(list))
    for r in rd:
        try:
            h = float(r["lead_time_hours"]) if r.get("lead_time_hours") not in (None, "") else _lead(r)
            f = lambda k: float(r[k]) if r.get(k) not in (None, "", "nan") else np.nan
            row = [h, f("lat"), f("lon"), f("maximum_sustained_wind_speed_knots"), f("minimum_sea_level_pressure_hpa"),
                   f("radius_of_maximum_winds_km") if "radius_of_maximum_winds_km" in r else np.nan] + \
                  ([f(c) for c in RAD] if hasr else [np.nan] * 12)
        except (ValueError, KeyError):
            continue
        if not (np.isfinite(row[1]) and np.isfinite(row[2])): continue
        out[r["track_id"]][int(float(r["sample"]))].append(row)
    return {t: {s: np.array(sorted(v)) for s, v in ms.items()} for t, ms in out.items()}, hasr

def _lead(r):
    a = dt.datetime.fromisoformat(r["init_time"]); b = dt.datetime.fromisoformat(r["valid_time"])
    return (b - a).total_seconds() / 3600


# ---------------------------------------------------------------- ensemble container
class Ens:
    """members on the common lead grid: arrays [member, lead, 18 columns] (NaN = not alive), with per-member weights"""
    def __init__(self, tracks, weights, lon0, model_of=None):
        n, L = len(tracks), len(LEADS)
        self.n = n; self.w = np.asarray(weights, float); self.model = model_of or [None] * n
        self.A = np.full((n, L, 18), np.nan)
        for i, t in enumerate(tracks):
            t = t.copy(); t[:, 2] = unwrap(t[:, 2], lon0)
            for row in t:
                if row[0] in LIDX: self.A[i, LIDX[row[0]]] = row
        self.raw = tracks; self.lon0 = lon0
    def col(self, k): return self.A[:, :, k]
    @property
    def alive(self): return np.isfinite(self.A[:, :, 1])

LIDX = {float(h): i for i, h in enumerate(LEADS)}

def unwrap(lon, lon0):
    lon = np.asarray(lon, float)
    return lon0 + ((lon - lon0 + 180) % 360) - 180


# ---------------------------------------------------------------- statistics
QS = [0, .1, .25, .5, .75, .9, 1]

def wq(v, w, qs=QS):
    m = np.isfinite(v) & (w > 0)
    if m.sum() == 0: return None
    v, w = v[m], w[m]; o = np.argsort(v); v, w = v[o], w[o]
    c = (np.cumsum(w) - .5 * w) / w.sum()
    return [round(float(np.interp(q, c, v)), 1) for q in qs]

def r1(x, n=1):
    return None if x is None or not np.isfinite(x) else round(float(x), n)

def km_xy(lat, lon, lat0, lon0):
    return (lon - lon0) * 111.32 * math.cos(math.radians(lat0)), (lat - lat0) * 110.57

def diag(E: Ens, radii_ok=True):
    W = E.w / E.w.sum(); A = E.alive; L = len(LEADS)
    lat, lon, v, p = E.col(1), E.col(2), E.col(3), E.col(4)
    d = {"n": int(E.n), "leads": LEADS}
    d["alive"] = [r1((W * A[:, k]).sum(), 3) for k in range(L)]
    d["vmax_q"] = [wq(v[:, k], W) for k in range(L)]
    d["mslp_q"] = [wq(p[:, k], W) for k in range(L)]
    d["vmax_mean"] = [r1(np.nansum(W * np.nan_to_num(v[:, k])) / max(1e-9, (W * A[:, k]).sum())) if A[:, k].any() else None for k in range(L)]
    # mean position + spread + ellipses
    mean, spread, ell = [], [], {}
    for k in range(L):
        m = A[:, k]
        if (W[m]).sum() < .05: mean.append(None); spread.append(None); continue
        ww = W[m] / W[m].sum(); la, lo = lat[m, k], lon[m, k]
        cla, clo = float((ww * la).sum()), float((ww * lo).sum()); mean.append([round(cla, 2), round(clo, 2)])
        x, y = km_xy(la, lo, cla, clo); spread.append(round(float((ww * np.hypot(x, y)).sum())))
        if LEADS[k] % 24 == 0 and LEADS[k] and m.sum() >= 5:
            C = np.cov(np.vstack([x, y]), aweights=ww) if m.sum() > 2 else np.eye(2)
            ev, evec = np.linalg.eigh(C); ev = np.maximum(ev, 1e-6)
            polys = {}
            for pc, kk in (("50", 1.1774), ("90", 2.1460)):
                pts = []
                for a in np.linspace(0, 2 * math.pi, 41):
                    u = kk * (math.sqrt(ev[1]) * math.cos(a) * evec[:, 1] + math.sqrt(ev[0]) * math.sin(a) * evec[:, 0])
                    pts.append([round(cla + u[1] / 110.57, 2), round(clo + u[0] / (111.32 * math.cos(math.radians(cla))), 2)])
                polys[pc] = pts
            ell[str(LEADS[k])] = polys
    d["mean"], d["spread_km"], d["ellipses"] = mean, spread, ell
    # categories by lead
    cat = []
    for k in range(0, L, 2):
        vv = v[:, k]; row = {"h": LEADS[k], "diss": r1((W * ~A[:, k]).sum(), 3)}
        for (nm, lo_), (_, hi_) in zip(CATS[1:], CATS[2:] + [(None, 1e9)]):
            row[nm] = r1((W * (A[:, k] & (vv >= lo_) & (vv < hi_))).sum(), 3)
        cat.append(row)
    d["cat"] = cat
    # intensity change: dv over 12/24 h, RI (>= 30 kt / 24 h)
    dv24 = np.full_like(v, np.nan); dv24[:, 4:] = v[:, 4:] - v[:, :-4]
    dv12 = np.full_like(v, np.nan); dv12[:, 2:] = v[:, 2:] - v[:, :-2]
    ri = np.nan_to_num(dv24) >= 30
    d["ri_lead"] = [r1((W * ri[:, k]).sum(), 3) for k in range(L)]
    d["ri_cum"] = [r1((W * ri[:, :k + 1].any(1)).sum(), 3) for k in range(L)]
    bins = list(range(-60, 71, 10))
    def hist(x, k):
        m = np.isfinite(x[:, k])
        if not m.any(): return None
        h, _ = np.histogram(np.clip(x[m, k], -59.9, 69.9), bins=bins, weights=W[m]); return [round(float(a / W[m].sum()), 3) for a in h]
    d["dv_bins"] = bins
    d["dv24"] = {str(h): hist(dv24, LIDX[float(h)]) for h in range(24, 241, 12)}
    d["dv12"] = {str(h): hist(dv12, LIDX[float(h)]) for h in range(12, 241, 12)}
    # lifetime peak (0-240 h) and when it happens
    has = A.any(1); pv = np.where(has, np.nanmax(np.where(A, v, -1), 1), np.nan)
    pk = np.where(has, np.nanargmax(np.where(A, v, -1), 1), 0)
    ph = np.array(LEADS, float)[pk]
    vb = list(range(0, 191, 10)); hb = list(range(0, 241, 24))
    d["peak_q"] = wq(pv, W)
    d["peak_lead_q"] = wq(np.where(has, ph, np.nan), W)
    h1, _ = np.histogram(np.clip(pv[has], 0, 189.9), bins=vb, weights=W[has]); d["peak_hist"] = {"bins": vb, "p": [round(float(x), 3) for x in h1]}
    h2, _, _ = np.histogram2d(np.clip(pv[has], 0, 189.9), np.clip(ph[has], 0, 239.9), bins=[vb, hb], weights=W[has])
    d["peak2d"] = {"vbins": vb, "hbins": hb, "p": [[round(float(x), 3) for x in row] for row in h2]}
    # ACE accumulated by lead (6-hourly, >= 34 kt)
    ace = np.cumsum(np.where(A & (np.nan_to_num(v) >= 34), np.nan_to_num(v) ** 2 / 1e4, 0), 1)
    d["ace_q"] = [wq(ace[:, k], W) for k in range(0, L, 2)]
    # structure
    if radii_ok and np.isfinite(E.col(5)).any():
        rmw = E.col(5); r34 = E.A[:, :, 6:10]; r50 = E.A[:, :, 10:14]; r64 = E.A[:, :, 14:18]
        def qmean(R): x = np.where(R > 0, R, np.nan); return np.where(np.isfinite(x).any(2), np.nanmean(x, 2), np.nan)
        m34, m50, m64 = qmean(r34), qmean(r50), qmean(r64)
        ok = A & (np.nan_to_num(v) >= 34)
        d["rmw_q"] = [wq(np.where(ok[:, k], rmw[:, k], np.nan), W) for k in range(L)]
        d["r34_q"] = [wq(m34[:, k], W) for k in range(L)]
        d["r50_med"] = [r1(wq(m50[:, k], W, [.5])[0]) if np.isfinite(m50[:, k]).any() else None for k in range(L)]
        d["r64_med"] = [r1(wq(m64[:, k], W, [.5])[0]) if np.isfinite(m64[:, k]).any() else None for k in range(L)]
        area = np.where(r34 > 0, r34, 0) ** 2 * math.pi / 4
        a34 = np.where((r34 > 0).any(2), area.sum(2), np.nan) / 1e3   # thousand km^2
        d["area34_q"] = [wq(a34[:, k], W) for k in range(L)]
        rose = {}
        for nm, R in (("34", r34), ("50", r50), ("64", r64)):
            vals = R[np.arange(E.n), pk]                                # each member's radii at its peak
            rose[nm] = [r1(wq(np.where(vals[:, q] > 0, vals[:, q], np.nan), W, [.5])[0]) if (vals[:, q] > 0).any() else 0 for q in range(4)]
        d["rose_at_peak"] = rose
    return d


# ---------------------------------------------------------------- wind / strike probabilities on a grid
def grids(E: Ens, radii_ok, hours=(72, 120)):
    """P(34/50/64-kt winds) from each member's own quadrant radii, P(centre within 120 km), median 34-kt arrival"""
    A = E.alive; H = max(hours)
    pts = []
    for i in range(E.n):
        t = E.A[i]; ok = np.isfinite(t[:, 1]) & (np.array(LEADS) <= H)
        if ok.sum() < 1: pts.append(None); continue
        hh = np.array(LEADS, float)[ok]; T = t[ok]
        fine = np.arange(hh[0], hh[-1] + .01, 2.0)
        pts.append((fine, np.column_stack([np.interp(fine, hh, T[:, c]) for c in range(1, 18)])))
    allp = np.concatenate([p[1][:, :2] for p in pts if p is not None]) if any(p is not None for p in pts) else None
    if allp is None: return None
    s, n = math.floor(allp[:, 0].min() - 5), math.ceil(allp[:, 0].max() + 5)
    w_, e_ = math.floor(allp[:, 1].min() - 6), math.ceil(allp[:, 1].max() + 6)
    s, n = max(s, -70), min(n, 70)
    res = .25; ny, nx = int((n - s) / res), int((e_ - w_) / res)
    if nx * ny > 400 * 300: res = .5; ny, nx = int((n - s) / res), int((e_ - w_) / res)
    glat = n - (np.arange(ny) + .5) * res; glon = w_ + (np.arange(nx) + .5) * res
    W = E.w / E.w.sum(); kinds = ["c120"] + (["34", "50", "64"] if radii_ok else [])
    P = {f"{k}_{h}": np.zeros((ny, nx)) for k in kinds for h in hours}
    arrival = np.full((E.n, ny, nx), np.nan, np.float32) if radii_ok else None
    for i, pp in enumerate(pts):
        if pp is None: continue
        fine, T = pp
        hit = {f"{k}_{h}": np.zeros((ny, nx), bool) for k in kinds for h in hours}
        for j in range(len(fine)):
            la, lo = T[j, 0], T[j, 1]; rq = T[j, 5:17].reshape(3, 4)
            rmax = max(150.0, np.nanmax(np.nan_to_num(rq)) if radii_ok else 150.0)
            dla = rmax / 110.57 + res; dlo = rmax / (111.32 * max(.2, math.cos(math.radians(la)))) + res
            r0, r1_ = np.searchsorted(-glat, -(la + dla)), np.searchsorted(-glat, -(la - dla))
            c0, c1 = np.searchsorted(glon, lo - dlo), np.searchsorted(glon, lo + dlo)
            if r1_ <= r0 or c1 <= c0: continue
            Y, X = np.meshgrid(glat[r0:r1_], glon[c0:c1], indexing="ij")
            x, y = km_xy(Y, X, la, lo); dist = np.hypot(x, y)
            q = np.where(x >= 0, np.where(y >= 0, 0, 1), np.where(y < 0, 2, 3))     # NE, SE, SW, NW
            for h in hours:
                if fine[j] > h: continue
                hit[f"c120_{h}"][r0:r1_, c0:c1] |= dist <= 120
                if radii_ok:
                    for ki, kn in enumerate(("34", "50", "64")):
                        R = np.nan_to_num(rq[ki])[q]
                        hit[f"{kn}_{h}"][r0:r1_, c0:c1] |= (R > 0) & (dist <= R)
            if radii_ok:
                R = np.nan_to_num(rq[0])[q]; m = (R > 0) & (dist <= R)
                sub = arrival[i, r0:r1_, c0:c1]; sub[m & np.isnan(sub)] = fine[j]
        for k in P: P[k] += W[i] * hit[k]
    enc = lambda g: base64.b64encode(np.clip(np.round(g * 100), 0, 100).astype(np.uint8).tobytes()).decode()
    out = {"bbox": [w_, s, e_, n], "res": res, "nx": nx, "ny": ny, "hours": list(hours), "p": {k: enc(g) for k, g in P.items()}}
    if radii_ok:
        # weighted median arrival time of 34-kt winds among members that bring them (255 = under 10 % of members)
        arr = np.full((ny, nx), 255, np.uint8); frac = P[f"34_{max(hours)}"]
        idx = np.argwhere(frac >= .1)
        for r, c in idx:
            a = arrival[:, r, c]; m = np.isfinite(a)
            if m.any(): arr[r, c] = min(254, int(round(wq(a[m], W[m], [.5])[0] / 2)))
        out["arrival34"] = base64.b64encode(arr.tobytes()).decode()
    return out


# ---------------------------------------------------------------- landfall
_LAND = None
def land():
    """0.1-degree global raster of Natural Earth 10m countries (the 10m file carries the names): 0 = sea, k = country index + 1"""
    global _LAND
    if _LAND is not None: return _LAND
    from PIL import Image, ImageDraw
    gj = json.load(open(os.path.join(ROOT, "ne_10m_admin_0_countries.geojson")))
    img = Image.new("I", (3600, 1800), 0); dr = ImageDraw.Draw(img); names = []
    for k, f in enumerate(gj["features"]):
        p = f["properties"]; names.append(p.get("NAME") or p.get("ADMIN") or p.get("name") or "?")
        g = f["geometry"]; polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for poly in polys:
            ring = poly[0]
            dr.polygon([((x + 180) * 10, (90 - y) * 10) for x, y in ring], fill=k + 1)
    _LAND = (np.array(img, dtype=np.int32), names); return _LAND

def landfall(E: Ens):
    M, names = land(); W = E.w / E.w.sum()
    def at(la, lo):
        c = int(((lo + 180) % 360) * 10) % 3600; r = min(1799, max(0, int((90 - la) * 10))); return M[r, c]
    ev = []
    for i in range(E.n):
        t = E.A[i]; ok = np.isfinite(t[:, 1])
        if ok.sum() < 2: ev.append(None); continue
        hh = np.array(LEADS, float)[ok]; fine = np.arange(hh[0], hh[-1] + .01, 1.0)
        la = np.interp(fine, hh, t[ok, 1]); lo = np.interp(fine, hh, t[ok, 2]); vv = np.interp(fine, hh, t[ok, 3])
        sea = False; hit = None
        for j in range(len(fine)):
            c = at(la[j], lo[j])
            if c == 0: sea = True
            elif sea: hit = (fine[j], la[j], lo[j], vv[j], c); break
        ev.append(hit)
    tb = list(range(0, 241, 12)); cats = [c for c, _ in CATS[1:]]
    timing = {c: [0.0] * (len(tb) - 1) for c in cats}; atcat = {c: 0.0 for c in cats}; where = defaultdict(float); pts = []
    p120 = pall = 0.0
    for i, e in enumerate(ev):
        if not e: continue
        h, la, lo, vv, c = e; cn = catname(vv)
        pall += W[i]; p120 += W[i] if h <= 120 else 0
        timing[cn][min(len(tb) - 2, int(h // 12))] += W[i]; atcat[cn] += W[i]; where[names[c - 1]] += W[i]
        pts.append([round(float(la), 2), round(float(lo), 2), round(float(h)), round(float(vv)), E.model[i]])
    tot = max(1e-9, pall)
    return {"p120": r1(p120, 3), "p240": r1(pall, 3), "bins": tb,
            "timing": {c: [round(x, 3) for x in v] for c, v in timing.items()},
            "cat": {c: r1(x / tot, 3) for c, x in atcat.items()},
            "where": sorted([[k, r1(x, 3)] for k, x in where.items()], key=lambda z: -z[1])[:8],
            "pts": pts if E.n <= 400 else None}

def catname(v):
    for nm, lo in reversed(CATS[1:]):
        if v >= lo: return nm
    return "TD"


# ---------------------------------------------------------------- per-cycle build
def members_payload(E: Ens):
    out = []
    for i in range(E.n):
        t = E.A[i]; ok = np.isfinite(t[:, 1])
        out.append({"m": E.model[i], "t": [[LEADS[k], round(float(t[k, 1]), 2), round(float(t[k, 2]), 2),
                                             r1(t[k, 3], 0), r1(t[k, 4], 0)] for k in np.where(ok)[0]]})
    return out

def build_cycle(cyc, names):
    raw = {}
    for slug, (m, _, _) in SUITES.items():
        b = get(URL.format(m=m, c=cyc))
        if b: raw[slug] = parse(b)
        print(f"gdmdiag: {cyc:%Y%m%d%H} {m}: {'ok' if b else 'not published'}", flush=True)
    if not raw: return None, {}
    ids = sorted(set().union(*[set(r[0]) for r in raw.values()]))
    files, summ = {}, {}
    for tid in ids:
        first = None
        for slug in raw:
            if tid in raw[slug][0]:
                t = next(iter(raw[slug][0][tid].values())); first = t[0]; break
        lon0 = float(first[2])
        doc = {"id": tid, "name": names.get(tid, ""), "basin": tid[:2], "cycle": f"{cyc:%Y%m%d%H}", "init": f"{cyc:%Y-%m-%dT%H:00Z}",
               "lon0": round(lon0, 2), "attribution": ATTRIB, "suites": {}}
        ens = {}
        for slug, (tracks, hasr) in raw.items():
            if tid not in tracks: continue
            ms = tracks[tid]; n = len(ms)
            E = Ens(list(ms.values()), [1.0] * n, lon0, [slug] * n); ens[slug] = (E, hasr)
        pool = [s for s in POOL if s in ens]
        if len(pool) >= 2:
            tr, w, mo = [], [], []
            for s in pool:
                E = ens[s][0]; tr += E.raw; w += [1.0 / (len(pool) * E.n)] * E.n; mo += [s] * E.n
            ens["gdm"] = (Ens(tr, w, lon0, mo), True)
        for slug, (E, hasr) in ens.items():
            if slug == "gdm":
                rad = [s for s in pool if ens[s][1]]
                ER = Ens(sum([ens[s][0].raw for s in rad], []), sum([[1.0 / (len(rad) * ens[s][0].n)] * ens[s][0].n for s in rad], []), lon0,
                         sum([[s] * ens[s][0].n for s in rad], [])) if rad else None
                d = diag(E, False)
                if ER is not None:
                    dr = diag(ER, True)
                    for k in ("rmw_q", "r34_q", "r50_med", "r64_med", "area34_q", "rose_at_peak"):
                        if k in dr: d[k] = dr[k]
                d["grid"] = grids(ER if ER is not None else E, ER is not None)
                d["landfall"] = landfall(E)
                d["label"] = "Google super ensemble"; d["derived"] = True; d["pooled"] = pool; d["radii_from"] = rad; d["method"] = METHOD
            else:
                d = diag(E, hasr); d["grid"] = grids(E, hasr); d["landfall"] = landfall(E)
                d["label"] = SUITES[slug][1]; d["radii"] = bool(hasr)
                if SUITES[slug][2]: d["members"] = members_payload(E)
            doc["suites"][slug] = d
        files[tid] = doc
        summ[tid] = {"name": doc["name"], "basin": doc["basin"], "suites": {s: {
            "mean": [doc["suites"][s]["mean"][LIDX[float(h)]] for h in range(0, 121, 24)],
            "peak50": (doc["suites"][s]["peak_q"] or [None] * 7)[3],
            "ri72": doc["suites"][s]["ri_cum"][LIDX[72.0]],
            "lf120": doc["suites"][s]["landfall"]["p120"]} for s in doc["suites"]}}
    return files, summ, sorted(raw)


def storm_names():
    """ATCF id -> name from the site's global feed (best effort)"""
    try:
        gj = json.loads(get("https://cdn.triple-a-tropics.com/global_storms.geojson"))
    except Exception:
        return {}
    out = {}
    for f in gj.get("features", []):
        p = f.get("properties") or {}; sid = (p.get("storm_id") or "").upper()
        m = sid.split("_")[-1]
        if len(m) == 8 and p.get("name"): out[m] = p["name"]
    return out

def latest_cycles(n):
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    c = now.replace(hour=now.hour // 6 * 6, minute=0, second=0, microsecond=0)
    return [c - dt.timedelta(hours=6 * i) for i in range(n)]

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out-dir", default="gdm_out"); ap.add_argument("--prior", default="")
    ap.add_argument("--cycle", default=""); ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    od = os.path.join(a.out_dir, "models", "gdmdiag"); os.makedirs(od, exist_ok=True)
    idx = {"cycles": {}}
    if a.prior and os.path.exists(a.prior):
        try: idx = json.load(open(a.prior))
        except Exception: pass
    names = storm_names()
    want = [dt.datetime.strptime(a.cycle, "%Y%m%d%H")] if a.cycle else latest_cycles(LOOKBACK)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    for cyc in want:
        key = f"{cyc:%Y%m%d%H}"; have = idx["cycles"].get(key)
        # rebuild a cycle while it is young and a suite is still missing (GenCast / the 1000-member run post later)
        if have and not a.force and (set(have.get("models", [])) >= set(SUITES) or now - cyc > dt.timedelta(hours=18)): continue
        res = build_cycle(cyc, names)
        if not res or res[0] is None: continue
        files, summ, models = res
        os.makedirs(os.path.join(od, key), exist_ok=True)
        for tid, doc in files.items():
            json.dump(doc, open(os.path.join(od, key, f"{tid}.json"), "w"), separators=(",", ":"), allow_nan=False)
        idx["cycles"][key] = {"models": models, "storms": summ, "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        print(f"gdmdiag: {key}: {len(files)} storms ({', '.join(models)})", flush=True)
    keep = sorted(idx["cycles"], reverse=True)[:LOOKBACK]
    idx["cycles"] = {k: idx["cycles"][k] for k in keep}
    for k in idx["cycles"]:   # names learnt later (an invest named after the fact) flow back into the index
        for tid, s in idx["cycles"][k]["storms"].items():
            if names.get(tid): s["name"] = names[tid]
    idx.update({"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "suites": {k: v[1] for k, v in SUITES.items()} | {"gdm": "Google super ensemble"},
                "method": METHOD, "attribution": ATTRIB})
    json.dump(idx, open(os.path.join(od, "index.json"), "w"), separators=(",", ":"), allow_nan=False)
    print(f"gdmdiag: index has {len(idx['cycles'])} cycles")

if __name__ == "__main__":
    main()
