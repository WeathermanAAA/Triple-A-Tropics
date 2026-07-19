"""sarobs.discover - page-parse discovery of storms + Level-2 passes.

The listing is a PHP page (the directory autoindex is closed), so discovery
is regex page-parsing:

  * ``sarwinds_tropical.php?year=YYYY`` lists every storm WITH data for that
    year (the authoritative storm set — an untasked storm simply is not
    listed). Storm ids look like ``AL012026_ARTHUR``.
  * ``sarwinds_tropical.php?year=YYYY&storm=ID`` carries hrefs to the
    per-pass products; the Level-2 wind NetCDF is ``..._wind_level2.nc``.

Pass identity is the filename STEM (everything before ``_wind_level2.nc``),
which embeds satellite, station, acquisition time, scene center and
polarization — unique per pass and stable, so it is the dedup watermark key.
"""
from __future__ import annotations

import datetime as dt
import re

from . import fetch

# in-scope basins (site scope): Atlantic, E/C Pacific, W Pacific
BASINS = ("AL", "EP", "CP", "WP")

_STORM_ID = re.compile(r"[?&]storm=([A-Z]{2}\d{6}_[A-Z0-9-]+)")
_YEAR_DIV = re.compile(r'<div\s+id="(\d{4})">')
_NC_HREF = re.compile(
    r'href="(AKDEMO_products/APL_winds/tropical/[^"]+_wind_level2\.nc)"')
# stem: SAT_STA_YYYY_MM_DD_HH_MN_SS_JULSEC_LON_LAT_POL_BEAM_PROC
_STEM_TIME = re.compile(
    r"^([A-Z0-9]+)_[A-Z0-9]+_(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})_(\d{2})_")
_STEM_POL = re.compile(r"_(VV|VH|HV|HH)_")


def year_block(html: str, year: int) -> str:
    """Cut the listing HTML to the requested year's block (the nav dropdown
    above it carries EVERY year's storm links — without the cut, discovery
    would sweep the whole archive every tick)."""
    m = _YEAR_DIV.search(html)
    starts = [(m.start(), m.group(1))
              for m in _YEAR_DIV.finditer(html)]
    for i, (pos, y) in enumerate(starts):
        if int(y) == year:
            end = starts[i + 1][0] if i + 1 < len(starts) else len(html)
            return html[pos:end]
    return ""


def storms_for_year(year: int, *, html: str | None = None) -> list[str]:
    """Storm ids with data for ``year``, in-scope basins only."""
    if html is None:
        html = fetch.get_text(f"{fetch.LISTING}?year={year}")
    if not html:
        return []
    block = year_block(html, year)
    ids = []
    for sid in _STORM_ID.findall(block):
        if sid[:2] in BASINS and sid not in ids:
            ids.append(sid)
    return ids


def stem_of(nc_relpath: str) -> str:
    return nc_relpath.rsplit("/", 1)[-1][: -len("_wind_level2.nc")]


def stem_time(stem: str) -> dt.datetime | None:
    m = _STEM_TIME.match(stem)
    if not m:
        return None
    try:
        return dt.datetime(int(m.group(2)), int(m.group(3)), int(m.group(4)),
                           int(m.group(5)), int(m.group(6)), int(m.group(7)),
                           tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def stem_sat(stem: str) -> str:
    return stem.split("_", 1)[0]


def stem_pol(stem: str) -> str | None:
    m = _STEM_POL.search(stem)
    return m.group(1) if m else None


def passes_for_storm(year: int, storm_id: str,
                     *, html: str | None = None) -> list[dict]:
    """Level-2 passes on a storm's page, sorted newest first. Each item:
    {stem, url, t (datetime|None), sat, pol}. Empty list when the storm has
    no page/data (the listing returns a generic index, not a 404)."""
    if html is None:
        html = fetch.get_text(
            f"{fetch.LISTING}?year={year}&storm={storm_id}")
    if not html:
        return []
    out, seen = [], set()
    for rel in _NC_HREF.findall(html):
        stem = stem_of(rel)
        if stem in seen:
            continue
        seen.add(stem)
        out.append({"stem": stem, "url": fetch.BASE + rel,
                    "t": stem_time(stem), "sat": stem_sat(stem),
                    "pol": stem_pol(stem)})
    out.sort(key=lambda p: p["t"] or dt.datetime.min.replace(
        tzinfo=dt.timezone.utc), reverse=True)
    return out


def storm_slug(storm_id: str) -> str:
    return storm_id.lower()


def storm_fields(storm_id: str) -> dict:
    """{atcf, basin, num, year, name} from an id like AL012026_ARTHUR."""
    head, _, name = storm_id.partition("_")
    return {"atcf": head.lower(), "basin": head[:2], "num": int(head[2:4]),
            "year": int(head[4:8]), "name": name.title()}
