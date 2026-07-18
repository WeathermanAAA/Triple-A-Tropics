#!/usr/bin/env python3
"""MRMS radar overlay for the Satellite Explorer (tester item #11).

Ingests NOAA's FREE public MRMS MergedReflectivityQCComposite (AWS Open Data
``noaa-mrms-pds``, anonymous HTTPS — no creds), colorizes it with the house
``assets/TAT-radar.pal`` (the same discrete-dBZ palette the HAFS sim-radar
uses, via hafs_render.load_pal_cmap — one palette, every radar product), and
writes a WEB-MERCATOR-warped RGBA WebP + a small manifest for the explorer's
image-source overlay layer:

    radar/mrms/conus/{YYYYMMDDTHHMMSSZ}.webp   (immutable)
    radar/mrms/conus/latest_times.json         (max-age 30; latest + times)

Web-mercator warp matters: MapLibre image sources are positioned by CORNER
coordinates in mercator space, so an equirectangular (plate carrée) image
would misregister by tens of km at CONUS mid-latitudes. The MRMS grid is
regular lat/lon; the warp is therefore a pure per-ROW resample (each output
mercator row samples one input lat row — nearest neighbor, no smearing of
the discrete dBZ bins).

The grid is downsampled to ~0.02° (3500×1750 → the emitted PNG stays a few
hundred KB of mostly-transparent WebP) — 2 km class, matching the tile
imagery's native resolution at CONUS zooms. Sub-10-dBZ is transparent (the
palette's `under` color), exactly like the HAFS sim-radar rendering.

Modes:
    --store r2      write to R2 (needs R2_ENDPOINT/R2_ACCESS_KEY_ID/
                    R2_SECRET_ACCESS_KEY env — same secrets the s2 emitters
                    use; the update-mrms.yml workflow provides them)
    --store local:/path   write to a directory (local verification)

Keeps ``--keep`` frames listed (default 12 = ~2 h at the workflow cadence);
older R2 objects are left for lifecycle TTL, mirroring the s2 emitters.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import io
import json
import os
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "hafs_render"))

BUCKET = "https://noaa-mrms-pds.s3.amazonaws.com"
PRODUCT = "CONUS/MergedReflectivityQCComposite_00.50"
UTC = dt.timezone.utc

# output grid: ~0.02 deg (2 km class) over the MRMS CONUS domain
OUT_W, OUT_H = 3500, 1750
MIN_DBZ = 10.0          # below = transparent (palette 'under')
CACHE_IMMUTABLE = "public, max-age=31536000, immutable"
CACHE_MANIFEST = "public, max-age=30"


def _http(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "triple-a-tropics-mrms"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def newest_key(now: dt.datetime | None = None) -> str:
    """Newest grib2.gz key via anonymous prefix listing (hour-narrowed so the
    listing stays one page; falls back through the previous hours/day)."""
    now = now or dt.datetime.now(UTC)
    for back in range(6):
        t = now - dt.timedelta(hours=back)
        prefix = (f"{PRODUCT}/{t:%Y%m%d}/MRMS_MergedReflectivityQCComposite_"
                  f"00.50_{t:%Y%m%d}-{t:%H}")
        xml = _http(f"{BUCKET}/?list-type=2&prefix={prefix}&max-keys=1000").decode()
        keys = re.findall(r"<Key>([^<]+)</Key>", xml)
        if keys:
            return sorted(keys)[-1]
    raise RuntimeError("no MRMS composite found in the last 6 hours")


def stamp_of(key: str) -> str:
    m = re.search(r"_(\d{8})-(\d{6})\.grib2", key)
    if not m:
        raise ValueError(f"unparseable MRMS key {key}")
    return f"{m.group(1)}T{m.group(2)}Z"


def load_grid(key: str):
    """(dbz 2-D float32, lat1 desc, lon1 asc) from the grib2.gz object."""
    import pygrib
    raw = gzip.decompress(_http(f"{BUCKET}/{key}", timeout=120))
    with tempfile.NamedTemporaryFile(suffix=".grib2") as f:
        f.write(raw)
        f.flush()
        with pygrib.open(f.name) as g:
            msg = g.message(1)
            dbz = msg.values.astype(np.float32)      # masked where missing
            lat1 = msg.distinctLatitudes.astype(np.float64)
            lon1 = msg.distinctLongitudes.astype(np.float64)
    if isinstance(dbz, np.ma.MaskedArray):
        dbz = dbz.filled(-999.0)
    lon1 = np.where(lon1 > 180.0, lon1 - 360.0, lon1)
    return dbz, lat1, lon1


FLOOR_DBZ = MIN_DBZ - 5.0    # echo-free fill: lets smoothing/bilinear fade
                             # edges naturally instead of -999 poisoning them


def smooth_warp_webmerc(dbz: np.ndarray, lat1: np.ndarray, lon1: np.ndarray):
    """Light Gaussian on the native FIELD, then a FIELD-SPACE bilinear
    resample onto the web-mercator output grid (rows: mercator-y -> input
    lat; cols: linear). Smoothing + interpolating the dBZ field FIRST and
    colorizing at output resolution keeps the discrete TAT palette's bin
    boundaries as smooth contours instead of 2-4 km blocks (the tester
    "blocky" report — nearest-resampled pre-colorized pixels), while a
    small sigma (~1.5 native cells ≈ 1.5 km) preserves core structure.
    Returns (dbz_out, bounds W,S,E,N)."""
    from scipy.ndimage import gaussian_filter, map_coordinates
    field = np.where(np.isfinite(dbz) & (dbz > -900), dbz, FLOOR_DBZ)
    field = np.maximum(field, FLOOR_DBZ).astype(np.float32)
    # MAX-PRESERVING smoothing: plain gaussian at display-friendly sigma
    # measurably erased small intense cores against the floor fill (a
    # 1-cell 65 dBZ echo smoothed below the 10 dBZ cutoff = invisible; a
    # ~3 km 60 dBZ core displayed 4+ bins weak). Blend the smoothed field
    # back with the original via max: surroundings pick up the smooth
    # gradient, every native pixel keeps at least its true value — cores
    # stay honest, edges stop being blocky. sigma 0.8 is ample
    # anti-aliasing for the 2x downsample.
    field = np.maximum(gaussian_filter(field, sigma=0.8, mode="nearest"), field)

    lat_n, lat_s = float(lat1.max()), float(lat1.min())
    lon_w, lon_e = float(lon1.min()), float(lon1.max())

    def merc_y(lat):
        return np.log(np.tan(np.pi / 4.0 + np.radians(lat) / 2.0))

    y = np.linspace(merc_y(lat_n), merc_y(lat_s), OUT_H)
    lat_out = np.degrees(2.0 * (np.arctan(np.exp(y)) - np.pi / 4.0))
    # fractional input coords: lat1 descends (north row 0); lons are uniform
    rows = (lat1[0] - lat_out) / (lat1[0] - lat1[-1]) * (len(lat1) - 1)
    cols = np.linspace(0.0, dbz.shape[1] - 1.0, OUT_W)
    rr, cc = np.meshgrid(rows, cols, indexing="ij")
    out = map_coordinates(field, [rr, cc], order=1, mode="nearest")
    return out.astype(np.float32), [lon_w, lat_s, lon_e, lat_n]


def colorize(dbz: np.ndarray) -> np.ndarray:
    """RGBA via the house TAT-radar.pal (discrete 5-dBZ bins, HAFS parity)."""
    from hafs_render.hafs_plot import _refl_pal
    pal = _refl_pal()
    rgba = pal.cmap(pal.norm(dbz))                  # float 0..1, HxWx4
    rgba = (rgba * 255).astype(np.uint8)
    rgba[dbz < MIN_DBZ] = 0                          # fully transparent
    return rgba


def encode_webp(rgba: np.ndarray) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    # q90 like the s2 tiles: hard colortable edges fall apart below that
    Image.fromarray(rgba, "RGBA").save(buf, "WEBP", quality=90, method=6)
    return buf.getvalue()


class LocalStore:
    def __init__(self, root: str):
        self.root = Path(root)

    def put(self, key: str, data: bytes, cache: str, ctype: str):
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get_json(self, key: str):
        p = self.root / key
        return json.loads(p.read_text()) if p.exists() else None

    def delete(self, key: str):
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

    def put(self, key: str, data: bytes, cache: str, ctype: str):
        self.c.put_object(Bucket=self.bucket, Key=key, Body=data,
                          CacheControl=cache, ContentType=ctype)

    def get_json(self, key: str):
        try:
            r = self.c.get_object(Bucket=self.bucket, Key=key)
            return json.loads(r["Body"].read())
        except Exception:
            return None

    def delete(self, key: str):
        self.c.delete_object(Bucket=self.bucket, Key=key)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="local:/tmp/tat-mrms")
    ap.add_argument("--prefix", default="", help="key prefix (e.g. shadow)")
    ap.add_argument("--keep", type=int, default=18)
    args = ap.parse_args()

    store = R2Store() if args.store == "r2" else LocalStore(args.store.split(":", 1)[1])
    base = (args.prefix.rstrip("/") + "/" if args.prefix else "") + "radar/mrms/conus"

    key = newest_key()
    stamp = stamp_of(key)
    mkey = f"{base}/latest_times.json"
    manifest = store.get_json(mkey) or {}
    if manifest.get("latest") == stamp:
        print(f"[mrms] {stamp} already current — no-op")
        return 0

    print(f"[mrms] source {key}")
    dbz, lat1, lon1 = load_grid(key)
    print(f"[mrms] grid {dbz.shape[1]}x{dbz.shape[0]}  "
          f"lat {lat1.min():.2f}..{lat1.max():.2f} lon {lon1.min():.2f}..{lon1.max():.2f}  "
          f"max {float(np.nanmax(dbz)):.0f} dBZ")
    dbz_out, bounds = smooth_warp_webmerc(dbz, lat1, lon1)
    out = colorize(dbz_out)
    webp = encode_webp(out)
    cover = float((out[..., 3] > 0).mean())
    print(f"[mrms] webp {len(webp)//1024} KB  echo coverage {cover:.1%}  bounds {bounds}")
    if cover > 0.90:
        # >90% of CONUS painted = a decode/QC failure, not weather; refuse to
        # publish garbage over every pane (honest-gate ethos)
        raise RuntimeError(f"implausible echo coverage {cover:.0%} — refusing to publish")

    store.put(f"{base}/{stamp}.webp", webp, CACHE_IMMUTABLE, "image/webp")
    times = [t for t in manifest.get("times", []) if t != stamp]
    times.append(stamp)
    times = sorted(times)
    rolled = times[:-args.keep] if len(times) > args.keep else []
    times = times[-args.keep:]
    # DEFERRED prune: frames that rolled off THIS run are only queued
    # (prune_next); the actual deletes happen on the NEXT run, AFTER that
    # run publishes its manifest. Deleting immediately raced live readers:
    # the old manifest (still at origin until the put below, and cached in
    # clients up to a poll tick) listed the frame being deleted — a
    # time-locked pane then 404'd and showed blank radar under a badge
    # claiming the scan. A publish failure after deletes was worse (a full
    # cadence of 404s). One-cadence deferral means nothing referenced by
    # the current OR previous manifest is ever deleted.
    prune_now = [t for t in manifest.get("prune_next", []) if t not in times]
    manifest = {
        "product": "mrms/conus/reflectivity",
        "image": base + "/{t}.webp",
        "times": times, "latest": stamp,
        "prune_next": rolled,
        "bounds": bounds, "projection": "webmercator",
        "units": "dBZ", "palette": "TAT-radar.pal",
        "source": "NOAA MRMS MergedReflectivityQCComposite (noaa-mrms-pds)",
        "as_of": dt.datetime.now(UTC).isoformat(timespec="seconds"),
    }
    store.put(mkey, json.dumps(manifest).encode(), CACHE_MANIFEST, "application/json")
    for t in prune_now:
        try:
            store.delete(f"{base}/{t}.webp")
        except Exception:
            pass
    print(f"[mrms] wrote {base}/{stamp}.webp + manifest ({len(times)} times, "
          f"pruned {len(prune_now)}, queued {len(rolled)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
