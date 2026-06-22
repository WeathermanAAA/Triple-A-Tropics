/* Aircraft-Recon viewer (/recon/).
 *
 * A SELF-CONTAINED, copyable, TAT-branded figure of one reconnaissance pass:
 * a burned-in header (mission id + valid window + storm + aircraft), the
 * flight track on the shared TATRegions basemap (each ob colored by SFMR
 * surface wind, falling back to flight-level wind where SFMR is null), the
 * VDM center fixes + dropsonde markers, a discrete SSHWS legend, a dual-axis
 * time-series panel (surface pressure inverted vs wind), a disclosure line and
 * the @WeathermanAAA_ watermark, ALL drawn onto ONE <canvas> so a right-click
 * Save / the Download PNG button yields the complete figure.
 *
 * Hydrates entirely from R2 JSON (no server-rendered images). Mirrors the
 * HafsViewer mount shape so the CycloLab per-storm tab can lazy-load this file
 * and do `new ReconViewer(el, { stormLock, ... })`. Dependency-free except for
 * window.TATRegions (basemap + projection), which the host page also loads.
 *
 * Isolated from the other viewers (own IIFE, recon-* ids).
 */
(function () {
  'use strict';

  var BASE_DEFAULT = 'https://cdn.triple-a-tropics.com/recon';
  var POLL_MS = 60000;                 // current.json + manifest refresh cadence
  var WATERMARK = '@WeathermanAAA_';
  var FONT = 'Metropolis, "Helvetica Neue", Arial, sans-serif';
  var DISCLOSURE = 'SFMR unreliable in heavy rain / very high wind; obs are point-in-time.';

  // ---- styles (>=3). A style = a track-color ramp + a couple of figure tones.
  // The MUTED ramp (default) desaturates the canonical SSHWS anchors so the
  // data reads scientific, not neon; Classic is the full-saturation canonical
  // ramp; Dark is a high-contrast variant on a deeper ground. Persisted in
  // localStorage. Each ramp is [maxKt, color] thresholds in knots.
  var STYLES = {
    muted: {
      label: 'Muted',
      ramp: [[33, '#5b86b8'], [63, '#5fae7e'], [82, '#d6c25a'], [95, '#d68f55'],
             [112, '#cf5d55'], [136, '#c265bd']],
      c5: '#9d6fc4',
      bg: '#0b1320', ocean: '#07101c', land: '#2f3f59'
    },
    classic: {
      label: 'Classic SSHWS',
      ramp: [[33, '#3fa4ff'], [63, '#46c56a'], [82, '#ffe14d'], [95, '#ff9a2f'],
             [112, '#f5333c'], [136, '#e33ad4']],
      c5: '#b03bff',
      bg: '#0b1320', ocean: '#07101c', land: '#2f3f59'
    },
    contrast: {
      label: 'High contrast',
      ramp: [[33, '#36b6ff'], [63, '#36e07a'], [82, '#ffe600'], [95, '#ff8c1a'],
             [112, '#ff2a2a'], [136, '#ff39d0']],
      c5: '#c14dff',
      bg: '#04080e', ocean: '#03080f', land: '#243247'
    }
  };
  var LS_STYLE = 'recon.style';

  // figure palette (chrome). The track-color ramp comes from the active STYLE.
  var C = { fg: '#e8ebef', muted: '#9199a4', border: '#2a2e36',
            accent: (typeof window !== 'undefined' && window.TATRegions && window.TATRegions.ACCENT) || '#2b9cff' };

  // canonical SSHWS thresholds (kt) for the legend bands
  var BANDS = [
    { lab: 'TD <34', max: 33 }, { lab: 'TS 34-63', max: 63 },
    { lab: 'C1 64-82', max: 82 }, { lab: 'C2 83-95', max: 95 },
    { lab: 'C3 96-112', max: 112 }, { lab: 'C4 113-136', max: 136 },
    { lab: 'C5 137+', max: Infinity }
  ];

  var WK = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function el(id) { return document.getElementById(id); }
  function num(v) { return (typeof v === 'number' && isFinite(v)) ? v : null; }

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

  // ========================================================================
  function ReconViewer(root, opts) {
    opts = opts || {};
    this.root = root;
    this.base = (opts.base || BASE_DEFAULT).replace(/\/+$/, '');
    this.stormLock = opts.stormLock || null;
    this.startTab = (opts.startTab === 'storms') ? 'storms' : 'current';

    // Self-scope chrome accent to the shared blue without leaking to the site.
    if (root && root.style) root.style.setProperty('--recon-accent', C.accent);

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

    var s = null; try { s = localStorage.getItem(LS_STYLE); } catch (e) {}
    this.style = STYLES[s] ? s : 'muted';

    this._wire();
    this._boot();
  }

  ReconViewer.prototype._S = function () { return STYLES[this.style] || STYLES.muted; };

  // color for a wind speed (kt) under the active style's ramp
  ReconViewer.prototype._windColor = function (kt) {
    var S = this._S();
    if (kt == null || isNaN(kt)) return C.muted;
    for (var i = 0; i < S.ramp.length; i++) if (kt <= S.ramp[i][0]) return S.ramp[i][1];
    return S.c5;
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
    this._status('Loading…');
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
      ? TATRegions.loadGeo()
      : Promise.all([
          fetch('/ne_50m_admin_0_countries.geojson').then(function (r) { return r.json(); }),
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

    // STORMS tab: build the storm selector (current first), hidden when locked.
    if (this.stormLock && this.dom.stormWrap) this.dom.stormWrap.style.display = 'none';
    this._buildStormSelect();

    // CURRENT tab: render the Plan of the Day + spotlight the active/recent mission.
    this._loadCurrentView();

    // STORMS tab: if locked, go straight to that storm; else default to current.
    var startSlug = this.stormLock ? (storms[0] && storms[0].slug)
      : (m.current_slug || (storms[0] && storms[0].slug));
    if (startSlug) this._selectStorm(startSlug);

    // A locked single-storm embed leads with the storm's pass, not the TCPOD.
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
    // a locked embed has no Current tab (no TCPOD context) - hide the tab bar.
    if (this.dom.tabs) this.dom.tabs.style.display = this.stormLock ? 'none' : '';
    if (this.dom.viewCurrent) this.dom.viewCurrent.style.display = (cur && !this.stormLock) ? '' : 'none';
    if (this.dom.viewStorms) this.dom.viewStorms.style.display = (!cur || this.stormLock) ? '' : 'none';
    if (this.dom.tabCurrent) this.dom.tabCurrent.classList.toggle('active', cur);
    if (this.dom.tabStorms) this.dom.tabStorms.classList.toggle('active', !cur);
    // when switching INTO storms, ensure the canvas reflects the loaded mission.
    if ((!cur || this.stormLock) && this.mission) { this._layoutAndDraw(); }
  };

  ReconViewer.prototype._setTab = function (t) {
    this.tab = (t === 'storms') ? 'storms' : 'current';
    this._applyTab();
  };

  // ====================================================================
  // CURRENT MISSION tab: Plan of the Day (lead) + active/recent spotlight.
  // ====================================================================
  ReconViewer.prototype._loadCurrentView = function () {
    var self = this;
    this._fetchJson('/tcpod.json', true)
      .then(function (t) { self._renderTcpod(t); })
      .catch(function (e) { console.warn('recon: tcpod load failed', e); self._renderTcpod(null); });
    this._fetchJson('/current.json', true)
      .then(function (c) { self._renderSpotlight(c); })
      .catch(function (e) { console.warn('recon: current load failed', e); self._renderSpotlight(null); });
  };

  // Render the parsed Plan of the Day as readable cards: header (number + valid
  // window), then per-basin tasked missions, then the next-day outlook. A
  // negative basin reads "No active reconnaissance tasked" cleanly.
  ReconViewer.prototype._renderTcpod = function (t) {
    var host = this.dom.tcpod;
    if (!host) return;
    host.innerHTML = '';
    if (!t) {
      host.appendChild(this._note('Plan of the Day is unavailable right now.'));
      return;
    }
    // header card
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
    // next-day outlook
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

  // Spotlight the active / most-recent mission from current.json below the TCPOD.
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
      self._setMission(mission);
      if (mission.slug && self.dom.stormSel) {
        // align the storms-tab selectors if this storm is in the manifest
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
    // current storm first
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
    if (this.dom.stormWrap) this.dom.stormWrap.style.display = (this.stormLock || ordered.length <= 1) && this.stormLock ? 'none' : '';
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
    this._status('Loading missions…');
    this._fetchJson('/' + slug + '/recon.json', false)
      .then(function (r) {
        if (self.curStorm !== slug) return;   // user moved on
        self.recon = r;
        self._buildMissionSelect(r, preferMissionId);
      })
      .catch(function (e) {
        console.warn('recon: recon.json load failed', e);
        if (self.curStorm === slug) { self._status(''); }
      });
  };

  ReconViewer.prototype._buildMissionSelect = function (recon, preferMissionId) {
    var sel = this.dom.missionSel;
    var missions = (recon && recon.missions) || [];
    if (sel) {
      sel.innerHTML = '';
      // newest first (by valid_start)
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
    }
    if (!missions.length) { this._status('No missions for this storm.'); return; }
    // prefer the requested mission id, else the latest_mission_id, else newest.
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

  // Fetch the per-mission file ({slug}/{file}) and plot it.
  ReconViewer.prototype._loadMissionFile = function (meta) {
    var self = this, slug = this.curStorm, seq = ++this._fetchSeq;
    if (!meta || !slug) return;
    this._status('Loading mission…');
    this._fetchJson('/' + slug + '/' + meta.file, false)
      .then(function (d) {
        if (seq !== self._fetchSeq) return;   // superseded
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
    this._status('');
    this._layoutAndDraw();
    this._renderStats(m);
  };

  // small stat chips under the canvas (live HTML, not part of the export)
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
  // FIGURE: track map + dual-axis time series, all on one canvas.
  // ====================================================================

  // track bbox (lon/lat) padded, then the TATRegions extent for the map rect.
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
    var figW = Math.max(availW, 720);     // legible PNG floor; scales down on mobile
    // figure stacks: header, then the map (wide), then the time-series panel.
    var pad = 16, headerH = 56, gap = 14;
    var mapH = Math.round(figW * 0.42);
    var tsH = Math.round(figW * 0.26);
    var footerH = 26;
    var figH = pad + headerH + mapH + gap + tsH + footerH + pad;

    var dpr = Math.min((typeof window !== 'undefined' && window.devicePixelRatio) || 1, 2);
    this.dpr = dpr; this.figW = figW; this.figH = figH;
    cv.width = Math.round(figW * dpr);
    cv.height = Math.round(figH * dpr);
    cv.style.width = availW + 'px';
    cv.style.height = (availW * figH / figW) + 'px';

    this.layout = {
      pad: pad,
      header: { x: pad, y: pad, w: figW - 2 * pad, h: headerH },
      map: { x: pad, y: pad + headerH, w: figW - 2 * pad, h: mapH },
      ts: { x: pad, y: pad + headerH + mapH + gap, w: figW - 2 * pad, h: tsH },
      footerY: pad + headerH + mapH + gap + tsH + footerH - 8
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
    var title = (name ? (name + '  ·  ') : '') + 'Aircraft Recon';
    g.fillText(title, h.x, h.y + 18);
    g.fillStyle = C.muted; g.font = '600 12.5px ' + FONT;
    var sub = (m.mission_id || '') +
      (m.aircraft ? ('  ·  ' + m.aircraft) : '') +
      (m.valid_start ? ('  ·  ' + fmtZ(m.valid_start) + ' to ' +
        (m.valid_end ? fmtZ(m.valid_end) : '?')) : '');
    g.fillText(sub, h.x, h.y + 38);
    g.restore();
  };

  ReconViewer.prototype._drawMap = function (g) {
    var L = this.layout.map, m = this.mission, S = this._S();
    var track = (m.track || []);
    var ext = this._trackExtent(track);
    this._ext = ext;
    var proj = (window.TATRegions && TATRegions.project)
      ? function (lo, la) { return TATRegions.project(lo, la, ext, L.w, L.h); }
      : function (lo, la) {
          return [(lo - ext[0]) / (ext[1] - ext[0]) * L.w, (ext[3] - la) / (ext[3] - ext[2]) * L.h];
        };

    g.save();
    g.beginPath(); g.rect(L.x, L.y, L.w, L.h); g.clip();
    g.translate(L.x, L.y);
    // basemap fill (ocean + land), graticule subtle, then coast/borders ON TOP.
    var bmStyle = {
      ocean: S.ocean, land: S.land,
      grid: 'rgba(255,255,255,0.05)', gridLw: 0.5,
      coast: 'rgba(150,175,205,0.5)', coastLw: 0.7,
      country: 'rgba(150,175,205,0.4)', countryLw: 0.6,
      state: 'rgba(150,175,205,0.16)', stateLw: 0.4
    };
    if (window.TATRegions && TATRegions.drawBasemapFill) {
      // sub-degree graticule for these small extents (the shared layer steps 30deg)
      g.fillStyle = S.ocean; g.fillRect(0, 0, L.w, L.h);
      this._drawGraticule(g, ext, L.w, L.h);
      // land fill only (skip the layer's ocean so our graticule shows under it)
      if (this.geo && this.geo.countries) {
        g.fillStyle = S.land; g.beginPath();
        TATRegions.drawBasemapFill(g, ext, { countries: this.geo.countries }, L.w, L.h,
          { ocean: 'rgba(0,0,0,0)', land: S.land });
      }
    } else {
      g.fillStyle = S.ocean; g.fillRect(0, 0, L.w, L.h);
    }

    // ---- flight track polyline, colored per ob by wind; thin neutral spine first
    var pts = [];
    for (var i = 0; i < track.length; i++) {
      var la = num(track[i].lat), lo = num(track[i].lon);
      if (la == null || lo == null) { pts.push(null); continue; }
      var p = proj(lo, la);
      var sfmr = num(track[i].sfmr), wspd = num(track[i].wspd);
      var kt = (sfmr != null) ? sfmr : wspd;
      pts.push({ x: p[0], y: p[1], kt: kt, fallback: (sfmr == null), ob: track[i] });
    }
    this._pts = pts;   // cache projected pts for hover (map-local coords)
    // neutral connecting spine (so the pass reads as one continuous line)
    g.strokeStyle = 'rgba(190,205,225,0.32)'; g.lineWidth = 1.1; g.lineJoin = 'round'; g.lineCap = 'round';
    g.beginPath();
    var started = false;
    for (i = 0; i < pts.length; i++) {
      if (!pts[i]) { started = false; continue; }
      if (!started) { g.moveTo(pts[i].x, pts[i].y); started = true; } else g.lineTo(pts[i].x, pts[i].y);
    }
    g.stroke();
    // per-ob dots: SFMR (filled), flight-level fallback (smaller hollow ring)
    for (i = 0; i < pts.length; i++) {
      var pt = pts[i]; if (!pt) continue;
      var col = this._windColor(pt.kt);
      if (pt.fallback) {
        g.strokeStyle = col; g.lineWidth = 1.1;
        g.beginPath(); g.arc(pt.x, pt.y, 1.7, 0, 6.2832); g.stroke();
      } else {
        g.fillStyle = col;
        g.beginPath(); g.arc(pt.x, pt.y, 2.5, 0, 6.2832); g.fill();
      }
    }

    // ---- dropsondes: small diamond
    var sondes = m.sondes || [];
    for (i = 0; i < sondes.length; i++) {
      var sla = num(sondes[i].lat), slo = num(sondes[i].lon);
      if (sla == null || slo == null) continue;
      var sp = proj(slo, sla);
      g.save();
      g.translate(sp[0], sp[1]); g.rotate(Math.PI / 4);
      g.fillStyle = 'rgba(232,235,239,0.9)'; g.strokeStyle = '#1a2436'; g.lineWidth = 1;
      g.fillRect(-3, -3, 6, 6); g.strokeRect(-3, -3, 6, 6);
      g.restore();
    }

    // ---- VDM centers: a cross + ring, labeled with MSLP
    var vdm = m.vdm_centers || [];
    for (i = 0; i < vdm.length; i++) {
      var vla = num(vdm[i].lat), vlo = num(vdm[i].lon);
      if (vla == null || vlo == null) continue;
      var vp = proj(vlo, vla);
      this._drawVdm(g, vp[0], vp[1], vdm[i], L.w, L.h);
    }

    // coast/border lines ON TOP of the data
    if (window.TATRegions && TATRegions.drawBasemapLines) {
      TATRegions.drawBasemapLines(g, ext, this.geo, L.w, L.h, bmStyle);
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

  // simple lat/lon graticule for a small (sub-30deg) extent
  ReconViewer.prototype._drawGraticule = function (g, ext, W, H) {
    var lonSpan = ext[1] - ext[0], latSpan = ext[3] - ext[2];
    var step = lonSpan > 14 ? 5 : (lonSpan > 6 ? 2 : 1);
    g.save();
    g.strokeStyle = 'rgba(255,255,255,0.055)'; g.lineWidth = 0.6;
    g.beginPath();
    var l0 = Math.ceil(ext[0] / step) * step;
    for (var lon = l0; lon <= ext[1]; lon += step) {
      var x = (lon - ext[0]) / lonSpan * W; g.moveTo(x, 0); g.lineTo(x, H);
    }
    var b0 = Math.ceil(ext[2] / step) * step;
    for (var lat = b0; lat <= ext[3]; lat += step) {
      var y = (ext[3] - lat) / latSpan * H; g.moveTo(0, y); g.lineTo(W, y);
    }
    g.stroke();
    g.restore();
  };

  ReconViewer.prototype._drawVdm = function (g, x, y, v, mw, mh) {
    g.save();
    g.lineCap = 'round'; g.lineJoin = 'round';
    // dark casing + bright cross + ring
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
      var txt = Math.round(mslp) + ' hPa';
      g.font = '700 10px ' + FONT; g.textBaseline = 'middle';
      var tw = g.measureText(txt).width, padx = 4, bw = tw + padx * 2, bh = 15;
      var left = (x + 12 + bw <= mw - 2);
      var bx = left ? (x + 12) : (x - 12 - bw), by = y - bh / 2;
      if (by < 2) by = 2; if (by + bh > mh) by = mh - bh;
      roundRectPath(g, bx, by, bw, bh, 3);
      g.fillStyle = 'rgba(7,16,28,0.82)'; g.fill();
      g.strokeStyle = 'rgba(255,255,255,0.5)'; g.lineWidth = 1; g.stroke();
      g.textAlign = 'left'; g.fillStyle = '#ffffff'; g.fillText(txt, bx + padx, by + bh / 2 + 0.5);
    }
    g.restore();
  };

  // discrete SSHWS-band legend, top-left of the map
  ReconViewer.prototype._drawLegend = function (g) {
    var L = this.layout.map;
    var lh = 14, padx = 9, pady = 7;
    var rows = BANDS.length + 2;        // bands + SFMR/FL note + sonde note
    var w = 132, h = pady * 2 + rows * lh;
    var x = L.x + 8, y = L.y + 8;
    g.save();
    g.fillStyle = 'rgba(7,16,28,0.8)'; g.strokeStyle = C.border; g.lineWidth = 1;
    g.fillRect(x, y, w, h); g.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
    g.textBaseline = 'middle'; g.textAlign = 'left';
    for (var i = 0; i < BANDS.length; i++) {
      var cy = y + pady + i * lh + lh / 2;
      var col = this._windColor(BANDS[i].max === Infinity ? 200 : BANDS[i].max);
      g.fillStyle = col; g.beginPath(); g.arc(x + padx + 4, cy, 3.6, 0, 6.2832); g.fill();
      g.fillStyle = C.fg; g.font = '600 10px ' + FONT;
      g.fillText(BANDS[i].lab, x + padx + 14, cy);
    }
    // glyph key
    var ny = y + pady + BANDS.length * lh;
    g.font = '500 9.5px ' + FONT; g.fillStyle = C.muted;
    var m1 = ny + lh / 2;
    g.fillStyle = '#cfd8e6'; g.beginPath(); g.arc(x + padx + 4, m1, 2.6, 0, 6.2832); g.fill();
    g.strokeStyle = '#cfd8e6'; g.lineWidth = 1; g.beginPath(); g.arc(x + padx + 12, m1, 2.0, 0, 6.2832); g.stroke();
    g.fillStyle = C.muted; g.fillText('SFMR · FL fallback', x + padx + 20, m1);
    var m2 = ny + lh + lh / 2;
    g.save(); g.translate(x + padx + 4, m2); g.rotate(Math.PI / 4);
    g.fillStyle = 'rgba(232,235,239,0.9)'; g.fillRect(-2.6, -2.6, 5.2, 5.2); g.restore();
    g.fillStyle = C.muted; g.fillText('Dropsonde', x + padx + 14, m2);
    g.restore();
  };

  ReconViewer.prototype._drawWatermark = function (g, L) {
    g.save();
    g.font = '700 12px ' + FONT; g.textAlign = 'right'; g.textBaseline = 'bottom';
    g.fillStyle = 'rgba(232,235,239,0.42)';
    g.fillText(WATERMARK, L.x + L.w - 9, L.y + L.h - 7);
    g.restore();
  };

  // ---- dual-axis time series: left = p_sfc (inverted), right = wind (SFMR/FL)
  ReconViewer.prototype._drawTimeSeries = function (g) {
    var L = this.layout.ts, m = this.mission, S = this._S();
    var track = m.track || [];
    g.save();
    g.fillStyle = 'rgba(7,16,28,0.55)'; g.fillRect(L.x, L.y, L.w, L.h);
    g.strokeStyle = C.border; g.lineWidth = 1; g.strokeRect(L.x + 0.5, L.y + 0.5, L.w - 1, L.h - 1);

    // plot area, gutters for both axes
    var gl = 44, gr = 40, gt = 16, gb = 22;
    var px = L.x + gl, py = L.y + gt, pw = L.w - gl - gr, ph = L.h - gt - gb;

    // collect time series
    var t0 = null, t1 = null, i, ts;
    var pmin = Infinity, pmax = -Infinity, wmax = -Infinity;
    var rows = [];
    for (i = 0; i < track.length; i++) {
      ts = Date.parse(track[i].t || '');
      if (isNaN(ts)) continue;
      if (t0 == null || ts < t0) t0 = ts;
      if (t1 == null || ts > t1) t1 = ts;
      var ps = num(track[i].p_sfc);
      var sfmr = num(track[i].sfmr), wspd = num(track[i].wspd);
      var w = (sfmr != null) ? sfmr : wspd;
      if (ps != null) { if (ps < pmin) pmin = ps; if (ps > pmax) pmax = ps; }
      if (w != null && w > wmax) wmax = w;
      rows.push({ t: ts, p: ps, w: w, fallback: (sfmr == null) });
    }
    if (t1 == null || t1 <= t0) { g.fillStyle = C.muted; g.font = '600 11px ' + FONT; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText('No time-series data for this pass.', L.x + L.w / 2, L.y + L.h / 2); g.restore(); return; }
    if (!isFinite(pmin)) { pmin = 1000; pmax = 1015; }
    if (pmax <= pmin) pmax = pmin + 5;
    if (!isFinite(wmax) || wmax <= 0) wmax = 40;
    // pad axes a touch
    var pPad = (pmax - pmin) * 0.1 + 0.5; pmin -= pPad; pmax += pPad;
    wmax = Math.ceil((wmax * 1.1) / 10) * 10;

    var tspan = t1 - t0;
    function X(t) { return px + (t - t0) / tspan * pw; }
    // pressure axis is INVERTED so lows read as PEAKS
    function YP(p) { return py + (p - pmin) / (pmax - pmin) * ph; }
    function YW(w) { return py + ph - (w / wmax) * ph; }

    // grid + axis labels
    g.strokeStyle = 'rgba(255,255,255,0.06)'; g.lineWidth = 0.6;
    g.font = '600 9px ' + FONT; g.textBaseline = 'middle';
    for (var k = 0; k <= 4; k++) {
      var yy = py + (ph * k / 4);
      g.beginPath(); g.moveTo(px, yy); g.lineTo(px + pw, yy); g.stroke();
      // left = pressure (inverted: top = pmin)
      var pv = pmin + (pmax - pmin) * (k / 4);
      g.fillStyle = '#9fb3cc'; g.textAlign = 'right';
      g.fillText(Math.round(pv), px - 5, yy);
      // right = wind (top = wmax)
      var wv = wmax * (1 - k / 4);
      g.fillStyle = '#d6b07a'; g.textAlign = 'left';
      g.fillText(Math.round(wv), px + pw + 5, yy);
    }
    // x time ticks
    g.fillStyle = C.muted; g.textAlign = 'center'; g.textBaseline = 'top';
    g.font = '600 9px ' + FONT;
    for (k = 0; k <= 3; k++) {
      var tt = t0 + tspan * (k / 3);
      g.fillText(hhmm(new Date(tt).toISOString()), X(tt), py + ph + 4);
    }
    // axis titles
    g.save();
    g.translate(L.x + 11, py + ph / 2); g.rotate(-Math.PI / 2);
    g.textAlign = 'center'; g.textBaseline = 'middle'; g.font = '700 9px ' + FONT;
    g.fillStyle = '#9fb3cc'; g.fillText('Psfc hPa (inv)', 0, 0);
    g.restore();
    g.save();
    g.translate(L.x + L.w - 9, py + ph / 2); g.rotate(Math.PI / 2);
    g.textAlign = 'center'; g.textBaseline = 'middle'; g.font = '700 9px ' + FONT;
    g.fillStyle = '#d6b07a'; g.fillText('Wind kt', 0, 0);
    g.restore();

    // wind series first (under), colored per-segment by SSHWS band; dots for FL fallback
    g.lineJoin = 'round'; g.lineCap = 'round'; g.lineWidth = 1.8;
    var prev = null;
    for (i = 0; i < rows.length; i++) {
      var r = rows[i];
      if (r.w == null) { prev = null; continue; }
      var cx = X(r.t), cyw = YW(r.w);
      if (prev) {
        g.strokeStyle = this._windColor(r.w);
        g.beginPath(); g.moveTo(prev[0], prev[1]); g.lineTo(cx, cyw); g.stroke();
      }
      prev = [cx, cyw];
    }
    // pressure series on top: clean neutral line (inverted)
    g.strokeStyle = '#cfe0f5'; g.lineWidth = 1.8;
    prev = null;
    for (i = 0; i < rows.length; i++) {
      if (rows[i].p == null) { prev = null; continue; }
      var cxp = X(rows[i].t), cyp = YP(rows[i].p);
      if (prev) { g.beginPath(); g.moveTo(prev[0], prev[1]); g.lineTo(cxp, cyp); g.stroke(); }
      prev = [cxp, cyp];
    }
    // tiny inline legend for the two traces
    g.font = '600 9.5px ' + FONT; g.textBaseline = 'middle'; g.textAlign = 'left';
    var lx = px + 6, ly = py + 8;
    g.strokeStyle = '#cfe0f5'; g.lineWidth = 2; g.beginPath(); g.moveTo(lx, ly); g.lineTo(lx + 12, ly); g.stroke();
    g.fillStyle = '#9fb3cc'; g.fillText('Surface pressure', lx + 16, ly);
    var lx2 = lx + 16 + g.measureText('Surface pressure').width + 14;
    g.strokeStyle = '#d6b07a'; g.beginPath(); g.moveTo(lx2, ly); g.lineTo(lx2 + 12, ly); g.stroke();
    g.fillStyle = '#d6b07a'; g.fillText('Wind (SFMR / FL)', lx2 + 16, ly);
    g.restore();
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
    var sx = this.dom.canvas.width / rect.width / this.dpr;   // css px per client px
    var mx = (ev.clientX - rect.left) * sx - L.x;
    var my = (ev.clientY - rect.top) * sx - L.y;
    if (mx < 0 || my < 0 || mx > L.w || my > L.h) { tip.style.display = 'none'; return; }
    var best = null, bestD = 10 * 10;
    for (var i = 0; i < this._pts.length; i++) {
      var p = this._pts[i]; if (!p) continue;
      var dx = p.x - mx, dy = p.y - my, d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = p; }
    }
    if (!best) { tip.style.display = 'none'; return; }
    var ob = best.ob;
    var sfmr = num(ob.sfmr), wspd = num(ob.wspd), ps = num(ob.p_sfc), pk = num(ob.pkwnd);
    var lines = [];
    lines.push(hhmm(ob.t));
    if (sfmr != null) lines.push('SFMR ' + Math.round(sfmr) + ' kt');
    if (wspd != null) lines.push('FL wind ' + Math.round(wspd) + ' kt');
    if (pk != null) lines.push('Peak FL ' + Math.round(pk) + ' kt');
    if (ps != null) lines.push('Psfc ' + Math.round(ps) + ' hPa');
    tip.style.display = 'block';
    tip.style.left = (ev.clientX - rect.left + 12) + 'px';
    tip.style.top = (ev.clientY - rect.top + 12) + 'px';
    tip.innerHTML = lines.join('<br>');
  };

  // ====================================================================
  // Shareable export: Download PNG / Copy (the full figure incl. header,
  // legend, watermark) via canvas.toBlob.
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

  // ---- style picker ----
  ReconViewer.prototype._setStyle = function (key) {
    this.style = STYLES[key] ? key : 'muted';
    try { localStorage.setItem(LS_STYLE, this.style); } catch (e) {}
    if (this.mission) this._draw();
  };

  // ---- poll: refresh manifest + current.json (60s) ----
  ReconViewer.prototype._schedulePoll = function () {
    clearTimeout(this._pollTimer);
    var self = this;
    this._pollTimer = setTimeout(function () { self._poll(); }, POLL_MS);
  };

  ReconViewer.prototype._poll = function () {
    var self = this;
    this._fetchJson('/manifest.json', true)
      .then(function (m) {
        self.manifest = m;
        // refresh the current-tab Plan of the Day + spotlight (cheap, live files)
        if (self.tab === 'current' && !self.stormLock) self._loadCurrentView();
      })
      .catch(function () {})
      .then(function () { self._schedulePoll(); });
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
      // populate style options
      this.dom.styleSel.innerHTML = '';
      for (var key in STYLES) if (STYLES.hasOwnProperty(key)) {
        var o = document.createElement('option');
        o.value = key; o.textContent = STYLES[key].label;
        if (key === this.style) o.selected = true;
        this.dom.styleSel.appendChild(o);
      }
      this.dom.styleSel.addEventListener('change', function () { self._setStyle(this.value); });
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
      if (r) new ReconViewer(r);
    });
  }
  if (typeof window !== 'undefined') window.ReconViewer = ReconViewer;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { ReconViewer: ReconViewer, STYLES: STYLES };
  }
})();
