"""Source parsers + per-basin assembly for the records engine.

Everything funnels into one canonical per-fix DataFrame (see package
docstring). HURDAT2 is parsed line-by-line (tiny files); IBTrACS is parsed
vectorized (the WP list alone is ~240k rows). The live b-deck tail reuses
generate_ace_plot.fetch_live_season + ace_core.parse_bdeck so a live storm has
the exact same fix set here as on the ACE/tracks products.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

import ace_core as ac

CANON_COLS = ["sid", "atcf", "name", "season", "time", "lat", "lon",
              "wind", "pres", "status", "trop", "syn", "src"]

# HURDAT2 tropical/subtropical statuses (EX/LO/WV/DB excluded everywhere).
HURDAT2_TROP = {"TD", "TS", "HU", "SS", "SD"}
# IBTrACS tropical/subtropical NATUREs; NR is accepted only on PROVISIONAL
# rows (current season before NCEI QC — the house rule from the ACE product).
IBTRACS_TROP = {"TS", "SS"}

SIX_HOURLY = {0, 6, 12, 18}

_PLACEHOLDER_NAMES = {"", "UNNAMED", "NAMELESS", "INVEST", "NOT_NAMED"}


def _clean_name(raw) -> str:
    s = str(raw or "").strip().upper()
    if s in _PLACEHOLDER_NAMES or re.fullmatch(r"GENESIS\d+", s):
        return ""
    return s


# ---------------------------------------------------------------------------
# HURDAT2
# ---------------------------------------------------------------------------

_H2_HEADER_RE = re.compile(r"^(AL|EP|CP)(\d{2})(\d{4})\s*$")


def _h2_latlon(tok: str) -> float:
    tok = tok.strip()
    if not tok:
        return math.nan
    hemi = tok[-1].upper()
    try:
        v = float(tok[:-1])
    except ValueError:
        return math.nan
    if hemi in ("S", "W"):
        v = -v
    return v


def parse_hurdat2(path: Path, src: str = "hurdat2") -> pd.DataFrame:
    """Parse a HURDAT2 txt file into the canonical schema. Keeps every row
    (including intermediate L/I entries — ``syn`` is what gates the sums)."""
    rows: list[dict] = []
    sid = atcf = name = None
    season = 0
    for line in path.read_text().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if not parts or not parts[0]:
            continue
        m = _H2_HEADER_RE.match(parts[0])
        if m and len(parts) <= 4:
            atcf = parts[0]
            sid = f"HURDAT2_{atcf}"
            name = _clean_name(parts[1])
            season = int(m.group(3))
            continue
        if sid is None or len(parts) < 8:
            continue
        try:
            t = dt.datetime.strptime(parts[0] + parts[1], "%Y%m%d%H%M")
        except ValueError:
            continue
        status = parts[3].upper()
        wind = float(parts[6]) if parts[6] else math.nan
        if wind < 0:
            wind = math.nan
        pres = float(parts[7]) if len(parts) > 7 and parts[7] else math.nan
        if pres <= 0:
            pres = math.nan
        lon = _h2_latlon(parts[5])
        if lon < -180:
            lon += 360.0
        rows.append({
            "sid": sid, "atcf": atcf, "name": name, "season": season,
            "time": t, "lat": _h2_latlon(parts[4]), "lon": lon,
            "wind": wind, "pres": pres, "status": status,
            "trop": status in HURDAT2_TROP,
            "syn": t.hour in SIX_HOURLY and t.minute == 0,
            "src": src,
        })
    df = pd.DataFrame(rows, columns=CANON_COLS)
    # Defensive: one fix per (storm, timestamp) — the L/I row IS the synoptic
    # fix when it lands exactly on a synoptic hour.
    df = df.drop_duplicates(subset=["sid", "time"], keep="first")
    return df.sort_values(["sid", "time"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# IBTrACS
# ---------------------------------------------------------------------------

_IBTRACS_USECOLS = ["SID", "SEASON", "BASIN", "NAME", "ISO_TIME", "NATURE",
                    "LAT", "LON", "TRACK_TYPE", "USA_ATCF_ID",
                    "USA_WIND", "USA_PRES", "WMO_WIND", "WMO_PRES",
                    "TOKYO_WIND", "TOKYO_PRES"]

# Pressure fallback mirrors the wind preference order (USA first), so a fix's
# wind and pressure come from the same agency wherever possible.
_PRES_FOR_WIND = {"USA_WIND": "USA_PRES", "WMO_WIND": "WMO_PRES",
                  "TOKYO_WIND": "TOKYO_PRES"}


def load_ibtracs(path: Path, basin_short: str,
                 seasons: tuple[int, int] | None = None,
                 src: str = "ibtracs") -> pd.DataFrame:
    """Vectorized IBTrACS list-file → canonical frame.

    Only main + PROVISIONAL tracks; wind is assembled with the exact
    ace_core.WIND_PREFERENCE chain for ``basin_short`` (1-min equivalent);
    genesis-basin filtering happens later in assembly (the list files carry
    every storm that ever ENTERED the basin, e.g. Ioke sits in the WP file
    with genesis BASIN=EP).
    """
    df = pd.read_csv(path, usecols=lambda c: c in _IBTRACS_USECOLS,
                     skiprows=[1], low_memory=False,
                     na_values=[" ", ""], keep_default_na=True)
    df = df[df["TRACK_TYPE"].astype(str).str.strip()
            .isin(["main", "PROVISIONAL"])].copy()
    df["SEASON"] = pd.to_numeric(df["SEASON"], errors="coerce")
    df = df.dropna(subset=["SEASON"])
    df["SEASON"] = df["SEASON"].astype(int)
    if seasons is not None:
        df = df[(df["SEASON"] >= seasons[0]) & (df["SEASON"] <= seasons[1])]
    df["ISO_TIME"] = pd.to_datetime(df["ISO_TIME"], errors="coerce")
    df = df.dropna(subset=["ISO_TIME"]).copy()

    for col in ("USA_WIND", "WMO_WIND", "TOKYO_WIND",
                "USA_PRES", "WMO_PRES", "TOKYO_PRES", "LAT", "LON"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = math.nan

    # Wind: first available column per ace_core preference, ×factor (10-min
    # sources ÷0.88 → 1-min equivalent). Pressure follows the same agency
    # order (no conversion — pressure is averaging-free).
    wind = pd.Series(math.nan, index=df.index)
    pres = pd.Series(math.nan, index=df.index)
    for col, factor in ac.WIND_PREFERENCE[basin_short]:
        if col not in df.columns:
            continue
        take = wind.isna() & df[col].notna()
        wind[take] = df.loc[take, col] * factor
        pcol = _PRES_FOR_WIND.get(col)
        if pcol and pcol in df.columns:
            ptake = pres.isna() & df[pcol].notna()
            pres[ptake] = df.loc[ptake, pcol]
    # A fix can report pressure without wind — keep any remaining pressure in
    # the same preference order.
    for pcol in ("USA_PRES", "WMO_PRES", "TOKYO_PRES"):
        ptake = pres.isna() & df[pcol].notna()
        pres[ptake] = df.loc[ptake, pcol]
    pres[pres <= 0] = math.nan

    nature = df["NATURE"].astype(str).str.strip().str.upper()
    provisional = df["TRACK_TYPE"].astype(str).str.strip() == "PROVISIONAL"
    trop = nature.isin(IBTRACS_TROP) | (provisional & (nature == "NR"))

    lon = df["LON"].copy()
    lon[lon > 180] -= 360.0

    out = pd.DataFrame({
        "sid": df["SID"].astype(str),
        "atcf": df["USA_ATCF_ID"].astype(str).str.strip().str.upper()
                  .replace({"NAN": ""}),
        "name": df["NAME"].map(_clean_name),
        "season": df["SEASON"],
        "time": df["ISO_TIME"],
        "lat": df["LAT"], "lon": lon,
        "wind": wind, "pres": pres,
        "status": nature,
        "trop": trop,
        "syn": df["ISO_TIME"].dt.hour.isin(SIX_HOURLY)
               & (df["ISO_TIME"].dt.minute == 0),
        "src": src,
    })
    out["_basin_col"] = df["BASIN"].astype(str).str.strip().str.upper()
    return out.sort_values(["sid", "time"]).reset_index(drop=True)


def genesis_basin(frame: pd.DataFrame) -> pd.Series:
    """First-row BASIN per SID (frame must be time-sorted)."""
    return frame.groupby("sid", sort=False)["_basin_col"].first()


# ---------------------------------------------------------------------------
# Live b-deck tail (current season)
# ---------------------------------------------------------------------------

def live_canonical(season: int, basin_cfg: dict, log_prefix: str = "[records]",
                   fetcher=None) -> pd.DataFrame:
    """Fetch the live b-deck sweep (via generate_ace_plot.fetch_live_season)
    and map parse_bdeck's schema onto the canonical one. Empty frame on any
    failure — live is a top-up, never a hard dependency."""
    if fetcher is None:
        from generate_ace_plot import fetch_live_season as fetcher
    live = fetcher(season, basin_cfg, log_prefix)
    if live is None or len(live) == 0:
        return pd.DataFrame(columns=CANON_COLS)
    deck = basin_cfg["atcf_prefix"][1:].upper()

    def _atcf(row):
        n = row.get("storm_num")
        if n is None or (isinstance(n, float) and math.isnan(n)):
            return ""
        return f"{deck}{int(n):02d}{season}"

    out = pd.DataFrame({
        "sid": live["SID"].astype(str),
        "atcf": live.apply(_atcf, axis=1),
        "name": live["NAME"].map(_clean_name),
        "season": season,
        "time": pd.to_datetime(live["time"]),
        "lat": pd.to_numeric(live.get("lat"), errors="coerce"),
        "lon": pd.to_numeric(live.get("lon"), errors="coerce"),
        "wind": pd.to_numeric(live.get("wind_kt"), errors="coerce"),
        "pres": pd.to_numeric(live.get("pressure_mb"), errors="coerce"),
        "status": live["ace_nature"].astype(str).str.upper(),
        # parse_bdeck maps ATCF dev levels through STATUS_TO_NATURE: tropical
        # (incl. TD) → TS, subtropical → SS. ET/DS stay excluded.
        "trop": live["ace_nature"].astype(str).str.upper().isin({"TS", "SS"}),
        "syn": pd.to_datetime(live["time"]).dt.hour.isin(SIX_HOURLY)
               & (pd.to_datetime(live["time"]).dt.minute == 0),
        "src": "live",
    })
    out.loc[out["lon"] > 180, "lon"] -= 360.0
    return out.sort_values(["sid", "time"]).reset_index(drop=True)


def merge_live_tail(archive: pd.DataFrame, live: pd.DataFrame) -> pd.DataFrame:
    """Append live fixes newer than each storm's archive coverage, keyed by
    ATCF id; live-only storms come in whole. Archive rows always win on
    overlap (IBTrACS provisional is the groomed spine; the b-deck adds the
    last days between IBTrACS refreshes)."""
    if live.empty:
        return archive
    pieces = [archive]
    last_by_atcf = (archive[archive["atcf"] != ""]
                    .groupby("atcf")["time"].max().to_dict())
    sid_by_atcf = (archive[archive["atcf"] != ""]
                   .groupby("atcf")["sid"].first().to_dict())
    name_by_atcf = {}
    named = archive[(archive["atcf"] != "") & (archive["name"] != "")]
    if len(named):
        name_by_atcf = named.groupby("atcf")["name"].first().to_dict()
    for atcf, grp in live.groupby("atcf", sort=False):
        if not atcf:
            continue
        cutoff = last_by_atcf.get(atcf)
        tail = grp if cutoff is None else grp[grp["time"] > cutoff]
        if tail.empty:
            continue
        tail = tail.copy()
        # Stitch identity: live fixes adopt the archive SID/name so the storm
        # stays ONE storm in every per-SID computation.
        if atcf in sid_by_atcf:
            tail["sid"] = sid_by_atcf[atcf]
        if name_by_atcf.get(atcf):
            tail["name"] = name_by_atcf[atcf]
        pieces.append(tail)
    out = pd.concat(pieces, ignore_index=True)
    return out.sort_values(["sid", "time"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Per-product-basin assembly
# ---------------------------------------------------------------------------

def assemble_basin(basin: str, *, hurdat2_path: Path | None,
                   ibtracs_path: Path, records_since: int,
                   current_year: int, fetch_live: bool = True,
                   basin_cfg: dict | None = None,
                   log_prefix: str = "[records]") -> pd.DataFrame:
    """Build the full canonical frame for one product basin.

    al/ep: HURDAT2 archive + IBTrACS top-up for seasons past HURDAT2's end
    + live tail. wp: IBTrACS (genesis-basin WP, ≥ records_since) + live tail.
    """
    if basin in ("al", "ep"):
        arch = parse_hurdat2(hurdat2_path)
        h2_last = int(arch["season"].max())
        print(f"{log_prefix}   hurdat2: {arch['sid'].nunique():,} storms "
              f"through {h2_last}")
        if current_year > h2_last:
            top = load_ibtracs(ibtracs_path, basin,
                               seasons=(h2_last + 1, current_year),
                               src="ibtracs-provisional")
            if len(top):
                gb = genesis_basin(top)
                accept = {"NA", "AL"} if basin == "al" else {"EP", "CP"}
                keep = gb[gb.isin(accept)].index
                top = top[top["sid"].isin(keep)].drop(columns=["_basin_col"])
                print(f"{log_prefix}   ibtracs top-up: "
                      f"{top['sid'].nunique():,} storm(s) "
                      f"{h2_last + 1}–{current_year}")
                arch = pd.concat([arch, top], ignore_index=True)
    else:
        arch = load_ibtracs(ibtracs_path, basin,
                            seasons=(records_since, current_year))
        gb = genesis_basin(arch)
        keep = gb[gb == "WP"].index
        arch = arch[arch["sid"].isin(keep)].drop(columns=["_basin_col"])
        print(f"{log_prefix}   ibtracs: {arch['sid'].nunique():,} WP-genesis "
              f"storms {records_since}–{current_year}")

    arch = arch[arch["season"] >= records_since]
    arch = arch.sort_values(["sid", "time"]).reset_index(drop=True)

    if fetch_live and basin_cfg is not None:
        live = live_canonical(current_year, basin_cfg, log_prefix)
        if len(live):
            before = len(arch)
            arch = merge_live_tail(arch, live)
            print(f"{log_prefix}   live tail: +{len(arch) - before} fix(es)")
    return arch
