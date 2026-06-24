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

  var PRODUCTS = [
    { key: '89pct',   label: '89 GHz PCT', short: '89 PCT' },
    { key: '37color', label: '37 GHz Color', short: '37 Color' }
  ];

  function el(id) { return document.getElementById(id); }

  function MicrowaveViewer(root, opts) {
    opts = opts || {};
    this.root = root;
    this.base = (opts.base || BASE_DEFAULT).replace(/\/+$/, '');
    this.product = '89pct';
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
        b.addEventListener('click', function () { self._setProduct(p.key); });
        d.toggle.appendChild(b);
      });
    }
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
      var nm = s.name || s.atcf || s.slug;
      var n = s.overpass_count || 0;
      o.textContent = nm + '  ·  ' + (s.basin || '') + ' ' + (s.year || '') +
        '  ·  ' + n + ' pass' + (n === 1 ? '' : 'es');
      sel.appendChild(o);
    });
  };

  // ---- storm -> overpasses ------------------------------------------------
  MicrowaveViewer.prototype._selectStorm = function (slug) {
    var s = this._stormBySlug(slug);
    if (!s) return;
    this.curStorm = slug;
    if (this.dom.stormSel) this.dom.stormSel.value = slug;
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

  function _overpassLabel(o) {
    var t = _fmtTime(o.valid_utc);
    var kt = (typeof o.intensity_kt === 'number') ? (o.intensity_kt + ' kt') : '';
    var dev = o.dev_level ? (' ' + o.dev_level) : '';
    var sensor = o.sensor || '';
    if (o.platform) sensor += ' ' + o.platform;
    return t + '  ·  ' + sensor + (kt ? ('  ·  ' + kt + dev) : '');
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

  // ---- auto-mount ---------------------------------------------------------
  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('DOMContentLoaded', function () {
      var r = el('microwave-viewer');
      if (r) r.__microwaveView = new MicrowaveViewer(r);
    });
  }
  if (typeof window !== 'undefined') window.MicrowaveViewer = MicrowaveViewer;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { MicrowaveViewer: MicrowaveViewer };
  }
})();
