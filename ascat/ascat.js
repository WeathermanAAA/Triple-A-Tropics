/* ASCAT ocean-surface winds viewer (/ascat/).
 *
 * A SELF-CONTAINED, copyable, TAT-branded figure of ASCAT scatterometer winds:
 * speed-colored wind barbs (one per wind-vector cell) over the same clean
 * recon-style basemap (opaque ocean, ne_10m coastline + faint borders + a faint
 * lat/lon graticule, aspect-correct equirectangular projection via TATRegions),
 * with the shared tropical-cyclone kt color scale (RED at 64 kt) and colorbar.
 *
 * The barb, basemap, color-scale, colorbar, watermark and PNG-export primitives
 * are CLONED from recon/recon.js - ASCAT is the same visual primitive (vector
 * winds over ocean), only the data model differs: a "pass" is one ~100-min orbit
 * of decimated WVCs (lat/lon + speed-kt + barb FROM-direction), NOT a flight
 * track, so there is no time-series panel.
 *
 * Two mounts (mirrors ReconViewer):
 *   - /ascat/ main site: a REGION composite - the most recent ASCAT-B + ASCAT-C
 *     passes over a chosen basin (TATRegions.RegionPicker), B+C stitched so a gap
 *     in one swath may be filled by the other.
 *   - CycloLab per-storm tab: new AscatViewer(el, { stormLock: '<sid>', ... }) -
 *     the SAME viewer locked to one storm: only that storm's tagged passes,
 *     centered on the storm. (Phase B, lazy-loaded like the recon/HAFS tabs.)
 *
 * Hydrates entirely from R2 JSON (ascat/manifest.json + current.json + per-pass
 * {id}.json). Everything is drawn onto ONE <canvas> so Save / Copy / Download PNG
 * yields the complete figure. Dependency-free except window.TATRegions.
 *
 * Honest caveats (drawn on the figure): C-band ASCAT underestimates extreme
 * TC-core winds; rain/quality-flagged cells are removed; swaths are intermittent;
 * the near-real-time feed is per-orbit (latest pass typically a few hours old).
 *
 * Isolated from the other viewers (own IIFE, ascat-* ids).
 */
(function () {
  'use strict';

  var BASE_DEFAULT = 'https://cdn.triple-a-tropics.com/ascat';
  var POLL_MS = 600000;                // manifest refresh (feed is ~daily; slow cadence)
  var WATERMARK = '@WeathermanAAA_';
  var FONT = 'Metropolis, "Helvetica Neue", Arial, sans-serif';
  var CREDIT = '© EUMETSAT';
  var MAX_PASSES = 6;                  // recent passes loaded for a basin composite
  var GLOBAL_MAX_PASSES = 90;          // Global view: cover the full ~60 h
                                       // ingest window (~68 orbits: MetOp-B +
                                       // MetOp-C at ~14/day each over 2.5 d);
                                       // the density control culls overlapping
                                       // barbs, so more passes = fuller globe,
                                       // not more clutter. 40 left a day of
                                       // orbits undrawn -> empty regions.

  // ---- TC kt color scale (hard bins), genuinely shared with recon now: both
  // read the SAME derived ramp from the canonical palette instead of each
  // carrying a verbatim copy of it. Each entry [minKt, color]; a speed picks
  // the LAST bin it meets. Category-exact at every SSHWS threshold.
  function TATP() {
    var p = window.TATPalette;
    if (!p) throw new Error('ascat.js: load /tat_palette.js first');
    return p;
  }
  var KT_SCALE = TATP().windRamp;
  var KT_SCALE_HC = KT_SCALE;

  var STYLES = {
    sshws: {
      label: 'Classic SSHWS', scale: KT_SCALE,
      bg: '#07101c', ocean: '#0a1626', land: '#19314e',
      coast: 'rgba(201,219,242,0.95)', coastLw: 1.0,
      country: 'rgba(150,175,205,0.42)', countryLw: 0.6,
      state: 'rgba(150,175,205,0.16)', stateLw: 0.5,
      grid: 'rgba(176,196,222,0.10)', gridLab: 'rgba(176,196,222,0.5)',
      barbLw: 1.1
    },
    highcontrast: {
      label: 'High contrast', scale: KT_SCALE_HC,
      bg: '#04080e', ocean: '#07101e', land: '#13243c',
      coast: 'rgba(224,236,250,1.0)', coastLw: 1.15,
      country: 'rgba(180,205,235,0.55)', countryLw: 0.75,
      state: 'rgba(180,205,235,0.20)', stateLw: 0.55,
      grid: 'rgba(214,228,245,0.14)', gridLab: 'rgba(214,228,245,0.62)',
      barbLw: 1.35
    }
  };
  var LS_STYLE = 'ascat.style';

  var C = { fg: '#e5edf6', muted: '#8ea2bd', border: '#2a3e5c', panel: '#0a1324',
            accent: (typeof window !== 'undefined' && window.TATRegions && window.TATRegions.ACCENT) || '#2b9cff' };

  // density presets -> screen-space minimum spacing (px) between barb roots
  var DENSITY = { auto: 20, dense: 13, sparse: 30 };

  // basin -> default main-site region key (TATRegions)
  var BASIN_REGION = { AL: 'atlantic', EP: 'epac', CP: 'epac', WP: 'wpac',
                       IO: 'io', SH: 'aus', SP: 'swpac', SI: 'io' };

  var WK = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function el(id) { return document.getElementById(id); }
  function num(v) { return (typeof v === 'number' && isFinite(v)) ? v : null; }

  function fmtZ(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso || '';
    return MO[d.getUTCMonth()] + ' ' + d.getUTCDate() + ' ' +
      String(d.getUTCHours()).padStart(2, '0') + String(d.getUTCMinutes()).padStart(2, '0') + 'Z';
  }
  function fmtDTG(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso || '';
    return WK[d.getUTCDay()] + ' ' + fmtZ(iso);
  }
  // "5 h ago" / "1 d 4 h ago" relative age
  function ageStr(iso) {
    var d = new Date(iso); if (isNaN(d.getTime())) return '';
    var s = (Date.now() - d.getTime()) / 1000;
    if (s < 0) return 'just now';
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    if (h < 1) return m + ' min ago';
    if (h < 36) return h + ' h ago';
    var dd = Math.floor(h / 24); return dd + ' d ' + (h % 24) + ' h ago';
  }

  function roundRectPath(g, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    g.beginPath(); g.moveTo(x + r, y);
    g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r);
    g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath();
  }

  // DOM scaffold injected when embedded without the /ascat/ page markup (the
  // CycloLab tab passes only a bare root); no-op on /ascat/ which ships its own.
  var ASCAT_SCAFFOLD =
    '<div class="ascat-controls">' +
      '<div id="ascat-storm-wrap" class="ascat-group"><label for="ascat-storm">View</label><select id="ascat-storm"></select></div>' +
      '<div id="ascat-region-wrap" class="ascat-group"><label>Region</label><button id="ascat-region" type="button" class="ascat-btn ascat-region-btn"><span id="ascat-region-lab">Atlantic</span> <span class="ascat-caret">▾</span></button></div>' +
      '<div class="ascat-group"><label for="ascat-pass">Pass</label><select id="ascat-pass"></select></div>' +
      '<div class="ascat-group"><label for="ascat-density">Density</label><select id="ascat-density"><option value="auto">Auto</option><option value="dense">Dense</option><option value="sparse">Sparse</option></select></div>' +
      '<div id="ascat-backdrop-wrap" class="ascat-group ascat-backdrop-wrap" title="Satellite backdrop is available in storm-centered view"><label for="ascat-backdrop">Satellite</label>' +
        '<div class="ascat-bd-row"><label class="ascat-chk"><input type="checkbox" id="ascat-backdrop"> Vis / SWIR</label>' +
        '<input type="range" id="ascat-bd-opacity" min="10" max="100" value="40" title="Backdrop opacity" disabled></div></div>' +
      '<div class="ascat-group ascat-style-wrap"><label for="ascat-style">Style</label><select id="ascat-style"></select></div>' +
    '</div>' +
    '<div id="ascat-mapframe" class="ascat-mapframe">' +
      '<canvas id="ascat-canvas" width="900" height="560" aria-label="ASCAT scatterometer ocean-surface wind barbs"></canvas>' +
      '<div id="ascat-tooltip" class="ascat-tooltip"></div>' +
      '<div id="ascat-zoomhint" class="ascat-zoomhint">drag to zoom</div>' +
      '<button id="ascat-reset" class="ascat-reset" type="button" style="display:none" title="Reset to full extent">⤢ Reset view</button>' +
      '<div id="ascat-status" class="ascat-status"><div class="ascat-spinner"></div><span>Loading…</span></div>' +
    '</div>' +
    '<div class="ascat-actions">' +
      '<button id="ascat-download" class="ascat-btn" type="button" title="Download this figure as a PNG">⬇ Download PNG</button>' +
      '<button id="ascat-copy" class="ascat-btn" type="button" title="Copy this figure to the clipboard">Copy</button>' +
    '</div>' +
    '<div id="ascat-stats" class="ascat-stats"></div>' +
    '<div id="ascat-empty" class="ascat-empty"><h2>No recent ASCAT passes</h2>' +
      '<p>ASCAT-B and ASCAT-C ocean-surface wind passes appear here as they are published. Scatterometer swaths are intermittent; the near-real-time feed is per-orbit, typically a few hours old.</p></div>';

  var ASCAT_EMBED_CSS =
    '.ascat-controls{display:flex;flex-wrap:wrap;gap:10px 14px;align-items:flex-end;margin-bottom:10px}' +
    '.ascat-group{display:flex;flex-direction:column;gap:3px}' +
    '.ascat-group label{font:600 10px/1 inherit;letter-spacing:.04em;text-transform:uppercase;color:#8ea2bd}' +
    '.ascat-controls select{background:#0e1a30;color:#e5edf6;border:1px solid #1d2c44;border-radius:6px;padding:6px 8px;font:13px/1 inherit;min-width:130px}' +
    '.ascat-style-wrap{margin-left:auto}' +
    '.ascat-mapframe{position:relative;width:100%}' +
    '#ascat-canvas{display:block;width:100%;height:auto;border-radius:8px;cursor:crosshair;background:#0b1320}' +
    '.ascat-status{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;gap:8px;color:#aebdd4;background:rgba(7,16,28,.6)}' +
    '.ascat-spinner{width:16px;height:16px;border:2px solid #2b6cb0;border-top-color:transparent;border-radius:50%;animation:ascatspin 1s linear infinite}' +
    '@keyframes ascatspin{to{transform:rotate(360deg)}}' +
    '.ascat-tooltip{position:absolute;display:none;pointer-events:none;z-index:5;background:rgba(7,16,28,.94);border:1px solid #2a3e5c;border-radius:6px;padding:5px 8px;font:11px/1.4 inherit;color:#e5edf6;white-space:nowrap}' +
    '.ascat-actions{display:flex;gap:8px;margin:10px 0}' +
    '.ascat-btn{background:#0e1a30;color:#cfe0f5;border:1px solid #1d2c44;border-radius:7px;padding:7px 13px;font:600 13px/1 inherit;cursor:pointer}' +
    '.ascat-btn:hover{border-color:#2b6cb0}' +
    '.ascat-caret{color:#2b9cff;font-size:11px}' +
    '.ascat-stats{display:flex;flex-wrap:wrap;gap:8px 18px;color:#cdd9ea;font:13px/1.3 inherit}' +
    '.ascat-empty{color:#8ea2bd;padding:18px 4px}.ascat-empty h2{color:#e5edf6;margin:0 0 6px;font-size:18px}' +
    '.ascat-bd-row{display:flex;align-items:center;gap:8px}' +
    '.ascat-chk{display:inline-flex;align-items:center;gap:5px;font:13px/1 inherit;color:#cfe0f5;text-transform:none;letter-spacing:0;cursor:pointer}' +
    '.ascat-chk input{accent-color:#2b9cff}' +
    '#ascat-bd-opacity{width:78px;accent-color:#2b9cff}' +
    '.ascat-backdrop-wrap.ascat-disabled{opacity:.42}' +
    '.ascat-backdrop-wrap.ascat-disabled .ascat-chk{cursor:not-allowed}' +
    '.ascat-zoomhint{position:absolute;right:10px;bottom:44px;z-index:4;pointer-events:none;font:600 10px/1 inherit;color:#bcdcff;background:rgba(7,16,28,.62);border:1px solid rgba(43,156,255,.4);border-radius:4px;padding:3px 6px;opacity:0;transition:opacity .2s}' +
    '.ascat-mapframe:hover .ascat-zoomhint{opacity:.85}' +
    '.ascat-reset{position:absolute;right:10px;top:10px;z-index:5;background:rgba(14,26,48,.92);color:#cfe0f5;border:1px solid #2b6cb0;border-radius:7px;padding:6px 11px;font:600 12px/1 inherit;cursor:pointer}' +
    '.ascat-reset:hover{border-color:#2b9cff;color:#fff}';

  // ========================================================================
  function AscatViewer(root, opts) {
    opts = opts || {};
    this.root = root;
    this.base = (opts.base || BASE_DEFAULT).replace(/\/+$/, '');
    this.stormLock = opts.stormLock || null;
    this.lockFixed = !!opts.stormLock;          // CycloLab locks the storm at build
    this.center = opts.center || null;          // {lat,lon} override (CycloLab)
    this.region = opts.region || null;          // main-site region key
    this.density = 'auto';
    this.storms = [];                            // active storms w/ passes (F1 picker)
    this.zoomExt = null;                         // drag-zoom extent override (F3)
    this.backdrop = false;                       // satellite backdrop on? (F2)
    this.bdOpacity = 0.4;                         // PART 3: default dimmed so barbs read
    this.bdFrame = null;                         // matched clean-IR frame {t,band,bounds:[W,S,E,N]}
    this.bdImg = null;                           // loaded backdrop Image (CORS-clean)
    this._floaterCache = {};                     // slug -> per-storm floater manifest

    if (root && root.style) root.style.setProperty('--ascat-accent', C.accent);

    if (!opts.els && root && !el('ascat-canvas')) {
      if (!document.getElementById('ascat-embed-css')) {
        var ecss = document.createElement('style');
        ecss.id = 'ascat-embed-css'; ecss.textContent = ASCAT_EMBED_CSS;
        document.head.appendChild(ecss);
      }
      root.innerHTML = ASCAT_SCAFFOLD;
    }

    this.dom = (opts.els) || {
      root: root,
      stormWrap: el('ascat-storm-wrap'), stormSel: el('ascat-storm'),
      regionWrap: el('ascat-region-wrap'), regionBtn: el('ascat-region'),
      regionLab: el('ascat-region-lab'),
      passSel: el('ascat-pass'), densitySel: el('ascat-density'),
      backdropWrap: el('ascat-backdrop-wrap'), backdropChk: el('ascat-backdrop'),
      bdOpacity: el('ascat-bd-opacity'),
      reset: el('ascat-reset'), zoomhint: el('ascat-zoomhint'),
      styleSel: el('ascat-style'), canvas: el('ascat-canvas'),
      mapframe: el('ascat-mapframe'), status: el('ascat-status'),
      tooltip: el('ascat-tooltip'), empty: el('ascat-empty'),
      download: el('ascat-download'), copy: el('ascat-copy'),
      stats: el('ascat-stats')
    };

    this.ctx = this.dom.canvas ? this.dom.canvas.getContext('2d') : null;
    this.geo = { coast: null, countries: null, states: null };
    this.manifest = null;
    this.passMeta = [];        // manifest pass entries (lock-filtered)
    this.loaded = {};          // id -> full pass json
    this.selectedId = 'all';   // 'all' composite, or a specific pass id
    this.layout = null;
    this._cells = [];          // projected WVCs for hover/draw (map-local)
    this._picker = null;

    var s = null; try { s = localStorage.getItem(LS_STYLE); } catch (e) {}
    this.style = STYLES[s] ? s : 'sshws';

    this._wire();
    this._boot();
  }

  AscatViewer.prototype._S = function () { return STYLES[this.style] || STYLES.sshws; };

  AscatViewer.prototype._windColor = function (kt) {
    if (kt == null || isNaN(kt)) return C.muted;
    var scale = this._S().scale, col = scale[0][1];
    for (var i = 0; i < scale.length; i++) if (kt >= scale[i][0]) col = scale[i][1];
    return col;
  };

  AscatViewer.prototype._status = function (msg) {
    var s = this.dom.status; if (!s) return;
    if (msg) { s.style.display = 'flex'; var sp = s.querySelector('span'); if (sp) sp.textContent = msg; }
    else { s.style.display = 'none'; }
  };

  AscatViewer.prototype._showEmpty = function (on) {
    if (this.dom.empty) this.dom.empty.style.display = on ? 'block' : 'none';
    if (this.dom.mapframe) this.dom.mapframe.style.display = on ? 'none' : '';
  };

  // ---- boot ----
  AscatViewer.prototype._boot = function () {
    var self = this;
    this._status('Loading…');
    this._fetchRegionBackdrops();   // know which regions (incl. wide-area mosaic) have a backdrop
    Promise.all([this._loadBasemap(), this._fetchJson('/manifest.json', true)])
      .then(function (res) { return self._onManifest(res[1]); })
      .catch(function (e) { console.warn('ascat: boot failed', e); self._status(''); self._showEmpty(true); });
    if (typeof document !== 'undefined' && document.fonts && document.fonts.ready) {
      document.fonts.ready.then(function () { if (self._cells.length) self._draw(); });
    }
    this._schedulePoll();
  };

  AscatViewer.prototype._loadBasemap = function () {
    var self = this;
    var p = (window.TATRegions && TATRegions.loadGeo)
      ? TATRegions.loadGeo({ coast: '10m', land: '50m', states: true })
      : Promise.resolve({ coast: null, countries: null, states: null });
    // Lower-res 50m coastline, used ONLY by the whole-world Global view, where
    // the 10m coast reads thick + rough (every micro-inlet piles up at planet
    // scale). Optional + guarded: a miss leaves geo.coastLo null and Global
    // falls back to the 10m coast at the finer weight. Zoomed/basin/storm views
    // always use the crisp 10m coast (geo.coast) — untouched.
    var loP = fetch('/ne_50m_coastline.geojson')   // same-origin basemap (like TATRegions.loadGeo)
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
    return Promise.all([p, loP])
      .then(function (a) { self.geo = a[0] || { coast: null, countries: null, states: null }; self.geo.coastLo = a[1]; })
      .catch(function () { self.geo = { coast: null, countries: null, states: null, coastLo: null }; });
  };

  AscatViewer.prototype._fetchJson = function (path, noStore) {
    var url = this.base + path + (noStore ? ('?t=' + Date.now()) : '');
    return fetch(url, { cache: noStore ? 'no-store' : 'default' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + path); return r.json(); });
  };

  // does association tag `s` identify the storm keyed by `lock` (slug/atcf/name)?
  function stormMatch(s, lock) {
    if (!s) return false;
    return String(s.slug).toLowerCase() === lock ||
           (s.atcf && String(s.atcf).toLowerCase() === lock) ||
           (s.name && String(s.name).toLowerCase() === lock);
  }

  // ---- pure helpers (unit-tested) ----
  // the band frame whose time `t` is nearest `tMs`, with the gap in ms
  function nearestFrame(frames, tMs) {
    var best = null, bd = Infinity;
    for (var i = 0; i < (frames || []).length; i++) {
      var d = Math.abs((Date.parse(frames[i].t) || 0) - tMs);
      if (d < bd) { bd = d; best = frames[i]; }
    }
    return { frame: best, dms: bd };
  }
  // Storm backdrop frame: the MOST RECENT frame that carries a bare backdrop
  // (bd_key + bounds), preferring one already widened to the plot aspect (~1.667)
  // so it FILLS edge-to-edge. Matching the (possibly hours-old) overpass/pass time
  // can pick an older 12x12 SQUARE frame (pre-widen producer) that letterboxes into
  // the wide plot, leaving bare navy margins. Mirror of microwave.js backdropFrame.
  function backdropFrame(frames) {
    var withBd = [];
    for (var i = 0; i < (frames || []).length; i++) {
      var f = frames[i], src = f && (f.bd_key || f.backdrop_key);
      if (src && f.bounds && f.bounds.length === 4) withBd.push(f);
    }
    if (!withBd.length) return null;
    withBd.sort(function (a, b) { return (Date.parse(b.t) || 0) - (Date.parse(a.t) || 0); });
    for (var j = 0; j < withBd.length; j++) {
      var bb = withBd[j].bounds, asp = Math.abs(bb[2] - bb[0]) / Math.max(1e-6, Math.abs(bb[3] - bb[1]));
      if (asp >= 1.4) return withBd[j];
    }
    return withBd[0];
  }
  // Floater slug from an ATCF id: basin + 2-digit number, no year (WP072026 ->
  // wp07) -- lets the backdrop reach a storm's per-storm floater manifest even
  // after that storm is dropped from the top floaters/manifest.json.
  function floaterSlug(atcf) {
    atcf = String(atcf || '');
    return atcf.length >= 4 ? (atcf.slice(0, 2) + atcf.slice(2, 4)).toLowerCase() : null;
  }
  // inverse equirectangular projection: map-local (sx,sy) -> [lon,lat] in ext's frame
  function invProjectExt(ext, W, H, sx, sy) {
    return [ext[0] + (sx / W) * (ext[1] - ext[0]), ext[3] - (sy / H) * (ext[3] - ext[2])];
  }
  // two corner [lon,lat] -> normalized [w,e,s,n]
  function rectToBbox(a, b) {
    return [Math.min(a[0], b[0]), Math.max(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[1], b[1])];
  }

  // ---- manifest -> storm list + picker -> choose passes -> load them ----
  AscatViewer.prototype._onManifest = function (m) {
    this.manifest = m || {};
    this.allPasses = (m && m.passes) || [];
    this._buildStorms();            // unique active storms across all passes (F1)
    this._buildStormSelect();
    // CycloLab passes a fixed stormLock; the main page starts in Recent mode.
    if (this.lockFixed && this.stormLock && this.dom.stormWrap) this.dom.stormWrap.style.display = 'none';
    this._applyView();
  };

  // Unique storms (newest-first) that have at least one tagged pass, each with a
  // representative centre + pass count. Drives the storm picker.
  AscatViewer.prototype._buildStorms = function () {
    var seen = {}, out = [];
    for (var i = 0; i < this.allPasses.length; i++) {
      var st = this.allPasses[i].storms || [];
      for (var j = 0; j < st.length; j++) {
        var s = st[j], key = String(s.slug || s.atcf || s.name).toLowerCase();
        if (!key || key === 'undefined') continue;
        if (!seen[key]) {
          seen[key] = { slug: s.slug, atcf: s.atcf, name: s.name, basin: s.basin,
                        lat: s.lat, lon: s.lon, isInvest: s.is_invest, n: 0, key: key };
          out.push(seen[key]);
        }
        seen[key].n++;
        if (seen[key].lat == null && s.lat != null) { seen[key].lat = s.lat; seen[key].lon = s.lon; }
      }
    }
    this.storms = out;
  };

  AscatViewer.prototype._buildStormSelect = function () {
    var sel = this.dom.stormSel; if (!sel) return;
    sel.innerHTML = '';
    var rec = document.createElement('option');
    rec.value = ''; rec.textContent = 'Recent passes (by basin)';
    sel.appendChild(rec);
    for (var i = 0; i < this.storms.length; i++) {
      var s = this.storms[i], o = document.createElement('option');
      o.value = s.key;
      var nm = (s.name && String(s.name).toUpperCase()) || (s.atcf || s.slug);
      o.textContent = (s.isInvest ? '● ' : '') + nm + '  (' + s.n + ' pass' + (s.n === 1 ? '' : 'es') + ')';
      sel.appendChild(o);
    }
    sel.value = this.stormLock ? String(this.stormLock).toLowerCase() : '';
  };

  // Apply the current view (storm-centered if stormLock set, else region composite).
  AscatViewer.prototype._applyView = function () {
    var passes = this.allPasses || [];
    var inStorm = !!this.stormLock;
    if (inStorm) {
      var lock = String(this.stormLock).toLowerCase();
      passes = passes.filter(function (p) { return (p.storms || []).some(function (s) { return stormMatch(s, lock); }); });
      if (!this.center || !this.lockFixed) {
        this.center = null;
        for (var i = 0; i < passes.length && !this.center; i++) {
          var hit = (passes[i].storms || []).filter(function (s) { return stormMatch(s, lock); })[0];
          if (hit && hit.lat != null && hit.lon != null) this.center = { lat: hit.lat, lon: hit.lon };
        }
      }
    } else if (!this.region) {
      this.region = this._defaultRegion(passes);
    }
    if (this.dom.regionWrap) this.dom.regionWrap.style.display = inStorm ? 'none' : '';
    // The Vis/SWIR satellite backdrop is available in STORM-centered AND every
    // BASIN/REGIONAL view (each has a matching single-disk georeferenced cutout).
    // HEMISPHERE + GLOBAL ('nhem'/'shem'/'global') span multiple satellite disks
    // and the antimeridian, so no single-disk cutout fills them; the wide-area
    // day-Vis/night-SWIR MOSAIC that will is still in progress. Until it ships the
    // toggle is disabled + greyed with an explaining tooltip (NOT silently blank)
    // for those three views.
    // Hemisphere + Global have a backdrop ONLY once the wide-area mosaic is
    // published (backdrops.json gains a global/nhem/shem entry). Until then the
    // toggle is greyed with an explainer (not silently blank); once the mosaic is
    // there those views behave like any basin/regional view.
    var wideRegion = !this.stormLock && ['nhem', 'shem', 'global'].indexOf(this.region) >= 0;
    var hasMosaic = !!(this._regionBd && this._regionBd[this.region] && this._regionBd[this.region].key);
    var isWide = wideRegion && !hasMosaic;
    if (this.dom.backdropWrap) {
      this.dom.backdropWrap.classList.toggle('ascat-disabled', isWide);
      this.dom.backdropWrap.title = isWide
        ? 'Hemisphere & Global: wide-area Vis/SWIR mosaic in progress — basin, regional and storm views have a backdrop'
        : 'Satellite backdrop — Visible by day, Shortwave IR by night';
      if (this.dom.backdropChk) this.dom.backdropChk.disabled = isWide;
      if (this.dom.bdOpacity) this.dom.bdOpacity.disabled = isWide || !this.backdrop;
      if (isWide && this.backdrop) {
        this.backdrop = false;
        if (this.dom.backdropChk) this.dom.backdropChk.checked = false;
        this.bdImg = null; this.bdFrame = null;
      }
    }
    this.passMeta = passes;
    if (this.backdrop && !isWide) this._loadBackdrop();   // storm or basin/regional backdrop

    if (!passes.length) { this._status(''); this._showEmpty(true); return; }
    this._showEmpty(false);
    if (this.dom.regionLab && this.region && window.TATRegions) {
      var r = TATRegions.get(this.region); if (r) this.dom.regionLab.textContent = r.label;
    }
    this._buildPassSelect();
    this._loadActivePasses();
  };

  // Switch storm-centered target (key from the picker; '' = Recent mode).
  AscatViewer.prototype._setStorm = function (key) {
    this.stormLock = key || null;
    this.center = null; this.zoomExt = null; this.selectedId = 'all';
    this.bdFrame = null; this.bdImg = null;
    if (this.dom.reset) this.dom.reset.style.display = 'none';
    this._applyView();
    if (this.backdrop && this.stormLock) this._loadBackdrop();
  };

  // default region = the basin of the freshest storm tag, else Atlantic
  AscatViewer.prototype._defaultRegion = function (passes) {
    for (var i = 0; i < passes.length; i++) {
      var st = passes[i].storms || [];
      if (st.length && st[0].basin && BASIN_REGION[st[0].basin]) return BASIN_REGION[st[0].basin];
    }
    return 'atlantic';
  };

  // Which manifest passes feed the current view: the storm's (lock) or the newest
  // few overall (composite). Newest-first, capped.
  // Global view = whole-world recent composite (no storm lock, region 'global').
  AscatViewer.prototype._isGlobal = function () {
    return !this.stormLock && this.region === 'global';
  };

  AscatViewer.prototype._viewPasses = function () {
    var passes = this.passMeta.slice();
    if (this.selectedId !== 'all') passes = passes.filter(function (p) { return p.id === this.selectedId; }, this);
    return passes.slice(0, this._isGlobal() ? GLOBAL_MAX_PASSES : MAX_PASSES);
  };

  AscatViewer.prototype._loadActivePasses = function () {
    var self = this;
    var want = this._viewPasses();
    if (!want.length) { this._status(''); this._draw(); return; }
    this._status('Loading passes…');
    // current.json inlines the newest pass for an instant first paint
    var seedId = (this.manifest && this.manifest.current_id) || null;
    var jobs = want.map(function (meta) {
      if (self.loaded[meta.id]) return Promise.resolve(self.loaded[meta.id]);
      if (meta.id === seedId) {
        return self._fetchJson('/current.json', true)
          .then(function (c) { var p = c && c.pass; if (p) self.loaded[meta.id] = p; return p; })
          .catch(function () { return self._loadPass(meta.id); });
      }
      return self._loadPass(meta.id);
    });
    Promise.all(jobs).then(function () { self._status(''); self._layoutAndDraw(); })
      .catch(function (e) { console.warn('ascat: pass load failed', e); self._status(''); self._layoutAndDraw(); });
  };

  AscatViewer.prototype._loadPass = function (id) {
    var self = this;
    return this._fetchJson('/' + id + '.json', false)
      .then(function (p) { self.loaded[id] = p; return p; })
      .catch(function (e) { console.warn('ascat: pass ' + id + ' failed', e); return null; });
  };

  // ---- extent ----
  AscatViewer.prototype._extent = function () {
    if (this.zoomExt) return this.zoomExt.slice();   // F3: drag-zoom override (top)
    // Storm view with a backdrop: the frame IS the satellite cutout -- fit to its
    // WGS84 corner bounds so barbs clip to the imagery and the composite reads as
    // ONE coherent frame. The producer pre-widens the cutout to THIS map aspect
    // (BACKDROP_VIEW_ASPECT), so _aspectExtent below is a no-op and the imagery
    // fills edge-to-edge with no bare-basemap margins. Gated to storm mode: basin
    // views keep their region extent (a basin backdrop fills it via its own wide
    // bounds + the draw clip). bounds = [W,S,E,N]; _extent returns [W,E,S,N].
    if (this.stormLock && this.backdrop && this.bdImg && this.bdFrame && this.bdFrame.bounds) {
      var b = this.bdFrame.bounds;
      return [b[0], b[2], b[1], b[3]];   // storm view: frame == the cutout
    }
    if ((this.stormLock || this.center) && this.center) {
      var c = this.center, half = 8.0;            // +/-8 deg lat box around the storm
      return [c.lon - half, c.lon + half, c.lat - half, c.lat + half];
    }
    if (window.TATRegions && this.region) {
      var r = TATRegions.get(this.region);
      if (r) return TATRegions.extentOf(r);
    }
    return [-100, -5, 0, 55];                     // Atlantic fallback
  };

  // Aspect-correct an extent for a WxH px rect (clone of recon._aspectExtent):
  // expand (never crop) so on-screen degrees are proportional given the linear
  // (no cos-lat) projection.
  AscatViewer.prototype._aspectExtent = function (ext, W, H) {
    var w = ext[0], e = ext[1], s = ext[2], n = ext[3];
    var midLat = (s + n) / 2;
    var cosl = Math.max(0.12, Math.cos(midLat * Math.PI / 180));
    var lonSpan = e - w, latSpan = n - s;
    var target = (W / H) / cosl, cur = lonSpan / latSpan;
    if (cur < target) { var nl = latSpan * target, cx = (w + e) / 2; w = cx - nl / 2; e = cx + nl / 2; }
    else { var nh = lonSpan / target, cy = (s + n) / 2; s = cy - nh / 2; n = cy + nh / 2; }
    return [w, e, s, n];
  };

  // ---- layout (clone of recon, minus the time-series block) ----
  AscatViewer.prototype._layoutAndDraw = function () { this._layout(); this._draw(); };

  AscatViewer.prototype._layout = function () {
    var cv = this.dom.canvas; if (!cv) return;
    var availW = (this.dom.mapframe && this.dom.mapframe.clientWidth) || 900;
    availW = Math.max(360, availW);
    this._lastAvailW = availW;
    var figW = Math.max(availW, 760);
    var pad = 16, headerH = 54, footerH = 26;
    var mapH = Math.round(figW * 0.6);
    var figH = pad + headerH + mapH + 10 + footerH + pad;
    var dpr = Math.min((typeof window !== 'undefined' && window.devicePixelRatio) || 1, 2);
    this.dpr = dpr; this.figW = figW; this.figH = figH;
    cv.width = Math.round(figW * dpr); cv.height = Math.round(figH * dpr);
    cv.style.width = availW + 'px'; cv.style.height = (availW * figH / figW) + 'px';
    this.layout = {
      pad: pad,
      header: { x: pad, y: pad, w: figW - 2 * pad, h: headerH },
      map: { x: pad, y: pad + headerH, w: figW - 2 * pad, h: mapH },
      footerY: pad + headerH + mapH + 10 + footerH - 8
    };
  };

  AscatViewer.prototype._scale = function (g) { g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0); };

  AscatViewer.prototype._draw = function () {
    var g = this.ctx, L = this.layout; if (!g || !L) return;
    var S = this._S();
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, this.dom.canvas.width, this.dom.canvas.height);
    this._scale(g);
    g.fillStyle = S.bg; g.fillRect(0, 0, this.figW, this.figH);
    this._drawHeader(g);
    this._drawMap(g);
    this._drawFooter(g);
  };

  AscatViewer.prototype._drawHeader = function (g) {
    var h = this.layout.header;
    g.save(); g.textAlign = 'left'; g.textBaseline = 'alphabetic';
    g.fillStyle = C.fg; g.font = '800 19px ' + FONT;
    var scope = this.stormLock ? (this._lockName() || 'Storm')
      : (window.TATRegions && this.region && TATRegions.get(this.region) ? TATRegions.get(this.region).label : 'Recent');
    g.fillText(scope + '  ·  ASCAT Ocean Winds', h.x, h.y + 18);
    // subtitle: passes shown + freshest time + age
    // Health badge: the ingest flags manifest.health='stale' when the newest orbit
    // passes the source-aware health bound (a real stall: PO.DAAC >8 h, KNMI >36 h);
    // normal cadence stays 'ok'. On 'stale' we recolor the subtitle amber and append
    // a loud marker so a real stall is visible, not silent.
    var feedStale = !!(this.manifest && this.manifest.health === 'stale');
    g.fillStyle = feedStale ? '#ffb24d' : C.muted; g.font = '600 12.5px ' + FONT;
    var pv = this._loadedView();
    var sub;
    if (pv.length) {
      var newest = pv[0];
      var sensors = uniq(pv.map(function (p) { return p.sensor; })).join(' + ');
      sub = pv.length + ' pass' + (pv.length === 1 ? '' : 'es') + '  ·  ' + sensors +
        '  ·  latest ' + fmtZ(newest.start_utc) + ' (' + ageStr(newest.start_utc) + ')';
    } else { sub = 'No passes loaded for this view.'; }
    // F2: backdrop provenance (honest sat/band + frame age) appended to subtitle
    if (this.backdrop && this.bdImg && this.bdFrame) {
      sub += '   ·   ' + this.bdFrame.sat + ' ' + String(this.bdFrame.band).toUpperCase() +
        ' ' + fmtZ(this.bdFrame.t);
      // Backdrop-stale honesty: imagery under the barbs must never read as
      // current when its frame is hours old (producer stall or a satellite
      // outage, e.g. the 2026-07-15 GOES-19 safe mode). Age is computed from
      // the frame's own t at draw time, so the marker clears itself as soon
      // as fresh frames land.
      var bdAgeMs = this.bdFrame.t ? Date.now() - (Date.parse(this.bdFrame.t) || 0) : NaN;
      if (isFinite(bdAgeMs) && bdAgeMs > 3 * 3600e3) {
        g.fillStyle = '#ffb24d';
        sub += ' · BACKDROP STALE (' + (bdAgeMs >= 48 * 3600e3
          ? Math.round(bdAgeMs / 86400e3) + ' d'
          : (bdAgeMs / 3600e3).toFixed(1) + ' h') + ' old)';
      }
    }
    if (feedStale) sub += '   ·   FEED DELAYED';
    g.fillText(sub, h.x, h.y + 38);
    // sensor chip
    g.font = '700 11px ' + FONT; g.textAlign = 'right';
    var chip = '10 m wind · barbs FROM';
    var cw = g.measureText(chip).width + 16, cx = h.x + h.w - cw, cy = h.y + 6, ch = 18;
    roundRectPath(g, cx, cy, cw, ch, 4);
    g.fillStyle = 'rgba(43,156,255,0.14)'; g.fill();
    g.strokeStyle = 'rgba(43,156,255,0.5)'; g.lineWidth = 1; g.stroke();
    g.fillStyle = '#bcdcff'; g.textBaseline = 'middle';
    g.fillText(chip, h.x + h.w - 8, cy + ch / 2 + 0.5);
    g.restore();
  };

  // loaded full-pass objects for the current view, newest-first
  AscatViewer.prototype._loadedView = function () {
    var self = this, out = [];
    this._viewPasses().forEach(function (meta) { if (self.loaded[meta.id]) out.push(self.loaded[meta.id]); });
    out.sort(function (a, b) { return (Date.parse(b.start_utc) || 0) - (Date.parse(a.start_utc) || 0); });
    return out;
  };

  AscatViewer.prototype._drawMap = function (g) {
    var L = this.layout.map, S = this._S();
    var ext = this._aspectExtent(this._extent(), L.w, L.h);
    this._ext = ext;
    var proj = (window.TATRegions && TATRegions.project)
      ? function (lo, la) { return TATRegions.project(lo, la, ext, L.w, L.h); }
      : function (lo, la) { return [(lo - ext[0]) / (ext[1] - ext[0]) * L.w, (ext[3] - la) / (ext[3] - ext[2]) * L.h]; };

    g.save();
    g.beginPath(); g.rect(L.x, L.y, L.w, L.h); g.clip();
    g.translate(L.x, L.y);
    g.lineJoin = 'round'; g.lineCap = 'round';

    // 1) ocean + land fill (under the data)
    if (window.TATRegions && TATRegions.drawBasemapFill && this.geo && this.geo.countries) {
      TATRegions.drawBasemapFill(g, ext, { countries: this.geo.countries }, L.w, L.h, { ocean: S.ocean, land: S.land });
    } else { g.fillStyle = S.ocean; g.fillRect(0, 0, L.w, L.h); }
    // 1.5) satellite backdrop (F2): the storm's floater frame, georef'd to its 12 deg box
    this._drawBackdrop(g, proj);
    // 2) graticule
    this._drawGraticule(g, ext, L.w, L.h);
    // 3) coastline + faint state borders ON TOP of the fill (and imagery)
    if (window.TATRegions && TATRegions.drawBasemapLines) {
      var lineGeo = this.geo;
      var lineOpts = { coast: S.coast, coastLw: S.coastLw, state: S.state, stateLw: S.stateLw };
      if (this._isGlobal()) {
        // Global only: the 10m coast reads thick + rough at whole-world scale and
        // competes with the wind swaths. Swap to the cleaner 50m coastline (falls
        // back to 10m if it didn't load) and a finer weight so the geography frames
        // the field without overpowering it. Zoomed/basin/storm views keep the
        // crisp 10m coast at full weight (untouched).
        lineGeo = { coast: (this.geo && this.geo.coastLo) || (this.geo && this.geo.coast),
                    states: this.geo && this.geo.states };
        lineOpts = { coast: S.coast, coastLw: 0.6, state: S.state, stateLw: 0.4 };
      }
      TATRegions.drawBasemapLines(g, ext, lineGeo, L.w, L.h, lineOpts);
    }
    // 4) the data: Global = filled colored swaths (whole-world composite);
    //    storm/basin = wind barbs (newest pass on top).
    if (this._isGlobal()) this._drawSwaths(g, proj, ext, L.w, L.h);
    else this._drawBarbs(g, proj, ext, L.w, L.h, S);
    g.restore();
    this._drawDragRect(g, L);             // F3 rubber-band (figure space)

    // map border + colorbar + watermark (figure space)
    g.save(); g.strokeStyle = C.border; g.lineWidth = 1;
    g.strokeRect(L.x + 0.5, L.y + 0.5, L.w - 1, L.h - 1); g.restore();
    this._drawLegend(g);
    this._drawWatermark(g, L);
  };

  // Collect WVCs in view from all loaded passes, thin by a screen-space spatial
  // hash (one barb per `step` px cell), then draw barbs. Caches projected cells
  // for hover. Newest pass wins a contested cell (added last).
  AscatViewer.prototype._drawBarbs = function (g, proj, ext, W, H, S) {
    var step = DENSITY[this.density] || DENSITY.auto;
    var cols = Math.max(1, Math.ceil(W / step));
    var grid = {};                       // cellKey -> chosen point
    var passes = this._loadedView().slice().reverse();   // oldest first; newest overwrites
    var wrap = (ext[1] > 180 || ext[0] < -180);
    for (var pi = 0; pi < passes.length; pi++) {
      var p = passes[pi], w = p.wvc; if (!w || !w.la) continue;
      var la = w.la, lo = w.lo, kt = w.kt, dr = w.dir, n = la.length;
      for (var i = 0; i < n; i++) {
        var lon = lo[i], lat = la[i];
        if (lat < ext[2] || lat > ext[3]) continue;
        var Ln = lon; if (wrap && Ln < ext[0]) Ln += 360;
        if (Ln < ext[0] || Ln > ext[1]) continue;
        var xy = proj(lon, lat);
        var key = (Math.floor(xy[0] / step)) + ':' + (Math.floor(xy[1] / step));
        grid[key] = { x: xy[0], y: xy[1], kt: kt[i], dir: dr[i], sensor: p.sensor, t: p.start_utc };
      }
    }
    var cells = [];
    for (var k in grid) if (grid.hasOwnProperty(k)) cells.push(grid[k]);
    this._cells = cells;

    g.save(); g.lineJoin = 'round'; g.lineCap = 'round';
    // halo pass: over a satellite backdrop, a dark casing keeps barbs legible
    var halo = this.backdrop && this.bdImg;
    if (halo) {
      for (var h = 0; h < cells.length; h++) this._barb(g, cells[h].x, cells[h].y, cells[h].kt, cells[h].dir, 'rgba(5,10,20,0.82)', S.barbLw + 2.4);
    }
    for (var c = 0; c < cells.length; c++) {
      var cell = cells[c];
      this._barb(g, cell.x, cell.y, cell.kt, cell.dir, this._windColor(cell.kt), S.barbLw);
    }
    g.restore();
  };

  // Global composite: filled colored cells (one per WVC) instead of barbs, so the
  // whole-world recent ASCAT swaths read as a continuous filled wind field on the
  // plain map. Fine screen-space grid (newest pass overwrites); hover still works
  // off this._cells. No satellite backdrop at global (a world montage has none).
  AscatViewer.prototype._drawSwaths = function (g, proj, ext, W, H) {
    var step = 2;                                  // px cell -> continuous swaths
    var grid = {};
    var passes = this._loadedView().slice().reverse();   // oldest first; newest wins
    var wrap = (ext[1] > 180 || ext[0] < -180);
    for (var pi = 0; pi < passes.length; pi++) {
      var p = passes[pi], w = p.wvc; if (!w || !w.la) continue;
      var la = w.la, lo = w.lo, kt = w.kt, dr = w.dir, n = la.length;
      for (var i = 0; i < n; i++) {
        var lat = la[i]; if (lat < ext[2] || lat > ext[3]) continue;
        var Ln = lo[i]; if (wrap && Ln < ext[0]) Ln += 360;
        if (Ln < ext[0] || Ln > ext[1]) continue;
        var xy = proj(lo[i], lat);
        var key = (Math.floor(xy[0] / step)) + ':' + (Math.floor(xy[1] / step));
        grid[key] = { x: xy[0], y: xy[1], kt: kt[i], dir: dr[i], sensor: p.sensor, t: p.start_utc };
      }
    }
    var cells = [];
    for (var k in grid) if (grid.hasOwnProperty(k)) cells.push(grid[k]);
    this._cells = cells;
    g.save();
    for (var c = 0; c < cells.length; c++) {
      g.fillStyle = this._windColor(cells[c].kt);
      g.fillRect(cells[c].x - step / 2, cells[c].y - step / 2, step + 0.7, step + 0.7);
    }
    g.restore();
  };

  // One standard wind barb at (x,y); shaft points FROM the wind (dirFrom deg).
  // half=5kt, full=10kt, pennant=50kt; calm (<5kt) = small open ring. Clone of
  // recon._barb (minus the SFMR-suspect dashing).
  AscatViewer.prototype._barb = function (g, x, y, kt, dirFrom, color, lw) {
    if (kt == null || dirFrom == null || isNaN(kt) || isNaN(dirFrom)) return;
    var SHAFT = 13, BARB = 5.5, PEN = 6.5, SP = 3.4;
    var a = dirFrom * Math.PI / 180;
    var ux = Math.sin(a), uy = -Math.cos(a), px = -uy, py = ux;
    g.save(); if (lw) g.lineWidth = lw; g.strokeStyle = color; g.fillStyle = color;
    var spd = Math.round(kt / 5) * 5;
    if (spd < 5) { g.beginPath(); g.arc(x, y, 2.2, 0, 6.2832); g.stroke(); g.restore(); return; }
    var ex = x + ux * SHAFT, ey = y + uy * SHAFT;
    g.beginPath(); g.moveTo(x, y); g.lineTo(ex, ey); g.stroke();
    g.beginPath(); g.arc(x, y, 1.3, 0, 6.2832); g.fill();
    var rem = spd, pos = SHAFT;
    var nPen = Math.floor(rem / 50); rem -= nPen * 50;
    var nFull = Math.floor(rem / 10); rem -= nFull * 10;
    var nHalf = Math.floor(rem / 5), k;
    for (k = 0; k < nPen; k++) {
      var b0x = x + ux * pos, b0y = y + uy * pos, b1x = x + ux * (pos - SP), b1y = y + uy * (pos - SP);
      var tipx = b0x + px * PEN, tipy = b0y + py * PEN;
      g.beginPath(); g.moveTo(b0x, b0y); g.lineTo(tipx, tipy); g.lineTo(b1x, b1y); g.closePath(); g.fill();
      pos -= SP + 1.3;
    }
    if (nPen) pos -= 0.9;
    for (k = 0; k < nFull; k++) {
      var f0x = x + ux * pos, f0y = y + uy * pos;
      g.beginPath(); g.moveTo(f0x, f0y); g.lineTo(f0x + px * BARB, f0y + py * BARB); g.stroke();
      pos -= SP;
    }
    for (k = 0; k < nHalf; k++) {
      var hpos = (nPen === 0 && nFull === 0) ? (pos - SP) : pos;
      var h0x = x + ux * hpos, h0y = y + uy * hpos;
      g.beginPath(); g.moveTo(h0x, h0y); g.lineTo(h0x + px * (BARB / 2), h0y + py * (BARB / 2)); g.stroke();
      pos -= SP;
    }
    g.restore();
  };

  // ===== F2: satellite backdrop (storm floater frame, georef'd) =============
  AscatViewer.prototype._cdnRoot = function () { return this.base.replace(/\/[^/]+\/?$/, ''); };

  // Find the floater whose storm matches this view's locked storm, pick the band
  // frame nearest the current pass time, and load it CORS-clean. Async; redraws.
  AscatViewer.prototype._loadBackdrop = function () {
    var self = this;
    if (!this.backdrop) { this.bdImg = null; this.bdFrame = null; return; }
    // Non-storm view (basin/regional OR the wide-area hemisphere/global mosaic):
    // both come from backdrops.json keyed by region. Global is no longer special-
    // cased off here now that it has a mosaic entry.
    if (!this.stormLock) { this._loadRegionBackdrop(); return; }
    if (!this.center) { this.bdImg = null; return; }
    var storm = this._currentStorm(); if (!storm) return;
    var root = this._cdnRoot(), band = 'ir';
    var matchT = this._matchTime();
    var perStorm = function (slug) {
      var cached = self._floaterCache[slug];
      var p = cached ? Promise.resolve(cached) : fetch(root + '/floaters/' + slug + '/manifest.json' + '?t=' + Date.now())
        .then(function (r) { if (!r.ok) throw 0; return r.json(); }).then(function (j) { self._floaterCache[slug] = j; return j; });
      return p.then(function (fm) {
        var b = (fm.bands && (fm.bands[band] || fm.bands.irbd || fm.bands.ir)) || null;
        var frames = (b && b.frames) || [];
        if (!frames.length) throw 0;
        var best = backdropFrame(frames);
        if (!best) { self.bdImg = null; self.bdFrame = null; self._draw(); return; }
        var bd = Math.abs((Date.parse(best.t) || 0) - matchT);
        // Draw ONLY the bare chrome-free grayscale Vis/SWIR cutout (bd_key + WGS84
        // bounds from the backdrop producer). The viewer owns the single shared
        // graticule/coastline/colorbar/legend/watermark, so the finished CHROMED
        // floater frame (best.key) is never painted under the barbs. No clean raster
        // yet -> draw nothing (honest blank), never chrome.
        var src = best.bd_key || best.backdrop_key || null;
        if (!src || !best.bounds) { self.bdImg = null; self.bdFrame = null; self._draw(); return; }
        var img = new Image(); img.crossOrigin = 'anonymous';
        self.bdFrame = { t: best.t, band: (best.bd_product || 'Satellite'), bounds: best.bounds,
                         sat: self._floaterSat(storm.basin), ageMs: bd };
        // relayout (not just redraw): the extent now follows the cutout bounds.
        img.onload = function () { self.bdImg = img; self._layoutAndDraw(); };
        img.onerror = function () { self.bdImg = null; self.bdFrame = null; self._layoutAndDraw(); };
        img.src = root + '/' + src;
      });
    };
    // Try the slug derived from the ATCF id FIRST (basin+number, no year:
    // WP072026 -> wp07) so a storm DROPPED from the top floaters/manifest.json
    // (retired / went extratropical) but whose per-storm floater data still exists
    // STILL shows a backdrop. Fall back to the top-manifest name/atcf match.
    var topMatch = function () {
      return fetch(root + '/floaters/manifest.json?t=' + Date.now())
        .then(function (r) { if (!r.ok) throw 0; return r.json(); })
        .then(function (top) {
          var st = (top.storms || []), hit = null;
          for (var i = 0; i < st.length; i++) {
            var f = st[i], fid = String(f.id || '').toLowerCase(), fnm = String(f.name || '').toLowerCase();
            if ((storm.atcf && fid.indexOf(String(storm.atcf).toLowerCase()) >= 0) ||
                (storm.name && fnm === String(storm.name).toLowerCase())) { hit = f; break; }
          }
          if (!hit || !hit.slug) throw 0;
          return perStorm(hit.slug);
        });
    };
    var derived = floaterSlug(storm.atcf);
    (derived ? perStorm(derived).catch(topMatch) : topMatch())
      .catch(function () { self.bdImg = null; self.bdFrame = null; self._draw(); });
  };

  // Basin/regional Vis/SWIR backdrop: a per-region georeferenced cutout from the
  // shared backdrop producer (tsr held PR #22), indexed in floaters/backdrops.json
  // as { backdrops: { <region>: { product:"Vis"|"SWIR", t, bounds:[W,S,E,N], key } } }
  // (region slugs match this.region: atlantic / epac / wpac). Absent (until that
  // producer deploys) -> draw nothing (honest); the region extent is kept (the
  // raster fills it via its bounds). One-shot cached fetch.
  // Fetch backdrops.json ONCE up front so the wide-area (hemisphere/global) toggle
  // gating in _applyView knows whether the mosaic is published. Re-evaluates the
  // view if we're sitting on a wide region when it arrives (so the toggle un-greys
  // without a manual region change).
  AscatViewer.prototype._fetchRegionBackdrops = function () {
    var self = this, root = this._cdnRoot();
    if (self._regionBd) return;
    fetch(root + '/floaters/backdrops.json?t=' + Date.now())
      .then(function (r) { if (!r.ok) throw 0; return r.json(); })
      .then(function (j) {
        self._regionBd = (j && j.backdrops) || {};
        if (!self.stormLock && ['nhem', 'shem', 'global'].indexOf(self.region) >= 0) self._applyView();
      })
      .catch(function () {});
  };

  AscatViewer.prototype._loadRegionBackdrop = function () {
    var self = this, root = this._cdnRoot(), region = this.region;
    var draw = function (idx) {
      var bk = idx && idx[region];
      if (!bk || !bk.key || !bk.bounds) { self.bdImg = null; self.bdFrame = null; self._draw(); return; }
      var img = new Image(); img.crossOrigin = 'anonymous';
      self.bdFrame = { t: bk.t || null, band: bk.product || 'Satellite',
                       bounds: bk.bounds, sat: bk.sat || '', ageMs: 0 };
      img.onload = function () { if (self.region === region) { self.bdImg = img; self._draw(); } };
      img.onerror = function () { self.bdImg = null; self.bdFrame = null; self._draw(); };
      img.src = root + '/' + bk.key;
    };
    if (self._regionBd) { draw(self._regionBd); return; }
    fetch(root + '/floaters/backdrops.json?t=' + Date.now())
      .then(function (r) { if (!r.ok) throw 0; return r.json(); })
      .then(function (j) { self._regionBd = (j && j.backdrops) || {}; draw(self._regionBd); })
      .catch(function () { self.bdImg = null; self.bdFrame = null; self._draw(); });
  };

  AscatViewer.prototype._currentStorm = function () {
    var lock = String(this.stormLock || '').toLowerCase();
    for (var i = 0; i < this.storms.length; i++) if (this.storms[i].key === lock) return this.storms[i];
    return null;
  };
  AscatViewer.prototype._matchTime = function () {
    var lv = this._loadedView();
    return (lv.length && Date.parse(lv[0].start_utc)) || Date.now();
  };
  AscatViewer.prototype._floaterSat = function (basin) {
    return (['WP', 'SH', 'SP', 'IO', 'SI', 'AU'].indexOf(String(basin).toUpperCase()) >= 0) ? 'Himawari' : 'GOES';
  };

  AscatViewer.prototype._drawBackdrop = function (g, proj) {
    if (!this.backdrop || !this.bdImg || !this.bdImg.complete || !this.bdImg.naturalWidth) return;
    // Georeference by the raster's real WGS84 corner bounds [W,S,E,N] (the
    // bounds re-center as the storm drifts) rather than a hard-coded box.
    var bf = this.bdFrame, W, S, E, N;
    if (bf && bf.bounds) { W = bf.bounds[0]; S = bf.bounds[1]; E = bf.bounds[2]; N = bf.bounds[3]; }
    else if (this.center) { var c = this.center, half = 6; W = c.lon - half; E = c.lon + half; S = c.lat - half; N = c.lat + half; }
    else return;
    var tl = proj(W, N), br = proj(E, S);
    // Bleed ~1px outward so sub-pixel rounding never leaves a bare-basemap seam at
    // the frame edge when the (aspect-matched) backdrop fills the whole plot. The
    // map rect clip (set in _drawMap) crops the overflow.
    var x = tl[0] - 1, y = tl[1] - 1, w = (br[0] - tl[0]) + 2, h = (br[1] - tl[1]) + 2;
    g.save(); g.globalAlpha = this.bdOpacity;
    try { g.drawImage(this.bdImg, x, y, w, h); } catch (e) {}
    g.restore();
  };

  // ===== F3: drag-bbox-to-zoom (pure extent math on loaded WVCs) ============
  AscatViewer.prototype._invProject = function (sx, sy) {
    return invProjectExt(this._ext, this.layout.map.w, this.layout.map.h, sx, sy);
  };
  // map-local (x,y) from a pointer event
  AscatViewer.prototype._evXY = function (ev) {
    var L = this.layout && this.layout.map, cv = this.dom.canvas;
    if (!L || !cv) return null;
    var rect = cv.getBoundingClientRect(), sx = cv.width / rect.width / this.dpr;
    return { x: (ev.clientX - rect.left) * sx - L.x, y: (ev.clientY - rect.top) * sx - L.y };
  };
  AscatViewer.prototype._drawDragRect = function (g, L) {
    var d = this._drag; if (!d || !d.active) return;
    var x0 = Math.min(d.x0, d.x1), y0 = Math.min(d.y0, d.y1), w = Math.abs(d.x1 - d.x0), h = Math.abs(d.y1 - d.y0);
    g.save();
    g.fillStyle = 'rgba(43,156,255,0.14)'; g.fillRect(L.x + x0, L.y + y0, w, h);
    g.strokeStyle = 'rgba(43,156,255,0.95)'; g.lineWidth = 1.2; g.setLineDash([5, 3]);
    g.strokeRect(L.x + x0 + 0.5, L.y + y0 + 0.5, w, h);
    g.restore();
  };
  AscatViewer.prototype._applyZoom = function () {
    var d = this._drag; if (!d) return;
    if (Math.abs(d.x1 - d.x0) < 8 || Math.abs(d.y1 - d.y0) < 8) return;   // a click, not a drag
    this.zoomExt = rectToBbox(this._invProject(d.x0, d.y0), this._invProject(d.x1, d.y1));
    if (this.dom.reset) this.dom.reset.style.display = '';
    this._layoutAndDraw();
  };
  AscatViewer.prototype._resetZoom = function () {
    this.zoomExt = null; if (this.dom.reset) this.dom.reset.style.display = 'none'; this._layoutAndDraw();
  };

  // faint lat/lon graticule with edge labels (clone of recon._drawGraticule)
  AscatViewer.prototype._drawGraticule = function (g, ext, W, H) {
    var S = this._S();
    var lonSpan = ext[1] - ext[0], latSpan = ext[3] - ext[2];
    var step = (Math.max(lonSpan, latSpan) > 40) ? 10 : (Math.max(lonSpan, latSpan) > 12 ? 5 : 2);
    g.save(); g.strokeStyle = S.grid; g.lineWidth = 0.6; g.beginPath();
    var l0 = Math.ceil(ext[0] / step) * step, lon, x;
    for (lon = l0; lon <= ext[1]; lon += step) { x = (lon - ext[0]) / lonSpan * W; g.moveTo(x, 0); g.lineTo(x, H); }
    var b0 = Math.ceil(ext[2] / step) * step, lat, y;
    for (lat = b0; lat <= ext[3]; lat += step) { y = (ext[3] - lat) / latSpan * H; g.moveTo(0, y); g.lineTo(W, y); }
    g.stroke();
    g.fillStyle = S.gridLab; g.font = '600 9px ' + FONT;
    g.textBaseline = 'bottom'; g.textAlign = 'center';
    for (lon = l0; lon <= ext[1]; lon += step) { x = (lon - ext[0]) / lonSpan * W; if (x < 14 || x > W - 14) continue; g.fillText(this._lonLab(lon), x, H - 2); }
    g.textBaseline = 'middle'; g.textAlign = 'left';
    for (lat = b0; lat <= ext[3]; lat += step) { y = (ext[3] - lat) / latSpan * H; if (y < 9 || y > H - 9) continue; g.fillText(this._latLab(lat), 3, y); }
    g.restore();
  };
  AscatViewer.prototype._lonLab = function (lon) { var l = lon; while (l > 180) l -= 360; while (l < -180) l += 360; return Math.abs(Math.round(l)) + (l >= 0 ? 'E' : 'W'); };
  AscatViewer.prototype._latLab = function (lat) { return Math.abs(Math.round(lat)) + (lat >= 0 ? 'N' : 'S'); };

  // discrete TC kt-scale colorbar (clone of recon._drawLegend, no glyph key)
  AscatViewer.prototype._drawLegend = function (g) {
    var L = this.layout.map, scale = this._S().scale;
    var nseg = scale.length - 1, pad = 8, tri = 9, barH = 11;
    var barW = Math.min(L.w - 84, 452), segW = (barW - 2 * tri) / nseg;
    var labH = 11, capH = 12, boxW = barW + pad * 2, boxH = pad * 2 + barH + 4 + labH + 5 + capH;
    var x = L.x + 10, y = L.y + L.h - boxH - 9;
    g.save(); roundRectPath(g, x, y, boxW, boxH, 6);
    g.fillStyle = 'rgba(7,16,28,0.86)'; g.fill();
    g.strokeStyle = C.border; g.lineWidth = 1; g.stroke();
    var bx = x + pad, by = y + pad, mid = by + barH / 2;
    g.fillStyle = scale[0][1];
    g.beginPath(); g.moveTo(bx + tri, by); g.lineTo(bx + tri, by + barH); g.lineTo(bx, mid); g.closePath(); g.fill();
    for (var i = 0; i < nseg; i++) { g.fillStyle = scale[i][1]; g.fillRect(bx + tri + i * segW, by, segW + 0.6, barH); }
    var rx = bx + tri + nseg * segW; g.fillStyle = scale[nseg][1];
    g.beginPath(); g.moveTo(rx, by); g.lineTo(rx, by + barH); g.lineTo(rx + tri, mid); g.closePath(); g.fill();
    g.strokeStyle = 'rgba(220,232,246,0.30)'; g.lineWidth = 0.8;
    g.strokeRect(bx + tri + 0.5, by + 0.5, nseg * segW - 1, barH - 1);
    var ly = by + barH + 4;
    g.font = '600 8px ' + FONT; g.textAlign = 'center'; g.textBaseline = 'top';
    for (i = 0; i < scale.length; i++) {
      var tx = bx + tri + i * segW;
      g.strokeStyle = 'rgba(220,232,246,0.42)'; g.lineWidth = 0.8;
      g.beginPath(); g.moveTo(tx, by + barH); g.lineTo(tx, by + barH + 2.5); g.stroke();
      g.fillStyle = C.fg; g.fillText(String(scale[i][0]), tx, ly);
    }
    g.fillStyle = C.muted; g.font = '600 9px ' + FONT; g.textAlign = 'left';
    g.fillText('10 m ocean-surface wind speed (kt) · C-band underestimates extreme cores', bx, ly + labH + 5);
    g.restore();
  };

  AscatViewer.prototype._drawWatermark = function (g, L) {
    g.save(); g.font = '700 12px ' + FONT; g.textAlign = 'right'; g.textBaseline = 'top';
    g.shadowColor = 'rgba(4,9,16,0.85)'; g.shadowBlur = 3;
    g.fillStyle = 'rgba(233,241,250,0.5)';
    g.fillText(WATERMARK, L.x + L.w - 10, L.y + 9);
    g.restore();
  };

  AscatViewer.prototype._drawFooter = function (g) {
    g.save(); g.font = '500 10.5px ' + FONT; g.textAlign = 'left'; g.textBaseline = 'alphabetic';
    g.fillStyle = C.muted;
    var disc = (this.manifest && this.manifest.disclosure) ||
      'C-band ASCAT underestimates extreme TC-core winds; rain-flagged cells removed; swaths are intermittent (near-real-time, per-orbit, typically a few hours old).';
    var maxw = this.layout.map.w - 150;
    g.fillText(this._ellipsize(g, disc, maxw), this.layout.pad, this.layout.footerY);
    g.textAlign = 'right'; g.fillStyle = C.muted; g.font = '600 10.5px ' + FONT;
    g.fillText(CREDIT, this.layout.pad + this.layout.map.w, this.layout.footerY);
    g.restore();
  };
  AscatViewer.prototype._ellipsize = function (g, txt, maxw) {
    if (g.measureText(txt).width <= maxw) return txt;
    var s = txt;
    while (s.length > 8 && g.measureText(s + '…').width > maxw) s = s.slice(0, -1);
    return s + '…';
  };

  // ---- hover ----
  AscatViewer.prototype._hover = function (ev) {
    var tip = this.dom.tooltip, L = this.layout && this.layout.map;
    if (!tip || !L || !this._cells.length) return;
    var rect = this.dom.canvas.getBoundingClientRect();
    var sx = this.dom.canvas.width / rect.width / this.dpr;
    var mx = (ev.clientX - rect.left) * sx - L.x, my = (ev.clientY - rect.top) * sx - L.y;
    if (mx < 0 || my < 0 || mx > L.w || my > L.h) { tip.style.display = 'none'; return; }
    var best = null, bestD = 14 * 14;
    for (var i = 0; i < this._cells.length; i++) {
      var p = this._cells[i], dx = p.x - mx, dy = p.y - my, d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = p; }
    }
    if (!best) { tip.style.display = 'none'; return; }
    var lines = [Math.round(best.kt) + ' kt' + (best.dir != null ? (' from ' + Math.round(best.dir) + '°') : '')];
    if (best.sensor) lines.push(best.sensor + (best.t ? ('  ·  ' + fmtZ(best.t)) : ''));
    tip.style.display = 'block';
    tip.style.left = (ev.clientX - rect.left + 12) + 'px';
    tip.style.top = (ev.clientY - rect.top + 12) + 'px';
    tip.innerHTML = lines.join('<br>');
  };

  // ---- export (clone of recon) ----
  AscatViewer.prototype._exportName = function () {
    var base = this.stormLock ? ('storm_' + this.stormLock) : (this.region || 'recent');
    return 'ascat_' + String(base).replace(/[^A-Za-z0-9_-]/g, '_') + '.png';
  };
  AscatViewer.prototype._download = function () {
    var cv = this.dom.canvas; if (!cv || !this._cells.length) return;
    var self = this;
    function viaDataUrl() { try { var a = document.createElement('a'); a.href = cv.toDataURL('image/png'); a.download = self._exportName(); document.body.appendChild(a); a.click(); document.body.removeChild(a); } catch (e) { console.warn('ascat: export failed', e); } }
    if (cv.toBlob) {
      cv.toBlob(function (blob) {
        if (!blob) { viaDataUrl(); return; }
        var u = URL.createObjectURL(blob), a = document.createElement('a');
        a.href = u; a.download = self._exportName(); document.body.appendChild(a); a.click(); document.body.removeChild(a);
        requestAnimationFrame(function () { URL.revokeObjectURL(u); });
      }, 'image/png');
    } else { viaDataUrl(); }
  };
  AscatViewer.prototype._copy = function () {
    var cv = this.dom.canvas; if (!cv || !this._cells.length) return;
    var self = this;
    if (!(navigator.clipboard && window.ClipboardItem && cv.toBlob)) { this._download(); return; }
    cv.toBlob(function (blob) {
      if (!blob) { self._download(); return; }
      try { navigator.clipboard.write([new window.ClipboardItem({ 'image/png': blob })]).then(function () { self._flash('Copied'); }, function () { self._download(); }); }
      catch (e) { self._download(); }
    }, 'image/png');
  };
  AscatViewer.prototype._flash = function (msg) { var b = this.dom.copy; if (!b) return; var o = b.textContent; b.textContent = msg; setTimeout(function () { b.textContent = o; }, 1400); };

  // ---- selectors ----
  AscatViewer.prototype._buildPassSelect = function () {
    var sel = this.dom.passSel; if (!sel) return;
    sel.innerHTML = '';
    var all = document.createElement('option'); all.value = 'all';
    all.textContent = this.stormLock ? 'All passes (composite)' : 'Latest (composite)';
    sel.appendChild(all);
    var shown = this.passMeta.slice(0, this.stormLock ? 20 : MAX_PASSES);
    for (var i = 0; i < shown.length; i++) {
      var p = shown[i], o = document.createElement('option');
      o.value = p.id;
      // Concise label: sensor + time. No appended storm-name summary blurb
      // (redundant in storm-locked mode where the picker is already filtered).
      o.textContent = p.sensor + '  ·  ' + fmtZ(p.start_utc);
      sel.appendChild(o);
    }
    sel.value = this.selectedId;
  };

  AscatViewer.prototype._lockName = function () {
    for (var i = 0; i < this.passMeta.length; i++) {
      var st = this.passMeta[i].storms || [];
      for (var j = 0; j < st.length; j++) {
        var s = st[j], lock = String(this.stormLock).toLowerCase();
        if (String(s.slug).toLowerCase() === lock || (s.atcf && String(s.atcf).toLowerCase() === lock) || (s.name && String(s.name).toLowerCase() === lock)) return s.name || s.atcf;
      }
    }
    return null;
  };

  AscatViewer.prototype._setRegion = function (key) {
    this.region = key;
    this.zoomExt = null;
    if (this.dom.regionLab && window.TATRegions) { var r = TATRegions.get(key); if (r) this.dom.regionLab.textContent = r.label; }
    this._applyView();   // re-evaluate backdrop gating (off at Global) + reload + redraw
  };

  AscatViewer.prototype._wire = function () {
    var self = this;
    if (this.dom.stormSel) this.dom.stormSel.addEventListener('change', function () { self._setStorm(this.value); });
    if (this.dom.passSel) this.dom.passSel.addEventListener('change', function () { self.selectedId = this.value; self._loadActivePasses(); if (self.backdrop) self._loadBackdrop(); });
    if (this.dom.densitySel) this.dom.densitySel.addEventListener('change', function () { self.density = this.value; self._draw(); });
    if (this.dom.backdropChk) this.dom.backdropChk.addEventListener('change', function () {
      self.backdrop = this.checked;
      if (self.dom.bdOpacity) self.dom.bdOpacity.disabled = !this.checked;
      if (self.backdrop) self._loadBackdrop(); else { self.bdImg = null; self.bdFrame = null; self._layoutAndDraw(); }
    });
    if (this.dom.bdOpacity) this.dom.bdOpacity.addEventListener('input', function () { self.bdOpacity = Math.max(0.1, Math.min(1, (+this.value || 40) / 100)); self._draw(); });
    if (this.dom.reset) this.dom.reset.addEventListener('click', function () { self._resetZoom(); });
    if (this.dom.download) this.dom.download.addEventListener('click', function () { self._download(); });
    if (this.dom.copy) this.dom.copy.addEventListener('click', function () { self._copy(); });
    if (this.dom.styleSel) {
      this.dom.styleSel.innerHTML = '';
      for (var key in STYLES) if (STYLES.hasOwnProperty(key)) {
        var o = document.createElement('option'); o.value = key; o.textContent = STYLES[key].label;
        if (key === this.style) o.selected = true; this.dom.styleSel.appendChild(o);
      }
      this.dom.styleSel.addEventListener('change', function () { self.style = STYLES[this.value] ? this.value : 'sshws'; try { localStorage.setItem(LS_STYLE, self.style); } catch (e) {} self._draw(); });
    }
    if (this.dom.regionBtn && window.TATRegions && TATRegions.RegionPicker && !this.stormLock) {
      this.dom.regionBtn.addEventListener('click', function () {
        if (!self._picker) self._picker = new TATRegions.RegionPicker({ current: self.region, onPick: function (k) { self._setRegion(k); } });
        self._picker.setCurrent(self.region); self._picker.open();
      });
    } else if (this.dom.regionWrap && this.stormLock) { this.dom.regionWrap.style.display = 'none'; }
    if (this.dom.canvas) {
      // drag = zoom (F3); plain move = hover tooltip. Mouse + touch.
      var down = function (ev) {
        var p = self._evXY(ev); if (!p) return;
        var L = self.layout && self.layout.map; if (!L || p.x < 0 || p.y < 0 || p.x > L.w || p.y > L.h) return;
        self._drag = { x0: p.x, y0: p.y, x1: p.x, y1: p.y, active: true };
        if (self.dom.tooltip) self.dom.tooltip.style.display = 'none';
        ev.preventDefault();
      };
      var move = function (ev) {
        if (self._drag && self._drag.active) { var p = self._evXY(ev); if (p) { self._drag.x1 = p.x; self._drag.y1 = p.y; self._draw(); } return; }
        self._hover(ev);
      };
      var up = function () { if (self._drag && self._drag.active) { self._drag.active = false; self._applyZoom(); self._drag = null; self._draw(); } };
      this.dom.canvas.addEventListener('mousedown', down);
      this.dom.canvas.addEventListener('mousemove', move);
      window.addEventListener('mouseup', up);
      this.dom.canvas.addEventListener('mouseleave', function () { if (self.dom.tooltip) self.dom.tooltip.style.display = 'none'; });
    }
    if (typeof window !== 'undefined' && window.ResizeObserver && this.dom.mapframe) {
      this._ro = new ResizeObserver(function () { self._resizeDebounced(); }); this._ro.observe(this.dom.mapframe);
    } else if (typeof window !== 'undefined') { window.addEventListener('resize', function () { self._resizeDebounced(); }); }
  };

  AscatViewer.prototype._resizeDebounced = function () {
    var self = this; clearTimeout(this._rt);
    this._rt = setTimeout(function () {
      if (!self._cells.length && !self.passMeta.length) return;
      var w = (self.dom.mapframe && self.dom.mapframe.clientWidth) || 0;
      if (w === self._lastAvailW) return; self._layoutAndDraw();
    }, 140);
  };

  // ---- poll: refresh manifest. A slow cadence (NRT, multi-hour swath gaps); the
  // CycloLab tab pauses it while the tab is hidden (mirrors ReconViewer). ----
  AscatViewer.prototype._schedulePoll = function () { clearTimeout(this._pollTimer); if (this._paused) return; var self = this; this._pollTimer = setTimeout(function () { self._poll(); }, POLL_MS); };
  AscatViewer.prototype._poll = function () {
    if (this._paused) return;
    var self = this;
    this._fetchJson('/manifest.json', true).then(function (m) {
      // only rebuild if the current pass changed (a new pass landed)
      var changed = !self.manifest || (m && m.current_id !== self.manifest.current_id);
      if (changed) { self.loaded = {}; self._onManifest(m); }
    }).catch(function () {}).then(function () { self._schedulePoll(); });
  };
  // Tab-gated polling for the CycloLab mount: pause when the tab is hidden, resume
  // (with an immediate refresh) when it's shown again. No-op on the main /ascat/ page.
  AscatViewer.prototype._pause = function () { this._paused = true; clearTimeout(this._pollTimer); };
  AscatViewer.prototype._resume = function () { if (!this._paused) return; this._paused = false; this._poll(); };

  function uniq(arr) { var seen = {}, out = []; for (var i = 0; i < arr.length; i++) if (arr[i] && !seen[arr[i]]) { seen[arr[i]] = 1; out.push(arr[i]); } return out; }

  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('DOMContentLoaded', function () {
      var r = el('ascat-viewer');
      if (r) r.__ascatView = new AscatViewer(r);
    });
  }
  if (typeof window !== 'undefined') window.AscatViewer = AscatViewer;
  // reusable primitives for the explorer cockpit's native ASCAT fields
  // (re-host, not rebuild): the kt scales/styles and the barb painter —
  // additive-only; _barb touches no instance state.
  AscatViewer.STYLES = STYLES;
  AscatViewer.KT_SCALE = KT_SCALE;
  AscatViewer.KT_SCALE_HC = KT_SCALE_HC;
  AscatViewer.DENSITY = DENSITY;
  AscatViewer.drawBarb = function (g, x, y, kt, dirFrom, color, lw) {
    AscatViewer.prototype._barb.call(null, g, x, y, kt, dirFrom, color, lw);
  };
  AscatViewer.stormMatch = stormMatch;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { AscatViewer: AscatViewer, STYLES: STYLES, KT_SCALE: KT_SCALE,
      stormMatch: stormMatch, nearestFrame: nearestFrame,
      invProjectExt: invProjectExt, rectToBbox: rectToBbox };
  }
})();
