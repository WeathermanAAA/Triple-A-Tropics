// jsdom smoke test for the /satellite/ "Last 6h" loop-window control.
//
// Loads satellite/index.html into jsdom with stubbed fetch / Image / rAF,
// drives the Live Storm Floater viewer (makeSatViewer "sat"), and proves:
//   1. default "All" mode is byte-identical to today (winStart 0, scrub.min 0)
//   2. "Last 6h" clamps the loop + scrubber to frames within 6h of the newest
//      (correct boundary: frames[winStart] in-window, frames[winStart-1] out)
//   3. the playback loop WRAPS to the window start (not 0)
//   4. manual stepping is clamped to the window start (no escape below it)
//   5. switching back to "All" restores the full range
//   6. when fewer than 6h of frames exist, "Last 6h" loops everything (winStart 0)
//
// Requires jsdom (not a repo dependency): npm install --no-save jsdom
// Usage: node tests/sat_loop_window_smoke.cjs [path/to/satellite/index.html]
"use strict";
const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const PAGE_PATH = process.argv[2] ||
  path.join(__dirname, "..", "satellite", "index.html");
const PAGE = fs.readFileSync(PAGE_PATH, "utf8");
const CDN = "https://cdn.triple-a-tropics.com";

let failures = 0;
function ok(cond, msg) {
  console.log((cond ? "ok" : "NOT OK") + " - " + msg);
  if (!cond) failures++;
}
const delay = (ms) => new Promise((r) => setTimeout(r, ms));

// A loop band: `spanHours` of frames at `stepMin` spacing, newest ~= now so the
// viewer stays on its active (non-stale) path.
function makeFrames(spanHours, stepMin) {
  const n = Math.round((spanHours * 60) / stepMin) + 1;
  const newest = Date.now();
  const base = newest - spanHours * 3600000;
  const out = [];
  for (let i = 0; i < n; i++) {
    const iso = new Date(base + i * stepMin * 60000).toISOString().slice(0, 19);
    out.push({ t: iso, key: "floaters/test/ir/f" + i + ".webp" });
  }
  return out;
}

// Pure mirror of satWinStart, to compute the expected boundary independently.
function expectWinStart(frames, hours) {
  if (!frames.length || !hours) return 0;
  const newestT = new Date(frames[frames.length - 1].t).getTime();
  const cutoff = newestT - hours * 3600000;
  for (let i = 0; i < frames.length; i++) {
    if (new Date(frames[i].t).getTime() >= cutoff) return i;
  }
  return 0;
}

function buildDom(frames) {
  const vc = new VirtualConsole(); // swallow the zoom-tool's leaflet `L is not defined`
  const dom = new JSDOM(PAGE, {
    runScripts: "dangerously",
    url: "https://triple-a-tropics.com/satellite/",
    virtualConsole: vc,
    beforeParse(window) {
      // ---- fetch stub: floaters top -> one storm -> band sub-manifest -------
      window.fetch = function (url) {
        let body = null;
        if (url.indexOf("floaters/manifest.json") >= 0) {
          body = { storms: [{ slug: "test", name: "TEST", basin: "AL",
                              manifest: "floaters/test/ir.json" }] };
        } else if (url.indexOf("floaters/test") >= 0) {
          body = { bands: { ir: { label: "IR window", frames: frames } } };
        } else if (url.indexOf("meso/manifest.json") >= 0) {
          body = { sectors: [] };
        }
        return Promise.resolve({
          ok: body !== null,
          json: () => Promise.resolve(body),
        });
      };
      // ---- Image stub: src-set settles as a decoded frame ------------------
      window.Image = class {
        constructor() {
          this.onload = null; this.onerror = null;
          this.crossOrigin = null; this.decoding = null; this.fetchPriority = null;
          this.naturalWidth = 120; this.naturalHeight = 120;
          this._src = "";
        }
        set src(v) { this._src = v; const s = this; setTimeout(() => { if (s.onload) s.onload(); }, 0); }
        get src() { return this._src; }
        decode() { return Promise.resolve(); }
      };
      // ---- controllable rAF: capture ticks, flush on demand ----------------
      let rafCbs = [];
      window.requestAnimationFrame = function (cb) { rafCbs.push(cb); return rafCbs.length; };
      window.cancelAnimationFrame = function () {};
      window.__flushRaf = function (ts) {
        const cbs = rafCbs; rafCbs = [];
        cbs.forEach((cb) => { try { cb(ts); } catch (e) {} });
      };
      // the player clocks slots in performance.now(); make it ours. __tick(ms)
      // advances the clock, runs the rAF (advance), then two more rAFs so the
      // no-decode() fallback arm presents the slot and starts its clock.
      window.__now = 1e7;
      window.performance.now = function () { return window.__now; };
      window.__tick = function (ms) {
        window.__now += ms;
        window.__flushRaf(window.__now); window.__flushRaf(window.__now); window.__flushRaf(window.__now);
      };
    },
  });
  return dom;
}

async function settle(dom) {
  const v = dom.window.__satViewers && dom.window.__satViewers.sat;
  for (let i = 0; i < 80; i++) {            // up to ~4s for fetch+decode to land
    await delay(50);
    if (v) { const s = v.state(); if (s.frames > 0 && s.decoded >= 2) return v; }
  }
  return v;
}

(async () => {
  // ===== Scenario A: 12h of frames at 10-min spacing (> 6h window) ==========
  const framesA = makeFrames(12, 10);
  const nA = framesA.length;
  const expWin = expectWinStart(framesA, 6);
  const domA = buildDom(framesA);
  const win = domA.window, doc = win.document;
  const v = await settle(domA);
  ok(!!v, "A: floater viewer mounted (__satViewers.sat present)");
  if (!v) { console.log("FATAL: viewer never mounted"); process.exit(1); }

  const loopHost = doc.getElementById("sat-loop");
  const scrub = doc.getElementById("sat-scrub");
  ok(!!loopHost && loopHost.children.length === 2, "A: sat-loop has 2 buttons");
  ok(loopHost && /All/.test(loopHost.children[0].textContent) &&
     /6h/.test(loopHost.children[1].textContent), "A: buttons read All / Last 6h");

  let s = v.state();
  ok(s.frames === nA, "A: loaded all " + nA + " frames (got " + s.frames + ")");
  ok(s.loopHours === 0 && s.winStart === 0, "A: default mode = All (winStart 0)");
  ok(scrub.min === "0" && scrub.max === String(nA - 1),
     "A: default scrub range 0.." + (nA - 1) + " (got " + scrub.min + ".." + scrub.max + ")");
  ok(loopHost.children[0].classList.contains("active"), "A: 'All' button is active");

  // ---- engage Last 6h ------------------------------------------------------
  loopHost.children[1].click();
  s = v.state();
  ok(s.loopHours === 6, "A: clicking 'Last 6h' sets loopHours=6");
  ok(s.winStart === expWin && expWin > 0,
     "A: winStart clamps to " + expWin + " (got " + s.winStart + ")");
  ok(scrub.min === String(expWin) && scrub.max === String(nA - 1),
     "A: scrub range rides up to " + expWin + ".." + (nA - 1) +
     " (got " + scrub.min + ".." + scrub.max + ")");
  ok(loopHost.children[1].classList.contains("active"), "A: 'Last 6h' button is active");
  // boundary correctness vs the raw timestamps
  const newestMs = new Date(framesA[nA - 1].t).getTime();
  const inWin = (newestMs - new Date(framesA[expWin].t).getTime()) <= 6 * 3600000;
  const outBelow = expWin > 0 &&
    (newestMs - new Date(framesA[expWin - 1].t).getTime()) > 6 * 3600000;
  ok(inWin, "A: frames[winStart] is within 6h of newest");
  ok(outBelow, "A: frames[winStart-1] is older than 6h (boundary excluded)");

  // ---- loop WRAPS to window start, not 0 -----------------------------------
  // Park on the newest frame, then resume and tick: the advance must wrap to
  // winStart (the head of the 6h window), never to index 0.
  scrub.value = String(nA - 1);
  scrub.dispatchEvent(new win.Event("input"));   // satShow(newest) + pause
  ok(v.state().idx === nA - 1, "A: scrubbed to newest frame (idx " + (nA - 1) + ")");
  doc.getElementById("sat-play").click();        // resume the loop
  ok(v.state().playing, "A: playback resumed");
  await delay(0);                                 // let the decode-ahead warm promises settle
  win.__tick(1e7);                                // one tick: newest -> wrap
  ok(v.state().idx === expWin,
     "A: loop wrapped from newest to winStart=" + expWin + " (got " + v.state().idx + ")");
  await delay(0);
  win.__tick(1e7);                                // next tick advances within window
  ok(v.state().idx === expWin + 1,
     "A: loop advances within window to " + (expWin + 1) + " (got " + v.state().idx + ")");

  // ---- manual step is clamped to the window --------------------------------
  doc.getElementById("sat-play").click();         // pause
  scrub.value = String(expWin);
  scrub.dispatchEvent(new win.Event("input"));     // sit on the window start
  ok(v.state().idx === expWin, "A: parked on window start " + expWin);
  doc.getElementById("sat-step-back").click();     // step back -> must NOT escape
  ok(v.state().idx === expWin,
     "A: step-back clamped at window start (stayed " + v.state().idx + ")");

  // ---- back to All restores the full range ---------------------------------
  loopHost.children[0].click();
  s = v.state();
  ok(s.loopHours === 0 && s.winStart === 0, "A: 'All' restores winStart 0");
  ok(scrub.min === "0", "A: 'All' restores scrub.min 0");
  doc.getElementById("sat-step-back").click();     // now free to step below expWin
  ok(v.state().idx === expWin - 1,
     "A: in All mode step-back crosses below old window (idx " + v.state().idx + ")");
  domA.window.close();

  // ===== Scenario B: only 3h of frames -> "Last 6h" loops everything ========
  const framesB = makeFrames(3, 10);
  const nB = framesB.length;
  const domB = buildDom(framesB);
  const vB = await settle(domB);
  ok(!!vB, "B: viewer mounted (3h fixture)");
  const loopB = domB.window.document.getElementById("sat-loop");
  const scrubB = domB.window.document.getElementById("sat-scrub");
  loopB.children[1].click();                        // Last 6h
  const sB = vB.state();
  ok(sB.loopHours === 6, "B: loopHours=6 selected");
  ok(sB.winStart === 0,
     "B: <6h of frames -> window clamps to everything (winStart 0, got " + sB.winStart + ")");
  ok(scrubB.min === "0" && scrubB.max === String(nB - 1),
     "B: scrub range stays full 0.." + (nB - 1));
  domB.window.close();

  console.log("\n" + (failures ? "FAILED: " + failures + " check(s)" : "ALL CHECKS PASSED"));
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error("HARNESS ERROR:", e); process.exit(2); });
