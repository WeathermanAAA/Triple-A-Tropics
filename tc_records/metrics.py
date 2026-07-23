"""Per-storm and per-season metric computation.

Input: the canonical fix frame from sources.assemble_basin (one basin).
Output: a per-storm DataFrame + per-season aggregates + the auxiliary
structures the visual pages need (pace matrices, gantt segments, concurrency
episodes).

Rule recap (enforced here, not in the boards):
- Σ metrics (ACE, PDI, duration, distance) and rate metrics (6/12/24-h
  windows, forward speed) use SYNOPTIC fixes only (syn == True).
- Intensity extremes (peak wind, min pressure) use ALL tropical/subtropical
  fixes including HURDAT2 special rows (landfall/peak entries) — an extreme
  is an observation, not a sum; the boards disclose this.
- Everything is gated on trop (tropical or subtropical); EX/LO/DB never count.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

import ace_core as ac

from . import NAMED_KT, HURR_KT, MAJOR_KT, MAX_PLAUSIBLE_SPEED_KT

EARTH_RADIUS_KM = 6371.0088
KM_PER_NM = 1.852


def _haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1
    dl = np.radians(lon2 - lon1)
    a = np.sin(dp / 2.0) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _window_extreme(times: np.ndarray, values: np.ndarray, hours: int,
                    sign: float) -> tuple[float, object]:
    """Max of sign*(v[t] - v[t+hours]) over exact-window synoptic pairs.
    sign=+1 → biggest fall (deepening); sign=-1 → biggest rise.
    Returns (extreme, end_time) or (nan, None)."""
    if len(times) < 2:
        return math.nan, None
    idx = {t: i for i, t in enumerate(times)}
    delta = np.timedelta64(hours, "h")
    best, best_t = math.nan, None
    for i, t in enumerate(times):
        j = idx.get(t + delta)
        if j is None:
            continue
        d = sign * (values[i] - values[j])
        if not math.isnan(d) and (math.isnan(best) or d > best):
            best, best_t = d, times[j]
    return best, best_t


def compute_storms(fixes: pd.DataFrame) -> pd.DataFrame:
    """One row per SID with every per-storm metric the boards need."""
    out = []
    for sid, g in fixes.groupby("sid", sort=False):
        g = g.sort_values("time")
        trop = g[g["trop"]]
        if trop.empty:
            continue
        syn = trop[trop["syn"]]
        syn_ts = syn[syn["wind"] >= NAMED_KT]

        name = next((n for n in g["name"] if isinstance(n, str) and n), "")
        season = int(g["season"].iloc[0])
        atcf = next((a for a in g["atcf"] if isinstance(a, str) and a), "")

        # --- Σ metrics (synoptic, ≥34 kt, tropical/subtropical) ---
        w = syn_ts["wind"].to_numpy(dtype=float)
        w = w[~np.isnan(w)]
        ace = round(float(np.sum(w * w) / 1e4), 3)
        pdi = float(np.sum(w ** 3) / 1e6)

        # --- intensity extremes (all tropical fixes, specials included) ---
        peak_wind = float(trop["wind"].max()) if trop["wind"].notna().any() \
            else math.nan
        if not math.isnan(peak_wind):
            pk = trop[trop["wind"] == peak_wind].iloc[0]
            peak_time, peak_lat, peak_lon = pk["time"], pk["lat"], pk["lon"]
        else:
            peak_time = peak_lat = peak_lon = None
        has_pres = trop["pres"].notna().any()
        if has_pres:
            min_pres = float(trop["pres"].min())
            pr = trop[trop["pres"] == min_pres].iloc[0]
            min_pres_time = pr["time"]
        else:
            min_pres, min_pres_time = math.nan, None

        # --- rate metrics (synoptic, exact windows) ---
        t_syn = syn["time"].to_numpy(dtype="datetime64[ns]")
        p_syn = syn["pres"].to_numpy(dtype=float)
        w_syn = syn["wind"].to_numpy(dtype=float)
        deep = {}
        for h in (6, 12, 24):
            deep[h] = _window_extreme(t_syn, p_syn, h, +1.0)
        rise24, rise24_t = _window_extreme(t_syn, w_syn, 24, -1.0)
        # _window_extreme(sign=-1) maximizes v[t+24]-v[t] → biggest rise;
        # fall is the mirror.
        fall24, fall24_t = _window_extreme(t_syn, w_syn, 24, +1.0)

        # --- durations (synoptic fix counts × 0.25 d) ---
        dur_tc = 0.25 * len(syn)
        dur_hu = 0.25 * int((syn["wind"] >= HURR_KT).sum())
        dur_major = 0.25 * int((syn["wind"] >= MAJOR_KT).sum())

        # --- track distance + forward speed (consecutive synoptic fixes) ---
        dist_km = math.nan
        max_speed, max_speed_time = math.nan, None
        if len(syn) >= 2:
            lat = syn["lat"].to_numpy(dtype=float)
            lon = syn["lon"].to_numpy(dtype=float)
            tt = syn["time"].to_numpy(dtype="datetime64[ns]")
            gap_h = np.diff(tt) / np.timedelta64(1, "h")
            # Dateline-safe: only Δlon enters the haversine, wrapped to
            # [-180, 180] so a 179.8 → -179.9 hop is a 0.3° step.
            dlon = (np.diff(lon) + 180.0) % 360.0 - 180.0
            seg = _haversine_km(lat[:-1], np.zeros(len(lat) - 1),
                                lat[1:], dlon)
            ok = (gap_h > 0) & (gap_h <= 12) & ~np.isnan(seg)
            dist_km = float(np.sum(seg[ok])) if ok.any() else math.nan
            six = ok & (gap_h == 6)
            both_ts = (syn["wind"].to_numpy(dtype=float)[:-1] >= NAMED_KT) \
                & (syn["wind"].to_numpy(dtype=float)[1:] >= NAMED_KT)
            spd = (seg / KM_PER_NM) / gap_h
            cand = six & both_ts & (spd <= MAX_PLAUSIBLE_SPEED_KT)
            if cand.any():
                k = int(np.nanargmax(np.where(cand, spd, np.nan)))
                max_speed = float(spd[k])
                max_speed_time = tt[k + 1]

        # --- timing anchors (synoptic) ---
        form = syn_ts["time"].min() if len(syn_ts) else None
        first_hu = syn[syn["wind"] >= HURR_KT]["time"].min() \
            if (syn["wind"] >= HURR_KT).any() else None
        first_major = syn[syn["wind"] >= MAJOR_KT]["time"].min() \
            if (syn["wind"] >= MAJOR_KT).any() else None
        last_trop = syn["time"].max() if len(syn) else None

        # --- geography ---
        ts_fix = trop[trop["wind"] >= NAMED_KT]
        hu_fix = trop[trop["wind"] >= HURR_KT]
        gen_lat = gen_lon = math.nan
        if form is not None and len(syn_ts):
            g0 = syn_ts.iloc[0]
            gen_lat, gen_lon = float(g0["lat"]), float(g0["lon"])
        min_abs_lat_ts = float(ts_fix["lat"].abs().min()) if len(ts_fix) \
            else math.nan
        min_abs_lat_hu = float(hu_fix["lat"].abs().min()) if len(hu_fix) \
            else math.nan
        max_lat_ts = float(ts_fix["lat"].max()) if len(ts_fix) else math.nan

        out.append({
            "sid": sid, "atcf": atcf, "name": name, "season": season,
            "ace": ace, "pdi": round(pdi, 2),
            "peak_wind": peak_wind, "peak_time": peak_time,
            "peak_lat": peak_lat, "peak_lon": peak_lon,
            "min_pres": min_pres, "min_pres_time": min_pres_time,
            "deep6": deep[6][0], "deep6_time": deep[6][1],
            "deep12": deep[12][0], "deep12_time": deep[12][1],
            "deep24": deep[24][0], "deep24_time": deep[24][1],
            "rise24": rise24, "rise24_time": rise24_t,
            "fall24": fall24, "fall24_time": fall24_t,
            "dur_tc": dur_tc, "dur_hu": dur_hu, "dur_major": dur_major,
            "dist_km": dist_km,
            "max_speed": max_speed, "max_speed_time": max_speed_time,
            "formation": form, "first_hu": first_hu,
            "first_major": first_major, "last_trop": last_trop,
            "gen_lat": gen_lat, "gen_lon": gen_lon,
            "min_abs_lat_ts": min_abs_lat_ts,
            "min_abs_lat_hu": min_abs_lat_hu,
            "max_lat_ts": max_lat_ts,
            "named": form is not None,
            "hu": first_hu is not None,
            "major": first_major is not None,
            "c5": (not math.isnan(peak_wind)) and peak_wind >= 137.0,
            "ri": (not math.isnan(rise24)) and rise24 >= 30.0,
        })
    return pd.DataFrame(out)


def season_table(storms: pd.DataFrame) -> pd.DataFrame:
    """Per-season aggregates over named storms (genesis-season attributed)."""
    named = storms[storms["named"]]
    g = named.groupby("season")
    tbl = pd.DataFrame({
        "named": g.size(),
        "hu": g["hu"].sum().astype(int),
        "major": g["major"].sum().astype(int),
        "c5": g["c5"].sum().astype(int),
        "ace": g["ace"].sum().round(1),
        "pdi": g["pdi"].sum().round(1),
        "ri_storms": g["ri"].sum().astype(int),
    })
    tbl.index.name = "season"
    return tbl.reset_index()


def concurrency(fixes: pd.DataFrame, min_wind: float) -> pd.DataFrame:
    """Per synoptic timestamp: number of distinct active storms at ≥min_wind
    (tropical/subtropical). Returns time, count, names, season columns."""
    f = fixes[fixes["syn"] & fixes["trop"] & (fixes["wind"] >= min_wind)]
    if f.empty:
        return pd.DataFrame(columns=["time", "count", "names", "season"])
    rows = []
    for t, g in f.groupby("time"):
        sids = g.drop_duplicates("sid")
        rows.append({
            "time": t, "count": len(sids),
            "names": [n if n else s for n, s in
                      zip(sids["name"], sids["atcf"])],
            "season": int(sids["season"].max()),
        })
    return pd.DataFrame(rows).sort_values("time").reset_index(drop=True)


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _pace_idx(ts: pd.Timestamp, season: int) -> int:
    """LEAP-ALIGNED day index (1..366) measured from the GENESIS season's
    Jan 1, so a Jan-crosser (Zeta 2005 → Jan 2006) keeps extending its own
    season's curve. Non-leap seasons shift Mar 1 onward by +1 so a given
    calendar date always lands on the same slot across seasons (the pace
    page's month axis assumes the leap calendar). Clamped to 366."""
    d = (ts - pd.Timestamp(year=season, month=1, day=1)).days + 1
    if not _is_leap(season) and d >= 60:
        d += 1
    return max(1, min(366, int(d)))


def pace_matrices(storms: pd.DataFrame, fixes: pd.DataFrame,
                  seasons: list[int]) -> dict:
    """Cumulative named-storm count and cumulative ACE by day-of-year for
    every season → {'count': DataFrame[doy x season], 'ace': same}."""
    doys = np.arange(1, 367)
    count = pd.DataFrame(0.0, index=doys, columns=seasons)
    for _, s in storms[storms["named"]].iterrows():
        if s["season"] not in count.columns or s["formation"] is None:
            continue
        d = _pace_idx(pd.Timestamp(s["formation"]), s["season"])
        count.loc[d, s["season"]] += 1.0
    count = count.cumsum(axis=0)

    ace = pd.DataFrame(0.0, index=doys, columns=seasons)
    f = fixes[fixes["syn"] & fixes["trop"]
              & (fixes["wind"] >= NAMED_KT)].copy()
    f = f.dropna(subset=["wind"])
    if len(f):
        origin = pd.to_datetime(f["season"].astype(int).astype(str)
                                + "-01-01")
        d = (f["time"].dt.normalize() - origin).dt.days + 1
        season = f["season"].astype(int)
        leap = (season % 4 == 0) & ((season % 100 != 0)
                                    | (season % 400 == 0))
        # Leap-align (see _pace_idx): non-leap seasons shift Mar 1+ by +1.
        f["_doy"] = (d + ((~leap) & (d >= 60)).astype(int)).clip(1, 366)
        f["_inc"] = (f["wind"] * f["wind"]) / 1e4
        g = f.groupby(["season", "_doy"])["_inc"].sum()
        for (season, d), inc in g.items():
            if season in ace.columns:
                ace.loc[int(d), season] += inc
    ace = ace.cumsum(axis=0)
    return {"count": count, "ace": ace}


def gantt_seasons(fixes: pd.DataFrame, storms: pd.DataFrame,
                  min_season: int) -> dict:
    """Per-season storm bars for the Gantt page: consecutive synoptic
    tropical fixes grouped into SSHS-class runs (ace_core.sshs_class)."""
    meta = storms.set_index("sid")
    out: dict[int, list[dict]] = {}
    for sid, g in fixes[fixes["syn"] & fixes["trop"]].groupby("sid",
                                                             sort=False):
        if sid not in meta.index:
            continue
        m = meta.loc[sid]
        season = int(m["season"])
        if season < min_season:
            continue
        g = g.sort_values("time")
        segs = []
        six = pd.Timedelta(hours=6)

        def close_run(t0, t1, cls):
            # A fix stands for the 6 h that follow it, so a run's interval
            # extends one fix past its last observation — a single-fix run
            # is a visible 6-h block and back-to-back runs stay contiguous.
            segs.append([t0.isoformat(), (t1 + six).isoformat(), cls])

        cur_cls, cur_t0, cur_t1 = None, None, None
        for t, wnd in zip(g["time"], g["wind"]):
            cls = ac.sshs_class(None if pd.isna(wnd) else float(wnd))
            if cls == cur_cls and cur_t1 is not None \
                    and (t - cur_t1) <= six:
                cur_t1 = t
                continue
            if cur_cls is not None:
                close_run(cur_t0, cur_t1, cur_cls)
            cur_cls, cur_t0, cur_t1 = cls, t, t
        if cur_cls is not None:
            close_run(cur_t0, cur_t1, cur_cls)
        if not segs:
            continue
        name = m["name"] or (ac.designation_label(
            ac.atcf_short_id(m["atcf"]), m["peak_wind"])
            if m["atcf"] else "") or "UNNAMED"
        out.setdefault(season, []).append({
            "sid": sid, "name": name,
            "peak_wind": None if pd.isna(m["peak_wind"])
                         else float(m["peak_wind"]),
            "peak_cls": ac.sshs_class(None if pd.isna(m["peak_wind"])
                                      else float(m["peak_wind"])),
            "ace": float(m["ace"]),
            "t0": segs[0][0], "t1": segs[-1][1],
            "seg": segs,
        })
    for season in out:
        out[season].sort(key=lambda s: s["t0"])
    return out
