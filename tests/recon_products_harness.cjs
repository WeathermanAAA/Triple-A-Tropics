// Headless harness for the recon viewer's dropsonde/VDM products - boots
// /obs/recon/ against the live CDN with an optional local override tree
// (OVERRIDES dir mirrors cdn paths, e.g. <dir>/recon/al022026/x.json), so
// enriched mission JSONs can be verified before they are published. Usage:
//   node tests/recon_products_harness.cjs <storm-slug> [outdir]
//   OVERRIDES=/path/to/tree node tests/recon_products_harness.cjs al022026
"use strict";
const { chromium } = require("playwright");
const http = require("http");
const path = require("path");
const fs = require("fs");

const ROOT = "/workspaces/Triple-A-Tropics";
const SLUG = process.argv[2] || "al022026";
const OUT = process.argv[3] ||
  "/tmp/claude-1000/-workspaces-Triple-A-Tropics/147d4189-aed5-4b34-a309-9d025a30da15/scratchpad/reconshots";
const OVERRIDES = process.env.OVERRIDES || "";
const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".geojson": "application/json", ".png": "image/png" };

function serve() {
  return new Promise((res) => {
    const srv = http.createServer((req, rsp) => {
      let p = decodeURIComponent(req.url.split("?")[0]);
      if (p.endsWith("/")) p += "index.html";
      fs.readFile(path.join(ROOT, p), (err, data) => {
        if (err) { rsp.writeHead(404); rsp.end("nf"); return; }
        rsp.writeHead(200, { "content-type": MIME[path.extname(p)] || "application/octet-stream" });
        rsp.end(data);
      });
    });
    srv.listen(0, "127.0.0.1", () => res(srv));
  });
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const srv = await serve();
  const base = "http://127.0.0.1:" + srv.address().port;
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1360, height: 1000 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push("PAGEERROR " + String(e).slice(0, 300)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text().slice(0, 200)); });

  // CDN: serve local override when present, else proxy with open CORS
  await page.route("https://cdn.triple-a-tropics.com/**", async (route) => {
    const u = new URL(route.request().url());
    if (OVERRIDES) {
      const f = path.join(OVERRIDES, u.pathname);
      if (fs.existsSync(f)) {
        return route.fulfill({ status: 200, body: fs.readFileSync(f),
          headers: { "content-type": MIME[path.extname(f)] || "application/json",
                     "access-control-allow-origin": "*",
                     "cross-origin-resource-policy": "cross-origin" } });
      }
    }
    try {
      const r = await fetch(u.href);
      const buf = Buffer.from(await r.arrayBuffer());
      return route.fulfill({ status: r.status, body: buf,
        headers: { "content-type": r.headers.get("content-type") || "application/octet-stream",
                   "access-control-allow-origin": "*",
                   "cross-origin-resource-policy": "cross-origin" } });
    } catch (e) { return route.abort(); }
  });

  await page.goto(base + "/obs/recon/", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  await page.click("#recon-tab-storms");
  await page.waitForTimeout(800);
  await page.selectOption("#recon-storm", SLUG).catch(() => {});
  await page.waitForTimeout(2500);

  // full page (map + panels)
  await page.screenshot({ path: OUT + "/1_full.png", fullPage: true });
  // map card (numbered drops)
  const mapEl = await page.$("#recon-mapframe");
  if (mapEl) await mapEl.screenshot({ path: OUT + "/2_map.png" });
  // VDM panel
  const vdmEl = await page.$("#recon-vdm");
  if (vdmEl && await vdmEl.isVisible()) await vdmEl.screenshot({ path: OUT + "/3_vdm.png" });
  // Skew-T card, then a different drop via the selector
  const skEl = await page.$("#recon-skewt-wrap");
  if (skEl && await skEl.isVisible()) {
    await skEl.screenshot({ path: OUT + "/4_skewt.png" });
    const opts = await page.$$eval("#recon-sonde option", (o) => o.length);
    if (opts > 2) {
      await page.selectOption("#recon-sonde", String(Math.min(2, opts - 1)));
      await page.waitForTimeout(400);
      await skEl.screenshot({ path: OUT + "/5_skewt_other.png" });
      await mapEl.screenshot({ path: OUT + "/6_map_selected.png" });
    }
  }
  const state = await page.evaluate(() => ({
    sondes: document.querySelectorAll("#recon-sonde option").length,
    vdmVisible: !!document.querySelector("#recon-vdm") &&
      document.querySelector("#recon-vdm").style.display !== "none",
    skewtVisible: !!document.querySelector("#recon-skewt-wrap") &&
      document.querySelector("#recon-skewt-wrap").style.display !== "none",
    vdmChips: document.querySelectorAll("#recon-vdm-body .recon-chip").length,
    tableRows: document.querySelectorAll("#recon-skewt-table tr").length
  }));
  console.log("STATE", JSON.stringify(state));
  console.log("ERRORS", errors.length ? errors.slice(0, 8) : "none");
  await browser.close();
  srv.close();
  process.exit(errors.filter((e) => e.indexOf("PAGEERROR") === 0).length ? 1 : 0);
})();
