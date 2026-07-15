#!/usr/bin/env python3
"""One-off analog composite: TC frequency anomaly (analog years 1972, 1982,
1991, 1997, 2015 minus 1979-2014 climatology) per 1x1 deg box, 3-deg Gaussian
smooth, analog-year tracks overlaid, current NHC 7-day GTWO MDR area hatched.
Reuses generate_sst_plots chrome (palette, basemap, axes, colorbar, watermark)
and the locally-cached IBTrACS NA/EP list CSVs. Output: analog_composite.png
"""
from __future__ import annotations

import glob
import struct
import sys

import numpy as np
import pandas as pd
from scipy.ndimage import gaussian_filter

import generate_sst_plots as gsp
import matplotlib as mpl
import matplotlib.pyplot as plt

ANALOG_YEARS = [1972, 1982, 1991, 1997, 2015]
CLIMO_YEARS = list(range(1979, 2015))          # 1979-2014 inclusive (36 yrs)
LON_MIN, LON_MAX, LAT_MIN, LAT_MAX = -180, -10, 5, 60
EXTENT = (LON_MIN, LON_MAX, LAT_MIN, LAT_MAX)
NLON, NLAT = LON_MAX - LON_MIN, LAT_MAX - LAT_MIN
MDR = (-60.0, -20.0, 10.0, 20.0)               # lon_min, lon_max, lat_min, lat_max
GTWO_DIR = ("/tmp/claude-1000/-workspaces-Triple-A-Tropics/"
            "40002596-c8a5-4cab-a1f7-accfe3303f8f/scratchpad/gtwo")
OUT = "analog_composite.png"


def load_ibtracs() -> pd.DataFrame:
    usecols = ["SID", "SEASON", "NATURE", "ISO_TIME", "LAT", "LON", "TRACK_TYPE"]
    frames = []
    for f in ("ibtracs.NA.list.v04r01.csv", "ibtracs.EP.list.v04r01.csv"):
        df = pd.read_csv(f, usecols=usecols, skiprows=[1], low_memory=False)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    # Basin-crossing storms appear in both files with the same SID/rows.
    df = df.drop_duplicates(subset=["SID", "ISO_TIME"])
    df["SEASON"] = pd.to_numeric(df["SEASON"], errors="coerce")
    df["LAT"] = pd.to_numeric(df["LAT"], errors="coerce")
    df["LON"] = pd.to_numeric(df["LON"], errors="coerce")
    years = set(ANALOG_YEARS) | set(CLIMO_YEARS)
    df = df[df["SEASON"].isin(years)
            & (df["TRACK_TYPE"] == "main")
            & df["NATURE"].isin(["TS", "SS"])
            & df["LAT"].notna() & df["LON"].notna()]
    return df


def freq_grid(df: pd.DataFrame, years: list[int]) -> np.ndarray:
    """Storms/year per 1x1 box: each storm counted once per box it enters."""
    sub = df[df["SEASON"].isin(years)].copy()
    sub["ix"] = np.floor(sub["LON"] - LON_MIN).astype(int)
    sub["iy"] = np.floor(sub["LAT"] - LAT_MIN).astype(int)
    sub = sub[(sub["ix"] >= 0) & (sub["ix"] < NLON)
              & (sub["iy"] >= 0) & (sub["iy"] < NLAT)]
    cells = sub.drop_duplicates(subset=["SID", "iy", "ix"])
    grid = np.zeros((NLAT, NLON))
    np.add.at(grid, (cells["iy"].to_numpy(), cells["ix"].to_numpy()), 1.0)
    return grid / len(years)


def read_gtwo_mdr():
    """[(poly_points, prob7)] for GTWO areas intersecting the MDR; [] on any miss."""
    try:
        shp_path = glob.glob(f"{GTWO_DIR}/gtwo_areas_*.shp")[0]
        dbf_path = shp_path[:-4] + ".dbf"
        d = open(dbf_path, "rb").read()
        nrec = struct.unpack_from("<I", d, 4)[0]
        hsz, rsz = struct.unpack_from("<HH", d, 8)
        fields, off = [], 32
        while d[off] != 0x0D:
            fields.append((d[off:off + 11].split(b"\0")[0].decode(), d[off + 16]))
            off += 32
        probs = []
        for i in range(nrec):
            rec, p, row = d[hsz + i * rsz:hsz + (i + 1) * rsz][1:], 0, {}
            for name, ln in fields:
                row[name] = rec[p:p + ln].decode(errors="replace").strip()
                p += ln
            probs.append(row.get("PROB7DAY", ""))
        s = open(shp_path, "rb").read()
        out, off, idx = [], 100, 0
        while off < len(s):
            _, clen = struct.unpack(">II", s[off:off + 8]); off += 8
            if struct.unpack("<i", s[off:off + 4])[0] == 5:
                x0, y0, x1, y1 = struct.unpack("<4d", s[off + 4:off + 36])
                nparts, npts = struct.unpack("<2i", s[off + 36:off + 44])
                pts_off = off + 44 + 4 * nparts
                pts = np.frombuffer(s, "<f8", 2 * npts, pts_off).reshape(-1, 2)
                if (x1 >= MDR[0] and x0 <= MDR[1] and y1 >= MDR[2] and y0 <= MDR[3]):
                    out.append((pts, probs[idx] if idx < len(probs) else ""))
                idx += 1
            off += clen * 2
        return out
    except Exception as e:  # noqa: BLE001 — AOI is optional; never block the render
        print(f"GTWO overlay skipped: {type(e).__name__}: {e}", file=sys.stderr)
        return []


def main() -> None:
    df = load_ibtracs()
    n_analog = df[df["SEASON"].isin(ANALOG_YEARS)]["SID"].nunique()
    print(f"points={len(df)} analog storms={n_analog}")

    anom = freq_grid(df, ANALOG_YEARS) - freq_grid(df, CLIMO_YEARS)
    anom = gaussian_filter(anom, sigma=3.0)
    vmax = max(0.1, np.ceil(np.abs(anom).max() * 20) / 20)

    fig, ax = plt.subplots(figsize=(14, 5.6), facecolor=gsp.BG_COLOR)
    fig.subplots_adjust(left=0.05, right=0.89, top=0.84, bottom=0.10)

    # House diverging ramp, symmetrically trimmed of its extreme indigo/magenta
    # tails so the white center stays at zero.
    cmap = mpl.colors.LinearSegmentedColormap.from_list(
        "freq_anom", gsp.CMAP_ANOM(np.linspace(0.07, 0.93, 256)))
    lon_edges = np.arange(LON_MIN, LON_MAX + 1)
    lat_edges = np.arange(LAT_MIN, LAT_MAX + 1)
    mesh = ax.pcolormesh(lon_edges, lat_edges, anom, cmap=cmap,
                         vmin=-vmax, vmax=vmax, zorder=2)

    # Analog-year tracks, thin gray; break at dateline-size jumps.
    for _, storm in df[df["SEASON"].isin(ANALOG_YEARS)].groupby("SID"):
        lons, lats = storm["LON"].to_numpy(), storm["LAT"].to_numpy()
        brk = np.where(np.abs(np.diff(lons)) > 90)[0] + 1
        for seg_lon, seg_lat in zip(np.split(lons, brk), np.split(lats, brk)):
            if len(seg_lon) >= 2:
                ax.plot(seg_lon, seg_lat, color="#c7d3e2", linewidth=0.45,
                        alpha=0.38, zorder=4)

    countries = gsp._load_geojson("ne_50m_admin_0_countries.geojson")
    coast = gsp._load_geojson("ne_50m_coastline.geojson")
    gsp._draw_basemap(ax, EXTENT, countries, coast)

    aoi_note = ""
    prev = {k: mpl.rcParams[k] for k in ("hatch.color", "hatch.linewidth")}
    mpl.rcParams["hatch.color"] = "#e7c24a"
    mpl.rcParams["hatch.linewidth"] = 0.6
    try:
        for pts, prob in read_gtwo_mdr():
            ax.fill(pts[:, 0], pts[:, 1], facecolor="none", hatch="///",
                    edgecolor="#e7c24a", linewidth=1.1, zorder=5)
            cx, cy = pts[:, 0].mean(), pts[:, 1].max()
            ax.text(cx, cy + 1.2, f"NHC 7-day: {prob}", color="#e7c24a",
                    fontsize=9, fontweight="bold", ha="center", va="bottom",
                    zorder=6, path_effects=[gsp._mpl_stroke("#000000", 0.6, 1.6)])
            aoi_note = " · hatch: NHC 7-day outlook area"
    finally:
        mpl.rcParams.update(prev)

    title = "Tropical cyclone frequency anomaly · analog-year composite"
    subtitle = ("1972, 1982, 1991, 1997, 2015 minus 1979-2014 climatology · "
                "storms/year per 1°×1° box, 3° Gaussian smooth · gray: analog "
                "tracks" + aoi_note + " · Data: IBTrACS v04r01")
    gsp._style_axes(ax, EXTENT, title, None)
    ax.text(0.0, 1.015, subtitle, color=gsp.MUTED_COLOR, fontsize=9,
            transform=ax.transAxes, va="bottom")
    gsp._add_colorbar(fig, mesh, "storms/year")
    gsp._draw_watermark(ax)

    fig.savefig(OUT, dpi=150, facecolor=gsp.BG_COLOR)
    print(f"wrote {OUT} (vmax={vmax:.2f}, "
          f"anom range {anom.min():+.2f}..{anom.max():+.2f})")


if __name__ == "__main__":
    main()
