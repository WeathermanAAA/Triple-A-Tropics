#!/usr/bin/env python3
"""UHR scatterometer feed for the Satellite Explorer's Scatterometer layer.

NOAA/STAR Ultra-High-Resolution ASCAT ocean-surface winds: ~2 km-class
gridded wind fields (vs the ~25 km operational product), swath extended
toward the coast, cut per STORM by the provider and published same-day on
an anonymous HTTPS directory (no creds):

    https://manati.star.nesdis.noaa.gov/UHR_ASCAT/UHR_ASCAT{B,C}/{year}/
    {STORMNAME}_{YYYYMMDD}_{orbit}_{B|C}_{A|D}-cmod5h-scaled_v3.nc

Each file is one orbit's storm-centered box (A/D = ascending/descending);
storm passes are found by the provider's NAME tag + the same distance
association the operational feed uses (ascatobs.storms). Output rides the
existing scatterometer feed contract so the explorer reuses the barb
painter, storm filter, and wind-speed legend unchanged:

    ascat/uhr/manifest.json        passes index (main-feed schema + uhr flag)
    ascat/uhr/{pass_id}.json       decimated wvc arrays {la,lo,kt,dir} + path
    ascat/uhr/{pass_id}.webp       the ~2 km wind-speed FIELD, colorized on
                                   the high-contrast kt ramp (bounds in the
                                   pass/manifest entry; barbs draw on top)

Decode notes:
  * the files ship a broken ``valid_range`` (scaled units, raw comparison)
    that makes netCDF4's auto-masking blank lat/lon entirely — unpack
    manually off _FillValue + scale/offset.
  * ``dir`` is oceanographic (flow-TO); stored FROM like the main feed.
  * land cells drop; QC-flagged cells KEEP (rain flags at 2 km would gut
    the storm core this product exists to show).

Modes: --store r2 (workflow) | --store local:/path (verification).
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import math
import os
import re
import tempfile
import urllib.request
from pathlib import Path

import numpy as np

# The R2 write kill switch (tat_killswitch.py at the repo root, mirrored in
# tsr). Optional on purpose: a missing or broken module means "allowed" --
# the switch can only ever STOP writes, never break a lane. Deletes are
# never guarded (free on R2; prune must keep reducing storage).
try:
    import tat_killswitch
except Exception:  # noqa: BLE001
    tat_killswitch = None

BASE = "https://manati.star.nesdis.noaa.gov/UHR_ASCAT"
SATS = {"B": ("UHR_ASCATB", "metopb", "UHR ASCAT-B"),
        "C": ("UHR_ASCATC", "metopc", "UHR ASCAT-C")}
NAME_RE = re.compile(
    r"^(?P<name>.+)_(?P<date>\d{8})_(?P<orbit>\d+)_(?P<sat>[BC])_"
    r"(?P<ad>[AD])-cmod5h-scaled_v3\.nc$")
UTC = dt.timezone.utc
EPOCH = dt.datetime(2000, 1, 1, tzinfo=UTC)
MS_TO_KT = 1.94384
WINDOW_H = 72          # display window; passes older than this prune
STRIDE = 6             # barb decimation: ~2.3 km grid -> ~14 km cells
FIELD_DEG = 0.03       # field raster bin (~3 km)
CACHE_MANIFEST = "public, max-age=120"
CACHE_IMMUTABLE = "public, max-age=31536000, immutable"

# the explorer's high-contrast kt ramp (ascat.js KT_SCALE_HC) — the field is
# baked with the SAME stepped classes the barbs/legend use. Keep in sync.
KT_SCALE_HC = [
    (0, "#3a6dff"), (10, "#2aa6ff"), (20, "#16d6ec"), (30, "#11e6b0"),
    (34, "#34e85f"), (40, "#95ef3a"), (45, "#dbff3a"), (50, "#fff23a"),
    (55, "#ffc91f"), (60, "#ff9a14"), (64, "#ff2f3a"), (83, "#e0143f"),
    (96, "#ff2a86"), (113, "#c45bff"), (137, "#f0c2ff")]


def _http(url, timeout=120):
    req = urllib.request.Request(url, headers={
        "User-Agent": "triple-a-tropics-uhr (contact: site owner)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def list_candidates(now: dt.datetime, max_age_h: float):
    """Directory listings -> recent storm files, newest first."""
    out = []
    cutoff = (now - dt.timedelta(hours=max_age_h)).strftime("%Y%m%d")
    for letter, (dirname, sat_key, sensor) in SATS.items():
        url = f"{BASE}/{dirname}/{now.year}/"
        try:
            html = _http(url, timeout=60).decode("utf-8", "replace")
        except Exception as e:                              # noqa: BLE001
            print(f"[uhr] listing unavailable ({dirname}): {e}")
            continue
        for fn in set(re.findall(r'href="([^"]+\.nc)"', html)):
            m = NAME_RE.match(fn)
            if not m or m.group("sat") != letter:
                continue
            if m.group("name").lower() == "test":
                continue
            if m.group("date") < cutoff:
                continue
            pid = (f"uhr_{sat_key}_{m.group('orbit')}_"
                   f"{m.group('date')}{m.group('ad')}")
            out.append({
                "pass_id": pid, "file": fn, "url": url + fn,
                "sat": sat_key, "sensor": sensor,
                "provider_tag": m.group("name"),
                "date": m.group("date"), "orbit": int(m.group("orbit")),
                "ad": m.group("ad"),
            })
    out.sort(key=lambda c: (c["date"], c["orbit"]), reverse=True)
    return out


def _unpack(ds, name):
    v = ds.variables[name]
    raw = v[:].astype("float64")
    out = raw * float(getattr(v, "scale_factor", 1.0)) + \
        float(getattr(v, "add_offset", 0.0))
    # -32768 is the declared _FillValue; -32767 (valid_min) is the file's
    # NO-RETRIEVAL sentinel (verified == number_ambiguities==0 cells) — both
    # must mask or the between-swath gap decodes as ~0 m/s "wind"
    out[raw <= -32767] = np.nan
    return out


def decode(path: str, meta: dict):
    """One UHR NetCDF -> pass dict (main-feed schema) + field raster inputs."""
    import netCDF4
    ds = netCDF4.Dataset(path, "r")
    ds.set_auto_maskandscale(False)
    try:
        lat = _unpack(ds, "lat")
        lon = _unpack(ds, "lon")
        spd = _unpack(ds, "speed")
        wdir = _unpack(ds, "dir")
        t = _unpack(ds, "time")
        land = _unpack(ds, "land")
        namb = ds.variables["number_ambiguities"][:].astype("int32")
    finally:
        ds.close()
    lon = np.where(lon > 180.0, lon - 360.0, lon)
    ok = (np.isfinite(lat) & np.isfinite(lon) & np.isfinite(spd)
          & np.isfinite(wdir) & (spd >= 0))
    ok &= namb > 0                  # the authoritative retrieval mask
    ok &= ~(np.nan_to_num(land, nan=1.0) > 0)
    n_ok = int(ok.sum())
    if n_ok < 200:
        raise RuntimeError(f"only {n_ok} valid ocean cells")

    kt_all = spd * MS_TO_KT
    tsec = np.where(ok & np.isfinite(t), t, np.nan)
    t0 = EPOCH + dt.timedelta(seconds=float(np.nanmin(tsec)))
    t1 = EPOCH + dt.timedelta(seconds=float(np.nanmax(tsec)))
    tm = EPOCH + dt.timedelta(seconds=float(np.nanmedian(tsec)))
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")   # noqa: E731

    # decimated barb cells (FROM-direction, like the operational feed)
    sl = (slice(None, None, STRIDE), slice(None, None, STRIDE))
    keep = ok[sl]
    la = np.round(lat[sl][keep], 2)
    lo = np.round(lon[sl][keep], 2)
    kt = np.round(kt_all[sl][keep], 1)
    frm = np.mod(wdir[sl][keep] + 180.0, 360.0)
    wvc = {"la": la.tolist(), "lo": lo.tolist(),
           "kt": kt.tolist(), "dir": [int(round(x)) % 360 for x in frm]}

    # centreline anchors (~per 40 rows) for the storm-overpass association
    path_anchors = []
    for r in range(0, lat.shape[0], 40):
        m = ok[r]
        if m.sum() < 5:
            continue
        ts = tsec[r][m]
        path_anchors.append({
            "lat": round(float(np.nanmean(lat[r][m])), 2),
            "lon": round(float(np.nanmean(lon[r][m])), 2),
            "t": iso(EPOCH + dt.timedelta(seconds=float(np.nanmedian(ts)))),
        })

    bbox = [round(float(np.nanmin(lo)), 2), round(float(np.nanmin(la)), 2),
            round(float(np.nanmax(lo)), 2), round(float(np.nanmax(la)), 2)]
    p = {
        "id": meta["pass_id"], "sensor": meta["sensor"], "sat": meta["sat"],
        "uhr": True, "provider_tag": meta["provider_tag"],
        "start_utc": iso(t0), "end_utc": iso(t1), "mid_utc": iso(tm),
        "bbox": bbox, "n_wvc": len(wvc["la"]),
        "max_kt": round(float(np.nanmax(kt_all[ok])), 1),
        "stride": STRIDE, "res_km": 2.3,
        "wvc": wvc, "path": path_anchors,
    }
    return p, (lat[ok], lon[ok], kt_all[ok])


def _hex_rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def render_field(lats, lons, kts):
    """Bin the ~2 km cells to a regular grid and colorize with the stepped
    high-contrast kt classes -> (lossless webp bytes, [W,S,E,N])."""
    from PIL import Image
    w0, e0 = float(lons.min()), float(lons.max())
    s0, n0 = float(lats.min()), float(lats.max())
    nx = max(8, int(math.ceil((e0 - w0) / FIELD_DEG)))
    ny = max(8, int(math.ceil((n0 - s0) / FIELD_DEG)))
    ix = np.clip(((lons - w0) / FIELD_DEG).astype(int), 0, nx - 1)
    iy = np.clip(((n0 - lats) / FIELD_DEG).astype(int), 0, ny - 1)
    flat = iy * nx + ix
    ssum = np.bincount(flat, weights=kts, minlength=ny * nx)
    cnt = np.bincount(flat, minlength=ny * nx)
    with np.errstate(invalid="ignore"):
        grid = (ssum / cnt).reshape(ny, nx)
    have = cnt.reshape(ny, nx) > 0
    # pinhole fill: an empty bin flanked by >=4 filled neighbors takes their
    # mean (one pass; swath edges stay honest)
    pad_have = np.pad(have, 1)
    pad_grid = np.pad(np.where(have, grid, 0.0), 1)
    nsum = np.zeros_like(grid)
    ncnt = np.zeros_like(grid)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            if dy == 1 and dx == 1:
                continue
            nsum += pad_grid[dy:dy + ny, dx:dx + nx]
            ncnt += pad_have[dy:dy + ny, dx:dx + nx]
    fill = (~have) & (ncnt >= 4)
    grid[fill] = nsum[fill] / ncnt[fill]
    have |= fill

    rgba = np.zeros((ny, nx, 4), dtype=np.uint8)
    gv = np.where(have, grid, -1.0)
    for lo_kt, hexc in KT_SCALE_HC:
        m = gv >= lo_kt
        r, g, b = _hex_rgb(hexc)
        rgba[m] = (r, g, b, 255)
    rgba[~have] = 0
    im = Image.fromarray(rgba, "RGBA")
    buf = io.BytesIO()
    im.save(buf, format="WEBP", lossless=True)
    return buf.getvalue(), [round(w0, 3), round(s0, 3),
                            round(e0, 3), round(n0, 3)]


class LocalStore:
    def __init__(self, root):
        self.root = Path(root)

    def put(self, key, data, cache, ctype):
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get_json(self, key):
        p = self.root / key
        return json.loads(p.read_text()) if p.exists() else None

    def delete(self, key):
        p = self.root / key
        if p.exists():
            p.unlink()


class R2Store:
    def __init__(self):
        import boto3
        self.bucket = os.environ.get("R2_BUCKET", "triple-a-tropics-media")
        self.c = boto3.client(
            "s3", endpoint_url=os.environ["R2_ENDPOINT"],
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"])

    def put(self, key, data, cache, ctype):
        if tat_killswitch is not None and not tat_killswitch.writes_allowed(key):
            return                       # dropped (the switch logs it)
        self.c.put_object(Bucket=self.bucket, Key=key, Body=data,
                          CacheControl=cache, ContentType=ctype)

    def get_json(self, key):
        try:
            r = self.c.get_object(Bucket=self.bucket, Key=key)
            return json.loads(r["Body"].read())
        except Exception as e:
            code = str(getattr(e, "response", {}).get("Error", {}).get("Code", ""))
            if code in ("NoSuchKey", "NotFound", "404"):
                return None
            # transient read failure must fail the run — "no manifest" would
            # collapse the rolling window (the metar/mrms discipline)
            raise RuntimeError(f"manifest read failed ({key}): {e}")

    def delete(self, key):
        self.c.delete_object(Bucket=self.bucket, Key=key)


def _manifest_entry(p, field_bounds):
    return {
        "id": p["id"], "file": f"{p['id']}.json", "sensor": p["sensor"],
        "sat": p["sat"], "uhr": True, "provider_tag": p["provider_tag"],
        "start_utc": p["start_utc"], "end_utc": p["end_utc"],
        "mid_utc": p["mid_utc"], "bbox": p["bbox"], "n_wvc": p["n_wvc"],
        "max_kt": p["max_kt"],
        "field": {"file": f"{p['id']}.webp", "bounds": field_bounds},
        "storms": p.get("storms", []),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="local:/tmp/tat-uhr")
    ap.add_argument("--max-new", type=int, default=4,
                    help="downloads per run (files are ~110 MB; the 30-min "
                         "cadence catches up across runs)")
    ap.add_argument("--window-hours", type=float, default=WINDOW_H)
    args = ap.parse_args()
    store = R2Store() if args.store == "r2" else \
        LocalStore(args.store.split(":", 1)[1])

    from ascatobs import storms as storms_mod
    now = dt.datetime.now(UTC)
    base = "ascat/uhr"
    prior = store.get_json(f"{base}/manifest.json") or {}
    known = {e["id"]: e for e in prior.get("passes", [])}

    cands = [c for c in list_candidates(now, args.window_hours)
             if c["pass_id"] not in known]
    todo = cands[:args.max_new]
    if len(cands) > len(todo):
        print(f"[uhr] {len(cands)} new files, taking {len(todo)} this run "
              f"(the next tick continues)")

    try:
        active = storms_mod.active_storms()
    except Exception as e:                                  # noqa: BLE001
        print(f"[uhr] storm feed unavailable ({e}) — passes untagged this run")
        active = []

    new_entries = []
    for c in todo:
        try:
            raw = _http(c["url"], timeout=300)
            with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
                f.write(raw)
                ncpath = f.name
            try:
                p, field_pts = decode(ncpath, c)
            finally:
                os.unlink(ncpath)
            webp, fbounds = render_field(*field_pts)
            p["storms"] = storms_mod.associate(
                active, p["wvc"]["la"], p["wvc"]["lo"], p["path"])
            p["field"] = {"file": f"{p['id']}.webp", "bounds": fbounds}
            store.put(f"{base}/{p['id']}.webp", webp,
                      CACHE_IMMUTABLE, "image/webp")
            body = json.dumps(p, separators=(",", ":")).encode()
            store.put(f"{base}/{p['id']}.json", body,
                      CACHE_IMMUTABLE, "application/json")
            new_entries.append(_manifest_entry(p, fbounds))
            print(f"[uhr] {p['id']}  {p['n_wvc']} cells  max {p['max_kt']} kt"
                  f"  field {len(webp)//1024} KB  "
                  f"storms {[s['name'] for s in p['storms']]}")
        except Exception as e:                              # noqa: BLE001
            print(f"[uhr] {c['file']}: {e} — skipped this run")

    # merged window: prior + new, newest first, pruned past the window
    cutoff = now - dt.timedelta(hours=args.window_hours)
    merged = list(known.values()) + new_entries
    fresh, rolled = [], []
    for e in merged:
        ts = e.get("mid_utc") or e.get("start_utc") or ""
        try:
            t = dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except ValueError:
            t = now
        (fresh if t >= cutoff else rolled).append(e)
    fresh.sort(key=lambda e: e.get("start_utc", ""), reverse=True)
    if not fresh and prior.get("passes"):
        # quiet tropics: an empty window replaces a stale one honestly, but a
        # LISTING outage must not wipe a good manifest
        if not cands and not todo and new_entries == []:
            print("[uhr] no candidates and empty window — keeping prior manifest")
            return 0
    prune_now = [pid for pid in prior.get("prune_next", [])
                 if pid not in {e["id"] for e in fresh}]
    manifest = {
        "schema_version": 1,
        "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "NOAA/STAR Ultra-High-Resolution ASCAT winds (2 km-class)",
        "credit": "NOAA/STAR · EUMETSAT Metop-B/C ASCAT",
        "disclosure": "Experimental 2 km-class research winds; rain and "
                      "high-wind conditions can bias speeds. Storm cuts are "
                      "provider-selected.",
        "window_hours": args.window_hours,
        "uhr": True,
        "passes": fresh,
        "prune_next": [e["id"] for e in rolled],
    }
    store.put(f"{base}/manifest.json",
              json.dumps(manifest, separators=(",", ":")).encode(),
              CACHE_MANIFEST, "application/json")
    for pid in prune_now:
        for ext in (".json", ".webp"):
            try:
                store.delete(f"{base}/{pid}{ext}")
            except Exception:                               # noqa: BLE001
                pass
    print(f"[uhr] manifest: {len(fresh)} passes ({len(new_entries)} new), "
          f"pruned {len(prune_now)}, queued {len(rolled)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
