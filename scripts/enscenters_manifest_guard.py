#!/usr/bin/env python3
"""Sibling-preserve guard for the shared Ensemble Cyclone Centers manifest.

Every ensemble model (ecens, ecaie, gefs, ...) publishes to ONE shared
``models/enscenters/manifest.json`` from its OWN workflow. The builder reads the
prior manifest over the PUBLIC CDN to merge against; if that read transiently
fails (Cloudflare can briefly 403 a present object), the builder fresh-starts and
its locally-written manifest is missing every OTHER model - publishing it would
CLOBBER those siblings (this dropped ECMWF ENS, then AIFS-ENS, in practice).

This guard runs in the workflow AFTER the per-cycle JSON sync and BEFORE the
manifest swap. It re-reads the CURRENT live manifest directly from R2 via the
authenticated S3 API (reliable - NOT the flaky public CDN) and unions back any
model that has cycles live but is absent from the new manifest. It ONLY ADDS a
missing sibling verbatim (cycles + cycle_versions preserved for cache-busting);
it never removes or alters THIS run's own model entry. If the live read failed
entirely the caller passes an empty ``{}`` and the guard is a no-op (the existing
consistency gate / abort then decides).

stdlib only (the GEFS workflow installs no JSON/cloud deps).

argv: <new_manifest_path> <live_manifest_path>
"""
import json
import sys

# Canonical selector order (matches enscenters.registry order). Unknown slugs are
# appended after, so a future model still publishes even before this list is bumped.
ORDER = ["ecens", "ecaie", "gefs"]


def _load(path: str) -> dict:
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001 - absent / malformed -> treat as empty
        return {}


def _models(man: dict) -> dict:
    out = {}
    for m in man.get("models", []) or []:
        if isinstance(m, dict) and m.get("slug"):
            out[m["slug"]] = m
    return out


def main(argv) -> int:
    new_path, live_path = argv[1], argv[2]
    new = _load(new_path)
    live = _load(live_path)
    new_models = _models(new)
    live_models = _models(live)

    preserved = []
    for slug, entry in live_models.items():
        if slug in new_models:
            continue                       # this run (or a prior union) already has it
        if not entry.get("cycles"):
            continue                       # nothing to preserve
        new_models[slug] = entry           # union the sibling back, verbatim
        preserved.append(slug)

    ordered = [new_models[s] for s in ORDER if s in new_models]
    ordered += [m for s, m in new_models.items() if s not in ORDER]
    new["models"] = ordered

    # Stable default: the registry default (ecens) whenever present, so a transient
    # clobber + recovery never changes which model the viewer loads first; else keep
    # the current default if valid; else the first model.
    slugs = [m["slug"] for m in ordered]
    if "ecens" in slugs:
        new["default_model"] = "ecens"
    elif new.get("default_model") not in slugs:
        new["default_model"] = slugs[0] if slugs else None

    with open(new_path, "w") as f:
        json.dump(new, f, separators=(",", ":"))

    if preserved:
        print(f"[guard] preserved sibling model(s) from live R2 manifest: {preserved}")
    else:
        print("[guard] no sibling models needed preserving")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
