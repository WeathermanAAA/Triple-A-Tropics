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
