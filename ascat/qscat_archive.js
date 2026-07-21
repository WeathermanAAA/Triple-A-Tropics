/* Archived QuikSCAT storm passes (1999-2009) - a compact season/storm/pass
 * browser under the live ASCAT viewer. Reads qscat/manifest.json +
 * qscat/{slug}/index.json from the CDN; the whole section stays hidden
 * until the archive manifest exists (progressive enhancement, no 404 UI).
 * Data: NASA JPL QuikSCAT/SeaWinds, enhanced-resolution winds by the BYU
 * Scatterometer Climate Pathfinder. */
(function () {
  'use strict';
  var CDN = 'https://cdn.triple-a-tropics.com/qscat';
  var root = document.getElementById('qscat-archive');
  if (!root) return;

  function el(tag, cls, txt) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt != null) e.textContent = txt;
    return e;
  }

  fetch(CDN + '/manifest.json').then(function (r) {
    if (!r.ok) throw new Error('no manifest');
    return r.json();
  }).then(function (man) {
    var storms = (man && man.storms) || [];
    if (!storms.length) return;
    root.style.display = '';

    var controls = el('div', 'qsa-controls');
    var seasonSel = el('select'), stormSel = el('select'),
        passSel = el('select');
    [['Season', seasonSel], ['Storm', stormSel], ['Pass', passSel]]
      .forEach(function (pair) {
        var g = el('div', 'qsa-group');
        var lb = el('label', null, pair[0]);
        g.appendChild(lb); g.appendChild(pair[1]); controls.appendChild(g);
      });
    var img = el('img', 'qsa-img');
    img.alt = 'Archived QuikSCAT storm-pass wind field';
    var cap = el('div', 'qsa-cap');
    root.appendChild(controls);
    root.appendChild(img);
    root.appendChild(cap);

    var seasons = [];
    storms.forEach(function (s) {
      if (seasons.indexOf(s.season) < 0) seasons.push(s.season);
    });
    seasons.sort();
    var idxCache = {};

    function fill(sel, opts, val) {
      sel.innerHTML = '';
      opts.forEach(function (o) {
        var op = el('option', null, o[1]);
        op.value = o[0];
        sel.appendChild(op);
      });
      if (val != null) sel.value = String(val);
    }

    function stormsFor(season) {
      return storms.filter(function (s) {
        return s.season === +season;
      }).sort(function (a, b) {
        return a.basin < b.basin ? -1 : a.basin > b.basin ? 1
          : a.storm < b.storm ? -1 : 1;
      });
    }

    function showPass(slug, meta, p) {
      img.src = CDN + '/' + slug + '/rev' + p.rev + '.png';
      var when = p.t ? p.t.replace('T', ' ').slice(0, 16) + 'Z' : 'time n/a';
      cap.textContent = meta.storm + ' (' + meta.basin + ' ' + meta.season +
        ') · ' + when + ' · rev ' + p.rev +
        (p.bt_wind_kt ? ' · best track ' + p.bt_type + ' ' +
          p.bt_wind_kt + ' kt' : '');
    }

    function loadStorm(slug) {
      var meta = storms.filter(function (s) { return s.slug === slug; })[0];
      var got = idxCache[slug]
        ? Promise.resolve(idxCache[slug])
        : fetch(CDN + '/' + slug + '/index.json')
            .then(function (r) { return r.json(); })
            .then(function (j) { idxCache[slug] = j; return j; });
      got.then(function (j) {
        var ps = j.passes || [];
        fill(passSel, ps.map(function (p, i) {
          return [i, (p.t ? p.t.slice(5, 16).replace('T', ' ') + 'Z'
                          : 'rev ' + p.rev) +
                  (p.bt_type ? ' · ' + p.bt_type : '')];
        }));
        passSel.onchange = function () {
          showPass(slug, meta, ps[+this.value]);
        };
        if (ps.length) showPass(slug, meta, ps[0]);
      }).catch(function () {});
    }

    function onSeason() {
      var list = stormsFor(seasonSel.value);
      fill(stormSel, list.map(function (s) {
        return [s.slug, s.storm + ' (' + s.basin + ')' +
                (s.peak_bt_kt ? ' · ' + s.peak_bt_kt + ' kt' : '')];
      }));
      stormSel.onchange = function () { loadStorm(this.value); };
      if (list.length) loadStorm(list[0].slug);
    }

    fill(seasonSel, seasons.map(function (y) { return [y, String(y)]; }),
         seasons[seasons.length - 1]);
    seasonSel.onchange = onSeason;
    onSeason();
  }).catch(function () { /* archive not published yet - stay hidden */ });
})();
