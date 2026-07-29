/* cockpit_fields.js — Microwave + Scatterometer as NATIVE cockpit fields and
 * layers (retires the ?embed=1 stage takeover). The fetch/product/legend/barb
 * logic is RE-HOSTED from the existing viewers, not rebuilt:
 *   - /microwave/microwave.js exports PRODUCTS/tileRel/boundsOf — the same
 *     manifest -> overpasses -> chrome-free georeferenced tile model.
 *   - /ascat/ascat.js exports STYLES/KT scales/drawBarb — the same manifest ->
 *     per-pass WVC arrays -> screen-space-thinned barbs.
 * Rendering into the cockpit's MapLibre panes:
 *   - MW: an overpass product tile becomes a maplibre `image` source
 *     georeferenced by bounds_wgs84, inserted below the vector furniture —
 *     pan/zoom/camera-linking come for free; smoothed/raw = raster-resampling.
 *   - ASCAT: barbs draw on a per-pane overlay canvas synced to the camera
 *     (map.project per render frame) — the barb painter is the legacy one.
 * Both work as a pane FIELD (imagery hidden) or a LAYER over any base field.
 * Per-pane settings; controls live in the left rail (MW / Scatterometer tabs)
 * and act on the ACTIVE pane. ASCAT style DEFAULTS to high-contrast.
 */
(function () {
  'use strict';

  var CDN = 'https://cdn.triple-a-tropics.com';
  var MW_BASE = CDN + '/microwave';
  var SC_BASE = CDN + '/ascat';
  var UHR_BASE = CDN + '/ascat/uhr';
  var $ = function (id) { return document.getElementById(id); };

  var CX = null;           // cockpit state (window.__cockpit)
  var H = {};              // cockpit helpers {flash, renderPaneChrome, ...}

  var MW_KEYMAP = { 'mw-91c': 'color91', 'mw-91h': '91H', 'mw-37c': 'color37', 'mw-37h': '37H' };
  var MW_LABELS = { color37: '37 GHz color', color91: '91 GHz color', '37H': '37H BT', '91H': '91H BT' };

  // ========================================================================
  // shared data stores (one fetch per manifest, all panes share)
  // ========================================================================
  var MWData = {
    manifest: null, ops: {}, _p: null,
    load: function () {
      var self = this;
      if (this._p) return this._p;
      this._p = fetch(MW_BASE + '/manifest.json?t=' + Date.now(), { cache: 'no-store' })
        .then(function (r) { if (!r.ok) throw 0; return r.json(); })
        .then(function (m) { self.manifest = m; return m; });
      return this._p;
    },
    overpasses: function (slug) {
      var self = this;
      if (this.ops[slug]) return Promise.resolve(this.ops[slug]);
      return fetch(MW_BASE + '/' + slug + '/overpasses.json', { cache: 'default' })
        .then(function (r) { if (!r.ok) throw 0; return r.json(); })
        .then(function (doc) {
          self.ops[slug] = (doc && doc.overpasses) || [];
          return self.ops[slug];
        });
    }
  };
  // MRMS radar overlay (tester item #11): a single web-mercator-warped RGBA
  // WebP of the newest MergedReflectivityQCComposite scan, emitted by
  // update-mrms.yml (NOAA noaa-mrms-pds, TAT-radar.pal), CONUS to start.
  // Same honest-gate as MW/ASCAT: the toggle un-greys iff this manifest
  // fetch succeeds; reload() drives the 60 s freshness poll.
  var MRMS_BASE = CDN + '/radar/mrms/conus';
  var MRMSData = {
    manifest: null, _p: null,
    load: function () {
      var self = this;
      if (this._p) return this._p;
      this._p = fetch(MRMS_BASE + '/latest_times.json?t=' + Date.now(), { cache: 'no-store' })
        .then(function (r) { if (!r.ok) throw 0; return r.json(); })
        .then(function (m) { self.manifest = m; return m; });
      return this._p;
    },
    reload: function () {
      this._p = null;
      return this.load();
    }
  };
  // MIMIC-TPW2 total precipitable water (CIMSS/SSEC) — hourly global frames
  // from the box poller. LIVE layer only: fresh() gates the toggle so a
  // stalled upstream shows a disabled button, never 13-day-old moisture
  // presented as current. Self-activates when the mirror resumes.
  var TPW_BASE = CDN + '/env/tpw';
  var TPW_FRESH_MS = 3 * 3600e3;
  var TPWData = {
    manifest: null, _p: null,
    load: function () {
      var self = this;
      if (this._p) return this._p;
      this._p = fetch(TPW_BASE + '/latest_times.json?t=' + Date.now(), { cache: 'no-store' })
        .then(function (r) { if (!r.ok) throw 0; return r.json(); })
        .then(function (m) { self.manifest = m; return m; });
      return this._p;
    },
    reload: function () {
      this._p = null;
      return this.load();
    },
    fresh: function () {
      var m = this.manifest;
      if (!m || !m.latest) return false;
      return (Date.now() - radStampMs(m.latest)) <= TPW_FRESH_MS;
    }
  };
  // METAR surface obs (tester item #12): one compact global JSON emitted by
  // update-metar.yml (aviationweather.gov cache — free, global, no creds).
  // Same honest-gate + poll model as MRMS.
  var OBS_BASE = CDN + '/obs/metar';
  // Series-aware store (the MRMS treatment): a latest_times manifest + one
  // immutable frame per emit, LRU-cached client-side so the playback join
  // re-uses decoded frames. Falls back to the legacy static latest.json
  // when the manifest doesn't exist yet (deploy-order safety).
  function seriesStore(base, lru) {
    return {
      manifest: null, doc: null, _p: null, _frames: {}, _order: [],
      load: function () {
        var self = this;
        if (this._p) return this._p;
        this._p = fetch(base + '/latest_times.json?t=' + Date.now(), { cache: 'no-store' })
          .then(function (r) { if (!r.ok) throw 0; return r.json(); })
          .then(function (m) {
            self.manifest = m;
            return self.frame(m.latest).then(function (d) {
              self.doc = d;
              return m;
            });
          })
          .catch(function () {
            // legacy fallback: pre-series feed shape
            return fetch(base + '/latest.json?t=' + Date.now(), { cache: 'no-store' })
              .then(function (r) { if (!r.ok) throw 0; return r.json(); })
              .then(function (d) { self.doc = d; self.manifest = null; return null; });
          });
        return this._p;
      },
      reload: function () {
        this._p = null;
        return this.load();
      },
      frame: function (t) {
        var self = this;
        if (!t) return Promise.resolve(this.doc);
        if (this._frames[t]) return this._frames[t];
        var p = fetch(base + '/' + t + '.json')
          .then(function (r) { if (!r.ok) throw 0; return r.json(); })
          .catch(function () {
            // forget BOTH maps on failure so a later join retries cleanly —
            // leaving t in _order accumulated ghost entries that could
            // evict the entry just inserted and return undefined into the
            // playback clock (review-caught crash; reproduced in sim)
            delete self._frames[t];
            var oi = self._order.indexOf(t);
            if (oi >= 0) self._order.splice(oi, 1);
            return null;
          });
        this._frames[t] = p;
        if (this._order.indexOf(t) < 0) this._order.push(t);
        while (this._order.length > lru) {
          var ev = this._order.shift();
          if (ev === t) { this._order.push(ev); break; }  // never evict the newcomer
          delete this._frames[ev];
        }
        return p;
      },
      nearest: function (satStamp, skewMs) {
        var m = this.manifest;
        if (!m || !m.times || !m.times.length || !satStamp) return null;
        var t = radStampMs(satStamp), best = null, bd = Infinity;
        for (var i = 0; i < m.times.length; i++) {
          var d = Math.abs(radStampMs(m.times[i]) - t);
          if (d < bd) { bd = d; best = m.times[i]; }
        }
        return (best != null && bd <= skewMs) ? best : null;
      }
    };
  }
  var OBSData = seriesStore(OBS_BASE, 44);   // >= a full playback lap of the
  // 30-frame server series — an LRU smaller than the loop's join set
  // refetches+reparses every frame JSON at each wrap (stutter fuel)
  // WPC surface analysis (tester item #13): fronts + pressure centers from
  // the coded CODSUS bulletin, emitted by update-sfc-analysis.yml.
  var SFC_BASE = CDN + '/sfc/analysis';
  var SFCData = seriesStore(SFC_BASE, 12);
  // NHC products overlay: cones + formation areas from the emitted feed;
  // current-position icons REUSE the site's global storm feed (the same
  // marker classification the home map renders — one truth, every map)
  var NHCData = {
    doc: null, _p: null,
    load: function () {
      var self = this;
      if (this._p) return this._p;
      this._p = fetch(CDN + '/nhc/overlay/latest.json?t=' + Date.now(), { cache: 'no-store' })
        .then(function (r) { if (!r.ok) throw 0; return r.json(); })
        .then(function (d) { self.doc = d; return d; });
      return this._p;
    },
    reload: function () { this._p = null; return this.load(); }
  };
  var GSData = {
    doc: null, _p: null,
    load: function () {
      var self = this;
      if (this._p) return this._p;
      this._p = fetch(CDN + '/global_storms.geojson?t=' + Date.now(), { cache: 'no-store' })
        .then(function (r) { if (!r.ok) throw 0; return r.json(); })
        .then(function (d) { self.doc = d; return d; });
      return this._p;
    },
    reload: function () { this._p = null; return this.load(); }
  };
  var SCData = {
    manifest: null, loaded: {}, _p: null,
    load: function () {
      var self = this;
      if (this._p) return this._p;
      var main = fetch(SC_BASE + '/manifest.json?t=' + Date.now(), { cache: 'no-store' })
        .then(function (r) { if (!r.ok) throw 0; return r.json(); });
      // UHR companion feed (~2 km-class storm cuts, same schema + uhr flag).
      // Optional by design: its absence must never gate the operational feed.
      var uhr = fetch(UHR_BASE + '/manifest.json?t=' + Date.now(), { cache: 'no-store' })
        .then(function (r) { if (!r.ok) throw 0; return r.json(); })
        .catch(function () { return null; });
      this._p = Promise.all([main, uhr]).then(function (ms) {
        var m = ms[0], u = ms[1];
        if (u && u.passes && u.passes.length) {
          m.passes = (m.passes || []).concat(u.passes).sort(function (a, b) {
            return String(b.start_utc || '').localeCompare(String(a.start_utc || ''));
          });
        }
        self.manifest = m;
        return m;
      });
      return this._p;
    },
    pass: function (id) {
      var self = this;
      if (this.loaded[id]) return Promise.resolve(this.loaded[id]);
      var base = String(id).indexOf('uhr_') === 0 ? UHR_BASE : SC_BASE;
      return fetch(base + '/' + id + '.json', { cache: 'default' })
        .then(function (r) { if (!r.ok) throw 0; return r.json(); })
        .then(function (p) { self.loaded[id] = p; return p; });
    },
    storms: function () {
      // unique tagged storms across passes (mirrors AscatViewer._buildStorms)
      var seen = {}, out = [];
      ((this.manifest && this.manifest.passes) || []).forEach(function (p) {
        (p.storms || []).forEach(function (s) {
          var key = String(s.slug || s.atcf || s.name || '').toLowerCase();
          if (!key || seen[key]) { if (key) seen[key].n++; return; }
          seen[key] = { key: key, name: s.name || s.atcf || s.slug, n: 1,
                        lat: s.lat, lon: s.lon };
          out.push(seen[key]);
        });
      });
      return out;
    }
  };

  // ========================================================================
  // MW per-pane rendering (maplibre image sources)
  // ========================================================================
  function mwDefaults() {
    return { slug: null, opIdx: -1, product: '91H', raw: false, pinned: false };
  }
  function mwState(pane) {
    if (!pane.mw) pane.mw = mwDefaults();
    return pane.mw;
  }
  function mwClearLayers(pane) {
    var map = pane.tv && pane.tv.map;
    if (!map) return;
    if (pane._mwPending) { map.off('sourcedata', pane._mwPending); pane._mwPending = null; }
    (pane._mwLayers || []).forEach(function (id) {
      if (map.getLayer(id)) map.removeLayer(id);
      if (map.getSource(id)) map.removeSource(id);
    });
    pane._mwLayers = [];
  }
  function mwRender(pane, paneIdx) {
    var MV = window.MicrowaveViewer;
    var st = mwState(pane);
    var map = pane.tv && pane.tv.map;
    if (!map || !st.slug) return;
    MWData.overpasses(st.slug).then(function (ops) {
      if (!ops.length) { H.flash('no ' + st.slug + ' overpasses yet'); return; }
      if (st.opIdx < 0 || st.opIdx >= ops.length) st.opIdx = ops.length - 1;
      var o = ops[st.opIdx];
      var tr = MV.tileRel(o, st.product, st.raw);
      var b = MV.boundsOf(o);
      if (!tr || !b) { mwClearLayers(pane); H.flash(MW_LABELS[st.product] + ' not available for this pass'); mwChrome(pane, paneIdx); return; }
      // double-buffer the overpass swap: mount the NEW image under a versioned
      // id and only tear the old one down once the new source has decoded --
      // clearing first flashed the dark basemap on every clock-driven overpass
      // change (raster-fade-duration:0, image still fetching).
      if (pane._mwPending) { map.off('sourcedata', pane._mwPending); pane._mwPending = null; }
      var prev = pane._mwLayers || [];
      var id = 'ofmw-' + paneIdx + '-' + (pane._mwSeq = (pane._mwSeq || 0) + 1);
      map.addSource(id, {
        type: 'image', url: MW_BASE + '/' + tr.rel,
        coordinates: [[b[0], b[3]], [b[2], b[3]], [b[2], b[1]], [b[0], b[1]]]
      });
      var before = map.getLayer('grat') ? 'grat' : undefined;
      map.addLayer({ id: id, type: 'raster', source: id,
        paint: { 'raster-opacity': 1, 'raster-fade-duration': 0,
                 'raster-resampling': st.raw ? 'nearest' : 'linear' } }, before);
      // _mwLayers lists EVERY mounted mw id (old + new) until the swap lands,
      // so a mode exit mid-swap tears down both -- no ghost overpass.
      pane._mwLayers = prev.concat([id]);
      var dropPrev = function () {
        prev.forEach(function (old) {
          if (map.getLayer(old)) map.removeLayer(old);
          if (map.getSource(old)) map.removeSource(old);
        });
        pane._mwLayers = (pane._mwLayers || []).filter(function (x) {
          return prev.indexOf(x) < 0;
        });
      };
      if (!prev.length) { dropPrev(); }
      else {
        var onData = function (e) {
          if (e.sourceId !== id) return;
          if (!map.isSourceLoaded(id)) return;
          map.off('sourcedata', onData);
          if (pane._mwPending === onData) pane._mwPending = null;
          dropPrev();
        };
        pane._mwPending = onData;
        map.on('sourcedata', onData);
      }
      if (pane.kind === 'mw' && st.flyTo) {
        map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 30, duration: 500 });
        st.flyTo = false;
      }
      mwChrome(pane, paneIdx);
      syncControls();          // the overpass list resolves async — refill now
    }).catch(function () { H.flash('microwave data unavailable'); });
  }
  function mwChrome(pane, paneIdx) {
    if (H.renderPaneChrome) H.renderPaneChrome(paneIdx);
  }
  function mwCurrentOp(pane) {
    var st = pane.mw;
    if (!st || !st.slug) return null;
    var ops = MWData.ops[st.slug];
    if (!ops || !ops.length) return null;
    return ops[Math.max(0, Math.min(ops.length - 1, st.opIdx))];
  }

  // ========================================================================
  // SC per-pane rendering (camera-synced barb overlay canvas)
  // ========================================================================
  function scDefaults() {
    return { view: 'recent', passId: 'all', density: 'auto',
             style: 'highcontrast',        // per the integration brief: DEFAULT
             backdrop: 'clean',            // satellite under the barbs; colored
                                           // barbs need a calm gray stage
             pinned: false };
  }
  function scState(pane) {
    if (!pane.sc) pane.sc = scDefaults();
    if (!pane.sc.backdrop) pane.sc.backdrop = 'clean';
    return pane.sc;
  }
  // Backdrop products per option, in preference order for the pane's domain:
  // "clean" wants a NATIVE grayscale IR — C07/B07 3.9 µm shortwave (smooth
  // gray ramp, day+night) where the domain ships it, else the geo ring's
  // Dvorak-BD C13 (pure-gray stepped enhancement, the operational TC look).
  // "ir" is the rainbow C13 every domain carries. NO desaturation hacks:
  // rainbow_ir's cold-top luminance is non-monotonic, so a desaturated copy
  // would lie about relative cloud-top height.
  var SC_BACKDROP_PREFS = { clean: ['c07', 'b07', 'irbd'], ir: ['ir'] };
  function scBackdropProduct(pane) {
    var st = scState(pane);
    if (st.backdrop === 'none' || !H.productByKey) return null;
    var prefs = SC_BACKDROP_PREFS[st.backdrop] || SC_BACKDROP_PREFS.clean;
    for (var i = 0; i < prefs.length; i++) {
      var p = H.productByKey(prefs[i]);
      if (p && (!H.productAvailable || H.productAvailable(p))) return p;
    }
    return H.productByKey('ir');
  }
  function scApplyBackdrop(pane) {
    var tv = pane.tv;
    if (!tv) return;
    var p = scBackdropProduct(pane);
    if (!p) {
      // "none" (or helpers absent): the classic black stage
      pane._scBackdrop = null;
      if (pane._scSwapped && pane.product && H.manifestUrlFor) {
        pane._scSwapped = false;
        tv.setProduct(H.manifestUrlFor(pane.product), pane.product).catch(function () {});
      }
      tv.setImageryVisible(false);
      scDraw(pane);
      return;
    }
    pane._scBackdrop = p;
    // Swap the pane's tiles to the backdrop product WITHOUT touching
    // pane.product — clearPaneField restores the user's own field from it.
    var showing = pane._scSwapped ? pane._scShownKey
                                  : (pane.product && pane.product.key);
    if (showing === p.key) { tv.setImageryVisible(true); scDraw(pane); return; }
    pane._scSwapped = true;
    (H.manifestUrlFor ? tv.setProduct(H.manifestUrlFor(p), p) : Promise.reject())
      .then(function () {
        pane._scShownKey = p.key;
        tv.setImageryVisible(true);
        scDraw(pane);
      })
      .catch(function () { tv.setImageryVisible(false); });
  }
  function scCanvas(pane) {
    if (pane._scCanvas) return pane._scCanvas;
    var cv = document.createElement('canvas');
    cv.className = 'cx-sc-overlay';
    cv.style.cssText = 'position:absolute;inset:0;z-index:3;pointer-events:none;width:100%;height:100%';
    pane.el.appendChild(cv);
    pane._scCanvas = cv;
    return cv;
  }
  function scViewPasses(pane) {
    var st = scState(pane);
    var passes = ((SCData.manifest && SCData.manifest.passes) || []).slice();
    if (st.view !== 'recent') {
      passes = passes.filter(function (p) {
        return (p.storms || []).some(function (s) {
          return window.AscatViewer.stormMatch(s, st.view);
        });
      });
    }
    if (st.passId !== 'all') passes = passes.filter(function (p) { return p.id === st.passId; });
    return passes.slice(0, 6);   // MAX_PASSES parity with the legacy viewer
  }
  function scRender(pane, paneIdx) {
    var st = scState(pane);
    var map = pane.tv && pane.tv.map;
    if (!map) return;
    SCData.load().then(function () {
      var metas = scViewPasses(pane);
      return Promise.all(metas.map(function (m) { return SCData.pass(m.id).catch(function () { return null; }); }));
    }).then(function (passes) {
      pane._scPasses = passes.filter(Boolean);
      scFieldSync(pane);                    // UHR ~2 km field rasters
      scCanvas(pane);                       // ensure the overlay canvas exists
      if (!pane._scWired) {
        pane._scWired = true;
        map.on('render', function () { scDraw(pane); });
        // barb hover: the overlay canvas is pointer-events:none, so the map
        // beneath still gets mousemove — nearest thinned cell within 14 px
        map.on('mousemove', function (e) { scHover(pane, e); });
        pane.el.addEventListener('mouseleave', function () {
          if (pane._scTip) pane._scTip.style.display = 'none';
        });
      }
      // storm view: frame the storm once
      if (pane.kind === 'sc' && st.flyTo) {
        st.flyTo = false;
        var target = null;
        if (st.view !== 'recent') {
          SCData.storms().forEach(function (s) { if (s.key === st.view && s.lat != null) target = s; });
        }
        if (target) {
          map.fitBounds([[target.lon - 8, target.lat - 6], [target.lon + 8, target.lat + 6]],
            { padding: 20, duration: 500 });
        } else if (pane._scPasses.length) {
          // recent composite: fit the newest pass's populated area is too big
          // (global orbit) — leave the camera; the region rail frames it.
        }
      }
      scDraw(pane);
      if (H.renderPaneChrome) H.renderPaneChrome(paneIdx);
    }).catch(function () { H.flash('ASCAT data unavailable'); });
  }
  // UHR passes carry a baked ~2 km wind-speed FIELD (same stepped kt classes
  // as the barbs/legend) — mounted as in-GL image rasters under the barbs,
  // one per drawn UHR pass, torn down when the pass leaves the drawn set or
  // the layer goes off. Operational passes have no field: barbs only.
  function scFieldSync(pane) {
    var map = pane.tv && pane.tv.map;
    if (!map) return;
    var paneIdx = (CX && CX.panes) ? CX.panes.indexOf(pane) : 0;
    var st = pane.sc;
    var active = pane.kind === 'sc' || (st && st.on);
    var want = {};
    if (active && pane._scPasses) {
      pane._scPasses.forEach(function (p) {
        if (p && p.uhr && p.field && p.field.bounds && p.field.file) {
          want['ofscf-' + paneIdx + '-' + p.id] = p;
        }
      });
    }
    var cur = pane._scFieldLayers || [];
    cur.forEach(function (id) {
      if (!want[id]) {
        if (map.getLayer(id)) map.removeLayer(id);
        if (map.getSource(id)) map.removeSource(id);
      }
    });
    var kept = cur.filter(function (id) { return !!want[id]; });
    Object.keys(want).forEach(function (id) {
      if (kept.indexOf(id) >= 0) return;
      var p = want[id], b = p.field.bounds;   // [W,S,E,N]
      var base = String(p.id).indexOf('uhr_') === 0 ? UHR_BASE : SC_BASE;
      try {
        map.addSource(id, { type: 'image', url: base + '/' + p.field.file,
          coordinates: [[b[0], b[3]], [b[2], b[3]], [b[2], b[1]], [b[0], b[1]]] });
        var before = map.getLayer('grat') ? 'grat' : undefined;
        map.addLayer({ id: id, type: 'raster', source: id,
          paint: { 'raster-opacity': 0.82, 'raster-fade-duration': 0,
                   'raster-resampling': 'linear' } }, before);
        kept.push(id);
      } catch (e) { /* map mid-teardown: next render re-syncs */ }
    });
    pane._scFieldLayers = kept;
  }
  function scFieldRaise(pane) {
    var map = pane.tv && pane.tv.map;
    if (!map || !map.getLayer('grat')) return;
    (pane._scFieldLayers || []).forEach(function (id) {
      try { if (map.getLayer(id)) map.moveLayer(id, 'grat'); } catch (e) {}
    });
  }
  function scDraw(pane) {
    var AV = window.AscatViewer;
    var st = pane.sc;
    var cv = pane._scCanvas;
    var map = pane.tv && pane.tv.map;
    if (!st || !cv || !map || !pane._scPasses) return;
    var active = pane.kind === 'sc' || st.on;
    var box = pane.el.getBoundingClientRect();
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    cv.width = Math.round(box.width * dpr); cv.height = Math.round(box.height * dpr);
    var g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, box.width, box.height);
    pane._scCells = null;
    if (!active || !pane._scPasses.length) {
      if (pane._scTip) pane._scTip.style.display = 'none';
      return;
    }

    var style = AV.STYLES[st.style] || AV.STYLES.highcontrast;
    var scale = style.scale;
    var step = AV.DENSITY[st.density] || AV.DENSITY.auto;
    var bounds = map.getBounds();
    var w = bounds.getWest(), e = bounds.getEast(), s = bounds.getSouth(), n = bounds.getNorth();
    // screen-space thinning grid; newest pass wins a contested cell (drawn
    // last <=> iterate oldest first) — the legacy viewer's exact model
    var grid = {};
    var passes = pane._scPasses.slice().sort(function (a, b) {
      return (Date.parse(a.start_utc) || 0) - (Date.parse(b.start_utc) || 0);
    });
    for (var pi = 0; pi < passes.length; pi++) {
      var p = passes[pi], wv = p.wvc;
      if (!wv || !wv.la) continue;
      var la = wv.la, lo = wv.lo, kt = wv.kt, dr = wv.dir, N = la.length;
      for (var i = 0; i < N; i++) {
        var lat = la[i]; if (lat < s || lat > n) continue;
        var lon = lo[i];
        if (lon < w && lon + 360 <= e) lon += 360;
        if (lon < w || lon > e) continue;
        var xy = map.project([lon, lat]);
        if (xy.x < -20 || xy.y < -20 || xy.x > box.width + 20 || xy.y > box.height + 20) continue;
        grid[(Math.floor(xy.x / step)) + ':' + (Math.floor(xy.y / step))] =
          { x: xy.x, y: xy.y, kt: kt[i], dir: dr[i], lat: la[i], lon: lo[i],
            t: p.start_utc, sensor: p.sensor };
      }
    }
    g.lineJoin = 'round'; g.lineCap = 'round';
    // dark casing whenever imagery sits underneath: SC as a LAYER over any
    // tile field, or the SC FIELD's own satellite backdrop
    var halo = pane.kind !== 'sc' || st.backdrop !== 'none';
    var cells = Object.keys(grid);
    var kept = [];
    for (var c = 0; c < cells.length; c++) {
      var cell = grid[cells[c]];
      kept.push(cell);
      if (halo) AV.drawBarb(g, cell.x, cell.y, cell.kt, cell.dir, 'rgba(5,10,20,0.82)', style.barbLw + 2.4);
      AV.drawBarb(g, cell.x, cell.y, cell.kt, cell.dir, windColor(scale, cell.kt), style.barbLw);
    }
    pane._scCells = kept;   // hover hit-test set (screen-space, this frame)
  }
  function scTip(pane) {
    if (pane._scTip) return pane._scTip;
    var d = document.createElement('div');
    d.className = 'cx-sc-tip';
    pane.el.appendChild(d);
    pane._scTip = d;
    return d;
  }
  function scHover(pane, e) {
    var st = pane.sc;
    var active = st && (pane.kind === 'sc' || st.on);
    var cells = pane._scCells;
    if (!active || !cells || !cells.length) {
      if (pane._scTip) pane._scTip.style.display = 'none';
      return;
    }
    var best = null, bd = 14 * 14;   // the legacy viewer's 14 px pick radius
    for (var i = 0; i < cells.length; i++) {
      var dx = cells[i].x - e.point.x, dy = cells[i].y - e.point.y;
      var d2 = dx * dx + dy * dy;
      if (d2 < bd) { bd = d2; best = cells[i]; }
    }
    var tip = scTip(pane);
    if (!best) { tip.style.display = 'none'; return; }
    var ktS = (best.kt == null || isNaN(best.kt)) ? '—' : String(Math.round(best.kt));
    var dirS = (best.dir == null || isNaN(best.dir)) ? '—' : String(Math.round(best.dir));
    var lonN = best.lon;
    while (lonN > 180) lonN -= 360;
    while (lonN < -180) lonN += 360;
    tip.innerHTML = '<b>' + ktS + ' kt</b> from ' + dirS + '°<br>' +
      Math.abs(best.lat).toFixed(1) + '°' + (best.lat < 0 ? 'S' : 'N') + ' ' +
      Math.abs(lonN).toFixed(1) + '°' + (lonN < 0 ? 'W' : 'E') +
      ' · ' + (best.sensor || 'ASCAT') + '<br>' + fmtZ(best.t);
    tip.style.display = 'block';
    var box = pane.el.getBoundingClientRect();
    tip.style.left = Math.max(0, Math.min(e.point.x + 14, box.width - 180)) + 'px';
    tip.style.top = Math.max(0, Math.min(e.point.y + 14, box.height - 64)) + 'px';
  }
  function windColor(scale, kt) {
    if (kt == null || isNaN(kt)) return '#8ea2bd';
    var col = scale[0][1];
    for (var i = 0; i < scale.length; i++) if (kt >= scale[i][0]) col = scale[i][1];
    return col;
  }
  function scNewest(pane) {
    if (!pane._scPasses || !pane._scPasses.length) return null;
    return pane._scPasses.slice().sort(function (a, b) {
      return (Date.parse(b.start_utc) || 0) - (Date.parse(a.start_utc) || 0);
    })[0];
  }

  // ========================================================================
  // pane field / layer switching (called from cockpit.js)
  // ========================================================================
  function setPaneField(i, key) {
    var pane = CX.panes[i];
    if (!pane || !pane.tv || !pane.tv.map) return;
    var kind = key.slice(0, 2);           // 'mw' | 'sc'
    pane.kind = kind;
    pane.fieldKey = key;
    // MW blanks the tiles (the overpass cutout IS the imagery); SC keeps a
    // satellite backdrop under the barbs per its backdrop setting
    if (kind === 'mw') pane.tv.setImageryVisible(false);
    else scApplyBackdrop(pane);
    if (kind === 'mw') {
      var st = mwState(pane);
      st.product = MW_KEYMAP[key] || st.product;
      st.flyTo = true;
      MWData.load().then(function (m) {
        if (!st.slug && m.storms && m.storms.length) {
          st.slug = (m.default_slug) || m.storms[0].slug;
        }
        mwRender(pane, i);
        syncControls();
      });
      // a pane can't be both; drop any sc layer canvas drawing
      if (pane.sc) pane.sc.on = pane.sc.on && kind === 'sc';
    } else {
      var sst = scState(pane);
      sst.flyTo = key === 'sc-storm';
      if (key === 'sc-storm') {
        SCData.load().then(function () {
          var storms = SCData.storms();
          if (storms.length) sst.view = storms[0].key;
          scRender(pane, i);
          syncControls();
        });
      } else {
        sst.view = 'recent';
        scRender(pane, i);
        syncControls();
      }
      mwClearLayers(pane);
    }
    if (H.markFieldActive) H.markFieldActive();
    if (H.renderPaneChrome) H.renderPaneChrome(i);
  }
  function clearPaneField(i) {
    var pane = CX.panes[i];
    if (!pane) return;
    pane.kind = 'tile';
    pane.fieldKey = pane.product ? pane.product.key : null;
    // the SC backdrop swapped the tv's tiles without touching pane.product —
    // put the user's own field back before re-showing imagery
    if (pane._scSwapped && pane.product && pane.tv && H.manifestUrlFor) {
      pane._scSwapped = false;
      pane._scShownKey = null;
      pane.tv.setProduct(H.manifestUrlFor(pane.product), pane.product).catch(function () {});
    }
    if (pane._scTip) pane._scTip.style.display = 'none';
    if (pane.tv) pane.tv.setImageryVisible(true);
    if (!(pane.mw && pane.mw.on)) mwClearLayers(pane);
    scDraw(pane);   // clears unless sc layer is on
    scFieldSync(pane);
    if (H.renderPaneChrome) H.renderPaneChrome(i);
  }
  // ========================================================================
  // MRMS radar layer (maplibre image source; MW's double-buffer discipline)
  // ========================================================================
  function radState(pane) {
    if (!pane.rad) pane.rad = { on: false, stamp: null };
    return pane.rad;
  }
  function radClearLayers(pane) {
    var map = pane.tv && pane.tv.map;
    if (!map) return;
    if (pane._radPending) { map.off('sourcedata', pane._radPending); pane._radPending = null; }
    (pane._radLayers || []).forEach(function (id) {
      if (map.getLayer(id)) map.removeLayer(id);
      if (map.getSource(id)) map.removeSource(id);
    });
    pane._radLayers = [];
    pane._radLoading = false; pane._radNext = null; pane._radUrl = null;
    if (pane.rad) { pane.rad.stamp = null; pane.rad.shown = null; }
  }
  function radRender(pane, paneIdx) {
    var map = pane.tv && pane.tv.map;
    if (!map) return;
    MRMSData.load().then(function () {
      radSyncTo(pane, paneIdx, radPaneStamp(pane));
      if (H.renderPaneChrome) H.renderPaneChrome(paneIdx);
    }).catch(function () { H.flash('radar data unavailable'); });
  }
  // the sat stamp this pane currently displays (fallback: newest scan)
  function radPaneStamp(pane) {
    var tv = pane.tv;
    return (tv && tv.frames && tv.frames[tv.frameIdx]) ||
           (MRMSData.manifest && MRMSData.manifest.latest) || null;
  }
  function radStampMs(s) {
    return Date.UTC(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8),
                    +s.slice(9, 11), +s.slice(11, 13), +s.slice(13, 15) || 0);
  }
  var RAD_MAX_SKEW_MS = 45 * 60e3;
  // TIME-LOCK: show the MRMS scan nearest the displayed sat frame. ONE
  // stable image source per pane driven by ImageSource.updateImage — the
  // sanctioned image-animation idiom (no per-frame source add/remove churn;
  // the old texture holds until the new image decodes, so swaps never
  // flash). Scans land ~10-min so frames repeat between updates — correct.
  // A sat frame with no scan within 45 min hides the layer for that frame
  // (honest: no radar existed "then" as far as the series knows).
  function radSyncTo(pane, paneIdx, satStamp) {
    var st = radState(pane);
    var map = pane.tv && pane.tv.map;
    var m = MRMSData.manifest;
    if (!st.on || !map || !m || !m.times || !m.times.length || !satStamp) return;
    var t = radStampMs(satStamp), best = null, bd = Infinity;
    for (var i = 0; i < m.times.length; i++) {
      var d = Math.abs(radStampMs(m.times[i]) - t);
      if (d < bd) { bd = d; best = m.times[i]; }
    }
    var id = 'ofrad-' + paneIdx;
    var lyr = map.getLayer(id);
    if (best == null || bd > RAD_MAX_SKEW_MS) {
      if (lyr) map.setLayoutProperty(id, 'visibility', 'none');
      st.shown = null;
      return;
    }
    var b = m.bounds;   // [W,S,E,N]; image web-mercator warped
    var coords = [[b[0], b[3]], [b[2], b[3]], [b[2], b[1]], [b[0], b[1]]];
    // ANIMATION VARIANT: during playback the half-res scan animates (a
    // full-res 7000x3500 texture is a ~98 MB GPU upload per advance — the
    // visible radar stall in the 4-pane capture); the freshness poll swaps
    // the full-res back within a tick once paused.
    var playing = !!(pane.tv && (pane.tv.playing || pane.tv._extPlaying));
    var tmpl = (playing && m.image_small && m.small_since &&
                best >= m.small_since) ? m.image_small : m.image;
    var url = CDN + '/' + tmpl.replace('{t}', best);
    if (!map.getSource(id)) {
      map.addSource(id, { type: 'image', url: url, coordinates: coords });
      var before = map.getLayer('grat') ? 'grat' : undefined;
      map.addLayer({ id: id, type: 'raster', source: id,
        paint: { 'raster-opacity': 0.9, 'raster-fade-duration': 0,
                 'raster-opacity-transition': { duration: 0, delay: 0 },
                 'raster-resampling': 'linear' } }, before);
      pane._radLayers = [id];
      pane._radUrl = url;
    } else if (pane._radUrl !== url) {
      // SERIALIZED update: MapLibre replaces the pending image on every
      // updateImage call — issuing one per advance while decodes lag left
      // the texture stuck frames behind. One in flight; only the LATEST
      // wanted image is queued behind it.
      radApply(pane, map, id, url, coords);
    }
    if (lyr || map.getLayer(id)) {
      map.setLayoutProperty(id, 'visibility', 'visible');
      // RE-RAISE: imagery frame layers also insert before 'grat', so every
      // frame mounted after the radar lands ABOVE it — without this, the
      // radar is buried within one manifest refresh (and instantly on a
      // product switch) while its badge still claims it shows. The old
      // versioned-id path self-healed by re-adding per scan; the stable-id
      // lifecycle must re-raise explicitly. Cheap + idempotent per sync.
      try { if (map.getLayer('grat')) map.moveLayer(id, 'grat'); } catch (e) {}
    }
    if (st.shown !== best) {
      st.shown = best;
      st.stamp = best;                     // badge shows the displayed scan
      radPrefetch(m, best);
      if (H.renderPaneChrome) H.renderPaneChrome(paneIdx);
    }
  }
  function radApply(pane, map, id, url, coords) {
    if (pane._radLoading) { pane._radNext = { url: url, coords: coords }; return; }
    pane._radLoading = true;
    pane._radUrl = url;
    try { map.getSource(id).updateImage({ url: url, coordinates: coords }); }
    catch (e) { pane._radLoading = false; return; }   // mid-teardown: next sync remounts
    var onData = function (e) {
      if (e.sourceId !== id) return;
      if (!map.getSource(id)) { map.off('sourcedata', onData); pane._radLoading = false; return; }
      if (!map.isSourceLoaded(id)) return;
      map.off('sourcedata', onData);
      pane._radLoading = false;
      if (pane._radNext) {
        var nx = pane._radNext;
        pane._radNext = null;
        if (nx.url !== pane._radUrl) radApply(pane, map, id, nx.url, nx.coords);
      }
    };
    map.on('sourcedata', onData);
  }
  // warm the displayed scan's temporal neighbors into the HTTP cache so the
  // NEXT join boundary's updateImage is a disk hit, not a network fetch —
  // the scans are immutable-cached, so one warm fetch serves every replay
  var _radWarm = {};
  function radPrefetch(m, shown) {
    var i = m.times.indexOf(shown);
    [i - 1, i + 1].forEach(function (j) {
      if (j < 0 || j >= m.times.length) return;
      var t = m.times[j];
      var tmpls = [m.image];
      if (m.image_small && m.small_since && t >= m.small_since) tmpls.push(m.image_small);
      tmpls.forEach(function (tp) {
        var u = CDN + '/' + tp.replace('{t}', t);
        if (_radWarm[u]) return;
        _radWarm[u] = 1;
        try { fetch(u, { mode: 'cors' }).catch(function () {}); } catch (e) {}
      });
    });
  }
  // freshness poll: ONE timer for all panes; only fetches while some pane
  // shows the layer, so an untoggled cockpit costs nothing.
  var _radT = null;
  function radStartPoll() {
    if (_radT) return;
    _radT = setInterval(function () {
      if (typeof document !== 'undefined' && document.hidden) return;
      var any = false;
      (CX && CX.panes || []).forEach(function (p) { if (p && p.rad && p.rad.on) any = true; });
      if (!any) return;
      MRMSData.reload().then(function () {
        CX.panes.forEach(function (p, i) {
          if (p && p.rad && p.rad.on) radSyncTo(p, i, radPaneStamp(p));
        });
      }).catch(function () {});
    }, 60e3);
    if (_radT && _radT.unref) _radT.unref();
  }

  // ========================================================================
  // TPW moisture layer (MIMIC-TPW2, CIMSS/SSEC) — the radar image-source
  // discipline verbatim: ONE stable per-pane image source, serialized
  // updateImage swaps, nearest-frame time-lock. Hourly product, so the
  // join skew is 90 min; a sat frame with no TPW within that hides the
  // layer for that frame (honest: no moisture field existed "then").
  // ========================================================================
  function tpwState(pane) {
    if (!pane.tpw) pane.tpw = { on: false, stamp: null };
    return pane.tpw;
  }
  function tpwClearLayers(pane) {
    var map = pane.tv && pane.tv.map;
    if (map) {
      (pane._tpwLayers || []).forEach(function (id) {
        if (map.getLayer(id)) map.removeLayer(id);
        if (map.getSource(id)) map.removeSource(id);
      });
    }
    pane._tpwLayers = [];
    pane._tpwLoading = false; pane._tpwNext = null; pane._tpwUrl = null;
    if (pane._tpwCbar) { pane._tpwCbar.style.display = 'none'; }
    if (pane.tpw) { pane.tpw.stamp = null; pane.tpw.shown = null; }
  }
  function tpwRender(pane, paneIdx) {
    var map = pane.tv && pane.tv.map;
    if (!map) return;
    TPWData.load().then(function () {
      if (!TPWData.fresh()) {
        // upstream stalled mid-session: refuse to show stale moisture
        var st = tpwState(pane);
        st.on = false;
        H.flash('TPW feed is stale upstream — layer disabled until it resumes');
        if (H.renderPaneChrome) H.renderPaneChrome(paneIdx);
        if (window.CockpitFields) window.CockpitFields.syncControls();
        return;
      }
      tpwSyncTo(pane, paneIdx, radPaneStamp(pane));
      if (H.renderPaneChrome) H.renderPaneChrome(paneIdx);
    }).catch(function () { H.flash('TPW data unavailable'); });
  }
  var TPW_MAX_SKEW_MS = 90 * 60e3;
  function tpwSyncTo(pane, paneIdx, satStamp) {
    var st = tpwState(pane);
    var map = pane.tv && pane.tv.map;
    var m = TPWData.manifest;
    if (!st.on || !map || !m || !m.times || !m.times.length || !satStamp) return;
    var t = radStampMs(satStamp), best = null, bd = Infinity;
    for (var i = 0; i < m.times.length; i++) {
      var d = Math.abs(radStampMs(m.times[i]) - t);
      if (d < bd) { bd = d; best = m.times[i]; }
    }
    var id = 'oftpw-' + paneIdx;
    var lyr = map.getLayer(id);
    tpwEnsureCbar(pane, m);
    if (best == null || bd > TPW_MAX_SKEW_MS) {
      if (lyr) map.setLayoutProperty(id, 'visibility', 'none');
      if (pane._tpwCbar) pane._tpwCbar.style.display = 'none';
      st.shown = null;
      return;
    }
    var b = m.bounds;   // [W,S,E,N]; frames web-mercator warped like radar
    var coords = [[b[0], b[3]], [b[2], b[3]], [b[2], b[1]], [b[0], b[1]]];
    var url = CDN + '/' + m.frame.replace('{t}', best);
    if (!map.getSource(id)) {
      map.addSource(id, { type: 'image', url: url, coordinates: coords });
      var before = map.getLayer('grat') ? 'grat' : undefined;
      map.addLayer({ id: id, type: 'raster', source: id,
        paint: { 'raster-opacity': 0.78, 'raster-fade-duration': 0,
                 'raster-opacity-transition': { duration: 0, delay: 0 },
                 'raster-resampling': 'linear' } }, before);
      pane._tpwLayers = [id];
      pane._tpwUrl = url;
    } else if (pane._tpwUrl !== url) {
      tpwApply(pane, map, id, url, coords);
    }
    if (lyr || map.getLayer(id)) {
      map.setLayoutProperty(id, 'visibility', 'visible');
      // same frame-layer burial as radar: re-raise under the graticule
      try { if (map.getLayer('grat')) map.moveLayer(id, 'grat'); } catch (e) {}
      // radar reads above moisture when both are on (reflectivity is the
      // sparser, more urgent field)
      try {
        (pane._radLayers || []).forEach(function (rid) {
          if (map.getLayer(rid) && map.getLayer('grat')) map.moveLayer(rid, 'grat');
        });
      } catch (e) {}
    }
    if (pane._tpwCbar) pane._tpwCbar.style.display = 'block';
    if (st.shown !== best) {
      st.shown = best;
      st.stamp = best;                     // badge shows the joined frame
      tpwPrefetch(m, best);
      if (H.renderPaneChrome) H.renderPaneChrome(paneIdx);
    }
  }
  function tpwApply(pane, map, id, url, coords) {
    if (pane._tpwLoading) { pane._tpwNext = { url: url, coords: coords }; return; }
    pane._tpwLoading = true;
    pane._tpwUrl = url;
    try { map.getSource(id).updateImage({ url: url, coordinates: coords }); }
    catch (e) { pane._tpwLoading = false; return; }   // mid-teardown: next sync remounts
    var onData = function (e) {
      if (e.sourceId !== id) return;
      if (!map.getSource(id)) { map.off('sourcedata', onData); pane._tpwLoading = false; return; }
      if (!map.isSourceLoaded(id)) return;
      map.off('sourcedata', onData);
      pane._tpwLoading = false;
      if (pane._tpwNext) {
        var nx = pane._tpwNext;
        pane._tpwNext = null;
        if (nx.url !== pane._tpwUrl) tpwApply(pane, map, id, nx.url, nx.coords);
      }
    };
    map.on('sourcedata', onData);
  }
  var _tpwWarm = {};
  function tpwPrefetch(m, shown) {
    var i = m.times.indexOf(shown);
    [i - 1, i + 1].forEach(function (j) {
      if (j < 0 || j >= m.times.length) return;
      var u = CDN + '/' + m.frame.replace('{t}', m.times[j]);
      if (_tpwWarm[u]) return;
      _tpwWarm[u] = 1;
      try { fetch(u, { mode: 'cors' }).catch(function () {}); } catch (e) {}
    });
  }
  // right-side mm colorbar (the feed ships it rendered); sits BELOW the
  // product colorbar's top-right slot so the two never collide
  function tpwEnsureCbar(pane, m) {
    if (pane._tpwCbar || !m || !m.cbar) return;
    var img = document.createElement('img');
    img.className = 'cx-tpw-cbar';
    img.alt = 'TPW (mm)';
    img.src = CDN + '/' + m.cbar;
    img.style.display = 'none';
    pane.el.appendChild(img);
    pane._tpwCbar = img;
  }
  var _tpwT = null;
  function tpwStartPoll() {
    if (_tpwT) return;
    _tpwT = setInterval(function () {
      if (typeof document !== 'undefined' && document.hidden) return;
      var any = false;
      (CX && CX.panes || []).forEach(function (p) { if (p && p.tpw && p.tpw.on) any = true; });
      if (!any) return;
      TPWData.reload().then(function () {
        CX.panes.forEach(function (p, i) {
          if (p && p.tpw && p.tpw.on) tpwSyncTo(p, i, radPaneStamp(p));
        });
      }).catch(function () {});
    }, 120e3);
    if (_tpwT && _tpwT.unref) _tpwT.unref();
  }

  // ========================================================================
  // METAR station-plot layer (canvas overlay; the ASCAT camera-sync model:
  // per-pane pointer-events:none canvas redrawn on map 'render', projected
  // per station, screen-space declutter — the barb painter is the legacy
  // AscatViewer one)
  // ========================================================================
  function obsState(pane) {
    if (!pane.obs) pane.obs = { on: false };
    return pane.obs;
  }
  function obsCanvas(pane) {
    if (pane._obsCanvas) return pane._obsCanvas;
    var cv = document.createElement('canvas');
    cv.className = 'cx-obs-overlay';
    cv.style.cssText = 'position:absolute;inset:0;z-index:4;pointer-events:none;width:100%;height:100%';
    pane.el.appendChild(cv);
    pane._obsCanvas = cv;
    return cv;
  }
  function obsRender(pane, paneIdx) {
    var map = pane.tv && pane.tv.map;
    if (!map) return;
    OBSData.load().then(function () {
      obsCanvas(pane);
      if (!pane._obsWired) {
        pane._obsWired = true;
        map.on('render', function () { obsDraw(pane); });
      }
      obsDraw(pane);
      if (H.renderPaneChrome) H.renderPaneChrome(paneIdx);
    }).catch(function () { H.flash('surface obs unavailable'); });
  }
  // camera+size fingerprint for the overlay canvases: their 'render'-driven
  // redraws are full projection loops (thousands of map.project calls), and
  // playback fires 'render' on every opacity flip — skip when neither the
  // camera nor the joined doc changed since the last draw
  function camKey(map, box, dpr) {
    var c = map.getCenter();
    return map.getZoom().toFixed(5) + ':' + c.lng.toFixed(5) + ',' + c.lat.toFixed(5) +
      ':' + map.getBearing() + ':' + box.width + 'x' + box.height + ':' + dpr;
  }
  function obsDraw(pane) {
    var AV = window.AscatViewer;
    var st = pane.obs;
    var cv = pane._obsCanvas;
    var map = pane.tv && pane.tv.map;
    var doc = pane._obsDoc || OBSData.doc;
    if (!st || !cv || !map) return;
    var box = pane.el.getBoundingClientRect();
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var ck = camKey(map, box, dpr) + ':' + (st.on ? 1 : 0);
    if (pane._obsDrawDoc === doc && pane._obsDrawCam === ck) return;
    pane._obsDrawDoc = doc; pane._obsDrawCam = ck;
    if (cv.width !== Math.round(box.width * dpr)) cv.width = Math.round(box.width * dpr);
    if (cv.height !== Math.round(box.height * dpr)) cv.height = Math.round(box.height * dpr);
    var g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, box.width, box.height);
    if (!st.on || !doc || !doc.stations) return;
    var z = map.getZoom();
    // one full station model needs ~64x48 px; coarser cells zoomed out.
    var step = z < 4 ? 120 : z < 5.5 ? 88 : 64;
    var bounds = map.getBounds();
    var w = bounds.getWest(), e = bounds.getEast(), s = bounds.getSouth(), n = bounds.getNorth();
    var proj = function (o) {
      var lat = o[1];
      if (lat < s || lat > n) return null;
      var lon = o[2];
      if (lon < w && lon + 360 <= e) lon += 360;   // antimeridian nudge (SC parity)
      if (lon < w || lon > e) return null;
      var xy = map.project([lon, lat]);
      if (xy.x < -30 || xy.y < -30 || xy.x > box.width + 30 || xy.y > box.height + 30) return null;
      return xy;
    };
    // STABLE DECLUTTER: land stations and moored platforms are FIXED sites,
    // so the kept set is computed ONCE per camera from the newest series
    // doc in a fully deterministic order (rank desc, then station id) and
    // every frame draws exactly those stations at their canonical spots
    // with that frame's values. Re-decluttering per frame reshuffled the
    // winners as ranks/ages changed between emits — watched on a real loop
    // capture as land stations "jittering". Only ships (plat 1) place per
    // frame: they genuinely move, and they fill leftover cells without
    // ever displacing the stable set.
    var stable = pane._obsKeep;
    var sck = camKey(map, box, 1) + ':' + step + ':' +
              ((OBSData.doc && OBSData.doc.as_of) || '');
    if (!stable || stable.ck !== sck) {
      var cells = {}, keepIds = {};
      var ref = (OBSData.doc && OBSData.doc.stations) || [];
      var order = ref.slice().sort(function (a, b) {
        return (b[9] - a[9]) ||
               (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0);
      });
      for (var ri = 0; ri < order.length; ri++) {
        var ro = order[ri];
        if ((ro[11] || 0) === 1) continue;         // ships are per-frame
        var rxy = proj(ro);
        if (!rxy) continue;
        var rk = Math.floor(rxy.x / step) + ':' + Math.floor(rxy.y / step);
        if (!cells[rk]) { cells[rk] = 1; keepIds[ro[0]] = 1; }
      }
      stable = pane._obsKeep = { ck: sck, keep: keepIds, cells: cells };
    }
    var grid = {};
    var rows = doc.stations;   // [id,lat,lon,t,td,slp,wdir,wspd,gust,rank,age,plat]
    var i, o, xy, key;
    for (i = 0; i < rows.length; i++) {
      o = rows[i];
      if ((o[11] || 0) === 1 || !stable.keep[o[0]]) continue;
      xy = proj(o);
      if (!xy) continue;
      key = Math.floor(xy.x / step) + ':' + Math.floor(xy.y / step);
      if (!grid[key]) grid[key] = { x: xy.x, y: xy.y, o: o };
    }
    var ships = [];
    for (i = 0; i < rows.length; i++) if ((rows[i][11] || 0) === 1) ships.push(rows[i]);
    ships.sort(function (a, b) {          // deterministic per frame
      return (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0) ||
             (a[1] - b[1]) || (a[2] - b[2]);
    });
    for (i = 0; i < ships.length; i++) {
      o = ships[i];
      xy = proj(o);
      if (!xy) continue;
      key = Math.floor(xy.x / step) + ':' + Math.floor(xy.y / step);
      if (!grid[key] && !stable.cells[key]) grid[key] = { x: xy.x, y: xy.y, o: o };
    }
    g.lineJoin = 'round'; g.lineCap = 'round';
    var ids = z >= 5.5;
    g.font = '10px "Segoe UI", system-ui, sans-serif';
    var txt = function (str, tx, ty, color, align) {
      g.textAlign = align || 'right';
      g.lineWidth = 3; g.strokeStyle = 'rgba(5,10,20,0.85)';
      g.strokeText(str, tx, ty);
      g.fillStyle = color; g.fillText(str, tx, ty);
    };
    Object.keys(grid).forEach(function (k) {
      var cell = grid[k], o = cell.o, x = cell.x, y = cell.y;
      // marine platforms get a distinct center symbol under the barb:
      // filled diamond = moving ship (VOS), open diamond = moored buoy/C-MAN
      var plat = o[11] || 0;
      if (plat) {
        g.beginPath();
        g.moveTo(x, y - 5); g.lineTo(x + 5, y);
        g.lineTo(x, y + 5); g.lineTo(x - 5, y); g.closePath();
        g.lineWidth = 3; g.strokeStyle = 'rgba(5,10,20,0.85)'; g.stroke();
        if (plat === 1) { g.fillStyle = '#5bc8d5'; g.fill(); }
        else { g.lineWidth = 1.5; g.strokeStyle = '#5bc8d5'; g.stroke(); }
      }
      // barb: white over the SC dark-halo discipline; calm ring is built in
      if (o[7] != null && o[6] != null) {
        AV.drawBarb(g, x, y, o[7], o[6], 'rgba(5,10,20,0.82)', 3.4);
        AV.drawBarb(g, x, y, o[7], o[6], '#dfe8f2', 1.1);
      } else if (!plat) {
        g.beginPath(); g.arc(x, y, 2.2, 0, Math.PI * 2);
        g.strokeStyle = '#dfe8f2'; g.lineWidth = 1.1; g.stroke();
      }
      // the standard station model: T upper-left, Td lower-left, coded SLP
      // upper-right (last 3 digits of mb*10), ID lower-right at high zoom
      if (o[3] != null) txt(Math.round(o[3]) + '\u00b0', x - 6, y - 8, '#ff9d5c');
      if (o[4] != null) txt(Math.round(o[4]) + '\u00b0', x - 6, y + 14, '#3fcf6f');
      if (o[5] != null) {
        var code = String(Math.round(o[5] * 10) % 1000);
        while (code.length < 3) code = '0' + code;
        txt(code, x + 6, y - 8, '#dfe8f2', 'left');
      }
      if (ids) txt(o[0], x + 6, y + 14, '#8ea2bd', 'left');
    });
  }
  var OBS_MAX_SKEW_MS = 75 * 60e3;    // obs land ~10-min; hourly METARs
  function obsSyncTo(pane, paneIdx, satStamp) {
    var st = pane.obs;
    if (!st || !st.on || !satStamp) return;
    if (!OBSData.manifest) return;       // legacy static feed: nothing to join
    var best = OBSData.nearest(satStamp, OBS_MAX_SKEW_MS);
    if (best === st.shown) return;
    st.shown = best;
    if (best == null) {
      pane._obsDoc = { stations: [] };   // honest: no obs near this frame
      obsDraw(pane);
      if (H.renderPaneChrome) H.renderPaneChrome(paneIdx);
      return;
    }
    OBSData.frame(best).then(function (d) {
      if (pane.obs && pane.obs.shown === best && d) {
        pane._obsDoc = d;                // stale frame held until this lands
        obsDraw(pane);
        if (H.renderPaneChrome) H.renderPaneChrome(paneIdx);
      }
    });
  }
  var _obsT = null;
  function obsStartPoll() {
    if (_obsT) return;
    _obsT = setInterval(function () {
      if (typeof document !== 'undefined' && document.hidden) return;
      var any = false;
      (CX && CX.panes || []).forEach(function (p) { if (p && p.obs && p.obs.on) any = true; });
      if (!any) return;
      OBSData.reload().then(function () {
        CX.panes.forEach(function (p, i) {
          if (p && p.obs && p.obs.on) {
            p.obs.shown = undefined;               // force a fresh join
            obsSyncTo(p, i, radPaneStamp(p));
            obsDraw(p);
          }
        });
      }).catch(function () {});
    }, 300e3);
    if (_obsT && _obsT.unref) _obsT.unref();
  }

  // ========================================================================
  // Surface-analysis layer (canvas overlay): fronts drawn as smoothed
  // polylines with alternating pips + H/L pressure centers, TAT dark-theme
  // colors. Pips straddle the line (the coded bulletin does not carry the
  // frontal-movement side; asserting one would be fabrication — stationary
  // fronts alternate sides per the standard convention, which the coded
  // points DO support).
  // ========================================================================
  var SFC_COLORS = { cold: '#4da3ff', warm: '#ff6d7a', ocfnt: '#c58cff',
                     stnry: null, trof: '#ffc94d' };
  function sfcState(pane) {
    if (!pane.sfc) pane.sfc = { on: false };
    return pane.sfc;
  }
  function sfcCanvas(pane) {
    if (pane._sfcCanvas) return pane._sfcCanvas;
    var cv = document.createElement('canvas');
    cv.className = 'cx-sfc-overlay';
    cv.style.cssText = 'position:absolute;inset:0;z-index:3;pointer-events:none;width:100%;height:100%';
    pane.el.appendChild(cv);
    pane._sfcCanvas = cv;
    return cv;
  }
  function sfcRender(pane, paneIdx) {
    var map = pane.tv && pane.tv.map;
    if (!map) return;
    SFCData.load().then(function () {
      sfcCanvas(pane);
      if (!pane._sfcWired) {
        pane._sfcWired = true;
        map.on('render', function () { sfcDraw(pane); });
      }
      sfcDraw(pane);
      if (H.renderPaneChrome) H.renderPaneChrome(paneIdx);
    }).catch(function () { H.flash('surface analysis unavailable'); });
  }
  function sfcPip(g, x, y, ang, type, color) {
    // type 'tri' (cold) | 'semi' (warm); ang = perpendicular direction
    var s = 5.5;
    g.save();
    g.translate(x, y); g.rotate(ang);
    g.beginPath();
    if (type === 'tri') {
      g.moveTo(-s, 0); g.lineTo(s, 0); g.lineTo(0, -s * 1.5); g.closePath();
    } else {
      g.arc(0, 0, s, Math.PI, 0, false); g.closePath();
    }
    g.fillStyle = color; g.fill();
    g.restore();
  }
  function sfcDraw(pane) {
    var st = pane.sfc;
    var cv = pane._sfcCanvas;
    var map = pane.tv && pane.tv.map;
    var doc = pane._sfcDoc || SFCData.doc;
    if (!st || !cv || !map) return;
    var box = pane.el.getBoundingClientRect();
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var ck = camKey(map, box, dpr) + ':' + (st.on ? 1 : 0);
    if (pane._sfcDrawDoc === doc && pane._sfcDrawCam === ck) return;
    pane._sfcDrawDoc = doc; pane._sfcDrawCam = ck;
    if (cv.width !== Math.round(box.width * dpr)) cv.width = Math.round(box.width * dpr);
    if (cv.height !== Math.round(box.height * dpr)) cv.height = Math.round(box.height * dpr);
    var g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, box.width, box.height);
    if (!st.on || !doc) return;
    var bounds = map.getBounds();
    var w = bounds.getWest(), e = bounds.getEast(), s = bounds.getSouth(), n = bounds.getNorth();
    var margin = 12;   // deg slack so a front reaching in from off-screen draws
    g.lineJoin = 'round'; g.lineCap = 'round';
    (doc.fronts || []).forEach(function (f) {
      var pts = [], any = false;
      (f.points || []).forEach(function (p) {
        var lat = p[0], lon = p[1];
        if (lat > s - margin && lat < n + margin &&
            lon > w - margin && lon < e + margin) any = true;
        var xy = map.project([lon, lat]);
        pts.push([xy.x, xy.y]);
      });
      if (!any || pts.length < 2) return;
      // ONE geometry for line AND pips: densely sample the midpoint-
      // quadratic curve, stroke THAT polyline, and walk THE SAME samples
      // for pip placement. Drawing the smoothed curve but walking the raw
      // polyline for pips floated symbols off the line at every bend (the
      // tester "detached triangles" bug) — position, spacing and tangent
      // all have to come from the geometry actually drawn.
      var sm = [], SEG = 8;                      // samples per curve span
      var q = function (a, c, b, t) {            // quadratic bezier point
        var u = 1 - t;
        return [u * u * a[0] + 2 * u * t * c[0] + t * t * b[0],
                u * u * a[1] + 2 * u * t * c[1] + t * t * b[1]];
      };
      if (pts.length === 2) {
        sm = [pts[0], pts[1]];
      } else {
        sm.push(pts[0]);
        var prevMid = pts[0];
        for (var ci = 1; ci < pts.length - 1; ci++) {
          var mid = [(pts[ci][0] + pts[ci + 1][0]) / 2,
                     (pts[ci][1] + pts[ci + 1][1]) / 2];
          for (var ti = 1; ti <= SEG; ti++)
            sm.push(q(prevMid, pts[ci], mid, ti / SEG));
          prevMid = mid;
        }
        sm.push(pts[pts.length - 1]);
      }
      var path = function () {
        g.beginPath();
        g.moveTo(sm[0][0], sm[0][1]);
        for (var si = 1; si < sm.length; si++) g.lineTo(sm[si][0], sm[si][1]);
      };
      var color = SFC_COLORS[f.kind] || '#dfe8f2';
      path();
      g.setLineDash(f.kind === 'trof' ? [7, 6] : []);
      g.lineWidth = 4; g.strokeStyle = 'rgba(5,10,20,0.8)'; g.stroke();
      path();
      g.lineWidth = 2;
      g.strokeStyle = (f.kind === 'stnry') ? '#4da3ff' : color;
      g.stroke();
      g.setLineDash([]);
      if (f.kind !== 'trof') {
        // pips ON the sampled curve, evenly spaced ALONG it, oriented by
        // the LOCAL tangent; stationary alternates type AND side (the
        // standard couplet), occluded alternates type on one side
        var SP = 30, acc = SP * 0.5, k = 0;
        for (var i2 = 1; i2 < sm.length; i2++) {
          var ax = sm[i2 - 1][0], ay = sm[i2 - 1][1];
          var bx = sm[i2][0], by = sm[i2][1];
          var seg = Math.hypot(bx - ax, by - ay);
          if (!seg) continue;
          var dir = Math.atan2(by - ay, bx - ax);
          while (acc < seg) {
            var px = ax + (bx - ax) * (acc / seg), py = ay + (by - ay) * (acc / seg);
            var alt = (k++ % 2) === 0;
            if (f.kind === 'cold') sfcPip(g, px, py, dir, 'tri', color);
            else if (f.kind === 'warm') sfcPip(g, px, py, dir, 'semi', color);
            else if (f.kind === 'ocfnt') sfcPip(g, px, py, dir, alt ? 'tri' : 'semi', color);
            else if (f.kind === 'stnry')
              sfcPip(g, px, py, dir + (alt ? 0 : Math.PI),
                     alt ? 'tri' : 'semi', alt ? '#4da3ff' : '#ff6d7a');
            acc += SP;
          }
          acc -= seg;
        }
      }
    });
    // H / L pressure centers
    g.font = 'bold 17px "Segoe UI", system-ui, sans-serif';
    g.textAlign = 'center';
    (doc.centers || []).forEach(function (c) {
      if (c.lat < s - 2 || c.lat > n + 2 || c.lon < w - 4 || c.lon > e + 4) return;
      var xy = map.project([c.lon, c.lat]);
      if (xy.x < -20 || xy.y < -20 || xy.x > box.width + 20 || xy.y > box.height + 20) return;
      var col = c.kind === 'high' ? '#6db4ff' : '#ff6d7a';
      g.lineWidth = 4; g.strokeStyle = 'rgba(5,10,20,0.85)';
      var letter = c.kind === 'high' ? 'H' : 'L';
      g.strokeText(letter, xy.x, xy.y + 6);
      g.fillStyle = col; g.fillText(letter, xy.x, xy.y + 6);
      g.font = '10px "Segoe UI", system-ui, sans-serif';
      g.lineWidth = 3;
      g.strokeText(String(c.mb), xy.x, xy.y + 18);
      g.fillStyle = '#dfe8f2'; g.fillText(String(c.mb), xy.x, xy.y + 18);
      g.font = 'bold 17px "Segoe UI", system-ui, sans-serif';
    });
  }
  var SFC_MAX_SKEW_MS = 4.5 * 3600e3; // analyses are 3-hourly + latency
  function sfcSyncTo(pane, paneIdx, satStamp) {
    var st = pane.sfc;
    if (!st || !st.on || !satStamp) return;
    if (!SFCData.manifest) return;
    var best = SFCData.nearest(satStamp, SFC_MAX_SKEW_MS);
    if (best === st.shown) return;
    st.shown = best;
    if (best == null) {
      pane._sfcDoc = { fronts: [], centers: [] };
      sfcDraw(pane);
      if (H.renderPaneChrome) H.renderPaneChrome(paneIdx);
      return;
    }
    SFCData.frame(best).then(function (d) {
      if (pane.sfc && pane.sfc.shown === best && d) {
        pane._sfcDoc = d;
        sfcDraw(pane);
        if (H.renderPaneChrome) H.renderPaneChrome(paneIdx);
      }
    });
  }
  var _sfcT = null;
  function sfcStartPoll() {
    if (_sfcT) return;
    _sfcT = setInterval(function () {
      if (typeof document !== 'undefined' && document.hidden) return;
      var any = false;
      (CX && CX.panes || []).forEach(function (p) { if (p && p.sfc && p.sfc.on) any = true; });
      if (!any) return;
      SFCData.reload().then(function () {
        CX.panes.forEach(function (p, i) {
          if (p && p.sfc && p.sfc.on) {
            p.sfc.shown = undefined;
            sfcSyncTo(p, i, radPaneStamp(p));
            sfcDraw(p);
          }
        });
      }).catch(function () {});
    }, 600e3);
    if (_sfcT && _sfcT.unref) _sfcT.unref();
  }

  // ========================================================================
  // NHC layer: cones/areas as in-GL vector layers; storm icons as canvas
  // glyphs with the home-map SSHWS identity (D/S/1-5 letters, invest = X)
  // ========================================================================
  // Canonical SSHWS palette (tat_palette.js), so an explorer storm glyph is
  // the same color as the same storm on the home map.
  function TATP() {
    var p = window.TATPalette;
    if (!p) throw new Error('cockpit_fields.js: load /tat_palette.js first');
    return p;
  }
  function nhcState(pane) {
    if (!pane.nhc) pane.nhc = { on: false };
    return pane.nhc;
  }
  function nhcLayerIds(i) {
    var b = 'ofnhc-' + i;
    return [b + '-area-fill', b + '-area-line', b + '-cone-fill',
            b + '-cone-line', b + '-track-case', b + '-track'];
  }
  function nhcClearLayers(pane, paneIdx) {
    var map = pane.tv && pane.tv.map;
    if (!map) return;
    nhcLayerIds(paneIdx).forEach(function (id) {
      if (map.getLayer(id)) map.removeLayer(id);
    });
    if (map.getSource('ofnhc-' + paneIdx)) map.removeSource('ofnhc-' + paneIdx);
    if (pane._nhcCanvas) {
      var g = pane._nhcCanvas.getContext('2d');
      g.clearRect(0, 0, pane._nhcCanvas.width, pane._nhcCanvas.height);
    }
    nhcDialogHide(pane);
    if (pane._nhcCursor) {
      pane._nhcCursor = false;
      if (map.getCanvas()) map.getCanvas().style.cursor = '';
    }
  }
  function nhcRender(pane, paneIdx) {
    var map = pane.tv && pane.tv.map;
    var st = nhcState(pane);
    if (!map || !st.on) return;
    Promise.all([NHCData.load(), GSData.load().catch(function () { return null; })])
      .then(function (rs) {
        var doc = rs[0];
        // the fetch outlives a quick toggle-off (or a pane teardown):
        // re-check before mounting or ghost layers render with the button
        // off (review-caught)
        if (!doc || !st.on || !pane.tv || !pane.tv.map) return;
        var sid = 'ofnhc-' + paneIdx;
        var before = map.getLayer('grat') ? 'grat' : undefined;
        if (!map.getSource(sid)) {
          map.addSource(sid, { type: 'geojson', data: doc });
          // formation areas: outlook coloring, TAT-muted (low yellow /
          // medium orange / high red by the 7-day chance)
          var areaColor = ['step', ['get', 'prob7'],
                           '#ffcf5c', 40, '#ff9a2f', 60, '#f5333c'];
          map.addLayer({ id: sid + '-area-fill', type: 'fill', source: sid,
            filter: ['==', ['get', 'kind'], 'area'],
            // the strong dashed outline carries the contrast; the fill is a
            // wash — imagery (and a just-designated storm still inside a
            // lingering outlook area) must read through it
            paint: { 'fill-color': areaColor, 'fill-opacity': 0.16 } }, before);
          map.addLayer({ id: sid + '-area-line', type: 'line', source: sid,
            filter: ['==', ['get', 'kind'], 'area'],
            paint: { 'line-color': areaColor, 'line-opacity': 0.95,
                     'line-width': 2.2, 'line-dasharray': [5, 3] } }, before);
          // outline-forward cone: the fill frames the storm, never buries
          // it — imagery and station plots must read clearly THROUGH it
          map.addLayer({ id: sid + '-cone-fill', type: 'fill', source: sid,
            filter: ['==', ['get', 'kind'], 'cone'],
            paint: { 'fill-color': '#dfe8f2', 'fill-opacity': 0.045 } }, before);
          map.addLayer({ id: sid + '-cone-line', type: 'line', source: sid,
            filter: ['==', ['get', 'kind'], 'cone'],
            paint: { 'line-color': '#eef4fb', 'line-opacity': 0.95,
                     'line-width': 1.7, 'line-dasharray': [4, 3] } }, before);
          // forecast track: solid, cased for contrast over any imagery —
          // the cone alone hides the forecast; positions ride the glyph
          // canvas (timed, intensity-lettered) in nhcDraw
          map.addLayer({ id: sid + '-track-case', type: 'line', source: sid,
            filter: ['==', ['get', 'kind'], 'track'],
            paint: { 'line-color': 'rgba(5,10,20,0.8)', 'line-opacity': 0.8,
                     'line-width': 3.4 } }, before);
          map.addLayer({ id: sid + '-track', type: 'line', source: sid,
            filter: ['==', ['get', 'kind'], 'track'],
            paint: { 'line-color': '#dfe8f2', 'line-opacity': 0.9,
                     'line-width': 1.6 } }, before);
        } else {
          map.getSource(sid).setData(doc);
        }
        if (!pane._nhcCanvas) {
          var cv = document.createElement('canvas');
          cv.className = 'cx-nhc-overlay';
          cv.style.cssText = 'position:absolute;inset:0;z-index:3;pointer-events:none;width:100%;height:100%';
          pane.el.appendChild(cv);
          pane._nhcCanvas = cv;
        }
        if (!pane._nhcWired) {
          pane._nhcWired = true;
          map.on('render', function () { nhcDraw(pane); });
          // AOI click -> genesis-chance dialog. The glyph canvas is
          // pointer-events:none, so the map still owns the pointer; hit-test
          // the real GL area layer (same pattern as select-on-map)
          map.on('click', function (ev) { nhcAreaClick(pane, paneIdx, ev); });
          map.on('mousemove', function (ev) {
            var s2 = pane.nhc, fill = 'ofnhc-' + paneIdx + '-area-fill';
            if (!s2 || !s2.on || !map.getLayer(fill) || (pane.tv && pane.tv._armed)) return;
            var hit = map.queryRenderedFeatures(ev.point, { layers: [fill] }).length > 0;
            // only touch the cursor we set — never clobber another tool's
            if (hit && !pane._nhcCursor) { pane._nhcCursor = true; map.getCanvas().style.cursor = 'pointer'; }
            else if (!hit && pane._nhcCursor) { pane._nhcCursor = false; map.getCanvas().style.cursor = ''; }
          });
        }
        nhcDraw(pane);
        if (H.renderPaneChrome) H.renderPaneChrome(paneIdx);
      }).catch(function () { H.flash('NHC products unavailable'); });
  }
  function nhcTierColor(p) {
    return p >= 60 ? '#f5333c' : p >= 40 ? '#ff9a2f' : '#ffcf5c';
  }
  function nhcDialogHide(pane) {
    if (pane._nhcDialog) pane._nhcDialog.style.display = 'none';
  }
  function nhcAreaClick(pane, paneIdx, ev) {
    var st = pane.nhc;
    var map = pane.tv && pane.tv.map;
    var fill = 'ofnhc-' + paneIdx + '-area-fill';
    if (!st || !st.on || !map || !map.getLayer(fill)) return;
    if (pane.tv && pane.tv._armed) return;   // draw-a-box owns this click
    var fs = map.queryRenderedFeatures(ev.point, { layers: [fill] });
    if (!fs.length) { nhcDialogHide(pane); return; }
    var esc = function (s) {
      return String(s == null ? '' : s).replace(/[&<>"]/g, function (ch) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch];
      });
    };
    var p = fs[0].properties || {};
    var el = pane._nhcDialog;
    if (!el) {
      el = document.createElement('div');
      el.className = 'cx-nhc-dialog';
      el.style.cssText = 'position:absolute;z-index:6;min-width:200px;max-width:250px;' +
        'background:rgba(10,13,18,.94);border:1px solid rgba(90,110,140,.45);' +
        'border-radius:8px;padding:8px 10px;color:#dfe8f2;' +
        'font:12px "Segoe UI",system-ui,sans-serif;display:none';
      pane.el.appendChild(el);
      pane._nhcDialog = el;
    }
    var row = function (label, prob, risk) {
      var v = typeof prob === 'number' ? prob : parseInt(prob, 10) || 0;
      return '<div style="display:flex;justify-content:space-between;gap:12px;padding:1px 0">' +
        '<span style="color:#8ea2bd">' + label + '</span>' +
        '<b style="color:' + nhcTierColor(v) + '">' + v + '%' +
        (risk ? ' · ' + esc(risk) : '') + '</b></div>';
    };
    var as = (NHCData.doc && NHCData.doc.as_of) ? fmtZ(NHCData.doc.as_of) : '';
    el.innerHTML =
      '<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">' +
        '<b style="flex:1">Formation area' + (p.basin ? ' · ' + esc(p.basin) : '') + '</b>' +
        '<span class="cx-nhc-dlg-x" style="cursor:pointer;color:#8ea2bd;padding:0 3px">✕</span></div>' +
      row('2-day chance', p.prob2, p.risk2) +
      row('7-day chance', p.prob7, p.risk7) +
      '<div style="margin-top:6px;color:#8ea2bd;font-size:10.5px">Tropical cyclone formation · NHC outlook' +
        (as ? ' · ' + as : '') + '</div>';
    el.querySelector('.cx-nhc-dlg-x').onclick = function () { nhcDialogHide(pane); };
    var box = pane.el.getBoundingClientRect();
    el.style.left = Math.max(4, Math.min(ev.point.x + 12, box.width - 260)) + 'px';
    el.style.top = Math.max(4, Math.min(ev.point.y + 12, box.height - 120)) + 'px';
    el.style.display = 'block';
  }
  function nhcRaise(pane, paneIdx) {
    var map = pane.tv && pane.tv.map;
    if (!map || !map.getLayer('grat')) return;
    nhcLayerIds(paneIdx).forEach(function (id) {
      try { if (map.getLayer(id)) map.moveLayer(id, 'grat'); } catch (e) {}
    });
  }
  function nhcDraw(pane) {
    var st = pane.nhc;
    var cv = pane._nhcCanvas;
    var map = pane.tv && pane.tv.map;
    if (!st || !cv || !map) return;
    var box = pane.el.getBoundingClientRect();
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var ck = camKey(map, box, dpr) + ':' + (st.on ? 1 : 0);
    if (pane._nhcDrawN === NHCData.doc && pane._nhcDrawG === GSData.doc &&
        pane._nhcDrawCam === ck) return;
    pane._nhcDrawN = NHCData.doc; pane._nhcDrawG = GSData.doc;
    pane._nhcDrawCam = ck;
    if (cv.width !== Math.round(box.width * dpr)) cv.width = Math.round(box.width * dpr);
    if (cv.height !== Math.round(box.height * dpr)) cv.height = Math.round(box.height * dpr);
    var g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, box.width, box.height);
    if (!st.on) return;
    var bounds = map.getBounds();
    var w = bounds.getWest(), e = bounds.getEast(), s = bounds.getSouth(), n = bounds.getNorth();
    var z = map.getZoom();
    var proj = function (c) {
      var lon = c[0], lat = c[1];
      if (lat < s - 2 || lat > n + 2) return null;
      if (lon < w && lon + 360 <= e) lon += 360;
      if (lon < w - 2 || lon > e + 2) return null;
      var xy = map.project([lon, lat]);
      if (xy.x < -20 || xy.y < -20 || xy.x > box.width + 20 || xy.y > box.height + 20) return null;
      return xy;
    };
    // forecast positions (from the NHC feed): timed, intensity-lettered
    // points along the track inside the cone. Drawn BEFORE the live markers
    // so the current-position glyph always wins the overlap.
    var nd = NHCData.doc;
    if (nd && nd.features) {
      g.textAlign = 'center'; g.lineJoin = 'round';
      nd.features.forEach(function (f) {
        var p = f.properties || {};
        if (p.kind !== 'point') return;
        if (p.tau === 0) return;   // current position: the live marker owns it
        var c = f.geometry && f.geometry.coordinates;
        var xy = c && proj(c);
        if (!xy) return;
        var kt = typeof p.maxwind === 'number' ? p.maxwind : null;
        var cat = kt == null || kt < 34 ? 'TD' : kt < 64 ? 'TS' : kt < 83 ? 'C1' :
                  kt < 96 ? 'C2' : kt < 113 ? 'C3' : kt < 137 ? 'C4' : 'C5';
        var letter = cat === 'TD' ? 'D' : cat === 'TS' ? 'S' : cat.slice(1);
        g.beginPath(); g.arc(xy.x, xy.y, 7, 0, Math.PI * 2);
        g.fillStyle = 'rgba(5,10,20,0.85)'; g.fill();
        g.beginPath(); g.arc(xy.x, xy.y, 5.6, 0, Math.PI * 2);
        g.fillStyle = TATP().cats[cat]; g.fill();
        g.font = 'bold 9px "Segoe UI", system-ui, sans-serif';
        g.fillStyle = '#0a0d12';
        g.fillText(letter, xy.x, xy.y + 3);
        if (z >= 4 && p.datelbl) {
          g.font = '9px "Segoe UI", system-ui, sans-serif';
          g.lineWidth = 3; g.strokeStyle = 'rgba(5,10,20,0.85)';
          g.strokeText(p.datelbl, xy.x, xy.y + 17);
          g.fillStyle = '#c6d2e2';
          g.fillText(p.datelbl, xy.x, xy.y + 17);
        }
      });
    }
    var gs = GSData.doc;
    if (!gs || !gs.features) return;
    g.font = 'bold 11px "Segoe UI", system-ui, sans-serif';
    g.textAlign = 'center'; g.lineJoin = 'round';
    gs.features.forEach(function (f) {
      var p = f.properties || {};
      if (p.kind !== 'active_marker') return;
      var c = f.geometry && f.geometry.coordinates;
      if (!c) return;
      var lon = c[0], lat = c[1];
      if (lat < s - 2 || lat > n + 2) return;
      if (lon < w && lon + 360 <= e) lon += 360;
      if (lon < w - 2 || lon > e + 2) return;
      var xy = map.project([lon, lat]);
      if (xy.x < -20 || xy.y < -20 || xy.x > box.width + 20 || xy.y > box.height + 20) return;
      if (p.marker_type === 'invest_x') {
        // invest area: the home map's red X
        g.lineWidth = 4; g.strokeStyle = 'rgba(5,10,20,0.85)';
        g.beginPath();
        g.moveTo(xy.x - 6, xy.y - 6); g.lineTo(xy.x + 6, xy.y + 6);
        g.moveTo(xy.x + 6, xy.y - 6); g.lineTo(xy.x - 6, xy.y + 6);
        g.stroke();
        g.lineWidth = 2.4; g.strokeStyle = '#f5333c'; g.stroke();
      } else {
        var cat = p.current_category || 'TD';
        var col = TATP().cats[cat] || TATP().cats[TATP().unknown];
        var letter = cat === 'TD' ? 'D' : cat === 'TS' ? 'S' : cat.slice(1);
        g.beginPath(); g.arc(xy.x, xy.y, 9, 0, Math.PI * 2);
        g.fillStyle = 'rgba(5,10,20,0.85)'; g.fill();
        g.beginPath(); g.arc(xy.x, xy.y, 7.5, 0, Math.PI * 2);
        g.fillStyle = col; g.fill();
        g.fillStyle = '#0a0d12';
        g.fillText(letter, xy.x, xy.y + 4);
      }
      if (z >= 3.2 && p.name) {
        g.lineWidth = 3; g.strokeStyle = 'rgba(5,10,20,0.85)';
        g.strokeText(p.name, xy.x, xy.y + 20);
        g.fillStyle = '#dfe8f2';
        g.fillText(p.name, xy.x, xy.y + 20);
      }
    });
  }
  var _nhcT = null;
  function nhcStartPoll() {
    if (_nhcT) return;
    _nhcT = setInterval(function () {
      if (typeof document !== 'undefined' && document.hidden) return;
      var any = false;
      (CX && CX.panes || []).forEach(function (p) { if (p && p.nhc && p.nhc.on) any = true; });
      if (!any) return;
      Promise.all([NHCData.reload(), GSData.reload().catch(function () { return null; })])
        .then(function () {
          CX.panes.forEach(function (p, i) {
            if (p && p.nhc && p.nhc.on) nhcRender(p, i);
          });
        }).catch(function () {});
    }, 300e3);
    if (_nhcT && _nhcT.unref) _nhcT.unref();
  }

  // ========================================================================
  // Model track guidance layer: deterministic + consensus aids and ensemble
  // members/mean per ACTIVE storm, from the shared per-storm guidance
  // document (cyclolab/{sid}/guidance.json — the same one the per-storm hub
  // hydrates; ONE data product, every consumer). Latest init cycle only.
  // ========================================================================
  var GD_BASE = CDN + '/cyclolab';
  // per-model hues (TAT palette, muted; consensus family warm+bold). Any
  // tech not listed falls to the neutral slate.
  var GD_COLORS = {
    AVNI: '#5aa9ff', AVNO: '#5aa9ff', AEMN: '#5aa9ff',
    HFAI: '#46c56a', HFBI: '#2bd4c0', HWFI: '#ffe14d', HMNI: '#ff9a2f',
    CMCI: '#c08bff', CEMN: '#c08bff', NVGI: '#8ea2bd', NEMN: '#8ea2bd',
    EGRI: '#ff6b9d', EGRR: '#ff6b9d', EMXI: '#46c56a', EEMN: '#46c56a',
    CTCI: '#7aa0ff', OFCL: '#ffffff', TVCN: '#f5333c', HCCA: '#ff7a59'
  };
  var GD_ENS = { ecens: '#49b6c8', gefs: '#ff9a5c' };
  var GDData = {
    docs: null, _p: null,
    load: function () {
      var self = this;
      if (this._p) return this._p;
      this._p = GSData.load().then(function (gs) {
        var sids = [];
        ((gs && gs.features) || []).forEach(function (f) {
          var p = f.properties || {};
          var sid = p.storm_id || p.sid;
          if (p.kind === 'active_marker' && sid && sids.indexOf(sid) < 0) sids.push(sid);
        });
        return Promise.all(sids.slice(0, 8).map(function (sid) {
          return fetch(GD_BASE + '/' + encodeURIComponent(sid) + '/guidance.json?t=' + Date.now(),
                       { cache: 'no-store' })
            .then(function (r) { if (!r.ok) throw 0; return r.json(); })
            .catch(function () { return null; });
        }));
      }).then(function (docs) {
        docs = (docs || []).filter(function (d) {
          return d && d.init_cycle &&
            ((d.track_aids && d.track_aids.length) || (d.ens && d.ens.models));
        });
        if (!docs.length) throw 0;   // honest gate: nothing to draw anywhere
        self.docs = docs;
        return docs;
      });
      return this._p;
    },
    reload: function () { this._p = null; return this.load(); }
  };
  function gdState(pane) {
    if (!pane.guid) pane.guid = { on: false };
    return pane.guid;
  }
  function gdLayerIds(i) {
    var b = 'ofgd-' + i;
    return [b + '-em', b + '-emean-case', b + '-emean',
            b + '-aid-case', b + '-aid'];
  }
  function gdFeatures(docs) {
    var fs = [];
    (docs || []).forEach(function (d) {
      var aids = d.aids || {};
      var cons = {};
      (d.consensus || []).forEach(function (c) { cons[c] = 1; });
      var techs = (d.track_aids || []).slice();
      if (aids.OFCL && techs.indexOf('OFCL') < 0) techs.push('OFCL');
      techs.forEach(function (t) {
        var pts = (aids[t] || []).filter(function (p) { return p.lat != null; });
        if (pts.length < 2) return;
        fs.push({ type: 'Feature',
          geometry: { type: 'LineString',
            coordinates: pts.map(function (p) { return [p.lon, p.lat]; }) },
          properties: { k: 'aid', tech: t, sid: d.sid,
                        cons: cons[t] || t === 'OFCL' ? 1 : 0,
                        color: GD_COLORS[t] || '#8ea2bd' } });
      });
      (((d.ens || {}).models) || []).forEach(function (m) {
        var col = GD_ENS[m.model] || '#8ea2bd';
        (m.members || []).forEach(function (mm) {
          if ((mm.points || []).length < 2) return;
          fs.push({ type: 'Feature',
            geometry: { type: 'LineString',
              coordinates: mm.points.map(function (p) { return [p[2], p[1]]; }) },
            properties: { k: 'em', model: m.model, color: col } });
        });
        if ((m.mean || []).length >= 2) {
          fs.push({ type: 'Feature',
            geometry: { type: 'LineString',
              coordinates: m.mean.map(function (p) { return [p[2], p[1]]; }) },
            properties: { k: 'emean', model: m.model, sid: d.sid, color: col } });
        }
      });
    });
    return { type: 'FeatureCollection', features: fs };
  }
  function gdClearLayers(pane, paneIdx) {
    var map = pane.tv && pane.tv.map;
    if (!map) return;
    gdLayerIds(paneIdx).forEach(function (id) {
      if (map.getLayer(id)) map.removeLayer(id);
    });
    if (map.getSource('ofgd-' + paneIdx)) map.removeSource('ofgd-' + paneIdx);
    if (pane._gdCanvas) {
      var g = pane._gdCanvas.getContext('2d');
      g.clearRect(0, 0, pane._gdCanvas.width, pane._gdCanvas.height);
    }
  }
  function gdRender(pane, paneIdx) {
    var map = pane.tv && pane.tv.map;
    var st = gdState(pane);
    if (!map || !st.on) return;
    GDData.load().then(function (docs) {
      if (!docs || !st.on || !pane.tv || !pane.tv.map) return;
      var sid = 'ofgd-' + paneIdx;
      var data = gdFeatures(docs);
      var before = map.getLayer('grat') ? 'grat' : undefined;
      if (!map.getSource(sid)) {
        map.addSource(sid, { type: 'geojson', data: data });
        // ensemble members: thin, translucent — texture, not line-reading
        map.addLayer({ id: sid + '-em', type: 'line', source: sid,
          filter: ['==', ['get', 'k'], 'em'],
          paint: { 'line-color': ['get', 'color'], 'line-opacity': 0.28,
                   'line-width': 0.9 } }, before);
        map.addLayer({ id: sid + '-emean-case', type: 'line', source: sid,
          filter: ['==', ['get', 'k'], 'emean'],
          paint: { 'line-color': 'rgba(5,10,20,0.8)', 'line-opacity': 0.85,
                   'line-width': 4.0 } }, before);
        map.addLayer({ id: sid + '-emean', type: 'line', source: sid,
          filter: ['==', ['get', 'k'], 'emean'],
          paint: { 'line-color': ['get', 'color'], 'line-opacity': 0.95,
                   'line-width': 2.2 } }, before);
        map.addLayer({ id: sid + '-aid-case', type: 'line', source: sid,
          filter: ['all', ['==', ['get', 'k'], 'aid'], ['==', ['get', 'cons'], 1]],
          paint: { 'line-color': 'rgba(5,10,20,0.8)', 'line-opacity': 0.85,
                   'line-width': 4.2 } }, before);
        map.addLayer({ id: sid + '-aid', type: 'line', source: sid,
          filter: ['==', ['get', 'k'], 'aid'],
          paint: { 'line-color': ['get', 'color'],
                   'line-opacity': ['case', ['==', ['get', 'cons'], 1], 1, 0.8],
                   'line-width': ['case', ['==', ['get', 'cons'], 1], 2.6, 1.4] } }, before);
      } else {
        map.getSource(sid).setData(data);
      }
      if (!pane._gdCanvas) {
        var cv = document.createElement('canvas');
        cv.className = 'cx-gd-overlay';
        cv.style.cssText = 'position:absolute;inset:0;z-index:3;pointer-events:none;width:100%;height:100%';
        pane.el.appendChild(cv);
        pane._gdCanvas = cv;
      }
      if (!pane._gdWired) {
        pane._gdWired = true;
        map.on('render', function () { gdDraw(pane); });
      }
      gdDraw(pane);
      if (H.renderPaneChrome) H.renderPaneChrome(paneIdx);
    }).catch(function () { H.flash('model guidance unavailable'); });
  }
  function gdRaise(pane, paneIdx) {
    var map = pane.tv && pane.tv.map;
    if (!map || !map.getLayer('grat')) return;
    gdLayerIds(paneIdx).forEach(function (id) {
      try { if (map.getLayer(id)) map.moveLayer(id, 'grat'); } catch (e) {}
    });
  }
  var GD_TAUS = [24, 48, 72, 96, 120];
  function gdDraw(pane) {
    var st = pane.guid;
    var cv = pane._gdCanvas;
    var map = pane.tv && pane.tv.map;
    if (!st || !cv || !map) return;
    var box = pane.el.getBoundingClientRect();
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var ck = camKey(map, box, dpr) + ':' + (st.on ? 1 : 0);
    if (pane._gdDrawDocs === GDData.docs && pane._gdDrawCam === ck) return;
    pane._gdDrawDocs = GDData.docs; pane._gdDrawCam = ck;
    if (cv.width !== Math.round(box.width * dpr)) cv.width = Math.round(box.width * dpr);
    if (cv.height !== Math.round(box.height * dpr)) cv.height = Math.round(box.height * dpr);
    var g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, box.width, box.height);
    if (!st.on || !GDData.docs) return;
    var bounds = map.getBounds();
    var w = bounds.getWest(), e = bounds.getEast(), s = bounds.getSouth(), n = bounds.getNorth();
    var z = map.getZoom();
    var proj = function (lon, lat) {
      if (lat < s - 2 || lat > n + 2) return null;
      if (lon < w && lon + 360 <= e) lon += 360;
      if (lon < w - 2 || lon > e + 2) return null;
      var xy = map.project([lon, lat]);
      if (xy.x < -24 || xy.y < -24 || xy.x > box.width + 24 || xy.y > box.height + 24) return null;
      return xy;
    };
    g.textAlign = 'left'; g.lineJoin = 'round';
    GDData.docs.forEach(function (d) {
      var aids = d.aids || {};
      // forecast-hour chips ride the consensus spine (or the first aid)
      var spine = aids.TVCN || aids.HCCA || aids[(d.track_aids || [])[0]] || [];
      spine.forEach(function (p) {
        if (p.lat == null || GD_TAUS.indexOf(p.tau) < 0) return;
        var xy = proj(p.lon, p.lat);
        if (!xy) return;
        g.font = 'bold 9px "Segoe UI", system-ui, sans-serif';
        g.lineWidth = 3; g.strokeStyle = 'rgba(5,10,20,0.85)';
        g.strokeText(p.tau + 'h', xy.x + 5, xy.y + 3);
        g.fillStyle = '#dfe8f2';
        g.fillText(p.tau + 'h', xy.x + 5, xy.y + 3);
      });
      if (z >= 3.6) {
        // model label at each aid's endpoint (its own hue, dark halo)
        var techs = (d.track_aids || []).slice();
        if (aids.OFCL && techs.indexOf('OFCL') < 0) techs.push('OFCL');
        techs.forEach(function (t) {
          var pts = (aids[t] || []).filter(function (p) { return p.lat != null; });
          if (pts.length < 2) return;
          var pe = pts[pts.length - 1];
          var xy = proj(pe.lon, pe.lat);
          if (!xy) return;
          g.font = 'bold 9.5px "Segoe UI", system-ui, sans-serif';
          g.lineWidth = 3; g.strokeStyle = 'rgba(5,10,20,0.85)';
          g.strokeText(t, xy.x + 4, xy.y - 4);
          g.fillStyle = GD_COLORS[t] || '#8ea2bd';
          g.fillText(t, xy.x + 4, xy.y - 4);
        });
        (((d.ens || {}).models) || []).forEach(function (m) {
          if ((m.mean || []).length < 2) return;
          var pe = m.mean[m.mean.length - 1];
          var xy = proj(pe[2], pe[1]);
          if (!xy) return;
          var lbl = (m.label || m.model) + ' mean (' + m.n_matched + ')';
          g.font = 'bold 9.5px "Segoe UI", system-ui, sans-serif';
          g.lineWidth = 3; g.strokeStyle = 'rgba(5,10,20,0.85)';
          g.strokeText(lbl, xy.x + 4, xy.y - 4);
          g.fillStyle = GD_ENS[m.model] || '#8ea2bd';
          g.fillText(lbl, xy.x + 4, xy.y - 4);
        });
      }
    });
  }
  var _gdT = null;
  function gdStartPoll() {
    if (_gdT) return;
    _gdT = setInterval(function () {
      if (typeof document !== 'undefined' && document.hidden) return;
      var any = false;
      (CX && CX.panes || []).forEach(function (p) { if (p && p.guid && p.guid.on) any = true; });
      if (!any) return;
      GDData.reload().then(function () {
        CX.panes.forEach(function (p, i) {
          if (p && p.guid && p.guid.on) gdRender(p, i);
        });
      }).catch(function () {});
    }, 600e3);
    if (_gdT && _gdT.unref) _gdT.unref();
  }

  function setLayer(i, kind, on) {
    var pane = CX.panes[i];
    if (!pane || !pane.tv || !pane.tv.map) return;
    if (kind === 'guid') {
      var gst = gdState(pane);
      gst.on = on;
      if (on) { gdRender(pane, i); gdStartPoll(); }
      else { gdClearLayers(pane, i); }
      if (H.renderPaneChrome) H.renderPaneChrome(i);
      return;
    }
    if (kind === 'nhc') {
      var nst = nhcState(pane);
      nst.on = on;
      if (on) { nhcRender(pane, i); nhcStartPoll(); }
      else { nhcClearLayers(pane, i); }
      if (H.renderPaneChrome) H.renderPaneChrome(i);
      return;
    }
    if (kind === 'sfc') {
      var fst = sfcState(pane);
      fst.on = on;
      if (on) {
        sfcRender(pane, i);
        sfcStartPoll();
        SFCData.load().then(function () { sfcSyncTo(pane, i, radPaneStamp(pane)); });
      }
      else if (pane._sfcCanvas) { sfcDraw(pane); }
      if (H.renderPaneChrome) H.renderPaneChrome(i);
      return;
    }
    if (kind === 'obs') {
      var ost = obsState(pane);
      ost.on = on;
      if (on) {
        obsRender(pane, i);
        obsStartPoll();
        OBSData.load().then(function () { obsSyncTo(pane, i, radPaneStamp(pane)); });
      }
      else if (pane._obsCanvas) { obsDraw(pane); }
      if (H.renderPaneChrome) H.renderPaneChrome(i);
      return;
    }
    if (kind === 'rad') {
      var rst = radState(pane);
      rst.on = on;
      if (on) { radRender(pane, i); radStartPoll(); }
      else { radClearLayers(pane); }
      if (H.renderPaneChrome) H.renderPaneChrome(i);
      return;
    }
    if (kind === 'tpw') {
      var tst = tpwState(pane);
      tst.on = on;
      if (on) { tpwRender(pane, i); tpwStartPoll(); }
      else { tpwClearLayers(pane); }
      if (H.renderPaneChrome) H.renderPaneChrome(i);
      return;
    }
    if (kind === 'mw') {
      var st = mwState(pane);
      st.on = on;
      if (on) {
        MWData.load().then(function (m) {
          if (!st.slug && m.storms && m.storms.length) st.slug = m.default_slug || m.storms[0].slug;
          mwRender(pane, i);
          syncControls();
        });
      } else if (pane.kind !== 'mw') {
        mwClearLayers(pane);
        if (H.renderPaneChrome) H.renderPaneChrome(i);
      }
    } else {
      var sst = scState(pane);
      sst.on = on;
      if (on) scRender(pane, i);
      else { scDraw(pane); scFieldSync(pane); }
      if (H.renderPaneChrome) H.renderPaneChrome(i);
    }
  }

  // time-lock: the shared clock's stamp pulls each MW pane to its nearest
  // overpass (unless the user pinned one) — nearest-in-time, the cockpit's
  // follower semantic. SC passes are daily-cadence; nearest pass applies only
  // when a specific pass is pinned ('all' composite stays composite).
  function timeSync(stamp) {
    if (!CX || !stamp) return;
    var t = Date.UTC(+stamp.slice(0, 4), +stamp.slice(4, 6) - 1, +stamp.slice(6, 8),
                     +stamp.slice(9, 11), +stamp.slice(11, 13), +stamp.slice(13, 15) || 0);
    CX.panes.forEach(function (pane, i) {
      if (!pane) return;
      if ((pane.kind === 'mw' || (pane.mw && pane.mw.on)) && pane.mw && pane.mw.slug && !pane.mw.pinned) {
        var ops = MWData.ops[pane.mw.slug];
        if (!ops || !ops.length) return;
        var best = 0, bd = Infinity;
        for (var k = 0; k < ops.length; k++) {
          var d = Math.abs((Date.parse(ops[k].valid_utc) || 0) - t);
          if (d < bd) { bd = d; best = k; }
        }
        if (best !== pane.mw.opIdx) { pane.mw.opIdx = best; mwRender(pane, i); }
      }
      // the animated overlays ride the same clock: nearest frame each
      if (pane.rad && pane.rad.on) radSyncTo(pane, i, stamp);
      if (pane.tpw && pane.tpw.on) tpwSyncTo(pane, i, stamp);
      if (pane.obs && pane.obs.on) obsSyncTo(pane, i, stamp);
      if (pane.sfc && pane.sfc.on) sfcSyncTo(pane, i, stamp);
      // NHC vector layers share the frame-layer burial problem: re-raise
      if (pane.nhc && pane.nhc.on) nhcRaise(pane, i);
      if (pane.guid && pane.guid.on) gdRaise(pane, i);
      if (pane._scFieldLayers && pane._scFieldLayers.length) scFieldRaise(pane);
    });
  }

  // ========================================================================
  // chrome (pane header + key) + export compositing
  // ========================================================================
  function fmtZ(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso || '';
    return d.toISOString().slice(0, 16).replace('T', ' ') + 'Z';
  }
  function chromeFor(pane) {
    if (pane.kind === 'mw' && pane.mw) {
      var o = mwCurrentOp(pane);
      var stormName = mwStormName(pane.mw.slug);
      return {
        title: 'Passive MW · ' + (MW_LABELS[pane.mw.product] || pane.mw.product) +
               (stormName ? ' · ' + stormName : ''),
        sub: o ? ('Valid ' + fmtZ(o.valid_utc) + ' · ' + (o.sensor || '') +
                  (o.platform ? ' ' + o.platform : '') + (pane.mw.raw ? ' · raw' : '')) : 'no overpass',
        legend: mwLegend(pane.mw.product),
        credit: 'NASA GPM/PPS + NOAA/CIRA TC-PRIMED'
      };
    }
    if (pane.kind === 'sc' && pane.sc) {
      var newest = scNewest(pane);
      var n = (pane._scPasses || []).length;
      var bg = pane.sc.backdrop !== 'none' && pane._scBackdrop
        ? ' · over ' + pane._scBackdrop.title : '';
      return {
        title: 'ASCAT Ocean Winds' + (pane.sc.view !== 'recent' ? ' · ' + pane.sc.view.toUpperCase() : ''),
        sub: newest ? ('Latest pass ' + fmtZ(newest.start_utc) + ' · ' + n + ' pass' + (n === 1 ? '' : 'es') +
                       ' · barbs FROM · C-band underestimates extreme cores' + bg) : 'no passes',
        legend: scLegend(pane.sc.style),
        credit: '© EUMETSAT / OSI SAF / KNMI'
      };
    }
    // layer badges over a tile field
    var extra = [];
    if (pane.mw && pane.mw.on) {
      var op = mwCurrentOp(pane);
      extra.push('MW ' + (MW_LABELS[pane.mw.product] || '') + (op ? ' · ' + fmtZ(op.valid_utc) : ''));
    }
    if (pane.sc && pane.sc.on) {
      var nw = scNewest(pane);
      extra.push('ASCAT winds' + (nw ? ' · ' + fmtZ(nw.start_utc) : ''));
    }
    if (pane.sfc && pane.sfc.on) {
      var sd = pane._sfcDoc || SFCData.doc;
      extra.push('Sfc analysis' + (sd && sd.valid ? ' \u00b7 valid ' + fmtZ(sd.valid) : ''));
    }
    if (pane.obs && pane.obs.on) {
      var od = pane._obsDoc || OBSData.doc;
      extra.push('Surface obs' + (od && od.as_of ? ' \u00b7 ' + fmtZ(od.as_of) : ''));
    }
    if (pane.guid && pane.guid.on) {
      var gdocs = GDData.docs;
      var gi = gdocs && gdocs.length && gdocs[0].init_time;
      extra.push('Model guidance' + (gi ? ' \u00b7 init ' + fmtZ(gi) : ''));
    }
    if (pane.nhc && pane.nhc.on) {
      var nd = NHCData.doc;
      extra.push('NHC' + (nd && nd.as_of ? ' \u00b7 ' + fmtZ(nd.as_of) : ''));
    }
    if (pane.rad && pane.rad.on) {
      var rs = pane.rad.stamp;
      extra.push('MRMS radar' + (rs
        ? ' · ' + rs.slice(0, 8).replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3') +
          ' ' + rs.slice(9, 11) + ':' + rs.slice(11, 13) + 'Z'
        : ''));
    }
    if (pane.tpw && pane.tpw.on) {
      var ts = pane.tpw.stamp;
      extra.push('TPW (CIMSS/SSEC)' + (ts
        ? ' · ' + ts.slice(0, 8).replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3') +
          ' ' + ts.slice(9, 11) + ':' + ts.slice(11, 13) + 'Z'
        : ''));
    }
    return extra.length ? { layerBadge: extra.join('  +  ') } : null;
  }
  function mwStormName(slug) {
    var m = MWData.manifest;
    if (!m || !m.storms) return null;
    for (var i = 0; i < m.storms.length; i++) if (m.storms[i].slug === slug) return m.storms[i].name;
    return null;
  }
  function mwLegend(product) {
    var legs = (MWData.manifest && MWData.manifest.legends) || {};
    var leg = legs[product];
    if (leg && leg.discrete && leg.stops) {
      // compact: every other stop keeps the key readable in a pane corner
      var rows = [];
      for (var i = 0; i < leg.stops.length; i += 2) {
        rows.push([leg.stops[i].color, leg.stops[i].label + ' K']);
      }
      return { rows: rows, cap: leg.label || product };
    }
    return { rows: [], cap: (leg && leg.label) || (MW_LABELS[product] + ' — qualitative composite') };
  }
  function scLegend(styleKey) {
    var AV = window.AscatViewer;
    var scale = (AV.STYLES[styleKey] || AV.STYLES.highcontrast).scale;
    var picks = [0, 34, 50, 64, 96, 113, 137];
    var rows = picks.map(function (kt) {
      return [windColor(scale, kt), kt + ' kt' + (kt === 34 ? ' TS' : kt === 64 ? ' H' : kt === 96 ? ' MH' : '')];
    });
    return { rows: rows, cap: '10 m wind (kt)' };
  }
  // composited into PNG/WebM exports (the SC canvas isn't part of the map GL canvas)
  function compositeOverlays(ctx, pane, w, h) {
    if (pane._scCanvas && (pane.kind === 'sc' || (pane.sc && pane.sc.on))) {
      try { ctx.drawImage(pane._scCanvas, 0, 0, w, h); } catch (e) {}
    }
    if (pane._obsCanvas && pane.obs && pane.obs.on) {
      try { ctx.drawImage(pane._obsCanvas, 0, 0, w, h); } catch (e) {}
    }
    if (pane._sfcCanvas && pane.sfc && pane.sfc.on) {
      try { ctx.drawImage(pane._sfcCanvas, 0, 0, w, h); } catch (e) {}
    }
    if (pane._nhcCanvas && pane.nhc && pane.nhc.on) {
      try { ctx.drawImage(pane._nhcCanvas, 0, 0, w, h); } catch (e) {}
    }
  }

  // ========================================================================
  // rail controls (act on the ACTIVE pane, field or layer)
  // ========================================================================
  function controlsCard(kind) {
    var d = document.createElement('div');
    d.className = 'cx-fieldctl';
    d.id = 'cx-ctl-' + kind;
    if (kind === 'mw') {
      d.innerHTML =
        '<h5>Controls — active pane</h5>' +
        '<label>Storm</label><select id="cxmw-storm"></select>' +
        '<label>Overpass</label><select id="cxmw-op"></select>' +
        '<div class="cx-seg2" id="cxmw-prod"></div>' +
        '<div class="cx-seg2" id="cxmw-raw">' +
        '  <button type="button" data-v="0" class="on">Smoothed</button>' +
        '  <button type="button" data-v="1">Raw</button></div>';
    } else {
      d.innerHTML =
        '<h5>Controls — active pane</h5>' +
        '<label>View</label><select id="cxsc-view"></select>' +
        '<label>Pass</label><select id="cxsc-pass"></select>' +
        '<label>Density</label><select id="cxsc-dens">' +
        '  <option value="auto">Auto</option><option value="dense">Dense</option>' +
        '  <option value="sparse">Sparse</option></select>' +
        '<label>Style</label><select id="cxsc-style">' +
        '  <option value="highcontrast">High contrast</option>' +
        '  <option value="sshws">Classic SSHWS</option></select>' +
        '<label>Backdrop</label><select id="cxsc-bg">' +
        '  <option value="clean">Clean IR (gray)</option>' +
        '  <option value="ir">IR (color)</option>' +
        '  <option value="none">None (black)</option></select>';
    }
    return d;
  }
  function wireControls() {
    // MW
    $('cxmw-storm').onchange = function () {
      var p = activeWith('mw'); if (!p) return;
      p.pane.mw.slug = this.value; p.pane.mw.opIdx = -1; p.pane.mw.pinned = false;
      p.pane.mw.flyTo = p.pane.kind === 'mw';
      mwRender(p.pane, p.i); setTimeout(syncControls, 400);
    };
    $('cxmw-op').onchange = function () {
      var p = activeWith('mw'); if (!p) return;
      p.pane.mw.opIdx = +this.value; p.pane.mw.pinned = true;
      mwRender(p.pane, p.i);
    };
    var prod = $('cxmw-prod');
    window.MicrowaveViewer.PRODUCTS.forEach(function (pr) {
      var b = document.createElement('button');
      b.type = 'button'; b.dataset.v = pr.key; b.textContent = pr.label;
      b.onclick = function () {
        var p = activeWith('mw'); if (!p) return;
        p.pane.mw.product = pr.key;
        if (p.pane.kind === 'mw') p.pane.fieldKey = keyForMW(pr.key);
        mwRender(p.pane, p.i); syncControls();
        if (H.markFieldActive) H.markFieldActive();
      };
      prod.appendChild(b);
    });
    $('cxmw-raw').querySelectorAll('button').forEach(function (b) {
      b.onclick = function () {
        var p = activeWith('mw'); if (!p) return;
        p.pane.mw.raw = b.dataset.v === '1';
        mwRender(p.pane, p.i); syncControls();
      };
    });
    // SC
    $('cxsc-view').onchange = function () {
      var p = activeWith('sc'); if (!p) return;
      p.pane.sc.view = this.value; p.pane.sc.passId = 'all';
      p.pane.sc.flyTo = this.value !== 'recent' && p.pane.kind === 'sc';
      scRender(p.pane, p.i); setTimeout(syncControls, 400);
    };
    $('cxsc-pass').onchange = function () {
      var p = activeWith('sc'); if (!p) return;
      p.pane.sc.passId = this.value;
      scRender(p.pane, p.i);
    };
    $('cxsc-dens').onchange = function () {
      var p = activeWith('sc'); if (!p) return;
      p.pane.sc.density = this.value; scDraw(p.pane);
    };
    $('cxsc-style').onchange = function () {
      var p = activeWith('sc'); if (!p) return;
      p.pane.sc.style = this.value; scDraw(p.pane);
      if (H.renderPaneChrome) H.renderPaneChrome(p.i);
    };
    $('cxsc-bg').onchange = function () {
      var p = activeWith('sc'); if (!p) return;
      p.pane.sc.backdrop = this.value;
      // only meaningful when SC is the pane FIELD; as a layer the base tile
      // field already is the imagery (select is disabled there)
      if (p.pane.kind === 'sc') scApplyBackdrop(p.pane);
      if (H.renderPaneChrome) H.renderPaneChrome(p.i);
    };
  }
  function activeWith(kind) {
    var i = CX.active, pane = CX.panes[i];
    if (!pane) return null;
    if (kind === 'mw' && !(pane.kind === 'mw' || (pane.mw && pane.mw.on))) return null;
    if (kind === 'sc' && !(pane.kind === 'sc' || (pane.sc && pane.sc.on))) return null;
    if (kind === 'mw') mwState(pane);
    if (kind === 'sc') scState(pane);
    return { pane: pane, i: i };
  }
  function keyForMW(product) {
    for (var k in MW_KEYMAP) if (MW_KEYMAP[k] === product) return k;
    return 'mw-91h';
  }
  function syncControls() {
    // overlay-layer buttons reflect the ACTIVE pane's actual layer state —
    // per-pane layers with global buttons desync the moment the active
    // pane changes (an "on" toggle must always be truly on)
    var ap = CX && CX.panes && CX.panes[CX.active];
    [['cx-ov-mrms', 'rad'], ['cx-ov-metar', 'obs'], ['cx-ov-sfc', 'sfc'],
     ['cx-ov-nhc', 'nhc'], ['cx-ov-guid', 'guid']]
      .forEach(function (pair) {
        var b = $(pair[0]);
        if (b) b.classList.toggle('on', !!(ap && ap[pair[1]] && ap[pair[1]].on));
      });
    var pane = CX.panes[CX.active];
    // MW card
    var mwCard = $('cx-ctl-mw'), scCard = $('cx-ctl-sc');
    var mwOn = pane && (pane.kind === 'mw' || (pane.mw && pane.mw.on));
    var scOn = pane && (pane.kind === 'sc' || (pane.sc && pane.sc.on));
    if (mwCard) mwCard.style.display = mwOn ? '' : 'none';
    if (scCard) scCard.style.display = scOn ? '' : 'none';
    if (mwOn && MWData.manifest) {
      var st = pane.mw;
      var sSel = $('cxmw-storm');
      sSel.innerHTML = '';
      (MWData.manifest.storms || []).forEach(function (s) {
        var o = document.createElement('option');
        o.value = s.slug;
        o.textContent = (s.name || s.slug) + ' · ' + (s.basin || '') + ' · ' + s.overpass_count + ' passes';
        sSel.appendChild(o);
      });
      if (st.slug) sSel.value = st.slug;
      var oSel = $('cxmw-op');
      oSel.innerHTML = '';
      var ops = st.slug ? (MWData.ops[st.slug] || []) : [];
      ops.forEach(function (o, k) {
        var e = document.createElement('option');
        e.value = String(k);
        e.textContent = fmtZ(o.valid_utc) + ' · ' + (o.sensor || '') + (o.platform ? ' ' + o.platform : '');
        oSel.appendChild(e);
      });
      if (st.opIdx >= 0) oSel.value = String(st.opIdx);
      $('cxmw-prod').querySelectorAll('button').forEach(function (b) {
        b.classList.toggle('on', b.dataset.v === st.product);
      });
      $('cxmw-raw').querySelectorAll('button').forEach(function (b) {
        b.classList.toggle('on', (b.dataset.v === '1') === st.raw);
      });
    }
    if (scOn && SCData.manifest) {
      var sst = pane.sc;
      var vSel = $('cxsc-view');
      vSel.innerHTML = '<option value="recent">Recent passes</option>';
      SCData.storms().forEach(function (s) {
        var o = document.createElement('option');
        o.value = s.key;
        o.textContent = '🌀 ' + String(s.name).toUpperCase() + ' (' + s.n + ')';
        vSel.appendChild(o);
      });
      vSel.value = sst.view;
      var pSel = $('cxsc-pass');
      pSel.innerHTML = '<option value="all">Composite (latest)</option>';
      scViewPassesAll(pane).forEach(function (p) {
        var o = document.createElement('option');
        o.value = p.id;
        o.textContent = p.sensor + ' · ' + fmtZ(p.start_utc);
        pSel.appendChild(o);
      });
      pSel.value = sst.passId;
      $('cxsc-dens').value = sst.density;
      $('cxsc-style').value = sst.style;
      var bgSel = $('cxsc-bg');
      if (bgSel) {
        bgSel.value = sst.backdrop || 'clean';
        bgSel.disabled = pane.kind !== 'sc';
      }
    }
    // overlay toggle reflection
    var ovMW = $('cx-ov-mw'), ovSC = $('cx-ov-sc');
    if (ovMW) ovMW.classList.toggle('on', !!(pane && pane.mw && pane.mw.on));
    if (ovSC) ovSC.classList.toggle('on', !!(pane && pane.sc && pane.sc.on));
  }
  function scViewPassesAll(pane) {
    var st = pane.sc;
    var passes = ((SCData.manifest && SCData.manifest.passes) || []).slice();
    if (st.view !== 'recent') {
      passes = passes.filter(function (p) {
        return (p.storms || []).some(function (s) {
          return window.AscatViewer.stormMatch(s, st.view);
        });
      });
    }
    return passes.slice(0, 20);
  }

  // availability gating for the rail entries (same manifests as before)
  function checkAvailability() {
    MWData.load().then(function () {
      document.querySelectorAll('[data-embed="mw"]').forEach(unGrey);
    }).catch(function () {});
    SCData.load().then(function () {
      document.querySelectorAll('[data-embed="sc"]').forEach(unGrey);
      var ov = $('cx-ov-sc');
      if (ov) ov.disabled = false;
    }).catch(function () {});
    MWData.load().then(function () {
      var ov = $('cx-ov-mw');
      if (ov) ov.disabled = false;
    }).catch(function () {});
    // MRMS: the stub button enables the moment the emitter's manifest exists
    // on R2 (honesty chip flips live via the workflow's first emit)
    MRMSData.load().then(function () {
      var ov = $('cx-ov-mrms');
      if (ov) {
        ov.disabled = false;
        var chip = ov.querySelector('.cx-chip');
        if (chip) chip.remove();
      }
    }).catch(function () {});
    // METAR obs: same honest gate off its own feed
    OBSData.load().then(function () {
      var ov = $('cx-ov-metar');
      if (ov) {
        ov.disabled = false;
        var chip = ov.querySelector('.cx-chip');
        if (chip) chip.remove();
      }
    }).catch(function () {});
    // NHC products: same honest gate
    NHCData.load().then(function () {
      var ov = $('cx-ov-nhc');
      if (ov) {
        ov.disabled = false;
        var chip = ov.querySelector('.cx-chip');
        if (chip) chip.remove();
      }
    }).catch(function () {});
    // TPW moisture: honest gate PLUS freshness — a manifest whose newest
    // frame is hours stale (the mirror lags/stalls) keeps the button
    // disabled with the chip flipped to say so; self-enables on resume
    TPWData.load().then(function () {
      var ov = $('cx-ov-tpw');
      if (!ov) return;
      var chip = ov.querySelector('.cx-chip');
      if (TPWData.fresh()) {
        ov.disabled = false;
        if (chip) chip.remove();
      } else if (chip) {
        chip.textContent = 'stale';
        ov.title += ' — upstream feed currently stalled; enables automatically when it resumes';
      }
    }).catch(function () {});
    // model guidance: same honest gate — enables only when at least one
    // active storm has a current-cycle guidance document
    GDData.load().then(function () {
      var ov = $('cx-ov-guid');
      if (ov) {
        ov.disabled = false;
        var chip = ov.querySelector('.cx-chip');
        if (chip) chip.remove();
      }
    }).catch(function () {});
    // surface analysis: same honest gate
    SFCData.load().then(function () {
      var ov = $('cx-ov-sfc');
      if (ov) {
        ov.disabled = false;
        var chip = ov.querySelector('.cx-chip');
        if (chip) chip.remove();
      }
    }).catch(function () {});
  }
  function unGrey(el) {
    el.classList.remove('coming');
    var chip = el.querySelector('.cx-chip');
    if (chip) chip.remove();
    var meta = el.querySelector('.cx-meta');
    if (meta) meta.innerHTML = meta.innerHTML.replace(/ · $/, '');
  }

  function init(cockpitState, helpers) {
    CX = cockpitState;
    H = helpers || {};
    // controls cards into the rail lists
    var mwList = $('cx-list-mw'), scList = $('cx-list-sc');
    if (mwList) mwList.appendChild(controlsCard('mw'));
    if (scList) scList.appendChild(controlsCard('sc'));
    wireControls();
    checkAvailability();
    var style = document.createElement('style');
    style.textContent =
      '.cx-fieldctl{border-top:1px solid var(--cx-line-soft);margin-top:8px;padding:8px 2px;' +
      ' display:flex;flex-direction:column;gap:5px}' +
      '.cx-fieldctl h5{margin:0 0 2px;font-size:10px;font-weight:700;letter-spacing:.12em;' +
      ' text-transform:uppercase;color:var(--cx-dim)}' +
      '.cx-fieldctl label{font-size:9.5px;font-weight:700;letter-spacing:.08em;' +
      ' text-transform:uppercase;color:var(--cx-dim);margin-top:3px}' +
      '.cx-fieldctl select{font-family:inherit;font-size:11.5px;color:var(--cx-fg);' +
      ' background:rgba(17,23,33,.9);border:1px solid var(--cx-line);border-radius:7px;padding:5px 7px}' +
      '.cx-seg2{display:flex;flex-wrap:wrap;gap:3px;margin-top:3px}' +
      '.cx-seg2 button{flex:1 1 45%;font-family:inherit;font-size:10.5px;font-weight:600;' +
      ' color:var(--cx-mut);background:rgba(17,23,33,.9);border:1px solid var(--cx-line);' +
      ' border-radius:7px;padding:5px 4px;cursor:pointer}' +
      '.cx-seg2 button.on{color:var(--cx-teal);border-color:rgba(73,182,200,.55);' +
      ' background:var(--cx-teal-soft)}' +
      '.cx-pane-lbadge{position:absolute;left:8px;top:44px;z-index:4;pointer-events:none;' +
      ' font-size:10px;font-weight:600;color:#bcdcff;background:rgba(10,13,18,.72);' +
      ' border:1px solid rgba(43,156,255,.35);border-radius:6px;padding:3px 7px}' +
      '.cx-tpw-cbar{position:absolute;right:8px;bottom:64px;z-index:4;' +
      ' pointer-events:none;border:1px solid rgba(255,255,255,.12);border-radius:7px}' +
      '.cx-sc-tip{position:absolute;z-index:5;pointer-events:none;display:none;' +
      ' font-size:11px;line-height:1.45;white-space:nowrap;color:var(--cx-fg);' +
      ' background:rgba(10,13,18,.88);border:1px solid var(--cx-line);' +
      ' border-radius:7px;padding:5px 8px}' +
      '.cx-sc-tip b{color:var(--cx-teal)}';
    document.head.appendChild(style);
  }

  window.CockpitFields = {
    init: init,
    setPaneField: setPaneField,
    clearPaneField: clearPaneField,
    setLayer: setLayer,
    timeSync: timeSync,
    chromeFor: chromeFor,
    compositeOverlays: compositeOverlays,
    syncControls: syncControls,
    scDraw: scDraw,
    MWData: MWData,
    SCData: SCData
  };
})();
