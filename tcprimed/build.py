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

from . import SCHEMA_VERSION, SOURCE, DISCLOSURE, IMAGER_SENSORS
from . import fetch as fx
from . import render as rnd


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(when: dt.datetime) -> str:
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(out_dir: str, rel: str, obj) -> None:
    path = os.path.join(out_dir, rel)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"), allow_nan=False)


def _fetch_prior_manifest(url: Optional[str]) -> tuple[dict, bool]:
    """Return (manifest, ok). ok is False ONLY when a fetch was attempted and
    failed (transient CDN error). A missing url is not a failure (ok=True) — it
    means no prior was expected (local/backfill run). The flag lets the caller
    refuse to overwrite a populated live manifest with an empty one when it
    couldn't read the prior to merge onto."""
    if not url:
        return {}, True
    try:
        with urllib.request.urlopen(url, timeout=20) as r:  # noqa: S310
            return json.loads(r.read().decode("utf-8")), True
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
        with urllib.request.urlopen(url, timeout=20) as r:  # noqa: S310
            return json.loads(r.read().decode("utf-8"))
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

    # Build the manifest (growing union, newest-first by latest_overpass_utc).
    storms = list(union.values())
    storms.sort(key=lambda s: s.get("latest_overpass_utc") or "",
                reverse=True)
    default_slug = storms[0]["slug"] if storms else None
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": _now_iso(),
        "source": SOURCE,
        "disclosure": DISCLOSURE,
        "storms": storms,
        "default_slug": default_slug,
    }
    # Never clobber a populated live manifest with an empty one: if the union is
    # empty AND we couldn't read the prior (transient CDN error / first bootstrap
    # blip), skip the write so the workflow's "no manifest -> last-known-good
    # stays live" guard engages. A genuinely empty union with a reachable prior
    # (true off-season / fresh local build) writes normally.
    if not storms and not prior_ok:
        print("tcprimed: empty union AND prior manifest unavailable - NOT "
              "writing manifest (workflow keeps last-known-good live)",
              file=sys.stderr)
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
                products = rnd.render_overpass(meta, storm_dir, oid)
            except Exception as e:  # noqa: BLE001
                print(f"tcprimed: render {oid} ({atcf}) failed: "
                      f"{type(e).__name__}: {e}", file=sys.stderr)
                continue
            # products holds whichever of {89pct, 37color} rendered (a data gap
            # in one channel publishes the other rather than dropping the pass).
            prod_paths = {k: f"{slug}/{v}" for k, v in products.items()}
            existing[oid] = {
                "id": oid,
                "sensor": meta["sensor"],
                "platform": meta["platform"],
                "valid_utc": _iso(meta["valid"]),
                "intensity_kt": int(meta["intensity_kt"]),
                "dev_level": meta["dev_level"],
                "products": prod_paths,
            }
            print(f"tcprimed: rendered {atcf} {oid} ({tier}) "
                  f"[{'+'.join(sorted(products))}]")

    overpasses = sorted(existing.values(), key=lambda o: o.get("valid_utc", ""))
    if not overpasses:
        return

    # Derive peak intensity + sensor list from the FINAL merged overpass set, not
    # this run's (possibly --max-overpasses-sliced) `ops`, so carried-forward
    # passes outside the slice still count toward the manifest summary.
    peak_kt = max((int(o.get("intensity_kt") or 0) for o in overpasses),
                  default=0)
    sensors = sorted({o["sensor"] for o in overpasses if o.get("sensor")})

    name = rnd.storm_short_name(atcf)
    latest = overpasses[-1]["valid_utc"]
    per_storm = {
        "slug": slug, "name": name, "atcf": atcf, "basin": basin,
        "year": int(year), "updated_utc": _now_iso(),
        "overpasses": overpasses,
    }
    _write(out_dir, f"{slug}/overpasses.json", per_storm)

    union[slug] = {
        "slug": slug, "name": name, "atcf": atcf, "basin": basin,
        "year": int(year),
        "overpass_count": len(overpasses),
        "sensors": sensors,
        "latest_overpass_utc": latest,
        "peak_intensity_kt": int(peak_kt),
    }
