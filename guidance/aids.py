#!/usr/bin/env python3
"""The ATCF AID CATALOG - what each aid IS, and when it is available.

``guidance.atcf`` parses decks. This module says what the parsed aid ids MEAN,
because three of the four things the guidance page must communicate are
properties of the aid rather than of the data:

  * **kind** - an ensemble MEAN is not a multi-model CONSENSUS, and a skill
    BASELINE is not a forecast. Collapsing those into one "consensus" bucket is
    how a page ends up claiming something the data does not support (see the
    JTWC note below, which is a defect this catalog exists to make impossible).
  * **timing (early vs late)** - an EARLY aid is available in time for the
    forecast cycle it is labelled with; a LATE aid is not. Mixing them silently
    is a classic error: a late aid's apparent skill is partly hindsight, because
    it saw initial conditions the forecaster did not have when the official
    forecast went out. They are badged and never blended.
  * **basin capability** - which kinds exist at all in a given basin.

BASIN CAPABILITY IS A HARD CONSTRAINT, NOT A CAVEAT.
Verified across the live 2026 decks: JTWC-basin (WP/IO/SH) a-decks have NEVER
carried official, consensus or statistical aids - 52 techs fewer than NHC, raw
ensembles only. So a "consensus" envelope in a JTWC basin is not a degraded
product, it is a fabricated one. :func:`basin_capability` is the gate, and
:func:`classify` refuses to return CONSENSUS for a JTWC-basin aid.

The concrete defect this prevents, observed live on 2026-07-28: the per-storm
guidance document for DOLPHIN (WP 12) listed ``"consensus": ["AEMN"]``. AEMN is
the GEFS ensemble MEAN - one model's members averaged - and the West Pacific
has no multi-model consensus aid at all. Presenting it as consensus tells the
reader several independent models agree, when the truth is that one model was
averaged with itself.

Stdlib only.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class AidKind(Enum):
    """What an aid IS. The distinctions here are the ones that change meaning."""

    #: The human forecast (OFCL). The thing every other aid is judged against.
    OFFICIAL = "official"
    #: Post-analysis best track (BEST, from the b-deck). Truth, not guidance.
    BEST = "best"
    #: MULTI-MODEL consensus - several independent models combined (TVCN, IVCN,
    #: RVCN, HCCA, NNIC). "Several models agree" is a real claim.
    CONSENSUS = "consensus"
    #: A SINGLE model's ensemble averaged with itself (AEMN, GDMN, AEMI, GDMI).
    #: Deliberately NOT consensus: one model's spread is not model agreement.
    ENSEMBLE_MEAN = "ensemble_mean"
    #: One perturbed member (AC00, AP01..AP30).
    ENSEMBLE_MEMBER = "ensemble_member"
    #: A dynamical model integration (AVNO, HWRF, HFSA, CMC, NVGM, UKX, CTCX).
    DYNAMICAL = "dynamical"
    #: A statistical-dynamical intensity aid (DSHP, SHIP, LGEM).
    STATISTICAL = "statistical"
    #: NO-SKILL REFERENCE (OCD5, CLP5, SHF5). Not a forecast: the bar every
    #: real aid has to clear. A guidance chart without one cannot tell you
    #: whether any aid is adding value.
    SKILL_BASELINE = "skill_baseline"
    #: Anything unrecognised. Rendered, but never counted as any of the above.
    OTHER = "other"


class AidTiming(Enum):
    """Whether an aid arrives in time for the cycle it is labelled with."""

    #: Available before the forecast deadline - interpolated/adjusted model
    #: output, and everything computed at cycle time (official, consensus,
    #: statistical, baselines).
    EARLY = "early"
    #: Raw model output, which lands AFTER the deadline. Comparing a late aid
    #: to the official forecast flatters the aid: it is partly hindsight.
    LATE = "late"
    #: Best track / analysis - the timing axis does not apply.
    NOT_APPLICABLE = "n/a"


#: Basins where NHC publishes the full aid suite.
NHC_BASINS = frozenset({"al", "ep", "cp"})
#: Basins served by JTWC decks: raw ensembles only (see the module docstring).
JTWC_BASINS = frozenset({"wp", "io", "sh"})

#: Explicit per-aid classification. Anything not listed falls through to the
#: suffix rules in :func:`classify`, which are what actually carry the 31 GEFS
#: members and any aid added upstream after this table was written.
_EXPLICIT = {
    "OFCL": (AidKind.OFFICIAL, AidTiming.EARLY),
    "OFCI": (AidKind.OFFICIAL, AidTiming.EARLY),
    "BEST": (AidKind.BEST, AidTiming.NOT_APPLICABLE),
    "CARQ": (AidKind.BEST, AidTiming.NOT_APPLICABLE),

    # Multi-model consensus.
    "TVCN": (AidKind.CONSENSUS, AidTiming.EARLY),
    "TVCE": (AidKind.CONSENSUS, AidTiming.EARLY),
    "TVCX": (AidKind.CONSENSUS, AidTiming.EARLY),
    "IVCN": (AidKind.CONSENSUS, AidTiming.EARLY),
    "RVCN": (AidKind.CONSENSUS, AidTiming.EARLY),
    "HCCA": (AidKind.CONSENSUS, AidTiming.EARLY),
    "NNIC": (AidKind.CONSENSUS, AidTiming.EARLY),

    # Single-model ensemble MEANS - not consensus.
    "AEMN": (AidKind.ENSEMBLE_MEAN, AidTiming.LATE),
    "AEMI": (AidKind.ENSEMBLE_MEAN, AidTiming.EARLY),
    "AEM2": (AidKind.ENSEMBLE_MEAN, AidTiming.EARLY),
    "GDMN": (AidKind.ENSEMBLE_MEAN, AidTiming.LATE),
    "GDMI": (AidKind.ENSEMBLE_MEAN, AidTiming.EARLY),
    "CEMN": (AidKind.ENSEMBLE_MEAN, AidTiming.LATE),
    "CEMI": (AidKind.ENSEMBLE_MEAN, AidTiming.EARLY),

    # No-skill baselines.
    "OCD5": (AidKind.SKILL_BASELINE, AidTiming.EARLY),
    "CLP5": (AidKind.SKILL_BASELINE, AidTiming.EARLY),
    "SHF5": (AidKind.SKILL_BASELINE, AidTiming.EARLY),
    "TCLP": (AidKind.SKILL_BASELINE, AidTiming.EARLY),
    "XTRP": (AidKind.SKILL_BASELINE, AidTiming.EARLY),

    # Statistical-dynamical intensity.
    "DSHP": (AidKind.STATISTICAL, AidTiming.EARLY),
    "SHIP": (AidKind.STATISTICAL, AidTiming.EARLY),
    "LGEM": (AidKind.STATISTICAL, AidTiming.EARLY),
    "DRCL": (AidKind.STATISTICAL, AidTiming.EARLY),

    # Raw dynamical runs (LATE) and their interpolated twins (EARLY).
    "AVNO": (AidKind.DYNAMICAL, AidTiming.LATE),
    "AVNI": (AidKind.DYNAMICAL, AidTiming.EARLY),
    "AVN2": (AidKind.DYNAMICAL, AidTiming.EARLY),
    "HWRF": (AidKind.DYNAMICAL, AidTiming.LATE),
    "HWFI": (AidKind.DYNAMICAL, AidTiming.EARLY),
    "HMON": (AidKind.DYNAMICAL, AidTiming.LATE),
    "HMNI": (AidKind.DYNAMICAL, AidTiming.EARLY),
    # HFSA/HFSB are the LIVE HAFS ids. HAFA/HAFB are defined in
    # nhc_techlist.dat and have ZERO live rows - never key on them.
    "HFSA": (AidKind.DYNAMICAL, AidTiming.LATE),
    "HFAI": (AidKind.DYNAMICAL, AidTiming.EARLY),
    "HFSB": (AidKind.DYNAMICAL, AidTiming.LATE),
    "HFBI": (AidKind.DYNAMICAL, AidTiming.EARLY),
    "CMC":  (AidKind.DYNAMICAL, AidTiming.LATE),
    "CMCI": (AidKind.DYNAMICAL, AidTiming.EARLY),
    "NVGM": (AidKind.DYNAMICAL, AidTiming.LATE),
    "NVGI": (AidKind.DYNAMICAL, AidTiming.EARLY),
    "CTCX": (AidKind.DYNAMICAL, AidTiming.LATE),
    "CTCI": (AidKind.DYNAMICAL, AidTiming.EARLY),
    "UKX":  (AidKind.DYNAMICAL, AidTiming.LATE),
    "UKXI": (AidKind.DYNAMICAL, AidTiming.EARLY),
    "UKX2": (AidKind.DYNAMICAL, AidTiming.EARLY),
}

#: Human labels. Only where the id is not self-explanatory.
LABELS = {
    "OFCL": "NHC official forecast",
    "BEST": "Best track (post-analysis)",
    "TVCN": "Track consensus (variable)",
    "IVCN": "Intensity consensus (variable)",
    "RVCN": "Track consensus (regional, variable)",
    "HCCA": "HFIP corrected-consensus",
    "NNIC": "Neural-net intensity consensus",
    "AEMN": "GEFS ensemble mean",
    "AEMI": "GEFS ensemble mean (interpolated)",
    "GDMN": "GEFS ensemble mean (GFDL tracker)",
    "GDMI": "GEFS ensemble mean (interpolated)",
    "AC00": "GEFS control member",
    "OCD5": "OCD5 (climatology + persistence, no-skill baseline)",
    "CLP5": "CLIPER5 (climatology + persistence track baseline)",
    "SHF5": "SHIFOR5 (climatology + persistence intensity baseline)",
    "AVNO": "GFS", "AVNI": "GFS (interpolated)",
    "HWRF": "HWRF", "HWFI": "HWRF (interpolated)",
    "HMON": "HMON", "HMNI": "HMON (interpolated)",
    "HFSA": "HAFS-A", "HFAI": "HAFS-A (interpolated)",
    "HFSB": "HAFS-B", "HFBI": "HAFS-B (interpolated)",
    "CMC": "CMC/GEM", "CMCI": "CMC/GEM (interpolated)",
    "NVGM": "NAVGEM", "NVGI": "NAVGEM (interpolated)",
    "CTCX": "COAMPS-TC", "CTCI": "COAMPS-TC (interpolated)",
    "UKX": "UKMET (GFS tracker)", "UKXI": "UKMET (interpolated)",
    "DSHP": "DSHIPS (statistical-dynamical intensity)",
    "SHIP": "SHIPS (statistical-dynamical intensity)",
    "LGEM": "LGEM (logistic growth intensity)",
}


def _is_gefs_member(tech: str) -> bool:
    """``AC00`` (control) or ``AP01``..``AP30`` (perturbed)."""
    if tech == "AC00":
        return True
    return (len(tech) == 4 and tech.startswith("AP") and tech[2:].isdigit()
            and 1 <= int(tech[2:]) <= 30)


def classify(tech: str, basin: Optional[str] = None) -> tuple:
    """``(AidKind, AidTiming)`` for an aid id, optionally basin-aware.

    Falls through to suffix rules for anything not explicitly tabled, so the 31
    GEFS members and any upstream addition are still classified rather than
    silently bucketed as OTHER.

    BASIN GATE: a JTWC-basin aid is NEVER returned as CONSENSUS. Those decks
    carry no consensus aid, so an id that looks like one there is a
    misclassification, and letting it through would put a fabricated "several
    models agree" claim on the page.
    """
    tech = (tech or "").strip().upper()
    if not tech:
        return AidKind.OTHER, AidTiming.LATE

    kind, timing = _EXPLICIT.get(tech, (None, None))

    if kind is None:
        if _is_gefs_member(tech):
            # Raw members are late; there is no interpolated member product.
            kind, timing = AidKind.ENSEMBLE_MEMBER, AidTiming.LATE
        else:
            kind = AidKind.OTHER
            # Suffix rule: an interpolated ("I") or 2-cycle-old ("2") variant is
            # adjusted to the current synoptic time and IS available early.
            timing = (AidTiming.EARLY if tech.endswith(("I", "2"))
                      else AidTiming.LATE)

    if (kind is AidKind.CONSENSUS and basin
            and basin.strip().lower() in JTWC_BASINS):
        # Refuse the claim rather than repeat it (see the module docstring).
        kind = AidKind.OTHER

    return kind, timing


def label(tech: str) -> str:
    """Human label for an aid, falling back to the id itself."""
    tech = (tech or "").strip().upper()
    if tech in LABELS:
        return LABELS[tech]
    if _is_gefs_member(tech):
        return f"GEFS member {tech[2:]}"
    return tech


def basin_capability(basin: Optional[str]) -> dict:
    """What guidance a basin can honestly support.

    AL/EP/CP get the full suite. WP/IO/SH get raw ensembles only - the page must
    say so and must not draw a consensus envelope there.
    """
    b = (basin or "").strip().lower()
    if b in NHC_BASINS:
        return {
            "basin": b,
            "tier": "full",
            "source": "NHC public a-deck (ftp.nhc.noaa.gov/atcf/aid_public)",
            "has_official": True,
            "has_consensus": True,
            "has_statistical": True,
            "has_skill_baseline": True,
            "note": ("Full guidance: official forecast, multi-model consensus, "
                     "statistical intensity aids and the OCD5 no-skill "
                     "baseline are all published for this basin."),
        }
    if b in JTWC_BASINS:
        return {
            "basin": b,
            "tier": "ensemble_only",
            "source": "UCAR adecks_open (best-effort mirror)",
            "has_official": False,
            "has_consensus": False,
            "has_statistical": False,
            "has_skill_baseline": False,
            "note": ("Raw ensemble tracks only. JTWC-basin decks have never "
                     "carried official, consensus or statistical aids, so "
                     "there is no consensus envelope and no skill baseline to "
                     "show here - not a gap in our ingest. The official track "
                     "for this basin comes from the JTWC warning text, not "
                     "from a deck aid."),
        }
    return {
        "basin": b, "tier": "unknown", "source": "",
        "has_official": False, "has_consensus": False,
        "has_statistical": False, "has_skill_baseline": False,
        "note": "Unrecognised basin; no guidance capability claimed.",
    }
