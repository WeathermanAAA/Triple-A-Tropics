"""Basin config, JSON emit, and the hard validation gate.

The gate runs on OUR OWN computed output before anything is written — if a
sentinel fails, the run exits non-zero and the workflow never uploads, so a
parsing regression can't silently poison the live records JSON.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from . import ENGINE_VERSION
from . import boards as boards_mod
from . import metrics, sources

CLIMO_START, CLIMO_END = 1991, 2020

# Per-product-basin records configuration. records_since = first season on the
# boards (WP starts at the JTWC best-track era so 1-min/10-min provenance is
# clean); satellite_era gates the "fewest/lowest" boards and the undercount
# caveats. season_start (m, d) = official season start where one exists.
RECORDS_BASINS: dict[str, dict] = {
    "al": {
        "name": "Atlantic", "full_name": "North Atlantic",
        "records_since": 1851, "satellite_era": 1966,
        "season_start": (6, 1), "hu_word": "hurricane",
        "wind_note": "NHC best track, 1-min sustained winds.",
        "sources_note": "HURDAT2 (NHC) through the last complete season; "
                        "IBTrACS v04r01 + live ATCF b-decks for the running "
                        "season.",
        "hurdat2_index": "https://www.nhc.noaa.gov/data/hurdat/",
        "hurdat2_prefix": "hurdat2-1851-",
        "hurdat2_fallback": "hurdat2-1851-2025-02272026.txt",
    },
    "ep": {
        "name": "East Pacific", "full_name": "Northeast Pacific",
        "records_since": 1949, "satellite_era": 1971,
        "season_start": (5, 15), "hu_word": "hurricane",
        "wind_note": "NHC/CPHC best track, 1-min sustained winds.",
        "sources_note": "HURDAT2 NE/NC-Pacific (NHC, includes Central "
                        "Pacific storms) through the last complete season; "
                        "IBTrACS v04r01 + live ATCF b-decks for the running "
                        "season.",
        "hurdat2_index": "https://www.nhc.noaa.gov/data/hurdat/",
        "hurdat2_prefix": "hurdat2-nepac-",
        "hurdat2_fallback": "hurdat2-nepac-1949-2025-02272026.txt",
    },
    "wp": {
        "name": "West Pacific", "full_name": "Western North Pacific",
        "records_since": 1945, "satellite_era": 1966,
        "season_start": None, "hu_word": "typhoon",
        "wind_note": "JTWC 1-min winds where available; JMA/WMO 10-min "
                     "winds ÷0.88 otherwise (1-min equivalent).",
        "sources_note": "IBTrACS v04r01 (JTWC columns) from 1945; live ATCF "
                        "b-decks for the running season. Storms are "
                        "attributed to their genesis basin, so Central "
                        "Pacific crossers (e.g. Ioke 2006) sit in the East "
                        "Pacific tables.",
    },
}

GANTT_MIN = {"al": 1950, "ep": 1950, "wp": 1950}


def _pct(mat: pd.DataFrame, q: float, cols: list[int]) -> list[float]:
    sub = mat[[c for c in cols if c in mat.columns]]
    return [round(float(v), 2) for v in np.percentile(sub.to_numpy(),
                                                     q, axis=1)]


def build_pace(pace: dict, seasons_tbl: pd.DataFrame, basin_cfg: dict,
               current_year: int) -> dict:
    """Climatology bands + record traces + current-season curves for the
    pace page. Climo = 1991–2020 (house standard). Record envelopes: counts
    use the satellite era (undercount), ACE uses all complete seasons —
    both exclude the running season (house envelope rule)."""
    out = {}
    climo_cols = list(range(CLIMO_START, CLIMO_END + 1))
    for kind in ("count", "ace"):
        mat = pace[kind]
        complete = [c for c in mat.columns if c != current_year]
        env_min = basin_cfg["satellite_era"] if kind == "count" \
            else basin_cfg["records_since"]
        env_cols = [c for c in complete if c >= env_min]
        env = mat[env_cols]
        finals = env.iloc[-1]
        rec_max_season = int(finals.idxmax())
        rec_min_season = int(finals.idxmin())
        cur = mat[current_year] if current_year in mat.columns else None
        out[kind] = {
            "mean": _pct(mat, 50, climo_cols),
            "climo_mean": [round(float(v), 2) for v in
                           mat[[c for c in climo_cols if c in mat.columns]]
                           .mean(axis=1)],
            "p25": _pct(mat, 25, climo_cols),
            "p75": _pct(mat, 75, climo_cols),
            "p10": _pct(mat, 10, climo_cols),
            "p90": _pct(mat, 90, climo_cols),
            "rec_max": {"season": rec_max_season,
                        "curve": [round(float(v), 2)
                                  for v in env[rec_max_season]]},
            "rec_min": {"season": rec_min_season,
                        "curve": [round(float(v), 2)
                                  for v in env[rec_min_season]]},
            "env_since": env_min,
            "current": None if cur is None
                       else [round(float(v), 2) for v in cur],
        }
    out["current_season"] = current_year
    return out


def compute_basin(basin: str, *, ibtracs_path: Path,
                  hurdat2_path: Path | None, fetch_live: bool,
                  current_year: int, log_prefix: str = "[records]") -> dict:
    cfg = RECORDS_BASINS[basin]
    live_cfg = None
    if fetch_live:
        from generate_ace_plot import BASINS as LIVE_BASINS
        live_cfg = LIVE_BASINS[basin]
    fixes = sources.assemble_basin(
        basin, hurdat2_path=hurdat2_path, ibtracs_path=ibtracs_path,
        records_since=cfg["records_since"], current_year=current_year,
        fetch_live=fetch_live, basin_cfg=live_cfg, log_prefix=log_prefix)
    storms = metrics.compute_storms(fixes)
    seasons_tbl = metrics.season_table(storms)
    conc_ts = metrics.concurrency(fixes, 34)
    conc_hu = metrics.concurrency(fixes, 64)
    all_seasons = sorted(seasons_tbl["season"].unique())
    pace = metrics.pace_matrices(storms, fixes, all_seasons)
    gantt = metrics.gantt_seasons(fixes, storms, GANTT_MIN[basin])
    board_list = boards_mod.build_boards(cfg, storms, seasons_tbl, fixes,
                                         conc_ts, conc_hu, current_year)
    return {"basin": basin, "cfg": cfg, "fixes": fixes, "storms": storms,
            "seasons_tbl": seasons_tbl, "boards": board_list,
            "pace": build_pace(pace, seasons_tbl, cfg, current_year),
            "gantt": gantt}


# ---------------------------------------------------------------------------
# Validation gate
# ---------------------------------------------------------------------------

def _storm(storms: pd.DataFrame, name: str, season: int):
    hit = storms[(storms["name"] == name) & (storms["season"] == season)]
    return hit.iloc[0] if len(hit) else None


def validate_or_die(results: dict[str, dict]) -> dict:
    """Sentinel checks over the computed output. Any failure = SystemExit.
    Returns the summary dict recorded in meta.json."""
    checks: list[tuple[str, bool, str]] = []

    def check(label: str, ok: bool, detail: str):
        checks.append((label, bool(ok), detail))

    if "wp" in results:
        tip = _storm(results["wp"]["storms"], "TIP", 1979)
        check("WP Tip 1979 min pressure = 870",
              tip is not None and tip["min_pres"] == 870.0,
              f"computed {None if tip is None else tip['min_pres']}")

    if "al" in results:
        st = results["al"]["storms"]
        wilma = _storm(st, "WILMA", 2005)
        gilbert = _storm(st, "GILBERT", 1988)
        ivan = _storm(st, "IVAN", 2004)
        check("AL Wilma 2005 min pressure = 882",
              wilma is not None and wilma["min_pres"] == 882.0,
              f"computed {None if wilma is None else wilma['min_pres']}")
        for h, floor in ((6, 50), (12, 80), (24, 90)):
            top = max(st[f"deep{h}"].dropna())
            wv = None if wilma is None else wilma[f"deep{h}"]
            check(f"AL deep{h} record is Wilma (≥{floor} mb)",
                  wilma is not None and wv == top and wv >= floor,
                  f"Wilma {wv} mb vs board top {top} mb")
        check("AL Gilbert 1988 min pressure = 888",
              gilbert is not None and gilbert["min_pres"] == 888.0,
              f"computed {None if gilbert is None else gilbert['min_pres']}")
        check("AL Ivan 2004 ACE ≈ 70.4",
              ivan is not None and abs(ivan["ace"] - 70.4) <= 0.5,
              f"computed {None if ivan is None else ivan['ace']}")
        tbl = results["al"]["seasons_tbl"]
        n2005 = tbl[tbl["season"] == 2005]["named"]
        check("AL 2005 named storms = 28",
              len(n2005) and int(n2005.iloc[0]) == 28,
              f"computed {None if not len(n2005) else int(n2005.iloc[0])}")

    if "ep" in results:
        st = results["ep"]["storms"]
        ioke = _storm(st, "IOKE", 2006)
        john = _storm(st, "JOHN", 1994)
        check("EP Ioke 2006 ACE ≈ 85.3 (genesis-basin, full lifetime)",
              ioke is not None and abs(ioke["ace"] - 85.3) <= 0.5,
              f"computed {None if ioke is None else ioke['ace']}")
        top_dur = max(st["dur_tc"].dropna())
        jv = None if john is None else john["dur_tc"]
        check("EP John 1994 longest-lived ≈ 30–31 d and rank 1",
              john is not None and jv == top_dur and 29.0 <= jv <= 32.0,
              f"John {jv} d vs board top {top_dur} d")

    passed = all(ok for _, ok, _ in checks)
    print("[records] validation gate:")
    for label, ok, detail in checks:
        print(f"[records]   {'PASS' if ok else 'FAIL'}  {label} ({detail})")
    if not passed:
        print("[records] VALIDATION FAILED — refusing to publish.",
              file=sys.stderr)
        raise SystemExit(1)
    return {label: {"ok": ok, "detail": detail}
            for label, ok, detail in checks}


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit(results: dict[str, dict], out_dir: Path, validation: dict,
         hurdat2_names: dict[str, str], current_year: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    generated = _iso_now()
    for basin, r in results.items():
        cfg = r["cfg"]
        provenance = {
            "ibtracs": "IBTrACS v04r01",
            "hurdat2": hurdat2_names.get(basin),
            "generated": generated,
            "engine": ENGINE_VERSION,
            "wind_note": cfg["wind_note"],
            "sources_note": cfg["sources_note"],
            "records_since": cfg["records_since"],
            "satellite_era": cfg["satellite_era"],
            "current_season": current_year,
        }
        records = {
            "basin": basin, "name": cfg["name"],
            "full_name": cfg["full_name"],
            "provenance": provenance,
            "boards": r["boards"],
        }
        (out_dir / f"{basin}_records.json").write_text(
            json.dumps(records, separators=(",", ":"), allow_nan=False))

        seasons = {
            "basin": basin, "name": cfg["name"],
            "provenance": provenance,
            "seasons": {
                str(int(row["season"])): {
                    "named": int(row["named"]), "hu": int(row["hu"]),
                    "major": int(row["major"]),
                    "ace": float(row["ace"]), "pdi": float(row["pdi"]),
                    "ri_storms": int(row["ri_storms"]),
                } for _, row in r["seasons_tbl"].iterrows()
            },
            "gantt": {str(k): v for k, v in sorted(r["gantt"].items())},
            "pace": r["pace"],
        }
        (out_dir / f"{basin}_seasons.json").write_text(
            json.dumps(seasons, separators=(",", ":"), allow_nan=False))

    # Cross-basin boards: pressure is averaging-free; ACE/duration carry the
    # 1-min/10-min disclaimer (WP winds are 1-min-equivalent via ÷0.88 —
    # the conversion is disclosed, never silent).
    xb_note = ("Cross-basin comparison. Atlantic/East Pacific winds are "
               "1-min (NHC); West Pacific winds are JTWC 1-min where "
               "available, otherwise 10-min ÷0.88 (a disclosed conversion), "
               "so treat close rankings as indicative, not exact.")
    all_storms = pd.concat(
        [r["storms"].assign(_basin=b.upper()) for b, r in results.items()],
        ignore_index=True)
    xb = []
    for key, title, col, reverse, disp, unit in (
            ("xb_min_pres", "Lowest central pressure (all basins)",
             "min_pres", False, lambda v: f"{v:.0f}", "mb"),
            ("xb_top_ace", "Highest single-storm ACE (all basins)",
             "ace", True, lambda v: f"{v:.1f}", "10⁴ kt²"),
            ("xb_dur_tc", "Longest-lived (all basins)",
             "dur_tc", True, lambda v: f"{v:.2f}", "days")):
        rows = []
        for _, s in all_storms.iterrows():
            v = s[col]
            if v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            rows.append({"value": float(v), "disp": disp(v),
                         "name": s["name"] or (s["atcf"] or "UNNAMED"),
                         "season": int(s["season"]), "sid": s["sid"],
                         "date": "", "extra": s["_basin"]})
        xb.append({"key": key, "page": "concurrency", "title": title,
                   "definition": "", "unit": unit, "note": xb_note,
                   "since": 1851,
                   "rows": boards_mod._rank(rows, reverse=reverse)})
    (out_dir / "global_records.json").write_text(
        json.dumps({"provenance": {"generated": generated,
                                   "engine": ENGINE_VERSION},
                    "boards": xb}, separators=(",", ":"), allow_nan=False))

    meta = {
        "generated": generated,
        "engine": ENGINE_VERSION,
        "ibtracs": "IBTrACS v04r01",
        "hurdat2": hurdat2_names,
        "current_season": current_year,
        "basins": sorted(results.keys()),
        "validation": validation,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=1))
    for f in sorted(out_dir.glob("*.json")):
        print(f"[records]   wrote {f.name} ({f.stat().st_size:,} B)")
