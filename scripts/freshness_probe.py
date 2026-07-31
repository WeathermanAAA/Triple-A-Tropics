#!/usr/bin/env python3
"""freshness_probe.py — standing origin-freshness monitor for every live
TAT data product (built from the 2026-07-16 full-site staleness audit, so
Andrew stops eyeballing products one by one).

For each registered product the probe fetches the ORIGIN (cache-busted CDN
read straight through to R2, or the GitHub API for committed/orphan-branch
products — never the browser/edge path), extracts the newest data
timestamp, and compares its age against the writer's expected cadence:

    stale  <=>  age_min > max(3 * cadence_min, cadence_min + 45)

(the same 3x-cadence-plus-slack margin the site's own honesty gates use —
objfix's WP gate, sat-health.js). Output is ONE rollup JSON written to
`feeds/freshness.json` on R2:

    {generated_utc, n, n_stale, stale: [names...], products: [
        {name, writer, cadence_min, last_utc, age_min, stale, note}]}

plus a nonzero exit when a product NEWLY went stale versus the previous
rollup (read back from the CDN), so the workflow run goes red exactly once
per new incident — GH's failure email is the alert channel; an
already-known-stale product does not re-fail every run.

KNOWN-DOWN list: products the audit left waiting on QUEUED BOX STEPS
(fd/wpac/himawari-fd emit suites, floater fleet) carry known_down=True so
they report+count but never fail the run — they go back to normal alerting
the moment they first turn fresh (the CDN prior shows them fresh).

Registry notes live next to each entry. Timestamps come from each
product's own manifest fields (generated_utc / as_of / latest / cycle),
never from HTTP Date headers, except where Last-Modified IS the write
time (R2 object PUT time) and the manifest carries no stamp.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request

CDN = "https://cdn.triple-a-tropics.com/"
API = "https://api.github.com/repos/WeathermanAAA/Triple-A-Tropics"
UA = {"User-Agent": "tat-freshness-probe"}

OUT = os.environ.get("FRESHNESS_OUT", "./freshness_build/freshness.json")
PRIOR_URL = CDN + "feeds/freshness.json"


def _get(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers=UA)
    tok = os.environ.get("GITHUB_TOKEN")
    if url.startswith("https://api.github.com") and tok:
        req.add_header("Authorization", f"Bearer {tok}")
    return urllib.request.urlopen(req, timeout=timeout)


def _json(url: str):
    with _get(url) as r:
        return json.load(r)


def _cdn_json(path: str):
    return _json(CDN + path + ("&" if "?" in path else "?") +
                 f"t={int(time.time())}")


def _parse_any(ts):
    """ISO / compact-stamp / epoch -> aware datetime, else None."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc)
    s = str(ts).strip()
    m = re.match(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$", s)
    if m:
        return dt.datetime(*map(int, m.groups()), tzinfo=dt.timezone.utc)
    m = re.match(r"^(\d{4})(\d{2})(\d{2})(\d{2})$", s)  # HAFS cycle 2026071606
    if m:
        return dt.datetime(*map(int, m.groups()), tzinfo=dt.timezone.utc)
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


# ---- extractors: each returns the newest DATA timestamp (aware dt) --------

def j(path: str, *keys):
    """CDN JSON -> first parseable of the given (possibly dotted) keys."""
    def fn():
        doc = _cdn_json(path)
        for k in keys:
            cur = doc
            for part in k.split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
                if cur is None:
                    break
            t = _parse_any(cur)
            if t:
                return t
        return None
    return fn


def jlist_max(path: str, list_key: str, item_key: str):
    """CDN JSON -> max timestamp over doc[list_key][*][item_key]."""
    def fn():
        doc = _cdn_json(path)
        best = None
        for it in (doc.get(list_key) or []):
            t = _parse_any(it.get(item_key) if isinstance(it, dict) else it)
            if t and (best is None or t > best):
                best = t
        return best
    return fn


def head_lm(path: str):
    """CDN object Last-Modified (R2 PUT time) — for stampless binaries."""
    def fn():
        req = urllib.request.Request(
            CDN + path + f"?t={int(time.time())}", headers=UA, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as r:
            return _parse_any_http(r.headers.get("Last-Modified"))
    return fn


def _parse_any_http(s):
    if not s:
        return None
    try:
        import email.utils
        return email.utils.parsedate_to_datetime(s)
    except Exception:  # noqa: BLE001
        return None


def git_path(path: str):
    """Newest commit on origin/main touching path (GitHub API)."""
    def fn():
        doc = _json(f"{API}/commits?path={path}&per_page=1&sha=main")
        return _parse_any(doc[0]["commit"]["committer"]["date"]) if doc else None
    return fn


def git_branch(branch: str):
    """Tip-commit time of a branch (the SST orphan media branch)."""
    def fn():
        doc = _json(f"{API}/branches/{branch}")
        return _parse_any(doc["commit"]["commit"]["committer"]["date"])
    return fn


# ---- the registry ----------------------------------------------------------
# (name, writer, cadence_min, extractor, known_down_note-or-None)
# cadence = how often NEW DATA should land at origin under normal operation.

REGISTRY = [
    # live feeds (box intensity poller, ~2 min; alert margin comes from the
    # formula floor of cadence+45)
    ("feeds/al_ace_data.json", "box intensity poller", 2,
     j("feeds/al_ace_data.json", "generated_utc"), None),
    ("feeds/ep_ace_data.json", "box intensity poller", 2,
     j("feeds/ep_ace_data.json", "generated_utc"), None),
    ("feeds/wp_ace_data.json", "box intensity poller", 2,
     j("feeds/wp_ace_data.json", "generated_utc"), None),
    ("feeds/al_tracks_data.json", "box intensity poller", 2,
     j("feeds/al_tracks_data.json", "generated_utc"), None),
    ("feeds/ep_tracks_data.json", "box intensity poller", 2,
     j("feeds/ep_tracks_data.json", "generated_utc"), None),
    ("feeds/wp_tracks_data.json", "box intensity poller", 2,
     j("feeds/wp_tracks_data.json", "generated_utc"), None),
    ("global_storms.geojson", "box intensity poller", 2,
     j("global_storms.geojson", "generated_utc"), None),

    # committed chart pages (GH Action update-ace.yml, 6 h)
    ("al_ace.html (page)", "GH update-ace.yml", 360,
     git_path("al_ace.html"), None),
    ("wp_tracks.html (page)", "GH update-ace.yml", 360,
     git_path("wp_tracks.html"), None),

    # SST family (GH Actions, daily)
    ("sst statics (sst/)", "GH update-sst.yml", 1440,
     git_path("sst"), None),
    ("subsurface (subsurface/)", "GH update-subsurface.yml", 1440,
     git_path("subsurface"), None),
    ("armor3d (armor3d/)", "GH update-armor3d.yml", 1440,
     git_path("armor3d"), None),
    ("season gif wpac (R2)", "GH update-season-gifs.yml", 1440,
     head_lm(f"wpac_{dt.date.today().year}_season.gif"), None),
    # Repointed 2026-07-31: this entry still probed the mp4-artifacts ORPHAN
    # BRANCH, which was retired on 2026-07-19 (R2 is canonical) - a dead probe
    # that read as permanently stale. The live manifest is sst/manifest.json.
    ("sst animations (R2 manifest)", "GH update-sst.yml", 1440,
     j("sst/manifest.json", "generated_at"), None),

    # models
    # RE-ARMED 2026-07-31. This entry carried a known-down annotation left
    # over from the FIRST HAFS staleness incident ("gated off via
    # RENDER_HAFS_ON_CRON; manifest fix in flight") - and known-down products
    # never alarm, so the SECOND and THIRD incidents ran silent: three stalls
    # in two weeks and not one alert reached a human before the stale banner
    # reached users. A silencer without an expiry becomes a blindfold; if HAFS
    # must ever be muted again, put the reason AND the re-arm condition here.
    ("models/hafs/manifest.json", "GH update-hafs.yml", 360,
     j("models/hafs/manifest.json", "generated_at", "cycle"), None),
    ("models/enscenters/manifest.json", "GH enscenters workflows", 360,
     j("models/enscenters/manifest.json", "generated_at"), None),

    # CycloLab
    ("cyclolab analogs (manifest)", "GH update-analogs.yml", 360,
     j("cyclolab/manifest.json", "generated_utc"), None),

    # subseasonal (GH update-subseasonal.yml, daily)
    ("subseasonal vp_meta.json", "GH update-subseasonal.yml", 1440,
     j("subseasonal/vp_meta.json", "generated_utc"), None),
    ("subseasonal hov_meta.json", "GH update-subseasonal.yml", 1440,
     j("subseasonal/hov_meta.json", "generated_utc"), None),
    ("subseasonal mjo_meta.json", "GH update-subseasonal.yml", 1440,
     j("subseasonal/mjo_meta.json", "generated_utc"), None),

    # explorer sat suites (box s2 emit-cron / GH riders). Scan time is the
    # honest signal (as_of refreshes only on new data).
    ("explorer goes19/conus/ir", "box s2 emit-cron (conus)", 60,
     j("shadow/sat/goes19/conus/ir/latest_times.json", "latest"), None),
    ("explorer goes19/fd/ir", "GH emit-geo-global rider (box fd cron queued)",
     60, j("shadow/sat/goes19/fd/ir/latest_times.json", "latest"), None),
    ("explorer himawari9/wpac/ir", "GH emit-geo-global rider (box cron queued)",
     60, j("shadow/sat/himawari9/wpac/ir/latest_times.json", "latest"), None),
    ("explorer geo/global/ir", "GH emit-geo-global.yml", 60,
     j("shadow/sat/geo/global/ir/latest_times.json", "latest"), None),
    ("explorer goes19/conus/truecolor", "box s2 emit-cron (conus)", 60,
     j("shadow/sat/goes19/conus/truecolor/latest_times.json", "latest"),
     "box post-restore band failure under investigation (2026-07-16)"),
    ("explorer goes19/fd suite (sandwich)", "box s2 emit-cron fd — CRON NOT STARTED",
     60, j("shadow/sat/goes19/fd/sandwich/latest_times.json", "latest"),
     "queued box step: S2_CRON_SUITES + emit-cron restart"),
    ("explorer himawari9/wpac suite (sandwich)",
     "box s2 emit-cron wpac — CRON NOT STARTED", 60,
     j("shadow/sat/himawari9/wpac/sandwich/latest_times.json", "latest"),
     "queued box step: S2_CRON_SUITES + emit-cron restart"),

    # floater fleet + backdrops (box floater poller)
    ("floaters fleet manifest", "box floater poller", 15,
     j("floaters/manifest.json", "generated_utc", "generated", "as_of"),
     "box floater poller stalled 2026-07-15 ~01Z; restart queued"),
    ("floater backdrops", "box floater poller", 60,
     j("floaters/backdrops.json", "generated_utc", "generated", "as_of"),
     "box floater poller stalled 2026-07-15 ~01Z; restart queued"),

    # MW / ASCAT / recon swaths (GH Actions)
    ("microwave manifest", "GH update-tcprimed tiers", 180,
     j("microwave/manifest.json", "generated_utc", "generated"), None),
    ("ascat manifest", "GH ascat workflow", 180,
     j("ascat/manifest.json", "generated_utc", "generated"), None),
    ("recon manifest", "GH recon workflow", 180,
     j("recon/manifest.json", "generated_utc", "generated"), None),
]


def doc_ts(t: "dt.datetime") -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(v):
    """Rollup timestamp -> aware datetime, or None (absent/garbage)."""
    if not v:
        return None
    try:
        return dt.datetime.strptime(str(v), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def main() -> None:
    now = dt.datetime.now(dt.timezone.utc)
    rows, stale_names = [], []
    for name, writer, cadence, extract, known_down in REGISTRY:
        last = age_min = None
        note = known_down or ""
        try:
            last = extract()
        except Exception as e:  # noqa: BLE001 — a probe error IS a finding
            note = (note + " · " if note else "") + \
                f"probe error: {type(e).__name__}: {e}"
        if last is not None:
            age_min = (now - last).total_seconds() / 60.0
        stale_at = max(3 * cadence, cadence + 45)
        stale = age_min is None or age_min > stale_at
        if stale:
            stale_names.append(name)
        rows.append({
            "name": name, "writer": writer, "cadence_min": cadence,
            "last_utc": last.strftime("%Y-%m-%dT%H:%M:%SZ") if last else None,
            "age_min": round(age_min, 1) if age_min is not None else None,
            "stale_after_min": stale_at, "stale": stale,
            "known_down": bool(known_down), "note": note,
        })
        print(f"{'STALE ' if stale else 'fresh '} {name}: "
              f"age={rows[-1]['age_min']} min (limit {stale_at})"
              + (f" · {note}" if note else ""))

    # Prior rollup FIRST: the alerting state (stale_since / last_alarm) is
    # carried through the published document, so it must be read before this
    # run's document is written.
    try:
        prior = _json(PRIOR_URL + f"?t={int(time.time())}")
        prior_stale = set(prior.get("stale") or [])
        prior_rows = {r.get("name"): r for r in (prior.get("products") or [])}
    except Exception:  # noqa: BLE001 — first run / CDN hiccup: no alerting
        prior_stale = set(stale_names)  # treat everything as already known
        prior_rows = {}

    # ESCALATING RE-ALARM (2026-07-31, after the third silent HAFS stall).
    # Red-once alerting fires ONE email the moment a product goes stale, and
    # never again - one missed email at 05:49 became a three-day outage that
    # users saw before anyone did. Detection now escalates: a product that
    # STAYS stale re-alarms every REALARM_H hours until it recovers. The
    # cadence is hours, not the probe's half-hour tick, so a real outage
    # nags without becoming spam that trains people to ignore it.
    REALARM_H = 6.0
    newly, realarm = [], []
    for r in rows:
        if not r["stale"]:
            continue
        p = prior_rows.get(r["name"]) or {}
        r["stale_since"] = p.get("stale_since") or doc_ts(now)
        r["last_alarm"] = p.get("last_alarm")
        if r["known_down"]:
            continue
        if r["name"] not in prior_stale:
            newly.append(r["name"])
            r["last_alarm"] = doc_ts(now)
        else:
            last = _parse_ts(r.get("last_alarm"))
            if last is None or (now - last).total_seconds() >= REALARM_H * 3600:
                hours = None
                since = _parse_ts(r.get("stale_since"))
                if since is not None:
                    hours = (now - since).total_seconds() / 3600.0
                realarm.append(f"{r['name']} (stale "
                               f"{hours:.0f}h)" if hours is not None
                               else r["name"])
                r["last_alarm"] = doc_ts(now)

    doc = {"schema": "tat-freshness/1",
           "generated_utc": doc_ts(now),
           "n": len(rows), "n_stale": len(stale_names),
           "stale": stale_names, "products": rows}
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"))
    print(f"wrote {OUT}: {len(stale_names)}/{len(rows)} stale")

    if newly or realarm:
        parts = []
        if newly:
            parts.append("NEWLY STALE since last rollup: " + ", ".join(newly))
        if realarm:
            parts.append("STILL STALE (re-alarm, every "
                         f"{REALARM_H:.0f}h until recovery): "
                         + ", ".join(realarm))
        raise SystemExit(" | ".join(parts))


if __name__ == "__main__":
    main()
