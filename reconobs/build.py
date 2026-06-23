"""reconobs.build - assemble recon bulletins into the R2 JSON tree.

Output layout (mirrors the R2 keys under the ``recon/`` prefix; the viewer
hydrates from these):

  recon/manifest.json          index of storms + the current spotlight
  recon/tcpod.json             parsed Plan of the Day
  recon/current.json           the live/most-recent mission (auto-updates)
  recon/{slug}/recon.json      per-storm mission index
  recon/{slug}/{mission}.json  one mission's full track + VDM + sondes

Stateless rolling-window rebuild (idempotent): each run re-decodes the last
``window_days`` of bulletins and rewrites those storms. Re-decoding the same
bulletins yields byte-identical points (dedup by timestamp), so a re-run is a
no-op; storms older than the window keep their prior R2 JSON untouched.
``--backfill-year`` widens the window to a whole season for the archive batch.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re

from . import ingest, missions as _m, fetch
from .tcpod import parse_tcpod

SCHEMA_VERSION = 1
_now = lambda: _dt.datetime.now(_dt.timezone.utc)   # noqa: E731


def _fetch_prior_manifest(url: str | None) -> dict | None:
    """Read the existing manifest (the growing union) so this run can merge
    into it rather than replace it. http(s) URL -> cache-busted GET; a local
    path -> read it; None/missing/parse-fail -> None (start fresh)."""
    if not url:
        return None
    try:
        if url.startswith("http"):
            sep = "&" if "?" in url else "?"
            body = fetch.get(f"{url}{sep}t={int(_now().timestamp())}",
                             timeout=20)
        else:
            body = open(url, encoding="utf-8").read() \
                if os.path.exists(url) else None
        return json.loads(body) if body else None
    except Exception:                            # noqa: BLE001
        return None


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def _storm_slug(basin: str, name: str, year: int, atcf: str | None) -> str:
    if atcf:
        return atcf.lower()
    return f"{basin.lower()}_{_slugify(name) or 'unknown'}_{year}"


def _write(out_dir: str, key: str, obj) -> None:
    path = os.path.join(out_dir, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"), allow_nan=False)


def _best_name(missions: list[dict]) -> str:
    """Prefer a real (alpha, non-INVEST) TC name over INVEST/numeric, so a
    storm's invest-stage + named-stage sorties read under the named identity."""
    real = [m["storm_name"] for m in missions
            if not m["is_invest"] and m["storm_name"].isalpha()]
    if real:
        return max(real, key=len).title()
    return (missions[0]["storm_name"] or "Unknown").title()


def _group_missions(all_missions: list[dict], year: int) -> dict[str, dict]:
    """Group missions into storms keyed by a stable slug. atcf (from a VDM) is
    authoritative and unifies a storm's invest-stage + named sorties; absent an
    atcf, group by (basin, name)."""
    # resolve a slug per mission: atcf if the mission (or a same name+basin
    # sibling) carries one, else basin_name_year.
    atcf_by_key: dict[tuple, str] = {}
    for mm in all_missions:
        a = mm.get("atcf") or next((c["atcf"] for c in mm.get("vdm_centers", [])
                                    if c.get("atcf")), None)
        if a:
            atcf_by_key.setdefault((mm["basin"], mm["storm_name"]), a)
    storms: dict[str, dict] = {}
    for mm in all_missions:
        atcf = mm.get("atcf") or atcf_by_key.get((mm["basin"],
                                                  mm["storm_name"]))
        slug = _storm_slug(mm["basin"], mm["storm_name"], year, atcf)
        st = storms.setdefault(slug, {
            "slug": slug, "basin": mm["basin"], "year": year,
            "atcf": atcf, "missions": []})
        st["missions"].append(mm)
        if atcf and not st["atcf"]:
            st["atcf"] = atcf
    for st in storms.values():
        st["name"] = _best_name(st["missions"])
        st["is_invest"] = all(m["is_invest"] for m in st["missions"])
    return storms


def _month_bounds(year: int, month: int):
    since = _dt.datetime(year, month, 1, tzinfo=_dt.timezone.utc)
    nm_y, nm_m = (year + 1, 1) if month == 12 else (year, month + 1)
    return since, _dt.datetime(nm_y, nm_m, 1, tzinfo=_dt.timezone.utc)


def build(out_dir: str, *, window_days: int = 4, year: int | None = None,
         basins=("AL", "EP"), backfill_year: int | None = None,
         backfill_month: int | None = None,
         prior_manifest_url: str | None = None,
         stagger_s: float = 0.0, log=print) -> dict:
    """Run one ingest+assemble cycle. Returns a small summary dict.

    Incremental (default) owns the live "current" data + rolling-window
    storms. ``backfill_year`` (optionally + ``backfill_month`` to bound a
    busy year by month) ADDS historical storms: it merges them into the
    existing manifest and leaves current.json / tcpod.json / the manifest's
    current_slug+tcpod_number untouched, so a backfill never regresses the
    live current-season viewer. Either way the manifest is the growing UNION
    (read the prior one from ``prior_manifest_url`` and upsert by slug), so
    backfilled storms survive subsequent incremental runs."""
    now = _now()
    is_backfill = bool(backfill_year)
    year = backfill_year or year or now.year
    until = None
    if is_backfill:
        if backfill_month:
            since, until = _month_bounds(backfill_year, backfill_month)
        else:
            since, until = _month_bounds(backfill_year, 1)
            until = _dt.datetime(backfill_year + 1, 1, 1,
                                 tzinfo=_dt.timezone.utc)
    else:
        since = now - _dt.timedelta(days=window_days)

    # ---- prior manifest (the growing union; merged into below) ----
    prior = _fetch_prior_manifest(prior_manifest_url)

    # ---- TCPOD (incremental only; a backfill leaves the live one) ----
    tcpod = None
    if not is_backfill:
        tcpod_raw = ingest.gather_tcpod()
        tcpod = parse_tcpod(tcpod_raw) if tcpod_raw else {"pil": "REPRPD",
                                                          "raw": "",
                                                          "basins": {}}
        tcpod["fetched_utc"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        _write(out_dir, "tcpod.json", tcpod)

    # ---- gather bulletins ----
    win = ingest.gather_window(year, since, until=until, basins=basins,
                               stagger_s=stagger_s, log=log)
    if win["dropped"]:
        log(f"recon: capped {win['dropped']} over-window archive files")
    live_blocks = ingest.gather_live_hdob(basins=basins) if not is_backfill \
        else []

    # ---- decode + group per basin (basin tag travels with the mission) ----
    all_missions: list[dict] = []
    for b in basins:
        bag = win["basins"].get(b, {"hdob": [], "vdm": [], "sonde": []})
        hdob = list(bag["hdob"])
        if b == "AL":
            hdob += [x for x in live_blocks]      # live blocks join AL/EP by
        mis = _m.build_missions(hdob)             # their own ids (harmless dup)
        # keep only TC/invest sorties (drop research/training/ferry) BEFORE
        # attaching VDM/sondes, so a fix never binds to a mission we discard.
        mis = {k: v for k, v in mis.items() if v.get("is_tropical")}
        _m.add_vdm(mis, bag["vdm"])
        _m.add_sondes(mis, bag["sonde"])
        for mm in mis.values():
            mm["basin"] = b
            all_missions.append(mm)
    # live EP blocks: rebuild EP too (cheap) - already covered since EP bag
    # decodes EP archive; live blocks were appended to AL pass. To keep basin
    # correct, re-tag live-only missions by their HDOB product is overkill for
    # V1; live blocks also appear in the archive within ~40 min.

    storms = _group_missions(all_missions, year)

    # ---- write per-storm + per-mission, build manifest ----
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest_storms = []
    latest_mission = None
    for slug, st in sorted(storms.items()):
        ms = sorted(st["missions"], key=lambda m: m.get("valid_end") or "")
        index_missions = []
        for mm in ms:
            mid_slug = _slugify(mm["mission_id"])
            full = {k: mm[k] for k in (
                "mission_id", "aircraft", "flight", "storm_name",
                "valid_start", "valid_end", "n_obs", "peak_sfmr_kt",
                "peak_fl_wind_kt", "min_p_sfc_hpa", "track", "vdm_centers",
                "sondes")}
            full.update({"slug": slug, "name": st["name"], "basin": st["basin"],
                         "updated_utc": stamp})
            _write(out_dir, f"{slug}/{mid_slug}.json", full)
            index_missions.append({
                "mission_id": mm["mission_id"], "file": f"{mid_slug}.json",
                "aircraft": mm["aircraft"], "flight": mm["flight"],
                "valid_start": mm["valid_start"], "valid_end": mm["valid_end"],
                "n_obs": mm["n_obs"], "peak_sfmr_kt": mm["peak_sfmr_kt"],
                "peak_fl_wind_kt": mm["peak_fl_wind_kt"],
                "min_p_sfc_hpa": mm["min_p_sfc_hpa"]})
            if mm["valid_end"] and (latest_mission is None
                                    or mm["valid_end"] > latest_mission[0]):
                latest_mission = (mm["valid_end"], slug, full)
        per_storm = {"slug": slug, "name": st["name"], "basin": st["basin"],
                     "year": st["year"], "atcf": st["atcf"],
                     "is_invest": st["is_invest"], "updated_utc": stamp,
                     "missions": index_missions}
        _write(out_dir, f"{slug}/recon.json", per_storm)
        last_end = index_missions[-1]["valid_end"] if index_missions else None
        manifest_storms.append({
            "slug": slug, "name": st["name"], "basin": st["basin"],
            "year": st["year"], "atcf": st["atcf"],
            "is_invest": st["is_invest"], "mission_count": len(index_missions),
            "latest_mission_id": (index_missions[-1]["mission_id"]
                                  if index_missions else None),
            "last_ob_utc": last_end,
            "peak_sfmr_kt": max((m["peak_sfmr_kt"] or 0
                                 for m in index_missions), default=0) or None,
            "min_p_sfc_hpa": min((m["min_p_sfc_hpa"] for m in index_missions
                                  if m["min_p_sfc_hpa"]), default=None)})

    # ---- current spotlight: incremental OWNS it; a backfill PRESERVES the
    # live one (its "latest" mission would be historical). ----
    if is_backfill:
        cur_slug = (prior or {}).get("current_slug")
        cur_active = (prior or {}).get("has_active_recon", False)
        cur_tcpod = (prior or {}).get("tcpod_number")
    else:
        current = {"generated_utc": stamp, "has_active": False,
                   "mission": None, "storm_slug": None,
                   "tcpod_number": tcpod.get("tcpod_number")}
        if latest_mission:
            end, slug, full = latest_mission
            active = (now - _m._iso(end)).total_seconds() < 24 * 3600
            current.update({"has_active": active, "mission": full,
                            "storm_slug": slug})
        _write(out_dir, "current.json", current)
        cur_slug, cur_active = current["storm_slug"], current["has_active"]
        cur_tcpod = tcpod.get("tcpod_number")

    # ---- merge this run's storms into the prior manifest (the GROWING UNION,
    # so backfilled storms survive later incremental runs + vice versa) ----
    by_slug = {s["slug"]: s for s in (prior or {}).get("storms", [])}
    for s in manifest_storms:
        by_slug[s["slug"]] = s                   # upsert; this run's data wins
    union = sorted(by_slug.values(),
                   key=lambda s: s.get("last_ob_utc") or "", reverse=True)

    manifest = {
        "schema_version": SCHEMA_VERSION, "generated_utc": stamp,
        "source": "NHC recon (HDOB/VDM/dropsonde) + CARCAH TCPOD",
        "year": now.year, "storms": union,
        "current_slug": cur_slug, "has_active_recon": cur_active,
        "tcpod_number": cur_tcpod,
        "disclosure": ("SFMR surface winds are unreliable in heavy rain and "
                       "at very high wind speeds; all obs are point-in-time "
                       "aircraft measurements."),
    }
    _write(out_dir, "manifest.json", manifest)

    summary = {"mode": "backfill" if is_backfill else "incremental",
               "year": year, "month": backfill_month,
               "storms_this_run": len(storms), "storms_total": len(union),
               "missions_this_run": len(all_missions), "current": cur_slug}
    log(f"recon: {summary}")
    return summary
