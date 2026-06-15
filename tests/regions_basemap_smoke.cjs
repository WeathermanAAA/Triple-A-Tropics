// Geo-trace regression for the shared basemap (models/regions.js _traceGeo).
// Drives drawBasemapFill with a recording 2D context + synthetic polygons and
// reports the FILL path structure, so we can assert the antimeridian fix:
//   - a contiguous landmass near the seam fills as ONE continuous subpath with NO
//     chord (no path segment jumping more than half the map width),
//   - a ring that genuinely straddles the seam splits into TWO closed subpolygons
//     (one per edge), still chord-free,
//   - an ordinary in-window polygon is one clean closed subpath.
// The OLD per-point-unwrap + JUMP-closePath code chord-closed across the interior
// (triangular land wedges); this guards against that returning.
//   node regions_basemap_smoke.cjs <regions.js>
"use strict";
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const REGIONS = process.argv[2] || path.join(__dirname, "..", "models", "regions.js");
const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only", url: "https://triple-a-tropics.com/" });
const win = dom.window;
win.eval(fs.readFileSync(REGIONS, "utf8"));
const TR = win.TATRegions;
const W = 800, H = 400;

// recording 2D context: capture path ops, no-op everything else
function rec() {
  const ops = [];
  return new Proxy({}, {
    get(_t, k) {
      if (k === "moveTo") return (x, y) => ops.push(["move", x, y]);
      if (k === "lineTo") return (x, y) => ops.push(["line", x, y]);
      if (k === "closePath") return () => ops.push(["close"]);
      if (k === "beginPath") return () => ops.push(["begin"]);
      if (k === "fill") return () => ops.push(["fill"]);
      if (k === "measureText") return () => ({ width: 0 });
      if (k === "canvas") return { width: W, height: H };
      if (k === "_ops") return ops;
      return typeof k === "string" ? () => {} : undefined;
    },
    set() { return true; },
  });
}
// the land fill path = ops between the last beginPath and the following fill
function landPath(ops) {
  for (let i = ops.length - 1; i >= 0; i--) {
    if (ops[i][0] === "fill") {
      for (let j = i - 1; j >= 0; j--) if (ops[j][0] === "begin") return ops.slice(j + 1, i);
    }
  }
  return [];
}
function analyze(seg) {
  let moves = 0, closes = 0, maxJump = 0, px = null;
  for (const o of seg) {
    if (o[0] === "move") { moves++; px = o[1]; }
    else if (o[0] === "line") { if (px != null) maxJump = Math.max(maxJump, Math.abs(o[1] - px)); px = o[1]; }
    else if (o[0] === "close") { closes++; px = null; }
  }
  return { moves, closes, maxJump: Math.round(maxJump) };
}
function poly(coords) {
  return { type: "FeatureCollection", features: [{ type: "Feature", geometry: { type: "Polygon", coordinates: [coords] } }] };
}
function fillWith(featColl, ext) { const g = rec(); TR.drawBasemapFill(g, ext, { countries: featColl }, W, H, { land: "#222" }); return analyze(landPath(g._ops)); }

const out = { W: W, halfW: W * 0.5 };
// 1) Australia-like landmass spanning the unwrap boundary of a Pacific extent
//    ([120,280]); raw lons 113..150 (113 < ext[0]). Must NOT chord.
out.wedge = fillWith(poly([[113, -12], [150, -12], [150, -30], [113, -30], [113, -12]]), [120, 280, -40, 5]);
// 2) ring straddling the antimeridian in a [-180,180] extent -> two closed subpolys
out.seam = fillWith(poly([[170, 10], [-175, 10], [-175, -10], [170, -10], [170, 10]]), [-180, 180, -30, 30]);
// 3) ordinary in-window polygon -> one clean closed subpath
out.plain = fillWith(poly([[-60, 10], [-40, 10], [-40, -10], [-60, -10], [-60, 10]]), [-100, -5, -30, 30]);
process.stdout.write(JSON.stringify(out));
