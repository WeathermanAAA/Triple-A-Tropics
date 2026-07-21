"""qscatobs.fetch - BYU SCP HRStorms listing, colocation table, downloads.

Tree (anonymous HTTPS, directory-listed):
  https://ftp.scp.byu.edu/data/qscatv2/HRStorms/{BASIN}/{YYYY}/{STORM}/
    QS_S1B<rev5>.<yyyydddhhmm>.avewr_BYU_<STORM>_<mmddyy>_WRave3.gz  (winds)
    ...quicklook GIFs...
  https://ftp.scp.byu.edu/data/qscatv2/HRStorms/AllStormColocsWBestTracks_2009.txt

The colocation table is the source of truth for OBSERVATION time and the
best-track center at overpass (the filename timestamp is the JPL product
CREATION time, hours later - never use it as the obs time).
"""
from __future__ import annotations

import datetime as dt
import re
import urllib.request

BASE = "https://ftp.scp.byu.edu/data/qscatv2/HRStorms"
COLOC = f"{BASE}/AllStormColocsWBestTracks_2009.txt"
_UA = {"User-Agent": "triple-a-tropics-qscat/1.0"}
_WR = re.compile(
    r'href="(QS_S1B(\d{5})\.(\d{11})\.avewr_BYU_([A-Z0-9\-]+)_'
    r'(\d{6})_WRave3\.gz)"')


def get_bytes(url: str, timeout: float = 120.0) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:                        # noqa: BLE001 — caller retries
        return None


def get_text(url: str, timeout: float = 60.0) -> str | None:
    raw = get_bytes(url, timeout)
    return raw.decode("utf-8", "ignore") if raw is not None else None


def storm_dir(basin: str, year: int, storm: str) -> str:
    return f"{BASE}/{basin.upper()}/{year}/{storm.upper()}/"


def list_passes(basin: str, year: int, storm: str) -> list[dict]:
    """WRave3 wind files in a storm dir: [{file, rev}] sorted by rev."""
    html = get_text(storm_dir(basin, year, storm))
    if not html:
        return []
    out = {}
    for m in _WR.finditer(html):
        out[int(m.group(2))] = {"file": m.group(1), "rev": int(m.group(2))}
    return [out[r] for r in sorted(out)]


def load_colocation(text: str | None = None) -> list[dict]:
    """Parsed colocation rows: storm identity + per-overpass rev, obs time,
    and the best-track center/intensity interpolated to overpass time."""
    text = text if text is not None else get_text(COLOC, timeout=120)
    if not text:
        return []
    rows = []
    for line in text.splitlines():
        p = line.split()
        if len(p) != 13 or "/" not in p[0]:
            continue
        try:
            year, doy = int(p[7]), int(p[8])
            hh, mm, ss = int(p[10][:2]), int(p[10][2:4]), int(p[10][4:6])
            t = (dt.datetime(year, 1, 1, hh, mm, ss,
                             tzinfo=dt.timezone.utc)
                 + dt.timedelta(days=doy - 1))
            rows.append({
                "storm": p[1], "season": int(p[2]), "num": int(p[3]),
                "type": p[4], "basin": p[5], "bt_wind_kt": int(p[6]),
                "rev": int(p[9]), "t": t,
                "bt_lat": float(p[11]), "bt_lon": float(p[12])})
        except (ValueError, IndexError):
            continue
    return rows


def storm_colocs(rows: list[dict], basin: str, season: int,
                 storm: str) -> dict[int, dict]:
    """{rev: coloc row} for one storm."""
    return {r["rev"]: r for r in rows
            if r["basin"] == basin.upper() and r["season"] == season
            and r["storm"] == storm.upper()}


def list_storms(basin: str, year: int) -> list[str]:
    """Storm dir names for a basin/year."""
    html = get_text(f"{BASE}/{basin.upper()}/{year}/")
    if not html:
        return []
    return sorted(set(re.findall(r'href="([A-Z][A-Z0-9\-]+)/"', html)))
