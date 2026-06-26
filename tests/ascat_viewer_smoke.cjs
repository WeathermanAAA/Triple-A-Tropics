// Smoke test for ascat/ascat.js: the module loads (catches syntax/load
// regressions) and the exported color-scale invariants hold. The interactive
// render (barbs over the basemap, region + storm-lock framing, colorbar, PNG
// export) is verified by a headless jsdom + node-canvas render of a real pass
// against ascat_build/ — see the render proof in the PR.
"use strict";
const assert = require("assert");
const path = require("path");
const m = require(path.join(__dirname, "..", "ascat", "ascat.js"));

assert.ok(typeof m.AscatViewer === "function", "AscatViewer exported");
assert.ok(m.STYLES && m.STYLES.sshws && m.STYLES.highcontrast, "two styles");
assert.ok(Array.isArray(m.KT_SCALE), "KT_SCALE exported");

// The shared TC kt scale: 15 bins, and RED starts at 64 kt (hurricane), matching
// recon. Below 64 must NOT be the canonical red.
assert.strictEqual(m.KT_SCALE.length, 15, "15 kt bins");
const byKt = {};
m.KT_SCALE.forEach(([kt, col]) => { byKt[kt] = col; });
assert.strictEqual(byKt[64], "#f5333c", "64 kt is the canonical TAT red");
assert.strictEqual(byKt[34], "#2fbf52", "34 kt is green (gale)");
assert.ok(m.STYLES.sshws.scale === m.KT_SCALE, "sshws style uses the shared scale");

// Replicate the viewer's bin pick (last bin whose minKt is met) to lock the
// color mapping: a 64 kt cell is red; a 50 kt cell is not the 64-kt red.
function windColor(scale, kt) {
  let col = scale[0][1];
  for (let i = 0; i < scale.length; i++) if (kt >= scale[i][0]) col = scale[i][1];
  return col;
}
assert.strictEqual(windColor(m.KT_SCALE, 64), "#f5333c", "64 kt -> red");
assert.strictEqual(windColor(m.KT_SCALE, 70), "#f5333c", "70 kt -> still C1 red");
assert.notStrictEqual(windColor(m.KT_SCALE, 50), "#f5333c", "50 kt not red");
assert.strictEqual(windColor(m.KT_SCALE, 0), m.KT_SCALE[0][1], "calm -> lowest bin");

console.log("ascat_viewer smoke: PASS");
