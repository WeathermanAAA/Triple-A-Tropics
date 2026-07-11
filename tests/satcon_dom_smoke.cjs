// DOM smoke test for the satcon.js PANEL half (the pure core is covered by
// test_satcon.cjs): mounts in jsdom, drives the honest empty states, then a
// full consensus render from a stubbed microwave manifest. Canvas 2D is
// mocked (jsdom has no canvas backend) — chart calls must not throw.
// Needs jsdom (npm install --no-save jsdom); run: node tests/satcon_dom_smoke.cjs
"use strict";
const assert = require("assert");
const path = require("path");
const fs = require("fs");
const { JSDOM } = require("jsdom");

const dom = new JSDOM('<div id="host"></div>', {
  url: "https://triple-a-tropics.com/",
  runScripts: "outside-only"
});
const win = dom.window;
const doc = win.document;

// ---- mock canvas 2d (every method a no-op, numeric fields writable) --------
function mockCtx() {
  return new Proxy({ canvas: null }, {
    get(t, k) {
      if (k in t) return t[k];
      return function () { return { width: 10 }; };   // measureText etc.
    },
    set(t, k, v) { t[k] = v; return true; }
  });
}
win.HTMLCanvasElement.prototype.getContext = function () { return mockCtx(); };

// ---- fetch stub -------------------------------------------------------------
const CARD = {
  version: "mwi-v1.0",
  error_overall: { rmse: 12.0 },
  error_by_bin: [{ lo: 0, hi: 64, rmse: 9.0, bias: 2.0 },
                 { lo: 64, hi: 250, rmse: 14.0, bias: -3.0 }],
  error_by_sensor: { GMI: { rmse: 10.0 } },
  mslp_error: { rmse: 7.0, bias: 0.5 }
};
let manifestBody = null;   // set per scenario
let overpassBody = null;
win.fetch = function (url) {
  const body = url.indexOf("overpasses.json") >= 0 ? overpassBody : manifestBody;
  if (!body) return Promise.reject(new Error("no stub"));
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
};

// ---- load satcon.js into the window ----------------------------------------
global.window = win;
global.document = doc;
const src = fs.readFileSync(
  path.join(__dirname, "..", "satellite", "explorer", "satcon.js"), "utf8");
win.eval(src);
const SatCon = win.SatCon;
assert.ok(SatCon && SatCon.mount && SatCon.core, "SatCon registered on window");

const host = doc.getElementById("host");
const H = 3600e3;
const t0 = Date.parse("2026-07-11T06:00:00Z");
const mkResult = (h, vmax) => ({
  frame: { timeMs: t0 + h * H, stamp: "s" + h },
  archer: { center: {}, confidenceScore: 1.0 },
  rec: { vmax, mslp: 985, eyescene: 3, cloudscene: 0, land: 0 }
});

function tick() { return new Promise(r => setTimeout(r, 20)); }

(async function () {
  // 1 · mount with nothing: honest "no workup" message
  SatCon.mount(host);
  assert.ok(host.textContent.indexOf("no workup yet") >= 0,
    "empty state asks for a workup");

  // 2 · archive view (no ATCF identity): no consensus, honest note
  SatCon.setStorm({ id: "archive", name: "ARCHIVE VIEW" });
  SatCon.update([mkResult(0, 60), mkResult(2, 62)]);
  assert.ok(host.textContent.indexOf("no storm identity") >= 0,
    "archive mode explains the missing MW member");

  // 3 · live storm but the model card is not deployed yet
  manifestBody = { storms: [{ slug: "wp072026" }] };  // no intensity_model
  SatCon.setStorm({ id: "JTWC_WP072026", name: "MEKKHALA" });
  await tick();
  assert.ok(host.textContent.indexOf("not deployed") >= 0,
    "missing model card stated honestly: " + host.textContent.slice(0, 200));

  // 4 · full path: model card + one fresh usable overpass -> consensus renders
  manifestBody = { storms: [{ slug: "al092026" }], intensity_model: CARD };
  overpassBody = { overpasses: [{
    id: "GMI_GPM_x", sensor: "GMI",
    valid_utc: new Date(t0 + 1.5 * H).toISOString(),
    intensity: { usable: true, vmax_kt: 80.0, mslp_hpa: 979.0, confidence: "moderate" }
  }] };
  SatCon.setStorm({ id: "NHC_AL092026", name: "TEST" });
  await tick();
  SatCon.update([mkResult(0, 60), mkResult(2, 62)]);
  const txt = host.textContent;
  assert.ok(/~\d+ kt/.test(txt), "consensus Vmax rendered: " + txt.slice(0, 120));
  assert.ok(/±\d+ kt/.test(txt), "uncertainty band rendered");
  assert.ok(txt.indexOf("2 members") >= 0, "member count shown");
  assert.ok(host.querySelectorAll(".scn-tbl tbody tr").length === 2,
    "member table has ADT + MW rows");
  assert.ok(txt.indexOf("NOT the CIMSS SATCON product") >= 0,
    "naming disclosure present");
  // the MW member (80 kt, bias -3 -> 83) must pull the blend above ADT's 62
  const m = /~(\d+) kt/.exec(txt);
  const v = parseInt(m[1], 10);
  assert.ok(v > 62 && v < 83, "blend sits between members (" + v + ")");

  // 5 · stale-only MW (7 h old at the latest frame): membership fails again
  SatCon.update([mkResult(0, 60), mkResult(8.6, 66)]);
  assert.ok(host.textContent.indexOf("6-h window") >= 0,
    "stale MW yields the honest no-consensus message");

  console.log("satcon_dom_smoke.cjs: all assertions passed");
})().catch(function (e) { console.error(e); process.exit(1); });
