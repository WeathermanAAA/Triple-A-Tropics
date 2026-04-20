#!/usr/bin/env python3
"""
Triple-A-Tropics · Pacific TCHP GIF generator (AOML, one-off)
=============================================================

Fetches daily TCHP straight from NOAA AOML's precomputed TCHP/D26
OPeNDAP feed for a custom date range, crops to a Pacific-centered
window, renders one frame per day, and assembles everything into a
single GIF intended for blog / social-media posts.

This script is **not** wired into the site or any scheduled update.
It runs on demand via the `make-pacific-tchp-gif.yml` GitHub Actions
workflow, and the resulting GIF is uploaded as a workflow artifact
(not committed to the repo).

Data source
-----------
NOAA AOML PhOD TCHP/D26 Fields, OPeNDAP:
    https://cwcgom.aoml.noaa.gov/thredds/dodsC/TCHP/TCHP.nc

Same source used by the daily subsurface maps on the /sst/ page.
Coverage: 2022-01-01 → present, ~1-day latency, 0.25° global.

Usage
-----
    python generate_pacific_tchp_gif.py \\
        --start 2026-01-20 --end 2026-04-19 \\
        --out pacific_tchp.gif
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import sys
from pathlib import Path

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
from PIL import Image

import generate_subsurface_plots as gss


# AOML OPeNDAP endpoint + variable name. Same constants the daily
# subsurface generator uses, just re-referenced here so this script
# stays runnable on its own.
OPENDAP_URL = gss.OPENDAP_URL
TCHP_VAR = gss.TCHP_VAR


# Pacific warm-pool + ENSO thermocline tilt view. 100°E to 80°W, with a
# ±30° lat band so we get both hemispheres' subtropical gyres without
# pulling in subpolar water we don't care about for TCHP.
LAT_MIN, LAT_MAX = -30.0, 30.0
LON_MIN_0360, LON_MAX_0360 = 100.0, 280.0  # Pacific span in 0-360 convention

# Frame size + TCHP colorbar range. Keep the figure wide-ish so the
# basin-wide warm pool evolution reads clearly, but not so huge that
# the GIF overflows Twitter's 15 MB limit at ~90 frames.
FIGSIZE = (13.5, 5.8)
TCHP_VMAX = 200.0


def _fetch_pacific_timeseries(
    start: dt.datetime,
    end: dt.datetime,
    log: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Open AOML OPeNDAP and pull the Pacific TCHP time-series slab.

    Returns (tchp[time, lat, lon], times, lat, lon) with lon in 0-360
    convention and lat ascending — matches the sister scripts, so the
    existing basemap code works unchanged.

    AOML lon is -180..+180, so the Pacific (crossing the dateline)
    comes back in two subsets. We pull both halves lazily via
    OPeNDAP's range-select, load into memory, roll east to +360, and
    concat. The slab is small enough (~0.25° × 90 days × Pacific
    strip) to fit easily.
    """
    print(f"{log} opening {OPENDAP_URL}")
    ds = xr.open_dataset(OPENDAP_URL)
    try:
        time_slice = slice(np.datetime64(start.date()),
                           np.datetime64(end.date()))
        lat_slice = slice(LAT_MIN, LAT_MAX)

        print(f"{log} fetching west Pacific (100°E..180°E) "
              f"{start.date()} → {end.date()}")
        west = ds[[TCHP_VAR]].sel(
            time=time_slice,
            lat=lat_slice,
            lon=slice(100.0, 180.0),
        ).load()

        print(f"{log} fetching east Pacific (180°..80°W) "
              f"{start.date()} → {end.date()}")
        east = ds[[TCHP_VAR]].sel(
            time=time_slice,
            lat=lat_slice,
            lon=slice(-180.0, -80.0),
        ).load()
    finally:
        ds.close()

    # Roll east half to +360 convention and concat onto the west half.
    east = east.assign_coords(lon=east["lon"] + 360.0)
    combined = xr.concat([west, east], dim="lon").sortby("lon")

    tchp = combined[TCHP_VAR].values.astype(np.float32)
    times = combined["time"].values
    lat = combined["lat"].values.astype(np.float32)
    lon = combined["lon"].values.astype(np.float32)

    # Ensure ascending lat — AOML typically comes back ascending but
    # normalize defensively so downstream pcolormesh is always sane.
    if lat.size >= 2 and lat[0] > lat[-1]:
        lat = lat[::-1]
        tchp = tchp[:, ::-1, :]

    return tchp, times, lat, lon


def _np_datetime_to_date(t) -> dt.date:
    """Convert numpy.datetime64 → python date (UTC)."""
    seconds = (t - np.datetime64("1970-01-01T00:00:00")) \
        / np.timedelta64(1, "s")
    return dt.datetime.utcfromtimestamp(float(seconds)).date()


def _render_frame(
    tchp_slice: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    valid_date: dt.date,
    countries, coast,
) -> Image.Image:
    """Render a single day's AOML TCHP as an in-memory PIL image."""
    extent = (LON_MIN_0360, LON_MAX_0360, LAT_MIN, LAT_MAX)

    fig, ax = plt.subplots(figsize=FIGSIZE, facecolor=gss.BG_COLOR)
    ax.set_facecolor(gss.PANEL_COLOR)
    LON2, LAT2 = np.meshgrid(lon, lat)
    norm = mcolors.Normalize(vmin=0, vmax=TCHP_VMAX)
    pcm = ax.pcolormesh(
        LON2, LAT2, tchp_slice, cmap=gss.CMAP_TCHP, norm=norm,
        shading="auto", rasterized=True,
    )
    gss._draw_basemap(ax, extent, countries, coast)

    date_label = valid_date.strftime("%B %-d, %Y")
    gss._style_axes(
        ax, extent,
        "Pacific · Tropical Cyclone Heat Potential",
        f"Valid: {date_label}  ·  NOAA AOML",
    )

    cax = fig.add_axes([0.91, 0.18, 0.018, 0.64])
    cb = fig.colorbar(pcm, cax=cax, extend="max")
    cb.set_label("TCHP (kJ/cm²)", color=gss.TEXT_COLOR, fontsize=10)
    cb.ax.yaxis.set_tick_params(
        color=gss.MUTED_COLOR, labelcolor=gss.MUTED_COLOR, labelsize=9,
    )
    cb.outline.set_edgecolor(gss.MUTED_COLOR)
    cb.outline.set_linewidth(0.4)

    gss._draw_watermark(ax)
    fig.subplots_adjust(left=0.05, right=0.89, top=0.86, bottom=0.08)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, facecolor=gss.BG_COLOR)
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Render a Pacific-basin TCHP GIF from AOML data."
    )
    p.add_argument("--start", default="2026-01-20",
                   help="Start date (YYYY-MM-DD).")
    p.add_argument("--end",   default="2026-04-19",
                   help="End date (YYYY-MM-DD), inclusive.")
    p.add_argument("--out",   default="pacific_tchp.gif",
                   help="Output GIF path.")
    p.add_argument("--fps", type=int, default=8,
                   help="Playback speed (frames per second).")
    p.add_argument("--step", type=int, default=1,
                   help="Day step (1 = daily, 2 = every other day, ...).")
    args = p.parse_args(argv)

    start = dt.datetime.fromisoformat(args.start)
    end   = dt.datetime.fromisoformat(args.end)

    log = "[pac-tchp-gif]"

    # Basemap — prefer 50 m resolution, fall back to 110 m if only that
    # was downloaded. If neither exists, plot will have no coastlines.
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

    tchp, times, lat, lon = _fetch_pacific_timeseries(start, end, log)
    print(f"{log} got {len(times)} time steps, "
          f"{lat.size} lat × {lon.size} lon")
    if tchp.size == 0 or len(times) == 0:
        print(f"{log} no data returned for that window — exiting")
        return 1

    # Render every Nth day per --step.
    frames: list[Image.Image] = []
    indices = list(range(0, len(times), max(1, args.step)))
    for n, ti in enumerate(indices):
        valid_date = _np_datetime_to_date(times[ti])
        print(f"{log}   rendering {valid_date}  ({n + 1}/{len(indices)})")
        frames.append(_render_frame(
            tchp[ti], lat, lon, valid_date, countries, coast,
        ))

    if not frames:
        print(f"{log} no frames rendered — exiting")
        return 1

    # Palette-optimize each frame. 128 colors is the sweet spot between
    # file size and banding for ocean-heat maps.
    print(f"{log} palette-optimizing {len(frames)} frames…")
    frames_p = [
        f.convert("P", palette=Image.ADAPTIVE, colors=128)
        for f in frames
    ]

    # Playback: user-requested fps, then hold the last frame for ~1.5 s
    # so viewers can read the final date before the GIF loops.
    duration_ms = max(50, int(1000 / args.fps))
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
