"""tcprimed.mwi_fit - fit + validate the MW-imager intensity model.

Consumes the extraction shards written by mwi_train (the ring/sector PCT
superset + best-track targets), derives the literature-guided predictor
table, fits an INTERPRETABLE multiple-linear model for Vmax (and MSLP),
validates leave-one-YEAR-out, and writes tcprimed/mwi_model_v1.json with
coefficients + gate + error tables + provenance.

METHOD PROVENANCE (beyond the mwi.py header; read 2026-07-11):
  Cecil & Zipser 1999 (MWR 127) Table 2 + Fig. 6: the 0-1 deg (0-100 km)
    areal-MEAN PCT85 and the fractional area PCT85 <= 250 K are the
    strongest PMW intensity relations (r = -0.54 vs concurrent Vmax for
    1-deg mean PCT; min-PCT/extreme-convection fractions are weaker) -
    so the mean-PCT and cold-area-fraction predictors lead the candidate
    list, ahead of minima.
  Jones, Cecil & DeMaria 2006 (WAF 21) Sec. 2d: all PMW predictors from the
    0-100 km disk (larger radii tested and DISCARDED); >=90% valid-ocean
    coverage gate; quadrant-mean symmetry (STDQM: stdev of the four
    quadrant means about the disk mean) as the resolution-robust asymmetry
    metric; 6-h overpass thinning for serial correlation (mwi_train
    matches); eye detected when a PCT85 < 260 K ring surrounds >= 75% of
    the center.
  Kieper & Jiang 2012 (GRL 39): 37-GHz warm-rain ring closure about the
    center as an organization/RI signal - carried as the ring37 closure
    candidates.
  Velden & Herndon 2020 (WAF 35) Sec. 2c: members are weighted by
    SITUATIONAL RMSE - hence the by-intensity-bin AND by-sensor error
    tables published in the model JSON (the consensus client reads them).

MODEL FORM: multiple linear regression (normal equations, no sklearn dep)
on a forward-selected subset of the candidate predictors (selection metric
= leave-one-year-out MAE, the honest generalization estimate), plus
per-sensor intercept offsets (SSMIS's 91.665 GHz + coarse conical footprint
vs GMI/AMSR2 89 GHz shifts the PCT distributions; Jones et al. handled this
by spatial averaging to a common resolution - we let the fit absorb the
mean shift and PUBLISH per-sensor validation so nothing hides). A squared
term of the leading mean-PCT predictor is allowed to capture the saturation
at the intense end (the SATCON paper's Cat-5 weak-bias lesson, V&H Sec. 3).
NOT a neural net, on purpose: every coefficient is inspectable and the
sign is physically checkable.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from . import mwi

# development levels EXCLUDED from training (extratropical / post-tropical /
# disturbance / wave / low / fill: best-track Vmax there is not a TC
# intensity in the Dvorak sense). TC-PRIMED carries the raw best-track codes
# (DB/TD/TS/TY/ST/TC/HU/SD/SS/EX/PT/IN/DS/LO/WV/ET/MD/XX, fill UN);
# subtropical SD/SS stay in (NHC counts them operationally).
EXCLUDE_DEV = {"EX", "ET", "PT", "DB", "LO", "WV", "MD", "IN", "DS", "XX",
               "UN"}

VMAX_BINS = [(0, 34, "TD"), (34, 64, "TS"), (64, 83, "cat1"),
             (83, 96, "cat2"), (96, 113, "cat3"), (113, 137, "cat4"),
             (137, 250, "cat5")]


# ---------------------------------------------------------------------------
# shard -> predictor row
# ---------------------------------------------------------------------------
def shard_row(npz) -> dict:
    """One shard -> {meta..., predictors...}. Rebuilds a stats-like dict and
    reuses mwi.compute_predictors verbatim - the runtime emits EVERY fit
    candidate, so the model JSON references only names the cron produces."""
    stats = {k: npz[k] for k in npz.files
             if k.endswith(("_min", "_sum", "_cnt", "_tot"))}
    stats["land_frac_ring"] = npz["land_frac_ring"]
    stats["raw_min"] = {v: npz[f"rawmin_{v}"] for v in mwi.PCT_VARIANTS
                        if f"rawmin_{v}" in npz.files}
    row = dict(mwi.compute_predictors(stats))
    # targets + meta
    for k in ("atcf", "sensor", "platform", "stamp", "dev_level"):
        row[k] = str(npz[k])
    for k in ("clat", "clon", "vmax_kt", "mslp_hpa", "distance_to_land",
              "storm_speed", "coverage_fraction"):
        row[k] = float(npz[k]) if k in npz.files else np.nan
    row["year"] = int(row["atcf"][4:8])
    row["abs_lat"] = abs(row["clat"])
    return row


# ---------------------------------------------------------------------------
# linear algebra (no sklearn)
# ---------------------------------------------------------------------------
def _design(rows, predictors, sensors):
    """X (n, k) with intercept + per-sensor offsets (first sensor = base)."""
    cols = []
    for r in rows:
        c = [1.0] + [float(r[p]) for p in predictors]
        c += [1.0 if r["sensor"] == s else 0.0 for s in sensors[1:]]
        cols.append(c)
    return np.asarray(cols, dtype=float)


def _lstsq(X, y):
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    return coef


def _loyo_predict(rows, predictors, sensors, target):
    """Leave-one-year-out predictions for every row (NaN if that year's
    training fold is degenerate)."""
    years = sorted({r["year"] for r in rows})
    yhat = np.full(len(rows), np.nan)
    idx_by_year = defaultdict(list)
    for i, r in enumerate(rows):
        idx_by_year[r["year"]].append(i)
    y = np.asarray([r[target] for r in rows], dtype=float)
    X = _design(rows, predictors, sensors)
    for yr in years:
        test = np.asarray(idx_by_year[yr])
        train = np.asarray([i for i in range(len(rows)) if rows[i]["year"] != yr])
        if train.size < 50:
            continue
        coef = _lstsq(X[train], y[train])
        yhat[test] = X[test] @ coef
    return yhat, y


def _mae(a, b):
    m = np.isfinite(a) & np.isfinite(b)
    return float(np.mean(np.abs(a[m] - b[m]))) if m.any() else np.nan


# ---------------------------------------------------------------------------
# the fit
# ---------------------------------------------------------------------------
# Candidate predictors, ordered by literature priority (CZ99/Jones: mean PCT
# and cold-area fraction first, minima later; closures/structure last). All
# names are RUNTIME names (mwi.compute_predictors emits every one of them).
CANDIDATES = [
    "pct89_mean100", "pct89_cold250_100", "pct89_cold225_100",
    "pct89_cold275_100", "pct37_mean100", "pct89_min100", "pct37_min100",
    "pct89_eyewall_min", "pct89_stdqm100", "pct37_stdqm100",
    "kj_fracdark100", "kj_fracbright100", "kj_ring_closure",
    "ring37_closure", "pct37_closure260", "ring89_closure",
    "cold_ring_radius89", "abs_lat",
]

GATE = {"min_cov100": 0.85, "min_cov_eyewall": 0.60,
        "max_land_frac100": 0.15}


def _usable(row) -> bool:
    if row["dev_level"].upper() in EXCLUDE_DEV:
        return False
    if not (10.0 <= row["vmax_kt"] <= 200.0):
        return False
    if row.get("pct89_cov100", 0.0) < GATE["min_cov100"]:
        return False
    if row.get("pct89_cov_eyewall", 0.0) < GATE["min_cov_eyewall"]:
        return False
    if row.get("land_frac100", 1.0) > GATE["max_land_frac100"]:
        return False
    return True


def fit(work: Path, out_path: Path | None = None, max_predictors: int = 7):
    shard_dir = Path(work) / "shards"
    rows = []
    skipped = 0
    for f in sorted(shard_dir.glob("*.npz")):
        try:
            with np.load(f, allow_pickle=False) as npz:
                rows.append(shard_row(npz))
        except Exception:  # noqa: BLE001
            skipped += 1
    print(f"fit: {len(rows)} shards ({skipped} unreadable)")

    usable = [r for r in rows if _usable(r)]
    print(f"fit: {len(usable)} pass the training gate "
          f"({len(rows) - len(usable)} gated out)")
    sensors = sorted({r["sensor"] for r in usable})

    # forward selection by LOYO MAE (the honest metric)
    chosen: list[str] = []
    best_mae = np.inf
    pool = [c for c in CANDIDATES
            if sum(np.isfinite(r.get(c, np.nan)) for r in usable) > 0.95 * len(usable)]
    frows = [r for r in usable
             if all(np.isfinite(r.get(c, np.nan)) for c in pool)]
    print(f"fit: {len(frows)} rows with all {len(pool)} candidates finite")
    while len(chosen) < max_predictors:
        best_c, best_c_mae = None, best_mae
        for c in pool:
            if c in chosen:
                continue
            yhat, y = _loyo_predict(frows, chosen + [c], sensors, "vmax_kt")
            m = _mae(yhat, y)
            if m < best_c_mae - 0.02:      # require a real gain
                best_c, best_c_mae = c, m
        if best_c is None:
            break
        chosen.append(best_c)
        best_mae = best_c_mae
        print(f"  + {best_c:22s} LOYO MAE {best_mae:.2f} kt")

    # allow one squared term of the leading predictor (saturation at the
    # intense end - the SATCON paper's Cat-5 weak-bias lesson, V&H Sec. 3);
    # centered/scaled on the training sample so the term is O(1), and the
    # spec is published in the model JSON's `derived` block for the runtime.
    lead = chosen[0]
    lead_vals = np.asarray([r[lead] for r in frows])
    sq_center = round(float(np.mean(lead_vals)), 2)
    sq_scale = round(float(np.var(lead_vals)) or 1.0, 2)
    derived = {}
    for r in frows:
        r[f"{lead}_sq"] = (r[lead] - sq_center) ** 2 / sq_scale
    yhat, y = _loyo_predict(frows, chosen + [f"{lead}_sq"], sensors, "vmax_kt")
    if _mae(yhat, y) < best_mae - 0.05:
        chosen.append(f"{lead}_sq")
        best_mae = _mae(yhat, y)
        derived[f"{lead}_sq"] = {"kind": "square", "base": lead,
                                 "center": sq_center, "scale": sq_scale}
        print(f"  + {lead}_sq (saturation term) LOYO MAE {best_mae:.2f} kt")

    # final coefficients on ALL usable rows
    Xall = _design(frows, chosen, sensors)
    yall = np.asarray([r["vmax_kt"] for r in frows])
    coef = _lstsq(Xall, yall)
    yhat_loyo, _ = _loyo_predict(frows, chosen, sensors, "vmax_kt")

    # ---- error tables (LOYO = out-of-sample honest) ----
    resid = yhat_loyo - yall
    fin = np.isfinite(resid)
    err_overall = {
        "n": int(fin.sum()),
        "mae": round(float(np.mean(np.abs(resid[fin]))), 2),
        "rmse": round(float(np.sqrt(np.mean(resid[fin] ** 2))), 2),
        "bias": round(float(np.mean(resid[fin])), 2),
    }
    err_by_bin = []
    for lo, hi, label in VMAX_BINS:
        m = fin & (yall >= lo) & (yall < hi)
        if m.sum() < 10:
            continue
        err_by_bin.append({
            "lo": lo, "hi": hi, "label": label, "n": int(m.sum()),
            "mae": round(float(np.mean(np.abs(resid[m]))), 2),
            "rmse": round(float(np.sqrt(np.mean(resid[m] ** 2))), 2),
            "bias": round(float(np.mean(resid[m])), 2),
        })
    err_by_sensor = {}
    svec = np.asarray([r["sensor"] for r in frows])
    for s in sensors:
        m = fin & (svec == s)
        err_by_sensor[s] = {
            "n": int(m.sum()),
            "mae": round(float(np.mean(np.abs(resid[m]))), 2),
            "rmse": round(float(np.sqrt(np.mean(resid[m] ** 2))), 2),
            "bias": round(float(np.mean(resid[m])), 2),
        }
    err_by_year = {}
    yvec = np.asarray([r["year"] for r in frows])
    for yr in sorted(set(yvec.tolist())):
        m = fin & (yvec == yr)
        err_by_year[str(yr)] = {
            "n": int(m.sum()),
            "mae": round(float(np.mean(np.abs(resid[m]))), 2),
            "bias": round(float(np.mean(resid[m])), 2),
        }

    # ---- MSLP model on the same predictor set (rows with a pressure) ----
    prows = [r for r in frows if np.isfinite(r["mslp_hpa"])
             and 850.0 < r["mslp_hpa"] < 1020.0]
    mslp_block = None
    if len(prows) > 300:
        Xp = _design(prows, chosen, sensors)
        yp = np.asarray([r["mslp_hpa"] for r in prows])
        coefp = _lstsq(Xp, yp)
        yhat_p, _ = _loyo_predict(prows, chosen, sensors, "mslp_hpa")
        rp = yhat_p - yp
        fp = np.isfinite(rp)
        mslp_block = {
            "coef": coefp, "n": int(fp.sum()),
            "mae": round(float(np.mean(np.abs(rp[fp]))), 2),
            "rmse": round(float(np.sqrt(np.mean(rp[fp] ** 2))), 2),
            "bias": round(float(np.mean(rp[fp])), 2),
        }

    # ---- assemble the model JSON (runtime predictor names) ----
    def _pack(coefvec):
        d = {"intercept": round(float(coefvec[0]), 4)}
        for i, c in enumerate(chosen):
            d[c] = round(float(coefvec[1 + i]), 4)
        for j, s in enumerate(sensors[1:]):
            d[f"sensor:{s}"] = round(float(coefvec[1 + len(chosen) + j]), 4)
        return d

    model = {
        "version": "mwi-v1.0",
        "fitted_utc": dt.datetime.now(dt.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "disclosure": ("EXPERIMENTAL automated objective estimate from "
                       "89/37-GHz passive-microwave structure. Not official. "
                       "See NHC/JTWC for official intensities."),
        "provenance": {
            "training_data": "NOAA/CIRA TC-PRIMED v01r01 final tier",
            "years": sorted({r['year'] for r in frows}),
            "basins": sorted({r['atcf'][:2] for r in frows}),
            "sensors": sensors,
            "n_overpasses": len(frows),
            "target": ("best-track Vmax (kt, 1-min) linearly interpolated "
                       "to overpass time (TC-PRIMED overpass_storm_metadata)"),
            "validation": "leave-one-year-out",
            "thinning": "6-h per-storm minimum gap (Jones et al. 2006 Sec 3a)",
            "gate_training": GATE,
            "extraction": {"grid_half_deg": mwi.GRID_HALF_DEG,
                           "grid_step_deg": mwi.GRID_STEP_DEG,
                           "ring_km": mwi.RING_KM,
                           "n_sectors": mwi.N_SECTORS},
            "methods": ("Spencer et al. 1989 (PCT); Cecil & Zipser 1999; "
                        "Jones, Cecil & DeMaria 2006 (SHIPS-MI); "
                        "Kieper & Jiang 2012 (37-GHz ring); "
                        "Cecil & Chronis 2018 (PCT coefficients)"),
        },
        "predictors": ["intercept"] + list(chosen)
        + [f"sensor:{s}" for s in sensors[1:]],
        "published_predictors": [c for c in chosen if not c.endswith("_sq")],
        "derived": derived,
        "vmax": _pack(coef),
        "vmax_range": [15.0, 185.0],
        # runtime gate == training gate: the published error tables were
        # measured on exactly this screen, so looser runtime acceptance
        # would quote errors the estimate doesn't actually have.
        "gate": dict(GATE),
        "error_overall": err_overall,
        "error_by_bin": err_by_bin,
        "error_by_sensor": err_by_sensor,
        "error_by_year": err_by_year,
        "confidence_mae_cut": 13.0,
    }
    if mslp_block is not None:
        model["mslp"] = _pack(mslp_block.pop("coef"))
        model["mslp_range"] = [880.0, 1015.0]
        model["mslp_error"] = mslp_block

    out = Path(out_path) if out_path else (Path(__file__).parent
                                           / "mwi_model_v1.json")
    out.write_text(json.dumps(model, indent=1))
    print(f"\nmodel written -> {out}")
    print(json.dumps({k: model[k] for k in
                      ("error_overall", "error_by_sensor")}, indent=1))
    print("by bin:")
    for b in err_by_bin:
        print(f"  {b['label']:5s} n={b['n']:5d} mae={b['mae']:6.2f} "
              f"rmse={b['rmse']:6.2f} bias={b['bias']:+6.2f}")
    return model
