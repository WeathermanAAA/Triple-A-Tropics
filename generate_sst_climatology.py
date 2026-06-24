#!/usr/bin/env python3
"""Daily SST climatology curves (kouya-style), one per region.

The "curve" product is the spaghetti plot meteorologists know from
ClimateReanalyzer / kouya: for a given region, every historical year's
region-mean SST is drawn as a faint line across the day-of-year axis,
with the 1991–2020 daily mean and the most-recent years highlighted on
top. It answers "where does today sit against the whole record?" at a
glance.

THE DATA PROBLEM. The curve needs region-mean SST for *every day of
every year* (OISST 1982-present ≈ 16k daily grids). Re-downloading 16k
grids on every daily run is infeasible, so we keep a compact cache of
just the scalar region means:

    _sst_clim_build/sst_region_daily_means.npz
        dates   int32  (N,)             YYYYMMDD per day we have
        means   float32 (n_regions, N)  cos-lat-weighted region mean SST
        regions <U..   (n_regions,)      region slugs, row order

That's 18 regions × ~16k days × 4 B ≈ 1.2 MB raw (~0.7 MB compressed) —
small, but it is a build INPUT that is rewritten daily, so it lives on
R2 (cdn.triple-a-tropics.com/sst/sst_region_daily_means.npz) rather than
in git, exactly like armor3d/armor3d_climatology.nc. Never commit it.

Two entry points:
  --backfill   one-time (manual workflow): walk 1982→present, computing
               the per-region means and discarding each grid immediately
               (26 GB of downloads won't fit on a runner). Resumable:
               re-runs skip dates already in the cache, and a
               --time-budget-minutes deadline lets the job stop cleanly
               before GitHub's 6-h hard-kill so the cache survives.
  --update     daily (folded into update-sst.yml): append the newest
               available day's means to the cache.

The region-mean math reuses generate_sst_plots' own _subset_to_extent +
compute_global_mean so the cached scalar equals the cos-lat-weighted
ocean mean of exactly the area each published region map shows.

Phase 2 adds the actual curve rendering; this module is the data layer.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import sys
import time
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np

# Reuse the OISST plumbing + region definitions from the map generator.
# Same directory, so a plain import works both locally and in CI.
import generate_sst_plots as gsp
from generate_sst_plots import (
    REGIONS,
    _draw_basemap,
    _load_geojson,
    _subset_to_extent,
    compute_global_mean,
)

# --- TAT-dark palette (the climatology curves' own theme) ---------------
# Intentionally NOT generate_sst_plots' navy BG_COLOR (#07101c) — these
# curves match the site's neutral dark theme in styles.css :root.
BG = "#131519"        # figure background
PANEL = "#1b1e24"     # header bar / card surface
BORDER = "#2a2e36"    # spines, gridlines, inset frame
FG = "#e8ebef"        # near-white text + climatology mean line
MUTED = "#9199a4"     # secondary text, spaghetti, ticks
CYAN = "#5dd3ff"      # current year (always highlighted)
AMBER = "#ffb83a"     # 2nd highlight colour
RED = "#ef5350"       # lead highlight colour

WATERMARK = "@WeathermanAAA_"

# --- Highlighted years (per region) -------------------------------------
# The CURRENT year is ALWAYS highlighted — bold CYAN, drawn only to the
# latest available day, with the end dot + dotted "now" line. The lists
# here name the ADDITIONAL, fully-drawn past years to colour on top of the
# gray spaghetti: each takes the next colour from HIGHLIGHT_PALETTE
# (newest-first) and gets its own legend entry. The whole 1982-present
# record is already in the R2 cache, so highlighting a past year is just a
# matter of listing it — no backfill needed.
#
# A region NOT listed here falls back to the previous two completed years,
# reproducing the original "current + 2" look.
HIGHLIGHT_YEARS: dict[str, list[int]] = {
    "east-pacific": [2015, 2018],
}

# Colours for the non-current highlighted years, applied newest-first so
# the most recent past year leads with RED (matching the original look).
HIGHLIGHT_PALETTE: list[str] = [
    RED, AMBER, "#c792ea", "#7bd88f", "#ff8a65", "#f06292",
]


def highlight_spec(slug: str, cur: int) -> dict[int, tuple[str, float, str]]:
    """{year: (colour, linewidth, label)} for the coloured lines drawn over
    the spaghetti. The current year is always present (CYAN); the
    region-configured past years — or the default previous two — follow,
    newest-first, in HIGHLIGHT_PALETTE order."""
    past = HIGHLIGHT_YEARS.get(slug)
    if past is None:
        past = [cur - 1, cur - 2]            # default: previous two years
    # Newest first, de-duplicated, current year dropped (always its own CYAN).
    past = sorted({y for y in past if y != cur}, reverse=True)
    spec: dict[int, tuple[str, float, str]] = {cur: (CYAN, 3.0, str(cur))}
    for i, y in enumerate(past):
        spec[y] = (HIGHLIGHT_PALETTE[i % len(HIGHLIGHT_PALETTE)], 2.0, str(y))
    return spec

HERE = Path(__file__).resolve().parent
SST_DIR = HERE / "sst"
SST_DIR.mkdir(parents=True, exist_ok=True)

# Local staging dir for the cache npz (gitignored). The canonical copy
# lives on R2; the workflow fetches it here before --update/--backfill
# and uploads it back afterwards.
CLIM_BUILD_DIR = HERE / "_sst_clim_build"
CLIM_BUILD_DIR.mkdir(exist_ok=True)
CACHE_NAME = "sst_region_daily_means.npz"
DEFAULT_CACHE_PATH = CLIM_BUILD_DIR / CACHE_NAME
# R2 object key (under the sst/ prefix so it's served from the same CDN
# path the rest of the SST products use).
R2_CACHE_KEY = f"sst/{CACHE_NAME}"

OISST_START = dt.date(1982, 1, 1)

# Baseline window for the daily climatology mean — NHC-standard 1991–2020,
# matching generate_sst_plots' CLIMO_START/END so the curve and the map
# anomalies share a baseline.
CLIMO_START = gsp.CLIMO_START   # 1991
CLIMO_END = gsp.CLIMO_END       # 2020

# Row order of the means matrix is locked to REGIONS insertion order so
# the cache stays stable across runs even if REGIONS grows.
REGION_SLUGS: list[str] = list(REGIONS.keys())

LOG = "[sst-clim]"

# A leap reference year gives a fixed 366-slot day-of-year axis so all
# years align on calendar date (March 1 is always position 61), and
# Feb 29 has a real slot that only leap years populate.
REF_YEAR = 2000


# --- Time budget (mirrors build_armor3d_climatology.py) -----------------

_DEADLINE: float | None = None


def _arm_budget(minutes: float | None) -> None:
    global _DEADLINE
    _DEADLINE = None if minutes is None else time.monotonic() + minutes * 60.0


def _budget_exhausted() -> bool:
    return _DEADLINE is not None and time.monotonic() >= _DEADLINE


def _budget_remaining_min() -> float | None:
    if _DEADLINE is None:
        return None
    return max(0.0, (_DEADLINE - time.monotonic()) / 60.0)


# --- Day-of-year helpers ------------------------------------------------

def doy_pos(d: dt.date) -> int:
    """Calendar-aligned day-of-year position, 1..366.

    Uses a fixed leap reference year so the seasonal cycle lines up across
    leap and non-leap years (unlike raw tm_yday, which shifts everything
    after Feb 28 by a day between the two)."""
    return (dt.date(REF_YEAR, d.month, d.day) - dt.date(REF_YEAR, 1, 1)).days + 1


def month_tick_positions() -> tuple[list[int], list[str]]:
    """(positions, labels) for the 1st of each month on the doy axis."""
    pos = [doy_pos(dt.date(REF_YEAR, m, 1)) for m in range(1, 13)]
    labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return pos, labels


# --- Cache I/O ----------------------------------------------------------

def load_records(path: Path) -> dict[dt.date, np.ndarray]:
    """Load the cache npz into {date: means(n_regions,)}.

    Returns {} if the cache is absent. Rows are reordered to the current
    REGION_SLUGS order so adding a region later doesn't scramble history
    (missing rows for old regions are filled with NaN)."""
    if not path.exists():
        return {}
    z = np.load(path, allow_pickle=False)
    ymd = z["dates"].astype(np.int64)
    means = z["means"].astype(np.float32)
    cached_slugs = [str(s) for s in z["regions"]]

    # Map cached rows onto the current slug order.
    row_of = {slug: i for i, slug in enumerate(cached_slugs)}
    records: dict[dt.date, np.ndarray] = {}
    for j, v in enumerate(ymd):
        v = int(v)
        d = dt.date(v // 10000, (v // 100) % 100, v % 100)
        row = np.full(len(REGION_SLUGS), np.nan, np.float32)
        for i, slug in enumerate(REGION_SLUGS):
            src = row_of.get(slug)
            if src is not None:
                row[i] = means[src, j]
        records[d] = row
    return records


def save_records(path: Path, records: dict[dt.date, np.ndarray]) -> None:
    """Serialize {date: means} to the compact npz (dates sorted ascending)."""
    dates = sorted(records)
    ymd = np.array([d.year * 10000 + d.month * 100 + d.day for d in dates],
                   dtype=np.int32)
    if dates:
        means = np.stack([records[d] for d in dates], axis=1).astype(np.float32)
    else:
        means = np.empty((len(REGION_SLUGS), 0), np.float32)
    # Write through an open handle so numpy doesn't helpfully re-append
    # ".npz" to the temp path (it does that when given a str/Path).
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        np.savez_compressed(
            f,
            dates=ymd,
            means=means,
            regions=np.array(REGION_SLUGS),
        )
    tmp.replace(path)


# --- Region-mean computation --------------------------------------------

def region_means_for_grid(sst: np.ndarray, lat: np.ndarray,
                          lon: np.ndarray) -> np.ndarray:
    """cos-lat-weighted, land-excluded mean SST per region for one grid.

    Returns float32 (n_regions,). NaN for any region whose subset is
    empty (shouldn't happen for the standard 18, but defensive)."""
    out = np.full(len(REGION_SLUGS), np.nan, np.float32)
    for i, slug in enumerate(REGION_SLUGS):
        sub, la, _lo = _subset_to_extent(sst, lat, lon, REGIONS[slug]["extent"])
        if sub.size:
            out[i] = compute_global_mean(sub, la)
    return out


def _iter_days(start: dt.date, end_exclusive: dt.date):
    d = start
    one = dt.timedelta(days=1)
    while d < end_exclusive:
        yield d
        d += one


def _fetch_days_concurrent(days: list[dt.date]) -> dict[dt.date, Path]:
    """Download a batch of OISST days in parallel; return {date: ncpath}."""
    out: dict[dt.date, Path] = {}
    with cf.ThreadPoolExecutor(max_workers=gsp.FETCH_WORKERS) as pool:
        futs = {pool.submit(gsp.fetch_day, d, LOG): d for d in days}
        for fut in cf.as_completed(futs):
            d = futs[fut]
            try:
                p = fut.result()
            except Exception:  # noqa: BLE001
                p = None
            if p is not None:
                out[d] = p
    return out


# --- Series + climatology derivation ------------------------------------

def region_series(records: dict[dt.date, np.ndarray],
                  region_idx: int) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """{year: (doy_positions, values)} for one region, sorted by position.

    Drops days whose value is NaN (NCEI gaps). Positions are the
    calendar-aligned 1..366 day-of-year slots."""
    by_year: dict[int, list[tuple[int, float]]] = {}
    for d, row in records.items():
        v = float(row[region_idx])
        if not np.isfinite(v):
            continue
        by_year.setdefault(d.year, []).append((doy_pos(d), v))
    out: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for y, pairs in by_year.items():
        pairs.sort()
        out[y] = (np.array([p for p, _ in pairs], dtype=np.int32),
                  np.array([v for _, v in pairs], dtype=np.float32))
    return out


def climatology_curve(records: dict[dt.date, np.ndarray], region_idx: int,
                      y0: int = CLIMO_START, y1: int = CLIMO_END
                      ) -> np.ndarray:
    """1991–2020 daily-of-year mean as an array indexed by position 1..366.

    Position 0 is unused; positions with no contributing year are NaN
    (only Feb 29 could be thin, and the baseline's 8 leap years cover it)."""
    sums = np.zeros(367, dtype=np.float64)
    cnts = np.zeros(367, dtype=np.float64)
    for d, row in records.items():
        if y0 <= d.year <= y1:
            v = float(row[region_idx])
            if np.isfinite(v):
                p = doy_pos(d)
                sums[p] += v
                cnts[p] += 1
    with np.errstate(invalid="ignore", divide="ignore"):
        clim = np.where(cnts > 0, sums / cnts, np.nan)
    return clim


# --- Plotting -----------------------------------------------------------

def _render_inset(ax, slug: str, anom: np.ndarray, lat: np.ndarray,
                  lon: np.ndarray, countries, coast) -> None:
    """Draw the region's current SST-anomaly map into the inset axes.

    Clean re-render from the latest OISST `anom` field (same data the
    pipeline's anomaly map uses), RdBu_r −3…+3 °C, no colorbar/watermark —
    the shared right-margin colorbar is its key. Dashed muted frame."""
    extent = REGIONS[slug]["extent"]
    sub, la, lo = _subset_to_extent(anom, lat, lon, extent)
    if sub.size:
        LON2, LAT2 = np.meshgrid(lo, la)
        ax.pcolormesh(LON2, LAT2, sub, cmap="RdBu_r",
                      norm=mcolors.Normalize(-3.0, 3.0),
                      shading="auto", rasterized=True, zorder=1)
        _draw_basemap(ax, extent, countries, coast)
        ax.set_xlim(float(lo.min()), float(lo.max()))
        ax.set_ylim(float(la.min()), float(la.max()))
    ax.set_facecolor(PANEL)
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_edgecolor(MUTED)
        sp.set_linewidth(0.9)
        sp.set_linestyle((0, (4, 2)))  # dashed bbox
    ax.set_title("current SST anomaly", color=MUTED, fontsize=8,
                 fontweight="bold", pad=3)


def _inset_figsize(extent: tuple, base: float = 0.052,
                   long_side_min: float = 3.2, long_side_max: float = 6.0
                   ) -> tuple[float, float]:
    """Figure size (inches) matching the region's *geographic* aspect.

    With ``ax.set_aspect("equal")`` the data box is forced square-per-degree,
    so to fill the figure without letterbox padding the figure itself must
    carry the lon/lat span ratio. We scale degrees → inches by ``base`` and a
    ``cos(mean_lat)`` longitude foreshortening (Plate-Carrée-ish), giving a
    figure whose W:H equals the region's true geographic aspect. Only the
    aspect matters (the SVG/CSS sizes the PNG by width), but we clamp the
    LONGER side into ``[long_side_min, long_side_max]`` — scaling both axes
    together so the aspect is preserved — to keep coastline DPI consistent
    and the file a sane size across the 18 wildly different extents."""
    lon_min, lon_max, lat_min, lat_max = extent
    lon_span = max(1.0, float(lon_max - lon_min))
    lat_span = max(1.0, float(lat_max - lat_min))
    mean_lat = (lat_min + lat_max) / 2.0
    coslat = max(0.25, float(np.cos(np.radians(mean_lat))))
    w = lon_span * coslat * base
    h = lat_span * base
    # Clamp the long side, scaling both dimensions by the same factor so the
    # true geographic aspect ratio is never distorted by the clamp itself.
    long_side = max(w, h)
    target = float(np.clip(long_side, long_side_min, long_side_max))
    scale = target / long_side
    return w * scale, h * scale


def render_anom_inset(slug: str, anom_grid, countries, coast,
                      out_path: Path) -> bool:
    """Standalone, fixed-aspect SST-anomaly map for one region.

    This is the inset used by the interactive widget, drawn *beside* the
    curves rather than over them. Unlike the combined-PNG inset it sets
    ``ax.set_aspect("equal")`` and sizes the figure to the region's
    geographic aspect (see ``_inset_figsize``), so coastlines are
    undistorted. Same RdBu_r −3…+3 °C palette, dashed muted frame, with a
    tiny in-corner −3…+3 scale label instead of a colorbar."""
    if anom_grid is None:
        return False
    extent = REGIONS[slug]["extent"]
    fig = plt.figure(figsize=_inset_figsize(extent), facecolor=BG)
    ax = fig.add_axes([0.02, 0.02, 0.96, 0.90])
    _render_inset(ax, slug, anom_grid[0], anom_grid[1], anom_grid[2],
                  countries, coast)
    # The shared colorbar lives on the combined PNG; here a tiny corner
    # label documents the −3…+3 °C RdBu_r scale so the standalone map is
    # self-explanatory beside the curves.
    ax.set_aspect("equal")
    # set_aspect("equal") letterboxes wide/tall regions, so the corner label can
    # land in a dark margin bar; a semi-opaque chip keeps it readable everywhere.
    ax.text(0.015, 0.03, "anomaly −3…+3 °C", transform=ax.transAxes,
            color=FG, fontsize=6.5, ha="left", va="bottom", fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.25", facecolor=BG, alpha=0.72,
                      edgecolor="none"))
    fig.savefig(out_path, dpi=150, facecolor=BG)
    plt.close(fig)
    return True


def _round_or_none(v: float) -> float | None:
    return None if (v is None or not np.isfinite(v)) else round(float(v), 2)


def write_region_json(slug: str, records: dict[dt.date, np.ndarray],
                      region_idx: int, out_path: Path) -> bool:
    """Emit the per-region curve payload the interactive widget draws.

    Mirrors exactly the arrays the PNG plots: every year's daily region
    mean (gray spaghetti), the 1991–2020 daily mean (clim), the y-limits,
    and the highlight defaults/colours. Each series is a length-366 array
    indexed by day-of-year position 1..366 (array index i → doy i+1), with
    `null` on NCEI gap days so the JS can break the line. SST rounded to
    2 dp. Returns False if the region has no usable data."""
    series = region_series(records, region_idx)
    if not series:
        return False
    clim = climatology_curve(records, region_idx)
    cur = dt.datetime.utcnow().date().year
    highlight = highlight_spec(slug, cur)

    years = sorted(series)

    # series[year] → length-366 list (doy pos 1..366) with null on gaps.
    series_out: dict[str, list] = {}
    for y in years:
        pos, val = series[y]
        arr: list = [None] * 366
        for p, v in zip(pos.tolist(), val.tolist()):
            if 1 <= p <= 366:
                arr[p - 1] = _round_or_none(v)
        series_out[str(y)] = arr

    clim_out = [_round_or_none(float(clim[p])) for p in range(1, 367)]

    # Latest available current-year point (end-dot + "now" line in the SVG).
    latest = None
    if cur in series:
        pos, val = series[cur]
        if pos.size:
            latest = {"doy": int(pos[-1]), "val": _round_or_none(float(val[-1]))}

    # y-limits matching the PNG's auto-limit logic (incl. the headroom pad
    # the PNG applies) so the SVG y-domain agrees with the baked image.
    allvals = np.concatenate([v for _, v in series.values()])
    finite = allvals[np.isfinite(allvals)]
    if finite.size:
        ymin = round(float(np.min(finite)) - 0.3, 2)
        ymax = round(float(np.max(finite)) + 0.5, 2)
    else:
        ymin, ymax = 0.0, 1.0

    present = set(years)
    # Default the interactive view to exactly the years the baked PNG highlights
    # (current + the region-configured HIGHLIGHT_YEARS, else the previous two),
    # newest-first — so a region like east-pacific surfaces its curated El Niño
    # comparison years (2015/2018) by default instead of hiding them behind a
    # toggle, and the SVG default matches the no-JS PNG fallback.
    default_years = [y for y in sorted(highlight, reverse=True) if y in present]

    # Highlight colours: current → cyan, configured/default past years take
    # the same palette slots the PNG uses (newest-first). highlight_spec is
    # the single source of truth so the SVG matches the PNG legend.
    highlight_colors = {str(y): col for y, (col, _lw, _lab) in highlight.items()
                        if y in present}

    payload = {
        "slug": slug,
        "label": REGIONS[slug]["label"],
        "cur": cur,
        "years": years,
        "series": series_out,
        "clim": clim_out,
        "latest": latest,
        "ymin": ymin,
        "ymax": ymax,
        "default_years": default_years,
        "highlight_colors": highlight_colors,
        "units": "°C",
        "climo_label": "1991–2020 mean",
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    return True


def plot_region_curve(slug: str, records: dict[dt.date, np.ndarray],
                      region_idx: int, latest_date: dt.date,
                      anom_grid: tuple[np.ndarray, np.ndarray, np.ndarray] | None,
                      countries, coast, out_path: Path) -> bool:
    """Render one region's kouya-style climatology curve PNG. Returns
    True on success, False if the region has too little data."""
    series = region_series(records, region_idx)
    if not series:
        print(f"{LOG} {slug}: no data, skip render")
        return False
    clim = climatology_curve(records, region_idx)

    cur = dt.datetime.utcnow().date().year
    highlight = highlight_spec(slug, cur)

    # Widened from 11→13 in so the anomaly inset + colorbar live in a
    # dedicated right-hand gutter (B1): the curves fill the left axes
    # edge-to-edge, so any inset overlaid on the plot would cover them.
    fig = plt.figure(figsize=(13.0, 7.0), facecolor=BG)

    # --- Header bar -----------------------------------------------------
    hax = fig.add_axes([0.0, 0.915, 1.0, 0.085])
    hax.set_facecolor(PANEL)
    hax.set_xlim(0, 1)
    hax.set_ylim(0, 1)
    hax.set_xticks([])
    hax.set_yticks([])
    for sp in hax.spines.values():
        sp.set_visible(False)
    # Thin bottom border.
    hax.axhline(0.02, color=BORDER, linewidth=1.2, zorder=5)
    hax.text(0.012, 0.5, REGIONS[slug]["label"], color=FG, fontsize=16,
             fontweight="bold", va="center", ha="left")
    hax.text(0.988, 0.5,
             f"daily SST climatology  ·  as of {latest_date:%Y-%m-%d}",
             color=MUTED, fontsize=11, va="center", ha="right")

    # --- Main plot ------------------------------------------------------
    # Curves occupy the left ~62% of the figure; the right ~32% is a gutter
    # holding the (now fixed-aspect) anomaly inset and the colorbar, so no
    # plotted line is ever covered (B1).
    ax = fig.add_axes([0.06, 0.16, 0.595, 0.70])
    ax.set_facecolor(BG)

    # Gray spaghetti — every historical year except the highlighted ones
    # (those get drawn in color on top).
    for y in sorted(series):
        if y in highlight:
            continue
        pos, val = series[y]
        ax.plot(pos, val, color=MUTED, alpha=0.18, linewidth=0.6,
                zorder=1, solid_capstyle="round")

    # 1991–2020 daily mean — dashed, near-white.
    xs = np.arange(1, 367)
    ax.plot(xs, clim[1:367], color=FG, linewidth=2.2, linestyle="--",
            zorder=4, label="1991–2020 mean")

    # Highlighted past years — full-year coloured lines over the spaghetti,
    # drawn oldest → newest so more-recent years sit on top (the current
    # year, below, then sits on top of all of them).
    for y in sorted(y for y in highlight if y != cur):
        if y in series:
            color, lw, lab = highlight[y]
            pos, val = series[y]
            ax.plot(pos, val, color=color, linewidth=lw, zorder=5, label=lab)

    # Current year — only through the latest available day, with an end
    # dot and a dotted "now" line.
    if cur in series:
        color, lw, lab = highlight[cur]
        pos, val = series[cur]
        ax.plot(pos, val, color=color, linewidth=lw, zorder=7, label=lab)
        ax.plot(pos[-1], val[-1], "o", color=color, markersize=5.5,
                markeredgecolor=BG, markeredgewidth=0.8, zorder=8)
        ax.axvline(pos[-1], color=color, linewidth=1.0, linestyle=":",
                   alpha=0.55, zorder=3)

    # A faint proxy entry so the legend documents the gray lines.
    ax.plot([], [], color=MUTED, alpha=0.5, linewidth=1.2,
            label="all years 1982–present")

    # Auto y-limits with ~0.5 °C headroom so warm-year peaks never clip.
    allvals = np.concatenate([v for _, v in series.values()])
    finite = allvals[np.isfinite(allvals)]
    if finite.size:
        ymin, ymax = float(np.min(finite)), float(np.max(finite))
        ax.set_ylim(ymin - 0.3, ymax + 0.5)

    ax.set_xlim(1, 366)
    pos_ticks, lab_ticks = month_tick_positions()
    ax.set_xticks(pos_ticks)
    ax.set_xticklabels(lab_ticks)
    ax.set_ylabel("region-mean SST (°C)", color=FG, fontsize=11)
    ax.tick_params(colors=MUTED, labelsize=9)
    for sp in ax.spines.values():
        sp.set_color(BORDER)
    ax.grid(True, color=BORDER, linewidth=0.5, alpha=0.55)
    ax.set_axisbelow(True)

    # --- Inset anomaly map (right gutter, fixed aspect) -----------------
    # Placed in the right-hand gutter (outside the curve axes) so it never
    # overlaps a plotted line (B1), with set_aspect("equal") so coastlines
    # are undistorted (B2). The axes box is generous; "equal" letterboxes
    # the data inside it to the region's true geographic aspect.
    if anom_grid is not None:
        iax = fig.add_axes([0.71, 0.46, 0.265, 0.40])
        _render_inset(iax, slug, anom_grid[0], anom_grid[1], anom_grid[2],
                      countries, coast)
        iax.set_aspect("equal")

    # --- Shared anomaly colorbar (right gutter, below the inset) --------
    cax = fig.add_axes([0.74, 0.20, 0.20, 0.022])
    sm = plt.cm.ScalarMappable(cmap="RdBu_r",
                               norm=mcolors.Normalize(-3.0, 3.0))
    sm.set_array([])
    cbar = fig.colorbar(sm, cax=cax, extend="both", orientation="horizontal")
    cbar.set_label("SST anomaly (°C)", color=MUTED, fontsize=9)
    cbar.set_ticks(np.arange(-3, 4, 1))
    cbar.ax.xaxis.set_tick_params(color=MUTED, labelcolor=MUTED, labelsize=8)
    cbar.outline.set_edgecolor(BORDER)

    # --- Legend — frameless, single row, centered below the curve axes --
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center",
               bbox_to_anchor=(0.357, 0.045), ncol=len(labels),
               frameon=False, fontsize=10, labelcolor=FG,
               handlelength=1.8, columnspacing=1.6)

    # --- Footer ---------------------------------------------------------
    fig.text(0.06, 0.018, "source: NOAA OISST", color=MUTED, fontsize=9,
             ha="left", va="bottom")
    fig.text(0.975, 0.018, WATERMARK, color=MUTED, fontsize=9,
             ha="right", va="bottom", fontweight="bold")

    fig.savefig(out_path, dpi=150, facecolor=BG)
    plt.close(fig)
    return True


def render_all(records: dict[dt.date, np.ndarray], latest_date: dt.date,
               anom_grid, slugs: list[str]) -> int:
    """Render the curve PNG for each requested region into sst/."""
    countries = (_load_geojson("ne_50m_admin_0_countries.geojson")
                 or _load_geojson("ne_110m_admin_0_countries.geojson"))
    coast = (_load_geojson("ne_50m_coastline.geojson")
             or _load_geojson("ne_110m_coastline.geojson"))
    n = 0
    for slug in slugs:
        idx = REGION_SLUGS.index(slug)
        out_path = SST_DIR / f"{slug}_climatology.png"
        if plot_region_curve(slug, records, idx, latest_date, anom_grid,
                             countries, coast, out_path):
            print(f"{LOG} wrote {out_path}")
            n += 1
            # Per-region interactive payload (rides the same sst/ → R2
            # dir-sync as the PNG).
            json_path = SST_DIR / f"{slug}_climatology.json"
            if write_region_json(slug, records, idx, json_path):
                print(f"{LOG} wrote {json_path}")
            # Standalone fixed-aspect anomaly inset (beside the SVG curves).
            inset_path = SST_DIR / f"{slug}_anom_inset.png"
            if render_anom_inset(slug, anom_grid, countries, coast, inset_path):
                print(f"{LOG} wrote {inset_path}")
    print(f"{LOG} rendered {n}/{len(slugs)} region curve(s)")
    return n


# --- Commands -----------------------------------------------------------

def cmd_backfill(cache_path: Path, start_year: int, end_year: int | None) -> int:
    """Build/extend the per-region daily-mean cache, oldest day first.

    Processes one calendar year at a time so disk stays bounded (~600 MB
    of NetCDF per year, deleted before moving on) and so we have a clean
    checkpoint after each year. Resumable: dates already cached are
    skipped; honors the --time-budget deadline between years AND between
    download batches within a year."""
    records = load_records(cache_path)
    print(f"{LOG} cache loaded: {len(records)} days already present "
          f"({cache_path})")

    today = dt.datetime.utcnow().date()
    last_year = end_year if end_year is not None else today.year
    rem = _budget_remaining_min()
    if rem is not None:
        print(f"{LOG} time budget: {rem:.1f} min")

    total_added = 0
    for year in range(start_year, last_year + 1):
        if _budget_exhausted():
            print(f"{LOG} budget exhausted before {year}; stopping cleanly.")
            break

        y_start = max(dt.date(year, 1, 1), OISST_START)
        y_end = min(dt.date(year + 1, 1, 1), today)  # exclusive; up to yesterday
        if y_start >= y_end:
            continue

        missing = [d for d in _iter_days(y_start, y_end) if d not in records]
        if not missing:
            print(f"{LOG} {year}: complete ({(y_end - y_start).days} days), skip.")
            continue

        print(f"{LOG} {year}: fetching {len(missing)} missing day(s) ...")
        added_this_year = 0
        # Sub-batch within the year so a mid-year budget stop still
        # checkpoints the partial year, and disk never holds a full year
        # of grids at once on slow links.
        BATCH = 120
        for k in range(0, len(missing), BATCH):
            if _budget_exhausted():
                print(f"{LOG} {year}: budget hit mid-year after "
                      f"{added_this_year} day(s).")
                break
            batch = missing[k:k + BATCH]
            paths = _fetch_days_concurrent(batch)
            for d in batch:
                p = paths.get(d)
                if p is None:
                    continue  # NCEI gap / 404 — leave the day out
                try:
                    sst, lat, lon = gsp.read_sst_grid(p)
                    records[d] = region_means_for_grid(sst, lat, lon)
                    added_this_year += 1
                except Exception as e:  # noqa: BLE001
                    print(f"{LOG}   read error {d}: {type(e).__name__}: {e}",
                          file=sys.stderr)
                finally:
                    # Discard the grid immediately — 16k × 1.6 MB won't fit.
                    try:
                        p.unlink()
                    except OSError:
                        pass

        if added_this_year:
            save_records(cache_path, records)  # checkpoint after each year
            total_added += added_this_year
            rem = _budget_remaining_min()
            tail = f" · {rem:.1f} min left" if rem is not None else ""
            print(f"{LOG} {year}: +{added_this_year} day(s), "
                  f"cache now {len(records)} days{tail}")

    save_records(cache_path, records)
    span = (min(records), max(records)) if records else (None, None)
    print(f"{LOG} backfill done: +{total_added} this run, "
          f"{len(records)} days total, span {span[0]}..{span[1]}")
    return 0


def _resolve_slugs(arg: str | None) -> list[str]:
    """Comma-separated --regions filter → validated slug list (all if None)."""
    if not arg:
        return list(REGION_SLUGS)
    out = []
    for s in arg.split(","):
        s = s.strip()
        if not s:
            continue
        if s not in REGION_SLUGS:
            raise SystemExit(f"{LOG} unknown region '{s}'. "
                             f"Known: {', '.join(REGION_SLUGS)}")
        out.append(s)
    return out


def cmd_update(cache_path: Path, regions: str | None,
               render: bool = True) -> int:
    """Append the newest available OISST day, then render the curves.

    The latest day's grid doubles as the inset anomaly source and fixes
    the 'as of' date + the current-year line's endpoint, so we read it
    once for both purposes."""
    records = load_records(cache_path)
    before = len(records)
    d, p = gsp.latest_available_day(LOG)  # cache hit if plots step ran first
    sst, lat, lon = gsp.read_sst_grid(p)
    if d in records:
        print(f"{LOG} latest available {d} already in cache "
              f"({before} days); not appending.")
    else:
        records[d] = region_means_for_grid(sst, lat, lon)
        save_records(cache_path, records)
        print(f"{LOG} appended {d}; cache now {len(records)} days "
              f"(was {before}).")
    if render:
        anom, alat, alon = gsp.read_sst_grid(p, var_name="anom")
        render_all(records, d, (anom, alat, alon), _resolve_slugs(regions))
    return 0


def cmd_render_only(cache_path: Path, regions: str | None) -> int:
    """Re-render curves from the existing cache without appending a day.

    Still fetches the latest available grid (cache hit in CI) for the
    inset map + the 'as of' date. Handy for local style iteration."""
    records = load_records(cache_path)
    if not records:
        raise SystemExit(f"{LOG} cache {cache_path} is empty — run "
                         f"--backfill or --update first.")
    d, p = gsp.latest_available_day(LOG)
    anom, alat, alon = gsp.read_sst_grid(p, var_name="anom")
    render_all(records, d, (anom, alat, alon), _resolve_slugs(regions))
    return 0


# --- CLI ----------------------------------------------------------------

def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--backfill", action="store_true",
                      help="One-time: build the full 1982-present cache "
                           "(resumable).")
    mode.add_argument("--update", action="store_true",
                      help="Daily: append the latest available day, then "
                           "render the curves.")
    mode.add_argument("--render-only", action="store_true",
                      help="Re-render curves from the cache without "
                           "appending (local iteration).")
    p.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH,
                   help="Cache npz path (default: %(default)s).")
    p.add_argument("--regions", default=None,
                   help="Comma-separated region slugs to render "
                        "(default: all 18).")
    p.add_argument("--start-year", type=int, default=OISST_START.year,
                   help="Backfill start year (default: %(default)s).")
    p.add_argument("--end-year", type=int, default=None,
                   help="Backfill end year inclusive (default: current).")
    p.add_argument("--time-budget-minutes", type=float, default=None,
                   help="Stop backfill cleanly after this many minutes so "
                        "CI can save state before the 6-h hard-kill.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    _arm_budget(args.time_budget_minutes)
    if args.backfill:
        return cmd_backfill(args.cache, args.start_year, args.end_year)
    if args.render_only:
        return cmd_render_only(args.cache, args.regions)
    return cmd_update(args.cache, args.regions)


if __name__ == "__main__":
    raise SystemExit(main())
