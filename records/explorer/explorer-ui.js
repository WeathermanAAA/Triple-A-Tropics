/* explorer-ui.js
   Boot + controls for the TC track explorer (shadow). Consumes XPData
   (explorer-data.js) and XPMap (explorer-map.js). Defensive throughout:
   a missing manifest renders one quiet error panel, a missing map library
   leaves the filters and storm list fully working. All data-driven DOM is
   built with createElement/textContent, never innerHTML. */
(function () {
  'use strict';

  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var MC_KEYS = { td: 1, ts: 1, c1: 1, c2: 1, c3: 1, c4: 1, c5: 1 };
  var U_KEYS = { kt: 1, mph: 1, kmh: 1 };
  var UP_KEYS = { mb: 1, inhg: 1 };
  var PAL_LOCAL = {
    std: { TD: '#3fa4ff', TS: '#46c56a', C1: '#ffe14d', C2: '#ff9a2f', C3: '#f5333c', C4: '#e33ad4', C5: '#b03bff' },
    cb: { TD: '#999999', TS: '#56B4E9', C1: '#009E73', C2: '#F0E442', C3: '#E69F00', C4: '#D55E00', C5: '#CC79A7' }
  };
  var LIST_CAP = 400;
  var DETAIL_MAX = 800;
  var EXPORT_CAP = 2000;

  var state = {
    b: 'al', y0: null, y1: null, mc: 'ts', m0: 1, m1: 12, q: '',
    loc: null, sel: null, pin: [], u: 'kt', up: 'mb', pal: 'std',
    dens: '', v: null
  };

  var els = {};
  var byI = {};              /* storm i -> catalog storm, current basin */
  var curIds = [];           /* current filter result (catalog order) */
  var radiusRows = null;     /* ranked radius results or null */
  var radiusLabel = '';
  var mapOk = false;
  var mapDead = false;
  var gen = 0;               /* async generation guard (basin switches) */
  var searchGen = 0;
  var searchTimer = null;
  var yearsTouched = false;
  var basinSyncing = false;
  var firstRefreshDone = false;
  var userView = false;      /* view came from the user (URL v or a manual
                                pan/zoom), not from programmatic moves */

  /* ---- tiny DOM helpers ---- */

  function $(id) { return document.getElementById(id); }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }

  function chipEl(cat) { return el('span', 'xp-chip c-' + cat, cat); }

  function p2(n) { return (n < 10 ? '0' : '') + n; }

  function palette() {
    var p = (window.XPMap && XPMap.PALETTES && XPMap.PALETTES[state.pal]) || PAL_LOCAL[state.pal];
    return p || PAL_LOCAL.std;
  }

  /* ---- data shortcuts ---- */

  function manifestBasin() {
    var m = XPData.manifest();
    return (m && m.basins && m.basins[state.b]) || null;
  }

  function prov() {
    var c = XPData.catalog();
    return (c && c.provenance) || {};
  }

  function currentSeason() {
    var p = prov();
    if (p.current_season) return p.current_season;
    return new Date().getUTCFullYear();
  }

  function defaultY0() {
    var mb = manifestBasin();
    return (mb && mb.records_since) || 1851;
  }

  function stormDecade(i) {
    var s = byI[i];
    return s ? Math.floor(s.season / 10) * 10 : null;
  }

  function neededDecades() {
    var decs = XPData.requiredDecades(state.y0, state.y1);
    var extra = state.pin.slice();
    if (state.sel !== null && state.sel !== undefined) extra.push(state.sel);
    for (var k = 0; k < extra.length; k++) {
      var d = stormDecade(extra[k]);
      if (d !== null && decs.indexOf(d) < 0) decs.push(d);
    }
    return decs;
  }

  /* ---- URL ---- */

  function writeUrl() {
    var o = {};
    if (state.b !== 'al') o.b = state.b;
    if (state.y0 !== null && state.y0 !== defaultY0()) o.y0 = state.y0;
    if (state.y1 !== null && state.y1 !== currentSeason()) o.y1 = state.y1;
    if (state.mc !== 'ts') o.mc = state.mc;
    if (!(state.m0 === 1 && state.m1 === 12)) { o.m0 = state.m0; o.m1 = state.m1; }
    if (state.q) o.q = state.q;
    if (state.loc) o.loc = state.loc;
    if (state.sel !== null && state.sel !== undefined) o.sel = state.sel;
    if (state.pin.length) o.pin = state.pin;
    if (state.u !== 'kt') o.u = state.u;
    if (state.up !== 'mb') o.up = state.up;
    if (state.pal !== 'std') o.pal = state.pal;
    if (state.dens) o.dens = state.dens;
    if (state.v) o.v = state.v;
    XPData.urlWrite(o);
  }

  function readUrl() {
    var st = XPData.urlRead();
    if (st.b === 'al' || st.b === 'ep' || st.b === 'wp') state.b = st.b;
    if (st.y0 !== undefined) { state.y0 = st.y0; yearsTouched = true; }
    if (st.y1 !== undefined) { state.y1 = st.y1; yearsTouched = true; }
    if (st.mc && MC_KEYS[st.mc]) state.mc = st.mc;
    if (st.m0 >= 1 && st.m0 <= 12) state.m0 = st.m0;
    if (st.m1 >= 1 && st.m1 <= 12) state.m1 = st.m1;
    if (st.q) state.q = st.q;
    if (st.loc) state.loc = st.loc;
    if (st.sel !== undefined) state.sel = st.sel;
    if (st.pin) {
      state.pin = st.pin.slice(0, 2);
    }
    if (st.u && U_KEYS[st.u]) state.u = st.u;
    if (st.up && UP_KEYS[st.up]) state.up = st.up;
    if (st.pal === 'cb' || st.pal === 'std') state.pal = st.pal;
    if (st.dens && /^(track_all|genesis|track_m(0[1-9]|1[0-2]))$/.test(st.dens)) state.dens = st.dens;
    if (st.v) { state.v = st.v; userView = true; }
  }

  /* ---- error panels ---- */

  function recError(host, msg) {
    if (!host) return;
    host.textContent = '';
    host.appendChild(el('div', 'rec-error', msg));
  }

  function shellError() {
    var stampEl = $('recStamp');
    if (stampEl) stampEl.textContent = '';
    recError(els.shell, 'Explorer data is unavailable right now. It may still be generating.');
  }

  function catalogError() {
    if (els.count) els.count.textContent = '';
    recError(els.list, 'Storm catalog failed to load for this basin.');
  }

  function mapError() {
    mapDead = true;
    if (els.mapEl) {
      recError(els.mapEl, 'Map failed to load. Filters and the storm list still work.');
    }
  }

  /* ---- provenance ---- */

  function applyProvenance() {
    var p = prov();
    if (window.TATRecords && TATRecords.stamp) {
      try { TATRecords.stamp(p); } catch (e) {}
    }
    if (els.caveat) {
      var bits = [];
      if (p.wind_note) bits.push(p.wind_note);
      if (p.ace_note) bits.push(p.ace_note);
      var sat = p.satellite_era || (manifestBasin() && manifestBasin().satellite_era);
      if (sat) bits.push('Seasons before ' + sat + ' undercount real activity.');
      els.caveat.textContent = bits.join(' ');
    }
    if (els.mapNote) {
      var mb = manifestBasin();
      var line = (mb && mb.name ? mb.name + ' · ' : '') + (p.ibtracs || 'IBTrACS v04r01');
      if (p.generated) line += ' · updated ' + String(p.generated).slice(0, 10);
      els.mapNote.textContent = line;
    }
  }

  /* ---- catalog install ---- */

  function afterCatalog(cat) {
    byI = {};
    for (var k = 0; k < cat.storms.length; k++) byI[cat.storms[k].i] = cat.storms[k];
    if (!yearsTouched || state.y0 === null) state.y0 = defaultY0();
    if (!yearsTouched || state.y1 === null) state.y1 = currentSeason();
    if (els.y0) els.y0.value = state.y0;
    if (els.y1) els.y1.value = state.y1;
    applyProvenance();
  }

  /* ---- map ---- */

  function maybeFlyToSel() {
    if (mapOk && firstRefreshDone && state.sel !== null && state.sel !== undefined && !state.v) {
      try { XPMap.flyToStorm(state.sel); } catch (e) {}
    }
  }

  function createMap() {
    if (!window.maplibregl || !window.XPMap || typeof XPMap.create !== 'function') {
      mapError();
      return;
    }
    var p;
    try {
      p = XPMap.create(els.mapEl, {
        onClickStorm: function (i) { selectStorm(parseInt(i, 10)); },
        hoverText: hoverText,
        view: state.v || null,
        basin: state.b
      });
    } catch (e) { p = null; }
    if (!p || typeof p.then !== 'function') { mapError(); return; }
    p.then(function () {
      mapOk = true;
      try {
        XPMap.on('moveend', function (ev) {
          try { state.v = XPMap.getView(); } catch (e) {}
          /* only user gestures carry originalEvent; programmatic jumpTo /
             fitBounds moves must not count as an explicit user view */
          if (ev && ev.originalEvent) userView = true;
          writeUrl();
        });
      } catch (e) {}
      drawTracks();
      maybeFlyToSel();
    }).catch(function () { mapError(); });
  }

  function hoverText(props) {
    if (!props) return null;
    var s = byI[parseInt(props.i, 10)];
    if (!s) return null;
    /* [null, text] renders as the popup's emphasized header line */
    var head = (s.name || 'UNNAMED') + ' · ' + s.season;
    if (props.t !== undefined && props.t !== null) {
      return [
        [null, head],
        ['Time', XPData.tsFmt(props.t)],
        ['Wind', props.wind === null || props.wind === undefined ? 'n/a' : XPData.fmtWind(props.wind, state.u)],
        ['Pres', props.pres === null || props.pres === undefined ? 'n/a' : XPData.fmtPres(props.pres, state.up)],
        ['Cat', XPData.catFromWind(props.wind)]
      ];
    }
    var rows = [[null, head]];
    if (s.peak !== null && s.peak !== undefined) {
      rows.push(['Peak', XPData.catFromWind(s.peak) + ' · ' + XPData.fmtWind(s.peak, state.u)]);
    }
    return rows;
  }

  function resolveDensity() {
    if (!state.dens) return null;
    var m = XPData.manifest();
    if (!m || !m.density) return null;
    var key = state.b + '_' + state.dens;
    for (var k = 0; k < m.density.length; k++) {
      if (m.density[k].key === key) return m.density[k];
    }
    return null;
  }

  function updateDensityHint() {
    if (!els.densHint) return;
    els.densHint.textContent = (state.dens && !resolveDensity()) ?
      'No overlay for this month in this basin.' : '';
  }

  function drawTracks() {
    if (!mapOk) return;
    var ids = curIds.slice();
    var extra = state.pin.slice();
    if (state.sel !== null && state.sel !== undefined) extra.push(state.sel);
    for (var k = 0; k < extra.length; k++) {
      if (byI[extra[k]] && ids.indexOf(extra[k]) < 0) ids.push(extra[k]);
    }
    var detail = curIds.length <= DETAIL_MAX;
    try {
      var built = XPMap.buildFeatures(ids, detail, palette());
      XPMap.setTracks(built);
    } catch (e) {}
    if (typeof XPMap.setPinned === 'function') {
      try { XPMap.setPinned(state.pin.slice()); } catch (e) {}
    }
    try { XPMap.setSelected(state.sel === undefined ? null : state.sel); } catch (e) {}
    try { XPMap.setRadius(state.loc || null); } catch (e) {}
    try { XPMap.setDensity(resolveDensity()); } catch (e) {}
  }

  /* ---- filtering + list ---- */

  function refresh() {
    if (!XPData.catalog()) return;
    var g = ++gen;
    curIds = XPData.filter({
      y0: state.y0, y1: state.y1, mc: state.mc,
      m0: state.m0, m1: state.m1, q: state.q
    });
    XPData.ensureTracks(neededDecades()).then(function () {
      if (g !== gen) return;
      if (state.loc) runRadius(); else { radiusRows = null; renderList(); }
      if (els.trackHint) {
        els.trackHint.textContent = XPData.tracksReady(neededDecades()) ? '' :
          'Some track data is unavailable right now.';
      }
      drawTracks();
      updateDensityHint();
      writeUrl();
      if (!firstRefreshDone) {
        firstRefreshDone = true;
        restoreSelection();
        maybeFlyToSel();
      } else if (state.sel !== null && state.sel !== undefined && byI[state.sel]) {
        /* a storm click racing this refresh loses its render to the
           generation guard in selectStorm; re-render here so the click
           never selects invisibly */
        renderCard(byI[state.sel]);
        markListSelection();
      }
    });
  }

  function runRadius() {
    if (!state.loc) return;
    radiusRows = XPData.radiusQuery(state.loc.lat, state.loc.lon, state.loc.km, curIds);
    renderList();
  }

  function renderList() {
    if (!els.list) return;
    els.list.textContent = '';
    var isRad = !!(state.loc && radiusRows);
    var n, rows = [];
    if (isRad) {
      n = radiusRows.length;
      els.count.textContent = n + (n === 1 ? ' storm' : ' storms') + ' within ' +
        Math.round(state.loc.km) + ' km' + (radiusLabel ? ' of ' + radiusLabel : '');
      rows = radiusRows;
    } else {
      n = curIds.length;
      els.count.textContent = n + (n === 1 ? ' storm matches' : ' storms match');
      /* newest first for browsing */
      for (var k = curIds.length - 1; k >= 0; k--) rows.push({ i: curIds[k] });
    }
    var cap = Math.min(rows.length, LIST_CAP);
    for (var j = 0; j < cap; j++) {
      var r = rows[j];
      var s = byI[r.i];
      if (!s) continue;
      var btn = el('button', 'xp-row');
      btn.type = 'button';
      btn.setAttribute('data-i', s.i);
      var cat = isRad ? XPData.catFromWind(r.wind) : XPData.catFromWind(s.peak);
      btn.appendChild(chipEl(cat));
      btn.appendChild(el('span', 'xp-rowname', (s.name || 'UNNAMED') + ' · ' + s.season));
      var val;
      if (isRad) {
        val = Math.round(r.dist) + ' km · ' +
              (r.wind === null || r.wind === undefined ? 'n/a' : XPData.fmtWind(r.wind, state.u));
      } else {
        val = (s.ace === null || s.ace === undefined) ? '' : (+s.ace).toFixed(1) + ' ACE';
      }
      btn.appendChild(el('span', 'xp-rowval', val));
      els.list.appendChild(btn);
    }
    if (rows.length > LIST_CAP) {
      els.list.appendChild(el('div', 'xp-listnote', 'Showing first ' + LIST_CAP + ' of ' + rows.length + '.'));
    }
    if (!rows.length) {
      els.list.appendChild(el('div', 'xp-listnote', 'No storms match the current filters.'));
    }
    markListSelection();
  }

  function markListSelection() {
    if (!els.list) return;
    var kids = els.list.children;
    for (var k = 0; k < kids.length; k++) {
      var di = kids[k].getAttribute && kids[k].getAttribute('data-i');
      if (di === null || di === undefined) continue;
      if (state.sel !== null && state.sel !== undefined && +di === +state.sel) {
        kids[k].className = 'xp-row sel';
      } else if (kids[k].className.indexOf('xp-row') === 0) {
        kids[k].className = 'xp-row';
      }
    }
  }

  /* ---- selection + detail card ---- */

  function restoreSelection() {
    if (state.sel === null || state.sel === undefined) return;
    var s = byI[state.sel];
    if (!s) { state.sel = null; writeUrl(); return; }
    renderCard(s);
    markListSelection();
    if (mapOk) drawTracks();
  }

  function selectStorm(i) {
    if (i === null || i === undefined || isNaN(i)) return;
    var s = byI[i];
    if (!s) return;
    state.sel = i;
    var g = gen;
    XPData.ensureTracks([Math.floor(s.season / 10) * 10]).then(function () {
      if (g !== gen) return;
      renderCard(s);
      drawTracks();
      markListSelection();
      writeUrl();
    });
  }

  function closeCard() {
    state.sel = null;
    if (els.card) {
      els.card.hidden = true;
      els.card.textContent = '';
    }
    drawTracks();
    markListSelection();
    writeUrl();
  }

  function togglePin(i) {
    var idx = state.pin.indexOf(i);
    if (idx >= 0) state.pin.splice(idx, 1);
    else {
      state.pin.push(i);
      if (state.pin.length > 2) state.pin.shift();
    }
    drawTracks();
    writeUrl();
  }

  function stat(grid, label, value, wide) {
    var d = el('div', 'xp-stat' + (wide ? ' wide' : ''));
    d.appendChild(el('span', 'k', label));
    d.appendChild(el('span', 'v', value));
    grid.appendChild(d);
  }

  function buildTimeline(pts) {
    if (!pts || pts.length < 2) return null;
    var spans = [], cur = null, total = 0, k;
    for (k = 0; k < pts.length - 1; k++) {
      var dt = XPData.tsParse(pts[k + 1][0]).getTime() - XPData.tsParse(pts[k][0]).getTime();
      if (!(dt > 0)) continue;
      var c = XPData.catFromWind(pts[k][3]);
      if (cur && cur.cat === c) cur.ms += dt;
      else { cur = { cat: c, ms: dt }; spans.push(cur); }
      total += dt;
    }
    if (!total || !spans.length) return null;
    var pal = palette();
    var wrap = el('div', 'xp-tl');
    for (k = 0; k < spans.length; k++) {
      var sp = el('span');
      sp.style.width = (100 * spans[k].ms / total) + '%';
      sp.style.background = pal[spans[k].cat] || '#666';
      sp.title = spans[k].cat + ' ' + Math.round(spans[k].ms / 36e5) + ' h';
      wrap.appendChild(sp);
    }
    return wrap;
  }

  function fmtLat(la) {
    return Math.abs(la).toFixed(1) + (la < 0 ? 'S' : 'N');
  }

  function fmtLonDisp(lo) {
    var L = ((lo + 180) % 360 + 360) % 360 - 180;
    return Math.abs(L).toFixed(1) + (L < 0 ? 'W' : 'E');
  }

  function renderCard(s) {
    if (!els.card) return;
    var card = els.card;
    card.textContent = '';

    var head = el('div', 'xp-cardhead');
    head.appendChild(chipEl(XPData.catFromWind(s.peak)));
    head.appendChild(el('h2', null, (s.name || 'UNNAMED') + ' · ' + s.season));
    card.appendChild(head);

    var grid = el('div', 'xp-stats');
    stat(grid, 'Dates', (s.t0 && s.t1) ? s.t0 + ' to ' + s.t1 : 'n/a', true);
    stat(grid, 'Peak wind', s.peak === null || s.peak === undefined ? 'n/a' : XPData.fmtWind(s.peak, state.u));
    stat(grid, 'Min pressure', s.pres === null || s.pres === undefined ? 'n/a' : XPData.fmtPres(s.pres, state.up));
    stat(grid, 'ACE', s.ace === null || s.ace === undefined ? 'n/a' : (+s.ace).toFixed(1));
    stat(grid, 'Duration', s.dur === null || s.dur === undefined ? 'n/a' : (+s.dur).toFixed(1) + ' d');
    card.appendChild(grid);

    var pts = XPData.trackOf(s.i);
    var tl = buildTimeline(pts);
    if (tl) card.appendChild(tl);

    card.appendChild(el('h3', null, 'Landfalls'));
    if (s.lf && s.lf.length) {
      var ul = el('ul', 'xp-lfs');
      for (var k = 0; k < s.lf.length; k++) {
        var lf = s.lf[k];
        var w = lf[3] === null || lf[3] === undefined ? 'wind n/a' : XPData.fmtWind(lf[3], state.u);
        ul.appendChild(el('li', null, XPData.tsFmt(lf[0]) + ' · ' + w));
      }
      card.appendChild(ul);
    } else {
      card.appendChild(el('p', 'xp-cardnote', 'No recorded landfalls.'));
    }

    if (s.rep && s.rep.length === 2 && s.rep[1]) {
      card.appendChild(el('h3', null, 'Official report'));
      var pa = el('p', 'xp-cardnote');
      var a = el('a', null, s.rep[0] || 'Report');
      a.href = s.rep[1];
      a.target = '_blank';
      a.rel = 'noopener';
      pa.appendChild(a);
      card.appendChild(pa);
    }

    if (s.rec && s.rec.length) {
      card.appendChild(el('h3', null, 'Record boards'));
      var rl = el('ul', 'xp-recs');
      for (var r = 0; r < s.rec.length; r++) {
        var row = s.rec[r];
        var li = el('li');
        var ra = el('a', null, row[2] + (row[3] === null || row[3] === undefined ? '' : ' - #' + row[3]));
        ra.href = '/records/' + row[1] + '/?basin=' + state.b;
        li.appendChild(ra);
        rl.appendChild(li);
      }
      card.appendChild(rl);
    }

    card.appendChild(el('h3', null, 'Fixes'));
    if (pts && pts.length) {
      var wrap = el('div', 'xp-fixwrap');
      var table = el('table');
      var thead = el('thead');
      var trh = el('tr');
      var cols = ['Time UTC', 'Lat', 'Lon', 'Wind', 'Pres', 'Cat'];
      for (var h = 0; h < cols.length; h++) trh.appendChild(el('th', null, cols[h]));
      thead.appendChild(trh);
      table.appendChild(thead);
      var tbody = el('tbody');
      for (var j = 0; j < pts.length; j++) {
        var p = pts[j];
        var tr = el('tr');
        var tdT = el('td', null, XPData.tsFmt(p[0]));
        if (p[5] & 2) tdT.appendChild(el('span', 'lf', 'LF'));
        tr.appendChild(tdT);
        tr.appendChild(el('td', null, fmtLat(p[1])));
        tr.appendChild(el('td', null, fmtLonDisp(p[2])));
        tr.appendChild(el('td', null, p[3] === null || p[3] === undefined ? '' : XPData.fmtWind(p[3], state.u)));
        tr.appendChild(el('td', null, p[4] === null || p[4] === undefined ? '' : XPData.fmtPres(p[4], state.up)));
        tr.appendChild(el('td', null, XPData.catFromWind(p[3])));
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      wrap.appendChild(table);
      card.appendChild(wrap);
    } else {
      card.appendChild(el('p', 'xp-cardnote', 'Track fixes unavailable.'));
    }

    var btns = el('div', 'xp-cardbtns');
    var zb = el('button', null, 'Zoom to storm');
    zb.type = 'button';
    zb.addEventListener('click', function () {
      if (mapOk) { try { XPMap.flyToStorm(s.i); } catch (e) {} }
    });
    btns.appendChild(zb);
    var pinned = state.pin.indexOf(s.i) >= 0;
    var pb = el('button', pinned ? 'on' : null, pinned ? 'Unpin' : 'Pin for compare');
    pb.type = 'button';
    pb.addEventListener('click', function () {
      togglePin(s.i);
      renderCard(s);
    });
    btns.appendChild(pb);
    var cb = el('button', null, 'Close');
    cb.type = 'button';
    cb.addEventListener('click', closeCard);
    btns.appendChild(cb);
    card.appendChild(btns);

    card.hidden = false;
  }

  /* ---- search ---- */

  function nameMatches(q) {
    var cat = XPData.catalog();
    if (!cat || !cat.storms) return false;
    var qq = q.toUpperCase();
    for (var k = 0; k < cat.storms.length; k++) {
      var s = cat.storms[k];
      if ((s.name || '').indexOf(qq) >= 0) return true;
      if ((s.atcf || '').toUpperCase() === qq) return true;
    }
    return false;
  }

  function clearRadius() {
    state.loc = null;
    radiusRows = null;
    radiusLabel = '';
    if (mapOk) { try { XPMap.setRadius(null); } catch (e) {} }
  }

  function applyLoc(loc) {
    state.q = '';
    var km = parseInt(els.radiusKm.value, 10) || 150;
    state.loc = { lat: loc.lat, lon: loc.lon, km: km };
    radiusLabel = loc.label || '';
    els.searchHint.textContent = 'Closest approaches within ' + km + ' km of ' + (radiusLabel || 'this point') + '.';
    refresh();
  }

  function runSearch() {
    var raw = els.search.value.replace(/^\s+|\s+$/g, '');
    els.searchHint.textContent = '';
    if (!raw) {
      if (state.q || state.loc) {
        state.q = '';
        clearRadius();
        refresh();
      }
      return;
    }
    if (/^\d{4}$/.test(raw) || nameMatches(raw)) {
      clearRadius();
      state.q = raw;
      refresh();
      return;
    }
    var direct = XPData.parseLoc(raw, null);
    if (direct) { applyLoc(direct); return; }
    var g = ++searchGen;
    els.searchHint.textContent = 'Looking up place...';
    XPData.loadGazetteer().then(function (places) {
      if (g !== searchGen) return;
      els.searchHint.textContent = '';
      var hit = places ? XPData.parseLoc(raw, places) : null;
      if (hit) { applyLoc(hit); return; }
      clearRadius();
      state.q = raw;
      els.searchHint.textContent = 'No place matched, filtering storm names instead.';
      refresh();
    });
  }

  /* ---- exports ---- */

  function exportIds() {
    if (state.loc && radiusRows) {
      var out = [];
      for (var k = 0; k < radiusRows.length; k++) out.push(radiusRows[k].i);
      return out;
    }
    return curIds;
  }

  function download(txt, fname, mime) {
    try {
      var blob = new Blob([txt], { type: mime });
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = fname;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(function () { try { URL.revokeObjectURL(url); } catch (e) {} }, 5000);
    } catch (e) {
      els.exportNote.textContent = 'Export failed in this browser.';
    }
  }

  function doExport(kind) {
    els.exportNote.textContent = '';
    var ids = exportIds();
    if (!ids.length) { els.exportNote.textContent = 'Nothing to export.'; return; }
    if (ids.length > EXPORT_CAP) {
      els.exportNote.textContent = 'Export capped at ' + EXPORT_CAP + ' storms, narrow the filters.';
      return;
    }
    var g = gen;
    XPData.ensureTracks(neededDecades()).then(function () {
      if (g !== gen) return;
      var txt, fname, mime;
      var span = state.y0 + '-' + state.y1;
      if (kind === 'csv') {
        txt = XPData.exportCSV(ids, XPData.catalog(), null, { u: state.u, up: state.up });
        fname = 'tracks_' + state.b + '_' + span + '.csv';
        mime = 'text/csv';
      } else {
        txt = XPData.exportGeoJSON(ids);
        fname = 'tracks_' + state.b + '_' + span + '.geojson';
        mime = 'application/geo+json';
      }
      if (!txt) {
        els.exportNote.textContent = 'Export capped at ' + EXPORT_CAP + ' storms, narrow the filters.';
        return;
      }
      download(txt, fname, mime);
    });
  }

  /* ---- basin ---- */

  function wireBasinChips() {
    if (!window.TATRecords || !TATRecords.onBasin) return;
    if (TATRecords.basin() !== state.b) {
      /* records.js keeps setBasin internal to the chip buttons, so sync the
         shared chip state by clicking the matching chip; fall back to the
         API if a later records.js exports it. */
      basinSyncing = true;
      try {
        if (typeof TATRecords.setBasin === 'function') {
          TATRecords.setBasin(state.b);
        } else {
          var chipsHost = $('basinChips');
          var btn = chipsHost && chipsHost.querySelector('button[data-basin="' + state.b + '"]');
          if (btn) btn.click();
        }
      } catch (e) {}
      basinSyncing = false;
    }
    /* onBasin fires the callback immediately on registration; if the chip
       sync above failed, that first fire must not stomp the URL basin. */
    var ignoreFirst = TATRecords.basin() !== state.b;
    TATRecords.onBasin(function (b) {
      if (basinSyncing) return;
      if (ignoreFirst) {
        ignoreFirst = false;
        if (b !== state.b) return;
      }
      if (!b || b === state.b) return;
      switchBasin(b);
    });
  }

  function switchBasin(b) {
    state.b = b;
    state.sel = null;
    state.pin = [];
    radiusRows = null;
    if (els.card) { els.card.hidden = true; els.card.textContent = ''; }
    /* without an explicit user view, the old basin's viewport is meaningless
       here; jump to the new basin's default so WP never opens on the Atlantic */
    if (mapOk && !userView && typeof XPMap.basinView === 'function') {
      var bv = XPMap.basinView(b);
      if (bv) {
        state.v = null;
        try { XPMap.setView(bv); } catch (e) {}
      }
    }
    var g = ++gen;
    XPData.setBasin(b).then(function (cat) {
      if (g !== gen) return;
      if (!cat) { catalogError(); writeUrl(); return; }
      afterCatalog(cat);
      refresh();
    });
  }

  /* ---- controls ---- */

  function paintSeg(container, attr, val) {
    if (!container) return;
    var btns = container.querySelectorAll('button');
    for (var k = 0; k < btns.length; k++) {
      btns[k].className = btns[k].getAttribute(attr) === val ? 'on' : '';
    }
  }

  function wireSeg(container, attr, cb) {
    if (!container) return;
    var btns = container.querySelectorAll('button');
    for (var k = 0; k < btns.length; k++) {
      (function (btn) {
        btn.addEventListener('click', function () {
          var v = btn.getAttribute(attr);
          paintSeg(container, attr, v);
          cb(v);
        });
      })(btns[k]);
    }
  }

  function fillMonthSelect(sel, withAll, allLabel) {
    if (!sel) return;
    sel.textContent = '';
    if (withAll) {
      var o0 = el('option', null, allLabel || 'All');
      o0.value = '';
      sel.appendChild(o0);
    }
    for (var m = 1; m <= 12; m++) {
      var o = el('option', null, MONTHS[m - 1]);
      o.value = String(m);
      sel.appendChild(o);
    }
  }

  function yearsChanged() {
    yearsTouched = true;
    var a = parseInt(els.y0.value, 10);
    var b = parseInt(els.y1.value, 10);
    if (isFinite(a)) state.y0 = a;
    if (isFinite(b)) state.y1 = b;
    if (state.y0 !== null && state.y1 !== null && state.y0 > state.y1) {
      var t = state.y0; state.y0 = state.y1; state.y1 = t;
      els.y0.value = state.y0;
      els.y1.value = state.y1;
    }
    refresh();
  }

  function setYears(a, b) {
    yearsTouched = true;
    state.y0 = a;
    state.y1 = b;
    els.y0.value = a;
    els.y1.value = b;
    refresh();
  }

  function densChanged() {
    var v = els.density.value;
    if (v === 'month') {
      els.densMonth.hidden = false;
      var mm = parseInt(els.densMonth.value, 10) || 9;
      els.densMonth.value = String(mm);
      state.dens = 'track_m' + p2(mm);
    } else {
      els.densMonth.hidden = true;
      state.dens = v === 'track_all' ? 'track_all' : (v === 'genesis' ? 'genesis' : '');
    }
    if (mapOk) { try { XPMap.setDensity(resolveDensity()); } catch (e) {} }
    updateDensityHint();
    writeUrl();
  }

  function setPalClass() {
    var cls = document.body.className.replace(/\s*\bpal-cb\b/g, '');
    document.body.className = state.pal === 'cb' ? (cls ? cls + ' pal-cb' : 'pal-cb') : cls;
  }

  function applyStateToControls() {
    els.search.value = state.q || '';
    if (els.radiusKm && state.loc) {
      var kmStr = String(Math.round(state.loc.km));
      var kmFound = false;
      for (var k = 0; k < els.radiusKm.options.length; k++) {
        if (els.radiusKm.options[k].value === kmStr) { kmFound = true; break; }
      }
      if (!kmFound) {
        /* URL radius outside the preset list: add a temporary option */
        var kmOpt = el('option', null, kmStr + ' km');
        kmOpt.value = kmStr;
        els.radiusKm.appendChild(kmOpt);
      }
      els.radiusKm.value = kmStr;
    }
    els.minCat.value = state.mc;
    els.m0.value = state.m0 === 1 ? '' : String(state.m0);
    els.m1.value = state.m1 === 12 ? '' : String(state.m1);
    /* month window that starts or ends mid-range still shows both */
    if (!(state.m0 === 1 && state.m1 === 12)) {
      els.m0.value = String(state.m0);
      els.m1.value = String(state.m1);
    }
    paintSeg(els.pal, 'data-pal', state.pal);
    setPalClass();
    paintSeg(els.unitW, 'data-u', state.u);
    paintSeg(els.unitP, 'data-up', state.up);
    var dm = /^track_m(\d\d)$/.exec(state.dens);
    if (dm) {
      els.density.value = 'month';
      els.densMonth.hidden = false;
      els.densMonth.value = String(parseInt(dm[1], 10));
    } else if (state.dens) {
      els.density.value = state.dens;
    }
    if (state.loc) {
      var ll = XPData.parseLoc(state.loc.lat + ', ' + state.loc.lon, null);
      radiusLabel = ll ? ll.label : '';
      els.searchHint.textContent = 'Closest approaches within ' + Math.round(state.loc.km) +
        ' km of ' + (radiusLabel || 'this point') + '.';
    }
  }

  function wireControls() {
    /* one delegated click handler survives renderList() row rebuilds, so a
       click landing across a refresh (e.g. year-input blur) still selects.
       When the rebuild happens mid-click the event targets the container
       itself, so fall back to the row under the pointer. */
    els.list.addEventListener('click', function (ev) {
      var t = ev.target;
      var row = (t && t.closest) ? t.closest('[data-i]') : null;
      if (!row && typeof ev.clientX === 'number' && document.elementFromPoint) {
        var at = document.elementFromPoint(ev.clientX, ev.clientY);
        row = (at && at.closest) ? at.closest('[data-i]') : null;
      }
      if (!row || !els.list.contains(row)) return;
      var i = parseInt(row.getAttribute('data-i'), 10);
      if (isFinite(i)) selectStorm(i);
    });

    els.search.addEventListener('input', function () {
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = setTimeout(runSearch, 300);
    });
    els.search.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.keyCode === 13) {
        if (searchTimer) clearTimeout(searchTimer);
        runSearch();
      }
    });
    els.radiusKm.addEventListener('change', function () {
      if (state.loc) {
        state.loc.km = parseInt(els.radiusKm.value, 10) || 150;
        els.searchHint.textContent = 'Closest approaches within ' + state.loc.km +
          ' km of ' + (radiusLabel || 'this point') + '.';
        refresh();
      }
    });
    els.radiusClear.addEventListener('click', function () {
      clearRadius();
      els.search.value = '';
      state.q = '';
      els.searchHint.textContent = '';
      refresh();
    });

    els.y0.addEventListener('change', yearsChanged);
    els.y1.addEventListener('change', yearsChanged);
    var eras = els.eras.querySelectorAll('button');
    for (var k = 0; k < eras.length; k++) {
      (function (btn) {
        btn.addEventListener('click', function () {
          var kind = btn.getAttribute('data-era');
          var mb = manifestBasin() || {};
          var cur = currentSeason();
          if (kind === 'all') setYears(mb.records_since || defaultY0(), cur);
          else if (kind === 'sat') setYears(mb.satellite_era || defaultY0(), cur);
          else setYears(cur - 29, cur);
        });
      })(eras[k]);
    }

    els.minCat.addEventListener('change', function () {
      state.mc = MC_KEYS[els.minCat.value] ? els.minCat.value : 'ts';
      refresh();
    });
    els.m0.addEventListener('change', function () {
      state.m0 = parseInt(els.m0.value, 10) || 1;
      refresh();
    });
    els.m1.addEventListener('change', function () {
      state.m1 = parseInt(els.m1.value, 10) || 12;
      refresh();
    });

    els.density.addEventListener('change', densChanged);
    els.densMonth.addEventListener('change', densChanged);

    wireSeg(els.pal, 'data-pal', function (v) {
      state.pal = v === 'cb' ? 'cb' : 'std';
      setPalClass();
      renderList();
      if (state.sel !== null && state.sel !== undefined && byI[state.sel]) renderCard(byI[state.sel]);
      drawTracks();
      writeUrl();
    });
    wireSeg(els.unitW, 'data-u', function (v) {
      state.u = U_KEYS[v] ? v : 'kt';
      renderList();
      if (state.sel !== null && state.sel !== undefined && byI[state.sel]) renderCard(byI[state.sel]);
      writeUrl();
    });
    wireSeg(els.unitP, 'data-up', function (v) {
      state.up = UP_KEYS[v] ? v : 'mb';
      renderList();
      if (state.sel !== null && state.sel !== undefined && byI[state.sel]) renderCard(byI[state.sel]);
      writeUrl();
    });

    els.csv.addEventListener('click', function () { doExport('csv'); });
    els.geo.addEventListener('click', function () { doExport('geojson'); });
  }

  /* ---- boot ---- */

  function grabEls() {
    els.shell = $('xpShell');
    els.search = $('xpSearch');
    els.searchHint = $('xpSearchHint');
    els.radiusKm = $('xpRadiusKm');
    els.radiusClear = $('xpRadiusClear');
    els.y0 = $('xpY0');
    els.y1 = $('xpY1');
    els.eras = els.shell ? els.shell.querySelector('.xp-eras') : null;
    els.minCat = $('xpMinCat');
    els.m0 = $('xpM0');
    els.m1 = $('xpM1');
    els.density = $('xpDensity');
    els.densMonth = $('xpDensMonth');
    els.densHint = $('xpDensHint');
    els.pal = $('xpPal');
    els.unitW = $('xpUnitW');
    els.unitP = $('xpUnitP');
    els.count = $('xpCount');
    els.list = $('xpList');
    els.trackHint = $('xpTrackHint');
    els.csv = $('xpCsv');
    els.geo = $('xpGeo');
    els.exportNote = $('xpExportNote');
    els.caveat = $('xpCaveat');
    els.mapEl = $('xpMap');
    els.mapNote = $('xpMapNote');
    els.card = $('xpCard');
  }

  function boot() {
    grabEls();
    if (!els.shell || !window.XPData) return;
    fillMonthSelect(els.m0, true, 'All');
    fillMonthSelect(els.m1, true, 'All');
    fillMonthSelect(els.densMonth, false);
    els.densMonth.value = '9';
    readUrl();
    applyStateToControls();
    wireControls();
    XPData.init().then(function (m) {
      if (!m) { shellError(); return; }
      if (!m.basins || !m.basins[state.b]) state.b = 'al';
      wireBasinChips();
      XPData.setBasin(state.b).then(function (cat) {
        if (!cat) { catalogError(); createMap(); return; }
        afterCatalog(cat);
        createMap();
        refresh();
      });
    });
  }

  window.XPUI = {
    state: function () { return state; },
    refresh: refresh,
    selectStorm: selectStorm
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
