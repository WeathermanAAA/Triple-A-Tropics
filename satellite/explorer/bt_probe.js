/* Shared value-probe: reads REAL brightness temperature from the calibrated BT
 * data raster emitted beside the tiles (NOT the colorized PNG). Given a manifest
 * `bt` descriptor {path,scale,offset,dims,bounds,encoding}, it loads a frame's
 * BT PNG into an offscreen canvas once and samples deg C / K at any lon/lat.
 * Reused by the tiled viewer AND every compare pane (one component, no drift).
 */
(function () {
  'use strict';

  function BTProbe(bt, base) {
    this.bt = bt;                 // manifest.bt descriptor
    this.base = base || '';       // R2/CDN base to join with bt.path
    this.W = bt.bounds[0]; this.S = bt.bounds[1];
    this.E = bt.bounds[2]; this.N = bt.bounds[3];
    this._cache = {};             // stamp -> {data:Uint8ClampedArray, w, h}
    this._order = [];             // insertion order for LRU-ish eviction
    this._cap = 16;               // bound retained BT ImageData (each ~few MB)
  }
  var PP = BTProbe.prototype;

  PP.url = function (stamp) { return this.base + this.bt.path.replace('{t}', stamp); };

  // Load (once, cached) a frame's BT raster into an offscreen ImageData.
  PP.load = function (stamp) {
    var self = this;
    if (this._cache[stamp]) return Promise.resolve(this._cache[stamp]);
    return new Promise(function (res, rej) {
      var im = new Image(); im.crossOrigin = 'anonymous';
      im.onload = function () {
        var c = document.createElement('canvas');
        c.width = im.width; c.height = im.height;
        var g = c.getContext('2d', { willReadFrequently: true });
        g.drawImage(im, 0, 0);
        self._cache[stamp] = { data: g.getImageData(0, 0, im.width, im.height).data,
                               w: im.width, h: im.height };
        self._order.push(stamp);
        while (self._order.length > self._cap) {        // bound retained memory
          var old = self._order.shift();
          if (old !== stamp) delete self._cache[old];
        }
        res(self._cache[stamp]);
      };
      im.onerror = function (e) { rej(e); };
      im.src = self.url(stamp);
    });
  };

  // Sample BT (deg C) at lon/lat for a loaded stamp, or null (off-data / not loaded).
  // Antimeridian-crossing rasters (Himawari full disk) carry an UNWRAPPED east
  // bound (E > 180): probe longitudes west of W unwrap by +360 to match.
  PP.sample = function (stamp, lon, lat) {
    var c = this._cache[stamp];
    if (!c) return null;
    if (this.E > 180 && lon < this.W && lon + 360 <= this.E + 1e-9) lon += 360;
    if (lon < this.W || lon > this.E || lat < this.S || lat > this.N) return null;
    var col = Math.round((lon - this.W) / (this.E - this.W) * (c.w - 1));
    var row = Math.round((this.N - lat) / (this.N - this.S) * (c.h - 1));
    col = Math.max(0, Math.min(c.w - 1, col));
    row = Math.max(0, Math.min(c.h - 1, row));
    var i = (row * c.w + col) * 4, d = c.data;
    if (d[i + 3] === 0) return null;   // nodata alpha
    return (d[i] * 256 + d[i + 1]) * this.bt.scale + this.bt.offset;  // deg C
  };

  PP.fmt = function (btC) {
    if (btC == null) return '—';
    return btC.toFixed(1) + ' °C · ' + (btC + 273.15).toFixed(1) + ' K';
  };

  if (typeof window !== 'undefined') window.BTProbe = BTProbe;
  if (typeof module !== 'undefined' && module.exports) module.exports = { BTProbe: BTProbe };
})();
