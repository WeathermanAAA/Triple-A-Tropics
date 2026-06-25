"""tcprimed.storms - active-TC centers for the LIVE passive-MW tier.

The live tier (tcprimed.pps) pulls GLOBAL 1C granules and must crop each to a
storm. The set of currently-active systems + their positions already lives in the
home map's feed (``global_storms.geojson`` on R2, written by the poller, read by
the global tracks map and enscenters/anchors.py). We reuse it: each
``kind=="active_marker"`` feature gives the storm id, current centre [lon, lat]
(-180..180, matching the 1C longitude frame), intensity, category and last fix.

A storm's position is taken as its latest fix; over the ~hours-long NRT window a TC
moves <~2 deg, well inside the storm-centred render box, so no per-overpass track
interpolation is needed for v1.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import urllib.request
from typing import Optional

GLOBAL_GEOJSON_URL = "https://cdn.triple-a-tropics.com/global_storms.geojson"
_UA = {"User-Agent": "tat-tcprimed-live/1.0"}
_ATCF_RE = re.compile(r"([A-Z]{2})(\d{2})(\d{4})")


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
    last_fix (datetime|None), is_invest. Returns [] on any failure (live tier then
    simply renders nothing this run, leaving last-known-good R2 live)."""
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
        last_fix = None
        lf = p.get("last_fix")
        if lf:
            try:
                last_fix = dt.datetime.fromisoformat(str(lf).replace("Z", "+00:00"))
                if last_fix.tzinfo is None:
                    last_fix = last_fix.replace(tzinfo=dt.timezone.utc)
            except Exception:  # noqa: BLE001
                last_fix = None
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


def storm_covers(storm: dict, lat: "Optional[object]" = None,
                 lon: "Optional[object]" = None, *, half_deg: float = 6.0) -> bool:
    """True if the storm centre falls within a swath's lat/lon coverage (with a
    small margin). ``lat``/``lon`` are the granule swath 2-D arrays. Cheap reject
    before the (expensive) crop/render."""
    import numpy as np
    if lat is None or lon is None:
        return False
    clat, clon = storm["lat"], storm["lon"]
    la = np.asarray(lat, dtype=float)
    lo = np.asarray(lon, dtype=float)
    finite = np.isfinite(la) & np.isfinite(lo)
    if not finite.any():
        return False
    if not (np.nanmin(la[finite]) - 1.0 <= clat <= np.nanmax(la[finite]) + 1.0):
        return False
    # Longitude: compare in a centre-unwrapped frame so the dateline is safe.
    lou = lo.copy()
    d = lou - clon
    lou[d > 180] -= 360.0
    lou[d < -180] += 360.0
    lou = lou[finite]
    if not (np.nanmin(lou) - 1.0 <= clon <= np.nanmax(lou) + 1.0):
        return False
    # Require the storm to be reasonably INSIDE the swath, not just the bbox:
    # at least one valid pixel within half_deg of the centre.
    near = (np.abs(la[finite] - clat) <= half_deg) & (np.abs(lou - clon) <= half_deg)
    return bool(near.any())
