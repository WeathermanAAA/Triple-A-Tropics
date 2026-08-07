#!/usr/bin/env python3
"""Publish the cron's freshly-rendered v2 manifest to R2 by MERGING it into the
existing manifest -- the never-regress, atomic half of the dual-writer fix.

``models/hafs/manifest.json`` has TWO writers: the box render worker (the
primary, continuous) and this cron (the ens-watchdog recovery lever, on
workflow_dispatch). Before this module the cron blindly overwrote the worker's
multi-cycle v2 manifest with its own single-cycle one, clobbering the building
cycle and flapping the shape. Now the cron MERGES:

  * upsert the cron's freshly-rendered (COMPLETE) cycle into the existing
    ``cycles[]`` -- authoritative for ITS cycle id, every OTHER cycle preserved;
  * NEVER-REGRESS: refuse to write if the merge would drop the newest cycle the
    existing manifest already had (the worker's in-progress/building cycle);
  * ATOMIC: conditional PUT with ``If-Match`` on the read ETag (``If-None-Match:
    *`` when the key is absent) -- if a concurrent worker write lands between the
    read and the PUT, the PUT 412s and we re-read + re-merge + retry, so the
    cron can never overwrite a newer worker manifest;
  * empty render (off-season / nothing rendered) -> NO-OP: leave the existing
    manifest untouched (the worker's quiescence gate owns off-season clearing),
    never clobber.

The merged shape is byte-schema-identical to the worker's ``compose_manifest_v2``
(``hafs_render.generate_hafs_plots._compose_manifest_v2``), so whichever writer
wins a given moment, the manifest is always a consistent v2.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

# newest-N published cycles. Matches the render worker's published count so the
# two writers never flap the cycle count.
DEFAULT_RETAIN = 2

# R2/S3 codes meaning "the precondition failed -> someone else wrote; re-merge".
_PRECONDITION_CODES = {"PreconditionFailed", "412", "ConditionalRequestConflict"}


def merge_cycles(existing: Optional[dict], fresh: dict,
                 *, retain: int = DEFAULT_RETAIN) -> Optional[dict]:
    """PURE never-regress merge of two v2 manifests.

    ``fresh`` is the cron's freshly-rendered v2 manifest (cycles = its one
    COMPLETE cycle). ``existing`` is what is on R2 now (v2, legacy, or None).

    Returns the merged v2 manifest to write, or **None** to signal "do not write"
    -- either the cron rendered nothing (off-season; never clobber the worker) or
    the merge would drop the newest existing cycle (a regression we refuse).
    """
    fresh_cycles = (fresh or {}).get("cycles") or []
    if not fresh_cycles:
        return None  # nothing rendered -> never clobber the live manifest

    existing_cycles = (existing or {}).get("cycles") or []
    by_id = {c["cycle"]: c for c in existing_cycles}
    for c in fresh_cycles:
        by_id[c["cycle"]] = c   # the cron's complete cycle wins for its own id
    merged = sorted(by_id.values(), key=lambda c: c["cycle"], reverse=True)[:retain]
    merged_ids = {c["cycle"] for c in merged}

    # NEVER-REGRESS: the newest cycle the existing manifest advertised (the
    # worker's building/newest cycle) must survive the merge. Dropping the OLDEST
    # to honor `retain` is fine; dropping the newest is a regression -> refuse.
    if existing_cycles:
        newest_existing = max(c["cycle"] for c in existing_cycles)
        if newest_existing not in merged_ids:
            return None

    # Inherit fresh's static v2 header fields (identical schema), swap in the
    # merged cycles + recompute the legacy single-cycle mirror.
    out = dict(fresh)
    out["cycles"] = merged
    legacy = next((e for e in merged if not e.get("in_progress")), None)
    if legacy is None:
        legacy = next((e for e in merged if e.get("storms")), None)
    out["cycle"] = legacy["cycle"] if legacy else None
    out["storms"] = legacy["storms"] if legacy else []
    out["path_template"] = (
        f"{legacy['cycle']}/{{model}}/{{storm}}/{{domain}}/{{product}}/f{{fxx}}.png"
        if legacy else "{model}/{storm}/{domain}/{product}/f{fxx}.png")
    return out


# ---------------------------------------------------------------------------
# R2 I/O (boto3 against the S3-compatible R2 endpoint; conditional writes)
# ---------------------------------------------------------------------------
def _r2_client():
    import boto3
    from botocore.config import Config as BotoConfig
    # R2-only on purpose — no AWS_* fallback. With ambient real-AWS creds (the
    # codespace carries the tat-sat-ingest key), a fallback signs R2 calls with
    # the real key and ships it to Cloudflare. The workflow env sets R2_*.
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        config=BotoConfig(retries={"max_attempts": 3, "mode": "standard"}))


def _read(client, bucket: str, key: str):
    """(manifest_dict, etag) or (None, None) if the key is absent."""
    from botocore.exceptions import ClientError
    try:
        r = client.get_object(Bucket=bucket, Key=key)
        return json.loads(r["Body"].read()), r.get("ETag")
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NoSuchBucket"):
            return None, None
        raise


def _put(client, bucket: str, key: str, obj: dict, *,
         if_match: Optional[str] = None, if_none_match: Optional[str] = None):
    kw = dict(Bucket=bucket, Key=key,
              Body=json.dumps(obj, separators=(",", ":")).encode(),
              ContentType="application/json", CacheControl="public, max-age=300")
    if if_match:
        kw["IfMatch"] = if_match
    if if_none_match:
        kw["IfNoneMatch"] = if_none_match
    client.put_object(**kw)


def publish(manifest_path: str, *, bucket: str, key: str,
            attempts: int = 5, client=None) -> int:
    """Read R2 -> merge -> conditional PUT, retrying on a precondition conflict.
    Returns a process exit code (0 ok / no-op, 1 on exhausted retries)."""
    from botocore.exceptions import ClientError
    with open(manifest_path, encoding="utf-8") as f:
        fresh = json.load(f)
    client = client or _r2_client()
    for i in range(attempts):
        existing, etag = _read(client, bucket, key)
        merged = merge_cycles(existing, fresh)
        if merged is None:
            print("publish: no-op (empty render or would-regress) -- "
                  "leaving the existing manifest untouched")
            return 0
        try:
            if etag:
                _put(client, bucket, key, merged, if_match=etag)
            else:
                _put(client, bucket, key, merged, if_none_match="*")
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code in _PRECONDITION_CODES:
                print(f"publish: conditional PUT lost the race (attempt {i + 1}/"
                      f"{attempts}) -- re-reading + re-merging")
                continue
            # The endpoint rejected the conditional write (not a precondition
            # failure -- e.g. If-Match unsupported on this S3-compatible store).
            # Fall back to an UNCONDITIONAL PUT of the freshly-merged result: the
            # merge already read the latest state this iteration, so it is still
            # never-regress; only the atomicity guard against a same-instant
            # concurrent write is lost (rare -- the cron runs mostly when the
            # worker is wedged). A genuine error (auth/etc.) re-raises here.
            print(f"publish: conditional PUT rejected ({code}) -- falling back "
                  f"to an unconditional PUT (atomicity guard degraded)",
                  file=sys.stderr)
            _put(client, bucket, key, merged)
        print(f"publish: wrote v2 manifest -> cycles="
              f"{[c['cycle'] for c in merged['cycles']]} "
              f"(legacy cycle={merged.get('cycle')})")
        return 0
    print("publish: exhausted retries under concurrent writes", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Merge + atomically publish the "
                                 "cron's v2 HAFS manifest to R2 (never-regress).")
    ap.add_argument("--manifest", required=True,
                    help="path to the cron's freshly-rendered manifest.json")
    ap.add_argument("--bucket", default=os.environ.get("R2_BUCKET",
                                                       "triple-a-tropics-media"))
    ap.add_argument("--key", default="models/hafs/manifest.json")
    ap.add_argument("--attempts", type=int, default=5)
    a = ap.parse_args(argv)
    return publish(a.manifest, bucket=a.bucket, key=a.key, attempts=a.attempts)


if __name__ == "__main__":
    sys.exit(main())
