#!/usr/bin/env python3
"""
Triple-A-Tropics · Global CRW anomaly GIF generator (one-off)
=============================================================

Fetches NOAA Coral Reef Watch's daily precomputed 5 km SST anomaly
(`ct5km_ssta_v3.1_YYYYMMDD.nc`) for a custom date range, renders one
Pacific-centered global frame per day, and assembles everything into a
single GIF intended for blog / social-media posts.

This script is **not** wired into the site or any scheduled update.
It runs on demand via the `make-global-crw-anom-gif.yml` GitHub Actions
workflow, and the resulting GIF is uploaded as a workflow artifact
(not committed to the repo).

Data source
-----------
NOAA Coral Reef Watch 5 km SST anomaly:
    https://www.star.nesdis.noaa.gov/pub/sod/mecb/crw/data/5km/v3.1_op
    /nc/v1.0/daily/ssta/{YYYY}/ct5km_ssta_v3.1_{YYYYMMDD}.nc

This is the official CRW precomputed SSTA product (baseline 1985–2012,
CoralTemp's own climatology). It differs from the /sst/ page's 1991–
2020 OISST-based anomalies — we use CRW's own product here because a
1991–2020 recompute would need 30 × N daily files per frame and would
blow the Actions timeout. The CRW product is more than sufficient for
a social-media GIF.

Usage
-----
    python generate_global_crw_anom_gif.py \
        --start 2026-01-21 --end 2026-04-21 \
        --out global_crw_anom.gif
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import io
import sys
from pathlib import Path

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

# Share the CRW fetch helpers + anomaly colormap with generate_sst_plots
# and share the styling/basemap helpers with generate_subsurface_plots.
import generate_sst_plots as gsp
import generate_subsurface_plots as gss


# --- Geographic window --------------------------------------------------
# Pacific-centered full-globe extent matching the site's `global` SST
# region in generate_sst_plots.py. `_subset_to_extent` duplicates the
# 0–30°E sliver onto the right edge so the wrap is seamless. Lat limited
# to ±75° because CRW SSTA has no polar ocean signal worth rendering.
GLOBAL_EXTENT: tuple[float, float, float, float] = (30.0, 390.0, -75.0, 75.0)

# Figure size tuned so 91 daily frames comes in well under Twitter's
# 15 MB GIF limit. Lower dpi + modest figure size + palette quantization
# does most of the compression work.
FIGSIZE = (13.5, 6.4)
DPI = 85

# Anomaly color ramp limits. CRW's SSTA product is in °C. ±5 °C matches
# the site's /sst/ anomaly tab so the visual vocabulary is the same.
ANOM_VLIM = 5.0

# Spatial subsample stride. CRW native grid is 7200×3600 (5 km). At the
# global figure size above, our output is ~1150×545 px, so we don't need
# anywhere close to 5 km resolution — subsample by 4 (≈20 km effective)
# for a 4× speedup with no visible loss.
SUBSAMPLE = 4

# Parallelism for CRW SSTA downloads. CRW's CDN handles ~8–12 concurrent
# fine; the fetch step dominates wall time for 91 frames.
FETCH_WORKERS = 10


def _fetch_one_day(d: dt.date, log: str
                   ) -> tuple[dt.date, np.ndarray | None,
                              np.ndarray | None, np.ndarray | None]:
    """Download the CRW SSTA NetCDF for one day and return the (anom,
    lat, lon) grid — lat ascending, lon in 0–360 convention. Returns
    (d, None, None, None) when the file isn't available yet (preliminary
    latency gap, network blip, etc.) so we can skip that frame."""
    p = gsp.fetch_crw_day(d, "ssta", log, verbose=False)
    if p is None:
        return d, None, None, None
    try:
        data, lat, lon = gsp.read_crw_grid(
            p, "sea_surface_temperature_anomaly"
        )
    except Exception as e:  # noqa: BLE001
        print(f"{log}   ! {d} read error: {type(e).__name__}: {e}",
              file=sys.stderr)
        return d, None, None, None
    if SUBSAMPLE > 1:
        data = data[::SUBSAMPLE, ::SUBSAMPLE]
        lat = lat[::SUBSAMPLE]
        lon = lon[::SUBSAMPLE]
    return d, data, lat, lon


def _render_frame(
    anom: np.ndarray, lat: np.ndarray, lon: np.ndarray,
    valid_date: dt.date, countries, coast,
) -> Image.Image:
    """Render a single day's CRW SSTA as an in-memory PIL image."""
    sub, la, lo = gsp._subset_to_extent(anom, lat, lon, GLOBAL_EXTENT)
    if sub.size == 0:
        # Empty subset is exceptional but survivable — return a blank
        # frame so the GIF doesn't desync with the date axis.
        fig, ax = plt.subplots(figsize=FIGSIZE, facecolor=gsp.BG_COLOR)
        ax.set_facecolor(gsp.LAND_COLOR)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=DPI, facecolor=gsp.BG_COLOR)
        plt.close(fig)
        buf.seek(0)
        return Image.open(buf).convert("RGB")

    fig, ax = plt.subplots(figsize=FIGSIZE, facecolor=gsp.BG_COLOR)
    ax.set_facecolor(gsp.LAND_COLOR)

    LON2, LAT2 = np.meshgrid(lo, la)
    norm = mcolors.Normalize(vmin=-ANOM_VLIM, vmax=ANOM_VLIM)
    pcm = ax.pcolormesh(
        LON2, LAT2, sub, cmap=gsp.CMAP_ANOM, norm=norm,
        shading="auto", rasterized=True, zorder=1,
    )
    gsp._draw_basemap(ax, GLOBAL_EXTENT, countries, coast)

    date_label = valid_date.strftime("%B %-d, %Y")
    gsp._style_axes(
        ax, GLOBAL_EXTENT,
        "Global · Sea-Surface Temperature Anomaly",
        f"Valid: {date_label}  ·  NOAA Coral Reef Watch 5 km  ·  baseline 1985–2012",
    )
    # Lock the axes face again after _style_axes (it sets PANEL_COLOR)
    # so any NaN gutters render as the land color, not the panel color.
    ax.set_facecolor(gsp.LAND_COLOR)

    cax = fig.add_axes([0.91, 0.18, 0.018, 0.64])
    cb = fig.colorbar(pcm, cax=cax, extend="both",
                      ticks=np.arange(-5, 6, 1))
    cb.set_label("SST anomaly (°C)", color=gsp.TEXT_COLOR, fontsize=10)
    cb.ax.yaxis.set_tick_params(
        color=gsp.MUTED_COLOR, labelcolor=gsp.MUTED_COLOR, labelsize=9,
    )
    cb.outline.set_edgecolor(gsp.MUTED_COLOR)
    cb.outline.set_linewidth(0.4)

    gsp._draw_watermark(ax)
    fig.subplots_adjust(left=0.05, right=0.89, top=0.86, bottom=0.08)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, facecolor=gsp.BG_COLOR)
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def _daterange(start: dt.date, end: dt.date, step: int) -> list[dt.date]:
    out = []
    d = start
    while d <= end:
        out.append(d)
        d += dt.timedelta(days=max(1, step))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Render a Pacific-centered global CRW SSTA GIF."
    )
    p.add_argument("--start", default="2026-01-21",
                   help="Start date (YYYY-MM-DD).")
    p.add_argument("--end",   default="2026-04-21",
                   help="End date (YYYY-MM-DD), inclusive.")
    p.add_argument("--out",   default="global_crw_anom.gif",
                   help="Output GIF path.")
    p.add_argument("--fps", type=int, default=8,
                   help="Playback speed (frames per second).")
    p.add_argument("--step", type=int, default=1,
                   help="Day step (1 = daily, 2 = every other day, ...).")
    p.add_argument("--colors", type=int, default=128,
                   help="GIF palette colors per frame (64–256). "
                        "Lower = smaller file, more banding.")
    args = p.parse_args(argv)

    start = dt.datetime.fromisoformat(args.start).date()
    end   = dt.datetime.fromisoformat(args.end).date()
    if end < start:
        print("[crw-anom-gif] end date is before start", file=sys.stderr)
        return 1

    log = "[crw-anom-gif]"
    dates = _daterange(start, end, args.step)
    print(f"{log} rendering {len(dates)} frames for {start} → {end} "
          f"(step={args.step}, fps={args.fps}, colors={args.colors})")

    # Basemap — prefer 50 m resolution, fall back to 110 m if that's all
    # that was downloaded.
    countries = (
        gss._load_geojson("ne_50m_admin_0_countries.geojson")
        or gss._load_geojson("ne_110m_admin_0_countries.geojson")
    )
    coast = (
        gss._load_geojson("ne_50m_coastline.geojson")
        or gss._load_geojson("ne_110m_coastline.geojson")
    )
    if countries is None and coast is None:
        print(f"{log} WARN: no basemap GeoJSON found — plots will have no coastlines")

    # Parallel fetch first so render loop isn't blocked on network.
    print(f"{log} fetching {len(dates)} CRW SSTA files ({FETCH_WORKERS} workers)…")
    results: dict[dt.date, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    missing: list[dt.date] = []
    with cf.ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        futures = [pool.submit(_fetch_one_day, d, log) for d in dates]
        for fut in cf.as_completed(futures):
            d, data, lat, lon = fut.result()
            if data is None:
                missing.append(d)
                print(f"{log}   ✗ {d} (unavailable)")
            else:
                results[d] = (data, lat, lon)
    print(f"{log} fetched {len(results)}/{len(dates)} frames "
          f"(missing: {len(missing)})")
    if missing:
        print(f"{log} missing dates: "
              f"{', '.join(m.isoformat() for m in sorted(missing)[:10])}"
              + (" …" if len(missing) > 10 else ""))

    if not results:
        print(f"{log} no frames downloaded — exiting")
        return 1

    # Render in chronological order so the GIF plays forward.
    frames: list[Image.Image] = []
    ordered = [d for d in dates if d in results]
    for n, d in enumerate(ordered):
        data, lat, lon = results[d]
        print(f"{log}   rendering {d}  ({n + 1}/{len(ordered)})")
        frames.append(_render_frame(data, lat, lon, d, countries, coast))

    if not frames:
        print(f"{log} no frames rendered — exiting")
        return 1

    # Palette-optimize each frame. 128 colors is the sweet spot between
    # size and banding for diverging anomaly maps.
    print(f"{log} palette-optimizing {len(frames)} frames "
          f"(colors={args.colors})…")
    frames_p = [
        f.convert("P", palette=Image.ADAPTIVE, colors=max(32, min(256, args.colors)))
        for f in frames
    ]

    # Playback: user-requested fps, then hold the last frame for ~1.5 s
    # so viewers can read the final date before the GIF loops.
    duration_ms = max(50, int(1000 / max(1, args.fps)))
    hold_copies = int(round(1500 / duration_ms))
    final_frames = frames_p + [frames_p[-1]] * hold_copies

    out_path = Path(args.out)
    frames_p[0].save(
        out_path,
        save_all=True,
        append_images=final_frames[1:],
        duration=duration_ms,
        loop=0,
        optimize=True,
    )
    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"{log} wrote {out_path}  ({len(frames)} frames, {size_mb:.1f} MB)")
    if size_mb > 15.0:
        print(f"{log} WARNING: GIF is {size_mb:.1f} MB which exceeds "
              f"Twitter's 15 MB limit — rerun with --step 2 or "
              f"--colors 96 to shrink.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
