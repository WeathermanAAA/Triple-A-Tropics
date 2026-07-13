// Functional smoke for the ASCAT cockpit field's satellite backdrop +
// barb hover tooltips (cockpit_fields.js). Driven by
// tests/test_cockpit_sc.py. Loads cockpit_fields.js under jsdom with the
// cockpit, MapLibre pane, AscatViewer and fetch all stubbed, then walks
// the REAL code paths:
//
//   setPaneField('sc-storm')  -> scApplyBackdrop swaps the pane tiles to
//     the clean-IR product (helpers.productByKey chain) WITHOUT touching
//     pane.product, then re-shows imagery; barbs draw with the dark halo.
//   scDraw                    -> retains the thinned cells (lat/lon/kt/dir
//     + pass time/sensor) for hit-testing.
//   mousemove near a barb     -> the cursor tooltip carries wind speed,
//     FROM direction, lat/lon, sensor and pass time.
//   backdrop 'none'           -> imagery hidden again; clearPaneField
//     restores the user's own product.
//
// Usage: node cockpit_sc_smoke.cjs <cockpit_fields.js>
"use strict";
const fs = require("fs");
const { JSDOM } = require("jsdom");

const SRC = fs.readFileSync(process.argv[2], "utf8");

const dom = new JSDOM("<!doctype html><html><body>" +
  '<div id="cx-list-mw"></div><div id="cx-list-sc"></div>' +
  "</body></html>", { runScripts: "outside-only", pretendToBeVisual: true });
const { window } = dom;

// jsdom has no canvas backend — hand scDraw a no-op recording context
const ctxCalls = [];
window.HTMLCanvasElement.prototype.getContext = function () {
  return new Proxy({}, {
    get: (t, k) => (k === "canvas" ? null : (...a) => { ctxCalls.push(String(k)); }),
    set: () => true,
  });
};

// ---- stubs the module expects on window --------------------------------
const barbs = [];
window.AscatViewer = {
  STYLES: { highcontrast: { scale: [[0, "#9fd"], [34, "#fc0"]], barbLw: 2 },
            sshws: { scale: [[0, "#9fd"]], barbLw: 2 } },
  DENSITY: { auto: 26, dense: 18, sparse: 40 },
  KT_SCALE: [], KT_SCALE_HC: [],
  drawBarb: (g, x, y, kt, dir, color, lw) => barbs.push({ x, y, kt, dir, color, lw }),
  stormMatch: (s, key) => String(s.slug || "").toLowerCase() === key,
};
window.MicrowaveViewer = { PRODUCTS: [], tileRel: () => null, boundsOf: () => null };

const MANIFEST = { passes: [{
  id: "p1", sensor: "Metop-C", start_utc: "2026-07-13T09:27:00Z",
  storms: [{ slug: "cp902026", atcf: "CP902026", name: "90C", lat: 14.5, lon: -151.5 }],
}] };
const PASS = { id: "p1", sensor: "Metop-C", start_utc: "2026-07-13T09:27:00Z",
  wvc: { la: [14.5], lo: [-151.5], kt: [38], dir: [210] } };
window.fetch = (url) => Promise.resolve({
  ok: true,
  json: () => Promise.resolve(String(url).indexOf("manifest.json") >= 0 ? MANIFEST : PASS),
});

// ---- fake pane / map / tv ----------------------------------------------
const mapHandlers = {};
const map = {
  on: (evt, fn) => { (mapHandlers[evt] = mapHandlers[evt] || []).push(fn); },
  off: () => {},
  project: ([lon, lat]) => ({ x: (lon + 180) * 4, y: (90 - lat) * 4 }),
  getBounds: () => ({ getWest: () => -180, getEast: () => 0,
                      getSouth: () => 0, getNorth: () => 80 }),
  getLayer: () => null, fitBounds: () => {},
};
const tvLog = [];
const paneEl = window.document.createElement("div");
window.document.body.appendChild(paneEl);
paneEl.getBoundingClientRect = () => ({ width: 800, height: 600, left: 0, top: 0 });
const pane = {
  el: paneEl,
  kind: "tile",
  product: { key: "truecolor", id: "goes19-conus-truecolor", title: "True Color" },
  tv: {
    map,
    setImageryVisible: (on) => tvLog.push("vis:" + on),
    setProduct: (url, p) => { tvLog.push("prod:" + p.key); return Promise.resolve(); },
  },
};
const CX = { panes: [pane], active: 0 };
const helpers = {
  flash: () => {},
  renderPaneChrome: () => {},
  markFieldActive: () => {},
  productByKey: (k) =>
    ({ c07: { key: "c07", id: "goes19-conus-c07", title: "C07 · 3.9 µm (Shortwave IR)" },
       ir: { key: "ir", id: "goes19-conus-ir", title: "C13 · 10.3 µm (Clean IR)" } }[k] || null),
  manifestUrlFor: (p) => "https://x/" + p.key + "/latest_times.json",
  productAvailable: () => true,
};

// ---- load the module under test ----------------------------------------
dom.window.eval(SRC);
const CF = window.CockpitFields;

const fails = [];
function ok(cond, label) { (cond ? [] : fails).push(label); if (!cond) console.error("FAIL: " + label); }

(async () => {
  CF.init(CX, helpers);

  // 1) enter the storm-locked SC field: backdrop defaults to clean-IR
  CF.setPaneField(0, "sc-storm");
  await new Promise((r) => setTimeout(r, 30));

  ok(tvLog.indexOf("prod:c07") >= 0, "clean-IR backdrop product applied (c07)");
  ok(tvLog.indexOf("vis:true") > tvLog.indexOf("prod:c07"),
     "imagery re-shown after the backdrop swap");
  ok(pane.product.key === "truecolor", "pane.product untouched by the backdrop swap");
  ok(pane.sc && pane.sc.view === "cp902026", "storm-locked view resolved to cp902026");

  // 2) draw retained the thinned cells and drew halo + barb (2 draw calls)
  CF.scDraw(pane);
  ok(pane._scCells && pane._scCells.length === 1, "one thinned cell retained for hover");
  ok(barbs.length >= 2, "halo casing + colored barb drawn over imagery");
  const cell = pane._scCells[0];
  ok(cell.kt === 38 && cell.dir === 210 && cell.lat === 14.5, "cell carries wind + position");

  // 3) hover near the barb -> tooltip with speed/dir/latlon/sensor/time
  const mm = (mapHandlers.mousemove || [])[0];
  ok(!!mm, "mousemove handler wired");
  if (mm) {
    mm({ point: { x: cell.x + 4, y: cell.y + 4 } });
    const tip = paneEl.querySelector(".cx-sc-tip");
    ok(!!tip && tip.style.display === "block", "tooltip shown near a barb");
    const html = tip ? tip.innerHTML : "";
    ok(html.indexOf("38 kt") >= 0, "tooltip has wind speed");
    ok(html.indexOf("from 210°") >= 0, "tooltip has FROM direction");
    ok(html.indexOf("14.5°N") >= 0 && html.indexOf("151.5°W") >= 0, "tooltip has lat/lon");
    ok(html.indexOf("Metop-C") >= 0, "tooltip has sensor");
    ok(html.indexOf("2026-07-13 09:27Z") >= 0, "tooltip has pass time");
    mm({ point: { x: cell.x + 200, y: cell.y + 200 } });
    ok(tip.style.display === "none", "tooltip hides away from barbs");
  }

  // 4) backdrop 'none' hides imagery and restores the user's product
  pane.sc.backdrop = "none";
  const before = tvLog.length;
  CF.setPaneField(0, "sc-storm");
  await new Promise((r) => setTimeout(r, 30));
  ok(tvLog.slice(before).indexOf("vis:false") >= 0, "backdrop none hides imagery");
  ok(tvLog.slice(before).indexOf("prod:truecolor") >= 0,
     "backdrop none restores the user's own product");

  // 5) exiting the field restores tiles + shows imagery
  pane.sc.backdrop = "clean";
  CF.setPaneField(0, "sc-storm");
  await new Promise((r) => setTimeout(r, 30));
  const beforeClear = tvLog.length;
  CF.clearPaneField(0);
  const afterClear = tvLog.slice(beforeClear);
  ok(afterClear.indexOf("prod:truecolor") >= 0, "clearPaneField restores the user's product");
  ok(afterClear.indexOf("vis:true") >= 0, "clearPaneField re-shows imagery");

  if (fails.length) { console.error(fails.length + " failure(s)"); process.exit(1); }
  console.log("cockpit_sc smoke: PASS");
})().catch((e) => { console.error(e); process.exit(1); });
