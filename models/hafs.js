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
    this._fetchManifest()
      .then(function (m) { self._onManifest(m); })
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

  // Placeholder until the smart default lands (item 11): manifest order.
  HafsViewer.prototype._defaultStormId = function (storms) {
    return storms[0] && storms[0].id;
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
    // CYCLES MODE: cycle-scoped immutable keys -> path_template_cycles with
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
    this.dom.img.src = url;
    this.dom.fhour.textContent = 'F' + pad(fxx, 3);
    // Valid time uses the SELECTED cycle's storm init.
    var init = new Date(this.storm.init);
    var valid = new Date(init.getTime() + fxx * 3600 * 1000);
    this.dom.valid.textContent = 'Valid ' + fmtUTC(valid) +
      '  ·  Init ' + fmtUTC(init);
    this._highlightHour();
    this._updatePill();
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
      im.src = u;
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
  // replaceState debounced 250 ms, because 43 forecast hours must never become
  // 43 back-button presses.

  HafsViewer.prototype._readUrl = function () {
    try {
      var q = new URLSearchParams(location.search);
      var out = {};
      ['run', 'storm', 'model', 'domain', 'product', 'fxx', 'mode']
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

    // Keyboard: ←/→ step, space play/pause. Skip when focus is on a form
    // control (the storm <select> or a transport button) so its native
    // arrow/space behavior wins — EXCEPT the hour-grid buttons: a click
    // leaves them focused (Chrome/Firefox/Edge), buttons have no native
    // arrow behavior, and space would just re-click the same hour, so the
    // "pick an hour, then arrow through frames" path must keep working.
    this.root.addEventListener('keydown', function (e) {
      var t = e.target, tag = t && t.tagName;
      var hourBtn = tag === 'BUTTON' && t.classList &&
                    t.classList.contains('hafs-hr');
      if ((tag === 'SELECT' || tag === 'INPUT' || tag === 'BUTTON') && !hourBtn) return;
      if (e.key === 'ArrowLeft')  { self._pause(); self._step(-1); e.preventDefault(); }
      else if (e.key === 'ArrowRight') { self._pause(); self._step(1); e.preventDefault(); }
      else if (e.key === ' ' || e.key === 'Spacebar') { self._togglePlay(); e.preventDefault(); }
    });
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
