// /subseasonal/ Hovmöller-selector smoke: proves the meta-driven selector
// builds, swaps panels, hides the wave row off-OLR, and falls back to the
// baked default when the meta fetch fails. Zero page errors in both.
//
//   node tests/hov_page_smoke.cjs
//
// Needs puppeteer resolvable (set PUPPETEER_DIR to a node_modules parent
// if it isn't installed next to the repo).
"use strict";

const http = require("http");
const path = require("path");
const fs = require("fs");

const ROOT = path.resolve(__dirname, "..");
let puppeteer;
try {
  puppeteer = require("puppeteer");
} catch (e) {
  puppeteer = require(path.join(process.env.PUPPETEER_DIR || ".", "node_modules", "puppeteer"));
}

// A meta doc shaped exactly like generate_hovmollers.py writes it.
const META = {
  vars: {
    olr: { through: "2026-07-12", gap_filled_days: 0, waves: true },
    u200: { through: "2026-07-14", days_archived: 220 },
    u850: { through: "2026-07-14", days_archived: 220 },
    chi200: { through: "2026-07-14", days_archived: 220 },
  },
  waves: ["all", "mjo", "kelvin", "er", "mrgtd", "lowfreq", "none"],
  bands: { eq: "7.5°S–7.5°N", trop: "15°S–15°N", nh: "0°–15°N", sh: "15°S–0°" },
  days: [60, 120, 180],
  regions: { glob: "Global", ipac: "Indo-Pacific" },
  template_olr: "hov/hov_olr_{wave}_{band}_{days}_{region}.png",
  template: "hov/hov_{var}_{band}_{days}_{region}.png",
  genesis_markers: 50,
  generated_utc: "2026-07-15T20:00:00Z",
};

let failures = 0;
function check(name, ok, detail) {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok || detail === undefined ? "" : `  (${detail})`}`);
  if (!ok) failures += 1;
}

function serve() {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      const rel = decodeURIComponent(new URL(req.url, "http://x").pathname);
      let file = path.join(ROOT, rel);
      if (rel.endsWith("/")) file = path.join(file, "index.html");
      fs.readFile(file, (err, data) => {
        if (err) { res.writeHead(404); res.end(); return; }
        const type = file.endsWith(".html") ? "text/html"
          : file.endsWith(".js") ? "text/javascript"
          : file.endsWith(".css") ? "text/css" : "application/octet-stream";
        res.writeHead(200, { "content-type": type });
        res.end(data);
      });
    });
    srv.listen(0, "127.0.0.1", () => resolve(srv));
  });
}

async function scenario(browser, base, { metaOk }) {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.setRequestInterception(true);
  page.on("request", (req) => {
    const url = req.url();
    if (url.startsWith(`http://127.0.0.1`) || url.startsWith(base)) return req.continue();
    if (url.includes("cdn.triple-a-tropics.com/subseasonal/")) {
      if (url.includes("hov_meta.json") && metaOk) {
        return req.respond({
          status: 200,
          contentType: "application/json",
          headers: { "access-control-allow-origin": "*" },
          body: JSON.stringify(META),
        });
      }
      if (url.endsWith(".png") || url.includes(".png?")) {
        // 1x1 png so <img> loads resolve quietly
        return req.respond({
          status: 200, contentType: "image/png",
          body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64"),
        });
      }
      return req.respond({ status: 404, body: "" });   // other meta fetches fail quietly
    }
    return req.respond({ status: 404, body: "" });      // fonts etc: offline
  });
  await page.goto(`${base}/subseasonal/`, { waitUntil: "networkidle0", timeout: 30000 });
  await new Promise((r) => setTimeout(r, 300));
  const state = await page.evaluate(() => {
    const img = document.getElementById("img-hov");
    const rows = {};
    ["hov-var", "hov-wave", "hov-band", "hov-days", "hov-region"].forEach((id) => {
      const el = document.getElementById(id);
      rows[id] = {
        n: el.children.length,
        hiddenRow: el.style.display === "none",
        on: Array.prototype.filter.call(el.children, (b) => b.className === "on")
          .map((b) => b.dataset.k),
      };
    });
    return {
      src: img.getAttribute("src") || "",
      selHidden: document.getElementById("hov-sel").hidden,
      asof: document.getElementById("hov-asof").textContent,
      rows,
    };
  });
  return { page, errors, state };
}

(async () => {
  const srv = await serve();
  const base = `http://127.0.0.1:${srv.address().port}`;
  const browser = await puppeteer.launch({
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });

  // A — meta available: full selector
  {
    const { page, errors, state } = await scenario(browser, base, { metaOk: true });
    check("A selector shown", state.selHidden === false);
    check("A default panel", state.src.includes("hov/hov_olr_all_eq_60_glob.png"), state.src);
    check("A field buttons", state.rows["hov-var"].n === 4, state.rows["hov-var"].n);
    check("A wave buttons", state.rows["hov-wave"].n === 7, state.rows["hov-wave"].n);
    check("A band buttons", state.rows["hov-band"].n === 4);
    check("A day buttons", state.rows["hov-days"].n === 3);
    check("A region buttons", state.rows["hov-region"].n === 2);
    check("A wave row visible on OLR", state.rows["hov-wave"].hiddenRow === false);
    check("A as-of line", /through 2026-07-12/.test(state.asof) && /50 TC-genesis/.test(state.asof), state.asof);

    // switch field -> u850: wave row hides, src swaps to the plain template
    await page.evaluate(() => {
      Array.prototype.find.call(
        document.getElementById("hov-var").children,
        (b) => b.dataset.k === "u850").click();
    });
    let s = await page.evaluate(() => ({
      src: document.getElementById("img-hov").getAttribute("src"),
      waveHidden: document.getElementById("hov-wave").style.display === "none",
      asof: document.getElementById("hov-asof").textContent,
    }));
    check("A u850 panel", s.src.includes("hov/hov_u850_eq_60_glob.png"), s.src);
    check("A wave row hidden on u850", s.waveHidden === true);
    check("A u850 as-of", /through 2026-07-14/.test(s.asof), s.asof);

    // days + region + band swap on the non-OLR template
    await page.evaluate(() => {
      Array.prototype.find.call(document.getElementById("hov-days").children,
        (b) => b.dataset.k === "180").click();
      Array.prototype.find.call(document.getElementById("hov-region").children,
        (b) => b.dataset.k === "ipac").click();
      Array.prototype.find.call(document.getElementById("hov-band").children,
        (b) => b.dataset.k === "nh").click();
    });
    s = await page.evaluate(() => ({
      src: document.getElementById("img-hov").getAttribute("src"),
    }));
    check("A u850 nh/180/ipac panel", s.src.includes("hov/hov_u850_nh_180_ipac.png"), s.src);

    // back to OLR with a wave pick
    await page.evaluate(() => {
      Array.prototype.find.call(document.getElementById("hov-var").children,
        (b) => b.dataset.k === "olr").click();
      Array.prototype.find.call(document.getElementById("hov-wave").children,
        (b) => b.dataset.k === "lowfreq").click();
    });
    s = await page.evaluate(() => ({
      src: document.getElementById("img-hov").getAttribute("src"),
      waveHidden: document.getElementById("hov-wave").style.display === "none",
    }));
    check("A olr lowfreq panel", s.src.includes("hov/hov_olr_lowfreq_nh_180_ipac.png"), s.src);
    check("A wave row back", s.waveHidden === false);
    check("A zero page errors", errors.length === 0, errors.join(" | "));
    await page.close();
  }

  // B — meta fetch fails: baked fallback, selector stays hidden
  {
    const { page, errors, state } = await scenario(browser, base, { metaOk: false });
    check("B fallback panel", state.src.includes("hov/hov_olr_all_eq_60_glob.png"), state.src);
    check("B selector hidden", state.selHidden === true);
    check("B zero page errors", errors.length === 0, errors.join(" | "));
    await page.close();
  }

  await browser.close();
  srv.close();
  console.log(failures ? `\n${failures} FAILURE(S)` : "\nALL GREEN");
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
