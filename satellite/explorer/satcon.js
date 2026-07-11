/* satcon.js — OBJECTIVE INTENSITY CONSENSUS for the TC-Diagnostics board:
 * a time-aware weighted blend of TAT's objective intensity members using the
 * published SATCON method. Pure-math core (node-testable, no DOM) + the
 * dashboard panel. THIS IS NOT THE CIMSS SATCON PRODUCT: it is TAT's own
 * experimental consensus USING the peer-reviewed SATCON methodology, with
 * TAT's own members (the ADT port + the MW-imager model) and TAT-validated
 * member errors. The label and disclosure below state that, always.
 *
 * PROVENANCE — method from primary source, read 2026-07-11:
 *   Velden, C. S., and D. Herndon, 2020: A Consensus Approach for Estimating
 *   Tropical Cyclone Intensity from Meteorological Satellites: SATCON.
 *   Wea. Forecasting, 35, 1645-1662, doi:10.1175/WAF-D-20-0015.1  ["V&H"]
 *   + the CIMSS SATCON explanation page (tropic.ssec.wisc.edu/misc/satcon/
 *   info.html) for the operational recency rule  ["CIMSS info page"].
 *
 *   - Weights are the members' SITUATIONAL RMSEs ("The SATCON weights are
 *     proportional to the member RMSE values for given situations", V&H
 *     §2c). The 3-member combination equation (V&H §2c, verbatim):
 *       SATCON = [W1W2(W1+W2)E3 + W1W3(W1+W3)E2 + W2W3(W3+W2)E1]
 *              / [W1W2(W1+W2)  + W1W3(W1+W3)  + W2W3(W3+W2)]
 *     i.e. each member's blend coefficient is built from the OTHER members'
 *     RMSEs: coeff(Ei) = (Prod_{j!=i} Wj) * (Sum_{j!=i} Wj). Only the
 *     3-member form is printed; the same product-sum rule reproduces it and
 *     is used here for n=2 (reduces to inverse-MSE weights) and n=4
 *     [D1: flagged generalization — the 2-/4-member forms are unpublished].
 *   - "At least two coincident members must be available to produce a
 *     SATCON estimate (the ADT is always one member)" (V&H §2c) — with a
 *     single member this module reports NO consensus, honestly.
 *   - Members of different valid times combine within a 2-h coincidence
 *     window, and MW estimates are carried forward with an age-decayed
 *     weight: full value to 3 h, then exponential decay "approaching zero
 *     once the estimate is older than 6 hours" (CIMSS info page; the decay
 *     constant is unpublished [D2: e-folding 45 min chosen so the weight
 *     factor is ~0.02 at 6 h, where we cut off]).
 *   - Separate weight sets for MSW and MSLP (V&H §2b-c).
 *   - ADT situational RMSE by IR scene type (V&H Fig. 4a-c, MSW):
 *     EYE 11 kt · CDO 13 kt · SHEAR 16 kt.
 *   - SATCON publishes 2-standard-deviation, situation-dependent error
 *     bounds (V&H §2e); here the band is max(2·sigma_blend, half the member
 *     spread, ±10 kt) [D3: the exact bound formula and floors are
 *     unpublished; ±10 kt tracks the Dvorak/ADT per-estimate uncertainty
 *     scale].
 *   DOCUMENTED DEPARTURES (beyond D1-D3, each deliberate, none silent):
 *   D4 no P>W member: V&H blend the final MSW 0.75/0.25 with a
 *      pressure-to-wind member built from recon-fitted regressions +
 *      agency environmental pressure (V&H §2a(3), §2c) — inputs this
 *      client does not have; omitted.
 *   D5 no endpoint bias adjustments: V&H apply ~10-kt-order corrections
 *      above 85 kt and in the first ~36 h of a TC's life (V&H §2c); the
 *      published values are approximate ("on the order of") and were fit
 *      to THEIR members — applying them unvalidated would violate the
 *      honesty contract, so v1 omits them.
 *   D6 no Schwerdt et al. (1979) storm-motion MSW adjustment (V&H §2b).
 *   D7 member bias correction: the MW member is de-biased with TAT's OWN
 *      leave-one-year-out validated per-bin bias (the model card rides the
 *      microwave manifest). The ADT-port member gets NO bias correction:
 *      V&H's per-member corrections are unpublished numerically, and the
 *      published ADT full-sample biases are small and sample-dependent
 *      (+0.9 kt dependent, V&H Table 2; -4.2 kt independent, Table 4).
 *   D8 ADT weight degradations for a weak/low-confidence ARCHER fix
 *      (x1.25) and LUT-inverted input (x1.15) are TAT additions in the
 *      spirit of V&H's situational weighting, NOT published values.
 *
 * MEMBERS
 *   #1 ADT  — the objfix.js ADT port, per analyzed frame (CI -> Vmax/MSLP).
 *   #2 MW   — the tcprimed MW-imager model: per-overpass intensity{} records
 *             in the microwave manifest (89/37-GHz PCT structure -> Vmax,
 *             MSLP), with the model card's validated error tables.
 *
 * HONESTY CONTRACT: automated objective estimates, experimental, never
 * official, uncertainty always visible; consensus only when the method's
 * own membership rule is met. See NHC/JTWC for official intensities.
 */
(function () {
  'use strict';

  // ---- constants (provenance above) ---------------------------------------
  var ADT_SIGMA_BY_SCENE = { EYE: 11.0, CDO: 13.0, SHEAR: 16.0 };  // V&H Fig. 4
  var ADT_SIGMA_MSLP = 9.3;         // V&H Table 3 (ADT MSLP RMSE, hPa)
  var WEAK_FIX_FACTOR = 1.25;       // D8 (TAT addition, flagged)
  var DEGRADED_INPUT_FACTOR = 1.15; // D8
  var MW_FULL_WEIGHT_HOURS = 3.0;   // CIMSS info page
  var MW_CUTOFF_HOURS = 6.0;        // CIMSS info page ("approaches zero")
  var MW_DECAY_EFOLD_HOURS = 0.75;  // D2 (unpublished constant; ~0.02 at 6 h)
  var BAND_FLOOR_KT = 10.0;         // D3
  var BAND_FLOOR_HPA = 8.0;         // D3 (MSLP analogue, chosen)
  var MAX_MEMBERS = 4;              // V&H §2c ("up to four coincident")
  var CDN = 'https://cdn.triple-a-tropics.com';

  // ---------------------------------------------------------------------------
  // pure core
  // ---------------------------------------------------------------------------

  function adtScene(rec) {
    if (rec.eyescene <= 2) return 'EYE';
    if (rec.cloudscene === 4) return 'SHEAR';
    return 'CDO';
  }

  // ADT-port member from one objfix result {frame, archer, rec, field?}
  // -> member or null (over land: Dvorak-family estimates suspended).
  function adtMember(r) {
    if (!r || !r.rec || r.rec.vmax == null) return null;
    if (r.rec.land === 1) return null;
    var scene = adtScene(r.rec);
    var sigma = ADT_SIGMA_BY_SCENE[scene];
    var weak = !(r.archer && r.archer.center) ||
      (r.archer && r.archer.confidenceScore < 0.4);
    if (weak) sigma *= WEAK_FIX_FACTOR;
    var degraded = !!(r.field && r.field.degraded);
    if (degraded) sigma *= DEGRADED_INPUT_FACTOR;
    return {
      kind: 'adt', label: 'ADT-port (IR)',
      t: r.frame.timeMs,
      vmax: r.rec.vmax, sigmaV: sigma,
      mslp: (r.rec.mslp != null ? r.rec.mslp : null), sigmaP: ADT_SIGMA_MSLP,
      scene: scene, weak: weak, degraded: degraded, bias: 0
    };
  }

  // per-bin lookup in the model card's error tables
  function binRow(errByBin, vmax) {
    if (!errByBin) return null;
    for (var i = 0; i < errByBin.length; i++) {
      if (vmax >= errByBin[i].lo && vmax < errByBin[i].hi) return errByBin[i];
    }
    return null;
  }

  // MW-imager member from one microwave-manifest overpass record + the
  // manifest's intensity_model card -> member or null.
  // Bias correction [D7]: subtract the validated per-bin model bias.
  // Situational sigma: max(per-bin RMSE, per-sensor RMSE) — conservative.
  function mwMember(op, modelCard) {
    if (!op || !op.intensity || op.intensity.usable === false ||
        op.intensity.vmax_kt == null || !modelCard) return null;
    var est = op.intensity;
    var row = binRow(modelCard.error_by_bin, est.vmax_kt);
    var sens = modelCard.error_by_sensor &&
      modelCard.error_by_sensor[op.sensor];
    var overall = modelCard.error_overall || {};
    var sigma = Math.max(
      (row && row.rmse) || overall.rmse || 15.0,
      (sens && sens.rmse) || 0);
    var bias = (row && row.bias) || 0;
    var m = {
      kind: 'mw', label: 'MW-imager (' + (op.sensor || '?') + ')',
      t: Date.parse(op.valid_utc),
      vmax: est.vmax_kt - bias, sigmaV: sigma,
      mslp: null, sigmaP: null,
      sensor: op.sensor, id: op.id, bias: bias,
      rawVmax: est.vmax_kt, confidence: est.confidence
    };
    if (est.mslp_hpa != null && modelCard.mslp_error) {
      m.mslp = est.mslp_hpa - (modelCard.mslp_error.bias || 0);
      m.sigmaP = modelCard.mslp_error.rmse || 10.0;
    }
    return m;
  }

  // MW age factor at consensus time t (ms): 1.0 to 3 h, exponential decay
  // after, 0 at/after 6 h; MW from the FUTURE of t never counts.
  function ageFactor(memberT, t) {
    var ageH = (t - memberT) / 3600e3;
    if (ageH < 0) return 0;
    if (ageH <= MW_FULL_WEIGHT_HOURS) return 1.0;
    if (ageH >= MW_CUTOFF_HOURS) return 0;
    return Math.exp(-(ageH - MW_FULL_WEIGHT_HOURS) / MW_DECAY_EFOLD_HOURS);
  }

  // The V&H §2c combination over n members: value_i with weight W_i (RMSE)
  // and an age factor f_i. coeff(E_i) = f_i * prod_{j!=i} W_j * sum_{j!=i}
  // W_j (exact printed form at n=3; D1 generalization elsewhere).
  // Returns {value, sigma, coeffs[]} or null (n=0 / all coeffs 0).
  function combine(values, sigmas, factors) {
    var n = values.length;
    if (!n) return null;
    if (n === 1) {
      if (!factors[0]) return null;
      return { value: values[0], sigma: sigmas[0], coeffs: [1] };
    }
    var coeffs = [], total = 0, i, j;
    for (i = 0; i < n; i++) {
      var prod = 1, sum = 0;
      for (j = 0; j < n; j++) {
        if (j === i) continue;
        prod *= sigmas[j];
        sum += sigmas[j];
      }
      var c = (factors[i] == null ? 1 : factors[i]) * prod * sum;
      coeffs.push(c);
      total += c;
    }
    if (!(total > 0)) return null;
    var v = 0;
    for (i = 0; i < n; i++) v += (coeffs[i] / total) * values[i];
    // blend sigma: error propagation over the normalized coefficients,
    // assuming member independence [D3 — display only, band has floors]
    var s2 = 0;
    for (i = 0; i < n; i++) {
      var w = coeffs[i] / total;
      s2 += w * w * sigmas[i] * sigmas[i];
    }
    return { value: v, sigma: Math.sqrt(s2), coeffs: coeffs.map(function (c) {
      return c / total; }) };
  }

  // Consensus at time t from the ADT member (at t) + MW members (aged).
  // metric: 'vmax' | 'mslp'. Returns {value, half, members[], n} or null
  // when the V&H membership rule (>=2 live members) is not met.
  function consensusAt(t, adt, mws, metric) {
    var vals = [], sigs = [], facs = [], used = [];
    var vKey = metric === 'mslp' ? 'mslp' : 'vmax';
    var sKey = metric === 'mslp' ? 'sigmaP' : 'sigmaV';
    if (adt && adt[vKey] != null) {
      vals.push(adt[vKey]); sigs.push(adt[sKey]); facs.push(1);
      used.push({ member: adt, factor: 1 });
    }
    // newest-first, cap total members at MAX_MEMBERS [V&H §2c]
    var live = (mws || []).map(function (m) {
      return { m: m, f: m[vKey] != null ? ageFactor(m.t, t) : 0 };
    }).filter(function (x) { return x.f > 0; })
      .sort(function (a, b) { return b.m.t - a.m.t; })
      .slice(0, MAX_MEMBERS - vals.length);
    live.forEach(function (x) {
      vals.push(x.m[vKey]); sigs.push(x.m[sKey]); facs.push(x.f);
      used.push({ member: x.m, factor: x.f });
    });
    if (vals.length < 2) return null;   // V&H §2c membership rule
    var c = combine(vals, sigs, facs);
    if (!c) return null;
    var spread = Math.max.apply(null, vals) - Math.min.apply(null, vals);
    var floor = metric === 'mslp' ? BAND_FLOOR_HPA : BAND_FLOOR_KT;
    var half = Math.max(2 * c.sigma, spread / 2, floor);
    for (var i = 0; i < used.length; i++) used[i].weight = c.coeffs[i];
    return { value: c.value, half: half, sigma: c.sigma,
             members: used, n: vals.length, t: t };
  }

  // Full series: consensus evaluated at each ADT frame time (past-only MW,
  // so the line honestly rides ADT between passes and pulls toward a fresh
  // overpass). results = objfix results array; mws = MW members.
  function series(results, mws) {
    var out = [];
    (results || []).forEach(function (r) {
      var adt = adtMember(r);
      if (!adt) { out.push(null); return; }
      out.push(consensusAt(r.frame.timeMs, adt, mws, 'vmax'));
    });
    return out;
  }

  // ---------------------------------------------------------------------------
  // storm -> microwave-manifest slug ('JTWC_WP072026' / 'NHC_AL092024' -> slug)
  // ---------------------------------------------------------------------------
  var ATCF_RE = /([A-Z]{2})(\d{2})(\d{4})/;
  function stormSlug(storm) {
    if (!storm) return null;
    var m = ATCF_RE.exec(String(storm.id || storm.slug || '').toUpperCase());
    return m ? (m[1] + m[2] + m[3]).toLowerCase() : null;
  }

  // ---------------------------------------------------------------------------
  // panel (DOM; inert under node)
  // ---------------------------------------------------------------------------
  var S = {
    host: null, built: false,
    storm: null, slug: null,
    modelCard: null, mws: [], mwState: 'idle', mwFetched: 0,
    results: []
  };

  var CSS = '' +
    '.scn-head{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:7px}' +
    '.scn-val{font-size:22px;font-weight:700;color:var(--cx-fg);font-variant-numeric:tabular-nums}' +
    '.scn-val small{font-size:11px;font-weight:600;color:var(--cx-dim)}' +
    '.scn-band{font-size:11.5px;color:var(--cx-teal);font-weight:600;font-variant-numeric:tabular-nums}' +
    '.scn-sub{font-size:10px;color:var(--cx-dim)}' +
    '.scn-canvas{width:100%;display:block;border:1px solid var(--cx-line-soft);' +
    ' border-radius:8px;background:#0a0d12}' +
    '.scn-tbl{width:100%;border-collapse:collapse;margin-top:7px;font-size:10.5px;' +
    ' color:var(--cx-fg);font-variant-numeric:tabular-nums}' +
    '.scn-tbl th{text-align:left;font-size:9px;font-weight:700;letter-spacing:.1em;' +
    ' text-transform:uppercase;color:var(--cx-dim);padding:2px 8px 3px 0;border-bottom:1px solid var(--cx-line-soft)}' +
    '.scn-tbl td{padding:3px 8px 3px 0;border-bottom:1px solid rgba(255,255,255,0.04)}' +
    '.scn-tbl .dim{color:var(--cx-dim)}' +
    '.scn-note{margin-top:6px;font-size:9.5px;color:var(--cx-dim);line-height:1.55}' +
    '.scn-empty{font-size:11px;color:var(--cx-dim);line-height:1.6;padding:6px 2px}';

  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }

  function mount(host) {
    if (typeof document === 'undefined' || !host) return;
    if (S.built && S.host === host) { redraw(); return; }
    S.host = host; S.built = true;
    if (!document.getElementById('scn-style')) {
      var st = el('style'); st.id = 'scn-style'; st.textContent = CSS;
      document.head.appendChild(st);
    }
    host.innerHTML = '';
    S.head = el('div', 'scn-head');
    host.appendChild(S.head);
    S.cv = el('canvas', 'scn-canvas'); S.cv.height = 240;
    host.appendChild(S.cv);
    S.tbl = el('div');
    host.appendChild(S.tbl);
    host.appendChild(el('div', 'scn-note',
      'TAT’s own objective consensus USING the published SATCON method ' +
      '(Velden &amp; Herndon 2020, WAF, doi:10.1175/WAF-D-20-0015.1): members ' +
      'weighted by situational RMSE via the paper’s combination equation; ' +
      'MW weight decays with overpass age (full ≤3 h, →0 by 6 h, per the ' +
      'CIMSS operational rule); band = max(2σ blend, member spread, ±10 kt). ' +
      'Members: the ADT-port (per-scene RMSE 11/13/16 kt, V&amp;H Fig. 4) and the ' +
      'MW-imager model (TAT-trained on TC-PRIMED, leave-one-year-out errors from ' +
      'its model card, per-bin bias-corrected). NOT the CIMSS SATCON product. ' +
      'Automated objective estimate · experimental · never official — see ' +
      'NHC / JTWC advisories.'));
    window.addEventListener('resize', redraw);
    redraw();
  }

  // ---- MW data ---------------------------------------------------------------
  function fetchJson(url) {
    return fetch(url, { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function setStorm(storm) {
    S.storm = storm || null;
    var slug = stormSlug(storm);
    if (slug !== S.slug) {
      S.slug = slug; S.mws = []; S.modelCard = null;
      S.mwState = slug ? 'loading' : 'idle';
      S.mwFetched = 0;
      if (slug) loadMw();
    }
    redraw();
  }

  function loadMw() {
    var slug = S.slug;
    if (!slug) return;
    S.mwFetched = Date.now();
    fetchJson(CDN + '/microwave/manifest.json').then(function (man) {
      if (S.slug !== slug) return;
      S.modelCard = man.intensity_model || null;
      var listed = (man.storms || []).some(function (s) { return s.slug === slug; });
      if (!S.modelCard) { S.mwState = 'nomodel'; redraw(); return; }
      if (!listed) { S.mwState = 'nostorm'; redraw(); return; }
      return fetchJson(CDN + '/microwave/' + slug + '/overpasses.json')
        .then(function (doc) {
          if (S.slug !== slug) return;
          S.mws = (doc.overpasses || []).map(function (op) {
            return mwMember(op, S.modelCard);
          }).filter(Boolean);
          S.mwState = 'ok';
          redraw();
        });
    }).catch(function () {
      if (S.slug !== slug) return;
      S.mwState = 'error';
      redraw();
    });
  }

  function maybeRefreshMw() {
    if (S.slug && S.mwState !== 'loading' &&
        Date.now() - S.mwFetched > 5 * 60e3) loadMw();
  }

  // ---- drawing ---------------------------------------------------------------
  function dpr() { return Math.min(2, window.devicePixelRatio || 1); }
  function prepCanvas(cv, cssH) {
    var w = cv.parentNode ? cv.parentNode.clientWidth : 640;
    if (w < 80) w = 640;
    var r = dpr();
    cv.style.height = cssH + 'px';
    cv.width = Math.round(w * r); cv.height = Math.round(cssH * r);
    var g = cv.getContext('2d');
    g.setTransform(r, 0, 0, r, 0, 0);
    return { g: g, W: w, H: cssH };
  }
  function fmtHH(t) {
    var d = new Date(t);
    return String(d.getUTCHours()).padStart(2, '0') + ':' +
           String(d.getUTCMinutes()).padStart(2, '0');
  }
  function fmtAge(ms) {
    var h = ms / 3600e3;
    if (h < 1) return Math.round(h * 60) + ' min';
    return h.toFixed(1) + ' h';
  }

  function usableResults() {
    return (S.results || []).filter(function (r) { return r && r.rec; });
  }

  function latestConsensus() {
    var rs = usableResults();
    if (!rs.length) return null;
    var last = rs[rs.length - 1];
    var adt = adtMember(last);
    if (!adt) return null;
    return {
      t: last.frame.timeMs,
      vmax: consensusAt(last.frame.timeMs, adt, S.mws, 'vmax'),
      mslp: consensusAt(last.frame.timeMs, adt, S.mws, 'mslp'),
      adt: adt
    };
  }

  function emptyMsg() {
    if (!usableResults().length) {
      return 'no workup yet · pick a storm (live) or run an archive-window analysis — ' +
        'the ADT member comes from the per-frame workup';
    }
    if (!S.slug) {
      return 'no storm identity in this view (archive mode) · the MW member needs a ' +
        'cataloged storm — consensus requires ≥2 members (V&H §2c), so none is shown';
    }
    if (S.mwState === 'loading') return 'loading MW overpasses…';
    if (S.mwState === 'nomodel') {
      return 'the MW-imager intensity model is not deployed yet — consensus requires ' +
        '≥2 members (V&H §2c), so none is shown';
    }
    if (S.mwState === 'nostorm') {
      return 'no MW overpasses cataloged for this storm yet — consensus requires ' +
        '≥2 members (V&H §2c)';
    }
    if (S.mwState === 'error') return 'MW manifest unavailable — consensus needs ≥2 members';
    return null;
  }

  function redraw() {
    if (!S.built || typeof document === 'undefined') return;
    maybeRefreshMw();
    var lc = latestConsensus();
    var msg = emptyMsg();
    var mwLive = S.mws.filter(function (m) {
      return lc && ageFactor(m.t, lc.t) > 0;
    });
    if (!msg && lc && !lc.vmax) {
      msg = mwLive.length
        ? 'combination degenerate — no consensus'
        : 'no MW overpass within the 6-h window at the latest frame — the consensus ' +
          'needs ≥2 live members (V&H §2c); it will return with the next usable pass';
    }

    // header
    S.head.innerHTML = '';
    if (lc && lc.vmax) {
      var v = lc.vmax;
      S.head.appendChild(el('div', 'scn-val',
        '~' + Math.round(v.value) + ' kt <small>(1-min)</small>'));
      S.head.appendChild(el('div', 'scn-band',
        '±' + Math.round(v.half) + ' kt'));
      if (lc.mslp) {
        S.head.appendChild(el('div', 'scn-val',
          '~' + Math.round(lc.mslp.value) + ' <small>hPa ±' +
          Math.round(lc.mslp.half) + '</small>'));
      }
      S.head.appendChild(el('div', 'scn-sub',
        v.n + ' members · as of ' + fmtHH(lc.t) + 'Z · experimental'));
    } else {
      S.head.appendChild(el('div', 'scn-sub', 'no consensus'));
    }

    drawChart(lc);
    drawTable(lc, msg);
  }

  function drawChart(lc) {
    var rs = usableResults();
    if (rs.length < 2) {
      var c0 = prepCanvas(S.cv, 240), g0 = c0.g;
      g0.fillStyle = '#0a0d12'; g0.fillRect(0, 0, c0.W, c0.H);
      g0.fillStyle = '#5b6879';
      g0.font = '500 12.5px Metropolis,system-ui,sans-serif';
      g0.fillText('trend needs a loop workup (Analyze loop / archive window)', 16, c0.H / 2);
      return;
    }
    var c = prepCanvas(S.cv, 240), g = c.g, W = c.W, H = c.H;
    g.fillStyle = '#0a0d12'; g.fillRect(0, 0, W, H);
    var mL = 40, mR = 12, mT = 24, mB = 26;
    var pw = W - mL - mR, ph = H - mT - mB;
    var t0 = rs[0].frame.timeMs, t1 = rs[rs.length - 1].frame.timeMs;
    // include MW markers inside the window
    var mwIn = S.mws.filter(function (m) { return m.t >= t0 - 6 * 3600e3 && m.t <= t1; });
    var tMin = t0, tMax = Math.max(t1, tMin + 1);
    mwIn.forEach(function (m) { tMin = Math.min(tMin, m.t); });

    var sc = series(rs, S.mws);
    var vMin = Infinity, vMax = -Infinity;
    rs.forEach(function (r) {
      var m = adtMember(r);
      if (m) { vMin = Math.min(vMin, m.vmax); vMax = Math.max(vMax, m.vmax); }
    });
    mwIn.forEach(function (m) {
      vMin = Math.min(vMin, m.vmax); vMax = Math.max(vMax, m.vmax);
    });
    sc.forEach(function (p) {
      if (p) { vMin = Math.min(vMin, p.value - p.half); vMax = Math.max(vMax, p.value + p.half); }
    });
    if (!isFinite(vMin)) { vMin = 20; vMax = 80; }
    vMin = Math.max(0, Math.floor((vMin - 8) / 10) * 10);
    vMax = Math.ceil((vMax + 8) / 10) * 10;

    var px = function (t) { return mL + (t - tMin) / (tMax - tMin) * pw; };
    var py = function (v) { return mT + (1 - (v - vMin) / (vMax - vMin)) * ph; };

    // grid + axes
    g.strokeStyle = '#232d3a'; g.lineWidth = 1;
    g.strokeRect(mL + 0.5, mT + 0.5, pw - 1, ph - 1);
    g.fillStyle = '#8ea2bd'; g.font = '500 10px Metropolis,system-ui,sans-serif';
    for (var v = vMin; v <= vMax; v += (vMax - vMin > 80 ? 20 : 10)) {
      g.fillText(String(v), 10, py(v) + 3.5);
      g.strokeStyle = 'rgba(255,255,255,0.05)';
      g.beginPath(); g.moveTo(mL, py(v)); g.lineTo(mL + pw, py(v)); g.stroke();
    }
    var span = tMax - tMin;
    var tickMs = span > 30 * 3600e3 ? 6 * 3600e3 : span > 12 * 3600e3 ? 3 * 3600e3 : 3600e3;
    var tt = Math.ceil(tMin / tickMs) * tickMs;
    g.textAlign = 'center';
    for (; tt <= tMax; tt += tickMs) {
      g.fillStyle = '#8ea2bd';
      g.fillText(fmtHH(tt) + 'Z', px(tt), H - 8);
    }
    g.textAlign = 'left';

    // consensus band (only where defined — honest gaps)
    g.fillStyle = 'rgba(111,208,140,0.13)';
    var i = 0;
    while (i < sc.length) {
      if (!sc[i]) { i++; continue; }
      var j = i;
      while (j + 1 < sc.length && sc[j + 1]) j++;
      if (j > i) {
        g.beginPath();
        for (var k = i; k <= j; k++) g.lineTo(px(rs[k].frame.timeMs), py(sc[k].value + sc[k].half));
        for (k = j; k >= i; k--) g.lineTo(px(rs[k].frame.timeMs), py(sc[k].value - sc[k].half));
        g.closePath(); g.fill();
      }
      i = j + 1;
    }

    // ADT curve
    g.strokeStyle = 'rgba(73,182,200,0.9)'; g.lineWidth = 1.6;
    g.beginPath();
    var started = false;
    rs.forEach(function (r) {
      var m = adtMember(r);
      if (!m) return;
      var x = px(r.frame.timeMs), y = py(m.vmax);
      if (!started) { g.moveTo(x, y); started = true; } else g.lineTo(x, y);
    });
    g.stroke();

    // consensus line segments
    g.strokeStyle = '#6fd08c'; g.lineWidth = 2.4;
    i = 0;
    while (i < sc.length) {
      if (!sc[i]) { i++; continue; }
      g.beginPath();
      g.moveTo(px(rs[i].frame.timeMs), py(sc[i].value));
      var j2 = i;
      while (j2 + 1 < sc.length && sc[j2 + 1]) {
        j2++;
        g.lineTo(px(rs[j2].frame.timeMs), py(sc[j2].value));
      }
      g.stroke();
      if (j2 === i) {   // single point — draw a dot so it isn't invisible
        g.fillStyle = '#6fd08c';
        g.beginPath();
        g.arc(px(rs[i].frame.timeMs), py(sc[i].value), 2.6, 0, 6.2832);
        g.fill();
      }
      i = j2 + 1;
    }

    // MW overpass markers (diamonds at the bias-corrected member value)
    mwIn.forEach(function (m) {
      var x = px(m.t), y = py(m.vmax);
      g.fillStyle = '#e8d84a';
      g.beginPath();
      g.moveTo(x, y - 4.5); g.lineTo(x + 4.5, y); g.lineTo(x, y + 4.5);
      g.lineTo(x - 4.5, y); g.closePath(); g.fill();
    });

    // legend + provenance (burned in)
    g.font = '600 10px Metropolis,system-ui,sans-serif';
    g.fillStyle = '#49b6c8'; g.fillText('ADT-port', mL + 6, 16);
    g.fillStyle = '#e8d84a'; g.fillText('◆ MW overpass', mL + 62, 16);
    g.fillStyle = '#6fd08c'; g.fillText('consensus ± band', mL + 148, 16);
    g.fillStyle = 'rgba(255,255,255,0.42)';
    g.textAlign = 'right';
    g.fillText('SATCON-method · objective · experimental · @WeathermanAAA_', W - mR - 4, 16);
    g.textAlign = 'left';
  }

  function drawTable(lc, msg) {
    if (msg) {
      S.tbl.innerHTML = '<div class="scn-empty">' + msg + '</div>';
      return;
    }
    if (!lc || !lc.vmax) { S.tbl.innerHTML = ''; return; }
    var rows = lc.vmax.members.map(function (u) {
      var m = u.member;
      var age = lc.t - m.t;
      var caveat = [];
      if (m.kind === 'adt') {
        caveat.push(m.scene + ' scene');
        if (m.weak) caveat.push('weak fix');
        if (m.degraded) caveat.push('LUT input');
      } else {
        if (m.bias) caveat.push('bias-corr ' + (m.bias > 0 ? '−' : '+') +
          Math.abs(m.bias).toFixed(1) + ' kt');
        if (m.confidence) caveat.push(m.confidence + ' conf');
        if (u.factor < 1) caveat.push('age-decayed');
      }
      return '<tr><td>' + m.label + '</td>' +
        '<td>~' + Math.round(m.vmax) + ' kt</td>' +
        '<td>±' + m.sigmaV.toFixed(1) + '</td>' +
        '<td>' + Math.round(u.weight * 100) + '%</td>' +
        '<td>' + fmtAge(age) + '</td>' +
        '<td class="dim">' + caveat.join(' · ') + '</td></tr>';
    }).join('');
    S.tbl.innerHTML = '<table class="scn-tbl"><thead><tr>' +
      '<th>member</th><th>Vmax</th><th>σ (RMSE)</th><th>weight</th>' +
      '<th>age</th><th>notes</th></tr></thead><tbody>' + rows +
      '</tbody></table>';
  }

  // ---------------------------------------------------------------------------
  var SatCon = {
    mount: mount,
    setStorm: setStorm,
    update: function (results /* , state */) {
      S.results = results || [];
      redraw();
    },
    // pure core (node tests + reuse)
    core: {
      adtScene: adtScene,
      adtMember: adtMember,
      mwMember: mwMember,
      ageFactor: ageFactor,
      combine: combine,
      consensusAt: consensusAt,
      series: series,
      stormSlug: stormSlug,
      constants: {
        ADT_SIGMA_BY_SCENE: ADT_SIGMA_BY_SCENE,
        ADT_SIGMA_MSLP: ADT_SIGMA_MSLP,
        MW_FULL_WEIGHT_HOURS: MW_FULL_WEIGHT_HOURS,
        MW_CUTOFF_HOURS: MW_CUTOFF_HOURS,
        BAND_FLOOR_KT: BAND_FLOOR_KT,
        MAX_MEMBERS: MAX_MEMBERS
      }
    }
  };

  if (typeof window !== 'undefined') window.SatCon = SatCon;
  if (typeof module !== 'undefined' && module.exports) module.exports = SatCon;
})();
