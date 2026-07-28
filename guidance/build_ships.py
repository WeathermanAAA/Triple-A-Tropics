#!/usr/bin/env python3
"""Build the per-storm SHIPS document the CycloLab panel renders.

Reads the RAW ARCHIVE this repo maintains in R2 (``ships/{SID}/{DTG}/…``,
see :mod:`guidance.harvest_ships`) rather than NHC directly, so the panel and
the archive can never disagree and a re-render costs no upstream traffic.

Output: ``cyclolab/{sid}/ships_v2.json`` - the newest cycle parsed in full,
plus a compact history of the intensity forecast so the panel can show how the
guidance has been trending rather than only where it stands now.

WHY ``_v2``: the render box already writes ``cyclolab/{sid}/ships.json`` in its
own shape and the shell's legacy SHIPS renderer reads it. Two writers on one
key with different shapes is how a schema starts flapping, so this takes a
separate key and the viewer prefers it, exactly as ``guidance_v2.json`` does.

THE ROUNDING RESIDUAL is carried through deliberately. TOTAL CHANGE does NOT
equal the sum of the printed components - measured 43.5% exact over the season,
residual up to 4 kt, because both sides are rounded to whole knots. The
residual is published as its own term so the waterfall closes exactly on the
stated total with the gap labelled. See :mod:`guidance.ships`.

Run::

    python -m guidance.build_ships --out-dir cyclolab_ships
    python -m guidance.build_ships --sid NHC_EP072026 --out-dir /tmp/s
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path
from typing import Callable, Optional

from guidance import ships as shipsmod

log = logging.getLogger("ships-build")

CDN = "https://cdn.triple-a-tropics.com"
ARCHIVE_INDEX = f"{CDN}/ships/index.json"

#: How many past cycles of the intensity forecast to carry, newest last. Six
#: cycles is 36 h - enough to see a trend without bloating the document.
HISTORY_CYCLES = 6

SCHEMA_VERSION = 2


def _http_get(url: str, timeout: float = 30.0) -> bytes:
    import requests
    r = requests.get(url, timeout=timeout,
                     headers={"User-Agent": "triple-a-tropics/ships-build"})
    r.raise_for_status()
    return r.content


def _sid_of(stem: str) -> str:
    return f"NHC_{stem[8:12]}20{stem[12:14]}"


def _dtg_of(stem: str) -> str:
    return "20" + stem[:8]


def load_index(opener: Optional[Callable] = None) -> dict:
    """``{sid: [stem, ...]}`` newest last, from the raw archive's index."""
    opener = opener or _http_get
    idx = json.loads(opener(ARCHIVE_INDEX))
    by_sid: dict = collections.defaultdict(list)
    for stem, sides in idx.get("archived", {}).items():
        if "ships" not in sides:
            continue          # a sibling-only stem has no bulletin to parse
        by_sid[_sid_of(stem)].append(stem)
    return {k: sorted(v) for k, v in by_sid.items()}


def _fetch_bulletin(stem: str, opener: Callable) -> Optional[str]:
    url = f"{CDN}/ships/{_sid_of(stem)}/{_dtg_of(stem)}/{stem}_ships.txt"
    try:
        return opener(url).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 - one cycle must not sink a storm
        log.warning("    fetch failed %s: %s", stem, e)
        return None


def build_storm(sid: str, stems: list, *, opener: Optional[Callable] = None,
                history: int = HISTORY_CYCLES) -> Optional[dict]:
    """Parse the newest cycle in full + a short intensity history."""
    opener = opener or _http_get
    if not stems:
        return None
    newest = stems[-1]
    txt = _fetch_bulletin(newest, opener)
    if txt is None:
        return None
    doc = shipsmod.parse_ships(txt)

    hist = []
    for stem in stems[-history:]:
        t = txt if stem == newest else _fetch_bulletin(stem, opener)
        if t is None:
            continue
        d = doc if stem == newest else shipsmod.parse_ships(t)
        tr = shipsmod.intensity_traces(d)
        hist.append({
            "cycle": _dtg_of(stem),
            "ships": tr["ships"],
            "lgem": tr["lgem"],
            "current_max_wind": tr["current_max_wind"],
        })

    out = {
        "schema": SCHEMA_VERSION,
        "sid": sid,
        "cycle": _dtg_of(newest),
        "stem": newest,
        "generated_at": (dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
                         .isoformat().replace("+00:00", "Z")),
        "source": "NHC /atcf/stext/ (archived by triple-a-tropics)",
        "archive_url": (f"{CDN}/ships/{sid}/{_dtg_of(newest)}/"
                        f"{newest}_ships.txt"),

        "header": doc["header"],
        "taus": doc["taus"],
        "contribution_taus": doc["contribution_taus"],
        "traces": shipsmod.intensity_traces(doc),
        "history": hist,

        "env": doc["env"],
        "storm_type": doc["storm_type"],
        "scalars": doc["scalars"],

        "contributions": doc["contributions"],
        "total_change": doc["total_change"],
        "rounding_residual": doc["rounding_residual"],

        "ri_predictors": doc["ri_predictors"],
        "ri_probabilities": doc["ri_probabilities"],
        "ri_matrix": doc["ri_matrix"],
        "annularity": doc["annularity"],
        "has_seef": doc["has_seef"],
        "has_erc": doc["has_erc"],

        # Stated on the panel, not buried: the printed components do not sum to
        # the printed total, and the reader is entitled to know the bar was
        # closed with a rounding term rather than silently fudged.
        "residual_note": (
            "SHIPS prints every contribution and the total rounded to whole "
            "knots, so the components do not sum exactly to TOTAL CHANGE "
            "(measured 43.5% exact across the 2026 season, residual up to "
            "4 kt). The waterfall closes on the published total with the "
            "difference shown as a labelled rounding term."),
    }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="cyclolab_ships")
    ap.add_argument("--sid", action="append", default=None,
                    help="build only these sids (repeatable)")
    ap.add_argument("--history", type=int, default=HISTORY_CYCLES)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    index = load_index()
    sids = a.sid or sorted(index)
    log.info("ships: %d storm(s) with archived bulletins", len(sids))

    out_dir = Path(a.out_dir)
    n = 0
    for sid in sids:
        stems = index.get(sid) or []
        if not stems:
            log.info("  %s: no archived bulletin (skipped)", sid)
            continue
        try:
            doc = build_storm(sid, stems, history=a.history)
        except Exception as e:  # noqa: BLE001
            log.warning("  %s: FAILED %s: %s", sid, type(e).__name__, e)
            continue
        if doc is None:
            continue
        d = out_dir / sid
        d.mkdir(parents=True, exist_ok=True)
        (d / "ships_v2.json").write_text(
            json.dumps(doc, separators=(",", ":")), encoding="utf-8")
        n += 1
        log.info("  %s -> cycle %s, %d env row(s), %d contribution(s), "
                 "%d RI predictor(s), %d history cycle(s)", sid, doc["cycle"],
                 len(doc["env"]), len(doc["contributions"]),
                 len(doc["ri_predictors"]), len(doc["history"]))

    log.info("ships: wrote %d document(s) to %s", n, out_dir)
    if sids and n == 0:
        log.error("ships: storms were listed but NOTHING built")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
