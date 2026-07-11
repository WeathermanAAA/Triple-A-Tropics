/* diag_core.js — pure-compute TC diagnostics for the storm-analysis
 * dashboard: the IR radial-profile column (Hovmöller input) and the
 * Deviation-Angle Variance (DAV). No DOM: works in the browser
 * (window.TCDiagCore), inside the objfix Web Worker (importScripts), and in
 * node (module.exports) for the unit tests.
 *
 * PROVENANCE — implemented from primary literature, read 2026-07-11:
 *
 * RADIAL PROFILE / HOVMÖLLER (azimuthal-mean BT vs radius vs time):
 *   Kossin 2002 (MWR 130, 2260–2270): radius–time diagrams of azimuthally
 *     averaged T_BB, 4-km radial bins to r=600 km, averaging only where the
 *     circle is contained in the image (coverage honesty).
 *   Ditchek et al. 2019 (MWR 147, 591–605): objective radius–time treatment
 *     on GridSat-B1; azimuthal bins at grid resolution; areal coverage of a
 *     radial bin must exceed a threshold (they use the coverage median, 27%)
 *     before the bin is trusted.
 *   Dunion, Thorncroft & Velden 2014 (MWR 142, 3900–3919): azimuthal means
 *     over 100–600 km; the TC diurnal pulse reads as outward-propagating
 *     cooling (~5–10 m/s; 8–14 m/s in Ditchek's climatology).
 *
 * DAV (IR axisymmetry index; low = organized):
 *   Piñeros, Ritchie & Tyo 2008 (IEEE TGRS 46, 3574–3580) — the technique;
 *   Piñeros, Ritchie & Tyo 2011 (WAF 26, 690–698) — intensity estimation;
 *   Ritchie et al. 2012 (WAF 27, 1264–1277) — N Atlantic, center-based DAV:
 *     low-pass (5×5 Gaussian, σ²=1 px², per the Piñeros 2009 dissertation
 *     §3.2), SOBEL gradient, DIRECTION only (never magnitude), deviation
 *     angle folded to −90°..+90° (a gradient along the radial, inward OR
 *     outward, is 0), sample variance in deg² over ALL pixels in a fixed
 *     disk about the center (no BT mask in the center-based variant),
 *     images first resampled to a UNIFORM ~10 km grid;
 *   Ritchie et al. 2014 (WAF 29, 505–516) — WPAC/EPAC radii (300 / 200–250
 *     km; Atlantic optimum 200–250 km);
 *   Hu, Ritchie & Tyo 2020 — trailing 24-h mean instead of the 100-h IIR
 *     (the IIR destroys lead-time information); DAV leads intensity change
 *     by up to ~36 h.
 *   Reference regimes (published cases, deg², storm-centered): intense
 *     hurricanes ~600–1300 (Rita 130 kt → 593); mid-strength ~1300–1600;
 *     TD/weak ~1600–1900; disorganized ~1900–2300; uniform-random 2700.
 *
 * DOCUMENTED DEPARTURES (each deliberate, none silent):
 *   d1 center source: the objfix (ARCHER-port) per-frame fix, not best
 *      track — objective and available live; low-confidence fixes FLAG the
 *      column/point downstream (the panels dim them).
 *   d2 working grid: block-mean decimation of the lat/lon grid to ~10 km
 *      instead of a true Mercator resample; over a ≤500 km disk at TC
 *      latitudes the cell-area variation is ≤ a few % (the papers note the
 *      technique is insensitive to resolution).
 *   d3 no 8-bit quantization: BT stays float (counts were an implementation
 *      detail of the original GOES ingest, not part of the method).
 */
(function () {
  'use strict';

  var DEG = Math.PI / 180;
  var KM_PER_DEG = 111.32;

  // ---- shared small numerics ----------------------------------------------

  // block-mean decimation by integer stride (NaN-aware: a block with any
  // valid cell keeps the mean of its valid cells)
  function blockMean(bt, nr, nc, stride) {
    if (stride <= 1) return { bt: bt, nr: nr, nc: nc };
    var mr = Math.floor(nr / stride), mc = Math.floor(nc / stride);
    var out = new Float64Array(mr * mc);
    for (var i = 0; i < mr; i++) {
      for (var j = 0; j < mc; j++) {
        var s = 0, n = 0;
        for (var a = 0; a < stride; a++) {
          var base = (i * stride + a) * nc + j * stride;
          for (var b = 0; b < stride; b++) {
            var v = bt[base + b];
            if (v === v) { s += v; n++; }
          }
        }
        out[i * mc + j] = n ? s / n : NaN;
      }
    }
    return { bt: out, nr: mr, nc: mc };
  }

  // 5x5 Gaussian, sigma^2 = 1 px^2 [Piñeros 2009 diss. §3.2] — separable.
  var G5 = (function () {
    var k = [], s = 0;
    for (var x = -2; x <= 2; x++) { var w = Math.exp(-x * x / 2); k.push(w); s += w; }
    for (var i = 0; i < 5; i++) k[i] /= s;
    return k;
  })();
  function gauss5(a, nr, nc) {
    var t = new Float64Array(nr * nc), o = new Float64Array(nr * nc);
    var i, j, k, v, w, s, n;
    for (i = 0; i < nr; i++) {
      for (j = 0; j < nc; j++) {
        s = 0; n = 0;
        for (k = -2; k <= 2; k++) {
          var jj = j + k;
          if (jj < 0 || jj >= nc) continue;
          v = a[i * nc + jj];
          if (v !== v) continue;
          w = G5[k + 2]; s += v * w; n += w;
        }
        t[i * nc + j] = n > 0 ? s / n : NaN;
      }
    }
    for (i = 0; i < nr; i++) {
      for (j = 0; j < nc; j++) {
        s = 0; n = 0;
        for (k = -2; k <= 2; k++) {
          var ii = i + k;
          if (ii < 0 || ii >= nr) continue;
          v = t[ii * nc + j];
          if (v !== v) continue;
          w = G5[k + 2]; s += v * w; n += w;
        }
        o[i * nc + j] = n > 0 ? s / n : NaN;
      }
    }
    return o;
  }

  // Sobel gradient [Ritchie et al. 2012 §2b]. Returns row-derivative
  // (southward index direction) and col-derivative; NaN where any tap is NaN.
  function sobel(a, nr, nc) {
    var gr = new Float64Array(nr * nc), gc = new Float64Array(nr * nc);
    gr.fill(NaN); gc.fill(NaN);
    for (var i = 1; i < nr - 1; i++) {
      for (var j = 1; j < nc - 1; j++) {
        var p00 = a[(i - 1) * nc + j - 1], p01 = a[(i - 1) * nc + j], p02 = a[(i - 1) * nc + j + 1];
        var p10 = a[i * nc + j - 1], p12 = a[i * nc + j + 1];
        var p20 = a[(i + 1) * nc + j - 1], p21 = a[(i + 1) * nc + j], p22 = a[(i + 1) * nc + j + 1];
        if (p00 !== p00 || p01 !== p01 || p02 !== p02 || p10 !== p10 ||
            p12 !== p12 || p20 !== p20 || p21 !== p21 || p22 !== p22 ||
            a[i * nc + j] !== a[i * nc + j]) continue;
        gc[i * nc + j] = (p02 + 2 * p12 + p22) - (p00 + 2 * p10 + p20);
        gr[i * nc + j] = (p20 + 2 * p21 + p22) - (p00 + 2 * p01 + p02);
      }
    }
    return { row: gr, col: gc };
  }

  // ---- radial profile (one Hovmöller column) ------------------------------
  // field: { latArr, lonArr (deg, lat DESCENDING), bt (KELVIN, NaN=void),
  //          nr, nc } — the objfix field contract.
  // center: { lat, lon }.  opts: { maxKm=450, ringKm=10 }
  // -> { ringKm, maxKm, radii[], meanC[], p10C[], coverage[], nValid }
  function radialProfile(field, center, opts) {
    opts = opts || {};
    var maxKm = opts.maxKm || 450;
    var ringKm = opts.ringKm || 10;
    var nRings = Math.ceil(maxKm / ringKm);
    var sums = new Float64Array(nRings);
    var counts = new Int32Array(nRings);
    var totals = new Int32Array(nRings);
    var perRing = [];
    for (var r = 0; r < nRings; r++) perRing.push([]);

    var cosC = Math.cos(DEG * center.lat);
    var nValid = 0;
    for (var i = 0; i < field.nr; i++) {
      var dyKm = (field.latArr[i] - center.lat) * KM_PER_DEG;
      if (Math.abs(dyKm) > maxKm) continue;
      for (var j = 0; j < field.nc; j++) {
        var dxKm = (field.lonArr[j] - center.lon) * KM_PER_DEG * cosC;
        var rKm = Math.sqrt(dxKm * dxKm + dyKm * dyKm);
        if (rKm >= maxKm) continue;
        var ring = Math.floor(rKm / ringKm);
        totals[ring]++;
        var v = field.bt[i * field.nc + j];
        if (v !== v) continue;
        var c = v - 273.15;
        sums[ring] += c;
        counts[ring]++;
        perRing[ring].push(c);
        nValid++;
      }
    }
    var radii = new Array(nRings), meanC = new Array(nRings);
    var p10C = new Array(nRings), coverage = new Array(nRings);
    for (r = 0; r < nRings; r++) {
      radii[r] = (r + 0.5) * ringKm;
      coverage[r] = totals[r] ? counts[r] / totals[r] : 0;
      if (counts[r]) {
        meanC[r] = sums[r] / counts[r];
        var arr = perRing[r];
        arr.sort(function (a, b) { return a - b; });
        p10C[r] = arr[Math.max(0, Math.floor(0.1 * (arr.length - 1)))];
      } else { meanC[r] = null; p10C[r] = null; }
    }
    return { ringKm: ringKm, maxKm: maxKm, radii: radii, meanC: meanC,
             p10C: p10C, coverage: coverage, nValid: nValid };
  }

  // ---- DAV -----------------------------------------------------------------
  // opts: { radiusKm=250 (Atlantic optimum 200–250; WPAC 300 — Ritchie 2012/14),
  //         workKm=10 (uniform working grid) }
  // -> { varDeg2, sigmaDeg, meanDeg, nPix, coverage, radiusKm, workKm }
  function dav(field, center, opts) {
    opts = opts || {};
    var radiusKm = opts.radiusKm || 250;
    var workKm = opts.workKm || 10;

    // d2: decimate the lat/lon grid to ~workKm (papers: uniform 10 km)
    var resKm = Math.abs(field.latArr[0] - field.latArr[1]) * KM_PER_DEG;
    var stride = Math.max(1, Math.round(workKm / resKm));
    var g = blockMean(field.bt, field.nr, field.nc, stride);
    var lat0 = field.latArr[0], dLat = (field.latArr[1] - field.latArr[0]) * stride;
    var lon0 = field.lonArr[0], dLon = (field.lonArr[1] - field.lonArr[0]) * stride;
    // block centers
    var latC = function (i) { return lat0 + (i + 0.5 * (stride - 1) / stride) * dLat; };
    var lonC = function (j) { return lon0 + (j + 0.5 * (stride - 1) / stride) * dLon; };

    var sm = gauss5(g.bt, g.nr, g.nc);        // 5×5 Gaussian σ²=1 [diss. §3.2]
    var grad = sobel(sm, g.nr, g.nc);         // Sobel [Ritchie 2012 §2b]

    var cosC = Math.cos(DEG * center.lat);
    var sum = 0, sum2 = 0, n = 0, total = 0;
    for (var i = 0; i < g.nr; i++) {
      var dyKm = (latC(i) - center.lat) * KM_PER_DEG;
      if (Math.abs(dyKm) > radiusKm) continue;
      for (var j = 0; j < g.nc; j++) {
        var dxKm = (lonC(j) - center.lon) * KM_PER_DEG * cosC;
        var rKm = Math.sqrt(dxKm * dxKm + dyKm * dyKm);
        if (rKm > radiusKm || rKm < 1e-6) continue;
        total++;
        var gr = grad.row[i * g.nc + j], gc = grad.col[i * g.nc + j];
        if (gr !== gr || gc !== gc) continue;
        if (gr === 0 && gc === 0) continue;   // no defined direction
        // gradient vector in (east, north): col-derivative = east,
        // row index runs SOUTH (lat descending) so north = -row-derivative
        var gAng = Math.atan2(-gr, gc);
        var rAng = Math.atan2(dyKm, dxKm);
        var d = (gAng - rAng) / DEG;
        // fold mod 180 into −90..+90: radial-aligned (in OR out) = 0
        d = ((d % 180) + 270) % 180 - 90;
        sum += d; sum2 += d * d; n++;
      }
    }
    if (n < 30) {
      return { varDeg2: null, sigmaDeg: null, meanDeg: null, nPix: n,
               coverage: total ? n / total : 0, radiusKm: radiusKm, workKm: workKm };
    }
    var mean = sum / n;
    var variance = (sum2 - n * mean * mean) / (n - 1);   // sample variance, deg²
    return { varDeg2: variance, sigmaDeg: Math.sqrt(variance), meanDeg: mean,
             nPix: n, coverage: total ? n / total : 0,
             radiusKm: radiusKm, workKm: workKm };
  }

  // one call the objfix worker makes per frame, while the BT grid is alive
  function frameDiagnostics(field, center, opts) {
    opts = opts || {};
    var out = {};
    try { out.radial = radialProfile(field, center, opts.radial); }
    catch (e) { out.radial = null; out.radialError = String(e && e.message || e); }
    try { out.dav = dav(field, center, opts.dav); }
    catch (e) { out.dav = null; out.davError = String(e && e.message || e); }
    return out;
  }

  var TCDiagCore = {
    radialProfile: radialProfile,
    dav: dav,
    frameDiagnostics: frameDiagnostics,
    // published reference regimes for the DAV chart bands (deg²; see header)
    DAV_REGIMES: [
      { lo: 0, hi: 1300, label: 'organized / hurricane-typical' },
      { lo: 1300, hi: 1900, label: 'TS / consolidating' },
      { lo: 1900, hi: 2700, label: 'disorganized' }
    ],
    _internal: { blockMean: blockMean, gauss5: gauss5, sobel: sobel, G5: G5 }
  };

  if (typeof window !== 'undefined') window.TCDiagCore = TCDiagCore;
  if (typeof self !== 'undefined' && typeof window === 'undefined') self.TCDiagCore = TCDiagCore;
  if (typeof module !== 'undefined' && module.exports) module.exports = TCDiagCore;
})();
