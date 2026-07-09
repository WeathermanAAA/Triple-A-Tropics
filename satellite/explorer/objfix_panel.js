/* objfix_panel.js — the cockpit UI for the objective center + intensity
 * feature. Everything shown here is an AUTOMATED OBJECTIVE SATELLITE
 * ESTIMATE (the honesty contract in OBJFIX-METHODS.md): the label, the
 * confidence readout and the poor-scene degradation are non-optional, and
 * nothing is ever presented as official or as a replacement for NHC/JTWC.
 *
 * Compute runs in a Web Worker (objfix.js is UMD and self-registers there);
 * data extraction (canvas decode) runs on the main thread in
 * objfix_sources.js. The per-storm center track is exposed at
 * window.ObjFix.tracks[stormKey] (+ a JSON download) — the reusable output
 * the Hovmöller and floater auto-centering consume.
 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var S = {
    storms: [], storm: null,
    src: null,          // FdSource | FloaterSource
    frames: [],
    results: [],        // per-frame {frame, archer, rec}
    history: [],        // ADT history records (oldest first)
    isLand: null,
    running: false,
    worker: null,
    panelOpen: false
  };

  // ---- worker ---------------------------------------------------------------
  function makeWorker() {
    try {
      var src = "importScripts('" + location.origin + "/satellite/explorer/objfix.js');\n" +
        "onmessage = function (e) {\n" +
        "  var d = e.data;\n" +
        "  try {\n" +
        "    var field = d.field;\n" +
        "    field.latArr = new Float64Array(field.latArr);\n" +
        "    field.lonArr = new Float64Array(field.lonArr);\n" +
        "    field.bt = new Float64Array(field.bt);\n" +
        "    var env = d.env;\n" +
        "    var lm = env.landMask;\n" +
        "    env.isLand = lm ? function (lat, lon) {\n" +
        "      var i = Math.round((lm.latTop - lat) / lm.step);\n" +
        "      var j = Math.round((lon - lm.lonLeft) / lm.step);\n" +
        "      if (i < 0 || j < 0 || i >= lm.n || j >= lm.n) return false;\n" +
        "      return !!lm.mask[i * lm.n + j];\n" +
        "    } : null;\n" +
        "    var archer = self.ObjFix.archerFix(field, d.guess, d.opts);\n" +
        "    var center = archer.center || archer.weakCenter || { lat: d.guess.lat, lon: d.guess.lon };\n" +
        "    var rec = self.ObjFix.adtEstimate(field, center, d.timeMs, d.history, env);\n" +
        "    postMessage({ id: d.id, archer: archer, rec: rec });\n" +
        "  } catch (err) {\n" +
        "    postMessage({ id: d.id, error: String(err && (err.stack || err.message) || err) });\n" +
        "  }\n" +
        "};";
      return new Worker(URL.createObjectURL(new Blob([src], { type: 'application/javascript' })));
    } catch (e) { return null; }
  }
  var _mid = 0, _pending = {};
  function analyzeInWorker(field, guess, timeMs, history, env, opts) {
    if (!S.worker) S.worker = makeWorker();
    if (!S.worker) {
      // main-thread fallback (blocks briefly; the worker path is the norm)
      return new Promise(function (res, rej) {
        setTimeout(function () {
          try {
            var lm = env.landMask;
            env.isLand = lm ? function (lat, lon) {
              var i = Math.round((lm.latTop - lat) / lm.step);
              var j = Math.round((lon - lm.lonLeft) / lm.step);
              if (i < 0 || j < 0 || i >= lm.n || j >= lm.n) return false;
              return !!lm.mask[i * lm.n + j];
            } : null;
            var archer = window.ObjFix.archerFix(field, guess, opts);
            var center = archer.center || archer.weakCenter || { lat: guess.lat, lon: guess.lon };
            var rec = window.ObjFix.adtEstimate(field, center, timeMs, history, env);
            res({ archer: archer, rec: rec });
          } catch (e) { rej(e); }
        }, 20);
      });
    }
    return new Promise(function (res, rej) {
      var id = ++_mid;
      _pending[id] = { res: res, rej: rej };
      S.worker.onmessage = function (e) {
        var p = _pending[e.data.id];
        if (!p) return;
        delete _pending[e.data.id];
        if (e.data.error) p.rej(new Error(e.data.error));
        else p.res(e.data);
      };
      var msg = {
        id: id,
        field: { latArr: field.latArr.buffer.slice(0), lonArr: field.lonArr.buffer.slice(0),
                 bt: field.bt.buffer.slice(0), nr: field.nr, nc: field.nc, resKm: field.resKm },
        guess: guess, timeMs: timeMs, history: history, env: env, opts: opts
      };
      S.worker.postMessage(msg, [msg.field.latArr, msg.field.lonArr, msg.field.bt]);
    });
  }

  // ---- panel DOM ------------------------------------------------------------
  var CSS = '' +
    '#ofx-panel{position:absolute;top:0;right:0;bottom:0;width:352px;z-index:8;display:none;' +
    ' background:rgba(13,18,25,.97);border-left:1px solid var(--cx-line);overflow-y:auto;' +
    ' font-family:Metropolis,system-ui,sans-serif;scrollbar-width:thin}' +
    '#ofx-panel.open{display:block}' +
    '.ofx-head{position:sticky;top:0;z-index:2;background:#0d1219;padding:10px 14px 8px;' +
    ' border-bottom:1px solid var(--cx-line)}' +
    '.ofx-head h4{margin:0;font-size:13px;font-weight:700;color:var(--cx-fg);display:flex;' +
    ' align-items:center;gap:8px;justify-content:space-between}' +
    '.ofx-x{background:none;border:0;color:var(--cx-mut);font-size:15px;cursor:pointer;padding:2px 6px}' +
    '.ofx-x:hover{color:#fff}' +
    '.ofx-banner{margin-top:7px;font-size:9.5px;font-weight:700;letter-spacing:.08em;' +
    ' text-transform:uppercase;color:#c9a35a;border:1px solid rgba(201,163,90,.4);' +
    ' border-radius:6px;padding:5px 8px;line-height:1.5}' +
    '.ofx-banner span{display:block;font-weight:500;letter-spacing:.02em;text-transform:none;' +
    ' color:#a8905c;font-size:10px}' +
    '.ofx-body{padding:10px 14px 16px;display:flex;flex-direction:column;gap:10px}' +
    '.ofx-row{display:flex;gap:6px;align-items:center}' +
    '.ofx-row select{flex:1;font-family:inherit;font-size:12px;color:var(--cx-fg);' +
    ' background:rgba(17,23,33,.9);border:1px solid var(--cx-line);border-radius:7px;padding:6px 8px}' +
    '.ofx-btn{font-family:inherit;font-size:11.5px;font-weight:600;color:var(--cx-fg);' +
    ' background:rgba(17,23,33,.9);border:1px solid var(--cx-line);border-radius:8px;' +
    ' padding:6px 10px;cursor:pointer;white-space:nowrap}' +
    '.ofx-btn:hover:not(:disabled){border-color:var(--cx-teal);color:var(--cx-teal)}' +
    '.ofx-btn:disabled{opacity:.4;cursor:default}' +
    '#ofx-scene{width:100%;border:1px solid var(--cx-line);border-radius:8px;background:#0a0d12;display:block}' +
    '#ofx-trend{width:100%;height:120px;border:1px solid var(--cx-line-soft);border-radius:8px;' +
    ' background:#0a0d12;display:block}' +
    '.ofx-stat{display:grid;grid-template-columns:118px 1fr;gap:3px 10px;font-size:11.5px;' +
    ' color:var(--cx-fg);font-variant-numeric:tabular-nums}' +
    '.ofx-stat b{color:var(--cx-dim);font-weight:600;font-size:10.5px;text-transform:uppercase;' +
    ' letter-spacing:.06em}' +
    '.ofx-stat i{font-style:normal}' +
    '.ofx-conf-hi{color:#6fd08c}.ofx-conf-md{color:#e8d84a}.ofx-conf-lo{color:#e88a5a}' +
    '.ofx-note{font-size:10px;color:var(--cx-dim);line-height:1.55}' +
    '.ofx-warn{font-size:10.5px;color:#e88a5a;font-weight:600;line-height:1.5}' +
    '.ofx-prog{height:4px;border-radius:2px;background:#141b25;overflow:hidden;display:none}' +
    '.ofx-prog i{display:block;height:100%;background:var(--cx-teal);width:0%}';

  function buildPanel() {
    var st = document.createElement('style');
    st.textContent = CSS;
    document.head.appendChild(st);
    var el = document.createElement('div');
    el.id = 'ofx-panel';
    el.innerHTML =
      '<div class="ofx-head">' +
      '  <h4>Objective Center + Intensity' +
      '    <button type="button" class="ofx-x" id="ofx-close" title="Close">✕</button></h4>' +
      '  <div class="ofx-banner">Automated objective satellite estimate' +
      '    <span>Experimental. Computed from satellite imagery only (ARCHER-style center fix +' +
      '    ADT-style intensity, ports of CIMSS methods). Not official — see NHC / JTWC' +
      '    advisories for official positions and intensities.</span></div>' +
      '</div>' +
      '<div class="ofx-body">' +
      '  <div class="ofx-row"><select id="ofx-storm"></select>' +
      '    <button type="button" class="ofx-btn" id="ofx-run">Analyze latest</button></div>' +
      '  <div class="ofx-row"><button type="button" class="ofx-btn" id="ofx-loop">Analyze loop (trend)</button>' +
      '    <button type="button" class="ofx-btn" id="ofx-stop" disabled>Stop</button>' +
      '    <button type="button" class="ofx-btn" id="ofx-dl" disabled title="Center-track JSON — the reusable output (Hovmöller consumer)">Track JSON</button></div>' +
      '  <div class="ofx-prog" id="ofx-prog"><i></i></div>' +
      '  <canvas id="ofx-scene" width="640" height="640"></canvas>' +
      '  <div class="ofx-stat" id="ofx-stats"></div>' +
      '  <div class="ofx-warn" id="ofx-warn" style="display:none"></div>' +
      '  <canvas id="ofx-trend" width="640" height="240"></canvas>' +
      '  <div class="ofx-note" id="ofx-note"></div>' +
      '  <div class="ofx-note">Center marker: solid crosshair = accepted fix; faint crosshairs =' +
      '   rejected candidate maxima; dashed = weak center (quality gates failed — position only,' +
      '   low confidence). Circles: 50% / 95% position-certainty radii from the ARCHER error model.</div>' +
      '</div>';
    document.querySelector('.cx-view').appendChild(el);

    $('ofx-close').onclick = closePanel;
    $('ofx-run').onclick = function () { runAnalysis(false); };
    $('ofx-loop').onclick = function () { runAnalysis(true); };
    $('ofx-stop').onclick = function () { S.running = false; };
    $('ofx-dl').onclick = downloadTrack;
    $('ofx-storm').onchange = function () {
      S.storm = S.storms[+this.value] || null;
      S.results = []; S.history = []; S.src = null;
      renderStats(null); drawTrend(); drawScene(null);
    };
  }

  function addToolButton() {
    var group = document.querySelector('.cx-tools .cx-tgroup:nth-child(3)');
    var btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'cx-btn'; btn.id = 'cx-objfix';
    btn.title = 'Objective center + intensity — automated satellite estimate (experimental)';
    btn.innerHTML = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" ' +
      'stroke-linecap="round"><use href="#i-inspect"/></svg><span class="lbl">Obj Fix</span>' +
      '<i class="cx-chip">beta</i>';
    btn.onclick = function () { S.panelOpen ? closePanel() : openPanel(); };
    if (group) group.appendChild(btn);
  }

  function openPanel() {
    S.panelOpen = true;
    $('ofx-panel').classList.add('open');
    $('cx-objfix').classList.add('on');
    if (!S.storms.length) loadStorms();
  }
  function closePanel() {
    S.panelOpen = false;
    $('ofx-panel').classList.remove('open');
    var b = $('cx-objfix');
    if (b) b.classList.remove('on');
  }

  // ---- storms ---------------------------------------------------------------
  function loadStorms() {
    window.ObjFixSources.listStorms().then(function (storms) {
      S.storms = storms;
      var sel = $('ofx-storm');
      sel.innerHTML = '';
      if (!storms.length) {
        sel.innerHTML = '<option>— no active storms in the feeds —</option>';
        return;
      }
      storms.forEach(function (s, i) {
        var o = document.createElement('option');
        o.value = String(i);
        o.textContent = s.name + ' · ' + s.basin +
          (s.vmax ? ' · ' + s.vmax + ' kt' : '') +
          (s.source === 'fd' ? ' · fd BT' : ' · floater (LUT)');
        sel.appendChild(o);
      });
      S.storm = storms[0];
    }).catch(function (e) {
      $('ofx-storm').innerHTML = '<option>— storm feed unavailable —</option>';
    });
    // land test for ADT's over-land suspension
    if (window.TATRegions && TATRegions.loadGeo) {
      TATRegions.loadGeo({}).then(function (geo) {
        S.isLand = window.ObjFixSources.makeLandTest(geo);
      }).catch(function () {});
    }
  }

  function getSource() {
    if (S.src) return Promise.resolve(S.src);
    var st = S.storm;
    var src = st.source === 'fd'
      ? new window.ObjFixSources.FdSource()
      : new window.ObjFixSources.FloaterSource(st.slug);
    return src.load().then(function () { S.src = src; S.frames = src.frames(); return src; });
  }

  // ---- analysis -------------------------------------------------------------
  function envFor(storm) {
    // land test rasterized to a mask so it survives the worker boundary
    var lm = window.ObjFixSources.buildLandMask(S.isLand, storm.lat, storm.lon);
    return {
      domainID: storm.domainID, basinID: storm.basinID,
      landFlag: true,
      landMask: lm,
      isLand: null,
      initRawT: window.ObjFix.vmaxToTno(storm.vmax),
      initStrengthTF: false
    };
  }

  function frameField(frame) {
    var st = S.storm;
    if (st.source === 'fd') return S.src.field(frame.stamp, st.lat, st.lon, 4.0);
    return S.src.field(frame);
  }

  function runAnalysis(loop) {
    if (S.running || !S.storm) return;
    var st = S.storm;
    if (st.lat == null) { warn('no first-guess position in the feed for ' + st.name); return; }
    S.running = true;
    $('ofx-run').disabled = $('ofx-loop').disabled = true;
    $('ofx-stop').disabled = !loop;
    S.results = []; S.history = [];
    getSource().then(function () {
      var frames = S.frames.slice();
      if (!frames.length) throw new Error('no frames available for ' + st.name);
      if (!loop) frames = frames.slice(-1);
      // trend window: the trailing 26 h of frames, thinned to ~30 min cadence
      // (Rule 8/9 look back 24 h; finer cadence adds cost, not skill)
      if (loop) {
        var newest = frames[frames.length - 1].timeMs;
        frames = frames.filter(function (f) { return newest - f.timeMs <= 26 * 3600e3; });
        frames = thinFrames(frames, 30 * 60e3);
      }
      var env = envFor(st);
      env.initStrengthTF = loop;         // ADT ops seed the FIRST loop frame
      var prog = $('ofx-prog');
      prog.style.display = loop ? 'block' : 'none';

      var chain = Promise.resolve();
      frames.forEach(function (frame, fi) {
        chain = chain.then(function () {
          if (!S.running && loop) return null;
          note((loop ? 'analyzing frame ' + (fi + 1) + '/' + frames.length + ' — '
                     : 'analyzing ') + frame.stamp + ' …', true);
          prog.firstChild.style.width = (100 * fi / frames.length).toFixed(1) + '%';
          return frameField(frame).then(function (field) {
            // first guess: the official-track anchor for THIS frame (the
            // floater box center follows the agency fixes) — NEVER a chained
            // prior fix, which un-anchors the penalty term and drifts.
            var guess = {
              lat: frame.guessLat != null ? frame.guessLat : st.lat,
              lon: frame.guessLon != null ? frame.guessLon : st.lon,
              vmax: st.vmax || 0
            };
            var opts = { channelType: 'IR', searchRadiusDeg: loop ? 1.5 : 2.0 };
            return analyzeInWorker(field, guess, frame.timeMs, S.history, env, opts)
              .then(function (r) {
                r.frame = frame; r.field = field;
                // memory: only the NEWEST frame keeps its heavy field data
                // (BT grids + decoded image); older results keep the numbers.
                // A 40-frame loop would otherwise pin ~0.5 GB and can kill
                // the tab.
                if (S.results.length) {
                  var prev = S.results[S.results.length - 1];
                  prev.field = { inputQuality: prev.field.inputQuality,
                                 degraded: prev.field.degraded };
                }
                S.results.push(r);
                S.history.push(r.rec);
                env.initStrengthTF = false;
                if (!loop || fi === frames.length - 1 || fi % 4 === 3) {
                  drawScene(r); renderStats(r); drawTrend(); paneMarkers(r);
                }
              });
          }).catch(function (e) {
            // per-frame failure: skip, keep the loop honest about it
            console.warn('objfix frame failed', frame.stamp, e);
          });
        });
      });
      return chain;
    }).then(function () {
      finishRun();
    }).catch(function (e) {
      warn(String(e && e.message || e));
      finishRun();
    });
  }
  function finishRun() {
    S.running = false;
    $('ofx-run').disabled = $('ofx-loop').disabled = false;
    $('ofx-stop').disabled = true;
    $('ofx-prog').style.display = 'none';
    $('ofx-dl').disabled = !S.results.length;
    if (S.results.length) {
      var last = S.results[S.results.length - 1];
      drawScene(last); renderStats(last); drawTrend(); paneMarkers(last);
      publishTrack();
      note(S.results.length + ' frame' + (S.results.length === 1 ? '' : 's') + ' analyzed.');
    }
  }
  function thinFrames(frames, minGapMs) {
    var out = [], lastT = -Infinity;
    for (var i = 0; i < frames.length; i++) {
      if (frames[i].timeMs - lastT >= minGapMs || i === frames.length - 1) {
        out.push(frames[i]); lastT = frames[i].timeMs;
      }
    }
    return out;
  }

  // ---- track output (the reusable product) ----------------------------------
  function trackJSON() {
    var st = S.storm;
    return {
      storm: { id: st.id, name: st.name, basin: st.basin },
      method: 'ARCHER-style IR center fix (objfix.js port); confidence radii from the ARCHER error model',
      disclosure: 'AUTOMATED OBJECTIVE SATELLITE ESTIMATE — experimental, not official. See NHC/JTWC.',
      generated_utc: new Date().toISOString(),
      input: S.results.length ? S.results[0].field.inputQuality : null,
      points: S.results.map(function (r) {
        var c = r.archer.center || r.archer.weakCenter;
        return {
          t: new Date(r.frame.timeMs).toISOString(),
          lat: c ? +c.lat.toFixed(3) : null,
          lon: c ? +c.lon.toFixed(3) : null,
          fix: !!r.archer.center,
          confidence_score: +r.archer.confidenceScore.toFixed(3),
          r50_km: +(r.archer.radius50percCertDeg * 111).toFixed(0),
          r95_km: +(r.archer.radius95percCertDeg * 111).toFixed(0),
          eye_prob_pct: r.archer.eyeProb == null ? null : Math.round(r.archer.eyeProb),
          scene: sceneName(r.rec),
          rawT: r.rec.TrawO, finalT: r.rec.Tfinal, CI: r.rec.CI,
          vmax_kt: r.rec.vmax, mslp_mb: r.rec.mslp, land: r.rec.land
        };
      })
    };
  }
  function publishTrack() {
    if (!window.ObjFix.tracks) window.ObjFix.tracks = {};
    window.ObjFix.tracks[S.storm.id || S.storm.name] = trackJSON();
  }
  function downloadTrack() {
    if (!S.results.length) return;
    var blob = new Blob([JSON.stringify(trackJSON(), null, 1)], { type: 'application/json' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'objfix_' + (S.storm.slug || S.storm.name || 'storm') + '_track.json';
    a.click();
  }

  // ---- scene canvas ----------------------------------------------------------
  function drawScene(r) {
    var cv = $('ofx-scene'), g = cv.getContext('2d');
    g.clearRect(0, 0, cv.width, cv.height);
    g.fillStyle = '#0a0d12'; g.fillRect(0, 0, cv.width, cv.height);
    if (!r) return;
    var field = r.field, W = cv.width, H = cv.height;
    var ext = field.extent;   // [W,S,E,N]
    var proj = function (lat, lon) {
      return [ (lon - ext[0]) / (ext[2] - ext[0]) * W,
               (ext[3] - lat) / (ext[3] - ext[1]) * H ];
    };
    if (field.image && field.cropRect) {
      // floater: the source frame's data-rect crop IS the scene
      var cr = field.cropRect;
      g.drawImage(field.image, cr.x, cr.y, cr.w, cr.h, 0, 0, W, H);
    } else {
      // fd: render the BT grid via a compact IR ramp
      renderBT(g, field, W, H);
    }
    // rejected candidates: faint crosshairs (transparency — spec)
    (r.archer.rejected || []).forEach(function (c) {
      crosshair(g, proj(c.lat, c.lon), 11, 'rgba(255,255,255,0.28)', 1, false);
    });
    var c = r.archer.center || r.archer.weakCenter;
    if (c) {
      var p = proj(c.lat, c.lon);
      var accepted = !!r.archer.center;
      // 50 / 95% certainty circles (degrees -> px via lat scale)
      var degPx = H / (ext[3] - ext[1]);
      [['radius50percCertDeg', 0.55], ['radius95percCertDeg', 0.3]].forEach(function (rr) {
        var rad = r.archer[rr[0]];
        if (!rad) return;
        g.beginPath();
        g.arc(p[0], p[1], rad * degPx, 0, 6.2832);
        g.strokeStyle = 'rgba(73,182,200,' + rr[1] + ')';
        g.lineWidth = 1.2; g.setLineDash([4, 4]); g.stroke(); g.setLineDash([]);
      });
      crosshair(g, p, 16, accepted ? '#49b6c8' : 'rgba(73,182,200,0.75)', 2, !accepted);
      // eye-size ring from the ring fit
      if (r.archer.ringRadiusDeg > 0.051 && accepted) {
        g.beginPath();
        g.arc(p[0], p[1], r.archer.ringRadiusDeg * degPx, 0, 6.2832);
        g.strokeStyle = 'rgba(255,179,71,0.8)'; g.lineWidth = 1.4; g.stroke();
      }
    }
    // stamp + input badge
    g.font = '600 15px Metropolis,system-ui,sans-serif';
    g.fillStyle = 'rgba(219,227,236,0.9)';
    g.fillText(r.frame.stamp.replace('T', ' ').replace(/Z$/, ' Z'), 12, 24);
    g.font = '500 12px Metropolis,system-ui,sans-serif';
    g.fillStyle = field.degraded ? 'rgba(232,138,90,0.9)' : 'rgba(142,162,189,0.9)';
    g.fillText(field.degraded ? 'LUT-inverted input — degraded precision' : 'calibrated BT input', 12, 42);
  }
  function crosshair(g, p, r, color, lw, dashed) {
    g.save();
    g.strokeStyle = color; g.lineWidth = lw;
    if (dashed) g.setLineDash([4, 3]);
    g.beginPath();
    g.moveTo(p[0] - r, p[1]); g.lineTo(p[0] - r * 0.3, p[1]);
    g.moveTo(p[0] + r * 0.3, p[1]); g.lineTo(p[0] + r, p[1]);
    g.moveTo(p[0], p[1] - r); g.lineTo(p[0], p[1] - r * 0.3);
    g.moveTo(p[0], p[1] + r * 0.3); g.lineTo(p[0], p[1] + r);
    g.stroke();
    g.restore();
  }
  // compact BT ramp for the fd path (approximates the rainbow_ir look —
  // display only, the analysis uses the raw numbers)
  var RAMP = [[40, 45, 45, 45], [0, 190, 190, 190], [-30, 230, 230, 230],
    [-40, 60, 200, 120], [-50, 240, 220, 60], [-60, 240, 130, 40],
    [-70, 220, 50, 50], [-80, 150, 30, 160], [-95, 250, 250, 250]];
  function renderBT(g, field, W, H) {
    var img = g.createImageData(W, H);
    for (var y = 0; y < H; y++) {
      var i = Math.floor(y / H * field.nr);
      for (var x = 0; x < W; x++) {
        var j = Math.floor(x / W * field.nc);
        var k = (y * W + x) * 4;
        var tC = field.bt[i * field.nc + j] - 273.15;
        if (isNaN(tC)) { img.data[k + 3] = 0; continue; }
        var col = rampColor(tC);
        img.data[k] = col[0]; img.data[k + 1] = col[1]; img.data[k + 2] = col[2];
        img.data[k + 3] = 255;
      }
    }
    g.putImageData(img, 0, 0);
  }
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

  // ---- stats readout ----------------------------------------------------------
  function sceneName(rec) {
    var n = window.ObjFix.SCENE_NAMES;
    return rec.eyescene <= 2 ? n.eye[rec.eyescene] : n.cloud[rec.cloudscene] || '—';
  }
  function confTier(score) {
    if (score >= 1.0) return ['high', 'ofx-conf-hi'];
    if (score >= 0.4) return ['moderate', 'ofx-conf-md'];
    return ['low', 'ofx-conf-lo'];
  }
  function renderStats(r) {
    var host = $('ofx-stats');
    $('ofx-warn').style.display = 'none';
    if (!r) { host.innerHTML = ''; return; }
    var a = r.archer, rec = r.rec;
    var c = a.center || a.weakCenter;
    var tier = confTier(a.confidenceScore);
    var skill = window.ObjFix.sceneSkill(rec);
    var rows = [];
    rows.push(['Center', c ? Math.abs(c.lat).toFixed(2) + '°' + (c.lat >= 0 ? 'N' : 'S') + ' ' +
      Math.abs(c.lon).toFixed(2) + '°' + (c.lon >= 0 ? 'E' : 'W') +
      (a.center ? '' : ' · WEAK (gates failed)') : '—']);
    rows.push(['Fix confidence', '<i class="' + tier[1] + '">' + tier[0] + '</i> · score ' +
      a.confidenceScore.toFixed(2) + ' · r50 ' + Math.round(a.radius50percCertDeg * 111) +
      ' km · r95 ' + Math.round(a.radius95percCertDeg * 111) + ' km']);
    if (a.eyeProb != null) rows.push(['Eye probability', Math.round(a.eyeProb) + ' %']);
    rows.push(['Scene type', sceneName(rec) + ' · skill: ' + skill.tier]);
    rows.push(['Raw T# / Final T#', (rec.TrawO != null ? rec.TrawO.toFixed(1) : '—') + ' / ' +
      (rec.Tfinal != null ? rec.Tfinal.toFixed(1) : '—')]);
    // poor fix -> the intensity number itself carries the caveat, not just
    // the warning block (honesty contract: no confident wrong numbers)
    var unrel = !a.center ? ' — UNRELIABLE (poor fix)' : '';
    rows.push(['CI number', (rec.CI != null ? rec.CI.toFixed(1) : '—') + unrel]);
    rows.push(['Est. Vmax', rec.vmax != null ? '~' + Math.round(rec.vmax) + ' kt (1-min)' + unrel : '—']);
    rows.push(['Est. MSLP', rec.mslp != null ? '~' + Math.round(rec.mslp) + ' mb (table, unadjusted)' + unrel : '—']);
    rows.push(['Eye / cloud BT', rec.eyet.toFixed(1) + ' / ' + rec.cloudt.toFixed(1) + ' °C']);
    host.innerHTML = rows.map(function (rw) {
      return '<b>' + rw[0] + '</b><i>' + rw[1] + '</i>';
    }).join('');

    // honesty degradations — loud, before the numbers get quoted
    var warns = [];
    if (!a.center) warns.push('POOR FIX — quality gates rejected the center; position shown is the weak-center candidate and the intensity below inherits that uncertainty.');
    if (rec.land === 1) warns.push('CENTER OVER LAND — Dvorak-family estimates are not valid over land; intensity is suspended (shown for continuity only).');
    if (a.confidenceScore < 0.4) warns.push('LOW CONFIDENCE SCENE — treat the estimate as indicative only.');
    if (S.results.length < 2) warns.push('Single frame — the 3 h averaging and Rule 8/9 time rules are inactive; Raw T# only.');
    if (r.field.degraded) warns.push('Input is a LUT inversion of colorized imagery (WP floater path) — degraded precision vs calibrated BT.');
    if (warns.length) {
      $('ofx-warn').style.display = 'block';
      $('ofx-warn').innerHTML = warns.join('<br>');
    }
    note('skill: ' + skill.note + ' · input: ' + r.field.inputQuality);
  }

  // ---- trend chart -------------------------------------------------------------
  function drawTrend() {
    var cv = $('ofx-trend'), g = cv.getContext('2d');
    var W = cv.width, H = cv.height;
    g.clearRect(0, 0, W, H);
    g.fillStyle = '#0a0d12'; g.fillRect(0, 0, W, H);
    var rs = S.results;
    g.font = '500 18px Metropolis,system-ui,sans-serif';
    if (rs.length < 2) {
      g.fillStyle = '#5b6879';
      g.fillText(rs.length === 1 ? 'trend needs a loop — Analyze loop' : 'no analysis yet', 16, H / 2);
      return;
    }
    var t0 = rs[0].frame.timeMs, t1 = rs[rs.length - 1].frame.timeMs;
    var vMin = 1.0, vMax = 8.5;
    rs.forEach(function (r) {
      vMax = Math.max(vMax, (r.rec.CI || 0) + 0.5);
    });
    var px = function (t) { return 34 + (t - t0) / Math.max(1, t1 - t0) * (W - 48); };
    var py = function (v) { return H - 26 - (v - vMin) / (vMax - vMin) * (H - 44); };
    // T# gridlines
    g.strokeStyle = '#141b25'; g.fillStyle = '#5b6879';
    g.font = '500 14px Metropolis,system-ui,sans-serif';
    for (var t = 1; t <= Math.floor(vMax); t++) {
      g.beginPath(); g.moveTo(30, py(t)); g.lineTo(W - 8, py(t)); g.stroke();
      g.fillText(String(t), 8, py(t) + 5);
    }
    // Raw T# dots
    rs.forEach(function (r) {
      g.fillStyle = 'rgba(142,162,189,0.65)';
      g.beginPath(); g.arc(px(r.frame.timeMs), py(r.rec.TrawO), 3, 0, 6.2832); g.fill();
    });
    // Final T# line
    line(g, rs, px, py, function (r) { return r.rec.Tfinal; }, '#e8d84a', 2);
    // CI line
    line(g, rs, px, py, function (r) { return r.rec.CI; }, '#49b6c8', 2.4);
    // legend
    g.font = '600 13px Metropolis,system-ui,sans-serif';
    g.fillStyle = '#8ea2bd'; g.fillText('Raw T# ·', W - 218, 20);
    g.fillStyle = '#e8d84a'; g.fillText('Final T#', W - 158, 20);
    g.fillStyle = '#49b6c8'; g.fillText('CI', W - 90, 20);
    g.fillStyle = '#5b6879';
    g.font = '500 12px Metropolis,system-ui,sans-serif';
    var span = ((t1 - t0) / 3600e3).toFixed(0);
    g.fillText(span + ' h', W - 60, 20);
  }
  function line(g, rs, px, py, val, color, lw) {
    g.strokeStyle = color; g.lineWidth = lw;
    g.beginPath();
    var started = false;
    rs.forEach(function (r) {
      var v = val(r);
      if (v == null) return;
      if (!started) { g.moveTo(px(r.frame.timeMs), py(v)); started = true; }
      else g.lineTo(px(r.frame.timeMs), py(v));
    });
    g.stroke();
  }

  // ---- markers on the cockpit panes ------------------------------------------
  // crosshair line features at the fixed center (+ faint rejected candidates)
  // on every ready pane map — the panes are geo-referenced, so an out-of-view
  // marker simply doesn't show.
  function crossFeature(lat, lon, sizeDeg, props) {
    var s = sizeDeg, gap = sizeDeg * 0.3;
    return [
      { type: 'Feature', properties: props, geometry: { type: 'LineString',
        coordinates: [[lon - s, lat], [lon - gap, lat]] } },
      { type: 'Feature', properties: props, geometry: { type: 'LineString',
        coordinates: [[lon + gap, lat], [lon + s, lat]] } },
      { type: 'Feature', properties: props, geometry: { type: 'LineString',
        coordinates: [[lon, lat - s], [lon, lat - gap]] } },
      { type: 'Feature', properties: props, geometry: { type: 'LineString',
        coordinates: [[lon, lat + gap], [lon, lat + s]] } }
    ];
  }
  function paneMarkers(r) {
    var CX = window.__cockpit;
    if (!CX || !CX.panes) return;
    var feats = [];
    var c = r.archer.center || r.archer.weakCenter;
    if (c) feats = feats.concat(crossFeature(c.lat, c.lon, 0.22, { kind: r.archer.center ? 'fix' : 'weak' }));
    (r.archer.rejected || []).forEach(function (rc) {
      feats = feats.concat(crossFeature(rc.lat, rc.lon, 0.13, { kind: 'rejected' }));
    });
    var fc = { type: 'FeatureCollection', features: feats };
    CX.panes.forEach(function (p) {
      if (!p || !p.tv || !p.tv.map || !p.ready) return;
      var map = p.tv.map;
      if (!map.getSource('objfix')) {
        map.addSource('objfix', { type: 'geojson', data: fc });
        map.addLayer({ id: 'objfix-rej', type: 'line', source: 'objfix',
          filter: ['==', ['get', 'kind'], 'rejected'],
          paint: { 'line-color': '#ffffff', 'line-opacity': 0.30, 'line-width': 1.1 } });
        // dasharray can't be data-driven in MapLibre -> separate weak layer
        map.addLayer({ id: 'objfix-fix', type: 'line', source: 'objfix',
          filter: ['==', ['get', 'kind'], 'fix'],
          paint: { 'line-color': '#49b6c8', 'line-opacity': 0.95, 'line-width': 2 } });
        map.addLayer({ id: 'objfix-weak', type: 'line', source: 'objfix',
          filter: ['==', ['get', 'kind'], 'weak'],
          paint: { 'line-color': '#49b6c8', 'line-opacity': 0.75, 'line-width': 2,
                   'line-dasharray': [2, 2] } });
      } else {
        map.getSource('objfix').setData(fc);
      }
    });
  }

  var noteTimer = null;
  function note(msg, busy) {
    $('ofx-note').textContent = msg || '';
  }
  function warn(msg) {
    $('ofx-warn').style.display = 'block';
    $('ofx-warn').textContent = msg;
  }

  function boot() {
    if (!document.querySelector('.cx-view')) return;   // cockpit not on this page
    buildPanel();
    addToolButton();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
