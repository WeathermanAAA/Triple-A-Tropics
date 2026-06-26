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


def _fetch_prior_manifest(url: str | None) -> dict | None:
    if not url:
        return None
    try:
        if url.startswith("http"):
            sep = "&" if "?" in url else "?"
            body = fetch._request(                   # reuse the guarded GET
                "GET", f"{url}{sep}t={int(_now().timestamp())}", api_key=None)
            body = body.text if body is not None else None
        else:
            body = open(url, encoding="utf-8").read() if os.path.exists(url) else None
        return json.loads(body) if body else None
    except Exception:                                # noqa: BLE001
        return None


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
    run or a fresh daily batch otherwise pulls dozens of orbits). The manifest is
    a growing union upserted by pass id, so passes survive later runs until pruned.
    """
    now = _now()
    api_key = api_key or fetch.api_key_from_env()
    if not api_key:
        log("ascat: no KNMI_API_KEY in env - cannot list/fetch; leaving R2 as-is")
        return {"mode": "noop", "reason": "no_api_key", "new": 0}

    ingest_hours = max(int(window_hours), int(backfill_hours or 0))
    window_start = now - _dt.timedelta(hours=window_hours)
    ingest_start = now - _dt.timedelta(hours=ingest_hours)

    prior = _fetch_prior_manifest(prior_manifest_url) or {}
    by_id: dict[str, dict] = {p["id"]: p for p in prior.get("passes", [])
                              if isinstance(p, dict) and p.get("id")}

    # active storms once per run (read-only consumer of the published feed)
    active = _storms.active_storms()
    log(f"ascat: {len(active)} active system(s) in the live feed")

    # ---- list newest files per sensor, pick the in-window, not-yet-ingested ----
    candidates: list[dict] = []
    for sk in sensors:
        recs = fetch.fetch_recent(sk, api_key=api_key, max_keys=120)
        log(f"ascat: {sk}: {len(recs)} listed")
        for rec in recs:
            pid = _pass_id(rec)
            if pid in by_id:
                continue                             # already ingested (watermark)
            if rec["start"] < ingest_start:
                continue                             # older than the (ingest) window
            candidates.append({**rec, "pass_id": pid})
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
            # time pad. The KNMI feed is daily-batched, so a pass is routinely
            # hours-to-~a-day older than the storm's latest fix; the canonical
            # +/-3 h gate would reject every batched pass. CycloLab (Phase B) does
            # the precise +/-3 h / 750 km filter against the real best track it
            # already holds; this tag is the coarse candidate hint.
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
    pruned: list[str] = []
    kept: dict[str, dict] = {}
    for pid, entry in by_id.items():
        mid = _parse_iso(entry.get("mid_utc")) or _parse_iso(entry.get("start_utc"))
        if mid is not None and mid < window_start and pid not in new_ids:
            pruned.append(pid)
        else:
            kept[pid] = entry
    _write(out_dir, "_pruned_ids.json", sorted(pruned))

    passes = sorted(kept.values(),
                    key=lambda e: e.get("start_utc") or "", reverse=True)
    current_id = passes[0]["id"] if passes else None

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
        "latency_note": ("KNMI Open Data publishes ASCAT in daily batches; "
                         "the newest pass may be several hours to about a day old."),
        "passes": passes, "current_id": current_id,
        "watermark": _watermark(passes),
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
