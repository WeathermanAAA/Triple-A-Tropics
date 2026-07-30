"""cyclolab_analogs.py - the CycloLab "most resembles" analog engine.

Ranks historical tropical cyclones by combined track + intensity similarity to a
target storm and emits ``cyclolab/{sid}/analogs.json`` for the explorer's
"Most resembles" card + Analysis tab.

Method (meteorologically defensible by design - Andrew is a met):
  * tropycal is the DATA ENGINE ONLY - it loads the best-track archive
    (HURDAT2 1-min winds for AL/EP via include_btk for the current season;
    IBTrACS jtwc 1-min for other basins). WE compute the ranking. ace_core
    stays the ACE canon and is NOT touched.
  * Candidate pre-filter (cheap, at the source): same basin only; genesis
    day-of-year +/- 21 days (year-agnostic); tracks passing within 750 km of the
    target genesis (tropycal analogs_from_point); then drop candidates whose OWN
    genesis is > 1500 km from the target's (kills recurver-vs-straight mismatch).
  * Similarity = the POINT-SEPARATION form of the TSAI metric (Track-and-
    intensity Similarity Area Index; Yang, Tang & Yuan 2018, Weather and
    Forecasting 33(5), doi:10.1175/WAF-D-17-0182.1). Both tracks are resampled to
    N=20 points (shape mode: equally spaced by cumulative along-track distance;
    lead-time mode for a live/incomplete storm: matched hours-since-genesis over
    the overlap). D_track = mean great-circle separation (km) over the 20 pairs;
    D_int = RMS Vmax difference (kt) over the 20 points;
    score = 0.65*(D_track/300) + 0.35*(D_int/25), ranked ascending. Non-tropical
    points are excluded. The true TSAI polygon area (shoelace) is also emitted.

Recon (2007+, by ATCF id) ENRICHES a result with a "recon_available" flag; it is
NEVER a filter on candidates.
"""
from __future__ import annotations

import datetime as _dt
import math
from typing import Optional

# --------------------------------------------------------------------------
# Tunables (the published-metric constants; do not drift without a met review)
# --------------------------------------------------------------------------
N_RESAMPLE = 20                 # points per track (TSAI uses a fixed resample)
TRACK_SCALE_KM = 300.0          # D_track normalizer
INT_SCALE_KT = 25.0             # D_int normalizer
W_TRACK = 0.65                  # combined-score weights (track-dominant)
W_INT = 0.35
TOPK = 10
CAND_RADIUS_KM = 750.0          # analogs_from_point search radius
GENESIS_DOY_WINDOW = 21         # +/- days, year-agnostic
GENESIS_MAX_SEP_KM = 1500.0     # drop candidates whose genesis is farther
MIN_OVERLAP_HOURS = 24.0        # lead-time mode below this -> low confidence
CONF_HIGH = 0.6                 # absolute-score confidence boundaries
CONF_MODERATE = 1.2

# Agency prefix for the canonical cyclolab sid (the /cyclolab/{sid}/ dir + the
# analogs.json R2 key + the click-through id). NHC owns AL/EP/CP; JTWC the rest.
AGENCY = {"AL": "NHC", "EP": "NHC", "CP": "NHC",
          "WP": "JTWC", "IO": "JTWC", "SH": "JTWC"}

# Cyclone stages kept for scoring; everything else (EX extratropical, LO low,
# WV tropical wave, DB disturbance, MD monsoon dep, NR not-rated) is dropped as
# "non-tropical". TD/TS/HU tropical + SD/SS subtropical = the warm/named life.
TROPICAL_TYPES = frozenset({"TD", "TS", "HU", "SD", "SS"})

TSAI_CITATION = ("Yang, Tang & Yuan 2018, Weather and Forecasting 33(5), "
                 "doi:10.1175/WAF-D-17-0182.1 (TSAI; point-separation form)")

# Basin code (from the ATCF/cyclolab sid prefix) -> tropycal load config.
# AL/EP use HURDAT2 (1-min winds, include_btk pulls the current season). The
# JTWC basins use IBTrACS in jtwc mode (1-min). ONE TrackDataset per basin,
# cached; NEVER compare across basins.
BASIN_CONFIG = {
    "AL": {"basin": "north_atlantic", "source": "hurdat", "ibtracs_mode": None, "floor": 1950},
    "EP": {"basin": "east_pacific",   "source": "hurdat", "ibtracs_mode": None, "floor": 1950},
    "CP": {"basin": "east_pacific",   "source": "hurdat", "ibtracs_mode": None, "floor": 1950},
    "WP": {"basin": "west_pacific",   "source": "ibtracs", "ibtracs_mode": "jtwc", "floor": 1980},
    "IO": {"basin": "north_indian",   "source": "ibtracs", "ibtracs_mode": "jtwc", "floor": 1980},
    "SH": {"basin": "south_indian",   "source": "ibtracs", "ibtracs_mode": "jtwc", "floor": 1980},
}

EARTH_R_KM = 6371.0


# --------------------------------------------------------------------------
# Pure geometry / resample / score (no tropycal -> directly unit-testable)
# --------------------------------------------------------------------------
def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance (km) between two lat/lon points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return 2 * EARTH_R_KM * math.asin(min(1.0, math.sqrt(a)))


def _unwrap_lons(lons):
    """Unwrap longitudes so a track crossing the antimeridian interpolates
    continuously (no 180->-180 jump). Returns a new list."""
    out = [float(lons[0])]
    for x in lons[1:]:
        prev = out[-1]
        x = float(x)
        while x - prev > 180:
            x -= 360
        while x - prev < -180:
            x += 360
        out.append(x)
    return out


def _cumulative_km(lats, lons):
    s = [0.0]
    for i in range(1, len(lats)):
        s.append(s[-1] + haversine_km(lats[i - 1], lons[i - 1], lats[i], lons[i]))
    return s


def _interp(x, xs, ys):
    """1-D linear interpolation of ys(xs) at x (xs ascending), clamped to ends."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    span = xs[hi] - xs[lo]
    f = 0.0 if span == 0 else (x - xs[lo]) / span
    return ys[lo] + f * (ys[hi] - ys[lo])


def resample_shape(lats, lons, vmax, n=N_RESAMPLE):
    """Resample a track to ``n`` points equally spaced by cumulative along-track
    great-circle distance (TSAI shape mode). Returns (lats, lons, vmax) lists of
    length n. Longitudes are unwrapped for interpolation then re-wrapped."""
    lons_u = _unwrap_lons(lons)
    s = _cumulative_km(lats, lons_u)
    total = s[-1]
    if total <= 0 or len(lats) < 2:
        return ([float(lats[0])] * n, [((float(lons[0]) + 180) % 360) - 180] * n,
                [float(vmax[0])] * n)
    targets = [total * i / (n - 1) for i in range(n)]
    rlat = [_interp(t, s, lats) for t in targets]
    rlon = [((_interp(t, s, lons_u) + 180) % 360) - 180 for t in targets]
    rv = [_interp(t, s, vmax) for t in targets]
    return rlat, rlon, rv


def resample_leadtime(hours_a, lats_a, lons_a, vmax_a,
                      hours_b, lats_b, lons_b, vmax_b, n=N_RESAMPLE):
    """Lead-time mode for a live/incomplete storm: resample BOTH tracks by matched
    hours-since-genesis over the OVERLAP window only. Returns
    (a_resampled, b_resampled, overlap_hours) where each *_resampled is
    (lats, lons, vmax) of length n. None overlap -> (None, None, 0.0)."""
    overlap = min(hours_a[-1], hours_b[-1])
    if overlap <= 0:
        return None, None, 0.0
    lons_au, lons_bu = _unwrap_lons(lons_a), _unwrap_lons(lons_b)
    targets = [overlap * i / (n - 1) for i in range(n)]
    a = ([_interp(t, hours_a, lats_a) for t in targets],
         [((_interp(t, hours_a, lons_au) + 180) % 360) - 180 for t in targets],
         [_interp(t, hours_a, vmax_a) for t in targets])
    b = ([_interp(t, hours_b, lats_b) for t in targets],
         [((_interp(t, hours_b, lons_bu) + 180) % 360) - 180 for t in targets],
         [_interp(t, hours_b, vmax_b) for t in targets])
    return a, b, float(overlap)


def score_pair(a, b):
    """Combined TSAI-style similarity between two resampled tracks
    a=(lats,lons,vmax), b=(...), each length N. Returns
    (score, d_track_km, d_int_kt, tsai_area_km2). Lower score = more similar."""
    la, lo_a, va = a
    lb, lo_b, vb = b
    n = len(la)
    seps = [haversine_km(la[i], lo_a[i], lb[i], lo_b[i]) for i in range(n)]
    d_track = sum(seps) / n
    d_int = math.sqrt(sum((va[i] - vb[i]) ** 2 for i in range(n)) / n)
    score = W_TRACK * (d_track / TRACK_SCALE_KM) + W_INT * (d_int / INT_SCALE_KT)
    area = _tsai_polygon_area_km2(la, lo_a, lb, lo_b)
    return score, d_track, d_int, area


def _tsai_polygon_area_km2(la, lo_a, lb, lo_b):
    """True TSAI track-separation polygon area (km^2): the closed polygon traced
    by track A forward then track B backward, via the shoelace formula on a local
    equirectangular projection about the mean latitude. A complement to the
    point-separation score (handles crossing tracks via |signed area|)."""
    lats = la + lb
    lons = lo_a + list(reversed(lo_b))
    pts_lat = la + list(reversed(lb))
    lat0 = math.radians(sum(lats) / len(lats))
    kx = EARTH_R_KM * math.cos(lat0) * math.pi / 180.0   # km per deg lon
    ky = EARTH_R_KM * math.pi / 180.0                    # km per deg lat
    xs = [l * kx for l in lons]
    ys = [p * ky for p in pts_lat]
    area2 = 0.0
    m = len(xs)
    for i in range(m):
        j = (i + 1) % m
        area2 += xs[i] * ys[j] - xs[j] * ys[i]
    return abs(area2) / 2.0


def sshs_category(peak_vmax_kt) -> str:
    """Peak Saffir-Simpson category label from peak 1-min Vmax (kt)."""
    v = peak_vmax_kt
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "TD"
    if v < 34:
        return "TD"
    if v < 64:
        return "TS"
    if v < 83:
        return "C1"
    if v < 96:
        return "C2"
    if v < 113:
        return "C3"
    if v < 137:
        return "C4"
    return "C5"


def confidence_label(score, overlap_hours, mode) -> str:
    """Honest confidence from the ABSOLUTE score (+ overlap for lead-time mode).
    A weak match is flagged "low" (the UI shows "nearest available"), never
    dressed up - wrong is worse than absent."""
    if mode == "leadtime" and overlap_hours < MIN_OVERLAP_HOURS:
        return "low"            # too little shared life to judge resemblance
    if score <= CONF_HIGH:
        return "high"
    if score <= CONF_MODERATE:
        return "moderate"
    return "low"


# --------------------------------------------------------------------------
# tropycal-backed layer (lazy import; the pure functions above don't need it)
# --------------------------------------------------------------------------
import re as _re
import sys as _sys

_DATASETS: dict = {}          # basin_code -> TrackDataset (cached per process)
_RECON_ATCFS: set = set()     # lowercase ATCF ids with a recon entry (2025+)
_RECON_SLUGS: set = set()     # name-slug keys (2007-2024 archive, atcf=null)


def set_recon_index(atcfs=None, slugs=None) -> None:
    """Register recon-available ATCF ids + name-slug keys (from recon/manifest's
    storms[].atcf + storms[].slug), so recon_available() can enrich results.
    Recon NEVER filters candidates."""
    global _RECON_ATCFS, _RECON_SLUGS
    _RECON_ATCFS = {str(s).lower() for s in (atcfs or []) if s}
    _RECON_SLUGS = {str(s).lower() for s in (slugs or []) if s}


def _recon_slugify(name) -> str:
    return _re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def recon_available(tropycal_sid, name=None, year=None, basin=None) -> bool:
    """True if a 2007+ recon archive entry exists for this storm - by ATCF id
    (2025+) OR the basin_name_year slug (2007-2024; mirrors reconobs/build.py).
    Recon ENRICHES a result, it is never a filter on candidates."""
    try:
        if year is not None and int(year) < 2007:
            return False
    except (TypeError, ValueError):
        pass
    if str(tropycal_sid).lower() in _RECON_ATCFS:
        return True
    if name and basin and year:
        slug = f"{str(basin).lower()}_{_recon_slugify(name)}_{int(year)}"
        return slug in _RECON_SLUGS
    return False


def canonical_sid(tropycal_sid, basin) -> str:
    """The prefixed cyclolab sid (the /cyclolab/{sid}/ dir + analog key +
    click-through id), e.g. AL092004 -> NHC_AL092004, WP072026 -> JTWC_WP072026."""
    return f"{AGENCY.get(basin, 'NHC')}_{tropycal_sid}"


def normalize_sid(sid: str):
    """(tropycal_sid, basin_code) from a cyclolab sid. Accepts 'JTWC_WP072026',
    'wp072026', 'AL012026', 'al012026' -> ('WP072026','WP') / ('AL012026','AL').
    Returns (None, None) if it isn't a parseable designated-storm id."""
    s = str(sid or "").strip().upper()
    s = _re.sub(r"^[A-Z]+_", "", s)            # strip agency prefix (JTWC_)
    m = _re.match(r"^([A-Z]{2})(\d{2})(\d{4})$", s)
    if not m:
        return None, None
    return s, m.group(1)


def load_dataset(basin_code: str):
    """Cached tropycal TrackDataset for a basin code (AL/EP/WP/IO/SH). Lazy import
    so the pure-math layer + its tests never need tropycal."""
    cfg = BASIN_CONFIG.get(basin_code)
    if cfg is None:
        raise ValueError(f"no analog dataset configured for basin {basin_code!r}")
    if basin_code in _DATASETS:
        return _DATASETS[basin_code]
    from tropycal import tracks  # noqa: E402
    kw = {"basin": cfg["basin"], "source": cfg["source"]}
    if cfg["source"] == "hurdat":
        kw["include_btk"] = True               # current season from b-decks
    if cfg["ibtracs_mode"]:
        kw["ibtracs_mode"] = cfg["ibtracs_mode"]
    ds = tracks.TrackDataset(**kw)
    _DATASETS[basin_code] = ds
    return ds


def _trop_points(storm_dict):
    """(lats, lons, vmax, times) for the TROPICAL points of a storm dict, with
    finite lat/lon/vmax. Non-tropical (EX/LO/WV/DB/...) points are dropped."""
    lats, lons, vm, times = [], [], [], []
    types = storm_dict.get("type", [])
    tt = storm_dict.get("time", storm_dict.get("date", []))
    for i in range(len(storm_dict["lat"])):
        if types[i] not in TROPICAL_TYPES:
            continue
        la, lo, v = storm_dict["lat"][i], storm_dict["lon"][i], storm_dict["vmax"][i]
        try:
            la, lo, v = float(la), float(lo), float(v)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(la) and math.isfinite(lo) and math.isfinite(v)):
            continue
        lats.append(la); lons.append(lo); vm.append(v); times.append(tt[i])
    return lats, lons, vm, times


def _hours_since(times):
    t0 = times[0]
    return [(t - t0).total_seconds() / 3600.0 for t in times]


def _date_window(genesis_time, days=GENESIS_DOY_WINDOW):
    lo = genesis_time - _dt.timedelta(days=days)
    hi = genesis_time + _dt.timedelta(days=days)
    return [lo.strftime("%m/%d"), hi.strftime("%m/%d")]


def _resolve_target(sid, ds, target_points):
    """(tsid, basin, ds, name, year, t_lat, t_lon, t_vm, t_time). ``target_points``
    overrides the tropycal track for a live storm not yet in the archive."""
    tsid, basin = normalize_sid(sid)
    if tsid is None:
        raise ValueError(f"unparseable sid {sid!r}")
    ds = ds or load_dataset(basin)
    name, year = None, None
    if target_points is not None:
        t_lat, t_lon, t_vm, t_time = target_points
    else:
        td = ds.get_storm(tsid).to_dict()
        t_lat, t_lon, t_vm, t_time = _trop_points(td)
        name = (td.get("name") or "UNNAMED").title()
        year = int(td.get("year", 0))
    if len(t_lat) < 2:
        raise ValueError(f"{tsid}: <2 tropical points, cannot rank analogs")
    return tsid, basin, ds, name, year, t_lat, t_lon, t_vm, t_time


def find_analogs(sid: str, mode: str = "shape", topk: int = TOPK, ds=None,
                 target_points=None):
    """Ranked analogs (list of dicts, ascending score, top ``topk``) for a target
    storm. mode='shape' (complete/archive) or 'leadtime' (live/incomplete).
    ``target_points`` = (lats,lons,vmax,times) of the target's TROPICAL points,
    supplied for a live storm whose best track isn't in the archive yet (the
    cyclolab feed is authoritative); candidates always come from the archive."""
    tsid, basin, ds, _, _, t_lat, t_lon, t_vm, t_time = _resolve_target(
        sid, ds, target_points)
    g_lat, g_lon, g_time = t_lat[0], t_lon[0], t_time[0]
    floor = BASIN_CONFIG[basin]["floor"]

    cands = ds.analogs_from_point((g_lat, g_lon), CAND_RADIUS_KM, units="km",
                                  non_tropical=False,
                                  date_range=_date_window(g_time))
    t_rs = resample_shape(t_lat, t_lon, t_vm) if mode == "shape" else None
    t_hours = _hours_since(t_time) if mode != "shape" else None

    out = []
    for csid in cands:
        if csid == tsid:
            continue
        if normalize_sid(csid)[1] != basin:        # never cross basin
            continue
        try:
            cd = ds.get_storm(csid).to_dict()
            if int(cd.get("year", 0)) < floor:
                continue
            c_lat, c_lon, c_vm, c_time = _trop_points(cd)
            if len(c_lat) < 2:
                continue
            gen_sep = haversine_km(g_lat, g_lon, c_lat[0], c_lon[0])
            if gen_sep > GENESIS_MAX_SEP_KM:       # genesis-to-genesis sanity bound
                continue
            if mode == "shape":
                score, d_track, d_int, area = score_pair(
                    t_rs, resample_shape(c_lat, c_lon, c_vm))
                overlap = None
            else:
                a_rs, b_rs, overlap = resample_leadtime(
                    t_hours, t_lat, t_lon, t_vm,
                    _hours_since(c_time), c_lat, c_lon, c_vm)
                if a_rs is None:
                    continue
                score, d_track, d_int, area = score_pair(a_rs, b_rs)
        except Exception as e:  # noqa: BLE001 - one bad candidate never kills the run
            print(f"cyclolab_analogs: candidate {csid} skipped: "
                  f"{type(e).__name__}: {e}", file=_sys.stderr)
            continue
        peak = max(c_vm)
        cname = (cd.get("name") or "UNNAMED").title()
        cyear = int(cd.get("year", 0))
        out.append({
            "sid": canonical_sid(csid, basin),
            "atcf_id": csid.lower(),
            "name": cname,
            "year": cyear,
            "basin": basin,
            "peak_cat": sshs_category(peak),
            "peak_wind_kt": int(round(peak)),
            "score": round(score, 4),
            "d_track_km": round(d_track, 1),
            "d_int_kt": round(d_int, 1),
            "tsai_area_km2": round(area, 0),
            "genesis_sep_km": round(gen_sep, 1),
            "overlap_hours": (round(overlap, 1) if overlap is not None else None),
            "confidence": confidence_label(score, overlap or 0.0, mode),
            "recon_available": recon_available(csid, cname, cyear, basin),
        })
    out.sort(key=lambda r: r["score"])
    out = out[:topk]
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


def build_analogs_json(sid: str, mode: str = "shape", topk: int = TOPK,
                       ds=None, now_iso: Optional[str] = None,
                       target_points=None, target_name=None,
                       target_year=None) -> dict:
    """The full cyclolab/{CANONICAL_SID}/analogs.json document for a target."""
    tsid, basin, ds, name, year, t_lat, t_lon, t_vm, t_time = _resolve_target(
        sid, ds, target_points)
    name = target_name or name or tsid
    year = target_year or year
    overlap = None
    analogs = find_analogs(sid, mode=mode, topk=topk, ds=ds,
                           target_points=(t_lat, t_lon, t_vm, t_time))
    if mode != "shape" and analogs:
        overlap = max((a.get("overlap_hours") or 0) for a in analogs)
    cfg = BASIN_CONFIG[basin]
    return {
        "schema": "cyclolab-analogs/1",
        "sid": canonical_sid(tsid, basin),
        "atcf_id": tsid.lower(),
        "name": name,
        "basin": basin,
        "season": year,
        "mode": mode,
        "overlap_hours": overlap,
        "n_points": N_RESAMPLE,
        "generated_utc": now_iso,
        "source": ("HURDAT2 (NHC) include_btk" if cfg["source"] == "hurdat"
                   else "IBTrACS (jtwc)"),
        "archive_floor_year": cfg["floor"],
        "weights": {"track": W_TRACK, "intensity": W_INT,
                    "track_scale_km": TRACK_SCALE_KM, "int_scale_kt": INT_SCALE_KT},
        "method": ("Point-separation TSAI: D_track = mean great-circle separation "
                   "(km) over 20 along-track-equal points; D_int = RMS Vmax diff "
                   "(kt); score = 0.65*(D_track/300) + 0.35*(D_int/25), ascending."),
        "citation": TSAI_CITATION,
        "count": len(analogs),
        "analogs": analogs,
    }


def build_archive_track(sid: str, ds=None) -> dict:
    """A map-ready storm object for the archive explorer (cyclolab_map.js loads
    this verbatim). Mirrors the live cyclolab storm schema: per-point t/lat/lon/
    wind_kt/pressure_mb/cls/nature + storm-level identity. ALL tropical+
    subtropical points (the full track, not the scoring resample)."""
    tsid, basin = normalize_sid(sid)
    if tsid is None:
        raise ValueError(f"unparseable sid {sid!r}")
    ds = ds or load_dataset(basin)
    d = ds.get_storm(tsid).to_dict()
    times = d.get("time", d.get("date", []))
    pts, peak = [], 0.0
    for i in range(len(d["lat"])):
        if d["type"][i] not in TROPICAL_TYPES:
            continue
        try:
            la, lo, v = float(d["lat"][i]), float(d["lon"][i]), float(d["vmax"][i])
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(la) and math.isfinite(lo)):
            continue
        try:
            mb = float(d["mslp"][i])
            mb = mb if math.isfinite(mb) else None
        except (TypeError, ValueError, KeyError):
            mb = None
        v = v if math.isfinite(v) else None
        peak = max(peak, v or 0.0)
        pts.append({"t": times[i].strftime("%Y-%m-%dT%H:%M:%S"),
                    "lat": round(la, 2), "lon": round(lo, 2),
                    "wind_kt": v, "pressure_mb": mb,
                    "cls": sshs_category(v), "nature": d["type"][i]})
    name = (d.get("name") or "UNNAMED").title()
    year = int(d.get("year", 0))
    return {
        "sid": canonical_sid(tsid, basin),
        "atcf_id": tsid.lower(),
        "atcf_long": tsid.lower(),
        "name": name,
        "season": year,
        "basin_label": BASIN_CONFIG[basin]["basin"].replace("_", " ").title(),
        "current_category": sshs_category(peak),
        "max_category": sshs_category(peak),
        "is_invest": False,
        "is_active": False,
        "recon_available": recon_available(tsid, name, year, basin),
        "points": pts,
    }
