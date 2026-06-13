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

  // The map extent is now per-REGION (TATRegions.extentOf), not a fixed global
  // box. Detection stays fully global; the region is a client-side view crop.
  var DEFAULT_REGION = 'atlantic';   // Andrew's default
  var LS_REGION = 'ens.region';      // remember the user's last pick

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

  // Basemap style (Andrew's reference, FINAL) passed to the shared renderer.
  var BASEMAP_STYLE = {
    ocean: '#07101c', land: '#2f3f59',
    coast: 'rgba(150,175,205,0.28)', coastLw: 0.6,
    grid: 'rgba(255,255,255,0.05)', gridLw: 0.5
  };

  var RING_ALPHA = 0.92;    // crisp but overlapping rings still read as density
  var BIN_ORDER = ['gt1000', 'p990_1000', 'p970_990', 'p950_970', 'lt950'];

  function regionOr(key) {
    return (window.TATRegions && TATRegions.get(key)) ? key : DEFAULT_REGION;
  }

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
      empty: el('enscenters-empty'),
      regionBtn: el('enscenters-region-btn'),
      regionLabel: el('enscenters-region-label')
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
    this.visible = [];    // region-filtered centers of the current frame (for hover)

    // region: remembered pick or Atlantic default
    var saved = null;
    try { saved = localStorage.getItem(LS_REGION); } catch (e) { /* ignore */ }
    this.region = regionOr(saved || DEFAULT_REGION);
    this.extent = (window.TATRegions ? TATRegions.extentOf(TATRegions.get(this.region)) : [0, 360, -90, 90]);
    this.picker = null;

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
        self._initRegion();        // geo is ready -> build the region picker
        self._onManifest(res[1]);
      })
      .catch(function (e) {
        console.warn('enscenters: boot failed', e);
        self._status('');
        self._showEmpty(true);
      });
  };

  // ---- region (shared TATRegions layer) ----
  EnsCentersViewer.prototype._initRegion = function () {
    var self = this;
    var r = window.TATRegions ? TATRegions.get(this.region) : null;
    if (this.dom.regionLabel && r) this.dom.regionLabel.textContent = r.label;
    if (window.TATRegions) {
      this.picker = new TATRegions.RegionPicker({
        geo: this.geo, current: this.region,
        onPick: function (key) { self._selectRegion(key); }
      });
    }
  };

  EnsCentersViewer.prototype._selectRegion = function (key) {
    if (!window.TATRegions) return;
    var r = TATRegions.get(key); if (!r) return;
    this.region = key;
    this.extent = TATRegions.extentOf(r);
    try { localStorage.setItem(LS_REGION, key); } catch (e) { /* ignore */ }
    if (this.dom.regionLabel) this.dom.regionLabel.textContent = r.label;
    if (this.picker) this.picker.setCurrent(key);
    if (this.data) this._buildRegionPeaks();
    this._resize();   // redraw basemap at the new extent + re-show (filters scatter)
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
    this._buildRegionPeaks();
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

  // ---- canvas sizing + basemap (shared TATRegions projection/basemap) ----
  EnsCentersViewer.prototype._resize = function () {
    var stage = this.dom.stage;
    var cssW = stage.clientWidth || 900;
    var e = this.extent;
    var aspect = (e[1] - e[0]) / (e[3] - e[2]);   // per-region aspect
    var cssH = cssW / aspect;
    // cap height for tall/portrait regions so the canvas stays in the viewport;
    // shrink width to preserve aspect.
    var maxH = Math.min(660, (window.innerHeight || 800) * 0.72);
    if (cssH > maxH) { cssH = maxH; cssW = Math.round(cssH * aspect); }
    cssW = Math.round(cssW); cssH = Math.round(cssH);
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
    return TATRegions.project(lon, lat, this.extent, this.W, this.H);
  };

  EnsCentersViewer.prototype._drawBasemap = function () {
    var bg = this.basemapCanvas;
    bg.width = this.W; bg.height = this.H;
    TATRegions.drawBasemap(bg.getContext('2d'), this.extent, this.geo, this.W, this.H, BASEMAP_STYLE);
  };

  // ---- the per-step scatter draw ----
  EnsCentersViewer.prototype._show = function (i) {
    if (!this.frames.length) return;
    var n = this.frames.length;
    this.idx = ((i % n) + n) % n;
    var ctx = this.ctx;
    ctx.clearRect(0, 0, this.W, this.H);
    ctx.drawImage(this.basemapCanvas, 0, 0);

    // region crop: keep only centers inside the selected region (frame center
    // is [lat, lon, mslp, vmax]). The visible set also drives hover.
    var all = this.frames[this.idx];
    var r = TATRegions.get(this.region);
    var centers = [];
    for (var f = 0; f < all.length; f++) {
      if (TATRegions.inRegion(all[f][1], all[f][0], r)) centers.push(all[f]);
    }
    this.visible = centers;

    // bucket by bin so each color is one stroke pass, drawn deepest-last (on top)
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

  // Per-member peak recomputed for the SELECTED region: each member's deepest
  // center inside the region box, across all forecast steps.
  EnsCentersViewer.prototype._buildRegionPeaks = function () {
    var host = this.dom.peaks; if (!host || !this.data) return;
    var r = TATRegions.get(this.region);
    var members = this.data.members || [];
    var rows = [];
    for (var i = 0; i < members.length; i++) {
      var cs = members[i].centers || [], best = null;
      for (var k = 0; k < cs.length; k++) {
        var c = cs[k];   // [step_h, lat, lon, mslp, vmax]
        if (!TATRegions.inRegion(c[2], c[1], r)) continue;
        if (best === null || c[3] < best.mslp) best = { mslp: c[3], vmax: c[4] };
      }
      if (best) rows.push({ id: members[i].id, best: best });
    }
    rows.sort(function (a, b) { return a.best.mslp - b.best.mslp; });
    var html = '<div class="ens-peaks-title">Peak in ' + r.label + '</div>' +
      '<div class="ens-peaks-head"><span>Member</span><span>Pmin</span><span>Vmax</span></div>';
    for (var x = 0; x < rows.length; x++) {
      var row = rows[x];
      var cls = 'ens-peak-row' + (row.id === 'CTL' ? ' ctl' : '');
      var sw = '<i class="ens-sw" style="border-color:' + (PRESSURE_BIN_COLORS[binKey(row.best.mslp)] || '#fff') + '"></i>';
      html += '<div class="' + cls + '">' +
        '<span>' + sw + row.id + '</span>' +
        '<span>' + fix0(row.best.mslp) + '</span>' +
        '<span>' + fix0(row.best.vmax) + '</span></div>';
    }
    if (!rows.length) html += '<div class="ens-peaks-empty">No centers in this region</div>';
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
    if (this.dom.regionBtn) {
      this.dom.regionBtn.addEventListener('click', function () { if (self.picker) self.picker.open(); });
    }
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
    var centers = this.visible || [];   // region-filtered (matches what is drawn)
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
