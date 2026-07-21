"""sarobs.build - one poller tick: discover, gate, render, publish.

Never-miss contract:
  * WATERMARK lives in R2: each storm's index.json lists the pass stems
    already published; only unseen stems are fetched/rendered (idempotent —
    stems are unique per pass and immutable upstream).
  * SWEEP CADENCE: the currently-active storms are checked every tick via
    the year listing; a storm whose page count matches its index is skipped
    without touching its page. A full re-listing of every in-scope storm
    happens whenever the manifest's last_sweep_utc is older than
    SWEEP_EVERY_S (self-heals passes added late to an old storm).
  * PER-TICK CAP: at most ``max_new`` passes render per tick (~10 MB nc +
    render each); the rest backfill on following ticks.
  * ORDER: pass PNG + thumb first, then the storm index, then the manifest —
    the index never points at files that did not land.
  * FAULT ISOLATION: each pass is try/except'd; a bad file logs and retries
    naturally next tick (it never enters the index).
Off-season: the year listing shows no in-scope storms -> the tick is a
listing-read no-op.
"""
from __future__ import annotations

import datetime as dt
import json

from . import discover, fetch, render, salinity
from .store import make_store

SCHEMA_VERSION = 1
CACHE_JSON = "public, max-age=60"
CACHE_MEDIA = "public, max-age=31536000, immutable"
SWEEP_EVERY_S = 3600.0
MAX_FAILS = 3
CREDIT = ("RADARSAT Constellation Mission imagery, Government of Canada; "
          "processed at NOAA/NESDIS/STAR/SOCD")

_now = lambda: dt.datetime.now(dt.timezone.utc)  # noqa: E731


def _iso(t: dt.datetime | None) -> str | None:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ") if t else None


def _age_s(stamp: str | None) -> float:
    if not stamp:
        return float("inf")
    try:
        t = dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc)
        return (_now() - t).total_seconds()
    except ValueError:
        return float("inf")


def build(store_spec: str, *, year: int | None = None, max_new: int = 6,
          geo_dir: str = ".", force_sweep: bool = False, rerender: bool = False,
          extra_years: tuple = (), log=print) -> dict:
    store = make_store(store_spec)
    year = year or _now().year
    # low-salinity reliability grid (optional, additive): the SAR render
    # hatches low-salinity water where C-band winds are less reliable. A
    # missing/failed read just renders without the overlay.
    salinity_grid = None
    try:
        sbytes = store.get_bytes("sar/salinity/mask.nc")
        if sbytes:
            salinity_grid = salinity.read_grid(sbytes)
    except Exception as e:                       # noqa: BLE001 — additive cue
        log(f"sar: salinity mask unavailable ({type(e).__name__}) - no overlay")
    manifest = store.get_json("sar/manifest.json") or {
        "schema_version": SCHEMA_VERSION, "storms": []}
    by_slug = {s["slug"]: s for s in manifest.get("storms", [])}

    full_sweep = force_sweep or \
        _age_s(manifest.get("last_sweep_utc")) > SWEEP_EVERY_S

    # ---- discover in-scope storms (year listing = authoritative) ----
    years = (year,) + tuple(extra_years)
    storm_ids: list[tuple[int, str]] = []
    for y in years:
        for sid in discover.storms_for_year(y):
            storm_ids.append((y, sid))
    if not storm_ids:
        log("sar: no in-scope storms listed (off-season or listing "
            "unreachable) - no-op tick")
        return {"storms": 0, "new_passes": 0}

    budget = max_new
    new_total = 0
    touched = False
    for y, sid in storm_ids:
        slug = discover.storm_slug(sid)
        fields = discover.storm_fields(sid)
        entry = by_slug.get(slug)
        index = None
        # Cheap skip: outside a full sweep, only storms that are NEW, still
        # BACKFILLING (an earlier tick's budget truncated them), or recently
        # active (latest pass < 8 days) get their page re-read every tick.
        if not full_sweep and not rerender and entry is not None \
                and entry.get("n_passes") and not entry.get("backfilling"):
            if _age_s(entry.get("latest_utc")) > 8 * 86400:
                continue
        passes = discover.passes_for_storm(y, sid)
        if not passes:
            continue
        index = store.get_json(f"sar/{slug}/index.json") or {
            "slug": slug, "storm_id": sid, "passes": []}
        known = {p["stem"] for p in index.get("passes", [])}
        # dead-letter: a stem that failed MAX_FAILS times stops burning the
        # tick budget (and stops hammering the upstream) until a full sweep
        # after an upstream fix would... it stays skipped; log once per tick.
        fails = dict(index.get("failed") or {})
        dead = {k for k, v in fails.items() if v >= MAX_FAILS}
        fresh = [p for p in passes
                 if (rerender or p["stem"] not in known)
                 and p["stem"] not in dead]
        if dead:
            log(f"sar: {sid}: {len(dead)} pass(es) dead-lettered "
                f"(>= {MAX_FAILS} failures)")
        if not fresh and entry is not None:
            continue

        added = []
        fails_changed = False
        truncated = False
        for p in fresh:
            if budget <= 0:
                truncated = True
                log(f"sar: per-tick budget reached - {sid} continues "
                    "next tick")
                break
            budget -= 1
            try:
                nc = fetch.get_bytes(p["url"])
                if not nc:
                    fails[p["stem"]] = fails.get(p["stem"], 0) + 1
                    fails_changed = True
                    log(f"sar: fetch failed {p['stem']} "
                        f"(attempt {fails[p['stem']]})")
                    continue
                png, thumb, stats = render.render_pass(nc, {
                    "stem": p["stem"], "sat": p["sat"], "pol": p["pol"],
                    "t": p["t"], "storm_name": fields["name"],
                    "atcf": fields["atcf"]}, geo_dir=geo_dir,
                    salinity=salinity_grid)
                store.put(f"sar/{slug}/{p['stem']}.png", png,
                          "image/png", CACHE_MEDIA)
                store.put(f"sar/{slug}/{p['stem']}_th.jpg", thumb,
                          "image/jpeg", CACHE_MEDIA)
                added.append({
                    "stem": p["stem"], "t": stats["t"] or _iso(p["t"]),
                    "sat": p["sat"], "pol": p["pol"],
                    "png": f"{p['stem']}.png", "thumb": f"{p['stem']}_th.jpg",
                    "max_ms": stats["max_ms"], "peak_ms": stats["peak_ms"],
                    "peak_kt": stats["peak_kt"], "n_cells": stats["n_cells"],
                    "bbox": stats["bbox"]})
                new_total += 1
                log(f"sar: rendered {sid} {p['stem']} "
                    f"(max {stats['max_ms']} m/s)")
            except Exception as e:               # noqa: BLE001
                fails[p["stem"]] = fails.get(p["stem"], 0) + 1
                fails_changed = True
                log(f"sar: pass {p['stem']} failed: "
                    f"{type(e).__name__}: {e} "
                    f"(attempt {fails[p['stem']]})")

        if added or fails_changed or entry is None:
            # merge by stem (a re-render REPLACES the prior entry)
            by_stem = {p["stem"]: p for p in index.get("passes", [])}
            for p in added:
                by_stem[p["stem"]] = p
            merged = list(by_stem.values())
            merged.sort(key=lambda p: p.get("t") or "", reverse=True)
            index.update({
                "schema_version": SCHEMA_VERSION, "slug": slug,
                "storm_id": sid, "name": fields["name"],
                "basin": fields["basin"], "year": fields["year"],
                "atcf": fields["atcf"], "credit": CREDIT,
                "updated_utc": _iso(_now()), "passes": merged,
                "failed": fails})
            store.put(f"sar/{slug}/index.json",
                      json.dumps(index, separators=(",", ":")).encode(),
                      "application/json", CACHE_JSON)
            by_slug[slug] = {
                "slug": slug, "storm_id": sid, "name": fields["name"],
                "basin": fields["basin"], "year": fields["year"],
                "atcf": fields["atcf"], "n_passes": len(merged),
                "latest_utc": merged[0].get("t") if merged else None,
                "latest_thumb": (f"{slug}/{merged[0]['thumb']}"
                                 if merged else None),
                "backfilling": bool(truncated or
                                    len(added) + len([k for k in fails
                                                      if fails.get(k, 0) >= MAX_FAILS
                                                      and k not in known])
                                    < len(fresh))}
            touched = True

    # ---- manifest (storm index, newest activity first) ----
    if touched or full_sweep:
        storms = sorted(by_slug.values(),
                        key=lambda s: s.get("latest_utc") or "", reverse=True)
        manifest = {
            "schema_version": SCHEMA_VERSION, "generated_utc": _iso(_now()),
            "credit": CREDIT,
            "note": ("Storm-tasked SAR: passes exist only when a storm was "
                     "tasked for acquisition."),
            "last_sweep_utc": _iso(_now()) if full_sweep
            else manifest.get("last_sweep_utc"),
            "storms": storms}
        store.put("sar/manifest.json",
                  json.dumps(manifest, separators=(",", ":")).encode(),
                  "application/json", CACHE_JSON)

    summary = {"storms": len(by_slug), "new_passes": new_total,
               "full_sweep": full_sweep}
    log(f"sar: {summary}")
    return summary
