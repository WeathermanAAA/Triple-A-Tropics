#!/usr/bin/env python3
"""SHIPS RAW ARCHIVER - copy NHC's ships/lsdiag text products to R2 before they
are gone.

WHY THIS IS URGENT AND WHY IT IS FIRST. There is NO archive of these files
anywhere on the NHC FTP: ``/atcf/archive/{YYYY}/`` contains zero ``*ships*``
and zero ``*lsdiag*`` entries. The live directories hold ONE SEASON - measured
2026-07-28, ``/atcf/stext/`` spans 2026-03-06 to 2026-07-28 (285 files) - and
when the season rolls over that history is not recoverable from anywhere. So
every cycle we fail to copy is permanently lost, and the only fix is to start
copying now. That is the whole justification for shipping a raw archiver before
anything that parses the data.

RAW ONLY. This module deliberately does NOT parse. Parsing is a separate,
revisable decision; archiving is a one-shot opportunity. The bytes go to R2
exactly as served, so any future parser can be re-run over the full history
rather than being limited to whatever a parser happened to extract at capture
time.

BOTH SIDES, INDEPENDENTLY. The environmental diagnostics split across two
products: the ``_ships.txt`` bulletin and its ``_lsdiag.dat`` sibling. The
low- and high-layer relative humidity rows (RHLO / RHHI) live ONLY in the
sibling - the bulletin carries just 700-500 mb RH - so archiving one is not
enough. They are USUALLY 1:1 but not always: measured 2026-07-28, 284 stems had
both, 1 had only the bulletin and 7 had only the sibling. Each side is therefore
fetched and recorded independently; a missing counterpart is normal, not an
error, and never blocks the side that does exist.

AL/EP/CP ONLY. No JTWC-basin SHIPS files exist - the directory carries only AL,
CP and EP, which matches the a-deck picture (JTWC basins publish no official,
consensus or statistical aids at all). Nothing is "missing" for WP/IO/SH.

TEST DECKS ARE EXCLUDED. Cyclone numbers 80-89 are GSTEST / ATCFTEST /
GCOLTEST / MLCOLTEST fixtures carrying physically absurd values (forecast VMAX
to 337 kt). 14 such files were present on 2026-07-28. They would corrupt any
aggregate built over the archive later, so they never enter it.

NEVER-MISS. The archive is IMMUTABLE and append-only: a stem already archived is
never re-fetched and never overwritten, so a truncated re-read can not replace a
good capture. What to fetch is decided by diffing the upstream listing against
an index in R2, and if that index is absent or corrupt it is REBUILT by listing
the bucket prefix - so losing the index costs one slow run, not the archive. A
run that finds upstream files but manages to store none exits non-zero rather
than quietly reporting success.

Run::

    python -m guidance.harvest_ships --dry-run           # what would be fetched
    python -m guidance.harvest_ships                     # fetch + upload to R2
    python -m guidance.harvest_ships --rebuild-index     # re-derive from R2
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import sys
from typing import Callable, Optional

log = logging.getLogger("ships-harvest")

STEXT_DIR = "https://ftp.nhc.noaa.gov/atcf/stext/"
LSDIAG_DIR = "https://ftp.nhc.noaa.gov/atcf/lsdiag/"

#: ``{YY}{MM}{DD}{HH}{BB}{NN}{YY}`` - note the storm year is TWO digits, not
#: four (the brief's four-digit example does not match the live feed).
STEM_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})(\d{2})([A-Z]{2})(\d{2})(\d{2})$")

#: R2 prefix. Kept separate from ``cyclolab/`` because this is a RAW archive
#: with its own lifecycle, not a rendered per-storm product.
R2_PREFIX = "ships"
INDEX_KEY = f"{R2_PREFIX}/index.json"
BUCKET = os.environ.get("R2_BUCKET", "triple-a-tropics-media")

#: The two sides of one cycle's diagnostics.
SIDES = (
    ("ships", STEXT_DIR, "_ships.txt", re.compile(r"([0-9]{8}[A-Z]{2}[0-9]{4})_ships\.txt")),
    ("lsdiag", LSDIAG_DIR, "_lsdiag.dat", re.compile(r"([0-9]{8}[A-Z]{2}[0-9]{4})_lsdiag\.dat")),
)

#: Basins that publish SHIPS at all.
SHIPS_BASINS = frozenset({"AL", "EP", "CP"})


class Stem:
    """A parsed SHIPS filename stem."""

    __slots__ = ("raw", "dtg", "basin", "cy", "year")

    def __init__(self, raw: str, dtg: str, basin: str, cy: int, year: int):
        self.raw, self.dtg, self.basin, self.cy, self.year = raw, dtg, basin, cy, year

    @property
    def sid(self) -> str:
        """CycloLab storm id, so the archive is keyed the same way the site is."""
        return f"NHC_{self.basin}{self.cy:02d}{self.year}"

    @property
    def is_test_deck(self) -> bool:
        return 80 <= self.cy <= 89

    def key(self, side: str, suffix: str) -> str:
        return f"{R2_PREFIX}/{self.sid}/{self.dtg}/{self.raw}{suffix}"

    def __repr__(self) -> str:
        return f"<Stem {self.raw} {self.sid} {self.dtg}>"


def parse_stem(raw: str) -> Optional[Stem]:
    """``26072818EP0726`` -> Stem, or None if it is not a well-formed stem.

    The leading two digits are the CYCLE year and the trailing two are the
    STORM year; both are expanded to 20xx. They are normally equal, and a
    mismatch is not an error worth rejecting on - the file is still real data.
    """
    m = STEM_RE.match(raw)
    if not m:
        return None
    yy, mm, dd, hh, basin, cy, syy = m.groups()
    if basin not in SHIPS_BASINS:
        return None
    try:
        year = 2000 + int(syy)
        dtg = f"20{yy}{mm}{dd}{hh}"
        dt.datetime.strptime(dtg, "%Y%m%d%H")     # reject impossible dates
    except ValueError:
        return None
    return Stem(raw, dtg, basin, int(cy), year)


# ---------------------------------------------------------------------------
# Upstream listing
# ---------------------------------------------------------------------------
def _http_get(url: str, timeout: float = 60.0) -> bytes:
    import requests
    r = requests.get(url, timeout=timeout,
                     headers={"User-Agent": "triple-a-tropics/ships-archive"})
    r.raise_for_status()
    return r.content


def list_upstream(opener: Optional[Callable] = None) -> dict:
    """``{side: {stem_raw: Stem}}`` for everything upstream currently serves.

    Test decks are dropped here rather than downstream, so they can never enter
    the archive even if a later caller forgets.
    """
    opener = opener or _http_get
    out: dict = {}
    for side, url, _suffix, pat in SIDES:
        body = opener(url).decode("utf-8", "replace")
        found = {}
        for raw in sorted(set(pat.findall(body))):
            st = parse_stem(raw)
            if st is None or st.is_test_deck:
                continue
            found[raw] = st
        out[side] = found
        log.info("upstream %-6s: %d file(s)", side, len(found))
    return out


# ---------------------------------------------------------------------------
# R2
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


def read_index(client, bucket: str = BUCKET) -> Optional[dict]:
    """The archive index, or None if absent/corrupt (caller rebuilds)."""
    from botocore.exceptions import ClientError
    try:
        r = client.get_object(Bucket=bucket, Key=INDEX_KEY)
        idx = json.loads(r["Body"].read())
        if not isinstance(idx, dict) or "archived" not in idx:
            raise ValueError("shape")
        return idx
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise
    except (ValueError, json.JSONDecodeError):
        log.warning("index is corrupt - will rebuild from the bucket")
        return None


def rebuild_index(client, bucket: str = BUCKET) -> dict:
    """Re-derive the index by LISTING the archive prefix.

    The self-healing path: losing or corrupting the index costs one slow run,
    never the archive itself.
    """
    archived: dict = {}
    token = None
    n = 0
    while True:
        kw = {"Bucket": bucket, "Prefix": R2_PREFIX + "/", "MaxKeys": 1000}
        if token:
            kw["ContinuationToken"] = token
        r = client.list_objects_v2(**kw)
        for o in r.get("Contents", []):
            key = o["Key"]
            if key.endswith("_ships.txt"):
                side = "ships"
            elif key.endswith("_lsdiag.dat"):
                side = "lsdiag"
            else:
                continue
            raw = key.rsplit("/", 1)[-1].split("_")[0]
            archived.setdefault(raw, {})[side] = o.get("Size", 0)
            n += 1
        if not r.get("IsTruncated"):
            break
        token = r.get("NextContinuationToken")
    log.info("rebuilt index from bucket: %d object(s), %d stem(s)", n, len(archived))
    return {"version": 1, "archived": archived, "rebuilt_at": _iso_now()}


def _iso_now() -> str:
    return (dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
            .isoformat().replace("+00:00", "Z"))


def write_index(client, idx: dict, bucket: str = BUCKET) -> None:
    idx["updated_at"] = _iso_now()
    client.put_object(
        Bucket=bucket, Key=INDEX_KEY,
        Body=json.dumps(idx, separators=(",", ":")).encode(),
        ContentType="application/json",
        CacheControl="public, max-age=60")


# ---------------------------------------------------------------------------
# Harvest
# ---------------------------------------------------------------------------
def plan(upstream: dict, index: dict) -> list:
    """``[(side, Stem)]`` for every file upstream has that the archive lacks.

    Each SIDE is decided independently: a stem whose bulletin is archived but
    whose sibling is not still yields the sibling. That is what makes the ~3%
    of unpaired stems a non-event.
    """
    have = index.get("archived", {})
    todo = []
    for side, _url, _suffix, _pat in SIDES:
        for raw, st in upstream.get(side, {}).items():
            if side not in have.get(raw, {}):
                todo.append((side, st))
    # Oldest first: if a run is cut short, the archive advances forward in time
    # rather than leaving a hole behind the newest captures.
    todo.sort(key=lambda t: (t[1].dtg, t[1].raw, t[0]))
    return todo


def harvest(*, dry_run: bool = False, limit: Optional[int] = None,
            opener: Optional[Callable] = None, client=None,
            bucket: str = BUCKET, rebuild: bool = False) -> dict:
    """Fetch every un-archived file and store it. Returns a summary dict."""
    opener = opener or _http_get
    upstream = list_upstream(opener)
    n_upstream = sum(len(v) for v in upstream.values())

    if dry_run and client is None:
        index = {"version": 1, "archived": {}}
    else:
        client = client or _r2_client()
        index = None if rebuild else read_index(client, bucket)
        if index is None:
            index = rebuild_index(client, bucket)

    todo = plan(upstream, index)
    if limit:
        todo = todo[:limit]
    log.info("archive holds %d stem(s); %d file(s) to fetch",
             len(index.get("archived", {})), len(todo))

    suffix_of = {s[0]: s[2] for s in SIDES}
    url_of = {s[0]: s[1] for s in SIDES}
    stored = 0
    failed = 0
    for side, st in todo:
        name = st.raw + suffix_of[side]
        url = url_of[side] + name
        if dry_run:
            log.info("  would fetch %s -> %s", name, st.key(side, suffix_of[side]))
            stored += 1
            continue
        try:
            body = opener(url)
        except Exception as e:  # noqa: BLE001 - one file must not sink the run
            log.warning("  FETCH FAILED %s: %s: %s", name, type(e).__name__, e)
            failed += 1
            continue
        if not body:
            log.warning("  EMPTY %s - not archiving (an empty capture would "
                        "permanently mask the real file)", name)
            failed += 1
            continue
        key = st.key(side, suffix_of[side])
        client.put_object(
            Bucket=bucket, Key=key, Body=body,
            ContentType="text/plain; charset=utf-8",
            # Immutable once written: these are per-cycle products that never
            # change after publication.
            CacheControl="public, max-age=31536000, immutable")
        index.setdefault("archived", {}).setdefault(st.raw, {})[side] = len(body)
        stored += 1
        log.info("  archived %s (%d bytes) -> %s", name, len(body), key)

    if not dry_run and stored:
        write_index(client, index, bucket)

    summary = {
        "upstream_files": n_upstream,
        "archive_stems": len(index.get("archived", {})),
        "planned": len(todo),
        "stored": stored,
        "failed": failed,
        "dry_run": dry_run,
    }
    log.info("ships-harvest: %s", json.dumps(summary))
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be fetched; touches nothing")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap the number of files fetched this run")
    ap.add_argument("--rebuild-index", action="store_true",
                    help="ignore the stored index and re-derive it from R2")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    s = harvest(dry_run=a.dry_run, limit=a.limit, rebuild=a.rebuild_index)

    # Upstream has files but we stored none AND hold none: that is a broken
    # run, not a quiet no-op. Fail loudly - a silent failure here loses history
    # that cannot be recovered later.
    if s["upstream_files"] and s["archive_stems"] == 0 and s["stored"] == 0:
        log.error("ships-harvest: upstream served %d file(s) but the archive is "
                  "EMPTY and nothing was stored - refusing to report success",
                  s["upstream_files"])
        return 1
    # Everything already archived is the healthy steady state.
    if s["planned"] and s["stored"] == 0:
        log.error("ships-harvest: %d file(s) were due but NONE stored "
                  "(%d fetch failures)", s["planned"], s["failed"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
