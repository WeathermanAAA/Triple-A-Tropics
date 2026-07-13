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


def render_phase(rows: list[dict], days: int, out: Path,
                 now: dt.date) -> dict:
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
    d0, d1 = dt.date(*track[0]["ymd"]), dt.date(*latest["ymd"])
    ax.set_title(f"MJO phase space (RMM)  ·  {d0:%d %b} – {d1:%d %b %Y}",
                 color=TEXT_COLOR, fontsize=13, fontweight="bold", pad=26)
    # 1.012 (not 1.022): axes-fraction offsets grew with the 1.5x canvas;
    # this keeps the same physical clearance under the pad=26 title.
    ax.text(0.0, 1.012,
            f"latest: phase {latest['phase']} · amplitude {latest['amp']:.2f}"
            f" · eastward propagation is counterclockwise",
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


def render_amplitude(rows: list[dict], out: Path, days: int = 180) -> None:
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
    ax.set_ylim(0, max(2.6, float(amp.max()) + 0.3))
    ax.set_xlim(d[0], d[-1])
    ax.set_ylabel("RMM amplitude", color=MUTED_COLOR, fontsize=9.5)
    ax.set_title(f"MJO amplitude · last {days} days", color=TEXT_COLOR,
                 fontsize=12, fontweight="bold", loc="left")
    ax.text(1.0, 1.02, WATERMARK, transform=ax.transAxes, ha="right",
            color=MUTED_COLOR, alpha=0.7, fontsize=8.5)
    fig.tight_layout()
    fig.savefig(out / "mjo_amplitude.png", dpi=150, facecolor=BG_COLOR)
    plt.close(fig)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=40)
    p.add_argument("--out", default=str(HERE / "subseasonal" / "out"))
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows, source = fetch_rmm(out / "_rmm_cache.json")
    print(f"RMM rows: {len(rows)} · newest {rows[-1]['ymd']} · {source}")
    meta = render_phase(rows, args.days, out, dt.date.today())
    render_amplitude(rows, out)
    meta.update({"generated_utc":
                 dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "source": "Australian Bureau of Meteorology RMM (WH04)"})
    (out / "mjo_meta.json").write_text(json.dumps(meta))
    print("wrote", out / "mjo_phase.png", "+ amplitude + meta")


if __name__ == "__main__":
    main()
