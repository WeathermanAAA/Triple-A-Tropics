/* Google ensemble diagnostics (/models/): one storm, one cycle, every Google DeepMind Weather Lab suite.
   Data: models/gdmdiag/index.json + models/gdmdiag/<cycle>/<ATCF>.json on the CDN, built by gdmdiag/build.py
   (all statistics are precomputed there; this file only draws). Maps are a small Mercator canvas over Natural Earth;
   charts are inline SVG. Every basin is treated the same. */
(function () {
  'use strict';
  var CDN = 'https://cdn.triple-a-tropics.com/models/gdmdiag/';
  var root = document.getElementById('gdm-diag'); if (!root) return;
  var $ = function (s) { return root.querySelector(s); };
  var esc = function (t) { return String(t == null ? '' : t).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); };
  var SUITES = [['fnv3', 'FNV3 (50)'], ['wnv3', 'WN3 (64)'], ['genc', 'GenCast (50)'], ['fnv3x', 'FNV3 1000'], ['gdm', 'Google super ensemble']];
  var SC = { fnv3: '#5dd3ff', wnv3: '#c084fc', genc: '#7ee08a', fnv3x: '#ff6b9a', gdm: '#ffb83a' };
  var SN = { fnv3: 'FNV3', wnv3: 'WN3', genc: 'GenCast', fnv3x: 'FNV3 1000', gdm: 'Super' };
  var CAT = [['TD', 0, '#3fa4ff'], ['TS', 34, '#46c56a'], ['C1', 64, '#ffe14d'], ['C2', 83, '#ff9a2f'], ['C3', 96, '#f5333c'], ['C4', 113, '#e33ad4'], ['C5', 137, '#b03bff']];
  var catC = function (v) { var c = CAT[0][2]; CAT.forEach(function (x) { if (v >= x[1]) c = x[2]; }); return c; };
  var MEAN = '#ffb83a';
  var TABS = [['over', 'Overview'], ['track', 'Track'], ['int', 'Intensity'], ['chg', 'Intensity change'], ['str', 'Structure'], ['wind', 'Wind risk'], ['land', 'Landfall'], ['cmp', 'Compare'], ['trend', 'Trends']];
  var S = { idx: null, cyc: null, sid: null, suite: 'gdm', tab: 'over', doc: null, lead: 48, metric: 'v', wk: '34', wh: '120', dvk: '24', dvh: '48', land: null };
  var DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'], MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var cycMs = function (c) { return Date.UTC(+c.slice(0, 4), +c.slice(4, 6) - 1, +c.slice(6, 8), +c.slice(8, 10)); };
  var vt = function (h) { var d = new Date(cycMs(S.cyc) + h * 36e5); return DOW[d.getUTCDay()] + ' ' + MON[d.getUTCMonth()] + ' ' + d.getUTCDate() + ', ' + String(d.getUTCHours()).padStart(2, '0') + 'Z'; };
  var initTxt = function () { var d = new Date(cycMs(S.cyc)); return 'init ' + MON[d.getUTCMonth()] + ' ' + d.getUTCDate() + ' ' + String(d.getUTCHours()).padStart(2, '0') + 'Z'; };
  var pct = function (p) { return p == null ? '–' : (p < .005 && p > 0 ? '<1' : Math.round(p * 100)) + '%'; };
  var D = function () { return S.doc && S.doc.suites[S.suite]; };
  /* members to draw: a suite's own, or for the pooled super ensemble every pooled model's members (tagged by model) */
  var membersOf = function (d) { if (d.members) return d.members; if (!d.pooled) return null;
    var out = []; d.pooled.forEach(function (k) { var x = S.doc.suites[k]; if (x && x.members) out = out.concat(x.members); }); return out.length ? out : null; };
  var LI = function (h) { return Math.round(h / 6); };

  /* ---------------- data ---------------- */
  function loadIndex() {
    return fetch(CDN + 'index.json?t=' + Math.floor(Date.now() / 3e5)).then(function (r) { if (!r.ok) throw r.status; return r.json(); });
  }
  function storms() {
    var c = S.idx.cycles[S.cyc] || { storms: {} };
    return Object.keys(c.storms).map(function (id) { return { id: id, name: c.storms[id].name, basin: c.storms[id].basin }; })
      .sort(function (a, b) { return a.basin === b.basin ? a.id.localeCompare(b.id) : a.basin.localeCompare(b.basin); });
  }
  function loadStorm() {
    S.doc = null; status('Loading…');
    return fetch(CDN + S.cyc + '/' + S.sid + '.json?v=' + encodeURIComponent(S.idx.cycles[S.cyc].built || ''))
      .then(function (r) { if (!r.ok) throw r.status; return r.json(); })
      .then(function (d) { S.doc = d; if (!d.suites[S.suite]) S.suite = d.suites.gdm ? 'gdm' : Object.keys(d.suites)[0]; status(''); render(); })
      .catch(function () { status('This storm is not available for that run.'); });
  }
  function landGeo() {
    if (!S.land) S.land = fetch('/ne_50m_admin_0_countries.geojson').then(function (r) { return r.json(); }).catch(function () { return null; });
    return S.land;
  }
  function status(t) { var e = $('.gd-status'); e.textContent = t; e.style.display = t ? '' : 'none'; }

  /* ---------------- controls ---------------- */
  function seg(el, items, cur, on, dis) {
    el.innerHTML = items.map(function (x) { return '<button type="button" class="hafs-seg' + (x[0] === cur ? ' active' : '') + '" data-k="' + x[0] + '"' + (dis && dis(x[0]) ? ' disabled' : '') + '>' + esc(x[1]) + '</button>'; }).join('');
    Array.prototype.forEach.call(el.querySelectorAll('button'), function (b) { b.onclick = function () { on(b.getAttribute('data-k')); }; });
  }
  function controls() {
    var cy = Object.keys(S.idx.cycles).sort().reverse();
    $('#gd-run').innerHTML = cy.map(function (c, i) { return '<option value="' + c + '"' + (c === S.cyc ? ' selected' : '') + '>' + c.slice(4, 6) + '/' + c.slice(6, 8) + ' ' + c.slice(8, 10) + 'Z' + (i ? '' : ' (latest)') + '</option>'; }).join('');
    var st = storms();
    $('#gd-storm').innerHTML = st.map(function (s) { return '<option value="' + s.id + '"' + (s.id === S.sid ? ' selected' : '') + '>' + esc((s.name ? s.name + ' · ' : '') + s.id) + '</option>'; }).join('');
    seg($('#gd-suites'), SUITES, S.suite, function (k) { S.suite = k; controls(); render(); }, function (k) { return !S.doc || !S.doc.suites[k]; });
    seg($('#gd-tabs'), TABS, S.tab, function (k) { S.tab = k; controls(); render(); });
  }
  function pickCycle(c) {
    S.cyc = c; var st = storms();
    if (!st.some(function (s) { return s.id === S.sid; })) S.sid = st.length ? st[0].id : null;
    controls(); if (S.sid) loadStorm(); else { $('.gd-body').innerHTML = ''; status('No storms in this run.'); }
  }

  /* ---------------- SVG chart kit ---------------- */
  function Chart(o) {
    var W = o.w || 520, H = o.h || 240, P = { l: 44, r: 14, t: 28, b: 30 };
    var x0 = o.x[0], x1 = o.x[1], y0 = o.y[0], y1 = o.y[1];
    var X = function (v) { return P.l + (v - x0) / (x1 - x0) * (W - P.l - P.r); }, Y = function (v) { return H - P.b - (v - y0) / (y1 - y0) * (H - P.t - P.b); };
    var s = '<svg viewBox="0 0 ' + W + ' ' + H + '" class="gd-svg" role="img" aria-label="' + esc(o.title) + '"><text class="gd-t" x="' + P.l + '" y="16">' + esc(o.title) + '</text>';
    if (o.sub) s += '<text class="gd-ts" x="' + (W - P.r) + '" y="16" text-anchor="end">' + esc(o.sub) + '</text>';
    (o.yt || []).forEach(function (v) { s += '<line class="gd-g" x1="' + P.l + '" x2="' + (W - P.r) + '" y1="' + Y(v) + '" y2="' + Y(v) + '"/><text class="gd-a" x="' + (P.l - 5) + '" y="' + (Y(v) + 3) + '" text-anchor="end">' + (o.yf ? o.yf(v) : v) + '</text>'; });
    (o.xt || []).forEach(function (v) { s += '<line class="gd-g" x1="' + X(v) + '" x2="' + X(v) + '" y1="' + P.t + '" y2="' + (H - P.b) + '"/><text class="gd-a" x="' + X(v) + '" y="' + (H - P.b + 14) + '" text-anchor="middle">' + (o.xf ? o.xf(v) : v) + '</text>'; });
    if (o.xl) s += '<text class="gd-a" x="' + (W - P.r) + '" y="' + (H - 3) + '" text-anchor="end">' + esc(o.xl) + '</text>';
    if (o.yl) s += '<text class="gd-a" x="4" y="' + (P.t - 6) + '">' + esc(o.yl) + '</text>';
    var api = {
      X: X, Y: Y,
      line: function (pts, c, w, dash) { var d = seg2(pts); if (d) s += '<path d="' + d + '" fill="none" stroke="' + c + '" stroke-width="' + (w || 2) + '"' + (dash ? ' stroke-dasharray="' + dash + '"' : '') + ' stroke-linejoin="round"/>'; },
      band: function (lo, hi, c, op) { var a = [], b = []; for (var i = 0; i < lo.length; i++) if (lo[i][1] != null && hi[i][1] != null) { a.push([lo[i][0], lo[i][1]]); b.unshift([hi[i][0], hi[i][1]]); }
        if (a.length > 1) s += '<path d="' + seg2(a) + 'L' + seg2(b).slice(1) + 'Z" fill="' + c + '" opacity="' + (op || .25) + '"/>'; },
      bar: function (xa, xb, ya, yb, c, op) { s += '<rect x="' + X(xa) + '" y="' + Y(yb) + '" width="' + Math.max(0, X(xb) - X(xa)) + '" height="' + Math.max(0, Y(ya) - Y(yb)) + '" fill="' + c + '"' + (op ? ' opacity="' + op + '"' : '') + '/>'; },
      hl: function (v, c, lab) { s += '<line x1="' + P.l + '" x2="' + (W - P.r) + '" y1="' + Y(v) + '" y2="' + Y(v) + '" stroke="' + c + '" stroke-opacity=".5" stroke-dasharray="3 3"/>' + (lab ? '<text class="gd-a" x="' + (W - P.r - 2) + '" y="' + (Y(v) - 3) + '" text-anchor="end" fill="' + c + '">' + lab + '</text>' : ''); },
      text: function (x, y, t, c, anc) { s += '<text class="gd-a" x="' + X(x) + '" y="' + Y(y) + '" fill="' + (c || '#cfd6e0') + '" text-anchor="' + (anc || 'start') + '">' + esc(t) + '</text>'; },
      raw: function (t) { s += t; },
      legend: function (items) { var x = P.l; s += '<g class="gd-leg">'; items.forEach(function (it) { s += '<rect x="' + x + '" y="' + (H - 12) + '" width="10" height="4" fill="' + it[1] + '"/><text class="gd-a" x="' + (x + 13) + '" y="' + (H - 8) + '">' + esc(it[0]) + '</text>'; x += 18 + it[0].length * 6.2; }); s += '</g>'; },
      done: function () { return s + '</svg>'; }
    };
    function seg2(pts) { var d = '', pen = false; pts.forEach(function (p) { if (p[1] == null || !isFinite(p[1])) { pen = false; return; } d += (pen ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1); pen = true; }); return d; }
    return api;
  }
  var HT = [0, 24, 48, 72, 96, 120, 144, 168, 192, 216, 240];
  function fan(d, key, title, unit, opt) {
    opt = opt || {};
    var q = d[key], L = d.leads, all = [];
    q.forEach(function (r) { if (r) all.push(r[0], r[6]); });
    if (!all.length) return '';
    var lo = opt.lo != null ? opt.lo : Math.floor(Math.min.apply(null, all) / 10) * 10, hi = Math.ceil(Math.max.apply(null, all) / 10) * 10 + (opt.pad || 0);
    var st = opt.step || niceStep(hi - lo), yt = []; for (var v = Math.ceil(lo / st) * st; v <= hi; v += st) yt.push(v);
    var c = Chart({ title: title, sub: opt.sub, x: [0, 240], y: [lo, hi], xt: HT, yt: yt, xl: 'forecast hour', yl: unit });
    /* hide the fan where fewer than 10 % of members still track the system: a handful of survivors is not a distribution */
    var ok = function (k) { return q[k] && (d.alive[k] == null || d.alive[k] >= .1); };
    var col = function (i) { return L.map(function (h, k) { return [h, ok(k) ? q[k][i] : null]; }); };
    if (opt.cats) CAT.forEach(function (x, i) { var a = x[1], b = CAT[i + 1] ? CAT[i + 1][1] : hi; if (a < hi && b > lo) c.bar(0, 240, Math.max(lo, a), Math.min(hi, b), x[2], .06); });
    c.band(col(0), col(6), opt.color || '#5dd3ff', .12); c.band(col(1), col(5), opt.color || '#5dd3ff', .2); c.band(col(2), col(4), opt.color || '#5dd3ff', .32);
    c.line(col(3), '#ffffff', 2.4);
    if (opt.mean) c.line(L.map(function (h, k) { return [h, ok(k) ? opt.mean[k] : null]; }), MEAN, 2, '5 3');
    var cut = L.filter(function (h, k) { return q[k] && !ok(k); })[0];
    if (cut != null) c.raw('<text class="gd-a" x="' + (cut > 150 ? c.X(cut) - 4 : c.X(cut) + 4) + '" y="40" text-anchor="' + (cut > 150 ? 'end' : 'start') + '">fewer than 10 % of members beyond here</text>');
    (opt.extra || []).forEach(function (e) { c.line(e.pts, e.c, e.w || 1.6, e.dash); });
    c.legend([['min-max', 'rgba(93,211,255,.3)'], ['P10-P90', 'rgba(93,211,255,.45)'], ['P25-P75', 'rgba(93,211,255,.7)'], ['median', '#fff']].concat(opt.mean ? [['mean', MEAN]] : []).concat(opt.leg || []));
    return c.done();
  }
  function niceStep(r) { return r > 400 ? 100 : r > 160 ? 40 : r > 80 ? 20 : r > 40 ? 10 : r > 16 ? 5 : r > 6 ? 2 : 1; }

  /* ---------------- map kit (Mercator canvas, frame = the storm's own unwrapped longitudes) ---------------- */
  function GMap(cv, bbox) {
    var g = cv.getContext('2d'), dpr = window.devicePixelRatio || 1, W = cv.clientWidth || 640, H = cv.clientHeight || 420;
    cv.width = W * dpr; cv.height = H * dpr; g.setTransform(dpr, 0, 0, dpr, 0, 0);
    var my = function (la) { return Math.log(Math.tan(Math.PI / 4 + la * Math.PI / 360)); };
    var w = bbox[0], s = bbox[1], e = bbox[2], n = bbox[3];
    var sx = W / (e - w) * 180 / Math.PI, sy = H / (my(n) - my(s)), k = Math.min(sx, sy);
    var cx = (w + e) / 2, cyv = (my(n) + my(s)) / 2;
    var P = function (la, lo) { return [W / 2 + (lo - cx) * Math.PI / 180 * k, H / 2 - (my(la) - cyv) * k]; };
    var inv = function (x, y) { var lo = cx + (x - W / 2) / k * 180 / Math.PI, m = cyv - (y - H / 2) / k; return [(2 * Math.atan(Math.exp(m)) - Math.PI / 2) * 180 / Math.PI, lo]; };
    g.fillStyle = '#0b1726'; g.fillRect(0, 0, W, H);
    return { g: g, W: W, H: H, P: P, inv: inv, k: k,
      land: function (geo, lon0) {
        if (!geo) return;
        g.fillStyle = '#2f3f59'; g.strokeStyle = 'rgba(160,185,215,.45)'; g.lineWidth = .7;
        geo.features.forEach(function (f) {
          var gm = f.geometry; if (!gm) return; var polys = gm.type === 'MultiPolygon' ? gm.coordinates : [gm.coordinates];
          polys.forEach(function (poly) {
            var sh = Math.round((lon0 - poly[0][0][0]) / 360) * 360;
            [sh - 360, sh, sh + 360].forEach(function (o) {
              var ring = poly[0], xs = ring.map(function (p) { return p[0] + o; });
              if (Math.max.apply(null, xs) < cx - 200 || Math.min.apply(null, xs) > cx + 200) return;
              g.beginPath(); ring.forEach(function (p, i) { var q = P(p[1], p[0] + o); i ? g.lineTo(q[0], q[1]) : g.moveTo(q[0], q[1]); }); g.closePath(); g.fill(); g.stroke();
            });
          });
        });
      },
      grid: function () {
        g.strokeStyle = 'rgba(255,255,255,.07)'; g.lineWidth = 1; g.fillStyle = 'rgba(200,210,225,.55)'; g.font = '10px Metropolis, Arial';
        var step = (e - w) > 40 ? 10 : 5;
        for (var lo = Math.ceil(w / step) * step; lo <= e; lo += step) { var a = P(n, lo), b = P(s, lo); g.beginPath(); g.moveTo(a[0], 0); g.lineTo(b[0], H); g.stroke(); var L = ((lo + 540) % 360) - 180; g.fillText(Math.abs(L) + (L < 0 ? 'W' : L > 0 && L < 180 ? 'E' : ''), a[0] + 3, H - 4); }
        for (var la = Math.ceil(s / step) * step; la <= n; la += step) { var c2 = P(la, w); g.beginPath(); g.moveTo(0, c2[1]); g.lineTo(W, c2[1]); g.stroke(); g.fillText(Math.abs(la) + (la < 0 ? 'S' : 'N'), 3, c2[1] - 3); }
      },
      path: function (pts, c, w, a, dash) { g.save(); g.globalAlpha = a == null ? 1 : a; g.strokeStyle = c; g.lineWidth = w || 1; g.lineJoin = 'round'; if (dash) g.setLineDash(dash); g.beginPath(); var pen = false;
        pts.forEach(function (p) { if (!p) { pen = false; return; } var q = P(p[0], p[1]); pen ? g.lineTo(q[0], q[1]) : g.moveTo(q[0], q[1]); pen = true; }); g.stroke(); g.restore(); },
      poly: function (pts, fill, stroke, a) { g.save(); g.globalAlpha = a == null ? 1 : a; g.beginPath(); pts.forEach(function (p, i) { var q = P(p[0], p[1]); i ? g.lineTo(q[0], q[1]) : g.moveTo(q[0], q[1]); }); g.closePath(); if (fill) { g.fillStyle = fill; g.fill(); } if (stroke) { g.strokeStyle = stroke; g.lineWidth = 1.4; g.stroke(); } g.restore(); },
      dot: function (la, lo, r, c, ring) { var q = P(la, lo); g.beginPath(); g.arc(q[0], q[1], r, 0, 7); g.fillStyle = c; g.fill(); if (ring) { g.strokeStyle = ring; g.lineWidth = 1.2; g.stroke(); } },
      header: function (t1, t2) { g.fillStyle = 'rgba(7,16,28,.82)'; g.fillRect(0, 0, W, 40); g.fillStyle = '#e8ebef'; g.font = '700 14px Metropolis, Arial'; g.fillText(t1, 10, 17); g.fillStyle = '#9fb3d6'; g.font = '11px Metropolis, Arial'; g.fillText(t2, 10, 33);
        g.textAlign = 'right'; g.fillStyle = 'rgba(232,235,239,.55)'; g.fillText('@WeathermanAAA_', W - 8, H - 6); g.textAlign = 'left'; },
      raster: function (gd, bytes, ramp, alpha) {
        var c = document.createElement('canvas'); c.width = gd.nx; c.height = gd.ny; var x = c.getContext('2d'), im = x.createImageData(gd.nx, gd.ny);
        for (var i = 0; i < bytes.length; i++) { var col = ramp(bytes[i]); if (!col) continue; im.data[i * 4] = col[0]; im.data[i * 4 + 1] = col[1]; im.data[i * 4 + 2] = col[2]; im.data[i * 4 + 3] = alpha || 200; }
        x.putImageData(im, 0, 0);
        // rows are equal-angle; draw strip by strip so the Mercator stretch is right
        var b = gd.bbox; g.imageSmoothingEnabled = false;
        for (var r = 0; r < gd.ny; r++) { var la1 = b[3] - r * gd.res, la2 = la1 - gd.res, p1 = P(la1, b[0]), p2 = P(la2, b[2]); g.drawImage(c, 0, r, gd.nx, 1, p1[0], p1[1], p2[0] - p1[0], p2[1] - p1[1] + .6); }
      }
    };
  }
  function bboxOf(d, extra) {
    var la = [], lo = [];
    (d.mean || []).forEach(function (p) { if (p) { la.push(p[0]); lo.push(p[1]); } });
    Object.keys(d.ellipses || {}).forEach(function (h) { if (+h <= 120) d.ellipses[h]['90'].forEach(function (p) { la.push(p[0]); lo.push(p[1]); }); });
    (extra || []).forEach(function (p) { la.push(p[0]); lo.push(p[1]); });
    if (!la.length) return [S.doc.lon0 - 15, 0, S.doc.lon0 + 15, 30];
    var w = Math.min.apply(null, lo) - 4, e = Math.max.apply(null, lo) + 4, s = Math.max(-75, Math.min.apply(null, la) - 4), n = Math.min(75, Math.max.apply(null, la) + 4);
    var asp = (e - w) / (n - s); if (asp < 1.35) { var add = ((n - s) * 1.35 - (e - w)) / 2; w -= add; e += add; }
    return [w, s, e, n];
  }
  function b64(s) { var b = atob(s), a = new Uint8Array(b.length); for (var i = 0; i < b.length; i++) a[i] = b.charCodeAt(i); return a; }
  var PRAMP = [[5, '#b8e3ff'], [10, '#7cc3ea'], [20, '#3fa4ff'], [30, '#46c56a'], [40, '#c7e66c'], [50, '#ffe14d'], [60, '#ffb83a'], [70, '#ff7a2f'], [80, '#f5333c'], [90, '#b0186c']];
  var hex = function (h) { return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)]; };
  function pcol(v) { var c = null; PRAMP.forEach(function (x) { if (v >= x[0]) c = x[1]; }); return c && hex(c); }
  function pLegend(t) { return '<div class="gd-ramp"><span>' + esc(t) + '</span>' + PRAMP.map(function (x) { return '<i style="background:' + x[1] + '">' + x[0] + '</i>'; }).join('') + '<em>%</em></div>'; }

  /* ---------------- panels ---------------- */
  function tiles(d) {
    var ri48 = d.ri_cum[LI(48)], ri120 = d.ri_cum[LI(120)], c72 = (d.cat || []).filter(function (r) { return r.h === 72; })[0];
    var hu72 = c72 ? ['C1', 'C2', 'C3', 'C4', 'C5'].reduce(function (s, k) { return s + (c72[k] || 0); }, 0) : null;
    var pk = d.peak_q || [], lf = d.landfall || {};
    var T = [['Peak wind P10 / P50 / P90', pk.length ? Math.round(pk[1]) + ' / <b>' + Math.round(pk[3]) + '</b> / ' + Math.round(pk[5]) + ' kt' : '–'],
      ['RI chance by 48 h', pct(ri48)], ['RI chance by 120 h', pct(ri120)], ['Hurricane at 72 h', pct(hu72)],
      ['Landfall by 120 h', pct(lf.p120)], ['Members tracking at 120 h', pct(d.alive[LI(120)])]];
    return '<div class="gd-tiles">' + T.map(function (t) { return '<div><small>' + esc(t[0]) + '</small><span>' + t[1] + '</span></div>'; }).join('') + '</div>';
  }
  function mapBlock(id) { return '<div class="gd-mapwrap"><canvas class="gd-map" id="' + id + '"></canvas><div class="gd-tip" id="' + id + 'Tip"></div></div>'; }
  function trackMap(cv, d, lead, opt) {
    opt = opt || {};
    landGeo().then(function (geo) {
      var mem = membersOf(d) || [], pts = [], hmax = opt.density || opt.land ? 120 : 240;
      mem.forEach(function (m) { m.t.forEach(function (p) { if (p[0] <= 120) pts.push([p[1], p[2]]); }); });
      var M = GMap(cv, bboxOf(d, pts.filter(function (_, i) { return i % 7 === 0; })));
      M.land(geo, S.doc.lon0); M.grid();
      if (opt.density && d.grid) { M.raster(d.grid, b64(d.grid.p['c120_120']), pcol, 190); }
      if (!opt.density) mem.forEach(function (m) { M.path(m.t.filter(function (p) { return p[0] <= 240; }).map(function (p) { return [p[1], p[2]]; }), SC[m.m] || '#9fb3d6', 1, .45); });
      ['90', '50'].forEach(function (pc) { Object.keys(d.ellipses).forEach(function (h) { if (+h <= 120) M.poly(d.ellipses[h][pc], null, pc === '50' ? 'rgba(255,255,255,.85)' : 'rgba(255,255,255,.45)'); }); });
      var mt = d.mean.map(function (p, k) { return p && d.leads[k] <= hmax ? p : null; });
      M.path(mt, '#06101f', 5, .7); M.path(mt, MEAN, 2.6);
      d.mean.forEach(function (p, k) { if (p && d.leads[k] % 24 === 0 && d.leads[k] <= hmax) M.dot(p[0], p[1], 3.5, MEAN, '#06101f'); });
      if (lead != null && !opt.density) mem.forEach(function (m) { m.t.forEach(function (p) { if (p[0] === lead) M.dot(p[1], p[2], 3.6, catC(p[3] || 0), '#06101f'); }); });
      if (opt.land && d.landfall && d.landfall.pts) d.landfall.pts.forEach(function (p) { M.dot(p[0], p[1], 3.4, catC(p[3]), '#fff'); });
      var t1 = (S.doc.name || S.doc.id) + ' · ' + d.label + (opt.density ? ' · track density' : opt.land ? ' · landfall points' : ' · tracks');
      M.header(t1, initTxt() + (lead != null && !opt.density && !opt.land ? ' · F' + String(lead).padStart(3, '0') + ' · valid ' + vt(lead) : ' · 0-120 h') + ' · mean (amber), 50/90 % position ellipses every 24 h');
    });
  }
  function windMap(cv, d) {
    landGeo().then(function (geo) {
      var gd = d.grid; if (!gd) return;
      var b = gd.bbox, M = GMap(cv, [b[0] + 3, b[1] + 3, b[2] - 3, b[3] - 3]);
      M.land(geo, S.doc.lon0);
      var key = S.wk + '_' + S.wh, bytes = gd.p[key] ? b64(gd.p[key]) : null;
      if (bytes) M.raster(gd, bytes, pcol, 205);
      M.grid();
      M.path(d.mean.map(function (p, k) { return p && d.leads[k] <= +S.wh ? p : null; }), '#06101f', 4.5, .7); M.path(d.mean.map(function (p, k) { return p && d.leads[k] <= +S.wh ? p : null; }), MEAN, 2.2);
      var lab = S.wk === 'c120' ? 'Centre passes within 120 km' : S.wk + '-kt winds';
      M.header((S.doc.name || S.doc.id) + ' · ' + d.label + ' · P(' + lab + ')', initTxt() + ' · within ' + S.wh + ' h (valid to ' + vt(+S.wh) + ') · from each member\'s own wind radii');
      var arr = gd.arrival34 ? b64(gd.arrival34) : null, tip = document.getElementById(cv.id + 'Tip');
      cv.onmousemove = function (ev) {
        var r = cv.getBoundingClientRect(), ll = M.inv(ev.clientX - r.left, ev.clientY - r.top);
        var c = Math.floor((ll[1] - b[0]) / gd.res), rr = Math.floor((b[3] - ll[0]) / gd.res);
        if (c < 0 || rr < 0 || c >= gd.nx || rr >= gd.ny) { tip.style.display = 'none'; return; }
        var i = rr * gd.nx + c, lines = [];
        ['34', '50', '64', 'c120'].forEach(function (k) { var a = gd.p[k + '_' + S.wh]; if (a) lines.push((k === 'c120' ? 'centre within 120 km' : k + ' kt') + ': ' + b64cache(a)[i] + '%'); });
        if (arr && arr[i] < 255) lines.push('34-kt winds arrive (median): ' + vt(arr[i] * 2));
        tip.innerHTML = '<b>' + Math.abs(ll[0]).toFixed(1) + (ll[0] < 0 ? 'S' : 'N') + ' ' + Math.abs(((ll[1] + 540) % 360) - 180).toFixed(1) + (((ll[1] + 540) % 360) - 180 < 0 ? 'W' : 'E') + '</b><br>' + lines.join('<br>');
        tip.style.display = 'block'; tip.style.left = Math.min(r.width - 190, ev.clientX - r.left + 12) + 'px'; tip.style.top = (ev.clientY - r.top + 12) + 'px';
      };
      cv.onmouseleave = function () { tip.style.display = 'none'; };
    });
  }
  var B64C = {}; function b64cache(s) { if (!B64C[s.length + s.slice(0, 40)]) B64C[s.length + s.slice(0, 40)] = b64(s); return B64C[s.length + s.slice(0, 40)]; }

  function render() {
    controls();
    var d = D(), body = $('.gd-body'); if (!d) { body.innerHTML = ''; return; }
    var cap = d.derived ? '<p class="gd-note">Derived, not a model run: pools ' + d.pooled.map(function (k) { return SN[k]; }).join(', ') + ' with equal weight per model' + (d.radii_from && d.radii_from.length < d.pooled.length ? '; size and wind products use ' + d.radii_from.map(function (k) { return SN[k]; }).join(' and ') + ' (GenCast carries no wind radii)' : '') + '.</p>' : '';
    if (S.suite === 'genc') cap += '<p class="gd-note">GenCast publishes track and intensity only, so it has no size or wind-probability products.</p>';
    if (S.suite === 'fnv3x') cap += '<p class="gd-note">The 1000-member run is summarised as statistics; individual tracks are not drawn.</p>';
    var t = S.tab, h = '';
    if (t === 'over') {
      h = tiles(d) + '<div class="gd-two">' + mapBlock('gdMap') + '<div>' + fan(d, 'vmax_q', 'Max sustained wind', 'kt', { cats: true, lo: 0, mean: d.vmax_mean, sub: initTxt() }) + '</div></div>';
    } else if (t === 'track') {
      h = '<div class="gd-ctl"><label>Forecast hour <input type="range" id="gdLead" min="0" max="240" step="6" value="' + S.lead + '"></label><b id="gdLeadT"></b>' +
        '<span class="hafs-seg-group" id="gdTm"></span></div>' + mapBlock('gdMap') +
        '<div class="gd-two">' + spreadChart(d) + catChart(d) + '</div>';
    } else if (t === 'int') {
      h = '<div class="gd-ctl"><span class="hafs-seg-group" id="gdMet"></span></div><div class="gd-two">' +
        (S.metric === 'v' ? fan(d, 'vmax_q', 'Max sustained wind', 'kt', { cats: true, lo: 0, mean: d.vmax_mean, sub: initTxt() }) : fan(d, 'mslp_q', 'Minimum pressure', 'hPa', { sub: initTxt(), color: '#7cc3ea' })) +
        aceFan(d) + '</div><div class="gd-two">' + peakHist(d) + peak2d(d) + '</div>';
    } else if (t === 'chg') {
      h = '<div class="gd-two">' + riChart(d) + '<div><div class="gd-ctl"><span class="hafs-seg-group" id="gdDvk"></span><label>Ending at hour <select id="gdDvh">' +
        Object.keys(d['dv' + S.dvk]).map(function (x) { return '<option' + (x === S.dvh ? ' selected' : '') + '>' + x + '</option>'; }).join('') + '</select></label></div>' + dvHist(d) + '</div></div>' + catChart(d, true);
    } else if (t === 'str') {
      h = d.rmw_q ? '<div class="gd-two">' + fan(d, 'rmw_q', 'Radius of maximum wind', 'km', { lo: 0, color: '#c084fc', sub: 'members at 34 kt or more' }) + fan(d, 'r34_q', '34-kt radius, mean of quadrants', 'km', { lo: 0, color: '#46c56a', sub: 'storm size' }) + '</div>' +
        '<div class="gd-two">' + nesting(d) + rose(d) + '</div><div class="gd-two">' + fan(d, 'area34_q', 'Area inside 34-kt winds', '', { lo: 0, color: '#ffb83a', sub: 'thousand km²' }) + '<div></div></div>'
        : '<p class="gd-empty">No wind-radii data for this suite.</p>';
    } else if (t === 'wind') {
      h = d.grid ? '<div class="gd-ctl"><span class="hafs-seg-group" id="gdWk"></span><span class="hafs-seg-group" id="gdWh"></span></div>' + mapBlock('gdMap') + pLegend(S.wk === 'c120' ? 'P(centre within 120 km)' : 'P(' + S.wk + '-kt winds)') +
        '<p class="gd-note">A point counts for a member when the member\'s own quadrant radius covers it at any time in the window (tracks interpolated every 2 h). Hover the map for every threshold and the median arrival time of 34-kt winds.</p>' : '<p class="gd-empty">No grid for this suite.</p>';
    } else if (t === 'land') {
      var lf = d.landfall || {};
      h = '<div class="gd-tiles"><div><small>Landfall by 120 h</small><span>' + pct(lf.p120) + '</span></div><div><small>Landfall by 240 h</small><span>' + pct(lf.p240) + '</span></div>' +
        '<div><small>Most likely landfall area</small><span>' + (lf.where && lf.where.length ? esc(lf.where[0][0]) + ' ' + pct(lf.where[0][1]) : '–') + '</span></div></div>' +
        '<div class="gd-two">' + (lf.pts ? mapBlock('gdMap') : '<div></div>') + '<div>' + lfTiming(lf) + lfCat(lf) + '</div></div>' +
        (lf.where && lf.where.length ? '<div class="gd-where"><b>Landfall areas</b>' + lf.where.map(function (w) { return '<span>' + esc(w[0]) + ' <em>' + pct(w[1]) + '</em></span>'; }).join('') + '</div>' : '') +
        '<p class="gd-note">Landfall is the first move from sea onto land (Natural Earth 10 m coastline, tracks interpolated hourly) within 240 h. Shares are of all members.</p>';
    } else if (t === 'cmp') {
      h = cmpTable() + '<div class="gd-two">' + cmpChart('v') + cmpTracks() + '</div>';
    } else if (t === 'trend') {
      h = trends();
    }
    body.innerHTML = h + cap + '<p class="gd-attr">' + esc(S.doc.attribution) + ' Not an official forecast; see NHC, CPHC, JTWC or the responsible warning centre.</p>';
    wire(d);
  }
  function wire(d) {
    var cv = document.getElementById('gdMap');
    if (S.tab === 'over' && cv) trackMap(cv, d, null, { density: !membersOf(d) });
    if (S.tab === 'track') {
      var r = document.getElementById('gdLead'), lt = document.getElementById('gdLeadT');
      var go = function () { S.lead = +r.value; lt.textContent = 'F' + String(S.lead).padStart(3, '0') + ' · valid ' + vt(S.lead); trackMap(cv, d, S.lead, { density: S.tmode === 'den' || !membersOf(d) }); };
      r.oninput = go; seg(document.getElementById('gdTm'), [['mem', 'Members'], ['den', 'Density']], S.tmode || 'mem', function (k) { S.tmode = k; render(); }, function (k) { return k === 'mem' && !membersOf(d); }); go();
    }
    if (S.tab === 'int') seg(document.getElementById('gdMet'), [['v', 'Wind'], ['p', 'Pressure']], S.metric, function (k) { S.metric = k; render(); });
    if (S.tab === 'chg') { seg(document.getElementById('gdDvk'), [['24', '24-h change'], ['12', '12-h change']], S.dvk, function (k) { S.dvk = k; if (!d['dv' + k][S.dvh]) S.dvh = '48'; render(); });
      document.getElementById('gdDvh').onchange = function (e) { S.dvh = e.target.value; render(); }; }
    if (S.tab === 'wind' && cv) {
      seg(document.getElementById('gdWk'), [['34', '34 kt'], ['50', '50 kt'], ['64', '64 kt'], ['c120', 'Centre within 120 km']], S.wk, function (k) { S.wk = k; render(); }, function (k) { return !d.grid.p[k + '_120']; });
      seg(document.getElementById('gdWh'), [['72', '72 h'], ['120', '120 h']], S.wh, function (k) { S.wh = k; render(); });
      if (!d.grid.p[S.wk + '_120']) S.wk = 'c120';
      windMap(cv, d);
    }
    if (S.tab === 'land' && cv) trackMap(cv, d, null, { land: true });
    if (S.tab === 'cmp') drawCmpTracks();
  }

  /* ---------------- individual charts ---------------- */
  function spreadChart(d) {
    var pts = d.leads.map(function (h, k) { return [h, d.spread_km[k]]; }), mx = Math.max.apply(null, d.spread_km.filter(Number.isFinite).concat([100]));
    var st = niceStep(mx), yt = []; for (var v = 0; v <= mx * 1.1; v += st * 2) yt.push(v);
    var c = Chart({ title: 'Track spread', sub: 'mean distance from the ensemble-mean position', x: [0, 240], y: [0, mx * 1.1], xt: HT, yt: yt, yl: 'km', xl: 'forecast hour' });
    c.line(pts, SC[S.suite] || '#5dd3ff', 2.4);
    c.line(d.leads.map(function (h, k) { return [h, d.alive[k] * mx]; }), '#9fb3d6', 1.2, '3 3'); c.legend([['spread', SC[S.suite]], ['members still tracking (scaled)', '#9fb3d6']]);
    return c.done();
  }
  function catChart(d, wide) {
    var rows = d.cat, keys = ['diss', 'TD', 'TS', 'C1', 'C2', 'C3', 'C4', 'C5'], col = { diss: '#3a4560', TD: '#3fa4ff', TS: '#46c56a', C1: '#ffe14d', C2: '#ff9a2f', C3: '#f5333c', C4: '#e33ad4', C5: '#b03bff' };
    var c = Chart({ w: wide ? 1060 : 520, title: 'Intensity category by forecast hour', sub: 'share of members', x: [0, 240], y: [0, 1], xt: HT, yt: [0, .25, .5, .75, 1], yf: function (v) { return Math.round(v * 100) + '%'; }, xl: 'forecast hour' });
    var base = rows.map(function () { return 0; });
    keys.forEach(function (k) {
      var lo = rows.map(function (r, i) { return [r.h, base[i]]; }), hi = rows.map(function (r, i) { base[i] += r[k] || 0; return [r.h, base[i]]; });
      c.band(lo, hi, col[k], .85);
    });
    c.legend(keys.map(function (k) { return [k === 'diss' ? 'lost' : k, col[k]]; }));
    return c.done();
  }
  function aceFan(d) {
    var q = d.ace_q, L = d.leads.filter(function (_, k) { return k % 2 === 0; });
    var mx = Math.max.apply(null, q.filter(Boolean).map(function (r) { return r[5]; }).concat([1]));
    var c = Chart({ title: 'Accumulated cyclone energy', sub: '6-hourly, 34 kt and above', x: [0, 240], y: [0, mx * 1.1], xt: HT, yt: [0, mx / 4, mx / 2, mx * .75, mx].map(function (v) { return +v.toFixed(1); }), yl: 'ACE', xl: 'forecast hour' });
    var col = function (i) { return L.map(function (h, k) { return [h, q[k] ? q[k][i] : null]; }); };
    c.band(col(1), col(5), '#ffb83a', .2); c.band(col(2), col(4), '#ffb83a', .35); c.line(col(3), '#fff', 2.2); c.legend([['P10-P90', 'rgba(255,184,58,.4)'], ['P25-P75', 'rgba(255,184,58,.7)'], ['median', '#fff']]);
    return c.done();
  }
  function peakHist(d) {
    var hb = d.peak_hist, mx = Math.max.apply(null, hb.p.concat([.05]));
    var c = Chart({ title: 'Lifetime peak wind', sub: '0-240 h, share of members', x: [0, 190], y: [0, mx * 1.15], xt: [0, 34, 64, 96, 137, 180], yt: [0, mx / 2, mx].map(function (v) { return +v.toFixed(2); }), yf: function (v) { return Math.round(v * 100) + '%'; }, xl: 'kt' });
    hb.p.forEach(function (p, i) { c.bar(hb.bins[i] + .6, hb.bins[i + 1] - .6, 0, p, catC(hb.bins[i] + 5)); });
    if (d.peak_q) { c.hl(0, '#fff'); c.raw('<line x1="' + c.X(d.peak_q[3]) + '" x2="' + c.X(d.peak_q[3]) + '" y1="28" y2="210" stroke="#fff" stroke-dasharray="4 3"/>'); c.text(d.peak_q[3] + 2, mx * 1.08, 'median ' + Math.round(d.peak_q[3]) + ' kt', '#fff'); }
    return c.done();
  }
  function peak2d(d) {
    var P = d.peak2d, mx = 0; P.p.forEach(function (r) { r.forEach(function (v) { mx = Math.max(mx, v); }); });
    var c = Chart({ title: 'When the peak happens', sub: 'peak wind against forecast hour of the peak', x: [0, 240], y: [0, 190], xt: HT, yt: [0, 34, 64, 96, 137, 180], yl: 'kt', xl: 'forecast hour of peak' });
    P.p.forEach(function (row, i) { row.forEach(function (v, j) { if (v > 0) c.bar(P.hbins[j], P.hbins[j + 1], P.vbins[i], P.vbins[i + 1], '#5dd3ff', Math.min(1, .15 + .85 * v / mx)); }); });
    return c.done();
  }
  function riChart(d) {
    var c = Chart({ title: 'Rapid intensification', sub: '30 kt or more in 24 h', x: [0, 240], y: [0, 1], xt: HT, yt: [0, .25, .5, .75, 1], yf: function (v) { return Math.round(v * 100) + '%'; }, xl: 'forecast hour' });
    c.line(d.leads.map(function (h, k) { return [h, d.ri_cum[k]]; }), '#ff6b5e', 2.6); c.line(d.leads.map(function (h, k) { return [h, d.ri_lead[k]]; }), '#ffd24a', 2, '4 3');
    c.legend([['RI by this hour (cumulative)', '#ff6b5e'], ['RI ending at this hour', '#ffd24a']]);
    return c.done();
  }
  function dvHist(d) {
    var hb = (d['dv' + S.dvk] || {})[S.dvh]; if (!hb) return '<p class="gd-empty">No members at that hour.</p>';
    var B = d.dv_bins, mx = Math.max.apply(null, hb.concat([.05]));
    var c = Chart({ title: S.dvk + '-h wind change ending at F' + String(S.dvh).padStart(3, '0'), sub: 'valid ' + vt(+S.dvh), x: [B[0], B[B.length - 1]], y: [0, mx * 1.15], xt: B.filter(function (_, i) { return i % 2 === 0; }), yt: [0, mx / 2, mx].map(function (v) { return +v.toFixed(2); }), yf: function (v) { return Math.round(v * 100) + '%'; }, xl: 'kt' });
    hb.forEach(function (p, i) { c.bar(B[i] + .8, B[i + 1] - .8, 0, p, B[i] >= 30 && S.dvk === '24' ? '#ff6b5e' : B[i] >= 0 ? '#ffb83a' : '#5dd3ff'); });
    return c.done();
  }
  function nesting(d) {
    var L = d.leads, all = [].concat(d.r50_med, d.r64_med, (d.r34_q || []).map(function (r) { return r && r[3]; })).filter(Number.isFinite);
    var mx = Math.max.apply(null, all.concat([100])) * 1.1, st = niceStep(mx), yt = []; for (var v = 0; v <= mx; v += st * 2) yt.push(v);
    var c = Chart({ title: 'Wind-field nesting', sub: 'median quadrant-mean radii', x: [0, 240], y: [0, mx], xt: HT, yt: yt, yl: 'km', xl: 'forecast hour' });
    c.line(L.map(function (h, k) { return [h, d.r34_q[k] && d.r34_q[k][3]]; }), '#46c56a', 2.4); c.line(L.map(function (h, k) { return [h, d.r50_med[k]]; }), '#ffe14d', 2.2); c.line(L.map(function (h, k) { return [h, d.r64_med[k]]; }), '#f5333c', 2.2);
    c.legend([['34 kt', '#46c56a'], ['50 kt', '#ffe14d'], ['64 kt', '#f5333c']]);
    return c.done();
  }
  function rose(d) {
    var R = d.rose_at_peak; if (!R) return '<div></div>';
    var W = 520, H = 240, cx = 260, cy = 132, mx = Math.max.apply(null, R['34'].concat([50])), k = 92 / mx;
    var s = '<svg viewBox="0 0 ' + W + ' ' + H + '" class="gd-svg"><text class="gd-t" x="44" y="16">Wind radii at each member\'s peak</text><text class="gd-ts" x="506" y="16" text-anchor="end">median by quadrant</text>';
    [.25, .5, .75, 1].forEach(function (f) { s += '<circle cx="' + cx + '" cy="' + cy + '" r="' + (92 * f) + '" fill="none" stroke="#2a2e36"/><text class="gd-a" x="' + (cx + 3) + '" y="' + (cy - 92 * f + 10) + '">' + Math.round(mx * f) + ' km</text>'; });
    [['34', '#46c56a'], ['50', '#ffe14d'], ['64', '#f5333c']].forEach(function (x) {
      R[x[0]].forEach(function (r, q) { var a0 = (-90 + q * 90) * Math.PI / 180, a1 = a0 + Math.PI / 2, rr = (r || 0) * k;
        s += '<path d="M' + cx + ' ' + cy + 'L' + (cx + rr * Math.cos(a0)) + ' ' + (cy + rr * Math.sin(a0)) + 'A' + rr + ' ' + rr + ' 0 0 1 ' + (cx + rr * Math.cos(a1)) + ' ' + (cy + rr * Math.sin(a1)) + 'Z" fill="' + x[1] + '" fill-opacity=".35" stroke="' + x[1] + '"/>'; });
    });
    s += '<text class="gd-a" x="' + cx + '" y="' + (cy - 98) + '" text-anchor="middle">N</text><text class="gd-a" x="' + (cx + 100) + '" y="' + (cy + 4) + '">E</text>';
    return s + '</svg>';
  }
  function lfTiming(lf) {
    if (!lf.timing) return '';
    var B = lf.bins, tot = B.slice(0, -1).map(function (_, i) { return Object.keys(lf.timing).reduce(function (s, k) { return s + lf.timing[k][i]; }, 0); }), mx = Math.max.apply(null, tot.concat([.02]));
    var c = Chart({ title: 'When members make landfall', sub: '12-h bins, by intensity at landfall', x: [0, 240], y: [0, mx * 1.15], xt: HT, yt: [0, mx / 2, mx].map(function (v) { return +v.toFixed(2); }), yf: function (v) { return Math.round(v * 100) + '%'; }, xl: 'forecast hour' });
    B.slice(0, -1).forEach(function (b, i) { var y = 0; CAT.forEach(function (x) { var v = lf.timing[x[0]][i]; if (v) { c.bar(b + 1, B[i + 1] - 1, y, y + v, x[2]); y += v; } }); });
    c.legend(CAT.map(function (x) { return [x[0], x[2]]; }));
    return c.done();
  }
  function lfCat(lf) {
    if (!lf.cat || !lf.p240) return '';
    var c = Chart({ h: 170, title: 'Intensity at landfall', sub: 'share of landfalling members', x: [0, 7], y: [0, 1], yt: [0, .5, 1], yf: function (v) { return Math.round(v * 100) + '%'; } });
    CAT.forEach(function (x, i) { c.bar(i + .1, i + .9, 0, lf.cat[x[0]] || 0, x[2]); c.text(i + .5, -0.08, x[0], '#cfd6e0', 'middle'); });
    return c.done();
  }
  function cmpRows() { return SUITES.filter(function (s) { return S.doc.suites[s[0]]; }); }
  function cmpTable() {
    var rows = cmpRows(), ref = S.doc.suites.gdm || S.doc.suites[rows[0][0]];
    var km = function (a, b) { if (!a || !b) return null; var x = (a[1] - b[1]) * 111.32 * Math.cos(a[0] * Math.PI / 180), y = (a[0] - b[0]) * 110.57; return Math.round(Math.hypot(x, y)); };
    var h = '<div class="gd-tablewrap"><table class="gd-table"><thead><tr><th>Hour</th>' + rows.map(function (s) { return '<th style="color:' + SC[s[0]] + '">' + esc(SN[s[0]]) + '</th>'; }).join('') + '</tr></thead><tbody>';
    [24, 48, 72, 96, 120].forEach(function (hh) {
      var k = LI(hh), kc = hh / 12;
      h += '<tr><td><b>F' + String(hh).padStart(3, '0') + '</b><small>' + vt(hh) + '</small></td>' + rows.map(function (s) {
        var d = S.doc.suites[s[0]], q = d.vmax_q[k], c = d.cat[kc], hu = c ? ['C1', 'C2', 'C3', 'C4', 'C5'].reduce(function (a, x) { return a + (c[x] || 0); }, 0) : null, dk = km(d.mean[k], ref.mean[k]);
        return '<td><b>' + (q ? Math.round(q[3]) + ' kt' : '–') + '</b><small>P(hurricane) ' + pct(hu) + ' · spread ' + (d.spread_km[k] != null ? d.spread_km[k] + ' km' : '–') + (dk != null && d !== ref ? ' · ' + dk + ' km from ' + SN[S.doc.suites.gdm ? 'gdm' : rows[0][0]] : '') + '</small></td>';
      }).join('') + '</tr>';
    });
    return h + '</tbody></table></div><p class="gd-note">Median wind, chance of hurricane strength, track spread, and how far each suite\'s mean position sits from the ' + (S.doc.suites.gdm ? 'super-ensemble mean' : 'first suite') + ', aligned by valid time.</p>';
  }
  function cmpChart() {
    var rows = cmpRows(), all = [];
    rows.forEach(function (s) { S.doc.suites[s[0]].vmax_q.forEach(function (q) { if (q) all.push(q[4]); }); });
    var hi = Math.ceil(Math.max.apply(null, all.concat([80])) / 20) * 20 + 10;
    var c = Chart({ title: 'Median wind by suite', sub: 'with the interquartile range', x: [0, 240], y: [0, hi], xt: HT, yt: [0, 34, 64, 96, 137].filter(function (v) { return v < hi; }), yl: 'kt', xl: 'forecast hour' });
    rows.forEach(function (s) { var d = S.doc.suites[s[0]], col = function (i) { return d.leads.map(function (h, k) { return [h, d.vmax_q[k] ? d.vmax_q[k][i] : null]; }); }; c.band(col(2), col(4), SC[s[0]], .12); c.line(col(3), SC[s[0]], 2.2); });
    c.legend(rows.map(function (s) { return [SN[s[0]], SC[s[0]]]; }));
    return c.done();
  }
  function cmpTracks() { return mapBlock('gdCmp'); }
  function drawCmpTracks() {
    var cv = document.getElementById('gdCmp'); if (!cv) return;
    landGeo().then(function (geo) {
      var rows = cmpRows(), ref = S.doc.suites.gdm || S.doc.suites[rows[0][0]], M = GMap(cv, bboxOf(ref));
      M.land(geo, S.doc.lon0); M.grid();
      rows.forEach(function (s) { var d = S.doc.suites[s[0]]; if (d.ellipses['72']) M.poly(d.ellipses['72']['50'], null, SC[s[0]], .7);
        var tr = d.mean.map(function (p, k) { return p && d.leads[k] <= 168 ? p : null; }); M.path(tr, '#06101f', 4.5, .6); M.path(tr, SC[s[0]], 2.4);
        d.mean.forEach(function (p, k) { if (p && d.leads[k] % 24 === 0 && d.leads[k] <= 168) M.dot(p[0], p[1], 3, SC[s[0]], '#06101f'); }); });
      M.header((S.doc.name || S.doc.id) + ' · mean tracks by suite', initTxt() + ' · 0-168 h, dots every 24 h, 50 % ellipse at 72 h');
    });
  }
  function trends() {
    var cy = Object.keys(S.idx.cycles).sort(), rows = [];
    cy.forEach(function (c) { var st = S.idx.cycles[c].storms[S.sid]; if (st && st.suites[S.suite]) rows.push([c, st.suites[S.suite]]); });
    if (!rows.length) return '<p class="gd-empty">No earlier runs for this storm and suite.</p>';
    var h = '<div class="gd-two">' + mapBlock('gdTr') + '<div class="gd-tablewrap"><table class="gd-table"><thead><tr><th>Run</th><th>Median peak</th><th>RI by 72 h</th><th>Landfall by 120 h</th></tr></thead><tbody>' +
      rows.slice().reverse().map(function (r) { return '<tr' + (r[0] === S.cyc ? ' class="on"' : '') + '><td><b>' + r[0].slice(6, 8) + '/' + r[0].slice(8, 10) + 'Z</b></td><td>' + (r[1].peak50 != null ? Math.round(r[1].peak50) + ' kt' : '–') + '</td><td>' + pct(r[1].ri72) + '</td><td>' + pct(r[1].lf120) + '</td></tr>'; }).join('') +
      '</tbody></table></div></div><p class="gd-note">Run-to-run: each run\'s mean track to 120 h, older runs in slate and the newest in cyan.</p>';
    setTimeout(function () {
      var cv = document.getElementById('gdTr'); if (!cv) return;
      landGeo().then(function (geo) {
        var pts = []; rows.forEach(function (r) { r[1].mean.forEach(function (p) { if (p) pts.push(p); }); });
        var M = GMap(cv, bboxOf({ mean: [] }, pts)); M.land(geo, S.doc.lon0); M.grid();
        rows.forEach(function (r, i) {
          var f = rows.length > 1 ? i / (rows.length - 1) : 1, col = 'rgb(' + Math.round(110 - 17 * f) + ',' + Math.round(125 + 86 * f) + ',' + Math.round(150 + 105 * f) + ')';
          var tr = r[1].mean.map(function (p) { return p ? [p[0], S.doc.lon0 + ((p[1] - S.doc.lon0 + 540) % 360) - 180] : null; });
          M.path(tr, col, r[0] === S.cyc ? 3 : 1.8, r[0] === S.cyc ? 1 : .8);
          tr.forEach(function (p) { if (p) M.dot(p[0], p[1], 2.4, col); });
        });
        M.header((S.doc.name || S.doc.id) + ' · ' + D().label + ' · run-to-run mean tracks', rows.length + ' runs, ' + rows[0][0].slice(6, 8) + '/' + rows[0][0].slice(8, 10) + 'Z to ' + rows[rows.length - 1][0].slice(6, 8) + '/' + rows[rows.length - 1][0].slice(8, 10) + 'Z');
      });
    }, 0);
    return h;
  }

  /* ---------------- boot ---------------- */
  $('#gd-run').onchange = function (e) { pickCycle(e.target.value); };
  $('#gd-storm').onchange = function (e) { S.sid = e.target.value; controls(); loadStorm(); };
  var rt = null; window.addEventListener('resize', function () { clearTimeout(rt); rt = setTimeout(function () { if (S.doc) render(); }, 250); });
  loadIndex().then(function (ix) {
    S.idx = ix; var cy = Object.keys(ix.cycles).sort().reverse();
    if (!cy.length) throw 'empty';
    /* open on the newest run that has storms, and prefer the strongest-looking system there */
    var c = cy.filter(function (k) { return Object.keys(ix.cycles[k].storms).length; })[0] || cy[0];
    var st = ix.cycles[c].storms, best = Object.keys(st).sort(function (a, b) { var pa = (st[a].suites.gdm || st[a].suites.fnv3 || {}).peak50 || 0, pb = (st[b].suites.gdm || st[b].suites.fnv3 || {}).peak50 || 0; return pb - pa; })[0];
    S.sid = best; pickCycle(c);
  }).catch(function () { status('Google ensemble diagnostics are not available right now. They publish a few hours after each 00, 06, 12 and 18 UTC run.'); });
})();
