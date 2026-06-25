"""tcprimed - OBSERVED passive-microwave imagery for tropical cyclones.

Renders 89 GHz PCT (polarization-corrected) and 37 GHz color composites for
tropical-cyclone overpasses, writes a per-storm manifest + per-overpass index +
PNGs into a build dir, and the workflow syncs that tree to R2 under microwave/.
Both products use the canonical NRL passive-MW recipes (89 PCT on the NRL ice-
scattering colormap; 37 GHz true-color RGB) -- see tcprimed.render. PCT math
reuses hafs_render.compute_pct89. Mirrors the reconobs build pattern (growing-
union manifest, kill switch, fail-loud-but-non-destructive).

Two data tiers merge into the one microwave/ manifest:
  * ARCHIVE: NOAA/CIRA TC-PRIMED (anonymous public S3 bucket
    noaa-nesdis-tcprimed-pds) -- research-tiered (`final` post-season,
    `preliminary` lagging hours-to-days). See tcprimed.fetch / build.build.
  * LIVE/NRT: NASA GPM / PPS near-real-time GPM-constellation 1C (~1-3 h
    latency) for currently-active storms. See tcprimed.pps / build.build_live.
The viewer's disclosure describes both tiers.
"""

SCHEMA_VERSION = 1
# Per-render credit: live overpasses come straight from NASA GPM/PPS NRT; the
# archive overpasses are the inter-calibrated NOAA/CIRA TC-PRIMED L1C.
SOURCE_ARCHIVE = "NOAA/CIRA TC-PRIMED"
SOURCE_LIVE = "NASA GPM / PPS"
# Manifest-level credit (the manifest merges both tiers).
SOURCE = "NASA GPM/PPS (live) + NOAA/CIRA TC-PRIMED (archive)"
DISCLOSURE = (
    "Observed passive microwave for tropical cyclones. Recent storms: live from "
    "NASA GPM / PPS near-real-time (~1-3 h latency). Archive: NOAA/CIRA TC-PRIMED "
    "(research-tiered, post-season; its data lags ~months)."
)

# Sensors carrying an 89/37 V/H imager pair (group, V channel, H channel).
# Verified against real overpass NetCDFs. ATMS / MHS are sounders (no 89 V/H
# imager pair) and are skipped at the listing stage.
PMW_CHANNELS = {
    "AMSR2": {"37": ("S4", "TB_36.5V", "TB_36.5H"),
              "89": ("S5", "TB_A89.0V", "TB_A89.0H")},
    "GMI":   {"37": ("S1", "TB_36.64V", "TB_36.64H"),
              "89": ("S1", "TB_89.0V", "TB_89.0H")},
    "SSMIS": {"37": ("S2", "TB_37.0V", "TB_37.0H"),
              "89": ("S4", "TB_91.665V", "TB_91.665H")},
}

# Imager sensors we process (skip ATMS, MHS - sounders).
IMAGER_SENSORS = tuple(PMW_CHANNELS.keys())

__all__ = [
    "SCHEMA_VERSION", "SOURCE", "SOURCE_ARCHIVE", "SOURCE_LIVE", "DISCLOSURE",
    "PMW_CHANNELS", "IMAGER_SENSORS",
]
