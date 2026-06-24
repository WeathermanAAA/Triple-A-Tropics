"""tcprimed.fetch - anonymous (UNSIGNED) reads from the public TC-PRIMED bucket.

Bucket: s3://noaa-nesdis-tcprimed-pds  (NOAA Open Data, no credentials).
Path layout:
  v01r01/{tier}/{year}/{BASIN}/{NN}/TCPRIMED_v01r01-{tier}_{ATCFID}_{SENSOR}_{PLATFORM}_{rev}_{YYYYMMDDhhmmss}.nc

We use boto3 with botocore UNSIGNED so no AWS creds are touched (boto3 is already
a repo dependency for the R2 writer). list_objects_v2 with Delimiter="/" walks the
year/basin/NN prefix levels; a paginator handles >1000 keys per storm.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from typing import Iterable, Optional

from . import IMAGER_SENSORS

BUCKET = "noaa-nesdis-tcprimed-pds"
PREFIX_ROOT = "v01r01"

# TCPRIMED_v01r01-final_AL092024_AMSR2_GCOMW1_065717_20240924074222.nc
#                      ^ATCFID  ^SENSOR ^PLATFORM ^rev   ^YYYYMMDDhhmmss
_FN_RE = re.compile(
    r"TCPRIMED_v01r01-\w+_([A-Z]{2}\d{6})_([A-Z0-9]+)_([A-Z0-9]+)_(\d+)_(\d{14})\.nc$"
)


def _client():
    """Anonymous S3 client (UNSIGNED): no credentials, public-bucket reads only."""
    import boto3
    from botocore import UNSIGNED
    from botocore.client import Config
    return boto3.client(
        "s3",
        config=Config(signature_version=UNSIGNED,
                      retries={"max_attempts": 3, "mode": "standard"}),
    )


def parse_overpass_filename(name: str) -> Optional[dict]:
    """Parse a TC-PRIMED overpass filename into its fields, or None if it does
    not match / is not one of the imager sensors we process."""
    m = _FN_RE.search(name)
    if not m:
        return None
    atcf, sensor, platform, rev, stamp = m.groups()
    if sensor not in IMAGER_SENSORS:
        return None
    try:
        when = dt.datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(
            tzinfo=dt.timezone.utc)
    except ValueError:
        return None
    return {
        "atcf": atcf,
        "sensor": sensor,
        "platform": platform,
        "rev": rev,
        "stamp": stamp,
        "valid": when,
        # Stable per-overpass id: SENSOR_PLATFORM_timestamp (no rev - rev can
        # differ between reprocessings of the same pass; the timestamp is the
        # natural key and matches the spec's overpass id shape).
        "id": f"{sensor}_{platform}_{stamp}",
    }


def list_storms(tier: str, year: int, basin: str, client=None) -> list[str]:
    """ATCF ids that have at least one overpass for (tier, year, basin).

    Walks v01r01/{tier}/{year}/{BASIN}/ with Delimiter="/" to get the NN/ subdirs
    (annual storm numbers), then derives the ATCFID = {BASIN}{NN}{year}. Returns
    sorted, deduped ATCF ids (uppercase)."""
    c = client or _client()
    prefix = f"{PREFIX_ROOT}/{tier}/{year}/{basin.upper()}/"
    out: list[str] = []
    paginator = c.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            p = cp.get("Prefix", "")
            tail = p[len(prefix):].rstrip("/")
            if re.fullmatch(r"\d{2}", tail):
                out.append(f"{basin.upper()}{tail}{year}")
    return sorted(set(out))


def list_overpasses(tier: str, year: int, basin: str, nn: str,
                    client=None) -> list[dict]:
    """All processable (imager) overpasses under a storm's NN/ prefix.

    Returns the parsed dicts (see parse_overpass_filename) with an extra ``key``
    (the full S3 key), sorted by valid time."""
    c = client or _client()
    prefix = f"{PREFIX_ROOT}/{tier}/{year}/{basin.upper()}/{nn}/"
    out: list[dict] = []
    paginator = c.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".nc"):
                continue
            meta = parse_overpass_filename(key.rsplit("/", 1)[-1])
            if meta is None:
                continue
            meta["key"] = key
            out.append(meta)
    out.sort(key=lambda m: m["valid"])
    return out


def atcf_parts(atcf: str) -> tuple[str, str, int]:
    """('AL092024',) -> ('AL', '09', 2024)."""
    return atcf[:2].upper(), atcf[2:4], int(atcf[4:8])


def download(key: str, dest_dir: str, client=None) -> str:
    """Download an overpass NetCDF to dest_dir; returns the local path."""
    c = client or _client()
    os.makedirs(dest_dir, exist_ok=True)
    local = os.path.join(dest_dir, key.rsplit("/", 1)[-1])
    c.download_file(BUCKET, key, local)
    return local


def list_tiers_with_storm(year: int, basin: str, nn: str,
                          tiers: Iterable[str] = ("final", "preliminary"),
                          client=None) -> list[str]:
    """Which tiers carry overpasses for this storm (cheap one-key probe each)."""
    c = client or _client()
    have: list[str] = []
    for tier in tiers:
        prefix = f"{PREFIX_ROOT}/{tier}/{year}/{basin.upper()}/{nn}/"
        r = c.list_objects_v2(Bucket=BUCKET, Prefix=prefix, MaxKeys=1)
        if r.get("KeyCount", 0) > 0:
            have.append(tier)
    return have
