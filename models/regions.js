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
 *   RegionPicker (TAT grouped thumbnail modal: bright-blue accent, basin
 *     grouping, and true-aspect letterboxed thumbnails)
 */
(function () {
  'use strict';

  // SINGLE SOURCE OF TRUTH for the bright-blue accent shared across every
  // non-storm-nest model product (the picker chrome here + the ENS figure's
  // own chrome via TATRegions.ACCENT). Deliberately NOT the site-global amber
  // --accent; self-scoped so satellite/HAFS keep their amber.
  var ACCENT = '#2b9cff';

  // Bounding boxes: lon -180..180, S/N deg. w > e => crosses the dateline.
  // Grouped by BASIN, the way a TC forecaster reads it (Atlantic -> Pacific ->
  // Indian -> Continents -> Global), NOT a-reference-site's tropics/US/land/hemi split.
  var GROUPS = [
    { key: 'atlantic', label: 'Atlantic', regions: [
      { key: 'atlantic', label: 'Atlantic',          w: -100, e: -5,   s: 0,   n: 55 },
      { key: 'watl',     label: 'West Atlantic',      w: -100, e: -55,  s: 7,   n: 45 },
      { key: 'eatl',     label: 'East Atlantic',      w: -65,  e: 0,    s: 0,   n: 35 },
      { key: 'nafrica',  label: 'North Africa',       w: -25,  e: 60,   s: 0,   n: 42 }
    ]},
    { key: 'pacific', label: 'Pacific', regions: [
      { key: 'epac',     label: 'East Pacific',       w: -140, e: -80,  s: 5,   n: 35 },
      { key: 'nepac',    label: 'Northeast Pacific',  w: -180, e: -110, s: 15,  n: 60 },
      { key: 'npac',     label: 'North Pacific',      w: 120,  e: -110, s: 10,  n: 60 },
      { key: 'wpac',     label: 'West Pacific',       w: 100,  e: 180,  s: 0,   n: 45 },
      { key: 'twpac',    label: 'Tropical WPAC',      w: 100,  e: 180,  s: 0,   n: 35 },
      { key: 'tpac',     label: 'Tropical Pacific',   w: 120,  e: -80,  s: -25, n: 25 },
      { key: 'swpac',    label: 'SW Pacific',         w: 140,  e: -160, s: -35, n: 5 }
    ]},
    { key: 'indian', label: 'Indian Ocean', regions: [
      { key: 'io',       label: 'Indian Ocean',       w: 30,   e: 110,  s: -35, n: 30 }
    ]},
    { key: 'continents', label: 'Continents', regions: [
      { key: 'us',     label: 'United States', w: -125, e: -66,  s: 24,  n: 50 },
      { key: 'wus',    label: 'Western US',    w: -125, e: -100, s: 30,  n: 50 },
      { key: 'eus',    label: 'Eastern US',    w: -100, e: -66,  s: 24,  n: 50 },
      { key: 'namer',  label: 'North America', w: -170, e: -50,  s: 10,  n: 75 },
      { key: 'samer',  label: 'South America', w: -85,  e: -34,  s: -56, n: 13 },
      { key: 'europe', label: 'Europe',        w: -25,  e: 45,   s: 34,  n: 72 },
      { key: 'asia',   label: 'Asia',          w: 40,   e: 150,  s: 5,   n: 75 },
      { key: 'aus',    label: 'Australia',     w: 110,  e: 155,  s: -45, n: -8 }
    ]},
    { key: 'global', label: 'Global', regions: [
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

  // Trace geojson rings/lines into the current path. Longitude is unwrapped
  // CONTINUOUSLY per ring (each vertex takes the +/-360 multiple nearest the
  // previous one), so a contiguous landmass never picks up an internal 360 jump.
  // The OLD code unwrapped per-point in project() and broke on a projected-x JUMP:
  // for a Pacific-centered extent, land near the 180 seam got vertices 360 apart,
  // and for a FILL the break did closePath()+moveTo, chord-closing across the
  // polygon interior -> a triangular land wedge (e.g. Australia in Tropical
  // Pacific). Now each ring is drawn at EVERY 360-shift whose span overlaps the
  // extent's longitude window, so a ring that genuinely straddles the antimeridian
  // is rendered on BOTH edges as separate CLOSED loops; the map-rect clip (below)
  // trims the off-window remainder. closePath therefore only ever joins a ring's
  // true first/last vertices (adjacent on the real boundary) - never a chord.
  function _traceGeo(g, geojson, ext, W, H, closeRings) {
    if (!geojson || !geojson.features) return;
    var wrap = (ext[1] > 180 || ext[0] < -180);
    var lon0 = ext[0], lonSpan = (ext[1] - ext[0]) || 1;
    var latHi = ext[3], latSpan = (ext[3] - ext[2]) || 1;
    function ring(coords) {
      var n = coords.length; if (n < 2) return;
      // continuous unwrapped longitudes (no internal 360 discontinuity)
      var U = new Array(n), minU, maxU, i;
      U[0] = coords[0][0]; minU = maxU = U[0];
      for (i = 1; i < n; i++) {
        var d = coords[i][0] - coords[i - 1][0];
        d -= 360 * Math.round(d / 360);                 // nearest equivalent step
        U[i] = U[i - 1] + d;
        if (U[i] < minU) minU = U[i];
        if (U[i] > maxU) maxU = U[i];
      }
      // every 360-shift whose [minU,maxU]+360k overlaps the visible lon window
      var kMin = Math.ceil((lon0 - maxU) / 360), kMax = Math.floor((ext[1] - minU) / 360);
      if (kMax < kMin) return;                           // not in view at any shift
      if (kMax - kMin > 4) {                             // degenerate (ring wider than the window): one centered copy
        kMin = Math.round(((lon0 + ext[1]) / 2 - (minU + maxU) / 2) / 360); kMax = kMin;
      }
      for (var k = kMin; k <= kMax; k++) {
        var off = 360 * k;
        for (var j = 0; j < n; j++) {
          var X = (U[j] + off - lon0) / lonSpan * W, Y = (latHi - coords[j][1]) / latSpan * H;
          if (j === 0) g.moveTo(X, Y); else g.lineTo(X, Y);
        }
        if (closeRings) g.closePath();
      }
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

  // CANONICAL TAT BASEMAP - the ONE filled basemap for every model plot. Split in
  // two so a viewer can put its DATA FIELD between them and keep the line work ON
  // TOP (canonical order: ocean -> land fill -> [data] -> coast -> country borders
  // -> state borders). Borders are deliberately MUTED/secondary so they never
  // overpower the data. Shared by the viewer canvas AND the picker thumbnails so
  // the two cannot drift. Spec values live in the caller's opts (see enscenters
  // BASEMAP_STYLE); the defaults here are the same canonical hexes.

  // UNDER the data: ocean fill + optional graticule + land fill.
  function drawBasemapFill(g, ext, geo, W, H, opts) {
    opts = opts || {};
    g.save();
    g.beginPath(); g.rect(0, 0, W, H); g.clip();      // never paint a fill outside the map rect
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
    g.restore();
  }

  // ON TOP of the data: coastline, then country (admin_0) borders, then
  // state/province (admin_1) borders. Each guarded so a missing layer or an
  // un-set style is a no-op (never breaks the basemap). Country borders STROKE
  // the same admin_0 polygons used for the land fill (no extra fetch); state
  // borders stroke the optional admin_1 layer.
  function drawBasemapLines(g, ext, geo, W, H, opts) {
    opts = opts || {};
    g.save();
    g.beginPath(); g.rect(0, 0, W, H); g.clip();      // keep border strokes inside the map rect
    g.lineJoin = 'round'; g.lineCap = 'round';
    if (geo && geo.coast && opts.coast) {
      g.strokeStyle = opts.coast; g.lineWidth = opts.coastLw || 0.6; g.beginPath();
      _traceGeo(g, geo.coast, ext, W, H, false); g.stroke();
    }
    if (geo && geo.countries && opts.country) {
      g.strokeStyle = opts.country; g.lineWidth = opts.countryLw || 0.7; g.beginPath();
      _traceGeo(g, geo.countries, ext, W, H, true); g.stroke();
    }
    if (geo && geo.states && opts.state) {
      g.strokeStyle = opts.state; g.lineWidth = opts.stateLw || 0.4; g.beginPath();
      _traceGeo(g, geo.states, ext, W, H, true); g.stroke();
    }
    g.restore();
  }

  // Full basemap (fill + lines) in one call, for callers without a data field on
  // top (the picker thumbnails). Viewers with a data field call the two halves
  // around their field instead.
  function drawBasemap(g, ext, geo, W, H, opts) {
    drawBasemapFill(g, ext, geo, W, H, opts);
    drawBasemapLines(g, ext, geo, W, H, opts);
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
    // admin_1 state/province borders: 50m (10m is huge + slow to draw; 50m is
    // plenty at these scales). OPTIONAL + guarded - a missing/failed layer
    // resolves to null and the basemap simply draws without state borders.
    // Skipped for the picker thumbnails (opts.states === false) where the tiny
    // maps don't need them and the 2 MB fetch would be wasted.
    var wantStates = opts.states !== false;
    var statesP = wantStates
      ? fetch('/ne_50m_admin_1_states_provinces.geojson')
          .then(function (r) { return r.ok ? r.json() : null; })
          .catch(function () { return null; })
      : Promise.resolve(null);
    return Promise.all([
      fetch('/ne_' + landRes + '_admin_0_countries.geojson').then(function (r) { return r.json(); }),
      fetch('/ne_' + coastRes + '_coastline.geojson').then(function (r) { return r.json(); }),
      statesP
    ]).then(function (g) { return { countries: g[0], coast: g[1], states: g[2] }; });
  }

  // ---- shared cache-busting token for a model's per-cycle data fetch ----
  // A backfill/overwrite rewrites a cycle's JSON at its STABLE R2 key, so a
  // stable fetch URL (?v=<cycle>) lets the browser + Cloudflare serve the old
  // copy indefinitely. Key the URL on the cycle's content version instead: the
  // per-cycle generated_at from the manifest (manifest.models[].cycle_versions)
  // when present, so an overwrite changes the URL (busts both caches) while an
  // UNCHANGED cycle keeps its token (stays cached). Fall back to the manifest's
  // top-level generated_at (busts on every publish - the transition state before
  // cycle_versions ships), then to the cycle string. Returned URL-safe. Every
  // ensemble model viewer (ECMWF ENS now, AIFS/GEFS later) shares this.
  function cycleVersion(manifest, slug, cycle) {
    var ver = String(cycle);
    if (manifest) {
      var models = manifest.models || [], entry = null;
      for (var i = 0; i < models.length; i++) {
        if (models[i] && models[i].slug === slug) { entry = models[i]; break; }
      }
      var perCycle = entry && entry.cycle_versions && entry.cycle_versions[cycle];
      if (perCycle) ver = cycle + '-' + perCycle;
      else if (manifest.generated_at) ver = cycle + '-' + manifest.generated_at;
    }
    return ver.replace(/[^A-Za-z0-9]/g, '');
  }

  // ---- one-time picker CSS (self-contained shared component) ----
  // Triple-A-Tropics' OWN styling: a bright-blue accent (--tatreg-acc, scoped to
  // the picker so the site's amber --accent never leaks in), accent-bar group
  // headers, the region label overlaid on the thumbnail with a gradient, and a
  // bright-blue selected state with a corner check.
  function _injectCss() {
    if (document.getElementById('tatreg-css')) return;
    var s = document.createElement('style');
    s.id = 'tatreg-css';
    s.textContent = [
      // Picker-owned accent. Site-wide --accent is amber; we DON'T inherit it -
      // the picker is its own bright-blue component, set on both roots (the
      // trigger button lives outside the overlay). Value = the shared ACCENT.
      '.tatreg-btn,.tatreg-overlay{--tatreg-acc:' + ACCENT + '}',
      '.tatreg-btn{background:var(--bg,#131519);color:var(--fg,#e8ebef);border:1px solid var(--border,#2a2e36);',
      'border-radius:7px;padding:8px 13px;font-size:13px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:8px;width:100%;justify-content:space-between;transition:border-color .12s}',
      '.tatreg-btn:hover{border-color:var(--tatreg-acc)}',
      '.tatreg-btn .tatreg-caret{color:var(--tatreg-acc);font-size:11px}',
      '.tatreg-overlay{position:fixed;inset:0;z-index:2000;background:rgba(4,8,14,0.78);backdrop-filter:blur(3px);',
      'display:none;align-items:flex-start;justify-content:center;padding:40px 16px;overflow:auto}',
      '.tatreg-modal{background:var(--panel,#1b1e24);border:1px solid var(--border,#2a2e36);border-top:3px solid var(--tatreg-acc);',
      'border-radius:5px 5px 12px 12px;max-width:940px;width:100%;box-shadow:0 22px 70px rgba(0,0,0,0.6)}',
      '.tatreg-head{display:flex;align-items:center;gap:11px;padding:15px 20px;border-bottom:1px solid var(--border,#2a2e36)}',
      '.tatreg-head .tatreg-mark{width:7px;height:20px;background:var(--tatreg-acc);border-radius:2px;flex:0 0 auto;box-shadow:0 0 11px rgba(43,156,255,0.6)}',
      '.tatreg-head h3{margin:0;font-size:15px;letter-spacing:1.2px;text-transform:uppercase;color:var(--fg,#e8ebef);font-weight:800;flex:1 1 auto}',
      '.tatreg-x{background:none;border:none;color:var(--muted,#9199a4);font-size:22px;line-height:1;cursor:pointer;padding:0 4px;transition:color .12s}',
      '.tatreg-x:hover{color:var(--tatreg-acc)}',
      '.tatreg-body{padding:4px 20px 22px}',
      '.tatreg-group-h{display:flex;align-items:center;gap:10px;color:var(--tatreg-acc);font-size:11px;font-weight:800;',
      'text-transform:uppercase;letter-spacing:1.5px;margin:21px 0 12px}',
      '.tatreg-group-h:first-child{margin-top:12px}',
      '.tatreg-group-h::before{content:"";width:16px;height:3px;background:var(--tatreg-acc);border-radius:2px;flex:0 0 auto}',
      '.tatreg-group-h::after{content:"";flex:1 1 auto;height:1px;background:var(--border,#2a2e36)}',
      '.tatreg-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(132px,1fr));gap:12px}',
      '.tatreg-card{position:relative;border:1px solid var(--border,#2a2e36);border-radius:9px;overflow:hidden;',
      'cursor:pointer;background:#070d18;transition:border-color .12s,box-shadow .12s,transform .08s}',
      '.tatreg-card:hover{border-color:var(--tatreg-acc);transform:translateY(-1px)}',
      '.tatreg-card.sel{border-color:var(--tatreg-acc);box-shadow:0 0 0 2px var(--tatreg-acc) inset}',
      // Thumb is a fixed 8:5 frame; the canvas keeps that aspect (intrinsic
      // 320x200) so the map inside is letterboxed, never stretched.
      '.tatreg-thumb{position:relative;display:block;line-height:0;background:#070d18}',
      '.tatreg-thumb canvas{width:100%;height:auto;display:block}',
      '.tatreg-lab{position:absolute;left:0;right:0;bottom:0;padding:15px 9px 6px;font-size:11.5px;font-weight:700;',
      'color:#fff;text-align:left;letter-spacing:0.2px;background:linear-gradient(180deg,rgba(7,13,24,0),rgba(7,13,24,0.9))}',
      '.tatreg-check{position:absolute;top:6px;right:6px;width:18px;height:18px;border-radius:50%;background:var(--tatreg-acc);',
      'color:#04121f;font-size:12px;font-weight:900;line-height:18px;text-align:center;display:none;box-shadow:0 1px 4px rgba(0,0,0,0.45)}',
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
          // Fixed 8:5 bitmap; each region's true-aspect map is letterboxed inside
          // it (see _renderThumbs), so every card is the same size and no map is
          // stretched. Generous resolution keeps it crisp when CSS downscales.
          var cv = document.createElement('canvas'); cv.width = 320; cv.height = 200;
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

  // Render each thumbnail at its region's TRUE equirectangular aspect ratio
  // (lon-span / lat-span of the display extent), centered and letterboxed inside
  // the fixed 8:5 card - so a wide box (Tropical Pacific, North Hemisphere) and a
  // boxy one (Western US, Australia) keep their real proportions, never stretched
  // to fill. The letterbox bars are the card frame tone; a hairline outlines the
  // map so it reads as a framed inset.
  RegionPicker.prototype._renderThumbs = function () {
    if (this._thumbsDone || !this.geo) return;
    for (var k in this._cards) if (this._cards.hasOwnProperty(k)) {
      var c = this._cards[k], cv = c.canvas, W = cv.width, H = cv.height;
      var g = cv.getContext('2d');
      var ext = extentOf(c.region);
      var ar = (ext[1] - ext[0]) / (ext[3] - ext[2]);   // true aspect of the crop
      // letterbox fill (frame tone, deliberately darker than the map ocean)
      g.clearRect(0, 0, W, H);
      g.fillStyle = '#070d18'; g.fillRect(0, 0, W, H);
      // largest ar-rect that fits, centered (fit-to-width else fit-to-height)
      var mw = W, mh = W / ar;
      if (mh > H) { mh = H; mw = H * ar; }
      var ox = (W - mw) / 2, oy = (H - mh) / 2;
      g.save();
      g.beginPath(); g.rect(ox, oy, mw, mh); g.clip();
      g.translate(ox, oy);
      drawBasemap(g, ext, this.geo, mw, mh, {
        ocean: '#0b1422', land: '#37475f',
        coast: 'rgba(150,175,205,0.32)', coastLw: 1.0,
        grid: 'rgba(255,255,255,0.05)', gridLw: 0.6
      });
      g.restore();
      g.strokeStyle = 'rgba(255,255,255,0.14)'; g.lineWidth = 1;
      g.strokeRect(ox + 0.5, oy + 0.5, mw - 1, mh - 1);
    }
    this._thumbsDone = true;
  };

  RegionPicker.prototype.open = function () {
    var self = this;
    this.overlay.style.display = 'flex';
    if (this.geo) { this._renderThumbs(); return; }
    loadGeo({ coast: THUMB_RES, land: THUMB_RES, states: false })   // 110m thumbnails (fast; no state borders)
      .then(function (g) { self.geo = g; self._renderThumbs(); })
      .catch(function () {});
  };
  RegionPicker.prototype.close = function () { this.overlay.style.display = 'none'; };

  window.TATRegions = {
    GROUPS: GROUPS, list: list, get: get, inRegion: inRegion, extentOf: extentOf,
    project: project, drawBasemap: drawBasemap,
    drawBasemapFill: drawBasemapFill, drawBasemapLines: drawBasemapLines,
    RegionPicker: RegionPicker,
    loadGeo: loadGeo, COAST_RES: COAST_RES, LAND_RES: LAND_RES, THUMB_RES: THUMB_RES,
    ACCENT: ACCENT, cycleVersion: cycleVersion
  };
})();
