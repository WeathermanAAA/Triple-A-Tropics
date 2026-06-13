/* Shared "Model Regions" layer for the non-storm-nest model viewers.
 *
 * Used now by the ECMWF ENS Ensemble Cyclone Centers viewer; built to be reused
 * unchanged by AIFS-ENS, GEFS, GDM-FNV3/GenCast and any future synoptic
 * ensemble / global product. Storm-NEST viewers (HAFS, which auto-centers on a
 * storm) are EXCLUDED - they keep their storm-following framing and never load
 * this file.
 *
 * The region is a VIEW CROP only: detection in the R2 JSON is fully global. A
 * region sets the map extent + filters the displayed scatter + scopes the
 * per-member peak table. Pacific boxes cross the dateline (w > e wraps past 180);
 * the crop test and the map extent both handle the wrap.
 *
 * Exposes window.TATRegions:
 *   GROUPS, list(), get(key), inRegion(lon,lat,r), extentOf(r),
 *   project(lon,lat,extent,W,H), drawBasemap(ctx,extent,geo,W,H,opts),
 *   RegionPicker (a-reference-site-style grouped thumbnail modal)
 */
(function () {
  'use strict';

  // Bounding boxes: lon -180..180, S/N deg. w > e => crosses the dateline.
  var GROUPS = [
    { key: 'tropics', label: 'Tropics', regions: [
      { key: 'atlantic', label: 'Atlantic',          w: -100, e: -5,   s: 0,   n: 55 },
      { key: 'watl',     label: 'West Atlantic',      w: -100, e: -55,  s: 7,   n: 45 },
      { key: 'eatl',     label: 'East Atlantic',      w: -65,  e: 0,    s: 0,   n: 35 },
      { key: 'nafrica',  label: 'North Africa',       w: -25,  e: 60,   s: 0,   n: 42 },
      { key: 'epac',     label: 'East Pacific',       w: -140, e: -80,  s: 5,   n: 35 },
      { key: 'nepac',    label: 'Northeast Pacific',  w: -180, e: -110, s: 15,  n: 60 },
      { key: 'npac',     label: 'North Pacific',      w: 120,  e: -110, s: 10,  n: 60 },
      { key: 'wpac',     label: 'West Pacific',       w: 100,  e: 180,  s: 0,   n: 45 },
      { key: 'twpac',    label: 'Tropical WPAC',      w: 100,  e: 180,  s: 0,   n: 35 },
      { key: 'io',       label: 'Indian Ocean',       w: 30,   e: 110,  s: -35, n: 30 },
      { key: 'tpac',     label: 'Tropical Pacific',   w: 120,  e: -80,  s: -25, n: 25 },
      { key: 'swpac',    label: 'SW Pacific',         w: 140,  e: -160, s: -35, n: 5 }
    ]},
    { key: 'us', label: 'United States', regions: [
      { key: 'us',  label: 'United States', w: -125, e: -66,  s: 24, n: 50 },
      { key: 'wus', label: 'Western US',    w: -125, e: -100, s: 30, n: 50 },
      { key: 'eus', label: 'Eastern US',    w: -100, e: -66,  s: 24, n: 50 }
    ]},
    { key: 'land', label: 'Land', regions: [
      { key: 'namer',  label: 'North America', w: -170, e: -50, s: 10,  n: 75 },
      { key: 'aus',    label: 'Australia',     w: 110,  e: 155, s: -45, n: -8 },
      { key: 'asia',   label: 'Asia',          w: 40,   e: 150, s: 5,   n: 75 },
      { key: 'europe', label: 'Europe',        w: -25,  e: 45,  s: 34,  n: 72 },
      { key: 'samer',  label: 'South America', w: -85,  e: -34, s: -56, n: 13 }
    ]},
    { key: 'hemi', label: 'Hemispheres', regions: [
      { key: 'nhem',   label: 'North Hemisphere', w: -180, e: 180, s: 0,   n: 88 },
      { key: 'shem',   label: 'South Hemisphere', w: -180, e: 180, s: -88, n: 0 },
      { key: 'global', label: 'Global',           w: -180, e: 180, s: -88, n: 88 }
    ]}
  ];

  var BYKEY = {};
  GROUPS.forEach(function (g) { g.regions.forEach(function (r) { BYKEY[r.key] = r; }); });

  function get(key) { return BYKEY[key] || null; }
  function list() { return Object.keys(BYKEY); }

  // Dateline-aware crop test. lon in -180..180.
  function inRegion(lon, lat, r) {
    if (lat < r.s || lat > r.n) return false;
    if (r.w <= r.e) return lon >= r.w && lon <= r.e;
    return lon >= r.w || lon <= r.e;   // wraps past 180
  }

  // Contiguous display extent [lonMin, lonMax, latS, latN]; a wrapping box
  // (w > e) extends lonMax past 180 so the projection stays monotonic. A
  // full-globe box (Hemispheres / Global) displays Pacific-centered ([0,360],
  // dateline at center) to keep WPAC systems together.
  function extentOf(r) {
    if (r.w <= -180 && r.e >= 180) return [0, 360, r.s, r.n];
    var lonMax = (r.e >= r.w) ? r.e : r.e + 360;
    return [r.w, lonMax, r.s, r.n];
  }

  // Equirectangular projection into [0,W]x[0,H] for a (possibly wrapping) extent.
  function project(lon, lat, ext, W, H) {
    var L = lon;
    if (ext[1] > 180 && L < ext[0]) L += 360;
    return [(L - ext[0]) / (ext[1] - ext[0]) * W, (ext[3] - lat) / (ext[3] - ext[2]) * H];
  }

  // per-feature lon/lat bbox, computed once and cached on the feature, so a
  // region crop can skip the (most) features it does not show - the difference
  // between a snappy 10m redraw and a multi-second one.
  function _bbox(feat) {
    if (feat._bb !== undefined) return feat._bb;
    var g = feat.geometry;
    if (!g || !g.coordinates) return (feat._bb = null);
    var mnx = 1e9, mxx = -1e9, mny = 1e9, mxy = -1e9;
    (function scan(c) {
      if (typeof c[0] === 'number') {
        if (c[0] < mnx) mnx = c[0]; if (c[0] > mxx) mxx = c[0];
        if (c[1] < mny) mny = c[1]; if (c[1] > mxy) mxy = c[1];
      } else { for (var i = 0; i < c.length; i++) scan(c[i]); }
    })(g.coordinates);
    return (feat._bb = [mnx, mxx, mny, mxy]);
  }

  function _visible(bb, ext, wrap) {
    if (!bb) return true;
    if (bb[3] < ext[2] || bb[2] > ext[3]) return false;          // lat outside
    if (!wrap && (bb[1] < ext[0] || bb[0] > ext[1])) return false; // lon outside (non-wrapping)
    return true;
  }

  function _traceGeo(g, geojson, ext, W, H, closeRings) {
    if (!geojson || !geojson.features) return;
    var JUMP = W * 0.5, wrap = (ext[1] > 180 || ext[0] < -180);
    function ring(coords) {
      var prevX = null, started = false;
      for (var i = 0; i < coords.length; i++) {
        var p = project(coords[i][0], coords[i][1], ext, W, H);
        if (prevX === null || Math.abs(p[0] - prevX) > JUMP) {
          if (started && closeRings) g.closePath();
          g.moveTo(p[0], p[1]); started = true;
        } else { g.lineTo(p[0], p[1]); }
        prevX = p[0];
      }
      if (closeRings && started) g.closePath();
    }
    for (var f = 0; f < geojson.features.length; f++) {
      var feat = geojson.features[f];
      if (!_visible(_bbox(feat), ext, wrap)) continue;
      var geom = feat.geometry; if (!geom) continue;
      var t = geom.type, co = geom.coordinates;
      if (t === 'Polygon') { for (var a = 0; a < co.length; a++) ring(co[a]); }
      else if (t === 'MultiPolygon') { for (var b = 0; b < co.length; b++) for (var c = 0; c < co[b].length; c++) ring(co[b][c]); }
      else if (t === 'LineString') { ring(co); }
      else if (t === 'MultiLineString') { for (var e2 = 0; e2 < co.length; e2++) ring(co[e2]); }
    }
  }

  // Shared basemap (ocean + graticule + land fill + coast) for an extent. Used
  // by the viewer canvas AND the picker thumbnails so they cannot drift.
  function drawBasemap(g, ext, geo, W, H, opts) {
    opts = opts || {};
    g.clearRect(0, 0, W, H);
    g.fillStyle = opts.ocean || '#07101c';
    g.fillRect(0, 0, W, H);
    if (opts.grid) {
      g.strokeStyle = opts.grid; g.lineWidth = opts.gridLw || 0.5; g.beginPath();
      var l0 = Math.ceil(ext[0] / 30) * 30;
      for (var lon = l0; lon <= ext[1]; lon += 30) { var p = project(lon, 0, ext, W, H); g.moveTo(p[0], 0); g.lineTo(p[0], H); }
      var b0 = Math.ceil(ext[2] / 30) * 30;
      for (var lat = b0; lat <= ext[3]; lat += 30) { var q = project(0, lat, ext, W, H); g.moveTo(0, q[1]); g.lineTo(W, q[1]); }
      g.stroke();
    }
    if (geo && geo.countries) {
      g.fillStyle = opts.land || '#2f3f59'; g.beginPath();
      _traceGeo(g, geo.countries, ext, W, H, true); g.fill('nonzero');
    }
    if (geo && geo.coast && opts.coast) {
      g.strokeStyle = opts.coast; g.lineWidth = opts.coastLw || 0.6;
      g.lineJoin = 'round'; g.lineCap = 'round'; g.beginPath();
      _traceGeo(g, geo.coast, ext, W, H, false); g.stroke();
    }
  }

  // Basemap coastline/land resolution - ONE SHARED source of truth for every
  // non-storm-nest viewer (change here, not per-viewer). The main figure uses
  // Natural Earth 10m (high-detail coastlines); the picker thumbnails use 110m
  // (tiny maps - drawing full 10m geojson into 23 cards would be far too slow).
  var COAST_RES = '10m';   // figure COASTLINE resolution (the shared constant; high detail)
  var LAND_RES = '50m';    // land-fill resolution (solid fill under the 10m coast; lighter)
  var THUMB_RES = '110m';  // picker thumbnail resolution (tiny maps)

  // Fetch the basemap geojson at the shared resolution. opts.coast / opts.land
  // override (the picker passes 110m). Files live same-origin at /ne_<res>_*.
  function loadGeo(opts) {
    opts = opts || {};
    var coastRes = opts.coast || COAST_RES, landRes = opts.land || LAND_RES;
    return Promise.all([
      fetch('/ne_' + landRes + '_admin_0_countries.geojson').then(function (r) { return r.json(); }),
      fetch('/ne_' + coastRes + '_coastline.geojson').then(function (r) { return r.json(); })
    ]).then(function (g) { return { countries: g[0], coast: g[1] }; });
  }

  // ---- one-time picker CSS (self-contained shared component) ----
  // Triple-A-Tropics' OWN styling (NOT a-reference-site): amber accent identity,
  // accent-bar group headers, the region label overlaid on the thumbnail with a
  // gradient, and an amber selected state with a corner check.
  function _injectCss() {
    if (document.getElementById('tatreg-css')) return;
    var s = document.createElement('style');
    s.id = 'tatreg-css';
    s.textContent = [
      '.tatreg-btn{background:var(--bg,#131519);color:var(--fg,#e8ebef);border:1px solid var(--border,#2a2e36);',
      'border-radius:6px;padding:8px 12px;font-size:13px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:8px;width:100%;justify-content:space-between}',
      '.tatreg-btn:hover{border-color:var(--accent,#ffb83a)}',
      '.tatreg-btn .tatreg-caret{color:var(--accent,#ffb83a);font-size:11px}',
      '.tatreg-overlay{position:fixed;inset:0;z-index:2000;background:rgba(4,8,14,0.78);backdrop-filter:blur(3px);',
      'display:none;align-items:flex-start;justify-content:center;padding:40px 16px;overflow:auto}',
      '.tatreg-modal{background:var(--panel,#1b1e24);border:1px solid var(--border,#2a2e36);border-top:3px solid var(--accent,#ffb83a);',
      'border-radius:4px 4px 12px 12px;max-width:940px;width:100%;box-shadow:0 22px 70px rgba(0,0,0,0.6)}',
      '.tatreg-head{display:flex;align-items:center;gap:11px;padding:15px 20px;border-bottom:1px solid var(--border,#2a2e36)}',
      '.tatreg-head .tatreg-mark{width:7px;height:20px;background:var(--accent,#ffb83a);border-radius:2px;flex:0 0 auto}',
      '.tatreg-head h3{margin:0;font-size:15px;letter-spacing:1.2px;text-transform:uppercase;color:var(--fg,#e8ebef);font-weight:800;flex:1 1 auto}',
      '.tatreg-x{background:none;border:none;color:var(--muted,#9199a4);font-size:22px;line-height:1;cursor:pointer;padding:0 4px}',
      '.tatreg-x:hover{color:var(--accent,#ffb83a)}',
      '.tatreg-body{padding:6px 20px 22px}',
      '.tatreg-group-h{display:flex;align-items:center;gap:9px;color:var(--accent,#ffb83a);font-size:11px;font-weight:800;',
      'text-transform:uppercase;letter-spacing:1.4px;margin:20px 0 11px}',
      '.tatreg-group-h::before{content:"";width:14px;height:3px;background:var(--accent,#ffb83a);border-radius:2px}',
      '.tatreg-group-h::after{content:"";flex:1 1 auto;height:1px;background:var(--border,#2a2e36)}',
      '.tatreg-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(128px,1fr));gap:11px}',
      '.tatreg-card{position:relative;border:1px solid var(--border,#2a2e36);border-radius:9px;overflow:hidden;',
      'cursor:pointer;background:#0b1320;transition:border-color .12s,transform .08s}',
      '.tatreg-card:hover{border-color:var(--accent-2,#5dd3ff);transform:translateY(-1px)}',
      '.tatreg-card.sel{border-color:var(--accent,#ffb83a);box-shadow:0 0 0 2px var(--accent,#ffb83a) inset}',
      '.tatreg-thumb{position:relative;display:block;line-height:0}',
      '.tatreg-thumb canvas{width:100%;height:auto;display:block}',
      '.tatreg-lab{position:absolute;left:0;right:0;bottom:0;padding:14px 9px 6px;font-size:11.5px;font-weight:700;',
      'color:#fff;text-align:left;background:linear-gradient(180deg,rgba(7,16,28,0),rgba(7,16,28,0.88))}',
      '.tatreg-check{position:absolute;top:6px;right:6px;width:17px;height:17px;border-radius:50%;background:var(--accent,#ffb83a);',
      'color:#1a1205;font-size:12px;font-weight:900;line-height:17px;text-align:center;display:none}',
      '.tatreg-card.sel .tatreg-check{display:block}'
    ].join('');
    document.head.appendChild(s);
  }

  // ---- the picker (a-reference-site-style grouped thumbnail modal) ----
  function RegionPicker(opts) {
    _injectCss();
    this.geo = null;   // loaded lazily at THUMB_RES on first open
    this.onPick = opts.onPick || function () {};
    this.current = opts.current || 'atlantic';
    this._thumbsDone = false;
    this._cards = {};
    this._build();
  }

  RegionPicker.prototype._build = function () {
    var self = this;
    var ov = document.createElement('div'); ov.className = 'tatreg-overlay';
    var modal = document.createElement('div'); modal.className = 'tatreg-modal';
    var head = document.createElement('div'); head.className = 'tatreg-head';
    var mark = document.createElement('span'); mark.className = 'tatreg-mark';
    var h = document.createElement('h3'); h.textContent = 'Model Regions';
    var x = document.createElement('button'); x.className = 'tatreg-x'; x.innerHTML = '&times;';
    x.setAttribute('aria-label', 'Close'); x.addEventListener('click', function () { self.close(); });
    head.appendChild(mark); head.appendChild(h); head.appendChild(x);
    var body = document.createElement('div'); body.className = 'tatreg-body';

    for (var gi = 0; gi < GROUPS.length; gi++) {
      var grp = GROUPS[gi];
      var gh = document.createElement('div'); gh.className = 'tatreg-group-h'; gh.textContent = grp.label;
      body.appendChild(gh);
      var grid = document.createElement('div'); grid.className = 'tatreg-grid';
      for (var ri = 0; ri < grp.regions.length; ri++) {
        (function (r) {
          var card = document.createElement('div');
          card.className = 'tatreg-card' + (r.key === self.current ? ' sel' : '');
          card.setAttribute('data-key', r.key);
          card.setAttribute('role', 'button'); card.tabIndex = 0;
          var thumb = document.createElement('div'); thumb.className = 'tatreg-thumb';
          var cv = document.createElement('canvas'); cv.width = 128; cv.height = 78;
          var lab = document.createElement('span'); lab.className = 'tatreg-lab'; lab.textContent = r.label;
          var chk = document.createElement('span'); chk.className = 'tatreg-check'; chk.innerHTML = '&#10003;';
          thumb.appendChild(cv); thumb.appendChild(lab); thumb.appendChild(chk);
          card.appendChild(thumb);
          card.addEventListener('click', function () { self._pick(r.key); });
          card.addEventListener('keydown', function (ev) {
            if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); self._pick(r.key); }
          });
          grid.appendChild(card);
          self._cards[r.key] = { card: card, canvas: cv, region: r };
        })(grp.regions[ri]);
      }
      body.appendChild(grid);
    }

    modal.appendChild(head); modal.appendChild(body); ov.appendChild(modal);
    ov.addEventListener('click', function (e) { if (e.target === ov) self.close(); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && ov.style.display === 'flex') self.close();
    });
    document.body.appendChild(ov);
    this.overlay = ov;
  };

  RegionPicker.prototype._pick = function (key) {
    this.setCurrent(key);
    this.close();
    this.onPick(key);
  };

  RegionPicker.prototype.setCurrent = function (key) {
    this.current = key;
    for (var k in this._cards) if (this._cards.hasOwnProperty(k)) {
      this._cards[k].card.classList.toggle('sel', k === key);
    }
  };

  RegionPicker.prototype.setGeo = function (geo) {
    this.geo = geo;
    this._thumbsDone = false;
  };

  RegionPicker.prototype._renderThumbs = function () {
    if (this._thumbsDone || !this.geo) return;
    for (var k in this._cards) if (this._cards.hasOwnProperty(k)) {
      var c = this._cards[k];
      var g = c.canvas.getContext('2d');
      drawBasemap(g, extentOf(c.region), this.geo, c.canvas.width, c.canvas.height,
                  { ocean: '#0b1422', land: '#37475f', grid: 'rgba(255,255,255,0.04)', gridLw: 0.5 });
    }
    this._thumbsDone = true;
  };

  RegionPicker.prototype.open = function () {
    var self = this;
    this.overlay.style.display = 'flex';
    if (this.geo) { this._renderThumbs(); return; }
    loadGeo({ coast: THUMB_RES, land: THUMB_RES })   // 110m thumbnails (fast)
      .then(function (g) { self.geo = g; self._renderThumbs(); })
      .catch(function () {});
  };
  RegionPicker.prototype.close = function () { this.overlay.style.display = 'none'; };

  window.TATRegions = {
    GROUPS: GROUPS, list: list, get: get, inRegion: inRegion, extentOf: extentOf,
    project: project, drawBasemap: drawBasemap, RegionPicker: RegionPicker,
    loadGeo: loadGeo, COAST_RES: COAST_RES, LAND_RES: LAND_RES, THUMB_RES: THUMB_RES
  };
})();
