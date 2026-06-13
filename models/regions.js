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
 *   RegionPicker (cyclonicwx-style grouped thumbnail modal)
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

  function _traceGeo(g, geojson, ext, W, H, closeRings) {
    if (!geojson || !geojson.features) return;
    var JUMP = W * 0.5;
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
      var geom = geojson.features[f].geometry; if (!geom) continue;
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

  // ---- one-time picker CSS (self-contained shared component) ----
  function _injectCss() {
    if (document.getElementById('tatreg-css')) return;
    var s = document.createElement('style');
    s.id = 'tatreg-css';
    s.textContent = [
      '.tatreg-btn{background:var(--bg,#131519);color:var(--fg,#e8ebef);border:1px solid var(--border,#2a2e36);',
      'border-radius:6px;padding:8px 12px;font-size:13px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:8px;width:100%;justify-content:space-between}',
      '.tatreg-btn:hover{border-color:var(--accent-2,#5dd3ff)}',
      '.tatreg-btn .tatreg-caret{color:var(--muted,#9199a4);font-size:11px}',
      '.tatreg-overlay{position:fixed;inset:0;z-index:2000;background:rgba(4,8,14,0.72);',
      'display:none;align-items:flex-start;justify-content:center;padding:40px 16px;overflow:auto}',
      '.tatreg-modal{background:var(--panel,#1b1e24);border:1px solid var(--border,#2a2e36);border-radius:12px;',
      'max-width:920px;width:100%;box-shadow:0 18px 60px rgba(0,0,0,0.5)}',
      '.tatreg-head{display:flex;align-items:center;justify-content:space-between;padding:16px 20px;border-bottom:1px solid var(--border,#2a2e36)}',
      '.tatreg-head h3{margin:0;font-size:16px;color:var(--fg,#e8ebef)}',
      '.tatreg-x{background:none;border:none;color:var(--muted,#9199a4);font-size:22px;line-height:1;cursor:pointer;padding:0 4px}',
      '.tatreg-x:hover{color:var(--fg,#e8ebef)}',
      '.tatreg-body{padding:8px 20px 20px}',
      '.tatreg-group-h{color:var(--muted,#9199a4);font-size:11px;font-weight:700;text-transform:uppercase;',
      'letter-spacing:0.6px;margin:18px 0 10px}',
      '.tatreg-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:10px}',
      '.tatreg-card{background:var(--bg,#131519);border:1.5px solid var(--border,#2a2e36);border-radius:8px;',
      'padding:6px;cursor:pointer;display:flex;flex-direction:column;gap:5px;transition:border-color .12s}',
      '.tatreg-card:hover{border-color:var(--accent-2,#5dd3ff)}',
      '.tatreg-card.sel{border-color:var(--accent-2,#5dd3ff);box-shadow:0 0 0 1px var(--accent-2,#5dd3ff)}',
      '.tatreg-card canvas{width:100%;height:auto;border-radius:4px;display:block}',
      '.tatreg-card span{font-size:11.5px;color:var(--fg,#e8ebef);text-align:center;font-weight:600}'
    ].join('');
    document.head.appendChild(s);
  }

  // ---- the picker (cyclonicwx-style grouped thumbnail modal) ----
  function RegionPicker(opts) {
    _injectCss();
    this.geo = opts.geo || null;
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
    var h = document.createElement('h3'); h.textContent = 'Model Regions';
    var x = document.createElement('button'); x.className = 'tatreg-x'; x.innerHTML = '&times;';
    x.setAttribute('aria-label', 'Close'); x.addEventListener('click', function () { self.close(); });
    head.appendChild(h); head.appendChild(x);
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
          var cv = document.createElement('canvas'); cv.width = 116; cv.height = 70;
          var lab = document.createElement('span'); lab.textContent = r.label;
          card.appendChild(cv); card.appendChild(lab);
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
    this._renderThumbs();
    this.overlay.style.display = 'flex';
  };
  RegionPicker.prototype.close = function () { this.overlay.style.display = 'none'; };

  window.TATRegions = {
    GROUPS: GROUPS, list: list, get: get, inRegion: inRegion, extentOf: extentOf,
    project: project, drawBasemap: drawBasemap, RegionPicker: RegionPicker
  };
})();
