#!/usr/bin/env python3
"""purge_edge_cache.py — purge the Cloudflare edge cache for exactly the
site files a push to main changed, AFTER GitHub Pages has deployed them.

Why: the zone fronts the site with a ~4 h edge TTL, so any unstamped asset
(styles.css, every .html) lags up to 4 h behind a deploy — the root cause
of the strobe-saga stale-JS class and false "layout broken" tester reports.
Purging the changed URLs right after each Pages deploy closes the window to
~1 min.

Deliberately NEVER purge_everything: data workflows push to main many times
a day, and a full purge would also flush the cdn.* media cache (floater
frames, tiles) each time. URL-list purges only (30 URLs/call, free plan).

Env:
  GITHUB_REPOSITORY   owner/repo            (Actions default)
  GITHUB_TOKEN        API token for compare/pages polling
  BEFORE, AFTER       push range SHAs (github.event.before / .after)
  CLOUDFLARE_ZONE_ID, CLOUDFLARE_PURGE_TOKEN
                      zone + a token with Zone -> Cache Purge -> Purge.
                      Missing -> print a notice and exit 0 (workflow is
                      safe to ship before the token exists).
  SITE_HOST           default triple-a-tropics.com
  DRY_RUN             "1" -> print the URL list, purge nothing
  EXTRA_PATHS         optional space-separated repo paths to purge too
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

API = "https://api.github.com"
CF = "https://api.cloudflare.com/client/v4"
HOST = os.environ.get("SITE_HOST", "triple-a-tropics.com")
# repo dirs that are not meaningfully served (CI config, worker sources,
# tests, tooling) — everything else in this repo IS the site
SKIP_PREFIXES = (".github/", ".claude/", "workers/", "tests/", "scripts/",
                 "manual-fetch/")
MAX_URLS = 900          # 30 calls; a bigger push logs the overflow loudly
PAGES_WAIT_S = 360
CHUNK = 30


def gh(path: str):
    req = urllib.request.Request(API + path, headers={
        "authorization": f"token {os.environ['GITHUB_TOKEN']}",
        "accept": "application/vnd.github+json",
        "user-agent": "tat-purge-edge-cache",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def changed_paths(repo: str, before: str, after: str) -> list[str]:
    if not before or set(before) == {"0"}:
        print("no usable BEFORE sha; nothing to diff")
        return []
    cmp = gh(f"/repos/{repo}/compare/{before}...{after}")
    return [f["filename"] for f in cmp.get("files", [])]


def to_urls(paths: list[str]) -> list[str]:
    urls: list[str] = []
    for p in paths:
        if p.startswith(SKIP_PREFIXES):
            continue
        urls.append(f"https://{HOST}/{p}")
        if p == "index.html":
            urls.append(f"https://{HOST}/")
        elif p.endswith("/index.html"):
            urls.append(f"https://{HOST}/{p[: -len('index.html')]}")
    # de-dupe, stable order
    return list(dict.fromkeys(urls))


def wait_for_pages(repo: str, sha: str) -> None:
    """Poll the Pages build until our commit (or a successor) is live —
    purging before deploy would just re-cache the stale bytes."""
    deadline = time.time() + PAGES_WAIT_S
    while time.time() < deadline:
        try:
            b = gh(f"/repos/{repo}/pages/builds/latest")
            if b.get("status") == "built":
                if b.get("commit") == sha:
                    print(f"pages build of {sha[:10]} is live")
                    return
                # a later push superseded ours; its own run purges its files,
                # ours are deployed within that build too
                print(f"pages latest build is {str(b.get('commit'))[:10]} "
                      f"(ours {sha[:10]} superseded or included) — proceeding")
                return
        except Exception as e:  # noqa: BLE001 — keep polling
            print(f"pages poll: {e}")
        time.sleep(15)
    print("pages build wait timed out — purging anyway (late > never)")


def purge(urls: list[str]) -> None:
    zone = os.environ["CLOUDFLARE_ZONE_ID"]
    tok = os.environ["CLOUDFLARE_PURGE_TOKEN"]
    for i in range(0, len(urls), CHUNK):
        chunk = urls[i:i + CHUNK]
        req = urllib.request.Request(
            f"{CF}/zones/{zone}/purge_cache",
            data=json.dumps({"files": chunk}).encode(),
            headers={"authorization": f"Bearer {tok}",
                     "content-type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.load(r)
        if not res.get("success"):
            raise RuntimeError(f"purge failed: {res.get('errors')}")
        print(f"purged {len(chunk)} URLs ({i + len(chunk)}/{len(urls)})")


def main() -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    before = os.environ.get("BEFORE", "")
    after = os.environ.get("AFTER", "") or os.environ.get("GITHUB_SHA", "")
    dry = os.environ.get("DRY_RUN") == "1"

    paths = changed_paths(repo, before, after) if before else []
    paths += [p for p in os.environ.get("EXTRA_PATHS", "").split() if p]
    urls = to_urls(paths)
    if not urls:
        print("no served files in this push — nothing to purge")
        return
    if len(urls) > MAX_URLS:
        print(f"WARNING: {len(urls)} URLs changed; purging the first "
              f"{MAX_URLS}, DROPPING {len(urls) - MAX_URLS} (mass regen — "
              f"the 4 h TTL covers the rest)")
        urls = urls[:MAX_URLS]

    print(f"{len(urls)} URL(s) to purge:")
    for u in urls:
        print("  " + u)
    if dry:
        print("DRY_RUN=1 — not purging")
        return
    if not (os.environ.get("CLOUDFLARE_ZONE_ID")
            and os.environ.get("CLOUDFLARE_PURGE_TOKEN")):
        print("::notice::CLOUDFLARE_ZONE_ID / CLOUDFLARE_PURGE_TOKEN not set "
              "— skipping purge (add the secrets to activate; see "
              "queued manual steps)")
        return
    wait_for_pages(repo, after)
    purge(urls)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"::error::purge-edge-cache failed: {e}")
        sys.exit(1)
