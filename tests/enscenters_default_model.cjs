// jsdom LOGIC test for the enscenters viewer's default-model selection (open on
// the FRESHEST model, not the hard-default laggard). Tests _freshestModel
// (pure) for mixed-freshness / all-equal / no-cycle, AND the full-viewer load +
// user-selection stickiness. Prints a JSON probe for the Python test.
//
//   node enscenters_default_model.cjs <enscenters.js>
"use strict";
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
const JS = process.argv[2];
const REGIONS = path.join(path.dirname(JS), "regions.js");

const HTML = `<!doctype html><html><body>
<div id="enscenters-viewer" tabindex="0"><div id="enscenters-mapframe">
<canvas id="enscenters-canvas" width="900" height="560"></canvas>
<div id="enscenters-tooltip"></div>
<div id="enscenters-status" style="display:none"><span></span></div></div>
<div class="ens-controlbar">
<button id="enscenters-region-btn"><span id="enscenters-region-label"></span></button>
<div class="ens-modelgroup"><div id="enscenters-models" class="hafs-seg-group"></div></div>
<button id="enscenters-step-back"></button><button id="enscenters-play"></button>
<button id="enscenters-step-fwd"></button><button id="enscenters-trail"></button>
<span id="enscenters-fhour"></span><span id="enscenters-valid"></span>
<select id="enscenters-speed"></select><select id="enscenters-run"></select></div>
<input id="enscenters-scrub" class="ens-scrub" type="range" min="0" max="0" value="0">
<p class="ens-caption"></p></div></body></html>`;

const dom = new JSDOM(HTML, { runScripts: "outside-only",
  url: "https://triple-a-tropics.com/models/" });
const win = dom.window;
const fake2d = new Proxy({}, { get(_t, k) {
  if (k === "canvas") return { width: 0, height: 0 };
  if (k === "measureText") return (s) => ({ width: String(s == null ? "" : s).length * 6 });
  return typeof k === "string" ? () => {} : undefined; }, set() { return true; } });
win.HTMLCanvasElement.prototype.getContext = function () { return fake2d; };
win.requestAnimationFrame = function () { return 0; };
win.cancelAnimationFrame = function () {};
win.ResizeObserver = function () { this.observe = function () {}; };
win.devicePixelRatio = 1;
try { win.localStorage.clear(); } catch (e) {}

// Mixed-freshness manifest: ecens (the default_model) is one cycle BEHIND aifs.
const CYCLE = { schema_version: 1, model: "x", init_cycle: "2026061212",
  cycle_hour: 12, run_steps: [0, 24], n_members: 1, n_centers: 1,
  center_fields: ["step_h", "lat", "lon", "mslp_hpa", "vmax_kt"],
  pressure_bins: [{ key: "lt950", label: "<950", lo: null, hi: 950 }],
  members: [{ id: "CTL", label: "Control",
    peak: { mslp_hpa: 960, vmax_kt: 80, lat: 20, lon: -60, step_h: 24 },
    n_centers: 1, centers: [[24, 20, -60, 960, 80]] }] };
const MIXED = { schema_version: 1, default_model: "ecens", models: [
  { slug: "ecens", label: "ECMWF ENS", cycles: ["2026061206", "2026061118"], latest: "2026061206" },
  { slug: "aifs", label: "AIFS-ENS", cycles: ["2026061212", "2026061206"], latest: "2026061212" },
  { slug: "gefs", label: "GEFS", cycles: ["2026061212"], latest: "2026061212" }] };
const EMPTY_GEO = { type: "FeatureCollection", features: [] };
win.fetch = function (url) {
  let body = {};
  if (/manifest\.json/.test(url)) body = MIXED;
  else if (/\.geojson/.test(url)) body = EMPTY_GEO;
  else if (/\.json/.test(url)) body = CYCLE;
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
};

win.eval(fs.readFileSync(REGIONS, "utf8"));
win.eval(fs.readFileSync(JS, "utf8"));
const Proto = win.EnsCentersViewer.prototype;

function freshest(defaultModel, models) {
  return Proto._freshestModel.call({ manifest: { default_model: defaultModel } }, models);
}
const flush = () => new Promise((r) => setTimeout(r, 0));

(async () => {
  const out = {};
  // --- _freshestModel pure-logic cases -----------------------------------
  out.mixed = freshest("ecens", [
    { slug: "ecens", latest: "2026061206" }, { slug: "aifs", latest: "2026061212" }]);
  out.allEqualPrefersDefault = freshest("ecens", [
    { slug: "aifs", latest: "2026061212" }, { slug: "ecens", latest: "2026061212" },
    { slug: "gefs", latest: "2026061212" }]);
  out.tieNoDefaultKeepsOrder = freshest("zzz", [
    { slug: "gefs", latest: "2026061212" }, { slug: "aifs", latest: "2026061212" }]);
  out.noCyclesFallsBackToDefault = freshest("ecens", [
    { slug: "aifs", latest: null }, { slug: "ecens", latest: null }]);
  out.defaultBehindStillBeatsLaggard = freshest("ecens", [
    { slug: "ecens", latest: "2026061206" }, { slug: "fnv3", latest: "2026061212" },
    { slug: "gencast", latest: "2026061212" }]);

  // --- full viewer: load opens on the freshest, user click is sticky ------
  const V = new win.EnsCentersViewer(win.document.getElementById("enscenters-viewer"));
  for (let i = 0; i < 8; i++) await flush();
  out.loadSelectedModel = V.model;                  // should be aifs (freshest)
  V._selectModel("ecens");                           // simulate a user click
  for (let i = 0; i < 4; i++) await flush();
  out.afterUserClick = V.model;                      // ecens
  await V._poll();                                   // a poll must NOT re-select
  for (let i = 0; i < 4; i++) await flush();
  out.afterPoll = V.model;                           // still ecens (sticky)
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
