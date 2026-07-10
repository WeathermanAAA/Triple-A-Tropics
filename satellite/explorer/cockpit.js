/* Satellite Explorer COCKPIT — the full §6 shell around the working TiledViewer.
 * ADDITIVE UI LAYER: the pyramid, BT inspector, draw-box, export and compare
 * mechanics all live in tiled_viewer.js / bt_probe.js and are driven, never
 * reimplemented, here. This file owns: the field rail (left), the satellite/
 * domain/region rail (right), the timeline + transport + tool bar (bottom),
 * the 1/2/4 pane grid (time-locked, per-pane field+region), URL-state
 * permalinks, and the honesty gating — anything without real data behind it
 * (GOES-18, meso domains, MRMS/obs/model overlays, Chart, time machine) is
 * rendered greyed with a "coming"/"no data yet" tag, never faked.
 *
 * Availability ground truth = the R2 products.json the box emitter writes
 * (ONE fetch): a products.js entry whose id is absent there greys out until
 * the box emits it (e.g. a new recipe lands in the registry before the box
 * pulls). Full Disk enables itself the same way off the fd products.json.
 */
(function () {
  'use strict';

  var PRODUCTS = (window.TVProducts && window.TVProducts.products) || [];
  var HW_PRODUCTS = (window.TVProducts && window.TVProducts.himawari9 &&
                     window.TVProducts.himawari9.products) || [];
  var GEO_PRODUCTS = (window.TVProducts && window.TVProducts.geo &&
                      window.TVProducts.geo.products) || [];
  var PBASE = window.TVProducts ? window.TVProducts.base : '';
  var params = new URLSearchParams(location.search);
  var $ = function (id) { return document.getElementById(id); };

  // Domains are satellite-scoped: each row names its satellite, display
  // labels, and the sector token substituted into the product path (the
  // products.js export sector is conus for goes19, wpac for himawari9).
  var DOMAINS = {
    conus:     { sat: 'goes19', satLabel: 'GOES-19', label: 'CONUS', sector: 'conus',
                 sensor: 'ABI', source: 'NOAA', scanProd: 'CMIPC' },
    fd:        { sat: 'goes19', satLabel: 'GOES-19', label: 'Full Disk', sector: 'fd',
                 sensor: 'ABI', source: 'NOAA', scanProd: 'CMIPF' },
    'hw-wpac': { sat: 'himawari9', satLabel: 'Himawari-9', label: 'W Pacific', sector: 'wpac',
                 sensor: 'AHI', source: 'JMA', scanProd: 'FLDK' },
    'hw-fd':   { sat: 'himawari9', satLabel: 'Himawari-9', label: 'Full Disk', sector: 'fd',
                 sensor: 'AHI', source: 'JMA', scanProd: 'FLDK' },
    // the GLOBAL DEFAULT: GOES-19 + GOES-18 + Himawari-9 full disks stitched
    // nadir-nearest; the Meteosat sector is an HONEST transparent gap
    global:    { sat: 'geo', satLabel: 'GEO ring', label: 'Global', sector: 'global',
                 sensor: 'multi-sat', source: 'NOAA + JMA', scanProd: 'GEO-RING' }
  };
  function domainInfo(d) { return DOMAINS[d] || DOMAINS.conus; }
  // palette tag for the unified header's product·palette slot (archive
  // parity: "CMIPC · rainbow_ir"); scalar products name their frozen
  // enhancement, composites/RGBs name themselves honestly
  function paletteTag(p) {
    if (p.cbar && p.cbar.img) {
      var m = /cbars\/(.+)\.png$/.exec(p.cbar.img);
      if (m) return m[1] === 'gray_refl' ? 'gray refl' : m[1];
    }
    return p.group === 'composite' ? p.key : 'RGB';
  }
  function productSet(domain) {
    var sat = domainInfo(domain).sat;
    return sat === 'himawari9' ? HW_PRODUCTS
      : sat === 'geo' ? GEO_PRODUCTS
      : PRODUCTS;
  }
  function productByKey(k, domain) {
    var set = productSet(domain === undefined ? S.domain : domain);
    for (var i = 0; i < set.length; i++) if (set[i].key === k) return set[i];
    return null;
  }
  // cross-satellite field continuity: same key, else C##<->B## by number
  // (the AHI green B02 and the ABI cirrus C04 have no counterpart), else ir.
  function mapKeyAcross(key, toDomain) {
    if (productByKey(key, toDomain)) return key;
    var m = /^([cb])(\d\d)$/.exec(key);
    if (m) {
      var alt = (m[1] === 'c' ? 'b' : 'c') + m[2];
      if (productByKey(alt, toDomain)) return alt;
    }
    return 'ir';
  }
  function manifestUrlFor(p, domain) {
    var path = p.path.replace(/\/(conus|fd|wpac)\//, '/' + domainInfo(domain).sector + '/');
    return PBASE + path + '/latest_times.json';
  }
  function fmtStamp(s) {
    if (!s || s.length < 13) return s || '';
    return s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6, 8) + ' ' +
           s.slice(9, 11) + ':' + s.slice(11, 13) + 'Z';
  }

  // ========================================================================
  // STATE
  // ========================================================================
  var S = {
    domain: 'conus',            // DOMAINS key (meso1/2 + goes18 are "coming")
    avail: {},                  // per-domain Set of emitted product ids (null = unprobed)
    available: null,            // conus Set (kept name: boot availability ground truth)
    panes: [],                  // [{tv, el, product, ready}] — pane 0 persistent
    active: 0,                  // active pane index (field/region target)
    playing: false, fps: 6, dwell: true, linked: true,
    raf: null, last: 0,
    hqExport: false,
    tool: null,                 // 'measure' | 'sketch' | 'selectmap' | null
    measure: null, sketch: null,
    booted: false
  };
  var PANE_DEFAULTS = ['ir', 'c08', 'truecolor', 'airmass'];

  // ========================================================================
  // LEFT RAIL — field selector (tabs: RGB/Composites | Channels)
  // ========================================================================
  var CH_RE = /^([CB])(\d\d) · ([\d.]+ µm) \((.+)\)$/;
  function chParts(p) {
    var m = CH_RE.exec(p.title);
    if (!m) return null;
    var met = m[4];
    if (p.key === 'irbd') met = 'Dvorak BD';
    return { num: m[1] + m[2], wl: m[3], met: met };
  }

  // Microwave + Scatterometer are NATIVE pane fields (cockpit_fields.js):
  // selecting one repurposes the ACTIVE pane — MW overpass tiles become
  // georeferenced maplibre image sources, ASCAT barbs a camera-synced canvas
  // overlay; the legacy standalone viewers' fetch/render logic is re-hosted,
  // not rebuilt. Entries grey "SOON" off each source's own R2 manifest.
  var MW_FIELDS = [
    { key: 'mw-91c', title: '91 GHz color composite', meta: 'NRL · convective structure' },
    { key: 'mw-91h', title: '91H brightness temp', meta: 'NRL · eyewall through cirrus' },
    { key: 'mw-37c', title: '37 GHz color composite', meta: 'NRL · low-level rain bands' },
    { key: 'mw-37h', title: '37H brightness temp', meta: 'NRL · forming eye' }
  ];
  var SC_FIELDS = [
    { key: 'sc-basin', title: 'Ocean winds · recent passes', meta: 'wind barbs · frame via Regions' },
    { key: 'sc-storm', title: 'Ocean winds · storm-locked', meta: 'tagged passes per storm' }
  ];

  function buildFieldRail() {
    var tabs = { rgb: $('cx-tab-rgb'), ch: $('cx-tab-ch'),
                 mw: $('cx-tab-mw'), sc: $('cx-tab-sc') };
    var lists = { rgb: $('cx-list-rgb'), ch: $('cx-list-ch'),
                  mw: $('cx-list-mw'), sc: $('cx-list-sc') };
    function switchTab(which) {
      Object.keys(tabs).forEach(function (k) {
        tabs[k].classList.toggle('on', k === which);
        lists[k].style.display = (k === which) ? '' : 'none';
      });
    }
    Object.keys(tabs).forEach(function (k) {
      tabs[k].onclick = function () { switchTab(k); };
    });

    // MW / ASCAT categories -> native pane fields (cockpit_fields.js)
    [['mw', MW_FIELDS], ['sc', SC_FIELDS]].forEach(function (pair) {
      var kind = pair[0];
      pair[1].forEach(function (f) {
        var row = document.createElement('button');
        row.type = 'button'; row.className = 'cx-field coming';
        row.dataset.embed = kind; row.dataset.key = f.key;
        row.innerHTML = '<b>' + f.title + '</b><span class="cx-meta">' + f.meta +
          ' · <i class="cx-chip">soon</i></span>';
        row.onclick = function () {
          if (row.classList.contains('coming')) return;
          if (S.tm.on) { flash('MW / ASCAT are live-only — exit Time Machine first'); return; }
          if (window.CockpitFields) window.CockpitFields.setPaneField(S.active, f.key);
        };
        lists[kind].appendChild(row);
      });
    });

    rebuildProductRows();
    switchTab('rgb');
  }

  // tile-product rows for the ACTIVE satellite's product set — rebuilt on a
  // cross-satellite domain switch (composites lead the RGB tab, then RGBs;
  // channels keep their products.js order: Clean IR first, then the bands).
  function rebuildProductRows() {
    var lists = { rgb: $('cx-list-rgb'), ch: $('cx-list-ch') };
    ['rgb', 'ch'].forEach(function (k) {
      lists[k].querySelectorAll('.cx-field:not([data-embed])').forEach(function (el) {
        el.remove();
      });
    });
    var set = productSet(S.domain);
    var tmOK = domainInfo(S.domain).sat === 'goes19';   // archive backend is GOES-East
    var ordered = set.filter(function (p) { return p.group === 'composite'; })
      .concat(set.filter(function (p) { return p.group === 'rgb'; }))
      .concat(set.filter(function (p) { return p.group === 'channel'; }));
    ordered.forEach(function (p) {
      var isCh = p.group === 'channel';
      var row = document.createElement('button');
      row.type = 'button'; row.className = 'cx-field'; row.dataset.key = p.key;
      if (tmOK && TM_MAP[p.key]) row.dataset.tm = '1';   // archive-servable in Time Machine
      var ch = isCh ? chParts(p) : null;
      if (ch) {
        row.innerHTML = '<b>' + ch.met + '</b><span class="cx-meta">' +
          ch.num + ' · ' + ch.wl + (p.dayOnly ? ' · ☀ day' : '') + '</span>';
      } else {
        row.innerHTML = '<b>' + p.title + '</b><span class="cx-meta">' +
          (p.group === 'composite' ? 'composite' : 'RGB') +
          (p.bt ? ' · BT' : '') + (p.dayOnly ? ' · ☀ day' : '') + '</span>';
      }
      row.onclick = function () {
        if (row.classList.contains('coming')) return;
        S._touched = true;
        if (window.CockpitFields) window.CockpitFields.clearPaneField(S.active);
        setPaneProduct(S.active, p);
      };
      lists[isCh ? 'ch' : 'rgb'].appendChild(row);
    });
    applyAvailability();
    markFieldActive();
  }

  function markFieldActive() {
    var pane = S.panes[S.active];
    var key = pane ? (pane.kind === 'mw' || pane.kind === 'sc'
      ? pane.fieldKey
      : (pane.product ? pane.product.key : null)) : null;
    document.querySelectorAll('.cx-field').forEach(function (el) {
      el.classList.toggle('active', el.dataset.key === key);
    });
  }

  function availSet(domain) {
    // the honesty ground truth for a domain: its own products.json id Set
    // (null until the probe lands -> rows stay ungated rather than lying)
    if (domain === 'conus') return S.available;
    return S.avail[domain] || null;
  }
  function applyAvailability() {
    var set = availSet(S.domain);
    if (!set) return;
    document.querySelectorAll('.cx-field').forEach(function (el) {
      if (el.dataset.embed) return;   // MW/ASCAT rows: their own manifests gate them
      var p = productByKey(el.dataset.key);
      var ok = p && set.has(p.id);
      el.classList.toggle('coming', !ok);
      var meta = el.querySelector('.cx-meta');
      if (!ok && meta && meta.textContent.indexOf('no data yet') < 0) {
        meta.innerHTML += ' · <i class="cx-chip">no data yet</i>';
      } else if (ok && meta) {
        var chip = meta.querySelector('.cx-chip');
        if (chip && chip.textContent === 'no data yet') {
          meta.innerHTML = meta.innerHTML.replace(/ · <i class="cx-chip">no data yet<\/i>/, '');
        }
      }
    });
  }

  // ========================================================================
  // RIGHT RAIL — satellite / domain / regions / overlays
  // ========================================================================
  function buildDomainRail() {
    document.querySelectorAll('#cx-domains .cx-item').forEach(function (el) {
      el.onclick = function () {
        var d = el.dataset.domain;
        if (el.classList.contains('coming')) return;
        S._touched = true;
        if (d === 'drawbox') { armDrawBox(); return; }
        if (d === 'selectmap') { armTool('selectmap', el); return; }
        setDomain(d);
      };
    });
    // satellite rows scope the domain list; a satellite with no emitted
    // domain yet stays greyed (probeDomains un-greys it mechanically)
    document.querySelectorAll('#cx-sats .cx-item[data-sat]').forEach(function (el) {
      el.onclick = function () {
        if (el.classList.contains('coming')) return;
        var sat = el.dataset.sat;
        if (sat === domainInfo(S.domain).sat) return;
        var first = null;
        Object.keys(DOMAINS).forEach(function (d) {
          if (first) return;
          var row = document.querySelector('[data-domain="' + d + '"]');
          if (DOMAINS[d].sat === sat && row && !row.classList.contains('coming')) first = d;
        });
        if (first) setDomain(first);
        else flash('no ' + (sat === 'himawari9' ? 'Himawari-9' : sat) + ' data yet — box emit pending');
      };
    });
  }
  function markDomain() {
    var sat = domainInfo(S.domain).sat;
    document.querySelectorAll('#cx-domains .cx-item').forEach(function (el) {
      el.classList.toggle('active', el.dataset.domain === S.domain);
      // domain rows show only for the active satellite (tools always show)
      var grp = el.dataset.satgroup;
      if (grp) el.style.display = (grp === sat) ? '' : 'none';
    });
    document.querySelectorAll('#cx-sats .cx-item[data-sat]').forEach(function (el) {
      el.classList.toggle('active', el.dataset.sat === sat);
    });
  }

  function setDomain(d) {
    if (d === S.domain || !DOMAINS[d]) return;
    var row = document.querySelector('[data-domain="' + d + '"]');
    if (row && row.classList.contains('coming')) return;
    if (S.tm.on && domainInfo(d).sat !== 'goes19') {
      flash('Time Machine covers the GOES-East archive — exit it to view Himawari');
      return;
    }
    var crossSat = domainInfo(d).sat !== domainInfo(S.domain).sat;
    S.domain = d;
    markDomain();
    updateGapBadges();
    if (crossSat) rebuildProductRows();
    // re-point every pane at the (mapped) product key in the new domain; the
    // Himawari full disk crosses the antimeridian, so its panes render world
    // copies (the eastern lobe lives at wrapped longitudes).
    var copies = (d === 'hw-fd');
    S.panes.forEach(function (pane, i) {
      if (pane.tv && pane.tv.map && pane.tv.map.setRenderWorldCopies)
        pane.tv.map.setRenderWorldCopies(copies);
      if (pane.product) {
        var p = productByKey(mapKeyAcross(pane.product.key, d), d) || productByKey('ir', d);
        setPaneProduct(i, p, true);
      }
    });
  }

  // The Meteosat sector (~10°W–75°E) has no ingested satellite: the global
  // composite leaves it transparent, and this badge says WHY on the map —
  // never stretch a neighboring disk across it.
  function updateGapBadges() {
    S.panes.forEach(function (pane) {
      if (!pane || !pane.tv || !pane.tv.map) return;
      if (S.domain === 'global' && !pane._gapBadge && window.maplibregl) {
        var el = document.createElement('div');
        el.className = 'cx-gap-badge';
        el.textContent = 'Meteosat sector — no ingest yet · coming';
        pane._gapBadge = new maplibregl.Marker({ element: el })
          .setLngLat([32, 12]).addTo(pane.tv.map);
      } else if (S.domain !== 'global' && pane._gapBadge) {
        pane._gapBadge.remove();
        pane._gapBadge = null;
      }
    });
  }

  // availability probes: each non-default domain self-enables off ITS OWN
  // products.json (the box emitter's on-R2 SSOT) — never faked. A himawari
  // domain succeeding also un-greys the Himawari-9 satellite row.
  function probeDomains() {
    [{ d: 'global', url: 'sat/geo/global/products.json' },
     { d: 'fd', url: 'sat/goes19/fd/products.json' },
     { d: 'hw-wpac', url: 'sat/himawari9/wpac/products.json' },
     { d: 'hw-fd', url: 'sat/himawari9/fd/products.json' }].forEach(function (spec) {
      fetch(PBASE + spec.url, { cache: 'no-cache' })
        .then(function (r) { if (!r.ok) throw 0; return r.json(); })
        .then(function (idx) {
          if (!idx || !idx.count) throw 0;
          S.avail[spec.d] = new Set((idx.products || []).map(function (p) { return p.id; }));
          var el = document.querySelector('[data-domain="' + spec.d + '"]');
          if (el) {
            el.classList.remove('coming');
            var chip = el.querySelector('.cx-chip'); if (chip) chip.remove();
          }
          if (DOMAINS[spec.d].sat !== 'goes19') {
            var srow = document.querySelector('#cx-sats [data-sat="' + DOMAINS[spec.d].sat + '"]');
            if (srow) {
              srow.classList.remove('coming');
              var sc = srow.querySelector('.cx-chip'); if (sc) sc.remove();
            }
          }
          if (spec.d === S.domain) applyAvailability();
          // GLOBAL BY DEFAULT: the explorer opens on the stitched world when
          // the composite exists and the user hasn't steered elsewhere. The
          // probe usually resolves BEFORE pane 0's map is ready — wait for it.
          if (spec.d === 'global' && !params.get('domain') &&
              !params.get('product') && !params.get('manifest')) {
            var tryGlobal = function () {
              if (S._touched || S.domain !== 'conus') return;
              var p0 = S.panes[0];
              if (!p0 || !p0.ready) { setTimeout(tryGlobal, 400); return; }
              setDomain('global');
              // "opens on the world": frame the composite once it swaps in
              var fit = function (n) {
                var pane = S.panes[0];
                if (S.domain !== 'global' || S._touched || n > 20) return;
                if (pane.product && pane.product.id.indexOf('geo-') === 0 &&
                    pane.tv.frames.length) { pane.tv.fitData(); return; }
                setTimeout(function () { fit(n + 1); }, 400);
              };
              fit(0);
            };
            tryGlobal();
          }
        })
        .catch(function () { /* stays greyed "no data yet" — honest */ });
    });
  }

  function buildRegionRail() {
    var host = $('cx-regions');
    if (!window.TATRegions) { host.style.display = 'none'; return; }
    window.TATRegions.GROUPS.forEach(function (g) {
      host.appendChild(regionGroup(g.label, g.regions.map(function (r) {
        return { label: r.label, go: function (tv) { tv.gotoRegion(r.key); } };
      }), g.key === 'continents'));
    });
    // US states, derived from the SAME admin_1 geojson the map overlays draw —
    // no new data source. Filled in once the primary pane's furniture loads.
    S.statesGroup = regionGroup('US States', [], false);
    S.statesGroup.style.display = 'none';
    host.appendChild(S.statesGroup);
  }
  function regionGroup(label, items, open) {
    var d = document.createElement('details');
    if (open) d.open = true;
    var s = document.createElement('summary'); s.textContent = label;
    d.appendChild(s);
    var box = document.createElement('div'); box.className = 'cx-rgn-box';
    items.forEach(function (it) { box.appendChild(regionBtn(it)); });
    d.appendChild(box);
    return d;
  }
  function regionBtn(it) {
    var b = document.createElement('button');
    b.type = 'button'; b.className = 'cx-rgn'; b.textContent = it.label;
    b.onclick = function () {
      var tv = S.panes[S.active] && S.panes[S.active].tv;
      if (tv && tv.map) it.go(tv);
    };
    return b;
  }
  function fillStates(geo) {
    if (!geo || !geo.states || !S.statesGroup) return;
    var seen = {}, items = [];
    (geo.states.features || []).forEach(function (f) {
      var pr = f.properties || {};
      if ((pr.admin || pr.adm0name) !== 'United States of America') return;
      var name = pr.name; if (!name || seen[name]) return; seen[name] = 1;
      var bb = geomBBox(f.geometry); if (!bb) return;
      items.push({ name: name, bb: bb });
    });
    if (!items.length) return;
    items.sort(function (a, b) { return a.name < b.name ? -1 : 1; });
    var box = S.statesGroup.querySelector('.cx-rgn-box');
    items.forEach(function (it) {
      box.appendChild(regionBtn({ label: it.name, go: function (tv) {
        tv.map.fitBounds([[it.bb[0], it.bb[1]], [it.bb[2], it.bb[3]]],
                         { padding: 30, duration: 500 });
      }}));
    });
    S.statesGroup.style.display = '';
  }
  function geomBBox(g) {
    if (!g) return null;
    var w = 180, s = 90, e = -180, n = -90, any = false;
    function eat(c) { any = true;
      if (c[0] < w) w = c[0]; if (c[0] > e) e = c[0];
      if (c[1] < s) s = c[1]; if (c[1] > n) n = c[1]; }
    function walk(a, depth) {
      if (depth === 0) { eat(a); return; }
      for (var i = 0; i < a.length; i++) walk(a[i], depth - 1);
    }
    if (g.type === 'Polygon') walk(g.coordinates, 2);
    else if (g.type === 'MultiPolygon') walk(g.coordinates, 3);
    else return null;
    if (!any || (e - w) > 200) return null;   // AK antimeridian wrap — skip odd boxes
    return [w, s, e, n];
  }

  function buildOverlayRail() {
    ['coast', 'borders', 'states', 'grid'].forEach(function (k) {
      var b = $('cx-ov-' + k);
      b.onclick = function () {
        var on = !b.classList.contains('on');
        b.classList.toggle('on', on);
        S.panes.forEach(function (p) { if (p.tv && p.tv.map) p.tv.setLayer(k, on); });
      };
    });
    // MW pass / ASCAT winds layer over the ACTIVE pane's base field (per-pane
    // settings; the same layer path model/MRMS/obs overlays will use). The
    // buttons enable off their manifests (cockpit_fields.checkAvailability).
    [['mw', 'cx-ov-mw'], ['sc', 'cx-ov-sc']].forEach(function (pair) {
      var b = $(pair[1]);
      if (!b) return;
      b.onclick = function () {
        if (b.disabled || !window.CockpitFields) return;
        var pane = S.panes[S.active];
        if (!pane) return;
        if (pane.kind === pair[0]) { flash('already the pane field — pick a base field first'); return; }
        var st = pair[0] === 'mw' ? pane.mw : pane.sc;
        var on = !(st && st.on);
        window.CockpitFields.setLayer(S.active, pair[0], on);
        b.classList.toggle('on', on);
        window.CockpitFields.syncControls();
      };
    });
    // MRMS / METAR / model-field toggles are STUBS on purpose: each needs its
    // own ingest pipeline (separate builds). The buttons exist, disabled, so
    // the panel shows the plan without faking data.
  }

  // ========================================================================
  // PANES — 1 / 2 / 4, time-locked; camera + field + region are PER-PANE.
  // Pane 0 is the persistent primary viewer (never rebooted on grid change).
  // ========================================================================
  function paneShell(i) {
    var el = document.createElement('div');
    el.className = 'cx-pane';
    el.innerHTML = '<div class="cx-pane-map" id="cx-map-' + i + '"></div>' +
      '<div class="cx-pane-head" id="cx-ph-' + i + '">' +
        '<div class="cx-ph-badge" id="cx-phb-' + i + '"></div>' +
        '<div class="cx-ph-center">' +
          '<div class="cx-ph-title" id="cx-pht-' + i + '"></div>' +
          '<div class="cx-ph-sub" id="cx-phs-' + i + '"></div></div>' +
        '<div class="cx-ph-tag" id="cx-phg-' + i + '"></div></div>' +
      '<div class="cx-ph-wm" id="cx-phw-' + i + '"></div>' +
      '<div class="cx-ph-minmax" id="cx-phm-' + i + '"></div>' +
      '<div class="cx-pane-cbar" id="cx-pc-' + i + '">' +
        '<div class="ticks" id="cx-pct-' + i + '"></div>' +
        '<img alt="" id="cx-pci-' + i + '"></div>' +
      '<div class="cx-pane-key" id="cx-pk-' + i + '"></div>' +
      '<img class="cx-tm-img" id="cx-tm-img-' + i + '" alt="">' +
      '<div class="cx-pane-probe" id="cx-pp-' + i + '"></div>' +
      '<div class="cx-load" id="cx-load-' + i + '"><i></i><span>Loading GOES-19 tiles…</span></div>';
    el.onclick = function () { setActivePane(i); };
    return el;
  }

  function makePane(i, product) {
    var el = paneShell(i);
    $('cx-panes').appendChild(el);
    var pane = { el: el, tv: null, product: product, ready: false };
    S.panes[i] = pane;
    var tv = new TiledViewer({
      container: 'cx-map-' + i,
      manifest: manifestUrlFor(product, S.domain),
      onStatus: paneStatus(i)
    });
    pane.tv = tv;
    tv.boot().then(function () {
      if (!tv.map) return;
      tv.map.on('load', function () {
        pane.ready = true;
        tv.enableInspector();
        tv.map.doubleClickZoom.disable();
        tv.map.on('dblclick', function () { tv.fitData(); });
        // antimeridian-crossing domain (Himawari full disk): world copies on
        if (S.domain === 'hw-fd' && tv.map.setRenderWorldCopies)
          tv.map.setRenderWorldCopies(true);
        applyOverlayState(tv);
        renderPaneChrome(i);
        wireCameraSync(pane);
        updateGapBadges();
        tv.map.on('moveend', function () { paneMinMax(i); });   // header min/max readout
      });
    });
    return pane;
  }

  function applyOverlayState(tv) {
    ['coast', 'borders', 'states', 'grid'].forEach(function (k) {
      tv.setLayer(k, $('cx-ov-' + k).classList.contains('on'));
    });
  }

  function paneStatus(i) {
    return function (kind, data) {
      var pane = S.panes[i];
      if (kind === 'ready' || kind === 'frame' || kind === 'error') {
        var ld = $('cx-load-' + i);
        if (ld) ld.style.display = 'none';
      }
      if (kind === 'error') {
        if (i === 0) {
          $('cx-err').style.display = 'flex';
          $('cx-err').innerHTML =
            '<div class="cx-err-card">' +
            '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" ' +
            'stroke-linecap="round" stroke-linejoin="round"><use href="#i-warn"/></svg>' +
            '<b>Imagery unavailable</b>' +
            '<span>' + String(data).replace(/[<>]/g, '') +
            '<br>The tile feed may still be spinning up — try again shortly.</span></div>';
        }
      } else if (kind === 'frame') {
        if (i === 0) { updateClockUI(data); drawTimeline(); }
        paneTag(i, data.stamp);
      } else if (kind === 'ready') {
        if (i === 0) { drawTimeline(); updateHeader(); }
      } else if (kind === 'probe') {
        var pp = $('cx-pp-' + i);
        if (!pp) return;
        if (!data || data.btC == null) { pp.style.display = 'none'; return; }
        pp.style.display = 'block';
        pp.innerHTML = '<b>' + data.btC.toFixed(1) + ' °C</b> · ' +
          (data.btC + 273.15).toFixed(1) + ' K<br><span>' +
          data.lat.toFixed(2) + '°, ' + data.lon.toFixed(2) + '°</span>';
      } else if (kind === 'product-missing') {
        if (i === 0) flash('no data yet for that field — box emit pending');
      }
    };
  }

  // RGB interpretation keys (CIRA/RAMMB quick-guide semantics, the same
  // sources the recipes were verified against) — every cbar-less field gets
  // one so no pane is ever bare. Swatch hex ≈ the product's canonical hue.
  var LEGENDS = {
    truecolor: [['#e8e8e8', 'Cloud'], ['#3e5c33', 'Vegetation'],
                ['#b09a6a', 'Bare ground'], ['#0b2740', 'Ocean']],
    daylandcloud: [['#7fd4d4', 'Ice cloud / snow'], ['#e8e8e8', 'Water cloud'],
                   ['#3f6b35', 'Vegetation'], ['#0b2740', 'Water']],
    airmass: [['#c0392b', 'Dry stratospheric air'], ['#3f9b4c', 'Moist tropical air'],
              ['#3a6fd8', 'Cool moist air'], ['#e8e8e8', 'Thick high cloud']],
    dust: [['#d452c4', 'Lofted dust'], ['#8a1f1f', 'Thick high cloud'],
           ['#8fb6c9', 'Moist low levels']],
    ash: [['#d452c4', 'Ash / dust plume'], ['#b8d44f', 'SO₂-rich plume'],
          ['#8a1f1f', 'Thick ice cloud']],
    dayconvection: [['#e8d84a', 'Vigorous updrafts'], ['#7a3b2e', 'Mature glaciated tops'],
                    ['#2c4d6e', 'Weak / clear']],
    daycloudphase: [['#e8d84a', 'Thick ice / convection'], ['#49c8c8', 'Liquid water cloud'],
                    ['#59b04f', 'Snow / ice surface'], ['#101820', 'Clear']],
    nightmicro: [['#9fd4c8', 'Fog / low stratus'], ['#c74a3a', 'Thick cold cloud'],
                 ['#1a2340', 'Thin cirrus']],
    snowfog: [['#c0392b', 'Snow cover'], ['#e8e0b0', 'Fog / low cloud'],
              ['#182028', 'Clear / water']],
    firetemp: [['#ffffff', 'Hottest fire cores'], ['#e8b84a', 'Active fire'],
               ['#c0392b', 'Warm hotspot']]
  };

  // burned-in branded chrome per pane: title strip + valid time + color key.
  // Scalar/BT fields carry their colorbar; cbar-less fields get their
  // quick-guide interpretation legend — no pane is ever bare.
  function renderPaneChrome(i, stamp) {
    var pane = S.panes[i];
    if (!pane || !$('cx-pht-' + i)) return;
    var CF = window.CockpitFields;
    var badge = pane.el.querySelector('.cx-pane-lbadge');
    // MW/ASCAT FIELD panes: cockpit_fields owns the chrome content
    if (pane.kind === 'mw' || pane.kind === 'sc') {
      var ch = CF ? CF.chromeFor(pane) : null;
      if (ch) {
        $('cx-pht-' + i).textContent = ch.title;
        $('cx-phs-' + i).textContent = ch.sub || '';
        var cbx = $('cx-pc-' + i), keyx = $('cx-pk-' + i);
        cbx.style.display = 'none';
        if (ch.legend && ch.legend.rows.length) {
          keyx.innerHTML = ch.legend.rows.map(function (e) {
            return '<i><b style="background:' + e[0] + '"></b>' + e[1] + '</i>';
          }).join('') + '<i style="color:#8ea2bd">' + ch.legend.cap + '</i>';
          keyx.style.display = 'flex';
        } else keyx.style.display = 'none';
      }
      if (badge) badge.remove();
      return;
    }
    if (!pane.product) return;
    var p = pane.product;
    // MW/ASCAT as LAYERS over a tile field: a small provenance badge
    var lch = CF ? CF.chromeFor(pane) : null;
    if (lch && lch.layerBadge) {
      if (!badge) {
        badge = document.createElement('div');
        badge.className = 'cx-pane-lbadge';
        pane.el.appendChild(badge);
      }
      badge.textContent = lch.layerBadge;
    } else if (badge) badge.remove();
    // UNIFIED header (archive-render parity): centered SAT·INSTRUMENT·
    // CHANNEL·VALID title, right product·palette tag, per-pane watermark +
    // source attribution, min/max BT readout, colorbar right.
    var di = domainInfo(S.domain);
    var s = stamp ||
      (pane.tv && pane.tv.frames && pane.tv.frames[pane.tv.frameIdx]) ||
      (pane.tv && pane.tv.manifest && pane.tv.manifest.latest);
    $('cx-pht-' + i).textContent =
      di.satLabel + ' ' + di.sensor + ' · ' + p.title +
      (s ? ' · ' + fmtStamp(s).replace(/Z$/, '') + ' UTC' : '');
    $('cx-phs-' + i).textContent = di.label;
    $('cx-phg-' + i).textContent = di.scanProd + ' · ' + paletteTag(p);
    $('cx-phw-' + i).textContent =
      '@WeathermanAAA_  ·  ' + di.source + ' ' + di.satLabel + ' ' + di.sensor;
    paneMinMax(i);
    var cb = $('cx-pc-' + i), key = $('cx-pk-' + i);
    if (p.cbar) {
      $('cx-pci-' + i).src = p.cbar.img;
      $('cx-pct-' + i).innerHTML = p.cbar.ticks.map(function (t) {
        var pos = Math.max(0.02, Math.min(0.98, t.p)) * 100;
        return '<span style="top:' + pos.toFixed(2) + '%">' + t.t + '</span>';
      }).join('');
      cb.style.display = 'flex'; key.style.display = 'none';
    } else {
      cb.style.display = 'none';
      var lg = LEGENDS[p.key];
      if (lg) {
        key.innerHTML = lg.map(function (e) {
          return '<i><b style="background:' + e[0] + '"></b>' + e[1] + '</i>';
        }).join('');
        key.style.display = 'flex';
      } else key.style.display = 'none';
    }
  }
  function paneTag(i, stamp) { renderPaneChrome(i, stamp); }

  // min/max brightness temperature over the CURRENT viewport, read from the
  // frame's calibrated BT raster (the archive render's bottom-left readout,
  // reproduced from real data — IR/BT products only, hidden otherwise).
  function paneMinMax(i) {
    var pane = S.panes[i], el = $('cx-phm-' + i);
    if (!el) return;
    var tv = pane && pane.tv;
    var p = pane && pane.product;
    if (!tv || !tv.map || !tv.probe || !p || !p.bt) { el.style.display = 'none'; return; }
    var stamp = tv.frames[tv.frameIdx];
    if (!stamp) { el.style.display = 'none'; return; }
    var compute = function () {
      if (!tv.probe || !tv.probe._cache[stamp]) { el.style.display = 'none'; return; }
      var b = tv.map.getBounds();
      var w = b.getWest(), e = b.getEast(), sB = b.getSouth(), n = b.getNorth();
      var mn = Infinity, mx = -Infinity;
      var N = 36;
      for (var yi = 0; yi <= N; yi++) {
        for (var xi = 0; xi <= N; xi++) {
          var v = tv.probe.sample(stamp, w + (e - w) * xi / N, sB + (n - sB) * yi / N);
          if (v == null) continue;
          if (v < mn) mn = v;
          if (v > mx) mx = v;
        }
      }
      if (mn > mx) { el.style.display = 'none'; return; }
      el.textContent = 'min: ' + Math.round(mn) + '°C  ·  max: ' + Math.round(mx) + '°C';
      el.style.display = 'block';
    };
    if (tv.probe._cache[stamp]) compute();
    else tv.probe.load(stamp).then(compute).catch(function () { el.style.display = 'none'; });
  }

  // ---- linked cameras: pan/zoom one pane, all follow (toolbar-toggleable;
  // default ON). Feedback-guarded like syncViewers; per-pane views when off.
  function wireCameraSync(pane) {
    pane.tv.map.on('move', function () {
      if (!S.linked || S._camSync) return;
      S._camSync = true;
      var c = pane.tv.map.getCenter(), z = pane.tv.map.getZoom();
      S.panes.forEach(function (o) {
        if (o && o !== pane && o.ready)
          o.tv.map.jumpTo({ center: c, zoom: z });
      });
      S._camSync = false;
    });
  }

  function setActivePane(i) {
    if (!S.panes[i]) return;
    S.active = i;
    S.panes.forEach(function (p, k) {
      if (p) p.el.classList.toggle('cx-active', k === i && S.panes.filter(Boolean).length > 1);
    });
    updateHeader(); markFieldActive();
    if (window.CockpitFields) window.CockpitFields.syncControls();
  }

  function setPaneCount(n) {
    var cur = S.panes.filter(Boolean).length;
    if (n === cur) return;
    stopClock();
    if (n > cur) {
      for (var i = cur; i < n; i++) {
        var used = S.panes.filter(Boolean).map(function (p) { return p.product.key; });
        var av = availSet(S.domain);
        var pick = PANE_DEFAULTS.filter(function (k) {
          var p = productByKey(k);
          return used.indexOf(k) < 0 && p && (!av || av.has(p.id));
        })[0] || 'ir';
        makePane(i, productByKey(pick) || productSet(S.domain)[0]);
      }
    } else {
      for (var j = cur - 1; j >= n; j--) {
        var pane = S.panes[j];
        if (pane) { if (pane.tv && pane.tv.map) pane.tv.map.remove(); pane.el.remove(); }
        S.panes[j] = undefined;
      }
      S.panes.length = n;
      if (S.active >= n) setActivePane(0);
    }
    $('cx-panes').dataset.n = n;
    document.querySelectorAll('[data-panes]').forEach(function (b) {
      b.classList.toggle('on', +b.dataset.panes === n);
    });
    S.panes.forEach(function (p, k) { if (p) paneTag(k); });
    setActivePane(S.active < n ? S.active : 0);
  }

  function setPaneProduct(i, p, forceDomain) {
    var pane = S.panes[i];
    if (!pane || !pane.tv || !pane.tv.map) return;
    if (S.tm.on) {
      // Time Machine: the field only changes what the archive renders — the
      // live tile manifests are untouched until Live mode returns.
      if (!tmCurrentMap()[p.key]) { flash(document.body.classList.contains('cx-tm-deep')
        ? 'not available before 2017 — the deep archive is single-channel IR (Clean IR / Dvorak BD / WV)'
        : 'that field is live-only'); return; }
      pane.product = p;
      if (i === S.active) markFieldActive();
      tmRenderOnce();
      return;
    }
    var av = availSet(S.domain);
    if (av && !av.has(p.id)) return;
    flash('Loading ' + p.title + '…', true);
    pane.tv.setProduct(manifestUrlFor(p, S.domain), p).then(function () {
      pane.product = p;
      paneTag(i);
      if (i === S.active) { updateHeader(); markFieldActive(); }
      if (i === 0) drawTimeline();
      flash('');
    }).catch(function () {
      if (forceDomain) flash('no ' + domainInfo(S.domain).label + ' data yet for ' + p.title);
    });
  }

  // every pane owns its chrome now; "header" = re-render all panes
  function updateHeader() {
    S.panes.forEach(function (p, i) { if (p) renderPaneChrome(i); });
  }

  // ========================================================================
  // CLOCK — one transport drives all panes, synced by VALID TIME (pane 0
  // leads; followers show their own nearest-in-time frame). Slower/Faster
  // steps the fps ladder; dwell-on-newest is a Settings toggle.
  // ========================================================================
  var FPS_STEPS = [2, 3, 4, 6, 8, 10, 15];
  function lead() { return S.panes[0] && S.panes[0].tv; }

  function clockShow(idx) {
    if (S.tm.on) { tmShowFrame(idx); return; }
    var tv = lead();
    if (!tv || !tv.frames.length) return;
    var n = tv.frames.length;
    idx = ((idx % n) + n) % n;
    tv.showFrame(idx);
    var stamp = tv.frames[idx];
    for (var k = 1; k < S.panes.length; k++)
      if (S.panes[k] && S.panes[k].ready) S.panes[k].tv.showStamp(stamp);
    // MW panes follow the clock to their nearest-in-time overpass
    if (window.CockpitFields) window.CockpitFields.timeSync(stamp);
  }
  function clockIdx() { return S.tm.on ? S.tm.idx : (lead() ? lead().frameIdx : 0); }
  function startClock() {
    var tv = lead();
    if (S.playing || framesList().length < 2) {
      if (framesList().length < 2)
        flash(S.tm.on ? 'load an archive loop first'
                      : '1 frame — the loop fills as the emit cron runs');
      return;
    }
    S.playing = true; S.last = 0;
    $('cx-play').classList.add('playing', 'on');
    function step(t) {
      if (!S.playing) return;
      if (!S.last) S.last = t;
      var iv = 1000 / S.fps;
      if (S.dwell && clockIdx() === framesList().length - 1) iv *= 6;
      if (t - S.last >= iv) { S.last = t; clockShow(clockIdx() + 1); }
      S.raf = requestAnimationFrame(step);
    }
    S.raf = requestAnimationFrame(step);
  }
  function stopClock() {
    S.playing = false;
    if (S.raf) cancelAnimationFrame(S.raf);
    $('cx-play').classList.remove('playing', 'on');
  }
  function speed(delta) {
    var i = FPS_STEPS.indexOf(S.fps);
    if (i < 0) i = 3;
    i = Math.max(0, Math.min(FPS_STEPS.length - 1, i + delta));
    S.fps = FPS_STEPS[i];
    $('cx-speed').textContent = S.fps + ' fps';
  }

  function updateClockUI(data) {
    $('cx-valid').textContent = fmtStamp(data.stamp);
    $('cx-count').textContent = (data.idx + 1) + ' / ' + data.n;
  }

  // ---- timeline scrubber (canvas ruler; hover-scrub, click-jump) ----------
  function drawTimeline() {
    var cv = $('cx-tl'), tv = lead();
    if (!cv || !tv) return;
    var frames = framesList(), fi = clockIdx();
    var dpr = window.devicePixelRatio || 1;
    var w = cv.clientWidth, h = cv.clientHeight;
    if (!w) return;
    cv.width = w * dpr; cv.height = h * dpr;
    var ctx = cv.getContext('2d');
    ctx.scale(dpr, dpr); ctx.clearRect(0, 0, w, h);
    var n = frames.length;
    ctx.fillStyle = '#141b25'; ctx.fillRect(0, h / 2 - 3, w, 6);
    if (!n) return;
    // progress fill to the current frame
    var fx = n > 1 ? (fi / (n - 1)) * w : w;
    ctx.fillStyle = 'rgba(73,182,200,.35)'; ctx.fillRect(0, h / 2 - 3, fx, 6);
    ctx.fillStyle = '#3a4756';
    for (var i = 0; i < n; i++) {
      var x = n > 1 ? (i / (n - 1)) * (w - 2) + 1 : w / 2;
      ctx.fillRect(x - 0.5, h / 2 - (i % 6 === 0 ? 8 : 5), 1, i % 6 === 0 ? 16 : 10);
    }
    // current-frame cursor
    ctx.fillStyle = '#49b6c8';
    ctx.fillRect(fx - 1.25, 2, 2.5, h - 4);
    // end labels
    ctx.fillStyle = '#71809a'; ctx.font = '600 9.5px Metropolis,system-ui,sans-serif';
    ctx.textBaseline = 'top';
    ctx.fillText(fmtStamp(frames[0]).slice(5), 2, h - 12);
    var lastLbl = fmtStamp(frames[n - 1]).slice(5);
    ctx.fillText(lastLbl, w - ctx.measureText(lastLbl).width - 2, h - 12);
    if (n === 1 && !S.tm.on) {
      ctx.fillStyle = '#5b6879';
      var note = 'single frame — the loop fills as new scans land';
      ctx.fillText(note, (w - ctx.measureText(note).width) / 2, 2);
    }
  }
  function wireTimeline() {
    var cv = $('cx-tl');
    var wasPlaying = false, scrubbing = false;
    function idxAt(e) {
      var r = cv.getBoundingClientRect(), frames = framesList();
      if (!frames.length) return 0;
      var f = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
      return Math.round(f * (frames.length - 1));
    }
    cv.addEventListener('mouseenter', function () {
      scrubbing = true; wasPlaying = S.playing; if (S.playing) stopClock();
    });
    cv.addEventListener('mousemove', function (e) { if (scrubbing) clockShow(idxAt(e)); });
    cv.addEventListener('mouseleave', function () {
      scrubbing = false; if (wasPlaying) startClock();
    });
    cv.addEventListener('click', function (e) { wasPlaying = false; clockShow(idxAt(e)); });
    window.addEventListener('resize', drawTimeline);
  }

  // ========================================================================
  // TOOLS — measure (real), sketch (real, first pass), select-on-map (real),
  // chart/icons/time-machine (honest stubs). All operate on the ACTIVE pane.
  // ========================================================================
  function armTool(name, btnEl) {
    var to = (S.tool === name) ? null : name;
    disarmTools();
    S.tool = to;
    if (btnEl && to) btnEl.classList.add('on');
    if (to === 'measure') startMeasure();
    if (to === 'sketch') startSketch();
    if (to === 'selectmap') flash('click the map to frame that spot');
  }
  function disarmTools() {
    S.tool = null;
    ['cx-measure', 'cx-sketch'].forEach(function (id) { $(id).classList.remove('on'); });
    document.querySelectorAll('[data-domain="selectmap"]').forEach(function (el) {
      el.classList.remove('on');
    });
    endMeasure(); endSketch();
  }

  // -- measure: two clicks, geodesic distance -------------------------------
  function haversineKm(a, b) {
    var R = 6371, dLa = (b.lat - a.lat) * Math.PI / 180, dLo = (b.lng - a.lng) * Math.PI / 180;
    var la1 = a.lat * Math.PI / 180, la2 = b.lat * Math.PI / 180;
    var h = Math.sin(dLa / 2) * Math.sin(dLa / 2) +
            Math.cos(la1) * Math.cos(la2) * Math.sin(dLo / 2) * Math.sin(dLo / 2);
    return 2 * R * Math.asin(Math.sqrt(h));
  }
  function startMeasure() {
    var pane = S.panes[S.active];
    if (!pane || !pane.ready) return;
    var map = pane.tv.map, pts = [], label = null;
    pane.tv.setInspector(false);      // clicks belong to the ruler while armed
    function ensureLayer() {
      if (map.getSource('cx-measure')) return;
      map.addSource('cx-measure', { type: 'geojson',
        data: { type: 'FeatureCollection', features: [] } });
      map.addLayer({ id: 'cx-measure', type: 'line', source: 'cx-measure',
        paint: { 'line-color': '#ffb347', 'line-width': 2, 'line-dasharray': [2, 1.5] } });
    }
    function setLine(a, b) {
      ensureLayer();
      map.getSource('cx-measure').setData({ type: 'FeatureCollection', features: [{
        type: 'Feature', properties: {},
        geometry: { type: 'LineString', coordinates: [[a.lng, a.lat], [b.lng, b.lat]] }
      }]});
      var km = haversineKm(a, b);
      var txt = km >= 100 ? Math.round(km) + ' km' : km.toFixed(1) + ' km';
      txt += ' · ' + Math.round(km * 0.53996) + ' nmi';
      if (!label) {
        var el = document.createElement('div'); el.className = 'cx-mlabel';
        label = new maplibregl.Marker({ element: el })
          .setLngLat([(a.lng + b.lng) / 2, (a.lat + b.lat) / 2]).addTo(map);
      }
      label.setLngLat([(a.lng + b.lng) / 2, (a.lat + b.lat) / 2]);
      label.getElement().textContent = txt;
    }
    function onClick(e) {
      pts.push(e.lngLat);
      if (pts.length === 1) flash('click the far end');
      if (pts.length === 2) { setLine(pts[0], pts[1]); flash(''); }
      if (pts.length === 3) { pts = [pts[2]]; }  // start a fresh measurement
    }
    function onMove(e) { if (pts.length === 1) setLine(pts[0], e.lngLat); }
    map.on('click', onClick); map.on('mousemove', onMove);
    S.measure = { map: map, tv: pane.tv, onClick: onClick, onMove: onMove,
      label: function () { return label; } };
    flash('Measure: click the start point');
  }
  function endMeasure() {
    var m = S.measure;
    if (!m) return;
    m.map.off('click', m.onClick); m.map.off('mousemove', m.onMove);
    if (m.map.getLayer('cx-measure')) m.map.removeLayer('cx-measure');
    if (m.map.getSource('cx-measure')) m.map.removeSource('cx-measure');
    var l = m.label(); if (l) l.remove();
    m.tv.setInspector($('cx-inspect').classList.contains('on'));
    S.measure = null; flash('');
  }

  // -- freehand sketch: drag to draw annotation lines (first pass) ----------
  function startSketch() {
    var pane = S.panes[S.active];
    if (!pane || !pane.ready) return;
    var map = pane.tv.map, drawing = false, line = null;
    var feats = { type: 'FeatureCollection', features: [] };
    pane.tv.setInspector(false);
    if (!map.getSource('cx-sketch')) {
      map.addSource('cx-sketch', { type: 'geojson', data: feats });
      map.addLayer({ id: 'cx-sketch', type: 'line', source: 'cx-sketch',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#ffb347', 'line-width': 2.25 } });
    } else {
      feats = null;   // keep existing sketches; re-read on first stroke
    }
    function data() {
      if (!feats) feats = map.getSource('cx-sketch')._data ||
        { type: 'FeatureCollection', features: [] };
      return feats;
    }
    function down(e) {
      drawing = true; map.dragPan.disable();
      line = { type: 'Feature', properties: {},
        geometry: { type: 'LineString', coordinates: [[e.lngLat.lng, e.lngLat.lat]] } };
      data().features.push(line);
      e.preventDefault();
    }
    function move(e) {
      if (!drawing || !line) return;
      line.geometry.coordinates.push([e.lngLat.lng, e.lngLat.lat]);
      map.getSource('cx-sketch').setData(data());
    }
    function up() { drawing = false; line = null; map.dragPan.enable(); }
    map.on('mousedown', down); map.on('mousemove', move); map.on('mouseup', up);
    S.sketch = { map: map, tv: pane.tv, down: down, move: move, up: up };
    flash('Sketch: drag to draw · Clear wipes');
  }
  function endSketch() {
    var sk = S.sketch;
    if (!sk) return;
    sk.map.off('mousedown', sk.down); sk.map.off('mousemove', sk.move); sk.map.off('mouseup', sk.up);
    sk.map.dragPan.enable();
    sk.tv.setInspector($('cx-inspect').classList.contains('on'));
    S.sketch = null; flash('');
    // strokes stay on the map until Clear/Reset — annotation, not a mode
  }
  function clearSketch() {
    S.panes.forEach(function (p) {
      if (!p || !p.tv || !p.tv.map) return;
      var map = p.tv.map;
      if (map.getLayer('cx-sketch')) map.removeLayer('cx-sketch');
      if (map.getSource('cx-sketch')) map.removeSource('cx-sketch');
      p.tv.clearPins();
    });
  }

  // -- select-on-map: one click frames a ~8°x5° box on the spot -------------
  function wireSelectMap() {
    // one persistent capture on each pane map; acts only when the tool is armed
    S.panes.forEach(function (p) {
      if (!p || !p.tv || !p.tv.map || p._selWired) return;
      p._selWired = true;
      p.tv.map.on('click', function (e) {
        if (S.tool !== 'selectmap') return;
        var ln = e.lngLat.lng, la = e.lngLat.lat;
        p.tv.map.fitBounds([[ln - 4, la - 2.5], [ln + 4, la + 2.5]],
                           { padding: 10, duration: 450 });
        disarmTools();
      });
    });
  }

  function armDrawBox() {
    var pane = S.panes[S.active];
    if (pane && pane.ready) {
      if (!pane._drawWired) { pane._drawWired = true; pane.tv.enableDrawBox(null); }
      pane.tv._armed = true;
      flash('drag a box to frame it (or shift-drag anytime)');
    }
  }

  // -- share: permalink = URL state ------------------------------------------
  function shareURL() {
    var u = new URL(location.href.split('?')[0]);
    var tv = lead();
    var names = S.panes.filter(Boolean).map(function (p) { return p.product.key; });
    u.searchParams.set('product', names[0]);
    if (names.length > 1) u.searchParams.set('pp', names.join(','));
    u.searchParams.set('panes', String(names.length));
    if (S.domain !== 'conus') u.searchParams.set('domain', S.domain);
    if (tv && tv.map) {
      var c = tv.map.getCenter(), z = tv.map.getZoom();
      u.searchParams.set('cam', c.lng.toFixed(3) + ',' + c.lat.toFixed(3) + ',' + z.toFixed(2));
    }
    if (tv && tv.frames[tv.frameIdx]) u.searchParams.set('t', tv.frames[tv.frameIdx]);
    var s = u.toString();
    (navigator.clipboard ? navigator.clipboard.writeText(s) : Promise.reject())
      .then(function () { flash('link copied'); })
      .catch(function () { prompt('Permalink:', s); });
  }
  function applyURLState() {
    var cam = params.get('cam');
    var tv = lead();
    if (cam && tv && tv.map) {
      var p = cam.split(',').map(Number);
      if (p.length === 3 && p.every(isFinite))
        tv.map.jumpTo({ center: [p[0], p[1]], zoom: p[2] });
    }
    var t = params.get('t');
    if (t && tv) tv.showStamp(t);
    var n = parseInt(params.get('panes') || '1', 10);
    var pp = (params.get('pp') || '').split(',').filter(Boolean);
    if (n > 1) {
      setPaneCount(Math.min(4, n));
      pp.slice(1).forEach(function (k, i) {
        var pr = productByKey(k);
        var waitReady = function () {
          var pane = S.panes[i + 1];
          if (pane && pane.ready && pr) setPaneProduct(i + 1, pr);
          else setTimeout(waitReady, 300);
        };
        if (pr) waitReady();
      });
    }
  }

  // -- exports: composite the burned-in chrome so a saved pane is a finished
  // branded graphic (same strip/watermark/colorbar the overlay shows) --------
  function drawChrome(ctx, pane, w, h, stamp) {
    var CF = window.CockpitFields;
    var dpr = w / pane.tv.map.getCanvas().clientWidth || 1;
    var f = function (px) { return Math.round(px * dpr); };
    var grad = ctx.createLinearGradient(0, 0, 0, f(56));
    grad.addColorStop(0, 'rgba(10,13,18,.84)'); grad.addColorStop(1, 'rgba(10,13,18,0)');
    ctx.fillStyle = grad; ctx.fillRect(0, 0, w, f(56));
    ctx.textBaseline = 'top';
    // MW/ASCAT field panes: their own strip + key + credit (same look)
    if (pane.kind === 'mw' || pane.kind === 'sc') {
      var ch = CF ? CF.chromeFor(pane) : null;
      if (!ch) return;
      ctx.fillStyle = '#dbe3ec';
      ctx.font = '700 ' + f(13) + 'px Metropolis,system-ui,sans-serif';
      ctx.fillText(ch.title, f(12), f(9));
      ctx.fillStyle = '#9aa8b8';
      ctx.font = '500 ' + f(10.5) + 'px Metropolis,system-ui,sans-serif';
      if (ch.sub) ctx.fillText(ch.sub, f(12), f(27));
      ctx.fillStyle = 'rgba(255,255,255,.48)';
      ctx.font = '700 ' + f(13.5) + 'px Metropolis,system-ui,sans-serif';
      var brandX = '@WeathermanAAA_';
      ctx.fillText(brandX, w - ctx.measureText(brandX).width - f(12), f(9));
      if (ch.legend && ch.legend.rows.length) {
        var rows = ch.legend.rows.concat([[null, ch.legend.cap]]);
        var lx = w - f(158), ly = f(46), lh = rows.length * f(15) + f(10);
        ctx.fillStyle = 'rgba(10,13,18,.72)';
        ctx.fillRect(lx, ly, f(150), lh);
        ctx.strokeStyle = 'rgba(255,255,255,.12)';
        ctx.strokeRect(lx + .5, ly + .5, f(150) - 1, lh - 1);
        ctx.font = '500 ' + f(9.5) + 'px Metropolis,system-ui,sans-serif';
        ctx.textBaseline = 'middle';
        rows.forEach(function (e, k) {
          var ry = ly + f(8) + k * f(15);
          if (e[0]) {
            ctx.fillStyle = e[0]; ctx.fillRect(lx + f(8), ry - f(4.5), f(9), f(9));
            ctx.strokeStyle = 'rgba(255,255,255,.25)';
            ctx.strokeRect(lx + f(8) + .5, ry - f(4.5) + .5, f(9) - 1, f(9) - 1);
            ctx.fillStyle = '#c6d0da'; ctx.fillText(e[1], lx + f(22), ry);
          } else {
            ctx.fillStyle = '#8ea2bd'; ctx.fillText(e[1], lx + f(8), ry);
          }
        });
        ctx.textBaseline = 'top';
      }
      if (ch.credit) {
        ctx.fillStyle = '#8ea2bd';
        ctx.font = '600 ' + f(9.5) + 'px Metropolis,system-ui,sans-serif';
        ctx.fillText(ch.credit, w - ctx.measureText(ch.credit).width - f(10), h - f(18));
      }
      return;
    }
    // UNIFIED header, export form (same layout as the live overlay + the
    // archive render): centered title, right product·palette tag, top-left
    // watermark+attribution, bottom-left min/max BT
    var p = pane.product;
    var dinf = domainInfo(S.domain);
    var title = dinf.satLabel + ' ' + dinf.sensor + ' · ' + p.title +
      (stamp ? ' · ' + fmtStamp(stamp).replace(/Z$/, '') + ' UTC' : '');
    ctx.fillStyle = '#dbe3ec';
    ctx.font = '700 ' + f(13) + 'px Metropolis,system-ui,sans-serif';
    ctx.fillText(title, Math.max(f(12), (w - ctx.measureText(title).width) / 2), f(9));
    ctx.fillStyle = '#9aa8b8';
    ctx.font = '500 ' + f(10.5) + 'px Metropolis,system-ui,sans-serif';
    var sub = dinf.label;
    ctx.fillText(sub, (w - ctx.measureText(sub).width) / 2, f(27));
    ctx.fillStyle = '#49b6c8';
    ctx.font = '600 ' + f(10) + 'px Metropolis,system-ui,sans-serif';
    var tag = dinf.scanProd + ' · ' + paletteTag(p);
    ctx.fillText(tag, w - ctx.measureText(tag).width - f(12), f(11));
    var wmTxt = '@WeathermanAAA_  ·  ' + dinf.source + ' ' + dinf.satLabel + ' ' + dinf.sensor;
    ctx.fillStyle = 'rgba(0,0,0,.4)';
    ctx.fillRect(f(8), f(40), ctx.measureText(wmTxt).width + f(12), f(17));
    ctx.fillStyle = '#49b6c8';
    ctx.fillText(wmTxt, f(14), f(44));
    var mmEl = $('cx-phm-' + S.panes.indexOf(pane));
    if (mmEl && mmEl.style.display !== 'none' && mmEl.textContent) {
      var mm = mmEl.textContent;
      ctx.fillStyle = 'rgba(0,0,0,.4)';
      ctx.fillRect(f(8), h - f(26), ctx.measureText(mm).width + f(12), f(17));
      ctx.fillStyle = '#49b6c8';
      ctx.fillText(mm, f(14), h - f(22));
    }
    // MW/ASCAT layer provenance badge rides into the export too
    var lch = CF ? CF.chromeFor(pane) : null;
    if (lch && lch.layerBadge) {
      ctx.fillStyle = '#bcdcff';
      ctx.font = '600 ' + f(10) + 'px Metropolis,system-ui,sans-serif';
      ctx.fillText(lch.layerBadge, f(12), f(62));
    }
    // color key: colorbar for scalar/BT fields, quick-guide legend otherwise
    var lg = !p.cbar && LEGENDS[p.key];
    if (lg) {
      var lx = w - f(158), ly = f(46), lh = lg.length * f(15) + f(10);
      ctx.fillStyle = 'rgba(10,13,18,.72)';
      ctx.fillRect(lx, ly, f(150), lh);
      ctx.strokeStyle = 'rgba(255,255,255,.12)';
      ctx.strokeRect(lx + .5, ly + .5, f(150) - 1, lh - 1);
      ctx.font = '500 ' + f(9.5) + 'px Metropolis,system-ui,sans-serif';
      ctx.textBaseline = 'middle';
      lg.forEach(function (e, k) {
        var ry = ly + f(8) + k * f(15);
        ctx.fillStyle = e[0]; ctx.fillRect(lx + f(8), ry - f(4.5), f(9), f(9));
        ctx.strokeStyle = 'rgba(255,255,255,.25)';
        ctx.strokeRect(lx + f(8) + .5, ry - f(4.5) + .5, f(9) - 1, f(9) - 1);
        ctx.fillStyle = '#c6d0da'; ctx.fillText(e[1], lx + f(22), ry);
      });
      ctx.textBaseline = 'top';
    }
    var img = $('cx-pci-' + S.panes.indexOf(pane));
    if (p.cbar && img && img.complete && img.naturalWidth) {
      var bw = f(10), bh = f(150), bx = w - bw - f(10), by = f(46);
      ctx.drawImage(img, bx, by, bw, bh);
      ctx.strokeStyle = 'rgba(255,255,255,.25)'; ctx.lineWidth = 1;
      ctx.strokeRect(bx - .5, by - .5, bw + 1, bh + 1);
      ctx.fillStyle = '#b6c0cc';
      ctx.font = '500 ' + f(9) + 'px Metropolis,system-ui,sans-serif';
      ctx.textBaseline = 'middle';
      p.cbar.ticks.forEach(function (t) {
        var ty = by + Math.max(.02, Math.min(.98, t.p)) * bh;
        ctx.fillText(t.t, bx - ctx.measureText(t.t).width - f(4), ty);
      });
      ctx.textBaseline = 'top';
    }
  }
  function compositeCanvas(pane) {
    var src = pane.tv.map.getCanvas();
    var c = document.createElement('canvas');
    c.width = src.width; c.height = src.height;
    return c;
  }
  function exportPNG() {
    var pane = S.panes[S.active];
    if (!pane || !pane.ready) return;
    if (S.tm.on) {
      // the archive render IS the finished branded graphic — save it directly
      var im = $('cx-tm-img-' + S.active);
      if (!im || !im.src) { flash('render an archive view first'); return; }
      var a = document.createElement('a');
      a.href = im.src;
      a.download = 'archive_' + ($('cx-tm-time').value || 'view').replace(/[-:]/g, '') + '.png';
      a.click(); return;
    }
    var map = pane.tv.map;
    map.triggerRepaint();
    requestAnimationFrame(function () {
      var src = map.getCanvas(), c = compositeCanvas(pane), ctx = c.getContext('2d');
      ctx.drawImage(src, 0, 0);
      // ASCAT barbs live on an overlay canvas, not in the GL canvas
      if (window.CockpitFields) window.CockpitFields.compositeOverlays(ctx, pane, c.width, c.height);
      drawChrome(ctx, pane, c.width, c.height, pane.tv.frames[pane.tv.frameIdx]);
      c.toBlob(function (blob) {
        if (!blob) { flash('PNG export failed'); return; }
        var name = (pane.kind === 'mw' || pane.kind === 'sc')
          ? pane.fieldKey : pane.product.id;
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = name + '_' +
          (pane.tv.frames[pane.tv.frameIdx] || 'latest') + '.png';
        a.click(); flash('');
      }, 'image/png');
    });
  }
  function exportLoop(btn) {
    if (S.tm.on) { exportTMLoop(btn); return; }
    var tv = lead(), pane = S.panes[0];
    if (!tv || btn.dataset.busy) return;
    if (tv.frames.length < 2) { flash('1 frame — loop export needs the cron backfill'); return; }
    btn.dataset.busy = '1'; stopClock();
    // record a COMPOSITE canvas: map frames + the branded chrome, redrawn
    // every animation tick so the valid time tracks the playing frame.
    var c = compositeCanvas(pane), ctx = c.getContext('2d');
    var compositing = true;
    (function tick() {
      if (!compositing) return;
      ctx.drawImage(pane.tv.map.getCanvas(), 0, 0, c.width, c.height);
      if (window.CockpitFields) window.CockpitFields.compositeOverlays(ctx, pane, c.width, c.height);
      drawChrome(ctx, pane, c.width, c.height, tv.frames[tv.frameIdx]);
      requestAnimationFrame(tick);
    })();
    var finish = function (label) {
      compositing = false;
      btn.querySelector('.lbl').textContent = label; delete btn.dataset.busy;
    };
    tv.exportWebM({
      captureCanvas: c,
      maxBytes: S.hqExport ? 24e6 : 9e6,
      maxBitrate: S.hqExport ? 12e6 : 6e6,
      onProgress: function (i, n) {
        btn.querySelector('.lbl').textContent = i < n ? i + '/' + n : 'encoding…';
      },
      onDone: function (blob) {
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = pane.product.id + '_loop.webm';
        a.click(); finish('Loop');
      },
      onError: function (m) { finish(m); }
    });
  }

  function resetAll() {
    if (S.tm.on) exitTM();
    disarmTools(); stopClock(); clearSketch();
    // drop MW/ASCAT fields + layers back to boot state
    if (window.CockpitFields) {
      S.panes.forEach(function (pane, i) {
        if (!pane) return;
        if (pane.mw) { pane.mw.on = false; }
        if (pane.sc) { pane.sc.on = false; }
        if (pane.kind === 'mw' || pane.kind === 'sc') window.CockpitFields.clearPaneField(i);
        else if (pane.tv && pane.tv.map) { window.CockpitFields.setLayer(i, 'mw', false); window.CockpitFields.setLayer(i, 'sc', false); }
      });
      window.CockpitFields.syncControls();
    }
    setPaneCount(1);
    if (S.domain !== 'conus') {
      var wasCross = domainInfo(S.domain).sat !== 'goes19';
      S.domain = 'conus'; markDomain();
      if (wasCross) rebuildProductRows();
      var pane0 = S.panes[0];
      if (pane0 && pane0.tv && pane0.tv.map && pane0.tv.map.setRenderWorldCopies)
        pane0.tv.map.setRenderWorldCopies(false);
    }
    var p = productByKey('ir', 'conus') || PRODUCTS[0];
    if (S.panes[0].product.id !== p.id) setPaneProduct(0, p);
    var tv = lead();
    if (tv && tv.map) { tv.fitData(); tv.clearPins(); clockShow(tv.frames.length - 1); }
    history.replaceState(null, '', location.pathname);
    flash('');
  }

  // ========================================================================
  // TIME MACHINE — render-on-demand from the GOES-R archive via the EXISTING
  // custom-snapshot backend (smallest-covering-product crop logic lives
  // server-side; we only map cockpit state onto its request shape). The
  // returned image is a finished branded graphic (own strip/colorbar), so
  // panes show it as-is and hide their live chrome. Field = rail, region =
  // viewport, overlays = toggles, resolution = the ≤10MB/HQ toggle; the only
  // new control is TIME. Live tiles are untouched — Live mode = exit.
  // ========================================================================
  var RENDER_API = 'https://web-production-b88d.up.railway.app/render';
  var TM_MAP = {   // cockpit field -> what the archive backend can serve
    truecolor: { channel: 'true_color' },
    ir: { channel: 'clean_ir', enh: 'rainbow_ir' },
    irbd: { channel: 'clean_ir', enh: 'dvorak' },
    c02: { channel: 'visible_red', enh: 'ir_gray' },
    c07: { channel: 'shortwave_ir', enh: 'ir_gray' },
    c08: { channel: 'wv_upper', enh: 'ir_gray' },
    c10: { channel: 'wv_lower', enh: 'ir_gray' },
    c14: { channel: 'ir_window', enh: 'rainbow_ir' }
  };
  // DEEP ARCHIVE (pre-2017): the backend serves GridSat-B1 — a single-channel
  // 11 µm IR (+6.7 µm WV) geostationary composite, GLOBAL, 3-hourly, ~8 km.
  // Only IR-based fields exist in that era; the backend bakes the honest era
  // header ("GridSat-B1 · 11 µm IR window · 3-hourly · ~8 km").
  var TM_ABI_START = Date.parse('2017-03-01T00:00:00Z');
  var TM_DEEP_MAP = {
    ir: { channel: 'clean_ir', enh: 'rainbow_ir' },
    irbd: { channel: 'clean_ir', enh: 'dvorak' },
    c08: { channel: 'wv_upper', enh: 'ir_gray' }
  };
  function tmDeepEra(iso) { return Date.parse(iso) < TM_ABI_START; }
  function tmMapFor(iso) { return tmDeepEra(iso) ? TM_DEEP_MAP : TM_MAP; }
  function tmSyncEraUI() {
    var v = $('cx-tm-time').value;
    var deep = S.tm.on && v && tmDeepEra(v + ':00Z');
    document.body.classList.toggle('cx-tm-deep', !!deep);
    document.querySelectorAll('.cx-field[data-key]').forEach(function (el) {
      if (TM_DEEP_MAP[el.dataset.key]) el.dataset.tmDeep = '1';
      else delete el.dataset.tmDeep;
    });
  }
  var TM_MAX_LOOP = 12;        // archive renders are rate-limited (~10/min)
  var TM_PACE_MS = 6500;
  // frames = the timeline's stamp list; byPane[i] = per-pane rendered frames
  // (multi-pane loops: every servable pane loads the same stamps, paced)
  S.tm = { on: false, frames: [], byPane: {}, idx: 0, busy: false };

  function tmStamp(v) { return v.replace(/[-:]/g, '') + '00Z'; }  // input -> STAMP_FMT
  function framesList() {
    return S.tm.on ? S.tm.frames.map(function (f) { return f.stamp; })
                   : (lead() ? lead().frames : []);
  }
  function tmShowFrame(idx) {
    var n = S.tm.frames.length;
    if (!n) return;
    S.tm.idx = ((idx % n) + n) % n;
    var f = S.tm.frames[S.tm.idx];
    // every pane with a loaded loop shows ITS render of this stamp
    S.panes.forEach(function (pane, i) {
      if (!pane) return;
      var fp = S.tm.byPane[i] && S.tm.byPane[i][S.tm.idx];
      if (!fp) return;
      var im = $('cx-tm-img-' + i);
      if (im) { im.src = fp.url; im.style.display = 'block'; }
      pane.el.classList.add('cx-tm-showing');
    });
    $('cx-valid').textContent = fmtStamp(f.stamp);
    $('cx-count').textContent = (S.tm.idx + 1) + ' / ' + n + ' · archive';
    drawTimeline();
    // per-frame diagnostics: TC-Diagnostics recomputes for the scrubbed frame
    if (window.TCDiag && window.TCDiag.onArchiveFrame)
      window.TCDiag.onArchiveFrame(f.stamp);
  }

  // 12 h scrub window centered on the rendered date (+6 h buffer each end):
  // CENTER-OUT render-ahead so the scrubbed date is usable immediately and
  // the buffers fill behind it, paced under the backend rate limit. Frames
  // stay time-sorted (the timeline scrubber drags through real archive data).
  function insertFrameSorted(rec, paneIdx) {
    var k = 0;
    while (k < S.tm.frames.length && S.tm.frames[k].stamp < rec.stamp) k++;
    S.tm.frames.splice(k, 0, rec);
    if (k <= S.tm.idx && S.tm.frames.length > 1) S.tm.idx++;
    S.tm.byPane[paneIdx] = S.tm.frames;    // single-pane window: same list
  }
  function tmLoadWindow(centerIso) {
    if (!S.tm.on || S.tm.windowBusy) return;
    var t0 = Date.parse(centerIso);
    if (S.tm.windowFor != null && Math.abs(t0 - S.tm.windowFor) < 9 * 3600e3) return;
    var pane = S.panes[S.active];
    var map = tmMapFor(centerIso);
    if (!pane || !pane.product || !map[pane.product.key]) return;
    tmClearLoop();
    S.tm.windowFor = t0;
    S.tm.windowBusy = true;
    var deep = tmDeepEra(centerIso);
    var stepMs = (deep ? 180 : 90) * 60e3;   // era cadence: GridSat 3-hourly
    var half = 12 * 3600e3;
    var times = [];
    for (var t = t0 - half; t <= t0 + half; t += stepMs) times.push(t);
    times.sort(function (a, b) { return Math.abs(a - t0) - Math.abs(b - t0); });
    var done = 0;
    var next = function () {
      if (!S.tm.on || S.tm.windowFor !== t0 || done >= times.length) {
        S.tm.windowBusy = false;
        if (S.tm.on && S.tm.windowFor === t0)
          flash(S.tm.frames.length + '-frame archive window loaded — scrub the timeline, play, or export the loop');
        return;
      }
      var iso = new Date(times[done]).toISOString().slice(0, 16);
      done++;
      var stamp = tmStamp(iso);
      if (S.tm.frames.some(function (f) { return f.stamp === stamp; })) { next(); return; }
      flash('archive window: ' + S.tm.frames.length + '/' + times.length +
            ' frames — scrub anytime while it fills', true);
      tmFetchBody(tmBody(pane, iso + ':00Z', 'low')).then(function (blob) {
        if (!S.tm.on || S.tm.windowFor !== t0) { S.tm.windowBusy = false; return; }
        insertFrameSorted({ stamp: stamp, url: URL.createObjectURL(blob) }, S.active);
        drawTimeline();
        setTimeout(next, TM_PACE_MS);      // stay under the backend rate limit
      }).catch(function () { setTimeout(next, TM_PACE_MS); });
    };
    next();
  }
  var TM_MAX_DEG = 60;   // per-axis cap, kept below the backend's 80° limit
  // GOES-East usable window: the renderer crashes on off-disk (limb-masked)
  // extents, so the request box never leaves this envelope.
  var TM_SAFE = { w: -145, e: -15, s: -55, n: 55 };
  function tmBody(pane, timeIso, quality) {
    var deep = tmDeepEra(timeIso);
    var map = tmMapFor(timeIso);
    var m = map[pane.product.key] || map.ir;
    var b = pane.tv.map.getBounds();
    var w = b.getWest(), s = b.getSouth(), e = b.getEast(), n = b.getNorth();
    // an over-wide viewport (e.g. the boot fit) exceeds the render cap —
    // clamp around the view center rather than failing the request
    if (e - w > TM_MAX_DEG) { var cx = (e + w) / 2; w = cx - TM_MAX_DEG / 2; e = cx + TM_MAX_DEG / 2; }
    if (n - s > TM_MAX_DEG) { var cy = (n + s) / 2; s = cy - TM_MAX_DEG / 2; n = cy + TM_MAX_DEG / 2; }
    if (deep) {
      // GridSat-B1 is GLOBAL (no disk limb) but covers 70°S–70°N
      s = Math.max(-70, s); n = Math.min(70, n);
      if (!(e - w > 1 && n - s > 1)) return null;
    } else {
      w = Math.max(TM_SAFE.w, w); e = Math.min(TM_SAFE.e, e);
      s = Math.max(TM_SAFE.s, s); n = Math.min(TM_SAFE.n, n);
      if (!(e - w > 1 && n - s > 1)) return null;   // view is off the GOES-East disk
      // limb guard: the renderer crashes when a corner is beyond the usable
      // disk (~68° great-circle from the 75.2°W sub-satellite point) — shrink
      // toward the box center until every corner is on-disk.
      var sep = function (lon, lat) {
        var la = lat * Math.PI / 180, dl = (lon + 75.2) * Math.PI / 180;
        return Math.acos(Math.cos(la) * Math.cos(dl)) * 180 / Math.PI;
      };
      for (var it = 0; it < 8; it++) {
        var worst = Math.max(sep(w, s), sep(w, n), sep(e, s), sep(e, n));
        if (worst <= 68) break;
        var mx = (w + e) / 2, my = (s + n) / 2;
        w = mx + (w - mx) * 0.88; e = mx + (e - mx) * 0.88;
        s = my + (s - my) * 0.88; n = my + (n - my) * 0.88;
      }
    }
    return {
      bbox: [w, s, e, n],
      time: timeIso,
      channel: m.channel, enhancement: m.enh || 'rainbow_ir',
      quality: quality || (S.hqExport ? 'high' : 'default'),
      gridlines: $('cx-ov-grid').classList.contains('on'),
      coastlines: $('cx-ov-coast').classList.contains('on')
    };
  }
  function tmFetch(body) {
    return fetch(RENDER_API, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) {
      if (!r.ok) return r.json().catch(function () { return {}; })
        .then(function (j) { throw new Error(j.detail || ('render failed (' + r.status + ')')); });
      return r.blob();
    });
  }
  function tmFetchBody(body) {
    if (!body) return Promise.reject(new Error('view is outside GOES-East coverage — pan east'));
    return tmFetch(body);
  }
  function enterTM() {
    if (domainInfo(S.domain).sat !== 'goes19') {
      flash('Time Machine covers the GOES-East archive — switch to a GOES-19 domain first');
      return;
    }
    stopClock(); disarmTools();
    S.tm.on = true;
    document.body.classList.add('cx-tm-mode');
    $('cx-tm').classList.add('on');
    $('cx-tm').querySelector('.lbl').textContent = 'Time Machine';
    var maxIso = new Date(Date.now() - 3600e3).toISOString().slice(0, 16);
    $('cx-tm-time').max = maxIso; $('cx-tm-end').max = maxIso;
    if (!$('cx-tm-time').value)
      $('cx-tm-time').value = new Date(Date.now() - 864e5).toISOString().slice(0, 16);
    tmSyncEraUI();
    flash('Time Machine: set a UTC time, then Render — the current field, view and overlays apply');
  }
  function exitTM() {
    S.tm.on = false; S.tm.busy = false;
    document.body.classList.remove('cx-tm-mode');
    document.body.classList.remove('cx-tm-deep');
    $('cx-tm').classList.remove('on');
    $('cx-tm').querySelector('.lbl').textContent = 'Live';
    tmClearLoop();
    S.panes.forEach(function (p, i) {
      if (!p) return;
      var im = $('cx-tm-img-' + i);
      if (im) { im.style.display = 'none'; im.removeAttribute('src'); }
      p.el.classList.remove('cx-tm-showing');
    });
    stopClock(); drawTimeline(); updateHeader();
    var tv = lead();
    if (tv) { updateClockUI({ stamp: tv.frames[tv.frameIdx], idx: tv.frameIdx, n: tv.frames.length }); }
    flash('');
  }
  function tmRenderOnce() {
    if (S.tm.busy) return;
    var t = $('cx-tm-time').value;
    if (!t) { flash('pick a UTC time first'); return; }
    var timeIso = t + ':00Z', stamp = tmStamp(t);
    // every ready pane whose field the archive can serve renders at the SAME
    // time (linked cameras share the box); unsupported panes sit out.
    var targets = [];
    S.panes.forEach(function (p, i) {
      if (p && p.ready && (!p.kind || p.kind === 'tile') &&
          p.product && tmMapFor(timeIso)[p.product.key]) targets.push(i);
    });
    if (!targets.length) { flash('this field is live-only — pick a channel or True Color'); return; }
    S.tm.busy = true; flash('rendering the archive view…', true);
    var chain = Promise.resolve();
    targets.forEach(function (i) {
      chain = chain.then(function () {
        return tmFetchBody(tmBody(S.panes[i], timeIso)).then(function (blob) {
          var im = $('cx-tm-img-' + i);
          if (im.dataset.url) URL.revokeObjectURL(im.dataset.url);
          im.src = im.dataset.url = URL.createObjectURL(blob);
          im.style.display = 'block';
          S.panes[i].el.classList.add('cx-tm-showing');
        });
      });
    });
    chain.then(function () {
      S.tm.busy = false; flash('');
      $('cx-valid').textContent = fmtStamp(stamp);
      $('cx-count').textContent = 'archive';
      tmSyncEraUI();
      tmLoadWindow(timeIso);   // 12 h scrub window + 6 h buffers, center-out
    }).catch(function (e) {
      S.tm.busy = false; flash(String(e.message || e).slice(0, 90));
    });
  }
  function tmClearLoop() {
    var seen = [];
    S.tm.frames.forEach(function (f) { if (f.url) { URL.revokeObjectURL(f.url); seen.push(f.url); } });
    Object.keys(S.tm.byPane).forEach(function (k) {
      (S.tm.byPane[k] || []).forEach(function (f) {
        if (f && f.url && seen.indexOf(f.url) < 0) URL.revokeObjectURL(f.url);
      });
    });
    S.tm.frames = []; S.tm.byPane = {}; S.tm.idx = 0; S.tm.windowFor = null;
  }
  function tmCurrentMap() {
    var v = $('cx-tm-time').value;
    return tmMapFor(v ? v + ':00Z' : new Date().toISOString());
  }
  function tmLoadLoop() {
    if (S.tm.busy) return;
    var a = $('cx-tm-time').value, z = $('cx-tm-end').value;
    var stepMin = +$('cx-tm-step').value;
    if (!a || !z) { flash('set both loop times (UTC)'); return; }
    var t0 = Date.parse(a + ':00Z'), t1 = Date.parse(z + ':00Z');
    if (!(t1 > t0)) { flash('loop end must be after the start'); return; }
    var stamps = [];
    for (var t = t0; t <= t1 && stamps.length < TM_MAX_LOOP; t += stepMin * 60e3)
      stamps.push(new Date(t));
    // MULTI-PANE: every ready pane whose field the archive serves loads the
    // loop (paced renders; N panes multiply the wait — progress says so)
    var targets = [];
    S.panes.forEach(function (p, i) {
      if (p && p.ready && (!p.kind || p.kind === 'tile') &&
          p.product && tmCurrentMap()[p.product.key]) targets.push(i);
    });
    if (!targets.length) { flash('these fields are live-only — pick a channel or True Color'); return; }
    tmClearLoop();
    S.tm.busy = true;
    var jobs = [];
    stamps.forEach(function (d) {
      targets.forEach(function (i) { jobs.push({ d: d, i: i }); });
    });
    targets.forEach(function (i) { S.tm.byPane[i] = []; });
    var done = 0;
    var next = function () {
      if (done >= jobs.length) {
        S.tm.busy = false;
        flash(S.tm.frames.length + '-frame archive loop ready on ' + targets.length +
              ' pane' + (targets.length === 1 ? '' : 's') + ' — play or export');
        tmShowFrame(S.tm.frames.length - 1);
        return;
      }
      var job = jobs[done], iso = job.d.toISOString().slice(0, 16);
      flash('archive loop: rendering ' + (done + 1) + '/' + jobs.length +
            (targets.length > 1 ? ' (pane ' + (job.i + 1) + ')' : '') + '…', true);
      tmFetchBody(tmBody(S.panes[job.i], iso + ':00Z', 'low')).then(function (blob) {
        var rec = { stamp: tmStamp(iso), url: URL.createObjectURL(blob) };
        S.tm.byPane[job.i].push(rec);
        if (job.i === targets[0]) S.tm.frames.push(rec);   // the timeline's list
        tmShowFrame(S.tm.frames.length - 1);
        done++;
        setTimeout(next, TM_PACE_MS);   // stay under the backend's rate limit
      }).catch(function (e) {
        // keep the per-pane lists index-aligned: a failed slot holds its place
        S.tm.byPane[job.i].push(null);
        if (job.i === targets[0]) S.tm.frames.push({ stamp: tmStamp(iso), url: '' });
        done++;
        setTimeout(next, TM_PACE_MS);
      });
    };
    next();
  }
  // archive loop export: same recorder + byte budget as the live path, fed by
  // the rendered frames (already branded by the backend — no extra chrome).
  function exportTMLoop(btn) {
    // export the ACTIVE pane's loop when it has one, else the timeline list;
    // failed slots (null / empty url) are skipped, not black frames
    var list = (S.tm.byPane[S.active] || S.tm.frames)
      .filter(function (f) { return f && f.url; });
    if (list.length < 2) { flash('load an archive loop first'); return; }
    if (btn.dataset.busy) return;
    btn.dataset.busy = '1'; stopClock();
    var imgs = [], loaded = 0, fps = 6;
    list.forEach(function (f, k) {
      var im = new Image();
      im.onload = function () { if (++loaded === list.length) start(); };
      im.src = f.url; imgs[k] = im;
    });
    function start() {
      var c = document.createElement('canvas');
      c.width = imgs[0].naturalWidth; c.height = imgs[0].naturalHeight;
      var ctx = c.getContext('2d');
      var stream = c.captureStream(fps);
      var mime = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm']
        .filter(function (t) { return MediaRecorder.isTypeSupported(t); })[0] || 'video/webm';
      var secs = Math.max(1, imgs.length / fps);
      var budget = S.hqExport ? 24e6 : 9e6;
      var rec = new MediaRecorder(stream, { mimeType: mime,
        videoBitsPerSecond: Math.min(S.hqExport ? 12e6 : 6e6, Math.floor(budget * 8 / secs)) });
      var chunks = [];
      rec.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
      rec.onstop = function () {
        var a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob(chunks, { type: 'video/webm' }));
        a.download = 'archive_' + S.tm.frames[0].stamp + '_loop.webm';
        a.click();
        btn.querySelector('.lbl').textContent = 'Loop'; delete btn.dataset.busy;
      };
      var k = 0; rec.start();
      (function step() {
        ctx.drawImage(imgs[k % imgs.length], 0, 0, c.width, c.height);
        btn.querySelector('.lbl').textContent = (k + 1) + '/' + imgs.length;
        k++;
        if (k < imgs.length) setTimeout(step, 1000 / fps);
        else setTimeout(function () { try { rec.stop(); } catch (e) {} }, 2000 / fps);
      })();
    }
  }

  var flashTimer = null;
  function flash(msg, busy) {
    var el = $('cx-flash');
    el.textContent = msg || ''; el.style.opacity = msg ? 1 : 0;
    el.classList.toggle('busy', !!(msg && busy));
    if (flashTimer) clearTimeout(flashTimer);
    if (msg && !busy) flashTimer = setTimeout(function () { el.style.opacity = 0; }, 4000);
  }

  // ========================================================================
  // BOOT
  // ========================================================================
  function wireBottomBar() {
    $('cx-play').onclick = function () { S.playing ? stopClock() : startClock(); };
    $('cx-slower').onclick = function () { speed(-1); };
    $('cx-faster').onclick = function () { speed(+1); };
    $('cx-inspect').onclick = function () {
      var on = !$('cx-inspect').classList.contains('on');
      $('cx-inspect').classList.toggle('on', on);
      S.panes.forEach(function (p) { if (p && p.tv) p.tv.setInspector(on); });
    };
    $('cx-measure').onclick = function () { armTool('measure', $('cx-measure')); };
    $('cx-box').onclick = function () { armDrawBox(); };
    $('cx-sketch').onclick = function () { armTool('sketch', $('cx-sketch')); };
    $('cx-clear').onclick = function () { disarmTools(); clearSketch(); };
    $('cx-share').onclick = shareURL;
    $('cx-png').onclick = exportPNG;
    $('cx-loop').onclick = function () { exportLoop($('cx-loop')); };
    $('cx-hq').onclick = function () {
      S.hqExport = !S.hqExport;
      $('cx-hq').classList.toggle('on', S.hqExport);
      $('cx-hq').querySelector('.lbl').textContent = S.hqExport ? 'HQ' : '≤10MB';
    };
    $('cx-fit').onclick = function () {
      var p = S.panes[S.active]; if (p && p.ready) p.tv.fitData();
    };
    $('cx-reset').onclick = resetAll;
    $('cx-settings').onclick = function () {
      var pop = $('cx-setpop');
      pop.style.display = pop.style.display === 'block' ? 'none' : 'block';
    };
    $('cx-set-dwell').onclick = function () {
      S.dwell = !S.dwell;
      $('cx-set-dwell').classList.toggle('on', S.dwell);
    };
    document.querySelectorAll('[data-panes]').forEach(function (b) {
      b.onclick = function () { setPaneCount(+b.dataset.panes); wireSelectMap(); };
    });
    $('cx-link').onclick = function () {
      S.linked = !S.linked;
      $('cx-link').classList.toggle('on', S.linked);
      flash(S.linked ? 'panes linked — pan/zoom moves all' : 'panes independent');
    };
    $('cx-tm').onclick = function () { S.tm.on ? exitTM() : enterTM(); };
    $('cx-tm-render').onclick = tmRenderOnce;
    $('cx-tm-time').addEventListener('change', tmSyncEraUI);
    $('cx-tm-loop').onclick = tmLoadLoop;
    var tmShift = function (h) {
      var el = $('cx-tm-time');
      if (!el.value) return;
      var t = new Date(Date.parse(el.value + ':00Z') + h * 3600e3);
      var iso = t.toISOString().slice(0, 16);
      if (el.max && iso > el.max) iso = el.max;
      if (iso < el.min) iso = el.min;
      el.value = iso; tmRenderOnce();
    };
    $('cx-tm-back').onclick = function () { tmShift(-1); };
    $('cx-tm-fwd').onclick = function () { tmShift(+1); };
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') disarmTools();
      if (e.key === ' ' && document.activeElement === document.body) {
        e.preventDefault(); S.playing ? stopClock() : startClock();
      }
      if (e.key === 'ArrowRight') { stopClock(); clockShow(clockIdx() + 1); }
      if (e.key === 'ArrowLeft') { stopClock(); clockShow(clockIdx() - 1); }
    });
  }

  function boot() {
    buildFieldRail(); buildDomainRail(); buildRegionRail(); buildOverlayRail();
    wireBottomBar(); wireTimeline();
    // native MW/ASCAT fields+layers (controls cards, manifests, adapters)
    if (window.CockpitFields) {
      window.CockpitFields.init(S, {
        flash: flash,
        renderPaneChrome: renderPaneChrome,
        markFieldActive: markFieldActive
      });
    }
    $('cx-speed').textContent = S.fps + ' fps';

    // first screen = exactly one viewport below the main nav
    var setNavH = function () {
      var nav = document.querySelector('.nav');
      if (nav) document.documentElement.style.setProperty('--cx-nav', nav.offsetHeight + 'px');
    };
    setNavH(); window.addEventListener('resize', setNavH);
    // the below-fold legacy page reports its height (postMessage) — size its iframe
    window.addEventListener('message', function (e) {
      var d = e.data || {};
      if (d.tatEmbedHeight && d.page === '/satellite/') {
        var fr = $('cx-legacy-frame');
        if (fr) fr.style.height = Math.max(900, d.tatEmbedHeight) + 'px';
      }
    });

    var urlDomain = params.get('domain');
    S.domain = DOMAINS[urlDomain] ? urlDomain : 'conus';
    var bootKey = params.get('product') || 'ir';
    var p0 = productByKey(bootKey) || productByKey('ir') || productSet(S.domain)[0];
    if (domainInfo(S.domain).sat !== 'goes19') rebuildProductRows();
    var pane0 = makePane(0, p0);
    // dev override: ?manifest= forces pane 0's manifest verbatim
    if (params.get('manifest')) pane0.tv.manifestUrl = params.get('manifest');
    markDomain(); setActivePane(0); updateHeader(); markFieldActive();

    // availability ground truth (ONE fetch per sector index) + domain probes
    fetch(PBASE + 'sat/goes19/conus/products.json', { cache: 'no-cache' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (idx) {
        if (!idx || !idx.products) return;
        S.available = new Set(idx.products.map(function (r) { return r.id; }));
        applyAvailability();
      }).catch(function () {});
    probeDomains();

    // US states region group once the shared geojson lands (same loader the
    // furniture uses; cached by the browser so this costs ~nothing extra)
    if (window.TATRegions && window.TATRegions.loadGeo) {
      window.TATRegions.loadGeo({}).then(fillStates).catch(function () {});
    }

    var applied = false;
    var tryApply = function () {
      if (applied || !pane0.ready) return;
      applied = true;
      applyURLState(); wireSelectMap(); drawTimeline();
    };
    var poll = setInterval(function () {
      tryApply(); if (applied) clearInterval(poll);
    }, 250);
  }

  // dev hook: sibling explorer modules (objfix panel markers, MW/ASCAT
  // adapters) reach the pane list through this — read-only by convention.
  window.__cockpit = S;

  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
