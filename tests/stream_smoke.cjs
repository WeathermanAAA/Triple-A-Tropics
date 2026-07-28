// /stream/ broadcast-page smoke: proves the NEVER-BLANK contract and the
// Cat-1+ storm-focus trigger headless at 1920x1080, against Andrew's
// stream design (data layer re-pointed 2026-07-15).
//
//   node tests/stream_smoke.cjs
//
// Needs puppeteer resolvable (set PUPPETEER_DIR to a node_modules parent
// if it isn't installed next to the repo). Scenarios:
//   A  fully OFFLINE (every off-origin request blocked): the baked SAMPLE
//      must render the whole frame - active rows, ACE bars, name boards,
//      crawl, clock - the sidecard chevron wipe must fire, and the page
//      must throw zero errors.
//   B  synthetic live feed (CDN intercepted, ELIDA promoted to C2,
//      ?fast=1): hydration must pick it up, the full-frame "NOW TRACKING"
//      stinger must play, STORM FOCUS must engage with the storm's accent
//      color, the vitals panel must appear, and the stage lower-third
//      must keep rotating.
"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
let puppeteer;
try {
  puppeteer = require("puppeteer");
} catch (e) {
  puppeteer = require(path.join(process.env.PUPPETEER_DIR || ".", "node_modules", "puppeteer"));
}

const MIME = { ".html": "text/html", ".geojson": "application/json", ".json": "application/json", ".svg": "image/svg+xml", ".css": "text/css", ".woff2": "font/woff2" };

function serve() {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      const rel = decodeURIComponent(req.url.split("?")[0]);
      let p = path.join(ROOT, rel);
      if (rel.endsWith("/")) p = path.join(p, "index.html");
      fs.readFile(p, (err, data) => {
        if (err) { res.writeHead(404); res.end("nope"); return; }
        res.writeHead(200, { "content-type": MIME[path.extname(p)] || "application/octet-stream" });
        res.end(data);
      });
    });
    srv.listen(0, "127.0.0.1", () => resolve(srv));
  });
}

// Synthetic feed set: ELIDA at Category 2 (85 kt), schema-faithful to the
// verified live global_storms.geojson + {basin}_ace_data.json shapes.
function syntheticFeeds() {
  const geo = {
    type: "FeatureCollection",
    generated_utc: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
    staleness_minutes: 5,
    features: [
      { type: "Feature",
        geometry: { type: "LineString", coordinates: [[-110.0, 14.0], [-111.5, 14.6], [-112.9, 15.3]] },
        properties: { kind: "track", storm_id: "NHC_EP052026", name: "ELIDA", basin: "ep",
          peak_intensity: "C2", peak_kt: 85.0, is_active: true, is_invest: false, is_ptc: false,
          designation: "05E" } },
      { type: "Feature", geometry: { type: "Point", coordinates: [-111.5, 14.6] },
        properties: { kind: "observation", storm_id: "NHC_EP052026", storm_name: "ELIDA", basin: "ep",
          intensity_kt: 75.0, mslp_mb: 980, time_iso: "2026-07-15T06:00:00", sshws_cat: "C1" } },
      { type: "Feature", geometry: { type: "Point", coordinates: [-112.9, 15.3] },
        properties: { kind: "observation", storm_id: "NHC_EP052026", storm_name: "ELIDA", basin: "ep",
          intensity_kt: 85.0, mslp_mb: 971, time_iso: "2026-07-15T12:00:00", sshws_cat: "C2" } },
      { type: "Feature", geometry: { type: "Point", coordinates: [-112.9, 15.3] },
        properties: { kind: "active_marker", storm_id: "NHC_EP052026", name: "ELIDA", designation: "05E",
          current_intensity_kt: 85.0, current_category: "C2", current_mslp_mb: 971,
          marker_type: "hurricane", is_ptc: false, last_fix: "2026-07-15T12:00:00" } },
      // a 90-numbered invest: must stay an invest row via the number gate
      { type: "Feature", geometry: { type: "Point", coordinates: [-166.8, 10.7] },
        properties: { kind: "active_marker", storm_id: "NHC_CP902026", name: "90C", designation: "90C",
          current_intensity_kt: 25.0, current_category: "TD", current_mslp_mb: 1008,
          marker_type: "invest_x", is_ptc: false, last_fix: "2026-07-15T12:00:00" } },
    ],
  };
  const mean = Array.from({ length: 240 }, (_, i) => i * 0.15);
  const ace = (label, val, storms) => ({
    generated_utc: geo.generated_utc, staleness_minutes: 5, today_doy: 196,
    total_seasons: 100, current_rank: 42,
    current: { label: "2026", latest_value: val, doy: [196], values: [val] },
    climo: { mean },
    storms_by_year: { "2026": storms },
  });
  return {
    geo,
    ace: {
      al: ace("al", 0.4, [{ name: "ARTHUR", formation: "2026-06-30", peak_wind_kt: 40, ace_total: 0.4 }]),
      ep: ace("ep", 8.1, [
        { name: "AMANDA", formation: "2026-06-01", peak_wind_kt: 40, ace_total: 1.0 },
        { name: "ELIDA", formation: "2026-07-12", peak_wind_kt: 85, ace_total: 3.4 }]),
      wp: ace("wp", 126.2, [{ name: "BAVI", formation: "2026-07-01", peak_wind_kt: 155, ace_total: 40.0 }]),
    },
  };
}

let failures = 0;
function check(name, ok, extra) {
  console.log((ok ? "  ok  " : "  FAIL") + " " + name + (extra ? "  [" + extra + "]" : ""));
  if (!ok) failures += 1;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const srv = await serve();
  const base = "http://127.0.0.1:" + srv.address().port;
  const browser = await puppeteer.launch({
    headless: "new",
    args: ["--no-sandbox", "--window-size=1920,1080"],
    defaultViewport: { width: 1920, height: 1080 },
  });

  // ---------------------------------------------------- scenario A
  console.log("scenario A: fully offline -> baked SAMPLE renders, never blanks");
  {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (msg) => { if (msg.type() === "error" && !/net::|Failed to load resource/.test(msg.text())) errors.push(msg.text()); });
    await page.setRequestInterception(true);
    page.on("request", (req) => {
      req.url().startsWith(base) ? req.continue() : req.abort();
    });
    await page.goto(base + "/stream/?fast=1", { waitUntil: "networkidle0", timeout: 30000 });
    await sleep(600);

    const s = await page.evaluate(() => ({
      noindex: !!document.querySelector('meta[name="robots"][content*="noindex"]'),
      rows: document.querySelectorAll("#sideBody .row").length,
      sideHd: document.getElementById("sideHd").textContent,
      crawlLen: document.getElementById("crawl").textContent.length,
      clock: document.getElementById("clk").textContent,
      mtag: document.getElementById("mtag").textContent,
      chevPolys: document.querySelectorAll("#chevsvg polygon").length,
      stingerPolys: document.querySelectorAll("#stingsvg polygon").length,
      activeInSample: window.__stream.S.active.length,
    }));
    check("page errors = 0", errors.length === 0, errors.slice(0, 2).join(" | "));
    check("noindex meta present", s.noindex);
    check("active rows render from baked sample", s.rows >= 1 && s.activeInSample >= 1, "rows=" + s.rows);
    check("crawl populated", s.crawlLen > 60, String(s.crawlLen));
    check("clock ticking", /^\d\d:\d\d UTC$/.test(s.clock), s.clock);
    check("starts in LIVE OVERVIEW", s.mtag === "LIVE OVERVIEW", s.mtag);
    check("chevron fields built", s.chevPolys > 5 && s.stingerPolys > 5);

    // sidecard cycles offline too: heading changes + wipe fires
    const seenHds = new Set([s.sideHd]);
    let sawWipe = false;
    for (let i = 0; i < 8; i++) {
      await sleep(850);
      const t = await page.evaluate(() => ({
        hd: document.getElementById("sideHd").textContent,
        wiping: document.getElementById("chev").classList.contains("go"),
      }));
      seenHds.add(t.hd);
      if (t.wiping) sawWipe = true;
    }
    check("sidecard cycles panels", seenHds.size >= 3, [...seenHds].join(" / "));
    check("chevron wipe fires", sawWipe);

    // stage lower-third rotates through the overview products (images are
    // blocked offline -> cyclone decor stays, but the titles must cycle)
    const t0 = await page.evaluate(() => document.getElementById("nowt").textContent);
    let rotated = false;
    for (let i = 0; i < 5 && !rotated; i++) {
      await sleep(1300);
      rotated = await page.evaluate((prev) =>
        document.getElementById("nowt").textContent !== prev, t0);
    }
    check("stage lower-third rotates (overview)", rotated);
    const boards = await page.evaluate(() => {
      const nm = document.querySelectorAll("#sideBody .nm").length;
      const used = document.querySelectorAll("#sideBody .nm.u").length;
      return { nm, used, hd: document.getElementById("sideHd").textContent };
    }).catch(() => ({ nm: 0, used: 0 }));
    // (not guaranteed to be on a names panel at this instant — informational)
    console.log("       (panel now: " + boards.hd + ", names=" + boards.nm + " used=" + boards.used + ")");
    await page.screenshot({ path: process.env.SHOT_DIR ? path.join(process.env.SHOT_DIR, "stream_offline.png") : "/tmp/stream_offline.png" });
    await page.close();
  }

  // ---------------------------------------------------- scenario B
  console.log("scenario B: synthetic Cat-2 via intercepted CDN -> stinger + storm focus");
  {
    const feeds = syntheticFeeds();
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.setRequestInterception(true);
    page.on("request", (req) => {
      const u = req.url();
      const cors = { "access-control-allow-origin": "*" };
      if (u.includes("global_storms.geojson") && u.includes("cdn.")) {
        req.respond({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify(feeds.geo) });
      } else if (u.includes("_ace_data.json")) {
        const b = (u.match(/(al|ep|wp)_ace_data/) || [])[1];
        req.respond({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify(feeds.ace[b] || {}) });
      } else if (u.startsWith(base)) {
        req.continue();
      } else {
        req.abort();     // manifests, images, fonts: hidden panes, decor stays
      }
    });
    await page.goto(base + "/stream/?fast=1", { waitUntil: "networkidle0", timeout: 30000 });

    const sawStinger = await page
      .waitForFunction(() => document.getElementById("stinger").classList.contains("go"), { timeout: 15000 })
      .then(() => true).catch(() => false);
    check("stinger plays on Cat-1+ arrival", sawStinger);
    if (sawStinger) {
      const st = await page.evaluate(() => ({
        k: document.getElementById("stk").textContent,
        t: document.getElementById("stt").textContent,
        s: document.getElementById("sts").textContent,
      }));
      check('stinger reads "NOW TRACKING"', st.k === "NOW TRACKING", st.k);
      check("stinger names ELIDA", st.t === "ELIDA", st.t);
      check("stinger sub carries basin + category", /E\.PAC/.test(st.s) && /C2/.test(st.s), st.s);
      await page.screenshot({ path: process.env.SHOT_DIR ? path.join(process.env.SHOT_DIR, "stream_stinger.png") : "/tmp/stream_stinger.png" });
    }

    const inFocus = await page
      .waitForFunction(() => document.getElementById("mtag").textContent === "STORM FOCUS", { timeout: 8000 })
      .then(() => true).catch(() => false);
    check("STORM FOCUS engages", inFocus);
    if (inFocus) {
      const f = await page.evaluate(() => ({
        acc: document.getElementById("stage").style.getPropertyValue("--acc").trim(),
        mode: window.__stream.mode(),
      }));
      check("accent flips to the storm's category color (C2)", f.acc === "#ff9f3a", f.acc);
      check("mode state is sf", f.mode === "sf", f.mode);
      // vitals panel appears within one panel lap
      let sawVitals = false;
      for (let i = 0; i < 6 && !sawVitals; i++) {
        await sleep(900);
        sawVitals = await page.evaluate(() => /VITALS/i.test(document.getElementById("sideHd").textContent));
      }
      check("vitals panel appears in the sidecard", sawVitals);
      const vit = await page.evaluate(() => document.getElementById("sideBody").textContent);
      check("vitals carry live wind + pressure", /85/.test(vit) && /971/.test(vit),
        vit.replace(/\s+/g, " ").slice(0, 80));
      await sleep(400);
      await page.screenshot({ path: process.env.SHOT_DIR ? path.join(process.env.SHOT_DIR, "stream_focus.png") : "/tmp/stream_focus.png" });
    }

    // focus stage: with every product manifest blocked, the rotation
    // honestly collapses to the single CycloLab pane naming the storm
    const focusStage = await page.evaluate(() => ({
      n: window.__stream.seq().length,
      nowt: document.getElementById("nowt").textContent,
      nowk: document.getElementById("nowk").textContent,
    }));
    check("focus stage shows the storm's CycloLab pane", focusStage.n >= 1 &&
      focusStage.nowt === "ELIDA" && /CycloLab/i.test(focusStage.nowk),
      focusStage.nowk + " · " + focusStage.nowt);

    // number gate: 90C stays an Invest row even while ELIDA focuses
    const gate = await page.evaluate(() => {
      const inv = (window.__stream.S.active || []).filter((s) => s.name === "90C")[0];
      const eli = (window.__stream.S.active || []).filter((s) => s.name === "ELIDA")[0];
      return { inv: inv && inv.invest === true, eli: eli && eli.invest === false && eli.cat === "C2" };
    });
    check("number gate: 90C = invest, ELIDA = designated C2", !!(gate.inv && gate.eli));
    check("page errors = 0", errors.length === 0, errors.slice(0, 2).join(" | "));
    await page.close();
  }

  await browser.close();
  srv.close();
  console.log(failures ? "FAILED: " + failures + " check(s)" : "all stream smoke checks passed");
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
