"""
generate_climo_panel.py — wyz23x2/xrq-style 4-panel ACE climatology PNG.

For one (basin, year), render:
  Panel 1: cumulative ACE percentile curve overlaid on 1991-2020 climatology
  Panel 2: rank trajectory through the season (inverted; 1 = best)
  Panel 3: daily ACE bars
  Panel 4: storm Gantt timeline, color-coded by peak SSHWS

Usage
-----
  python generate_climo_panel.py --basin wp --year 1994
  python generate_climo_panel.py --basin al --backfill                # 1970..current
  python generate_climo_panel.py --all-basins --backfill              # full 3-basin
  python generate_climo_panel.py --all-basins --year 2026             # current year only

Output:
  climatology/{basin_long}/panels/<year>.png
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import FancyBboxPatch
from matplotlib.gridspec import GridSpec
from matplotlib.transforms import blended_transform_factory
import matplotlib.patheffects as path_effects


# ---------------------------------------------------------------------------
# Basin config — kept aligned with generate_ace_plot.py BASINS dict.
# ---------------------------------------------------------------------------
BASINS = {
    "wp": {
        "short": "wp",
        "name": "WPAC",
        "full_name": "West Pacific",
        "long_dir": "west-pacific",
        "ace_natures": {"TS"},      # JTWC: tropical only
    },
    "al": {
        "short": "al",
        "name": "AL",
        "full_name": "Atlantic",
        "long_dir": "atlantic",
        "ace_natures": {"TS", "SS"},
    },
    "ep": {
        "short": "ep",
        "name": "EPAC",
        "full_name": "East Pacific",
        "long_dir": "east-pacific",
        "ace_natures": {"TS", "SS"},
    },
}

# wyz23x2-style palette mapped onto site CSS tokens.
COL = {
    "bg":         "#131519",   # --bg
    "panel":      "#1b1e24",   # --panel
    "border":     "#2a2e36",   # --border
    "fg":         "#e8ebef",   # --fg
    "muted":      "#9199a4",   # --muted
    "accent":     "#ffb83a",   # --accent (amber)
    "cyan":       "#5dd3ff",   # --accent-2
    "violet":     "#c084fc",   # --accent-3

    # ACE band fills, layered light → dark
    "band_minmax": (0.31, 0.51, 0.71, 0.18),  # lightest blue, min-max envelope
    "band_p1090":  (0.31, 0.51, 0.71, 0.34),
    "band_p2575":  (0.31, 0.51, 0.71, 0.55),  # darkest, IQR
    "band_edge":   (0.55, 0.75, 0.95, 0.30),

    "rank_line":   "#4ade80",   # green for rank trajectory
    "bar_cyan":    "#5dd3ff",
}

# SSHWS colors for the Gantt panel.
SSHWS = [
    (33,  "TD",  "#fff5cc"),
    (63,  "TS",  "#4ade80"),
    (82,  "C1",  "#5dd3ff"),
    (95,  "C2",  "#ffb83a"),
    (112, "C3",  "#ec4899"),
    (136, "C4",  "#ef4444"),
    (999, "C5",  "#c084fc"),
]


def sshws_color(peak_kt: float | None) -> tuple[str, str]:
    if peak_kt is None or not np.isfinite(peak_kt):
        return ("--", COL["muted"])
    for ceiling, label, color in SSHWS:
        if peak_kt <= ceiling:
            return (label, color)
    return ("C5", "#c084fc")


HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_ace_data(basin: str) -> dict:
    p = HERE / f"{basin}_ace_data.json"
    with open(p) as f:
        return json.load(f)


def load_tracks(basin: str, year: int, current_year: int) -> dict | None:
    """Return tracks_data-shaped dict for a (basin, year), or None if no
    per-storm history exists for that year (pre-1970 typically).
    """
    if year == current_year:
        p = HERE / f"{basin}_tracks_data.json"
    else:
        p = HERE / "historical" / basin / "tracks" / f"tracks_{year}.json"
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


SIX = {0, 6, 12, 18}


def daily_ace_from_tracks(tracks: dict, basin: str, year: int) -> np.ndarray:
    """Compute daily ACE (length-366 array, doy index 1..366) from a
    tracks_data-shaped dict. Filters to the basin's accepted natures,
    6-hourly synoptic times, and wind ≥ 34 kt — same rules as
    generate_ace_plot.compute_ace_timeseries.
    """
    natures = BASINS[basin]["ace_natures"]
    daily = np.zeros(367, dtype=float)
    for storm in tracks.get("storms", []):
        for p in storm.get("points", []):
            w = p.get("wind_kt")
            if w is None:
                continue
            try:
                t = dt.datetime.fromisoformat(p["t"])
            except (ValueError, KeyError):
                continue
            if t.year != year:
                continue            # off-season carryover
            if t.hour not in SIX or t.minute != 0:
                continue
            if w < 34:
                continue
            if (p.get("nature") or "").strip() not in natures:
                continue
            doy = t.timetuple().tm_yday
            daily[doy] += (w * w) / 1e4
    return daily


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def month_tick_locs(year: int) -> tuple[list[int], list[str]]:
    """Day-of-year for the 1st of each month, plus single-letter abbreviations."""
    locs = []
    labels = []
    for m in range(1, 13):
        locs.append(dt.date(year, m, 1).timetuple().tm_yday)
        labels.append(calendar.month_abbr[m])
    return locs, labels


def _ranks_at_doys(all_years_matrix: np.ndarray, year_index: int) -> np.ndarray:
    """For the given year (row index), return its rank at each DOY across all
    years in the matrix. Rank is 1 = highest cumulative ACE. Ties broken by
    matrix order (stable). Returns length-(366,) int array indexed 0..365.
    """
    # argsort descending: position of each year in sorted order = rank-1
    # We want rank for `year_index` at each column.
    # Faster: compute (matrix > target).sum(axis=0) + 1
    target = all_years_matrix[year_index]      # (366,)
    higher = (all_years_matrix > target).sum(axis=0)
    # Tie behavior: ties get the same rank (lowest among them). +1 for 1-based.
    return higher + 1


def _current_cumulative(ace_data: dict) -> np.ndarray:
    """Extract a length-366 cumulative ACE array from the `current` block.
    Shape on disk is {label, doy: [...], values: [...], latest_value} where
    doy/values cover [1, today_doy]; we forward-fill to 366 with the last value.
    """
    cur = ace_data.get("current")
    if cur is None:
        return np.zeros(366)
    if isinstance(cur, list):
        arr = np.asarray(cur, dtype=float)
    elif isinstance(cur, dict):
        vals = cur.get("values") or []
        if not vals:
            return np.zeros(366)
        arr = np.asarray(vals, dtype=float)
    else:
        return np.zeros(366)
    if len(arr) >= 366:
        return arr[:366]
    pad = np.full(366 - len(arr), arr[-1] if len(arr) else 0.0)
    return np.concatenate([arr, pad])


def _years_matrix(ace_data: dict, current_year: int) -> tuple[list[int], np.ndarray]:
    """Return (years, matrix[N, 366]) of cumulative ACE for every season
    available in ace_data['all_years'], plus the current year from
    ace_data['current'] if it isn't already present in all_years.
    """
    all_years = ace_data.get("all_years", {})
    years = sorted(int(y) for y in all_years.keys())
    rows = [np.asarray(all_years[str(y)], dtype=float) for y in years]
    if current_year not in years:
        cur = _current_cumulative(ace_data)
        years.append(current_year)
        rows.append(cur)
    matrix = np.vstack(rows)
    if matrix.shape[1] != 366:
        # all_years entries are length-366 already, but defensive pad
        if matrix.shape[1] < 366:
            matrix = np.hstack([matrix, np.tile(matrix[:, -1:], (1, 366 - matrix.shape[1]))])
        else:
            matrix = matrix[:, :366]
    return years, matrix


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_panel(basin: str, year: int, out_path: Path,
                 ace_data: dict | None = None,
                 current_year: int | None = None) -> Path:
    """Render and save the 4-panel PNG for (basin, year). Returns out_path."""
    if current_year is None:
        current_year = dt.date.today().year
    if ace_data is None:
        ace_data = load_ace_data(basin)

    cfg = BASINS[basin]
    doy = np.asarray(ace_data["doy"], dtype=int)        # 1..366
    climo = ace_data["climo"]
    band_min   = np.asarray(climo["min"],  dtype=float)
    band_p10   = np.asarray(climo["p10"],  dtype=float)
    band_p25   = np.asarray(climo["p25"],  dtype=float)
    band_mean  = np.asarray(climo["mean"], dtype=float)
    band_p75   = np.asarray(climo["p75"],  dtype=float)
    band_p90   = np.asarray(climo["p90"],  dtype=float)
    band_max   = np.asarray(climo["max"],  dtype=float)
    # median: midpoint of p25/p75 isn't right. Use the column median — matplotlib
    # commonly reuses mean as a stand-in, but the JSON only ships the seven bands.
    # Compute the true median from all_years if we have it.
    years_list, years_mat = _years_matrix(ace_data, current_year)

    climo_start, climo_end = 1991, 2020
    climo_rows = np.array([y for y in years_list if climo_start <= y <= climo_end])
    if len(climo_rows):
        idx = [years_list.index(y) for y in climo_rows]
        band_median = np.median(years_mat[idx], axis=0)
    else:
        band_median = band_mean.copy()

    # Selected year cumulative ACE
    if year in years_list:
        sel_cum = years_mat[years_list.index(year)].copy()
    elif year == current_year:
        sel_cum = _current_cumulative(ace_data)
    else:
        sel_cum = np.zeros(366)

    if len(sel_cum) < 366:
        sel_cum = np.concatenate([sel_cum, np.full(366 - len(sel_cum), sel_cum[-1] if len(sel_cum) else 0.0)])
    sel_cum = sel_cum[:366]

    # If this is the in-progress current year, only draw up to today; the
    # header values are also "as of today" so the comparison is apples-to-
    # apples (YTD vs YTD-climo) rather than YTD vs end-of-season.
    is_current = (year == current_year)
    today_doy = ace_data.get("today_doy") or dt.date.today().timetuple().tm_yday
    if is_current:
        cum_plot = sel_cum.copy()
        cum_plot[today_doy:] = np.nan
        ref_doy_idx = max(0, today_doy - 1)
    else:
        cum_plot = sel_cum
        ref_doy_idx = 365   # last DOY (366th)

    total_ace = float(np.nan_to_num(sel_cum, nan=0.0)[ref_doy_idx])
    avg_total = float(band_mean[ref_doy_idx])
    delta = total_ace - avg_total

    # Rank at each DOY (across all years available in matrix)
    if year in years_list:
        ranks = _ranks_at_doys(years_mat, years_list.index(year))
    else:
        ranks = np.full(366, np.nan)

    # Header rank: for past years use final-season rank by total ACE; for the
    # in-progress current year use the YTD rank already computed in ace_data.
    rank_total = None
    if is_current:
        rank_total = ace_data.get("current_rank", "?")
    elif year in years_list:
        totals = years_mat[:, -1]
        order = np.argsort(-totals, kind="stable")
        rank_pos = np.where(order == years_list.index(year))[0]
        if len(rank_pos):
            rank_total = int(rank_pos[0]) + 1
    if rank_total is None:
        rank_total = "?"

    # Per-storm tracks (may be None for pre-1970 historical years)
    tracks = load_tracks(basin, year, current_year)
    storms = tracks.get("storms", []) if tracks else []
    daily = daily_ace_from_tracks(tracks, basin, year) if tracks else np.zeros(367)

    # ----------- figure layout -----------
    # Panel 4 height scales with storm count so each row has enough vertical
    # space for a readable 9pt name (≈ 0.18 in/row) without crushing big
    # seasons (44 storms in WP 1994) or wasting space in quiet ones.
    n_storms = len(storms)
    panel4_in = max(2.6, n_storms * 0.20 + 1.0)
    panel123_in = 8.0 + 1.7 + 1.7    # ACE curve, rank traj, daily bars
    margin_in = 1.1                  # header + bottom + hspace allowance
    fig_h = panel123_in + panel4_in + margin_in

    fig = plt.figure(figsize=(14, fig_h), dpi=110, facecolor=COL["bg"])
    gs = GridSpec(4, 1, figure=fig,
                  height_ratios=[8.0, 1.7, 1.7, panel4_in],
                  hspace=0.32, left=0.13, right=0.965,
                  top=1.0 - 0.6 / fig_h,
                  bottom=0.5 / fig_h)

    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1], sharex=ax1)
    ax3 = fig.add_subplot(gs[2], sharex=ax1)
    ax4 = fig.add_subplot(gs[3], sharex=ax1)

    for ax in (ax1, ax2, ax3, ax4):
        ax.set_facecolor(COL["panel"])
        ax.tick_params(colors=COL["fg"], labelsize=9)
        for spine in ax.spines.values():
            spine.set_color(COL["border"])
            spine.set_linewidth(0.8)
        ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.30, color=COL["border"])
        ax.set_xlim(1, 366)

    # Hide top/right spines on sub-panels 2/3/4
    for ax in (ax2, ax3, ax4):
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    locs, labels = month_tick_locs(year if not calendar.isleap(year) else year)
    for ax in (ax1, ax2, ax3, ax4):
        ax.set_xticks(locs)
    # Only the bottom panel shows the labels; intermediate panels keep ticks.
    ax1.set_xticklabels([])
    ax2.set_xticklabels([])
    ax3.set_xticklabels([])
    ax4.set_xticklabels(labels)

    # ===== Panel 1: ACE percentile curve =====
    # nested fills, light → dark; legend values are end-of-season (climo final)
    ax1.fill_between(doy, band_min, band_max,
                     facecolor=COL["band_minmax"], linewidth=0,
                     label=f"Min–Max ({band_min[-1]:.1f} – {band_max[-1]:.1f})")
    ax1.fill_between(doy, band_p10, band_p90,
                     facecolor=COL["band_p1090"], linewidth=0,
                     label=f"10–90% ({band_p10[-1]:.1f} – {band_p90[-1]:.1f})")
    ax1.fill_between(doy, band_p25, band_p75,
                     facecolor=COL["band_p2575"], linewidth=0,
                     label=f"25–75% ({band_p25[-1]:.1f} – {band_p75[-1]:.1f})")

    ax1.plot(doy, band_median, color=COL["cyan"],   linestyle="--", linewidth=1.2,
             label=f"Median ({band_median[-1]:.1f})")
    ax1.plot(doy, band_mean,   color=COL["violet"], linestyle="--", linewidth=1.2,
             label=f"Average ({avg_total:.1f})")

    # Selected year — white core w/ cyan halo
    line, = ax1.plot(doy, cum_plot, color="#ffffff", linewidth=2.4,
                     label=f"{year} ({total_ace:.1f})")
    line.set_path_effects([
        path_effects.Stroke(linewidth=5.6, foreground=COL["cyan"], alpha=0.55),
        path_effects.Normal(),
    ])

    ax1.set_ylabel("Cumulative ACE (10⁴ kt²)", color=COL["fg"], fontsize=10)
    ax1.set_ylim(bottom=0)
    leg = ax1.legend(loc="upper left", fontsize=9, framealpha=0.85,
                     facecolor=COL["panel"], edgecolor=COL["border"], labelcolor=COL["fg"])
    leg.get_frame().set_linewidth(0.8)

    # ===== Panel 2: rank trajectory =====
    if np.isfinite(ranks).any():
        finite = np.isfinite(ranks)
        rmask = ranks.astype(float).copy()
        if year == current_year:
            today_doy = ace_data.get("today_doy") or dt.date.today().timetuple().tm_yday
            rmask[today_doy:] = np.nan
        ax2.plot(doy, rmask, color=COL["rank_line"], linewidth=1.8)
        valid = ranks[finite]
        if year == current_year:
            valid = ranks[:today_doy] if today_doy >= 1 else valid
        if len(valid):
            r_best = int(np.nanmin(valid))
            r_worst = int(np.nanmax(valid))
            n_seasons = years_mat.shape[0]
            ax2.plot([], [], ' ', label=f"Rank ({r_best} – {r_worst}, of {n_seasons})")
            leg2 = ax2.legend(loc="upper left", fontsize=9, framealpha=0.85,
                              facecolor=COL["panel"], edgecolor=COL["border"],
                              labelcolor=COL["fg"], handlelength=0)
            leg2.get_frame().set_linewidth(0.8)
        ax2.invert_yaxis()
        ax2.set_ylabel("Rank", color=COL["fg"], fontsize=10)
    else:
        ax2.text(0.5, 0.5, "Rank trajectory unavailable",
                 transform=ax2.transAxes, ha="center", va="center",
                 color=COL["muted"], fontsize=10)

    # ===== Panel 3: daily ACE bars =====
    bars_x = np.arange(1, 367)
    bars_y = daily[1:367]
    ax3.bar(bars_x, bars_y, color=COL["bar_cyan"], width=1.0, linewidth=0)
    if bars_y.max() > 0:
        i_max = int(bars_y.argmax())
        peak_val = float(bars_y[i_max])
        ax3.bar([i_max + 1], [peak_val], color=COL["accent"], width=1.0, linewidth=0,
                label=f"Max Daily ACE ({peak_val:.4f})")
        leg3 = ax3.legend(loc="upper left", fontsize=9, framealpha=0.85,
                          facecolor=COL["panel"], edgecolor=COL["border"],
                          labelcolor=COL["fg"])
        leg3.get_frame().set_linewidth(0.8)
    ax3.set_ylabel("Daily ACE", color=COL["fg"], fontsize=10)
    ax3.set_ylim(bottom=0)

    # ===== Panel 4: storm Gantt =====
    if storms:
        # sort earliest start → latest, top to bottom
        named = []
        for s in storms:
            try:
                start = dt.datetime.fromisoformat(s["start"])
                end = dt.datetime.fromisoformat(s["end"])
            except (KeyError, ValueError):
                continue
            named.append((s, start, end))
        named.sort(key=lambda t: t[1])
        # Storm names render in a fixed left column at axes-fraction x = -0.005,
        # data-coord y = row center. clip_on=False lets them extend into the
        # figure's left margin (which we widened above to make room).
        name_trans = blended_transform_factory(ax4.transAxes, ax4.transData)
        n = len(named)
        for i, (s, start, end) in enumerate(named):
            row = n - 1 - i
            d0 = start.timetuple().tm_yday + (start.hour + start.minute / 60.0) / 24.0
            d1 = end.timetuple().tm_yday + (end.hour + end.minute / 60.0) / 24.0
            d0 = max(1.0, min(366.0, d0))
            d1 = max(1.0, min(366.0, d1))
            if d1 <= d0:
                d1 = d0 + 0.25
            _, color = sshws_color(s.get("peak_wind_kt"))
            patch = FancyBboxPatch(
                (d0, row + 0.18),
                d1 - d0, 0.64,
                boxstyle="round,pad=0.0,rounding_size=0.18",
                linewidth=0,
                facecolor=color,
                edgecolor="none",
                mutation_aspect=1.0,
            )
            ax4.add_patch(patch)
            name = (s.get("name") or s.get("sid") or "—").upper()
            txt = ax4.text(-0.008, row + 0.5, name,
                           transform=name_trans,
                           ha="right", va="center", fontsize=9,
                           fontweight="600", color=COL["fg"])
            txt.set_clip_on(False)
        ax4.set_ylim(-0.4, n + 0.4)
        ax4.set_yticks([])
        ax4.set_ylabel("")
    else:
        ax4.text(0.5, 0.5, "No per-storm tracks available for this year",
                 transform=ax4.transAxes, ha="center", va="center",
                 color=COL["muted"], fontsize=10)
        ax4.set_yticks([])

    # ===== Header =====
    sign = "+" if delta >= 0 else "−"
    n_seasons = ace_data.get("total_seasons") or years_mat.shape[0]
    rank_disp = rank_total if rank_total != "?" else ace_data.get("current_rank", "?")
    header_left = (
        f"{year} {cfg['name']}      "
        f"ACE: {total_ace:.1f}  ({sign}{abs(delta):.1f} vs avg)      "
        f"Rank: {rank_disp}/{n_seasons}"
    )
    header_right = (
        f"@xrq | Plotted by @WeathermanAAA_  "
        f"at {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    header_y = 1.0 - 0.18 / fig_h
    fig.text(0.13, header_y, header_left,
             color=COL["fg"], fontsize=15, fontweight="700", ha="left", va="top")
    fig.text(0.965, header_y, header_right,
             color=COL["muted"], fontsize=9, ha="right", va="top")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=110, facecolor=COL["bg"], bbox_inches=None)
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# Backfill driver
# ---------------------------------------------------------------------------
def panels_dir(basin: str) -> Path:
    return HERE / "climatology" / BASINS[basin]["long_dir"] / "panels"


def _render_one(args):
    basin, year, current_year, force = args
    out = panels_dir(basin) / f"{year}.png"
    if out.exists() and not force:
        return basin, year, "skip"
    try:
        ace_data = load_ace_data(basin)
        # Only render if we have per-storm tracks for years < current
        if year < current_year:
            tracks_path = HERE / "historical" / basin / "tracks" / f"tracks_{year}.json"
            if not tracks_path.exists():
                return basin, year, "no-tracks"
        render_panel(basin, year, out, ace_data=ace_data, current_year=current_year)
        return basin, year, "ok"
    except Exception as e:
        return basin, year, f"ERROR: {type(e).__name__}: {e}"


def backfill(basins: list[str], year_start: int = 1970,
             year_end: int | None = None, workers: int = 4, force: bool = False):
    today = dt.date.today()
    current_year = today.year
    if year_end is None:
        year_end = current_year
    jobs = []
    for basin in basins:
        for y in range(year_start, year_end + 1):
            jobs.append((basin, y, current_year, force))
    print(f"[backfill] {len(jobs)} panels across {basins} years {year_start}-{year_end}")
    if workers <= 1:
        for j in jobs:
            basin, year, status = _render_one(j)
            print(f"  {basin} {year}: {status}")
        return
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_render_one, j): j for j in jobs}
        for f in as_completed(futures):
            basin, year, status = f.result()
            print(f"  {basin} {year}: {status}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--basin", choices=sorted(BASINS.keys()),
                    help="Basin to render. Omit with --all-basins.")
    ap.add_argument("--all-basins", action="store_true",
                    help="Render for all three basins.")
    ap.add_argument("--year", type=int, default=None,
                    help="Year to render. Omit with --backfill.")
    ap.add_argument("--backfill", action="store_true",
                    help="Render all years 1970..current.")
    ap.add_argument("--year-start", type=int, default=1970)
    ap.add_argument("--year-end", type=int, default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true",
                    help="Re-render even if PNG already exists.")
    ap.add_argument("--out", type=str, default=None,
                    help="Output path override (single-panel mode only).")
    args = ap.parse_args()

    if not args.all_basins and not args.basin:
        ap.error("--basin or --all-basins required")

    basins = sorted(BASINS.keys()) if args.all_basins else [args.basin]

    if args.backfill:
        backfill(basins, year_start=args.year_start, year_end=args.year_end,
                 workers=args.workers, force=args.force)
        return

    year = args.year or dt.date.today().year
    for basin in basins:
        out = Path(args.out) if args.out else (panels_dir(basin) / f"{year}.png")
        if args.out and len(basins) > 1:
            print("--out is single-file; ignoring for additional basins.", file=sys.stderr)
            out = panels_dir(basin) / f"{year}.png"
        ace_data = load_ace_data(basin)
        render_panel(basin, year, out, ace_data=ace_data, current_year=dt.date.today().year)
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
