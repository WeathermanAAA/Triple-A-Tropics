/*
 * static_widget.js — Triple-A-Tropics /sst/ page
 * -------------------------------------------------------------
 * Unified static-plot widget. One Source dropdown chooses the data
 * family (OISST / CRW / AOML / ARMOR3D); per-source Region + Variant
 * controls filter down from there. All image URLs are resolved from
 * `sst/static_manifest.json` so adding a source or changing a path
 * convention is a config edit, not a code edit.
 *
 * Region filtering: a variant may declare its own `regions` array to
 * override the source-level union — the Region dropdown rebuilds on
 * every variant change, preserving the current selection when it's
 * still valid under the new variant.
 *
 * Container contract:
 *   <div id="sstStatic" data-manifest="/sst/static_manifest.json"></div>
 *
 * URL hash is written as `#source,region,variant` so reloads and shared
 * links land on the same view.
 */
(function () {
  'use strict';

  const ROOT_SELECTOR = '#sstStatic';

  class StaticWidget {
    constructor(root, manifest) {
      this.root = root;
      this.m = manifest;
      this.sourceSlug = null;
      this.regionSlug = null;
      this.variantSlug = null;
      this.use15d = false;
      this.showLabels = false;
      this._render();
      this._restoreFromHashOrDefault();
      this._update();
      this._loadMeta();
    }

    _render() {
      this.root.innerHTML = `
        <div class="sw-head">
          <div class="sw-subtitle" data-role="subtitle"></div>
          <div class="sw-meta" data-role="meta"></div>
        </div>
        <div class="sw-controls">
          <label class="sw-ctrl">
            <span>Source</span>
            <select class="sw-select" data-role="source"></select>
          </label>
          <label class="sw-ctrl">
            <span>Region</span>
            <select class="sw-select" data-role="region"></select>
          </label>
        </div>
        <nav class="sw-tabs" data-role="variants" aria-label="Variant"></nav>
        <div class="sw-toggles">
          <label class="sw-toggle" data-role="toggle-15d">
            <input type="checkbox" data-role="chk-15d">
            <span>15-day running mean</span>
          </label>
          <label class="sw-toggle" data-role="toggle-labels">
            <input type="checkbox" data-role="chk-labels">
            <span>Show values</span>
          </label>
        </div>
        <section class="chart-section">
          <div class="chart-card sst-chart">
            <img class="sst-image sw-image" data-role="image" alt="">
            <div class="sst-caption sw-caption" data-role="caption"></div>
            <details class="sst-caption sw-method" data-role="method" hidden
                     style="margin-top:.35rem">
              <summary style="cursor:pointer">How is this derived?</summary>
              <div data-role="method-text" style="margin-top:.3rem"></div>
            </details>
          </div>
        </section>
      `;
      this.subtitleEl = this.root.querySelector('[data-role="subtitle"]');
      this.metaEl     = this.root.querySelector('[data-role="meta"]');
      this.sourceSel  = this.root.querySelector('[data-role="source"]');
      this.regionSel  = this.root.querySelector('[data-role="region"]');
      this.variantNav = this.root.querySelector('[data-role="variants"]');
      this.chk15d     = this.root.querySelector('[data-role="chk-15d"]');
      this.chkLabels  = this.root.querySelector('[data-role="chk-labels"]');
      this.toggle15dWrap    = this.root.querySelector('[data-role="toggle-15d"]');
      this.toggleLabelsWrap = this.root.querySelector('[data-role="toggle-labels"]');
      this.image      = this.root.querySelector('[data-role="image"]');
      this.captionEl  = this.root.querySelector('[data-role="caption"]');
      this.methodEl     = this.root.querySelector('[data-role="method"]');
      this.methodTextEl = this.root.querySelector('[data-role="method-text"]');

      // Source options — same order as manifest.
      this.sourceSel.innerHTML = this.m.sources.map(s =>
        `<option value="${escapeAttr(s.slug)}">${escapeHTML(s.label)}</option>`
      ).join('');

      this.sourceSel.addEventListener('change', () => {
        this._setSource(this.sourceSel.value);
        this._update();
      });
      this.regionSel.addEventListener('change', () => {
        this.regionSlug = this.regionSel.value;
        this._update();
      });
      this.chk15d.addEventListener('change', () => {
        this.use15d = this.chk15d.checked;
        this._update();
      });
      this.chkLabels.addEventListener('change', () => {
        this.showLabels = this.chkLabels.checked;
        this._update();
      });
    }

    _restoreFromHashOrDefault() {
      const saved = this._parseHash();
      const initialSource =
        (saved && this._sourceBySlug(saved.source)) ||
        this.m.sources[0];
      this._setSource(initialSource.slug, { desiredRegion: saved && saved.region,
                                            desiredVariant: saved && saved.variant });
    }

    _parseHash() {
      const h = location.hash.replace(/^#/, '');
      if (!h) return null;
      const [s, r, v] = h.split(',');
      if (!s) return null;
      return { source: s, region: r, variant: v };
    }

    _writeHash() {
      const parts = [this.sourceSlug, this.regionSlug, this.variantSlug].filter(Boolean);
      try {
        history.replaceState(null, '', '#' + parts.join(','));
      } catch (e) { /* some sandboxed iframes forbid this */ }
    }

    _sourceBySlug(slug) {
      return (this.m.sources || []).find(s => s.slug === slug);
    }

    _setSource(slug, opts = {}) {
      const src = this._sourceBySlug(slug);
      if (!src) return;
      this.sourceSlug = slug;
      this.sourceSel.value = slug;
      this.subtitleEl.textContent = src.subtitle || '';

      // Variant tabs, rebuilt per source. _setVariant handles the
      // Region dropdown rebuild (so it refilters on variant change
      // too, when a variant carries its own `regions` list).
      this.variantNav.innerHTML = src.variants.map((v, i) =>
        `<a href="#" class="${i === 0 ? 'active' : ''}" data-variant="${escapeAttr(v.slug)}">${escapeHTML(v.label)}</a>`
      ).join('');
      this.variantNav.querySelectorAll('a').forEach(a => {
        a.addEventListener('click', (e) => {
          e.preventDefault();
          this._setVariant(a.dataset.variant, { desiredRegion: this.regionSlug });
          this._update();
        });
      });
      // Hide the variant bar when a source has just one variant — keeps
      // single-product sources from looking visually empty.
      this.variantNav.hidden = src.variants.length <= 1;

      const preferredVariant = opts.desiredVariant || this.variantSlug;
      const found = src.variants.find(v => v.slug === preferredVariant);
      this._setVariant(
        (found && found.slug) || src.variants[0].slug,
        { desiredRegion: opts.desiredRegion || this.regionSlug }
      );

      // Toggles: hide what the source doesn't support.
      this.toggle15dWrap.hidden = !src.supports_15d;
      this.toggleLabelsWrap.hidden = !src.supports_labels;
      if (!src.supports_15d) this.chk15d.checked = false;
      if (!src.supports_labels) this.chkLabels.checked = false;
      this.use15d = this.chk15d.checked;
      this.showLabels = this.chkLabels.checked;

      this._loadMeta();
    }

    _setVariant(slug, opts = {}) {
      const src = this._currentSource();
      if (!src) return;
      this.variantSlug = slug;
      this.variantNav.querySelectorAll('a').forEach(a => {
        a.classList.toggle('active', a.dataset.variant === slug);
      });

      // Region dropdown: use the variant's own regions list when
      // present (coverage that varies by variant); otherwise the
      // source-level union. Preserve the current region across the
      // rebuild if it's still valid; otherwise default to the first.
      const variant = src.variants.find(v => v.slug === slug) || src.variants[0];
      const effective = (variant && Array.isArray(variant.regions) && variant.regions.length)
        ? variant.regions
        : (src.regions || []);
      const allowed = new Set(effective);
      const groups = (this.m.region_groups || []).map(g => ({
        label: g.label,
        regions: (g.regions || []).filter(r => allowed.has(r)),
      })).filter(g => g.regions.length > 0);
      this.regionSel.innerHTML = groups.map(g => `
        <optgroup label="${escapeAttr(g.label)}">
          ${g.regions.map(r => `
            <option value="${escapeAttr(r)}">${escapeHTML(this.m.region_labels[r] || r)}</option>
          `).join('')}
        </optgroup>
      `).join('');
      const preferred = opts.desiredRegion || this.regionSlug;
      this.regionSlug = (preferred && allowed.has(preferred))
        ? preferred
        : (effective[0] || null);
      this.regionSel.value = this.regionSlug;
    }

    _currentSource() { return this._sourceBySlug(this.sourceSlug); }
    _currentVariant() {
      const src = this._currentSource();
      if (!src) return null;
      return src.variants.find(v => v.slug === this.variantSlug) || src.variants[0];
    }

    _buildSuffix() {
      // Filename suffix order matches the Python generators:
      //   base.png, base_labels.png, base_15d.png, base_15d_labels.png
      let s = '';
      if (this.use15d) s += '_15d';
      if (this.showLabels) s += '_labels';
      return s;
    }

    _update() {
      const src = this._currentSource();
      const variant = this._currentVariant();
      if (!src || !variant || !this.regionSlug) return;
      const suffix = this._buildSuffix();
      const url = src.path_template
        .replace('{region}', this.regionSlug)
        .replace('{variant}', variant.slug)
        .replace('{suffix}', suffix);
      this.image.src = url + '?t=' + Date.now();
      const regionLabel = this.m.region_labels[this.regionSlug] || this.regionSlug;
      this.image.alt = `${regionLabel}: ${src.label} ${variant.label}`;
      this.captionEl.textContent =
        `${regionLabel}: ${variant.caption || ''}` +
        (this.use15d ? ' · 15-day running mean.' : '') +
        (this.showLabels ? ' · values overlaid.' : '');
      // Method panel — the "how is this derived?" disclosure, shown only
      // for variants that declare one in the manifest (e.g. the ARMOR3D
      // TCHP record hatching). Collapses on variant change so a stale
      // open panel never describes the wrong product.
      if (variant.method) {
        this.methodTextEl.textContent = variant.method;
        this.methodEl.hidden = false;
        this.methodEl.open = false;
      } else {
        this.methodTextEl.textContent = '';
        this.methodEl.hidden = true;
      }
      this._writeHash();
    }

    _loadMeta() {
      const src = this._currentSource();
      if (!src || !src.meta_url) { this.metaEl.textContent = ''; return; }
      // Tag the current load so a source change mid-fetch doesn't
      // overwrite the label with the previous source's date.
      const token = (this._metaToken = (this._metaToken || 0) + 1);
      fetch(src.meta_url + '?t=' + Date.now(), { cache: 'no-store' })
        .then(r => r.ok ? r.json() : null)
        .then(meta => {
          if (token !== this._metaToken) return;
          if (!meta || !meta.date) { this.metaEl.textContent = ''; return; }
          const d = new Date(meta.date + 'T00:00:00Z');
          const txt = d.toLocaleDateString('en-US', {
            month: 'long', day: 'numeric', year: 'numeric', timeZone: 'UTC',
          });
          const updated = meta.updated_utc ? `  ·  updated ${meta.updated_utc}` : '';
          this.metaEl.textContent = `Valid ${txt}${updated}`;
        })
        .catch(() => { if (token === this._metaToken) this.metaEl.textContent = ''; });
    }
  }

  function escapeHTML(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function escapeAttr(s) { return escapeHTML(s); }

  function init() {
    const root = document.querySelector(ROOT_SELECTOR);
    if (!root || root._staticWidget) return;
    const manifestUrl = root.dataset.manifest || '/sst/static_manifest.json';
    fetch(manifestUrl + '?t=' + Date.now(), { cache: 'no-store' })
      .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(m => { root._staticWidget = new StaticWidget(root, m); })
      .catch(e => {
        root.innerHTML = `<div class="sw-error">Could not load static_manifest.json: ${escapeHTML(e.message)}</div>`;
      });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
