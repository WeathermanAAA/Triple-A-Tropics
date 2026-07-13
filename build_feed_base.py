#!/usr/bin/env python3
"""
build_feed_base.py
------------------
Write the slow-moving ARCHIVE BASE for each basin so the streaming intensity
poller can merge fresh live b-decks onto it and reproduce the live feed within
minutes of an advisory (instead of waiting on the 6 h cron).

Outputs (FEED_BASE_DIR, default feeds/base/):
    {basin}_ace_base.json     archive ACE base (curves, climo, past storms, the
                              current-year IBTrACS canon to merge live onto)
    {basin}_tracks_base.json  static tracks vocab + the current-year IBTrACS
                              tracks frame (the non-live backbone)

This is ADDITIVE and OFFLINE-SAFE: it does NOT touch the live
{basin}_{ace,tracks}_data.json the cron still writes (no cutover), and it
reuses the generators' loaders + ace_core's shared assembly verbatim - no ACE
or feed-assembly logic is reimplemented here. The base is exactly the cron's
--no-live intermediate (IBTrACS only); the poller adds the live current-season
slice with the SAME ace_core functions, so poller output == cron output for the
same fixes.

Field-ownership (confirmed against the live feeds):
  ACE base (cron-owned, slow):  doy, climo bands, cum curves for ALL past
    seasons, past storms_by_year, last_obs_doy, + current_year_canon (IBTrACS).
  ACE live (poller-owned):      current{} curve, today_doy, rankings+rank,
    all_years[current], storms_by_year[current], freshness stamps.
  Tracks base:                  basin/basin_name/year/vocab + current-year
    IBTrACS tracks frame (needed so the poller reproduces the cron's
    IBTrACS+live merge and keeps ace == tracks).
  Tracks live (poller-owned):   header, storms[], updated/freshness.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import sys
from pathlib import Path

import pandas as pd

import ace_core as ac
import generate_ace_plot as ag
import generate_tracks_plot as tg

HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("FEED_BASE_DIR", str(HERE / "feeds" / "base")))
BASINS = ["wp", "al", "ep"]


def _csv_path(cfg: dict) -> Path:
    return Path(os.environ.get(
        f"IBTRACS_{cfg['short'].upper()}_CSV",
        str(HERE / f"ibtracs.{cfg['ibtracs_file_code']}.list.v04r01.csv")))


def _clean(v):
    """JSON-safe scalar: NaN/NaT -> None, datetime -> ISO Z, numpy -> python."""
    if v is None:
        return None
    if isinstance(v, float):
        return None if math.isnan(v) else v
    if isinstance(v, dt.datetime):
        return ac.iso_z(v)
    if isinstance(v, (pd.Timestamp,)):
        return ac.iso_z(v.to_pydatetime())
    if hasattr(v, "item"):          # numpy scalar
        try:
            v = v.item()
        except Exception:
            return v
        return _clean(v)
    return v


def _records(df: pd.DataFrame) -> list[dict]:
    """Serialize a frame to a list of JSON-safe dicts (times -> ISO Z)."""
    return [{k: _clean(val) for k, val in row.items()}
            for row in df.to_dict("records")]


def _poller_cfg(basin: str) -> dict:
    """The minimal basin_cfg the poller needs for parse_bdeck + the fetch proxy
    chain + the IBTrACS/live merge (everything else lives in the base data)."""
    a = ag.BASINS[basin]
    t = tg.BASINS[basin]
    return {
        "short": a["short"],
        "agency_name": a["agency_name"],
        "invest_letter": t.get("invest_letter", ""),
        # multi-letter basins (EP also accepts "C"/Central Pacific); pollers
        # fall back to [invest_letter] when this key is absent (old bases).
        "invest_letters": t.get("invest_letters")
                          or ([t["invest_letter"]] if t.get("invest_letter")
                              else []),
        "atcf_patterns": a["atcf_patterns"],
    }


# ---------------------------------------------------------------------------
# ACE base
# ---------------------------------------------------------------------------
def build_ace_base(basin: str) -> dict:
    cfg = ag.BASINS[basin]
    csv_path = _csv_path(cfg)
    log = f"[{basin}-acebase]"
    df = pd.read_csv(csv_path, skiprows=[1], low_memory=False, na_values=[" ", ""])
    year = dt.date.today().year

    # Historical ACE points (every season EXCEPT the current calendar year). The
    # current-year curve column is the poller's job; the base carries the
    # IBTrACS canon for it to merge live onto. compute_ace_timeseries returns
    # (ace_points, trop_points); the archive base only needs the ACE-eligible
    # frame (the trop frame is Storm-Activity-Gantt-only).
    points, _trop = ag.compute_ace_timeseries(df, cfg, log_prefix=log)
    hist = points[points["season"] != year].copy()
    if hist.empty:
        cum_hist = pd.DataFrame(index=range(1, 367))
        cum_hist.index.name = "doy"
    else:
        cum_hist = ac.cumulative_by_doy(hist)

    # Climatology excludes the current year, so it depends only on the past
    # seasons in cum_hist (identical to the cron's climo, which also excludes
    # the current year). Single-sourced through ace_core.climatology.
    climo = ac.climatology(cum_hist, ag.CLIMO_START, ag.CLIMO_END,
                           exclude_years={year})

    last_obs_doy = ({} if hist.empty
                    else hist.groupby("season")["doy"].max().to_dict())
    storms_by_year = ac.extract_storms_by_year(hist, min_year=1970)

    # The current-year IBTrACS ACE canon (all natures, no live) - the backbone
    # the poller merges the live b-decks onto via ac.merge_named_sources.
    ib_cur = ag.current_year_ibtracs_fixes(df, cfg, year, log)

    # Full double precision preserved (json shortest-roundtrip repr) so the
    # poller's rankings/all_years match the cron exactly.
    cum_hist_json = {str(int(s)): [float(v) for v in cum_hist[s].values]
                     for s in cum_hist.columns}
    climo_json = {k: [float(v) for v in climo[k].values]
                  for k in ("min", "p10", "p25", "mean", "p75", "p90", "max")}

    return {
        "schema_version": 1,
        "kind": "ace_base",
        "basin": basin,
        "base_year": year,
        "doy": list(range(1, 367)),
        "cum_hist": cum_hist_json,
        "climo": climo_json,
        "storms_by_year": {str(y): v for y, v in storms_by_year.items()},
        "last_obs_doy": {str(int(s)): int(v) for s, v in last_obs_doy.items()},
        "current_year_canon": _records(ib_cur),
        "basin_cfg": _poller_cfg(basin),
        "generated_utc": ac.now_iso_z(),
    }


# ---------------------------------------------------------------------------
# Tracks base
# ---------------------------------------------------------------------------
def build_tracks_base(basin: str) -> dict:
    cfg = tg.BASINS[basin]
    csv_path = _csv_path(cfg)
    log = f"[{basin}-trkbase]"
    year = dt.date.today().year
    ibtracs_frame = tg.load_ibtracs_current_year(csv_path, cfg, year, log)
    return {
        "schema_version": 1,
        "kind": "tracks_base",
        "basin": basin,
        "basin_name": cfg["full_name"],
        "year": year,
        "vocab": cfg["vocab"],
        "current_year_ibtracs": _records(ibtracs_frame),
        "basin_cfg": _poller_cfg(basin),
        "generated_utc": ac.now_iso_z(),
    }


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Build the per-basin archive base feeds.")
    ap.add_argument("--basins", default=",".join(BASINS),
                    help="comma list of basins (default wp,al,ep)")
    args = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    for basin in [b.strip() for b in args.basins.split(",") if b.strip()]:
        ace_base = build_ace_base(basin)
        trk_base = build_tracks_base(basin)
        (OUT / f"{basin}_ace_base.json").write_text(
            json.dumps(ace_base, separators=(",", ":")), encoding="utf-8")
        (OUT / f"{basin}_tracks_base.json").write_text(
            json.dumps(trk_base, separators=(",", ":")), encoding="utf-8")
        print(f"[{basin}] wrote ace_base ({len(ace_base['cum_hist'])} past seasons, "
              f"{len(ace_base['current_year_canon'])} current-yr canon fixes) + "
              f"tracks_base ({len(trk_base['current_year_ibtracs'])} ibtracs obs)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
