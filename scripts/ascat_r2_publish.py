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

Usage: ascat_r2_publish.py <build_dir> [--prefix ascat]
Env:   R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

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
    n = 0
    for p in files:
        c.put_object(Bucket=bucket, Key=f"{args.prefix}/{p.name}",
                     Body=p.read_bytes(), ContentType=CTYPE, CacheControl=CACHE)
        n += 1
    reaped = 0
    pruned = root / "_pruned_ids.json"
    if pruned.exists():
        for pid in json.load(pruned.open()):
            try:
                c.delete_object(Bucket=bucket, Key=f"{args.prefix}/{pid}.json")
                reaped += 1
            except Exception as e:                          # noqa: BLE001
                print(f"[ascat-publish] reap {pid} failed (orphan left): {e}")
    print(f"[ascat-publish] uploaded {n} file(s), reaped {reaped} pruned "
          f"pass(es) -> {bucket}/{args.prefix}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
