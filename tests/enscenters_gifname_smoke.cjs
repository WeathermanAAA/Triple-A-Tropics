// jsdom harness for the two small fixes:
//   FIX 1 - the exported GIF filename starts with the ACTIVE model slug (was a
//           hardcoded "ecens" for every model).
//   FIX 2 - the selector labels read "Google FNV3 (50)" / "Google GenCast".
// Drives the REAL _makeGif download line per model (with a stubbed GIF encoder
// that fires 'finished' immediately) and captures the anchor's download name.
//
//   node enscenters_gifname_smoke.cjs <enscenters.js>
"use strict";
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const [, , JS] = process.argv;
const REGIONS = path.join(path.dirname(JS), "regions.js");

const SLUGS = ["ecens", "ecaie", "gefs", "fnv3", "genc"];
const LABELS = { ecens: "ECMWF ENS", ecaie: "AIFS-ENS", gefs: "GEFS",
                 fnv3: "Google FNV3 (50)", genc: "Google GenCast" };
const manifest = { schema_version: 1, default_model: "ecens",
  models: SLUGS.map((s) => ({ slug: s, label: LABELS[s], cycles: ["2026061412"], latest: "2026061412" })) };
function cycleFor(slug) {
  const STEPS = [0, 6, 12, 18];
  return { schema_version: 1, model: slug, model_label: LABELS[slug],
    init_time: "2026-06-14T12:00:00Z", init_cycle: "2026061412", cycle_hour: 12,
    generated_at: "2026-06-14T18:00:00Z", attribution: "x", grid: "g",
    run_steps: STEPS, n_members: 2, n_centers: 4,
    center_fields: ["step_h", "lat", "lon", "mslp_hpa", "vmax_kt"],
    pressure_bins: [{ key: "gt1000", label: ">1000 hPa", lo: 1000, hi: null }],
    members: [{ id: "M0", label: "M0", peak: { mslp_hpa: 990, vmax_kt: 40, lat: 20, lon: -60, step_h: 6 },
                n_centers: 4, centers: STEPS.map((s, i) => [s, 20 + i, -60 - i, 1000 - i, 30 + i]) }] };
}

const GIFHTML = `<button id="enscenters-gif"></button><div id="enscenters-gifmodal"></div>
<select id="enscenters-gifstart"></select><select id="enscenters-gifend"></select>
<input id="enscenters-giffps" value="10">
<input id="enscenters-gifskip" value="0"><button id="enscenters-gifmake"></button>
<div id="enscenters-gifstatus"></div><button id="enscenters-gifx"></button>`;
const HTML = `<!doctype html><html><body>
<div id="enscenters-viewer" tabindex="0"><div id="enscenters-mapframe">
<canvas id="enscenters-canvas" width="900" height="560"></canvas>
<div id="enscenters-tooltip"></div><div id="enscenters-status" style="display:none"><span></span></div></div>
<div class="ens-controlbar"><button id="enscenters-region-btn"><span id="enscenters-region-label"></span></button>
<div class="ens-modelgroup"><div id="enscenters-models" class="hafs-seg-group"></div></div>
<button id="enscenters-step-back"></button><button id="enscenters-play"></button>
<button id="enscenters-step-fwd"></button><button id="enscenters-trail"></button>
<span id="enscenters-fhour"></span><span id="enscenters-valid"></span>
<select id="enscenters-speed"></select><select id="enscenters-run"></select>${GIFHTML}</div>
<input id="enscenters-scrub" class="ens-scrub" type="range" min="0" max="0" value="0">
<p class="ens-caption"></p><div id="enscenters-empty" style="display:none"></div></div></body></html>`;

const dom = new JSDOM(HTML, { runScripts: "outside-only", url: "https://triple-a-tropics.com/models/" });
const win = dom.window;
const fake2d = new Proxy({}, { get(_t, k) {
  if (k === "canvas") return { width: 0, height: 0 };
  if (k === "measureText") return (s) => ({ width: String(s == null ? "" : s).length * 6 });
  return typeof k === "string" ? () => {} : undefined;
}, set() { return true; } });
win.HTMLCanvasElement.prototype.getContext = function () { return fake2d; };
win.requestAnimationFrame = function (cb) { return 0; };
win.cancelAnimationFrame = function () {};
win.ResizeObserver = function () { this.observe = function () {}; };
win.devicePixelRatio = 1;
try { win.localStorage.clear(); } catch (e) {}
win.URL.createObjectURL = function () { return "blob:x"; };
win.URL.revokeObjectURL = function () {};
// stub GIF encoder: render() fires 'finished' synchronously with a fake blob
win.GIF = function () {
  this._h = {}; this.on = function (ev, fn) { this._h[ev] = fn; };
  this.addFrame = function () {}; this.render = function () { if (this._h.finished) this._h.finished({}); };
};
// capture the download filename from the real _makeGif anchor
let lastDownload = null;
const origClick = win.HTMLAnchorElement.prototype.click;
win.HTMLAnchorElement.prototype.click = function () { if (this.download) lastDownload = this.download; };

let curSlug = "ecens";
win.fetch = function (url) {
  let body;
  if (/manifest\.json/.test(url)) body = manifest;
  else if (/\.geojson/.test(url)) body = { type: "FeatureCollection", features: [] };
  else {
    const m = /enscenters\/([^/]+)\/\d+\.json/.exec(url);
    body = cycleFor(m ? m[1] : curSlug);
  }
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
};
// The shared SSHWS palette, as the real page loads it (ordered before
// the viewer). enscenters.js holds no fallback copy by design.
win.eval(fs.readFileSync(path.join(__dirname, "..", "tat_palette.js"), "utf8"));
win.eval(fs.readFileSync(REGIONS, "utf8"));
win.eval(fs.readFileSync(JS, "utf8"));
const flush = () => new Promise((r) => setTimeout(r, 0));

(async () => {
  const V = new win.EnsCentersViewer(win.document.getElementById("enscenters-viewer"));
  for (let i = 0; i < 6; i++) await flush();
  V._ensureGifWorker = function (cb) { cb("w"); };   // skip the CDN worker fetch
  const chips = [...win.document.querySelectorAll("#enscenters-models button")].map((e) => e.textContent.trim());
  const names = {};
  for (const slug of SLUGS) {
    curSlug = slug;
    V._selectModel(slug);
    for (let i = 0; i < 4; i++) await flush();
    V._openGif();              // populate the Start/End hour selects (full range)
    lastDownload = null;
    V._makeGif();
    for (let i = 0; i < 2; i++) await flush();
    names[slug] = lastDownload;
  }
  process.stdout.write(JSON.stringify({ chips, names }));
  process.exit(0);
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
