/* objfix_sources.js — BT field extraction for the objective center/intensity
 * feature (browser-only; feeds satellite/explorer/objfix.js).
 *
 * Data paths (OBJFIX-METHODS.md §C, updated for the Himawari suite):
 *  - AL/EP: the GOES fd pyramid's per-frame calibrated bt.png (lossless u16,
 *    BTProbe's formula) — clean input.
 *  - WP: the himawari9 WPAC suite's per-frame calibrated AHI Band-13 bt.png
 *    (same u16 encoding, ~3.7 km) — clean input; per-frame FIRST-GUESS
 *    anchors still come from the floater manifest's box centers (the
 *    official-track anchor — NEVER chained fixes). Live BT is direct
 *    per-basin Band 13 (GOES ABI / Himawari AHI); MergIR + GridSat stay
 *    ARCHIVE-ONLY (~24 h latency) for the Time Machine / global composite.
 *  - Floater LUT fallback (WP only, until the box emits the himawari suite;
 *    + CP and any basin without a suite domain): the floater's rainbow_ir-
 *    colorized WebP frames with baked chrome. BT recovery = crop the
 *    render.py data rect + invert the rainbow_ir LUT, SELF-CALIBRATED per
 *    frame from the baked colorbar strip. DEGRADED-PRECISION input by
 *    construction — surfaced in every readout.
 *
 * Frame geometry (verified against tsr render.py + live graticule, ±2 px):
 *  - figure: 12 in × 12 in (square 12° floater box) -> WebP 1056×1056
 *  - axes rect [0.04, 0.04, 0.84, 0.90] fig-fraction; cartopy equal-aspect
 *    centers the square 12°×12° extent in that box -> drawn data rect
 *    x: 0.04..0.88 of width, y: 0.09..0.93 of height (0.84 square)
 *  - colorbar rect [0.905, 0.08, 0.016, 0.82] -> sampled at x=0.913,
 *    y 0.10..0.92 (top=+40 °C, bottom=−95 °C, linear)
 *  - display extent per frame = [cx−span/2, S, cx+span/2, N] where
 *    [W,S,E,N] are the manifest's (backdrop) bounds, cx=(W+E)/2,
 *    span=N−S: the backdrop widen keeps S/N + the lon center
 *    (floater_poller.py widen-to-aspect), and the display box is the
 *    BBOX_DEG=12 square.
 */
(function () {
  'use strict';

  var CDN = 'https://cdn.triple-a-tropics.com';
  var FD_MANIFEST = CDN + '/shadow/sat/goes19/fd/ir/latest_times.json';
  var WP_MANIFEST = CDN + '/shadow/sat/himawari9/wpac/ir/latest_times.json';
  var KELVIN = 273.15;

  // render.py layout fractions (see header)
  var LAYOUT = {
    dataX0: 0.04, dataX1: 0.88, dataY0: 0.09, dataY1: 0.93,
    cbarX: 0.913, cbarY0: 0.10, cbarY1: 0.92,
    cbarVmaxC: 40.0, cbarVminC: -95.0
  };

  function fetchJson(url) {
    return fetch(url + (url.indexOf('?') < 0 ? '?t=' + Date.now() : ''), { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + url); return r.json(); });
  }
  function loadImage(url) {
    return new Promise(function (res, rej) {
      var im = new Image();
      im.crossOrigin = 'anonymous';
      im.onload = function () { res(im); };
      im.onerror = function () { rej(new Error('image failed: ' + url)); };
      im.src = url;
    });
  }
  function imageData(im) {
    var c = document.createElement('canvas');
    c.width = im.naturalWidth; c.height = im.naturalHeight;
    var g = c.getContext('2d', { willReadFrequently: true });
    g.drawImage(im, 0, 0);
    return { data: g.getImageData(0, 0, c.width, c.height), canvas: c };
  }

  // ========================================================================
  // Suite bt.png sources (calibrated u16): GOES fd for AL/EP, himawari9
  // wpac AHI B13 for WP — one decoder, parameterized by manifest.
  // ========================================================================
  function FdSource(manifestUrl, qualityTag, diskName) {
    this.manifestUrl = manifestUrl || FD_MANIFEST;
    this.qualityTag = qualityTag || 'GOES fd calibrated BT (u16)';
    this.diskName = diskName || 'GOES-East';
    this.manifest = null;
    this._cache = {};   // stamp -> decoded full-domain field (subgrids cut per storm)
  }
  FdSource.prototype.load = function () {
    var self = this;
    return fetchJson(this.manifestUrl).then(function (m) { self.manifest = m; return m; });
  };
  FdSource.prototype.frames = function () {
    return this.manifest ? (this.manifest.times || []).map(function (t) {
      return { stamp: t, timeMs: stampMs(t) };
    }) : [];
  };
  // Decode one frame's bt.png and cut a subgrid around (lat, lon) ±halfDeg.
  FdSource.prototype.field = function (stamp, lat, lon, halfDeg) {
    var self = this, m = this.manifest, bt = m.bt;
    halfDeg = halfDeg || 4.0;
    var key = stamp;
    var p = this._cache[key]
      ? Promise.resolve(this._cache[key])
      : loadImage(CDN + '/shadow/' + bt.path.replace('{t}', stamp)).then(function (im) {
          var id = imageData(im).data;
          self._cache = {};              // hold ONE decoded frame (each ~5 MB)
          self._cache[key] = id;
          return id;
        });
    return p.then(function (id) {
      var W = bt.bounds[0], S = bt.bounds[1], E = bt.bounds[2], N = bt.bounds[3];
      // antimeridian-crossing domain (unwrapped E > 180): unwrap the probe lon
      if (E > 180 && lon < W && lon + 360 <= E + 1e-9) lon += 360;
      var nx = id.width, ny = id.height;
      var dLon = (E - W) / (nx - 1), dLat = (N - S) / (ny - 1);
      var j0 = Math.max(0, Math.floor((lon - halfDeg - W) / dLon));
      var j1 = Math.min(nx - 1, Math.ceil((lon + halfDeg - W) / dLon));
      var i0 = Math.max(0, Math.floor((N - (lat + halfDeg)) / dLat));
      var i1 = Math.min(ny - 1, Math.ceil((N - (lat - halfDeg)) / dLat));
      if (j1 <= j0 || i1 <= i0) throw new Error('storm outside the ' + self.diskName + ' domain');
      var nr = i1 - i0 + 1, nc = j1 - j0 + 1;
      var latArr = new Float64Array(nr), lonArr = new Float64Array(nc);
      var out = new Float64Array(nr * nc);
      for (var i = 0; i < nr; i++) latArr[i] = N - (i0 + i) * dLat;
      for (var j = 0; j < nc; j++) {
        var lj = W + (j0 + j) * dLon;
        lonArr[j] = lj > 180 ? lj - 360 : lj;   // wrap back for display/geodesy
      }
      // NOTE: `id` IS an ImageData (imageData(im).data) — its pixel buffer is
      // id.data. The original fd path read id.data.data (undefined) — a latent
      // TypeError that never fired live because every storm to date had a
      // floater; caught when the WP AHI-B13 path first exercised this decode.
      var d = id.data;
      for (i = 0; i < nr; i++) {
        for (j = 0; j < nc; j++) {
          var px = ((i0 + i) * nx + (j0 + j)) * 4;
          if (d[px + 3] === 0) { out[i * nc + j] = NaN; continue; }
          var c = (d[px] * 256 + d[px + 1]) * bt.scale + bt.offset;   // °C (BTProbe formula)
          out[i * nc + j] = c + KELVIN;
        }
      }
      return {
        latArr: latArr, lonArr: lonArr, bt: out, nr: nr, nc: nc,
        resKm: dLat * 111,
        inputQuality: self.qualityTag + ' · ~' + Math.round(dLat * 111) + ' km input',
        degraded: false,
        extent: [lonArr[0], latArr[nr - 1], lonArr[nc - 1], latArr[0]]
      };
    });
  };

  // WP: himawari9 wpac AHI B13 BT frames + per-frame OFFICIAL-TRACK anchors
  // from the floater manifest (the floater box center follows agency fixes;
  // ARCHER's penalty term anchors to it — never to its own prior fixes).
  function WpBtSource(slug) {
    FdSource.call(this, WP_MANIFEST,
      'Himawari-9 AHI B13 calibrated BT (u16)', 'Himawari WPAC');
    this.slug = slug || null;
    this._anchors = [];   // [{timeMs, lat, lon}] from the floater ir frames
  }
  WpBtSource.prototype = Object.create(FdSource.prototype);
  WpBtSource.prototype.constructor = WpBtSource;
  WpBtSource.prototype.load = function () {
    var self = this;
    var anchors = !this.slug ? Promise.resolve(null)
      : fetchJson(CDN + '/floaters/' + this.slug + '/manifest.json')
          .catch(function () { return null; });
    return Promise.all([FdSource.prototype.load.call(this), anchors])
      .then(function (r) {
        var b = r[1] && r[1].bands && r[1].bands.ir;
        self._anchors = (b && b.frames || []).filter(function (f) {
          return f.bounds && f.bounds.length === 4;
        }).map(function (f) {
          var ext = displayExtent(f.bounds);
          return { timeMs: Date.parse(f.t),
                   lat: (ext[1] + ext[3]) / 2, lon: (ext[0] + ext[2]) / 2 };
        });
        return r[0];
      });
  };
  WpBtSource.prototype.frames = function () {
    var self = this;
    return FdSource.prototype.frames.call(this).map(function (f) {
      var best = null, bd = 45 * 60e3;   // official anchor within ±45 min
      for (var k = 0; k < self._anchors.length; k++) {
        var d = Math.abs(self._anchors[k].timeMs - f.timeMs);
        if (d < bd) { bd = d; best = self._anchors[k]; }
      }
      if (best) { f.guessLat = best.lat; f.guessLon = best.lon; }
      return f;
    });
  };

  // ========================================================================
  // WP / floaters — rainbow_ir WebP inversion
  // ========================================================================
  function FloaterSource(slug) {
    this.slug = slug;
    this.manifest = null;
    this._lutBox = null;   // quantized RGB -> LUT row cache (per colorbar hash)
  }
  FloaterSource.prototype.load = function () {
    var self = this;
    return fetchJson(CDN + '/floaters/' + this.slug + '/manifest.json').then(function (m) {
      self.manifest = m;
      return m;
    });
  };
  FloaterSource.prototype.frames = function () {
    var b = this.manifest && this.manifest.bands && this.manifest.bands.ir;
    return (b && b.frames || []).filter(function (f) {
      return f.bounds && f.bounds.length === 4;   // pre-backdrop frames lack bounds
    }).map(function (f) {
      var ext = displayExtent(f.bounds);
      return { stamp: f.t, timeMs: Date.parse(f.t), key: f.key, bounds: f.bounds,
               // the anchored floater box center = the official-track-following
               // first guess for THIS frame (ARCHER's penalty anchors to it)
               guessLat: (ext[1] + ext[3]) / 2, guessLon: (ext[0] + ext[2]) / 2 };
    });
  };

  // per-frame display extent from the (backdrop) bounds — see header
  function displayExtent(bounds) {
    var W = bounds[0], S = bounds[1], E = bounds[2], N = bounds[3];
    var span = N - S;
    var cx = (W + E) / 2;
    return [cx - span / 2, S, cx + span / 2, N];   // [W,S,E,N]
  }

  // Build the 256-row LUT from the frame's own baked colorbar strip.
  FloaterSource.prototype._calibrateLUT = function (id) {
    var w = id.width, h = id.height, d = id.data;
    var x = Math.round(LAYOUT.cbarX * w);
    var y0 = LAYOUT.cbarY0 * h, y1 = LAYOUT.cbarY1 * h;
    var N = 256;
    var lut = new Uint8Array(N * 3), btC = new Float64Array(N);
    var span = LAYOUT.cbarVmaxC - LAYOUT.cbarVminC;
    var distinct = {};
    for (var k = 0; k < N; k++) {
      var fy = y0 + (k + 0.5) / N * (y1 - y0);
      var px = (Math.round(fy) * w + x) * 4;
      lut[k * 3] = d[px]; lut[k * 3 + 1] = d[px + 1]; lut[k * 3 + 2] = d[px + 2];
      btC[k] = LAYOUT.cbarVmaxC - (k + 0.5) / N * span;   // top = warm
      distinct[d[px] + ',' + d[px + 1] + ',' + d[px + 2]] = 1;
    }
    // sanity: a real colorbar has many distinct colors; a mis-located strip
    // (layout drift) reads flat background -> bail honestly.
    if (Object.keys(distinct).length < 24) {
      throw new Error('colorbar calibration failed — frame layout unexpected');
    }
    return { lut: lut, btC: btC };
  };

  // Quantized nearest-color box: 32^3 RGB bins -> {btC, dist}. Built once per
  // calibration; O(1) per pixel afterwards.
  FloaterSource.prototype._lutIndex = function (cal) {
    var Q = 32, B = 256 / Q;
    var box = { bt: new Float64Array(Q * Q * Q), d2: new Float64Array(Q * Q * Q) };
    var n = cal.btC.length;
    for (var r = 0; r < Q; r++) {
      for (var g = 0; g < Q; g++) {
        for (var b = 0; b < Q; b++) {
          var cr = r * B + B / 2, cg = g * B + B / 2, cb = b * B + B / 2;
          var best = Infinity, bi = 0;
          for (var k = 0; k < n; k++) {
            var dr = cr - cal.lut[k * 3], dg = cg - cal.lut[k * 3 + 1], db = cb - cal.lut[k * 3 + 2];
            var d2 = dr * dr + dg * dg + db * db;
            if (d2 < best) { best = d2; bi = k; }
          }
          var idx = (r * Q + g) * Q + b;
          box.bt[idx] = cal.btC[bi];
          box.d2[idx] = best;
        }
      }
    }
    return box;
  };

  FloaterSource.prototype.field = function (frame) {
    var self = this;
    return loadImage(CDN + '/' + frame.key).then(function (im) {
      var id = imageData(im).data;
      var w = id.width, h = id.height, d = id.data;
      var cal = self._calibrateLUT(id);
      if (!self._lutBox) self._lutBox = self._lutIndex(cal);
      var box = self._lutBox;
      var Q = 32;

      var x0 = Math.round(LAYOUT.dataX0 * w), x1 = Math.round(LAYOUT.dataX1 * w);
      var y0 = Math.round(LAYOUT.dataY0 * h), y1 = Math.round(LAYOUT.dataY1 * h);
      var nc = x1 - x0, nr = y1 - y0;
      var ext = displayExtent(frame.bounds);   // [W,S,E,N]
      var latArr = new Float64Array(nr), lonArr = new Float64Array(nc);
      var i, j;
      for (i = 0; i < nr; i++) latArr[i] = ext[3] - (i + 0.5) / nr * (ext[3] - ext[1]);
      for (j = 0; j < nc; j++) lonArr[j] = ext[0] + (j + 0.5) / nc * (ext[2] - ext[0]);

      // invert: nearest LUT color via the quantized box; pixels far from any
      // LUT color are chrome/coastline/graticule/watermark -> NaN, then
      // median-filled below (spec §C: contamination handling).
      var MAXD2 = 42 * 42 * 3;
      var bt = new Float64Array(nr * nc);
      for (i = 0; i < nr; i++) {
        for (j = 0; j < nc; j++) {
          var px = ((y0 + i) * w + (x0 + j)) * 4;
          var qi = ((d[px] >> 3) * Q + (d[px + 1] >> 3)) * Q + (d[px + 2] >> 3);
          bt[i * nc + j] = box.d2[qi] > MAXD2 ? NaN : box.bt[qi] + KELVIN;
        }
      }
      // median-fill masked pixels from their 5x5 neighborhood
      var filled = medianFill(bt, nr, nc);

      return {
        latArr: latArr, lonArr: lonArr, bt: filled.bt, nr: nr, nc: nc,
        resKm: (ext[3] - ext[1]) / nr * 111,
        inputQuality: 'rainbow_ir LUT inversion (colorized WebP) · DEGRADED PRECISION · ' +
          filled.maskedPct.toFixed(1) + '% chrome/coast pixels in-filled',
        degraded: true,
        extent: ext,
        cropRect: { x: x0, y: y0, w: nc, h: nr },
        image: im
      };
    });
  };

  function medianFill(bt, nr, nc) {
    var out = new Float64Array(bt);
    var masked = 0, total = nr * nc;
    for (var i = 0; i < nr; i++) {
      for (var j = 0; j < nc; j++) {
        if (!isNaN(bt[i * nc + j])) continue;
        masked++;
        var vals = [];
        for (var a = Math.max(0, i - 2); a <= Math.min(nr - 1, i + 2); a++) {
          for (var b = Math.max(0, j - 2); b <= Math.min(nc - 1, j + 2); b++) {
            var v = bt[a * nc + b];
            if (!isNaN(v)) vals.push(v);
          }
        }
        if (vals.length >= 5) {
          vals.sort(function (p, q) { return p - q; });
          out[i * nc + j] = vals[Math.floor(vals.length / 2)];
        }
      }
    }
    return { bt: out, maskedPct: 100 * masked / total };
  }

  // ========================================================================
  // storm discovery: live feed + floater manifest
  // ========================================================================
  function listStorms() {
    var feed = fetchJson(CDN + '/global_storms.geojson').catch(function () { return null; });
    var flt = fetchJson(CDN + '/floaters/manifest.json').catch(function () { return null; });
    // is the himawari9 wpac B13 suite live? (box emit) — decides the WP path
    var wp = fetchJson(WP_MANIFEST).then(function (m) {
      return (m && m.bt && m.times && m.times.length) ? true : false;
    }).catch(function () { return false; });
    return Promise.all([feed, flt, wp]).then(function (r) {
      var wpBtLive = r[2];
      var markers = {};
      if (r[0]) {
        (r[0].features || []).forEach(function (f) {
          var p = f.properties || {};
          if (p.kind !== 'active_marker') return;
          var c = f.geometry && f.geometry.coordinates;
          markers[String(p.storm_id || p.name)] = {
            id: p.storm_id, name: p.name || p.designation,
            vmax: p.current_intensity_kt, category: p.current_category,
            mslp: p.current_mslp_mb,
            lat: c ? c[1] : null, lon: c ? c[0] : null
          };
        });
      }
      var storms = [];
      if (r[1]) {
        (r[1].storms || []).forEach(function (s) {
          var mk = markers[String(s.id)] || {};
          var basin = String(s.basin || '').toUpperCase();
          // WP live BT = the himawari9 wpac AHI B13 raster (rainbow_ir LUT
          // inversion RETIRED for WP once the suite is on R2; the floater
          // path remains the honest fallback until the box emits, and for
          // basins with no suite domain)
          var src = (basin === 'WP' && wpBtLive) ? 'wp_bt' : 'floater';
          storms.push({
            slug: s.slug, id: s.id, name: s.name || mk.name, basin: basin,
            lat: mk.lat != null ? mk.lat : s.lat,
            lon: mk.lon != null ? mk.lon : s.lon,
            vmax: mk.vmax || 0, category: mk.category || '',
            source: src,
            domainID: basin === 'AL' ? 0 : 1,
            basinID: basin === 'AL' ? 0 : (basin === 'EP' || basin === 'CP' ? 2 : 1)
          });
        });
      }
      // AL/EP markers with no floater still get the fd path
      Object.keys(markers).forEach(function (k) {
        var mk = markers[k];
        var basin = (String(mk.id || '').replace(/^JTWC_/, '').slice(0, 2) || '').toUpperCase();
        if (storms.some(function (s) { return s.id === mk.id; })) return;
        if (basin !== 'AL' && basin !== 'EP' && basin !== 'CP') return;
        storms.push({
          slug: null, id: mk.id, name: mk.name, basin: basin,
          lat: mk.lat, lon: mk.lon, vmax: mk.vmax || 0, category: mk.category || '',
          source: 'fd',
          domainID: basin === 'AL' ? 0 : 1,
          basinID: basin === 'AL' ? 0 : 2
        });
      });
      return storms;
    });
  }

  function stampMs(s) {
    return Date.UTC(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8),
                    +s.slice(9, 11), +s.slice(11, 13), +s.slice(13, 15) || 0);
  }

  // point-in-land test over the shared ne_* countries geojson (ray casting) —
  // powers ADT's over-land suspension. Coarse (50m) is fine for this purpose.
  function makeLandTest(geo) {
    var polys = [];
    if (geo && geo.countries) {
      (geo.countries.features || []).forEach(function (f) {
        var g = f.geometry;
        if (!g) return;
        var add = function (rings) {
          var bb = [Infinity, Infinity, -Infinity, -Infinity];
          rings[0].forEach(function (c) {
            if (c[0] < bb[0]) bb[0] = c[0];
            if (c[1] < bb[1]) bb[1] = c[1];
            if (c[0] > bb[2]) bb[2] = c[0];
            if (c[1] > bb[3]) bb[3] = c[1];
          });
          polys.push({ rings: rings, bb: bb });
        };
        if (g.type === 'Polygon') add(g.coordinates);
        else if (g.type === 'MultiPolygon') g.coordinates.forEach(add);
      });
    }
    return function (lat, lon) {
      for (var p = 0; p < polys.length; p++) {
        var bb = polys[p].bb;
        if (lon < bb[0] || lon > bb[2] || lat < bb[1] || lat > bb[3]) continue;
        var rings = polys[p].rings;
        if (inRing(rings[0], lon, lat)) {
          var hole = false;
          for (var h = 1; h < rings.length; h++) {
            if (inRing(rings[h], lon, lat)) { hole = true; break; }
          }
          if (!hole) return true;
        }
      }
      return false;
    };
  }
  function inRing(ring, x, y) {
    var inside = false;
    for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      var xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
      if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  // Rasterize the land test to a small mask grid around (lat, lon) so it can
  // cross the worker boundary (functions can't). Polygons are bbox-prefiltered
  // — open-ocean storms usually intersect none.
  function buildLandMask(isLand, lat, lon, halfDeg, stepDeg) {
    if (!isLand) return null;
    halfDeg = halfDeg || 4.5; stepDeg = stepDeg || 0.05;
    var n = Math.round(2 * halfDeg / stepDeg) + 1;
    var mask = new Uint8Array(n * n);
    var latTop = lat + halfDeg, lonLeft = lon - halfDeg;
    for (var i = 0; i < n; i++) {
      for (var j = 0; j < n; j++) {
        if (isLand(latTop - i * stepDeg, lonLeft + j * stepDeg)) mask[i * n + j] = 1;
      }
    }
    return { latTop: latTop, lonLeft: lonLeft, step: stepDeg, n: n, mask: mask };
  }

  window.ObjFixSources = {
    FdSource: FdSource,
    WpBtSource: WpBtSource,
    FloaterSource: FloaterSource,
    listStorms: listStorms,
    makeLandTest: makeLandTest,
    buildLandMask: buildLandMask,
    displayExtent: displayExtent,
    LAYOUT: LAYOUT
  };
})();
