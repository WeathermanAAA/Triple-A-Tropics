// Unit tests for the ASCAT viewer v2 features: storm-association reuse, floater
// time-match selection, drag-bbox extent math, and the /satellite/ IA restructure
// + /ascat/ redirect mapping. Pure-logic + static-HTML assertions (no browser);
// the interactive behaviour is covered by the headless render proofs in the PR.
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const ROOT = path.join(__dirname, '..');
const m = require(path.join(ROOT, 'ascat', 'ascat.js'));

// ---- F1: storm-association reuse (same predicate the manifest tags + CycloLab use)
(function stormMatch() {
  const s = { slug: 'al052026', atcf: 'AL052026', name: 'FIONA' };
  assert.ok(m.stormMatch(s, 'al052026'), 'matches by slug');
  assert.ok(m.stormMatch(s, 'fiona'), 'matches by name (case-insensitive)');
  assert.ok(m.stormMatch({ slug: 'x', atcf: 'WP072026' }, 'wp072026'), 'matches by atcf');
  assert.ok(!m.stormMatch(s, 'gaston'), 'no false match');
  assert.ok(!m.stormMatch(null, 'al052026'), 'null tag is safe');
})();

// ---- F2: floater frame time-match selection
(function nearestFrame() {
  const frames = [
    { t: '2026-06-25T20:00:00Z', key: 'a' },
    { t: '2026-06-25T23:45:00Z', key: 'b' },
    { t: '2026-06-26T03:00:00Z', key: 'c' },
  ];
  const pass = Date.parse('2026-06-25T23:40:00Z');
  const nf = m.nearestFrame(frames, pass);
  assert.strictEqual(nf.frame.key, 'b', 'picks the nearest-in-time frame');
  assert.strictEqual(nf.dms, 5 * 60 * 1000, 'reports the 5-min gap');
  assert.strictEqual(m.nearestFrame([], pass).frame, null, 'empty frames -> null');
})();

// ---- F3: drag-bbox extent math (inverse equirectangular projection + normalize)
(function bboxMath() {
  const ext = [-60, -40, 10, 30];   // [w,e,s,n]
  const W = 200, H = 100;
  assert.deepStrictEqual(m.invProjectExt(ext, W, H, 0, 0), [-60, 30], 'top-left = W,N');
  assert.deepStrictEqual(m.invProjectExt(ext, W, H, 200, 100), [-40, 10], 'bottom-right = E,S');
  assert.deepStrictEqual(m.invProjectExt(ext, W, H, 100, 50), [-50, 20], 'centre');
  // a drag from (50,25) to (150,75) -> the inner quarter box, normalized w<e, s<n
  const a = m.invProjectExt(ext, W, H, 50, 25), b = m.invProjectExt(ext, W, H, 150, 75);
  assert.deepStrictEqual(m.rectToBbox(a, b), [-55, -45, 15, 25], 'rect -> [w,e,s,n]');
  // order-independent (drag the other diagonal)
  assert.deepStrictEqual(m.rectToBbox(b, a), [-55, -45, 15, 25], 'normalized regardless of drag dir');
})();

// ---- F4: redirect mapping (/ascat/ -> /satellite/ascat/) preserving OG
(function redirect() {
  const html = fs.readFileSync(path.join(ROOT, 'ascat', 'index.html'), 'utf8');
  assert.ok(/http-equiv="refresh"[^>]*url=\/satellite\/ascat\//.test(html), 'meta-refresh to new path');
  assert.ok(/location\.replace\('\/satellite\/ascat\//.test(html), 'JS redirect to new path');
  assert.ok(/rel="canonical" href="https:\/\/triple-a-tropics\.com\/satellite\/ascat\/"/.test(html), 'canonical -> new path');
  assert.ok(/og:url" content="https:\/\/triple-a-tropics\.com\/satellite\/ascat\/"/.test(html), 'og:url -> new path');
  assert.ok(/og:title/.test(html) && /EUMETSAT/.test(html), 'OG card preserved');
})();

// ---- F4: IA structure (subpages exist, sub-nav present, MW moved off /satellite/)
(function ia() {
  const ascat = fs.readFileSync(path.join(ROOT, 'satellite', 'ascat', 'index.html'), 'utf8');
  const mw = fs.readFileSync(path.join(ROOT, 'satellite', 'microwave', 'index.html'), 'utf8');
  const sat = fs.readFileSync(path.join(ROOT, 'satellite', 'index.html'), 'utf8');
  for (const [name, h] of [['ascat', ascat], ['microwave', mw], ['satellite', sat]]) {
    assert.ok(/class="subnav"/.test(h), name + ' has the sub-nav');
    assert.ok(/\/satellite\/microwave\//.test(h) && /\/satellite\/ascat\//.test(h), name + ' sub-nav links both siblings');
  }
  assert.ok(/id="ascat-viewer"/.test(ascat) && /ascat\/ascat\.js/.test(ascat), 'ascat page mounts the viewer');
  assert.ok(/id="microwave-viewer"/.test(mw) && /microwave\/microwave\.js/.test(mw), 'microwave page mounts the viewer');
  assert.ok(!/id="microwave-viewer"/.test(sat), '/satellite/ no longer holds the MW viewer');
  assert.ok(!/microwave\.js/.test(sat), '/satellite/ no longer loads microwave.js');
  assert.ok(!/<a href="\/ascat\/">ASCAT<\/a>/.test(sat), '/satellite/ top-nav drops the standalone ASCAT link');
})();

console.log('ascat_v2: PASS');
