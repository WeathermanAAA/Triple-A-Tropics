"""
GEFS genesis-track ingest for the Ensemble Cyclone Centers platform.

UNLIKE ECMWF ENS / AIFS-ENS (which self-detect closed lows from MSLP fields and
then warm-core filter), GEFS uses NOAA's ensemble GENESIS TRACKER product
(atcf_gen) - the cyclones are ALREADY detected and warm-core / TC filtered by
NOAA. So this is a LIGHT path: fetch the small per-member ATCF text files for one
cycle, parse the genesis tracks, and map them straight into the model-agnostic
JSON. No GRIB, no field detection, no warmcore.py, no heavy compute. vmax is the
model's OWN maximum wind from the ATCF (NOT an Atkinson-Holliday estimate).

REAL NCO LAYOUT (verified live 2026-06-14):
  ens_tracker/prod/gefs.YYYYMMDD/CC/genesis/storms.{member}.atcf_gen.altg.YYYYMMDDCC
where {member} is one file PER member: ac00 (control) + ap01..ap30 (31 files).
There is NO single combined file. The genesis "altg" ATCF carries an extra
storm-id column vs plain atcfunix, so the data columns are shifted by one:
  [0]=tag [1]=cand# [2]=stormid [3]=init [4]=technum [5]=tech(member) [6]=tau
  [7]=lat [8]=lon [9]=vmax(kt) [10]=mslp(mb) ...
The member is taken from the FILENAME (each file is single-member).

Hosts: nomads.ncep.noaa.gov serves the autoindex + files reliably and is tried
FIRST; ftpprd.ncep.noaa.gov is a fallback only (it is frequently unreachable from
cloud runners, and putting it first made every probe eat its connect timeout).
"""
from __future__ import annotations

import datetime as dt
import math
import re
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

from .pipeline import CENTER_FIELDS, SCHEMA_VERSION, _cycle_iso, _utcnow_iso
from .registry import EnsModelSpec, member_label, pressure_bins_json

# NCO HTTP roots. nomads FIRST (reachable + fast); ftpprd as a fallback only.
NCO_BASES = (
    "https://nomads.ncep.noaa.gov/pub/data/nccf/com",
    "https://ftpprd.ncep.noaa.gov/data/nccf/com",
)
# Per-cycle genesis directory, relative to a base. {d}=YYYYMMDD, {cc}=CC.
GENESIS_DIR_TMPL = "ens_tracker/prod/gefs.{d}/{cc}/genesis/"
# One ATCF file per member: ac00 (control) + ap01..ap30.
GEFS_MEMBER_FILES: Tuple[str, ...] = ("ac00",) + tuple(f"ap{n:02d}" for n in range(1, 31))
GENESIS_FILE_TMPL = "storms.{member}.atcf_gen.altg.{stamp}"
# The cycle is "complete" once the LAST member's file (ap30) has posted; we HEAD
# just that one in the gate (the members are written together near job end).
GATE_MEMBER = "ap30"
# Fraction of the 31 member files that must fetch before we publish (else the run
# is mid-dissemination -> raise and retry next cron). Quiet members still have a
# file; this guards against a half-written directory, not against low activity.
MIN_MEMBER_FILES_FRAC = 0.6

# 6-hourly to 384 h - the GEFS genesis-tracker horizon. A genesis candidate can
# first appear at any 6-hourly step, so we keep every step on the grid.
GEFS_STEPS: List[int] = list(range(0, 385, 6))
_STEP_SET = set(GEFS_STEPS)

_UA = {"User-Agent": "triple-a-tropics-enscenters/1.0"}
_GET_TIMEOUT = 15.0
_HEAD_TIMEOUT = 8.0
# Cache the working genesis-dir URL per cycle so the gate + ingest don't re-probe.
_DIR_CACHE: Dict[str, Optional[str]] = {}


def _get(url: str, timeout: float = _GET_TIMEOUT) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:  # noqa: BLE001 - HTTP / network: treat as absent for this host
        return None


def _head_ok(url: str, timeout: float = _HEAD_TIMEOUT) -> bool:
    try:
        req = urllib.request.Request(url, headers=_UA, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= getattr(r, "status", 200) < 300
    except Exception:  # noqa: BLE001
        return False


def genesis_dir(cycle: dt.datetime) -> Optional[str]:
    """The base URL of the genesis directory for ``cycle`` on the first reachable
    NCO host whose terminal member file (ap30) is present, or None if not posted
    on any host yet. Memoized per cycle (the gate + ingest share the result)."""
    key = f"{cycle:%Y%m%d%H}"
    if key in _DIR_CACHE:
        return _DIR_CACHE[key]
    d, cc, stamp = cycle.strftime("%Y%m%d"), cycle.strftime("%H"), key
    found = None
    for base in NCO_BASES:
        diru = f"{base}/{GENESIS_DIR_TMPL.format(d=d, cc=cc)}"
        gate = diru + GENESIS_FILE_TMPL.format(member=GATE_MEMBER, stamp=stamp)
        if _head_ok(gate):
            found = diru
            break
    _DIR_CACHE[key] = found
    return found


# --- ATCF genesis ("altg") parsing ---------------------------------------
def _parse_latlon(field: str) -> Optional[float]:
    """ATCF lat/lon in tenths of a degree with a hemisphere letter: '268N'->26.8,
    '0600W'->-60.0, '1199E'->119.9. Longitudes normalized to [-180, 180)."""
    field = field.strip().upper()
    if len(field) < 2 or field[-1] not in "NSEW":
        return None
    try:
        val = int(field[:-1]) / 10.0
    except ValueError:
        return None
    hemi = field[-1]
    if hemi in "SW":
        val = -val
    if hemi in "EW":
        val = ((val + 180.0) % 360.0) - 180.0
    return round(val, 2)


def parse_member_genesis(text: str) -> List[list]:
    """Parse ONE member's genesis ATCF (altg) text into a list of centers
    [step_h, lat, lon, mslp_hpa, vmax_kt] (the model-agnostic schema). The altg
    format carries an extra storm-id column, so the data columns are: [6]=tau,
    [7]=lat, [8]=lon, [9]=vmax(kt), [10]=mslp(mb). Rows with bad coords, a
    non-positive pressure, or an off-grid step are skipped; identical (step, lat,
    lon) points (the same system tracked under more than one candidate id) are
    de-duped."""
    centers: List[list] = []
    seen = set()
    for line in text.splitlines():
        f = [c.strip() for c in line.split(",")]
        if len(f) < 11:
            continue
        try:
            step_h = int(f[6])
        except ValueError:
            continue
        if step_h not in _STEP_SET:
            continue
        lat, lon = _parse_latlon(f[7]), _parse_latlon(f[8])
        if lat is None or lon is None:
            continue
        try:
            vmax = float(int(f[9]))
        except ValueError:
            vmax = 0.0
        try:
            mslp = float(int(f[10]))
        except ValueError:
            continue
        if mslp <= 0:
            continue
        key = (step_h, round(lat, 1), round(lon, 1))
        if key in seen:
            continue
        seen.add(key)
        centers.append([step_h, round(lat, 2), round(lon, 2), round(mslp, 1), round(vmax, 1)])
    return centers


def _member_id(fname_member: str) -> str:
    """Map a filename member token to our member id: ac00 -> CTL, apNN -> PNN."""
    return "CTL" if fname_member == "ac00" else "P" + fname_member[2:]


def build_gefs_cycle(spec: EnsModelSpec, cycle: dt.datetime, out_dir: str,
                     *, progress=print, **_ignored) -> dict:
    """The GEFS ``ingest_cycle`` hook: fetch + parse the per-member genesis ATCF
    files for one cycle and write the model-agnostic per-cycle JSON (same schema
    as the field models). Raises if the directory isn't posted yet or too few of
    the 31 member files fetch (mid-dissemination), so the currency core skips it
    and retries; a complete-but-quiet cycle (files present, few/no candidates)
    publishes an empty-but-valid cycle so GEFS still appears in the selector."""
    import json
    import os

    diru = genesis_dir(cycle)
    if not diru:
        raise RuntimeError(f"GEFS genesis dir not posted for {cycle:%Y%m%d%H}")
    progress(f"[gefs] cycle {cycle:%Y-%m-%d %HZ}: {diru}")

    stamp = f"{cycle:%Y%m%d%H}"
    members_objs, total, fetched = [], 0, 0
    for fm in GEFS_MEMBER_FILES:                       # ac00, ap01..ap30 (canonical order)
        url = diru + GENESIS_FILE_TMPL.format(member=fm, stamp=stamp)
        raw = _get(url)
        if raw is None:
            continue                                   # missing member file: count toward quorum
        fetched += 1
        centers = parse_member_genesis(raw.decode("latin-1", "ignore"))
        if not centers:
            continue
        centers.sort(key=lambda c: c[0])
        total += len(centers)
        mid = _member_id(fm)
        peak = min(centers, key=lambda c: c[3])        # deepest center
        members_objs.append({
            "id": mid, "label": member_label(mid),
            "peak": {"mslp_hpa": peak[3], "vmax_kt": peak[4],
                     "lat": peak[1], "lon": peak[2], "step_h": peak[0]},
            "n_centers": len(centers), "centers": centers,
        })

    need = max(1, math.ceil(MIN_MEMBER_FILES_FRAC * len(GEFS_MEMBER_FILES)))
    if fetched < need:
        raise RuntimeError(
            f"GEFS {stamp}: only {fetched}/{len(GEFS_MEMBER_FILES)} member files "
            f"fetched (< quorum {need}); dir mid-dissemination, retry next run")

    data = {
        "schema_version": SCHEMA_VERSION,
        "model": spec.slug,
        "model_label": spec.label,
        "init_time": _cycle_iso(cycle),
        "init_cycle": stamp,
        "cycle_hour": cycle.hour,
        "generated_at": _utcnow_iso(),
        "attribution": spec.attribution,
        "grid": "n/a (genesis tracks)",
        "run_steps": list(GEFS_STEPS),
        "n_members": len(members_objs),
        "n_centers": total,
        "source": "genesis_tracks",                    # vs the field models' "detect"
        "center_fields": CENTER_FIELDS,
        "pressure_bins": pressure_bins_json(),
        "caption": spec.caption,                        # model-aware viewer caption
        "members": members_objs,
    }
    model_dir = os.path.join(out_dir, spec.slug)
    os.makedirs(model_dir, exist_ok=True)
    cycle_path = os.path.join(model_dir, f"{stamp}.json")
    with open(cycle_path, "w") as fh:
        json.dump(data, fh, separators=(",", ":"))
    bytes_json = os.path.getsize(cycle_path)
    progress(f"[gefs] wrote {cycle_path} ({bytes_json/1e6:.2f} MB), "
             f"{fetched}/{len(GEFS_MEMBER_FILES)} files, "
             f"{len(members_objs)} members w/ tracks, {total} centers")
    return {"cycle": stamp, "generated_at": data["generated_at"],
            "members": len(members_objs), "n_centers": total,
            "bytes_json": bytes_json, "cycle_path": cycle_path, "failures": []}


def gefs_complete(spec: EnsModelSpec, cycle: dt.datetime) -> bool:
    """Completeness gate: the genesis directory (terminal member file) is posted."""
    return genesis_dir(cycle) is not None


def list_complete_cycles(spec: EnsModelSpec, candidates) -> List[dt.datetime]:
    """Filter candidate cycle datetimes to those whose GEFS genesis dir is posted."""
    return sorted(c for c in candidates if gefs_complete(spec, c))
