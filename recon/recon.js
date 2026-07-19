/* Aircraft-Recon viewer (/recon/) - V2.
 *
 * A SELF-CONTAINED, copyable, TAT-branded figure of one reconnaissance pass.
 * V2 replaces the V1 "dot soup" map with a clean pro-recon look:
 *   - dark-ocean basemap: ne_10m coastlines (single crisp stroke) + country /
 *     state borders (dimmer) + a faint lat/lon graticule with edge labels,
 *   - a STANDARD wind-barb spatial plot (sub-sampled along the track so barbs
 *     never overlap) colored by peak flight-level wind on a vivid TC kt scale,
 *   - VDM center fixes labeled with MSLP, dropsonde markers,
 *   - an SFMR toggle (flight-level <-> SFMR surface wind), and
 *   - a multi-panel time series (MSLP+FL wind, SFMR+rain, temp+dewpoint,
 *     static pressure+altitude) in clean dark TAT styling.
 * The Current-Mission tab adds Last-10-min / Full-Mission sub-views.
 *
 * Everything is drawn onto ONE <canvas> so a right-click Save / the Download
 * PNG button yields the complete figure.
 *
 * Hydrates entirely from R2 JSON (no server-rendered images). Mirrors the
 * HafsViewer mount shape so the CycloLab per-storm tab can lazy-load this file
 * and do `new ReconViewer(el, { stormLock, base, startTab, els })`. The
 * constructor/options + els/stormLock contract is UNCHANGED. Dependency-free
 * except for window.TATRegions (basemap projection + line drawing).
 *
 * Isolated from the other viewers (own IIFE, recon-* ids).
 */
(function () {
  'use strict';

  var BASE_DEFAULT = 'https://cdn.triple-a-tropics.com/recon';
  var CDN_ROOT = 'https://cdn.triple-a-tropics.com';
  var POLL_MS = 60000;                 // current.json + manifest refresh cadence
  var WATERMARK = '@WeathermanAAA_';
  var FONT = 'Metropolis, "Helvetica Neue", Arial, sans-serif';
  var DISCLOSURE = 'SFMR unreliable in heavy rain / very high wind; obs are point-in-time.';

  // ---- TC kt color scale (hard bins). Anchored to SSHWS at the category
  // thresholds (34 green, 64/83/96 reds, 113 magenta, 137 purple), vivid on
  // dark. Each entry is [minKt, color]; a speed picks the LAST bin whose
  // minKt it meets. Shared by barbs, the legend, and the time-series traces.
  // RED STARTS AT 64 kt (hurricane threshold): cool -> green -> yellow -> orange
  // BELOW 64, a clean RED at 64, then deeper red -> magenta -> purple -> pink for
  // 83/96/113/137. (64 is the canonical TAT red #f5333c, promoted from 83.)
  var KT_SCALE = [
    [0,   '#3563d4'],
    [10,  '#2f93e8'],
    [20,  '#1fc3d6'],
    [30,  '#18c79a'],
    [34,  '#2fbf52'],
    [40,  '#7fd038'],
    [45,  '#c3df3a'],
    [50,  '#ffe534'],
    [55,  '#ffb91f'],
    [60,  '#ff8a1f'],
    [64,  '#f5333c'],
    [83,  '#c81f4a'],
    [96,  '#d61f6a'],
    [113, '#b23bff'],
    [137, '#e6a8ff']
  ];

  // High-contrast variant: brighter / bolder, same bin edges.
  var KT_SCALE_HC = [
    [0,   '#3a6dff'],
    [10,  '#2aa6ff'],
    [20,  '#16d6ec'],
    [30,  '#11e6b0'],
    [34,  '#34e85f'],
    [40,  '#95ef3a'],
    [45,  '#dbff3a'],
    [50,  '#fff23a'],
    [55,  '#ffc91f'],
    [60,  '#ff9a14'],
    [64,  '#ff2f3a'],
    [83,  '#e0143f'],
    [96,  '#ff2a86'],
    [113, '#c45bff'],
    [137, '#f0c2ff']
  ];

  // ---- styles. "muted" is RETIRED; only two remain. A style = a kt ramp +
  // basemap / stroke tones. Persisted in localStorage; a stored "muted" coerces
  // to "sshws".
  var STYLES = {
    sshws: {
      label: 'Classic SSHWS',
      scale: KT_SCALE,
      bg: '#07101c', ocean: '#0a1626', land: '#19314e',
      coast: 'rgba(201,219,242,0.95)', coastLw: 1.0,
      country: 'rgba(150,175,205,0.42)', countryLw: 0.6,
      state: 'rgba(150,175,205,0.16)', stateLw: 0.5,
      grid: 'rgba(176,196,222,0.24)', gridLab: 'rgba(176,196,222,0.5)',
      spine: 'rgba(190,205,225,0.34)', barbLw: 1.25
    },
    highcontrast: {
      label: 'High contrast',
      scale: KT_SCALE_HC,
      bg: '#04080e', ocean: '#07101e', land: '#13243c',
      coast: 'rgba(224,236,250,1.0)', coastLw: 1.15,
      country: 'rgba(180,205,235,0.55)', countryLw: 0.75,
      state: 'rgba(180,205,235,0.20)', stateLw: 0.55,
      grid: 'rgba(214,228,245,0.30)', gridLab: 'rgba(214,228,245,0.62)',
      spine: 'rgba(214,228,245,0.45)', barbLw: 1.5
    }
  };
  var LS_STYLE = 'recon.style';

  // figure palette (chrome). The wind-color ramp comes from the active STYLE.
  var C = { fg: '#e5edf6', muted: '#8ea2bd', border: '#2a3e5c', panel: '#0a1324',
            accent: (typeof window !== 'undefined' && window.TATRegions && window.TATRegions.ACCENT) || '#2b9cff' };

  // discrete legend bands (label + the representative kt used to color the swatch)
  var LEGEND_BANDS = [
    { lab: '<34', kt: 20 }, { lab: '34', kt: 34 }, { lab: '50', kt: 50 },
    { lab: '64 C1', kt: 64 }, { lab: '83 C2', kt: 83 }, { lab: '96 C3', kt: 96 },
    { lab: '113 C4', kt: 113 }, { lab: '137 C5', kt: 137 }
  ];

  var WK = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function el(id) { return document.getElementById(id); }
  function num(v) { return (typeof v === 'number' && isFinite(v)) ? v : null; }
  function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }

  // "2026-06-17T15:04:30Z" -> "Jun 17 1504Z" (compact valid-window label)
  function fmtZ(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso || '';
    return MO[d.getUTCMonth()] + ' ' + d.getUTCDate() + ' ' +
      String(d.getUTCHours()).padStart(2, '0') + String(d.getUTCMinutes()).padStart(2, '0') + 'Z';
  }
  function fmtDTG(iso) {   // full "Mon Jun 17 1504Z"
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso || '';
    return WK[d.getUTCDay()] + ' ' + fmtZ(iso);
  }
  function hhmm(iso) {     // bare "1504Z" tick label
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    return String(d.getUTCHours()).padStart(2, '0') + String(d.getUTCMinutes()).padStart(2, '0') + 'Z';
  }

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

  // The DOM scaffold the viewer wires to. The main /recon/ page ships it in
  // index.html; when the component is embedded WITHOUT it (the CycloLab tab
  // passes only a bare root + no els), the constructor injects this so the
  // by-id lookups below resolve - making ReconViewer truly self-contained.
  var RECON_SCAFFOLD =
    '<div id="recon-tabs" class="recon-tabs" role="tablist">' +
      '<button id="recon-tab-current" class="recon-tab active" type="button" role="tab">Current Mission</button>' +
      '<button id="recon-tab-storms" class="recon-tab" type="button" role="tab">Storms</button>' +
    '</div>' +
    '<div id="recon-view-current">' +
      '<div id="recon-tcpod" class="recon-tcpod"></div>' +
      '<div id="recon-spotlight" class="recon-spotlight"></div>' +
    '</div>' +
    '<div id="recon-view-storms" style="display:none">' +
      '<div class="recon-controls">' +
        '<div id="recon-storm-wrap" class="recon-group"><label for="recon-storm">Storm</label><select id="recon-storm"></select></div>' +
        '<div id="recon-mission-wrap" class="recon-group"><label for="recon-mission">Mission</label><select id="recon-mission"></select></div>' +
        '<div class="recon-group"><label for="recon-wind">Wind</label><select id="recon-wind"><option value="fl">Flight-level</option><option value="sfmr">SFMR surface</option></select></div>' +
        '<div class="recon-group"><label for="recon-scope">View</label><select id="recon-scope"><option value="full">Full mission</option><option value="last10">Last 10 min</option></select></div>' +
        '<div class="recon-group recon-style-wrap"><label for="recon-style">Style</label><select id="recon-style"></select></div>' +
      '</div>' +
      '<div id="recon-mapframe" class="recon-mapframe">' +
        '<canvas id="recon-canvas" width="900" height="640" aria-label="Aircraft reconnaissance flight track and time series figure"></canvas>' +
        '<div id="recon-tooltip"></div>' +
        '<div id="recon-status" class="recon-status"><div class="recon-spinner"></div><span>Loading…</span></div>' +
      '</div>' +
      '<div class="recon-actions">' +
        '<button id="recon-download" class="recon-btn" type="button" title="Download this figure as a PNG">⬇ Download PNG</button>' +
        '<button id="recon-copy" class="recon-btn" type="button" title="Copy this figure to the clipboard">Copy</button>' +
      '</div>' +
      '<div id="recon-stats" class="recon-stats"></div>' +
    '</div>' +
    '<div id="recon-empty" class="recon-empty"><h2>No reconnaissance data right now</h2>' +
      '<p>The Plan of the Day and aircraft missions appear here when the Hurricane Hunters are flying tropical systems. This view refreshes automatically as new missions and Plans of the Day are issued.</p></div>';

  // Compact chrome CSS injected only when self-building (the CycloLab embed);
  // the canvas figure draws itself, so this just dresses the controls/buttons
  // to the dark theme. /recon/ ships its own fuller stylesheet.
  var RECON_EMBED_CSS =
    '#recon-tabs{display:flex;gap:6px;margin-bottom:10px}' +
    '.recon-tab{background:#0e1a30;color:#aebdd4;border:1px solid #1d2c44;border-radius:7px;padding:6px 14px;font:600 13px/1 inherit;cursor:pointer}' +
    '.recon-tab.active{background:#16365e;color:#eaf2ff;border-color:#2b6cb0}' +
    '.recon-controls{display:flex;flex-wrap:wrap;gap:10px 14px;align-items:flex-end;margin-bottom:10px}' +
    '.recon-group{display:flex;flex-direction:column;gap:3px}' +
    '.recon-group label{font:600 10px/1 inherit;letter-spacing:.04em;text-transform:uppercase;color:#8ea2bd}' +
    '.recon-controls select{background:#0e1a30;color:#e5edf6;border:1px solid #1d2c44;border-radius:6px;padding:6px 8px;font:13px/1 inherit;min-width:120px}' +
    '.recon-mapframe{position:relative;width:100%}' +
    '#recon-canvas{display:block;width:100%;height:auto;border-radius:8px}' +
    '.recon-status{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;gap:8px;color:#aebdd4;background:rgba(7,16,28,.6)}' +
    '.recon-spinner{width:16px;height:16px;border:2px solid #2b6cb0;border-top-color:transparent;border-radius:50%;animation:reconspin 1s linear infinite}' +
    '@keyframes reconspin{to{transform:rotate(360deg)}}' +
    '.recon-actions{display:flex;gap:8px;margin:10px 0}' +
    '.recon-btn{background:#0e1a30;color:#cfe0f5;border:1px solid #1d2c44;border-radius:7px;padding:7px 13px;font:600 13px/1 inherit;cursor:pointer}' +
    '.recon-btn:hover{border-color:#2b6cb0}' +
    '.recon-stats{display:flex;flex-wrap:wrap;gap:8px 18px;color:#cdd9ea;font:13px/1.3 inherit}' +
    '.recon-tcpod,.recon-spotlight{margin-bottom:12px;color:#cdd9ea}' +
    '.recon-empty{color:#8ea2bd;padding:18px 4px}.recon-tooltip{display:none}';

  // ========================================================================
  function ReconViewer(root, opts) {
    opts = opts || {};
    this.root = root;
    this.base = (opts.base || BASE_DEFAULT).replace(/\/+$/, '');
    this.stormLock = opts.stormLock || null;
    this.startTab = (opts.startTab === 'storms') ? 'storms' : 'current';

    // Self-scope chrome accent to the shared blue without leaking to the site.
    if (root && root.style) root.style.setProperty('--recon-accent', C.accent);

    // Self-contained: build the scaffold + a compact chrome stylesheet into the
    // page when absent (embedded without index.html's markup/CSS, e.g. the
    // CycloLab tab); no-op on /recon/ which already ships both.
    if (!opts.els && root && !el('recon-canvas')) {
      if (!document.getElementById('recon-embed-css')) {
        var ecss = document.createElement('style');
        ecss.id = 'recon-embed-css';
        ecss.textContent = RECON_EMBED_CSS;
        document.head.appendChild(ecss);
      }
      root.innerHTML = RECON_SCAFFOLD;
    }

    this.dom = (opts.els) || {
      root: root,
      tabCurrent: el('recon-tab-current'),
      tabStorms: el('recon-tab-storms'),
      tabs: el('recon-tabs'),
      tcpod: el('recon-tcpod'),
      spotlight: el('recon-spotlight'),
      stormSel: el('recon-storm'),
      stormWrap: el('recon-storm-wrap'),
      missionSel: el('recon-mission'),
      missionWrap: el('recon-mission-wrap'),
      canvas: el('recon-canvas'),
      mapframe: el('recon-mapframe'),
      status: el('recon-status'),
      empty: el('recon-empty'),
      tooltip: el('recon-tooltip'),
      styleSel: el('recon-style'),
      windSel: el('recon-wind'),       // FL <-> SFMR toggle
      scopeSel: el('recon-scope'),     // Last 10 min <-> Full mission
      download: el('recon-download'),
      copy: el('recon-copy'),
      stats: el('recon-stats'),
      viewCurrent: el('recon-view-current'),
      viewStorms: el('recon-view-storms')
    };

    this.ctx = this.dom.canvas ? this.dom.canvas.getContext('2d') : null;
    this.geo = { coast: null, countries: null, states: null };
    this.manifest = null;
    this.mission = null;        // the mission object currently plotted
    this.storms = [];           // manifest storms (lock-filtered)
    this.curStorm = null;       // selected storm slug (storms tab)
    this.recon = null;          // {slug}/recon.json for curStorm
    this.tab = this.startTab;
    this.layout = null;         // computed geometry
    this._pts = [];             // projected track points (for hover)
    this._fetchSeq = 0;         // guards against out-of-order mission fetches
    this.windMode = 'fl';       // 'fl' (flight-level, default) | 'sfmr'
    this.scope = 'full';        // 'full' | 'last10' (storms tab keeps 'full')
    this._sat = null;           // {img, ext} backdrop when georeferenced (best-effort)

    var s = null; try { s = localStorage.getItem(LS_STYLE); } catch (e) {}
    if (s === 'muted' || s === 'classic' || s === 'contrast') s = null; // retired keys
    this.style = STYLES[s] ? s : 'sshws';

    this._wire();
    this._boot();
  }

  ReconViewer.prototype._S = function () { return STYLES[this.style] || STYLES.sshws; };

  // color for a wind speed (kt) under the active style's kt scale (hard bins)
  ReconViewer.prototype._windColor = function (kt) {
    if (kt == null || isNaN(kt)) return C.muted;
    var scale = this._S().scale, col = scale[0][1];
    for (var i = 0; i < scale.length; i++) if (kt >= scale[i][0]) col = scale[i][1];
    return col;
  };

  ReconViewer.prototype._status = function (msg) {
    var s = this.dom.status;
    if (!s) return;
    if (msg) { s.style.display = 'flex'; var sp = s.querySelector('span'); if (sp) sp.textContent = msg; }
    else { s.style.display = 'none'; }
  };

  // ---- boot ----
  ReconViewer.prototype._boot = function () {
    var self = this;
    this._status('Loading...');
    this._applyTab();
    Promise.all([this._loadBasemap(), this._fetchJson('/manifest.json', true)])
      .then(function (res) { self._onManifest(res[1]); })
      .catch(function (e) {
        console.warn('recon: boot failed', e);
        self._status('');
        self._showEmpty(true);
      });
    if (typeof document !== 'undefined' && document.fonts && document.fonts.ready) {
      document.fonts.ready.then(function () { if (self.mission) self._draw(); });
    }
    this._schedulePoll();
  };

  ReconViewer.prototype._loadBasemap = function () {
    var self = this;
    var p = (window.TATRegions && TATRegions.loadGeo)
      ? TATRegions.loadGeo({ coast: '10m', land: '10m', states: true })
      : Promise.all([
          fetch('/ne_10m_admin_0_countries.geojson').then(function (r) { return r.json(); }),
          fetch('/ne_10m_coastline.geojson').then(function (r) { return r.json(); })
        ]).then(function (g) { return { countries: g[0], coast: g[1], states: null }; });
    return p.then(function (g) { self.geo = g; }).catch(function () { self.geo = { coast: null, countries: null, states: null }; });
  };

  // fetch JSON under the recon base; noStore for live files (manifest / current).
  ReconViewer.prototype._fetchJson = function (path, noStore) {
    var url = this.base + path + (noStore ? ('?t=' + Date.now()) : '');
    return fetch(url, { cache: noStore ? 'no-store' : 'default' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + path); return r.json(); });
  };

  // ---- manifest ----
  ReconViewer.prototype._onManifest = function (m) {
    this.manifest = m;
    var storms = (m && m.storms) || [];
    if (this.stormLock) {
      var locked = this._resolveLock(storms, this.stormLock);
      storms = locked ? [locked] : [];
    }
    this.storms = storms;
    if (!storms.length && !(m && m.tcpod_number)) {
      this._status('');
      this._showEmpty(true);
      return;
    }
    this._showEmpty(false);
    this._status('');

    if (this.stormLock && this.dom.stormWrap) this.dom.stormWrap.style.display = 'none';
    this._buildStormSelect();
    this._loadCurrentView();

    var startSlug = this.stormLock ? (storms[0] && storms[0].slug)
      : (m.current_slug || (storms[0] && storms[0].slug));
    if (startSlug) this._selectStorm(startSlug);

    if (this.stormLock) this.tab = 'storms';
    this._applyTab();
  };

  // Resolve stormLock by manifest slug OR atcf OR case-insensitive name.
  ReconViewer.prototype._resolveLock = function (storms, lock) {
    var L = String(lock).toLowerCase();
    for (var i = 0; i < storms.length; i++) {
      var s = storms[i];
      if (String(s.slug).toLowerCase() === L) return s;
      if (s.atcf && String(s.atcf).toLowerCase() === L) return s;
      if (s.name && String(s.name).toLowerCase() === L) return s;
    }
    return null;
  };

  ReconViewer.prototype._showEmpty = function (on) {
    if (this.dom.empty) this.dom.empty.style.display = on ? 'block' : 'none';
    if (this.dom.tabs) this.dom.tabs.style.display = on ? 'none' : '';
    if (this.dom.viewCurrent && this.tab === 'current') this.dom.viewCurrent.style.display = on ? 'none' : '';
    if (this.dom.viewStorms && this.tab === 'storms') this.dom.viewStorms.style.display = on ? 'none' : '';
  };

  // ---- tabs ----
  ReconViewer.prototype._applyTab = function () {
    var cur = this.tab === 'current';
    if (this.dom.tabs) this.dom.tabs.style.display = this.stormLock ? 'none' : '';
    if (this.dom.viewCurrent) this.dom.viewCurrent.style.display = (cur && !this.stormLock) ? '' : 'none';
    if (this.dom.viewStorms) this.dom.viewStorms.style.display = (!cur || this.stormLock) ? '' : 'none';
    if (this.dom.tabCurrent) this.dom.tabCurrent.classList.toggle('active', cur);
    if (this.dom.tabStorms) this.dom.tabStorms.classList.toggle('active', !cur);
    // the scope control (Last 10 / Full) is meaningful only on the Storms tab
    // when looking at the live/most-recent mission; keep it visible there.
    if (this.dom.scopeSel && this.dom.scopeSel.parentNode) {
      this.dom.scopeSel.parentNode.style.display = '';
    }
    if ((!cur || this.stormLock) && this.mission) { this._layoutAndDraw(); }
  };

  ReconViewer.prototype._setTab = function (t) {
    this.tab = (t === 'storms') ? 'storms' : 'current';
    this._applyTab();
    // catch up on whatever this pane missed while hidden
    if (this.tab === 'current' && !this.stormLock && this.manifest &&
        this.manifest.generated_utc !== this._curViewStamp) {
      this._loadCurrentView();
    }
    if (this._stormsVisible() && this._stormsStale) this._refreshRecon();
  };

  // ====================================================================
  // CURRENT MISSION tab: Plan of the Day (lead) + active/recent spotlight.
  // ====================================================================
  ReconViewer.prototype._loadCurrentView = function () {
    var self = this;
    // watermark of the manifest this pane was last refreshed against — lets a
    // tab switch know whether the pane missed updates while hidden.
    this._curViewStamp = this.manifest && this.manifest.generated_utc;
    this._fetchJson('/tcpod.json', true)
      .then(function (t) {
        // content-gated: a heartbeat republish (fresh stamps, same data)
        // must not rebuild the pane DOM under the reader.
        var key = t ? (t.tcpod_number || '') + '|' + (t.raw || '') : 'none';
        if (key === self._tcpodKey) return;
        self._tcpodKey = key;
        self._renderTcpod(t);
      })
      .catch(function (e) { console.warn('recon: tcpod load failed', e); self._renderTcpod(null); });
    this._fetchJson('/current.json', true)
      .then(function (c) {
        var mi = (c && c.mission) || {};
        var key = c ? [c.storm_slug, c.has_active, mi.mission_id, mi.valid_end,
                       mi.n_obs, (mi.vdm_centers || []).length,
                       (mi.sondes || []).length].join('|') : 'none';
        if (key === self._spotKey) return;
        self._spotKey = key;
        self._renderSpotlight(c);
      })
      .catch(function (e) { console.warn('recon: current load failed', e); self._renderSpotlight(null); });
  };

  ReconViewer.prototype._renderTcpod = function (t) {
    var host = this.dom.tcpod;
    if (!host) return;
    host.innerHTML = '';
    if (!t) {
      host.appendChild(this._note('Plan of the Day is unavailable right now.'));
      return;
    }
    var head = document.createElement('div');
    head.className = 'recon-tcpod-head';
    var title = document.createElement('div');
    title.className = 'recon-tcpod-title';
    title.textContent = 'Tropical Cyclone Plan of the Day' +
      (t.tcpod_number ? '  ·  No. ' + t.tcpod_number : '') +
      (t.amendment ? '  (amended)' : '');
    head.appendChild(title);
    var win = document.createElement('div');
    win.className = 'recon-tcpod-window';
    var vf = t.valid_from_utc ? fmtDTG(t.valid_from_utc) : (t.valid_from || '');
    var vt = t.valid_to_utc ? fmtDTG(t.valid_to_utc) : (t.valid_to || '');
    win.textContent = (vf || vt) ? ('Valid ' + vf + '  to  ' + vt) : '';
    head.appendChild(win);
    if (t.issued_local) {
      var iss = document.createElement('div');
      iss.className = 'recon-tcpod-issued';
      iss.textContent = 'Issued ' + t.issued_local;
      head.appendChild(iss);
    }
    host.appendChild(head);

    var basins = t.basins || {};
    var order = [['atlantic', 'Atlantic'], ['pacific', 'Pacific']];
    for (var i = 0; i < order.length; i++) {
      var key = order[i][0], lab = order[i][1], b = basins[key];
      if (!b) continue;
      host.appendChild(this._renderBasin(lab, b));
    }
  };

  ReconViewer.prototype._renderBasin = function (label, b) {
    var wrap = document.createElement('div');
    wrap.className = 'recon-basin';
    var h = document.createElement('div');
    h.className = 'recon-basin-h';
    h.textContent = label + ' requirements';
    wrap.appendChild(h);

    var missions = (b.missions || []).filter(function (m) { return m; });
    if (b.negative || !missions.length) {
      wrap.appendChild(this._note('No active reconnaissance tasked.'));
    } else {
      for (var i = 0; i < missions.length; i++) {
        wrap.appendChild(this._renderTaskCard(missions[i]));
      }
    }
    var outlook = (b.outlook || []).filter(function (s) { return s && String(s).trim(); });
    var ol = document.createElement('div');
    ol.className = 'recon-outlook';
    if (outlook.length) {
      ol.innerHTML = '<span class="recon-outlook-lab">Outlook for succeeding day:</span> ' +
        outlook.map(function (s) { return self_escape(s); }).join('  ');
    } else {
      ol.innerHTML = '<span class="recon-outlook-lab">Outlook for succeeding day:</span> Negative.';
    }
    wrap.appendChild(ol);
    return wrap;
  };

  ReconViewer.prototype._renderTaskCard = function (m) {
    var card = document.createElement('div');
    card.className = 'recon-task';
    var ttl = document.createElement('div');
    ttl.className = 'recon-task-title';
    ttl.textContent = m.title || m.mission_type || 'Tasked mission';
    if (m.status) {
      var st = document.createElement('span');
      st.className = 'recon-task-status';
      st.textContent = m.status;
      ttl.appendChild(st);
    }
    card.appendChild(ttl);

    var rows = [];
    if (m.aircraft) rows.push(['Aircraft', m.aircraft]);
    if (m.takeoff) rows.push(['Takeoff', m.takeoff]);
    if (m.fix_time) rows.push(['Fix time', m.fix_time]);
    if (m.fix_window) rows.push(['Fix window', m.fix_window]);
    if (m.target && (m.target.lat != null && m.target.lon != null)) {
      rows.push(['Target', fmtLatLon(m.target.lat, m.target.lon)]);
    } else if (m.target && m.target.raw) {
      rows.push(['Target', m.target.raw]);
    }
    if (m.altitude) rows.push(['Altitude', m.altitude]);
    if (m.mission_type) rows.push(['Type', m.mission_type]);

    var grid = document.createElement('div');
    grid.className = 'recon-task-grid';
    for (var i = 0; i < rows.length; i++) {
      var k = document.createElement('span'); k.className = 'recon-k'; k.textContent = rows[i][0];
      var v = document.createElement('span'); v.className = 'recon-v'; v.textContent = rows[i][1];
      grid.appendChild(k); grid.appendChild(v);
    }
    card.appendChild(grid);
    if (m.remarks) {
      var rem = document.createElement('div');
      rem.className = 'recon-task-remarks';
      rem.textContent = m.remarks;
      card.appendChild(rem);
    }
    return card;
  };

  ReconViewer.prototype._renderSpotlight = function (c) {
    var host = this.dom.spotlight;
    if (!host) return;
    host.innerHTML = '';
    var mission = c && c.mission;
    var h = document.createElement('div');
    h.className = 'recon-spot-h';
    h.textContent = (c && c.has_active) ? 'Aircraft on station now' : 'Most recent mission';
    host.appendChild(h);
    if (!mission) {
      host.appendChild(this._note('No recent reconnaissance mission to show.'));
      return;
    }
    var line = document.createElement('div');
    line.className = 'recon-spot-line';
    var bits = [];
    bits.push((mission.name || mission.storm_name || mission.slug || 'Mission'));
    if (mission.mission_id) bits.push(mission.mission_id);
    if (mission.aircraft) bits.push(mission.aircraft);
    line.textContent = bits.join('  ·  ');
    host.appendChild(line);
    if (mission.valid_start) {
      var w = document.createElement('div');
      w.className = 'recon-spot-window';
      w.textContent = 'Valid ' + fmtZ(mission.valid_start) +
        (mission.valid_end ? ('  to  ' + fmtZ(mission.valid_end)) : '');
      host.appendChild(w);
    }
    host.appendChild(this._statRow(mission));
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'recon-btn recon-spot-plot';
    btn.textContent = 'Plot this mission';
    var self = this;
    btn.addEventListener('click', function () {
      // a live spotlight defaults to the Last-10-min view; everything else full.
      self.scope = (c && c.has_active) ? 'last10' : 'full';
      if (self.dom.scopeSel) self.dom.scopeSel.value = self.scope;
      self._setMission(mission);
      if (mission.slug && self.dom.stormSel) {
        self.curStorm = mission.slug;
        if (self._hasStorm(mission.slug)) {
          self.dom.stormSel.value = mission.slug;
          self._loadRecon(mission.slug, mission.mission_id);
        }
      }
      self._setTab('storms');
    });
    host.appendChild(btn);
  };

  ReconViewer.prototype._statRow = function (m) {
    var row = document.createElement('div');
    row.className = 'recon-spot-stats';
    var stats = [
      ['Obs', m.n_obs != null ? String(m.n_obs) : '-'],
      ['Peak SFMR', m.peak_sfmr_kt != null ? Math.round(m.peak_sfmr_kt) + ' kt' : 'n/a'],
      ['Peak FL', m.peak_fl_wind_kt != null ? Math.round(m.peak_fl_wind_kt) + ' kt' : 'n/a'],
      ['Min Psfc', m.min_p_sfc_hpa != null ? Math.round(m.min_p_sfc_hpa) + ' hPa' : 'n/a']
    ];
    for (var i = 0; i < stats.length; i++) {
      var c = document.createElement('div'); c.className = 'recon-stat';
      var k = document.createElement('span'); k.className = 'recon-stat-k'; k.textContent = stats[i][0];
      var v = document.createElement('span'); v.className = 'recon-stat-v'; v.textContent = stats[i][1];
      c.appendChild(v); c.appendChild(k); row.appendChild(c);
    }
    return row;
  };

  ReconViewer.prototype._note = function (txt) {
    var d = document.createElement('div');
    d.className = 'recon-note';
    d.textContent = txt;
    return d;
  };

  // ====================================================================
  // STORMS tab: storm selector -> mission list -> plot the picked mission.
  // ====================================================================
  ReconViewer.prototype._hasStorm = function (slug) {
    for (var i = 0; i < this.storms.length; i++) if (this.storms[i].slug === slug) return true;
    return false;
  };

  ReconViewer.prototype._buildStormSelect = function () {
    var sel = this.dom.stormSel;
    if (!sel) return;
    sel.innerHTML = '';
    var cur = this.manifest && this.manifest.current_slug;
    var ordered = this.storms.slice().sort(function (a, b) {
      if (a.slug === cur) return -1;
      if (b.slug === cur) return 1;
      return 0;
    });
    for (var i = 0; i < ordered.length; i++) {
      var s = ordered[i];
      var o = document.createElement('option');
      o.value = s.slug;
      var tag = s.is_invest ? ' (invest)' : '';
      o.textContent = (s.name || s.slug) + '  ·  ' + (s.basin || '') + ' ' + (s.year || '') + tag;
      sel.appendChild(o);
    }
    if (this.dom.stormWrap) this.dom.stormWrap.style.display = this.stormLock ? 'none' : '';
  };

  ReconViewer.prototype._stormBySlug = function (slug) {
    for (var i = 0; i < this.storms.length; i++) if (this.storms[i].slug === slug) return this.storms[i];
    return null;
  };

  ReconViewer.prototype._selectStorm = function (slug) {
    var s = this._stormBySlug(slug);
    if (!s) return;
    this.curStorm = slug;
    if (this.dom.stormSel) this.dom.stormSel.value = slug;
    this._loadRecon(slug, s.latest_mission_id || null);
  };

  ReconViewer.prototype._loadRecon = function (slug, preferMissionId) {
    var self = this;
    this._status('Loading missions...');
    this._fetchJson('/' + slug + '/recon.json', false)
      .then(function (r) {
        if (self.curStorm !== slug) return;
        self.recon = r;
        self._buildMissionSelect(r, preferMissionId);
      })
      .catch(function (e) {
        console.warn('recon: recon.json load failed', e);
        if (self.curStorm === slug) { self._status(''); }
      });
  };

  // (re)fill the mission dropdown options; selection is the caller's job.
  // No-op when the option set is unchanged (a live tick must not close an
  // open dropdown for nothing).
  ReconViewer.prototype._fillMissionSelect = function (missions) {
    var sel = this.dom.missionSel;
    if (!sel) return;
    var sig = missions.map(function (m) {
      return m.mission_id + '|' + (m.valid_start || '') + '|' +
             (m.peak_sfmr_kt != null ? m.peak_sfmr_kt : '');
    }).join('');
    if (sig === this._missionSelSig) return;
    this._missionSelSig = sig;
    sel.innerHTML = '';
    var ordered = missions.slice().sort(function (a, b) {
      return (Date.parse(b.valid_start || '') || 0) - (Date.parse(a.valid_start || '') || 0);
    });
    for (var i = 0; i < ordered.length; i++) {
      var m = ordered[i];
      var o = document.createElement('option');
      o.value = m.mission_id;
      o.textContent = (m.aircraft || '') + ' ' + (m.flight || '') + '  ·  ' + fmtZ(m.valid_start) +
        (m.peak_sfmr_kt != null ? ('  ·  ' + Math.round(m.peak_sfmr_kt) + ' kt SFMR') : '');
      sel.appendChild(o);
    }
    if (this.dom.missionWrap) this.dom.missionWrap.style.display = missions.length ? '' : 'none';
  };

  ReconViewer.prototype._buildMissionSelect = function (recon, preferMissionId) {
    var missions = (recon && recon.missions) || [];
    this._fillMissionSelect(missions);
    if (!missions.length) { this._status('No missions for this storm.'); return; }
    var pick = null;
    if (preferMissionId) pick = this._missionMetaById(missions, preferMissionId);
    if (!pick) pick = this._missionMetaById(missions, recon && recon.latest_mission_id);
    if (!pick) {
      pick = missions.slice().sort(function (a, b) {
        return (Date.parse(b.valid_start || '') || 0) - (Date.parse(a.valid_start || '') || 0);
      })[0];
    }
    if (this.dom.missionSel && pick) this.dom.missionSel.value = pick.mission_id;
    this._loadMissionFile(pick);
  };

  ReconViewer.prototype._missionMetaById = function (missions, id) {
    if (!id) return null;
    for (var i = 0; i < missions.length; i++) if (missions[i].mission_id === id) return missions[i];
    return null;
  };

  // newest mission (by valid_start, matching the dropdown ordering) — the
  // "latest" the live refresh follows when the user is already on it.
  ReconViewer.prototype._latestMissionId = function (missions) {
    var best = null, bt = -1;
    for (var i = 0; i < missions.length; i++) {
      var t = Date.parse(missions[i].valid_start || '') || 0;
      if (t > bt) { bt = t; best = missions[i].mission_id; }
    }
    return best;
  };

  ReconViewer.prototype._loadMissionFile = function (meta) {
    var self = this, slug = this.curStorm, seq = ++this._fetchSeq;
    if (!meta || !slug) return;
    this._status('Loading mission...');
    this._fetchJson('/' + slug + '/' + meta.file, false)
      .then(function (d) {
        if (seq !== self._fetchSeq) return;
        self._status('');
        self._setMission(d);
      })
      .catch(function (e) {
        console.warn('recon: mission file load failed', e);
        if (seq === self._fetchSeq) self._status('Could not load mission.');
      });
  };

  ReconViewer.prototype._setMission = function (m) {
    this.mission = m;
    this._sat = null;
    this._status('');
    this._layoutAndDraw();
    this._renderStats(m);
    // Best-effort satellite backdrop for a LIVE current mission only. Never
    // blocks the view: it redraws on success and is a no-op on any failure.
    this._maybeLoadSatBackdrop(m);
  };

  ReconViewer.prototype._renderStats = function (m) {
    var host = this.dom.stats;
    if (!host || !m) return;
    host.innerHTML = '';
    var stats = [
      ['Mission', m.mission_id || '-'],
      ['Aircraft', m.aircraft || '-'],
      ['Obs', m.n_obs != null ? String(m.n_obs) : '-'],
      ['Peak SFMR', m.peak_sfmr_kt != null ? Math.round(m.peak_sfmr_kt) + ' kt' : 'n/a'],
      ['Peak FL wind', m.peak_fl_wind_kt != null ? Math.round(m.peak_fl_wind_kt) + ' kt' : 'n/a'],
      ['Min Psfc', m.min_p_sfc_hpa != null ? Math.round(m.min_p_sfc_hpa) + ' hPa' : 'n/a']
    ];
    for (var i = 0; i < stats.length; i++) {
      var c = document.createElement('div'); c.className = 'recon-chip';
      var v = document.createElement('span'); v.className = 'recon-chip-v'; v.textContent = stats[i][1];
      var k = document.createElement('span'); k.className = 'recon-chip-k'; k.textContent = stats[i][0];
      c.appendChild(v); c.appendChild(k); host.appendChild(c);
    }
  };

  // ====================================================================
  // Satellite backdrop (best-effort, current LIVE mission only).
  //
  // The floater pipeline stamps IR frames with a chrome-free grayscale
  // backdrop sibling (`bd_key`) georeferenced by WGS84 `bounds` [W,S,E,N]
  // (the same contract ascat.js consumes). Only that sibling is painted -
  // the CHROMED floater frame is never used: its true extent is the square
  // floater box, not the backdrop bbox, and its burned-in chrome does not
  // belong under barbs. Frames older than 3 h are skipped (a live mission
  // must never fly over silently day-old imagery during a satellite outage
  // or producer stall) - the clean coastline basemap is the honest fallback.
  // ====================================================================
  // ``keep``: in-place live refresh — leave the existing backdrop up until
  // (unless) a fresh frame replaces it, so a tick never flashes it away.
  ReconViewer.prototype._maybeLoadSatBackdrop = function (m, keep) {
    var self = this;
    if (!keep) this._sat = null;
    // only for a live mission whose storm is currently floating
    var manifest = this.manifest;
    if (!m || !manifest || !manifest.has_active_recon) return;
    if (typeof Image === 'undefined' || typeof fetch === 'undefined') return;
    var slug = m.slug || this.curStorm;
    if (!slug) return;
    fetch(CDN_ROOT + '/floaters/manifest.json', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (fm) {
        if (!fm || !fm.storms) return null;
        // match the recon storm to a floater entry by name (case-insensitive)
        var want = String(m.name || m.storm_name || '').toLowerCase();
        var entry = null;
        for (var i = 0; i < fm.storms.length; i++) {
          var nm = String(fm.storms[i].name || '').toLowerCase();
          if (want && nm && (nm === want || nm.indexOf(want) >= 0 || want.indexOf(nm) >= 0)) { entry = fm.storms[i]; break; }
        }
        if (!entry || !entry.manifest) return null;
        return fetch(CDN_ROOT + '/' + entry.manifest, { cache: 'no-store' })
          .then(function (r) { return r.ok ? r.json() : null; });
      })
      .then(function (sm) {
        if (!sm || !sm.bands) return;
        var band = sm.bands.ir || sm.bands.truecolor || null;
        if (!band) return;
        var frames = band.frames || [];
        // newest frame carrying the chrome-free backdrop sibling
        var best = null;
        for (var i = frames.length - 1; i >= 0; i--) {
          if (frames[i] && frames[i].bd_key && frames[i].bounds) { best = frames[i]; break; }
        }
        if (!best) return;
        // age gate: stale imagery under a live track misleads; basemap instead
        var age = Date.now() - (Date.parse(best.t) || 0);
        if (!isFinite(age) || age > 3 * 3600e3) return;
        var bounds = self._frameBounds(best);
        if (!bounds) return;
        var img = new Image();
        try { img.crossOrigin = 'anonymous'; } catch (e) {}
        img.onload = function () {
          self._sat = { img: img, ext: bounds };
          if (self.mission === m) self._draw();
        };
        img.onerror = function () {};
        img.src = CDN_ROOT + '/' + best.bd_key;
      })
      .catch(function () {});
  };

  // Producer bounds are WGS84 [W,S,E,N] (the floater backdrop-sibling
  // contract, same as ascat.js). The recon draw path wants [w,e,s,n].
  ReconViewer.prototype._frameBounds = function (frame) {
    var b = frame && (frame.bounds || frame.ext || frame.extent);
    if (Array.isArray(b) && b.length === 4 && b.every(function (v) { return typeof v === 'number'; })) {
      return [b[0], b[2], b[1], b[3]];   // [W,S,E,N] -> [w,e,s,n]
    }
    if (b && typeof b === 'object' && b.w != null && b.e != null && b.s != null && b.n != null) {
      return [b.w, b.e, b.s, b.n];
    }
    return null;
  };

  // ====================================================================
  // FIGURE: header + barb map + multi-panel time series, all on one canvas.
  // ====================================================================

  // Drop obs whose timestamp year is implausible (a corrupt HDOB decode can stamp
  // future years like 2095 -- see the decoder sanity guard). A handful of bad
  // points would otherwise blow up the time-series x-domain (squashing the real
  // data to the edge) and scatter stray barbs. Never returns empty: if EVERY ob
  // is implausible (whole-mission decode failure) the raw track is kept so the
  // map still shows something.
  ReconViewer.prototype._saneTrack = function (track) {
    var yMax = (new Date()).getUTCFullYear() + 1, out = [];
    for (var i = 0; i < track.length; i++) {
      var ts = Date.parse(track[i].t || '');
      if (isNaN(ts)) { out.push(track[i]); continue; }   // undated: time-series skips it
      var y = (new Date(ts)).getUTCFullYear();
      if (y >= 2006 && y <= yMax) out.push(track[i]);
    }
    return out.length ? out : track;
  };

  // Sane [start,end] ISO from the sane full track (ignores corrupt-year obs), for
  // the header valid window. Nulls if the track has no usable times.
  ReconViewer.prototype._saneTimeBounds = function () {
    var tr = this._saneTrack((this.mission && this.mission.track) || []);
    var lo = null, hi = null;
    for (var i = 0; i < tr.length; i++) {
      var ts = Date.parse(tr[i].t || '');
      if (isNaN(ts)) continue;
      if (lo == null || ts < lo) lo = ts;
      if (hi == null || ts > hi) hi = ts;
    }
    return { start: lo != null ? new Date(lo).toISOString() : null,
             end: hi != null ? new Date(hi).toISOString() : null };
  };

  // Which track points belong to the current scope (full mission vs last 10 min).
  ReconViewer.prototype._scopedTrack = function () {
    var m = this.mission;
    var track = this._saneTrack((m && m.track) || []);
    if (this.scope !== 'last10' || !track.length) return track;
    var maxT = null;
    for (var i = 0; i < track.length; i++) {
      var ts = Date.parse(track[i].t || '');
      if (!isNaN(ts) && (maxT == null || ts > maxT)) maxT = ts;
    }
    if (maxT == null) return track;
    var cut = maxT - 10 * 60000;
    var out = [];
    for (var j = 0; j < track.length; j++) {
      var t2 = Date.parse(track[j].t || '');
      if (!isNaN(t2) && t2 >= cut) out.push(track[j]);
    }
    return out.length ? out : track;
  };

  // track bbox (lon/lat) padded, then the extent for the map rect.
  ReconViewer.prototype._trackExtent = function (track) {
    var mnx = Infinity, mxx = -Infinity, mny = Infinity, mxy = -Infinity, n = 0;
    for (var i = 0; i < track.length; i++) {
      var la = num(track[i].lat), lo = num(track[i].lon);
      if (la == null || lo == null) continue;
      n++;
      if (lo < mnx) mnx = lo; if (lo > mxx) mxx = lo;
      if (la < mny) mny = la; if (la > mxy) mxy = la;
    }
    if (!n) return [-100, -60, 10, 40];
    var lonSpan = Math.max(0.6, mxx - mnx), latSpan = Math.max(0.6, mxy - mny);
    var padL = lonSpan * 0.18 + 0.4, padT = latSpan * 0.18 + 0.4;
    return [mnx - padL, mxx + padL, mny - padT, mxy + padT];
  };

  // Aspect-correct an extent for a W x H px rect. TATRegions.project maps lon/lat
  // LINEARLY (no cos-lat), so a raw bbox squashes the map (1 deg lon !== 1 deg lat
  // on the ground). Expand the extent (never crop) so on-screen degrees are
  // proportional: px/lon-deg == cos(midLat) * px/lat-deg. Florida looks correct.
  ReconViewer.prototype._aspectExtent = function (ext, W, H) {
    var w = ext[0], e = ext[1], s = ext[2], n = ext[3];
    var midLat = (s + n) / 2;
    var cosl = Math.max(0.12, Math.cos(midLat * Math.PI / 180));
    var lonSpan = e - w, latSpan = n - s;
    var target = (W / H) / cosl;          // desired lonSpan / latSpan
    var cur = lonSpan / latSpan;
    if (cur < target) {                   // too tall -> widen longitude
      var nl = latSpan * target, cx = (w + e) / 2;
      w = cx - nl / 2; e = cx + nl / 2;
    } else {                              // too wide -> heighten latitude
      var nh = lonSpan / target, cy = (s + n) / 2;
      s = cy - nh / 2; n = cy + nh / 2;
    }
    return [w, e, s, n];
  };

  ReconViewer.prototype._layoutAndDraw = function () {
    this._layout();
    this._draw();
  };

  ReconViewer.prototype._layout = function () {
    var cv = this.dom.canvas;
    if (!cv) return;
    var availW = (this.dom.mapframe && this.dom.mapframe.clientWidth) || 900;
    availW = Math.max(360, availW);
    this._lastAvailW = availW;
    var figW = Math.max(availW, 760);     // legible PNG floor; scales down on mobile
    var pad = 16, headerH = 58, gap = 14;
    var mapH = Math.round(figW * 0.5);
    // stacked time-series panels (shared x). Sized off the figure width. Only the
    // panels that actually carry data are shown (empty ones are hidden), so the
    // figure height collapses to the surviving panel count.
    var panelH = Math.round(figW * 0.16);
    var nPanels = Math.max(1, this._activePanels().length);
    var tsGap = 8;
    var tsH = panelH * nPanels + tsGap * (nPanels - 1);
    var footerH = 24;
    var figH = pad + headerH + mapH + gap + tsH + gap + footerH + pad;

    var dpr = Math.min((typeof window !== 'undefined' && window.devicePixelRatio) || 1, 2);
    this.dpr = dpr; this.figW = figW; this.figH = figH;
    cv.width = Math.round(figW * dpr);
    cv.height = Math.round(figH * dpr);
    cv.style.width = availW + 'px';
    cv.style.height = (availW * figH / figW) + 'px';

    var tsY = pad + headerH + mapH + gap;
    this.layout = {
      pad: pad,
      header: { x: pad, y: pad, w: figW - 2 * pad, h: headerH },
      map: { x: pad, y: pad + headerH, w: figW - 2 * pad, h: mapH },
      ts: { x: pad, y: tsY, w: figW - 2 * pad, h: tsH, panelH: panelH, gap: tsGap, n: nPanels },
      footerY: tsY + tsH + gap + footerH - 8
    };
  };

  ReconViewer.prototype._scale = function (g) { g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0); };

  ReconViewer.prototype._draw = function () {
    var g = this.ctx, L = this.layout, m = this.mission;
    if (!g || !L) return;
    var S = this._S();
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, this.dom.canvas.width, this.dom.canvas.height);
    this._scale(g);
    g.fillStyle = S.bg; g.fillRect(0, 0, this.figW, this.figH);

    if (!m) {
      g.fillStyle = C.muted; g.font = '600 14px ' + FONT; g.textAlign = 'center'; g.textBaseline = 'middle';
      g.fillText('Pick a storm and mission to plot a pass.', this.figW / 2, this.figH / 2);
      return;
    }
    this._drawHeader(g);
    this._drawMap(g);
    this._drawTimeSeries(g);
    this._drawFooter(g);
  };

  ReconViewer.prototype._drawHeader = function (g) {
    var h = this.layout.header, m = this.mission;
    g.save();
    g.textAlign = 'left'; g.textBaseline = 'alphabetic';
    g.fillStyle = C.fg; g.font = '800 19px ' + FONT;
    var name = m.name || m.storm_name || '';
    var scopeTag = (this.scope === 'last10') ? '  ·  Last 10 min' : '';
    var title = (name ? (name + '  ·  ') : '') + 'Aircraft Recon' + scopeTag;
    g.fillText(title, h.x, h.y + 18);
    g.fillStyle = C.muted; g.font = '600 12.5px ' + FONT;
    // valid window from the SANE track (the mission-level valid_start/valid_end
    // can carry a corrupt-decode year like 2095 -> "to Jan 20"); fall back to the
    // mission metadata only when the track has no usable times.
    var tb = this._saneTimeBounds();
    var vs = tb.start || m.valid_start, ve = tb.end || m.valid_end;
    var sub = (m.mission_id || '') +
      (m.aircraft ? ('  ·  ' + m.aircraft) : '') +
      (vs ? ('  ·  ' + fmtZ(vs) + ' to ' + (ve ? fmtZ(ve) : '?')) : '');
    g.fillText(sub, h.x, h.y + 38);
    // wind-mode chip (right side of the header)
    var chip = (this.windMode === 'sfmr') ? 'SFMR surface wind' : 'Flight-level wind';
    g.font = '700 11px ' + FONT; g.textAlign = 'right';
    var cw = g.measureText(chip).width + 16, cx = h.x + h.w - cw, cy = h.y + 6, ch = 18;
    roundRectPath(g, cx, cy, cw, ch, 4);
    g.fillStyle = 'rgba(43,156,255,0.14)'; g.fill();
    g.strokeStyle = 'rgba(43,156,255,0.5)'; g.lineWidth = 1; g.stroke();
    g.fillStyle = '#bcdcff'; g.textBaseline = 'middle';
    g.fillText(chip, h.x + h.w - 8, cy + ch / 2 + 0.5);
    g.restore();
  };

  // value used for the barb's wind SPEED (length encoding): the active wind mode.
  ReconViewer.prototype._barbSpeed = function (ob) {
    return (this.windMode === 'sfmr') ? num(ob.sfmr) : num(ob.wspd);
  };
  // value used for the barb's COLOR: peak FL wind in FL mode; SFMR in SFMR mode.
  ReconViewer.prototype._barbColorKt = function (ob) {
    if (this.windMode === 'sfmr') return num(ob.sfmr);
    var pk = num(ob.pkwnd);
    return (pk != null) ? pk : num(ob.wspd);
  };

  ReconViewer.prototype._drawMap = function (g) {
    var L = this.layout.map, m = this.mission, S = this._S();
    var track = this._scopedTrack();
    var ext = this._aspectExtent(this._trackExtent(track), L.w, L.h);
    this._ext = ext;
    var proj = (window.TATRegions && TATRegions.project)
      ? function (lo, la) { return TATRegions.project(lo, la, ext, L.w, L.h); }
      : function (lo, la) {
          return [(lo - ext[0]) / (ext[1] - ext[0]) * L.w, (ext[3] - la) / (ext[3] - ext[2]) * L.h];
        };

    g.save();
    g.beginPath(); g.rect(L.x, L.y, L.w, L.h); g.clip();
    g.translate(L.x, L.y);
    g.lineJoin = 'round'; g.lineCap = 'round';

    // ---- 1) dark ocean ground
    g.fillStyle = S.ocean; g.fillRect(0, 0, L.w, L.h);

    // ---- 2) optional satellite backdrop (best-effort; only if georeferenced)
    if (this._sat && this._sat.img && this._sat.ext) {
      this._drawSatBackdrop(g, proj, L.w, L.h);
    } else {
      // OPAQUE land fill over the opaque ocean. drawBasemapFill clearRects the
      // rect then fills its `ocean` color, so it MUST be the opaque ocean (a
      // transparent ocean here erased the rect and let the page show through).
      if (window.TATRegions && TATRegions.drawBasemapFill && this.geo && this.geo.countries) {
        TATRegions.drawBasemapFill(g, ext, { countries: this.geo.countries }, L.w, L.h,
          { ocean: S.ocean, land: S.land });
      }
    }

    // ---- 3) faint graticule (drawn UNDER the coastlines)
    this._drawGraticule(g, ext, L.w, L.h);

    // ---- 4) basemap lines: a SINGLE crisp coastline stroke. We deliberately do
    //         NOT stroke admin_0 country borders here -- that polygon set retraces
    //         the same coast as the coastline layer and the two together produced
    //         the doubled / fuzzy translucent-blue coast outline. Faint inland
    //         state/province borders give orientation without re-tracing the coast
    //         enough to read as a double at their low alpha.
    if (window.TATRegions && TATRegions.drawBasemapLines) {
      TATRegions.drawBasemapLines(g, ext, this.geo, L.w, L.h, {
        coast: S.coast, coastLw: S.coastLw,
        state: S.state, stateLw: S.stateLw
      });
    }

    // ---- 5) faint centerline spine connecting the track for context
    var pts = [];
    for (var i = 0; i < track.length; i++) {
      var la = num(track[i].lat), lo = num(track[i].lon);
      if (la == null || lo == null) { pts.push(null); continue; }
      var p = proj(lo, la);
      pts.push({ x: p[0], y: p[1], ob: track[i] });
    }
    g.strokeStyle = S.spine; g.lineWidth = 1.0;
    g.beginPath();
    var started = false;
    for (i = 0; i < pts.length; i++) {
      if (!pts[i]) { started = false; continue; }
      if (!started) { g.moveTo(pts[i].x, pts[i].y); started = true; } else g.lineTo(pts[i].x, pts[i].y);
    }
    g.stroke();

    // cache projected pts for hover (map-local coords)
    this._pts = pts;

    // ---- 6) wind barbs, dense along-track sampling (~16 px spacing, TT-style)
    this._drawBarbs(g, pts, S);

    // ---- 7) dropsondes: small diamond
    var sondes = m.sondes || [];
    for (i = 0; i < sondes.length; i++) {
      var sla = num(sondes[i].lat), slo = num(sondes[i].lon);
      if (sla == null || slo == null) continue;
      var sp = proj(slo, sla);
      g.save();
      g.translate(sp[0], sp[1]); g.rotate(Math.PI / 4);
      g.fillStyle = 'rgba(233,241,250,0.92)'; g.strokeStyle = '#10203a'; g.lineWidth = 1;
      g.fillRect(-3, -3, 6, 6); g.strokeRect(-3, -3, 6, 6);
      g.restore();
    }

    // ---- 8) VDM centers: cross + ring, labeled with MSLP
    var vdm = m.vdm_centers || [];
    for (i = 0; i < vdm.length; i++) {
      var vla = num(vdm[i].lat), vlo = num(vdm[i].lon);
      if (vla == null || vlo == null) continue;
      var vp = proj(vlo, vla);
      this._drawVdm(g, vp[0], vp[1], vdm[i], L.w, L.h);
    }

    g.restore();

    // map box border + legend (in figure space)
    g.save();
    g.strokeStyle = C.border; g.lineWidth = 1;
    g.strokeRect(L.x + 0.5, L.y + 0.5, L.w - 1, L.h - 1);
    g.restore();
    this._drawLegend(g);
    this._drawWatermark(g, L);
  };

  // Draw the georeferenced satellite frame, scaled/positioned from its [w,e,s,n]
  // extent into the current map extent. Only reached when self._sat is set.
  ReconViewer.prototype._drawSatBackdrop = function (g, proj, W, H) {
    try {
      var b = this._sat.ext;            // [w,e,s,n]
      var tl = proj(b[0], b[3]);        // top-left = (w, n)
      var br = proj(b[1], b[2]);        // bottom-right = (e, s)
      var x = tl[0], y = tl[1], w = br[0] - tl[0], h = br[1] - tl[1];
      g.save();
      g.globalAlpha = 0.85;
      g.drawImage(this._sat.img, x, y, w, h);
      g.restore();
      // a faint darkening so the barbs stay legible over bright cloud tops
      g.save(); g.globalAlpha = 0.25; g.fillStyle = '#07101c'; g.fillRect(0, 0, W, H); g.restore();
    } catch (e) { /* tainted canvas / draw fail -> just the basemap */ }
  };

  // ---- standard meteorological wind barbs ----
  // Sub-sample the track to one barb per ~step px of along-track distance for a
  // dense, Tropical-Tidbits-style comb (the track is ~1000 obs; this yields a
  // tight evenly-spaced barb field without raw-resolution clutter).
  ReconViewer.prototype._drawBarbs = function (g, pts, S) {
    var step = 10;                       // min spacing (px) between barb roots (dense TT-style comb)
    var step2 = step * step;
    var lastX = null, lastY = null;
    g.save();
    g.lineWidth = S.barbLw; g.lineJoin = 'round'; g.lineCap = 'round';
    for (var i = 0; i < pts.length; i++) {
      var p = pts[i]; if (!p) continue;
      if (lastX != null) {
        var dx = p.x - lastX, dy = p.y - lastY;
        if (dx * dx + dy * dy < step2) continue;   // too close to the last barb
      }
      var ob = p.ob;
      var spd = this._barbSpeed(ob);
      var dir = num(ob.wdir);
      var colKt = this._barbColorKt(ob);
      if (spd == null || dir == null) continue;
      var col = this._windColor(colKt);
      var suspect = (this.windMode === 'sfmr') && (ob.sfmr_suspect === true);
      this._barb(g, p.x, p.y, spd, dir, col, suspect);
      lastX = p.x; lastY = p.y;
    }
    g.restore();
  };

  // Draw one barb at (x,y): shaft points FROM the wind (wdir = direction FROM,
  // deg). half-barb=5kt, full-barb=10kt, pennant=50kt. A calm (<5kt) ob is a
  // small open ring. Suspect SFMR obs are drawn dashed + hollow-rooted.
  ReconViewer.prototype._barb = function (g, x, y, kt, dirFrom, color, suspect) {
    var SHAFT = 14, BARB = 6, PEN = 7, SP = 3.6;   // px geometry
    // shaft unit vector points toward the source of the wind (FROM)
    var a = (dirFrom) * Math.PI / 180;
    var ux = Math.sin(a), uy = -Math.cos(a);        // 0deg=N -> up
    // perpendicular (for barb/pennant flags), to the left of the shaft
    var px = -uy, py = ux;

    g.save();
    g.strokeStyle = color; g.fillStyle = color;
    if (suspect) g.setLineDash([3, 2]);

    var spd = Math.round(kt / 5) * 5;               // barbs encode in 5-kt steps
    if (spd < 5) {
      // calm: small open ring
      g.beginPath(); g.arc(x, y, 2.6, 0, 6.2832);
      g.stroke();
      g.restore();
      return;
    }

    // shaft from root (x,y) outward toward the wind source
    var ex = x + ux * SHAFT, ey = y + uy * SHAFT;
    g.beginPath(); g.moveTo(x, y); g.lineTo(ex, ey); g.stroke();
    // root dot (hollow if suspect, filled otherwise)
    g.beginPath(); g.arc(x, y, 1.5, 0, 6.2832);
    if (suspect) g.stroke(); else g.fill();

    // place flags from the tip back toward the root
    var rem = spd;
    var pos = SHAFT;                                 // distance along shaft from root
    var nPen = Math.floor(rem / 50); rem -= nPen * 50;
    var nFull = Math.floor(rem / 10); rem -= nFull * 10;
    var nHalf = Math.floor(rem / 5);

    var k;
    for (k = 0; k < nPen; k++) {
      var b0x = x + ux * pos, b0y = y + uy * pos;
      var b1x = x + ux * (pos - SP), b1y = y + uy * (pos - SP);
      var tipx = b0x + px * PEN, tipy = b0y + py * PEN;
      g.beginPath();
      g.moveTo(b0x, b0y); g.lineTo(tipx, tipy); g.lineTo(b1x, b1y); g.closePath();
      g.fill();
      pos -= SP + 1.4;
    }
    if (nPen) pos -= 1.0;
    for (k = 0; k < nFull; k++) {
      var f0x = x + ux * pos, f0y = y + uy * pos;
      var ftipx = f0x + px * BARB, ftipy = f0y + py * BARB;
      g.beginPath(); g.moveTo(f0x, f0y); g.lineTo(ftipx, ftipy); g.stroke();
      pos -= SP;
    }
    for (k = 0; k < nHalf; k++) {
      // a lone half-barb sits one step IN from the tip so it is not at the very end
      var hpos = (nPen === 0 && nFull === 0) ? (pos - SP) : pos;
      var h0x = x + ux * hpos, h0y = y + uy * hpos;
      var htipx = h0x + px * (BARB / 2), htipy = h0y + py * (BARB / 2);
      g.beginPath(); g.moveTo(h0x, h0y); g.lineTo(htipx, htipy); g.stroke();
      pos -= SP;
    }
    g.restore();
  };

  // faint lat/lon graticule for a small (sub-30deg) extent, with edge labels
  ReconViewer.prototype._drawGraticule = function (g, ext, W, H) {
    var S = this._S();
    var lonSpan = ext[1] - ext[0], latSpan = ext[3] - ext[2];
    var step = (Math.max(lonSpan, latSpan) > 12) ? 5 : (Math.max(lonSpan, latSpan) > 6 ? 2 : 1);
    g.save();
    g.strokeStyle = S.grid; g.lineWidth = 0.6;
    g.beginPath();
    var l0 = Math.ceil(ext[0] / step) * step, lon, x;
    for (lon = l0; lon <= ext[1]; lon += step) {
      x = (lon - ext[0]) / lonSpan * W; g.moveTo(x, 0); g.lineTo(x, H);
    }
    var b0 = Math.ceil(ext[2] / step) * step, lat, y;
    for (lat = b0; lat <= ext[3]; lat += step) {
      y = (ext[3] - lat) / latSpan * H; g.moveTo(0, y); g.lineTo(W, y);
    }
    g.stroke();
    // small edge labels
    g.fillStyle = S.gridLab; g.font = '600 9px ' + FONT;
    g.textBaseline = 'bottom'; g.textAlign = 'center';
    for (lon = l0; lon <= ext[1]; lon += step) {
      x = (lon - ext[0]) / lonSpan * W;
      if (x < 12 || x > W - 12) continue;
      g.fillText(this._lonLab(lon), x, H - 2);
    }
    g.textBaseline = 'middle'; g.textAlign = 'left';
    for (lat = b0; lat <= ext[3]; lat += step) {
      y = (ext[3] - lat) / latSpan * H;
      if (y < 9 || y > H - 9) continue;
      g.fillText(this._latLab(lat), 3, y);
    }
    g.restore();
  };

  ReconViewer.prototype._lonLab = function (lon) {
    var l = lon; while (l > 180) l -= 360; while (l < -180) l += 360;
    return Math.abs(Math.round(l)) + (l >= 0 ? 'E' : 'W');
  };
  ReconViewer.prototype._latLab = function (lat) {
    return Math.abs(Math.round(lat)) + (lat >= 0 ? 'N' : 'S');
  };

  ReconViewer.prototype._drawVdm = function (g, x, y, v, mw, mh) {
    g.save();
    g.lineCap = 'round'; g.lineJoin = 'round';
    function cross(stroke, lw) {
      g.strokeStyle = stroke; g.lineWidth = lw;
      g.beginPath(); g.arc(x, y, 6, 0, 6.2832); g.stroke();
      g.beginPath();
      g.moveTo(x - 9, y); g.lineTo(x + 9, y);
      g.moveTo(x, y - 9); g.lineTo(x, y + 9);
      g.stroke();
    }
    cross('rgba(7,16,28,0.9)', 4);
    cross('#ffffff', 1.8);
    // MSLP label on a dark pill
    var mslp = num(v.mslp_hpa);
    if (mslp != null) {
      var txt = (Math.round(mslp * 10) / 10) + ' hPa';
      g.font = '700 10px ' + FONT; g.textBaseline = 'middle';
      var tw = g.measureText(txt).width, padx = 4, bw = tw + padx * 2, bh = 15;
      var left = (x + 12 + bw <= mw - 2);
      var bx = left ? (x + 12) : (x - 12 - bw), by = y - bh / 2;
      if (by < 2) by = 2; if (by + bh > mh) by = mh - bh;
      roundRectPath(g, bx, by, bw, bh, 3);
      g.fillStyle = 'rgba(7,16,28,0.85)'; g.fill();
      g.strokeStyle = 'rgba(255,255,255,0.55)'; g.lineWidth = 1; g.stroke();
      g.textAlign = 'left'; g.fillStyle = '#ffffff'; g.fillText(txt, bx + padx, by + bh / 2 + 0.5);
    }
    g.restore();
  };

  // discrete TC kt-scale legend (bottom-left of the map) + glyph key
  ReconViewer.prototype._drawLegend = function (g) {
    var L = this.layout.map;
    // Horizontal discrete kt color bar (reference-style): equal-width segments
    // between the 15 scale breakpoints, kt values labeled beneath, triangle
    // ends for off-scale, caption below. No category labels.
    var scale = this._S().scale;        // [[minKt,color],...] 15 bins
    var nseg = scale.length - 1;        // 14 segments between 15 breakpoints
    var pad = 8, tri = 9, barH = 11;
    var barW = Math.min(L.w - 84, 452);
    var segW = (barW - 2 * tri) / nseg;
    var labH = 11, capH = 12;
    var boxW = barW + pad * 2;
    var boxH = pad * 2 + barH + 4 + labH + 5 + capH;
    var x = L.x + 10, y = L.y + L.h - boxH - 9;
    g.save();
    roundRectPath(g, x, y, boxW, boxH, 6);
    g.fillStyle = 'rgba(7,16,28,0.86)'; g.fill();
    g.strokeStyle = C.border; g.lineWidth = 1; g.stroke();

    var bx = x + pad, by = y + pad, mid = by + barH / 2;
    // left triangle: off-scale low (lowest bin color)
    g.fillStyle = scale[0][1];
    g.beginPath(); g.moveTo(bx + tri, by); g.lineTo(bx + tri, by + barH); g.lineTo(bx, mid); g.closePath(); g.fill();
    // equal-width color segments
    for (var i = 0; i < nseg; i++) {
      g.fillStyle = scale[i][1];
      g.fillRect(bx + tri + i * segW, by, segW + 0.6, barH);
    }
    // right triangle: off-scale high (top bin color, >= last breakpoint)
    var rx = bx + tri + nseg * segW;
    g.fillStyle = scale[nseg][1];
    g.beginPath(); g.moveTo(rx, by); g.lineTo(rx, by + barH); g.lineTo(rx + tri, mid); g.closePath(); g.fill();
    // hairline frame around the rectangular part
    g.strokeStyle = 'rgba(220,232,246,0.30)'; g.lineWidth = 0.8;
    g.strokeRect(bx + tri + 0.5, by + 0.5, nseg * segW - 1, barH - 1);

    // kt breakpoint labels + ticks at every segment boundary
    var ly = by + barH + 4;
    g.font = '600 8px ' + FONT; g.textAlign = 'center'; g.textBaseline = 'top';
    for (i = 0; i < scale.length; i++) {
      var tx = bx + tri + i * segW;
      g.strokeStyle = 'rgba(220,232,246,0.42)'; g.lineWidth = 0.8;
      g.beginPath(); g.moveTo(tx, by + barH); g.lineTo(tx, by + barH + 2.5); g.stroke();
      g.fillStyle = C.fg;
      g.fillText(String(scale[i][0]), tx, ly);
    }
    // caption
    g.fillStyle = C.muted; g.font = '600 9px ' + FONT; g.textAlign = 'left';
    var cap = (this.windMode === 'sfmr')
      ? 'Peak 10-second Average SFMR Surface Wind Speed (kt)'
      : 'Peak 10-second Average Flight-level Wind Speed (kt)';
    g.fillText(cap, bx, ly + labH + 5);
    g.restore();

    // glyph key (top-left): barb spec + VDM + sonde + suspect note
    var ky = L.y + 8, kx = L.x + 8;
    g.save();
    var keyW = 184, keyRows = 4, keyH = pad * 2 + keyRows * 13;
    roundRectPath(g, kx, ky, keyW, keyH, 5);
    g.fillStyle = 'rgba(7,16,28,0.82)'; g.fill();
    g.strokeStyle = C.border; g.lineWidth = 1; g.stroke();
    g.font = '500 9.5px ' + FONT; g.textAlign = 'left'; g.textBaseline = 'middle';
    var ry = ky + pad + 6;
    g.fillStyle = C.muted;
    g.fillText('Barbs: half 5 / full 10 / pennant 50 kt', kx + pad, ry);
    ry += 13;
    // VDM glyph
    g.strokeStyle = '#ffffff'; g.lineWidth = 1.4;
    g.beginPath(); g.arc(kx + pad + 5, ry, 4, 0, 6.2832); g.stroke();
    g.beginPath(); g.moveTo(kx + pad, ry); g.lineTo(kx + pad + 10, ry);
    g.moveTo(kx + pad + 5, ry - 5); g.lineTo(kx + pad + 5, ry + 5); g.stroke();
    g.fillStyle = C.muted; g.fillText('VDM center fix (MSLP)', kx + pad + 18, ry);
    ry += 13;
    // sonde glyph
    g.save(); g.translate(kx + pad + 5, ry); g.rotate(Math.PI / 4);
    g.fillStyle = 'rgba(233,241,250,0.92)'; g.fillRect(-3, -3, 6, 6); g.restore();
    g.fillStyle = C.muted; g.fillText('Dropsonde', kx + pad + 18, ry);
    ry += 13;
    // suspect note
    g.strokeStyle = C.muted; g.lineWidth = 1; g.setLineDash([3, 2]);
    g.beginPath(); g.moveTo(kx + pad, ry); g.lineTo(kx + pad + 10, ry); g.stroke();
    g.setLineDash([]);
    g.fillStyle = C.muted; g.fillText('Dashed = suspect SFMR', kx + pad + 18, ry);
    g.restore();
  };

  // Watermark in the map's top-right corner, clear of the bottom-left colorbar,
  // the bottom lon labels and the left lat labels. A soft dark halo keeps it
  // legible over bright land/cloud without competing with the data.
  ReconViewer.prototype._drawWatermark = function (g, L) {
    g.save();
    g.font = '700 12px ' + FONT; g.textAlign = 'right'; g.textBaseline = 'top';
    g.shadowColor = 'rgba(4,9,16,0.85)'; g.shadowBlur = 3;
    g.fillStyle = 'rgba(233,241,250,0.5)';
    g.fillText(WATERMARK, L.x + L.w - 10, L.y + 9);
    g.restore();
  };

  // ====================================================================
  // MULTI-PANEL TIME SERIES (shared x-axis, stacked):
  //  (a) MSLP (p_sfc, high at top) + flight-level wind (wspd)
  //  (b) SFMR wind (sfmr) + rain rate (rain, twin) ; suspect marked
  //  (c) temperature (temp) + dewpoint (dwpt)
  //  (d) static pressure (plane_p) + pressure-altitude (twin)
  // ====================================================================
  // Which time-series panels have data for the current scope. Empty panels (e.g.
  // SFMR on a mission that carried no SFMR) are HIDDEN so the figure never shows a
  // blank axis box; the layout (panel count) and this draw pass both read this.
  ReconViewer.prototype._activePanels = function () {
    var track = this._scopedTrack();
    function any(key) { for (var i = 0; i < track.length; i++) if (num(track[i][key]) != null) return true; return false; }
    var hasRain = any('rain');
    var defs = [];
    if (any('p_sfc') || any('wspd')) defs.push({ kind: 'mslp_wind', title: 'Sea-level pressure + flight-level wind' });
    if (any('sfmr')) defs.push({ kind: 'sfmr_rain', title: hasRain ? 'SFMR surface wind + rain rate' : 'SFMR surface wind' });
    if (any('temp') || any('dwpt')) defs.push({ kind: 'temp_dwpt', title: 'Temperature + dewpoint' });
    if (any('plane_p')) defs.push({ kind: 'pres_alt', title: 'Aircraft static pressure + altitude' });
    return defs;
  };

  ReconViewer.prototype._drawTimeSeries = function (g) {
    var L = this.layout.ts, m = this.mission;
    var track = this._scopedTrack();

    // shared time domain
    var t0 = null, t1 = null;
    for (var i = 0; i < track.length; i++) {
      var ts = Date.parse(track[i].t || '');
      if (isNaN(ts)) continue;
      if (t0 == null || ts < t0) t0 = ts;
      if (t1 == null || ts > t1) t1 = ts;
    }
    if (t0 == null || t1 == null || t1 <= t0) {
      g.save();
      g.fillStyle = 'rgba(10,19,36,0.7)'; g.fillRect(L.x, L.y, L.w, L.h);
      g.strokeStyle = C.border; g.lineWidth = 1; g.strokeRect(L.x + 0.5, L.y + 0.5, L.w - 1, L.h - 1);
      g.fillStyle = C.muted; g.font = '600 12px ' + FONT; g.textAlign = 'center'; g.textBaseline = 'middle';
      g.fillText('No time-series data for this pass.', L.x + L.w / 2, L.y + L.h / 2);
      g.restore();
      return;
    }
    var tspan = t1 - t0;

    // only the panels with data (empty SFMR/temp/etc. panels are hidden)
    var panels = this._activePanels();
    if (!panels.length) {
      g.save();
      g.fillStyle = 'rgba(10,19,36,0.7)'; g.fillRect(L.x, L.y, L.w, L.h);
      g.strokeStyle = C.border; g.lineWidth = 1; g.strokeRect(L.x + 0.5, L.y + 0.5, L.w - 1, L.h - 1);
      g.fillStyle = C.muted; g.font = '600 12px ' + FONT; g.textAlign = 'center'; g.textBaseline = 'middle';
      g.fillText('No time-series data for this pass.', L.x + L.w / 2, L.y + L.h / 2);
      g.restore();
      return;
    }

    for (var pi = 0; pi < panels.length; pi++) {
      var py = L.y + pi * (L.panelH + L.gap);
      this._drawPanel(g, panels[pi], track, L.x, py, L.w, L.panelH, t0, tspan,
        pi === panels.length - 1);
    }
  };

  // One time-series panel. `last` => draw the x time ticks under it.
  ReconViewer.prototype._drawPanel = function (g, panel, track, X0, Y0, W, H, t0, tspan, last) {
    var self = this;
    g.save();
    g.fillStyle = 'rgba(10,19,36,0.72)'; g.fillRect(X0, Y0, W, H);
    g.strokeStyle = C.border; g.lineWidth = 1; g.strokeRect(X0 + 0.5, Y0 + 0.5, W - 1, H - 1);

    var gl = 46, gr = 46, gt = 16, gb = last ? 16 : 8;
    var px = X0 + gl, py = Y0 + gt, pw = W - gl - gr, ph = H - gt - gb;

    function X(t) { return px + (t - t0) / tspan * pw; }

    // collect the two series for this panel
    var leftKey, rightKey, leftLab, rightLab, leftColor, rightColor;
    var leftInvert = false, rightSeries = true;
    if (panel.kind === 'mslp_wind') {
      leftKey = 'p_sfc'; leftLab = 'MSLP hPa'; leftColor = '#cfe0f5';
      rightKey = 'wspd'; rightLab = 'FL kt'; rightColor = '#ffc857';
    } else if (panel.kind === 'sfmr_rain') {
      leftKey = 'sfmr'; leftLab = 'SFMR kt'; leftColor = '#5fd1e0';
      rightKey = 'rain'; rightLab = 'mm/hr'; rightColor = '#7aa2ff';
    } else if (panel.kind === 'temp_dwpt') {
      leftKey = 'temp'; leftLab = 'Temp C'; leftColor = '#ff8b6b';
      rightKey = 'dwpt'; rightLab = 'Dewpt C'; rightColor = '#5fd18f';
      // temp/dwpt share one axis (both degrees C) -> draw both against the left
    } else { // pres_alt
      leftKey = 'plane_p'; leftLab = 'Static hPa'; leftColor = '#cfe0f5';
      rightKey = '__alt__'; rightLab = 'Alt m'; rightColor = '#a4b9d6';
    }

    // value extractor (with the derived altitude special case)
    function val(ob, key) {
      if (key === '__alt__') {
        var pp = num(ob.plane_p);
        if (pp == null || pp <= 0) return null;
        return 44330 * (1 - Math.pow(pp / 1013.25, 0.1903));
      }
      return num(ob[key]);
    }

    // axis ranges
    var lmin = Infinity, lmax = -Infinity, rmin = Infinity, rmax = -Infinity, anyL = false, anyR = false;
    var sameAxis = (panel.kind === 'temp_dwpt');
    for (var i = 0; i < track.length; i++) {
      var lv = val(track[i], leftKey), rv = val(track[i], rightKey);
      if (lv != null) { anyL = true; if (lv < lmin) lmin = lv; if (lv > lmax) lmax = lv; }
      if (rv != null) { anyR = true; if (rv < rmin) rmin = rv; if (rv > rmax) rmax = rv; }
    }
    if (sameAxis) {
      // temp + dewpoint -> one shared C axis spanning both
      var allmin = Math.min(anyL ? lmin : Infinity, anyR ? rmin : Infinity);
      var allmax = Math.max(anyL ? lmax : -Infinity, anyR ? rmax : -Infinity);
      if (!isFinite(allmin)) { allmin = 0; allmax = 30; }
      lmin = rmin = allmin; lmax = rmax = allmax;
    }
    if (!anyL) { lmin = 0; lmax = 1; }
    if (lmax <= lmin) lmax = lmin + 1;
    if (!anyR) { rmin = 0; rmax = 1; }
    if (rmax <= rmin) rmax = rmin + 1;
    // pad
    var lp = (lmax - lmin) * 0.08 + 1e-6; lmin -= lp; lmax += lp;
    var rp = (rmax - rmin) * 0.08 + 1e-6; rmin -= rp; rmax += rp;
    // rain / wind floor at zero
    if (panel.kind === 'sfmr_rain') { rmin = 0; if (lmin < 0) lmin = 0; }
    if (panel.kind === 'mslp_wind') { if (rmin < 0) rmin = 0; }

    function YL(v) {
      if (leftInvert) return py + (v - lmin) / (lmax - lmin) * ph;     // inverted (lows up)
      return py + ph - (v - lmin) / (lmax - lmin) * ph;
    }
    function YR(v) { return py + ph - (v - rmin) / (rmax - rmin) * ph; }

    // horizontal grid
    g.strokeStyle = 'rgba(176,196,222,0.08)'; g.lineWidth = 0.6;
    g.beginPath();
    for (var k = 0; k <= 3; k++) { var yy = py + ph * k / 3; g.moveTo(px, yy); g.lineTo(px + pw, yy); }
    g.stroke();

    // left axis ticks + label
    g.font = '600 8.5px ' + FONT; g.textBaseline = 'middle';
    for (k = 0; k <= 3; k++) {
      var yL = py + ph * k / 3;
      var vL = leftInvert ? (lmin + (lmax - lmin) * (k / 3)) : (lmax - (lmax - lmin) * (k / 3));
      g.fillStyle = leftColor; g.textAlign = 'right';
      g.fillText(this._axTick(vL, leftKey), px - 5, yL);
    }
    if (!sameAxis) {
      for (k = 0; k <= 3; k++) {
        var yR = py + ph * k / 3;
        var vR = rmax - (rmax - rmin) * (k / 3);
        g.fillStyle = rightColor; g.textAlign = 'left';
        g.fillText(this._axTick(vR, rightKey), px + pw + 5, yR);
      }
    }

    // axis titles
    g.save();
    g.translate(X0 + 11, py + ph / 2); g.rotate(-Math.PI / 2);
    g.textAlign = 'center'; g.textBaseline = 'middle'; g.font = '700 8.5px ' + FONT;
    g.fillStyle = leftColor; g.fillText(leftLab + (leftInvert ? ' (inv)' : ''), 0, 0);
    g.restore();
    if (!sameAxis) {
      g.save();
      g.translate(X0 + W - 9, py + ph / 2); g.rotate(Math.PI / 2);
      g.textAlign = 'center'; g.textBaseline = 'middle'; g.font = '700 8.5px ' + FONT;
      g.fillStyle = rightColor; g.fillText(rightLab, 0, 0);
      g.restore();
    }

    // ---- plot the series
    g.lineJoin = 'round'; g.lineCap = 'round';

    // right series first (under), unless it's the same-axis temp/dwpt case
    if (!sameAxis && anyR) {
      if (panel.kind === 'sfmr_rain' && rightKey === 'rain') {
        // rain as faint filled area under the curve
        g.fillStyle = 'rgba(122,162,255,0.16)';
        this._areaPath(g, track, function (ob) { return val(ob, rightKey); }, X, YR, py + ph);
        g.fill();
      }
      g.strokeStyle = rightColor; g.lineWidth = 1.4;
      this._linePath(g, track, function (ob) { return val(ob, rightKey); }, X, YR);
    }

    // left series (on top)
    if (anyL) {
      // special: SFMR panel colors the wind line per-bin and marks suspect pts
      if (panel.kind === 'sfmr_rain') {
        this._coloredLine(g, track, function (ob) { return num(ob.sfmr); }, X, YL, function (kt) { return self._windColor(kt); });
        // suspect markers (hollow circles, dashed look via small open rings)
        for (i = 0; i < track.length; i++) {
          if (track[i].sfmr_suspect !== true) continue;
          var sv = num(track[i].sfmr); var st = Date.parse(track[i].t || '');
          if (sv == null || isNaN(st)) continue;
          g.strokeStyle = '#ff5a1f'; g.lineWidth = 1.2;
          g.beginPath(); g.arc(X(st), YL(sv), 2.6, 0, 6.2832); g.stroke();
        }
      } else if (panel.kind === 'mslp_wind') {
        // FL wind on the RIGHT axis colored per-bin; pressure on LEFT clean line
        g.strokeStyle = rightColor; g.lineWidth = 1.5;
        this._coloredLine(g, track, function (ob) { return num(ob.wspd); }, X, YR, function (kt) { return self._windColor(kt); });
        g.strokeStyle = leftColor; g.lineWidth = 1.7;
        this._linePath(g, track, function (ob) { return num(ob.p_sfc); }, X, YL);
      } else {
        g.strokeStyle = leftColor; g.lineWidth = 1.5;
        this._linePath(g, track, function (ob) { return val(ob, leftKey); }, X, YL);
        if (sameAxis && anyR) {
          g.strokeStyle = rightColor; g.lineWidth = 1.5;
          this._linePath(g, track, function (ob) { return val(ob, rightKey); }, X, YL);
        }
      }
    }

    // panel title (top-left, inside the plot area)
    g.font = '700 9.5px ' + FONT; g.textAlign = 'left'; g.textBaseline = 'top';
    g.fillStyle = C.fg; g.fillText(panel.title, px + 4, py + 2);

    // SFMR caveat on the SFMR panel
    if (panel.kind === 'sfmr_rain') {
      g.font = '500 8px ' + FONT; g.textAlign = 'right'; g.textBaseline = 'top';
      g.fillStyle = C.muted; g.fillText('SFMR unreliable in heavy rain / very high wind', px + pw - 2, py + 2);
    }

    // x time ticks under the last panel
    if (last) {
      g.fillStyle = C.muted; g.textAlign = 'center'; g.textBaseline = 'top';
      g.font = '600 8.5px ' + FONT;
      for (k = 0; k <= 4; k++) {
        var tt = t0 + tspan * (k / 4);
        g.fillText(hhmm(new Date(tt).toISOString()), X(tt), py + ph + 3);
      }
    }
    g.restore();
  };

  // tick formatter per series key
  ReconViewer.prototype._axTick = function (v, key) {
    if (key === '__alt__') {
      if (Math.abs(v) >= 1000) return (v / 1000).toFixed(1) + 'k';
      return String(Math.round(v));
    }
    if (key === 'rain') return (Math.round(v * 10) / 10).toString();
    return String(Math.round(v));
  };

  ReconViewer.prototype._linePath = function (g, track, getv, X, Y) {
    var prev = null;
    g.beginPath();
    for (var i = 0; i < track.length; i++) {
      var v = getv(track[i]); var t = Date.parse(track[i].t || '');
      if (v == null || isNaN(t)) { prev = null; continue; }
      var x = X(t), y = Y(v);
      if (prev) g.lineTo(x, y); else g.moveTo(x, y);
      prev = [x, y];
    }
    g.stroke();
  };

  ReconViewer.prototype._areaPath = function (g, track, getv, X, Y, baseY) {
    var started = false, firstX = null, lastX = null;
    g.beginPath();
    for (var i = 0; i < track.length; i++) {
      var v = getv(track[i]); var t = Date.parse(track[i].t || '');
      if (v == null || isNaN(t)) continue;
      var x = X(t), y = Y(v);
      if (!started) { g.moveTo(x, baseY); g.lineTo(x, y); firstX = x; started = true; }
      else g.lineTo(x, y);
      lastX = x;
    }
    if (started) { g.lineTo(lastX, baseY); g.closePath(); }
  };

  // per-segment colored line (color from the segment's end value)
  ReconViewer.prototype._coloredLine = function (g, track, getv, X, Y, colorOf) {
    var prev = null;
    for (var i = 0; i < track.length; i++) {
      var v = getv(track[i]); var t = Date.parse(track[i].t || '');
      if (v == null || isNaN(t)) { prev = null; continue; }
      var x = X(t), y = Y(v);
      if (prev) {
        g.strokeStyle = colorOf(v);
        g.beginPath(); g.moveTo(prev[0], prev[1]); g.lineTo(x, y); g.stroke();
      }
      prev = [x, y];
    }
  };

  // disclosure line + data source, bottom of the figure
  ReconViewer.prototype._drawFooter = function (g) {
    g.save();
    g.font = '500 10.5px ' + FONT; g.textAlign = 'left'; g.textBaseline = 'alphabetic';
    g.fillStyle = C.muted;
    g.fillText(DISCLOSURE, this.layout.pad, this.layout.footerY);
    g.restore();
  };

  // ---- hover tooltip over the track (map-local hit test) ----
  ReconViewer.prototype._hover = function (ev) {
    var tip = this.dom.tooltip, L = this.layout && this.layout.map;
    if (!tip || !L || !this._pts || !this._pts.length) return;
    var rect = this.dom.canvas.getBoundingClientRect();
    var sx = this.dom.canvas.width / rect.width / this.dpr;
    var mx = (ev.clientX - rect.left) * sx - L.x;
    var my = (ev.clientY - rect.top) * sx - L.y;
    if (mx < 0 || my < 0 || mx > L.w || my > L.h) { tip.style.display = 'none'; return; }
    var best = null, bestD = 12 * 12;
    for (var i = 0; i < this._pts.length; i++) {
      var p = this._pts[i]; if (!p) continue;
      var dx = p.x - mx, dy = p.y - my, d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = p; }
    }
    if (!best) { tip.style.display = 'none'; return; }
    var ob = best.ob;
    var sfmr = num(ob.sfmr), wspd = num(ob.wspd), ps = num(ob.p_sfc), pk = num(ob.pkwnd),
        rain = num(ob.rain), wdir = num(ob.wdir);
    var lines = [];
    lines.push(hhmm(ob.t));
    if (wspd != null) lines.push('FL wind ' + Math.round(wspd) + ' kt' + (wdir != null ? (' / ' + Math.round(wdir) + '°') : ''));
    if (pk != null) lines.push('Peak FL ' + Math.round(pk) + ' kt');
    if (sfmr != null) lines.push('SFMR ' + Math.round(sfmr) + ' kt' + (ob.sfmr_suspect === true ? ' (suspect)' : ''));
    if (rain != null) lines.push('Rain ' + (Math.round(rain * 10) / 10) + ' mm/hr');
    if (ps != null) lines.push('MSLP ' + Math.round(ps) + ' hPa');
    tip.style.display = 'block';
    tip.style.left = (ev.clientX - rect.left + 12) + 'px';
    tip.style.top = (ev.clientY - rect.top + 12) + 'px';
    tip.innerHTML = lines.join('<br>');
  };

  // ====================================================================
  // Shareable export: Download PNG / Copy (the full figure) via canvas.toBlob.
  // ====================================================================
  ReconViewer.prototype._exportName = function () {
    var m = this.mission;
    var base = (m && m.mission_id) ? m.mission_id : (this.curStorm || 'recon');
    return 'recon_' + String(base).replace(/[^A-Za-z0-9_-]/g, '_') + '.png';
  };

  ReconViewer.prototype._download = function () {
    var cv = this.dom.canvas; if (!cv || !this.mission) return;
    var self = this;
    function viaDataUrl() {
      try {
        var a = document.createElement('a');
        a.href = cv.toDataURL('image/png');
        a.download = self._exportName();
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
      } catch (e) { console.warn('recon: export failed', e); }
    }
    if (cv.toBlob) {
      cv.toBlob(function (blob) {
        if (!blob) { viaDataUrl(); return; }
        var u = URL.createObjectURL(blob), a = document.createElement('a');
        a.href = u; a.download = self._exportName();
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        requestAnimationFrame(function () { URL.revokeObjectURL(u); });
      }, 'image/png');
    } else { viaDataUrl(); }
  };

  ReconViewer.prototype._copy = function () {
    var cv = this.dom.canvas; if (!cv || !this.mission) return;
    var self = this;
    if (!(navigator.clipboard && window.ClipboardItem && cv.toBlob)) { this._download(); return; }
    cv.toBlob(function (blob) {
      if (!blob) { self._download(); return; }
      try {
        navigator.clipboard.write([new window.ClipboardItem({ 'image/png': blob })])
          .then(function () { self._flashCopy('Copied'); }, function () { self._download(); });
      } catch (e) { self._download(); }
    }, 'image/png');
  };

  ReconViewer.prototype._flashCopy = function (msg) {
    var b = this.dom.copy; if (!b) return;
    var orig = b.textContent; b.textContent = msg;
    setTimeout(function () { b.textContent = orig; }, 1400);
  };

  // ---- style / wind-mode / scope pickers ----
  ReconViewer.prototype._setStyle = function (key) {
    this.style = STYLES[key] ? key : 'sshws';
    try { localStorage.setItem(LS_STYLE, this.style); } catch (e) {}
    if (this.mission) this._draw();
  };

  ReconViewer.prototype._setWindMode = function (mode) {
    this.windMode = (mode === 'sfmr') ? 'sfmr' : 'fl';
    if (this.mission) this._draw();
  };

  ReconViewer.prototype._setScope = function (scope) {
    this.scope = (scope === 'last10') ? 'last10' : 'full';
    if (this.mission) this._layoutAndDraw();
  };

  // ---- live poll: manifest watermark -> in-place refresh (60s) ----
  // One small manifest GET per tick. An unchanged generated_utc is a true
  // no-op (no further fetches, no re-render). On change, only what actually
  // moved refreshes, preserving the user's tab/storm/mission/scope selection.
  ReconViewer.prototype._schedulePoll = function () {
    clearTimeout(this._pollTimer);
    var self = this;
    this._pollTimer = setTimeout(function () { self._poll(); }, POLL_MS);
  };

  ReconViewer.prototype._poll = function () {
    var self = this;
    this._fetchJson('/manifest.json', true)
      .then(function (m) { self._onPollManifest(m); })
      .catch(function () {})
      .then(function () { self._schedulePoll(); });
  };

  ReconViewer.prototype._stormsVisible = function () {
    return this.tab !== 'current' || !!this.stormLock;
  };

  ReconViewer.prototype._onPollManifest = function (m) {
    if (!m) return;
    var prev = this.manifest;
    if (prev && m.generated_utc === prev.generated_utc) return;   // idle tick
    var hadStorms = !!(this.storms && this.storms.length);
    if (!hadStorms) {
      // empty/failed boot -> live: full boot semantics (no user state to lose)
      if ((m.storms || []).length || m.tcpod_number) this._onManifest(m);
      else this.manifest = m;
      return;
    }
    this.manifest = m;
    var storms = (m.storms || []);
    if (this.stormLock) {
      var locked = this._resolveLock(storms, this.stormLock);
      storms = locked ? [locked] : [];
    }
    if (!storms.length && !m.tcpod_number) {      // season went quiet
      this.storms = [];
      this._showEmpty(true);
      return;
    }
    this.storms = storms;
    this._showEmpty(false);

    // storm dropdown: rebuild only when the option set changed; keep selection
    var sig = m.current_slug + '' + storms.map(function (s) {
      return s.slug + '|' + (s.name || '') + '|' + (s.basin || '') + '|' +
             (s.year || '') + '|' + (s.is_invest ? 1 : 0);
    }).join('');
    if (sig !== this._stormSelSig) {
      this._stormSelSig = sig;
      this._buildStormSelect();
      if (this.curStorm && this._stormBySlug(this.curStorm) &&
          this.dom.stormSel) this.dom.stormSel.value = this.curStorm;
    }
    // selected storm pruned from the index -> fall back like boot
    if (this.curStorm && !this._stormBySlug(this.curStorm)) {
      var fb = m.current_slug || (storms[0] && storms[0].slug);
      if (fb) this._selectStorm(fb);
      return;
    }

    // Current Mission pane (visible only): content-gated render inside
    if (this.tab === 'current' && !this.stormLock) this._loadCurrentView();

    // Storms pane: refresh the SELECTED storm when its index entry moved
    var entry = this._stormBySlug(this.curStorm);
    var prevEntry = null;
    if (prev) {
      var ps = prev.storms || [];
      for (var i = 0; i < ps.length; i++) {
        if (ps[i].slug === this.curStorm) { prevEntry = ps[i]; break; }
      }
    }
    var moved = entry && (!prevEntry ||
      entry.last_ob_utc !== prevEntry.last_ob_utc ||
      entry.mission_count !== prevEntry.mission_count ||
      entry.latest_mission_id !== prevEntry.latest_mission_id);
    if (moved) {
      if (this._stormsVisible()) this._refreshRecon();
      else this._stormsStale = true;              // catch up on tab switch
    }
  };

  // Re-read the selected storm's mission index and advance the plot in place.
  // The user's picked mission is preserved; only when they were already on
  // the storm's latest mission does the view follow a newer sortie.
  ReconViewer.prototype._refreshRecon = function () {
    var self = this, slug = this.curStorm;
    if (!slug) return;
    this._stormsStale = false;
    this._fetchJson('/' + slug + '/recon.json', true)
      .then(function (r) {
        if (self.curStorm !== slug || !r) return;
        var prevLatest = self._latestMissionId(
          (self.recon && self.recon.missions) || []);
        self.recon = r;
        var missions = r.missions || [];
        var newLatest = self._latestMissionId(missions);
        var selId = (self.dom.missionSel && self.dom.missionSel.value) ||
                    (self.mission && self.mission.mission_id) || null;
        self._fillMissionSelect(missions);
        var follow = selId && prevLatest && selId === prevLatest &&
                     newLatest && newLatest !== prevLatest;
        var meta = self._missionMetaById(missions,
                     follow ? newLatest : selId) ||
                   self._missionMetaById(missions, newLatest);
        if (!meta) return;
        if (self.dom.missionSel) self.dom.missionSel.value = meta.mission_id;
        var cur = self.mission;
        if (!cur || cur.mission_id !== meta.mission_id ||
            cur.valid_end !== meta.valid_end || cur.n_obs !== meta.n_obs) {
          self._refreshMissionFile(meta);
        }
      })
      .catch(function () {});
  };

  // Like _loadMissionFile but silent (no status flash), cache-busted, and the
  // in-place swap keeps the sat backdrop up until its replacement loads.
  ReconViewer.prototype._refreshMissionFile = function (meta) {
    var self = this, slug = this.curStorm, seq = ++this._fetchSeq;
    if (!meta || !slug) return;
    this._fetchJson('/' + slug + '/' + meta.file, true)
      .then(function (d) {
        if (seq !== self._fetchSeq || self.curStorm !== slug) return;
        self.mission = d;
        self._layoutAndDraw();
        self._renderStats(d);
        self._maybeLoadSatBackdrop(d, true);
      })
      .catch(function () {});
  };

  // ---- wiring ----
  ReconViewer.prototype._wire = function () {
    var self = this;
    if (this.dom.tabCurrent) this.dom.tabCurrent.addEventListener('click', function () { self._setTab('current'); });
    if (this.dom.tabStorms) this.dom.tabStorms.addEventListener('click', function () { self._setTab('storms'); });
    if (this.dom.stormSel) this.dom.stormSel.addEventListener('change', function () { self._selectStorm(this.value); });
    if (this.dom.missionSel) this.dom.missionSel.addEventListener('change', function () {
      var meta = self._missionMetaById((self.recon && self.recon.missions) || [], this.value);
      if (meta) self._loadMissionFile(meta);
    });
    if (this.dom.download) this.dom.download.addEventListener('click', function () { self._download(); });
    if (this.dom.copy) this.dom.copy.addEventListener('click', function () { self._copy(); });

    if (this.dom.styleSel) {
      this.dom.styleSel.innerHTML = '';
      for (var key in STYLES) if (STYLES.hasOwnProperty(key)) {
        var o = document.createElement('option');
        o.value = key; o.textContent = STYLES[key].label;
        if (key === this.style) o.selected = true;
        this.dom.styleSel.appendChild(o);
      }
      this.dom.styleSel.addEventListener('change', function () { self._setStyle(this.value); });
    }
    if (this.dom.windSel) {
      this.dom.windSel.addEventListener('change', function () { self._setWindMode(this.value); });
      this.dom.windSel.value = this.windMode;
    }
    if (this.dom.scopeSel) {
      this.dom.scopeSel.addEventListener('change', function () { self._setScope(this.value); });
      this.dom.scopeSel.value = this.scope;
    }

    if (this.dom.canvas) {
      this.dom.canvas.addEventListener('mousemove', function (ev) { self._hover(ev); });
      this.dom.canvas.addEventListener('mouseleave', function () { if (self.dom.tooltip) self.dom.tooltip.style.display = 'none'; });
    }
    if (typeof window !== 'undefined' && window.ResizeObserver && this.dom.mapframe) {
      this._ro = new ResizeObserver(function () { self._resizeDebounced(); });
      this._ro.observe(this.dom.mapframe);
    } else if (typeof window !== 'undefined') {
      window.addEventListener('resize', function () { self._resizeDebounced(); });
    }
  };

  ReconViewer.prototype._resizeDebounced = function () {
    var self = this;
    clearTimeout(this._rt);
    this._rt = setTimeout(function () {
      if (!self.mission) return;
      var w = (self.dom.mapframe && self.dom.mapframe.clientWidth) || 0;
      if (w === self._lastAvailW) return;
      self._layoutAndDraw();
    }, 140);
  };

  // small helpers used inside _renderBasin's outlook (kept here to avoid leaking)
  function self_escape(s) {
    return String(s).replace(/[&<>"]/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch];
    });
  }
  function fmtLatLon(lat, lon) {
    var la = Number(lat), lo = Number(lon);
    if (isNaN(la) || isNaN(lo)) return '';
    return Math.abs(la).toFixed(1) + (la >= 0 ? 'N' : 'S') + ' ' +
      Math.abs(lo).toFixed(1) + (lo >= 0 ? 'E' : 'W');
  }

  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('DOMContentLoaded', function () {
      var r = el('recon-viewer');
      if (r) r.__reconView = new ReconViewer(r);
    });
  }
  if (typeof window !== 'undefined') window.ReconViewer = ReconViewer;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { ReconViewer: ReconViewer, STYLES: STYLES, KT_SCALE: KT_SCALE };
  }
})();
