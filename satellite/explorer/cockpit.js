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
  var PBASE = window.TVProducts ? window.TVProducts.base : '';
  var params = new URLSearchParams(location.search);
  var $ = function (id) { return document.getElementById(id); };

  function productByKey(k) {
    for (var i = 0; i < PRODUCTS.length; i++) if (PRODUCTS[i].key === k) return PRODUCTS[i];
    return null;
  }
  function manifestUrlFor(p, domain) {
    var path = (domain === 'fd') ? p.path.replace('/conus/', '/fd/') : p.path;
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
    domain: 'conus',            // conus | fd (meso1/2 + goes18 are "coming")
    fdAvailable: false,
    available: null,            // Set of product ids the box has actually emitted
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
  var CH_RE = /^C(\d\d) · ([\d.]+ µm) \((.+)\)$/;
  function chParts(p) {
    var m = CH_RE.exec(p.title);
    if (!m) return null;
    var met = m[3];
    if (p.key === 'irbd') met = 'Dvorak BD';
    return { num: 'C' + m[1], wl: m[2], met: met };
  }

  // Microwave + Scatterometer fold into the rail as categories: selecting one
  // mounts the EXISTING viewer (?embed=1 iframe) in the stage — reuse, not a
  // rebuild. Entries grey "SOON" off each source's own R2 manifest.
  var MW_FIELDS = [
    { key: 'mw-91c', title: '91 GHz color composite', meta: 'NRL · convective structure' },
    { key: 'mw-91h', title: '91H brightness temp', meta: 'NRL · eyewall through cirrus' },
    { key: 'mw-37c', title: '37 GHz color composite', meta: 'NRL · low-level rain bands' },
    { key: 'mw-37h', title: '37H brightness temp', meta: 'NRL · forming eye' }
  ];
  var SC_FIELDS = [
    { key: 'sc-basin', title: 'Ocean winds · basin passes', meta: 'wind barbs · stitched passes' },
    { key: 'sc-storm', title: 'Ocean winds · storm-locked', meta: 'tagged passes per storm' }
  ];
  var EMBEDS = {
    mw: { src: '/satellite/microwave/?embed=1', label: 'Passive Microwave' },
    sc: { src: '/satellite/ascat/?embed=1', label: 'Scatterometer' }
  };

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

    // legacy-source categories (embed on select)
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
          showEmbed(kind);
          document.querySelectorAll('.cx-field').forEach(function (el) {
            el.classList.toggle('active', el === row);
          });
        };
        lists[kind].appendChild(row);
      });
    });
    checkLegacySource('mw', 'https://cdn.triple-a-tropics.com/microwave/manifest.json');
    checkLegacySource('sc', 'https://cdn.triple-a-tropics.com/ascat/manifest.json');

    // composites lead the RGB tab (True Color, Sandwich), then the RGBs;
    // channels keep their products.js order (Clean IR first, then C01..C16).
    var ordered = PRODUCTS.filter(function (p) { return p.group === 'composite'; })
      .concat(PRODUCTS.filter(function (p) { return p.group === 'rgb'; }))
      .concat(PRODUCTS.filter(function (p) { return p.group === 'channel'; }));
    ordered.forEach(function (p) {
      var isCh = p.group === 'channel';
      var row = document.createElement('button');
      row.type = 'button'; row.className = 'cx-field'; row.dataset.key = p.key;
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
        hideEmbed();
        setPaneProduct(S.active, p);
      };
      lists[isCh ? 'ch' : 'rgb'].appendChild(row);
    });
    switchTab('rgb');
  }

  // ---- legacy-source embeds (Microwave / Scatterometer) -------------------
  function checkLegacySource(kind, url) {
    fetch(url, { cache: 'no-cache' })
      .then(function (r) { if (!r.ok) throw 0; return r.json(); })
      .then(function () {
        document.querySelectorAll('[data-embed="' + kind + '"]').forEach(function (el) {
          el.classList.remove('coming');
          var chip = el.querySelector('.cx-chip'); if (chip) chip.remove();
          var meta = el.querySelector('.cx-meta');
          if (meta) meta.innerHTML = meta.innerHTML.replace(/ · $/, '');
        });
      })
      .catch(function () { /* stays greyed "soon" — honest */ });
  }
  function showEmbed(kind) {
    stopClock(); disarmTools();
    var box = $('cx-embed');
    if (box.dataset.kind !== kind) {
      box.innerHTML = '<iframe src="' + EMBEDS[kind].src + '" title="' +
        EMBEDS[kind].label + '"></iframe>';
      box.dataset.kind = kind;
    }
    box.style.display = 'block';
    flash(EMBEDS[kind].label + ' — controls are inside the viewer');
  }
  function hideEmbed() {
    var box = $('cx-embed');
    if (box.style.display === 'block') box.style.display = 'none';
  }

  function markFieldActive() {
    var key = S.panes[S.active] && S.panes[S.active].product
      ? S.panes[S.active].product.key : null;
    document.querySelectorAll('.cx-field').forEach(function (el) {
      el.classList.toggle('active', el.dataset.key === key);
    });
  }

  function applyAvailability() {
    if (!S.available) return;
    document.querySelectorAll('.cx-field').forEach(function (el) {
      if (el.dataset.embed) return;   // MW/ASCAT rows: their own manifests gate them
      var p = productByKey(el.dataset.key);
      var ok = p && S.available.has(p.id);
      el.classList.toggle('coming', !ok);
      var meta = el.querySelector('.cx-meta');
      if (!ok && meta && meta.textContent.indexOf('no data yet') < 0) {
        meta.innerHTML += ' · <i class="cx-chip">no data yet</i>';
      }
    });
  }

  // ========================================================================
  // RIGHT RAIL — satellite / domain / regions / overlays
  // ========================================================================
  function buildDomainRail() {
    // satellite rows are static in the HTML (GOES-19 on, GOES-18 coming).
    document.querySelectorAll('#cx-domains .cx-item').forEach(function (el) {
      el.onclick = function () {
        var d = el.dataset.domain;
        if (el.classList.contains('coming')) return;
        if (d === 'drawbox') { armDrawBox(); return; }
        if (d === 'selectmap') { armTool('selectmap', el); return; }
        setDomain(d);
      };
    });
  }
  function markDomain() {
    document.querySelectorAll('#cx-domains .cx-item').forEach(function (el) {
      el.classList.toggle('active', el.dataset.domain === S.domain);
    });
  }

  function setDomain(d) {
    if (d === S.domain) return;
    if (d === 'fd' && !S.fdAvailable) return;
    S.domain = d;
    markDomain();
    // re-point every pane at the same product key in the new domain
    S.panes.forEach(function (pane, i) {
      if (pane.product) setPaneProduct(i, pane.product, true);
    });
  }

  function checkFullDisk() {
    fetch(PBASE + 'sat/goes19/fd/products.json', { cache: 'no-cache' })
      .then(function (r) { if (!r.ok) throw 0; return r.json(); })
      .then(function (idx) {
        if (!idx || !idx.count) throw 0;
        S.fdAvailable = true;
        var el = document.querySelector('[data-domain="fd"]');
        el.classList.remove('coming');
        var chip = el.querySelector('.cx-chip'); if (chip) chip.remove();
      })
      .catch(function () { /* stays greyed "no data yet" — honest */ });
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
      '<div class="cx-pane-head" id="cx-ph-' + i + '"><div>' +
        '<div class="cx-ph-title" id="cx-pht-' + i + '"></div>' +
        '<div class="cx-ph-sub" id="cx-phs-' + i + '"></div></div>' +
        '<div class="cx-ph-brand">@WeathermanAAA_</div></div>' +
      '<div class="cx-pane-cbar" id="cx-pc-' + i + '">' +
        '<div class="ticks" id="cx-pct-' + i + '"></div>' +
        '<img alt="" id="cx-pci-' + i + '"></div>' +
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
        applyOverlayState(tv);
        renderPaneChrome(i);
        wireCameraSync(pane);
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

  // burned-in branded chrome per pane: title strip + valid time + colorbar.
  // Scalar/BT fields carry their color table; RGB composites have no cbar in
  // products.js (cbar:null) so nothing meaningless is forced.
  function renderPaneChrome(i, stamp) {
    var pane = S.panes[i];
    if (!pane || !pane.product || !$('cx-pht-' + i)) return;
    var p = pane.product;
    $('cx-pht-' + i).textContent =
      'GOES-19 · ' + (S.domain === 'fd' ? 'Full Disk' : 'CONUS') + ' · ' + p.title;
    var s = stamp ||
      (pane.tv && pane.tv.frames && pane.tv.frames[pane.tv.frameIdx]) ||
      (pane.tv && pane.tv.manifest && pane.tv.manifest.latest);
    $('cx-phs-' + i).textContent = s ? 'Valid ' + fmtStamp(s) : '';
    var cb = $('cx-pc-' + i);
    if (p.cbar) {
      $('cx-pci-' + i).src = p.cbar.img;
      $('cx-pct-' + i).innerHTML = p.cbar.ticks.map(function (t) {
        var pos = Math.max(0.02, Math.min(0.98, t.p)) * 100;
        return '<span style="top:' + pos.toFixed(2) + '%">' + t.t + '</span>';
      }).join('');
      cb.style.display = 'flex';
    } else cb.style.display = 'none';
  }
  function paneTag(i, stamp) { renderPaneChrome(i, stamp); }

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
  }

  function setPaneCount(n) {
    var cur = S.panes.filter(Boolean).length;
    if (n === cur) return;
    stopClock();
    if (n > cur) {
      for (var i = cur; i < n; i++) {
        var used = S.panes.filter(Boolean).map(function (p) { return p.product.key; });
        var pick = PANE_DEFAULTS.filter(function (k) {
          var p = productByKey(k);
          return used.indexOf(k) < 0 && p && (!S.available || S.available.has(p.id));
        })[0] || 'ir';
        makePane(i, productByKey(pick) || PRODUCTS[0]);
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
    if (S.available && !S.available.has(p.id) && S.domain === 'conus') return;
    flash('Loading ' + p.title + '…', true);
    pane.tv.setProduct(manifestUrlFor(p, S.domain), p).then(function () {
      pane.product = p;
      paneTag(i);
      if (i === S.active) { updateHeader(); markFieldActive(); }
      if (i === 0) drawTimeline();
      flash('');
    }).catch(function () {
      if (forceDomain) flash('no ' + (S.domain === 'fd' ? 'full-disk' : S.domain) +
                             ' data yet for ' + p.title);
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
    var tv = lead();
    if (!tv || !tv.frames.length) return;
    var n = tv.frames.length;
    idx = ((idx % n) + n) % n;
    tv.showFrame(idx);
    var stamp = tv.frames[idx];
    for (var k = 1; k < S.panes.length; k++)
      if (S.panes[k] && S.panes[k].ready) S.panes[k].tv.showStamp(stamp);
  }
  function startClock() {
    var tv = lead();
    if (S.playing || !tv || tv.frames.length < 2) {
      if (tv && tv.frames.length < 2) flash('1 frame — the loop fills as the emit cron runs');
      return;
    }
    S.playing = true; S.last = 0;
    $('cx-play').classList.add('playing', 'on');
    function step(t) {
      if (!S.playing) return;
      if (!S.last) S.last = t;
      var iv = 1000 / S.fps;
      if (S.dwell && lead().frameIdx === lead().frames.length - 1) iv *= 6;
      if (t - S.last >= iv) { S.last = t; clockShow(lead().frameIdx + 1); }
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
    var dpr = window.devicePixelRatio || 1;
    var w = cv.clientWidth, h = cv.clientHeight;
    if (!w) return;
    cv.width = w * dpr; cv.height = h * dpr;
    var ctx = cv.getContext('2d');
    ctx.scale(dpr, dpr); ctx.clearRect(0, 0, w, h);
    var n = tv.frames.length;
    ctx.fillStyle = '#141b25'; ctx.fillRect(0, h / 2 - 3, w, 6);
    if (!n) return;
    // progress fill to the current frame
    var fx = n > 1 ? (tv.frameIdx / (n - 1)) * w : w;
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
    ctx.fillText(fmtStamp(tv.frames[0]).slice(5), 2, h - 12);
    var lastLbl = fmtStamp(tv.frames[n - 1]).slice(5);
    ctx.fillText(lastLbl, w - ctx.measureText(lastLbl).width - 2, h - 12);
    if (n === 1) {
      ctx.fillStyle = '#5b6879';
      var note = 'single frame — the loop fills as new scans land';
      ctx.fillText(note, (w - ctx.measureText(note).width) / 2, 2);
    }
  }
  function wireTimeline() {
    var cv = $('cx-tl');
    var wasPlaying = false, scrubbing = false;
    function idxAt(e) {
      var r = cv.getBoundingClientRect(), tv = lead();
      if (!tv || !tv.frames.length) return 0;
      var f = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
      return Math.round(f * (tv.frames.length - 1));
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
    var p = pane.product, dpr = w / pane.tv.map.getCanvas().clientWidth || 1;
    var f = function (px) { return Math.round(px * dpr); };
    var grad = ctx.createLinearGradient(0, 0, 0, f(56));
    grad.addColorStop(0, 'rgba(10,13,18,.84)'); grad.addColorStop(1, 'rgba(10,13,18,0)');
    ctx.fillStyle = grad; ctx.fillRect(0, 0, w, f(56));
    ctx.textBaseline = 'top';
    ctx.fillStyle = '#dbe3ec';
    ctx.font = '700 ' + f(13) + 'px Metropolis,system-ui,sans-serif';
    ctx.fillText('GOES-19 · ' + (S.domain === 'fd' ? 'Full Disk' : 'CONUS') + ' · ' + p.title,
                 f(12), f(9));
    ctx.fillStyle = '#9aa8b8';
    ctx.font = '500 ' + f(10.5) + 'px Metropolis,system-ui,sans-serif';
    if (stamp) ctx.fillText('Valid ' + fmtStamp(stamp), f(12), f(27));
    ctx.fillStyle = 'rgba(255,255,255,.48)';
    ctx.font = '700 ' + f(13.5) + 'px Metropolis,system-ui,sans-serif';
    var brand = '@WeathermanAAA_';
    ctx.fillText(brand, w - ctx.measureText(brand).width - f(12), f(9));
    // colorbar (scalar/BT fields only — RGB composites carry none)
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
    var map = pane.tv.map;
    map.triggerRepaint();
    requestAnimationFrame(function () {
      var src = map.getCanvas(), c = compositeCanvas(pane), ctx = c.getContext('2d');
      ctx.drawImage(src, 0, 0);
      drawChrome(ctx, pane, c.width, c.height, pane.tv.frames[pane.tv.frameIdx]);
      c.toBlob(function (blob) {
        if (!blob) { flash('PNG export failed'); return; }
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = pane.product.id + '_' +
          (pane.tv.frames[pane.tv.frameIdx] || 'latest') + '.png';
        a.click(); flash('');
      }, 'image/png');
    });
  }
  function exportLoop(btn) {
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
    disarmTools(); stopClock(); clearSketch(); hideEmbed();
    setPaneCount(1);
    var p = productByKey('ir') || PRODUCTS[0];
    if (S.domain !== 'conus') { S.domain = 'conus'; markDomain(); }
    if (S.panes[0].product.key !== p.key) setPaneProduct(0, p);
    var tv = lead();
    if (tv && tv.map) { tv.fitData(); tv.clearPins(); clockShow(tv.frames.length - 1); }
    history.replaceState(null, '', location.pathname);
    flash('');
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
      b.onclick = function () { hideEmbed(); setPaneCount(+b.dataset.panes); wireSelectMap(); };
    });
    $('cx-link').onclick = function () {
      S.linked = !S.linked;
      $('cx-link').classList.toggle('on', S.linked);
      flash(S.linked ? 'panes linked — pan/zoom moves all' : 'panes independent');
    };
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') disarmTools();
      if (e.key === ' ' && document.activeElement === document.body) {
        e.preventDefault(); S.playing ? stopClock() : startClock();
      }
      if (e.key === 'ArrowRight') { stopClock(); clockShow(lead().frameIdx + 1); }
      if (e.key === 'ArrowLeft') { stopClock(); clockShow(lead().frameIdx - 1); }
    });
  }

  function boot() {
    buildFieldRail(); buildDomainRail(); buildRegionRail(); buildOverlayRail();
    wireBottomBar(); wireTimeline();
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

    var bootKey = params.get('product') || 'ir';
    var p0 = productByKey(bootKey) || productByKey('ir') || PRODUCTS[0];
    S.domain = (params.get('domain') === 'fd') ? 'fd' : 'conus';
    var pane0 = makePane(0, p0);
    // dev override: ?manifest= forces pane 0's manifest verbatim
    if (params.get('manifest')) pane0.tv.manifestUrl = params.get('manifest');
    markDomain(); setActivePane(0); updateHeader(); markFieldActive();

    // availability ground truth (ONE fetch) + fd probe
    fetch(PBASE + 'sat/goes19/conus/products.json', { cache: 'no-cache' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (idx) {
        if (!idx || !idx.products) return;
        S.available = new Set(idx.products.map(function (r) { return r.id; }));
        applyAvailability();
      }).catch(function () {});
    checkFullDisk();

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

  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
