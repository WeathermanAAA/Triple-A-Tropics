#!/usr/bin/env python3
"""Build the per-basin verification scoreboard documents.

One JSON per NHC basin and season (``verification/{basin}{year}.json`` on R2):
the season's a-decks verified against the season's b-decks, scored by
:mod:`guidance.verify` - homogeneous panels, storm-block bootstrap intervals,
early and late never pooled, OCD5 as the protected no-skill baseline.

NHC BASINS ONLY, structurally: JTWC-basin decks have never carried official,
consensus, statistical or baseline aids - raw ensembles only - so there is
nothing there that this scoreboard could honestly score, and computing a
"consensus skill" for a basin with no consensus aid would be fabrication. The
viewer explains that on JTWC storm pages rather than showing a board.

The caveats the page MUST print ride in the document itself, so a viewer
change can never silently drop them:

  * scores are PROVISIONAL - the operational b-deck self-corrects (observed:
    a fix already published was later revised 140 kt -> 150 kt / 909 mb), so
    today's numbers can change retroactively until post-season reanalysis;
  * the public a-deck is FILTERED - every ECMWF-derived aid and UKM/UKMI/
    UEMN/FSSE/GFEX are withheld, so the models people most want compared
    CANNOT be scored, and the board says so instead of silently omitting them.

Run::

    python -m guidance.build_verification --out-dir verification
    python -m guidance.build_verification --basin ep --out-dir /tmp/v
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from guidance import atcf, verify
from guidance.build_guidance import discover_storms

log = logging.getLogger("verification-build")

NHC_BASINS = ("al", "ep", "cp")
SCHEMA_VERSION = 1

CAVEATS = {
    "provisional": (
        "Verified against the OPERATIONAL best track, which self-corrects: "
        "fixes already published are revised as new data arrives (observed "
        "this season: an intensity already carried at 140 kt was later "
        "revised to 150 kt / 909 mb). Every number here is provisional until "
        "the post-season reanalysis; treat this as a running scoreboard, not "
        "a final verification."),
    "filtered_deck": (
        "NHC's public a-deck withholds every ECMWF-derived aid (EMX/EMXI/"
        "EMX2, EEMN/EMNI, SHPE/DSPE/LGME, EAIO/EAMN) as well as UKM/UKMI, "
        "UEMN, FSSE and GFEX. Those models cannot be scored from public data "
        "- this board is not silently omitting them, it is unable to include "
        "them, and no public feed can."),
    "homogeneity": (
        "Every panel is HOMOGENEOUS: models are compared only on cases where "
        "every model in the panel has a forecast, so a model cannot look "
        "better by skipping hard cases. When that filter leaves too few "
        "cases, models are dropped (least coverage first) rather than the "
        "filter relaxed; lead times that still fall short are omitted."),
    "blocks": (
        "Confidence intervals block-bootstrap over STORMS, because "
        "consecutive 6-hourly forecasts of one storm are near-duplicates; "
        "bootstrapping individual forecasts would badly overstate "
        "confidence. A sample with a single storm shows no interval at all."),
    "early_late": (
        "EARLY aids (interpolated model output, the official forecast, "
        "consensus, statistical aids and the baseline) were available in "
        "time for the forecast cycle; LATE aids are raw model runs that "
        "arrive after the deadline, so part of their apparent skill is "
        "hindsight. The two are scored as separate panels and never pooled."),
    "jtwc": (
        "JTWC-basin (WP/IO/SH) decks carry no official, consensus, "
        "statistical or baseline aids - raw ensembles only - so an official/"
        "consensus scoreboard is structurally impossible there from public "
        "data."),
}


def build_basin(basin: str, year: int, *, opener=None) -> Optional[dict]:
    """The verification document for one NHC basin-season."""
    storms = [(b, cy) for b, cy in discover_storms(year, opener=opener)
              if b == basin]
    all_cases: list = []
    storm_rows: list = []
    for b, cy in storms:
        a = atcf.fetch_deck(b, cy, year, kind="a", opener=opener)
        bt = atcf.fetch_deck(b, cy, year, kind="b", opener=opener)
        if not a or not bt:
            continue
        a_rows, _ = atcf.parse_deck(a)
        b_rows, _ = atcf.parse_deck(bt)
        truth = verify.truth_from_bdeck(b_rows)
        sid = f"{b.upper()}{cy:02d}"
        cases = verify.cases_for_storm(sid, a_rows, truth)
        if not cases:
            continue
        all_cases.extend(cases)
        storm_rows.append({"storm": sid, "n_cases": len(cases),
                           "is_invest": cy >= 90})
    if not all_cases:
        return None

    panels: dict = {}
    for timing in ("early", "late"):
        panels[timing] = {
            "track": verify.score_panel(all_cases, basin, timing, "track"),
            "intensity": verify.score_panel(all_cases, basin, timing,
                                            "intensity"),
        }

    return {
        "schema": SCHEMA_VERSION,
        "basin": basin,
        "year": year,
        "generated_at": (dt.datetime.now(dt.timezone.utc)
                         .replace(microsecond=0).isoformat()
                         .replace("+00:00", "Z")),
        "truth": "NHC operational b-deck (ftp.nhc.noaa.gov/atcf/btk)",
        "taus": list(verify.TAUS),
        "min_cases": verify.MIN_CASES,
        "storms": storm_rows,
        "panels": panels,
        "unverifiable": list(atcf.WITHHELD_TECHS),
        "caveats": CAVEATS,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="verification")
    ap.add_argument("--year", type=int, default=dt.date.today().year)
    ap.add_argument("--basin", action="append", default=None,
                    choices=list(NHC_BASINS))
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    out_dir = Path(a.out_dir)
    n = 0
    for basin in (a.basin or NHC_BASINS):
        try:
            doc = build_basin(basin, a.year)
        except Exception as e:  # noqa: BLE001 - one basin must not sink the run
            log.warning("  %s: FAILED %s: %s", basin, type(e).__name__, e)
            continue
        if doc is None:
            log.info("  %s%d: no verifiable cases (skipped)", basin, a.year)
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{basin}{a.year}.json").write_text(
            json.dumps(doc, separators=(",", ":")), encoding="utf-8")
        n += 1
        e_tr = doc["panels"]["early"]["track"]["per_tau"]
        shown = [t for t, v in e_tr.items() if not v.get("omitted")]
        log.info("  %s%d -> %d storm(s), %d case(s); early-track taus shown: %s",
                 basin, a.year, len(doc["storms"]),
                 sum(s["n_cases"] for s in doc["storms"]), shown)
    log.info("verification: wrote %d document(s) to %s", n, out_dir)
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
