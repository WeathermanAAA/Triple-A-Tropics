#!/usr/bin/env python3
"""generate_velocity_potential.py — global velocity-potential (chi) anomaly
maps at 200 & 850 hPa for /subseasonal/, rendered in-house.

Pipeline: latest available GFS/GDAS analysis u/v (1.0-deg, via Herbie
byte-range GRIB fetch) -> spherical-harmonic Poisson solve for chi at T21
(subseasonal/chi_core.py; the windspharm method reimplemented on pyshtools)
-> anomaly against the committed 1991-2020 NCEP/NCAR R1 monthly chi
climatology (linear interpolation between month centers) -> Pacific-centered
maps with the divergent-wind quiver.

Reading the maps (green = NEGATIVE chi anomaly at both levels, by design):
  200 hPa: green = upper-level divergence -> enhanced deep convection.
  850 hPa: green = LOW-level divergence; enhanced convection sits over the
           BROWN (low-level convergence) centers. Each panel says so.

Outputs: subseasonal/out/chi_anom_200.png, chi_anom_850.png, vp_meta.json.
Usage:   python generate_velocity_potential.py [--out subseasonal/out]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "subseasonal"))
import chi_core  # noqa: E402

CLIMO_NC = HERE / "subseasonal" / "chi_climo_1991_2020.nc"

BG_COLOR = "#07101c"
TEXT_COLOR = "#e5edf6"
MUTED_COLOR = "#8ea2bd"
GRID = "#22304a"
COAST = "#10151d"
WATERMARK = "@WeathermanAAA_"

LAT_BAND = 45.0            # map band: 45S-45N (the tropics are the subject)
QUIVER_STRIDE = 8          # 1-deg grid -> arrows every 8 deg
LEVELS_1E6 = np.arange(-15, 15.1, 1.5)   # filled contours, 1e6 m^2/s units


def latest_analysis(max_back_hours: int = 24):
    """Newest GFS analysis (fxx=0) u/v at 200 & 850 on the 1-deg grid."""
    from herbie import Herbie
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    cyc = now.replace(minute=0, second=0, microsecond=0)
    cyc = cyc.replace(hour=(cyc.hour // 6) * 6)
    for back in range(0, max_back_hours + 1, 6):
        t = cyc - dt.timedelta(hours=back)
        try:
            h = Herbie(t, model="gfs", product="pgrb2.1p00", fxx=0)
            if not h.grib:
                continue
            ds = h.xarray(":(UGRD|VGRD):(200|850) mb")
            if isinstance(ds, list):
                import xarray as xr
                ds = xr.merge(ds, compat="override")
            return ds, t
        except Exception as e:  # noqa: BLE001 - walk back to the prior cycle
            print(f"cycle {t:%Y-%m-%d %HZ} unavailable: {e}")
    raise RuntimeError("no GFS analysis reachable in the lookback window")


def climo_chi_for(date: dt.date, level: float, lats, lons) -> np.ndarray:
    """Monthly climo chi linearly interpolated to the date (month centers),
    bilinearly regridded from the 2.5-deg climatology grid to (lats, lons)."""
    import xarray as xr
    ds = xr.open_dataset(CLIMO_NC)
    # month-center weights
    doy = date.timetuple().tm_yday
    year = date.year
    centers = []
    for m in range(1, 13):
        d0 = dt.date(year, m, 1)
        d1 = (dt.date(year + (m == 12), (m % 12) + 1, 1) - dt.timedelta(days=1))
        centers.append((d0.timetuple().tm_yday + d1.timetuple().tm_yday) / 2)
    centers = np.array(centers)
    if doy <= centers[0]:
        m0, m1, w = 12, 1, 0.5 + (doy / (2 * centers[0]))  # simple wrap blend
        w = min(max((doy - (centers[0] - 30.5)) / 30.5, 0.0), 1.0)
    elif doy >= centers[-1]:
        m0, m1 = 12, 1
        w = min(max((doy - centers[-1]) / 30.5, 0.0), 1.0)
    else:
        m1 = int(np.searchsorted(centers, doy)) + 1
        m0 = m1 - 1
        w = (doy - centers[m0 - 1]) / (centers[m1 - 1] - centers[m0 - 1])
    import xarray as xr
    c0 = ds.chi.sel(level=level, month=m0)
    c1 = ds.chi.sel(level=level, month=m1)
    blend = (1 - w) * c0 + w * c1
    # periodic longitude: pad the 0-column at 360 so target lons past the
    # climo's last column (357.5) interpolate instead of going NaN
    wrap = blend.isel(lon=0).assign_coords(lon=360.0)
    blend = xr.concat([blend, wrap], dim="lon")
    out = blend.interp(lat=lats, lon=lons, method="linear")
    return out.values


def load_coast() -> list[np.ndarray]:
    import json as _json
    segs: list[np.ndarray] = []
    for name in ("ne_110m_coastline.geojson", "ne_50m_coastline.geojson"):
        p = HERE / name
        if not p.exists():
            continue
        gj = _json.loads(p.read_text())
        for f in gj.get("features", []):
            g = f.get("geometry") or {}
            lines = ([g["coordinates"]] if g.get("type") == "LineString"
                     else g.get("coordinates", []) if g.get("type") == "MultiLineString"
                     else [])
            for ln in lines:
                a = np.asarray(ln, float)
                a[:, 0] = np.mod(a[:, 0], 360.0)   # Pacific-centered 0..360
                # split segments that wrap across the 0/360 seam
                jump = np.where(np.abs(np.diff(a[:, 0])) > 180)[0]
                for chunk in np.split(a, jump + 1):
                    if len(chunk) > 1:
                        segs.append(chunk)
        break
    return segs


def render_level(chi_anom: np.ndarray, u_chi: np.ndarray, v_chi: np.ndarray,
                 lats: np.ndarray, lons: np.ndarray, level: int,
                 valid: dt.datetime, out: Path, coast) -> None:
    sel = np.abs(lats) <= LAT_BAND
    la = lats[sel]
    z = chi_anom[sel, :] / 1e6

    fig, ax = plt.subplots(figsize=(12.6, 4.6), facecolor=BG_COLOR)
    ax.set_facecolor(BG_COLOR)
    # symmetric integer-stepped levels (clean colorbar ticks)
    vmax = max(4.0, np.ceil(float(np.percentile(np.abs(z), 99)) / 2) * 2)
    step = 2.0 if vmax <= 16 else 3.0
    levels = np.arange(-vmax, vmax + step / 2, step)
    # BrBG: CVD-safe diverging; REVERSED so green sits on NEGATIVE chi'
    cf = ax.contourf(lons, la, z, levels=levels, cmap="BrBG_r", extend="both")
    ax.contour(lons, la, z, levels=[0], colors=GRID, linewidths=0.7)

    for seg in coast:
        ax.plot(seg[:, 0], seg[:, 1], color=COAST, lw=0.8, alpha=0.9, zorder=4)

    # divergent-wind quiver: quiet annotation, never the subject (thin, short)
    st = QUIVER_STRIDE
    qsel = np.abs(la) <= 40
    ax.quiver(lons[::st], la[qsel][::st],
              u_chi[sel, :][qsel][::st, ::st], v_chi[sel, :][qsel][::st, ::st],
              color="#0c1118", scale=260, width=0.0012, headwidth=4.5,
              alpha=0.7, zorder=5)

    ax.set_xlim(0, 360)
    ax.set_ylim(-LAT_BAND, LAT_BAND)
    ax.set_xticks(np.arange(0, 361, 60))
    ax.set_xticklabels(["0°", "60°E", "120°E", "180°", "120°W", "60°W", "0°"])
    ax.set_yticks(np.arange(-40, 41, 20))
    ax.set_yticklabels(["40°S", "20°S", "EQ", "20°N", "40°N"])
    ax.tick_params(colors=MUTED_COLOR, labelsize=9)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.grid(color=GRID, lw=0.4, alpha=0.5)

    reading = ("green: upper-level divergence → enhanced deep convection"
               if level == 200 else
               "green: LOW-level divergence — enhanced convection sits over "
               "the brown (low-level convergence) centers")
    ax.set_title(f"{level}-hPa velocity potential anomaly (χ′, T21) · "
                 f"vs 1991–2020 · {valid:%Y-%m-%d %H} UTC analysis",
                 color=TEXT_COLOR, fontsize=12.5, fontweight="bold",
                 loc="left", pad=24)
    ax.text(0.0, 1.03, reading + " · arrows: divergent wind",
            transform=ax.transAxes, color=MUTED_COLOR, fontsize=9)
    ax.text(1.0, 1.03, WATERMARK, transform=ax.transAxes, ha="right",
            color=MUTED_COLOR, alpha=0.7, fontsize=9)
    cb = fig.colorbar(cf, ax=ax, pad=0.012, fraction=0.035)
    cb.set_label("χ′ (10⁶ m² s⁻¹)", color=MUTED_COLOR, fontsize=9)
    cb.ax.tick_params(colors=MUTED_COLOR, labelsize=8)
    cb.outline.set_edgecolor(GRID)
    ax.text(0.0, -0.16,
            "Data: NOAA NCEP GFS analysis · climatology: NCEP/NCAR "
            "Reanalysis 1 (NOAA PSL) · χ solved spectrally, truncated T21",
            transform=ax.transAxes, color=MUTED_COLOR, alpha=0.9, fontsize=8)
    fig.tight_layout()
    fig.savefig(out / f"chi_anom_{level}.png", dpi=150, facecolor=BG_COLOR)
    plt.close(fig)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(HERE / "subseasonal" / "out"))
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    ds, cycle = latest_analysis()
    lats = ds.latitude.values
    lons = ds.longitude.values
    coast = load_coast()
    meta = {"cycle": cycle.strftime("%Y-%m-%dT%H:00Z"), "levels": []}
    for level in (200, 850):
        u = ds.u.sel(isobaricInhPa=level).values.astype(float)
        v = ds.v.sel(isobaricInhPa=level).values.astype(float)
        chi, u_chi, v_chi = chi_core.chi_from_uv(u, v, lats, lons)
        climo = climo_chi_for(cycle.date(), float(level), lats, lons)
        anom = chi - climo
        anom -= anom.mean()                     # chi is defined up to a constant
        render_level(anom, u_chi, v_chi, lats, lons, level, cycle, out, coast)
        meta["levels"].append(level)
        print(f"chi'({level}) range [{anom.min()/1e6:+.1f}, "
              f"{anom.max()/1e6:+.1f}] x1e6 m2/s")
    meta["generated_utc"] = dt.datetime.now(dt.timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    (out / "vp_meta.json").write_text(json.dumps(meta))
    print("wrote", out)


if __name__ == "__main__":
    main()
