/* Ensemble Cyclone Centers viewer (/models/).
 *
 * The canvas is a SELF-CONTAINED, copyable, TAT-branded figure: header (title +
 * metadata) top-left, the region-cropped map (bold hollow-ring pressure ramp,
 * Pacific-centered, dateline-safe via the shared TATRegions layer), the
 * pressure-bin legend bottom-left, the per-member peak table as a right column,
 * and the @WeathermanAAA_ watermark bottom-right - ALL drawn on the canvas, so a
 * right-click "Copy image / Save image" yields the complete figure as a PNG
 * (the canvas is untainted - every draw is local). The only HTML over the canvas
 * is the hover tooltip (pointer-events:none, NOT part of the exported image).
 *
 * Animator: an ACCUMULATING TRAIL (no fading). At frame N, steps 0..N-1 are
 * HOLLOW rings (committed to an offscreen trail layer) and the CURRENT step N is
 * FILLED solid circles drawn on top, so the current centers stand out as the
 * cloud grows toward the full member spread. A toggle switches to "Current step
 * only".
 *
 * Isolated from the HAFS viewer (separate IIFE, enscenters-* ids).
 */
(function () {
  'use strict';

  var BASE = 'https://cdn.triple-a-tropics.com';
  var MANIFEST_URL = BASE + '/models/enscenters/manifest.json';
  var DATA_BASE = BASE + '/models/enscenters/';

  var SPEED_OPTIONS = [0.5, 1, 2, 4];
  var BASE_FPS = 4;
  var POLL_IDLE_MS = 300000;
  var GIF_MAX_W = 1600;          // cap GIF width; high enough to export at ~source res (color fidelity)
  var GIF_WORKER_URL = 'https://cdnjs.cloudflare.com/ajax/libs/gif.js/0.2.0/gif.worker.js';
  var GIF_LAST_DWELL = 6;        // hold the full-cloud frame N x longer before looping
  // SIZE/QUALITY presets. The lever is RESOLUTION + FRAME COUNT, never palette
  // quality (always gif.js quality:1, no dither) - so colors stay true at any size.
  var GIF_PRESET_W = { full: 1600, balanced: 1200, discord: 900 };   // per-preset width cap
  var GIF_DISCORD_TARGET = 9.5 * 1024 * 1024;   // aim under this (safety margin below 10 MB)
  var GIF_HARD_CAP = 10 * 1024 * 1024;          // Discord free-tier reject threshold
  var GIF_FLOOR_FRAMES = 8;                     // don't trim a Discord loop below this

  var DEFAULT_REGION = 'atlantic';
  var LS_REGION = 'ens.region';
  var LS_GIFPRESET = 'ens.gifpreset';   // GIF size/quality preset: full | balanced | discord
  var LS_TRAIL = 'ens.trail';
  var LS_STYLE = 'ens.style';     // 'cheerios' (default) | 'lines'
  var LS_MEAN = 'ens.mean';       // 'on' | 'off' (default)
  var LS_PPTS = 'ens.ppts';       // mean-track dated MSLP labels: 'on' | 'off' (default)
  var LS_MINP = 'ens.minp';       // region-deepest-center highlight: 'on' | 'off' (default)
  // Toolkit overlays consume the sibling tracks JSON (loaded whenever a tracks file
  // exists - the DEFAULT Cheerios view draws subtle connector threads from it; Lines
  // / Mean / Obs use it too). A model without a tracks file falls back cleanly: plain
  // Cheerios dots, track toggles hidden. Two data styles:
  //   CHEERIOS (default) = pressure-bin dots (hollow trail + filled current head)
  //     PLUS thin neutral connector threads linking each member's points.
  //   LINES = bold pressure-colored per-member spaghetti tracks.
  // The mean track is BOLD with a dark casing so it pops without burying the field.
  var LINE_LW = 1.0, LINE_ALPHA = 0.5;          // bold Lines-mode spaghetti
  // Cheerios connector threads: thin, low-opacity, neutral grey - link each member's
  // points without competing with the colored dots.
  var CONNECTOR_COLOR = 'rgba(176,190,212,0.30)', CONNECTOR_LW = 0.8;
  var MEAN_LW = 3.0, MEAN_DIM_LW = 1.5, MEAN_DIM_ALPHA = 0.45;
  var MEAN_CASING = 'rgba(7,16,28,0.9)';
  var MEAN_MIN_MEMBERS = 3;       // hide clusters tinier than this (unreliable)
  // ONE canonical map-box aspect (w/h) for EVERY region + model, so every plot is the
  // same shape/size. 2.0 is forced by Global: lon spans the full 360 and lat caps at
  // the poles (180), so Global can only fill a 2:1 box without cropping lon or
  // letterboxing. Each region's extent is EXPANDED (never cropped) on its deficient
  // axis to this aspect (see frameExtent), so the map fills the box undistorted with
  // no letterbox bands. Legend is pinned top-left, the Vmax plume top-right.
  var BOX_ASPECT = 2.0;
  var PLUME_W = 192, PLUME_H = 120;
  // Stage 2b OBS-vs-envelope. The observed-system feed is the SAME global feed the
  // home/global tracks map already reads (cdn .../global_storms.geojson, written by
  // the main-repo ace_core storm-display path). It is fetched INDEPENDENTLY and
  // READ-ONLY here - the floater poller / floater code is never touched or imported,
  // and nothing here writes back to track/ACE/climo (invest isolation, both ways).
  var LS_OBS = 'ens.obs';
  var OBS_FEED_URL = BASE + '/global_storms.geojson';
  var OBS_MATCH_MAX_DEG = 9.0;    // refuse a match beyond this great-circle gap
  // The observed-position marker matches the home track maps: an INVEST gets the red
  // X (NHC invest-area convention); an active STORM gets the spinning TAT hurricane
  // glyph, colored + lettered by its SSHWS stage. Glyph path + scale + invest-X geom
  // mirror generate_tracks_plot.py (HURRICANE_PATH / ICON_GLYPH_SCALE / the X path).
  var HURRICANE_PATH_D = 'M 16.37,-28.27 C 13.58,-28.13 11.51,-27.90 9.23,-27.49 C 1.27,-26.06 -5.88,-22.70 -10.92,-18.02 C -14.83,-14.40 -17.41,-10.06 -18.49,-5.32 C -18.95,-3.30 -19.15,-1.42 -19.15,0.91 C -19.15,2.53 -19.09,3.28 -18.89,4.45 C -18.38,7.38 -17.47,9.46 -15.41,12.37 C -13.88,14.54 -13.43,15.31 -13.20,16.13 C -13.11,16.44 -13.09,16.62 -13.09,17.14 C -13.10,17.93 -13.20,18.32 -13.67,19.28 C -15.30,22.59 -18.65,24.93 -23.49,26.14 C -25.26,26.58 -27.29,26.87 -29.18,26.95 L -30.00,26.98 L -29.65,27.06 C -27.33,27.62 -24.41,28.05 -21.57,28.27 C -20.04,28.38 -16.31,28.38 -14.80,28.27 C -12.93,28.13 -11.43,27.95 -9.77,27.67 C -0.59,26.14 7.56,22.03 12.68,16.37 C 16.22,12.45 18.28,8.10 18.93,3.13 C 19.64,-2.25 18.99,-6.47 16.84,-10.16 C 16.48,-10.80 15.79,-11.82 14.99,-12.95 C 13.61,-14.89 13.18,-15.77 13.12,-16.83 C 13.07,-17.61 13.23,-18.26 13.71,-19.23 C 14.97,-21.79 17.38,-23.84 20.67,-25.16 C 23.13,-26.14 26.24,-26.77 29.15,-26.87 L 30.00,-26.90 L 29.67,-26.98 C 29.13,-27.12 27.57,-27.44 26.66,-27.58 C 24.96,-27.87 23.39,-28.05 21.66,-28.18 C 20.72,-28.25 17.16,-28.30 16.37,-28.27 Z';
  // Path2D may be absent under jsdom (canvas stubbed); guard so the glyph degrades to
  // a filled disc there (tests) while drawing the real icon in the browser/node-canvas.
  var HURRICANE_PATH2D = (typeof Path2D !== 'undefined') ? new Path2D(HURRICANE_PATH_D) : null;
  var GLYPH_SCALE = 0.5;          // x the +/-30-unit path -> ~30 px glyph
  var INVEST_RED = '#ff2a2a';
  // SSHWS stage -> {fill color, letter} for the active-storm glyph (matches
  // ace_core.SSHS_COLORS + sshs_class/sshs_label thresholds, 1-min wind in kt).
  function sshsMark(kt) {
    if (kt == null || isNaN(kt)) return { color: '#3fa4ff', letter: 'D' };
    if (kt < 34) return { color: '#3fa4ff', letter: 'D' };
    if (kt < 64) return { color: '#46c56a', letter: 'S' };
    if (kt < 83) return { color: '#ffe14d', letter: '1' };
    if (kt < 96) return { color: '#ff9a2f', letter: '2' };
    if (kt < 113) return { color: '#ff4d3b', letter: '3' };
    if (kt < 137) return { color: '#e33ad4', letter: '4' };
    return { color: '#b03bff', letter: '5' };
  }
  var COMPASS8 = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
  var MIN_FIG_W = 760;     // figure renders at least this wide (legible PNG; scales on mobile)
  var WATERMARK = '@WeathermanAAA_';
  var FONT = 'Metropolis, "Helvetica Neue", Arial, sans-serif';

  // Pressure-bin colors (Andrew's reference ramp), keyed by manifest bin key. The
  // sub-950 end is split into 930-950 / 910-930 / <910 so intense systems read
  // distinctly instead of saturating one "<950" color; the deep end continues the
  // ramp pink -> violet -> indigo (deeper = stronger), high-contrast on the navy
  // ocean without going neon.
  var PRESSURE_BIN_COLORS = {
    gt1000: '#dfe8ff', p990_1000: '#1f9bff', p970_990: '#ffd21a',
    p950_970: '#ff1f47', p930_950: '#ff3d9a', p910_930: '#cf4dff', lt910: '#8b5cff'
  };
  var BIN_ORDER = ['gt1000', 'p990_1000', 'p970_990', 'p950_970',
                   'p930_950', 'p910_930', 'lt910'];
  // SSHWS wind-category palette for the Vmax plume ONLY. The plume is a WIND
  // product, so a Saffir-Simpson ramp is a genuinely different, meaningful
  // encoding from the pressure-bin centers (and reads "warmer = stronger" at a
  // glance). Ramp matches the live active-banner: TD blue -> TS green -> C1
  // yellow -> C2 orange -> C3 red -> C4 magenta -> C5 purple. Confined to the
  // boxed inset (labeled "kt"), so it never competes with the field's bin colors.
  var SSHWS_RAMP = [
    [33, '#3fa4ff'], [63, '#46c56a'], [82, '#ffe14d'], [95, '#ff9a2f'],
    [112, '#ff4d3b'], [136, '#e33ad4']
  ];
  function sshwsColor(kt) {
    if (kt == null || isNaN(kt)) return '#3fa4ff';
    for (var i = 0; i < SSHWS_RAMP.length; i++) if (kt <= SSHWS_RAMP[i][0]) return SSHWS_RAMP[i][1];
    return '#b03bff';   // C5
  }
  // Expand an extent [w,e,s,n] symmetrically on its DEFICIENT axis until its geo
  // aspect (lonSpan/latSpan) equals `aspect`, so it fills the fixed-aspect map box
  // undistorted with NO crop + NO letterbox (we only ever ADD surrounding ocean,
  // never trim the region). Too-wide -> grow lat (clamped into [-90,90], shifting the
  // window if a pole is hit); too-narrow -> grow lon (unbounded; project() handles
  // ext[1]>180). Region stays centered. The region object (data filtering, peak
  // table) is unchanged - only the DISPLAY window grows.
  function frameExtent(ext, aspect) {
    var w = ext[0], e = ext[1], s = ext[2], n = ext[3];
    var lonSpan = e - w, latSpan = n - s, geoA = lonSpan / latSpan;
    if (geoA > aspect) {                          // too wide -> grow latitude
      var tLat = lonSpan / aspect, c = (s + n) / 2, half = tLat / 2;
      var lo = c - half, hi = c + half;
      if (lo < -90) { hi = Math.min(90, hi + (-90 - lo)); lo = -90; }
      if (hi > 90) { lo = Math.max(-90, lo - (hi - 90)); hi = 90; }
      s = lo; n = hi;
    } else if (geoA < aspect) {                   // too narrow -> grow longitude (unbounded)
      var tLon = latSpan * aspect, cl = (w + e) / 2, halfL = tLon / 2;
      w = cl - halfL; e = cl + halfL;
    }
    return [w, e, s, n];
  }
  // CANONICAL TAT BASEMAP spec (single source of truth - same hexes server-side).
  // Borders muted/secondary so they never overpower the centers. Draw order:
  // ocean -> land fill (static layer) -> centers -> coast -> country -> state
  // borders (overlaid ON TOP per frame, see _show).
  var BASEMAP_STYLE = {
    ocean: '#07101c', land: '#2f3f59',
    coast: 'rgba(150,175,205,0.28)', coastLw: 0.6,
    country: 'rgba(150,175,205,0.45)', countryLw: 0.7,    // admin_0 borders
    state: 'rgba(150,175,205,0.18)', stateLw: 0.4,        // admin_1 borders (subtle)
    grid: 'rgba(255,255,255,0.05)', gridLw: 0.5
  };
  // figure palette. accent = the shared bright blue (TATRegions.ACCENT, same as
  // the picker) so the whole ENS product reads as one blue identity; falls back
  // to the literal if the shared layer somehow isn't loaded yet.
  var ACCENT = (typeof window !== 'undefined' && window.TATRegions && window.TATRegions.ACCENT) || '#2b9cff';
  var C = { bg: '#0b1320', fg: '#e8ebef', muted: '#9199a4', accent: ACCENT,
            border: '#2a2e36', panel: '#12182280' };

  function el(id) { return document.getElementById(id); }
  function fmtInt(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ','); }
  function regionOr(key) { return (window.TATRegions && TATRegions.get(key)) ? key : DEFAULT_REGION; }
  function wrap180(lo) { return ((lo + 180) % 360 + 360) % 360 - 180; }   // tracks JSON lons are display-unwrapped
  var EARTH_R_KM = 6371.0088;
  function gcDeg(la1, lo1, la2, lo2) {            // haversine, degrees of arc (dateline-safe)
    var R = Math.PI / 180, p1 = la1 * R, p2 = la2 * R;
    var dp = (la2 - la1) * R, dl = (lo2 - lo1) * R;
    var a = Math.sin(dp / 2) * Math.sin(dp / 2) + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) * Math.sin(dl / 2);
    return 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a)) / R;
  }
  // local east/north offset (km) of a point from a reference, dateline-safe (the
  // lon delta is wrapped before scaling) - for the covariance z-score + bearing.
  function localXYkm(la, lo, rla, rlo) {
    var R = Math.PI / 180, dlon = lo - rlo; dlon -= 360 * Math.round(dlon / 360);
    return [dlon * R * Math.cos(rla * R) * EARTH_R_KM, (la - rla) * R * EARTH_R_KM];
  }
  function compass8(eastKm, northKm) {            // 8-point compass of an offset
    var ang = (Math.atan2(eastKm, northKm) * 180 / Math.PI + 360) % 360;
    return COMPASS8[Math.round(ang / 45) % 8];
  }

  function binKey(p) {
    if (p < 910) return 'lt910';
    if (p < 930) return 'p910_930';
    if (p < 950) return 'p930_950';
    if (p < 970) return 'p950_970';
    if (p < 990) return 'p970_990';
    if (p < 1000) return 'p990_1000';
    return 'gt1000';
  }

  // small rounded-rect path (used for the neutral CTL chip in the peak table)
  function roundRectPath(g, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    g.beginPath();
    g.moveTo(x + r, y);
    g.arcTo(x + w, y, x + w, y + h, r);
    g.arcTo(x + w, y + h, x, y + h, r);
    g.arcTo(x, y + h, x, y, r);
    g.arcTo(x, y, x + w, y, r);
    g.closePath();
  }

  var WK = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function validLabel(initMs, stepH) {
    var d = new Date(initMs + stepH * 3600000);
    return WK[d.getUTCDay()] + ' ' + MO[d.getUTCMonth()] + ' ' + d.getUTCDate() + ', ' +
      String(d.getUTCHours()).padStart(2, '0') + 'Z';
  }
  function shortInit(initMs) {
    var d = new Date(initMs);
    return MO[d.getUTCMonth()] + ' ' + d.getUTCDate() + ' ' + String(d.getUTCHours()).padStart(2, '0') + 'Z';
  }
  // "2026061306" -> "Jun 13 06Z" (Run-selector option label; same shape as shortInit)
  function cycleLabel(cyc) {
    cyc = String(cyc);
    if (cyc.length < 10) return cyc;
    var mo = parseInt(cyc.slice(4, 6), 10) - 1, day = parseInt(cyc.slice(6, 8), 10), hr = cyc.slice(8, 10);
    return (MO[mo] || '?') + ' ' + day + ' ' + hr + 'Z';
  }

  // ========================================================================
  function EnsCentersViewer(root) {
    this.root = root;
    // Self-scope the figure's DOM chrome (F-hour readout, trail-on indicator)
    // to the shared bright blue by overriding --accent on THIS viewer's root
    // only - the site-global amber --accent (satellite/HAFS) is untouched.
    if (root && root.style) root.style.setProperty('--accent', ACCENT);
    this.dom = {
      mapframe: el('enscenters-mapframe'),
      canvas: el('enscenters-canvas'),
      status: el('enscenters-status'),
      models: el('enscenters-models'),
      play: el('enscenters-play'),
      stepB: el('enscenters-step-back'),
      stepF: el('enscenters-step-fwd'),
      fhour: el('enscenters-fhour'),
      valid: el('enscenters-valid'),
      speed: el('enscenters-speed'),
      run: el('enscenters-run'),
      scrub: el('enscenters-scrub'),
      trail: el('enscenters-trail'),
      style: el('enscenters-style'),
      mean: el('enscenters-mean'),
      ppts: el('enscenters-ppts'),
      minp: el('enscenters-minp'),
      obs: el('enscenters-obs'),
      gif: el('enscenters-gif'),
      gifmodal: el('enscenters-gifmodal'),
      gifstart: el('enscenters-gifstart'),
      gifend: el('enscenters-gifend'),
      giffps: el('enscenters-giffps'),
      gifskip: el('enscenters-gifskip'),
      gifpreset: el('enscenters-gifpreset'),
      gifmake: el('enscenters-gifmake'),
      gifstatus: el('enscenters-gifstatus'),
      gifx: el('enscenters-gifx'),
      tooltip: el('enscenters-tooltip'),
      empty: el('enscenters-empty'),
      regionBtn: el('enscenters-region-btn'),
      regionLabel: el('enscenters-region-label'),
      caption: el('enscenters-caption')
    };
    this.ctx = this.dom.canvas.getContext('2d');
    this.staticLayer = document.createElement('canvas');
    this.trailLayer = document.createElement('canvas');

    this.manifest = null;
    this.model = null;
    this.data = null;
    this.steps = [];
    this.frames = [];          // per step: [[lat, lon, mslp, vmax], ...] (all)
    this.regionFrames = [];    // per step: region-filtered subset
    this.regionPrefix = [];    // prefix center counts by step
    this.peaks = [];           // per-member region peak rows (sorted)
    this.initMs = 0;
    this.idx = 0;
    this.visible = [];         // current step region-filtered (for hover)
    this.playing = false;
    this.speed = 1;
    this.followLatest = true;   // Run selector: true = track the newest cycle on poll
    this._runSig = null;        // cached signature of the Run <select> options
    this.raf = null;
    this.lastTick = 0;
    this.geo = { countries: null, coast: null };
    this.picker = null;
    this.trailUpTo = -1;

    var saved = null; try { saved = localStorage.getItem(LS_REGION); } catch (e) {}
    this.region = regionOr(saved || DEFAULT_REGION);
    this.extent = (window.TATRegions ? frameExtent(TATRegions.extentOf(TATRegions.get(this.region)), BOX_ASPECT) : [0, 360, -90, 90]);
    var tm = null; try { tm = localStorage.getItem(LS_TRAIL); } catch (e) {}
    this.trailMode = (tm === 'current') ? 'current' : 'trail';
    // Toolkit (Stage 2) state. Persisted like trail; tracks are loaded lazily.
    var ds = null; try { ds = localStorage.getItem(LS_STYLE); } catch (e) {}
    this.dataStyle = (ds === 'lines') ? 'lines' : 'cheerios';
    var mn = null; try { mn = localStorage.getItem(LS_MEAN); } catch (e) {}
    this.meanOn = (mn === 'on');
    var pp = null; try { pp = localStorage.getItem(LS_PPTS); } catch (e) {}
    this.pptsOn = (pp === 'on');   // dated mean-track MSLP labels; default OFF (busy)
    var mp = null; try { mp = localStorage.getItem(LS_MINP); } catch (e) {}
    this.minpOn = (mp === 'on');   // region-deepest-center highlight; default OFF
    this.minCenter = null;         // {id,mslp,vmax,lat,lon,step} deepest center in region
    var ob = null; try { ob = localStorage.getItem(LS_OBS); } catch (e) {}
    this.obsOn = (ob === 'on');
    this.obs = null;             // active observed systems (from global_storms.geojson)
    this.obsLoading = false;
    this._obsFailed = false;
    this.tracks = null;          // parsed tracks JSON for the loaded model+cycle
    this.tracksCycle = null;     // which cycle this.tracks belongs to
    this.tracksModel = null;     // which model this.tracks belongs to
    this.tracksRegion = null;    // region-cropped per-member tracks (for Lines)
    this.tracksLoading = false;

    this._wire();
    this._boot();
  }

  EnsCentersViewer.prototype._status = function (msg) {
    var s = this.dom.status; if (!s) return;
    if (msg) { s.style.display = 'flex'; s.querySelector('span').textContent = msg; }
    else { s.style.display = 'none'; }
  };

  EnsCentersViewer.prototype._showEmpty = function (on) {
    if (this.dom.empty) this.dom.empty.style.display = on ? 'block' : 'none';
    var sels = ['#enscenters-mapframe', '.ens-controlbar', '.ens-scrub', '.ens-caption'];
    for (var i = 0; i < sels.length; i++) {
      var e2 = this.root.querySelector(sels[i]);
      if (e2) e2.style.display = on ? 'none' : '';
    }
  };

  // ---- boot ----
  EnsCentersViewer.prototype._boot = function () {
    var self = this;
    this._status('Loading…');
    Promise.all([this._loadBasemap(), this._fetchManifest()])
      .then(function (res) { self._initRegion(); self._onManifest(res[1]); })
      .catch(function (e) { console.warn('enscenters: boot failed', e); self._status(''); self._showEmpty(true); });
    // re-render once webfonts settle so the burned-in text is crisp
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(function () { if (self.data) { self._drawFigure(); self._show(self.idx); } });
    }
  };

  EnsCentersViewer.prototype._loadBasemap = function () {
    var self = this;
    // basemap resolution is owned by the shared layer (TATRegions.COAST_RES =
    // 10m), one source of truth for every non-storm-nest viewer.
    var p = (window.TATRegions && TATRegions.loadGeo) ? TATRegions.loadGeo()
      : Promise.all([
          fetch('/ne_10m_admin_0_countries.geojson').then(function (r) { return r.json(); }),
          fetch('/ne_10m_coastline.geojson').then(function (r) { return r.json(); })
        ]).then(function (g) { return { countries: g[0], coast: g[1] }; });
    return p.then(function (g) { self.geo = g; });
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
    var defs = models.map(function (x) { return { slug: x.slug, label: x.label }; });
    // Open on the FRESHEST model, not the hard default. ECMWF ENS (ecens)
    // disseminates ~1h after the AI models, so for a ~1-2h window each 00/12Z
    // cycle the default_model is legitimately one cycle behind its peers -
    // landing the user on a stale view. Pick the model with the newest latest
    // cycle; among models tied on that cycle, honor the preferred order
    // (default_model first). _onManifest runs ONCE on load; the poll never
    // re-selects the model, so a user's later click is never overridden.
    var active = this._freshestModel(models);
    this._buildToggle(this.dom.models, defs, active, this._selectModel.bind(this));
    this._selectModel(active);
    this._schedulePoll();
  };

  // The slug to open on: the model whose ``latest`` cycle is newest (cycle ids
  // are fixed-width YYYYMMDDHH, so string compare == chronological). Ties go to
  // the preferred model (manifest default_model), else manifest order. Models
  // with no cycle can't lead. Falls back to default_model / models[0] when none
  // has a cycle yet.
  EnsCentersViewer.prototype._freshestModel = function (models) {
    var pref = (this.manifest && this.manifest.default_model) || null;
    var best = null;
    for (var i = 0; i < models.length; i++) {
      var x = models[i];
      if (!x.latest) continue;
      if (best === null) { best = x; }
      else if (x.latest > best.latest) { best = x; }
      else if (x.latest === best.latest &&
               x.slug === pref && best.slug !== pref) { best = x; }
    }
    if (best) return best.slug;
    if (pref && models.some(function (z) { return z.slug === pref; })) return pref;
    return models[0].slug;
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
    this.followLatest = true;            // a new model starts on its latest run
    this._highlight(this.dom.models, slug);
    this._buildRunSelect(entry, entry.latest);
    this._loadCycle(slug, entry.latest);
  };

  // Populate the "Run" <select> from THIS model's manifest cycle list (the
  // rolling window already on R2, newest-first), newest labelled "(latest)".
  // Shared / model-agnostic: reads whatever model's entry it is handed. Rebuilds
  // only when the cycle list actually changes (so a poll doesn't disrupt an open
  // dropdown), then reflects ``selected``.
  EnsCentersViewer.prototype._buildRunSelect = function (entry, selected) {
    var sel = this.dom.run;
    if (!sel || !entry) return;
    var cycles = (entry.cycles && entry.cycles.length) ? entry.cycles.slice()
      : (entry.latest ? [entry.latest] : []);
    var sig = cycles.join(',') + '|' + (entry.latest || '');
    if (sig !== this._runSig) {
      sel.innerHTML = '';
      for (var i = 0; i < cycles.length; i++) {
        var o = document.createElement('option');
        o.value = cycles[i];
        o.textContent = cycleLabel(cycles[i]) + (cycles[i] === entry.latest ? ' (latest)' : '');
        sel.appendChild(o);
      }
      this._runSig = sig;
    }
    if (selected && cycles.indexOf(selected) !== -1) sel.value = selected;
    else if (cycles.length) sel.value = cycles[0];
  };

  EnsCentersViewer.prototype._loadCycle = function (slug, cycle) {
    var self = this;
    this._pause();
    this.loadedCycle = cycle;
    // a new model/cycle invalidates any loaded tracks; reset so the toolkit
    // re-checks availability + lazily reloads for the new selection.
    this.tracks = null; this.tracksModel = null; this.tracksCycle = null;
    this.tracksRegion = null; this._tracksFailedKey = null;
    if (this.dom.run) this.dom.run.value = cycle;   // keep the Run selector in sync
    this._status('Loading ' + slug.toUpperCase() + ' ' + cycle + '…');
    // Cache-bust on the cycle's CONTENT version (not the stable cycle string), so
    // a backfill/overwrite of this cycle's JSON busts the browser + CDN cache; an
    // unchanged cycle keeps its token and stays cached. force-cache is safe now
    // that the URL is version-keyed. See TATRegions.cycleVersion.
    var ver = (window.TATRegions && TATRegions.cycleVersion)
      ? TATRegions.cycleVersion(this.manifest, slug, cycle) : cycle;
    fetch(DATA_BASE + slug + '/' + cycle + '.json?v=' + ver, { cache: 'force-cache' })
      .then(function (r) { if (!r.ok) throw new Error('cycle HTTP ' + r.status); return r.json(); })
      .then(function (d) { self._onData(d); })
      .catch(function (e) { console.warn('enscenters: cycle load failed', e); self._status('Could not load cycle.'); });
  };

  EnsCentersViewer.prototype._onData = function (d) {
    this.data = d;
    this._applyCaption(d);
    this.steps = d.run_steps || [];
    this.initMs = Date.parse(d.init_time);
    var byStep = {};
    for (var s = 0; s < this.steps.length; s++) byStep[this.steps[s]] = [];
    var members = d.members || [];
    for (var i = 0; i < members.length; i++) {
      var cs = members[i].centers || [];
      for (var k = 0; k < cs.length; k++) {
        var c = cs[k], arr = byStep[c[0]];
        if (arr) arr.push([c[1], c[2], c[3], c[4]]);
      }
    }
    this.frames = this.steps.map(function (st) { return byStep[st] || []; });
    this.idx = 0;
    this.dom.scrub.min = 0;
    this.dom.scrub.max = Math.max(0, this.steps.length - 1);
    this.dom.scrub.value = 0;
    this._recomputeRegion();
    this._layout();
    this._drawFigure();
    this._status('');
    this._show(0);
    // toolkit: reflect this model+cycle's tracks availability, then lazily load
    // the sibling tracks JSON + obs feed if a consuming feature is currently on.
    this._syncToolkitButtons();
    this._ensureTracks();
    this._ensureObs();
    // keep the GIF-maker hour dropdowns in step with the loaded run (a new
    // model/cycle can have a different hour set + max fhr); selection persists
    // if still valid, else clamps. Harmless when the modal is closed.
    this._populateGifHours();
  };

  // Model-aware caption: a model whose per-cycle JSON carries its own `caption`
  // (e.g. GEFS - genesis tracks, vmax is the model's own wind, different source)
  // overrides the default ECMWF text; everything else falls back to the
  // data-default attribute so the field models keep the closed-low explainer.
  EnsCentersViewer.prototype._applyCaption = function (d) {
    var elc = this.dom.caption;
    if (!elc) return;
    var dflt = elc.getAttribute('data-default') || elc.textContent;
    elc.textContent = (d && d.caption) ? d.caption : dflt;
  };

  // ---- region (shared TATRegions layer) ----
  EnsCentersViewer.prototype._initRegion = function () {
    var self = this;
    var r = window.TATRegions ? TATRegions.get(this.region) : null;
    if (this.dom.regionLabel && r) this.dom.regionLabel.textContent = r.label;
    if (window.TATRegions) {
      this.picker = new TATRegions.RegionPicker({
        current: this.region,   // picker loads its own 110m thumbnail geo
        onPick: function (key) { self._selectRegion(key); }
      });
    }
  };

  EnsCentersViewer.prototype._selectRegion = function (key) {
    if (!window.TATRegions) return;
    var r = TATRegions.get(key); if (!r) return;
    this.region = key;
    this.extent = frameExtent(TATRegions.extentOf(r), BOX_ASPECT);   // fixed-aspect display window
    try { localStorage.setItem(LS_REGION, key); } catch (e) {}
    if (this.dom.regionLabel) this.dom.regionLabel.textContent = r.label;
    if (this.picker) this.picker.setCurrent(key);
    if (this.data) { this._recomputeRegion(); this._layout(); this._drawFigure(); this._show(this.idx); }
  };

  // region-filter every step + per-member region peaks + prefix counts
  EnsCentersViewer.prototype._recomputeRegion = function () {
    var r = window.TATRegions ? TATRegions.get(this.region) : null;
    var self = this;
    this.regionFrames = this.frames.map(function (f) {
      if (!r) return f.slice();
      var out = [];
      for (var i = 0; i < f.length; i++) if (TATRegions.inRegion(f[i][1], f[i][0], r)) out.push(f[i]);
      return out;
    });
    this.regionPrefix = []; var run = 0;
    for (var s = 0; s < this.regionFrames.length; s++) { run += this.regionFrames[s].length; this.regionPrefix[s] = run; }
    // per-member region peaks. Capture the deepest center's lat/lon/step too (not
    // just mslp/vmax) so the Min-MSLP highlight can be placed; c = [step,lat,lon,mslp,vmax].
    var members = (this.data && this.data.members) || [];
    var rows = [];
    for (var m = 0; m < members.length; m++) {
      var cs = members[m].centers || [], best = null;
      for (var k = 0; k < cs.length; k++) {
        var c = cs[k];
        if (r && !TATRegions.inRegion(c[2], c[1], r)) continue;
        if (best === null || c[3] < best.mslp) best = { mslp: c[3], vmax: c[4], lat: c[1], lon: c[2], step: c[0] };
      }
      if (best) rows.push({ id: members[m].id, mslp: best.mslp, vmax: best.vmax, lat: best.lat, lon: best.lon, step: best.step });
    }
    rows.sort(function (a, b) { return a.mslp - b.mslp; });
    this.peaks = rows;
    this.minCenter = rows.length ? rows[0] : null;   // region-deepest center (peak-table top row)
    this._prepTracksRegion();   // re-crop the toolkit tracks to the new region (no-op if none)
    this._resetTrail();    // region changed -> trail invalid (clear pixels + counter)
  };

  // ===================== Stage 2 toolkit: tracks + clusters =====================
  // tracks_versions in the manifest is the cache-bust token AND the availability
  // signal; absent -> the model has no tracks file (toggles hide).
  EnsCentersViewer.prototype._tracksVersion = function (slug, cycle) {
    var e = this._modelEntry(slug);
    var tv = e && e.tracks_versions;
    return (tv && tv[cycle]) ? tv[cycle] : null;
  };
  EnsCentersViewer.prototype._hasTracks = function () {
    return !!this._tracksVersion(this.model, this.loadedCycle);
  };
  // available = the manifest says a tracks file exists AND it has not failed to
  // load for the current model+cycle (a fetch failure hides the toggles too).
  EnsCentersViewer.prototype._tracksAvailable = function () {
    return this._hasTracks() && this._tracksFailedKey !== (this.model + '|' + this.loadedCycle);
  };
  // ready = the loaded tracks JSON matches the current model+cycle on screen.
  EnsCentersViewer.prototype.tracksReady = function () {
    return !!(this.tracks && this.tracksModel === this.model && this.tracksCycle === this.loadedCycle);
  };

  // Load the sibling tracks JSON ONLY when a track-consuming feature is enabled,
  // version-keyed for cache-bust. Graceful: a 404 / parse error hides the toggles
  // and falls back to Cheerios, never throwing.
  EnsCentersViewer.prototype._ensureTracks = function () {
    // Load whenever a tracks file exists: the DEFAULT Cheerios view now draws subtle
    // per-member connector threads (needs the sibling tracks JSON), and Lines / Mean
    // / Obs consume it too. Graceful: no file -> no threads, plain Cheerios dots.
    if (!this._tracksAvailable()) return;
    if (this.tracksReady() || this.tracksLoading) return;     // already have / loading
    this._loadTracks(this.model, this.loadedCycle);
  };

  EnsCentersViewer.prototype._loadTracks = function (slug, cycle) {
    var self = this, ver = this._tracksVersion(slug, cycle) || cycle;
    this.tracksLoading = true;
    fetch(DATA_BASE + slug + '/' + cycle + '.tracks.json?v=' + ver, { cache: 'force-cache' })
      .then(function (r) { if (!r.ok) throw new Error('tracks HTTP ' + r.status); return r.json(); })
      .then(function (d) {
        self.tracksLoading = false;
        if (slug !== self.model || cycle !== self.loadedCycle) return;   // user moved on
        self.tracks = d; self.tracksModel = slug; self.tracksCycle = cycle;
        self._prepTracksRegion();
        self._syncToolkitButtons();
        if (self.regionFrames.length) self._show(self.idx);
      })
      .catch(function (e) {
        self.tracksLoading = false;
        console.warn('enscenters: tracks load failed (Cheerios fallback)', e);
        if (slug === self.model && cycle === self.loadedCycle) {
          self.tracks = null; self.tracksModel = null; self.tracksCycle = null;
          self._tracksFailedKey = slug + '|' + cycle;   // hide toggles for this cycle
          self._syncToolkitButtons();
          if (self.regionFrames.length) self._show(self.idx);
        }
      });
  };

  // region-crop the per-member tracks for Lines: keep a track if ANY fix lands in
  // the region (so a track entering/leaving the crop shows its full in-view path).
  EnsCentersViewer.prototype._prepTracksRegion = function () {
    this.tracksRegion = [];
    if (!this.tracks || !this.tracks.members) return;
    var r = window.TATRegions ? TATRegions.get(this.region) : null;
    var members = this.tracks.members;
    for (var i = 0; i < members.length; i++) {
      var trs = members[i].tracks || [];
      for (var t = 0; t < trs.length; t++) {
        var fixes = trs[t];
        if (!fixes || fixes.length < 2) continue;
        var inR = !r;
        for (var k = 0; k < fixes.length && !inR; k++) {
          if (TATRegions.inRegion(wrap180(fixes[k][2]), fixes[k][1], r)) inR = true;
        }
        if (inR) this.tracksRegion.push(fixes);
      }
    }
  };

  // Subtle CONNECTOR THREADS for the default CHEERIOS view: a thin, low-opacity,
  // NEUTRAL grey line linking each member's consecutive per-step centers, drawn
  // UNDER the pressure-bin dots so the dots stay the emphasis (dots + threads).
  // Dateline-safe (broken on a >half-map x jump). Needs the sibling tracks JSON
  // (per-member connectivity); a no-op when tracks aren't available.
  EnsCentersViewer.prototype._drawConnectors = function (g, idx) {
    if (!this.tracksRegion || !this.tracksRegion.length) return;
    var uptoStep = this.steps[Math.min(idx, this.steps.length - 1)];
    var ext = this.extent, mw = this.map.w, mh = this.map.h, JUMP = mw * 0.5;
    var conn = [], trs = this.tracksRegion;
    for (var i = 0; i < trs.length; i++) {
      var fixes = trs[i], prev = null;
      for (var k = 0; k < fixes.length; k++) {
        var f = fixes[k]; if (f[0] > uptoStep) break;          // step-sorted
        var p = TATRegions.project(wrap180(f[2]), f[1], ext, mw, mh);
        if (prev && Math.abs(p[0] - prev[0]) <= JUMP) conn.push(prev[0], prev[1], p[0], p[1]);
        prev = p;
      }
    }
    if (!conn.length) return;
    g.globalAlpha = 1; g.strokeStyle = CONNECTOR_COLOR; g.lineWidth = CONNECTOR_LW;
    g.lineJoin = 'round'; g.lineCap = 'round'; g.beginPath();
    for (var c = 0; c < conn.length; c += 4) { g.moveTo(conn[c], conn[c + 1]); g.lineTo(conn[c + 2], conn[c + 3]); }
    g.stroke();
  };

  // LINES mode: BOLD pressure-colored per-member spaghetti tracks up to the current
  // F-hour - each segment colored by its endpoint's pressure bin (batched per bin),
  // with a filled leading head at the current step. Dateline-safe (a >half-map x
  // jump breaks the stroke, never a wrapping streak). The distinct, heavier
  // counterpart to Cheerios' dots+threads.
  EnsCentersViewer.prototype._drawLines = function (g, idx) {
    if (!this.tracksRegion || !this.tracksRegion.length) return;
    var uptoStep = this.steps[Math.min(idx, this.steps.length - 1)];
    var ext = this.extent, mw = this.map.w, mh = this.map.h, JUMP = mw * 0.5;
    var buckets = {}, heads = {};
    for (var b = 0; b < BIN_ORDER.length; b++) { buckets[BIN_ORDER[b]] = []; heads[BIN_ORDER[b]] = []; }
    var trs = this.tracksRegion;
    for (var i = 0; i < trs.length; i++) {
      var fixes = trs[i], prev = null, head = null;
      for (var k = 0; k < fixes.length; k++) {
        var f = fixes[k]; if (f[0] > uptoStep) break;          // step-sorted
        var p = TATRegions.project(wrap180(f[2]), f[1], ext, mw, mh);
        var bk = (f[3] != null) ? binKey(f[3]) : BIN_ORDER[0];
        if (prev && Math.abs(p[0] - prev[0]) <= JUMP) buckets[bk].push(prev[0], prev[1], p[0], p[1]);
        prev = p; head = [p[0], p[1], bk];                     // current F-hour position (leads the line)
      }
      if (head) heads[head[2]].push(head[0], head[1]);
    }
    // bold pressure-colored segments, batched per bin
    g.globalAlpha = LINE_ALPHA; g.lineWidth = LINE_LW; g.lineJoin = 'round'; g.lineCap = 'round';
    for (var bo = 0; bo < BIN_ORDER.length; bo++) {
      var key = BIN_ORDER[bo], seg = buckets[key]; if (!seg.length) continue;
      g.strokeStyle = PRESSURE_BIN_COLORS[key] || '#fff'; g.beginPath();
      for (var s = 0; s < seg.length; s += 4) { g.moveTo(seg[s], seg[s + 1]); g.lineTo(seg[s + 2], seg[s + 3]); }
      g.stroke();
    }
    // filled leading heads at the current F-hour
    g.globalAlpha = 1; var hr = this.fillR;
    for (var ho = 0; ho < BIN_ORDER.length; ho++) {
      var hk = BIN_ORDER[ho], hd = heads[hk]; if (!hd.length) continue;
      g.fillStyle = PRESSURE_BIN_COLORS[hk] || '#fff'; g.beginPath();
      for (var hh = 0; hh < hd.length; hh += 2) { g.moveTo(hd[hh] + hr, hd[hh + 1]); g.arc(hd[hh], hd[hh + 1], hr, 0, 6.2832); }
      g.fill();
    }
    g.globalAlpha = 1;
  };

  // Mean: each confident cluster's ensemble-mean track as a BOLD line (dark casing
  // for legibility) colored by the cluster's p50 MSLP at each lead. low_confidence
  // / tiny clusters are de-emphasized (thin, faint) or hidden, so the eye goes to
  // the strong consensus systems. Drawn up to the current F-hour, dateline-safe.
  EnsCentersViewer.prototype._drawMean = function (g, idx) {
    if (!this.tracks || !this.tracks.clusters) return;
    var uptoStep = this.steps[Math.min(idx, this.steps.length - 1)];
    var ext = this.extent, mw = this.map.w, mh = this.map.h, JUMP = mw * 0.5;
    var r = window.TATRegions ? TATRegions.get(this.region) : null;
    // dated MSLP labels go on the HEADLINE system only (the same dominant cluster
    // the plume describes), so secondary tracks don't crowd the field with text.
    var dom = this._dominantCluster();
    var items = [];
    for (var i = 0; i < this.tracks.clusters.length; i++) {
      var c = this.tracks.clusters[i];
      if ((c.member_count || 0) < MEAN_MIN_MEMBERS) continue;     // hide tiny clusters
      // Only CONFIDENT, populous clusters get a mean line - the low-confidence ones
      // were the jagged faint tangle. The single exception is the region's dominant
      // system: drawn faint so a lone marginal system still shows ONE clean line
      // (and its plume) rather than an empty field.
      if (c.low_confidence && c !== dom) continue;
      var mt = c.mean_track || [], vis = !r;
      for (var k = 0; k < mt.length && !vis; k++) if (TATRegions.inRegion(wrap180(mt[k][2]), mt[k][1], r)) vis = true;
      if (vis) items.push(c);
    }
    // draw de-emphasized (low_confidence) first so bold consensus sits on top
    items.sort(function (a, b) { return (a.low_confidence ? 0 : 1) - (b.low_confidence ? 0 : 1); });
    for (var it = 0; it < items.length; it++) {
      this._drawMeanTrack(g, items[it], uptoStep, ext, mw, mh, JUMP,
        !!items[it].low_confidence, items[it] === dom);
    }
  };

  // The headline cluster for the CURRENTLY SELECTED REGION: among clusters of
  // reliable size whose mean track enters the region (same crop the peak table +
  // _drawMean use), pick the dominant one - confident beats low-confidence, then
  // most-populous. Region-aware so the plume + dated labels describe the in-region
  // system (and its "N members" count), not a globally-first system elsewhere; on a
  // region with no system it returns null and the plume/labels hide cleanly.
  // Recomputed at draw time, so a region switch redraws against the new region.
  EnsCentersViewer.prototype._dominantCluster = function () {
    var cl = (this.tracks && this.tracks.clusters) || [];
    var r = window.TATRegions ? TATRegions.get(this.region) : null;
    function inRegion(c) {
      if (!r) return true;
      var mt = c.mean_track || [];
      for (var k = 0; k < mt.length; k++) if (TATRegions.inRegion(wrap180(mt[k][2]), mt[k][1], r)) return true;
      return false;
    }
    var best = null;
    for (var i = 0; i < cl.length; i++) {
      var c = cl[i];
      if ((c.member_count || 0) < MEAN_MIN_MEMBERS || !inRegion(c)) continue;
      if (!best) { best = c; continue; }
      var cConf = !c.low_confidence, bConf = !best.low_confidence;
      if (cConf !== bConf) { if (cConf) best = c; continue; }          // confident wins
      if ((c.member_count || 0) > (best.member_count || 0)) best = c;   // then most members
    }
    return best;
  };

  EnsCentersViewer.prototype._drawMeanTrack = function (g, c, uptoStep, ext, mw, mh, JUMP, dim, labelDaily) {
    var mt = c.mean_track || []; if (mt.length < 2) return;
    var pm = (c.plume && c.plume.mslp) || { lead: [], p50: [] };
    var lut = {}; for (var i = 0; i < pm.lead.length; i++) lut[pm.lead[i]] = pm.p50[i];
    var lw = dim ? MEAN_DIM_LW : MEAN_LW;
    var pts = [];
    for (var k = 0; k < mt.length; k++) {
      if (mt[k][0] > uptoStep) break;
      var p = TATRegions.project(wrap180(mt[k][2]), mt[k][1], ext, mw, mh);
      pts.push([p[0], p[1], mt[k][0]]);
    }
    if (pts.length < 2) return;
    g.lineJoin = 'round'; g.lineCap = 'round';
    if (!dim) {                                                   // dark casing under the bold line
      g.strokeStyle = MEAN_CASING; g.lineWidth = lw + 2.5; g.globalAlpha = 0.9; g.beginPath();
      for (var s = 1; s < pts.length; s++) {
        if (Math.abs(pts[s][0] - pts[s - 1][0]) > JUMP) continue;
        g.moveTo(pts[s - 1][0], pts[s - 1][1]); g.lineTo(pts[s][0], pts[s][1]);
      }
      g.stroke();
    }
    g.globalAlpha = dim ? MEAN_DIM_ALPHA : 0.95; g.lineWidth = lw;
    for (var s2 = 1; s2 < pts.length; s2++) {
      if (Math.abs(pts[s2][0] - pts[s2 - 1][0]) > JUMP) continue;
      var mslp = lut[pts[s2][2]];
      g.strokeStyle = PRESSURE_BIN_COLORS[(mslp != null) ? binKey(mslp) : BIN_ORDER[0]] || '#fff';
      g.beginPath(); g.moveTo(pts[s2 - 1][0], pts[s2 - 1][1]); g.lineTo(pts[s2][0], pts[s2][1]); g.stroke();
    }
    if (!dim) {
      // dated median-MSLP labels at daily leads (the classic ensemble-mean look) -
      // behind the "Pressure points" toggle (default off), headline cluster only.
      if (labelDaily && this.pptsOn) this._drawMeanDailyLabels(g, pts, lut);
      var last = pts[pts.length - 1], hmslp = lut[last[2]];
      var hcol = PRESSURE_BIN_COLORS[(hmslp != null) ? binKey(hmslp) : BIN_ORDER[0]] || '#fff';
      g.globalAlpha = 1;
      g.fillStyle = MEAN_CASING; g.beginPath(); g.arc(last[0], last[1], 5.4, 0, 6.2832); g.fill();   // dark casing
      g.fillStyle = hcol; g.beginPath(); g.arc(last[0], last[1], 4.0, 0, 6.2832); g.fill();           // bin-colored head
    }
    g.globalAlpha = 1;
  };

  // Dated median-MSLP labels stepping along the bold ensemble-mean track at DAILY
  // leads (step % 24 == 0), keyed to the actual mean-track points (clusters often
  // start mid-run, so we never assume a 0/24/48 origin). MSLP is the cluster's p50
  // at that lead (lut, joined from plume.mslp). Legibility over a busy field: each
  // label is OFFSET consistently up-and-right of its track point (flipped left near
  // the map's right edge) with a tiny leader, sits on a small dark halo pill, and
  // COLLISION-AVOIDED - a candidate whose box would overlap an already-placed label
  // (or its own track point clutter) is skipped, so a slow-mover never piles labels.
  EnsCentersViewer.prototype._drawMeanDailyLabels = function (g, pts, lut) {
    // gather daily candidates first
    var cand = [];
    for (var i = 0; i < pts.length; i++) {
      var step = pts[i][2];
      if (step <= 0 || step % 24 !== 0) continue;
      var mslp = lut[step]; if (mslp == null) continue;
      var d = new Date(this.initMs + step * 3600000);
      cand.push({ x: pts[i][0], y: pts[i][1],
        txt: MO[d.getUTCMonth()] + ' ' + d.getUTCDate() + '  ' + Math.round(mslp) + ' hPa' });
    }
    if (!cand.length) return;
    g.save();
    g.font = '600 9px ' + FONT; g.lineJoin = 'round';
    var OFFX = 9, OFFY = -13, PADX = 4, BH = 13, placed = [];
    function overlaps(r) {
      for (var j = 0; j < placed.length; j++) {
        var q = placed[j];
        if (r.x < q.x + q.w + 3 && r.x + r.w + 3 > q.x && r.y < q.y + q.h + 2 && r.y + r.h + 2 > q.y) return true;
      }
      return false;
    }
    for (var c = 0; c < cand.length; c++) {
      var x = cand[c].x, y = cand[c].y, txt = cand[c].txt;
      var tw = g.measureText(txt).width, bw = tw + PADX * 2;
      // consistent up-right offset; flip to up-left near the right edge
      var left = (x + OFFX + bw <= this.map.w - 2);
      var bx = left ? (x + OFFX) : (x - OFFX - bw);
      var by = y + OFFY - BH / 2;
      if (by < 2) by = 2;
      var rect = { x: bx, y: by, w: bw, h: BH };
      if (overlaps(rect)) continue;                          // collision avoidance: skip
      placed.push(rect);
      // tiny leader from the track point to the label pill
      g.globalAlpha = 1; g.strokeStyle = 'rgba(160,180,208,0.55)'; g.lineWidth = 0.8;
      g.beginPath(); g.moveTo(x, y); g.lineTo(left ? bx : bx + bw, by + BH / 2); g.stroke();
      // tick dot at the track point
      g.fillStyle = MEAN_CASING; g.beginPath(); g.arc(x, y, 2.8, 0, 6.2832); g.fill();
      g.fillStyle = '#fff'; g.beginPath(); g.arc(x, y, 1.5, 0, 6.2832); g.fill();
      // dark halo pill + text
      roundRectPath(g, bx, by, bw, BH, 3); g.fillStyle = 'rgba(7,16,28,0.78)'; g.fill();
      g.strokeStyle = 'rgba(120,140,170,0.35)'; g.lineWidth = 0.8; g.stroke();
      g.textBaseline = 'middle'; g.textAlign = 'left'; g.fillStyle = '#eef3fb';
      g.fillText(txt, bx + PADX, by + BH / 2 + 0.5);
    }
    g.restore();
  };

  // Vmax plume inset, TOP-RIGHT (opposite the legend). Shown only in Mean mode;
  // describes the headline cluster. Calm + data-forward: ONE low-opacity p10-p90
  // spread band, a clear MEDIAN line as the emphasis (SSHWS-colored - it is a wind
  // plume), and thin NEUTRAL Max + Min bound lines, each labeled. Curves are lightly
  // smoothed to kill per-step jaggedness. A vertical marker tracks the current
  // F-hour; tidy kt axis; "Vmax plume - N members" caption.
  EnsCentersViewer.prototype._drawPlumeInset = function (g) {
    if (!this.tracks || !this.tracks.clusters) return;
    var dom = this._dominantCluster();
    if (!dom) { this._drawPlumeNote(g); return; }   // no in-region system: clean note, no plume
    var pv = (dom.plume && dom.plume.vmax) || null;
    if (!pv || !pv.lead || pv.lead.length < 2) { this._drawPlumeNote(g); return; }
    // light [1,2,1]/4 smoothing for display only (kills per-step jaggedness)
    function smooth(arr) {
      var o = []; for (var i = 0; i < arr.length; i++) {
        var a = arr[Math.max(0, i - 1)], b = arr[i], c = arr[Math.min(arr.length - 1, i + 1)];
        o.push((a + 2 * b + c) / 4);
      } return o;
    }
    var leads = pv.lead;
    var p10 = smooth(pv.p10), p90 = smooth(pv.p90),
        sMax = smooth(pv.max), sMed = smooth(pv.p50), sMin = smooth(pv.min);
    // FIXED top-right (opposite the top-left legend); same spot on every region
    var w = PLUME_W, h = PLUME_H, x = this.map.x + this.map.w - w - 8, y = this.map.y + 8;
    g.save();
    g.fillStyle = 'rgba(7,16,28,0.82)'; g.strokeStyle = C.border; g.lineWidth = 1;
    roundRectPath(g, x, y, w, h, 5); g.fill(); g.stroke();
    g.fillStyle = C.fg; g.font = '700 10px ' + FONT; g.textBaseline = 'top'; g.textAlign = 'left';
    g.fillText('Vmax plume  ·  this system', x + 8, y + 5);
    // The plume describes the HEADLINE system, whose member support is a SUBSET of the
    // full ensemble. Spell it out as "N of M members" so it never reads as a broken
    // mismatch against the header's total ensemble count (51).
    var nTot = (this.tracks && this.tracks.n_members) || 0;
    g.fillStyle = C.muted; g.font = '600 9px ' + FONT;
    g.fillText(dom.member_count + ' of ' + nTot + ' members', x + 8, y + 16);
    // plot area; right gutter reserved for the Max/Median/Min labels
    var cx = x + 8, cy = y + 28, gutter = 36, cw = w - 16 - gutter, ch = h - 38;
    var lmin = leads[0], lmax = leads[leads.length - 1], lspan = Math.max(1, lmax - lmin);
    var vlo = Infinity, vhi = -Infinity;
    for (var k = 0; k < leads.length; k++) { if (sMin[k] < vlo) vlo = sMin[k]; if (sMax[k] > vhi) vhi = sMax[k]; }
    if (!(vhi > vlo)) vhi = vlo + 1;
    var pad = (vhi - vlo) * 0.08;
    vlo = Math.max(0, vlo - pad); vhi += pad;   // wind can't be negative -> floor the axis at 0
    function px(l) { return cx + ((l - lmin) / lspan) * cw; }
    function py(v) { return cy + ch - ((v - vlo) / (vhi - vlo)) * ch; }
    function poly(vals) {
      g.beginPath();
      for (var s = 0; s < leads.length; s++) { var X = px(leads[s]), Y = py(vals[s]); s ? g.lineTo(X, Y) : g.moveTo(X, Y); }
      g.stroke();
    }
    // 1) single low-opacity p10-p90 spread band
    g.beginPath();
    for (var a = 0; a < leads.length; a++) { var X = px(leads[a]), Y = py(p90[a]); a ? g.lineTo(X, Y) : g.moveTo(X, Y); }
    for (var bb = leads.length - 1; bb >= 0; bb--) g.lineTo(px(leads[bb]), py(p10[bb]));
    g.closePath(); g.fillStyle = 'rgba(43,156,255,0.12)'; g.fill();
    // 2) current-F-hour marker
    var curStep = this.steps[Math.min(this.idx, this.steps.length - 1)];
    var mx = px(Math.max(lmin, Math.min(curStep, lmax)));
    g.strokeStyle = 'rgba(255,255,255,0.4)'; g.lineWidth = 1; g.beginPath(); g.moveTo(mx, cy); g.lineTo(mx, cy + ch); g.stroke();
    // 3) thin NEUTRAL Max + Min bound lines, then the MEDIAN emphasis line on top
    g.lineJoin = 'round'; g.lineCap = 'round'; g.globalAlpha = 1;
    g.strokeStyle = 'rgba(196,208,224,0.5)'; g.lineWidth = 1.0; poly(sMax); poly(sMin);
    for (var s2 = 1; s2 < leads.length; s2++) {   // median: SSHWS-colored per (smoothed) segment
      g.strokeStyle = sshwsColor(sMed[s2]); g.lineWidth = 2.4;
      g.beginPath(); g.moveTo(px(leads[s2 - 1]), py(sMed[s2 - 1])); g.lineTo(px(leads[s2]), py(sMed[s2])); g.stroke();
    }
    // labels in the right gutter (max>=median>=min => y-ordered; de-collide downward)
    function clampY(v) { return Math.max(cy + 6, Math.min(cy + ch - 2, py(v))); }
    var lyHi = clampY(sMax[sMax.length - 1]);
    var lyMd = Math.max(lyHi + 11, clampY(sMed[sMed.length - 1]));
    var lyLo = Math.max(lyMd + 11, clampY(sMin[sMin.length - 1]));
    var gx = x + w - 6, NEU = 'rgba(212,222,236,0.9)';
    g.font = '700 9px ' + FONT; g.textBaseline = 'middle'; g.textAlign = 'right'; g.lineJoin = 'round';
    function gutterLabel(text, ly, col) {
      g.lineWidth = 2.6; g.strokeStyle = 'rgba(7,16,28,0.92)'; g.strokeText(text, gx, ly);
      g.fillStyle = col; g.fillText(text, gx, ly);
    }
    gutterLabel('Max', lyHi, NEU);
    gutterLabel('Median', lyMd, sshwsColor(sMed[sMed.length - 1]));
    gutterLabel('Min', lyLo, NEU);
    // y-axis kt range (subtle)
    g.fillStyle = C.muted; g.font = '600 8px ' + FONT; g.textBaseline = 'alphabetic'; g.textAlign = 'left';
    g.fillText(Math.round(vhi) + ' kt', cx + 1, cy + 7);
    g.fillText(Math.round(vlo) + ' kt', cx + 1, cy + ch - 1);
    g.restore();
  };

  // Compact chip when Mean is on but no cluster falls in the region, so the plume +
  // dated labels hide cleanly with a one-line reason (mirrors the obs "no system"
  // note). Sits where the plume would be: FIXED top-right.
  EnsCentersViewer.prototype._drawPlumeNote = function (g) {
    var msg = 'No system in this region';
    g.save(); g.font = '600 11px ' + FONT; g.textBaseline = 'top'; g.textAlign = 'left';
    var pad = 8, tw = g.measureText(msg).width, w = tw + pad * 2, h = 26;
    var x = this.map.x + this.map.w - w - 8, y = this.map.y + 8;
    g.fillStyle = 'rgba(7,16,28,0.82)'; g.strokeStyle = C.border; g.lineWidth = 1;
    roundRectPath(g, x, y, w, h, 5); g.fill(); g.stroke();
    g.fillStyle = C.muted; g.fillText(msg, x + pad, y + 8);
    g.restore();
  };

  // Min-MSLP highlight: a bold, sober WHITE reticle (crosshair + double ring, dark
  // casing) at the DEEPEST member-center in the selected region (this.minCenter, the
  // peak table's top row) with its value label. Distinct from the pressure-bin field
  // + the lime obs marker; region-aware (recomputed in _recomputeRegion); dateline-
  // safe via project's per-point unwrap; drawn in the translated map space.
  EnsCentersViewer.prototype._drawMinMslp = function (g) {
    var mc = this.minCenter; if (!mc || mc.mslp == null) return;
    var p = TATRegions.project(mc.lon, mc.lat, this.extent, this.map.w, this.map.h);
    var x = p[0], y = p[1];
    if (x < -20 || x > this.map.w + 20 || y < -20 || y > this.map.h + 20) return;   // off-map guard
    g.save(); g.lineCap = 'round'; g.lineJoin = 'round';
    function reticle(stroke, lwRing, lwTick) {
      g.strokeStyle = stroke; g.lineWidth = lwRing;
      g.beginPath(); g.arc(x, y, 9.5, 0, 6.2832); g.stroke();
      g.beginPath(); g.arc(x, y, 4.3, 0, 6.2832); g.stroke();
      g.lineWidth = lwTick; g.beginPath();
      g.moveTo(x - 15, y); g.lineTo(x - 11, y); g.moveTo(x + 11, y); g.lineTo(x + 15, y);
      g.moveTo(x, y - 15); g.lineTo(x, y - 11); g.moveTo(x, y + 11); g.lineTo(x, y + 15); g.stroke();
    }
    reticle('rgba(7,16,28,0.92)', 4.5, 4);     // dark casing
    reticle('#ffffff', 2, 1.6);                // bright reticle on top
    // value label on a dark halo pill, offset to one side (flip near the right edge)
    var txt = Math.round(mc.mslp) + ' hPa';
    g.font = '700 11px ' + FONT; g.textBaseline = 'middle';
    var tw = g.measureText(txt).width, padx = 5, bw = tw + padx * 2, bh = 17;
    var left = (x + 16 + bw <= this.map.w - 2);
    var bx = left ? (x + 16) : (x - 16 - bw), by = y - bh / 2;
    if (by < 2) by = 2; if (by + bh > this.map.h) by = this.map.h - bh;
    roundRectPath(g, bx, by, bw, bh, 4);
    g.fillStyle = 'rgba(7,16,28,0.82)'; g.fill();
    g.strokeStyle = 'rgba(255,255,255,0.55)'; g.lineWidth = 1; g.stroke();
    g.textAlign = 'left'; g.fillStyle = '#ffffff'; g.fillText(txt, bx + padx, by + bh / 2 + 0.5);
    g.restore();
  };

  EnsCentersViewer.prototype._syncToolkitButtons = function () {
    var avail = this._tracksAvailable();
    if (this.dom.style) {
      this.dom.style.style.display = avail ? '' : 'none';
      var lines = (this.dataStyle === 'lines');
      this.dom.style.textContent = lines ? 'Style: Lines' : 'Style: Cheerios';
      this.dom.style.classList.toggle('on', lines);
    }
    if (this.dom.mean) {
      this.dom.mean.style.display = avail ? '' : 'none';
      this.dom.mean.textContent = this.meanOn ? 'Mean: on' : 'Mean: off';
      this.dom.mean.classList.toggle('on', this.meanOn);
    }
    if (this.dom.ppts) {                           // dated MSLP labels - only meaningful with the mean track shown
      this.dom.ppts.style.display = (avail && this.meanOn) ? '' : 'none';
      this.dom.ppts.textContent = this.pptsOn ? 'Pressure points: on' : 'Pressure points: off';
      this.dom.ppts.classList.toggle('on', this.pptsOn);
    }
    if (this.dom.obs) {                            // obs needs clusters to compare against
      this.dom.obs.style.display = avail ? '' : 'none';
      this.dom.obs.textContent = this.obsOn ? 'Obs: on' : 'Obs: off';
      this.dom.obs.classList.toggle('on', this.obsOn);
    }
    if (this.dom.minp) {                           // deepest-center highlight: uses CENTERS (always present), not tracks
      this.dom.minp.textContent = this.minpOn ? 'Min MSLP: on' : 'Min MSLP: off';
      this.dom.minp.classList.toggle('on', this.minpOn);
    }
  };

  EnsCentersViewer.prototype._setDataStyle = function (mode) {
    this.dataStyle = (mode === 'lines') ? 'lines' : 'cheerios';
    try { localStorage.setItem(LS_STYLE, this.dataStyle); } catch (e) {}
    this._syncToolkitButtons();
    this._ensureTracks();
    if (this.regionFrames.length) this._show(this.idx);
  };

  EnsCentersViewer.prototype._setMean = function (on) {
    this.meanOn = !!on;
    try { localStorage.setItem(LS_MEAN, this.meanOn ? 'on' : 'off'); } catch (e) {}
    this._syncToolkitButtons();   // also reveals/hides the dependent Pressure-points toggle
    this._ensureTracks();
    if (this.regionFrames.length) this._show(this.idx);
  };

  EnsCentersViewer.prototype._setPpts = function (on) {
    this.pptsOn = !!on;
    try { localStorage.setItem(LS_PPTS, this.pptsOn ? 'on' : 'off'); } catch (e) {}
    this._syncToolkitButtons();
    if (this.regionFrames.length) this._show(this.idx);
  };

  EnsCentersViewer.prototype._setMinp = function (on) {
    this.minpOn = !!on;
    try { localStorage.setItem(LS_MINP, this.minpOn ? 'on' : 'off'); } catch (e) {}
    this._syncToolkitButtons();
    if (this.regionFrames.length) this._show(this.idx);
  };

  // ===================== Stage 2b: OBS vs ENVELOPE =====================
  // Lazily load the SAME global observed-system feed the home tracks map uses
  // (READ-ONLY; never the floater poller), only while the toggle is on.
  EnsCentersViewer.prototype._ensureObs = function () {
    if (!this.obsOn) return;
    if (!this._tracksAvailable()) return;        // need clusters to compare against
    if (this.obs || this.obsLoading) return;
    this._loadObs();
  };

  EnsCentersViewer.prototype._loadObs = function () {
    var self = this; this.obsLoading = true;
    fetch(OBS_FEED_URL, { cache: 'no-cache' })
      .then(function (r) { if (!r.ok) throw new Error('obs HTTP ' + r.status); return r.json(); })
      .then(function (gj) {
        self.obsLoading = false; self._obsFailed = false;
        self.obs = self._extractActiveObs(gj);
        if (self.regionFrames.length) self._show(self.idx);
      })
      .catch(function (e) {
        self.obsLoading = false; self._obsFailed = true; self.obs = [];   // clean no-op
        console.warn('enscenters: obs feed load failed (overlay no-ops)', e);
        if (self.regionFrames.length) self._show(self.idx);
      });
  };

  // Parse the global_storms.geojson into CURRENT positions of ACTIVE systems:
  // active invests (the active_marker points) + named storms whose track is_active
  // (their latest observation fix). Everything else (historical fixes, inactive
  // tracks) is ignored - this is purely a read of observed reality.
  EnsCentersViewer.prototype._extractActiveObs = function (gj) {
    var feats = (gj && gj.features) || [], out = [], latest = {}, seen = {};
    for (var i = 0; i < feats.length; i++) {
      var p = feats[i].properties || {}, g = feats[i].geometry || {};
      if (p.kind === 'observation' && g.type === 'Point') {
        var t = Date.parse(p.time_iso || '') || 0, id = p.storm_id;
        if (!latest[id] || t > latest[id].t) latest[id] = { t: t, lon: g.coordinates[0], lat: g.coordinates[1], kt: p.intensity_kt, mslp: p.mslp_mb };
      }
    }
    for (var j = 0; j < feats.length; j++) {
      var p2 = feats[j].properties || {}, g2 = feats[j].geometry || {};
      if (p2.kind === 'active_marker' && g2.type === 'Point') {
        // The ATCF NUMBER drives the glyph (house marker rule 2026-07-14):
        // 90-99 -> one red X; a parseable 01-89 designation is a DESIGNATED
        // system (glyph) even when a stale feed stamped it invest_x
        // (pre-0.8.5 writers marked PTCs as invests); no parseable number
        // falls back to marker_type. Record the id so the is_active track
        // loop below doesn't re-add the SAME named storm a second time
        // (Arthur was drawing both a red X and a green glyph).
        var investKind = (function () {
          var m = /^(\d{1,2})[A-Z]$/.exec(String(p2.designation || '').toUpperCase());
          if (m) return parseInt(m[1], 10) >= 90;
          var s = /^[A-Z]+_[A-Z]{2}(\d{2})\d{4}$/.exec(String(p2.storm_id || '').toUpperCase());
          if (s) return parseInt(s[1], 10) >= 90;
          return p2.marker_type === 'invest_x';
        })();
        out.push({ id: p2.storm_id, name: p2.name || p2.designation || p2.storm_id,
          lat: g2.coordinates[1], lon: g2.coordinates[0], kt: p2.current_intensity_kt, mslp: p2.current_mslp_mb,
          timeMs: Date.parse(p2.last_fix || '') || 0,
          kind: investKind ? 'invest' : 'storm' });
        seen[p2.storm_id] = true;
      }
    }
    for (var k = 0; k < feats.length; k++) {
      var p3 = feats[k].properties || {};
      if (p3.kind === 'track' && p3.is_active === true && !seen[p3.storm_id]) {
        var lf = latest[p3.storm_id];
        if (lf) out.push({ id: p3.storm_id, name: p3.name || p3.designation || p3.storm_id,
          lat: lf.lat, lon: lf.lon, kt: lf.kt, mslp: lf.mslp, timeMs: lf.t, kind: 'storm' });
      }
    }
    return out;
  };

  // active systems in the current region, each matched (or not) to a cluster
  EnsCentersViewer.prototype._resolveObs = function () {
    var out = [];
    if (!this.obs || !this.tracks) return out;
    var r = window.TATRegions ? TATRegions.get(this.region) : null;
    for (var i = 0; i < this.obs.length; i++) {
      var o = this.obs[i];
      if (r && !TATRegions.inRegion(wrap180(o.lon), o.lat, r)) continue;   // not in view
      var leadH = (o.timeMs && this.initMs) ? Math.max(0, (o.timeMs - this.initMs) / 3600000) : 0;
      out.push({ obs: o, match: this._matchCluster(o, leadH) });
    }
    return out;
  };

  // nearest cluster by mean position at the obs's valid lead (nearest-valid-time
  // bucket); null if none within a sane great-circle distance.
  EnsCentersViewer.prototype._matchCluster = function (o, leadH) {
    var cl = (this.tracks && this.tracks.clusters) || [], best = null;
    for (var i = 0; i < cl.length; i++) {
      var c = cl[i]; if ((c.member_count || 0) < MEAN_MIN_MEMBERS) continue;
      var mt = c.mean_track || [], bp = null, bstep = null, bd = 1e9;
      for (var k = 0; k < mt.length; k++) {
        var dl = Math.abs(mt[k][0] - leadH);
        if (dl < bd) { bd = dl; bp = mt[k]; bstep = mt[k][0]; }
      }
      if (!bp) continue;
      var dist = gcDeg(o.lat, wrap180(o.lon), bp[1], wrap180(bp[2]));
      if (best === null || dist < best.dist) best = { cluster: c, step: bstep, dist: dist };
    }
    return (best && best.dist <= OBS_MATCH_MAX_DEG) ? best : null;
  };

  // obs_support, client-side from the emitted envelope covariance: percentile rank
  // (bivariate-normal coverage at the obs's Mahalanobis radius) + which side of the
  // mean it sits + offset distance.
  EnsCentersViewer.prototype._obsRank = function (cluster, step, lat, lon) {
    var env = cluster.envelope || [], e = null, bd = 1e9;
    for (var i = 0; i < env.length; i++) { var d = Math.abs(env[i].step - step); if (d < bd) { bd = d; e = env[i]; } }
    if (!e || e.mean_lat == null) return null;
    var v = localXYkm(lat, wrap180(lon), e.mean_lat, e.mean_lon);
    var out = { side: compass8(v[0], v[1]), offsetKm: Math.sqrt(v[0] * v[0] + v[1] * v[1]), step: e.step, pct: null };
    if (e.cov_km) {
      var cxx = e.cov_km[0][0], cxy = e.cov_km[0][1], cyy = e.cov_km[1][1], det = cxx * cyy - cxy * cxy;
      if (det > 1e-6) {
        var ix = cyy / det, ixy = -cxy / det, iy = cxx / det;
        var m2 = v[0] * (ix * v[0] + ixy * v[1]) + v[1] * (ixy * v[0] + iy * v[1]);   // Mahalanobis^2
        if (m2 >= 0) out.pct = Math.max(0, Math.min(100, (1 - Math.exp(-m2 / 2)) * 100));
      }
    }
    return out;
  };

  // Obs overlay = just the observed position marker + a readout (NO envelope
  // ellipses - they cluttered the tracks). The marker matches the home track maps:
  // invest -> red X, active storm -> the spinning hurricane glyph. The readout still
  // ranks the obs vs the matched cluster's spread (from the envelope covariance, not
  // drawn). Markers/labels sit ON TOP (drawn after the coast lines in _show).
  EnsCentersViewer.prototype._drawObsMarkers = function (g, resolved) {
    var ext = this.extent, mw = this.map.w, mh = this.map.h;
    // Project all markers first and seed the OCCUPIED list with each glyph footprint,
    // so a label never lands on another system's icon. Each label then picks the
    // first candidate anchor (around its own marker) clear of everything placed so
    // far (other glyphs + earlier labels) - collision avoidance for nearby systems.
    var items = [], placed = [], i;
    for (i = 0; i < resolved.length; i++) {
      var o = resolved[i].obs;
      var p = TATRegions.project(wrap180(o.lon), o.lat, ext, mw, mh);
      items.push({ o: o, x: p[0], y: p[1] });
      placed.push({ x: p[0] - 12, y: p[1] - 12, w: 24, h: 24 });
    }
    for (i = 0; i < items.length; i++) {
      var it = items[i], ob = it.o;
      var col = this._drawObsMarker(g, it.x, it.y, ob);
      // readout: name, location, time of fix, intensity (V wind / P pressure)
      var lon180 = wrap180(ob.lon);
      var loc = Math.abs(ob.lat).toFixed(1) + (ob.lat >= 0 ? 'N' : 'S') + '  ' +
                Math.abs(lon180).toFixed(1) + (lon180 >= 0 ? 'E' : 'W');
      var lines = [ob.name, loc];
      if (ob.timeMs) lines.push(shortInit(ob.timeMs));
      var vp = [];
      if (ob.kt != null) vp.push('V ' + Math.round(ob.kt) + ' kt');
      if (ob.mslp != null) vp.push('P ' + Math.round(ob.mslp) + ' hPa');
      if (vp.length) lines.push(vp.join('   '));
      this._drawObsLabel(g, it.x, it.y, lines, col, placed);
    }
  };

  // Draw the observed-position marker; returns its accent color (for the label).
  EnsCentersViewer.prototype._drawObsMarker = function (g, x, y, o) {
    return (o.kind === 'storm') ? this._drawStormGlyph(g, x, y, o) : this._drawInvestX(g, x, y);
  };

  // Invest: the NHC red X (mirrors render_tracks_svg's invest-current X), with a red
  // glow so it reads on the field. Returns the label accent.
  EnsCentersViewer.prototype._drawInvestX = function (g, x, y) {
    var R = 7;
    g.save();
    g.shadowColor = 'rgba(255,42,42,0.9)'; g.shadowBlur = 7;
    g.strokeStyle = INVEST_RED; g.lineWidth = 2.6; g.lineCap = 'round';
    g.beginPath();
    g.moveTo(x - R, y - R); g.lineTo(x + R, y + R);
    g.moveTo(x - R, y + R); g.lineTo(x + R, y - R);
    g.stroke();
    g.restore();
    return '#ff6a6a';
  };

  // Active storm: the spinning TAT hurricane glyph, filled + lettered by SSHWS stage
  // (same icon/colors as the home maps). The path spins by frame index (CCW north of
  // the equator, CW south) so a copied GIF shows it rotating; the stage letter stays
  // upright. Returns the SSHWS fill color (for the label).
  EnsCentersViewer.prototype._drawStormGlyph = function (g, x, y, o) {
    var mk = sshsMark(o.kt), color = mk.color;
    var dir = (o.lat >= 0) ? -1 : 1, rot = dir * ((this.idx || 0) * 0.4);
    g.save();
    g.translate(x, y); g.rotate(rot);
    g.shadowColor = color; g.shadowBlur = 6; g.fillStyle = color;
    if (HURRICANE_PATH2D) { g.scale(GLYPH_SCALE, GLYPH_SCALE); g.fill(HURRICANE_PATH2D); }
    else { g.beginPath(); g.arc(0, 0, 11, 0, 6.2832); g.fill(); }   // jsdom fallback (no Path2D)
    g.restore();
    // stage letter, upright + centered, white with a dark casing
    g.save();
    g.font = '900 13px ' + FONT; g.textAlign = 'center'; g.textBaseline = 'middle'; g.lineJoin = 'round';
    g.lineWidth = 2.6; g.strokeStyle = 'rgba(0,0,0,0.6)'; g.strokeText(mk.letter, x, y);
    g.fillStyle = '#ffffff'; g.fillText(mk.letter, x, y);
    g.restore();
    return color;
  };

  EnsCentersViewer.prototype._drawObsLabel = function (g, x, y, lines, accent, placed) {
    accent = accent || '#cfd8e6';   // neutral fallback (marker always supplies its color)
    placed = placed || [];
    g.save(); g.font = '700 10px ' + FONT;
    var w = 0, i; for (i = 0; i < lines.length; i++) w = Math.max(w, g.measureText(lines[i]).width);
    var pad = 5, lh = 13, bw = w + pad * 2, bh = lines.length * lh + pad * 2;
    var mapW = this.map.w, mapH = this.map.h;
    // candidate anchors around the marker in RINGS of increasing distance (right,
    // left, below, above, then diagonals at each ring); first clear of `placed`
    // (+ in bounds) wins, so a crowded label escapes farther out instead of stacking.
    var cands = [], rings = [13, 46, 82], ri, d;
    for (ri = 0; ri < rings.length; ri++) {
      d = rings[ri];
      cands.push([x + d, y - bh / 2], [x - d - bw, y - bh / 2],
                 [x - bw / 2, y + d], [x - bw / 2, y - d - bh],
                 [x + d, y + d], [x - d - bw, y + d],
                 [x + d, y - d - bh], [x - d - bw, y - d - bh]);
    }
    function overlaps(bx, by) {
      for (var k = 0; k < placed.length; k++) {
        var q = placed[k];
        if (bx < q.x + q.w + 3 && bx + bw + 3 > q.x && by < q.y + q.h + 3 && by + bh + 3 > q.y) return true;
      }
      return false;
    }
    var bx = null, by = null, fbx = null, fby = null;
    for (i = 0; i < cands.length; i++) {
      var cx = Math.max(2, Math.min(cands[i][0], mapW - bw - 2));
      var cy = Math.max(2, Math.min(cands[i][1], mapH - bh - 2));
      if (fbx === null) { fbx = cx; fby = cy; }     // first (preferred) as the fallback
      if (!overlaps(cx, cy)) { bx = cx; by = cy; break; }
    }
    if (bx === null) { bx = fbx; by = fby; }         // all crowded -> least-bad (right of marker)
    placed.push({ x: bx, y: by, w: bw, h: bh });
    // leader from the marker to the label's nearest edge when it sits far out, so a
    // pushed-aside label still clearly belongs to its system.
    var nx = Math.max(bx, Math.min(x, bx + bw)), ny = Math.max(by, Math.min(y, by + bh));
    if ((nx - x) * (nx - x) + (ny - y) * (ny - y) > 16 * 16) {
      g.strokeStyle = accent; g.globalAlpha = 0.55; g.lineWidth = 1;
      g.beginPath(); g.moveTo(x, y); g.lineTo(nx, ny); g.stroke(); g.globalAlpha = 1;
    }
    g.fillStyle = 'rgba(7,16,28,0.85)'; g.strokeStyle = accent; g.lineWidth = 1;
    roundRectPath(g, bx, by, bw, bh, 4); g.fill(); g.stroke();
    g.textBaseline = 'top'; g.textAlign = 'left';
    for (i = 0; i < lines.length; i++) { g.fillStyle = (i === 0) ? accent : C.fg; g.fillText(lines[i], bx + pad, by + pad + i * lh); }
    g.restore();
  };

  // canvas-space note when obs mode is on but nothing is in view to compare
  EnsCentersViewer.prototype._drawObsNote = function (g) {
    var msg = this._obsFailed ? 'Obs feed unavailable' : 'No active system to compare';
    g.save(); g.font = '600 11px ' + FONT; g.textBaseline = 'top'; g.textAlign = 'left';
    var pad = 7, tw = g.measureText(msg).width, w = tw + pad * 2, h = 24;
    var x = this.map.x + this.map.w / 2 - w / 2, y = this.map.y + 8;
    g.fillStyle = 'rgba(7,16,28,0.82)'; g.strokeStyle = C.border; g.lineWidth = 1;
    roundRectPath(g, x, y, w, h, 5); g.fill(); g.stroke();
    g.fillStyle = C.muted; g.fillText(msg, x + pad, y + 6);
    g.restore();
  };

  EnsCentersViewer.prototype._setObs = function (on) {
    this.obsOn = !!on;
    try { localStorage.setItem(LS_OBS, this.obsOn ? 'on' : 'off'); } catch (e) {}
    this._syncToolkitButtons();
    this._ensureTracks(); this._ensureObs();
    if (this.regionFrames.length) this._show(this.idx);
  };

  // ---- figure layout (CSS px; contexts are dpr-scaled so we draw in CSS px) ----
  EnsCentersViewer.prototype._layout = function () {
    var availW = (this.dom.mapframe && this.dom.mapframe.clientWidth) || 800;
    this._lastAvailW = availW;
    var figW = Math.max(availW, MIN_FIG_W);
    var displayW = availW;
    var pad = 14, gap = 14, headerH = 50;
    var tableW = (figW < 620) ? Math.round(figW * 0.3) : 212;
    var mapBoxW = figW - 2 * pad - tableW - gap;
    // ONE fixed box aspect for every region/model. The extent is pre-framed to
    // BOX_ASPECT (frameExtent), so the map FILLS the box exactly - no contain-fit, no
    // letterbox bands, identical figure dimensions for a given figW across all regions.
    var boxH = mapBoxW / BOX_ASPECT;
    var figH = pad + headerH + boxH + pad;

    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.dpr = dpr; this.figW = figW; this.figH = figH;
    var cv = this.dom.canvas;
    cv.width = Math.round(figW * dpr); cv.height = Math.round(figH * dpr);
    cv.style.width = displayW + 'px';
    cv.style.height = (displayW * figH / figW) + 'px';
    this._scale(this.ctx);
    // the map rect == the box (fills it; framed extent guarantees no distortion)
    this.box = { x: pad, y: pad + headerH, w: mapBoxW, h: boxH };
    this.map = { x: pad, y: pad + headerH, w: mapBoxW, h: boxH };
    this.table = { x: pad + mapBoxW + gap, y: pad + headerH, w: tableW, h: boxH };
    this.headerXY = { x: pad, y: pad };
    this.ringR = 2.3; this.ringLW = 1.4; this.fillR = 2.5;
    // size offscreen layers
    this.staticLayer.width = cv.width; this.staticLayer.height = cv.height;
    this.trailLayer.width = Math.round(this.map.w * dpr); this.trailLayer.height = Math.round(this.map.h * dpr);
    this._resetTrail();    // reassigning width already wiped the bitmap; reset counter via the one helper
  };

  EnsCentersViewer.prototype._scale = function (ctx) {
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
  };

  // ---- static figure parts: bg + header-less basemap box + table + border ----
  EnsCentersViewer.prototype._drawFigure = function () {
    if (!this.map) return;
    var g = this.staticLayer.getContext('2d');
    this._scale(g);
    g.clearRect(0, 0, this.figW, this.figH);
    g.fillStyle = C.bg; g.fillRect(0, 0, this.figW, this.figH);

    // map box bg (ocean) so letterbox bands are seamless, then basemap
    g.save();
    g.fillStyle = BASEMAP_STYLE.ocean;
    g.fillRect(this.box.x, this.box.y, this.box.w, this.box.h);
    g.beginPath(); g.rect(this.map.x, this.map.y, this.map.w, this.map.h); g.clip();
    g.translate(this.map.x, this.map.y);
    // FILL only (ocean + grid + land) on the static layer; the coast + country +
    // state border LINES are drawn ON TOP of the centers per frame (see _show).
    TATRegions.drawBasemapFill(g, this.extent, this.geo, this.map.w, this.map.h, BASEMAP_STYLE);
    g.restore();
    // map box border
    g.strokeStyle = C.border; g.lineWidth = 1;
    g.strokeRect(this.box.x + 0.5, this.box.y + 0.5, this.box.w - 1, this.box.h - 1);

    this._drawTable(g);
  };

  EnsCentersViewer.prototype._drawTable = function (g) {
    var t = this.table, rows = this.peaks, r = TATRegions.get(this.region);
    g.save();
    g.fillStyle = '#12182a'; g.strokeStyle = C.border; g.lineWidth = 1;
    g.fillRect(t.x, t.y, t.w, t.h);
    g.strokeRect(t.x + 0.5, t.y + 0.5, t.w - 1, t.h - 1);
    // title (accent bar + label)
    g.fillStyle = C.accent; g.fillRect(t.x, t.y, 3, 18);
    g.fillStyle = C.fg; g.font = '700 12px ' + FONT; g.textBaseline = 'top';
    g.fillText('Peak  ·  ' + (r ? r.label : ''), t.x + 9, t.y + 8);
    // 2-column body
    var n = rows.length, perCol = Math.ceil(n / 2);
    var colW = (t.w - 12) / 2;
    var headerY = t.y + 32, bodyTop = t.y + 42, bodyH = t.h - 50;
    var rowH = Math.max(11, Math.min(bodyH / perCol, 18));
    var fs = Math.max(8.5, Math.min(rowH * 0.66, 11));
    // ONE compact header per column, label x-positions matched to the data
    // columns below (member left, Pmin/V right-aligned) - no overlap.
    g.font = '700 8px ' + FONT; g.fillStyle = C.muted; g.textBaseline = 'alphabetic';
    for (var hc = 0; hc < 2; hc++) {
      var hx = t.x + 6 + hc * colW;
      g.textAlign = 'left'; g.fillText('MEMBER', hx + 6, headerY);
      g.textAlign = 'right'; g.fillText('Pmin', hx + colW - 26, headerY);
      g.fillText('V', hx + colW - 4, headerY);
    }
    g.textAlign = 'left'; g.textBaseline = 'top';
    g.strokeStyle = C.border; g.lineWidth = 1;
    g.beginPath(); g.moveTo(t.x + 6, headerY + 4); g.lineTo(t.x + t.w - 6, headerY + 4); g.stroke();
    if (!n) { g.fillStyle = C.muted; g.font = '500 10px ' + FONT; g.fillText('No centers in region', t.x + 10, bodyTop + 4); }
    for (var i = 0; i < n; i++) {
      var col = (i < perCol) ? 0 : 1, rowi = (i < perCol) ? i : i - perCol;
      var cx = t.x + 6 + col * colW, cy = bodyTop + rowi * rowH, midY = cy + rowH / 2;
      var row = rows[i], ctl = (row.id === 'CTL');
      // swatch ring (pressure-bin color; identical for every member incl. CTL)
      g.strokeStyle = PRESSURE_BIN_COLORS[binKey(row.mslp)] || '#fff';
      g.lineWidth = 1.4; g.beginPath(); g.arc(cx + 5, midY, 3.4, 0, 6.2832); g.stroke();
      g.font = (ctl ? '700 ' : '600 ') + fs.toFixed(1) + 'px ' + FONT;
      g.textBaseline = 'middle'; g.textAlign = 'left';
      // member id. The control run gets a NEUTRAL white-outlined chip + bold
      // white text - NOT the accent and NOT any pressure-bin hue - so it reads
      // as "the control", distinct from the 970-990 yellow and 990-1000 blue bins.
      if (ctl) {
        var tw = g.measureText(row.id).width;
        var chH = Math.min(rowH - 2, fs + 4), chW = tw + 8, chX = cx + 11, chY = midY - chH / 2;
        roundRectPath(g, chX, chY, chW, chH, Math.min(3, chH / 2));
        g.fillStyle = 'rgba(255,255,255,0.10)'; g.fill();
        g.strokeStyle = 'rgba(255,255,255,0.55)'; g.lineWidth = 1; g.stroke();
        g.fillStyle = '#ffffff'; g.fillText(row.id, chX + 4, midY);
      } else {
        g.fillStyle = C.fg; g.fillText(row.id, cx + 13, midY);
      }
      g.textAlign = 'right';
      g.fillStyle = ctl ? '#ffffff' : C.fg;
      g.fillText(row.mslp.toFixed(0), cx + colW - 26, midY);
      g.fillStyle = ctl ? '#ffffff' : C.muted;
      g.fillText(row.vmax.toFixed(0), cx + colW - 4, midY);
      g.textAlign = 'left'; g.textBaseline = 'top';
    }
    g.restore();
  };

  // ---- per-frame dynamic overlays ----
  EnsCentersViewer.prototype._drawHeader = function (g, i) {
    var d = this.data, acc = this.regionPrefix.length ? this.regionPrefix[Math.min(i, this.regionPrefix.length - 1)] : 0;
    g.save(); g.textBaseline = 'alphabetic'; g.textAlign = 'left';
    g.fillStyle = C.fg; g.font = '800 17px ' + FONT;
    g.fillText((d.model_label || 'ECMWF ENS') + '  ·  Ensemble Cyclone Centers', this.headerXY.x, this.headerXY.y + 17);
    g.fillStyle = C.muted; g.font = '500 12px ' + FONT;
    // HARD RULE: the burned-in header carries the CURRENT forecast hour + its
    // valid time (not just init), so a copied still / every GIF frame is self-
    // documenting. Drawn per-frame here (not on the static layer) so each frame
    // shows its own F-hour + valid time. The HTML chrome (dom.fhour/dom.valid)
    // is kept for the live UI, but the canvas is the source of truth on share.
    var stepH = (this.steps && this.steps.length)
      ? this.steps[Math.min(i, this.steps.length - 1)] : 0;
    g.fillText('init ' + shortInit(this.initMs) +
      '  ·  F' + String(stepH).padStart(3, '0') +
      '  ·  valid ' + validLabel(this.initMs, stepH) +
      '  ·  ' + (d.n_members || 0) + ' members  ·  ' + fmtInt(acc) + ' centers',
      this.headerXY.x, this.headerXY.y + 35);
    g.restore();
  };

  // Legend: FIXED top-left, below the burned-in header. No relocation, no crowding
  // test - with the uniform fixed-aspect frame it sits in the same spot on every
  // region/model (the Vmax plume is pinned top-right in _drawPlumeInset).
  EnsCentersViewer.prototype._drawLegend = function (g) {
    var bins = (this.data && this.data.pressure_bins) || [];
    var lines = bins.length + 2, lh = 14, padx = 9, pady = 7;
    var w = 132, h = pady * 2 + lines * lh;
    var x = this.map.x + 8, y = this.map.y + 8;
    g.save();
    g.fillStyle = 'rgba(7,16,28,0.78)'; g.strokeStyle = C.border; g.lineWidth = 1;
    g.fillRect(x, y, w, h); g.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
    g.textBaseline = 'middle'; g.textAlign = 'left';
    for (var i = 0; i < bins.length; i++) {
      var cy = y + pady + i * lh + lh / 2;
      g.strokeStyle = PRESSURE_BIN_COLORS[bins[i].key] || '#fff'; g.lineWidth = 1.7;
      g.beginPath(); g.arc(x + padx + 4, cy, 4, 0, 6.2832); g.stroke();
      g.fillStyle = C.fg; g.font = '600 10.5px ' + FONT;
      g.fillText(bins[i].label, x + padx + 14, cy);
    }
    // bottom note(s): glyph key. Cheerios = filled/hollow (unchanged); Lines mode
    // explains the spaghetti + (if on) the bold ensemble mean.
    var ny = y + pady + bins.length * lh;
    g.font = '500 9.5px ' + FONT; g.textBaseline = 'middle';
    if (this.dataStyle === 'lines' && this.tracksReady()) {
      var my = ny + lh / 2;                                   // connector thread + a colored dot
      g.strokeStyle = CONNECTOR_COLOR; g.lineWidth = CONNECTOR_LW;
      g.beginPath(); g.moveTo(x + padx, my); g.lineTo(x + padx + 9, my); g.stroke();
      g.fillStyle = '#dfe8ff'; g.beginPath(); g.arc(x + padx + 4.5, my, 2, 0, 6.2832); g.fill();
      g.fillStyle = C.muted; g.fillText('Member tracks', x + padx + 14, my);
      if (this.meanOn) {
        g.strokeStyle = '#fff'; g.lineWidth = MEAN_LW;
        g.beginPath(); g.moveTo(x + padx, ny + lh + lh / 2); g.lineTo(x + padx + 9, ny + lh + lh / 2); g.stroke();
        g.fillStyle = C.muted; g.fillText('Ensemble mean', x + padx + 14, ny + lh + lh / 2);
      } else {
        g.fillStyle = C.muted; g.fillText('Up to current F-hour', x + padx, ny + lh + lh / 2);
      }
    } else {
      g.fillStyle = '#fff'; g.beginPath(); g.arc(x + padx + 4, ny + lh / 2, 3.2, 0, 6.2832); g.fill();
      g.fillStyle = C.muted; g.fillText('Filled = current step', x + padx + 14, ny + lh / 2);
      g.strokeStyle = '#fff'; g.lineWidth = 1.5; g.beginPath(); g.arc(x + padx + 4, ny + lh + lh / 2, 3.4, 0, 6.2832); g.stroke();
      g.fillText('Hollow = trail', x + padx + 14, ny + lh + lh / 2);
    }
    g.restore();
  };

  EnsCentersViewer.prototype._drawWatermark = function (g) {
    g.save();
    g.font = '700 12px ' + FONT; g.textAlign = 'right'; g.textBaseline = 'bottom';
    g.fillStyle = 'rgba(232,235,239,0.42)';
    g.fillText(WATERMARK, this.map.x + this.map.w - 9, this.map.y + this.map.h - 7);
    g.restore();
  };

  // draw one step's centers into a (translated) map-space context
  EnsCentersViewer.prototype._drawStep = function (g, s, filled) {
    var pts = this.regionFrames[s]; if (!pts || !pts.length) return;
    var ext = this.extent, mw = this.map.w, mh = this.map.h;
    var buckets = {}; var bo;
    for (bo = 0; bo < BIN_ORDER.length; bo++) buckets[BIN_ORDER[bo]] = [];
    for (var k = 0; k < pts.length; k++) buckets[binKey(pts[k][2])].push(pts[k]);
    g.globalAlpha = filled ? 1 : 0.92;
    var r = filled ? this.fillR : this.ringR;
    for (bo = 0; bo < BIN_ORDER.length; bo++) {
      var key = BIN_ORDER[bo], arr = buckets[key]; if (!arr.length) continue;
      g.beginPath();
      for (var j = 0; j < arr.length; j++) {
        var p = TATRegions.project(arr[j][1], arr[j][0], ext, mw, mh);
        g.moveTo(p[0] + r, p[1]); g.arc(p[0], p[1], r, 0, 6.2832);
      }
      if (filled) { g.fillStyle = PRESSURE_BIN_COLORS[key] || '#fff'; g.fill(); }
      else { g.strokeStyle = PRESSURE_BIN_COLORS[key] || '#fff'; g.lineWidth = this.ringLW; g.stroke(); }
    }
    g.globalAlpha = 1;
  };

  // ensure the trail layer holds hollow rings for steps 0..(i-1)
  // Invalidate the trail: clear the offscreen layer's pixels AND reset the
  // progress counter, together. The invariant "trailUpTo === -1 implies the
  // layer is empty" must hold unconditionally, so callers that mean to
  // invalidate the trail use THIS (never a bare `trailUpTo = -1`, which would
  // leave stale pixels for _ensureTrail to draw a new pass on top of).
  EnsCentersViewer.prototype._resetTrail = function () {
    var g = this.trailLayer.getContext('2d');
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, this.trailLayer.width, this.trailLayer.height);
    this.trailUpTo = -1;
  };

  EnsCentersViewer.prototype._ensureTrail = function (i) {
    var target = (this.trailMode === 'trail') ? (i - 1) : -1;
    var g = this.trailLayer.getContext('2d');
    if (target < 0) {
      // ALWAYS clear - never trust a possibly-dirty layer. Clearing an
      // already-empty layer is a cheap no-op.
      g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, this.trailLayer.width, this.trailLayer.height);
      this.trailUpTo = -1;
      return;
    }
    if (this.trailUpTo === target) return;
    // Scrubbed back -> rebuild, OR a from-scratch build (counter at -1): clear
    // before the draw loop so a new pass never lands on stale pixels. An
    // in-range forward step (0 <= trailUpTo < target) keeps building incrementally.
    if (this.trailUpTo > target || this.trailUpTo === -1) {
      g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, this.trailLayer.width, this.trailLayer.height);
      this.trailUpTo = -1;
    }
    this._scale(g);
    for (var s = this.trailUpTo + 1; s <= target; s++) this._drawStep(g, s, false);
    this.trailUpTo = target;
  };

  // ---- compose a frame ----
  EnsCentersViewer.prototype._show = function (i) {
    if (!this.regionFrames.length || !this.map) return;
    var n = this.regionFrames.length;
    this.idx = ((i % n) + n) % n;
    this.visible = this.regionFrames[this.idx];
    // the Cheerios trail layer is not used in Lines mode; skip building it there.
    if (!(this.dataStyle === 'lines' && this.tracksReady())) this._ensureTrail(this.idx);

    var ctx = this.ctx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.dom.canvas.width, this.dom.canvas.height);
    // static figure (bg + basemap + table + border)
    ctx.drawImage(this.staticLayer, 0, 0);
    this._scale(ctx);
    this._drawHeader(ctx, this.idx);
    // data into the map rect. Default = Cheerios (per-step rings/dots, unchanged);
    // Lines = per-member spaghetti. Mean overlay (independent) sits ABOVE the data
    // but BELOW the coast lines (canonical order), so geography stays legible.
    var lines = (this.dataStyle === 'lines' && this.tracksReady());
    var meanOn = (this.meanOn && this.tracksReady());
    var obsResolved = (this.obsOn && this.tracksReady() && this.obs) ? this._resolveObs() : null;
    ctx.save();
    ctx.beginPath(); ctx.rect(this.map.x, this.map.y, this.map.w, this.map.h); ctx.clip();
    if (!lines && this.trailMode === 'trail') ctx.drawImage(this.trailLayer, this.map.x, this.map.y, this.map.w, this.map.h);
    ctx.translate(this.map.x, this.map.y);
    if (lines) {
      this._drawLines(ctx, this.idx);            // bold per-member spaghetti
    } else {
      // Cheerios: subtle grey connector threads UNDER the dots, then the per-step
      // hollow trail (drawn above) + the filled current heads on top.
      if (this.tracksReady()) this._drawConnectors(ctx, this.idx);
      this._drawStep(ctx, this.idx, true);       // current step filled
    }
    if (meanOn) this._drawMean(ctx, this.idx);   // bold ensemble-mean tracks
    // coast + country + state borders ON TOP of the centers (canonical order),
    // still clipped + translated to the map rect.
    if (window.TATRegions && TATRegions.drawBasemapLines) {
      TATRegions.drawBasemapLines(ctx, this.extent, this.geo, this.map.w, this.map.h, BASEMAP_STYLE);
    }
    // annotations ON TOP of everything: the region-deepest-center highlight, then
    // the bold focal obs marker + readout (the point of obs mode).
    if (this.minpOn) this._drawMinMslp(ctx);
    if (obsResolved && obsResolved.length) this._drawObsMarkers(ctx, obsResolved);
    ctx.restore();
    this._drawLegend(ctx);
    if (meanOn) this._drawPlumeInset(ctx);       // compact Vmax plume, bottom-right
    if (this.obsOn && this.tracksReady() && obsResolved && !obsResolved.length) this._drawObsNote(ctx);
    this._drawWatermark(ctx);

    var stepH = this.steps[this.idx];
    this.dom.fhour.textContent = 'F' + String(stepH).padStart(3, '0');
    var validTxt = validLabel(this.initMs, stepH) + '  ·  ' + fmtInt(this.visible.length) + ' this step';
    if (this.minpOn && this.minCenter && this.minCenter.mslp != null) {
      validTxt += '  ·  min MSLP ' + Math.round(this.minCenter.mslp) + ' hPa (F' + String(this.minCenter.step).padStart(3, '0') + ')';
    }
    this.dom.valid.textContent = validTxt;
    if (String(this.dom.scrub.value) !== String(this.idx)) this.dom.scrub.value = this.idx;
  };

  EnsCentersViewer.prototype._setTrailMode = function (mode) {
    this.trailMode = (mode === 'current') ? 'current' : 'trail';
    try { localStorage.setItem(LS_TRAIL, this.trailMode); } catch (e) {}
    this._syncTrailBtn();
    this._resetTrail();   // clear pixels too, not just the counter (the bug)
    if (this.regionFrames.length) this._show(this.idx);
  };

  // ---- segmented toggle (model selector) ----
  EnsCentersViewer.prototype._buildToggle = function (container, defs, active, onPick) {
    if (!container) return;
    container.innerHTML = '';
    for (var i = 0; i < defs.length; i++) (function (def) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'hafs-seg' + (def.slug === active ? ' active' : '');
      b.textContent = def.label; b.setAttribute('data-slug', def.slug);
      b.addEventListener('click', function () { onPick(def.slug); });
      container.appendChild(b);
    })(defs[i]);
    // Always show the model selector when there is at least one model (even a
    // single one), so the active model is always labelled; only hide it if the
    // manifest somehow carried zero models.
    container.parentNode.style.display = defs.length ? '' : 'none';
  };

  EnsCentersViewer.prototype._highlight = function (container, slug) {
    if (!container) return;
    var btns = container.querySelectorAll('.hafs-seg');
    for (var i = 0; i < btns.length; i++) btns[i].classList.toggle('active', btns[i].getAttribute('data-slug') === slug);
  };

  // ---- transport ----
  EnsCentersViewer.prototype._frameMs = function () { return 1000 / (BASE_FPS * (this.speed || 1)); };
  EnsCentersViewer.prototype._tick = function (ts) {
    if (!this.playing) return;
    if (ts - this.lastTick >= this._frameMs()) { this.lastTick = ts; this._show(this.idx + 1); }
    var self = this; this.raf = requestAnimationFrame(function (t) { self._tick(t); });
  };
  EnsCentersViewer.prototype._play = function () {
    if (this.regionFrames.length <= 1) return;
    this.playing = true; this.dom.play.textContent = '❚❚ Pause'; this.lastTick = 0;
    if (this.raf) cancelAnimationFrame(this.raf);
    var self = this; this.raf = requestAnimationFrame(function (t) { self._tick(t); });
  };
  EnsCentersViewer.prototype._pause = function () {
    this.playing = false; if (this.dom.play) this.dom.play.textContent = '► Play';
    if (this.raf) { cancelAnimationFrame(this.raf); this.raf = null; }
  };
  EnsCentersViewer.prototype._syncTrailBtn = function () {
    if (!this.dom.trail) return;
    this.dom.trail.textContent = (this.trailMode === 'trail') ? 'Trail: on' : 'Trail: off';
    this.dom.trail.classList.toggle('on', this.trailMode === 'trail');
  };

  EnsCentersViewer.prototype._togglePlay = function () { this.playing ? this._pause() : this._play(); };
  EnsCentersViewer.prototype._step = function (delta) { if (this.regionFrames.length) this._show(this.idx + delta); };

  EnsCentersViewer.prototype._wire = function () {
    var self = this;
    if (this.dom.regionBtn) this.dom.regionBtn.addEventListener('click', function () { if (self.picker) self.picker.open(); });
    if (this.dom.trail) this.dom.trail.addEventListener('click', function () {
      self._setTrailMode(self.trailMode === 'trail' ? 'current' : 'trail');
    });
    if (this.dom.style) this.dom.style.addEventListener('click', function () {
      self._setDataStyle(self.dataStyle === 'lines' ? 'cheerios' : 'lines');
    });
    if (this.dom.mean) this.dom.mean.addEventListener('click', function () {
      self._setMean(!self.meanOn);
    });
    if (this.dom.ppts) this.dom.ppts.addEventListener('click', function () {
      self._setPpts(!self.pptsOn);
    });
    if (this.dom.obs) this.dom.obs.addEventListener('click', function () {
      self._setObs(!self.obsOn);
    });
    if (this.dom.minp) this.dom.minp.addEventListener('click', function () {
      self._setMinp(!self.minpOn);
    });
    if (this.dom.gif) this.dom.gif.addEventListener('click', function () { self._openGif(); });
    if (this.dom.gifpreset) this.dom.gifpreset.addEventListener('change', function () {
      try { localStorage.setItem(LS_GIFPRESET, this.value); } catch (e) {}
    });
    if (this.dom.gifmake) this.dom.gifmake.addEventListener('click', function () { self._makeGif(); });
    if (this.dom.gifx) this.dom.gifx.addEventListener('click', function () { self._closeGif(); });
    if (this.dom.gifmodal) this.dom.gifmodal.addEventListener('click', function (e) {
      if (e.target === self.dom.gifmodal && !self.encoding) self._closeGif();
    });
    this.dom.play.addEventListener('click', function () { self._togglePlay(); });
    this.dom.stepB.addEventListener('click', function () { self._pause(); self._step(-1); });
    this.dom.stepF.addEventListener('click', function () { self._pause(); self._step(1); });

    var sp = this.dom.speed;
    for (var i = 0; i < SPEED_OPTIONS.length; i++) {
      var o = document.createElement('option');
      o.value = SPEED_OPTIONS[i]; o.textContent = SPEED_OPTIONS[i] + '×';
      if (SPEED_OPTIONS[i] === 1) o.selected = true; sp.appendChild(o);
    }
    sp.addEventListener('change', function () { self.speed = parseFloat(this.value); if (self.playing) self.lastTick = 0; });

    // Run (cycle) selector: load the chosen cycle's JSON and re-render the full
    // figure for it. Region / trail / speed persist (instance state, untouched by
    // _onData); the scrubber resets to the new run's steps.
    if (this.dom.run) {
      this.dom.run.addEventListener('change', function () {
        var entry = self._modelEntry(self.model), cyc = this.value;
        if (!entry || !cyc) return;
        self.followLatest = (cyc === entry.latest);   // picking latest re-enables auto-advance
        self._loadCycle(self.model, cyc);
      });
    }
    this.dom.scrub.addEventListener('input', function () { self._pause(); self._show(parseInt(this.value, 10) || 0); });

    this.root.addEventListener('keydown', function (e) {
      var tag = e.target && e.target.tagName;
      if (tag === 'SELECT' || tag === 'INPUT' || tag === 'BUTTON') return;
      if (e.key === 'ArrowLeft') { self._pause(); self._step(-1); e.preventDefault(); }
      else if (e.key === 'ArrowRight') { self._pause(); self._step(1); e.preventDefault(); }
      else if (e.key === ' ' || e.key === 'Spacebar') { self._togglePlay(); e.preventDefault(); }
    });

    this._syncTrailBtn();
    this._syncToolkitButtons();

    this.dom.canvas.addEventListener('mousemove', function (ev) { self._hover(ev); });
    this.dom.canvas.addEventListener('mouseleave', function () { if (self.dom.tooltip) self.dom.tooltip.style.display = 'none'; });

    if (window.ResizeObserver) {
      this._ro = new ResizeObserver(function () { self._resizeDebounced(); });
      if (this.dom.mapframe) this._ro.observe(this.dom.mapframe);
    } else { window.addEventListener('resize', function () { self._resizeDebounced(); }); }
  };

  EnsCentersViewer.prototype._resizeDebounced = function () {
    var self = this;
    clearTimeout(this._rt);
    this._rt = setTimeout(function () {
      if (!self.regionFrames.length) return;
      var w = (self.dom.mapframe && self.dom.mapframe.clientWidth) || 0;
      if (w === self._lastAvailW) return;
      self._layout(); self._drawFigure(); self._show(self.idx);
    }, 140);
  };

  // hover hit-tests the CURRENT step's (filled) centers, in map space
  EnsCentersViewer.prototype._hover = function (ev) {
    var tip = this.dom.tooltip;
    if (!tip || !this.visible || !this.visible.length || !this.map) return;
    var rect = this.dom.canvas.getBoundingClientRect();
    var sx = this.dom.canvas.width / rect.width / this.dpr;   // css px per client px
    var mx = (ev.clientX - rect.left) * sx - this.map.x;
    var my = (ev.clientY - rect.top) * sx - this.map.y;
    if (mx < 0 || my < 0 || mx > this.map.w || my > this.map.h) { tip.style.display = 'none'; return; }
    var centers = this.visible, best = null, bestD = 11 * 11;
    for (var k = 0; k < centers.length; k++) {
      var p = TATRegions.project(centers[k][1], centers[k][0], this.extent, this.map.w, this.map.h);
      var dx = p[0] - mx, dy = p[1] - my, d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = centers[k]; }
    }
    if (!best) { tip.style.display = 'none'; return; }
    tip.style.display = 'block';
    tip.style.left = (ev.clientX - rect.left + 12) + 'px';
    tip.style.top = (ev.clientY - rect.top + 12) + 'px';
    tip.innerHTML = best[2].toFixed(0) + ' hPa  ·  ' + best[3].toFixed(0) + ' kt<br>' +
      Math.abs(best[0]).toFixed(1) + (best[0] >= 0 ? 'N' : 'S') + '  ' +
      Math.abs(best[1]).toFixed(1) + (best[1] >= 0 ? 'E' : 'W');
  };

  // ---- GIF export (reuses the /satellite/ gif.js 0.2.0 pattern) ----
  EnsCentersViewer.prototype._ensureGifWorker = function (cb) {
    if (this._gifWorker) { cb(this._gifWorker); return; }
    var self = this;
    fetch(GIF_WORKER_URL).then(function (r) { return r.text(); }).then(function (src) {
      self._gifWorker = URL.createObjectURL(new Blob([src], { type: 'application/javascript' }));
      cb(self._gifWorker);
    }).catch(function () { cb(GIF_WORKER_URL); });   // CDN fallback
  };

  EnsCentersViewer.prototype._gifPreset = function () {
    var p = this.dom.gifpreset && this.dom.gifpreset.value;
    return GIF_PRESET_W[p] ? p : 'full';
  };

  // Fill the Start/End hour <select>s from the LOADED run's real forecast hours
  // (this.steps, e.g. 0, 6, 12 … the model's max), labelled F000-style. The
  // prior selection PERSISTS when still a valid hour in the new run; otherwise
  // it clamps (start -> first, end -> last). With nothing valid yet (first open,
  // or a different hour set) the default is the FULL forecast: first -> last.
  EnsCentersViewer.prototype._populateGifHours = function () {
    var sSel = this.dom.gifstart, eSel = this.dom.gifend, steps = this.steps;
    if (!sSel || !eSel || !steps.length) return;
    var prevS = parseInt(sSel.value, 10), prevE = parseInt(eSel.value, 10);
    var opts = '';
    for (var i = 0; i < steps.length; i++) {
      opts += '<option value="' + steps[i] + '">F' +
        String(steps[i]).padStart(3, '0') + '</option>';
    }
    sSel.innerHTML = opts; eSel.innerHTML = opts;
    var first = steps[0], last = steps[steps.length - 1];
    var s = (steps.indexOf(prevS) !== -1) ? prevS : first;   // persist or clamp
    var e = (steps.indexOf(prevE) !== -1) ? prevE : last;
    if (e < s) { var t = s; s = e; e = t; }                  // keep end >= start
    sSel.value = String(s); eSel.value = String(e);
  };

  EnsCentersViewer.prototype._openGif = function () {
    if (!this.steps.length) return;
    this._populateGifHours();                      // reflect the loaded run's hours
    if (this.dom.gifpreset) {                       // reflect the persisted preset
      var saved = null; try { saved = localStorage.getItem(LS_GIFPRESET); } catch (e) {}
      if (saved && GIF_PRESET_W[saved]) this.dom.gifpreset.value = saved;
    }
    this.dom.gifstatus.style.display = 'none';
    this.dom.gifmake.disabled = false;
    this.dom.gifmodal.classList.add('open');
  };

  EnsCentersViewer.prototype._closeGif = function () {
    this.dom.gifmodal.classList.remove('open');
    this.encoding = false;
  };

  // The forecast-step INDICES to capture: those whose forecast hour falls in
  // [startH, endH] inclusive, thinned by "skip every" WITHIN that range, always
  // keeping the final in-range step so the loop still ends on endH.
  EnsCentersViewer.prototype._rangeSteps = function (startH, endH, skip) {
    var steps = this.steps, inRange = [];
    for (var i = 0; i < steps.length; i++) {
      if (steps[i] >= startH && steps[i] <= endH) inRange.push(i);
    }
    if (!inRange.length) return [];
    var stride = (skip > 0) ? (skip + 1) : 1, out = [];
    for (var k = 0; k < inRange.length; k += stride) out.push(inRange[k]);
    var lastIdx = inRange[inRange.length - 1];
    if (out[out.length - 1] !== lastIdx) out.push(lastIdx);
    return out;
  };

  // Evenly sample a frame-index list down to n, keeping the endpoints. The
  // Discord preset uses this to TRIM WITHIN the chosen hour range (not a raw
  // frame count) so the GIF still spans start->end, just with fewer in-betweens.
  EnsCentersViewer.prototype._decimate = function (list, n) {
    if (n < 2 || list.length <= n) return list.slice();
    var out = [], seen = {};
    for (var k = 0; k < n; k++) {
      var v = list[Math.round(k * (list.length - 1) / (n - 1))];
      if (!seen[v]) { seen[v] = 1; out.push(v); }
    }
    return out;
  };

  EnsCentersViewer.prototype._makeGif = function () {
    if (typeof window.GIF === 'undefined') { alert('GIF library still loading, try again in a second.'); return; }
    var steps = this.steps, total = steps.length;
    if (total < 2) return;
    var first = steps[0], last = steps[steps.length - 1];
    // The user picks a forecast-HOUR range (Start/End). Read it, defaulting a
    // missing/garbage value to the full forecast, and clamp/swap so end >= start.
    var startH = parseInt(this.dom.gifstart && this.dom.gifstart.value, 10);
    var endH = parseInt(this.dom.gifend && this.dom.gifend.value, 10);
    if (isNaN(startH)) startH = first;
    if (isNaN(endH)) endH = last;
    if (endH < startH) { var t = startH; startH = endH; endH = t; }
    var fps = Math.max(1, Math.min(30, parseInt(this.dom.giffps.value, 10) || 10));
    var skip = Math.max(0, parseInt(this.dom.gifskip.value, 10) || 0);
    var preset = this._gifPreset();
    // The preset's only effect: the export WIDTH cap (color fidelity is fixed -
    // quality:1, no dither, in _gifRun). Discord additionally auto-trims frames
    // WITHIN the chosen range. Width is capped at the source device width cw.
    var W = Math.min(this.dom.canvas.width, GIF_PRESET_W[preset] || GIF_MAX_W);
    var status = this.dom.gifstatus, mk = this.dom.gifmake, self = this;
    // Frame set = the steps inside [startH, endH], skip-thinned, in order.
    var baseSel = this._rangeSteps(startH, endH, skip);
    if (baseSel.length < 2) {
      status.style.display = '';
      status.textContent = (startH === endH)
        ? 'Pick an End hour after the Start hour — a GIF needs at least 2 frames.'
        : 'That hour range and “Skip every” leave fewer than 2 frames — lower “Skip every”.';
      return;
    }
    status.style.display = ''; status.textContent = 'Encoding… 0%'; mk.disabled = true;
    this.encoding = true;
    this._pause();
    var maxTry = (preset === 'discord') ? 4 : 1;

    function fail(msg) {
      self.encoding = false; mk.disabled = false;
      status.style.display = ''; status.textContent = msg || 'GIF export failed - try again.';
    }
    function deliver(blob) {
      var bytes = (blob && blob.size) || 0;
      var mb = (bytes / 1048576).toFixed(1), over = bytes > GIF_HARD_CAP;
      var u = URL.createObjectURL(blob), a = document.createElement('a');
      a.href = u;
      a.download = (self.model || 'ens') + '_' + self.region + '_' + (self.data ? self.data.init_cycle : 'cycle') + '.gif';
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      requestAnimationFrame(function () { URL.revokeObjectURL(u); });
      self.encoding = false; mk.disabled = false;
      if (over) {
        status.textContent = 'Saved — ' + mb + ' MB  ⚠ over Discord’s 10 MB; try the Discord preset, a narrower hour range, or a lower FPS.';
      } else {
        status.textContent = 'Saved — ' + mb + ' MB';
        setTimeout(function () { self._closeGif(); }, 1400);
      }
    }
    function attempt(sel, tryNo) {
      self._gifRun(sel, fps, W, function (blob) {
        // Discord: under-target? deliver. Over? evenly TRIM the frame set WITHIN
        // the chosen hour range (size ~ linear in frames; quality/colors + the
        // start->end span untouched) and re-encode.
        if (preset === 'discord' && blob.size > GIF_DISCORD_TARGET && tryNo < maxTry && sel.length > GIF_FLOOR_FRAMES) {
          var next = Math.floor(sel.length * (GIF_DISCORD_TARGET / blob.size) * 0.92);
          next = Math.max(GIF_FLOOR_FRAMES, Math.min(next, sel.length - 2));   // always make progress
          status.textContent = 'Trimming to fit Discord (' + (blob.size / 1048576).toFixed(1) + ' MB)…';
          attempt(self._decimate(sel, next), tryNo + 1);
        } else {
          deliver(blob);
        }
      }, fail);
    }
    attempt(baseSel, 1);
  };

  // One encode pass at width W over the explicit frame-index list `sel` (already
  // range-filtered + skip-thinned by _makeGif). quality:1 + no dither (color
  // fidelity); builds the burned-in-header frames off the live canvas. Calls
  // onBlob(blob) on success, onFail(msg) on any error/timeout. Re-callable (the
  // Discord auto-fit re-runs it with a decimated list).
  EnsCentersViewer.prototype._gifRun = function (sel, fps, W, onBlob, onFail) {
    var self = this, total = this.steps.length;
    if (!sel || sel.length < 2) { onFail('Not enough frames for a GIF; lower "Skip every".'); return; }
    var selSet = {}; for (var s = 0; s < sel.length; s++) selSet[sel[s]] = 1;
    var lastSel = sel[sel.length - 1], delay = Math.round(1000 / fps);
    var cw = this.dom.canvas.width, ch = this.dom.canvas.height;
    var H = Math.round(W * ch / cw);
    var oc = document.createElement('canvas'); oc.width = W; oc.height = H;
    var octx = oc.getContext('2d');
    var status = this.dom.gifstatus;
    var settled = false, safety = null;
    function done() { if (settled) return true; settled = true; if (safety) { clearTimeout(safety); safety = null; } return false; }

    this._ensureGifWorker(function (worker) {
      var gif;
      try {
        gif = new window.GIF({ workers: 2, quality: 1, width: W, height: H,
          workerScript: worker, background: '#0b1320' });
      } catch (e) { if (!done()) onFail('GIF encoder unavailable.'); return; }
      gif.on('progress', function (p) { status.textContent = 'Encoding… ' + Math.round(p * 100) + '%'; });
      gif.on('error', function () { if (!done()) onFail('GIF encoding failed - try again.'); });
      gif.on('finished', function (blob) { if (!done()) onBlob(blob); });

      // Render the run in order so the trail builds incrementally; the synchronous
      // loop never repaints mid-way, so the visible canvas does not flash.
      var savedIdx = self.idx, added = 0;
      self._resetTrail();
      for (var i = 0; i < total; i++) {
        self._show(i);
        if (selSet[i]) {
          octx.clearRect(0, 0, W, H);
          try { octx.drawImage(self.dom.canvas, 0, 0, cw, ch, 0, 0, W, H); } catch (e) { continue; }
          gif.addFrame(octx, { copy: true, delay: (i === lastSel) ? delay * GIF_LAST_DWELL : delay });
          added++;
        }
      }
      self._show(savedIdx);   // restore the viewer
      if (added < 2) { if (!done()) onFail('Could not render enough frames.'); return; }
      safety = setTimeout(function () { if (!done()) onFail('GIF encoding timed out - try again.'); }, 90000);
      try { gif.render(); } catch (e) { if (!done()) onFail('GIF encoder failed to start.'); }
    });
  };

  // ---- poll for newer cycle ----
  EnsCentersViewer.prototype._schedulePoll = function () {
    clearTimeout(this._pollTimer);
    var self = this;
    this._pollTimer = setTimeout(function () { self._poll(); }, POLL_IDLE_MS);
  };
  EnsCentersViewer.prototype._poll = function () {
    var self = this;
    if (this.encoding) { this._schedulePoll(); return; }   // don't reload mid-encode
    this._fetchManifest().then(function (m) {
      self.manifest = m;
      var entry = self._modelEntry(self.model);
      if (!entry) return;
      // A pinned older run that has rolled off the retention window is gone from
      // R2 - fall back to following the latest so we never show a 404'd cycle.
      if (!self.followLatest && entry.cycles && entry.cycles.indexOf(self.loadedCycle) === -1) {
        self.followLatest = true;
      }
      self._buildRunSelect(entry, self.followLatest ? entry.latest : self.loadedCycle);
      // Only auto-advance to a fresh cycle when the user is following latest;
      // a user who picked a specific run stays on it.
      if (self.followLatest && entry.latest && entry.latest !== self.loadedCycle) {
        self._loadCycle(self.model, entry.latest);
      }
      // refresh the live observed-system feed too (when obs mode is on)
      if (self.obsOn && self._tracksAvailable() && !self.obsLoading) self._loadObs();
    }).catch(function () {}).then(function () { self._schedulePoll(); });
  };

  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('DOMContentLoaded', function () {
      var r = el('enscenters-viewer'); if (r) new EnsCentersViewer(r);
    });
  }
  if (typeof window !== 'undefined') window.EnsCentersViewer = EnsCentersViewer;
})();
