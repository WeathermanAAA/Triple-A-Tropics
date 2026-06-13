/* Ensemble Cyclone Centers viewer (/models/).
 *
 * Hydrates from the model-agnostic JSON at
 *   cdn.triple-a-tropics.com/models/enscenters/{model}/{cycle}.json
 * (manifest at .../enscenters/manifest.json). A hand-rolled CANVAS scatter of
 * every ensemble member's detected cyclone centers over a global equirectangular
 * basemap, colored by central pressure, animatable across forecast steps, with a
 * per-member peak table.
 *
 * Fully isolated from the HAFS viewer (hafs.js): different IIFE, different DOM
 * ids (enscenters-*), boots only on #enscenters-viewer. The transport engine
 * (single rAF loop, speed-as-dwell) is the HAFS pattern with the image-decode
 * gate removed - the canvas redraw is synchronous.
 *
 * House rules honored: no chart libs, no CDN map tiles (basemap is the bundled
 * Natural Earth GeoJSON, same-origin), no em-dashes in on-screen text.
 */
(function () {
  'use strict';

  var BASE = 'https://cdn.triple-a-tropics.com';
  var MANIFEST_URL = BASE + '/models/enscenters/manifest.json';
  var DATA_BASE = BASE + '/models/enscenters/';

  var SPEED_OPTIONS = [0.5, 1, 2, 4];   // playback dwell multiplier
  var BASE_FPS = 4;                     // steps/sec at 1x
  var POLL_IDLE_MS = 300000;            // re-check manifest every 5 min

  // Global equirectangular extent [lon_min, lon_max, lat_min, lat_max].
  // Pacific-centered (central_longitude = 180): the visible x-range is 0..360
  // with the dateline at center, so WPAC systems near +/-180 stay together and
  // the seam falls on the Atlantic. project() folds -180..180 inputs into 0..360.
  var MAP_EXTENT = [0, 360, -90, 90];

  // Pressure-bin ring colors (Andrew's reference, FINAL), keyed by manifest bin
  // key. Bold HOLLOW rings (stroke, no fill) on the navy basemap; warmer/pinker
  // = deeper (more intense). The bin thresholds/labels come from the JSON.
  var PRESSURE_BIN_COLORS = {
    gt1000: '#dfe8ff',     // >1000 hPa  - pale
    p990_1000: '#1f9bff',  // 990-1000   - blue
    p970_990: '#ffd21a',   // 970-990    - yellow
    p950_970: '#ff1f47',   // 950-970    - red
    lt950: '#ff3d9a'       // <950       - hot pink (most intense)
  };

  // Basemap (Andrew's reference, FINAL): navy panel, muted land.
  var BASEMAP = {
    ocean: '#07101c',
    land: '#2f3f59',
    coast: 'rgba(150,175,205,0.28)',
    grid: 'rgba(255,255,255,0.05)',
    coast_lw: 0.6,
    grid_lw: 0.5
  };

  var RING_ALPHA = 0.92;    // crisp but overlapping rings still read as density
  var BIN_ORDER = ['gt1000', 'p990_1000', 'p970_990', 'p950_970', 'lt950'];

  function el(id) { return document.getElementById(id); }
  function fmtInt(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ','); }
  function fix0(x) { var n = Number(x); return isFinite(n) ? n.toFixed(0) : '-'; }

  function binKey(p) {
    if (p < 950) return 'lt950';
    if (p < 970) return 'p950_970';
    if (p < 990) return 'p970_990';
    if (p < 1000) return 'p990_1000';
    return 'gt1000';
  }

  var WK = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function validLabel(initMs, stepH) {
    var d = new Date(initMs + stepH * 3600000);
    var hh = String(d.getUTCHours()).padStart(2, '0');
    return WK[d.getUTCDay()] + ' ' + MO[d.getUTCMonth()] + ' ' + d.getUTCDate() + ', ' + hh + 'Z';
  }

  // -------------------------------------------------------------------------
  function EnsCentersViewer(root) {
    this.root = root;
    this.dom = {
      stage: el('enscenters-stage'),
      canvas: el('enscenters-canvas'),
      status: el('enscenters-status'),
      models: el('enscenters-models'),
      play: el('enscenters-play'),
      stepB: el('enscenters-step-back'),
      stepF: el('enscenters-step-fwd'),
      fhour: el('enscenters-fhour'),
      valid: el('enscenters-valid'),
      speed: el('enscenters-speed'),
      scrub: el('enscenters-scrub'),
      legend: el('enscenters-legend'),
      peaks: el('enscenters-peaks'),
      subtitle: el('enscenters-subtitle'),
      tooltip: el('enscenters-tooltip'),
      empty: el('enscenters-empty')
    };
    this.ctx = this.dom.canvas.getContext('2d');
    this.basemapCanvas = document.createElement('canvas');

    this.manifest = null;
    this.model = null;
    this.data = null;
    this.frames = [];     // per-step arrays of [lat, lon, mslp, vmax]
    this.steps = [];
    this.initMs = 0;
    this.idx = 0;
    this.playing = false;
    this.speed = 1;
    this.raf = null;
    this.lastTick = 0;
    this.W = 0;
    this.H = 0;
    this.geo = { countries: null, coast: null };

    this._wire();
    this._boot();
  }

  EnsCentersViewer.prototype._status = function (msg) {
    var s = this.dom.status;
    if (!s) return;
    if (msg) { s.style.display = 'flex'; s.querySelector('span').textContent = msg; }
    else { s.style.display = 'none'; }
  };

  EnsCentersViewer.prototype._showEmpty = function (on) {
    if (this.dom.empty) this.dom.empty.style.display = on ? 'block' : 'none';
    // Hide the whole viewer chrome (stage + gutter + scrubber + captions), not
    // just the stage, so the empty state does not sit beside dead controls
    // (HAFS hides its controls the same way).
    var hide = [this.dom.stage, this.root.querySelector('.vw-aside'),
                this.root.querySelector('.vw-below')];
    var caps = this.root.querySelectorAll('.hafs-caption');
    for (var c = 0; c < caps.length; c++) hide.push(caps[c]);
    for (var i = 0; i < hide.length; i++) if (hide[i]) hide[i].style.display = on ? 'none' : '';
  };

  // ---- boot: load basemap + manifest in parallel ----
  EnsCentersViewer.prototype._boot = function () {
    var self = this;
    this._status('Loading…');
    Promise.all([this._loadBasemap(), this._fetchManifest()])
      .then(function (res) {
        self._onManifest(res[1]);
      })
      .catch(function (e) {
        console.warn('enscenters: boot failed', e);
        self._status('');
        self._showEmpty(true);
      });
  };

  EnsCentersViewer.prototype._loadBasemap = function () {
    var self = this;
    return Promise.all([
      fetch('/ne_110m_admin_0_countries.geojson').then(function (r) { return r.json(); }),
      fetch('/ne_110m_coastline.geojson').then(function (r) { return r.json(); })
    ]).then(function (g) {
      self.geo.countries = g[0];
      self.geo.coast = g[1];
    });
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
    // model selector (auto-hides while there is only one model)
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
    this._highlight(this.dom.models, slug);
    this._loadCycle(slug, entry.latest);
  };

  EnsCentersViewer.prototype._loadCycle = function (slug, cycle) {
    var self = this;
    this._pause();
    this.loadedCycle = cycle;   // what the poll compares against (not d.init_cycle)
    this._status('Loading ' + slug.toUpperCase() + ' ' + cycle + '…');
    var url = DATA_BASE + slug + '/' + cycle + '.json?v=' + cycle;
    fetch(url, { cache: 'force-cache' })
      .then(function (r) { if (!r.ok) throw new Error('cycle HTTP ' + r.status); return r.json(); })
      .then(function (d) { self._onData(d); })
      .catch(function (e) { console.warn('enscenters: cycle load failed', e); self._status('Could not load cycle.'); });
  };

  EnsCentersViewer.prototype._onData = function (d) {
    this.data = d;
    this.steps = d.run_steps || [];
    this.initMs = Date.parse(d.init_time);
    // index centers by step: members[].centers = [[step_h, lat, lon, mslp, vmax], ...]
    var byStep = {};
    for (var s = 0; s < this.steps.length; s++) byStep[this.steps[s]] = [];
    var members = d.members || [];
    for (var i = 0; i < members.length; i++) {
      var cs = members[i].centers || [];
      for (var k = 0; k < cs.length; k++) {
        var c = cs[k];
        var arr = byStep[c[0]];
        if (arr) arr.push([c[1], c[2], c[3], c[4]]);
      }
    }
    this.frames = this.steps.map(function (st) { return byStep[st] || []; });

    this.idx = 0;
    this.dom.scrub.min = 0;
    this.dom.scrub.max = Math.max(0, this.steps.length - 1);
    this.dom.scrub.value = 0;

    this._buildLegend(d.pressure_bins || []);
    this._buildPeaks(members);
    this._setSubtitle();
    this._resize();           // sizes canvas + draws basemap
    this._status('');
    this._show(0);
  };

  EnsCentersViewer.prototype._setSubtitle = function () {
    if (!this.dom.subtitle || !this.data) return;
    var d = this.data;
    var initLbl = validLabel(this.initMs, 0).replace(/^\w+ /, '');  // "Jun 13, 00Z"
    this.dom.subtitle.textContent =
      d.model_label + '  ·  init ' + initLbl + '  ·  ' + d.n_members +
      ' members  ·  ' + fmtInt(d.n_centers) + ' centers';
  };

  // ---- canvas sizing + basemap ----
  EnsCentersViewer.prototype._resize = function () {
    var stage = this.dom.stage;
    var cssW = stage.clientWidth || 900;
    var aspect = (MAP_EXTENT[1] - MAP_EXTENT[0]) / (MAP_EXTENT[3] - MAP_EXTENT[2]); // 2:1
    var cssH = Math.round(cssW / aspect);
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.W = Math.round(cssW * dpr);
    this.H = Math.round(cssH * dpr);
    var cv = this.dom.canvas;
    cv.width = this.W; cv.height = this.H;
    cv.style.width = cssW + 'px'; cv.style.height = cssH + 'px';
    this.dpr = dpr;
    this.ringR = 2.6 * dpr;     // ring radius (device px)
    this.ringLW = 1.7 * dpr;    // ring stroke ~1.5-2 css px
    this._drawBasemap();
    if (this.frames.length) this._show(this.idx);
  };

  EnsCentersViewer.prototype._project = function (lon, lat) {
    var e = MAP_EXTENT, L = lon;
    if (e[1] > 180 && L < e[0]) L += 360;
    return [(L - e[0]) / (e[1] - e[0]) * this.W, (e[3] - lat) / (e[3] - e[2]) * this.H];
  };

  // Trace each ring/line onto ctx, starting a new subpath at the antimeridian
  // seam (a projected jump > half the canvas width).
  EnsCentersViewer.prototype._traceGeo = function (g, geojson, closeRings) {
    if (!geojson || !geojson.features) return;
    var JUMP = this.W * 0.5;
    var self = this;
    function ring(coords) {
      var prevX = null, started = false;
      for (var i = 0; i < coords.length; i++) {
        var p = self._project(coords[i][0], coords[i][1]);
        if (prevX === null || Math.abs(p[0] - prevX) > JUMP) {
          if (started && closeRings) g.closePath();  // close the prior seam subpath
          g.moveTo(p[0], p[1]);
          started = true;
        } else {
          g.lineTo(p[0], p[1]);
        }
        prevX = p[0];
      }
      if (closeRings && started) g.closePath();
    }
    for (var f = 0; f < geojson.features.length; f++) {
      var geom = geojson.features[f].geometry;
      if (!geom) continue;
      var t = geom.type, co = geom.coordinates;
      if (t === 'Polygon') { for (var a = 0; a < co.length; a++) ring(co[a]); }
      else if (t === 'MultiPolygon') { for (var b = 0; b < co.length; b++) for (var c = 0; c < co[b].length; c++) ring(co[b][c]); }
      else if (t === 'LineString') { ring(co); }
      else if (t === 'MultiLineString') { for (var e2 = 0; e2 < co.length; e2++) ring(co[e2]); }
    }
  };

  EnsCentersViewer.prototype._drawBasemap = function () {
    var bg = this.basemapCanvas;
    bg.width = this.W; bg.height = this.H;
    var g = bg.getContext('2d');
    g.clearRect(0, 0, this.W, this.H);
    g.fillStyle = BASEMAP.ocean;
    g.fillRect(0, 0, this.W, this.H);

    // graticule (every 30 deg)
    g.strokeStyle = BASEMAP.grid;
    g.lineWidth = BASEMAP.grid_lw;
    g.beginPath();
    for (var lon = 0; lon < 360; lon += 30) { var p = this._project(lon, 0); g.moveTo(p[0], 0); g.lineTo(p[0], this.H); }
    for (var lat = -60; lat <= 60; lat += 30) { var q = this._project(0, lat); g.moveTo(0, q[1]); g.lineTo(this.W, q[1]); }
    g.stroke();

    // land fill
    if (this.geo.countries) {
      g.fillStyle = BASEMAP.land;
      g.beginPath();
      this._traceGeo(g, this.geo.countries, true);
      g.fill('nonzero');
    }
    // coastlines
    if (this.geo.coast) {
      g.strokeStyle = BASEMAP.coast;
      g.lineWidth = BASEMAP.coast_lw;
      g.lineJoin = 'round'; g.lineCap = 'round';
      g.beginPath();
      this._traceGeo(g, this.geo.coast, false);
      g.stroke();
    }
  };

  // ---- the per-step scatter draw ----
  EnsCentersViewer.prototype._show = function (i) {
    if (!this.frames.length) return;
    var n = this.frames.length;
    this.idx = ((i % n) + n) % n;
    var ctx = this.ctx;
    ctx.clearRect(0, 0, this.W, this.H);
    ctx.drawImage(this.basemapCanvas, 0, 0);

    var centers = this.frames[this.idx];
    // bucket by bin so each color is one fill pass, drawn deepest-last (on top)
    var buckets = {}; var bo;
    for (bo = 0; bo < BIN_ORDER.length; bo++) buckets[BIN_ORDER[bo]] = [];
    for (var k = 0; k < centers.length; k++) buckets[binKey(centers[k][2])].push(centers[k]);

    // bold HOLLOW rings (stroke, no fill), deepest bin drawn last (on top)
    ctx.globalAlpha = RING_ALPHA;
    ctx.lineWidth = this.ringLW;
    var r = this.ringR;
    for (bo = 0; bo < BIN_ORDER.length; bo++) {
      var key = BIN_ORDER[bo];
      var pts = buckets[key];
      if (!pts.length) continue;
      ctx.strokeStyle = PRESSURE_BIN_COLORS[key] || '#ffffff';
      ctx.beginPath();
      for (var j = 0; j < pts.length; j++) {
        var p = this._project(pts[j][1], pts[j][0]);
        ctx.moveTo(p[0] + r, p[1]);
        ctx.arc(p[0], p[1], r, 0, 6.2832);
      }
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    // readout + scrub + frame counts
    var stepH = this.steps[this.idx];
    this.dom.fhour.textContent = 'F' + String(stepH).padStart(3, '0');
    this.dom.valid.textContent = validLabel(this.initMs, stepH) + '  ·  ' + fmtInt(centers.length) + ' centers';
    if (String(this.dom.scrub.value) !== String(this.idx)) this.dom.scrub.value = this.idx;
  };

  // ---- legend + peak table ----
  EnsCentersViewer.prototype._buildLegend = function (bins) {
    var host = this.dom.legend; if (!host) return;
    host.innerHTML = '';
    for (var i = 0; i < bins.length; i++) {
      var b = bins[i];
      var row = document.createElement('span');
      row.className = 'ens-leg';
      var sw = document.createElement('i');
      sw.className = 'ens-sw';
      sw.style.borderColor = PRESSURE_BIN_COLORS[b.key] || '#fff';
      var lab = document.createElement('span');
      lab.textContent = b.label;
      row.appendChild(sw); row.appendChild(lab);
      host.appendChild(row);
    }
  };

  EnsCentersViewer.prototype._buildPeaks = function (members) {
    var host = this.dom.peaks; if (!host) return;
    var rows = members.filter(function (m) { return m.peak; })
      .map(function (m) { return { id: m.id, label: m.id, peak: m.peak }; })
      .sort(function (a, b) { return a.peak.mslp_hpa - b.peak.mslp_hpa; });
    var html = '<div class="ens-peaks-head"><span>Member</span><span>Pmin</span><span>Vmax</span></div>';
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var cls = 'ens-peak-row' + (r.id === 'CTL' ? ' ctl' : '');
      var sw = '<i class="ens-sw" style="border-color:' + (PRESSURE_BIN_COLORS[binKey(Number(r.peak.mslp_hpa))] || '#fff') + '"></i>';
      html += '<div class="' + cls + '">' +
        '<span>' + sw + r.label + '</span>' +
        '<span>' + fix0(r.peak.mslp_hpa) + '</span>' +
        '<span>' + fix0(r.peak.vmax_kt) + '</span></div>';
    }
    host.innerHTML = html;
  };

  // ---- segmented toggle (HAFS _buildToggle/_highlight, verbatim shape) ----
  EnsCentersViewer.prototype._buildToggle = function (container, defs, active, onPick) {
    if (!container) return;
    container.innerHTML = '';
    for (var i = 0; i < defs.length; i++) {
      (function (def) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'hafs-seg' + (def.slug === active ? ' active' : '');
        b.textContent = def.label;
        b.setAttribute('data-slug', def.slug);
        b.addEventListener('click', function () { onPick(def.slug); });
        container.appendChild(b);
      })(defs[i]);
    }
    container.parentNode.style.display = defs.length > 1 ? '' : 'none';
  };

  EnsCentersViewer.prototype._highlight = function (container, slug) {
    if (!container) return;
    var btns = container.querySelectorAll('.hafs-seg');
    for (var i = 0; i < btns.length; i++)
      btns[i].classList.toggle('active', btns[i].getAttribute('data-slug') === slug);
  };

  // ---- transport (HAFS single-rAF stepper; no decode gate) ----
  EnsCentersViewer.prototype._frameMs = function () { return 1000 / (BASE_FPS * (this.speed || 1)); };

  EnsCentersViewer.prototype._tick = function (ts) {
    if (!this.playing) return;
    if (ts - this.lastTick >= this._frameMs()) {
      this.lastTick = ts;
      this._show(this.idx + 1);
    }
    var self = this;
    this.raf = requestAnimationFrame(function (t) { self._tick(t); });
  };

  EnsCentersViewer.prototype._play = function () {
    if (this.frames.length <= 1) return;
    this.playing = true;
    this.dom.play.textContent = '❚❚ Pause';
    this.lastTick = 0;
    if (this.raf) cancelAnimationFrame(this.raf);
    var self = this;
    this.raf = requestAnimationFrame(function (t) { self._tick(t); });
  };

  EnsCentersViewer.prototype._pause = function () {
    this.playing = false;
    if (this.dom.play) this.dom.play.textContent = '► Play';
    if (this.raf) { cancelAnimationFrame(this.raf); this.raf = null; }
  };

  EnsCentersViewer.prototype._togglePlay = function () { this.playing ? this._pause() : this._play(); };
  EnsCentersViewer.prototype._step = function (delta) { if (this.frames.length) this._show(this.idx + delta); };

  EnsCentersViewer.prototype._wire = function () {
    var self = this;
    this.dom.play.addEventListener('click', function () { self._togglePlay(); });
    this.dom.stepB.addEventListener('click', function () { self._pause(); self._step(-1); });
    this.dom.stepF.addEventListener('click', function () { self._pause(); self._step(1); });

    var sp = this.dom.speed;
    for (var i = 0; i < SPEED_OPTIONS.length; i++) {
      var o = document.createElement('option');
      o.value = SPEED_OPTIONS[i]; o.textContent = SPEED_OPTIONS[i] + '×';
      if (SPEED_OPTIONS[i] === 1) o.selected = true;
      sp.appendChild(o);
    }
    sp.addEventListener('change', function () {
      self.speed = parseFloat(this.value);
      if (self.playing) self.lastTick = 0;  // applies next tick; do NOT start a 2nd loop
    });

    this.dom.scrub.addEventListener('input', function () {
      self._pause();
      self._show(parseInt(this.value, 10) || 0);
    });

    this.root.addEventListener('keydown', function (e) {
      var tag = e.target && e.target.tagName;
      if (tag === 'SELECT' || tag === 'INPUT' || tag === 'BUTTON') return;
      if (e.key === 'ArrowLeft') { self._pause(); self._step(-1); e.preventDefault(); }
      else if (e.key === 'ArrowRight') { self._pause(); self._step(1); e.preventDefault(); }
      else if (e.key === ' ' || e.key === 'Spacebar') { self._togglePlay(); e.preventDefault(); }
    });

    // hover tooltip: nearest center in the current frame
    this.dom.canvas.addEventListener('mousemove', function (ev) { self._hover(ev); });
    this.dom.canvas.addEventListener('mouseleave', function () { if (self.dom.tooltip) self.dom.tooltip.style.display = 'none'; });

    if (window.ResizeObserver) {
      this._ro = new ResizeObserver(function () { self._resizeDebounced(); });
      this._ro.observe(this.dom.stage);
    } else {
      window.addEventListener('resize', function () { self._resizeDebounced(); });
    }
  };

  EnsCentersViewer.prototype._resizeDebounced = function () {
    var self = this;
    clearTimeout(this._rt);
    this._rt = setTimeout(function () { if (self.frames.length) self._resize(); }, 120);
  };

  EnsCentersViewer.prototype._hover = function (ev) {
    var tip = this.dom.tooltip;
    if (!tip || !this.frames.length) return;
    var rect = this.dom.canvas.getBoundingClientRect();
    var dpr = this.W / rect.width;
    var mx = (ev.clientX - rect.left) * dpr, my = (ev.clientY - rect.top) * dpr;
    var centers = this.frames[this.idx];
    var best = null, bestD = (12 * dpr) * (12 * dpr);
    for (var k = 0; k < centers.length; k++) {
      var p = this._project(centers[k][1], centers[k][0]);
      var dx = p[0] - mx, dy = p[1] - my, d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = centers[k]; }
    }
    if (!best) { tip.style.display = 'none'; return; }
    tip.style.display = 'block';
    tip.style.left = (ev.clientX - rect.left + 12) + 'px';
    tip.style.top = (ev.clientY - rect.top + 12) + 'px';
    tip.innerHTML = fix0(best[2]) + ' hPa  ·  ' + fix0(best[3]) + ' kt<br>' +
      Math.abs(best[0]).toFixed(1) + (best[0] >= 0 ? 'N' : 'S') + '  ' +
      Math.abs(best[1]).toFixed(1) + (best[1] >= 0 ? 'E' : 'W');
  };

  // ---- poll for a newer cycle ----
  EnsCentersViewer.prototype._schedulePoll = function () {
    clearTimeout(this._pollTimer);
    var self = this;
    this._pollTimer = setTimeout(function () { self._poll(); }, POLL_IDLE_MS);
  };

  EnsCentersViewer.prototype._poll = function () {
    var self = this;
    this._fetchManifest().then(function (m) {
      self.manifest = m;
      var entry = self._modelEntry(self.model);
      if (entry && entry.latest && entry.latest !== self.loadedCycle) {
        self._loadCycle(self.model, entry.latest);
      }
    }).catch(function () { }).then(function () { self._schedulePoll(); });
  };

  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('DOMContentLoaded', function () {
      var root = el('enscenters-viewer');
      if (root) new EnsCentersViewer(root);
    });
  }

  if (typeof window !== 'undefined') window.EnsCentersViewer = EnsCentersViewer;
})();
