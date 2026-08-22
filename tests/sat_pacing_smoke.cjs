// jsdom smoke test for the /satellite/ gated, hold-through-gaps playback
// (makeSatViewer's playback contract, v3).
//
// Proves, deterministically, with a controllable Image stub:
//   1. steady play advances exactly +1 slot per tick (never 2-3), and one
//      lap paints EVERY slot of the archive window in order (coverage is
//      measured on painted slots, not index continuity)
//   2. a slot whose frame FAILED to load is HELD: the playhead moves to it
//      for its full duration, the previous image stays on the stage, the
//      readout says "no frame", the data-gap note appears, and the next slot
//      paints its own image -- nothing is stepped over
//   3. a poll-appended, still-loading tail is WAITED ON at the newest slot
//      (Buffering, visible) -- there is no early wrap; once the tail decodes
//      the loop continues INTO it and wraps only at the true end
//   4. the __satTimingHook stream carries key/shownKey/held so a harness can
//      verify displayed frames against the archive list
//
// Ticks yield a task between them (real browsers always do) so the
// decode()-ahead warm promises can settle.
// Requires jsdom: npm install --no-save jsdom
// Usage: node tests/sat_pacing_smoke.cjs [path/to/satellite/index.html]
"use strict";
const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const PAGE_PATH = process.argv[2] || path.join(__dirname, "..", "satellite", "index.html");
const PAGE = fs.readFileSync(PAGE_PATH, "utf8");
let failures = 0;
function ok(cond, msg) { console.log((cond ? "ok" : "NOT OK") + " - " + msg); if (!cond) failures++; }
const delay = (ms) => new Promise((r) => setTimeout(r, ms));

function makeFrames(n) {
  const newest = Date.now(), base = newest - (n - 1) * 600000, out = [];
  for (let i = 0; i < n; i++)
    out.push({ t: new Date(base + i * 600000).toISOString().slice(0, 19), key: "floaters/test/ir/f" + i + ".webp" });
  return out;
}

function buildDom(state) {
  const vc = new VirtualConsole();
  return new JSDOM(PAGE, {
    runScripts: "dangerously", url: "https://triple-a-tropics.com/satellite/", virtualConsole: vc,
    beforeParse(window) {
      window.fetch = function (url) {
        let body = null;
        if (url.indexOf("floaters/manifest.json") >= 0)
          body = { storms: [{ slug: "test", name: "TEST", basin: "AL", manifest: "floaters/test/ir.json" }] };
        else if (url.indexOf("floaters/test") >= 0) body = { bands: { ir: { label: "IR", frames: state.frames } } };
        else if (url.indexOf("meso/manifest.json") >= 0) body = { sectors: [] };
        return Promise.resolve({ ok: body !== null, json: () => Promise.resolve(body) });
      };
      // Image stub: decodes instantly, unless its src matches a held key
      // (parks until state.release) or a failing key (fires onerror).
      state.pending = {};
      state.release = function (frag) {
        for (const src in state.pending) if (src.indexOf(frag) >= 0) { const cb = state.pending[src]; delete state.pending[src]; cb(); }
      };
      window.Image = class {
        constructor() { this.onload = null; this.onerror = null; this.crossOrigin = null; this.decoding = null;
          this.fetchPriority = null; this.naturalWidth = 120; this.naturalHeight = 120; this._src = ""; }
        set src(v) {
          this._src = v; const s = this;
          const fail = (state.fails || []).some((h) => v.indexOf(h) >= 0);
          const held = (state.holds || []).some((h) => v.indexOf(h) >= 0);
          const fire = () => { if (fail) { if (s.onerror) s.onerror(); } else if (s.onload) s.onload(); };
          if (held) state.pending[v] = fire; else setTimeout(fire, 0);
        }
        get src() { return this._src; }
        decode() { return Promise.resolve(); }
      };
      let rafCbs = [];
      window.requestAnimationFrame = function (cb) { rafCbs.push(cb); return rafCbs.length; };
      window.cancelAnimationFrame = function () {};
      window.__flushRaf = function (ts) { const cbs = rafCbs; rafCbs = []; cbs.forEach((cb) => { try { cb(ts); } catch (e) {} }); };
      window.__satLog = [];
      window.__satTimingHook = function (ev) { window.__satLog.push(ev); };
    },
  });
}
async function settle(dom) {
  const v = dom.window.__satViewers && dom.window.__satViewers.sat;
  for (let i = 0; i < 80; i++) { await delay(50); if (v) { const s = v.state(); if (s.frames > 0 && s.decoded >= 2) return v; } }
  return v;
}
async function tick(win, ts) { await delay(0); win.__flushRaf(ts); }

(async () => {
  // ===== Scenario A: a 30-slot window with frame 12 FAILING (an archive gap)
  const N = 30;
  const state = { frames: makeFrames(N), holds: [], fails: ["/f12."] };
  const dom = buildDom(state), win = dom.window, doc = win.document;
  const v = await settle(dom);
  ok(!!v, "viewer mounted");
  if (!v) process.exit(1);
  for (let i = 0; i < 40 && v.state().decoded < N - 1; i++) await delay(50);
  const fr = v.frames();
  ok(fr.length === N && fr.filter((f) => f.done && !f.ok).length === 1 && !fr[12].ok, "30 slots loaded, slot 12 is a permanent gap");
  ok(v.build === "v3-held-gaps", "player build is v3-held-gaps");

  const scrub = doc.getElementById("sat-scrub"), frameEl = doc.getElementById("sat-frame");
  scrub.value = "0"; scrub.dispatchEvent(new win.Event("input"));
  doc.getElementById("sat-play").click();
  ok(v.state().playing, "playback started from slot 0");
  let ts = 1e7;
  const painted = [];                                       // one full lap, slot by slot
  for (let k = 0; k < N + 2 && !(painted.length && v.state().idx === 0 && k > 0); k++) {
    ts += 1000; await tick(win, ts);
    painted.push({ idx: v.state().idx, src: frameEl.getAttribute("src"), readout: doc.getElementById("sat-time").textContent });
    if (v.state().idx === N - 1) { ts += 10000; await tick(win, ts); painted.push({ idx: v.state().idx, src: frameEl.getAttribute("src"), readout: doc.getElementById("sat-time").textContent }); break; }
  }
  const idxSeq = painted.map((p) => p.idx);
  const steps = idxSeq.slice(1).map((x, i) => x - idxSeq[i]).filter((d) => d !== 0);
  const fwd = steps.filter((d) => d > 0), wraps = steps.filter((d) => d < 0);
  ok(fwd.every((d) => d === 1) && wraps.length === 1,
     "steady play advances exactly +1 slot per tick, one wrap at the end (steps: " + steps.join(",") + ")");
  const covered = new Set(idxSeq);
  const absent = []; for (let i = 1; i < N; i++) if (!covered.has(i)) absent.push(i);
  ok(absent.length === 0, "one lap painted EVERY slot 1.." + (N - 1) + " (absent: " + (absent.join(",") || "none") + ")");
  const gap = painted.find((p) => p.idx === 12), before = painted.find((p) => p.idx === 11), after = painted.find((p) => p.idx === 13);
  ok(!!gap && gap.src === before.src, "gap slot 12 HELD the previous image (slot 11's src stayed on the stage)");
  ok(!!gap && /no frame/.test(gap.readout), "gap slot readout says 'no frame' (" + (gap && gap.readout) + ")");
  ok(!!after && /f13\./.test(after.src), "slot 13 painted its own image after the held gap");
  ok(!v.state().buffering, "no Buffering during a fully-loaded lap");
  ok(v.state().nearestOkInPlay === 0, "nearestOk snap never hit during play");
  // gap note: park on the gap and let the debounce elapse
  doc.getElementById("sat-play").click();                 // pause
  scrub.value = "12"; scrub.dispatchEvent(new win.Event("input"));
  await delay(320);
  const note = doc.getElementById("sat-gapnote");
  ok(!note.hidden && /No frame at/.test(note.textContent), "data-gap note shown on the stage for a held slot: " + note.textContent);
  scrub.value = "13"; scrub.dispatchEvent(new win.Event("input"));
  ok(note.hidden, "data-gap note hides when a real frame paints");
  // manual step walks the gap slot too (held), never over it
  scrub.value = "11"; scrub.dispatchEvent(new win.Event("input"));
  doc.getElementById("sat-step-fwd").click();
  ok(v.state().idx === 12 && frameEl.getAttribute("src").indexOf("/f11.") >= 0, "step-forward lands ON the gap slot, holding slot 11's image");

  // hook stream: key/shownKey/held present and consistent
  const log = win.__satLog.filter((e) => e.type === "frame" && e.playing);
  const heldEv = log.filter((e) => e.held);
  ok(log.length >= N - 1 && log.every((e) => e.key && e.shownKey) && heldEv.length >= 1 &&
     heldEv.every((e) => /f12\./.test(e.key) && /f11\./.test(e.shownKey)),
     "hook stream carries key/shownKey/held; held events are slot 12 showing slot 11");
  dom.window.close();

  // ===== Scenario B: appended still-loading tail -> WAIT at the newest slot, no early wrap
  const stateB = { frames: makeFrames(20), holds: [] };
  const domB = buildDom(stateB), winB = domB.window, docB = winB.document;
  const vB = await settle(domB);
  for (let i = 0; i < 40 && vB.state().decoded < 20; i++) await delay(50);
  const scrubB = docB.getElementById("sat-scrub");
  scrubB.value = "19"; scrubB.dispatchEvent(new winB.Event("input"));
  docB.getElementById("sat-play").click();
  stateB.holds = ["/fh"];
  const newest = Date.now();
  for (let a = 1; a <= 3; a++) stateB.frames = stateB.frames.concat([{ t: new Date(newest + a * 600000).toISOString().slice(0, 19), key: "floaters/test/ir/fh" + a + ".webp" }]);
  await vB.pollNow();
  ok(vB.state().frames === 23, "poll appended 3 still-loading frames");
  let tsB = 1e7; tsB += 10000; await tick(winB, tsB);     // dwell-sized step past the old newest
  ok(vB.state().idx === 19 && vB.state().buffering, "still-loading tail: playhead WAITS at slot 19 in Buffering (no early wrap, idx " + vB.state().idx + ")");
  for (let k = 0; k < 3; k++) { tsB += 1000; await tick(winB, tsB); }
  ok(vB.state().idx === 19, "playhead still held while the tail loads");
  await delay(320);
  const veil = docB.getElementById("sat-status");
  ok(veil.style.display === "flex" && /Buffering/.test(veil.textContent), "Buffering veil VISIBLE and says so");
  stateB.release("fh1"); stateB.release("fh2"); stateB.release("fh3");
  for (let i = 0; i < 20 && vB.state().decoded < 23; i++) await delay(25);
  tsB += 1000; await tick(winB, tsB);                      // buffering tick -> resume
  ok(!vB.state().buffering, "Buffering exits once the tail decodes");
  const seq = [];
  for (let k = 0; k < 4; k++) { tsB += 10000; await tick(winB, tsB); seq.push(vB.state().idx); }
  ok(seq.join(",") === "20,21,22,0", "loop continues INTO the appended tail and wraps only at the true end (" + seq.join(",") + ")");
  domB.window.close();

  console.log("\n" + (failures ? "FAILED: " + failures + " check(s)" : "ALL CHECKS PASSED"));
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error("HARNESS ERROR:", e); process.exit(2); });
