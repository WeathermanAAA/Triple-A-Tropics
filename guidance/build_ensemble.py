#!/usr/bin/env python3
"""Build the per-storm ENSEMBLE TRACK ("paintball") document.

Two ensembles, two sources, zero tracker compute - both publishers already run
their own cyclone tracker and publish the result, so nothing here integrates a
model or chases a vortex:

  * **ECMWF ENS**, 51 members, from ECMWF's own open-data cyclone-track BUFR
    (``…-enfo-tf.bufr``). ONE file per cycle, ~1.4 MB, carrying every storm,
    every member and every step to 360 h.
  * **GEFS**, 31 members (AC00 + AP01..AP30), straight out of the ATCF a-deck
    this repo already ingests - no extra fetch at all.

WORKS IN EVERY BASIN, which the deck-derived guidance does not: the ECMWF BUFR
is global and does not depend on NHC's filtered a-deck, so a West Pacific storm
gets a real 51-member ECMWF paintball even though its consensus and official
aids do not exist. That makes this the one guidance product with the same
quality everywhere.

STORM MATCHING IS BY NAME FIRST, AND THAT IS NOT FUSSiness. ECMWF's storm
identifier does NOT agree with the agency's: on 2026-07-28 DOLPHIN is ``15W``
in the BUFR and ``WP12`` to JTWC, because ECMWF numbers West Pacific systems on
its own sequence. Matching on the identifier alone silently attaches one
storm's ensemble to another storm's page - the worst failure this product can
have. So the name is authoritative when both sides have one, the
basin-letter + number is a fallback only for unnamed systems, and anything
that resolves ambiguously is dropped rather than guessed.

Output ``cyclolab/{sid}/ensemble_v2.json``. Member tracks are stored as
PARALLEL ARRAYS rather than a list of point objects - 51 members x 60 steps is
~3,000 points per storm, and the object-per-point shape triples the payload for
no gain.

Run::

    python -m guidance.build_ensemble --out-dir cyclolab_ens
    python -m guidance.build_ensemble --cycle 2026072800 --out-dir /tmp/e
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import re
import sys
from pathlib import Path
from typing import Callable, Optional

from guidance import aids as aidcat
from guidance import atcf

log = logging.getLogger("ensemble-build")

ECMWF_BUFR = ("https://data.ecmwf.int/forecasts/{ymd}/{hh}z/ifs/0p25/enfo/"
              "{ymd}{hh}0000-360h-enfo-tf.bufr")
#: ECMWF open data publishes 00/12Z ensemble cyclone tracks.
ECMWF_CYCLES = ("00", "12")

SCHEMA_VERSION = 2

#: ECMWF basin letters -> our basin slug. Their storm NUMBER is their own
#: sequence and must not be trusted against ours (see the module docstring).
_ECMWF_BASIN = {"L": "al", "E": "ep", "C": "cp", "W": "wp",
                "A": "io", "B": "io", "S": "sh", "P": "sh", "U": "sh"}


def _http_get(url: str, timeout: float = 180.0) -> bytes:
    import requests
    r = requests.get(url, timeout=timeout,
                     headers={"User-Agent": "triple-a-tropics/ensemble"})
    r.raise_for_status()
    return r.content


# ---------------------------------------------------------------------------
# ECMWF ENS, from the published cyclone-track BUFR
# ---------------------------------------------------------------------------
def decode_ecmwf_bufr(raw: bytes) -> list:
    """``[{storm_id, name, basin, members:[…]}]`` from one enfo-tf BUFR.

    Requires eccodes. Each BUFR message is one storm; within it the arrays are
    member-major, so the per-member stride is len(values)//n_members.
    """
    import eccodes as ec
    import tempfile

    out = []
    with tempfile.NamedTemporaryFile(suffix=".bufr") as tf:
        tf.write(raw)
        tf.flush()
        with open(tf.name, "rb") as f:
            while True:
                h = ec.codes_bufr_new_from_file(f)
                if h is None:
                    break
                try:
                    ec.codes_set(h, "unpack", 1)
                    rec = _one_bufr_storm(ec, h)
                    if rec:
                        out.append(rec)
                except Exception as e:  # noqa: BLE001 - one storm, not the run
                    log.warning("  BUFR message skipped: %s: %s",
                                type(e).__name__, e)
                finally:
                    ec.codes_release(h)
    return out


def _arr(ec, h, key):
    try:
        return list(ec.codes_get_array(h, key))
    except Exception:
        return None


def _clean(v, lo, hi, scale=1.0):
    """A BUFR value -> float in range, or None. eccodes yields very large
    sentinels for missing, so a range test is the reliable filter."""
    if v is None:
        return None
    try:
        x = float(v) * scale
    except (TypeError, ValueError):
        return None
    if not (lo <= x <= hi):
        return None
    return round(x, 2)


def _demux(arr, sub, n_subsets, skip=0):
    """De-interleave one member's series out of a COMPRESSED BUFR array.

    These messages are compressed with one SUBSET PER MEMBER
    (``compressedData=1``, ``numberOfSubsets=51``), and eccodes returns the
    full compressed array for every key regardless of ``extractSubset`` - that
    key silently does nothing here, returning identical arrays for every
    subset. The real layout is SUBSET-MINOR: ``value[occurrence * n_subsets +
    member]``.

    Getting this wrong does not fail loudly, it produces a plausible-looking
    track. A naive member-major stride yielded positions that jittered by whole
    degrees between "consecutive" 6-hourly steps and intensities swinging
    69->96->79 kt; with the correct de-interleave the same member steps a
    physically sane 0.4-0.8 deg per 6 h. Smoothness is the check that
    distinguishes them, so it is asserted in the tests.
    """
    a = arr[skip:]
    n = len(a) // n_subsets
    return [a[i * n_subsets + sub] for i in range(n)]


def _one_bufr_storm(ec, h) -> Optional[dict]:
    try:
        sid = (ec.codes_get(h, "#1#stormIdentifier") or "").strip()
        name = (ec.codes_get(h, "#1#longStormName") or "").strip()
    except Exception:
        return None
    mem = _arr(ec, h, "ensembleMemberNumber")
    lat = _arr(ec, h, "latitude")
    lon = _arr(ec, h, "longitude")
    if not mem or not lat or not lon:
        return None
    wind = _arr(ec, h, "windSpeedAt10M") or []
    pres = _arr(ec, h, "pressureReducedToMeanSeaLevel") or []
    tp = _arr(ec, h, "timePeriod") or []

    members = sorted({int(m) for m in mem if m is not None})
    ns = len(members)
    if not ns:
        return None
    taus = sorted({int(t) for t in tp if t is not None and t >= 0})
    if not taus:
        return None

    # lat/lon carry TWO positions per step - the storm centre and the location
    # of the maximum wind - preceded by one analysis value, so 2*steps+1
    # occurrences against the wind array's one-per-step. The centre is the
    # first of each pair; taking every other value after the leading one is
    # what separates them.
    out_members = []
    for i, m in enumerate(members):
        la_all = _demux(lat, i, ns, skip=1)
        lo_all = _demux(lon, i, ns, skip=1)
        la = [_clean(v, -90, 90) for v in la_all[0::2]]
        lo_ = [_clean(v, -360, 360) for v in lo_all[0::2]]
        # BUFR wind is m/s; the site speaks knots everywhere else.
        wd = [_clean(v, 0, 200, 1.943844) for v in _demux(wind, i, ns)]
        # Bounds are in the OUTPUT units. BUFR ships Pa (96800) and the site
        # speaks hPa, so the scale runs first and the range must be 800-1100 -
        # checking Pa bounds against an already-scaled value rejected every
        # reading and silently emptied the whole field.
        pr = [_clean(v, 800, 1100, 0.01) for v in _demux(pres, i, ns)] \
            if pres else []
        n = min(len(taus), len(la), len(lo_))
        if not n or not any(x is not None for x in la[:n]):
            continue
        out_members.append({
            "id": m,
            "lat": la[:n], "lon": lo_[:n],
            "vmax": (wd + [None] * n)[:n],
            "mslp": (pr + [None] * n)[:n],
        })
    if not out_members:
        return None
    n = len(out_members[0]["lat"])
    return {
        "storm_id": sid, "name": name,
        "basin": _ECMWF_BASIN.get(sid[-1:].upper(), ""),
        "taus": taus[:n], "members": out_members,
    }


def fetch_ecmwf(cycle: str, opener: Optional[Callable] = None) -> list:
    """Decode the ECMWF ensemble tracks for ``YYYYMMDDHH``, or [] if absent."""
    opener = opener or _http_get
    ymd, hh = cycle[:8], cycle[8:10]
    if hh not in ECMWF_CYCLES:
        return []
    url = ECMWF_BUFR.format(ymd=ymd, hh=hh)
    try:
        raw = opener(url)
    except Exception as e:  # noqa: BLE001 - a not-yet-published cycle is normal
        log.info("  ECMWF %s not available (%s)", cycle, e)
        return []
    storms = decode_ecmwf_bufr(raw)
    log.info("  ECMWF %s: %d storm(s), %d member(s) each",
             cycle, len(storms), storms[0]["members"].__len__() if storms else 0)
    return storms


def _norm_name(s: str) -> str:
    return re.sub(r"[^A-Z]", "", (s or "").upper())


def match_ecmwf(storms: list, sid: str, name: str) -> Optional[dict]:
    """Find the ECMWF record for one of OUR storms.

    NAME FIRST. ECMWF's storm number is its own sequence and disagrees with the
    agencies' - DOLPHIN is 15W to ECMWF and WP12 to JTWC - so matching on the
    identifier attaches the wrong ensemble to the page. The identifier is used
    only as a fallback for unnamed systems, and only when it is unambiguous.
    """
    want_name = _norm_name(name)
    if want_name and want_name not in ("INVEST",):
        hits = [s for s in storms if _norm_name(s["name"]) == want_name]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            log.warning("    %s: name %r matched %d ECMWF storms - dropped "
                        "rather than guessed", sid, name, len(hits))
            return None
    # Fallback: basin + number, for systems with no name on either side.
    m = re.match(r"^[A-Z]+_([A-Z]{2})(\d{2})(\d{4})$", sid or "")
    if not m:
        return None
    basin, num = m.group(1).lower(), int(m.group(2))
    hits = [s for s in storms
            if s["basin"] == basin and s["storm_id"][:2].isdigit()
            and int(s["storm_id"][:2]) == num]
    return hits[0] if len(hits) == 1 else None


# ---------------------------------------------------------------------------
# GEFS, straight from the a-deck we already ingest
# ---------------------------------------------------------------------------
def gefs_from_adeck(basin: str, cy: int, year: int,
                    opener: Optional[Callable] = None) -> Optional[dict]:
    """The 31 GEFS members (AC00 + AP01..AP30) for one storm's newest cycle."""
    text = atcf.fetch_deck(basin, cy, year, kind="a", opener=opener)
    if not text:
        return None
    rows, _qc = atcf.parse_deck(text)
    if not rows:
        return None
    cycle = max(r.dtg for r in rows)
    per: dict = {}
    for r in rows:
        if r.dtg != cycle or r.rad not in (None, 34):
            continue
        kind, _ = aidcat.classify(r.tech, basin)
        if kind is not aidcat.AidKind.ENSEMBLE_MEMBER:
            continue
        per.setdefault(r.tech, {})[r.tau] = r
    if not per:
        return None
    taus = sorted({t for v in per.values() for t in v})
    members = []
    for tech in sorted(per):
        pts = per[tech]
        members.append({
            "id": tech,
            "lat": [pts[t].lat if t in pts else None for t in taus],
            "lon": [pts[t].lon if t in pts else None for t in taus],
            "vmax": [pts[t].vmax_kt if t in pts else None for t in taus],
            "mslp": [pts[t].mslp_hpa if t in pts else None for t in taus],
        })
    return {"cycle": cycle.strftime("%Y%m%d%H"), "taus": taus,
            "members": members}


# ---------------------------------------------------------------------------
def build_document(sid: str, name: str, basin: str, cy: int, year: int, *,
                   ecmwf_storms: list, opener: Optional[Callable] = None,
                   now_iso: Optional[str] = None) -> Optional[dict]:
    sources = []

    ec_rec = match_ecmwf(ecmwf_storms, sid, name)
    if ec_rec:
        sources.append({
            "model": "ecmwf_ens", "label": "ECMWF ENS",
            "n_members": len(ec_rec["members"]),
            "taus": ec_rec["taus"], "members": ec_rec["members"],
            "matched_by": "name" if _norm_name(name) else "id",
            "upstream_id": ec_rec["storm_id"],
        })

    gefs = gefs_from_adeck(basin, cy, year, opener=opener)
    if gefs:
        sources.append({
            "model": "gefs", "label": "GEFS",
            "n_members": len(gefs["members"]),
            "taus": gefs["taus"], "members": gefs["members"],
            "matched_by": "adeck", "upstream_id": f"{basin.upper()}{cy:02d}",
        })

    if not sources:
        return None
    return {
        "schema": SCHEMA_VERSION,
        "sid": sid, "name": name, "basin": basin,
        "generated_at": now_iso or (dt.datetime.now(dt.timezone.utc)
                                    .replace(microsecond=0).isoformat()
                                    .replace("+00:00", "Z")),
        "sources": sources,
        "note": ("Ensemble member tracks as published by each centre's own "
                 "cyclone tracker - no tracking is done here. ECMWF ENS comes "
                 "from ECMWF open data and is available in EVERY basin, "
                 "including those where the public a-deck carries no official "
                 "or consensus aids at all."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="cyclolab_ens")
    ap.add_argument("--cycle", default=None, help="YYYYMMDDHH (default: newest)")
    ap.add_argument("--year", type=int, default=dt.date.today().year)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cycle = a.cycle
    if not cycle:
        now = dt.datetime.now(dt.timezone.utc)
        hh = "12" if now.hour >= 16 else "00"
        d = now if now.hour >= 4 else now - dt.timedelta(days=1)
        cycle = d.strftime("%Y%m%d") + hh
    log.info("ensemble: cycle %s", cycle)
    ecmwf = fetch_ecmwf(cycle)

    from guidance.build_guidance import BASIN_AGENCY, discover_storms, sid_for
    targets = discover_storms(a.year)
    # ECMWF is global, so JTWC-basin storms get a paintball too even though
    # NHC publishes no decks for them. They are added from the BUFR itself.
    names: dict = {}
    for s in ecmwf:
        b = s["basin"]
        if not b or not s["storm_id"][:2].isdigit():
            continue
        agency = BASIN_AGENCY.get(b, "NHC")
        names[f"{agency}_{b.upper()}{int(s['storm_id'][:2]):02d}{a.year}"] = s

    out_dir = Path(a.out_dir)
    n = 0
    seen = set()
    for basin, cy in targets:
        sid = sid_for(basin, cy, a.year)
        rec = match_ecmwf(ecmwf, sid, "")
        doc = build_document(sid, (rec or {}).get("name", ""), basin, cy, a.year,
                             ecmwf_storms=ecmwf)
        if not doc:
            continue
        seen.add(sid)
        d = out_dir / sid
        d.mkdir(parents=True, exist_ok=True)
        (d / "ensemble_v2.json").write_text(
            json.dumps(doc, separators=(",", ":")), encoding="utf-8")
        n += 1
        log.info("  %s -> %s", sid,
                 ", ".join(f"{s['label']} {s['n_members']}m" for s in doc["sources"]))
    log.info("ensemble: wrote %d document(s) to %s", n, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
