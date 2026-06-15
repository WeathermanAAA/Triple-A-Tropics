// jsdom harness for the GIF size/quality PRESET (Full / Balanced / Discord).
// gif.js itself only runs in a browser worker, so we mock the ONE encode pass
// (_gifRun) with a controllable fake blob size and drive the REAL _makeGif
// orchestrator. This proves, deterministically:
//   * the export WIDTH cap per preset (full=1600, discord=900, both <= canvas cw),
//   * Discord AUTO-TRIMS frame count to land under the ~9.5 MB budget,
//   * the trim never goes below the GIF_FLOOR_FRAMES floor,
//   * the result size readout ("Saved - X.X MB") matches the delivered blob,
//   * the over-10 MB warning fires for BOTH a too-big Full export and a Discord
//     export that can't fit even at the floor.
// Color fidelity (quality:1, no dither) lives in _gifRun, which is unchanged and
// not what a preset touches - the preset only moves width + frame count.
//
//   node enscenters_gifsize_smoke.cjs <enscenters.js>
"use strict";
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const [, , JS] = process.argv;
const REGIONS = path.join(path.dirname(JS), "regions.js");
const MB = 1048576;

const GIFHTML = `<button id="enscenters-gif"></button><div id="enscenters-gifmodal"></div>
<select id="enscenters-gifpreset"><option value="full">Full</option>
<option value="balanced">Balanced</option><option value="discord">Discord</option></select>
<input id="enscenters-gifn" value="22"><input id="enscenters-giffps" value="10">
<input id="enscenters-gifskip" value="0"><button id="enscenters-gifmake"></button>
<div id="enscenters-gifstatus"></div><button id="enscenters-gifx"></button>`;
const HTML = `<!doctype html><html><body>
<div id="enscenters-viewer" tabindex="0"><div id="enscenters-mapframe">
<canvas id="enscenters-canvas" width="1800" height="900"></canvas>
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
win.requestAnimationFrame = function (cb) { cb(0); return 0; };
win.cancelAnimationFrame = function () {};
win.ResizeObserver = function () { this.observe = function () {}; };
win.devicePixelRatio = 1;
try { win.localStorage.clear(); } catch (e) {}
win.URL.createObjectURL = function () { return "blob:x"; };
win.URL.revokeObjectURL = function () {};
win.GIF = function () { this.on = function () {}; this.addFrame = function () {}; this.render = function () {}; };
let lastDownload = null;
win.HTMLAnchorElement.prototype.click = function () { if (this.download) lastDownload = this.download; };
win.fetch = function () { return Promise.resolve({ ok: true, status: 200,
  json: () => Promise.resolve({ type: "FeatureCollection", features: [] }) }); };
win.eval(fs.readFileSync(REGIONS, "utf8"));
win.eval(fs.readFileSync(JS, "utf8"));
const flush = () => new Promise((r) => setTimeout(r, 0));

(async () => {
  const V = new win.EnsCentersViewer(win.document.getElementById("enscenters-viewer"));
  for (let i = 0; i < 6; i++) await flush();

  // Pin the viewer state _makeGif reads (the model load is irrelevant - _gifRun is
  // mocked, so only steps.length / canvas width / model+region+cycle matter).
  V.model = "fnv3"; V.region = "wpac"; V.data = { init_cycle: "2026061412" };
  V.dom.canvas.width = 1800; V.dom.canvas.height = 900;     // source bigger than every preset cap
  V._pause = function () {};

  // Record each encode pass and return a fake blob whose size follows the scenario's
  // model (bytes per frame * frames). Discord's auto-fit re-runs this with fewer frames.
  let attempts = [];
  let sizeOf = function () { return 0; };
  V._gifRun = function (n, fps, skip, W, onBlob, onFail) {
    attempts.push({ n: n, W: W });
    onBlob({ size: sizeOf(n, W) });
  };

  function scenario(label, preset, steps, perFrameMB) {
    attempts = [];
    lastDownload = null;
    sizeOf = function (n) { return Math.round(n * perFrameMB * MB); };
    V.steps = new Array(steps).fill(0);                       // total available frames
    V.dom.gifn.value = "22";                                  // request 22 frames
    V.dom.gifpreset.value = preset;
    V._makeGif();                                             // synchronous (mock fires onBlob inline)
    const status = V.dom.gifstatus.textContent;
    return { label, preset, attempts: attempts.slice(),
      finalN: attempts.length ? attempts[attempts.length - 1].n : null,
      W: attempts.length ? attempts[0].W : null,
      status, warned: /over/i.test(status) && /10\s*MB/i.test(status),
      download: lastDownload };
  }

  const out = {
    // Full, normal size: one pass at width 1600, plain "Saved" readout, no warning.
    fullNormal: scenario("full-normal", "full", 31, 0.30),
    // Full, too big: still one pass (Full never trims) but the warning fires.
    fullOver: scenario("full-over", "full", 31, 0.60),
    // Discord, over budget: auto-trims frames once and lands under 9.5 MB, no warning.
    discordTrim: scenario("discord-trim", "discord", 31, 0.55),
    // Discord, impossible: trims to the floor, still over 10 MB -> warning.
    discordFloor: scenario("discord-floor", "discord", 31, 2.0),
  };
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
