#!/usr/bin/env python3
"""Publish a recon build directory to R2 — the box-poller replacement for the
update-recon.yml sync step, reproducing its contract and adding the poller's
publish gate:

  * no manifest.json in the build dir -> clean no-op (kill switch / failed
    build leaves last-known-good R2 live); RECON_ENABLED=0 is honored here
    too, and a build manifest older than --max-build-age is REFUSED — a
    crashed tick's leftover tree must never republish as if fresh
  * PUBLISH GATE (R2-resident watermark, no local state): a data fingerprint
    of the built manifest/current/tcpod is compared against the LIVE R2 copy;
    unchanged data skips the upload entirely unless the live copy is older
    than the heartbeat (so freshness stamps still advance every ~10 min).
    Volatile stamps (generated_utc/updated_utc/fetched_utc) are excluded, so
    an idle tick is a true no-op.
  * SHRINK GUARD: the manifest is a growing union — if the built storm list
    is smaller than the live one beyond this run's declared prunes, the build
    lost its prior-manifest merge (the build itself fails loudly on an
    unreadable prior, so this is the last-resort net for logic bugs); the
    union-independent spotlight files (current.json + tcpod.json) still
    publish, the index and storm trees keep last-known-good, exit 1.
  * upload ONLY files the built manifest owns (top-level jsons + the listed
    slugs' trees) EXCEPT _pruned_slugs.json — a stale unlisted slug dir in a
    reused build dir must never resurrect a reaped R2 tree — with
    Content-Type: application/json, Cache-Control: public, max-age=30
    (matching the workflow sync); current.json then manifest.json upload
    LAST so the index never points at storm files that did not land
  * then a TARGETED recursive delete per slug in _pruned_slugs.json (never a
    blanket prune); individual delete failures are non-fatal.
  * the reap is tied to the INDEX, not to the kill switch (deletes are never
    guarded): it runs only on a tick whose manifest.json landed. The pruned
    slugs are what the LIVE manifest still lists and the built one drops, so
    reaping their trees under a manifest that did not land (switch off) would
    leave the live index pointing at 404s — the mirror image of "index LAST".
    Deferred, not lost: the builder re-derives the list from the live union
    every tick, so the first landed manifest reaps them.

Usage: recon_r2_publish.py <build_dir> [--prefix recon] [--heartbeat 600]
Env:   R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

# The R2 write kill switch (tat_killswitch.py at the repo root, mirrored in
# tsr). `python scripts/x.py` puts scripts/ (not the repo root) on sys.path,
# so the root is appended here. Optional on purpose: a missing or broken
# module means "allowed" -- the switch can only ever STOP writes. Deletes
# (the targeted reaps below) are never guarded.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
try:
    if _REPO_ROOT not in sys.path:
        sys.path.append(_REPO_ROOT)
    import tat_killswitch
except Exception:  # noqa: BLE001
    tat_killswitch = None

CACHE = "public, max-age=30"
CTYPE = "application/json"


def _fingerprint(manifest: dict | None, current: dict | None,
                 tcpod: dict | None) -> str:
    """Stamp-free digest of the published data: changes iff the DATA changed.
    Mirrors what a new bulletin/fix/tasking actually moves: per-storm windows
    and counts, the current spotlight's obs extent, and the tasking text."""
    m = manifest or {}
    cur = current or {}
    mis = cur.get("mission") or {}
    basis = {
        "storms": sorted(
            (s.get("slug"), s.get("last_ob_utc"), s.get("mission_count"),
             s.get("latest_mission_id"), s.get("peak_sfmr_kt"),
             s.get("min_p_sfc_hpa"))
            for s in m.get("storms", [])),
        "current_slug": m.get("current_slug"),
        "has_active": m.get("has_active_recon"),
        "tcpod_number": m.get("tcpod_number"),
        "cur": (cur.get("storm_slug"), cur.get("has_active"),
                mis.get("mission_id"), mis.get("valid_end"),
                mis.get("n_obs"), len(mis.get("vdm_centers") or []),
                len(mis.get("sondes") or [])),
        "tcpod_raw": hashlib.sha1(
            ((tcpod or {}).get("raw") or "").encode()).hexdigest(),
    }
    return hashlib.sha1(
        json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()


def _get_json(client, bucket: str, key: str) -> dict | None:
    """Live R2 JSON, or None when absent. A transient read error raises —
    treating it as 'absent' would defeat the shrink guard."""
    try:
        r = client.get_object(Bucket=bucket, Key=key)
        return json.loads(r["Body"].read())
    except client.exceptions.NoSuchKey:
        return None


def _age_s(stamp: str | None) -> float:
    if not stamp:
        return float("inf")
    try:
        t = dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc)
        return (dt.datetime.now(dt.timezone.utc) - t).total_seconds()
    except ValueError:
        return float("inf")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("build_dir")
    ap.add_argument("--prefix", default="recon")
    ap.add_argument("--heartbeat", type=float, default=600.0,
                    help="republish-anyway age (s) for unchanged data")
    ap.add_argument("--max-build-age", type=float, default=900.0,
                    help="refuse a build manifest older than this (s) — a "
                         "crashed tick's leftovers must not republish")
    args = ap.parse_args()
    if (os.environ.get("RECON_ENABLED", "1") or "1").lower() in \
            ("0", "false", "no"):
        print("[recon-publish] disabled (kill switch) — no write")
        return 0
    root = Path(args.build_dir)
    if not (root / "manifest.json").exists():
        print("[recon-publish] no manifest.json in build dir — nothing to "
              "publish (guarded no-op, last-known-good stays live)")
        return 0

    import boto3
    bucket = os.environ.get("R2_BUCKET", "triple-a-tropics-media")
    c = boto3.client(
        "s3", endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"])

    def _load(name: str) -> dict | None:
        p = root / name
        return json.loads(p.read_text()) if p.exists() else None

    built_manifest = _load("manifest.json")
    built_current = _load("current.json")
    built_tcpod = _load("tcpod.json")
    pruned = _load("_pruned_slugs.json") or []

    # ---- build freshness: leftovers from a crashed tick must not republish
    build_age = _age_s(built_manifest.get("generated_utc"))
    if build_age > args.max_build_age:
        print(f"[recon-publish] ABORT: build manifest is {build_age:.0f}s old "
              f"(> {args.max_build_age:.0f}) — stale leftover, not publishing")
        return 1

    live_manifest = _get_json(c, bucket, f"{args.prefix}/manifest.json")
    live_current = _get_json(c, bucket, f"{args.prefix}/current.json")
    live_tcpod = _get_json(c, bucket, f"{args.prefix}/tcpod.json")

    skipped = 0

    def _put(p: Path) -> bool:
        """True if the object was uploaded; False when the kill switch
        dropped it (the switch logs the drop)."""
        nonlocal skipped
        key = f"{args.prefix}/{p.relative_to(root).as_posix()}"
        if tat_killswitch is not None and not tat_killswitch.writes_allowed(key):
            skipped += 1
            return False
        c.put_object(Bucket=bucket, Key=key, Body=p.read_bytes(),
                     ContentType=CTYPE, CacheControl=CACHE)
        return True

    # ---- shrink guard (growing-union invariant; last-resort net) ----
    if live_manifest is not None:
        built_n = len(built_manifest.get("storms", []))
        live_n = len(live_manifest.get("storms", []))
        if built_n < live_n - len(pruned):
            for name in ("tcpod.json", "current.json"):
                if (root / name).exists():
                    _put(root / name)             # spotlight stays fresh
            print(f"[recon-publish] ABORT: built manifest has {built_n} "
                  f"storms vs {live_n} live with only {len(pruned)} pruned — "
                  "prior-manifest merge lost; index + storm trees keep "
                  "last-known-good (spotlight files published)")
            return 1

    # ---- publish gate ----
    built_fp = _fingerprint(built_manifest, built_current, built_tcpod)
    live_fp = _fingerprint(live_manifest, live_current, live_tcpod)
    live_age = _age_s((live_manifest or {}).get("generated_utc"))
    if built_fp == live_fp and live_age < args.heartbeat:
        print(f"[recon-publish] unchanged (live {live_age:.0f}s old) — no-op")
        return 0

    # Upload ONLY what the built manifest owns: top-level jsons + the listed
    # slugs' trees. A stale dir left in a reused build dir (aged-out storm, or
    # a slug reaped on an earlier publish) must never re-upload — that would
    # resurrect pruned R2 trees and grow the publish all season.
    owned = {s.get("slug") for s in built_manifest.get("storms", [])}
    def _owned(p: Path) -> bool:
        rel = p.relative_to(root)
        return len(rel.parts) == 1 or rel.parts[0] in owned
    files = sorted(p for p in root.rglob("*")
                   if p.is_file() and p.name != "_pruned_slugs.json"
                   and _owned(p))
    # index files LAST (current.json, then manifest.json): the index must
    # never point at storm/mission files that did not land.
    _rank = {"current.json": 1, "manifest.json": 2}
    files.sort(key=lambda p: _rank.get(p.relative_to(root).as_posix(), 0))
    n = 0
    index_landed = False
    for p in files:
        if _put(p):
            n += 1
            if p.relative_to(root).as_posix() == "manifest.json":
                index_landed = True

    # ---- targeted reap of superseded slug trees (never a blanket delete) ----
    # Only under a manifest that landed (see the docstring): the slugs below
    # are still listed by the LIVE manifest until the built one replaces it.
    # Not a switch check -- deletes are never guarded.
    reaped = 0
    if index_landed:
        for slug in pruned:
            if not slug:
                continue
            try:
                token = None
                while True:
                    kw = {"Bucket": bucket, "Prefix": f"{args.prefix}/{slug}/"}
                    if token:
                        kw["ContinuationToken"] = token
                    page = c.list_objects_v2(**kw)
                    keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                    if keys:
                        c.delete_objects(Bucket=bucket, Delete={"Objects": keys})
                        reaped += len(keys)
                    if not page.get("IsTruncated"):
                        break
                    token = page.get("NextContinuationToken")
            except Exception as e:                      # noqa: BLE001
                print(f"[recon-publish] reap {slug} failed (orphans left): {e}")
        reap_note = (f"reaped {reaped} object(s) across {len(pruned)} "
                     "pruned slug(s)")
    else:
        reap_note = (f"reap of {len(pruned)} pruned slug(s) deferred "
                     "(manifest.json did not land)")
    print(f"[recon-publish] uploaded {n} file(s), {reap_note} "
          f"-> {bucket}/{args.prefix}/"
          + (f" (kill switch dropped {skipped} put(s))" if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
