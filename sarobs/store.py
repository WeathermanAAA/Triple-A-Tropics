"""sarobs.store - Local / R2 storage (the house poller store abstraction).

R2 creds come from the environment (R2_ENDPOINT, R2_ACCESS_KEY_ID,
R2_SECRET_ACCESS_KEY, R2_BUCKET default triple-a-tropics-media) — the same
set every box poller already uses; nothing new. The live R2 tree is also the
poller's WATERMARK (get_json of manifest/indexes) so the container stays
fully disposable.
"""
from __future__ import annotations

import json
import os
import pathlib

# The R2 write kill switch (tat_killswitch.py at the repo root, mirrored in
# tsr). Optional on purpose: a missing or broken module means "allowed" --
# the switch can only ever STOP writes, never break a lane. Deletes are
# never guarded (free on R2; prune must keep reducing storage).
try:
    import tat_killswitch
except Exception:  # noqa: BLE001
    tat_killswitch = None


class LocalStore:
    def __init__(self, root: str):
        self.root = pathlib.Path(root)

    def put(self, key: str, body: bytes, content_type: str,
            cache_control: str) -> None:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)

    def get_json(self, key: str):
        p = self.root / key
        if not p.exists():
            return None
        return json.loads(p.read_text())

    def get_bytes(self, key: str):
        p = self.root / key
        return p.read_bytes() if p.exists() else None


class R2Store:
    def __init__(self, bucket: str | None = None):
        import boto3
        self.bucket = bucket or os.environ.get("R2_BUCKET",
                                               "triple-a-tropics-media")
        self.c = boto3.client(
            "s3", endpoint_url=os.environ["R2_ENDPOINT"],
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"])

    def put(self, key: str, body: bytes, content_type: str,
            cache_control: str) -> None:
        if tat_killswitch is not None and not tat_killswitch.writes_allowed(key):
            return                       # dropped (the switch logs it)
        self.c.put_object(Bucket=self.bucket, Key=key, Body=body,
                          ContentType=content_type, CacheControl=cache_control)

    def get_json(self, key: str):
        """None only for a genuinely-missing key; transient errors RAISE (a
        watermark read failure must fail the tick, not look like 'no data'
        and trigger a full re-render/re-publish)."""
        try:
            r = self.c.get_object(Bucket=self.bucket, Key=key)
        except self.c.exceptions.NoSuchKey:
            return None
        return json.loads(r["Body"].read())

    def get_bytes(self, key: str):
        """Raw object bytes, or None for a missing key."""
        try:
            r = self.c.get_object(Bucket=self.bucket, Key=key)
        except self.c.exceptions.NoSuchKey:
            return None
        return r["Body"].read()


def make_store(spec: str):
    """'r2' or 'local:/path'."""
    if spec == "r2":
        return R2Store()
    if spec.startswith("local:"):
        return LocalStore(spec[len("local:"):])
    raise ValueError(f"unknown store spec: {spec}")
