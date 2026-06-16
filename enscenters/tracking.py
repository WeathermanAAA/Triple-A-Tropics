"""
Tracking + clustering keystone for the Ensemble Cyclone Centers platform.

Turns the existing per-STEP centers (the lean ``{slug}/{cycle}.json`` the Cheerios
view already uses) into per-MEMBER tracks and per-SYSTEM clusters, and emits an
ENRICHED SIBLING per-cycle JSON (``{slug}/{cycle}.tracks.json``) the viewer will
later read for connected lines, an ensemble-mean track, an intensity plume, and an
obs-vs-envelope overlay. This file is BACKEND/DATA ONLY; the viewer features are a
follow-up. The lean centers JSON is NOT touched, so the default fast Cheerios view
is unaffected and the tracks file loads only when the richer view needs it.

REUSE, NOT RE-INGEST: nothing here re-downloads GRIB or re-runs detection. The
self-detected models (ecens, ecaie, gefs) feed their per-step centers into Stage A
linkage; the native-track models (fnv3, genc) are already per-member linked by the
CSV's ``track_id`` and hand that grouping in directly, SKIPPING Stage A.

Stages:
  A  TRACK LINKAGE (self-detected only): greedy great-circle nearest-neighbour
     stitch with an advected first guess + intensity-continuity tie-break.
  B  PER-SYSTEM CLUSTERING (all models): genesis-proximity seeds, then HDBSCAN
     (metric='precomputed') on a track-to-track distance = mean great-circle
     separation over OVERLAPPING lead times (fair to late-forming members).
  C  DERIVED PRODUCTS (per cluster): robust spherical ensemble-mean track,
     intensity plume (Vmax + MSLP percentiles by lead), position covariance
     envelope (50%/90% ellipses chained to a swath), and an obs-vs-envelope helper.
  D  EMIT the sibling JSON + a manifest reference (handled by the caller via the
     returned ``generated_at`` token, mirroring the centers cache-bust tokens).

ALL geographic math is on the unit sphere / haversine. Longitudes are never raw-
averaged and positions are never compared in Euclidean lat/lon - the antimeridian
is the #1 pitfall. Display longitudes in the emitted JSON are UNWRAPPED to a
continuous, dateline-safe sequence.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

EARTH_R_KM = 6371.0088
DEG_PER_KM_LAT = 1.0 / 110.574          # ~1 deg lat
_BIG = 1.0e6                             # "no overlap" track-track distance sentinel

# --- Stage A linkage defaults (scaled to the actual step spacing at runtime) ---
LINK_RANGE_DEG_6H = 5.0                  # max great-circle step at 6 h spacing
LINK_RANGE_DEG_3H = 3.0                  # ... at 3 h spacing
W_POS = 1.0                              # position weight in the match cost
W_INT = 0.5                              # intensity-continuity tie-break weight
DMSLP_NORM = 30.0                        # hPa normaliser for the intensity term
MAX_DMSLP_PER_6H = 28.0                  # cap implausible jumps (cross-system guard)
MIN_DURATION_H = 24.0                    # drop tracks shorter than this
MIN_PATH_DEG = 2.0                       # drop near-stationary spurious centers (~222 km)

# --- Stage B clustering defaults ---
GENESIS_RADIUS_DEG = 5.0                 # seed grouping radius at genesis
MIN_CLUSTER_FRAC = 0.13                  # min_cluster_size ~= 13% of members (~6-7 of 50)
MIN_SAMPLES = 1                          # loose: keep loose-but-real clusters
# Within a genesis seed there is ONE candidate system; HDBSCAN on a near-uniform
# jittered blob can otherwise shatter it into spurious sub-clusters. This DBSCAN-
# like merge epsilon (deg of mean track separation) coalesces intra-system
# fragments while leaving genuinely divergent paths (a second system that shared
# the seed) split - it is well below inter-system divergence yet above the ~0.5-2
# deg intra-system spread.
CLUSTER_MERGE_EPS_DEG = 2.5
# Post-HDBSCAN robustness (the uniform-blob fix). HDBSCAN finds density peaks; one
# coherent system (members gradually fanning from a shared genesis) is a single
# blob that HDBSCAN nonetheless shatters into sub-clusters + noise. Distinct
# systems are ALREADY separated by the genesis seeds, so WITHIN a seed we coalesce
# anything closer than the same-system scale: merge sub-clusters whose mean track
# separation is below it, and re-absorb noise tracks that fall within it of a
# cluster. A genuinely divergent second system sharing the seed stays split (its
# mean separation exceeds the scale), and a far outlier stays noise (dropped).
SAME_SYSTEM_DEG = 6.0                    # mean great-circle track sep of one system
MERGE_FACTOR = 1.5                       # ...or this multiple of a cluster's own spread
MERGE_FLOOR_DEG = 1.0                    # min spread scale (deg)

SCHEMA_VERSION = 1


# ===========================================================================
# Geometry - dateline-safe, unit-sphere / haversine ONLY
# ===========================================================================
def to_xyz(lat: float, lon: float) -> np.ndarray:
    la, lo = math.radians(lat), math.radians(lon)
    cla = math.cos(la)
    return np.array([cla * math.cos(lo), cla * math.sin(lo), math.sin(la)])


def xyz_to_latlon(v: np.ndarray) -> Tuple[float, float]:
    x, y, z = v
    lat = math.degrees(math.asin(max(-1.0, min(1.0, z / (np.linalg.norm(v) or 1.0)))))
    lon = math.degrees(math.atan2(y, x))
    return lat, lon


def gc_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle separation in DEGREES of arc (haversine; dateline-safe)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return math.degrees(2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def gc_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return math.radians(gc_deg(lat1, lon1, lat2, lon2)) * EARTH_R_KM


def spherical_mean(latlons: Sequence[Tuple[float, float]],
                   weights: Optional[Sequence[float]] = None) -> Tuple[float, float]:
    """Mean position = normalised mean of unit vectors (never raw-average lon)."""
    V = np.array([to_xyz(la, lo) for la, lo in latlons])
    if weights is not None:
        V = V * np.asarray(weights, float)[:, None]
    s = V.sum(axis=0)
    n = np.linalg.norm(s)
    if n < 1e-12:                       # antipodal cancellation: fall back to first
        return latlons[0]
    return xyz_to_latlon(s / n)


def geometric_median(latlons: Sequence[Tuple[float, float]],
                     iters: int = 96, eps: float = 1e-9) -> Tuple[float, float]:
    """Robust spherical centroid: Weiszfeld geometric median on the unit sphere
    (minimises summed chord distance), renormalised. Resists a stray outlier far
    better than the plain mean."""
    if len(latlons) == 1:
        return latlons[0]
    P = np.array([to_xyz(la, lo) for la, lo in latlons])
    x = P.mean(axis=0)
    nx = np.linalg.norm(x)
    x = x / nx if nx > 1e-12 else P[0]
    for _ in range(iters):
        d = np.linalg.norm(P - x, axis=1)
        d = np.maximum(d, eps)
        w = 1.0 / d
        xn = (P * w[:, None]).sum(axis=0) / w.sum()
        nn = np.linalg.norm(xn)
        if nn < 1e-12:
            break
        xn = xn / nn
        if np.linalg.norm(xn - x) < eps:
            x = xn
            break
        x = xn
    return xyz_to_latlon(x)


def unwrap_lons(lons: Sequence[float]) -> List[float]:
    """Continuous, dateline-safe longitude sequence for DISPLAY: each successive
    lon is shifted by +/-360 to stay within 180 deg of the previous one. The
    result may run outside [-180, 180]; the renderer wraps it back per tile."""
    out: List[float] = []
    prev = None
    for lo in lons:
        if prev is None:
            out.append(lo)
        else:
            d = lo - prev
            d -= 360.0 * round(d / 360.0)
            out.append(prev + d)
        prev = out[-1]
    return out


def _local_xy_km(lat: float, lon: float, ref_lat: float, ref_lon: float) -> Tuple[float, float]:
    """Local east/north offset (km) of (lat,lon) from a reference, dateline-safe
    (the lon delta is wrapped to [-180,180] before scaling). Good for the small
    spreads of a per-lead member cloud (a tangent-plane approximation)."""
    dlon = lon - ref_lon
    dlon -= 360.0 * round(dlon / 360.0)
    x = math.radians(dlon) * math.cos(math.radians(ref_lat)) * EARTH_R_KM
    y = math.radians(lat - ref_lat) * EARTH_R_KM
    return x, y


def _xy_km_to_latlon(x: float, y: float, ref_lat: float, ref_lon: float) -> Tuple[float, float]:
    lat = ref_lat + math.degrees(y / EARTH_R_KM)
    coslat = math.cos(math.radians(ref_lat)) or 1e-6
    lon = ref_lon + math.degrees(x / (EARTH_R_KM * coslat))
    return lat, lon


# ===========================================================================
# Stage A - track linkage (self-detected models only)
# ===========================================================================
def link_tracks(centers: Sequence[Sequence], spacing_h: float, *,
                range_deg: Optional[float] = None, maxgap: Optional[int] = None,
                w_pos: float = W_POS, w_int: float = W_INT,
                dmslp_norm: float = DMSLP_NORM, max_dmslp_per_6h: float = MAX_DMSLP_PER_6H,
                min_duration_h: float = MIN_DURATION_H,
                min_path_deg: float = MIN_PATH_DEG) -> List[List[list]]:
    """Greedy great-circle nearest-neighbour stitcher for ONE member's per-step
    centers ``[step_h, lat, lon, mslp, vmax]``.

    For each open track predict the next position by linear extrapolation of the
    last two fixes (advected first guess; persistence with one fix); among unused
    centers at the next step within the (gap-scaled) range, pick the one minimising
    ``w_pos*(gc/range) + w_int*(|dMSLP|/dMSLP_norm)``, rejecting implausible
    pressure jumps (cross-system guard). Allow ``maxgap`` skipped steps. Finally
    drop tracks below the min duration or min path distance (kills stationary
    spurious centers). Returns a list of tracks, each a list of center rows.
    """
    if range_deg is None:
        range_deg = LINK_RANGE_DEG_6H if spacing_h >= 6 else LINK_RANGE_DEG_3H
    if maxgap is None:
        maxgap = 1 if spacing_h >= 6 else 2
    gap_h = (maxgap + 1) * spacing_h

    by_step: Dict[int, List[list]] = {}
    for c in centers:
        by_step.setdefault(int(c[0]), []).append(list(c))
    steps = sorted(by_step)
    if not steps:
        return []

    # open track = {"fixes":[row...], "last": step}
    open_tracks: List[dict] = []
    closed: List[dict] = []

    def predict(tr: dict, step: int) -> Tuple[float, float]:
        fixes = tr["fixes"]
        if len(fixes) >= 2:
            (s0, la0, lo0), (s1, la1, lo1) = ((fixes[-2][0], fixes[-2][1], fixes[-2][2]),
                                              (fixes[-1][0], fixes[-1][1], fixes[-1][2]))
            denom = (s1 - s0) or spacing_h
            ratio = (step - s1) / denom
            v0, v1 = to_xyz(la0, lo0), to_xyz(la1, lo1)
            vp = v1 + (v1 - v0) * ratio
            n = np.linalg.norm(vp)
            if n > 1e-12:
                return xyz_to_latlon(vp / n)
        f = fixes[-1]
        return f[1], f[2]

    for k, step in enumerate(steps):
        cands = by_step[step]
        if k == 0:
            for c in cands:
                open_tracks.append({"fixes": [c], "last": step})
            continue
        live = [tr for tr in open_tracks if (step - tr["last"]) <= gap_h]
        stale = [tr for tr in open_tracks if (step - tr["last"]) > gap_h]
        closed.extend(stale)

        pairs: List[Tuple[float, int, int]] = []   # (cost, track_idx, cand_idx)
        for ti, tr in enumerate(live):
            plat, plon = predict(tr, step)
            gapr = max(1.0, (step - tr["last"]) / spacing_h)
            rng = range_deg * gapr
            last_mslp = tr["fixes"][-1][3]
            for ci, c in enumerate(cands):
                d = gc_deg(plat, plon, c[1], c[2])
                if d > rng:
                    continue
                dm = abs((c[3] or 0.0) - (last_mslp or 0.0)) if (c[3] is not None and last_mslp is not None) else 0.0
                if dm > max_dmslp_per_6h * gapr:
                    continue
                cost = w_pos * (d / range_deg) + w_int * (dm / dmslp_norm)
                pairs.append((cost, ti, ci))

        pairs.sort(key=lambda p: p[0])
        used_t, used_c = set(), set()
        for cost, ti, ci in pairs:
            if ti in used_t or ci in used_c:
                continue
            used_t.add(ti)
            used_c.add(ci)
            live[ti]["fixes"].append(cands[ci])
            live[ti]["last"] = step
        for ci, c in enumerate(cands):
            if ci not in used_c:
                open_tracks.append({"fixes": [c], "last": step})
        # drop tracks that have now gone stale beyond the gap (already in `closed`)
        open_tracks = [tr for tr in open_tracks if (step - tr["last"]) <= gap_h]

    closed.extend(open_tracks)

    out: List[List[list]] = []
    for tr in closed:
        fixes = sorted(tr["fixes"], key=lambda r: r[0])
        if len(fixes) < 2:
            continue
        duration = fixes[-1][0] - fixes[0][0]
        if duration < min_duration_h:
            continue
        path = sum(gc_deg(fixes[i][1], fixes[i][2], fixes[i + 1][1], fixes[i + 1][2])
                   for i in range(len(fixes) - 1))
        if path < min_path_deg:
            continue
        out.append(fixes)
    return out


# ===========================================================================
# Stage B - per-system clustering (all models)
# ===========================================================================
def _genesis(track: Sequence[Sequence]) -> Tuple[float, float, int]:
    f = track[0]
    return f[1], f[2], int(f[0])


def _track_distance(a: Sequence[Sequence], b: Sequence[Sequence]) -> float:
    """Mean great-circle separation (deg) over OVERLAPPING steps; ``_BIG`` if the
    two tracks never share a lead time (so they cannot be the same system)."""
    bb = {int(r[0]): r for r in b}
    sep, n = 0.0, 0
    for r in a:
        o = bb.get(int(r[0]))
        if o is not None:
            sep += gc_deg(r[1], r[2], o[1], o[2])
            n += 1
    return sep / n if n else _BIG


def _seed_groups(tracks: List[dict], radius_deg: float,
                 overlap_deg: float = SAME_SYSTEM_DEG) -> List[List[int]]:
    """Union-find grouping of track indices into candidate systems (dateline-safe).
    Two tracks seed together when they are CO-LOCATED over their overlapping valid
    times (mean great-circle sep <= ``overlap_deg``) - a generalisation of genesis
    proximity that, unlike comparing raw genesis POINTS, correctly groups a late-
    forming member whose first fix is mid-track (the system has already moved). When
    two tracks never share a valid time, fall back to genesis-point proximity so a
    dissipate-then-reform member still seeds with its system. Coarse: distinct
    systems fall in separate seeds; HDBSCAN then refines WITHIN each seed."""
    n = len(tracks)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    gen = [_genesis(t["fixes"]) for t in tracks]
    for i in range(n):
        for j in range(i + 1, n):
            d = _track_distance(tracks[i]["fixes"], tracks[j]["fixes"])
            if d < _BIG:
                if d <= overlap_deg:
                    union(i, j)
            elif gc_deg(gen[i][0], gen[i][1], gen[j][0], gen[j][1]) <= radius_deg:
                union(i, j)
    groups: Dict[int, List[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _hdbscan_labels(D: np.ndarray, min_cluster_size: int, min_samples: int,
                    merge_eps: float = CLUSTER_MERGE_EPS_DEG) -> np.ndarray:
    """HDBSCAN on a precomputed distance matrix, with a DBSCAN-like
    ``cluster_selection_epsilon`` so a coherent system is not shattered into
    sub-clusters. Prefers the standalone ``hdbscan`` package, then sklearn's
    HDBSCAN; if neither is importable, falls back to a single-linkage threshold so
    the pipeline still produces clusters (degraded but never a hard failure)."""
    n = D.shape[0]
    mcs = max(2, min(min_cluster_size, n))
    D = D.astype(np.float64)
    try:
        import hdbscan  # type: ignore
        lab = hdbscan.HDBSCAN(min_cluster_size=mcs, min_samples=min_samples,
                              metric="precomputed", cluster_selection_method="eom",
                              cluster_selection_epsilon=float(merge_eps)
                              ).fit_predict(D)
        return np.asarray(lab)
    except Exception:  # noqa: BLE001
        pass
    try:
        from sklearn.cluster import HDBSCAN  # sklearn >= 1.3
        lab = HDBSCAN(min_cluster_size=mcs, min_samples=min_samples,
                      metric="precomputed", cluster_selection_method="eom",
                      cluster_selection_epsilon=float(merge_eps), copy=True
                      ).fit_predict(D)
        return np.asarray(lab)
    except Exception:  # noqa: BLE001
        return _single_linkage_labels(D, mcs, thresh_deg=max(4.0, merge_eps))


def _single_linkage_labels(D: np.ndarray, min_cluster_size: int,
                           thresh_deg: float = 4.0) -> np.ndarray:
    """numpy-only fallback: connected components under a mean-separation threshold,
    components smaller than ``min_cluster_size`` marked noise (-1)."""
    n = D.shape[0]
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if D[i, j] <= thresh_deg:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    comp: Dict[int, List[int]] = {}
    for i in range(n):
        comp.setdefault(find(i), []).append(i)
    labels = np.full(n, -1, dtype=int)
    lab = 0
    for members in comp.values():
        if len(members) >= min_cluster_size:
            for i in members:
                labels[i] = lab
            lab += 1
    return labels


def _intra_median(D: np.ndarray, members: List[int]) -> float:
    if len(members) < 2:
        return 0.0
    vals = [D[a, b] for i, a in enumerate(members) for b in members[i + 1:] if D[a, b] < _BIG]
    return float(np.median(vals)) if vals else _BIG


def _inter_mean(D: np.ndarray, A: List[int], B: List[int]) -> float:
    vals = [D[a, b] for a in A for b in B if D[a, b] < _BIG]
    return float(np.mean(vals)) if vals else _BIG


def _refine_seed(D: np.ndarray, labels: np.ndarray) -> List[List[int]]:
    """Turn HDBSCAN labels on one seed into final clusters: scale-adaptively merge
    same-system sub-clusters, then re-absorb noise tracks that fall within a
    cluster's spread. Returns lists of LOCAL (seed) indices."""
    groups: Dict[int, List[int]] = {}
    noise: List[int] = []
    for i, l in enumerate(labels):
        if l < 0:
            noise.append(i)
        else:
            groups.setdefault(int(l), []).append(i)
    clusters = [sorted(v) for v in groups.values()]

    def merge_thr(a, b):
        return max(SAME_SYSTEM_DEG, MERGE_FACTOR * max(_intra_median(D, a),
                                                       _intra_median(D, b), MERGE_FLOOR_DEG))

    # agglomeratively merge the closest pair while they are within same-system
    # range; stop when the closest pair is a genuinely distinct (divergent) system.
    changed = True
    while changed and len(clusters) > 1:
        changed = False
        best = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                im = _inter_mean(D, clusters[i], clusters[j])
                if im < merge_thr(clusters[i], clusters[j]) and (best is None or im < best[0]):
                    best = (im, i, j)
        if best:
            _, i, j = best
            clusters[i] = sorted(clusters[i] + clusters[j])
            del clusters[j]
            changed = True

    # re-absorb noise tracks within same-system range of a cluster (orphans HDBSCAN
    # dropped from a fanned blob); a genuine far outlier stays noise (dropped).
    for t in noise:
        cand = None
        for ci, c in enumerate(clusters):
            m = _inter_mean(D, [t], c)
            thr = max(SAME_SYSTEM_DEG, MERGE_FACTOR * max(_intra_median(D, c), MERGE_FLOOR_DEG))
            if m < thr and (cand is None or m < cand[0]):
                cand = (m, ci)
        if cand:
            clusters[cand[1]].append(t)
    return [sorted(c) for c in clusters]


def cluster_tracks(tracks: List[dict], n_members: int, *,
                   genesis_radius_deg: float = GENESIS_RADIUS_DEG,
                   min_cluster_frac: float = MIN_CLUSTER_FRAC,
                   min_samples: int = MIN_SAMPLES) -> List[List[int]]:
    """Group member tracks into per-system clusters. ``tracks`` is a flat list of
    ``{"member": id, "fixes": [...]}``. Returns a list of clusters, each a list of
    indices into ``tracks``. Distinct systems separate (genesis seeds + HDBSCAN);
    spurious one-off / far-outlier tracks land in noise and are omitted; a coherent
    system stays one cluster (post-HDBSCAN merge + noise re-absorption). Clusters
    are ordered most-populated first.
    """
    if not tracks:
        return []
    nominal = max(2, round(min_cluster_frac * max(1, n_members)))
    clusters: List[List[int]] = []
    for seed in _seed_groups(tracks, genesis_radius_deg):
        m = len(seed)
        if m < 2:
            continue                                # lone track -> spurious, drop
        D = np.zeros((m, m), dtype=float)
        for a in range(m):
            for b in range(a + 1, m):
                d = _track_distance(tracks[seed[a]]["fixes"], tracks[seed[b]]["fixes"])
                D[a, b] = D[b, a] = d
        labels = _hdbscan_labels(D, nominal, min_samples)
        if any(l >= 0 for l in labels):
            for local in _refine_seed(D, labels):
                clusters.append([seed[i] for i in local])
        else:
            # HDBSCAN found no core cluster. Rescue a small-but-coherent seed (a
            # real system supported by few members) as one low-confidence cluster;
            # an incoherent seed is genuinely spurious and stays dropped.
            finite = D[np.isfinite(D) & (D < _BIG)]
            if m >= 3 and finite.size and float(np.median(finite)) <= 1.5 * genesis_radius_deg:
                clusters.append(list(seed))
    clusters.sort(key=lambda idx: len({tracks[i]["member"] for i in idx}), reverse=True)
    return clusters


# ===========================================================================
# Stage C - derived products (per cluster, per lead, members present only)
# ===========================================================================
def _cluster_member_tracks(tracks: List[dict], idx: List[int]) -> Dict[str, List[list]]:
    """One representative track per member in the cluster (the longest, if a member
    contributed more than one)."""
    best: Dict[str, List[list]] = {}
    for i in idx:
        mid = tracks[i]["member"]
        cur = best.get(mid)
        if cur is None or len(tracks[i]["fixes"]) > len(cur):
            best[mid] = tracks[i]["fixes"]
    return best


def _by_lead(member_tracks: Dict[str, List[list]]) -> Dict[int, List[list]]:
    """lead step -> list of member center rows present at that lead."""
    out: Dict[int, List[list]] = {}
    for fixes in member_tracks.values():
        for r in fixes:
            out.setdefault(int(r[0]), []).append(r)
    return out


# --- ensemble-mean-track quality knobs (Stage C) ---------------------------
# The mean line must read as ONE clean system path, not per-step jitter that whips
# to an outlier wherever a cluster's members have fanned out or dissipated to a
# handful. Two defences, applied to the robust per-lead geometric medians:
#   * SUPPORT TRIM: keep only the contiguous span where the per-lead member backing
#     stays >= floor, so the sparse genesis ramp and the divergent long-range tail
#     (a 1-2 member "median" = a single outlier) are dropped, not drawn.
#   * SMOOTHING: a light [1,2,1]/4 pass (x2) on the kept lat / unwrapped-lon series.
MEAN_SUPPORT_MIN_ABS = 4          # never anchor a mean point on < this many members
MEAN_SUPPORT_PEAK_FRAC = 0.25     # ...or on < this fraction of the cluster's PEAK support


def _smooth_series(vals: Sequence[float], passes: int = 2) -> List[float]:
    """Light [1,2,1]/4 smoothing with clamped ends, applied ``passes`` times.
    Preserves the track shape while removing per-step jitter; a no-op for < 3
    points (a 2-point line is already straight)."""
    v = [float(x) for x in vals]
    n = len(v)
    if n < 3:
        return v
    for _ in range(passes):
        out = v[:]
        for i in range(1, n - 1):
            out[i] = 0.25 * v[i - 1] + 0.5 * v[i] + 0.25 * v[i + 1]
        v = out
    return v


def mean_track(member_tracks: Dict[str, List[list]]) -> List[list]:
    """Robust spherical ensemble-mean track: per lead time present, the geometric
    median of the member positions (outlier-resistant), restricted to the contiguous
    well-supported span and lightly smoothed so the line never whips to a thin-support
    outlier. Each point carries its supporting member count. Display lons are unwrapped
    to a continuous sequence (smoothing runs on the unwrapped lons, dateline-safe)."""
    bl = _by_lead(member_tracks)
    raw = []
    for s in sorted(bl):
        rows = bl[s]
        la, lo = geometric_median([(r[1], r[2]) for r in rows])
        raw.append([s, la, lo, len(rows)])
    if not raw:
        return []
    # support trim: keep the contiguous run between the first and last lead whose
    # member backing clears the floor (interior dips stay - a system's support is
    # unimodal in lead, so this only trims the sparse ends, never punches holes).
    peak = max(p[3] for p in raw)
    floor = max(MEAN_SUPPORT_MIN_ABS, int(round(MEAN_SUPPORT_PEAK_FRAC * peak)))
    idx = [i for i, p in enumerate(raw) if p[3] >= floor]
    kept = raw[idx[0]:idx[-1] + 1] if idx else raw   # all-thin cluster: keep as-is
    ulons = unwrap_lons([p[2] for p in kept])
    lat_s = _smooth_series([p[1] for p in kept])
    lon_s = _smooth_series(ulons)
    return [[p[0], round(la, 3), round(lo, 3), p[3]]
            for p, la, lo in zip(kept, lat_s, lon_s)]


_PCTS = [10, 25, 50, 75, 90]
MIN_PLUME_SUPPORT = 3             # drop leads backed by < this many members (the 1-2
                                  # member tail whose min/max/percentiles are pure noise)


def intensity_plume(member_tracks: Dict[str, List[list]]) -> dict:
    """p10/p25/p50/p75/p90 + min/max of Vmax AND MSLP (separately) by lead. Leads with
    fewer than ``MIN_PLUME_SUPPORT`` members are dropped so the spread band and the
    min/max bounds don't spike on a 1-2 member tail (matches the mean-track trim)."""
    bl = _by_lead(member_tracks)
    leads = [s for s in sorted(bl) if len(bl[s]) >= MIN_PLUME_SUPPORT]

    def series(col: int) -> dict:
        out = {"lead": [], "p10": [], "p25": [], "p50": [], "p75": [], "p90": [],
               "min": [], "max": [], "n": []}
        for s in leads:
            vals = [r[col] for r in bl[s] if r[col] is not None]
            if not vals:
                continue
            arr = np.asarray(vals, float)
            pc = np.percentile(arr, _PCTS)
            out["lead"].append(s)
            for k, p in zip(("p10", "p25", "p50", "p75", "p90"), pc):
                out[k].append(round(float(p), 1))
            out["min"].append(round(float(arr.min()), 1))
            out["max"].append(round(float(arr.max()), 1))
            out["n"].append(len(vals))
        return out

    return {"vmax": series(4), "mslp": series(3)}


def _cov_ellipse(positions: List[Tuple[float, float]]) -> Optional[dict]:
    """50%/90% bivariate position covariance ellipses from member positions at one
    lead, on a local tangent plane centred at the spherical mean. Returns the mean,
    the 2x2 covariance (km^2, for an obs z-score), each ellipse's semi-axes (km) +
    bearing of the major axis, and dateline-safe lat/lon polygons."""
    if len(positions) < 3:
        return None
    mlat, mlon = spherical_mean(positions)
    xy = np.array([_local_xy_km(la, lo, mlat, mlon) for la, lo in positions])
    cov = np.cov(xy.T)                              # 2x2 km^2
    if not np.all(np.isfinite(cov)):
        return None
    vals, vecs = np.linalg.eigh(cov)
    vals = np.maximum(vals, 0.0)
    order = np.argsort(vals)[::-1]
    vals, vecs = vals[order], vecs[:, order]
    major = vecs[:, 0]
    bearing = (math.degrees(math.atan2(major[0], major[1]))) % 180.0  # axis, compass

    def ellipse(p: float) -> dict:
        # Mahalanobis radius for bivariate-normal coverage p: r = sqrt(-2 ln(1-p))
        r = math.sqrt(-2.0 * math.log(1.0 - p))
        a_km = math.sqrt(vals[0]) * r
        b_km = math.sqrt(vals[1]) * r
        poly = []
        for k in range(24):
            th = 2 * math.pi * k / 24
            off = (vecs[:, 0] * (a_km * math.cos(th)) + vecs[:, 1] * (b_km * math.sin(th)))
            la, lo = _xy_km_to_latlon(off[0], off[1], mlat, mlon)
            poly.append([round(la, 3), lo])
        ulon = unwrap_lons([q[1] for q in poly])
        for q, lo in zip(poly, ulon):
            q[1] = round(lo, 3)
        return {"a_km": round(a_km, 1), "b_km": round(b_km, 1),
                "bearing_deg": round(bearing, 1), "poly": poly}

    return {"mean_lat": round(mlat, 3), "mean_lon": round(mlon, 3),
            "cov_km": [[round(float(cov[0, 0]), 2), round(float(cov[0, 1]), 2)],
                       [round(float(cov[1, 0]), 2), round(float(cov[1, 1]), 2)]],
            "ell50": ellipse(0.50), "ell90": ellipse(0.90)}


def track_envelope(member_tracks: Dict[str, List[list]]) -> List[dict]:
    """Per-lead covariance-ellipse envelope chained into a swath. Leads with < 3
    members carry only the mean + count (no ellipse)."""
    bl = _by_lead(member_tracks)
    env = []
    for s in sorted(bl):
        rows = bl[s]
        positions = [(r[1], r[2]) for r in rows]
        ell = _cov_ellipse(positions)
        if ell is None:
            mlat, mlon = spherical_mean(positions)
            env.append({"step": s, "n": len(rows),
                        "mean_lat": round(mlat, 3), "mean_lon": round(mlon, 3)})
        else:
            env.append({"step": s, "n": len(rows), **ell})
    return env


def obs_support(member_tracks: Dict[str, List[list]], obs_lat: float, obs_lon: float,
                valid_step: int) -> Optional[dict]:
    """OBS-vs-ENVELOPE helper (the viewer overlay is the follow-up; this returns the
    numbers). Given a current observed position and its valid lead, find the nearest
    lead with members and return the obs's percentile rank within that lead's member
    distance-from-mean distribution + a spread-normalised offset (Mahalanobis z on
    the position covariance)."""
    bl = _by_lead(member_tracks)
    if not bl:
        return None
    lead = min(bl, key=lambda s: abs(s - valid_step))
    rows = bl[lead]
    positions = [(r[1], r[2]) for r in rows]
    mlat, mlon = spherical_mean(positions)
    md = [gc_km(mlat, mlon, la, lo) for la, lo in positions]
    od = gc_km(mlat, mlon, obs_lat, obs_lon)
    pct = 100.0 * sum(1 for d in md if d <= od) / len(md)
    maha = None
    if len(positions) >= 3:
        xy = np.array([_local_xy_km(la, lo, mlat, mlon) for la, lo in positions])
        cov = np.cov(xy.T)
        try:
            inv = np.linalg.inv(cov)
            v = np.array(_local_xy_km(obs_lat, obs_lon, mlat, mlon))
            maha = float(math.sqrt(max(0.0, v @ inv @ v)))
        except np.linalg.LinAlgError:
            maha = None
    return {"valid_step": valid_step, "matched_lead": lead, "n": len(rows),
            "percentile": round(pct, 1), "offset_km": round(od, 1),
            "mahalanobis": None if maha is None else round(maha, 2)}


# ===========================================================================
# Stage A/B/C assembly + Stage D emit
# ===========================================================================
def _spacing_from_steps(run_steps: Sequence[int]) -> float:
    diffs = [b - a for a, b in zip(run_steps, run_steps[1:]) if b > a]
    return float(min(diffs)) if diffs else 6.0


def _display_track(fixes: Sequence[Sequence]) -> List[list]:
    """A member track with display-unwrapped longitudes, rounded for compactness."""
    ulon = unwrap_lons([r[2] for r in fixes])
    return [[int(r[0]), round(r[1], 2), round(lo, 2),
             None if r[3] is None else round(r[3], 1),
             None if r[4] is None else round(r[4], 1)]
            for r, lo in zip(fixes, ulon)]


def assemble_tracks_doc(per_member_tracks: Dict[str, List[List[list]]], *,
                        spec, cycle: dt.datetime, n_members: int, spacing_h: float,
                        source_kind: str) -> dict:
    """Stage B + C + D assembly: cluster the per-member tracks, derive products, and
    return the enriched-JSON dict. ``per_member_tracks`` maps member id -> list of
    that member's tracks (each a list of center rows), already linked (Stage A for
    self-detected, native track_id for fnv3/genc)."""
    flat: List[dict] = []
    members_out = []
    for mid in sorted(per_member_tracks):
        mtracks = per_member_tracks[mid]
        members_out.append({"id": mid, "tracks": [_display_track(t) for t in mtracks]})
        for t in mtracks:
            if len(t) >= 2:
                flat.append({"member": mid, "fixes": [list(r) for r in t]})

    cluster_idx = cluster_tracks(flat, n_members)
    clusters_out = []
    for ci, idx in enumerate(cluster_idx):
        mt = _cluster_member_tracks(flat, idx)
        member_ids = sorted(mt)
        member_count = len(member_ids)
        population = len(idx)                       # tracks in the cluster
        nominal = max(2, round(MIN_CLUSTER_FRAC * max(1, n_members)))
        # PEAK simultaneous support: the most members agreeing on this system at ANY
        # one lead. member_count (cumulative, members come and go) overstates a noisy
        # scatter cluster that never actually consensus-formed; peak_support is the
        # honest "how many members ever agreed at once" - so a cluster that many
        # members merely brushed is now correctly de-emphasised as low-confidence.
        peak_support = max((len(r) for r in _by_lead(mt).values()), default=0)
        gla, glo, gstep = _genesis(min((flat[i]["fixes"] for i in idx),
                                       key=lambda f: f[0][0]))
        clusters_out.append({
            "id": ci,
            "members": member_ids,
            "member_count": member_count,
            "peak_support": peak_support,
            "coverage_fraction": round(member_count / max(1, n_members), 3),
            "population": population,
            "low_confidence": member_count < nominal or peak_support < nominal,
            "genesis": {"lat": round(gla, 2), "lon": round(glo, 2), "step": int(gstep)},
            "mean_track": mean_track(mt),
            "plume": intensity_plume(mt),
            "envelope": track_envelope(mt),
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "model": spec.slug,
        "model_label": spec.label,
        "init_time": cycle.replace(tzinfo=None).isoformat() + "Z",
        "init_cycle": f"{cycle:%Y%m%d%H}",
        "generated_at": (dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
                         .isoformat().replace("+00:00", "Z")),
        "source_kind": source_kind,
        "spacing_h": spacing_h,
        "n_members": n_members,
        "n_member_tracks": len(flat),
        "n_clusters": len(clusters_out),
        "members": members_out,
        "clusters": clusters_out,
    }


def per_member_tracks_from_centers(centers_doc: dict) -> Tuple[Dict[str, List[List[list]]], float]:
    """Stage A driver for self-detected models: link each member's per-step centers
    into tracks. Returns (per_member_tracks, spacing_h)."""
    spacing = _spacing_from_steps(centers_doc.get("run_steps") or [0, 6])
    out: Dict[str, List[List[list]]] = {}
    for m in centers_doc.get("members", []):
        out[m["id"]] = link_tracks(m.get("centers", []), spacing)
    return out, spacing


def build_tracks_for_cycle(spec, cycle: dt.datetime, out_dir: str, *,
                           cycle_path: Optional[str] = None,
                           native_member_tracks: Optional[Dict[str, List[List[list]]]] = None,
                           progress=print) -> dict:
    """Build + write ``{slug}/{cycle}.tracks.json`` for ONE cycle and return a small
    summary (incl. ``generated_at`` for the manifest cache-bust token). Self-detected
    models run Stage A from the just-written centers JSON; native models skip Stage A
    and use the handed-in per-member ``track_id`` grouping."""
    source_kind = getattr(spec, "source_kind", "self_detect")
    if native_member_tracks is not None:
        pmt = native_member_tracks
        # native CSVs are 6-hourly; derive spacing from the data when possible
        all_steps = sorted({int(r[0]) for ts in pmt.values() for t in ts for r in t})
        spacing = _spacing_from_steps(all_steps) if len(all_steps) > 1 else 6.0
        n_members = len(pmt)
    else:
        path = cycle_path or os.path.join(out_dir, spec.slug, f"{cycle:%Y%m%d%H}.json")
        with open(path) as f:
            centers_doc = json.load(f)
        pmt, spacing = per_member_tracks_from_centers(centers_doc)
        n_members = int(centers_doc.get("n_members") or len(pmt))

    doc = assemble_tracks_doc(pmt, spec=spec, cycle=cycle, n_members=n_members,
                              spacing_h=spacing, source_kind=source_kind)

    model_dir = os.path.join(out_dir, spec.slug)
    os.makedirs(model_dir, exist_ok=True)
    stamp = f"{cycle:%Y%m%d%H}"
    tpath = os.path.join(model_dir, f"{stamp}.tracks.json")
    with open(tpath, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    nbytes = os.path.getsize(tpath)
    progress(f"[{spec.slug}] wrote {tpath} ({nbytes/1e6:.2f} MB), "
             f"{doc['n_member_tracks']} member-tracks, {doc['n_clusters']} cluster(s)")
    return {"cycle": stamp, "generated_at": doc["generated_at"],
            "tracks_path": tpath, "n_clusters": doc["n_clusters"],
            "n_member_tracks": doc["n_member_tracks"], "bytes_json": nbytes}


def wrap_ingest(ingest_fn, spec, out_dir: str, *, progress=print):
    """Wrap a per-cycle ``ingest_cycle`` hook so that, AFTER the lean centers JSON is
    written, the sibling tracks JSON is built and its cache-bust token attached to
    the result (``tracks_generated_at``). A tracks-build failure is logged and
    swallowed - the centers publish must never be blocked by the additive layer."""
    def wrapped(cycle: dt.datetime) -> dict:
        res = ingest_fn(cycle)
        try:
            tr = build_tracks_for_cycle(
                spec, cycle, out_dir,
                cycle_path=res.get("cycle_path"),
                native_member_tracks=res.get("native_member_tracks"),
                progress=progress)
            res["tracks_generated_at"] = tr["generated_at"]
            res["n_clusters"] = tr["n_clusters"]
        except Exception as e:  # noqa: BLE001 - additive; never block centers
            progress(f"[{spec.slug}] WARN: tracks build failed for "
                     f"{cycle:%Y%m%d%H} ({e}); centers still published")
        return res
    return wrapped
