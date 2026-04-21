#!/usr/bin/env python3
"""Animated season GIF for any basin (AL/EP/WP) and any year.

Produces a 1080×1080, 15-second GIF showing the basin's tropical cyclone
tracks drawing themselves in real time, with an ACE accumulation panel
below comparing the season to the 1991-2020 climo band and the prior year.
Visually matches the pre-existing wpac_2026_season.gif / wpac_1997_season.gif.

Data sources:
  * Current-season tracks   → {basin}_tracks_data.json   (live, in repo root)
  * Historical tracks       → historical/{basin}/tracks/tracks_{YEAR}.json
  * ACE climatology + years → {basin}_ace_data.json       (in repo root)

Usage:
  python generate_season_gif.py --basin wp --year 2026
  python generate_season_gif.py --basin al --year 2005
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.patches import Polygon as MPLPoly
import imageio.v2 as imageio
from PIL import Image

# ---------------------------------------------------------------------------
# Palette (matches triple-a-tropics.com styles.css)
# ---------------------------------------------------------------------------
BG            = "#131519"
PANEL         = "#1b1e24"
BORDER        = "#2a2e36"
FG            = "#e8ebef"
MUTED         = "#9199a4"
ACCENT_AMBER  = "#ffb83a"
ACCENT_CYAN   = "#5dd3ff"
ACCENT_VIOLET = "#c084fc"
HURRICANE_BLUE = "#1e3a8a"

CAT_COLORS = {
    "TD":  "#6bb7ff",
    "TS":  "#6ff0a0",
    "C1":  "#ffd166",
    "C2":  "#ffa34f",
    "C3":  "#ff6b4d",
    "C4":  "#e53f71",
    "C5":  "#c084fc",
}

# ---------------------------------------------------------------------------
# Basin configuration
# ---------------------------------------------------------------------------
BASIN_CFG = {
    "wp": {
        "full_name": "Western North Pacific",
        "ace_file": "wp_ace_data.json",
        "current_tracks_file": "wp_tracks_data.json",
        # Full historical extent (wide enough for dateline crossers)
        "extent_hist": (100, 195, -2, 50),
        "extent_curr": (105, 180, -2, 45),
        "xticks_hist": ([110, 130, 150, 170, 190],
                        ["110°E", "130°E", "150°E", "170°E", "170°W"]),
        "xticks_curr": ([110, 130, 150, 170],
                        ["110°E", "130°E", "150°E", "170°E"]),
        "gridlines_lon": (120, 140, 160, 180),
        "lon_convention": "0-360",     # tracks JSON uses 0..360
        "needs_dateline_wrap": True,    # render countries across 180° seam
        "vocab_short": "named",
    },
    "al": {
        "full_name": "North Atlantic",
        "ace_file": "al_ace_data.json",
        "current_tracks_file": "al_tracks_data.json",
        "extent_hist": (-100, -5, 5, 50),
        "extent_curr": (-100, -5, 5, 50),
        "xticks_hist": ([-90, -70, -50, -30, -10],
                        ["90°W", "70°W", "50°W", "30°W", "10°W"]),
        "xticks_curr": ([-90, -70, -50, -30, -10],
                        ["90°W", "70°W", "50°W", "30°W", "10°W"]),
        "gridlines_lon": (-90, -70, -50, -30),
        "lon_convention": "-180-180",
        "needs_dateline_wrap": False,
        "vocab_short": "named",
    },
    "ep": {
        "full_name": "Northeast Pacific",
        "ace_file": "ep_ace_data.json",
        "current_tracks_file": "ep_tracks_data.json",
        "extent_hist": (-180, -80, 0, 35),
        "extent_curr": (-180, -80, 0, 35),
        "xticks_hist": ([-170, -150, -130, -110, -90],
                        ["170°W", "150°W", "130°W", "110°W", "90°W"]),
        "xticks_curr": ([-170, -150, -130, -110, -90],
                        ["170°W", "150°W", "130°W", "110°W", "90°W"]),
        "gridlines_lon": (-160, -140, -120, -100),
        "lon_convention": "-180-180",
        "needs_dateline_wrap": False,
        "vocab_short": "named",
    },
}


def wind_to_cat(w):
    if w is None or (isinstance(w, float) and math.isnan(w)):
        return "TD"
    if w < 34:  return "TD"
    if w < 64:  return "TS"
    if w < 83:  return "C1"
    if w < 96:  return "C2"
    if w < 113: return "C3"
    if w < 137: return "C4"
    return "C5"


def render_hurricane_icon(px: int = 128) -> np.ndarray:
    fig = plt.figure(figsize=(1, 1), dpi=px)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(-34, 34); ax.set_ylim(-34, 34)
    ax.set_aspect("equal"); ax.axis("off")
    fig.patch.set_alpha(0.0); ax.set_facecolor("none")

    def spiral_arm(mirror: bool = False):
        th = np.linspace(0.0, 2.6 * math.pi, 220)
        r  = 3.0 * np.exp(0.18 * th)
        r  = np.clip(r, 0, 30)
        x  = r * np.cos(th)
        y  = r * np.sin(th)
        w  = 6.5 * (1 - (th / th.max())**1.4)
        w  = np.clip(w, 0.8, 6.5)
        left  = np.stack([x + w * -np.sin(th), y + w *  np.cos(th)], axis=1)
        right = np.stack([x + w *  np.sin(th), y + w * -np.cos(th)], axis=1)
        poly = np.concatenate([left, right[::-1]])
        if mirror:
            poly = poly * np.array([-1, -1])
        return poly

    ax.add_patch(MPLPoly(spiral_arm(False), color=HURRICANE_BLUE, lw=0))
    ax.add_patch(MPLPoly(spiral_arm(True),  color=HURRICANE_BLUE, lw=0))
    ax.text(0, 0, "A", ha="center", va="center",
            color="#ffffff", fontsize=24, fontweight="900",
            family="DejaVu Sans")
    fig.canvas.draw()
    buf = np.asarray(fig.canvas.buffer_rgba())
    plt.close(fig)
    return buf


def load_geo(coast_path: Path, ctry_path: Path):
    cdata = json.loads(coast_path.read_text())
    coast = []
    for feat in cdata["features"]:
        g = feat["geometry"]
        if g["type"] == "LineString":
            coast.append(np.asarray(g["coordinates"], dtype=float))
        elif g["type"] == "MultiLineString":
            for ls in g["coordinates"]:
                coast.append(np.asarray(ls, dtype=float))
    ndata = json.loads(ctry_path.read_text())
    land = []
    for feat in ndata["features"]:
        g = feat["geometry"]
        if g["type"] == "Polygon":
            land.append(np.asarray(g["coordinates"][0], dtype=float))
        elif g["type"] == "MultiPolygon":
            for shp in g["coordinates"]:
                land.append(np.asarray(shp[0], dtype=float))
    return coast, land


def draw_basemap(ax, extent, cfg, coast, land):
    lon_min, lon_max, lat_min, lat_max = extent
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_aspect("auto")
    ax.tick_params(colors=MUTED, labelsize=8, length=3)
    for sp in ax.spines.values():
        sp.set_color(BORDER)

    offsets = (0.0, 360.0, -360.0) if cfg["needs_dateline_wrap"] else (0.0,)

    for poly in land:
        for off in offsets:
            xs = poly[:, 0] + off
            ys = poly[:, 1]
            if xs.max() < lon_min or xs.min() > lon_max:
                continue
            if ys.max() < lat_min or ys.min() > lat_max:
                continue
            ax.add_patch(MPLPoly(np.column_stack([xs, ys]), closed=True,
                                 facecolor="#20242b", edgecolor="none", zorder=1))
    for ls in coast:
        for off in offsets:
            xs = ls[:, 0] + off
            ys = ls[:, 1]
            if xs.max() < lon_min or xs.min() > lon_max:
                continue
            if ys.max() < lat_min or ys.min() > lat_max:
                continue
            ax.plot(xs, ys, color="#3a4050", lw=0.6, zorder=2)

    for lat in range(int(lat_min // 10) * 10, int(lat_max // 10 + 1) * 10 + 1, 10):
        ax.axhline(lat, color=BORDER, lw=0.4, ls="--", alpha=0.45, zorder=1)
    for lon in cfg["gridlines_lon"]:
        ax.axvline(lon, color=BORDER, lw=0.4, ls="--", alpha=0.45, zorder=1)


def normalize_lon(lon, convention):
    if convention == "0-360":
        return lon + 360.0 if lon < 0 else lon
    return lon - 360.0 if lon > 180 else lon


def load_tracks(basin: str, year: int, repo_root: Path, current_year: int):
    """Load storm tracks, adapting to lon convention of this basin."""
    cfg = BASIN_CFG[basin]
    if year == current_year:
        path = repo_root / cfg["current_tracks_file"]
        mode = "current"
    else:
        path = repo_root / "historical" / basin / "tracks" / f"tracks_{year}.json"
        mode = "historical"
    if not path.exists():
        raise FileNotFoundError(f"No tracks file at {path}")
    doc = json.loads(path.read_text())
    storms = doc.get("storms", [])
    for s in storms:
        pts = s["points"]
        s["times"] = np.array([dt.datetime.fromisoformat(p["t"]) for p in pts])
        s["lats"]  = np.array([p["lat"] for p in pts], dtype=float)
        lons = np.array([p["lon"] for p in pts], dtype=float)
        s["lons"]  = np.array([normalize_lon(l, cfg["lon_convention"]) for l in lons])
        s["winds"] = np.array([p.get("wind_kt") or np.nan for p in pts], dtype=float)
        s["cats"]  = np.array([wind_to_cat(w) for w in s["winds"]])
    return doc, mode


def load_ace_series(basin: str, year: int, repo_root: Path, current_year: int):
    """Return dict with climo_mean/p10/p90/doy, season curve, and prior-year curve."""
    cfg = BASIN_CFG[basin]
    ace = json.loads((repo_root / cfg["ace_file"]).read_text())
    ace_doy = np.asarray(ace["doy"], dtype=int)
    climo_mean = np.asarray(ace["climo"]["mean"], dtype=float)
    climo_p10  = np.asarray(ace["climo"]["p10"],  dtype=float)
    climo_p90  = np.asarray(ace["climo"]["p90"],  dtype=float)

    all_years = ace.get("all_years", {})
    prior = all_years.get(str(year - 1))
    prior_arr = np.asarray(prior, dtype=float) if prior else None

    # Build season curve (366 points) from all_years for historical,
    # from current.values+doy for current year.
    if year == current_year:
        current_values = np.asarray(ace["current"]["values"], dtype=float)
        current_doy    = np.asarray(ace["current"]["doy"],    dtype=int)
        today_doy      = int(ace["today_doy"])
        curve = np.full(today_doy, fill_value=np.nan)
        for d, v in zip(current_doy, current_values):
            if 1 <= d <= today_doy:
                curve[d - 1] = v
        # forward-fill
        last = 0.0
        for i in range(len(curve)):
            if np.isnan(curve[i]):
                curve[i] = last
            else:
                last = curve[i]
        curve_doy = np.arange(1, today_doy + 1)
        season_final = float(curve[-1]) if len(curve) else 0.0
    else:
        year_vals = all_years.get(str(year))
        if year_vals is None:
            raise ValueError(f"No ACE series for {basin} {year}")
        curve = np.asarray(year_vals, dtype=float)
        curve_doy = np.arange(1, len(curve) + 1)
        today_doy = len(curve)
        season_final = float(curve[-1]) if len(curve) else 0.0

    # Rank for title.
    finals = [(int(y), float(v[-1])) for y, v in all_years.items() if v]
    finals.sort(key=lambda x: -x[1])
    order = {y: i + 1 for i, (y, _) in enumerate(finals)}
    rank = order.get(year)
    total_seasons = len(finals)

    return {
        "ace_doy": ace_doy,
        "climo_mean": climo_mean,
        "climo_p10": climo_p10,
        "climo_p90": climo_p90,
        "prior_values": prior_arr,
        "curve": curve,
        "curve_doy": curve_doy,
        "today_doy": today_doy,
        "season_final": season_final,
        "rank": rank,
        "total_seasons": total_seasons,
    }


def build_shared_palette_from_arrays(frames, colors=128, sample_count=10):
    """Sample ~10 representative frames to build one shared adaptive palette.
    `frames` is a list of RGB numpy arrays. We do not hold full copies."""
    h, w, _ = frames[0].shape
    idxs = list(range(0, len(frames), max(1, len(frames) // sample_count)))
    sampler = Image.new("RGB", (w, h * len(idxs)))
    for i, k in enumerate(idxs):
        sampler.paste(Image.fromarray(frames[k]), (0, i * h))
    pal = sampler.quantize(colors=colors,
                           method=Image.Quantize.MEDIANCUT,
                           dither=Image.Dither.NONE)
    return pal


def render(basin: str, year: int,
           repo_root: Path, out_path: Path,
           fps: int = 15, duration_s: float = 15.0,
           current_year: int | None = None) -> Path:
    cfg = BASIN_CFG[basin]
    if current_year is None:
        current_year = dt.datetime.now().year
    is_current = (year == current_year)

    print(f"[{basin}/{year}] loading data…")
    tracks_doc, mode = load_tracks(basin, year, repo_root, current_year)
    ace = load_ace_series(basin, year, repo_root, current_year)

    storms = tracks_doc.get("storms", [])
    header = tracks_doc.get("header", {})
    named = header.get("named", 0)

    # Timeline bounds.
    if is_current:
        # Animate up to "now".
        all_times = np.concatenate([s["times"] for s in storms]) if storms else \
                    np.array([dt.datetime(year, 1, 1)])
        t_start = dt.datetime(year, 1, 1)
        t_end   = all_times.max() if len(all_times) else dt.datetime(year, 4, 1)
    else:
        t_start = dt.datetime(year, 1, 1)
        t_end   = dt.datetime(year, 12, 31, 18)

    # Geo assets.
    coast, land = load_geo(
        repo_root / "ne_50m_coastline.geojson",
        repo_root / "ne_50m_admin_0_countries.geojson",
    )

    # Figure setup.
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "axes.edgecolor": BORDER,
        "axes.labelcolor": FG,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "text.color": FG,
    })
    FIG_PX = 1080; DPI = 120
    fig = plt.figure(figsize=(FIG_PX / DPI, FIG_PX / DPI), dpi=DPI, facecolor=BG)
    gs = fig.add_gridspec(
        nrows=3, ncols=1,
        height_ratios=[0.10, 0.58, 0.32],
        left=0.055, right=0.97, top=0.97, bottom=0.07, hspace=0.18,
    )
    ax_title = fig.add_subplot(gs[0, 0]); ax_title.axis("off")
    ax_map   = fig.add_subplot(gs[1, 0])
    ax_ace   = fig.add_subplot(gs[2, 0])
    for a in (ax_title, ax_map, ax_ace):
        a.set_facecolor(BG)

    # Title block.
    subtitle = "Season to date" if is_current else "Full season"
    ax_title.set_xlim(0, 1); ax_title.set_ylim(0, 1)
    ax_title.text(0.0, 0.70, f"{cfg['full_name']} · {year}",
                  fontsize=22, fontweight="900", color=FG, va="center")
    ax_title.text(0.0, 0.20, subtitle,
                  fontsize=11, color=MUTED, va="center")
    stats_rank = (f"Rank {ace['rank']}/{ace['total_seasons']}"
                  if ace["rank"] and ace["total_seasons"] else "")
    ax_title.text(1.0, 0.70, f"{named} named",
                  fontsize=14, color=ACCENT_CYAN, fontweight="700",
                  ha="right", va="center")
    ax_title.text(1.0, 0.30,
                  f"ACE {ace['season_final']:.1f}   {stats_rank}".strip(),
                  fontsize=12, color=MUTED, ha="right", va="center")

    # Map axes.
    extent = cfg["extent_hist"] if not is_current else cfg["extent_curr"]
    lon_min, lon_max, lat_min, lat_max = extent
    draw_basemap(ax_map, extent, cfg, coast, land)
    xt, xtl = cfg["xticks_hist"] if not is_current else cfg["xticks_curr"]
    ax_map.set_xticks(xt); ax_map.set_xticklabels(xtl)

    yt = [y for y in range(int(lat_min // 10) * 10, int(lat_max // 10 + 1) * 10 + 1, 10)
          if lat_min <= y <= lat_max]
    ax_map.set_yticks(yt)
    ax_map.set_yticklabels([f"{y}°" if y == 0 else
                            f"{abs(y)}°N" if y > 0 else f"{abs(y)}°S" for y in yt])

    # Watermark (bottom-right of map).
    ax_map.text(lon_max - 0.8, lat_min + 0.3, "@WeathermanAAA_",
                fontsize=11, fontweight="900",
                color="#ffffff", alpha=0.30,
                ha="right", va="bottom", zorder=0.5)

    # ACE axes.
    ax_ace.set_xlim(1, 366)
    max_y = max(float(np.nanmax(ace["curve"])) * 1.2 if len(ace["curve"]) else 1.0,
                float(np.nanmax(ace["climo_p90"]))) * 1.08
    ax_ace.set_ylim(0, max_y)
    ax_ace.tick_params(colors=MUTED, labelsize=8, length=3)
    for sp in ax_ace.spines.values():
        sp.set_color(BORDER)

    ax_ace.fill_between(ace["ace_doy"], ace["climo_p10"], ace["climo_p90"],
                        color=MUTED, alpha=0.18, linewidth=0,
                        label="1991–2020 10–90%")
    ax_ace.plot(ace["ace_doy"], ace["climo_mean"],
                color=MUTED, lw=1.0, ls="--", alpha=0.9,
                label="1991–2020 mean")
    if ace["prior_values"] is not None:
        ax_ace.plot(np.arange(1, len(ace["prior_values"]) + 1),
                    ace["prior_values"],
                    color=ACCENT_VIOLET, lw=1.2, alpha=0.7,
                    label=str(year - 1))
    if is_current:
        ax_ace.axvline(ace["today_doy"], color=FG, lw=0.8, ls=":", alpha=0.6)

    month_doy = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335]
    month_lbl = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    ax_ace.set_xticks(month_doy); ax_ace.set_xticklabels(month_lbl)

    (line_current,) = ax_ace.plot([], [], color=ACCENT_AMBER, lw=2.4,
                                  label=str(year), zorder=10)
    ace_dot = ax_ace.scatter([], [], s=60, color=ACCENT_AMBER,
                             edgecolors=FG, linewidths=1.2, zorder=11)
    ace_label = ax_ace.text(0, 0, "", color=ACCENT_AMBER, fontsize=10,
                            fontweight="700", ha="left", va="bottom", zorder=12)
    leg = ax_ace.legend(loc="upper left", fontsize=8, frameon=False,
                        labelcolor=FG, ncol=4, handlelength=1.5,
                        columnspacing=1.0)
    for t in leg.get_texts():
        t.set_color(FG)

    # Align panels.
    fig.canvas.draw()
    pos_map   = ax_map.get_position()
    pos_ace   = ax_ace.get_position()
    pos_title = ax_title.get_position()
    ax_ace.set_position([pos_map.x0, pos_ace.y0, pos_map.width, pos_ace.height])
    ax_title.set_position([pos_map.x0, pos_title.y0, pos_map.width, pos_title.height])

    # Hurricane icon
    ICON_IMG = Image.fromarray(render_hurricane_icon(px=128), mode="RGBA")

    # Frame schedule
    LINGER_S = 2.0
    n_linger = int(LINGER_S * fps)
    n_anim   = int(duration_s * fps) - n_linger
    frame_times = [t_start + (t_end - t_start) * (i / max(1, n_anim - 1))
                   for i in range(n_anim)]
    frame_times += [t_end] * n_linger
    TOTAL = len(frame_times)

    def t_to_doy(t):
        return (t - dt.datetime(t.year, 1, 1)).total_seconds() / 86400.0 + 1.0

    def trail_segs(s, cutoff_t):
        mask = s["times"] <= cutoff_t
        if mask.sum() < 2:
            return [], []
        lats = s["lats"][mask]; lons = s["lons"][mask]; cats = s["cats"][mask]
        segs, cols = [], []
        for i in range(len(lats) - 1):
            # Skip segments that jump across the dateline seam
            # (e.g. EP track with points at both +180 and -179.9).
            if abs(lons[i+1] - lons[i]) > 180:
                continue
            segs.append([(lons[i], lats[i]), (lons[i+1], lats[i+1])])
            cols.append(CAT_COLORS.get(cats[i+1], "#6bb7ff"))
        return segs, cols

    def storm_head(s, cutoff_t):
        mask = s["times"] <= cutoff_t
        if mask.sum() == 0:
            return None
        i = int(np.where(mask)[0][-1])
        head_lat = s["lats"][i]; head_lon = s["lons"][i]
        if i >= 1:
            dlat = s["lats"][i] - s["lats"][i-1]
            dlon = s["lons"][i] - s["lons"][i-1]
            bearing = math.degrees(math.atan2(dlon, dlat))
        else:
            bearing = 0.0
        is_active = (cutoff_t >= s["times"][0]) and (cutoff_t <= s["times"][-1])
        return head_lat, head_lon, bearing, is_active

    print(f"[{basin}/{year}] rendering {TOTAL} frames ({duration_s:.0f}s @ {fps}fps)…")
    frames: list[np.ndarray] = []
    dynamic_artists: list = []
    for fi, t_now in enumerate(frame_times):
        for art in dynamic_artists:
            try: art.remove()
            except Exception: pass
        dynamic_artists.clear()

        # Tracks
        all_segs, all_cols = [], []
        for s in storms:
            segs, cols = trail_segs(s, t_now)
            all_segs.extend(segs); all_cols.extend(cols)
        if all_segs:
            lc = LineCollection(all_segs, colors=all_cols, lw=2.6,
                                capstyle="round", zorder=10)
            ax_map.add_collection(lc); dynamic_artists.append(lc)

        # Collect active storms, stamp icons on all, label top 3 by intensity.
        spin = (fi * 22) % 360
        actives = []
        for s in storms:
            head = storm_head(s, t_now)
            if head is None:
                continue
            lat, lon, bearing, active = head
            if not active:
                pt = ax_map.scatter([lon], [lat], s=18, color="#6b7280",
                                    edgecolors="#cfd5df", linewidths=0.6,
                                    zorder=15, alpha=0.9)
                dynamic_artists.append(pt)
                continue
            idx = int(np.argmin(np.abs(s["times"] - t_now)))
            w = float(s["winds"][idx]) if np.isfinite(s["winds"][idx]) else 30.0
            actives.append((s, lat, lon, bearing, w, idx))

        actives.sort(key=lambda x: -x[4])  # strongest first
        for i, (s, lat, lon, bearing, w, idx) in enumerate(actives):
            size_deg = 1.8 + max(0, min(1.0, (w - 30) / 130.0)) * 1.2
            rot = ICON_IMG.rotate(spin - bearing,
                                  resample=Image.BICUBIC, expand=False)
            half = size_deg / 2.0
            ax_map.imshow(np.asarray(rot),
                          extent=(lon - half, lon + half,
                                  lat - half, lat + half),
                          interpolation="bilinear", zorder=20)
            dynamic_artists.append(ax_map.images[-1])
            if i < 3:
                tag = ax_map.text(lon + 0.6, lat + 0.6,
                                  f"{s['name']} · {int(w)} kt",
                                  fontsize=9, color=FG, fontweight="700",
                                  zorder=25,
                                  bbox=dict(facecolor="#0f1216cc",
                                            edgecolor=BORDER, pad=2.2,
                                            boxstyle="round,pad=0.25"))
                dynamic_artists.append(tag)

        # ACE partial
        t_doy = t_to_doy(t_now)
        cutoff = int(min(len(ace["curve"]), max(1, math.floor(t_doy))))
        xs = ace["curve_doy"][:cutoff]; ys = ace["curve"][:cutoff]
        line_current.set_data(xs, ys)
        if len(xs) > 0:
            ace_dot.set_offsets(np.c_[[xs[-1]], [ys[-1]]])
            ace_label.set_position((xs[-1] + 3, ys[-1] + max_y * 0.015))
            ace_label.set_text(f"{ys[-1]:.1f}")
        else:
            ace_dot.set_offsets(np.empty((0, 2)))
            ace_label.set_text("")

        dlabel = ax_map.text(
            0.995, 0.965,
            t_now.strftime("%b %d, %Y · %H UTC"),
            transform=ax_map.transAxes, ha="right", va="top",
            color=FG, fontsize=10, fontweight="700",
            bbox=dict(facecolor="#0f1216cc", edgecolor=BORDER,
                      pad=3.0, boxstyle="round,pad=0.3"),
            zorder=30,
        )
        dynamic_artists.append(dlabel)

        fig.canvas.draw()
        img = np.asarray(fig.canvas.buffer_rgba())[:, :, :3].copy()
        frames.append(img)
        if (fi + 1) % 30 == 0 or fi == TOTAL - 1:
            print(f"  frame {fi+1}/{TOTAL}")

    plt.close(fig)

    # Compress via shared 128-color palette before writing.
    # Streamed so we never hold (RGB × N) + (PIL × N) + (quant × N) at once.
    print(f"[{basin}/{year}] encoding GIF…")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pal = build_shared_palette_from_arrays(frames, colors=128)

    n = len(frames)
    quant_arrs: list[np.ndarray] = []
    for i in range(n):
        fr = frames[i]
        frames[i] = None                       # release as we go
        q = Image.fromarray(fr).quantize(palette=pal, dither=Image.Dither.NONE)
        quant_arrs.append(np.array(q.convert("RGB")))
        del fr, q
    frames.clear()

    imageio.mimsave(out_path, quant_arrs,
                    duration=duration_s / len(quant_arrs),
                    loop=0, subrectangles=True)
    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"  ✓ {out_path}  ({size_mb:.2f} MB, {len(quant_arrs)} frames)")
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--basin", required=True, choices=list(BASIN_CFG.keys()))
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--repo-root", type=Path, default=Path.cwd(),
                    help="Root of the repo checkout "
                         "(defaults to the current working directory).")
    ap.add_argument("--out", type=Path, default=None,
                    help="Output path (defaults to historical/{basin}/gifs/"
                         "{basin}_{year}_season.gif or wpac/alpac/epac_{year}_season.gif "
                         "at repo root for current year).")
    ap.add_argument("--duration", type=float, default=15.0)
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--current-year", type=int, default=None)
    args = ap.parse_args()

    current_year = args.current_year or dt.datetime.now().year
    if args.out is None:
        if args.year == current_year:
            slug = {"wp": "wpac", "al": "atl", "ep": "epac"}[args.basin]
            args.out = args.repo_root / f"{slug}_{args.year}_season.gif"
        else:
            args.out = (args.repo_root / "historical" / args.basin / "gifs" /
                        f"{args.basin}_{args.year}_season.gif")

    render(args.basin, args.year, args.repo_root, args.out,
           fps=args.fps, duration_s=args.duration, current_year=current_year)


if __name__ == "__main__":
    main()
