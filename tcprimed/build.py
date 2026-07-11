"""tcprimed.build - the fetch -> render -> write pipeline (run by CI cron).

Mirrors reconobs.build: read the prior manifest from the live CDN (best-effort),
render only NEW overpasses, upsert this run's storms by slug into a growing-union
manifest sorted newest-first, and write everything through a json.dump that uses
compact separators + allow_nan=False.

Output tree (synced to s3://triple-a-tropics-media/microwave/):
  manifest.json                 storm index + default_slug
  {slug}/overpasses.json        per-storm overpass index
  {slug}/{id}_89pct.png         89 GHz PCT
  {slug}/{id}_37color.png       37 GHz color
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
import urllib.request
from typing import Optional

from . import SCHEMA_VERSION, SOURCE, SOURCE_LIVE, DISCLOSURE, IMAGER_SENSORS
from . import fetch as fx
from . import render as rnd
from . import pps
from . import storms as st
from . import mwi


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(when: dt.datetime) -> str:
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(out_dir: str, rel: str, obj) -> None:
    path = os.path.join(out_dir, rel)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"), allow_nan=False)


# Cloudflare (in front of the R2 CDN) 403s the default "Python-urllib/x.y"
# User-Agent from datacenter IPs, so a bare urlopen of the public manifest fails
# with HTTPError 403 on GitHub Actions even though the file is reachable. A
# browser-like UA passes. This is load-bearing: without it the cross-tier union
# never merges (each tier reads an empty prior and clobbers the other's storms),
# so the live (current-storm) and archive tiers can't coexist in one manifest.
_HTTP_UA = "Mozilla/5.0 (compatible; TripleATropics-tcprimed/1.0)"


def _http_get_json(url: str, timeout: int = 20):
    """GET + parse JSON with a non-bot User-Agent (see _HTTP_UA). Raises on any
    HTTP/network/parse error - callers decide how to degrade."""
    req = urllib.request.Request(url, headers={"User-Agent": _HTTP_UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


def _fetch_prior_manifest(url: Optional[str]) -> tuple[dict, bool]:
    """Return (manifest, ok). ok is False ONLY when a fetch was attempted and
    failed (transient CDN error). A missing url is not a failure (ok=True) — it
    means no prior was expected (local/backfill run). The flag lets the caller
    refuse to overwrite a populated live manifest with an empty one when it
    couldn't read the prior to merge onto."""
    if not url:
        return {}, True
    try:
        return _http_get_json(url), True
    except Exception as e:  # noqa: BLE001
        print(f"tcprimed: prior manifest unavailable ({type(e).__name__}); "
              f"starting fresh union", file=sys.stderr)
        return {}, False


def _fetch_prior_overpasses(base_url: Optional[str], slug: str) -> dict:
    """Best-effort prior {slug}/overpasses.json (so we can skip re-rendering)."""
    if not base_url:
        return {}
    url = base_url.rstrip("/") + f"/{slug}/overpasses.json"
    try:
        return _http_get_json(url)
    except Exception:  # noqa: BLE001
        return {}


def _png_exists_locally(out_dir: str, rel: str) -> bool:
    return os.path.exists(os.path.join(out_dir, rel))


def build(out_dir: str, *, tiers=("final", "preliminary"),
          year: Optional[int] = None, years=None,
          basins=("AL", "EP", "WP", "IO", "SH"),
          storm: Optional[str] = None, max_overpasses: Optional[int] = None,
          prior_manifest_url: Optional[str] = None,
          cdn_base: Optional[str] = None, force: bool = False) -> dict:
    """Render observed passive-MW overpasses and write the build tree.

    tiers: process in order, preferring `final` when a storm exists in both
    (a slug seen under final is not re-rendered from preliminary).
    year/years: a single calendar year (default current) or an iterable for
    backfill. storm: restrict to one ATCFID. max_overpasses: per-storm cap.
    """
    os.makedirs(out_dir, exist_ok=True)
    client = fx._client()

    if years is None:
        years = [year or dt.date.today().year]
    years = list(years)
    basins = tuple(b.upper() for b in basins)

    prior, prior_ok = _fetch_prior_manifest(prior_manifest_url)
    prior_storms = {s["slug"]: s for s in prior.get("storms", [])
                    if isinstance(s, dict) and s.get("slug")}

    # union[slug] = manifest storm entry; per_storm[slug] = overpasses.json dict
    union: dict[str, dict] = dict(prior_storms)
    rendered_any = False

    storm_filter = storm.upper() if storm else None

    # Walk (tier, year, basin) -> storms -> overpasses. A slug first seen under
    # `final` is not reprocessed from a later (preliminary) tier.
    seen_final: set[str] = set()
    for tier in tiers:
        for yr in years:
            for basin in basins:
                try:
                    atcfs = fx.list_storms(tier, yr, basin, client=client)
                except Exception as e:  # noqa: BLE001
                    print(f"tcprimed: list {tier}/{yr}/{basin} failed: "
                          f"{type(e).__name__}: {e}", file=sys.stderr)
                    continue
                for atcf in atcfs:
                    if storm_filter and atcf != storm_filter:
                        continue
                    slug = atcf.lower()
                    if tier != "final" and slug in seen_final:
                        continue
                    nn = fx.atcf_parts(atcf)[1]
                    try:
                        ops = fx.list_overpasses(tier, yr, basin, nn,
                                                 client=client)
                    except Exception as e:  # noqa: BLE001
                        print(f"tcprimed: list overpasses {atcf} failed: "
                              f"{type(e).__name__}: {e}", file=sys.stderr)
                        continue
                    if not ops:
                        continue
                    if tier == "final":
                        seen_final.add(slug)
                    _process_storm(out_dir, slug, atcf, basin, yr, tier, ops,
                                   union, prior_manifest_base=cdn_base,
                                   max_overpasses=max_overpasses,
                                   client=client, force=force)
                    rendered_any = True

    return _write_manifest(out_dir, union, prior_ok, rendered_any)


def _write_manifest(out_dir, union, prior_ok, rendered_any, *, live=False) -> dict:
    """Build the growing-union manifest (newest-first by latest_overpass_utc) and
    write it -- UNLESS doing so would clobber a populated live manifest we could
    not read to merge onto. Two skip cases (both leave the workflow's "no manifest
    -> last-known-good stays live" guard engaged):
      * union empty AND prior unreadable (any tier), or
      * the LIVE tier when the prior was unreadable: its union holds only the
        current active storms, so writing it would drop the entire archive (the
        live tier never re-enumerates the archive - it relies on the prior). The
        archive tier is exempt because it re-enumerates every season storm, so a
        fresh union there IS the full set."""
    storms = list(union.values())
    storms.sort(key=lambda s: s.get("latest_overpass_utc") or "", reverse=True)
    default_slug = storms[0]["slug"] if storms else None
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": _now_iso(),
        "source": SOURCE,
        "disclosure": DISCLOSURE,
        "storms": storms,
        "default_slug": default_slug,
        # ADDITIVE: static legend descriptors for the map-mounted tiles
        # (cyclolab_map.js). Ignored by the existing /satellite/ viewer.
        "legends": rnd.mw_legends(),
    }
    # ADDITIVE: the MW-imager intensity model card (version + validated error
    # tables) so the consensus client (satellite/explorer/satcon.js) weights
    # members from the SAME numbers the fit published - no JS-side copy to
    # drift. Absent until a model JSON is committed. Ignored by the viewer.
    model = mwi.load_model()
    if model:
        manifest["intensity_model"] = {
            "version": model.get("version"),
            "disclosure": model.get("disclosure"),
            "error_overall": model.get("error_overall"),
            "error_by_bin": model.get("error_by_bin"),
            "error_by_sensor": model.get("error_by_sensor"),
        }
    if not storms and not prior_ok:
        print("tcprimed: empty union AND prior manifest unavailable - NOT "
              "writing manifest (workflow keeps last-known-good live)",
              file=sys.stderr)
        return manifest
    if storms and live and not prior_ok:
        print("tcprimed live: prior manifest unreadable - NOT writing a "
              "live-only manifest (would drop the archive); keeping "
              "last-known-good live", file=sys.stderr)
        return manifest
    _write(out_dir, "manifest.json", manifest)
    print(f"tcprimed: wrote manifest with {len(storms)} storm(s), "
          f"default={default_slug}, rendered_any={rendered_any}")
    return manifest


def _process_storm(out_dir, slug, atcf, basin, year, tier, ops, union, *,
                   prior_manifest_base, max_overpasses, client, force=False):
    """Render new overpasses for one storm, merge into its overpasses.json, and
    upsert the manifest entry."""
    # Prior overpasses (local build dir first, then the live CDN) so a re-run or
    # an incremental run does not re-render already-published PNGs.
    prior_local_path = os.path.join(out_dir, slug, "overpasses.json")
    prior_ops_doc = {}
    if os.path.exists(prior_local_path):
        try:
            with open(prior_local_path, "r", encoding="utf-8") as f:
                prior_ops_doc = json.load(f)
        except Exception:  # noqa: BLE001
            prior_ops_doc = {}
    if not prior_ops_doc:
        prior_ops_doc = _fetch_prior_overpasses(prior_manifest_base, slug)

    existing = {o["id"]: o for o in prior_ops_doc.get("overpasses", [])
                if isinstance(o, dict) and o.get("id")}

    if max_overpasses:
        ops = ops[-int(max_overpasses):]

    storm_dir = os.path.join(out_dir, slug)

    with tempfile.TemporaryDirectory(prefix=f"tcprimed_{slug}_") as tmp:
        for op in ops:
            oid = op["id"]
            prev = existing.get(oid)
            # Skip render if this id is already published (unless --force, used to
            # refresh R2 after a render-code change). A pass with a genuine
            # one-channel data gap publishes only the available product, and that
            # gap won't fill on a re-run, so any non-empty products record counts
            # as done (re-render only when nothing was published).
            if not force and prev and prev.get("products"):
                continue
            try:
                local = fx.download(op["key"], tmp, client=client)
                meta = rnd.read_overpass(local)
            except Exception as e:  # noqa: BLE001
                print(f"tcprimed: read {oid} ({atcf}) failed: "
                      f"{type(e).__name__}: {e}", file=sys.stderr)
                continue
            try:
                res = rnd.render_overpass(meta, storm_dir, oid)
            except Exception as e:  # noqa: BLE001
                print(f"tcprimed: render {oid} ({atcf}) failed: "
                      f"{type(e).__name__}: {e}", file=sys.stderr)
                continue
            products, tiles = res["products"], res["tiles"]
            tiles_raw = res.get("tiles_raw", {})
            # products holds whichever of {89pct, 37color} rendered (a data gap
            # in one channel publishes the other rather than dropping the pass).
            prod_paths = {k: f"{slug}/{v}" for k, v in products.items()}
            # ADDITIVE: the EXPERIMENTAL MW-imager objective intensity estimate
            # (tcprimed.mwi; committed model JSON). None until a model ships /
            # when the swath fails the coverage-land gate hard. Never raises.
            mwi_est = mwi.intensity_record(meta, source="archive")
            existing[oid] = {
                "id": oid,
                "sensor": meta["sensor"],
                "platform": meta["platform"],
                "valid_utc": _iso(meta["valid"]),
                "intensity_kt": int(meta["intensity_kt"]),
                "dev_level": meta["dev_level"],
                **({"intensity": mwi_est} if mwi_est else {}),
                "products": prod_paths,
                # ADDITIVE map-ready fields (ignored by the existing viewer):
                "tiles": {k: f"{slug}/{v}" for k, v in tiles.items()},
                # ADDITIVE raw (native-footprint) tiles for the viewer's Raw view:
                "tiles_raw": {k: f"{slug}/{v}" for k, v in tiles_raw.items()},
                "bounds_wgs84": rnd.overpass_bounds_wgs84(meta),
            }
            print(f"tcprimed: rendered {atcf} {oid} ({tier}) "
                  f"[{'+'.join(sorted(products))}]")

    _finalize_storm(out_dir, slug, atcf, basin, year, existing, union)


def _finalize_storm(out_dir, slug, atcf, basin, year, existing, union, *,
                    name=None):
    """Write {slug}/overpasses.json (sorted) + upsert the manifest union entry.
    Shared by the archive (_process_storm) and live (build_live) paths. ``name``
    overrides the display name (live has the real storm name; archive uses the
    ATCF short id). peak_intensity + sensors come from the FINAL merged set."""
    overpasses = sorted(existing.values(), key=lambda o: o.get("valid_utc", ""))
    if not overpasses:
        return
    peak_kt = max((int(o.get("intensity_kt") or 0) for o in overpasses),
                  default=0)
    sensors = sorted({o["sensor"] for o in overpasses if o.get("sensor")})
    disp_name = name or rnd.storm_short_name(atcf)
    latest = overpasses[-1]["valid_utc"]
    _write(out_dir, f"{slug}/overpasses.json", {
        "slug": slug, "name": disp_name, "atcf": atcf, "basin": basin,
        "year": int(year), "updated_utc": _now_iso(),
        "overpasses": overpasses,
    })
    union[slug] = {
        "slug": slug, "name": disp_name, "atcf": atcf, "basin": basin,
        "year": int(year),
        "overpass_count": len(overpasses),
        "sensors": sensors,
        "latest_overpass_utc": latest,
        "peak_intensity_kt": int(peak_kt),
    }


# ---------------------------------------------------------------------------
# LIVE (near-real-time) tier: PPS NRT GPM-constellation 1C over active storms
# ---------------------------------------------------------------------------
def build_live(out_dir, *, window_hours=6, prior_manifest_url=None,
               cdn_base=None, include_invests=True, max_granules=None,
               pad=8.0, force=False) -> dict:
    """Render NRT passive-MW overpasses for CURRENTLY-ACTIVE storms and merge them
    into the same `microwave/` manifest as the archive (so live storms sort to the
    top by recency and share a slug with a future archived version).

    Flow: read the live active-storm feed -> list recent NRT 1C granules -> for
    each granule, download once, and for every active storm it covers, crop the
    global swath to the storm box and render (reusing tcprimed.render). No PPS
    credential (env PPS_EMAIL / ~/.pps_email) -> the live tier is a graceful no-op
    (prior manifest kept). Never raises into the workflow on a per-granule error.
    """
    import datetime as dt

    prior, prior_ok = _fetch_prior_manifest(prior_manifest_url)
    union = {s["slug"]: s for s in prior.get("storms", [])
             if isinstance(s, dict) and s.get("slug")}

    email = pps.pps_credential()
    if not email:
        print("tcprimed live: no PPS credential (PPS_EMAIL / ~/.pps_email); "
              "live tier skipped", file=sys.stderr)
        return _write_manifest(out_dir, union, prior_ok, False, live=True)

    active = st.active_storms(include_invests=include_invests)
    print(f"tcprimed live: {len(active)} active system(s): "
          f"{[s['slug'] for s in active]}")
    if not active:
        return _write_manifest(out_dir, union, prior_ok, False, live=True)

    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=window_hours)
    try:
        granules = pps.recent_granule_urls(email, since)
    except Exception as e:  # noqa: BLE001
        print(f"tcprimed live: NRT listing failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        return _write_manifest(out_dir, union, prior_ok, False, live=True)
    if max_granules:
        granules = granules[-int(max_granules):]
    print(f"tcprimed live: {len(granules)} candidate NRT granule(s) since "
          f"{since:%Y-%m-%d %H:%MZ}")

    # Per-active-storm existing overpasses (local build dir, then live CDN) so a
    # re-run does not re-render already-published passes.
    existing_by_slug: dict[str, dict] = {}
    for s in active:
        slug = s["slug"]
        doc = {}
        local = os.path.join(out_dir, slug, "overpasses.json")
        if os.path.exists(local):
            try:
                with open(local, "r", encoding="utf-8") as f:
                    doc = json.load(f)
            except Exception:  # noqa: BLE001
                doc = {}
        if not doc:
            doc = _fetch_prior_overpasses(cdn_base, slug)
        existing_by_slug[slug] = {o["id"]: o for o in doc.get("overpasses", [])
                                  if isinstance(o, dict) and o.get("id")}

    touched: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="tcprimed_live_") as tmp:
        for g in granules:
            sensor, platform = g["sensor"], g["platform"]
            stamp = g["start"].strftime("%Y%m%d%H%M%S")
            oid = f"{sensor}_{platform}_{stamp}"
            # Cheap pre-check: any active storm still NEEDING this pass?
            need = [s for s in active if force or
                    not existing_by_slug[s["slug"]].get(oid, {}).get("products")]
            if not need:
                continue
            try:
                local = pps.download(g["url"], email, tmp)
                data = pps.read_1c(local, sensor, platform)
            except Exception as e:  # noqa: BLE001
                print(f"tcprimed live: {g['file']} failed: "
                      f"{type(e).__name__}: {e}", file=sys.stderr)
                continue
            for s in need:
                slug = s["slug"]
                cov89 = st.storm_covers(s, data["lat89"], data["lon89"],
                                        half_deg=pad)
                cov37 = st.storm_covers(s, data["lat37"], data["lon37"],
                                        half_deg=pad)
                if not (cov89 or cov37):
                    continue
                # Pin the valid time to the storm's along-track scan (the granule
                # mid-time can be ~45 min off for full-orbit AMSR2/SSMIS).
                tband = ("lat89", "lon89") if cov89 else ("lat37", "lon37")
                valid = pps.overpass_time(data[tband[0]], data[tband[1]],
                                          s["lat"], s["lon"],
                                          g["start"], g["end"])
                meta = {
                    "sensor": sensor, "platform": platform, "atcf": s["atcf"],
                    "basin": s["basin"], "year": s["year"], "valid": valid,
                    "intensity_kt": int(s.get("intensity_kt") or 0),
                    "min_p_hpa": s.get("mslp"),
                    "dev_level": s.get("category") or "",
                    "clat": s["lat"], "clon": s["lon"],
                    "source_label": SOURCE_LIVE,
                }
                c89 = pps.crop_swath(data["lat89"], data["lon89"],
                                     data["tb89v"], data["tb89h"],
                                     s["lat"], s["lon"], pad) if cov89 else None
                c37 = pps.crop_swath(data["lat37"], data["lon37"],
                                     data["tb37v"], data["tb37h"],
                                     s["lat"], s["lon"], pad) if cov37 else None
                if c89:
                    (meta["lat89"], meta["lon89"],
                     meta["tb89v"], meta["tb89h"]) = c89
                if c37:
                    (meta["lat37"], meta["lon37"],
                     meta["tb37v"], meta["tb37h"]) = c37
                try:
                    res = rnd.render_overpass(
                        meta, os.path.join(out_dir, slug), oid)
                except Exception as e:  # noqa: BLE001
                    print(f"tcprimed live: render {slug} {oid} failed: "
                          f"{type(e).__name__}: {e}", file=sys.stderr)
                    continue
                products, tiles = res["products"], res["tiles"]
                tiles_raw = res.get("tiles_raw", {})
                # ADDITIVE: EXPERIMENTAL MW-imager objective intensity (mwi).
                mwi_est = mwi.intensity_record(meta, source="live")
                existing_by_slug[slug][oid] = {
                    "id": oid, "sensor": sensor, "platform": platform,
                    "valid_utc": _iso(meta["valid"]),
                    "intensity_kt": int(meta["intensity_kt"]),
                    "dev_level": meta["dev_level"],
                    **({"intensity": mwi_est} if mwi_est else {}),
                    "products": {k: f"{slug}/{v}" for k, v in products.items()},
                    # ADDITIVE map-ready fields (ignored by the existing viewer):
                    "tiles": {k: f"{slug}/{v}" for k, v in tiles.items()},
                    # ADDITIVE raw (native-footprint) tiles for the Raw view:
                    "tiles_raw": {k: f"{slug}/{v}" for k, v in tiles_raw.items()},
                    "bounds_wgs84": rnd.overpass_bounds_wgs84(meta),
                    "source": "live",
                }
                touched.add(slug)
                print(f"tcprimed live: rendered {slug} {oid} "
                      f"[{'+'.join(sorted(products))}]")

    for slug in touched:
        s = next(x for x in active if x["slug"] == slug)
        _finalize_storm(out_dir, slug, s["atcf"], s["basin"], s["year"],
                        existing_by_slug[slug], union, name=s["name"])
    return _write_manifest(out_dir, union, prior_ok, bool(touched), live=True)
