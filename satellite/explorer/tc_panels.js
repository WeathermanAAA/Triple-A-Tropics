/* tc_panels.js — the storm-analysis dashboard's two live diagnostic panels:
 *
 *   IR HOVMÖLLER — azimuthal-mean brightness temperature vs RADIUS vs TIME
 *     (Kossin 2002 MWR; Dunion/Thorncroft/Velden 2014 MWR; Ditchek et al.
 *     2019 MWR). Columns = the per-frame radial profiles diag_core computes
 *     inside the objfix pipeline, centered on each frame's OBJECTIVE fix.
 *     Reveals eyewall replacement (secondary cold ring contracting inward),
 *     the diurnal pulse (outward-propagating cooling, ~5-14 m/s), and
 *     convective bursts.
 *
 *   DAV — deviation-angle variance time series (Piñeros/Ritchie/Tyo 2008;
 *     Piñeros et al. 2011; Ritchie et al. 2012/2014; Hu et al. 2020). Low
 *     DAV = axisymmetric/organized; the signal changes ahead of intensity
 *     (≤ ~36 h lead, Hu et al. 2020). Plotted raw + trailing 24-h mean; the
 *     published storm-centered regimes band the chart. NO DAV→intensity
 *     sigmoid is applied — the fits are basin/sensor-specific and quoting
 *     kt from them here would overstate skill (honesty contract).
 *
 * HONESTY: everything here is objective + experimental. Columns/points
 * whose center fix failed the ARCHER quality gates or scored low
 * confidence are DIMMED + amber-flagged; rings below the coverage
 * threshold render as no-data (Ditchek-style gating, never interpolated);
 * time gaps stay visible gaps. Works LIVE and in the Time Machine (the
 * archive-window workup) — same pipeline, same rules.
 */
(function () {
  'use strict';

  var S = {
    results: [], running: false,
    hovHost: null, davHost: null, built: false,
    highlight: null,             // stamp of the TM-scrubbed frame
    set: { maxKm: 450, ringKm: 10, covMin: 0.33, stat: 'mean', davRadius: 250 }
  };

  // compact IR ramp (display-only; approximates the house rainbow_ir look —
  // same table the objfix scene canvas uses)
  var RAMP = [[40, 45, 45, 45], [0, 190, 190, 190], [-30, 230, 230, 230],
    [-40, 60, 200, 120], [-50, 240, 220, 60], [-60, 240, 130, 40],
    [-70, 220, 50, 50], [-80, 150, 30, 160], [-95, 250, 250, 250]];
  function rampColor(tC) {
    if (tC >= RAMP[0][0]) return [RAMP[0][1], RAMP[0][2], RAMP[0][3]];
    for (var i = 1; i < RAMP.length; i++) {
      if (tC >= RAMP[i][0]) {
        var a = RAMP[i], b = RAMP[i - 1];
        var f = (tC - a[0]) / (b[0] - a[0]);
        return [a[1] + f * (b[1] - a[1]), a[2] + f * (b[2] - a[2]), a[3] + f * (b[3] - a[3])];
      }
    }
    var l = RAMP[RAMP.length - 1];
    return [l[1], l[2], l[3]];
  }

  var CSS = '' +
    '.tcp-ctl{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:7px}' +
    '.tcp-ctl label{font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;' +
    ' color:var(--cx-dim)}' +
    '.tcp-ctl select{font-family:inherit;font-size:11px;color:var(--cx-fg);' +
    ' background:rgba(17,23,33,.9);border:1px solid var(--cx-line);border-radius:6px;padding:3px 6px}' +
    '.tcp-btn{font-family:inherit;font-size:10.5px;font-weight:600;color:var(--cx-fg);' +
    ' background:rgba(17,23,33,.9);border:1px solid var(--cx-line);border-radius:7px;' +
    ' padding:4px 9px;cursor:pointer;margin-left:auto}' +
    '.tcp-btn:hover{border-color:var(--cx-teal);color:var(--cx-teal)}' +
    '.tcp-canvas{width:100%;display:block;border:1px solid var(--cx-line-soft);' +
    ' border-radius:8px;background:#0a0d12}' +
    '.tcp-note{margin-top:6px;font-size:9.5px;color:var(--cx-dim);line-height:1.55}' +
    '.tcp-read{margin-top:5px;font-size:11.5px;color:var(--cx-fg);' +
    ' font-variant-numeric:tabular-nums}' +
    '.tcp-read b{color:var(--cx-teal)}' +
    '.tcp-read i{font-style:normal;color:#e88a5a;font-weight:600}';

  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function sel(opts, val, onch) {
    var s = document.createElement('select');
    opts.forEach(function (o) {
      var op = document.createElement('option');
      op.value = String(o[0]); op.textContent = o[1];
      s.appendChild(op);
    });
    s.value = String(val);
    s.onchange = onch;
    return s;
  }

  // ---- mount ---------------------------------------------------------------
  function mount(hovHost, davHost) {
    if (!hovHost || !davHost) return;
    if (S.built && S.hovHost === hovHost) { redraw(); return; }
    S.hovHost = hovHost; S.davHost = davHost; S.built = true;
    if (!document.getElementById('tcp-style')) {
      var st = el('style'); st.id = 'tcp-style'; st.textContent = CSS;
      document.head.appendChild(st);
    }

    // Hovmöller
    hovHost.innerHTML = '';
    var hc = el('div', 'tcp-ctl');
    hc.appendChild(el('label', null, 'radius'));
    hc.appendChild(sel([[300, '0–300 km'], [450, '0–450 km'], [600, '0–600 km']],
      S.set.maxKm, function () { S.set.maxKm = +this.value; noteRerun(); }));
    hc.appendChild(el('label', null, 'coverage ≥'));
    hc.appendChild(sel([[0.25, '25%'], [0.33, '33%'], [0.5, '50%']],
      S.set.covMin, function () { S.set.covMin = +this.value; redraw(); }));
    hc.appendChild(el('label', null, 'statistic'));
    hc.appendChild(sel([['mean', 'azimuthal mean'], ['p10', 'coldest decile']],
      S.set.stat, function () { S.set.stat = this.value; redraw(); }));
    var hbtn = el('button', 'tcp-btn', 'PNG'); hbtn.type = 'button';
    hbtn.onclick = function () { exportPng(S.hovCv, 'hovmoller'); };
    hc.appendChild(hbtn);
    hovHost.appendChild(hc);
    S.hovCv = el('canvas', 'tcp-canvas'); S.hovCv.height = 340;
    hovHost.appendChild(S.hovCv);
    hovHost.appendChild(el('div', 'tcp-note',
      'Azimuthal-mean IR BT per 10 km ring about each frame’s OBJECTIVE center ' +
      '(objfix / ARCHER-port fix; low-confidence columns dimmed + flagged). Rings under the ' +
      'coverage threshold render as no-data — never interpolated. Radius/statistic changes to ' +
      'ring binning apply to the NEXT analysis run. Methods: Kossin 2002 (MWR); Dunion, ' +
      'Thorncroft &amp; Velden 2014 (MWR); Ditchek et al. 2019 (MWR). Signatures: eyewall ' +
      'replacement = a second cold ring contracting inward; diurnal pulse = outward-propagating ' +
      'cooling (~5–14 m s⁻¹); convective burst = deep sustained inner-core cooling.'));

    // DAV
    davHost.innerHTML = '';
    var dc = el('div', 'tcp-ctl');
    dc.appendChild(el('label', null, 'disk radius'));
    dc.appendChild(sel([[150, '150 km'], [200, '200 km'], [250, '250 km (AL optimum)'],
                        [300, '300 km (WP optimum)'], [350, '350 km']],
      S.set.davRadius, function () { S.set.davRadius = +this.value; noteRerun(); }));
    var dbtn = el('button', 'tcp-btn', 'PNG'); dbtn.type = 'button';
    dbtn.onclick = function () { exportPng(S.davCv, 'dav'); };
    dc.appendChild(dbtn);
    davHost.appendChild(dc);
    S.davCv = el('canvas', 'tcp-canvas'); S.davCv.height = 230;
    davHost.appendChild(S.davCv);
    S.davRead = el('div', 'tcp-read', '');
    davHost.appendChild(S.davRead);
    davHost.appendChild(el('div', 'tcp-note',
      'Deviation-angle variance over a storm-centered disk: gradient DIRECTIONS of the ' +
      'smoothed IR field vs the radial, folded ±90°, sample variance in deg² — low = ' +
      'axisymmetric/organized. Bands = published storm-centered regimes; dashed line = the ' +
      'uniform-random limit (2700 deg²). The DAV signal changes AHEAD of intensity (≤ ~36 h ' +
      'lead — Hu et al. 2020); heavy line = trailing 24 h mean (their recommended filter). No ' +
      'DAV→intensity sigmoid is applied (basin/sensor-specific fits — quoting kt would ' +
      'overstate skill). Methods: Piñeros, Ritchie &amp; Tyo 2008 (IEEE TGRS); Piñeros et al. ' +
      '2011 (WAF); Ritchie et al. 2012, 2014 (WAF); Hu et al. 2020. Objective · experimental.'));

    window.addEventListener('resize', redraw);
    redraw();
  }
  function noteRerun() {
    if (window.TCDiag && document.getElementById('tcd-status'))
      document.getElementById('tcd-status').textContent =
        'setting applies to the NEXT analysis run — hit Analyze to recompute.';
  }

  // ---- shared axes helpers -------------------------------------------------
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
  function fmtDay(t) {
    var d = new Date(t);
    return (d.getUTCMonth() + 1) + '/' + d.getUTCDate();
  }
  function drawEmpty(cv, cssH, msg) {
    var c = prepCanvas(cv, cssH), g = c.g;
    g.fillStyle = '#0a0d12'; g.fillRect(0, 0, c.W, c.H);
    g.fillStyle = '#5b6879';
    g.font = '500 12.5px Metropolis,system-ui,sans-serif';
    g.fillText(msg, 16, c.H / 2);
  }
  function usable() {
    return S.results.filter(function (r) { return r && r.diag; });
  }
  function lowConf(r) {
    return !r.archer.center || r.archer.confidenceScore < 0.4;
  }
  function stampOf(r) {
    return r.frame && (r.frame.stamp || '');
  }

  // ---- Hovmöller -------------------------------------------------------------
  function drawHov() {
    if (!S.hovCv) return;
    var rs = usable().filter(function (r) { return r.diag.radial; });
    if (rs.length < 2) {
      drawEmpty(S.hovCv, 340, rs.length === 1
        ? 'one frame analyzed — the Hovmöller needs a loop (Analyze loop / archive window)'
        : 'no analysis yet — pick a storm (live) or load a Time Machine window and Analyze');
      return;
    }
    var c = prepCanvas(S.hovCv, 340), g = c.g, W = c.W, H = c.H;
    g.fillStyle = '#0a0d12'; g.fillRect(0, 0, W, H);
    var mL = 46, mR = 56, mT = 26, mB = 30;
    var pw = W - mL - mR, ph = H - mT - mB;
    var rad = rs[0].diag.radial;
    var maxKm = rad.maxKm;

    var t0 = rs[0].frame.timeMs, t1 = rs[rs.length - 1].frame.timeMs;
    // median step -> gap detection + last column width
    var steps = [];
    for (var i = 1; i < rs.length; i++) steps.push(rs[i].frame.timeMs - rs[i - 1].frame.timeMs);
    steps.sort(function (a, b) { return a - b; });
    var med = steps[Math.floor(steps.length / 2)] || 3600e3;
    var tEnd = t1 + med;
    var px = function (t) { return mL + (t - t0) / (tEnd - t0) * pw; };
    var py = function (km) { return mT + (1 - km / maxKm) * ph; };   // radius UP

    for (i = 0; i < rs.length; i++) {
      var r = rs[i];
      var ta = r.frame.timeMs;
      var tb = (i + 1 < rs.length) ? rs[i + 1].frame.timeMs : ta + med;
      if (tb - ta > 2.5 * med) tb = ta + med;   // honest gap: don't stretch
      var x0 = px(ta), x1 = px(tb);
      var col = r.diag.radial;
      var dim = lowConf(r);
      g.globalAlpha = dim ? 0.45 : 1.0;
      for (var k = 0; k < col.radii.length; k++) {
        if (col.radii[k] > maxKm) break;
        var v = col.meanC[k];
        if (S.set.stat === 'p10') v = col.p10C[k];
        var y1 = py(Math.min(maxKm, col.radii[k] + col.ringKm / 2));
        var y0 = py(col.radii[k] - col.ringKm / 2);
        if (v == null || col.coverage[k] < S.set.covMin) {
          g.fillStyle = '#141a22';   // no-data ring (below coverage) — honest
        } else {
          var rgb = rampColor(v);
          g.fillStyle = 'rgb(' + (rgb[0] | 0) + ',' + (rgb[1] | 0) + ',' + (rgb[2] | 0) + ')';
        }
        g.fillRect(x0, y1, Math.max(0.5, x1 - x0 - 0.35), y0 - y1);
      }
      g.globalAlpha = 1.0;
      if (dim) {   // amber low-confidence flag above the column
        g.fillStyle = '#e88a5a';
        g.fillRect(x0, mT - 5, Math.max(1, x1 - x0 - 0.35), 3);
      }
      if (S.highlight && stampOf(r) === S.highlight) {
        g.strokeStyle = 'rgba(255,255,255,0.85)'; g.lineWidth = 1.4;
        g.strokeRect(x0 + 0.5, mT + 0.5, x1 - x0 - 1, ph - 1);
      }
    }

    // axes
    g.strokeStyle = '#232d3a'; g.lineWidth = 1;
    g.strokeRect(mL + 0.5, mT + 0.5, pw - 1, ph - 1);
    g.fillStyle = '#8ea2bd';
    g.font = '500 10px Metropolis,system-ui,sans-serif';
    for (var km = 0; km <= maxKm; km += 100) {
      g.fillText(km + '', 14, py(km) + 3.5);
      g.strokeStyle = 'rgba(255,255,255,0.07)';
      g.beginPath(); g.moveTo(mL, py(km)); g.lineTo(mL + pw, py(km)); g.stroke();
    }
    g.save(); g.translate(10, mT + ph / 2); g.rotate(-Math.PI / 2);
    g.textAlign = 'center'; g.fillText('radius (km)', 0, 0); g.restore();
    // time ticks: ~6 labels at nice hours
    var span = tEnd - t0;
    var tickMs = span > 30 * 3600e3 ? 6 * 3600e3 : span > 12 * 3600e3 ? 3 * 3600e3 : 3600e3;
    var tt = Math.ceil(t0 / tickMs) * tickMs;
    g.textAlign = 'center';
    var lastDay = '';
    for (; tt < tEnd; tt += tickMs) {
      g.fillStyle = '#8ea2bd';
      g.fillText(fmtHH(tt) + 'Z', px(tt), H - 17);
      var dl = fmtDay(tt);
      if (dl !== lastDay) { g.fillStyle = '#5b6879'; g.fillText(dl, px(tt), H - 5); lastDay = dl; }
      g.strokeStyle = 'rgba(255,255,255,0.06)';
      g.beginPath(); g.moveTo(px(tt), mT); g.lineTo(px(tt), mT + ph); g.stroke();
    }
    g.textAlign = 'left';

    // colorbar (right)
    var cbX = W - mR + 14, cbW = 10;
    for (var yy = 0; yy < ph; yy++) {
      var tc = 40 - (yy / ph) * 135;   // +40 .. -95 °C top-down
      var rc = rampColor(tc);
      g.fillStyle = 'rgb(' + (rc[0] | 0) + ',' + (rc[1] | 0) + ',' + (rc[2] | 0) + ')';
      g.fillRect(cbX, mT + yy, cbW, 1.5);
    }
    g.strokeStyle = 'rgba(255,255,255,0.25)';
    g.strokeRect(cbX + 0.5, mT + 0.5, cbW - 1, ph - 1);
    g.fillStyle = '#8ea2bd';
    [[40, '40'], [0, '0'], [-40, '-40'], [-80, '-80']].forEach(function (t) {
      var y = mT + (40 - t[0]) / 135 * ph;
      g.fillText(t[1], cbX + cbW + 3, y + 3);
    });
    g.save(); g.translate(W - 6, mT + ph / 2); g.rotate(-Math.PI / 2);
    g.textAlign = 'center'; g.fillText('BT (°C)', 0, 0); g.restore();

    // provenance line (burned in — survives PNG export)
    g.fillStyle = 'rgba(219,227,236,0.9)';
    g.font = '700 11px Metropolis,system-ui,sans-serif';
    var st = window.ObjFixPanel && window.ObjFixPanel.storm();
    g.fillText('IR HOVMÖLLER · ' + (st ? st.name : '—') + ' · ' +
      (S.set.stat === 'p10' ? 'coldest-decile' : 'azimuthal-mean') +
      ' BT · objfix centers', mL, 15);
    g.fillStyle = 'rgba(255,255,255,0.42)';
    g.font = '600 10px Metropolis,system-ui,sans-serif';
    g.textAlign = 'right';
    g.fillText('objective · experimental · @WeathermanAAA_', W - mR - 4, 15);
    g.textAlign = 'left';
  }

  // ---- DAV --------------------------------------------------------------------
  function trailing24h(rs, i) {
    var t = rs[i].frame.timeMs, s = 0, n = 0;
    for (var k = 0; k <= i; k++) {
      if (t - rs[k].frame.timeMs <= 24 * 3600e3 && rs[k].diag.dav &&
          rs[k].diag.dav.varDeg2 != null) {
        s += rs[k].diag.dav.varDeg2; n++;
      }
    }
    return n ? s / n : null;
  }
  function drawDav() {
    if (!S.davCv) return;
    var rs = usable().filter(function (r) { return r.diag.dav && r.diag.dav.varDeg2 != null; });
    if (!rs.length) {
      drawEmpty(S.davCv, 230, 'no DAV yet — run an analysis (live loop or archive window)');
      if (S.davRead) S.davRead.textContent = '';
      return;
    }
    var c = prepCanvas(S.davCv, 230), g = c.g, W = c.W, H = c.H;
    g.fillStyle = '#0a0d12'; g.fillRect(0, 0, W, H);
    var mL = 52, mR = 12, mT = 24, mB = 28;
    var pw = W - mL - mR, ph = H - mT - mB;
    var t0 = rs[0].frame.timeMs, t1 = Math.max(rs[rs.length - 1].frame.timeMs, t0 + 1);
    var vMax = 3000, vMin = 0;
    rs.forEach(function (r) { vMax = Math.max(vMax, r.diag.dav.varDeg2 + 100); });
    var px = function (t) { return mL + (t - t0) / (t1 - t0) * pw; };
    var py = function (v) { return mT + (1 - (v - vMin) / (vMax - vMin)) * ph; };

    // published regime bands (see diag_core header) — subtle, labeled
    var REG = (window.TCDiagCore && window.TCDiagCore.DAV_REGIMES) || [];
    var tints = ['rgba(111,208,140,0.07)', 'rgba(232,216,74,0.06)', 'rgba(232,138,90,0.07)'];
    REG.forEach(function (b, i) {
      g.fillStyle = tints[i % tints.length];
      g.fillRect(mL, py(Math.min(b.hi, vMax)), pw, py(b.lo) - py(Math.min(b.hi, vMax)));
      g.fillStyle = '#5b6f8c';
      g.font = '500 9px Metropolis,system-ui,sans-serif';
      g.textAlign = 'right';
      g.fillText(b.label, mL + pw - 4, py(Math.min(b.hi, vMax)) + 10);
      g.textAlign = 'left';
    });
    // uniform-random reference
    g.strokeStyle = 'rgba(142,162,189,0.5)'; g.setLineDash([4, 4]);
    g.beginPath(); g.moveTo(mL, py(2700)); g.lineTo(mL + pw, py(2700)); g.stroke();
    g.setLineDash([]);
    g.fillStyle = '#5b6f8c'; g.font = '500 9px Metropolis,system-ui,sans-serif';
    g.fillText('uniform-random 2700', mL + 4, py(2700) - 4);

    // axes + ticks
    g.strokeStyle = '#232d3a';
    g.strokeRect(mL + 0.5, mT + 0.5, pw - 1, ph - 1);
    g.fillStyle = '#8ea2bd'; g.font = '500 10px Metropolis,system-ui,sans-serif';
    for (var v = 0; v <= vMax; v += 500) {
      g.fillText(String(v), 8, py(v) + 3.5);
      g.strokeStyle = 'rgba(255,255,255,0.05)';
      g.beginPath(); g.moveTo(mL, py(v)); g.lineTo(mL + pw, py(v)); g.stroke();
    }
    var span = t1 - t0;
    var tickMs = span > 30 * 3600e3 ? 6 * 3600e3 : span > 12 * 3600e3 ? 3 * 3600e3 : 3600e3;
    var tt = Math.ceil(t0 / tickMs) * tickMs;
    g.textAlign = 'center';
    for (; tt <= t1; tt += tickMs) {
      g.fillStyle = '#8ea2bd';
      g.fillText(fmtHH(tt) + 'Z', px(tt), H - 8);
    }
    g.textAlign = 'left';

    // raw series: dots (open amber = low-confidence fix) + thin line
    g.strokeStyle = 'rgba(73,182,200,0.55)'; g.lineWidth = 1;
    g.beginPath();
    var started = false;
    rs.forEach(function (r) {
      var x = px(r.frame.timeMs), y = py(r.diag.dav.varDeg2);
      if (!started) { g.moveTo(x, y); started = true; } else g.lineTo(x, y);
    });
    g.stroke();
    rs.forEach(function (r) {
      var x = px(r.frame.timeMs), y = py(r.diag.dav.varDeg2);
      if (lowConf(r)) {
        g.strokeStyle = '#e88a5a'; g.lineWidth = 1.4;
        g.beginPath(); g.arc(x, y, 2.8, 0, 6.2832); g.stroke();
      } else {
        g.fillStyle = '#49b6c8';
        g.beginPath(); g.arc(x, y, 2.8, 0, 6.2832); g.fill();
      }
      if (S.highlight && stampOf(r) === S.highlight) {
        g.strokeStyle = 'rgba(255,255,255,0.8)'; g.lineWidth = 1;
        g.beginPath(); g.moveTo(x, mT); g.lineTo(x, mT + ph); g.stroke();
      }
    });
    // trailing 24-h mean (Hu et al. 2020) — heavier line
    g.strokeStyle = '#e8d84a'; g.lineWidth = 2;
    g.beginPath(); started = false;
    rs.forEach(function (r, i) {
      var m = trailing24h(rs, i);
      if (m == null) return;
      var x = px(r.frame.timeMs), y = py(m);
      if (!started) { g.moveTo(x, y); started = true; } else g.lineTo(x, y);
    });
    g.stroke();

    // provenance
    g.fillStyle = 'rgba(219,227,236,0.9)';
    g.font = '700 11px Metropolis,system-ui,sans-serif';
    var st = window.ObjFixPanel && window.ObjFixPanel.storm();
    var rKm = rs[rs.length - 1].diag.dav.radiusKm;
    g.fillText('DAV · ' + (st ? st.name : '—') + ' · ' + rKm + ' km disk · deg²', mL, 15);
    g.fillStyle = 'rgba(255,255,255,0.42)';
    g.font = '600 10px Metropolis,system-ui,sans-serif';
    g.textAlign = 'right';
    g.fillText('objective · experimental · @WeathermanAAA_', W - mR - 4, 15);
    g.textAlign = 'left';

    // readout: current + Δ6 h (falling DAV = organizing)
    var cur = rs[rs.length - 1];
    var curV = cur.diag.dav.varDeg2;
    var refT = cur.frame.timeMs - 6 * 3600e3, refV = null;
    for (var i2 = rs.length - 1; i2 >= 0; i2--) {
      if (rs[i2].frame.timeMs <= refT) { refV = rs[i2].diag.dav.varDeg2; break; }
    }
    var trend = refV == null ? '' :
      ' · Δ6h ' + (curV - refV >= 0 ? '+' : '') + Math.round(curV - refV) +
      (curV - refV < -100 ? ' (organizing)' : curV - refV > 100 ? ' (disorganizing)' : '');
    S.davRead.innerHTML = 'current <b>' + Math.round(curV) + ' deg²</b>' + trend +
      (lowConf(cur) ? ' · <i>low-confidence center — treat as indicative</i>' : '') +
      ' · n=' + cur.diag.dav.nPix + ' px';
  }

  function exportPng(cv, tag) {
    if (!cv) return;
    var st = window.ObjFixPanel && window.ObjFixPanel.storm();
    cv.toBlob(function (blob) {
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = tag + '_' + ((st && (st.slug || st.name)) || 'storm') + '.png';
      a.click();
    });
  }

  function redraw() { drawHov(); drawDav(); }

  window.TCPanels = {
    mount: mount,
    update: function (results, state) {
      S.results = results || [];
      S.running = !!(state && state.running);
      redraw();
    },
    // TM scrub: highlight the column if the workup covers this stamp;
    // returns true when covered (tc_diag then skips the single-frame recompute)
    highlight: function (stamp) {
      S.highlight = stamp;
      var covered = usable().some(function (r) { return stampOf(r) === stamp; });
      if (covered) redraw();
      return covered;
    },
    diagOpts: function () {
      return { radial: { maxKm: S.set.maxKm, ringKm: S.set.ringKm },
               dav: { radiusKm: S.set.davRadius } };
    }
  };
})();
