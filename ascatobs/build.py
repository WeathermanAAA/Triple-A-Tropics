"""ascatobs.build - assemble ASCAT orbits into the R2 JSON tree.

Output layout (R2 keys under the ``ascat/`` prefix; the viewer hydrates from these):

  ascat/manifest.json      index of recent passes (+ each pass's storm tags)
  ascat/current.json       the most-recent pass, inlined (the spotlight)
  ascat/{pass_id}.json     one pass: decoded + decimated wind-vector cells

Incremental rolling-window rebuild (idempotent). Each run:
  1. reads the prior manifest (the growing union + per-sensor watermark),
  2. lists the newest files for ASCAT-B and ASCAT-C,
  3. downloads + decodes + storm-tags only the IN-WINDOW files not already
     ingested (the filename-timestamp watermark makes a re-run a no-op),
  4. prunes passes older than the display window (and reaps their R2 JSON via a
     ``_pruned_ids.json`` sidecar - never a blanket --delete),
  5. writes current.json (newest pass) + the merged manifest.

A backfill widens the ingest window (``backfill_hours``) for a manual catch-up;
it still merges into the union, so backfilled passes survive later runs.

Guarded throughout: a dead source / empty listing yields no new passes (the prior
R2 stays live), and any hard failure raises to the CLI which fails the workflow
BEFORE the R2 sync, so a half-written tree is never published.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile

from . import (CREDIT, DISCLOSURE, SCHEMA_VERSION, SOURCE, decode, fetch,
               storms as _storms)

_now = lambda: _dt.datetime.now(_dt.timezone.utc)            # noqa: E731
SENSORS = ("metop-b", "metop-c")
MANIFEST_URL = "https://cdn.triple-a-tropics.com/ascat/manifest.json"


def _iso(d: _dt.datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s):
    if not s:
        return None
    try:
        d = _dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)
    except Exception:                                # noqa: BLE001
        return None


def _pass_id(meta: dict) -> str:
    """Stable, unique pass id from the parsed filename fields."""
    return f"{meta['sat']}_{meta['orbit']}_{meta['start'].strftime('%Y%m%dT%H%M%S')}"


def _fetch_prior_manifest(url: str | None) -> "tuple[str, dict | None]":
    """Read the prior manifest, distinguishing 'absent' (no manifest yet - a clean
    first run, bootstrap from empty) from 'error' (a transient fetch/parse failure
    on an EXISTING manifest - the caller must abort so an empty rebuild can't
    clobber last-known-good R2). Returns (status, manifest) with status in
    {'ok','absent','error'}."""
    if not url:
        return ("absent", None)            # merging disabled -> bootstrap from empty
    if url.startswith("http"):
        sep = "&" if "?" in url else "?"
        status, obj = fetch.get_json_status(f"{url}{sep}t={int(_now().timestamp())}")
        if status == "ok" and not isinstance(obj, dict):
            return ("error", None)
        return (status, obj if isinstance(obj, dict) else None)
    # local-file manifest (tests / offline)
    if not os.path.exists(url):
        return ("absent", None)
    try:
        with open(url, encoding="utf-8") as f:
            return ("ok", json.load(f))
    except Exception:                                # noqa: BLE001
        return ("error", None)


def _write(out_dir: str, key: str, obj) -> None:
    path = os.path.join(out_dir, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"), allow_nan=False)


def _manifest_entry(p: dict, pass_id: str) -> dict:
    """The compact per-pass record carried in manifest.json (no WVC arrays)."""
    return {
        "id": pass_id, "file": f"{pass_id}.json",
        "sensor": p["sensor"], "sat": p["sat"],
        "start_utc": p["start_utc"], "end_utc": p["end_utc"],
        "mid_utc": p["mid_utc"], "bbox": p["bbox"],
        "n_wvc": p["n_wvc"], "max_kt": p["max_kt"],
        "storms": p.get("storms", []),
    }


def build(out_dir: str, *, window_hours: int = 36, backfill_hours: int | None = None,
          sensors=SENSORS, max_new_per_run: int = 240, stride: int = 2,
          prior_manifest_url: str | None = MANIFEST_URL,
          api_key: str | None = None, log=print) -> dict:
    """Run one ingest cycle. Returns a small summary dict.

    ``window_hours`` is the display window (passes older than it are pruned + their
    R2 JSON reaped). ``backfill_hours`` (>= window_hours) widens the INGEST reach
    for a manual catch-up. ``max_new_per_run`` caps downloads per run (a cold first
    run or a wide backfill otherwise pulls dozens of orbits). The manifest is a
    growing union upserted by pass id, so passes survive later runs until pruned.
    """
    now = _now()
    api_key = api_key or fetch.api_key_from_env()
    if not api_key:
        log("ascat: no KNMI_API_KEY in env - cannot list/fetch; leaving R2 as-is")
        return {"mode": "noop", "reason": "no_api_key", "new": 0}

    ingest_hours = max(int(window_hours), int(backfill_hours or 0))
    window_start = now - _dt.timedelta(hours=window_hours)
    ingest_start = now - _dt.timedelta(hours=ingest_hours)

    # Read the prior manifest BEFORE doing anything destructive. A transient read
    # failure on an existing manifest must NOT rebuild from an empty union (that
    # would drop the rolling window / blank the product); abort so the workflow's
    # exit-code + manifest-presence guards skip the R2 sync and last-known-good
    # stays live. A clean 'absent' (first run / merge disabled) bootstraps empty.
    pstatus, prior = _fetch_prior_manifest(prior_manifest_url)
    if pstatus == "error":
        raise RuntimeError(
            "ascat: prior manifest fetch failed (not a clean 404) - aborting before "
            "the R2 sync so an empty rebuild cannot clobber last-known-good")
    prior = prior or {}
    by_id: dict[str, dict] = {p["id"]: p for p in prior.get("passes", [])
                              if isinstance(p, dict) and p.get("id")}

    # active storms once per run (read-only consumer of the published feed)
    active = _storms.active_storms()
    log(f"ascat: {len(active)} active system(s) in the live feed")

    # resolve the ASCAT-B/C coastal-NRT dataset names from the live KNMI catalog
    # (falls back to the pinned pair if the catalog is unreachable / unmatched)
    datasets = fetch.resolve_datasets(api_key=api_key, log=log)

    # ---- list newest files per sensor, pick the in-window, not-yet-ingested ----
    # Newest-first listing: 120 keys/sensor spans ~8 days at ~14 orbits/day, so the
    # incremental 36h window always fits one page. A wide backfill can exceed it,
    # so page deeper proportional to the ingest reach (so the older-but-in-window
    # tail is not silently missed).
    pages = 1 + max(0, int(ingest_hours * 14 / (24 * 120)))  # ~1 page per 120 orbits
    pages = min(pages, 12)
    candidates: list[dict] = []
    newest_seen: _dt.datetime | None = None
    for sk in sensors:
        recs = fetch.fetch_recent(sk, api_key=api_key, max_keys=120,
                                  datasets=datasets, max_pages=pages)
        if recs:
            sk_newest = max(r["start"] for r in recs)
            if newest_seen is None or sk_newest > newest_seen:
                newest_seen = sk_newest
        log(f"ascat: {sk}: {len(recs)} listed"
            + (f", newest {_iso(sk_newest)}" if recs else ""))
        for rec in recs:
            pid = _pass_id(rec)
            if pid in by_id:
                continue                             # already ingested (watermark)
            if rec["start"] < ingest_start:
                continue                             # older than the (ingest) window
            candidates.append({**rec, "pass_id": pid})
    # source-freshness verdict (loud but non-fatal: a stale/dead feed still leaves
    # last-known-good R2 live). OSI SAF coastal NRT latency is ~2h45m per orbit, so
    # a newest orbit older than ~12 h means the upstream feed has likely stalled.
    newest_age_h = None
    if newest_seen is not None:
        newest_age_h = (now - newest_seen).total_seconds() / 3600.0
        log(f"ascat: source freshness {'OK' if newest_age_h <= 12 else 'STALE'}: "
            f"newest orbit {_iso(newest_seen)} ({newest_age_h:.1f} h old)")
    else:
        log("ascat: source freshness: NO orbits listed (empty/unreachable feed)")
    # STALE = upstream stalled or the listing was empty/unreachable. We then keep
    # last-known-good passes visible instead of draining the window (below).
    stale = (newest_seen is None) or (newest_age_h is not None and newest_age_h > 12)

    # newest first, then cap (so a huge cold/backfill run stays bounded)
    candidates.sort(key=lambda c: c["start"], reverse=True)
    dropped = max(0, len(candidates) - max_new_per_run)
    candidates = candidates[:max_new_per_run]
    if dropped:
        log(f"ascat: capped {dropped} candidate orbit(s) over max_new_per_run")

    # ---- download + decode + tag + write each new pass ----
    new_ids: list[str] = []
    tmpdir = tempfile.mkdtemp(prefix="ascat_nc_")
    try:
        for c in candidates:
            url = fetch.get_download_url(c["dataset"], c["version"], c["name"],
                                         api_key=api_key)
            if not url:
                log(f"ascat: no download url for {c['name']} - skip")
                continue
            ncpath = os.path.join(tmpdir, c["pass_id"] + ".nc")
            if not fetch.download(url, ncpath):
                log(f"ascat: download failed {c['name']} - skip")
                continue
            try:
                p = decode.decode(ncpath, sat=c["sat"], stride=stride)
            except Exception as e:                   # noqa: BLE001
                log(f"ascat: decode failed {c['name']}: {type(e).__name__}: {e}")
                continue
            finally:
                _rm(ncpath)
            if p["n_wvc"] <= 0:
                log(f"ascat: {c['pass_id']} has no valid WVCs - skip")
                continue
            # Ingest-time tag: distance to the storm's CURRENT centre (a moving
            # proxy - we have no historical track here) within a WINDOW-generous
            # time pad. Swath gaps + the ~3 h NRT latency mean a pass is routinely
            # a few hours older than the storm's latest fix, and the rolling window
            # keeps older passes too; the canonical +/-3 h gate against the current
            # fix alone would drop most of them. CycloLab (Phase B) does the precise
            # +/-3 h / 750 km filter against the real best track it already holds;
            # this tag is the coarse candidate hint.
            p["storms"] = _storms.associate(
                active, p["wvc"]["la"], p["wvc"]["lo"], p.get("path"),
                max_dt_h=float(window_hours + 6))
            p["id"] = c["pass_id"]
            p["ingested_utc"] = _iso(now)
            _write(out_dir, f"{c['pass_id']}.json", p)
            by_id[c["pass_id"]] = _manifest_entry(p, c["pass_id"])
            new_ids.append(c["pass_id"])
    finally:
        _rmtree(tmpdir)
    log(f"ascat: ingested {len(new_ids)} new pass(es)")

    # ---- prune passes older than the display window; reap their R2 JSON ----
    # When the feed is STALE (upstream stalled / listing failed) we do NOT prune:
    # draining the window on a transient outage would blank the product, so we keep
    # the last-known-good passes visible (each shows its true age in the viewer)
    # until fresh orbits arrive and pruning resumes - recon's last-known-good rule.
    pruned: list[str] = []
    kept: dict[str, dict] = {}
    if stale:
        kept = dict(by_id)
        if by_id:
            log("ascat: feed STALE - keeping last-known-good passes (no prune)")
    else:
        for pid, entry in by_id.items():
            mid = _parse_iso(entry.get("mid_utc")) or _parse_iso(entry.get("start_utc"))
            if mid is not None and mid < window_start and pid not in new_ids:
                pruned.append(pid)
            else:
                kept[pid] = entry

    passes = sorted(kept.values(),
                    key=lambda e: e.get("start_utc") or "", reverse=True)
    current_id = passes[0]["id"] if passes else None

    # Last-known-good guard: never publish an EMPTY manifest unless the prior union
    # was confirmed empty too (a real prune-to-empty / off-season). If this run
    # produced nothing and there was no prior union to reconcile against, write
    # NOTHING - the workflow's manifest-presence guard then skips the R2 sync and
    # last-known-good stays live. (A transient empty listing lands here harmlessly;
    # the 'error' prior-read case already raised above.) A genuine prune-to-empty
    # has a truthy prior['passes'], so the truthful empty manifest is still written.
    if not passes and not prior.get("passes"):
        log("ascat: empty result with no prior union - skipping write so R2 stays live")
        return {"mode": "noop", "reason": "empty_no_prior",
                "new": len(new_ids), "total": 0, "pruned": 0,
                "active_storms": len(active), "current": None}

    _write(out_dir, "_pruned_ids.json", sorted(pruned))

    # ---- current.json: the newest pass, inlined (instant spotlight) ----
    if current_id and current_id in new_ids:
        cur_full = json.load(open(os.path.join(out_dir, f"{current_id}.json")))
    elif current_id:
        # newest pass wasn't (re)written this run - re-read it from R2 by ref so
        # current.json always points at a real, present pass file.
        cur_full = _fetch_pass(current_id)
    else:
        cur_full = None
    current = {"generated_utc": _iso(now), "has_recent": bool(current_id),
               "current_id": current_id, "pass": cur_full,
               "disclosure": DISCLOSURE}
    _write(out_dir, "current.json", current)

    manifest = {
        "schema_version": SCHEMA_VERSION, "generated_utc": _iso(now),
        "source": SOURCE, "credit": CREDIT, "disclosure": DISCLOSURE,
        "window_hours": window_hours,
        "latency_note": ("Near-real-time feed (OSI SAF coastal latency ~3 h, per "
                         "orbit); swaths are intermittent, so the newest pass over "
                         "any one storm may be a few hours old."),
        "passes": passes, "current_id": current_id,
        "watermark": _watermark(passes),
        "stale": bool(stale),
        "newest_orbit_age_h": (round(newest_age_h, 1)
                               if newest_age_h is not None else None),
    }
    _write(out_dir, "manifest.json", manifest)

    summary = {"mode": "backfill" if backfill_hours else "incremental",
               "new": len(new_ids), "total": len(passes),
               "pruned": len(pruned), "active_storms": len(active),
               "current": current_id}
    log(f"ascat: {summary}")
    return summary


def _watermark(passes: list[dict]) -> dict:
    """Newest start_utc seen per satellite (for visibility / debugging)."""
    wm: dict[str, str] = {}
    for p in passes:
        sat, st = p.get("sat"), p.get("start_utc")
        if sat and st and st > wm.get(sat, ""):
            wm[sat] = st
    return wm


def _fetch_pass(pass_id: str) -> dict | None:
    """Re-read a pass JSON from R2 (used for current.json when the newest pass was
    not rewritten this run). None on failure -> current.pass just stays null and
    the viewer falls back to the manifest pass list."""
    url = f"https://cdn.triple-a-tropics.com/ascat/{pass_id}.json"
    r = fetch._request("GET", url, api_key=None)
    if r is None:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _rm(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _rmtree(path):
    import shutil
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:                                # noqa: BLE001
        pass
