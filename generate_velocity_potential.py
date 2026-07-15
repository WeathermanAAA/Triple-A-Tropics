#!/usr/bin/env python3
"""generate_velocity_potential.py — TIME-MEAN global velocity-potential
(chi) anomaly maps at 200 & 850 hPa for /subseasonal/, rendered in-house.

METHOD (v2, 2026-07-13 — replaces the single-snapshot v1): a velocity-
potential anomaly product must be a TIME MEAN; an instantaneous analysis
is transient-wave noise (local bullseyes, ~3x the magnitude of a real
anomaly). This generator maintains a ROLLING DAILY-CHI ARCHIVE:

  each day = mean of that day's GFS 1-deg analyses (00/06/12/18Z, >=2
  required) -> chi solved at T21 (subseasonal/chi_core.py). The solve is
  LINEAR in wind, so daily-mean chi == chi of the daily-mean wind, window
  means of chi == chi of window-mean wind, and the divergent wind of a
  mean is the gradient of the mean chi (all test-locked in
  tests/test_vp_windows.py). The archive (~50 KB/day) lives in R2 and is
  restored/saved by the workflow around each run; missing days self-heal
  (newest first, --backfill-days per run).

Products (window selector on the page; 30-day is the default):
  pentad  5-day mean        chi_anom_{200,850}_pentad.png
  30d     30-day mean       chi_anom_{200,850}_30d.png (+ legacy
                            chi_anom_{200,850}.png copies)
  90d     90-day mean       chi_anom_{200,850}_90d.png
  mjo     20-100-day Lanczos bandpass (Duchon 1979), real-time endpoint:
          the future half-window is zero-padded, so the newest map is
          amplitude-damped — the retained fraction is PRINTED ON the map.
          Needs >=61 archived days; skipped honestly below that.

Anomalies are vs the 1991-2020 ERA5 monthly chi climatology
(subseasonal/chi_climo_1991_2020.nc, built by build_chi_climatology.py:
ERA5 monthly-mean winds subsampled to 1 deg, the SAME T21 solve both
sides — like-vs-modern-like under the GFS analyses at the planetary
scales this product keeps; the earlier NCEP/NCAR R1 baseline ran hot).
CREDITS MUST MATCH THE COMMITTED FILE — the credit strings here +
subseasonal/index.html flip together with the .nc, never separately.
Quiver arrows are the ANOMALOUS divergent wind (grad of the plotted
anomaly chi). Maps span 60S-60N (the 45-deg band clipped the
subtropical centers).

Reading the maps (green = NEGATIVE chi anomaly at both levels):
  200 hPa: green = anomalous upper-level divergence -> enhanced deep
           convection.
  850 hPa: green = anomalous LOW-level divergence; enhanced convection
           sits over the BROWN (low-level convergence) centers.

Outputs: subseasonal/out/chi_anom_*.png, vp_meta.json (+ the archive).
Usage:   python generate_velocity_potential.py [--out DIR]
             [--archive PATH] [--backfill-days N] [--target-depth N]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import multiprocessing as mp

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "subseasonal"))
import chi_core  # noqa: E402
import vp_windows  # noqa: E402

CLIMO_NC = HERE / "subseasonal" / "chi_climo_1991_2020.nc"

BG_COLOR = "#07101c"
TEXT_COLOR = "#e5edf6"
MUTED_COLOR = "#8ea2bd"
GRID = "#22304a"
COAST = "#0a0e15"          # crisp dark coastline stroke
COAST_CASING = "#dce7f3"   # thin light casing UNDER it — keeps the line
                           # legible over saturated BrBG fills at any lightness
WATERMARK = "@WeathermanAAA_"

LAT_BAND = 60.0            # map band: 60S-60N (45 clipped divergent centers)
QUIVER_BAND = 55.0         # keep arrows off the noisy band edge
QUIVER_STRIDE = 8          # 1-deg grid -> arrows every 8 deg
LEVELS = (200, 850)
MIN_CYCLES_PER_DAY = 2

WINDOWS = [("pentad", "5-day mean", 5),
           ("30d", "30-day mean", 30),
           ("90d", "90-day mean", 90)]
DEFAULT_WINDOW = "30d"
MJO_MIN_DAYS = 61          # half-window + 1: below this the filter is fiction


# ------------------------------------------------------------------ fetching

def fetch_cycle(t: dt.datetime):
    """One GFS 1-deg analysis (fxx=0) -> (u, v, lats, lons) at 200/850,
    or None if the cycle isn't available."""
    from herbie import Herbie
    warnings.filterwarnings("ignore")
    if t > dt.datetime.now(dt.timezone.utc).replace(tzinfo=None):
        return None
    try:
        h = Herbie(t, model="gfs", product="pgrb2.1p00", fxx=0,
                   verbose=False)
        if not h.grib:
            return None
        ds = h.xarray(":(UGRD|VGRD):(200|850) mb")
        if isinstance(ds, list):
            import xarray as xr
            ds = xr.merge(ds, compat="override")
        lats = ds.latitude.values
        lons = ds.longitude.values
        u = np.stack([ds.u.sel(isobaricInhPa=lv).values for lv in LEVELS])
        v = np.stack([ds.v.sel(isobaricInhPa=lv).values for lv in LEVELS])
        return u.astype(float), v.astype(float), lats, lons
    except Exception as e:  # noqa: BLE001 — a missing cycle is expected
        print(f"    cycle {t:%Y-%m-%d %HZ}: {e}")
        return None


def fetch_day_chi(day: dt.date):
    """Daily-mean chi for one day: mean of the day's available analyses'
    winds (>= MIN_CYCLES_PER_DAY) -> one T21 solve per level (linearity).
    -> (chi[level,lat,lon], ncycles, lats, lons) or None."""
    got = []
    for hour in (0, 6, 12, 18):
        r = fetch_cycle(dt.datetime(day.year, day.month, day.day, hour))
        if r is not None:
            got.append(r)
    if len(got) < MIN_CYCLES_PER_DAY:
        print(f"  {day}: only {len(got)} cycles — skipped")
        return None
    lats, lons = got[0][2], got[0][3]
    u_mean = np.mean([g[0] for g in got], axis=0)
    v_mean = np.mean([g[1] for g in got], axis=0)
    chi = np.empty_like(u_mean)
    for li in range(len(LEVELS)):
        chi[li], _, _ = chi_core.chi_from_uv(u_mean[li], v_mean[li],
                                             lats, lons)
    print(f"  {day}: {len(got)} cycles ok")
    return chi, len(got), lats, lons


def backfill(archive, target_depth: int, max_fetch: int, workers: int = 4):
    """Fill the archive toward target_depth days ending yesterday/today,
    newest missing days first (the 30-day product heals before the 90-day
    tail). Returns the updated (times, levels, lats, lons, chi, ncycles)."""
    today = dt.datetime.now(dt.timezone.utc).date()
    # today counts once >=2 of its cycles exist; try it, fall back silently
    wanted = [today - dt.timedelta(days=i) for i in range(target_depth + 1)]

    if archive is None:
        times, levels, lats, lons = [], np.array(LEVELS, float), None, None
        chi = None
        ncyc = np.zeros(0, np.int8)
    else:
        times, levels, lats, lons, chi, ncyc = archive

    have = set(times)
    missing = [d for d in wanted if d not in have][:max_fetch]
    if not missing:
        print("archive complete — no backfill needed")
        return times, levels, lats, lons, chi, ncyc
    print(f"backfilling {len(missing)} day(s) (newest first, "
          f"{workers} workers)")
    # PROCESSES, not threads: eccodes/cfgrib (and pyshtools' Fortran) are
    # not thread-safe — a ThreadPoolExecutor here segfaulted. Spawned
    # workers give each fetch+decode+solve its own library state.
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
        results = list(ex.map(fetch_day_chi, missing, chunksize=4))
    for day, r in zip(missing, results):
        if r is None:
            continue
        day_chi, n, la, lo = r
        if lats is None:
            lats, lons = la, lo
        if chi is None:
            chi = day_chi[None]
            times = [day]
            ncyc = np.array([n], np.int8)
        else:
            chi = np.concatenate([chi, day_chi[None]], axis=0)
            times.append(day)
            ncyc = np.append(ncyc, np.int8(n))
    if chi is None:
        raise RuntimeError("no GFS analyses reachable at all")
    # trim anything older than the deepest window we could ever need
    keep = [i for i, t in enumerate(times)
            if (today - t).days <= max(400, target_depth)]
    order = sorted(keep, key=lambda i: times[i])
    return ([times[i] for i in order], levels, lats, lons,
            chi[order], ncyc[order])


# ---------------------------------------------------------------- climo

_CLIMO_DS = None


def climo_chi_for(date: dt.date, level: float, lats, lons) -> np.ndarray:
    """Monthly climo chi linearly interpolated to the date (month centers),
    bilinearly regridded from the climatology grid to (lats, lons)."""
    global _CLIMO_DS
    import xarray as xr
    if _CLIMO_DS is None:
        _CLIMO_DS = xr.open_dataset(CLIMO_NC)
    ds = _CLIMO_DS
    doy = date.timetuple().tm_yday
    year = date.year
    centers = []
    for m in range(1, 13):
        d0 = dt.date(year, m, 1)
        d1 = (dt.date(year + (m == 12), (m % 12) + 1, 1)
              - dt.timedelta(days=1))
        centers.append((d0.timetuple().tm_yday + d1.timetuple().tm_yday) / 2)
    centers = np.array(centers)
    if doy <= centers[0]:
        m0, m1 = 12, 1
        w = min(max((doy - (centers[0] - 30.5)) / 30.5, 0.0), 1.0)
    elif doy >= centers[-1]:
        m0, m1 = 12, 1
        w = min(max((doy - centers[-1]) / 30.5, 0.0), 1.0)
    else:
        m1 = int(np.searchsorted(centers, doy)) + 1
        m0 = m1 - 1
        w = (doy - centers[m0 - 1]) / (centers[m1 - 1] - centers[m0 - 1])
    c0 = ds.chi.sel(level=level, month=m0)
    c1 = ds.chi.sel(level=level, month=m1)
    blend = (1 - w) * c0 + w * c1
    # periodic longitude pad so target lons past the last column interp
    wrap = blend.isel(lon=0).assign_coords(lon=360.0)
    blend = xr.concat([blend, wrap], dim="lon")
    out = blend.interp(lat=lats, lon=lons, method="linear")
    return out.values


# ---------------------------------------------------------------- rendering

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
                     else g.get("coordinates", [])
                     if g.get("type") == "MultiLineString" else [])
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
                 heading: str, extra_note: str, out_png: Path, coast) -> None:
    sel = np.abs(lats) <= LAT_BAND
    la = lats[sel]
    z = chi_anom[sel, :] / 1e6

    # 1.5x-canvas note (see the full-bleed page): constant dpi/fonts.
    # Height carries the 60S-60N band at the same deg-per-inch as the old
    # 45-deg map's 4.6-in-worth of chrome + data.
    fig, ax = plt.subplots(figsize=(18.9, 8.6), facecolor=BG_COLOR)
    ax.set_facecolor(BG_COLOR)
    # symmetric integer-stepped levels (clean colorbar ticks); time-mean
    # anomalies are ~±4-6, so step 1 below vmax 8
    vmax = max(4.0, np.ceil(float(np.percentile(np.abs(z), 99)) / 2) * 2)
    step = 1.0 if vmax <= 8 else 2.0 if vmax <= 16 else 3.0
    levels = np.arange(-vmax, vmax + step / 2, step)
    # BrBG: CVD-safe diverging; REVERSED so green sits on NEGATIVE chi'
    cf = ax.contourf(lons, la, z, levels=levels, cmap="BrBG_r", extend="both")
    ax.contour(lons, la, z, levels=[0], colors=GRID, linewidths=0.7)

    for seg in coast:
        ax.plot(seg[:, 0], seg[:, 1], color=COAST_CASING, lw=1.6,
                alpha=0.62, zorder=4, solid_capstyle="round")
    for seg in coast:
        ax.plot(seg[:, 0], seg[:, 1], color=COAST, lw=0.7, alpha=0.95,
                zorder=4.01)

    # anomalous divergent-wind quiver: quiet annotation, never the subject
    st = QUIVER_STRIDE
    qsel = np.abs(la) <= QUIVER_BAND
    ax.quiver(lons[::st], la[qsel][::st],
              u_chi[sel, :][qsel][::st, ::st],
              v_chi[sel, :][qsel][::st, ::st],
              color="#0c1118", scale=90, width=0.0012, headwidth=4.5,
              alpha=0.7, zorder=5)

    ax.set_xlim(0, 360)
    ax.set_ylim(-LAT_BAND, LAT_BAND)
    ax.set_xticks(np.arange(0, 361, 60))
    ax.set_xticklabels(["0°", "60°E", "120°E", "180°", "120°W", "60°W",
                        "0°"])
    ax.set_yticks(np.arange(-60, 61, 20))
    ax.set_yticklabels(["60°S", "40°S", "20°S", "EQ", "20°N", "40°N",
                        "60°N"])
    ax.tick_params(colors=MUTED_COLOR, labelsize=9)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.grid(color=GRID, lw=0.4, alpha=0.5)

    reading = ("green: anomalous upper-level divergence → enhanced deep "
               "convection" if level == 200 else
               "green: anomalous LOW-level divergence, enhanced convection "
               "sits over the brown (low-level convergence) centers")
    ax.set_title(f"{level}-hPa velocity potential anomaly (χ′, T21) · "
                 f"{heading} · vs 1991–2020",
                 color=TEXT_COLOR, fontsize=12.5, fontweight="bold",
                 loc="left", pad=24)
    note = reading + " · arrows: anomalous divergent wind"
    if extra_note:
        note += " · " + extra_note
    ax.text(0.0, 1.018, note, transform=ax.transAxes, color=MUTED_COLOR,
            fontsize=9)
    ax.text(1.0, 1.018, WATERMARK, transform=ax.transAxes, ha="right",
            color=MUTED_COLOR, alpha=0.7, fontsize=9)
    cb = fig.colorbar(cf, ax=ax, pad=0.012, fraction=0.035)
    cb.set_label("χ′ (10⁶ m² s⁻¹)", color=MUTED_COLOR, fontsize=9)
    cb.ax.tick_params(colors=MUTED_COLOR, labelsize=8)
    cb.outline.set_edgecolor(GRID)
    ax.text(0.0, -0.12,
            "Data: NOAA NCEP GFS analyses (daily means) · climatology: "
            "ERA5 monthly means 1991–2020 (ECMWF, via UH APDRC), χ solved "
            "identically · spectral solve truncated T21",
            transform=ax.transAxes, color=MUTED_COLOR, alpha=0.9, fontsize=8)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150, facecolor=BG_COLOR)
    plt.close(fig)


# ---------------------------------------------------------------- main

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(HERE / "subseasonal" / "out"))
    p.add_argument("--archive", default=None,
                   help="daily-chi archive NetCDF (default <out>/"
                        "chi_daily_archive.nc)")
    p.add_argument("--backfill-days", type=int, default=45,
                   help="max missing days fetched this run")
    p.add_argument("--target-depth", type=int, default=220,
                   help="how many days back the archive should reach")
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    archive_path = Path(args.archive) if args.archive else \
        out / "chi_daily_archive.nc"

    archive = vp_windows.load_archive(archive_path)
    if archive:
        print(f"archive: {len(archive[0])} days "
              f"({archive[0][0]} .. {archive[0][-1]})")
    times, levels, lats, lons, chi, ncyc = backfill(
        archive, args.target_depth, args.backfill_days)
    vp_windows.save_archive(archive_path, times, levels, lats, lons,
                            chi, ncyc)
    print(f"archive saved: {len(times)} days "
          f"({times[0]} .. {times[-1]})")

    end = times[-1]
    today = dt.datetime.now(dt.timezone.utc).date()
    if (today - end).days > 3:
        print(f"WARNING: newest archived day {end} is "
              f"{(today - end).days} days old")

    coast = load_coast()
    weights = vp_windows.lanczos_bandpass_weights()
    meta = {"levels": list(LEVELS), "default": DEFAULT_WINDOW,
            "windows": {}, "available": []}

    for li, level in enumerate(LEVELS):
        # daily anomaly stack once per level; every window derives from it
        anom = np.empty_like(chi[:, li])
        for ti, t in enumerate(times):
            anom[ti] = chi[ti, li] - climo_chi_for(t, float(level),
                                                   lats, lons)

        for key, label, days in WINDOWS:
            try:
                field, used = vp_windows.window_mean(times, anom, days, end)
            except ValueError as e:
                print(f"{key}@{level}: {e} — skipped")
                continue
            field = field - field.mean()      # chi gauge constant
            u_chi, v_chi = chi_core.grad_chi(field, lats, lons)
            heading = f"{label} ending {end:%Y-%m-%d}"
            render_level(field, u_chi, v_chi, lats, lons, level, heading,
                         "", out / f"chi_anom_{level}_{key}.png", coast)
            print(f"chi'({level}) {key}: [{field.min() / 1e6:+.1f}, "
                  f"{field.max() / 1e6:+.1f}] x1e6 m2/s ({used} days)")
            if li == 0:
                meta["windows"][key] = {"label": label,
                                        "end": end.isoformat(),
                                        "days_used": used}
                meta["available"].append(key)

        # MJO band: only when the archive is deep enough to mean anything
        if len(times) >= MJO_MIN_DAYS:
            # the filter assumes a regular daily series; use the contiguous
            # tail (gaps end a run) so weights align with real days
            tail = [len(times) - 1]
            for i in range(len(times) - 2, -1, -1):
                if (times[i + 1] - times[i]).days == 1:
                    tail.append(i)
                else:
                    break
            tail = tail[::-1]
            if len(tail) >= MJO_MIN_DAYS:
                filt, retained = vp_windows.bandpass_latest(anom[tail],
                                                            weights)
                filt = filt - filt.mean()
                u_chi, v_chi = chi_core.grad_chi(filt, lats, lons)
                note = (f"real-time filter endpoint: ~{retained:.0%} "
                        f"amplitude retained")
                render_level(filt, u_chi, v_chi, lats, lons, level,
                             f"20–100-day (MJO band) filtered · "
                             f"{end:%Y-%m-%d}", note,
                             out / f"chi_anom_{level}_mjo.png", coast)
                print(f"chi'({level}) mjo: [{filt.min() / 1e6:+.1f}, "
                      f"{filt.max() / 1e6:+.1f}] x1e6 m2/s "
                      f"(retained {retained:.0%})")
                if li == 0:
                    meta["windows"]["mjo"] = {
                        "label": "MJO band (20–100-day filter)",
                        "end": end.isoformat(),
                        "days_used": len(tail),
                        "retained": round(retained, 2)}
                    meta["available"].append("mjo")
            else:
                print(f"mjo@{level}: contiguous tail {len(tail)} < "
                      f"{MJO_MIN_DAYS} days — skipped")
        else:
            print(f"mjo@{level}: archive {len(times)} < {MJO_MIN_DAYS} "
                  f"days — skipped")

    # legacy filenames = the default window (cached pages keep working)
    for level in LEVELS:
        src = out / f"chi_anom_{level}_{DEFAULT_WINDOW}.png"
        if src.exists():
            shutil.copyfile(src, out / f"chi_anom_{level}.png")

    dflt = meta["windows"].get(DEFAULT_WINDOW, {})
    meta["cycle"] = (f"{dflt.get('label', '30-day mean')} ending "
                     f"{dflt.get('end', end.isoformat())}")
    meta["generated_utc"] = dt.datetime.now(dt.timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    (out / "vp_meta.json").write_text(json.dumps(meta))
    print("wrote", out)


if __name__ == "__main__":
    main()
