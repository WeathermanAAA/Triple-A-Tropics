/* explorer-data.js
   Data core for the TC track explorer (shadow). No DOM access, loadable in
   node for tests. All network access goes through getJSON, which never throws
   and resolves null on any failure. */
(function () {
  'use strict';

  var BASE = 'https://cdn.triple-a-tropics.com/explorer/v1';

  /* min peak wind (kt) per category filter key */
  var CAT_MIN = { td: 0, ts: 34, c1: 64, c2: 83, c3: 96, c4: 113, c5: 137 };
  var WIND_LABEL = { kt: 'kt', mph: 'mph', kmh: 'km/h' };

  var _manifest = null;
  var _basin = null;
  var _catalogs = {};        /* basin -> catalog */
  var _tracks = {};          /* storm i -> points, current basin only */
  var _loaded = {};          /* decade -> true, current basin only */
  var _fails = {};           /* "basin_decade" -> failed fetch count, per page load */
  var _places = null;
  var _placesPromise = null;

  function getJSON(url) {
    try {
      return fetch(url + '?t=' + Math.floor(Date.now() / 6e5), { cache: 'no-store' })
        .then(function (r) {
          if (!r || !r.ok) return null;
          return r.json().catch(function () { return null; });
        })
        .catch(function () { return null; });
    } catch (e) {
      return Promise.resolve(null);
    }
  }

  function num(x) {
    return (x === undefined || x === null || x === '' || isNaN(+x)) ? null : +x;
  }

  function p2(n) { return (n < 10 ? '0' : '') + n; }

  function basinDecades() {
    var b = _manifest && _manifest.basins && _manifest.basins[_basin];
    return (b && b.decades) || [];
  }

  /* ---- loading ---- */

  function init() {
    if (_manifest) return Promise.resolve(_manifest);
    return getJSON(BASE + '/explorer_manifest.json').then(function (m) {
      _manifest = m || null;
      return _manifest;
    });
  }

  function setBasin(b) {
    _basin = b;
    _tracks = {};
    _loaded = {};
    if (_catalogs[b]) return Promise.resolve(_catalogs[b]);
    return getJSON(BASE + '/catalog_' + b + '.json').then(function (c) {
      if (c) _catalogs[b] = c;
      return c || null;
    });
  }

  function requiredDecades(y0, y1) {
    var decs = basinDecades();
    var a = num(y0), b = num(y1);
    if (!decs.length || a === null || b === null) return [];
    if (a > b) { var t = a; a = b; b = t; }
    var d0 = Math.floor(a / 10) * 10, d1 = Math.floor(b / 10) * 10;
    var out = [];
    for (var d = d0; d <= d1; d += 10) if (decs.indexOf(d) >= 0) out.push(d);
    return out;
  }

  function ensureTracks(decades) {
    var b = _basin;
    var decs = basinDecades();
    var want = [];
    for (var k = 0; k < (decades || []).length; k++) {
      var d = decades[k];
      /* negative cache: a decade that failed twice (initial fetch + one
         retry) is not fetched again this page load */
      if (!_loaded[d] && decs.indexOf(d) >= 0 && want.indexOf(d) < 0 &&
          (_fails[b + '_' + d] || 0) < 2) want.push(d);
    }
    if (!want.length) return Promise.resolve(0);
    var jobs = [];
    for (var j = 0; j < want.length; j++) {
      jobs.push((function (dec) {
        return getJSON(BASE + '/tracks_' + b + '_' + dec + '.json').then(function (t) {
          if (!t || !t.tracks) {
            _fails[b + '_' + dec] = (_fails[b + '_' + dec] || 0) + 1;
            return 0;
          }
          if (_basin !== b) return 0;
          for (var id in t.tracks) {
            if (t.tracks.hasOwnProperty(id)) _tracks[id] = t.tracks[id];
          }
          _loaded[dec] = true;
          return 1;
        });
      })(want[j]));
    }
    return Promise.all(jobs).then(function (res) {
      var n = 0;
      for (var i = 0; i < res.length; i++) n += res[i];
      return n;
    });
  }

  function trackOf(i) { return _tracks[i] || null; }

  function tracksReady(decades) {
    var decs = basinDecades();
    for (var k = 0; k < (decades || []).length; k++) {
      var d = decades[k];
      if (decs.indexOf(d) >= 0 && !_loaded[d]) return false;
    }
    return true;
  }

  /* ---- filtering ---- */

  function filter(f) {
    f = f || {};
    var cat = _catalogs[_basin];
    if (!cat || !cat.storms) return [];
    var y0 = num(f.y0), y1 = num(f.y1), m0 = num(f.m0), m1 = num(f.m1);
    var minW = (f.mc && CAT_MIN.hasOwnProperty(f.mc)) ? CAT_MIN[f.mc] : 0;
    var useMonth = m0 !== null && m1 !== null && !(m0 === 1 && m1 === 12);
    var qq = f.q ? String(f.q).replace(/^\s+|\s+$/g, '').toUpperCase() : '';
    var out = [];
    for (var k = 0; k < cat.storms.length; k++) {
      var s = cat.storms[k];
      if (y0 !== null && s.season < y0) continue;
      if (y1 !== null && s.season > y1) continue;
      if (minW > 0 && !(s.peak != null && s.peak >= minW)) continue;
      if (useMonth) {
        if (!s.t0) continue;
        var mo = parseInt(s.t0.slice(5, 7), 10);
        var mok = (m0 <= m1) ? (mo >= m0 && mo <= m1) : (mo >= m0 || mo <= m1);
        if (!mok) continue;
      }
      if (qq) {
        var hit = (s.name || '').toUpperCase().indexOf(qq) >= 0 ||
                  String(s.season) === qq ||
                  (s.atcf || '').toUpperCase() === qq;
        if (!hit) continue;
      }
      out.push(s.i);
    }
    return out;
  }

  /* ---- geometry ---- */

  function haversineKm(lat1, lon1, lat2, lon2) {
    var rad = Math.PI / 180;
    var dLat = (lat2 - lat1) * rad, dLon = (lon2 - lon1) * rad;
    var a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(lat1 * rad) * Math.cos(lat2 * rad) *
            Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return 12742 * Math.asin(Math.min(1, Math.sqrt(a)));
  }

  function radiusQuery(lat, lon, km, ids) {
    var out = [];
    for (var k = 0; k < (ids || []).length; k++) {
      var i = ids[k];
      var pts = _tracks[i];
      if (!pts) continue;
      var best = null, bestD = Infinity;
      for (var j = 0; j < pts.length; j++) {
        var p = pts[j];
        /* tracks carry unwrapped longitudes, compare against lon and lon+-360 */
        var d = Math.min(
          haversineKm(lat, lon, p[1], p[2]),
          haversineKm(lat, lon, p[1], p[2] - 360),
          haversineKm(lat, lon, p[1], p[2] + 360)
        );
        if (d < bestD) { bestD = d; best = p; }
      }
      if (best && bestD <= km) {
        out.push({ i: i, dist: bestD, fix: best, wind: best[3] == null ? null : best[3] });
      }
    }
    out.sort(function (a, b) { return a.dist - b.dist; });
    return out;
  }

  /* ---- location parsing ---- */

  function llLabel(lat, lon) {
    var r = function (x) { return Math.round(Math.abs(x) * 100) / 100; };
    return r(lat) + (lat < 0 ? 'S' : 'N') + ', ' + r(lon) + (lon < 0 ? 'W' : 'E');
  }

  function parseLoc(str, places) {
    if (str === undefined || str === null) return null;
    var s = String(str).replace(/^\s+|\s+$/g, '');
    if (!s) return null;
    var la, lo;
    /* "25.5N 80.1W" or "17N 155W" */
    var m = s.match(/^(\d+(?:\.\d+)?)\s*([NS])[\s,]*(\d+(?:\.\d+)?)\s*([EW])$/i);
    if (m) {
      la = parseFloat(m[1]) * (m[2].toUpperCase() === 'S' ? -1 : 1);
      lo = parseFloat(m[3]) * (m[4].toUpperCase() === 'W' ? -1 : 1);
      if (Math.abs(la) <= 90) return { lat: la, lon: lo, label: llLabel(la, lo) };
      return null;
    }
    /* "25.5, -80.1" */
    m = s.match(/^(-?\d+(?:\.\d+)?)\s*[,\s]\s*(-?\d+(?:\.\d+)?)$/);
    if (m) {
      la = parseFloat(m[1]);
      lo = parseFloat(m[2]);
      if (Math.abs(la) <= 90) return { lat: la, lon: lo, label: llLabel(la, lo) };
      return null;
    }
    /* place-name prefix, gazetteer is sorted by population desc */
    var pl = places || _places;
    if (pl) {
      var q = s.toLowerCase();
      for (var k = 0; k < pl.length; k++) {
        var name = String(pl[k][0]);
        if (name.toLowerCase().indexOf(q) === 0) {
          return { lat: pl[k][2], lon: pl[k][3], label: name + ', ' + pl[k][1] };
        }
      }
    }
    return null;
  }

  function loadGazetteer() {
    if (_places) return Promise.resolve(_places);
    if (_placesPromise) return _placesPromise;
    _placesPromise = getJSON(BASE + '/gazetteer.json').then(function (g) {
      _placesPromise = null;
      if (g && g.places) _places = g.places;
      return _places;
    });
    return _placesPromise;
  }

  /* ---- categories and units ---- */

  function catFromWind(w) {
    var x = +w;
    if (w === undefined || w === null || isNaN(x)) return 'TD';
    if (x < 34) return 'TD';
    if (x < 64) return 'TS';
    if (x < 83) return 'C1';
    if (x < 96) return 'C2';
    if (x < 113) return 'C3';
    if (x < 137) return 'C4';
    return 'C5';
  }

  function convWind(kt, u) {
    if (u === 'mph') return kt * 1.15078;
    if (u === 'kmh') return kt * 1.852;
    return kt;
  }

  function convPres(mb, u) {
    if (u === 'inhg') return mb * 0.02953;
    return mb;
  }

  function fmtWind(kt, u) {
    if (kt === undefined || kt === null || isNaN(+kt)) return '';
    return Math.round(convWind(+kt, u)) + ' ' + (WIND_LABEL[u] || 'kt');
  }

  function fmtPres(mb, u) {
    if (mb === undefined || mb === null || isNaN(+mb)) return '';
    if (u === 'inhg') return (+mb * 0.02953).toFixed(2) + ' inHg';
    return Math.round(+mb) + ' mb';
  }

  /* ---- timestamps ---- */

  function tsRaw(t) {
    if (t instanceof Date) {
      return '' + t.getUTCFullYear() + p2(t.getUTCMonth() + 1) + p2(t.getUTCDate()) +
             p2(t.getUTCHours()) + p2(t.getUTCMinutes());
    }
    return String(t);
  }

  function tsParse(t) {
    var s = String(t);
    return new Date(Date.UTC(
      +s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8),
      +s.slice(8, 10) || 0, +s.slice(10, 12) || 0));
  }

  function tsFmt(t) {
    var s = tsRaw(t);
    var out = s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6, 8) + ' ' + s.slice(8, 10);
    var mi = s.slice(10, 12);
    if (mi && mi !== '00') out += ':' + mi;
    return out + 'Z';
  }

  function tsIso(t) {
    var s = tsRaw(t);
    return s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6, 8) + 'T' +
           s.slice(8, 10) + ':' + s.slice(10, 12) + 'Z';
  }

  /* ---- url state ---- */

  var STR_KEYS = { b: 1, mc: 1, q: 1, u: 1, up: 1, pal: 1, dens: 1 };
  var INT_KEYS = { y0: 1, y1: 1, m0: 1, m1: 1, sel: 1 };

  function urlRead() {
    var qs = '';
    try { qs = String(location.search || ''); } catch (e) { return {}; }
    if (qs.charAt(0) === '?') qs = qs.slice(1);
    var st = {};
    if (!qs) return st;
    var parts = qs.split('&');
    for (var k = 0; k < parts.length; k++) {
      var eq = parts[k].indexOf('=');
      if (eq < 0) continue;
      var key, val;
      try {
        key = decodeURIComponent(parts[k].slice(0, eq));
        val = decodeURIComponent(parts[k].slice(eq + 1));
      } catch (e2) { continue; }
      if (STR_KEYS[key]) {
        st[key] = val;
      } else if (INT_KEYS[key]) {
        var n = parseInt(val, 10);
        if (isFinite(n)) st[key] = n;
      } else if (key === 'loc') {
        var a = val.split(',');
        if (a.length === 3) {
          var lla = num(a[0]), llo = num(a[1]), lkm = num(a[2]);
          if (lla !== null && llo !== null && lkm !== null) st.loc = { lat: lla, lon: llo, km: lkm };
        }
      } else if (key === 'pin') {
        var ps = val.split(','), pins = [];
        for (var j = 0; j < ps.length; j++) {
          var pi = parseInt(ps[j], 10);
          if (isFinite(pi)) pins.push(pi);
        }
        if (pins.length) st.pin = pins;
      } else if (key === 'v') {
        var vv = val.split(',');
        if (vv.length === 3) {
          var vlo = num(vv[0]), vla = num(vv[1]), vz = num(vv[2]);
          if (vlo !== null && vla !== null && vz !== null) st.v = { lon: vlo, lat: vla, zoom: vz };
        }
      }
    }
    return st;
  }

  function rnd(x, p) {
    var f = Math.pow(10, p);
    return String(Math.round(x * f) / f);
  }

  var URL_ORDER = ['b', 'y0', 'y1', 'mc', 'm0', 'm1', 'q', 'loc', 'sel', 'pin', 'u', 'up', 'pal', 'dens', 'v'];

  function urlWrite(state) {
    state = state || {};
    var parts = [];
    for (var k = 0; k < URL_ORDER.length; k++) {
      var key = URL_ORDER[k], v = state[key], out;
      if (v === undefined || v === null || v === '') continue;
      if (key === 'loc') {
        out = (typeof v === 'string') ? v :
              rnd(v.lat, 2) + ',' + rnd(v.lon, 2) + ',' + Math.round(v.km);
      } else if (key === 'pin') {
        out = (typeof v === 'string') ? v : v.join(',');
        if (!out) continue;
      } else if (key === 'v') {
        out = (typeof v === 'string') ? v :
              rnd(v.lon, 2) + ',' + rnd(v.lat, 2) + ',' + rnd(v.zoom, 2);
      } else if (INT_KEYS[key]) {
        out = String(Math.round(v));
      } else {
        out = String(v);
      }
      parts.push(key + '=' + encodeURIComponent(out));
    }
    try {
      history.replaceState(null, '', location.pathname + (parts.length ? '?' + parts.join('&') : ''));
    } catch (e) {}
  }

  /* ---- exports ---- */

  function csvCell(v) {
    var s = String(v);
    if (/[",\n]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
    return s;
  }

  function stormIndex(cat) {
    var byI = {};
    if (cat && cat.storms) {
      for (var k = 0; k < cat.storms.length; k++) byI[cat.storms[k].i] = cat.storms[k];
    }
    return byI;
  }

  function exportCSV(ids, catalog, tracksMap, units) {
    ids = ids || [];
    if (ids.length > 2000) return null;
    var cat = catalog || _catalogs[_basin];
    if (!cat) return null;
    var u = (units && units.u) || 'kt';
    var up = (units && units.up) || 'mb';
    var byI = stormIndex(cat);
    var rows = ['sid,name,season,time,lat,lon,wind_' + (u === 'kt' || u === 'mph' || u === 'kmh' ? u : 'kt') +
                ',pres_' + (up === 'inhg' ? 'inhg' : 'mb') + ',category'];
    for (var a = 0; a < ids.length; a++) {
      var s = byI[ids[a]];
      if (!s) continue;
      var pts = (tracksMap && tracksMap[ids[a]]) || _tracks[ids[a]];
      if (!pts) continue;
      for (var j = 0; j < pts.length; j++) {
        var p = pts[j];
        rows.push([
          csvCell(s.sid), csvCell(s.name), s.season, tsIso(p[0]), p[1], p[2],
          p[3] == null ? '' : Math.round(convWind(p[3], u)),
          p[4] == null ? '' : (up === 'inhg' ? (p[4] * 0.02953).toFixed(2) : Math.round(p[4])),
          catFromWind(p[3])
        ].join(','));
      }
    }
    return rows.join('\n') + '\n';
  }

  function exportGeoJSON(ids) {
    ids = ids || [];
    if (ids.length > 2000) return null;
    var byI = stormIndex(_catalogs[_basin]);
    var feats = [];
    for (var a = 0; a < ids.length; a++) {
      var s = byI[ids[a]];
      var pts = _tracks[ids[a]];
      if (!s || !pts) continue;
      var coords = [];
      for (var j = 0; j < pts.length; j++) coords.push([pts[j][2], pts[j][1]]);
      feats.push({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: coords },
        properties: { sid: s.sid, name: s.name, season: s.season, peak: s.peak, pres: s.pres, ace: s.ace }
      });
    }
    return JSON.stringify({ type: 'FeatureCollection', features: feats });
  }

  /* ---- public api ---- */

  var XPData = {
    init: init,
    manifest: function () { return _manifest; },
    setBasin: setBasin,
    catalog: function () { return _catalogs[_basin] || null; },
    basin: function () { return _basin; },
    requiredDecades: requiredDecades,
    ensureTracks: ensureTracks,
    trackOf: trackOf,
    tracksReady: tracksReady,
    filter: filter,
    radiusQuery: radiusQuery,
    haversineKm: haversineKm,
    parseLoc: parseLoc,
    loadGazetteer: loadGazetteer,
    catFromWind: catFromWind,
    convWind: convWind,
    convPres: convPres,
    fmtWind: fmtWind,
    fmtPres: fmtPres,
    tsParse: tsParse,
    tsFmt: tsFmt,
    urlRead: urlRead,
    urlWrite: urlWrite,
    exportCSV: exportCSV,
    exportGeoJSON: exportGeoJSON
  };

  var root = (typeof window !== 'undefined') ? window :
             (typeof globalThis !== 'undefined') ? globalThis :
             (typeof self !== 'undefined') ? self : {};
  root.XPData = XPData;
  if (typeof module !== 'undefined' && module.exports) module.exports = XPData;
})();
