# Objective center + intensity — method notes (pre-implementation)

Research-first notes for the cockpit's objective storm-center (ARCHER-style)
and objective intensity (ADT-style) feature. Compiled 2026-07-09 from primary
sources; anything the sources did not state is marked UNCONFIRMED and must not
be silently invented in the implementation.

**HONESTY CONTRACT (non-negotiable, from the product brief):** everything this
feature shows is an AUTOMATED OBJECTIVE SATELLITE ESTIMATE — never presented
as official intensity or a replacement for NHC/JTWC. Uncertainty/confidence is
always shown; poor scene type or coverage says so instead of emitting a
confident wrong number.

## A. Center fixing — ARCHER (Wimmers & Velden)

Sources: Wimmers & Velden 2010 (JAMC 49, doi:10.1175/2010JAMC2490.1);
Wimmers & Velden 2016 "ARCHER-2" (JAMC 55, 197-212, doi:10.1175/JAMC-D-15-0098.1);
CIMSS "How ARCHER works" (groups.ssec.wisc.edu/groups/archer/how-archer-works);
the author's released implementation (github.com/ajwimmers/archer —
archer4_visir.py, archer4.py, utilities/ScoreFuncs.py), from which the exact
constants below are read; NHC JHT presentation (nhc.noaa.gov/jht/ihc_15/s3a-05-wimmers.pdf).

- **Spiral score:** fit a 5° log-spiral unit-vector field to the BT gradient;
  score = mean alignment (dot/cross-product form) of grad(BT) with the
  hemisphere-signed spiral direction at each pixel, per candidate center.
  Per-pixel weighting internals: UNCONFIRMED (ScoreFuncs.combo_parts_calc_3_0).
- **Ring score:** per candidate center and radius, mean dot product of the
  image gradient with the outward radius vector on the circle (warm eye edge).
  Radii searched 0-0.5° for Vis/IR. Argmax radius = eye-size estimate.
- **Combination (verbatim constants from archer4_visir.py):**
  `combo = (spiral − penalty) + ring_weight·ring`, ring_weight = 0.0167 (IR),
  0.0020 (Vis); first-guess distance penalty weight = 0.33 (functional form
  UNCONFIRMED — a gentle quadratic stand-in must be labeled an approximation);
  search grid ≈ 4 km spacing.
- **Quality gates:** reject when the combo max sits >0.5° from valid data, on
  the domain edge, or when >50% of the domain is void; rejected fixes are
  demoted to weak-center status (report as faint crosshair, never as the fix).
- **Confidence:** peak prominence of the no-penalty combo surface —
  `confidence = max − (best score >0.75° from the peak)`; maps through an
  empirical per-sensor alpha to a gamma-type error CDF
  `P(err ≤ x) = 1 − (αx+1)e^(−αx)` → report 50%/95% certainty radii.
  The confidence→alpha lookup: UNCONFIRMED (Conversions.py not read) — v1
  reports the raw prominence + a qualitative tier instead of km radii.
- **IR preprocessing:** score BT directly; ~4 km resampling; the operational
  two-pass parallax/low-cloud logic is out of scope for v1 (flagged
  simplification).

## B. Intensity — ADT (Olander & Velden)

Primary source actually read in full: **ADT Users' Guide v8.2.1** (CIMSS,
tropic.ssec.wisc.edu/misc/adt/guides/ADTV8.2.1_Guide.pdf) — the operational
rulebook. Olander & Velden 2007 (WAF 22:287-298) and 2019 (WAF 34) confirm the
scheme but were paywalled at fetch time; anything not in the Guide is marked.

- **Geometry:** eye region 0-24 km (Teye = warmest pixel); cloud region
  24-136 km annulus → per-ring warmest BT, coldest of those = Tcw at ring
  CWRN; Tcloud = mean of 24 15°-arc means over an 80 km annulus centered on
  CWRN; Sym = mean |difference| of the 12 opposing arc pairs; CDO radius from
  the −54 °C BD boundary; eye radius from the −30 °C boundary.
- **BD categories:** >+9 °C low cloud; +9→−30 Off-White; −30→−42 Dark Gray;
  −42→−54 Medium Gray; −54→−64 Light Gray; −64→−70 Black; −70→−76 White;
  −76→−80 Top Med Gray; <−80 Top Dark Gray.
- **Scene typing:** additive Eye/Cloud scene scores (formulas in the Guide,
  §3B; reproduced in the research log). The numeric cutoffs mapping score →
  scene type are UNCONFIRMED (ADT source only). v1 rule: EYE only when a warm
  eye pixel + cold surround are unambiguous; curved-band vs CDO vs SHEAR via
  the log-spiral segment counts (≥25 segments cold at Light Gray → CDO/EC;
  ≤7 → SHEAR fallback; else CURVED BAND); LARGE EYE at radius ≥38 km.
- **Raw T# (v8.x regressions, temps °C):**
  - EYE: `1.10 − 0.070·Tcloud + 0.011·(Teye − Tcloud) − 0.015·Sym`
  - CDO/EC/Irregular: `2.60 − 0.020·Tcloud + 0.002·Rcdo − 0.030·Sym`
  - SHEAR: distance-to-cold rule (≥140 km → T1.5, +0.5 per 30 km closer,
    d=80 → T2.25, ≤35 km → T3.5 max)
  - CURVED BAND: 10° log-spiral wrap fraction at the Light-Gray threshold
    (<20% → T1.5; 20-40% → 1.5-2.5; 40-100% → 2.5-4.0; to 4.5 at 120%).
- **CI → Vmax (Dvorak 1984 table; winds basin-independent):** CI 1.0→25 kt,
  1.5→25, 2.0→30, 2.5→35, 3.0→45, 3.5→55, 4.0→65, 4.5→77, 5.0→90, 5.5→102,
  6.0→115, 6.5→127, 7.0→140, 7.5→155, 8.0→170, 8.5→185. Atlantic MSLP column
  per the same table; WPac MSLP (Shewchuk & Weir 1980) UNCONFIRMED — v1 shows
  Vmax only, no MSLP for WPac.
- **Time rules:** Final T# = unweighted mean of Raw T# over the trailing 3 h;
  Rule-8 rate limits (0.5/6 h below T4.0; 1.0/1.5/2.0/2.5 per 6/12/18/24 h
  above, eye scenes +0.5, CDO/EC/CB −0.5; ≤0.5 per 1 h gross check); Rule-9
  CI# = highest Final T# over the prior 12 h, never >1.0 above current, with
  the rapid-weakening 0.5 variant; over land → no estimate.
- **Skill:** eye-scene regression r≈0.70, cloud scenes r≈0.50 (Guide §3H1) —
  CDO is the weakest scene; per-estimate RMSE figure UNCONFIRMED (2019 paper
  paywalled). The UI must show scene type + these skill tiers with the number.

## C. Data path (established this session)

- AL/EP storms: calibrated BT from the fd pyramid's per-frame `bt.png`
  (lossless u16, decode = BTProbe's formula) — clean input for both methods.
- WP storms (e.g. BAVI): floater frames are rainbow_ir-colorized WebP with
  baked chrome; BT recovery = crop chrome margins + invert the rainbow_ir
  LUT (norm −95→40 °C, verified against the generated colorbar ticks);
  coastline-pixel contamination must be median-filled and the inversion
  labeled as degraded-precision input in the confidence readout.
- First guess + storm list: `feeds/global_storms.geojson` +
  `floaters/manifest.json` (lat/lon/intensity per storm, live).
- Center-track output: reusable JSON (stamp, lat, lon, confidence, method
  flags) for the Hovmöller / floater auto-centering consumers.

Status: methods verified and frozen here; implementation lands next session
against this spec. Do not implement constants not written above without
re-verifying against the cited sources.

## D. Resolution addendum (2026-07-09, implementation session)

Every UNCONFIRMED above was resolved against primary source before
implementation (ajwimmers/archer @ d09f5c7: archer4_visir.py, ScoreFuncs.py,
Conversions.py; ADT v8.x via the SSEC McIDAS-V port Scene/Intensity/Functions
.java, cross-checked against AODT v7.2 C in Unidata/gempak). Corrections to
the text above — the source is authoritative where they differ:

- **Spiral score (exact):** grad(BT) per 0.025° cell, log-compressed
  (`g·ln(1+|g|)/|g|`); spiral unit field alpha=5°, hemisphere via sign(lat);
  score per pixel = cross(spiral, grad_log), inward full weight,
  counter-aligned ×0.5 (IR); grid score = 15·mean − 20. Analysis grid 0.025°
  over ±2.5° perimeter; spiral candidates searched on a 0.05° grid within
  2.0°, then interpolated; input first thinned to ~4 km.
- **Ring score (exact):** radii 0.05–0.50° step 0.05, 72 azimuths (reject→0
  if ≤42.5% points valid), gradient of BT^(1/3) with the author's ±1.14
  spacing, dot with inward radials, ×r^0.1, best radius kept, ×250
  internally; THEN ring_weight 0.0167 (IR) / 0.0020 (Vis) in the combo. Ring
  evaluated only in a swarm ≤1.5 score-units below / ≤0.25° around the
  penalized spiral max.
- **Penalty (CORRECTED — was flagged quadratic stand-in):** LINEAR,
  0.33 × great-circle degrees from first guess.
- **Confidence (exact, §A form confirmed):** prominence of the no-penalty
  combo beyond 0.75°; alpha = per-sensor linear fit with vmax blend
  (IR lo 9.89·c−2.07, hi 9.26·c+1.95; blend 60–85 kt; floor 0.5);
  P(err≤x) = 1−(αx+1)e^(−αx) → 50%/95% radii ARE reportable in v1.
- **Quality gates (exact):** valid fraction of the 2.5° filter disk <0.5 →
  no fix; spiral peak on grid edge (≤1 or ≥n−2) or NaN within 2 cells in a
  cardinal direction → no fix; BT < 80 K treated as void; failed fix demotes
  to weak-center (faint crosshair).
- **ADT scene cutoffs (RESOLVED):** Eye factor total ≥0.50 → EYE (large eye
  radius ≥38 km in v8.x); Cloud factor total <0 → SHEAR, ≥1/≥2/≥3 ladder →
  curved band / CDO with the source's symmetry (>40/>30 °C) + temp-diff
  (±8 °C) overrides; EMBEDDED CENTER via 10° log-spiral arc 8–20 segs.
- **Raw T# (CORRECTED — the §B linear formulas are the 2007 paper's, not the
  operational code):** v8.x interpolates BD-category base tables
  (EYE Atl: 1.00…8.25; CLD Atl: 2.00…4.70 across DG→CDG+) + the confirmed
  additive terms (eye 0.011·(Teye−Tcloud) − 0.015·Sym; cloud 0.002·Rcdo −
  0.030·Sym − 0.1 bias). SHEAR: dist {0,35,50,80,110,140} km → T#
  {3.5,3.0,2.5,2.25,2.0,1.5}. Curved band: {1.5,1.5,2.0,2.5,3.0,3.5,4.0} per
  20% wrap.
- **CI→Vmax:** Dvorak table confirmed; implement the full 0.1-step table
  (non-uniform increments), not interpolation between half points.
