#!/usr/bin/env python3
"""Report the age of the PUBLISHED HAFS manifest, for the cron's dead-man check.

Why this exists
---------------
``update-hafs.yml`` can be stood down with the repo variable
``RENDER_HAFS_ON_CRON=false`` so another renderer can own ``models/hafs``. On
2026-07-19 that variable was set while the intended successor -- the box
``hafs-worker`` -- was profile-gated OFF and had never been started on any box.
Neither renderer ran. ``/models/`` then served cycle 2026071906 for eight days
as though it were current, and the only thing that noticed was the external
watchdog, which had no token and could merely log ``WOULD dispatch``.

So the gate is not allowed to be unconditional any more: the cron stands down
only while something else is *demonstrably* still publishing. This script is
that "demonstrably" -- it prints the published manifest's age in hours and
exits 0 if the manifest is STALE (i.e. the cron should render regardless of the
variable), 1 if it is fresh.

Unreadable manifest counts as stale. A renderer that cannot even prove the
product exists must not be silenced by a config flag.

    python3 scripts/hafs_manifest_age.py [--max-age-h 9] [--url URL]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request

DEFAULT_URL = "https://cdn.triple-a-tropics.com/models/hafs/manifest.json"
# The CDN 403s a bare urllib request (default Python-urllib UA); curl works.
# Without this the probe reports "unreadable" for a perfectly healthy manifest,
# which would make it cry stale forever -- a dead-man check that always fires
# is as useless as one that never does.
UA = "Mozilla/5.0 (compatible; TAT-hafs-freshness/1.0; +https://triple-a-tropics.com)"
# 6-h cycle cadence + a full render + slack. Below ~7 h a slow-but-healthy
# cycle would trip it; far above and a real stall hides for most of a day.
DEFAULT_MAX_AGE_H = 9.0
UNREADABLE = 999.0


def manifest_age_hours(url: str = DEFAULT_URL, timeout: int = 60,
                       now: dt.datetime | None = None) -> float:
    """Hours since the published manifest's ``generated_at``.

    Returns ``UNREADABLE`` (a large number, so callers treat it as stale)
    when the manifest is missing, unparseable, or has no usable stamp --
    never raises, because this runs in a workflow gate.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            gen = (json.load(r) or {}).get("generated_at") or ""
        stamp = dt.datetime.strptime(gen, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc)
    except Exception:                                   # noqa: BLE001
        return UNREADABLE
    return (now - stamp).total_seconds() / 3600.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--max-age-h", type=float, default=DEFAULT_MAX_AGE_H)
    ap.add_argument("--github-output", default=None,
                    help="path to append stale=/age_h= (GITHUB_OUTPUT)")
    a = ap.parse_args(argv)

    age = manifest_age_hours(a.url)
    stale = age > a.max_age_h
    shown = "unreadable" if age >= UNREADABLE else f"{age:.2f}h"
    print(f"published HAFS manifest age={shown} threshold={a.max_age_h}h -> "
          + ("STALE (cron renders regardless of RENDER_HAFS_ON_CRON)"
             if stale else "fresh (the configured gate stands)"))
    if a.github_output:
        with open(a.github_output, "a", encoding="utf-8") as fh:
            fh.write(f"stale={'true' if stale else 'false'}\n")
            fh.write(f"age_h={age:.2f}\n")
    return 0 if stale else 1


if __name__ == "__main__":
    raise SystemExit(main())
