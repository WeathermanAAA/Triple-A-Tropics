#!/usr/bin/env python3
"""Atomic, monotonic reconcile for the shared Ensemble Cyclone Centers manifest.

Every ensemble model (ecens, ecaie, gefs, ...) publishes to ONE shared
``models/enscenters/manifest.json`` from its OWN per-model workflow. Those
workflows race: each builds a manifest from a best-effort read of the prior
(over the public CDN, which can transiently 403 a PRESENT object) and then has
to fold its update into whatever the SIBLINGS published in the meantime. Two
failure modes clobbered the live manifest in practice:

  (A) REGRESSION of a model's OWN latest. If the builder's CDN read came back
      empty (a persistent Cloudflare 403, indistinguishable there from a genuine
      first run), the currency core saw an empty watermark and rebuilt the
      OLDEST complete cycles - so the model's manifest entry moved BACKWARD
      (e.g. ECMWF ENS regressed from Jun 13 to Jun 12). The old sibling-preserve
      guard did not protect a model's own latest, only other models.

  (B) DROPPED siblings. The earlier guard re-read live R2 but the workflow fell
      back to ``{}`` on ANY read hiccup (``get-object ... || echo '{}'``), which
      silently disabled the preserve and let a thin manifest clobber the others.

This guard closes both. It reconciles the freshly-built manifest against the
AUTHORITATIVE live manifest (read by the workflow directly from R2 via the
authenticated S3 API - reliable, NOT the flaky public CDN) with three rules that
make a clobber IMPOSSIBLE:

  * MERGE BY MODEL: for every model present in EITHER side, the result keeps the
    UNION of its cycles (newest ``retain`` kept), so a model's latest can only
    move FORWARD and a sibling is always carried through.
  * NEVER DROP: every model that is live with >=1 cycle survives.
  * NEVER REGRESS: every model's resulting ``latest`` is >= its live ``latest``.

The last two are also asserted after the merge as a hard refuse-to-write gate:
if the reconciled manifest would somehow drop a model or regress a latest (a
logic bug), the guard writes NOTHING and exits non-zero - the workflow then
leaves the prior manifest live and the next cron self-heals. Worst case is a
skipped publish, never a clobber.

``live_status`` (argv[3]) lets the workflow tell the guard whether the live read
is TRUSTWORTHY:
  ok      -> live_manifest is the real live JSON; reconcile against it.
  absent  -> live object genuinely does not exist (first run); nothing to
             preserve, the new manifest stands (still validated to be non-empty).
  failed  -> the workflow could not read live R2; ABORT (exit non-zero) so we
             never publish a manifest that might clobber siblings. (The workflow
             normally skips the publish itself before reaching here; this is a
             belt-and-suspenders backstop.)

stdlib only (the GEFS workflow installs no JSON/cloud deps).

argv: <new_manifest_path> <live_manifest_path> [live_status=ok] [retain=8]
"""
import datetime as dt
import json
import sys

# Canonical selector order (matches enscenters.registry order). Unknown slugs are
# appended after, so a future model still publishes even before this list is bumped.
ORDER = ["ecens", "ecaie", "gefs"]
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


def reconcile(new: dict, live: dict, live_status: str = "ok",
              retain: int = DEFAULT_RETAIN):
    """Pure core. Merge the freshly-built ``new`` manifest with the authoritative
    ``live`` one, per model, monotonically. Returns ``(manifest, ok, reason)``:

      * ``ok=True``  -> ``manifest`` is safe to publish.
      * ``ok=False`` -> do NOT write; ``reason`` says why (read failed, or a
        refuse-to-write invariant tripped). ``manifest`` is None.

    Invariants on a successful return, vs ``live``: every live model with cycles
    is present, and no model's ``latest`` moved backward.
    """
    if live_status == "failed":
        return None, False, "live R2 read failed; refusing to publish (avoid clobber)"

    new_models = _models(new)
    if not new_models:
        # The builder only writes a manifest when it published >=1 cycle, so an
        # empty new manifest is itself a bug; never publish it over live data.
        return None, False, "new manifest has no models; refusing to publish"

    live_models = _models(live) if live_status == "ok" else {}

    # --- merge by model: union cycles, newest `retain`, monotone latest ---
    merged = {}
    for slug in set(new_models) | set(live_models):
        n = new_models.get(slug, {})
        l = live_models.get(slug, {})
        cycles = sorted(set(n.get("cycles", [])) | set(l.get("cycles", [])), reverse=True)
        kept = cycles[:retain]
        if not kept:
            continue                      # a model with no cycles is omitted
        versions = dict(l.get("cycle_versions") or {})
        versions.update(n.get("cycle_versions") or {})   # this run's versions win
        entry = dict(n) if n else dict(l)                # prefer this run's label/meta
        entry["slug"] = slug
        entry["label"] = n.get("label") or l.get("label") or slug
        entry["cycles"] = kept
        entry["latest"] = kept[0]
        entry["cycle_versions"] = {c: versions[c] for c in kept if c in versions}
        merged[slug] = entry

    # --- refuse-to-write gate: never drop a live model, never regress a latest ---
    for slug, l in live_models.items():
        if not l.get("cycles"):
            continue
        if slug not in merged:
            return None, False, f"refusing to write: would DROP live model {slug!r}"
        live_latest = _latest(l)
        res_latest = merged[slug].get("latest")
        if live_latest and (res_latest is None or res_latest < live_latest):
            return None, False, (f"refusing to write: would REGRESS {slug!r} latest "
                                 f"{live_latest} -> {res_latest}")

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
    new_path = argv[1]
    live_path = argv[2]
    live_status = argv[3] if len(argv) > 3 else "ok"
    retain = int(argv[4]) if len(argv) > 4 else DEFAULT_RETAIN

    new = _load(new_path)
    live = _load(live_path)

    live_before = sorted(_models(live).keys())
    manifest, ok, reason = reconcile(new, live, live_status, retain)
    if not ok:
        sys.stderr.write(f"[guard] ABORT: {reason}\n")
        return 2

    with open(new_path, "w") as f:
        json.dump(manifest, f, separators=(",", ":"))

    new_slugs = set(_models(new).keys())
    after = [(m["slug"], m.get("latest")) for m in manifest["models"]]
    preserved = [s for s in live_before if s not in new_slugs]  # siblings carried through
    print(f"[guard] reconciled (live_status={live_status}); "
          f"live models={live_before or '[]'} -> published {after}; "
          f"preserved siblings={preserved or '[]'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
