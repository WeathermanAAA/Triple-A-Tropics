"""Leaderboard builders — per-storm/per-season metric tables → board dicts.

Every board is a plain dict the frontend renders verbatim:

    {key, page, title, definition, unit, note, since, rows: [...]}

Rows carry raw values plus a preformatted ``disp`` string so every consumer
prints identical numbers. Ranking is competition-style (ties share a rank);
boards cut at TOP_N rows but extend through a tie group (capped at TIE_CAP).

Current-season handling: "most/highest" boards include the running season
(a record is a record the moment it happens); "fewest/lowest" boards exclude
it (an incomplete season would falsely bottom the table) — each such board
says so in its note.
"""

from __future__ import annotations

import math

import pandas as pd

TOP_N = 15
TIE_CAP = 25

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _d(ts) -> str:
    """Compact fix-time display: '2005-10-19 12Z'."""
    if ts is None or (isinstance(ts, float) and math.isnan(ts)) or pd.isna(ts):
        return ""
    ts = pd.Timestamp(ts)
    return f"{ts.strftime('%Y-%m-%d')} {ts.hour:02d}Z"


def _md(ts) -> str:
    """Month-day display for timing boards: 'Jan 3'."""
    ts = pd.Timestamp(ts)
    return f"{MONTHS[ts.month - 1]} {ts.day}"


def _monthday_key(ts):
    ts = pd.Timestamp(ts)
    return (ts.month, ts.day, ts.hour)


def _rank(rows: list[dict], *, reverse: bool) -> list[dict]:
    """Sort by 'value', assign competition ranks, cut at TOP_N + ties."""
    rows = [r for r in rows
            if r["value"] is not None
            and not (isinstance(r["value"], float) and math.isnan(r["value"]))]
    rows.sort(key=lambda r: (-r["value"] if reverse else r["value"],
                             r.get("season", 0)))
    out, rank = [], 0
    for i, r in enumerate(rows):
        if i == 0 or r["value"] != rows[i - 1]["value"]:
            rank = i + 1
        if rank > TOP_N or len(out) >= TIE_CAP:
            break
        out.append({**r, "rank": rank})
    return out


def _storm_rows(storms: pd.DataFrame, value_col: str, disp,
                date_col: str | None = None) -> list[dict]:
    rows = []
    for _, s in storms.iterrows():
        v = s[value_col]
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        rows.append({
            "value": float(v), "disp": disp(v),
            "name": s["name"] or (s["atcf"] or "UNNAMED"),
            "season": int(s["season"]), "sid": s["sid"],
            "date": _d(s[date_col]) if date_col else "",
        })
    return rows


def _season_rows(tbl: pd.DataFrame, col: str, disp) -> list[dict]:
    return [{"value": float(r[col]), "disp": disp(r[col]),
             "season": int(r["season"])} for _, r in tbl.iterrows()]


def _board(key, page, title, definition, unit, note, since, rows):
    return {"key": key, "page": page, "title": title,
            "definition": definition, "unit": unit, "note": note,
            "since": since, "rows": rows}


def build_boards(basin_cfg: dict, storms: pd.DataFrame,
                 seasons_tbl: pd.DataFrame, fixes: pd.DataFrame,
                 conc_ts: pd.DataFrame, conc_hu: pd.DataFrame,
                 current_year: int) -> list[dict]:
    since = basin_cfg["records_since"]
    sat = basin_cfg["satellite_era"]
    wind_note = basin_cfg["wind_note"]
    hu_word = basin_cfg["hu_word"]
    complete = seasons_tbl[seasons_tbl["season"] < current_year]
    sat_complete = complete[complete["season"] >= sat]

    b: list[dict] = []
    i0 = lambda v: f"{v:.0f}"
    i1 = lambda v: f"{v:.1f}"
    i2 = lambda v: f"{v:.2f}"

    # ------------------------------------------------------------------ seasons
    b.append(_board(
        "season_most_named", "seasons", "Most named storms in a season",
        "Storms reaching ≥34 kt at tropical or subtropical status, attributed "
        "to their genesis season. Non-developing depressions excluded.",
        "storms", "Pre-satellite seasons undercount real activity.", since,
        _rank(_season_rows(seasons_tbl, "named", i0), reverse=True)))
    b.append(_board(
        "season_fewest_named", "seasons", "Fewest named storms in a season",
        "Same definition, satellite era only; the running season is excluded "
        "until complete.",
        "storms", f"Satellite era ({sat} onward) only.", sat,
        _rank(_season_rows(sat_complete, "named", i0), reverse=False)))
    b.append(_board(
        "season_most_hu", "seasons", f"Most {hu_word}s in a season",
        "Named storms whose 1-min-equivalent wind reached ≥64 kt.",
        "storms", "Pre-satellite seasons undercount real activity.", since,
        _rank(_season_rows(seasons_tbl, "hu", i0), reverse=True)))
    b.append(_board(
        "season_most_major", "seasons", "Most major storms in a season",
        "Named storms reaching ≥96 kt (Category 3+).",
        "storms", "Pre-satellite seasons undercount real activity.", since,
        _rank(_season_rows(seasons_tbl, "major", i0), reverse=True)))
    b.append(_board(
        "season_top_ace", "seasons", "Highest season ACE",
        "Σ v²/10⁴ over 6-hourly synoptic fixes (00/06/12/18Z) at tropical or "
        "subtropical status and ≥34 kt.",
        "10⁴ kt²", wind_note, since,
        _rank(_season_rows(seasons_tbl, "ace", i1), reverse=True)))
    b.append(_board(
        "season_low_ace", "seasons", "Lowest season ACE",
        "Same ACE definition, satellite era only; running season excluded.",
        "10⁴ kt²", f"Satellite era ({sat} onward) only.", sat,
        _rank(_season_rows(sat_complete, "ace", i1), reverse=False)))
    b.append(_board(
        "season_top_pdi", "seasons", "Highest season PDI",
        "Σ v³ over the same synoptic ≥34-kt fix set as ACE (power dissipation "
        "index, 6-hourly discrete form).",
        "10⁶ kt³", wind_note, since,
        _rank(_season_rows(seasons_tbl, "pdi", i1), reverse=True)))
    b.append(_board(
        "season_most_ri", "seasons",
        "Most rapidly intensifying storms in a season",
        "Storms with at least one +30 kt / 24 h increase between synoptic "
        "fixes (both fixes tropical or subtropical).",
        "storms", f"Satellite era ({sat} onward) shown; RI detection is "
        "unreliable earlier.", sat,
        _rank(_season_rows(seasons_tbl[seasons_tbl["season"] >= sat],
                           "ri_storms", i0), reverse=True)))

    # ---------------------------------------------------------------- intensity
    st = storms
    b.append(_board(
        "storm_min_pres", "intensity", "Lowest central pressure",
        "Minimum reported central pressure at tropical or subtropical status "
        "(all best-track fixes, special entries included). Storms with no "
        "reported pressure are excluded, never assumed.",
        "mb", "Pressure needs no wind-averaging conversion.", since,
        _rank(_storm_rows(st, "min_pres", i0, "min_pres_time"),
              reverse=False)))
    b.append(_board(
        "storm_peak_wind", "intensity", "Highest sustained wind",
        "Lifetime maximum sustained wind at tropical or subtropical status.",
        "kt", wind_note, since,
        _rank(_storm_rows(st, "peak_wind", i0, "peak_time"), reverse=True)))
    for h in (24, 12, 6):
        b.append(_board(
            f"storm_deep{h}", "intensity",
            f"Fastest {h}-hour deepening",
            f"Largest central-pressure fall over exactly {h} h between "
            "synoptic fixes, both fixes tropical or subtropical. Computed "
            "from 6-hourly best-track data (aircraft-fix values in TCRs can "
            "differ).",
            f"mb / {h} h", "", since,
            _rank(_storm_rows(st, f"deep{h}", i0, f"deep{h}_time"),
                  reverse=True)))
    b.append(_board(
        "storm_rise24", "intensity", "Largest 24-h wind increase",
        "Largest sustained-wind increase over exactly 24 h between synoptic "
        "fixes, both tropical or subtropical (≥30 kt is the standard "
        "rapid-intensification threshold).",
        "kt / 24 h", wind_note, since,
        _rank(_storm_rows(st, "rise24", i0, "rise24_time"), reverse=True)))
    b.append(_board(
        "storm_fall24", "intensity", "Most rapid 24-hour weakening",
        "Largest sustained-wind decrease over exactly 24 h between synoptic "
        "fixes, both tropical or subtropical. Landfall decay is included and "
        "usually the cause.",
        "kt / 24 h", wind_note, since,
        _rank(_storm_rows(st, "fall24", i0, "fall24_time"), reverse=True)))
    lat_rows = []
    for _, s in st[st["named"]].iterrows():
        if s["peak_lat"] is None or pd.isna(s["peak_lat"]):
            continue
        lat_rows.append({
            "value": abs(float(s["peak_lat"])),
            "disp": f"{abs(s['peak_lat']):.1f}°N",
            "name": s["name"] or (s["atcf"] or "UNNAMED"),
            "season": int(s["season"]), "sid": s["sid"],
            "date": _d(s["peak_time"]),
            "extra": f"{s['peak_wind']:.0f} kt",
        })
    b.append(_board(
        "storm_peak_lat_low", "intensity",
        "Lowest latitude at peak intensity",
        "Latitude of the lifetime peak-wind fix, i.e. how close to the "
        "equator a storm reached its maximum.",
        "° lat", "", since, _rank(list(lat_rows), reverse=False)))
    b.append(_board(
        "storm_peak_lat_high", "intensity",
        "Highest latitude at peak intensity",
        "Latitude of the lifetime peak-wind fix: storms still "
        "strengthening unusually far north.",
        "° lat", "", since, _rank(list(lat_rows), reverse=True)))
    b.append(_board(
        "storm_top_ace", "intensity", "Highest single-storm ACE",
        "Σ v²/10⁴ over the storm's synoptic ≥34-kt tropical/subtropical "
        "fixes over the whole lifetime, attributed to the genesis basin.",
        "10⁴ kt²", wind_note, since,
        _rank(_storm_rows(st, "ace", i1), reverse=True)))
    b.append(_board(
        "storm_top_pdi", "intensity", "Highest single-storm PDI",
        "Σ v³ over the same fix set as ACE.",
        "10⁶ kt³", wind_note, since,
        _rank(_storm_rows(st, "pdi", i1), reverse=True)))

    # ----------------------------------------------------- duration & motion
    b.append(_board(
        "storm_dur_tc", "duration", "Longest-lived tropical cyclones",
        "Time spent at tropical or subtropical status at any intensity: "
        "6-hourly synoptic fixes summed (0.25 d each), so extratropical or "
        "remnant gaps don't count.",
        "days", "", since,
        _rank(_storm_rows(st, "dur_tc", i2), reverse=True)))
    b.append(_board(
        "storm_dur_hu", "duration", f"Most time at {hu_word} strength",
        "Synoptic fixes at ≥64 kt, tropical or subtropical, summed.",
        "days", wind_note, since,
        _rank(_storm_rows(st[st["dur_hu"] > 0], "dur_hu", i2),
              reverse=True)))
    b.append(_board(
        "storm_dur_major", "duration", "Most time at major strength",
        "Synoptic fixes at ≥96 kt (Category 3+), summed.",
        "days", wind_note, since,
        _rank(_storm_rows(st[st["dur_major"] > 0], "dur_major", i2),
              reverse=True)))
    b.append(_board(
        "storm_dist", "duration", "Longest track distance",
        "Great-circle distance summed over consecutive synoptic tropical/"
        "subtropical fixes (gaps over 12 h skipped).",
        "km", "", since,
        _rank(_storm_rows(st, "dist_km", lambda v: f"{v:,.0f}"),
              reverse=True)))
    b.append(_board(
        "storm_speed", "duration", "Fastest forward motion",
        "Fastest 6-h great-circle leg between synoptic fixes with both ends "
        "at ≥34 kt tropical/subtropical. Legs over 70 kt are dropped as "
        "best-track position artifacts.",
        "kt", "", since,
        _rank(_storm_rows(st, "max_speed", i1, "max_speed_time"),
              reverse=True)))
    b.append(_board(
        "storm_gen_lat_low", "duration", "Lowest-latitude genesis",
        "Latitude of the first synoptic fix at ≥34 kt tropical/subtropical "
        "status (where the storm became a named storm).",
        "° lat", "", since,
        _rank([{"value": abs(float(s["gen_lat"])),
                "disp": f"{abs(s['gen_lat']):.1f}°N",
                "name": s["name"] or (s["atcf"] or "UNNAMED"),
                "season": int(s["season"]), "sid": s["sid"],
                "date": _d(s["formation"])}
               for _, s in st[st["named"]].iterrows()
               if not math.isnan(s["gen_lat"])], reverse=False)))
    b.append(_board(
        "storm_hu_lat_low", "duration",
        f"Lowest-latitude {hu_word}-strength fix",
        "Minimum |latitude| of any tropical/subtropical fix at ≥64 kt.",
        "° lat", "", since,
        _rank([{"value": float(s["min_abs_lat_hu"]),
                "disp": f"{s['min_abs_lat_hu']:.1f}°N",
                "name": s["name"] or (s["atcf"] or "UNNAMED"),
                "season": int(s["season"]), "sid": s["sid"], "date": ""}
               for _, s in st.iterrows()
               if not math.isnan(s["min_abs_lat_hu"])], reverse=False)))
    b.append(_board(
        "storm_ts_lat_high", "duration",
        "Highest-latitude fix at storm strength",
        "Maximum latitude while still tropical or subtropical at ≥34 kt "
        "(post-tropical phases never count).",
        "° lat", "", since,
        _rank([{"value": float(s["max_lat_ts"]),
                "disp": f"{s['max_lat_ts']:.1f}°N",
                "name": s["name"] or (s["atcf"] or "UNNAMED"),
                "season": int(s["season"]), "sid": s["sid"], "date": ""}
               for _, s in st.iterrows()
               if not math.isnan(s["max_lat_ts"])], reverse=True)))

    # ------------------------------------------------------------------ timing
    named = st[st["named"]].dropna(subset=["formation"])
    by_season: dict[int, list] = {}
    for _, s in named.iterrows():
        by_season.setdefault(int(s["season"]), []).append(s)
    for season in by_season:
        by_season[season].sort(key=lambda s: s["formation"])

    nth_rows = []
    max_n = max((len(v) for v in by_season.values()), default=0)
    for n in range(1, max_n + 1):
        cands = [(v[n - 1], season) for season, v in by_season.items()
                 if len(v) >= n]
        s, season = min(cands,
                        key=lambda c: _monthday_key(c[0]["formation"]))
        nth_rows.append({
            "rank": n, "value": n,
            "disp": _md(s["formation"]),
            "name": s["name"] or (s["atcf"] or "UNNAMED"),
            "season": season, "sid": s["sid"],
            "date": _d(s["formation"]),
        })
    b.append(_board(
        "timing_earliest_nth", "timing", "Earliest Nth named storm",
        "For each ordinal N: the earliest calendar date any season's Nth "
        "named storm formed (formation is the first synoptic fix at ≥34 kt).",
        "", "High ordinals only exist in hyperactive seasons.", since,
        nth_rows))

    first_rows = [{"value": float(_monthday_key(v[0]["formation"])[0] * 100
                                  + _monthday_key(v[0]["formation"])[1]),
                   "disp": _md(v[0]["formation"]),
                   "name": v[0]["name"] or (v[0]["atcf"] or "UNNAMED"),
                   "season": season, "sid": v[0]["sid"],
                   "date": _d(v[0]["formation"])}
                  for season, v in by_season.items()
                  if season >= sat and season < current_year]
    b.append(_board(
        "timing_latest_first", "timing", "Latest-starting seasons",
        "Latest formation date of a season's FIRST named storm (satellite "
        "era; the running season is excluded).",
        "", f"Satellite era ({sat} onward) only.", sat,
        _rank(first_rows, reverse=True)))

    for key, col, label in (("timing_earliest_hu", "first_hu", hu_word),
                            ("timing_earliest_major", "first_major",
                             "major")):
        rows = []
        seen: dict[int, dict] = {}
        for _, s in st.dropna(subset=[col]).iterrows():
            season = int(s["season"])
            if season not in seen or s[col] < seen[season][col]:
                seen[season] = s
        for season, s in seen.items():
            mk = _monthday_key(s[col])
            rows.append({"value": float(mk[0] * 10000 + mk[1] * 100 + mk[2]),
                         "disp": _md(s[col]),
                         "name": s["name"] or (s["atcf"] or "UNNAMED"),
                         "season": season, "sid": s["sid"],
                         "date": _d(s[col])})
        b.append(_board(
            key, "timing", f"Earliest {label} of a season",
            f"Earliest calendar date a season's first {label} "
            "(≥{th} kt) fix occurred.".format(
                th=64 if col == "first_hu" else 96),
            "", "", since, _rank(rows, reverse=False)))

    late_rows = []
    for season, v in by_season.items():
        last = max((s["last_trop"] for s in v if s["last_trop"] is not None),
                   default=None)
        if last is None:
            continue
        s = next(s for s in v if s["last_trop"] == last)
        days = (pd.Timestamp(last)
                - pd.Timestamp(year=season, month=1, day=1)).days + 1
        late_rows.append({"value": float(days),
                          "disp": _d(last),
                          "name": s["name"] or (s["atcf"] or "UNNAMED"),
                          "season": season, "sid": s["sid"], "date": ""})
    b.append(_board(
        "timing_latest_activity", "timing", "Latest in-season activity",
        "Last tropical/subtropical synoptic fix of each season's storms. "
        "Jan/Feb dates belong to the PRIOR season when a storm crossed "
        "New Year.",
        "", "", since, _rank(late_rows, reverse=True)))

    span_rows = []
    for season, v in by_season.items():
        first = min(s["formation"] for s in v)
        last = max((s["last_trop"] for s in v if s["last_trop"] is not None),
                   default=None)
        if last is None:
            continue
        span = (pd.Timestamp(last) - pd.Timestamp(first)).days
        span_rows.append({"value": float(span), "disp": f"{span} d",
                          "season": season,
                          "date": f"{_md(first)} to {_d(last)}"})
    b.append(_board(
        "timing_longest_span", "timing", "Longest season span",
        "Days from the season's first named-storm formation to its last "
        "tropical fix.",
        "days", "", since, _rank(span_rows, reverse=True)))

    if basin_cfg.get("season_start"):
        sm, sd = basin_cfg["season_start"]
        pre_rows = []
        for season, v in by_season.items():
            n = sum(1 for s in v
                    if _monthday_key(s["formation"])[:2] < (sm, sd))
            if n > 0:
                pre_rows.append({"value": float(n), "disp": f"{n}",
                                 "season": season})
        b.append(_board(
            "timing_preseason", "timing", "Most pre-season storms",
            f"Named storms forming before the official season start "
            f"({MONTHS[sm - 1]} {sd}).",
            "storms", "", since, _rank(pre_rows, reverse=True)))

    # ------------------------------------------------------------- concurrency
    for key, conc, label, floor in (
            ("conc_sim_ts", conc_ts, "storms", 34),
            ("conc_sim_hu", conc_hu, f"{hu_word}s", 64)):
        rows = []
        if len(conc):
            for season, g in conc.groupby("season"):
                peak = int(g["count"].max())
                first = g[g["count"] == peak].iloc[0]
                rows.append({"value": float(peak), "disp": f"{peak}",
                             "season": int(season),
                             "date": _d(first["time"]),
                             "extra": ", ".join(first["names"])})
        b.append(_board(
            key, "concurrency", f"Most simultaneous {label}",
            f"Distinct storms with a synoptic tropical/subtropical fix at "
            f"≥{floor} kt at the same 00/06/12/18Z snapshot.",
            label, "", since, _rank(rows, reverse=True)))

    form_month = {}
    for _, s in named.iterrows():
        t = pd.Timestamp(s["formation"])
        k = (t.year, t.month)
        form_month[k] = form_month.get(k, 0) + 1
    month_rows = []
    for m in range(1, 13):
        cands = [(v, y) for (y, mm), v in form_month.items() if mm == m]
        if not cands:
            continue
        v, y = max(cands, key=lambda c: (c[0], -c[1]))
        month_rows.append({"rank": m, "value": float(v),
                           "disp": f"{v}", "season": y,
                           "name": MONTHS[m - 1], "date": ""})
    b.append(_board(
        "month_formations", "concurrency",
        "Most formations in a calendar month",
        "Record number of named-storm formations in each calendar month "
        "(record-holding year shown).",
        "storms", "", since, month_rows))

    f = fixes[fixes["syn"] & fixes["trop"] & (fixes["wind"] >= 34)].copy()
    f = f.dropna(subset=["wind"])
    f["_inc"] = (f["wind"] * f["wind"]) / 1e4
    by_ym = f.groupby([f["time"].dt.year, f["time"].dt.month])["_inc"].sum()
    ace_month_rows = []
    for m in range(1, 13):
        cands = [(v, y) for (y, mm), v in by_ym.items() if mm == m]
        if not cands:
            continue
        v, y = max(cands, key=lambda c: (c[0], -c[1]))
        ace_month_rows.append({"rank": m, "value": round(float(v), 1),
                               "disp": f"{v:.1f}", "season": int(y),
                               "name": MONTHS[m - 1], "date": ""})
    b.append(_board(
        "month_ace", "concurrency", "Busiest calendar month by ACE",
        "Record ACE accumulated inside each calendar month (record-holding "
        "year shown).",
        "10⁴ kt²", wind_note, since, ace_month_rows))

    return b
