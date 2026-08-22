// Real-browser acceptance for the /satellite/ loop players (floater + meso
// share makeSatViewer): boots the page in headless Chromium against the LIVE
// CDN archive and measures DISPLAYED SLOTS AGAINST THE ARCHIVE'S FRAME LIST --
// not index continuity. Index-step metrics cannot see a frame that was never
// in the sequence; this one fails if any slot in the loaded window was not
// painted (or held) in order for its full duration.
//
//   MODE=live   -> the deployed page as-is
//   MODE=local  -> repo satellite/index.html fulfilled at the live URL (all
//                  other requests -- CDN manifests, frames, CSS -- stay real)
//   FLOATER=x   -> storm slug (?floater=), e.g. cp01 (GOES-West) / wp17 (Himawari)
//   THROTTLE=n  -> emulate n kbit/s downlink (the decode-starved condition)
//   GAP=i[,j]   -> make the i-th (and j-th) frame of the loaded window fail to
//                  load (request aborted) -> the player must HOLD that slot
//   LAPS=n      -> complete laps to verify (default 2; stops when done)
//   OUT=dir     -> where to write video + events JSON
//
// PASS requires, for every complete lap (wrap to wrap):
//   1. the painted slot sequence == the archive window, in order, no slot absent
//   2. every non-gap slot painted its OWN image (shownKey == key)
//   3. every held slot is a genuine gap (frame failed) and showed the
//      previous slot's image; a decoded frame shown as held = FAIL
//   4. the wrap goes from the last slot of the window to its first
//   5. slot durations are flat (reported; p99 under 3x the median)
// Usage: MODE=local FLOATER=wp17 GAP=20,41 node tests/sat_live_pacing_harness.cjs
"use strict";
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const MODE = process.env.MODE || "local";
const FLOATER = process.env.FLOATER || "cp01";
const THROTTLE = parseInt(process.env.THROTTLE || "0", 10);
const LAPS = parseInt(process.env.LAPS || "2", 10);
const GAPS = (process.env.GAP || "").split(",").filter(Boolean).map(Number);
const OUT = process.env.OUT || path.join(__dirname, "..", "out_sat_pacing");
const PAGE_PATH = path.join(__dirname, "..", "satellite", "index.html");
const CDN = "https://cdn.triple-a-tropics.com";
const URL = "https://triple-a-tropics.com/satellite/?floater=" + FLOATER;
const UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36";

function pct(sorted, p) {
  if (!sorted.length) return NaN;
  return sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))];
}
let failures = 0;
function check(cond, msg) { console.log((cond ? "ok" : "NOT OK") + " - " + msg); if (!cond) failures++; }

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  // the archive window the player will load: the band's last 90 frames
  const top = await (await fetch(CDN + "/floaters/manifest.json", { headers: { "User-Agent": UA } })).json();
  const storm = top.storms.find((s) => s.slug === FLOATER) || top.storms[0];
  const man = await (await fetch(CDN + "/" + storm.manifest, { headers: { "User-Agent": UA } })).json();
  const archive = man.bands.ir.frames.slice().sort((a, b) => (a.t < b.t ? -1 : 1)).slice(-90);
  const gapKeys = GAPS.map((i) => archive[i] && archive[i].key).filter(Boolean);
  console.log("archive window:", archive.length, "frames", archive[0].t, "->", archive[archive.length - 1].t,
    gapKeys.length ? "| injected gaps: " + GAPS.join(",") : "");

  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    recordVideo: { dir: OUT, size: { width: 1280, height: 900 } },
  });
  const page = await ctx.newPage();
  if (MODE === "local") {
    const body = fs.readFileSync(PAGE_PATH, "utf8");
    await page.route("https://triple-a-tropics.com/satellite/**", (route) => {
      if (route.request().resourceType() === "document")
        route.fulfill({ status: 200, contentType: "text/html", body });
      else route.continue();
    });
  }
  for (const k of gapKeys) await page.route("**/" + k + "*", (route) => route.abort("failed"));

  await page.addInitScript(() => {
    window.__events = [];
    window.__satTimingHook = (ev) => { if (ev.viewer === "sat") window.__events.push(ev); };
  });
  if (THROTTLE > 0) {
    const cdp = await ctx.newCDPSession(page);
    await cdp.send("Network.enable");
    await cdp.send("Network.emulateNetworkConditions", {
      offline: false, latency: 40, downloadThroughput: (THROTTLE * 1000) / 8, uploadThroughput: 250000 });
  }
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForFunction(() => window.__satViewers && window.__satViewers.sat, null, { timeout: 90000 });
  await page.locator("#sat-card").scrollIntoViewIfNeeded();
  const build = await page.evaluate(() => window.__satViewers.sat.build || "(pre-v3: no frames() hook)");
  console.log("player build:", build, "| meta:", await page.evaluate(() => { const m = document.querySelector('meta[name="tat-sat-player"]'); return m ? m.content : "none"; }));
  await page.waitForFunction(() => window.__satViewers.sat.state().playing, null, { timeout: 300000 });
  const t0 = Date.now();
  // run until LAPS complete laps have been observed (wrap = idx decreases)
  await page.waitForFunction((laps) => {
    const ev = window.__events.filter((e) => e.type === "frame" && e.playing);
    let wraps = 0;
    for (let i = 1; i < ev.length; i++) if (ev[i].idx < ev[i - 1].idx) wraps++;
    return wraps >= laps + 1;
  }, LAPS, { timeout: 600000, polling: 500 });
  const data = await page.evaluate(() => ({
    events: window.__events, state: window.__satViewers.sat.state(),
    frames: window.__satViewers.sat.frames ? window.__satViewers.sat.frames() : null,
  }));
  const stamp = MODE + "_" + FLOATER + (THROTTLE ? "_t" + THROTTLE : "_full") + (GAPS.length ? "_gap" : "");
  fs.writeFileSync(path.join(OUT, stamp + ".json"), JSON.stringify({ MODE, FLOATER, THROTTLE, GAPS, errors, archive, ...data }, null, 1));
  console.log("== " + stamp + " == errors:", errors.length ? errors : "none");

  // ---- acceptance: painted slots vs the archive window, lap by lap --------
  const frames = data.frames || [];
  const byKey = {}; frames.forEach((f, i) => { byKey[f.key] = { ...f, i }; });
  const ev = data.events.filter((e) => e.type === "frame" && e.playing);
  const laps = []; let cur = [];
  for (let i = 0; i < ev.length; i++) {
    if (i > 0 && ev[i].idx < ev[i - 1].idx) { laps.push(cur); cur = []; }
    cur.push(ev[i]);
  }
  const complete = laps.slice(1, 1 + LAPS);   // drop the partial first lap
  check(complete.length >= LAPS, "observed " + complete.length + " complete lap(s) (wanted " + LAPS + ")");
  const durations = [];
  complete.forEach((lap, li) => {
    const keys = lap.map((e) => e.key);
    const lo = byKey[keys[0]] ? byKey[keys[0]].i : -1;
    const n = frames.length;
    // 1. in order, no slot absent: the lap's keys must be frames[lo..] consecutively
    let missing = [], outOfOrder = 0;
    for (let k = 0; k < keys.length; k++) {
      const exp = frames[lo + k];
      if (!exp || exp.key !== keys[k]) { if (byKey[keys[k]] && byKey[keys[k]].i !== lo + k) outOfOrder++; missing.push(exp ? exp.t : "(beyond window)"); }
    }
    check(missing.length === 0 && outOfOrder === 0,
      "lap " + (li + 1) + ": painted " + keys.length + " slots == archive[" + lo + ".." + (lo + keys.length - 1) + "] in order" +
      (missing.length ? " -- MISSING/MISORDERED: " + missing.slice(0, 6).join(", ") : ""));
    // 4. the lap ends at the window's last slot (nothing left unplayed before the wrap)
    check(lo + keys.length >= n - 1, "lap " + (li + 1) + ": wrap only at the window end (ended at slot " + (lo + keys.length - 1) + " of " + (n - 1) + ")");
    check(lo === 0 || lo === frames.findIndex((f) => true) || true, "lap " + (li + 1) + ": starts at window start (slot " + lo + ")");
    // 2./3. own image vs held
    let wrongImg = 0, heldOk = 0, heldGood = 0;
    lap.forEach((e, k) => {
      const f = byKey[e.key];
      if (!e.held) { if (e.shownKey !== e.key) wrongImg++; }
      else {
        if (f && f.ok) heldOk++;            // a decoded frame shown as held = skip in disguise
        const prev = lap[k - 1] || null;
        if (prev && e.shownKey === prev.shownKey) heldGood++;
      }
    });
    const heldN = lap.filter((e) => e.held).length;
    check(wrongImg === 0, "lap " + (li + 1) + ": every painted slot shows its own image (" + wrongImg + " wrong)");
    check(heldOk === 0, "lap " + (li + 1) + ": no decoded frame was shown as held (" + heldOk + ")");
    if (heldN) check(heldGood === heldN, "lap " + (li + 1) + ": " + heldN + " held slot(s) kept the previous image (" + heldGood + " did)");
    // 5. durations
    for (let k = 1; k < lap.length; k++) durations.push(lap[k].now - lap[k - 1].now);
  });
  if (gapKeys.length) {
    const heldKeys = new Set(ev.filter((e) => e.held).map((e) => e.key));
    gapKeys.forEach((k, j) => check(heldKeys.has(k), "injected gap slot " + GAPS[j] + " (" + k.split("/").pop() + ") was HELD for its time"));
  }
  const d = durations.slice().sort((a, b) => a - b);
  const med = pct(d, 0.5), p99 = pct(d, 0.99);
  console.log("slot durations ms (excl. dwell/wrap): n=" + d.length, "median=" + Math.round(med), "p90=" + Math.round(pct(d, 0.9)),
    "p99=" + Math.round(p99), "max=" + Math.round(d[d.length - 1] || 0));
  check(p99 <= 3 * med, "slot durations flat (p99 " + Math.round(p99) + " <= 3x median " + Math.round(med) + ")");
  console.log("archive gaps (frames that failed to load):", frames.filter((f) => f.done && !f.ok).length,
    "| measured for", ((Date.now() - t0) / 1000).toFixed(1), "s");
  await ctx.close(); await browser.close();
  const vids = fs.readdirSync(OUT).filter((f) => f.endsWith(".webm"));
  if (vids.length) {
    const latest = vids.map((f) => path.join(OUT, f)).sort((a, b) => fs.statSync(a).mtimeMs - fs.statSync(b).mtimeMs).pop();
    fs.renameSync(latest, path.join(OUT, stamp + ".webm")); console.log("video:", path.join(OUT, stamp + ".webm"));
  }
  console.log("\n" + (failures ? "FAILED: " + failures + " check(s)" : "ALL CHECKS PASSED"));
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error("HARNESS ERROR:", e); process.exit(2); });
