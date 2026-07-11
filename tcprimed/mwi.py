"""tcprimed.mwi - MW-imager objective intensity: shared predictor extraction.

This module is the SINGLE extraction path for the MW-imager intensity member
(consensus member #2 of the TC-Diagnostics objective intensity consensus).
The SAME functions run in BOTH places, so there is no train/serve skew:
  * offline training (tcprimed.mwi_train): TC-PRIMED archive overpasses ->
    ring/sector PCT statistics + best-track targets -> a fitted, versioned
    coefficients JSON (committed to the repo with provenance);
  * the CI cron (tcprimed.build / build_live): the same statistics from the
    same storm-centered regrid -> predictors -> the committed model ->
    intensity{vmax_kt, ...} written into each overpass record.

PROVENANCE - methods from primary literature (read 2026-07-11; the SATCON
consensus layer cites its own sources in satellite/explorer/satcon.js):
  Spencer, Goodman & Hood 1989 (JTECH 6, 254-273): the polarization-corrected
    temperature, PCT85 = 1.818*Tb_V - 0.818*Tb_H (their eq. 4 coefficient for
    85.5 GHz) - removes the surface-emissivity (ocean V/H) split so cold PCT
    isolates ice scattering.
  Cecil & Chronis 2018 (JAMC 57, 2249-2259): frequency-specific PCT
    coefficients; for 85-92 GHz their Table 1 recommends beta ~ 0.7
    (PCT89 = 1.7*V - 0.7*H); for 36-37 GHz beta ~ 1.15-1.18
    (PCT37 = 2.15*V - 1.15*H canonical after Cecil et al. 2002).
  Cecil & Zipser 1999 (MWR 127, 103-123): 85-GHz ice-scattering statistics
    (min PCT, fractional area colder than thresholds) inside a 1-degree-radius
    inner core correlate with CURRENT intensity (their Table 2: r ~ 0.5-0.55
    for inner-core min PCT / cold fractional area) - the basis for using
    inner-core PCT statistics as intensity predictors.
  Jones, Cecil & DeMaria 2006 (WAF 21, 613-635; SHIPS-MI): operational use of
    85-92 GHz PCT spatial statistics (mean/min PCT and cold-pixel fractions in
    fixed radial bands about the interpolated center; their predictors use
    0-100 km and 0-300 km annuli) with land + coverage screening.
  Kieper & Jiang 2012 (GRL 39, L13804): the 37-GHz "ring" - a closed/nearly
    closed ring of warm-rain signal (cyan, PCT37 >= ~260 K AND Tb37H
    depression) around the center precedes/accompanies intensification;
    operationalized here as azimuthal closure fractions of the 37-GHz
    warm-rain + convective classes in an eyewall-scale annulus.

DESIGN NOTES (documented departures, none silent):
  n1 GRID: the swath is cropped (pps.crop_swath) then resampled with
     render._regrid (Delaunay linear + the same swath-edge mask the display
     tiles use) onto a storm-centered lat/lon grid, half=3.8 deg, step=0.04 deg
     (~4.4 km, ~ the 89-GHz footprint scale). Identical in training + runtime.
  n2 RING/SECTOR SUPERSET: extraction stores min/sum/count per 5-km ring x
     15-deg sector for FOUR PCT variants (both literature coefficient choices
     per band). The fitted model selects ONE variant per band; the superset
     exists so re-fitting never requires re-downloading 500 GB of swaths.
  n3 CENTER: the best-track-interpolated center (training) / the live-feed
     center (runtime) - the same official-track anchor convention as the
     objfix first guess. No MW re-centering in v1 (ARCHER-MW is future work).
  n4 DISTANCES: flat-earth km with cos(center lat) zonal scaling and
     111.32 km/deg on both axes - the diag_core.js convention.
  n5 LAND: Natural Earth 50m country polygons (repo root geojson), rasterized
     per ring - NOT the TC-PRIMED per-pixel surface_type, because the live
     tier (PPS 1C) has no surface class; the NE mask is available in both.

The model itself (coefficients, validation, honesty tiers) lives in the
committed mwi_model JSON; apply_model() below is a dumb evaluator.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Optional

import numpy as np

from . import render as rnd
from . import pps

KM_PER_DEG = 111.32          # both axes; zonal additionally scaled by cos(clat) [n4]

# Extraction geometry [n1, n2] - LOCKED; changing any of these invalidates the
# training set (the model JSON records them and apply-time re-checks).
GRID_HALF_DEG = 3.8
GRID_STEP_DEG = 0.04
CROP_PAD_DEG = 4.2
RING_KM = 5.0
MAX_KM = 300.0
N_RINGS = int(MAX_KM / RING_KM)          # 60
N_SECTORS = 24                            # 15 deg each, math convention (E=0, CCW)

# PCT variants [header provenance]: name -> (v_coef, h_coef).
#   pct89  : Spencer et al. 1989 eq. 4 (85-GHz heritage; used by NRL/CIRA
#            89-GHz products and hafs_render.compute_pct89)
#   pct89cc: Cecil & Chronis 2018 Table 1 (89-91 GHz recommended)
#   pct37  : canonical 37-GHz (Cecil et al. 2002; the NRL color37 recipe uses
#            2.181/1.181 - within the published 1.15-1.18 beta range)
#   pct37cc: Cecil & Chronis 2018 Table 1 (36-37 GHz recommended)
PCT_VARIANTS = {
    "pct89":   ("89", 1.818, 0.818),
    "pct89cc": ("89", 1.700, 0.700),
    "pct37":   ("37", 2.181, 1.181),
    "pct37cc": ("37", 2.150, 1.150),
}
# Physical clip (K), after hafs_render.compute_pct89's NRL range guard.
PCT_CLIP_LO, PCT_CLIP_HI = 105.0, 320.0

# Raw-swath (native-footprint) minima radii, km.
RAW_MIN_RADII_KM = (25.0, 50.0, 75.0, 100.0, 150.0)


# ---------------------------------------------------------------------------
# PCT
# ---------------------------------------------------------------------------
def pct(v: np.ndarray, h: np.ndarray, v_coef: float, h_coef: float) -> np.ndarray:
    """Polarization-corrected temperature in KELVIN, NaN-safe, clipped to the
    physical range. v/h are Tb(K) arrays."""
    out = v_coef * np.asarray(v, dtype=float) - h_coef * np.asarray(h, dtype=float)
    return np.clip(out, PCT_CLIP_LO, PCT_CLIP_HI)


# ---------------------------------------------------------------------------
# Land mask from Natural Earth (repo-root geojson) [n5]
# ---------------------------------------------------------------------------
_LAND_PATHS_CACHE: Optional[list] = None


def _load_land_paths():
    """[(matplotlib Path (outer ring), (lon_min, lat_min, lon_max, lat_max))].
    Lon in -180..180 as shipped in the geojson. Outer rings only (lake holes
    are negligible for TC land gating). Cached per process."""
    global _LAND_PATHS_CACHE
    if _LAND_PATHS_CACHE is not None:
        return _LAND_PATHS_CACHE
    from matplotlib.path import Path as MplPath
    geo = rnd._load_geojson("ne_50m_admin_0_countries.geojson")
    paths = []
    if geo:
        for feat in geo.get("features", []):
            geom = feat.get("geometry") or {}
            if geom.get("type") == "Polygon":
                polys = [geom["coordinates"]]
            elif geom.get("type") == "MultiPolygon":
                polys = geom["coordinates"]
            else:
                continue
            for poly in polys:
                if not poly or len(poly[0]) < 3:
                    continue
                ring = np.asarray(poly[0], dtype=float)     # outer ring only
                paths.append((MplPath(ring),
                              (ring[:, 0].min(), ring[:, 1].min(),
                               ring[:, 0].max(), ring[:, 1].max())))
    _LAND_PATHS_CACHE = paths
    return paths


def land_mask_grid(gx_lon: np.ndarray, gy_lat: np.ndarray) -> np.ndarray:
    """Boolean (ny, nx) land mask for grid cell centers. gx_lon is the
    CENTER-UNWRAPPED display lon (may exceed +/-180); tested against the
    polygons in both the -180..180 wrap and the +/-360 aliases so dateline
    boxes work."""
    GX, GY = np.meshgrid(gx_lon, gy_lat)
    pts_lat = GY.ravel()
    mask = np.zeros(pts_lat.shape[0], dtype=bool)
    lon_range = (float(gx_lon.min()), float(gx_lon.max()))
    lat_range = (float(gy_lat.min()), float(gy_lat.max()))
    for alias in (0.0, 360.0, -360.0):
        lo, hi = lon_range[0] + alias, lon_range[1] + alias
        if hi < -180.0 or lo > 180.0:
            continue
        pts = np.column_stack([GX.ravel() + alias, pts_lat])
        for path, (bx0, by0, bx1, by1) in _load_land_paths():
            if bx1 < lo or bx0 > hi or by1 < lat_range[0] or by0 > lat_range[1]:
                continue
            sub = ((pts[:, 0] >= bx0) & (pts[:, 0] <= bx1) &
                   (pts[:, 1] >= by0) & (pts[:, 1] <= by1) & ~mask)
            if not sub.any():
                continue
            mask[np.where(sub)[0][path.contains_points(pts[sub])]] = True
    return mask.reshape(GY.shape)


# ---------------------------------------------------------------------------
# Ring / sector statistics (the extraction superset) [n2]
# ---------------------------------------------------------------------------
def _grid_geometry(clat: float, clon: float):
    """The storm-centered analysis grid axes (display/center-unwrapped lon)."""
    n = int(round(2.0 * GRID_HALF_DEG / GRID_STEP_DEG)) + 1
    gx = np.linspace(clon - GRID_HALF_DEG, clon + GRID_HALF_DEG, n)
    gy = np.linspace(clat - GRID_HALF_DEG, clat + GRID_HALF_DEG, n)
    return gx, gy


def _ring_sector_index(gx, gy, clat, clon):
    """(ring_idx, sector_idx, inside) int arrays for the grid; ring/sector per
    the locked geometry, inside = r < MAX_KM. Azimuth math-convention [n4]."""
    GX, GY = np.meshgrid(gx, gy)
    cosc = math.cos(math.radians(clat))
    dx = (GX - clon) * KM_PER_DEG * cosc
    dy = (GY - clat) * KM_PER_DEG
    r = np.hypot(dx, dy)
    ring = np.minimum((r / RING_KM).astype(int), N_RINGS)   # == N_RINGS -> outside
    az = np.degrees(np.arctan2(dy, dx)) % 360.0
    sector = np.minimum((az / (360.0 / N_SECTORS)).astype(int), N_SECTORS - 1)
    return ring, sector, (r < MAX_KM)


def ring_sector_stats(meta: dict, *, bands=("89", "37")) -> Optional[dict]:
    """The extraction superset for one overpass.

    meta: the render.read_overpass / build_live dict (lat{band}/lon{band}/
    tb{band}v/tb{band}h + clat/clon). Returns None when neither band's swath
    covers the storm box. Output (all numpy, compact dtypes):
      grid: {half_deg, step_deg, ring_km, n_rings, n_sectors}
      land_frac_ring: (N_RINGS,) f4 - NE land fraction of each ring's cells
      per variant in PCT_VARIANTS (whose band was available):
        {name}_min:  (N_RINGS, N_SECTORS) f4, NaN where no valid cell
        {name}_sum:  (N_RINGS, N_SECTORS) f4
        {name}_cnt:  (N_RINGS, N_SECTORS) i4   valid-cell count
      per band: {band}_tot: (N_RINGS, N_SECTORS) i4  cell count inside swath
                box (denominator for coverage, land included)
      raw_min: {variant: (len(RAW_MIN_RADII_KM),) f4} native-footprint minima
    """
    clat, clon = float(meta["clat"]), float(meta["clon"])
    gx, gy = _grid_geometry(clat, clon)
    ring, sector, inside = _ring_sector_index(gx, gy, clat, clon)
    flat_rs = (ring * N_SECTORS + sector)[inside]
    nbins = N_RINGS * N_SECTORS

    land = land_mask_grid(gx, gy)
    land_ring = np.bincount(ring[inside], weights=land[inside].astype(float),
                            minlength=N_RINGS)
    ring_tot_cells = np.bincount(ring[inside], minlength=N_RINGS)
    out = {
        "grid": {"half_deg": GRID_HALF_DEG, "step_deg": GRID_STEP_DEG,
                 "ring_km": RING_KM, "n_rings": N_RINGS,
                 "n_sectors": N_SECTORS},
        "land_frac_ring": (land_ring / np.maximum(ring_tot_cells, 1)
                           ).astype(np.float32),
        "raw_min": {},
    }

    got_band = False
    for band in bands:
        latk, lonk = f"lat{band}", f"lon{band}"
        vk, hk = f"tb{band}v", f"tb{band}h"
        if latk not in meta or not np.isfinite(meta[vk]).any() \
                or not np.isfinite(meta[hk]).any():
            continue
        crop = pps.crop_swath(meta[latk], meta[lonk], meta[vk], meta[hk],
                              clat, clon, pad=CROP_PAD_DEG)
        if crop is None:
            continue
        cla, clo, cv, ch = crop
        try:
            _, (gv, gh) = rnd._regrid(cla, clo, [cv, ch], clat, clon,
                                      half=GRID_HALF_DEG, step=GRID_STEP_DEG)
        except ValueError:
            continue
        got_band = True
        valid_any = np.isfinite(gv) & np.isfinite(gh)
        out[f"{band}_tot"] = np.bincount(
            flat_rs, minlength=nbins).astype(np.int32).reshape(N_RINGS, N_SECTORS)

        # raw-swath native-footprint minima (per variant) [Cecil & Zipser 1999]
        cosc = math.cos(math.radians(clat))
        rlon = np.asarray(clo, dtype=float).copy()
        d = rlon - clon
        rlon[d > 180.0] -= 360.0
        rlon[d < -180.0] += 360.0
        rr = np.hypot((np.asarray(cla, float) - clat) * KM_PER_DEG,
                      (rlon - clon) * KM_PER_DEG * cosc)

        for name, (vb, vc, hc) in PCT_VARIANTS.items():
            if vb != band:
                continue
            p = pct(gv, gh, vc, hc)
            sel = np.isfinite(p) & valid_any & inside
            fsel = (ring * N_SECTORS + sector)[sel]
            pv = p[sel]
            cnt = np.bincount(fsel, minlength=nbins).astype(np.int32)
            ssum = np.bincount(fsel, weights=pv, minlength=nbins)
            mn = np.full(nbins, np.nan)
            if fsel.size:
                order = np.argsort(fsel, kind="stable")
                fs, ps = fsel[order], pv[order]
                starts = np.searchsorted(fs, np.arange(nbins), side="left")
                ends = np.searchsorted(fs, np.arange(nbins), side="right")
                nz = np.where(ends > starts)[0]
                mn[nz] = np.minimum.reduceat(ps, starts[nz])
            out[f"{name}_min"] = mn.astype(np.float32).reshape(N_RINGS, N_SECTORS)
            out[f"{name}_sum"] = ssum.astype(np.float32).reshape(N_RINGS, N_SECTORS)
            out[f"{name}_cnt"] = cnt.reshape(N_RINGS, N_SECTORS)

            praw = pct(cv, ch, vc, hc)
            rawmins = []
            for rad in RAW_MIN_RADII_KM:
                sel_r = (rr <= rad) & np.isfinite(praw)
                rawmins.append(float(np.nanmin(praw[sel_r])) if sel_r.any()
                               else np.nan)
            out["raw_min"][name] = np.asarray(rawmins, dtype=np.float32)

    return out if got_band else None


# ---------------------------------------------------------------------------
# Predictors from the superset (the model's feature vector)
# ---------------------------------------------------------------------------
def _ring_slice(stats, name, r0_km, r1_km):
    """(min_2d, sum_2d, cnt_2d, tot_2d) for rings covering [r0, r1) km."""
    i0, i1 = int(r0_km / RING_KM), int(r1_km / RING_KM)
    band = PCT_VARIANTS[name][0]
    return (stats[f"{name}_min"][i0:i1], stats[f"{name}_sum"][i0:i1],
            stats[f"{name}_cnt"][i0:i1], stats[f"{band}_tot"][i0:i1])


def _area_stats(stats, name, r0_km, r1_km):
    """(min, mean, coverage) over an annulus; NaN/0 when empty."""
    mn, sm, cn, tot = _ring_slice(stats, name, r0_km, r1_km)
    n = int(cn.sum())
    t = int(tot.sum())
    cov = n / t if t else 0.0
    if n == 0:
        return float("nan"), float("nan"), cov
    return float(np.nanmin(mn)), float(sm.sum() / n), cov


def _ring_closure(stats, name, r0_km, r1_km, thresh_k):
    """Fraction of the annulus' 15-deg sectors whose sector-min PCT is below
    thresh_k (azimuthal closure of a cold/warm-rain ring; sectors with no
    valid data do NOT count as closed). Also returns the fraction OBSERVED."""
    mn, _, cn, _ = _ring_slice(stats, name, r0_km, r1_km)
    sec_has = cn.sum(axis=0) > 0
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)   # all-NaN sectors
        sec_min = np.where(sec_has,
                           np.nanmin(np.where(cn > 0, mn, np.nan), axis=0),
                           np.nan)
    observed = float(sec_has.mean())
    if not sec_has.any():
        return 0.0, observed
    closed = np.nansum(sec_min[sec_has] < thresh_k) / N_SECTORS
    return float(closed), observed


def _coldest_ring_radius(stats, name, r0_km, r1_km):
    """Radius (km, ring center) of the coldest azimuthal-MEAN ring in
    [r0, r1); NaN if no ring has data."""
    i0, i1 = int(r0_km / RING_KM), int(r1_km / RING_KM)
    sm = stats[f"{name}_sum"][i0:i1].sum(axis=1)
    cn = stats[f"{name}_cnt"][i0:i1].sum(axis=1)
    with np.errstate(invalid="ignore"):
        mean = np.where(cn > 0, sm / np.maximum(cn, 1), np.nan)
    if not np.isfinite(mean).any():
        return float("nan")
    return float((np.nanargmin(mean) + i0 + 0.5) * RING_KM)


# The fitted feature set (locked by the model JSON's `predictors` list; this
# dict maps name -> extractor so training and runtime share ONE definition).
# Radii per Jones et al. 2006 (0-100 inner core / 100-300 environment bands)
# and Cecil & Zipser 1999 (~1-deg inner core); ring-closure annulus 20-80 km
# spans climatological eyewall radii; thresholds per the header provenance
# (PCT89 < 220 K deep convection; PCT37 < 260 K warm-rain/convective ring -
# the Kieper & Jiang cyan-ring boundary).
def compute_predictors(stats: dict) -> dict:
    """The full named predictor dict (superset; the model picks its subset).
    NaN values mean 'not observable in this overpass' - the gate decides."""
    p = {}
    for name in ("pct89", "pct37"):
        mn50, mean50, cov50 = _area_stats(stats, name, 0.0, 50.0)
        mn100, mean100, cov100 = _area_stats(stats, name, 0.0, 100.0)
        emn, emean, ecov = _area_stats(stats, name, 20.0, 80.0)
        omn, omean, ocov = _area_stats(stats, name, 100.0, 300.0)
        p[f"{name}_min50"] = mn50
        p[f"{name}_mean50"] = mean50
        p[f"{name}_min100"] = mn100
        p[f"{name}_mean100"] = mean100
        p[f"{name}_eyewall_min"] = emn
        p[f"{name}_eyewall_mean"] = emean
        p[f"{name}_outer_mean"] = omean
        p[f"{name}_cov100"] = cov100
        p[f"{name}_cov_eyewall"] = ecov
    c89, o89 = _ring_closure(stats, "pct89", 20.0, 80.0, 220.0)
    c37, o37 = _ring_closure(stats, "pct37", 20.0, 80.0, 260.0)
    p["ring89_closure"] = c89
    p["ring37_closure"] = c37
    p["ring37_flag"] = 1.0 if (c37 >= 0.90 and o37 >= 0.90) else 0.0
    p["cold_ring_radius89"] = _coldest_ring_radius(stats, "pct89", 10.0, 150.0)
    # land fractions for the gate [n5]
    lf = stats["land_frac_ring"]
    p["land_frac100"] = float(lf[: int(100 / RING_KM)].mean())
    p["land_frac300"] = float(lf.mean())
    rm = stats["raw_min"].get("pct89")
    p["pct89_rawmin100"] = float(rm[3]) if rm is not None else float("nan")
    rm37 = stats["raw_min"].get("pct37")
    p["pct37_rawmin100"] = float(rm37[3]) if rm37 is not None else float("nan")
    return p


# ---------------------------------------------------------------------------
# Model application (the committed coefficients JSON)
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
MODEL_PATH = _HERE / "mwi_model_v1.json"
_MODEL_CACHE: Optional[dict] = None


def load_model(path: Optional[str] = None) -> Optional[dict]:
    global _MODEL_CACHE
    if path is None and _MODEL_CACHE is not None:
        return _MODEL_CACHE
    p = Path(path) if path else MODEL_PATH
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        model = json.load(f)
    if path is None:
        _MODEL_CACHE = model
    return model


def quality_gate(predictors: dict, model: dict) -> tuple[bool, list[str]]:
    """(usable, reasons[]) - the coverage/land screen (Jones et al. 2006-style).
    Thresholds live in the model JSON so they version with the fit."""
    g = model.get("gate", {})
    reasons = []
    if predictors.get("pct89_cov100", 0.0) < g.get("min_cov100", 0.60):
        reasons.append("partial 89-GHz coverage of the inner core")
    if predictors.get("pct89_cov_eyewall", 0.0) < g.get("min_cov_eyewall", 0.60):
        reasons.append("partial eyewall-annulus coverage")
    if predictors.get("land_frac100", 1.0) > g.get("max_land_frac100", 0.25):
        reasons.append("inner core over land")
    needed = [k for k in model.get("predictors", []) if k != "intercept"]
    missing = [k for k in needed
               if not np.isfinite(predictors.get(k, float("nan")))]
    if missing:
        reasons.append("missing predictors: " + ", ".join(missing))
    return (len(reasons) == 0), reasons


def apply_model(predictors: dict, model: Optional[dict] = None) -> Optional[dict]:
    """predictors -> {vmax_kt, mslp_hpa, confidence, caveats, model_version} or
    None when no model is committed / the gate fails hard. Pure evaluation -
    all science lives in the fit (mwi_train) and the JSON."""
    model = model or load_model()
    if not model:
        return None
    ok, reasons = quality_gate(predictors, model)
    if not ok:
        return {"usable": False, "reasons": reasons,
                "model_version": model["version"]}

    def _linear(coefs: dict) -> float:
        v = coefs.get("intercept", 0.0)
        for k, c in coefs.items():
            if k == "intercept":
                continue
            v += c * float(predictors[k])
        return v

    vmax = _linear(model["vmax"])
    lo, hi = model.get("vmax_range", [15.0, 185.0])
    vmax = min(max(vmax, lo), hi)
    out = {
        "usable": True,
        "vmax_kt": round(vmax, 1),
        "model_version": model["version"],
    }
    if "mslp" in model:
        mp = _linear(model["mslp"])
        plo, phi = model.get("mslp_range", [880.0, 1015.0])
        out["mslp_hpa"] = round(min(max(mp, plo), phi), 1)
    # confidence tier from the validated by-bin error table
    err = model.get("error_by_bin") or []
    mae = None
    for row in err:
        if row["lo"] <= vmax < row["hi"]:
            mae = row["mae"]
            break
    out["mae_kt"] = mae
    out["confidence"] = ("moderate" if (mae is not None and mae <= model.get(
        "confidence_mae_cut", 13.0)) else "low")
    return out


def intensity_record(meta: dict, *, model: Optional[dict] = None,
                     source: str) -> Optional[dict]:
    """One-call runtime path: overpass meta -> the manifest intensity dict
    (or None: no model / no usable swath). Never raises - the MW render must
    not fail because the EXPERIMENTAL intensity member hiccupped."""
    try:
        model = model or load_model()
        if not model:
            return None
        stats = ring_sector_stats(meta)
        if stats is None:
            return None
        preds = compute_predictors(stats)
        est = apply_model(preds, model)
        if est is None:
            return None
        est["source"] = source
        est["sensor"] = meta.get("sensor")
        # the interpretable inputs ride along (honesty: show your work)
        est["predictors"] = {k: (round(float(preds[k]), 2)
                                 if np.isfinite(preds.get(k, float("nan")))
                                 else None)
                             for k in model.get("published_predictors",
                                                model.get("predictors", []))
                             if k != "intercept"}
        return est
    except Exception:  # noqa: BLE001 - additive feature, never sinks a render
        return None
