/* TC Records (shadow) shared runtime.
   Exposes window.TATRecords; every /records/ page uses it.
   Records JSON lives on the CDN; override with ?data= for local testing.
   Defensive throughout: fetch failures resolve to null, page callbacks
   run inside try/catch, and all data lands in the DOM via textContent. */
(function () {
  'use strict';

  var BASINS = { al: 'Atlantic', ep: 'East Pacific', wp: 'West Pacific' };
  var BASIN_ORDER = ['al', 'ep', 'wp'];

  // Canonical SSHWS palette (tat_palette.js, generated from
  // palette/tat_palettes/categories.py); every /records/ page loads it ahead
  // of this file.
  function TATP() {
    var p = window.TATPalette;
    if (!p) throw new Error('records.js: load /tat_palette.js first');
    return p;
  }

  function param(name) {
    try {
      return new URLSearchParams(window.location.search).get(name) || null;
    } catch (e) { return null; }
  }

  var DATA_BASE = (function () {
    var q = param('data');
    return q ? q.replace(/\/+$/, '') : 'https://cdn.triple-a-tropics.com/records/v1';
  })();

  /* ---- basin state ---- */
  var cur = (function () {
    var q = param('basin');
    if (q && BASINS[q]) return q;
    try {
      var s = localStorage.getItem('tat_records_basin');
      if (s && BASINS[s]) return s;
    } catch (e) {}
    return 'al';
  })();

  var listeners = [];

  function basin() { return cur; }

  function fire(cb) {
    /* never-throws contract, but keep the trace debuggable */
    try { cb(cur); } catch (e) { try { console.error(e); } catch (e2) {} }
  }

  function onBasin(cb) {
    listeners.push(cb);
    fire(cb);
  }

  function setBasin(b) {
    if (!BASINS[b] || b === cur) return;
    cur = b;
    try { localStorage.setItem('tat_records_basin', b); } catch (e) {}
    try {
      var u = new URL(window.location.href);
      u.searchParams.set('basin', b);
      history.replaceState(null, '', u.pathname + u.search + u.hash);
    } catch (e) {}
    paintChips();
    for (var i = 0; i < listeners.length; i++) fire(listeners[i]);
  }

  /* ---- data access ---- */
  function fetchJSON(name) {
    var url = DATA_BASE + '/' + name + '?t=' + Date.now();
    try {
      return fetch(url, { cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .catch(function () { return null; });
    } catch (e) {
      return Promise.resolve(null);
    }
  }

  function loadFor(suffix, cb) {
    onBasin(function (b) {
      fetchJSON(b + suffix).then(function (data) {
        if (b !== cur) return; /* superseded by a later basin switch */
        try { cb(data); } catch (e) { try { console.error(e); } catch (e2) {} }
      });
    });
  }

  function loadRecords(cb) { loadFor('_records.json', cb); }
  function loadSeasons(cb) { loadFor('_seasons.json', cb); }

  /* ---- basin chips ---- */
  var chipWrap = null;

  function paintChips() {
    if (!chipWrap) return;
    var btns = chipWrap.querySelectorAll('button');
    for (var i = 0; i < btns.length; i++) {
      btns[i].className = btns[i].getAttribute('data-basin') === cur ? 'on' : '';
    }
  }

  function buildChips() {
    var host = document.getElementById('basinChips');
    if (!host) return;
    chipWrap = document.createElement('div');
    chipWrap.className = 'rec-chips';
    for (var i = 0; i < BASIN_ORDER.length; i++) {
      (function (k) {
        var b = document.createElement('button');
        b.type = 'button';
        b.textContent = BASINS[k];
        b.setAttribute('data-basin', k);
        b.addEventListener('click', function () { setBasin(k); });
        chipWrap.appendChild(b);
      })(BASIN_ORDER[i]);
    }
    host.textContent = '';
    host.appendChild(chipWrap);
    paintChips();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', buildChips);
  } else {
    buildChips();
  }

  /* ---- provenance stamp ---- */
  function fmtGenerated(iso) {
    if (!iso) return '';
    var m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(iso);
    return m ? m[1] + ' ' + m[2] + ' UTC' : iso;
  }

  function stamp(prov) {
    var el = document.getElementById('recStamp');
    if (!el || !prov) return;
    var srcs = prov.ibtracs || 'IBTrACS v04r01';
    if (prov.hurdat2) srcs += ' + HURDAT2';
    el.textContent = '@WeathermanAAA_ · Triple-A-Tropics · computed from ' +
      srcs + ' · updated ' + fmtGenerated(prov.generated);
  }

  /* ---- error panel ---- */
  function error(el, msg) {
    if (!el) return;
    /* a failed load also invalidates whatever provenance line is showing */
    var st = document.getElementById('recStamp');
    if (st) st.textContent = '';
    el.textContent = '';
    var d = document.createElement('div');
    d.className = 'rec-error';
    d.textContent = msg || 'Records data unavailable. It may still be generating.';
    el.appendChild(d);
  }

  /* ---- board renderer ---- */
  function cell(tr, cls, text) {
    var td = document.createElement('td');
    if (cls) td.className = cls;
    td.textContent = (text === null || text === undefined) ? '' : String(text);
    tr.appendChild(td);
  }

  function head(tr, text) {
    var th = document.createElement('th');
    th.textContent = text;
    tr.appendChild(th);
  }

  /* Appends one titled table card for `board` to `el`. */
  function renderBoard(el, board) {
    if (!el || !board) return;
    var rows = board.rows || [];
    var key = board.key || '';
    /* Special boards: Nth column instead of rank, month or decade labels. */
    var nth = key === 'timing_earliest_nth';
    var monthly = key === 'month_formations' || key === 'month_ace';
    var decade = key === 'decade_peak';

    function has(field) {
      for (var i = 0; i < rows.length; i++) {
        var v = rows[i][field];
        if (v !== null && v !== undefined && v !== '') return true;
      }
      return false;
    }
    var showName = !monthly && !decade && has('name');
    var showDate = has('date');
    var showExtra = has('extra');

    var card = document.createElement('div');
    card.className = 'rec-board';

    var h = document.createElement('h2');
    h.textContent = board.title || '';
    card.appendChild(h);

    if (board.definition) {
      var def = document.createElement('p');
      def.className = 'rec-def';
      def.textContent = board.definition;
      card.appendChild(def);
    }

    var wrap = document.createElement('div');
    wrap.className = 'rec-tablewrap';
    var table = document.createElement('table');
    var thead = document.createElement('thead');
    var trh = document.createElement('tr');
    head(trh, nth ? 'Nth' : (monthly ? 'Month' : (decade ? 'Decade' : '#')));
    head(trh, board.unit ? 'Value (' + board.unit + ')' : 'Value');
    head(trh, showName ? 'Storm' : 'Season');
    if (showDate) head(trh, 'Date');
    if (showExtra) head(trh, 'Detail');
    thead.appendChild(trh);
    table.appendChild(thead);

    var tbody = document.createElement('tbody');
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var tr = document.createElement('tr');
      cell(tr, 'rank', nth ? r.value
        : ((monthly || decade) ? r.name : r.rank));
      cell(tr, 'val', (r.disp === null || r.disp === undefined) ? r.value : r.disp);
      if (showName && r.name !== null && r.name !== undefined && r.name !== '') {
        cell(tr, 'who', r.name + ' · ' + r.season);
      } else {
        cell(tr, 'who', r.season);
      }
      if (showDate) cell(tr, 'date', r.date);
      if (showExtra) cell(tr, 'extra', r.extra);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    card.appendChild(wrap);

    if (board.note) {
      var note = document.createElement('p');
      note.className = 'rec-note';
      note.textContent = board.note;
      card.appendChild(note);
    }
    if (board.since !== null && board.since !== undefined && board.since !== '') {
      var since = document.createElement('p');
      since.className = 'rec-since';
      since.textContent = 'Records since ' + board.since + '.';
      card.appendChild(since);
    }

    el.appendChild(card);
  }

  window.TATRecords = {
    DATA_BASE: DATA_BASE,
    basin: basin,
    setBasin: setBasin,
    onBasin: onBasin,
    fetchJSON: fetchJSON,
    loadRecords: loadRecords,
    loadSeasons: loadSeasons,
    renderBoard: renderBoard,
    stamp: stamp,
    error: error,
    // Canonical SSHWS palette (tat_palette.js, generated from
    // palette/tat_palettes/categories.py). Getters, not a snapshot, so a
    // consumer always reads the live table; SSHS_DARK_INK is derived from the
    // palette's own ink rather than a separately-maintained list of "bright"
    // categories that had to be kept in step by hand.
    get SSHS_COLORS() { return TATP().cats; },
    get SSHS_DARK_INK() {
      var ink = TATP().ink;
      return TATP().order.filter(function (c) { return ink[c] !== '#ffffff'; });
    }
  };
})();
