"""
NOAA GEFS 0.5 deg ingest (pgrb2ap5) via S3 ``.idx`` byte-range subsetting.

A drop-in FIELD backend for the self-detect pipeline: it exposes the same
``make_client`` / ``iter_member_fields`` that the ECMWF open-data ingest does, so
GEFS rides the IDENTICAL closed-low detector + warm-core filter + currency core
as ECMWF ENS and AIFS-ENS (one clean super-ensemble methodology). It also
provides the cycle COMPLETENESS gate (terminal-step file present).

Why S3 + .idx and not the ECMWF client: GEFS is published as one GRIB2 file per
(member, forecast hour) on the public ``noaa-gefs-pds`` bucket, each with a
sidecar ``.idx`` listing every record's byte offset. We fetch the .idx, then
issue HTTP Range GETs for ONLY the three records we need (PRMSL, HGT 300 mb,
HGT 500 mb) and concatenate them into a tiny multi-message GRIB2 (each GRIB2
message is self-contained, so a concatenation of byte-range slices is itself a
valid GRIB2). Process-and-delete per (member, step): one ~0.6 MB GRIB is resident
at a time. A full 31-member cycle transfers ~2 GB - lighter than ECMWF ENS.

Verified live 2026-06-14: pgrb2ap5 carries PRMSL, HGT:300 mb and HGT:500 mb, so
no pgrb2b pull is needed. HGT is geopotential HEIGHT (gpm), so the warm-core
thickness is HGT300 - HGT500 directly (gh_to_gpm = 1.0, no z/g conversion).
Members: gec00 (control) + gep01..gep30. Steps reach f384 for every cycle hour.
"""
from __future__ import annotations

import os
import time
from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np

from .registry import EnsModelSpec

S3_BASE = "https://noaa-gefs-pds.s3.amazonaws.com"

# The three GRIB records we subset, matched on the ".idx" PARAM:LEVEL columns.
# Order here is the order they are concatenated into the temp GRIB (irrelevant to
# cfgrib, which keys by message metadata).
_WANTED: Tuple[Tuple[str, str], ...] = (
    ("PRMSL", "mean sea level"),
    ("HGT", "300 mb"),
    ("HGT", "500 mb"),
)

_RETRIES = 4
_BACKOFF_S = 4.0
_TIMEOUT = 60.0


def member_s3(member_id: str) -> str:
    """Schema member id -> GEFS S3 member token. ``CTL`` -> ``gec00`` (low-res
    control); ``Pnn`` -> ``gepNN`` (perturbed)."""
    if member_id == "CTL":
        return "gec00"
    return f"gep{int(member_id[1:]):02d}"


def _file_stem(cycle, member_tok: str, step: int) -> str:
    hh = f"{cycle.hour:02d}"
    return (f"gefs.{cycle:%Y%m%d}/{hh}/atmos/pgrb2ap5/"
            f"{member_tok}.t{hh}z.pgrb2a.0p50.f{step:03d}")


def grib_url(cycle, member_tok: str, step: int) -> str:
    return f"{S3_BASE}/{_file_stem(cycle, member_tok, step)}"


def idx_url(cycle, member_tok: str, step: int) -> str:
    return grib_url(cycle, member_tok, step) + ".idx"


def make_client(*_args, **_kwargs):
    """A pooled ``requests.Session`` with a small retry adapter. The ``source`` /
    ``model`` positional args (passed by the shared pipeline) are ignored - GEFS
    has exactly one source (the public S3 bucket)."""
    import requests
    from requests.adapters import HTTPAdapter
    sess = requests.Session()
    try:
        from urllib3.util.retry import Retry
        retry = Retry(total=2, backoff_factor=0.5,
                      status_forcelist=(429, 500, 502, 503, 504))
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    except Exception:  # noqa: BLE001 - urllib3 shape drift; degrade to no-retry adapter
        adapter = HTTPAdapter(pool_connections=8, pool_maxsize=8)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return sess


def _http_bytes(session, url: str, headers: Optional[dict] = None) -> Optional[bytes]:
    """GET ``url`` with bounded retry. Returns content on 200/206, None on a
    definitive 404 or after exhausting retries on transient errors."""
    last = None
    for attempt in range(1, _RETRIES + 1):
        try:
            r = session.get(url, headers=headers or {}, timeout=_TIMEOUT)
            if r.status_code in (200, 206):
                return r.content
            if r.status_code == 404:
                return None
            last = RuntimeError(f"HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001 - transient network
            last = e
        if attempt < _RETRIES:
            time.sleep(_BACKOFF_S * attempt)
    return None


def parse_idx(idx_text: str) -> List[Tuple[int, str, str]]:
    """Parse a GEFS ``.idx`` into ``(start_byte, PARAM, LEVEL)`` rows, in file
    order. Each line is ``recnum:start:d=YYYYMMDDHH:PARAM:LEVEL:...``."""
    rows: List[Tuple[int, str, str]] = []
    for line in idx_text.splitlines():
        parts = line.split(":")
        if len(parts) < 5:
            continue
        try:
            start = int(parts[1])
        except ValueError:
            continue
        rows.append((start, parts[3], parts[4]))
    return rows


def idx_byte_ranges(rows: List[Tuple[int, str, str]]) -> Dict[Tuple[str, str], Tuple[int, Optional[int]]]:
    """Map each wanted ``(PARAM, LEVEL)`` to its ``[start, end)`` byte range. A
    record spans from its start to the NEXT record's start; the final record runs
    to EOF (``end`` is None -> open-ended Range)."""
    starts = sorted({s for s, _, _ in rows})
    nxt = {s: (starts[i + 1] if i + 1 < len(starts) else None)
           for i, s in enumerate(starts)}
    out: Dict[Tuple[str, str], Tuple[int, Optional[int]]] = {}
    for start, param, level in rows:
        key = (param, level)
        if key in _WANTED and key not in out:
            out[key] = (start, nxt[start])
    return out


def _download_subset(session, cycle, member_tok: str, step: int, target: str) -> bool:
    """Fetch the ``.idx``, Range-GET PRMSL + HGT300 + HGT500, and concatenate them
    into ``target``. Returns True iff ALL three records were written (a missing
    field -> skip the step rather than feed the detector a partial decode)."""
    idx = _http_bytes(session, idx_url(cycle, member_tok, step))
    if not idx:
        return False
    rows = parse_idx(idx.decode("utf-8", "ignore"))
    ranges = idx_byte_ranges(rows)
    if len(ranges) < len(_WANTED):
        return False
    gurl = grib_url(cycle, member_tok, step)
    with open(target, "wb") as f:
        for key in _WANTED:
            start, end = ranges[key]
            rng = f"bytes={start}-{end - 1}" if end is not None else f"bytes={start}-"
            chunk = _http_bytes(session, gurl, headers={"Range": rng})
            if not chunk:
                return False
            f.write(chunk)
    return True


def _read_fields(target: str, spec: EnsModelSpec):
    """Open the subset GRIB and return ``(lats, lons, mslp_hPa, thk_gpm | None)``.
    MSLP is converted Pa -> hPa; thickness is HGT[top] - HGT[bottom] (gpm). If the
    gh layer is unavailable the thickness is None (the warm-core filter then passes
    that step's centers through unfiltered - degraded but lossless)."""
    import xarray as xr

    ds = xr.open_dataset(
        target, engine="cfgrib",
        backend_kwargs={"filter_by_keys": {"typeOfLevel": "meanSea"}, "indexpath": ""})
    try:
        if not ds.data_vars:
            return None
        var = ("prmsl" if "prmsl" in ds.data_vars
               else "msl" if "msl" in ds.data_vars else list(ds.data_vars)[0])
        lats = np.asarray(ds["latitude"].values, dtype=float)
        lons = np.asarray(ds["longitude"].values, dtype=float)
        arr = np.asarray(ds[var].values, dtype=float)
    finally:
        ds.close()
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return None
    if finite.max() > 2000.0:           # Pa -> hPa
        arr = arr / 100.0

    thk = None
    try:
        dg = xr.open_dataset(
            target, engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "isobaricInhPa",
                                               "shortName": spec.gh_param},
                            "indexpath": ""})
        try:
            levs = list(np.atleast_1d(dg["isobaricInhPa"].values).astype(int))
            top, bot = int(spec.gh_levels[0]), int(spec.gh_levels[1])
            if top in levs and bot in levs:
                gh = dg[spec.gh_param if spec.gh_param in dg.data_vars else list(dg.data_vars)[0]]
                tg = np.asarray(gh.isel(isobaricInhPa=levs.index(top)).values, dtype=float)
                bg = np.asarray(gh.isel(isobaricInhPa=levs.index(bot)).values, dtype=float)
                d = (tg - bg) * spec.gh_to_gpm
                if np.isfinite(d).any():
                    thk = d
        finally:
            dg.close()
    except Exception:  # noqa: BLE001 - gh missing/undecodable: pass centers unfiltered
        thk = None
    return lats, lons, arr, thk


def iter_member_fields(
    client,
    spec: EnsModelSpec,
    cycle,
    member_id: str,
    steps: List[int],
    tmpdir: str,
) -> Iterator[Tuple[np.ndarray, np.ndarray, int, np.ndarray, Optional[np.ndarray]]]:
    """Yield ``(lats, lons, step_h, MSLP hPa, THK gpm | None)`` one forecast step
    at a time for ``member_id``. Each step is its OWN GEFS file, so we download a
    byte-range subset, decode it, yield, and delete it before the next step - only
    one tiny GRIB is ever on disk. A step whose subset fails to download or decode
    is skipped (not yielded); a step missing only gh yields THK=None."""
    member_tok = member_s3(member_id)
    for step_h in steps:
        target = os.path.join(
            tmpdir, f"{spec.slug}_{cycle:%Y%m%d%H}_{member_id}_f{step_h:03d}.grib2")
        try:
            if not _download_subset(client, cycle, member_tok, step_h, target):
                continue
            fields = _read_fields(target, spec)
            if fields is None:
                continue
            lats, lons, arr, thk = fields
            yield lats, lons, step_h, arr, thk
        finally:
            for p in (target, target + ".idx", target + ".923a8.idx"):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass


# --------------------------------------------------------------------------
# Completeness GATE (the per-model hook the shared currency core calls). A GEFS
# cycle is "complete" iff the TERMINAL forecast-step file is present for BOTH the
# control (gec00) and the last perturbed member (gep30) - a representative
# all-members-disseminated check. File presence only, never wall-clock: a late or
# still-disseminating cycle 404s and is excluded, so a half-written cycle is never
# published. Mirrors enscenters.ingest's gate for the ECMWF backend.
# --------------------------------------------------------------------------
def terminal_present(session, cycle, member_tok: str, step: int,
                     *, timeout: float = 20.0, retries: int = 2) -> bool:
    """HEAD the ``.idx`` for one (member, terminal step). 200 -> present; 404 ->
    definitively absent; transient errors -> not present after retries
    (conservative: the cycle simply retries next run)."""
    url = idx_url(cycle, member_tok, step)
    for attempt in range(retries + 1):
        try:
            code = session.head(url, timeout=timeout).status_code
        except Exception:  # noqa: BLE001 - transient network
            code = None
        if code == 200:
            return True
        if code == 404:
            return False
        if attempt < retries:
            time.sleep(1.0 * (attempt + 1))
    return False


def cycle_complete(spec: EnsModelSpec, cycle, session) -> bool:
    step = spec.steps_for_cycle_hour(cycle.hour)[-1]    # 384 for every cycle hour
    return (terminal_present(session, cycle, member_s3("CTL"), step)
            and terminal_present(session, cycle, member_s3(f"P{spec.n_perturbed:02d}"), step))


def list_complete_cycles(spec: EnsModelSpec, candidates, session) -> List:
    """Filter ``candidates`` (cycle datetimes) to those COMPLETE on S3, ascending."""
    return sorted(c for c in candidates if cycle_complete(spec, c, session))


def resolve_latest_complete(spec: EnsModelSpec, candidates, session):
    """Newest complete cycle among ``candidates`` (or None)."""
    done = list_complete_cycles(spec, candidates, session)
    return done[-1] if done else None
