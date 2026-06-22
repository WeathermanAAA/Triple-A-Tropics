"""reconobs.ingest - gather recon bulletins from NHC (no auth), guarded.

Sources (all public, no key):
  * HDOB archive (full record):  archive/recon/{yr}/AHONT1/ (Atlantic),
    AHOPN1/ (E-Pacific). Filenames carry a YYYYMMDDHHMM timestamp.
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

# basin -> (hdob_pil, vdm_pil, sonde_pil, live_hdob_prods)
BASINS = {
    "AL": ("AHONT1", "REPNT2", "REPNT3", ("URNT15-NOAA", "URNT15-USAF")),
    "EP": ("AHOPN1", "REPPN2", "REPPN3", ("URPN15-NOAA", "URPN15-USAF")),
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


def _recent_files(year: int, pil: str, since: _dt.datetime,
                  until: _dt.datetime | None = None,
                  cap: int = 4000) -> tuple[list[str], int]:
    """Archive .txt filenames for one PIL/year with timestamp in [since, until)
    (until=None means open-ended). Capped newest-first; returns (names, dropped)."""
    names = fetch.list_dir_txt(ARCHIVE.format(year=year, pil=pil))
    keep = [n for n in names if (ts := file_ts(n)) and ts >= since
            and (until is None or ts < until)]
    keep.sort(key=lambda n: file_ts(n) or _dt.datetime.min.replace(
        tzinfo=_dt.timezone.utc))
    dropped = max(0, len(keep) - cap)
    return keep[-cap:], dropped


def gather_window(year: int, since: _dt.datetime,
                  until: _dt.datetime | None = None,
                  basins=("AL", "EP"), stagger_s: float = 0.0) -> dict:
    """Fetch all HDOB/VDM/sonde bulletins with timestamp in [since, until) for
    the given basins/year. ``stagger_s`` sleeps between file fetches (backfill
    politeness). Returns {basin: {"hdob":[...], "vdm":[...], "sonde":[...]}}
    plus a flat ``dropped`` count for logging."""
    out: dict = {"basins": {}, "dropped": 0}
    for b in basins:
        if b not in BASINS:
            continue
        hdob_pil, vdm_pil, sonde_pil, _ = BASINS[b]
        bag = {"hdob": [], "vdm": [], "sonde": []}
        for key, pil in (("hdob", hdob_pil), ("vdm", vdm_pil),
                         ("sonde", sonde_pil)):
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
