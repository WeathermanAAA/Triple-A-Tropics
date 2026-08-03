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
 *
 * PLAYBACK CONTRACT (the no-strobe rules -- keep all five):
 *   1. BOUNDED-LOOP RESIDENCY. The playback loop is the manifest's trailing
 *      `loopCap` stamps (default 48; the cockpit scales it down per pane so a
 *      4-pane compare never mounts 4x90 raster sources -- the 10-min backfill
 *      grew manifests to 90+ frames and unbounded residency turned every
 *      camera move into a 90-source tile-fetch storm that starved the visible
 *      frame: seconds of partial-dark that reads as a strobe). Every in-loop
 *      frame keeps its raster source MOUNTED for the life of the loop;
 *      sources are only dropped when a stamp leaves the loop window or the
 *      product is torn down.
 *   2. PRELOAD BEFORE PLAY. _preloadLoop mounts the whole loop (staggered, in
 *      playback order from the current frame) and reports progress via
 *      onStatus('loading', {done,total}) ... onStatus('loaded'). The page shows
 *      a real "loading loop N/M" state instead of a dark map. Background
 *      fills (manifest refresh, camera resume, cap changes) run QUIET -- no
 *      toast churn mid-session.
 *   3. REVEAL ONLY DECODED FRAMES. showFrame never exposes a frame whose tiles
 *      aren't fully loaded at the current camera: the prior frame HOLDS opaque
 *      until the persistent sourcedata/idle handlers confirm the target source
 *      (readiness is event-confirmed, never inferred from a hidden source), and
 *      a request token (_wantStamp) kills out-of-order reveals from stale
 *      scrubs. The dark basemap can never flash through mid-loop.
 *   4. CAMERA FETCH DISCIPLINE. During a camera move every resident frame
 *      except the on-screen one is PARKED (visibility:none, readiness
 *      revoked) so the visible frame owns the bandwidth; parked frames
 *      resume STAGGERED after the camera rests and must re-confirm their
 *      tiles through the same event gate before any reveal. Parking is the
 *      one sanctioned use of hiding -- it always revokes _ready with it
 *      (a hidden source's isSourceLoaded() is a lie; trusting it was the
 *      original strobe).
 *   5. LIVE MANIFESTS MERGE IN PLACE. A background refresh (90 s) picks up
 *      emitter backfill/densification: the merge preserves the CURRENT
 *      stamp (never remaps frameIdx under a playing loop), drops rolled-off
 *      sources, and preloads new frames quietly.
 */
(function () {
  'use strict';

  var DEFAULT_MANIFEST =
    'https://cdn.triple-a-tropics.com/shadow/sat/goes19/conus/ir/latest_times.json';

  // ---- device perf profile (the GEO-ring lag/crash + blurry-IR fixes) ----
  // Two coupled knobs, derived once:
  //   hiDpi -> declare the 512-px tiles at HALF size so MapLibre pulls one
  //     pyramid level deeper and a device pixel gets a source pixel (the
  //     §4.2 "a 512 CSS tile = an @2x asset" intent; the emitter ships
  //     512-px tiles, so the declaration is where the @2x happens). Costs
  //     4x the tile fetches + textures, hence the cap interplay below.
  //   lowMem -> smaller loop caps and NO @2x: a world-spanning frame stack
  //     on an integrated GPU is exactly what blew the WebGL context for
  //     testers. Stability beats crisp. A live context loss flips the
  //     profile to lowMem for the rest of the session (see _initMap).
  var PERF = (function () {
    var dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1;
    var mem = (typeof navigator !== 'undefined' && navigator.deviceMemory) || 8;
    var lowMem = mem <= 4;
    return { hiDpi: dpr > 1.5 && !lowMem, lowMem: lowMem };
  })();

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

  // ---- container frames (s2 tar blocks + byte-range reads; the hafs #27 ----
  // pattern, emit side in tsr s2_container.py). A frame may publish as a few
  // zoom-banded USTAR blocks + a tiles.z{N}.json byte-offset index instead of
  // one object per tile (~305 -> ~6 Class A writes per full-disk frame, the
  // 2026-08 R2 cost incident). EVERY frame's tiles route through the 's2c'
  // protocol; the handler resolves per stamp: index present -> ONE ranged
  // fetch per tile (exact bytes, same WebP the per-tile path would return;
  // CDN caches the block after first touch so a viewport is edge-ranges);
  // index 404 -> the legacy per-tile object, byte-for-byte the old behavior.
  // No worker, no server: the same maplibregl.addProtocol machinery ir3d's
  // 'tatdem' DEM synth already relies on. Index fetches dedupe in-flight and
  // cache per stamp URL (immutable content).
  var S2C = 's2c://';
  // Index cache: stampDir|mz -> Promise<index|null>. null is cached ONLY for
  // a definitive 404/403 (a genuinely legacy frame); transient failures
  // (network, 5xx) clear the entry so the NEXT tile retries, and the failing
  // request itself falls back to the legacy per-tile object -- a hiccup can
  // cost one fallback fetch, never a permanently blank frame (review finding
  // 2026-08-03). LRU-capped: stamps roll off the loop constantly and a
  // long-lived cockpit tab must not grow this without bound.
  var s2cIndexCache = new Map();
  var S2C_INDEX_CACHE_MAX = 240;
  // 1x1 transparent PNG, canvas-encoded (the ir3d pngBytes technique -- a
  // hand-typed base64 here shipped a tile the decoder rejected, which turned
  // every skip_empty tile into an ERROR and stalled the readiness-gated
  // preload). A tile absent from the index was skip_empty at the cutter:
  // render transparent, exactly the missing-object slippy contract. A failed
  // encode clears the memo so the next tile retries instead of erroring
  // forever.
  var s2cEmptyP = null;
  function s2cEmptyTile() {
    if (!s2cEmptyP) {
      s2cEmptyP = new Promise(function (res, rej) {
        var c = document.createElement('canvas');
        c.width = 1; c.height = 1;
        c.getContext('2d');           // blank canvas = fully transparent
        c.toBlob(function (b) {
          if (!b) { rej(new Error('s2c: empty-tile encode failed')); return; }
          b.arrayBuffer().then(res, rej);
        }, 'image/png');
      });
      s2cEmptyP.catch(function () { s2cEmptyP = null; });
    }
    return s2cEmptyP.then(function (buf) { return buf.slice(0); });
  }
  function s2cIndex(dir, mz) {
    var key = dir + '|' + mz;
    var hit = s2cIndexCache.get(key);
    if (hit) return hit;
    var p = fetch(dir + '/tiles.z' + mz + '.json')
      .then(function (r) {
        if (r.status === 404 || r.status === 403) return null;   // legacy: cache
        if (!r.ok) throw new Error('s2c index HTTP ' + r.status);
        return r.json();
      })
      .catch(function (e) {
        s2cIndexCache.delete(key);      // transient: retry on the next tile
        throw e;
      });
    s2cIndexCache.set(key, p);
    if (s2cIndexCache.size > S2C_INDEX_CACHE_MAX) {
      var oldest = s2cIndexCache.keys().next().value;
      s2cIndexCache.delete(oldest);
    }
    return p;
  }
  function s2cLegacyFetch(dir, tile, signal) {
    return fetch(dir + '/' + tile, { signal: signal }).then(function (r) {
      if (r.status === 404 || r.status === 403) return s2cEmptyTile();
      if (!r.ok) throw new Error('s2c tile HTTP ' + r.status);
      return r.arrayBuffer();
    }).then(function (b) { return { data: b }; });
  }
  function s2cHandler(params, abortController) {
    // url: s2c://<stampDirUrl>|<maxzoom>|<hint>|<z>/<x>/<y><ext>
    // hint=1 -> the manifest says this product publishes containers, probe
    // the index; hint=0 -> pure legacy product, skip the probe entirely (no
    // per-frame 404 GET on never-flipped products -- review finding). The
    // hint is sticky through rollback on the emitter side, so container
    // frames stay readable after the flag is turned off.
    var signal = abortController && abortController.signal;
    var parts = params.url.slice(S2C.length).split('|');
    var dir = parts[0], mz = parts[1], hinted = parts[2] === '1', tile = parts[3];
    if (!hinted) return s2cLegacyFetch(dir, tile, signal);
    return s2cIndex(dir, mz).then(function (idx) {
      if (!idx || !idx.tiles) return s2cLegacyFetch(dir, tile, signal);
      var m = idx.tiles[tile];
      if (!m)                                       // skip_empty: transparent
        return s2cEmptyTile().then(function (b) { return { data: b }; });
      var off = m[1], len = m[2];
      return fetch(dir + '/' + m[0],
                   { signal: signal,
                     headers: { Range: 'bytes=' + off + '-' + (off + len - 1) } })
        .then(function (r) {
          if (r.status !== 206 && r.status !== 200)
            throw new Error('s2c range HTTP ' + r.status);
          return r.arrayBuffer().then(function (b) {
            // a 200 (range unsupported on some path) is the WHOLE block:
            // slice locally so the viewer still gets exactly its tile
            if (r.status === 200 && b.byteLength > len) b = b.slice(off, off + len);
            return { data: b };
          });
        });
    }, function () {
      // index fetch failed transiently: this tile takes the legacy path;
      // the cleared cache entry retries the index on a later tile
      return s2cLegacyFetch(dir, tile, signal);
    });
  }
  if (typeof maplibregl !== 'undefined' && maplibregl.addProtocol)
    maplibregl.addProtocol('s2c', s2cHandler);
  function s2cFrameTiles(base, tileTemplate, stamp, maxzoom, hinted) {
    var legacy = frameTiles(base, tileTemplate, stamp);
    var cut = legacy.indexOf('/{z}/');
    if (cut < 0 || typeof maplibregl === 'undefined' || !maplibregl.addProtocol)
      return legacy;                            // unknown shape: legacy direct
    return S2C + legacy.slice(0, cut) + '|' +
           (maxzoom == null ? 5 : maxzoom) + '|' + (hinted ? '1' : '0') + '|' +
           legacy.slice(cut + 1);
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
    this._ready = {};                // stamp -> true once its tiles are event-confirmed loaded
    this._parked = {};               // stamp -> true while camera-parked (hidden, readiness revoked)
    this._mountQ = [];               // stamps awaiting a (staggered) preload mount/resume
    this._wantStamp = null;          // reveal token: the stamp the LATEST showFrame wants
    this._wantIdx = 0;
    this._loadedEmitted = false;     // 'loaded' fires once per preload pass
    this._announce = true;           // false = background fill (no loading toasts)
    this.loopCap = opts.loopCap || 48;      // playback window (trailing stamps)
    this.refreshS = (opts.refreshS != null) ? opts.refreshS : 90;  // manifest refresh; 0 = off
    this.showLayers = { coast: true, borders: true, states: true, grid: true };
    this._geo = null;
  }
  var MOUNT_AHEAD = 6;               // preload stagger: max not-yet-loaded sources in flight
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
    this._pfx = m.product;    // product-scoped source ids (setProduct teardown safety)
    // calibrated BT data raster probe (pixel/BT inspector), if the product ships one
    this.probe = (m.bt && window.BTProbe) ? new window.BTProbe(m.bt, this.base) : null;
    this._startRamp();               // boot is always a cold load
    this.frames = this._deriveFrames();
    this.frameIdx = Math.max(0, this.frames.length - 1);

    var b = m.bounds; // [W,S,E,N]
    this.map = new maplibregl.Map({
      container: this.el, style: darkStyle(),
      bounds: b ? [[b[0], b[1]], [b[2], b[3]]] : [[-160, -60], [10, 60]],
      fitBoundsOptions: { padding: 24 },
      minZoom: (m.minzoom || 0),
      maxZoom: (m.maxzoom == null ? 5 : m.maxzoom) + 1,   // native + 1 over-zoom level
      renderWorldCopies: false, attributionControl: false, cooperativeGestures: true,
      preserveDrawingBuffer: true
    });
    this.map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
    this.map.dragRotate.disable();
    this.map.touchZoomRotate.disableRotation();
    // Our shift-drag AOI owns the shift+drag gesture; disable MapLibre's built-in
    // box-zoom so the two don't both fire (double camera move) on shift-drag.
    this.map.boxZoom.disable();

    // GPU memory pressure (a big frame stack on an integrated GPU) can kill
    // the WebGL context -- previously a dead black stage that read as "the
    // site crashed". preventDefault signals the browser we want a restore;
    // the 'gl-lost' status lets the cockpit rebuild the pane outright; and
    // the profile degrades to lowMem so the rebuilt loop mounts a smaller,
    // cheaper frame stack for the rest of the session.
    var glCanvas = (typeof this.map.getCanvas === 'function') ? this.map.getCanvas() : null;
    if (glCanvas && glCanvas.addEventListener) {
      glCanvas.addEventListener('webglcontextlost', function (ev) {
        if (ev && ev.preventDefault) ev.preventDefault();
        PERF.lowMem = true;
        PERF.hiDpi = false;
        self.onStatus('gl-lost');
      }, false);
    }

    // ONE persistent pair of listeners drives readiness, the preload pump and
    // pending reveals (no per-showFrame listeners to leak or fire stale).
    this.map.on('sourcedata', function (e) { self._onSourceData(e); });
    this.map.on('idle', function () { self._onIdle(); });

    // camera fetch discipline (contract rule 4): park the loop while moving
    this.map.on('movestart', function () { self._onCamStart(); });
    this.map.on('moveend', function () { self._onCamEnd(); });

    this.map.on('load', function () { self._onLoad(); });
  };

  VP._onLoad = function () {
    var self = this, m = this.manifest;
    // 1) the imagery: reveal the latest frame via the gated path (the 'frame'
    //    status only fires once real pixels are up -- the page's loading state
    //    stays honest), then preload the whole loop behind it.
    if (this.frames.length) {
      this.showFrame(this.frameIdx);
      this._preloadLoop();
    }

    // 2) graticule (below coast so land lines read on top)
    this.map.addSource('grat', { type: 'geojson', data: graticule(10) });
    this.map.addLayer({ id: 'grat', type: 'line', source: 'grat',
      paint: { 'line-color': '#ffffff', 'line-opacity': 0.14, 'line-width': 0.6 } });

    // 3) ne_* vector furniture (white, CycloLab canon) via the shared loader,
    // PLUS the boundary-LINES files (fetched here, not via loadGeo -- they are
    // a MapLibre-line-furniture concern; canvas viewers keep the polygons for
    // land fill). Guarded: a failed lines fetch falls back to polygon outlines.
    var geoLine = function (u) {
      return fetch(u).then(function (r) { return r.ok ? r.json() : null; })
        .catch(function () { return null; });
    };
    var loader = (window.TATRegions && window.TATRegions.loadGeo)
      ? Promise.all([
          window.TATRegions.loadGeo({}),
          geoLine('/ne_50m_admin_0_boundary_lines_land.geojson'),
          geoLine('/ne_50m_admin_1_states_provinces_lines.geojson')
        ]).then(function (r) {
          var geo = r[0] || {};
          geo.borderLines = r[1];
          geo.stateLines = r[2];
          return geo;
        })
      : Promise.resolve(null);
    loader.then(function (geo) { self._addFurniture(geo); }).catch(function () {});

    this.onStatus('ready', m.latest);
    // (no _updateReadout here: 'frame' fires from the reveal, once pixels land)
    // Constrain zoom-OUT to the data footprint: once the initial fitBounds
    // settles, pin minZoom to that zoom so you can never zoom out into empty
    // global space (a regional product is never a tiny patch on the whole world).
    var self = this;
    this.map.once('idle', function () { self._pinMinZoom(); });
    this._startRefresh();
  };

  // ---- live-manifest refresh (contract rule 5): the emitter backfills /
  // densifies manifests mid-session (10-min slot backfill grew loops 17->90);
  // without a refresh a live loop just goes stale, and any later merge that
  // remapped indexes under a playing loop caused jumps into unloaded frames. ----
  VP._startRefresh = function () {
    if (this._refreshT || !this.refreshS) return;
    var self = this;
    this._refreshT = setInterval(function () { self._refreshManifest(); },
                                 this.refreshS * 1000);
    // node (tests): never keep the process alive for a background timer
    if (this._refreshT && this._refreshT.unref) this._refreshT.unref();
    // _refreshManifest skips hidden tabs; without a catch-up, a returning tab
    // shows its accrued staleness for up to another full tick. Refresh the
    // moment the tab is visible again.
    if (typeof document !== 'undefined' && typeof document.addEventListener === 'function'
        && !this._visT) {
      this._visT = function () { if (!document.hidden) self._refreshManifest(); };
      document.addEventListener('visibilitychange', this._visT);
    }
  };
  VP._refreshManifest = function () {
    var self = this, url = this.manifestUrl;
    if (typeof document !== 'undefined' && document.hidden) return;
    fetch(url, { cache: 'no-cache' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (m) {
        // a product switch may have raced the fetch: only adopt if this is
        // still the current product's manifest
        if (!m || !m.tile || self.manifestUrl !== url || m.product !== self._pfx) return;
        var before = (self.manifest.times || []).join();
        self.manifest = m;
        if (self._mfCache) self._mfCache[url] = { m: m, t: Date.now() };
        if ((m.times || []).join() !== before) {
          // mid-PLAYBACK a remerge is synchronous source surgery under a
          // running clock (a visible hitch) — defer it to the loop's wrap
          // seam (or pause), where the tail->head jump hides it
          if (self.playing || self._extPlaying) self._pendingRemerge = true;
          else self._remergeFrames();
        }
      })
      .catch(function () {});   // transient fetch failure: keep the current loop
  };
  VP.applyPendingRemerge = function () {
    if (!this._pendingRemerge) return;
    this._pendingRemerge = false;
    this._remergeFrames();
  };

  // Loop window: the trailing loopCap stamps of the manifest. The full
  // manifest stays the SSOT; the cap bounds what playback keeps resident.
  // The requested cap is CLAMPED by product footprint + device profile:
  // a world-spanning product's every frame covers the whole viewport at
  // every camera, so its texture residency is the worst case (48 world
  // frames was the tester-reported GPU crash); @2x tiles quadruple the
  // per-frame cost, so hiDpi shaves the regional cap too.
  VP._loopCapFor = function () {
    var cap = Math.max(2, this.loopCap | 0);
    var b = this.manifest && this.manifest.bounds;
    var wide = b && (b[2] - b[0]) >= 300;
    if (wide) cap = Math.min(cap, PERF.lowMem ? 10 : 20);
    else if (PERF.lowMem) cap = Math.min(cap, 24);
    else if (PERF.hiDpi) cap = Math.min(cap, 36);
    return cap;
  };
  // DENSE RECENT WINDOW: the manifest holds days of history, and outage
  // eras leave multi-hour holes in it — a loop cut as "the trailing N of
  // everything" can span DAYS with wildly uneven spacing (measured: 36
  // conus frames across 47.8 h with a 32 h hole), so the scene teleports
  // between frames and no playback cadence can smooth it. Cut the loop as
  // "frames within LOOP_WINDOW_H of the newest" instead, floored at
  // LOOP_MIN_FRAMES so a thin feed still animates (reaching further back
  // only then), THEN cap by the device/product residency budget.
  var LOOP_WINDOW_H = 6;
  var LOOP_MIN_FRAMES = 12;
  // ---- visible-product dawn gate ----------------------------------------
  // A visible/day-only product's loop must not MIX night and day frames:
  // the near-black early frames read as a dark->bright flash on every wrap
  // (watched on a real capture). Trim unlit frames (sun below horizon at
  // the data footprint's center) when the window is mixed; an all-dark
  // window stays untouched (uniform dark doesn't flash, and the latest
  // frame must stay reachable). IR loops 24/7 as-is.
  var D2R = Math.PI / 180;
  function sunElevDeg(ms, lat, lon) {
    var d = ms / 86400000 - 10957.5;             // days since J2000
    var g = (357.529 + 0.98560028 * d) * D2R;    // solar mean anomaly
    var q = 280.459 + 0.98564736 * d;            // mean longitude (deg)
    var L = (q + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * D2R;
    var e = (23.439 - 0.00000036 * d) * D2R;     // obliquity
    var dec = Math.asin(Math.sin(e) * Math.sin(L));
    var ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L));
    var gmst = (280.46061837 + 360.98564736629 * d) % 360;
    var ha = (gmst + lon) * D2R - ra;
    return Math.asin(Math.sin(lat * D2R) * Math.sin(dec) +
                     Math.cos(lat * D2R) * Math.cos(dec) * Math.cos(ha)) / D2R;
  }
  VP._isDayProduct = function () {
    var m = this.productMeta || {};
    if (m.dayOnly || m.day_only) return true;
    var k = String(m.key || m.id || '');
    return /(^|-)[cb]0[1-6]$/.test(k);     // visible/NIR channels
  };
  VP._deriveFrames = function () {
    var t = (this.manifest && this.manifest.times) || [];
    var cap = this._loopCapFor();
    if (t.length > LOOP_MIN_FRAMES) {
      var newest = stampMs(t[t.length - 1]);
      var cut = 0;
      while (cut < t.length - 1 &&
             newest - stampMs(t[cut]) > LOOP_WINDOW_H * 3600e3) cut++;
      cut = Math.min(cut, t.length - LOOP_MIN_FRAMES);
      t = t.slice(cut);
    }
    // head-straggler trim: the floor's reach-back (or an outage hole inside
    // the window) can leave one gap that dwarfs the loop's own cadence — a
    // visible teleport no playback can smooth. Cut at the LAST such gap and
    // keep the dense tail (measured live: a lone 17:01Z frame ahead of a
    // 5 h hole in an otherwise 10-min loop). Evenly-sparse feeds are
    // untouched: the threshold scales off the set's own median gap.
    if (t.length > 4) {
      var g = [], gi;
      for (gi = 1; gi < t.length; gi++) g.push(stampMs(t[gi]) - stampMs(t[gi - 1]));
      var sg = g.slice().sort(function (a, b) { return a - b; });
      var thr = Math.max(45 * 60e3, 6 * sg[Math.floor(sg.length / 2)]);
      for (gi = g.length - 1; gi >= 0; gi--) {
        if (g[gi] > thr && t.length - (gi + 1) >= 4) { t = t.slice(gi + 1); break; }
      }
    }
    // UNIFORM CADENCE: mixed emit lanes leave 5/10/15-min gaps inside one
    // window (measured live: [25,5,15,10,10,10,5,15,...]) and the motion
    // hiccups at every irregular pair. Present ONE frame per fixed slot:
    // grid step = the window's modal gap floored at 10 min (the fast-lane
    // slot grid), walked back from the newest frame; a slot with no frame
    // within a third of the step is skipped consistently — never a 5/15 mix.
    if (t.length > 3) {
      var msArr = t.map(stampMs);
      var counts = {}, di;
      for (di = 1; di < msArr.length; di++) {
        var rd = Math.round((msArr[di] - msArr[di - 1]) / 300e3) * 300e3;
        if (rd > 0) counts[rd] = (counts[rd] || 0) + 1;
      }
      var G = 0, bestN = 0;
      Object.keys(counts).forEach(function (k) {
        if (counts[k] > bestN) { bestN = counts[k]; G = +k; }
      });
      G = Math.max(G, 600e3);
      this._loopCadenceMs = (G <= 3600e3) ? G : 0;
      if (G <= 3600e3) {
        var kept = [], tol = G / 3;
        for (var slot = msArr[msArr.length - 1]; slot >= msArr[0] - tol; slot -= G) {
          var bi = -1, bd = tol;
          for (di = msArr.length - 1; di >= 0; di--) {
            var dd = Math.abs(msArr[di] - slot);
            if (dd <= bd) { bd = dd; bi = di; }
          }
          if (bi >= 0 && (kept.length === 0 || kept[kept.length - 1] !== t[bi]))
            kept.push(t[bi]);
        }
        kept.reverse();
        if (kept.length >= 4) t = kept;
      }
    }
    if (this._isDayProduct() && t.length > 2) {
      var bb = this.manifest && this.manifest.bounds;
      var cLat = bb ? (bb[1] + bb[3]) / 2 : 25;
      var cLon = bb ? (bb[0] + bb[2]) / 2 : -90;
      var lit = t.filter(function (st2) {
        return sunElevDeg(stampMs(st2), cLat, cLon) > -1;
      });
      if (lit.length >= 2 && lit.length < t.length) t = lit;
    }
    // UNIFORM CADENCE THROUGH THE LAST FRAME. The cadence grid above anchors
    // to the NEWEST frame, so a newest frame that arrives after a latency gap
    // (the intervening cadence slots never landed) is kept on a grid slot but
    // separated from the dense run by a MULTIPLE of the step — an oversized
    // final step below the head-straggler threshold (measured: a 40-min last
    // step in a 10-min C13 loop, 03:31Z→04:11Z). Keep the newest frame in the
    // loop only when it sits within ~1 cadence step of the prior on-grid
    // frame; otherwise HOLD the tail at that last on-cadence slot so the final
    // interval equals the cadence like every other frame, and record the hold
    // (surfaced as FEED PAUSED). Clears the instant the missing slots fill and
    // the loop follows live again.
    this._offGridHoldMs = 0;
    var cad = this._loopCadenceMs || 0;
    if (!cad && t.length > 2) {
      var gaps = [];
      for (var ci = 1; ci < t.length; ci++) {
        gaps.push(stampMs(t[ci]) - stampMs(t[ci - 1]));
      }
      gaps.sort(function (a, b) { return a - b; });
      cad = gaps[Math.floor(gaps.length / 2)];
    }
    if (cad > 0 && cad <= 3600e3) {
      while (t.length > 4 &&
             stampMs(t[t.length - 1]) - stampMs(t[t.length - 2]) > 1.5 * cad) {
        this._offGridHoldMs = stampMs(t[t.length - 2]);  // the held-at slot
        t = t.slice(0, t.length - 1);
      }
    }
    return t.slice(Math.max(0, t.length - cap));
  };
  // PROGRESSIVE COLD LOAD: a cold product mount starts with a small
  // RESIDENCY cap (how many loop frames may be mounted at all) and grows
  // it stepwise as each slice finishes loading (_onIdle) — mounting a
  // heavy product's whole loop at once (world-covering rasters x 20
  // frames, tiles fetched+decoded+uploaded in one burst) was the cold
  // GEO-ring switch OOM. The frame LIST stays full (playback order,
  // follower joins and the newest pointer are unchanged); playback's
  // next-ready advance plays the resident subset while the rest streams
  // in. Warm resurrects skip the ramp (sources already resident).
  VP._startRamp = function () {
    this._residCap = Math.min(6, this._loopCapFor());
  };
  VP._mountedInLoop = function () {
    var n = 0;
    for (var i = 0; i < this.frames.length; i++)
      if (this._added[this.frames[i]]) n++;
    return n;
  };
  // Re-derive frames from the (updated) manifest, PRESERVING the current
  // stamp -- a merge must never remap frameIdx under a playing loop. Rolled-
  // off sources drop, new frames preload QUIETLY (no toast mid-session).
  VP._remergeFrames = function () {
    // GEOMETRY ADOPTION: a deeper pyramid cut (maxzoom bump) arrives via the
    // 90 s manifest refresh, but the camera cap was only ever set at mount/
    // product-switch — a live session would keep the old ceiling until
    // reload. Re-apply it here so deeper native tiles unlock in place.
    if (this.map && this.manifest) {
      try {
        var wantMax = (this.manifest.maxzoom == null ? 5 : this.manifest.maxzoom) + 1;
        if (this.map.getMaxZoom() !== wantMax) {
          this.map.setMaxZoom(wantMax);
          this._pinMinZoom();
        }
      } catch (e) {}
    }
    var cur = this.frames[this.frameIdx];
    var wasTail = this.frames.length > 0 && this.frameIdx === this.frames.length - 1;
    this.frames = this._deriveFrames();
    var idx = cur ? this.frames.indexOf(cur) : -1;
    if (idx < 0 && cur) {
      // current stamp rolled off (or cap shrank past it): nearest by time
      var t = stampMs(cur), bd = Infinity;
      for (var i = 0; i < this.frames.length; i++) {
        var d = Math.abs(stampMs(this.frames[i]) - t);
        if (d < bd) { bd = d; idx = i; }
      }
    }
    this.frameIdx = Math.max(0, idx);
    this._syncLoop(true);
    // FOLLOW LIVE: a viewer on the loop's newest frame is watching "now" --
    // when a merge brings newer stamps, advance to the new tail via the gated
    // showFrame path (the hold keeps the current frame up until the new tiles
    // land, so this can never flash or land on an unloaded frame). A viewer
    // scrubbed back mid-loop stays put; only the live edge follows the feed.
    var tail = this.frames.length - 1;
    if (wasTail && tail >= 0 && this.frameIdx !== tail) this.showFrame(tail);
    this._updateReadout();
  };
  VP.setLoopCap = function (n) {
    n = Math.max(2, n | 0);
    if (n === this.loopCap) return;
    this.loopCap = n;
    if (this.map && this.manifest) this._remergeFrames();
  };

  // Pin zoom-OUT to the CURRENT product's data footprint. Computed from the
  // manifest bounds (cameraForBounds — no camera move), so a product switch
  // re-derives it: CONUS→World must unlock the world fit, World→CONUS must
  // re-lock it (a stale pin either blocks zooming out to the globe or lets a
  // regional product shrink into empty space).
  VP._pinMinZoom = function () {
    var b = this.manifest && this.manifest.bounds;
    var fitZ = null;
    if (b) {
      try {
        var cam = this.map.cameraForBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 24 });
        if (cam) fitZ = cam.zoom;
      } catch (e) { /* degenerate container: keep the current pin */ }
    }
    if (fitZ == null) fitZ = this.map.getZoom();
    this._fitZoom = fitZ;
    this.map.setMinZoom(Math.max(0, fitZ - 0.15));
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
        paint: { 'line-color': '#000000', 'line-opacity': 0.5,
                 'line-width': ['interpolate', ['linear'], ['zoom'], 0, 1.2, 4, 1.8, 8, 2.6] } });
      map.addLayer({ id: 'coast', type: 'line', source: 'coast',
        paint: { 'line-color': '#ffffff', 'line-opacity': 0.92,
                 'line-width': ['interpolate', ['linear'], ['zoom'], 0, 0.5, 4, 0.9, 8, 1.4] } });
    }
    // Borders/states MUST be boundary-LINES, not admin-polygon outlines:
    // polygon rings re-trace every coastline (50m, offset from the 10m
    // coast) -- the doubled fuzzy coastal edge testers reported. Polygon
    // fallback only when the lines files failed to fetch.
    var borderSrc = geo.borderLines || geo.countries;
    if (borderSrc) {
      map.addSource('adm0', { type: 'geojson', data: borderSrc });
      map.addLayer({ id: 'borders', type: 'line', source: 'adm0',
        paint: { 'line-color': '#ffffff', 'line-opacity': 0.5,
                 'line-width': ['interpolate', ['linear'], ['zoom'], 0, 0.4, 4, 0.7, 8, 1.1] } });
    }
    var stateSrc = geo.stateLines || geo.states;
    if (stateSrc) {
      map.addSource('adm1', { type: 'geojson', data: stateSrc });
      map.addLayer({ id: 'states', type: 'line', source: 'adm1', minzoom: 3,
        paint: { 'line-color': '#ffffff', 'line-opacity': 0.28,
                 'line-width': ['interpolate', ['linear'], ['zoom'], 3, 0.3, 8, 0.8] } });
    }
    this._geo = geo;
    this._applyLayerVis();
  };

  // ---- frame raster sources (§6.1: per-frame source + opacity toggle) ----
  VP._srcId = function (stamp) { return (this._pfx || 'ir') + '-' + stamp; };

  // ---- 3D cloud-top DEM twins (ir3d.js) ------------------------------------
  // While 3D is on, every in-loop stamp gets a raster-dem twin (Terrain-RGB
  // synthesized client-side from the frame's bt.png via the tatdem protocol)
  // plus an INVISIBLE hillshade layer: MapLibre only fetches raster-dem tiles
  // for a source something actively uses, and setTerrain uses exactly one
  // source at a time — the transparent hillshade keeps every twin fetching
  // through the same MOUNT_AHEAD/park machinery as the imagery, so the
  // per-frame terrain flip in _reveal lands on already-cached tiles instead
  // of popping a fresh mesh fetch every advance. All of this is gated on
  // _dem3d: with 3D off none of it mounts and the 2D path is untouched.
  VP._demId = function (stamp) { return (this._pfx || 'ir') + '-dem-' + stamp; };
  VP._registerDem = function () {
    if (this._dem3d && this.manifest && this.manifest.bt && window.IR3D)
      window.IR3D.register(this._pfx, this.manifest.bt, this.base);
  };
  VP._ensureDem = function (stamp) {
    if (!this._dem3d || !this.manifest || !this.manifest.bt || !window.IR3D) return;
    var did = this._demId(stamp), m = this.manifest;
    if (!this.map.getSource(did)) {
      this.map.addSource(did, {
        type: 'raster-dem', tiles: [window.IR3D.urlTemplate(this._pfx, stamp)],
        tileSize: 256, minzoom: m.minzoom || 0,
        // the BT raster is 1280/2560 px wide — past z5 it's pure upsample
        maxzoom: Math.min(m.maxzoom || 5, 5),
        bounds: m.bounds || undefined, encoding: 'mapbox'
      });
    }
    if (!this.map.getLayer(did)) {
      var before = this.map.getLayer('grat') ? 'grat' : undefined;
      this.map.addLayer({ id: did, type: 'hillshade', source: did,
        paint: { 'hillshade-exaggeration': 0,
                 'hillshade-shadow-color': 'rgba(0,0,0,0)',
                 'hillshade-highlight-color': 'rgba(0,0,0,0)',
                 'hillshade-accent-color': 'rgba(0,0,0,0)' } }, before);
    }
    this.map.setLayoutProperty(did, 'visibility',
      (this._parked[stamp] || this._imgHidden) ? 'none' : 'visible');
  };
  VP._dropDem = function (did) {
    if (this.map.getLayer(did)) this.map.removeLayer(did);
    if (this.map.getSource(did)) {
      var t = this.map.getTerrain && this.map.getTerrain();
      if (t && t.source === did) this.map.setTerrain(null);
      this.map.removeSource(did);
    }
  };
  VP._flipTerrain = function (did) {
    if (!this.map.getSource(did)) return;
    var t = this.map.getTerrain && this.map.getTerrain();
    if (t && t.source === did && t.exaggeration === this._demEx) return;
    // setTerrain can emit camera events — the flip must not trip the
    // camera-parking machinery on every frame advance
    this._terrainFlip = true;
    try { this.map.setTerrain({ source: did, exaggeration: this._demEx }); }
    catch (e) {}
    this._terrainFlip = false;
  };
  VP.setTerrain3D = function (on, ex) {
    if (ex != null) this._demEx = ex;
    if (!this._demEx) this._demEx = 8;
    this._dem3d = !!on;
    if (!this.map) return;
    if (on) {
      this._registerDem();
      for (var s in this._added) this._ensureDem(s);
      var cur = this.frames[this.frameIdx];
      // first enable: attach terrain to the on-screen frame now (an accepted
      // one-time mesh pop); every later flip rides _reveal's confirmed gate
      if (cur && this.manifest && this.manifest.bt) this._flipTerrain(this._demId(cur));
    } else {
      if (this.map.getTerrain && this.map.getTerrain()) {
        try { this.map.setTerrain(null); } catch (e) {}
      }
      // drop every dem twin on this map — current AND retired product
      var style = this.map.getStyle();
      var ids = Object.keys((style && style.sources) || {});
      for (var i = 0; i < ids.length; i++)
        if (ids[i].indexOf('-dem-') >= 0) this._dropDem(ids[i]);
    }
  };
  VP.setTerrainEx = function (ex) {
    this._demEx = ex;
    var t = this.map && this.map.getTerrain && this.map.getTerrain();
    if (t) {
      try { this.map.setTerrain({ source: t.source, exaggeration: ex }); }
      catch (e) {}
    }
  };

  VP._ensureFrame = function (stamp, opacity) {
    var sid = this._srcId(stamp);
    if (this._added[stamp]) {
      if (this.map.getLayer(sid)) {
        this.map.setPaintProperty(sid, 'raster-opacity', opacity);
        if (!this._imgHidden) this.map.setLayoutProperty(sid, 'visibility', 'visible');
      }
      if (this._dem3d) this._ensureDem(stamp);
      return;
    }
    var m = this.manifest;
    // s2c routes container frames through ranged reads and legacy frames
    // through their per-tile objects -- per-frame, transparently (see the
    // protocol block above). Mixed manifests during the container
    // transition need no special casing here.
    var url = s2cFrameTiles(this.base, m.tile, stamp, m.maxzoom,
                            !!m.containers);
    // @2x on HiDPI: declaring the physical 512-px tile at half size pulls
    // one pyramid level deeper -- native-res pixels instead of the 2x
    // upsample testers read as "blurry at the default view". The pyramid
    // itself is unchanged; requests past its maxzoom just over-zoom the
    // deepest level exactly as before.
    this.map.addSource(sid, {
      type: 'raster', tiles: [url],
      tileSize: PERF.hiDpi ? (m.tile_size || 512) / 2 : (m.tile_size || 512),
      minzoom: m.minzoom || 0,
      maxzoom: m.maxzoom == null ? 5 : m.maxzoom,
      bounds: m.bounds || undefined, scheme: 'xyz'
    });
    // insert imagery BELOW the first furniture layer (grat/coast) if present
    var before = this.map.getLayer('grat') ? 'grat' : undefined;
    // raster-fade-duration:0 kills the per-TILE fade-in, but raster-opacity is
    // a TRANSITIONABLE paint property: without an explicit zero transition,
    // every setPaintProperty flip in _reveal animates through MapLibre's
    // default 300 ms ease -- the exact ghost/crossfade the no-strobe contract
    // forbids. Both zeros are required for a clean cut.
    this.map.addLayer({ id: sid, type: 'raster', source: sid,
      paint: { 'raster-opacity': opacity, 'raster-fade-duration': 0,
               'raster-opacity-transition': { duration: 0, delay: 0 },
               'raster-resampling': 'linear' } }, before);
    if (this._imgHidden) this.map.setLayoutProperty(sid, 'visibility', 'none');
    this._added[stamp] = true;
    if (this._dem3d) this._ensureDem(stamp);
    // NOT ready yet: readiness is event-confirmed by _onSourceData/_onIdle.
  };

  // ---- full-loop preload: mount every in-loop frame at opacity 0 so all
  // tiles fetch+decode up front; playback then only flips opacity between
  // fully-loaded frames. Mounting is staggered (MOUNT_AHEAD unloaded sources
  // in flight) so 90 addSource calls don't land in one style update and the
  // 'loading N/M' progress reads monotonically in playback order. ----
  VP._preloadLoop = function (quiet) {
    if (!this.map || !this.frames.length) return;
    var q = [], n = this.frames.length;
    for (var i = 1; i <= n; i++) {           // playback order from current+1
      var s = this.frames[(this.frameIdx + i) % n];
      if (!this._added[s] || this._parked[s]) q.push(s);
    }
    this._mountQ = q;
    this._loadedEmitted = false;
    this._announce = !quiet;
    this._pumpMounts();
    this._emitLoading();
  };
  VP._pumpMounts = function () {
    if (!this.map || this._imgHidden || this._camMoving) return;
    // in flight = mounted, fetching, not yet confirmed (parked frames are
    // hidden and fetch nothing, so they don't count against the stagger)
    var inflight = 0;
    for (var s in this._added) if (!this._ready[s] && !this._parked[s]) inflight++;
    // world-spanning products: every source fetches viewport-covering tiles,
    // so halve the in-flight stagger (the per-source cost is ~4x a regional
    // frame's at the same camera)
    var bb = this.manifest && this.manifest.bounds;
    var ahead = (bb && (bb[2] - bb[0]) >= 300) ? 3 : MOUNT_AHEAD;
    var resid = this._residCap || Infinity;
    while (inflight < ahead && this._mountQ.length) {
      var st = this._mountQ.shift();
      if (this.frames.indexOf(st) < 0) continue;
      if (this._added[st]) {
        if (!this._parked[st]) continue;     // mounted + visible: nothing to do
        // camera-parked frame: unhide so its tiles fetch, then the normal
        // sourcedata/idle gate re-confirms it. NOT ramp-gated: parked
        // frames are already mounted, so unparking adds zero residency —
        // gating them wedged every camera-move resume during a cold ramp
        // (review-caught).
        delete this._parked[st];
        var id = this._srcId(st);
        if (this.map.getLayer(id)) this.map.setLayoutProperty(id, 'visibility', 'visible');
        if (this._dem3d && this.map.getLayer(this._demId(st)))
          this.map.setLayoutProperty(this._demId(st), 'visibility', 'visible');
        inflight++;
        continue;
      }
      // only NEW mounts consume residency; requeue and stop when capped
      if (this._mountedInLoop() >= resid) { this._mountQ.unshift(st); break; }
      this._ensureFrame(st, 0);
      inflight++;
    }
  };
  VP._frameReady = function (stamp) {
    // sticky event-confirmed flag AND a live check (a camera move needs new
    // tiles even for a once-loaded source). Never trust a hidden source --
    // parked frames are hidden by definition, so they can never be "ready".
    return !this._parked[stamp] && !!this._ready[stamp] &&
      this.map.isSourceLoaded(this._srcId(stamp));
  };
  VP._onSourceData = function (e) {
    var pfx = (this._pfx || 'ir') + '-';
    if (!e.sourceId || e.sourceId.indexOf(pfx) !== 0) return;
    var stamp = e.sourceId.slice(pfx.length);
    if (!this._added[stamp] || this._parked[stamp]) return;  // hidden = no evidence
    if (!this.map.isSourceLoaded(e.sourceId)) return;
    if (!this._ready[stamp]) { this._ready[stamp] = true; this._emitLoading(); }
    this._pumpMounts();
    this._growRamp();
    if (this._wantStamp === stamp) this._revealPending();
  };
  VP._onIdle = function () {
    // idle = every visible source has all its tiles: confirm the lot at once
    // (belt-and-braces for any sourcedata event the per-source path missed).
    // Parked frames are hidden -- idle says NOTHING about them, so they are
    // excluded (blanket-confirming hidden sources was the original strobe).
    if (this._imgHidden) return;
    var changed = false;
    for (var s in this._added)
      if (!this._ready[s] && !this._parked[s]) { this._ready[s] = true; changed = true; }
    this._pumpMounts();
    if (this._wantStamp != null && this._added[this._wantStamp]) this._revealPending();
    if (changed) this._emitLoading();
    this._growRamp();
  };
  // ramp growth: every mounted frame is confirmed -> double the residency
  // cap and pump the next slice QUIETLY. Growth-paced-by-load is the hard
  // working-set bound that keeps a cold heavy switch from mounting
  // everything at once. Checked from idle AND sourcedata AND camera-resume:
  // a no-new-tiles camera move can consume the only idle inside the resume
  // debounce, and growth must not depend on an event that never re-fires
  // (review-caught wedge).
  VP._growRamp = function () {
    if (!this._residCap || this._camMoving) return;
    var mounted = this._mountedInLoop();
    if (mounted >= this.frames.length) { this._residCap = null; return; }
    var allMountedReady = true;
    for (var i = 0; i < this.frames.length; i++) {
      var st = this.frames[i];
      if (this._added[st] && !this._ready[st] && !this._parked[st]) {
        allMountedReady = false;
        break;
      }
    }
    if (allMountedReady && mounted) {
      this._residCap = Math.min(this.frames.length, this._residCap * 2);
      this._pumpMounts();
    }
  };
  // explicit play = the user wants the WHOLE loop: drop the progressive
  // residency cap so the remaining frames stream in now (still bounded by
  // the MOUNT_AHEAD fetch stagger + the loop cap itself). The ramp exists
  // to soften a cold background mount, not to ration an active playback —
  // rationing it is exactly the "choppy loop" testers see on a cold page.
  VP.finishRamp = function () {
    if (!this._residCap) return;
    this._residCap = null;
    this._pumpMounts();
  };

  // ---- camera fetch discipline (contract rule 4) ----
  VP._onCamStart = function () {
    if (this._terrainFlip) return;   // a terrain source swap is not a camera move
    this._camMoving = true;
    if (this._resumeT) { clearTimeout(this._resumeT); this._resumeT = null; }
    if (this._imgHidden) return;   // field mode already owns visibility
    var keep = {};
    keep[this.frames[this.frameIdx]] = 1;
    if (this._wantStamp != null) keep[this._wantStamp] = 1;
    for (var s in this._added) {
      if (keep[s] || this._parked[s]) continue;
      var id = this._srcId(s);
      if (this.map.getLayer(id)) this.map.setLayoutProperty(id, 'visibility', 'none');
      if (this._dem3d) {
        var did = this._demId(s);
        if (this.map.getLayer(did)) this.map.setLayoutProperty(did, 'visibility', 'none');
      }
      this._parked[s] = true;
      delete this._ready[s];   // a hidden source's readiness is a lie
    }
  };
  VP._onCamEnd = function () {
    var self = this;
    if (this._resumeT) clearTimeout(this._resumeT);
    // debounce: linked panes replay a drag as a burst of jumpTo moveends
    this._resumeT = setTimeout(function () {
      self._resumeT = null;
      self._camMoving = false;
      self._resumeParked();
    }, 300);
  };
  VP._resumeParked = function () {
    if (!this.map) return;
    if (this._imgHidden) { this._parked = {}; return; }
    // camera resumes are background fills: re-confirming half the loop must
    // not re-run the 'loading N/M' toast on every pan. If a FOREGROUND fill
    // was mid-flight (boot/switch), release its toast first -- a quiet fill
    // never emits 'loaded', and the cockpit's toast is cleared by 'loaded'.
    if (this._announce && !this._loadedEmitted) {
      this._loadedEmitted = true;
      this.onStatus('loaded', { total: this.frames.length });
    }
    this._announce = false;
    this._loadedEmitted = true;
    var q = [], n = this.frames.length, i, s;
    for (i = 1; i <= n; i++) {               // playback order from current+1
      s = this.frames[(this.frameIdx + i) % n];
      if (this._parked[s]) q.push(s);
    }
    // a reveal requested mid-move resumes FIRST (the user is waiting on it)
    if (this._wantStamp != null) {
      var wi = q.indexOf(this._wantStamp);
      if (wi > 0) { q.splice(wi, 1); q.unshift(this._wantStamp); }
    }
    // parked stamps no longer in the loop: leave them hidden; _syncLoop /
    // _dropRetired own their teardown
    this._mountQ = q.concat(this._mountQ.filter(function (x) {
      return q.indexOf(x) < 0;
    }));
    this._pumpMounts();      // staggered unhide -> re-fetch -> re-confirm
    this._growRamp();        // a no-new-tiles move may never idle again
    this._revealPending();   // a reveal parked mid-move resumes through the gate
  };
  VP._emitLoading = function () {
    if (this._imgHidden) return;   // field mode: imagery isn't fetching; a
                                   // frozen 'loading N/M' would read as a hang
    if (!this._announce) return;   // background fill: no toast churn mid-session
    var total = this.frames.length, done = 0;
    for (var i = 0; i < total; i++) if (this._ready[this.frames[i]]) done++;
    if (done < total) { this.onStatus('loading', { done: done, total: total }); return; }
    if (!this._loadedEmitted && total) {
      this._loadedEmitted = true;
      this.onStatus('loaded', { total: total });
    }
  };

  VP._reveal = function (idx, stamp) {
    // flip: raise the incoming frame, zero every other in-loop frame. The
    // explicit raise matters for frames that were preloaded/resumed at
    // opacity 0 -- zeroing the others around a still-transparent target
    // would flash the basemap.
    var tid = this._srcId(stamp);
    if (this.map.getLayer(tid)) {
      this.map.setPaintProperty(tid, 'raster-opacity', 1);
      if (!this._imgHidden) this.map.setLayoutProperty(tid, 'visibility', 'visible');
    }
    // 3D: flip the terrain to this stamp's dem twin at the SAME gated moment
    // the imagery cuts — the twin's tiles prefetched via its hillshade, so
    // the mesh swap rides the same tiles-confirmed reveal, never earlier
    if (this._dem3d && this.manifest && this.manifest.bt) {
      this._ensureDem(stamp);
      this._flipTerrain(this._demId(stamp));
    }
    for (var i = 0; i < this.frames.length; i++) {
      var s = this.frames[i];
      if (s !== stamp && this._added[s] && this.map.getLayer(this._srcId(s)))
        this.map.setPaintProperty(this._srcId(s), 'raster-opacity', 0);
    }
    this._hideRetired();   // the incoming product is on screen -- stop the
                           // outgoing one showing through transparent pixels
    this.frameIdx = idx;
    this._wantStamp = null;
    this._updateReadout();
  };
  VP._revealPending = function () {
    var stamp = this._wantStamp;
    if (stamp == null) return;
    if (this._imgHidden || this._frameReady(stamp)) this._reveal(this._wantIdx, stamp);
  };

  VP.showFrame = function (idx) {
    if (!this.frames.length) return;
    idx = (idx + this.frames.length) % this.frames.length;
    var stamp = this.frames[idx], n = this.frames.length;
    // mid-camera-move: park the request (newest wins); _resumeParked picks it
    // up after the camera rests -- unparking frames during a drag would
    // restart the loop-wide fetch storm the parking exists to prevent
    if (this._camMoving) { this._wantStamp = stamp; this._wantIdx = idx; return; }
    // BT raster for the inspector — NOT during playback: fetching+decoding
    // a few-hundred-KB PNG per frame advance was a real stutter source; a
    // paused/scrubbed frame still loads it for hover/pin sampling.
    if (this.probe && !this.playing && !this._extPlaying)
      this.probe.load(stamp).catch(function () {});
    // Mount/raise the target at full opacity but HOLD the prior frame opaque
    // underneath until the target's tiles are confirmed loaded -- the reveal
    // (zeroing the others) is what must never run early. Until then the new
    // layer just renders transparent-over-prior (raster-fade-duration:0).
    if (this._parked[stamp]) delete this._parked[stamp];   // unhide via ensure
    this._ensureFrame(stamp, 1);
    // decode-ahead insurance for scrubs past the preload frontier (skip the
    // currently-displayed frame: zeroing it here would blank the hold).
    var cur = this.frames[this.frameIdx];
    for (var ah = 1; ah <= 2; ah++) {
      var nxt = this.frames[(idx + ah) % n];
      if (nxt && nxt !== stamp && nxt !== cur) this._ensureFrame(nxt, 0);
    }
    this._wantStamp = stamp; this._wantIdx = idx;   // newest request wins;
    this._revealPending();                          // stale reveals are dead
  };

  // ---- product switching (the imagery-suite picker) ----
  // Swap manifests IN PLACE: camera, furniture, layer toggles and inspector all
  // survive; only the frame sources + probe + clock change. On a missing/failed
  // manifest the current product stays up (onStatus 'product-missing' lets the
  // page say "no data yet" -- products appear as the box emits them).
  //
  // NO-FLASH CONTRACT: the outgoing product's layers stay VISIBLE until the
  // incoming product's frame actually has tiles (showFrame's reveal hides them)
  // -- a switch never blanks to the background mid-fetch. The outgoing product
  // is RETIRED, not destroyed: its sources/textures stay resident (hidden), so
  // toggling straight back is instant. One retired product is kept; switching
  // to a third tears the oldest down.
  VP._manifestCached = function (url) {
    // session manifest cache (45 s TTL): repeat switches skip the RTT; the
    // CDN's own cache headers still bound real staleness.
    this._mfCache = this._mfCache || {};
    var hit = this._mfCache[url];
    if (hit && (Date.now() - hit.t) < 45e3) return Promise.resolve(hit.m);
    var self = this;
    return fetch(url, { cache: 'no-cache' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (m) {
        if (!m || !m.tile) throw new Error('not a tiled manifest');
        self._mfCache[url] = { m: m, t: Date.now() };
        return m;
      });
  };
  VP._retire = function () {
    // hide the current product's layers and remember everything needed to
    // resurrect it; drop any previously retired product for real.
    if (this._retired) this._dropRetired();
    var stamps = [];
    // layers keep their exact on-screen state (current frame opaque, the
    // rest transparent/hidden) -- the outgoing view must not change at all
    // until the incoming product's tiles land
    for (var s in this._added) stamps.push(s);
    this._retired = {
      manifestUrl: this.manifestUrl, manifest: this.manifest, base: this.base,
      pfx: this._pfx, probe: this.probe, frames: this.frames,
      frameIdx: this.frameIdx, added: this._added, ready: this._ready,
      stamps: stamps
    };
    // stamp-keyed state swaps WITH the product: two products can share stamps,
    // so carried-over flags would fake readiness on the incoming loop.
    this._added = {};
    this._ready = {};
    this._parked = {};
    this._mountQ = [];
    this._wantStamp = null;
  };
  VP._dropRetired = function () {
    var r = this._retired;
    if (!r) return;
    for (var i = 0; i < r.stamps.length; i++) {
      var id = r.pfx + '-' + r.stamps[i];
      if (this.map.getLayer(id)) this.map.removeLayer(id);
      if (this.map.getSource(id)) this.map.removeSource(id);
      this._dropDem(r.pfx + '-dem-' + r.stamps[i]);
    }
    this._retired = null;
  };
  VP._hideRetired = function () {
    // called from showFrame's reveal: the new product is on screen, so the
    // retired one must stop showing through the new product's transparent
    // pixels (e.g. CONUS bleeding through the world composite's honest gap)
    var r = this._retired;
    if (!r) return;
    for (var i = 0; i < r.stamps.length; i++) {
      var id = r.pfx + '-' + r.stamps[i];
      if (this.map.getLayer(id)) this.map.setLayoutProperty(id, 'visibility', 'none');
      var did = r.pfx + '-dem-' + r.stamps[i];
      if (this.map.getLayer(did)) this.map.setLayoutProperty(did, 'visibility', 'none');
    }
  };
  VP._adoptState = function (st) {
    this.manifestUrl = st.manifestUrl;
    this.manifest = st.manifest;
    this.base = st.base;
    this._pfx = st.pfx;
    this.probe = st.probe;
    this.frames = st.frames;
    this.frameIdx = Math.min(st.frameIdx, Math.max(0, st.frames.length - 1));
    this._added = st.added || {};
    this._ready = st.ready || {};
    this._registerDem();   // 3D on: the resurrected product's twins re-mount via ensure
    this.map.setMaxZoom(((st.manifest && st.manifest.maxzoom) || 5) + 1);
    this._pinMinZoom();
  };
  // frames list changed in place (manifest refresh/merge): drop sources whose
  // stamp rolled off the loop, then preload whatever is new. The ONLY teardown
  // path for in-loop residency -- never runs mid-playback on a stable list.
  // quiet=true for background merges (manifest refresh / cap changes): the
  // fill happens without 'loading N/M' toast churn.
  VP._syncLoop = function (quiet) {
    for (var s in this._added) {
      if (this.frames.indexOf(s) >= 0) continue;
      var id = this._srcId(s);
      // flags BEFORE map surgery: MapLibre fires sourcedata SYNCHRONOUSLY
      // inside removeSource, and a stale flag would walk that event into
      // isSourceLoaded() on the just-removed source (console error spam)
      delete this._added[s];
      delete this._ready[s];
      delete this._parked[s];
      if (this.map.getLayer(id)) this.map.removeLayer(id);
      if (this.map.getSource(id)) this.map.removeSource(id);
      this._dropDem(this._demId(s));   // dem twin rolls off with its frame
    }
    this._preloadLoop(quiet);
  };
  VP.setProduct = function (manifestUrl, meta) {
    var self = this;
    // Request epoch: every setProduct call — including the no-op guard —
    // supersedes any in-flight switch. Without it, A->B->A inside B's
    // manifest RTT no-ops on the guard (manifestUrl still A) and then B's
    // stale .then lands and stomps the selection (tiles B, rail A — the
    // desync class this file exists to prevent).
    this._prodReq = (this._prodReq || 0) + 1;
    var prodReq = this._prodReq;
    if (meta) this.productMeta = meta;   // day-only/visible gating reads this
    // RE-SELECTING the current product is a freshness no-op, never a
    // teardown/remount: retiring a product into ITSELF collides the
    // stamp-keyed source ids ("source already exists" — the GEO-ring
    // re-select crash) and there is nothing to switch anyway.
    if (manifestUrl === this.manifestUrl && this.manifest) {
      this._refreshManifest();
      this.onStatus('ready', this.manifest.latest);
      return Promise.resolve(this.manifest);
    }
    // instant switch-back: the retired product resurrects without a fetch
    // (its sources are still mounted); the manifest refreshes in background.
    if (this._retired && this._retired.manifestUrl === manifestUrl) {
      this.pause();
      var back = this._retired;
      this._retired = null;
      this._retire();                       // current product retires in its place
      this._adoptState(back);
      this._residCap = null;                // warm resurrect: already resident
      // resurrected layers are HIDDEN (retire hid them): park them all so the
      // pump unhides staggered and readiness re-confirms through the event
      // gate -- adopted _ready flags describe a hidden source, i.e. nothing
      for (var rs in this._added) { this._parked[rs] = true; delete this._ready[rs]; }
      if (this.frames.length) { this.showFrame(this.frameIdx); this._preloadLoop(); }
      this.onStatus('ready', this.manifest.latest);
      // background freshness: merge any frames emitted while it was retired
      this._manifestCached(manifestUrl).then(function (m) {
        if (self.manifestUrl !== manifestUrl || m.product !== self._pfx) return;
        self.manifest = m;
        self.frames = self._deriveFrames();
        self._syncLoop();
        if (self.frames.length) self.showFrame(self.frames.length - 1);
      }).catch(function () {});
      return Promise.resolve(this.manifest);
    }
    return this._manifestCached(manifestUrl)
      .then(function (m) {
        if (prodReq !== self._prodReq) return self.manifest;  // superseded
        self.pause();
        self._retire();                     // old product stays VISIBLE under the new
                                            // (retire also kills any pending reveal)
        self.manifestUrl = manifestUrl;
        self.manifest = m;
        self.base = manifestBase(manifestUrl, m.product);
        self._pfx = m.product;
        self.probe = (m.bt && window.BTProbe) ? new window.BTProbe(m.bt, self.base) : null;
        self._registerDem();             // 3D on: dem twins follow the product
        self._startRamp();               // full-fetch switch = cold mount
        self.frames = self._deriveFrames();
        self.frameIdx = Math.max(0, self.frames.length - 1);
        self.map.setMaxZoom((m.maxzoom == null ? 5 : m.maxzoom) + 1);   // per-product native zoom
        self._pinMinZoom();
        if (self.frames.length) {
          // showFrame holds the retired product on screen until the new
          // frame's tiles land, then hides it (no black flash, ever);
          // the rest of the loop preloads behind it with 'loading N/M' status
          self.showFrame(self.frameIdx);
          self._preloadLoop();
          if (self.probe) self.probe.load(self.frames[self.frameIdx]).catch(function () {});
        } else {
          self._hideRetired();
        }
        self.onStatus('ready', m.latest);
        self._updateReadout();
        return m;
      })
      .catch(function (e) {
        // a superseded request's failure is not the CURRENT product's news
        if (prodReq === self._prodReq)
          self.onStatus('product-missing', { url: manifestUrl, meta: meta, err: e.message });
        throw e;
      });
  };

  // Nearest-in-time frame (compare panes hold DIFFERENT products whose stamp
  // lists may differ; the shared clock syncs by time, not index).
  function stampMs(s) {
    return Date.UTC(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8),
                    +s.slice(9, 11), +s.slice(11, 13), +s.slice(13, 15) || 0);
  }
  VP.showStamp = function (stamp) {
    var f = this.frames;
    if (!f.length || !stamp) return;
    var t = stampMs(stamp), best = 0, bd = Infinity;
    for (var i = 0; i < f.length; i++) {
      var d = Math.abs(stampMs(f[i]) - t);
      if (d < bd) { bd = d; best = i; }
    }
    this.showFrame(best);
  };

  // ---- fixed-timestep loop playback ----
  VP.play = function () {
    if (this.playing || this.frames.length < 2) return;
    this.playing = true; this._last = 0; this._dwell = 0;
    this.finishRamp();
    var self = this;
    var step = function (t) {
      if (!self.playing) return;
      if (!self._last) self._last = t;
      var interval = 1000 / self.fps;
      var atNewest = self.frameIdx === self.frames.length - 1;
      if (atNewest) interval *= self.dwellNewest;   // 6x dwell on the latest frame
      if (t - self._last >= interval) {
        self._last = t;
        if (self.frameIdx + 1 >= self.frames.length) self.applyPendingRemerge();
        self.showFrame(self.frameIdx + 1);
      }
      self._raf = requestAnimationFrame(step);
    };
    this._raf = requestAnimationFrame(step);
    this.onStatus('play');
  };
  VP.pause = function () {
    this.playing = false;
    this.applyPendingRemerge();
    if (this._raf) cancelAnimationFrame(this._raf);
    // playback skipped the per-frame BT loads; restore the inspector's
    // raster for the frame we stopped on
    if (this.probe && this.frames.length)
      this.probe.load(this.frames[this.frameIdx]).catch(function () {});
    this.onStatus('pause');
  };

  // Playback advance policy: keep CADENCE, not completeness. If the next
  // frame's tiles aren't event-confirmed yet, skip ahead to the next READY
  // frame — unready frames fill in quietly and get picked up next lap —
  // instead of stalling the clock on a fetch and jump-cutting when it
  // lands (the residual "stutter" testers still saw). When nothing ahead
  // is ready (boot, camera-resume refill), fall back to +1: showFrame's
  // hold keeps the current frame up, which is the old behavior.
  VP.nextReadyIdx = function (from) {
    var n = this.frames.length;
    if (!n) return 0;
    for (var k = 1; k <= n; k++) {
      var idx = (from + k) % n;
      if (this._frameReady(this.frames[idx])) return idx;
    }
    return (from + 1) % n;
  };
  VP.toggle = function () { this.playing ? this.pause() : this.play(); };

  // ---- layer toggles ----
  VP.setLayer = function (key, on) { this.showLayers[key] = on; this._applyLayerVis(); };

  // Hide/show ALL imagery frame layers (cockpit MW/ASCAT field mode: the
  // vector furniture stays, the tiles go). _ensureFrame honors the flag so
  // frames added later while hidden stay hidden.
  VP.setImageryVisible = function (on) {
    this._imgHidden = !on;
    for (var s in this._added) {
      var id = this._srcId(s);
      if (this.map.getLayer(id))
        this.map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
      if (this._dem3d) {
        var did = this._demId(s);
        if (this.map.getLayer(did))
          this.map.setLayoutProperty(did, 'visibility', on ? 'visible' : 'none');
      }
    }
    if (on) {
      // hidden sources fetched nothing (and hiding un-trusts their readiness),
      // so restart the preload pump + any reveal that was parked while hidden.
      // Field-mode restore made every layer visible above, so camera-parking
      // flags are void too.
      this._parked = {};
      for (var s2 in this._ready) if (this.frames.indexOf(s2) >= 0) delete this._ready[s2];
      this._loadedEmitted = false;
      this._pumpMounts();
      this._revealPending();
    }
  };
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
    var w = r.w, e = r.e, crosses = e < w;
    if (crosses) e += 360;
    this.map.setRenderWorldCopies(crosses);   // restore false for non-crossing regions
    this.map.fitBounds([[w, r.s], [e, r.n]], { padding: 20, duration: 500 });
  };
  VP.fitData = function () {
    var b = this.manifest.bounds;
    // the data footprint always fits in ONE world -- world copies left on by
    // an antimeridian region visit would tile duplicate earths side by side
    this.map.setRenderWorldCopies(false);
    if (b) this.map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 24, duration: 500 });
  };

  // A drag-rectangle AOI over the map -> fitBounds. Reusable across MapLibre
  // viewers (satellite/models/TAW). Shift+drag to draw (so plain drag still
  // pans); the caller can also arm one drag via tv._armed (the cockpit's Box
  // button — the touch path, since touch has no shift). MUST be wired at pane
  // creation, not lazily: this listener OWNS the shift+drag gesture, and a
  // pane without it silently pans instead (the tester "draw box doesn't
  // work" bug — it was only wired on the first Box-button click). Pointer
  // events cover mouse + touch in one path; mouse events are the fallback.
  // buttonEl is display-only (armed state cleanup) — arming stays with the
  // caller.
  VP.enableDrawBox = function (buttonEl, onBox) {
    var self = this, map = this.map, canvas = map.getCanvasContainer();
    var start = null, box = null, active = false;
    var PTR = (typeof window !== 'undefined') && ('PointerEvent' in window);
    var MOVE = PTR ? 'pointermove' : 'mousemove';
    var UP = PTR ? 'pointerup' : 'mouseup';
    function mousePos(e) {
      var rect = canvas.getBoundingClientRect();
      return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    }
    function onDown(e) {
      if (!active || !(e.shiftKey || self._armed)) return;
      // left button or touch only — right/middle clicks keep their meaning
      if (e.button != null && e.button !== 0) return;
      // capture phase on the canvas CONTAINER: stopping propagation here
      // means MapLibre (listening on the canvas below) never sees this
      // gesture at all — no competing pan start, and no synthesized map
      // 'click' on release to drop a stray BT pin. dragPan.disable() stays
      // as belt-and-braces for the mouse-event fallback path.
      e.preventDefault();
      if (e.stopPropagation) e.stopPropagation();
      map.dragPan.disable();
      start = mousePos(e);
      document.addEventListener(MOVE, onMove);
      document.addEventListener(UP, onUp);
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
      document.removeEventListener(MOVE, onMove);
      document.removeEventListener(UP, onUp);
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
      if (onBox) onBox([w, s, e2, n]);   // consumers (Time Machine AOI) get the box
    }
    active = true;
    canvas.addEventListener(PTR ? 'pointerdown' : 'mousedown', onDown, true);
  };

  VP._updateReadout = function () {
    var stamp = this.frames[this.frameIdx] || (this.manifest && this.manifest.latest);
    if (stamp) this.onStatus('frame', { stamp: stamp, idx: this.frameIdx, n: this.frames.length });
  };

  // ---- pixel/BT inspector: hover reads REAL brightness temp from the BT raster
  // (not the colorized tile); click pins it. ----
  VP.enableInspector = function () {
    // Attach unconditionally: `this.probe` is re-resolved per event, so a
    // product switch (setProduct) turns the inspector on/off with the product.
    // setInspector(false) gates BOTH the hover readout and click-to-pin (the
    // cockpit's Inspect toggle) without detaching anything.
    var self = this, map = this.map;
    this._inspect = true;
    map.on('mousemove', function (e) {
      if (!self._inspect || !self.probe) { self.onStatus('probe', null); return; }
      var btC = self.probe.sample(self.frames[self.frameIdx], e.lngLat.lng, e.lngLat.lat);
      self.onStatus('probe', { lon: e.lngLat.lng, lat: e.lngLat.lat, btC: btC });
    });
    map.getCanvas().addEventListener('mouseout', function () { self.onStatus('probe', null); });
    map.on('click', function (e) { if (!self._armed && self._inspect && self.probe) self._pinBT(e.lngLat); });
  };
  VP.setInspector = function (on) {
    this._inspect = !!on;
    if (!on) this.onStatus('probe', null);
  };
  VP.clearPins = function () {
    (this._pins || []).forEach(function (p) { p.remove(); });
    this._pins = [];
  };
  VP._pinBT = function (lngLat) {
    if (!this.probe) return;
    var self = this, stamp = this.frames[this.frameIdx];
    var el = document.createElement('div'); el.className = 'tv-pin';
    var lbl = document.createElement('b'), bar = document.createElement('i');
    el.appendChild(lbl); el.appendChild(bar);
    // Sample now; re-sample once the BT PNG is decoded, so a pin dropped before
    // the raster loaded upgrades a provisional 'no data' to the real BT (genuine
    // off-data still resolves to 'no data'). load() returns a resolved promise
    // when already cached, so this is a no-op in the common case.
    var render = function () {
      var btC = self.probe.sample(stamp, lngLat.lng, lngLat.lat);
      lbl.textContent = (btC == null ? 'no data' : btC.toFixed(1) + '°C');
    };
    render();
    self.probe.load(stamp).then(render, render);
    var mk = new maplibregl.Marker({ element: el, anchor: 'bottom' }).setLngLat(lngLat).addTo(this.map);
    (this._pins = this._pins || []).push(mk);
  };

  // Wait until the CURRENT frame's tiles are loaded AND painted, so a
  // frame-stepped export captures finished imagery instead of whatever the
  // realtime pacing happened to catch. Timeout keeps a stuck tile from
  // hanging the export (it captures the best available frame instead).
  VP._awaitFrameSettle = function (timeoutMs) {
    var map = this.map;
    return new Promise(function (res) {
      var done = false;
      function fin() {
        if (done) return; done = true;
        // one paint after idle so the captured canvas holds the frame
        requestAnimationFrame(function () { res(); });
      }
      try {
        if (map.loaded() && map.areTilesLoaded()) { fin(); return; }
      } catch (e) {}
      try { map.once('idle', fin); } catch (e) { fin(); return; }
      setTimeout(fin, timeoutMs || 1500);
    });
  };

  // ---- Loop export, MP4 first: WebCodecs H.264 + faststart muxing via
  // LoopExport (plays and saves everywhere, incl. iOS/Safari which has no
  // webm recorder at all). Frame-stepped: show frame -> settle -> caller
  // composites -> encode. Falls back to the legacy realtime WebM recorder
  // when WebCodecs/avc1/muxer are unavailable. Client-only, no server. ----
  VP.exportLoop = function (opts) {
    opts = opts || {};
    var self = this;
    if (!this.frames.length) {
      if (opts.onError) opts.onError('no frames'); return;
    }
    var fps = opts.fps || 8;
    var N = Math.min(this.frames.length, opts.maxFrames || 90);
    var canvas = opts.captureCanvas || this.map.getCanvas();
    var fallback = function () { self.exportWebM(opts); };
    if (typeof window.LoopExport === 'undefined'
        || !window.LoopExport.available()) { fallback(); return; }
    window.LoopExport.create({
      width: canvas.width, height: canvas.height, fps: fps, frames: N,
      maxBytes: opts.maxBytes || 9e6, maxBitrate: opts.maxBitrate || 6e6
    }).then(function (enc) {
      if (!enc) { fallback(); return; }
      var i = 0, start = self.frames.length - N;
      if (opts.onProgress) opts.onProgress(0, N);
      function fail(e) {
        try { enc.abort(); } catch (e2) {}
        if (opts.onError) opts.onError('export failed');
      }
      (function step() {
        self.showFrame(start + i);
        self._awaitFrameSettle(1500).then(function () {
          try { if (opts.drawFrame) opts.drawFrame(); } catch (e) {}
          enc.addFrame(canvas).then(function () {
            i++;
            if (opts.onProgress) opts.onProgress(i, N);
            if (i < N) { step(); return; }
            enc.finish().then(function (out) {
              if (opts.onDone) opts.onDone(out.blob, out.ext);
            }, fail);
          }, fail);
        });
      })();
    }, fallback);
  };

  // ---- Legacy 90-frame export: encode the loaded frames to WebM (true palette,
  // no GIF palette drift; Discord-safe <=10 MB at viewer res). Kept as the
  // fallback for browsers without WebCodecs H.264. ----
  VP.exportWebM = function (opts) {
    opts = opts || {};
    if (!this.frames.length || typeof MediaRecorder === 'undefined') {
      if (opts.onError) opts.onError('video export unsupported here'); return;
    }
    var self = this, map = this.map, fps = opts.fps || 8;
    var N = Math.min(this.frames.length, opts.maxFrames || 90);
    // opts.captureCanvas: record a caller-composited canvas (e.g. map + the
    // cockpit's branded chrome) instead of the raw map canvas.
    var canvas = opts.captureCanvas || map.getCanvas();
    var stream = canvas.captureStream(fps);
    var mime = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm']
      .filter(function (t) { return MediaRecorder.isTypeSupported(t); })[0] || 'video/webm';
    // budget the bitrate so N/fps seconds stays comfortably under the byte
    // budget (default ~9 MB = Discord-safe; the cockpit's HQ mode raises it).
    var secs = Math.max(1, N / fps);
    var bitrate = Math.min(opts.maxBitrate || 6e6,
                           Math.floor((opts.maxBytes || 9e6) * 8 / secs));
    var rec;
    try {
      rec = new MediaRecorder(stream, { mimeType: mime,
                                        videoBitsPerSecond: bitrate });
    } catch (e) {
      // Safari has MediaRecorder but no webm codec — surface it instead of
      // throwing out of the click handler.
      if (opts.onError) opts.onError('video export unsupported here');
      return;
    }
    var chunks = [];
    rec.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
    rec.onstop = function () {
      var blob = new Blob(chunks, { type: 'video/webm' });
      if (opts.onDone) { opts.onDone(blob); return; }
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = (self.manifest.product.replace(/\//g, '-')) + '_loop.webm';
      a.click();
    };
    var i = 0, start = this.frames.length - N, dwell = 1000 / fps;
    if (opts.onProgress) opts.onProgress(0, N);
    rec.start();
    (function step() {
      self.showFrame(start + i); i++;
      if (opts.onProgress) opts.onProgress(i, N);
      if (i < N) setTimeout(step, dwell);
      else setTimeout(function () { try { rec.stop(); } catch (e) {} }, dwell * 2);
    })();
  };

  // Link N TiledViewers into a COMPARE group: ONE camera (AOI) + ONE clock across
  // all panes (feedback-guarded). Each pane keeps its own product / BT / manifest,
  // so it generalizes to product-vs-product; here the panes share a product and
  // demonstrate the time-locked, AOI-locked mechanics.
  function syncViewers(viewers) {
    var syncing = false;
    viewers.forEach(function (v) {
      v.map.on('move', function () {
        if (syncing) return; syncing = true;
        var c = v.map.getCenter(), z = v.map.getZoom();
        viewers.forEach(function (o) { if (o !== v) o.map.jumpTo({ center: c, zoom: z }); });
        syncing = false;
      });
    });
    var playing = false, raf = null, last = 0, fps = 6;
    var n0 = function () { return viewers[0].frames.length; };
    // ONE clock, synced by TIME not index: pane 0 leads; every other pane shows
    // its own nearest-in-time frame (panes hold different products whose stamp
    // lists can differ in cadence/coverage).
    function syncTo(idx) {
      var lead = viewers[0], n = lead.frames.length;
      if (!n) return;
      idx = ((idx % n) + n) % n;
      lead.showFrame(idx);
      // Use the REQUESTED stamp, not lead.frameIdx -- on an uncached frame the
      // lead updates frameIdx asynchronously (after tiles load), and re-reading
      // it here would sync every follower to the lead's PREVIOUS frame.
      var stamp = lead.frames[idx];
      for (var k = 1; k < viewers.length; k++) viewers[k].showStamp(stamp);
    }
    function step(t) {
      if (!playing) return;
      if (!last) last = t;
      if (t - last >= 1000 / fps) {
        last = t;
        syncTo((viewers[0].frameIdx + 1) % n0());
      }
      raf = requestAnimationFrame(step);
    }
    return {
      viewers: viewers,
      showFrame: function (i) { syncTo(i); },
      play: function () { if (playing || n0() < 2) return; playing = true; last = 0; raf = requestAnimationFrame(step); },
      pause: function () { playing = false; if (raf) cancelAnimationFrame(raf); },
      toggle: function () { if (playing) { this.pause(); } else { this.play(); } return playing; },
      fitData: function () { viewers.forEach(function (v) { v.fitData(); }); },
      gotoRegion: function (k) { viewers.forEach(function (v) { v.gotoRegion(k); }); },
      setLayer: function (k, on) { viewers.forEach(function (v) { v.setLayer(k, on); }); }
    };
  }

  if (typeof window !== 'undefined') { window.TiledViewer = TiledViewer; window.syncViewers = syncViewers; }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { TiledViewer: TiledViewer, syncViewers: syncViewers,
      _test: { manifestBase: manifestBase, frameTiles: frameTiles, graticule: graticule } };
  }
})();
