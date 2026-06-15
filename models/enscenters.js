/* Ensemble Cyclone Centers viewer (/models/).
 *
 * The canvas is a SELF-CONTAINED, copyable, TAT-branded figure: header (title +
 * metadata) top-left, the region-cropped map (bold hollow-ring pressure ramp,
 * Pacific-centered, dateline-safe via the shared TATRegions layer), the
 * pressure-bin legend bottom-left, the per-member peak table as a right column,
 * and the @WeathermanAAA_ watermark bottom-right - ALL drawn on the canvas, so a
 * right-click "Copy image / Save image" yields the complete figure as a PNG
 * (the canvas is untainted - every draw is local). The only HTML over the canvas
 * is the hover tooltip (pointer-events:none, NOT part of the exported image).
 *
 * Animator: an ACCUMULATING TRAIL (no fading). At frame N, steps 0..N-1 are
 * HOLLOW rings (committed to an offscreen trail layer) and the CURRENT step N is
 * FILLED solid circles drawn on top, so the current centers stand out as the
 * cloud grows toward the full member spread. A toggle switches to "Current step
 * only".
 *
 * Isolated from the HAFS viewer (separate IIFE, enscenters-* ids).
 */
(function () {
  'use strict';

  var BASE = 'https://cdn.triple-a-tropics.com';
  var MANIFEST_URL = BASE + '/models/enscenters/manifest.json';
  var DATA_BASE = BASE + '/models/enscenters/';

  var SPEED_OPTIONS = [0.5, 1, 2, 4];
  var BASE_FPS = 4;
  var POLL_IDLE_MS = 300000;
  var GIF_MAX_W = 1000;          // downscale the figure for a shareable GIF
  var GIF_WORKER_URL = 'https://cdnjs.cloudflare.com/ajax/libs/gif.js/0.2.0/gif.worker.js';
  var GIF_LAST_DWELL = 6;        // hold the full-cloud frame N x longer before looping

  var DEFAULT_REGION = 'atlantic';
  var LS_REGION = 'ens.region';
  var LS_TRAIL = 'ens.trail';
  var MIN_FIG_W = 760;     // figure renders at least this wide (legible PNG; scales on mobile)
  var WATERMARK = '@WeathermanAAA_';
  var FONT = 'Metropolis, "Helvetica Neue", Arial, sans-serif';

  // Pressure-bin colors (Andrew's reference, FINAL), keyed by manifest bin key.
  var PRESSURE_BIN_COLORS = {
    gt1000: '#dfe8ff', p990_1000: '#1f9bff', p970_990: '#ffd21a',
    p950_970: '#ff1f47', lt950: '#ff3d9a'
  };
  var BIN_ORDER = ['gt1000', 'p990_1000', 'p970_990', 'p950_970', 'lt950'];
  var BASEMAP_STYLE = {
    ocean: '#07101c', land: '#2f3f59',
    coast: 'rgba(150,175,205,0.28)', coastLw: 0.6,
    grid: 'rgba(255,255,255,0.05)', gridLw: 0.5
  };
  // figure palette. accent = the shared bright blue (TATRegions.ACCENT, same as
  // the picker) so the whole ENS product reads as one blue identity; falls back
  // to the literal if the shared layer somehow isn't loaded yet.
  var ACCENT = (typeof window !== 'undefined' && window.TATRegions && window.TATRegions.ACCENT) || '#2b9cff';
  var C = { bg: '#0b1320', fg: '#e8ebef', muted: '#9199a4', accent: ACCENT,
            border: '#2a2e36', panel: '#12182280' };

  function el(id) { return document.getElementById(id); }
  function fmtInt(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ','); }
  function regionOr(key) { return (window.TATRegions && TATRegions.get(key)) ? key : DEFAULT_REGION; }

  function binKey(p) {
    if (p < 950) return 'lt950';
    if (p < 970) return 'p950_970';
    if (p < 990) return 'p970_990';
    if (p < 1000) return 'p990_1000';
    return 'gt1000';
  }

  // small rounded-rect path (used for the neutral CTL chip in the peak table)
  function roundRectPath(g, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    g.beginPath();
    g.moveTo(x + r, y);
    g.arcTo(x + w, y, x + w, y + h, r);
    g.arcTo(x + w, y + h, x, y + h, r);
    g.arcTo(x, y + h, x, y, r);
    g.arcTo(x, y, x + w, y, r);
    g.closePath();
  }

  var WK = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function validLabel(initMs, stepH) {
    var d = new Date(initMs + stepH * 3600000);
    return WK[d.getUTCDay()] + ' ' + MO[d.getUTCMonth()] + ' ' + d.getUTCDate() + ', ' +
      String(d.getUTCHours()).padStart(2, '0') + 'Z';
  }
  function shortInit(initMs) {
    var d = new Date(initMs);
    return MO[d.getUTCMonth()] + ' ' + d.getUTCDate() + ' ' + String(d.getUTCHours()).padStart(2, '0') + 'Z';
  }
  // "2026061306" -> "Jun 13 06Z" (Run-selector option label; same shape as shortInit)
  function cycleLabel(cyc) {
    cyc = String(cyc);
    if (cyc.length < 10) return cyc;
    var mo = parseInt(cyc.slice(4, 6), 10) - 1, day = parseInt(cyc.slice(6, 8), 10), hr = cyc.slice(8, 10);
    return (MO[mo] || '?') + ' ' + day + ' ' + hr + 'Z';
  }

  // ========================================================================
  function EnsCentersViewer(root) {
    this.root = root;
    // Self-scope the figure's DOM chrome (F-hour readout, trail-on indicator)
    // to the shared bright blue by overriding --accent on THIS viewer's root
    // only - the site-global amber --accent (satellite/HAFS) is untouched.
    if (root && root.style) root.style.setProperty('--accent', ACCENT);
    this.dom = {
      mapframe: el('enscenters-mapframe'),
      canvas: el('enscenters-canvas'),
      status: el('enscenters-status'),
      models: el('enscenters-models'),
      play: el('enscenters-play'),
      stepB: el('enscenters-step-back'),
      stepF: el('enscenters-step-fwd'),
      fhour: el('enscenters-fhour'),
      valid: el('enscenters-valid'),
      speed: el('enscenters-speed'),
      run: el('enscenters-run'),
      scrub: el('enscenters-scrub'),
      trail: el('enscenters-trail'),
      gif: el('enscenters-gif'),
      gifmodal: el('enscenters-gifmodal'),
      gifn: el('enscenters-gifn'),
      giffps: el('enscenters-giffps'),
      gifskip: el('enscenters-gifskip'),
      gifmake: el('enscenters-gifmake'),
      gifstatus: el('enscenters-gifstatus'),
      gifx: el('enscenters-gifx'),
      tooltip: el('enscenters-tooltip'),
      empty: el('enscenters-empty'),
      regionBtn: el('enscenters-region-btn'),
      regionLabel: el('enscenters-region-label'),
      caption: el('enscenters-caption')
    };
    this.ctx = this.dom.canvas.getContext('2d');
    this.staticLayer = document.createElement('canvas');
    this.trailLayer = document.createElement('canvas');

    this.manifest = null;
    this.model = null;
    this.data = null;
    this.steps = [];
    this.frames = [];          // per step: [[lat, lon, mslp, vmax], ...] (all)
    this.regionFrames = [];    // per step: region-filtered subset
    this.regionPrefix = [];    // prefix center counts by step
    this.peaks = [];           // per-member region peak rows (sorted)
    this.initMs = 0;
    this.idx = 0;
    this.visible = [];         // current step region-filtered (for hover)
    this.playing = false;
    this.speed = 1;
    this.followLatest = true;   // Run selector: true = track the newest cycle on poll
    this._runSig = null;        // cached signature of the Run <select> options
    this.raf = null;
    this.lastTick = 0;
    this.geo = { countries: null, coast: null };
    this.picker = null;
    this.trailUpTo = -1;

    var saved = null; try { saved = localStorage.getItem(LS_REGION); } catch (e) {}
    this.region = regionOr(saved || DEFAULT_REGION);
    this.extent = (window.TATRegions ? TATRegions.extentOf(TATRegions.get(this.region)) : [0, 360, -90, 90]);
    var tm = null; try { tm = localStorage.getItem(LS_TRAIL); } catch (e) {}
    this.trailMode = (tm === 'current') ? 'current' : 'trail';

    this._wire();
    this._boot();
  }

  EnsCentersViewer.prototype._status = function (msg) {
    var s = this.dom.status; if (!s) return;
    if (msg) { s.style.display = 'flex'; s.querySelector('span').textContent = msg; }
    else { s.style.display = 'none'; }
  };

  EnsCentersViewer.prototype._showEmpty = function (on) {
    if (this.dom.empty) this.dom.empty.style.display = on ? 'block' : 'none';
    var sels = ['#enscenters-mapframe', '.ens-controlbar', '.ens-scrub', '.ens-caption'];
    for (var i = 0; i < sels.length; i++) {
      var e2 = this.root.querySelector(sels[i]);
      if (e2) e2.style.display = on ? 'none' : '';
    }
  };

  // ---- boot ----
  EnsCentersViewer.prototype._boot = function () {
    var self = this;
    this._status('Loading…');
    Promise.all([this._loadBasemap(), this._fetchManifest()])
      .then(function (res) { self._initRegion(); self._onManifest(res[1]); })
      .catch(function (e) { console.warn('enscenters: boot failed', e); self._status(''); self._showEmpty(true); });
    // re-render once webfonts settle so the burned-in text is crisp
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(function () { if (self.data) { self._drawFigure(); self._show(self.idx); } });
    }
  };

  EnsCentersViewer.prototype._loadBasemap = function () {
    var self = this;
    // basemap resolution is owned by the shared layer (TATRegions.COAST_RES =
    // 10m), one source of truth for every non-storm-nest viewer.
    var p = (window.TATRegions && TATRegions.loadGeo) ? TATRegions.loadGeo()
      : Promise.all([
          fetch('/ne_10m_admin_0_countries.geojson').then(function (r) { return r.json(); }),
          fetch('/ne_10m_coastline.geojson').then(function (r) { return r.json(); })
        ]).then(function (g) { return { countries: g[0], coast: g[1] }; });
    return p.then(function (g) { self.geo = g; });
  };

  EnsCentersViewer.prototype._fetchManifest = function () {
    return fetch(MANIFEST_URL + '?t=' + Date.now(), { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('manifest HTTP ' + r.status); return r.json(); });
  };

  EnsCentersViewer.prototype._onManifest = function (m) {
    this.manifest = m;
    var models = (m && m.models) || [];
    if (!models.length) { this._status(''); this._showEmpty(true); return; }
    this._showEmpty(false);
    var defs = models.map(function (x) { return { slug: x.slug, label: x.label }; });
    var active = m.default_model && models.some(function (x) { return x.slug === m.default_model; })
      ? m.default_model : models[0].slug;
    this._buildToggle(this.dom.models, defs, active, this._selectModel.bind(this));
    this._selectModel(active);
    this._schedulePoll();
  };

  EnsCentersViewer.prototype._modelEntry = function (slug) {
    var models = (this.manifest && this.manifest.models) || [];
    for (var i = 0; i < models.length; i++) if (models[i].slug === slug) return models[i];
    return null;
  };

  EnsCentersViewer.prototype._selectModel = function (slug) {
    var entry = this._modelEntry(slug);
    if (!entry || !entry.latest) return;
    this.model = slug;
    this.followLatest = true;            // a new model starts on its latest run
    this._highlight(this.dom.models, slug);
    this._buildRunSelect(entry, entry.latest);
    this._loadCycle(slug, entry.latest);
  };

  // Populate the "Run" <select> from THIS model's manifest cycle list (the
  // rolling window already on R2, newest-first), newest labelled "(latest)".
  // Shared / model-agnostic: reads whatever model's entry it is handed. Rebuilds
  // only when the cycle list actually changes (so a poll doesn't disrupt an open
  // dropdown), then reflects ``selected``.
  EnsCentersViewer.prototype._buildRunSelect = function (entry, selected) {
    var sel = this.dom.run;
    if (!sel || !entry) return;
    var cycles = (entry.cycles && entry.cycles.length) ? entry.cycles.slice()
      : (entry.latest ? [entry.latest] : []);
    var sig = cycles.join(',') + '|' + (entry.latest || '');
    if (sig !== this._runSig) {
      sel.innerHTML = '';
      for (var i = 0; i < cycles.length; i++) {
        var o = document.createElement('option');
        o.value = cycles[i];
        o.textContent = cycleLabel(cycles[i]) + (cycles[i] === entry.latest ? ' (latest)' : '');
        sel.appendChild(o);
      }
      this._runSig = sig;
    }
    if (selected && cycles.indexOf(selected) !== -1) sel.value = selected;
    else if (cycles.length) sel.value = cycles[0];
  };

  EnsCentersViewer.prototype._loadCycle = function (slug, cycle) {
    var self = this;
    this._pause();
    this.loadedCycle = cycle;
    if (this.dom.run) this.dom.run.value = cycle;   // keep the Run selector in sync
    this._status('Loading ' + slug.toUpperCase() + ' ' + cycle + '…');
    // Cache-bust on the cycle's CONTENT version (not the stable cycle string), so
    // a backfill/overwrite of this cycle's JSON busts the browser + CDN cache; an
    // unchanged cycle keeps its token and stays cached. force-cache is safe now
    // that the URL is version-keyed. See TATRegions.cycleVersion.
    var ver = (window.TATRegions && TATRegions.cycleVersion)
      ? TATRegions.cycleVersion(this.manifest, slug, cycle) : cycle;
    fetch(DATA_BASE + slug + '/' + cycle + '.json?v=' + ver, { cache: 'force-cache' })
      .then(function (r) { if (!r.ok) throw new Error('cycle HTTP ' + r.status); return r.json(); })
      .then(function (d) { self._onData(d); })
      .catch(function (e) { console.warn('enscenters: cycle load failed', e); self._status('Could not load cycle.'); });
  };

  EnsCentersViewer.prototype._onData = function (d) {
    this.data = d;
    this._applyCaption(d);
    this.steps = d.run_steps || [];
    this.initMs = Date.parse(d.init_time);
    var byStep = {};
    for (var s = 0; s < this.steps.length; s++) byStep[this.steps[s]] = [];
    var members = d.members || [];
    for (var i = 0; i < members.length; i++) {
      var cs = members[i].centers || [];
      for (var k = 0; k < cs.length; k++) {
        var c = cs[k], arr = byStep[c[0]];
        if (arr) arr.push([c[1], c[2], c[3], c[4]]);
      }
    }
    this.frames = this.steps.map(function (st) { return byStep[st] || []; });
    this.idx = 0;
    this.dom.scrub.min = 0;
    this.dom.scrub.max = Math.max(0, this.steps.length - 1);
    this.dom.scrub.value = 0;
    this._recomputeRegion();
    this._layout();
    this._drawFigure();
    this._status('');
    this._show(0);
  };

  // Model-aware caption: a model whose per-cycle JSON carries its own `caption`
  // (e.g. GEFS - genesis tracks, vmax is the model's own wind, different source)
  // overrides the default ECMWF text; everything else falls back to the
  // data-default attribute so the field models keep the closed-low explainer.
  EnsCentersViewer.prototype._applyCaption = function (d) {
    var elc = this.dom.caption;
    if (!elc) return;
    var dflt = elc.getAttribute('data-default') || elc.textContent;
    elc.textContent = (d && d.caption) ? d.caption : dflt;
  };

  // ---- region (shared TATRegions layer) ----
  EnsCentersViewer.prototype._initRegion = function () {
    var self = this;
    var r = window.TATRegions ? TATRegions.get(this.region) : null;
    if (this.dom.regionLabel && r) this.dom.regionLabel.textContent = r.label;
    if (window.TATRegions) {
      this.picker = new TATRegions.RegionPicker({
        current: this.region,   // picker loads its own 110m thumbnail geo
        onPick: function (key) { self._selectRegion(key); }
      });
    }
  };

  EnsCentersViewer.prototype._selectRegion = function (key) {
    if (!window.TATRegions) return;
    var r = TATRegions.get(key); if (!r) return;
    this.region = key;
    this.extent = TATRegions.extentOf(r);
    try { localStorage.setItem(LS_REGION, key); } catch (e) {}
    if (this.dom.regionLabel) this.dom.regionLabel.textContent = r.label;
    if (this.picker) this.picker.setCurrent(key);
    if (this.data) { this._recomputeRegion(); this._layout(); this._drawFigure(); this._show(this.idx); }
  };

  // region-filter every step + per-member region peaks + prefix counts
  EnsCentersViewer.prototype._recomputeRegion = function () {
    var r = window.TATRegions ? TATRegions.get(this.region) : null;
    var self = this;
    this.regionFrames = this.frames.map(function (f) {
      if (!r) return f.slice();
      var out = [];
      for (var i = 0; i < f.length; i++) if (TATRegions.inRegion(f[i][1], f[i][0], r)) out.push(f[i]);
      return out;
    });
    this.regionPrefix = []; var run = 0;
    for (var s = 0; s < this.regionFrames.length; s++) { run += this.regionFrames[s].length; this.regionPrefix[s] = run; }
    // per-member region peaks
    var members = (this.data && this.data.members) || [];
    var rows = [];
    for (var m = 0; m < members.length; m++) {
      var cs = members[m].centers || [], best = null;
      for (var k = 0; k < cs.length; k++) {
        var c = cs[k];
        if (r && !TATRegions.inRegion(c[2], c[1], r)) continue;
        if (best === null || c[3] < best.mslp) best = { mslp: c[3], vmax: c[4] };
      }
      if (best) rows.push({ id: members[m].id, mslp: best.mslp, vmax: best.vmax });
    }
    rows.sort(function (a, b) { return a.mslp - b.mslp; });
    this.peaks = rows;
    this._resetTrail();    // region changed -> trail invalid (clear pixels + counter)
  };

  // ---- figure layout (CSS px; contexts are dpr-scaled so we draw in CSS px) ----
  EnsCentersViewer.prototype._layout = function () {
    var availW = (this.dom.mapframe && this.dom.mapframe.clientWidth) || 800;
    this._lastAvailW = availW;
    var figW = Math.max(availW, MIN_FIG_W);
    var displayW = availW;
    var pad = 14, gap = 14, headerH = 50;
    var tableW = (figW < 620) ? Math.round(figW * 0.3) : 212;
    var mapBoxW = figW - 2 * pad - tableW - gap;
    var e = this.extent, aspect = (e[1] - e[0]) / (e[3] - e[2]);
    var boxH = Math.max(360, Math.min(mapBoxW / aspect, 560));   // table always >= 360 tall
    // contain the map within [mapBoxW x boxH] preserving aspect
    var drawW = mapBoxW, drawH = mapBoxW / aspect;
    if (drawH > boxH) { drawH = boxH; drawW = boxH * aspect; }
    var figH = pad + headerH + boxH + pad;

    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.dpr = dpr; this.figW = figW; this.figH = figH;
    var cv = this.dom.canvas;
    cv.width = Math.round(figW * dpr); cv.height = Math.round(figH * dpr);
    cv.style.width = displayW + 'px';
    cv.style.height = (displayW * figH / figW) + 'px';
    this._scale(this.ctx);
    // map box (allocated) + drawn map rect (contained, centered)
    this.box = { x: pad, y: pad + headerH, w: mapBoxW, h: boxH };
    this.map = { x: pad + (mapBoxW - drawW) / 2, y: pad + headerH + (boxH - drawH) / 2, w: drawW, h: drawH };
    this.table = { x: pad + mapBoxW + gap, y: pad + headerH, w: tableW, h: boxH };
    this.headerXY = { x: pad, y: pad };
    this.ringR = 2.3; this.ringLW = 1.4; this.fillR = 2.5;
    // size offscreen layers
    this.staticLayer.width = cv.width; this.staticLayer.height = cv.height;
    this.trailLayer.width = Math.round(this.map.w * dpr); this.trailLayer.height = Math.round(this.map.h * dpr);
    this._resetTrail();    // reassigning width already wiped the bitmap; reset counter via the one helper
  };

  EnsCentersViewer.prototype._scale = function (ctx) {
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
  };

  // ---- static figure parts: bg + header-less basemap box + table + border ----
  EnsCentersViewer.prototype._drawFigure = function () {
    if (!this.map) return;
    var g = this.staticLayer.getContext('2d');
    this._scale(g);
    g.clearRect(0, 0, this.figW, this.figH);
    g.fillStyle = C.bg; g.fillRect(0, 0, this.figW, this.figH);

    // map box bg (ocean) so letterbox bands are seamless, then basemap
    g.save();
    g.fillStyle = BASEMAP_STYLE.ocean;
    g.fillRect(this.box.x, this.box.y, this.box.w, this.box.h);
    g.beginPath(); g.rect(this.map.x, this.map.y, this.map.w, this.map.h); g.clip();
    g.translate(this.map.x, this.map.y);
    TATRegions.drawBasemap(g, this.extent, this.geo, this.map.w, this.map.h, BASEMAP_STYLE);
    g.restore();
    // map box border
    g.strokeStyle = C.border; g.lineWidth = 1;
    g.strokeRect(this.box.x + 0.5, this.box.y + 0.5, this.box.w - 1, this.box.h - 1);

    this._drawTable(g);
  };

  EnsCentersViewer.prototype._drawTable = function (g) {
    var t = this.table, rows = this.peaks, r = TATRegions.get(this.region);
    g.save();
    g.fillStyle = '#12182a'; g.strokeStyle = C.border; g.lineWidth = 1;
    g.fillRect(t.x, t.y, t.w, t.h);
    g.strokeRect(t.x + 0.5, t.y + 0.5, t.w - 1, t.h - 1);
    // title (accent bar + label)
    g.fillStyle = C.accent; g.fillRect(t.x, t.y, 3, 18);
    g.fillStyle = C.fg; g.font = '700 12px ' + FONT; g.textBaseline = 'top';
    g.fillText('Peak  ·  ' + (r ? r.label : ''), t.x + 9, t.y + 8);
    // 2-column body
    var n = rows.length, perCol = Math.ceil(n / 2);
    var colW = (t.w - 12) / 2;
    var headerY = t.y + 32, bodyTop = t.y + 42, bodyH = t.h - 50;
    var rowH = Math.max(11, Math.min(bodyH / perCol, 18));
    var fs = Math.max(8.5, Math.min(rowH * 0.66, 11));
    // ONE compact header per column, label x-positions matched to the data
    // columns below (member left, Pmin/V right-aligned) - no overlap.
    g.font = '700 8px ' + FONT; g.fillStyle = C.muted; g.textBaseline = 'alphabetic';
    for (var hc = 0; hc < 2; hc++) {
      var hx = t.x + 6 + hc * colW;
      g.textAlign = 'left'; g.fillText('MEMBER', hx + 6, headerY);
      g.textAlign = 'right'; g.fillText('Pmin', hx + colW - 26, headerY);
      g.fillText('V', hx + colW - 4, headerY);
    }
    g.textAlign = 'left'; g.textBaseline = 'top';
    g.strokeStyle = C.border; g.lineWidth = 1;
    g.beginPath(); g.moveTo(t.x + 6, headerY + 4); g.lineTo(t.x + t.w - 6, headerY + 4); g.stroke();
    if (!n) { g.fillStyle = C.muted; g.font = '500 10px ' + FONT; g.fillText('No centers in region', t.x + 10, bodyTop + 4); }
    for (var i = 0; i < n; i++) {
      var col = (i < perCol) ? 0 : 1, rowi = (i < perCol) ? i : i - perCol;
      var cx = t.x + 6 + col * colW, cy = bodyTop + rowi * rowH, midY = cy + rowH / 2;
      var row = rows[i], ctl = (row.id === 'CTL');
      // swatch ring (pressure-bin color; identical for every member incl. CTL)
      g.strokeStyle = PRESSURE_BIN_COLORS[binKey(row.mslp)] || '#fff';
      g.lineWidth = 1.4; g.beginPath(); g.arc(cx + 5, midY, 3.4, 0, 6.2832); g.stroke();
      g.font = (ctl ? '700 ' : '600 ') + fs.toFixed(1) + 'px ' + FONT;
      g.textBaseline = 'middle'; g.textAlign = 'left';
      // member id. The control run gets a NEUTRAL white-outlined chip + bold
      // white text - NOT the accent and NOT any pressure-bin hue - so it reads
      // as "the control", distinct from the 970-990 yellow and 990-1000 blue bins.
      if (ctl) {
        var tw = g.measureText(row.id).width;
        var chH = Math.min(rowH - 2, fs + 4), chW = tw + 8, chX = cx + 11, chY = midY - chH / 2;
        roundRectPath(g, chX, chY, chW, chH, Math.min(3, chH / 2));
        g.fillStyle = 'rgba(255,255,255,0.10)'; g.fill();
        g.strokeStyle = 'rgba(255,255,255,0.55)'; g.lineWidth = 1; g.stroke();
        g.fillStyle = '#ffffff'; g.fillText(row.id, chX + 4, midY);
      } else {
        g.fillStyle = C.fg; g.fillText(row.id, cx + 13, midY);
      }
      g.textAlign = 'right';
      g.fillStyle = ctl ? '#ffffff' : C.fg;
      g.fillText(row.mslp.toFixed(0), cx + colW - 26, midY);
      g.fillStyle = ctl ? '#ffffff' : C.muted;
      g.fillText(row.vmax.toFixed(0), cx + colW - 4, midY);
      g.textAlign = 'left'; g.textBaseline = 'top';
    }
    g.restore();
  };

  // ---- per-frame dynamic overlays ----
  EnsCentersViewer.prototype._drawHeader = function (g, i) {
    var d = this.data, acc = this.regionPrefix.length ? this.regionPrefix[Math.min(i, this.regionPrefix.length - 1)] : 0;
    g.save(); g.textBaseline = 'alphabetic'; g.textAlign = 'left';
    g.fillStyle = C.fg; g.font = '800 17px ' + FONT;
    g.fillText((d.model_label || 'ECMWF ENS') + '  ·  Ensemble Cyclone Centers', this.headerXY.x, this.headerXY.y + 17);
    g.fillStyle = C.muted; g.font = '500 12px ' + FONT;
    // HARD RULE: the burned-in header carries the CURRENT forecast hour + its
    // valid time (not just init), so a copied still / every GIF frame is self-
    // documenting. Drawn per-frame here (not on the static layer) so each frame
    // shows its own F-hour + valid time. The HTML chrome (dom.fhour/dom.valid)
    // is kept for the live UI, but the canvas is the source of truth on share.
    var stepH = (this.steps && this.steps.length)
      ? this.steps[Math.min(i, this.steps.length - 1)] : 0;
    g.fillText('init ' + shortInit(this.initMs) +
      '  ·  F' + String(stepH).padStart(3, '0') +
      '  ·  valid ' + validLabel(this.initMs, stepH) +
      '  ·  ' + (d.n_members || 0) + ' members  ·  ' + fmtInt(acc) + ' centers',
      this.headerXY.x, this.headerXY.y + 35);
    g.restore();
  };

  EnsCentersViewer.prototype._drawLegend = function (g) {
    var bins = (this.data && this.data.pressure_bins) || [];
    var lines = bins.length + 2, lh = 14, padx = 9, pady = 7;
    var w = 132, h = pady * 2 + lines * lh;
    var x = this.map.x + 8, y = this.map.y + this.map.h - h - 8;
    g.save();
    g.fillStyle = 'rgba(7,16,28,0.78)'; g.strokeStyle = C.border; g.lineWidth = 1;
    g.fillRect(x, y, w, h); g.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
    g.textBaseline = 'middle'; g.textAlign = 'left';
    for (var i = 0; i < bins.length; i++) {
      var cy = y + pady + i * lh + lh / 2;
      g.strokeStyle = PRESSURE_BIN_COLORS[bins[i].key] || '#fff'; g.lineWidth = 1.7;
      g.beginPath(); g.arc(x + padx + 4, cy, 4, 0, 6.2832); g.stroke();
      g.fillStyle = C.fg; g.font = '600 10.5px ' + FONT;
      g.fillText(bins[i].label, x + padx + 14, cy);
    }
    // filled vs hollow note
    var ny = y + pady + bins.length * lh;
    g.fillStyle = C.muted; g.font = '500 9.5px ' + FONT;
    g.textBaseline = 'middle';
    g.fillStyle = '#fff'; g.beginPath(); g.arc(x + padx + 4, ny + lh / 2, 3.2, 0, 6.2832); g.fill();
    g.fillStyle = C.muted; g.fillText('Filled = current step', x + padx + 14, ny + lh / 2);
    g.strokeStyle = '#fff'; g.lineWidth = 1.5; g.beginPath(); g.arc(x + padx + 4, ny + lh + lh / 2, 3.4, 0, 6.2832); g.stroke();
    g.fillText('Hollow = trail', x + padx + 14, ny + lh + lh / 2);
    g.restore();
  };

  EnsCentersViewer.prototype._drawWatermark = function (g) {
    g.save();
    g.font = '700 12px ' + FONT; g.textAlign = 'right'; g.textBaseline = 'bottom';
    g.fillStyle = 'rgba(232,235,239,0.42)';
    g.fillText(WATERMARK, this.map.x + this.map.w - 9, this.map.y + this.map.h - 7);
    g.restore();
  };

  // draw one step's centers into a (translated) map-space context
  EnsCentersViewer.prototype._drawStep = function (g, s, filled) {
    var pts = this.regionFrames[s]; if (!pts || !pts.length) return;
    var ext = this.extent, mw = this.map.w, mh = this.map.h;
    var buckets = {}; var bo;
    for (bo = 0; bo < BIN_ORDER.length; bo++) buckets[BIN_ORDER[bo]] = [];
    for (var k = 0; k < pts.length; k++) buckets[binKey(pts[k][2])].push(pts[k]);
    g.globalAlpha = filled ? 1 : 0.92;
    var r = filled ? this.fillR : this.ringR;
    for (bo = 0; bo < BIN_ORDER.length; bo++) {
      var key = BIN_ORDER[bo], arr = buckets[key]; if (!arr.length) continue;
      g.beginPath();
      for (var j = 0; j < arr.length; j++) {
        var p = TATRegions.project(arr[j][1], arr[j][0], ext, mw, mh);
        g.moveTo(p[0] + r, p[1]); g.arc(p[0], p[1], r, 0, 6.2832);
      }
      if (filled) { g.fillStyle = PRESSURE_BIN_COLORS[key] || '#fff'; g.fill(); }
      else { g.strokeStyle = PRESSURE_BIN_COLORS[key] || '#fff'; g.lineWidth = this.ringLW; g.stroke(); }
    }
    g.globalAlpha = 1;
  };

  // ensure the trail layer holds hollow rings for steps 0..(i-1)
  // Invalidate the trail: clear the offscreen layer's pixels AND reset the
  // progress counter, together. The invariant "trailUpTo === -1 implies the
  // layer is empty" must hold unconditionally, so callers that mean to
  // invalidate the trail use THIS (never a bare `trailUpTo = -1`, which would
  // leave stale pixels for _ensureTrail to draw a new pass on top of).
  EnsCentersViewer.prototype._resetTrail = function () {
    var g = this.trailLayer.getContext('2d');
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, this.trailLayer.width, this.trailLayer.height);
    this.trailUpTo = -1;
  };

  EnsCentersViewer.prototype._ensureTrail = function (i) {
    var target = (this.trailMode === 'trail') ? (i - 1) : -1;
    var g = this.trailLayer.getContext('2d');
    if (target < 0) {
      // ALWAYS clear - never trust a possibly-dirty layer. Clearing an
      // already-empty layer is a cheap no-op.
      g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, this.trailLayer.width, this.trailLayer.height);
      this.trailUpTo = -1;
      return;
    }
    if (this.trailUpTo === target) return;
    // Scrubbed back -> rebuild, OR a from-scratch build (counter at -1): clear
    // before the draw loop so a new pass never lands on stale pixels. An
    // in-range forward step (0 <= trailUpTo < target) keeps building incrementally.
    if (this.trailUpTo > target || this.trailUpTo === -1) {
      g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, this.trailLayer.width, this.trailLayer.height);
      this.trailUpTo = -1;
    }
    this._scale(g);
    for (var s = this.trailUpTo + 1; s <= target; s++) this._drawStep(g, s, false);
    this.trailUpTo = target;
  };

  // ---- compose a frame ----
  EnsCentersViewer.prototype._show = function (i) {
    if (!this.regionFrames.length || !this.map) return;
    var n = this.regionFrames.length;
    this.idx = ((i % n) + n) % n;
    this.visible = this.regionFrames[this.idx];
    this._ensureTrail(this.idx);

    var ctx = this.ctx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.dom.canvas.width, this.dom.canvas.height);
    // static figure (bg + basemap + table + border)
    ctx.drawImage(this.staticLayer, 0, 0);
    this._scale(ctx);
    this._drawHeader(ctx, this.idx);
    // dots into the map rect
    ctx.save();
    ctx.beginPath(); ctx.rect(this.map.x, this.map.y, this.map.w, this.map.h); ctx.clip();
    if (this.trailMode === 'trail') ctx.drawImage(this.trailLayer, this.map.x, this.map.y, this.map.w, this.map.h);
    ctx.translate(this.map.x, this.map.y);
    this._drawStep(ctx, this.idx, true);    // current step filled
    ctx.restore();
    this._drawLegend(ctx);
    this._drawWatermark(ctx);

    var stepH = this.steps[this.idx];
    this.dom.fhour.textContent = 'F' + String(stepH).padStart(3, '0');
    this.dom.valid.textContent = validLabel(this.initMs, stepH) + '  ·  ' + fmtInt(this.visible.length) + ' this step';
    if (String(this.dom.scrub.value) !== String(this.idx)) this.dom.scrub.value = this.idx;
  };

  EnsCentersViewer.prototype._setTrailMode = function (mode) {
    this.trailMode = (mode === 'current') ? 'current' : 'trail';
    try { localStorage.setItem(LS_TRAIL, this.trailMode); } catch (e) {}
    this._syncTrailBtn();
    this._resetTrail();   // clear pixels too, not just the counter (the bug)
    if (this.regionFrames.length) this._show(this.idx);
  };

  // ---- segmented toggle (model selector) ----
  EnsCentersViewer.prototype._buildToggle = function (container, defs, active, onPick) {
    if (!container) return;
    container.innerHTML = '';
    for (var i = 0; i < defs.length; i++) (function (def) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'hafs-seg' + (def.slug === active ? ' active' : '');
      b.textContent = def.label; b.setAttribute('data-slug', def.slug);
      b.addEventListener('click', function () { onPick(def.slug); });
      container.appendChild(b);
    })(defs[i]);
    // Always show the model selector when there is at least one model (even a
    // single one), so the active model is always labelled; only hide it if the
    // manifest somehow carried zero models.
    container.parentNode.style.display = defs.length ? '' : 'none';
  };

  EnsCentersViewer.prototype._highlight = function (container, slug) {
    if (!container) return;
    var btns = container.querySelectorAll('.hafs-seg');
    for (var i = 0; i < btns.length; i++) btns[i].classList.toggle('active', btns[i].getAttribute('data-slug') === slug);
  };

  // ---- transport ----
  EnsCentersViewer.prototype._frameMs = function () { return 1000 / (BASE_FPS * (this.speed || 1)); };
  EnsCentersViewer.prototype._tick = function (ts) {
    if (!this.playing) return;
    if (ts - this.lastTick >= this._frameMs()) { this.lastTick = ts; this._show(this.idx + 1); }
    var self = this; this.raf = requestAnimationFrame(function (t) { self._tick(t); });
  };
  EnsCentersViewer.prototype._play = function () {
    if (this.regionFrames.length <= 1) return;
    this.playing = true; this.dom.play.textContent = '❚❚ Pause'; this.lastTick = 0;
    if (this.raf) cancelAnimationFrame(this.raf);
    var self = this; this.raf = requestAnimationFrame(function (t) { self._tick(t); });
  };
  EnsCentersViewer.prototype._pause = function () {
    this.playing = false; if (this.dom.play) this.dom.play.textContent = '► Play';
    if (this.raf) { cancelAnimationFrame(this.raf); this.raf = null; }
  };
  EnsCentersViewer.prototype._syncTrailBtn = function () {
    if (!this.dom.trail) return;
    this.dom.trail.textContent = (this.trailMode === 'trail') ? 'Trail: on' : 'Trail: off';
    this.dom.trail.classList.toggle('on', this.trailMode === 'trail');
  };

  EnsCentersViewer.prototype._togglePlay = function () { this.playing ? this._pause() : this._play(); };
  EnsCentersViewer.prototype._step = function (delta) { if (this.regionFrames.length) this._show(this.idx + delta); };

  EnsCentersViewer.prototype._wire = function () {
    var self = this;
    if (this.dom.regionBtn) this.dom.regionBtn.addEventListener('click', function () { if (self.picker) self.picker.open(); });
    if (this.dom.trail) this.dom.trail.addEventListener('click', function () {
      self._setTrailMode(self.trailMode === 'trail' ? 'current' : 'trail');
    });
    if (this.dom.gif) this.dom.gif.addEventListener('click', function () { self._openGif(); });
    if (this.dom.gifmake) this.dom.gifmake.addEventListener('click', function () { self._makeGif(); });
    if (this.dom.gifx) this.dom.gifx.addEventListener('click', function () { self._closeGif(); });
    if (this.dom.gifmodal) this.dom.gifmodal.addEventListener('click', function (e) {
      if (e.target === self.dom.gifmodal && !self.encoding) self._closeGif();
    });
    this.dom.play.addEventListener('click', function () { self._togglePlay(); });
    this.dom.stepB.addEventListener('click', function () { self._pause(); self._step(-1); });
    this.dom.stepF.addEventListener('click', function () { self._pause(); self._step(1); });

    var sp = this.dom.speed;
    for (var i = 0; i < SPEED_OPTIONS.length; i++) {
      var o = document.createElement('option');
      o.value = SPEED_OPTIONS[i]; o.textContent = SPEED_OPTIONS[i] + '×';
      if (SPEED_OPTIONS[i] === 1) o.selected = true; sp.appendChild(o);
    }
    sp.addEventListener('change', function () { self.speed = parseFloat(this.value); if (self.playing) self.lastTick = 0; });

    // Run (cycle) selector: load the chosen cycle's JSON and re-render the full
    // figure for it. Region / trail / speed persist (instance state, untouched by
    // _onData); the scrubber resets to the new run's steps.
    if (this.dom.run) {
      this.dom.run.addEventListener('change', function () {
        var entry = self._modelEntry(self.model), cyc = this.value;
        if (!entry || !cyc) return;
        self.followLatest = (cyc === entry.latest);   // picking latest re-enables auto-advance
        self._loadCycle(self.model, cyc);
      });
    }
    this.dom.scrub.addEventListener('input', function () { self._pause(); self._show(parseInt(this.value, 10) || 0); });

    this.root.addEventListener('keydown', function (e) {
      var tag = e.target && e.target.tagName;
      if (tag === 'SELECT' || tag === 'INPUT' || tag === 'BUTTON') return;
      if (e.key === 'ArrowLeft') { self._pause(); self._step(-1); e.preventDefault(); }
      else if (e.key === 'ArrowRight') { self._pause(); self._step(1); e.preventDefault(); }
      else if (e.key === ' ' || e.key === 'Spacebar') { self._togglePlay(); e.preventDefault(); }
    });

    this._syncTrailBtn();

    this.dom.canvas.addEventListener('mousemove', function (ev) { self._hover(ev); });
    this.dom.canvas.addEventListener('mouseleave', function () { if (self.dom.tooltip) self.dom.tooltip.style.display = 'none'; });

    if (window.ResizeObserver) {
      this._ro = new ResizeObserver(function () { self._resizeDebounced(); });
      if (this.dom.mapframe) this._ro.observe(this.dom.mapframe);
    } else { window.addEventListener('resize', function () { self._resizeDebounced(); }); }
  };

  EnsCentersViewer.prototype._resizeDebounced = function () {
    var self = this;
    clearTimeout(this._rt);
    this._rt = setTimeout(function () {
      if (!self.regionFrames.length) return;
      var w = (self.dom.mapframe && self.dom.mapframe.clientWidth) || 0;
      if (w === self._lastAvailW) return;
      self._layout(); self._drawFigure(); self._show(self.idx);
    }, 140);
  };

  // hover hit-tests the CURRENT step's (filled) centers, in map space
  EnsCentersViewer.prototype._hover = function (ev) {
    var tip = this.dom.tooltip;
    if (!tip || !this.visible || !this.visible.length || !this.map) return;
    var rect = this.dom.canvas.getBoundingClientRect();
    var sx = this.dom.canvas.width / rect.width / this.dpr;   // css px per client px
    var mx = (ev.clientX - rect.left) * sx - this.map.x;
    var my = (ev.clientY - rect.top) * sx - this.map.y;
    if (mx < 0 || my < 0 || mx > this.map.w || my > this.map.h) { tip.style.display = 'none'; return; }
    var centers = this.visible, best = null, bestD = 11 * 11;
    for (var k = 0; k < centers.length; k++) {
      var p = TATRegions.project(centers[k][1], centers[k][0], this.extent, this.map.w, this.map.h);
      var dx = p[0] - mx, dy = p[1] - my, d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = centers[k]; }
    }
    if (!best) { tip.style.display = 'none'; return; }
    tip.style.display = 'block';
    tip.style.left = (ev.clientX - rect.left + 12) + 'px';
    tip.style.top = (ev.clientY - rect.top + 12) + 'px';
    tip.innerHTML = best[2].toFixed(0) + ' hPa  ·  ' + best[3].toFixed(0) + ' kt<br>' +
      Math.abs(best[0]).toFixed(1) + (best[0] >= 0 ? 'N' : 'S') + '  ' +
      Math.abs(best[1]).toFixed(1) + (best[1] >= 0 ? 'E' : 'W');
  };

  // ---- GIF export (reuses the /satellite/ gif.js 0.2.0 pattern) ----
  EnsCentersViewer.prototype._ensureGifWorker = function (cb) {
    if (this._gifWorker) { cb(this._gifWorker); return; }
    var self = this;
    fetch(GIF_WORKER_URL).then(function (r) { return r.text(); }).then(function (src) {
      self._gifWorker = URL.createObjectURL(new Blob([src], { type: 'application/javascript' }));
      cb(self._gifWorker);
    }).catch(function () { cb(GIF_WORKER_URL); });   // CDN fallback
  };

  EnsCentersViewer.prototype._openGif = function () {
    if (!this.steps.length) return;
    var nIn = this.dom.gifn;
    nIn.max = this.steps.length;
    if (!parseInt(nIn.value, 10) || parseInt(nIn.value, 10) > this.steps.length) {
      nIn.value = Math.min(this.steps.length, 30);
    }
    this.dom.gifstatus.style.display = 'none';
    this.dom.gifmake.disabled = false;
    this.dom.gifmodal.classList.add('open');
  };

  EnsCentersViewer.prototype._closeGif = function () {
    this.dom.gifmodal.classList.remove('open');
    this.encoding = false;
  };

  // The forecast steps to capture: the full run thinned by "skip every", then
  // evenly sampled down to N, always ending on the last step (the full cloud).
  EnsCentersViewer.prototype._pickSteps = function (total, n, skip) {
    var base = [];
    for (var i = 0; i < total; i++) if (skip <= 0 || i % (skip + 1) === 0) base.push(i);
    if (base[base.length - 1] !== total - 1) base.push(total - 1);
    if (base.length <= n) return base;
    var out = [], seen = {};
    for (var k = 0; k < n; k++) {
      var st = base[Math.round(k * (base.length - 1) / (n - 1))];
      if (!seen[st]) { seen[st] = 1; out.push(st); }
    }
    return out;
  };

  EnsCentersViewer.prototype._makeGif = function () {
    if (typeof window.GIF === 'undefined') { alert('GIF library still loading, try again in a second.'); return; }
    var total = this.steps.length;
    if (total < 2) return;
    var n = Math.max(2, Math.min(total, parseInt(this.dom.gifn.value, 10) || total));
    var fps = Math.max(1, Math.min(30, parseInt(this.dom.giffps.value, 10) || 10));
    var skip = Math.max(0, parseInt(this.dom.gifskip.value, 10) || 0);
    var delay = Math.round(1000 / fps);
    var sel = this._pickSteps(total, n, skip);
    if (sel.length < 2) { alert('Not enough frames for a GIF; lower "Skip every".'); return; }
    var selSet = {}; for (var s = 0; s < sel.length; s++) selSet[sel[s]] = 1;
    var lastSel = sel[sel.length - 1];

    // GIF canvas (downscaled from the retina figure canvas)
    var cw = this.dom.canvas.width, ch = this.dom.canvas.height;
    var W = Math.min(this.figW, GIF_MAX_W), H = Math.round(W * this.figH / this.figW);
    var oc = document.createElement('canvas'); oc.width = W; oc.height = H;
    var octx = oc.getContext('2d');

    var status = this.dom.gifstatus, mk = this.dom.gifmake, self = this;
    status.style.display = ''; status.textContent = 'Encoding… 0%'; mk.disabled = true;
    this.encoding = true;
    this._pause();
    var settled = false, safety = null;
    function end() { if (settled) return; settled = true; if (safety) { clearTimeout(safety); safety = null; } }
    function fail(msg) {
      if (settled) return; end(); self.encoding = false; mk.disabled = false;
      status.style.display = ''; status.textContent = msg || 'GIF export failed - try again.';
    }

    this._ensureGifWorker(function (worker) {
      var gif;
      try {
        gif = new window.GIF({ workers: 2, quality: 10, width: W, height: H,
          workerScript: worker, background: '#0b1320' });
      } catch (e) { fail('GIF encoder unavailable.'); return; }
      gif.on('progress', function (p) { status.textContent = 'Encoding… ' + Math.round(p * 100) + '%'; });
      gif.on('error', function () { fail('GIF encoding failed - try again.'); });
      gif.on('finished', function (blob) {
        end();
        var u = URL.createObjectURL(blob), a = document.createElement('a');
        a.href = u;
        a.download = 'ecens_' + self.region + '_' + (self.data ? self.data.init_cycle : 'cycle') + '.gif';
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        requestAnimationFrame(function () { URL.revokeObjectURL(u); });
        status.textContent = 'Done!'; mk.disabled = false;
        setTimeout(function () { self._closeGif(); }, 700);
      });

      // Render the run in order so the trail builds incrementally (honoring the
      // current region crop + trail/current toggle), capturing the sampled
      // steps. The synchronous loop never repaints mid-way, so the visible
      // canvas does not flash.
      var savedIdx = self.idx, added = 0;
      self._resetTrail();   // build the GIF trail from a clean layer
      for (var i = 0; i < total; i++) {
        self._show(i);
        if (selSet[i]) {
          octx.clearRect(0, 0, W, H);
          try { octx.drawImage(self.dom.canvas, 0, 0, cw, ch, 0, 0, W, H); } catch (e) { continue; }
          gif.addFrame(octx, { copy: true, delay: (i === lastSel) ? delay * GIF_LAST_DWELL : delay });
          added++;
        }
      }
      self._show(savedIdx);   // restore the viewer
      if (added < 2) { fail('Could not render enough frames.'); return; }
      safety = setTimeout(function () { fail('GIF encoding timed out - try again.'); }, 90000);
      try { gif.render(); } catch (e) { fail('GIF encoder failed to start.'); }
    });
  };

  // ---- poll for newer cycle ----
  EnsCentersViewer.prototype._schedulePoll = function () {
    clearTimeout(this._pollTimer);
    var self = this;
    this._pollTimer = setTimeout(function () { self._poll(); }, POLL_IDLE_MS);
  };
  EnsCentersViewer.prototype._poll = function () {
    var self = this;
    if (this.encoding) { this._schedulePoll(); return; }   // don't reload mid-encode
    this._fetchManifest().then(function (m) {
      self.manifest = m;
      var entry = self._modelEntry(self.model);
      if (!entry) return;
      // A pinned older run that has rolled off the retention window is gone from
      // R2 - fall back to following the latest so we never show a 404'd cycle.
      if (!self.followLatest && entry.cycles && entry.cycles.indexOf(self.loadedCycle) === -1) {
        self.followLatest = true;
      }
      self._buildRunSelect(entry, self.followLatest ? entry.latest : self.loadedCycle);
      // Only auto-advance to a fresh cycle when the user is following latest;
      // a user who picked a specific run stays on it.
      if (self.followLatest && entry.latest && entry.latest !== self.loadedCycle) {
        self._loadCycle(self.model, entry.latest);
      }
    }).catch(function () {}).then(function () { self._schedulePoll(); });
  };

  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('DOMContentLoaded', function () {
      var r = el('enscenters-viewer'); if (r) new EnsCentersViewer(r);
    });
  }
  if (typeof window !== 'undefined') window.EnsCentersViewer = EnsCentersViewer;
})();
