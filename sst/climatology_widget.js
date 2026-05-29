/*
 * climatology_widget.js — Triple-A-Tropics /sst/ page
 * -------------------------------------------------------------
 * Lightweight picker for the kouya-style daily-SST climatology curves.
 * One grouped Region dropdown → one PNG per region, resolved from
 * `sst/climatology_manifest.json` (so adding a region or changing the
 * path convention is a config edit, not a code edit). The header bar,
 * legend, colorbar, and inset all live INSIDE the rendered PNG, so the
 * widget is just region selection + the image + a "Valid <date>" line.
 *
 * Container contract:
 *   <div id="sstClimatology" data-manifest="/sst/climatology_manifest.json"></div>
 *
 * Deliberately does NOT touch location.hash — the static-map widget on
 * the same page owns the hash; a second writer would fight it.
 */
(function () {
  'use strict';

  const ROOT_SELECTOR = '#sstClimatology';

  class ClimatologyWidget {
    constructor(root, manifest) {
      this.root = root;
      this.m = manifest;
      this.regionSlug = null;
      this._render();
      this._pickDefault();
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
            <span>Region</span>
            <select class="sw-select" data-role="region"></select>
          </label>
        </div>
        <section class="chart-section">
          <div class="chart-card sst-chart">
            <img class="sst-image sw-image" data-role="image" alt="">
            <div class="sst-caption sw-caption" data-role="caption"></div>
          </div>
        </section>
      `;
      this.subtitleEl = this.root.querySelector('[data-role="subtitle"]');
      this.metaEl     = this.root.querySelector('[data-role="meta"]');
      this.regionSel  = this.root.querySelector('[data-role="region"]');
      this.image      = this.root.querySelector('[data-role="image"]');
      this.captionEl  = this.root.querySelector('[data-role="caption"]');

      this.subtitleEl.textContent = this.m.subtitle || '';

      // Grouped region options, mirroring the static widget's layout.
      const groups = (this.m.region_groups || []).map(g => ({
        label: g.label,
        regions: (g.regions || []),
      })).filter(g => g.regions.length > 0);
      this.regionSel.innerHTML = groups.map(g => `
        <optgroup label="${escapeAttr(g.label)}">
          ${g.regions.map(r => `
            <option value="${escapeAttr(r)}">${escapeHTML(this.m.region_labels[r] || r)}</option>
          `).join('')}
        </optgroup>
      `).join('');

      this.regionSel.addEventListener('change', () => {
        this.regionSlug = this.regionSel.value;
        this._update();
      });
    }

    _pickDefault() {
      const groups = this.m.region_groups || [];
      const firstRegion = groups.length && groups[0].regions.length
        ? groups[0].regions[0] : null;
      this.regionSlug = this.m.default_region || firstRegion;
      if (this.regionSlug) this.regionSel.value = this.regionSlug;
    }

    _update() {
      if (!this.regionSlug || !this.m.path_template) return;
      const url = this.m.path_template.replace('{region}', this.regionSlug);
      this.image.src = url + '?t=' + Date.now();
      const label = this.m.region_labels[this.regionSlug] || this.regionSlug;
      this.image.alt = `${label}: daily SST climatology curve`;
      this.captionEl.textContent =
        `${label}: region-mean SST by day of year vs the 1991–2020 daily ` +
        `climatology and the full 1982-present record (NOAA OISST).`;
    }

    _loadMeta() {
      if (!this.m.meta_url) { this.metaEl.textContent = ''; return; }
      fetch(this.m.meta_url + '?t=' + Date.now(), { cache: 'no-store' })
        .then(r => r.ok ? r.json() : null)
        .then(meta => {
          if (!meta || !meta.date) { this.metaEl.textContent = ''; return; }
          const d = new Date(meta.date + 'T00:00:00Z');
          const txt = d.toLocaleDateString('en-US', {
            month: 'long', day: 'numeric', year: 'numeric', timeZone: 'UTC',
          });
          const updated = meta.updated_utc ? `  ·  updated ${meta.updated_utc}` : '';
          this.metaEl.textContent = `Valid ${txt}${updated}`;
        })
        .catch(() => { this.metaEl.textContent = ''; });
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
    if (!root || root._climatologyWidget) return;
    const manifestUrl = root.dataset.manifest || '/sst/climatology_manifest.json';
    fetch(manifestUrl + '?t=' + Date.now(), { cache: 'no-store' })
      .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(m => { root._climatologyWidget = new ClimatologyWidget(root, m); })
      .catch(e => {
        root.innerHTML = `<div class="sw-error">Could not load climatology_manifest.json: ${escapeHTML(e.message)}</div>`;
      });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
