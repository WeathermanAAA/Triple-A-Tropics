#!/usr/bin/env python3
"""Surface-observation (METAR) overlay feed for the Satellite Explorer
(tester item #12).

Pulls the FREE public aviationweather.gov METAR cache (GLOBAL, no creds,
updated every minute), and emits a ROLLING TIMESTAMPED SERIES the
explorer's station-plot canvas layer time-locks to the playback clock:

    obs/metar/{YYYYMMDDTHHMMSSZ}.json   (immutable frames)
    obs/metar/latest_times.json         (manifest: times/latest/frame tmpl)
    obs/metar/latest.json               (legacy static-latest, kept in sync
                                         for deploy-order safety)

Schema (arrays keep it small — ~5k stations ≈ 250 KB raw, ~60 KB gzipped
at the CDN edge):

    { "as_of": iso, "count": n,
      "fields": ["id","lat","lon","t","td","slp","wdir","wspd","gust","rank","age_min","plat"],
      "stations": [["KMIA", 25.79, -80.32, 29.4, 24.1, 1016.2, 120, 9, null, 5, 12, 0], ...] }

plat: 0 = land METAR, 1 = moving VOS ship, 2 = buoy/C-MAN (marine platforms
come from the NDBC latest-obs + ship listings, same no-creds tier; the
client draws marine stations with a distinct symbol). Wind arrives m/s on
the marine side and is converted to kt so one barb painter serves all.

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

# The R2 write kill switch (tat_killswitch.py at the repo root, mirrored in
# tsr). Optional on purpose: a missing or broken module means "allowed" --
# the switch can only ever STOP writes, never break a lane. Deletes are
# never guarded (free on R2; prune must keep reducing storage).
try:
    import tat_killswitch
except Exception:  # noqa: BLE001
    tat_killswitch = None

SOURCE = "https://aviationweather.gov/data/cache/metars.cache.csv.gz"
# marine companions (same free/no-creds tier): fixed platforms (moored
# buoys + coastal C-MAN) and moving VOS ships. Both hourly-ish; both feed
# the same station-plot schema with a plat flag so the client can mark
# marine platforms with a distinct symbol.
MARINE_SOURCE = "https://www.ndbc.noaa.gov/data/latest_obs/latest_obs.txt"
SHIP_SOURCE = "https://www.ndbc.noaa.gov/ship_obs.php?uom=M&time=2"
MS_TO_KT = 1.94384
UTC = dt.timezone.utc
MAX_AGE_MIN = 75
CACHE_MANIFEST = "public, max-age=60"
# plat codes (fields[11]): 0 = land METAR, 1 = moving ship, 2 = buoy/C-MAN
PLAT_LAND, PLAT_SHIP, PLAT_BUOY = 0, 1, 2


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
              rank, int(round(age_min)), PLAT_LAND]
        cur = best.get(sid)
        if cur is None or ob[10] < cur[10]:      # newest ob per station
            best[sid] = ob
    return best


def _num(tok, scale=1.0):
    """NDBC token -> rounded number (MM / '-' are missing)."""
    if tok in ("MM", "-", "", None):
        return None
    try:
        return round(float(tok) * scale, 1)
    except ValueError:
        return None


def parse_marine(raw: bytes, now: dt.datetime):
    """NDBC latest_obs.txt -> fixed marine platforms (buoys + C-MAN).
    Columns: STN LAT LON YYYY MM DD hh mm WDIR WSPD GST WVHT DPD APD MWD
    PRES PTDY ATMP WTMP DEWP VIS TIDE (wind in m/s -> kt)."""
    best = {}
    for ln in raw.decode("utf-8", "replace").splitlines():
        if ln.startswith("#"):
            continue
        c = ln.split()
        if len(c) < 20:
            continue
        try:
            lat, lon = round(float(c[1]), 1), round(float(c[2]), 1)
            t_obs = dt.datetime(int(c[3]), int(c[4]), int(c[5]),
                                int(c[6]), int(c[7]), tzinfo=UTC)
        except ValueError:
            continue
        age_min = (now - t_obs).total_seconds() / 60.0
        if age_min < -10 or age_min > MAX_AGE_MIN:
            continue
        wdir = _num(c[8])
        wspd = _num(c[9], MS_TO_KT)
        gust = _num(c[10], MS_TO_KT)
        slp, atmp, dewp = _num(c[15]), _num(c[17]), _num(c[19])
        rank = sum(x is not None for x in (atmp, dewp, slp, wdir, wspd))
        if rank == 0:
            continue
        ob = [c[0], lat, lon, atmp, dewp, slp,
              int(wdir) if wdir is not None else None,
              int(round(wspd)) if wspd is not None else None,
              int(round(gust)) if gust is not None else None,
              rank, int(round(age_min)), PLAT_BUOY]
        cur = best.get(c[0])
        if cur is None or ob[10] < cur[10]:
            best[c[0]] = ob
    return best


def parse_ships(page: bytes, now: dt.datetime):
    """VOS ship listing -> moving-ship obs. The listing is an HTML page with
    one <pre class="wide-content"> block: ID HOUR LAT LON WDIR WSPD GST WVHT
    DPD PRES PTDY ATMP WTMP DEWP ... ('-' missing; metric units; anonymous
    ships all report as 'SHIP', so the dedup key includes position)."""
    import html as _html
    import re
    m = re.search(rb'<pre class="wide-content">(.*?)</pre>', page, re.S)
    if not m:
        raise RuntimeError("ship listing: data block not found")
    body = _html.unescape(re.sub(r"<[^>]+>", "", m.group(1).decode(
        "utf-8", "replace")))
    best = {}
    for ln in body.splitlines():
        c = ln.split()
        if len(c) < 14:
            continue
        try:                       # header/divider lines fail the parse
            hour = int(c[1])
            lat, lon = round(float(c[2]), 1), round(float(c[3]), 1)
        except ValueError:
            continue
        if not (0 <= hour <= 23 and -90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        t_obs = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if t_obs > now + dt.timedelta(minutes=10):
            t_obs -= dt.timedelta(days=1)
        age_min = (now - t_obs).total_seconds() / 60.0
        if age_min > MAX_AGE_MIN:
            continue
        wdir = _num(c[4])
        wspd = _num(c[5], MS_TO_KT)
        gust = _num(c[6], MS_TO_KT)
        slp, atmp, dewp = _num(c[9]), _num(c[11]), _num(c[13])
        rank = sum(x is not None for x in (atmp, dewp, slp, wdir, wspd))
        if rank == 0:
            continue
        ob = [c[0], lat, lon, atmp, dewp, slp,
              int(wdir) if wdir is not None else None,
              int(round(wspd)) if wspd is not None else None,
              int(round(gust)) if gust is not None else None,
              rank, int(round(age_min)), PLAT_SHIP]
        key = (c[0], lat, lon)
        cur = best.get(key)
        if cur is None or ob[10] < cur[10]:
            best[key] = ob
    return list(best.values())


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
                return None            # genuinely no manifest yet
            # a TRANSIENT read failure must fail the run — treating it as
            # "no manifest" silently collapsed the rolling series to one
            # frame and orphaned every pruned-but-listed object
            # (review-caught); failing keeps the last good manifest live.
            raise RuntimeError(f"manifest read failed ({key}): {e}")

    def delete(self, key):
        self.c.delete_object(Bucket=self.bucket, Key=key)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="local:/tmp/tat-metar")
    args = ap.parse_args()
    store = R2Store() if args.store == "r2" else LocalStore(args.store.split(":", 1)[1])

    now = dt.datetime.now(UTC)
    land = parse_cache(_http(SOURCE), now)
    if len(land) < 500:
        # a healthy global cycle carries thousands; a tiny parse is a source/
        # schema failure — refuse to overwrite a good feed with a broken one
        raise RuntimeError(f"only {len(land)} stations parsed — refusing to publish")
    # marine platforms degrade honestly: a fetch failure drops that class of
    # obs for the cycle (disclosed in the log), never the whole feed
    marine, ships = {}, []
    try:
        marine = parse_marine(_http(MARINE_SOURCE), now)
    except Exception as e:                                     # noqa: BLE001
        print(f"[metar] marine platforms unavailable this cycle: {e}")
    try:
        ships = parse_ships(_http(SHIP_SOURCE), now)
    except Exception as e:                                     # noqa: BLE001
        print(f"[metar] ship obs unavailable this cycle: {e}")
    n_buoy = 0
    for sid, ob in marine.items():
        if sid in land:            # C-MAN/buoys that also file METAR: keep
            land[sid][11] = PLAT_BUOY   # the METAR ob, mark it marine
        else:
            land[sid] = ob
            n_buoy += 1
    # ONE rank-desc sort across classes — the client declutter is first-claim
    # -wins, so a full ship model can still beat a wind-only land ob
    stations = sorted(list(land.values()) + ships,
                      key=lambda o: (-o[9], o[10]))
    doc = {
        "as_of": now.isoformat(timespec="seconds"),
        "count": len(stations),
        "fields": ["id", "lat", "lon", "t", "td", "slp",
                   "wdir", "wspd", "gust", "rank", "age_min", "plat"],
        "source": "aviationweather.gov METAR cache + NDBC marine/ship obs",
        "stations": stations,
    }
    body = json.dumps(doc, separators=(",", ":")).encode()
    base = "obs/metar"
    stamp = now.replace(second=0, microsecond=0).strftime("%Y%m%dT%H%M%SZ")
    manifest = store.get_json(f"{base}/latest_times.json") or {}
    times = [t for t in manifest.get("times", []) if t != stamp]
    times.append(stamp)
    times = sorted(times)
    keep = 100      # ~8.3 h at the box poller's 5-min cadence — MUST outrun
    #                 the viewer's 6-h dense loop window with margin, or the
    #                 time-locked join blanks the loop's old tail and the
    #                 overlay flashes on/off across every wrap (watched live)
    rolled = times[:-keep] if len(times) > keep else []
    times = times[-keep:]
    prune_now = [t for t in manifest.get("prune_next", []) if t not in times]
    store.put(f"{base}/{stamp}.json", body,
              "public, max-age=31536000, immutable", "application/json")
    mdoc = {
        "product": "obs/metar", "frame": base + "/{t}.json",
        "times": times, "latest": stamp, "prune_next": rolled,
        "as_of": doc["as_of"], "count": doc["count"],
        "source": doc["source"],
    }
    store.put(f"{base}/latest_times.json",
              json.dumps(mdoc, separators=(",", ":")).encode(),
              CACHE_MANIFEST, "application/json")
    # legacy static-latest stays in sync (older clients + deploy-order)
    store.put(f"{base}/latest.json", body, CACHE_MANIFEST, "application/json")
    # DEFERRED prune (the MRMS discipline): delete only frames that rolled
    # off a full cadence ago, AFTER this run's manifest is published
    for t in prune_now:
        try:
            store.delete(f"{base}/{t}.json")
        except Exception:
            pass
    print(f"[metar] {len(stations)} stations ({n_buoy} buoy/C-MAN, "
          f"{len(ships)} ship)  {len(body)//1024} KB  frame {stamp}  "
          f"({len(times)} times, pruned {len(prune_now)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
