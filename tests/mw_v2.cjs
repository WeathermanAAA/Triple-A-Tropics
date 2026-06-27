// Unit tests for the microwave viewer v3 (canvas model): four-product set +
// clean labels, bare-raster tile consumption, WGS84-bounds backdrop draw,
// storm/global mode gating, per-storm nav, and the smoothing toggle. Pure-logic
// + mock-`this` (no browser); interactive behaviour is covered by the headless
// preview screenshots in the PR.
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const ROOT = path.join(__dirname, '..');
const M = require(path.join(ROOT, 'microwave', 'microwave.js'));
const V = M.MicrowaveViewer.prototype;

// ---- four canonical products, concise labels, default 91H
(function products() {
  assert.deepStrictEqual(M.PRODUCTS.map(p => p.key),
    ['color37', 'color91', '37H', '91H'], 'four canonical product keys, in order');
  assert.strictEqual(M.DEFAULT_PRODUCT, '91H');
  M.PRODUCTS.forEach(p => {
    assert.ok(p.label && p.label.length <= 10, 'concise product label: ' + p.label);
    assert.ok(!/·|pass|kt|latest/i.test(p.label), 'no summary blurb: ' + p.label);
  });
})();

// ---- clean dropdown labels (no auto-generated summary tails)
(function labels() {
  const sl = M.stormLabel({ name: 'ERICK', basin: 'EP', year: 2026, overpass_count: 12 });
  assert.ok(/ERICK/.test(sl) && /EP 2026/.test(sl) && !/pass/i.test(sl), 'storm: ' + sl);
  assert.strictEqual(M.stormLabel({ name: 'BERYL' }), 'BERYL');
  const ol = M.overpassLabel({ valid_utc: '2024-09-26T18:25:00Z', sensor: 'GMI',
    platform: 'GPM', intensity_kt: 95, dev_level: 'HU' });
  assert.ok(/GMI GPM/.test(ol) && !/95|kt|HU/.test(ol), 'overpass: ' + ol);
})();

// ---- bare-raster tile accessor: prefer chrome-free tiles[product] over the
// chromed products[product]; null when neither exists.
(function tileRel() {
  assert.deepStrictEqual(M.tileRel({ tiles: { '91H': 's/a_91H_geo.png' } }, '91H'),
    { rel: 's/a_91H_geo.png', bare: true }, 'prefers the bare geo tile');
  assert.deepStrictEqual(M.tileRel({ products: { '91H': 's/a_91H.png' } }, '91H'),
    { rel: 's/a_91H.png', bare: false }, 'falls back to chromed product');
  assert.strictEqual(M.tileRel({ tiles: {} }, '91H'), null, 'null when absent');
  assert.deepStrictEqual(M.boundsOf({ bounds_wgs84: [10, 20, 30, 40] }), [10, 20, 30, 40]);
  assert.strictEqual(M.boundsOf({ bounds_wgs84: [1, 2, 3] }), null, 'bad bounds -> null');
})();

// ---- extent: global = world; storm = overpass cutout (padded), [W,E,S,N]
(function extent() {
  const g = V._extent.call({ mode: 'global' });
  assert.deepStrictEqual(g, [-180, 180, -62, 62], 'global extent = world (no TATRegions)');
  const s = V._extent.call({ mode: 'storm', backdrop: false,
    curOverpass: { bounds_wgs84: [10, 20, 30, 40] } });
  assert.deepStrictEqual(s, [10 - 1.2, 30 + 1.2, 20 - 1.2, 40 + 1.2],
    'storm extent fits the cutout bounds with ~6% pad');
  // FIX: with a backdrop ON, the frame fits EXACTLY to the (aspect-widened)
  // backdrop bounds (no pad) so the imagery fills the frame edge-to-edge.
  const sb = V._extent.call({ mode: 'storm', backdrop: true,
    bdFrame: { bounds: [5, 20, 35, 40] },
    curOverpass: { bounds_wgs84: [10, 20, 30, 40] } });
  assert.deepStrictEqual(sb, [5, 35, 20, 40],
    'storm extent fits the backdrop bounds exactly when backdrop is on');
})();

// ---- backdrop draw: georeferenced by WGS84 corner bounds (W,N)->tl (E,S)->br
(function backdropDraw() {
  const calls = [];
  const g = { save() {}, restore() {}, globalAlpha: 1, drawImage() { calls.push([].slice.call(arguments)); } };
  const proj = (lon, lat) => [(lon - 130) / 15 * 1000, (41 - lat) / 11 * 800];   // [130..145]x[30..41] -> 1000x800
  V._drawBackdrop.call({
    backdrop: true, mode: 'storm', bdOpacity: 0.4,
    bdImg: { complete: true, naturalWidth: 100 }, bdFrame: { bounds: [130, 30, 145, 41] }
  }, g, proj);
  assert.strictEqual(calls.length, 1, 'drawImage called once');
  const [, x, y, w, h] = calls[0];
  // bled 1px outward so a sub-pixel seam never shows at the frame edge (the
  // map-rect clip crops the overflow).
  assert.deepStrictEqual([x, y, w, h], [-1, -1, 1002, 802], 'georef by bounds (tl=W,N; br=E,S), 1px edge bleed');
  // backdrop never draws in global mode
  calls.length = 0;
  V._drawBackdrop.call({ backdrop: true, mode: 'global', bdImg: { complete: true, naturalWidth: 1 }, bdFrame: { bounds: [0, 0, 1, 1] } }, g, proj);
  assert.strictEqual(calls.length, 0, 'no backdrop in global mode');
})();

// ---- mode toggle: Global disables + clears the backdrop (no single cutout)
(function modeGating() {
  let loadedGlobal = false;
  const ctx = { mode: 'storm', backdrop: true, bdImg: {}, bdFrame: {},
    dom: { modeSel: null, bdWrap: { classList: { toggle() {} } }, bdChk: {}, bdOpac: {} },
    _syncStormNav() {}, _loadGlobal() { loadedGlobal = true; }, _draw() {} };
  V._setMode.call(ctx, 'global');
  assert.strictEqual(ctx.mode, 'global');
  assert.strictEqual(ctx.backdrop, false, 'backdrop forced off at Global');
  assert.strictEqual(ctx.dom.bdChk.disabled, true, 'backdrop control disabled at Global');
  assert.ok(loadedGlobal, 'global overpasses loaded');
})();

// ---- smoothing: flips this.raw (canvas imageSmoothing applied in _draw)
(function smoothing() {
  const ctx = { raw: false, dom: { smooth: null }, _draw() {} };
  V._setSmoothing.call(ctx, true); assert.strictEqual(ctx.raw, true, 'raw on');
  V._setSmoothing.call(ctx, false); assert.strictEqual(ctx.raw, false, 'raw off');
})();

// ---- per-storm nav steps the newest-first storms[] by slug
(function stormNav() {
  const picked = [];
  const ctx = { storms: [{ slug: 'a' }, { slug: 'b' }, { slug: 'c' }], curStorm: 'b', mode: 'storm',
    _stormIndex: V._stormIndex, _selectStorm(s) { picked.push(s); } };
  V._stepStorm.call(ctx, 1); assert.strictEqual(picked.pop(), 'c');
  V._stepStorm.call(ctx, -1); assert.strictEqual(picked.pop(), 'a');
  ctx.curStorm = 'a'; V._stepStorm.call(ctx, -1); assert.strictEqual(picked.length, 0, 'no step past newest');
})();

// ---- source contract: canvas model + controls present
(function source() {
  const js = fs.readFileSync(path.join(ROOT, 'microwave', 'microwave.js'), 'utf8');
  assert.ok(/getContext\('2d'\)/.test(js), 'uses a 2D canvas context');
  assert.ok(/imageSmoothingEnabled = !this\.raw/.test(js), 'smoothing -> canvas imageSmoothing on the data tile');
  assert.ok(/bd_product/.test(js), 'reads the producer bd_product (Vis/SWIR), backward-compatible');
  assert.ok(/\.tiles\[/.test(js) || /o\.tiles/.test(js), 'consumes the bare geo tiles');
  const html = fs.readFileSync(path.join(ROOT, 'satellite', 'microwave', 'index.html'), 'utf8');
  assert.ok(/id="mw-canvas"/.test(html), 'page mounts a canvas');
  assert.ok(/id="mw-mode"/.test(html), 'page has the View (storm|global) toggle');
  assert.ok(/id="mw-backdrop"/.test(html), 'page has the Satellite backdrop control');
  assert.ok(/microwave\.js\?v=7/.test(html), 'cache-bust bumped');
})();

console.log('mw_v2: PASS');
