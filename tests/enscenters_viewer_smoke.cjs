// jsdom LOGIC smoke for the Ensemble Cyclone Centers viewer + shared region layer.
//
// The canvas is now the full figure; its PIXELS are verified in a real browser
// (Playwright). jsdom does no real canvas/layout, so this guards only the LOGIC:
// the viewer constructs, hydrates, region-filters, computes per-member peaks,
// steps the transport, and toggles the trail mode. Canvas 2D calls hit a no-op
// stub. Prints a JSON state probe for tests/test_enscenters_viewer.py.
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
  <div id="enscenters-mapframe">
    <canvas id="enscenters-canvas" width="900" height="560"></canvas>
    <div id="enscenters-tooltip"></div>
    <div id="enscenters-status" style="display:none"><span></span></div>
  </div>
  <div class="ens-controlbar">
    <button id="enscenters-region-btn"><span id="enscenters-region-label"></span></button>
    <div class="ens-modelgroup"><div id="enscenters-models" class="hafs-seg-group"></div></div>
    <button id="enscenters-step-back"></button>
    <button id="enscenters-play"></button>
    <button id="enscenters-step-fwd"></button>
    <button id="enscenters-trail"></button>
    <span id="enscenters-fhour"></span><span id="enscenters-valid"></span>
    <select id="enscenters-speed"></select>
    <select id="enscenters-run"></select>
  </div>
  <input id="enscenters-scrub" class="ens-scrub" type="range" min="0" max="0" value="0">
  <p class="ens-caption"></p>
  <div id="enscenters-empty" style="display:none"></div>
</div></body></html>`;

const dom = new JSDOM(HTML, { runScripts: "outside-only",
  url: "https://triple-a-tropics.com/models/" });
const win = dom.window;

const fake2d = new Proxy({}, {
  get(_t, k) {
    if (k === "canvas") return { width: 0, height: 0 };
    // real 2d contexts always have measureText -> TextMetrics (the peak-table
    // CTL chip measures its label); the no-op default would yield undefined.
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
const R = win.TATRegions;

(async () => {
  const V = new win.EnsCentersViewer(win.document.getElementById("enscenters-viewer"));
  for (let i = 0; i < 6; i++) await flush();

  const before = V.idx; V._step(1); const afterStep = V.idx;
  V._show(2);
  const fhour2 = win.document.getElementById("enscenters-fhour").textContent;

  const peaksHasCTL = V.peaks.some((p) => p.id === "CTL");
  const peaksSorted = V.peaks.length < 2 ||
    V.peaks.every((p, i) => i === 0 || V.peaks[i - 1].mslp <= p.mslp);

  // trail toggle
  const trailDefault = V.trailMode;
  V._setTrailMode("current");
  const trailAfter = V.trailMode;
  V._setTrailMode("trail");

  // region switch
  const defaultRegion = V.region;
  const regionLabelText = win.document.getElementById("enscenters-region-label").textContent;
  if (V.picker) V.picker.open();
  const pickerCards = V.picker ? V.picker.overlay.querySelectorAll(".tatreg-card").length : -1;
  if (V.picker) V.picker.close();
  V._selectRegion("global");
  const globalExtent = V.extent.slice();
  V._selectRegion("wpac");
  V._show(0);
  const wpac = R.get("wpac");
  let allVisibleInWpac = true;
  for (const c of V.visible) if (!R.inRegion(c[1], c[0], wpac)) { allVisibleInWpac = false; break; }
  let lastRegion = null; try { lastRegion = win.localStorage.getItem("ens.region"); } catch (e) {}

  // Run (cycle) selector: built from the manifest's per-model cycle list.
  const runSel = win.document.getElementById("enscenters-run");
  const runOptions = runSel ? Array.from(runSel.options).map((o) => o.textContent) : [];
  const runValue = runSel ? runSel.value : null;

  process.stdout.write(JSON.stringify({
    runOptionCount: runOptions.length,
    runValue,
    runFirstLabel: runOptions[0] || null,
    runSecondLabel: runOptions[1] || null,
    regionFramesLen: V.regionFrames.length,
    runStepsLen: (V.data && V.data.run_steps || []).length,
    nCenters: V.data && V.data.n_centers,
    nMembers: V.data && V.data.n_members,
    peaksLen: V.peaks.length, peaksHasCTL, peaksSorted,
    idxBefore: before, idxAfterStep: afterStep, fhourAfterShow2: fhour2,
    trailDefault, trailAfter,
    defaultRegion, regionLabelText,
    controlbarHidden: win.document.querySelector(".ens-controlbar").style.display === "none",
    modelgroupHidden: win.document.querySelector(".ens-modelgroup").style.display === "none",
    pickerCardCount: pickerCards,
    globalExtent, wpacExtent: V.extent.slice(), allVisibleInWpac,
    lastRegionSaved: lastRegion,
  }));
  process.exit(0);
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
