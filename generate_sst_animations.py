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
    global_mean(anomaly)` for OISST; `crw_anomaly = sst - climo` for
    CRW.
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
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

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

OISST_PRODUCT_SLUGS = frozenset({"actual", "anomaly", "anomaly_gmr"})


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


# ----------------------------------------------------------------------
# Frame rendering
# ----------------------------------------------------------------------
def _render_frame(product: dict, data: np.ndarray, lat: np.ndarray,
                  lon: np.ndarray, region_cfg: dict, valid_date: dt.date,
                  countries, coast, out_path: Path) -> bool:
    """Render one frame (one date × region × product) to `out_path`.

    Mirrors the static plot_actual / plot_anomaly visual style exactly
    (colormap, basemap, watermark, subtitle layout) but at lower DPI
    and skipping the labels variant + record overlays for animation
    speed. Returns False if the subset is empty (region outside the
    data's coverage)."""
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
    # Cap poster width so the JPG stays small (~50 KB target).
    max_w = 1200
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


def _build_day_products(d: dt.date, log: str
                        ) -> list[tuple[dict, np.ndarray, np.ndarray,
                                        np.ndarray, str | None]]:
    """For one date, fetch the source data + climatology and return a
    list of (product_dict, derived_field, lat, lon, version) tuples ready
    to feed into _render_frame across all regions.

    `version` is "final"/"prelim" for OISST products and None for CRW
    (single-publish source). All three OISST products derived from the
    same day's SST share that day's version.

    OISST anomaly products are skipped if the OISST climatology can't
    be built (e.g. network failure on >half the historical years);
    `actual` still renders. Same for CRW.
    """
    out: list[tuple[dict, np.ndarray, np.ndarray,
                    np.ndarray, str | None]] = []

    # OISST family
    oisst = _load_oisst_sst(d, log)
    if oisst is not None:
        sst, oi_lat, oi_lon, oi_version = oisst
        out.append((PRODUCT_BY_SLUG["actual"], sst, oi_lat, oi_lon, oi_version))

        oi_climo = _oisst_climo(d, log)
        if oi_climo is not None and oi_climo.shape == sst.shape:
            anom = sst - oi_climo
            out.append((PRODUCT_BY_SLUG["anomaly"], anom, oi_lat, oi_lon,
                        oi_version))

            gm = gsp.compute_global_mean(anom, oi_lat)
            if np.isfinite(gm):
                anom_gmr = anom - gm
                out.append((PRODUCT_BY_SLUG["anomaly_gmr"],
                            anom_gmr, oi_lat, oi_lon, oi_version))

    # CRW family — single-version publish, no per-day version tag.
    crw = _load_crw_sst(d, log)
    if crw is not None:
        crw_sst, crw_lat, crw_lon = crw
        crw_clim = _crw_climo(d, log)
        if crw_clim is not None and crw_clim.shape == crw_sst.shape:
            anom_crw = crw_sst - crw_clim
            out.append((PRODUCT_BY_SLUG["crw_anomaly"],
                        anom_crw, crw_lat, crw_lon, None))

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
    requested_slugs = {p["slug"] for p in products}
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

        day_products = _build_day_products(d, log)
        # Restrict to the user-requested product subset.
        day_products = [(p, data, la, lo, ver) for (p, data, la, lo, ver)
                        in day_products if p["slug"] in requested_slugs]
        if not day_products:
            stats["skipped_unavailable"] += len(regions) * len(products)
            continue

        for region in regions:
            rcfg = gsp.REGIONS[region]
            for product, data, lat, lon, version in day_products:
                target = _frame_path(region, product, d, version)
                if target.exists():
                    stats["cached"] += 1
                    continue
                ok = _render_frame(product, data, lat, lon, rcfg, d,
                                   countries, coast, target)
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
def _write_manifest(clips: dict, regions: list[str], products: list[dict],
                    end_date: dt.date) -> Path:
    """Write the per-family manifest the widget reads."""
    SST_BUILD_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "family": "sst",
        "generated_at": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "frame_rate_fps": FPS,
        "window": {"unit": "days", "length": WINDOW_DAYS},
        "regions": [
            {
                "slug": r,
                "label": gsp.REGIONS[r]["label"],
                "extent": list(gsp.REGIONS[r]["extent"]),
            }
            for r in regions
        ],
        "products": [
            {
                "slug": p["slug"],
                "label": p["label"],
                "description": p["description"],
            }
            for p in products
        ],
        "clips": clips,
    }
    out = SST_BUILD_DIR / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"[sst-anim] wrote {out}  ({len(clips)} clips)")
    return out


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

    # Encode
    t1 = time.time()
    clips = _encode_all(end_date, regions, products, log)
    print(f"{log} encode phase: {time.time() - t1:.1f}s — {len(clips)} clips")

    # Manifest + README
    _write_manifest(clips, regions, products, end_date)
    _write_branch_readme()

    # Prune
    _prune_old_frames(end_date)

    print(f"{log} done in {time.time() - t0:.1f}s "
          f"(staged at {SST_BUILD_DIR})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
