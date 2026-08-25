#!/usr/bin/env python3
"""Publish an ascatobs build directory to R2 — the box-poller replacement for
the update-ascat.yml sync step, reproducing its contract EXACTLY:

  * no manifest.json in the build dir -> clean no-op (kill switch / no creds /
    failed build leaves last-known-good R2 live)
  * upload every file EXCEPT _pruned_ids.json with
    Content-Type: application/json, Cache-Control: public, max-age=300
    (single header set for all served files, like the workflow's one sync)
  * manifest.json is uploaded LAST so a mid-publish failure never points the
    index at pass files that did not land
  * then a TARGETED delete per id in _pruned_ids.json (never a blanket
    prune); individual delete failures are non-fatal — the id is already
    out of the manifest, a leftover object is just an orphan
  * the reap is tied to the INDEX, not to the kill switch (deletes are never
    guarded): it runs only on a tick whose manifest.json landed. The pruned
    ids are what the LIVE manifest still lists and the built one drops, so
    reaping them under a manifest that did not land (switch off) would leave
    the live index pointing at 404s — the mirror image of "manifest LAST".
    Deferred, not lost: the builder re-derives the list from the live
    manifest every tick, so the first landed manifest reaps them.

Usage: ascat_r2_publish.py <build_dir> [--prefix ascat]
Env:   R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
"""
from __future__ import annotations

import argparse
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

CACHE = "public, max-age=300"
CTYPE = "application/json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("build_dir")
    ap.add_argument("--prefix", default="ascat")
    args = ap.parse_args()
    root = Path(args.build_dir)
    if not (root / "manifest.json").exists():
        print("[ascat-publish] no manifest.json in build dir — nothing to "
              "publish (guarded no-op, last-known-good stays live)")
        return 0

    import boto3
    bucket = os.environ.get("R2_BUCKET", "triple-a-tropics-media")
    c = boto3.client(
        "s3", endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"])

    files = sorted(p for p in root.iterdir()
                   if p.is_file() and p.name != "_pruned_ids.json")
    # manifest LAST: the index must never precede its pass files
    files.sort(key=lambda p: p.name == "manifest.json")
    n = skipped = 0
    index_landed = False
    for p in files:
        key = f"{args.prefix}/{p.name}"
        if tat_killswitch is not None and not tat_killswitch.writes_allowed(key):
            skipped += 1                 # dropped (the switch logs it)
            continue
        c.put_object(Bucket=bucket, Key=key,
                     Body=p.read_bytes(), ContentType=CTYPE, CacheControl=CACHE)
        n += 1
        if p.name == "manifest.json":
            index_landed = True
    # Reap only under a manifest that landed (see the docstring): the ids
    # below are still listed by the LIVE manifest until the built one
    # replaces it. Not a switch check -- deletes are never guarded.
    reaped = deferred = 0
    pruned = root / "_pruned_ids.json"
    if pruned.exists():
        ids = json.loads(pruned.read_text())
        if index_landed:
            for pid in ids:
                try:
                    c.delete_object(Bucket=bucket, Key=f"{args.prefix}/{pid}.json")
                    reaped += 1
                except Exception as e:                      # noqa: BLE001
                    print(f"[ascat-publish] reap {pid} failed (orphan left): {e}")
        else:
            deferred = len(ids)
    print(f"[ascat-publish] uploaded {n} file(s), reaped {reaped} pruned "
          f"pass(es) -> {bucket}/{args.prefix}/"
          + (f" (kill switch dropped {skipped} put(s))" if skipped else "")
          + (f" (reap of {deferred} pruned pass(es) deferred: manifest.json "
             "did not land)" if deferred else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
