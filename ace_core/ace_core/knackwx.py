"""RyanKnack's ATCF aggregator as an intensity leg.

WHY THIS EXISTS
---------------
Our JTWC chain reaches a synoptic hour through three routes that all trail the
agency by different amounts: the b-deck via an unofficial mirror, NCEP tcvitals
via the model-init pipeline, and (since 0.8.10) the warning text itself. Each
can be first, and each can be wrong about an hour another one already has.

``api.knackwx.com/atcf/v2`` aggregates JTWC's and NHC's currently-active systems
into a single JSON array refreshed on every bulletin push. The repo already
leaned on it for invests (``fetch_live_invests`` in generate_tracks_plot.py),
where it replaced a 90-99 b-deck sweep that depended on a mirror last updated in
January; that code's docstring left designated storms explicitly unconfirmed:
"knackwx may or may not list them and we haven't confirmed yet." It does. The
2026-07-30T03:15Z payload carried 07E GENEVIEVE, 12W DOLPHIN and 06E FAUSTO
alongside 94W, all with position, 1-min wind, MSLP and dev level.

WHAT IT IS AND IS NOT
---------------------
It is a **leading-edge** source: exactly ONE fix per storm, the current
analysis. There is no track history in the payload, so this leg can lead the
leading edge and it can disagree at the newest hour, but it can never fill in a
past hour the other legs missed. Sizing expectations correctly here matters —
it was proposed as the fix for a 145 kt / 915 mb peak that lived at an OLDER
synoptic hour, and no amount of polling this endpoint would have produced it.

It is, uniquely among the live legs, **self-typing**: ``cyclone_nature`` is the
ATCF dev level (TD/TS/TY/ST/HU/DB/LO), so a knackwx fix goes through
``STATUS_TO_NATURE`` like a b-deck row instead of waiting on the warnings leg.
A fix that arrives typed cannot be stranded as indeterminate and silently
excluded from ACE.

SCOPE
-----
Designated systems only (storm number < 90). Invests stay with the existing
``fetch_live_invests`` path so the two never both claim a 9x system.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import urllib.request
from typing import Callable, Optional

from . import (
    RADII_COLS,
    SIX_HOURLY,
    STATUS_TO_NATURE,
)
from . import tcvitals as tcv

#: The aggregator. Same endpoint the invest path already uses.
KNACKWX_URL = "https://api.knackwx.com/atcf/v2"

#: Provenance tag. Free-form, like the other legs' source strings.
KNACKWX_SOURCE = "live-knackwx"

#: Invests start here. Below this is a designated system.
INVEST_MIN = 90

_ID_RE = re.compile(r"^(\d{2})([A-Z])$")

_UA = ("Mozilla/5.0 (compatible; Triple-A-Tropics/1.0; "
       "+https://triple-a-tropics.com)")

_TIMEOUT = 20


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _get(url: str) -> Optional[str]:
    """Fetch, or None. Never raises — one dead leg must not cost the others."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            if r.status != 200:
                return None
            return r.read().decode("utf-8", errors="ignore")
    except Exception:                                            # noqa: BLE001
        return None


def _parse_time(raw) -> Optional[dt.datetime]:
    """'2026-07-30T00:00:00.000Z' -> naive UTC datetime, like every other leg."""
    s = str(raw or "").strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if t.tzinfo is not None:
        t = t.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return t


def _num(val) -> Optional[float]:
    """A finite number, or None. The API uses -9999.99 as its absent marker."""
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if f != f or f <= -9000.0:
        return None
    return f


def parse_knackwx(text: str, season: int, basin_cfg: dict,
                  now: Optional[dt.datetime] = None,
                  max_age_h: float = 36.0):
    """Aggregator JSON -> ``parse_bdeck``-schema rows for THIS basin.

    Emits the same columns as ``parse_tcvitals`` so downstream consumers need no
    changes. Unlike that leg, ``nature``/``ace_nature`` are filled from the
    payload's own dev level and ``type_status`` is ``observed`` — the fix is
    typed at the source, so it never depends on the warnings leg resolving.

    Guards, in order: valid JSON list; a well-formed ``NNL`` id; designated
    (< 90) and in this basin's letters; a parseable analysis time that is
    6-hourly, on the minute, in ``season``, not in the future and not older than
    ``max_age_h``; finite position and wind. Anything failing a guard is skipped
    silently rather than poisoning the frame — this is a third-party endpoint and
    a schema change upstream must degrade to "no fixes", never to bad fixes.
    """
    import pandas as pd

    now = now or _utcnow()
    short = str(basin_cfg.get("short") or "").strip().lower()
    letters = set(tcv.BASIN_LETTERS.get(short, ()))
    agency = str(basin_cfg.get("agency_name") or "").strip()

    try:
        data = json.loads(text or "")
    except (ValueError, TypeError):
        return pd.DataFrame()
    if not isinstance(data, list):
        return pd.DataFrame()

    rows: list[dict] = []
    seen: dict[tuple, int] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        m = _ID_RE.match(str(item.get("atcf_id") or "").strip().upper())
        if not m:
            continue
        storm_num, letter = int(m.group(1)), m.group(2)
        if storm_num >= INVEST_MIN:
            continue                      # invests stay with fetch_live_invests
        if letters and letter not in letters:
            continue
        atcf_token = tcv.LETTER_TO_ATCF.get(letter)
        if atcf_token is None:
            continue

        t = _parse_time(item.get("analysis_time"))
        if t is None or t.hour not in SIX_HOURLY or t.minute or t.second:
            continue
        if t.year != int(season):
            continue
        age_h = (now - t).total_seconds() / 3600.0
        if age_h < -1.0 or age_h > float(max_age_h):
            continue

        lat, lon = _num(item.get("latitude")), _num(item.get("longitude"))
        wind = _num(item.get("winds"))
        if lat is None or lon is None or wind is None:
            continue
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 360.0):
            continue

        # Self-typing: the payload carries the ATCF dev level directly.
        status = str(item.get("cyclone_nature") or "").strip().upper()
        nature = STATUS_TO_NATURE.get(status)
        if nature is None:
            continue        # an unmapped dev level is not guessed at

        pres = _num(item.get("pressure"))
        rec = {
            "SID": f"{agency}_{atcf_token}{storm_num:02d}{int(season)}",
            "NAME": tcv._clean_name(str(item.get("storm_name") or ""),
                                    storm_num, letter),
            "season": int(season),
            "time": t,
            "lat": float(lat),
            "lon": float(lon),
            "wind_kt": float(wind),
            "pressure_mb": float(pres) if pres is not None else float("nan"),
            "nature": nature,
            "ace_nature": nature,
            "source": KNACKWX_SOURCE,
            "storm_num": storm_num,
            "atcf_short": f"{storm_num:02d}{letter}",
            # Typed at the source, so resolve_fix_types has nothing to add and
            # cannot downgrade it to indeterminate.
            "type_status": tcv.TYPE_OBSERVED,
            "spawn_invest": None,
            "spawn_invest_letter": None,
            "rmw_nm": None,
        }
        for col in RADII_COLS:
            rec[col] = None        # the payload carries no wind radii

        key = (rec["SID"], t)
        if key in seen:
            rows[seen[key]] = rec
            continue
        seen[key] = len(rows)
        rows.append(rec)

    return pd.DataFrame(rows)


def fetch_knackwx(season: int, basin_cfg: dict,
                  now: Optional[dt.datetime] = None,
                  getter: Callable[[str], Optional[str]] = _get):
    """``(DataFrame, ok, detail)`` — the leg, fetched and parsed.

    ``ok`` is False on a dead endpoint so the caller can report honest staleness
    instead of quietly showing one fewer source.
    """
    import pandas as pd

    text = getter(KNACKWX_URL)
    if not text:
        return pd.DataFrame(), False, "fetch failed"
    df = parse_knackwx(text, season, basin_cfg, now=now)
    return df, True, f"{len(df)} fix(es)"
