/* tc_diag.js — TC-DIAGNOSTICS MODE for the explorer cockpit: the container
 * for storm-centered analytical products. A third mode alongside Live and
 * Time Machine (mutually exclusive with TM; Reset exits it).
 *
 * Storm-centered: ONE storm selector (the same active-storm list the Obj Fix
 * panel builds from the live feeds — reused, not re-fetched) drives a
 * per-storm dashboard. The ANCHOR is the cockpit's primary pane framed on the
 * storm with the objective center marked (objfix's paneMarkers draw on every
 * pane); the diagnostics dock at right stacks one card per diagnostic.
 *
 * HONESTY: only Obj Fix is live — it is diagnostic card #1, the SAME DOM node
 * re-parented (window.ObjFixPanel.dock, canvases/worker/state survive), with
 * its full honesty contract intact. Every other diagnostic is a greyed SOON
 * card so the mode's scope is visible without faking a single number.
 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  // the mode's planned scope — greyed cards, never faked data
  var SOON = [
    { title: 'Sat Intensity Fixes', meta: 'agency + objective fix history on one timeline' },
    { title: 'SATCON Consensus', meta: 'weighted multi-source intensity consensus' },
    { title: 'DAV', meta: 'deviation-angle variance — IR organization index' },
    { title: 'WN-1 Asymmetry', meta: 'wavenumber-1 cloud-top asymmetry' },
    { title: 'Eye / CDO Metrics', meta: 'eye diameter · CDO size · BT statistics' },
    { title: 'IR Hovmöller', meta: 'azimuthal-mean BT vs radius vs time (ObjFix track input)' },
    { title: 'Environmental Favorability', meta: 'shear · SST/OHC · mid-level RH scorecard' },
    { title: 'GLM Lightning Trend', meta: 'inner-core flash-rate trend (GOES basins)' }
  ];

  var S = { on: false, built: false };

  var CSS = '' +
    '#tcd-dock{position:absolute;top:0;right:0;bottom:0;width:392px;z-index:8;display:none;' +
    ' background:rgba(13,18,25,.97);border-left:1px solid var(--cx-line);overflow-y:auto;' +
    ' font-family:Metropolis,system-ui,sans-serif;scrollbar-width:thin}' +
    'body.cx-tcd-mode #tcd-dock{display:block}' +
    '.tcd-head{position:sticky;top:0;z-index:3;background:#0d1219;padding:10px 14px 10px;' +
    ' border-bottom:1px solid var(--cx-line)}' +
    '.tcd-head h4{margin:0;font-size:13px;font-weight:700;color:var(--cx-fg);display:flex;' +
    ' align-items:center;justify-content:space-between;gap:8px}' +
    '.tcd-x{background:none;border:0;color:var(--cx-mut);font-size:15px;cursor:pointer;padding:2px 6px}' +
    '.tcd-x:hover{color:#fff}' +
    '.tcd-banner{margin-top:7px;font-size:9.5px;font-weight:700;letter-spacing:.08em;' +
    ' text-transform:uppercase;color:#c9a35a;border:1px solid rgba(201,163,90,.4);' +
    ' border-radius:6px;padding:5px 8px;line-height:1.5}' +
    '.tcd-banner span{display:block;font-weight:500;letter-spacing:.02em;text-transform:none;' +
    ' color:#a8905c;font-size:10px}' +
    '.tcd-head label{display:block;margin:8px 0 3px;font-size:9.5px;font-weight:700;' +
    ' letter-spacing:.1em;text-transform:uppercase;color:var(--cx-dim)}' +
    '#tcd-storm{width:100%;font-family:inherit;font-size:12px;color:var(--cx-fg);' +
    ' background:rgba(17,23,33,.9);border:1px solid var(--cx-line);border-radius:7px;padding:6px 8px}' +
    '#tcd-cards{padding:8px 12px 16px;display:flex;flex-direction:column;gap:8px}' +
    '.tcd-card{border:1px solid var(--cx-line-soft);border-radius:9px;padding:9px 11px;' +
    ' background:rgba(17,23,33,.45)}' +
    '.tcd-card>h5{margin:0;font-size:11px;font-weight:700;color:var(--cx-fg);display:flex;' +
    ' align-items:center;gap:7px}' +
    '.tcd-card>h5 .cx-chip.live{color:#6fd08c;border-color:rgba(111,208,140,.45)}' +
    '.tcd-card .tcd-meta{margin-top:3px;font-size:10px;color:var(--cx-dim);line-height:1.5}' +
    '.tcd-card.coming{opacity:.5}' +
    '.tcd-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}' +
    '.tcd-grid .tcd-card{margin:0}';

  function buildButton() {
    var tm = $('cx-tm');
    if (!tm) return;
    var b = document.createElement('button');
    b.type = 'button'; b.className = 'cx-btn'; b.id = 'cx-tcd';
    b.title = 'TC-Diagnostics — storm-centered analytical products (experimental)';
    b.innerHTML = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" ' +
      'stroke-linecap="round"><use href="#i-inspect"/></svg><span class="lbl">TC Diag</span>' +
      '<i class="cx-chip">beta</i>';
    b.onclick = function () { S.on ? exitMode() : enterMode(); };
    tm.parentNode.insertBefore(b, tm.nextSibling);
    // mutual exclusion: entering Time Machine (or Reset) leaves TC-Diagnostics
    tm.addEventListener('click', function () { if (S.on) exitMode(); }, true);
    var rst = $('cx-reset');
    if (rst) rst.addEventListener('click', function () { if (S.on) exitMode(); }, true);
  }

  function buildDock() {
    if (S.built) return;
    S.built = true;
    var st = document.createElement('style');
    st.textContent = CSS;
    document.head.appendChild(st);
    var el = document.createElement('div');
    el.id = 'tcd-dock';
    el.innerHTML =
      '<div class="tcd-head">' +
      '  <h4>TC Diagnostics' +
      '    <button type="button" class="tcd-x" id="tcd-x" title="Exit TC-Diagnostics">✕</button></h4>' +
      '  <div class="tcd-banner">Storm-centered objective diagnostics' +
      '    <span>Experimental, automated, computed from satellite data only — not official.' +
      '    See NHC / JTWC advisories for official positions and intensities.</span></div>' +
      '  <label>Storm</label><select id="tcd-storm"></select>' +
      '</div>' +
      '<div id="tcd-cards">' +
      '  <div class="tcd-card" id="tcd-card-objfix">' +
      '    <h5>1 · Objective Center + Intensity <i class="cx-chip live">live</i></h5>' +
      '    <div id="tcd-slot-objfix"></div>' +
      '  </div>' +
      '  <div class="tcd-grid">' +
      SOON.map(function (d, i) {
        return '<div class="tcd-card coming">' +
          '<h5>' + (i + 2) + ' · ' + d.title + ' <i class="cx-chip">soon</i></h5>' +
          '<div class="tcd-meta">' + d.meta + '</div></div>';
      }).join('') +
      '  </div>' +
      '</div>';
    document.querySelector('.cx-view').appendChild(el);
    $('tcd-x').onclick = exitMode;
    $('tcd-storm').onchange = function () {
      if (!window.ObjFixPanel) return;
      window.ObjFixPanel.select(+this.value);
      frameStorm();
      if (!window.ObjFixPanel.running()) window.ObjFixPanel.analyze(false);
    };
  }

  function fillStorms(storms) {
    var sel = $('tcd-storm');
    sel.innerHTML = '';
    if (!storms || !storms.length) {
      sel.innerHTML = '<option>— no active storms in the feeds —</option>';
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

  function enterMode() {
    var CX = window.__cockpit;
    if (CX && CX.tm && CX.tm.on) $('cx-tm').click();     // leave Time Machine first
    S.on = true;
    document.body.classList.add('cx-tcd-mode');
    $('cx-tcd').classList.add('on');
    buildDock();
    if (window.ObjFixPanel) {
      window.ObjFixPanel.dock($('tcd-slot-objfix'));
      window.ObjFixPanel.loadStorms().then(fillStorms);
    }
  }

  function exitMode() {
    if (!S.on) return;
    S.on = false;
    document.body.classList.remove('cx-tcd-mode');
    var b = $('cx-tcd'); if (b) b.classList.remove('on');
    if (window.ObjFixPanel) window.ObjFixPanel.undock();
  }

  function boot() {
    if (!document.querySelector('.cx-view') || !$('cx-tm')) return;  // cockpit only
    buildButton();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();

  window.TCDiag = { enter: enterMode, exit: exitMode, active: function () { return S.on; } };
})();
