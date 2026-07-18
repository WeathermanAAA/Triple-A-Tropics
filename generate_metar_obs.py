#!/usr/bin/env python3
"""Surface-observation (METAR) overlay feed for the Satellite Explorer
(tester item #12).

Pulls the FREE public aviationweather.gov METAR cache (GLOBAL, no creds,
updated every minute), and emits ONE compact JSON the explorer's
station-plot canvas layer reads:

    obs/metar/latest.json   (max-age 60)

Schema (arrays keep it small — ~5k stations ≈ 250 KB raw, ~60 KB gzipped
at the CDN edge):

    { "as_of": iso, "count": n,
      "fields": ["id","lat","lon","t","td","slp","wdir","wspd","gust","rank","age_min"],
      "stations": [["KMIA", 25.79, -80.32, 29.4, 24.1, 1016.2, 120, 9, null, 5, 12], ...] }

rank = how complete the ob is (t/td/slp/wind present) — the client's
declutter keeps the highest-rank station per screen cell, so sparse-data
stations yield to full station models when space is tight. Stale obs
(>75 min) are dropped; the newest ob per station wins.

Modes: --store r2 (workflow) | --store local:/path (verification).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import io
import json
import os
import sys
import urllib.request
from pathlib import Path

SOURCE = "https://aviationweather.gov/data/cache/metars.cache.csv.gz"
UTC = dt.timezone.utc
MAX_AGE_MIN = 75
CACHE_MANIFEST = "public, max-age=60"


def _http(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": "triple-a-tropics-obs (contact: site owner)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _f(row, key):
    v = (row.get(key) or "").strip()
    if not v:
        return None
    try:
        return round(float(v), 1)
    except ValueError:
        return None


def parse_cache(raw: bytes, now: dt.datetime):
    """csv.gz -> station rows. The cache file carries a few preamble lines
    before the header row (``raw_text,station_id,...``) — skip to it."""
    text = gzip.decompress(raw).decode("utf-8", "replace")
    lines = text.splitlines()
    hdr = next((i for i, ln in enumerate(lines)
                if ln.startswith("raw_text,")), None)
    if hdr is None:
        raise RuntimeError("METAR cache: header row not found")
    rd = csv.DictReader(io.StringIO("\n".join(lines[hdr:])))
    best = {}
    for row in rd:
        sid = (row.get("station_id") or "").strip()
        lat, lon = _f(row, "latitude"), _f(row, "longitude")
        if not sid or lat is None or lon is None:
            continue
        try:
            t_obs = dt.datetime.fromisoformat(
                (row.get("observation_time") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        age_min = (now - t_obs).total_seconds() / 60.0
        if age_min < -10 or age_min > MAX_AGE_MIN:
            continue
        t, td = _f(row, "temp_c"), _f(row, "dewpoint_c")
        slp = _f(row, "sea_level_pressure_mb")
        wdir = _f(row, "wind_dir_degrees")
        wspd = _f(row, "wind_speed_kt")
        gust = _f(row, "wind_gust_kt")
        rank = sum(x is not None for x in (t, td, slp, wdir, wspd))
        ob = [sid, lat, lon, t, td, slp,
              int(wdir) if wdir is not None else None,
              int(wspd) if wspd is not None else None,
              int(gust) if gust is not None else None,
              rank, int(round(age_min))]
        cur = best.get(sid)
        if cur is None or ob[10] < cur[10]:      # newest ob per station
            best[sid] = ob
    return sorted(best.values(), key=lambda o: (-o[9], o[10]))


class LocalStore:
    def __init__(self, root):
        self.root = Path(root)

    def put(self, key, data, cache, ctype):
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


class R2Store:
    def __init__(self):
        import boto3
        self.bucket = os.environ.get("R2_BUCKET", "triple-a-tropics-media")
        self.c = boto3.client(
            "s3", endpoint_url=os.environ["R2_ENDPOINT"],
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"])

    def put(self, key, data, cache, ctype):
        self.c.put_object(Bucket=self.bucket, Key=key, Body=data,
                          CacheControl=cache, ContentType=ctype)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="local:/tmp/tat-metar")
    args = ap.parse_args()
    store = R2Store() if args.store == "r2" else LocalStore(args.store.split(":", 1)[1])

    now = dt.datetime.now(UTC)
    stations = parse_cache(_http(SOURCE), now)
    if len(stations) < 500:
        # a healthy global cycle carries thousands; a tiny parse is a source/
        # schema failure — refuse to overwrite a good feed with a broken one
        raise RuntimeError(f"only {len(stations)} stations parsed — refusing to publish")
    doc = {
        "as_of": now.isoformat(timespec="seconds"),
        "count": len(stations),
        "fields": ["id", "lat", "lon", "t", "td", "slp",
                   "wdir", "wspd", "gust", "rank", "age_min"],
        "source": "aviationweather.gov METAR cache",
        "stations": stations,
    }
    body = json.dumps(doc, separators=(",", ":")).encode()
    store.put("obs/metar/latest.json", body, CACHE_MANIFEST, "application/json")
    print(f"[metar] {len(stations)} stations  {len(body)//1024} KB  as_of {doc['as_of']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
