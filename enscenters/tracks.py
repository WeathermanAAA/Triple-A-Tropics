"""
GEFS genesis-track ingest for the Ensemble Cyclone Centers platform.

UNLIKE ECMWF ENS / AIFS-ENS (which self-detect closed lows from MSLP fields and
then warm-core filter), GEFS uses NOAA's ensemble GENESIS TRACKER product
(atcf_gen) - the cyclones are ALREADY detected and warm-core / TC filtered by
NOAA. So this is a LIGHT path: fetch one small ATCF text file per cycle, parse
the per-member genesis tracks, and map them straight into the model-agnostic
JSON. No GRIB, no field detection, no warmcore.py, no heavy compute - it runs in
minutes. vmax is the model's OWN maximum wind from the ATCF (NOT an
Atkinson-Holliday estimate, which we only use when deriving wind from pressure).

The genesis tracker file lives under (NCO production):
  ens_tracker/prod/gefs.YYYYMMDD/CC/   and/or
  gens/prod/gefs.YYYYMMDD/CC/genesis/
on ftpprd.ncep.noaa.gov (primary, allows directory browsing) and
nomads.ncep.noaa.gov (fallback). Members are AC00 (control) + AP01..AP30.
"""
from __future__ import annotations

import datetime as dt
import gzip
import re
import urllib.error
import urllib.request
from typing import List, Optional

from .pipeline import CENTER_FIELDS, SCHEMA_VERSION, _cycle_iso, _utcnow_iso
from .registry import EnsModelSpec, member_label, pressure_bins_json

# NCO HTTP roots, primary first. ftpprd serves browsable autoindex; nomads is the
# fallback (it 403s directory listing but serves files by exact name).
NCO_BASES = (
    "https://ftpprd.ncep.noaa.gov/data/nccf/com",
    "https://nomads.ncep.noaa.gov/pub/data/nccf/com",
)
# Per-cycle directory templates (relative to a base). {d}=YYYYMMDD, {cc}=CC.
GEFS_DIR_TEMPLATES = (
    "ens_tracker/prod/gefs.{d}/{cc}",
    "gens/prod/gefs.{d}/{cc}/genesis",
)
# A genesis file in a listing: NCO names it like
# storms.gefso.atcf_gen.altg.YYYYMMDDCC . Match anything carrying atcf_gen.
_GENESIS_HREF_RE = re.compile(r'href="([^"?][^"]*atcf_gen[^"]*)"', re.I)
# Known direct filenames to try when the directory listing is forbidden (nomads).
_GENESIS_FILENAMES = (
    "storms.gefso.atcf_gen.altg.{stamp}",
    "trak.gefso.atcf_gen.altg.{stamp}",
    "gefso.atcf_gen.altg.{stamp}",
)

# 6-hourly to 384 h - the GEFS genesis-tracker horizon.
GEFS_STEPS: List[int] = list(range(0, 385, 6))
_STEP_SET = set(GEFS_STEPS)

_UA = {"User-Agent": "triple-a-tropics-enscenters/1.0"}


def _get(url: str, timeout: float = 30.0) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError:
        return None
    except Exception:  # noqa: BLE001 - network
        return None


def _head_ok(url: str, timeout: float = 15.0) -> bool:
    try:
        req = urllib.request.Request(url, headers=_UA, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def find_genesis_url(cycle: dt.datetime) -> Optional[str]:
    """Locate the GEFS genesis ATCF file for ``cycle`` across the NCO hosts/dirs:
    prefer a directory listing (match an ``atcf_gen`` file), then fall back to the
    known NCO filenames by direct HEAD. Returns the file URL or None."""
    d, cc = cycle.strftime("%Y%m%d"), cycle.strftime("%H")
    stamp = cycle.strftime("%Y%m%d%H")
    for base in NCO_BASES:
        for tmpl in GEFS_DIR_TEMPLATES:
            diru = f"{base}/{tmpl.format(d=d, cc=cc)}/"
            html = _get(diru)
            if html:
                for href in _GENESIS_HREF_RE.findall(html.decode("latin-1", "ignore")):
                    return diru + href.split("/")[-1]
            for fn in _GENESIS_FILENAMES:
                for cand in (fn.format(stamp=stamp), fn.format(stamp=stamp) + ".gz"):
                    if _head_ok(diru + cand):
                        return diru + cand
    return None


def fetch_genesis_text(url: str) -> Optional[str]:
    """GET the genesis file, transparently gunzip if needed."""
    raw = _get(url)
    if raw is None:
        return None
    if url.endswith(".gz") or raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except OSError:
            return None
    return raw.decode("latin-1", "ignore")


# --- ATCF parsing --------------------------------------------------------
_MEMBER_RE = re.compile(r"^A([CP])(\d{2})$")   # AC00 (control) / AP01..AP30


def _member_id(tech: str) -> Optional[str]:
    """Map an ATCF tech id to our member id. AC00 -> CTL, APnn -> Pnn. Anything
    else (means/spreads AEMN/AEAR/..., other models) -> None (skip)."""
    m = _MEMBER_RE.match(tech.strip().upper())
    if not m:
        return None
    kind, num = m.group(1), m.group(2)
    if kind == "C":
        return "CTL"
    return "P" + num   # AP01 -> P01


def _parse_latlon(field: str) -> Optional[float]:
    """ATCF lat/lon in tenths of a degree with a hemisphere letter: '250N'->25.0,
    '0700W'->-70.0. Longitudes normalized to [-180, 180)."""
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


def parse_atcf_genesis(text: str) -> dict:
    """Parse a GEFS genesis ATCF (atcfunix) file into {member_id: [center,...]}.
    A center is [step_h, lat, lon, mslp_hpa, vmax_kt] (the model-agnostic schema).
    Standard atcfunix columns: [2]=init, [4]=tech/member, [5]=tau, [6]=lat,
    [7]=lon, [8]=vmax(kt), [9]=mslp(mb). Lines with no member, bad coords, or a
    non-positive pressure are skipped. De-dups identical (member, step, lat, lon)
    rows that the tracker can emit for a system tracked under multiple ids."""
    members: dict[str, list] = {}
    seen: dict[str, set] = {}
    for line in text.splitlines():
        f = [c.strip() for c in line.split(",")]
        if len(f) < 10:
            continue
        mid = _member_id(f[4])
        if mid is None:
            continue
        try:
            step_h = int(f[5])
        except ValueError:
            continue
        lat, lon = _parse_latlon(f[6]), _parse_latlon(f[7])
        if lat is None or lon is None:
            continue
        try:
            vmax = float(int(f[8]))
        except ValueError:
            vmax = 0.0
        try:
            mslp = float(int(f[9]))
        except ValueError:
            continue
        if mslp <= 0 or step_h not in _STEP_SET:
            continue
        key = (step_h, round(lat, 1), round(lon, 1))
        s = seen.setdefault(mid, set())
        if key in s:
            continue
        s.add(key)
        members.setdefault(mid, []).append(
            [step_h, round(lat, 2), round(lon, 2), round(mslp, 1), round(vmax, 1)])
    return members


def build_gefs_cycle(spec: EnsModelSpec, cycle: dt.datetime, out_dir: str,
                     *, progress=print, **_ignored) -> dict:
    """The GEFS ``ingest_cycle`` hook: fetch + parse the genesis ATCF for one
    cycle and write the model-agnostic per-cycle JSON (same schema as the
    field models). Raises if the genesis file can't be fetched/parsed (so the
    currency core skips it and retries), mirroring build_one_cycle's contract."""
    import json
    import os

    url = find_genesis_url(cycle)
    if not url:
        raise RuntimeError(f"GEFS genesis file not found for {cycle:%Y%m%d%H}")
    progress(f"[gefs] cycle {cycle:%Y-%m-%d %HZ}: {url}")
    text = fetch_genesis_text(url)
    if not text:
        raise RuntimeError(f"GEFS genesis fetch/parse failed for {url}")

    by_member = parse_atcf_genesis(text)
    if not by_member:
        raise RuntimeError(f"GEFS genesis parsed 0 members for {cycle:%Y%m%d%H}")

    members_objs, total = [], 0
    for mid in spec.member_ids():                  # canonical order (CTL, P01..P30)
        centers = by_member.get(mid)
        if not centers:
            continue
        centers.sort(key=lambda c: c[0])
        total += len(centers)
        peak = min(centers, key=lambda c: c[3])    # deepest center
        members_objs.append({
            "id": mid, "label": member_label(mid),
            "peak": {"mslp_hpa": peak[3], "vmax_kt": peak[4],
                     "lat": peak[1], "lon": peak[2], "step_h": peak[0]},
            "n_centers": len(centers), "centers": centers,
        })

    data = {
        "schema_version": SCHEMA_VERSION,
        "model": spec.slug,
        "model_label": spec.label,
        "init_time": _cycle_iso(cycle),
        "init_cycle": f"{cycle:%Y%m%d%H}",
        "cycle_hour": cycle.hour,
        "generated_at": _utcnow_iso(),
        "attribution": spec.attribution,
        "grid": "n/a (genesis tracks)",
        "run_steps": list(GEFS_STEPS),
        "n_members": len(members_objs),
        "n_centers": total,
        "source": "genesis_tracks",                 # vs the field models' "detect"
        "center_fields": CENTER_FIELDS,
        "pressure_bins": pressure_bins_json(),
        "caption": spec.caption,                     # model-aware viewer caption
        "members": members_objs,
    }
    model_dir = os.path.join(out_dir, spec.slug)
    os.makedirs(model_dir, exist_ok=True)
    cyc_str = f"{cycle:%Y%m%d%H}"
    cycle_path = os.path.join(model_dir, f"{cyc_str}.json")
    with open(cycle_path, "w") as fh:
        json.dump(data, fh, separators=(",", ":"))
    bytes_json = os.path.getsize(cycle_path)
    progress(f"[gefs] wrote {cycle_path} ({bytes_json/1e6:.2f} MB), "
             f"{len(members_objs)} members, {total} centers")
    return {"cycle": cyc_str, "generated_at": data["generated_at"],
            "members": len(members_objs), "n_centers": total,
            "bytes_json": bytes_json, "cycle_path": cycle_path, "failures": []}


def gefs_complete(spec: EnsModelSpec, cycle: dt.datetime) -> bool:
    """Completeness gate: the genesis file for ``cycle`` is fetchable."""
    return find_genesis_url(cycle) is not None


def list_complete_cycles(spec: EnsModelSpec, candidates) -> List[dt.datetime]:
    """Filter candidate cycle datetimes to those whose GEFS genesis file exists."""
    return sorted(c for c in candidates if gefs_complete(spec, c))
