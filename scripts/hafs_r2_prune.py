#!/usr/bin/env python3
"""hafs_r2_prune.py -- cycle-level retention for models/hafs/ on R2.

WHY (cost audit 2026-08-25, COST-AUDIT-2026-08.md section 4): every HAFS
cycle lands under its own ``models/hafs/{YYYYMMDDHH}/`` prefix and
update-hafs.yml only ``--delete``s inside the cycle it just rendered, so
cycles never aged out -- 919 GB after ~35 days, 30-55 GB/day, the only
compounding line on the bill. The viewer (models/hafs.js) reads
``manifest.json`` whose ``cycles[]`` holds at most the two newest cycles, so
anything older than a couple of days is unreachable from the site already.

WHAT IT DOES: one delimiter listing of ``models/hafs/`` gives the cycle
prefixes; a cycle is condemned iff its stamp is older than ``--days`` (by the
CYCLE time, not LastModified) AND it is not among the newest ``--keep-min``
cycles AND it is not referenced by the live manifest (belt and braces). Each
condemned cycle is walked (ListObjectsV2, ~10 pages) and, with ``--apply``,
deleted in DeleteObjects batches of 1000 (free on R2). Children of
``models/hafs/`` that are not a 10-digit cycle (manifest.json, legacy flat
keys) are reported and never touched.

DRY-RUN BY DEFAULT. ``--apply`` deletes. ``--report FILE`` writes the plan
(and, with --apply, the outcome) as JSON for the record.

Cost: 1 + ~10 LIST pages per condemned cycle; DeleteObjects is free.
Bucket lifecycle rules would be simpler but the R2 tokens in use cannot set
them (AccessDenied, verified 2026-07-08), so this is a walk, like s2_prune.

Env: R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY (AWS_* fallbacks),
R2_BUCKET (default triple-a-tropics-media).

  python scripts/hafs_r2_prune.py                    # dry run, 14 d
  python scripts/hafs_r2_prune.py --apply --days 14  # delete
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time

UTC = dt.timezone.utc
PREFIX = "models/hafs/"
MANIFEST_KEY = "models/hafs/manifest.json"
CYCLE_RE = re.compile(r"^\d{10}$")
DELETE_BATCH = 1000


def parse_cycle(seg: str):
    if not CYCLE_RE.match(seg):
        return None
    try:
        return dt.datetime.strptime(seg, "%Y%m%d%H").replace(tzinfo=UTC)
    except ValueError:
        return None


def _client():
    import boto3
    ep = os.environ.get("R2_ENDPOINT")
    if not ep:
        sys.exit("ERROR: R2_ENDPOINT is required")
    return boto3.client(
        "s3", endpoint_url=ep, region_name="auto",
        aws_access_key_id=os.environ.get("R2_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY"))


def _retry(fn, what, attempts=5):
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if i == attempts - 1:
                raise
            print(f"  retry {i + 1}/{attempts} {what}: {e}", file=sys.stderr)
            time.sleep(2 ** i)


def list_cycles(s3, bucket: str):
    """(cycle prefixes, other children) under models/hafs/ via one delimiter listing."""
    cycles, others, files = [], [], []
    token = None
    while True:
        kw = {"Bucket": bucket, "Prefix": PREFIX, "Delimiter": "/"}
        if token:
            kw["ContinuationToken"] = token
        r = _retry(lambda: s3.list_objects_v2(**kw), "list cycles")
        for p in r.get("CommonPrefixes", []):
            seg = p["Prefix"][len(PREFIX):].strip("/")
            (cycles if parse_cycle(seg) else others).append(seg)
        files.extend(o["Key"] for o in r.get("Contents", []))
        if not r.get("IsTruncated"):
            break
        token = r.get("NextContinuationToken")
    return sorted(cycles), sorted(others), files


def walk(s3, bucket: str, prefix: str):
    keys, size, pages = [], 0, 0
    token = None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kw["ContinuationToken"] = token
        r = _retry(lambda: s3.list_objects_v2(**kw), f"walk {prefix}")
        pages += 1
        for o in r.get("Contents", []):
            keys.append(o["Key"])
            size += o.get("Size", 0)
        if not r.get("IsTruncated"):
            break
        token = r.get("NextContinuationToken")
    return keys, size, pages


def manifest_cycles(s3, bucket: str) -> set:
    try:
        body = _retry(lambda: s3.get_object(Bucket=bucket, Key=MANIFEST_KEY)["Body"].read(), "get manifest")
        m = json.loads(body)
    except Exception as e:  # noqa: BLE001
        print(f"  manifest unreadable ({e}); protecting nothing extra", file=sys.stderr)
        return set()
    out = set()
    if isinstance(m.get("cycle"), str):
        out.add(m["cycle"])
    for c in m.get("cycles") or []:
        if isinstance(c, dict) and isinstance(c.get("cycle"), str):
            out.add(c["cycle"])
    return out


def plan(cycles, *, days: int, keep_min: int, protected: set, now: dt.datetime):
    cutoff = now - dt.timedelta(days=days)
    newest = set(cycles[-keep_min:]) if keep_min > 0 else set()
    condemned, kept = [], []
    for c in cycles:
        t = parse_cycle(c)
        why = None
        if c in protected:
            why = "live manifest"
        elif c in newest:
            why = f"newest {keep_min}"
        elif t >= cutoff:
            why = "younger than TTL"
        (kept if why else condemned).append((c, why))
    return condemned, kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="models/hafs cycle retention (dry-run by default)")
    ap.add_argument("--apply", action="store_true", help="actually delete (default: report only)")
    ap.add_argument("--days", type=int, default=14, help="TTL in days by cycle time (default 14)")
    ap.add_argument("--keep-min", type=int, default=2, help="always keep the newest N cycles (default 2)")
    ap.add_argument("--bucket", default=os.environ.get("R2_BUCKET", "triple-a-tropics-media"))
    ap.add_argument("--report", help="write the plan/outcome JSON here")
    args = ap.parse_args(argv)
    if args.days < 1:
        sys.exit("REFUSED: --days must be >= 1")
    now = dt.datetime.now(UTC)
    s3 = _client()
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[hafs-prune] {mode} prefix={PREFIX} ttl={args.days}d keep-min={args.keep_min} bucket={args.bucket}")

    cycles, others, files = list_cycles(s3, args.bucket)
    protected = manifest_cycles(s3, args.bucket)
    print(f"[hafs-prune] {len(cycles)} cycle prefixes ({cycles[0] if cycles else '-'} .. {cycles[-1] if cycles else '-'}); "
          f"live manifest cycles: {sorted(protected) or '-'}; untouched non-cycle children: {others or '-'}; "
          f"top-level files: {len(files)}")
    condemned, kept = plan(cycles, days=args.days, keep_min=args.keep_min, protected=protected, now=now)

    rows = []
    tot_n = tot_b = tot_pages = 0
    for c, _ in condemned:
        keys, size, pages = walk(s3, args.bucket, f"{PREFIX}{c}/")
        age_d = (now - parse_cycle(c)).total_seconds() / 86400
        rows.append({"cycle": c, "age_days": round(age_d, 1), "objects": len(keys), "bytes": size})
        tot_n += len(keys); tot_b += size; tot_pages += pages
        print(f"  {c}  age {age_d:5.1f} d  {len(keys):7,} objects  {size / 1e9:7.2f} GB  -> {'DELETE' if args.apply else 'would delete'}")
        if args.apply and keys:
            for i in range(0, len(keys), DELETE_BATCH):
                batch = keys[i:i + DELETE_BATCH]
                r = _retry(lambda: s3.delete_objects(
                    Bucket=args.bucket, Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True}),
                    f"delete {c} batch {i // DELETE_BATCH}")
                errs = r.get("Errors") or []
                if errs:
                    print(f"  {c}: {len(errs)} delete error(s), first: {errs[0]}", file=sys.stderr)
    print(f"[hafs-prune] TOTAL {'deleted' if args.apply else 'would delete'}: {len(condemned)} cycles, "
          f"{tot_n:,} objects, {tot_b / 1e9:.1f} GB (listing cost: {tot_pages + 1} pages); "
          f"kept {len(kept)} cycles")
    if args.report:
        out = {"mode": mode, "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "ttl_days": args.days,
               "keep_min": args.keep_min, "bucket": args.bucket, "protected": sorted(protected),
               "non_cycle_children": others, "kept": [{"cycle": c, "why": w} for c, w in kept],
               "condemned": rows, "total_objects": tot_n, "total_bytes": tot_b, "list_pages": tot_pages + 1}
        with open(args.report, "w") as f:
            json.dump(out, f, indent=1)
        print(f"[hafs-prune] report -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
