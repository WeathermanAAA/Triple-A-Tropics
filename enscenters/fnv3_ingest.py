"""
Google DeepMind Weather Lab ensemble TC-track ingest (FNV3 + GenCast).

This is the shared "track_csv" backend for the Weather Lab native-TC products:
FNV3 (download slug "FNV3") and GenCast / "WeatherNext Gen" (slug "GENC"). They
are IDENTICAL except the slug - same endpoint, CSV schema, native Vmax, and
no-detect/no-warmcore treatment - so the model is selected by ``spec.api_model``;
there is ONE parser, not a fork per model.

FNV3/GenCast emit tropical cyclones DIRECTLY (native TC objects), so there is NO self-
detection and NO warm-core filter (unlike the ECMWF/GEFS field models). We pull
the per-cycle "cyclogenesis" CSV (every member's TC tracks, basin-wide) from the
anonymous Weather Lab download endpoint and normalize to the model-agnostic
per-cycle JSON. The model's NATIVE Vmax is used (it carries its own wind; NO
Atkinson-Holliday). 50 members (sample 0..49).

VERIFIED SCHEMA (live 2026-06-14, cyclogenesis CSV; a leading '#' license
preamble precedes the column header):
  sample                              -> member id (float, 0..49)
  lead_time_hours                     -> step_h (int, strictly 6-hourly)
  valid_time                          -> = init + lead (the viewer DERIVES it from
                                         init + step_h, so it is not stored)
  lat, lon
  minimum_sea_level_pressure_hpa      -> mslp_hpa
  maximum_sustained_wind_speed_knots  -> vmax_kt (NATIVE; not an AH estimate)
One CSV per cycle (atomic publish), so "complete" == the CSV is fetchable (404 =
not yet published). GenCast is NOT exposed under this endpoint (404, all name
variants, 2026-06-14); only FNV3 (50) is ingested here. FNV3_LARGE_ENSEMBLE (1000)
and the super-ensemble are deliberately out of scope.

ToU: Weather Lab data < 48 h old is under Google DeepMind's Real-Time
Experimental Data ToU; attribution is required and the product is EXPERIMENTAL,
not for real-world use (see the registry caption).
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import os
import urllib.error
import urllib.request
from typing import List, Optional, Tuple

from .pipeline import CENTER_FIELDS, SCHEMA_VERSION, _cycle_iso, _utcnow_iso
from .registry import EnsModelSpec, pressure_bins_json

# Weather Lab "Scriptable URLs" download (anonymous). The cyclogenesis product is
# every member's basin-wide TC tracks (the paired product is only storms matched
# to observed systems - too sparse for an ensemble-spread view).
BASE_URL = ("https://deepmind.google.com/science/weatherlab/download/cyclones/"
            "{model}/ensemble/{pairing}/csv/"
            "{model}_{y}_{m:02d}_{d:02d}T{h:02d}_00_{pairing}.csv")
DEFAULT_API_MODEL = "FNV3"            # fallback when a spec omits api_model
PAIRING = "cyclogenesis"             # per-member basin-wide tracks (not "paired")
STEP_H = 6
MAX_LEAD_H = 480                      # cap; observed max ~312 h
FNV3_STEPS: List[int] = list(range(0, MAX_LEAD_H + 1, STEP_H))
MAX_CYCLES_BACK = 8
_TIMEOUT = 240.0
_RETRIES = 3
UA = {"User-Agent": "triple-a-tropics.com enscenters (weather hobby site)"}

# CSV column names (verified live).
_COL_SAMPLE = "sample"
_COL_TRACK = "track_id"
_COL_LEAD = "lead_time_hours"
_COL_LAT = "lat"
_COL_LON = "lon"
_COL_MSLP = "minimum_sea_level_pressure_hpa"
_COL_VMAX = "maximum_sustained_wind_speed_knots"


def _api_model(spec: EnsModelSpec) -> str:
    return getattr(spec, "api_model", None) or DEFAULT_API_MODEL


def cycle_url(api_model: str, cycle: dt.datetime) -> str:
    return BASE_URL.format(model=api_model, pairing=PAIRING,
                           y=cycle.year, m=cycle.month, d=cycle.day, h=cycle.hour)


def fetch_cycle_csv(api_model: str, cycle: dt.datetime) -> Optional[str]:
    """CSV text for one cycle, or None if not published yet (404). Bounded retry
    on transient errors; raises only on persistent non-404 failure."""
    url = cycle_url(api_model, cycle)
    last = None
    for attempt in range(1, _RETRIES + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                        timeout=_TIMEOUT) as r:
                return r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last = e
        except Exception as e:  # noqa: BLE001 - transient network
            last = e
    raise RuntimeError(f"FNV3 fetch failed for {cycle:%Y%m%d%H}: {last}")


def cycle_complete(api_model: str, cycle: dt.datetime) -> bool:
    """The cycle's CSV is published (one file, atomic) -> ingestable."""
    return fetch_cycle_csv(api_model, cycle) is not None


def list_complete_cycles(spec: EnsModelSpec, candidates) -> List[dt.datetime]:
    """Filter candidate cycle datetimes to those whose CSV is published, ascending."""
    am = _api_model(spec)
    return sorted(c for c in candidates if cycle_complete(am, c))


def _f(v) -> Optional[float]:
    try:
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def parse_csv(text: str) -> Tuple[list, int, List[int]]:
    """Parse the cyclogenesis CSV into (members_objs, total_centers, run_steps).
    Rows are grouped by ``sample`` (member); each member's centers are
    [step_h, lat, lon, mslp_hpa, vmax_kt] (CENTER_FIELDS order), sorted by step.
    The leading '#' license preamble is skipped."""
    lines = [ln for ln in text.splitlines() if not ln.startswith("#")]
    if not lines:
        return [], 0, [0]
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    by_member: dict = {}
    for row in reader:
        lat, lon = _f(row.get(_COL_LAT)), _f(row.get(_COL_LON))
        lead = _f(row.get(_COL_LEAD))
        smp = _f(row.get(_COL_SAMPLE))
        if lat is None or lon is None or lead is None or smp is None:
            continue
        step = int(round(lead))
        if step < 0 or step > MAX_LEAD_H or (step % STEP_H):
            continue
        mslp = _f(row.get(_COL_MSLP))
        vmax = _f(row.get(_COL_VMAX))
        center = [step, round(lat, 2), round(lon, 2),
                  None if mslp is None else round(mslp, 1),
                  None if vmax is None else round(vmax, 1)]
        by_member.setdefault(int(smp), []).append(center)

    members_objs, total = [], 0
    for smp in sorted(by_member):
        centers = sorted(by_member[smp], key=lambda c: c[0])
        total += len(centers)
        # deepest center with a real Pmin (else the first center) for the peak row
        withp = [c for c in centers if c[3] is not None]
        peak_c = min(withp, key=lambda c: c[3]) if withp else centers[0]
        mid = f"M{smp:02d}"
        members_objs.append({
            "id": mid, "label": f"Member {smp}",
            "peak": {"mslp_hpa": peak_c[3], "vmax_kt": peak_c[4],
                     "lat": peak_c[1], "lon": peak_c[2], "step_h": peak_c[0]},
            "n_centers": len(centers), "centers": centers,
        })
    max_step = max((c[0] for m in members_objs for c in m["centers"]), default=0)
    run_steps = [s for s in FNV3_STEPS if s <= max_step] or [0]
    return members_objs, total, run_steps


def member_tracks_from_csv(text: str):
    """Per-member NATIVE tracks for the tracking keystone (Stage A is SKIPPED for
    native models). The cyclogenesis CSV carries a ``track_id``, so a member
    (sample) is already split into distinct storms: returns ``{member_id: [track,
    ...]}`` where each track is a step-sorted list of ``[step, lat, lon, mslp,
    vmax]``. This is the in-memory hand-off the lean centers JSON cannot carry (it
    drops track_id to stay a flat per-step list) - NO re-fetch, centers untouched."""
    lines = [ln for ln in text.splitlines() if not ln.startswith("#")]
    if not lines:
        return {}
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    by_key: dict = {}
    for row in reader:
        lat, lon = _f(row.get(_COL_LAT)), _f(row.get(_COL_LON))
        lead = _f(row.get(_COL_LEAD))
        smp = _f(row.get(_COL_SAMPLE))
        if lat is None or lon is None or lead is None or smp is None:
            continue
        step = int(round(lead))
        if step < 0 or step > MAX_LEAD_H or (step % STEP_H):
            continue
        tid = row.get(_COL_TRACK)
        mid = f"M{int(smp):02d}"
        mslp, vmax = _f(row.get(_COL_MSLP)), _f(row.get(_COL_VMAX))
        center = [step, round(lat, 2), round(lon, 2),
                  None if mslp is None else round(mslp, 1),
                  None if vmax is None else round(vmax, 1)]
        by_key.setdefault((mid, str(tid)), []).append(center)
    out: dict = {}
    for (mid, _tid), centers in by_key.items():
        out.setdefault(mid, []).append(sorted(centers, key=lambda c: c[0]))
    return out


def build_cycle(spec: EnsModelSpec, cycle: dt.datetime, out_dir: str,
                *, progress=print, **_ignored) -> dict:
    """The FNV3 ``ingest_cycle`` hook: fetch + parse the cyclogenesis CSV for one
    cycle and write the model-agnostic per-cycle JSON (same schema as the other
    models). Raises if the cycle CSV is not published yet, so the currency core
    skips it and retries."""
    am = _api_model(spec)
    progress(f"[{spec.slug}] cycle {cycle:%Y-%m-%d %HZ}: {cycle_url(am, cycle)}")
    text = fetch_cycle_csv(am, cycle)
    if text is None:
        raise RuntimeError(f"{spec.slug} CSV not published for {cycle:%Y%m%d%H}")
    members_objs, total, run_steps = parse_csv(text)
    # NATIVE per-(sample, track_id) tracks for the tracking keystone (Stage A is
    # skipped for native models) - handed to the tracks step in-memory so the lean
    # centers JSON stays untouched and the CSV is not re-fetched.
    native_tracks = member_tracks_from_csv(text)

    stamp = f"{cycle:%Y%m%d%H}"
    data = {
        "schema_version": SCHEMA_VERSION,
        "model": spec.slug,
        "model_label": spec.label,
        "init_time": _cycle_iso(cycle),
        "init_cycle": stamp,
        "cycle_hour": cycle.hour,
        "generated_at": _utcnow_iso(),
        "attribution": spec.attribution,
        "grid": spec.grid_label,
        "run_steps": run_steps,
        "n_members": len(members_objs),
        "n_centers": total,
        "source": "track_csv",            # native TC tracks (vs field "detect")
        "center_fields": CENTER_FIELDS,
        "pressure_bins": pressure_bins_json(),
        "caption": spec.caption,          # model-aware viewer caption (source + disclaimer)
        "members": members_objs,
    }
    model_dir = os.path.join(out_dir, spec.slug)
    os.makedirs(model_dir, exist_ok=True)
    cycle_path = os.path.join(model_dir, f"{stamp}.json")
    with open(cycle_path, "w") as fh:
        json.dump(data, fh, separators=(",", ":"))
    bytes_json = os.path.getsize(cycle_path)
    progress(f"[{spec.slug}] wrote {cycle_path} ({bytes_json/1e6:.2f} MB), "
             f"{len(members_objs)} members, {total} centers, run_steps "
             f"{run_steps[0]}..{run_steps[-1]}")
    return {"cycle": stamp, "generated_at": data["generated_at"],
            "members": len(members_objs), "n_centers": total,
            "bytes_json": bytes_json, "cycle_path": cycle_path, "failures": [],
            "native_member_tracks": native_tracks}
