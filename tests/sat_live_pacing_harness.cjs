// Real-browser pacing measurement for the /satellite/ loop players (floater +
// meso share makeSatViewer). Boots the page in headless Chromium against the
// LIVE CDN archive, samples every frame advance, and reports measured
// frame-to-frame wall intervals + archive-index steps -- the numbers that
// distinguish honest pacing from the silent skip-ahead that read as
// "the loop speeds up after 13Z".
//
//   MODE=live   -> the deployed page as-is (baseline / regression reference)
//   MODE=local  -> repo satellite/index.html fulfilled at the live URL (all
//                  other requests -- CDN manifests, frames, CSS -- stay real)
//   FLOATER=x   -> storm slug (?floater=), e.g. cp01 (GOES-West) / wp17 (Himawari)
//   THROTTLE=n  -> emulate n kbit/s downlink (forces the decode-starved
//                  condition that produced the bug; omit for full-speed)
//   SECS=n      -> measurement window after playback starts (default 30)
//   OUT=dir     -> where to write video + samples JSON (default scratch ./out)
//
// Usage: MODE=local FLOATER=wp17 THROTTLE=4000 node tests/sat_live_pacing_harness.cjs
"use strict";
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const MODE = process.env.MODE || "local";
const FLOATER = process.env.FLOATER || "cp01";
const THROTTLE = parseInt(process.env.THROTTLE || "0", 10);
const SECS = parseInt(process.env.SECS || "30", 10);
const OUT = process.env.OUT || path.join(__dirname, "..", "out_sat_pacing");
const PAGE_PATH = path.join(__dirname, "..", "satellite", "index.html");
const URL = "https://triple-a-tropics.com/satellite/?floater=" + FLOATER;

function pct(sorted, p) {
  if (!sorted.length) return NaN;
  return sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))];
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
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

  // sampler: record every playhead move (idx change) + buffer transitions.
  await page.addInitScript(() => {
    window.__samples = [];
    window.__bufEvents = [];
    window.__satTimingHook = (ev) => {           // new player emits these
      if (ev.type !== "frame") window.__bufEvents.push(ev);
    };
    const poll = () => {
      try {
        const v = window.__satViewers && window.__satViewers.sat;
        if (v) {
          const s = v.state();
          const last = window.__samples[window.__samples.length - 1];
          if (!last || last.idx !== s.idx || last.n !== s.frames)
            window.__samples.push({ now: performance.now(), idx: s.idx,
              n: s.frames, playing: s.playing, buffering: !!s.buffering,
              decoded: s.decoded });
        }
      } catch (e) {}
      requestAnimationFrame(poll);
    };
    requestAnimationFrame(poll);
  });

  if (THROTTLE > 0) {
    const cdp = await ctx.newCDPSession(page);
    await cdp.send("Network.enable");
    await cdp.send("Network.emulateNetworkConditions", {
      offline: false, latency: 40,
      downloadThroughput: (THROTTLE * 1000) / 8, uploadThroughput: 250000,
    });
  }

  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForFunction(
    () => window.__satViewers && window.__satViewers.sat, null, { timeout: 90000 });
  await page.locator("#sat-card").scrollIntoViewIfNeeded();
  // wait for autoplay (decode-gate or its 10 s forced start)
  await page.waitForFunction(
    () => window.__satViewers.sat.state().playing, null, { timeout: 300000 });
  const t0 = Date.now();
  await page.waitForTimeout(SECS * 1000);

  const data = await page.evaluate(() => ({
    samples: window.__samples, buf: window.__bufEvents,
    state: window.__satViewers.sat.state(),
  }));
  const stamp = MODE + "_" + FLOATER + (THROTTLE ? "_t" + THROTTLE : "_full");
  fs.writeFileSync(path.join(OUT, stamp + ".json"),
    JSON.stringify({ MODE, FLOATER, THROTTLE, SECS, errors, ...data }, null, 1));

  // ---- analysis: intervals + archive steps between consecutive PLAYING moves
  const s = data.samples.filter((x) => x.playing);
  const intervals = [], steps = [], skips = [];
  for (let i = 1; i < s.length; i++) {
    if (s[i].n !== s[i - 1].n) continue;         // poll re-cap shifted indices
    const d = s[i].idx - s[i - 1].idx;
    const dt = s[i].now - s[i - 1].now;
    if (d === 0) continue;
    steps.push(d);
    if (d === 1) intervals.push(dt);             // wrap/dwell measured separately
    if (d > 1) skips.push({ at: s[i - 1].idx, jump: d, dt: Math.round(dt) });
  }
  intervals.sort((a, b) => a - b);
  const hist = {};
  steps.forEach((d) => { const k = d < 0 ? "wrap" : String(d); hist[k] = (hist[k] || 0) + 1; });
  const bufStarts = data.buf.filter((e) => e.type === "buffer-start").length;

  console.log("== " + stamp + " ==");
  console.log("page errors:", errors.length ? errors : "none");
  console.log("state at end:", JSON.stringify(data.state));
  console.log("playhead moves:", steps.length,
    "| step histogram:", JSON.stringify(hist));
  console.log("+1-step intervals ms: n=" + intervals.length,
    "median=" + Math.round(pct(intervals, 0.5)),
    "p90=" + Math.round(pct(intervals, 0.9)),
    "p99=" + Math.round(pct(intervals, 0.99)),
    "max=" + Math.round(intervals[intervals.length - 1] || 0));
  console.log("multi-frame skips (>1):", skips.length,
    skips.length ? JSON.stringify(skips.slice(0, 12)) : "");
  console.log("buffering episodes (hook):", bufStarts);
  console.log("measured for", ((Date.now() - t0) / 1000).toFixed(1), "s");

  await ctx.close();                              // flush video
  await browser.close();
  const vids = fs.readdirSync(OUT).filter((f) => f.endsWith(".webm"));
  if (vids.length) {
    const latest = vids.map((f) => path.join(OUT, f))
      .sort((a, b) => fs.statSync(a).mtimeMs - fs.statSync(b).mtimeMs).pop();
    const named = path.join(OUT, stamp + ".webm");
    fs.renameSync(latest, named);
    console.log("video:", named);
  }
})().catch((e) => { console.error("HARNESS ERROR:", e); process.exit(2); });
