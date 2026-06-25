// Smoke test for cyclolab_map.js: the module loads (catches syntax/load
// regressions in the polish pass) and the exported pure helpers behave. The
// interactive behaviour (scrubber ticks, glyph spin, MW hold, tools, draw) is
// verified in a real browser (puppeteer) on a live storm — see the polish PR.
"use strict";
const assert = require("assert");
const path = require("path");
const m = require(path.join(__dirname, "..", "cyclolab_map.js"));

assert.ok(typeof m.CycloLabMap === "function", "CycloLabMap exported");
assert.ok(typeof m.stormToGeoJSON === "function", "stormToGeoJSON exported");
assert.ok(typeof m.natureFlags === "function", "natureFlags exported");

// stormToGeoJSON builds a track LineString + one observation Point per fix,
// sliced to the playhead index.
const storm = { name: "TEST", points: [
  { lat: 12, lon: -40, wind_kt: 30, nature: "TS", t: "2009-08-15T06:00:00" },
  { lat: 14, lon: -42, wind_kt: 65, nature: "TS", t: "2009-08-15T18:00:00" },
  { lat: 17, lon: -45, wind_kt: 110, nature: "TS", t: "2009-08-16T06:00:00" },
] };
const fc = m.stormToGeoJSON(storm, 2);
assert.strictEqual(fc.type, "FeatureCollection");
const pts = fc.features.filter((f) => f.geometry.type === "Point");
const lines = fc.features.filter((f) => f.geometry.type === "LineString");
assert.strictEqual(pts.length, 3, "one observation point per fix");
assert.ok(lines.length >= 1, "a track line");

// Sliced to the playhead: only fixes at/before the index are included.
const fc1 = m.stormToGeoJSON(storm, 1);
assert.strictEqual(fc1.features.filter((f) => f.geometry.type === "Point").length, 2);

// natureFlags: TS tropical, SS subtropical, EX non-tropical.
assert.deepStrictEqual(m.natureFlags("TS"), { sub: false, non: false });
assert.deepStrictEqual(m.natureFlags("SS"), { sub: true, non: false });
assert.deepStrictEqual(m.natureFlags("EX"), { sub: false, non: true });

console.log("cyclolab_map smoke: PASS");
