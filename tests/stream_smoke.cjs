// /stream/ broadcast-canvas smoke: proves the NEVER-BLANK contract and
// the Cat-1+ storm-focus trigger headless at 1920x1080.
//
//   node tests/stream_smoke.cjs
//
// Needs puppeteer resolvable (set PUPPETEER_DIR to a node_modules parent
// if it isn't installed next to the repo). Scenarios:
//   A  fully OFFLINE (every off-origin request blocked): the embedded
//      fallback snapshot must render the whole canvas - active systems,
//      map, tiles, boards, ticker - and the page must throw zero errors.
//   B  synthetic live feed (CDN intercepted, one storm promoted to C2,
//      ?fast=1): hydration must pick it up, the full-frame stinger must
//      play, storm-focus mode must engage and then return to overview.
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

const MIME = { ".html": "text/html", ".geojson": "application/json", ".json": "application/json", ".svg": "image/svg+xml", ".css": "text/css" };

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

function fallbackSnapshot() {
  const html = fs.readFileSync(path.join(ROOT, "stream", "index.html"), "utf8");
  const m = html.match(/<script id="fallback-data" type="application\/json">(.*?)<\/script>/s);
  if (!m) throw new Error("fallback-data block missing from stream/index.html");
  return JSON.parse(m[1].replace(/<\\\//g, "</"));
}

let failures = 0;
function check(name, ok, extra) {
  console.log((ok ? "  ok  " : "  FAIL") + " " + name + (extra ? "  [" + extra + "]" : ""));
  if (!ok) failures += 1;
}

(async () => {
  const srv = await serve();
  const base = "http://127.0.0.1:" + srv.address().port;
  const browser = await puppeteer.launch({
    headless: "new",
    args: ["--no-sandbox", "--window-size=1920,1080"],
    defaultViewport: { width: 1920, height: 1080 },
  });

  // ---------------------------------------------------- scenario A
  console.log("scenario A: fully offline -> fallback renders, never blanks");
  {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (msg) => { if (msg.type() === "error" && !/net::|Failed to load resource/.test(msg.text())) errors.push(msg.text()); });
    await page.setRequestInterception(true);
    page.on("request", (req) => {
      req.url().startsWith(base) ? req.continue() : req.abort();
    });
    await page.goto(base + "/stream/", { waitUntil: "networkidle0", timeout: 30000 });
    await new Promise((r) => setTimeout(r, 1500));

    const s = await page.evaluate(() => ({
      activeCount: document.getElementById("active-count").textContent,
      sysRows: document.querySelectorAll("#active-list .sysrow").length,
      mapNodes: document.querySelectorAll("#worldmap path, #worldmap circle, #worldmap text").length,
      tiles: document.querySelectorAll("#ace-tiles .tile").length,
      tileText: document.getElementById("ace-tiles").textContent,
      faces: document.querySelectorAll("#carousel .face").length,
      tickerLen: document.getElementById("ticker-track").textContent.length,
      clock: document.getElementById("clock-hms").textContent,
      stamp: document.getElementById("stamp-asof").textContent,
      canvasVisible: getComputedStyle(document.getElementById("canvas")).display !== "none",
    }));
    check("page errors = 0", errors.length === 0, errors.slice(0, 2).join(" | "));
    check("canvas visible", s.canvasVisible);
    check("active systems rendered from fallback", Number(s.activeCount) >= 1 && s.sysRows >= 1, "count=" + s.activeCount);
    check("world map has geometry", s.mapNodes > 10, String(s.mapNodes));
    check("3 ACE tiles with data", s.tiles === 3 && /ACE/.test(s.tileText) && !/unavailable/.test(s.tileText));
    check("carousel face mounted", s.faces >= 1);
    check("ticker populated", s.tickerLen > 40, String(s.tickerLen));
    check("clock ticking", /^\d\d:\d\d:\d\d$/.test(s.clock), s.clock);
    check("as-of stamp shows fallback time", /\d\d:\d\d UTC/.test(s.stamp), s.stamp);
    await page.screenshot({ path: process.env.SHOT_DIR ? path.join(process.env.SHOT_DIR, "stream_offline.png") : "/tmp/stream_offline.png" });
    await page.close();
  }

  // ---------------------------------------------------- scenario B
  console.log("scenario B: synthetic Cat-2 via intercepted CDN -> stinger + storm focus");
  {
    const snap = fallbackSnapshot();
    const geo = snap.geo;
    let promoted = null;
    for (const f of geo.features) {
      const p = f.properties || {};
      if (p.kind === "active_marker" && !promoted) {
        p.current_intensity_kt = 85;
        p.current_category = "C2";
        p.marker_type = "hurricane";
        promoted = p.name;
      }
    }
    if (!promoted) throw new Error("no active_marker in fallback to promote");

    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.setRequestInterception(true);
    page.on("request", (req) => {
      const u = req.url();
      if (u.includes("global_storms.geojson") && u.includes("cdn.")) {
        req.respond({ status: 200, contentType: "application/json", headers: { "access-control-allow-origin": "*" }, body: JSON.stringify(geo) });
      } else if (u.includes("_ace_data.json")) {
        const b = (u.match(/(al|ep|wp)_ace_data/) || [])[1];
        req.respond({ status: 200, contentType: "application/json", headers: { "access-control-allow-origin": "*" }, body: JSON.stringify(snap.ace[b] || {}) });
      } else if (u.startsWith(base)) {
        req.continue();
      } else {
        req.abort();
      }
    });
    await page.goto(base + "/stream/?fast=1", { waitUntil: "networkidle0", timeout: 30000 });

    const sawStinger = await page
      .waitForFunction(() => document.getElementById("stinger").classList.contains("play"), { timeout: 15000 })
      .then(() => true).catch(() => false);
    check("stinger plays on Cat-1+ arrival", sawStinger);

    const inFocus = await page
      .waitForFunction(() => !document.getElementById("pane-focus").hidden, { timeout: 8000 })
      .then(() => true).catch(() => false);
    check("storm-focus mode engages", inFocus);
    if (inFocus) {
      const f = await page.evaluate(() => ({
        name: document.getElementById("f-name").textContent,
        cat: document.getElementById("f-cat").textContent,
        kt: document.getElementById("f-kt").textContent,
        mapNodes: document.querySelectorAll("#focusmap path, #focusmap circle").length,
      }));
      check("focused storm is the promoted one", f.name === promoted, f.name + " vs " + promoted);
      check("category chip reads CATEGORY 2", f.cat === "CATEGORY 2", f.cat);
      check("intensity shows 85", f.kt === "85", f.kt);
      check("focus map has geometry", f.mapNodes > 3, String(f.mapNodes));
      await page.screenshot({ path: process.env.SHOT_DIR ? path.join(process.env.SHOT_DIR, "stream_stinger.png") : "/tmp/stream_stinger.png" });
      await page.waitForFunction(() => !document.getElementById("stinger").classList.contains("play"), { timeout: 6000 }).catch(() => {});
      await new Promise((r) => setTimeout(r, 300));
      await page.screenshot({ path: process.env.SHOT_DIR ? path.join(process.env.SHOT_DIR, "stream_focus.png") : "/tmp/stream_focus.png" });
    }

    const backToOverview = await page
      .waitForFunction(() => !document.getElementById("pane-overview").hidden, { timeout: 12000 })
      .then(() => true).catch(() => false);
    check("returns to overview after focus dwell", backToOverview);

    // carousel wipes at least once in fast mode
    const wiped = await page
      .waitForFunction(() => document.querySelectorAll("#carousel .face").length >= 2 || document.querySelector("#carousel .face.in"), { timeout: 8000 })
      .then(() => true).catch(() => false);
    check("carousel chevron wipe fires", wiped);
    check("page errors = 0", errors.length === 0, errors.slice(0, 2).join(" | "));
    await page.close();
  }

  await browser.close();
  srv.close();
  console.log(failures ? "FAILED: " + failures + " check(s)" : "all stream smoke checks passed");
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
