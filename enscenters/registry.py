"""
Declarative registry for the Ensemble Cyclone Centers platform.

Adding a model is a DATA edit (append an ``EnsModelSpec``), not a code change -
the ingest/detect/assemble pipeline reads every model-specific knob from here.
This mirrors the house pattern in ``hafs_render/hafs_registry.py`` (frozen
dataclasses + a ``{slug: spec}`` dict + accessors) and the "registry-as-data"
discipline in ``CYCLOLAB_DESIGN.md``.

Stage 1 ships ECMWF ENS ("ecens") only. The other four planned models
(AIFS-ENS, GEFS, GDM-FNV3, GDM-GenCast) plus the SUPER-ENSEMBLE are documented
in ``ENSEMBLE_DESIGN.md`` and land as later registry entries against this same
schema and the same model-agnostic JSON.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# Per-cycle MSLP-only download is ~2.8 GB at 00/12Z, ~1.6 GB at 06/18Z when the
# param is byte-range subset to "msl" (measured live; ~0.6 MB/member/step).

# ECMWF ENS forecast steps (verified live, 2026-06-13):
#   00/12Z: 0..144 by 3, then 150..360 by 6  -> 85 steps
#   06/18Z: 0..144 by 3                        -> 49 steps
STEPS_LONG: List[int] = list(range(0, 145, 3)) + list(range(150, 361, 6))
STEPS_SHORT: List[int] = list(range(0, 145, 3))

# AIFS-ENS (ECMWF's AI ensemble, open-data model "aifs-ens") is plain 6-hourly to
# 360 h for EVERY cycle hour (00/06/12/18Z), control included - no long/short
# split, no 3-hourly head. Verified live 2026-06-13: 50 perturbed + cf control,
# msl + gh(300/500) at 0..360 by 6, same 721x1440 0.25 deg grid as IFS ENS.
STEPS_AIFS: List[int] = list(range(0, 361, 6))   # 61 steps

# GEFS 0.5 deg (pgrb2ap5) forecast steps, identical for EVERY cycle hour
# (00/06/12/18Z all reach f384). Verified live 2026-06-14: the bucket publishes
# 3-hourly to 240 h then 6-hourly to 384 h; we SAMPLE 3-hourly to 192 h then
# 6-hourly to 384 h (the same shape as ECMWF ENS: dense early, coarse in the long
# range), so the viewer scrubs the full 384 h horizon.
STEPS_GEFS: List[int] = list(range(0, 193, 3)) + list(range(198, 385, 6))   # 97 steps


# --- pressure bins (Andrew's five) ---------------------------------------
# The thresholds are canonical and model-agnostic; they drive both the detect
# output and the viewer legend. COLORS are intentionally NOT defined here - the
# viewer owns the dot palette (Andrew's reference ramp), see models/enscenters.js
# PRESSURE_BIN_COLORS. Labels avoid em-dashes per the house on-screen-text rule.
@dataclass(frozen=True)
class PressureBin:
    key: str
    label: str
    lo: Optional[float]   # inclusive lower bound (hPa), None = open below
    hi: Optional[float]   # exclusive upper bound (hPa), None = open above


PRESSURE_BINS: Tuple[PressureBin, ...] = (
    PressureBin("gt1000", ">1000 hPa", 1000.0, None),
    PressureBin("p990_1000", "990 to 1000 hPa", 990.0, 1000.0),
    PressureBin("p970_990", "970 to 990 hPa", 970.0, 990.0),
    PressureBin("p950_970", "950 to 970 hPa", 950.0, 970.0),
    PressureBin("lt950", "<950 hPa", None, 950.0),
)


def pressure_bins_json() -> List[dict]:
    return [{"key": b.key, "label": b.label, "lo": b.lo, "hi": b.hi}
            for b in PRESSURE_BINS]


# --- detection knobs (per model, tunable to match reference density) ------
@dataclass(frozen=True)
class DetectParams:
    min_footprint_deg: float = 2.5
    closed_threshold_hpa: float = 2.0
    search_radius_km: float = 500.0
    n_azimuth: int = 16
    n_radial: int = 12
    dedup_km: float = 250.0
    # Detection is FULLY GLOBAL: centers to ~|lat| 88 so the viewer's Hemisphere
    # and Global region crops are populated (the region is a client-side view
    # crop, not a detection limit). The polar cap (>88) is dropped - the 0.25 deg
    # grid is degenerate there and the closed test's geometry is unreliable.
    lat_limit: Optional[float] = 88.0
    max_central_hpa: float = 1015.0

    def as_kwargs(self) -> dict:
        return {
            "min_footprint_deg": self.min_footprint_deg,
            "closed_threshold_hpa": self.closed_threshold_hpa,
            "search_radius_km": self.search_radius_km,
            "n_azimuth": self.n_azimuth,
            "n_radial": self.n_radial,
            "dedup_km": self.dedup_km,
            "lat_limit": self.lat_limit,
            "max_central_hpa": self.max_central_hpa,
        }


# --- warm-core (tropical-only) filter knobs ------------------------------
# Applied to SELF-DETECTED centers only (see enscenters.warmcore). The defaults
# are the community-standard upper-level thickness test: a closed THK (gh300 -
# gh500) max within ~1 deg of the surface low, falling >= ~6 m (58.8 m^2/s^2 / g)
# within 6.5 deg, plus |lat| <= 50 and a high-terrain (thermal-low) mask.
@dataclass(frozen=True)
class WarmCoreParams:
    max_lat: float = 50.0
    terrain_max_m: Optional[float] = 1000.0
    bg_box_deg: float = 10.0          # boxcar half-width for the background mean (anomaly)
    warm_anom_min_m: float = 6.0      # minimum core thickness ANOMALY (warm-core amplitude)
    search_max_deg: float = 1.0
    closed_drop_m: float = 6.0
    closed_radius_deg: float = 6.5
    n_azimuth: int = 16
    n_radial: int = 12

    def as_kwargs(self) -> dict:
        return {
            "max_lat": self.max_lat,
            "terrain_max_m": self.terrain_max_m,
            "bg_box_deg": self.bg_box_deg,
            "warm_anom_min_m": self.warm_anom_min_m,
            "search_max_deg": self.search_max_deg,
            "closed_drop_m": self.closed_drop_m,
            "closed_radius_deg": self.closed_radius_deg,
            "n_azimuth": self.n_azimuth,
            "n_radial": self.n_radial,
        }


# --- model spec ----------------------------------------------------------
@dataclass(frozen=True)
class EnsModelSpec:
    slug: str                       # R2 subdir + manifest model slug, e.g. "ecens"
    label: str                      # human label, e.g. "ECMWF ENS"
    source: str                     # ingest backend, e.g. "ecmwf-opendata"
    # How centers are produced: "self_detect" = our MSLP closed-low detector +
    # warm-core filter (ECMWF ENS, AIFS-ENS); "genesis_tracks" = parse NOAA's
    # already-TC-filtered ensemble genesis tracker ATCF (GEFS) - a light path
    # with no field pull / detect / warmcore (see enscenters.tracks).
    source_kind: str = "self_detect"
    # Optional model-specific viewer caption suffix (None -> the default
    # Atkinson-Holliday / ECMWF text). GEFS sets this because its vmax is the
    # model's own wind, not an AH estimate, and the data source differs.
    caption: Optional[str] = None
    # --- ecmwf-opendata ingest config ---
    od_model: str = "ifs"           # ecmwf-opendata model
    od_resol: str = "0p25"
    ens_stream: str = "enfo"        # perturbed-member stream
    pf_type: str = "pf"             # perturbed-member type
    n_perturbed: int = 50
    # Control source: post-50r1 the ENS control lives at (stream, type) =
    # ("oper", "fc") (it is identical to HRES); pre-50r1 it was ("enfo", "cf").
    control_stream: Optional[str] = "oper"
    control_type: Optional[str] = "fc"
    param: str = "msl"
    param_id: int = 151             # cfgrib paramId for msl
    type_of_level: str = "meanSea"  # informational: msl's GRIB typeOfLevel
    steps_long: List[int] = field(default_factory=lambda: list(STEPS_LONG))
    steps_short: List[int] = field(default_factory=lambda: list(STEPS_SHORT))
    # Control (HRES, oper/fc) publishes a SHORTER horizon than the perturbed
    # members: 240h at 00/12Z, 90h at 06/18Z. The completeness gate HEADs the
    # control's OWN terminal step (not the perturbed 360/144) so a cycle is only
    # "complete" once BOTH streams have fully disseminated.
    control_step_long: int = 240
    control_step_short: int = 90
    # --- detect config ---
    detect: DetectParams = field(default_factory=DetectParams)
    # --- warm-core (tropical-only) filter on the SELF-DETECTED centers ---
    # True for every model that self-detects MSLP (ECMWF ENS, AIFS-ENS). When on,
    # the ingest also pulls gh at gh_levels to build the thickness field. The
    # already-TC-only models would ingest tracks via a different adapter and set
    # this False.
    warm_core: bool = True
    warm_core_params: WarmCoreParams = field(default_factory=WarmCoreParams)
    # Upper-level thickness layer for the warm-core test. IFS ENS publishes gh
    # (geopotential HEIGHT, gpm) so gh_to_gpm=1.0; AIFS-ENS publishes NO gh, only
    # z (geopotential, m^2/s^2), so it uses param=z, paramId 129, and gh_to_gpm
    # =1/g to convert the layer difference to thickness in gpm. THK is then in the
    # same units (gpm) for both, so warmcore.py is unchanged.
    gh_param: str = "gh"                       # thickness param: "gh" (IFS) or "z" (AIFS)
    gh_levels: Tuple[int, int] = (300, 500)    # thickness layer top, bottom (hPa)
    gh_param_id: int = 156                      # cfgrib paramId: 156 (gh) / 129 (z)
    gh_to_gpm: float = 1.0                       # factor: 1.0 (gh) / 1/9.80665 (z->gpm)
    # Grid label stamped into the per-cycle JSON (informational; the viewer shows
    # it). 0.25 deg for the ECMWF open-data models, 0.5 deg for GEFS pgrb2ap5.
    grid_label: str = "0.25 deg"
    # --- provenance ---
    attribution: str = "ECMWF open data (CC-BY-4.0)"

    def steps_for_cycle_hour(self, hour: int) -> List[int]:
        return list(self.steps_long) if hour in (0, 12) else list(self.steps_short)

    def pf_terminal_step(self, hour: int) -> int:
        """Perturbed-member terminal forecast hour for this cycle hour (360/144)."""
        return self.steps_long[-1] if hour in (0, 12) else self.steps_short[-1]

    def control_terminal_step(self, hour: int) -> int:
        """Control (oper/fc) terminal forecast hour for this cycle hour (240/90)."""
        return self.control_step_long if hour in (0, 12) else self.control_step_short

    def member_ids(self) -> List[str]:
        ids = ["CTL"] if self.control_stream else []
        ids += [f"P{n:02d}" for n in range(1, self.n_perturbed + 1)]
        return ids


def member_label(member_id: str) -> str:
    if member_id == "CTL":
        return "Control"
    return "Perturbed " + member_id[1:]


# --- THE REGISTRY --------------------------------------------------------
# Stage 1: ECMWF ENS only. Tuned defaults; closed_threshold/search_radius are
# the density knobs to adjust against Andrew's reference plot.
_SPECS: Tuple[EnsModelSpec, ...] = (
    EnsModelSpec(
        slug="ecens",
        label="ECMWF ENS",
        source="ecmwf-opendata",
    ),
    # AIFS-ENS ("ECAIE"): ECMWF ENS's AI twin. Pure config - it inherits the
    # detector, warm-core filter, currency core, cache-version helper, viewer,
    # regions, GIF, and run selector unchanged. open-data differences from IFS
    # ENS: model="aifs-ens"; control is enfo/cf (NOT oper/fc); 6-hourly to 360 h
    # for ALL cycle hours (control included), so steps_long == steps_short and
    # both terminals are 360.
    EnsModelSpec(
        slug="ecaie",
        label="AIFS-ENS",
        source="ecmwf-opendata",
        od_model="aifs-ens",
        ens_stream="enfo",
        pf_type="pf",
        n_perturbed=50,
        control_stream="enfo",
        control_type="cf",
        steps_long=list(STEPS_AIFS),
        steps_short=list(STEPS_AIFS),
        control_step_long=STEPS_AIFS[-1],   # control runs to 360 h too
        control_step_short=STEPS_AIFS[-1],
        # AIFS-ENS has NO gh; use z (geopotential) at 300/500 and /g for thickness.
        gh_param="z",
        gh_param_id=129,
        gh_to_gpm=1.0 / 9.80665,
    ),
    # GEFS: NOAA's ensemble - now the SAME methodology as ECMWF ENS / AIFS-ENS.
    # It SELF-DETECTS closed MSLP lows from the 0.5 deg "a" fields (pgrb2ap5 on
    # the public noaa-gefs-pds S3 bucket) and runs the identical warm-core
    # thickness filter, so all three models form one clean super-ensemble. The
    # ingest is the S3 .idx byte-range backend (enscenters.gefs_ingest); detect,
    # warm-core, currency, viewer, regions, GIF and run-selector are all reused
    # unchanged. 31 members (gec00 control + gep01..gep30), 4x/day, full 384 h.
    # GEFS publishes HGT (geopotential HEIGHT, gpm), so THK = HGT300 - HGT500
    # directly (gh_param "gh", gh_to_gpm 1.0). vmax is Atkinson-Holliday from the
    # central pressure, SAME as ECMWF/AIFS (not the genesis tracker's 10 m wind).
    EnsModelSpec(
        slug="gefs",
        label="GEFS",
        source="noaa-gefs-aws",
        source_kind="self_detect",
        n_perturbed=30,                 # gec00 control + gep01..gep30 = 31 members
        control_stream="gec00",         # truthy so member_ids() includes CTL
        control_type="ctl",             # (GEFS gate is in gefs_ingest, not used here)
        param="prmsl",
        param_id=151,                   # PRMSL (mean sea level)
        steps_long=list(STEPS_GEFS),
        steps_short=list(STEPS_GEFS),   # GEFS reaches 384 h for every cycle hour
        control_step_long=STEPS_GEFS[-1],
        control_step_short=STEPS_GEFS[-1],
        warm_core=True,                 # self-detected -> warm-core thickness filter
        gh_param="gh",                  # GEFS HGT is geopotential height (gpm)
        gh_param_id=156,
        gh_to_gpm=1.0,
        gh_levels=(300, 500),
        grid_label="0.5 deg",
        attribution="NOAA GEFS 0.5 deg open data (noaa-gefs-pds)",
        caption=("Self-detected closed MSLP lows from the GEFS 0.5 deg ensemble, "
                 "filtered to warm-core tropical systems by the same upper-level "
                 "thickness test as the ECMWF models. Peak winds are an "
                 "Atkinson-Holliday estimate from central pressure. "
                 "Data: NOAA GEFS (noaa-gefs-pds)."),
    ),
    # Roadmap (later stages, ENSEMBLE_DESIGN.md): "gdm_fnv3", "gdm_gencast",
    # and a derived "super" entry.
)

REGISTRY = {s.slug: s for s in _SPECS}
DEFAULT_MODEL = _SPECS[0].slug


def get_spec(slug: str) -> EnsModelSpec:
    if slug not in REGISTRY:
        raise KeyError(f"unknown ensemble model {slug!r}; have {list(REGISTRY)}")
    return REGISTRY[slug]


def model_slugs() -> List[str]:
    return list(REGISTRY)


def models_meta() -> List[dict]:
    """The manifest's model list (slug + label), in registry order."""
    return [{"slug": s.slug, "label": s.label} for s in _SPECS]
