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

# The R2 write kill switch (tat_killswitch.py at the repo root, mirrored in
# tsr). Optional on purpose: a missing or broken module means "allowed" --
# the switch can only ever STOP writes, never break a lane. Deletes are
# never guarded (free on R2; prune must keep reducing storage).
try:
    import tat_killswitch
except Exception:  # noqa: BLE001
    tat_killswitch = None

BUCKET = "https://noaa-mrms-pds.s3.amazonaws.com"
PRODUCT = "CONUS/MergedReflectivityQCComposite_00.50"
UTC = dt.timezone.utc

# output grid: NATIVE ~0.01 deg over the MRMS CONUS domain — emitting at a
# coarser grid read as chunky cells at normal zoom no matter how the field
# was smoothed; modern-radar smoothness needs native width + smooth
# resampling at every later stage (bicubic warp here, linear GPU sampling
# in the client)
OUT_W, OUT_H = 7000, 3500
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
    from scipy.ndimage import gaussian_filter
    from scipy.interpolate import interp1d
    field = np.where(np.isfinite(dbz) & (dbz > -900), dbz, FLOOR_DBZ)
    field = np.maximum(field, FLOOR_DBZ).astype(np.float32)
    # MAX-PRESERVING smoothing: plain gaussian at display-friendly sigma
    # measurably erased small intense cores against the floor fill (a
    # 1-cell 65 dBZ echo smoothed below the 10 dBZ cutoff = invisible).
    # Blend the smoothed field back via max: surroundings pick up the
    # smooth gradient, every native pixel keeps at least its true value.
    field = np.maximum(gaussian_filter(field, sigma=0.8, mode="nearest"), field)

    lat_n, lat_s = float(lat1.max()), float(lat1.min())
    lon_w, lon_e = float(lon1.min()), float(lon1.max())

    def merc_y(lat):
        return np.log(np.tan(np.pi / 4.0 + np.radians(lat) / 2.0))

    y = np.linspace(merc_y(lat_n), merc_y(lat_s), OUT_H)
    lat_out = np.degrees(2.0 * (np.arctan(np.exp(y)) - np.pi / 4.0))
    # fractional input rows: lat1 descends (north row 0); lons are uniform
    rows = (lat1[0] - lat_out) / (lat1[0] - lat1[-1]) * (len(lat1) - 1)
    rows = np.clip(rows, 0.0, len(lat1) - 1.0)
    # BICUBIC along the only resampled axis. Columns are identity at native
    # width, so the warp is a row-wise cubic — vastly cheaper than a full
    # 2-D bicubic and exactly as smooth. Clip kills cubic overshoot at
    # sharp echo edges (ringing above cores / below the floor).
    fmax = float(field.max())
    out = interp1d(np.arange(field.shape[0], dtype=np.float64), field,
                   kind="cubic", axis=0, copy=False,
                   assume_sorted=True)(rows).astype(np.float32)
    if OUT_W != field.shape[1]:
        cols = np.linspace(0.0, field.shape[1] - 1.0, OUT_W)
        out = interp1d(np.arange(out.shape[1], dtype=np.float64), out,
                       kind="cubic", axis=1, copy=False,
                       assume_sorted=True)(cols).astype(np.float32)
    out = np.clip(out, FLOOR_DBZ, fmax)
    return out, [lon_w, lat_s, lon_e, lat_n]


def _smooth_ramp():
    """Continuous colormap from the house TAT-radar.pal anchors: the SAME
    color identity (every solidcolor stop keeps its exact color at its
    exact dBZ) but linearly interpolated BETWEEN stops, so gradients read
    as smooth continuous radar instead of hard 5-dBZ blocks. Returns
    (cmap, vmin, vmax)."""
    from matplotlib.colors import LinearSegmentedColormap
    stops = []
    for ln in (HERE / "assets" / "TAT-radar.pal").read_text().splitlines():
        ln = ln.strip()
        if not ln.startswith("solidcolor:"):
            continue
        parts = ln.split(":", 1)[1].split()
        stops.append((float(parts[0]),
                      (int(parts[1]) / 255, int(parts[2]) / 255, int(parts[3]) / 255)))
    stops.sort()
    vmin, vmax = stops[0][0], 75.0        # >=75 is the palette's white cap
    stops = [s for s in stops if s[0] <= vmax]
    pos = [(d - vmin) / (vmax - vmin) for d, _ in stops]
    cmap = LinearSegmentedColormap.from_list(
        "tat_radar_smooth", list(zip(pos, [c for _, c in stops])))
    return cmap, vmin, vmax


def colorize(dbz: np.ndarray) -> np.ndarray:
    """RGBA via the smooth TAT-radar ramp (continuous; sub-cutoff clear)."""
    cmap, vmin, vmax = _smooth_ramp()
    t = np.clip((dbz - vmin) / (vmax - vmin), 0.0, 1.0)
    rgba = (cmap(t) * 255).astype(np.uint8)
    rgba[dbz < MIN_DBZ] = 0                          # fully transparent
    # feathered edge: fade alpha over the first 3 dBZ above the cutoff so
    # the echo boundary is a soft edge, not a hard mask line
    edge = (dbz >= MIN_DBZ) & (dbz < MIN_DBZ + 3.0)
    if edge.any():
        a = rgba[..., 3].astype(np.float32)
        a[edge] *= ((dbz[edge] - MIN_DBZ) / 3.0)
        rgba[..., 3] = a.astype(np.uint8)
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
        if tat_killswitch is not None and not tat_killswitch.writes_allowed(key):
            return                       # dropped (the switch logs it)
        self.c.put_object(Bucket=self.bucket, Key=key, Body=data,
                          CacheControl=cache, ContentType=ctype)

    def get_json(self, key: str):
        try:
            r = self.c.get_object(Bucket=self.bucket, Key=key)
            return json.loads(r["Body"].read())
        except Exception as e:
            code = str(getattr(e, "response", {}).get("Error", {}).get("Code", ""))
            if code in ("NoSuchKey", "NotFound", "404"):
                return None            # genuinely no manifest yet
            # a TRANSIENT read failure must fail the run — treating it as
            # "no manifest" silently collapsed the rolling series to one
            # frame and orphaned every pruned-but-listed object
            # (review-caught); failing keeps the last good manifest live.
            raise RuntimeError(f"manifest read failed ({key}): {e}")

    def delete(self, key: str):
        self.c.delete_object(Bucket=self.bucket, Key=key)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="local:/tmp/tat-mrms")
    ap.add_argument("--prefix", default="", help="key prefix (e.g. shadow)")
    ap.add_argument("--keep", type=int, default=30)   # ~5 h at the 10-min
    # cadence — must outrun the deepest sat loop (48 x 5-min conus = 4 h)
    # or the time-locked join blanks the loop's old tail
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
    # ANIMATION VARIANT: a half-res copy per scan. A 7000x3500 RGBA texture
    # is ~98 MB on the GPU — re-uploading it every loop advance is the
    # visible radar stall during playback; the half-res (~24 MB) variant
    # animates fluidly and the full-res swaps back in when paused/zoomed.
    small = encode_webp(out[::2, ::2])
    store.put(f"{base}/{stamp}.s.webp", small, CACHE_IMMUTABLE, "image/webp")
    times = [t for t in manifest.get("times", []) if t != stamp]
    times.append(stamp)
    # RENDER-EPOCH floor: frames emitted before the smooth-render pipeline
    # (native 7000x3500 grid, bicubic, continuous ramp) landed are visually
    # blocky AND immutable-cached — evict them from the series instead of
    # letting the time-lock keep joining them for another retention cycle.
    # Bump the epoch on any future render-quality change; safe to remove
    # once the window has rolled past it.
    RENDER_EPOCH = "20260718T192500Z"
    epoch_dropped = [t for t in times if t < RENDER_EPOCH]
    times = [t for t in times if t >= RENDER_EPOCH]
    times = sorted(times)
    rolled = times[:-args.keep] if len(times) > args.keep else []
    times = times[-args.keep:]
    rolled += epoch_dropped        # epoch-evicted frames prune next run too
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
        "image_small": base + "/{t}.s.webp",
        # the first stamp that HAS a small variant — the client must not
        # request smalls for older frames still in the rolling series
        "small_since": manifest.get("small_since") or stamp,
        "times": times, "latest": stamp,
        "prune_next": rolled,
        "bounds": bounds, "projection": "webmercator",
        "units": "dBZ", "palette": "TAT-radar.pal",
        "source": "NOAA MRMS MergedReflectivityQCComposite (noaa-mrms-pds)",
        "as_of": dt.datetime.now(UTC).isoformat(timespec="seconds"),
    }
    store.put(mkey, json.dumps(manifest).encode(), CACHE_MANIFEST, "application/json")
    for t in prune_now:
        for ext in (".webp", ".s.webp"):
            try:
                store.delete(f"{base}/{t}{ext}")
            except Exception:
                pass
    print(f"[mrms] wrote {base}/{stamp}.webp + manifest ({len(times)} times, "
          f"pruned {len(prune_now)}, queued {len(rolled)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
