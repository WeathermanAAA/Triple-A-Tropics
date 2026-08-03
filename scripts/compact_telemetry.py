#!/usr/bin/env python3
"""Fold telemetry/inbox/* into telemetry/summary.json on R2, then delete the
folded batches. Runs inside update-guidance (4x daily). The summary is the
per-product demand signal the hero-set scheduler (spec #30) will read:
{day: {product: {v: views, d: dwell_s}}}, rolling 90 days.
"""
import datetime as dt
import json
import os

import boto3

BUCKET = "triple-a-tropics-media"
PREFIX = "telemetry/inbox/"
SUMMARY = "telemetry/summary.json"
KEEP_DAYS = 90
MAX_BATCH_BYTES = 65536


def main() -> int:
    c = boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"])
    try:
        summary = json.loads(
            c.get_object(Bucket=BUCKET, Key=SUMMARY)["Body"].read())
    except Exception:  # noqa: BLE001 - first run / missing / corrupt
        summary = {"v": 1, "days": {}}
    days = summary.setdefault("days", {})

    folded = []
    token = None
    while True:
        kw = {"Bucket": BUCKET, "Prefix": PREFIX, "MaxKeys": 1000}
        if token:
            kw["ContinuationToken"] = token
        r = c.list_objects_v2(**kw)
        for o in r.get("Contents", []):
            key = o["Key"]
            if o.get("Size", 0) > MAX_BATCH_BYTES:
                folded.append(key)      # oversized garbage: delete unread
                continue
            try:
                batch = json.loads(
                    c.get_object(Bucket=BUCKET, Key=key)["Body"].read())
                day = key[len(PREFIX):].split("/")[0]
                bucket_day = days.setdefault(day, {})
                for row in (batch.get("rows") or [])[:64]:
                    p = str(row.get("p", ""))[:40]
                    if not p:
                        continue
                    e = bucket_day.setdefault(p, {"v": 0, "d": 0})
                    e["v"] += max(0, int(row.get("v", 0)))
                    e["d"] += max(0, int(row.get("d", 0)))
            except Exception as ex:  # noqa: BLE001 - one bad batch never blocks
                print(f"skip {key}: {ex}")
            folded.append(key)
        token = r.get("NextContinuationToken")
        if not token:
            break

    cutoff = (dt.date.today() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    for day in [d for d in days if d < cutoff]:
        del days[day]

    if folded:
        summary["updated"] = dt.datetime.now(dt.timezone.utc).isoformat()
        c.put_object(Bucket=BUCKET, Key=SUMMARY,
                     Body=json.dumps(summary).encode(),
                     ContentType="application/json")
        for i in range(0, len(folded), 1000):
            c.delete_objects(Bucket=BUCKET, Objects={
                "Objects": [{"Key": k} for k in folded[i:i + 1000]]}
                if False else {"Objects": [{"Key": k} for k in folded[i:i + 1000]]})
        print(f"folded {len(folded)} batch(es); days retained: {len(days)}")
    else:
        print("inbox empty")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
