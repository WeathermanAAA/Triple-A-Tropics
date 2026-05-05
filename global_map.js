// Triple-A-Tropics · home-page global tropical-cyclone map.
//
// Single Pacific-centered SVG showing every storm of the current season
// across all three basins (NA / EP / WP). Active storms get the spinning
// hurricane icon (TS+) or a red "L" label (invest); dissipated storms
// render as per-observation intensity-colored dots.
//
// Reads:
//   /al_tracks_data.json
//   /ep_tracks_data.json
//   /wp_tracks_data.json
//   /ne_50m_admin_0_countries.geojson
//   /ne_50m_coastline.geojson
//
// Renders into <svg id="globalMap"> + <ul id="stormList">.
//
// Defensive: any fetch failure or runtime exception leaves a small
// "Tropical activity unavailable" placeholder. Never throws past the
// outer try/catch, so the rest of the home page remains intact.

(function () {
  'use strict';

  // ---- Projection -------------------------------------------------------
  // Pacific-centered equirectangular: visible longitude window is
  // -25° (left edge) → +335° (right edge), so Africa hugs the left,
  // Pacific sits in the middle, Americas anchor the right. Latitude
  // clamp ±60° trims polar regions where TCs don't occur.
  const LON_MIN = -25, LON_MAX = 335;
  const LAT_MIN = -60, LAT_MAX = 60;
  const VB_W = 360;   // 1 viewBox unit ≈ 1° longitude
  const VB_H = 120;   // 1 viewBox unit ≈ 1° latitude (square pixels at equator)

  function projX(lon) {
    // Wrap into [0, 360) such that lon=-25 → x=0, lon=180 → x=205,
    // lon=335 (=-25) → x=0 again.
    return ((lon - LON_MIN) % 360 + 360) % 360;
  }
  function projY(lat) { return LAT_MAX - lat; }

  const SVG_NS = 'http://www.w3.org/2000/svg';
  function el(tag, attrs, parent) {
    const n = document.createElementNS(SVG_NS, tag);
    if (attrs) for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  // ---- TAT category palette (matches scale.js + active-banner.js) ------
  const CAT_COLORS = {
    'TD': '#fff5cc',
    'TS': '#4ade80', 'SS': '#4ade80', 'STS': '#4ade80', 'SSS': '#4ade80',
    'C1': '#5dd3ff', 'TY': '#5dd3ff',
    'C2': '#ffb83a',
    'C3': '#ec4899',
    'C4': '#ef4444',
    'C5': '#c084fc', 'VSTY': '#c084fc',
  };
  function windToCategory(kt) {
    if (kt == null || kt < 34) return 'TD';
    if (kt < 64) return 'TS';
    if (kt < 83) return 'C1';
    if (kt < 96) return 'C2';
    if (kt < 113) return 'C3';
    if (kt < 137) return 'C4';
    return 'C5';
  }
  function catLabel(cls) {
    // SSHWS-style label rendered inside the spinning icon.
    if (cls === 'TD') return 'D';
    if (cls === 'TS' || cls === 'SS' || cls === 'STS' || cls === 'SSS') return 'S';
    if (cls === 'TY' || cls === 'C1') return '1';
    if (cls === 'C2') return '2';
    if (cls === 'C3') return '3';
    if (cls === 'C4') return '4';
    if (cls === 'C5' || cls === 'VSTY') return '5';
    return 'D';
  }
  function catFullLabel(cls) {
    const map = {
      'TD':'Depression','TS':'Tropical Storm','SS':'Subtropical',
      'STS':'Severe TS','SSS':'Severe Subtropical',
      'TY':'Typhoon','C1':'Cat 1','C2':'Cat 2','C3':'Cat 3',
      'C4':'Cat 4','C5':'Cat 5','VSTY':'Super Typhoon',
    };
    return map[cls] || cls || 'Depression';
  }

  // Hurricane spin path — lifted from /active-banner.js so the home-page
  // marker matches the per-storm banner. Intentionally identical so they
  // read as the same brand glyph.
  const HURRICANE_PATH = 'M 16.37,-28.27 C 13.58,-28.13 11.51,-27.90 9.23,-27.49 C 1.27,-26.06 -5.88,-22.70 -10.92,-18.02 C -14.83,-14.40 -17.41,-10.06 -18.49,-5.32 C -18.95,-3.30 -19.15,-1.42 -19.15,0.91 C -19.15,2.53 -19.09,3.28 -18.89,4.45 C -18.38,7.38 -17.47,9.46 -15.41,12.37 C -13.88,14.54 -13.43,15.31 -13.20,16.13 C -13.11,16.44 -13.09,16.62 -13.09,17.14 C -13.10,17.93 -13.20,18.32 -13.67,19.28 C -15.30,22.59 -18.65,24.93 -23.49,26.14 C -25.26,26.58 -27.29,26.87 -29.18,26.95 L -30.00,26.98 L -29.65,27.06 C -27.33,27.62 -24.41,28.05 -21.57,28.27 C -20.04,28.38 -16.31,28.38 -14.80,28.27 C -12.93,28.13 -11.43,27.95 -9.77,27.67 C -0.59,26.14 7.56,22.03 12.68,16.37 C 16.22,12.45 18.28,8.10 18.93,3.13 C 19.64,-2.25 18.99,-6.47 16.84,-10.16 C 16.48,-10.80 15.79,-11.82 14.99,-12.95 C 13.61,-14.89 13.18,-15.77 13.12,-16.83 C 13.07,-17.61 13.23,-18.26 13.71,-19.23 C 14.97,-21.79 17.38,-23.84 20.67,-25.16 C 23.13,-26.14 26.24,-26.77 29.15,-26.87 L 30.00,-26.90 L 29.67,-26.98 C 29.13,-27.12 27.57,-27.44 26.66,-27.58 C 24.96,-27.87 23.39,-28.05 21.66,-28.18 C 20.72,-28.25 17.16,-28.30 16.37,-28.27 Z';

  // ---- Antimeridian split for basemap ----------------------------------
  // Lifted from satellite/index.html. Splits MultiPolygon / MultiLineString
  // features that have parts on both sides of ±180° into two sibling
  // features (one per hemisphere) so each side renders cleanly.
  function splitAntimeridianFeatures(fc) {
    const out = [];
    for (const feat of fc.features) {
      const t = feat.geometry.type;
      if (t !== 'MultiPolygon' && t !== 'MultiLineString') {
        out.push(feat); continue;
      }
      const east = [], west = [];
      for (const part of feat.geometry.coordinates) {
        const sample = (t === 'MultiPolygon') ? part[0][0][0] : part[0][0];
        (sample >= 0 ? east : west).push(part);
      }
      if (east.length && west.length) {
        out.push({ ...feat, geometry: { type: t, coordinates: east } });
        out.push({ ...feat, geometry: { type: t, coordinates: west } });
      } else {
        out.push(feat);
      }
    }
    return { ...fc, features: out };
  }

  // Convert a [lon,lat] ring/line into an SVG path string. Detects big
  // x-jumps in PROJECTED space (which signal a feature straddling our
  // -25°/+335° cut) and inserts an "M" to break the path there. Without
  // this, anything near the eastern Atlantic seam (Iceland, Greenland,
  // Azores, etc.) draws as a horizontal stripe across the whole canvas.
  function ringToPath(coords, closed) {
    let d = '', prevX = null;
    for (let i = 0; i < coords.length; i++) {
      const lon = coords[i][0], lat = coords[i][1];
      const x = projX(lon), y = projY(lat);
      if (prevX === null || Math.abs(x - prevX) > 180) {
        d += 'M' + x.toFixed(2) + ' ' + y.toFixed(2);
      } else {
        d += 'L' + x.toFixed(2) + ' ' + y.toFixed(2);
      }
      prevX = x;
    }
    if (closed) d += 'Z';
    return d;
  }
  function geometryToD(geom) {
    let d = '';
    if (geom.type === 'Polygon') {
      for (const ring of geom.coordinates) d += ringToPath(ring, true);
    } else if (geom.type === 'MultiPolygon') {
      for (const poly of geom.coordinates) for (const ring of poly) d += ringToPath(ring, true);
    } else if (geom.type === 'LineString') {
      d += ringToPath(geom.coordinates, false);
    } else if (geom.type === 'MultiLineString') {
      for (const line of geom.coordinates) d += ringToPath(line, false);
    }
    return d;
  }

  // ---- Formatters -------------------------------------------------------
  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function ktToMph(kt) { return kt == null ? null : Math.round(kt * 1.15077945); }
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  function fmtPointTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return MONTHS[d.getUTCMonth()] + ' ' + d.getUTCDate() + ', ' +
           String(d.getUTCHours()).padStart(2,'0') + 'z';
  }
  function fmtUpdated(updated) {
    // "2026-05-03 20:28 UTC" → "20z May 3, 2026"
    if (!updated) return '';
    const m = updated.match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2})/);
    if (!m) return updated;
    return m[4] + 'z ' + MONTHS[parseInt(m[2],10)-1] + ' ' +
           parseInt(m[3],10) + ', ' + m[1];
  }
  function fmtPos(lat, lon) {
    let lo = lon;
    while (lo > 180) lo -= 360;
    while (lo < -180) lo += 360;
    return Math.abs(lat).toFixed(1) + '°' + (lat >= 0 ? 'N' : 'S') + ' ' +
           Math.abs(lo).toFixed(1) + '°' + (lo >= 0 ? 'E' : 'W');
  }
  function fetchJSON(url) {
    // Cache-bust on a slow rolling key (15-minute resolution) so visitors
    // always get the latest workflow output without flooding a CDN with
    // unique URLs every page-load.
    const bust = Math.floor(Date.now() / 900000);
    const sep = url.indexOf('?') >= 0 ? '&' : '?';
    return fetch(url + sep + 't=' + bust, { cache: 'no-store' })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(r.status + ' ' + url)));
  }

  // ---- Module-level state for interactivity -----------------------------
  // selectedSid is the currently-clicked storm; null = nothing selected.
  // Track groups + list rows are stamped with data-sid so highlight/dim
  // can flip classes in O(n) instead of full re-render.
  let selectedSid = null;
  let allStorms = [];
  let svgEl = null, listEl = null, tooltipEl = null;

  // ---- Build basemap ----------------------------------------------------
  function buildBasemap(svg, countries, coast) {
    // Ocean fill — single rect spanning the entire viewBox.
    el('rect', {
      x: 0, y: 0, width: VB_W, height: VB_H,
      fill: '#2463a0',
    }, svg);

    // Latitude grid lines: equator + ±30° + ±60° (the last two are clamp
    // boundaries, drawn so the eye doesn't think the canvas just ends).
    const grid = el('g', { stroke: '#2a2e36', 'stroke-opacity': 0.3,
                            'stroke-dasharray': '2 2', 'stroke-width': 0.3,
                            fill: 'none' }, svg);
    [-60, -30, 0, 30, 60].forEach(lat => {
      const y = projY(lat);
      el('line', { x1: 0, y1: y, x2: VB_W, y2: y }, grid);
    });
    // Reference meridians: prime meridian + antimeridian.
    [0, 180].forEach(lon => {
      const x = projX(lon);
      el('line', { x1: x, y1: 0, x2: x, y2: VB_H }, grid);
    });

    // Country fills with white borders.
    if (countries && countries.features) {
      const g = el('g', {
        fill: '#aeb2b5',
        stroke: '#ffffff',
        'stroke-width': 0.18,
        'stroke-opacity': 0.9,
        'stroke-linejoin': 'round',
        'stroke-linecap': 'round',
      }, svg);
      for (const feat of countries.features) {
        const d = geometryToD(feat.geometry);
        if (d) el('path', { d }, g);
      }
    }
    // Coastlines on top of country fills.
    if (coast && coast.features) {
      const g = el('g', {
        fill: 'none',
        stroke: '#ffffff',
        'stroke-width': 0.10,
        'stroke-opacity': 0.85,
        'stroke-linejoin': 'round',
        'stroke-linecap': 'round',
      }, svg);
      for (const feat of coast.features) {
        const d = geometryToD(feat.geometry);
        if (d) el('path', { d }, g);
      }
    }
  }

  // ---- Build tracks (dots + connector lines per storm) -----------------
  function buildTracks(svg, storms) {
    const g = el('g', { class: 'gm-tracks' }, svg);
    for (const s of storms) {
      if (!s.points || !s.points.length) continue;
      const stormGroup = el('g', {
        class: 'gm-storm' + (s.is_active ? ' gm-active' : ''),
        'data-sid': s.sid,
      }, g);

      // Connector polyline through all observations. Single thin gray
      // line — its only job is to make consecutive observations read as
      // one trajectory, the dots carry the intensity story.
      let lineD = '', prevX = null;
      for (const p of s.points) {
        if (p.lat == null || p.lon == null) continue;
        const x = projX(p.lon), y = projY(p.lat);
        if (prevX === null || Math.abs(x - prevX) > 180) {
          lineD += 'M' + x.toFixed(2) + ' ' + y.toFixed(2);
        } else {
          lineD += 'L' + x.toFixed(2) + ' ' + y.toFixed(2);
        }
        prevX = x;
      }
      if (lineD) {
        el('path', {
          d: lineD,
          fill: 'none',
          stroke: '#2a2e36',
          'stroke-width': 0.18,
          'stroke-opacity': 0.75,
          'stroke-linecap': 'round',
          'stroke-linejoin': 'round',
        }, stormGroup);
      }

      // Per-observation intensity-colored dots.
      for (let i = 0; i < s.points.length; i++) {
        const p = s.points[i];
        if (p.lat == null || p.lon == null) continue;
        const x = projX(p.lon), y = projY(p.lat);
        const cat = (p.cls && CAT_COLORS[p.cls]) ? p.cls : windToCategory(p.wind_kt);
        const fill = CAT_COLORS[cat] || '#fff5cc';
        const dot = el('circle', {
          cx: x.toFixed(2), cy: y.toFixed(2),
          r: 0.95,
          fill,
          stroke: s.is_active ? '#ffffff' : 'none',
          'stroke-width': s.is_active ? 0.18 : 0,
          'fill-opacity': s.is_active ? 1.0 : 0.7,
          class: 'gm-dot',
          'data-sid': s.sid,
          'data-idx': i,
        }, stormGroup);
        // Stash a tiny tooltip payload on the element to avoid rebuilding
        // every hover.
        dot.__t = {
          name: s.name, sid: s.sid,
          windKt: p.wind_kt, presMb: p.pressure_mb,
          time: p.t, cat, lat: p.lat, lon: p.lon,
          isActive: s.is_active,
        };
      }
    }
  }

  // ---- Build active-storm markers (z-top) ------------------------------
  function buildActiveMarkers(svg, storms) {
    const g = el('g', { class: 'gm-markers' }, svg);
    for (const s of storms) {
      if (!s.is_active) continue;
      const last = (s.points || []).slice().reverse().find(p => p.lat != null && p.lon != null);
      if (!last) continue;
      const x = projX(last.lon), y = projY(last.lat);
      const peakKt = s.peak_wind_kt;
      const isInvest = !!s.is_invest || (peakKt != null && peakKt < 34);

      const marker = el('g', {
        class: 'gm-marker' + (isInvest ? ' gm-marker-invest' : ' gm-marker-cyclone'),
        transform: 'translate(' + x.toFixed(2) + ' ' + y.toFixed(2) + ')',
        'data-sid': s.sid,
      }, g);

      if (isInvest) {
        // TD / invest: bold red "L" with white designation label below.
        // CSS drop-shadow keeps the L legible over the dark blue ocean.
        const t = el('text', {
          x: 0, y: 0,
          'text-anchor': 'middle',
          'dominant-baseline': 'central',
          'font-family': 'Metropolis, "Helvetica Neue", Arial, sans-serif',
          'font-weight': 900,
          'font-size': 7,
          fill: '#ef4444',
          stroke: 'rgba(0,0,0,0.55)',
          'stroke-width': 0.5,
          'paint-order': 'stroke',
          style: 'filter: drop-shadow(0 0 1.5px rgba(0,0,0,0.7));',
        }, marker);
        t.textContent = 'L';
        const lbl = el('text', {
          x: 0, y: 5.4,
          'text-anchor': 'middle',
          'dominant-baseline': 'hanging',
          'font-family': 'Metropolis, "Helvetica Neue", Arial, sans-serif',
          'font-weight': 800,
          'font-size': 3.2,
          fill: '#ffffff',
          stroke: 'rgba(0,0,0,0.7)',
          'stroke-width': 0.45,
          'paint-order': 'stroke',
        }, marker);
        lbl.textContent = (s.atcf_id || s.name || '').toUpperCase();
      } else {
        // Named TS+ system: spinning hurricane icon.
        const cls = s.current_category || windToCategory(peakKt);
        const fill = CAT_COLORS[cls] || '#fff5cc';
        // Inner group rotates; outer holds position. Icon's native viewBox
        // is 68 units (-34..34); scale factor 0.13 gives a marker ~9 viewBox
        // units across, which renders ~30 px on a 1180 px container.
        const inner = el('g', { transform: 'scale(0.13)' }, marker);
        const spin = el('g', {}, inner);
        el('path', {
          d: HURRICANE_PATH,
          fill,
          stroke: 'rgba(0,0,0,0.55)',
          'stroke-width': 1.2,
        }, spin);
        const anim = document.createElementNS(SVG_NS, 'animateTransform');
        anim.setAttribute('attributeName', 'transform');
        anim.setAttribute('attributeType', 'XML');
        anim.setAttribute('type', 'rotate');
        anim.setAttribute('from', '360');
        anim.setAttribute('to', '0');
        anim.setAttribute('dur', '2.6s');
        anim.setAttribute('repeatCount', 'indefinite');
        spin.appendChild(anim);
        // Center label sits on the outer marker (NOT inside spin) so it
        // stays upright while the path rotates.
        const lbl = el('text', {
          x: 0, y: 0,
          'text-anchor': 'middle',
          'dominant-baseline': 'central',
          'font-family': 'Metropolis, "Helvetica Neue", Arial, sans-serif',
          'font-weight': 900,
          'font-size': 4,
          fill: '#131519',
          'paint-order': 'stroke',
          stroke: 'rgba(255,255,255,0.65)',
          'stroke-width': 0.4,
          'stroke-linejoin': 'round',
        }, marker);
        lbl.textContent = catLabel(cls);
      }

      // Hit-test overlay — invisible but catches hover/click reliably
      // even when the rotating path moves out from under the cursor.
      const hit = el('circle', {
        cx: 0, cy: 0, r: 6,
        fill: 'transparent',
        class: 'gm-hit',
        'data-sid': s.sid,
      }, marker);
      marker.__t = {
        name: s.name, sid: s.sid,
        windKt: (s.points && s.points[s.points.length - 1] && s.points[s.points.length - 1].wind_kt) || s.peak_wind_kt,
        peakKt: s.peak_wind_kt,
        presMb: (s.points && s.points[s.points.length - 1] && s.points[s.points.length - 1].pressure_mb) || s.peak_pressure_mb,
        cat: s.current_category || windToCategory(peakKt),
        ace: s.ace,
        lat: last.lat, lon: last.lon,
        isInvest, isActive: true,
      };
    }
  }

  // ---- Overlays (timestamp pill, watermark) ----------------------------
  function buildOverlays(svg, latestUpdated) {
    // Timestamp pill bottom-left. SVG <foreignObject> would be cleaner
    // for HTML styling, but plain <text> + <rect> avoids the foreignObject
    // browser-inconsistency footgun.
    const stamp = 'Current Storms at ' + fmtUpdated(latestUpdated);
    const pad = 1.5, charW = 1.6, fontSize = 3.4;
    const w = stamp.length * charW + pad * 2;
    const x0 = 4, y0 = VB_H - fontSize - pad * 2 - 4;
    const g = el('g', { class: 'gm-overlay-stamp' }, svg);
    el('rect', {
      x: x0, y: y0, width: w, height: fontSize + pad * 2,
      rx: 1.2, ry: 1.2,
      fill: '#1b1e24', 'fill-opacity': 0.9,
      stroke: '#2a2e36', 'stroke-width': 0.18,
    }, g);
    const t = el('text', {
      x: x0 + pad, y: y0 + fontSize + pad - 0.2,
      'font-family': 'Metropolis, "Helvetica Neue", Arial, sans-serif',
      'font-weight': 600, 'font-size': fontSize,
      fill: '#e8ebef',
    }, g);
    t.textContent = stamp;

    // Brand watermark bottom-right.
    const mark = 'triple-a-tropics.com';
    const mw = mark.length * 1.2 + 1;
    const mt = el('text', {
      x: VB_W - mw - 2, y: VB_H - 2,
      'font-family': 'Metropolis, "Helvetica Neue", Arial, sans-serif',
      'font-weight': 600, 'font-size': 2.6,
      fill: '#9199a4', 'fill-opacity': 0.75,
    }, svg);
    mt.textContent = mark;
  }

  // ---- Side panel: storm list -----------------------------------------
  function buildSidePanel(list, storms, headerEl) {
    list.innerHTML = '';
    const active = storms.filter(s => s.is_active);
    const dissipated = storms.filter(s => !s.is_active);
    // Active first (already pinned visually by CSS), dissipated by
    // formation date descending so the most recent dissipated storm is
    // closest to the top of the dissipated section.
    dissipated.sort((a, b) => {
      const ta = a.start ? new Date(a.start).getTime() : 0;
      const tb = b.start ? new Date(b.start).getTime() : 0;
      return tb - ta;
    });
    const ordered = active.concat(dissipated);

    if (headerEl) {
      const total = ordered.length;
      const basinsWith = new Set(ordered.map(s => s.basin)).size;
      let txt;
      if (active.length > 0) {
        txt = active.length + ' active across ' + basinsWith + ' basin' +
              (basinsWith === 1 ? '' : 's') +
              ' · ' + total + ' season storm' + (total === 1 ? '' : 's');
      } else {
        txt = total + ' season storm' + (total === 1 ? '' : 's') +
              ' · ' + (basinsWith || 0) + ' basin' + (basinsWith === 1 ? '' : 's');
      }
      headerEl.textContent = txt;
    }

    const BASIN_LABELS = { 'al': 'ATL', 'ep': 'EPAC', 'wp': 'WPAC' };
    for (const s of ordered) {
      const li = document.createElement('li');
      li.className = 'gm-list-row' + (s.is_active ? ' gm-list-active' : '');
      li.setAttribute('data-sid', s.sid);
      li.setAttribute('role', 'option');
      li.tabIndex = 0;

      const peakCat = s.max_category || windToCategory(s.peak_wind_kt);
      const peakColor = CAT_COLORS[peakCat] || '#fff5cc';

      const basinTag = (BASIN_LABELS[s.basin] || (s.basin || '').toUpperCase());
      const nameDisplay = s.name || s.atcf_id || 'Unnamed';
      const peakKt = s.peak_wind_kt != null ? Math.round(s.peak_wind_kt) : '—';
      const peakMph = s.peak_wind_kt != null ? ktToMph(s.peak_wind_kt) : null;
      const presMb = s.peak_pressure_mb != null ? Math.round(s.peak_pressure_mb) : null;
      const ace = s.ace != null ? s.ace.toFixed(1) : '—';

      const intensityChip = '<span class="gm-list-chip" style="background:' + peakColor + '">' +
        escHtml(peakCat) + '</span>';
      const stats = 'Peak <b>' + escHtml(peakKt) + ' kt</b>' +
        (peakMph != null ? ' (' + peakMph + ' mph)' : '') +
        (presMb != null ? ' · Min <b>' + presMb + ' mb</b>' : '') +
        ' · ACE <b>' + escHtml(ace) + '</b>';

      li.innerHTML =
        '<div class="gm-list-top">' +
          '<span class="gm-list-basin">' + escHtml(basinTag) + '</span>' +
          '<span class="gm-list-name">' + escHtml(nameDisplay) + '</span>' +
          intensityChip +
        '</div>' +
        '<div class="gm-list-stats">' + stats + '</div>';
      list.appendChild(li);
    }
  }

  // ---- Tooltip (HTML over SVG) ----------------------------------------
  function ensureTooltip() {
    if (tooltipEl) return tooltipEl;
    tooltipEl = document.createElement('div');
    tooltipEl.className = 'gm-tooltip';
    tooltipEl.setAttribute('role', 'tooltip');
    tooltipEl.style.display = 'none';
    document.body.appendChild(tooltipEl);
    return tooltipEl;
  }
  function hideTooltip() { if (tooltipEl) tooltipEl.style.display = 'none'; }
  function showTooltipAt(html, evt) {
    const tt = ensureTooltip();
    tt.innerHTML = html;
    tt.style.display = 'block';
    // Mobile: pin to bottom of screen instead of cursor-follow so a
    // finger doesn't cover the data.
    if (window.innerWidth < 760) {
      tt.classList.add('gm-tooltip-bottom');
      tt.style.left = '8px';
      tt.style.right = '8px';
      tt.style.top = 'auto';
      tt.style.bottom = '12px';
    } else {
      tt.classList.remove('gm-tooltip-bottom');
      const offX = 14, offY = 14;
      const w = tt.offsetWidth || 240;
      const h = tt.offsetHeight || 100;
      let l = evt.clientX + offX;
      let t = evt.clientY + offY;
      if (l + w > window.innerWidth - 4) l = evt.clientX - w - offX;
      if (t + h > window.innerHeight - 4) t = evt.clientY - h - offY;
      tt.style.left = l + 'px';
      tt.style.top = t + 'px';
      tt.style.right = 'auto';
      tt.style.bottom = 'auto';
    }
  }
  function tooltipForActive(t) {
    const cat = catFullLabel(t.cat);
    const peakKtTxt = t.peakKt != null ? Math.round(t.peakKt) + ' kt peak' : '';
    const curKtTxt = t.windKt != null ? Math.round(t.windKt) + ' kt' : '—';
    const curMphTxt = t.windKt != null ? ktToMph(t.windKt) + ' mph' : '';
    return (
      '<div class="gm-tt-head">' +
        '<b>' + escHtml(t.name || '—') + '</b>' +
        '<span class="gm-tt-cat" style="background:' + (CAT_COLORS[t.cat] || '#fff5cc') + '">' +
          escHtml(cat) + '</span>' +
      '</div>' +
      '<div class="gm-tt-row">' + escHtml(curKtTxt) +
        (curMphTxt ? ' (' + curMphTxt + ')' : '') +
        (t.presMb != null ? ' · ' + Math.round(t.presMb) + ' mb' : '') +
      '</div>' +
      '<div class="gm-tt-row">' + escHtml(fmtPos(t.lat, t.lon)) + '</div>' +
      (peakKtTxt ? '<div class="gm-tt-row gm-tt-muted">' + escHtml(peakKtTxt) + '</div>' : '') +
      (t.ace != null ? '<div class="gm-tt-row gm-tt-muted">ACE ' + t.ace.toFixed(1) + '</div>' : '') +
      '<div class="gm-tt-hint">Click for full track</div>'
    );
  }
  function tooltipForDot(t) {
    const cat = catFullLabel(t.cat);
    const ktTxt = t.windKt != null ? Math.round(t.windKt) + ' kt' : '—';
    const mphTxt = t.windKt != null ? ' (' + ktToMph(t.windKt) + ' mph)' : '';
    return (
      '<div class="gm-tt-head">' +
        '<b>' + escHtml(t.name || '—') + '</b>' +
        '<span class="gm-tt-cat" style="background:' + (CAT_COLORS[t.cat] || '#fff5cc') + '">' +
          escHtml(cat) + '</span>' +
      '</div>' +
      '<div class="gm-tt-row">' + escHtml(ktTxt) + mphTxt +
        (t.presMb != null ? ' · ' + Math.round(t.presMb) + ' mb' : '') +
      '</div>' +
      '<div class="gm-tt-row gm-tt-muted">' + escHtml(fmtPointTime(t.time)) + '</div>'
    );
  }

  // ---- Selection (click to highlight one storm, dim the rest) ----------
  function applySelection() {
    const wrap = svgEl && svgEl.closest('.global-map-card');
    if (wrap) wrap.classList.toggle('gm-has-selection', selectedSid != null);
    if (svgEl) {
      svgEl.querySelectorAll('.gm-storm').forEach(g => {
        const sid = g.getAttribute('data-sid');
        g.classList.toggle('gm-storm-selected', sid === selectedSid);
        g.classList.toggle('gm-storm-dimmed', selectedSid != null && sid !== selectedSid);
      });
      svgEl.querySelectorAll('.gm-marker').forEach(g => {
        const sid = g.getAttribute('data-sid');
        g.classList.toggle('gm-marker-dimmed', selectedSid != null && sid !== selectedSid);
      });
    }
    if (listEl) {
      let firstActive = null;
      listEl.querySelectorAll('.gm-list-row').forEach(li => {
        const sid = li.getAttribute('data-sid');
        const on = sid === selectedSid;
        li.classList.toggle('gm-list-selected', on);
        if (on && !firstActive) firstActive = li;
      });
      // Scroll the selected row into view so the user can see what they
      // clicked when the panel is scrolled below the fold.
      if (firstActive && typeof firstActive.scrollIntoView === 'function') {
        firstActive.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }
    }
  }
  function setSelected(sid) {
    selectedSid = (sid === selectedSid) ? null : sid;
    applySelection();
  }

  // ---- Wire up event listeners ----------------------------------------
  function attachInteractivity() {
    if (!svgEl) return;
    // SVG hover/click — delegate from the root.
    svgEl.addEventListener('mousemove', evt => {
      const target = evt.target;
      if (target.classList && target.classList.contains('gm-dot')) {
        if (target.__t) showTooltipAt(tooltipForDot(target.__t), evt);
        return;
      }
      // Marker hit-circle bubbles its __t off the parent <g>.
      if (target.classList && target.classList.contains('gm-hit')) {
        const m = target.parentNode;
        if (m && m.__t) showTooltipAt(tooltipForActive(m.__t), evt);
        return;
      }
      hideTooltip();
    });
    svgEl.addEventListener('mouseleave', hideTooltip);
    svgEl.addEventListener('click', evt => {
      const target = evt.target;
      // Click on a marker → select that storm.
      if (target.classList && target.classList.contains('gm-hit')) {
        const m = target.parentNode;
        const sid = m && m.getAttribute('data-sid');
        if (sid) { setSelected(sid); evt.stopPropagation(); return; }
      }
      // Click on a dot → select its storm.
      if (target.classList && target.classList.contains('gm-dot')) {
        const sid = target.getAttribute('data-sid');
        if (sid) { setSelected(sid); evt.stopPropagation(); return; }
      }
      // Click on empty ocean → clear selection.
      selectedSid = null;
      applySelection();
    });

    // Side panel hover/click.
    if (listEl) {
      listEl.addEventListener('click', evt => {
        const li = evt.target.closest('.gm-list-row');
        if (!li) return;
        const sid = li.getAttribute('data-sid');
        if (sid) setSelected(sid);
      });
      listEl.addEventListener('keydown', evt => {
        if (evt.key !== 'Enter' && evt.key !== ' ') return;
        const li = evt.target.closest('.gm-list-row');
        if (!li) return;
        const sid = li.getAttribute('data-sid');
        if (sid) { evt.preventDefault(); setSelected(sid); }
      });
    }

    // Esc clears.
    document.addEventListener('keydown', evt => {
      if (evt.key === 'Escape' && selectedSid) { selectedSid = null; applySelection(); }
    });
  }

  // ---- Fallback when something goes wrong -----------------------------
  function renderFallback(card) {
    if (!card) return;
    card.innerHTML =
      '<div class="gm-fallback">' +
        '<div>Tropical activity unavailable</div>' +
        '<div class="gm-fallback-sub">Map will return shortly. Check back in a few minutes.</div>' +
      '</div>';
  }

  // ---- Boot ------------------------------------------------------------
  async function init() {
    svgEl = document.getElementById('globalMap');
    listEl = document.getElementById('stormList');
    const headerEl = document.getElementById('stormListHeader');
    const card = document.querySelector('.global-map-card');
    if (!svgEl || !card) return;

    svgEl.setAttribute('viewBox', '0 0 ' + VB_W + ' ' + VB_H);
    svgEl.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    svgEl.setAttribute('role', 'img');
    svgEl.setAttribute('aria-label',
      'Global tropical cyclone map for the current season — every storm and active disturbance across the Atlantic, East Pacific, and West Pacific basins.');

    try {
      const [al, ep, wp, countries, coast] = await Promise.all([
        fetchJSON('/al_tracks_data.json').catch(() => ({ storms: [], updated: '', basin: 'al' })),
        fetchJSON('/ep_tracks_data.json').catch(() => ({ storms: [], updated: '', basin: 'ep' })),
        fetchJSON('/wp_tracks_data.json').catch(() => ({ storms: [], updated: '', basin: 'wp' })),
        fetchJSON('/ne_50m_admin_0_countries.geojson'),
        fetchJSON('/ne_50m_coastline.geojson'),
      ]);

      // Stamp basin onto each storm so the side panel can label rows
      // even though the upstream JSON doesn't repeat it per-storm.
      function stamp(j) {
        const list = (j && Array.isArray(j.storms)) ? j.storms : [];
        for (const s of list) s.basin = j.basin;
        return list;
      }
      allStorms = stamp(al).concat(stamp(ep)).concat(stamp(wp));

      const splitCountries = splitAntimeridianFeatures(countries);
      const splitCoast = splitAntimeridianFeatures(coast);

      // Z-order: ocean → grid → countries → coast → tracks → markers → overlays.
      buildBasemap(svgEl, splitCountries, splitCoast);
      buildTracks(svgEl, allStorms);
      buildActiveMarkers(svgEl, allStorms);

      // Latest updated stamp: pick the freshest of the three feeds so a
      // stale basin doesn't dominate.
      const allUpdated = [al && al.updated, ep && ep.updated, wp && wp.updated]
        .filter(Boolean)
        .sort()
        .reverse();
      buildOverlays(svgEl, allUpdated[0] || '');

      buildSidePanel(listEl, allStorms, headerEl);
      attachInteractivity();
    } catch (err) {
      console.error('[global-map] init failed:', err);
      renderFallback(card);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
