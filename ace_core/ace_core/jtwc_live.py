"""Guarded two-leg poller for JTWC basins — the network half.

The parsers in ``ace_core.tcvitals`` and ``ace_core.jtwc_warnings`` are pure
functions over text. This module is the only place that touches the network,
which is what makes the parsers testable against captured bulletins and keeps
retry/timeout/failure policy in one reviewable place.

DESIGN CONSTRAINTS (all of them learned the hard way on this repo)
------------------------------------------------------------------
*Per-source isolation.* Every source is fetched inside its own guard. A source
that 404s, times out, returns HTML, or throws mid-parse contributes nothing and
cannot fail the run or the other leg. Intensity going stale must never take the
type leg down with it, and vice versa — they fail independently by construction,
not by convention.

*Watermark + idempotent backfill.* The primary tcvitals source is a WHOLE-SEASON
file, so a run re-reads every record every time and a missed run self-heals on
the next one with no state to keep. That is the strongest possible form of
"never miss": there is no watermark to corrupt because correctness does not
depend on one. The watermark exists only as the BACKFILL BOUND for the
per-cycle secondary — how far back to sweep NOMADS when the season file is
unavailable — and it is derived from data already in hand (the newest fix we
hold), never persisted. ``parse_tcvitals`` dedups on (SID, time), so re-ingesting
overlapping records is a no-op.

*Honest staleness.* Every fetch result carries the age of what it returned.
``poll_jtwc`` reports per-source status so a silently-dead source shows up as
dead rather than as a storm frozen at its last intensity.

*No source is trusted to be alive.* As of 2026-07 the JTWC a-decks are gone and
the b-decks survive only through an unofficial third-party mirror. Both legs
here were chosen because they ride infrastructure that was NOT part of that
shutdown: NCEP's model-init pipeline (tcvitals) and NOAA's public WMO bulletin
feed (warnings). That does not make them permanent — it makes them independent.
"""

from __future__ import annotations

import datetime as dt
import gzip
import io
import re
import urllib.error
import urllib.request
from typing import Callable, Iterable, Optional

from . import (
    RADII_COLS,
    RADII_QUADS,
    RADII_THRESHOLDS,
    SIX_HOURLY,
)
from . import tcvitals as tcv
from . import jtwc_warnings as jw

#: Identify ourselves. Several of these hosts reject the default urllib UA.
FETCH_UA = "triple-a-tropics/1.0 (+https://triple-a-tropics.com)"

FETCH_TIMEOUT = 25.0

# --- tcvitals sources ------------------------------------------------------

#: PRIMARY. NCAR/RAL's open tcvitals archive: one file per season, refreshed
#: within minutes of each cycle, carrying BOTH NHC and JTWC records. Whole-file
#: means backfill is automatic and idempotent — see the module docstring.
#: Observed 2026-07-25 to be AHEAD of the NOMADS copy (it had the 18Z fix at
#: 19:46Z, before the 18Z GFS cycle had published).
UCAR_TCVITALS = ("https://hurricanes.ral.ucar.edu/repository/data/"
                 "tcvitals_open/combined_tcvitals.{season}.dat")

#: SECONDARY. NCEP's per-cycle copy inside the GFS run. Independent
#: infrastructure from UCAR, but only ~11 days of retention (probed
#: 2026-07-25: gfs.20260715 present, gfs.20260714 already pruned), so it is a
#: gap-filler, not a season source.
NOMADS_CYCLE = ("https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/"
                "gfs.{ymd}/{hh}/atmos/gfs.t{hh}z.syndata.tcvitals.tm00")

#: NOT USED, deliberately. ``ftpprd.ncep.noaa.gov/data/nccf/com/gfs/prod/
#: syndat/syndat_tcvitals.<YYYY>`` is the documented season file, but the host
#: did not accept connections from this network or from GitHub-hosted runners
#: when probed (2026-07-25, connect timeout to 140.90.101.48). The NOMADS
#: ``/syndat/`` directory 403s. Both are recorded here so the next person does
#: not re-derive it; UCAR covers the same content.
_UNREACHABLE = (
    "https://ftpprd.ncep.noaa.gov/data/nccf/com/gfs/prod/syndat/"
    "syndat_tcvitals.{season}",
    "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/syndat/",
)

#: How far back the per-cycle secondary sweeps when it has to carry the load
#: alone. Bounded by NOMADS retention; the watermark shortens it further.
NOMADS_MAX_BACKFILL_H = 240

#: How far back a tcvitals fix may sit and still be published into the live
#: frame. THIS IS A SCOPE BOUND, and it is not optional.
#:
#: The season file carries the WHOLE year, so an unbounded merge quietly
#: back-fills every gap the per-storm b-deck sweep left — measured on a live WP
#: run: 73 fixes across 18 storms going back to January, none of them typeable
#: (tgftp keeps no warning history, so they all resolve indeterminate).
#:
#: They would accrue no ACE, but "no ACE" is not "no effect": ``canonical_peak_wind``
#: deliberately has no nature gate, so a back-filled fix can change a storm's
#: reported peak intensity, and ``merge_named_sources`` picks whichever source
#: has MORE observations for a storm — so padding the live frame can flip a
#: storm's canonical track away from IBTrACS. Silently rewriting January from a
#: real-time feed is not what this leg is for.
#:
#: This leg's job is the 0-6 h hole at the leading edge. 48 h is generous
#: headroom for a missed cron or a short b-deck outage while still being
#: unambiguously "live". Reconstructing a season from tcvitals is a legitimate
#: but SEPARATE, offline job — it must not happen as a side effect of a
#: 6-hourly render.
LEAD_WINDOW_H = 48.0


def _utcnow() -> dt.datetime:
    """Naive UTC now. Naive on purpose: every timestamp in this codebase
    (parse_bdeck, parse_tcvitals, parse_atcg) is a naive UTC datetime, and
    mixing in an aware one makes every comparison raise."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _get(url: str, timeout: float = FETCH_TIMEOUT) -> Optional[str]:
    """Fetch a URL as text, or None on ANY failure.

    Returning None rather than raising is the isolation primitive every guarded
    source is built on. Transparently gunzips, and rejects HTML — several of
    these hosts answer a missing file with a 200-status redirect page or an
    Apache error document rather than a 404, and parsing that as a bulletin
    would produce silent garbage instead of a clean miss.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": FETCH_UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if getattr(r, "status", 200) != 200:
                return None
            raw = r.read()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            ValueError):
        return None
    except Exception:
        return None
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        except OSError:
            return None
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return None
    head = text.lstrip()[:200].upper()
    if head.startswith("<!DOCTYPE") or head.startswith("<HTML"):
        return None
    return text


class SourceResult:
    """One guarded source's outcome — what it returned and whether it worked.

    Carried out of every poll so the caller can report honest per-source status
    instead of collapsing a dead source into an empty result set."""

    __slots__ = ("name", "ok", "url", "records", "error", "newest")

    def __init__(self, name: str, url: str = "", ok: bool = False,
                 records: int = 0, error: Optional[str] = None,
                 newest: Optional[dt.datetime] = None):
        self.name = name
        self.url = url
        self.ok = ok
        self.records = records
        self.error = error
        self.newest = newest

    def __repr__(self) -> str:
        state = "ok" if self.ok else f"FAILED({self.error})"
        stamp = f" newest={self.newest:%Y-%m-%d %HZ}" if self.newest else ""
        return f"<{self.name} {state} records={self.records}{stamp}>"


def _newest(df) -> Optional[dt.datetime]:
    if df is None or getattr(df, "empty", True) or "time" not in df:
        return None
    return df["time"].max()


# ---------------------------------------------------------------------------
# Leg 1 — tcvitals
# ---------------------------------------------------------------------------

def fetch_tcvitals(season: int, basin_cfg: dict,
                   watermark: Optional[dt.datetime] = None,
                   now: Optional[dt.datetime] = None,
                   getter: Callable[[str], Optional[str]] = _get):
    """Fetch and parse tcvitals for one basin. Returns ``(df, [SourceResult])``.

    Tries UCAR's season file first; it is a superset of anything the per-cycle
    copies hold, so on success the NOMADS sweep is skipped entirely. If UCAR is
    unavailable the sweep runs, walking synoptic cycles backwards from ``now``
    until it reaches ``watermark`` (the newest fix the caller already holds, so
    we re-fetch only what is genuinely missing) or hits the retention bound.

    ``getter`` is injected so the whole path is testable without a network.
    """
    import pandas as pd

    now = now or _utcnow()
    results: list[SourceResult] = []

    url = UCAR_TCVITALS.format(season=int(season))
    text = getter(url)
    if text:
        try:
            df = tcv.parse_tcvitals(text, season, basin_cfg, center="JTWC")
        except Exception as exc:                      # never kill the run
            results.append(SourceResult("ucar-season", url,
                                        error=f"parse: {exc}"))
            df = None
        else:
            results.append(SourceResult("ucar-season", url, ok=True,
                                        records=len(df), newest=_newest(df)))
            if not df.empty:
                return df, results
    else:
        results.append(SourceResult("ucar-season", url, error="fetch"))

    # Secondary: per-cycle sweep, bounded by the watermark.
    frames = []
    horizon = NOMADS_MAX_BACKFILL_H
    if watermark is not None:
        span = (now - watermark).total_seconds() / 3600.0
        # +12 h of overlap so a boundary cycle can never fall between the two
        # sources; duplicates are free (parse_tcvitals dedups on SID+time).
        horizon = max(6.0, min(horizon, span + 12.0))
    cycles = 0
    t = now.replace(minute=0, second=0, microsecond=0)
    t -= dt.timedelta(hours=t.hour % 6)
    while cycles * 6 <= horizon:
        u = NOMADS_CYCLE.format(ymd=f"{t:%Y%m%d}", hh=f"{t:%H}")
        body = getter(u)
        if body:
            try:
                frames.append(tcv.parse_tcvitals(body, season, basin_cfg,
                                                 center="JTWC"))
            except Exception:
                pass
        t -= dt.timedelta(hours=6)
        cycles += 1
    if frames:
        df2 = pd.concat(frames, ignore_index=True)
        df2 = df2.drop_duplicates(subset=["SID", "time"], keep="first")
        df2 = df2.reset_index(drop=True)
        results.append(SourceResult("nomads-cycles", NOMADS_CYCLE, ok=True,
                                    records=len(df2), newest=_newest(df2)))
        return df2, results
    results.append(SourceResult("nomads-cycles", NOMADS_CYCLE, error="fetch"))
    return pd.DataFrame(), results


# ---------------------------------------------------------------------------
# Leg 2 — JTWC warnings
# ---------------------------------------------------------------------------

def fetch_warnings(tokens: Iterable[str] = ("pn", "io", "ps", "xs"),
                   now: Optional[dt.datetime] = None,
                   getter: Callable[[str], Optional[str]] = _get):
    """Sweep JTWC warning slots. Returns ``(current_warnings, [SourceResult])``.

    For each basin token, every prose slot is paired with its ATCG twin
    (``wt{tok}31`` <-> ``wt{tok}51``) and merged; the ATCG half supplies the
    numbers and, crucially, the only unambiguous timestamp in the product set.
    ``select_current`` then drops leftover slots — see the slot trap in
    ``ace_core.jtwc_warnings``, which is the single most dangerous property of
    this feed.

    Each slot is independently guarded: one unparseable bulletin costs that
    slot, not the sweep.
    """
    now = now or _utcnow()
    merged: list[dict] = []
    results: list[SourceResult] = []
    for tok in tokens:
        found = 0
        for prose_slot, atcg_slot in zip(jw.PROSE_SLOTS, jw.ATCG_SLOTS):
            try:
                a_txt = getter(jw.tgftp_url(tok, atcg_slot))
                p_txt = getter(jw.tgftp_url(tok, prose_slot))
                atcg = jw.parse_atcg(a_txt) if a_txt else None
                prose = jw.parse_prose(p_txt) if p_txt else None
                m = jw.merge_slot(atcg, prose)
            except Exception:
                continue
            if m:
                merged.append(m)
                found += 1
        results.append(SourceResult(f"tgftp-{tok}", jw.TGFTP_BASE,
                                    ok=found > 0, records=found))
    current = jw.select_current(merged, now=now)
    results.append(SourceResult("warnings-current", "", ok=True,
                                records=len(current),
                                newest=max((w["time"] for w in current),
                                           default=None)))
    return current, results


# ---------------------------------------------------------------------------
# Leg 3 — the warning's own analysis, as fixes
# ---------------------------------------------------------------------------

#: Provenance tag for a fix recovered from the warning text itself. Free-form,
#: like ``tcv.TCVITALS_SOURCE``; nothing branches on it.
WARNING_SOURCE = "live-warning"

_ATCF_SHORT_RE = re.compile(r"(\d{2})([A-Z])")


def warning_fixes(warnings: Iterable[dict], season: int, basin_cfg: dict):
    """Merged warning records -> ``parse_bdeck``-schema fix rows.

    WHY THIS LEG EXISTS. JTWC publishes an analysis in the warning text before
    it reaches any of the machine-readable products we poll. Measured on 12W
    at 2026-07-29 19:07Z, the four sources stood at:

        b-deck mirror   290600Z   120 kt      13 h old
        ATCG wtpn51     290600Z   120 kt      13 h old
        tcvitals        291200Z   130 kt       7 h old
        prose wtpn31    291200Z   130 kt       7 h old   <- and issued first

    Before this leg the feed's leading edge was ``b-deck ∪ tcvitals``, so
    whenever tcvitals lagged a cycle the site sat on the 13-hour-old b-deck
    edge while JTWC's current analysis was sitting in a text bulletin we were
    already downloading and reading — for the storm TYPE only.

    Emits the same schema as ``parse_tcvitals`` so every downstream consumer
    works unchanged, and leaves ``nature`` indeterminate for
    ``resolve_fix_types`` to stamp — one type-resolution path, not two.

    ``pressure_mb`` is NaN: neither the prose nor the ATCG form carries MSLP.
    That is why the caller prefers a tcvitals row at the same (SID, hour) —
    this leg is for hours nothing else reaches, never a substitute.
    """
    import pandas as pd

    short = str(basin_cfg.get("short") or "").strip().lower()
    letters = set(tcv.BASIN_LETTERS.get(short, ()))
    agency = str(basin_cfg.get("agency_name") or "").strip()

    rows: list[dict] = []
    seen: dict[tuple, int] = {}
    for w in warnings or ():
        t = w.get("time")
        m = _ATCF_SHORT_RE.fullmatch(str(w.get("atcf_id") or "").upper())
        if t is None or not m:
            continue
        storm_num, letter = int(m.group(1)), m.group(2)
        if letters and letter not in letters:
            continue
        atcf_token = tcv.LETTER_TO_ATCF.get(letter)
        if atcf_token is None:
            continue
        # Same two gates parse_tcvitals applies, for the same reasons: an
        # off-cycle special bulletin must never enter the ACE fix set, and the
        # season must match or the SID would not line up with the b-deck's.
        if t.hour not in SIX_HOURLY or t.minute != 0:
            continue
        if t.year != int(season):
            continue
        lat, lon = w.get("lat"), w.get("lon")
        wind = w.get("wind_kt")
        if lat is None or lon is None or wind is None:
            continue

        rec = {
            "SID": f"{agency}_{atcf_token}{storm_num:02d}{int(season)}",
            "NAME": tcv._clean_name(str(w.get("name") or ""),
                                    storm_num, letter),
            "season": int(season),
            "time": t,
            "lat": float(lat),
            "lon": float(lon),
            "wind_kt": float(wind),
            "pressure_mb": float("nan"),
            "nature": tcv.NATURE_INDETERMINATE,
            "ace_nature": tcv.NATURE_INDETERMINATE,
            "source": WARNING_SOURCE,
            "storm_num": storm_num,
            "atcf_short": f"{storm_num:02d}{letter}",
            "type_status": tcv.TYPE_INDETERMINATE,
            "spawn_invest": None,
            "spawn_invest_letter": None,
            "rmw_nm": None,
        }
        for col in RADII_COLS:
            rec[col] = None
        # Unlike tcvitals, the warning text carries all three thresholds.
        for thr, quads in (w.get("radii") or {}).items():
            if thr not in RADII_THRESHOLDS or len(quads) != 4:
                continue
            for q, val in zip(RADII_QUADS, quads):
                rec[f"r{thr}_{q}"] = int(val)

        key = (rec["SID"], t)
        if key in seen:
            rows[seen[key]] = rec
            continue
        seen[key] = len(rows)
        rows.append(rec)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def poll_jtwc(season: int, basin_cfg: dict, bdeck_df=None,
              now: Optional[dt.datetime] = None,
              lead_window_h: float = LEAD_WINDOW_H,
              getter: Callable[[str], Optional[str]] = _get) -> dict:
    """Run both legs and return everything a generator needs to publish.

    Returns a dict with:
      ``fixes``     tcvitals fixes that EXTEND ``bdeck_df``, types resolved,
                    in ``parse_bdeck`` schema — concatenate and go.
      ``warnings``  the current warning set (for final-warning handling).
      ``sources``   per-source ``SourceResult`` list, for honest status.
      ``types``     ``type_summary`` tally, including indeterminate fixes.
      ``coverage``  per-storm account of what tcvitals added over the b-deck.

    Both legs are attempted regardless of the other's outcome. With the type leg
    down the fixes still come back, typed ``indeterminate`` — they render (so a
    stale intensity is fixed) but do not accrue ACE (so a guess never counts).
    """
    now = now or _utcnow()
    watermark = _newest(bdeck_df)
    df, src_a = fetch_tcvitals(season, basin_cfg, watermark=watermark,
                               now=now, getter=getter)
    warnings, src_b = fetch_warnings(now=now, getter=getter)

    kept = tcv.prefer_bdeck(bdeck_df, df)

    # Leg 3. Precedence is b-deck > tcvitals > warning, strictly by richness:
    # the b-deck is the revised post-analysis, tcvitals carries MSLP and RMW,
    # the warning text carries neither but is published FIRST. So a warning fix
    # is only ever added for an (SID, hour) the other two do not reach — which
    # makes this leg purely additive. When tcvitals is current the output is
    # unchanged, fix for fix.
    wfx = warning_fixes(warnings, season, basin_cfg)
    added_from_warnings = 0
    if wfx is not None and not getattr(wfx, "empty", True):
        wfx = tcv.prefer_bdeck(bdeck_df, wfx)
    if wfx is not None and not getattr(wfx, "empty", True):
        wfx = tcv.prefer_bdeck(kept, wfx)
    if wfx is not None and not getattr(wfx, "empty", True):
        import pandas as pd
        added_from_warnings = len(wfx)
        if kept is None or getattr(kept, "empty", True):
            kept = wfx.reset_index(drop=True)
        else:
            kept = pd.concat([kept, wfx], ignore_index=True)
        kept = kept.sort_values(["SID", "time"]).reset_index(drop=True)

    # Scope bound BEFORE type resolution — see LEAD_WINDOW_H. Anything older
    # than the window is a season back-fill, not a leading edge, and is dropped
    # rather than published into the live frame.
    dropped = 0
    if kept is not None and not getattr(kept, "empty", True) and lead_window_h:
        cutoff = now - dt.timedelta(hours=float(lead_window_h))
        before = len(kept)
        kept = kept[kept["time"] >= cutoff].reset_index(drop=True)
        dropped = before - len(kept)
    resolved = tcv.resolve_fix_types(kept, warnings, now=now)
    return {
        "fixes": resolved,
        "warnings": warnings,
        "sources": list(src_a) + list(src_b),
        "types": tcv.type_summary(resolved),
        "coverage": tcv.coverage_report(bdeck_df, resolved),
        "watermark": watermark,
        "outside_lead_window": dropped,
        "from_warnings": added_from_warnings,
    }


#: ``type_status`` stamped on b-deck rows. Their type is not inferred at all —
#: it comes from the deck's own dev-level column via ``STATUS_TO_NATURE``.
BDECK_TYPE_STATUS = "bdeck"


def extend_with_tcvitals(bdeck_df, season: int, basin_cfg: dict,
                         log_prefix: str = "", now=None, getter=_get):
    """Generator-facing entry point: b-deck frame in, extended frame out.

    Returns ``(combined_df, info)``. ``combined_df`` is the b-deck frame with
    the tcvitals leading edge appended, in ``parse_bdeck`` schema plus three
    columns (``type_status``, ``atcf_short``, ``rmw_nm``) that b-deck rows also
    receive so the frame stays rectangular.

    A basin opts in with ``basin_cfg["tcvitals"] = True``. Basins that do not
    are returned UNCHANGED — no new columns, no reordering, nothing. That is
    what keeps NHC-basin output byte-identical while WP gains the second leg.

    Every failure mode degrades to "return what we already had": no network, a
    dead source, a parse error, or an empty result all leave ``bdeck_df``
    untouched rather than raising into the generator.
    """
    import pandas as pd

    info: dict = {"enabled": False, "added": 0, "types": {}, "sources": []}
    if not basin_cfg.get("tcvitals"):
        return bdeck_df, info
    info["enabled"] = True

    try:
        out = poll_jtwc(season, basin_cfg, bdeck_df=bdeck_df, now=now,
                        getter=getter)
    except Exception as exc:                      # never break the run
        info["error"] = f"{type(exc).__name__}: {exc}"
        print(f"{log_prefix}   tcvitals: FAILED ({info['error']}) — "
              f"b-deck data used as-is")
        return bdeck_df, info

    fixes = out["fixes"]
    info.update(types=out["types"], sources=out["sources"],
                coverage=out["coverage"], warnings=len(out["warnings"]),
                outside_lead_window=out.get("outside_lead_window", 0),
                added=0 if fixes is None or fixes.empty else len(fixes))

    for s in out["sources"]:
        print(f"{log_prefix}     {s}")
    # Never silent: say what the scope bound dropped, so a growing number here
    # is visible as a b-deck coverage problem rather than as nothing at all.
    if info["outside_lead_window"]:
        print(f"{log_prefix}     {info['outside_lead_window']} fix(es) older "
              f"than the {LEAD_WINDOW_H:.0f} h lead window (season back-fill, "
              f"not published)")
    if fixes is None or fixes.empty:
        print(f"{log_prefix}   tcvitals: no fixes beyond the b-deck")
        return bdeck_df, info

    base = bdeck_df
    if base is not None and not getattr(base, "empty", True):
        base = base.copy()
        if "type_status" not in base.columns:
            base["type_status"] = BDECK_TYPE_STATUS
        combined = pd.concat([base, fixes], ignore_index=True)
    else:
        combined = fixes.reset_index(drop=True)

    counts = out["types"]
    unresolved = sum(v for k, v in counts.items()
                     if k in (tcv.TYPE_INDETERMINATE, tcv.TYPE_ENDED))
    print(f"{log_prefix}   tcvitals: +{len(fixes)} fix(es) beyond the b-deck "
          f"({counts.get('ace_eligible', 0)} ACE-eligible, "
          f"{unresolved} not typed -> excluded from ACE)")
    for sid, c in sorted(out["coverage"].items()):
        if c.get("extends_hours"):
            print(f"{log_prefix}     {sid}: +{c['extends_hours']:.0f} h "
                  f"({c['tcvitals_added']} fix(es))")
    return combined, info
