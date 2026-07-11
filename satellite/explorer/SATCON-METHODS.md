# Objective Intensity Consensus — method notes

Research-first notes for the TC-Diagnostics board's intensity consensus
(`satcon.js`) and its MW-imager member (`tcprimed/mwi*.py`). Compiled
2026-07-11 from primary sources, in the same discipline as
OBJFIX-METHODS.md: every constant in the implementation traces to a citation
below, and anything the sources do not state is a FLAGGED departure — never
silently invented.

**HONESTY CONTRACT (non-negotiable):** everything shown is an AUTOMATED
OBJECTIVE SATELLITE ESTIMATE — experimental, never official, never a
replacement for NHC/JTWC. The consensus is presented as **TAT's own objective
consensus USING the published SATCON method** — it is NOT the CIMSS SATCON
product (different members, different fitted error tables). Uncertainty is
always visible; when the method's own membership rule is unmet, no number is
shown.

## A. The consensus method — SATCON (Velden & Herndon 2020)

Source: Velden, C. S., and D. Herndon, 2020: *A Consensus Approach for
Estimating Tropical Cyclone Intensity from Meteorological Satellites:
SATCON.* Wea. Forecasting, **35**, 1645–1662, doi:10.1175/WAF-D-20-0015.1
(full text + all 8 tables + Figs. 4–5 read). Operational recency behavior
from the CIMSS SATCON explanation page
(tropic.ssec.wisc.edu/misc/satcon/info.html).

- **Weights are situational RMSEs** ("The SATCON weights are proportional to
  the member RMSE values for given situations", §2c), with separate weight
  sets for MSW and MSLP (§2b–c).
- **The printed 3-member combination equation** (§2c, verbatim):
  `SATCON = [W1W2(W1+W2)E3 + W1W3(W1+W3)E2 + W2W3(W3+W2)E1] / [W1W2(W1+W2) +
  W1W3(W1+W3) + W2W3(W3+W2)]` — each member's blend coefficient is built from
  the OTHER members' RMSEs: `coeff(Ei) = (Π_{j≠i} Wj)·(Σ_{j≠i} Wj)`.
- **Membership:** "At least two coincident members must be available … (the
  ADT is always one member)"; members combine within a 2-h coincidence
  window, up to four (§2c).
- **Recency (operational):** MW estimates keep full weight to 3 h, then the
  weight decays exponentially "approaching zero once the estimate is older
  than 6 hours" (CIMSS info page; decay constant unpublished).
- **ADT situational RMSE (MSW), by IR scene type** (Fig. 4a–c): EYE 11 kt ·
  CDO 13 kt · SHEAR 16 kt. ADT MSLP RMSE 9.3 hPa (Table 3).
- **Uncertainty:** SATCON displays situational **2-standard-deviation error
  bounds** (§2e); the computation and floors are unpublished.
- **Published validation** (recon-verified): dependent 2006–14 N=3167 —
  SATCON MSW MAE 7.2 kt / RMSE 9.0 kt vs ADT 9.8/12.0 (Table 2); independent
  2015–19 N=568 — SATCON 7.6/9.8 vs ADT 10.8/12.9 (Table 4); SATCON beats a
  simple average by ~10–15% (§3).

**Implementation (satcon.js) departures, each flagged in the header:**
D1 the 2-/4-member forms are unpublished — the same product–sum rule is used
(n=2 reduces to inverse-MSE weights); D2 decay e-fold 45 min chosen inside
the published 3-h/6-h envelope; D3 band = max(2σ_blend, half member spread,
±10 kt) — the ±10 kt floor tracks the Dvorak/ADT per-estimate scale; D4 no
pressure→wind member (V&H §2a(3) 0.75/0.25 blend needs agency environmental
pressure); D5 no endpoint bias adjustments (V&H §2c describes ~10-kt-order
corrections >85 kt and in the first ~36 h, values unpublished); D6 no
Schwerdt motion adjustment; D7 member bias correction only where TAT has
validated numbers (the MW member's leave-one-year-out per-bin bias); D8 ADT
weight degradations for weak ARCHER fix (×1.25) / LUT input (×1.15) are TAT
additions in the spirit of V&H's situational weighting.

## B. The MW-imager member (tcprimed/mwi.py + mwi_fit.py)

An interpretable multiple-linear model mapping storm-centered 89/37-GHz PCT
structure to best-track Vmax (and MSLP), trained offline on NOAA/CIRA
TC-PRIMED (final tier) and applied per overpass in the tcprimed cron. The
committed model JSON (`tcprimed/mwi_model_v1.json`) carries coefficients,
quality gate, leave-one-year-out error tables (overall / by intensity bin /
by sensor / by year) and full training provenance.

- **PCT:** Spencer, Goodman & Hood 1989 (JTECH 6, 254–273, eq. 4):
  PCT85 = 1.818·V − 0.818·H. Frequency-specific coefficients per Cecil &
  Chronis 2018 (JAMC 57, 2249–2259, Table 1). Both variants per band are
  extracted; the fit selects.
- **Predictor geometry:** Cecil & Zipser 1999 (MWR 127, 103–123): 0–1°
  (0–100 km) areal-mean PCT85 vs concurrent Vmax r = −0.54 (Fig. 6); the
  mean-PCT and moderate-rain area fraction (PCT ≤ 250 K) carry the signal,
  minima are weaker (Tables 2–3). Jones, Cecil & DeMaria 2006 (WAF 21,
  613–635, SHIPS-MI, §2d): predictors from the 0–100 km disk (larger radii
  tested and discarded); ≥90% valid-ocean coverage rule; STDQM quadrant-mean
  symmetry; 6-h overpass thinning for serial correlation (§3a) — the trainer
  matches all three.
- **37-GHz ring:** Kieper & Jiang 2012 (GRL 39, L13804): a ≥90%-closed ring
  of the NRL 37color cyan+pink classes around the warm center;
  P(RI|ring) = 38% vs 9% climatology, 74% with favorable environment
  (Table 3). Class Kelvin bounds from Jiang, Zagrodnik, Tao & Zipser 2018
  (JGR-Atmos 123, Table 2): GREEN (precip-free) PCT37 > 270 K ∧ H37 < 225 K;
  BRIGHT CYAN PCT37 ≥ 275 K ∧ H37 ≥ 255 K; PINK (deep convection)
  PCT37 ≤ 260 K; PCT37 = 2.18·V − 1.18·H (K&J12 exact). The detector fits a
  free 30-km annulus (15–120 km, JHT-style; IHC15/16 JHT decks) about the
  official-track center and requires ≥90% azimuthal closure. Companion
  fractions: JHT `fracDark`/`fracBright` (cyan+pink / bright classes within
  100 km) and the standalone 85-GHz RII fraction (area with PCT89 < 275 K;
  RI threshold 0.69, IHC15 deck).
- **Training target:** best-track Vmax (kt, 1-min) linearly interpolated to
  overpass time — TC-PRIMED `overpass_storm_metadata/intensity` (Razin et
  al. 2023, BAMS, TC PRIMED). Land gate from Natural Earth polygons (the
  live PPS tier has no per-pixel surface class), plus coverage gates on the
  inner core and eyewall annulus.
- **Validation:** leave-one-year-out across all training seasons; MAE/RMSE/
  bias reported overall, by intensity bin, by sensor, by year — published in
  the model JSON and surfaced by the manifest's `intensity_model` card so
  the consensus client weights members from the same numbers.

**Departures (flagged in mwi.py):** n1 fixed analysis grid (3.8°/0.04°)
via the render regrid; n2 ring/sector superset storage; n3 official-track
center (no MW re-centering in v1); n4 flat-earth km distances (diag_core
convention); n5 Natural Earth land mask in BOTH training and runtime. The
cold-area fractions are evaluated on ~5 km × 15° bin means (near footprint
scale in the inner core) rather than raw pixels — a documented
approximation.

## C. Data path

- ADT member: `window.ObjFix` per-frame records from the objfix loop workup
  (the dashboard's member #1) — scene type, CI→Vmax/MSLP, ARCHER confidence.
- MW member: `microwave/manifest.json` (`intensity_model` card) +
  `microwave/{slug}/overpasses.json` (`intensity{}` per overpass), written by
  the tcprimed cron (archive tier: TC-PRIMED; live tier: NASA PPS NRT 1C).
- Storm identity: the ATCF id embedded in the cockpit storm id
  (`JTWC_WP072026` → `wp072026`); archive views without an identity honestly
  show no consensus.
