/* HY-2 HSCAT delayed-daily wind viewer: region x satellite x pass-direction
 * selects over hy2/meta.json + PNGs. Hidden until meta exists; slots older
 * than 6 days are dropped (a stalled upstream never shows silently-old data
 * as if fresh - the date is always printed). */
(function () {
  'use strict';
  var CDN = 'https://cdn.triple-a-tropics.com/hy2';
  var root = document.getElementById('hy2-view');
  if (!root) return;
  var bust = '?t=' + Math.floor(Date.now() / 6e5);
  fetch(CDN + '/meta.json' + bust).then(function (r) {
    if (!r.ok) throw 0;
    return r.json();
  }).then(function (m) {
    var slots = m.slots || {};
    var fresh = Object.keys(slots).filter(function (k) {
      return (Date.now() - Date.parse(slots[k].date)) < 6 * 864e5;
    });
    if (!fresh.length) return;
    root.style.display = '';
    var ctr = document.getElementById('hy2-controls');
    var img = document.getElementById('hy2-img');
    var cap = document.getElementById('hy2-cap');
    var regions = m.regions || { atl: 'Atlantic' };
    function sel(labels, opts) {
      var g = document.createElement('div'); g.className = 'qsa-group';
      var lb = document.createElement('label'); lb.textContent = labels;
      var s = document.createElement('select');
      opts.forEach(function (o) {
        var op = document.createElement('option');
        op.value = o[0]; op.textContent = o[1];
        s.appendChild(op);
      });
      g.appendChild(lb); g.appendChild(s); ctr.appendChild(g);
      return s;
    }
    var regSel = sel('Region', Object.keys(regions).map(function (k) {
      return [k, regions[k]];
    }));
    var slotSel = sel('Satellite · passes', fresh.map(function (k) {
      return [k, k.replace('_', ' · ').toUpperCase()];
    }));
    function show() {
      var k = slotSel.value, s = slots[k];
      img.src = CDN + '/' + regSel.value + '_' + k + '.png' + bust;
      cap.textContent = 'Day ' + s.date +
        (s.tmin ? ' · passes ' + s.tmin.slice(11, 16) + '-' +
          s.tmax.slice(11, 16) + 'Z' : '') + ' · delayed product';
    }
    regSel.onchange = show;
    slotSel.onchange = show;
    show();
  }).catch(function () { /* not published yet - stay hidden */ });
})();
