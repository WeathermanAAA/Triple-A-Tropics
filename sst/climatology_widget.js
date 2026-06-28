/*
 * climatology_widget.js — Triple-A-Tropics /sst/ page
 * -------------------------------------------------------------
 * Interactive daily-SST climatology curves (kouya-style spaghetti),
 * drawn as an inline <svg> the browser renders from per-region data JSON
 * — no baked image, no CDN charting deps. One grouped Region dropdown
 * picks the region; a YEAR multi-select toggles any individual season on
 * or off. The 1991–2020 daily mean and the current year are on by default.
 *
 * The SVG idiom (el/clear, month gridlines, niceStep ticks, linePath, the
 * y-invert (M.t+PH)-(v/max)*PH form, the createSVGPoint()+getScreenCTM()
 * crosshair transform) mirrors _ace_template.py's hand-rolled charts so
 * the two products read as siblings.
 *
 * Data contract (sst/{region}_climatology.json, written by
 * generate_sst_climatology.py):
 *   { slug, label, cur, years:[...], series:{ "YYYY":[v_or_null x366] },
 *     clim:[v x366], latest:{doy,val}, ymin, ymax, default_years:[...],
 *     highlight_colors:{ "YYYY":"#hex" }, units, climo_label }
 * series index i → day-of-year position i+1 (1..366); null = NCEI gap.
 *
 * Manifest contract:
 *   <div id="sstClimatology" data-manifest="/sst/climatology_manifest.json">
 *   manifest: { subtitle, data_path_template, inset_path_template,
 *               path_template (no-JS PNG fallback), meta_url,
 *               region_labels, region_groups, default_region }
 *
 * Deliberately does NOT touch location.hash — the static-map widget on the
 * same page owns the hash; a second writer would fight it.
 */
(function () {
  'use strict';

  const ROOT_SELECTOR = '#sstClimatology';
  const NS = 'http://www.w3.org/2000/svg';

  // ---- SVG viewBox geometry (mirrors _ace_template.py) ----
  const W = 1000, H = 460;
  const M_L = 56, M_R = 16, M_T = 14, M_B = 30;
  const PW = W - M_L - M_R;          // plot width
  const PH = H - M_T - M_B;          // plot height

  const MONTH_STARTS = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335];
  const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  const MEAN_KEY = '__mean__';                 // pseudo-entry for the climo line
  const CYAN = '#5dd3ff';
  // Deterministic fallback palette for selected years without a configured
  // colour (assigned newest-first), echoing the PNG's HIGHLIGHT_PALETTE.
  const FALLBACK_PALETTE = [
    '#ef5350', '#ffb83a', '#c792ea', '#7bd88f', '#ff8a65', '#f06292',
    '#4dd0e1', '#aed581', '#ba68c8', '#ffd54f',
  ];

  // x scale: doy 1..366 → viewBox x. Inverse snaps a pixel back to a doy.
  const xs = (doy) => M_L + (doy - 1) / 365 * PW;
  const xToDoy = (x) => Math.max(1, Math.min(366,
    Math.round(1 + (x - M_L) / PW * 365)));

  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function niceStep(x) {
    if (!isFinite(x) || x <= 0) return 1;
    const pow = Math.pow(10, Math.floor(Math.log10(x)));
    const n = x / pow;
    let step;
    if (n < 1.5) step = 1; else if (n < 3) step = 2;
    else if (n < 7) step = 5; else step = 10;
    return step * pow;
  }

  class ClimatologyWidget {
    constructor(root, manifest) {
      this.root = root;
      this.m = manifest;
      this.regionSlug = null;
      this.data = null;            // current region's JSON payload
      this.selected = new Set();   // chosen year-keys (strings) + MEAN_KEY
      this._reqToken = 0;          // guards against out-of-order fetches
      this._hoverFrame = null;
      this._hoverEvt = null;
      this._render();
      this._pickDefault();
      this._loadRegion();
      this._loadMeta();
    }

    _render() {
      this.root.innerHTML = `
        <div class="sw-head">
          <div class="sw-titlerow">
            <h3 class="sw-title" data-role="title"></h3>
            <div class="sw-meta" data-role="meta"></div>
          </div>
          <div class="sw-subtitle" data-role="subtitle"></div>
        </div>
        <div class="sw-controls">
          <label class="sw-ctrl">
            <span>Region</span>
            <select class="sw-select" data-role="region"></select>
          </label>
          <div class="sw-ctrl swc-years-ctrl">
            <span>Years</span>
            <div class="swc-years">
              <div class="swc-years-actions">
                <button type="button" class="swc-chip" data-act="recent">Recent 3</button>
                <button type="button" class="swc-chip" data-act="all">All</button>
                <button type="button" class="swc-chip" data-act="clear">Clear</button>
              </div>
              <div class="swc-years-list" data-role="years"></div>
            </div>
          </div>
        </div>
        <section class="chart-section">
          <div class="chart-card swc-card">
            <div class="swc-row">
              <div class="swc-chart-wrap">
                <svg class="swc-svg" data-role="svg" viewBox="0 0 ${W} ${H}"
                     preserveAspectRatio="xMidYMid meet" role="img"></svg>
                <div class="swc-tip" data-role="tip" aria-hidden="true"></div>
              </div>
              <figure class="swc-inset">
                <img class="swc-inset-img" data-role="inset" alt="" loading="lazy">
                <figcaption class="swc-inset-cap" data-role="insetcap"></figcaption>
              </figure>
            </div>
            <div class="sst-caption swc-caption" data-role="caption"></div>
          </div>
        </section>
      `;
      this.titleEl    = this.root.querySelector('[data-role="title"]');
      this.subtitleEl = this.root.querySelector('[data-role="subtitle"]');
      this.metaEl     = this.root.querySelector('[data-role="meta"]');
      this.regionSel  = this.root.querySelector('[data-role="region"]');
      this.yearsEl    = this.root.querySelector('[data-role="years"]');
      this.svg        = this.root.querySelector('[data-role="svg"]');
      this.tipEl      = this.root.querySelector('[data-role="tip"]');
      this.insetImg   = this.root.querySelector('[data-role="inset"]');
      this.insetCap   = this.root.querySelector('[data-role="insetcap"]');
      this.captionEl  = this.root.querySelector('[data-role="caption"]');
      this.chartWrap  = this.root.querySelector('.swc-chart-wrap');

      this.subtitleEl.textContent = this.m.subtitle || '';

      // Grouped region options (mirrors the static widget's layout).
      const groups = (this.m.region_groups || []).map(g => ({
        label: g.label, regions: (g.regions || []),
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
        this._loadRegion();
      });

      // Quick year affordances.
      this.root.querySelectorAll('.swc-chip').forEach(btn => {
        btn.addEventListener('click', () => this._applyYearAction(btn.dataset.act));
      });

      // Hover crosshair (rAF-throttled; mirrors _ace_template.py).
      this.svg.addEventListener('mousemove', (e) => this._scheduleHover(e));
      this.svg.addEventListener('mouseleave', () => this._hideHover());
      this.svg.addEventListener('touchmove', (e) => this._scheduleHover(e), { passive: true });
      this.svg.addEventListener('touchend', () => this._hideHover());
    }

    _pickDefault() {
      const groups = this.m.region_groups || [];
      const firstRegion = groups.length && groups[0].regions.length
        ? groups[0].regions[0] : null;
      this.regionSlug = this.m.default_region || firstRegion;
      if (this.regionSlug) this.regionSel.value = this.regionSlug;
    }

    _dataUrl(slug) {
      const tpl = this.m.data_path_template;
      return tpl ? tpl.replace('{region}', slug) : null;
    }
    _insetUrl(slug) {
      const tpl = this.m.inset_path_template;
      return tpl ? tpl.replace('{region}', slug) : null;
    }

    _loadRegion() {
      if (!this.regionSlug) return;
      const label = this.m.region_labels[this.regionSlug] || this.regionSlug;

      // Plot header (the region label) — mirrors the baked PNG's bold
      // left-aligned title (generate_sst_climatology.py header bar).
      if (this.titleEl) this.titleEl.textContent = label;

      // Swap the inset immediately (independent of the JSON fetch).
      const iurl = this._insetUrl(this.regionSlug);
      if (iurl) {
        this.insetImg.src = iurl + '?t=' + Date.now();
        this.insetImg.alt = `${label}: current SST-anomaly map`;
        this.insetImg.style.display = '';
        this.insetCap.textContent = 'current SST anomaly · RdBu −3…+3 °C';
      } else {
        this.insetImg.removeAttribute('src');
        this.insetImg.style.display = 'none';
        this.insetCap.textContent = '';
      }

      this.captionEl.textContent =
        `${label}: region-mean SST by day of year vs the 1991–2020 daily ` +
        `climatology and the full 1982-present record (NOAA OISST). ` +
        `Toggle individual years with the Years control.`;

      const url = this._dataUrl(this.regionSlug);
      if (!url) { this._renderError('No data_path_template in manifest.'); return; }

      const token = ++this._reqToken;
      this._showLoading();
      fetch(url + '?t=' + Date.now(), { cache: 'no-store' })
        .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
        .then(d => {
          if (token !== this._reqToken) return;     // a newer region won
          this.data = d;
          this._initSelection();
          this._buildYearControl();
          this._draw();
        })
        .catch(e => {
          if (token !== this._reqToken) return;
          this._renderError('Could not load region data: ' + e.message);
        });
    }

    // Default ON = the 1991–2020 mean + default_years (cur, cur-1, cur-2).
    _initSelection() {
      this.selected = new Set([MEAN_KEY]);
      const def = (this.data.default_years || []).map(String);
      const present = new Set((this.data.years || []).map(String));
      def.forEach(y => { if (present.has(y)) this.selected.add(y); });
    }

    _applyYearAction(act) {
      if (!this.data) return;
      const years = (this.data.years || []).map(String);
      if (act === 'all') {
        this.selected = new Set([MEAN_KEY, ...years]);
      } else if (act === 'clear') {
        this.selected = new Set();
      } else if (act === 'recent') {
        this.selected = new Set([MEAN_KEY]);
        const def = (this.data.default_years || []).map(String);
        const present = new Set(years);
        def.forEach(y => { if (present.has(y)) this.selected.add(y); });
      }
      this._syncYearControl();
      this._draw();
    }

    _buildYearControl() {
      const years = (this.data.years || []).slice().sort((a, b) => b - a); // newest first
      const cur = this.data.cur;
      const climoLabel = this.data.climo_label || '1991–2020 mean';
      const rows = [];
      rows.push(this._yearRowHTML(MEAN_KEY, climoLabel, this._colorFor(MEAN_KEY)));
      years.forEach(y => {
        const key = String(y);
        const lab = (y === cur) ? `${y} (current)` : key;
        rows.push(this._yearRowHTML(key, lab, this._colorFor(key)));
      });
      this.yearsEl.innerHTML = rows.join('');
      this.yearsEl.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.addEventListener('change', () => {
          if (cb.checked) this.selected.add(cb.value);
          else this.selected.delete(cb.value);
          this._draw();
        });
      });
      this._syncYearControl();
    }

    _yearRowHTML(key, label, color) {
      return `<label class="swc-year">
        <input type="checkbox" value="${escapeAttr(key)}">
        <span class="swc-swatch" style="background:${escapeAttr(color)}"></span>
        <span class="swc-year-lab">${escapeHTML(label)}</span>
      </label>`;
    }

    _syncYearControl() {
      this.yearsEl.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.checked = this.selected.has(cb.value);
      });
    }

    // Colour for a year-key: configured highlight colour, else cyan for the
    // current year, else a deterministic fallback by descending-year rank.
    // The mean pseudo-entry is near-white.
    _colorFor(key) {
      if (key === MEAN_KEY) return '#e8ebef';
      const hc = this.data && this.data.highlight_colors;
      if (hc && hc[key]) return hc[key];
      if (this.data && String(this.data.cur) === key) return CYAN;
      const years = (this.data.years || []).slice().sort((a, b) => b - a);
      const rank = years.map(String).indexOf(key);
      return FALLBACK_PALETTE[(rank < 0 ? 0 : rank) % FALLBACK_PALETTE.length];
    }

    // ---- the SVG chart -------------------------------------------------
    _draw() {
      const svg = this.svg;
      clear(svg);
      const d = this.data;
      if (!d || !d.series) return;

      const ymin = (d.ymin != null) ? d.ymin : 0;
      const ymax = (d.ymax != null && d.ymax > ymin) ? d.ymax : ymin + 1;
      const pad = (ymax - ymin) * 0.04;
      const lo = ymin - pad, hi = ymax + pad;
      const yToPx = (v) => M_T + PH - ((v - lo) / (hi - lo)) * PH;
      this._yToPx = yToPx; this._lo = lo; this._hi = hi;

      // Y gridlines + labels.
      const yStep = niceStep((hi - lo) / 5);
      const yStart = Math.ceil(lo / yStep) * yStep;
      for (let v = yStart; v <= hi; v += yStep) {
        const y = yToPx(v);
        el('line', { x1: M_L, x2: M_L + PW, y1: y, y2: y,
          stroke: 'var(--grid-dim, var(--border))', 'stroke-width': 1 }, svg);
        el('text', { x: M_L - 8, y: y + 4, 'text-anchor': 'end',
          'font-size': 13, fill: 'var(--muted)' }, svg).textContent = v.toFixed(1);
      }
      el('text', { x: 14, y: M_T + PH / 2, 'text-anchor': 'middle',
        'font-size': 14, fill: 'var(--muted)',
        transform: `rotate(-90 14 ${M_T + PH / 2})` }, svg)
        .textContent = 'region-mean SST (°C)';

      // Month dividers + labels + baseline.
      MONTH_STARTS.forEach((m, i) => {
        el('line', { x1: xs(m), x2: xs(m), y1: M_T, y2: M_T + PH,
          stroke: 'var(--border)', 'stroke-width': 1, 'stroke-opacity': 0.4 }, svg);
        el('text', { x: xs(m + 15), y: M_T + PH + 20, 'text-anchor': 'middle',
          'font-size': 13, fill: 'var(--muted)' }, svg).textContent = MONTH_LABELS[i];
      });
      el('line', { x1: M_L, x2: M_L + PW, y1: M_T + PH, y2: M_T + PH,
        stroke: 'var(--border)', 'stroke-width': 1 }, svg);

      // (1) Faint gray spaghetti — ALL years, always on.
      const years = (d.years || []).map(String);
      for (const key of years) {
        this._linePath(d.series[key], 'var(--muted)', 0.7, null, 0.16);
      }

      // (2) 1991–2020 mean — dashed near-white (toggleable).
      if (this.selected.has(MEAN_KEY)) {
        this._linePath(d.clim, this._colorFor(MEAN_KEY), 2.2, '6 4', 0.95);
      }

      // (3) Selected colored years on top (current year last + end-dot).
      const cur = String(d.cur);
      const sel = years.filter(y => this.selected.has(y));
      // Draw non-current selected first (oldest→newest), current on top.
      sel.filter(y => y !== cur).sort((a, b) => a - b).forEach(y => {
        this._linePath(d.series[y], this._colorFor(y), 2.2, null, 1);
      });
      if (this.selected.has(cur) && d.series[cur]) {
        const col = this._colorFor(cur);
        this._linePath(d.series[cur], col, 3.0, null, 1);
        if (d.latest && d.latest.val != null) {
          el('circle', { cx: xs(d.latest.doy), cy: yToPx(d.latest.val),
            r: 4.5, fill: col, stroke: 'var(--bg)', 'stroke-width': 1 }, svg);
          el('line', { x1: xs(d.latest.doy), x2: xs(d.latest.doy),
            y1: M_T, y2: M_T + PH, stroke: col, 'stroke-width': 1,
            'stroke-dasharray': '2 3', 'stroke-opacity': 0.5 }, svg);
        }
      }

      // On-plot legend of the currently-plotted series (mean + toggled
      // years). Rebuilt here on every redraw, so it tracks toggles live.
      this._drawLegend();

      // Crosshair line (recreated on each redraw since clear() wiped it).
      this._cross = el('line', { x1: 0, x2: 0, y1: M_T, y2: M_T + PH,
        stroke: 'var(--accent-2)', 'stroke-width': 1.1,
        'stroke-dasharray': '4 4', opacity: 0, 'pointer-events': 'none' }, svg);
    }

    // On-plot legend: one row per currently-plotted, identifiable series
    // (the 1991–2020 mean + each toggled-on year, current-year flagged),
    // each with its exact line colour/dash. Sits in the top-left corner —
    // clear of the curves (NH winter SST is low-left; the latest-value
    // end-dot is mid-year) and clear of the anomaly inset (a separate
    // element outside the SVG). A translucent panel keeps it readable over
    // the faint gray spaghetti. Capped so "All" can't paper over the chart.
    _drawLegend() {
      const d = this.data;
      if (!d) return;
      const entries = [];
      if (this.selected.has(MEAN_KEY)) {
        entries.push({ label: d.climo_label || '1991–2020 mean',
          color: this._colorFor(MEAN_KEY), dash: '6 4', width: 2.2 });
      }
      const cur = String(d.cur);
      const years = (d.years || []).map(String)
        .filter(y => this.selected.has(y))
        .sort((a, b) => b - a);                       // newest first
      for (const y of years) {
        entries.push({ label: (y === cur) ? `${y} (current)` : y,
          color: this._colorFor(y), dash: null, width: (y === cur) ? 3.0 : 2.2 });
      }
      if (!entries.length) return;                    // nothing on → no legend

      const MAX = 14;                                 // keep it from covering the plot
      let extra = 0;
      if (entries.length > MAX) {
        extra = entries.length - (MAX - 1);
        entries.length = MAX - 1;
      }

      const fs = 13, rowH = 17, padX = 10, padY = 8, swW = 22, swGap = 8;
      const rows = entries.length + (extra ? 1 : 0);
      let maxChars = 0;
      for (const e of entries) maxChars = Math.max(maxChars, e.label.length);
      const moreLab = extra ? `+${extra} more year${extra === 1 ? '' : 's'}` : '';
      if (extra) maxChars = Math.max(maxChars, moreLab.length);
      const boxW = padX * 2 + swW + swGap + maxChars * (fs * 0.58);
      const boxH = padY * 2 + rows * rowH;
      const x0 = M_L + 12, y0 = M_T + 12;

      const g = el('g', { 'pointer-events': 'none' }, this.svg);
      el('rect', { x: x0, y: y0, width: boxW.toFixed(1), height: boxH.toFixed(1),
        rx: 8, fill: 'var(--panel)', 'fill-opacity': 0.82,
        stroke: 'var(--border)', 'stroke-width': 1 }, g);

      const sx0 = x0 + padX, sx1 = sx0 + swW, tx = sx1 + swGap;
      let ry = y0 + padY + rowH / 2;
      for (const e of entries) {
        const a = { x1: sx0, x2: sx1, y1: ry, y2: ry, stroke: e.color,
          'stroke-width': Math.min(e.width, 2.6), 'stroke-linecap': 'round' };
        if (e.dash) a['stroke-dasharray'] = e.dash;
        el('line', a, g);
        el('text', { x: tx, y: ry + 4, 'font-size': fs, fill: 'var(--fg)' }, g)
          .textContent = e.label;
        ry += rowH;
      }
      if (extra) {
        el('text', { x: sx0, y: ry + 4, 'font-size': fs, fill: 'var(--muted)',
          'font-style': 'italic' }, g).textContent = moreLab;
      }
    }

    // Polyline from a length-366 array (index i → doy i+1); breaks the path
    // at null (NCEI gap) days so the line never bridges a missing stretch.
    _linePath(arr, stroke, width, dash, opacity) {
      if (!arr || !arr.length) return;
      const yToPx = this._yToPx;
      let dstr = '', pen = false;
      for (let i = 0; i < arr.length; i++) {
        const v = arr[i];
        if (v == null || !isFinite(v)) { pen = false; continue; }
        const cmd = pen ? 'L' : 'M';
        dstr += cmd + xs(i + 1).toFixed(1) + ',' + yToPx(v).toFixed(1) + ' ';
        pen = true;
      }
      if (!dstr) return;
      const a = { d: dstr, fill: 'none', stroke, 'stroke-width': width,
        'stroke-linejoin': 'round', 'stroke-linecap': 'round' };
      if (dash) a['stroke-dasharray'] = dash;
      if (opacity != null) a['stroke-opacity'] = opacity;
      el('path', a, this.svg);
    }

    // ---- hover crosshair + tooltip ------------------------------------
    _scheduleHover(evt) {
      this._hoverEvt = evt;
      if (this._hoverFrame) return;
      this._hoverFrame = requestAnimationFrame(() => {
        this._hoverFrame = null;
        if (this._hoverEvt) this._drawHover(this._hoverEvt);
      });
    }

    _drawHover(evt) {
      const d = this.data;
      if (!d || !this._cross || !this.svg.getScreenCTM) { return; }
      const pt = this.svg.createSVGPoint();
      const src = evt.touches ? evt.touches[0] : evt;
      pt.x = src.clientX; pt.y = src.clientY;
      const ctm = this.svg.getScreenCTM();
      if (!ctm) return;
      const p = pt.matrixTransform(ctm.inverse());
      if (p.x < M_L || p.x > M_L + PW) { this._hideHover(); return; }
      const doy = xToDoy(p.x);
      const x = xs(doy);
      this._cross.setAttribute('x1', x);
      this._cross.setAttribute('x2', x);
      this._cross.setAttribute('opacity', 1);

      // Tooltip: the value of each selected year (+ mean) at this doy.
      const idx = doy - 1;
      const rows = [];
      if (this.selected.has(MEAN_KEY) && d.clim && d.clim[idx] != null) {
        rows.push(`<div><span class="swc-tip-sw" style="background:${this._colorFor(MEAN_KEY)}"></span>`
          + `${escapeHTML(d.climo_label || 'mean')}: <b>${d.clim[idx].toFixed(2)}°</b></div>`);
      }
      const years = (d.years || []).map(String)
        .filter(y => this.selected.has(y))
        .sort((a, b) => b - a);
      for (const y of years) {
        const v = d.series[y] && d.series[y][idx];
        if (v == null) continue;
        rows.push(`<div><span class="swc-tip-sw" style="background:${this._colorFor(y)}"></span>`
          + `${y}: <b>${v.toFixed(2)}°</b></div>`);
      }
      const dateLabel = doyToShort(doy);
      this.tipEl.innerHTML =
        `<div class="swc-tip-head">${dateLabel} <span class="swc-tip-doy">DOY ${doy}</span></div>`
        + (rows.length ? rows.join('') : '<div class="swc-tip-doy">no data</div>');

      // Position relative to the chart wrapper, scaled viewBox → px.
      const wrapRect = this.chartWrap.getBoundingClientRect();
      const svgRect = this.svg.getBoundingClientRect();
      const scale = svgRect.width / W;
      let tipX = (x * scale) + (svgRect.left - wrapRect.left);
      const tipY = (svgRect.top - wrapRect.top) + 6;
      // Keep the tooltip inside the wrapper horizontally.
      const tw = this.tipEl.offsetWidth || 120;
      tipX = Math.max(2, Math.min(tipX + 10, wrapRect.width - tw - 2));
      this.tipEl.style.left = tipX + 'px';
      this.tipEl.style.top = tipY + 'px';
      this.tipEl.style.opacity = 1;
    }

    _hideHover() {
      if (this._cross) this._cross.setAttribute('opacity', 0);
      if (this.tipEl) this.tipEl.style.opacity = 0;
    }

    // ---- chrome --------------------------------------------------------
    _showLoading() {
      clear(this.svg);
      el('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle',
        'font-size': 15, fill: 'var(--muted)' }, this.svg).textContent = 'Loading…';
      this.yearsEl.innerHTML = '';
    }

    _renderError(msg) {
      clear(this.svg);
      el('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle',
        'font-size': 14, fill: 'var(--muted)' }, this.svg).textContent = msg;
      // Fall back to the baked combined PNG if the manifest carries one.
      if (this.m.path_template && this.insetImg) {
        const png = this.m.path_template.replace('{region}', this.regionSlug);
        this.insetCap.textContent = 'interactive data unavailable — showing the baked chart';
        this.insetImg.src = png + '?t=' + Date.now();
        this.insetImg.style.display = '';
      }
    }

    _loadMeta() {
      if (!this.m.meta_url) { this.metaEl.textContent = ''; return; }
      fetch(this.m.meta_url + '?t=' + Date.now(), { cache: 'no-store' })
        .then(r => r.ok ? r.json() : null)
        .then(meta => {
          if (!meta || !meta.date) { this.metaEl.textContent = ''; return; }
          const dd = new Date(meta.date + 'T00:00:00Z');
          const txt = dd.toLocaleDateString('en-US', {
            month: 'long', day: 'numeric', year: 'numeric', timeZone: 'UTC',
          });
          const updated = meta.updated_utc ? `  ·  updated ${meta.updated_utc}` : '';
          this.metaEl.textContent = `Valid ${txt}${updated}`;
        })
        .catch(() => { this.metaEl.textContent = ''; });
    }
  }

  // doy (1..366, fixed leap reference) → "Mon D" label.
  function doyToShort(doy) {
    const d = new Date(Date.UTC(2000, 0, 1));
    d.setUTCDate(doy);
    return d.toLocaleDateString('en-US',
      { month: 'short', day: 'numeric', timeZone: 'UTC' });
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
