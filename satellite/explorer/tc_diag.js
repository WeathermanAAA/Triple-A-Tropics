/* tc_diag.js — TC-DIAGNOSTICS MODE for the explorer cockpit: a first-class
 * STORM-ANALYSIS DASHBOARD. Picking a storm opens its full analytical
 * worksheet: the stage splits into storm-centered imagery (left) and the
 * diagnostics board (right) — real panels with real space, not cards
 * overlaying the map.
 *
 * Board sections:
 *   1 · Objective Center + Intensity — the ObjFix panel (ARCHER/ADT ports),
 *       the SAME DOM node re-parented (window.ObjFixPanel.dock — canvases/
 *       worker/state survive), restyled wide (scene | stats side by side).
 *   2 · IR Hovmöller — azimuthal-mean BT vs radius vs time (tc_panels.js)
 *   3 · DAV — deviation-angle variance time series (tc_panels.js)
 *   4 · Pipeline — honest greyed SOON slots for the unbuilt diagnostics.
 *
 * Data flow: the diagnostics CONSUME the objfix per-frame pipeline (loop
 * analysis) — each frame's radial profile + DAV are computed in the worker
 * while that frame's BT grid is still alive (the grid itself is discarded
 * per the loop-memory rule; only small derived arrays survive on results).
 * objfix_panel calls TCDiag.onFrameResult after every analyzed frame.
 *
 * LIVE + ARCHIVE: live storms auto-run the loop workup on selection; in
 * Time Machine the board offers "Analyze archive window" over the loaded
 * TM frames (per-frame objfix on GridSat/archive BT), and scrubbing
 * highlights the corresponding column. Every diagnostic here must take
 * the archive path — first-class requirement.
 *
 * HONESTY: everything on the board is an AUTOMATED OBJECTIVE SATELLITE
 * ESTIMATE — banner always visible; unbuilt diagnostics are greyed SOON
 * slots, never faked; low-confidence center fixes dim/flag the dependent
 * columns (the panels own that logic).
 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  // the mode's remaining pipeline — greyed slots, never faked data
  var SOON = [
    { title: 'Sat Intensity Fixes', meta: 'agency + objective fix history on one timeline' },
    { title: 'WN-1 Asymmetry', meta: 'wavenumber-1 cloud-top asymmetry' },
    { title: 'Eye / CDO Metrics', meta: 'eye diameter · CDO size · BT statistics' },
    { title: 'Environmental Favorability', meta: 'shear · SST/OHC · mid-level RH scorecard' },
    { title: 'GLM Lightning Trend', meta: 'inner-core flash-rate trend (GOES basins)' }
  ];

  var S = { on: false, built: false, autoRan: false };

  var CSS = '' +
    /* stage split: imagery | worksheet board */
    'body.cx-tcd-mode .cx-view{display:grid;grid-template-columns:minmax(340px,42%) minmax(0,1fr)}' +
    'body.cx-tcd-mode #cx-panes{min-width:0;min-height:0}' +
    '#tcd-board{display:none;min-width:0;min-height:0;overflow-y:auto;overflow-x:hidden;' +
    ' background:#0c111a;border-left:1px solid var(--cx-line);scrollbar-width:thin;' +
    ' font-family:Metropolis,system-ui,sans-serif}' +
    'body.cx-tcd-mode #tcd-board{display:block}' +
    /* sticky worksheet header */
    '.tcd-bhead{position:sticky;top:0;z-index:6;background:rgba(13,18,25,.97);' +
    ' backdrop-filter:blur(2px);border-bottom:1px solid var(--cx-line);padding:10px 16px 9px}' +
    '.tcd-title{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}' +
    '.tcd-title h4{margin:0;font-size:14.5px;font-weight:700;color:var(--cx-fg);' +
    ' letter-spacing:.02em;white-space:nowrap}' +
    '.tcd-title .tcd-sub{font-size:10px;font-weight:700;letter-spacing:.14em;' +
    ' text-transform:uppercase;color:var(--cx-dim)}' +
    '.tcd-x{margin-left:auto;background:none;border:0;color:var(--cx-mut);font-size:15px;' +
    ' cursor:pointer;padding:2px 6px}' +
    '.tcd-x:hover{color:#fff}' +
    '.tcd-ctl{display:flex;gap:6px;align-items:center;margin-top:8px;flex-wrap:wrap}' +
    '#tcd-storm{flex:1;min-width:170px;font-family:inherit;font-size:12px;color:var(--cx-fg);' +
    ' background:rgba(17,23,33,.9);border:1px solid var(--cx-line);border-radius:7px;padding:6px 8px}' +
    '.tcd-btn{font-family:inherit;font-size:11.5px;font-weight:600;color:var(--cx-fg);' +
    ' background:rgba(17,23,33,.9);border:1px solid var(--cx-line);border-radius:8px;' +
    ' padding:6px 10px;cursor:pointer;white-space:nowrap}' +
    '.tcd-btn:hover:not(:disabled){border-color:var(--cx-teal);color:var(--cx-teal)}' +
    '.tcd-btn:disabled{opacity:.4;cursor:default}' +
    '#tcd-status{margin-top:6px;font-size:10.5px;color:var(--cx-dim);line-height:1.5;' +
    ' font-variant-numeric:tabular-nums;min-height:14px}' +
    '.tcd-banner{margin-top:7px;font-size:9.5px;font-weight:700;letter-spacing:.08em;' +
    ' text-transform:uppercase;color:#c9a35a;border:1px solid rgba(201,163,90,.4);' +
    ' border-radius:6px;padding:5px 8px;line-height:1.5}' +
    '.tcd-banner span{display:block;font-weight:500;letter-spacing:.02em;text-transform:none;' +
    ' color:#a8905c;font-size:10px}' +
    /* worksheet sections */
    '#tcd-secs{padding:12px 16px 22px;display:flex;flex-direction:column;gap:12px}' +
    '.tcd-sec{border:1px solid var(--cx-line-soft);border-radius:10px;padding:11px 13px;' +
    ' background:rgba(17,23,33,.45)}' +
    '.tcd-sec>h5{margin:0 0 8px;font-size:11.5px;font-weight:700;color:var(--cx-fg);' +
    ' display:flex;align-items:center;gap:8px;letter-spacing:.03em}' +
    '.tcd-sec>h5 .cx-chip.live{color:#6fd08c;border-color:rgba(111,208,140,.45)}' +
    '.tcd-sec>h5 .cx-chip.exp{color:#49b6c8;border-color:rgba(73,182,200,.45)}' +
    '.tcd-sec>h5 .tcd-hmeta{margin-left:auto;font-size:9.5px;font-weight:500;color:var(--cx-dim);' +
    ' letter-spacing:.02em;text-transform:none}' +
    /* objfix docked WIDE: scene | stats side by side, trend full-width */
    '#tcd-slot-objfix #ofx-panel.ofx-docked .ofx-body{display:grid;' +
    ' grid-template-columns:minmax(260px,340px) minmax(0,1fr);gap:6px 14px;align-items:start;' +
    ' grid-template-areas:"r1 r1" "r2 r2" "pg pg" "sc st" "sc wn" "tr tr" "nt nt" "n2 n2"}' +
    '#tcd-slot-objfix .ofx-body>.ofx-row:nth-of-type(1){grid-area:r1;display:none}' +
    '#tcd-slot-objfix .ofx-body>.ofx-row:nth-of-type(2){grid-area:r2;display:none}' +
    '#tcd-slot-objfix .ofx-body>.ofx-prog{grid-area:pg;display:none!important}' +
    '#tcd-slot-objfix #ofx-scene{grid-area:sc;width:100%;height:auto}' +
    '#tcd-slot-objfix #ofx-stats{grid-area:st}' +
    '#tcd-slot-objfix #ofx-warn{grid-area:wn;align-self:start}' +
    '#tcd-slot-objfix #ofx-trend{grid-area:tr;height:150px}' +
    '#tcd-slot-objfix #ofx-note{grid-area:nt}' +
    '#tcd-slot-objfix .ofx-body>.ofx-note:last-child{grid-area:n2}' +
    '@media (max-width:1500px){#tcd-slot-objfix #ofx-panel.ofx-docked .ofx-body{' +
    ' grid-template-columns:1fr;grid-template-areas:"r1" "r2" "pg" "sc" "st" "wn" "tr" "nt" "n2"}' +
    ' #tcd-slot-objfix #ofx-scene{max-width:420px}}' +
    /* panel hosts (tc_panels.js mounts here) + SOON tiles */
    '.tcd-phost{min-height:60px}' +
    '.tcd-empty{font-size:11px;color:var(--cx-dim);line-height:1.6;padding:6px 2px}' +
    '.tcd-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:8px}' +
    '.tcd-card{border:1px solid var(--cx-line-soft);border-radius:9px;padding:9px 11px;' +
    ' background:rgba(17,23,33,.45);opacity:.5}' +
    '.tcd-card>h6{margin:0;font-size:10.5px;font-weight:700;color:var(--cx-fg);display:flex;' +
    ' align-items:center;gap:7px}' +
    '.tcd-card .tcd-meta{margin-top:3px;font-size:9.5px;color:var(--cx-dim);line-height:1.5}' +
    /* compact screens: board stacks below the imagery */
    '@media (max-width:1080px){' +
    ' body.cx-tcd-mode .cx-view{display:block;overflow:visible}' +
    ' #tcd-board{border-left:0;border-top:1px solid var(--cx-line);max-height:none}}';

  function buildButton() {
    var tm = $('cx-tm');
    if (!tm) return;
    var b = document.createElement('button');
    b.type = 'button'; b.className = 'cx-btn'; b.id = 'cx-tcd';
    b.title = 'TC-Diagnostics — the storm-analysis dashboard (objective, experimental)';
    b.innerHTML = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" ' +
      'stroke-linecap="round"><use href="#i-inspect"/></svg><span class="lbl">TC Diag</span>' +
      '<i class="cx-chip">beta</i>';
    b.onclick = function () { S.on ? exitMode() : enterMode(); };
    tm.parentNode.insertBefore(b, tm.nextSibling);
    // TC-Diagnostics COEXISTS with Time Machine (the deep archive feeds the
    // diagnostics per scrubbed frame); only Reset exits the mode.
    var rst = $('cx-reset');
    if (rst) rst.addEventListener('click', function () { if (S.on) exitMode(); }, true);
  }

  function buildBoard() {
    if (S.built) return;
    S.built = true;
    var st = document.createElement('style');
    st.textContent = CSS;
    document.head.appendChild(st);
    var el = document.createElement('div');
    el.id = 'tcd-board';
    el.innerHTML =
      '<div class="tcd-bhead">' +
      '  <div class="tcd-title"><span class="tcd-sub">Storm analysis</span>' +
      '    <h4 id="tcd-name">—</h4>' +
      '    <button type="button" class="tcd-x" id="tcd-x" title="Exit TC-Diagnostics">✕</button></div>' +
      '  <div class="tcd-ctl">' +
      '    <select id="tcd-storm"></select>' +
      '    <button type="button" class="tcd-btn" id="tcd-run">Analyze loop</button>' +
      '    <button type="button" class="tcd-btn" id="tcd-stop" disabled>Stop</button>' +
      '    <button type="button" class="tcd-btn" id="tcd-dl" title="Center-track + diagnostics JSON">Track JSON</button>' +
      '  </div>' +
      '  <div id="tcd-status"></div>' +
      '  <div class="tcd-banner">Storm-centered objective diagnostics' +
      '    <span>Experimental, automated, computed from satellite data only — not official.' +
      '    See NHC / JTWC advisories for official positions and intensities.</span></div>' +
      '</div>' +
      '<div id="tcd-secs">' +
      '  <div class="tcd-sec" id="tcd-sec-objfix">' +
      '    <h5>1 · Objective Center + Intensity <i class="cx-chip live">live</i>' +
      '      <span class="tcd-hmeta">ARCHER + ADT ports · per-frame</span></h5>' +
      '    <div id="tcd-slot-objfix"></div>' +
      '  </div>' +
      '  <div class="tcd-sec" id="tcd-sec-hov">' +
      '    <h5>2 · IR Hovmöller <i class="cx-chip exp">experimental</i>' +
      '      <span class="tcd-hmeta">azimuthal-mean BT · radius × time</span></h5>' +
      '    <div class="tcd-phost" id="tcd-p-hov"></div>' +
      '  </div>' +
      '  <div class="tcd-sec" id="tcd-sec-dav">' +
      '    <h5>3 · Deviation-Angle Variance <i class="cx-chip exp">experimental</i>' +
      '      <span class="tcd-hmeta">IR axisymmetry index · time series</span></h5>' +
      '    <div class="tcd-phost" id="tcd-p-dav"></div>' +
      '  </div>' +
      '  <div class="tcd-sec" id="tcd-sec-satcon">' +
      '    <h5>4 · Objective Intensity Consensus <i class="cx-chip exp">experimental</i>' +
      '      <span class="tcd-hmeta">SATCON-method blend · ADT-port + MW-imager</span></h5>' +
      '    <div class="tcd-phost" id="tcd-p-satcon"></div>' +
      '  </div>' +
      '  <div class="tcd-sec" id="tcd-sec-soon">' +
      '    <h5>5 · Pipeline <i class="cx-chip">soon</i>' +
      '      <span class="tcd-hmeta">planned scope — nothing faked</span></h5>' +
      '    <div class="tcd-grid">' +
      SOON.map(function (d) {
        return '<div class="tcd-card"><h6>' + d.title + ' <i class="cx-chip">soon</i></h6>' +
          '<div class="tcd-meta">' + d.meta + '</div></div>';
      }).join('') +
      '    </div>' +
      '  </div>' +
      '</div>';
    document.querySelector('.cx-view').appendChild(el);
    $('tcd-x').onclick = exitMode;
    $('tcd-run').onclick = function () { runWorkup(); };
    $('tcd-stop').onclick = function () {
      if (window.ObjFixPanel && window.ObjFixPanel.stop) window.ObjFixPanel.stop();
      else { var b = $('ofx-stop'); if (b) b.click(); }   // pre-API fallback
    };
    $('tcd-dl').onclick = function () {
      if (window.ObjFixPanel && window.ObjFixPanel.downloadTrack) window.ObjFixPanel.downloadTrack();
    };
    $('tcd-storm').onchange = function () {
      if (!window.ObjFixPanel) return;
      window.ObjFixPanel.select(+this.value);
      var st = window.ObjFixPanel.storm();
      $('tcd-name').textContent = st ? st.name + ' · ' + st.basin +
        (st.vmax ? ' · ' + st.vmax + ' kt' : '') : '—';
      frameStorm();
      mountPanels();
      // picking a storm opens its workup: auto-run the LOOP analysis (the
      // Hovmöller/DAV need the trend window, not one frame). Stop cancels.
      if (!window.ObjFixPanel.running()) {
        window.ObjFixPanel.analyze(true);
        $('tcd-stop').disabled = false;
      }
    };
  }

  function mountPanels() {
    if (window.SatCon && window.SatCon.mount) {
      window.SatCon.mount($('tcd-p-satcon'));
      window.SatCon.setStorm(window.ObjFixPanel && window.ObjFixPanel.storm());
    } else if ($('tcd-p-satcon')) {
      $('tcd-p-satcon').innerHTML = '<div class="tcd-empty">SATCON-method consensus ' +
        '(ADT-port + MW-imager members) — lands with satcon.js</div>';
    }
    if (window.TCPanels && window.TCPanels.mount)
      window.TCPanels.mount($('tcd-p-hov'), $('tcd-p-dav'));
    else {
      // panels ship separately (tc_panels.js) — honest placeholder, no fakes
      $('tcd-p-hov').innerHTML = '<div class="tcd-empty">azimuthal-mean BT vs radius vs time, ' +
        'per-frame objfix centers — in build, lands with tc_panels.js</div>';
      $('tcd-p-dav').innerHTML = '<div class="tcd-empty">deviation-angle variance time series ' +
        '(Piñeros / Ritchie / Tyo technique) — in build, lands with tc_panels.js</div>';
    }
  }

  function inTM() {
    var CX = window.__cockpit;
    return !!(CX && CX.tm && CX.tm.on);
  }

  function fillStorms(storms) {
    var sel = $('tcd-storm');
    sel.innerHTML = '';
    if (inTM()) {
      // archive mode: the storm is whatever the user framed in the panes —
      // the workup runs over the loaded Time Machine window
      sel.innerHTML = '<option>— archive view (frame the storm in the pane) —</option>';
      sel.disabled = true;
      $('tcd-name').textContent = 'ARCHIVE VIEW';
      syncArchiveUI();
      return;
    }
    sel.disabled = false;
    if (!storms || !storms.length) {
      sel.innerHTML = '<option>— no active storms in the feeds —</option>';
      $('tcd-name').textContent = '—';
      status('No active storms. Time Machine + "Analyze archive window" works on historical storms.');
      return;
    }
    storms.forEach(function (s, i) {
      var o = document.createElement('option');
      o.value = String(i);
      o.textContent = s.name + ' · ' + s.basin + (s.vmax ? ' · ' + s.vmax + ' kt' : '');
      sel.appendChild(o);
    });
    sel.value = '0';
    sel.onchange.call(sel);
  }

  // anchor: frame the storm on EVERY ready pane (multi-pane dashboards get
  // the storm in all fields; with linked cameras the lead pane propagates,
  // unlinked panes each fit themselves). The objfix center markers land on
  // every pane map via objfix_panel.paneMarkers.
  function frameStorm() {
    var P = window.ObjFixPanel, CX = window.__cockpit;
    var st = P && P.storm();
    if (!st || st.lat == null || !CX) return;
    var box = [[st.lon - 6, st.lat - 4.5], [st.lon + 6, st.lat + 4.5]];
    var panes = CX.linked ? [CX.panes[0]] : CX.panes;
    (panes || []).forEach(function (pane) {
      if (pane && pane.ready) pane.tv.map.fitBounds(box, { padding: 20, duration: 600 });
    });
  }

  function resizePanes() {
    var CX = window.__cockpit;
    if (!CX || !CX.panes) return;
    // container width changed with the board toggle — MapLibre observes it,
    // but resize NOW so fitBounds right after lands on the final size
    CX.panes.forEach(function (p) {
      if (p && p.ready && p.tv && p.tv.map) { try { p.tv.map.resize(); } catch (e) {} }
    });
  }

  function runWorkup() {
    if (!window.ObjFixPanel || window.ObjFixPanel.running()) return;
    if (inTM()) {
      if (window.ObjFixPanel.analyzeArchiveLoop) window.ObjFixPanel.analyzeArchiveLoop();
    } else {
      window.ObjFixPanel.analyze(true);
    }
    $('tcd-stop').disabled = false;
  }

  function status(msg) {
    var el = $('tcd-status');
    if (el) el.textContent = msg || '';
  }

  function syncArchiveUI() {
    var CX = window.__cockpit;
    var run = $('tcd-run');
    if (!run) return;
    if (inTM()) {
      var n = CX.tm.frames ? CX.tm.frames.length : 0;
      run.textContent = 'Analyze archive window';
      run.disabled = n < 2;
      status(n >= 2
        ? n + ' archive frames loaded — Analyze runs the objective workup over the window.'
        : 'Load a Time Machine loop first (Load loop), then analyze the window.');
    } else {
      run.textContent = 'Analyze loop';
      run.disabled = false;
    }
  }

  function enterMode() {
    S.on = true;
    document.body.classList.add('cx-tcd-mode');
    $('cx-tcd').classList.add('on');
    buildBoard();
    resizePanes();
    mountPanels();
    if (window.ObjFixPanel) {
      window.ObjFixPanel.dock($('tcd-slot-objfix'));
      window.ObjFixPanel.loadStorms().then(fillStorms);
      // a workup that already ran (panel used standalone) shows immediately
      if (window.TCPanels && window.ObjFixPanel.results)
        window.TCPanels.update(window.ObjFixPanel.results(), { running: window.ObjFixPanel.running() });
    }
    syncArchiveUI();
  }

  function exitMode() {
    if (!S.on) return;
    S.on = false;
    document.body.classList.remove('cx-tcd-mode');
    var b = $('cx-tcd'); if (b) b.classList.remove('on');
    if (window.ObjFixPanel) window.ObjFixPanel.undock();
    resizePanes();
  }

  function boot() {
    if (!document.querySelector('.cx-view') || !$('cx-tm')) return;  // cockpit only
    buildButton();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();

  // ---- per-frame pipeline hooks (objfix_panel calls these) -----------------
  function onFrameResult(results, state) {
    if (!S.on) return;
    if (window.TCPanels) window.TCPanels.update(results, state || {});
    if (window.SatCon) {
      window.SatCon.setStorm(window.ObjFixPanel && window.ObjFixPanel.storm());
      window.SatCon.update(results);
    }
    if (state && state.running) {
      status('analyzing — ' + results.length + ' frame' + (results.length === 1 ? '' : 's') + ' so far…');
      $('tcd-stop').disabled = false;
    } else {
      status(results.length + ' frame' + (results.length === 1 ? '' : 's') + ' analyzed.');
      $('tcd-stop').disabled = true;
    }
  }

  // Time Machine tie-in: scrubbing an archive frame either highlights its
  // column (a window workup already covers it) or recomputes the single-frame
  // objfix estimate (debounced so a fast drag analyzes the frame you settle
  // on). Every future diagnostic must take this hook — archive-frame support
  // is a first-class requirement.
  var _afTimer = null;
  function onArchiveFrame(stamp) {
    if (!S.on || !window.ObjFixPanel) return;
    syncArchiveUI();
    if (window.TCPanels && window.TCPanels.highlight && window.TCPanels.highlight(stamp)) {
      return;   // covered by a window workup — no recompute on scrub
    }
    if (_afTimer) clearTimeout(_afTimer);
    _afTimer = setTimeout(function () {
      if (S.on && !window.ObjFixPanel.running())
        window.ObjFixPanel.analyzeArchive(stamp);
    }, 500);
  }

  window.TCDiag = { enter: enterMode, exit: exitMode,
                    active: function () { return S.on; },
                    onArchiveFrame: onArchiveFrame,
                    onFrameResult: onFrameResult };
})();
