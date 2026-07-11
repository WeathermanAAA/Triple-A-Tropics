"""tcprimed.mwi_train - OFFLINE trainer for the MW-imager intensity member.

One-time (re-runnable) Codespace job; the CI cron never imports this. Committed
for provenance/reproducibility of tcprimed/mwi_model_v1.json.

Pipeline (each stage restart-safe, all state under --work):
  1. inventory : list every final-tier imager overpass on the public TC-PRIMED
                 bucket (anonymous) for the training years -> inventory.json
  2. sample    : thin per storm (min time gap, prefer GMI > AMSR2 > SSMIS so the
                 kept pass in a window is the better sensor) + per-year cap ->
                 sample.json. Deterministic (seeded).
  3. extract   : download each sampled overpass, run the SHARED extraction
                 (tcprimed.mwi.ring_sector_stats - identical to runtime) plus
                 the best-track targets, write one .npz shard per overpass,
                 delete the NetCDF. Parallel; skips existing shards.
  4. fit       : shards -> feature table -> interpretable least-squares fit for
                 Vmax (+MSLP), leave-one-YEAR-out validation, MAE/bias by
                 intensity bin AND by sensor -> tcprimed/mwi_model_v1.json
                 (coefficients + gate + error tables + full provenance).

Usage (repo root):
  python -m tcprimed.mwi_train inventory --work /tmp/mwi
  python -m tcprimed.mwi_train sample    --work /tmp/mwi --target 6600
  python -m tcprimed.mwi_train extract   --work /tmp/mwi --workers 2
  python -m tcprimed.mwi_train fit       --work /tmp/mwi
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from . import fetch as fx
from . import render as rnd
from . import mwi

YEARS = list(range(2014, 2025))          # GMI-era: all three runtime sensors
BASINS = ("AL", "EP", "CP", "WP", "IO", "SH")
SENSOR_PREF = {"GMI": 0, "AMSR2": 1, "SSMIS": 2}
MIN_GAP_HOURS = 6.0                       # best-track targets are 6-h anyway
PER_STORM_CAP = 40
SEED = 20260711


# ---------------------------------------------------------------------------
def cmd_inventory(work: Path):
    c = fx._client()
    paginator = c.get_paginator("list_objects_v2")
    inv = []
    for year in YEARS:
        for basin in BASINS:
            n = 0
            prefix = f"{fx.PREFIX_ROOT}/final/{year}/{basin}/"
            for page in paginator.paginate(Bucket=fx.BUCKET, Prefix=prefix):
                for obj in page.get("Contents", []):
                    m = fx.parse_overpass_filename(obj["Key"].rsplit("/", 1)[-1])
                    if m is None:
                        continue
                    inv.append({"key": obj["Key"], "size": obj["Size"],
                                "atcf": m["atcf"], "sensor": m["sensor"],
                                "platform": m["platform"], "stamp": m["stamp"],
                                "id": m["id"]})
                    n += 1
            print(f"{year} {basin}: {n}", flush=True)
    out = work / "inventory.json"
    out.write_text(json.dumps(inv))
    print(f"inventory: {len(inv)} overpasses -> {out}")


# ---------------------------------------------------------------------------
def _thin_storm(ops: list[dict]) -> list[dict]:
    """Chronological greedy thin with a MIN_GAP_HOURS window; within a window
    the BEST sensor wins (GMI > AMSR2 > SSMIS): we sort by (time), then for
    each kept anchor, any pass within the gap replaces it only if strictly
    preferred. Cap per storm."""
    ops = sorted(ops, key=lambda o: o["stamp"])
    kept: list[dict] = []
    for o in ops:
        t = dt.datetime.strptime(o["stamp"], "%Y%m%d%H%M%S")
        o["_t"] = t
        if kept and (t - kept[-1]["_t"]).total_seconds() < MIN_GAP_HOURS * 3600:
            if SENSOR_PREF[o["sensor"]] < SENSOR_PREF[kept[-1]["sensor"]]:
                kept[-1] = o
            continue
        kept.append(o)
    return kept[:PER_STORM_CAP]


def cmd_sample(work: Path, target: int):
    inv = json.loads((work / "inventory.json").read_text())
    years = {str(y) for y in YEARS}
    inv = [o for o in inv if o["atcf"][4:8] in years]
    by_storm = defaultdict(list)
    for o in inv:
        by_storm[o["atcf"]].append(o)
    thinned = []
    for atcf, ops in by_storm.items():
        thinned.extend(_thin_storm(ops))
    print(f"thinned: {len(inv)} -> {len(thinned)}")

    # per-year stratified downsample to the target (seeded, deterministic)
    by_year = defaultdict(list)
    for o in thinned:
        by_year[o["atcf"][4:8]].append(o)
    per_year = max(1, target // len(by_year))
    rng = random.Random(SEED)
    sample = []
    for year in sorted(by_year):
        ops = by_year[year]
        if len(ops) > per_year:
            ops = rng.sample(ops, per_year)
        sample.extend(ops)
    for o in sample:
        o.pop("_t", None)
    sample.sort(key=lambda o: o["key"])
    (work / "sample.json").write_text(json.dumps(sample))
    gb = sum(o["size"] for o in sample) / 1e9
    from collections import Counter
    print(f"sample: {len(sample)} overpasses, {gb:.1f} GB to fetch")
    print("  by sensor:", dict(Counter(o['sensor'] for o in sample)))
    print("  by year:", dict(sorted(Counter(o['atcf'][4:8] for o in sample).items())))


# ---------------------------------------------------------------------------
def _extract_one(args) -> str:
    """Worker: download one overpass, extract the mwi superset + targets,
    write the shard, delete the NetCDF. Returns a status string."""
    op, shard_dir = args
    import netCDF4 as nc
    sid = op["id"]
    shard = Path(shard_dir) / f"{sid}.npz"
    if shard.exists():
        return "skip"
    try:
        with tempfile.TemporaryDirectory(prefix="mwi_") as tmp:
            local = fx.download(op["key"], tmp)
            meta = rnd.read_overpass(local)
            stats = mwi.ring_sector_stats(meta)
            if stats is None:
                return "noswath"
            # training targets + extras straight from the file
            extra = {}
            with nc.Dataset(local) as ds:
                sm = ds["overpass_storm_metadata"]
                om = ds["overpass_metadata"]
                for k in ("distance_to_land", "storm_speed", "storm_heading"):
                    try:
                        extra[k] = float(sm[k][0])
                    except Exception:  # noqa: BLE001
                        extra[k] = np.nan
                try:
                    extra["coverage_fraction"] = float(om["coverage_fraction"][0])
                except Exception:  # noqa: BLE001
                    extra["coverage_fraction"] = np.nan
        payload = {
            "atcf": meta["atcf"], "sensor": meta["sensor"],
            "platform": meta["platform"], "stamp": op["stamp"],
            "clat": meta["clat"], "clon": meta["clon"],
            "vmax_kt": float(meta["intensity_kt"]),
            "mslp_hpa": float(meta["min_p_hpa"] or np.nan),
            "dev_level": meta["dev_level"],
            "land_frac_ring": stats["land_frac_ring"],
            **{k: float(v) for k, v in extra.items()},
        }
        for key, val in stats.items():
            if key in ("grid", "raw_min", "land_frac_ring"):
                continue
            payload[key] = val
        for name, arr in stats["raw_min"].items():
            payload[f"rawmin_{name}"] = arr
        tmp_shard = shard.with_suffix(".tmp.npz")
        np.savez_compressed(tmp_shard, **payload)
        os.replace(tmp_shard, shard)
        return "ok"
    except Exception as e:  # noqa: BLE001
        return f"err:{type(e).__name__}"


def cmd_extract(work: Path, workers: int):
    sample = json.loads((work / "sample.json").read_text())
    shard_dir = work / "shards"
    shard_dir.mkdir(exist_ok=True)
    todo = [o for o in sample
            if not (shard_dir / f"{o['id']}.npz").exists()]
    print(f"extract: {len(sample)} sampled, {len(todo)} to do, "
          f"{workers} workers", flush=True)
    from multiprocessing import Pool
    t0 = time.time()
    counts = defaultdict(int)
    with Pool(workers, maxtasksperchild=200) as pool:
        for i, status in enumerate(pool.imap_unordered(
                _extract_one, [(o, str(shard_dir)) for o in todo],
                chunksize=4)):
            counts[status.split(":")[0]] += 1
            if (i + 1) % 100 == 0:
                el = time.time() - t0
                rate = (i + 1) / el
                eta = (len(todo) - i - 1) / rate / 3600
                print(f"  {i+1}/{len(todo)} ({dict(counts)}) "
                      f"{rate*3600:.0f}/h eta {eta:.1f} h", flush=True)
    print(f"extract done: {dict(counts)} in {(time.time()-t0)/3600:.2f} h")


# ---------------------------------------------------------------------------
def cmd_fit(work: Path):
    """Implemented in mwi_fit (kept separate so the long extract can run while
    the fit spec is finalized)."""
    from . import mwi_fit
    mwi_fit.fit(work)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["inventory", "sample", "extract", "fit"])
    ap.add_argument("--work", default="/tmp/mwi")
    ap.add_argument("--target", type=int, default=6600)
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    if a.stage == "inventory":
        cmd_inventory(work)
    elif a.stage == "sample":
        cmd_sample(work, a.target)
    elif a.stage == "extract":
        cmd_extract(work, a.workers)
    elif a.stage == "fit":
        cmd_fit(work)


if __name__ == "__main__":
    main()
