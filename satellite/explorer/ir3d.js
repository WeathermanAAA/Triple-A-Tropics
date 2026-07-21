/* 3D cloud tops — client-side DEM synthesized from the per-frame lossless BT
 * raster the emit pipeline already publishes ({product}/{stamp}/bt.png: u16
 * RG-packed deg C at 0.01 resolution, alpha = validity, equirectangular over
 * the manifest bt.bounds — same decode as bt_probe.js). Registered as a
 * MapLibre protocol so raster-dem sources can point at
 *   tatdem://<encodeURIComponent(productPath)>/<stamp>/<z>/<x>/<y>
 * and each 256x256 tile is Terrain-RGB encoded on the fly from a cloud-top
 * height PROXY: h_km = clamp((15 - btC) / 6.5, 0, 18) — a 288.15 K surface
 * reference lapsed at the standard 6.5 K/km. This is relief for depth
 * perception, NOT measured altitude; the cockpit's 3D chip says so on screen.
 *
 * The viewer must register() each product's bt descriptor + CDN base before
 * mounting a dem source — the protocol never fetches manifests itself.
 */
(function () {
  'use strict';

  var REG = {};        // productPath -> { bt: manifest bt descriptor, base }
  var CACHE = {};      // productPath|stamp -> { d: RGBA bytes, w, h }
  var ORDER = [];      // insertion order for LRU eviction
  var CAP = 10;        // decoded BT rasters retained (geo 2560x853 ~ 8.7 MB each)
  var PENDING = {};    // in-flight decodes (promise dedupe)

  var TILE = 256;
  var REF_C = 15.0;    // 288.15 K surface reference, deg C
  var LAPSE = 6.5;     // K/km
  var MAX_KM = 18;

  function register(productPath, bt, base) {
    if (productPath && bt) REG[productPath] = { bt: bt, base: base || '' };
  }
  function urlTemplate(productPath, stamp) {
    return 'tatdem://' + encodeURIComponent(productPath) + '/' + stamp + '/{z}/{x}/{y}';
  }

  function loadBT(productPath, stamp) {
    var reg = REG[productPath];
    if (!reg) return Promise.reject(new Error('tatdem: unregistered ' + productPath));
    var key = productPath + '|' + stamp;
    if (CACHE[key]) return Promise.resolve(CACHE[key]);
    if (PENDING[key]) return PENDING[key];
    var url = reg.base + reg.bt.path.replace('{t}', stamp);
    var p = new Promise(function (res, rej) {
      var im = new Image();
      im.crossOrigin = 'anonymous';
      im.onload = function () {
        try {
          var c = document.createElement('canvas');
          c.width = im.width; c.height = im.height;
          var g = c.getContext('2d', { willReadFrequently: true });
          g.drawImage(im, 0, 0);
          var cell = { d: g.getImageData(0, 0, im.width, im.height).data,
                       w: im.width, h: im.height };
          CACHE[key] = cell;
          ORDER.push(key);
          while (ORDER.length > CAP) delete CACHE[ORDER.shift()];
          res(cell);
        } catch (e) { rej(e); }
      };
      im.onerror = function () { rej(new Error('tatdem: bt.png failed ' + url)); };
      im.src = url;
    });
    PENDING[key] = p;
    var done = function () { delete PENDING[key]; };
    p.then(done, done);
    return p;
  }

  // packed u16 RG -> deg C; alpha 0 = nodata (NaN)
  function btAt(d, idx, scale, offset) {
    return d[idx + 3] === 0 ? NaN : (d[idx] * 256 + d[idx + 1]) * scale + offset;
  }

  // Synthesize one Terrain-RGB tile: invert webmercator XYZ per pixel to
  // lon/lat, bilinear-sample the BT grid (row 0 = north), map BT -> proxy
  // height, encode meters at the standard 0.1 m step. lon is constant per
  // column and lat per row, so the grid coordinates precompute to two 256-
  // element vectors and the hot loop is pure typed-array arithmetic.
  function synthTile(bt, cell, z, x, y) {
    var d = cell.d, w = cell.w, h = cell.h;
    var W = bt.bounds[0], S = bt.bounds[1], E = bt.bounds[2], N = bt.bounds[3];
    var scale = bt.scale, offset = bt.offset;
    var n = Math.pow(2, z);
    var img = new ImageData(TILE, TILE);
    var px = img.data;
    var c0 = new Int32Array(TILE), c1 = new Int32Array(TILE);
    var fc = new Float64Array(TILE), cOK = new Uint8Array(TILE);
    var i, lon, u;
    for (i = 0; i < TILE; i++) {
      lon = (x + (i + 0.5) / TILE) / n * 360 - 180;
      // antimeridian-crossing rasters (Himawari FD) carry an unwrapped E > 180
      if (E > 180 && lon < W && lon + 360 <= E + 1e-9) lon += 360;
      u = (lon - W) / (E - W) * (w - 1);
      if (u >= 0 && u <= w - 1) {
        cOK[i] = 1;
        c0[i] = Math.floor(u);
        c1[i] = Math.min(c0[i] + 1, w - 1);
        fc[i] = u - c0[i];
      }
    }
    var base = 100000;   // (0 m + 10000) / 0.1 — encoded ground level
    for (var r = 0; r < TILE; r++) {
      var mrc = Math.PI - 2 * Math.PI * (y + (r + 0.5) / TILE) / n;
      var lat = 180 / Math.PI * Math.atan(Math.sinh(mrc));
      var v = (N - lat) / (N - S) * (h - 1);
      var rowOK = v >= 0 && v <= h - 1;
      var r0 = 0, r1 = 0, fr = 0, r0w = 0, r1w = 0;
      if (rowOK) {
        r0 = Math.floor(v); r1 = Math.min(r0 + 1, h - 1); fr = v - r0;
        r0w = r0 * w; r1w = r1 * w;
      }
      var o = r * TILE * 4;
      for (i = 0; i < TILE; i++, o += 4) {
        var val = base;
        if (rowOK && cOK[i]) {
          var fcv = fc[i];
          var i00 = (r0w + c0[i]) * 4, i01 = (r0w + c1[i]) * 4;
          var i10 = (r1w + c0[i]) * 4, i11 = (r1w + c1[i]) * 4;
          var v00 = btAt(d, i00, scale, offset), v01 = btAt(d, i01, scale, offset);
          var v10 = btAt(d, i10, scale, offset), v11 = btAt(d, i11, scale, offset);
          var btC;
          if (v00 === v00 && v01 === v01 && v10 === v10 && v11 === v11) {
            var top = v00 + (v01 - v00) * fcv;
            var bot = v10 + (v11 - v10) * fcv;
            btC = top + (bot - top) * fr;
          } else {
            // validity edge: nearest neighbor, so one NaN corner does not
            // sink a whole bilinear cell to ground level
            btC = fr < 0.5 ? (fcv < 0.5 ? v00 : v01) : (fcv < 0.5 ? v10 : v11);
          }
          if (btC === btC) {
            var km = (REF_C - btC) / LAPSE;
            if (km < 0) km = 0; else if (km > MAX_KM) km = MAX_KM;
            val = ((km * 1000 + 10000) * 10 + 0.5) | 0;
          }
        }
        px[o] = (val >> 16) & 255;
        px[o + 1] = (val >> 8) & 255;
        px[o + 2] = val & 255;
        px[o + 3] = 255;
      }
    }
    return pngBytes(img);
  }

  function pngBytes(img) {
    return new Promise(function (res, rej) {
      var c = document.createElement('canvas');
      c.width = img.width; c.height = img.height;
      c.getContext('2d').putImageData(img, 0, 0);
      c.toBlob(function (b) {
        if (!b) { rej(new Error('tatdem: png encode failed')); return; }
        b.arrayBuffer().then(res, rej);
      }, 'image/png');
    });
  }

  function handler(params) {
    var m = /^tatdem:\/\/([^/]+)\/([^/]+)\/(\d+)\/(\d+)\/(\d+)$/.exec(params.url);
    if (!m) return Promise.reject(new Error('tatdem: bad url ' + params.url));
    var productPath = decodeURIComponent(m[1]);
    var reg = REG[productPath];
    if (!reg) return Promise.reject(new Error('tatdem: unregistered ' + productPath));
    var z = +m[3], x = +m[4], y = +m[5];
    return loadBT(productPath, m[2]).then(function (cell) {
      return synthTile(reg.bt, cell, z, x, y);
    }).then(function (buf) { return { data: buf }; });
  }

  if (typeof maplibregl !== 'undefined' && maplibregl.addProtocol)
    maplibregl.addProtocol('tatdem', handler);

  if (typeof window !== 'undefined')
    window.IR3D = { register: register, urlTemplate: urlTemplate };
})();
