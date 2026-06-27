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

// ---- PART 3: clean satellite backdrop (storm-centered, georef by WGS84 bounds)
(function backdropExtentClip() {
  const ext = m.AscatViewer.prototype._extent;
  // storm-centered, backdrop OFF -> the +/-8 deg box around the storm center
  const off = ext.call({ zoomExt: null, backdrop: false, stormLock: 'wp082026',
                         center: { lat: 34, lon: 137.5 } });
  assert.deepStrictEqual(off, [129.5, 145.5, 26, 42], 'backdrop off -> +/-8 storm box');
  // backdrop ON with a loaded raster + bounds -> the frame IS the cutout (barbs
  // clip to it: one coherent frame). bounds=[W,S,E,N]; _extent returns [W,E,S,N].
  const on = ext.call({ zoomExt: null, backdrop: true, bdImg: { naturalWidth: 1 },
                        bdFrame: { bounds: [130, 30, 145, 41] },
                        stormLock: 'wp082026', center: { lat: 34, lon: 137.5 } });
  assert.deepStrictEqual(on, [130, 145, 30, 41], 'backdrop on -> view fits the cutout bounds');
})();

(function backdropGeorefByBounds() {
  const calls = [];
  const g = { save() {}, restore() {}, globalAlpha: 1,
              drawImage() { calls.push([].slice.call(arguments)); } };
  // proj maps lon/lat -> x,y linearly over [130..145]x[30..41] into a 1000x800 rect
  const proj = (lon, lat) => [(lon - 130) / 15 * 1000, (41 - lat) / 11 * 800];
  m.AscatViewer.prototype._drawBackdrop.call({
    backdrop: true, bdOpacity: 0.4,
    bdImg: { complete: true, naturalWidth: 100 },
    bdFrame: { bounds: [130, 30, 145, 41] },
  }, g, proj);
  assert.strictEqual(calls.length, 1, 'drawImage called once');
  const [, x, y, w, h] = calls[0];
  // tl=proj(130,41)=[0,0]; br=proj(145,30)=[1000,800], bled 1px outward so a
  // sub-pixel seam never shows at the frame edge (map-rect clip crops it).
  assert.deepStrictEqual([x, y, w, h], [-1, -1, 1002, 802], 'georef by WGS84 corner bounds, 1px edge bleed');
})();

(function backdropSourceContract() {
  const src = fs.readFileSync(path.join(ROOT, 'ascat', 'ascat.js'), 'utf8');
  assert.ok(/id="ascat-bd-opacity"[^>]*value="40"/.test(src), 'backdrop opacity defaults to 40% (dimmed so barbs read)');
  assert.ok(/this\.bdOpacity = 0\.4/.test(src), 'bdOpacity initial = 0.4');
  assert.ok(/checkbox" id="ascat-backdrop"> Vis \/ SWIR/.test(src), 'backdrop control labeled Vis / SWIR (day Vis / night SWIR), NOT "Clean IR"');
  assert.ok(!/Clean IR/.test(src), 'no stale "Clean IR" label remains');
  assert.ok(/function backdropFrame/.test(src), 'uses latest-widened-frame picker so the storm backdrop fills the plot');
  assert.ok(/best\.bd_key/.test(src), 'consumes the clean chrome-free bd_key raster');
  assert.ok(!/img\.src = root \+ '\/' \+ best\.key;/.test(src), 'never draws the chromed floater frame under the barbs');
  assert.ok(/ascat-disabled/.test(src), 'backdrop control greys out in composite mode');
})();

// ---- dropdown cleanup: the Pass dropdown drops the appended storm-name blurb,
// but the View/storm dropdown KEEPS its concise "(N passes)" qualifier.
(function dropdownLabels() {
  const src = fs.readFileSync(path.join(ROOT, 'ascat', 'ascat.js'), 'utf8');
  assert.ok(!/var stormTag = /.test(src), 'pass dropdown no longer appends a storm-name summary');
  assert.ok(/o\.textContent = p\.sensor \+ '  ·  ' \+ fmtZ\(p\.start_utc\);/.test(src),
    'pass label is sensor + time only');
  assert.ok(/pass' \+ \(s\.n === 1 \? '' : 'es'\) \+ '\)'/.test(src),
    'storm dropdown keeps the concise (N passes) qualifier');
})();

// ---- Global view + backdrop available in storm+basin (Vis/SWIR revision)
(function globalMode() {
  const isG = m.AscatViewer.prototype._isGlobal;
  assert.strictEqual(isG.call({ stormLock: null, region: 'global' }), true, 'global region + no lock -> global');
  assert.strictEqual(isG.call({ stormLock: null, region: 'atlantic' }), false, 'basin -> not global');
  assert.strictEqual(isG.call({ stormLock: 'wp082026', region: 'global' }), false, 'storm lock -> not global');
})();

(function viewPassesCap() {
  const proto = m.AscatViewer.prototype;
  const passes = Array.from({ length: 30 }, (_, i) => ({ id: 'p' + i }));
  const basin = proto._viewPasses.call({ passMeta: passes, selectedId: 'all', stormLock: null, region: 'atlantic', _isGlobal: proto._isGlobal });
  assert.strictEqual(basin.length, 6, 'basin composite caps at MAX_PASSES=6');
  const global = proto._viewPasses.call({ passMeta: passes, selectedId: 'all', stormLock: null, region: 'global', _isGlobal: proto._isGlobal });
  assert.ok(global.length > 6, 'Global lifts the pass cap (whole-world composite)');
})();

(function drawSwathsFills() {
  const calls = [];
  const g = { save() {}, restore() {}, fillRect() { calls.push([].slice.call(arguments)); },
              set fillStyle(v) {}, get fillStyle() { return ''; } };
  const proj = (lon, lat) => [(lon + 180) / 360 * 1000, (90 - lat) / 180 * 500];
  const pass = { sensor: 'ASCAT-B', start_utc: '2026-06-27T00:00:00Z',
                 wvc: { la: [10, 25], lo: [150, 160], kt: [20, 45], dir: [90, 90] } };
  m.AscatViewer.prototype._drawSwaths.call(
    { _loadedView: () => [pass], _windColor: () => '#abc', _cells: null },
    g, proj, [0, 360, -88, 88], 1000, 500);
  assert.ok(calls.length >= 2, 'Global draws filled colored cells per WVC (got ' + calls.length + ')');
})();

(function extentBackdropGating() {
  const ext = m.AscatViewer.prototype._extent;
  const storm = ext.call({ zoomExt: null, stormLock: 'x', backdrop: true, bdImg: {}, bdFrame: { bounds: [130, 30, 145, 41] }, center: { lat: 34, lon: 137.5 } });
  assert.deepStrictEqual(storm, [130, 145, 30, 41], 'storm view collapses to the backdrop cutout');
  // basin: stub `window` (the TATRegions branch references it) so node doesn't
  // ReferenceError; with no TATRegions it falls to the Atlantic fallback, proving
  // the cutout-collapse is gated to storm mode only.
  global.window = {};
  try {
    const basin = ext.call({ zoomExt: null, stormLock: null, backdrop: true, bdImg: {}, bdFrame: { bounds: [130, 30, 145, 41] }, center: null, region: 'atlantic' });
    assert.deepStrictEqual(basin, [-100, -5, 0, 55], 'basin view keeps the region extent (no cutout collapse)');
  } finally { delete global.window; }
})();

(function visswirSourceContract() {
  const src = fs.readFileSync(path.join(ROOT, 'ascat', 'ascat.js'), 'utf8');
  assert.ok(/Global composite/.test(src) && /_drawSwaths/.test(src), 'global filled-swath renderer present');
  assert.ok(/GLOBAL_MAX_PASSES/.test(src), 'global pass cap present');
  assert.ok(/_loadRegionBackdrop/.test(src), 'basin/regional backdrop loader present');
  assert.ok(/best\.bd_product/.test(src), 'header reads the true backdrop product (Vis/SWIR)');
  assert.ok(/classList\.toggle\('ascat-disabled', isWide\)/.test(src), 'backdrop toggle greyed for wide-area (hemisphere/global), not basin/regional');
  assert.ok(/\['nhem', 'shem', 'global'\]\.indexOf\(this\.region\)/.test(src), 'hemisphere+global are the wide-area no-single-disk set');
})();

console.log('ascat_v2: PASS');
