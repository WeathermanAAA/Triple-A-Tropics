// jsdom smoke harness for the Ensemble Cyclone Centers viewer (models/enscenters.js).
//
// Loads enscenters.js under jsdom behind stubbed canvas/fetch/rAF, hydrates it
// from a real manifest + cycle JSON, and prints a JSON state probe so
// tests/test_enscenters_viewer.py can assert the data wiring + transport offline
// (canvas is not installed, so the 2D context is a no-op stub).
//
//   node enscenters_viewer_smoke.cjs <enscenters.js> <manifest.json> <cycle.json>
"use strict";

const fs = require("fs");
const { JSDOM } = require("jsdom");

const [, , JS, MANIFEST, CYCLE] = process.argv;
const manifest = JSON.parse(fs.readFileSync(MANIFEST, "utf8"));
const cycle = JSON.parse(fs.readFileSync(CYCLE, "utf8"));

const HTML = `<!doctype html><html><body>
<div id="enscenters-viewer" tabindex="0">
  <div id="enscenters-stage">
    <canvas id="enscenters-canvas" width="900" height="450"></canvas>
    <div id="enscenters-status" style="display:none"><span></span></div>
    <div id="enscenters-tooltip"></div>
  </div>
  <div>
    <div id="enscenters-controls"><div class="hafs-group"><div id="enscenters-models" class="hafs-seg-group"></div></div></div>
    <div id="enscenters-player">
      <button id="enscenters-step-back"></button>
      <button id="enscenters-play"></button>
      <button id="enscenters-step-fwd"></button>
      <span id="enscenters-fhour"></span><span id="enscenters-valid"></span>
      <select id="enscenters-speed"></select>
    </div>
    <div id="enscenters-legend"></div>
    <div id="enscenters-peaks"></div>
  </div>
  <input id="enscenters-scrub" type="range" min="0" max="0" value="0">
  <p id="enscenters-subtitle"></p>
  <div id="enscenters-empty" style="display:none"></div>
</div></body></html>`;

const dom = new JSDOM(HTML, { runScripts: "outside-only" });
const win = dom.window;

// ---- stubs ----
const fake2d = new Proxy({}, {
  get(_t, k) {
    if (k === "canvas") return { width: 0, height: 0 };
    return typeof k === "string" ? () => {} : undefined;
  },
  set() { return true; },
});
win.HTMLCanvasElement.prototype.getContext = function () { return fake2d; };
win.requestAnimationFrame = function () { return 0; };  // no auto-loop in test
win.cancelAnimationFrame = function () {};
win.ResizeObserver = function () { this.observe = function () {}; };
win.devicePixelRatio = 1;

const EMPTY_GEO = { type: "FeatureCollection", features: [] };
win.fetch = function (url) {
  let body;
  if (/manifest\.json/.test(url)) body = manifest;
  else if (/\.geojson/.test(url)) body = EMPTY_GEO;
  else if (/\.json/.test(url)) body = cycle;
  else body = {};
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
};

// ---- load the viewer ----
win.eval(fs.readFileSync(JS, "utf8"));

const flush = () => new Promise((r) => setTimeout(r, 0));

(async () => {
  const root = win.document.getElementById("enscenters-viewer");
  const V = new win.EnsCentersViewer(root);
  for (let i = 0; i < 6; i++) await flush();   // let basemap+manifest+cycle resolve

  const peaksHtml = win.document.getElementById("enscenters-peaks").innerHTML;
  const modelBtns = win.document.getElementById("enscenters-models").querySelectorAll("button").length;
  const scrub = win.document.getElementById("enscenters-scrub");
  const legend = win.document.getElementById("enscenters-legend").querySelectorAll(".ens-leg").length;

  const before = V.idx;
  V._step(1);
  const afterStep = V.idx;
  V._show(2);
  const fhour2 = win.document.getElementById("enscenters-fhour").textContent;
  const lastIdx = V.steps.length - 1;
  scrub.value = String(lastIdx);
  scrub.dispatchEvent(new win.Event("input"));
  const afterScrub = V.idx;

  process.stdout.write(JSON.stringify({
    framesLen: V.frames.length,
    stepsLen: V.steps.length,
    runStepsLen: (V.data && V.data.run_steps || []).length,
    nCenters: V.data && V.data.n_centers,
    nMembers: V.data && V.data.n_members,
    firstFrameCenters: V.frames[0] ? V.frames[0].length : -1,
    peaksHasCTL: /\bCTL\b/.test(peaksHtml),
    peakRows: (peaksHtml.match(/ens-peak-row/g) || []).length,
    modelBtns,
    legendRows: legend,
    scrubMax: Number(scrub.max),
    idxBefore: before,
    idxAfterStep: afterStep,
    fhourAfterShow2: fhour2,
    idxAfterScrub: afterScrub,
    subtitle: win.document.getElementById("enscenters-subtitle").textContent,
  }));
  // The viewer schedules a 5-min poll timer (production behavior) that keeps
  // jsdom's event loop alive, so exit explicitly once the probe is written.
  process.exit(0);
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
