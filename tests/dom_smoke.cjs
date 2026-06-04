// DOM-glue smoke test for the per-basin live overlay (LIVE_BASIN_JS).
//
// Driven by tests/test_dom_glue.py, which renders a baked page from an
// EMPTY-season fixture payload and hands us a fixture "live feed" with
// storms in it. Requires jsdom (not a repo dependency):
//
//   npm install --no-save jsdom     # repo root, or anywhere on NODE_PATH
//
// Scenarios:
//   1. happy path     -> all storm fragments swapped, as-of updated
//   2. fetch failure  -> baked render fully intact
//   3. year mismatch  -> baked render fully intact (rollover guard)
//   4. basin mismatch -> baked render fully intact
//
// Usage: node dom_smoke.cjs <page.html> <feed.json>
"use strict";
const fs = require("fs");
const { JSDOM } = require("jsdom");

const PAGE = fs.readFileSync(process.argv[2], "utf8");
const FEED = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

function runScenario(name, fetchImpl) {
  return new Promise((resolve) => {
    const dom = new JSDOM(PAGE, {
      runScripts: "dangerously",
      url: "https://triple-a-tropics.com/test_tracks.html",
      beforeParse(window) {
        window.fetch = fetchImpl;
        window.console.warn = (...a) =>
          console.log(`[${name}] warn:`, a.map(String).join(" "));
      },
    });
    // the overlay's fetch chain settles on microtasks; one macrotask is enough
    setTimeout(() => resolve(dom), 200);
  });
}

function snapshot(dom) {
  const d = dom.window.document;
  return {
    asOf: d.getElementById("as-of").textContent,
    stats: d.getElementById("season-stats").innerHTML,
    panelTitle: d.getElementById("panel-title").innerHTML,
    tracksHtml: d.querySelector("#chart > g.tracks").innerHTML,
    activeHtml: d.querySelector("#chart > g.active-storms").innerHTML,
    cardsText: d.getElementById("storms").textContent,
  };
}

(async () => {
  let failures = 0;
  const ok = (cond, msg) => {
    console.log((cond ? "ok" : "NOT OK") + " - " + msg);
    if (!cond) failures++;
  };

  // Baseline: fetch never resolves -> exactly the baked render.
  const base = snapshot(await runScenario("baseline", () => new Promise(() => {})));

  // 1. Happy path: overlay swaps every storm-derived fragment.
  const live = snapshot(await runScenario("live", () =>
    Promise.resolve({ ok: true, json: () => Promise.resolve(FEED) })));
  const firstName = String(FEED.storms[0].name || "").replace(/"/g, "");
  const hasActive = FEED.storms.some((s) => s.is_active);
  ok(live.asOf === "As of " + FEED.updated,
     `as-of overwritten from feed ("${live.asOf}")`);
  ok(live.tracksHtml.length > base.tracksHtml.length,
     `tracks layer swapped (${base.tracksHtml.length} -> ${live.tracksHtml.length} chars)`);
  ok(live.cardsText !== base.cardsText && live.cardsText.indexOf(firstName.slice(0, 3)) !== -1,
     "storm cards rebuilt from feed");
  ok(!hasActive || live.activeHtml.indexOf("active-icon") !== -1,
     "active marker(s) drawn");
  ok(live.stats !== base.stats, "season stats updated");
  ok(live.panelTitle !== base.panelTitle, "panel title updated");

  // 2. Fetch failure -> baked render stands untouched.
  const fail = snapshot(await runScenario("fetchfail", () =>
    Promise.reject(new Error("network down"))));
  ok(JSON.stringify(fail) === JSON.stringify(base), "fetch-fail: baked render intact");

  // 3. Year mismatch (season rollover) -> baked render stands.
  const wrongYear = Object.assign({}, FEED, { year: FEED.year - 1 });
  const year = snapshot(await runScenario("yearmismatch", () =>
    Promise.resolve({ ok: true, json: () => Promise.resolve(wrongYear) })));
  ok(JSON.stringify(year) === JSON.stringify(base), "year-mismatch: baked render intact");

  // 4. Basin mismatch -> baked render stands.
  const wrongBasin = Object.assign({}, FEED, { basin: FEED.basin === "ep" ? "al" : "ep" });
  const basin = snapshot(await runScenario("basinmismatch", () =>
    Promise.resolve({ ok: true, json: () => Promise.resolve(wrongBasin) })));
  ok(JSON.stringify(basin) === JSON.stringify(base), "basin-mismatch: baked render intact");

  console.log(failures === 0 ? "# all DOM smoke assertions passed"
                             : `# ${failures} assertion(s) FAILED`);
  process.exit(failures === 0 ? 0 : 1);
})();
