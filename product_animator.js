/*
 * product_animator.js — Triple-A-Tropics
 * -------------------------------------------------------------
 * MP4 animation widget for SST / subsurface / ARMOR3D family pages.
 *
 * Container contract (two modes):
 *
 *   1) Single-family (legacy):
 *      <div class="product-animator" data-family="sst"></div>
 *
 *   2) Multi-source with a Data-source dropdown:
 *      <div class="product-animator" data-sources='[
 *        {"slug":"oisst","label":"OISST","family":"sst",
 *         "products":["actual","anomaly","anomaly_gmr"]},
 *        {"slug":"crw","label":"CRW","family":"sst",
 *         "products":["crw_anomaly"]},
 *        {"slug":"aoml_tchp","label":"AOML TCHP","disabled":true,
 *         "disabledReason":"Animations coming soon"}
 *      ]'></div>
 *
 *   The multi-source mode fetches each distinct `family`'s manifest on
 *   demand (and memoizes), then filters the Product dropdown to the
 *   source's `products` list. Disabled sources show a status message
 *   and freeze the video surface.
 *
 * URLs it hits:
 *   .../mp4-artifacts/{family}/manifest.json
 *   .../mp4-artifacts/{family}/{region}_{product}.mp4
 *   .../mp4-artifacts/{family}/{region}_{product}.jpg
 *
 * Cache-busting: every URL gets ?v=encodeURIComponent(generated_at)
 * appended so a fresh workflow run invalidates browser/CDN cache.
 */
(function () {
  'use strict';

  const ARTIFACTS_BASE =
    'https://raw.githubusercontent.com/WeathermanAAA/Triple-A-Tropics/mp4-artifacts';

  const SPEED_OPTIONS = [0.5, 1, 2, 4];

  class ProductAnimator {
    constructor(root) {
      this.root = root;
      this.sources = this._parseSources(root);
      if (!this.sources.length) {
        console.error('product-animator: missing data-family or data-sources');
        return;
      }
      this.sourceSlug = null;
      this.manifestsByFamily = new Map();   // family → manifest (fetched once)
      this.manifestPromises = new Map();    // family → in-flight fetch
      this.manifest = null;                 // active manifest
      this.regionSlug = null;
      this.productSlug = null;
      this.timescale = null;
      this.speed = 1;
      this._clipStart = 0;
      this._scrubbing = false;
      this._wasPlayingBeforeScrub = false;
      this._renderShell();
      this._wireEvents();
      // Pick the first non-disabled source as the initial selection.
      const first = this.sources.find(s => !s.disabled) || this.sources[0];
      this._setSource(first.slug);
    }

    _parseSources(root) {
      const raw = root.dataset.sources;
      if (raw) {
        try {
          const parsed = JSON.parse(raw);
          if (Array.isArray(parsed) && parsed.length) return parsed;
        } catch (e) {
          console.error('product-animator: invalid data-sources JSON', e);
        }
      }
      const fam = root.dataset.family;
      if (fam) return [{ slug: fam, label: fam.toUpperCase(), family: fam }];
      return [];
    }

    _renderShell() {
      const hasSourcePicker = this.sources.length > 1;
      this.root.innerHTML = `
        <div class="pa-head">
          <div class="pa-title">
            <span class="pa-family-label">Loading…</span>
          </div>
          <div class="pa-controls">
            ${hasSourcePicker ? `
              <label class="pa-ctrl">
                <span>Source</span>
                <select class="pa-select" data-role="source">
                  ${this.sources.map(s => `
                    <option value="${escapeAttr(s.slug)}"${s.disabled ? ' disabled' : ''}
                            title="${escapeAttr(s.disabled ? (s.disabledReason || 'Not available yet') : s.label)}"
                    >${escapeHTML(s.label)}${s.disabled ? ' (coming soon)' : ''}</option>
                  `).join('')}
                </select>
              </label>
            ` : ''}
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
                 playsinline preload="metadata"></video>
          <div class="pa-status" data-role="status">Loading manifest…</div>
        </div>
        <div class="pa-scrub-row">
          <button class="pa-play" data-role="play" type="button"
                  aria-label="Play/pause" disabled>▶</button>
          <input class="pa-scrub" data-role="scrub" type="range"
                 min="0" max="1000" step="1" value="0"
                 aria-label="Seek" disabled>
          <span class="pa-date" data-role="date">—</span>
        </div>
        <div class="pa-caption" data-role="caption"></div>
      `;
      this.familyLabelEl = this.root.querySelector('.pa-family-label');
      this.sourceSelect  = this.root.querySelector('[data-role="source"]');
      this.regionSelect  = this.root.querySelector('[data-role="region"]');
      this.productSelect = this.root.querySelector('[data-role="product"]');
      this.timescaleSel  = this.root.querySelector('[data-role="timescale"]');
      this.speedSelect   = this.root.querySelector('[data-role="speed"]');
      this.downloadBtn   = this.root.querySelector('[data-role="download"]');
      this.video         = this.root.querySelector('[data-role="video"]');
      this.statusEl      = this.root.querySelector('[data-role="status"]');
      this.playBtn       = this.root.querySelector('[data-role="play"]');
      this.scrub         = this.root.querySelector('[data-role="scrub"]');
      this.dateEl        = this.root.querySelector('[data-role="date"]');
      this.captionEl     = this.root.querySelector('[data-role="caption"]');
    }

    _wireEvents() {
      if (this.sourceSelect) {
        this.sourceSelect.addEventListener('change', () => {
          this._setSource(this.sourceSelect.value);
        });
      }
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

      // Loop within the user-selected timescale window.
      this.video.addEventListener('timeupdate', () => {
        if (!this.video.duration || !isFinite(this.video.duration)) return;
        if (this.video.currentTime >= this.video.duration - 0.05) {
          try { this.video.currentTime = this._clipStart; }
          catch (e) { /* iOS sometimes blocks pre-metadata seeks */ }
        }
        if (!this._scrubbing) this._syncScrubberFromVideo();
      });
      this.video.addEventListener('loadedmetadata', () => {
        this._applyClipBounds();
        this.video.playbackRate = this.speed;
        this._syncScrubberFromVideo();
        this.scrub.disabled = false;
        this.playBtn.disabled = false;
      });
      this.video.addEventListener('error', () => {
        this._setStatus('Video failed to load. The MP4 may not be ready yet.');
      });
      this.video.addEventListener('play',  () => this._updatePlayIcon());
      this.video.addEventListener('pause', () => this._updatePlayIcon());
      this.video.addEventListener('ended', () => this._updatePlayIcon());

      this.playBtn.addEventListener('click', () => this._togglePlay());
      // Space-to-toggle when the button has focus, matching the
      // keyboard behavior of a native <video controls>.
      this.playBtn.addEventListener('keydown', (e) => {
        if (e.code === 'Space' || e.key === ' ') {
          e.preventDefault();
          this._togglePlay();
        }
      });

      // Scrubber: pause during drag, seek on input, optionally resume.
      this.scrub.addEventListener('pointerdown', () => {
        this._scrubbing = true;
        this._wasPlayingBeforeScrub = !this.video.paused;
        if (this._wasPlayingBeforeScrub) this.video.pause();
      });
      this.scrub.addEventListener('input', () => {
        const dur = this.video.duration;
        if (!dur || !isFinite(dur)) return;
        // Range maps 0→1000 onto [_clipStart, duration]. Stays inside
        // the active timescale window so drag can't escape it.
        const frac = parseInt(this.scrub.value, 10) / 1000;
        const t = this._clipStart + frac * (dur - this._clipStart);
        try { this.video.currentTime = t; } catch (e) { /* noop */ }
        this._updateDateLabel(this.video.currentTime);
      });
      const endScrub = () => {
        if (!this._scrubbing) return;
        this._scrubbing = false;
        if (this._wasPlayingBeforeScrub) {
          this.video.play().catch(() => {});
        }
      };
      this.scrub.addEventListener('pointerup', endScrub);
      this.scrub.addEventListener('pointercancel', endScrub);
      this.scrub.addEventListener('change', endScrub);
    }

    _setSource(slug) {
      const src = this.sources.find(s => s.slug === slug);
      if (!src) return;
      this.sourceSlug = slug;
      if (this.sourceSelect) this.sourceSelect.value = slug;

      if (src.disabled) {
        // Freeze the surface; tell the user what's going on.
        this.manifest = null;
        this.video.removeAttribute('src');
        this.video.load();
        this.regionSelect.disabled = true;
        this.productSelect.disabled = true;
        this.timescaleSel.disabled = true;
        this.speedSelect.disabled = true;
        this.downloadBtn.hidden = true;
        this.playBtn.disabled = true;
        this.scrub.disabled = true;
        this.dateEl.textContent = '—';
        this.captionEl.textContent = '';
        this.familyLabelEl.textContent = src.label;
        this._setStatus(src.disabledReason || 'Animations coming soon.');
        return;
      }

      this._setStatus('Loading manifest…');
      this._loadManifestFor(src.family).then((manifest) => {
        if (this.sourceSlug !== slug) return; // user switched again
        this.manifest = manifest;
        this._populateFromManifest(src);
        this._loadCurrent();
      }).catch((e) => {
        if (this.sourceSlug !== slug) return;
        this._setStatus(
          `Animations are not available yet — the orphan ` +
          `\`mp4-artifacts\` branch hasn't been published for ` +
          `${src.family}. (${e.message})`
        );
      });
    }

    _loadManifestFor(family) {
      if (this.manifestsByFamily.has(family)) {
        return Promise.resolve(this.manifestsByFamily.get(family));
      }
      if (this.manifestPromises.has(family)) {
        return this.manifestPromises.get(family);
      }
      const url = `${ARTIFACTS_BASE}/${family}/manifest.json?t=${Date.now()}`;
      const p = fetch(url, { cache: 'no-store' })
        .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
        .then(m => { this.manifestsByFamily.set(family, m); return m; })
        .finally(() => { this.manifestPromises.delete(family); });
      this.manifestPromises.set(family, p);
      return p;
    }

    _populateFromManifest(src) {
      const m = this.manifest;
      const win = m.window || { unit: 'days', length: 90 };
      const unit = win.unit;
      const len  = win.length;

      this.familyLabelEl.textContent =
        `${src.label} · ${len}-${unit.replace(/s$/, '')} MP4 animations`;

      // Regions — sort with Global / Global Tropics pinned to the top.
      // If the source declares a `regions` allow-list, filter down to it.
      let regions = (m.regions || []).slice();
      if (Array.isArray(src.regions) && src.regions.length) {
        const allow = new Set(src.regions);
        regions = regions.filter(r => allow.has(r.slug));
      }
      regions.sort((a, b) => {
        if (a.slug === 'global') return -1;
        if (b.slug === 'global') return 1;
        if (a.slug === 'global-tropics') return -1;
        if (b.slug === 'global-tropics') return 1;
        return a.label.localeCompare(b.label);
      });
      // Preserve current region selection across source switches when it
      // still exists in the new source's regions.
      const wantRegion = this.regionSlug && regions.find(r => r.slug === this.regionSlug)
        ? this.regionSlug : (regions[0] ? regions[0].slug : null);
      this.regionSelect.innerHTML = regions.map(r =>
        `<option value="${escapeAttr(r.slug)}">${escapeHTML(r.label)}</option>`
      ).join('');
      this.regionSelect.disabled = false;
      this.regionSelect.value = wantRegion;
      this.regionSlug = wantRegion;

      // Products — filter to the source's declared list, preserving
      // manifest order so the renderer controls presentation.
      let products = m.products || [];
      if (Array.isArray(src.products) && src.products.length) {
        const allow = new Set(src.products);
        products = products.filter(p => allow.has(p.slug));
      }
      const wantProduct = this.productSlug && products.find(p => p.slug === this.productSlug)
        ? this.productSlug : (products[0] ? products[0].slug : null);
      this.productSelect.innerHTML = products.map(p =>
        `<option value="${escapeAttr(p.slug)}">${escapeHTML(p.label)}</option>`
      ).join('');
      this.productSelect.disabled = false;
      this.productSelect.value = wantProduct;
      this.productSlug = wantProduct;

      // Timescale: thirds of the full window, full window last + default.
      const thirds = [Math.round(len / 3), Math.round(2 * len / 3), len]
        .filter((v, i, a) => a.indexOf(v) === i && v > 0);
      this.timescaleSel.innerHTML = thirds.map(n =>
        `<option value="${n}"${n === len ? ' selected' : ''}>${n} ${unit}</option>`
      ).join('');
      this.timescaleSel.disabled = false;
      this.timescale = len;

      this.speedSelect.disabled = false;
      this.speed = 1;
      this.speedSelect.value = '1';

      this.downloadBtn.hidden = false;
      this._setStatus('');
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
        this.scrub.disabled = true;
        this.playBtn.disabled = true;
        this.dateEl.textContent = '—';
        return;
      }
      this._setStatus('');
      this._currentClip = clip;
      const v = encodeURIComponent(this.manifest.generated_at || '');
      const family = (this.sources.find(s => s.slug === this.sourceSlug) || {}).family
                   || this.manifest.family || '';
      const base = `${ARTIFACTS_BASE}/${family}`;
      const mp4Url    = `${base}/${clip.src}?v=${v}`;
      const posterUrl = `${base}/${clip.poster}?v=${v}`;
      this.video.poster = posterUrl;
      this.video.src = mp4Url;
      this.video.load();
      this.video.playbackRate = this.speed;

      // Download button — always points at the full clip.
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
      this.dateEl.textContent = clip.first_frame ? fmtDate(clip.first_frame) : '—';
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
      this._syncScrubberFromVideo();
    }

    _togglePlay() {
      if (!this.video.src) return;
      if (this.video.paused || this.video.ended) {
        // If we looped past the clip window, jump back to its start.
        if (this.video.currentTime < this._clipStart ||
            this.video.currentTime >= this.video.duration - 0.05) {
          try { this.video.currentTime = this._clipStart; } catch (e) { /* noop */ }
        }
        this.video.play().catch(() => {});
      } else {
        this.video.pause();
      }
    }

    _updatePlayIcon() {
      this.playBtn.textContent = (this.video.paused || this.video.ended) ? '▶' : '❚❚';
    }

    _syncScrubberFromVideo() {
      const dur = this.video.duration;
      if (!dur || !isFinite(dur)) return;
      const windowDur = dur - this._clipStart;
      if (windowDur <= 0) return;
      const frac = (this.video.currentTime - this._clipStart) / windowDur;
      this.scrub.value = String(Math.max(0, Math.min(1000, Math.round(frac * 1000))));
      this._updateDateLabel(this.video.currentTime);
    }

    _updateDateLabel(currentTime) {
      const clip = this._currentClip;
      if (!clip || !clip.first_frame || !clip.last_frame) return;
      const dur = this.video.duration;
      if (!dur || !isFinite(dur)) return;
      const start = Date.UTC(
        +clip.first_frame.slice(0, 4),
        +clip.first_frame.slice(5, 7) - 1,
        +clip.first_frame.slice(8, 10));
      const end = Date.UTC(
        +clip.last_frame.slice(0, 4),
        +clip.last_frame.slice(5, 7) - 1,
        +clip.last_frame.slice(8, 10));
      const spanDays = Math.max(1, Math.round((end - start) / 86400000));
      const frac = Math.max(0, Math.min(1, currentTime / dur));
      const dayIdx = Math.round(frac * spanDays);
      const d = new Date(start + dayIdx * 86400000);
      this.dateEl.textContent = d.toLocaleDateString('en-US', {
        month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC',
      });
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
  function escapeAttr(s) { return escapeHTML(s); }

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
