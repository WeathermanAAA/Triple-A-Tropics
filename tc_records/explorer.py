"""Pre-render emitter for the TC History track explorer.

Turns the per-basin computed results (fixes + storms + boards from
publish.compute_basin) into the static artifacts the thin map client reads
from the CDN — zero per-user compute:

- ``catalog_{basin}.json``   storm-level index: int id ↔ SID, name, season,
  dates, peak/min-pres/ACE, genesis point, landfalls (HURDAT2 L rows),
  official-report link, and which record boards the storm appears on
  (the Phase-1 cross-link).
- ``tracks_{basin}_{decade}.json``  per-fix arrays for map rendering,
  search-by-radius, hover tooltips and export: [t, lat, lon, wind, pres,
  flags]. Longitudes are UNWRAPPED per storm (continuous across the
  dateline) so a WP crosser draws as one line.
- ``density/…png`` + entries in the manifest: 1°-binned track- and
  genesis-density rasters (per basin, all-year + per-month) rendered as
  transparent overlays with bounds for a map image source.
- ``explorer_manifest.json``  everything the client needs to boot.

Category classing stays client-side off the shared SSHWS table — tracks
carry raw winds, so a palette change never needs a data rebuild.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from . import ENGINE_VERSION

# Density raster geometry, per basin: (lon_min, lon_max, lat_min, lat_max).
# WP uses 0–360 longitudes so the dateline sits mid-raster.
DENSITY_EXTENT = {
    "al": (-110.0, 10.0, 0.0, 70.0),
    "ep": (-180.0, -75.0, 0.0, 60.0),
    "wp": (95.0, 200.0, 0.0, 60.0),
}
DENSITY_BIN_DEG = 1.0

FLAG_SYNOPTIC = 1
FLAG_LANDFALL = 2
FLAG_TROPICAL = 4


def _report_link(basin: str, atcf: str, name: str, season: int):
    """Official post-storm report link, where a stable one exists."""
    if basin in ("al", "ep") and atcf and season >= 2000:
        if atcf.startswith("CP"):
            return ("CPHC summaries",
                    "https://www.weather.gov/cphc/summaries")
        pretty = name.capitalize() if name else ""
        if pretty:
            return ("NHC TCR",
                    f"https://www.nhc.noaa.gov/data/tcr/{atcf}_{pretty}.pdf")
        return ("NHC TCR index",
                f"https://www.nhc.noaa.gov/data/tcr/index.php?season={season}")
    if basin in ("al", "ep") and season >= 1998:
        return ("NHC archive",
                f"https://www.nhc.noaa.gov/archive/{season}/")
    if basin == "wp":
        return ("JTWC annual report",
                "https://www.metoc.navy.mil/jtwc/jtwc.html?"
                "annual-tropical-cyclone-reports")
    return None


def _unwrap_lons(lons: np.ndarray) -> np.ndarray:
    """Make each storm's longitude sequence continuous across the dateline
    (consecutive steps forced into (-180, 180])."""
    out = lons.copy()
    for i in range(1, len(out)):
        d = out[i] - out[i - 1]
        if d > 180.0:
            out[i:] -= 360.0
            # recompute from the shifted value on the next iteration
        elif d < -180.0:
            out[i:] += 360.0
    return out


def _record_links(boards: list[dict]) -> dict[str, list]:
    """sid -> [[board_key, page, title, rank], ...] across all boards."""
    out: dict[str, list] = {}
    for bd in boards:
        for row in bd.get("rows", []):
            sid = row.get("sid")
            if not sid:
                continue
            out.setdefault(sid, []).append(
                [bd["key"], bd["page"], bd["title"], row.get("rank")])
    return out


def _compact_time(ts: pd.Timestamp) -> str:
    return pd.Timestamp(ts).strftime("%Y%m%d%H%M")


def emit_explorer(results: dict[str, dict], out_dir: Path,
                  current_year: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "density").mkdir(exist_ok=True)
    generated = dt.datetime.now(dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    manifest: dict = {"generated": generated, "engine": ENGINE_VERSION,
                      "basins": {}, "density": []}

    for basin, r in results.items():
        cfg = r["cfg"]
        fixes = r["fixes"]
        storms = r["storms"].reset_index(drop=True)
        rec_links = _record_links(r["boards"])

        # Stable int ids: chronological (season, then genesis time; the
        # datetime column sorts NaT last on its own).
        storms = storms.sort_values(["season", "formation"],
                                    na_position="last") \
            .reset_index(drop=True)
        id_by_sid = {sid: i for i, sid in enumerate(storms["sid"])}

        trop_fixes = fixes[fixes["trop"]].sort_values(["sid", "time"])
        landfalls_by_sid: dict[str, list] = {}
        lf = trop_fixes[trop_fixes["rec"] == "L"]
        for sid, g in lf.groupby("sid"):
            rows = [
                [_compact_time(t), round(float(la), 2), round(float(lo), 2),
                 None if pd.isna(w) else int(w)]
                for t, la, lo, w in zip(g["time"], g["lat"], g["lon"],
                                        g["wind"])
                if not (pd.isna(la) or pd.isna(lo))]
            if rows:
                landfalls_by_sid[sid] = rows

        # ---- catalog ----
        cat_rows = []
        for i, s in storms.iterrows():
            cat_rows.append({
                "i": int(i), "sid": s["sid"], "atcf": s["atcf"] or None,
                "name": s["name"] or (s["atcf"] or "UNNAMED"),
                "season": int(s["season"]),
                "t0": None if pd.isna(s["formation"])
                      else pd.Timestamp(s["formation"]).strftime("%Y-%m-%d"),
                "t1": None if pd.isna(s["last_trop"])
                      else pd.Timestamp(s["last_trop"]).strftime("%Y-%m-%d"),
                "peak": None if pd.isna(s["peak_wind"])
                        else int(s["peak_wind"]),
                "pres": None if pd.isna(s["min_pres"])
                        else int(s["min_pres"]),
                "ace": float(s["ace"]),
                "dur": None if pd.isna(s["dur_tc"]) else float(s["dur_tc"]),
                "gen": None if math.isnan(s["gen_lat"])
                       else [round(float(s["gen_lat"]), 2),
                             round(float(s["gen_lon"]), 2)],
                "lf": landfalls_by_sid.get(s["sid"], []),
                "rep": _report_link(basin, s["atcf"] or "", s["name"] or "",
                                    int(s["season"])),
                "rec": rec_links.get(s["sid"], []),
            })
        catalog = {
            "basin": basin, "name": cfg["name"],
            "provenance": {
                "generated": generated, "engine": ENGINE_VERSION,
                "ibtracs": "IBTrACS v04r01",
                "wind_note": cfg["wind_note"],
                "ace_note": cfg["ace_note"],
                "records_since": cfg["records_since"],
                "satellite_era": cfg["satellite_era"],
                "current_season": current_year,
            },
            "storms": cat_rows,
        }
        (out_dir / f"catalog_{basin}.json").write_text(
            json.dumps(catalog, separators=(",", ":"), allow_nan=False))

        # ---- decade track bundles ----
        decades: dict[int, dict] = {}
        for sid, g in trop_fixes.groupby("sid", sort=False):
            i = id_by_sid.get(sid)
            if i is None:
                continue
            season = int(storms.loc[i, "season"])
            dec = (season // 10) * 10
            lons = _unwrap_lons(g["lon"].to_numpy(dtype=float))
            pts = []
            for k, (t, la, w, p, syn, recf) in enumerate(
                    zip(g["time"], g["lat"], g["wind"], g["pres"],
                        g["syn"], g["rec"])):
                if pd.isna(la) or pd.isna(lons[k]):
                    continue
                flags = FLAG_TROPICAL
                if syn:
                    flags |= FLAG_SYNOPTIC
                if recf == "L":
                    flags |= FLAG_LANDFALL
                pts.append([
                    _compact_time(t), round(float(la), 2),
                    round(float(lons[k]), 2),
                    None if pd.isna(w) else int(w),
                    None if pd.isna(p) else int(p),
                    flags,
                ])
            if pts:
                decades.setdefault(dec, {})[str(i)] = pts
        for dec, tracks in sorted(decades.items()):
            (out_dir / f"tracks_{basin}_{dec}.json").write_text(
                json.dumps({"basin": basin, "decade": dec,
                            "tracks": tracks},
                           separators=(",", ":"), allow_nan=False))

        manifest["basins"][basin] = {
            "name": cfg["name"],
            "storms": len(cat_rows),
            "decades": sorted(decades.keys()),
            "records_since": cfg["records_since"],
            "satellite_era": cfg["satellite_era"],
        }

        # ---- density rasters ----
        manifest["density"].extend(
            _emit_density(basin, trop_fixes, storms, out_dir))

    (out_dir / "explorer_manifest.json").write_text(
        json.dumps(manifest, separators=(",", ":")))
    for f in sorted(out_dir.rglob("*")):
        if f.is_file():
            rel = f.relative_to(out_dir)
            print(f"[explorer]   wrote {rel} ({f.stat().st_size:,} B)")


def _emit_density(basin: str, trop_fixes: pd.DataFrame,
                  storms: pd.DataFrame, out_dir: Path) -> list[dict]:
    """1°-binned track/genesis density → transparent PNG overlays."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import colormaps
    from matplotlib.colors import Normalize
    from PIL import Image

    lon0, lon1, lat0, lat1 = DENSITY_EXTENT[basin]
    nx = int(round((lon1 - lon0) / DENSITY_BIN_DEG))
    ny = int(round((lat1 - lat0) / DENSITY_BIN_DEG))
    entries = []

    def raster(name: str, lons, lats, title: str):
        lons = np.asarray(lons, dtype=float)
        lats = np.asarray(lats, dtype=float)
        if basin == "wp":
            lons = np.where(lons < 0, lons + 360.0, lons)
        h, _, _ = np.histogram2d(
            lats, lons, bins=[ny, nx],
            range=[[lat0, lat1], [lon0, lon1]])
        if h.max() <= 0:
            return
        v = np.log1p(h) / np.log1p(h.max())
        rgba = colormaps["magma"](Normalize(0, 1)(v))
        rgba[..., 3] = np.clip(v * 0.82, 0.0, 0.82)
        rgba[v <= 0, 3] = 0.0
        img = Image.fromarray((rgba[::-1] * 255).astype(np.uint8), "RGBA")
        fname = f"density/{name}.png"
        img.save(out_dir / fname)
        # Image-source coordinates: [[w,n],[e,n],[e,s],[w,s]]; WP uses
        # 0-360 lons on purpose so the overlay spans the dateline.
        entries.append({
            "key": name, "title": title, "file": fname,
            "coords": [[lon0, lat1], [lon1, lat1],
                       [lon1, lat0], [lon0, lat0]],
        })

    syn = trop_fixes[trop_fixes["syn"] & (trop_fixes["wind"] >= 34)]
    raster(f"{basin}_track_all", syn["lon"], syn["lat"],
           "Track density, all seasons")
    for m in range(1, 13):
        mm = syn[syn["time"].dt.month == m]
        if len(mm):
            raster(f"{basin}_track_m{m:02d}", mm["lon"], mm["lat"],
                   f"Track density, month {m:02d}")
    gen = storms.dropna(subset=["gen_lat"])
    raster(f"{basin}_genesis", gen["gen_lon"], gen["gen_lat"],
           "Genesis density, all seasons")
    return entries
