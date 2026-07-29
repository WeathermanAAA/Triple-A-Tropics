// jsdom harness for the Stage 2 Ensemble Toolkit (models/enscenters.js):
// data-style toggle (Cheerios <-> Lines), ensemble-mean overlay + plume, lazy +
// graceful tracks loading, dateline-safe mean track, localStorage persistence, and
// the Cheerios-byte-identity guard (toolkit OFF must not call any track drawer).
//
//   node enscenters_toolkit_smoke.cjs <enscenters.js>
"use strict";
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const [, , JS] = process.argv;
const REGIONS = path.join(path.dirname(JS), "regions.js");
const CYC = "2026061418";
const STEPS = [0, 24, 48, 72, 96];

function centers(lat, lon) { return STEPS.map((s) => [s, lat, lon, 1000 - s * 0.3, 20 + s * 0.3]); }
function cycleDoc(model) {
  return {
    schema_version: 1, model: model, model_label: model.toUpperCase(),
    init_time: "2026-06-14T18:00:00Z", init_cycle: CYC, cycle_hour: 18,
    generated_at: "2026-06-15T00:00:00Z", attribution: "test", grid: "0.25 deg",
    run_steps: STEPS, n_members: 3, n_centers: 15,
    detect: { closed_threshold_hpa: 2.0 },
    center_fields: ["step_h", "lat", "lon", "mslp_hpa", "vmax_kt"],
    pressure_bins: [
      { key: "gt1000", label: ">1000 hPa", lo: 1000, hi: null },
      { key: "p990_1000", label: "990 to 1000 hPa", lo: 990, hi: 1000 },
      { key: "p970_990", label: "970 to 990 hPa", lo: 970, hi: 990 },
      { key: "p950_970", label: "950 to 970 hPa", lo: 950, hi: 970 },
      { key: "p930_950", label: "930 to 950 hPa", lo: 930, hi: 950 },
      { key: "p910_930", label: "910 to 930 hPa", lo: 910, hi: 930 },
      { key: "lt910", label: "<910 hPa", lo: null, hi: 910 },
    ],
    members: [
      { id: "CTL", label: "Control", peak: { mslp_hpa: 970, vmax_kt: 60, lat: -20, lon: 178, step_h: 96 }, n_centers: 5, centers: centers(-20, 178) },
      { id: "P01", label: "P01", peak: { mslp_hpa: 980, vmax_kt: 50, lat: -22, lon: -179, step_h: 96 }, n_centers: 5, centers: centers(-22, -179) },
      { id: "P02", label: "P02", peak: { mslp_hpa: 985, vmax_kt: 45, lat: -19, lon: 176, step_h: 96 }, n_centers: 5, centers: centers(-19, 176) },
    ],
  };
}
// A dateline-straddling S. Pacific cluster: mean lons UNWRAPPED continuous 170->222.
const MEAN_DL = STEPS.map((s, i) => [s, -15 - i * 4, 170 + i * 13, 1000 - s * 0.4, 20 + s * 0.45]);
function plume() {
  const L = STEPS;
  return {
    vmax: { lead: L, p10: L.map((s) => 20 + s * 0.2), p25: L.map((s) => 22 + s * 0.25), p50: L.map((s) => 25 + s * 0.35), p75: L.map((s) => 28 + s * 0.45), p90: L.map((s) => 32 + s * 0.55), min: L.map((s) => 18 + s * 0.1), max: L.map((s) => 38 + s * 0.6), n: L.map(() => 18) },
    mslp: { lead: L, p10: L.map((s) => 1004 - s * 0.5), p25: L.map((s) => 1002 - s * 0.45), p50: L.map((s) => 1000 - s * 0.4), p75: L.map((s) => 998 - s * 0.35), p90: L.map((s) => 996 - s * 0.3), min: L.map((s) => 1006 - s * 0.55), max: L.map((s) => 994 - s * 0.25), n: L.map(() => 18) },
  };
}
function memberTrack(lon0) { return STEPS.map((s, i) => [s, -15 - i * 4, lon0 + i * 13, 1000 - s * 0.4, 20 + s * 0.45]); }
const tracksDoc = {
  schema_version: 1, model: "ecens", init_cycle: CYC, generated_at: "2026-06-15T05:00:00Z",
  source_kind: "self_detect", spacing_h: 24, n_members: 3, n_member_tracks: 3, n_clusters: 3,
  members: [
    { id: "CTL", tracks: [memberTrack(170)] },
    { id: "P01", tracks: [memberTrack(172)] },
    { id: "P02", tracks: [memberTrack(168)] },
  ],
  clusters: [
    { id: 0, members: ["CTL", "P01", "P02"], member_count: 20, coverage_fraction: 0.9, population: 22, low_confidence: false,
      genesis: { lat: -15, lon: 170, step: 0 }, mean_track: MEAN_DL, plume: plume(),
      envelope: STEPS.map((s) => ({ step: s, n: 18, mean_lat: -15, mean_lon: 170, ell50: { a_km: 100, b_km: 60, bearing_deg: 30, poly: [] }, ell90: { a_km: 200, b_km: 120, bearing_deg: 30, poly: [] } })) },
    { id: 1, members: ["X"], member_count: 5, coverage_fraction: 0.2, population: 5, low_confidence: true,
      genesis: { lat: -10, lon: 175, step: 0 }, mean_track: memberTrack(175), plume: plume(), envelope: [] },
    { id: 2, members: ["Y"], member_count: 2, coverage_fraction: 0.1, population: 2, low_confidence: true,
      genesis: { lat: -12, lon: 165, step: 0 }, mean_track: memberTrack(165), plume: plume(), envelope: [] },
  ],
};

const manifest = {
  schema_version: 1, generated_at: "2026-06-15T05:00:00Z", default_model: "ecens",
  models: [
    { slug: "ecens", label: "ECMWF ENS", cycles: [CYC], latest: CYC,
      cycle_versions: { [CYC]: "cv" }, tracks_versions: { [CYC]: "tv-ecens" } },
    { slug: "noend", label: "No Tracks", cycles: [CYC], latest: CYC, cycle_versions: { [CYC]: "cv" } },
    { slug: "failm", label: "Fail Tracks", cycles: [CYC], latest: CYC,
      cycle_versions: { [CYC]: "cv" }, tracks_versions: { [CYC]: "tv-fail" } },
  ],
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
    <button id="enscenters-step-fwd"></button>
    <button id="enscenters-trail"></button>
    <button id="enscenters-style" style="display:none"></button>
    <button id="enscenters-mean" style="display:none"></button>
    <span id="enscenters-fhour"></span><span id="enscenters-valid"></span>
    <select id="enscenters-speed"></select><select id="enscenters-run"></select>
  </div>
  <input id="enscenters-scrub" class="ens-scrub" type="range" min="0" max="0" value="0">
  <p id="enscenters-caption" class="ens-caption" data-default="x"></p>
  <div id="enscenters-empty" style="display:none"></div>
</div></body></html>`;

const dom = new JSDOM(HTML, { runScripts: "outside-only", url: "https://triple-a-tropics.com/models/" });
const win = dom.window;
const fake2d = new Proxy({}, {
  get(_t, k) {
    if (k === "canvas") return { width: 0, height: 0 };
    if (k === "measureText") return (s) => ({ width: String(s == null ? "" : s).length * 6 });
    return typeof k === "string" ? () => {} : undefined;
  }, set() { return true; },
});
win.HTMLCanvasElement.prototype.getContext = function () { return fake2d; };
win.requestAnimationFrame = function () { return 0; };
win.cancelAnimationFrame = function () {};
win.ResizeObserver = function () { this.observe = function () {}; };
win.devicePixelRatio = 1;
try { win.localStorage.clear(); } catch (e) {}
const EMPTY_GEO = { type: "FeatureCollection", features: [] };
win.fetch = function (url) {
  let body, ok = true;
  if (/manifest\.json/.test(url)) body = manifest;
  else if (/\.geojson/.test(url)) body = EMPTY_GEO;
  else if (/failm\/.*\.tracks\.json/.test(url)) { ok = false; body = {}; }   // tracks fetch fails
  else if (/ecens\/.*\.tracks\.json/.test(url)) body = tracksDoc;
  else if (/\/(ecens|noend|failm)\/.*\.json/.test(url)) body = cycleDoc(/noend/.test(url) ? "noend" : /failm/.test(url) ? "failm" : "ecens");
  else body = {};
  return Promise.resolve({ ok: ok, status: ok ? 200 : 404, json: () => Promise.resolve(body) });
};
// The shared SSHWS palette, as the real page loads it (ordered before
// the viewer). enscenters.js holds no fallback copy by design.
win.eval(fs.readFileSync(path.join(__dirname, "..", "tat_palette.js"), "utf8"));
win.eval(fs.readFileSync(REGIONS, "utf8"));
win.eval(fs.readFileSync(JS, "utf8"));
const flush = () => new Promise((r) => setTimeout(r, 0));
const vis = (b) => b && b.style.display !== "none";

(async () => {
  const out = {};
  const root = win.document.getElementById("enscenters-viewer");
  const V = new win.EnsCentersViewer(root);
  for (let i = 0; i < 8; i++) await flush();

  // a dateline region so the S. Pacific cluster is in view
  V._selectRegion("global");
  for (let i = 0; i < 4; i++) await flush();

  // instrument draw call counts + capture mean-track projected x jumps
  const calls = { lines: 0, mean: 0, step: 0, plume: 0 };
  const o = { lines: V._drawLines, mean: V._drawMean, step: V._drawStep, plume: V._drawPlumeInset, mt: V._drawMeanTrack };
  V._drawLines = function () { calls.lines++; return o.lines.apply(this, arguments); };
  V._drawMean = function () { calls.mean++; return o.mean.apply(this, arguments); };
  V._drawStep = function () { calls.step++; return o.step.apply(this, arguments); };
  V._drawPlumeInset = function () { calls.plume++; return o.plume.apply(this, arguments); };
  let maxJump = 0, jumpLimit = 0;
  V._drawMeanTrack = function (g, c, uptoStep, ext, mw, mh, JUMP, dim) {
    if (c && c.genesis && Math.abs(c.genesis.lon - 170) < 2) {     // the dateline cluster
      jumpLimit = JUMP;
      let prev = null;
      const mt = c.mean_track || [];
      for (let k = 0; k < mt.length; k++) {
        if (mt[k][0] > uptoStep) break;
        const p = win.TATRegions.project(((mt[k][2] + 180) % 360 + 360) % 360 - 180, mt[k][1], ext, mw, mh);
        if (prev) maxJump = Math.max(maxJump, Math.abs(p[0] - prev[0]));
        prev = p;
      }
    }
    return o.mt.apply(this, arguments);
  };

  // --- toolkit OFF (default Cheerios, mean off): no track drawer may run ---
  calls.lines = calls.mean = calls.step = calls.plume = 0;
  V._show(V.idx);
  out.off = Object.assign({}, calls);
  out.ecens_style_visible = vis(V.dom.style);
  out.ecens_mean_visible = vis(V.dom.mean);

  // --- Lines mode: lazily loads tracks, draws lines, animates with F-hour ---
  V._setDataStyle("lines");
  for (let i = 0; i < 6; i++) await flush();
  calls.lines = calls.step = 0;
  V._show(2); V._show(4);
  out.lines_after = { lines: calls.lines, step: calls.step };
  out.lines_tracksReady = V.tracksReady();
  out.ls_style = win.localStorage.getItem("ens.style");

  // --- Mean on: mean track + plume drawn; dateline continuity captured ---
  V._setMean(true);
  for (let i = 0; i < 4; i++) await flush();
  calls.mean = calls.plume = 0; maxJump = 0;
  V._show(STEPS.length - 1);     // last frame: full mean track
  out.mean_after = { mean: calls.mean, plume: calls.plume };
  out.ls_mean = win.localStorage.getItem("ens.mean");
  out.dateline_maxJump = maxJump;
  out.dateline_jumpLimit = jumpLimit;

  // --- persistence across reload: a fresh viewer reads ens.style/ens.mean ---
  const V2 = new win.EnsCentersViewer(root);
  out.persist_style = V2.dataStyle;
  out.persist_mean = V2.meanOn;
  for (let i = 0; i < 8; i++) await flush();

  // --- model with NO tracks: toggles hide, Cheerios only, no error ---
  let threw = false;
  try {
    V._selectModel("noend");
    for (let i = 0; i < 8; i++) await flush();
    calls.lines = calls.mean = calls.step = 0;
    V._show(V.idx);
  } catch (e) { threw = true; out.noend_err = String(e); }
  out.noend_style_visible = vis(V.dom.style);
  out.noend_mean_visible = vis(V.dom.mean);
  out.noend_after = { lines: calls.lines, mean: calls.mean, step: calls.step };
  out.noend_threw = threw;

  // --- model whose tracks.json FAILS to load: toggles hide, no error ---
  let threw2 = false;
  try {
    V._selectModel("failm");
    for (let i = 0; i < 10; i++) await flush();   // _ensureTracks fetch fails (lines still on)
  } catch (e) { threw2 = true; out.failm_err = String(e); }
  out.failm_style_visible = vis(V.dom.style);
  out.failm_mean_visible = vis(V.dom.mean);
  out.failm_threw = threw2;

  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { process.stderr.write(String((e && e.stack) || e)); process.exit(1); });
