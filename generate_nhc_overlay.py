#!/usr/bin/env python3
"""NHC products overlay feed for the Satellite Explorer (tester item #4).

One GeoJSON-ish document from free public NHC endpoints (no creds):

  - forecast CONES + track lines per active storm (the per-storm official
    GIS zips at a stable ``{stormid}_5day_latest.zip`` path, discovered via
    the public CurrentStorms.json)
  - FORMATION AREAS from the graphical Tropical Weather Outlook shapefile
    (2- and 7-day probability polygons, AL/EP/CP basins)

    nhc/overlay/latest.json   (max-age 300)

Storm CURRENT-POSITION icons are NOT emitted here — the explorer reuses the
site's existing ``global_storms.geojson`` (already classified with the
home-map marker semantics: marker_type + current_category), one feed for
every map. NHC covers AL/EP/CP only; elsewhere the layer is honestly empty.

Runs on the NHC cycle via update-nhc-overlay.yml (30-min staggered); the
document only changes when an advisory or outlook does.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import urllib.request
import zipfile
from pathlib import Path

# The R2 write kill switch (tat_killswitch.py at the repo root, mirrored in
# tsr). Optional on purpose: a missing or broken module means "allowed" --
# the switch can only ever STOP writes, never break a lane. Deletes are
# never guarded (free on R2; prune must keep reducing storage).
try:
    import tat_killswitch
except Exception:  # noqa: BLE001
    tat_killswitch = None

CURRENT = "https://www.nhc.noaa.gov/CurrentStorms.json"
CONE_ZIP = "https://www.nhc.noaa.gov/gis/forecast/archive/{sid}_5day_latest.zip"
GTWO_ZIP = "https://www.nhc.noaa.gov/xgtwo/gtwo_shapefiles.zip"
UTC = dt.timezone.utc
CACHE = "public, max-age=300"


def _http(url, timeout=60, tries=2):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "triple-a-tropics-nhc"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:                    # noqa: BLE001
            last = e
    raise last


def _shp_reader(z: zipfile.ZipFile, base: str):
    import shapefile
    return shapefile.Reader(
        shp=io.BytesIO(z.read(base + ".shp")),
        dbf=io.BytesIO(z.read(base + ".dbf")),
        shx=io.BytesIO(z.read(base + ".shx")))


def _rings(shape):
    """shapefile polygon -> list of rings [[lon,lat],...] (parts split)."""
    pts = [[round(x, 3), round(y, 3)] for x, y in shape.points]
    parts = list(shape.parts) + [len(pts)]
    return [pts[parts[i]:parts[i + 1]] for i in range(len(parts) - 1)]


def storm_features(sid: str, meta: dict):
    """Cone polygon + track line features for one active storm."""
    out = []
    try:
        z = zipfile.ZipFile(io.BytesIO(_http(CONE_ZIP.format(sid=sid), timeout=90)))
    except Exception as e:
        print(f"[nhc] {sid}: cone zip unavailable ({e}) — storm skipped")
        return out
    names = z.namelist()
    props = {
        "storm_id": sid.upper(), "name": meta.get("name"),
        "classification": meta.get("classification"),
        "adv": (meta.get("forecastAdvisory") or {}).get("advNum"),
        "adv_time": (meta.get("forecastAdvisory") or {}).get("issuance"),
    }
    pgn = next((n[:-4] for n in names if n.endswith("_5day_pgn.shp")), None)
    if pgn:
        r = _shp_reader(z, pgn)
        for sr in r.shapeRecords():
            out.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": _rings(sr.shape)},
                "properties": dict(props, kind="cone"),
            })
    lin = next((n[:-4] for n in names if n.endswith("_5day_lin.shp")), None)
    if lin:
        r = _shp_reader(z, lin)
        for sr in r.shapeRecords():
            line = [[round(x, 3), round(y, 3)] for x, y in sr.shape.points]
            if len(line) >= 2:
                out.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": line},
                    "properties": dict(props, kind="track"),
                })
    # forecast POSITIONS (same zip, third shapefile): the cone envelope alone
    # hides the forecast — emit each advisory point so the client can draw
    # the track with timed, intensity-labeled positions inside the cone
    pts = next((n[:-4] for n in names if n.endswith("_5day_pts.shp")), None)
    if pts:
        r = _shp_reader(z, pts)
        for sr in r.shapeRecords():
            rec = sr.record.as_dict()
            if not sr.shape.points:
                continue
            x, y = sr.shape.points[0]

            def num(key):
                try:
                    v = rec.get(key)
                    return None if v in (None, "") else int(float(v))
                except (TypeError, ValueError):
                    return None
            out.append({
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [round(x, 3), round(y, 3)]},
                "properties": dict(
                    props, kind="point",
                    tau=num("TAU"), maxwind=num("MAXWIND"), gust=num("GUST"),
                    mslp=num("MSLP"),
                    dvlbl=rec.get("DVLBL"), tcdvlp=rec.get("TCDVLP"),
                    validtime=rec.get("VALIDTIME"), datelbl=rec.get("DATELBL"),
                ),
            })
    return out


def _point_in_ring(lon: float, lat: float, ring) -> bool:
    """Ray-cast point-in-polygon on one [[lon,lat],...] ring."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and \
           lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def gtwo_features(designated_positions=None):
    """Formation areas — POTENTIAL-development systems only. An area whose
    polygon contains a DESIGNATED storm's current position is dropped: that
    system already developed (it shows a cone + track, never a chance-of-
    formation wash); the outlook shapefile just lags designation by up to a
    cycle. Same invest-vs-designated rule as the marker number gate."""
    out = []
    try:
        z = zipfile.ZipFile(io.BytesIO(_http(GTWO_ZIP, timeout=90)))
    except Exception as e:
        print(f"[nhc] GTWO unavailable ({e}) — no formation areas this cycle")
        return out
    base = next((n[:-4] for n in z.namelist()
                 if n.startswith("gtwo_areas") and n.endswith(".shp")), None)
    if not base:
        return out
    r = _shp_reader(z, base)
    for sr in r.shapeRecords():
        rec = sr.record.as_dict()

        def pct(key):
            try:
                return int(str(rec.get(key, "")).rstrip("%") or 0)
            except ValueError:
                return 0
        rings = _rings(sr.shape)
        if rings and any(_point_in_ring(lon, lat, rings[0])
                         for lat, lon in (designated_positions or [])):
            print("[nhc] outlook area contains a designated storm — dropped "
                  "(developed systems carry a cone, not a formation wash)")
            continue
        out.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": rings},
            "properties": {
                "kind": "area", "basin": rec.get("BASIN"),
                "prob2": pct("PROB2DAY"), "risk2": rec.get("RISK2DAY"),
                "prob7": pct("PROB7DAY"), "risk7": rec.get("RISK7DAY"),
            },
        })
    return out


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
        if tat_killswitch is not None and not tat_killswitch.writes_allowed(key):
            return                       # dropped (the switch logs it)
        self.c.put_object(Bucket=self.bucket, Key=key, Body=data,
                          CacheControl=cache, ContentType=ctype)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="local:/tmp/tat-nhc")
    args = ap.parse_args()
    store = R2Store() if args.store == "r2" else LocalStore(args.store.split(":", 1)[1])

    now = dt.datetime.now(UTC)
    try:
        current = json.loads(_http(CURRENT))
    except Exception as e:
        raise RuntimeError(f"CurrentStorms.json unavailable: {e}")
    storms = current.get("activeStorms") or []
    feats = []
    designated = []
    for s in storms:
        sid = str(s.get("id") or "").lower()
        if not sid:
            continue
        feats.extend(storm_features(sid, s))
        # CurrentStorms lists DESIGNATED systems only (invests never appear)
        # — their positions gate the formation areas below
        try:
            designated.append((float(s["latitudeNumeric"]),
                               float(s["longitudeNumeric"])))
        except (KeyError, TypeError, ValueError):
            pass
    areas = gtwo_features(designated)
    feats.extend(areas)
    # honest-gate parity with the sibling emitters (metar station floor,
    # MRMS coverage ceiling, sfc parse floor): active storms with ZERO
    # cone/track features fetched = an upstream GIS outage, not a quiet
    # season — refuse to replace a good doc with an empty one.
    if storms and not any(f["properties"].get("kind") in ("cone", "track")
                          for f in feats):
        raise RuntimeError(
            f"{len(storms)} active storm(s) but zero cone/track features "
            "fetched — refusing to publish an empty overlay")
    doc = {
        "type": "FeatureCollection",
        "as_of": now.isoformat(timespec="seconds"),
        "storm_count": len(storms),
        "area_count": len(areas),
        "source": "NHC GIS (forecast cones, graphical outlook) + CurrentStorms",
        "features": feats,
    }
    body = json.dumps(doc, separators=(",", ":")).encode()
    store.put("nhc/overlay/latest.json", body, CACHE, "application/json")
    kinds = {}
    for f in feats:
        k = f["properties"]["kind"]
        kinds[k] = kinds.get(k, 0) + 1
    print(f"[nhc] {len(storms)} storm(s), features {kinds}, {len(body)//1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
