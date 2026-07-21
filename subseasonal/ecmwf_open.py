"""ecmwf_open.py — ECMWF open-data (IFS) MJO forecast fetch: HRES + ENS.

Drop-in siblings of ``gefs_mean`` for the MJO forecast products: two model
adapters, ``OPER`` (IFS/HRES deterministic) and ``ENS`` (IFS ensemble, 50
perturbed members), each exposing the same surface the GEFS pipeline consumes
(``newest_complete_init``, ``fetch_members_rmm``, ``fetch_olr_tail``,
``grid``, ``LEVELS``), so ``generate_mjo_rmm``/``generate_hovmollers`` can
dispatch on a --model flag without touching the WH04 math.

Source: https://data.ecmwf.int/forecasts (anonymous, CC-BY-4.0; credit
"ECMWF open data" on every product built from this). 0.25-deg GRIB2, one
file per (cycle, step); each file ships a JSON-lines ``.index`` with
``_offset``/``_length`` per message, so we do byte-range GETs of ONLY the
needed messages — never whole files (an enfo step file is ~5 GB).

The source rate-limits (HTTP 429). Etiquette here, in order of importance:
1. ONE network pass per (init, stream): every fetched message is reduced
   immediately (15S-15N RMM band rows + a 1-deg OLR subfield) and cached to
   disk per (init, stream, day) — process restarts and the RMM/Hovmoller
   double-consumption cost zero extra requests.
2. Range COALESCING: wanted messages are sorted by offset and adjacent ones
   (gap < 1 MB) merge into a single ranged GET — an enfo step's 150 wanted
   messages typically collapse to a handful of requests.
3. Exponential backoff on 429/5xx + a polite inter-request pause.

Field conventions (verified against the live source 2026-07-21):
- OLR: ``ttr`` (top net thermal radiation, J m-2, accumulated FROM INIT,
  negative-up) at sfc. Daily-mean OLR for forecast day dd (1-based) =
  -(ttr(24*dd) - ttr(24*(dd-1))) / 86400  [W m-2]; ttr(0h) == 0.
  Spot-checked: 15S-15N mean ~263 W m-2 (physical).
- Winds: ``u`` at pl 850/200, instantaneous; daily proxy = mean of the
  12Z and next-00Z instants (steps 24dd-12, 24dd), mirroring gefs_mean's
  2-sample proxy (the EOF projection's intraseasonal filtering smooths it).
- ENS members: enfo carries 50 perturbed members (``type=pf``,
  ``number`` 1..50) for both ttr and u at this cycle; no control member is
  published in the open 0.25-deg enfo pressure-level set.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import time
import urllib.error
import urllib.request

import numpy as np

from gefs_mean import _band15_to_rmm  # same 15S-15N cos-weighted reduce

BASE = "https://data.ecmwf.int/forecasts"
LEVELS = (200.0, 850.0)                # match gefs_mean ordering
CACHE_DIR = pathlib.Path(__file__).resolve().parent / "out" / "ecmwf_cache"
CACHE_MAX_AGE_S = 5 * 86400            # prune cached reductions after 5 days
_PAUSE_S = 0.25                        # polite gap between ranged GETs
_MERGE_GAP = 1 << 20                   # coalesce ranges separated by < 1 MB

_STREAMS = {
    # path piece, filename piece, required last step (h), max forecast days
    "oper": {"dir": "ifs/0p25/oper", "suffix": "oper-fc",
             "required_h": 240, "max_days": 15},
    "enfo": {"dir": "ifs/0p25/enfo", "suffix": "enfo-ef",
             "required_h": 360, "max_days": 15},
}


# ------------------------------------------------------------- transport
def _get(url: str, rng: tuple[int, int] | None = None, *,
         timeout: float = 120.0, tries: int = 6, method: str = "GET"):
    """GET (optionally byte-ranged) with backoff on 429/5xx. Returns bytes,
    or None for a 404 (missing step = end of tail, not an error)."""
    last = None
    for i in range(tries):
        try:
            headers = {"User-Agent": "triple-a-tropics-mjo/1.0"}
            if rng is not None:
                headers["Range"] = f"bytes={rng[0]}-{rng[1]}"
            req = urllib.request.Request(url, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last = e
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(min(45.0, 3.0 * (i + 1)))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            if i < tries - 1:
                time.sleep(min(30.0, 2.0 * (i + 1)))
                continue
            raise
    raise RuntimeError(f"ecmwf_open: unreachable after retries: {last}")


def _file_base(init: dt.datetime, stream: str, step: int) -> str:
    cfg = _STREAMS[stream]
    d = init.strftime("%Y%m%d")
    hh = f"{init.hour:02d}"
    return (f"{BASE}/{d}/{hh}z/{cfg['dir']}/"
            f"{d}{hh}0000-{step}h-{cfg['suffix']}")


_index_cache: dict[tuple, list | None] = {}


def _index(init: dt.datetime, stream: str, step: int) -> list | None:
    """Parsed .index for one step file (JSON-lines records with _offset/
    _length). None when the step doesn't exist. In-memory cached."""
    key = (init, stream, step)
    if key not in _index_cache:
        raw = _get(_file_base(init, stream, step) + ".index", timeout=60)
        _index_cache[key] = (
            None if raw is None else
            [json.loads(l) for l in raw.decode().splitlines() if l.strip()])
    return _index_cache[key]


def _fetch_messages(init: dt.datetime, stream: str, step: int,
                    want) -> dict:
    """Byte-range fetch of the index records selected by ``want(rec)``.
    Coalesces adjacent ranges into merged GETs. Returns
    {(param, levelist, number): grib_message_bytes}."""
    recs = _index(init, stream, step)
    if not recs:
        return {}
    sel = sorted((r for r in recs if want(r)), key=lambda r: r["_offset"])
    if not sel:
        return {}
    url = _file_base(init, stream, step) + ".grib2"
    # merge adjacent/near-adjacent ranges
    groups, cur = [], [sel[0]]
    for r in sel[1:]:
        tail = cur[-1]["_offset"] + cur[-1]["_length"]
        if r["_offset"] - tail <= _MERGE_GAP:
            cur.append(r)
        else:
            groups.append(cur)
            cur = [r]
    groups.append(cur)
    # the wanted messages are scattered through the (multi-GB) step file, so
    # coalescing alone leaves ~1 GET per message — fetch merged ranges with a
    # small thread pool (network-bound; GRIB DECODE stays in the caller's
    # thread, eccodes is not thread-safe)
    from concurrent.futures import ThreadPoolExecutor

    def _fetch_group(g):
        lo = g[0]["_offset"]
        hi = g[-1]["_offset"] + g[-1]["_length"] - 1
        time.sleep(_PAUSE_S)
        return g, _get(url, rng=(lo, hi))

    out = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for g, blob in ex.map(_fetch_group, groups):
            if blob is None:
                continue
            lo = g[0]["_offset"]
            for r in g:
                a = r["_offset"] - lo
                out[(r.get("param"), r.get("levelist"),
                     r.get("number"))] = blob[a:a + r["_length"]]
    return out


# --------------------------------------------------------------- decode
def _decode(msg: bytes):
    """One GRIB2 message -> (field[nlat, nlon] float64, lats, lons),
    normalized to lats N->S and lons ascending 0..360 (ECMWF open-data
    GRIBs start longitudes at 180 == -180; unwrapped they would break the
    periodic interp in the band reduce)."""
    import eccodes as ecc
    h = ecc.codes_new_from_message(msg)
    try:
        ni = ecc.codes_get(h, "Ni")
        nj = ecc.codes_get(h, "Nj")
        la1 = ecc.codes_get(h, "latitudeOfFirstGridPointInDegrees")
        la2 = ecc.codes_get(h, "latitudeOfLastGridPointInDegrees")
        lo1 = ecc.codes_get(h, "longitudeOfFirstGridPointInDegrees")
        vals = ecc.codes_get_values(h).reshape(nj, ni)
    finally:
        ecc.codes_release(h)
    lats = np.linspace(la1, la2, nj)
    if lats[0] < lats[-1]:                 # force N->S like gefs_mean.grid()
        lats = lats[::-1]
        vals = vals[::-1]
    lons = (lo1 + np.arange(ni) * (360.0 / ni)) % 360.0
    order = np.argsort(lons)               # ascending 0..360 (no-op if so)
    if not np.array_equal(order, np.arange(ni)):
        lons = lons[order]
        vals = vals[:, order]
    return vals, lats, lons


# ------------------------------------------------------- per-day reduce
def _cache_path(init: dt.datetime, stream: str, dd: int) -> pathlib.Path:
    return (CACHE_DIR /
            f"{init:%Y%m%d%H}_{stream}_d{dd:02d}.npz")


def _prune_cache() -> None:
    try:
        now = time.time()
        for p in CACHE_DIR.glob("*.npz"):
            if now - p.stat().st_mtime > CACHE_MAX_AGE_S:
                p.unlink(missing_ok=True)
    except OSError:
        pass


def _member_ids(stream: str, recs) -> list:
    """The member axis for one stream: enfo = the numbers present on ttr
    records (strings '1'..'50'); oper = a single pseudo-member 'hres'."""
    if stream == "oper":
        return ["hres"]
    nums = sorted({r.get("number") for r in recs
                   if r.get("param") == "ttr"}, key=lambda s: int(s))
    return nums


def _build_day(init: dt.datetime, stream: str, dd: int,
               prev_ttr_rows: dict | None, prev_ttr_mean):
    """Fetch+reduce forecast day dd. Returns (payload dict or None,
    ttr_rows, ttr_mean_sub) where the ttr values are the day's ACCUMULATED
    field reductions carried forward for the next day's differencing.

    payload: {members: [ids], olr[nm,144], u850[nm,144], u200[nm,144],
              olr_mean_1deg[181,360], wind_samples}
    """
    h2 = 24 * dd
    h1 = h2 - 12
    cfg = _STREAMS[stream]
    if h2 > 24 * cfg["max_days"]:
        return None, None, None

    def want_ttr(r):
        return r.get("param") == "ttr"

    def want_u(r):
        return (r.get("param") == "u" and
                str(r.get("levelist")) in ("200", "850"))

    msgs2 = _fetch_messages(init, stream, h2,
                            lambda r: want_ttr(r) or want_u(r))
    if not msgs2 or not any(k[0] == "ttr" for k in msgs2):
        return None, None, None            # step missing -> tail ends
    msgs1 = _fetch_messages(init, stream, h1, want_u)

    recs = _index(init, stream, h2) or []
    candidates = _member_ids(stream, recs)
    # only members whose ttr message actually arrived join this day's axis
    # (a still-uploading enfo file publishes members incrementally)
    members = [m for m in candidates
               if ("ttr", None, None if stream == "oper" else m) in msgs2]
    if not members:
        return None, None, None
    nm = len(members)

    ttr_rows = {}
    ttr_mean_sub = None
    olr = np.full((nm, 144), np.nan)
    u850 = np.full((nm, 144), np.nan)
    u200 = np.full((nm, 144), np.nan)
    wind_samples = 0

    for mi, m in enumerate(members):
        num = None if stream == "oper" else m
        # --- OLR from accumulated ttr differencing
        fld, lats, lons = _decode(msgs2[("ttr", None, num)])
        ttr_rows[m] = _band15_to_rmm(fld, lats, lons)
        sub = fld[::4, ::4]                # 0.25 -> 1.0 deg (721->181)
        ttr_mean_sub = sub if ttr_mean_sub is None else ttr_mean_sub + sub
        # day 1 differences against ttr(0h) == 0; later days need the
        # member's own previous accumulation — a member absent yesterday
        # stays NaN here (tail-break), never a bogus 0-difference
        prev_row = 0.0 if dd == 1 else (prev_ttr_rows or {}).get(m)
        if prev_row is not None:
            olr[mi] = -(ttr_rows[m] - prev_row) / 86400.0
        # --- winds: mean of the available instants (h1, h2)
        for lv, dest in ((850.0, u850), (200.0, u200)):
            rows = []
            for msgs in (msgs1, msgs2):
                raw = msgs.get(("u", str(int(lv)), num))
                if raw is not None:
                    f, la, lo = _decode(raw)
                    rows.append(_band15_to_rmm(f, la, lo))
            if rows:
                dest[mi] = np.mean(rows, axis=0)
        wind_samples = max(
            wind_samples,
            sum(1 for msgs in (msgs1, msgs2)
                if msgs.get(("u", "850", num)) is not None))

    ttr_mean_sub = ttr_mean_sub / nm
    # the mean-accumulated difference is only valid when BOTH days average
    # the SAME member set (a still-uploading enfo day can publish a subset);
    # a membership change would silently corrupt the daily-mean OLR, so
    # invalidate that day instead — fetch_olr_tail's finite check ends the
    # tail there (contiguous-tail semantics keep the product honest)
    if dd > 1 and set(members) != set(prev_ttr_rows or {}):
        olr_mean_1deg = np.full_like(ttr_mean_sub, np.nan)
    else:
        prev_mean = prev_ttr_mean if prev_ttr_mean is not None else 0.0
        olr_mean_1deg = -(ttr_mean_sub - prev_mean) / 86400.0
    payload = {"members": members, "olr": olr, "u850": u850, "u200": u200,
               "olr_mean_1deg": olr_mean_1deg,
               "wind_samples": wind_samples}
    return payload, ttr_rows, ttr_mean_sub


def _ensure_days(init: dt.datetime, stream: str, days: int,
                 log=print) -> list:
    """The single network pass: forecast days 1..days reduced + disk-cached.
    Returns the list of per-day payloads (contiguous; stops at the first
    missing day). Cached days cost zero requests."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _prune_cache()
    payloads = []
    prev_rows: dict | None = None
    prev_mean = None
    for dd in range(1, days + 1):
        cp = _cache_path(init, stream, dd)
        if cp.exists():
            try:
                z = np.load(cp, allow_pickle=False)
                members = [m for m in z["members"]]
                payloads.append({
                    "members": members, "olr": z["olr"], "u850": z["u850"],
                    "u200": z["u200"], "olr_mean_1deg": z["olr_mean_1deg"],
                    "wind_samples": int(z["wind_samples"])})
                prev_rows = {m: r for m, r in zip(members, z["ttr_rows"])}
                prev_mean = z["ttr_mean_sub"]
                continue
            except Exception:              # noqa: BLE001 — refetch this day
                cp.unlink(missing_ok=True)
        # a cache gap needs the previous day's accumulated ttr to difference
        if dd > 1 and prev_rows is None:
            break
        payload, rows, mean_sub = _build_day(init, stream, dd,
                                             prev_rows, prev_mean)
        if payload is None:
            break
        np.savez_compressed(
            cp, members=np.array(payload["members"]),
            olr=payload["olr"].astype("f4"),
            u850=payload["u850"].astype("f4"),
            u200=payload["u200"].astype("f4"),
            olr_mean_1deg=payload["olr_mean_1deg"].astype("f4"),
            wind_samples=np.int32(payload["wind_samples"]),
            ttr_rows=np.stack([rows[m] for m in payload["members"]]
                              ).astype("f8"),
            ttr_mean_sub=(mean_sub if isinstance(mean_sub, np.ndarray)
                          else np.zeros((181, 360))).astype("f8"))
        payloads.append(payload)
        prev_rows, prev_mean = rows, mean_sub
        log(f"  ecmwf {stream} d{dd:02d}: {len(payload['members'])} member(s)"
            f", winds x{payload['wind_samples']}")
    return payloads


# ------------------------------------------------------------ model API
class _Model:
    """gefs_mean-compatible adapter for one ECMWF open-data stream."""

    LEVELS = LEVELS

    def __init__(self, stream: str, label: str):
        self.stream = stream
        self.label = label                 # honest on-plot name

    @staticmethod
    def grid():
        """Same 1-deg grid gefs_mean returns: lats +90..-90, lons 0..359."""
        return np.arange(90.0, -90.1, -1.0), np.arange(0.0, 360.0, 1.0)

    def newest_complete_init(self, now: dt.datetime | None = None
                             ) -> dt.datetime:
        """Newest 00Z cycle whose long-range files are up (open-data 00Z
        dissemination completes ~09:00 UTC; verified by HEAD on the
        required last step, walking back up to 2 days)."""
        now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        newest = dt.datetime(now.year, now.month, now.day, 0)
        if now.hour < 10:
            newest -= dt.timedelta(days=1)
        init = newest
        for _ in range(3):
            url = (_file_base(init, self.stream,
                              _STREAMS[self.stream]["required_h"])
                   + ".index")
            try:
                if _get(url, timeout=30, tries=3, method="HEAD") is not None:
                    return init
            except Exception:              # noqa: BLE001 — treat as missing
                pass
            init -= dt.timedelta(days=1)
        # every probe failed (transient outage, most likely): return the
        # NEWEST candidate, never an unprobed older one — downstream fetches
        # will find nothing and the forecast layer skips honestly
        return newest

    def fetch_members_rmm(self, init: dt.datetime, days: int = 15,
                          workers: int = 0, members=None) -> dict:
        """{member: (dates[], olr[nd,144], u850[nd,144], u200[nd,144])} —
        same contract as gefs_mean.fetch_members_rmm. ``workers`` is
        accepted for signature parity (fetching is range-coalesced, not
        process-pooled). ``members`` optionally subsets (test hook)."""
        payloads = _ensure_days(init, self.stream, days)
        out = {}
        if not payloads:
            return out
        all_members = payloads[0]["members"]
        keep = [m for m in all_members
                if members is None or m in set(members)]
        for m in keep:
            dates, o, a, b = [], [], [], []
            for dd, p in enumerate(payloads, start=1):
                if m not in p["members"]:
                    break                  # contiguous tail per member
                mi = p["members"].index(m)
                row_o, row_a, row_b = p["olr"][mi], p["u850"][mi], p["u200"][mi]
                if not (np.isfinite(row_o).all()
                        and np.isfinite(row_a).all()
                        and np.isfinite(row_b).all()):
                    break
                dates.append((init + dt.timedelta(days=dd)).date())
                o.append(row_o)
                a.append(row_a)
                b.append(row_b)
            if dates:
                out[m] = (dates, np.stack(o), np.stack(a), np.stack(b))
        return out

    def fetch_olr_tail(self, init: dt.datetime, days: int = 15,
                       workers: int = 0):
        """(dates, olr[nd,181,360], nsteps[nd]) member-mean daily OLR on the
        1-deg grid() — same contract as gefs_mean.fetch_olr_tail. nsteps is
        4 per day (the ttr accumulation covers the full 24 h, matching the
        quality of GEFS's four 6-h buckets)."""
        payloads = _ensure_days(init, self.stream, days)
        dates, olrs, ns = [], [], []
        for dd, p in enumerate(payloads, start=1):
            fld = p["olr_mean_1deg"]
            if fld is None or not np.isfinite(fld).all():
                break
            dates.append((init + dt.timedelta(days=dd)).date())
            olrs.append(np.asarray(fld, dtype=float))
            ns.append(4)
        if not dates:
            return [], None, []
        return dates, np.stack(olrs), ns


OPER = _Model("oper", "ECMWF IFS (HRES)")
ENS = _Model("enfo", "ECMWF IFS ensemble")

# credit line every downstream product must carry
CREDIT = "Forecast data: ECMWF open data (CC BY 4.0)"
