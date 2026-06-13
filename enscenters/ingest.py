"""
ECMWF open-data ingest for the Ensemble Cyclone Centers platform.

The ONLY module that touches the network / GRIB. It downloads MSLP one member at
a time (peak disk ~ one member's GRIB), then yields that member's fields ONE
forecast step at a time so only a single 2-D field is resident at once (the
caller detects per step and discards). A full 51-member 00/12Z cycle transfers
~2.8 GB, comfortably inside a GitHub Actions runner.

Verified against live open-data (2026-06-13):
  - perturbed members: stream="enfo", type="pf", number=1..50
  - control (post IFS-50r1): stream="oper", type="fc" (NO number; identical to
    HRES). The old "enfo"/"cf" control was retired 2026-05-12; requesting
    type="cf" on a current cycle matches zero index records.
  - grid 0.25 deg, lon -180..179.75, lat -90..90, msl in Pa (paramId 151),
    typeOfLevel "meanSea" (NOT "surface").
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from typing import Iterator, List, Optional, Tuple

import numpy as np

from .registry import EnsModelSpec

_RETRIES = 4
_BACKOFF_S = 5.0


def make_client(source: str = "ecmwf"):
    """Build an ecmwf-opendata Client. ``source`` can be ecmwf|aws|azure|google."""
    from ecmwf.opendata import Client
    return Client(source=source)


def resolve_latest_complete(client, spec: EnsModelSpec):
    """Return the newest cycle (datetime) whose ENS MSLP is fully published.

    A 00/12Z cycle is complete when its terminal step (360h) is on the index; a
    06/18Z cycle when 144h is. ``client.latest`` checks the index (HEAD) without
    downloading, and returns a NAIVE datetime. Returns None if nothing is
    complete yet. NOTE: this checks only the terminal-step file is present; the
    real defense against an under-populated cycle is the member quorum in
    ``pipeline.build_cycle``.
    """
    base = dict(stream=spec.ens_stream, type=spec.pf_type, param=spec.param)
    cands = []
    try:
        c_long = client.latest(step=spec.steps_long[-1], **base)
        if c_long is not None:
            cands.append(c_long)
    except Exception:
        pass
    try:
        c_short = client.latest(step=spec.steps_short[-1], **base)
        if c_short is not None and c_short.hour in (6, 18):  # 06/18Z terminal is 144h
            cands.append(c_short)
    except Exception:
        pass
    return max(cands) if cands else None


def _retrieve(client, target: str, **request) -> None:
    """client.retrieve with bounded retry/backoff (open-data 5xx / portal cap)."""
    last = None
    for attempt in range(1, _RETRIES + 1):
        try:
            if os.path.exists(target):
                os.remove(target)
            client.retrieve(target=target, **request)
            return
        except Exception as e:  # noqa: BLE001 - transient network/portal errors
            last = e
            if attempt < _RETRIES:
                time.sleep(_BACKOFF_S * attempt)
    raise RuntimeError(f"retrieve failed after {_RETRIES} attempts: {request}") from last


def _request_for_member(spec: EnsModelSpec, member_id: str, steps: List[int]) -> dict:
    if member_id == "CTL":
        return dict(stream=spec.control_stream, type=spec.control_type,
                    param=spec.param, step=steps)
    number = int(member_id[1:])
    return dict(stream=spec.ens_stream, type=spec.pf_type, number=number,
                param=spec.param, step=steps)


def _data_type_for_member(spec: EnsModelSpec, member_id: str) -> str:
    return spec.control_type if member_id == "CTL" else spec.pf_type


def iter_member_fields(
    client,
    spec: EnsModelSpec,
    cycle,
    member_id: str,
    steps: List[int],
    tmpdir: str,
) -> Iterator[Tuple[np.ndarray, np.ndarray, int, np.ndarray]]:
    """Download one member's MSLP, then yield (lats, lons, step_h, hPa 2-D field)
    one step at a time. Only a single field is resident at once. The temp GRIB
    (+ any .idx) is removed when the generator is exhausted or closed.

    The control member may publish fewer steps than the perturbed members
    (oper/fc stops at 240h at 00/12Z, 90h at 06/18Z); only the steps actually
    present are yielded. Filter by paramId + dataType only - msl's typeOfLevel is
    "meanSea" (constraining typeOfLevel="surface" matches zero messages).
    """
    import xarray as xr

    target = os.path.join(tmpdir, f"{spec.slug}_{cycle:%Y%m%d%H}_{member_id}.grib2")
    request = _request_for_member(spec, member_id, steps)
    request["date"] = cycle.strftime("%Y%m%d")
    request["time"] = cycle.hour

    try:
        _retrieve(client, target, **request)
        ds = xr.open_dataset(
            target,
            engine="cfgrib",
            backend_kwargs={
                "filter_by_keys": {
                    "paramId": spec.param_id,
                    "dataType": _data_type_for_member(spec, member_id),
                },
                "indexpath": "",
            },
        )
        try:
            if not ds.data_vars:
                raise RuntimeError(f"no MSLP messages decoded for member {member_id} of {cycle}")
            lats = np.asarray(ds["latitude"].values, dtype=float)
            lons = np.asarray(ds["longitude"].values, dtype=float)
            var = "msl" if "msl" in ds.data_vars else list(ds.data_vars)[0]
            step_vals = np.atleast_1d(ds["step"].values)
            has_step_dim = "step" in ds[var].dims
            for s in range(len(step_vals)):
                sv = step_vals[s]
                step_h = (int(sv / np.timedelta64(1, "h"))
                          if np.issubdtype(np.asarray(sv).dtype, np.timedelta64)
                          else int(sv))
                da = ds[var].isel(step=s) if has_step_dim else ds[var]
                arr = np.asarray(da.values, dtype=float)
                finite = arr[np.isfinite(arr)]
                if finite.size == 0:
                    continue  # all-NaN step -> skip (failed decode for this step)
                if finite.max() > 2000.0:  # Pa -> hPa
                    arr = arr / 100.0
                yield lats, lons, step_h, arr
        finally:
            ds.close()
    finally:
        for p in (target, target + ".idx"):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass


class IngestSession:
    """Owns a tempdir for one cycle's ingest. Context-managed."""

    def __init__(self, spec: EnsModelSpec):
        self.spec = spec
        self._tmp: Optional[str] = None

    def __enter__(self) -> "IngestSession":
        self._tmp = tempfile.mkdtemp(prefix="enscenters_")
        return self

    def __exit__(self, *exc) -> None:
        if self._tmp and os.path.isdir(self._tmp):
            shutil.rmtree(self._tmp, ignore_errors=True)

    @property
    def tmpdir(self) -> str:
        return self._tmp
