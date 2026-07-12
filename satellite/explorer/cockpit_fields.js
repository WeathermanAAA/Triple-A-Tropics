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
  var SCData = {
    manifest: null, loaded: {}, _p: null,
    load: function () {
      var self = this;
      if (this._p) return this._p;
      this._p = fetch(SC_BASE + '/manifest.json?t=' + Date.now(), { cache: 'no-store' })
        .then(function (r) { if (!r.ok) throw 0; return r.json(); })
        .then(function (m) { self.manifest = m; return m; });
      return this._p;
    },
    pass: function (id) {
      var self = this;
      if (this.loaded[id]) return Promise.resolve(this.loaded[id]);
      return fetch(SC_BASE + '/' + id + '.json', { cache: 'default' })
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
             pinned: false };
  }
  function scState(pane) {
    if (!pane.sc) pane.sc = scDefaults();
    return pane.sc;
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
      scCanvas(pane);                       // ensure the overlay canvas exists
      if (!pane._scWired) {
        pane._scWired = true;
        map.on('render', function () { scDraw(pane); });
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
    if (!active || !pane._scPasses.length) return;

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
          { x: xy.x, y: xy.y, kt: kt[i], dir: dr[i] };
      }
    }
    g.lineJoin = 'round'; g.lineCap = 'round';
    var halo = pane.kind !== 'sc';   // layered over imagery -> dark casing
    var cells = Object.keys(grid);
    for (var c = 0; c < cells.length; c++) {
      var cell = grid[cells[c]];
      if (halo) AV.drawBarb(g, cell.x, cell.y, cell.kt, cell.dir, 'rgba(5,10,20,0.82)', style.barbLw + 2.4);
      AV.drawBarb(g, cell.x, cell.y, cell.kt, cell.dir, windColor(scale, cell.kt), style.barbLw);
    }
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
    pane.tv.setImageryVisible(false);
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
    if (pane.tv) pane.tv.setImageryVisible(true);
    if (!(pane.mw && pane.mw.on)) mwClearLayers(pane);
    scDraw(pane);   // clears unless sc layer is on
    if (H.renderPaneChrome) H.renderPaneChrome(i);
  }
  function setLayer(i, kind, on) {
    var pane = CX.panes[i];
    if (!pane || !pane.tv || !pane.tv.map) return;
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
      else scDraw(pane);
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
      return {
        title: 'ASCAT Ocean Winds' + (pane.sc.view !== 'recent' ? ' · ' + pane.sc.view.toUpperCase() : ''),
        sub: newest ? ('Latest pass ' + fmtZ(newest.start_utc) + ' · ' + n + ' pass' + (n === 1 ? '' : 'es') +
                       ' · barbs FROM · C-band underestimates extreme cores') : 'no passes',
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
        '  <option value="sshws">Classic SSHWS</option></select>';
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
      ' border:1px solid rgba(43,156,255,.35);border-radius:6px;padding:3px 7px}';
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
