#!/usr/bin/env python3
"""Surface analysis overlay feed (tester item #13 — fronts + pressure centers).

Parses the WPC CODED SURFACE FRONTAL POSITIONS bulletin (CODSUS — free
public NWS text, no creds) into GeoJSON-ish features the explorer's canvas
layer draws in TAT style — a ROLLING SERIES keyed on the analysis VALID
time so the layer time-locks to the playback clock:

    sfc/analysis/{YYYYMMDDTHHMMSSZ}.json   (immutable frames, stamp = valid)
    sfc/analysis/latest_times.json         (manifest)
    sfc/analysis/latest.json               (legacy static-latest, kept in sync)
    { "valid": iso, "as_of": iso, "source": "WPC CODSUS",
      "centers": [{"kind":"high","mb":1023,"lat":38.4,"lon":-106.8}, ...],
      "fronts":  [{"kind":"cold","points":[[lat,lon],...]}, ...] }

Bulletin grammar (ASUS02 KWBC):
    VALID MMDDHHZ
    HIGHS 1023 3841068 1016 5731045 ...     (pressure, position) pairs
    LOWS  ...
    COLD/WARM/STNRY/OCFNT/TROF p1 p2 p3 ... one line per front, wrapped
    continuation lines carry bare position groups.
Position group = 7 digits: lat*10 (3) + lonW*10 (4) -> 3841068 = 38.4N 106.8W.

CONUS/N-America scope (that is WPC's analysis domain). Isobars are a
follow-on (RTMA/GFS MSLP contouring) — the coded bulletin carries none.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
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

SOURCE = "https://tgftp.nws.noaa.gov/data/raw/as/asus02.kwbc.cod.sus.txt"
UTC = dt.timezone.utc
CACHE = "public, max-age=300"
FRONT_KINDS = {"COLD": "cold", "WARM": "warm", "STNRY": "stnry",
               "OCFNT": "ocfnt", "TROF": "trof"}


def _http(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "triple-a-tropics-sfc"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _pos(tok: str):
    if not re.fullmatch(r"\d{7}", tok):
        return None
    lat = int(tok[:3]) / 10.0
    lon = -int(tok[3:]) / 10.0
    if not (0 < lat < 90 and -180 < lon < 0):
        return None
    return [lat, lon]


def parse_codsus(text: str, now: dt.datetime):
    lines = [ln.rstrip() for ln in text.splitlines()]
    valid = None
    centers, fronts = [], []
    cur = None                      # current front dict while its line wraps

    def flush():
        nonlocal cur
        if cur and len(cur["points"]) >= 2:
            fronts.append(cur)
        cur = None

    mode = None                     # 'HIGHS' | 'LOWS' | None
    pend_mb = None
    for ln in lines:
        m = re.match(r"VALID (\d{2})(\d{2})(\d{2})Z", ln)
        if m:
            mo, dd, hh = int(m.group(1)), int(m.group(2)), int(m.group(3))
            yr = now.year
            if mo > now.month + 1:      # December bulletin read in January
                yr -= 1
            valid = dt.datetime(yr, mo, dd, hh, tzinfo=UTC)
            continue
        toks = ln.split()
        if not toks:
            continue
        head = toks[0]
        if head in ("HIGHS", "LOWS"):
            flush()
            mode = head
            pend_mb = None
            toks = toks[1:]
        elif head in FRONT_KINDS:
            flush()
            mode = None
            cur = {"kind": FRONT_KINDS[head], "points": []}
            toks = toks[1:]
        elif not re.fullmatch(r"\d+", head):
            flush()
            mode = None
            continue                    # prose/header line
        for t in toks:
            if cur is not None:
                p = _pos(t)
                if p:
                    cur["points"].append(p)
                continue
            if mode in ("HIGHS", "LOWS"):
                if re.fullmatch(r"\d{3,4}", t):
                    pend_mb = int(t)
                else:
                    p = _pos(t)
                    if p and pend_mb is not None:
                        centers.append({
                            "kind": "high" if mode == "HIGHS" else "low",
                            "mb": pend_mb, "lat": p[0], "lon": p[1]})
    flush()
    return valid, centers, fronts


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
    ap.add_argument("--store", default="local:/tmp/tat-sfc")
    args = ap.parse_args()
    store = R2Store() if args.store == "r2" else LocalStore(args.store.split(":", 1)[1])

    now = dt.datetime.now(UTC)
    valid, centers, fronts = parse_codsus(_http(SOURCE), now)
    if valid is None or (len(centers) < 3 and len(fronts) < 2):
        raise RuntimeError(
            f"implausible CODSUS parse (valid={valid}, {len(centers)} centers, "
            f"{len(fronts)} fronts) — refusing to publish")
    doc = {
        "valid": valid.isoformat(timespec="seconds"),
        "as_of": now.isoformat(timespec="seconds"),
        "source": "NWS/WPC coded surface analysis (CODSUS)",
        "centers": centers,
        "fronts": fronts,
    }
    body = json.dumps(doc, separators=(",", ":")).encode()
    base = "sfc/analysis"
    stamp = valid.strftime("%Y%m%dT%H%M%SZ")
    manifest = store.get_json(f"{base}/latest_times.json") or {}
    if manifest.get("latest") == stamp:
        # analyses are 3-hourly; a tight poll loop must write only on a NEW
        # valid time (the MRMS watermark discipline)
        print(f"[sfc] {stamp} already current — no-op")
        return 0
    times = [t for t in manifest.get("times", []) if t != stamp]
    times.append(stamp)
    times = sorted(times)
    keep = 10                               # ~30 h of 3-hourly analyses
    rolled = times[:-keep] if len(times) > keep else []
    times = times[-keep:]
    prune_now = [t for t in manifest.get("prune_next", []) if t not in times]
    store.put(f"{base}/{stamp}.json", body,
              "public, max-age=31536000, immutable", "application/json")
    mdoc = {
        "product": "sfc/analysis", "frame": base + "/{t}.json",
        "times": times, "latest": stamp, "prune_next": rolled,
        "as_of": doc["as_of"], "source": doc["source"],
    }
    store.put(f"{base}/latest_times.json",
              json.dumps(mdoc, separators=(",", ":")).encode(),
              CACHE, "application/json")
    store.put(f"{base}/latest.json", body, CACHE, "application/json")
    for t in prune_now:                     # deferred prune (MRMS discipline)
        try:
            store.delete(f"{base}/{t}.json")
        except Exception:
            pass
    kinds = {}
    for f in fronts:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    print(f"[sfc] valid {doc['valid']}  {len(centers)} centers  "
          f"{len(fronts)} fronts {kinds}  {len(body)//1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
