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
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from typing import List, Optional

from . import warmcore
from .detect import detect_centers
from .ingest import IngestSession
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
# Hard wall-clock ceiling for ONE cycle's member gather (parallel path). The
# upstream clients (ecmwf-opendata -> multiurl, and our S3 fetch) can stall on a
# degraded portal with NO hard timeout, which otherwise wedges the run to the
# workflow's wall-clock limit (observed: a healthy ECMWF cycle finishes ~2 h; a
# stalled one rode past 3.5 h). At the deadline we abandon the un-finished members
# and force-kill their workers; the quorum check + never-miss retry then handle
# the gap. Set well ABOVE the healthy single-cycle time (~126 min observed) so a
# slow-but-healthy cycle never false-trips, yet well BELOW the workflow's 330-min
# timeout so a true stall is bounded and recovered ~2 h sooner than before.
DEFAULT_MEMBER_DEADLINE_S = 12000  # 200 min


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _cycle_iso(cycle: dt.datetime) -> str:
    return cycle.replace(tzinfo=None).isoformat() + "Z"


# --- ingest-backend selection -------------------------------------------
# The field self-detect path is shared, but the per-member DOWNLOAD differs by
# model source: ECMWF ENS / AIFS-ENS pull via the ecmwf-opendata client
# (enscenters.ingest); GEFS pulls 0.5 deg fields via S3 .idx byte-range
# (enscenters.gefs_ingest). Both expose make_client + iter_member_fields with the
# identical signature, so process_member is otherwise model-agnostic.
def _ingest_backend(spec: EnsModelSpec):
    src = getattr(spec, "source", "")
    if src == "noaa-gefs-aws":
        from . import gefs_ingest as backend
    elif src == "ecmwf-opendata":
        # Direct .index byte-range, multi-homed (prefer the fast US Google mirror,
        # fall back to AWS) - replaces the slow eu-central-1 ecmwf-opendata client.
        from . import ecmwf_byterange_ingest as backend
    else:
        from . import ingest as backend
    return backend


# --- per-member work (module-level so ProcessPoolExecutor can pickle it) ---
def process_member(spec: EnsModelSpec, cycle: dt.datetime, member_id: str,
                   steps: List[int], tmpdir: str, source: str):
    """Ingest + detect one member, STREAMING per step so only one field is
    resident. When the model self-detects MSLP (spec.warm_core), the iterator also
    yields the 300-500 thickness field and we keep ONLY warm-core tropical centers.
    Returns (member_id, peak|None, centers_rows)."""
    backend = _ingest_backend(spec)
    client = backend.make_client(source, spec.od_model)
    kwargs = spec.detect.as_kwargs()
    wc_kwargs = spec.warm_core_params.as_kwargs() if spec.warm_core else None
    centers: List[list] = []
    peak: Optional[dict] = None
    wc_skipped = 0   # steps where thickness was unavailable -> strict lat fallback (no warm-core)
    gen = backend.iter_member_fields(client, spec, cycle, member_id, steps, tmpdir)
    try:
        for lats, lons, step_h, field, thk in gen:
            cs = detect_centers(field, lats, lons, **kwargs)
            if wc_kwargs is not None:
                if thk is None:
                    wc_skipped += 1
                cs = warmcore.filter_centers(cs, thk, lats, lons, **wc_kwargs)
            for c in cs:
                centers.append([step_h, c["lat"], c["lon"], c["mslp_hpa"], c["vmax_kt"]])
                if peak is None or c["mslp_hpa"] < peak["mslp_hpa"]:
                    peak = {"mslp_hpa": c["mslp_hpa"], "vmax_kt": c["vmax_kt"],
                            "lat": c["lat"], "lon": c["lon"], "step_h": step_h}
    finally:
        gen.close()  # triggers temp-file cleanup even on early exit
    return member_id, peak, centers, wc_skipped


def build_one_cycle(
    spec: EnsModelSpec,
    cycle: dt.datetime,
    out_dir: str,
    *,
    members: Optional[List[str]] = None,
    steps: Optional[List[int]] = None,
    jobs: int = 1,
    min_members_frac: float = DEFAULT_MIN_MEMBERS_FRAC,
    source: str = "ecmwf",
    member_deadline_s: float = DEFAULT_MEMBER_DEADLINE_S,
    progress=print,
) -> dict:
    """Ingest + detect every member for ONE cycle, assemble the model-agnostic
    per-cycle JSON, and write it to ``out_dir/{slug}/{YYYYMMDDHH}.json`` (a
    deterministic, idempotent path - a re-run overwrites cleanly). Does NOT touch
    the manifest; that is the shared currency core's job (it folds in every cycle
    it built this run at once). This is the per-model ``ingest_cycle`` hook.

    Raises on a member QUORUM failure (a sparse/partial ingest), so a caller can
    skip this cycle without publishing degraded data."""
    members = members or spec.member_ids()
    steps = steps if steps is not None else spec.steps_for_cycle_hour(cycle.hour)

    progress(f"[{spec.slug}] cycle {cycle:%Y-%m-%d %HZ}: {len(members)} members x {len(steps)} steps, jobs={jobs}")

    results: dict = {}
    failures: List[str] = []
    wc_skipped_total = 0   # B3 telemetry: member-steps that ran the strict no-thickness fallback
    with IngestSession(spec) as sess:
        tmpdir = sess.tmpdir
        # Some backends pre-fetch a per-step index ONCE (shared across members)
        # before the member pool - ECMWF/AIFS's ~2 MB index packs all members, so
        # re-fetching it per member would be wasteful. GEFS has no prepare hook.
        prep = getattr(_ingest_backend(spec), "prepare", None)
        if prep is not None:
            prep(spec, cycle, steps, tmpdir, source=source, progress=progress)
        if jobs and jobs > 1:
            # NOT a `with` block: on a stall we force-kill workers and shut down
            # with wait=True ourselves (the default context-manager shutdown would
            # block forever on a worker stuck in an uninterruptible C-level
            # download).
            ex = ProcessPoolExecutor(max_workers=jobs)
            try:
                futs = {ex.submit(process_member, spec, cycle, mid, steps, tmpdir, source): mid
                        for mid in members}
                done = 0
                try:
                    for fut in as_completed(futs, timeout=member_deadline_s):
                        mid = futs[fut]
                        try:
                            m_id, peak, centers, wcs = fut.result()
                            results[m_id] = (peak, centers)
                            wc_skipped_total += wcs
                        except Exception as e:  # noqa: BLE001
                            failures.append(mid)
                            progress(f"[{spec.slug}]   member {mid} FAILED: {e}")
                        done += 1
                        if done % 5 == 0 or done == len(members):
                            progress(f"[{spec.slug}]   {done}/{len(members)} members done")
                except FuturesTimeoutError:
                    # A member's download stalled with no hard timeout. Abandon the
                    # un-finished members (the quorum check + never-miss retry handle
                    # the gap) and force-KILL the worker processes so the stuck
                    # C-level download cannot block shutdown. Bounds every field
                    # model (ECMWF/AIFS/GEFS) identically.
                    stuck = [futs[f] for f in futs if not f.done()]
                    failures.extend(stuck)
                    progress(f"[{spec.slug}]   DEADLINE {member_deadline_s}s hit; "
                             f"abandoning {len(stuck)} stalled member(s): {stuck}")
                    for p in list(getattr(ex, "_processes", {}).values()):
                        try:
                            p.kill()
                        except Exception:  # noqa: BLE001
                            pass
            finally:
                ex.shutdown(wait=True, cancel_futures=True)
        else:
            for k, mid in enumerate(members, 1):
                try:
                    _, peak, centers, wcs = process_member(spec, cycle, mid, steps, tmpdir, source)
                    results[mid] = (peak, centers)
                    wc_skipped_total += wcs
                except Exception as e:  # noqa: BLE001
                    failures.append(mid)
                    progress(f"[{spec.slug}]   member {mid} FAILED: {e}")
                if k % 5 == 0 or k == len(members):
                    progress(f"[{spec.slug}]   {k}/{len(members)} members done")

    # B3 telemetry: warm-core is meant to run on EVERY self-detect field. If the
    # thickness was unavailable for any member-step we applied the strict lat
    # fallback (not pass-through) - surface it LOUDLY so a warm-core outage can never
    # again silently splatter extratropical noise.
    if spec.warm_core and wc_skipped_total:
        progress(f"[{spec.slug}] WARN warm-core thickness unavailable on "
                 f"{wc_skipped_total} member-step(s) this cycle -> strict lat fallback "
                 f"applied (centers NOT passed through unfiltered)")

    # Member QUORUM: refuse to publish a sparse cycle. Without this, a partial
    # ingest failure (transient open-data 5xx, a half-published cycle) would write
    # a near-empty cycle. The currency core catches this and skips the cycle (it
    # stays "missing" and retries next run); if EVERY planned cycle fails, the CLI
    # exits 1 so the workflow aborts before the R2 prune and the prior data stays.
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
        "caption": spec.caption,                 # model-aware viewer caption (None -> default)
        "grid": spec.grid_label,
        "run_steps": list(steps),
        "n_members": len(member_objs),
        "n_centers": total_centers,
        "detect": spec.detect.as_kwargs(),
        "center_fields": CENTER_FIELDS,
        "pressure_bins": pressure_bins_json(),
        "members": member_objs,
    }

    # --- write per-cycle JSON (deterministic R2-keyed path) ---
    model_dir = os.path.join(out_dir, spec.slug)
    os.makedirs(model_dir, exist_ok=True)
    cyc_str = f"{cycle:%Y%m%d%H}"
    cycle_path = os.path.join(model_dir, f"{cyc_str}.json")
    with open(cycle_path, "w") as f:
        json.dump(data, f, separators=(",", ":"))

    bytes_json = os.path.getsize(cycle_path)
    progress(f"[{spec.slug}] wrote {cycle_path} ({bytes_json/1e6:.2f} MB), "
             f"{len(member_objs)} members, {total_centers} centers, {len(failures)} failed")
    return {
        "cycle": cyc_str,
        "generated_at": data["generated_at"],   # per-cycle cache-bust version
        "members": len(member_objs),
        "failures": failures,
        "n_centers": total_centers,
        "bytes_json": bytes_json,
        "cycle_path": cycle_path,
    }


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
    """Single-cycle convenience: build ONE cycle and fold it into the manifest +
    prune list (the historical entry point; the never-miss path uses
    ``currency.run_currency`` instead). Reads the prior manifest first so a hard
    CDN read failure aborts before any ingest rather than overwriting live data.
    Dispatches the per-model ingest: GEFS's light genesis-track path vs the field
    models' closed-low detect."""
    fetched = prior_manifest is None
    if fetched:
        prior_manifest = fetch_prior_manifest()
    _kind = getattr(spec, "source_kind", "self_detect")
    if _kind == "genesis_tracks":
        from .tracks import build_gefs_cycle
        res = build_gefs_cycle(spec, cycle, out_dir, progress=progress)
    elif _kind == "track_csv":
        # Native TC-track product (FNV3): fetch + parse the cyclogenesis CSV; no
        # detect, no warmcore. The manifest merge below is shared/unchanged.
        from .fnv3_ingest import build_cycle as build_track_cycle
        res = build_track_cycle(spec, cycle, out_dir, progress=progress)
    else:
        res = build_one_cycle(spec, cycle, out_dir, members=members, steps=steps,
                              jobs=jobs, min_members_frac=min_members_frac,
                              source=source, progress=progress)
    # Re-read the manifest just before merge: every model publishes to the SAME
    # manifest.json from its OWN workflow, and this run's ingest is a wide window
    # for another model to have published. Merging against the FRESH copy preserves
    # that concurrent update (merge_manifest_multi only replaces THIS model's
    # entry) - without this, the forced path clobbered ECMWF ENS when AIFS-ENS ran.
    # Tolerant: on a re-read failure keep the start-of-run manifest. Only re-read
    # when we fetched it ourselves (tests inject a fixed prior).
    # Sibling tracks/clusters JSON (additive; never blocks the centers publish).
    tracks_version = None
    try:
        from .tracking import build_tracks_for_cycle
        tr = build_tracks_for_cycle(
            spec, cycle, out_dir, cycle_path=res.get("cycle_path"),
            native_member_tracks=res.get("native_member_tracks"), progress=progress)
        tracks_version = tr.get("generated_at")
    except Exception as e:  # noqa: BLE001 - additive layer, centers still publish
        progress(f"[{spec.slug}] WARN: tracks build failed for {res['cycle']} ({e}); "
                 f"centers still published")

    if fetched:
        try:
            latest = fetch_prior_manifest()
            if latest is not None:
                prior_manifest = latest
        except Exception as e:  # noqa: BLE001 - keep the start-of-run manifest
            progress(f"[{spec.slug}] WARN: manifest re-read before merge failed ({e}); "
                     f"using start-of-run manifest")
    manifest, prune_keys = merge_manifest_multi(
        prior_manifest, spec, [res["cycle"]], retain,
        new_versions={res["cycle"]: res.get("generated_at")},
        new_tracks_versions={res["cycle"]: tracks_version})
    write_outputs(out_dir, manifest, prune_keys)
    progress(f"[{spec.slug}] manifest updated; prune {len(prune_keys)} old cycle(s)")
    return {**res, "prune_keys": prune_keys, "manifest": manifest}


def fetch_prior_manifest(url: str = MANIFEST_URL, timeout: float = 15.0,
                         attempts: int = 4) -> Optional[dict]:
    """Read the live manifest from the CDN (read-only, no creds).

    Returns None ONLY for a manifest that is PERSISTENTLY absent (genuine first
    run). On the Cloudflare R2 custom domain a missing object returns 403 (not
    404). CRITICAL: a 403/404 is now RETRIED, not treated as absent on the first
    try - a transient Cloudflare 403 on a PRESENT manifest must never be mistaken
    for an absent one, because the merge would then fresh-start and clobber every
    OTHER model's entry in the shared manifest (this exact bug dropped ECMWF ENS
    when AIFS-ENS published). Only a 403/404 that persists across ALL attempts is
    treated as absent; any other persistent failure RAISES so the caller aborts
    rather than overwriting live data."""
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(
                url + f"?t={int(dt.datetime.now(dt.timezone.utc).timestamp())}",
                headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - HTTP (incl 403/404) / network / parse; retry
            last = e
        if i < attempts - 1:
            time.sleep(2.0 * (i + 1))
    if isinstance(last, urllib.error.HTTPError) and last.code in (403, 404):
        return None  # PERSISTENTLY absent on R2's CDN: genuine fresh start
    raise RuntimeError(f"could not read prior manifest from {url}: {last}")


def published_cycles(manifest: Optional[dict], slug: str):
    """The set of cycle strings already published for ``slug`` in ``manifest``
    (the watermark). Tolerant of a malformed/empty manifest."""
    raw = (manifest or {}).get("models")
    raw = raw if isinstance(raw, list) else []
    for m in raw:
        if isinstance(m, dict) and m.get("slug") == slug:
            cyc = m.get("cycles")
            return set(c for c in cyc if isinstance(c, str)) if isinstance(cyc, list) else set()
    return set()


def merge_manifest_multi(prior: Optional[dict], spec: EnsModelSpec, new_cycles, retain: int,
                         new_versions: Optional[dict] = None,
                         new_tracks_versions: Optional[dict] = None):
    """Upsert ``new_cycles`` (a list of YYYYMMDDHH strings) into the manifest,
    trim to ``retain`` newest per model, and return (manifest, prune_keys). Models
    with no cycles are omitted so the viewer's selector only shows models that
    have data. ``latest`` is always the newest retained cycle. Folding several
    backfilled cycles in one call (and computing prune ONCE from the final kept
    set) means an old backfilled gap is never spuriously listed as a prune.

    ``new_versions`` maps each rebuilt cycle -> its content version (the per-cycle
    ``generated_at``); it is recorded under the model's ``cycle_versions`` so the
    viewer can cache-bust the data fetch on overwrite (an unchanged cycle keeps
    its version and stays cached). Versions for pruned cycles are dropped.

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
    new_set = set(new_cycles)
    cycles = [c for c in entry.get("cycles", []) if c not in new_set]
    cycles.extend(new_set)
    cycles = sorted(set(cycles), reverse=True)
    kept, pruned = cycles[:retain], cycles[retain:]
    entry["cycles"] = kept
    entry["latest"] = kept[0]
    # per-cycle cache-bust versions: prior + this run's, trimmed to retained cycles
    versions = dict(entry.get("cycle_versions") or {})
    if new_versions:
        versions.update(new_versions)
    entry["cycle_versions"] = {c: versions[c] for c in kept if c in versions}
    # SIBLING tracks/clusters JSON cache-bust tokens (additive; a cycle without a
    # tracks file simply has no entry, so the viewer falls back to centers-only).
    tversions = dict(entry.get("tracks_versions") or {})
    if new_tracks_versions:
        tversions.update({c: v for c, v in new_tracks_versions.items() if v})
    tkept = {c: tversions[c] for c in kept if c in tversions}
    if tkept:
        entry["tracks_versions"] = tkept
    elif "tracks_versions" in entry:
        del entry["tracks_versions"]
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
    # prune the rolled-out centers JSON AND its sibling tracks JSON (the shell's
    # prune filter keeps any key still referenced by the final manifest; a tracks
    # key is never "referenced" there, so only these explicitly-pruned cycles' track
    # files are removed - retained cycles' track files are never in this list).
    prune_keys = [f"{spec.slug}/{c}.json" for c in pruned]
    prune_keys += [f"{spec.slug}/{c}.tracks.json" for c in pruned]
    return manifest, prune_keys


def merge_manifest(prior: Optional[dict], spec: EnsModelSpec, cyc_str: str, retain: int):
    """Single-cycle convenience wrapper around :func:`merge_manifest_multi`."""
    return merge_manifest_multi(prior, spec, [cyc_str], retain)


def write_outputs(out_dir: str, manifest: dict, prune_keys) -> None:
    """Write manifest.json + prune_keys.txt into ``out_dir`` (consumed by the
    workflow's R2 sync + prune steps). The manifest's existence is the workflow's
    signal that something was published, so write it ONLY when there is a cycle
    to publish."""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, separators=(",", ":"))
    # Trailing newline matters: the workflow's `while read` loop would otherwise
    # drop the final (in steady state, only) prune key and never delete anything.
    with open(os.path.join(out_dir, "prune_keys.txt"), "w") as f:
        f.write("".join(k + "\n" for k in prune_keys))
