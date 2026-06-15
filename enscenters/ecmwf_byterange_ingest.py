"""
ECMWF ENS / AIFS-ENS ingest via direct ``.index`` byte-range, MULTI-HOMED across
the Google and AWS mirrors (prefer the fast US mirror, never depend on only one).

WHY: the ecmwf-opendata client's mirrors are in Europe; a single range GET from
US GitHub Actions measured ~860 ms x ~13k GETs/cycle = an hours-long crawl that
hangs the runs. The Google mirror is US-reachable (~130 ms/GET, 6.6x faster) but
Google Cloud Storage REJECTS multi-range requests, so we issue our OWN SINGLE-
range concurrent GETs. Both mirrors carry ECMWF open data under the IDENTICAL path
+ ``.index`` (JSON-Lines) scheme, so the exact same single-range fetch works
against either - only the base URL changes. We therefore prefer GCS and fall back
to AWS per request, with a sticky per-process circuit-breaker so a full GCS outage
demotes it after K failures instead of eating thousands of slow timeouts.

File layout (verified live 2026-06-14): one GRIB2 per (stream, type, step), all
members inside, with a sibling ``.index``. Members are keyed by the index
``number``/``type``, NOT the filename. Perturbed = (enfo, pf, number 1..50).
Control = a SEPARATE file: ifs ENS -> (oper, fc); AIFS -> (enfo, cf); single
record, no number. Geopotential is ``gh`` (gpm) for ifs, ``z`` (m^2/s^2) for AIFS
-> the spec's ``gh_param`` / ``gh_to_gpm`` (1/g) convert both to thickness in gpm.

GEFS (NOAA S3) is a separate path (enscenters.gefs_ingest) and is untouched.
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlparse

import numpy as np

from .registry import EnsModelSpec

# Ordered mirrors: prefer the fast US-reachable GCS, fall back to AWS (eu-central-1,
# slower from US but reliable). Same path + .index scheme on both; only base differs.
# Bases are env-overridable (ops can repoint a mirror; also used to simulate an
# outage in verification, e.g. ENSCENTERS_GCS_BASE=http://127.0.0.1:9).
_GCS_BASE = os.environ.get("ENSCENTERS_GCS_BASE", "https://storage.googleapis.com/ecmwf-open-data")
_AWS_BASE = os.environ.get("ENSCENTERS_AWS_BASE", "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com")
MIRRORS: List[Tuple[str, str]] = [
    ("gcs", _GCS_BASE),
    ("aws", _AWS_BASE),
]

_RETRIES = 3            # bounded retries within ONE mirror before falling back
_BACKOFF_S = 2.0
_TIMEOUT = 60.0
_DEMOTE_AFTER = 4       # K consecutive hard failures on a mirror -> demote it (this process)
_PREP_WORKERS = 16
_RANGE_WORKERS = 4

# --- sticky circuit-breaker (per worker PROCESS; persists across the members a
# ProcessPoolExecutor worker handles, so a full GCS outage costs ~K slow timeouts
# per process, not per request). Reset in tests via reset_breaker(). ---
_DEMOTED: set = set()
_FAILS: Dict[str, int] = {}


def reset_breaker() -> None:
    _DEMOTED.clear()
    _FAILS.clear()


def _active_mirrors() -> List[Tuple[str, str]]:
    """Mirrors to try, preferred first, minus demoted ones. Never empty: if every
    mirror is demoted, fall back to the LAST mirror (AWS) as a last resort so a
    request still attempts something (and surfaces a clean error if it too fails)."""
    live = [m for m in MIRRORS if m[0] not in _DEMOTED]
    return live or [MIRRORS[-1]]


class _Client:
    """Holder: an ecmwf-opendata Client (used ONLY for offline path resolution) +
    a pooled requests.Session for the byte-range GETs."""

    def __init__(self, od_client, session):
        self.od = od_client
        self.session = session


def make_client(source: str = "aws", model: str = "ifs") -> "_Client":
    import requests
    from requests.adapters import HTTPAdapter
    from ecmwf.opendata import Client
    # ALWAYS resolve paths with source="aws": its virtual-hosted bucket puts the
    # bucket in the HOST, so _get_urls yields a clean BUCKET-RELATIVE object path
    # ("{date}/{HH}z/...") that both mirror bases expect. The "ecmwf" portal source
    # would prepend "/forecasts" and "google" would prepend "ecmwf-open-data/",
    # breaking the mirror URLs. The passed ``source`` (the CLI --source) is ignored
    # here because path resolution is offline + mirror-independent; the actual data
    # comes from MIRRORS, not this client.
    od = Client(source="aws", model=model)             # _get_urls is offline string-building
    sess = requests.Session()
    try:
        from urllib3.util.retry import Retry
        retry = Retry(total=1, backoff_factor=0.4, status_forcelist=(429, 500, 502, 503, 504))
        adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16)
    except Exception:  # noqa: BLE001
        adapter = HTTPAdapter(pool_connections=16, pool_maxsize=16)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return _Client(od, sess)


def _path_of(ecmwf_url: str) -> str:
    """Mirror-independent object path (no leading slash) from a resolved
    ecmwf-opendata URL: the bit after the host that is identical across mirrors."""
    return urlparse(ecmwf_url).path.lstrip("/")


def resolve_path(client: "_Client", cycle, stream: str, typ: str, step: int) -> str:
    """Mirror-independent ``.grib2`` object path for (stream, type, step). Offline
    (string-building); the file is shared across members so param is irrelevant."""
    res = client.od._get_urls(request=None, use_index=False, date=cycle,
                              time=cycle.hour, stream=stream, type=typ,
                              param="msl", step=step)
    return _path_of(res.urls[0])


def _http_once(session, url: str, headers: Optional[dict], timeout: float) -> Tuple[Optional[bytes], str]:
    """ONE mirror, bounded retry. Returns (bytes, "ok") on 200/206; (None,
    "absent") on a definitive 404 (not published on THIS mirror - try the next,
    no breaker hit); (None, "fail") on timeout / 5xx / network after retries (a
    real mirror problem - counts toward the breaker)."""
    last_fail = False
    for attempt in range(1, _RETRIES + 1):
        try:
            r = session.get(url, headers=headers or {}, timeout=timeout)
            if r.status_code in (200, 206):
                return r.content, "ok"
            if r.status_code == 404:
                return None, "absent"
            last_fail = True
        except Exception:  # noqa: BLE001 - transient network
            last_fail = True
        if attempt < _RETRIES:
            time.sleep(_BACKOFF_S * attempt)
    return None, ("fail" if last_fail else "absent")


def fetch(client: "_Client", path: str, headers: Optional[dict] = None,
          *, timeout: float = _TIMEOUT, progress=None) -> Tuple[Optional[bytes], Optional[str]]:
    """Fetch ``path`` (a range GET if ``headers`` carries Range, else whole object)
    from the preferred live mirror, falling back to the next on absent/failure.
    Updates the sticky circuit-breaker on hard failures. Returns (bytes,
    mirror_name) or (None, None) when EVERY mirror failed (caller surfaces a clean
    error; no infinite loop)."""
    for name, base in _active_mirrors():
        data, status = _http_once(client.session, base + "/" + path.lstrip("/"),
                                  headers, timeout)
        if status == "ok":
            _FAILS[name] = 0
            return data, name
        if status == "fail":
            _FAILS[name] = _FAILS.get(name, 0) + 1
            # Demote a non-last mirror after K consecutive hard failures so the
            # rest of this process's requests skip it (sticky).
            if name != MIRRORS[-1][0] and _FAILS[name] >= _DEMOTE_AFTER and name not in _DEMOTED:
                _DEMOTED.add(name)
                if progress:
                    progress(f"[enscenters] mirror '{name}' DEMOTED after "
                             f"{_FAILS[name]} consecutive failures; serving from fallback")
        # "absent" or "fail" -> try the next mirror
    return None, None


def head_any(client: "_Client", path: str, *, timeout: float = 20.0) -> Optional[str]:
    """Return the name of the first mirror (preferred order) that HAS ``path``
    (HTTP 200), or None if none do. Used by the mirror-aware completeness gate."""
    for name, base in _active_mirrors():
        url = base + "/" + path.lstrip("/")
        for attempt in range(2):
            try:
                code = client.session.head(url, timeout=timeout).status_code
            except Exception:  # noqa: BLE001
                code = None
            if code == 200:
                return name
            if code == 404:
                break          # definitively absent on this mirror -> next mirror
            time.sleep(0.5 * (attempt + 1))
    return None


def filter_index(raw: str, spec: EnsModelSpec) -> Dict[str, Dict[str, Tuple[int, int]]]:
    """Parse a JSON-Lines ``.index`` -> ``{member_key: {kind: (offset, length)}}``
    for the records we need: ``msl`` plus ``gh``/``z`` at the two thickness levels.
    ``member_key`` is the int member number as a string for perturbed records, or
    ``"ctl"`` for the single control record (type cf/fc, no number). ``kind`` is
    ``"msl"`` / ``"gh<level>"`` (e.g. ``gh300``)."""
    top, bot = str(spec.gh_levels[0]), str(spec.gh_levels[1])
    out: Dict[str, Dict[str, Tuple[int, int]]] = {}
    for ln in raw.splitlines():
        if '"msl"' not in ln and ('"' + spec.gh_param + '"') not in ln:
            continue
        o = json.loads(ln)
        param = o.get("param")
        if param == "msl":
            kind = "msl"
        elif param == spec.gh_param and o.get("levelist") in (top, bot):
            kind = "gh" + str(o.get("levelist"))
        else:
            continue
        typ = o.get("type")
        if typ == spec.pf_type and o.get("number") is not None:
            key = str(int(o["number"]))
        elif typ == spec.control_type and o.get("number") in (None, "0", 0):
            key = "ctl"
        else:
            continue
        try:
            off, length = int(o["_offset"]), int(o["_length"])
        except (KeyError, ValueError, TypeError):
            continue
        out.setdefault(key, {})[kind] = (off, length)
    return out


def _idx_path(tmpdir: str, slug: str, step: int) -> str:
    return os.path.join(tmpdir, f"ecidx_{slug}_f{step:03d}.json")


def _index_path(grib_path: str) -> str:
    return grib_path.replace(".grib2", ".index")


def prepare(spec: EnsModelSpec, cycle, steps: List[int], tmpdir: str,
            *, source: str = "aws", progress=print) -> None:
    """Fetch each step's (~2 MB) ``.index`` ONCE - perturbed + control - from
    whichever mirror serves it, filter to the records we need, and write a small
    per-step JSON to ``tmpdir`` (the mirror-independent object PATH + the offsets)
    that the member workers read. Runs once per step in the main process. Raises if
    NO step yields a usable index, so the currency loop skips + retries."""
    client = make_client(source, spec.od_model)
    ct = spec.control_terminal_step(cycle.hour)

    def one(step: int):
        pf_path = resolve_path(client, cycle, spec.ens_stream, spec.pf_type, step)
        rec = {"pf_path": pf_path, "ctl_path": None, "members": {}}
        raw, _m = fetch(client, _index_path(pf_path), progress=progress)
        if raw:
            rec["members"].update(filter_index(raw.decode("utf-8", "ignore"), spec))
        if spec.control_stream and step <= ct:
            ctl_path = resolve_path(client, cycle, spec.control_stream, spec.control_type, step)
            rec["ctl_path"] = ctl_path
            craw, _m2 = fetch(client, _index_path(ctl_path), progress=progress)
            if craw:
                rec["members"].update(filter_index(craw.decode("utf-8", "ignore"), spec))
        return step, rec

    ok = 0
    with ThreadPoolExecutor(max_workers=_PREP_WORKERS) as ex:
        for step, rec in ex.map(one, steps):
            with open(_idx_path(tmpdir, spec.slug, step), "w") as f:
                json.dump(rec, f)
            if rec["members"]:
                ok += 1
    served = ",".join(sorted(MIRRORS[i][0] for i in range(len(MIRRORS)) if MIRRORS[i][0] not in _DEMOTED)) or "fallback"
    progress(f"[{spec.slug}] prepared {ok}/{len(steps)} step indices (mirrors: {served})")
    if ok == 0:
        raise RuntimeError(f"no usable .index for any step of {cycle:%Y%m%d%H} on any mirror")


def _read_fields(target: str, spec: EnsModelSpec):
    """Decode the concatenated subset GRIB -> (lats, lons, mslp_hPa, thk_gpm|None).
    thk = (gh[top] - gh[bot]) * gh_to_gpm (1.0 for ifs gh, 1/g for AIFS z)."""
    import xarray as xr
    ds = xr.open_dataset(target, engine="cfgrib",
                         backend_kwargs={"filter_by_keys": {"typeOfLevel": "meanSea"},
                                         "indexpath": ""})
    try:
        if not ds.data_vars:
            return None
        var = "msl" if "msl" in ds.data_vars else list(ds.data_vars)[0]
        lats = np.asarray(ds["latitude"].values, dtype=float)
        lons = np.asarray(ds["longitude"].values, dtype=float)
        arr = np.asarray(ds[var].values, dtype=float)
    finally:
        ds.close()
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return None
    if finite.max() > 2000.0:
        arr = arr / 100.0
    thk = None
    try:
        dg = xr.open_dataset(target, engine="cfgrib",
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
    except Exception as e:  # noqa: BLE001
        # LOUD, not silent: a thickness decode failure means warm-core can't run for
        # this field, so the caller will apply the strict lat fallback (NOT pass the
        # full track). Surface the reason so a warm-core outage is never hidden again.
        import sys
        print(f"[{spec.slug}] WARN thickness ({spec.gh_param}@{spec.gh_levels}) decode "
              f"failed -> warm-core fallback for this field: {e!r}", file=sys.stderr)
        thk = None
    return lats, lons, arr, thk


def iter_member_fields(
    client: "_Client",
    spec: EnsModelSpec,
    cycle,
    member_id: str,
    steps: List[int],
    tmpdir: str,
) -> Iterator[Tuple[np.ndarray, np.ndarray, int, np.ndarray, Optional[np.ndarray]]]:
    """Yield ``(lats, lons, step_h, MSLP hPa, THK gpm|None)`` per step for one
    member, reading the per-step indices written by :func:`prepare` and issuing
    concurrent SINGLE-range GETs (mirror fallback per request). A step missing
    this member's records (e.g. control beyond its terminal) is skipped."""
    key = "ctl" if member_id == "CTL" else str(int(member_id[1:]))
    for step_h in steps:
        ipath = _idx_path(tmpdir, spec.slug, step_h)
        if not os.path.exists(ipath):
            continue
        with open(ipath) as f:
            rec = json.load(f)
        recs = (rec.get("members") or {}).get(key)
        if not recs or "msl" not in recs:
            continue
        path = rec["ctl_path"] if key == "ctl" else rec["pf_path"]
        if not path:
            continue
        kinds = ["msl", f"gh{spec.gh_levels[0]}", f"gh{spec.gh_levels[1]}"]
        want = [(k, recs[k]) for k in kinds if k in recs]
        target = os.path.join(tmpdir, f"{spec.slug}_{member_id}_f{step_h:03d}.grib2")
        try:
            def _fetch_range(item):
                off, length = item[1]
                data, _m = fetch(client, path,
                                 headers={"Range": f"bytes={off}-{off + length - 1}"})
                return data
            with ThreadPoolExecutor(max_workers=_RANGE_WORKERS) as ex:
                chunks = list(ex.map(_fetch_range, want))
            if any(c is None for c in chunks):
                continue            # a record could not be served by ANY mirror -> skip step
            with open(target, "wb") as f:
                for c in chunks:
                    f.write(c)
            fields = _read_fields(target, spec)
            if fields is None:
                continue
            lats, lons, arr, thk = fields
            yield lats, lons, step_h, arr, thk
        finally:
            for p in (target, target + ".idx"):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass


# --------------------------------------------------------------------------
# Mirror-aware completeness gate. A cycle is ingestable if its terminal-step
# index is present on EITHER mirror (preferring GCS) - so we never block on the
# slower-publishing mirror. Probes the perturbed terminal step (the longest
# horizon); the control's slight lag is tolerated by the per-step prepare + quorum.
# --------------------------------------------------------------------------
def cycle_complete(spec: EnsModelSpec, cycle, client: "_Client") -> bool:
    step = spec.pf_terminal_step(cycle.hour)
    path = _index_path(resolve_path(client, cycle, spec.ens_stream, spec.pf_type, step))
    return head_any(client, path) is not None


def list_complete_cycles(spec: EnsModelSpec, candidates, client: "_Client") -> List:
    return sorted(c for c in candidates if cycle_complete(spec, c, client))
