// jsdom smoke test for the /satellite/ gated playback advance (the
// anti-acceleration contract in makeSatViewer).
//
// The 13Z bug: the fixed-timestep clock skipped past frames that hadn't
// decoded to "whatever is ready", which read as sped-up playback jumping 2-3
// frames between paints. The fix gates the advance on the next frame being
// decoded and pauses VISIBLY (Buffering) at any mid-loop hole; the only
// sanctioned skips are permanent gaps (404s) and the frontier wrap.
//
// This test proves, deterministically:
//   1. steady play advances EXACTLY +1 archive frame per tick (never 2-3)
//      (ticks yield a task between them, as real browsers do, so the
//      decode()-ahead warm promises can settle)
//   2. poll-appended, still-loading tail = FRONTIER: the loop wraps to the
//      window start without entering Buffering and without showing the tail
//   3. a MID-LOOP HOLE (pending frame with a decoded frame past it) pauses
//      playback in the Buffering state -- the playhead does NOT move and the
//      decoded later frame is NOT jumped to
//   4. when the hole decodes, playback resumes and shows that frame next
//   5. the __satTimingHook stream never contains an archive-step > 1 except
//      the wrap, and records the buffer-start/buffer-end pair
//
// Requires jsdom (not a repo dependency): npm install --no-save jsdom
// Usage: node tests/sat_pacing_smoke.cjs [path/to/satellite/index.html]
"use strict";
const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const PAGE_PATH = process.argv[2] ||
  path.join(__dirname, "..", "satellite", "index.html");
const PAGE = fs.readFileSync(PAGE_PATH, "utf8");

let failures = 0;
function ok(cond, msg) {
  console.log((cond ? "ok" : "NOT OK") + " - " + msg);
  if (!cond) failures++;
}
const delay = (ms) => new Promise((r) => setTimeout(r, ms));

// 30 frames at 10-min spacing, newest ~= now (non-stale path).
function makeFrames(n) {
  const newest = Date.now();
  const base = newest - (n - 1) * 600000;
  const out = [];
  for (let i = 0; i < n; i++) {
    const iso = new Date(base + i * 600000).toISOString().slice(0, 19);
    out.push({ t: iso, key: "floaters/test/ir/f" + i + ".webp" });
  }
  return out;
}

function buildDom(state) {
  const vc = new VirtualConsole(); // swallow the zoom-tool's `L is not defined`
  const dom = new JSDOM(PAGE, {
    runScripts: "dangerously",
    url: "https://triple-a-tropics.com/satellite/",
    virtualConsole: vc,
    beforeParse(window) {
      window.fetch = function (url) {
        let body = null;
        if (url.indexOf("floaters/manifest.json") >= 0) {
          body = { storms: [{ slug: "test", name: "TEST", basin: "AL",
                              manifest: "floaters/test/ir.json" }] };
        } else if (url.indexOf("floaters/test") >= 0) {
          body = { bands: { ir: { label: "IR", frames: state.frames } } };
        } else if (url.indexOf("meso/manifest.json") >= 0) {
          body = { sectors: [] };
        }
        return Promise.resolve({ ok: body !== null,
                                 json: () => Promise.resolve(body) });
      };
      // Image stub: decodes instantly UNLESS its src matches a held key --
      // then it parks until state.release(key) fires its onload.
      state.pending = {};
      state.release = function (frag) {
        for (const src in state.pending) {
          if (src.indexOf(frag) >= 0) {
            const cb = state.pending[src];
            delete state.pending[src];
            cb();
          }
        }
      };
      window.Image = class {
        constructor() {
          this.onload = null; this.onerror = null;
          this.crossOrigin = null; this.decoding = null; this.fetchPriority = null;
          this.naturalWidth = 120; this.naturalHeight = 120;
          this._src = "";
        }
        set src(v) {
          this._src = v;
          const s = this;
          const fire = () => { if (s.onload) s.onload(); };
          const held = (state.holds || []).some((h) => v.indexOf(h) >= 0);
          if (held) state.pending[v] = fire;
          else setTimeout(fire, 0);
        }
        get src() { return this._src; }
        decode() { return Promise.resolve(); }
      };
      let rafCbs = [];
      window.requestAnimationFrame = function (cb) { rafCbs.push(cb); return rafCbs.length; };
      window.cancelAnimationFrame = function () {};
      window.__flushRaf = function (ts) {
        const cbs = rafCbs; rafCbs = [];
        cbs.forEach((cb) => { try { cb(ts); } catch (e) {} });
      };
      // timing hook: the player reports every paint + buffer transition here
      window.__satLog = [];
      window.__satTimingHook = function (ev) { window.__satLog.push(ev); };
    },
  });
  return dom;
}

async function settle(dom) {
  const v = dom.window.__satViewers && dom.window.__satViewers.sat;
  for (let i = 0; i < 80; i++) {
    await delay(50);
    if (v) { const s = v.state(); if (s.frames > 0 && s.decoded >= 2) return v; }
  }
  return v;
}

(async () => {
  const N = 30;
  const state = { frames: makeFrames(N), holds: [] };
  const dom = buildDom(state);
  const win = dom.window, doc = win.document;
  const v = await settle(dom);
  ok(!!v, "viewer mounted");
  if (!v) process.exit(1);
  for (let i = 0; i < 40 && v.state().decoded < N; i++) await delay(50);
  ok(v.state().decoded === N, "all " + N + " frames decoded");

  // ---- 1. steady play: EXACTLY +1 per tick --------------------------------
  const scrub = doc.getElementById("sat-scrub");
  scrub.value = "0";
  scrub.dispatchEvent(new win.Event("input"));   // park at 0 (pauses + latches)
  doc.getElementById("sat-play").click();
  ok(v.state().playing, "playback started");
  let ts = 1e7;
  const seen = [v.state().idx];
  for (let k = 0; k < 10; k++) { ts += 1000; await delay(0); win.__flushRaf(ts); seen.push(v.state().idx); }
  const steps = seen.slice(1).map((x, i) => x - seen[i]);
  ok(steps.every((d) => d === 1),
     "steady play advances exactly +1 per tick (steps: " + steps.join(",") + ")");
  ok(!v.state().buffering, "no Buffering during steady play");
  ok(v.state().nearestOkInPlay === 0, "nearestOk snap never hit during play");

  // ---- 2. frontier: appended still-loading tail -> wrap, not skip-through --
  // Scrub to newest, resume, then append 3 frames that never decode.
  doc.getElementById("sat-play").click();        // pause
  scrub.value = String(N - 1);
  scrub.dispatchEvent(new win.Event("input"));
  doc.getElementById("sat-play").click();        // resume from newest
  state.holds = ["/fh"];                         // hold every appended frame
  const newest = Date.now();
  for (let a = 1; a <= 3; a++) {
    state.frames = state.frames.concat([{
      t: new Date(newest + a * 600000).toISOString().slice(0, 19),
      key: "floaters/test/ir/fh" + a + ".webp" }]);
  }
  await v.pollNow();
  ok(v.state().frames === N + 3, "poll appended 3 pending frames");
  ts += 10000; win.__flushRaf(ts);               // dwell-sized step past newest
  const afterWrap = v.state();
  ok(afterWrap.idx === 0,
     "frontier: loop wrapped to window start, not into the pending tail (idx " +
     afterWrap.idx + ")");
  ok(!afterWrap.buffering, "frontier wrap does not enter Buffering");

  // ---- 3./4. mid-loop hole: pause visibly, resume on decode ---------------
  // Let the LAST appended frame decode while the first two stay pending:
  // a decoded frame now exists PAST the hole -- skipping to it is the bug.
  state.release("fh3");
  for (let i = 0; i < 20 && v.state().decoded < N + 1; i++) await delay(25);
  ok(v.state().decoded === N + 1, "tail frame fh3 decoded (hole at fh1/fh2)");
  // play forward from just before the hole
  for (let k = 0; k < N + 5 && v.state().idx < N - 1; k++) {
    ts += 1000; await delay(0); win.__flushRaf(ts);
  }
  ok(v.state().idx === N - 1, "played up to the frame before the hole");
  const idxBefore = v.state().idx;
  ts += 10000; win.__flushRaf(ts);               // next advance meets the hole
  ok(v.state().buffering, "mid-loop hole -> Buffering state entered");
  ok(v.state().idx === idxBefore,
     "playhead HELD at " + idxBefore + " (no jump to the decoded frame past the hole)");
  await delay(300);                              // veil debounce is 250 ms
  const veil = doc.getElementById("sat-status");
  ok(veil.style.display === "flex" && /Buffering/.test(veil.textContent),
     "Buffering veil is VISIBLE and says so");
  // several ticks while still holed: playhead must not creep
  for (let k = 0; k < 5; k++) { ts += 1000; await delay(0); win.__flushRaf(ts); }
  ok(v.state().idx === idxBefore, "playhead still held across held ticks");
  // release the hole -> resume -> the held frame is the NEXT one shown
  state.release("fh1"); state.release("fh2");
  for (let i = 0; i < 20 && v.state().decoded < N + 3; i++) await delay(25);
  ts += 1000; win.__flushRaf(ts);                // buffering tick -> resume
  ok(!v.state().buffering, "Buffering exits once the hole decodes");
  ts += 10000; win.__flushRaf(ts);               // next advance
  ok(v.state().idx === N, "resumed INTO the held frame (idx " + v.state().idx + ")");

  // ---- 5. hook stream: no silent multi-frame archive steps ----------------
  const log = win.__satLog;
  const played = log.filter((e) => e.type === "frame" && e.playing);
  let badStep = 0;
  for (let i = 1; i < played.length; i++) {
    const d = played[i].idx - played[i - 1].idx;
    if (d > 1) badStep++;                        // wrap is negative; +2 or more = skip
  }
  ok(played.length > 10 && badStep === 0,
     "hook stream has zero multi-frame forward skips (" + played.length + " paints)");
  ok(log.some((e) => e.type === "buffer-start") &&
     log.some((e) => e.type === "buffer-end"),
     "hook recorded the buffer-start/buffer-end pair");

  console.log("\n" + (failures ? "FAILED: " + failures + " check(s)" : "ALL CHECKS PASSED"));
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error("HARNESS ERROR:", e); process.exit(2); });
