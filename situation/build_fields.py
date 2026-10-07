"""Situation Room model fields: GFS 0.25 deg deep-layer shear, steering flow and precipitable water around every
active NHC storm, out to 120 h every 12 h.

  python situation/build_fields.py --out-dir fields_out
  -> fields_out/situation/fields/<sid>/index.json             cycle, forecast hours, bounds, colour scales
  -> fields_out/situation/fields/<sid>/<field>_f<FFF>.png      shaded field (Web Mercator, transparent outside data)
  -> fields_out/situation/fields/<sid>/vec_f<FFF>.json         thinned vectors: shear and steering (u, v in kt)

Same fetch pattern as enscenters/gefs_ingest.py: read the GRIB ``.idx`` sidecar on NOAA's public GFS bucket, then
Range-GET only the records needed (PWAT + U/V at 850-200 hPa), decode with cfgrib, crop per storm.
  shear     200-850 hPa vector wind difference (kt)
  steering  850-200 hPa pressure-weighted layer-mean wind (kt)
  pwat      precipitable water (mm)
Isolated: writes only under situation/fields/. Touches nothing in track/ACE/climo.
"""
import argparse, io, json, math, os, sys, tempfile, time, urllib.request
from datetime import datetime, timedelta, timezone
import numpy as np
import xarray as xr
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import build_situation as BS

GFS = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
LEVELS = [850, 700, 500, 400, 300, 250, 200]
HOURS = list(range(0, 121, 12))
HALF_LON, HALF_LAT = 24.0, 18.0
KT = 1.943844

def get(url, rng=None):
    h = {"User-Agent": "triple-a-tropics situation"}
    if rng: h["Range"] = rng
    for i in range(4):
        try: return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60).read()
        except Exception as e:
            if i == 3: raise
            time.sleep(2 + 3 * i)

def latest_cycle():
    now = datetime.now(timezone.utc)
    for back in range(0, 30, 6):
        t = now - timedelta(hours=back); c = t.replace(hour=t.hour // 6 * 6, minute=0, second=0, microsecond=0)
        u = f"{GFS}/gfs.{c:%Y%m%d}/{c:%H}/atmos/gfs.t{c:%H}z.pgrb2.0p25.f120.idx"
        try: get(u); return c
        except Exception: continue
    raise SystemExit("no complete GFS cycle found")

def want(param, level):
    if param == "PWAT" and level.startswith("entire atmosphere"): return True
    if param in ("UGRD", "VGRD") and level.endswith(" mb"):
        try: return int(level.split()[0]) in LEVELS
        except ValueError: return False
    return False

def fetch(cycle, fh):
    base = f"{GFS}/gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos/gfs.t{cycle:%H}z.pgrb2.0p25.f{fh:03d}"
    rows = []
    for line in get(base + ".idx").decode().splitlines():
        p = line.split(":")
        if len(p) >= 5: rows.append((int(p[1]), p[3], p[4]))
    out = io.BytesIO()
    for i, (start, param, level) in enumerate(rows):
        if not want(param, level): continue
        end = rows[i + 1][0] - 1 if i + 1 < len(rows) else ""
        out.write(get(base, f"bytes={start}-{end}"))
    f = tempfile.NamedTemporaryFile(suffix=".grib2", delete=False); f.write(out.getvalue()); f.close()
    try:
        iso = xr.open_dataset(f.name, engine="cfgrib", backend_kwargs={"filter_by_keys": {"typeOfLevel": "isobaricInhPa"}, "indexpath": ""}).load()
        pw = xr.open_dataset(f.name, engine="cfgrib", backend_kwargs={"filter_by_keys": {"typeOfLevel": "atmosphereSingleLayer"}, "indexpath": ""}).load()
    finally: os.unlink(f.name)
    return iso, pw

def crop(da, lon, lat):
    """storm-centred box; GFS longitudes are 0..360, latitudes descending"""
    lo = (lon + 360) % 360
    lons = np.arange(lo - HALF_LON, lo + HALF_LON + .001, .25) % 360
    lats = np.arange(min(89, lat + HALF_LAT), max(-89, lat - HALF_LAT) - .001, -.25)
    return da.sel(longitude=xr.DataArray(lons, dims="x"), latitude=xr.DataArray(lats, dims="y"), method="nearest").values, lats, lons

SCALES = {
    "shear": {"unit": "kt", "stops": [[0, "#2b83ba"], [10, "#4fc3a1"], [15, "#c7e66c"], [20, "#ffe14d"], [25, "#ff9a2f"], [30, "#f5333c"], [40, "#b0186c"], [55, "#6a0fa8"]]},
    "steering": {"unit": "kt", "stops": [[0, "#0b1f3a"], [5, "#1b4f8a"], [10, "#2f8fc6"], [15, "#6cc7c2"], [20, "#c7e66c"], [30, "#ffd24a"], [40, "#ff8a1f"]]},
    "pwat": {"unit": "mm", "stops": [[20, "#7a4a1e"], [30, "#b88a3a"], [40, "#e6d27a"], [45, "#9ed36a"], [50, "#3fb36a"], [55, "#1f8f9a"], [60, "#2f6ee6"], [65, "#6a3fd6"], [70, "#c23fd6"]]},
}

def colour(v, stops, alpha=235):
    xs = np.array([s[0] for s in stops], float); cs = np.array([[int(s[1][i:i + 2], 16) for i in (1, 3, 5)] for s in stops], float)
    r = np.interp(v, xs, cs[:, 0]); g = np.interp(v, xs, cs[:, 1]); b = np.interp(v, xs, cs[:, 2])
    a = np.where(np.isfinite(v), alpha, 0)
    return np.dstack([r, g, b, a]).astype(np.uint8)

def to_mercator(img, lats):
    """rows are equally spaced in latitude; resample them to equal spacing in Mercator y"""
    my = lambda la: np.log(np.tan(np.pi / 4 + np.radians(la) / 2))
    y0, y1 = my(lats[0]), my(lats[-1]); out_y = np.linspace(y0, y1, len(lats))
    src = np.interp(out_y, my(lats)[::-1], np.arange(len(lats))[::-1])
    return img[np.clip(np.round(src).astype(int), 0, len(lats) - 1)]

def png(arr, path):
    Image.fromarray(arr, "RGBA").resize((arr.shape[1] * 2, arr.shape[0] * 2), Image.BILINEAR).save(path, optimize=True)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out-dir", default="fields_out"); ap.add_argument("--hours", default="")
    a = ap.parse_args()
    storms = [s for s in BS.nhc() if s.get("latitudeNumeric") is not None]
    if not storms: print("fields: no active storms"); return
    cyc = latest_cycle(); hours = [int(h) for h in a.hours.split(",")] if a.hours else HOURS
    print(f"fields: GFS {cyc:%Y%m%d %H}Z, {len(storms)} storm(s)", flush=True)
    meta = {}
    for s in storms:
        sid = s["id"].lower(); od = os.path.join(a.out_dir, "situation", "fields", sid); os.makedirs(od, exist_ok=True)
        meta[sid] = {"dir": od, "lon": float(s["longitudeNumeric"]), "lat": float(s["latitudeNumeric"]), "hours": []}
    for fh in hours:
        try: iso, pw = fetch(cyc, fh)
        except Exception as e:
            print(f"fields: f{fh:03d} failed: {e}", flush=True); continue
        p = iso["isobaricInhPa"].values
        for sid, m in meta.items():
            u, lats, lons = crop(iso["u"], m["lon"], m["lat"]); v, _, _ = crop(iso["v"], m["lon"], m["lat"])
            ilev = {int(x): i for i, x in enumerate(p)}
            su = (u[ilev[200]] - u[ilev[850]]) * KT; sv = (v[ilev[200]] - v[ilev[850]]) * KT
            w = np.array([1.0 if L in (850, 200) else 2.0 for L in LEVELS]); w /= w.sum()   # trapezoid weights over the pressure levels
            stu = sum(w[k] * u[ilev[L]] for k, L in enumerate(LEVELS)) * KT; stv = sum(w[k] * v[ilev[L]] for k, L in enumerate(LEVELS)) * KT
            pwat, _, _ = crop(pw["pwat"], m["lon"], m["lat"])
            png(to_mercator(colour(np.hypot(su, sv), SCALES["shear"]["stops"]), lats), os.path.join(m["dir"], f"shear_f{fh:03d}.png"))
            png(to_mercator(colour(np.hypot(stu, stv), SCALES["steering"]["stops"], 170), lats), os.path.join(m["dir"], f"steering_f{fh:03d}.png"))
            png(to_mercator(colour(pwat, SCALES["pwat"]["stops"]), lats), os.path.join(m["dir"], f"pwat_f{fh:03d}.png"))
            vec = {"shear": [], "steering": []}
            for j in range(0, len(lats), 6):
                for i in range(0, len(lons), 6):
                    lo = float(lons[i]); lo = lo - 360 if lo > 180 else lo
                    vec["shear"].append([round(lo, 2), round(float(lats[j]), 2), round(float(su[j, i]), 1), round(float(sv[j, i]), 1)])
                    vec["steering"].append([round(lo, 2), round(float(lats[j]), 2), round(float(stu[j, i]), 1), round(float(stv[j, i]), 1)])
            json.dump(vec, open(os.path.join(m["dir"], f"vec_f{fh:03d}.json"), "w"), separators=(",", ":"))
            m["bounds"] = [float(lons[0] - 360 if lons[0] > 180 else lons[0]), float(lats[-1]), float((lons[0] - 360 if lons[0] > 180 else lons[0]) + 2 * HALF_LON), float(lats[0])]
            m["hours"].append(fh)
        print(f"fields: f{fh:03d} done", flush=True)
    for sid, m in meta.items():
        json.dump({"model": "GFS", "cycle": f"{cyc:%Y-%m-%dT%H:00Z}", "hours": m["hours"], "bounds": m.get("bounds"), "scales": SCALES,
                   "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, open(os.path.join(m["dir"], "index.json"), "w"), separators=(",", ":"))
        print(f"fields: {sid} {len(m['hours'])} hours")

if __name__ == "__main__":
    main()
