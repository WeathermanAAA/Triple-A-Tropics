// Unit tests for the microwave viewer v2: four-product set + clean labels,
// per-storm nav, client-side smoothing toggle, and the per-product GIF/MP4
// frame-URL builder. Pure-logic + mock-`this` (no browser); the interactive
// behaviour is covered by the headless preview screenshots in the PR.
'use strict';
const assert = require('assert');
const path = require('path');
const ROOT = path.join(__dirname, '..');
const M = require(path.join(ROOT, 'microwave', 'microwave.js'));
const V = M.MicrowaveViewer.prototype;

// ---- four canonical products, concise labels, default 91H
(function products() {
  assert.deepStrictEqual(M.PRODUCTS.map(p => p.key),
    ['color37', 'color91', '37H', '91H'], 'four canonical product keys, in order');
  assert.strictEqual(M.DEFAULT_PRODUCT, '91H', 'default product = 91H (scattering view)');
  M.PRODUCTS.forEach(p => {
    assert.ok(p.label && p.label.length <= 10, 'concise product label: ' + p.label);
    assert.ok(!/·|pass|kt|latest/i.test(p.label), 'no summary blurb in product label: ' + p.label);
  });
})();

// ---- storm dropdown label: name + basin/year, NO "N passes" summary tail
(function stormLabel() {
  const lab = M.stormLabel({ name: 'ERICK', basin: 'EP', year: 2026, overpass_count: 12 });
  assert.ok(/ERICK/.test(lab) && /EP 2026/.test(lab), 'keeps name + basin/year: ' + lab);
  assert.ok(!/pass/i.test(lab), 'drops the "N passes" summary blurb: ' + lab);
  assert.strictEqual(M.stormLabel({ name: 'BERYL' }), 'BERYL', 'bare name when no basin/year');
})();

// ---- overpass dropdown label: time + sensor, NO "{kt} {dev}" intensity tail
(function overpassLabel() {
  const lab = M.overpassLabel({ valid_utc: '2024-09-26T18:25:00Z', sensor: 'GMI',
    platform: 'GPM', intensity_kt: 95, dev_level: 'HU' });
  assert.ok(/GMI GPM/.test(lab), 'keeps sensor/platform: ' + lab);
  assert.ok(!/95|kt|HU/.test(lab), 'drops the "{kt} {dev}" intensity blurb: ' + lab);
})();

// ---- per-product GIF/MP4 frame-URL builder: in order, only passes with product
(function frameUrls() {
  const ctx = { base: 'https://cdn.x/microwave', overpasses: [
    { products: { '91H': 's/a_91H.png', color37: 's/a_c37.png' } },
    { products: { color37: 's/b_c37.png' } },                       // no 91H -> skipped
    { products: { '91H': 's/c_91H.png' } },
  ] };
  assert.deepStrictEqual(V._frameUrlsForProduct.call(ctx, '91H'),
    ['https://cdn.x/microwave/s/a_91H.png', 'https://cdn.x/microwave/s/c_91H.png'],
    'collects current-product frames in order, skipping passes without it');
  assert.deepStrictEqual(V._frameUrlsForProduct.call(ctx, 'color37').length, 2);
})();

// ---- smoothing toggle: client-side CSS image-rendering (no double-render)
(function smoothing() {
  const ctx = { raw: false, dom: { smooth: null, img: { style: {} } } };
  V._setSmoothing.call(ctx, true);
  assert.strictEqual(ctx.raw, true);
  assert.strictEqual(ctx.dom.img.style.imageRendering, 'pixelated', 'raw -> pixelated');
  V._setSmoothing.call(ctx, false);
  assert.strictEqual(ctx.dom.img.style.imageRendering, '', 'smoothed -> auto ("")');
})();

// ---- per-storm nav: steps the newest-first storms[] and selects by slug
(function stormNav() {
  const picked = [];
  const ctx = { storms: [{ slug: 'a' }, { slug: 'b' }, { slug: 'c' }], curStorm: 'b',
    _stormIndex: V._stormIndex, _selectStorm: function (s) { picked.push(s); } };
  V._stepStorm.call(ctx, 1); assert.strictEqual(picked.pop(), 'c', 'next -> older');
  V._stepStorm.call(ctx, -1); assert.strictEqual(picked.pop(), 'a', 'prev -> newer');
  ctx.curStorm = 'a'; V._stepStorm.call(ctx, -1);
  assert.strictEqual(picked.length, 0, 'no step past the newest end');
})();

console.log('mw_v2: PASS');
