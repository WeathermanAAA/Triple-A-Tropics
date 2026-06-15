// jsdom harness for ITEM 1: the BURNED-IN canvas header must carry the current
// forecast hour + valid time, per frame (so copied stills / GIF frames are self-
// documenting). jsdom has no real canvas, so we instrument the main context's
// fillText to capture the header string drawn by _drawHeader at two different
// steps and assert F-hour + valid time are present and INCREMENT per frame.
//
//   node enscenters_header_smoke.cjs <enscenters.js>
"use strict";
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const [, , JS] = process.argv;
const REGIONS = path.join(path.dirname(JS), "regions.js");

const STEPS = [0, 24, 72, 120];
const cycle = {
  schema_version: 1, model: "fnv3", model_label: "FNV3 (50)",
  init_time: "2026-06-14T12:00:00Z", init_cycle: "2026061412", cycle_hour: 12,
  generated_at: "2026-06-14T18:00:00Z", attribution: "Google DeepMind Weather Lab (FNV3)",
  grid: "native TC tracks", run_steps: STEPS, n_members: 50, n_centers: 8,
  caption: "Experimental model output, not for real-world use.",
  center_fields: ["step_h", "lat", "lon", "mslp_hpa", "vmax_kt"],
  pressure_bins: [{ key: "gt1000", label: ">1000 hPa", lo: 1000, hi: null }],
  members: [
    { id: "M00", label: "Member 0", peak: { mslp_hpa: 980, vmax_kt: 60, lat: 20, lon: -60, step_h: 72 },
      n_centers: 4, centers: STEPS.map((s, i) => [s, 20 + i, -60 - i, 1000 - i, 30 + i]) },
    { id: "M01", label: "Member 1", peak: { mslp_hpa: 990, vmax_kt: 45, lat: 22, lon: -55, step_h: 24 },
      n_centers: 4, centers: STEPS.map((s, i) => [s, 22 + i, -55 - i, 1002 - i, 28 + i]) },
  ],
};
const manifest = { schema_version: 1, default_model: "fnv3",
  models: [{ slug: "fnv3", label: "FNV3 (50)", cycles: ["2026061412"], latest: "2026061412" }] };

const HTML = `<!doctype html><html><body>
<div id="enscenters-viewer" tabindex="0"><div id="enscenters-mapframe">
<canvas id="enscenters-canvas" width="900" height="560"></canvas>
<div id="enscenters-tooltip"></div><div id="enscenters-status" style="display:none"><span></span></div></div>
<div class="ens-controlbar"><button id="enscenters-region-btn"><span id="enscenters-region-label"></span></button>
<div class="ens-modelgroup"><div id="enscenters-models" class="hafs-seg-group"></div></div>
<button id="enscenters-step-back"></button><button id="enscenters-play"></button>
<button id="enscenters-step-fwd"></button><button id="enscenters-trail"></button>
<span id="enscenters-fhour"></span><span id="enscenters-valid"></span>
<select id="enscenters-speed"></select><select id="enscenters-run"></select></div>
<input id="enscenters-scrub" class="ens-scrub" type="range" min="0" max="0" value="0">
<p class="ens-caption"></p><div id="enscenters-empty" style="display:none"></div></div></body></html>`;

const dom = new JSDOM(HTML, { runScripts: "outside-only", url: "https://triple-a-tropics.com/models/" });
const win = dom.window;
const fills = [];
const fake2d = new Proxy({}, {
  get(_t, k) {
    if (k === "canvas") return { width: 0, height: 0 };
    if (k === "measureText") return (s) => ({ width: String(s == null ? "" : s).length * 6 });
    if (k === "fillText") return (s) => { fills.push(String(s)); };
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
win.fetch = function (url) {
  let body;
  if (/manifest\.json/.test(url)) body = manifest;
  else if (/\.geojson/.test(url)) body = { type: "FeatureCollection", features: [] };
  else if (/\.json/.test(url)) body = cycle;
  else body = {};
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
};
win.eval(fs.readFileSync(REGIONS, "utf8"));
win.eval(fs.readFileSync(JS, "utf8"));
const flush = () => new Promise((r) => setTimeout(r, 0));

function headerLine() {
  // the per-frame header line carrying init/F-hour/valid (drawn by _drawHeader)
  return fills.filter((s) => /init .*valid/.test(s)).slice(-1)[0] || null;
}

(async () => {
  const V = new win.EnsCentersViewer(win.document.getElementById("enscenters-viewer"));
  for (let i = 0; i < 6; i++) await flush();
  fills.length = 0; V._show(1); const h1 = headerLine();     // step index 1 -> F024
  fills.length = 0; V._show(3); const h2 = headerLine();     // step index 3 -> F120
  process.stdout.write(JSON.stringify({ header_at_idx1: h1, header_at_idx3: h2 }));
  process.exit(0);
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
