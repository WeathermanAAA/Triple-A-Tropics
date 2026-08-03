/*
 * hafs.js - Triple-A-Tropics /models/ HAFS viewer
 * ---------------------------------------------------------------------------
 * Manifest-driven forecast-loop viewer for the HAFS model plots. Reads a
 * manifest published to Cloudflare R2 by generate_hafs_plots.py and lets the
 * user pick a cycle -> storm -> model (HAFS-A/B) -> domain (storm nest /
 * parent) -> product (Wind / Reflectivity) and step, play, or jump straight
 * to any forecast hour.
 *
 * PROGRESSIVE FRAMES (manifest v2). The builder now publishes cycles as they
 * render, frame by frame. The manifest carries a `cycles[]` array (newest
 * first, at most 2) each describing one model cycle and its in-progress state;
 * frame PNG keys are CYCLE-SCOPED and immutable once written. The viewer:
 *   - shows a cycle picker when >1 cycle is present;
 *   - draws a NUMBERED forecast-hour button grid over the FULL expected grid
 *     (0..fxx_end step fxx_step): rendered hours are lit + clickable, pending
 *     hours are greyed but visible (watch the run fill in), and the current
 *     hour is highlighted;
 *   - polls the manifest (45 s while any cycle is in_progress, else 300 s) and
 *     DIFF-MERGES: new frames relight hour buttons and preload in place WITHOUT
 *     resetting the user's cycle/storm/model/domain/product/hour selection;
 *   - never yanks the user to a newly-discovered cycle - it shows a "building -
 *     view" badge that switches only on click.
 *
 * Manifest v2 shape (models/hafs/manifest.json on cdn.triple-a-tropics.com):
 *   {
 *     "generated_at": "2026-06-05T01:23:45Z",
 *     "products": [{"slug","label","short"}, ...],
 *     "models":   [{"slug","label"}, ...],
 *     "domains":  [{"slug","label","raw"}, ...],
 *     "fxx_step": 3, "fxx_pad": 3,
 *     "fxx_end": 126,
 *     "path_template_cycles": "{cycle}/{model}/{storm}/{domain}/{product}/f{fxx}.png",
 *     "cycles": [
 *       {"cycle":"2026060418","in_progress":true,"frames_done":96,
 *        "frames_expected":172,"started_utc":"...","storms":[ ... ]},
 *       {"cycle":"2026060412","in_progress":false, ...}
 *     ],
 *     // legacy mirror of the newest COMPLETE cycle (deploy-skew zero-blink):
 *     "cycle": "2026060412",
 *     "storms": [ ... ],
 *     "path_template": "2026060412/{model}/{storm}/{domain}/{product}/f{fxx}.png"
 *   }
 *
 * Backward-tolerant (LEGACY mode): a manifest with NO `cycles[]` is treated as
 * a single implicit cycle built from the top-level `storms` / `cycle` /
 * `path_template`. In that mode the old behavior is preserved byte-for-byte:
 * the hour grid spans the rendered hours only (all lit) and frame URLs keep
 * the ?v=generated_at cache-bust. New (cycles-mode) frames are immutable and
 * cycle-scoped, so they drop the ?v=.
 */
(function () {
  'use strict';

  // The browser global, or null under the node test harness (which supplies a
  // document but no `window`). Resolved ONCE here so optional integrations -
  // telemetry below - can be probed without a bare `window` reference, which
  // is a hard ReferenceError, not undefined, when the global is absent.
  var GLOBAL = (typeof window !== 'undefined') ? window : null;

  var BASE = 'https://cdn.triple-a-tropics.com';
  var MANIFEST_URL = BASE + '/models/hafs/manifest.json';
  var SPEED_OPTIONS = [0.5, 1, 2, 4];   // playback frames-per-step multiplier
  var BASE_FPS = 4;                     // frames/sec at 1× speed
  var POLL_IN_PROGRESS_MS = 45000;      // poll cadence while a cycle renders
  var POLL_IDLE_MS = 300000;            // poll cadence when all cycles complete

  var DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

  function pad(n, w) { n = String(n); while (n.length < w) n = '0' + n; return n; }

  // Format an ISO-ish instant (…Z) as "Sun 2023-09-10 12Z" in UTC.
  function fmtUTC(d) {
    return DOW[d.getUTCDay()] + ' ' +
      d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1, 2) + '-' +
      pad(d.getUTCDate(), 2) + ' ' + pad(d.getUTCHours(), 2) + 'Z';
  }

  // "2026060418" -> "2026-06-04 18Z"
  function fmtCycle(c) {
    if (!c || c.length < 10) return c || '';
    return c.slice(0, 4) + '-' + c.slice(4, 6) + '-' + c.slice(6, 8) +
      ' ' + c.slice(8, 10) + 'Z';
  }

  // "2026060418" -> "18Z" (the bare hour tag - kept for exports/back-compat).
  function cycleHourTag(c) {
    return (c && c.length >= 10) ? c.slice(8, 10) + 'Z' : (c || '');
  }

  // "2026061018" -> "Jun 10 18Z" - the DATED cycle tag every user-visible
  // surface shows. Bare hour tags made two consecutive cross-midnight runs
  // (06-10 18Z + 06-11 00Z) look like same-day runs with 06z/12z missing;
  // the date makes the picker honest about WHEN each run is from.
  var MONTH_TAGS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function cycleDayTag(c) {
    if (!c || c.length < 10) return c || '';
    var mo = parseInt(c.slice(4, 6), 10);
    if (!(mo >= 1 && mo <= 12)) return cycleHourTag(c);
    return MONTH_TAGS[mo - 1] + ' ' + parseInt(c.slice(6, 8), 10) + ' ' +
      c.slice(8, 10) + 'Z';
  }

  function el(id) { return document.getElementById(id); }

  function HafsViewer(root, opts) {
    // Mount config (one impl, two mounts - CYCLOLAB_DESIGN §7.3): the
    // /models/ page passes no opts and gets today's exact behavior; the
    // CycloLab per-storm page injects its own manifest URL, element table
    // and a storm lock (single-storm mount, picker hidden). Everything
    // below the constructor is mount-agnostic.
    opts = opts || {};
    this.root = root;
    this.manifestUrl = opts.manifestUrl || MANIFEST_URL;
    this.assetBase = opts.assetBase || (BASE + '/models/hafs/');
    this.stormLock = opts.stormLock || null;
    this.manifest = null;
    this.cacheBust = '';
    this.legacyMode = true;     // no cycles[] -> single implicit cycle
    this.cycles = [];           // normalized cycle objects (newest first)
    this.fxxStep = 3;
    this.fxxEnd = 0;            // expected terminal hour (grid upper bound)
    // current selection
    this.cycle = null;     // selected cycle object
    this.storm = null;     // storm object (within the selected cycle)
    this.model = null;     // slug
    this.domain = null;    // slug
    this.product = null;   // slug (defaults to the first manifest product = Wind)
    this.fxxGrid = [];     // FULL expected grid for the selection (0..fxx_end)
    this.fxxList = [];     // RENDERED forecast hours for the selection (subset)
    this.idx = 0;          // index into fxxList (rendered frames only)
    this.playing = false;
    this.speed = 1;
    // ---- orthogonal URL state (item 18) ----------------------------------
    // Every view is shareable: run/storm/model/domain/product/fxx/mode ride
    // as independent query params. Enabled only for the standalone /models/
    // mount - a storm-locked CycloLab embed must never rewrite ITS page URL.
    this.urlSync = opts.urlSync !== false && !this.stormLock &&
                   typeof window !== 'undefined' && !!window.history &&
                   typeof location !== 'undefined';
    this._urlBoot = this.urlSync ? this._readUrl() : null;   // consumed once
    this._urlTimer = null;
    // Shear-relative view (spec #4): an OVERLAY mode, not a render mode - the
    // raster stays north-up, the quadrant frame + shear vector rotate. Boot
    // from the URL here (display state, replaceState-only, never a history
    // entry), consumed like every other boot param.
    this.shearView = false;
    if (this._urlBoot && this._urlBoot.shear === '1') {
      this.shearView = true;
      delete this._urlBoot.shear;
    }
    this._expiredRun = null;
    // Playback pacing: a single requestAnimationFrame loop with a timestamp
    // threshold (the satellite sat-simple canon), NOT setInterval — vsync-
    // aligned, drift-free, and it never advances onto an undecoded frame.
    this.raf = null;
    this.lastTick = 0;
    this.pollTimer = null;
    this.pendingCycleKey = null;   // a newer cycle discovered mid-session
    this.preAnnounce = false;      // selected fallback because newest is empty
    this.preloaded = {};   // url → HTMLImageElement (load-started cache; bounded to selection)
    this.ready = {};       // url → true ONCE the image has DECODED (playback-gate)
    this.preloadGen = 0;   // bumped each selection so stale preloads are ignored

    this.dom = opts.els || {
      stage:    el('hafs-stage'),
      img:      el('hafs-img'),
      status:   el('hafs-status'),
      empty:    el('hafs-empty'),
      controls: el('hafs-controls'),
      cycleGroup: el('hafs-cycle-group'),
      cycles:   el('hafs-cycles'),
      stormSel: el('hafs-storm'),
      models:   el('hafs-models'),
      domains:  el('hafs-domains'),
      products: el('hafs-products'),
      hours:    el('hafs-hours'),
      play:     el('hafs-play'),
      stepB:    el('hafs-step-back'),
      stepF:    el('hafs-step-fwd'),
      speed:    el('hafs-speed'),
      fhour:    el('hafs-fhour'),
      valid:    el('hafs-valid'),
      meta:     el('hafs-meta'),
      badge:    el('hafs-badge'),
      pill:     el('hafs-pill'),
      stale:    el('hafs-stale'),
      buffer:   el('hafs-buffer'),
      player:   el('hafs-player'),
      caption:  el('hafs-caption')
    };
    // Single-storm mount: the picker is meaningless - one locked option.
    if (this.stormLock) this.dom.stormSel.style.display = 'none';
    this._wire();
    this._load();
  }

  HafsViewer.prototype._setStatus = function (msg, show) {
    if (msg != null) this.dom.status.querySelector('span').textContent = msg;
    this.dom.status.style.display = show ? 'flex' : 'none';
  };

  HafsViewer.prototype._load = function () {
    var self = this;
    this._setStatus('Loading manifest…', true);
    // The live-storm feed ranks the smart default (strongest active storm
    // first). Fetched IN PARALLEL with the manifest and raced against a short
    // timeout, so a slow or failed feed can only cost ~1.2 s and never blocks
    // the viewer - the ordering then falls back to the deck's own numbering,
    // which is deterministic without it. A storm-locked mount skips it.
    var feed = Promise.resolve(null);
    // Browser-only: the node test harness stubs the manifest fetch but must
    // never trigger a real network call for the feed.
    if (!this.stormLock && typeof window !== 'undefined' &&
        typeof fetch === 'function') {
      feed = Promise.race([
        fetch(BASE + '/global_storms.geojson', { cache: 'no-store' })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (gj) {
            if (!gj || !gj.features) return null;
            var rank = {};
            for (var i = 0; i < gj.features.length; i++) {
              var p = gj.features[i].properties || {};
              if (p.storm_id && p.is_active && rank[p.storm_id] == null) {
                rank[p.storm_id] = { peak_kt: p.peak_kt };
              }
            }
            return rank;
          }),
        new Promise(function (res) { setTimeout(function () { res(null); }, 1200); })
      ]).catch(function () { return null; });
    }
    Promise.all([this._fetchManifest(), feed])
      .then(function (r) {
        self.stormRank = r[1];
        self._onManifest(r[0]);
      })
      .catch(function (e) {
        console.error('hafs: manifest load failed', e);
        self._setStatus('Could not load model data. Try again shortly.', true);
      });
  };

  HafsViewer.prototype._fetchManifest = function () {
    return fetch(this.manifestUrl + '?t=' + Date.now(), { cache: 'no-store' })
      .then(function (r) {
        if (!r.ok) throw new Error('manifest HTTP ' + r.status);
        return r.json();
      });
  };

  // ---- manifest normalization ------------------------------------------

  // Build the canonical cycles[] list. v2 manifests carry it directly; a
  // legacy manifest is wrapped into a single implicit cycle so the rest of the
  // viewer has one code path. Each cycle is shallow-cloned with a guaranteed
  // `storms` array.
  HafsViewer.prototype._normalizeCycles = function (m) {
    var lockId = this.stormLock;
    // stormLock: filter every cycle's storm list to the locked id HERE so
    // every downstream path (default-cycle pick, frames test, empty state,
    // picker population, diff-merge) sees only the locked storm - the
    // newest cycle WITH FRAMES is then "newest with frames for THIS storm",
    // and a cycle that skipped the storm shows the normal empty state.
    function lock(storms) {
      storms = storms || [];
      if (!lockId) return storms;
      var out = [];
      for (var i = 0; i < storms.length; i++) {
        if (storms[i].id === lockId) out.push(storms[i]);
      }
      return out;
    }
    if (m.cycles && m.cycles.length) {
      this.legacyMode = false;
      return m.cycles.map(function (c) {
        return {
          cycle: c.cycle,
          in_progress: !!c.in_progress,
          frames_done: c.frames_done,
          frames_expected: c.frames_expected,
          started_utc: c.started_utc,
          storms: lock(c.storms)
        };
      });
    }
    // Legacy: one implicit cycle from the top-level fields.
    this.legacyMode = true;
    return [{
      cycle: m.cycle || '',
      in_progress: false,
      storms: lock(m.storms)
    }];
  };

  HafsViewer.prototype._cycleByKey = function (key) {
    for (var i = 0; i < this.cycles.length; i++) {
      if (this.cycles[i].cycle === key) return this.cycles[i];
    }
    return null;
  };

  // A cycle has frames if ANY storm under it has a non-empty fxx list.
  HafsViewer.prototype._cycleHasFrames = function (cyc) {
    var st = cyc.storms || [];
    for (var i = 0; i < st.length; i++) {
      var byModel = st[i].frames || {};
      for (var mdl in byModel) {
        if (byModel.hasOwnProperty(mdl) && this._anyFrames(byModel[mdl])) return true;
      }
    }
    return false;
  };

  // The default selection on a fresh load: newest cycle with ANY frames.
  // Sets this.preAnnounce when the newest entry is empty (pre-announce) and we
  // fell back to an older one.
  HafsViewer.prototype._defaultCycleKey = function () {
    this.preAnnounce = false;
    if (!this.cycles.length) return null;
    for (var i = 0; i < this.cycles.length; i++) {
      if (this._cycleHasFrames(this.cycles[i])) {
        // Newest-with-frames; if it isn't the literal newest entry, the newest
        // is a pre-announce shell -> flag the badge.
        if (i > 0) this.preAnnounce = true;
        return this.cycles[i].cycle;
      }
    }
    // No cycle has frames at all (rare) - take the newest so the empty state
    // shows against a real cycle key.
    return this.cycles[0].cycle;
  };

  HafsViewer.prototype._onManifest = function (m) {
    this.manifest = m;
    this.cacheBust = m.generated_at ? encodeURIComponent(m.generated_at) : '';
    this.fxxStep = m.fxx_step || 3;
    this.fxxEnd = (typeof m.fxx_end === 'number') ? m.fxx_end : 0;
    this.cycles = this._normalizeCycles(m);
    this.pendingCycleKey = null;

    var defKey = this._defaultCycleKey();
    // A shared link may name a run. If it still exists, honor it; if it has
    // EXPIRED (rotated out of the manifest), fall back to the default run and
    // say so - the rest of the link (storm/product/hour) still applies.
    var wantRun = this._urlTake('run');
    if (wantRun) {
      if (this._cycleByKey(wantRun)) defKey = wantRun;
      else this._expiredRun = wantRun;
    }
    var selCycle = this._cycleByKey(defKey);
    var storms = selCycle ? (selCycle.storms || []) : [];

    if (!storms.length) {
      // Off-season / no-data (or a lone pre-announce shell with nothing else).
      this._setStatus(null, false);
      this.dom.controls.style.display = 'none';
      this.dom.stage.style.display = 'none';
      this.dom.player.style.display = 'none';
      this.dom.hours.style.display = 'none';
      this.dom.caption.style.display = 'none';
      this.dom.empty.style.display = 'block';
      this._updateFooter();
      this._schedulePoll();
      return;
    }

    // Clear the "Loading manifest…" overlay now that we have storms - otherwise
    // the translucent spinner box sits over every frame for the whole session.
    this._setStatus(null, false);
    this.dom.empty.style.display = 'none';
    this.dom.controls.style.display = '';
    this.dom.stage.style.display = '';
    this.dom.player.style.display = '';
    this.dom.caption.style.display = '';

    this._selectCycle(defKey, false, false);
    this._updateFooter();
    this._schedulePoll();
    if (this._expiredRun) {
      this._toast('The linked run ' + this._expiredRun +
                  ' has expired — showing the latest run instead.');
      this._expiredRun = null;
    }
    // Canonicalize the address bar to the resolved state (replace, not push:
    // landing is not a navigation).
    this._syncUrl(false);
  };

  // ---- cycle picker -----------------------------------------------------

  // Show the cycle toggle group only when >1 cycle. A label reads "18Z" for a
  // complete cycle, "18Z · building" for one still rendering.
  // 6-h cadence: >9 h means a cycle was missed, >18 h means it is not coming.
  var STALE_WARN_H = 9, STALE_DEAD_H = 18;

  // "2026071906" -> Date. Returns null on anything that is not a cycle key,
  // so a malformed manifest degrades to "no banner" rather than "NaN days old".
  function parseCycleUTC(key) {
    var m = /^(\d{4})(\d{2})(\d{2})(\d{2})$/.exec(String(key || ''));
    if (!m) return null;
    return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4]));
  }

  HafsViewer.prototype._buildCyclePicker = function () {
    var group = this.dom.cycleGroup, host = this.dom.cycles;
    if (!group || !host) return;
    if (this.cycles.length <= 1) { group.style.display = 'none'; return; }
    group.style.display = '';
    var defs = this.cycles.map(function (c) {
      return {
        slug: c.cycle,
        label: cycleDayTag(c.cycle) + (c.in_progress ? ' · building' : '')
      };
    });
    var active = this.cycle ? this.cycle.cycle : defs[0].slug;
    this._buildToggle(host, defs, active, this._selectCycle.bind(this));
  };

  // Select a cycle (top of the sticky chain). `keepSelection` true preserves
  // the storm/model/domain/product/hour where the new cycle still offers them
  // (used by the badge "view" click and diff-merge); false resets down the
  // chain to that cycle's defaults (fresh load / explicit cycle switch).
  // `isUserSwitch` (default true) marks an explicit user choice, which clears
  // the pre-announce hint; the initial default selection passes false so the
  // pre-announce flag computed by _defaultCycleKey survives.
  HafsViewer.prototype._selectCycle = function (key, keepSelection, isUserSwitch) {
    if (isUserSwitch === undefined) isUserSwitch = true;
    var cyc = this._cycleByKey(key);
    if (!cyc) return;
    var switching = !this.cycle || this.cycle.cycle !== key;
    this.cycle = cyc;
    // Clicking a cycle is an explicit choice: clear any pre-announce hint, and
    // if we just switched to the pending (newer) cycle, clear that badge.
    if (switching && isUserSwitch) {
      if (this.pendingCycleKey === key) this.pendingCycleKey = null;
      this.preAnnounce = false;
    }
    this._highlight(this.dom.cycles, key);

    var prevStorm = (keepSelection && this.storm) ? this.storm.id : null;
    var storms = cyc.storms || [];

    if (!storms.length) {
      // This cycle has nothing to show for this mount - a storm-locked
      // mount whose storm hasn't rendered in this cycle yet, or an empty
      // pre-announce shell selected by hand. PER-CYCLE empty state, never
      // a crash: stage/player/grid hide, the cycle picker stays usable so
      // the user can switch back. (_selectStorm(undefined) used to
      // TypeError here and kill the viewer.)
      this._pause();
      this.storm = null; this.model = null; this.domain = null;
      this.dom.stormSel.innerHTML = '';
      this.dom.models.innerHTML = '';
      this.dom.domains.innerHTML = '';
      this.dom.products.innerHTML = '';
      this.dom.hours.innerHTML = '';
      this.dom.stage.style.display = 'none';
      this.dom.player.style.display = 'none';
      this.dom.hours.style.display = 'none';
      this.dom.caption.style.display = 'none';
      this.dom.empty.style.display = 'block';
      this._buildCyclePicker();
      this._updateFooter();
      return;
    }
    // Recover from a previously-empty selection: the apply-level path
    // owns first-load visibility; this mirrors it for cycle switches.
    this.dom.empty.style.display = 'none';
    this.dom.stage.style.display = '';
    this.dom.player.style.display = '';
    this.dom.hours.style.display = '';
    this.dom.caption.style.display = '';

    // Populate the storm dropdown for this cycle.
    var sel = this.dom.stormSel;
    sel.innerHTML = '';
    for (var i = 0; i < storms.length; i++) {
      var s = storms[i];
      var o = document.createElement('option');
      o.value = s.id;
      o.textContent = s.name + ' · ' + (s.basin_label || s.basin || '');
      sel.appendChild(o);
    }
    var keepId = null;
    if (prevStorm) {
      for (var j = 0; j < storms.length; j++) if (storms[j].id === prevStorm) keepId = prevStorm;
    }
    // Boot/restore precedence: an explicit URL storm > the kept selection >
    // the smart default (strongest active storm) > manifest order.
    var urlStorm = this._urlTake('storm');
    if (urlStorm && !this._stormInList(storms, urlStorm)) urlStorm = null;
    this._selectStorm(urlStorm || keepId || this._defaultStormId(storms),
                      keepSelection);
    this._buildCyclePicker();
    this._updateFooter();
  };

  HafsViewer.prototype._stormInList = function (storms, id) {
    for (var i = 0; i < storms.length; i++) {
      if (storms[i].id === id) return true;
    }
    return false;
  };

  // ---- smart default on the active storm (item 11) ------------------------
  // When storms are active, land on one - and pick it DETERMINISTICALLY, not
  // "whatever the manifest lists first". The ordering, explicitly:
  //
  //   1. named systems before invests (storm number < 90 before >= 90) - an
  //      invest is never the default while a designated storm exists;
  //   2. within each group, the STRONGEST first: current-season peak intensity
  //      (kt) from the site's live storm feed, fetched in parallel with the
  //      manifest and never allowed to delay boot by more than ~1.2 s;
  //   3. feed unavailable (or a storm missing from it): lower storm number
  //      first - the deck's own stable ordering - so the pick is deterministic
  //      with or without the feed;
  //   4. final tie: id string.
  //
  // "Strongest" is season-peak rather than instantaneous intensity because
  // peak is what the feed carries for every storm; the difference only
  // matters when a decaying ex-major coexists with a strengthening storm,
  // and either answer is defensible - this one is stated and fixed.
  HafsViewer.prototype._stormNum = function (id) {
    var m = /^(\d+)/.exec(String(id || ''));
    return m ? parseInt(m[1], 10) : 999;
  };

  HafsViewer.prototype._feedPeak = function (s) {
    if (!this.stormRank || !s) return null;
    var year = String(s.cycle || '').slice(0, 4);
    var b = String(s.basin || '').toLowerCase();
    var pre = (b === 'wp' || b === 'io' || b === 'sh') ? 'JTWC_' : 'NHC_';
    var sid = pre + b.toUpperCase() + pad(this._stormNum(s.id), 2) + year;
    var r = this.stormRank[sid];
    return (r && typeof r.peak_kt === 'number') ? r.peak_kt : null;
  };

  HafsViewer.prototype._defaultStormId = function (storms) {
    if (!storms || !storms.length) return null;
    var self = this;
    var ranked = storms.slice().sort(function (a, b) {
      var ia = self._stormNum(a.id) >= 90 ? 1 : 0;
      var ib = self._stormNum(b.id) >= 90 ? 1 : 0;
      if (ia !== ib) return ia - ib;                       // named before invests
      var pa = self._feedPeak(a), pb = self._feedPeak(b);
      if (pa !== pb) return (pb == null ? -1 : pb) - (pa == null ? -1 : pa);
      var na = self._stormNum(a.id), nb = self._stormNum(b.id);
      if (na !== nb) return na - nb;                       // deck order
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
    });
    return ranked[0].id;
  };

  HafsViewer.prototype._stormById = function (id) {
    var st = (this.cycle && this.cycle.storms) || [];
    for (var i = 0; i < st.length; i++) if (st[i].id === id) return st[i];
    return null;
  };

  // Product definitions, in manifest order. New manifests carry a "products"
  // list; older ones only a singular "product"; fall back to a Wind default.
  HafsViewer.prototype._productDefs = function () {
    var m = this.manifest || {};
    if (m.products && m.products.length) return m.products;
    if (m.product) return [m.product];
    return [{ slug: 'mslp_wind', label: 'MSLP + 10 m Wind', short: 'Wind' }];
  };

  HafsViewer.prototype._defaultProductSlug = function () {
    return this._productDefs()[0].slug;
  };

  // Normalize a (storm, model, domain) frame entry to a product -> [fxx] map.
  // New schema: {product: [...]}. Old schema: [...] (read as the default
  // product) so a stale manifest still works until the next cycle.
  HafsViewer.prototype._domFrames = function (storm, model, domain) {
    var fr = (storm.frames[model] || {})[domain];
    if (!fr) return {};
    if (Array.isArray(fr)) {
      var o = {};
      o[this._defaultProductSlug()] = fr;
      return o;
    }
    return fr;
  };

  // Any non-empty fxx list anywhere under a model's {domain: {product: [...]}}
  // (or the old {domain: [...]}) tree.
  HafsViewer.prototype._anyFrames = function (byDomain) {
    for (var d in byDomain) {
      if (!byDomain.hasOwnProperty(d)) continue;
      var entry = byDomain[d];
      if (Array.isArray(entry)) {
        if (entry.length) return true;
      } else {
        for (var p in entry) {
          if (entry.hasOwnProperty(p) && entry[p] && entry[p].length) return true;
        }
      }
    }
    return false;
  };

  // True if a domain entry (new object or old array shape) has any frames.
  HafsViewer.prototype._domainHasFrames = function (entry) {
    if (!entry) return false;
    if (Array.isArray(entry)) return entry.length > 0;
    for (var p in entry) {
      if (entry.hasOwnProperty(p) && entry[p] && entry[p].length) return true;
    }
    return false;
  };

  // Models that actually have frames for the current storm, in manifest order.
  HafsViewer.prototype._modelsFor = function (storm) {
    var out = [];
    var defs = this.manifest.models || [];
    for (var i = 0; i < defs.length; i++) {
      var slug = defs[i].slug;
      if (storm.frames[slug] && this._anyFrames(storm.frames[slug])) {
        out.push(defs[i]);
      }
    }
    return out;
  };

  // Domains with frames for the current storm+model, in manifest order.
  HafsViewer.prototype._domainsFor = function (storm, model) {
    var out = [];
    var defs = this.manifest.domains || [];
    var fr = storm.frames[model] || {};
    for (var i = 0; i < defs.length; i++) {
      if (this._domainHasFrames(fr[defs[i].slug])) out.push(defs[i]);
    }
    return out;
  };

  // Products with frames for the current storm+model+domain, in manifest order.
  // The model def for a slug, or null. Model defs now carry the ModelSpec meta
  // (convection treatment, AI paradigm, badge + intensity-stat flags); an older
  // manifest carries only {slug,label} and every consumer below degrades to the
  // pre-existing behavior.
  HafsViewer.prototype._modelDef = function (slug) {
    var defs = (this.manifest && this.manifest.models) || [];
    for (var i = 0; i < defs.length; i++) {
      if (defs[i].slug === slug) return defs[i];
    }
    return null;
  };

  HafsViewer.prototype._productsFor = function (storm, model, domain) {
    var out = [];
    var defs = this._productDefs();
    var fr = this._domFrames(storm, model, domain);
    // STRUCTURAL GATE. A product whose signal IS resolved deep convection
    // (reflectivity, simulated microwave) is meaningless off a model that
    // parameterises convection, so it must be unofferable rather than merely
    // unhelpful. The builder already refuses to render such a pair, so the
    // frames check below would usually hide it anyway; this makes the rule
    // explicit at the UI so a stale or hand-edited manifest cannot surface a
    // chip the physics does not support.
    var md = this._modelDef(model);
    var paramConv = !!(md && md.convection_explicit === false);
    for (var i = 0; i < defs.length; i++) {
      var slug = defs[i].slug;
      if (paramConv && defs[i].requires_explicit_convection) continue;
      if (fr[slug] && fr[slug].length) out.push(defs[i]);
    }
    return out;
  };

  HafsViewer.prototype._selectStorm = function (id, keepSelection) {
    this.storm = this._stormById(id);
    this.dom.stormSel.value = id;
    // Pick first available model, preferring to keep the current one.
    var models = this._modelsFor(this.storm);
    var keep = null;
    if (keepSelection !== false) {
      for (var i = 0; i < models.length; i++) if (models[i].slug === this.model) keep = this.model;
    }
    var urlModel = this._urlTake('model');
    if (urlModel && !models.some(function (m) { return m.slug === urlModel; })) urlModel = null;
    var pickM = urlModel || keep || (models[0] && models[0].slug);
    this._buildToggle(this.dom.models, models, pickM, this._selectModel.bind(this));
    this._selectModel(pickM, keepSelection);
  };

  HafsViewer.prototype._selectModel = function (slug, keepSelection) {
    this.model = slug;
    this._highlight(this.dom.models, slug);
    var domains = this._domainsFor(this.storm, slug);
    var keep = null;
    if (keepSelection !== false) {
      for (var i = 0; i < domains.length; i++) if (domains[i].slug === this.domain) keep = this.domain;
    }
    var urlDomain = this._urlTake('domain');
    if (urlDomain && !domains.some(function (d) { return d.slug === urlDomain; })) urlDomain = null;
    var pickD = urlDomain || keep || (domains[0] && domains[0].slug);
    this._buildToggle(this.dom.domains, domains, pickD, this._selectDomain.bind(this));
    this._selectDomain(pickD, keepSelection);
  };

  HafsViewer.prototype._selectDomain = function (slug, keepSelection) {
    this.domain = slug;
    this._highlight(this.dom.domains, slug);
    // Pick first available product, preferring to keep the current one (so a
    // Wind/Reflectivity choice survives storm/model/domain switches). Default on
    // first load is the first manifest product = Wind, so the view is unchanged.
    var products = this._productsFor(this.storm, this.model, slug);
    var keep = null;
    if (keepSelection !== false) {
      for (var i = 0; i < products.length; i++) if (products[i].slug === this.product) keep = this.product;
    }
    var urlProduct = this._urlTake('product');
    if (urlProduct && !products.some(function (p) { return p.slug === urlProduct; })) urlProduct = null;
    var pick = urlProduct || keep || (products[0] && products[0].slug);
    this._buildToggle(this.dom.products, products, pick,
                      this._selectProduct.bind(this), 'short');
    this._selectProduct(pick, keepSelection);
  };

  // Build the FULL expected forecast-hour grid for the active cycle:
  // range(0, fxx_end+1, fxx_step). Legacy manifests carry no fxx_end, so the
  // grid degenerates to the rendered hours (old availability-blind behavior).
  HafsViewer.prototype._buildGrid = function (rendered) {
    if (this.legacyMode || !this.fxxEnd) return rendered.slice();
    var grid = [];
    for (var h = 0; h <= this.fxxEnd; h += this.fxxStep) grid.push(h);
    // Guard against a rendered hour past the declared end (late grid growth).
    for (var i = 0; i < rendered.length; i++) {
      if (grid.indexOf(rendered[i]) === -1) grid.push(rendered[i]);
    }
    grid.sort(function (a, b) { return a - b; });
    return grid;
  };

  HafsViewer.prototype._selectProduct = function (slug, keepSelection) {
    this.product = slug;
    // Per-product VIEW counter (the hero-set scheduler's popularity term).
    // Emitted here, the single place `this.product` is assigned, so no call
    // path can view a product without being counted. This method fires from
    // nine paths - including the 45 s manifest poll's selection regrow - so
    // TatTelemetry dedupes on the full (cycle, storm, model, domain, product)
    // tuple and ignores a repeat; passing the whole context is what makes that
    // dedupe correct. Optional module: absent or opted out, this is a no-op.
    if (GLOBAL && GLOBAL.TatTelemetry) {
      GLOBAL.TatTelemetry.view({
        product: slug, model: this.model, domain: this.domain,
        // storm/cycle are OBJECTS on the viewer; the dedupe tuple needs their
        // ids, or every tuple would stringify to "[object Object]" and collapse.
        storm: this.storm && this.storm.id,
        cycle: this.cycle && this.cycle.cycle
      });
    }
    this._highlight(this.dom.products, slug);
    var fr = this._domFrames(this.storm, this.model, this.domain)[slug] || [];
    // Keep the same forecast HOUR across selection changes when possible (Wind
    // and Reflectivity share an fxx list, so a product toggle holds the hour;
    // a domain switch keeps it when present, else clamps the index).
    var prev = this.fxxList || [];
    var curF = prev.length ? prev[Math.min(this.idx, prev.length - 1)] : 0;
    this.fxxList = fr.slice().sort(function (a, b) { return a - b; });
    this.fxxGrid = this._buildGrid(this.fxxList);

    // Land on the same hour if it is still rendered; else snap to nearest
    // rendered (prefer lower), never landing on a pending tick.
    if (keepSelection !== false && this.fxxList.indexOf(curF) >= 0) {
      this.idx = this.fxxList.indexOf(curF);
    } else if (keepSelection !== false) {
      this.idx = this._renderedIndexNear(curF);
    } else {
      this.idx = 0;
    }
    this.idx = Math.max(0, Math.min(this.idx, Math.max(0, this.fxxList.length - 1)));

    // A shared link's hour and mode land last, once the rendered list exists.
    var urlFxx = this._urlTake('fxx');
    if (urlFxx != null) {
      var wantF = parseInt(urlFxx, 10);
      if (!isNaN(wantF)) this.idx = this._renderedIndexNear(wantF);
    }
    var urlMode = this._urlTake('mode');

    this._buildHourGrid();
    this._updateCaption();
    this._show(this.idx);
    this._preloadAll();
    if (urlMode === 'play' && !this.playing) this._play();
  };

  // ---- availability-aware forecast-hour grid ----------------------------

  // The index into fxxList (rendered hours) nearest to a target fxx, preferring
  // the lower (earlier) hour on a tie / when the exact hour is pending.
  HafsViewer.prototype._renderedIndexNear = function (targetFxx) {
    var list = this.fxxList;
    if (!list.length) return 0;
    // exact hit
    var exact = list.indexOf(targetFxx);
    if (exact >= 0) return exact;
    // largest rendered hour <= target (prefer lower)
    var lowIdx = -1;
    for (var i = 0; i < list.length; i++) {
      if (list[i] <= targetFxx) lowIdx = i; else break;
    }
    if (lowIdx >= 0) return lowIdx;
    return 0;   // target is below the earliest rendered hour
  };

  // Render the forecast-hour grid: one numbered button per grid slot, lit and
  // clickable when that hour is rendered for the current selection, greyed and
  // disabled while pending. Rebuilt whole on selection change and poll
  // diff-merge (cheap: <=43 buttons) - the merge path re-pins the current hour
  // first, so a rebuild never moves the user's frame.
  // The EXPECTED forecast hours for the current (storm, model, domain) pair -
  // what upstream posted, whether or not it has rendered yet. null on a
  // manifest that predates the field (the strip then degrades to the old
  // two-state view rather than guessing).
  HafsViewer.prototype._expectedFxx = function () {
    var s = this.storm;
    if (!s || !s.expected || !this.model || !this.domain) return null;
    var byModel = s.expected[this.model];
    var list = byModel && byModel[this.domain];
    return Array.isArray(list) ? list : null;
  };

  /* The FIVE-STATE availability model. PENDING vs UNAVAILABLE is the state
   * that matters most: "wait ~90 seconds" vs "stop waiting". It needs the
   * manifest's per-pair `expected` hours - present hours alone cannot tell a
   * frame that has not rendered YET from one that never will.
   *
   *   available   rendered, clickable            (+ `active` when displayed)
   *   pending     expected, not yet rendered, cycle still building
   *   unavailable expected, not rendered, cycle COMPLETE - it failed; stop
   *               waiting, this frame is not coming
   *   unscheduled not in expected at all - upstream never posted it (the
   *               storm ended, or the model stopped publishing)
   */
  HafsViewer.prototype._hourState = function (fxx, renderedSet, expectedSet) {
    if (renderedSet[fxx]) return 'lit';
    if (!expectedSet) return 'pending';          // legacy manifest: two-state
    var building = !!(this.cycle && this.cycle.in_progress);
    if (expectedSet[fxx]) return building ? 'pending' : 'unavail';
    return 'unsched';
  };

  var HOUR_STATE_TITLE = {
    lit: '',
    pending: ' · rendering — expected shortly',
    unavail: ' · did not render in this run (not coming)',
    unsched: ' · not produced for this storm/run'
  };

  HafsViewer.prototype._buildHourGrid = function () {
    var host = this.dom.hours;
    if (!host) return;
    host.innerHTML = '';
    var grid = this.fxxGrid, n = grid.length;
    if (n <= 1) { host.style.display = 'none'; return; }
    host.style.display = '';
    var renderedSet = {};
    for (var i = 0; i < this.fxxList.length; i++) renderedSet[this.fxxList[i]] = true;
    var expected = this._expectedFxx();
    var expectedSet = null;
    if (expected) {
      expectedSet = {};
      for (var j = 0; j < expected.length; j++) expectedSet[expected[j]] = true;
    }
    var self = this;
    for (var k = 0; k < n; k++) {
      (function (fxx) {
        var state = self._hourState(fxx, renderedSet, expectedSet);
        var lit = state === 'lit';
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'hafs-hr ' + state;
        b.textContent = pad(fxx, 3);
        b.setAttribute('data-fxx', String(fxx));
        b.setAttribute('data-state', state);
        b.title = 'F' + pad(fxx, 3) + (HOUR_STATE_TITLE[state] || '');
        b.disabled = !lit;
        if (lit) {
          b.addEventListener('click', function () {
            self._pause();
            // indexOf at CLICK time: the rendered list may have grown since
            // this button was built.
            self._show(self.fxxList.indexOf(fxx));
          });
        }
        host.appendChild(b);
      })(grid[k]);
    }
    this._highlightHour();
  };

  // Mark the button under the current frame as the active one. aria-pressed
  // goes only on the lit (interactive) buttons; pending ones are disabled and
  // carry no toggle semantics.
  HafsViewer.prototype._highlightHour = function () {
    var host = this.dom.hours;
    if (!host || !this.fxxList.length) return;
    var cur = String(this.fxxList[this.idx]);
    var btns = host.querySelectorAll('.hafs-hr');
    for (var i = 0; i < btns.length; i++) {
      var on = btns[i].getAttribute('data-fxx') === cur;
      btns[i].classList.toggle('current', on);
      if (!btns[i].disabled) {
        btns[i].setAttribute('aria-pressed', on ? 'true' : 'false');
      }
    }
  };

  // Build a segmented button group; calls onPick(slug) on click. labelKey picks
  // which field labels the buttons (default 'label'; the product toggle uses
  // 'short' so it reads "Wind" / "Reflectivity").
  HafsViewer.prototype._buildToggle = function (container, defs, active, onPick, labelKey) {
    var viewer = this;
    container.innerHTML = '';
    labelKey = labelKey || 'label';
    for (var i = 0; i < defs.length; i++) {
      (function (def) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'hafs-seg' + (def.slug === active ? ' active' : '');
        b.textContent = def[labelKey] || def.label;
        b.title = def.label || def[labelKey] || '';   // full name on hover (cells may ellipsize)
        // AI BADGE. Only model defs carry `is_ai`, so this is inert for the
        // product and domain toggles that share this builder. A user comparing
        // fields across models has to be able to see at a glance which ones are
        // learned emulators rather than integrations of the equations - and why
        // those cards show no intensity number.
        if (def.is_ai) {
          var badge = document.createElement('span');
          badge.className = 'hafs-badge-ai';
          badge.textContent = 'AI';
          b.appendChild(badge);
          b.title = (b.title ? b.title + ' — ' : '') +
            'AI model (' + (def.ai_paradigm || 'learned emulator') +
            '). Intensity statistics are withheld: current learned emulators ' +
            'systematically under-deepen tropical cyclones.';
        }
        b.setAttribute('data-slug', def.slug);
        b.addEventListener('click', function () {
          onPick(def.slug);
          // Every toggle in this builder is push-worthy navigation (cycle,
          // model, domain, product) - and ONLY this user-click path pushes:
          // the poll's programmatic regrow calls the same _select* functions
          // without ever passing through here, so it can never spam history.
          viewer._syncUrl(true);
        });
        container.appendChild(b);
      })(defs[i]);
    }
    // Hide the group entirely if there's only one option (nothing to choose).
    container.parentNode.style.display = defs.length > 1 ? '' : 'none';
  };

  HafsViewer.prototype._highlight = function (container, slug) {
    var btns = container.querySelectorAll('.hafs-seg');
    for (var i = 0; i < btns.length; i++) {
      btns[i].classList.toggle('active', btns[i].getAttribute('data-slug') === slug);
    }
  };

  // ---- container read path (spec #27) ----------------------------------
  // Rows may also publish as geometric tar blocks (manifest.containers +
  // storms[].blocks). A frame inside a block is read with ONE HTTP Range
  // request for exactly its bytes - same PNG, ~86% fewer object writes on
  // the publish side. Frames not covered by blocks use the per-frame path.
  HafsViewer.prototype._blockFor = function (fxx) {
    if (this.legacyMode || !this.manifest || !this.manifest.containers ||
        !this.storm || !this.storm.blocks) return null;
    var m = this.storm.blocks[this.model];
    var d = m && m[this.domain];
    var arr = d && d[this.product];
    if (!arr) return null;
    for (var i = 0; i < arr.length; i++) {
      if (arr[i].fxx.indexOf(fxx) !== -1) {
        var mem = arr[i].members && arr[i].members['f' + pad(fxx, 3) + '.png'];
        if (mem) return { key: arr[i].key, off: mem[0], len: mem[1] };
      }
    }
    return null;
  };

  HafsViewer.prototype._frameUrl = function (fxx) {
    var m = this.manifest;
    var pad3 = pad(fxx, m.fxx_pad || 3);
    if (this.legacyMode) {
      // LEGACY: build from the manifest's own path_template so an older
      // template without a {product} segment still resolves, and KEEP the
      // ?v=generated_at cache-bust (legacy frames are republished per cycle).
      var tmpl = m.path_template || '{model}/{storm}/{domain}/{product}/f{fxx}.png';
      var rel = tmpl
        .replace('{model}', this.model)
        .replace('{storm}', this.storm.id)
        .replace('{domain}', this.domain)
        .replace('{product}', this.product || '')
        .replace('{fxx}', pad3)
        .replace(/\/{2,}/g, '/');
      var u = this.assetBase + rel;
      return this.cacheBust ? (u + '?v=' + this.cacheBust) : u;
    }
    // CYCLES MODE. Container-first: when a block covers this frame, the
    // "URL" is the block object plus a #r=off,len,fxx marker - a stable
    // cache key for the preload maps, resolved by the loader into a ranged
    // fetch (an <img> src cannot carry a Range header). Per-frame PNG
    // otherwise, and as the loader's fallback if the ranged read fails.
    var blk = this._blockFor(fxx);
    if (blk) {
      var btmpl = this.manifest.containers.path_template;
      var brel = btmpl
        .replace('{cycle}', this.cycle.cycle)
        .replace('{model}', this.model)
        .replace('{storm}', this.storm.id)
        .replace('{domain}', this.domain)
        .replace('{product}', this.product || '')
        .replace('{key}', blk.key)
        .replace(/\/{2,}/g, '/');
      return this.assetBase + brel + '#r=' + blk.off + ',' + blk.len + ',' + fxx;
    }
    // Cycle-scoped immutable keys -> path_template_cycles with
    // {cycle} substituted, NO ?v= (the key itself busts on a new cycle).
    var ctmpl = m.path_template_cycles ||
      '{cycle}/{model}/{storm}/{domain}/{product}/f{fxx}.png';
    var crel = ctmpl
      .replace('{cycle}', this.cycle.cycle)
      .replace('{model}', this.model)
      .replace('{storm}', this.storm.id)
      .replace('{domain}', this.domain)
      .replace('{product}', this.product || '')
      .replace('{fxx}', pad3)
      .replace(/\/{2,}/g, '/');
    return this.assetBase + crel;
  };

  // Show the frame at RENDERED index i; update the hour grid + readouts.
  HafsViewer.prototype._show = function (i) {
    if (!this.fxxList.length) return;
    this.idx = Math.max(0, Math.min(i, this.fxxList.length - 1));
    var fxx = this.fxxList[this.idx];
    var url = this._frameUrl(fxx);
    if (url.indexOf('#r=') !== -1) {
      // Ranged frame: an <img> cannot fetch a byte range itself. Use the
      // decoded blob if it's here; otherwise kick the loader - its settle
      // re-shows this index, so the current frame holds (no blank flash)
      // until the bytes land.
      var pre = this.preloaded[url];
      if (pre && pre.src) this.dom.img.src = pre.src;
      else this._preloadUrls([url], this.preloadGen, false);
    } else {
      this.dom.img.src = url;
    }
    this.dom.fhour.textContent = 'F' + pad(fxx, 3);
    // Valid time uses the SELECTED cycle's storm init.
    var init = new Date(this.storm.init);
    var valid = new Date(init.getTime() + fxx * 3600 * 1000);
    this.dom.valid.textContent = 'Valid ' + fmtUTC(valid) +
      '  ·  Init ' + fmtUTC(init);
    this._highlightHour();
    this._updatePill();
    this._renderShearOverlay();
    // Scrubbing (clicks, arrows, playback ticks) rewrites the address bar via
    // debounced replaceState only - 43 forecast hours must never become 43
    // back-button presses.
    this._syncUrl(false);
  };

  // ---- in-progress pill, pre-announce badge, footer --------------------

  // The pill near the cycle text: "building · F036/F126" while the active
  // cycle still renders (max rendered fhr of THIS selection vs fxx_end);
  // hidden once complete.
  HafsViewer.prototype._updatePill = function () {
    var pill = this.dom.pill;
    if (!pill) return;
    if (this.cycle && this.cycle.in_progress) {
      var maxF = this.fxxList.length ? this.fxxList[this.fxxList.length - 1] : 0;
      var end = this.fxxEnd || maxF;
      pill.textContent = 'building · F' + pad(maxF, 3) + '/F' + pad(end, 3);
      pill.style.display = '';
    } else {
      pill.style.display = 'none';
    }
  };

  // The pre-announce / newer-cycle badge. Two distinct states:
  //  - pre-announce: the newest cycle is an empty shell, we picked an older one
  //    -> informational ("18Z run started - first frames soon").
  //  - newer-cycle button: a poll discovered a newer cycle WITH frames mid-
  //    session -> clickable "18Z building - view" that switches on click only.
  HafsViewer.prototype._updateBadge = function () {
    var badge = this.dom.badge;
    if (!badge) return;
    badge.innerHTML = '';
    badge.className = 'hafs-badge';
    if (this.pendingCycleKey) {
      var cyc = this._cycleByKey(this.pendingCycleKey);
      var tag = cycleDayTag(this.pendingCycleKey);
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'hafs-badge-btn';
      b.textContent = tag + (cyc && cyc.in_progress ? ' building' : '') + ' - view';
      var self = this, key = this.pendingCycleKey;
      b.addEventListener('click', function () {
        self._pause();
        self.pendingCycleKey = null;
        self._selectCycle(key, false);
        self._updateBadge();
      });
      badge.appendChild(b);
      badge.style.display = '';
    } else if (this.preAnnounce) {
      // Find the empty newest cycle to name it.
      var newestKey = this.cycles.length ? this.cycles[0].cycle : '';
      var span = document.createElement('span');
      span.className = 'hafs-preannounce';
      span.textContent = cycleDayTag(newestKey) + ' run started - first frames soon';
      badge.appendChild(span);
      badge.style.display = '';
    } else {
      badge.style.display = 'none';
    }
  };

  HafsViewer.prototype._updateFooter = function () {
    var bits = [];
    var key = this.cycle ? this.cycle.cycle : (this.manifest && this.manifest.cycle);
    if (key) bits.push('Cycle ' + fmtCycle(key));
    if (this.manifest && this.manifest.generated_at) {
      bits.push('Rendered ' + this.manifest.generated_at.replace('T', ' '));
    }
    if (this.cycle && this.cycle.in_progress &&
        typeof this.cycle.frames_done === 'number' &&
        typeof this.cycle.frames_expected === 'number') {
      bits.push('Building (' + this.cycle.frames_done + '/' +
                this.cycle.frames_expected + ' frames)');
    }
    if (this.dom.meta) this.dom.meta.textContent = bits.join('  ·  ');
    this._updateBadge();
    this._updatePill();
    this._updateStale();
    this._updateShearBtn();
  };

  // HAFS runs every 6 h. If the newest cycle we can show is much older than
  // that, the renderer has stopped and the page must SAY so -- on 2026-07-19
  // the cron was gated off for a box worker that never started, and /models/
  // then presented an 8-day-old cycle as the current run for eight days. The
  // age is derived from the cycle's own init time, not from a publish stamp,
  // so it stays honest even if a manifest is rewritten without new frames.
  HafsViewer.prototype._updateStale = function () {
    var el = this.dom.stale;
    if (!el) return;
    var key = this.cycle ? this.cycle.cycle : (this.manifest && this.manifest.cycle);
    var t = key && parseCycleUTC(key);
    if (!t) { el.style.display = 'none'; return; }
    var ageH = (Date.now() - t.getTime()) / 3600000;
    if (ageH < STALE_WARN_H) { el.style.display = 'none'; return; }
    var dead = ageH >= STALE_DEAD_H;
    var age = ageH < 48 ? Math.round(ageH) + ' hours'
                        : (ageH / 24).toFixed(1) + ' days';
    el.className = 'hafs-stale' + (dead ? ' dead' : '');
    el.innerHTML = dead
      ? '<b>These runs are ' + age + ' old.</b> HAFS initialises every 6 hours, ' +
        'so the renderer has stopped publishing and nothing here reflects the ' +
        'current forecast. Storms that formed since are missing entirely.'
      : '<b>Latest run is ' + age + ' old.</b> HAFS initialises every 6 hours; ' +
        'a newer cycle is late.';
    el.style.display = '';
  };

  // Caption describing the active product's shading. No em-dashes.
  HafsViewer.prototype._updateCaption = function () {
    var c = this.dom.caption;
    if (!c) return;
    if (this.product === 'refl') {
      c.textContent =
        'Filled shading: composite (column-maximum) radar reflectivity (dBZ), ' +
        'stepped TAT radar palette (light blue/green light returns, through ' +
        'yellow/orange/red heavy convection, to magenta extreme cores). ' +
        'Non-precip areas (below 10 dBZ) are transparent, showing the dark map ' +
        'and coastlines. White contours: MSLP every 4 hPa. The storm-nest ' +
        'domain follows the cyclone, so playback is roughly storm-centered.';
    } else {
      c.textContent =
        'Filled shading: 10 m wind speed (kt), Saffir-Simpson-flavored palette ' +
        '(calm, to green TS, to gold/orange Cat 1-2, to red/magenta Cat 3-4, to ' +
        'violet Cat 5). White barbs: 10 m wind. White contours: MSLP every ' +
        '4 hPa. The storm-nest domain follows the cyclone, so playback is ' +
        'roughly storm-centered.';
    }
  };

  // Preload every RENDERED frame of the current selection so stepping and
  // playback are smooth. A generation token makes a new selection supersede any in-flight
  // preload (stale onload callbacks are ignored), and the decoded-image cache
  // is reset each selection so a long session can't grow it without bound.
  HafsViewer.prototype._preloadAll = function () {
    var self = this;
    var gen = ++this.preloadGen;
    this.preloaded = {};
    this.ready = {};
    // Ranged frames hold blob object URLs - release them with the selection
    // or every selection change leaks the decoded bytes.
    if (this._blobUrls) {
      for (var bu in this._blobUrls) {
        try { URL.revokeObjectURL(this._blobUrls[bu]); } catch (e) { /* gone */ }
      }
    }
    this._blobUrls = {};
    this._vpCache = {};
    this._preloadUrls(this.fxxList.map(function (f) { return self._frameUrl(f); }), gen, true);
  };

  // Preload a list of frame URLs under generation token `gen`. When `reset` is
  // true the buffer counter restarts from 0/total (fresh selection); otherwise
  // it ADDS newly-discovered frames to the running counter (diff-merge).
  HafsViewer.prototype._preloadUrls = function (urls, gen, reset) {
    var self = this;
    urls = urls.filter(function (u) { return !self.preloaded[u]; });
    if (reset) { this._bufTotal = 0; this._bufDone = 0; }
    this._bufTotal = (this._bufTotal || 0) + urls.length;
    if (!this._bufTotal) { this.dom.buffer.style.display = 'none'; return; }
    if (!urls.length) {
      if ((this._bufDone || 0) >= this._bufTotal) this.dom.buffer.style.display = 'none';
      return;
    }
    this.dom.buffer.style.display = 'block';
    this.dom.buffer.textContent = 'Buffering ' + (this._bufDone || 0) + '/' + this._bufTotal;
    urls.forEach(function (u) {
      var im = new Image();
      im.decoding = 'async';
      // Settle each frame exactly once (decoded OR errored) so the buffer
      // readout always completes. `ready[u]` flips ONLY on a real decode, so
      // the playback loop can skip a frame that hasn't decoded yet instead of
      // swapping in a half-loaded image (the pop-in the old direct-src had).
      function settle(ok) {
        if (gen !== self.preloadGen) return;   // superseded by a newer selection
        self.preloaded[u] = im;
        if (ok) self.ready[u] = true;
        if (ok && u.indexOf('#r=') !== -1 && self.fxxList.length &&
            self._frameUrl(self.fxxList[self.idx]) === u) {
          self.dom.img.src = im.src;       // the frame being waited on
        }
        self._bufDone = (self._bufDone || 0) + 1;
        self.dom.buffer.textContent = 'Buffering ' + self._bufDone + '/' + self._bufTotal;
        if (self._bufDone >= self._bufTotal) self.dom.buffer.style.display = 'none';
      }
      im.onload = function () {
        // Decode off the main thread before marking ready, so the first paint
        // of this frame is an instant cached-bitmap swap (no decode hitch).
        if (im.decode) { im.decode().then(function () { settle(true); },
                                          function () { settle(true); }); }
        else { settle(true); }
      };
      im.onerror = function () { settle(false); };
      var r = u.indexOf('#r=');
      if (r === -1) {
        im.src = u;
      } else {
        // Container member: ranged fetch -> blob -> object URL. On ANY
        // failure fall back to the per-frame PNG (dual-published), so a
        // missing tar or a CORS surprise degrades to exactly the old path.
        var base = u.slice(0, r);
        var q = u.slice(r + 3).split(',');
        var off = +q[0], len = +q[1], ffxx = +q[2];
        fetch(base, { headers: { Range: 'bytes=' + off + '-' + (off + len - 1) } })
          .then(function (resp) {
            if (resp.status !== 206 && !resp.ok) throw new Error('http ' + resp.status);
            return resp.blob();
          })
          .then(function (blob) {
            if (gen !== self.preloadGen) return;
            self._blobUrls = self._blobUrls || {};
            self._blobUrls[u] = URL.createObjectURL(blob);
            im.src = self._blobUrls[u];
          })
          .catch(function () {
            im.src = base.replace(/b[0-9]+-[0-9]+\.tar$/,
                                  'f' + pad(ffxx, 3) + '.png');
          });
      }
    });
  };

  // Step by `delta` RENDERED frames (wrapping over the rendered prefix only).
  HafsViewer.prototype._step = function (delta) {
    if (!this.fxxList.length) return;
    var n = this.fxxList.length;
    this._show((this.idx + delta + n) % n);
  };

  HafsViewer.prototype._togglePlay = function () {
    this.playing ? this._pause() : this._play();
  };

  // Per-frame dwell (ms) at the current speed. Read fresh every tick so a
  // speed change applies on the very next frame with no loop restart.
  HafsViewer.prototype._frameMs = function () {
    return 1000 / (BASE_FPS * (this.speed || 1));
  };

  // Has frame `i` (index into fxxList) DECODED? Playback only ever lands on
  // decoded frames, so the swap is always an instant cached-bitmap paint.
  HafsViewer.prototype._frameReady = function (i) {
    var fxx = this.fxxList[i];
    if (fxx === undefined) return false;
    return !!this.ready[this._frameUrl(fxx)];
  };

  // Advance to the next DECODED frame over the rendered prefix (wrapping),
  // skipping any still-loading frame. If nothing else has decoded yet we hold
  // on the current frame rather than flash a half-loaded one.
  HafsViewer.prototype._advance = function () {
    var n = this.fxxList.length;
    if (!n) return;
    var j = this.idx;
    for (var s = 0; s < n; s++) {
      j = (j + 1) % n;
      if (this._frameReady(j)) { this._show(j); return; }
    }
  };

  // The whole pacing engine (the satellite sat-simple dumb-player pattern):
  // one rAF loop, advance when enough wall-clock has elapsed. Don't reintroduce
  // setInterval — it drifts off vsync and stutters under load.
  HafsViewer.prototype._tick = function (ts) {
    if (!this.playing) return;
    if (ts - this.lastTick >= this._frameMs()) {
      this.lastTick = ts;
      this._advance();
    }
    var self = this;
    this.raf = requestAnimationFrame(function (t) { self._tick(t); });
  };

  HafsViewer.prototype._play = function () {
    if (this.fxxList.length <= 1) return;
    this.playing = true;
    this.dom.play.textContent = '❚❚ Pause';
    this.lastTick = 0;
    if (this.raf) cancelAnimationFrame(this.raf);
    var self = this;
    this.raf = requestAnimationFrame(function (t) { self._tick(t); });
  };

  HafsViewer.prototype._pause = function () {
    this.playing = false;
    this.dom.play.textContent = '► Play';
    if (this.raf) { cancelAnimationFrame(this.raf); this.raf = null; }
  };

  // ---- manifest poll + diff-merge --------------------------------------

  HafsViewer.prototype._anyInProgress = function () {
    for (var i = 0; i < this.cycles.length; i++) {
      if (this.cycles[i].in_progress) return true;
    }
    return false;
  };

  HafsViewer.prototype._schedulePoll = function () {
    clearTimeout(this.pollTimer);
    var self = this;
    var delay = this._anyInProgress() ? POLL_IN_PROGRESS_MS : POLL_IDLE_MS;
    this.pollTimer = setTimeout(function () { self._poll(); }, delay);
  };

  HafsViewer.prototype._poll = function () {
    var self = this;
    this._fetchManifest()
      .then(function (m) { self._mergeManifest(m); })
      .catch(function (e) { console.warn('hafs: poll failed', e); })
      .then(function () { self._schedulePoll(); });
  };

  // Diff-merge a freshly polled manifest into live state WITHOUT resetting the
  // user's selection. Grows frame lists in place (relighting hour buttons,
  // preloading the new frames), refreshes the in-progress flags / footer, and
  // arms the "newer cycle" badge - but never yanks the active
  // cycle/storm/.../hour.
  HafsViewer.prototype._mergeManifest = function (m) {
    if (!m) return;
    var hadStorms = !!(this.cycle && this.cycle.storms && this.cycle.storms.length);
    var prevCycleKey = this.cycle ? this.cycle.cycle : null;
    this.manifest = m;
    this.cacheBust = m.generated_at ? encodeURIComponent(m.generated_at) : '';
    this.fxxStep = m.fxx_step || this.fxxStep;
    if (typeof m.fxx_end === 'number') this.fxxEnd = m.fxx_end;

    var newCycles = this._normalizeCycles(m);

    // If we have no active selection yet (e.g. the page came up off-season),
    // fall back to a full (re)init so a first storm can appear.
    if (!prevCycleKey || !hadStorms) {
      this._onManifest(m);
      return;
    }

    // A cycle whose key isn't currently selected and is NEWER than the active
    // one (newest-first ordering => lower index) and has frames -> badge it.
    // This scan is authoritative each poll: it re-derives the single newest
    // pending cycle, so a stale badge self-clears when its cycle ages out or
    // becomes the active one.
    var newPending = null;
    for (var i = 0; i < newCycles.length; i++) {
      var key = newCycles[i].cycle;
      if (key === prevCycleKey) break;   // reached the active cycle; stop
      if (this._cycleHasFramesOn(newCycles[i])) { newPending = key; break; }
    }

    this.cycles = newCycles;
    var sameCycle = this._cycleByKey(prevCycleKey);
    if (!sameCycle) {
      // The active cycle aged out of the manifest (rare) - re-default cleanly.
      this._onManifest(m);
      return;
    }

    // Re-point the live selection at the refreshed cycle/storm objects and grow
    // the frame list for the CURRENT selection.
    this.cycle = sameCycle;
    var freshStorm = null, storms = sameCycle.storms || [];
    for (var s = 0; s < storms.length; s++) if (storms[s].id === (this.storm && this.storm.id)) freshStorm = storms[s];

    if (freshStorm) {
      this.storm = freshStorm;
      this._regrowCurrentSelection();
    } else {
      // The selected storm vanished from the cycle - keep cycle, reselect its
      // first storm (rare; e.g. invest dropped). Preserves cycle, not hour.
      this._selectStorm(storms[0] && storms[0].id, false);
    }

    // Authoritative: a fresh poll re-derives the single newest pending cycle,
    // so an unacknowledged badge stays, an aged-out one self-clears.
    this.pendingCycleKey = newPending;
    // Rebuild the cycle picker if the cycle SET changed (count or keys).
    this._buildCyclePicker();
    this._highlight(this.dom.cycles, this.cycle.cycle);
    this._updateFooter();
    // (re)scheduling of the next poll is owned by _poll()'s finally chain.
  };

  HafsViewer.prototype._cycleHasFramesOn = function (cyc) {
    return this._cycleHasFrames(cyc);
  };

  // Grow the CURRENT selection's rendered-frame list from the refreshed storm,
  // relight hour buttons, preload the new frames - keeping idx pinned to the
  // same forecast HOUR (or its nearest rendered neighbor if it is still
  // pending).
  HafsViewer.prototype._regrowCurrentSelection = function () {
    var curF = this.fxxList.length ? this.fxxList[Math.min(this.idx, this.fxxList.length - 1)] : 0;
    var oldUrls = {};
    var self = this;
    this.fxxList.forEach(function (f) { oldUrls[self._frameUrl(f)] = true; });

    var fr = this._domFrames(this.storm, this.model, this.domain)[this.product] || [];
    var newList = fr.slice().sort(function (a, b) { return a - b; });

    // If the option SET for the current selection disappeared (e.g. the product
    // is no longer present), fall back to re-deriving pickers for this storm
    // while keeping cycle/storm; preserves the rest where possible.
    if (!newList.length) {
      this._selectStorm(this.storm.id, true);
      return;
    }

    this.fxxList = newList;
    this.fxxGrid = this._buildGrid(this.fxxList);
    var exact = this.fxxList.indexOf(curF);
    this.idx = exact >= 0 ? exact : this._renderedIndexNear(curF);
    this.idx = Math.max(0, Math.min(this.idx, this.fxxList.length - 1));

    this._buildHourGrid();
    this._show(this.idx);

    // Preload only the NEWLY-rendered frames (diff), under a fresh-enough gen.
    var newUrls = this.fxxList
      .map(function (f) { return self._frameUrl(f); })
      .filter(function (u) { return !oldUrls[u]; });
    if (newUrls.length) this._preloadUrls(newUrls, this.preloadGen, false);
  };

  // ---- orthogonal URL scheme + history policy (item 18) -------------------
  // Params: run, storm, model, domain, product, fxx, mode - each independent,
  // so any view is shareable and restorable. HISTORY POLICY: pushState only on
  // run/storm/model/domain/product/mode (real navigation); the fxx scrub uses
  // ---- interactive readouts (spec #28) ---------------------------------
  // Lat/lon under the cursor, a two-click ruler, and a VALUE readout sampled
  // from the frame's value plane - an 8-bit grey PNG member riding the same
  // container block as the frame (zero extra object writes). The plane's
  // extent IS the frame's published geometry bbox, so one affine serves the
  // raster, the plane, and the ruler. Decode contract: effective_step =
  // step * 2^k, k = smallest int with (vmax-vmin)/(step*2^k) <= 254;
  // raw 0 = no data; value = vmin + (raw-1) * effective_step.

  HafsViewer.prototype._geoFor = function (fxx) {
    var g = this.storm && this.storm.geometry;
    var d = g && g[this.model] && g[this.model][this.domain];
    return (d && d[String(fxx)]) || null;
  };

  // Cursor event -> {lon, lat} in the frame's CONTINUOUS lon frame, or null
  // outside the axes. All display normalisation happens at format time.
  HafsViewer.prototype._cursorGeo = function (ev) {
    if (!this.fxxList.length) return null;
    var geo = this._geoFor(this.fxxList[this.idx]);
    var img = this.dom.img;
    if (!geo || !geo.axes_px || !img.naturalWidth || !img.clientWidth) return null;
    var r = img.getBoundingClientRect();
    var sc = img.naturalWidth / r.width;
    var px = (ev.clientX - r.left) * sc, py = (ev.clientY - r.top) * sc;
    var ax = geo.axes_px, bb = geo.bbox;
    if (px < ax[0] || px > ax[0] + ax[2] || py < ax[1] || py > ax[1] + ax[3]) return null;
    return {
      lon: bb[0] + (px - ax[0]) / ax[2] * (bb[2] - bb[0]),
      lat: bb[3] - (py - ax[1]) / ax[3] * (bb[3] - bb[1]),
      geo: geo
    };
  };

  function fmtLon(v) {
    while (v > 180) v -= 360;
    while (v < -180) v += 360;
    return Math.abs(v).toFixed(2) + '\u00b0' + (v >= 0 ? 'E' : 'W');
  }
  function fmtLat(v) { return Math.abs(v).toFixed(2) + '\u00b0' + (v >= 0 ? 'N' : 'S'); }

  // Great-circle distance (km) + initial bearing (deg) - lon deltas
  // normalised first (the antimeridian rule).
  function greatCircle(a, b) {
    var R = 6371.0, D = Math.PI / 180;
    var dLon = ((b.lon - a.lon + 540) % 360 - 180) * D;
    var la1 = a.lat * D, la2 = b.lat * D, dLat = (b.lat - a.lat) * D;
    var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(la1) * Math.cos(la2) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
    var d = 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
    var brg = Math.atan2(Math.sin(dLon) * Math.cos(la2),
                         Math.cos(la1) * Math.sin(la2) -
                         Math.sin(la1) * Math.cos(la2) * Math.cos(dLon)) / D;
    return { km: d, brg: (brg + 360) % 360 };
  }

  // Value plane: lazy ranged fetch of the f{fxx}.values.png member from the
  // SAME block the frame lives in; decoded once per frame into ImageData.
  HafsViewer.prototype._valuePlane = function (fxx, cb) {
    var self = this;
    this._vpCache = this._vpCache || {};
    var ck = [this.cycle && this.cycle.cycle, this.model, this.domain,
              this.product, fxx].join('|');
    if (ck in this._vpCache) { cb(this._vpCache[ck]); return; }
    var st = this.storm, name = 'f' + pad(fxx, 3) + '.values.png';
    var arr = st && st.blocks && st.blocks[this.model] &&
              st.blocks[this.model][this.domain] &&
              st.blocks[this.model][this.domain][this.product];
    var blk = null, i;
    if (arr) for (i = 0; i < arr.length; i++) {
      if (arr[i].members && arr[i].members[name]) { blk = arr[i]; break; }
    }
    if (!blk) { this._vpCache[ck] = null; cb(null); return; }
    var mem = blk.members[name];
    var tmpl = this.manifest.containers.path_template;
    var url = this.assetBase + tmpl
      .replace('{cycle}', this.cycle.cycle).replace('{model}', this.model)
      .replace('{storm}', st.id).replace('{domain}', this.domain)
      .replace('{product}', this.product || '').replace('{key}', blk.key)
      .replace(/\/{2,}/g, '/');
    fetch(url, { headers: { Range: 'bytes=' + mem[0] + '-' + (mem[0] + mem[1] - 1) } })
      .then(function (r) {
        if (r.status !== 206 && !r.ok) throw new Error('http ' + r.status);
        return r.blob();
      })
      .then(function (b) { return createImageBitmap(b); })
      .then(function (bm) {
        var cv = document.createElement('canvas');
        cv.width = bm.width; cv.height = bm.height;
        var cx = cv.getContext('2d');
        cx.drawImage(bm, 0, 0);
        var id = cx.getImageData(0, 0, bm.width, bm.height);
        bm.close && bm.close();
        self._vpCache[ck] = { d: id.data, w: id.width, h: id.height };
        cb(self._vpCache[ck]);
      })
      .catch(function () { self._vpCache[ck] = null; cb(null); });
  };

  HafsViewer.prototype._sampleValue = function (pos, plane) {
    if (!plane) return null;
    var bb = pos.geo.bbox;
    var col = Math.floor((pos.lon - bb[0]) / (bb[2] - bb[0]) * plane.w);
    var row = Math.floor((bb[3] - pos.lat) / (bb[3] - bb[1]) * plane.h);
    if (col < 0 || col >= plane.w || row < 0 || row >= plane.h) return null;
    var raw = plane.d[(row * plane.w + col) * 4];
    if (!raw) return null;
    var pm = (this.manifest.products || []).filter(
      function (x) { return x.slug === this; }, this.product)[0];
    var q = pm && this.manifest.quantities &&
            this.manifest.quantities[pm.quantity];
    if (!q) return null;
    var vmin = q.vmin, vmax = q.vmax;
    if (vmin == null || vmax == null) {
      if (!q.value_range) return null;
      vmin = q.value_range[0]; vmax = q.value_range[1];
    }
    var step = q.step || 1.0;
    while ((vmax - vmin) / step > 254) step *= 2;
    var v = vmin + (raw - 1) * step;
    return (step < 1 ? v.toFixed(1) : Math.round(v)) + '\u2009' + (q.units || '');
  };

  HafsViewer.prototype._readoutEl = function () {
    if (this._roEl || typeof document === 'undefined') return this._roEl;
    var el = document.createElement('div');
    el.className = 'hafs-readout-chip';
    el.style.display = 'none';
    this.dom.stage.appendChild(el);
    this._roEl = el;
    return el;
  };

  HafsViewer.prototype._wireReadout = function () {
    var self = this;
    var img = this.dom.img;
    if (!img || !img.addEventListener) return;
    img.addEventListener('pointermove', function (ev) {
      var el = self._readoutEl();
      var pos = self._cursorGeo(ev);
      if (!pos) { if (!self._rulerA) el.style.display = 'none'; return; }
      var fxx = self.fxxList[self.idx];
      var base = fmtLat(pos.lat) + ' ' + fmtLon(pos.lon);
      if (self._rulerA) {
        var gc = greatCircle(self._rulerA, pos);
        el.textContent = base + '  \u00b7  ' + Math.round(gc.km) + ' km / ' +
          Math.round(gc.km * 0.5399568) + ' nm @ ' + Math.round(gc.brg) + '\u00b0';
        el.style.display = '';
        return;
      }
      self._valuePlane(fxx, function (plane) {
        if (self.fxxList[self.idx] !== fxx) return;   // frame moved on
        var v = self._sampleValue(pos, plane);
        el.textContent = base + (v ? '  \u00b7  ' + v : '');
        el.style.display = '';
      });
    });
    img.addEventListener('pointerleave', function () {
      if (self._roEl && !self._rulerA) self._roEl.style.display = 'none';
    });
    img.addEventListener('click', function (ev) {
      if (!self._rulerMode) return;
      var pos = self._cursorGeo(ev);
      if (!pos) return;
      if (!self._rulerA) { self._rulerA = { lon: pos.lon, lat: pos.lat }; }
      else { self._rulerMode = false; self._rulerA = null;
             if (self._rulerBtn) self._rulerBtn.classList.remove('on'); }
    });
  };

  HafsViewer.prototype._wireRulerBtn = function () {
    if (this._rulerBtn || !this.dom.player ||
        typeof document === 'undefined' ||
        typeof document.createElement !== 'function') return;
    var self = this;
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'hafs-btn hafs-ruler-btn';
    b.textContent = 'Ruler';
    b.title = 'Two clicks: great-circle distance + bearing (Esc cancels)';
    b.addEventListener('click', function () {
      self._rulerMode = !self._rulerMode;
      self._rulerA = null;
      b.classList.toggle('on', self._rulerMode);
    });
    this.dom.player.appendChild(b);
    this._rulerBtn = b;
  };

  // ---- shear-relative view (spec #4) -----------------------------------
  // A display TOGGLE on the existing storm-centred frames, not a new render:
  // the raster stays north-up and an SVG overlay draws the shear vector and
  // the shear-relative quadrant frame on top, rotated to the published
  // vortex-removed heading (spec #26's number, storms[].shear in the
  // manifest). Quadrants are defined PURELY by rotation from the shear
  // vector - DR/DL forward of the perpendicular, UR/UL rearward, left/right
  // as seen looking downshear - so the geometry is identical in both
  // hemispheres. The hemisphere-dependent PHYSICS (convective max downshear-
  // LEFT in the NH, downshear-RIGHT in the SH) lives ONLY in the chip text,
  // stating both. TAT serves SHEM: no NH assumption in any label or order.

  HafsViewer.prototype._shearHours = function () {
    var sh = this.storm && this.storm.shear;
    if (!sh || !sh.hours) return null;
    var h = sh.hours[this.model];
    for (var k in h) { if (h.hasOwnProperty(k)) return h; }
    return null;
  };

  HafsViewer.prototype._shearFor = function (fxx) {
    var h = this._shearHours();
    return (h && h[String(fxx)]) || null;
  };

  // Storm-centred (nest) products only: the overlay centres on the axes
  // centre, which is where the storm-following nest holds the vortex. On the
  // parent domain the storm is wherever it is - no overlay there.
  HafsViewer.prototype._shearEligible = function () {
    return this.domain === 'storm' && !!this._shearHours();
  };

  HafsViewer.prototype._toggleShear = function () {
    if (!this._shearEligible()) return;
    this.shearView = !this.shearView;
    this._updateShearBtn();
    this._renderShearOverlay();
    this._syncUrl(false);       // display state: replaceState, never a history entry
  };

  HafsViewer.prototype._updateShearBtn = function () {
    var el = this._shearBtn;
    if (!this._shearEligible()) {
      if (el) el.style.display = 'none';
      if (this.shearView) this._renderShearOverlay();   // hides the overlay
      return;
    }
    if (!el) {
      if (!this.dom.player || typeof document === 'undefined' ||
          typeof document.createElement !== 'function') return;
      var self = this;
      el = document.createElement('button');
      el.type = 'button';
      el.className = 'hafs-btn hafs-shear-btn';
      el.textContent = 'Shear view';
      el.title = 'Shear-relative overlay: quadrant frame + shear vector (S)';
      el.setAttribute('aria-pressed', 'false');
      el.addEventListener('click', function () { self._toggleShear(); });
      this.dom.player.appendChild(el);
      this._shearBtn = el;
    }
    el.style.display = '';
    el.classList.toggle('on', !!this.shearView);
    el.setAttribute('aria-pressed', this.shearView ? 'true' : 'false');
  };

  HafsViewer.prototype._renderShearOverlay = function () {
    var ov = this._shearOv;
    var on = this.shearView && this._shearEligible();
    if (!on) { if (ov) ov.style.display = 'none'; return; }
    if (typeof document === 'undefined' ||
        typeof document.createElement !== 'function') return;
    if (!ov) {
      ov = document.createElement('div');
      ov.className = 'hafs-shear-ov';
      this.dom.stage.appendChild(ov);
      this._shearOv = ov;
      // The overlay is positioned in DISPLAYED pixels: track the img size.
      var self = this;
      if (typeof window !== 'undefined' && window.addEventListener) {
        window.addEventListener('resize', function () {
          if (self._shearRz) clearTimeout(self._shearRz);
          self._shearRz = setTimeout(function () {
            self._renderShearOverlay();
          }, 150);
        });
      }
    }
    var img = this.dom.img;
    var natW = img.naturalWidth, natH = img.naturalHeight;
    var dw = img.clientWidth, dh = img.clientHeight;
    if (!natW || !dw) {
      // Frame not decoded yet (first paint of a fresh selection): draw once
      // it lands rather than leaving the overlay stale-hidden.
      ov.style.display = 'none';
      if (!this._shearOnLoad && img.addEventListener) {
        var v = this;
        this._shearOnLoad = function () { v._renderShearOverlay(); };
        img.addEventListener('load', this._shearOnLoad);
      }
      return;
    }
    ov.style.display = '';
    ov.style.left = img.offsetLeft + 'px';
    ov.style.top = img.offsetTop + 'px';
    ov.style.width = dw + 'px';
    ov.style.height = dh + 'px';

    var fxx = this.fxxList[this.idx];
    var val = this._shearFor(fxx);
    var g = this.storm.geometry;
    var geo = g && g[this.model] && g[this.model][this.domain] &&
              g[this.model][this.domain][String(fxx)];
    var scale = dw / natW;

    if (!val || !geo || !geo.axes_px || !geo.bbox) {
      // Honest degradation: no number for this hour (no model vortex fix, or
      // a pre-geometry frame) -> say so, draw no geometry.
      ov.innerHTML = '<div class="hafs-shear-chip" style="left:12px;bottom:12px">' +
        'No shear diagnostic at F' + pad(fxx, 3) +
        (val ? '' : ' — no model vortex fix this hour') + '</div>';
      return;
    }

    var ax = geo.axes_px, bb = geo.bbox;
    var cx = ax[0] + ax[2] / 2, cy = ax[1] + ax[3] / 2;
    // px per degree on the equirectangular axes: X and Y differ (~cos lat),
    // so a geographic bearing must be mapped through both scales or every
    // angle would be visibly wrong off the equator.
    var kx = ax[2] / (bb[2] - bb[0]), ky = ax[3] / (bb[3] - bb[1]);
    function dir(hdg) {
      var r = hdg * Math.PI / 180;
      var dx = Math.sin(r) * kx, dy = -Math.cos(r) * ky;
      var L = Math.sqrt(dx * dx + dy * dy) || 1;
      return [dx / L, dy / L];
    }
    var R = 0.42 * Math.min(ax[2], ax[3]);
    var hdg = val.hdg;
    function pt(a, rr) { var d = dir(a); return [cx + d[0] * rr, cy + d[1] * rr]; }
    function lineEl(a, dash) {
      var p1 = pt(a + 180, R), p2 = pt(a, R);
      return '<line x1="' + p1[0] + '" y1="' + p1[1] + '" x2="' + p2[0] +
        '" y2="' + p2[1] + '" class="sh-ln' + (dash ? ' sh-dash' : '') + '"/>';
    }
    // Arrowhead: screen-perpendicular of the shear line's screen direction.
    var d0 = dir(hdg);
    var tip = [cx + d0[0] * R, cy + d0[1] * R];
    var pd = [-d0[1], d0[0]];
    var ah = 0.055 * R, aw = 0.038 * R;
    var head = '<polygon class="sh-head" points="' +
      tip[0] + ',' + tip[1] + ' ' +
      (tip[0] - d0[0] * ah + pd[0] * aw) + ',' + (tip[1] - d0[1] * ah + pd[1] * aw) + ' ' +
      (tip[0] - d0[0] * ah - pd[0] * aw) + ',' + (tip[1] - d0[1] * ah - pd[1] * aw) + '"/>';
    // Quadrant labels: pure rotation from the shear vector. Looking
    // downshear, LEFT = heading - 90; the four quadrant midlines follow.
    var labels = [['DL', -45], ['DR', 45], ['UL', -135], ['UR', 135]]
      .map(function (q) {
        var p = pt(hdg + q[1], 0.8 * R);
        return '<text x="' + p[0] + '" y="' + p[1] + '" class="sh-lab">' +
          q[0] + '</text>';
      }).join('');
    var svg =
      '<svg viewBox="0 0 ' + natW + ' ' + natH + '" width="' + dw +
      '" height="' + dh + '" aria-hidden="true">' +
      lineEl(hdg, false) + lineEl(hdg + 90, true) + head + labels + '</svg>';
    var chip =
      '<div class="hafs-shear-chip" style="left:' +
      Math.round(ax[0] * scale + 10) + 'px;top:' +
      Math.round((ax[1] + ax[3]) * scale - 10) + 'px;transform:translateY(-100%)">' +
      '<b>Shear ' + val.kt + ' kt @ ' + Math.round(val.hdg) + '°</b>' +
      ' · naive ' + val.naive_kt + ' kt' +
      '<br>vortex-removed · 850–200 hPa · 0–500 km · model’s own centre' +
      '<br>Quadrants are shear-relative geometry. Convection favours ' +
      'downshear-<b>left</b> in the NH, downshear-<b>right</b> in the SH.' +
      '</div>';
    ov.innerHTML = svg + chip;
  };

  // replaceState debounced 250 ms, because 43 forecast hours must never become
  // 43 back-button presses.

  HafsViewer.prototype._readUrl = function () {
    try {
      var q = new URLSearchParams(location.search);
      var out = {};
      ['run', 'storm', 'model', 'domain', 'product', 'fxx', 'mode', 'shear']
        .forEach(function (k) { if (q.get(k)) out[k] = q.get(k); });
      return out;
    } catch (e) { return {}; }
  };

  // Consume a boot/restore override once: each selection level takes its own
  // param as the chain runs, then it is gone - a later user action must never
  // be fought by a stale URL value.
  HafsViewer.prototype._urlTake = function (key) {
    if (!this._urlBoot || this._urlBoot[key] == null) return null;
    var v = this._urlBoot[key];
    delete this._urlBoot[key];
    return v;
  };

  HafsViewer.prototype._urlQuery = function () {
    var p = [];
    function add(k, v) { if (v != null && v !== '') p.push(k + '=' + encodeURIComponent(v)); }
    add('run', this.cycle && this.cycle.cycle);
    add('storm', this.storm && this.storm.id);
    add('model', this.model);
    add('domain', this.domain);
    add('product', this.product);
    if (this.fxxList.length) add('fxx', this.fxxList[this.idx]);
    if (this.playing) add('mode', 'play');
    if (this.shearView) add('shear', '1');
    return p.join('&');
  };

  HafsViewer.prototype._syncUrl = function (push) {
    if (!this.urlSync || !this.storm) return;
    var self = this;
    var q = this._urlQuery();
    var target = location.pathname + (q ? '?' + q : '') + location.hash;
    var current = location.pathname + location.search + location.hash;
    if (target === current) return;
    if (push) {
      if (this._urlTimer) { clearTimeout(this._urlTimer); this._urlTimer = null; }
      try { history.pushState(null, '', target); } catch (e) { /* sandboxed */ }
      return;
    }
    // Scrub path: debounced replaceState. Never history entries.
    if (this._urlTimer) clearTimeout(this._urlTimer);
    this._urlTimer = setTimeout(function () {
      self._urlTimer = null;
      var q2 = self._urlQuery();
      var t2 = location.pathname + (q2 ? '?' + q2 : '') + location.hash;
      if (t2 !== location.pathname + location.search + location.hash) {
        try { history.replaceState(null, '', t2); } catch (e) { /* sandboxed */ }
      }
    }, 250);
  };

  // Back/forward: re-run the selection chain from the URL, silently (the
  // restore itself must not push).
  HafsViewer.prototype._applyUrlState = function () {
    if (!this.urlSync || !this.cycles.length) return;
    this._urlBoot = this._readUrl();
    var run = this._urlTake('run');
    var key = (run && this._cycleByKey(run)) ? run : this._defaultCycleKey();
    this._pause();
    this._selectCycle(key, false, false);
  };

  // A small transient notice (expired shared runs, keyboard feedback). One
  // element, reused; fades on a timer; never blocks anything.
  HafsViewer.prototype._toast = function (msg) {
    var host = this.dom.stage;
    if (!host) return;
    if (!this._toastEl) {
      this._toastEl = document.createElement('div');
      this._toastEl.className = 'hafs-toast';
      host.appendChild(this._toastEl);
    }
    var t = this._toastEl;
    t.textContent = msg;
    t.classList.add('show');
    if (this._toastTimer) clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(function () { t.classList.remove('show'); }, 3200);
  };

  HafsViewer.prototype._wire = function () {
    var self = this;
    this.dom.stormSel.addEventListener('change', function () {
      self._pause(); self._selectStorm(this.value, false);
      self._syncUrl(true);      // storm switch = navigation
    });
    this.dom.play.addEventListener('click', function () {
      self._togglePlay();
      self._syncUrl(true);      // mode change = navigation (spec: push on mode)
    });
    this.dom.stepB.addEventListener('click', function () { self._pause(); self._step(-1); });
    this.dom.stepF.addEventListener('click', function () { self._pause(); self._step(1); });

    // Speed selector.
    var sp = this.dom.speed;
    for (var i = 0; i < SPEED_OPTIONS.length; i++) {
      var o = document.createElement('option');
      o.value = SPEED_OPTIONS[i];
      o.textContent = SPEED_OPTIONS[i] + '×';
      if (SPEED_OPTIONS[i] === 1) o.selected = true;
      sp.appendChild(o);
    }
    // Back/forward restores the full view from the URL, silently - the
    // restore itself must never push (that would trap the user in a loop).
    if (this.urlSync && typeof window !== 'undefined') {
      window.addEventListener('popstate', function () {
        self._applyUrlState();
      });
    }

    sp.addEventListener('change', function () {
      self.speed = parseFloat(this.value);
      // _tick reads _frameMs() fresh each frame, so the new cadence applies on
      // the next tick — just reset the threshold so it takes effect at once.
      // (Calling _play() here would start a SECOND rAF loop = double speed.)
      if (self.playing) self.lastTick = 0;
    });

    this._wireKeyboard();
    this._wireReadout();
    this._wireRulerBtn();
  };

  // ---- expert keyboard layer (item 9) -------------------------------------
  // Left/right for forecast hour is universal across the class; UP/DOWN for
  // RUN TREND - the SAME VALID TIME across successive inits - is the analyst
  // move: hold the moment fixed and watch how the forecast for it changed
  // run over run. Plus space (play/pause), Home/End, Esc, and ? for the
  // shortcut sheet.
  //
  // WCAG 2.1.4: single-CHARACTER shortcuts (space, ?) sit behind a persisted
  // toggle in the shortcut sheet, so they can be disabled by anyone whose
  // speech or switch input they would collide with. Arrow/Home/End/Esc are
  // not character keys and stay active. The sheet is also reachable from a
  // visible button, so turning the character keys off cannot lock the sheet
  // (and the toggle) away.

  var KBD_PREF = 'tat.models.kbd.charkeys';

  HafsViewer.prototype._charKeysOn = function () {
    try { return localStorage.getItem(KBD_PREF) !== 'off'; }
    catch (e) { return true; }
  };

  HafsViewer.prototype._setCharKeys = function (on) {
    try { localStorage.setItem(KBD_PREF, on ? 'on' : 'off'); }
    catch (e) { /* private mode: session-only default */ }
  };

  // RUN TREND: switch to the adjacent init and land on the SAME VALID TIME.
  // dir +1 = newer run, -1 = older. valid = init + fxx, cycles are 6 h apart,
  // so the target hour is fxx + (thisInit - targetInit).
  HafsViewer.prototype._runTrend = function (dir) {
    if (!this.cycle || !this.cycles.length || !this.fxxList.length) return;
    var order = [];
    for (var i = 0; i < this.cycles.length; i++) order.push(this.cycles[i].cycle);
    var at = order.indexOf(this.cycle.cycle);
    var tgt = dir > 0 ? at - 1 : at + 1;         // list is newest-first
    if (tgt < 0 || tgt >= order.length) {
      this._toast(dir > 0 ? 'No newer run available.' : 'No older run available.');
      return;
    }
    var cur = parseCycleUTC(this.cycle.cycle), nxt = parseCycleUTC(order[tgt]);
    if (!cur || !nxt) return;
    var wantF = this.fxxList[this.idx] + Math.round((cur - nxt) / 3600000);
    this._pause();
    this._selectCycle(order[tgt], true, true);
    if (!this.fxxList.length) {
      this._toast('That run has no frames for this selection.');
      return;
    }
    var j = this.fxxList.indexOf(wantF);
    if (j >= 0) {
      this._show(j);
      this._toast('Run ' + cycleDayTag(order[tgt]) + ' · same valid time (F' +
                  pad(wantF, 3) + ')');
    } else {
      // The exact valid time is not rendered in that run (an older run's tail,
      // or a hole). Show the nearest frame and SAY so, never silently.
      this._show(this._renderedIndexNear(wantF));
      this._toast('No frame at this valid time in that run — showing F' +
                  pad(this.fxxList[this.idx], 3) + '.');
    }
    this._syncUrl(true);
  };

  HafsViewer.prototype._wireKeyboard = function () {
    var self = this;
    if (typeof document === 'undefined' || !document.addEventListener) return;

    // A visible way into the sheet (required: with character keys toggled
    // off, "?" cannot be the only door).
    if (this.dom.player && typeof document.createElement === 'function') {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'hafs-btn hafs-kbd-btn';
      btn.textContent = '⌨ Shortcuts';
      btn.title = 'Keyboard shortcuts (?)';
      btn.addEventListener('click', function () { self._toggleSheet(); });
      this.dom.player.appendChild(btn);
    }

    document.addEventListener('keydown', function (e) {
      // Only when THIS viewer is on screen (the CycloLab embed lives in a
      // tab that may be hidden), never with a modifier, and never when the
      // user is typing in a control - native behavior always wins there.
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      var stage = self.dom.stage;
      if (!stage || stage.offsetParent === null) return;
      var t = e.target, tag = t && t.tagName;
      if (tag === 'SELECT' || tag === 'INPUT' || tag === 'TEXTAREA' ||
          (t && t.isContentEditable)) return;
      // Buttons: skip so space/enter keep their native click - EXCEPT the
      // hour grid, where a click leaves focus and "pick an hour, then arrow
      // through frames" must keep working.
      var hourBtn = tag === 'BUTTON' && t.classList &&
                    t.classList.contains('hafs-hr');
      if (tag === 'BUTTON' && !hourBtn &&
          (e.key === ' ' || e.key === 'Spacebar' || e.key === 'Enter')) return;

      var chars = self._charKeysOn();
      switch (e.key) {
        case 'ArrowLeft':  self._pause(); self._step(-1); e.preventDefault(); break;
        case 'ArrowRight': self._pause(); self._step(1); e.preventDefault(); break;
        case 'ArrowUp':    self._runTrend(1); e.preventDefault(); break;
        case 'ArrowDown':  self._runTrend(-1); e.preventDefault(); break;
        case 'Home':
          if (self.fxxList.length) { self._pause(); self._show(0); e.preventDefault(); }
          break;
        case 'End':
          if (self.fxxList.length) {
            self._pause(); self._show(self.fxxList.length - 1); e.preventDefault();
          }
          break;
        case 'Escape':
          if (self._rulerMode || self._rulerA) {
            self._rulerMode = false; self._rulerA = null;
            if (self._rulerBtn) self._rulerBtn.classList.remove('on');
            if (self._roEl) self._roEl.style.display = 'none';
          } else if (self._sheetEl && self._sheetEl.style.display !== 'none') {
            self._toggleSheet(false);
          } else { self._pause(); }
          break;
        case ' ': case 'Spacebar':
          if (chars) { self._togglePlay(); self._syncUrl(true); e.preventDefault(); }
          break;
        case '?':
          if (chars) { self._toggleSheet(); e.preventDefault(); }
          break;
        case 's': case 'S':
          if (chars && self._shearEligible()) {
            self._toggleShear(); e.preventDefault();
          }
          break;
      }
    });
  };

  HafsViewer.prototype._toggleSheet = function (force) {
    var self = this;
    if (!this._sheetEl) {
      var sheet = document.createElement('div');
      sheet.className = 'hafs-kbd-sheet';
      sheet.setAttribute('role', 'dialog');
      sheet.setAttribute('aria-label', 'Keyboard shortcuts');
      sheet.innerHTML =
        '<div class="hafs-kbd-head">Keyboard shortcuts</div>' +
        '<table><tbody>' +
        '<tr><td><kbd>←</kbd> <kbd>→</kbd></td><td>previous / next forecast hour</td></tr>' +
        '<tr><td><kbd>↑</kbd> <kbd>↓</kbd></td><td><strong>run trend</strong> — newer / older init, ' +
        'same valid time (watch how the forecast for one moment changed run over run)</td></tr>' +
        '<tr><td><kbd>Space</kbd></td><td>play / pause</td></tr>' +
        '<tr><td><kbd>Home</kbd> <kbd>End</kbd></td><td>first / last rendered hour</td></tr>' +
        '<tr><td><kbd>Esc</kbd></td><td>pause · close this sheet</td></tr>' +
        '<tr><td><kbd>S</kbd></td><td>shear-relative view (storm view, when available)</td></tr>' +
        '<tr><td><kbd>?</kbd></td><td>this sheet</td></tr>' +
        '</tbody></table>' +
        '<label class="hafs-kbd-toggle"><input type="checkbox"> ' +
        'Enable single-key shortcuts (<kbd>Space</kbd>, <kbd>?</kbd>) — ' +
        'arrow and function keys always work</label>' +
        '<button type="button" class="hafs-btn hafs-kbd-close">Close</button>';
      this.root.appendChild(sheet);
      this._sheetEl = sheet;
      var cb = sheet.querySelector('input');
      cb.checked = this._charKeysOn();
      cb.addEventListener('change', function () { self._setCharKeys(cb.checked); });
      sheet.querySelector('.hafs-kbd-close')
        .addEventListener('click', function () { self._toggleSheet(false); });
    }
    var show = force !== undefined ? force
      : this._sheetEl.style.display === 'none' || !this._sheetEl.style.display;
    this._sheetEl.style.display = show ? 'block' : 'none';
  };

  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('DOMContentLoaded', function () {
      var root = el('hafs-viewer');
      // The instance is exposed for the browser test harnesses and for
      // console debugging; nothing on the page reads it.
      if (root && typeof window !== 'undefined') {
        window.__hafsViewer = new HafsViewer(root);
      } else if (root) {
        new HafsViewer(root);
      }
    });
  }

  // Second-mount export (CycloLab constructs the viewer manually with its
  // own config). The /models/ auto-boot above stays the only automatic path.
  if (typeof window !== 'undefined') {
    window.HafsViewer = HafsViewer;
  }

  // Tiny, guarded test hook: expose the constructor + a couple of pure helpers
  // under node so tests/hafs_viewer_harness.cjs can drive the viewer with a DOM
  // shim. No effect in the browser.
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      HafsViewer: HafsViewer,
      pad: pad,
      fmtUTC: fmtUTC,
      fmtCycle: fmtCycle,
      cycleHourTag: cycleHourTag,
      cycleDayTag: cycleDayTag
    };
  }
})();
