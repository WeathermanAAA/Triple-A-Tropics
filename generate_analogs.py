#!/usr/bin/env python3
"""generate_analogs.py - write cyclolab/{sid}/analogs.json for the CycloLab
"most resembles" engine. The GitHub Actions workflow (update-analogs.yml) runs
this and `aws s3 sync`s the output tree to R2 (cyclolab/ prefix), mirroring the
recon / tcprimed pollers (per-entity isolation, idempotent, no --delete).

Modes
-----
  --active            process currently-active storms (lead-time mode); the live
                      cyclolab tracks feed is the authoritative target track,
                      candidates come from the tropycal archive.
  --storm SID [...]   process specific storms (shape mode, target from tropycal).
  --archive BASIN     backfill a basin's archive (shape mode); --years A B bounds
                      the seasons (default: floor..current). Run once.

tropycal is the data engine (HURDAT2 / IBTrACS); ace_core is untouched.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.request

import cyclolab_analogs as A

CDN = "https://cdn.triple-a-tropics.com"
RECON_MANIFEST = f"{CDN}/recon/manifest.json"
# Per-basin live tracks feeds (cyclolab sids + is_active + points). IO/SH may not
# exist yet -> fetched best-effort.
FEED_BASES = {b: f"{CDN}/feeds/{b}_tracks_data.json" for b in ("al", "ep", "wp")}
_UA = "Mozilla/5.0 (compatible; TripleATropics-analogs/1.0)"
# Feed nature codes counted as the tropical/subtropical cyclone life (the feed
# lumps TD/TS/HU as 'TS'); DS (disturbance/invest) + ET (extratropical) dropped.
FEED_TROPICAL = frozenset({"TS", "SS", "NR"})


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


ARCHIVE_INDEX = f"{CDN}/cyclolab/archive/index.json"


def load_recon_index() -> int:
    """Register the recon-available ATCF ids + name-slugs (enrichment only)."""
    try:
        man = _get_json(RECON_MANIFEST)
        storms = man.get("storms") or []
        atcfs = [s.get("atcf") for s in storms if isinstance(s, dict)]
        slugs = [s.get("slug") for s in storms if isinstance(s, dict)]
        A.set_recon_index(atcfs=atcfs, slugs=slugs)
        return len(A._RECON_ATCFS) + len(A._RECON_SLUGS)
    except Exception as e:  # noqa: BLE001
        print(f"analogs: recon index unavailable ({type(e).__name__}); "
              f"recon flags off this run", file=sys.stderr)
        return 0


def active_storms():
    """[(sid, feed_storm)] for currently-active designated storms across the feeds."""
    out = []
    for base, url in FEED_BASES.items():
        try:
            feed = _get_json(url)
        except Exception as e:  # noqa: BLE001
            print(f"analogs: feed {base} unavailable ({type(e).__name__})",
                  file=sys.stderr)
            continue
        for s in feed.get("storms", []) or []:
            if s.get("is_active") and A.normalize_sid(s.get("sid"))[0]:
                out.append((s["sid"], s))
    return out


def feed_target_points(feed_storm):
    """(lats, lons, vmax, times) of the feed track's tropical/subtropical points,
    for a live storm whose best track isn't in the archive yet. None if <2."""
    lats, lons, vm, times = [], [], [], []
    for p in feed_storm.get("points", []) or []:
        if p.get("nature") not in FEED_TROPICAL:
            continue
        try:
            la, lo, v = float(p["lat"]), float(p["lon"]), float(p["wind_kt"])
            t = dt.datetime.fromisoformat(str(p["t"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            continue
        lats.append(la); lons.append(lo); vm.append(v); times.append(t)
    if len(lats) < 2:
        return None
    return lats, lons, vm, times


def _write(out_dir, relpath, doc):
    p = os.path.join(out_dir, relpath)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"), allow_nan=False)


def process_active(out_dir):
    """Active storms -> lead-time analogs.json, keyed by canonical sid."""
    n = 0
    for sid, feed in active_storms():
        try:
            tp = feed_target_points(feed)               # authoritative live track
            doc = A.build_analogs_json(
                sid, mode="leadtime", now_iso=_now_iso(), target_points=tp,
                target_name=(feed.get("name") or "").title() or None,
                target_year=feed.get("season") or feed.get("year"))
            if not doc["analogs"]:
                print(f"analogs: {sid} no analogs (skipped write)", file=sys.stderr)
                continue
            _write(out_dir, f"{doc['sid']}/analogs.json", doc)
            top = doc["analogs"][0]
            print(f"analogs: {doc['sid']} -> {doc['count']} (top {top['name']} "
                  f"{top['year']} score={top['score']} {top['confidence']})")
            n += 1
        except Exception as e:  # noqa: BLE001 - per-storm isolation
            print(f"analogs: {sid} failed: {type(e).__name__}: {e}", file=sys.stderr)
    return n


def process_archive(out_dir, sids):
    """Archive storms -> shape analogs.json + a map-ready track.json each; returns
    (index_rows, n) for the archive picker union."""
    rows, n = [], 0
    for sid in sids:
        try:
            doc = A.build_analogs_json(sid, mode="shape", now_iso=_now_iso())
            track = A.build_archive_track(sid)
            csid = doc["sid"]
            _write(out_dir, f"{csid}/analogs.json", doc)
            _write(out_dir, f"archive/{csid}/track.json", track)
            rows.append({"sid": csid, "atcf_id": track["atcf_id"],
                         "name": track["name"], "year": track["season"],
                         "basin": doc["basin"], "peak_cat": track["max_category"],
                         "recon_available": track["recon_available"]})
            print(f"analogs: {csid} -> {doc['count']} analogs + track "
                  f"({len(track['points'])} pts)")
            n += 1
        except Exception as e:  # noqa: BLE001
            print(f"analogs: {sid} failed: {type(e).__name__}: {e}", file=sys.stderr)
    return rows, n


def merge_archive_index(out_dir, rows):
    """Growing-union archive picker index (read prior from CDN, upsert by sid)."""
    union = {}
    try:
        for r in (_get_json(ARCHIVE_INDEX).get("storms") or []):
            if r.get("sid"):
                union[r["sid"]] = r
    except Exception:  # noqa: BLE001 - no prior / unreachable -> start fresh
        pass
    for r in rows:
        union[r["sid"]] = r
    storms = sorted(union.values(),
                    key=lambda r: (-(r.get("year") or 0), r.get("sid") or ""))
    _write(out_dir, "archive/index.json",
           {"schema": "cyclolab-archive/1", "generated_utc": _now_iso(),
            "count": len(storms), "storms": storms})
    return len(storms)


def archive_sids(basin, years):
    """All designated ATCF sids in a basin's archive over the year range."""
    ds = A.load_dataset(basin)
    lo, hi = years
    out = []
    for sid in ds.keys:
        tsid, b = A.normalize_sid(sid)
        if tsid is None or b != basin:
            continue
        try:
            yr = int(ds.get_storm(sid).year)
        except Exception:  # noqa: BLE001
            continue
        if lo <= yr <= hi:
            out.append(sid)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="./analogs_build")
    ap.add_argument("--active", action="store_true")
    ap.add_argument("--storm", nargs="+")
    ap.add_argument("--archive", help="basin code to backfill (AL/EP/CP/WP)")
    ap.add_argument("--years", nargs=2, type=int, help="season range for --archive")
    args = ap.parse_args()
    if (os.environ.get("ANALOGS_ENABLED", "1") or "1").lower() in ("0", "false", "no"):
        print("analogs: ANALOGS_ENABLED=false -> no-op (last-known-good R2 stays live)")
        return
    os.makedirs(args.out_dir, exist_ok=True)
    load_recon_index()

    total, idx_rows = 0, []
    if args.active:
        total += process_active(args.out_dir)
    if args.storm:
        rows, n = process_archive(args.out_dir, args.storm)
        idx_rows += rows
        total += n
    if args.archive:
        floor = A.BASIN_CONFIG[args.archive]["floor"]
        years = tuple(args.years) if args.years else (floor, dt.date.today().year)
        sids = archive_sids(args.archive, years)
        print(f"analogs: archive {args.archive} {years} -> {len(sids)} storms")
        rows, n = process_archive(args.out_dir, sids)
        idx_rows += rows
        total += n
    if idx_rows:
        print(f"analogs: archive index -> {merge_archive_index(args.out_dir, idx_rows)} storms")
    # Sentinel so the workflow's "manifest exists -> sync" guard fires even on a
    # quiet (no active storms) cycle and an empty build still publishes safely.
    _write(args.out_dir, "manifest.json",
           {"schema": "cyclolab-analogs-build/1", "generated_utc": _now_iso(),
            "wrote": total})
    print(f"analogs: wrote {total} storm(s) to {args.out_dir}")


if __name__ == "__main__":
    main()
