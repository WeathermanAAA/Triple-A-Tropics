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
<select id="enscenters-gifstart"></select><select id="enscenters-gifend"></select>
<input id="enscenters-giffps" value="10">
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
// The shared SSHWS palette, as the real page loads it (ordered before
// the viewer). enscenters.js holds no fallback copy by design.
win.eval(fs.readFileSync(path.join(__dirname, "..", "tat_palette.js"), "utf8"));
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
  // _gifRun now takes the explicit frame-index list; record its length + the list.
  V._gifRun = function (sel, fps, W, onBlob, onFail) {
    attempts.push({ n: sel.length, W: W, sel: sel.slice() });
    onBlob({ size: sizeOf(sel.length, W) });
  };

  // Range-selection probe (no encode-size pressure): 31 hourly-6 steps (0..180h).
  // Picking 0->endH (optionally skip-thinned) must yield ONLY the in-range hours.
  function rangeProbe(startH, endH, skip) {
    attempts = [];
    sizeOf = function () { return 1; };                      // tiny -> never trims
    V.steps = []; for (let i = 0; i < 31; i++) V.steps.push(i * 6);
    V._populateGifHours();
    V.dom.gifstart.value = String(startH);
    V.dom.gifend.value = String(endH);
    V.dom.gifskip.value = String(skip || 0);
    V.dom.gifpreset.value = "full";
    V._makeGif();
    V.dom.gifskip.value = "0";                               // reset for later scenarios
    const sel = attempts.length ? attempts[0].sel : [];
    return sel.map((i) => V.steps[i]);                       // -> the forecast hours
  }

  // The model exposes `nSteps` forecast hours (0,6,12 …); the user picks an HOUR
  // range covering the first `rangeFrames` of them, so the base frame set is
  // `rangeFrames` (mirrors the old "request N frames" calibration of 22). Discord
  // then auto-trims WITHIN that range.
  function scenario(label, preset, nSteps, perFrameMB, rangeFrames) {
    attempts = [];
    lastDownload = null;
    sizeOf = function (n) { return Math.round(n * perFrameMB * MB); };
    V.steps = []; for (let i = 0; i < nSteps; i++) V.steps.push(i * 6);   // 0,6,…
    V._populateGifHours();                                   // build the F-hour options
    V.dom.gifstart.value = "0";
    V.dom.gifend.value = String((rangeFrames - 1) * 6);      // in-range = rangeFrames steps
    V.dom.gifpreset.value = preset;
    V._makeGif();                                            // synchronous (mock fires onBlob inline)
    const status = V.dom.gifstatus.textContent;
    return { label, preset, attempts: attempts.slice(),
      finalN: attempts.length ? attempts[attempts.length - 1].n : null,
      baseN: attempts.length ? attempts[0].n : null,
      W: attempts.length ? attempts[0].W : null,
      status, warned: /over/i.test(status) && /10\s*MB/i.test(status),
      download: lastDownload };
  }

  const out = {
    // Full, normal size: one pass at width 1600, plain "Saved" readout, no warning.
    // 31 hours available, range covers the first 22 -> base 22 frames.
    fullNormal: scenario("full-normal", "full", 31, 0.30, 22),
    // Full, too big: still one pass (Full never trims) but the warning fires.
    fullOver: scenario("full-over", "full", 31, 0.60, 22),
    // Discord, over budget: auto-trims frames once and lands under 9.5 MB, no warning.
    discordTrim: scenario("discord-trim", "discord", 31, 0.55, 22),
    // Discord, impossible: trims to the floor, still over 10 MB -> warning.
    discordFloor: scenario("discord-floor", "discord", 31, 2.0, 22),
    // Range selection: 0->72h captures ONLY F000..F072 (13 frames of the 31).
    range_0_72: rangeProbe(0, 72, 0),
    // Reversed input auto-swaps to the same range (end >= start guard).
    range_swapped: rangeProbe(72, 0, 0),
    // Skip-every-1 thins WITHIN the range but keeps the end (F072).
    range_skip1: rangeProbe(0, 72, 1),
  };
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
