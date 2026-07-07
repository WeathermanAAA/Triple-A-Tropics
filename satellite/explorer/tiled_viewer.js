/* Tiled satellite explorer — MapLibre GL viewer over the Stage-2 shadow tile
 * pyramid (SATELLITE-REARCH §4.1/§5.5/§6). SHADOW-FIRST: a standalone, unlinked
 * page that reads the manifest SSOT and renders the webmercator WebP pyramid as a
 * RasterTileSource, with the TAT vector chrome (ne_10m coast + ne_50m borders as
 * toggleable white line layers, a graticule, header/colorbar/branding) laid on
 * top. The imagery tiles are chrome-free; ALL furniture is vector/HTML overlay.
 *
 * Manifest-SSOT: it fetches latest_times.json and derives every tile URL from the
 * `tile` template + `times` — it NEVER lists the bucket. Region picker (presets +
 * draw-a-box) resolves to a VIEWPORT rect over the pyramid — never an on-demand
 * server render (the report's anti-pattern). Reuses window.TATRegions (the shared
 * region component: basin presets + the ne_* geojson loader).
 *
 * Frozen renderer / zero-visual-change: pixels come from the pyramid the emitter
 * cut from the frozen render; this file only pans/zooms/loops + draws furniture.
 */
(function () {
  'use strict';

  var DEFAULT_MANIFEST =
    'https://cdn.triple-a-tropics.com/shadow/sat/goes19/conus/ir/latest_times.json';

  // ---- minimal dark base style (no external basemap; imagery IS the base) ----
  function darkStyle() {
    return {
      version: 8,
      sources: {},
      layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#0a0d12' } }],
      // MapLibre needs a glyphs endpoint only if we render text; graticule labels
      // use HTML markers instead, so none is declared (keeps it self-contained).
    };
  }

  // ---- graticule as GeoJSON (lon/lat grid lines) ----
  function graticule(step) {
    var f = [];
    for (var lon = -180; lon <= 180; lon += step) {
      var line = [];
      for (var lat = -85; lat <= 85; lat += 5) line.push([lon, lat]);
      f.push({ type: 'Feature', properties: { kind: 'meridian', deg: lon },
               geometry: { type: 'LineString', coordinates: line } });
    }
    for (var la = -80; la <= 80; la += step) {
      var l2 = [];
      for (var ln = -180; ln <= 180; ln += 5) l2.push([ln, la]);
      f.push({ type: 'Feature', properties: { kind: 'parallel', deg: la },
               geometry: { type: 'LineString', coordinates: l2 } });
    }
    return { type: 'FeatureCollection', features: f };
  }

  // ---- manifest -> tile-URL derivation (SSOT; never list the bucket) ----
  function manifestBase(manifestUrl, product) {
    // manifestUrl = <base>/<product>/latest_times.json  ->  <base>/
    var tail = product + '/latest_times.json';
    return manifestUrl.slice(0, manifestUrl.length - tail.length);
  }
  function frameTiles(base, tileTemplate, stamp) {
    // product-relative "sat/.../{t}/{z}/{x}/{y}.webp" -> absolute XYZ template
    return base + tileTemplate.replace('{t}', stamp);
  }

  // ===========================================================================
  function TiledViewer(opts) {
    this.el = opts.container;
    this.manifestUrl = opts.manifest || DEFAULT_MANIFEST;
    this.onStatus = opts.onStatus || function () {};
    this.map = null;
    this.manifest = null;
    this.frames = [];         // stamps, sorted oldest->newest
    this.frameIdx = 0;
    this.playing = false;
    this._raf = null;
    this._last = 0;
    this.fps = opts.fps || 6;        // fixed-timestep loop cadence
    this.dwellNewest = 6;            // extra dwell on the latest frame
    this._added = {};                // stamp -> true once its raster source exists
    this._window = opts.window || 12; // max concurrent frame sources (texture bound)
    this.showLayers = { coast: true, borders: true, states: true, grid: true };
    this._geo = null;
  }
  var VP = TiledViewer.prototype;

  VP.boot = function () {
    var self = this;
    return fetch(this.manifestUrl, { cache: 'no-cache' })
      .then(function (r) { if (!r.ok) throw new Error('manifest ' + r.status); return r.json(); })
      .then(function (m) { self.manifest = m; self._initMap(); })
      .catch(function (e) { self.onStatus('error', 'Could not load manifest: ' + e.message); });
  };

  VP._initMap = function () {
    var self = this, m = this.manifest;
    this.base = manifestBase(this.manifestUrl, m.product);
    this.frames = (m.times || []).slice();
    this.frameIdx = Math.max(0, this.frames.length - 1);

    var b = m.bounds; // [W,S,E,N]
    this.map = new maplibregl.Map({
      container: this.el, style: darkStyle(),
      bounds: b ? [[b[0], b[1]], [b[2], b[3]]] : [[-160, -60], [10, 60]],
      fitBoundsOptions: { padding: 24 },
      minZoom: (m.minzoom || 0), maxZoom: (m.maxzoom || 5) + 2,   // allow slight over-zoom
      renderWorldCopies: false, attributionControl: false, cooperativeGestures: true,
      preserveDrawingBuffer: true
    });
    this.map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
    this.map.dragRotate.disable();
    this.map.touchZoomRotate.disableRotation();
    // Our shift-drag AOI owns the shift+drag gesture; disable MapLibre's built-in
    // box-zoom so the two don't both fire (double camera move) on shift-drag.
    this.map.boxZoom.disable();

    this.map.on('load', function () { self._onLoad(); });
  };

  VP._onLoad = function () {
    var self = this, m = this.manifest;
    // 1) the imagery: the latest frame's raster pyramid, first (under furniture)
    if (this.frames.length) this._ensureFrame(this.frames[this.frameIdx], 1);

    // 2) graticule (below coast so land lines read on top)
    this.map.addSource('grat', { type: 'geojson', data: graticule(10) });
    this.map.addLayer({ id: 'grat', type: 'line', source: 'grat',
      paint: { 'line-color': '#ffffff', 'line-opacity': 0.14, 'line-width': 0.6 } });

    // 3) ne_* vector furniture (white, CycloLab canon) via the shared loader.
    var loader = (window.TATRegions && window.TATRegions.loadGeo)
      ? window.TATRegions.loadGeo({})
      : Promise.resolve(null);
    loader.then(function (geo) { self._addFurniture(geo); }).catch(function () {});

    this.onStatus('ready', m.latest);
    this._updateReadout();
  };

  VP._addFurniture = function (geo) {
    if (!geo) return;
    var map = this.map;
    // white coastline + borders on top of imagery -- razor-sharp at every zoom
    // (vector), each a toggleable layer. A faint dark casing keeps white legible
    // over both bright cloud-IR and dark ocean-IR.
    if (geo.coast) {
      map.addSource('coast', { type: 'geojson', data: geo.coast });
      map.addLayer({ id: 'coast-case', type: 'line', source: 'coast',
        paint: { 'line-color': '#000000', 'line-opacity': 0.45,
                 'line-width': ['interpolate', ['linear'], ['zoom'], 0, 1.4, 4, 2.0, 8, 3.0] } });
      map.addLayer({ id: 'coast', type: 'line', source: 'coast',
        paint: { 'line-color': '#ffffff', 'line-opacity': 0.9,
                 'line-width': ['interpolate', ['linear'], ['zoom'], 0, 0.5, 4, 0.9, 8, 1.4] } });
    }
    if (geo.countries) {
      map.addSource('adm0', { type: 'geojson', data: geo.countries });
      map.addLayer({ id: 'borders', type: 'line', source: 'adm0',
        paint: { 'line-color': '#ffffff', 'line-opacity': 0.55,
                 'line-width': ['interpolate', ['linear'], ['zoom'], 0, 0.4, 4, 0.7, 8, 1.1] } });
    }
    if (geo.states) {
      map.addSource('adm1', { type: 'geojson', data: geo.states });
      map.addLayer({ id: 'states', type: 'line', source: 'adm1', minzoom: 3,
        paint: { 'line-color': '#ffffff', 'line-opacity': 0.28,
                 'line-width': ['interpolate', ['linear'], ['zoom'], 3, 0.3, 8, 0.8] } });
    }
    this._geo = geo;
    this._applyLayerVis();
  };

  // ---- frame raster sources (§6.1: per-frame source + opacity toggle) ----
  VP._srcId = function (stamp) { return 'ir-' + stamp; };

  VP._ensureFrame = function (stamp, opacity) {
    if (this._added[stamp]) {
      this.map.setPaintProperty(this._srcId(stamp), 'raster-opacity', opacity);
      this.map.setLayoutProperty(this._srcId(stamp), 'visibility', 'visible');
      return;
    }
    var m = this.manifest;
    var url = frameTiles(this.base, m.tile, stamp);
    var sid = this._srcId(stamp);
    this.map.addSource(sid, {
      type: 'raster', tiles: [url], tileSize: m.tile_size || 512,
      minzoom: m.minzoom || 0, maxzoom: m.maxzoom || 5,
      bounds: m.bounds || undefined, scheme: 'xyz'
    });
    // insert imagery BELOW the first furniture layer (grat/coast) if present
    var before = this.map.getLayer('grat') ? 'grat' : undefined;
    this.map.addLayer({ id: sid, type: 'raster', source: sid,
      paint: { 'raster-opacity': opacity, 'raster-fade-duration': 0,
               'raster-resampling': 'linear' } }, before);
    this._added[stamp] = true;
    this._evictBeyondWindow(stamp);
  };

  VP._evictBeyondWindow = function (keepStamp) {
    // bound texture residency: hide (visibility:none) frames outside a sliding
    // window around the current index; MapLibre keeps them cheap but non-drawing.
    var keepSet = {};
    var lo = Math.max(0, this.frameIdx - this._window);
    for (var i = lo; i <= this.frameIdx; i++) keepSet[this.frames[i]] = 1;
    keepSet[keepStamp] = 1;
    for (var s in this._added) if (this._added[s] && !keepSet[s]) {
      if (this.map.getLayer(this._srcId(s)))
        this.map.setLayoutProperty(this._srcId(s), 'visibility', 'none');
    }
  };

  VP.showFrame = function (idx) {
    if (!this.frames.length) return;
    idx = (idx + this.frames.length) % this.frames.length;
    var stamp = this.frames[idx], sid = this._srcId(stamp), self = this;
    // Add/show the new frame ON TOP at full opacity, but HOLD the prior frame(s)
    // opaque underneath until the new source's tiles are actually loaded -- else
    // an uncached frame flashes the dark background (raster-fade-duration:0). The
    // new layer renders transparent (prior shows through) until its tiles land.
    this._ensureFrame(stamp, 1);
    var reveal = function () {
      for (var i = 0; i < self.frames.length; i++) {
        var s = self.frames[i];
        if (s !== stamp && self._added[s] && self.map.getLayer(self._srcId(s)))
          self.map.setPaintProperty(self._srcId(s), 'raster-opacity', 0);
      }
      self.frameIdx = idx;
      self._evictBeyondWindow(stamp);   // bound texture residency on EVERY advance
      self._updateReadout();
    };
    if (this.map.isSourceLoaded(sid)) { reveal(); return; }
    var onData = function (e) {
      if (e.sourceId === sid && self.map.isSourceLoaded(sid)) {
        self.map.off('sourcedata', onData); reveal();
      }
    };
    this.map.on('sourcedata', onData);
  };

  // ---- fixed-timestep loop playback ----
  VP.play = function () {
    if (this.playing || this.frames.length < 2) return;
    this.playing = true; this._last = 0; this._dwell = 0;
    var self = this;
    var step = function (t) {
      if (!self.playing) return;
      if (!self._last) self._last = t;
      var interval = 1000 / self.fps;
      var atNewest = self.frameIdx === self.frames.length - 1;
      if (atNewest) interval *= self.dwellNewest;   // 6x dwell on the latest frame
      if (t - self._last >= interval) {
        self._last = t;
        self.showFrame(self.frameIdx + 1);
      }
      self._raf = requestAnimationFrame(step);
    };
    this._raf = requestAnimationFrame(step);
    this.onStatus('play');
  };
  VP.pause = function () {
    this.playing = false;
    if (this._raf) cancelAnimationFrame(this._raf);
    this.onStatus('pause');
  };
  VP.toggle = function () { this.playing ? this.pause() : this.play(); };

  // ---- layer toggles ----
  VP.setLayer = function (key, on) { this.showLayers[key] = on; this._applyLayerVis(); };
  VP._applyLayerVis = function () {
    var v = this.showLayers, map = this.map;
    var set = function (id, on) { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none'); };
    set('coast', v.coast); set('coast-case', v.coast);
    set('borders', v.borders); set('states', v.states); set('grat', v.grid);
  };

  // ---- region picker: presets (TATRegions) + draw-a-box AOI. BOTH resolve to a
  // camera move over the pyramid -- NEVER an on-demand render. ----
  VP.gotoRegion = function (key) {
    var r = window.TATRegions && window.TATRegions.get(key);
    if (!r) return;
    // regions.js uses {w,e,s,n}; MapLibre wants [[W,S],[E,N]]. A region that
    // crosses the antimeridian (e < w) frames past +180, which only renders with
    // world copies on -- enable them just for that case (CONUS never crosses).
    var w = r.w, e = r.e;
    if (e < w) { e += 360; this.map.setRenderWorldCopies(true); }
    this.map.fitBounds([[w, r.s], [e, r.n]], { padding: 20, duration: 500 });
  };
  VP.fitData = function () {
    var b = this.manifest.bounds;
    if (b) this.map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 24, duration: 500 });
  };

  // A drag-rectangle AOI over the map -> fitBounds. Reusable across MapLibre
  // viewers (satellite/models/TAW). Shift+drag to draw (so plain drag still pans).
  VP.enableDrawBox = function (buttonEl) {
    var self = this, map = this.map, canvas = map.getCanvasContainer();
    var start = null, box = null, active = false;
    function mousePos(e) {
      var rect = canvas.getBoundingClientRect();
      return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    }
    function onDown(e) {
      if (!active || !(e.shiftKey || self._armed)) return;
      e.preventDefault(); map.dragPan.disable();
      start = mousePos(e);
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    }
    function onMove(e) {
      var cur = mousePos(e);
      if (!box) { box = document.createElement('div'); box.className = 'tv-drawbox'; canvas.appendChild(box); }
      var minX = Math.min(start.x, cur.x), maxX = Math.max(start.x, cur.x);
      var minY = Math.min(start.y, cur.y), maxY = Math.max(start.y, cur.y);
      box.style.transform = 'translate(' + minX + 'px,' + minY + 'px)';
      box.style.width = (maxX - minX) + 'px'; box.style.height = (maxY - minY) + 'px';
    }
    function onUp(e) {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      map.dragPan.enable();
      var end = mousePos(e);
      if (box) { box.parentNode.removeChild(box); box = null; }
      self._armed = false; if (buttonEl) buttonEl.classList.remove('on');
      var dx = Math.abs(end.x - start.x), dy = Math.abs(end.y - start.y);
      if (dx < 8 || dy < 8) return;   // too small -> ignore
      var p1 = map.unproject([start.x, start.y]), p2 = map.unproject([end.x, end.y]);
      var w = Math.min(p1.lng, p2.lng), e2 = Math.max(p1.lng, p2.lng);
      var s = Math.min(p1.lat, p2.lat), n = Math.max(p1.lat, p2.lat);
      map.fitBounds([[w, s], [e2, n]], { padding: 10, duration: 400 });  // viewport rect, no render
    }
    active = true;
    canvas.addEventListener('mousedown', onDown, true);
    if (buttonEl) buttonEl.addEventListener('click', function () {
      self._armed = !self._armed; buttonEl.classList.toggle('on', self._armed);
    });
  };

  VP._updateReadout = function () {
    var stamp = this.frames[this.frameIdx] || (this.manifest && this.manifest.latest);
    if (stamp) this.onStatus('frame', { stamp: stamp, idx: this.frameIdx, n: this.frames.length });
  };

  if (typeof window !== 'undefined') window.TiledViewer = TiledViewer;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { TiledViewer: TiledViewer,
      _test: { manifestBase: manifestBase, frameTiles: frameTiles, graticule: graticule } };
  }
})();
