#!/usr/bin/env python3
"""Model verification against the best track - the scoreboard engine.

Everything here exists to keep the scoreboard HONEST rather than decorative,
and each rule below is load-bearing:

**HOMOGENEITY IS A HARD FILTER.** A model's mean error is comparable to
another's only over the SAME cases. A model that skips hard cases (late-cycle
runs, weak systems, high-shear messes) posts a better average than one that
attempts everything - not because it is better, but because it answered easier
questions. So every score in a panel is computed over exactly the case set
where EVERY model in the panel has a forecast, and when that set is too small
the engine drops MODELS (least coverage first) rather than relaxing the
filter. What was dropped, and why, is part of the output - the page shows it.

**THE BASELINE IS PROTECTED.** OCD5 (CLIPER5 + SHIFOR5: climatology and
persistence, deliberately skill-free) can never be dropped by the coverage
rule, because a mean error without a no-skill reference does not say whether
anything is adding value. Skill = 1 - err/err_OCD5 is reported per model.

**EARLY AND LATE ARE NEVER POOLED.** An early aid was available in time for
the forecast it is stamped with; a late aid is raw model output that arrived
after the deadline, so part of its apparent skill is hindsight. The engine
scores them as separate panels that never share a table.

**CONFIDENCE INTERVALS BLOCK-BOOTSTRAP OVER STORMS.** Consecutive 6-hourly
forecasts of the same storm are near-duplicates - the same synoptic situation
verified against nearly the same track. Bootstrapping individual forecasts
treats them as independent and produces intervals that are far too narrow. The
resampling unit here is the STORM: all of a storm's cases enter or leave a
bootstrap replicate together. With one storm in the sample there is no
interval at all, and the output says so instead of printing a fake one.

**THE TRUTH ITSELF IS PROVISIONAL.** Scores verify against the operational
b-deck, and the b-deck self-corrects - observed this week, it revised a fix it
already carried (Dolphin 140 kt -> 150 kt / 909 mb at an hour it had already
published). Scores computed today can therefore change retroactively, and
nothing is final until post-season reanalysis. That caveat ships in the
document, for the page to print.

Format traps handled by the parser this engine sits on (``guidance.atcf``):
the primary key includes RAD (only the rad None/34 row of each forecast is a
track point - the 50/64 kt rows repeat it), and the 0N/0W position sentinel
resolves to None before anything measures a distance.

Stdlib only; deterministic (seeded) bootstrap.
"""
from __future__ import annotations

import random
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from guidance import aids as aidcat
from guidance import atcf

#: Verified lead times (h). 60 is skipped (thin coverage), 144/168 too few yet.
TAUS = (12, 24, 36, 48, 72, 96, 120)

#: A (panel, tau) needs at least this many homogeneous cases to be shown.
MIN_CASES = 8

#: A panel keeps at most this many models; more is unreadable and thins N.
MAX_MODELS = 9

#: Bootstrap replicates + fixed seed (deterministic output documents).
BOOT_ITER = 1000
BOOT_SEED = 20260731

#: The protected no-skill reference.
BASELINE = "OCD5"

#: Never dropped by the coverage rule (in drop-resistance order).
PROTECTED = (BASELINE, "OFCL")

#: INTENSITY-consensus aids. Their kind is CONSENSUS, so the kind filter alone
#: would admit them to TRACK panels - but the positions they carry are not
#: track forecasts (observed: NNIC posts 628 nm at 120 h against TVCN's 82 in
#: the same homogeneous sample - it is not competing, it is contaminating).
#: They stay fully eligible for intensity panels, which is what they are.
INTENSITY_ONLY_CONSENSUS = frozenset({"IVCN", "NNIC"})


# ---------------------------------------------------------------------------
# Case extraction
# ---------------------------------------------------------------------------
def truth_from_bdeck(b_rows: Sequence[atcf.AidRow]) -> dict:
    """``{dtg: AidRow}`` best-track fixes (primary radii row only)."""
    out = {}
    for r in b_rows:
        if r.rad in (None, 34):
            out.setdefault(r.dtg, r)
    return out


def cases_for_storm(storm: str, a_rows: Sequence[atcf.AidRow],
                    truth: dict) -> list:
    """Verification cases for one storm.

    A case is ``(storm, init_dtg, tau)`` with per-model errors. τ+init lands on
    a synoptic hour, so truth is an exact b-deck fix - no interpolation, and no
    case exists where the best track has no fix (the storm had ended, or the
    fix is the resolved 0N/0W sentinel).
    """
    import datetime as dt
    per: dict = defaultdict(dict)   # (init, tau) -> tech -> row
    for r in a_rows:
        if not r.is_forecast or r.rad not in (None, 34) or r.tau not in TAUS:
            continue
        per[(r.dtg, r.tau)].setdefault(r.tech, r)

    out = []
    for (init, tau), by_tech in per.items():
        valid = init + dt.timedelta(hours=tau)
        t = truth.get(valid)
        if t is None:
            continue
        errs: dict = {}
        for tech, r in by_tech.items():
            e: dict = {}
            if r.has_position and t.has_position:
                e["track_nm"] = round(
                    atcf.great_circle_nm((r.lat, r.lon), (t.lat, t.lon)), 1)
            if r.vmax_kt is not None and t.vmax_kt is not None:
                e["int_err_kt"] = abs(r.vmax_kt - t.vmax_kt)
                e["int_bias_kt"] = r.vmax_kt - t.vmax_kt
            if e:
                errs[tech] = e
        if errs:
            out.append({"storm": storm,
                        "init": init.strftime("%Y%m%d%H"),
                        "tau": tau, "errs": errs})
    return out


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------
def _metric_key(metric: str) -> str:
    return "track_nm" if metric == "track" else "int_err_kt"


def candidate_models(cases: list, basin: str, timing: str,
                     metric: str) -> list:
    """Techs eligible for a panel, most-covered first.

    Kind and timing come from the aid catalog (basin-aware, so a JTWC aid can
    never be classified as consensus). Only OCD5 represents the baselines -
    CLP5/SHF5/XTRP/TCLP would crowd the panel with variants of no-skill.
    """
    want_kinds = {aidcat.AidKind.OFFICIAL, aidcat.AidKind.CONSENSUS,
                  aidcat.AidKind.DYNAMICAL}
    if metric == "intensity":
        want_kinds.add(aidcat.AidKind.STATISTICAL)
    mk = _metric_key(metric)
    cover: dict = defaultdict(int)
    for c in cases:
        for tech, e in c["errs"].items():
            if mk not in e:
                continue
            if metric == "track" and tech in INTENSITY_ONLY_CONSENSUS:
                continue
            kind, tm = aidcat.classify(tech, basin)
            ok = (tech == BASELINE or
                  (kind in want_kinds and tm.value == timing))
            if ok:
                cover[tech] += 1
    return sorted(cover, key=lambda t: (-cover[t], t))


def homogeneous_panel(cases: list, models: list, tau: int,
                      metric: str, min_cases: int = MIN_CASES) -> tuple:
    """``(kept_models, case_list, dropped)`` for one lead time.

    Cases where every kept model has the metric. When N is short, the least
    covered UNPROTECTED model is dropped and the filter re-runs - fewer models
    honestly compared beats more models incomparably averaged. ``dropped``
    records each removal and the case count that forced it.
    """
    mk = _metric_key(metric)
    tau_cases = [c for c in cases if c["tau"] == tau]
    models = list(models)
    dropped: list = []

    def homog(ms):
        return [c for c in tau_cases
                if all(m in c["errs"] and mk in c["errs"][m] for m in ms)]

    while True:
        hom = homog(models)
        if len(hom) >= min_cases or len(models) <= 2:
            return models, hom, dropped
        # Drop the unprotected model with the least coverage at this tau.
        droppable = [m for m in models if m not in PROTECTED]
        if not droppable:
            return models, hom, dropped
        cov = {m: sum(1 for c in tau_cases
                      if m in c["errs"] and mk in c["errs"][m])
               for m in droppable}
        worst = min(cov, key=lambda m: (cov[m], m))
        models.remove(worst)
        dropped.append({"tech": worst, "at_n": len(hom),
                        "coverage": cov[worst]})


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def storm_block_bootstrap(values_by_storm: Dict[str, List[float]],
                          n_iter: int = BOOT_ITER,
                          seed: int = BOOT_SEED) -> Optional[tuple]:
    """95% CI of the mean, resampling STORMS with replacement.

    Returns ``(lo, hi)`` or None when there is only one storm - a bootstrap
    over one block is a fiction, and the honest output is "no interval".
    """
    storms = [s for s, v in values_by_storm.items() if v]
    if len(storms) < 2:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(n_iter):
        pool: list = []
        for _ in storms:
            pool.extend(values_by_storm[rng.choice(storms)])
        means.append(sum(pool) / len(pool))
    means.sort()
    lo = means[int(0.025 * n_iter)]
    hi = means[min(int(0.975 * n_iter), n_iter - 1)]
    return round(lo, 1), round(hi, 1)


def score_panel(cases: list, basin: str, timing: str, metric: str) -> dict:
    """One (panel, metric) table across all lead times."""
    mk = _metric_key(metric)
    cands = candidate_models(cases, basin, timing, metric)[:MAX_MODELS + 3]
    # Baseline first, then coverage order, capped.
    ordered = [m for m in PROTECTED if m in cands]
    ordered += [m for m in cands if m not in ordered]
    ordered = ordered[:MAX_MODELS]

    per_tau: dict = {}
    dropped_any: dict = {}
    for tau in TAUS:
        kept, hom, dropped = homogeneous_panel(cases, ordered, tau, metric)
        if dropped:
            dropped_any[str(tau)] = dropped
        if len(hom) < MIN_CASES:
            per_tau[str(tau)] = {"n": len(hom), "omitted": True}
            continue
        storms = sorted({c["storm"] for c in hom})
        entry: dict = {"n": len(hom), "n_storms": len(storms),
                       "omitted": False, "models": {}}
        base_mean = None
        for m in kept:
            vals = [c["errs"][m][mk] for c in hom]
            by_storm: dict = defaultdict(list)
            for c in hom:
                by_storm[c["storm"]].append(c["errs"][m][mk])
            mean = sum(vals) / len(vals)
            ci = storm_block_bootstrap(dict(by_storm))
            rec = {"mean": round(mean, 1),
                   "ci": list(ci) if ci else None}
            if metric == "intensity":
                bias = [c["errs"][m]["int_bias_kt"] for c in hom
                        if "int_bias_kt" in c["errs"][m]]
                if bias:
                    rec["bias"] = round(sum(bias) / len(bias), 1)
            entry["models"][m] = rec
            if m == BASELINE:
                base_mean = mean
        if base_mean:
            for m, rec in entry["models"].items():
                if m != BASELINE:
                    rec["skill_pct"] = round(
                        (1.0 - rec["mean"] / base_mean) * 100.0, 1)
        per_tau[str(tau)] = entry

    models_present = sorted({m for t in per_tau.values()
                             for m in (t.get("models") or {})})
    meta = {}
    for m in models_present:
        kind, tm = aidcat.classify(m, basin)
        meta[m] = {"label": aidcat.label(m), "kind": kind.value,
                   "timing": tm.value, "is_baseline": m == BASELINE}
    return {"metric": metric, "timing": timing, "taus": list(TAUS),
            "per_tau": per_tau, "model_meta": meta,
            "dropped": dropped_any}
