/*
 * product_animator.js — Triple-A-Tropics
 * -------------------------------------------------------------
 * MP4 animation widget for SST / subsurface / ARMOR3D family pages.
 * Reads a per-family manifest.json from the orphan `mp4-artifacts`
 * branch via raw.githubusercontent.com and presents a <video>-based
 * player with region / product / timescale / speed dropdowns.
 *
 * Container contract:
 *   <div class="product-animator"
 *        data-family="sst">                  // sst | subsurface | armor3d
 *     <!-- DOM rendered by this script -->
 *   </div>
 *
 * URLs it hits:
 *   .../mp4-artifacts/{family}/manifest.json
 *   .../mp4-artifacts/{family}/{region}_{product}.mp4
 *   .../mp4-artifacts/{family}/{region}_{product}.jpg
 *
 * Cache-busting: every URL gets ?v=encodeURIComponent(generated_at)
 * appended so a fresh workflow run invalidates browser/CDN cache
 * without any server-side header work.
 */
(function () {
  'use strict';

  // raw.githubusercontent.com sends CORS headers + supports byte-range
  // requests, so cross-origin fetch + <video> seek both work.
  const ARTIFACTS_BASE =
    'https://raw.githubusercontent.com/WeathermanAAA/Triple-A-Tropics/mp4-artifacts';

  const SPEED_OPTIONS = [0.5, 1, 2, 4];

  class ProductAnimator {
    constructor(root) {
      this.root = root;
      this.family = root.dataset.family;
      if (!this.family) {
        console.error('product-animator: missing data-family');
        return;
      }
      this.manifest = null;
      this.regionSlug = null;
      this.productSlug = null;
      this.timescale = null;     // in window units (days or weeks)
      this.speed = 1;
      this._renderShell();
      this._loadManifest();
    }

    _renderShell() {
      this.root.innerHTML = `
        <div class="pa-head">
          <div class="pa-title">
            <span class="pa-family-label">Loading…</span>
          </div>
          <div class="pa-controls">
            <label class="pa-ctrl">
              <span>Region</span>
              <select class="pa-select" data-role="region" disabled></select>
            </label>
            <label class="pa-ctrl">
              <span>Product</span>
              <select class="pa-select" data-role="product" disabled></select>
            </label>
            <label class="pa-ctrl">
              <span>Window</span>
              <select class="pa-select" data-role="timescale" disabled></select>
            </label>
            <label class="pa-ctrl">
              <span>Speed</span>
              <select class="pa-select" data-role="speed" disabled>
                ${SPEED_OPTIONS.map(s =>
                  `<option value="${s}"${s === 1 ? ' selected' : ''}>${s}×</option>`
                ).join('')}
              </select>
            </label>
            <a class="pa-btn pa-btn-download" data-role="download"
               download rel="noopener" hidden>Download MP4</a>
          </div>
        </div>
        <div class="pa-stage">
          <video class="pa-video" data-role="video"
                 controls playsinline preload="metadata"></video>
          <div class="pa-status" data-role="status">Loading manifest…</div>
        </div>
        <div class="pa-caption" data-role="caption"></div>
      `;
      this.familyLabelEl = this.root.querySelector('.pa-family-label');
      this.regionSelect  = this.root.querySelector('[data-role="region"]');
      this.productSelect = this.root.querySelector('[data-role="product"]');
      this.timescaleSel  = this.root.querySelector('[data-role="timescale"]');
      this.speedSelect   = this.root.querySelector('[data-role="speed"]');
      this.downloadBtn   = this.root.querySelector('[data-role="download"]');
      this.video         = this.root.querySelector('[data-role="video"]');
      this.statusEl      = this.root.querySelector('[data-role="status"]');
      this.captionEl     = this.root.querySelector('[data-role="caption"]');
    }

    async _loadManifest() {
      const url = `${ARTIFACTS_BASE}/${this.family}/manifest.json`
                + `?t=${Date.now()}`;   // bust browser cache on widget init
      try {
        const res = await fetch(url, { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        this.manifest = await res.json();
      } catch (e) {
        this._setStatus(
          `Animations are not available yet — the orphan ` +
          `\`mp4-artifacts\` branch hasn't been published. ` +
          `(${e.message})`
        );
        return;
      }
      this._populateFromManifest();
      this._wireEvents();
      this._loadCurrent();
    }

    _populateFromManifest() {
      const m = this.manifest;
      const win = m.window || { unit: 'days', length: 90 };
      const unit = win.unit;
      const len  = win.length;

      // Family label, e.g. "SST · 90-day MP4 animations"
      const fname = this.family.toUpperCase();
      this.familyLabelEl.textContent =
        `${fname} · ${len}-${unit.replace(/s$/, '')} MP4 animations`;

      // Regions — group by basin if names hint at it. Simpler: flat list
      // sorted alphabetically with "Global" pinned to the top.
      const regions = (m.regions || []).slice();
      regions.sort((a, b) => {
        if (a.slug === 'global') return -1;
        if (b.slug === 'global') return 1;
        if (a.slug === 'global-tropics') return -1;
        if (b.slug === 'global-tropics') return 1;
        return a.label.localeCompare(b.label);
      });
      this.regionSelect.innerHTML = regions.map(r =>
        `<option value="${r.slug}">${escapeHTML(r.label)}</option>`
      ).join('');
      this.regionSelect.disabled = false;
      this.regionSlug = regions[0] ? regions[0].slug : null;

      // Products — preserve manifest order (it's the renderer's
      // chosen presentation order).
      const products = m.products || [];
      this.productSelect.innerHTML = products.map(p =>
        `<option value="${p.slug}">${escapeHTML(p.label)}</option>`
      ).join('');
      this.productSelect.disabled = false;
      this.productSlug = products[0] ? products[0].slug : null;

      // Timescale: thirds of the full window. For days → 30/60/90,
      // for weeks → ≈len/3, len*2/3, len. Always show the full window
      // last and select it by default.
      const thirds = [Math.round(len / 3), Math.round(2 * len / 3), len]
        .filter((v, i, a) => a.indexOf(v) === i && v > 0);
      this.timescaleSel.innerHTML = thirds.map(n =>
        `<option value="${n}"${n === len ? ' selected' : ''}>${n} ${unit}</option>`
      ).join('');
      this.timescaleSel.disabled = false;
      this.timescale = len;

      // Speed defaults to 1×
      this.speedSelect.disabled = false;
      this.speed = 1;

      this.downloadBtn.hidden = false;
      this._setStatus('');
    }

    _wireEvents() {
      this.regionSelect.addEventListener('change', () => {
        this.regionSlug = this.regionSelect.value;
        this._loadCurrent();
      });
      this.productSelect.addEventListener('change', () => {
        this.productSlug = this.productSelect.value;
        this._loadCurrent();
      });
      this.timescaleSel.addEventListener('change', () => {
        this.timescale = parseInt(this.timescaleSel.value, 10);
        this._applyClipBounds();
      });
      this.speedSelect.addEventListener('change', () => {
        this.speed = parseFloat(this.speedSelect.value) || 1;
        this.video.playbackRate = this.speed;
      });
      // Loop within the user-selected timescale window. If the user
      // picked "30 days" but the underlying MP4 is 90 days, jump back
      // to the clip start (60-day mark) instead of the file start.
      this.video.addEventListener('timeupdate', () => {
        if (!this._clipStart && this._clipStart !== 0) return;
        if (this.video.duration > 0 &&
            this.video.currentTime >= this.video.duration - 0.05) {
          try { this.video.currentTime = this._clipStart; }
          catch (e) { /* iOS sometimes blocks pre-metadata seeks */ }
        }
      });
      this.video.addEventListener('loadedmetadata', () => {
        this._applyClipBounds();
        this.video.playbackRate = this.speed;
      });
      this.video.addEventListener('error', () => {
        this._setStatus('Video failed to load. The MP4 may not be ready yet.');
      });
    }

    _loadCurrent() {
      if (!this.manifest || !this.regionSlug || !this.productSlug) return;
      const key = `${this.regionSlug}_${this.productSlug}`;
      const clip = (this.manifest.clips || {})[key];
      if (!clip) {
        this._setStatus(`No clip for ${this.regionSlug} · ${this.productSlug} yet.`);
        this.video.removeAttribute('src');
        this.video.load();
        this.captionEl.textContent = '';
        this.downloadBtn.removeAttribute('href');
        return;
      }
      this._setStatus('');
      const v = encodeURIComponent(this.manifest.generated_at || '');
      const base = `${ARTIFACTS_BASE}/${this.family}`;
      const mp4Url    = `${base}/${clip.src}?v=${v}`;
      const posterUrl = `${base}/${clip.poster}?v=${v}`;
      this.video.poster = posterUrl;
      this.video.src = mp4Url;
      this.video.load();
      this.video.playbackRate = this.speed;

      // Download button: link to the full clip (always the entire
      // pre-rendered window, regardless of the timescale dropdown).
      this.downloadBtn.href = mp4Url;
      const win = this.manifest.window || { unit: 'days', length: 90 };
      this.downloadBtn.textContent =
        `Download ${win.length}-${win.unit.replace(/s$/, '')} MP4`;
      this.downloadBtn.download = clip.src;

      // Caption: product description + clip metadata
      const product = (this.manifest.products || [])
        .find(p => p.slug === this.productSlug);
      const region = (this.manifest.regions || [])
        .find(r => r.slug === this.regionSlug);
      const desc = product ? product.description : '';
      const fmtDate = (s) => {
        if (!s) return '';
        try {
          return new Date(s + 'T00:00:00Z').toLocaleDateString('en-US', {
            month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC',
          });
        } catch (e) { return s; }
      };
      const range = clip.first_frame && clip.last_frame
        ? ` · ${fmtDate(clip.first_frame)} → ${fmtDate(clip.last_frame)}`
        : '';
      const sizeMB = clip.bytes ? ` · ${(clip.bytes / (1024 * 1024)).toFixed(1)} MB` : '';
      this.captionEl.innerHTML =
        `<b>${escapeHTML((region && region.label) || this.regionSlug)}</b> — ` +
        `${escapeHTML(desc || '')}` +
        `<span class="pa-meta">${range}${sizeMB}</span>`;
    }

    _applyClipBounds() {
      const win = this.manifest && this.manifest.window;
      if (!win) return;
      const dur = this.video.duration;
      if (!dur || !isFinite(dur)) return;
      const frac = (win.length - this.timescale) / win.length;
      this._clipStart = Math.max(0, frac * dur);
      try {
        if (this.video.currentTime < this._clipStart) {
          this.video.currentTime = this._clipStart;
        }
      } catch (e) { /* swallow pre-metadata seek errors */ }
    }

    _setStatus(msg) {
      this.statusEl.textContent = msg || '';
      this.statusEl.style.display = msg ? '' : 'none';
    }
  }

  function escapeHTML(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function init() {
    document.querySelectorAll('.product-animator').forEach(el => {
      if (!el._productAnimator) el._productAnimator = new ProductAnimator(el);
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
