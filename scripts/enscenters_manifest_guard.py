#!/usr/bin/env python3
"""R2-truth reconcile for the shared Ensemble Cyclone Centers manifest.

Every ensemble model (ecens, ecaie, gefs, ...) publishes to ONE shared
``models/enscenters/manifest.json`` from its OWN per-model workflow. The manifest
is DERIVED FROM WHAT ACTUALLY EXISTS ON R2: the publishing workflow lists the
cycle objects under each ``models/enscenters/{slug}/`` prefix (authenticated S3,
the source of truth) and this reconcile sets each model's entry to the newest
``retain`` of those, with ``latest`` == the newest object present. The live
manifest contributes only cache-bust tokens (cycle_versions), labels, and
default_model. Consequences:

  * A model can NEVER stay frozen on an old cycle while newer cycles already sit
    on R2 (the prod bug: ecens/ecaie stuck at Jun-12 while Jun-13 JSONs were live
    on R2 but unreferenced) -- ANY model's run re-derives every model from R2.
  * ``latest`` is always the newest object on R2, so it can only move forward
    (prune removes the OLDEST beyond retain, never the newest).
  * A sibling is never dropped: if it has objects on R2 it is in the result.

This replaces the earlier prior-manifest-union guard, which trusted a (possibly
stale) prior entry and so kept a model frozen when its own workflow hadn't
re-published. The two historical failure modes it also fixes:

  (A) REGRESSION of a model's OWN latest. If the builder's CDN read came back
      empty (a persistent Cloudflare 403, indistinguishable there from a genuine
      first run), the currency core saw an empty watermark and rebuilt the
      OLDEST complete cycles - so the model's manifest entry moved BACKWARD
      (e.g. ECMWF ENS regressed from Jun 13 to Jun 12). The old sibling-preserve
      guard did not protect a model's own latest, only other models.

  (B) DROPPED siblings. The earlier guard re-read live R2 but the workflow fell
      back to ``{}`` on ANY read hiccup (``get-object ... || echo '{}'``), which
      silently disabled the preserve and let a thin manifest clobber the others.

  (A) REGRESSION of a model's own latest, when its build had an empty watermark.
  (B) DROPPED siblings, when a read hiccup left the prior manifest empty.

Deriving from the R2 listing makes BOTH impossible by construction: the truth is
the set of objects on R2, not any manifest read. A refuse-to-write backstop still
aborts (writes nothing, prior stays live) if the result would drop a model that
has objects on R2, or if the listing came back empty while the live manifest has
models (a suspected listing failure) -- worst case a skipped publish, never a
clobber. The workflow skips the publish entirely on a listing failure, so when
this guard is reached the listing succeeded.

stdlib only (the GEFS workflow installs no JSON/cloud deps).

argv: <new_manifest_path> <live_manifest_path> <r2_present_json> [retain=8]
  r2_present_json: {slug: [cycles...]} from `aws s3 ls` of each model prefix.
"""
import datetime as dt
import json
import sys

# Canonical selector order (matches enscenters.registry order). Unknown slugs are
# appended after, so a future model still publishes even before this list is bumped.
ORDER = ["ecens", "ecaie", "gefs", "fnv3", "genc"]
LABELS = {"ecens": "ECMWF ENS", "ecaie": "AIFS-ENS", "gefs": "GEFS",
          "fnv3": "Google FNV3 (50)", "genc": "Google GenCast"}
DEFAULT_MODEL = "ecens"           # mirrors enscenters.registry.DEFAULT_MODEL
SCHEMA_VERSION = 1
DEFAULT_RETAIN = 8                # mirrors enscenters.pipeline.DEFAULT_RETAIN


def _utcnow_iso() -> str:
    return (dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
            .isoformat().replace("+00:00", "Z"))


def _load(path: str) -> dict:
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001 - absent / malformed -> treat as empty
        return {}


def _models(man: dict) -> dict:
    """{slug: entry} from a manifest's ``models`` list, coercing a malformed
    ``cycles`` to a list and dropping entries without a slug."""
    out = {}
    for m in (man.get("models") or []):
        if isinstance(m, dict) and m.get("slug"):
            d = dict(m)
            d["cycles"] = [c for c in (d.get("cycles") or []) if isinstance(c, str)]
            d["cycle_versions"] = dict(d.get("cycle_versions") or {})
            out[d["slug"]] = d
    return out


def _latest(entry: dict):
    cyc = entry.get("cycles") or []
    return max(cyc) if cyc else None


def _present_map(r2_present, live_models, new_models):
    """The authoritative per-model cycle EXISTENCE map. When ``r2_present`` (the R2
    object listing, ``{slug: [cycles]}``) is given it IS the truth. When it is None
    (callers/tests without a listing) we fall back to the live manifest's own cycle
    lists, which reproduces the prior union-of-live-and-new behaviour."""
    if r2_present is not None:
        out = {}
        for slug, cyc in r2_present.items():
            out[slug] = [c for c in (cyc or []) if isinstance(c, str)]
        return out
    return {slug: list(l.get("cycles") or []) for slug, l in live_models.items()}


def reconcile(new: dict, live: dict, live_status: str = "ok",
              retain: int = DEFAULT_RETAIN, r2_present=None):
    """Pure core. Derive the published manifest from what ACTUALLY EXISTS ON R2
    (``r2_present``: ``{slug: [cycles]}`` from the authenticated object listing),
    folding in this run's freshly-built cycles. The live manifest contributes only
    cycle_versions (cache-bust tokens), labels, and default_model -- it is NOT the
    source of truth for a model's cycle list, so a model can NEVER stay frozen on an
    old cycle while newer cycles sit on R2 (the prod bug), and ``latest`` is always
    the newest cycle present on R2.

    Returns ``(manifest, ok, reason)``: ``ok=False`` -> do NOT write (``manifest``
    is None). By construction every model with objects on R2 is present and its
    ``latest`` is the newest such object, so the result can never drop a model that
    has R2 data nor regress a latest below R2 reality.
    """
    if live_status == "failed":
        return None, False, "live read failed; refusing to publish (avoid clobber)"

    new_models = _models(new)
    live_models = _models(live)
    present = _present_map(r2_present, live_models, new_models)

    # Suspected listing failure: R2 came back empty yet the live manifest has models
    # with cycles -> do NOT publish an empty/thinned manifest over live data.
    if r2_present is not None and not any(present.values()) \
            and any(l.get("cycles") for l in live_models.values()):
        return None, False, ("R2 listing returned no cycles but live manifest has "
                             "models; suspected listing failure, refusing to publish")

    # --- derive each model from R2 truth (+ this run's just-built cycles) ---
    merged = {}
    for slug in set(present) | set(new_models):
        cyc = set(present.get(slug, []))
        cyc |= set(new_models.get(slug, {}).get("cycles", []))   # listing may lag the sync
        kept = sorted(cyc, reverse=True)[:retain]
        if not kept:
            continue                      # a model with no cycles is omitted
        lv, nv = live_models.get(slug, {}), new_models.get(slug, {})
        versions = dict(lv.get("cycle_versions") or {})
        versions.update(nv.get("cycle_versions") or {})          # this run's versions win
        merged[slug] = {
            "slug": slug,
            "label": nv.get("label") or lv.get("label") or LABELS.get(slug) or slug,
            "cycles": kept,
            "latest": kept[0],
            "cycle_versions": {c: versions[c] for c in kept if c in versions},
        }

    if not merged:
        return None, False, "no model has any cycle on R2 or in this run; nothing to publish"

    # refuse-to-write backstop: a model that HAS objects on R2 must survive.
    for slug, cyc in present.items():
        if cyc and slug not in merged:
            return None, False, f"refusing to write: would DROP model {slug!r} that has cycles on R2"

    # order by registry, unknown slugs appended (a new model still publishes)
    ordered = [merged[s] for s in ORDER if s in merged]
    ordered += [m for s, m in merged.items() if s not in ORDER]

    slugs = [m["slug"] for m in ordered]
    if DEFAULT_MODEL in slugs:
        default_model = DEFAULT_MODEL          # stable: viewer always boots ecens when present
    else:
        cur = (new.get("default_model") or live.get("default_model"))
        default_model = cur if cur in slugs else (slugs[0] if slugs else DEFAULT_MODEL)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utcnow_iso(),
        "default_model": default_model,
        "models": ordered,
    }
    return manifest, True, "ok"


def main(argv) -> int:
    # argv: <new_manifest> <live_manifest> <r2_present_json> [retain]
    # r2_present_json is the authoritative R2 listing {slug: [cycles]} the shell
    # produced (it skips the publish entirely on a listing failure, so when we are
    # called the listing succeeded; the live manifest is best-effort for versions).
    new_path = argv[1]
    live_path = argv[2]
    r2_path = argv[3] if len(argv) > 3 else None
    retain = int(argv[4]) if len(argv) > 4 else DEFAULT_RETAIN

    new = _load(new_path)
    live = _load(live_path)
    r2_present = _load(r2_path) if r2_path else None
    if r2_present is not None and not isinstance(r2_present, dict):
        r2_present = None

    live_before = [(s, _latest(m)) for s, m in sorted(_models(live).items())]
    manifest, ok, reason = reconcile(new, live, "ok", retain, r2_present=r2_present)
    if not ok:
        sys.stderr.write(f"[guard] ABORT: {reason}\n")
        return 2

    with open(new_path, "w") as f:
        json.dump(manifest, f, separators=(",", ":"))

    after = [(m["slug"], m.get("latest")) for m in manifest["models"]]
    print(f"[guard] reconciled against R2 reality; live={live_before or '[]'} "
          f"-> published {after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
