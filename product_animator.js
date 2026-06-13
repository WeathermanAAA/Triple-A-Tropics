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
 *        {"slug":"aoml","label":"AOML","products":[
 *           {"slug":"tchp","family":"aoml_tchp"},
 *           {"slug":"d26", "family":"aoml_d26"}]},
 *        {"slug":"armor3d","label":"ARMOR3D","disabled":true,
 *         "disabledReason":"Animations coming soon"}
 *      ]'></div>
 *
 *   `products` may be an array of slug strings (uses the source-level
 *   `family`) OR an array of objects `{slug, label?, family?}` so that
 *   one source can fan out across multiple `{family}/` directories
 *   on the CDN — each product picks its own manifest. The widget
 *   fetches each distinct family's manifest on demand (and memoizes),
 *   merges products + regions across them, and swaps the active
 *   manifest when the user changes product. Disabled sources show a
 *   status message and freeze the video surface.
 *
 * URLs it hits (on cdn.triple-a-tropics.com / Cloudflare R2):
 *   .../{family}/manifest.json
 *   .../{family}/{region}_{product}.mp4
 *   .../{family}/{region}_{product}.jpg
 *
 * Cache-busting: every URL gets ?v=encodeURIComponent(generated_at)
 * appended so a fresh workflow run invalidates browser/CDN cache.
 */
(function () {
  'use strict';

  // Media lives on Cloudflare R2 (cdn.triple-a-tropics.com), with the
  // mp4-artifacts family paths mirrored verbatim at the CDN root:
  //   {family}/manifest.json, {family}/{region}_{product}.{mp4,jpg}.
  // (Was raw.githubusercontent.com/.../mp4-artifacts before R2 phase 3.)
  const ARTIFACTS_BASE = 'https://cdn.triple-a-tropics.com';

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
      this.manifest = null;                 // active manifest (current product's family)
      this._productFamily = new Map();      // product slug → family (for active source)
      this.regionSlug = null;
      this.productSlug = null;
      this.timescale = null;
      this.speed = 1;
      this._clipStart = 0;       // window-start TIME (s) within the MP4
      this._frames = 0;          // total frames in the active clip
      this._winStart = 0;        // global index of the window's first frame
      this._winFrames = 0;       // frames visible in the selected window
      this._scrubbing = false;
      this._wasPlayingBeforeScrub = false;
      // Frame-accurate playhead when available (smooth, per-presented-frame);
      // the 'timeupdate' handler (~4 Hz) is the fallback for browsers without it.
      this._rvfc = (typeof HTMLVideoElement !== 'undefined' &&
                    'requestVideoFrameCallback' in HTMLVideoElement.prototype);
      this._rvfcGen = 0;         // bumped each load so a stale rVFC chain dies
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
            <label class="pa-ctrl"
                   title="Affects playback only: downloaded MP4s play at native FPS.">
              <span>Speed <span class="pa-meta">(playback)</span></span>
              <select class="pa-select" data-role="speed" disabled
                      title="Affects playback only: downloaded MP4s play at native FPS.">
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
          <div class="pa-spinner" data-role="spinner" hidden aria-hidden="true"></div>
          <div class="pa-status" data-role="status">Loading manifest…</div>
        </div>
        <div class="pa-scrub-row">
          <button class="pa-play" data-role="play" type="button"
                  aria-label="Play/pause" disabled>▶</button>
          <input class="pa-scrub" data-role="scrub" type="range"
                 min="0" max="1000" step="1" value="0"
                 aria-label="Seek" disabled>
          <span class="pa-date" data-role="date">-</span>
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
      this.spinnerEl     = this.root.querySelector('[data-role="spinner"]');
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

      // FALLBACK loop + playhead for browsers without requestVideoFrameCallback.
      // When rVFC is available it owns the loop (frame-accurate, no jitter) and
      // the playhead (per presented frame, smooth), so this no-ops.
      this.video.addEventListener('timeupdate', () => {
        if (this._rvfc) return;
        if (!this.video.duration || !isFinite(this.video.duration)) return;
        if (this.video.currentTime >= this.video.duration - 0.05) {
          try { this.video.currentTime = this._clipStart; }
          catch (e) { /* iOS sometimes blocks pre-metadata seeks */ }
        }
        if (!this._scrubbing) this._syncFromVideo();
      });
      this.video.addEventListener('loadedmetadata', () => {
        this._applyClipBounds();
        this.video.playbackRate = this.speed;
        this.scrub.disabled = false;
        this.playBtn.disabled = false;
        // POSTER/LABEL AGREEMENT, crisply: the poster IS the newest frame and
        // is a sharp JPG, so DON'T seek (a seek would repaint it as a soft
        // decoded yuv420p frame). Just park the scrubber + date on that newest
        // frame so the load state agrees; currentTime stays at the window
        // start, ready for play to animate forward.
        this.scrub.value = String(Math.max(0, this._winFrames - 1));
        this.dateEl.textContent = this._dateForFrame(this._frames - 1);
        // Start the frame-accurate playhead/loop chain for this load.
        this._rvfcGen += 1;
        this._scheduleRvfc(this._rvfcGen);
      });
      this.video.addEventListener('error', () => {
        this._showSpinner(false);
        this._setStatus('Video failed to load. The MP4 may not be ready yet.');
      });
      this.video.addEventListener('play',  () => this._updatePlayIcon());
      this.video.addEventListener('pause', () => this._updatePlayIcon());
      this.video.addEventListener('ended', () => this._updatePlayIcon());

      // Buffering / seeking affordance: a scrub or a cold seek can stall while
      // the browser fetches the target keyframe — show a spinner so it never
      // looks frozen. Cleared as soon as the frame is ready / playback resumes.
      this.video.addEventListener('seeking', () => this._showSpinner(true));
      this.video.addEventListener('waiting', () => this._showSpinner(true));
      this.video.addEventListener('seeked',  () => this._showSpinner(false));
      this.video.addEventListener('canplay', () => this._showSpinner(false));
      this.video.addEventListener('playing', () => this._showSpinner(false));

      this.playBtn.addEventListener('click', () => this._togglePlay());

      // Scrubber: pause during drag, FRAME-QUANTIZED seek on input, resume.
      // The range is one step per frame (set in _applyClipBounds), so every
      // value maps to a real frame and the date label can never drift.
      this.scrub.addEventListener('pointerdown', () => {
        this._scrubbing = true;
        this._wasPlayingBeforeScrub = !this.video.paused;
        if (this._wasPlayingBeforeScrub) this.video.pause();
      });
      this.scrub.addEventListener('input', () => {
        const dur = this.video.duration;
        if (!dur || !isFinite(dur) || !this._winFrames) return;
        const gf = this._winStart + (parseInt(this.scrub.value, 10) || 0);
        try { this.video.currentTime = this._timeForFrame(gf); } catch (e) { /* noop */ }
        this.dateEl.textContent = this._dateForFrame(gf);
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

      // Transport keyboard, scoped to the player (events bubble up from the
      // focused control). ←/→ step one frame (pausing); Space toggles play.
      // SELECT / TEXTAREA / links keep their native key behavior.
      this.root.addEventListener('keydown', (e) => {
        const tag = e.target && e.target.tagName;
        if (tag === 'SELECT' || tag === 'TEXTAREA' || tag === 'A') return;
        if (e.key === 'ArrowLeft') { e.preventDefault(); this._stepFrame(-1); }
        else if (e.key === 'ArrowRight') { e.preventDefault(); this._stepFrame(1); }
        else if (e.key === ' ' || e.code === 'Space') { e.preventDefault(); this._togglePlay(); }
      });
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
        this.dateEl.textContent = '-';
        this.captionEl.textContent = '';
        this.familyLabelEl.textContent = src.label;
        this._setStatus(src.disabledReason || 'Animations coming soon.');
        return;
      }

      this._setStatus('Loading manifest…');
      const families = this._resolveSourceFamilies(src);
      Promise.all(families.map(f => this._loadManifestFor(f)))
        .then((manifests) => {
          if (this.sourceSlug !== slug) return; // user switched again
          this._populateFromManifests(src, families, manifests);
          this._loadCurrent();
        }).catch((e) => {
          if (this.sourceSlug !== slug) return;
          this._setStatus(
            `Animations are not available yet, the CDN hasn't ` +
            `published media for ` +
            `${families.join(', ') || src.family || src.slug}. (${e.message})`
          );
        });
    }

    _resolveSourceFamilies(src) {
      // Distinct families this source pulls from. Source-level `family`
      // (legacy) plus any per-product `family` overrides.
      const set = new Set();
      if (src.family) set.add(src.family);
      if (Array.isArray(src.products)) {
        for (const p of src.products) {
          if (p && typeof p === 'object' && p.family) set.add(p.family);
        }
      }
      return Array.from(set);
    }

    _normalizeProductSlugs(src) {
      if (!Array.isArray(src.products) || !src.products.length) return null;
      return new Set(src.products.map(p => typeof p === 'object' ? p.slug : p));
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

    _populateFromManifests(src, families, manifests) {
      // Build product → family map. Manifest-level entries first (every
      // product the manifests advertise), then explicit per-product
      // overrides from the data-sources attribute win.
      this._productFamily = new Map();
      for (let i = 0; i < manifests.length; i++) {
        const m = manifests[i];
        const fam = families[i];
        for (const p of (m.products || [])) {
          if (!this._productFamily.has(p.slug)) this._productFamily.set(p.slug, fam);
        }
      }
      if (Array.isArray(src.products)) {
        for (const p of src.products) {
          if (p && typeof p === 'object' && p.slug && p.family) {
            this._productFamily.set(p.slug, p.family);
          }
        }
      }

      // Use the first manifest as the metadata anchor (window/title).
      // For our use cases all of a source's families share the same
      // window length and unit; if they ever diverge, _loadCurrent will
      // re-anchor `this.manifest` to the active product's family.
      const primary = manifests[0];
      this.manifest = primary;
      const win = primary.window || { unit: 'days', length: 90 };
      const unit = win.unit;
      const len  = win.length;

      this.familyLabelEl.textContent =
        `${src.label} · ${len}-${unit.replace(/s$/, '')} MP4 animations`;

      // Regions — union across all manifests (so a source can span
      // families with overlapping but not identical region sets).
      // Pin Global / Global Tropics to the top; alpha after that.
      const regionMap = new Map();
      for (const m of manifests) {
        for (const r of (m.regions || [])) {
          if (!regionMap.has(r.slug)) regionMap.set(r.slug, r);
        }
      }
      let regions = Array.from(regionMap.values());
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
      const wantRegion = this.regionSlug && regions.find(r => r.slug === this.regionSlug)
        ? this.regionSlug : (regions[0] ? regions[0].slug : null);
      this.regionSelect.innerHTML = regions.map(r =>
        `<option value="${escapeAttr(r.slug)}">${escapeHTML(r.label)}</option>`
      ).join('');
      this.regionSelect.disabled = false;
      this.regionSelect.value = wantRegion;
      this.regionSlug = wantRegion;

      // Products — union across manifests, filtered by the source's
      // declared allow-list. If the source provided product objects,
      // honor their order; otherwise fall back to manifest order.
      const allowedSlugs = this._normalizeProductSlugs(src);
      const productMap = new Map();
      for (const m of manifests) {
        for (const p of (m.products || [])) {
          if (allowedSlugs && !allowedSlugs.has(p.slug)) continue;
          if (!productMap.has(p.slug)) productMap.set(p.slug, p);
        }
      }
      let products = Array.from(productMap.values());
      if (Array.isArray(src.products) && src.products.length) {
        const order = new Map();
        src.products.forEach((p, i) => {
          order.set(typeof p === 'object' ? p.slug : p, i);
        });
        products.sort((a, b) =>
          (order.has(a.slug) ? order.get(a.slug) : 999) -
          (order.has(b.slug) ? order.get(b.slug) : 999));
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
      if (!this.regionSlug || !this.productSlug) return;
      const src = this.sources.find(s => s.slug === this.sourceSlug);
      if (!src) return;
      // Resolve the family that owns this product (may differ per
      // product within a single source — e.g. AOML/TCHP → aoml_tchp,
      // AOML/D26 → aoml_d26) and pin `this.manifest` to it so the
      // clip + product lookups below all read from the right manifest.
      const family = this._productFamily.get(this.productSlug)
                  || src.family
                  || (this.manifest && this.manifest.family) || '';
      const m = (family && this.manifestsByFamily.get(family)) || this.manifest;
      if (!m) return;
      this.manifest = m;
      const key = `${this.regionSlug}_${this.productSlug}`;
      const clip = (m.clips || {})[key];
      if (!clip) {
        this._setStatus(`No clip for ${this.regionSlug} · ${this.productSlug} yet.`);
        this.video.removeAttribute('src');
        this.video.load();
        this.captionEl.textContent = '';
        this.downloadBtn.removeAttribute('href');
        this.scrub.disabled = true;
        this.playBtn.disabled = true;
        this.dateEl.textContent = '-';
        return;
      }
      this._setStatus('');
      this._currentClip = clip;
      const v = encodeURIComponent(m.generated_at || '');
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
      // Filename embeds the manifest's date range so saved files self-describe
      // their window. Falls back to today's date when the manifest hasn't
      // emitted first/last frame yet.
      const todayISO = new Date().toISOString().slice(0, 10);
      const first = clip.first_frame || todayISO;
      const last  = clip.last_frame  || todayISO;
      this.downloadBtn.download =
        `triple-a-tropics_${slugify(this.productSlug)}_${slugify(this.regionSlug)}_${first}_to_${last}.mp4`;

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
        `<b>${escapeHTML((region && region.label) || this.regionSlug)}</b>: ` +
        `${escapeHTML(desc || '')}` +
        `<span class="pa-meta">${range}${sizeMB}</span>`;
      // Initial label = the LAST (newest) frame, matching the poster (which is
      // the newest frame) so the load state is coherent before metadata lands.
      this.dateEl.textContent = clip.last_frame ? fmtDate(clip.last_frame)
                              : (clip.first_frame ? fmtDate(clip.first_frame) : '-');
    }

    _applyClipBounds() {
      const win = this.manifest && this.manifest.window;
      if (!win) return;
      const dur = this.video.duration;
      if (!dur || !isFinite(dur)) return;
      const N = this._frameCount();
      // Window = the newest `timescale` frames (clamped to what the clip has).
      const winFrames = Math.max(1, Math.min(this.timescale || win.length, N));
      this._frames = N;
      this._winFrames = winFrames;
      this._winStart = N - winFrames;
      // START-EDGE of the window's first frame (NOT the mid-slot): at the
      // default full window this is 0, so the load-time clamp below does not
      // fire and the crisp poster JPG is preserved (a metadata-preload seek
      // would replace it with a soft fast-seek frame on Safari).
      this._clipStart = (this._winStart / N) * dur;
      // FRAME-QUANTIZED scrubber: exactly one step per frame in the window, so
      // every thumb position lands on a real frame (no 1000-step ±1-day drift).
      this.scrub.min = '0';
      this.scrub.step = '1';
      this.scrub.max = String(winFrames - 1);
      try {
        if (this.video.currentTime < this._clipStart) {
          this.video.currentTime = this._clipStart;
        }
      } catch (e) { /* swallow pre-metadata seek errors */ }
      this._syncFromVideo();
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

    // ---- frame <-> time <-> date (the scrubber is frame-quantized) ----------

    // Total frames in the active clip. Authoritative from the manifest
    // (clip.frames); falls back to the window length, then the date span.
    _frameCount() {
      const c = this._currentClip;
      const win = this.manifest && this.manifest.window;
      let n = (c && c.frames) || (win && win.length) || 0;
      if (!n && c && c.first_frame && c.last_frame) {
        const s = Date.UTC(+c.first_frame.slice(0, 4), +c.first_frame.slice(5, 7) - 1,
                           +c.first_frame.slice(8, 10));
        const e = Date.UTC(+c.last_frame.slice(0, 4), +c.last_frame.slice(5, 7) - 1,
                           +c.last_frame.slice(8, 10));
        n = Math.round((e - s) / 86400000) + 1;
      }
      return Math.max(1, n);
    }

    // Time (s) at the CENTER of global frame gf's slot, so a seek lands cleanly
    // inside that frame rather than on a slot boundary.
    _timeForFrame(gf) {
      const dur = this.video.duration;
      const N = this._frameCount();
      if (!dur || !isFinite(dur) || !N) return 0;
      const g = Math.max(0, Math.min(N - 1, gf));
      return Math.min(dur - 1e-3, (g + 0.5) * dur / N);
    }

    // Global frame index displayed at time t.
    _globalFrameAt(t) {
      const dur = this.video.duration;
      const N = this._frameCount();
      if (!dur || !isFinite(dur) || !N) return 0;
      return Math.max(0, Math.min(N - 1, Math.floor(t * N / dur)));
    }

    // Exact date for a frame: uniform daily cadence (confirmed: clip.frames ==
    // date span), so date = first_frame + gf days. No time->date interpolation,
    // hence no ±1-day drift, and no per-frame date array needed in the manifest.
    _dateForFrame(gf) {
      const c = this._currentClip;
      if (!c || !c.first_frame) return '-';
      const start = Date.UTC(+c.first_frame.slice(0, 4), +c.first_frame.slice(5, 7) - 1,
                             +c.first_frame.slice(8, 10));
      const d = new Date(start + Math.max(0, gf) * 86400000);
      return d.toLocaleDateString('en-US', {
        month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC',
      });
    }

    _syncFromVideo() {
      const dur = this.video.duration;
      if (!dur || !isFinite(dur) || !this._winFrames) return;
      const gf = this._globalFrameAt(this.video.currentTime);
      const v = Math.max(0, Math.min(this._winFrames - 1, gf - this._winStart));
      this.scrub.value = String(v);
      this.dateEl.textContent = this._dateForFrame(gf);
    }

    // ←/→ transport: step one frame within the window, pausing playback.
    _stepFrame(delta) {
      if (!this.video.src || !this._winFrames) return;
      if (!this.video.paused) this.video.pause();
      const cur = this._globalFrameAt(this.video.currentTime);
      const gf = Math.max(this._winStart, Math.min(this._frames - 1, cur + delta));
      try { this.video.currentTime = this._timeForFrame(gf); } catch (e) { /* noop */ }
      this.scrub.value = String(Math.max(0, Math.min(this._winFrames - 1, gf - this._winStart)));
      this.dateEl.textContent = this._dateForFrame(gf);
    }

    // Frame-accurate playhead + clean loop. rVFC fires once per PRESENTED frame
    // (only while playing or after a seek), so the scrubber/date track smoothly
    // and the loop restarts exactly at the last frame — no `duration - 0.05`
    // overshoot/jitter. `gen` retires the chain when a new clip loads.
    _scheduleRvfc(gen) {
      if (!this._rvfc) return;
      this.video.requestVideoFrameCallback((now, metadata) => {
        if (gen !== this._rvfcGen) return;   // superseded by a newer load
        const dur = this.video.duration;
        if (dur && isFinite(dur) && this._winFrames) {
          const t = (metadata && typeof metadata.mediaTime === 'number')
            ? metadata.mediaTime : this.video.currentTime;
          const gf = this._globalFrameAt(t);
          if (!this.video.paused && gf >= this._frames - 1) {
            try { this.video.currentTime = this._timeForFrame(this._winStart); }
            catch (e) { /* noop */ }
          } else if (!this._scrubbing) {
            const v = Math.max(0, Math.min(this._winFrames - 1, gf - this._winStart));
            this.scrub.value = String(v);
            this.dateEl.textContent = this._dateForFrame(gf);
          }
        }
        this._scheduleRvfc(gen);
      });
    }

    _showSpinner(on) {
      if (!this.spinnerEl) return;
      this.spinnerEl.hidden = !on;
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
  function slugify(s) {
    return String(s == null ? '' : s).toLowerCase()
      .replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
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
