#!/usr/bin/env python3
"""Split reserved sandbox rows before model telemetry ingestion.

The existing collector transports short opaque rows. sc1_ rows have their own
validated schema/dataset; they never enter the models popularity summary.
"""
import datetime as dt
import json
import os
import re

PREFIX = "telemetry/sandbox-capability/"
PATTERN = re.compile(r"^sc1_([set])_(u|\d{1,3}_\d{1,3}_\d{1,3})_([cefso])([0-9]{1,3})_([wmlaio])_([ptd])_([nzaurle])$")
BROWSERS = {"c": "Chrome", "e": "Edge", "f": "Firefox", "s": "Safari", "o": "Other"}
SYSTEMS = {"w": "Windows", "m": "macOS", "l": "Linux", "a": "Android", "i": "iOS", "o": "Other"}
DEVICES = {"p": "phone", "t": "tablet", "d": "desktop"}
OUTCOMES = {"n": "api_absent", "z": "adapter_absent", "a": "adapter_available", "u": "unknown"}
EVENTS = {"r": "radar_ready", "l": "device_lost", "e": "export_error"}


def decode(row):
    if not isinstance(row, dict):
        return None
    match = PATTERN.fullmatch(str(row.get("p", "")))
    count = row.get("v")
    if not match or isinstance(count, bool) or not isinstance(count, int) or count < 1:
        return None
    kind, version, browser, major, system, device, outcome = match.groups()
    labels = EVENTS if kind == "e" else OUTCOMES
    if outcome not in labels:
        return None
    return {"kind": kind, "build": "unknown" if version == "u" else "Beta " + ".".join(str(int(n)) for n in version.split("_")),
            "browser": BROWSERS[browser], "browserMajor": int(major), "os": SYSTEMS[system],
            "device": DEVICES[device], "outcome": labels[outcome], "count": min(count, 500) if kind == "e" else 1}


def split(payload):
    models, capability, diagnostics = [], [], []
    for row in (payload.get("rows") or [])[:64]:
        if isinstance(row, dict) and str(row.get("p", "")).startswith("sc1_"):
            clean = decode(row)
            if clean:
                (diagnostics if clean["kind"] == "t" else capability).append(clean)
        else:
            models.append(row)
    return models, capability, diagnostics


def fold(summary, rows, day, batch_id):
    """Idempotent per Actions run; retries cannot double its received counts."""
    summary.setdefault("v", 1)
    summary["prospective"] = True
    summary["sessionDefinition"] = "received tab-session capability samples per build; runtime events are separate attempt counts"
    summary.setdefault("firstReceivedDay", day)
    seen = summary.setdefault("processed", {})
    if batch_id in seen:
        return False
    seen[batch_id] = day
    bucket = summary.setdefault("days", {}).setdefault(day, {"sessions": 0, "outcomes": {}, "events": {}, "platforms": {}})
    for row in rows:
        key = "|".join(str(row[k]) for k in ("build", "browser", "browserMajor", "os", "device"))
        platform = bucket["platforms"].setdefault(key, {k: row[k] for k in ("build", "browser", "browserMajor", "os", "device")})
        platform.setdefault("sessions", 0); platform.setdefault("outcomes", {}); platform.setdefault("events", {})
        for target in (bucket, platform):
            if row["kind"] == "s":
                target["sessions"] += row["count"]
            field = "events" if row["kind"] == "e" else "outcomes"
            target[field][row["outcome"]] = target[field].get(row["outcome"], 0) + row["count"]
    cutoff = (dt.date.fromisoformat(day) - dt.timedelta(days=90)).isoformat()
    summary["days"] = {k: v for k, v in summary["days"].items() if k >= cutoff}
    summary["processed"] = {k: v for k, v in seen.items() if v >= cutoff}
    summary["updated"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return True


def summarize(client, bucket, root=PREFIX, diagnostic=False):
    def put(key, value):
        client.put_object(Bucket=bucket, Key=key, Body=json.dumps(value).encode(), ContentType="application/json", CacheControl="no-cache")
    try:
        summary = json.loads(client.get_object(Bucket=bucket, Key=root + "state.json")["Body"].read())
    except client.exceptions.NoSuchKey:
        summary = {}
    folded = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=root + "inbox/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if obj.get("Size", 0) > 65536:
                raise ValueError("Oversized capability inbox object")
            batch = json.loads(client.get_object(Bucket=bucket, Key=key)["Body"].read())
            rows = batch["rows"]
            if diagnostic:
                rows = [{**row, "kind": "s"} for row in rows]
            fold(summary, rows, batch["receivedDay"], key.rsplit("/", 1)[-1].removesuffix(".json"))
            folded.append(key)
    if not summary:
        return 0
    if diagnostic:
        summary["diagnostic"] = True
    put(root + "state.json", summary)
    put(root + "summary.json", {k: v for k, v in summary.items() if k != "processed"})
    # Delete only after both durable writes. A crash/retry before this point is
    # idempotent through processed run IDs; it cannot silently count a batch twice.
    for offset in range(0, len(folded), 1000):
        client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": key} for key in folded[offset:offset+1000]]})
    return len(folded)


def main():
    import boto3
    client = boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"],
                          aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
                          aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"])
    bucket = "triple-a-tropics-media"
    def put(key, value):
        client.put_object(Bucket=bucket, Key=key, Body=json.dumps(value).encode(), ContentType="application/json", CacheControl="no-cache")
    if os.environ.get("CAPABILITY_MODE") == "summarize":
        print(json.dumps({"capabilityBatches": summarize(client, bucket),
                          "diagnosticBatches": summarize(client, bucket, PREFIX + "checks/", True)}))
        return
    payload = json.loads(os.environ["PAYLOAD"])
    if not isinstance(payload, dict):
        raise ValueError("Expected telemetry object")
    models, rows, diagnostics = split(payload)
    day = dt.datetime.now(dt.timezone.utc).date().isoformat()
    batch = os.environ["GITHUB_RUN_ID"]
    if not re.fullmatch(r"\d+", batch):
        raise ValueError("Invalid workflow run ID")
    if models:
        put(f"telemetry/inbox/{day}/{batch}.json", {**payload, "rows": models})
    if rows:
        put(f"{PREFIX}inbox/{day}/{batch}.json", {"v": 1, "receivedDay": day, "rows": rows})
    if diagnostics:
        value = {"v": 1, "receivedDay": day, "diagnostic": True, "rows": diagnostics}
        put(f"{PREFIX}checks/{batch}.json", value)
        put(f"{PREFIX}checks/inbox/{day}/{batch}.json", value)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as out:
            out.write("needs_summary=" + ("true" if rows or diagnostics else "false") + "\n")
    print(json.dumps({"modelsRows": len(models), "capabilityRows": len(rows), "diagnosticRows": len(diagnostics), "run": batch}))


if __name__ == "__main__":
    main()
