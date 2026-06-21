"""
B-deck moving ANCHORS for the Ensemble Cyclone Centers per-system clustering.

THE PROBLEM this solves (see ``docs/enscenters_clustering.md`` for the long form):
the per-system clustering in :mod:`enscenters.tracking` is purely SPATIAL (genesis
seeds + HDBSCAN on track-to-track separation). Spatial-only density has no notion
of system IDENTITY or absolute TIME, so it has two real failure modes:

  * OVER-SPLIT - one real system whose member centers fan / recurve along-track past
    the same-system scale breaks into TWO clusters (shows two systems when there is
    one).
  * UNDER-MERGE - two genuinely separate systems that happen to sit close get unioned
    into ONE cluster (shows one when there are two).

The cure is to ANCHOR each KNOWN system on the official designation / best-track
position and ASSOCIATE ensemble member tracks to it by gating on distance to a
MOVING anchor (one anchor per system, advanced forward along its motion). One
system = one anchor = one cluster as it moves/kinks; two systems = two anchors =
never merged. Centers near NO anchor still cluster by the density method (genesis /
new invests are not lost). This module supplies the anchors; the association lives
in :mod:`enscenters.tracking`.

ANCHOR SOURCE = the SAME live feed the home map uses: ``global_storms.geojson`` on
the CDN (``cdn.triple-a-tropics.com``), the FeatureCollection
``ace_core.build_global_geojson`` publishes and ``/global_tracks.html`` fetches. It
already carries EVERY active designated storm, PTC, and invest (NHC AL/EP/CP +
JTWC WP/IO/SH via the poller's knackwx path) with a recent track - so we reuse the
exact designated + invest feed instead of re-fetching b-decks / CurrentStorms /
knackwx ourselves.

DATELINE-SAFE: forward progress is a great-circle destination from the latest fix
along the persistence heading; longitudes are wrapped, never raw-advanced. Network
fetches are stdlib-only (the cron has no ``requests``) and degrade to an EMPTY
anchor set on any failure, so a feed outage just reverts to today's density-only
clustering - the centers publish is never blocked.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Live feed (the home map's own global FeatureCollection on the CDN).
GLOBAL_GEOJSON_URL = "https://cdn.triple-a-tropics.com/global_storms.geojson"
_FETCH_TIMEOUT = 20.0
_UA = {"User-Agent": "triple-a-tropics.com enscenters anchors (weather hobby site)",
       "Cache-Control": "no-cache"}

# Anchoring is a LIVE feature: only apply it to a cycle within this many hours of
# "now". An older backfill (--tracks-only on a days-old cycle) must NOT be anchored
# on TODAY's systems (they have moved / dissipated and new ones have formed) - it
# falls back to the spatial density method, which is time-agnostic and correct for
# any cycle. The never-miss window keeps recent cycles inside this guard.
ANCHOR_MAX_CYCLE_AGE_H = 30.0


# ===========================================================================
# Great-circle motion (exact on the sphere; dateline-safe)
# ===========================================================================
def _wrap180(lon: float) -> float:
    return ((lon + 180.0) % 360.0) - 180.0


def gc_initial_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing (deg clockwise from north) from 1 -> 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def gc_destination(lat: float, lon: float, bearing_deg: float,
                   dist_deg: float) -> Tuple[float, float]:
    """Point ``dist_deg`` degrees of arc from (lat,lon) on the initial great-circle
    heading ``bearing_deg``. Exact on the unit sphere (NOT a flat-earth offset, so a
    multi-thousand-km projection over a long forecast stays correct), dateline-safe
    (output lon wrapped to [-180,180)). ``dist_deg`` may be negative (reverse)."""
    d = math.radians(dist_deg)
    th = math.radians(bearing_deg)
    p1 = math.radians(lat)
    sin_p2 = math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(th)
    sin_p2 = max(-1.0, min(1.0, sin_p2))
    p2 = math.asin(sin_p2)
    y = math.sin(th) * math.sin(d) * math.cos(p1)
    x = math.cos(d) - math.sin(p1) * sin_p2
    lon2 = math.radians(lon) + math.atan2(y, x)
    return math.degrees(p2), _wrap180(math.degrees(lon2))


# ===========================================================================
# Anchor
# ===========================================================================
@dataclass
class Anchor:
    """One known system to seed a cluster on. Position + persistence motion are
    referenced to its LATEST official fix (``lat1``/``lon1`` at lead ``age1_h``
    relative to the model init); ``position_at`` advances along the heading.

      sid          official designation / id ("AL05", "WP07", "91L", "INVEST 90E")
      name         human name for labeling ("ARTHUR", "INVEST 90E")
      lat1, lon1   latest official fix position
      bearing_deg  persistence heading (great-circle) from the prior fix
      speed_deg_h  storm speed in degrees of arc per hour (0 if a single fix)
      age1_h       (latest-fix time - model init) in hours; the lead at which the
                   anchor sits exactly on lat1/lon1. Usually small + positive (the
                   b-deck has caught up past init); may be negative.
      is_invest    invest / PTC vs a named-or-numbered designated system (labeling)
    """
    sid: str
    name: str
    lat1: float
    lon1: float
    bearing_deg: float
    speed_deg_h: float
    age1_h: float = 0.0
    is_invest: bool = False

    def position_at(self, step_h: float) -> Tuple[float, float]:
        """Projected (lat,lon) of this system at forecast lead ``step_h`` (hours
        from the model init). Persistence motion: hold heading + speed, advance by
        the elapsed arc from the latest fix. A stationary anchor (single fix) just
        returns its fix position at every lead."""
        if self.speed_deg_h <= 0.0:
            return self.lat1, self.lon1
        return gc_destination(self.lat1, self.lon1, self.bearing_deg,
                              self.speed_deg_h * (step_h - self.age1_h))


def build_anchor_payload(anchors: List[Anchor],
                         leads: List[int]) -> List[Dict]:
    """Materialise each anchor's moving track over the forecast ``leads`` into the
    plain dict the clustering consumes: ``{"sid","name","is_invest","pos"}`` where
    ``pos`` maps lead step -> (lat, lon). Keeps :mod:`enscenters.tracking` decoupled
    from this class (and lets tests hand in precomputed positions)."""
    out = []
    for a in anchors:
        pos = {int(s): a.position_at(float(s)) for s in leads}
        out.append({"sid": a.sid, "name": a.name, "is_invest": a.is_invest, "pos": pos})
    return out


# ===========================================================================
# Parse the home map's global_storms.geojson into anchors
# ===========================================================================
def _parse_iso(s: Optional[str]) -> Optional[dt.datetime]:
    """Parse a feed timestamp (ISO 8601, trailing 'Z' or offset) to a naive-UTC
    datetime, matching the codebase's naive-UTC cycle convention."""
    if not s:
        return None
    t = s.strip().replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(t)
    except ValueError:
        try:
            d = dt.datetime.strptime(s.strip()[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if d.tzinfo is not None:
        d = d.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return d


def anchors_from_geojson(gj: dict, cycle: dt.datetime) -> List[Anchor]:
    """Build the active-system anchors from a ``global_storms.geojson``
    FeatureCollection (the home map feed). One anchor per ``active_marker`` feature
    (= every active designated storm / PTC / invest); its persistence motion comes
    from that storm's last two ``observation`` fixes; the anchor is referenced to
    the latest fix so ``position_at`` lands it on the right forecast lead.

    Pure (no network) so it is unit-testable. ``cycle`` is the model init (naive
    UTC). Systems with an unusable position are skipped, never raised on."""
    feats = gj.get("features") or []

    # observations grouped by storm, time-sorted (the motion source)
    obs: Dict[str, List[Tuple[dt.datetime, float, float]]] = {}
    markers: List[dict] = []
    for f in feats:
        props = f.get("properties") or {}
        kind = props.get("kind")
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates")
        if kind == "observation" and geom.get("type") == "Point" and coords:
            t = _parse_iso(props.get("time_iso"))
            if t is None:
                continue
            sid = str(props.get("storm_id") or "")
            try:
                lon, lat = float(coords[0]), float(coords[1])
            except (TypeError, ValueError, IndexError):
                continue
            obs.setdefault(sid, []).append((t, lat, lon))
        elif kind == "active_marker" and geom.get("type") == "Point" and coords:
            markers.append(f)

    for v in obs.values():
        v.sort(key=lambda r: r[0])

    anchors: List[Anchor] = []
    for f in markers:
        props = f.get("properties") or {}
        sid = str(props.get("storm_id") or "")
        coords = f["geometry"]["coordinates"]
        try:
            mlon, mlat = float(coords[0]), float(coords[1])
        except (TypeError, ValueError, IndexError):
            continue
        name = str(props.get("name") or props.get("designation") or sid or "SYSTEM")
        designation = str(props.get("designation") or sid or name)
        is_invest = bool(props.get("marker_type") == "invest_x")

        track = obs.get(sid) or []
        last_t = _parse_iso(props.get("last_fix"))
        if track:
            t1, lat1, lon1 = track[-1]
        else:
            # marker-only system (no separate observation points emitted): anchor on
            # the marker position, treat the marker time as the latest fix.
            t1, lat1, lon1 = (last_t or cycle), mlat, mlon

        bearing, speed = 0.0, 0.0
        if len(track) >= 2:
            t0, lat0, lon0 = track[-2]
            dt_h = (t1 - t0).total_seconds() / 3600.0
            if dt_h > 0:
                from .tracking import gc_deg
                arc = gc_deg(lat0, lon0, lat1, lon1)
                speed = arc / dt_h
                if arc > 1e-6:
                    bearing = gc_initial_bearing(lat0, lon0, lat1, lon1)
        age1_h = (t1 - cycle).total_seconds() / 3600.0
        anchors.append(Anchor(sid=designation, name=name, lat1=lat1, lon1=lon1,
                              bearing_deg=bearing, speed_deg_h=speed,
                              age1_h=age1_h, is_invest=is_invest))
    return anchors


# ===========================================================================
# Fetch (live; graceful)
# ===========================================================================
def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def fetch_global_anchors(cycle: dt.datetime, *, url: str = GLOBAL_GEOJSON_URL,
                         timeout: float = _FETCH_TIMEOUT,
                         max_cycle_age_h: float = ANCHOR_MAX_CYCLE_AGE_H,
                         now: Optional[dt.datetime] = None,
                         progress=print) -> List[Anchor]:
    """Fetch the home map's ``global_storms.geojson`` and build anchors for ``cycle``.

    Returns ``[]`` (-> density-only clustering, today's behavior) when: the cycle is
    older than ``max_cycle_age_h`` (an old backfill must not borrow today's systems);
    the feed is unreachable / malformed; or it lists no active systems. NEVER raises -
    anchoring is an additive enhancement and must never block the centers publish."""
    now = now or _utcnow()
    age_h = (now - cycle).total_seconds() / 3600.0
    if age_h > max_cycle_age_h:
        progress(f"[anchors] cycle {cycle:%Y%m%d%H} is {age_h:.0f} h old "
                 f"(> {max_cycle_age_h:.0f} h); density-only, no live anchors")
        return []
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            gj = json.loads(r.read().decode("utf-8"))
        anchors = anchors_from_geojson(gj, cycle)
        progress(f"[anchors] {len(anchors)} active system(s) from the global feed "
                 f"for cycle {cycle:%Y%m%d%H}")
        return anchors
    except Exception as e:  # noqa: BLE001 - additive; any failure -> density-only
        progress(f"[anchors] WARN: live feed fetch/parse failed ({e}); density-only")
        return []
