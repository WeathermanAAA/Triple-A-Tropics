#!/usr/bin/env python3
"""
Triple-A-Tropics · Pacific TCHP GIF generator (one-off)
========================================================

Fetches daily ARMOR3D temperature over the Pacific for a custom date
range, computes TCHP per day, renders one Pacific-centered frame per
day, and assembles everything into a single GIF intended for blog /
social-media posts.

This script is **not** wired into the site or the weekly ARMOR3D
update. It runs on demand via the `make-pacific-tchp-gif.yml`
GitHub Actions workflow, and the resulting GIF is uploaded as a
workflow artifact (not committed to the repo).

Usage
-----
    export COPERNICUSMARINE_SERVICE_USERNAME=...
    export COPERNICUSMARINE_SERVICE_PASSWORD=...
    python generate_pacific_tchp_gif.py \\
        --start 2026-01-20 --end 2026-04-19 \\
        --out pacific_tchp.gif
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import sys
import tempfile
from pathlib import Path

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
from PIL import Image

import generate_armor3d_plots as a3d
import generate_subsurface_plots as gss


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


def _fetch_pacific(
    start: dt.datetime,
    end: dt.datetime,
    dataset_id: str,
    log: str,
) -> xr.Dataset:
    """Fetch Pacific-only temperature as one xarray Dataset (lon 0-360).

    CMEMS expects lon in -180..180, so the Pacific (crossing the
    dateline) has to come back in two subsets. We download both halves,
    roll the east-hemisphere longitudes to +360, and concat."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="pac_tchp_"))
    west_path = tmp_dir / "pac_west.nc"
    east_path = tmp_dir / "pac_east.nc"

    print(f"{log} fetching west Pacific (100°E..180°E) "
          f"{start.date()} → {end.date()}")
    a3d._cmems_subset(
        dataset_id=dataset_id,
        start=start, end=end,
        lon_min=100.0, lon_max=179.875,
        lat_min=LAT_MIN, lat_max=LAT_MAX,
        depth_min=0.0, depth_max=a3d.DEPTH_MAX,
        variables=["to"],
        out_path=west_path, log=log,
    )

    print(f"{log} fetching east Pacific (180°..80°W) "
          f"{start.date()} → {end.date()}")
    a3d._cmems_subset(
        dataset_id=dataset_id,
        start=start, end=end,
        lon_min=-180.0, lon_max=-80.0,
        lat_min=LAT_MIN, lat_max=LAT_MAX,
        depth_min=0.0, depth_max=a3d.DEPTH_MAX,
        variables=["to"],
        out_path=east_path, log=log,
    )

    # Load both into memory (Pacific strip is small enough — <2 GB for
    # 90 days at 0.125° × 30 depth levels), roll east to +360, concat.
    west = xr.open_dataset(west_path).load()
    east = xr.open_dataset(east_path).load()
    east = east.assign_coords(longitude=east["longitude"] + 360.0)
    combined = xr.concat([west, east], dim="longitude").sortby("longitude")
    west.close()
    east.close()
    return combined


def _render_frame(
    t_arr: np.ndarray,
    depth: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    valid_date: dt.date,
    countries, coast,
) -> Image.Image:
    """Render a single day's TCHP as an in-memory PIL image."""
    d26 = a3d.compute_d26(t_arr, depth)
    tchp = a3d.compute_tchp(t_arr, depth, d26)

    extent = (LON_MIN_0360, LON_MAX_0360, LAT_MIN, LAT_MAX)

    fig, ax = plt.subplots(figsize=FIGSIZE, facecolor=gss.BG_COLOR)
    ax.set_facecolor(gss.PANEL_COLOR)
    LON2, LAT2 = np.meshgrid(lon, lat)
    norm = mcolors.Normalize(vmin=0, vmax=TCHP_VMAX)
    pcm = ax.pcolormesh(
        LON2, LAT2, tchp, cmap=gss.CMAP_TCHP, norm=norm,
        shading="auto", rasterized=True,
    )
    gss._draw_basemap(ax, extent, countries, coast)

    date_label = valid_date.strftime("%B %-d, %Y")
    gss._style_axes(
        ax, extent,
        "Pacific · Tropical Cyclone Heat Potential",
        f"Valid: {date_label}  ·  ARMOR3D daily",
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
        description="Render a Pacific-basin TCHP GIF over a date range."
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
    p.add_argument("--dataset", default=a3d.ARMOR3D_NRT_DATASET,
                   help=f"CMEMS dataset ID (default {a3d.ARMOR3D_NRT_DATASET}).")
    args = p.parse_args(argv)

    start = dt.datetime.fromisoformat(args.start)
    end   = dt.datetime.fromisoformat(args.end).replace(
        hour=23, minute=59, second=59,
    )

    if not a3d._have_credentials():
        raise RuntimeError(
            "CMEMS credentials missing. Set "
            "COPERNICUSMARINE_SERVICE_USERNAME and "
            "COPERNICUSMARINE_SERVICE_PASSWORD before running."
        )

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

    ds = _fetch_pacific(start, end, args.dataset, log)
    depth = ds["depth"].values.astype(np.float32)
    lat   = ds["latitude"].values.astype(np.float32)
    lon   = ds["longitude"].values.astype(np.float32)
    times = ds["time"].values
    print(f"{log} got {len(times)} time steps, "
          f"{lat.size} lat × {lon.size} lon × {depth.size} depth")

    # Render every Nth day per --step.
    frames: list[Image.Image] = []
    indices = list(range(0, len(times), max(1, args.step)))
    for n, ti in enumerate(indices):
        valid_date = a3d._np_datetime_to_date(times[ti])
        print(f"{log}   rendering {valid_date}  ({n + 1}/{len(indices)})")
        t_arr = ds["to"].isel(time=ti).values.astype(np.float32)
        frames.append(_render_frame(
            t_arr, depth, lat, lon, valid_date, countries, coast,
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
