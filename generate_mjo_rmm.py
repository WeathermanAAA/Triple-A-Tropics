#!/usr/bin/env python3
"""generate_mjo_rmm.py — MJO RMM phase-space diagram + amplitude timeseries
for /subseasonal/ (rendered in-house from the BoM RMM index; no external
imagery is ever embedded).

Data: Bureau of Meteorology (Australia) daily RMM values (Wheeler & Hendon
2004 index; BoM real-time file). Whitespace table, 2 header lines:
year month day RMM1 RMM2 phase amplitude [method]. Missing = 1.E36 / 999.
Primary URL is the IDCKGEM000 clim_data path (updated daily); the legacy
graphics/ path froze in Feb 2024 — a staleness gate refuses any source whose
newest row is older than MAX_STALE_DAYS rather than render old data as new.

Outputs (PNG, dark house style):
    subseasonal/out/mjo_phase.png      — RMM1/RMM2 phase diagram, unit circle,
                                         8 WH04 octants, 40-day dated track
    subseasonal/out/mjo_amplitude.png  — amplitude timeseries (last 180 days)
    subseasonal/out/mjo_meta.json      — as-of / current phase / amplitude

Usage:  python generate_mjo_rmm.py [--days 40] [--out subseasonal/out]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent

RMM_URLS = [
    # updated daily (verified 2026-07-12); the graphics/ copy froze 2024-02
    "https://www.bom.gov.au/clim_data/IDCKGEM000/rmm.74toRealtime.txt",
    "https://www.bom.gov.au/climate/mjo/graphics/rmm.74toRealtime.txt",
]
# NOTE: BoM's WAF 403s user agents containing a "+https://..." token; the
# plain parenthesized domain passes. Keep this exact shape.
UA = ("Mozilla/5.0 (X11; Linux x86_64) TAT-subseasonal/1.0 "
      "(triple-a-tropics.com)")
MAX_STALE_DAYS = 10          # refuse a source whose newest row is older

# house tokens (generate_subsurface_plots.py conventions)
BG_COLOR = "#07101c"
PANEL_COLOR = "#0a1324"
TEXT_COLOR = "#e5edf6"
MUTED_COLOR = "#8ea2bd"
ACCENT = "#49b6c8"
GRID = "#22304a"
WATERMARK = "@WeathermanAAA_"


def fetch_rmm(cache: Path) -> tuple[list[dict], str]:
    """Return (rows, source_url). Daily cache; staleness-gated per source."""
    import requests
    cache.parent.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    if cache.exists():
        try:
            doc = json.loads(cache.read_text())
            if doc.get("fetched") == today:
                return doc["rows"], doc["source"]
        except Exception:
            pass
    last_err: Exception | None = None
    for url in RMM_URLS:
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=(15, 120))
            r.raise_for_status()
            rows = parse_rmm(r.text)
            if not rows:
                raise ValueError("no parseable rows")
            newest = dt.date(*rows[-1]["ymd"])
            age = (dt.date.today() - newest).days
            if age > MAX_STALE_DAYS:
                raise ValueError(f"stale source: newest row {newest} ({age} d old)")
            cache.write_text(json.dumps(
                {"fetched": today, "source": url, "rows": rows}))
            return rows, url
        except Exception as e:  # noqa: BLE001 - try the next source
            print(f"RMM source failed ({url}): {e}")
            last_err = e
    # every source failed: fall back to the cache regardless of its date so
    # the page keeps its last honest render (the as-of line shows the age)
    if cache.exists():
        doc = json.loads(cache.read_text())
        print("using cached RMM data from", doc.get("fetched"))
        return doc["rows"], doc["source"]
    raise RuntimeError(f"no RMM source available: {last_err}")


def parse_rmm(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines()[2:]:
        parts = line.split()
        if len(parts) < 7:
            continue
        try:
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            r1, r2 = float(parts[3]), float(parts[4])
            ph, amp = int(float(parts[5])), float(parts[6])
        except ValueError:
            continue
        if abs(r1) > 100 or abs(r2) > 100 or amp > 100 or ph == 999:
            continue                       # 1.E36 / 999 missing markers
        rows.append({"ymd": [y, m, d], "rmm1": r1, "rmm2": r2,
                     "phase": ph, "amp": amp})
    return rows


# WH04 phase-space geometry: x=RMM1, y=RMM2; phases 1..8 counterclockwise
# (eastward propagation = CCW), phase 5 opening at the +RMM1 axis.
REGIONS = [
    ("Indian\nOcean", 270, 2.0),          # bottom  (phases 2-3)
    ("Maritime\nContinent", 0, 2.0),      # right   (phases 4-5)
    ("Western\nPacific", 90, 2.0),        # top     (phases 6-7)
    ("West. Hem.\n& Africa", 180, 2.0),   # left    (phases 8-1)
]
PHASE_ANGLE = {1: 202.5, 2: 247.5, 3: 292.5, 4: 337.5,
               5: 22.5, 6: 67.5, 7: 112.5, 8: 157.5}


def _style_axes(ax):
    ax.set_facecolor(PANEL_COLOR)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(colors=MUTED_COLOR, labelsize=9)


FC_COLOR = "#ffb83a"        # ensemble mean (amber; obs track stays teal)
FC_MEMBER = "#8ea2bd"       # member spaghetti (muted)


def render_phase(rows: list[dict], days: int, out: Path,
                 now: dt.date, fc=None) -> dict:
    track = rows[-days:]
    # 1.5x canvas at constant dpi/fonts: the /subseasonal/ page went
    # full-bleed, so the render must carry ~2x the pixels at its new
    # display width. Same point sizes = same physical text on screen.
    fig, ax = plt.subplots(figsize=(12.9, 12.9), facecolor=BG_COLOR)
    _style_axes(ax)
    lim = 4.0
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.set_aspect("equal")
    ax.set_xlabel("RMM1", color=MUTED_COLOR, fontsize=10)
    ax.set_ylabel("RMM2", color=MUTED_COLOR, fontsize=10)

    # octant boundaries (axes + diagonals), outside the unit circle only
    for ang in range(0, 360, 45):
        a = math.radians(ang)
        ax.plot([math.cos(a), lim * 1.42 * math.cos(a)],
                [math.sin(a), lim * 1.42 * math.sin(a)],
                color=GRID, lw=0.8, zorder=1)
    circ = plt.Circle((0, 0), 1.0, fill=True, facecolor=BG_COLOR,
                      edgecolor=GRID, lw=1.0, zorder=2)
    ax.add_patch(circ)
    ax.text(0, 0, "weak\nMJO", ha="center", va="center",
            color=MUTED_COLOR, fontsize=9, zorder=3)

    # phase numbers in each octant + basin labels on the rim
    for ph, ang in PHASE_ANGLE.items():
        a = math.radians(ang)
        ax.text(3.55 * math.cos(a), 3.55 * math.sin(a), str(ph),
                color=MUTED_COLOR, fontsize=13, fontweight="bold",
                ha="center", va="center", zorder=3)
    for label, ang, r in REGIONS:
        a = math.radians(ang)
        kw = dict(ha="center", va="center", color=MUTED_COLOR, fontsize=9)
        if ang == 0:
            kw.update(rotation=270)
        elif ang == 180:
            kw.update(rotation=90)
        ax.text((lim - 0.35) * math.cos(a), (lim - 0.35) * math.sin(a),
                label.replace("\n", " "), **kw, zorder=3)

    # trailing track: alpha ramps toward today (restrained fade, no effects)
    xs = [r["rmm1"] for r in track]
    ys = [r["rmm2"] for r in track]
    n = len(track)
    for i in range(1, n):
        alpha = 0.28 + 0.72 * (i / (n - 1))
        ax.plot(xs[i - 1:i + 1], ys[i - 1:i + 1], color=ACCENT,
                lw=1.8, alpha=alpha, zorder=4, solid_capstyle="round")
    ax.scatter(xs[:-1], ys[:-1], s=12, color=ACCENT, alpha=0.75, zorder=5)
    ax.scatter([xs[-1]], [ys[-1]], s=52, color=ACCENT,
               edgecolor=TEXT_COLOR, linewidth=1.2, zorder=6)

    # date labels on the 5/10/15/... of each month + the endpoint
    # (selective direct labels, never every point)
    for i, r in enumerate(track):
        d = dt.date(*r["ymd"])
        if i == n - 1 or d.day % 5 == 0:
            ax.annotate(d.strftime("%-d %b") if i == n - 1 else d.strftime("%-d"),
                        (xs[i], ys[i]), textcoords="offset points",
                        xytext=(6, 5), fontsize=8 if i < n - 1 else 9.5,
                        fontweight="bold" if i == n - 1 else "normal",
                        color=TEXT_COLOR if i == n - 1 else MUTED_COLOR,
                        zorder=7)

    latest = track[-1]

    # GEFS-member forecast layer: spaghetti thin, ensemble mean bold,
    # both joined onto the observed endpoint; dated labels on the mean
    if fc:
        ox, oy = xs[-1], ys[-1]
        for m, (fd, p1, p2) in fc["members"].items():
            ax.plot([ox] + list(p1), [oy] + list(p2), color=FC_MEMBER,
                    lw=0.9, alpha=0.30, zorder=4)
        m1, m2 = fc["mean"]
        ax.plot([ox] + list(m1), [oy] + list(m2), color=FC_COLOR,
                lw=2.4, zorder=6, solid_capstyle="round")
        for k, d in enumerate(fc["dates"]):
            if d.day % 5 == 0 or k == len(fc["dates"]) - 1:
                ax.annotate(d.strftime("%-d %b") if k == len(fc["dates"]) - 1
                            else d.strftime("%-d"),
                            (m1[k], m2[k]), textcoords="offset points",
                            xytext=(6, -9), fontsize=8, color=FC_COLOR,
                            fontweight="bold" if k == len(fc["dates"]) - 1
                            else "normal", zorder=7)
        ax.scatter([m1[-1]], [m2[-1]], s=42, color=FC_COLOR,
                   edgecolor=TEXT_COLOR, linewidth=1.0, zorder=7)

    d0, d1 = dt.date(*track[0]["ymd"]), dt.date(*latest["ymd"])
    title = f"MJO phase space (RMM)  ·  {d0:%d %b} – {d1:%d %b %Y}"
    if fc:
        title += f"  + GEFS to {fc['dates'][-1]:%d %b}"
    ax.set_title(title, color=TEXT_COLOR, fontsize=13, fontweight="bold",
                 pad=26)
    # 1.012 (not 1.022): axes-fraction offsets grew with the 1.5x canvas;
    # this keeps the same physical clearance under the pad=26 title.
    sub = (f"latest: phase {latest['phase']} · amplitude "
           f"{latest['amp']:.2f}"
           f" · eastward propagation is counterclockwise")
    if fc:
        sub += (f" · forecast: GEFS members (thin) + ensemble mean "
                f"(amber, smoothed), init {fc['init']:%Y-%m-%d} 00Z, "
                f"~16-day limit")
    ax.text(0.0, 1.012, sub,
            transform=ax.transAxes, color=MUTED_COLOR, fontsize=9)
    ax.text(0.995, 0.012, WATERMARK, transform=ax.transAxes, ha="right",
            color=MUTED_COLOR, alpha=0.7, fontsize=9)
    ax.text(0.005, 0.012,
            "RMM index: Australian Bureau of Meteorology (Wheeler & Hendon 2004)",
            transform=ax.transAxes, color=MUTED_COLOR, alpha=0.9, fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "mjo_phase.png", dpi=150, facecolor=BG_COLOR)
    plt.close(fig)
    return {"phase": latest["phase"], "amp": latest["amp"],
            "as_of": d1.isoformat()}


def render_amplitude(rows: list[dict], out: Path, days: int = 180,
                     fc=None) -> None:
    seg = rows[-days:]
    d = [dt.date(*r["ymd"]) for r in seg]
    amp = np.array([r["amp"] for r in seg])
    # 1.5x canvas for the full-bleed page (see render_phase note)
    fig, ax = plt.subplots(figsize=(15.75, 5.1), facecolor=BG_COLOR)
    _style_axes(ax)
    ax.grid(color=GRID, lw=0.5, alpha=0.6)
    ax.axhline(1.0, color=MUTED_COLOR, lw=1.0, ls=(0, (4, 3)), alpha=0.8)
    ax.text(d[0], 1.04, "amplitude 1: significant MJO", color=MUTED_COLOR,
            fontsize=8, va="bottom")
    ax.plot(d, amp, color=ACCENT, lw=1.7)
    ax.fill_between(d, 0, amp, color=ACCENT, alpha=0.12, lw=0)
    x_end, y_top = d[-1], float(amp.max())

    # GEFS forecast extension. Amplitude is >=0, so a raw member min/max
    # envelope reads as a lopsided near-black blob; instead show smooth,
    # translucent MEMBER-AMPLITUDE percentile bands (10-90 outer, 25-75
    # inner) with the MEDIAN member as the primary line. Crucially this
    # separates two different quantities the reader must not conflate:
    #   - member amplitudes (bands + median): each member's own |RMM|;
    #     these mostly stay strong even when the event survives.
    #   - the ensemble-mean VECTOR amplitude (thin dashed): |mean(RMM)|,
    #     which shrinks as members disperse in PHASE even if none weaken.
    # A drooping dashed line above a still-strong band = phase dispersion,
    # not the MJO dying. Labeled as such so the mean is not misread.
    if fc:
        fd = fc["dates"]
        m1, m2 = fc["mean"]
        vec_mean_amp = np.sqrt(np.array(m1) ** 2 + np.array(m2) ** 2)
        # per-forecast-day member amplitude distribution
        p10 = np.full(len(fd), np.nan); p25 = np.full(len(fd), np.nan)
        p50 = np.full(len(fd), np.nan); p75 = np.full(len(fd), np.nan)
        p90 = np.full(len(fd), np.nan)
        for k in range(len(fd)):
            vals = [np.hypot(p1[k], p2[k])
                    for (_mfd, p1, p2) in fc["members"].values()
                    if k < len(p1) and np.isfinite(p1[k]) and np.isfinite(p2[k])]
            if len(vals) >= 5:
                p10[k], p25[k], p50[k], p75[k], p90[k] = np.percentile(
                    vals, [10, 25, 50, 75, 90])
        ok = np.isfinite(p50)
        if ok.any():
            fda = np.array(fd)[ok]
            # join bands + median onto the last observed point for continuity
            jx = np.concatenate(([d[-1]], fda))
            def _j(arr):
                return np.concatenate(([amp[-1]], arr[ok]))
            ax.fill_between(jx, _j(p10), _j(p90), color=FC_COLOR,
                            alpha=0.11, lw=0)
            ax.fill_between(jx, _j(p25), _j(p75), color=FC_COLOR,
                            alpha=0.20, lw=0)
            ax.plot(jx, _j(p50), color=FC_COLOR, lw=2.0)      # median member
            ax.plot(jx, np.concatenate(([amp[-1]], vec_mean_amp[ok])),
                    color=FC_COLOR, lw=1.3, ls=(0, (5, 3)),
                    alpha=0.85)                               # vector mean
            ax.axvline(d[-1], color=TEXT_COLOR, lw=1.0, ls=(0, (5, 3)),
                       alpha=0.8)
            ax.text(d[-1], 0.06,
                    f"  GEFS · init {fc['init']:%Y-%m-%d} 00Z · median "
                    "member (line) + 25-75 / 10-90 member bands · dashed = "
                    "vector-mean |RMM| (drops with phase spread, not "
                    "weakening) · ~16-day limit", color=FC_COLOR,
                    fontsize=7.8, va="bottom")
            x_end = fd[-1]
            y_top = max(y_top, float(np.nanmax(p90)))

    ax.set_ylim(0, max(2.6, y_top + 0.3))
    ax.set_xlim(d[0], x_end)
    ax.set_ylabel("RMM amplitude", color=MUTED_COLOR, fontsize=9.5)
    title = f"MJO amplitude · last {days} days"
    if fc:
        title += f" + GEFS to {fc['dates'][-1]:%b %-d}"
    ax.set_title(title, color=TEXT_COLOR,
                 fontsize=12, fontweight="bold", loc="left")
    ax.text(1.0, 1.02, WATERMARK, transform=ax.transAxes, ha="right",
            color=MUTED_COLOR, alpha=0.7, fontsize=8.5)
    fig.tight_layout()
    fig.savefig(out / "mjo_amplitude.png", dpi=150, facecolor=BG_COLOR)
    plt.close(fig)


# --------------------------------------------------- GEFS RMM forecast
# (Phase 3 Group B item 4 — every constant primary-source-verified; see
# subseasonal/rmm_wh04.py's provenance header.)

def _to_rmm_lons(series_lons, series):
    """(t, nlon) on arbitrary lons -> (t, 144) periodic interp."""
    import sys as _sys
    _sys.path.insert(0, str(HERE / "subseasonal"))
    import rmm_wh04
    out = np.empty((series.shape[0], 144))
    ext_l = np.concatenate([series_lons, series_lons[:1] + 360.0])
    for t in range(series.shape[0]):
        ext = np.concatenate([series[t], series[t][:1]])
        out[t] = np.interp(rmm_wh04.RMM_LONS, ext_l, ext)
    return out


def build_forecast(out: Path, u_archive_path: Path, rows_obs: list[dict],
                   fc_days: int = 16, workers: int = 6):
    """GEFS-member RMM projection -> {init, dates, members{m:(pc1,pc2)},
    mean(pc1,pc2), seam, olr_bridge_days, anchored} or None on failure.

    Obs side: OLR anomalies from the PSL CDR (via the Hovmöller module's
    fetch — same LTM 3-harmonic seasonal cycle), winds from the restored
    GFS analysis archive vs the ERA5 monthly climatology. Forecast side:
    per-member GEFS fields (subseasonal/gefs_mean.fetch_members_rmm).
    Each member's series = obs anomalies + (OLR-lag bridge) + its own
    forecast days, then the WH04 steps 2-6 run on the concat exactly per
    Gottschalck et al. 2010 (the trailing 120-day mean at forecast day N
    mixes analyses + the first N forecast days). Projection is done with
    OUR obs pipeline; the seam between our obs-day PCs and BoM's official
    RMM is measured against the newest common day and, when it exceeds
    0.15, the whole forecast is anchored (constant offset) onto the BoM
    endpoint so the plotted track joins the official one — the offset and
    choice are disclosed in the meta."""
    import sys as _sys
    _sys.path.insert(0, str(HERE / "subseasonal"))
    import importlib.util
    import gefs_mean
    import rmm_wh04

    spec = importlib.util.spec_from_file_location(
        "gh_for_rmm", HERE / "generate_hovmollers.py")
    gh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gh)

    # ---- obs winds (restored archive)
    arch = gh.load_u_archive(u_archive_path)
    if not arch:
        print("rmm fc: no u archive — forecast skipped")
        return None
    times, levels, lats_u, lons_u, u, _v, _n = arch
    import xarray as xr
    uclim = xr.open_dataset(gh.U_CLIMO_NC)
    full = [times[0] + dt.timedelta(days=i)
            for i in range((times[-1] - times[0]).days + 1)]
    idx = {d: i for i, d in enumerate(times)}
    ua = {}
    for li, lev in enumerate(gh.U_LEVELS):
        a = np.full((len(full), lats_u.size, lons_u.size), np.nan)
        for di, d in enumerate(full):
            i = idx.get(d)
            if i is not None:
                a[di] = u[i, li] - gh.monthly_climo_for(
                    uclim, "u", d, float(lev), lats_u, lons_u)
        ua[int(lev)] = _to_rmm_lons(lons_u,
                                    gh.band_mean(a, lats_u, -15, 15))

    # ---- obs OLR (PSL CDR anomalies; lags realtime by ~3-5 days)
    dates_o, lats_o, lons_o, anom_o, _nb = gh.fetch_olr()
    olr_bm = _to_rmm_lons(lons_o, gh.band_mean(anom_o, lats_o, -15, 15))
    o_idx = {d: i for i, d in enumerate(dates_o)}

    # ---- member forecasts
    init = gefs_mean.newest_complete_init()
    members = gefs_mean.fetch_members_rmm(init, fc_days, workers)
    if not members:
        print("rmm fc: no GEFS members reachable — forecast skipped")
        return None
    print(f"rmm fc: {len(members)} member(s), init {init:%Y-%m-%d} 00Z")

    # unified obs axis: from OLR/wind overlap start to the wind end
    obs_days = [d for d in full if d in o_idx or d > dates_o[-1]]
    obs_days = [d for d in obs_days if d <= times[-1]]
    olr_end = dates_o[-1]
    bridge_days = max(0, (times[-1] - olr_end).days)
    eofs = rmm_wh04.load_eofs()

    def obs_matrix(key):
        if key == "olr":
            return np.stack([olr_bm[o_idx[d]] if d in o_idx
                             else np.full(144, np.nan) for d in obs_days])
        src = ua[key]
        return np.stack([src[full.index(d)] for d in obs_days])

    olr_obs = obs_matrix("olr")
    u850_obs = obs_matrix(850)
    u200_obs = obs_matrix(200)

    # per-DATE climatology rows (identical seasonal cycles to the obs
    # side), precomputed once — the member loop is pure arithmetic
    ltm_rmm = gh.fetch_olr_ltm_rmm()                     # (365, 144)
    fc_date_set = sorted({d for v in members.values() for d in v[0]})
    uclim_rmm = {}
    for d in fc_date_set:
        per = {}
        for lev in (850.0, 200.0):
            c = gh.monthly_climo_for(uclim, "u", d, lev, lats_u, lons_u)
            per[int(lev)] = _to_rmm_lons(
                lons_u, gh.band_mean(c[None], lats_u, -15, 15))[0]
        uclim_rmm[d] = per

    def olr_ltm_at(d):
        return ltm_rmm[min(d.timetuple().tm_yday, 365) - 1]

    out_members, obs_pc = {}, None
    for m, (fdates, folr_raw, fu850, fu200) in sorted(members.items()):
        folr = np.stack([folr_raw[k] - olr_ltm_at(fdates[k])
                         for k in range(len(fdates))])
        fu850a = np.stack([fu850[k] - uclim_rmm[fdates[k]][850]
                           for k in range(len(fdates))])
        fu200a = np.stack([fu200[k] - uclim_rmm[fdates[k]][200]
                           for k in range(len(fdates))])

        # OLR-lag bridge: linear from the last CDR day to member day 1
        olr_m = olr_obs.copy()
        if bridge_days:
            last = olr_bm[o_idx[olr_end]]
            first_fc = folr[0]
            for bi in range(bridge_days):
                w = (bi + 1) / (bridge_days + 1)
                pos = obs_days.index(olr_end) + 1 + bi
                if pos < olr_m.shape[0]:
                    olr_m[pos] = (1 - w) * last + w * first_fc

        cat = lambda a, b: np.vstack([a, b])  # noqa: E731
        pc1, pc2, _amp, _ph = rmm_wh04.rmm_series(
            cat(olr_m, folr), cat(u850_obs, fu850a),
            cat(u200_obs, fu200a), eofs=eofs)
        nfc = len(fdates)
        out_members[m] = (fdates, pc1[-nfc:], pc2[-nfc:])
        if obs_pc is None:
            obs_pc = (obs_days, pc1[:-nfc], pc2[:-nfc])

    uclim.close()

    # ens mean over members per forecast day
    all_dates = max((v[0] for v in out_members.values()), key=len)
    mean1, mean2 = [], []
    for k, d in enumerate(all_dates):
        xs = [v[1][k] for v in out_members.values() if len(v[0]) > k]
        ys = [v[2][k] for v in out_members.values() if len(v[0]) > k]
        mean1.append(float(np.mean(xs)))
        mean2.append(float(np.mean(ys)))

    # seam vs BoM on the newest PURE-obs day (bridge days are excluded —
    # their OLR is interpolated toward one member) + optional anchoring
    bom = {tuple(r["ymd"]): r for r in rows_obs}
    seam = (0.0, 0.0)
    for k in range(len(obs_pc[0]) - 1, -1, -1):
        d = obs_pc[0][k]
        key = (d.year, d.month, d.day)
        if d <= olr_end and key in bom and np.isfinite(obs_pc[1][k]):
            seam = (float(bom[key]["rmm1"] - obs_pc[1][k]),
                    float(bom[key]["rmm2"] - obs_pc[2][k]))
            break
    anchored = bool(max(abs(seam[0]), abs(seam[1])) > 0.15)
    if anchored:
        for m in out_members:
            fd, p1, p2 = out_members[m]
            out_members[m] = (fd, p1 + seam[0], p2 + seam[1])
        mean1 = [x + seam[0] for x in mean1]
        mean2 = [y + seam[1] for y in mean2]
    print(f"rmm fc: seam vs BoM ({seam[0]:+.2f},{seam[1]:+.2f}) · "
          f"anchored={anchored} · olr bridge {bridge_days} d")

    return {"init": init, "dates": all_dates,
            "members": out_members, "mean": (mean1, mean2),
            "seam": seam, "anchored": anchored,
            "olr_bridge_days": bridge_days}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=40)
    p.add_argument("--out", default=str(HERE / "subseasonal" / "out"))
    p.add_argument("--forecast", action="store_true",
                   help="add the GEFS-member RMM forecast layer")
    p.add_argument("--u-archive", default=None,
                   help="restored GFS wind archive (forecast obs side)")
    p.add_argument("--fc-days", type=int, default=16)
    p.add_argument("--fc-workers", type=int, default=6)
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows, source = fetch_rmm(out / "_rmm_cache.json")
    print(f"RMM rows: {len(rows)} · newest {rows[-1]['ymd']} · {source}")

    fc = None
    if args.forecast:
        try:
            u_arch = Path(args.u_archive) if args.u_archive else \
                out / "u_daily_archive.nc"
            fc = build_forecast(out, u_arch, rows,
                                args.fc_days, args.fc_workers)
        except Exception as e:  # noqa: BLE001 — forecast is additive
            print(f"rmm forecast failed ({type(e).__name__}: {e}) — "
                  f"observed-only renders")
            fc = None

    meta = render_phase(rows, args.days, out, dt.date.today(), fc=fc)
    render_amplitude(rows, out, fc=fc)
    meta.update({"generated_utc":
                 dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "source": "Australian Bureau of Meteorology RMM (WH04)"})
    if fc:
        meta["forecast"] = {
            "init": fc["init"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": fc["dates"][-1].isoformat(),
            "members": len(fc["members"]),
            "seam": [round(x, 3) for x in fc["seam"]],
            "anchored": fc["anchored"],
            "olr_bridge_days": fc["olr_bridge_days"],
            "source": "NOAA NCEP GEFS (members + ensemble mean), "
                      "WH04 EOF projection"}
    (out / "mjo_meta.json").write_text(json.dumps(meta))
    print("wrote", out / "mjo_phase.png", "+ amplitude + meta")


if __name__ == "__main__":
    main()
