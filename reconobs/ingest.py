"""reconobs.ingest - gather recon bulletins from NHC (no auth), guarded.

Sources (all public, no key):
  * HDOB archive (full record):  archive/recon/{yr}/AHONT1/ (Atlantic),
    AHOPN1/ (E-Pacific) for yr>=2012; 2007-2011 use the per-agency HDOB
    subtree (see ``hdob_dirs``). Filenames carry a YYYYMMDDHHMM timestamp.
  * VDM archive:        REPNT2/ (AL), REPPN2/ (EP)
  * Dropsonde archive:  REPNT3/ (AL), REPPN3/ (EP)
  * HDOB live freshness: text/URNT15-{NOAA,USAF}.shtml (AL),
    text/URPN15-{NOAA,USAF}.shtml (EP)  -> the latest 10-min block
  * TCPOD: text/MIAREPRPD.shtml (IEM AFOS REPRPD fallback)

Every fetch is guarded (fetch.get returns None on failure); a dead source
yields an empty list, never an exception.
"""
from __future__ import annotations

import datetime as _dt
import os as _os
import re
import time as _time

from . import fetch

ARCHIVE = fetch.NHC + "/archive/recon/{year}/{pil}/"
TEXT = fetch.NHC + "/text/{prod}.shtml"

# Pre-2012 the HDOB PIL dirs (AHONT1/AHOPN1) don't exist; the HDOBs instead
# live under a per-agency HDOB subtree. 2008-2011: a per-basin URxx15/ subdir
# (already basin-separated); 2007: a flat agency dir mixing both basins'
# files (so the listing is prefix-filtered by URxx15). 2006 is empty and
# <=2005 is a legacy storm-name layout we don't parse (skip-and-log upstream).
HDOB_LEGACY_FLOOR = 2012                          # first year AHONT1/AHOPN1 exist
HDOB_AGENCY = ("USAF", "NOAA")
HDOB_ALT = fetch.NHC + "/archive/recon/{year}/HDOB/{agency}/{sub}"

# basin -> (hdob_pil, vdm_pil, sonde_pil, live_hdob_prods, hdob_alt_prefix)
BASINS = {
    "AL": ("AHONT1", "REPNT2", "REPNT3", ("URNT15-NOAA", "URNT15-USAF"),
           "URNT15"),
    "EP": ("AHOPN1", "REPPN2", "REPPN3", ("URPN15-NOAA", "URPN15-USAF"),
           "URPN15"),
}
_TS = re.compile(r"\.(\d{12})\.txt$")


def file_ts(name: str) -> _dt.datetime | None:
    m = _TS.search(name)
    if not m:
        return None
    try:
        return _dt.datetime.strptime(m.group(1), "%Y%m%d%H%M").replace(
            tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def hdob_dirs(year: int, basin: str) -> list[tuple[str, str | None]]:
    """Archive directory URL(s) holding HDOB .txt for a basin/year, each paired
    with a filename prefix to keep (None = keep all). year>=2012 is the single
    PIL dir (AHONT1/AHOPN1); 2008-2011 merges both agencies' URxx15/ subdirs;
    2007 merges both agencies' flat dirs (prefix-filtered, since they mix
    basins). <2007 returns [] (legacy layout, handled as skip-and-log)."""
    if year >= HDOB_LEGACY_FLOOR:
        return [(ARCHIVE.format(year=year, pil=BASINS[basin][0]), None)]
    if year < 2007:
        return []
    pref = BASINS[basin][4]
    sub = "" if year == 2007 else pref + "/"        # 2007 is the flat variant
    keep_pref = pref if year == 2007 else None       # flat dir mixes basins
    return [(HDOB_ALT.format(year=year, agency=ag, sub=sub), keep_pref)
            for ag in HDOB_AGENCY]


def _filter_window(names, since, until, cap):
    """Keep .txt names with a [since, until) timestamp, sorted oldest-first and
    capped newest-first. Returns (names, dropped)."""
    keep = [n for n in names if (ts := file_ts(n)) and ts >= since
            and (until is None or ts < until)]
    keep.sort(key=lambda n: file_ts(n) or _dt.datetime.min.replace(
        tzinfo=_dt.timezone.utc))
    dropped = max(0, len(keep) - cap)
    return keep[-cap:], dropped


def _recent_files(year: int, pil: str, since: _dt.datetime,
                  until: _dt.datetime | None = None,
                  cap: int = 4000) -> tuple[list[str], int]:
    """Archive .txt filenames for one PIL/year with timestamp in [since, until)
    (until=None means open-ended). Capped newest-first; returns (names, dropped)."""
    names = fetch.list_dir_txt(ARCHIVE.format(year=year, pil=pil))
    return _filter_window(names, since, until, cap)


def _recent_hdob(year: int, basin: str, since: _dt.datetime,
                 until: _dt.datetime | None = None,
                 cap: int = 4000) -> tuple[list[tuple[str, str]], int]:
    """HDOB (full_url, filename) pairs in [since, until) for a basin/year,
    merged across the year's HDOB dir(s). Pairs (not bare names) because the
    pre-2012 alternate subtree spreads files over per-agency dirs. Empty list
    on a legacy/missing year (the caller logs+skips)."""
    by_name: dict[str, str] = {}
    for url, keep_pref in hdob_dirs(year, basin):
        for n in fetch.list_dir_txt(url):
            if keep_pref and not n.startswith(keep_pref):
                continue
            by_name[n] = url + n                  # filenames are unique by ts
    kept, dropped = _filter_window(list(by_name), since, until, cap)
    return [(by_name[n], n) for n in kept], dropped


# WMO abbreviated heading (URNT15 KNHC 191753 / UZNT13 KWBC 162306 ...) — the
# cheap "this is really a bulletin" check before a body is cached forever.
_WMO_ANY_HDR = re.compile(r"^[A-Z]{4}\d{2}\s+[A-Z]{4}\s+\d{6}", re.M)

# Poller-mode (cached) fetches run tighter than the default 2x20s-retry
# profile: a missing/erroring file must not stall the tick — it simply
# retries next tick (the filename stays uncached until it reads clean).
_CACHED_TIMEOUT_S = 10.0
_CACHED_RETRIES = 1
# Per-call failed-fetch budget: an upstream outage must degrade a tick to
# "listings only", never turn it into a full-window retry storm.
_FETCH_FAIL_BUDGET = 25


def _cached_fetch(url: str, name: str, pil: str, cache_dir: str | None,
                  stagger_s: float, fails: list | None = None) -> str | None:
    """Fetch one archive bulletin, via the filename-keyed local cache when
    ``cache_dir`` is set. Archive .txt files are immutable and their names are
    unique (embedded timestamp), so presence == fetched: a cache hit costs no
    network and no stagger, which is what makes a high-frequency poller tick
    cheap (only genuinely NEW files hit the wire). A fetched body is cached
    ONLY when it carries a WMO heading — a 200-status error/maintenance page
    must not poison the immutable cache (the body is still returned for this
    run; the decoder rejects garbage, and the name refetches next run)."""
    if cache_dir:
        path = _os.path.join(cache_dir, pil, name)
        if _os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return f.read()
            except OSError:
                pass                              # unreadable -> refetch
        txt = fetch.get(url, timeout=_CACHED_TIMEOUT_S,
                        retries=_CACHED_RETRIES)
    else:
        txt = fetch.get(url)
    if txt is None and fails is not None:
        fails.append(name)
    if txt and cache_dir and _WMO_ANY_HDR.search(txt):
        d = _os.path.join(cache_dir, pil)
        _os.makedirs(d, exist_ok=True)
        tmp = _os.path.join(d, f".{name}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(txt)
            _os.replace(tmp, _os.path.join(d, name))   # atomic: never half-written
        except OSError:
            pass                                  # cache is best-effort only
    if stagger_s:
        _time.sleep(stagger_s)
    return txt


def _prune_cache(cache_dir: str, since: _dt.datetime) -> None:
    """Drop cached bulletins that have aged out of the window (best-effort;
    the cache is an optimization, never state — losing it only re-downloads)."""
    try:
        for pil in _os.listdir(cache_dir):
            d = _os.path.join(cache_dir, pil)
            if not _os.path.isdir(d):
                continue
            for name in _os.listdir(d):
                ts = file_ts(name)
                if ts is not None and ts < since:
                    try:
                        _os.unlink(_os.path.join(d, name))
                    except OSError:
                        pass
    except OSError:
        pass


def gather_window(year: int, since: _dt.datetime,
                  until: _dt.datetime | None = None,
                  basins=("AL", "EP"), stagger_s: float = 0.0,
                  cache_dir: str | None = None,
                  log=print) -> dict:
    """Fetch all HDOB/VDM/sonde bulletins with timestamp in [since, until) for
    the given basins/year. ``stagger_s`` sleeps between file fetches (backfill
    politeness). ``cache_dir`` (poller mode) caches archive bulletins by
    filename so repeat ticks only download new files. Returns {basin:
    {"hdob":[...], "vdm":[...], "sonde":[...]}} plus a flat ``dropped`` count
    for logging. HDOB resolves the pre-2012 alternate archive subtree (see
    ``hdob_dirs``); a year with no modern-format HDOB dir is logged and
    skipped (VDM/sonde, which DO exist pre-2012, still gather normally)."""
    out: dict = {"basins": {}, "dropped": 0}
    if cache_dir and until is None:               # rolling window only
        _prune_cache(cache_dir, since)
    # Failed-fetch budget (poller mode only): once tripped, remaining files
    # this call are skipped — they stay uncached and heal on the next tick.
    fails: list | None = [] if cache_dir else None

    def _over_budget() -> bool:
        if fails is not None and len(fails) >= _FETCH_FAIL_BUDGET:
            log(f"recon: fetch-failure budget hit ({len(fails)}) - deferring "
                "remaining files to the next run")
            return True
        return False

    for b in basins:
        if b not in BASINS:
            continue
        hdob_pil, vdm_pil, sonde_pil, _, _ = BASINS[b]
        bag = {"hdob": [], "vdm": [], "sonde": []}
        # HDOB: pre-2012 the dir layout differs, so fetch by full URL.
        if not hdob_dirs(year, b):
            log(f"recon: {year} {b} predates the modern HDOB archive "
                "(legacy storm-name format) - skipping HDOB")
        else:
            hdob_pairs, dropped = _recent_hdob(year, b, since, until)
            out["dropped"] += dropped
            for url, name in hdob_pairs:
                if _over_budget():
                    break
                txt = _cached_fetch(url, name, hdob_pil, cache_dir, stagger_s,
                                    fails)
                if txt:
                    bag["hdob"].append(txt)
        # VDM + sonde share the long-standing PIL dir for every archive year.
        for key, pil in (("vdm", vdm_pil), ("sonde", sonde_pil)):
            files, dropped = _recent_files(year, pil, since, until)
            out["dropped"] += dropped
            for name in files:
                if _over_budget():
                    break
                url = ARCHIVE.format(year=year, pil=pil) + name
                txt = _cached_fetch(url, name, pil, cache_dir, stagger_s,
                                    fails)
                if txt:
                    bag[key].append(txt)
        out["basins"][b] = bag
    return out


# WMO header line of an HDOB bulletin (either basin) — the split anchor for a
# concatenated multi-product payload.
_WMO_HDOB_HDR = re.compile(r"^UR(?:NT|PN)15\s+[A-Z]{4}\s+\d{6}", re.M)


def split_hdob_blocks(payload: str | None) -> list[str]:
    """Split a concatenated text payload into individual HDOB bulletins. Each
    block is re-framed with a synthetic sequence line so the decoder's fixed
    row layout (seq line 0 / WMO line 1 / mission line 2) holds."""
    if not payload:
        return []
    starts = [m.start() for m in _WMO_HDOB_HDR.finditer(payload)]
    blocks = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(payload)
        blocks.append("000\n" + payload[s:e].strip("\n"))
    return blocks


def gather_live_hdob(basins=("AL", "EP")) -> dict[str, list[str]]:
    """Live HDOB blocks per basin - sub-archive freshness. Two lanes, both
    guarded: the live product pages (latest block per agency) plus a recent-
    products top-up (last ~8 blocks per basin, so a block that flipped off the
    live page between ticks is still caught). Keyed by basin so each block
    joins the RIGHT basin's decode pass; duplicates across lanes and vs the
    archive are harmless (points dedup by timestamp)."""
    out: dict[str, list[str]] = {}
    for b in basins:
        if b not in BASINS:
            continue
        blocks = []
        for prod in BASINS[b][3]:
            body = fetch.get_pre(TEXT.format(prod=prod))
            if body and "HDOB" in body:
                blocks.append(body)
        payload = fetch.recent_products(BASINS[b][0], limit=8)
        blocks += [x for x in split_hdob_blocks(payload) if "HDOB" in x]
        out[b] = blocks
    return out


def gather_tcpod() -> str | None:
    """TCPOD text: NHC live page first, IEM AFOS REPRPD as fallback."""
    body = fetch.get_pre(TEXT.format(prod="MIAREPRPD"))
    if body and "REPRPD" in body:
        return body
    return fetch.iem_afos("REPRPD", limit=1)
