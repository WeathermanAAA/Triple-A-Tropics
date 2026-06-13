"""
Cycle orchestrator: ingest every member, detect centers, assemble the
model-agnostic per-cycle JSON, and merge the manifest with a rolling-window
prune. R2-only output (like HAFS) - nothing is committed to main.

Output layout (== R2 key layout under the bucket, served from
``cdn.triple-a-tropics.com``):

  models/enscenters/manifest.json
  models/enscenters/{model}/{YYYYMMDDHH}.json
  models/enscenters/prune_keys.txt   (consumed by the workflow's R2 prune step)
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import List, Optional

from .detect import detect_centers
from .ingest import IngestSession, iter_member_fields, make_client, resolve_latest_complete
from .registry import (
    DEFAULT_MODEL,
    EnsModelSpec,
    member_label,
    models_meta,
    pressure_bins_json,
)

SCHEMA_VERSION = 1
CDN_BASE = "https://cdn.triple-a-tropics.com"
R2_PREFIX = "models/enscenters"
MANIFEST_URL = f"{CDN_BASE}/{R2_PREFIX}/manifest.json"
CENTER_FIELDS = ["step_h", "lat", "lon", "mslp_hpa", "vmax_kt"]
DEFAULT_RETAIN = 8  # rolling window of cycles kept on R2 per model (~2 days)
DEFAULT_MIN_MEMBERS_FRAC = 0.75  # refuse to publish a sparse cycle (quorum)


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _cycle_iso(cycle: dt.datetime) -> str:
    return cycle.replace(tzinfo=None).isoformat() + "Z"


# --- per-member work (module-level so ProcessPoolExecutor can pickle it) ---
def process_member(spec: EnsModelSpec, cycle: dt.datetime, member_id: str,
                   steps: List[int], tmpdir: str, source: str):
    """Ingest + detect one member, STREAMING per step so only one field is
    resident. Returns (member_id, peak|None, centers_rows)."""
    client = make_client(source)
    kwargs = spec.detect.as_kwargs()
    centers: List[list] = []
    peak: Optional[dict] = None
    gen = iter_member_fields(client, spec, cycle, member_id, steps, tmpdir)
    try:
        for lats, lons, step_h, field in gen:
            for c in detect_centers(field, lats, lons, **kwargs):
                centers.append([step_h, c["lat"], c["lon"], c["mslp_hpa"], c["vmax_kt"]])
                if peak is None or c["mslp_hpa"] < peak["mslp_hpa"]:
                    peak = {"mslp_hpa": c["mslp_hpa"], "vmax_kt": c["vmax_kt"],
                            "lat": c["lat"], "lon": c["lon"], "step_h": step_h}
    finally:
        gen.close()  # triggers temp-file cleanup even on early exit
    return member_id, peak, centers


def build_cycle(
    spec: EnsModelSpec,
    cycle: dt.datetime,
    out_dir: str,
    *,
    members: Optional[List[str]] = None,
    steps: Optional[List[int]] = None,
    jobs: int = 1,
    retain: int = DEFAULT_RETAIN,
    min_members_frac: float = DEFAULT_MIN_MEMBERS_FRAC,
    source: str = "ecmwf",
    prior_manifest: Optional[dict] = None,
    progress=print,
) -> dict:
    """Run one cycle end to end. Writes the per-cycle JSON + merged manifest +
    prune list into ``out_dir``. Returns a summary dict."""
    members = members or spec.member_ids()
    steps = steps if steps is not None else spec.steps_for_cycle_hour(cycle.hour)

    # Read the prior manifest FIRST, before any ingest. If the CDN read hard-fails
    # (network/5xx, not a clean 404) we abort here - publishing a single-cycle
    # manifest would collapse the viewer's history and orphan the prior cycles.
    # A clean 404 (first run) returns None and we start fresh.
    if prior_manifest is None:
        prior_manifest = fetch_prior_manifest()

    progress(f"[ecens] cycle {cycle:%Y-%m-%d %HZ}: {len(members)} members x {len(steps)} steps, jobs={jobs}")

    results: dict = {}
    failures: List[str] = []
    with IngestSession(spec) as sess:
        tmpdir = sess.tmpdir
        if jobs and jobs > 1:
            with ProcessPoolExecutor(max_workers=jobs) as ex:
                futs = {ex.submit(process_member, spec, cycle, mid, steps, tmpdir, source): mid
                        for mid in members}
                done = 0
                for fut in as_completed(futs):
                    mid = futs[fut]
                    try:
                        m_id, peak, centers = fut.result()
                        results[m_id] = (peak, centers)
                    except Exception as e:  # noqa: BLE001
                        failures.append(mid)
                        progress(f"[ecens]   member {mid} FAILED: {e}")
                    done += 1
                    if done % 5 == 0 or done == len(members):
                        progress(f"[ecens]   {done}/{len(members)} members done")
        else:
            for k, mid in enumerate(members, 1):
                try:
                    _, peak, centers = process_member(spec, cycle, mid, steps, tmpdir, source)
                    results[mid] = (peak, centers)
                except Exception as e:  # noqa: BLE001
                    failures.append(mid)
                    progress(f"[ecens]   member {mid} FAILED: {e}")
                if k % 5 == 0 or k == len(members):
                    progress(f"[ecens]   {k}/{len(members)} members done")

    # Member QUORUM: refuse to publish a sparse cycle. Without this, a partial
    # ingest failure (transient open-data 5xx, a half-published cycle) would
    # write a near-empty cycle, mark it `latest`, AND prune the prior COMPLETE
    # cycle out of the window - replacing good live data with degraded data and
    # deleting the fallback in one run. Raising maps to exit 1 in the CLI, so the
    # workflow aborts before the R2 prune and the prior cycle stays live.
    min_needed = max(2, math.ceil(min_members_frac * len(members)))
    if len(results) < min_needed:
        raise RuntimeError(
            f"only {len(results)}/{len(members)} members ingested for "
            f"{cycle:%Y%m%d%H} (need >= {min_needed}); refusing to publish")

    # --- assemble per-cycle JSON (members in canonical order) ---
    member_objs = []
    total_centers = 0
    for mid in members:
        if mid not in results:
            continue
        peak, centers = results[mid]
        total_centers += len(centers)
        member_objs.append({
            "id": mid,
            "label": member_label(mid),
            "peak": peak,
            "n_centers": len(centers),
            "centers": centers,
        })

    data = {
        "schema_version": SCHEMA_VERSION,
        "model": spec.slug,
        "model_label": spec.label,
        "init_time": _cycle_iso(cycle),
        "init_cycle": f"{cycle:%Y%m%d%H}",
        "cycle_hour": cycle.hour,
        "generated_at": _utcnow_iso(),
        "attribution": spec.attribution,
        "grid": "0.25 deg",
        "run_steps": list(steps),
        "n_members": len(member_objs),
        "n_centers": total_centers,
        "detect": spec.detect.as_kwargs(),
        "center_fields": CENTER_FIELDS,
        "pressure_bins": pressure_bins_json(),
        "members": member_objs,
    }

    # --- write per-cycle JSON ---
    model_dir = os.path.join(out_dir, spec.slug)
    os.makedirs(model_dir, exist_ok=True)
    cyc_str = f"{cycle:%Y%m%d%H}"
    cycle_path = os.path.join(model_dir, f"{cyc_str}.json")
    with open(cycle_path, "w") as f:
        json.dump(data, f, separators=(",", ":"))

    # --- merge manifest + compute prune list (prior_manifest resolved early) ---
    manifest, prune_keys = merge_manifest(prior_manifest, spec, cyc_str, retain)
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, separators=(",", ":"))
    # Trailing newline matters: the workflow's `while read` loop would otherwise
    # drop the final (in steady state, only) prune key and never delete anything.
    with open(os.path.join(out_dir, "prune_keys.txt"), "w") as f:
        f.write("".join(k + "\n" for k in prune_keys))

    bytes_json = os.path.getsize(cycle_path)
    progress(f"[ecens] wrote {cycle_path} ({bytes_json/1e6:.2f} MB), "
             f"{len(member_objs)} members, {total_centers} centers, "
             f"{len(failures)} failed; prune {len(prune_keys)} old cycle(s)")
    return {
        "cycle": cyc_str,
        "members": len(member_objs),
        "failures": failures,
        "n_centers": total_centers,
        "bytes_json": bytes_json,
        "cycle_path": cycle_path,
        "prune_keys": prune_keys,
        "manifest": manifest,
    }


def fetch_prior_manifest(url: str = MANIFEST_URL, timeout: float = 15.0,
                         attempts: int = 3) -> Optional[dict]:
    """Read the live manifest from the CDN (read-only, no creds).

    Returns None for an ABSENT manifest (first run). On the Cloudflare R2 custom
    domain a missing object returns 403 (not 404), so both are treated as absent.
    A persistent network/5xx/parse failure RAISES, so build_cycle aborts rather
    than overwriting the live manifest with a single-cycle one (which would
    collapse the viewer's history and permanently orphan the prior cycles)."""
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(
                url + f"?t={int(dt.datetime.now(dt.timezone.utc).timestamp())}",
                headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return None  # absent on R2's CDN: fresh start
            last = e
        except Exception as e:  # noqa: BLE001 - network / parse
            last = e
        if i < attempts - 1:
            time.sleep(2.0 * (i + 1))
    raise RuntimeError(f"could not read prior manifest from {url}: {last}")


def merge_manifest(prior: Optional[dict], spec: EnsModelSpec, cyc_str: str, retain: int):
    """Upsert this cycle into the manifest, trim to ``retain`` newest per model,
    and return (manifest, prune_keys). Models with no cycles are omitted so the
    viewer's model selector only shows models that actually have data.

    Tolerant of a malformed prior manifest fetched from the public CDN: a
    non-list `models`, entries missing `slug`, or a non-list `cycles` are
    skipped/coerced rather than crashing the builder."""
    raw = (prior or {}).get("models")
    raw = raw if isinstance(raw, list) else []
    by_slug = {}
    for m in raw:
        if isinstance(m, dict) and m.get("slug"):
            d = dict(m)
            d["cycles"] = d["cycles"] if isinstance(d.get("cycles"), list) else []
            by_slug[d["slug"]] = d
    entry = by_slug.get(spec.slug, {"slug": spec.slug, "label": spec.label, "cycles": []})
    entry["label"] = spec.label
    cycles = [c for c in entry.get("cycles", []) if c != cyc_str]
    cycles.append(cyc_str)
    cycles = sorted(set(cycles), reverse=True)
    kept, pruned = cycles[:retain], cycles[retain:]
    entry["cycles"] = kept
    entry["latest"] = kept[0]
    by_slug[spec.slug] = entry

    # order by registry; include only models that have at least one cycle
    order = [m["slug"] for m in models_meta()]
    for s in by_slug:
        if s not in order:
            order.append(s)
    models = [by_slug[s] for s in order if s in by_slug and by_slug[s].get("cycles")]

    default_model = DEFAULT_MODEL if any(m["slug"] == DEFAULT_MODEL for m in models) else (
        models[0]["slug"] if models else DEFAULT_MODEL)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utcnow_iso(),
        "default_model": default_model,
        "models": models,
    }
    prune_keys = [f"{spec.slug}/{c}.json" for c in pruned]
    return manifest, prune_keys
