"""
Shared "never-miss-a-cycle" currency core for the ensemble platform.

MODEL-AGNOSTIC. The greedy "latest complete cycle on a fixed cron" pattern skips
any cycle that disseminates late (the cron advances and never looks back). This
module replaces it with a watermark + backfill loop that EVERY ensemble model
(ECMWF ENS now; AIFS-ENS / GEFS / GDM later) reuses unchanged by supplying two
small per-model hooks - it does not re-solve currency per model:

  * ``list_complete_cycles(lookback)`` -> the cycle datetimes that are fully
    disseminated on the source within the lookback window (a per-model file
    COMPLETENESS gate, never wall-clock - see ``enscenters.ingest``).
  * ``ingest_cycle(cycle)`` -> build + write ONE cycle's per-cycle JSON (the
    per-model ingest, ``enscenters.pipeline.build_one_cycle`` for ECMWF). May
    raise to signal a sparse/failed cycle.

Each run:
  watermark = cycles already in the model's R2 manifest;
  backfill every COMPLETE cycle inside the retention window that is NOT in the
  watermark, OLDEST-MISSING FIRST, capped at ``max_per_run`` (leftovers catch up
  next run); fold them all into the manifest once and emit the prune list.

Idempotent: per-cycle paths are keyed by cycle (a re-run/backfill overwrites
cleanly), and the manifest merge dedups. The manifest is written ONLY when at
least one cycle was published this run, so a no-op run leaves live data alone and
an all-failed run raises (the CLI exits 1, the workflow aborts before the prune).
"""
from __future__ import annotations

import datetime as dt
from typing import Callable, Iterable, List, Optional, Set

from .pipeline import (
    DEFAULT_RETAIN,
    fetch_prior_manifest,
    merge_manifest_multi,
    published_cycles,
    write_outputs,
)
from .registry import EnsModelSpec

# Lookback: how many synoptic cycles back to consider each run. 10 (~60 h) spans
# well beyond the ~6-10 h dissemination window, so a cycle missed for a day still
# backfills. Cap: at most this many cycles ingested per run (each is ~30-60 min).
DEFAULT_LOOKBACK = 10
DEFAULT_MAX_PER_RUN = 3


def synoptic_cycles_back(now: dt.datetime, lookback: int, step_hours: int = 6) -> List[dt.datetime]:
    """The ``lookback`` most recent synoptic cycle datetimes at or before ``now``
    (floored to the ``step_hours`` grid), newest first. Naive UTC, matching the
    rest of the pipeline."""
    base = now.replace(minute=0, second=0, microsecond=0, tzinfo=None)
    base = base.replace(hour=(base.hour // step_hours) * step_hours)
    return [base - dt.timedelta(hours=step_hours * i) for i in range(lookback)]


def plan_backfill(published: Iterable[str], complete: Iterable[str],
                  retain: int, max_per_run: int) -> List[str]:
    """Pure planner - the heart of never-miss. Given the published watermark and
    the cycles COMPLETE on the source, return the cycle strings to ingest THIS
    run: cycles inside the target window (the newest ``retain`` of published U
    complete) that are complete but not yet published, OLDEST FIRST, capped at
    ``max_per_run``.

    - Oldest-first so a gap fills from the back rather than the newest starving it.
    - Window-bounded so we never ingest a cycle that would be pruned immediately.
    - Capped so per-run work is bounded; leftovers (incl. a long backlog) catch
      up monotonically over subsequent runs.
    """
    pub: Set[str] = set(published)
    comp: Set[str] = set(complete)
    window = sorted(comp | pub, reverse=True)[:retain]   # the set we want live
    missing = sorted(c for c in window if c in comp and c not in pub)  # oldest first
    return missing[:max_per_run]


def run_currency(
    *,
    spec: EnsModelSpec,
    out_dir: str,
    list_complete_cycles: Callable[[int], List[dt.datetime]],
    ingest_cycle: Callable[[dt.datetime], dict],
    retain: int = DEFAULT_RETAIN,
    lookback: int = DEFAULT_LOOKBACK,
    max_per_run: int = DEFAULT_MAX_PER_RUN,
    prior_manifest: Optional[dict] = None,
    fetch_prior: Callable[[], Optional[dict]] = fetch_prior_manifest,
    progress=print,
) -> dict:
    """Run the never-miss currency loop for one model. Returns a summary dict.

    Raises if the prior manifest cannot be read (don't clobber live data) or if
    every planned cycle fails to ingest (total failure -> CLI exit 1 -> workflow
    aborts before the prune). A clean no-op (nothing missing) returns
    ``skipped=True`` and writes NO manifest, so the workflow leaves R2 untouched.
    """
    # Read the watermark FIRST, before any ingest. A hard CDN read failure raises
    # here (a single-cycle manifest would collapse the viewer's history); a clean
    # absent manifest (first run) is None -> empty watermark.
    if prior_manifest is None:
        prior_manifest = fetch_prior()
    published = published_cycles(prior_manifest, spec.slug)

    complete_dt = list_complete_cycles(lookback) or []
    by_str = {f"{c:%Y%m%d%H}": c for c in complete_dt}
    complete = list(by_str.keys())
    plan = plan_backfill(published, complete, retain, max_per_run)
    progress(f"[currency] {spec.slug}: watermark={len(published)} complete={len(complete)} "
             f"plan={plan or '[]'}")

    if not plan:
        progress("[currency] no missing complete cycle - nothing to publish.")
        return {"slug": spec.slug, "skipped": True, "wrote_manifest": False,
                "published": [], "failed": [], "planned": []}

    ingested: List[str] = []
    failed: List[str] = []
    versions: dict = {}                       # cycle -> per-cycle generated_at (cache-bust)
    for cyc_str in plan:                      # oldest-first
        try:
            res = ingest_cycle(by_str[cyc_str])
            ingested.append(res["cycle"])
            versions[res["cycle"]] = res.get("generated_at")
        except Exception as e:  # noqa: BLE001 - skip a sparse/failed cycle, retry next run
            failed.append(cyc_str)
            progress(f"[currency] cycle {cyc_str} ingest FAILED, skipping (retries next run): {e}")

    if not ingested:
        # Every planned cycle failed: publish nothing and signal loudly so the
        # workflow aborts before the prune and the prior data stays live.
        raise RuntimeError(f"all {len(plan)} planned cycle(s) failed to ingest: {plan}")

    # Re-read the manifest just before merging: every model (ECMWF ENS, AIFS-ENS,
    # ...) publishes to the SAME manifest.json from its OWN workflow, and this
    # run's long ingest is a wide window for another model to have published in
    # between. Merging against the FRESH manifest preserves that concurrent update
    # (merge_manifest_multi only replaces THIS model's entry). Tolerant: on a
    # re-read failure, fall back to the start-of-run manifest.
    try:
        latest = fetch_prior()
        if latest is not None:
            prior_manifest = latest
    except Exception as e:  # noqa: BLE001 - keep the start-of-run manifest
        progress(f"[currency] WARN: manifest re-read before merge failed ({e}); "
                 f"using start-of-run manifest")
    manifest, prune_keys = merge_manifest_multi(
        prior_manifest, spec, ingested, retain, new_versions=versions)
    write_outputs(out_dir, manifest, prune_keys)
    progress(f"[currency] published {len(ingested)} cycle(s) {ingested}; "
             f"prune {len(prune_keys)} old cycle(s); "
             f"latest={manifest['models'][0]['latest'] if manifest['models'] else 'n/a'}")
    return {"slug": spec.slug, "skipped": False, "wrote_manifest": True,
            "published": ingested, "failed": failed, "planned": plan,
            "prune_keys": prune_keys, "manifest": manifest}
