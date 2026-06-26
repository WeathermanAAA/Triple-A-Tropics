"""ascatobs.storms - active-TC centers for ASCAT pass<->storm association.

Mirrors ``tcprimed.storms``: the set of currently-active systems + their
positions already lives in the home map's feed (``global_storms.geojson`` on R2,
written by the poller, read by the global tracks map, enscenters/anchors.py and
tcprimed/storms.py). We reuse it READ-ONLY - this product never reads or writes
the track / ACE / climatology pipeline, it only consumes the published feed, so
the ASCAT ingest stays fully isolated.

Each ``kind=="active_marker"`` feature gives the storm id, current centre
[lon, lat] (-180..180, matching ASCAT's longitude frame), intensity, category and
last fix. A pass is associated to a storm when the swath comes within
``MAX_KM`` of the storm centre AND the overpass time is within ``MAX_DT_H`` of the
storm's last fix (the same ~750 km / +/-3 h overpass rule the passive-MW work
uses). The current centre is only representative for RECENT passes, so the time
guard keeps an old pass from binding to a storm that has since moved; CycloLab's
per-storm tab does the precise filter client-side against the full best track it
already holds.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import re
import urllib.request

GLOBAL_GEOJSON_URL = "https://cdn.triple-a-tropics.com/global_storms.geojson"
_UA = {"User-Agent": "tat-ascat/1.0 (+https://triple-a-tropics.com)"}
_ATCF_RE = re.compile(r"([A-Z]{2})(\d{2})(\d{4})")

# Overpass-association thresholds (shared default; the CycloLab tab mirrors them).
MAX_KM = 750.0
MAX_DT_H = 3.0
_EARTH_R_KM = 6371.0088


def _parse_atcf(storm_id: str):
    """'JTWC_WP072026' / 'NHC_AL012026' / 'wp072026' -> ('WP072026','WP','07',2026).
    None if no ATCF id can be found."""
    m = _ATCF_RE.search((storm_id or "").upper())
    if not m:
        return None
    basin, num, year = m.group(1), m.group(2), int(m.group(3))
    return f"{basin}{num}{year}", basin, num, year


def active_storms(url: str = GLOBAL_GEOJSON_URL, *, timeout: float = 20.0,
                  include_invests: bool = True) -> list[dict]:
    """Active systems from the live global feed. Each: slug (atcf, lowercased),
    atcf, basin, year, name, lat, lon (-180..180), intensity_kt, category, mslp,
    last_fix (datetime|None), is_invest. Returns [] on any failure (the ingest
    then simply tags no storms this run, leaving last-known-good R2 live)."""
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            gj = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    for f in gj.get("features", []):
        p = f.get("properties") or {}
        if p.get("kind") != "active_marker":
            continue
        geom = f.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        is_invest = (p.get("marker_type") == "invest_x") or bool(p.get("is_ptc"))
        if is_invest and not include_invests:
            continue
        parsed = _parse_atcf(p.get("storm_id") or "")
        if not parsed:
            continue
        atcf, basin, _num, year = parsed
        last_fix = _parse_iso(p.get("last_fix"))
        out.append({
            "slug": atcf.lower(), "atcf": atcf, "basin": basin, "year": year,
            "name": (p.get("name") or p.get("designation") or atcf),
            "lat": float(coords[1]), "lon": float(coords[0]),
            "intensity_kt": (int(p["current_intensity_kt"])
                             if p.get("current_intensity_kt") is not None else None),
            "category": p.get("current_category"),
            "mslp": p.get("current_mslp_mb"),
            "last_fix": last_fix, "is_invest": is_invest,
        })
    return out


def _parse_iso(s) -> "_dt.datetime | None":
    if not s:
        return None
    try:
        d = _dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lon/lat points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return 2 * _EARTH_R_KM * math.asin(min(1.0, math.sqrt(a)))


def _min_dist_to_pass_km(clat: float, clon: float,
                         lats, lons, *, cap_km: float) -> float:
    """Smallest great-circle distance from a storm centre to any WVC in the pass.
    A cheap lon/lat bbox pre-reject (in a centre-unwrapped frame, dateline-safe)
    skips passes whose nearest cell is obviously far; the exact haversine is only
    run on the survivors. Returns ``inf`` when no cell is near."""
    # cap as a generous lat/lon degree pad (1 deg lat ~111 km); lon pad widens
    # toward the pole by 1/cos(lat) so the bbox stays a true distance bound.
    pad_deg = cap_km / 111.0 + 0.5
    coslat = max(0.15, math.cos(math.radians(clat)))
    lon_pad = pad_deg / coslat
    best = math.inf
    for la, lo in zip(lats, lons):
        if la != la or lo != lo:           # NaN
            continue
        if abs(la - clat) > pad_deg:
            continue
        dlon = lo - clon
        if dlon > 180:
            dlon -= 360
        elif dlon < -180:
            dlon += 360
        if abs(dlon) > lon_pad:
            continue
        d = haversine_km(clat, clon, la, lo)
        if d < best:
            best = d
            if best <= 1.0:
                break
    return best


def _nearest_anchor(clat: float, clon: float, path):
    """The (dist_km, time) of the path centreline anchor nearest a storm centre.
    ``path`` is the decoded pass's [{lat,lon,t}] list. Gives the storm's true
    LOCAL overpass time within a ~100-min orbit (the orbit's mid-time would be too
    coarse). Returns (inf, None) for an empty/path-less pass."""
    best_d = math.inf
    best_t = None
    for a in (path or []):
        la, lo = a.get("lat"), a.get("lon")
        if la is None or lo is None:
            continue
        d = haversine_km(clat, clon, la, lo)
        if d < best_d:
            best_d = d
            best_t = a.get("t")
    return best_d, best_t


def associate(storms: list[dict], lats, lons, path=None, *,
              max_km: float = MAX_KM, max_dt_h: float = MAX_DT_H) -> list[dict]:
    """Storms this pass observes: the swath comes within ``max_km`` of the centre
    AND (when both times are known) the local overpass is within ``max_dt_h`` of
    the storm's last fix. Distance is the closest WVC in the (decimated) field;
    the per-storm overpass TIME is the nearest centreline ``path`` anchor (a full
    orbit spans ~100 min, so a single pass time would be wrong for storms far
    apart). Returns compact dicts sorted nearest-first:
    {slug, atcf, name, basin, year, is_invest, dist_km, overpass_utc,
    intensity_kt}. Empty when nothing qualifies."""
    hits: list[dict] = []
    for s in storms:
        d = _min_dist_to_pass_km(s["lat"], s["lon"], lats, lons, cap_km=max_km)
        if d > max_km:
            continue
        _, overpass = _nearest_anchor(s["lat"], s["lon"], path)
        if s.get("last_fix") is not None and overpass:
            ot = _parse_iso(overpass)
            if ot is not None:
                dt_h = abs((ot - s["last_fix"]).total_seconds()) / 3600.0
                if dt_h > max_dt_h:
                    continue
        hits.append({
            "slug": s["slug"], "atcf": s["atcf"], "name": s["name"],
            "basin": s["basin"], "year": s["year"],
            "is_invest": s["is_invest"], "dist_km": round(d, 1),
            "overpass_utc": overpass, "intensity_kt": s.get("intensity_kt"),
            "lat": round(s["lat"], 2), "lon": round(s["lon"], 2),
        })
    hits.sort(key=lambda h: h["dist_km"])
    return hits
