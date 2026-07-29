/*
 * season_animation.js — Triple-A-Tropics
 * -------------------------------------------------------------
 * Canvas-based in-browser animation of a basin's storm tracks +
 * ACE accumulation, for any year. Mirrors the pre-rendered GIFs
 * in visual style but is interactive (play/pause/scrub) and
 * covers every season the site has JSON for.
 *
 * Container contract:
 *   <div class="season-animator"
 *        data-basin="wp"              // wp | al | ep
 *        data-current-year="2026">    // for mode switching
 *     ... markup rendered by this script ...
 *   </div>
 *
 * Data it fetches:
 *   /historical/{basin}/years.json                 list of available years
 *   /historical/{basin}/tracks/tracks_{YYYY}.json  per-year tracks
 *   {CDN}/feeds/{basin}_tracks_data.json            current-year tracks (R2)
 *   {CDN}/feeds/{basin}_ace_data.json               ACE climo + all_years (R2)
 *   /ne_50m_admin_0_countries.geojson               land polygons (cached)
 *   /ne_50m_coastline.geojson                       coastlines (cached)
 */
(function () {
  'use strict';

  // Live current-year ACE + tracks feeds moved to R2
  // (cdn.triple-a-tropics.com/feeds/) so the streaming poller can
  // refresh them without a git commit per update. Only the LIVE
  // current-year docs move; historical per-year tracks
  // (/historical/{basin}/tracks/tracks_{year}.json) stay on origin.
  // Live fetches below pass cache:'no-cache' so a poller update shows
  // up promptly instead of a stale CDN/browser copy.
  const LIVE_FEEDS = 'https://cdn.triple-a-tropics.com/feeds/';

  // --- palette (matches styles.css / GIFs) ------------------------
  const PAL = {
    bg: '#131519', panel: '#1b1e24', border: '#2a2e36',
    fg: '#e8ebef', muted: '#9199a4',
    amber: '#ffb83a', cyan: '#5dd3ff', violet: '#c084fc',
    // Light-gray landmass fill with a brighter coast outline, so the
    // continents read clearly against the dark-navy ocean backdrop
    // without competing visually with the storm tracks.
    land: '#6b7280', coast: '#9ca3af',
  };
  // Canonical SSHWS palette (tat_palette.js, generated from
  // palette/tat_palettes/categories.py). This file used to carry its own
  // coral/rose ramp (C3 #ff6b4d, C4 #e53f71) — the same storm was a different
  // color here than on the tracks map it sits under. No local fallback.
  function P() {
    const p = window.TATPalette;
    if (!p) throw new Error('season_animation.js: load /tat_palette.js first');
    return p;
  }
  function catColor(w) { return P().colorForKt(w); }

  // --- basin config (mirrors generate_season_gif.py) ---------------
  const BASIN_CFG = {
    wp: {
      fullName: 'Western North Pacific',
      extent: [100, 195, -2, 50],
      xticks: [[110, 130, 150, 170, 190], ['110°E','130°E','150°E','170°E','170°W']],
      lonConvention: '0-360',
      needsDatelineWrap: true,
    },
    al: {
      fullName: 'North Atlantic',
      extent: [-100, -5, 5, 50],
      xticks: [[-90,-70,-50,-30,-10], ['90°W','70°W','50°W','30°W','10°W']],
      lonConvention: '-180-180',
      needsDatelineWrap: false,
    },
    ep: {
      fullName: 'Northeast Pacific',
      extent: [-180, -80, 0, 35],
      xticks: [[-170,-150,-130,-110,-90], ['170°W','150°W','130°W','110°W','90°W']],
      lonConvention: '-180-180',
      needsDatelineWrap: false,
    },
    // Global mode (data-basin="global"): combined NA+EP+WP storms on a
    // Pacific-centered Mercator-style canvas (Africa LEFT, Pacific
    // MIDDLE, Americas RIGHT). _loadYearData() forks here to fetch all
    // three per-basin tracks_data.json + ace_data.json, then sums them.
    // Historical multi-basin replay isn't supported yet — only the
    // current year — because each year's per-basin tracks_*.json doesn't
    // always exist for older years.
    global: {
      fullName: 'Global',
      extent: [-25, 335, -60, 60],
      xticks: [[0, 60, 120, 180, 240, 300],
               ['0°', '60°E', '120°E', '180°', '120°W', '60°W']],
      lonConvention: 'global-pacific',
      needsDatelineWrap: true,
    },
  };

  function normalizeLon(lon, conv) {
    if (conv === '0-360') return lon < 0 ? lon + 360 : lon;
    if (conv === 'global-pacific') {
      // Visible window is [-25, 335]; longitudes west of -25° (the Atlantic
      // off Africa) get +360 so they fall on the right edge instead of
      // off-canvas to the left.
      return lon < -25 ? lon + 360 : lon;
    }
    return lon > 180 ? lon - 360 : lon;
  }

  // Sum two or more per-basin _ace_data.json documents into a single
  // global-equivalent doc with summed climo bands, summed all_years,
  // and a doy-aligned current curve. Output schema matches the per-basin
  // schema closely enough that the existing _drawAce code path works.
  function sumAces(aces) {
    if (!aces.length) return null;
    const ref = aces.reduce((a, b) =>
      (a.doy && a.doy.length >= ((b.doy && b.doy.length) || 0)) ? a : b);
    const climoKeys = ['min', 'p10', 'p25', 'mean', 'p75', 'p90', 'max'];
    const out = {
      doy: ref.doy.slice(),
      today_doy: Math.max.apply(null, aces.map(a => a.today_doy || 0)),
      climo: {},
      current: {
        label: (ref.current && ref.current.label) || '',
        doy: [],
        values: [],
        latest_value: 0,
      },
      prior_year: null,
      all_years: {},
      // Rank against summed history — _buildSeasonState recomputes from
      // all_years so we don't need to pre-compute current_rank here.
      current_rank: null,
      total_seasons: 0,
      rankings: [],
      storms_by_year: {},
    };
    for (const k of climoKeys) {
      if (!ref.climo || !ref.climo[k]) continue;
      out.climo[k] = ref.doy.map((_, i) =>
        aces.reduce((s, a) =>
          s + ((a.climo && a.climo[k] && a.climo[k][i]) || 0), 0));
    }
    // Current-year curve: align by doy across the three feeds.
    const curMap = {};
    for (const a of aces) {
      const cd = (a.current && a.current.doy) || [];
      const cv = (a.current && a.current.values) || [];
      for (let i = 0; i < cd.length; i++) {
        const d = cd[i];
        curMap[d] = (curMap[d] || 0) + (cv[i] || 0);
      }
    }
    const sortedDoys = Object.keys(curMap).map(Number).sort((a, b) => a - b);
    out.current.doy = sortedDoys;
    out.current.values = sortedDoys.map(d => curMap[d]);
    out.current.latest_value = out.current.values.length ?
      out.current.values[out.current.values.length - 1] : 0;
    // all_years: union of years; sum element-wise to the longest array.
    const yearKeys = new Set();
    aces.forEach(a => Object.keys(a.all_years || {}).forEach(y => yearKeys.add(y)));
    for (const y of yearKeys) {
      const vals = aces.map(a => (a.all_years || {})[y] || []);
      const maxLen = Math.max.apply(null, vals.map(v => v.length).concat([0]));
      const sum = [];
      for (let i = 0; i < maxLen; i++) {
        sum.push(vals.reduce((s, v) => s + (v[i] || 0), 0));
      }
      out.all_years[y] = sum;
    }
    return out;
  }

  function clearCache() { /* no-op, reserved for future */ }

  // --- module-level cache for geo (shared across all instances) ----
  let _landPromise = null;
  let _coastPromise = null;
  function fetchLand() {
    if (!_landPromise) {
      _landPromise = fetch('/ne_50m_admin_0_countries.geojson')
        .then(r => r.json()).then(gj => {
          const polys = [];
          gj.features.forEach(f => {
            if (f.geometry.type === 'Polygon') polys.push(f.geometry.coordinates[0]);
            else if (f.geometry.type === 'MultiPolygon')
              f.geometry.coordinates.forEach(c => polys.push(c[0]));
          });
          return polys;
        });
    }
    return _landPromise;
  }
  function fetchCoast() {
    if (!_coastPromise) {
      _coastPromise = fetch('/ne_50m_coastline.geojson')
        .then(r => r.json()).then(gj => {
          const paths = [];
          gj.features.forEach(f => {
            if (f.geometry.type === 'LineString') paths.push(f.geometry.coordinates);
            else if (f.geometry.type === 'MultiLineString')
              f.geometry.coordinates.forEach(ls => paths.push(ls));
          });
          return paths;
        });
    }
    return _coastPromise;
  }

  // --- per-instance state + methods --------------------------------
  class Animator {
    constructor(root) {
      this.root = root;
      this.basin = root.dataset.basin;
      this.currentYear = parseInt(root.dataset.currentYear, 10) ||
                         new Date().getFullYear();
      this.cfg = BASIN_CFG[this.basin];
      if (!this.cfg) {
        console.error('season-animator: unknown basin', this.basin);
        return;
      }
      this.year = this.currentYear;
      this.tracks = null;
      this.ace = null;
      this.land = null;
      this.coast = null;
      this.tracksByYear = new Map();    // year → tracks doc
      this._gifProbeCache = new Map();  // `${basin}:${year}` → bool (HEAD result)
      this._gifProbeInflight = new Map();
      this.playing = false;
      this.rafId = 0;
      this.frac = 0;                    // 0..1 through season
      this.fps = 15;
      this.durationS = 15;
      this.speed = 1.0;                 // playback multiplier (0.25..4)

      this._render();
      this._wire();
      this._loadYearList();
      this._loadBasemapAndData();
    }

    // Build the DOM
    _render() {
      this.root.innerHTML = `
        <div class="anim-head">
          <div class="anim-title">
            <span class="anim-basin">${this.cfg.fullName}</span>
            <span class="anim-year" id="animYearLabel">${this.year}</span>
          </div>
          <div class="anim-controls">
            <label class="anim-ctrl">
              <span>Season</span>
              <select id="animYearSelect" class="anim-select"></select>
            </label>
            <button id="animPlay" class="anim-btn" type="button">▶ Play</button>
            <div class="anim-speed" role="group" aria-label="Playback speed">
              <button type="button" class="anim-speed-btn" data-speed="0.25">0.25×</button>
              <button type="button" class="anim-speed-btn" data-speed="0.5">0.5×</button>
              <button type="button" class="anim-speed-btn anim-speed-active" data-speed="1">1×</button>
              <button type="button" class="anim-speed-btn" data-speed="2">2×</button>
              <button type="button" class="anim-speed-btn" data-speed="4">4×</button>
            </div>
            <a id="animDownload" class="anim-btn anim-btn-ghost"
               download rel="noopener">Download GIF</a>
          </div>
        </div>
        <canvas id="animCanvas" class="anim-canvas" width="1080" height="1080"
                aria-label="${this.cfg.fullName} season animation"></canvas>
        <div class="anim-scrub-wrap">
          <input id="animScrub" type="range" min="0" max="1000" value="0"
                 class="anim-scrub" aria-label="Scrub through season">
          <div id="animDateLabel" class="anim-date">-</div>
        </div>
        <div id="animStatus" class="anim-status"></div>
      `;
      this.canvas = this.root.querySelector('#animCanvas');
      this.ctx    = this.canvas.getContext('2d');
      // HD rendering: scale backing store to the device's pixel ratio so
      // tracks, coastlines, and text render crisply on retina displays.
      // All draw code uses logical 1080×1080 coords; the ctx.scale call
      // maps those to the larger backing store. Capped at 2 to keep the
      // per-frame allocation bounded.
      this._dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.canvas.width  = 1080 * this._dpr;
      this.canvas.height = 1080 * this._dpr;
      this.ctx.scale(this._dpr, this._dpr);
      this.ctx.imageSmoothingEnabled = true;
      this.select = this.root.querySelector('#animYearSelect');
      this.playBtn = this.root.querySelector('#animPlay');
      this.scrub  = this.root.querySelector('#animScrub');
      this.dateLabel = this.root.querySelector('#animDateLabel');
      this.yearLabel = this.root.querySelector('#animYearLabel');
      this.download = this.root.querySelector('#animDownload');
      this.status = this.root.querySelector('#animStatus');
      this._setDownloadHref();
    }

    _setDownloadHref() {
      const slug = {wp: 'wpac', al: 'atl', ep: 'epac'}[this.basin];
      this.download.href = this._gifUrl(this.year);
      this.download.download = `${slug}_${this.year}_season.gif`;
    }

    _gifUrl(year) {
      // Current-year GIFs live at root as {slug}_{year}_season.gif;
      // historical GIFs under historical/{basin}/gifs/{basin}_{year}_season.gif.
      // The two naming conventions are intentional — see generate_season_gif.py.
      const slug = {wp: 'wpac', al: 'atl', ep: 'epac'}[this.basin];
      return year === this.currentYear
        ? `https://cdn.triple-a-tropics.com/${slug}_${year}_season.gif`
        : `https://cdn.triple-a-tropics.com/historical/${this.basin}/gifs/${this.basin}_${year}_season.gif`;
    }

    // years.json advertises every year we have tracks for, but only a few
    // pre-rendered GIFs are committed. HEAD-probe the URL so the play +
    // download buttons aren't enabled for years with no committed file.
    async _probeGifAvailability(year) {
      const key = `${this.basin}:${year}`;
      if (this._gifProbeCache.has(key)) return this._gifProbeCache.get(key);
      if (this._gifProbeInflight.has(key)) return this._gifProbeInflight.get(key);
      const p = (async () => {
        try {
          const r = await fetch(this._gifUrl(year), { method: 'HEAD' });
          return r.ok;
        } catch (e) {
          return false;
        }
      })().then((ok) => {
        this._gifProbeCache.set(key, ok);
        this._gifProbeInflight.delete(key);
        return ok;
      });
      this._gifProbeInflight.set(key, p);
      return p;
    }

    _applyGifAvailability(year, ok) {
      // Stale probe — user moved on before HEAD resolved. Play stays
      // enabled regardless: it drives the canvas animation, which renders
      // from tracks JSON and doesn't depend on the committed GIF.
      if (this.year !== year) return;
      if (ok) {
        this.download.removeAttribute('aria-disabled');
        this.download.style.opacity = '';
        this.download.style.pointerEvents = '';
        this._setDownloadHref();
        // Don't clobber other status messages (e.g. load error).
        if (this.status.textContent === 'Animation not yet generated for this year.') {
          this._setStatus('');
        }
      } else {
        this.download.setAttribute('aria-disabled', 'true');
        this.download.removeAttribute('href');
        this.download.style.opacity = '0.45';
        this.download.style.pointerEvents = 'none';
        this._setStatus('Animation not yet generated for this year.');
      }
    }

    async _refreshGifAvailability() {
      const year = this.year;
      const ok = await this._probeGifAvailability(year);
      this._applyGifAvailability(year, ok);
    }

    _wire() {
      this.playBtn.addEventListener('click', () => {
        this.playing ? this.pause() : this.play();
      });
      this.scrub.addEventListener('input', (e) => {
        this.frac = parseInt(e.target.value, 10) / 1000;
        this.pause();
        this.redraw();
      });
      this.select.addEventListener('change', (e) => {
        this.changeYear(parseInt(e.target.value, 10));
      });
      // Speed pill — click any button to change the playback multiplier.
      // Applied in the tick loop so both running and paused states pick it up.
      const speedBtns = this.root.querySelectorAll('.anim-speed-btn');
      speedBtns.forEach(btn => {
        btn.addEventListener('click', () => {
          this.speed = parseFloat(btn.dataset.speed) || 1;
          speedBtns.forEach(b =>
            b.classList.toggle('anim-speed-active', b === btn));
        });
      });
    }

    async _loadYearList() {
      // Global mode: only the current year is supported (historical
      // replay would need each year's per-basin tracks_*.json to all
      // exist, which isn't always the case for older years).
      if (this.basin === 'global') {
        this.select.innerHTML =
          `<option value="${this.year}" selected>${this.year}</option>`;
        return;
      }
      try {
        const url = `/historical/${this.basin}/years.json`;
        const j = await (await fetch(url)).json();
        const years = j.years.slice().sort((a, b) => b - a);
        // Ensure the current year is at the top even if not in years.json yet.
        if (!years.includes(this.currentYear)) years.unshift(this.currentYear);
        this.select.innerHTML = years.map(y =>
          `<option value="${y}"${y === this.year ? ' selected' : ''}>${y}</option>`
        ).join('');
      } catch (e) {
        console.warn('years.json fetch failed', e);
        this.select.innerHTML = `<option value="${this.year}">${this.year}</option>`;
      }
    }

    async _loadBasemapAndData() {
      this._setStatus('Loading…');
      try {
        const [land, coast] = await Promise.all([fetchLand(), fetchCoast()]);
        this.land = land;
        this.coast = coast;
        await this._loadYearData(this.year);
        this._setStatus('');
        this.redraw();
        this._refreshGifAvailability();
      } catch (e) {
        this._setStatus('Failed to load data.');
        console.error(e);
      }
    }

    async _loadYearData(year) {
      if (this.basin === 'global') {
        if (this.tracksByYear.has(year)) {
          this.tracks = this.tracksByYear.get(year);
        } else if (year === this.currentYear) {
          // Combine current-year tracks across NA/EP/WP into one doc.
          const subBasins = ['al', 'ep', 'wp'];
          const docs = await Promise.all(subBasins.map(b =>
            fetch(`${LIVE_FEEDS}${b}_tracks_data.json`, { cache: 'no-cache' })
              .then(r => r.ok ? r.json() : null)
              .catch(() => null)
          ));
          const combined = {
            basin: 'global',
            basin_name: 'Global',
            year,
            updated: '',
            header: { named: 0, cat1plus: 0, cat3plus: 0, cat5: 0, total_ace: 0 },
            storms: [],
          };
          let latestUpdated = '';
          for (let i = 0; i < docs.length; i++) {
            const d = docs[i];
            if (!d) continue;
            for (const s of (d.storms || [])) {
              s.basin = d.basin || subBasins[i];
              combined.storms.push(s);
            }
            if (d.header) {
              for (const k of ['named', 'cat1plus', 'cat3plus', 'cat5', 'total_ace']) {
                combined.header[k] += d.header[k] || 0;
              }
            }
            if (d.updated && d.updated > latestUpdated) latestUpdated = d.updated;
          }
          combined.updated = latestUpdated;
          this._prepareTracks(combined);
          this.tracksByYear.set(year, combined);
          this.tracks = combined;
        } else {
          throw new Error('global mode: only the current year is supported');
        }
        if (!this.ace) {
          const subBasins = ['al', 'ep', 'wp'];
          const aceDocs = await Promise.all(subBasins.map(b =>
            fetch(`${LIVE_FEEDS}${b}_ace_data.json`, { cache: 'no-cache' })
              .then(r => r.ok ? r.json() : null)
              .catch(() => null)
          ));
          const validAces = aceDocs.filter(Boolean);
          if (!validAces.length) throw new Error('no ACE feeds available');
          this.ace = sumAces(validAces);
        }
        this._buildSeasonState();
        return;
      }

      if (this.tracksByYear.has(year)) {
        this.tracks = this.tracksByYear.get(year);
      } else {
        // Live current-year tracks come from R2 (cache:'no-cache');
        // immutable historical years stay on origin with default caching.
        const isLive = (year === this.currentYear);
        const tracksUrl = isLive
          ? `${LIVE_FEEDS}${this.basin}_tracks_data.json`
          : `/historical/${this.basin}/tracks/tracks_${year}.json`;
        const res = await fetch(tracksUrl, isLive ? { cache: 'no-cache' } : undefined);
        if (!res.ok) throw new Error(`tracks fetch ${res.status}`);
        const doc = await res.json();
        this._prepareTracks(doc);
        this.tracksByYear.set(year, doc);
        this.tracks = doc;
      }
      if (!this.ace) {
        const aceRes = await fetch(`${LIVE_FEEDS}${this.basin}_ace_data.json`, { cache: 'no-cache' });
        this.ace = await aceRes.json();
      }
      // Compute timeline bounds and season ACE curve for this year.
      this._buildSeasonState();
    }

    _prepareTracks(doc) {
      const conv = this.cfg.lonConvention;
      (doc.storms || []).forEach(s => {
        s.points.forEach(p => {
          p._t = Date.parse(p.t + 'Z');   // treat stored time as UTC
          p._lon = normalizeLon(p.lon, conv);
        });
        s._times = s.points.map(p => p._t);
        s._tMin = s._times[0];
        s._tMax = s._times[s._times.length - 1];
      });
    }

    _buildSeasonState() {
      const year = this.year;
      // Timeline
      if (year === this.currentYear) {
        const storms = this.tracks.storms || [];
        const all = [].concat(...storms.map(s => s._times));
        this.tStart = Date.UTC(year, 0, 1);
        this.tEnd   = all.length ? Math.max.apply(null, all) :
                                   Date.UTC(year, 3, 1);
      } else {
        this.tStart = Date.UTC(year, 0, 1);
        this.tEnd   = Date.UTC(year, 11, 31, 18);
      }
      // ACE curve for this year.
      const all = this.ace.all_years || {};
      this.priorYearValues = all[String(year - 1)] || null;
      if (year === this.currentYear) {
        const cur = this.ace.current || {values: [], doy: []};
        const today = this.ace.today_doy || 365;
        this.curveDoy = [];
        this.curveVal = [];
        let last = 0;
        for (let d = 1; d <= today; d++) {
          this.curveDoy.push(d);
          const idx = cur.doy.indexOf(d);
          const v = idx >= 0 ? cur.values[idx] : last;
          this.curveVal.push(v);
          last = v;
        }
      } else {
        const arr = all[String(year)] || [];
        this.curveVal = arr.slice();
        this.curveDoy = arr.map((_, i) => i + 1);
      }
      this.seasonFinal = this.curveVal.length
        ? this.curveVal[this.curveVal.length - 1] : 0;
      // Rank: for a completed historical season, compare final ACE.
      // For the current (in-progress) season, compare where we are
      // *today* against every historical season's value on the same
      // day-of-year — i.e. "on pace to rank" rather than "final rank."
      if (year === this.currentYear) {
        const today = this.ace.today_doy || this.curveVal.length;
        const cur = this.seasonFinal;
        let better = 0, total = 0;
        for (const [y, v] of Object.entries(all)) {
          if (parseInt(y, 10) === year) continue;      // skip self
          total++;
          const idx = Math.min(today, v.length) - 1;
          const paceVal = idx >= 0 ? v[idx] : 0;
          if (paceVal > cur) better++;
        }
        this.rank = better + 1;
        this.totalSeasons = total + 1;
        this.rankMode = 'pace';
      } else {
        const finals = Object.entries(all).map(([y, v]) =>
          [parseInt(y, 10), v.length ? v[v.length - 1] : 0]);
        finals.sort((a, b) => b[1] - a[1]);
        this.rank = finals.findIndex(([y]) => y === year) + 1;
        this.totalSeasons = finals.length;
        this.rankMode = 'final';
      }
      // Max-y for ACE panel (climo p90 × 1.1, or season final × 1.2)
      const p90 = this.ace.climo.p90;
      const climoMax = Math.max.apply(null, p90);
      this.aceMaxY = Math.max(climoMax, this.seasonFinal * 1.2) * 1.08;
      this.frac = 0;
      this.scrub.value = 0;
      this._updateYearLabel();
    }

    _updateYearLabel() {
      this.yearLabel.textContent = this.year;
    }

    async changeYear(year) {
      this.pause();
      this.year = year;
      this._setDownloadHref();
      this._setStatus('Loading ' + year + '…');
      try {
        await this._loadYearData(year);
        this._setStatus('');
        this.redraw();
        this._refreshGifAvailability();
      } catch (e) {
        this._setStatus('Load failed: ' + e.message);
      }
    }

    _setStatus(msg) { this.status.textContent = msg || ''; }

    play() {
      if (this.playing) return;
      this.playing = true;
      this.playBtn.textContent = '⏸ Pause';
      this._lastT = performance.now();
      const tick = (now) => {
        if (!this.playing) return;
        const dt = (now - this._lastT) / 1000;
        this._lastT = now;
        // Multiply by the user-selected speed so the full season still
        // covers `durationS` seconds at 1×, but 4× runs it in ~3.75s etc.
        this.frac += (dt * this.speed) / this.durationS;
        if (this.frac >= 1) this.frac = 0;   // loop
        this.scrub.value = Math.floor(this.frac * 1000);
        this.redraw();
        this.rafId = requestAnimationFrame(tick);
      };
      this.rafId = requestAnimationFrame(tick);
    }
    pause() {
      if (!this.playing) return;
      this.playing = false;
      this.playBtn.textContent = '▶ Play';
      cancelAnimationFrame(this.rafId);
    }

    // --- drawing -------------------------------------------------------
    redraw() {
      if (!this.tracks || !this.ace) return;
      const ctx = this.ctx;
      // Logical coordinate system is always 1080×1080 regardless of dpr;
      // the ctx has already been scaled in _render().
      const W = 1080, H = 1080;
      ctx.fillStyle = PAL.bg;
      ctx.fillRect(0, 0, W, H);

      // Budget: 96 title + 8 pad + 540 map + 32 lon-labels + 14 pad
      //         + 340 ACE + 32 month-labels + 18 bottom-pad = 1080
      const leftX  = 64;                 // room for y-axis labels
      const rightX = W - 20;
      const panelW = rightX - leftX;
      const titleH = 96;
      const mapY   = titleH + 8;
      const mapH   = 540;
      const aceY   = mapY + mapH + 32 + 14;
      const aceH   = 340;

      this._drawTitle(ctx, leftX, 0, panelW, titleH);
      this._drawMap(ctx, leftX, mapY, panelW, mapH);
      this._drawAce(ctx, leftX, aceY, panelW, aceH);
      this._drawDateLabel();
    }

    _drawTitle(ctx, x, y, w, h) {
      ctx.fillStyle = PAL.fg;
      ctx.font = '900 36px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
      ctx.textBaseline = 'middle';
      ctx.textAlign = 'left';
      ctx.fillText(`${this.cfg.fullName} · ${this.year}`, x, y + h * 0.42);
      ctx.fillStyle = PAL.muted;
      ctx.font = '12px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
      const sub = this.year === this.currentYear ? 'Season to date' : 'Full season';
      ctx.fillText(sub, x, y + h * 0.80);
      // Right stats.
      const named = (this.tracks.header && this.tracks.header.named) || 0;
      ctx.textAlign = 'right';
      ctx.fillStyle = PAL.cyan;
      ctx.font = '700 22px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
      ctx.fillText(`${named} named`, x + w, y + h * 0.42);
      ctx.fillStyle = PAL.muted;
      ctx.font = '14px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
      const rankLbl = this.rankMode === 'pace' ? 'Pace' : 'Rank';
      const rankStr = this.rank
        ? `   ${rankLbl} ${this.rank}/${this.totalSeasons}` : '';
      ctx.fillText(`ACE ${this.seasonFinal.toFixed(1)}${rankStr}`,
                   x + w, y + h * 0.80);
    }

    _drawMap(ctx, x, y, w, h) {
      const [lonMin, lonMax, latMin, latMax] = this.cfg.extent;
      const lonSpan = lonMax - lonMin;
      const latSpan = latMax - latMin;
      const p2x = lon => x + (lon - lonMin) / lonSpan * w;
      const p2y = lat => y + h - (lat - latMin) / latSpan * h;
      this._p2x = p2x; this._p2y = p2y;
      this._mapBounds = {x, y, w, h};

      // Background + border.
      ctx.fillStyle = PAL.panel;
      ctx.fillRect(x, y, w, h);
      ctx.strokeStyle = PAL.border;
      ctx.lineWidth = 1;
      ctx.strokeRect(x, y, w, h);

      // Clip to map area for land / coast / tracks.
      ctx.save();
      ctx.beginPath(); ctx.rect(x, y, w, h); ctx.clip();

      // Lat/lon grid.
      ctx.strokeStyle = PAL.border; ctx.lineWidth = 0.5;
      ctx.setLineDash([4, 5]);
      for (let lat = Math.ceil(latMin / 10) * 10; lat <= latMax; lat += 10) {
        ctx.beginPath();
        ctx.moveTo(x, p2y(lat)); ctx.lineTo(x + w, p2y(lat));
        ctx.stroke();
      }
      for (let lon = Math.ceil(lonMin / 20) * 20; lon <= lonMax; lon += 20) {
        ctx.beginPath();
        ctx.moveTo(p2x(lon), y); ctx.lineTo(p2x(lon), y + h);
        ctx.stroke();
      }
      ctx.setLineDash([]);

      // Land polygons (with dateline wrap for WP).
      const offsets = this.cfg.needsDatelineWrap ? [0, 360, -360] : [0];
      ctx.fillStyle = PAL.land;
      for (const poly of this.land) {
        for (const off of offsets) {
          let any = false;
          ctx.beginPath();
          for (let i = 0; i < poly.length; i++) {
            const lo = poly[i][0] + off;
            const la = poly[i][1];
            if (lo < lonMin - 5 || lo > lonMax + 5) continue;
            if (la < latMin - 5 || la > latMax + 5) continue;
            const cx = p2x(lo), cy = p2y(la);
            if (!any) { ctx.moveTo(cx, cy); any = true; }
            else       { ctx.lineTo(cx, cy); }
          }
          if (any) { ctx.closePath(); ctx.fill(); }
        }
      }

      // Coastlines (lighter lines).
      ctx.strokeStyle = PAL.coast;
      ctx.lineWidth = 0.6;
      for (const ls of this.coast) {
        for (const off of offsets) {
          let first = true;
          ctx.beginPath();
          for (let i = 0; i < ls.length; i++) {
            const lo = ls[i][0] + off;
            const la = ls[i][1];
            if (lo < lonMin - 5 || lo > lonMax + 5) { first = true; continue; }
            if (la < latMin - 5 || la > latMax + 5) { first = true; continue; }
            const cx = p2x(lo), cy = p2y(la);
            if (first) { ctx.moveTo(cx, cy); first = false; }
            else        { ctx.lineTo(cx, cy); }
          }
          ctx.stroke();
        }
      }

      // Storm tracks up to cutoff time.
      const tNow = this.tStart + this.frac * (this.tEnd - this.tStart);
      const storms = this.tracks.storms || [];
      ctx.lineWidth = 2.6;
      ctx.lineCap = 'round';
      for (const s of storms) {
        const pts = s.points;
        let prev = null, prevPos = null;
        for (let i = 0; i < pts.length; i++) {
          if (pts[i]._t > tNow) break;
          const lo = pts[i]._lon, la = pts[i].lat;
          const cx = p2x(lo), cy = p2y(la);
          if (prev !== null) {
            // Skip dateline-jump segments.
            if (Math.abs(lo - prev._lon) < 180) {
              ctx.strokeStyle = catColor(pts[i].wind_kt);
              ctx.beginPath();
              ctx.moveTo(prevPos[0], prevPos[1]);
              ctx.lineTo(cx, cy);
              ctx.stroke();
            }
          }
          prev = pts[i]; prevPos = [cx, cy];
        }
      }

      // Active storms — marker + name tag.
      const actives = [];
      for (const s of storms) {
        if (s._tMin > tNow || tNow > s._tMax + 6 * 3600 * 1000) continue;
        // Find last point ≤ tNow.
        let idx = -1;
        for (let i = 0; i < s.points.length; i++) {
          if (s.points[i]._t <= tNow) idx = i;
          else break;
        }
        if (idx < 0) continue;
        const p = s.points[idx];
        actives.push({s, p, idx});
      }
      actives.sort((a, b) =>
        ((b.p.wind_kt || 0) - (a.p.wind_kt || 0)));
      ctx.textBaseline = 'middle';
      actives.forEach((a, i) => {
        const cx = p2x(a.p._lon), cy = p2y(a.p.lat);
        const wKt = a.p.wind_kt || 30;
        const color = catColor(wKt);
        // Small dot colored by current intensity, with a thin white
        // halo so it reads against the storm-track line underneath.
        ctx.fillStyle = color;
        ctx.beginPath(); ctx.arc(cx, cy, 5.5, 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = 'rgba(255,255,255,0.9)';
        ctx.lineWidth = 1.4;
        ctx.stroke();
        if (i < 3) {
          const label = `${a.s.name} · ${Math.round(wKt)} kt`;
          ctx.font = '700 13px sans-serif';
          const m = ctx.measureText(label);
          const lx = cx + 10, ly = cy - 12;
          ctx.fillStyle = 'rgba(15,18,22,0.80)';
          ctx.fillRect(lx - 4, ly - 10, m.width + 8, 20);
          ctx.strokeStyle = PAL.border;
          ctx.strokeRect(lx - 4, ly - 10, m.width + 8, 20);
          ctx.fillStyle = PAL.fg;
          ctx.textAlign = 'left';
          ctx.fillText(label, lx, ly);
        }
      });

      ctx.restore();

      // Tick labels.
      ctx.fillStyle = PAL.muted;
      ctx.font = '12px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      const [xt, xtl] = this.cfg.xticks;
      xt.forEach((lon, i) => {
        const cx = p2x(lon);
        if (cx >= x && cx <= x + w) {
          ctx.fillText(xtl[i], cx, y + h + 4);
        }
      });
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      for (let lat = Math.ceil(latMin / 10) * 10; lat <= latMax; lat += 10) {
        if (lat < latMin || lat > latMax) continue;
        const cy = p2y(lat);
        const lbl = lat === 0 ? '0°' :
                    lat > 0  ? `${lat}°N` : `${Math.abs(lat)}°S`;
        ctx.fillText(lbl, x - 6, cy);
      }

      // Watermark @WeathermanAAA_ (bottom-right of map).
      ctx.fillStyle = 'rgba(255,255,255,0.30)';
      ctx.font = '900 16px sans-serif';
      ctx.textAlign = 'right';
      ctx.textBaseline = 'bottom';
      ctx.fillText('@WeathermanAAA_', x + w - 8, y + h - 4);

      // Date label top-right of map.
      const d = new Date(tNow);
      const monthNames = ['Jan','Feb','Mar','Apr','May','Jun',
                          'Jul','Aug','Sep','Oct','Nov','Dec'];
      const dateStr = `${monthNames[d.getUTCMonth()]} ${String(d.getUTCDate()).padStart(2,'0')}, ${d.getUTCFullYear()} · ${String(d.getUTCHours()).padStart(2,'0')} UTC`;
      ctx.font = '700 14px sans-serif';
      const mw = ctx.measureText(dateStr);
      const bx = x + w - mw.width - 16, by = y + 10;
      ctx.fillStyle = 'rgba(15,18,22,0.80)';
      ctx.fillRect(bx - 6, by - 4, mw.width + 16, 24);
      ctx.strokeStyle = PAL.border;
      ctx.strokeRect(bx - 6, by - 4, mw.width + 16, 24);
      ctx.fillStyle = PAL.fg;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'top';
      ctx.fillText(dateStr, bx + 2, by + 1);

      this._lastDateStr = dateStr;
    }

    _drawDateLabel() {
      this.dateLabel.textContent = this._lastDateStr || '-';
    }

    _drawAce(ctx, x, y, w, h) {
      // Background + frame.
      ctx.fillStyle = PAL.panel;
      ctx.fillRect(x, y, w, h);
      ctx.strokeStyle = PAL.border;
      ctx.lineWidth = 1;
      ctx.strokeRect(x, y, w, h);

      const doyMax = 366;
      const dx = d => x + (d - 1) / (doyMax - 1) * w;
      const dy = v => y + h - v / this.aceMaxY * (h - 28);

      // Month ticks.
      const monthDoy = [1,32,60,91,121,152,182,213,244,274,305,335];
      const monthLbl = ['Jan','Feb','Mar','Apr','May','Jun','Jul',
                        'Aug','Sep','Oct','Nov','Dec'];
      ctx.save();
      ctx.beginPath(); ctx.rect(x, y, w, h); ctx.clip();

      // Climo band (p10–p90).
      const doy = this.ace.doy;
      const p10 = this.ace.climo.p10, p90 = this.ace.climo.p90;
      ctx.fillStyle = 'rgba(145,153,164,0.18)';
      ctx.beginPath();
      ctx.moveTo(dx(doy[0]), dy(p10[0]));
      for (let i = 1; i < doy.length; i++) ctx.lineTo(dx(doy[i]), dy(p10[i]));
      for (let i = doy.length - 1; i >= 0; i--) ctx.lineTo(dx(doy[i]), dy(p90[i]));
      ctx.closePath(); ctx.fill();

      // Climo mean dashed.
      const mean = this.ace.climo.mean;
      ctx.strokeStyle = PAL.muted; ctx.lineWidth = 1.0;
      ctx.setLineDash([6, 5]);
      ctx.beginPath();
      ctx.moveTo(dx(doy[0]), dy(mean[0]));
      for (let i = 1; i < doy.length; i++) ctx.lineTo(dx(doy[i]), dy(mean[i]));
      ctx.stroke();
      ctx.setLineDash([]);

      // Prior year.
      if (this.priorYearValues) {
        ctx.strokeStyle = PAL.violet; ctx.lineWidth = 1.2; ctx.globalAlpha = 0.8;
        ctx.beginPath();
        ctx.moveTo(dx(1), dy(this.priorYearValues[0]));
        for (let i = 1; i < this.priorYearValues.length; i++)
          ctx.lineTo(dx(i + 1), dy(this.priorYearValues[i]));
        ctx.stroke();
        ctx.globalAlpha = 1;
      }

      // Current year up to cutoff.
      const tNow = this.tStart + this.frac * (this.tEnd - this.tStart);
      const d = new Date(tNow);
      const doyCut = Math.floor(
        (tNow - Date.UTC(this.year, 0, 1)) / (86400 * 1000)) + 1;
      const cut = Math.max(1, Math.min(this.curveVal.length, doyCut));
      ctx.strokeStyle = PAL.amber; ctx.lineWidth = 2.4;
      ctx.beginPath();
      if (cut > 0) {
        ctx.moveTo(dx(1), dy(this.curveVal[0]));
        for (let i = 1; i < cut; i++) ctx.lineTo(dx(i + 1), dy(this.curveVal[i]));
        ctx.stroke();
        // Current-value dot + label.
        const lastV = this.curveVal[cut - 1];
        const cx = dx(cut), cy = dy(lastV);
        ctx.fillStyle = PAL.amber;
        ctx.beginPath(); ctx.arc(cx, cy, 7, 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = PAL.fg; ctx.lineWidth = 1.2; ctx.stroke();
        ctx.fillStyle = PAL.amber;
        ctx.font = '700 14px sans-serif';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'bottom';
        ctx.fillText(lastV.toFixed(1), cx + 6, cy - 4);
      }
      ctx.restore();

      // Month ticks.
      ctx.fillStyle = PAL.muted;
      ctx.font = '11px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      for (let i = 0; i < monthDoy.length; i++) {
        ctx.fillText(monthLbl[i], dx(monthDoy[i]), y + h + 2);
      }
      // Y ticks.
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      const niceTicks = (max) => {
        const mag = Math.pow(10, Math.floor(Math.log10(max)));
        const step = max / 4 > mag * 2 ? mag * 2 : mag;
        const arr = [];
        for (let v = 0; v <= max; v += step) arr.push(v);
        return arr;
      };
      for (const v of niceTicks(this.aceMaxY)) {
        ctx.fillText(Math.round(v), x - 4, dy(v));
      }

      // Legend (top-left of ACE panel).
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.font = '11px sans-serif';
      const items = [
        {color: 'rgba(145,153,164,0.35)', text: '1991–2020 10–90%', box: true},
        {color: PAL.muted,  text: '1991–2020 mean', dash: true},
      ];
      if (this.priorYearValues) items.push({color: PAL.violet, text: String(this.year - 1)});
      items.push({color: PAL.amber, text: String(this.year)});
      let lx = x + 10;
      items.forEach(it => {
        if (it.box) {
          ctx.fillStyle = it.color;
          ctx.fillRect(lx, y + 10, 16, 10);
          lx += 20;
        } else if (it.dash) {
          ctx.strokeStyle = it.color; ctx.setLineDash([4,4]); ctx.lineWidth = 1.4;
          ctx.beginPath();
          ctx.moveTo(lx, y + 15); ctx.lineTo(lx + 16, y + 15); ctx.stroke();
          ctx.setLineDash([]); lx += 20;
        } else {
          ctx.strokeStyle = it.color; ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(lx, y + 15); ctx.lineTo(lx + 16, y + 15); ctx.stroke();
          lx += 20;
        }
        ctx.fillStyle = PAL.muted;
        const m = ctx.measureText(it.text);
        ctx.fillText(it.text, lx, y + 15);
        lx += m.width + 14;
      });
    }
  }

  // --- init ---------------------------------------------------------
  function init() {
    document.querySelectorAll('.season-animator').forEach(el => {
      if (!el._animator) el._animator = new Animator(el);
    });
  }
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', init);
  else init();
})();
