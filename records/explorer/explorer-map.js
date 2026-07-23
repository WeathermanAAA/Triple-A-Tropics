/* explorer-map.js
   Map layer for the TC track explorer (shadow). Wraps the vendored map
   library behind window.XPMap: dark inline basemap style (Natural Earth
   GeoJSON, no glyphs, no text layers), track/fix rendering from XPData,
   selection highlight with landfall markers, radius circle, density image
   underlay, and a single hover popup. One map per page (singleton). */
(function () {
  'use strict';

  var BASE = 'https://cdn.triple-a-tropics.com/explorer/v1';

  var PALETTES = {
    /* matches TATRecords.SSHS_COLORS */
    std: { TD: '#3fa4ff', TS: '#46c56a', C1: '#ffe14d', C2: '#ff9a2f', C3: '#f5333c', C4: '#e33ad4', C5: '#b03bff' },
    /* Okabe-Ito, colorblind-safe */
    cb: { TD: '#999999', TS: '#56B4E9', C1: '#009E73', C2: '#F0E442', C3: '#E69F00', C4: '#D55E00', C5: '#CC79A7' }
  };

  var BASIN_VIEWS = {
    al: { lon: -55, lat: 27, zoom: 2.8 },
    ep: { lon: -125, lat: 16, zoom: 2.8 },
    wp: { lon: 140, lat: 18, zoom: 2.8 }
  };

  var EMPTY = { type: 'FeatureCollection', features: [] };

  var _map = null;
  var _popup = null;
  var _opts = {};
  var _detail = false;
  var _palette = PALETTES.std;
  var _selId = null;
  var _densityKey = null;
  var _lastError = null;

  /* ---- helpers ---- */

  function stormsById() {
    var cat = window.XPData && window.XPData.catalog && window.XPData.catalog();
    var byI = {};
    if (cat && cat.storms) {
      for (var k = 0; k < cat.storms.length; k++) byI[cat.storms[k].i] = cat.storms[k];
    }
    return byI;
  }

  function peakColor(storm, palette) {
    var cat = window.XPData.catFromWind(storm ? storm.peak : null);
    return palette[cat] || palette.TD;
  }

  function baseStyle() {
    return {
      version: 8,
      sources: {
        'xp-land-src': { type: 'geojson', data: '/ne_50m_admin_0_countries.geojson' },
        'xp-coast-src': { type: 'geojson', data: '/ne_50m_coastline.geojson' },
        'xp-borders-src': { type: 'geojson', data: '/ne_50m_admin_0_boundary_lines_land.geojson' }
      },
      layers: [
        { id: 'xp-bg', type: 'background', paint: { 'background-color': '#10161d' } },
        { id: 'xp-land', type: 'fill', source: 'xp-land-src', paint: { 'fill-color': '#232a33' } },
        { id: 'xp-coast', type: 'line', source: 'xp-coast-src',
          paint: { 'line-color': '#566274', 'line-width': 0.6 } },
        { id: 'xp-borders', type: 'line', source: 'xp-borders-src',
          paint: { 'line-color': '#3a4050', 'line-width': 0.5 } }
      ]
    };
  }

  function addDataLayers(map) {
    map.addSource('xp-tracks', { type: 'geojson', data: EMPTY });
    map.addSource('xp-pin', { type: 'geojson', data: EMPTY });
    map.addSource('xp-sel', { type: 'geojson', data: EMPTY });
    map.addSource('xp-lf', { type: 'geojson', data: EMPTY });
    map.addSource('xp-fixes', { type: 'geojson', data: EMPTY });
    map.addSource('xp-radius', { type: 'geojson', data: EMPTY });

    map.addLayer({
      id: 'xp-tracks', type: 'line', source: 'xp-tracks',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': ['get', 'color'], 'line-width': 1.3, 'line-opacity': 0.85 }
    });
    /* pinned storms: white casing under a category-colored core line */
    map.addLayer({
      id: 'xp-pin-casing', type: 'line', source: 'xp-pin',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#ffffff', 'line-width': 4.5, 'line-opacity': 0.85 }
    });
    map.addLayer({
      id: 'xp-pin-line', type: 'line', source: 'xp-pin',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': ['get', 'color'], 'line-width': 2 }
    });
    map.addLayer({
      id: 'xp-sel-halo', type: 'line', source: 'xp-sel',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#ffffff', 'line-width': 6.5, 'line-opacity': 0.85 }
    });
    map.addLayer({
      id: 'xp-sel', type: 'line', source: 'xp-sel',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': ['get', 'color'], 'line-width': 3.5 }
    });
    map.addLayer({
      id: 'xp-fixes', type: 'circle', source: 'xp-fixes',
      paint: {
        'circle-radius': 3, 'circle-color': ['get', 'color'],
        'circle-stroke-color': '#10161d', 'circle-stroke-width': 0.8, 'circle-opacity': 0.95
      }
    });
    /* landfall markers, selected storm only: 11px double-ring circles */
    map.addLayer({
      id: 'xp-lf-outer', type: 'circle', source: 'xp-lf',
      paint: {
        'circle-radius': 5.5, 'circle-color': '#10161d', 'circle-opacity': 0.9,
        'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.4
      }
    });
    map.addLayer({
      id: 'xp-lf-inner', type: 'circle', source: 'xp-lf',
      paint: {
        'circle-radius': 2.2, 'circle-color': 'rgba(0,0,0,0)',
        'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.2
      }
    });
    map.addLayer({
      id: 'xp-radius', type: 'fill', source: 'xp-radius',
      paint: { 'fill-color': '#5dd3ff', 'fill-opacity': 0.08 }
    });
    map.addLayer({
      id: 'xp-radius-line', type: 'line', source: 'xp-radius',
      paint: { 'line-color': '#5dd3ff', 'line-width': 1.2, 'line-opacity': 0.9 }
    });
  }

  function setData(sourceId, fc) {
    if (!_map) return;
    var src = _map.getSource(sourceId);
    if (src) src.setData(fc || EMPTY);
  }

  function popupContent(rows) {
    var box = document.createElement('div');
    for (var k = 0; k < rows.length; k++) {
      var lab = rows[k][0], val = rows[k][1];
      if (lab === undefined || lab === null || lab === '') {
        /* header row: [null, text] renders one emphasized line */
        var hd = document.createElement('div');
        hd.className = 'xp-pop-head';
        hd.textContent = (val === undefined || val === null) ? '' : String(val);
        box.appendChild(hd);
        continue;
      }
      var row = document.createElement('div');
      row.className = 'xp-pop-row';
      var ks = document.createElement('span');
      ks.className = 'xp-pop-k';
      ks.textContent = String(lab);
      row.appendChild(ks);
      var vs = document.createElement('span');
      vs.className = 'xp-pop-v';
      vs.textContent = (val === undefined || val === null) ? '' : String(val);
      row.appendChild(vs);
      box.appendChild(row);
    }
    return box;
  }

  function hidePopup() {
    if (_popup) _popup.remove();
    if (_map) _map.getCanvas().style.cursor = '';
  }

  function handleHover(e) {
    if (!_map || !e.features || !e.features.length) return;
    var props = e.features[0].properties || {};
    var rows = null;
    if (typeof _opts.hoverText === 'function') {
      try { rows = _opts.hoverText(props); } catch (err) { rows = null; }
    }
    if (!rows || !rows.length) { hidePopup(); return; }
    _map.getCanvas().style.cursor = 'pointer';
    _popup.setLngLat(e.lngLat).setDOMContent(popupContent(rows)).addTo(_map);
  }

  function bindEvents(map) {
    map.on('mousemove', 'xp-fixes', function (e) { handleHover(e); });
    map.on('mouseleave', 'xp-fixes', hidePopup);
    map.on('mousemove', 'xp-tracks', function (e) {
      if (_detail) return; /* detail mode hovers the fix points instead */
      handleHover(e);
    });
    map.on('mouseleave', 'xp-tracks', function () { if (!_detail) hidePopup(); });

    function clicked(e) {
      if (!e.features || !e.features.length) return;
      var i = Number(e.features[0].properties.i);
      if (isFinite(i) && typeof _opts.onClickStorm === 'function') {
        try { _opts.onClickStorm(i); } catch (err) {}
      }
    }
    map.on('click', 'xp-tracks', clicked);
    map.on('click', 'xp-fixes', clicked);
  }

  /* great-circle circle polygon, longitudes kept continuous (never wrapped) */
  function circleRing(lat, lon, km) {
    var rad = Math.PI / 180;
    var d = km / 6371;
    var la1 = lat * rad, lo1 = lon * rad;
    var ring = [];
    for (var k = 0; k <= 72; k++) {
      var brg = (k / 72) * 2 * Math.PI;
      var la2 = Math.asin(Math.sin(la1) * Math.cos(d) +
                          Math.cos(la1) * Math.sin(d) * Math.cos(brg));
      var lo2 = lo1 + Math.atan2(
        Math.sin(brg) * Math.sin(d) * Math.cos(la1),
        Math.cos(d) - Math.sin(la1) * Math.sin(la2));
      ring.push([lo2 / rad, la2 / rad]);
    }
    return ring;
  }

  /* ---- public api ---- */

  function create(containerEl, opts) {
    if (typeof window === 'undefined' || !window.maplibregl) {
      return Promise.reject(new Error('map library unavailable'));
    }
    _opts = opts || {};
    var view = _opts.view || BASIN_VIEWS[_opts.basin] || { lon: 0, lat: 20, zoom: 1.6 };
    return new Promise(function (resolve, reject) {
      var map;
      try {
        map = new window.maplibregl.Map({
          container: containerEl,
          style: baseStyle(),
          center: [view.lon, view.lat],
          zoom: view.zoom,
          renderWorldCopies: true,
          attributionControl: false
        });
      } catch (e) {
        reject(e);
        return;
      }
      map.addControl(new window.maplibregl.AttributionControl({
        compact: true,
        customAttribution: 'Natural Earth · NHC HURDAT2 · NOAA IBTrACS'
      }));
      map.addControl(new window.maplibregl.NavigationControl(), 'top-right');
      /* keep source/image hiccups quiet; remember the last one for debugging */
      map.on('error', function (ev) { _lastError = ev && ev.error; });
      map.on('load', function () {
        try {
          addDataLayers(map);
          bindEvents(map);
          _map = map;
          _popup = new window.maplibregl.Popup({
            closeButton: false, closeOnClick: false,
            className: 'xp-pop', maxWidth: '300px', offset: 10
          });
          resolve(XPMap);
        } catch (e) {
          reject(e);
        }
      });
    });
  }

  function buildFeatures(ids, detail, palette) {
    var pal = palette || _palette || PALETTES.std;
    _palette = pal;
    var byI = stormsById();
    var lines = [];
    var points = [];
    for (var a = 0; a < (ids || []).length; a++) {
      var i = ids[a];
      var pts = window.XPData.trackOf(i);
      if (!pts || !pts.length) continue;
      if (!detail) {
        var coords = [];
        for (var j = 0; j < pts.length; j++) coords.push([pts[j][2], pts[j][1]]);
        lines.push({
          type: 'Feature',
          geometry: { type: 'LineString', coordinates: coords },
          properties: { i: i, color: peakColor(byI[i], pal) }
        });
      } else {
        for (var k = 0; k < pts.length; k++) {
          var p = pts[k];
          var c = pal[window.XPData.catFromWind(p[3])] || pal.TD;
          if (k < pts.length - 1) {
            lines.push({
              type: 'Feature',
              geometry: { type: 'LineString',
                coordinates: [[p[2], p[1]], [pts[k + 1][2], pts[k + 1][1]]] },
              properties: { i: i, k: k, color: c }
            });
          }
          points.push({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [p[2], p[1]] },
            properties: { i: i, k: k, color: c, wind: p[3] == null ? null : p[3],
                          pres: p[4] == null ? null : p[4], t: p[0], lat: p[1], lon: p[2] }
          });
        }
      }
    }
    return {
      lines: { type: 'FeatureCollection', features: lines },
      points: detail ? { type: 'FeatureCollection', features: points } : null
    };
  }

  function setTracks(built) {
    if (!_map) return;
    built = built || {};
    _detail = !!built.points;
    setData('xp-tracks', built.lines || EMPTY);
    setData('xp-fixes', built.points || EMPTY);
    try {
      _map.setPaintProperty('xp-tracks', 'line-width', _detail ? 2.2 : 1.3);
      _map.setPaintProperty('xp-tracks', 'line-opacity', _detail ? 0.95 : 0.85);
    } catch (e) {}
    hidePopup();
  }

  function setSelected(i) {
    _selId = (i === undefined || i === null) ? null : i;
    if (!_map) return;
    if (_selId === null) {
      setData('xp-sel', EMPTY);
      setData('xp-lf', EMPTY);
      return;
    }
    var byI = stormsById();
    var storm = byI[_selId];
    var pts = window.XPData.trackOf(_selId);
    var lineFC = EMPTY;
    if (pts && pts.length) {
      var coords = [];
      for (var j = 0; j < pts.length; j++) coords.push([pts[j][2], pts[j][1]]);
      lineFC = { type: 'FeatureCollection', features: [{
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: coords },
        properties: { i: _selId, color: peakColor(storm, _palette) }
      }] };
    }
    setData('xp-sel', lineFC);
    /* landfall markers from the catalog lf rows */
    var lf = (storm && storm.lf) || [];
    var lfFeats = [];
    for (var k = 0; k < lf.length; k++) {
      lfFeats.push({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [lf[k][2], lf[k][1]] },
        properties: { i: _selId, t: lf[k][0], wind: lf[k][3] == null ? null : lf[k][3] }
      });
    }
    setData('xp-lf', { type: 'FeatureCollection', features: lfFeats });
  }

  /* pinned storms render independently of the filter set: features are
     built straight from XPData.trackOf + the catalog peak category. The
     palette defaults to the one last passed to buildFeatures. */
  function setPinned(ids, palette) {
    if (!_map) return;
    var pal = palette || _palette || PALETTES.std;
    var byI = stormsById();
    var feats = [];
    for (var a = 0; a < (ids || []).length; a++) {
      var i = ids[a];
      var pts = window.XPData.trackOf(i);
      if (!pts || !pts.length) continue;
      var coords = [];
      for (var j = 0; j < pts.length; j++) coords.push([pts[j][2], pts[j][1]]);
      feats.push({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: coords },
        properties: { i: i, color: peakColor(byI[i], pal) }
      });
    }
    setData('xp-pin', { type: 'FeatureCollection', features: feats });
  }

  function setRadius(r) {
    if (!_map) return;
    if (!r || r.lat === undefined || r.lat === null) {
      setData('xp-radius', EMPTY);
      return;
    }
    setData('xp-radius', { type: 'FeatureCollection', features: [{
      type: 'Feature',
      geometry: { type: 'Polygon', coordinates: [circleRing(r.lat, r.lon, r.km || 100)] },
      properties: {}
    }] });
  }

  function setDensity(entry) {
    if (!_map) return;
    var key = entry && entry.key;
    if (key === _densityKey && key) return;
    try {
      if (_map.getLayer('xp-density')) _map.removeLayer('xp-density');
      if (_map.getSource('xp-density')) _map.removeSource('xp-density');
    } catch (e) {}
    _densityKey = null;
    if (!entry || !entry.file || !entry.coords) return;
    try {
      _map.addSource('xp-density', {
        type: 'image',
        url: BASE + '/' + entry.file,
        coordinates: entry.coords
      });
      _map.addLayer({
        id: 'xp-density', type: 'raster', source: 'xp-density',
        paint: { 'raster-opacity': 0.78, 'raster-fade-duration': 0 }
      }, 'xp-tracks');
      _densityKey = key || entry.file;
    } catch (e2) {}
  }

  function flyToStorm(i) {
    if (!_map) return;
    var pts = window.XPData.trackOf(i);
    if (!pts || !pts.length) return;
    var w = Infinity, e = -Infinity, s = Infinity, n = -Infinity;
    for (var k = 0; k < pts.length; k++) {
      var lo = pts[k][2], la = pts[k][1];
      if (lo < w) w = lo;
      if (lo > e) e = lo;
      if (la < s) s = la;
      if (la > n) n = la;
    }
    try {
      _map.fitBounds([[w, s], [e, n]], { padding: 70, maxZoom: 6.5, duration: 700 });
    } catch (err) {}
  }

  function setView(v) {
    if (!_map || !v) return;
    try {
      _map.jumpTo({ center: [v.lon, v.lat], zoom: v.zoom });
    } catch (e) {}
  }

  function basinView(b) {
    var v = BASIN_VIEWS[b];
    return v ? { lon: v.lon, lat: v.lat, zoom: v.zoom } : null;
  }

  function getView() {
    if (!_map) return null;
    var c = _map.getCenter();
    return { lon: c.lng, lat: c.lat, zoom: _map.getZoom() };
  }

  function on(evt, cb) {
    if (!_map || typeof cb !== 'function') return;
    _map.on(evt, cb);
  }

  var XPMap = {
    PALETTES: PALETTES,
    create: create,
    buildFeatures: buildFeatures,
    setTracks: setTracks,
    setSelected: setSelected,
    setPinned: setPinned,
    setRadius: setRadius,
    setDensity: setDensity,
    flyToStorm: flyToStorm,
    setView: setView,
    basinView: basinView,
    getView: getView,
    on: on,
    map: function () { return _map; },
    lastError: function () { return _lastError; }
  };

  window.XPMap = XPMap;
})();
