// jsdom smoke harness for the Ensemble Cyclone Centers viewer (models/enscenters.js)
// plus the shared region layer (models/regions.js).
//
// Loads both under jsdom behind stubbed canvas/fetch/rAF, hydrates from a real
// manifest + cycle JSON, and prints a JSON state probe so
// tests/test_enscenters_viewer.py can assert the data wiring, transport, AND the
// region crop (default region, region switch -> extent + peak-table + scatter
// filter, dateline-wrap) offline (canvas is not installed -> 2D context stub).
//
//   node enscenters_viewer_smoke.cjs <enscenters.js> <manifest.json> <cycle.json>
"use strict";

const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const [, , JS, MANIFEST, CYCLE] = process.argv;
const REGIONS = path.join(path.dirname(JS), "regions.js");
const manifest = JSON.parse(fs.readFileSync(MANIFEST, "utf8"));
const cycle = JSON.parse(fs.readFileSync(CYCLE, "utf8"));

const HTML = `<!doctype html><html><body>
<div id="enscenters-viewer" tabindex="0">
  <div id="enscenters-stage">
    <canvas id="enscenters-canvas" width="900" height="450"></canvas>
    <div id="enscenters-status" style="display:none"><span></span></div>
    <div id="enscenters-tooltip"></div>
  </div>
  <div class="vw-aside">
    <div id="enscenters-controls">
      <div class="hafs-group"><button id="enscenters-region-btn"><span id="enscenters-region-label"></span></button></div>
      <div class="hafs-group"><div id="enscenters-models" class="hafs-seg-group"></div></div>
    </div>
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
  <div class="vw-below"><input id="enscenters-scrub" type="range" min="0" max="0" value="0"></div>
  <p id="enscenters-subtitle"></p>
  <div id="enscenters-empty" style="display:none"></div>
</div></body></html>`;

const dom = new JSDOM(HTML, { runScripts: "outside-only",
  url: "https://triple-a-tropics.com/models/" });   // non-opaque origin -> localStorage works
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
win.requestAnimationFrame = function () { return 0; };
win.cancelAnimationFrame = function () {};
win.ResizeObserver = function () { this.observe = function () {}; };
win.devicePixelRatio = 1;
try { win.localStorage.clear(); } catch (e) { /* ignore */ }

const EMPTY_GEO = { type: "FeatureCollection", features: [] };
win.fetch = function (url) {
  let body;
  if (/manifest\.json/.test(url)) body = manifest;
  else if (/\.geojson/.test(url)) body = EMPTY_GEO;
  else if (/\.json/.test(url)) body = cycle;
  else body = {};
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
};

// ---- load the shared region layer THEN the viewer ----
win.eval(fs.readFileSync(REGIONS, "utf8"));
win.eval(fs.readFileSync(JS, "utf8"));

const flush = () => new Promise((r) => setTimeout(r, 0));
const R = win.TATRegions;

(async () => {
  const root = win.document.getElementById("enscenters-viewer");
  const V = new win.EnsCentersViewer(root);
  for (let i = 0; i < 6; i++) await flush();   // basemap+manifest+cycle resolve

  const peaksHtml = win.document.getElementById("enscenters-peaks").innerHTML;
  const scrub = win.document.getElementById("enscenters-scrub");

  const before = V.idx; V._step(1); const afterStep = V.idx;
  V._show(2);
  const fhour2 = win.document.getElementById("enscenters-fhour").textContent;
  const lastIdx = V.steps.length - 1;
  scrub.value = String(lastIdx); scrub.dispatchEvent(new win.Event("input"));
  const afterScrub = V.idx;

  // ---- region behavior ----
  const defaultRegion = V.region;
  const regionLabelText = win.document.getElementById("enscenters-region-label").textContent;
  if (V.picker) V.picker.open();   // exercises the thumbnail render path
  // count THIS viewer's own picker cards (the DOMContentLoaded auto-boot may
  // create a second viewer in the harness; the real page only ever has one).
  const pickerCards = V.picker ? V.picker.overlay.querySelectorAll(".tatreg-card").length : -1;
  const pickerOpen = V.picker ? (V.picker.overlay.style.display === "flex") : false;
  if (V.picker) V.picker.close();

  // switch to Global (Pacific-centered full extent), then West Pacific.
  V._selectRegion("global");
  const globalExtent = V.extent.slice();
  const globalPeaksHasTitle = /Peak in Global/.test(win.document.getElementById("enscenters-peaks").innerHTML);

  V._selectRegion("wpac");
  V._show(0);
  const wpac = R.get("wpac");
  let allVisibleInWpac = true;
  for (const c of V.visible) { if (!R.inRegion(c[1], c[0], wpac)) { allVisibleInWpac = false; break; } }
  const wpacExtent = V.extent.slice();
  let lastRegion = null;
  try { lastRegion = win.localStorage.getItem("ens.region"); } catch (e) { /* */ }

  process.stdout.write(JSON.stringify({
    framesLen: V.frames.length,
    runStepsLen: (V.data && V.data.run_steps || []).length,
    nCenters: V.data && V.data.n_centers,
    nMembers: V.data && V.data.n_members,
    peaksHasCTL: /\bCTL\b/.test(peaksHtml),
    scrubMax: Number(scrub.max),
    idxBefore: before, idxAfterStep: afterStep,
    fhourAfterShow2: fhour2, idxAfterScrub: afterScrub,
    subtitle: win.document.getElementById("enscenters-subtitle").textContent,
    // region probe
    defaultRegion, regionLabelText,
    pickerCardCount: pickerCards, pickerOpened: pickerOpen,
    globalExtent, globalPeaksHasTitle,
    wpacExtent, allVisibleInWpac, wpacVisibleCount: V.visible.length,
    lastRegionSaved: lastRegion,
  }));
  process.exit(0);
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
