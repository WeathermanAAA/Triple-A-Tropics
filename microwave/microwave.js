/* microwave.js - viewer for OBSERVED passive-microwave TC imagery.
 *
 * Data: NOAA/CIRA TC-PRIMED, rendered by generate_tcprimed.py and synced to R2
 * under microwave/. This is an ARCHIVE / RECENT-STORM product (research-tiered:
 * final post-season, preliminary lags hours-to-days) - NOT real-time; the
 * caption says so.
 *
 * Flow: fetch manifest.json (no-store, ?t=) -> STORM <select> from storms[] ->
 * on select, fetch {slug}/overpasses.json (default cache) -> OVERPASS <select>
 * -> a PRODUCT toggle (89 GHz PCT / 37 GHz Color) -> show the chosen PNG. An
 * out-of-order fetch guard (_fetchSeq) discards stale responses. Auto-mounts on
 * DOMContentLoaded by id (#microwave-viewer); exposes window.MicrowaveViewer.
 *
 * No CDN deps; plain DOM. Mirrors recon.js / hafs.js conventions.
 */
(function () {
  'use strict';

  var BASE_DEFAULT = 'https://cdn.triple-a-tropics.com/microwave';

  // The four canonical observed-MW products (keys match the producer + manifest).
  var PRODUCTS = [
    { key: 'color37', label: '37 Color', short: '37 Color' },
    { key: 'color91', label: '91 Color', short: '91 Color' },
    { key: '37H',     label: '37H',      short: '37H' },
    { key: '91H',     label: '91H',      short: '91H' }
  ];
  var DEFAULT_PRODUCT = '91H';   // the high-freq scattering view

  // Loop-export endpoint (shared with the satellite viewer): the render service
  // encodes a smooth mp4 (libx264) or single-palette gif from already-rendered
  // CDN frame URLs. Primary path; client gif.js is the fallback.
  var EXPORT_API = 'https://web-production-b88d.up.railway.app/export';
  var GIF_MAX_W = 900;

  function el(id) { return document.getElementById(id); }

  function MicrowaveViewer(root, opts) {
    opts = opts || {};
    this.root = root;
    this.base = (opts.base || BASE_DEFAULT).replace(/\/+$/, '');
    this.product = DEFAULT_PRODUCT;
    this.raw = false;           // smoothing: false = smoothed (default), true = raw
    this.encoding = false;      // GIF/MP4 export in flight
    this.manifest = null;
    this.storms = [];
    this.curStorm = null;       // slug
    this.overpasses = [];
    this.curOverpass = null;    // overpass record
    this._fetchSeq = 0;
    this.dom = {};
    this._mount();
    this._boot();
  }

  // ---- markup -------------------------------------------------------------
  MicrowaveViewer.prototype._mount = function () {
    var d = this.dom;
    d.stormSel    = el('mw-storm');
    d.overpassSel = el('mw-overpass');
    d.toggle      = el('mw-products');
    d.img         = el('mw-image');
    d.frame       = el('mw-imageframe');
    d.status      = el('mw-status');
    d.caption     = el('mw-caption');
    d.empty       = el('mw-empty');
    d.disclosure  = el('mw-disclosure');
    d.stormPrev   = el('mw-storm-prev');
    d.stormNext   = el('mw-storm-next');
    d.smooth      = el('mw-smooth');
    d.exFmt       = el('mw-exfmt');
    d.exBtn       = el('mw-export');
    d.exStatus    = el('mw-export-status');

    var self = this;
    if (d.stormSel) {
      d.stormSel.addEventListener('change', function () {
        self._selectStorm(self.dom.stormSel.value);
      });
    }
    if (d.overpassSel) {
      d.overpassSel.addEventListener('change', function () {
        self._selectOverpass(parseInt(self.dom.overpassSel.value, 10));
      });
    }
    // Product toggle: segmented buttons.
    if (d.toggle) {
      d.toggle.innerHTML = '';
      PRODUCTS.forEach(function (p) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'mw-seg' + (p.key === self.product ? ' active' : '');
        b.textContent = p.label;
        b.setAttribute('data-product', p.key);
        b.addEventListener('click', function () { self._chooseProduct(p.key); });
        d.toggle.appendChild(b);
      });
    }
    // Per-storm prev/next: primary navigation, stepping the newest-first storms[].
    if (d.stormPrev) d.stormPrev.addEventListener('click', function () { self._stepStorm(-1); });
    if (d.stormNext) d.stormNext.addEventListener('click', function () { self._stepStorm(1); });
    // Smoothing toggle: client-side CSS image-rendering (no double-render; the
    // exact colors live in the baked PNG so both modes read correctly).
    if (d.smooth) {
      d.smooth.innerHTML = '';
      [['Smoothed', false], ['Raw', true]].forEach(function (pair) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'mw-seg' + ((!!pair[1] === self.raw) ? ' active' : '');
        b.textContent = pair[0];
        b.setAttribute('data-raw', pair[1] ? '1' : '0');
        b.addEventListener('click', function () { self._setSmoothing(!!pair[1]); });
        d.smooth.appendChild(b);
      });
    }
    // Per-product loop export (server mp4/gif primary; client gif.js fallback).
    if (d.exBtn) d.exBtn.addEventListener('click', function () { self._export(); });
    // Keyboard left/right to step overpasses.
    if (this.root) {
      this.root.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowLeft') { self._step(-1); e.preventDefault(); }
        else if (e.key === 'ArrowRight') { self._step(1); e.preventDefault(); }
      });
    }
  };

  // ---- fetch helpers ------------------------------------------------------
  MicrowaveViewer.prototype._fetchJson = function (path, noStore) {
    var url = this.base + path + (noStore ? ('?t=' + Date.now()) : '');
    return fetch(url, { cache: noStore ? 'no-store' : 'default' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + path);
        return r.json();
      });
  };

  MicrowaveViewer.prototype._status = function (msg) {
    if (this.dom.status) {
      this.dom.status.style.display = msg ? 'flex' : 'none';
      var sp = this.dom.status.querySelector('span');
      if (sp && msg) sp.textContent = msg;
    }
  };

  MicrowaveViewer.prototype._showEmpty = function (on) {
    if (this.dom.empty) this.dom.empty.style.display = on ? 'block' : 'none';
    var body = this.root && this.root.querySelector('#mw-body');
    if (body) body.style.display = on ? 'none' : '';
  };

  // ---- boot ---------------------------------------------------------------
  MicrowaveViewer.prototype._boot = function () {
    var self = this;
    this._status('Loading…');
    this._fetchJson('/manifest.json', true)
      .then(function (m) { self._onManifest(m); })
      .catch(function (e) {
        self._status('');
        self._showEmpty(true);
        if (window.console) console.warn('microwave: manifest load failed', e);
      });
  };

  MicrowaveViewer.prototype._onManifest = function (m) {
    this.manifest = m || {};
    var storms = (m && m.storms) || [];
    this.storms = storms;
    if (this.dom.disclosure && m && m.disclosure) {
      this.dom.disclosure.textContent = m.disclosure;
    }
    if (!storms.length) {
      this._status('');
      this._showEmpty(true);
      return;
    }
    this._showEmpty(false);
    this._buildStormSelect();
    var startSlug = (m && m.default_slug) || (storms[0] && storms[0].slug);
    this._selectStorm(startSlug);
  };

  MicrowaveViewer.prototype._stormBySlug = function (slug) {
    for (var i = 0; i < this.storms.length; i++) {
      if (this.storms[i].slug === slug) return this.storms[i];
    }
    return null;
  };

  MicrowaveViewer.prototype._buildStormSelect = function () {
    var sel = this.dom.stormSel;
    if (!sel) return;
    sel.innerHTML = '';
    this.storms.forEach(function (s) {
      var o = document.createElement('option');
      o.value = s.slug;
      o.textContent = _stormLabel(s);
      sel.appendChild(o);
    });
  };

  // Concise storm label: name + basin/year qualifier. No auto-generated summary
  // blurb (the "N passes" count is dropped).
  function _stormLabel(s) {
    var nm = s.name || s.atcf || s.slug;
    var by = ((s.basin || '') + ' ' + (s.year || '')).trim();
    return by ? (nm + '  ·  ' + by) : nm;
  }

  // ---- storm -> overpasses ------------------------------------------------
  MicrowaveViewer.prototype._selectStorm = function (slug) {
    var s = this._stormBySlug(slug);
    if (!s) return;
    this.curStorm = slug;
    if (this.dom.stormSel) this.dom.stormSel.value = slug;
    this._syncStormNav();
    var self = this;
    var seq = ++this._fetchSeq;
    this._status('Loading…');
    this._fetchJson('/' + slug + '/overpasses.json', false)
      .then(function (doc) {
        if (seq !== self._fetchSeq || self.curStorm !== slug) return;
        self._status('');
        self.overpasses = (doc && doc.overpasses) || [];
        self._buildOverpassSelect();
        // Default to the latest overpass.
        if (self.overpasses.length) {
          self._selectOverpass(self.overpasses.length - 1);
        }
      })
      .catch(function (e) {
        if (seq !== self._fetchSeq) return;
        self._status('Could not load overpasses.');
        if (window.console) console.warn('microwave: overpasses failed', e);
      });
  };

  MicrowaveViewer.prototype._buildOverpassSelect = function () {
    var sel = this.dom.overpassSel;
    if (!sel) return;
    sel.innerHTML = '';
    this.overpasses.forEach(function (o, i) {
      var opt = document.createElement('option');
      opt.value = String(i);
      opt.textContent = _overpassLabel(o);
      sel.appendChild(opt);
    });
  };

  // Concise overpass label: time + sensor/platform. No auto-generated summary
  // blurb (the intensity "{kt} {dev}" tail is dropped; it still shows in the
  // caption below the image).
  function _overpassLabel(o) {
    var t = _fmtTime(o.valid_utc);
    var sensor = o.sensor || '';
    if (o.platform) sensor += ' ' + o.platform;
    return sensor ? (t + '  ·  ' + sensor) : t;
  }

  function _fmtTime(iso) {
    if (!iso) return '';
    // 2024-09-26T18:25:30Z -> "Sep 26 18:25Z"
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    var mon = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct',
               'Nov','Dec'][d.getUTCMonth()];
    function p2(n) { return (n < 10 ? '0' : '') + n; }
    return mon + ' ' + d.getUTCDate() + ' ' + p2(d.getUTCHours()) + ':' +
      p2(d.getUTCMinutes()) + 'Z';
  }

  // ---- overpass + product -------------------------------------------------
  MicrowaveViewer.prototype._selectOverpass = function (idx) {
    if (idx == null || idx < 0 || idx >= this.overpasses.length) return;
    this.curOverpass = this.overpasses[idx];
    if (this.dom.overpassSel) this.dom.overpassSel.value = String(idx);
    this._syncProductAvailability();
    this._render();
  };

  MicrowaveViewer.prototype._step = function (delta) {
    if (!this.overpasses.length || !this.curOverpass) return;
    var idx = this.overpasses.indexOf(this.curOverpass);
    if (idx < 0) idx = 0;
    var next = Math.min(this.overpasses.length - 1, Math.max(0, idx + delta));
    if (next !== idx) this._selectOverpass(next);
  };

  // Find the overpass index nearest the current one that actually rendered a
  // given product. Many overpasses publish only 89pct (e.g. SSMIS-F17 passes
  // whose 37 GHz V channel was all-fill), so 37color can be absent on the
  // latest pass even though earlier passes have it. Search outward by distance;
  // on a tie (same distance ahead vs. behind) prefer the MORE RECENT (later)
  // index. Returns -1 if no overpass has the product. fromIdx defaults to the
  // current overpass.
  MicrowaveViewer.prototype._findNearestOverpassWithProduct = function (key, fromIdx) {
    var ops = this.overpasses;
    var n = ops.length;
    if (!n) return -1;
    var start = (typeof fromIdx === 'number') ? fromIdx : ops.indexOf(this.curOverpass);
    if (start < 0) start = n - 1;
    function has(i) {
      var p = ops[i] && ops[i].products;
      return !!(p && p[key]);
    }
    if (has(start)) return start;
    for (var d = 1; d < n; d++) {
      var ahead = start + d;   // more recent: checked first (tie -> latest)
      if (ahead < n && has(ahead)) return ahead;
      var behind = start - d;
      if (behind >= 0 && has(behind)) return behind;
    }
    return -1;
  };

  // User picked a product from the toggle. If the current overpass has it, just
  // show it. If not, jump to the NEAREST overpass that does (preferring the
  // most recent) so a one-click toggle always lands on real imagery — this is
  // the fix for "where is 37 GHz?" when the latest pass is 89pct-only.
  MicrowaveViewer.prototype._chooseProduct = function (key) {
    var o = this.curOverpass || {};
    var prods = o.products || {};
    if (prods[key]) { this._setProduct(key); return; }
    var idx = this._findNearestOverpassWithProduct(key);
    if (idx >= 0) {
      // Set the desired product first so _selectOverpass ->
      // _syncProductAvailability won't bounce it back to the old product, then
      // move to the overpass that has it.
      this.product = key;
      this._selectOverpass(idx);
    } else {
      // No overpass in this storm has the product; reflect the (disabled)
      // selection and let the caption say it's unavailable.
      this._setProduct(key);
    }
  };

  MicrowaveViewer.prototype._setProduct = function (key) {
    this.product = key;
    var btns = this.dom.toggle ? this.dom.toggle.querySelectorAll('.mw-seg') : [];
    for (var i = 0; i < btns.length; i++) {
      var on = btns[i].getAttribute('data-product') === key;
      btns[i].classList.toggle('active', on);
    }
    this._render();
  };

  // Grey out a product button when the current overpass lacks it (e.g. an
  // SSMIS F17 pass whose 37 GHz channel was all-fill -> 89 PCT only). If the
  // active product is unavailable, fall back to one that exists.
  MicrowaveViewer.prototype._syncProductAvailability = function () {
    var o = this.curOverpass || {};
    var prods = o.products || {};
    var btns = this.dom.toggle ? this.dom.toggle.querySelectorAll('.mw-seg') : [];
    var firstAvail = null;
    for (var i = 0; i < btns.length; i++) {
      var key = btns[i].getAttribute('data-product');
      var has = !!prods[key];
      btns[i].disabled = !has;
      btns[i].classList.toggle('mw-unavailable', !has);
      btns[i].classList.toggle('active', key === this.product);
      if (has && firstAvail === null) firstAvail = key;
    }
    if (!prods[this.product] && firstAvail) this._setProduct(firstAvail);
  };

  MicrowaveViewer.prototype._render = function () {
    var o = this.curOverpass;
    if (!o) return;
    var prods = o.products || {};
    var rel = prods[this.product];
    if (this.dom.img) {
      this.dom.img.style.imageRendering = this.raw ? 'pixelated' : '';
      if (rel) {
        this.dom.img.src = this.base + '/' + rel;
        this.dom.img.alt = _overpassLabel(o) + ' ' + this.product;
        this.dom.img.style.display = 'block';
      } else {
        this.dom.img.removeAttribute('src');
        this.dom.img.style.display = 'none';
      }
    }
    this._renderCaption(o, !rel);
  };

  MicrowaveViewer.prototype._renderCaption = function (o, missing) {
    if (!this.dom.caption) return;
    var bits = [];
    var sensor = (o.sensor || '');
    if (o.platform) sensor += ' ' + o.platform;
    if (sensor) bits.push('<b>' + sensor + '</b>');
    if (o.valid_utc) bits.push('Valid ' + _fmtTime(o.valid_utc) +
      ' (' + o.valid_utc.replace('T', ' ').replace('Z', ' UTC') + ')');
    if (typeof o.intensity_kt === 'number') {
      var s = o.intensity_kt + ' kt';
      if (o.dev_level) s += ' ' + o.dev_level;
      bits.push(s);
    }
    var prodLabel = '';
    for (var i = 0; i < PRODUCTS.length; i++) {
      if (PRODUCTS[i].key === this.product) prodLabel = PRODUCTS[i].label;
    }
    if (prodLabel) bits.push(prodLabel);
    var html = bits.join(' &nbsp;·&nbsp; ');
    if (missing) {
      html += ' &nbsp;·&nbsp; <i>(' + prodLabel +
        ' not available for this pass)</i>';
    }
    this.dom.caption.innerHTML = html;
  };

  // ---- per-storm navigation (primary nav) ---------------------------------
  MicrowaveViewer.prototype._stormIndex = function () {
    for (var i = 0; i < this.storms.length; i++) {
      if (this.storms[i].slug === this.curStorm) return i;
    }
    return -1;
  };

  MicrowaveViewer.prototype._stepStorm = function (delta) {
    if (!this.storms.length) return;
    var idx = this._stormIndex();
    if (idx < 0) idx = 0;
    var next = Math.min(this.storms.length - 1, Math.max(0, idx + delta));
    if (next !== idx) this._selectStorm(this.storms[next].slug);
  };

  MicrowaveViewer.prototype._syncStormNav = function () {
    var idx = this._stormIndex(), n = this.storms.length;
    if (this.dom.stormPrev) this.dom.stormPrev.disabled = (idx <= 0);
    if (this.dom.stormNext) this.dom.stormNext.disabled = (idx < 0 || idx >= n - 1);
  };

  // ---- smoothing toggle (client-side CSS; no double-render) ----------------
  MicrowaveViewer.prototype._setSmoothing = function (raw) {
    this.raw = !!raw;
    var btns = this.dom.smooth ? this.dom.smooth.querySelectorAll('.mw-seg') : [];
    for (var i = 0; i < btns.length; i++) {
      btns[i].classList.toggle('active',
        (btns[i].getAttribute('data-raw') === '1') === this.raw);
    }
    if (this.dom.img) this.dom.img.style.imageRendering = this.raw ? 'pixelated' : '';
  };

  // ---- per-product loop export (GIF / MP4) --------------------------------
  // The selected storm's overpasses, oldest->newest, that actually have the
  // current product -> a frame sequence. Returns the absolute CDN URLs.
  MicrowaveViewer.prototype._frameUrlsForProduct = function (key) {
    var self = this, urls = [];
    (this.overpasses || []).forEach(function (o) {
      var rel = o && o.products && o.products[key];
      if (rel) urls.push(self.base + '/' + rel);
    });
    return urls;
  };

  MicrowaveViewer.prototype._exStatus = function (msg) {
    if (this.dom.exStatus) {
      this.dom.exStatus.textContent = msg || '';
      this.dom.exStatus.style.display = msg ? '' : 'none';
    }
  };

  MicrowaveViewer.prototype._export = function () {
    if (this.encoding) return;
    var key = this.product;
    var urls = this._frameUrlsForProduct(key);
    if (urls.length < 2) {
      this._exStatus('Need at least 2 ' + key + ' passes to make a loop.');
      return;
    }
    var fmtEl = this.dom.exFmt;
    var fmt = (fmtEl && String(fmtEl.value).toLowerCase() === 'gif') ? 'gif' : 'mp4';
    var fps = 2, skip = 0;
    var name = 'mw_' + (this.curStorm || 'storm') + '_' + key + '.' + fmt;
    var self = this;
    this.encoding = true;
    if (this.dom.exBtn) this.dom.exBtn.disabled = true;
    this._exStatus('Encoding ' + fmt.toUpperCase() + '…');
    // Server path (primary): one continuous mp4 / single-palette gif.
    fetch(EXPORT_API, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ frames: urls, fps: fps, skip: skip, format: fmt })
    }).then(function (r) {
      if (!r.ok) throw new Error('export HTTP ' + r.status);
      return r.blob();
    }).then(function (blob) {
      if (!blob || !blob.size) throw new Error('empty export');
      _download(blob, name);
      self._exStatus('Done.'); self.encoding = false;
      if (self.dom.exBtn) self.dom.exBtn.disabled = false;
      setTimeout(function () { self._exStatus(''); }, 1500);
    }).catch(function () {
      // Server unavailable -> never break the button: client-side gif.js.
      self._exStatus('Server busy — encoding GIF locally…');
      self._exportClient(urls, 'mw_' + (self.curStorm || 'storm') + '_' + key + '.gif');
    });
  };

  MicrowaveViewer.prototype._ensureGifWorker = function (cb) {
    if (this._gifWorkerUrl) { cb(this._gifWorkerUrl); return; }
    var self = this;
    var CDNW = 'https://cdnjs.cloudflare.com/ajax/libs/gif.js/0.2.0/gif.worker.js';
    fetch(CDNW).then(function (r) { return r.text(); }).then(function (src) {
      self._gifWorkerUrl = URL.createObjectURL(
        new Blob([src], { type: 'application/javascript' }));
      cb(self._gifWorkerUrl);
    }).catch(function () { cb(CDNW); });
  };

  // Client-side gif.js fallback: preload the frame PNGs CORS-clean, draw each to
  // an offscreen canvas, and encode. Used only when the server /export fails.
  MicrowaveViewer.prototype._exportClient = function (urls, name) {
    var self = this;
    function done(ok) {
      self.encoding = false;
      if (self.dom.exBtn) self.dom.exBtn.disabled = false;
      self._exStatus(ok ? 'Done.' : 'GIF export failed — try again.');
      if (ok) setTimeout(function () { self._exStatus(''); }, 1500);
    }
    if (typeof window === 'undefined' || typeof window.GIF === 'undefined') {
      done(false); return;
    }
    var imgs = [], pending = urls.length, failed = false;
    urls.forEach(function (u, i) {
      var im = new Image(); im.crossOrigin = 'anonymous';
      im.onload = function () { imgs[i] = im; if (!--pending) build(); };
      im.onerror = function () { failed = true; if (!--pending) build(); };
      im.src = u + (u.indexOf('?') >= 0 ? '&' : '?') + 'cors=1';
    });
    function build() {
      var frames = imgs.filter(function (x) { return x && x.naturalWidth; });
      if (frames.length < 2) { done(false); return; }
      var W0 = frames[0].naturalWidth, H0 = frames[0].naturalHeight;
      var scale = Math.min(1, GIF_MAX_W / W0);
      var W = Math.round(W0 * scale), H = Math.round(H0 * scale);
      var oc = document.createElement('canvas'); oc.width = W; oc.height = H;
      var octx = oc.getContext('2d');
      self._ensureGifWorker(function (worker) {
        var gif;
        try {
          gif = new window.GIF({ workers: 2, quality: 10, width: W, height: H,
            workerScript: worker, background: '#0b0e13' });
        } catch (e) { done(false); return; }
        gif.on('finished', function (blob) { _download(blob, name); done(true); });
        gif.on('error', function () { done(false); });
        var added = 0, last = frames.length - 1;
        frames.forEach(function (im, i) {
          octx.clearRect(0, 0, W, H);
          try { octx.drawImage(im, 0, 0, W, H); } catch (e) { return; }
          gif.addFrame(octx, { copy: true, delay: (i === last) ? 900 : 500 });
          added++;
        });
        if (added < 2) { done(false); return; }
        try { gif.render(); } catch (e) { done(false); }
      });
    }
  };

  function _download(blob, name) {
    var u = URL.createObjectURL(blob), a = document.createElement('a');
    a.href = u; a.download = name;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    requestAnimationFrame(function () { URL.revokeObjectURL(u); });
  }

  // ---- auto-mount ---------------------------------------------------------
  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('DOMContentLoaded', function () {
      var r = el('microwave-viewer');
      if (r) r.__microwaveView = new MicrowaveViewer(r);
    });
  }
  if (typeof window !== 'undefined') window.MicrowaveViewer = MicrowaveViewer;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      MicrowaveViewer: MicrowaveViewer,
      PRODUCTS: PRODUCTS, DEFAULT_PRODUCT: DEFAULT_PRODUCT,
      stormLabel: _stormLabel, overpassLabel: _overpassLabel
    };
  }
})();
