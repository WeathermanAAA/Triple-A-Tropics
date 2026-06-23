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


def gather_window(year: int, since: _dt.datetime,
                  until: _dt.datetime | None = None,
                  basins=("AL", "EP"), stagger_s: float = 0.0,
                  log=print) -> dict:
    """Fetch all HDOB/VDM/sonde bulletins with timestamp in [since, until) for
    the given basins/year. ``stagger_s`` sleeps between file fetches (backfill
    politeness). Returns {basin: {"hdob":[...], "vdm":[...], "sonde":[...]}}
    plus a flat ``dropped`` count for logging. HDOB resolves the pre-2012
    alternate archive subtree (see ``hdob_dirs``); a year with no modern-format
    HDOB dir is logged and skipped (VDM/sonde, which DO exist pre-2012, still
    gather normally)."""
    out: dict = {"basins": {}, "dropped": 0}
    for b in basins:
        if b not in BASINS:
            continue
        _, vdm_pil, sonde_pil, _, _ = BASINS[b]
        bag = {"hdob": [], "vdm": [], "sonde": []}
        # HDOB: pre-2012 the dir layout differs, so fetch by full URL.
        if not hdob_dirs(year, b):
            log(f"recon: {year} {b} predates the modern HDOB archive "
                "(legacy storm-name format) - skipping HDOB")
        else:
            hdob_pairs, dropped = _recent_hdob(year, b, since, until)
            out["dropped"] += dropped
            for url, _name in hdob_pairs:
                txt = fetch.get(url)
                if txt:
                    bag["hdob"].append(txt)
                if stagger_s:
                    _time.sleep(stagger_s)
        # VDM + sonde share the long-standing PIL dir for every archive year.
        for key, pil in (("vdm", vdm_pil), ("sonde", sonde_pil)):
            files, dropped = _recent_files(year, pil, since, until)
            out["dropped"] += dropped
            for name in files:
                txt = fetch.get(ARCHIVE.format(year=year, pil=pil) + name)
                if txt:
                    bag[key].append(txt)
                if stagger_s:
                    _time.sleep(stagger_s)
        out["basins"][b] = bag
    return out


def gather_live_hdob(basins=("AL", "EP")) -> list[str]:
    """Latest live HDOB blocks (the .shtml pages) - sub-archive freshness."""
    blocks = []
    for b in basins:
        if b not in BASINS:
            continue
        for prod in BASINS[b][3]:
            body = fetch.get_pre(TEXT.format(prod=prod))
            if body and "HDOB" in body:
                blocks.append(body)
    return blocks


def gather_tcpod() -> str | None:
    """TCPOD text: NHC live page first, IEM AFOS REPRPD as fallback."""
    body = fetch.get_pre(TEXT.format(prod="MIAREPRPD"))
    if body and "REPRPD" in body:
        return body
    return fetch.iem_afos("REPRPD", limit=1)
