"""tc_records — archive-stats engine behind the TC History records suite.

One engine, three basins (al / ep / wp), one canonical fix schema:

    sid, atcf, name, season, time, lat, lon, wind, pres, status, trop, syn, src

- ``wind`` is 1-minute-equivalent kt everywhere (IBTrACS WMO/Tokyo 10-min
  columns are converted via ace_core.WIND_PREFERENCE, exactly as the live ACE
  product does). ``pres`` is mb, NaN when unreported (-999 / blank).
- ``trop`` is the tropical-or-subtropical gate; ``syn`` marks synoptic fixes
  (00/06/12/18Z, minute 0). Every summed metric (ACE, PDI, duration,
  concurrency) uses ``syn & trop`` only — never interpolated rows, never
  EX/LO/DB fixes.
- AL + EP boards are computed from HURDAT2 (NHC authority, full lifetimes —
  dateline crossers like Ioke/Genevieve/Paka are tracked to dissipation in
  the nepac file). WP boards come from IBTrACS (JTWC columns). The current
  season is topped up from IBTrACS PROVISIONAL rows plus the live ATCF
  b-deck chain shared with generate_ace_plot.
- Storm identity is one SID; attribution is genesis basin + genesis season
  (Ioke belongs to EP-2006 with its whole 85.3 ACE, and is deduped out of
  the WP frame by genesis-basin filtering).

Andrew's non-negotiables (see the out-of-repo records spec) are enforced in
publish.validate_or_die(); the workflow refuses to upload JSON that fails it.
"""

ENGINE_VERSION = "1.0.0"

# Threshold vocabulary (kt, 1-min): named / hurricane-typhoon / major.
NAMED_KT = 34
HURR_KT = 64
MAJOR_KT = 96

# Forward-speed sanity ceiling — 6-h great-circle legs faster than this are
# position/typo artifacts in early best tracks, not motion (disclosed on the
# board).
MAX_PLAUSIBLE_SPEED_KT = 70.0
