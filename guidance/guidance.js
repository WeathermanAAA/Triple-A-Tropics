/*
 * guidance.js - the model GUIDANCE viewer, as a reusable component.
 *
 * ONE IMPL, MOUNTED WHEREVER IT IS NEEDED (CYCLOLAB_DESIGN 7.3), exactly like
 * hafs.js and recon.js: this file lives in the main repo, is served from the
 * house origin, and the CycloLab per-storm shell lazy-loads it on first tab
 * open and constructs it locked to one storm. There is deliberately no second
 * implementation in the render-box repo - the shell used to carry an inline
 * copy, and this replaces it.
 *
 * It hydrates from the per-storm document written by
 * `guidance/build_guidance.py` (R2 `cyclolab/{sid}/guidance_v2.json`), falling
 * back to the legacy `guidance.json` when the v2 key is not there yet.
 *
 * WHAT IT DRAWS, and why each piece exists
 *
 *   1. SPAGHETTI TRACKS - every track aid for the cycle, coloured by the
 *      intensity forecast along the track (shared SSHWS palette), with the
 *      OFFICIAL forecast and the verifying BEST TRACK drawn distinctly. An
 *      unlabelled bundle of tracks is decoration; the official line and the
 *      best track are what the reader is actually comparing against.
 *
 *   2. INTENSITY / MSLP with the OCD5 SKILL BASELINE. OCD5 is
 *      climatology-and-persistence - the no-skill reference. A guidance chart
 *      without it cannot answer the only question that matters when four aids
 *      disagree: is ANY of this beating the trivial forecast?
 *
 *   3. CONSENSUS MEMBERSHIP STRIP, in three states - present, absent, and
 *      WITHHELD FROM THE PUBLIC A-DECK. The public feed omits every
 *      ECMWF-derived aid, so TVCN and RVCN are plottable but NOT independently
 *      reproducible: they were computed upstream from members we cannot see.
 *      Rendering a withheld member as merely "absent" would imply it did not
 *      run. The third state is the entire point of the strip.
 *
 *   4. EARLY / LATE BADGING on every aid. An early aid was available in time
 *      for the cycle it is labelled with; a late one was not, so its apparent
 *      skill is partly hindsight. They are badged and never blended.
 *
 * BASIN SCOPING IS A HARD CONSTRAINT. AL/EP/CP get the full suite. WP/IO/SH
 * get raw ensemble tracks only, clearly labelled - those decks have never
 * carried official, consensus or statistical aids, so a consensus envelope
 * there would be fabricated, not merely degraded. The viewer never draws one:
 * it renders `capability.tier` as published and hides the panels the basin
 * cannot support.
 *
 * No CDN dependencies, no build step - inline SVG, house tokens, ES5.
 */
(function (w, d) {
  'use strict';

  var CDN = 'https://cdn.triple-a-tropics.com';

  // Shared SSHWS palette - the SAME hues as cyclolab_map.js and the track
  // pages, so an intensity reads as one colour everywhere on the site.
  var SSHS_COLORS = {
    TD: '#3fa4ff', TS: '#46c56a', C1: '#ffe14d',
    C2: '#ff9a2f', C3: '#f5333c', C4: '#e33ad4', C5: '#b03bff'
  };
  function ktToCat(kt) {
    kt = +kt || 0;
    return kt >= 137 ? 'C5' : kt >= 113 ? 'C4' : kt >= 96 ? 'C3'
      : kt >= 83 ? 'C2' : kt >= 64 ? 'C1' : kt >= 34 ? 'TS' : 'TD';
  }
  function ktColor(kt) { return SSHS_COLORS[ktToCat(kt)]; }

  // Distinct, non-SSHWS strokes for the two reference traces, so neither can
  // be mistaken for one of the aids.
  var OFCL_COLOR = '#ffffff';
  var BEST_COLOR = '#9fb3c8';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function el(tag, cls, html) {
    var n = d.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }

  // ---- longitude handling -------------------------------------------------
  // Track aids cross the antimeridian. Measuring span on raw values there is
  // the bug this site has already been bitten by twice: a crossing arrives as
  // a sign flip (-178.9 -> +179.8) whose raw midpoint is the ANTIPODE. So all
  // extent maths runs in a CONTINUOUS frame, and values are normalised only
  // for display.
  function contLon(lon, ref) {
    if (lon == null) return null;
    var v = +lon;
    while (v - ref > 180) v -= 360;
    while (ref - v > 180) v += 360;
    return v;
  }
  function dispLon(v) {
    var x = +v;
    while (x > 180) x -= 360;
    while (x < -180) x += 360;
    return x;
  }
  function fmtLon(v) {
    var x = dispLon(v);
    return Math.abs(x).toFixed(1) + (x < 0 ? '°W' : '°E');
  }

  // =========================================================================
  function GuidanceViewer(root, opts) {
    opts = opts || {};
    this.root = (typeof root === 'string') ? d.getElementById(root) : root;
    if (!this.root) throw new Error('GuidanceViewer: no root element');
    this.base = opts.base || (CDN + '/cyclolab');
    this.stormLock = opts.stormLock || opts.sid || null;
    this.stormName = opts.stormName || '';
    if (!this.stormLock) throw new Error('GuidanceViewer: stormLock is required');

    this.doc = null;
    this.tab = 'tracks';
    this._dead = false;

    this.root.classList.add('gv-root');
    this._injectCss();
    this._skeleton();
    this._load();
  }

  GuidanceViewer.prototype._injectCss = function () {
    if (d.getElementById('gv-css')) return;
    var s = el('style');
    s.id = 'gv-css';
    s.textContent = [
      '.gv-root{display:flex;flex-direction:column;gap:14px;min-width:0;}',
      '.gv-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 14px;}',
      '.gv-title{font-size:14px;font-weight:700;color:var(--fg,#e8eef6);}',
      '.gv-sub{font-size:11.5px;color:var(--muted,#8ea2bd);}',
      '.gv-tabs{display:flex;gap:0;flex-wrap:wrap;}',
      '.gv-tab{background:var(--bg,#0d1117);color:var(--muted,#8ea2bd);',
      ' border:1px solid var(--border,#243244);border-right:none;padding:6px 12px;',
      ' font-size:12px;font-weight:600;cursor:pointer;font-family:inherit;}',
      '.gv-tab:first-child{border-radius:6px 0 0 6px;}',
      '.gv-tab:last-child{border-radius:0 6px 6px 0;border-right:1px solid var(--border,#243244);}',
      '.gv-tab.active{background:var(--accent-2,#3fd0d4);color:#06222e;border-color:var(--accent-2,#3fd0d4);}',
      '.gv-panel{min-width:0;}',
      '.gv-panel[hidden]{display:none;}',
      '.gv-svg{width:100%;height:auto;display:block;background:#0a0d12;',
      ' border:1px solid var(--border,#243244);border-radius:8px;}',
      '.gv-legend{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:10.5px;',
      ' color:var(--muted,#8ea2bd);margin-top:8px;}',
      '.gv-legend span{display:inline-flex;align-items:center;gap:5px;}',
      '.gv-legend i{width:12px;height:3px;border-radius:2px;display:inline-block;}',
      '.gv-legend i.dot{width:9px;height:9px;border-radius:50%;}',
      // early/late badge
      '.gv-badge{display:inline-block;padding:0 4px;border-radius:3px;',
      ' font-size:9px;font-weight:700;letter-spacing:.05em;line-height:14px;',
      ' border:1px solid currentColor;opacity:.9;}',
      '.gv-badge.early{color:#46c56a;}',
      '.gv-badge.late{color:#ff9a2f;}',
      // aid table
      '.gv-aids{width:100%;border-collapse:collapse;font-size:11.5px;}',
      '.gv-aids th{text-align:left;font-weight:700;color:var(--muted,#8ea2bd);',
      ' text-transform:uppercase;letter-spacing:.05em;font-size:10px;',
      ' padding:6px 8px;border-bottom:1px solid var(--border,#243244);}',
      '.gv-aids td{padding:5px 8px;border-bottom:1px solid rgba(36,50,68,.5);',
      ' color:var(--fg,#e8eef6);vertical-align:middle;}',
      '.gv-aids tr:hover td{background:rgba(63,208,212,.05);}',
      '.gv-aids .k{color:var(--muted,#8ea2bd);font-size:10.5px;}',
      // membership strip
      '.gv-cons{display:flex;flex-direction:column;gap:10px;}',
      '.gv-consrow{border:1px solid var(--border,#243244);border-radius:8px;padding:10px 12px;}',
      '.gv-conshead{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px;margin-bottom:8px;}',
      '.gv-consname{font-weight:700;font-size:12.5px;color:var(--fg,#e8eef6);}',
      '.gv-repro{font-size:10px;font-weight:700;padding:1px 6px;border-radius:3px;',
      ' border:1px solid currentColor;}',
      '.gv-repro.no{color:#ff9a2f;}',
      '.gv-repro.yes{color:#46c56a;}',
      '.gv-mem{display:flex;flex-wrap:wrap;gap:5px;}',
      // DIRECT children only: the chip contains a nested <span> for the state
      // word, and an unscoped selector gave that its own border box too.
      '.gv-mem > span{display:inline-flex;align-items:center;gap:5px;font-size:11px;',
      ' padding:3px 8px;border-radius:4px;border:1px solid var(--border,#243244);',
      ' color:var(--muted,#8ea2bd);font-weight:700;}',
      '.gv-mem > span i{width:8px;height:8px;border-radius:50%;display:inline-block;',
      ' flex:none;}',
      // The STATE word is the secondary text; the aid id is the identifier.
      '.gv-mem > span > .k{font-weight:600;opacity:.75;font-size:10px;',
      ' text-transform:lowercase;}',
      '.gv-mem > span.present{color:#cfe3f7;border-color:rgba(70,197,106,.5);}',
      '.gv-mem > span.present i{background:#46c56a;}',
      '.gv-mem > span.absent i{background:#4a5a70;}',
      '.gv-mem > span.withheld{color:#ffc98a;border-color:rgba(255,154,47,.55);',
      ' background:rgba(255,154,47,.07);}',
      '.gv-mem > span.withheld i{background:#ff9a2f;}',
      '.gv-note{font-size:11.5px;line-height:1.55;color:var(--muted,#8ea2bd);',
      ' border-left:2px solid var(--accent-2,#3fd0d4);padding:2px 0 2px 10px;}',
      '.gv-warn{border-left-color:#ff9a2f;}',
      '.gv-status{padding:22px;text-align:center;color:var(--muted,#8ea2bd);font-size:12.5px;}'
    ].join('');
    d.head.appendChild(s);
  };

  GuidanceViewer.prototype._skeleton = function () {
    this.root.innerHTML = '';
    this.dom = {};
    this.dom.head = el('div', 'gv-head');
    this.dom.tabs = el('div', 'gv-tabs');
    this.dom.body = el('div', 'gv-panel');
    this.dom.status = el('div', 'gv-status', 'Loading model guidance…');
    this.root.appendChild(this.dom.head);
    this.root.appendChild(this.dom.tabs);
    this.root.appendChild(this.dom.status);
    this.root.appendChild(this.dom.body);
    this.dom.body.hidden = true;
  };

  GuidanceViewer.prototype._fail = function (msg) {
    this.dom.status.hidden = false;
    this.dom.status.textContent = msg;
    this.dom.body.hidden = true;
  };

  // Prefer the v2 document; fall back to the legacy one so the tab still shows
  // something on a storm the new builder has not reached yet.
  GuidanceViewer.prototype._load = function () {
    var self = this;
    var stem = this.base + '/' + encodeURIComponent(this.stormLock) + '/';
    var bust = '?t=' + Date.now();
    function get(url) {
      return fetch(url + bust, { cache: 'no-store' }).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      });
    }
    get(stem + 'guidance_v2.json')
      .catch(function () { return get(stem + 'guidance.json').then(upgrade); })
      .then(function (doc) {
        if (self._dead) return;
        self.doc = doc;
        self._render();
      })
      .catch(function () {
        if (!self._dead) self._fail('No model guidance for this storm yet.');
      });

    // A legacy document lacks every field added for this viewer. Rather than
    // branch on shape everywhere, normalise it ONCE into the v2 shape with the
    // missing pieces explicitly empty - so the panels that cannot be honest
    // simply do not render, instead of rendering something invented.
    function upgrade(o) {
      var present = o.present_aids || Object.keys(o.aids || {});
      return {
        schema: 1, sid: o.sid, basin: (o.basin || '').toLowerCase(),
        init_cycle: o.init_cycle, init_time: o.init_time,
        source: o.source || '', aids: o.aids || {}, aid_meta: {},
        present_aids: present,
        official: present.indexOf('OFCL') >= 0 ? 'OFCL' : null,
        skill_baseline: null,
        consensus_aids: [], ensemble_mean_aids: [], ensemble_members: [],
        early_aids: [], late_aids: [], best_track: [],
        consensus_membership: [], filtered_deck: null, capability: null,
        _legacy: true
      };
    }
  };

  // ---- tabs ---------------------------------------------------------------
  GuidanceViewer.prototype._render = function () {
    var self = this, doc = this.doc;
    this.dom.status.hidden = true;
    this.dom.body.hidden = false;

    var cap = doc.capability || {};
    var nAids = (doc.present_aids || []).length;
    this.dom.head.innerHTML =
      '<span class="gv-title">Model guidance</span>' +
      '<span class="gv-sub">' + esc(doc.init_cycle || 'no cycle') +
      ' · ' + nAids + ' aid' + (nAids === 1 ? '' : 's') +
      (cap.tier === 'ensemble_only' ? ' · raw ensembles only' : '') +
      '</span>';

    // Panels the basin cannot honestly support are not offered at all.
    var tabs = [{ id: 'tracks', label: 'Tracks' },
                { id: 'intensity', label: 'Intensity' }];
    if ((doc.consensus_membership || []).length) {
      tabs.push({ id: 'consensus', label: 'Consensus' });
    }
    tabs.push({ id: 'aids', label: 'Aids' });

    this.dom.tabs.innerHTML = '';
    tabs.forEach(function (t) {
      var b = el('button', 'gv-tab' + (t.id === self.tab ? ' active' : ''), esc(t.label));
      b.type = 'button';
      b.setAttribute('data-tab', t.id);
      b.addEventListener('click', function () { self._select(t.id); });
      self.dom.tabs.appendChild(b);
    });
    if (!tabs.some(function (t) { return t.id === self.tab; })) this.tab = 'tracks';
    this._paint();
  };

  GuidanceViewer.prototype._select = function (id) {
    this.tab = id;
    var btns = this.dom.tabs.querySelectorAll('.gv-tab');
    for (var i = 0; i < btns.length; i++) {
      btns[i].classList.toggle('active', btns[i].getAttribute('data-tab') === id);
    }
    this._paint();
  };

  GuidanceViewer.prototype._paint = function () {
    var b = this.dom.body;
    b.innerHTML = '';
    if (this.tab === 'tracks') b.appendChild(this._tracksPanel());
    else if (this.tab === 'intensity') b.appendChild(this._intensityPanel());
    else if (this.tab === 'consensus') b.appendChild(this._consensusPanel());
    else b.appendChild(this._aidsPanel());
  };

  // ---- shared helpers -----------------------------------------------------
  GuidanceViewer.prototype._meta = function (tech) {
    return (this.doc.aid_meta || {})[tech] || {};
  };
  GuidanceViewer.prototype._badge = function (tech) {
    var t = this._meta(tech).timing;
    if (t !== 'early' && t !== 'late') return '';
    return ' <span class="gv-badge ' + t + '">' + t.toUpperCase() + '</span>';
  };

  /* Track aids worth drawing: positioned, and not the two reference traces. */
  GuidanceViewer.prototype._trackAids = function () {
    var doc = this.doc, self = this, out = [];
    (doc.present_aids || []).forEach(function (t) {
      if (t === 'OFCL' || t === 'BEST') return;
      var pts = (doc.aids[t] || []).filter(function (p) {
        return p.lat != null && p.lon != null;
      });
      if (pts.length < 2) return;
      var m = self._meta(t);
      // A skill baseline is a reference, not guidance - it belongs on the
      // intensity chart as the bar to clear, not in the spaghetti bundle.
      if (m.kind === 'skill_baseline') return;
      out.push({ tech: t, pts: pts, meta: m });
    });
    return out;
  };

  // =========================================================================
  // 1. SPAGHETTI TRACKS
  // =========================================================================
  GuidanceViewer.prototype._tracksPanel = function () {
    var doc = this.doc, self = this;
    var wrap = el('div');
    var aids = this._trackAids();
    var ofcl = (doc.aids.OFCL || []).filter(function (p) {
      return p.lat != null && p.lon != null;
    });
    var best = (doc.best_track || []).filter(function (p) {
      return p.lat != null && p.lon != null;
    });

    if (!aids.length && !ofcl.length && !best.length) {
      wrap.appendChild(el('div', 'gv-status', 'No positioned track guidance for this cycle.'));
      return wrap;
    }

    // Continuous longitude frame, referenced to the first available fix.
    var ref = (best[0] || ofcl[0] || (aids[0] && aids[0].pts[0]) || {}).lon || 0;
    var xs = [], ys = [];
    function proj(p) {
      var x = contLon(p.lon, ref), y = +p.lat;
      xs.push(x); ys.push(y);
      return [x, y];
    }
    var series = aids.map(function (a) {
      return { tech: a.tech, meta: a.meta, xy: a.pts.map(proj), pts: a.pts };
    });
    var ofclXY = ofcl.map(proj), bestXY = best.map(proj);

    var W = 760, H = 460, PAD = { l: 44, r: 14, t: 14, b: 34 };
    var lo = Math.min.apply(null, xs), hi = Math.max.apply(null, xs);
    var la = Math.min.apply(null, ys), ha = Math.max.apply(null, ys);
    // Pad, and never let a degenerate span collapse the transform.
    var mx = Math.max((hi - lo) * 0.08, 0.75), my = Math.max((ha - la) * 0.08, 0.75);
    lo -= mx; hi += mx; la -= my; ha += my;
    var iw = W - PAD.l - PAD.r, ih = H - PAD.t - PAD.b;
    function X(v) { return PAD.l + (v - lo) / (hi - lo) * iw; }
    function Y(v) { return PAD.t + (ha - v) / (ha - la) * ih; }

    var svg = ['<svg class="gv-svg" viewBox="0 0 ' + W + ' ' + H +
               '" preserveAspectRatio="xMidYMid meet" role="img" ' +
               'aria-label="Model forecast track guidance">'];

    // graticule
    var gs = niceStep((hi - lo) / 6), lat0 = Math.ceil(la / gs) * gs;
    for (var gx = Math.ceil(lo / gs) * gs; gx <= hi; gx += gs) {
      svg.push('<line x1="' + X(gx).toFixed(1) + '" y1="' + PAD.t + '" x2="' +
        X(gx).toFixed(1) + '" y2="' + (H - PAD.b) + '" stroke="#1b2635" stroke-width="1"/>');
      svg.push('<text x="' + X(gx).toFixed(1) + '" y="' + (H - PAD.b + 14) +
        '" fill="#6d829e" font-size="9.5" text-anchor="middle">' + esc(fmtLon(gx)) + '</text>');
    }
    for (var gy = lat0; gy <= ha; gy += gs) {
      svg.push('<line x1="' + PAD.l + '" y1="' + Y(gy).toFixed(1) + '" x2="' + (W - PAD.r) +
        '" y2="' + Y(gy).toFixed(1) + '" stroke="#1b2635" stroke-width="1"/>');
      svg.push('<text x="' + (PAD.l - 5) + '" y="' + (Y(gy) + 3).toFixed(1) +
        '" fill="#6d829e" font-size="9.5" text-anchor="end">' +
        Math.abs(gy).toFixed(gs < 1 ? 1 : 0) + (gy < 0 ? '°S' : '°N') + '</text>');
    }

    // Aid tracks: a thin grey spine so the bundle reads as a bundle, then
    // per-leg SSHWS colour so intensity is visible along each track.
    series.forEach(function (s) {
      var dstr = s.xy.map(function (p, i) {
        return (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1);
      }).join(' ');
      var dash = s.meta.timing === 'late' ? ' stroke-dasharray="4 3"' : '';
      svg.push('<path d="' + dstr + '" fill="none" stroke="#43536b" ' +
        'stroke-width="2.6" stroke-linejoin="round" opacity="0.55"' + dash + '/>');
      for (var i = 1; i < s.xy.length; i++) {
        var v = s.pts[i].vmax;
        if (v == null) continue;
        svg.push('<line x1="' + X(s.xy[i - 1][0]).toFixed(1) + '" y1="' + Y(s.xy[i - 1][1]).toFixed(1) +
          '" x2="' + X(s.xy[i][0]).toFixed(1) + '" y2="' + Y(s.xy[i][1]).toFixed(1) +
          '" stroke="' + ktColor(v) + '" stroke-width="1.7" opacity="0.85"' + dash + '/>');
      }
    });

    // Best track (verifying history) - solid, muted, with fix dots.
    if (bestXY.length > 1) {
      svg.push('<path d="' + bestXY.map(function (p, i) {
        return (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1);
      }).join(' ') + '" fill="none" stroke="' + BEST_COLOR +
        '" stroke-width="2.2" stroke-linejoin="round" opacity="0.95"/>');
      bestXY.forEach(function (p, i) {
        svg.push('<circle cx="' + X(p[0]).toFixed(1) + '" cy="' + Y(p[1]).toFixed(1) +
          '" r="2.4" fill="' + ktColor(best[i].vmax) + '" stroke="#0a0d12" stroke-width="0.7"/>');
      });
    }

    // Official forecast - drawn LAST and heaviest: it is the reference the
    // whole bundle is being compared against.
    if (ofclXY.length > 1) {
      svg.push('<path d="' + ofclXY.map(function (p, i) {
        return (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1);
      }).join(' ') + '" fill="none" stroke="' + OFCL_COLOR +
        '" stroke-width="3" stroke-linejoin="round"/>');
      ofclXY.forEach(function (p, i) {
        svg.push('<circle cx="' + X(p[0]).toFixed(1) + '" cy="' + Y(p[1]).toFixed(1) +
          '" r="3.1" fill="' + OFCL_COLOR + '" stroke="#0a0d12" stroke-width="0.8"/>');
        if (ofcl[i].tau % 24 === 0) {
          svg.push('<text x="' + (X(p[0]) + 6).toFixed(1) + '" y="' + (Y(p[1]) - 5).toFixed(1) +
            '" fill="#cfe3f7" font-size="9" font-weight="700">' + ofcl[i].tau + 'h</text>');
        }
      });
    }
    svg.push('</svg>');
    wrap.innerHTML = svg.join('');

    // The legend lists only what was actually DRAWN - naming an official
    // forecast that is not on the plot would be its own small dishonesty.
    var leg = [];
    if (ofclXY.length > 1) {
      leg.push('<span><i style="background:' + OFCL_COLOR +
        '"></i>Official forecast (OFCL)</span>');
    }
    if (bestXY.length > 1) {
      leg.push('<span><i style="background:' + BEST_COLOR +
        '"></i>Best track (verifying)</span>');
    }
    leg.push('<span><i style="background:#43536b"></i>' + series.length + ' track aid' +
      (series.length === 1 ? '' : 's') + '</span>');
    if (series.some(function (s) { return s.meta.timing === 'late'; })) {
      leg.push('<span><i style="background:#43536b;border-top:1px dashed #8ea2bd"></i>' +
        'dashed = LATE aid</span>');
    }
    ['TD', 'TS', 'C1', 'C2', 'C3', 'C4', 'C5'].forEach(function (c) {
      leg.push('<span><i class="dot" style="background:' + SSHS_COLORS[c] + '"></i>' + c + '</span>');
    });
    wrap.appendChild(el('div', 'gv-legend', leg.join('')));

    if (!ofclXY.length) {
      wrap.appendChild(el('div', 'gv-note gv-warn',
        this._noOfficialNote()));
    }
    wrap.appendChild(this._sourceNote());
    return wrap;
  };

  GuidanceViewer.prototype._noOfficialNote = function () {
    var cap = this.doc.capability || {};
    if (cap.tier === 'ensemble_only') {
      return 'No official forecast track is drawn: ' + esc(cap.note || '');
    }
    return 'No official (OFCL) forecast is present in this cycle’s deck, ' +
           'so the aids are shown without it.';
  };

  // =========================================================================
  // 2. INTENSITY, WITH THE OCD5 SKILL BASELINE
  // =========================================================================
  GuidanceViewer.prototype._intensityPanel = function () {
    var doc = this.doc, self = this;
    var wrap = el('div');
    var baseline = doc.skill_baseline;

    var series = [];
    (doc.present_aids || []).forEach(function (t) {
      if (t === baseline) return;
      var pts = (doc.aids[t] || []).filter(function (p) { return p.vmax != null; });
      if (pts.length < 2) return;
      var m = self._meta(t);
      if (m.kind === 'skill_baseline') return;
      series.push({ tech: t, pts: pts, meta: m });
    });
    var basePts = baseline
      ? (doc.aids[baseline] || []).filter(function (p) { return p.vmax != null; })
      : [];

    if (!series.length) {
      wrap.appendChild(el('div', 'gv-status', 'No intensity guidance for this cycle.'));
      return wrap;
    }

    var W = 760, H = 380, PAD = { l: 44, r: 14, t: 14, b: 34 };
    var taus = [], vs = [];
    series.concat(basePts.length ? [{ pts: basePts }] : []).forEach(function (s) {
      s.pts.forEach(function (p) { taus.push(p.tau); vs.push(p.vmax); });
    });
    var t0 = 0, t1 = Math.max.apply(null, taus) || 120;
    var v0 = 0, v1 = Math.max(Math.max.apply(null, vs) + 15, 60);
    var iw = W - PAD.l - PAD.r, ih = H - PAD.t - PAD.b;
    function X(v) { return PAD.l + (v - t0) / (t1 - t0) * iw; }
    function Y(v) { return PAD.t + (v1 - v) / (v1 - v0) * ih; }

    var svg = ['<svg class="gv-svg" viewBox="0 0 ' + W + ' ' + H +
               '" preserveAspectRatio="xMidYMid meet" role="img" ' +
               'aria-label="Model forecast intensity guidance">'];

    // SSHWS threshold bands - the category boundaries the eye actually reads.
    [[34, 'TS'], [64, 'C1'], [83, 'C2'], [96, 'C3'], [113, 'C4'], [137, 'C5']].forEach(function (b) {
      if (b[0] > v1) return;
      svg.push('<line x1="' + PAD.l + '" y1="' + Y(b[0]).toFixed(1) + '" x2="' + (W - PAD.r) +
        '" y2="' + Y(b[0]).toFixed(1) + '" stroke="' + SSHS_COLORS[b[1]] +
        '" stroke-width="1" opacity="0.28" stroke-dasharray="3 4"/>');
      svg.push('<text x="' + (W - PAD.r - 3) + '" y="' + (Y(b[0]) - 3).toFixed(1) +
        '" fill="' + SSHS_COLORS[b[1]] + '" font-size="9" text-anchor="end" opacity="0.8">' +
        b[1] + '</text>');
    });
    for (var tk = 0; tk <= t1; tk += 24) {
      svg.push('<line x1="' + X(tk).toFixed(1) + '" y1="' + PAD.t + '" x2="' + X(tk).toFixed(1) +
        '" y2="' + (H - PAD.b) + '" stroke="#1b2635" stroke-width="1"/>');
      svg.push('<text x="' + X(tk).toFixed(1) + '" y="' + (H - PAD.b + 14) +
        '" fill="#6d829e" font-size="9.5" text-anchor="middle">' + tk + 'h</text>');
    }
    for (var vk = 0; vk <= v1; vk += 20) {
      svg.push('<text x="' + (PAD.l - 5) + '" y="' + (Y(vk) + 3).toFixed(1) +
        '" fill="#6d829e" font-size="9.5" text-anchor="end">' + vk + '</text>');
    }
    // Units on the axis: a bare "120" is not a wind speed.
    svg.push('<text x="' + (PAD.l - 5) + '" y="' + (PAD.t - 3) +
      '" fill="#8ea2bd" font-size="9.5" text-anchor="end" font-weight="700">kt</text>');

    // Each aid: a muted spine so the bundle reads as a bundle, then per-leg
    // SSHWS colour so the forecast CATEGORY is visible along the trace.
    series.forEach(function (s) {
      var dash = s.meta.timing === 'late' ? ' stroke-dasharray="4 3"' : '';
      svg.push('<path d="' + s.pts.map(function (p, i) {
        return (i ? 'L' : 'M') + X(p.tau).toFixed(1) + ' ' + Y(p.vmax).toFixed(1);
      }).join(' ') + '" fill="none" stroke="#43536b" stroke-width="2.4" ' +
        'stroke-linejoin="round" opacity="0.5"' + dash + '/>');
      for (var i = 1; i < s.pts.length; i++) {
        svg.push('<line x1="' + X(s.pts[i - 1].tau).toFixed(1) + '" y1="' + Y(s.pts[i - 1].vmax).toFixed(1) +
          '" x2="' + X(s.pts[i].tau).toFixed(1) + '" y2="' + Y(s.pts[i].vmax).toFixed(1) +
          '" stroke="' + ktColor(s.pts[i].vmax) + '" stroke-width="1.8" opacity="0.9"' + dash + '/>');
      }
    });

    // THE SKILL BASELINE, drawn LAST and unmistakable (heavy white dashes).
    // Without it the chart cannot say whether any aid is adding value over
    // climatology-and-persistence, which is the only question worth asking
    // when the aids disagree.
    if (basePts.length > 1) {
      svg.push('<path d="' + basePts.map(function (q, i) {
        return (i ? 'L' : 'M') + X(q.tau).toFixed(1) + ' ' + Y(q.vmax).toFixed(1);
      }).join(' ') + '" fill="none" stroke="#ffffff" stroke-width="2.4" ' +
        'stroke-dasharray="7 4" stroke-linejoin="round"/>');
    }
    svg.push('</svg>');
    wrap.innerHTML = svg.join('');

    var leg = [];
    if (basePts.length) {
      leg.push('<span><i style="background:#ffffff"></i>' + esc(baseline) +
        ' — no-skill baseline (climatology + persistence)</span>');
    }
    leg.push('<span><i style="background:#5b7a9e"></i>' + series.length + ' intensity aid' +
      (series.length === 1 ? '' : 's') + ', coloured by forecast category</span>');
    leg.push('<span><i style="background:#43536b;border-top:1px dashed #8ea2bd"></i>dashed = LATE aid</span>');
    wrap.appendChild(el('div', 'gv-legend', leg.join('')));

    if (!basePts.length) {
      var cap = doc.capability || {};
      wrap.appendChild(el('div', 'gv-note gv-warn',
        cap.tier === 'ensemble_only'
          ? 'No skill baseline is available in this basin, so these aids cannot be ' +
            'judged against climatology and persistence here. ' + esc(cap.note || '')
          : 'No OCD5/CLP5/SHF5 baseline in this cycle’s deck — without one, ' +
            'there is no reference for whether these aids are beating a trivial forecast.'));
    }
    wrap.appendChild(this._sourceNote());
    return wrap;
  };

  // =========================================================================
  // 3. CONSENSUS MEMBERSHIP STRIP - three states
  // =========================================================================
  GuidanceViewer.prototype._consensusPanel = function () {
    var doc = this.doc;
    var wrap = el('div', 'gv-cons');
    var rows = doc.consensus_membership || [];

    var anyWithheld = rows.some(function (r) { return r.n_withheld > 0; });
    if (anyWithheld) {
      wrap.appendChild(el('div', 'gv-note gv-warn',
        '<strong>These consensus aids are plottable but not independently ' +
        'reproducible.</strong> NHC’s public a-deck withholds every ' +
        'ECMWF-derived aid, so some members below are absent from the feed we ' +
        'read. We can show the consensus value the way NHC computed it; we ' +
        'cannot recompute or verify it from the members available to us.'));
    }

    rows.forEach(function (r) {
      var row = el('div', 'gv-consrow');
      row.appendChild(el('div', 'gv-conshead',
        '<span class="gv-consname">' + esc(r.tech) + '</span>' +
        '<span class="gv-sub">' + esc(r.label || '') + '</span>' +
        '<span class="gv-repro ' + (r.reproducible ? 'yes' : 'no') + '">' +
        (r.reproducible ? 'REPRODUCIBLE' : 'NOT REPRODUCIBLE') + '</span>' +
        '<span class="gv-sub">' + r.n_present + ' present · ' +
        r.n_withheld + ' withheld · ' + r.n_absent + ' absent</span>'));
      var mem = el('div', 'gv-mem');
      (r.members || []).forEach(function (m) {
        var title = m.state === 'withheld'
          ? m.tech + ' is produced but WITHHELD from the public a-deck'
          : m.state === 'present'
            ? m.tech + ' is present in this cycle’s deck'
            : m.tech + ' is not in this cycle’s deck';
        mem.appendChild(el('span', m.state,
          '<i></i>' + esc(m.tech) + '<span class="k">' +
          (m.state === 'withheld' ? 'withheld' : m.state) + '</span>'));
        mem.lastChild.title = title;
      });
      row.appendChild(mem);
      wrap.appendChild(row);
    });

    wrap.appendChild(this._sourceNote());
    return wrap;
  };

  // =========================================================================
  // 4. AID TABLE - every aid, badged early/late
  // =========================================================================
  GuidanceViewer.prototype._aidsPanel = function () {
    var doc = this.doc, self = this;
    var wrap = el('div');
    var KIND_LABEL = {
      official: 'Official', best: 'Best track', consensus: 'Consensus',
      ensemble_mean: 'Ensemble mean', ensemble_member: 'Ensemble member',
      dynamical: 'Dynamical', statistical: 'Statistical',
      skill_baseline: 'Skill baseline', other: '—'
    };
    var rows = (doc.present_aids || []).slice().sort(function (a, b) {
      var ka = self._meta(a).kind || 'zz', kb = self._meta(b).kind || 'zz';
      return ka === kb ? a.localeCompare(b) : ka.localeCompare(kb);
    });
    var html = ['<table class="gv-aids"><thead><tr><th>Aid</th><th>Kind</th>',
                '<th>Timing</th><th>Track</th><th>Intensity</th><th>Max τ</th>',
                '</tr></thead><tbody>'];
    rows.forEach(function (t) {
      var m = self._meta(t);
      html.push('<tr><td><strong>' + esc(t) + '</strong><div class="k">' +
        esc(m.label || '') + '</div></td>' +
        '<td>' + esc(KIND_LABEL[m.kind] || '—') + '</td>' +
        '<td>' + (m.timing === 'early' || m.timing === 'late'
          ? '<span class="gv-badge ' + m.timing + '">' + m.timing.toUpperCase() + '</span>'
          : '<span class="k">n/a</span>') + '</td>' +
        '<td>' + (m.has_track ? '✓' : '<span class="k">—</span>') + '</td>' +
        '<td>' + (m.has_intensity ? '✓' : '<span class="k">—</span>') + '</td>' +
        '<td>' + (m.tau_max == null ? '<span class="k">—</span>' : m.tau_max + 'h') + '</td></tr>');
    });
    html.push('</tbody></table>');
    wrap.innerHTML = html.join('');

    wrap.appendChild(el('div', 'gv-note',
      '<strong>Early vs late.</strong> An EARLY aid is available in time for the ' +
      'forecast cycle it is labelled with. A LATE aid is raw model output that ' +
      'lands after the deadline, so comparing it to the official forecast ' +
      'flatters it — part of its apparent skill is hindsight. They are ' +
      'badged here and never blended.'));

    var fd = doc.filtered_deck;
    if (fd && (fd.withheld || []).length) {
      wrap.appendChild(el('div', 'gv-note gv-warn',
        '<strong>The public a-deck is filtered.</strong> ' + esc(fd.note || '')));
    }
    wrap.appendChild(this._sourceNote());
    return wrap;
  };

  // ---- shared footer ------------------------------------------------------
  /* The footer is provenance ONLY: cycle + source. The capability explanation
   * belongs to whichever panel is missing something because of it, and is
   * rendered there - repeating it here printed the same paragraph twice on
   * every JTWC panel. */
  GuidanceViewer.prototype._sourceNote = function () {
    var doc = this.doc, cap = doc.capability || {};
    var bits = [];
    if (doc.init_cycle) bits.push('Cycle ' + esc(doc.init_cycle) + 'Z');
    if (cap.source || doc.source) bits.push('Source: ' + esc(cap.source || doc.source));
    return el('div', 'gv-note', bits.join(' · '));
  };

  // ---- lifecycle (the shell pauses a hidden tab) --------------------------
  GuidanceViewer.prototype._pause = function () { /* no timers to stop */ };
  GuidanceViewer.prototype._resume = function () { /* re-render is not needed */ };
  GuidanceViewer.prototype.destroy = function () {
    this._dead = true;
    if (this.root) this.root.innerHTML = '';
  };

  function niceStep(raw) {
    var pow = Math.pow(10, Math.floor(Math.log(Math.max(raw, 1e-6)) / Math.LN10));
    var n = raw / pow;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * pow;
  }

  if (typeof w !== 'undefined') {
    w.GuidanceViewer = GuidanceViewer;
    w.GuidanceViewerInternals = { ktToCat: ktToCat, contLon: contLon,
                                  dispLon: dispLon, niceStep: niceStep };
  }
})(typeof window !== 'undefined' ? window : this, typeof document !== 'undefined' ? document : null);
