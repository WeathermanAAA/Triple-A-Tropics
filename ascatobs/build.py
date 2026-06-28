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
               podaac, storms as _storms)

_now = lambda: _dt.datetime.now(_dt.timezone.utc)            # noqa: E731
SENSORS = ("metop-b", "metop-c")
MANIFEST_URL = "https://cdn.triple-a-tropics.com/ascat/manifest.json"

# Display window. KNMI publishes the ASCAT coastal feed in a ~daily BATCH, so the
# newest orbit ages in a sawtooth (~5 h old just after a batch, up to ~22-24 h old
# just before the next one). A window only modestly wider than that batch interval
# prunes old passes faster than fresh batches arrive, so mid-cycle the globe drains
# to a few sparse swaths. 60 h keeps a full ~36 h of orbits in-window even at the
# trough (~40+ passes, both satellites) so the Global view stays near-completely
# tiled all day long - see the viewer's GLOBAL_MAX_PASSES cap, which this feeds.
DEFAULT_WINDOW_HOURS = 60

# Health bound, DECOUPLED from the prune-stale window below, and SOURCE-aware. The
# bound is the age past which the newest orbit means a real upstream stall (a
# shouting log + manifest health flag), NOT the normal cadence lag. PO.DAAC is
# per-orbit (~2-4 h latency) so >8 h means several orbits were missed; KNMI is a
# ~daily batch (newest legitimately ages to ~24-30 h) so its bound is 36 h. This is
# a health ALERT only; the wider window_hours still governs pruning so a transient
# outage never drains the product.
HEALTH_STALE_H = 36.0           # KNMI fallback (daily batch)
PODAAC_HEALTH_STALE_H = 8.0     # PO.DAAC primary (per-orbit NRT)


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


def build(out_dir: str, *, window_hours: int = DEFAULT_WINDOW_HOURS,
          backfill_hours: int | None = None,
          sensors=SENSORS, max_new_per_run: int = 240, stride: int = 2,
          prior_manifest_url: str | None = MANIFEST_URL,
          source: str | None = None, api_key: str | None = None,
          earthdata_token: str | None = None, log=print) -> dict:
    """Run one ingest cycle. Returns a small summary dict.

    ``window_hours`` is the display window (passes older than it are pruned + their
    R2 JSON reaped). ``backfill_hours`` (>= window_hours) widens the INGEST reach
    for a manual catch-up. ``max_new_per_run`` caps downloads per run (a cold first
    run or a wide backfill otherwise pulls dozens of orbits). The manifest is a
    growing union upserted by pass id, so passes survive later runs until pruned.
    """
    now = _now()
    # ---- source selection: PO.DAAC (Earthdata, per-orbit ~2-4 h) is primary;
    # KNMI (~daily batch) is the automatic fallback so the product never goes dark
    # if the Earthdata creds are absent. ASCAT_SOURCE=knmi forces the fallback first.
    # The PRODUCT is identical (OSI SAF 12.5 km coastal); only the listing+download+
    # auth differ - decode/mask/decimate/window/health/manifest are shared.
    pref = (source or os.environ.get("ASCAT_SOURCE") or "podaac").strip().lower()
    ed_token = earthdata_token or podaac.creds_from_env(log=log)
    knmi_key = api_key or fetch.api_key_from_env()
    order = ["knmi", "podaac"] if pref == "knmi" else ["podaac", "knmi"]
    source_name, creds = None, None
    for name in order:
        if name == "podaac" and ed_token:
            source_name, creds = "podaac", ed_token
            break
        if name == "knmi" and knmi_key:
            source_name, creds = "knmi", knmi_key
            break
    if not source_name:
        log("ascat: no source creds - need EARTHDATA_TOKEN (or EARTHDATA_USERNAME+"
            "EARTHDATA_PASSWORD) for PO.DAAC, or KNMI_API_KEY for the fallback; "
            "leaving R2 as-is")
        return {"mode": "noop", "reason": "no_api_key", "new": 0}
    using_podaac = (source_name == "podaac")
    if using_podaac:
        log("ascat: source = PO.DAAC (per-orbit ~2-4 h NRT)")
    else:
        log("ascat: source = KNMI fallback (~daily batch; set EARTHDATA_TOKEN for "
            "PO.DAAC NRT)")
    health_bound = PODAAC_HEALTH_STALE_H if using_podaac else HEALTH_STALE_H

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

    # resolve the source: PO.DAAC collection ids are fixed; KNMI dataset names are
    # re-derived from the live KNMI catalog (validated, pinned-fallback).
    datasets = None if using_podaac else fetch.resolve_datasets(api_key=creds, log=log)

    # ---- list newest files per sensor, pick the in-window, not-yet-ingested ----
    # Newest-first listing: 120 keys/sensor spans ~8 days at ~14 orbits/day, so the
    # incremental 60h window always fits one page. A wide backfill can exceed it,
    # so page deeper proportional to the ingest reach (so the older-but-in-window
    # tail is not silently missed). Both sources return the same record shape
    # (start/sat/orbit/name) the window filter + watermark + decode consume.
    pages = 1 + max(0, int(ingest_hours * 14 / (24 * 120)))  # ~1 page per 120 orbits
    pages = min(pages, 12)
    candidates: list[dict] = []
    newest_seen: _dt.datetime | None = None
    for sk in sensors:
        if using_podaac:
            recs = podaac.fetch_recent(sk, token=creds, since=ingest_start,
                                       max_keys=120, max_pages=pages, log=log)
        else:
            recs = fetch.fetch_recent(sk, api_key=creds, max_keys=120,
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
    # last-known-good R2 live). STALE here is the PRUNE-protection bound (= the whole
    # display window): even the newest orbit predates the window, so the feed has
    # genuinely stopped - keep last-known-good rather than draining. The tighter,
    # source-aware HEALTH bound (below) is what surfaces a real stall loudly.
    newest_age_h = None
    stale_h = float(window_hours)
    if newest_seen is not None:
        newest_age_h = (now - newest_seen).total_seconds() / 3600.0
        log(f"ascat: source freshness {'OK' if newest_age_h <= stale_h else 'STALE'}: "
            f"newest orbit {_iso(newest_seen)} ({newest_age_h:.1f} h old, "
            f"stale>{stale_h:.0f}h)")
    else:
        log("ascat: source freshness: NO orbits listed (empty/unreachable feed)")
    # STALE = the feed has stalled past the display window, or the listing was
    # empty/unreachable. We then keep last-known-good passes visible (below).
    stale = (newest_seen is None) or (newest_age_h is not None and newest_age_h > stale_h)

    # ---- HEALTH guard (decoupled from the prune-stale window, SOURCE-aware) -------
    # A separate, TIGHTER bound so a real upstream stall surfaces loudly instead of
    # hiding inside the wide last-known-good window. PO.DAAC is per-orbit, so newest
    # > 8 h means orbits were actually missed; KNMI's daily batch legitimately ages
    # the newest to ~24-30 h, so its bound is 36 h.
    health, health_reason = "ok", ""
    if newest_seen is None:
        health, health_reason = "stale", "no orbits listed (feed empty/unreachable)"
    elif newest_age_h is not None and newest_age_h > health_bound:
        health = "stale"
        health_reason = (f"newest orbit {newest_age_h:.1f} h old "
                         f"(> {health_bound:.0f} h {source_name} health bound)")
    if health == "stale":
        log("ascat: !! FEED HEALTH STALE -- " + health_reason
            + f" -- the {source_name} feed appears STALLED; last-known-good stays "
              "live and coverage will drain until fresh orbits land. Investigate.")

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
            if using_podaac:
                url = c.get("download_url")
            else:
                url = fetch.get_download_url(c["dataset"], c["version"], c["name"],
                                             api_key=creds)
            if not url:
                log(f"ascat: no download url for {c['name']} - skip")
                continue
            ncpath = os.path.join(tmpdir, c["pass_id"] + ".nc")
            ok = (podaac.download(url, ncpath, token=creds) if using_podaac
                  else fetch.download(url, ncpath))
            if not ok:
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
            # time pad. Swath gaps + the ~day feed latency mean a pass is routinely
            # many hours older than the storm's latest fix, and the rolling window
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

    # Source-accurate latency wording (PO.DAAC NRT vs the KNMI daily-batch fallback).
    latency_note = (
        "Distributed via NASA PO.DAAC (Earthdata) near-real-time - per-orbit, "
        "typically a few hours old; swaths are intermittent, so the newest pass over "
        "any one storm may be a few hours old."
        if using_podaac else
        "Served from the KNMI Open Data fallback (~a day behind real time); swaths "
        "are intermittent, so the newest pass over any one storm may be several "
        "hours to about a day old.")
    manifest = {
        "schema_version": SCHEMA_VERSION, "generated_utc": _iso(now),
        "source": SOURCE, "credit": CREDIT, "disclosure": DISCLOSURE,
        "source_name": source_name,        # "podaac" (primary) | "knmi" (fallback)
        "window_hours": window_hours,
        "latency_note": latency_note,
        "passes": passes, "current_id": current_id,
        "watermark": _watermark(passes),
        "stale": bool(stale),
        "newest_orbit_age_h": (round(newest_age_h, 1)
                               if newest_age_h is not None else None),
        # Health: "ok" within the source's normal cadence, "stale" when the newest
        # orbit passes the source-aware health bound (a real stall). The viewer shows
        # an amber "feed delayed" badge on "stale" so a stall is visible, not silent.
        "health": health,
        "health_reason": health_reason,
        "health_stale_h": health_bound,
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
