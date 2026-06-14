#!/usr/bin/env python3
"""
Triple-A-Tropics · SST animation generator (90-day MP4 family)
==============================================================

Renders one MP4 per (region × product) for the SST family, intended to
be force-pushed to the orphan `mp4-artifacts` branch and consumed by
the on-page <video> widget.

Architecture
------------
This script is a sibling of `generate_sst_plots.py` — it imports that
module and reuses its data-fetch + climo + basemap + colormap +
watermark helpers verbatim so the animation visual style and
numerical baseline both match the static maps with no parallel
pipeline to maintain.

For each day in the 90-day window (today-89 → today inclusive):
  * Fetch OISST + CRW SST (re-uses .sst_cache populated by the daily
    static run; only the newest day actually triggers a download).
  * Recompute the OISST and CRW 1991–2020 climatologies for that
    day-of-year (via `gsp.compute_oisst_climo_for_date` and
    `gsp.compute_crw_climo_for_date`) so the anomaly products use
    EXACTLY the same baseline as the static `/sst/` page.
  * Derive `anomaly = sst - climo` and `anomaly_gmr = anomaly -
    global_mean(anomaly)` for both OISST and CRW; CRW also emits
    `crw_actual` (raw SST) for parity with OISST `actual`.
  * For each (region × core product), render a frame at FRAME_DPI to
    `_frame_cache/sst/{region}/{product}/{YYYYMMDD}.png` IF that frame
    is not already on disk. This is the incremental-cache lever — on
    warm runs only the newest day produces ~72 new PNGs.

Then for each (region × core product):
  * Stitch the last WINDOW_DAYS cached frames into an h.264 MP4 via
    the system ffmpeg (preinstalled on ubuntu-latest).
  * Save the last frame as a JPG poster so <video poster=…> shows a
    meaningful still before playback.

Finally write a per-family `manifest.json` + a branch-root README and
stage everything under `_mp4_build/sst/` for the workflow's orphan-
branch push step.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gc
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import matplotlib as mpl
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

# Reuse fetch + style + colormap + climo helpers from the static SST
# generator. Anomaly products in this animator share the EXACT same
# 1991–2020 baseline the static `/sst/` page uses for its anomaly
# tabs — climatologies are computed via gsp.compute_*_climo_for_date.
import generate_sst_plots as gsp


HERE = Path(__file__).resolve().parent
FRAME_CACHE_DIR = HERE / "_frame_cache" / "sst"
BUILD_DIR = HERE / "_mp4_build"
SST_BUILD_DIR = BUILD_DIR / "sst"

WINDOW_DAYS = 90
FPS = 15

# The live manifest on R2 (read-only public CDN). A partial run (one shard's
# product family) MERGES against this so it never drops the OTHER family's clips
# — see _write_manifest. Lets the OISST shard (Job 1) and the CRW shard (Job 2)
# each publish independently without clobbering each other's entries.
LIVE_MANIFEST_URL = "https://cdn.triple-a-tropics.com/sst/manifest.json"
# Match generate_sst_plots.py savefig dpi so animator frames have the
# same pixel dimensions as the on-site static PNG for each region.
# Anything lower quietly downscales the frame before ffmpeg sees it,
# which reads as blur in the encoded MP4.
FRAME_DPI = 150
# Keep a small over-window cushion in the cache so a single skipped CI
# run (e.g. CMEMS hiccup) doesn't immediately invalidate cached frames.
CACHE_KEEP_DAYS = WINDOW_DAYS + 14

# OISST publishes prelim then final per day; the animator uses render-
# once caching: whichever version is captured first stays in the cache
# for the rest of that date's lifetime in the rolling window. The
# upstream fetcher (gsp.fetch_day_versioned) prefers prelim because it
# is the single consistent operational pipeline available across the
# whole window — see that function's docstring for the full rationale.

OISST_PRODUCT_SLUGS = frozenset({
    "actual", "anomaly", "anomaly_records", "anomaly_gmr",
})


def _is_oisst_product(product: dict) -> bool:
    return product["slug"] in OISST_PRODUCT_SLUGS


# ----------------------------------------------------------------------
# Core product config
# ----------------------------------------------------------------------
# Each product is a dict the renderer + manifest both read. Adding a
# new product later means appending an entry here — no other code path
# needs to change.
CORE_PRODUCTS: list[dict] = [
    {
        "slug": "actual",
        "label": "SST (°C)",
        "description": "OISST v2.1 absolute sea-surface temperature.",
        "cmap": "actual",
        "vmin": 0.0,
        "vmax": 32.0,
        "cbar_label": "Sea-surface temperature (°C)",
        "cbar_ticks": list(range(0, 33, 4)),
        "cbar_extend": "both",
        "title_suffix": "Sea-Surface Temperature",
        "subtitle_src": "OISST v2.1 (NOAA NCEI)",
        # Integer-degree contours matching the static /sst/ actual plot.
        # Anomaly products intentionally skip this (different visual
        # language — a zero-line contour, not a full-grid overlay).
        "contour": True,
        # Every product bumped in lockstep when FRAME_DPI went 100→150;
        # old cached frames are the wrong pixel size and must be
        # re-rendered under a fresh path. Old dirs age out via
        # _prune_old_frames.
        "cache_version": 3,
    },
    {
        "slug": "anomaly",
        "label": "SST anomaly (°C)",
        "description": (
            "OISST v2.1 SST anomaly vs the 1991–2020 daily climatology "
            "(same baseline as the static /sst/ page)."
        ),
        "cmap": "anom",
        "vmin": -5.0,
        "vmax": 5.0,
        "cbar_label": "SST anomaly (°C)",
        "cbar_ticks": list(range(-5, 6)),
        "cbar_extend": "both",
        "title_suffix": "SST Anomaly",
        "subtitle_src": "OISST v2.1 · vs 1991–2020",
        "cache_version": 2,
    },
    {
        # Same anomaly field as `anomaly`, with diagonal-hatch stippling
        # over pixels whose value meets/exceeds the per-DOY OISST record
        # envelope (1982-present, excluding the current year). Mirrors the
        # static `<region>_anomaly_records.png` plot exactly. Records
        # masks are passed through `_render_frame`'s extras dict.
        "slug": "anomaly_records",
        "label": "SST anomaly + daily records (°C)",
        "description": (
            "OISST v2.1 SST anomaly vs the 1991–2020 daily climatology, "
            "with diagonal stippling where today's value meets or "
            "exceeds the warmest (///) or coldest (\\\\) value ever "
            "observed for this day-of-year since 1982."
        ),
        "cmap": "anom",
        "vmin": -5.0,
        "vmax": 5.0,
        "cbar_label": "SST anomaly (°C)",
        "cbar_ticks": list(range(-5, 6)),
        "cbar_extend": "both",
        "title_suffix": "SST Anomaly with Daily Records",
        "subtitle_src": "OISST v2.1 · vs 1991–2020 · records vs 1982-present",
        "records_overlay": True,
        "cache_version": 1,
    },
    {
        "slug": "anomaly_gmr",
        "label": "SSTA − global mean (°C)",
        "description": (
            "OISST anomaly with each day's area-weighted global-mean "
            "SSTA subtracted. Highlights spatial patterns vs the global "
            "warming trend. Same 1991–2020 baseline as the anomaly "
            "above."
        ),
        "cmap": "anom",
        "vmin": -3.0,
        "vmax": 3.0,
        "cbar_label": "SSTA − global mean (°C)",
        "cbar_ticks": list(range(-3, 4)),
        "cbar_extend": "both",
        "title_suffix": "SSTA − Global Mean SSTA",
        "subtitle_src": "OISST v2.1 · vs 1991–2020 · GMR",
        "cache_version": 2,
    },
    {
        "slug": "crw_actual",
        "label": "CRW SST (°C)",
        "description": (
            "Coral Reef Watch 5 km absolute sea-surface temperature."
        ),
        "cmap": "actual",
        "vmin": 0.0,
        "vmax": 32.0,
        "cbar_label": "Sea-surface temperature (°C)",
        "cbar_ticks": list(range(0, 33, 4)),
        "cbar_extend": "both",
        "title_suffix": "CRW · Sea Surface Temperature",
        "subtitle_src": "5 km Coral Reef Watch · daily",
        # Integer-degree contours, matching the OISST `actual` entry — same
        # visual language across both absolute-SST products.
        "contour": True,
        "cache_version": 1,
    },
    {
        "slug": "crw_anomaly",
        "label": "CRW SST anomaly (°C)",
        "description": (
            "Coral Reef Watch 5 km SST anomaly vs the 1991–2020 daily "
            "climatology (same baseline as the static /sst/ CRW page)."
        ),
        "cmap": "anom",
        "vmin": -5.0,
        "vmax": 5.0,
        "cbar_label": "SST anomaly (°C)",
        "cbar_ticks": list(range(-5, 6)),
        "cbar_extend": "both",
        "title_suffix": "CRW SST Anomaly",
        "subtitle_src": "Coral Reef Watch 5 km · vs 1991–2020",
        "cache_version": 2,
    },
    {
        # CRW counterpart of `anomaly_records`. Same overlay treatment,
        # CRW's CoralTemp v3.1 archive starts in 1985 so the records
        # baseline is shorter than OISST's by ~3 years.
        "slug": "crw_anomaly_records",
        "label": "CRW SST anomaly + daily records (°C)",
        "description": (
            "Coral Reef Watch 5 km SST anomaly vs the 1991–2020 daily "
            "climatology, with diagonal stippling where today's value "
            "meets or exceeds the warmest (///) or coldest (\\\\) value "
            "ever observed for this day-of-year since 1985."
        ),
        "cmap": "anom",
        "vmin": -5.0,
        "vmax": 5.0,
        "cbar_label": "SST anomaly (°C)",
        "cbar_ticks": list(range(-5, 6)),
        "cbar_extend": "both",
        "title_suffix": "CRW SST Anomaly with Daily Records",
        "subtitle_src": "Coral Reef Watch 5 km · vs 1991–2020 · records vs 1985-present",
        "records_overlay": True,
        "cache_version": 1,
    },
    {
        "slug": "crw_anomaly_gmr",
        "label": "CRW SSTA − global mean (°C)",
        "description": (
            "Coral Reef Watch SST anomaly with each day's area-weighted "
            "global-mean SSTA subtracted. Highlights spatial patterns vs "
            "the global warming trend. Same 1991–2020 baseline as the "
            "CRW anomaly above."
        ),
        "cmap": "anom",
        "vmin": -3.0,
        "vmax": 3.0,
        "cbar_label": "SSTA − global mean (°C)",
        "cbar_ticks": list(range(-3, 4)),
        "cbar_extend": "both",
        "title_suffix": "CRW · SST Anomaly (Global Mean Removed)",
        "subtitle_src": "Coral Reef Watch 5 km · vs 1991–2020 · GMR",
        "cache_version": 1,
    },
]
PRODUCT_BY_SLUG: dict[str, dict] = {p["slug"]: p for p in CORE_PRODUCTS}


# ----------------------------------------------------------------------
# Per-day data loading
# ----------------------------------------------------------------------
def _load_oisst_sst(d: dt.date, log: str
                    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, str] | None:
    """Returns (sst, lat, lon, version) for one OISST day, or None.

    `version` is "final" or "prelim" — passed through to the PNG cache
    path so the encoder can prefer final-version frames once they land
    upstream. The animator recomputes anomalies from raw SST + climo, so
    we only need the `sst` variable here (no `anom` read)."""
    res = gsp.fetch_day_versioned(d, log)
    if res is None:
        return None
    p, version = res
    try:
        sst, lat, lon = gsp.read_sst_grid(p, var_name="sst")
    except Exception as e:  # noqa: BLE001
        print(f"{log}   ! OISST {d} read error: {e}", file=sys.stderr)
        return None
    return sst, lat, lon, version


def _load_crw_sst(d: dt.date, log: str
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Returns (sst, lat, lon) for one CRW SST day, or None.

    Uses the raw CRW SST product (`coraltemp_v3.1`); the matching
    climatology comes from gsp.compute_crw_climo_for_date so the
    anomaly is on the same 1991–2020 baseline as the static page."""
    p = gsp.fetch_crw_day(d, "sst", log)
    if p is None:
        return None
    try:
        sst, lat, lon = gsp.read_crw_grid(p, "analysed_sst")
    except Exception as e:  # noqa: BLE001
        print(f"{log}   ! CRW SST {d} read error: {e}", file=sys.stderr)
        return None
    return sst, lat, lon


# ----------------------------------------------------------------------
# Climatology cache
# ----------------------------------------------------------------------
# DOYs repeat across the window-end advancing day-by-day, but for a
# single run we only need each DOY once across all 90 frames. Cache by
# (source, month, day) so the per-day loop's two anomaly computations
# share a single fetch+nanmean per DOY.
_OISST_CLIMO_CACHE: dict[tuple[int, int], np.ndarray | None] = {}
_CRW_CLIMO_CACHE:   dict[tuple[int, int], np.ndarray | None] = {}
# (record_max, record_min) per (month, day) for OISST. Per-DOY because
# the records envelope only depends on calendar day-of-year, so each
# DOY in the 90-day window gets computed at most once regardless of how
# many regions iterate over it. Bounded to the current DOY ± 1 by
# `_trim_records_cache` since the render walks dates sequentially —
# without that bound, the CRW variant alone holds ~6 GB by DOY 30 of a
# cold render and OOM-kills the runner.
_OISST_RECORDS_CACHE: dict[
    tuple[int, int], tuple[np.ndarray, np.ndarray] | None
] = {}
# Same shape, CRW source. CoralTemp v3.1 (5 km) — ~200 MB per max/min
# pair vs ~10 MB for OISST 0.25°, so the cache bound matters more here.
_CRW_RECORDS_CACHE: dict[
    tuple[int, int], tuple[np.ndarray, np.ndarray] | None
] = {}


def _oisst_climo(d: dt.date, log: str) -> np.ndarray | None:
    key = (d.month, d.day)
    if key in _OISST_CLIMO_CACHE:
        return _OISST_CLIMO_CACHE[key]
    climo, _ = gsp.compute_oisst_climo_for_date(d, log)
    _OISST_CLIMO_CACHE[key] = climo
    return climo


def _crw_climo(d: dt.date, log: str) -> np.ndarray | None:
    key = (d.month, d.day)
    if key in _CRW_CLIMO_CACHE:
        return _CRW_CLIMO_CACHE[key]
    climo, _ = gsp.compute_crw_climo_for_date(d, log)
    _CRW_CLIMO_CACHE[key] = climo
    return climo


def _trim_records_cache(
    cache: dict[tuple[int, int], tuple[np.ndarray, np.ndarray] | None],
    d: dt.date,
) -> None:
    """Drop cache entries whose (month, day) isn't within ±1 day of `d`.

    The cold-render walks the 90-day window in date order and never
    revisits a DOY, so a 3-DOY sliding window covers the next call. A
    full-resolution OISST max/min pair is ~10 MB; CRW (5 km) is ~200 MB.
    Without bounding the cache, by DOY 30 the CRW cache alone holds
    ~6 GB of arrays that will never be read again — which OOM-kills
    the runner partway through a cold render.
    """
    keep: set[tuple[int, int]] = set()
    for offset in (-1, 0, 1):
        try:
            nd = d + dt.timedelta(days=offset)
        except OverflowError:
            continue
        keep.add((nd.month, nd.day))
    for k in list(cache.keys()):
        if k not in keep:
            del cache[k]


def _oisst_records(d: dt.date, target_year: int, log: str
                   ) -> tuple[np.ndarray, np.ndarray] | None:
    """(record_max, record_min) over OISST history for (d.month, d.day).

    Mirrors the static /sst/ records computation but streams the reduce:
    instead of stacking every year for this DOY into a (n_years, lat,
    lon) array and calling np.nanmax/nanmin, we iterate years and update
    running np.fmax/fmin in place so peak memory is O(2 grids) instead
    of O(n_years × grid). The cache is bounded to current DOY ± 1 (see
    `_trim_records_cache`) since the cold-render walks dates
    sequentially. Historical NetCDFs themselves are still disk-cached
    by `gsp.fetch_day`, so warm runs only re-read from disk."""
    key = (d.month, d.day)
    if key not in _OISST_RECORDS_CACHE:
        hist_years = range(gsp.RECORDS_START, target_year)
        hist = gsp.day_of_year_files(d.month, d.day, hist_years, log)
        rmax: np.ndarray | None = None
        rmin: np.ndarray | None = None
        for y in sorted(hist):
            try:
                g, _, _ = gsp.read_sst_grid(hist[y])
            except Exception:  # noqa: BLE001
                continue
            if rmax is None:
                rmax = g.copy()
                rmin = g
            else:
                if g.shape != rmax.shape:
                    continue
                np.fmax(rmax, g, out=rmax)
                np.fmin(rmin, g, out=rmin)
            del g
        _OISST_RECORDS_CACHE[key] = (
            (rmax, rmin) if rmax is not None and rmin is not None else None
        )
    _trim_records_cache(_OISST_RECORDS_CACHE, d)
    gc.collect()
    return _OISST_RECORDS_CACHE.get(key)


def _crw_records(d: dt.date, target_year: int, log: str
                 ) -> tuple[np.ndarray, np.ndarray] | None:
    """(record_max, record_min) over CRW CoralTemp history for
    (d.month, d.day). Mirrors `_oisst_records` — same streaming
    fmax/fmin reduce and same DOY±1 cache bound — but pulls from
    CRW_RECORDS_START..target_year-1 (1985 onward) and reads the
    `analysed_sst` variable. CRW grids are ~7200×3600 float32 (~100 MB
    each) so streaming the reduce is the difference between a ~6 GB
    transient and ~200 MB."""
    key = (d.month, d.day)
    if key not in _CRW_RECORDS_CACHE:
        hist_years = range(gsp.CRW_RECORDS_START, target_year)
        hist = gsp.day_of_year_crw_files(d.month, d.day, hist_years,
                                         "sst", log)
        rmax: np.ndarray | None = None
        rmin: np.ndarray | None = None
        for y in sorted(hist):
            try:
                g, _, _ = gsp.read_crw_grid(hist[y], "analysed_sst")
            except Exception:  # noqa: BLE001
                continue
            if rmax is None:
                rmax = g.copy()
                rmin = g
            else:
                if g.shape != rmax.shape:
                    continue
                np.fmax(rmax, g, out=rmax)
                np.fmin(rmin, g, out=rmin)
            del g
        _CRW_RECORDS_CACHE[key] = (
            (rmax, rmin) if rmax is not None and rmin is not None else None
        )
    _trim_records_cache(_CRW_RECORDS_CACHE, d)
    gc.collect()
    return _CRW_RECORDS_CACHE.get(key)


# ----------------------------------------------------------------------
# Frame rendering
# ----------------------------------------------------------------------
def _render_frame(product: dict, data: np.ndarray, lat: np.ndarray,
                  lon: np.ndarray, region_cfg: dict, valid_date: dt.date,
                  countries, coast, out_path: Path,
                  extras: dict | None = None) -> bool:
    """Render one frame (one date × region × product) to `out_path`.

    Mirrors the static plot_actual / plot_anomaly visual style exactly
    (colormap, basemap, watermark, subtitle layout) but at lower DPI
    and skipping the labels variant for animation speed. Optional
    `extras` carries product-specific overlays — e.g. records_high /
    records_low boolean masks for the `anomaly_records` product.
    Returns False if the subset is empty (region outside the data's
    coverage)."""
    extent = region_cfg["extent"]
    figsize = region_cfg["figsize"]
    label = region_cfg["label"]

    sub, la, lo = gsp._subset_to_extent(data, lat, lon, extent)
    if sub.size == 0:
        return False

    cmap = gsp.CMAP_ACTUAL if product["cmap"] == "actual" else gsp.CMAP_ANOM
    norm = mcolors.Normalize(vmin=product["vmin"], vmax=product["vmax"])

    fig, ax = plt.subplots(figsize=figsize, facecolor=gsp.BG_COLOR)
    LON2, LAT2 = np.meshgrid(lo, la)
    pcm = ax.pcolormesh(
        LON2, LAT2, sub, cmap=cmap, norm=norm,
        shading="auto", rasterized=True, zorder=1,
    )
    if product.get("contour"):
        gsp.draw_integer_degree_contours(ax, LON2, LAT2, sub)

    # Records overlay — diagonal hatching in the same style as
    # gsp.plot_anomaly (forward-slash for highs, back-slash for lows).
    # Drawn before the basemap so coastlines sit on top of the hatching.
    if product.get("records_overlay") and extras:
        prev_hatch_lw = mpl.rcParams.get("hatch.linewidth", 1.0)
        prev_hatch_color = mpl.rcParams.get("hatch.color", "black")
        mpl.rcParams["hatch.linewidth"] = 0.55
        try:
            for rm, pattern, hatch_color in (
                (extras.get("records_high"), "///",  "#2a0412"),
                (extras.get("records_low"),  "\\\\", "#05122e"),
            ):
                if rm is None:
                    continue
                rm_sub, _, _ = gsp._subset_to_extent(rm, lat, lon, extent)
                if rm_sub.shape != sub.shape:
                    continue
                mask_float = np.where(rm_sub, 1.0, 0.0)
                if not (mask_float > 0.5).any():
                    continue
                mpl.rcParams["hatch.color"] = hatch_color
                ax.contourf(
                    LON2, LAT2, mask_float,
                    levels=[0.5, 1.5],
                    colors="none",
                    hatches=[pattern],
                    zorder=1.8,
                )
                ax.contour(
                    LON2, LAT2, mask_float,
                    levels=[0.5],
                    colors="#000000", linewidths=0.6, alpha=0.75,
                    zorder=1.9,
                )
        finally:
            mpl.rcParams["hatch.linewidth"] = prev_hatch_lw
            mpl.rcParams["hatch.color"] = prev_hatch_color

    gsp._draw_basemap(ax, extent, countries, coast)

    date_label = valid_date.strftime("%B %-d, %Y")
    title = f"{label} · {product['title_suffix']}"
    subtitle = f"Valid: {date_label}  ·  {product['subtitle_src']}"
    gsp._style_axes(ax, extent, title, subtitle)
    gsp._add_colorbar(
        fig, pcm, product["cbar_label"],
        ticks=np.array(product["cbar_ticks"]),
        extend=product["cbar_extend"],
    )
    gsp._draw_watermark(ax)
    fig.subplots_adjust(left=0.05, right=0.89, top=0.86, bottom=0.08)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=FRAME_DPI, facecolor=gsp.BG_COLOR)
    plt.close(fig)
    return True


# ----------------------------------------------------------------------
# Cache helpers
# ----------------------------------------------------------------------
def _product_cache_key(product: dict) -> str:
    """Segment used under FRAME_CACHE_DIR for this product's frames.

    `cache_version` lets us invalidate stale cached frames when a
    product's rendering changes (e.g. adding contours to `actual`)
    without touching other products. v1 is the implicit default for
    untagged products, so legacy dirs remain valid."""
    v = int(product.get("cache_version", 1))
    slug = product["slug"]
    return slug if v == 1 else f"{slug}_v{v}"


def _frame_path(region_key: str, product: dict, d: dt.date,
                version: str | None) -> Path:
    """Per-(region, product, date, version) PNG cache path.

    `version` is "final"/"prelim" for OISST products and None for CRW or
    legacy unversioned writes. Under render-once caching only one variant
    per date is ever written, but the version tag is still encoded in the
    filename so the cache remains diagnosable (you can tell at a glance
    whether a date came in as prelim or final)."""
    base = FRAME_CACHE_DIR / region_key / _product_cache_key(product)
    if version is None:
        return base / f"{d:%Y%m%d}.png"
    return base / f"{d:%Y%m%d}.{version}.png"


def _existing_cached(region_key: str, product: dict,
                     d: dt.date) -> Path | None:
    """Best-available cached PNG for (region, product, date), or None.

    Under render-once caching there is at most one variant per date, but
    we still check all three (.final / .prelim / legacy) to remain
    forward-compatible with caches written by older code paths. Used by
    `_list_window_frames` (encoder input) and `_needs_render` (cache-
    skip decision)."""
    base = FRAME_CACHE_DIR / region_key / _product_cache_key(product)
    if _is_oisst_product(product):
        for v in ("final", "prelim"):
            p = base / f"{d:%Y%m%d}.{v}.png"
            if p.exists():
                return p
    legacy = base / f"{d:%Y%m%d}.png"
    return legacy if legacy.exists() else None


def _needs_render(region_key: str, product: dict, d: dt.date) -> bool:
    """True if (region, product, date) has no cached PNG yet.

    Render-once: any cached variant (.final.png / .prelim.png / legacy
    .png) wins permanently — we never re-render a date that's already in
    the cache. This is what eliminates the prelim/final perceptual seam
    from the MP4 (after the ~60-90 day transition every frame in the
    window has been originally rendered from prelim → uniform processing).
    The same rule now applies to OISST and CRW."""
    return _existing_cached(region_key, product, d) is None


def _list_window_frames(region_key: str, product: dict,
                        window_dates: list[dt.date]) -> list[Path]:
    """Returns one PNG path per date in the window that has any cached
    variant, in chronological order. Prefers `.final.png` per
    `_existing_cached`. Drops dates with no cached frame at all."""
    out: list[Path] = []
    for d in window_dates:
        p = _existing_cached(region_key, product, d)
        if p is not None:
            out.append(p)
    return out


def _prune_old_frames(window_end: dt.date) -> None:
    """Delete cached frames older than (window_end - CACHE_KEEP_DAYS).

    Keeps the cache from growing without bound across many CI runs
    while still tolerating a one-week skipped run without invalidating
    the whole window."""
    cutoff = window_end - dt.timedelta(days=CACHE_KEEP_DAYS)
    if not FRAME_CACHE_DIR.exists():
        return
    removed = 0
    for png in FRAME_CACHE_DIR.rglob("*.png"):
        try:
            stem = png.stem  # YYYYMMDD
            d = dt.date(int(stem[:4]), int(stem[4:6]), int(stem[6:8]))
        except Exception:
            continue
        if d < cutoff:
            try:
                png.unlink()
                removed += 1
            except OSError:
                pass
    if removed:
        print(f"[sst-anim] pruned {removed} cached frames older than {cutoff}")


# ----------------------------------------------------------------------
# ffmpeg encoding
# ----------------------------------------------------------------------
def _encode_mp4(frames: list[Path], out_path: Path, fps: int = FPS) -> int:
    """Concat-decode a frame list into an h.264 MP4. Uses the concat
    demuxer so we don't need contiguous %04d-style filenames; that lets
    the cache mix frame dates as the sliding window advances.

    Returns the resulting file size in bytes (for the manifest)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Write an ffmpeg concat list to a temp file alongside the MP4.
    # Each line: "file '/abs/path/to/frame.png'" + "duration <secs>".
    # No trailing-repeat line: with `-vsync cfr -r fps` and an explicit
    # duration on every entry, ffmpeg honors per-frame timing exactly.
    # The trailing-repeat trick is only needed under `-vsync vfr` and,
    # combined with CFR resampling, used to push total stream duration
    # to exactly N/fps which CFR samples *inclusively at both endpoints*
    # — yielding N+1 output frames for N inputs. `-frames:v` below caps
    # to N to make this airtight regardless.
    concat_file = out_path.with_suffix(".concat.txt")
    per_frame_s = 1.0 / fps
    with concat_file.open("w") as f:
        for fp in frames:
            f.write(f"file '{fp.resolve()}'\n")
            f.write(f"duration {per_frame_s}\n")

    # `high` profile is required by -preset slow's 8x8 transform; it's
    # universally supported by modern browsers so there's no playback
    # cost. CRF 18 is visually ~lossless for scientific viz with text
    # and contour lines; -preset slow buys ~3x encode time for a
    # noticeably cleaner result at the same bitrate. -tune stillimage
    # biases x264 toward preserving sharp edges (text, tick marks,
    # contours) at the expense of inter-frame smoothing — appropriate
    # for this slideshow-style content where each frame is effectively
    # a still. yuv420p is kept for maximum browser compatibility.
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-vsync", "cfr", "-r", str(fps),
        "-frames:v", str(len(frames)),
        "-c:v", "libx264", "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-crf", "18", "-preset", "slow", "-tune", "stillimage",
        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2:0:0:black",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    concat_file.unlink(missing_ok=True)
    return out_path.stat().st_size


def _write_poster(last_frame: Path, out_path: Path) -> None:
    """Re-save the last frame as a small JPG for <video poster=…>."""
    img = Image.open(last_frame).convert("RGB")
    # Cap poster width to keep the JPG modest while staying sharp. 1920
    # (was 1200) so the still shown before play isn't visibly downscaled
    # on wide regions whose frames render ~2175 px wide at FRAME_DPI=150.
    # Narrower regions are left at native width (no upscaling). ~100-200
    # KB at quality=92 — fine for a poster fetched on page load.
    max_w = 1920
    if img.width > max_w:
        h = int(img.height * (max_w / img.width))
        img = img.resize((max_w, h), Image.LANCZOS)
    # quality=92 (not lower): posters carry sharp text, tick labels, and
    # contour lines that smear under aggressive JPEG compression even
    # when the source frame is high-DPI. Frame DPI / cache_version don't
    # govern poster sharpness — this knob does, independently.
    img.save(out_path, "JPEG", quality=92, optimize=True)


# ----------------------------------------------------------------------
# Top-level render orchestration
# ----------------------------------------------------------------------
def _build_window_dates(end_date: dt.date) -> list[dt.date]:
    return [end_date - dt.timedelta(days=WINDOW_DAYS - 1 - i)
            for i in range(WINDOW_DAYS)]


def _build_day_products(d: dt.date, target_year: int,
                        requested_slugs: frozenset[str], log: str
                        ) -> list[tuple[dict, np.ndarray, np.ndarray,
                                        np.ndarray, str | None, dict]]:
    """For one date, fetch the source data + climatology and return a
    list of (product_dict, derived_field, lat, lon, version, extras)
    tuples ready to feed into _render_frame across all regions.

    `version` is "final"/"prelim" for OISST products and None for CRW
    (single-publish source). All OISST products derived from the same
    day's SST share that day's version. `extras` is a per-product dict
    carrying overlay arrays (e.g. records masks for `anomaly_records`).

    OISST anomaly products are skipped if the OISST climatology can't
    be built (e.g. network failure on >half the historical years);
    `actual` still renders. Same for CRW. The `anomaly_records`
    product is also skipped silently if the per-DOY records envelope
    can't be assembled — its frame just gets the next-day shot at it.

    `target_year` scopes the records computation to RECORDS_START..
    target_year-1 (matching the static /sst/ page's behavior).
    `requested_slugs` lets the caller cheaply opt out of the records
    fetch when no anomaly_records frames are being rendered this run.
    """
    out: list[tuple[dict, np.ndarray, np.ndarray,
                    np.ndarray, str | None, dict]] = []

    # OISST family
    oisst = _load_oisst_sst(d, log)
    if oisst is not None:
        sst, oi_lat, oi_lon, oi_version = oisst
        out.append((PRODUCT_BY_SLUG["actual"], sst, oi_lat, oi_lon,
                    oi_version, {}))

        oi_climo = _oisst_climo(d, log)
        if oi_climo is not None and oi_climo.shape == sst.shape:
            anom = sst - oi_climo
            out.append((PRODUCT_BY_SLUG["anomaly"], anom, oi_lat, oi_lon,
                        oi_version, {}))

            if "anomaly_records" in requested_slugs:
                rec = _oisst_records(d, target_year, log)
                if rec is not None:
                    rmax, rmin = rec
                    if rmax.shape == sst.shape and rmin.shape == sst.shape:
                        eps = 0.001
                        rec_high = sst > (rmax - eps)
                        rec_low = sst < (rmin + eps)
                        nan_mask = np.isnan(sst)
                        rec_high = np.where(
                            nan_mask | np.isnan(rmax), False, rec_high)
                        rec_low = np.where(
                            nan_mask | np.isnan(rmin), False, rec_low)
                        out.append((
                            PRODUCT_BY_SLUG["anomaly_records"], anom,
                            oi_lat, oi_lon, oi_version,
                            {"records_high": rec_high,
                             "records_low": rec_low},
                        ))

            gm = gsp.compute_global_mean(anom, oi_lat)
            if np.isfinite(gm):
                anom_gmr = anom - gm
                out.append((PRODUCT_BY_SLUG["anomaly_gmr"],
                            anom_gmr, oi_lat, oi_lon, oi_version, {}))

    # CRW family — single-version publish, no per-day version tag.
    crw = _load_crw_sst(d, log)
    if crw is not None:
        crw_sst, crw_lat, crw_lon = crw
        out.append((PRODUCT_BY_SLUG["crw_actual"], crw_sst,
                    crw_lat, crw_lon, None, {}))
        crw_clim = _crw_climo(d, log)
        if crw_clim is not None and crw_clim.shape == crw_sst.shape:
            anom_crw = crw_sst - crw_clim
            out.append((PRODUCT_BY_SLUG["crw_anomaly"],
                        anom_crw, crw_lat, crw_lon, None, {}))

            if "crw_anomaly_records" in requested_slugs:
                rec = _crw_records(d, target_year, log)
                if rec is not None:
                    rmax, rmin = rec
                    if (rmax.shape == crw_sst.shape
                            and rmin.shape == crw_sst.shape):
                        eps = 0.001
                        rec_high = crw_sst > (rmax - eps)
                        rec_low = crw_sst < (rmin + eps)
                        nan_mask = np.isnan(crw_sst)
                        rec_high = np.where(
                            nan_mask | np.isnan(rmax), False, rec_high)
                        rec_low = np.where(
                            nan_mask | np.isnan(rmin), False, rec_low)
                        out.append((
                            PRODUCT_BY_SLUG["crw_anomaly_records"],
                            anom_crw, crw_lat, crw_lon, None,
                            {"records_high": rec_high,
                             "records_low": rec_low},
                        ))

            gm_crw = gsp.compute_global_mean(anom_crw, crw_lat)
            if np.isfinite(gm_crw):
                anom_crw_gmr = anom_crw - gm_crw
                out.append((PRODUCT_BY_SLUG["crw_anomaly_gmr"],
                            anom_crw_gmr, crw_lat, crw_lon, None, {}))

    return out


def _render_all_frames(dates: list[dt.date], regions: list[str],
                       products: list[dict], countries, coast,
                       log: str = "[sst-anim]") -> dict:
    """For every (date, region, product), render to the frame cache if
    needed. Returns a small stats dict.

    "Needed" is decided by `_needs_render`: render-once caching means
    any cached PNG variant for a date is kept forever, and only dates
    with no cached frame at all get rendered. New dates pick up
    whichever upstream version `gsp.fetch_day_versioned` returned first
    (prelim is preferred — see that function's docstring for why).

    Iterates date-major because each day's data + climo is loaded
    once and fanned out across all regions/products before being
    freed — keeps peak RAM ~bounded.
    """
    stats = {"rendered": 0, "cached": 0, "skipped_unavailable": 0}
    requested_slugs = frozenset(p["slug"] for p in products)
    target_year = dates[-1].year
    t0 = time.time()
    for di, d in enumerate(dates, start=1):
        # Skip the day entirely if no (region × product) needs work —
        # avoids needless data + climo fetches on warm runs.
        any_missing = False
        for region in regions:
            for product in products:
                if _needs_render(region, product, d):
                    any_missing = True
                    break
            if any_missing:
                break
        if not any_missing:
            stats["cached"] += len(regions) * len(products)
            continue

        day_products = _build_day_products(d, target_year,
                                           requested_slugs, log)
        # Restrict to the user-requested product subset.
        day_products = [
            (p, data, la, lo, ver, extras)
            for (p, data, la, lo, ver, extras) in day_products
            if p["slug"] in requested_slugs
        ]
        if not day_products:
            stats["skipped_unavailable"] += len(regions) * len(products)
            continue

        for region in regions:
            rcfg = gsp.REGIONS[region]
            for product, data, lat, lon, version, extras in day_products:
                target = _frame_path(region, product, d, version)
                if target.exists():
                    stats["cached"] += 1
                    continue
                ok = _render_frame(product, data, lat, lon, rcfg, d,
                                   countries, coast, target,
                                   extras=extras)
                if ok:
                    stats["rendered"] += 1
                else:
                    stats["skipped_unavailable"] += 1

        elapsed = time.time() - t0
        rate = (stats["rendered"] + stats["cached"]) / max(1, elapsed)
        print(f"{log} day {di}/{len(dates)} ({d}) — "
              f"rendered={stats['rendered']} cached={stats['cached']} "
              f"unavail={stats['skipped_unavailable']} · "
              f"{rate:.1f} frames/s", flush=True)

    return stats


def _encode_all(end_date: dt.date, regions: list[str],
                products: list[dict], log: str = "[sst-anim]") -> dict:
    """Encode one MP4 + poster per (region × product). Returns a
    `clips` dict ready to drop into manifest.json."""
    clips: dict[str, dict] = {}
    SST_BUILD_DIR.mkdir(parents=True, exist_ok=True)
    window_dates = _build_window_dates(end_date)
    for region in regions:
        for product in products:
            slug = product["slug"]
            # One PNG per date in the window. Under render-once caching
            # there's at most one variant per date, but the lookup still
            # tolerates final/prelim/legacy filenames for forward
            # compatibility. Dates with no cached frame are silently
            # dropped (the encoder accepts a short clip).
            window_frames = _list_window_frames(region, product, window_dates)
            if not window_frames:
                print(f"{log}   ! {region}/{slug}: no cached frames, skip",
                      flush=True)
                continue
            mp4_name = f"{region}_{slug}.mp4"
            poster_name = f"{region}_{slug}.jpg"
            mp4_path = SST_BUILD_DIR / mp4_name
            poster_path = SST_BUILD_DIR / poster_name
            try:
                size = _encode_mp4(window_frames, mp4_path)
            except subprocess.CalledProcessError as e:
                print(f"{log}   ! {region}/{slug} ffmpeg failed: {e}",
                      file=sys.stderr)
                continue
            try:
                _write_poster(window_frames[-1], poster_path)
            except Exception as e:  # noqa: BLE001
                print(f"{log}   ! {region}/{slug} poster failed: {e}",
                      file=sys.stderr)

            first_d = _date_from_frame(window_frames[0])
            last_d = _date_from_frame(window_frames[-1])
            clips[f"{region}_{slug}"] = {
                "src": mp4_name,
                "poster": poster_name,
                "region": region,
                "product": slug,
                "first_frame": first_d.isoformat() if first_d else None,
                "last_frame": last_d.isoformat() if last_d else None,
                "frames": len(window_frames),
                "duration_s": round(len(window_frames) / FPS, 3),
                "bytes": size,
            }
            print(f"{log}   ✓ {region}/{slug}  "
                  f"({len(window_frames)} frames, {size / 1024:.0f} KB)",
                  flush=True)
    return clips


def _date_from_frame(p: Path) -> dt.date | None:
    try:
        return dt.date(int(p.stem[:4]), int(p.stem[4:6]), int(p.stem[6:8]))
    except Exception:
        return None


# ----------------------------------------------------------------------
# Manifest + README writers
# ----------------------------------------------------------------------
def _fetch_live_manifest(log: str = "[sst-anim]", attempts: int = 3) -> dict | None:
    """Read the live manifest from the public CDN so a partial (single-shard)
    run can MERGE rather than clobber the other family's clips. Returns the
    parsed dict, or None if it is genuinely absent / unreachable."""
    url = f"{LIVE_MANIFEST_URL}?t={int(time.time())}"
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return None  # absent (first run / R2 custom-domain 403-on-missing)
            last = e
        except Exception as e:  # noqa: BLE001 - network / parse
            last = e
        if i < attempts - 1:
            time.sleep(2 * (i + 1))
    print(f"{log} WARN: could not read live manifest ({last}); this run's "
          f"manifest will NOT preserve the other shard's clips", file=sys.stderr)
    return None


def _write_manifest(clips: dict, regions: list[str], products: list[dict],
                    end_date: dt.date, *, merge: bool = True,
                    log: str = "[sst-anim]") -> dict:
    """Write the per-family manifest the widget reads, MERGING with the live
    manifest so a single-shard run (OISST in Job 1, CRW in Job 2) refreshes only
    its OWN products and preserves the other shard's clips/products/regions.
    Without the merge a partial run would publish a manifest missing the other
    family until the next full run. Returns the written manifest dict."""
    SST_BUILD_DIR.mkdir(parents=True, exist_ok=True)
    encoded = {p["slug"] for p in products}
    live = (_fetch_live_manifest(log) if merge else None) or {}

    # clips: keep the live clips for products NOT (re)encoded this run; the
    # encoded products' clips are fully replaced by this run's fresh set (so a
    # dropped region's stale clip is removed, not stranded).
    merged_clips = {
        k: v for k, v in (live.get("clips") or {}).items()
        if isinstance(v, dict) and v.get("product") not in encoded
    }
    merged_clips.update(clips)

    # products: live (others) + this run's fresh defs, ordered by CORE_PRODUCTS.
    prod_by_slug = {p["slug"]: p for p in (live.get("products") or [])
                    if isinstance(p, dict) and p.get("slug")}
    for p in products:
        prod_by_slug[p["slug"]] = {"slug": p["slug"], "label": p["label"],
                                   "description": p["description"]}
    core_order = [cp["slug"] for cp in CORE_PRODUCTS]
    ordered_products = [prod_by_slug[s] for s in core_order if s in prod_by_slug]
    ordered_products += [prod_by_slug[s] for s in prod_by_slug if s not in core_order]

    # regions: live + this run's, ordered by gsp.REGIONS.
    reg_by_slug = {r["slug"]: r for r in (live.get("regions") or [])
                   if isinstance(r, dict) and r.get("slug")}
    for r in regions:
        reg_by_slug[r] = {"slug": r, "label": gsp.REGIONS[r]["label"],
                          "extent": list(gsp.REGIONS[r]["extent"])}
    ordered_regions = [reg_by_slug[s] for s in gsp.REGIONS if s in reg_by_slug]
    ordered_regions += [reg_by_slug[s] for s in reg_by_slug if s not in gsp.REGIONS]

    manifest = {
        "family": "sst",
        "generated_at": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "frame_rate_fps": FPS,
        "window": {"unit": "days", "length": WINDOW_DAYS},
        "regions": ordered_regions,
        "products": ordered_products,
        "clips": merged_clips,
    }
    out = SST_BUILD_DIR / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{log} wrote {out}  ({len(merged_clips)} clips total, "
          f"{len(clips)} fresh from this shard)")
    return manifest


def _family_of(slug: str) -> str:
    return "OISST" if slug in OISST_PRODUCT_SLUGS else "CRW"


def _check_drift(manifest: dict, end_date: dt.date, encoded_products: list[dict],
                 log: str = "[sst-anim]") -> None:
    """Guard: surface (as a GitHub Actions ::warning::) if a product family THIS
    shard just published fell behind the static maps' latest available day — so
    the animator-vs-static drift is caught automatically, not by eye. Only the
    shard's OWN families are checked (the OISST shard does not warn about CRW,
    which is Job 2's responsibility and not yet refreshed when Job 1 runs)."""
    families = {_family_of(p["slug"]) for p in encoded_products}
    latest_by_family: dict[str, dt.date] = {}
    for c in (manifest.get("clips") or {}).values():
        if not isinstance(c, dict):
            continue
        fam = _family_of(c.get("product", ""))
        if fam not in families:
            continue
        lf = c.get("last_frame")
        if not lf:
            continue
        try:
            d = dt.date.fromisoformat(lf)
        except ValueError:
            continue
        if fam not in latest_by_family or d > latest_by_family[fam]:
            latest_by_family[fam] = d
    for fam in families:
        latest = latest_by_family.get(fam)
        if latest is None:
            print(f"::warning title=SST animation drift::{fam} shard produced "
                  f"NO clips this run (static latest {end_date}).")
            continue
        lag = (end_date - latest).days
        if lag >= 1:
            print(f"::warning title=SST animation drift::{fam} animation is "
                  f"{lag} day(s) behind the static maps (animation latest "
                  f"{latest}, static latest {end_date}); this shard did not keep "
                  f"pace this cycle.")
            print(f"{log} DRIFT: {fam} animation {latest} < static {end_date} "
                  f"({lag}d behind)", file=sys.stderr)
        else:
            print(f"{log} currency OK: {fam} animation at {latest} "
                  f"(static latest {end_date})")


def _write_branch_readme() -> Path:
    """Write the README that lives at the orphan branch root.

    The workflow only writes this if it isn't already present, so any
    manual edits on the branch survive future runs."""
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    out = BUILD_DIR / "README.md"
    out.write_text(
        "# Triple-A-Tropics — `mp4-artifacts` branch\n"
        "\n"
        "**This branch is regenerated by CI and force-pushed on every "
        "`update-{sst,subsurface,armor3d}.yml` workflow run.**\n"
        "\n"
        "Each subdirectory holds the latest MP4 animations + poster JPGs "
        "for one product family, plus a `manifest.json` the on-page "
        "`<video>` widget reads to populate its dropdowns.\n"
        "\n"
        "Layout:\n"
        "\n"
        "```\n"
        "sst/         — 90-day daily-cadence SST animations\n"
        "subsurface/  — (planned)\n"
        "armor3d/     — (planned)\n"
        "```\n"
        "\n"
        "## Don't file PRs against this branch\n"
        "\n"
        "History is intentionally ephemeral — every workflow run does "
        "`git push --force` against just its own family directory. "
        "Source code, including the generators that produce these MP4s, "
        "lives on `main`.\n"
        "\n"
        "Anomalies in the SST family use the same 1991–2020 daily "
        "climatology the static `/sst/` page recomputes against, so "
        "values are numerically consistent across the static maps and "
        "the MP4 animations for every date.\n"
    )
    print(f"[sst-anim] wrote {out}")
    return out


# ----------------------------------------------------------------------
# Entrypoint
# ----------------------------------------------------------------------
def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Render 90-day SST MP4 animations.",
    )
    p.add_argument("--end-date",
                   help="Window end date YYYY-MM-DD (default: latest "
                        "available OISST day).")
    p.add_argument("--regions", nargs="*",
                   help="Subset of region keys to render (default: all).")
    p.add_argument("--products", nargs="*",
                   help="Subset of product slugs to render (default: "
                        "all CORE_PRODUCTS).")
    p.add_argument("--clean-build", action="store_true",
                   help="Wipe _mp4_build/ before staging (does NOT touch "
                        "the frame cache).")
    p.add_argument("--render-only", action="store_true",
                   help="Render frames into _frame_cache/ and stop. "
                        "Skips ffmpeg encode, manifest, and README. Used "
                        "by the OISST shard of the split workflow so a "
                        "later shard (with all families warm) can do one "
                        "consolidated encode + write the unified manifest.")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    log = "[sst-anim]"

    # Determine window
    if args.end_date:
        end_date = dt.date.fromisoformat(args.end_date)
    else:
        end_date, _ = gsp.latest_available_day(log)
    dates = _build_window_dates(end_date)
    print(f"{log} window: {dates[0]} → {end_date}  ({WINDOW_DAYS} days)")

    regions = args.regions or list(gsp.REGIONS.keys())
    products = (
        [p for p in CORE_PRODUCTS if p["slug"] in args.products]
        if args.products else list(CORE_PRODUCTS)
    )
    print(f"{log} regions: {len(regions)}  products: {len(products)}  "
          f"frames per clip: {WINDOW_DAYS}  "
          f"total clips: {len(regions) * len(products)}")

    if args.clean_build and BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)

    # Basemap (50 m preferred, 110 m fallback — same as static SST page)
    countries = (
        gsp._load_geojson("ne_50m_admin_0_countries.geojson")
        or gsp._load_geojson("ne_110m_admin_0_countries.geojson")
    )
    coast = (
        gsp._load_geojson("ne_50m_coastline.geojson")
        or gsp._load_geojson("ne_110m_coastline.geojson")
    )
    if countries is None and coast is None:
        print(f"{log} WARN: no basemap GeoJSON — frames will have no coastlines")

    # Render
    t0 = time.time()
    stats = _render_all_frames(dates, regions, products, countries, coast, log)
    print(f"{log} render phase: {time.time() - t0:.1f}s — {stats}")

    if args.render_only:
        # Cache-priming shard: prune stale frames so the cache stays
        # bounded, then exit. The next shard will do the full encode +
        # manifest with everything warm.
        _prune_old_frames(end_date)
        print(f"{log} render-only done in {time.time() - t0:.1f}s "
              f"(frames in {FRAME_CACHE_DIR})")
        return 0

    # Encode
    t1 = time.time()
    clips = _encode_all(end_date, regions, products, log)
    print(f"{log} encode phase: {time.time() - t1:.1f}s — {len(clips)} clips")

    # Manifest (merged with the live one so this shard preserves the other
    # shard's clips) + README
    manifest = _write_manifest(clips, regions, products, end_date, log=log)
    _write_branch_readme()

    # Drift guard: surface (::warning::) if THIS shard's family fell behind the
    # static maps' latest available day.
    _check_drift(manifest, end_date, products, log)

    # Prune
    _prune_old_frames(end_date)

    print(f"{log} done in {time.time() - t0:.1f}s "
          f"(staged at {SST_BUILD_DIR})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
