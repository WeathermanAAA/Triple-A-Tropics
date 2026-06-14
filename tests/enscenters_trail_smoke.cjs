// jsdom regression harness for the stale-trail bug (models/enscenters.js).
//
// jsdom has no real canvas, so we cannot read pixels. Instead we instrument the
// trail layer's 2D context: clearRect() resets a "steps drawn since the last
// clear" set, and we wrap _drawStep to record every (non-filled) step drawn onto
// THAT layer. After the repro sequence the set is the faithful proxy for "which
// step rings are currently on the trail bitmap".
//
// Repro (the confirmed bug): accumulate the trail to a late step, toggle Trail
// OFF, move to an EARLIER step, toggle Trail ON. The trail must then hold ONLY
// steps 0..(idx-1). Pre-fix, _setTrailMode set trailUpTo=-1 WITHOUT clearing the
// bitmap and _ensureTrail did not clear on a from-scratch (-1) rebuild, so the
// late-step rings stayed underneath -> the set would still contain steps > idx-1.
//
//   node enscenters_trail_smoke.cjs <enscenters.js>
"use strict";

const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const [, , JS] = process.argv;
const REGIONS = path.join(path.dirname(JS), "regions.js");

// 6 steps, each with an Atlantic center for BOTH members, so every trail step
// actually draws rings (no early-return) and the recorded set == bitmap content.
const STEPS = [0, 6, 12, 18, 24, 30];
function centers(lat, lon) { return STEPS.map((st) => [st, lat, lon, 1000 - st, 20 + st]); }
const cycle = {
  schema_version: 1, model: "ecens", model_label: "ECMWF ENS",
  init_time: "2026-06-13T00:00:00Z", init_cycle: "2026061300", cycle_hour: 0,
  generated_at: "2026-06-13T08:41:00Z", attribution: "ECMWF open data",
  grid: "0.25 deg", run_steps: STEPS, n_members: 2, n_centers: 12,
  detect: { closed_threshold_hpa: 2.0 },
  center_fields: ["step_h", "lat", "lon", "mslp_hpa", "vmax_kt"],
  pressure_bins: [
    { key: "gt1000", label: ">1000 hPa", lo: 1000, hi: null },
    { key: "p990_1000", label: "990 to 1000 hPa", lo: 990, hi: 1000 },
    { key: "p970_990", label: "970 to 990 hPa", lo: 970, hi: 990 },
    { key: "p950_970", label: "950 to 970 hPa", lo: 950, hi: 970 },
    { key: "lt950", label: "<950 hPa", lo: null, hi: 950 },
  ],
  members: [
    { id: "CTL", label: "Control", peak: { mslp_hpa: 970, vmax_kt: 50, lat: 20, lon: -60, step_h: 30 }, n_centers: 6, centers: centers(20, -60) },
    { id: "P01", label: "Perturbed 01", peak: { mslp_hpa: 985, vmax_kt: 40, lat: 22, lon: -55, step_h: 30 }, n_centers: 6, centers: centers(22, -55) },
  ],
};
const manifest = {
  schema_version: 1, generated_at: "2026-06-13T08:41:00Z", default_model: "ecens",
  models: [{ slug: "ecens", label: "ECMWF ENS", cycles: ["2026061300"], latest: "2026061300" }],
};

const HTML = `<!doctype html><html><body>
<div id="enscenters-viewer" tabindex="0">
  <div id="enscenters-mapframe">
    <canvas id="enscenters-canvas" width="900" height="560"></canvas>
    <div id="enscenters-tooltip"></div>
    <div id="enscenters-status" style="display:none"><span></span></div>
  </div>
  <div class="ens-controlbar">
    <button id="enscenters-region-btn"><span id="enscenters-region-label"></span></button>
    <div class="ens-modelgroup"><div id="enscenters-models" class="hafs-seg-group"></div></div>
    <button id="enscenters-step-back"></button><button id="enscenters-play"></button>
    <button id="enscenters-step-fwd"></button><button id="enscenters-trail"></button>
    <span id="enscenters-fhour"></span><span id="enscenters-valid"></span>
    <select id="enscenters-speed"></select><select id="enscenters-run"></select>
  </div>
  <input id="enscenters-scrub" class="ens-scrub" type="range" min="0" max="0" value="0">
  <p class="ens-caption"></p>
  <div id="enscenters-empty" style="display:none"></div>
</div></body></html>`;

const dom = new JSDOM(HTML, { runScripts: "outside-only", url: "https://triple-a-tropics.com/models/" });
const win = dom.window;
const fake2d = new Proxy({}, {
  get(_t, k) {
    if (k === "canvas") return { width: 0, height: 0 };
    if (k === "measureText") return (s) => ({ width: String(s == null ? "" : s).length * 6 });
    return typeof k === "string" ? () => {} : undefined;
  },
  set() { return true; },
});
win.HTMLCanvasElement.prototype.getContext = function () { return fake2d; };
win.requestAnimationFrame = function () { return 0; };
win.cancelAnimationFrame = function () {};
win.ResizeObserver = function () { this.observe = function () {}; };
win.devicePixelRatio = 1;
try { win.localStorage.clear(); } catch (e) {}
const EMPTY_GEO = { type: "FeatureCollection", features: [] };
win.fetch = function (url) {
  let body;
  if (/manifest\.json/.test(url)) body = manifest;
  else if (/\.geojson/.test(url)) body = EMPTY_GEO;
  else if (/\.json/.test(url)) body = cycle;
  else body = {};
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
};
win.eval(fs.readFileSync(REGIONS, "utf8"));
win.eval(fs.readFileSync(JS, "utf8"));
const flush = () => new Promise((r) => setTimeout(r, 0));

(async () => {
  const V = new win.EnsCentersViewer(win.document.getElementById("enscenters-viewer"));
  for (let i = 0; i < 6; i++) await flush();

  // Instrument the trail layer: its own ctx whose clearRect resets the "drawn
  // since last clear" set; record every non-filled _drawStep onto that ctx.
  const drawn = new Set();
  const trailCtx = new Proxy({}, {
    get(_t, k) {
      if (k === "clearRect") return () => { drawn.clear(); };
      if (k === "canvas") return { width: 0, height: 0 };
      if (k === "measureText") return (s) => ({ width: String(s == null ? "" : s).length * 6 });
      return typeof k === "string" ? () => {} : undefined;
    },
    set() { return true; },
  });
  V.trailLayer.getContext = function () { return trailCtx; };
  const origDraw = V._drawStep;
  V._drawStep = function (g, s, filled) {
    if (g === trailCtx && !filled) drawn.add(s);
    return origDraw.call(this, g, s, filled);
  };

  // Repro: trail ON, accumulate to the LAST step (rings for steps 0..4 land on
  // the layer), toggle OFF, move to an EARLIER step (idx=2), toggle back ON.
  V._setTrailMode("trail");
  V._show(STEPS.length - 1);                 // idx=5 -> trail steps 0..4
  const afterAccum = [...drawn].sort((a, b) => a - b);
  V._setTrailMode("current");                // OFF
  V._show(2);                                // move to an earlier step (idx=2)
  V._setTrailMode("trail");                  // ON again at idx=2 -> trail steps 0..1

  const trailDrawnSteps = [...drawn].sort((a, b) => a - b);
  process.stdout.write(JSON.stringify({
    steps: STEPS,
    idx: V.idx,
    afterAccumSteps: afterAccum,            // sanity: the trail DID accumulate first
    trailDrawnSteps,                         // must be exactly the run-step values for 0..1
  }));
  process.exit(0);
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
