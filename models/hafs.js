/*
 * hafs.js — Triple-A-Tropics /models/ HAFS viewer
 * ---------------------------------------------------------------------------
 * Manifest-driven forecast-loop viewer for the HAFS model plots (MSLP + 10 m
 * wind). Reads a manifest published to Cloudflare R2 by generate_hafs_plots.py
 * and lets the user pick a storm → model (HAFS-A/B) → domain (storm nest /
 * parent) and scrub / play the forecast hours.
 *
 * Manifest shape (models/hafs/manifest.json on cdn.triple-a-tropics.com):
 *   {
 *     "generated_at": "2026-05-29T06:53:00Z",
 *     "product": {"slug":"mslp_wind","label":"MSLP + 10 m Wind"},
 *     "models":  [{"slug":"hafsa","label":"HAFS-A"}, …],
 *     "domains": [{"slug":"storm","label":"Storm nest (~2 km)","raw":"storm.atm"}, …],
 *     "fxx_step": 3, "fxx_pad": 3,
 *     "path_template": "{model}/{storm}/{domain}/f{fxx}.png",
 *     "cycle": "2023090900",
 *     "storms": [
 *       {"id":"13l","name":"13L","basin":"al","basin_label":"North Atlantic",
 *        "cycle":"2023090900","init":"2023-09-09T00:00:00Z",
 *        "frames": {"hafsa": {"storm":[0,3,…,126], "parent":[…]}, "hafsb": {…}}}
 *     ]
 *   }
 *
 * Frame URL: {BASE}/models/hafs/{model}/{storm}/{domain}/f{FFF}.png
 * Every URL gets ?v=encodeURIComponent(generated_at) so a fresh cycle busts
 * the browser/CDN cache.
 */
(function () {
  'use strict';

  var BASE = 'https://cdn.triple-a-tropics.com';
  var MANIFEST_URL = BASE + '/models/hafs/manifest.json';
  var SPEED_OPTIONS = [0.5, 1, 2, 4];   // playback frames-per-step multiplier
  var BASE_FPS = 4;                     // frames/sec at 1× speed

  var DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

  function pad(n, w) { n = String(n); while (n.length < w) n = '0' + n; return n; }

  // Format an ISO-ish instant (…Z) as "Sun 2023-09-10 12Z" in UTC.
  function fmtUTC(d) {
    return DOW[d.getUTCDay()] + ' ' +
      d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1, 2) + '-' +
      pad(d.getUTCDate(), 2) + ' ' + pad(d.getUTCHours(), 2) + 'Z';
  }

  function el(id) { return document.getElementById(id); }

  function HafsViewer(root) {
    this.root = root;
    this.manifest = null;
    this.cacheBust = '';
    // current selection
    this.storm = null;     // storm object
    this.model = null;     // slug
    this.domain = null;    // slug
    this.fxxList = [];     // available forecast hours for the selection
    this.idx = 0;          // index into fxxList
    this.playing = false;
    this.speed = 1;
    this.timer = null;
    this.preloaded = {};   // url → HTMLImageElement (decoded cache; bounded to selection)
    this.preloadGen = 0;   // bumped each selection so stale preloads are ignored

    this.dom = {
      stage:    el('hafs-stage'),
      img:      el('hafs-img'),
      status:   el('hafs-status'),
      empty:    el('hafs-empty'),
      controls: el('hafs-controls'),
      stormSel: el('hafs-storm'),
      models:   el('hafs-models'),
      domains:  el('hafs-domains'),
      scrub:    el('hafs-scrub'),
      play:     el('hafs-play'),
      stepB:    el('hafs-step-back'),
      stepF:    el('hafs-step-fwd'),
      speed:    el('hafs-speed'),
      fhour:    el('hafs-fhour'),
      valid:    el('hafs-valid'),
      meta:     el('hafs-meta'),
      buffer:   el('hafs-buffer'),
      player:   el('hafs-player'),
      caption:  el('hafs-caption')
    };
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
    fetch(MANIFEST_URL + '?t=' + Date.now(), { cache: 'no-store' })
      .then(function (r) {
        if (!r.ok) throw new Error('manifest HTTP ' + r.status);
        return r.json();
      })
      .then(function (m) { self._onManifest(m); })
      .catch(function (e) {
        console.error('hafs: manifest load failed', e);
        self._setStatus('Could not load model data. Try again shortly.', true);
      });
  };

  HafsViewer.prototype._onManifest = function (m) {
    this.manifest = m;
    this.cacheBust = m.generated_at ? encodeURIComponent(m.generated_at) : '';
    var storms = (m.storms || []);

    // Footer meta: cycle + generated time, always shown if present.
    var bits = [];
    if (m.cycle) {
      var c = m.cycle; // YYYYMMDDHH
      bits.push('Cycle ' + c.slice(0, 4) + '-' + c.slice(4, 6) + '-' +
                c.slice(6, 8) + ' ' + c.slice(8, 10) + 'Z');
    }
    if (m.generated_at) bits.push('Rendered ' + m.generated_at.replace('T', ' '));
    this.dom.meta.textContent = bits.join('  ·  ');

    if (!storms.length) {
      this._setStatus(null, false);
      this.dom.controls.style.display = 'none';
      this.dom.stage.style.display = 'none';
      this.dom.player.style.display = 'none';
      this.dom.caption.style.display = 'none';
      this.dom.empty.style.display = 'block';
      return;
    }
    // Clear the "Loading manifest…" overlay now that we have storms — otherwise
    // the translucent spinner box sits over every frame for the whole session.
    this._setStatus(null, false);
    this.dom.empty.style.display = 'none';
    this.dom.controls.style.display = '';
    this.dom.stage.style.display = '';
    this.dom.player.style.display = '';
    this.dom.caption.style.display = '';

    // Populate the storm dropdown.
    var sel = this.dom.stormSel;
    sel.innerHTML = '';
    for (var i = 0; i < storms.length; i++) {
      var s = storms[i];
      var o = document.createElement('option');
      o.value = s.id;
      o.textContent = s.name + ' · ' + (s.basin_label || s.basin || '');
      sel.appendChild(o);
    }
    this._selectStorm(storms[0].id);
  };

  HafsViewer.prototype._stormById = function (id) {
    var st = this.manifest.storms;
    for (var i = 0; i < st.length; i++) if (st[i].id === id) return st[i];
    return null;
  };

  // Models that actually have frames for the current storm, in manifest order.
  HafsViewer.prototype._modelsFor = function (storm) {
    var out = [];
    var defs = this.manifest.models || [];
    for (var i = 0; i < defs.length; i++) {
      var slug = defs[i].slug;
      if (storm.frames[slug] && Object.keys(storm.frames[slug]).length) {
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
      var slug = defs[i].slug;
      if (fr[slug] && fr[slug].length) out.push(defs[i]);
    }
    return out;
  };

  HafsViewer.prototype._selectStorm = function (id) {
    this.storm = this._stormById(id);
    this.dom.stormSel.value = id;
    // Pick first available model, preferring to keep the current one.
    var models = this._modelsFor(this.storm);
    var keep = null;
    for (var i = 0; i < models.length; i++) if (models[i].slug === this.model) keep = this.model;
    this._buildToggle(this.dom.models, models, keep || (models[0] && models[0].slug),
                      this._selectModel.bind(this));
    this._selectModel(keep || (models[0] && models[0].slug));
  };

  HafsViewer.prototype._selectModel = function (slug) {
    this.model = slug;
    this._highlight(this.dom.models, slug);
    var domains = this._domainsFor(this.storm, slug);
    var keep = null;
    for (var i = 0; i < domains.length; i++) if (domains[i].slug === this.domain) keep = this.domain;
    this._buildToggle(this.dom.domains, domains, keep || (domains[0] && domains[0].slug),
                      this._selectDomain.bind(this));
    this._selectDomain(keep || (domains[0] && domains[0].slug));
  };

  HafsViewer.prototype._selectDomain = function (slug) {
    this.domain = slug;
    this._highlight(this.dom.domains, slug);
    var fr = (this.storm.frames[this.model] || {})[slug] || [];
    this.fxxList = fr.slice();
    // Keep the same forecast hour across selection changes when possible.
    var curF = this.fxxList.length ? this.fxxList[Math.min(this.idx, this.fxxList.length - 1)] : 0;
    var newIdx = this.fxxList.indexOf(curF);
    this.idx = newIdx >= 0 ? newIdx : 0;

    var sc = this.dom.scrub;
    sc.min = 0;
    sc.max = Math.max(0, this.fxxList.length - 1);
    sc.value = this.idx;
    sc.disabled = this.fxxList.length <= 1;

    this._show(this.idx);
    this._preloadAll();
  };

  // Build a segmented button group; calls onPick(slug) on click.
  HafsViewer.prototype._buildToggle = function (container, defs, active, onPick) {
    container.innerHTML = '';
    for (var i = 0; i < defs.length; i++) {
      (function (def) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'hafs-seg' + (def.slug === active ? ' active' : '');
        b.textContent = def.label;
        b.setAttribute('data-slug', def.slug);
        b.addEventListener('click', function () { onPick(def.slug); });
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
    var pad3 = pad(fxx, this.manifest.fxx_pad || 3);
    var u = BASE + '/models/hafs/' + this.model + '/' + this.storm.id + '/' +
            this.domain + '/f' + pad3 + '.png';
    return this.cacheBust ? (u + '?v=' + this.cacheBust) : u;
  };

  // Show the frame at index i; update the slider + readouts.
  HafsViewer.prototype._show = function (i) {
    if (!this.fxxList.length) return;
    this.idx = Math.max(0, Math.min(i, this.fxxList.length - 1));
    var fxx = this.fxxList[this.idx];
    var url = this._frameUrl(fxx);
    this.dom.img.src = url;
    this.dom.scrub.value = this.idx;
    this.dom.fhour.textContent = 'F' + pad(fxx, 3);
    var init = new Date(this.storm.init);
    var valid = new Date(init.getTime() + fxx * 3600 * 1000);
    this.dom.valid.textContent = 'Valid ' + fmtUTC(valid) +
      '  ·  Init ' + fmtUTC(init);
  };

  // Preload every frame of the current selection so scrub/play is smooth.
  // A generation token makes a new selection supersede any in-flight preload
  // (stale onload callbacks are ignored), and the decoded-image cache is reset
  // each selection so a long session can't grow it without bound.
  HafsViewer.prototype._preloadAll = function () {
    var self = this;
    var gen = ++this.preloadGen;
    this.preloaded = {};
    var urls = this.fxxList.map(function (f) { return self._frameUrl(f); });
    var done = 0, total = urls.length;
    if (!total) { this.dom.buffer.style.display = 'none'; return; }
    this.dom.buffer.style.display = 'block';
    this.dom.buffer.textContent = 'Buffering 0/' + total;
    urls.forEach(function (u) {
      var im = new Image();
      im.onload = im.onerror = function () {
        if (gen !== self.preloadGen) return;   // superseded by a newer selection
        self.preloaded[u] = im;
        done++;
        self.dom.buffer.textContent = 'Buffering ' + done + '/' + total;
        if (done >= total) self.dom.buffer.style.display = 'none';
      };
      im.src = u;
    });
  };

  HafsViewer.prototype._step = function (delta) {
    if (!this.fxxList.length) return;
    var n = this.fxxList.length;
    this._show((this.idx + delta + n) % n);
  };

  HafsViewer.prototype._togglePlay = function () {
    this.playing ? this._pause() : this._play();
  };

  HafsViewer.prototype._play = function () {
    if (this.fxxList.length <= 1) return;
    this.playing = true;
    this.dom.play.textContent = '❚❚ Pause';
    var self = this;
    var interval = 1000 / (BASE_FPS * this.speed);
    clearInterval(this.timer);
    this.timer = setInterval(function () {
      // Loop; pause briefly at the end frame for legibility.
      var next = self.idx + 1;
      if (next >= self.fxxList.length) next = 0;
      self._show(next);
    }, interval);
  };

  HafsViewer.prototype._pause = function () {
    this.playing = false;
    this.dom.play.textContent = '► Play';
    clearInterval(this.timer);
    this.timer = null;
  };

  HafsViewer.prototype._wire = function () {
    var self = this;
    this.dom.stormSel.addEventListener('change', function () {
      self._pause(); self._selectStorm(this.value);
    });
    this.dom.scrub.addEventListener('input', function () {
      self._pause(); self._show(parseInt(this.value, 10));
    });
    this.dom.play.addEventListener('click', function () { self._togglePlay(); });
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
    sp.addEventListener('change', function () {
      self.speed = parseFloat(this.value);
      if (self.playing) self._play();   // restart timer at new rate
    });

    // Keyboard: ←/→ step, space play/pause. Skip when focus is on a form
    // control (the storm <select>, the scrub slider, or a button) so their
    // native arrow/space behavior still works.
    this.root.addEventListener('keydown', function (e) {
      var tag = e.target && e.target.tagName;
      if (tag === 'SELECT' || tag === 'INPUT' || tag === 'BUTTON') return;
      if (e.key === 'ArrowLeft')  { self._pause(); self._step(-1); e.preventDefault(); }
      else if (e.key === 'ArrowRight') { self._pause(); self._step(1); e.preventDefault(); }
      else if (e.key === ' ' || e.key === 'Spacebar') { self._togglePlay(); e.preventDefault(); }
    });
  };

  document.addEventListener('DOMContentLoaded', function () {
    var root = el('hafs-viewer');
    if (root) new HafsViewer(root);
  });
})();
