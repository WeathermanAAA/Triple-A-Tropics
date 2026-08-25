#!/usr/bin/env python3
"""MIMIC-TPW2 moisture overlay feed for the Satellite Explorer.

Morphed total-precipitable-water composite (all-microwave-sensor blend,
model-advected), courtesy CIMSS/SSEC — anonymous HTTPS, no creds:

    https://bin.ssec.wisc.edu/pub/mtpw2/data/{YYYYMM}/comp{YYYYMMDD}.{HHMMSS}.nc

Hourly global 0.25 deg. LIVE layer only — no archive, no per-storm index.
Emits the explorer's rolling-series contract (the MRMS/radar shape, image
frames instead of JSON):

    env/tpw/{stamp}.webp          (immutable web-mercator-warped frames)
    env/tpw/latest_times.json     (manifest: times/latest/frame/bounds/cbar)
    env/tpw/cbar.png              (the vertical mm colorbar, written once/run)

NEVER-MISS ingest: the source publishes hourly but the mirror can lag or
stall, so each tick LISTS the month directories (current + previous) and
ingests every listed file newer than the manifest watermark — presence-
gated, idempotent by stamp, backfilling holes; nothing assumes on-the-hour.
Display honesty lives client-side: the toggle only enables when the newest
frame is genuinely fresh, so a stalled upstream shows a disabled layer,
never a stale one presented as live.

Render: the 0.25 deg grid is upsampled and row-warped onto web-mercator
(rows: mercator-y -> source latitude, the radar-overlay discipline; columns
are uniform), colorized with the standard operational TPW enhancement
(continuous interpolation between the recognized mm anchors), TPW <= 1 mm
transparent. Values are millimeters.

Modes: --store r2 (box poller) | --store local:/path (verification).
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
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

BASE = "https://bin.ssec.wisc.edu/pub/mtpw2/data"
UTC = dt.timezone.utc
KEEP = 36                     # rolling frames (~36 h at the hourly cadence)
LAT_CLAMP = 75.0              # web-mercator sanity; the explorer world stops ~60
OUT_W = 2880                  # 2x the native 0.25 deg columns (smooth ramp)
CACHE_MANIFEST = "public, max-age=120"
CACHE_IMMUTABLE = "public, max-age=31536000, immutable"

# the standard operational TPW enhancement (mm -> color), continuously
# interpolated between anchors; dry browns -> greens -> blues -> purples
TPW_RAMP = [
    (5,  (59, 35, 19)), (12, (122, 74, 30)), (18, (185, 128, 56)),
    (24, (217, 194, 107)), (30, (143, 191, 77)), (36, (47, 158, 79)),
    (42, (31, 184, 165)), (48, (46, 134, 209)), (54, (75, 79, 209)),
    (60, (139, 63, 209)), (66, (209, 63, 184)), (72, (255, 154, 213)),
    (78, (255, 255, 255)),
]


def _http(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": "triple-a-tropics-tpw (contact: site owner)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def list_candidates(now: dt.datetime):
    """comp files across the current + previous month dirs, newest first."""
    out = []
    months = {now.strftime("%Y%m"),
              (now.replace(day=1) - dt.timedelta(days=1)).strftime("%Y%m")}
    for ym in sorted(months, reverse=True):
        try:
            html = _http(f"{BASE}/{ym}/", timeout=60).decode("utf-8", "replace")
        except Exception as e:                              # noqa: BLE001
            print(f"[tpw] listing {ym} unavailable: {e}")
            continue
        for fn in set(re.findall(r'comp(\d{8})\.(\d{6})\.nc', html)):
            stamp = f"{fn[0]}T{fn[1]}Z"
            out.append({"stamp": stamp, "url": f"{BASE}/{ym}/comp{fn[0]}.{fn[1]}.nc"})
    out.sort(key=lambda c: c["stamp"], reverse=True)
    return out


def _axis_lerp(arr: np.ndarray, idx: np.ndarray, axis: int) -> np.ndarray:
    """Vectorized linear resample of `arr` at float indices along `axis`.
    NaN (swath gaps) propagates and ends up transparent in the ramp."""
    i0 = np.clip(np.floor(idx).astype(np.int64), 0, arr.shape[axis] - 2)
    f = idx - i0
    a = np.take(arr, i0, axis=axis)
    b = np.take(arr, i0 + 1, axis=axis)
    shape = [1] * arr.ndim
    shape[axis] = len(idx)
    f = f.reshape(shape)
    return a * (1.0 - f) + b * f


def _lerp_ramp(vals: np.ndarray) -> np.ndarray:
    """mm field -> RGBA uint8 via the continuous ramp; <=1 mm / gaps
    transparent."""
    vals = np.nan_to_num(vals, nan=-1.0)
    pts = np.array([p[0] for p in TPW_RAMP], dtype=np.float64)
    cols = np.array([p[1] for p in TPW_RAMP], dtype=np.float64)
    v = np.clip(vals, pts[0], pts[-1])
    idx = np.clip(np.searchsorted(pts, v) - 1, 0, len(pts) - 2)
    f = (v - pts[idx]) / (pts[idx + 1] - pts[idx])
    rgb = cols[idx] + (cols[idx + 1] - cols[idx]) * f[..., None]
    out = np.zeros(vals.shape + (4,), dtype=np.uint8)
    out[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out[..., 3] = np.where(vals > 1.0, 255, 0).astype(np.uint8)
    return out


def render_frame(nc_path: str):
    """One comp nc -> (webp bytes, [W,S,E,N])."""
    import netCDF4
    ds = netCDF4.Dataset(nc_path)
    try:
        tpw = np.array(ds.variables["tpwGrid"][:], dtype=np.float32)
        lat = np.array(ds.variables["latArr"][:], dtype=np.float64)
        lon = np.array(ds.variables["lonArr"][:], dtype=np.float64)
    finally:
        ds.close()
    if lat[0] > lat[-1]:                      # normalize to south->north
        lat = lat[::-1]
        tpw = tpw[::-1]
    keep = (lat >= -LAT_CLAMP) & (lat <= LAT_CLAMP)
    lat = lat[keep]
    tpw = tpw[keep]
    s, n = float(lat[0]), float(lat[-1])
    w, e = float(lon[0]), float(lon[-1])

    def merc_y(la):
        return np.log(np.tan(np.pi / 4.0 + np.radians(la) / 2.0))

    out_h = int(OUT_W * (merc_y(n) - merc_y(s)) / np.radians(e - w + 0.25))
    y = np.linspace(merc_y(n), merc_y(s), out_h)
    lat_out = np.degrees(2.0 * (np.arctan(np.exp(y)) - np.pi / 4.0))
    rows = np.interp(lat_out, lat, np.arange(len(lat), dtype=np.float64))
    # row-warp (mercator-y -> source lat), then column upsample; linear both
    # ways — TPW is a smooth field, and the ramp interpolation happens after
    warped = _axis_lerp(tpw.astype(np.float64), rows, axis=0)
    warped = _axis_lerp(warped, np.linspace(0.0, tpw.shape[1] - 1.0, OUT_W),
                        axis=1)
    rgba = _lerp_ramp(warped)
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="WEBP", quality=85, method=5)
    return buf.getvalue(), [w, s, e + 0.25, n]


def render_cbar() -> bytes:
    """Vertical mm colorbar for the pane's right side."""
    from PIL import Image, ImageDraw
    H, W = 260, 46
    im = Image.new("RGBA", (W, H), (10, 13, 18, 230))
    dr = ImageDraw.Draw(im)
    lo, hi = TPW_RAMP[0][0], TPW_RAMP[-1][0]
    for py in range(10, H - 10):
        v = hi - (py - 10) / (H - 20) * (hi - lo)
        c = _lerp_ramp(np.array([[v]]))[0, 0]
        dr.line([(6, py), (20, py)], fill=tuple(int(x) for x in c[:3]) + (255,))
    for v in (10, 25, 40, 55, 70):
        py = 10 + int((hi - v) / (hi - lo) * (H - 20))
        dr.line([(20, py), (24, py)], fill=(223, 232, 242, 255))
        dr.text((27, py - 5), str(v), fill=(223, 232, 242, 255))
    dr.text((5, H - 9), "mm", fill=(142, 162, 189, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


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
            # transient read failure must fail the run (the house discipline)
            raise RuntimeError(f"manifest read failed ({key}): {e}")

    def delete(self, key):
        self.c.delete_object(Bucket=self.bucket, Key=key)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="local:/tmp/tat-tpw")
    ap.add_argument("--max-new", type=int, default=4)
    args = ap.parse_args()
    store = R2Store() if args.store == "r2" else \
        LocalStore(args.store.split(":", 1)[1])

    now = dt.datetime.now(UTC)
    base = "env/tpw"
    manifest = store.get_json(f"{base}/latest_times.json") or {}
    known = set(manifest.get("times", []))

    # never-miss WITHIN the rolling window: only the newest KEEP listed files
    # are ever useful (anything older rolls straight out of the manifest), so
    # backfill holes inside that window and ignore ancient history — without
    # this, a long-listing month would churn hundreds of doomed ingests
    cands = [c for c in list_candidates(now)[:KEEP]
             if c["stamp"] not in known]
    todo = cands[:args.max_new]
    if len(cands) > len(todo):
        print(f"[tpw] {len(cands)} new files listed, taking {len(todo)} "
              f"(next tick continues)")
    bounds = manifest.get("bounds")
    new = []
    for c in todo:
        try:
            raw = _http(c["url"], timeout=300)
            with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
                f.write(raw)
                p = f.name
            try:
                webp, bounds = render_frame(p)
            finally:
                os.unlink(p)
            store.put(f"{base}/{c['stamp']}.webp", webp,
                      CACHE_IMMUTABLE, "image/webp")
            new.append(c["stamp"])
            print(f"[tpw] {c['stamp']}  {len(webp)//1024} KB")
        except Exception as e:                              # noqa: BLE001
            print(f"[tpw] {c['stamp']}: {e} — skipped this run")
    if not new and manifest:
        print(f"[tpw] no new frames (newest listed "
              f"{cands[0]['stamp'] if cands else 'none'}; watermark holds)")
        return 0

    times = sorted(set(manifest.get("times", [])) | set(new))
    rolled = times[:-KEEP] if len(times) > KEEP else []
    prune_now = [t for t in manifest.get("prune_next", []) if t not in times]
    times = times[-KEEP:]
    store.put(f"{base}/cbar.png", render_cbar(), CACHE_MANIFEST, "image/png")
    mdoc = {
        "product": "env/tpw", "frame": base + "/{t}.webp",
        "times": times, "latest": times[-1] if times else None,
        "prune_next": rolled, "bounds": bounds,
        "units": "mm", "cbar": base + "/cbar.png",
        "as_of": now.isoformat(timespec="seconds"),
        "source": "MIMIC-TPW2, courtesy CIMSS/SSEC (Univ. of Wisconsin)",
    }
    store.put(f"{base}/latest_times.json",
              json.dumps(mdoc, separators=(",", ":")).encode(),
              CACHE_MANIFEST, "application/json")
    for t in prune_now:
        try:
            store.delete(f"{base}/{t}.webp")
        except Exception:                                    # noqa: BLE001
            pass
    print(f"[tpw] manifest: {len(times)} frames ({len(new)} new), "
          f"pruned {len(prune_now)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
