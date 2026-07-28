#!/usr/bin/env python3
"""Build the per-storm GUIDANCE document that ``/cyclolab/{sid}/`` renders.

One JSON per storm, published to R2 at ``cyclolab/{sid}/guidance_v2.json``.
Static, like everything else on this site: no per-user compute, no API.

WHY A v2 KEY. A guidance document is already published at
``cyclolab/{sid}/guidance.json`` by the render-box poller, and the explorer's
cockpit reads it. Overwriting that key from here would put two writers on one
object with different shapes. So this writes a SEPARATE key and the viewer
prefers it, falling back to the legacy document - the same never-regress
posture the HAFS manifest uses. The legacy writer keeps working untouched.

WHAT THIS ADDS over the legacy document, and why each is needed:

  * **OFCL and BEST.** The legacy document carries neither, so a spaghetti plot
    drawn from it has nothing to anchor on - the official forecast and the
    verifying best track are exactly what the reader is comparing the aids
    against.
  * **OCD5.** The no-skill baseline. An intensity chart without it cannot tell
    you whether any aid is adding value over climatology-and-persistence, which
    is the only question that matters when four aids disagree.
  * **Per-aid KIND and TIMING.** An ensemble mean is not a consensus and a
    baseline is not a forecast; an early aid was available for the cycle and a
    late one was not. See ``guidance.aids``.
  * **Consensus MEMBERSHIP in three states** - present / absent / WITHHELD.
    This is the strongest honesty feature available here. The public a-deck
    withholds every ECMWF-derived aid, so TVCN and RVCN are plottable but NOT
    independently reproducible: they were computed upstream from members we
    cannot see. Saying "member absent" would imply the member simply did not
    run; the third state is the whole point.
  * **Basin capability.** AL/EP/CP get the full suite. WP/IO/SH get raw
    ensemble tracks only - those decks have never carried official, consensus
    or statistical aids, so a consensus envelope there would be fabricated.

Run::

    python -m guidance.build_guidance --out-dir cyclolab_guidance
    python -m guidance.build_guidance --storm al02 --year 2026 --out-dir /tmp/g
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional, Sequence

from guidance import aids as aidcat
from guidance import atcf

log = logging.getLogger("guidance-build")

#: Basin letter in a storm id -> (basin slug, sid prefix used by CycloLab).
#: CycloLab keys storms as ``NHC_AL022026`` / ``JTWC_WP122026``.
BASIN_AGENCY = {
    "al": "NHC", "ep": "NHC", "cp": "NHC",
    "wp": "JTWC", "io": "JTWC", "sh": "JTWC",
}

#: Forecast hours a track/intensity trace is sampled at. The decks carry more
#: (out to 240 for some aids); this is the display grid.
TAU_GRID = (0, 12, 24, 36, 48, 60, 72, 96, 120, 144, 168)

SCHEMA_VERSION = 2


def sid_for(basin: str, cy: int, year: int) -> str:
    """The CycloLab storm id, e.g. ``NHC_AL022026`` / ``JTWC_WP122026``."""
    b = basin.lower()
    agency = BASIN_AGENCY.get(b, "NHC")
    return f"{agency}_{b.upper()}{int(cy):02d}{int(year)}"


def _traces(rows: Sequence[atcf.AidRow]) -> dict:
    """``{TECH: [{tau,lat,lon,vmax,mslp}, ...]}``, one point per TAU.

    Only the PRIMARY radii row contributes (``rad`` in (None, 34)): the 50 and
    64 kt rows repeat the same position and intensity, so counting them would
    triple every trace. This is the consumer-side half of the primary-key rule -
    RAD is part of the key precisely so these rows are distinguishable rather
    than deduplicated away.
    """
    by_tech: dict = defaultdict(dict)
    for r in rows:
        if r.rad not in (None, 34):
            continue
        # First writer wins per (tech, tau); rows are already QC'd, and a
        # genuine duplicate key is reported by the QC pass rather than merged.
        by_tech[r.tech].setdefault(r.tau, {
            "tau": r.tau,
            "lat": r.lat,
            "lon": r.lon,
            "vmax": r.vmax_kt,
            "mslp": r.mslp_hpa,
        })
    out = {}
    for tech, pts in by_tech.items():
        out[tech] = [pts[t] for t in sorted(pts)]
    return out


def _latest_cycle(rows: Sequence[atcf.AidRow]) -> Optional[dt.datetime]:
    """The newest synoptic time present. That IS the current guidance cycle."""
    dtgs = [r.dtg for r in rows]
    return max(dtgs) if dtgs else None


def consensus_membership(present: Sequence[str], basin: str) -> list:
    """Per consensus aid, its members in THREE states.

    ``withheld`` is the state that carries the honesty: the member exists and
    was produced, but NHC's public feed does not ship it, so the consensus is
    plottable and not reproducible. ``absent`` means the member simply is not in
    this storm's deck - a different and much weaker statement.

    Returns [] for a JTWC basin: those decks have no consensus aids, so there is
    no membership to show and inventing one would be the fabrication this whole
    module is built to avoid.
    """
    b = (basin or "").lower()
    if b in aidcat.JTWC_BASINS:
        return []
    have = {t.upper() for t in present}
    withheld = set(atcf.WITHHELD_TECHS)
    out = []
    for tech, members in atcf.CONSENSUS_MEMBERS.items():
        if tech not in have:
            continue
        states = []
        for m in members:
            if m in have:
                state = "present"
            elif m in withheld:
                state = "withheld"
            else:
                state = "absent"
            states.append({"tech": m, "state": state, "label": aidcat.label(m)})
        n_withheld = sum(1 for s in states if s["state"] == "withheld")
        out.append({
            "tech": tech,
            "label": aidcat.label(tech),
            "members": states,
            "n_present": sum(1 for s in states if s["state"] == "present"),
            "n_withheld": n_withheld,
            "n_absent": sum(1 for s in states if s["state"] == "absent"),
            # The claim the page must not make quietly.
            "reproducible": n_withheld == 0,
        })
    return out


def build_document(adeck_text: str, bdeck_text: Optional[str], *,
                   basin: str, cy: int, year: int,
                   now_iso: Optional[str] = None) -> dict:
    """The published per-storm guidance document. PURE - no I/O."""
    rows, qc = atcf.parse_deck(adeck_text)
    basin_l = basin.lower()

    cycle = _latest_cycle(rows)
    cyc_rows = [r for r in rows if r.dtg == cycle] if cycle else []
    traces = _traces(cyc_rows)

    # Best track: the whole b-deck, not just this cycle - it is the verifying
    # history the forecast traces are drawn against.
    best = []
    if bdeck_text:
        brows, _bqc = atcf.parse_deck(bdeck_text)
        for r in sorted(brows, key=lambda r: r.dtg):
            if r.rad not in (None, 34) or not r.has_position:
                continue
            best.append({
                "dtg": r.dtg.strftime("%Y%m%d%H"),
                "lat": r.lat, "lon": r.lon,
                "vmax": r.vmax_kt, "mslp": r.mslp_hpa,
            })

    present = sorted(traces)
    cap = aidcat.basin_capability(basin_l)

    # Per-aid metadata. classify() is basin-aware, so a JTWC aid can never come
    # back as CONSENSUS.
    meta = {}
    for tech in present:
        kind, timing = aidcat.classify(tech, basin_l)
        n_pos = sum(1 for p in traces[tech] if p["lat"] is not None)
        meta[tech] = {
            "kind": kind.value,
            "timing": timing.value,
            "label": aidcat.label(tech),
            "n_points": len(traces[tech]),
            "has_track": n_pos > 0,
            "has_intensity": any(p["vmax"] is not None for p in traces[tech]),
            "tau_max": max((p["tau"] for p in traces[tech]), default=None),
        }

    def _of_kind(k: aidcat.AidKind) -> list:
        return [t for t in present if meta[t]["kind"] == k.value]

    doc = {
        "schema": SCHEMA_VERSION,
        "sid": sid_for(basin_l, cy, year),
        "basin": basin_l,
        "cy": int(cy),
        "year": int(year),
        "generated_at": now_iso or (dt.datetime.now(dt.timezone.utc)
                                    .replace(microsecond=0).isoformat()
                                    .replace("+00:00", "Z")),
        "init_cycle": cycle.strftime("%Y%m%d%H") if cycle else None,
        "init_time": (cycle.replace(tzinfo=dt.timezone.utc).isoformat()
                      .replace("+00:00", "Z")) if cycle else None,
        "source": cap["source"],

        # What this basin can honestly support.
        "capability": cap,

        # The traces themselves.
        "aids": traces,
        "aid_meta": meta,
        "present_aids": present,

        # Roles, resolved server-side so the viewer never has to guess.
        "official": "OFCL" if "OFCL" in traces else None,
        "skill_baseline": next((t for t in ("OCD5", "CLP5", "SHF5")
                                if t in traces), None),
        "consensus_aids": _of_kind(aidcat.AidKind.CONSENSUS),
        "ensemble_mean_aids": _of_kind(aidcat.AidKind.ENSEMBLE_MEAN),
        "ensemble_members": _of_kind(aidcat.AidKind.ENSEMBLE_MEMBER),
        "early_aids": [t for t in present if meta[t]["timing"] == "early"],
        "late_aids": [t for t in present if meta[t]["timing"] == "late"],

        "best_track": best,

        # The honesty blocks.
        "consensus_membership": consensus_membership(present, basin_l),
        "filtered_deck": atcf.filtered_deck_notice(present),

        # What QC removed, so the page can say it rather than silently drop it.
        "qc": qc.as_dict(),
    }
    return doc


# ---------------------------------------------------------------------------
# Fetch + write
# ---------------------------------------------------------------------------
def build_storm(basin: str, cy: int, year: int, *,
                opener=None) -> Optional[dict]:
    """Fetch a storm's decks and build its document, or None if no a-deck."""
    a = atcf.fetch_deck(basin, cy, year, kind="a", opener=opener)
    if not a:
        return None
    b = atcf.fetch_deck(basin, cy, year, kind="b", opener=opener)
    return build_document(a, b, basin=basin, cy=cy, year=year)


def discover_storms(year: int, *, opener=None) -> list:
    """``[(basin, cy)]`` for every a-deck NHC currently publishes.

    Invests (cy >= 90) are included - they carry guidance and CycloLab has
    pages for them. Test decks (cy 80-89) are NOT: those are GSTEST/ATCFTEST
    fixtures with physically absurd values that would corrupt any view.
    """
    import re
    import requests
    url = "https://ftp.nhc.noaa.gov/atcf/aid_public/"
    body = (opener(url).decode("utf-8", "replace") if opener
            else requests.get(url, timeout=30).text)
    out = []
    for m in re.finditer(r"a([a-z]{2})(\d{2})(\d{4})\.dat\.gz", body):
        b, cy, yr = m.group(1), int(m.group(2)), int(m.group(3))
        if yr != int(year) or 80 <= cy <= 89:
            continue
        out.append((b, cy))
    return sorted(set(out))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="cyclolab_guidance",
                    help="write {sid}/guidance_v2.json under here")
    ap.add_argument("--year", type=int, default=dt.date.today().year)
    ap.add_argument("--storm", action="append", default=None,
                    help="basin+number, e.g. al02 (repeatable). "
                         "Default: every deck NHC publishes this year.")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if a.storm:
        targets = []
        for s in a.storm:
            s = s.strip().lower()
            targets.append((s[:2], int(s[2:])))
    else:
        targets = discover_storms(a.year)
    log.info("guidance: %d storm(s) to build for %d", len(targets), a.year)

    out_dir = Path(a.out_dir)
    n_ok = 0
    for basin, cy in targets:
        try:
            doc = build_storm(basin, cy, a.year)
        except Exception as e:  # noqa: BLE001 - one storm must not sink the run
            log.warning("  %s%02d: FAILED %s: %s", basin, cy,
                        type(e).__name__, e)
            continue
        if doc is None or not doc["present_aids"]:
            log.info("  %s%02d: no aids (skipped)", basin, cy)
            continue
        d = out_dir / doc["sid"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "guidance_v2.json").write_text(
            json.dumps(doc, separators=(",", ":")), encoding="utf-8")
        n_ok += 1
        log.info("  %s -> %d aids, cycle %s, %d consensus (%d not "
                 "reproducible)", doc["sid"], len(doc["present_aids"]),
                 doc["init_cycle"], len(doc["consensus_membership"]),
                 sum(1 for c in doc["consensus_membership"]
                     if not c["reproducible"]))

    log.info("guidance: wrote %d document(s) to %s", n_ok, out_dir)
    # An empty run in-season would prune the CDN, so fail loudly instead of
    # publishing nothing - the same posture the HAFS builder takes.
    if targets and n_ok == 0:
        log.error("guidance: storms were found but NOTHING built - refusing to "
                  "publish an empty set")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
