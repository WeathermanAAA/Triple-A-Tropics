"""qscatobs.build - resumable per-storm archive build into the R2 store.

R2 layout (all under qscat/):
  qscat/manifest.json                     {schema_version, storms: [...]}
  qscat/{slug}/index.json                 per-storm pass index
  qscat/{slug}/rev{rev}.png               rendered pass

slug = {basin}{num:02d}{season}_{storm} lowercased (al122005_katrina).
Resumable: a pass already in the storm index is never refetched; the
source archive is static (1999-2009), so a completed storm never changes.
"""
from __future__ import annotations

import datetime as dt
import json

from . import decode, fetch, render

SCHEMA_VERSION = 1
CACHE_MEDIA = "public, max-age=86400"
CACHE_JSON = "public, max-age=3600"


def _now():
    return dt.datetime.now(dt.timezone.utc)


def storm_slug(basin: str, num: int, season: int, storm: str) -> str:
    """Slug WITHOUT a storm number: BYU's colocation 'Number' column is its
    own per-season count and does NOT match ATCF numbering (Katrina 2005 is
    ATCF AL12 but BYU number 11) — publishing it would mislead. Names are
    unique within a basin+season, which is all the archive needs."""
    return f"{basin.lower()}{season}_{storm.lower()}"


def build_storm(store, basin: str, season: int, storm: str,
                coloc_rows=None, *, geo_dir: str = ".", max_new: int = 999,
                log=print) -> dict:
    """Fetch+decode+render every WRave3 pass of one storm. Skips passes
    already indexed; a fetch/decode failure skips that pass (retryable on
    a later run) without failing the storm."""
    import tempfile
    import os
    coloc_rows = coloc_rows if coloc_rows is not None \
        else fetch.load_colocation()
    colocs = fetch.storm_colocs(coloc_rows, basin, season, storm)
    passes = fetch.list_passes(basin, season, storm)
    if not passes:
        log(f"qscat: {storm} {season}: no passes listed")
        return {"passes": 0, "new": 0}
    num = next((c["num"] for c in colocs.values()), 0)
    slug = storm_slug(basin, num, season, storm)
    idx = store.get_json(f"qscat/{slug}/index.json") or {
        "schema_version": SCHEMA_VERSION, "storm": storm.title(),
        "basin": basin.upper(), "season": season, "num": num,
        "passes": []}
    have = {p["rev"] for p in idx["passes"]}
    new = 0
    for p in passes:
        if p["rev"] in have or new >= max_new:
            continue
        raw = fetch.get_bytes(fetch.storm_dir(basin, season, storm)
                              + p["file"], timeout=240)
        if not raw:
            log(f"qscat: {slug} rev{p['rev']}: fetch failed (retry later)")
            continue
        fd, path = tempfile.mkstemp(suffix=".gz")
        os.close(fd)
        try:
            with open(path, "wb") as fh:
                fh.write(raw)
            d = decode.load_byu_hrwind(path)
            c = colocs.get(p["rev"], {})
            png, stats = render.render_pass(d, {
                "storm": storm, "basin": basin, "season": season,
                "rev": p["rev"], "t": c.get("t"),
                "bt_wind_kt": c.get("bt_wind_kt"),
                "bt_lat": c.get("bt_lat"), "bt_lon": c.get("bt_lon"),
                "type": c.get("type")}, geo_dir=geo_dir)
        except Exception as e:                  # noqa: BLE001 — skip pass
            log(f"qscat: {slug} rev{p['rev']}: {type(e).__name__}: {e}")
            continue
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        store.put(f"qscat/{slug}/rev{p['rev']}.png", png, "image/png",
                  CACHE_MEDIA)
        idx["passes"].append({
            "rev": p["rev"], "t": stats["t"],
            "bt_wind_kt": c.get("bt_wind_kt"), "bt_type": c.get("type")})
        new += 1
        log(f"qscat: {slug} rev{p['rev']} rendered")
    if new:
        idx["passes"].sort(key=lambda x: x["rev"])
        idx["updated_utc"] = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
        store.put(f"qscat/{slug}/index.json",
                  json.dumps(idx, separators=(",", ":")).encode(),
                  "application/json", CACHE_JSON)
        _update_manifest(store, idx, slug)
    return {"passes": len(passes), "new": new, "slug": slug}


def _update_manifest(store, idx: dict, slug: str) -> None:
    man = store.get_json("qscat/manifest.json") or {
        "schema_version": SCHEMA_VERSION, "storms": []}
    entry = {"slug": slug, "storm": idx["storm"], "basin": idx["basin"],
             "season": idx["season"], "num": idx["num"],
             "n_passes": len(idx["passes"]),
             "peak_bt_kt": max((p.get("bt_wind_kt") or 0
                                for p in idx["passes"]), default=None)}
    man["storms"] = [s for s in man["storms"] if s["slug"] != slug]
    man["storms"].append(entry)
    man["storms"].sort(key=lambda s: (s["season"], s["basin"], s["num"]))
    man["generated_utc"] = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
    store.put("qscat/manifest.json",
              json.dumps(man, separators=(",", ":")).encode(),
              "application/json", CACHE_JSON)
