/* microwave.js - CANVAS viewer for OBSERVED passive-microwave TC imagery (v3).
 *
 * v3 moves the viewer to ASCAT's bare-raster + viewer-owned-chrome canvas model
 * so a satellite backdrop can sit UNDER the data. It consumes the producer's
 * chrome-free georeferenced tiles (overpass.tiles[product]) + overpass.bounds_wgs84
 * (the chromed display PNG in overpass.products[] is no longer used by the viewer),
 * draws them over an optional Vis/SWIR satellite backdrop (~40% opacity), and the
 * VIEWER draws ALL chrome: ocean/land basemap, graticule, coastline, the per-product
 * colorbar/legend, header, watermark, footer. Adds a Global view (recent overpasses
 * worldwide). Keeps the v2 features: four-product toggle, per-storm nav, a
 * Smoothed/Raw toggle (canvas imageSmoothing), and a GIF loop export.
 *
 * Data: NASA GPM/PPS near-real-time (active storms) + NOAA/CIRA TC-PRIMED archive,
 * rendered by generate_tcprimed.py and synced to R2 under microwave/.
 *
 * Dependency-light: window.TATRegions (projection + basemap), lazy-loaded if absent.
 * Auto-mounts on DOMContentLoaded by id (#microwave-viewer); exposes
 * window.MicrowaveViewer.
 */
(function () {
  'use strict';

  var BASE_DEFAULT = 'https://cdn.triple-a-tropics.com/microwave';
  var SITE = 'https://triple-a-tropics.com';
  var FONT = 'Metropolis, "Helvetica Neue", Arial, sans-serif';
  var WATERMARK = '@WeathermanAAA_';
  var EXPORT_API = 'https://render.triple-a-tropics.com/export';
  var GIF_MAX_W = 900;
  var GLOBAL_WINDOW_H = 48;   // global view: overpasses within this many hours
  var GLOBAL_MAX = 80;        // cap tiles drawn in the global composite

  // The four canonical observed-MW products (keys match the producer + manifest).
  var PRODUCTS = [
    { key: 'color37', label: '37 Color' },
    { key: 'color91', label: '91 Color' },
    { key: '37H',     label: '37H' },
    { key: '91H',     label: '91H' }
  ];
  var DEFAULT_PRODUCT = '91H';

  var STYLE = {
    bg: '#07101c', ocean: '#0a1626', land: '#19314e',
    coast: 'rgba(201,219,242,0.95)', coastLw: 1.0,
    state: 'rgba(150,175,205,0.16)', stateLw: 0.5,
    grid: 'rgba(176,196,222,0.10)', gridLab: 'rgba(176,196,222,0.5)'
  };
  var C = { fg: '#e5edf6', muted: '#8ea2bd', border: '#2a3e5c' };
  var MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function el(id) { return document.getElementById(id); }
  function fmtZ(iso) {
    var d = new Date(iso); if (isNaN(d.getTime())) return iso || '';
    return MO[d.getUTCMonth()] + ' ' + d.getUTCDate() + ' ' +
      String(d.getUTCHours()).padStart(2, '0') + String(d.getUTCMinutes()).padStart(2, '0') + 'Z';
  }
  function fmtTime(iso) { return fmtZ(iso); }
  function ageStr(iso) {
    var d = new Date(iso); if (isNaN(d.getTime())) return '';
    var s = (Date.now() - d.getTime()) / 1000; if (s < 0) return 'just now';
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    if (h < 1) return m + ' min ago';
    if (h < 36) return h + ' h ago';
    var dd = Math.floor(h / 24); return dd + ' d ' + (h % 24) + ' h ago';
  }
  function roundRectPath(g, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2); g.beginPath(); g.moveTo(x + r, y);
    g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r);
    g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath();
  }
  function nearestFrame(frames, tMs) {
    var best = null, bd = Infinity;
    for (var i = 0; i < (frames || []).length; i++) {
      var d = Math.abs((Date.parse(frames[i].t) || 0) - tMs);
      if (d < bd) { bd = d; best = frames[i]; }
    }
    return { frame: best, dms: bd };
  }
  // Pick the backdrop frame to draw under the storm: the MOST RECENT frame that
  // carries a bare backdrop (bd_key + WGS84 bounds), preferring one already widened
  // to the viewer's plot aspect (~1.667) so it FILLS edge-to-edge. We deliberately
  // do NOT match the backdrop to the (possibly hours-old) overpass time — an older
  // frame can predate the producer's widen-to-aspect fix and be a 12x12 SQUARE,
  // which letterboxes into the wide plot leaving bare navy margins. The freshest
  // widened frame is both gap-free and the most current sky; the dimmed 40% context
  // layer tolerates the small storm drift vs the overpass.
  function backdropFrame(frames) {
    var withBd = [];
    for (var i = 0; i < (frames || []).length; i++) {
      var f = frames[i], src = f && (f.bd_key || f.backdrop_key);
      if (src && f.bounds && f.bounds.length === 4) withBd.push(f);
    }
    if (!withBd.length) return null;
    withBd.sort(function (a, b) { return (Date.parse(b.t) || 0) - (Date.parse(a.t) || 0); });
    for (var j = 0; j < withBd.length; j++) {
      var bb = withBd[j].bounds, asp = Math.abs(bb[2] - bb[0]) / Math.max(1e-6, Math.abs(bb[3] - bb[1]));
      if (asp >= 1.4) return withBd[j];   // a widened frame -> fills the plot
    }
    return withBd[0];                      // fallback: freshest available
  }
  function lonLab(lon) { var l = lon; while (l > 180) l -= 360; while (l < -180) l += 360; return Math.abs(Math.round(l)) + (l >= 0 ? 'E' : 'W'); }
  function latLab(lat) { return Math.abs(Math.round(lat)) + (lat >= 0 ? 'N' : 'S'); }

  // Old (v2) product keys are aliases of the current (v3) products, so overpasses
  // rendered by the previous producer (89pct / 37color) still draw under the new
  // product set instead of vanishing -- this is what made the Global view show
  // only a handful of swaths out of dozens of recent overpasses. 89 PCT and 91H
  // are both high-freq scattering; 37color and color37 are the same 37 GHz
  // composite. color91 / 37H have no v2 equivalent.
  var PRODUCT_ALIASES = {
    'color37': ['color37', '37color'],
    'color91': ['color91'],
    '37H': ['37H'],
    '91H': ['91H', '89pct']
  };
  // ---- producer-tile accessor: prefer the chrome-free geo tile (then the chromed
  // product PNG as a last resort); the viewer owns the chrome. Tries the exact key
  // first, then its v2 aliases, so older overpasses still render.
  // When `raw` is set, prefer the producer's RAW (native-footprint, nearest-
  // neighbour, blocky) geo tile -- then FALL BACK to the smoothed tile when an
  // overpass predates the raw-tile producer, so availability/counts are identical
  // whichever smoothing is selected (a pass never disappears in Raw mode).
  function tileRel(o, key, raw) {
    if (!o) return null;
    var keys = PRODUCT_ALIASES[key] || [key];
    var i;
    if (raw && o.tiles_raw) {
      for (i = 0; i < keys.length; i++) if (o.tiles_raw[keys[i]]) return { rel: o.tiles_raw[keys[i]], bare: true, raw: true };
    }
    for (i = 0; i < keys.length; i++) if (o.tiles && o.tiles[keys[i]]) return { rel: o.tiles[keys[i]], bare: true };
    for (i = 0; i < keys.length; i++) if (o.products && o.products[keys[i]]) return { rel: o.products[keys[i]], bare: false };
    return null;
  }
  function boundsOf(o) {
    var b = o && o.bounds_wgs84;
    return (b && b.length === 4) ? b : null;   // [W,S,E,N]
  }
  // Floater slug from an ATCF id: basin + 2-digit number, no year (WP072026 ->
  // wp07). Lets the backdrop fetch a storm's per-storm floater manifest DIRECTLY
  // even when that storm has been dropped from the top floaters/manifest.json
  // (retired / went extratropical) but its floater data still exists.
  function floaterSlug(atcf) {
    atcf = String(atcf || '');
    return atcf.length >= 4 ? (atcf.slice(0, 2) + atcf.slice(2, 4)).toLowerCase() : null;
  }

  function MicrowaveViewer(root, opts) {
    opts = opts || {};
    this.root = root;
    this.base = (opts.base || BASE_DEFAULT).replace(/\/+$/, '');
    this.product = DEFAULT_PRODUCT;
    this.raw = false;            // false = smoothed (default), true = raw native pixels
    this.mode = 'storm';         // 'storm' | 'global'
    this.backdrop = false;       // satellite backdrop on?
    this.bdOpacity = 0.4;        // dimmed so the MW data reads on top
    this.bdImg = null; this.bdFrame = null;
    this.geo = null;             // TATRegions basemap geojson
    this._tiles = {};            // url -> Image (CORS-clean)
    this._floaterCache = {};
    this.encoding = false;
    this.manifest = null;
    this.storms = [];
    this.curStorm = null;
    this.overpasses = [];
    this.curOverpass = null;
    this.globalOps = [];         // union of recent overpasses (global mode)
    this._fetchSeq = 0;
    this.dom = {};
    this._mount();
    this._loadBasemap();
    this._boot();
  }

  // ---- markup -------------------------------------------------------------
  MicrowaveViewer.prototype._mount = function () {
    var d = this.dom, self = this;
    d.stormSel    = el('mw-storm');
    d.overpassSel = el('mw-overpass');
    d.toggle      = el('mw-products');
    d.frame       = el('mw-imageframe');
    d.canvas      = el('mw-canvas');
    d.status      = el('mw-status');
    d.caption     = el('mw-caption');
    d.empty       = el('mw-empty');
    d.disclosure  = el('mw-disclosure');
    d.stormPrev   = el('mw-storm-prev');
    d.stormNext   = el('mw-storm-next');
    d.smooth      = el('mw-smooth');
    d.modeSel     = el('mw-mode');
    d.bdWrap      = el('mw-backdrop-wrap');
    d.bdChk       = el('mw-backdrop');
    d.bdOpac      = el('mw-bd-opacity');
    d.exFmt       = el('mw-exfmt');
    d.exBtn       = el('mw-export');
    d.exStatus    = el('mw-export-status');
    if (d.canvas && d.canvas.getContext) this.ctx = d.canvas.getContext('2d');

    if (d.stormSel) d.stormSel.addEventListener('change', function () { self._selectStorm(self.dom.stormSel.value); });
    if (d.overpassSel) d.overpassSel.addEventListener('change', function () { self._selectOverpass(parseInt(self.dom.overpassSel.value, 10)); });
    if (d.toggle) {
      d.toggle.innerHTML = '';
      PRODUCTS.forEach(function (p) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'mw-seg' + (p.key === self.product ? ' active' : '');
        b.textContent = p.label;
        b.setAttribute('data-product', p.key);
        b.addEventListener('click', function () { self._chooseProduct(p.key); });
        d.toggle.appendChild(b);
      });
    }
    if (d.stormPrev) d.stormPrev.addEventListener('click', function () { self._stepStorm(-1); });
    if (d.stormNext) d.stormNext.addEventListener('click', function () { self._stepStorm(1); });
    if (d.smooth) {
      d.smooth.innerHTML = '';
      [['Smoothed', false], ['Raw', true]].forEach(function (pair) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'mw-seg' + ((!!pair[1] === self.raw) ? ' active' : '');
        b.textContent = pair[0];
        b.setAttribute('data-raw', pair[1] ? '1' : '0');
        b.addEventListener('click', function () { self._setSmoothing(!!pair[1]); });
        d.smooth.appendChild(b);
      });
    }
    // mode toggle: This storm | Global
    if (d.modeSel) {
      d.modeSel.innerHTML = '';
      [['This storm', 'storm'], ['Global', 'global']].forEach(function (pair) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'mw-seg' + (pair[1] === self.mode ? ' active' : '');
        b.textContent = pair[0];
        b.setAttribute('data-mode', pair[1]);
        b.addEventListener('click', function () { self._setMode(pair[1]); });
        d.modeSel.appendChild(b);
      });
    }
    if (d.bdChk) d.bdChk.addEventListener('change', function () {
      self.backdrop = this.checked;
      if (self.dom.bdOpac) self.dom.bdOpac.disabled = !this.checked;
      if (self.backdrop && self.mode === 'storm') self._loadBackdrop();
      else if (self.backdrop && self.mode === 'global') self._loadGlobalMosaic();
      else { self.bdImg = null; self.bdFrame = null; self._draw(); }
    });
    if (d.bdOpac) d.bdOpac.addEventListener('input', function () {
      self.bdOpacity = Math.max(0.1, Math.min(1, (+this.value || 40) / 100)); self._draw();
    });
    if (d.exBtn) d.exBtn.addEventListener('click', function () { self._export(); });
    if (this.root) this.root.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowLeft') { self._step(-1); e.preventDefault(); }
      else if (e.key === 'ArrowRight') { self._step(1); e.preventDefault(); }
    });
    if (typeof window !== 'undefined') window.addEventListener('resize', function () { self._draw(); });
  };

  // ---- fetch + basemap ----------------------------------------------------
  MicrowaveViewer.prototype._fetchJson = function (path, noStore) {
    var url = this.base + path + (noStore ? ('?t=' + Date.now()) : '');
    return fetch(url, { cache: noStore ? 'no-store' : 'default' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + path); return r.json(); });
  };
  MicrowaveViewer.prototype._cdnRoot = function () { return this.base.replace(/\/[^/]+\/?$/, ''); };

  MicrowaveViewer.prototype._loadBasemap = function () {
    var self = this;
    function go() {
      var p = (window.TATRegions && TATRegions.loadGeo)
        ? TATRegions.loadGeo({ coast: '10m', land: '50m', states: true })
        : Promise.resolve({ coast: null, countries: null, states: null });
      return p.then(function (g) { self.geo = g; self._draw(); })
        .catch(function () { self.geo = { coast: null, countries: null, states: null }; });
    }
    if (window.TATRegions && TATRegions.loadGeo) return go();
    // lazy-load the shared regions engine if the page didn't include it
    var s = document.createElement('script');
    s.src = SITE + '/models/regions.js';
    s.onload = go; s.onerror = function () { self.geo = {}; };
    document.head.appendChild(s);
  };

  MicrowaveViewer.prototype._status = function (msg) {
    if (this.dom.status) {
      this.dom.status.style.display = msg ? 'flex' : 'none';
      var sp = this.dom.status.querySelector('span'); if (sp && msg) sp.textContent = msg;
    }
  };
  MicrowaveViewer.prototype._showEmpty = function (on) {
    if (this.dom.empty) this.dom.empty.style.display = on ? 'block' : 'none';
    var body = this.root && this.root.querySelector('#mw-body');
    if (body) body.style.display = on ? 'none' : '';
  };

  // ---- boot + manifest ----------------------------------------------------
  MicrowaveViewer.prototype._boot = function () {
    var self = this;
    this._status('Loading…');
    this._fetchGlobalBackdrop();   // wide-area mosaic availability for the Global view
    this._fetchJson('/manifest.json', true)
      .then(function (m) { self._onManifest(m); })
      .catch(function (e) { self._status(''); self._showEmpty(true); if (window.console) console.warn('microwave: manifest load failed', e); });
  };

  // The Global view shows a wide-area day-Vis/night-SWIR mosaic backdrop when the
  // producer publishes one (backdrops.json 'global' entry); until then the toggle
  // stays greyed in Global. Fetched once up front so the gating knows.
  MicrowaveViewer.prototype._fetchGlobalBackdrop = function () {
    var self = this, root = this._cdnRoot();
    fetch(root + '/floaters/backdrops.json?t=' + Date.now())
      .then(function (r) { if (!r.ok) throw 0; return r.json(); })
      .then(function (j) {
        self._globalBd = (j && j.backdrops && j.backdrops.global) || null;
        if (self.mode === 'global') self._syncBackdropGate();
      })
      .catch(function () {});
  };

  MicrowaveViewer.prototype._syncBackdropGate = function () {
    var gl = (this.mode === 'global'), hasMosaic = !!(this._globalBd && this._globalBd.key);
    var blocked = gl && !hasMosaic;
    if (this.dom.bdWrap) {
      this.dom.bdWrap.classList.toggle('mw-disabled', blocked);
      this.dom.bdWrap.title = blocked ? 'Wide-area Vis/SWIR mosaic in progress'
        : 'Satellite backdrop — Visible by day, Shortwave IR by night';
    }
    if (this.dom.bdChk) { this.dom.bdChk.disabled = blocked; if (blocked) { this.dom.bdChk.checked = false; this.backdrop = false; } }
    if (this.dom.bdOpac) this.dom.bdOpac.disabled = blocked || !this.backdrop;
  };

  // Load the wide-area mosaic as the backdrop for the Global view.
  MicrowaveViewer.prototype._loadGlobalMosaic = function () {
    var self = this, bk = this._globalBd, root = this._cdnRoot();
    if (!bk || !bk.key || !bk.bounds) { this.bdImg = null; this.bdFrame = null; this._draw(); return; }
    var img = new Image(); img.crossOrigin = 'anonymous';
    this.bdFrame = { t: bk.t || null, product: bk.product || 'Vis/SWIR', bounds: bk.bounds, sat: bk.sat || '', mosaic: true };
    img.onload = function () { if (self.mode === 'global') { self.bdImg = img; self._draw(); } };
    img.onerror = function () { self.bdImg = null; self.bdFrame = null; self._draw(); };
    img.src = root + '/' + bk.key;
  };

  MicrowaveViewer.prototype._onManifest = function (m) {
    this.manifest = m || {};
    this.storms = (m && m.storms) || [];
    if (this.dom.disclosure && m && m.disclosure) this.dom.disclosure.textContent = m.disclosure;
    if (!this.storms.length) { this._status(''); this._showEmpty(true); return; }
    this._showEmpty(false);
    this._buildStormSelect();
    var startSlug = (m && m.default_slug) || (this.storms[0] && this.storms[0].slug);
    this._selectStorm(startSlug);
  };

  MicrowaveViewer.prototype._stormBySlug = function (slug) {
    for (var i = 0; i < this.storms.length; i++) if (this.storms[i].slug === slug) return this.storms[i];
    return null;
  };
  MicrowaveViewer.prototype._buildStormSelect = function () {
    var sel = this.dom.stormSel; if (!sel) return;
    sel.innerHTML = '';
    this.storms.forEach(function (s) {
      var o = document.createElement('option'); o.value = s.slug; o.textContent = _stormLabel(s); sel.appendChild(o);
    });
  };
  // Concise label: name + basin/year. No auto-generated "N passes" summary blurb.
  function _stormLabel(s) {
    var nm = s.name || s.atcf || s.slug;
    var by = ((s.basin || '') + ' ' + (s.year || '')).trim();
    return by ? (nm + '  ·  ' + by) : nm;
  }

  // ---- storm -> overpasses ------------------------------------------------
  MicrowaveViewer.prototype._selectStorm = function (slug) {
    var s = this._stormBySlug(slug); if (!s) return;
    this.curStorm = slug;
    if (this.dom.stormSel) this.dom.stormSel.value = slug;
    this._syncStormNav();
    this.bdImg = null; this.bdFrame = null;        // reset backdrop on storm switch
    var self = this, seq = ++this._fetchSeq;
    this._status('Loading…');
    this._fetchJson('/' + slug + '/overpasses.json', false)
      .then(function (doc) {
        if (seq !== self._fetchSeq || self.curStorm !== slug) return;
        self._status('');
        self.overpasses = (doc && doc.overpasses) || [];
        self._buildOverpassSelect();
        if (self.overpasses.length) self._selectOverpass(self.overpasses.length - 1);  // latest
      })
      .catch(function (e) { if (seq === self._fetchSeq) { self._status('Could not load overpasses.'); if (window.console) console.warn('microwave: overpasses failed', e); } });
  };

  MicrowaveViewer.prototype._buildOverpassSelect = function () {
    var sel = this.dom.overpassSel; if (!sel) return;
    sel.innerHTML = '';
    this.overpasses.forEach(function (o, i) {
      var opt = document.createElement('option'); opt.value = String(i); opt.textContent = _overpassLabel(o); sel.appendChild(opt);
    });
  };
  // Concise label: time + sensor/platform. Intensity tail dropped (it's in the caption).
  function _overpassLabel(o) {
    var t = fmtTime(o.valid_utc), sensor = o.sensor || '';
    if (o.platform) sensor += ' ' + o.platform;
    return sensor ? (t + '  ·  ' + sensor) : t;
  }

  // ---- overpass + product -------------------------------------------------
  MicrowaveViewer.prototype._selectOverpass = function (idx) {
    if (idx == null || idx < 0 || idx >= this.overpasses.length) return;
    this.curOverpass = this.overpasses[idx];
    if (this.dom.overpassSel) this.dom.overpassSel.value = String(idx);
    this._syncProductAvailability();
    if (this.backdrop && this.mode === 'storm') this._loadBackdrop();
    this._draw();
  };
  MicrowaveViewer.prototype._step = function (delta) {
    if (this.mode !== 'storm' || !this.overpasses.length || !this.curOverpass) return;
    var idx = this.overpasses.indexOf(this.curOverpass); if (idx < 0) idx = 0;
    var next = Math.min(this.overpasses.length - 1, Math.max(0, idx + delta));
    if (next !== idx) this._selectOverpass(next);
  };
  MicrowaveViewer.prototype._findNearestOverpassWithProduct = function (key, fromIdx) {
    var ops = this.overpasses, n = ops.length; if (!n) return -1;
    var start = (typeof fromIdx === 'number') ? fromIdx : ops.indexOf(this.curOverpass);
    if (start < 0) start = n - 1;
    function has(i) { return !!tileRel(ops[i], key); }
    if (has(start)) return start;
    for (var d = 1; d < n; d++) {
      if (start + d < n && has(start + d)) return start + d;
      if (start - d >= 0 && has(start - d)) return start - d;
    }
    return -1;
  };
  MicrowaveViewer.prototype._chooseProduct = function (key) {
    if (this.mode === 'global') { this._setProduct(key); return; }
    if (tileRel(this.curOverpass, key)) { this._setProduct(key); return; }
    var idx = this._findNearestOverpassWithProduct(key);
    if (idx >= 0) { this.product = key; this._selectOverpass(idx); }
    else this._setProduct(key);
  };
  MicrowaveViewer.prototype._setProduct = function (key) {
    this.product = key;
    var btns = this.dom.toggle ? this.dom.toggle.querySelectorAll('.mw-seg') : [];
    for (var i = 0; i < btns.length; i++) btns[i].classList.toggle('active', btns[i].getAttribute('data-product') === key);
    this._draw();
  };
  MicrowaveViewer.prototype._syncProductAvailability = function () {
    if (this.mode === 'global') return;
    var o = this.curOverpass, btns = this.dom.toggle ? this.dom.toggle.querySelectorAll('.mw-seg') : [], firstAvail = null;
    for (var i = 0; i < btns.length; i++) {
      var key = btns[i].getAttribute('data-product'), has = !!tileRel(o, key);
      btns[i].disabled = !has;
      btns[i].classList.toggle('mw-unavailable', !has);
      btns[i].classList.toggle('active', key === this.product);
      if (has && firstAvail === null) firstAvail = key;
    }
    if (!tileRel(o, this.product) && firstAvail) this._setProduct(firstAvail);
  };

  // ---- per-storm nav ------------------------------------------------------
  MicrowaveViewer.prototype._stormIndex = function () {
    for (var i = 0; i < this.storms.length; i++) if (this.storms[i].slug === this.curStorm) return i;
    return -1;
  };
  MicrowaveViewer.prototype._stepStorm = function (delta) {
    if (!this.storms.length) return;
    var idx = this._stormIndex(); if (idx < 0) idx = 0;
    var next = Math.min(this.storms.length - 1, Math.max(0, idx + delta));
    if (next !== idx) this._selectStorm(this.storms[next].slug);
  };
  MicrowaveViewer.prototype._syncStormNav = function () {
    var idx = this._stormIndex(), n = this.storms.length, gl = (this.mode === 'global');
    if (this.dom.stormPrev) this.dom.stormPrev.disabled = gl || (idx <= 0);
    if (this.dom.stormNext) this.dom.stormNext.disabled = gl || (idx < 0 || idx >= n - 1);
    if (this.dom.stormSel) this.dom.stormSel.disabled = gl;
    if (this.dom.overpassSel) this.dom.overpassSel.disabled = gl;
  };

  // ---- smoothing ----------------------------------------------------------
  MicrowaveViewer.prototype._setSmoothing = function (raw) {
    this.raw = !!raw;
    var btns = this.dom.smooth ? this.dom.smooth.querySelectorAll('.mw-seg') : [];
    for (var i = 0; i < btns.length; i++) btns[i].classList.toggle('active', (btns[i].getAttribute('data-raw') === '1') === this.raw);
    this._draw();
  };

  // ---- mode (storm | global) ----------------------------------------------
  MicrowaveViewer.prototype._setMode = function (mode) {
    if (mode === this.mode) return;
    this.mode = mode;
    var btns = this.dom.modeSel ? this.dom.modeSel.querySelectorAll('.mw-seg') : [];
    for (var i = 0; i < btns.length; i++) btns[i].classList.toggle('active', btns[i].getAttribute('data-mode') === mode);
    var gl = (mode === 'global');
    // Global: no single storm cutout, but a wide-area mosaic backdrop when one is
    // published. Gate the toggle on mosaic availability (greyed otherwise).
    this.bdImg = null; this.bdFrame = null;
    this._syncBackdropGate();
    this._syncStormNav();
    if (gl) {
      this._loadGlobal();
      if (this.backdrop && this._globalBd) this._loadGlobalMosaic();
    } else if (this.backdrop) {
      this._loadBackdrop();   // back to storm view -> its own (per-storm) backdrop
    }
    else { this._syncProductAvailability(); if (this.backdrop) this._loadBackdrop(); this._draw(); }
  };

  // Union recent overpasses across ALL storms (last GLOBAL_WINDOW_H hours).
  MicrowaveViewer.prototype._loadGlobal = function () {
    var self = this, seq = ++this._fetchSeq;
    this._status('Loading global…');
    var cutoff = Date.now() - GLOBAL_WINDOW_H * 3600 * 1000;
    var jobs = (this.storms || []).map(function (s) {
      return self._fetchJson('/' + s.slug + '/overpasses.json', false)
        .then(function (doc) { return { slug: s.slug, ops: (doc && doc.overpasses) || [] }; })
        .catch(function () { return { slug: s.slug, ops: [] }; });
    });
    Promise.all(jobs).then(function (res) {
      if (seq !== self._fetchSeq) return;
      var all = [];
      res.forEach(function (r) {
        r.ops.forEach(function (o) {
          var t = Date.parse(o.valid_utc) || 0;
          if (t >= cutoff && boundsOf(o)) { o.__slug = r.slug; all.push(o); }
        });
      });
      all.sort(function (a, b) { return (Date.parse(a.valid_utc) || 0) - (Date.parse(b.valid_utc) || 0); });
      if (all.length > GLOBAL_MAX) all = all.slice(all.length - GLOBAL_MAX);
      self.globalOps = all;
      self._status('');
      self._draw();
    });
  };

  // ---- backdrop (storm mode; clone of ascat) ------------------------------
  MicrowaveViewer.prototype._matchTime = function () {
    return (this.curOverpass && Date.parse(this.curOverpass.valid_utc)) || Date.now();
  };
  MicrowaveViewer.prototype._floaterSat = function (basin) {
    return (['WP', 'SH', 'SP', 'IO', 'SI', 'AU'].indexOf(String(basin || '').toUpperCase()) >= 0) ? 'Himawari' : 'GOES';
  };
  MicrowaveViewer.prototype._loadBackdrop = function () {
    var self = this;
    if (!this.backdrop || this.mode !== 'storm' || !this.curOverpass) { this.bdImg = null; return; }
    var s = this._stormBySlug(this.curStorm); if (!s) return;
    var root = this._cdnRoot(), matchT = this._matchTime();
    var perStorm = function (slug) {
      var cached = self._floaterCache[slug];
      var p = cached ? Promise.resolve(cached) : fetch(root + '/floaters/' + slug + '/manifest.json?t=' + Date.now())
        .then(function (r) { if (!r.ok) throw 0; return r.json(); }).then(function (j) { self._floaterCache[slug] = j; return j; });
      return p.then(function (fm) {
        var b = (fm.bands && (fm.bands.ir || fm.bands.irbd)) || null;
        var frames = (b && b.frames) || [];
        if (!frames.length) throw 0;
        var best = backdropFrame(frames);
        if (!best) { self.bdImg = null; self.bdFrame = null; self._draw(); return; }
        var src = best.bd_key || best.backdrop_key || null;
        if (!src || !best.bounds) { self.bdImg = null; self.bdFrame = null; self._draw(); return; }
        var img = new Image(); img.crossOrigin = 'anonymous';
        // backward-compatible: prefer the producer's true product (Vis/SWIR) when
        // present (bd_product, the new producer); else fall back to the band label.
        var product = best.bd_product || (b.label) || 'Satellite';
        self.bdFrame = { t: best.t, product: product, bounds: best.bounds, sat: self._floaterSat(s.basin) };
        img.onload = function () { self.bdImg = img; self._draw(); };
        img.onerror = function () { self.bdImg = null; self.bdFrame = null; self._draw(); };
        img.src = root + '/' + src;
      });
    };
    // Try the slug derived from the ATCF id FIRST (works even when the storm has
    // been dropped from the top floaters/manifest.json), then fall back to the
    // top-manifest name/atcf match for any storm whose floater slug differs.
    var topMatch = function () {
      return fetch(root + '/floaters/manifest.json?t=' + Date.now())
        .then(function (r) { if (!r.ok) throw 0; return r.json(); })
        .then(function (top) {
          var st = (top.storms || []), hit = null;
          for (var i = 0; i < st.length; i++) {
            var f = st[i], fid = String(f.id || '').toLowerCase(), fnm = String(f.name || '').toLowerCase();
            if ((s.atcf && fid.indexOf(String(s.atcf).toLowerCase()) >= 0) ||
                (s.name && fnm === String(s.name).toLowerCase())) { hit = f; break; }
          }
          if (!hit || !hit.slug) throw 0;
          return perStorm(hit.slug);
        });
    };
    var derived = floaterSlug(s.atcf);
    (derived ? perStorm(derived).catch(topMatch) : topMatch())
      .catch(function () { self.bdImg = null; self.bdFrame = null; self._draw(); });
  };

  // ---- tile loading -------------------------------------------------------
  MicrowaveViewer.prototype._tile = function (rel) {
    var url = this.base + '/' + rel;
    var im = this._tiles[url];
    if (im) return im;
    var self = this; im = new Image(); im.crossOrigin = 'anonymous';
    im.onload = function () { self._draw(); };
    im.onerror = function () {};
    im.src = url;
    this._tiles[url] = im;
    return im;
  };

  // ---- canvas render ------------------------------------------------------
  MicrowaveViewer.prototype._layout = function () {
    var cv = this.dom.canvas; if (!cv) return null;
    var availW = (this.dom.frame && this.dom.frame.clientWidth) || 900; availW = Math.max(360, availW);
    var figW = Math.max(availW, 760), pad = 16, headerH = 54, footerH = 26;
    // mapH ratio sets the map pane aspect (W/H = 1/0.6 = 1.667); the backdrop
    // producer pre-widens the satellite raster to THIS aspect so it fills the
    // frame edge-to-edge. Keep in sync with ascat.js _layout + the producer's
    // BACKDROP_VIEW_ASPECT (floater_poller.py).
    var mapH = Math.round(figW * 0.6);
    var figH = pad + headerH + mapH + 10 + footerH + pad;
    var dpr = Math.min((typeof window !== 'undefined' && window.devicePixelRatio) || 1, 2);
    this.dpr = dpr; this.figW = figW; this.figH = figH;
    cv.width = Math.round(figW * dpr); cv.height = Math.round(figH * dpr);
    cv.style.width = availW + 'px'; cv.style.height = (availW * figH / figW) + 'px';
    return {
      pad: pad, header: { x: pad, y: pad, w: figW - 2 * pad, h: headerH },
      map: { x: pad, y: pad + headerH, w: figW - 2 * pad, h: mapH },
      footerY: pad + headerH + mapH + 10 + footerH - 8
    };
  };

  MicrowaveViewer.prototype._extent = function () {
    if (this.mode === 'global') {
      var TR = (typeof window !== 'undefined') && window.TATRegions;
      if (TR && TR.get && TR.get('global')) return TR.extentOf(TR.get('global'));
      return [-180, 180, -62, 62];
    }
    // storm: when a backdrop is shown, the frame IS the satellite raster -- fit
    // EXACTLY to its bounds (the producer pre-widens it to this map aspect, so
    // _aspectExtent is a no-op and it fills the frame edge-to-edge with no bare
    // basemap). With no backdrop, fit to the overpass cutout, padded a touch so
    // the swath isn't flush to the frame. bounds = [W,S,E,N] -> [W,E,S,N].
    var bd = (this.backdrop && this.bdFrame && this.bdFrame.bounds) || null;
    if (bd) return [bd[0], bd[2], bd[1], bd[3]];   // backdrop bounds, no pad
    var b = boundsOf(this.curOverpass);
    if (b) { var px = (b[2] - b[0]) * 0.06, py = (b[3] - b[1]) * 0.06;
             return [b[0] - px, b[2] + px, b[1] - py, b[3] + py]; }   // [W,E,S,N]
    return [-100, -5, 0, 55];
  };
  MicrowaveViewer.prototype._aspectExtent = function (ext, W, H) {
    var w = ext[0], e = ext[1], s = ext[2], n = ext[3], midLat = (s + n) / 2;
    var cosl = Math.max(0.12, Math.cos(midLat * Math.PI / 180));
    var lonSpan = e - w, latSpan = n - s, target = (W / H) / cosl, cur = lonSpan / latSpan;
    if (cur < target) { var nl = latSpan * target, cx = (w + e) / 2; w = cx - nl / 2; e = cx + nl / 2; }
    else { var nh = lonSpan / target, cy = (s + n) / 2; s = cy - nh / 2; n = cy + nh / 2; }
    return [w, e, s, n];
  };

  MicrowaveViewer.prototype._draw = function () {
    var g = this.ctx; if (!g) return;
    var L = this._layout(); if (!L) return;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, this.dom.canvas.width, this.dom.canvas.height);
    g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    g.fillStyle = STYLE.bg; g.fillRect(0, 0, this.figW, this.figH);
    this._drawHeader(g, L);
    this._drawMap(g, L);
    this._drawFooter(g, L);
    this._renderCaption();
  };

  MicrowaveViewer.prototype._drawMap = function (g, L) {
    var m = L.map, ext = this._aspectExtent(this._extent(), m.w, m.h);
    if (ext[2] < -90) ext[2] = -90; if (ext[3] > 90) ext[3] = 90;   // keep lat labels valid (global)
    var proj = (window.TATRegions && TATRegions.project)
      ? function (lo, la) { return TATRegions.project(lo, la, ext, m.w, m.h); }
      : function (lo, la) { return [(lo - ext[0]) / (ext[1] - ext[0]) * m.w, (ext[3] - la) / (ext[3] - ext[2]) * m.h]; };
    g.save();
    g.beginPath(); g.rect(m.x, m.y, m.w, m.h); g.clip();
    g.translate(m.x, m.y);
    g.lineJoin = 'round'; g.lineCap = 'round';
    // 1) ocean + land fill
    if (window.TATRegions && TATRegions.drawBasemapFill && this.geo && this.geo.countries) {
      TATRegions.drawBasemapFill(g, ext, { countries: this.geo.countries }, m.w, m.h, { ocean: STYLE.ocean, land: STYLE.land });
    } else { g.fillStyle = STYLE.ocean; g.fillRect(0, 0, m.w, m.h); }
    // 2) satellite backdrop (storm mode, under the data)
    this._drawBackdrop(g, proj);
    // 3) MW data tile(s)
    g.imageSmoothingEnabled = !this.raw;
    if (this.mode === 'global') this._drawGlobalTiles(g, proj);
    else this._drawStormTile(g, proj);
    g.imageSmoothingEnabled = true;
    // 4) graticule + coast on top
    this._drawGraticule(g, ext, m.w, m.h);
    if (window.TATRegions && TATRegions.drawBasemapLines) {
      TATRegions.drawBasemapLines(g, ext, this.geo, m.w, m.h, { coast: STYLE.coast, coastLw: STYLE.coastLw, state: STYLE.state, stateLw: STYLE.stateLw });
    }
    g.restore();
    g.save(); g.strokeStyle = C.border; g.lineWidth = 1; g.strokeRect(m.x + 0.5, m.y + 0.5, m.w - 1, m.h - 1); g.restore();
    this._drawLegend(g, L);
    this._drawWatermark(g, L);
  };

  MicrowaveViewer.prototype._drawTileImg = function (g, proj, im, bounds) {
    if (!im || !im.complete || !im.naturalWidth || !bounds) return;
    var tl = proj(bounds[0], bounds[3]), br = proj(bounds[2], bounds[1]);   // (W,N)->tl, (E,S)->br
    try { g.drawImage(im, tl[0], tl[1], br[0] - tl[0], br[1] - tl[1]); } catch (e) {}
  };
  MicrowaveViewer.prototype._drawStormTile = function (g, proj) {
    var o = this.curOverpass; if (!o) return;
    var tr = tileRel(o, this.product, this.raw); if (!tr) return;
    this._drawTileImg(g, proj, this._tile(tr.rel), boundsOf(o));
  };
  MicrowaveViewer.prototype._drawGlobalTiles = function (g, proj) {
    var self = this;
    (this.globalOps || []).forEach(function (o) {
      var tr = tileRel(o, self.product, self.raw); if (!tr) return;
      self._drawTileImg(g, proj, self._tile(tr.rel), boundsOf(o));
    });
  };

  MicrowaveViewer.prototype._drawBackdrop = function (g, proj) {
    if (!this.backdrop || !this.bdImg || !this.bdImg.complete || !this.bdImg.naturalWidth) return;
    // Storm view draws its per-storm cutout; Global view draws ONLY the wide-area
    // mosaic (never a stale storm cutout).
    if (this.mode !== 'storm' && !(this.bdFrame && this.bdFrame.mosaic)) return;
    var b = this.bdFrame && this.bdFrame.bounds; if (!b) return;
    var tl = proj(b[0], b[3]), br = proj(b[2], b[1]);
    // Bleed ~1px outward so sub-pixel rounding never leaves a bare-basemap seam at
    // the frame edge when the aspect-matched backdrop fills the whole plot; the
    // map-rect clip in _drawMap crops the overflow.
    var x = tl[0] - 1, y = tl[1] - 1, w = (br[0] - tl[0]) + 2, h = (br[1] - tl[1]) + 2;
    g.save(); g.globalAlpha = this.bdOpacity;
    try { g.drawImage(this.bdImg, x, y, w, h); } catch (e) {}
    g.restore();
  };

  MicrowaveViewer.prototype._drawGraticule = function (g, ext, W, H) {
    var lonSpan = ext[1] - ext[0], latSpan = ext[3] - ext[2];
    var step = (Math.max(lonSpan, latSpan) > 40) ? 20 : (Math.max(lonSpan, latSpan) > 12 ? 5 : 2);
    g.save(); g.strokeStyle = STYLE.grid; g.lineWidth = 0.6; g.beginPath();
    var l0 = Math.ceil(ext[0] / step) * step, lon, x;
    for (lon = l0; lon <= ext[1]; lon += step) { x = (lon - ext[0]) / lonSpan * W; g.moveTo(x, 0); g.lineTo(x, H); }
    var b0 = Math.ceil(ext[2] / step) * step, lat, y;
    for (lat = b0; lat <= ext[3]; lat += step) { y = (ext[3] - lat) / latSpan * H; g.moveTo(0, y); g.lineTo(W, y); }
    g.stroke();
    g.fillStyle = STYLE.gridLab; g.font = '600 9px ' + FONT; g.textBaseline = 'bottom'; g.textAlign = 'center';
    for (lon = l0; lon <= ext[1]; lon += step) { x = (lon - ext[0]) / lonSpan * W; if (x < 14 || x > W - 14) continue; g.fillText(lonLab(lon), x, H - 2); }
    g.textBaseline = 'middle'; g.textAlign = 'left';
    for (lat = b0; lat <= ext[3]; lat += step) { y = (ext[3] - lat) / latSpan * H; if (y < 9 || y > H - 9) continue; g.fillText(latLab(lat), 3, y); }
    g.restore();
  };

  MicrowaveViewer.prototype._drawHeader = function (g, L) {
    var h = L.header;
    g.save(); g.textAlign = 'left'; g.textBaseline = 'alphabetic';
    g.fillStyle = C.fg; g.font = '800 19px ' + FONT;
    var scope, sub, o = this.curOverpass;
    if (this.mode === 'global') {
      scope = 'Global';
      var nd = this._globalDrawCount();
      sub = nd + ' overpass' + (nd === 1 ? '' : 'es') + '  ·  last ' + GLOBAL_WINDOW_H + ' h';
    } else {
      var s = this._stormBySlug(this.curStorm) || {};
      scope = (s.name || s.atcf || s.slug || 'Storm');
      if (o) {
        var sensor = (o.sensor || ''); if (o.platform) sensor += ' ' + o.platform;
        sub = sensor + '  ·  ' + fmtZ(o.valid_utc) + ' (' + ageStr(o.valid_utc) + ')';
        if (typeof o.intensity_kt === 'number') { sub += '  ·  ' + o.intensity_kt + ' kt'; if (o.dev_level) sub += ' ' + o.dev_level; }
      } else sub = 'No overpass loaded.';
      if (this.backdrop && this.bdImg && this.bdFrame) {
        sub += '   ·   🛰 ' + this.bdFrame.sat + ' ' + String(this.bdFrame.product).toUpperCase() + ' ' + fmtZ(this.bdFrame.t);
      }
    }
    g.fillText(scope + '  ·  Passive Microwave', h.x, h.y + 18);
    g.fillStyle = C.muted; g.font = '600 12.5px ' + FONT; g.fillText(sub, h.x, h.y + 38);
    // product chip (right)
    var prodLabel = '';
    for (var i = 0; i < PRODUCTS.length; i++) if (PRODUCTS[i].key === this.product) prodLabel = PRODUCTS[i].label;
    g.font = '700 11px ' + FONT; g.textAlign = 'right';
    var cw = g.measureText(prodLabel).width + 16, cx = h.x + h.w - cw, cy = h.y + 6, ch = 18;
    roundRectPath(g, cx, cy, cw, ch, 4);
    g.fillStyle = 'rgba(43,156,255,0.14)'; g.fill(); g.strokeStyle = 'rgba(43,156,255,0.5)'; g.lineWidth = 1; g.stroke();
    g.fillStyle = '#bcdcff'; g.textBaseline = 'middle'; g.fillText(prodLabel, h.x + h.w - 8, cy + ch / 2 + 0.5);
    g.restore();
  };

  // Per-product legend: 37H/91H -> a discrete BT colorbar from manifest.legends
  // stops; color37/color91 -> a short RGB descriptor (qualitative composite).
  MicrowaveViewer.prototype._drawLegend = function (g, L) {
    var m = L.map, legs = (this.manifest && this.manifest.legends) || {};
    var leg = legs[this.product];
    g.save();
    if (leg && leg.discrete && leg.stops && leg.stops.length) {
      var stops = leg.stops, n = stops.length;
      var barW = Math.min(m.w - 84, 430), segW = barW / n, barH = 11, pad = 8;
      var labH = 11, capH = 12, boxW = barW + pad * 2, boxH = pad * 2 + barH + 4 + labH + 5 + capH;
      var x = m.x + 10, y = m.y + m.h - boxH - 9;
      roundRectPath(g, x, y, boxW, boxH, 6); g.fillStyle = 'rgba(7,16,28,0.86)'; g.fill();
      g.strokeStyle = C.border; g.lineWidth = 1; g.stroke();
      var bx = x + pad, by = y + pad;
      for (var i = 0; i < n; i++) { g.fillStyle = stops[i].color || '#888'; g.fillRect(bx + i * segW, by, segW + 0.6, barH); }
      g.strokeStyle = 'rgba(220,232,246,0.30)'; g.lineWidth = 0.8; g.strokeRect(bx + 0.5, by + 0.5, barW - 1, barH - 1);
      g.font = '600 8px ' + FONT; g.textAlign = 'center'; g.textBaseline = 'top'; g.fillStyle = C.fg;
      for (i = 0; i < n; i++) g.fillText(String(stops[i].label), bx + i * segW + segW / 2, by + barH + 4);
      g.fillStyle = C.muted; g.font = '600 9px ' + FONT; g.textAlign = 'left';
      g.fillText((leg.label || 'Brightness Temperature (K)'), bx, by + barH + 4 + labH + 5);
    } else {
      var label = (leg && leg.label) || this.product;
      var txt = label + (leg && leg.legendHtml ? '' : '');
      g.font = '600 11px ' + FONT; var tw = g.measureText(txt).width;
      var bw = tw + 20, bh = 24, lx = m.x + 10, ly = m.y + m.h - bh - 9;
      roundRectPath(g, lx, ly, bw, bh, 6); g.fillStyle = 'rgba(7,16,28,0.86)'; g.fill();
      g.strokeStyle = C.border; g.lineWidth = 1; g.stroke();
      g.fillStyle = C.fg; g.textBaseline = 'middle'; g.textAlign = 'left'; g.fillText(txt, lx + 10, ly + bh / 2 + 0.5);
    }
    g.restore();
  };

  MicrowaveViewer.prototype._drawWatermark = function (g, L) {
    var m = L.map;
    g.save(); g.font = '700 12px ' + FONT; g.textAlign = 'right'; g.textBaseline = 'top';
    g.shadowColor = 'rgba(4,9,16,0.85)'; g.shadowBlur = 3; g.fillStyle = 'rgba(233,241,250,0.5)';
    g.fillText(WATERMARK, m.x + m.w - 10, m.y + 9);
    g.restore();
  };
  MicrowaveViewer.prototype._drawFooter = function (g, L) {
    g.save(); g.font = '500 10.5px ' + FONT; g.textAlign = 'left'; g.textBaseline = 'alphabetic'; g.fillStyle = C.muted;
    var disc = (this.manifest && this.manifest.disclosure) ||
      'Observed passive microwave for tropical cyclones. NASA GPM/PPS near-real-time + NOAA/CIRA TC-PRIMED archive.';
    var maxw = L.map.w - 150, s = disc;
    if (g.measureText(s).width > maxw) { while (s.length > 8 && g.measureText(s + '…').width > maxw) s = s.slice(0, -1); s += '…'; }
    g.fillText(s, L.pad, L.footerY);
    g.textAlign = 'right'; g.font = '600 10.5px ' + FONT;
    g.fillText('NASA GPM/PPS + NOAA/CIRA TC-PRIMED', L.pad + L.map.w, L.footerY);
    g.restore();
  };

  // ---- caption (HTML below the canvas) ------------------------------------
  // Recent overpasses actually DRAWABLE for the current product (have a tile via
  // tileRel, incl. v2 aliases) -- the honest count for the header/caption, not the
  // raw globalOps length which counts passes with no tile for this product.
  MicrowaveViewer.prototype._globalDrawCount = function () {
    var self = this, n = 0;
    (this.globalOps || []).forEach(function (o) { if (tileRel(o, self.product)) n++; });
    return n;
  };

  MicrowaveViewer.prototype._renderCaption = function () {
    if (!this.dom.caption) return;
    if (this.mode === 'global') {
      this.dom.caption.innerHTML = '<b>Global</b> &nbsp;·&nbsp; ' + this._globalDrawCount() +
        ' overpasses (last ' + GLOBAL_WINDOW_H + ' h) &nbsp;·&nbsp; ' + this._prodLabel();
      return;
    }
    var o = this.curOverpass; if (!o) { this.dom.caption.innerHTML = ''; return; }
    var bits = [], sensor = (o.sensor || ''); if (o.platform) sensor += ' ' + o.platform;
    if (sensor) bits.push('<b>' + sensor + '</b>');
    if (o.valid_utc) bits.push('Valid ' + fmtTime(o.valid_utc) + ' (' + o.valid_utc.replace('T', ' ').replace('Z', ' UTC') + ')');
    if (typeof o.intensity_kt === 'number') { var s = o.intensity_kt + ' kt'; if (o.dev_level) s += ' ' + o.dev_level; bits.push(s); }
    bits.push(this._prodLabel());
    var html = bits.join(' &nbsp;·&nbsp; ');
    if (!tileRel(o, this.product)) html += ' &nbsp;·&nbsp; <i>(' + this._prodLabel() + ' not available for this pass)</i>';
    this.dom.caption.innerHTML = html;
  };
  MicrowaveViewer.prototype._prodLabel = function () {
    for (var i = 0; i < PRODUCTS.length; i++) if (PRODUCTS[i].key === this.product) return PRODUCTS[i].label;
    return this.product;
  };

  // ---- GIF loop export (client canvas-capture; the chromed PNG is retired) -
  // Render each overpass (current product) to an offscreen canvas at a FIXED
  // extent (the storm's union bounds) so the loop is registration-stable, then
  // encode with gif.js. (Server /export needed chromed PNG URLs that no longer
  // exist, so export is client-side canvas capture now.)
  MicrowaveViewer.prototype._exStatus = function (msg) {
    if (this.dom.exStatus) { this.dom.exStatus.textContent = msg || ''; this.dom.exStatus.style.display = msg ? '' : 'none'; }
  };
  MicrowaveViewer.prototype._export = function () {
    if (this.encoding) return;
    if (this.mode === 'global') { this._exStatus('Export is per-storm; switch to a storm.'); return; }
    var key = this.product, self = this;
    var ops = (this.overpasses || []).filter(function (o) { return tileRel(o, key, self.raw) && boundsOf(o); });
    if (ops.length < 2) { this._exStatus('Need at least 2 ' + key + ' passes to make a loop.'); return; }
    // union bounds for a stable frame
    var W = 1e9, S = 1e9, E = -1e9, N = -1e9;
    ops.forEach(function (o) { var b = boundsOf(o); W = Math.min(W, b[0]); S = Math.min(S, b[1]); E = Math.max(E, b[2]); N = Math.max(N, b[3]); });
    var ext0 = [W - 0.4, E + 0.4, S - 0.4, N + 0.4];
    // preload tiles CORS-clean
    this.encoding = true; if (this.dom.exBtn) this.dom.exBtn.disabled = true;
    this._exStatus('Encoding GIF…');
    var imgs = [], pending = ops.length;
    ops.forEach(function (o, i) {
      var im = new Image(); im.crossOrigin = 'anonymous';
      im.onload = function () { imgs[i] = im; if (!--pending) build(); };
      im.onerror = function () { imgs[i] = null; if (!--pending) build(); };
      im.src = self.base + '/' + tileRel(o, key, self.raw).rel + '?cors=1';
    });
    function build() {
      var Wpx = Math.min(GIF_MAX_W, 760), Hpx = Math.round(Wpx * 0.62);
      var aspect = self._aspectExtent(ext0, Wpx, Hpx);
      var oc = document.createElement('canvas'); oc.width = Wpx; oc.height = Hpx;
      var octx = oc.getContext('2d');
      function proj(lo, la) {
        return (window.TATRegions && TATRegions.project) ? TATRegions.project(lo, la, aspect, Wpx, Hpx)
          : [(lo - aspect[0]) / (aspect[1] - aspect[0]) * Wpx, (aspect[3] - la) / (aspect[3] - aspect[2]) * Hpx];
      }
      self._ensureGifWorker(function (worker) {
        var gif;
        try { gif = new window.GIF({ workers: 2, quality: 10, width: Wpx, height: Hpx, workerScript: worker, background: STYLE.ocean }); }
        catch (e) { return self._exDone(false); }
        gif.on('finished', function (blob) { _download(blob, 'mw_' + (self.curStorm || 'storm') + '_' + key + '.gif'); self._exDone(true); });
        gif.on('error', function () { self._exDone(false); });
        var added = 0, last = ops.length - 1;
        ops.forEach(function (o, i) {
          octx.fillStyle = STYLE.ocean; octx.fillRect(0, 0, Wpx, Hpx);
          if (window.TATRegions && TATRegions.drawBasemapFill && self.geo && self.geo.countries)
            TATRegions.drawBasemapFill(octx, aspect, { countries: self.geo.countries }, Wpx, Hpx, { ocean: STYLE.ocean, land: STYLE.land });
          var im = imgs[i]; if (im && im.naturalWidth) {
            octx.imageSmoothingEnabled = !self.raw;
            var b = boundsOf(o), tl = proj(b[0], b[3]), br = proj(b[2], b[1]);
            try { octx.drawImage(im, tl[0], tl[1], br[0] - tl[0], br[1] - tl[1]); } catch (e) {}
          }
          if (window.TATRegions && TATRegions.drawBasemapLines)
            TATRegions.drawBasemapLines(octx, aspect, self.geo, Wpx, Hpx, { coast: STYLE.coast, coastLw: STYLE.coastLw, state: STYLE.state, stateLw: STYLE.stateLw });
          gif.addFrame(octx, { copy: true, delay: (i === last) ? 900 : 500 }); added++;
        });
        if (added < 2) return self._exDone(false);
        try { gif.render(); } catch (e) { self._exDone(false); }
      });
    }
  };
  MicrowaveViewer.prototype._exDone = function (ok) {
    this.encoding = false; if (this.dom.exBtn) this.dom.exBtn.disabled = false;
    this._exStatus(ok ? 'Done.' : 'GIF export failed — try again.');
    if (ok) { var self = this; setTimeout(function () { self._exStatus(''); }, 1500); }
  };
  MicrowaveViewer.prototype._ensureGifWorker = function (cb) {
    if (this._gifWorkerUrl) { cb(this._gifWorkerUrl); return; }
    if (typeof window === 'undefined' || typeof window.GIF === 'undefined') { cb(null); return; }
    var self = this, CDNW = 'https://cdnjs.cloudflare.com/ajax/libs/gif.js/0.2.0/gif.worker.js';
    fetch(CDNW).then(function (r) { return r.text(); }).then(function (src) {
      self._gifWorkerUrl = URL.createObjectURL(new Blob([src], { type: 'application/javascript' })); cb(self._gifWorkerUrl);
    }).catch(function () { cb(CDNW); });
  };

  function _download(blob, name) {
    var u = URL.createObjectURL(blob), a = document.createElement('a');
    a.href = u; a.download = name; document.body.appendChild(a); a.click(); document.body.removeChild(a);
    requestAnimationFrame(function () { URL.revokeObjectURL(u); });
  }

  // ---- auto-mount ---------------------------------------------------------
  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('DOMContentLoaded', function () {
      var r = el('microwave-viewer'); if (r) r.__microwaveView = new MicrowaveViewer(r);
    });
  }
  if (typeof window !== 'undefined') window.MicrowaveViewer = MicrowaveViewer;
  // reusable primitives for the explorer cockpit's native MW fields (re-host,
  // not rebuild): product list + tile/bounds accessors, additive-only.
  MicrowaveViewer.PRODUCTS = PRODUCTS;
  MicrowaveViewer.DEFAULT_PRODUCT = DEFAULT_PRODUCT;
  MicrowaveViewer.tileRel = tileRel;
  MicrowaveViewer.boundsOf = boundsOf;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      MicrowaveViewer: MicrowaveViewer, PRODUCTS: PRODUCTS, DEFAULT_PRODUCT: DEFAULT_PRODUCT,
      stormLabel: _stormLabel, overpassLabel: _overpassLabel, tileRel: tileRel, boundsOf: boundsOf,
      nearestFrame: nearestFrame
    };
  }
})();
